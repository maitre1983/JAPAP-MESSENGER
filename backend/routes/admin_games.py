"""
Admin endpoints to read/update Quiz + Tap runtime configuration.
================================================================

Routes:
  GET   /api/admin/games/quiz   → {config, defaults, bounds}
  PUT   /api/admin/games/quiz   → updates the Quiz config
  GET   /api/admin/games/tap    → {config, defaults, bounds}
  PUT   /api/admin/games/tap    → updates the Tap config

iter224 — Quiz AI question pool monitoring:
  GET   /api/admin/games/quiz/pool-status   → live health of quiz_questions
  POST  /api/admin/games/quiz/pool-refresh  → admin-triggered AI batch (async)

All updates are idempotent and audit-logged via routes.auth.log_admin_action.
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from database import get_pool
from routes.auth import get_current_user, log_admin_action
from services.games_settings import (
    QUIZ_DEFAULTS, QUIZ_BOUNDS, TAP_DEFAULTS, TAP_BOUNDS,
    get_quiz_config, get_tap_config,
    update_quiz_config, update_tap_config,
)
from services.quiz_champion_scheduler import (
    POOL_REFRESH_HOURS, POOL_HEALTH_MIN, POOL_BATCH_SIZE,
)

router = APIRouter(prefix="/api/admin/games", tags=["admin", "games"])


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    return user


class QuizUpdate(BaseModel):
    # extra='forbid' surfaces the original "Clé inconnue" error from
    # games_settings.update_quiz_config instead of silently dropping the key.
    model_config = ConfigDict(extra='forbid')
    quiz_enabled:                 bool | None = None
    quiz_sessions_per_day:        int | None = None
    quiz_timer_seconds:           int | None = None
    quiz_timer_per_question_seconds: int | None = None   # iter118
    quiz_timer_mode:              str | None = None      # iter118 ('global'|'per_question')
    quiz_points_per_correct:      int | None = None
    quiz_perfect_bonus:           int | None = None
    quiz_session_size:            int | None = None
    quiz_auto_advance_enabled:    bool | None = None     # iter118
    quiz_auto_advance_delay_ms:   int | None = None      # iter118
    quiz_auto_advance_delays_ms:  list | None = None     # iter120 (per-question)
    quiz_show_correct_after_wrong: bool | None = None    # iter122 (learning mode)
    # iter125 — Phase 3.B paid challenge settings
    quiz_challenge_paid_enabled:           bool | None = None
    quiz_challenge_commission_pct:         int | None = None
    quiz_challenge_stake_min:              int | None = None
    quiz_challenge_stake_max:              int | None = None
    quiz_challenge_refund_on_expiry:       bool | None = None
    quiz_challenge_challenger_bonus_points: int | None = None
    quiz_challenge_expiry_hours:           int | None = None
    # iter130 — Phase 3.E settings (anti-repeat, daily challenge, AI distribution).
    # MUST be declared here because of `extra='forbid'`. Missing them caused
    # a 422 Pydantic error every time the admin clicked "Enregistrer" on the
    # Quiz tab (frontend tried to render the error array → React #31 → blue
    # ErrorBoundary screen).
    quiz_anti_repeat_days:                   int | None = None
    quiz_dist_africa_pct:                    int | None = None
    quiz_dist_sport_pct:                     int | None = None
    quiz_dist_econ_pct:                      int | None = None
    quiz_dist_world_pct:                     int | None = None
    quiz_daily_challenge_enabled:            bool | None = None
    quiz_daily_challenge_points_per_correct: int | None = None
    quiz_daily_challenge_perfect_bonus:      int | None = None
    quiz_daily_challenge_streak_bonus_per_day: int | None = None
    quiz_daily_challenge_streak_bonus_cap:   int | None = None


class TapUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tap_enabled:                  bool | None = None
    tap_sessions_per_day:         int | None = None
    tap_duration_seconds:         int | None = None
    tap_max_taps_per_second:      int | None = None
    tap_reward_thresholds:        list | None = None


@router.get("/quiz")
async def get_quiz(request: Request):
    await _require_admin(request)
    return {
        "config":   await get_quiz_config(),
        "defaults": QUIZ_DEFAULTS,
        "bounds":   QUIZ_BOUNDS,
    }


@router.put("/quiz")
async def put_quiz(req: QuizUpdate, request: Request):
    admin = await _require_admin(request)
    updates = {k: v for k, v in req.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="Aucun champ à mettre à jour.")
    try:
        config = await update_quiz_config(updates, admin_id=admin["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await log_admin_action(
        actor_id=admin["user_id"],
        actor_email=admin.get("email", ""),
        action="games.quiz.config_update",
        metadata={"keys": sorted(updates.keys())},
    )
    return {"status": "ok", "config": config}


@router.get("/tap")
async def get_tap(request: Request):
    await _require_admin(request)
    return {
        "config":   await get_tap_config(),
        "defaults": TAP_DEFAULTS,
        "bounds":   TAP_BOUNDS,
    }


@router.put("/tap")
async def put_tap(req: TapUpdate, request: Request):
    admin = await _require_admin(request)
    updates = {k: v for k, v in req.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="Aucun champ à mettre à jour.")
    try:
        config = await update_tap_config(updates, admin_id=admin["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await log_admin_action(
        actor_id=admin["user_id"],
        actor_email=admin.get("email", ""),
        action="games.tap.config_update",
        metadata={"keys": sorted(updates.keys())},
    )
    return {"status": "ok", "config": config}



# ─────────────────────────────────────────────────────────────────────
# iter224 — Quiz AI Question Pool monitoring
# ─────────────────────────────────────────────────────────────────────

# Single-flight guard for the manual pool-refresh endpoint. Prevents
# admin double-clicks from spawning N parallel Claude batches.
_pool_refresh_in_flight: bool = False


def _health_status(active: int) -> str:
    """Map active question count to a 3-level health label.

    Aligned with the scheduler's POOL_HEALTH_MIN (default 30):
      • CRITICAL : count < 15  (immediate attention — picker may 503)
      • WARNING  : 15 ≤ count < POOL_HEALTH_MIN
      • OK       : count ≥ POOL_HEALTH_MIN
    """
    if active < 15:
        return "critical"
    if active < POOL_HEALTH_MIN:
        return "warning"
    return "ok"


@router.get("/quiz/pool-status")
async def get_quiz_pool_status(request: Request):
    """Read-only snapshot of the AI question pool.

    Returns counts + last/next refresh timestamps inferred from the
    `created_at` column of `quiz_questions WHERE source='ai'` so the
    answer survives backend pod rotations.
    """
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        active = await conn.fetchval(
            "SELECT COUNT(*) FROM quiz_questions "
            "WHERE active = TRUE AND obsolete = FALSE",
        ) or 0
        ai_total = await conn.fetchval(
            "SELECT COUNT(*) FROM quiz_questions WHERE source = 'ai'",
        ) or 0
        last_ai_created_at = await conn.fetchval(
            "SELECT MAX(created_at) FROM quiz_questions WHERE source = 'ai'",
        )
        # Last batch size = AI questions inserted within ±5 min of the most
        # recent AI created_at. Approximation: a single generate_with_distribution
        # call inserts the batch in a tight burst (~few seconds per category).
        last_batch_size = 0
        if last_ai_created_at is not None:
            window_start = last_ai_created_at - timedelta(minutes=5)
            window_end   = last_ai_created_at + timedelta(minutes=5)
            last_batch_size = await conn.fetchval(
                "SELECT COUNT(*) FROM quiz_questions "
                "WHERE source = 'ai' AND created_at BETWEEN $1 AND $2",
                window_start, window_end,
            ) or 0

    next_refresh_at = (
        last_ai_created_at + timedelta(hours=POOL_REFRESH_HOURS)
        if last_ai_created_at else None
    )
    now = datetime.now(timezone.utc)
    seconds_until_next = (
        max(0, int((next_refresh_at - now).total_seconds()))
        if next_refresh_at else None
    )

    return {
        "active_count":          int(active),
        "ai_total":              int(ai_total),
        "health":                _health_status(int(active)),
        "health_min":            POOL_HEALTH_MIN,
        "last_refresh_at":       last_ai_created_at.isoformat() if last_ai_created_at else None,
        "last_batch_size":       int(last_batch_size),
        "next_refresh_at":       next_refresh_at.isoformat() if next_refresh_at else None,
        "seconds_until_next":    seconds_until_next,
        "refresh_interval_hours": POOL_REFRESH_HOURS,
        "batch_size":            POOL_BATCH_SIZE,
        "refresh_in_flight":     _pool_refresh_in_flight,
    }


# ---------------------------------------------------------------------------
# iter232 — AI validation stats endpoint (Mission 3).
# ---------------------------------------------------------------------------
@router.get("/quiz/validation-stats")
async def get_quiz_validation_stats(request: Request, days: int = 30):
    """Returns aggregated acceptance/rejection rate from the Claude validator
    over the last `days` days. Used by the admin pool widget."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT
                  COALESCE(SUM(total_generated), 0)::int AS generated,
                  COALESCE(SUM(accepted),        0)::int AS accepted,
                  COALESCE(SUM(rejected),        0)::int AS rejected,
                  COALESCE(AVG(avg_confidence), 0)       AS avg_conf,
                  COUNT(*)::int                          AS batches
                FROM quiz_ai_validation_stats
               WHERE created_at >= NOW() - ($1 || ' days')::interval""",
            str(int(days)),
        )
        per_cat = await conn.fetch(
            """SELECT category,
                      SUM(total_generated)::int AS generated,
                      SUM(accepted)::int        AS accepted,
                      SUM(rejected)::int        AS rejected,
                      AVG(avg_confidence)        AS avg_conf
                 FROM quiz_ai_validation_stats
                WHERE created_at >= NOW() - ($1 || ' days')::interval
                GROUP BY category
                ORDER BY generated DESC""",
            str(int(days)),
        )
        recent = await conn.fetch(
            """SELECT batch_id, category, total_generated, accepted, rejected,
                      avg_confidence, rejection_reasons, created_at
                 FROM quiz_ai_validation_stats
                ORDER BY created_at DESC LIMIT 20""",
        )
    r0 = rows[0] if rows else {"generated": 0, "accepted": 0, "rejected": 0,
                               "avg_conf": 0, "batches": 0}
    g = int(r0["generated"]) or 0
    accept_rate = (int(r0["accepted"]) / g * 100) if g else 0
    return {
        "window_days": int(days),
        "generated": int(r0["generated"]),
        "accepted":  int(r0["accepted"]),
        "rejected":  int(r0["rejected"]),
        "avg_confidence": round(float(r0["avg_conf"] or 0), 2),
        "accept_rate_pct": round(accept_rate, 1),
        "batches": int(r0["batches"]),
        "by_category": [
            {
                "category": r["category"],
                "generated": int(r["generated"]),
                "accepted": int(r["accepted"]),
                "rejected": int(r["rejected"]),
                "avg_confidence": round(float(r["avg_conf"] or 0), 2),
            }
            for r in per_cat
        ],
        "recent_batches": [
            {
                "batch_id": r["batch_id"],
                "category": r["category"],
                "total_generated": int(r["total_generated"]),
                "accepted": int(r["accepted"]),
                "rejected": int(r["rejected"]),
                "avg_confidence": round(float(r["avg_confidence"] or 0), 2),
                "rejection_reasons": r["rejection_reasons"] or {},
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in recent
        ],
    }


async def _run_manual_refresh(admin_id: str, admin_email: str):
    """Background task — generates a fresh AI batch and audit-logs the result."""
    global _pool_refresh_in_flight
    try:
        from services.quiz_ai_generator import generate_with_distribution
        pool = await get_pool()
        async with pool.acquire() as conn:
            summary = await generate_with_distribution(conn, total=POOL_BATCH_SIZE)
        await log_admin_action(
            actor_id=admin_id,
            actor_email=admin_email,
            action="games.quiz.pool_refresh",
            metadata={
                "inserted": summary.get("inserted", 0),
                "skipped":  summary.get("skipped", 0),
                "by_category": summary.get("by_category", {}),
                "trigger": "manual",
            },
        )
    except Exception as e:  # noqa: BLE001
        await log_admin_action(
            actor_id=admin_id,
            actor_email=admin_email,
            action="games.quiz.pool_refresh_failed",
            metadata={"error": str(e)[:200]},
        )
    finally:
        _pool_refresh_in_flight = False


@router.post("/quiz/pool-refresh", status_code=202)
async def post_quiz_pool_refresh(request: Request, background_tasks: BackgroundTasks):
    """Admin-triggered manual AI batch. Returns 202 immediately; the
    actual Claude call runs in a background task (~30-60s). Single-flight
    guarded so concurrent clicks no-op."""
    global _pool_refresh_in_flight
    admin = await _require_admin(request)
    if _pool_refresh_in_flight:
        raise HTTPException(
            status_code=409,
            detail="Un renouvellement est déjà en cours.",
        )
    _pool_refresh_in_flight = True
    background_tasks.add_task(
        _run_manual_refresh,
        admin["user_id"],
        admin.get("email", ""),
    )
    return {
        "status":    "accepted",
        "message":   f"Génération de {POOL_BATCH_SIZE} questions en cours…",
        "batch_size": POOL_BATCH_SIZE,
    }
