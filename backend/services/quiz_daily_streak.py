"""
Quiz Daily Streak service — iter130, Phase 3.E.
================================================
Tracks per-user streaks of daily challenge plays. A streak ticks +1 if the
user plays on the day immediately after their previous play, otherwise it
resets to 1.

Streak counter is independent of points awarded — the points engine reads
the current_streak to compute the streak bonus on /daily-challenge/submit.

Schema (DDL self-heal):
  daily_quiz_streak (
    user_id          VARCHAR(32) PRIMARY KEY,
    current_streak   INT         NOT NULL DEFAULT 0,
    longest_streak   INT         NOT NULL DEFAULT 0,
    last_played_date DATE,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
  )
"""
from __future__ import annotations
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS daily_quiz_streak (
  user_id          VARCHAR(32) PRIMARY KEY,
  current_streak   INT         NOT NULL DEFAULT 0,
  longest_streak   INT         NOT NULL DEFAULT 0,
  last_played_date DATE,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_ddl_done = False


async def ensure_ddl(conn) -> None:
    global _ddl_done
    if _ddl_done:
        return
    await conn.execute(_DDL)
    _ddl_done = True


async def get_streak(conn, user_id: str) -> dict:
    await ensure_ddl(conn)
    row = await conn.fetchrow(
        """SELECT current_streak, longest_streak, last_played_date
             FROM daily_quiz_streak WHERE user_id = $1""",
        user_id,
    )
    if not row:
        return {"current_streak": 0, "longest_streak": 0, "last_played_date": None}
    return {
        "current_streak": int(row["current_streak"] or 0),
        "longest_streak": int(row["longest_streak"] or 0),
        "last_played_date": row["last_played_date"].isoformat()
        if row["last_played_date"] else None,
    }


async def tick_streak(conn, user_id: str, play_date: date) -> dict:
    """Increment / reset streak based on play_date. Returns the new streak.

    Caller must be inside a transaction. Uses ON CONFLICT for atomicity.
    """
    await ensure_ddl(conn)
    row = await conn.fetchrow(
        """SELECT current_streak, longest_streak, last_played_date
             FROM daily_quiz_streak WHERE user_id = $1 FOR UPDATE""",
        user_id,
    )
    if not row:
        new_current = 1
        new_longest = 1
    else:
        last = row["last_played_date"]
        cur = int(row["current_streak"] or 0)
        longest = int(row["longest_streak"] or 0)
        if last is None:
            new_current = 1
        elif last == play_date:
            # Same day — defensive idempotency (shouldn't happen because
            # quiz_daily_challenge_runs blocks dual plays).
            new_current = cur if cur > 0 else 1
        elif last == play_date - timedelta(days=1):
            new_current = cur + 1
        else:
            new_current = 1  # gap → reset
        new_longest = max(longest, new_current)
    await conn.execute(
        """INSERT INTO daily_quiz_streak (user_id, current_streak, longest_streak,
                                           last_played_date, updated_at)
           VALUES ($1, $2, $3, $4, NOW())
           ON CONFLICT (user_id) DO UPDATE
              SET current_streak   = EXCLUDED.current_streak,
                  longest_streak   = EXCLUDED.longest_streak,
                  last_played_date = EXCLUDED.last_played_date,
                  updated_at       = NOW()""",
        user_id, new_current, new_longest, play_date,
    )
    return {"current_streak": new_current, "longest_streak": new_longest,
            "last_played_date": play_date.isoformat()}


__all__ = ["ensure_ddl", "get_streak", "tick_streak"]
