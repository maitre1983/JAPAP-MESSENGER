"""
Messaging worker — asyncio in-process loop that drains email_send_queue.

Design notes :
  - Single worker task started at FastAPI startup (start_worker).
  - Polls every `poll_interval` seconds, pops up to `batch_size` rows using
    SELECT ... FOR UPDATE SKIP LOCKED (prevents double-send on restart).
  - Throttled by `messaging_worker_rate_per_minute` admin setting (default 50).
  - Uses Resend via the existing services.email_service helper.
  - Writes `email_logs(event='sent')` or `event='failed'` per attempt.
  - Retries up to `MAX_ATTEMPTS` then marks the row 'failed' permanently.
  - On success, updates the parent campaign's denormalized counters.
"""
from __future__ import annotations
import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WORKER_ID = f"w_{uuid.uuid4().hex[:10]}"
MAX_ATTEMPTS = 3
POLL_INTERVAL_SEC = 3
BATCH_SIZE = 10

_task: asyncio.Task | None = None
_stop_flag = asyncio.Event()


async def _send_one(conn, row) -> bool:
    """Actually dispatch one queue row. Returns True on success.

    Honors the `messaging_real_send_enabled` admin setting — when disabled,
    emails are logged as [SAFE-MODE] and marked as sent without hitting the
    real SMTP/API provider. This is an emergency kill-switch to prevent
    accidental delivery to real users during testing or incidents.
    """
    from services.email_service import send_email
    from services.settings_service import get_bool
    safe_mode = not await get_bool("messaging_real_send_enabled", True)
    if safe_mode:
        logger.info(
            "[SAFE-MODE] Skipping real delivery for campaign=%s to=%s subject=%s",
            row.get("campaign_id"), row["recipient_email"], row["rendered_subject"][:80],
        )
        return True
    try:
        ok = await send_email(
            to=row["recipient_email"],
            subject=row["rendered_subject"],
            html=row["rendered_html"],
            text=row["rendered_text"] or "",
        )
    except Exception as e:
        logger.warning(f"worker send_email raised: {e}")
        ok = False
    return bool(ok)


async def _drain_batch(pool):
    from services.settings_service import get_int
    rate = max(1, await get_int("messaging_worker_rate_per_minute", 60))
    batch_size = max(1, await get_int("messaging_batch_size", BATCH_SIZE))
    # Compute how many we may send in this poll window
    budget = max(1, rate * POLL_INTERVAL_SEC // 60)
    budget = min(budget, batch_size)

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT * FROM email_send_queue
                WHERE status = 'pending'
                  AND (locked_at IS NULL OR locked_at < NOW() - INTERVAL '120 seconds')
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                budget,
            )
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            await conn.execute(
                "UPDATE email_send_queue SET locked_by = $1, locked_at = NOW() "
                "WHERE id = ANY($2::int[])",
                WORKER_ID, ids,
            )

    # Dispatch each row outside the locking transaction so HTTP to Resend
    # doesn't hold a DB lock.
    for row in rows:
        ok = await _send_one(None, row)
        async with pool.acquire() as conn2:
            async with conn2.transaction():
                if ok:
                    await conn2.execute(
                        """UPDATE email_send_queue
                           SET status = 'sent', sent_at = NOW(), error_msg = NULL,
                               attempt_count = attempt_count + 1, locked_by = NULL
                           WHERE id = $1""",
                        row["id"],
                    )
                    if row["recipient_user_id"]:
                        await conn2.execute(
                            "UPDATE users SET last_email_sent_at = NOW() WHERE user_id = $1",
                            row["recipient_user_id"],
                        )
                    if row["campaign_id"]:
                        await conn2.execute(
                            "UPDATE email_campaigns SET sent_count = sent_count + 1 "
                            "WHERE campaign_id = $1",
                            row["campaign_id"],
                        )
                    await conn2.execute(
                        """INSERT INTO email_logs
                           (log_id, campaign_id, user_id, email, event, created_at)
                           VALUES ($1,$2,$3,$4,'sent', NOW())""",
                        f"log_{uuid.uuid4().hex[:14]}",
                        row["campaign_id"], row["recipient_user_id"], row["recipient_email"],
                    )
                else:
                    attempts = (row["attempt_count"] or 0) + 1
                    if attempts >= MAX_ATTEMPTS:
                        await conn2.execute(
                            """UPDATE email_send_queue
                               SET status = 'failed', attempt_count = $1,
                                   error_msg = 'max attempts reached', locked_by = NULL,
                                   locked_at = NULL
                               WHERE id = $2""",
                            attempts, row["id"],
                        )
                        await conn2.execute(
                            """INSERT INTO email_logs
                               (log_id, campaign_id, user_id, email, event, created_at)
                               VALUES ($1,$2,$3,$4,'failed', NOW())""",
                            f"log_{uuid.uuid4().hex[:14]}",
                            row["campaign_id"], row["recipient_user_id"], row["recipient_email"],
                        )
                    else:
                        # Release lock; next poll picks it up
                        await conn2.execute(
                            """UPDATE email_send_queue
                               SET attempt_count = $1, locked_by = NULL, locked_at = NULL
                               WHERE id = $2""",
                            attempts, row["id"],
                        )

    # Check if any campaign just finished
    async with pool.acquire() as conn3:
        await conn3.execute(
            """UPDATE email_campaigns SET status = 'sent', completed_at = NOW()
               WHERE status = 'sending'
                 AND NOT EXISTS (SELECT 1 FROM email_send_queue
                                  WHERE campaign_id = email_campaigns.campaign_id
                                    AND status = 'pending')"""
        )
    return len(rows)


async def _loop(pool):
    logger.info(f"Messaging worker loop started (id={WORKER_ID}).")
    last_wheel_daily = None
    last_pricing_weekly_iso = None
    while not _stop_flag.is_set():
        try:
            n = await _drain_batch(pool)

            # iter83 — daily cycle-reminder job (runs once per UTC day)
            from datetime import datetime as _dt, timezone as _tz
            now = _dt.now(_tz.utc)
            today = now.date()
            # Fire at ~09:00 UTC (morning in West Africa / afternoon in Asia)
            if now.hour >= 9 and last_wheel_daily != today:
                try:
                    from routes.wheel_fortune import run_cycle_notifications_job
                    result = await run_cycle_notifications_job()
                    logger.info(f"[wheel-daily] cycle reminders sent: {result}")
                    last_wheel_daily = today
                except Exception as _e:
                    logger.warning(f"[wheel-daily] failed: {_e}")

            # iter103 — weekly pricing AI proposal batch.
            # Fires Mondays UTC ≥ 02:00 once per ISO week. Tuesday
            # catch-up allowed if the worker was down Monday morning —
            # the iso-week dedup tracker still guarantees single-run.
            if (now.weekday() == 0 and now.hour >= 2) or now.weekday() == 1:
                iso_year, iso_week, _ = now.isocalendar()
                wk_key = f"{iso_year}-W{iso_week:02d}"
                if last_pricing_weekly_iso != wk_key:
                    try:
                        from services.transport_pricing_cron import run_weekly_ai_proposal_batch
                        result = await run_weekly_ai_proposal_batch()
                        logger.info(f"[pricing-weekly] {result}")
                        last_pricing_weekly_iso = wk_key
                    except Exception as _e:
                        logger.warning(f"[pricing-weekly] failed: {_e}")

            if n == 0:
                try:
                    await asyncio.wait_for(_stop_flag.wait(), timeout=POLL_INTERVAL_SEC)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(POLL_INTERVAL_SEC)
        except Exception as e:
            logger.exception(f"Messaging worker loop error: {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
    logger.info("Messaging worker loop stopped.")


def start_worker(app):
    """Wire @app.on_event("startup") / shutdown to run/stop the worker."""

    @app.on_event("startup")
    async def _startup():
        global _task
        from database import get_pool
        from services.messaging_seed import seed_system_templates_and_campaign
        from services.segment_compiler import SYSTEM_SEGMENTS, count_recipients
        import json as _json
        pool = await get_pool()
        # Seed system segments (idempotent) so the migration campaign can FK them
        async with pool.acquire() as _c:
            existing = set(r["segment_id"] for r in await _c.fetch(
                "SELECT segment_id FROM email_segments WHERE is_system = TRUE"))
            for seg_id, name, desc, rules in SYSTEM_SEGMENTS:
                if seg_id in existing:
                    continue
                try:
                    est = await count_recipients(_c, rules)
                except Exception:
                    est = 0
                await _c.execute(
                    """INSERT INTO email_segments
                         (segment_id, name, description, rules, is_system,
                          estimated_count, estimated_at)
                       VALUES ($1,$2,$3,$4,TRUE,$5,NOW())
                       ON CONFLICT (segment_id) DO NOTHING""",
                    seg_id, name, desc, _json.dumps(rules), est,
                )
        await seed_system_templates_and_campaign()
        _task = asyncio.create_task(_loop(pool))

    @app.on_event("shutdown")
    async def _shutdown():
        _stop_flag.set()
        if _task:
            try:
                await asyncio.wait_for(_task, timeout=5)
            except Exception:
                pass


async def drain_now_for_tests(pool):
    """Test helper — drain all currently pending rows synchronously."""
    total = 0
    while True:
        n = await _drain_batch(pool)
        if n == 0:
            break
        total += n
    return total
