"""
iter83 — Unified points engine (Roue + Quiz + Tap → 10 000 pts Starter Pro).

This service is the ONE place where games add points. It enforces :
    - the sovereign clamp (points < 10 000 while days_played < 25)
    - the daily distinct-day counter (anti-exploit multi-session)
    - the admin kill-switches `wheel_enabled`, `quiz_enabled`, `tap_enabled`
    - the 75 % cycle-wide quiz accuracy rule

Any game module MUST call :
    await ensure_game_enabled("quiz")          # before running the game loop
    await add_points(conn, user_id, amount, source="quiz|tap|wheel")
    await register_quiz_answers(...)           # quiz-only

The wheel of fortune keeps its own /spin endpoint (with weighted_pick, jackpot
logic, etc.) but could call add_points internally at some point in the future.
The service is idempotent-safe : callers open their own transaction and pass
the connection so we can serialise on the cycle row (`FOR UPDATE`).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException

from services.settings_service import get_bool

logger = logging.getLogger(__name__)

# ── Constants (source of truth — must match wheel_fortune.py) ──
POINTS_GOAL = 10_000
DAYS_GOAL = 25
CYCLE_LENGTH_DAYS = 30
QUIZ_ACCURACY_THRESHOLD = 0.75
QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY = 50   # ≥10 sessions of 5Q before the 75 % rule triggers

CYCLE_STATUS_IN_PROGRESS = "in_progress"
CYCLE_STATUS_REWARD_PENDING = "reward_pending"
CYCLE_STATUS_REWARD_CLAIMED = "reward_claimed"
CYCLE_STATUS_COMPLETED_WON = "completed_won"
CYCLE_STATUS_COMPLETED_LOST = "completed_lost"

VALID_GAMES = ("wheel", "quiz", "tap")
VALID_SOURCES = ("wheel", "quiz", "tap", "admin", "quiz_daily_challenge", "recruit")


def _today() -> date:
    return date.today()


# ══════════════════════════════════════════════════════════════════════════
#  Admin kill-switches
# ══════════════════════════════════════════════════════════════════════════

async def is_game_enabled(game: str) -> bool:
    """Return the current admin toggle for a game. Defaults to True."""
    if game not in VALID_GAMES:
        return True
    return await get_bool(f"{game}_enabled", True)


async def ensure_game_enabled(game: str) -> None:
    """Raise HTTP 503 if the admin has toggled the game off.

    Uses a stable error detail the frontend can detect word-for-word."""
    if not await is_game_enabled(game):
        raise HTTPException(
            status_code=503,
            detail="Ce jeu est temporairement indisponible.",
        )


# ══════════════════════════════════════════════════════════════════════════
#  Cycle helpers (mirror wheel_fortune._get_or_create_cycle without the
#  reward_pending flip — for quiz/tap we only need an active cycle)
# ══════════════════════════════════════════════════════════════════════════

async def get_active_cycle(conn, user_id: str, *, for_update: bool = False):
    """Return the active in_progress cycle (create on first access).

    Any expired cycle is flipped to its terminal state here too so the
    quiz/tap flows don't accidentally write to a dead cycle."""
    lock = " FOR UPDATE" if for_update else ""
    today = _today()
    row = await conn.fetchrow(
        f"""SELECT * FROM wheel_cycles
            WHERE user_id = $1 AND reward_status = $2{lock}""",
        user_id, CYCLE_STATUS_IN_PROGRESS,
    )
    if row and row["cycle_end_date"] < today:
        # Cycle expired — flip it and create a fresh one.
        quiz_total = int(row["quiz_answers_total"] or 0)
        quiz_correct = int(row["quiz_answers_correct"] or 0)
        quiz_ok = (
            quiz_total >= QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY
            and (quiz_correct / quiz_total) >= QUIZ_ACCURACY_THRESHOLD
        )
        if (
            int(row["points_cycle"]) >= POINTS_GOAL
            and int(row["days_played_count"]) >= DAYS_GOAL
            and quiz_ok
        ):
            new_status = CYCLE_STATUS_REWARD_PENDING
        else:
            new_status = CYCLE_STATUS_COMPLETED_LOST
        await conn.execute(
            "UPDATE wheel_cycles SET reward_status=$1, updated_at=NOW() WHERE id=$2",
            new_status, row["id"],
        )
        row = None

    if not row:
        start = today
        end = start + timedelta(days=CYCLE_LENGTH_DAYS - 1)
        row = await conn.fetchrow(
            """INSERT INTO wheel_cycles (user_id, cycle_start_date, cycle_end_date)
               VALUES ($1,$2,$3) RETURNING *""",
            user_id, start, end,
        )
    return row


# ══════════════════════════════════════════════════════════════════════════
#  Points mutation
# ══════════════════════════════════════════════════════════════════════════

async def add_points(
    conn,
    user_id: str,
    amount: int,
    source: str,
    *,
    count_as_day_played: bool = True,
    metadata: Optional[dict] = None,
) -> dict:
    """Add `amount` points to the user's active cycle, enforcing the
    sovereign clamp. Returns the updated cycle as a dict.

    `source` must be one of VALID_SOURCES and is logged in `wheel_spins`
    for auditing. Callers SHOULD be inside their own transaction and hold
    a FOR UPDATE lock — this helper does NOT acquire one.
    """
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source: {source}")

    today = _today()
    cycle = await get_active_cycle(conn, user_id, for_update=True)

    # Distinct-day counter
    new_days = int(cycle["days_played_count"])
    new_streak = int(cycle["streak_days"])
    if count_as_day_played and cycle["last_played_date"] != today:
        new_days += 1
        # Streak continues if last_played_date == today - 1
        if cycle["last_played_date"] == today - timedelta(days=1):
            new_streak += 1
        else:
            new_streak = 1

    # Sovereign clamp
    new_total_uncapped = int(cycle["points_cycle"]) + int(amount)
    if new_days < DAYS_GOAL:
        new_total = min(new_total_uncapped, POINTS_GOAL - 1)
    else:
        new_total = new_total_uncapped
    clamped_amount = new_total - int(cycle["points_cycle"])

    await conn.execute(
        """UPDATE wheel_cycles
             SET points_cycle = $1,
                 days_played_count = $2,
                 last_played_date = $3,
                 streak_days = $4,
                 updated_at = NOW()
           WHERE id = $5""",
        new_total, new_days, today, new_streak, cycle["id"],
    )

    # Audit log in wheel_spins (source column distinguishes Roue vs Quiz vs Tap)
    await conn.execute(
        """INSERT INTO wheel_spins
             (cycle_id, user_id, spin_date, prize_slot, points_awarded,
              phase, source, ip_address, user_agent)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        cycle["id"], user_id, today,
        -1,                  # no slot for quiz/tap
        int(clamped_amount),
        _phase(new_days),
        source,
        (metadata or {}).get("ip", ""),
        (metadata or {}).get("ua", "")[:512],
    )

    return {
        "cycle_id": int(cycle["id"]),
        "points_cycle": new_total,
        "points_awarded": int(clamped_amount),
        "points_clamped": clamped_amount < amount,
        "days_played_count": new_days,
        "streak_days": new_streak,
        "phase": _phase(new_days),
    }


async def register_quiz_answers(conn, user_id: str, correct: int, total: int) -> dict:
    """Record quiz performance on the active cycle. Points are added
    separately via `add_points`."""
    if correct < 0 or total < 0 or correct > total:
        raise ValueError("invalid correct/total")
    cycle = await get_active_cycle(conn, user_id, for_update=True)
    new_correct = int(cycle["quiz_answers_correct"]) + int(correct)
    new_total = int(cycle["quiz_answers_total"]) + int(total)
    await conn.execute(
        """UPDATE wheel_cycles
             SET quiz_answers_correct = $1, quiz_answers_total = $2, updated_at = NOW()
           WHERE id = $3""",
        new_correct, new_total, cycle["id"],
    )
    return {
        "quiz_answers_correct": new_correct,
        "quiz_answers_total": new_total,
        "quiz_accuracy": (new_correct / new_total) if new_total else 0.0,
    }


async def register_tap_run(conn, user_id: str) -> None:
    """Increment the tap_runs counter on the active cycle."""
    cycle = await get_active_cycle(conn, user_id, for_update=True)
    await conn.execute(
        "UPDATE wheel_cycles SET tap_runs = tap_runs + 1, updated_at = NOW() WHERE id = $1",
        cycle["id"],
    )


# ══════════════════════════════════════════════════════════════════════════
#  Eligibility
# ══════════════════════════════════════════════════════════════════════════

def quiz_accuracy(cycle: dict) -> float:
    total = int(cycle.get("quiz_answers_total") or 0)
    if total == 0:
        return 0.0
    return int(cycle.get("quiz_answers_correct") or 0) / total


def is_quiz_performance_met(cycle: dict) -> bool:
    """Cycle-level 75 % rule. Requires ≥50 answers AND ≥75 % accuracy."""
    total = int(cycle.get("quiz_answers_total") or 0)
    if total < QUIZ_MIN_ANSWERS_FOR_ELIGIBILITY:
        return False
    return quiz_accuracy(cycle) >= QUIZ_ACCURACY_THRESHOLD


def is_starter_pro_eligible(cycle: dict) -> bool:
    """THE one place where all eligibility rules live. A cycle is claim-able
    only if all three business rules pass : points + days + quiz performance.
    """
    return (
        int(cycle.get("points_cycle") or 0) >= POINTS_GOAL
        and int(cycle.get("days_played_count") or 0) >= DAYS_GOAL
        and is_quiz_performance_met(cycle)
    )


# ══════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════════════════════

def _phase(days_played: int) -> int:
    """Mirrors wheel_fortune._compute_phase."""
    if days_played <= 10:
        return 1
    if days_played <= 20:
        return 2
    return 3
