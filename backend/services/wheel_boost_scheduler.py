"""
JAPAP — Wheel Boost Scheduler (iter115)
========================================
Periodic in-process worker that auto-enables / disables the
Wheel Boost Event based on admin-defined recurring schedules.

Schedule kinds:
  • RECURRING: weekly window (e.g. every Friday 18h → Sunday 23h)
  • DATED:     one-shot date range (e.g. 2026-05-01 00h → 2026-05-01 23h59
               for "Fête du Travail")

The worker runs every POLL_INTERVAL_SEC seconds. On each tick:
  1. Find the schedule that should be ACTIVE right now (most-specific
     wins: a DATED schedule overrides RECURRING). DISABLED schedules
     are ignored.
  2. If a schedule is active and the current global boost is OFF (or owned
     by a different schedule), enable the boost with the schedule's
     parameters and mint a fresh boost_id.
  3. If no schedule is active and the current boost was minted by the
     scheduler (`wheel_boost_owner=schedule:<id>`), disable the boost.

Manual admin-toggled boosts are NEVER overridden — the scheduler only
takes ownership of boosts it created itself (tracked via the
`wheel_boost_owner` admin_setting).
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timezone, time as dtime

logger = logging.getLogger(__name__)

WORKER_ID = f"wbs_{uuid.uuid4().hex[:8]}"
POLL_INTERVAL_SEC = 60

_task: asyncio.Task | None = None
_stop_flag = asyncio.Event()


_DDL = [
    """
    CREATE TABLE IF NOT EXISTS wheel_boost_schedules (
        id BIGSERIAL PRIMARY KEY,
        name VARCHAR(80) NOT NULL,
        kind VARCHAR(16) NOT NULL DEFAULT 'recurring',  -- 'recurring' or 'dated'
        -- RECURRING : day_of_week 0=Mon ... 6=Sun, time HH:MM:SS UTC
        dow_start INT,
        time_start TIME,
        dow_end INT,
        time_end TIME,
        -- DATED : ISO timestamp window (UTC)
        date_start TIMESTAMPTZ,
        date_end TIMESTAMPTZ,
        -- Boost parameters
        gain_multiplier REAL NOT NULL DEFAULT 1.5,
        perdu_reduction_percent INT NOT NULL DEFAULT 50,
        unlock_jackpot_all_phases BOOLEAN NOT NULL DEFAULT FALSE,
        jackpot_odds_during_boost INT NOT NULL DEFAULT 25,
        -- Lifecycle
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        last_triggered_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        CONSTRAINT chk_kind CHECK (kind IN ('recurring','dated')),
        CONSTRAINT chk_perdu CHECK (perdu_reduction_percent BETWEEN 0 AND 95),
        CONSTRAINT chk_mult  CHECK (gain_multiplier BETWEEN 1.0 AND 5.0),
        CONSTRAINT chk_odds  CHECK (jackpot_odds_during_boost BETWEEN 0 AND 100)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wbs_enabled ON wheel_boost_schedules (enabled)",
    "CREATE INDEX IF NOT EXISTS idx_wbs_dated ON wheel_boost_schedules (date_start, date_end) WHERE kind='dated'",
]


async def ensure_schedules_ddl() -> None:
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        for stmt in _DDL:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"wheel_boost_schedules DDL failed: {e} — {stmt[:60]}")


def _is_recurring_active(now: datetime, dow_start: int, time_start: dtime,
                         dow_end: int, time_end: dtime) -> bool:
    """Return True if `now` is within the [dow_start time_start, dow_end time_end]
    weekly window (inclusive). Works for windows that cross the week boundary
    (e.g. Friday 18h → Sunday 23h)."""
    if dow_start is None or time_start is None or dow_end is None or time_end is None:
        return False
    # Compute "minutes-of-week" 0..10079
    def mow(dow: int, t: dtime) -> int:
        return dow * 24 * 60 + t.hour * 60 + t.minute
    cur = mow(now.weekday(), now.time().replace(second=0, microsecond=0))
    s = mow(dow_start, time_start)
    e = mow(dow_end, time_end)
    if s <= e:
        return s <= cur <= e
    # Wraps around the week boundary (e.g. Sat 22h → Mon 06h)
    return cur >= s or cur <= e


async def _find_active_schedule(conn, now: datetime) -> dict | None:
    """Pick the most specific schedule that should be active at `now`.
    DATED beats RECURRING. Lower id wins ties. Returns None if no match."""
    # 1) DATED schedules covering now
    dated = await conn.fetchrow(
        """SELECT * FROM wheel_boost_schedules
            WHERE enabled=TRUE AND kind='dated'
              AND date_start <= $1 AND date_end >= $1
            ORDER BY id ASC LIMIT 1""",
        now,
    )
    if dated:
        return dict(dated)

    # 2) RECURRING — fetch enabled rows then evaluate in Python (TIME math is
    #    simpler that way and the table stays tiny).
    rows = await conn.fetch(
        """SELECT * FROM wheel_boost_schedules
            WHERE enabled=TRUE AND kind='recurring'
            ORDER BY id ASC""",
    )
    for r in rows:
        if _is_recurring_active(now, r["dow_start"], r["time_start"],
                                r["dow_end"], r["time_end"]):
            return dict(r)
    return None


async def _enable_boost_from_schedule(conn, sched: dict) -> None:
    """Activate the global Wheel Boost using the schedule's parameters and
    mark this scheduler as the owner so we can deactivate later without
    stomping on a manual admin boost."""
    from services.settings_service import set_setting

    boost_id = f"sched_{sched['id']}_{uuid.uuid4().hex[:6]}"
    sched_name = sched["name"] or f"Schedule #{sched['id']}"
    await set_setting("wheel_boost_enabled", True)
    await set_setting("wheel_boost_name", sched_name)
    await set_setting("wheel_boost_id", boost_id)
    await set_setting("wheel_boost_owner", f"schedule:{sched['id']}")
    await set_setting("wheel_boost_starts_at", "")  # leave open-ended; window enforced by scheduler
    await set_setting("wheel_boost_ends_at", "")
    await set_setting("wheel_boost_gain_multiplier", float(sched["gain_multiplier"]))
    await set_setting("wheel_boost_perdu_reduction_percent",
                      int(sched["perdu_reduction_percent"]))
    await set_setting("wheel_boost_unlock_jackpot_all_phases",
                      bool(sched["unlock_jackpot_all_phases"]))
    await set_setting("wheel_boost_jackpot_odds",
                      int(sched["jackpot_odds_during_boost"]))
    await conn.execute(
        "UPDATE wheel_boost_schedules SET last_triggered_at=NOW(), updated_at=NOW() WHERE id=$1",
        sched["id"],
    )
    logger.warning(f"[WheelBoostScheduler] ENABLED boost from schedule {sched['id']} "
                   f"({sched_name}) → boost_id={boost_id}")


async def _disable_boost_if_owner() -> bool:
    """If the current boost was minted by the scheduler, disable it.
    Returns True when a disable actually happened."""
    from services.settings_service import get_setting, set_setting, get_bool
    if not await get_bool("wheel_boost_enabled", False):
        return False
    owner = (await get_setting("wheel_boost_owner") or "").strip()
    if not owner.startswith("schedule:"):
        # Manual admin boost — never touch.
        return False
    await set_setting("wheel_boost_enabled", False)
    await set_setting("wheel_boost_owner", "")
    logger.warning(f"[WheelBoostScheduler] DISABLED boost (owner was {owner}, window expired).")
    return True


async def _tick() -> None:
    from database import get_pool
    from services.settings_service import get_setting, get_bool
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        active_sched = await _find_active_schedule(conn, now)
        boost_on = await get_bool("wheel_boost_enabled", False)
        owner = (await get_setting("wheel_boost_owner") or "").strip()
        boost_id = (await get_setting("wheel_boost_id") or "").strip()

        if active_sched:
            # If a manual admin boost is currently running, leave it alone.
            # The admin took explicit action; we never override that.
            if boost_on and owner == "manual":
                return
            # If boost is OFF, or it's owned by a *different* schedule, take over.
            owns_current = owner == f"schedule:{active_sched['id']}"
            if not boost_on or not owns_current:
                await _enable_boost_from_schedule(conn, active_sched)
                # mint boost_id was inside enable; nothing more here
            else:
                # Already running under this schedule — refresh last_triggered_at
                # only if not done in last 5 minutes (cheap heartbeat).
                pass
        else:
            # No schedule should be active — if scheduler owns the current boost, kill it.
            if boost_on and owner.startswith("schedule:"):
                await _disable_boost_if_owner()


async def _loop() -> None:
    logger.info(f"[WheelBoostScheduler {WORKER_ID}] loop started "
                f"(poll {POLL_INTERVAL_SEC}s)")
    try:
        await ensure_schedules_ddl()
    except Exception as e:
        logger.warning(f"[WheelBoostScheduler] DDL ensure failed: {e}")
    while not _stop_flag.is_set():
        try:
            await _tick()
        except Exception as e:
            logger.exception(f"[WheelBoostScheduler] tick failed: {e}")
        try:
            await asyncio.wait_for(_stop_flag.wait(), timeout=POLL_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass
    logger.info(f"[WheelBoostScheduler {WORKER_ID}] loop stopped")


def start_scheduler(app) -> None:
    """Wire the scheduler into the FastAPI lifecycle (called from server.py)."""
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


__all__ = [
    "ensure_schedules_ddl",
    "start_scheduler",
    "_is_recurring_active",
    "_find_active_schedule",
]
