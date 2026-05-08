"""
JAPAP — Payment Verify Retry Worker (iter117)
=============================================
Background loop that retries `verify_payment_status` for transactions
stuck in `pending_verification` due to a temporarily unavailable provider
API. Honours exponential backoff defined in payment_health.BACKOFF_MINUTES.

Also dispatches the daily Payment Health digest e-mail at 18:00 UTC
(once per day, persisted in `payment_health_digests` so multi-worker /
restart races never trigger a re-send — iter151).
"""
from __future__ import annotations
import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WORKER_ID = f"pvr_{uuid.uuid4().hex[:8]}"
POLL_INTERVAL_SEC = 120  # 2 min — small enough to catch the 2-min backoff bucket
# iter151 — CEO request: send the daily digest at 18:00 UTC instead of 08:00.
DIGEST_HOUR_UTC = int(os.environ.get("PAYMENT_HEALTH_DIGEST_HOUR_UTC", "18"))
# iter157 — CEO request: after 24 h without provider confirmation, a
# pending deposit is flipped to `expired` (one last provider probe first
# to be safe). Prevents the admin list from rotting indefinitely.
DEPOSIT_TTL_HOURS = int(os.environ.get("DEPOSIT_PENDING_TTL_HOURS", "24"))

_task: asyncio.Task | None = None
_stop_flag = asyncio.Event()
_last_digest_date: str = ""


async def _retry_one(prov: str, tx_id: str, provider_ref: str) -> tuple[bool, str]:
    """Attempt one verify call. Returns (succeeded_credit, reason).
    The actual credit logic mirrors the IPN webhook flow but only acts
    when the provider authoritatively confirms `is_paid=True`."""
    from database import get_pool
    from services.payment_health import (
        measure_verify, mark_retry_resolved,
    )
    pool = await get_pool()

    try:
        if prov == "hubtel":
            from services.hubtel_service import (
                verify_transaction_status, HubtelConfigError,
            )
            try:
                async with measure_verify("hubtel", tx_id) as m:
                    res = await verify_transaction_status(
                        client_reference=tx_id, checkout_id=provider_ref or "",
                    )
                    m["ok"] = bool(res.get("ok"))
                    m["is_paid"] = bool(res.get("is_paid"))
            except HubtelConfigError as e:
                return False, f"config_missing: {e}"
        elif prov == "nowpayments":
            from services.nowpayments_service import (
                verify_payment_status, NowPaymentsConfigError,
            )
            try:
                async with measure_verify("nowpayments", tx_id) as m:
                    res = await verify_payment_status(provider_ref or tx_id)
                    m["ok"] = bool(res.get("ok"))
                    m["is_paid"] = bool(res.get("is_paid"))
            except NowPaymentsConfigError as e:
                return False, f"config_missing: {e}"
        else:
            return False, f"unknown_provider: {prov}"
    except Exception as e:
        return False, f"verify_crash: {str(e)[:200]}"

    if not res.get("ok"):
        return False, f"api_unavailable: {res.get('reason') or 'unknown'}"
    if not res.get("is_paid"):
        return False, f"not_paid_yet: status={res.get('status')}"

    # Authoritative success → credit (idempotent: skip if already completed)
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT * FROM transactions WHERE tx_id=$1 AND type='deposit'", tx_id)
        if not tx:
            return False, "tx_disappeared"
        if tx["status"] == "completed":
            await mark_retry_resolved(prov, tx_id, "already_credited")
            return True, "already_credited"

        async with conn.transaction():
            await conn.execute(
                "UPDATE wallets SET balance=balance+$1, updated_at=$2 WHERE user_id=$3",
                tx["amount"], datetime.now(timezone.utc), tx["to_user_id"],
            )
            ref = res.get("provider_ref") or provider_ref or tx["reference"]
            await conn.execute(
                "UPDATE transactions SET status='completed', reference=$1, "
                "admin_notes=$2 WHERE tx_id=$3",
                ref, f"auto-credited via retry queue ({prov})", tx_id,
            )
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """INSERT INTO notifications
                       (notif_id, user_id, type, title, message, data)
                       VALUES ($1, $2, 'deposit_completed',
                               'Dépôt confirmé', $3, $4)""",
                notif_id, tx["to_user_id"],
                f"Votre dépôt de {tx['amount']} USD a été crédité.",
                f'{{"tx_id": "{tx_id}", "provider": "{prov}", "via": "retry_queue"}}',
            )
    await mark_retry_resolved(prov, tx_id, f"credited via {prov} retry")
    logger.warning(f"[verify-retry] CREDITED {prov}:{tx_id}")
    return True, "credited"


async def _tick_retries() -> None:
    """Pull due retries and process them (max 5 per tick to keep loop short)."""
    from services.payment_health import list_due_retries, schedule_verify_retry
    due = await list_due_retries(limit=5)
    for r in due:
        ok, reason = await _retry_one(r["provider"], r["tx_id"],
                                       r.get("provider_ref") or "")
        if ok:
            continue
        # Bump attempts + reschedule (or abandon at MAX_RETRY_ATTEMPTS)
        await schedule_verify_retry(
            r["provider"], r["tx_id"],
            provider_ref=r.get("provider_ref") or "",
            reason=reason,
        )


async def _sweep_expired_deposits() -> int:
    """iter157 — Flip long-pending deposits to `expired`.

    A deposit is expired when:
      • type = 'deposit'
      • status = 'pending'
      • created_at < NOW() - DEPOSIT_TTL_HOURS

    Before flipping, we do ONE last provider probe (if a reference is
    stored) so we don't accidentally expire a paid-but-webhook-lost
    deposit. If the provider confirms `is_paid=true`, we credit and
    mark the deposit completed instead of expired.

    Returns: number of deposits expired on this tick (for observability).
    """
    from database import get_pool
    pool = await get_pool()
    # DEPOSIT_TTL_HOURS is already an int (parsed via `int(...)` at import
    # time) — interpolation is safe here, and avoids asyncpg's strict
    # parameter-type resolution for `INTERVAL * $1`.
    ttl_h = int(DEPOSIT_TTL_HOURS)
    cutoff_query = f"""
        SELECT tx_id, to_user_id, amount, amount_usd, currency, reference, notes, created_at
          FROM transactions
         WHERE type = 'deposit'
           AND status = 'pending'
           AND created_at < NOW() - INTERVAL '{ttl_h} hours'
         ORDER BY created_at ASC
         LIMIT 100
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(cutoff_query)
    if not rows:
        return 0

    expired = 0
    credited = 0
    for r in rows:
        tx_id = r["tx_id"]
        user_id = r["to_user_id"]
        amount = r["amount"]
        currency = r["currency"]
        ref = r["reference"] or ""
        notes_lower = (r["notes"] or "").lower()
        # One last provider probe to avoid expiring a paid deposit.
        is_paid = False
        provider = None
        if ref:
            try:
                if "nowpayments" in notes_lower:
                    provider = "nowpayments"
                    from services.nowpayments_service import verify_payment_status
                    v = await verify_payment_status(ref)
                    is_paid = bool(v.get("ok") and v.get("is_paid"))
                elif "hubtel" in notes_lower:
                    provider = "hubtel"
                    from services.hubtel_service import verify_transaction_status
                    v = await verify_transaction_status(checkout_id=ref)
                    is_paid = bool(v.get("ok") and v.get("is_paid"))
            except Exception as e:
                logger.warning(
                    "[ttl] final probe failed tx=%s provider=%s err=%s",
                    tx_id, provider, e,
                )
        async with pool.acquire() as conn:
            async with conn.transaction():
                locked = await conn.fetchrow(
                    "SELECT status FROM transactions WHERE tx_id = $1 FOR UPDATE",
                    tx_id,
                )
                if not locked or locked["status"] != "pending":
                    continue  # already handled by another actor
                if is_paid:
                    # Late but legit — credit it. iter158 — always USD.
                    credit_amt = r["amount_usd"] if r["amount_usd"] is not None else r["amount"]
                    await conn.execute(
                        "UPDATE wallets SET balance = balance + $1, "
                        "updated_at = $2 WHERE user_id = $3",
                        credit_amt, datetime.now(timezone.utc), user_id,
                    )
                    await conn.execute(
                        """UPDATE transactions
                              SET status = 'completed',
                                  admin_notes = COALESCE(NULLIF(admin_notes, ''), '')
                                                || ' | TTL-sweep: provider confirmed via '
                                                || $1
                            WHERE tx_id = $2""",
                        provider, tx_id,
                    )
                    try:
                        import uuid as _uuid
                        await conn.execute(
                            """INSERT INTO notifications
                                   (notif_id, user_id, type, title, message, data)
                               VALUES ($1, $2, 'deposit_completed', $3, $4, $5)""",
                            _uuid.uuid4().hex, user_id,
                            "Dépôt crédité",
                            f"Votre dépôt de {amount} {currency} vient "
                            f"d'être confirmé par {provider}.",
                            f'{{"tx_id":"{tx_id}","amount":"{amount}"}}',
                        )
                    except Exception:
                        pass  # notifications table schema variance
                    credited += 1
                    continue
                # Not paid — flip to expired.
                await conn.execute(
                    f"""UPDATE transactions
                           SET status = 'expired',
                               admin_notes = COALESCE(NULLIF(admin_notes, ''), '')
                                             || ' | TTL expired after {ttl_h}h '
                                             || 'without provider confirmation'
                         WHERE tx_id = $1""",
                    tx_id,
                )
                try:
                    import uuid as _uuid
                    await conn.execute(
                        """INSERT INTO notifications
                               (notif_id, user_id, type, title, message, data)
                           VALUES ($1, $2, 'deposit_expired', $3, $4, $5)""",
                        _uuid.uuid4().hex, user_id,
                        "Dépôt expiré",
                        ("Le paiement n'a pas été reçu dans les "
                         f"{DEPOSIT_TTL_HOURS} heures. Si tu as vraiment "
                         "payé, contacte le support avec ta preuve. "
                         "Sinon, tu peux relancer un nouveau dépôt."),
                        f'{{"tx_id":"{tx_id}","amount":"{amount}"}}',
                    )
                except Exception:
                    pass
                expired += 1
    if expired or credited:
        logger.warning(
            "[ttl] sweep done: expired=%d credited_late=%d (TTL=%dh)",
            expired, credited, DEPOSIT_TTL_HOURS,
        )
    return expired


async def _maybe_dispatch_digest() -> None:
    """Send the Payment Health daily digest at most once per day at
    DIGEST_HOUR_UTC (default 18:00 UTC).

    iter151 — The once-per-day guard now lives in the database
    (`payment_health_digests.digest_date PRIMARY KEY`). The in-memory
    `_last_digest_date` remains as a fast pre-filter so we don't even
    open a connection on the 99.9% of ticks where there's nothing to do.
    """
    global _last_digest_date
    now = datetime.now(timezone.utc)
    today_key = now.strftime("%Y-%m-%d")
    if _last_digest_date == today_key:
        return
    if now.hour < DIGEST_HOUR_UTC:
        return
    try:
        from services.payment_health import send_daily_digest
        result = await send_daily_digest(force=False, worker_id=WORKER_ID)
        # Always update the in-memory marker — whether we sent the mail
        # or skipped because another worker beat us to it, today's slot
        # is closed for this process.
        _last_digest_date = today_key
        if result.get("sent"):
            logger.info(f"[payment-health] daily digest sent for {today_key}")
        elif result.get("skipped"):
            logger.info(f"[payment-health] digest already claimed for {today_key} ({result.get('reason')})")
    except Exception as e:
        logger.warning(f"[payment-health] daily digest failed: {e}")


async def _loop() -> None:
    logger.info(f"[PaymentVerifyRetry {WORKER_ID}] loop started "
                f"(poll {POLL_INTERVAL_SEC}s, digest at {DIGEST_HOUR_UTC}h UTC)")
    try:
        from services.payment_health import ensure_payment_health_ddl
        await ensure_payment_health_ddl()
    except Exception as e:
        logger.warning(f"[PaymentVerifyRetry] DDL ensure failed: {e}")
    while not _stop_flag.is_set():
        try:
            await _tick_retries()
        except Exception as e:
            logger.exception(f"[PaymentVerifyRetry] tick failed: {e}")
        try:
            await _sweep_expired_deposits()
        except Exception as e:
            logger.warning(f"[PaymentVerifyRetry] TTL sweep failed: {e}")
        try:
            # iter176 — Marketplace Escrow auto-release sweep
            from routes.marketplace import sweep_auto_release, sweep_expired_boosts
            res = await sweep_auto_release(actor_id="system")
            if res.get("released"):
                logger.info(f"[mkt-escrow] auto-release {res}")
            res2 = await sweep_expired_boosts()
            if any(res2.values()):
                logger.info(f"[mkt-boost] expiry sweep {res2}")
        except Exception as e:
            logger.warning(f"[PaymentVerifyRetry] marketplace sweeps failed: {e}")
        try:
            # iter178 — currency canonical sweep (USD-only enforcement)
            from services.currency_canonical_sweep import run_full_sweep
            res = await run_full_sweep(source="worker")
            if res["wallets"].get("converted") or res["transactions"].get("backfilled"):
                logger.info(f"[currency-canonical] sweep {res}")
        except Exception as e:
            logger.warning(f"[PaymentVerifyRetry] currency sweep failed: {e}")
        try:
            await _maybe_dispatch_digest()
        except Exception as e:
            logger.warning(f"[PaymentVerifyRetry] digest tick failed: {e}")
        try:
            await asyncio.wait_for(_stop_flag.wait(), timeout=POLL_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass
    logger.info(f"[PaymentVerifyRetry {WORKER_ID}] loop stopped")


def start_worker(app) -> None:
    @app.on_event("startup")
    async def _start():
        global _task
        if _task is None or _task.done():
            _task = asyncio.create_task(_loop())

    @app.on_event("shutdown")
    async def _stop():
        _stop_flag.set()
        if _task:
            try:
                await asyncio.wait_for(_task, timeout=5)
            except Exception:
                pass


__all__ = ["start_worker", "_retry_one", "_tick_retries", "_sweep_expired_deposits"]
