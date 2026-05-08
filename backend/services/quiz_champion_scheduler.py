"""
Quiz Champion Scheduler (iter128, Phase 3.D).
=============================================

Background worker — supervisor-managed — that handles the two recurring
duties for the Quiz Champion subsystem:

  1. EXPIRY TICK — every 5 min : refund all stale challenges
     (status in (pending, accepted, …) AND expires_at < NOW()).
     Reuses `_lazy_expire` from routes/quiz_champion.py for atomicity.

  2. PROMOTION TICK — daily (cron at 03:00 UTC) : recompute the top-1
     player by quiz points over the last `quiz_champion_window_days`
     (default 7) per country and promote/keep/demote champions.
     Idempotent — calls `promote_champions` from services/quiz_champion.

Both ticks are wrapped in try/except so a transient failure never kills
the loop. State is logged to /var/log/supervisor/quiz_champion_scheduler.*
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

EXPIRY_POLL_SECONDS = int(os.environ.get("QUIZ_CHAMPION_EXPIRY_POLL_SECONDS", "300"))   # 5 min
PROMOTE_HOUR_UTC = int(os.environ.get("QUIZ_CHAMPION_PROMOTE_HOUR_UTC", "3"))
LOOP_TICK_SECONDS = 60   # outer loop tick — keeps the daily promote check responsive

# iter223 — Pool de questions IA (réutilise quiz_ai_generator + quiz_questions).
POOL_REFRESH_HOURS       = int(os.environ.get("QUIZ_POOL_REFRESH_HOURS",       "48"))
POOL_BATCH_SIZE          = int(os.environ.get("QUIZ_POOL_BATCH_SIZE",          "100"))
POOL_HEALTH_MIN          = int(os.environ.get("QUIZ_POOL_HEALTH_MIN",          "30"))
POOL_HEALTH_POLL_SECONDS = int(os.environ.get("QUIZ_POOL_HEALTH_POLL_SECONDS", "600"))


_last_promote_day: str | None = None  # 'YYYY-MM-DD' (UTC) of last successful promote
_last_pool_refresh_ts: float = 0.0    # iter223 — monotonic ts of last successful pool refresh
_last_pool_health_ts: float = 0.0     # iter223 — monotonic ts of last health probe
_emergency_in_flight: bool = False    # iter223 — single-flight guard for emergency batches
_pool_refresh_in_flight: bool = False # iter230 — single-flight guard for the 48 h refresh
_bg_tasks: set[asyncio.Task] = set()  # iter230 — keep refs to fire-and-forget tasks
                                       # so the event loop doesn't GC them mid-flight.


async def _expire_tick() -> None:
    """Run one expiration sweep — refund stale paid stakes, mark status=expired,
    award challenger bonus. Atomic per challenge."""
    try:
        from database import get_pool
        from routes.quiz_champion import _lazy_expire, ensure_ddl
        pool = await get_pool()
        async with pool.acquire() as conn:
            await ensure_ddl(conn)
            rows = await conn.fetch(
                """SELECT challenge_id FROM quiz_champion_challenges
                    WHERE status IN ('pending','accepted','challenger_played','champion_played')
                      AND expires_at IS NOT NULL AND expires_at < NOW()
                 ORDER BY expires_at LIMIT 100""",
            )
            if not rows:
                return
            count = 0
            for r in rows:
                cid = r["challenge_id"]
                try:
                    async with conn.transaction():
                        ch = await conn.fetchrow(
                            "SELECT * FROM quiz_champion_challenges WHERE challenge_id = $1 FOR UPDATE",
                            cid,
                        )
                        if ch:
                            await _lazy_expire(conn, ch)
                            count += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("expire_tick: challenge %s failed: %s", cid, e)
            if count:
                logger.info("[quiz-champion-expiry] expired %d challenge(s)", count)
    except Exception as e:  # noqa: BLE001
        logger.warning("expire_tick error: %s", e)


async def _promote_tick() -> None:
    """Run the auto top-1 promotion across all countries (window = configured)."""
    global _last_promote_day
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hour_now = datetime.now(timezone.utc).hour
    if hour_now < PROMOTE_HOUR_UTC:
        return
    if _last_promote_day == today:
        return
    try:
        from services.quiz_champion import promote_champions
        from services.games_settings import get_quiz_config
        cfg = await get_quiz_config()
        # Use admin-tunable window if present; fallback to 7 (default rule).
        window_days = int(cfg.get("quiz_champion_window_days") or 7)
        out = await promote_champions(window_days=window_days)
        promoted = len(out.get("promoted") or [])
        unchanged = len(out.get("unchanged") or [])
        logger.info("[quiz-champion-promote] window=%dd promoted=%d unchanged=%d",
                    window_days, promoted, unchanged)
        _last_promote_day = today
    except Exception as e:  # noqa: BLE001
        logger.warning("promote_tick error: %s", e)


async def _run_pool_refresh_bg() -> None:
    """iter230 — Heavy Claude generation done OFF the main loop tick.
    Single-flight guarded; never awaited from the scheduler iteration.
    """
    global _last_pool_refresh_ts, _pool_refresh_in_flight
    if _pool_refresh_in_flight:
        logger.info("[quiz-pool-refresh] skipped — another refresh is in flight")
        return
    _pool_refresh_in_flight = True
    started = asyncio.get_event_loop().time()
    try:
        from database import get_pool
        from services.quiz_ai_generator import generate_with_distribution
        pool = await get_pool()
        async with pool.acquire() as conn:
            summary = await generate_with_distribution(conn, total=POOL_BATCH_SIZE)
        elapsed = asyncio.get_event_loop().time() - started
        logger.info(
            "[quiz-pool-refresh] inserted=%d skipped=%d elapsed=%.1fs by_category=%s",
            summary.get("inserted", 0), summary.get("skipped", 0),
            elapsed, summary.get("by_category", {}),
        )
        _last_pool_refresh_ts = asyncio.get_event_loop().time()
    except asyncio.CancelledError:
        logger.info("[quiz-pool-refresh] cancelled (shutdown)")
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("pool_refresh background error: %s", e)
    finally:
        _pool_refresh_in_flight = False


def _spawn_bg(coro) -> asyncio.Task:
    """Schedule a fire-and-forget task that survives the scheduler tick.
    The task ref is kept in `_bg_tasks` to prevent GC mid-await.
    Note: `asyncio.shield` is used internally inside the coro itself if needed;
    here we just need a strong ref + completion callback to drop it."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def _pool_refresh_tick() -> None:
    """iter223 — Generate a fresh batch of AI questions every POOL_REFRESH_HOURS.

    iter230 — The actual Claude API calls (~30-60s blocking) are now offloaded
    to a fire-and-forget background task so the scheduler iteration returns
    immediately and the API event loop is never frozen.
    """
    now = asyncio.get_event_loop().time()
    if now - _last_pool_refresh_ts < POOL_REFRESH_HOURS * 3600:
        return
    if _pool_refresh_in_flight:
        return
    logger.info("[quiz-pool-refresh] dispatching background refresh")
    _spawn_bg(_run_pool_refresh_bg())


async def _run_pool_emergency_bg(initial_count: int) -> None:
    """iter230 — Emergency Claude batch when pool drops below MIN.
    Always done off-loop to avoid freezing API workers.
    """
    global _emergency_in_flight, _last_pool_refresh_ts
    if _emergency_in_flight:
        return
    _emergency_in_flight = True
    started = asyncio.get_event_loop().time()
    try:
        from database import get_pool
        from services.quiz_ai_generator import generate_with_distribution
        pool = await get_pool()
        async with pool.acquire() as conn:
            summary = await generate_with_distribution(conn, total=POOL_BATCH_SIZE)
        elapsed = asyncio.get_event_loop().time() - started
        logger.info(
            "[quiz-pool-health] emergency batch inserted=%d initial=%d elapsed=%.1fs",
            summary.get("inserted", 0), initial_count, elapsed,
        )
        # Reset the 48 h timer so we don't double-generate immediately.
        _last_pool_refresh_ts = asyncio.get_event_loop().time()
    except asyncio.CancelledError:
        logger.info("[quiz-pool-health] emergency cancelled (shutdown)")
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("pool_health emergency error: %s", e)
    finally:
        _emergency_in_flight = False


async def _pool_health_tick() -> None:
    """iter223 — Probe the active question pool every POOL_HEALTH_POLL_SECONDS.

    iter230 — If the pool is below MIN, dispatch a fire-and-forget batch
    instead of awaiting the (slow) Claude generation here. The probe itself
    is a single SELECT — non-blocking.
    """
    global _last_pool_health_ts
    now = asyncio.get_event_loop().time()
    if now - _last_pool_health_ts < POOL_HEALTH_POLL_SECONDS:
        return
    _last_pool_health_ts = now
    if _emergency_in_flight:
        return
    try:
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM quiz_questions "
                "WHERE active = TRUE AND obsolete = FALSE",
            )
        if count is None or count >= POOL_HEALTH_MIN:
            return
        logger.warning(
            "[quiz-pool-health] active=%d below min=%d — dispatching emergency batch",
            count, POOL_HEALTH_MIN,
        )
        _spawn_bg(_run_pool_emergency_bg(int(count)))
    except Exception as e:  # noqa: BLE001
        logger.warning("pool_health_tick error: %s", e)


async def loop() -> None:
    """Outer loop — runs forever. Calls expiry tick every EXPIRY_POLL_SECONDS
    and the promote tick once per day after PROMOTE_HOUR_UTC."""
    import uuid
    loop_id = f"qcs_{uuid.uuid4().hex[:8]}"
    logger.info("[QuizChampionScheduler %s] loop started "
                "(expiry every %ds, promote at %02d:00 UTC daily)",
                loop_id, EXPIRY_POLL_SECONDS, PROMOTE_HOUR_UTC)
    last_expiry = 0.0
    while True:
        try:
            now = asyncio.get_event_loop().time()
            if now - last_expiry >= EXPIRY_POLL_SECONDS:
                await _expire_tick()
                last_expiry = now
            await _promote_tick()
            # iter223 — Pool refresh (48 h) + health probe (every 10 min).
            await _pool_refresh_tick()
            await _pool_health_tick()
            # iter237k — DCQ paid pool refresh + health (additif).
            try:
                from services.dcq_paid_pool_worker import refresh_tick as _dcq_refresh, health_tick as _dcq_health
                await _dcq_refresh()
                await _dcq_health()
            except Exception as _e:  # noqa: BLE001
                logger.warning("[dcq-pool] tick error: %s", _e)
        except asyncio.CancelledError:
            logger.info("[QuizChampionScheduler %s] cancelled", loop_id)
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("[QuizChampionScheduler %s] loop error: %s", loop_id, e)
        await asyncio.sleep(LOOP_TICK_SECONDS)


def start_in_background() -> None:
    """Entry point used at server startup (server.py lifespan handler)."""
    try:
        asyncio.create_task(loop())
        logger.info("[QuizChampionScheduler] background task scheduled")
    except RuntimeError:
        # No running event loop yet (e.g., import-time call) — caller should
        # invoke this from inside an async context.
        logger.warning("[QuizChampionScheduler] no event loop — call from startup")


_task: asyncio.Task | None = None
_stop_flag = asyncio.Event()


def start_scheduler(app) -> None:
    """Wire the scheduler into the FastAPI lifecycle (called from server.py)."""
    @app.on_event("startup")
    async def _start():
        global _task
        if _task is None or _task.done():
            _task = asyncio.create_task(loop())

    @app.on_event("shutdown")
    async def _stop():
        _stop_flag.set()
        # iter230 — cancel any in-flight Claude generation tasks so the
        # process doesn't hang on shutdown waiting for the API to return.
        for t in list(_bg_tasks):
            try:
                t.cancel()
            except Exception:
                pass
        if _task:
            try:
                _task.cancel()
                await asyncio.wait_for(_task, timeout=5)
            except Exception:
                pass


__all__ = ["start_scheduler", "loop", "_expire_tick", "_promote_tick",
           "_pool_refresh_tick", "_pool_health_tick"]
