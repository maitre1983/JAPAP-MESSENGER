"""
JAPAP — Recruit (Viral) Rewards
================================
iter141seven.

Closes the viral loop on the duel-share sheet :
  • For each *new* friend who clicks the share link AND submits an
    attempt on a duel (1v1 opponent OR multi-attempt challenger), the
    initiator earns `recruit_per_friend_points` (default 50, admin
    configurable).
  • When the same initiator hits `recruit_buzz_threshold` distinct
    recruits on a single duel, they additionally earn
    `recruit_buzz_bonus_points` (default 200) and unlock the
    `recruit_buzz_badge_label` badge (default "Roi du Buzz").

All thresholds + reward values are admin-configurable via
`/api/admin/recruit/settings` and stored in the existing
`admin_settings` key/value store. Idempotent: each (initiator, duel,
recruit_user) tuple is credited at most once.

Two public functions :
  • get_recruit_settings()
  • record_recruit_credit(conn, *, initiator_user_id, recruit_user_id,
                          duel_id, source_kind)
        → returns {"awarded_points": int, "buzz_unlocked": bool}
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from services.settings_service import get_setting, set_setting
from services.points_service import add_points

logger = logging.getLogger(__name__)

RECRUIT_DEFAULTS: dict[str, Any] = {
    "recruit_enabled": True,
    "recruit_per_friend_points": 50,            # awarded on each first-time recruit
    "recruit_buzz_threshold": 3,                # # recruits required for the buzz bonus
    "recruit_buzz_bonus_points": 200,           # extra points on threshold reach
    "recruit_buzz_badge_label": "Roi du Buzz",  # human-readable badge name
    "recruit_buzz_badge_emoji": "👑",
    "recruit_leaderboard_period_days": 7,       # rolling window for the leaderboard
    "recruit_leaderboard_size": 10,
}

RECRUIT_BOUNDS: dict[str, tuple[int, int]] = {
    "recruit_per_friend_points": (0, 5000),
    "recruit_buzz_threshold": (1, 100),
    "recruit_buzz_bonus_points": (0, 50000),
    "recruit_leaderboard_period_days": (1, 365),
    "recruit_leaderboard_size": (1, 100),
}


def _parse(raw: Optional[str], type_: type, default: Any) -> Any:
    if raw is None:
        return default
    try:
        if type_ is bool:
            return str(raw).strip().lower() in ("true", "1", "yes", "on")
        if type_ is int:
            return int(str(raw).strip())
        return raw
    except (TypeError, ValueError):
        return default


async def get_recruit_settings() -> dict:
    out: dict[str, Any] = {}
    for key, default in RECRUIT_DEFAULTS.items():
        raw = await get_setting(key)
        out[key] = _parse(raw, type(default), default)
    # Clamp numeric values defensively.
    for k, (lo, hi) in RECRUIT_BOUNDS.items():
        if k in out and isinstance(out[k], int):
            out[k] = max(lo, min(hi, int(out[k])))
    return out


async def update_recruit_settings(updates: dict, admin_id: str = "") -> dict:
    """Persist a partial recruit config (admin-only)."""
    for k in updates:
        if k not in RECRUIT_DEFAULTS:
            raise ValueError(f"Clé inconnue : {k}")
    for k, v in updates.items():
        default = RECRUIT_DEFAULTS[k]
        if isinstance(default, bool):
            v = bool(v)
        elif isinstance(default, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} doit être un entier.")
            if k in RECRUIT_BOUNDS:
                lo, hi = RECRUIT_BOUNDS[k]
                if v < lo or v > hi:
                    raise ValueError(f"{k} doit être entre {lo} et {hi}.")
        else:
            v = str(v)
        # admin_settings is a string store
        if isinstance(v, bool):
            await set_setting(k, "true" if v else "false")
        else:
            await set_setting(k, str(v))
    logger.info("admin %s updated recruit settings: %s", admin_id or "?", list(updates.keys()))
    return await get_recruit_settings()


# ───────────────────────────────────────────────────────────────────────
#  DDL — recruit_credits + user_badges (badge ledger)
# ───────────────────────────────────────────────────────────────────────

DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS recruit_credits (
        id              BIGSERIAL PRIMARY KEY,
        initiator_id    VARCHAR(64) NOT NULL,
        recruit_id      VARCHAR(64) NOT NULL,
        duel_id         BIGINT      NOT NULL,
        source_kind     VARCHAR(32) NOT NULL,    -- 'multi_attempts' | 'classic_1v1'
        points_awarded  INTEGER     NOT NULL DEFAULT 0,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (initiator_id, recruit_id, duel_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_recruit_credits_initiator_time ON recruit_credits(initiator_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_recruit_credits_duel ON recruit_credits(duel_id)",
    """
    CREATE TABLE IF NOT EXISTS recruit_buzz_badges (
        id          BIGSERIAL PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        duel_id     BIGINT      NOT NULL,
        label       VARCHAR(64) NOT NULL,
        emoji       VARCHAR(8),
        awarded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, duel_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_buzz_badges_user_time ON recruit_buzz_badges(user_id, awarded_at DESC)",
)


async def ensure_recruit_ddl(conn) -> None:
    for stmt in DDL_STATEMENTS:
        await conn.execute(stmt)


# ───────────────────────────────────────────────────────────────────────
#  Public API — record a recruit
# ───────────────────────────────────────────────────────────────────────

async def record_recruit_credit(
    conn,
    *,
    initiator_user_id: str,
    recruit_user_id: str,
    duel_id: int,
    source_kind: str,
) -> dict:
    """Idempotent: at most one credit per (initiator, recruit, duel).
    Returns {"awarded_points": int, "buzz_unlocked": bool}.
    Caller MUST be inside a transaction — this function uses
    `add_points` which expects a live conn.
    """
    if not initiator_user_id or initiator_user_id == recruit_user_id:
        return {"awarded_points": 0, "buzz_unlocked": False}

    await ensure_recruit_ddl(conn)
    cfg = await get_recruit_settings()
    if not cfg.get("recruit_enabled", True):
        return {"awarded_points": 0, "buzz_unlocked": False}

    points = int(cfg.get("recruit_per_friend_points", 50))
    threshold = int(cfg.get("recruit_buzz_threshold", 3))
    bonus = int(cfg.get("recruit_buzz_bonus_points", 200))
    badge_label = str(cfg.get("recruit_buzz_badge_label", "Roi du Buzz"))
    badge_emoji = str(cfg.get("recruit_buzz_badge_emoji", "👑"))

    # Insert credit (idempotent via UNIQUE).
    inserted = await conn.fetchval(
        """INSERT INTO recruit_credits
              (initiator_id, recruit_id, duel_id, source_kind, points_awarded)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (initiator_id, recruit_id, duel_id) DO NOTHING
           RETURNING id""",
        initiator_user_id, recruit_user_id, int(duel_id), source_kind, points,
    )
    if inserted is None:
        # Already credited — nothing to do.
        return {"awarded_points": 0, "buzz_unlocked": False}

    # Award the per-friend points to the initiator.
    if points > 0:
        try:
            await add_points(
                conn, initiator_user_id, points, source="recruit",
                count_as_day_played=False,
                metadata={
                    "kind": "recruit_friend",
                    "recruit_id": recruit_user_id,
                    "duel_id": int(duel_id),
                    "source_kind": source_kind,
                },
            )
        except Exception as e:  # never let points-failure rollback the duel
            logger.warning("recruit add_points failed: %s", e)

    # Buzz threshold check : unique recruits on this duel.
    distinct_recruits = await conn.fetchval(
        "SELECT COUNT(*) FROM recruit_credits WHERE initiator_id = $1 AND duel_id = $2",
        initiator_user_id, int(duel_id),
    ) or 0
    buzz_unlocked = False
    if int(distinct_recruits) >= threshold:
        # Atomically claim the badge slot for this (user, duel) — UNIQUE keeps idempotency.
        claimed = await conn.fetchval(
            """INSERT INTO recruit_buzz_badges (user_id, duel_id, label, emoji)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (user_id, duel_id) DO NOTHING
               RETURNING id""",
            initiator_user_id, int(duel_id), badge_label, badge_emoji,
        )
        if claimed is not None:
            buzz_unlocked = True
            if bonus > 0:
                try:
                    await add_points(
                        conn, initiator_user_id, bonus, source="recruit",
                        count_as_day_played=False,
                        metadata={"kind": "recruit_buzz", "duel_id": int(duel_id),
                                  "label": badge_label},
                    )
                except Exception as e:
                    logger.warning("recruit buzz add_points failed: %s", e)

    return {"awarded_points": points, "buzz_unlocked": buzz_unlocked}


# ───────────────────────────────────────────────────────────────────────
#  Public API — read views (leaderboard + me)
# ───────────────────────────────────────────────────────────────────────

async def get_recruit_leaderboard(conn, *, period_days: int | None = None,
                                  limit: int | None = None) -> list[dict]:
    cfg = await get_recruit_settings()
    days = int(period_days if period_days is not None else cfg["recruit_leaderboard_period_days"])
    n = int(limit if limit is not None else cfg["recruit_leaderboard_size"])
    days = max(1, min(365, days))
    n = max(1, min(100, n))
    rows = await conn.fetch(
        """SELECT rc.initiator_id AS user_id,
                  COUNT(*)        AS recruits,
                  COALESCE(SUM(rc.points_awarded), 0) AS points,
                  u.first_name, u.last_name, u.username, u.avatar
             FROM recruit_credits rc
             LEFT JOIN users u ON u.user_id = rc.initiator_id
            WHERE rc.created_at >= NOW() - ($1 * INTERVAL '1 day')
            GROUP BY rc.initiator_id, u.first_name, u.last_name, u.username, u.avatar
            ORDER BY recruits DESC, points DESC
            LIMIT $2""",
        days, n,
    )
    items = []
    for i, r in enumerate(rows):
        name = (f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
                or r["username"] or r["user_id"])
        items.append({
            "rank": i + 1,
            "user_id": r["user_id"],
            "name": name,
            "avatar": r["avatar"],
            "recruits": int(r["recruits"]),
            "points": int(r["points"]),
        })
    return items


async def get_my_recruit_stats(conn, user_id: str) -> dict:
    cfg = await get_recruit_settings()
    days = int(cfg["recruit_leaderboard_period_days"])
    week_row = await conn.fetchrow(
        """SELECT COUNT(*) AS recruits, COALESCE(SUM(points_awarded), 0) AS points
             FROM recruit_credits
            WHERE initiator_id = $1
              AND created_at >= NOW() - ($2 * INTERVAL '1 day')""",
        user_id, days,
    )
    all_row = await conn.fetchrow(
        """SELECT COUNT(*) AS recruits, COALESCE(SUM(points_awarded), 0) AS points
             FROM recruit_credits
            WHERE initiator_id = $1""",
        user_id,
    )
    badges = await conn.fetch(
        """SELECT duel_id, label, emoji, awarded_at FROM recruit_buzz_badges
            WHERE user_id = $1 ORDER BY awarded_at DESC LIMIT 50""",
        user_id,
    )
    return {
        "settings": cfg,
        "period_days": days,
        "week_recruits": int(week_row["recruits"] or 0),
        "week_points": int(week_row["points"] or 0),
        "total_recruits": int(all_row["recruits"] or 0),
        "total_points": int(all_row["points"] or 0),
        "badges": [{
            "duel_id": int(b["duel_id"]),
            "label": b["label"],
            "emoji": b["emoji"],
            "awarded_at": b["awarded_at"].isoformat() if b["awarded_at"] else None,
        } for b in badges],
        "badges_count": len(badges),
    }
