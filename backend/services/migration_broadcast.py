"""
JAPAP — Migration Broadcast Campaign service (iter153)
======================================================
Drives a controlled-rate email campaign to invite legacy JAPAP 1.0 users to
reactivate their account on JAPAP 4.0 by setting a new password.

Hard guarantees:
  • ≤ 900 emails per UTC day per campaign (configurable).
  • Resumable after server restart (every state lives in PostgreSQL — no
    in-memory cursors). Re-entry is idempotent.
  • One row per (campaign, user) — DB UNIQUE constraint guards against
    duplicate sends even if the worker fires twice.
  • Excludes invalid emails, prior bounces (`email_logs.event='bounced'`),
    users who already migrated (`migration_completed=TRUE`), and users who
    unsubscribed (`users.email_subscribed=FALSE`).
  • Cooperative pause/resume/stop via a single `status` column.

Tables created (idempotent):
  • `migration_broadcast_campaigns`
  • `migration_broadcast_targets`

Worker loop:
  • Wakes every 60s.
  • For each campaign with status='running' AND start_at <= NOW():
      - Compute remaining_quota = daily_limit - sent_today_count.
      - Pull next BATCH_SIZE pending targets, pre-claim them via UPDATE
        RETURNING (status='sending') to avoid races.
      - For each: generate a long-lived reset token, send the migration
        email via Resend, persist the outcome.
      - Sleep ~1s between sends to respect Resend's 10/sec rate limit.
      - Stop the loop when quota exhausted.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from database import get_pool
from services.email_service import send_email_detailed

logger = logging.getLogger(__name__)

DAILY_LIMIT_DEFAULT = 900
BATCH_SIZE = 25                 # claim+send chunk
WORKER_TICK_SECONDS = 60
SEND_THROTTLE_SECONDS = 1.0     # 1 email / sec → 60/min, well under Resend 10/sec
RESET_TOKEN_TTL_DAYS = 30       # generous so users have time to click

# iter154 — 🔴 GLOBAL KILL SWITCH.
# Following Resend's high-bounce warning, the broadcast feature is DISABLED.
# Every send-side path is short-circuited:
#   • the worker loop refuses to process campaigns,
#   • create_campaign() raises before populating any target,
#   • set_status() refuses to flip a campaign back to 'running'.
# Re-enable only by setting BROADCAST_ENABLED=true in /app/backend/.env
# AFTER the legacy email list has been cleaned + warmed up via Resend's
# verification API. Default = disabled (False).
BROADCAST_ENABLED = (os.environ.get("BROADCAST_ENABLED", "false").lower()
                     in ("true", "1", "yes"))


# ──────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────
async def ensure_broadcast_tables() -> None:
    """Create the broadcast tables if missing. Idempotent."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS migration_broadcast_campaigns (
                id              SERIAL PRIMARY KEY,
                campaign_id     VARCHAR(48) UNIQUE NOT NULL,
                name            VARCHAR(160) NOT NULL,
                status          VARCHAR(20) NOT NULL DEFAULT 'scheduled',
                daily_limit     INTEGER NOT NULL DEFAULT 900,
                start_at        TIMESTAMPTZ NOT NULL,
                total_targets   INTEGER NOT NULL DEFAULT 0,
                sent_count      INTEGER NOT NULL DEFAULT 0,
                delivered_count INTEGER NOT NULL DEFAULT 0,
                opened_count    INTEGER NOT NULL DEFAULT 0,
                clicked_count   INTEGER NOT NULL DEFAULT 0,
                bounced_count   INTEGER NOT NULL DEFAULT 0,
                failed_count    INTEGER NOT NULL DEFAULT 0,
                excluded_count  INTEGER NOT NULL DEFAULT 0,
                created_by      VARCHAR(48),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS migration_broadcast_targets (
                id                  BIGSERIAL PRIMARY KEY,
                campaign_id         VARCHAR(48) NOT NULL,
                user_id             VARCHAR(48) NOT NULL,
                email               VARCHAR(255) NOT NULL,
                status              VARCHAR(20) NOT NULL DEFAULT 'pending',
                reset_token         VARCHAR(80),
                provider_message_id VARCHAR(80),
                sent_at             TIMESTAMPTZ,
                delivered_at        TIMESTAMPTZ,
                opened_at           TIMESTAMPTZ,
                clicked_at          TIMESTAMPTZ,
                bounced_at          TIMESTAMPTZ,
                failure_reason      TEXT,
                attempts            INTEGER NOT NULL DEFAULT 0,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT mbt_unique_user UNIQUE (campaign_id, user_id)
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mbt_campaign_status
                ON migration_broadcast_targets(campaign_id, status);
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mbt_email
                ON migration_broadcast_targets(LOWER(email));
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mbt_provider_msgid
                ON migration_broadcast_targets(provider_message_id)
                WHERE provider_message_id IS NOT NULL;
        """)
    logger.info("[broadcast] tables ensured")


# ──────────────────────────────────────────────────────────────────────
# Email rendering
# ──────────────────────────────────────────────────────────────────────
def _frontend_base() -> str:
    return (os.environ.get("FRONTEND_URL")
            or os.environ.get("REACT_APP_BACKEND_URL")
            or "https://japap-refactor.preview.emergentagent.com").rstrip("/")


def _logo_url() -> str:
    return f"{_frontend_base()}/japap-logo.jpg"


def _migration_email_html(reset_url: str, first_name: str = "") -> str:
    logo = _logo_url()
    greeting = f"Bonjour {first_name}," if first_name else "Bonjour,"
    return f"""
    <div style="font-family:Manrope,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;">
      <div style="background:#fff;padding:24px;border-radius:16px 16px 0 0;text-align:center;border:1px solid #eee;border-bottom:none;">
        <img src="{logo}" alt="JAPAP" style="height:60px;width:auto;display:inline-block;" />
      </div>
      <div style="background:#fff;padding:32px 28px;border:1px solid #eee;border-top:none;border-radius:0 0 16px 16px;">
        <h2 style="color:#0F056B;margin:0 0 12px;font-family:'Outfit',Arial,sans-serif;font-size:22px;">
          Ton compte JAPAP a été migré vers JAPAP 4.0 🎉
        </h2>
        <p style="color:#444;font-size:15px;line-height:1.55;margin:0 0 16px;">
          {greeting}<br/><br/>
          Bonne nouvelle — JAPAP fait peau neuve !
          Nous avons migré ton ancien compte JAPAP 1.0 vers la toute nouvelle plateforme
          <strong>JAPAP 4.0</strong> : feed social, messagerie temps réel, wallet, crypto,
          marketplace, jeux et bien plus.
        </p>
        <p style="color:#444;font-size:15px;line-height:1.55;margin:0 0 24px;">
          Pour des raisons de sécurité, ton ancien mot de passe n'a pas été conservé.
          Définis simplement un nouveau mot de passe en cliquant sur le bouton ci-dessous —
          tu retrouveras ensuite tous tes contacts, ton historique et ton wallet.
        </p>
        <div style="text-align:center;margin:28px 0;">
          <a href="{reset_url}" style="background:#0F056B;color:#fff;padding:16px 36px;
             border-radius:14px;text-decoration:none;font-weight:700;font-size:16px;
             font-family:'Outfit',Arial,sans-serif;display:inline-block;">
            Définir mon nouveau mot de passe
          </a>
        </div>
        <p style="color:#888;font-size:13px;line-height:1.5;margin:0 0 8px;">
          Ce lien est valable 30 jours et ne peut être utilisé qu'une seule fois.
        </p>
        <p style="color:#888;font-size:11px;word-break:break-all;margin:0 0 16px;">
          {reset_url}
        </p>
        <hr style="border:none;border-top:1px solid #eee;margin:20px 0;" />
        <p style="color:#999;font-size:12px;line-height:1.4;margin:0;">
          Si tu n'es plus l'utilisateur de ce compte, ignore simplement ce mail —
          aucune action ne sera effectuée et nous ne te recontacterons pas pour
          cette migration.<br/>
          — L'équipe JAPAP
        </p>
      </div>
    </div>
    """


def _migration_email_text(reset_url: str, first_name: str = "") -> str:
    greeting = f"Bonjour {first_name},\n\n" if first_name else "Bonjour,\n\n"
    return (f"{greeting}"
            f"Ton compte JAPAP a été migré vers JAPAP 4.0.\n\n"
            f"Définis ton nouveau mot de passe ici (lien valable 30 jours) :\n"
            f"{reset_url}\n\n"
            f"Si tu n'es plus l'utilisateur de ce compte, ignore ce mail.\n"
            f"— L'équipe JAPAP")


# ──────────────────────────────────────────────────────────────────────
# Campaign management
# ──────────────────────────────────────────────────────────────────────
async def create_campaign(
    *,
    name: str,
    start_at: datetime,
    daily_limit: int = DAILY_LIMIT_DEFAULT,
    created_by: Optional[str] = None,
) -> dict:
    """Create a campaign and populate its target list from the legacy
    users base, applying every exclusion rule. Idempotent on duplicate
    `name` — caller must use a fresh name.

    iter154 — guarded by BROADCAST_ENABLED kill switch. Disabled by default.
    """
    if not BROADCAST_ENABLED:
        raise RuntimeError(
            "BROADCAST_DISABLED: la campagne broadcast est désactivée "
            "(kill switch iter154 — taux de bounce élevé signalé par Resend). "
            "Active explicitement BROADCAST_ENABLED=true dans backend/.env "
            "après nettoyage de la liste."
        )
    if daily_limit <= 0 or daily_limit > 5000:
        raise ValueError("daily_limit must be between 1 and 5000")
    cid = f"camp_{uuid.uuid4().hex[:18]}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO migration_broadcast_campaigns
                       (campaign_id, name, status, daily_limit, start_at, created_by)
                   VALUES ($1, $2, 'scheduled', $3, $4, $5)""",
                cid, name, daily_limit, start_at, created_by,
            )
            # Populate targets from legacy users.
            #   • legacy_id IS NOT NULL  → only ETL imports
            #   • is_legacy_account     → canonical flag
            #   • migration_completed=FALSE → not yet reconnected
            #   • email LIKE '%@%.%'    → basic syntactic validity
            #   • email_subscribed     → respect prior unsubscribes
            #   • LEFT JOIN bounces     → exclude addresses that bounced
            #     in any prior `email_logs` row
            res = await conn.execute(
                """INSERT INTO migration_broadcast_targets
                       (campaign_id, user_id, email, status)
                   SELECT $1, u.user_id, LOWER(u.email), 'pending'
                     FROM users u
                LEFT JOIN (
                          SELECT DISTINCT LOWER(email) AS email
                            FROM email_logs
                           WHERE event IN ('bounced', 'complained')
                         ) b ON LOWER(u.email) = b.email
                    WHERE u.legacy_id IS NOT NULL
                      AND u.is_legacy_account = TRUE
                      AND u.migration_completed = FALSE
                      AND u.email IS NOT NULL
                      AND u.email LIKE '%@%.%'
                      AND COALESCE(u.email_subscribed, TRUE) = TRUE
                      AND b.email IS NULL
                   ON CONFLICT (campaign_id, user_id) DO NOTHING""",
                cid,
            )
            inserted = int(res.split()[-1]) if res and res.split()[-1].isdigit() else 0
            # Excluded count = total legacy candidates - inserted
            total_legacy = await conn.fetchval(
                """SELECT COUNT(*) FROM users
                    WHERE legacy_id IS NOT NULL
                      AND is_legacy_account = TRUE
                      AND migration_completed = FALSE"""
            )
            await conn.execute(
                """UPDATE migration_broadcast_campaigns
                      SET total_targets = $1,
                          excluded_count = $2,
                          updated_at = NOW()
                    WHERE campaign_id = $3""",
                inserted, max(0, int(total_legacy or 0) - inserted), cid,
            )
            row = await conn.fetchrow(
                "SELECT * FROM migration_broadcast_campaigns WHERE campaign_id = $1",
                cid,
            )
    logger.warning(
        "[broadcast] campaign_created id=%s name=%s daily_limit=%d targets=%d start_at=%s",
        cid, name, daily_limit, inserted, start_at.isoformat(),
    )
    return _row_to_dict(row)


async def list_campaigns() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM migration_broadcast_campaigns
                ORDER BY created_at DESC LIMIT 50"""
        )
    return [_row_to_dict(r) for r in rows]


async def get_campaign(campaign_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM migration_broadcast_campaigns WHERE campaign_id = $1",
            campaign_id,
        )
        if not row:
            return None
        d = _row_to_dict(row)
        # live status counts (re-read for freshness — denormalised counters
        # are kept up-to-date but we still aggregate to be exact).
        counts = await conn.fetch(
            """SELECT status, COUNT(*) AS c
                 FROM migration_broadcast_targets
                WHERE campaign_id = $1 GROUP BY status""",
            campaign_id,
        )
        d["status_breakdown"] = {r["status"]: int(r["c"]) for r in counts}
        # sent today
        d["sent_today"] = int(await conn.fetchval(
            """SELECT COUNT(*) FROM migration_broadcast_targets
                WHERE campaign_id = $1 AND sent_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')""",
            campaign_id,
        ) or 0)
    return d


async def set_status(campaign_id: str, new_status: str) -> bool:
    if new_status not in ("scheduled", "running", "paused", "stopped", "completed"):
        raise ValueError(f"invalid status {new_status}")
    # iter154 — kill switch refuses to bring a campaign back to life when
    # broadcast is globally disabled. Stop / pause / completed remain
    # always available (terminal/safe states).
    if new_status == "running" and not BROADCAST_ENABLED:
        raise RuntimeError(
            "BROADCAST_DISABLED: impossible de relancer une campagne — "
            "le mode broadcast est désactivé (iter154)."
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE migration_broadcast_campaigns
                  SET status = $1, updated_at = NOW()
                WHERE campaign_id = $2""",
            new_status, campaign_id,
        )
    logger.warning("[broadcast] status_change id=%s -> %s", campaign_id, new_status)
    return result.endswith(" 1")


async def list_targets(campaign_id: str, status: Optional[str] = None,
                        limit: int = 100, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """SELECT user_id, email, status, provider_message_id, sent_at,
                          delivered_at, opened_at, clicked_at, bounced_at,
                          failure_reason, attempts
                     FROM migration_broadcast_targets
                    WHERE campaign_id = $1 AND status = $2
                    ORDER BY id DESC LIMIT $3 OFFSET $4""",
                campaign_id, status, limit, offset,
            )
        else:
            rows = await conn.fetch(
                """SELECT user_id, email, status, provider_message_id, sent_at,
                          delivered_at, opened_at, clicked_at, bounced_at,
                          failure_reason, attempts
                     FROM migration_broadcast_targets
                    WHERE campaign_id = $1
                    ORDER BY id DESC LIMIT $2 OFFSET $3""",
                campaign_id, limit, offset,
            )
    out = []
    for r in rows:
        d = dict(r)
        for k in ("sent_at", "delivered_at", "opened_at", "clicked_at", "bounced_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        out.append(d)
    return out


def _row_to_dict(row) -> dict:
    if not row:
        return {}
    d = dict(row)
    for k in ("created_at", "updated_at", "start_at"):
        if d.get(k) and isinstance(d[k], datetime):
            d[k] = d[k].isoformat()
    return d


# ──────────────────────────────────────────────────────────────────────
# Worker
# ──────────────────────────────────────────────────────────────────────
async def _send_one(conn, target_row, campaign_id: str) -> tuple[bool, str]:
    """Generate token, send email, persist outcome. Returns (ok, reason)."""
    user_id = target_row["user_id"]
    email = target_row["email"]
    # Look up user first_name for greeting (best-effort).
    first_name = await conn.fetchval(
        "SELECT first_name FROM users WHERE user_id = $1", user_id,
    )
    # Generate fresh reset token.
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=RESET_TOKEN_TTL_DAYS)
    await conn.execute(
        """INSERT INTO password_reset_tokens (user_id, token, expires_at)
           VALUES ($1, $2, $3)""",
        user_id, token, expires,
    )
    reset_url = f"{_frontend_base()}/reset-password?token={token}&src=migration_broadcast&c={campaign_id}"

    delivery = await send_email_detailed(
        to=email,
        subject="JAPAP 4.0 — Réactive ton compte en 30 secondes",
        html=_migration_email_html(reset_url, first_name or ""),
        text=_migration_email_text(reset_url, first_name or ""),
        kind="migration_broadcast",
    )
    if delivery.get("ok"):
        await conn.execute(
            """UPDATE migration_broadcast_targets
                  SET status = 'sent',
                      reset_token = $1,
                      provider_message_id = $2,
                      sent_at = NOW(),
                      attempts = attempts + 1,
                      failure_reason = NULL
                WHERE campaign_id = $3 AND user_id = $4""",
            token, delivery.get("message_id") or "", campaign_id, user_id,
        )
        await conn.execute(
            """UPDATE migration_broadcast_campaigns
                  SET sent_count = sent_count + 1, updated_at = NOW()
                WHERE campaign_id = $1""",
            campaign_id,
        )
        return True, ""
    # Failure
    await conn.execute(
        """UPDATE migration_broadcast_targets
              SET status = 'failed',
                  attempts = attempts + 1,
                  failure_reason = $1
            WHERE campaign_id = $2 AND user_id = $3""",
        (delivery.get("error") or "send_failed")[:500], campaign_id, user_id,
    )
    await conn.execute(
        """UPDATE migration_broadcast_campaigns
              SET failed_count = failed_count + 1, updated_at = NOW()
            WHERE campaign_id = $1""",
        campaign_id,
    )
    return False, delivery.get("error") or "send_failed"


async def _process_campaign(campaign_id: str) -> dict:
    """Main per-campaign tick: claim a batch and send."""
    pool = await get_pool()
    sent = 0
    failed = 0
    skipped_quota = False
    async with pool.acquire() as conn:
        camp = await conn.fetchrow(
            "SELECT * FROM migration_broadcast_campaigns WHERE campaign_id = $1",
            campaign_id,
        )
        if not camp or camp["status"] != "running":
            return {"sent": 0, "failed": 0, "reason": "not_running"}
        if camp["start_at"].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            return {"sent": 0, "failed": 0, "reason": "before_start"}
        # quota check
        sent_today = await conn.fetchval(
            """SELECT COUNT(*) FROM migration_broadcast_targets
                WHERE campaign_id = $1
                  AND sent_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')""",
            campaign_id,
        ) or 0
        remaining = max(0, int(camp["daily_limit"]) - int(sent_today))
        if remaining <= 0:
            return {"sent": 0, "failed": 0, "reason": "daily_quota_reached"}
        # check for completion (no pending left)
        pending_count = await conn.fetchval(
            """SELECT COUNT(*) FROM migration_broadcast_targets
                WHERE campaign_id = $1 AND status = 'pending'""",
            campaign_id,
        ) or 0
        if pending_count == 0:
            await conn.execute(
                """UPDATE migration_broadcast_campaigns
                      SET status = 'completed', updated_at = NOW()
                    WHERE campaign_id = $1""",
                campaign_id,
            )
            return {"sent": 0, "failed": 0, "reason": "completed"}
        batch_size = min(BATCH_SIZE, remaining)
        # Atomic claim — flip pending → sending so a parallel worker
        # tick (or future scale-out) can't grab the same rows.
        rows = await conn.fetch(
            """UPDATE migration_broadcast_targets
                  SET status = 'sending'
                WHERE id IN (
                    SELECT id FROM migration_broadcast_targets
                     WHERE campaign_id = $1 AND status = 'pending'
                     ORDER BY id ASC
                     LIMIT $2
                     FOR UPDATE SKIP LOCKED
                )
                RETURNING id, user_id, email""",
            campaign_id, batch_size,
        )

    if not rows:
        return {"sent": 0, "failed": 0, "reason": "no_rows"}

    # Send outside the long transaction, one row at a time, with throttle.
    async with pool.acquire() as conn:
        for r in rows:
            ok, reason = await _send_one(conn, r, campaign_id)
            if ok:
                sent += 1
            else:
                failed += 1
            await asyncio.sleep(SEND_THROTTLE_SECONDS)

    return {"sent": sent, "failed": failed, "reason": "ok",
            "skipped_quota": skipped_quota}


async def _worker_loop():
    """Wakes every WORKER_TICK_SECONDS, processes every running campaign.

    iter154 — short-circuited when BROADCAST_ENABLED is False. The loop
    still spins (so the feature can be re-enabled live by editing .env
    and restarting backend) but performs no DB query / no send.
    """
    logger.info("[broadcast] worker started (tick=%ds, enabled=%s)",
                WORKER_TICK_SECONDS, BROADCAST_ENABLED)
    while True:
        if not BROADCAST_ENABLED:
            await asyncio.sleep(WORKER_TICK_SECONDS)
            continue
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                running = await conn.fetch(
                    """SELECT campaign_id FROM migration_broadcast_campaigns
                        WHERE status = 'running'
                          AND start_at <= NOW()"""
                )
            for r in running:
                try:
                    res = await _process_campaign(r["campaign_id"])
                    if res.get("sent") or res.get("failed"):
                        logger.info(
                            "[broadcast] tick id=%s sent=%d failed=%d reason=%s",
                            r["campaign_id"], res["sent"], res["failed"],
                            res.get("reason"),
                        )
                except Exception as e:
                    logger.exception("[broadcast] campaign %s failed: %s",
                                     r["campaign_id"], e)
        except Exception as e:
            logger.exception("[broadcast] worker tick failed: %s", e)
        await asyncio.sleep(WORKER_TICK_SECONDS)


def start_worker(app):
    """Attach the broadcast worker to the FastAPI app startup hook."""
    @app.on_event("startup")
    async def _start():
        await ensure_broadcast_tables()
        asyncio.create_task(_worker_loop())
        logger.info("[broadcast] startup hook registered")
