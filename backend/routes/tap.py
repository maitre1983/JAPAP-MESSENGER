"""
iter83 — Tap Challenge backend (Phase 3).

Product rules (ALL server-enforced) :
  - 10 seconds per run, server-clocked start/end
  - Max 1 run/day (anti-exploit, feeds Starter Pro cycle)
  - Anti-cheat : hard ceiling of 12 taps/second (human DPM max)
  - Points = validated_taps * 1 + milestone bonuses
      • 30 taps → +10 bonus
      • 50 taps → +25 bonus
      • 80 taps → +50 bonus (capped)
  - All points funnel into the unified `points_service` (no XAF).
  - Points clamped by the sovereign 10 000 / 25 days rule.

Endpoints (all /api/tap) :
  - POST /start   → opens a run, returns run_id + duration + start_at
  - POST /submit  → submit taps count, backend validates + adds points
  - GET  /status  → today's remaining runs, best score ever
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user
from services.points_service import (
    ensure_game_enabled, add_points, register_tap_run,
)
from services.games_settings import get_tap_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tap", tags=["tap"])

DURATION_SECONDS = 10
NETWORK_GRACE_SECONDS = 4
MAX_RUNS_PER_DAY = 1
TAPS_PER_SEC_CAP = 12  # anti-cheat ceiling — overridable via admin_settings
SUSPICIOUS_TAPS_THRESHOLD = 100   # ≥10 tps for 10s is top of human range
BONUS_TIERS = [(80, 50), (50, 25), (30, 10)]   # legacy fallback (threshold, bonus)


async def _tap_runtime_config() -> dict:
    """Single source of truth for runtime Tap config (defaults+bounds enforced).
    Imports hoisted at module level (iter108 perf cleanup)."""
    return await get_tap_config()

# ══════════════════════════════════════════════════════════════════════════
#  DDL
# ══════════════════════════════════════════════════════════════════════════

_DDL = [
    """CREATE TABLE IF NOT EXISTS tap_user_runs (
         id BIGSERIAL PRIMARY KEY,
         user_id       VARCHAR(32) NOT NULL,
         started_at    TIMESTAMPTZ NOT NULL,
         submitted_at  TIMESTAMPTZ,
         taps_raw      INT,
         taps_valid    INT,
         points_awarded INT,
         timed_out     BOOLEAN DEFAULT FALSE,
         cheated       BOOLEAN DEFAULT FALSE,
         suspicious    BOOLEAN DEFAULT FALSE,
         ip_address    VARCHAR(64) DEFAULT '',
         user_agent    VARCHAR(512) DEFAULT ''
       )""",
    "ALTER TABLE tap_user_runs ADD COLUMN IF NOT EXISTS suspicious BOOLEAN DEFAULT FALSE",
    "ALTER TABLE tap_user_runs ADD COLUMN IF NOT EXISTS ip_address VARCHAR(64) DEFAULT ''",
    "ALTER TABLE tap_user_runs ADD COLUMN IF NOT EXISTS user_agent VARCHAR(512) DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_tap_runs_user ON tap_user_runs(user_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tap_runs_suspicious ON tap_user_runs(suspicious) WHERE suspicious",
]

_ddl_done = False


async def _ensure_ddl(conn) -> None:
    global _ddl_done
    if _ddl_done:
        return
    for s in _DDL:
        await conn.execute(s)
    _ddl_done = True


# ══════════════════════════════════════════════════════════════════════════
#  Schemas
# ══════════════════════════════════════════════════════════════════════════

class StartResponse(BaseModel):
    run_id: int
    duration_seconds: int
    start_at: str           # ISO8601, server-side ground truth
    remaining_today: int


class SubmitRequest(BaseModel):
    run_id: int
    taps: int = Field(..., ge=0, le=10_000)


class SubmitResponse(BaseModel):
    run_id: int
    taps_raw: int
    taps_valid: int
    points_awarded: int
    points_cycle: int
    bonus_awarded: int
    timed_out: bool
    cheated: bool
    suspicious: bool
    best_today: int


class StatusResponse(BaseModel):
    remaining_today: int
    max_per_day: int
    best_taps_ever: int
    last_run: Optional[dict] = None


# ══════════════════════════════════════════════════════════════════════════
#  Endpoints
# ══════════════════════════════════════════════════════════════════════════

async def _plays_today(conn, user_id: str) -> int:
    return int(await conn.fetchval(
        """SELECT COUNT(*) FROM tap_user_runs
           WHERE user_id = $1 AND started_at::date = CURRENT_DATE""",
        user_id,
    ) or 0)


@router.get("/status", response_model=StatusResponse)
async def tap_status(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        played = await _plays_today(conn, user["user_id"])
        best = int(await conn.fetchval(
            "SELECT COALESCE(MAX(taps_valid), 0) FROM tap_user_runs WHERE user_id = $1",
            user["user_id"],
        ) or 0)
        last = await conn.fetchrow(
            """SELECT taps_valid, points_awarded, submitted_at
               FROM tap_user_runs
               WHERE user_id = $1 AND submitted_at IS NOT NULL
               ORDER BY started_at DESC LIMIT 1""",
            user["user_id"],
        )
    cfg = await _tap_runtime_config()
    max_per_day = int(cfg["tap_sessions_per_day"])
    return StatusResponse(
        remaining_today=max(0, max_per_day - played),
        max_per_day=max_per_day,
        best_taps_ever=best,
        last_run=None if not last else {
            "taps": int(last["taps_valid"] or 0),
            "points": int(last["points_awarded"] or 0),
            "at": last["submitted_at"].isoformat() if last["submitted_at"] else None,
        },
    )


@router.post("/start", response_model=StartResponse)
async def tap_start(request: Request):
    await ensure_game_enabled("tap")
    user = await get_current_user(request)
    cfg = await _tap_runtime_config()
    # `ensure_game_enabled('tap')` already gated `tap_enabled` upstream.
    max_per_day = int(cfg["tap_sessions_per_day"])
    duration_s = int(cfg["tap_duration_seconds"])
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        played = await _plays_today(conn, user["user_id"])
        if played >= max_per_day:
            raise HTTPException(
                status_code=429,
                detail=f"Limite atteinte ({max_per_day} session/jour). Revenez demain !",
            )
        now = datetime.now(timezone.utc)
        run_id = await conn.fetchval(
            """INSERT INTO tap_user_runs (user_id, started_at)
               VALUES ($1, $2) RETURNING id""",
            user["user_id"], now,
        )
    return StartResponse(
        run_id=int(run_id),
        duration_seconds=duration_s,
        start_at=now.isoformat(),
        remaining_today=max(0, max_per_day - played - 1),
    )


@router.post("/submit", response_model=SubmitResponse)
async def tap_submit(req: SubmitRequest, request: Request):
    await ensure_game_enabled("tap")
    user = await get_current_user(request)
    cfg = await _tap_runtime_config()
    duration_s = int(cfg["tap_duration_seconds"])
    taps_per_sec_cap = int(cfg["tap_max_taps_per_second"])
    thresholds = cfg["tap_reward_thresholds"]   # [{taps,reward}, ...]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                "SELECT * FROM tap_user_runs WHERE id = $1 AND user_id = $2 FOR UPDATE",
                req.run_id, user["user_id"],
            )
            if not run:
                raise HTTPException(status_code=404, detail="Run introuvable.")
            if run["submitted_at"]:
                raise HTTPException(status_code=400, detail="Session déjà soumise.")

            now = datetime.now(timezone.utc)
            elapsed = (now - run["started_at"]).total_seconds()
            timed_out = elapsed > (duration_s + NETWORK_GRACE_SECONDS)

            # Anti-cheat: cap taps at ceiling × duration (admin-tunable)
            ceiling = taps_per_sec_cap * duration_s
            taps_raw = int(req.taps)
            taps_valid = min(taps_raw, ceiling)
            cheated = taps_raw > ceiling
            # Sustained tps near the human ceiling = suspicious flag.
            suspicious = taps_valid >= max(SUSPICIOUS_TAPS_THRESHOLD,
                                           int(taps_per_sec_cap * duration_s * 0.8))
            if timed_out:
                # If user waited too long, invalidate the taps entirely
                taps_valid = 0
                suspicious = False

            # Base points + milestone bonus (admin-configurable thresholds).
            # Awarded as the FIRST tier the user qualifies for, walking the
            # list from the largest `taps` to the smallest.
            base_points = taps_valid
            bonus = 0
            for tier in sorted(thresholds, key=lambda t: int(t["taps"]), reverse=True):
                if taps_valid >= int(tier["taps"]):
                    bonus = int(tier["reward"])
                    break
            total_points = base_points + bonus

            client_ip = request.client.host if request.client else ""
            user_agent = (request.headers.get("user-agent") or "")[:512]

            await conn.execute(
                """UPDATE tap_user_runs
                     SET submitted_at = $1, taps_raw = $2, taps_valid = $3,
                         points_awarded = $4, timed_out = $5, cheated = $6,
                         suspicious = $7, ip_address = $8, user_agent = $9
                   WHERE id = $10""",
                now, taps_raw, taps_valid, total_points, timed_out, cheated,
                suspicious, client_ip, user_agent, req.run_id,
            )

            # Register tap run on cycle (for admin telemetry)
            await register_tap_run(conn, user["user_id"])

            # Add points (honours sovereign clamp)
            cycle_update = await add_points(
                conn, user["user_id"], total_points, source="tap",
                metadata={
                    "ip": request.client.host if request.client else "",
                    "ua": (request.headers.get("user-agent") or "")[:256],
                },
            )

            best_today = int(await conn.fetchval(
                """SELECT COALESCE(MAX(taps_valid), 0) FROM tap_user_runs
                   WHERE user_id = $1 AND started_at::date = CURRENT_DATE""",
                user["user_id"],
            ) or 0)

    return SubmitResponse(
        run_id=req.run_id,
        taps_raw=taps_raw,
        taps_valid=taps_valid,
        points_awarded=cycle_update["points_awarded"],
        points_cycle=cycle_update["points_cycle"],
        bonus_awarded=bonus,
        timed_out=timed_out,
        cheated=cheated,
        suspicious=suspicious,
        best_today=best_today,
    )


@router.get("/history")
async def tap_history(request: Request, limit: int = 20):
    user = await get_current_user(request)
    limit = max(1, min(int(limit), 50))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        rows = await conn.fetch(
            """SELECT id, started_at, submitted_at, taps_valid, points_awarded, timed_out
               FROM tap_user_runs WHERE user_id = $1
               ORDER BY started_at DESC LIMIT $2""",
            user["user_id"], limit,
        )
    return {
        "items": [
            {
                "run_id": int(r["id"]),
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
                "taps": int(r["taps_valid"] or 0),
                "points": int(r["points_awarded"] or 0),
                "timed_out": bool(r["timed_out"]),
            } for r in rows
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
#  Admin endpoints
# ══════════════════════════════════════════════════════════════════════════

@router.get("/admin/overview")
async def admin_overview(request: Request, days: int = 30):
    """Compact observability dashboard for Tap Challenge."""
    from routes.auth import require_admin as _ra
    await _ra(request)
    days = max(1, min(int(days), 90))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        totals = await conn.fetchrow(
            """SELECT COUNT(*)                  AS runs,
                      COUNT(DISTINCT user_id)   AS players,
                      COALESCE(SUM(points_awarded), 0) AS points,
                      COALESCE(SUM(taps_valid), 0)      AS taps,
                      COALESCE(AVG(taps_valid), 0)::int AS avg_taps,
                      COALESCE(MAX(taps_valid), 0)      AS max_taps,
                      COALESCE(
                        percentile_cont(0.95) WITHIN GROUP (ORDER BY taps_valid), 0
                      )::int                            AS p95_taps,
                      COUNT(*) FILTER (WHERE cheated)    AS cheat_attempts,
                      COUNT(*) FILTER (WHERE suspicious) AS suspicious_runs
               FROM tap_user_runs
               WHERE submitted_at IS NOT NULL
                 AND started_at >= NOW() - ($1 || ' days')::interval""",
            str(days),
        )
        top = await conn.fetch(
            """SELECT r.user_id,
                      COUNT(*)                AS runs,
                      SUM(r.points_awarded)   AS points,
                      MAX(r.taps_valid)       AS best_taps,
                      u.first_name, u.last_name, u.email, u.avatar
               FROM tap_user_runs r
               LEFT JOIN users u ON u.user_id = r.user_id
               WHERE r.submitted_at IS NOT NULL
                 AND r.started_at >= NOW() - ($1 || ' days')::interval
               GROUP BY r.user_id, u.first_name, u.last_name, u.email, u.avatar
               ORDER BY points DESC LIMIT 10""",
            str(days),
        )
        ts = await conn.fetch(
            """SELECT started_at::date AS day, COUNT(*) AS runs,
                      COALESCE(SUM(points_awarded),0) AS points,
                      COALESCE(AVG(taps_valid),0)::int AS avg_taps
               FROM tap_user_runs
               WHERE submitted_at IS NOT NULL
                 AND started_at >= NOW() - ($1 || ' days')::interval
               GROUP BY 1 ORDER BY 1""",
            str(days),
        )

    return {
        "window_days": days,
        "runs": int(totals["runs"] or 0),
        "players": int(totals["players"] or 0),
        "points_distributed": int(totals["points"] or 0),
        "taps_total": int(totals["taps"] or 0),
        "avg_taps_per_run": int(totals["avg_taps"] or 0),
        "max_taps_ever": int(totals["max_taps"] or 0),
        "p95_taps": int(totals["p95_taps"] or 0),
        "cheat_attempts": int(totals["cheat_attempts"] or 0),
        "suspicious_runs": int(totals["suspicious_runs"] or 0),
        "top_players": [
            {
                "user_id": r["user_id"],
                "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["email"] or r["user_id"],
                "email": r["email"],
                "avatar": r["avatar"],
                "runs": int(r["runs"]),
                "points": int(r["points"] or 0),
                "best_taps": int(r["best_taps"] or 0),
            } for r in top
        ],
        "timeseries": [
            {
                "day": t["day"].isoformat(),
                "runs": int(t["runs"]),
                "points": int(t["points"] or 0),
                "avg_taps": int(t["avg_taps"] or 0),
            } for t in ts
        ],
    }


@router.get("/admin/suspicious")
async def admin_suspicious(request: Request, days: int = 30, limit: int = 50):
    """List suspicious tap runs (high taps or cheat attempts) for admin review.

    A run is suspicious if `suspicious=TRUE` (taps ≥ 100 = ~10 tps sustained)
    or `cheated=TRUE` (client sent raw > 12 tps × 10s = 120 cap).
    """
    from routes.auth import require_admin as _ra
    await _ra(request)
    days = max(1, min(int(days), 90))
    limit = max(1, min(int(limit), 500))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        rows = await conn.fetch(
            """SELECT r.id, r.user_id, r.started_at, r.submitted_at,
                      r.taps_raw, r.taps_valid, r.points_awarded,
                      r.cheated, r.suspicious, r.ip_address, r.user_agent,
                      u.first_name, u.last_name, u.email, u.avatar
               FROM tap_user_runs r
               LEFT JOIN users u ON u.user_id = r.user_id
               WHERE r.submitted_at IS NOT NULL
                 AND (r.suspicious = TRUE OR r.cheated = TRUE)
                 AND r.started_at >= NOW() - ($1 || ' days')::interval
               ORDER BY r.started_at DESC LIMIT $2""",
            str(days), limit,
        )
    return {
        "window_days": days,
        "items": [
            {
                "run_id": int(r["id"]),
                "user_id": r["user_id"],
                "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["email"] or r["user_id"],
                "email": r["email"],
                "avatar": r["avatar"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
                "taps_raw": int(r["taps_raw"] or 0),
                "taps_valid": int(r["taps_valid"] or 0),
                "points": int(r["points_awarded"] or 0),
                "cheated": bool(r["cheated"]),
                "suspicious": bool(r["suspicious"]),
                "ip_address": r["ip_address"] or "",
                "user_agent": (r["user_agent"] or "")[:120],
            } for r in rows
        ],
    }


@router.post("/admin/reset-user/{target_user_id}")
async def admin_reset_user(target_user_id: str, request: Request):
    """Wipe a user's tap history. Use with care (abuse remediation)."""
    from routes.auth import require_admin as _ra
    admin = await _ra(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        deleted = await conn.execute(
            "DELETE FROM tap_user_runs WHERE user_id = $1", target_user_id,
        )
    try:
        from services.security_service import log_security_event
        await log_security_event(
            admin.get("user_id"), "tap.admin_reset_user",
            severity="warning", ip="", ua="",
            details={"target_user_id": target_user_id, "raw": deleted},
        )
    except Exception:
        pass
    return {"status": "ok", "raw": deleted}
