"""
JAPAP — Payment Health (iter117)
================================
Cockpit complet pour la santé des paiements :
  • Tracking de la latence des appels verify_payment_status (Hubtel + NowPayments)
  • Endpoint admin /api/wallet/admin/payment-health (KPIs cockpit)
  • Daily email report vers OPS_INBOX_EMAIL
  • Retry queue pour les transactions bloquées en pending_verification

Tables créées (idempotent DDL) :
  • payment_verify_metrics : trace chaque appel verify (provider, ok, latency_ms, error, tx_id)
  • payment_verify_retries : queue des tx à retenter (provider, tx_id, attempts, next_retry_at)
"""
from __future__ import annotations
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

logger = logging.getLogger(__name__)

# Limite sécurité : on n'essaie pas de re-vérifier indéfiniment.
MAX_RETRY_ATTEMPTS = 8
# Backoff exponentiel : 2 min, 4, 8, 16, 32, 64, 128, 256 minutes (~4h cumulés)
BACKOFF_MINUTES = (2, 4, 8, 16, 32, 64, 128, 256)

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS payment_verify_metrics (
        id BIGSERIAL PRIMARY KEY,
        provider VARCHAR(20) NOT NULL,        -- 'hubtel' | 'nowpayments'
        tx_id VARCHAR(64),
        ok BOOLEAN NOT NULL,
        is_paid BOOLEAN,
        latency_ms INT NOT NULL,
        http_status INT,
        error TEXT,
        called_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pvm_called ON payment_verify_metrics (called_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pvm_provider ON payment_verify_metrics (provider, called_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS payment_verify_retries (
        id BIGSERIAL PRIMARY KEY,
        provider VARCHAR(20) NOT NULL,
        tx_id VARCHAR(64) NOT NULL,
        provider_ref VARCHAR(120),
        attempts INT NOT NULL DEFAULT 0,
        next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_error TEXT,
        last_attempt_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        resolved BOOLEAN NOT NULL DEFAULT FALSE,
        resolved_at TIMESTAMPTZ,
        UNIQUE(provider, tx_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pvr_pending ON payment_verify_retries (next_retry_at) WHERE resolved=FALSE",
    # iter151 — persisted log of daily digests so the once-per-day guard
    # survives backend restarts AND is shared across multiple uvicorn
    # workers (the in-memory `_last_digest_date` couldn't enforce that).
    """
    CREATE TABLE IF NOT EXISTS payment_health_digests (
        digest_date DATE PRIMARY KEY,
        sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        worker_id   VARCHAR(32),
        recipients  TEXT,
        rows_count  INT,
        status      VARCHAR(16) NOT NULL DEFAULT 'sent'
    )
    """,
]


async def ensure_payment_health_ddl() -> None:
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        for stmt in _DDL:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"payment_health DDL: {e} — {stmt[:60]}")


# ──────────────────────────────────────────────────────────────────────────
# Latency tracking
# ──────────────────────────────────────────────────────────────────────────
async def track_verify(provider: str, tx_id: str, *,
                       ok: bool, is_paid: Optional[bool] = None,
                       latency_ms: int, http_status: Optional[int] = None,
                       error: str = "") -> None:
    """Record one call to verify_payment_status / verify_transaction_status."""
    try:
        from database import get_pool
        await ensure_payment_health_ddl()
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO payment_verify_metrics
                       (provider, tx_id, ok, is_paid, latency_ms, http_status, error)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                provider, tx_id[:64], bool(ok),
                None if is_paid is None else bool(is_paid),
                int(latency_ms), http_status, (error or "")[:500],
            )
    except Exception as e:
        logger.warning(f"track_verify failed: {e}")


@asynccontextmanager
async def measure_verify(provider: str, tx_id: str = ""):
    """Async context manager. Times the wrapped call and emits a metric.

    Usage:
        async with measure_verify("hubtel", tx_id) as m:
            res = await verify_transaction_status(...)
            m["ok"] = res["ok"]
            m["is_paid"] = res.get("is_paid")
            m["http_status"] = res.get("http_status")
    """
    started = time.monotonic()
    state: dict[str, Any] = {"ok": False, "is_paid": None,
                              "http_status": None, "error": ""}
    try:
        yield state
    except Exception as e:
        state["ok"] = False
        state["error"] = str(e)[:300]
        raise
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        await track_verify(
            provider, tx_id,
            ok=bool(state.get("ok")),
            is_paid=state.get("is_paid"),
            latency_ms=latency_ms,
            http_status=state.get("http_status"),
            error=state.get("error", ""),
        )


# ──────────────────────────────────────────────────────────────────────────
# Retry queue
# ──────────────────────────────────────────────────────────────────────────
async def schedule_verify_retry(provider: str, tx_id: str,
                                 *, provider_ref: str = "",
                                 reason: str = "") -> None:
    """Enqueue a transaction for periodic re-verification. Idempotent on
    (provider, tx_id) — bumps attempts + reschedules the next retry."""
    await ensure_payment_health_ddl()
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, attempts FROM payment_verify_retries "
            "WHERE provider=$1 AND tx_id=$2 AND resolved=FALSE",
            provider, tx_id,
        )
        if existing:
            attempts = int(existing["attempts"]) + 1
            if attempts > MAX_RETRY_ATTEMPTS:
                # Give up — mark resolved so we stop trying. Admin can re-enqueue.
                await conn.execute(
                    """UPDATE payment_verify_retries
                          SET resolved=TRUE, resolved_at=NOW(),
                              last_error=$2, last_attempt_at=NOW()
                        WHERE id=$1""",
                    existing["id"],
                    f"Abandonné après {MAX_RETRY_ATTEMPTS} tentatives — {reason}",
                )
                logger.warning(f"verify-retry abandoned: {provider}:{tx_id} ({reason})")
                return
            backoff_idx = min(attempts - 1, len(BACKOFF_MINUTES) - 1)
            next_at = datetime.now(timezone.utc) + timedelta(
                minutes=BACKOFF_MINUTES[backoff_idx])
            await conn.execute(
                """UPDATE payment_verify_retries
                      SET attempts=$2, next_retry_at=$3, last_error=$4,
                          last_attempt_at=NOW()
                    WHERE id=$1""",
                existing["id"], attempts, next_at, reason[:500],
            )
        else:
            next_at = datetime.now(timezone.utc) + timedelta(
                minutes=BACKOFF_MINUTES[0])
            await conn.execute(
                """INSERT INTO payment_verify_retries
                      (provider, tx_id, provider_ref, attempts,
                       next_retry_at, last_error)
                      VALUES ($1, $2, $3, 0, $4, $5)""",
                provider, tx_id, (provider_ref or "")[:120],
                next_at, reason[:500],
            )


async def mark_retry_resolved(provider: str, tx_id: str,
                              note: str = "credited") -> None:
    """Called by the credit path once a tx finally clears verification."""
    try:
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE payment_verify_retries
                      SET resolved=TRUE, resolved_at=NOW(), last_error=$3
                    WHERE provider=$1 AND tx_id=$2 AND resolved=FALSE""",
                provider, tx_id, note[:500],
            )
    except Exception as e:
        logger.warning(f"mark_retry_resolved failed: {e}")


async def list_due_retries(limit: int = 20) -> list[dict]:
    """Pulls retries whose next_retry_at <= NOW() and resolved=FALSE."""
    await ensure_payment_health_ddl()
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, provider, tx_id, provider_ref, attempts,
                      next_retry_at, last_error
                 FROM payment_verify_retries
                WHERE resolved=FALSE AND next_retry_at <= NOW()
                ORDER BY next_retry_at ASC
                LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────────
# Cockpit (admin endpoint payload)
# ──────────────────────────────────────────────────────────────────────────
async def build_cockpit(window_hours: int = 24) -> dict:
    """Aggregates all the panels of the Payment Health admin tab."""
    await ensure_payment_health_ddl()
    from database import get_pool
    pool = await get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    cockpit: dict[str, Any] = {
        "window_hours": window_hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers": {},
        "top_errors": [],
        "pending_verification": [],
        "retry_queue": {"due_now": 0, "scheduled": 0, "abandoned": 0},
    }

    async with pool.acquire() as conn:
        # Per-provider verify rates + latency p50/p95
        for prov in ("hubtel", "nowpayments"):
            agg = await conn.fetchrow(
                """SELECT
                     COUNT(*)::int AS total,
                     COUNT(*) FILTER (WHERE ok)::int AS ok_count,
                     COUNT(*) FILTER (WHERE is_paid=TRUE)::int AS paid_count,
                     COALESCE(AVG(latency_ms)::int, 0) AS latency_avg,
                     COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)::int, 0) AS latency_p50,
                     COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::int, 0) AS latency_p95,
                     COALESCE(MAX(latency_ms), 0) AS latency_max
                   FROM payment_verify_metrics
                  WHERE provider=$1 AND called_at >= $2""",
                prov, cutoff,
            )
            ipn_count = await conn.fetchval(
                """SELECT COUNT(*)::int FROM error_events
                    WHERE module=$1 AND occurred_at >= $2""",
                f"wallet.{prov}.ipn", cutoff,
            ) or 0
            cockpit["providers"][prov] = {
                "verify_calls": int(agg["total"] or 0),
                "verify_ok": int(agg["ok_count"] or 0),
                "verify_ok_rate": round(
                    100 * int(agg["ok_count"] or 0) / max(int(agg["total"] or 0), 1), 1),
                "paid_count": int(agg["paid_count"] or 0),
                "latency_avg_ms": int(agg["latency_avg"] or 0),
                "latency_p50_ms": int(agg["latency_p50"] or 0),
                "latency_p95_ms": int(agg["latency_p95"] or 0),
                "latency_max_ms": int(agg["latency_max"] or 0),
                "ipn_errors": int(ipn_count),
            }

        # Top 5 IPN-related error groups in the window
        cockpit["top_errors"] = [
            {
                "module": r["module"],
                "severity": r["severity"],
                "occurrences": int(r["occurrences"]),
                "affected_users": int(r["affected_users"] or 0),
                "message_sample": r["message_sample"],
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                "status": r["status"],
            }
            for r in await conn.fetch(
                """SELECT module, severity, occurrences, affected_users,
                          message_sample, last_seen, status
                     FROM error_groups
                    WHERE module IN ('wallet.hubtel.ipn',
                                     'wallet.nowpayments.ipn',
                                     'wallet.nowpayments.qr')
                      AND last_seen >= $1
                    ORDER BY last_seen DESC
                    LIMIT 5""",
                cutoff,
            )
        ]

        # Transactions stuck in pending_verification (hubtel/nowpayments)
        cockpit["pending_verification"] = [
            {
                "tx_id": r["tx_id"],
                "amount_usd": float(r["amount"] or 0),
                "notes": (r["notes"] or "")[:120],
                "user_id": r["to_user_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "admin_notes": (r["admin_notes"] or "")[:200],
            }
            for r in await conn.fetch(
                """SELECT tx_id, amount, notes, to_user_id, created_at,
                          admin_notes
                     FROM transactions
                    WHERE type='deposit' AND status='pending'
                      AND (admin_notes ILIKE '%vérification%'
                           OR admin_notes ILIKE '%verification%'
                           OR admin_notes ILIKE '%différé%')
                    ORDER BY created_at DESC
                    LIMIT 30"""
            )
        ]

        # Retry queue stats
        rq = await conn.fetchrow(
            """SELECT
                  COUNT(*) FILTER (WHERE resolved=FALSE AND next_retry_at <= NOW())::int AS due_now,
                  COUNT(*) FILTER (WHERE resolved=FALSE)::int AS scheduled,
                  COUNT(*) FILTER (WHERE resolved=TRUE AND last_error ILIKE 'Abandonné%')::int AS abandoned
                 FROM payment_verify_retries
                WHERE created_at >= $1 OR resolved=FALSE""",
            cutoff,
        )
        cockpit["retry_queue"] = {
            "due_now": int(rq["due_now"] or 0),
            "scheduled": int(rq["scheduled"] or 0),
            "abandoned": int(rq["abandoned"] or 0),
        }
    return cockpit


# ──────────────────────────────────────────────────────────────────────────
# Daily email digest → OPS_INBOX_EMAIL
# ──────────────────────────────────────────────────────────────────────────
def _format_digest_html(c: dict) -> str:
    rows: list[tuple[str, str]] = []
    for prov, p in c["providers"].items():
        prefix = prov.upper()
        rows.append((f"{prefix} · vérifications",
                     f"{p['verify_ok']}/{p['verify_calls']} OK ({p['verify_ok_rate']}%)"))
        rows.append((f"{prefix} · latence p50/p95",
                     f"{p['latency_p50_ms']}ms / {p['latency_p95_ms']}ms (max {p['latency_max_ms']}ms)"))
        rows.append((f"{prefix} · erreurs IPN", str(p["ipn_errors"])))
    rows.append(("Tx en pending_verification", str(len(c["pending_verification"]))))
    rows.append(("Retry queue (due now)", str(c["retry_queue"]["due_now"])))
    rows.append(("Retry queue (scheduled)", str(c["retry_queue"]["scheduled"])))
    rows.append(("Retry queue (abandoned)", str(c["retry_queue"]["abandoned"])))

    table_rows = "".join(
        f"<tr><td style='padding:6px 12px;color:#6b7280;font-size:12px'>{k}</td>"
        f"<td style='padding:6px 12px;color:#111;font-weight:600;font-size:12px'>{v}</td></tr>"
        for k, v in rows
    )
    err_html = ""
    if c["top_errors"]:
        items = "".join(
            f"<li style='font-size:12px;margin-bottom:4px'>"
            f"<strong>{e['module']}</strong> · {e['severity']} · {e['occurrences']}× · "
            f"<span style='color:#6b7280'>{(e['message_sample'] or '')[:120]}</span></li>"
            for e in c["top_errors"]
        )
        err_html = f"<h3 style='margin-top:18px;font-size:13px;color:#111'>Top erreurs IPN</h3><ul style='padding-left:18px;margin:8px 0'>{items}</ul>"
    return f"""<!doctype html><html><body style="font-family:'Inter','Helvetica',Arial,sans-serif;background:#f6f7fb;padding:24px;margin:0">
  <div style="max-width:620px;margin:0 auto;background:white;border-radius:14px;overflow:hidden;border:1px solid #e5e7eb">
    <div style="background:linear-gradient(135deg,#0F056B,#FFD700);padding:18px 22px;color:white">
      <div style="font-family:'Outfit',sans-serif;font-weight:800;font-size:18px">JAPAP · Payment Health Daily</div>
      <div style="font-size:11px;opacity:.85;margin-top:2px">Fenêtre : {c['window_hours']}h glissantes · {c['generated_at']}</div>
    </div>
    <div style="padding:22px">
      <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">{table_rows}</table>
      {err_html}
    </div>
    <div style="padding:14px 22px;border-top:1px solid #e5e7eb;font-size:11px;color:#9ca3af">
      Envoi automatique — connectez-vous au cockpit admin pour intervenir.
    </div>
  </div></body></html>"""


async def send_daily_digest(force: bool = False, worker_id: str = "") -> dict:
    """Build cockpit + send digest to OPS_INBOX. Returns the cockpit payload.

    iter151 — Strict once-per-day guarantee.
    A row in `payment_health_digests` is INSERT-ed *before* the actual mail
    is sent. The `digest_date PRIMARY KEY` makes this atomic across all
    backend processes (multi-worker, multi-pod) — a second concurrent
    caller hits a unique-violation and exits cleanly without re-sending.

    Pass `force=True` from the manual admin endpoint
    (`POST /api/admin/payment-health/digest`) to bypass the lock when ops
    deliberately wants a re-send.
    """
    import os
    from datetime import date
    from database import get_pool
    from services.email_service import send_email

    pool = await get_pool()
    today = date.today()

    if not force:
        async with pool.acquire() as conn:
            # Fast pre-check — avoids logging "skipped" noise when ops
            # already shipped today's mail.
            existing = await conn.fetchval(
                "SELECT 1 FROM payment_health_digests WHERE digest_date = $1",
                today,
            )
            if existing:
                logger.info(f"[payment-health] digest already sent for {today} — skipping")
                return {"sent_to": None, "sent": False, "skipped": True,
                        "reason": "already_sent_today", "digest_date": today.isoformat()}

    cockpit = await build_cockpit(window_hours=24)
    inbox = os.environ.get("OPS_INBOX_EMAIL", "liportalmerchand@gmail.com")
    html = _format_digest_html(cockpit)
    text = (
        "JAPAP — Payment Health Daily ({}h)\n\n"
        "Hubtel verify OK: {}/{} ({}%) · p95 {}ms · IPN err {}\n"
        "NowPayments verify OK: {}/{} ({}%) · p95 {}ms · IPN err {}\n"
        "Pending verification: {} | Retry queue due: {} scheduled: {} abandoned: {}"
    ).format(
        cockpit["window_hours"],
        cockpit["providers"]["hubtel"]["verify_ok"],
        cockpit["providers"]["hubtel"]["verify_calls"],
        cockpit["providers"]["hubtel"]["verify_ok_rate"],
        cockpit["providers"]["hubtel"]["latency_p95_ms"],
        cockpit["providers"]["hubtel"]["ipn_errors"],
        cockpit["providers"]["nowpayments"]["verify_ok"],
        cockpit["providers"]["nowpayments"]["verify_calls"],
        cockpit["providers"]["nowpayments"]["verify_ok_rate"],
        cockpit["providers"]["nowpayments"]["latency_p95_ms"],
        cockpit["providers"]["nowpayments"]["ipn_errors"],
        len(cockpit["pending_verification"]),
        cockpit["retry_queue"]["due_now"],
        cockpit["retry_queue"]["scheduled"],
        cockpit["retry_queue"]["abandoned"],
    )

    # Atomic "claim" of today's slot. UNIQUE on digest_date guarantees
    # exactly one INSERT wins across processes/pods. If we lose the race
    # we exit silently instead of re-sending.
    if not force:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO payment_health_digests
                            (digest_date, worker_id, recipients, rows_count, status)
                       VALUES ($1, $2, $3, $4, 'sending')""",
                    today, (worker_id or "")[:32], inbox,
                    len(cockpit.get("pending_verification") or []),
                )
        except Exception as e:
            # asyncpg.exceptions.UniqueViolationError → another worker
            # already claimed today. This is the expected race outcome.
            logger.info(f"[payment-health] another worker already claimed {today}: {e}")
            return {"sent_to": None, "sent": False, "skipped": True,
                    "reason": "race_lost", "digest_date": today.isoformat()}

    sent = False
    try:
        sent = await send_email(inbox, "JAPAP · Payment Health Daily", html, text)
    except Exception as e:
        logger.warning(f"payment_health digest failed: {e}")

    # Mark final status (success or failure) for ops debugging.
    try:
        async with pool.acquire() as conn:
            if force:
                # Force-resends don't claim a row; just record the audit
                # entry (multi-row UPSERT keyed on date+sent_at).
                await conn.execute(
                    """INSERT INTO payment_health_digests
                            (digest_date, worker_id, recipients, rows_count, status)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (digest_date) DO UPDATE
                          SET sent_at = NOW(),
                              worker_id = EXCLUDED.worker_id,
                              status = EXCLUDED.status""",
                    today, (worker_id or "force")[:32], inbox,
                    len(cockpit.get("pending_verification") or []),
                    "sent" if sent else "failed",
                )
            else:
                await conn.execute(
                    """UPDATE payment_health_digests
                          SET status = $2, sent_at = NOW()
                        WHERE digest_date = $1""",
                    today, "sent" if sent else "failed",
                )
    except Exception as e:
        logger.warning(f"[payment-health] status update failed: {e}")

    return {"sent_to": inbox, "sent": bool(sent), "cockpit": cockpit,
            "digest_date": today.isoformat()}


__all__ = [
    "ensure_payment_health_ddl",
    "track_verify", "measure_verify",
    "schedule_verify_retry", "mark_retry_resolved",
    "list_due_retries",
    "build_cockpit",
    "send_daily_digest",
    "MAX_RETRY_ATTEMPTS", "BACKOFF_MINUTES",
]
