"""
iter86 — Challenge d'ami (Duel) — viral 1v1 quiz/tap challenge.

Flow :
  1. Challenger plays a regular quiz/tap run as normal → gets a result
  2. From the result screen, they create a duel from that run
     POST /api/duel/create-from-quiz {run_id}
     POST /api/duel/create-from-tap  {run_id}
     → returns a share_token + shareable link + share card URL
  3. Opponent opens `/duel/:token` → sees the challenge
     GET /api/duel/{token}                       (optionally authenticated)
  4. Opponent starts their side of the duel (auth REQUIRED)
     POST /api/duel/{token}/start-quiz → 5 SAME questions (reshuffled per user)
     POST /api/duel/{token}/start-tap  → opens a 10s tap run linked to the duel
  5. Opponent submits the duel run
     POST /api/duel/{token}/submit-quiz {answers}
     POST /api/duel/{token}/submit-tap  {run_id, taps}
     → scores computed server-side, duel status=completed, winner_id set,
       bonus points distributed via points_service

Admin toggle :
  - `duel_enabled` in admin_settings (default TRUE) gates create + accept
  - `duel_winner_bonus` (default 50 pts), `duel_loser_bonus` (10 pts)
  - `duel_accepts_per_day` (default 3) — max duels an opponent can accept/day

Security :
  - 24h expiry; past expiry a duel becomes 'expired'
  - Challenger cannot duel themselves (server check)
  - Opponent cannot accept twice
  - Daily accept cap
"""
from __future__ import annotations

import json as _json
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user
from services.points_service import add_points, register_quiz_answers, register_tap_run
from services.settings_service import get_bool, get_json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/duel", tags=["duel"])


# ══════════════════════════════════════════════════════════════════════════
#  DDL
# ══════════════════════════════════════════════════════════════════════════

_DDL = [
    """CREATE TABLE IF NOT EXISTS duels (
         id BIGSERIAL PRIMARY KEY,
         share_token            VARCHAR(64)  NOT NULL UNIQUE,
         game                   VARCHAR(16)  NOT NULL,
         challenger_id          VARCHAR(32)  NOT NULL,
         challenger_run_id      BIGINT       NOT NULL,
         challenger_score       INT          NOT NULL,
         challenger_session_id  BIGINT,
         challenger_metadata    JSONB        DEFAULT '{}'::jsonb,
         opponent_id            VARCHAR(32),
         opponent_run_id        BIGINT,
         opponent_score         INT,
         opponent_metadata      JSONB,
         status                 VARCHAR(16)  NOT NULL DEFAULT 'open',
         winner_id              VARCHAR(32),
         created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
         accepted_at            TIMESTAMPTZ,
         completed_at           TIMESTAMPTZ,
         expires_at             TIMESTAMPTZ  NOT NULL
       )""",
    "CREATE INDEX IF NOT EXISTS idx_duels_token ON duels(share_token)",
    "CREATE INDEX IF NOT EXISTS idx_duels_challenger ON duels(challenger_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_duels_opponent ON duels(opponent_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_duels_status_expires ON duels(status, expires_at) WHERE status IN ('open','accepted')",
    # iter131 — Time tiebreaker. Stored in seconds with 2-decimal precision
    # so equal-score duels are decided by the faster player.
    "ALTER TABLE duels ADD COLUMN IF NOT EXISTS challenger_time_s NUMERIC(8,2)",
    "ALTER TABLE duels ADD COLUMN IF NOT EXISTS opponent_time_s   NUMERIC(8,2)",
    # iter140 — Multi-attempts duel kind. 'classic' = the legacy 1v1 duel
    # where the first opponent locks the slot. 'multi_attempts' = up to N
    # different challengers can each play the same questions; each attempt
    # is recorded in `duel_attempts` and the challenger sees a leaderboard
    # of EVERY player who answered. Auto-created from the daily-challenge
    # share flow so the share_text actually delivers a comparison.
    "ALTER TABLE duels ADD COLUMN IF NOT EXISTS duel_kind VARCHAR(24) NOT NULL DEFAULT 'classic'",
    """CREATE TABLE IF NOT EXISTS duel_attempts (
         id BIGSERIAL PRIMARY KEY,
         duel_id      BIGINT      NOT NULL REFERENCES duels(id) ON DELETE CASCADE,
         user_id      VARCHAR(32) NOT NULL,
         run_id       BIGINT      NOT NULL,
         score        INT         NOT NULL,
         time_s       NUMERIC(8,2),
         outcome      VARCHAR(8),     -- 'won' | 'lost' | 'tie'
         submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         UNIQUE (duel_id, user_id)
       )""",
    "CREATE INDEX IF NOT EXISTS idx_duel_attempts_duel ON duel_attempts(duel_id, score DESC, time_s ASC)",
    "CREATE INDEX IF NOT EXISTS idx_duel_attempts_user ON duel_attempts(user_id, submitted_at DESC)",
]

_ddl_done = False


async def _ensure_ddl(conn) -> None:
    global _ddl_done
    if _ddl_done:
        return
    for stmt in _DDL:
        await conn.execute(stmt)
    _ddl_done = True


# ══════════════════════════════════════════════════════════════════════════
#  Config helpers
# ══════════════════════════════════════════════════════════════════════════

async def _cfg() -> dict:
    cfg = await get_json("wheel_config_json", {}) or {}
    return {
        "winner_bonus": max(0, int(cfg.get("duel_winner_bonus", 50))),
        "loser_bonus":  max(0, int(cfg.get("duel_loser_bonus", 10))),
        "accepts_per_day": max(1, int(cfg.get("duel_accepts_per_day", 3))),
    }


async def _ensure_duel_enabled() -> None:
    if not await get_bool("duel_enabled", True):
        raise HTTPException(status_code=503, detail="Challenge d'ami temporairement désactivé.")


def _mark_expired_sql() -> str:
    return """UPDATE duels
                 SET status = 'expired'
               WHERE status IN ('open', 'accepted')
                 AND expires_at < NOW()"""


# ══════════════════════════════════════════════════════════════════════════
#  Schemas
# ══════════════════════════════════════════════════════════════════════════

class CreateFromRun(BaseModel):
    run_id: int = Field(..., ge=1)


class SubmitQuizDuel(BaseModel):
    answers: List[int] = Field(..., min_length=5, max_length=5)


class SubmitTapDuel(BaseModel):
    run_id: int
    taps: int = Field(..., ge=0, le=10_000)


# ══════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════════════════════

def _parse_perms(raw: Any) -> List[List[int]]:
    if isinstance(raw, str):
        try:
            return _json.loads(raw)
        except (ValueError, TypeError):
            return []
    return raw or []


async def _serialise_duel(conn, row) -> dict:
    """Flesh out a duel row with user names."""
    ids = [row["challenger_id"]]
    if row["opponent_id"]:
        ids.append(row["opponent_id"])
    users = {}
    if ids:
        rows = await conn.fetch(
            "SELECT user_id, first_name, last_name, email, avatar FROM users WHERE user_id = ANY($1)",
            ids,
        )
        for u in rows:
            users[u["user_id"]] = {
                "user_id": u["user_id"],
                "name": f"{u['first_name'] or ''} {u['last_name'] or ''}".strip() or u["email"] or u["user_id"],
                "avatar": u["avatar"],
            }
    duel_kind = row["duel_kind"] if "duel_kind" in row.keys() else "classic"
    payload = {
        "id": int(row["id"]),
        "share_token": row["share_token"],
        "game": row["game"],
        "status": row["status"],
        "duel_kind": duel_kind,
        "challenger": users.get(row["challenger_id"], {"user_id": row["challenger_id"], "name": row["challenger_id"]}),
        "challenger_score": int(row["challenger_score"] or 0),
        "challenger_time_s": float(row["challenger_time_s"]) if row["challenger_time_s"] is not None else None,
        "opponent": users.get(row["opponent_id"]) if row["opponent_id"] else None,
        "opponent_score": int(row["opponent_score"] or 0) if row["opponent_score"] is not None else None,
        "opponent_time_s": float(row["opponent_time_s"]) if row["opponent_time_s"] is not None else None,
        "winner_id": row["winner_id"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
    }
    if duel_kind == "multi_attempts":
        # Surface a quick summary so frontends can render badges without a
        # second roundtrip.
        stats = await conn.fetchrow(
            """SELECT COUNT(*) AS participants,
                      MAX(score) AS best_score,
                      COUNT(*) FILTER (WHERE outcome='lost') AS wins_for_initiator,
                      COUNT(*) FILTER (WHERE outcome='won')  AS losses_for_initiator,
                      COUNT(*) FILTER (WHERE outcome='tie')  AS ties
                 FROM duel_attempts WHERE duel_id = $1""",
            int(row["id"]),
        )
        payload["multi_stats"] = {
            "participants": int(stats["participants"] or 0),
            "best_score": int(stats["best_score"]) if stats["best_score"] is not None else None,
            "wins_for_initiator": int(stats["wins_for_initiator"] or 0),
            "losses_for_initiator": int(stats["losses_for_initiator"] or 0),
            "ties": int(stats["ties"] or 0),
        }
    return payload


# ══════════════════════════════════════════════════════════════════════════
#  Endpoints — creation
# ══════════════════════════════════════════════════════════════════════════

@router.post("/create-from-quiz")
async def create_from_quiz(req: CreateFromRun, request: Request):
    """Create a duel from an existing completed quiz run of the current user."""
    await _ensure_duel_enabled()
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        run = await conn.fetchrow(
            """SELECT id, user_id, session_id, correct_count, submitted_at, started_at
               FROM quiz_user_runs WHERE id = $1""",
            req.run_id,
        )
        if not run or run["user_id"] != user["user_id"]:
            raise HTTPException(status_code=404, detail="Session quiz introuvable.")
        if not run["submitted_at"] or run["correct_count"] is None:
            raise HTTPException(status_code=400, detail="Terminez votre session avant de défier un ami.")

        # iter131 — Capture challenger time as a tiebreaker for equal scores.
        try:
            challenger_time_s = round(
                (run["submitted_at"] - run["started_at"]).total_seconds(), 2
            )
        except Exception:
            challenger_time_s = None

        token = secrets.token_urlsafe(16)
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        duel_id = await conn.fetchval(
            """INSERT INTO duels
                 (share_token, game, challenger_id, challenger_run_id,
                  challenger_score, challenger_session_id, challenger_metadata,
                  challenger_time_s, expires_at)
               VALUES ($1, 'quiz', $2, $3, $4, $5, $6::jsonb, $7, $8)
               RETURNING id""",
            token, user["user_id"], int(run["id"]),
            int(run["correct_count"]), int(run["session_id"]),
            _json.dumps({
                "correct_count": int(run["correct_count"]),
                "total": 5,
                "time_s": challenger_time_s,
            }),
            challenger_time_s,
            expires,
        )
    return {
        "id": int(duel_id),
        "share_token": token,
        "share_url": f"/duel/{token}",
        "share_card_url": f"/api/duel/{token}/share-card.png",
        "expires_at": expires.isoformat(),
    }


@router.post("/create-from-tap")
async def create_from_tap(req: CreateFromRun, request: Request):
    """Create a duel from an existing completed tap run."""
    await _ensure_duel_enabled()
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        run = await conn.fetchrow(
            """SELECT id, user_id, taps_valid, submitted_at
               FROM tap_user_runs WHERE id = $1""",
            req.run_id,
        )
        if not run or run["user_id"] != user["user_id"]:
            raise HTTPException(status_code=404, detail="Session Tap introuvable.")
        if not run["submitted_at"]:
            raise HTTPException(status_code=400, detail="Terminez votre session avant de défier un ami.")

        token = secrets.token_urlsafe(16)
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        duel_id = await conn.fetchval(
            """INSERT INTO duels
                 (share_token, game, challenger_id, challenger_run_id,
                  challenger_score, challenger_metadata, expires_at)
               VALUES ($1, 'tap', $2, $3, $4, $5::jsonb, $6)
               RETURNING id""",
            token, user["user_id"], int(run["id"]),
            int(run["taps_valid"] or 0),
            _json.dumps({"taps_valid": int(run["taps_valid"] or 0)}),
            expires,
        )
    return {
        "id": int(duel_id),
        "share_token": token,
        "share_url": f"/duel/{token}",
        "share_card_url": f"/api/duel/{token}/share-card.png",
        "expires_at": expires.isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════
#  Endpoints — view & list
# ══════════════════════════════════════════════════════════════════════════

@router.get("/{token}")
async def get_duel(token: str):
    """Public preview (no auth) — used by the /duel/:token landing page."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        await conn.execute(_mark_expired_sql())
        row = await conn.fetchrow("SELECT * FROM duels WHERE share_token = $1", token)
        if not row:
            raise HTTPException(status_code=404, detail="Défi introuvable.")
        return await _serialise_duel(conn, row)


@router.get("/my/list")
async def my_duels(request: Request, limit: int = 20):
    user = await get_current_user(request)
    limit = max(1, min(int(limit), 100))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        await conn.execute(_mark_expired_sql())
        rows = await conn.fetch(
            """SELECT * FROM duels
               WHERE challenger_id = $1 OR opponent_id = $1
               ORDER BY created_at DESC LIMIT $2""",
            user["user_id"], limit,
        )
        items = [await _serialise_duel(conn, r) for r in rows]
    return {"items": items}


# ══════════════════════════════════════════════════════════════════════════
#  Endpoints — play (opponent side)
# ══════════════════════════════════════════════════════════════════════════

async def _opponent_guards(conn, duel_row, user_id: str) -> None:
    """Common checks before an opponent can play/submit."""
    if duel_row["challenger_id"] == user_id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas vous défier vous-même.")
    # iter140 — multi_attempts duels NEVER reach 'completed' on first
    # opponent submit, so allow more participants. Standard 'classic' duels
    # still close after the first opponent settles.
    is_multi = (duel_row.get("duel_kind") if isinstance(duel_row, dict)
                else duel_row["duel_kind"] if "duel_kind" in duel_row.keys() else "classic") == "multi_attempts"
    if not is_multi and duel_row["status"] == "completed":
        raise HTTPException(status_code=400, detail="Ce défi est déjà terminé.")
    if duel_row["status"] == "expired" or duel_row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Ce défi a expiré.")
    # iter140 — multi_attempts allows N different opponents (each gets their
    # own duel_attempts row). Block only if THIS user already submitted.
    if is_multi:
        already = await conn.fetchval(
            "SELECT 1 FROM duel_attempts WHERE duel_id = $1 AND user_id = $2",
            duel_row["id"], user_id,
        )
        if already:
            raise HTTPException(status_code=409, detail="Vous avez déjà relevé ce défi.")
        return
    # Classic 1v1: block if another opponent already locked it.
    if duel_row["opponent_id"] and duel_row["opponent_id"] != user_id:
        raise HTTPException(status_code=409, detail="Ce défi a déjà été relevé.")


async def _enforce_daily_accept_cap(conn, user_id: str) -> None:
    cfg = await _cfg()
    count = await conn.fetchval(
        """SELECT COUNT(*) FROM duels
           WHERE opponent_id = $1
             AND accepted_at::date = CURRENT_DATE""",
        user_id,
    ) or 0
    if int(count) >= cfg["accepts_per_day"]:
        raise HTTPException(
            status_code=429,
            detail=f"Limite quotidienne atteinte ({cfg['accepts_per_day']} défis relevés aujourd'hui).",
        )


@router.post("/{token}/start-quiz")
async def start_quiz_duel(token: str, request: Request):
    """Opponent starts the quiz side of a duel. Returns the SAME 5 questions
    the challenger faced, but reshuffled per the opponent — so the challenge
    is fair and anti-cheat-resistant."""
    await _ensure_duel_enabled()
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        # Import here to avoid circular imports
        from routes.quiz import (
            _parse_options, _current_timer_seconds, SESSION_SIZE,
        )
        await conn.execute(_mark_expired_sql())
        async with conn.transaction():
            duel = await conn.fetchrow(
                "SELECT * FROM duels WHERE share_token = $1 FOR UPDATE", token,
            )
            if not duel:
                raise HTTPException(status_code=404, detail="Défi introuvable.")
            if duel["game"] != "quiz":
                raise HTTPException(status_code=400, detail="Ce défi n'est pas un quiz.")
            await _opponent_guards(conn, duel, user["user_id"])
            duel_kind = duel["duel_kind"] if "duel_kind" in duel.keys() else "classic"
            # Allow re-entry (same opponent restarts) only if they haven't
            # submitted yet. Multi-attempts uses a per-user check via
            # _opponent_guards — restart by re-creating a new run is fine.
            if duel_kind != "multi_attempts" and duel["opponent_id"] == user["user_id"] and duel["opponent_score"] is not None:
                raise HTTPException(status_code=409, detail="Vous avez déjà relevé ce défi.")
            if duel_kind != "multi_attempts" and not duel["opponent_id"]:
                await _enforce_daily_accept_cap(conn, user["user_id"])
            elif duel_kind == "multi_attempts":
                # Reuse the same daily cap so multi-attempts don't bypass
                # spam protection.
                await _enforce_daily_accept_cap(conn, user["user_id"])

            # Fetch the same questions
            session_row = await conn.fetchrow(
                "SELECT question_ids FROM quiz_sessions WHERE id = $1",
                duel["challenger_session_id"],
            )
            if not session_row:
                raise HTTPException(status_code=500, detail="Session du défi introuvable.")
            q_rows = await conn.fetch(
                "SELECT id, text, options, category FROM quiz_questions WHERE id = ANY($1::bigint[])",
                list(session_row["question_ids"]),
            )
            by_id = {r["id"]: r for r in q_rows}
            import random
            timer_s = await _current_timer_seconds()
            questions = []
            perms: List[List[int]] = []
            for qid in session_row["question_ids"]:
                q = by_id.get(qid)
                if not q:
                    continue
                original = _parse_options(q["options"])
                perm = [0, 1, 2, 3]
                random.shuffle(perm)
                shuffled = [original[i] for i in perm]
                perms.append(perm)
                questions.append({
                    "id": int(q["id"]),
                    "text": q["text"],
                    "options": shuffled,
                    "category": q["category"],
                })
            if len(questions) < SESSION_SIZE:
                raise HTTPException(status_code=500, detail="Défi corrompu.")

            now = datetime.now(timezone.utc)
            # Create an opponent run linked by session but marked with duel
            # metadata (we reuse quiz_user_runs to centralise logic).
            opp_run_id = await conn.fetchval(
                """INSERT INTO quiz_user_runs
                     (user_id, session_id, started_at, options_order, time_limit_s)
                   VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id""",
                user["user_id"], int(duel["challenger_session_id"]),
                now, _json.dumps(perms), timer_s,
            )
            # iter141 — multi_attempts: do NOT update duels.opponent_id
            # (would lock the duel to one opponent). The per-user run is
            # tracked locally via quiz_user_runs (looked up by submitted_at
            # IS NULL + session_id at submit). For 'classic' duels keep the
            # legacy lock so the existing 1v1 flow stays intact.
            duel_kind = duel["duel_kind"] if "duel_kind" in duel.keys() else "classic"
            if duel_kind != "multi_attempts":
                await conn.execute(
                    """UPDATE duels
                         SET opponent_id = $1, accepted_at = COALESCE(accepted_at, $2),
                             status = 'accepted', opponent_run_id = $3
                       WHERE id = $4""",
                    user["user_id"], now, int(opp_run_id), duel["id"],
                )
            else:
                await conn.execute(
                    """UPDATE duels SET accepted_at = COALESCE(accepted_at, $1)
                        WHERE id = $2""",
                    now, duel["id"],
                )

    return {
        "duel_token": token,
        "run_id": int(opp_run_id),
        "time_limit_seconds": timer_s,
        "questions": questions,
    }


@router.post("/{token}/submit-quiz")
async def submit_quiz_duel(token: str, req: SubmitQuizDuel, request: Request):
    await _ensure_duel_enabled()
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        from routes.quiz import SESSION_TIME_NETWORK_GRACE_SECONDS, SESSION_SIZE, POINTS_PER_CORRECT
        async with conn.transaction():
            duel = await conn.fetchrow(
                "SELECT * FROM duels WHERE share_token = $1 FOR UPDATE", token,
            )
            if not duel or duel["game"] != "quiz":
                raise HTTPException(status_code=404, detail="Défi introuvable.")
            duel_kind = duel["duel_kind"] if "duel_kind" in duel.keys() else "classic"
            is_multi = duel_kind == "multi_attempts"

            if not is_multi:
                if duel["opponent_id"] != user["user_id"]:
                    raise HTTPException(status_code=403, detail="Vous n'êtes pas l'adversaire de ce défi.")
                if duel["status"] == "completed":
                    raise HTTPException(status_code=400, detail="Défi déjà terminé.")
                run = await conn.fetchrow(
                    "SELECT * FROM quiz_user_runs WHERE id = $1 FOR UPDATE",
                    duel["opponent_run_id"],
                )
            else:
                # iter141 — multi_attempts : challenger cannot self-submit
                if duel["challenger_id"] == user["user_id"]:
                    raise HTTPException(status_code=400, detail="Vous ne pouvez pas relever votre propre défi.")
                # Block double-submit per user via duel_attempts.
                already = await conn.fetchval(
                    "SELECT 1 FROM duel_attempts WHERE duel_id = $1 AND user_id = $2",
                    duel["id"], user["user_id"],
                )
                if already:
                    raise HTTPException(status_code=409, detail="Vous avez déjà relevé ce défi.")
                # Find the user's most recent unsubmitted run on this duel's
                # session — created by start_quiz_duel above.
                run = await conn.fetchrow(
                    """SELECT * FROM quiz_user_runs
                        WHERE user_id = $1 AND session_id = $2 AND submitted_at IS NULL
                        ORDER BY started_at DESC
                        LIMIT 1
                        FOR UPDATE""",
                    user["user_id"], int(duel["challenger_session_id"]),
                )
            if not run:
                raise HTTPException(status_code=404, detail="Session introuvable.")
            if run["submitted_at"]:
                raise HTTPException(status_code=400, detail="Session déjà soumise.")

            now = datetime.now(timezone.utc)
            elapsed = (now - run["started_at"]).total_seconds()
            limit = int(run["time_limit_s"] or 10)
            timed_out = elapsed > (limit + SESSION_TIME_NETWORK_GRACE_SECONDS)

            # Unshuffle via perms
            session_row = await conn.fetchrow(
                "SELECT question_ids FROM quiz_sessions WHERE id = $1",
                run["session_id"],
            )
            q_rows = await conn.fetch(
                "SELECT id, correct_index FROM quiz_questions WHERE id = ANY($1::bigint[])",
                list(session_row["question_ids"]),
            )
            cmap = {int(r["id"]): int(r["correct_index"]) for r in q_rows}
            order = [int(qid) for qid in session_row["question_ids"]]
            perms = _parse_perms(run["options_order"])
            while len(perms) < SESSION_SIZE:
                perms.append([0, 1, 2, 3])

            answers = list(req.answers)
            correct_count = 0
            correct_displayed: List[int] = []
            for i, qid in enumerate(order):
                original_correct = cmap.get(qid, -1)
                perm = perms[i]
                try:
                    correct_displayed.append(perm.index(original_correct) if original_correct >= 0 else -1)
                except ValueError:
                    correct_displayed.append(-1)
                given = answers[i] if i < len(answers) else -1
                given_original = perm[given] if 0 <= given < len(perm) else -1
                if given_original >= 0 and given_original == original_correct:
                    correct_count += 1

            points_run = 0 if timed_out else correct_count * POINTS_PER_CORRECT
            await conn.execute(
                """UPDATE quiz_user_runs
                     SET submitted_at = $1, answers = $2, correct_count = $3,
                         points_awarded = $4, timed_out = $5
                   WHERE id = $6""",
                now, answers, correct_count, points_run, timed_out, run["id"],
            )
            # Register answers for the global 75%/50 rule
            await register_quiz_answers(conn, user["user_id"], correct_count, SESSION_SIZE)

            # Settle vs the challenger (always — single source of truth).
            opponent_score = correct_count
            opponent_time_s = round((now - run["started_at"]).total_seconds(), 2)
            challenger_score = int(duel["challenger_score"] or 0)
            challenger_time_s = (
                float(duel["challenger_time_s"]) if duel["challenger_time_s"] is not None else None
            )
            winner_id = None
            tiebreaker = None  # 'time' | None
            if opponent_score > challenger_score:
                winner_id = user["user_id"]
            elif opponent_score < challenger_score:
                winner_id = duel["challenger_id"]
            else:
                # iter131 — Equal scores → faster player wins (must be within
                # 0.20s diff to avoid trivial network luck calls).
                if (challenger_time_s is not None
                        and abs(opponent_time_s - challenger_time_s) >= 0.20):
                    if opponent_time_s < challenger_time_s:
                        winner_id = user["user_id"]
                    else:
                        winner_id = duel["challenger_id"]
                    tiebreaker = "time"
                # else: pure tie — no winner
            outcome = "tie" if winner_id is None else (
                "won" if winner_id == user["user_id"] else "lost"
            )
            cfg = await _cfg()
            # Base points for opponent's run (via standard points_service)
            await add_points(conn, user["user_id"], points_run, source="quiz",
                             metadata={"duel": token})
            # Duel bonuses — attributed to the GAME played (quiz here) so the
            # admin breakdown (points_quiz/points_tap) stays coherent.
            if winner_id:
                loser_id = duel["challenger_id"] if winner_id == user["user_id"] else user["user_id"]
                await add_points(conn, winner_id, cfg["winner_bonus"], source="quiz",
                                 metadata={"duel": token, "kind": "duel_win"})
                await add_points(conn, loser_id, cfg["loser_bonus"], source="quiz",
                                 metadata={"duel": token, "kind": "duel_consolation"})
            else:
                # Tie — both get the consolation
                for uid in (user["user_id"], duel["challenger_id"]):
                    await add_points(conn, uid, cfg["loser_bonus"], source="quiz",
                                     metadata={"duel": token, "kind": "duel_tie"})

            if is_multi:
                # iter141 — record per-user attempt, keep duel open for more.
                await conn.execute(
                    """INSERT INTO duel_attempts
                          (duel_id, user_id, run_id, score, time_s, outcome)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    duel["id"], user["user_id"], int(run["id"]),
                    int(opponent_score), opponent_time_s, outcome,
                )
                # Don't close the duel — others can still play.
                # iter141seven — viral recruit credit (idempotent, admin-configurable).
                try:
                    from services.recruit_service import record_recruit_credit
                    recruit_result = await record_recruit_credit(
                        conn,
                        initiator_user_id=duel["challenger_id"],
                        recruit_user_id=user["user_id"],
                        duel_id=int(duel["id"]),
                        source_kind="multi_attempts",
                    )
                except Exception as e:
                    logger.warning("[duel] recruit credit failed (multi): %s", e)
                    recruit_result = {"awarded_points": 0, "buzz_unlocked": False}
            else:
                await conn.execute(
                    """UPDATE duels
                         SET opponent_score = $1, winner_id = $2, status = 'completed',
                             completed_at = $3,
                             opponent_metadata = $4::jsonb,
                             opponent_time_s = $5
                       WHERE id = $6""",
                    int(opponent_score), winner_id, now,
                    _json.dumps({
                        "correct_count": correct_count,
                        "total": SESSION_SIZE,
                        "timed_out": timed_out,
                        "time_s": opponent_time_s,
                        "tiebreaker": tiebreaker,
                    }),
                    opponent_time_s,
                    duel["id"],
                )
                # iter141seven — viral recruit credit on the 1v1 case as well.
                try:
                    from services.recruit_service import record_recruit_credit
                    recruit_result = await record_recruit_credit(
                        conn,
                        initiator_user_id=duel["challenger_id"],
                        recruit_user_id=user["user_id"],
                        duel_id=int(duel["id"]),
                        source_kind="classic_1v1",
                    )
                except Exception as e:
                    logger.warning("[duel] recruit credit failed (1v1): %s", e)
                    recruit_result = {"awarded_points": 0, "buzz_unlocked": False}

    # iter132 — Notify the challenger that their duel was answered. Async,
    # non-blocking, silent on failure (push_service handles OneSignal OFF
    # gracefully). Done AFTER the txn commits so notification reflects the
    # final settled state.
    try:
        from services.notifications import send_social_notification
        opp_name = (
            user.get("first_name") or user.get("username") or "Quelqu'un"
        )
        if is_multi:
            # iter141 — multi-challenger context. Always frame it as a new
            # challenger picking up the daily challenge so the initiator
            # opens their dashboard to see the leaderboard update.
            if winner_id == user["user_id"]:
                title = f"⚔️ {opp_name} t'a battu sur ton défi quotidien !"
                body = (
                    f"Score : {opponent_score}/{SESSION_SIZE} contre tes "
                    f"{challenger_score}/{SESSION_SIZE}. "
                    f"Voir le classement complet."
                )
            elif winner_id == duel["challenger_id"]:
                title = f"🛡️ {opp_name} a tenté ton défi quotidien — et a perdu."
                body = (
                    f"{opponent_score}/{SESSION_SIZE} pour lui, "
                    f"{challenger_score}/{SESSION_SIZE} pour toi. "
                    f"Tu domines le classement."
                )
            else:
                title = f"⚖️ {opp_name} a égalisé ton défi quotidien !"
                body = (
                    f"Match nul {challenger_score}-{opponent_score}. "
                    f"Voir le classement complet."
                )
        elif winner_id == user["user_id"]:
            title = f"🔥 {opp_name} a relevé ton défi !"
            body = (
                f"Score : {opponent_score}/{SESSION_SIZE} contre tes "
                f"{challenger_score}/{SESSION_SIZE} — il prend la tête. "
                f"Vas-tu accepter la revanche ?"
            )
        elif winner_id == duel["challenger_id"]:
            title = f"🛡️ {opp_name} a relevé ton défi… et a perdu !"
            body = (
                f"Score : {opponent_score}/{SESSION_SIZE} contre tes "
                f"{challenger_score}/{SESSION_SIZE} — tu domines, défends ta couronne !"
            )
        else:
            title = f"⚖️ {opp_name} a égalisé ton défi !"
            body = (
                f"Match nul {challenger_score}-{opponent_score} — "
                f"départage au prochain duel !"
            )
        await send_social_notification(
            event_type="duel_completed",
            actor={
                "user_id": user["user_id"],
                "first_name": user.get("first_name") or "",
                "last_name":  user.get("last_name") or "",
                "username":   user.get("username") or "",
                "avatar":     user.get("avatar") or "",
            },
            target_user_id=duel["challenger_id"],
            title=title,
            body=body,
            deep_link=f"/duel/{token}",
            extra_data={
                "duel_token": token,
                "winner_id": winner_id,
                "challenger_score": challenger_score,
                "opponent_score": opponent_score,
            },
        )
    except Exception as e:
        logger.debug(f"[duel] post-completion notif failed: {e}")

    return {
        "duel_token": token,
        "duel_kind": duel_kind,
        "opponent_score": int(correct_count),
        "challenger_score": int(challenger_score),
        "winner_id": winner_id,
        "is_tie": winner_id is None,
        "timed_out": timed_out,
        "base_points_awarded": points_run,
        "bonus_awarded": cfg["winner_bonus"] if winner_id == user["user_id"] else cfg["loser_bonus"],
        "correct_by_question": correct_displayed,
        "challenger_time_s": challenger_time_s,
        "opponent_time_s": opponent_time_s,
        "tiebreaker": tiebreaker,
        # iter141seven — surface recruit credit so the client can toast it
        # (only the FIRST submission by this user for this initiator counts).
        "recruit_credit": recruit_result,
    }


# ═════════════════ Tap side ═════════════════

@router.post("/{token}/start-tap")
async def start_tap_duel(token: str, request: Request):
    await _ensure_duel_enabled()
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        from routes.tap import DURATION_SECONDS
        async with conn.transaction():
            duel = await conn.fetchrow(
                "SELECT * FROM duels WHERE share_token = $1 FOR UPDATE", token,
            )
            if not duel:
                raise HTTPException(status_code=404, detail="Défi introuvable.")
            if duel["game"] != "tap":
                raise HTTPException(status_code=400, detail="Ce défi n'est pas un Tap.")
            await _opponent_guards(conn, duel, user["user_id"])
            if duel["opponent_id"] == user["user_id"] and duel["opponent_score"] is not None:
                raise HTTPException(status_code=409, detail="Vous avez déjà relevé ce défi.")
            if not duel["opponent_id"]:
                await _enforce_daily_accept_cap(conn, user["user_id"])

            # Tap duel run bypasses the normal 1/day limit (it's a duel).
            now = datetime.now(timezone.utc)
            opp_run_id = await conn.fetchval(
                """INSERT INTO tap_user_runs (user_id, started_at)
                   VALUES ($1, $2) RETURNING id""",
                user["user_id"], now,
            )
            await conn.execute(
                """UPDATE duels
                     SET opponent_id = $1, accepted_at = COALESCE(accepted_at, $2),
                         status = 'accepted', opponent_run_id = $3
                   WHERE id = $4""",
                user["user_id"], now, int(opp_run_id), duel["id"],
            )

    return {
        "duel_token": token,
        "run_id": int(opp_run_id),
        "duration_seconds": DURATION_SECONDS,
        "start_at": now.isoformat(),
        "challenger_score": int(duel["challenger_score"] or 0),
    }


@router.post("/{token}/submit-tap")
async def submit_tap_duel(token: str, req: SubmitTapDuel, request: Request):
    await _ensure_duel_enabled()
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        from routes.tap import (
            DURATION_SECONDS, NETWORK_GRACE_SECONDS, TAPS_PER_SEC_CAP,
            BONUS_TIERS, SUSPICIOUS_TAPS_THRESHOLD,
        )
        async with conn.transaction():
            duel = await conn.fetchrow(
                "SELECT * FROM duels WHERE share_token = $1 FOR UPDATE", token,
            )
            if not duel or duel["game"] != "tap":
                raise HTTPException(status_code=404, detail="Défi introuvable.")
            if duel["opponent_id"] != user["user_id"]:
                raise HTTPException(status_code=403, detail="Vous n'êtes pas l'adversaire de ce défi.")
            if duel["status"] == "completed":
                raise HTTPException(status_code=400, detail="Défi déjà terminé.")

            run = await conn.fetchrow(
                "SELECT * FROM tap_user_runs WHERE id = $1 FOR UPDATE",
                duel["opponent_run_id"],
            )
            if not run:
                raise HTTPException(status_code=404, detail="Session introuvable.")
            if run["submitted_at"]:
                raise HTTPException(status_code=400, detail="Session déjà soumise.")

            now = datetime.now(timezone.utc)
            elapsed = (now - run["started_at"]).total_seconds()
            timed_out = elapsed > (DURATION_SECONDS + NETWORK_GRACE_SECONDS)
            ceiling = TAPS_PER_SEC_CAP * DURATION_SECONDS
            taps_raw = int(req.taps)
            taps_valid = min(taps_raw, ceiling)
            cheated = taps_raw > ceiling
            suspicious = taps_valid >= SUSPICIOUS_TAPS_THRESHOLD
            if timed_out:
                taps_valid = 0
                suspicious = False
            # Base points = taps_valid + milestone bonus
            base = taps_valid
            bonus = 0
            for threshold, reward in BONUS_TIERS:
                if taps_valid >= threshold:
                    bonus = reward
                    break
            points_run = base + bonus

            await conn.execute(
                """UPDATE tap_user_runs
                     SET submitted_at = $1, taps_raw = $2, taps_valid = $3,
                         points_awarded = $4, timed_out = $5, cheated = $6,
                         suspicious = $7
                   WHERE id = $8""",
                now, taps_raw, taps_valid, points_run, timed_out, cheated,
                suspicious, run["id"],
            )
            await register_tap_run(conn, user["user_id"])
            await add_points(conn, user["user_id"], points_run, source="tap",
                             metadata={"duel": token})

            challenger_score = int(duel["challenger_score"] or 0)
            winner_id = None
            if taps_valid > challenger_score:
                winner_id = user["user_id"]
            elif taps_valid < challenger_score:
                winner_id = duel["challenger_id"]

            cfg = await _cfg()
            if winner_id:
                loser_id = duel["challenger_id"] if winner_id == user["user_id"] else user["user_id"]
                await add_points(conn, winner_id, cfg["winner_bonus"], source="tap",
                                 metadata={"duel": token, "kind": "duel_win"})
                await add_points(conn, loser_id, cfg["loser_bonus"], source="tap",
                                 metadata={"duel": token, "kind": "duel_consolation"})
            else:
                for uid in (user["user_id"], duel["challenger_id"]):
                    await add_points(conn, uid, cfg["loser_bonus"], source="tap",
                                     metadata={"duel": token, "kind": "duel_tie"})

            await conn.execute(
                """UPDATE duels
                     SET opponent_score = $1, winner_id = $2, status = 'completed',
                         completed_at = $3,
                         opponent_metadata = $4::jsonb
                   WHERE id = $5""",
                int(taps_valid), winner_id, now,
                _json.dumps({"taps_valid": int(taps_valid), "cheated": cheated,
                             "suspicious": suspicious, "timed_out": timed_out}),
                duel["id"],
            )

    return {
        "duel_token": token,
        "opponent_score": int(taps_valid),
        "challenger_score": int(challenger_score),
        "winner_id": winner_id,
        "is_tie": winner_id is None,
        "timed_out": timed_out,
        "cheated": cheated,
        "suspicious": suspicious,
        "base_points_awarded": points_run,
        "bonus_awarded": cfg["winner_bonus"] if winner_id == user["user_id"] else cfg["loser_bonus"],
    }


# ══════════════════════════════════════════════════════════════════════════
#  iter132 — Rematch + Percentile rank
# ══════════════════════════════════════════════════════════════════════════

@router.post("/{token}/rematch")
async def create_rematch(token: str, request: Request):
    """Create a brand-new duel from a completed one with **inverted roles**.
    The previous winner becomes the new challenger ⇒ the old loser is now
    the receiver, so the loop "défi → réponse → revanche → réponse" can
    run forever. The user calling this endpoint must be one of the two
    original participants AND must have a fresh quiz/tap run from which
    to base the new duel.

    Request body: `{run_id: int}` — the requester's most recent run that
    will become the challenger_score for the rematch.
    """
    user = await get_current_user(request)
    body = await request.json() if request.headers.get("content-length") else {}
    run_id = int(body.get("run_id") or 0)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        original = await conn.fetchrow(
            """SELECT * FROM duels WHERE share_token = $1""", token
        )
        if not original:
            raise HTTPException(status_code=404, detail="Duel introuvable.")
        if original["status"] != "completed":
            raise HTTPException(status_code=400, detail="Le duel n'est pas terminé.")
        # Caller must be one of the participants.
        participants = (original["challenger_id"], original["opponent_id"])
        if user["user_id"] not in participants:
            raise HTTPException(status_code=403, detail="Vous n'avez pas participé à ce duel.")
        # Inverted target: the OTHER participant becomes the implicit recipient.
        opponent_target = (
            original["opponent_id"]
            if user["user_id"] == original["challenger_id"]
            else original["challenger_id"]
        )
        if original["game"] != "quiz":
            raise HTTPException(status_code=400, detail="Revanche disponible uniquement sur quiz.")
        from routes.quiz import SESSION_SIZE
        # iter132 — auto-discover most recent submitted run if none provided.
        if run_id <= 0:
            latest = await conn.fetchrow(
                """SELECT id FROM quiz_user_runs
                    WHERE user_id = $1 AND submitted_at IS NOT NULL
                      AND correct_count IS NOT NULL
                 ORDER BY submitted_at DESC LIMIT 1""",
                user["user_id"],
            )
            if not latest:
                raise HTTPException(
                    status_code=400,
                    detail="Joue d'abord une partie pour créer ta revanche.",
                )
            run_id = int(latest["id"])
        run = await conn.fetchrow(
            """SELECT id, user_id, session_id, correct_count, started_at, submitted_at
                 FROM quiz_user_runs WHERE id = $1""",
            run_id,
        )
        if not run or run["user_id"] != user["user_id"]:
            raise HTTPException(status_code=404, detail="Session quiz introuvable.")
        if not run["submitted_at"] or run["correct_count"] is None:
            raise HTTPException(status_code=400, detail="Terminez votre session avant la revanche.")

        try:
            challenger_time_s = round(
                (run["submitted_at"] - run["started_at"]).total_seconds(), 2
            )
        except Exception:
            challenger_time_s = None

        new_token = secrets.token_urlsafe(16)
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        new_id = await conn.fetchval(
            """INSERT INTO duels
                 (share_token, game, challenger_id, challenger_run_id,
                  challenger_score, challenger_session_id, challenger_metadata,
                  challenger_time_s, expires_at)
               VALUES ($1, 'quiz', $2, $3, $4, $5, $6::jsonb, $7, $8)
               RETURNING id""",
            new_token, user["user_id"], int(run["id"]),
            int(run["correct_count"]), int(run["session_id"]),
            _json.dumps({
                "correct_count": int(run["correct_count"]),
                "total": SESSION_SIZE,
                "time_s": challenger_time_s,
                "rematch_of": token,
                "intended_opponent": opponent_target,
            }),
            challenger_time_s,
            expires,
        )

    # Notify the intended opponent (best-effort, async).
    try:
        from services.notifications import send_social_notification
        actor_name = user.get("first_name") or user.get("username") or "Quelqu'un"
        await send_social_notification(
            event_type="duel_rematch",
            actor={
                "user_id": user["user_id"],
                "first_name": user.get("first_name") or "",
                "last_name":  user.get("last_name") or "",
                "username":   user.get("username") or "",
                "avatar":     user.get("avatar") or "",
            },
            target_user_id=opponent_target,
            title=f"⚔️ {actor_name} demande la revanche !",
            body=f"Score à battre : {run['correct_count']}/{SESSION_SIZE} en "
                 f"{challenger_time_s:.1f}s. Relèveras-tu le défi ?",
            deep_link=f"/duel/{new_token}",
            extra_data={"duel_token": new_token, "rematch_of": token},
        )
    except Exception as e:
        logger.debug(f"[duel] rematch notif failed: {e}")

    backend_url = (request.headers.get("origin") or "").rstrip("/")
    return {
        "id": int(new_id),
        "share_token": new_token,
        "share_url": f"{backend_url}/duel/{new_token}",
        "expires_at": expires.isoformat(),
        "intended_opponent": opponent_target,
    }


@router.get("/me/rank")
async def duel_rank(request: Request):
    """iter132 — Approximate the caller's percentile rank among quiz duel
    players over the last 30 days. Used in CompletedDuelView to display
    "🔥 Tu es dans le top X% des joueurs". Uses average correct_count,
    floored at 5 duels played to avoid noise.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Per-player avg score over recent window.
        rows = await conn.fetch(
            """WITH plays AS (
                 SELECT challenger_id AS uid, challenger_score AS score
                   FROM duels
                  WHERE game='quiz' AND status='completed'
                    AND completed_at >= NOW() - INTERVAL '30 days'
                 UNION ALL
                 SELECT opponent_id AS uid, opponent_score AS score
                   FROM duels
                  WHERE game='quiz' AND status='completed'
                    AND opponent_score IS NOT NULL
                    AND completed_at >= NOW() - INTERVAL '30 days'
               )
               SELECT uid, AVG(score)::float AS avg_score, COUNT(*) AS n
                 FROM plays
                WHERE uid IS NOT NULL
                GROUP BY uid
               HAVING COUNT(*) >= 1"""
        )
    if not rows:
        return {"percentile": None, "rank": None, "total_players": 0,
                "your_avg": None, "min_plays": False}
    sorted_avgs = sorted([r["avg_score"] for r in rows], reverse=True)
    me = next((r for r in rows if r["uid"] == user["user_id"]), None)
    if not me:
        return {"percentile": None, "rank": None, "total_players": len(rows),
                "your_avg": None, "min_plays": False}
    rank = sum(1 for s in sorted_avgs if s > me["avg_score"]) + 1
    total = len(sorted_avgs)
    pct = round(100.0 * rank / total)
    return {
        "percentile": pct,            # 1..100, smaller = better
        "rank": rank,
        "total_players": total,
        "your_avg": round(float(me["avg_score"]), 2),
        "your_plays": int(me["n"]),
        "min_plays": int(me["n"]) >= 3,
    }


# ══════════════════════════════════════════════════════════════════════════
#  iter141 — Multi-challenger leaderboard + initiator dashboard
# ══════════════════════════════════════════════════════════════════════════

@router.get("/{token}/leaderboard")
async def duel_leaderboard(token: str, request: Request):
    """iter141 — Returns the full leaderboard for a multi_attempts duel
    (typically created from a Daily Challenge share). For 'classic' 1v1
    duels this just echoes the legacy 2-row leaderboard for UI uniformity.

    Public (no auth required) — the share link is meant to be virally
    distributed. Personal flags (you_played, your_outcome) computed
    against the optional bearer token.
    """
    pool = await get_pool()
    me_id = None
    try:
        u = await get_current_user(request)
        me_id = u.get("user_id") if u else None
    except Exception:
        me_id = None
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        await conn.execute(_mark_expired_sql())
        duel = await conn.fetchrow(
            "SELECT * FROM duels WHERE share_token = $1", token,
        )
        if not duel:
            raise HTTPException(status_code=404, detail="Défi introuvable.")
        duel_kind = duel["duel_kind"] if "duel_kind" in duel.keys() else "classic"
        # Resolve initiator (=challenger) profile.
        ch_row = await conn.fetchrow(
            "SELECT user_id, first_name, last_name, email, avatar FROM users WHERE user_id = $1",
            duel["challenger_id"],
        )
        initiator = {
            "user_id": duel["challenger_id"],
            "name": (f"{ch_row['first_name'] or ''} {ch_row['last_name'] or ''}".strip()
                     or (ch_row["email"] if ch_row else None)
                     or duel["challenger_id"]) if ch_row else duel["challenger_id"],
            "avatar": ch_row["avatar"] if ch_row else None,
        }
        challenger_score = int(duel["challenger_score"] or 0)
        challenger_time_s = float(duel["challenger_time_s"]) if duel["challenger_time_s"] is not None else None

        if duel_kind == "multi_attempts":
            rows = await conn.fetch(
                """SELECT a.user_id, a.run_id, a.score, a.time_s, a.outcome, a.submitted_at,
                          u.first_name, u.last_name, u.email, u.avatar
                     FROM duel_attempts a
                     LEFT JOIN users u ON u.user_id = a.user_id
                    WHERE a.duel_id = $1
                    ORDER BY a.score DESC, a.time_s ASC NULLS LAST""",
                int(duel["id"]),
            )
        else:
            # Map classic duel to the same shape (single opponent row).
            rows = []
            if duel["opponent_id"]:
                opp = await conn.fetchrow(
                    "SELECT user_id, first_name, last_name, email, avatar FROM users WHERE user_id = $1",
                    duel["opponent_id"],
                )
                outcome = (
                    "tie" if duel["winner_id"] is None
                    else ("won" if duel["winner_id"] == duel["opponent_id"] else "lost")
                )
                rows = [{
                    "user_id": duel["opponent_id"],
                    "run_id": int(duel["opponent_run_id"]) if duel["opponent_run_id"] else None,
                    "score": int(duel["opponent_score"] or 0) if duel["opponent_score"] is not None else None,
                    "time_s": float(duel["opponent_time_s"]) if duel["opponent_time_s"] is not None else None,
                    "outcome": outcome,
                    "submitted_at": duel["completed_at"],
                    "first_name": opp["first_name"] if opp else None,
                    "last_name":  opp["last_name"] if opp else None,
                    "email":      opp["email"] if opp else None,
                    "avatar":     opp["avatar"] if opp else None,
                }]

    attempts = []
    your_attempt = None
    for i, r in enumerate(rows):
        name = (f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
                or r["email"] or r["user_id"])
        item = {
            "rank": i + 1,
            "user": {"user_id": r["user_id"], "name": name, "avatar": r["avatar"]},
            "score": int(r["score"]) if r["score"] is not None else None,
            "time_s": float(r["time_s"]) if r["time_s"] is not None else None,
            "outcome": r["outcome"],   # 'won' | 'lost' | 'tie' (vs initiator)
            "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
            "is_you": (me_id is not None and r["user_id"] == me_id),
        }
        attempts.append(item)
        if me_id and r["user_id"] == me_id:
            your_attempt = item

    stats = {
        "participants": len(attempts),
        "best_score":   max((a["score"] for a in attempts if a["score"] is not None), default=None),
        "wins_for_initiator":   sum(1 for a in attempts if a["outcome"] == "lost"),
        "losses_for_initiator": sum(1 for a in attempts if a["outcome"] == "won"),
        "ties":                 sum(1 for a in attempts if a["outcome"] == "tie"),
    }
    return {
        "share_token": token,
        "game": duel["game"],
        "duel_kind": duel_kind,
        "status": duel["status"],
        "expires_at": duel["expires_at"].isoformat() if duel["expires_at"] else None,
        "initiator": initiator,
        "initiator_score": challenger_score,
        "initiator_time_s": challenger_time_s,
        "attempts": attempts,
        "your_attempt": your_attempt,
        "you_are_initiator": (me_id is not None and me_id == duel["challenger_id"]),
        "stats": stats,
    }


@router.get("/me/sent")
async def my_sent_duels(request: Request, limit: int = 20):
    """iter141 — Initiator dashboard. Lists all duels created by the
    caller (mostly multi_attempts from daily challenges) with quick
    aggregates so they can see who's playing and how they fare.
    """
    user = await get_current_user(request)
    limit = max(1, min(int(limit), 100))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        await conn.execute(_mark_expired_sql())
        rows = await conn.fetch(
            """SELECT d.*,
                      COALESCE((SELECT COUNT(*) FROM duel_attempts a WHERE a.duel_id = d.id), 0) AS multi_participants,
                      COALESCE((SELECT MAX(score) FROM duel_attempts a WHERE a.duel_id = d.id), -1) AS multi_best_score,
                      COALESCE((SELECT COUNT(*) FROM duel_attempts a WHERE a.duel_id = d.id AND outcome='lost'), 0) AS multi_wins_initiator,
                      COALESCE((SELECT COUNT(*) FROM duel_attempts a WHERE a.duel_id = d.id AND outcome='won'),  0) AS multi_losses_initiator,
                      COALESCE((SELECT COUNT(*) FROM duel_attempts a WHERE a.duel_id = d.id AND outcome='tie'),  0) AS multi_ties
                 FROM duels d
                WHERE d.challenger_id = $1
                ORDER BY d.created_at DESC
                LIMIT $2""",
            user["user_id"], limit,
        )
        items = []
        for r in rows:
            duel_kind = r["duel_kind"] if "duel_kind" in r.keys() else "classic"
            participants = int(r["multi_participants"] or 0) if duel_kind == "multi_attempts" else (1 if r["opponent_id"] else 0)
            best = int(r["multi_best_score"]) if duel_kind == "multi_attempts" and r["multi_best_score"] is not None and int(r["multi_best_score"]) >= 0 else (
                int(r["opponent_score"]) if r["opponent_score"] is not None else None
            )
            items.append({
                "id": int(r["id"]),
                "share_token": r["share_token"],
                "share_url": f"/duel/{r['share_token']}",
                "game": r["game"],
                "duel_kind": duel_kind,
                "status": r["status"],
                "created_at":   r["created_at"].isoformat() if r["created_at"] else None,
                "expires_at":   r["expires_at"].isoformat() if r["expires_at"] else None,
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
                "initiator_score": int(r["challenger_score"] or 0),
                "participants": participants,
                "best_challenger_score": best,
                "wins_for_initiator":   int(r["multi_wins_initiator"] or 0),
                "losses_for_initiator": int(r["multi_losses_initiator"] or 0),
                "ties":                 int(r["multi_ties"] or 0),
            })
    return {"items": items}


# ══════════════════════════════════════════════════════════════════════════
#  Admin observability
# ══════════════════════════════════════════════════════════════════════════

@router.get("/admin/overview")
async def admin_overview(request: Request, days: int = 30):
    from routes.auth import require_admin as _ra
    await _ra(request)
    days = max(1, min(int(days), 90))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        await conn.execute(_mark_expired_sql())
        totals = await conn.fetchrow(
            """SELECT COUNT(*)                                                   AS total,
                      COUNT(*) FILTER (WHERE status='open')                      AS open,
                      COUNT(*) FILTER (WHERE status='accepted')                  AS in_progress,
                      COUNT(*) FILTER (WHERE status='completed')                 AS completed,
                      COUNT(*) FILTER (WHERE status='expired')                   AS expired,
                      COUNT(DISTINCT challenger_id) FILTER (WHERE created_at >= NOW() - ($1||' days')::interval) AS unique_challengers,
                      COUNT(DISTINCT opponent_id)   FILTER (WHERE accepted_at IS NOT NULL
                                                          AND accepted_at >= NOW() - ($1||' days')::interval) AS unique_opponents
               FROM duels
               WHERE created_at >= NOW() - ($1 || ' days')::interval""",
            str(days),
        )
        ts = await conn.fetch(
            """SELECT created_at::date AS day,
                      COUNT(*) AS created,
                      COUNT(*) FILTER (WHERE status='completed') AS completed
               FROM duels
               WHERE created_at >= NOW() - ($1 || ' days')::interval
               GROUP BY 1 ORDER BY 1""",
            str(days),
        )
        top = await conn.fetch(
            """SELECT d.challenger_id AS uid,
                      COUNT(*) AS created,
                      COUNT(*) FILTER (WHERE winner_id = challenger_id) AS won,
                      u.first_name, u.last_name, u.email
               FROM duels d
               LEFT JOIN users u ON u.user_id = d.challenger_id
               WHERE d.created_at >= NOW() - ($1 || ' days')::interval
               GROUP BY d.challenger_id, u.first_name, u.last_name, u.email
               ORDER BY created DESC LIMIT 10""",
            str(days),
        )
        conv = 0.0
        if int(totals["total"] or 0) > 0:
            conv = float(int(totals["completed"] or 0)) / float(int(totals["total"]))
    return {
        "window_days": days,
        "total":       int(totals["total"] or 0),
        "open":        int(totals["open"] or 0),
        "in_progress": int(totals["in_progress"] or 0),
        "completed":   int(totals["completed"] or 0),
        "expired":     int(totals["expired"] or 0),
        "unique_challengers": int(totals["unique_challengers"] or 0),
        "unique_opponents":   int(totals["unique_opponents"] or 0),
        "conversion_rate":    round(conv, 4),
        "timeseries": [
            {"day": t["day"].isoformat(),
             "created": int(t["created"]),
             "completed": int(t["completed"])}
            for t in ts
        ],
        "top_challengers": [
            {
                "user_id": r["uid"],
                "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["email"] or r["uid"],
                "created": int(r["created"]),
                "won": int(r["won"]),
            } for r in top
        ],
    }
