"""
iter88 — Admin alerts (Wallet anomalies + rate-limit hits).

Thin service that :
  • looks up all admin / super_admin users from DB
  • sends a OneSignal push to each (via existing push_service)
  • also logs to audit_logs for a permanent trail

De-duplication : each alert includes a signature key (`alert_key`) and we
record a row in `admin_alerts` (created if missing). The same alert is
NOT re-sent within the last `window_minutes`.

Public API
──────────
  raise_alert(kind, title, body, url=None, alert_key=None, window_minutes=30)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from database import get_pool
from services.push_service import configured as push_configured, send_push_to_user, build_payload

logger = logging.getLogger(__name__)

_DDL_DONE = False


async def _ensure_ddl(conn) -> None:
    global _DDL_DONE
    if _DDL_DONE:
        return
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_alerts (
          id BIGSERIAL PRIMARY KEY,
          kind        VARCHAR(64) NOT NULL,
          alert_key   VARCHAR(128) NOT NULL,
          title       VARCHAR(200) NOT NULL,
          body        TEXT NOT NULL,
          url         VARCHAR(500),
          created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          push_sent   BOOLEAN NOT NULL DEFAULT FALSE,
          push_error  TEXT
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_alerts_key_at ON admin_alerts(alert_key, created_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_alerts_created ON admin_alerts(created_at DESC)")
    _DDL_DONE = True


async def _is_duplicate(conn, alert_key: str, window_minutes: int) -> bool:
    row = await conn.fetchval(
        """SELECT id FROM admin_alerts
           WHERE alert_key = $1 AND created_at > NOW() - ($2 || ' minutes')::interval
           LIMIT 1""",
        alert_key, str(window_minutes),
    )
    return bool(row)


async def raise_alert(
    kind: str, title: str, body: str,
    url: Optional[str] = None,
    alert_key: Optional[str] = None,
    window_minutes: int = 30,
) -> dict:
    """Non-blocking fire-and-forget alert. Runs the DB + push I/O in the
    background — the caller (usually a hot path like /wallet/send) never
    blocks on this call."""
    key = alert_key or f"{kind}:default"

    async def _work() -> None:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await _ensure_ddl(conn)
                if await _is_duplicate(conn, key, window_minutes):
                    return
                # persist first (so we always have a trail even if push fails)
                alert_id = await conn.fetchval(
                    """INSERT INTO admin_alerts (kind, alert_key, title, body, url)
                       VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                    kind, key, title, body, url,
                )
                admins = await conn.fetch(
                    "SELECT user_id FROM users WHERE role IN ('admin','super_admin') AND is_active=TRUE"
                )

            # Fire push (outside the DB lock)
            if not push_configured():
                logger.info(
                    "admin_alert[%s] push_skipped (onesignal not configured) — title=%r key=%r",
                    kind, title, key,
                )
                return
            payload = build_payload(title=title, body=body, url=url or "/admin")
            errors = []
            sent = 0
            for u in admins:
                try:
                    res = await send_push_to_user(u["user_id"], payload)
                    if res.get("ok"):
                        sent += 1
                    else:
                        errors.append(res.get("error", "unknown"))
                except Exception as e:
                    errors.append(str(e)[:200])

            pool2 = await get_pool()
            async with pool2.acquire() as conn2:
                await conn2.execute(
                    "UPDATE admin_alerts SET push_sent=$1, push_error=$2 WHERE id=$3",
                    sent > 0, ("; ".join(errors) or None) if errors else None, alert_id,
                )
            logger.info("admin_alert[%s] dispatched sent=%d errors=%d", kind, sent, len(errors))
        except Exception:
            logger.exception("admin_alert[%s] dispatch crashed", kind)

    asyncio.create_task(_work())
    return {"scheduled": True, "kind": kind, "alert_key": key}


# Thresholds (tunable via admin_settings later if needed)
LARGE_WITHDRAW_USD_THRESHOLD = 500.0
SEND_SPAM_COUNT = 10       # per window
SEND_SPAM_WINDOW_MINUTES = 5


async def trigger_large_withdraw_alert(user_id: str, amount_usd: float, method: str) -> None:
    if amount_usd < LARGE_WITHDRAW_USD_THRESHOLD:
        return
    await raise_alert(
        kind="wallet.large_withdrawal",
        title=f"⚠️ Retrait > {LARGE_WITHDRAW_USD_THRESHOLD:.0f} USD",
        body=f"User {user_id} demande {amount_usd:.2f} USD via {method}",
        url="/admin",
        alert_key=f"large_withdraw:{user_id}:{int(amount_usd)}",
        window_minutes=15,
    )


async def trigger_withdraw_without_kyc(user_id: str, amount_usd: float) -> None:
    await raise_alert(
        kind="wallet.withdraw_without_kyc",
        title="🚨 Tentative retrait sans KYC",
        body=f"User {user_id} a tenté un retrait de {amount_usd:.2f} USD sans KYC validé",
        url="/admin",
        alert_key=f"withdraw_no_kyc:{user_id}",
        window_minutes=60,
    )


async def trigger_send_spam(user_id: str, count_last_5min: int) -> None:
    await raise_alert(
        kind="wallet.send_spam",
        title="🚨 Spam send money détecté",
        body=f"User {user_id} : {count_last_5min} sends en 5 min",
        url="/admin",
        alert_key=f"send_spam:{user_id}",
        window_minutes=30,
    )
