"""
iter83 — Quiz JAPAP backend (Phase 2).

Architecture
============
DB (3 tables, all idempotent):
  - quiz_questions   : question bank (AI + admin-authored, category + difficulty + status)
  - quiz_sessions    : 100 deterministic 5Q sessions built from the bank
  - quiz_user_runs   : every run a user plays (5Q × 10s → scored by backend)

Endpoints (all /api/quiz):
  - POST /start     → start a run, returns 5 questions (WITHOUT correct index)
  - POST /submit    → submit all 5 answers, backend scores, adds points, registers accuracy
  - GET  /history   → user's last 20 runs
  - GET  /admin/questions (admin CRUD)
  - POST /admin/questions
  - PUT  /admin/questions/{id}
  - DELETE /admin/questions/{id}
  - POST /admin/regenerate-ai   → batch re-seed 100 sessions via Claude Sonnet 4.5

Strict rules enforced server-side :
  - 10s hard timeout (start_at stored server-side, submit rejected if ∆t > 12s incl. network)
  - Max 3 runs/day (anti-exploit)
  - 75 % accuracy rule is enforced at the CYCLE level by points_service
  - Never expose `correct_index` before /submit
"""
from __future__ import annotations

import json as _json
import logging
import os
import random
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional


def _parse_options(raw: Any) -> List[str]:
    """Decode a JSONB `options` column that asyncpg returns as raw text when
    no JSON codec is registered on the pool (Neon + statement_cache_size=0)."""
    if isinstance(raw, str):
        try:
            return _json.loads(raw)
        except (ValueError, TypeError):
            return []
    if isinstance(raw, list):
        return raw
    return []

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user
from services.points_service import (
    ensure_game_enabled, add_points, register_quiz_answers,
)
from services.games_settings import get_quiz_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/quiz", tags=["quiz"])

SESSION_SIZE = 5
SESSION_TIME_LIMIT_SECONDS = 60    # default fallback — can be overridden by admin
SESSION_TIME_NETWORK_GRACE_SECONDS = 10  # iter118: 10s grace (was 4s — too strict)
MAX_RUNS_PER_DAY = 3
POINTS_PER_CORRECT = 20
POINTS_PERFECT_BONUS = 30    # 5/5 → extra +30


async def _quiz_runtime_config() -> dict:
    """Single source of truth for the runtime Quiz config. Reads from
    `admin_settings` (defaults + bounds enforced). Imports are hoisted
    at module level (iter108 perf cleanup)."""
    return await get_quiz_config()


async def _current_timer_seconds() -> int:
    cfg = await _quiz_runtime_config()
    return int(cfg["quiz_timer_seconds"])
CATEGORIES = [
    "culture_generale", "sport", "economie",
    "crypto", "actualite", "afrique_monde",
]
DIFFICULTIES = ["easy", "medium", "hard"]


# ══════════════════════════════════════════════════════════════════════════
#  DDL
# ══════════════════════════════════════════════════════════════════════════

_DDL = [
    """CREATE TABLE IF NOT EXISTS quiz_questions (
         id BIGSERIAL PRIMARY KEY,
         text        TEXT NOT NULL,
         options     JSONB NOT NULL,
         correct_index INT NOT NULL,
         category    VARCHAR(32) NOT NULL,
         difficulty  VARCHAR(8) NOT NULL DEFAULT 'medium',
         source      VARCHAR(8) NOT NULL DEFAULT 'ai',
         active      BOOLEAN NOT NULL DEFAULT TRUE,
         created_by  VARCHAR(32),
         created_at  TIMESTAMPTZ DEFAULT NOW(),
         updated_at  TIMESTAMPTZ DEFAULT NOW()
       )""",
    "CREATE INDEX IF NOT EXISTS idx_quiz_q_active ON quiz_questions(active, category)",
    """CREATE TABLE IF NOT EXISTS quiz_sessions (
         id BIGSERIAL PRIMARY KEY,
         question_ids BIGINT[] NOT NULL,
         categories  TEXT[]    NOT NULL,
         created_at  TIMESTAMPTZ DEFAULT NOW()
       )""",
    """CREATE TABLE IF NOT EXISTS quiz_user_runs (
         id BIGSERIAL PRIMARY KEY,
         user_id     VARCHAR(32) NOT NULL,
         session_id  BIGINT NOT NULL,
         started_at  TIMESTAMPTZ NOT NULL,
         submitted_at TIMESTAMPTZ,
         answers     INT[],
         correct_count INT,
         points_awarded INT,
         timed_out   BOOLEAN DEFAULT FALSE,
         options_order JSONB
       )""",
    """ALTER TABLE quiz_user_runs ADD COLUMN IF NOT EXISTS options_order JSONB""",
    "ALTER TABLE quiz_user_runs ADD COLUMN IF NOT EXISTS time_limit_s INT NOT NULL DEFAULT 10",
    # iter120 — Anti-cheat for the live /answer reveal endpoint. Stores the
    # FIRST selected_option per question_idx so subsequent calls cannot
    # brute-force the correct answer by trying 0..3.
    "ALTER TABLE quiz_user_runs ADD COLUMN IF NOT EXISTS revealed_options JSONB",
    "CREATE INDEX IF NOT EXISTS idx_quiz_runs_user ON quiz_user_runs(user_id, started_at DESC)",
    # iter130 — Phase 3.E: Anti-repetition history. Tracks every (user, question)
    # pair that was SERVED in the last 7 days across all quiz modules, so the
    # session picker can exclude them on the next /start. Also enables admin
    # analytics ("question seen N times in 7d") and content quality control.
    """CREATE TABLE IF NOT EXISTS user_quiz_question_history (
         id BIGSERIAL PRIMARY KEY,
         user_id     VARCHAR(32) NOT NULL,
         question_id BIGINT      NOT NULL,
         source      VARCHAR(24) NOT NULL DEFAULT 'quiz_standard',
         seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
       )""",
    "CREATE INDEX IF NOT EXISTS idx_uqqh_user_seen ON user_quiz_question_history(user_id, seen_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_uqqh_question  ON user_quiz_question_history(question_id, seen_at DESC)",
    # iter130 — Per-category enable/disable + obsolete flag for content
    # mgmt. Defaults: all categories enabled, no question is obsolete.
    "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS obsolete BOOLEAN NOT NULL DEFAULT FALSE",
    """CREATE TABLE IF NOT EXISTS quiz_category_status (
         category VARCHAR(32) PRIMARY KEY,
         enabled  BOOLEAN NOT NULL DEFAULT TRUE,
         priority INT     NOT NULL DEFAULT 1,   -- higher = more likely in mixed sessions
         updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
       )""",
    # iter130 — Daily Challenge guard. Tracks the last successful daily play
    # per user so we cap to 1/day even across timezones (UTC date).
    """CREATE TABLE IF NOT EXISTS quiz_daily_challenge_runs (
         user_id    VARCHAR(32) NOT NULL,
         play_date  DATE        NOT NULL,
         run_id     BIGINT      NOT NULL,
         PRIMARY KEY (user_id, play_date)
       )""",
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
#  Pydantic models (moved up — referenced by daily-challenge endpoints below)
# ══════════════════════════════════════════════════════════════════════════

class StartResponse(BaseModel):
    run_id: int
    session_id: int
    time_limit_seconds: int
    timer_mode: str = "per_question"
    timer_per_question_seconds: int = 15
    auto_advance_enabled: bool = True
    auto_advance_delay_ms: int = 900
    auto_advance_delays_ms: List[int] = []
    questions: List[dict]


class SubmitRequest(BaseModel):
    run_id: int
    answers: List[int] = Field(..., min_length=5, max_length=5)


class AnswerRevealRequest(BaseModel):
    run_id: int
    question_idx: int = Field(..., ge=0, le=19)
    selected_option: int = Field(..., ge=0, le=3)


class AnswerRevealResponse(BaseModel):
    correct: bool
    question_idx: int
    correct_option: Optional[int] = None


@router.get("/daily-challenge/status")
async def daily_challenge_status(request: Request):
    """iter130 — Phase 3.E: One free Daily Challenge per UTC day. This
    endpoint tells the UI whether the user can play today + when the
    next eligible play unlocks (00:00 UTC the day after).
    """
    user = await get_current_user(request)
    pool = await get_pool()
    cfg = await _quiz_runtime_config()
    enabled = bool(cfg.get("quiz_daily_challenge_enabled", True))
    from services.quiz_daily_streak import get_streak
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        row = await conn.fetchrow(
            """SELECT play_date, run_id FROM quiz_daily_challenge_runs
                WHERE user_id = $1 AND play_date = (NOW() AT TIME ZONE 'utc')::date""",
            user["user_id"],
        )
        streak = await get_streak(conn, user["user_id"])
    # Next eligible at: tomorrow 00:00 UTC ISO8601
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    next_eligible_iso = datetime.combine(
        tomorrow, datetime.min.time(), tzinfo=timezone.utc
    ).isoformat()
    if row:
        result_row = None
        if row["run_id"]:
            async with pool.acquire() as conn:
                result_row = await conn.fetchrow(
                    """SELECT correct_count, points_awarded, submitted_at
                         FROM quiz_user_runs WHERE id=$1""",
                    int(row["run_id"]),
                )
        result = None
        if result_row:
            result = {
                "correct_count": int(result_row["correct_count"] or 0),
                "points_awarded": int(result_row["points_awarded"] or 0),
                "submitted_at": result_row["submitted_at"].isoformat()
                if result_row["submitted_at"] else None,
            }
        return {
            "available": False,
            "played_today": True,
            "result": result,
            "next_eligible_at": next_eligible_iso,
            "streak": streak,
            "enabled": enabled,
        }
    return {
        "available": enabled,
        "played_today": False,
        "next_eligible_at": None,
        "streak": streak,
        "enabled": enabled,
    }


@router.post("/daily-challenge/start", response_model=StartResponse)
async def daily_challenge_start(request: Request):
    """iter130 — Start the user's once-per-day free quiz session. Bypasses
    the standard daily-3 cap (it has its own dedicated 1/day limit). Uses
    the anti-repetition picker."""
    await ensure_game_enabled("quiz")
    user = await get_current_user(request)
    uid = user["user_id"]
    pool = await get_pool()
    cfg = await _quiz_runtime_config()
    if not bool(cfg.get("quiz_daily_challenge_enabled", True)):
        raise HTTPException(
            status_code=503,
            detail="Le défi quotidien est temporairement désactivé.",
        )
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        async with conn.transaction():
            # Already played today? (FOR UPDATE for concurrent safety)
            already = await conn.fetchrow(
                """SELECT run_id FROM quiz_daily_challenge_runs
                    WHERE user_id = $1
                      AND play_date = (NOW() AT TIME ZONE 'utc')::date
                    FOR UPDATE""",
                uid,
            )
            if already:
                raise HTTPException(
                    status_code=429,
                    detail="Vous avez déjà joué le défi quotidien aujourd'hui. Revenez demain !",
                )
            from services.quiz_question_picker import create_session_for_user
            session_id, qids, fallback = await create_session_for_user(
                conn, uid, source="daily_challenge", size=SESSION_SIZE,
            )
            if not session_id or len(qids) < SESSION_SIZE:
                raise HTTPException(status_code=503, detail="Banque de questions épuisée.")
            timer_mode = str(cfg.get("quiz_timer_mode") or "per_question")
            per_q_s = int(cfg.get("quiz_timer_per_question_seconds") or 15)
            timer_s = int(cfg["quiz_timer_seconds"])
            effective_total_s = (per_q_s * SESSION_SIZE if timer_mode == "per_question" else timer_s)
            now = datetime.now(timezone.utc)
            q_rows = await conn.fetch(
                "SELECT id, text, options, category FROM quiz_questions WHERE id = ANY($1::bigint[])",
                qids,
            )
            by_id = {r["id"]: r for r in q_rows}
            options_order: List[List[int]] = []
            questions = []
            for qid in qids:
                q = by_id.get(qid)
                if not q:
                    continue
                original = _parse_options(q["options"])
                perm = [0, 1, 2, 3]
                random.shuffle(perm)
                options_order.append(perm)
                questions.append({
                    "id": int(q["id"]),
                    "text": q["text"],
                    "options": [original[i] for i in perm],
                    "category": q["category"],
                })
            run_id = await conn.fetchval(
                """INSERT INTO quiz_user_runs
                     (user_id, session_id, started_at, options_order, time_limit_s)
                   VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id""",
                uid, session_id, now, _json.dumps(options_order), effective_total_s,
            )
            await conn.execute(
                """INSERT INTO quiz_daily_challenge_runs (user_id, play_date, run_id)
                   VALUES ($1, (NOW() AT TIME ZONE 'utc')::date, $2)""",
                uid, int(run_id),
            )
    return StartResponse(
        run_id=int(run_id),
        session_id=int(session_id),
        time_limit_seconds=effective_total_s,
        timer_mode=timer_mode,
        timer_per_question_seconds=per_q_s,
        auto_advance_enabled=bool(cfg.get("quiz_auto_advance_enabled", True)),
        auto_advance_delay_ms=int(cfg.get("quiz_auto_advance_delay_ms") or 900),
        auto_advance_delays_ms=list(cfg.get("quiz_auto_advance_delays_ms") or []),
        questions=questions,
    )


@router.post("/daily-challenge/submit")
async def daily_challenge_submit(req: SubmitRequest, request: Request):
    """iter130 — Submit the daily challenge run. Computes points using
    daily-challenge dedicated config keys (separate from /submit) and
    bumps the streak counter. Returns full result + new streak + share text.
    """
    await ensure_game_enabled("quiz")
    user = await get_current_user(request)
    uid = user["user_id"]
    pool = await get_pool()
    cfg = await _quiz_runtime_config()
    from services.quiz_daily_streak import tick_streak
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                """SELECT * FROM quiz_user_runs WHERE id = $1 AND user_id = $2 FOR UPDATE""",
                req.run_id, uid,
            )
            if not run:
                raise HTTPException(status_code=404, detail="Run introuvable.")
            if run["submitted_at"]:
                raise HTTPException(status_code=400, detail="Session déjà soumise.")
            # Confirm this is a daily-challenge run.
            dc_row = await conn.fetchrow(
                """SELECT play_date FROM quiz_daily_challenge_runs
                    WHERE user_id = $1 AND run_id = $2""",
                uid, req.run_id,
            )
            if not dc_row:
                raise HTTPException(
                    status_code=400,
                    detail="Ce run n'est pas un défi quotidien — utilisez /api/quiz/submit.",
                )

            now = datetime.now(timezone.utc)
            elapsed = (now - run["started_at"]).total_seconds()
            effective_limit = int(run["time_limit_s"] or SESSION_TIME_LIMIT_SECONDS)
            timed_out = elapsed > (effective_limit + SESSION_TIME_NETWORK_GRACE_SECONDS)

            session = await conn.fetchrow(
                "SELECT question_ids FROM quiz_sessions WHERE id = $1",
                run["session_id"],
            )
            q_rows = await conn.fetch(
                "SELECT id, correct_index FROM quiz_questions WHERE id = ANY($1::bigint[])",
                list(session["question_ids"]),
            )
            correct_map = {int(r["id"]): int(r["correct_index"]) for r in q_rows}
            order = [int(qid) for qid in session["question_ids"]]
            raw_perm = run["options_order"]
            if isinstance(raw_perm, str):
                try:
                    perms = _json.loads(raw_perm)
                except (ValueError, TypeError):
                    perms = []
            else:
                perms = raw_perm or []
            while len(perms) < SESSION_SIZE:
                perms.append([0, 1, 2, 3])

            answers = list(req.answers)
            correct_count = 0
            correct_displayed: List[int] = []
            for i, qid in enumerate(order):
                original_correct = correct_map.get(qid, -1)
                perm = perms[i] if i < len(perms) else [0, 1, 2, 3]
                try:
                    displayed_correct = perm.index(original_correct) if original_correct >= 0 else -1
                except ValueError:
                    displayed_correct = -1
                correct_displayed.append(displayed_correct)
                given = answers[i] if i < len(answers) else -1
                given_original = perm[given] if 0 <= given < len(perm) else -1
                if given_original >= 0 and given_original == original_correct:
                    correct_count += 1

            # Streak tick
            play_date = dc_row["play_date"]
            new_streak = await tick_streak(conn, uid, play_date)

            # Points calculation (daily-challenge dedicated config)
            base_pts = correct_count * int(cfg.get("quiz_daily_challenge_points_per_correct", 25))
            perfect_bonus = 0
            if correct_count == SESSION_SIZE and not timed_out:
                perfect_bonus = int(cfg.get("quiz_daily_challenge_perfect_bonus", 50))
            streak_bonus_per_day = int(cfg.get("quiz_daily_challenge_streak_bonus_per_day", 5))
            streak_cap = int(cfg.get("quiz_daily_challenge_streak_bonus_cap", 150))
            # Streak bonus only on at least 1 correct (avoids gaming with 0/5)
            streak_bonus = 0
            if correct_count > 0:
                streak_bonus = min(streak_cap, max(0, new_streak["current_streak"] - 1) * streak_bonus_per_day)
            total_points = base_pts + perfect_bonus + streak_bonus

            await conn.execute(
                """UPDATE quiz_user_runs
                     SET submitted_at = $1, answers = $2, correct_count = $3,
                         points_awarded = $4, timed_out = $5
                   WHERE id = $6""",
                now, answers, correct_count, total_points, timed_out, req.run_id,
            )
            await register_quiz_answers(conn, uid, correct_count, SESSION_SIZE)
            cycle_update = await add_points(
                conn, uid, total_points, source="quiz_daily_challenge",
                metadata={
                    "ip": request.client.host if request.client else "",
                    "ua": (request.headers.get("user-agent") or "")[:256],
                    "streak": new_streak["current_streak"],
                },
            )

    # iter140 — Auto-create a sharable Duel from the daily challenge run so
    # the share_text is no longer a vanity broadcast: every friend who
    # clicks the link will play THE SAME 5 questions, get a real comparison
    # screen (CompletedDuelView with both scores + winner + WhatsApp), and
    # the original player can poll their /duel/me/list dashboard to see
    # every challenger's outcome (multi-challenger via duel_attempts iter141).
    duel_token = ""
    duel_share_url = ""
    try:
        from routes.duel import _ensure_ddl as _duel_ensure_ddl
        import secrets as _secrets
        async with pool.acquire() as conn:
            await _duel_ensure_ddl(conn)
            already = await conn.fetchval(
                """SELECT share_token FROM duels
                    WHERE challenger_run_id = $1 AND game = 'quiz'""",
                req.run_id,
            )
            if already:
                duel_token = already
            else:
                duel_token = _secrets.token_urlsafe(16)
                expires = datetime.now(timezone.utc) + timedelta(hours=24)
                challenger_time_s = round(elapsed, 2) if elapsed else None
                # iter140 — duel_kind='multi_attempts' opens this duel to N
                # challengers (handled by routes/duel iter141 update).
                await conn.execute(
                    """INSERT INTO duels
                         (share_token, game, challenger_id, challenger_run_id,
                          challenger_score, challenger_session_id, challenger_metadata,
                          challenger_time_s, expires_at, duel_kind)
                       VALUES ($1, 'quiz', $2, $3, $4, $5, $6::jsonb, $7, $8, 'multi_attempts')""",
                    duel_token, uid, req.run_id, correct_count,
                    int(run["session_id"]),
                    _json.dumps({
                        "correct_count": correct_count,
                        "total": SESSION_SIZE,
                        "time_s": challenger_time_s,
                        "from_daily_challenge": True,
                    }),
                    challenger_time_s, expires,
                )
        # Build a public-facing URL using the request's origin so the link
        # works in production (japapmessenger.com) AND in preview env.
        origin = (request.headers.get("origin")
                  or request.headers.get("referer", "").split("/")[0:3] and "/".join(request.headers.get("referer", "").split("/")[:3])
                  or "")
        duel_share_url = f"{origin}/duel/{duel_token}" if origin and duel_token else f"/duel/{duel_token}"
    except Exception as _e:
        logger.debug(f"[daily-challenge] auto-duel creation skipped: {_e}")

    next_eligible = (datetime.now(timezone.utc) + timedelta(days=1))
    next_eligible_iso = datetime.combine(
        next_eligible.date(), datetime.min.time(), tzinfo=timezone.utc
    ).isoformat()
    return {
        "run_id": req.run_id,
        "correct_count": correct_count,
        "total": SESSION_SIZE,
        "accuracy": round(correct_count / SESSION_SIZE, 2),
        "perfect": correct_count == SESSION_SIZE and not timed_out,
        "timed_out": timed_out,
        "correct_by_question": correct_displayed,
        "points_breakdown": {
            "base": base_pts,
            "perfect_bonus": perfect_bonus,
            "streak_bonus": streak_bonus,
            "total": total_points,
        },
        "points_awarded": cycle_update["points_awarded"],
        "points_cycle": cycle_update["points_cycle"],
        "streak": new_streak,
        "next_eligible_at": next_eligible_iso,
        "duel_token": duel_token,
        "duel_share_url": duel_share_url,
        "share_text": (
            f"🔥 J'ai scoré {correct_count}/5 au défi quotidien JAPAP "
            f"({total_points} pts, série de {new_streak['current_streak']} jours). "
            f"Sauras-tu faire mieux sur les MÊMES 5 questions ? 👉 {duel_share_url}"
            if duel_share_url else
            f"Je viens de scorer {correct_count}/5 au défi quotidien JAPAP "
            f"({total_points} pts) — série de {new_streak['current_streak']} jours ! "
            f"Joue avec moi sur JAPAP."
        ),
    }


# ─────────────────────────────────────────────────────────────────────
#  Admin category management (iter130, Phase 3.E)
# ─────────────────────────────────────────────────────────────────────

@router.get("/admin/categories")
async def admin_list_categories(request: Request):
    """List all categories with question counts + obsolete counts +
    enable status. Admin only."""
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        rows = await conn.fetch(
            """SELECT q.category,
                      COUNT(*) FILTER (WHERE q.active AND NOT q.obsolete) AS active_count,
                      COUNT(*) FILTER (WHERE q.obsolete) AS obsolete_count,
                      COUNT(*) AS total
                 FROM quiz_questions q
              GROUP BY q.category
              ORDER BY q.category"""
        )
        status_rows = await conn.fetch("SELECT category, enabled, priority FROM quiz_category_status")
        status_by_cat = {r["category"]: r for r in status_rows}
    return {
        "items": [
            {
                "category": r["category"],
                "active_count": int(r["active_count"]),
                "obsolete_count": int(r["obsolete_count"]),
                "total": int(r["total"]),
                "enabled": bool(status_by_cat.get(r["category"], {}).get("enabled", True)),
                "priority": int(status_by_cat.get(r["category"], {}).get("priority", 1)),
            } for r in rows
        ],
    }


class CategoryUpdate(BaseModel):
    enabled: Optional[bool] = None
    priority: Optional[int] = None


@router.put("/admin/categories/{category}")
async def admin_update_category(category: str, body: CategoryUpdate, request: Request):
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    cat = (category or "").strip()[:32]
    if not cat:
        raise HTTPException(status_code=400, detail="Catégorie invalide.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        cur = await conn.fetchrow(
            "SELECT enabled, priority FROM quiz_category_status WHERE category = $1", cat,
        )
        new_enabled = bool(body.enabled) if body.enabled is not None else (cur["enabled"] if cur else True)
        new_priority = max(1, min(int(body.priority), 100)) if body.priority is not None else (cur["priority"] if cur else 1)
        await conn.execute(
            """INSERT INTO quiz_category_status (category, enabled, priority, updated_at)
               VALUES ($1, $2, $3, NOW())
               ON CONFLICT (category) DO UPDATE
                  SET enabled = EXCLUDED.enabled,
                      priority = EXCLUDED.priority,
                      updated_at = NOW()""",
            cat, new_enabled, new_priority,
        )
    return {"category": cat, "enabled": new_enabled, "priority": new_priority}


@router.post("/admin/questions/{qid}/obsolete")
async def admin_mark_obsolete(qid: int, request: Request, obsolete: bool = True):
    """Mark a single question as obsolete (true) or active (false). Useful
    for time-sensitive content (actualité)."""
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.execute(
            "UPDATE quiz_questions SET obsolete = $1, updated_at = NOW() WHERE id = $2",
            bool(obsolete), int(qid),
        )
    return {"id": qid, "obsolete": bool(obsolete), "rows": r}


# iter130 — Admin: AI-generate questions with the configured distribution.
class AIGenerateRequest(BaseModel):
    total: int = Field(default=20, ge=4, le=100)


@router.post("/admin/generate-ai")
async def admin_generate_ai(req: AIGenerateRequest, request: Request):
    """Generate fresh questions via Claude Sonnet 4.5 respecting the
    admin-configured distribution (Africa/Sport/Econ/World percentages).
    Inserts into quiz_questions — the dynamic picker uses them on next /start.

    Cost-aware: 4-7 LLM calls per request (one per sub-category)."""
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    pool = await get_pool()
    from services.quiz_ai_generator import generate_with_distribution
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        try:
            result = await generate_with_distribution(conn, total=req.total)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        except Exception as e:
            logger.exception("[admin/generate-ai] failed")
            raise HTTPException(status_code=500, detail=f"Erreur IA: {e}") from e
    try:
        from services.security_service import log_security_event
        await log_security_event(
            user.get("user_id"), "quiz.admin_generate_ai",
            severity="info", ip="", ua="", details=result,
        )
    except Exception:
        pass
    return {"status": "ok", **result}


# iter130 — Admin: read/update the 4-bucket distribution (must sum to 100).
class DistributionUpdate(BaseModel):
    quiz_dist_africa_pct: int = Field(..., ge=0, le=100)
    quiz_dist_sport_pct:  int = Field(..., ge=0, le=100)
    quiz_dist_econ_pct:   int = Field(..., ge=0, le=100)
    quiz_dist_world_pct:  int = Field(..., ge=0, le=100)


@router.get("/admin/distribution")
async def admin_get_distribution(request: Request):
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    cfg = await _quiz_runtime_config()
    return {
        "quiz_dist_africa_pct": int(cfg.get("quiz_dist_africa_pct", 50)),
        "quiz_dist_sport_pct":  int(cfg.get("quiz_dist_sport_pct",  20)),
        "quiz_dist_econ_pct":   int(cfg.get("quiz_dist_econ_pct",   15)),
        "quiz_dist_world_pct":  int(cfg.get("quiz_dist_world_pct",  15)),
        "quiz_anti_repeat_days": int(cfg.get("quiz_anti_repeat_days", 7)),
    }


@router.put("/admin/distribution")
async def admin_update_distribution(body: DistributionUpdate, request: Request):
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    total = (body.quiz_dist_africa_pct + body.quiz_dist_sport_pct
             + body.quiz_dist_econ_pct + body.quiz_dist_world_pct)
    if total != 100:
        raise HTTPException(
            status_code=400,
            detail=f"La distribution doit totaliser 100% (actuel: {total}).",
        )
    from services.games_settings import update_quiz_config
    try:
        cfg = await update_quiz_config({
            "quiz_dist_africa_pct": body.quiz_dist_africa_pct,
            "quiz_dist_sport_pct":  body.quiz_dist_sport_pct,
            "quiz_dist_econ_pct":   body.quiz_dist_econ_pct,
            "quiz_dist_world_pct":  body.quiz_dist_world_pct,
        }, admin_id=user.get("user_id"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "quiz_dist_africa_pct": int(cfg["quiz_dist_africa_pct"]),
        "quiz_dist_sport_pct":  int(cfg["quiz_dist_sport_pct"]),
        "quiz_dist_econ_pct":   int(cfg["quiz_dist_econ_pct"]),
        "quiz_dist_world_pct":  int(cfg["quiz_dist_world_pct"]),
    }


# ─────────────────────────────────────────────────────────────────────
#  Original endpoints continue below
# ─────────────────────────────────────────────────────────────────────


@router.post("/start", response_model=StartResponse)
async def quiz_start(request: Request):
    await ensure_game_enabled("quiz")
    user = await get_current_user(request)
    pool = await get_pool()
    uid = user["user_id"]
    import asyncio as _asyncio

    # iter234 — Read the requested language from the body (`{"language":"en"}`)
    # or fall back to the `Accept-Language` header. Default 'fr'. The
    # picker handles its own FR fallback if the EN bank is short.
    req_lang = "fr"
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("language"):
            req_lang = str(body["language"])[:2].lower()
    except Exception:
        accept = (request.headers.get("accept-language") or "").lower()
        if accept.startswith("en"):
            req_lang = "en"
    if req_lang not in ("fr", "en"):
        req_lang = "fr"

    async with pool.acquire() as conn:
        await _ensure_ddl(conn)

    # Run the 3 independent reads in parallel — saves ~2 RTTs on Neon (~600ms).
    async def _plays_today():
        async with pool.acquire() as c:
            return await c.fetchval(
                """SELECT COUNT(*) FROM quiz_user_runs r
                   WHERE r.user_id = $1
                     AND r.started_at::date = CURRENT_DATE
                     AND NOT EXISTS (
                         SELECT 1 FROM quiz_daily_challenge_runs d
                          WHERE d.user_id = r.user_id AND d.run_id = r.id
                     )""",
                uid,
            )

    async def _session_count():
        async with pool.acquire() as c:
            return await c.fetchval(
                "SELECT COUNT(*) FROM quiz_questions WHERE active=TRUE AND obsolete=FALSE"
            )

    # iter130 — Sessions are now generated PER USER on demand, excluding
    # questions seen in the last 7 days (anti-repetition + balanced
    # 50/20/15/15 distribution). The pre-built quiz_sessions bank is no
    # longer queried directly here.
    plays_today, count, cfg = await _asyncio.gather(
        _plays_today(), _session_count(), _quiz_runtime_config(),
    )
    # `ensure_game_enabled('quiz')` already returned the legacy 503 for the
    # 'quiz_enabled' setting upstream — no second source-of-truth check
    # here. cfg only drives the gameplay tunables (timer, points, bonus).
    timer_s = int(cfg["quiz_timer_seconds"])
    timer_mode = str(cfg.get("quiz_timer_mode") or "per_question")
    per_q_s = int(cfg.get("quiz_timer_per_question_seconds") or 15)
    max_per_day = int(cfg["quiz_sessions_per_day"])
    auto_advance = bool(cfg.get("quiz_auto_advance_enabled", True))
    auto_advance_ms = int(cfg.get("quiz_auto_advance_delay_ms") or 900)
    auto_advance_delays = list(cfg.get("quiz_auto_advance_delays_ms") or [])

    if int(plays_today or 0) >= max_per_day:
        raise HTTPException(
            status_code=429,
            detail=f"Limite atteinte ({max_per_day} sessions/jour). Revenez demain !",
        )
    if not count:
        raise HTTPException(
            status_code=503,
            detail="Aucune question disponible. L'admin doit lancer la génération IA.",
        )

    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        # iter130 — Build a fresh per-user session (anti-repetition).
        from services.quiz_question_picker import create_session_for_user
        async with conn.transaction():
            session_id, qids, fallback = await create_session_for_user(
                conn, uid, source="quiz_standard", size=SESSION_SIZE,
                language=req_lang,
            )
            if not session_id or len(qids) < SESSION_SIZE:
                raise HTTPException(
                    status_code=503,
                    detail="Banque de questions épuisée. Veuillez réessayer plus tard.",
                )
        if fallback:
            logger.warning("[quiz/start] fallback exclusion used for user=%s", uid)
        # Re-fetch in the same connection to avoid a second pool round-trip.
        q_rows = await conn.fetch(
            "SELECT id, text, options, category FROM quiz_questions WHERE id = ANY($1::bigint[])",
            qids,
        )
        # Rebuild a minimal `session`-like dict for downstream code.
        session = {"id": session_id, "question_ids": qids}
        # Preserve the session order + shuffle options per question to eliminate
        # any positional bias in the bank (defense against pattern-learning
        # and AI-generated skew). The permutation is persisted so /submit can
        # map the displayed answer back to the original correct index.
        by_id = {r["id"]: r for r in q_rows}
        questions = []
        options_order: List[List[int]] = []
        for qid in session["question_ids"]:
            q = by_id.get(qid)
            if not q:
                continue
            original = _parse_options(q["options"])
            # Random 0..3 permutation
            perm = [0, 1, 2, 3]
            random.shuffle(perm)
            shuffled = [original[i] for i in perm]
            options_order.append(perm)
            questions.append({
                "id": int(q["id"]),
                "text": q["text"],
                "options": shuffled,
                "category": q["category"],
            })
        if len(questions) < SESSION_SIZE:
            raise HTTPException(status_code=500, detail="Session corrompue — veuillez réessayer.")

        # Create the run (started_at is the hard clock) — we also lock the
        # admin-configured timer at start time so a mid-session change cannot
        # retroactively invalidate an in-flight session.
        # iter118: when timer_mode='per_question', we store the EFFECTIVE total
        # = N_questions × per_question_seconds so the server-side timeout
        # check honours the per-question budget.
        effective_total_s = (
            per_q_s * SESSION_SIZE if timer_mode == "per_question" else timer_s
        )
        now = datetime.now(timezone.utc)
        run_id = await conn.fetchval(
            """INSERT INTO quiz_user_runs
                 (user_id, session_id, started_at, options_order, time_limit_s)
               VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id""",
            uid, session["id"], now, _json.dumps(options_order), effective_total_s,
        )

    return StartResponse(
        run_id=int(run_id),
        session_id=int(session["id"]),
        time_limit_seconds=effective_total_s,
        timer_mode=timer_mode,
        timer_per_question_seconds=per_q_s,
        auto_advance_enabled=auto_advance,
        auto_advance_delay_ms=auto_advance_ms,
        auto_advance_delays_ms=auto_advance_delays,
        questions=questions,
    )


@router.post("/submit")
async def quiz_submit(req: SubmitRequest, request: Request):
    await ensure_game_enabled("quiz")
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                """SELECT * FROM quiz_user_runs WHERE id = $1 AND user_id = $2 FOR UPDATE""",
                req.run_id, user["user_id"],
            )
            if not run:
                raise HTTPException(status_code=404, detail="Run introuvable.")
            if run["submitted_at"]:
                raise HTTPException(status_code=400, detail="Session déjà soumise.")

            # Hard timeout check (server-side)
            now = datetime.now(timezone.utc)
            elapsed = (now - run["started_at"]).total_seconds()
            # Honour the timer captured at /start (so an admin mid-session change
            # cannot retroactively invalidate an in-flight session).
            effective_limit = int(run["time_limit_s"] or SESSION_TIME_LIMIT_SECONDS)
            timed_out = elapsed > (effective_limit + SESSION_TIME_NETWORK_GRACE_SECONDS)

            # Resolve the correct answers + per-question permutation
            session = await conn.fetchrow(
                "SELECT question_ids FROM quiz_sessions WHERE id = $1",
                run["session_id"],
            )
            q_rows = await conn.fetch(
                "SELECT id, correct_index FROM quiz_questions WHERE id = ANY($1::bigint[])",
                list(session["question_ids"]),
            )
            correct_map = {int(r["id"]): int(r["correct_index"]) for r in q_rows}
            order = [int(qid) for qid in session["question_ids"]]
            # Options permutations stored at /start. Back-compat : if missing,
            # treat each question as identity (legacy runs).
            raw_perm = run["options_order"]
            if isinstance(raw_perm, str):
                try:
                    perms = _json.loads(raw_perm)
                except (ValueError, TypeError):
                    perms = []
            else:
                perms = raw_perm or []
            while len(perms) < SESSION_SIZE:
                perms.append([0, 1, 2, 3])

            # If timed out, force the submitted answers into -1 (not answered)
            answers = list(req.answers)
            if timed_out:
                # Keep only the answers given before the timeout — caller is expected
                # to not send -1 unless intentional; we still count correctness fairly
                pass

            correct_count = 0
            # correct_by_question_displayed: the displayed (shuffled) index of
            # the correct answer for each question — safe to show after submit
            # so the UI can highlight the right option.
            correct_displayed: List[int] = []
            for i, qid in enumerate(order):
                original_correct = correct_map.get(qid, -1)
                perm = perms[i] if i < len(perms) else [0, 1, 2, 3]
                try:
                    displayed_correct = perm.index(original_correct) if original_correct >= 0 else -1
                except ValueError:
                    displayed_correct = -1
                correct_displayed.append(displayed_correct)
                given = answers[i] if i < len(answers) else -1
                # User sends a displayed index; resolve to original via perm
                given_original = perm[given] if 0 <= given < len(perm) else -1
                if given_original >= 0 and given_original == original_correct:
                    correct_count += 1

            # Points awarded by backend (never frontend) — settings driven.
            cfg_now = await _quiz_runtime_config()
            points = correct_count * int(cfg_now["quiz_points_per_correct"])
            if correct_count == SESSION_SIZE and not timed_out:
                points += int(cfg_now["quiz_perfect_bonus"])

            # Persist the run
            await conn.execute(
                """UPDATE quiz_user_runs
                     SET submitted_at = $1, answers = $2, correct_count = $3,
                         points_awarded = $4, timed_out = $5
                   WHERE id = $6""",
                now, answers, correct_count, points, timed_out, req.run_id,
            )

            # Register quiz accuracy on the active cycle
            await register_quiz_answers(conn, user["user_id"], correct_count, SESSION_SIZE)

            # Add points (respects sovereign clamp)
            cycle_update = await add_points(
                conn, user["user_id"], points, source="quiz",
                metadata={
                    "ip": request.client.host if request.client else "",
                    "ua": (request.headers.get("user-agent") or "")[:256],
                },
            )

    return {
        "run_id": req.run_id,
        "correct_count": correct_count,
        "total": SESSION_SIZE,
        "accuracy": round(correct_count / SESSION_SIZE, 2),
        "points_awarded": cycle_update["points_awarded"],
        "points_cycle": cycle_update["points_cycle"],
        "perfect": correct_count == SESSION_SIZE and not timed_out,
        "timed_out": timed_out,
        "correct_by_question": correct_displayed,  # DISPLAYED index — shown AFTER submit
    }


@router.get("/history")
async def quiz_history(request: Request, limit: int = 20):
    user = await get_current_user(request)
    limit = max(1, min(int(limit), 50))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        rows = await conn.fetch(
            """SELECT id, session_id, started_at, submitted_at,
                      correct_count, points_awarded, timed_out
               FROM quiz_user_runs WHERE user_id = $1
               ORDER BY started_at DESC LIMIT $2""",
            user["user_id"], limit,
        )
    return {
        "items": [
            {
                "run_id": int(r["id"]),
                "session_id": int(r["session_id"]),
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
                "correct_count": int(r["correct_count"] or 0),
                "points_awarded": int(r["points_awarded"] or 0),
                "timed_out": bool(r["timed_out"]),
            }
            for r in rows
        ],
    }


# iter120 — POST /answer : reveal correct/incorrect for live UX feedback.
# Stateless on purpose. The frontend sends one call per user click; the
# backend looks up the question's true correct_index and applies the same
# per-question shuffle that was committed at /start. Returns ONLY a bool.
@router.post("/answer", response_model=AnswerRevealResponse)
async def reveal_answer(req: AnswerRevealRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                """SELECT user_id, session_id, options_order, started_at,
                          submitted_at, time_limit_s, revealed_options
                     FROM quiz_user_runs WHERE id = $1 FOR UPDATE""",
                req.run_id,
            )
            if not run:
                raise HTTPException(status_code=404, detail="Run introuvable")
            if run["user_id"] != user["user_id"]:
                raise HTTPException(status_code=403, detail="Run d'un autre utilisateur")
            if run["submitted_at"] is not None:
                raise HTTPException(status_code=409, detail="Run déjà soumis")
            # Light timing guard: don't reveal answers after the lock-in period.
            elapsed = (datetime.now(timezone.utc) - run["started_at"]).total_seconds()
            max_elapsed = int(run["time_limit_s"]) + SESSION_TIME_NETWORK_GRACE_SECONDS
            if elapsed > max_elapsed:
                raise HTTPException(status_code=410, detail="Run expiré")

            session = await conn.fetchrow(
                "SELECT question_ids FROM quiz_sessions WHERE id = $1",
                run["session_id"],
            )
            order = list(session["question_ids"])
            if req.question_idx >= len(order):
                raise HTTPException(status_code=400, detail="question_idx hors plage")

            # iter120 — Anti-brute-force lock: store the first selected_option
            # per question_idx. Subsequent calls with a DIFFERENT option are
            # refused (409) — closes the vector where a malicious client tries
            # 0..3 to discover the correct answer before /submit.
            raw_revealed = run["revealed_options"]
            if isinstance(raw_revealed, str):
                try:
                    revealed = _json.loads(raw_revealed)
                except (ValueError, TypeError):
                    revealed = {}
            elif isinstance(raw_revealed, dict):
                revealed = raw_revealed
            else:
                revealed = {}
            qkey = str(req.question_idx)
            prior = revealed.get(qkey)
            if prior is not None and int(prior) != int(req.selected_option):
                raise HTTPException(
                    status_code=409,
                    detail="Cette question a déjà été révélée avec une autre réponse.",
                )

            qid = int(order[req.question_idx])
            q = await conn.fetchrow(
                "SELECT correct_index FROM quiz_questions WHERE id = $1", qid)
            if not q:
                raise HTTPException(status_code=404, detail="Question introuvable")

            raw_perm = run["options_order"]
            if isinstance(raw_perm, str):
                try:
                    perms = _json.loads(raw_perm)
                except (ValueError, TypeError):
                    perms = []
            else:
                perms = raw_perm or []
            perm = perms[req.question_idx] if req.question_idx < len(perms) else [0, 1, 2, 3]
            try:
                given_original = perm[req.selected_option]
            except (IndexError, TypeError):
                given_original = -1
            is_correct = given_original == int(q["correct_index"])

            # Persist the lock if this is the first reveal.
            if prior is None:
                revealed[qkey] = int(req.selected_option)
                await conn.execute(
                    "UPDATE quiz_user_runs SET revealed_options = $1::jsonb WHERE id = $2",
                    _json.dumps(revealed), req.run_id,
                )

            # iter122 — Learning mode: if the answer is wrong AND admin
            # enabled `quiz_show_correct_after_wrong`, expose the DISPLAYED
            # correct option index. Safe to expose because the user has
            # already locked in their choice (revealed_options is set).
            correct_option_displayed: Optional[int] = None
            if not is_correct:
                cfg = await _quiz_runtime_config()
                if bool(cfg.get("quiz_show_correct_after_wrong", False)):
                    try:
                        correct_option_displayed = perm.index(int(q["correct_index"]))
                    except (ValueError, IndexError, TypeError):
                        correct_option_displayed = None

            return AnswerRevealResponse(correct=bool(is_correct),
                                         question_idx=req.question_idx,
                                         correct_option=correct_option_displayed)




class QuestionCreate(BaseModel):
    text: str = Field(..., min_length=5, max_length=500)
    options: List[str] = Field(..., min_length=4, max_length=4)
    correct_index: int = Field(..., ge=0, le=3)
    category: str
    difficulty: str = "medium"
    active: bool = True


class QuestionUpdate(BaseModel):
    text: Optional[str] = Field(default=None, min_length=5, max_length=500)
    options: Optional[List[str]] = Field(default=None, min_length=4, max_length=4)
    correct_index: Optional[int] = Field(default=None, ge=0, le=3)
    category: Optional[str] = None
    difficulty: Optional[str] = None
    active: Optional[bool] = None


@router.get("/admin/questions")
async def admin_list_questions(
    request: Request, category: str = "", active: str = "all",
    limit: int = 50, offset: int = 0,
):
    from routes.auth import require_admin as _ra
    await _ra(request)
    limit = max(1, min(int(limit), 500))
    # Validate category against allowlist to eliminate any SQL injection surface.
    if category and category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category must be one of {CATEGORIES}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        where = ["1=1"]
        params: list = []
        if category:
            params.append(category)
            where.append(f"category = ${len(params)}")
        if active == "yes":
            where.append("active = TRUE")
        elif active == "no":
            where.append("active = FALSE")
        where_sql = " AND ".join(where)
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM quiz_questions WHERE {where_sql}", *params,
        )
        params_with_limit = [*params, limit, offset]
        rows = await conn.fetch(
            f"""SELECT id, text, options, correct_index, category, difficulty,
                       source, active, created_at
                  FROM quiz_questions
                 WHERE {where_sql}
              ORDER BY id DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}""",
            *params_with_limit,
        )
    return {
        "total": int(total or 0), "limit": limit, "offset": offset,
        "items": [
            {
                "id": int(r["id"]), "text": r["text"],
                "options": _parse_options(r["options"]),
                "correct_index": int(r["correct_index"]),
                "category": r["category"], "difficulty": r["difficulty"],
                "source": r["source"], "active": bool(r["active"]),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            } for r in rows
        ],
    }


@router.post("/admin/questions")
async def admin_create_question(req: QuestionCreate, request: Request):
    from routes.auth import require_admin as _ra
    admin = await _ra(request)
    if req.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category must be one of {CATEGORIES}")
    if req.difficulty not in DIFFICULTIES:
        raise HTTPException(status_code=400, detail=f"difficulty must be one of {DIFFICULTIES}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        qid = await conn.fetchval(
            """INSERT INTO quiz_questions
                 (text, options, correct_index, category, difficulty, source, active, created_by)
               VALUES ($1,$2::jsonb,$3,$4,$5,'admin',$6,$7) RETURNING id""",
            req.text, _json.dumps(req.options), req.correct_index,
            req.category, req.difficulty, req.active, admin.get("user_id"),
        )
    return {"status": "ok", "id": int(qid)}


@router.put("/admin/questions/{qid}")
async def admin_update_question(qid: int, req: QuestionUpdate, request: Request):
    from routes.auth import require_admin as _ra
    await _ra(request)
    pool = await get_pool()
    fields, vals = [], []
    for k, v in req.model_dump(exclude_none=True).items():
        if k == "options":
            fields.append(f"options = ${len(vals)+1}::jsonb")
            vals.append(_json.dumps(v))
        else:
            fields.append(f"{k} = ${len(vals)+1}")
            vals.append(v)
    if not fields:
        return {"status": "ok", "updated": []}
    fields.append("updated_at = NOW()")
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        vals.append(qid)
        r = await conn.execute(
            f"UPDATE quiz_questions SET {', '.join(fields)} WHERE id = ${len(vals)}",
            *vals,
        )
    return {"status": "ok", "raw": r}


@router.delete("/admin/questions/{qid}")
async def admin_delete_question(qid: int, request: Request):
    from routes.auth import require_admin as _ra
    await _ra(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        await conn.execute("DELETE FROM quiz_questions WHERE id = $1", qid)
    return {"status": "ok"}


@router.get("/admin/overview")
async def admin_overview(request: Request, days: int = 30):
    """Compact observability dashboard for Quiz : totals, accuracy distribution,
    top players, runs per day (for sparkline)."""
    from routes.auth import require_admin as _ra
    await _ra(request)
    days = max(1, min(int(days), 90))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        # Totals over the window
        totals = await conn.fetchrow(
            """SELECT COUNT(*)                 AS runs,
                      COUNT(DISTINCT user_id)  AS players,
                      COALESCE(SUM(points_awarded), 0) AS points,
                      COALESCE(SUM(correct_count), 0)  AS correct_total,
                      COUNT(*)                  * 5   AS answers_total,
                      COALESCE(AVG(points_awarded), 0)::int AS avg_points,
                      COALESCE(
                        AVG(EXTRACT(EPOCH FROM (submitted_at - started_at))), 0
                      )::numeric(6,2) AS avg_duration_s,
                      COUNT(*) FILTER (WHERE timed_out) AS timed_out_runs
               FROM quiz_user_runs
               WHERE submitted_at IS NOT NULL
                 AND started_at >= NOW() - ($1 || ' days')::interval""",
            str(days),
        )
        runs = int(totals["runs"] or 0)
        answers_total = int(totals["answers_total"] or 0)
        correct_total = int(totals["correct_total"] or 0)
        avg_accuracy = (correct_total / answers_total) if answers_total else 0.0
        # Accuracy bucket distribution
        buckets = await conn.fetch(
            """SELECT correct_count, COUNT(*) AS n
               FROM quiz_user_runs
               WHERE submitted_at IS NOT NULL
                 AND started_at >= NOW() - ($1 || ' days')::interval
               GROUP BY correct_count ORDER BY correct_count""",
            str(days),
        )
        # Top players (most points)
        top = await conn.fetch(
            """SELECT r.user_id,
                      COUNT(*)              AS runs,
                      SUM(r.points_awarded) AS points,
                      SUM(r.correct_count)  AS correct,
                      COUNT(*)*5            AS answers,
                      u.first_name, u.last_name, u.email, u.avatar
               FROM quiz_user_runs r
               LEFT JOIN users u ON u.user_id = r.user_id
               WHERE r.submitted_at IS NOT NULL
                 AND r.started_at >= NOW() - ($1 || ' days')::interval
               GROUP BY r.user_id, u.first_name, u.last_name, u.email, u.avatar
               ORDER BY points DESC LIMIT 10""",
            str(days),
        )
        # Daily timeseries for sparkline
        ts = await conn.fetch(
            """SELECT started_at::date AS day, COUNT(*) AS runs,
                      COALESCE(SUM(points_awarded),0) AS points
               FROM quiz_user_runs
               WHERE submitted_at IS NOT NULL
                 AND started_at >= NOW() - ($1 || ' days')::interval
               GROUP BY 1 ORDER BY 1""",
            str(days),
        )
        # Bank health (active/inactive)
        bank = await conn.fetchrow(
            """SELECT COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE active) AS active_qs,
                      COUNT(*) FILTER (WHERE NOT active) AS inactive_qs
               FROM quiz_questions"""
        )
        sessions_count = int(await conn.fetchval("SELECT COUNT(*) FROM quiz_sessions") or 0)

    return {
        "window_days": days,
        "runs": runs,
        "players": int(totals["players"] or 0),
        "points_distributed": int(totals["points"] or 0),
        "avg_accuracy": round(avg_accuracy, 4),
        "avg_points_per_run": int(totals["avg_points"] or 0),
        "avg_duration_seconds": float(totals["avg_duration_s"] or 0),
        "timed_out_runs": int(totals["timed_out_runs"] or 0),
        "buckets": [{"score": int(b["correct_count"] or 0), "n": int(b["n"])} for b in buckets],
        "top_players": [
            {
                "user_id": r["user_id"],
                "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["email"] or r["user_id"],
                "email": r["email"],
                "avatar": r["avatar"],
                "runs": int(r["runs"]),
                "points": int(r["points"] or 0),
                "correct": int(r["correct"] or 0),
                "answers": int(r["answers"] or 0),
                "accuracy": round(
                    (int(r["correct"] or 0) / max(1, int(r["answers"] or 0))), 4,
                ),
            } for r in top
        ],
        "timeseries": [
            {"day": t["day"].isoformat(), "runs": int(t["runs"]), "points": int(t["points"] or 0)}
            for t in ts
        ],
        "bank": {
            "total": int(bank["total"] or 0),
            "active": int(bank["active_qs"] or 0),
            "inactive": int(bank["inactive_qs"] or 0),
            "sessions": sessions_count,
        },
    }


@router.post("/admin/reset-user/{target_user_id}")
async def admin_reset_user(target_user_id: str, request: Request):
    """Wipe a user's quiz history (keeps the bank intact). Use with care —
    intended for test accounts or after confirmed abuse."""
    from routes.auth import require_admin as _ra
    admin = await _ra(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        deleted = await conn.execute(
            "DELETE FROM quiz_user_runs WHERE user_id = $1", target_user_id,
        )
    try:
        from services.security_service import log_security_event
        await log_security_event(
            admin.get("user_id"), "quiz.admin_reset_user",
            severity="warning", ip="", ua="",
            details={"target_user_id": target_user_id, "raw": deleted},
        )
    except Exception:
        pass
    return {"status": "ok", "raw": deleted}


@router.post("/admin/regenerate-ai")
async def admin_regenerate_ai(request: Request, target_sessions: int = 100):
    """Generate `target_sessions` × 5Q using Claude Sonnet 4.5 and rebuild
    the quiz_sessions table. Safe to re-run (admin kills the old bank first
    only if a force flag is passed — by default we ADD to the bank)."""
    from routes.auth import require_admin as _ra
    admin = await _ra(request)
    target_sessions = max(10, min(int(target_sessions), 500))

    from scripts.seed_quiz_ai import run_seed
    try:
        result = await run_seed(target_sessions=target_sessions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Seed failed: {e}") from e
    # Audit log
    try:
        from services.security_service import log_security_event
        await log_security_event(
            admin.get("user_id"), "quiz.admin_regenerate_ai",
            severity="info", ip="", ua="",
            details=result,
        )
    except Exception:
        pass
    return {"status": "ok", **result}
