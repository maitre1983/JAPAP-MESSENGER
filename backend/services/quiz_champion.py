"""
Quiz Champion par Pays — service core (iter123, Phase 3.A).
==========================================================

Tables (DDL idempotent):
  - quiz_country_champions(country_code PK, user_id, promoted_at, source,
      refusal_count_consecutive, refusal_count_30d_cached, last_refusal_at,
      demoted_at, demoted_reason)
  - quiz_champion_challenges(id PK, challenger_user_id, champion_user_id,
      country_code, session_id, mode, stake_amount, stake_currency,
      commission_pct, status, challenger_run_id, champion_run_id,
      challenger_score, champion_score, winner_user_id,
      created_at, accepted_at, refused_at, expires_at, completed_at, notes)
  - quiz_champion_refusals(id PK, champion_user_id, challenge_id, refused_at)

Champion promotion rule (admin-tunable):
  Top-1 player by quiz points over the last `quiz_champion_window_days`
  (default 7) per country_code. Run by a worker tick (idempotent) — every
  promotion change resets the consecutive refusal counter.

Refusal demotion rule:
  - Each refusal increments refusal_count_consecutive AND inserts a
    quiz_champion_refusals row (used to compute the rolling 30d count
    via SQL).
  - If consecutive >= MAX_REFUSALS_BEFORE_DEMOTION OR
    rolling_30d >= MAX_REFUSALS_BEFORE_DEMOTION → champion demoted (set
    demoted_at, demoted_reason). Next promotion cycle picks the next #1.

NB: A demoted user CAN come back as champion if they earn the top-1 spot
again — refusal counters reset on (re)promotion.
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from database import get_pool

logger = logging.getLogger(__name__)

# Tunables (admin can override via admin_settings).
DEFAULT_WINDOW_DAYS = 7
MAX_REFUSALS_BEFORE_DEMOTION = 5  # 4 refus autorisés, 5e = démotion
DEFAULT_CHALLENGE_EXPIRY_HOURS = 24
DEFAULT_COMMISSION_PCT = 10.0  # JAPAP fee on paid challenges
DEFAULT_STAKE_MIN = 0.50  # USD
DEFAULT_STAKE_MAX = 100.0
ROLLING_REFUSAL_WINDOW_DAYS = 30

# Status lifecycle for a challenge.
STATUS_PENDING            = "pending"            # created, awaiting champion
STATUS_AWAITING_ACCEPTOR  = "awaiting_acceptor"  # iter228 — open challenge: A played, awaits any acceptor
STATUS_ACCEPTED           = "accepted"           # champion accepted, both can play
STATUS_REFUSED            = "refused"            # champion refused — refusal counted
STATUS_EXPIRED            = "expired"            # 24h elapsed without action
STATUS_CHALLENGER_PLAYED  = "challenger_played"  # only challenger has played
STATUS_CHAMPION_PLAYED    = "champion_played"    # only champion has played
STATUS_COMPLETED          = "completed"          # both played; winner set
STATUS_CANCELLED          = "cancelled"          # admin/system cancel (e.g., escrow refund)

OPEN_STATUSES = (STATUS_PENDING, STATUS_AWAITING_ACCEPTOR, STATUS_ACCEPTED,
                 STATUS_CHALLENGER_PLAYED, STATUS_CHAMPION_PLAYED)
CLOSED_STATUSES = (STATUS_COMPLETED, STATUS_REFUSED, STATUS_EXPIRED, STATUS_CANCELLED)


_DDL = [
    """CREATE TABLE IF NOT EXISTS quiz_country_champions (
         country_code              VARCHAR(2)  PRIMARY KEY,
         user_id                   VARCHAR(32) NOT NULL,
         promoted_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         source                    VARCHAR(24) NOT NULL DEFAULT 'auto_top1',
         refusal_count_consecutive INTEGER     NOT NULL DEFAULT 0,
         last_refusal_at           TIMESTAMPTZ,
         demoted_at                TIMESTAMPTZ,
         demoted_reason            VARCHAR(64),
         updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
       )""",
    "CREATE INDEX IF NOT EXISTS idx_qcc_user ON quiz_country_champions(user_id)",

    """CREATE TABLE IF NOT EXISTS quiz_champion_challenges (
         id BIGSERIAL PRIMARY KEY,
         challenge_id        VARCHAR(32) UNIQUE NOT NULL,
         challenger_user_id  VARCHAR(32) NOT NULL,
         champion_user_id    VARCHAR(32) NOT NULL,
         country_code        VARCHAR(2)  NOT NULL,
         session_id          BIGINT      NOT NULL,
         mode                VARCHAR(8)  NOT NULL DEFAULT 'free',
         stake_amount        NUMERIC(14,4) NOT NULL DEFAULT 0,
         stake_currency      VARCHAR(8)  NOT NULL DEFAULT 'USD',
         commission_pct      NUMERIC(5,2) NOT NULL DEFAULT 10.00,
         status              VARCHAR(24) NOT NULL DEFAULT 'pending',
         challenger_run_id   BIGINT,
         champion_run_id     BIGINT,
         challenger_score    INTEGER,
         champion_score      INTEGER,
         winner_user_id      VARCHAR(32),
         escrow_locked       BOOLEAN     NOT NULL DEFAULT FALSE,
         escrow_payout_tx_id VARCHAR(40),
         commission_tx_id    VARCHAR(40),
         created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         accepted_at         TIMESTAMPTZ,
         refused_at          TIMESTAMPTZ,
         expires_at          TIMESTAMPTZ,
         completed_at        TIMESTAMPTZ,
         notes               TEXT
       )""",
    "CREATE INDEX IF NOT EXISTS idx_qcch_challenger ON quiz_champion_challenges(challenger_user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_qcch_champion   ON quiz_champion_challenges(champion_user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_qcch_status     ON quiz_champion_challenges(status, created_at DESC)",
    # iter228 — Allow open challenges (champion_user_id NULL until claimed).
    "ALTER TABLE quiz_champion_challenges ALTER COLUMN champion_user_id DROP NOT NULL",
    # Anti-spam: at most ONE open challenge per (challenger, champion) pair.
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_qcch_open_pair
         ON quiz_champion_challenges (challenger_user_id, champion_user_id)
         WHERE status IN ('pending','accepted','challenger_played','champion_played')""",

    """CREATE TABLE IF NOT EXISTS quiz_champion_refusals (
         id BIGSERIAL PRIMARY KEY,
         champion_user_id  VARCHAR(32) NOT NULL,
         challenge_id      VARCHAR(32) NOT NULL,
         country_code      VARCHAR(2)  NOT NULL,
         refused_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
       )""",
    "CREATE INDEX IF NOT EXISTS idx_qcr_champion_time ON quiz_champion_refusals(champion_user_id, refused_at DESC)",
]


_ddl_done = False


async def ensure_ddl(conn) -> None:
    global _ddl_done
    if _ddl_done:
        return
    for s in _DDL:
        await conn.execute(s)
    _ddl_done = True


def _new_challenge_id() -> str:
    return f"qcc_{uuid.uuid4().hex[:16]}"


# ─────────────────────────────────────────────────────────────────────
# Champion read
# ─────────────────────────────────────────────────────────────────────

async def get_country_champion(country_code: str) -> Optional[dict]:
    """Return the active champion for a country (or None)."""
    if not country_code:
        return None
    cc = country_code.upper()[:2]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        row = await conn.fetchrow(
            """SELECT c.country_code, c.user_id, c.promoted_at, c.source,
                      c.refusal_count_consecutive, c.last_refusal_at,
                      u.first_name, u.last_name, u.username, u.avatar, u.is_pro
                 FROM quiz_country_champions c
                 LEFT JOIN users u ON u.user_id = c.user_id
                WHERE c.country_code = $1 AND c.demoted_at IS NULL""",
            cc,
        )
        if not row:
            return None
        # Compute rolling 30d refusal count fresh, scoped to the CURRENT
        # promotion window (refusals before this user's promoted_at do NOT
        # count — a re-promoted champion starts with a clean slate).
        rolling = await conn.fetchval(
            """SELECT COUNT(*) FROM quiz_champion_refusals
                WHERE champion_user_id = $1
                  AND country_code = $2
                  AND refused_at >= $3
                  AND refused_at > NOW() - ($4 || ' days')::interval""",
            row["user_id"], row["country_code"], row["promoted_at"],
            str(ROLLING_REFUSAL_WINDOW_DAYS),
        ) or 0
        return {
            "country_code": row["country_code"],
            "user_id": row["user_id"],
            "promoted_at": row["promoted_at"].isoformat() if row["promoted_at"] else None,
            "source": row["source"],
            "refusal_count_consecutive": int(row["refusal_count_consecutive"] or 0),
            "refusal_count_30d": int(rolling),
            "last_refusal_at": row["last_refusal_at"].isoformat() if row["last_refusal_at"] else None,
            "user": {
                "user_id": row["user_id"],
                "first_name": row["first_name"] or "",
                "last_name": row["last_name"] or "",
                "username": row["username"] or "",
                "avatar": row["avatar"] or "",
                "is_pro": bool(row["is_pro"]),
            },
        }


# ─────────────────────────────────────────────────────────────────────
# Champion promotion (auto top-1 over last N days per country)
# ─────────────────────────────────────────────────────────────────────

async def promote_champions(window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Recompute champions for all active countries based on top-1 quiz
    points over the last `window_days`. Idempotent; safe to call frequently.

    Returns {countries_evaluated, promoted: [...], unchanged: [...], demoted: [...]}.
    """
    window_days = max(1, min(int(window_days), 90))
    pool = await get_pool()
    promoted, unchanged, demoted = [], [], []
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        # Distinct countries that had at least 1 quiz run during the window.
        rows = await conn.fetch(
            """SELECT u.country_code, r.user_id, SUM(r.points_awarded) AS pts,
                      SUM(r.correct_count) AS correct, COUNT(*) AS runs
                 FROM quiz_user_runs r
                 JOIN users u ON u.user_id = r.user_id
                WHERE r.submitted_at IS NOT NULL
                  AND r.started_at > NOW() - ($1 || ' days')::interval
                  AND COALESCE(NULLIF(u.country_code, ''), '') <> ''
             GROUP BY u.country_code, r.user_id
             ORDER BY u.country_code, pts DESC, correct DESC, runs DESC""",
            str(window_days),
        )
        # Group by country, take top-1 per country
        top_by_country: dict[str, dict[str, Any]] = {}
        for r in rows:
            cc = (r["country_code"] or "").upper()[:2]
            if not cc or cc in top_by_country:
                continue
            top_by_country[cc] = {
                "user_id": r["user_id"],
                "points": int(r["pts"] or 0),
                "correct": int(r["correct"] or 0),
                "runs": int(r["runs"] or 0),
            }
        # Upsert per country
        for cc, top in top_by_country.items():
            cur = await conn.fetchrow(
                """SELECT user_id, demoted_at FROM quiz_country_champions
                    WHERE country_code = $1""",
                cc,
            )
            if cur and cur["user_id"] == top["user_id"] and cur["demoted_at"] is None:
                unchanged.append({"country_code": cc, "user_id": top["user_id"]})
                continue
            # New or different champion → upsert + reset refusal counter.
            await conn.execute(
                """INSERT INTO quiz_country_champions
                     (country_code, user_id, promoted_at, source,
                      refusal_count_consecutive, last_refusal_at,
                      demoted_at, demoted_reason, updated_at)
                   VALUES ($1, $2, NOW(), 'auto_top1', 0, NULL, NULL, NULL, NOW())
                   ON CONFLICT (country_code) DO UPDATE
                      SET user_id = EXCLUDED.user_id,
                          promoted_at = NOW(),
                          source = 'auto_top1',
                          refusal_count_consecutive = 0,
                          last_refusal_at = NULL,
                          demoted_at = NULL,
                          demoted_reason = NULL,
                          updated_at = NOW()""",
                cc, top["user_id"],
            )
            promoted.append({
                "country_code": cc,
                "user_id": top["user_id"],
                "previous_user_id": cur["user_id"] if cur else None,
                "points": top["points"],
            })
        return {
            "window_days": window_days,
            "countries_evaluated": len(top_by_country),
            "promoted": promoted,
            "unchanged": unchanged,
            "demoted": demoted,
        }


async def admin_set_champion(country_code: str, user_id: str) -> dict:
    """Manual override (admin) — promote a user as country champion."""
    cc = country_code.upper()[:2]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        u = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        if not u:
            raise ValueError("Utilisateur introuvable.")
        await conn.execute(
            """INSERT INTO quiz_country_champions
                 (country_code, user_id, promoted_at, source,
                  refusal_count_consecutive, last_refusal_at,
                  demoted_at, demoted_reason, updated_at)
               VALUES ($1, $2, NOW(), 'admin_set', 0, NULL, NULL, NULL, NOW())
               ON CONFLICT (country_code) DO UPDATE
                  SET user_id = EXCLUDED.user_id,
                      promoted_at = NOW(),
                      source = 'admin_set',
                      refusal_count_consecutive = 0,
                      last_refusal_at = NULL,
                      demoted_at = NULL,
                      demoted_reason = NULL,
                      updated_at = NOW()""",
            cc, user_id,
        )
    return await get_country_champion(cc) or {}


async def admin_demote_champion(country_code: str, reason: str = "admin_demote") -> dict:
    cc = country_code.upper()[:2]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_ddl(conn)
        await conn.execute(
            """UPDATE quiz_country_champions
                  SET demoted_at = NOW(), demoted_reason = $2, updated_at = NOW()
                WHERE country_code = $1 AND demoted_at IS NULL""",
            cc, reason[:64],
        )
    return {"country_code": cc, "demoted": True, "reason": reason}


# ─────────────────────────────────────────────────────────────────────
# Refusal counters / demotion
# ─────────────────────────────────────────────────────────────────────

async def count_recent_refusals(conn, champion_user_id: str,
                                country_code: Optional[str] = None) -> int:
    """Number of refusals in the rolling 30d window.

    iter124 — Scoped to refusals AFTER the current champion's `promoted_at`
    (per country) so a re-promoted user starts with a clean slate. If the
    user is not currently a champion of any country, falls back to the raw
    30d window (used by tooling/admin views).
    """
    if country_code:
        n = await conn.fetchval(
            """SELECT COUNT(*) FROM quiz_champion_refusals r
                 JOIN quiz_country_champions c
                   ON c.country_code = r.country_code
                  AND c.user_id = r.champion_user_id
                WHERE r.champion_user_id = $1
                  AND c.country_code = $2
                  AND r.refused_at > NOW() - ($3 || ' days')::interval
                  AND r.refused_at >= c.promoted_at""",
            champion_user_id, country_code.upper()[:2],
            str(ROLLING_REFUSAL_WINDOW_DAYS),
        ) or 0
    else:
        # No country context — try to find one. If user is currently a
        # champion of any country, scope to that promotion.
        row = await conn.fetchrow(
            """SELECT country_code, promoted_at FROM quiz_country_champions
                WHERE user_id = $1 AND demoted_at IS NULL
             ORDER BY promoted_at DESC LIMIT 1""",
            champion_user_id,
        )
        if row:
            n = await conn.fetchval(
                """SELECT COUNT(*) FROM quiz_champion_refusals
                    WHERE champion_user_id = $1
                      AND country_code = $2
                      AND refused_at >= $3
                      AND refused_at > NOW() - ($4 || ' days')::interval""",
                champion_user_id, row["country_code"], row["promoted_at"],
                str(ROLLING_REFUSAL_WINDOW_DAYS),
            ) or 0
        else:
            # Demoted everywhere — full window for analytics.
            n = await conn.fetchval(
                """SELECT COUNT(*) FROM quiz_champion_refusals
                    WHERE champion_user_id = $1
                      AND refused_at > NOW() - ($2 || ' days')::interval""",
                champion_user_id, str(ROLLING_REFUSAL_WINDOW_DAYS),
            ) or 0
    return int(n)


async def record_refusal_and_maybe_demote(
    conn, *, champion_user_id: str, country_code: str, challenge_id: str,
) -> dict:
    """Insert a refusal row, bump the consecutive counter, and demote if
    the threshold (consecutive >=5 OR rolling_30d >=5) is reached.
    MUST be called inside an active transaction on `conn`.
    """
    cc = country_code.upper()[:2]
    await conn.execute(
        """INSERT INTO quiz_champion_refusals
             (champion_user_id, challenge_id, country_code, refused_at)
           VALUES ($1, $2, $3, NOW())""",
        champion_user_id, challenge_id, cc,
    )
    # Bump consecutive counter ATOMICALLY and read it back.
    cur = await conn.fetchrow(
        """UPDATE quiz_country_champions
              SET refusal_count_consecutive = refusal_count_consecutive + 1,
                  last_refusal_at = NOW(),
                  updated_at = NOW()
            WHERE country_code = $1 AND user_id = $2 AND demoted_at IS NULL
            RETURNING refusal_count_consecutive""",
        cc, champion_user_id,
    )
    consecutive = int(cur["refusal_count_consecutive"]) if cur else 0
    rolling = await count_recent_refusals(conn, champion_user_id, country_code=cc)
    demoted = False
    reason = None
    if consecutive >= MAX_REFUSALS_BEFORE_DEMOTION:
        reason = "consecutive_refusals"
        demoted = True
    elif rolling >= MAX_REFUSALS_BEFORE_DEMOTION:
        reason = "rolling_30d_refusals"
        demoted = True
    if demoted:
        await conn.execute(
            """UPDATE quiz_country_champions
                  SET demoted_at = NOW(), demoted_reason = $2, updated_at = NOW()
                WHERE country_code = $1 AND user_id = $3""",
            cc, reason, champion_user_id,
        )
    return {
        "consecutive": consecutive,
        "rolling_30d": rolling,
        "demoted": demoted,
        "reason": reason,
    }
