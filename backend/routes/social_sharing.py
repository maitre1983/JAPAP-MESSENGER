"""
JAPAP — Recent friends (iter141sixx)
====================================
Powers the multi-friend WhatsApp share sheet on the duel-share screen.

Returns the caller's "most actionable contacts" : a unioned, deduped,
ranked list of people the caller is most likely to invite to a duel.

Sources (with diminishing weight) :
  • mutual followers (accepted both ways)              ▶ +50
  • people the caller follows (accepted)               ▶ +30
  • people who follow the caller (accepted)            ▶ +20
  • recent direct-message conversation partners        ▶ +25 + recency
  • previous duel opponents (challenger or opponent)   ▶ +35 + recency
  • multi-attempt duel challengers (anyone who played)  ▶ +20 + recency

Each candidate keeps the most recent timestamp across all sources for
the secondary sort. Limit defaults to 12 (enough for two scrolls on
mobile) and the resulting payload is lightweight (id, name, avatar,
phone_number, last_interaction_at, score). Phone numbers are returned
in `+E.164` form (already enforced at registration) so the frontend
can build `wa.me/{phone}?text=...` links directly.
"""
from __future__ import annotations
import logging
from fastapi import APIRouter, HTTPException, Request, Query

from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/social", tags=["social-sharing"])


@router.get("/recent-friends")
async def recent_friends(
    request: Request,
    limit: int = Query(12, ge=1, le=30),
    only_with_phone: bool = Query(False),
):
    """Return the caller's top engaged contacts for the duel-share sheet."""
    me = await get_current_user(request)
    user_id = me["user_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = """
        WITH following AS (
            SELECT followed_id AS uid, 30 AS score, NOW() AS ts
              FROM user_follows
             WHERE follower_id = $1 AND status = 'accepted'
        ),
        followers AS (
            SELECT follower_id AS uid, 20 AS score, NOW() AS ts
              FROM user_follows
             WHERE followed_id = $1 AND status = 'accepted'
        ),
        recent_chats AS (
            -- Direct conversations only; pull the OTHER participant.
            SELECT cp2.user_id AS uid,
                   25 AS score,
                   c.updated_at AS ts
              FROM conversations c
              JOIN conversation_participants cp1
                    ON cp1.conv_id = c.conv_id AND cp1.user_id = $1
              JOIN conversation_participants cp2
                    ON cp2.conv_id = c.conv_id AND cp2.user_id <> $1
             WHERE c.type = 'direct'
             ORDER BY c.updated_at DESC
             LIMIT 30
        ),
        prev_duel_partners AS (
            -- Anyone the caller has already duelled (1v1) — exclude self.
            SELECT CASE WHEN d.challenger_id = $1 THEN d.opponent_id
                        ELSE d.challenger_id END AS uid,
                   35 AS score,
                   COALESCE(d.completed_at, d.created_at) AS ts
              FROM duels d
             WHERE (d.challenger_id = $1 OR d.opponent_id = $1)
               AND d.opponent_id IS NOT NULL
               AND d.opponent_id <> COALESCE(d.challenger_id, '')
             ORDER BY ts DESC
             LIMIT 30
        ),
        multi_duel_players AS (
            -- People who played the caller's multi-attempts duels (so they're
            -- already engaged with the caller's challenges).
            SELECT da.user_id AS uid, 20 AS score, da.submitted_at AS ts
              FROM duel_attempts da
              JOIN duels d ON d.id = da.duel_id
             WHERE d.challenger_id = $1
             ORDER BY da.submitted_at DESC
             LIMIT 30
        ),
        candidates AS (
            SELECT * FROM following
            UNION ALL SELECT * FROM followers
            UNION ALL SELECT * FROM recent_chats
            UNION ALL SELECT * FROM prev_duel_partners
            UNION ALL SELECT * FROM multi_duel_players
        ),
        ranked AS (
            SELECT uid,
                   SUM(score) AS score_total,
                   MAX(ts) AS last_interaction_at
              FROM candidates
             WHERE uid IS NOT NULL AND uid <> $1
             GROUP BY uid
        )
        SELECT u.user_id, u.first_name, u.last_name, u.username, u.avatar,
               u.phone_number, u.email,
               r.score_total, r.last_interaction_at
          FROM ranked r
          JOIN users u ON u.user_id = r.uid
         WHERE u.is_active = TRUE
        """
        params = [user_id]
        if only_with_phone:
            sql += " AND u.phone_number IS NOT NULL AND u.phone_number <> ''"
        sql += """
         ORDER BY r.score_total DESC, r.last_interaction_at DESC NULLS LAST
         LIMIT $2
        """
        params.append(int(limit))
        try:
            rows = await conn.fetch(sql, *params)
        except Exception as e:
            # Defensive: if `duel_attempts` doesn't exist yet (fresh deploy),
            # silently re-run without that branch instead of erroring.
            logger.warning("recent-friends fallback (no multi_attempts): %s", e)
            sql2 = sql.replace(
                "UNION ALL SELECT * FROM multi_duel_players", ""
            ).replace(
                """,
        multi_duel_players AS (
            -- People who played the caller's multi-attempts duels (so they're
            -- already engaged with the caller's challenges).
            SELECT da.user_id AS uid, 20 AS score, da.submitted_at AS ts
              FROM duel_attempts da
              JOIN duels d ON d.id = da.duel_id
             WHERE d.challenger_id = $1
             ORDER BY da.submitted_at DESC
             LIMIT 30
        )""", "",
            )
            rows = await conn.fetch(sql2, *params)

        items = []
        for r in rows:
            name = (f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
                    or r["username"] or r["email"] or r["user_id"])
            items.append({
                "user_id": r["user_id"],
                "name": name,
                "avatar": r["avatar"],
                "phone_number": r["phone_number"] or None,
                "has_phone": bool(r["phone_number"]),
                "score": int(r["score_total"]),
                "last_interaction_at": (
                    r["last_interaction_at"].isoformat()
                    if r["last_interaction_at"] else None
                ),
            })
        return {"items": items, "count": len(items)}
