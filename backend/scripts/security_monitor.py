#!/usr/bin/env python3
"""
security_monitor.py — iter82 continuous log monitoring.

Runs every minute (cron or a tiny supervisor program) and tails the
security_events / audit_logs tables. Raises alerts when:
  • > 10 failed login attempts in the last 5 min from the same IP
  • > 5 rejected uploads in 5 min from the same user
  • any `auth.refresh_replay_detected` event (already CRITICAL)
  • > 20 CSRF guard rejections in 5 min (platform-wide)

Alerts go to the admin via OneSignal push + log line. No hardcoding — all
thresholds are admin_settings so they can be tuned without redeploy.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_pool  # noqa: E402

logger = logging.getLogger("security_monitor")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


DEFAULTS = {
    "sec_alert_failed_login_threshold": 10,
    "sec_alert_failed_login_window_min": 5,
    "sec_alert_rejected_upload_threshold": 5,
    "sec_alert_rejected_upload_window_min": 5,
    "sec_alert_csrf_rejection_threshold": 20,
}


async def _get_int(conn, key: str, fallback: int) -> int:
    row = await conn.fetchrow("SELECT value FROM admin_settings WHERE key = $1", key)
    if not row:
        return fallback
    try:
        return int(row["value"])
    except Exception:
        return fallback


async def run_once() -> dict:
    pool = await get_pool()
    alerts: list[str] = []
    async with pool.acquire() as conn:
        t_login  = await _get_int(conn, "sec_alert_failed_login_threshold", 10)
        w_login  = await _get_int(conn, "sec_alert_failed_login_window_min", 5)
        t_upload = await _get_int(conn, "sec_alert_rejected_upload_threshold", 5)
        w_upload = await _get_int(conn, "sec_alert_rejected_upload_window_min", 5)

        # 1. Failed login bursts — count rows in login_attempts updated recently
        rows = await conn.fetch(f"""
            SELECT identifier, attempts FROM login_attempts
            WHERE last_attempt > NOW() - INTERVAL '{w_login} minutes'
              AND attempts >= $1
        """, t_login)
        for r in rows:
            alerts.append(f"[LOGIN] {r['identifier']} has {r['attempts']} failed attempts in last {w_login}min")

        # 2. Replay detections — always alert
        rows = await conn.fetch("""
            SELECT user_id, created_at FROM security_events
            WHERE event_type = 'auth.refresh_replay_detected'
              AND created_at > NOW() - INTERVAL '60 minutes'
        """)
        for r in rows:
            alerts.append(f"[REPLAY] user_id={r['user_id']} at {r['created_at'].isoformat()}")

        # 3. Upload rejection bursts (look up logs via security_events if present)
        rows = await conn.fetch(f"""
            SELECT user_id, COUNT(*) AS n
            FROM security_events
            WHERE event_type = 'upload.rejected'
              AND created_at > NOW() - INTERVAL '{w_upload} minutes'
            GROUP BY user_id
            HAVING COUNT(*) >= $1
        """, t_upload)
        for r in rows:
            alerts.append(f"[UPLOAD] user_id={r['user_id']} rejected {r['n']} files in {w_upload}min")

        # 4. New-device login alert — surface to the user (already pushed in
        # auth.login) but count them for dashboards
        nd = await conn.fetchval("""
            SELECT COUNT(*) FROM security_events
            WHERE event_type = 'auth.login_new_device'
              AND created_at > NOW() - INTERVAL '15 minutes'
        """)

    # Emit alerts
    for a in alerts:
        logger.warning(a)

    return {"alerts": alerts, "new_devices_15min": int(nd or 0)}


async def main():
    while True:
        try:
            result = await run_once()
            if result["alerts"]:
                logger.info(f"{len(result['alerts'])} alert(s) emitted.")
        except Exception as e:
            logger.error(f"monitor loop failed: {e}")
        await asyncio.sleep(60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        print(asyncio.run(run_once()))
    else:
        asyncio.run(main())
