"""
JAPAP — Crowdfunding recruiter attribution service (iter169)
=============================================================
Tracks who brought whom into the crowdfunding flow during a cycle, and
awards XP + badges (NEVER cash) to the most active recruiters.

CEO-validated rules:
  • Attribution covers the full cycle window (vote opens → vote closes).
  • A user CANNOT recruit for their own project (self-credit blocked).
  • Reward: badge + XP only. No cash.
  • Anti-fraud: max 3 distinct visit logs per (ip_hash, inviter, cycle).
  • A given recruit user is counted ONCE per recruiter per cycle
    (UNIQUE on crowdfunding_recruit_credits).

Public surface:
  • record_invite_visit(...) — called by the share-redirect endpoint.
  • try_credit_recruit(...) — called atomically inside the vote tx.
  • cycle_leaderboard(cycle_id, limit) — top recruiters (recruits_count desc).
  • award_tier_badges(cycle_id) — assigns Bronze/Silver/Gold/Platinum
    badges based on recruits count thresholds. Idempotent per (user, cycle, tier).
  • my_progress(user_id, cycle_id) — viewer's own count + tier + next-tier hint.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Tier thresholds — keep stairs reachable enough that mobile users feel
# rewarded after sharing 5-10 messages. Tiers are inclusive ranges
# (Bronze: 3+, Silver: 10+, Gold: 25+, Platinum: 50+).
RECRUITER_TIERS = [
    {"key": "bronze",   "label": "Bronze",   "emoji": "🥉", "threshold": 3,  "xp": 50},
    {"key": "silver",   "label": "Silver",   "emoji": "🥈", "threshold": 10, "xp": 150},
    {"key": "gold",     "label": "Gold",     "emoji": "🥇", "threshold": 25, "xp": 400},
    {"key": "platinum", "label": "Platinum", "emoji": "💎", "threshold": 50, "xp": 1000},
]

VISITS_PER_IP_CAP = 3  # iter169 — anti-fraud cap


async def record_invite_visit(conn, *, cycle_id: str, inviter_id: str,
                              project_slug: str, visitor_user_id: Optional[str],
                              ip_hash: str, user_agent_hash: str,
                              utm_source: Optional[str] = None) -> dict:
    """Log an invite-link click. Soft-rate-limited per (ip, inviter, cycle).

    Returns:
        {recorded: bool, reason: str|None, visits_count: int}
    """
    if not inviter_id or not cycle_id:
        return {"recorded": False, "reason": "missing_inviter_or_cycle", "visits_count": 0}

    # iter170 — Race-safe anti-fraud: serialise concurrent visits sharing the
    # same (ip_hash, inviter, cycle) triple via a transaction-scoped advisory
    # lock. Without this, two parallel visits could both pass the cap=3
    # check and insert a 4th row. The lock key is the 64-bit hash of the
    # triple (Postgres advisory locks take a single int8). The lock is
    # released automatically when the surrounding transaction commits — so
    # we MUST run inside an explicit transaction here.
    async with conn.transaction():
        lock_seed = f"cf_visit:{cycle_id}:{inviter_id}:{ip_hash}"
        # `hashtextextended` returns a stable bigint hash usable as an
        # advisory-lock key without collisions across long strings.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            lock_seed,
        )

        # iter169 — Anti-fraud: cap distinct visits at VISITS_PER_IP_CAP per
        # (ip, inviter, cycle). Above the cap we silently DROP the row but
        # let the redirect/share UX continue normally so attackers don't get
        # a "fraud detected" signal they could probe against.
        cur_count = await conn.fetchval(
            """SELECT COUNT(*) FROM crowdfunding_invite_visits
               WHERE ip_hash = $1 AND inviter_id = $2 AND cycle_id = $3""",
            ip_hash, inviter_id, cycle_id,
        )
        if cur_count and cur_count >= VISITS_PER_IP_CAP:
            return {"recorded": False, "reason": "ip_cap_reached",
                    "visits_count": int(cur_count)}

        await conn.execute(
            """INSERT INTO crowdfunding_invite_visits
                 (cycle_id, inviter_id, project_slug, visitor_user_id,
                  ip_hash, user_agent_hash, utm_source)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            cycle_id, inviter_id, project_slug,
            visitor_user_id, ip_hash, user_agent_hash, utm_source,
        )
        return {"recorded": True, "reason": None,
                "visits_count": int(cur_count or 0) + 1}


async def try_credit_recruit(conn, *, cycle_id: str, recruit_user_id: str,
                              vote_id: Optional[int],
                              project_id: str, project_owner_id: str,
                              ip_hash: str) -> dict:
    """Attempt to credit the recruiter who brought `recruit_user_id` into
    this cycle. Called inside the vote transaction.

    Attribution lookup order:
      1. Most recent visit by `visitor_user_id == recruit_user_id` in cycle.
      2. Fallback: most recent visit by same `ip_hash` in cycle.

    Anti-self-credit: if the resolved inviter_id == project_owner_id,
    we DO NOT credit (CEO rule: no recruiter credit for own project).
    Inviter == recruit is also blocked (defensive).

    Returns:
        {credited: bool, inviter_id: str|None, reason: str|None}
    """
    visit = await conn.fetchrow(
        """SELECT inviter_id FROM crowdfunding_invite_visits
           WHERE cycle_id = $1 AND visitor_user_id = $2
           ORDER BY created_at DESC LIMIT 1""",
        cycle_id, recruit_user_id,
    )
    if not visit:
        # Fallback to IP-based attribution. Useful when the visitor was
        # anonymous on click but signed up afterwards.
        visit = await conn.fetchrow(
            """SELECT inviter_id FROM crowdfunding_invite_visits
               WHERE cycle_id = $1 AND ip_hash = $2
                 AND (visitor_user_id IS NULL OR visitor_user_id = $3)
               ORDER BY created_at DESC LIMIT 1""",
            cycle_id, ip_hash, recruit_user_id,
        )
    if not visit:
        return {"credited": False, "inviter_id": None,
                "reason": "no_attribution_visit"}

    inviter_id = visit["inviter_id"]
    if inviter_id == project_owner_id:
        return {"credited": False, "inviter_id": inviter_id,
                "reason": "self_project_blocked"}
    if inviter_id == recruit_user_id:
        return {"credited": False, "inviter_id": inviter_id,
                "reason": "self_referral_blocked"}

    # ON CONFLICT DO NOTHING enforces the unique-per-cycle rule.
    inserted = await conn.execute(
        """INSERT INTO crowdfunding_recruit_credits
             (cycle_id, inviter_id, recruit_user_id, vote_id, project_id)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (cycle_id, inviter_id, recruit_user_id) DO NOTHING""",
        cycle_id, inviter_id, recruit_user_id, vote_id, project_id,
    )
    if inserted.endswith(" 0"):
        return {"credited": False, "inviter_id": inviter_id,
                "reason": "already_credited"}
    return {"credited": True, "inviter_id": inviter_id, "reason": None}


async def cycle_leaderboard(conn, cycle_id: str, *, limit: int = 50,
                             viewer_id: Optional[str] = None) -> dict:
    """Top recruiters for the cycle, sorted by recruits_count DESC.

    `me` block returns the viewer's rank + count even if they're outside
    the top-N (UI shows it pinned at the bottom of the list).
    """
    rows = await conn.fetch(
        """SELECT rc.inviter_id, COUNT(*) AS recruits_count,
                   u.first_name, u.last_name, u.username, u.avatar
           FROM crowdfunding_recruit_credits rc
           JOIN users u ON u.user_id = rc.inviter_id
           WHERE rc.cycle_id = $1
           GROUP BY rc.inviter_id, u.first_name, u.last_name,
                    u.username, u.avatar
           ORDER BY recruits_count DESC, MIN(rc.created_at) ASC
           LIMIT $2""",
        cycle_id, limit,
    )
    items = []
    for i, r in enumerate(rows):
        d = dict(r)
        d["rank"] = i + 1
        d["recruits_count"] = int(d["recruits_count"])
        d["tier"] = _tier_for_count(d["recruits_count"])
        items.append(d)

    me = None
    if viewer_id:
        my_count = await conn.fetchval(
            "SELECT COUNT(*) FROM crowdfunding_recruit_credits "
            "WHERE cycle_id = $1 AND inviter_id = $2",
            cycle_id, viewer_id,
        )
        my_count = int(my_count or 0)
        # Compute rank by counting users with strictly more recruits.
        better = await conn.fetchval(
            """SELECT COUNT(*) FROM (
                 SELECT inviter_id, COUNT(*) AS c
                 FROM crowdfunding_recruit_credits
                 WHERE cycle_id = $1
                 GROUP BY inviter_id
                 HAVING COUNT(*) > $2
               ) better""",
            cycle_id, my_count,
        )
        me = {
            "rank": int(better or 0) + 1 if my_count > 0 else None,
            "recruits_count": my_count,
            "tier": _tier_for_count(my_count),
        }
    return {"items": items, "me": me}


def _tier_for_count(count: int) -> Optional[dict]:
    """Return the highest tier currently held by `count` recruits, or None."""
    held = None
    for t in RECRUITER_TIERS:
        if count >= t["threshold"]:
            held = t
    return held


def next_tier_hint(count: int) -> Optional[dict]:
    """Lookup the next tier the user is climbing toward (UI nudge)."""
    for t in RECRUITER_TIERS:
        if count < t["threshold"]:
            return {**t, "missing": t["threshold"] - count}
    return None


async def my_progress(conn, user_id: str, cycle_id: str) -> dict:
    """Personal progression widget payload."""
    count = int(await conn.fetchval(
        "SELECT COUNT(*) FROM crowdfunding_recruit_credits "
        "WHERE inviter_id = $1 AND cycle_id = $2",
        user_id, cycle_id,
    ) or 0)
    visits = int(await conn.fetchval(
        "SELECT COUNT(*) FROM crowdfunding_invite_visits "
        "WHERE inviter_id = $1 AND cycle_id = $2",
        user_id, cycle_id,
    ) or 0)
    badges = await conn.fetch(
        "SELECT tier, recruits_count, awarded_at "
        "FROM crowdfunding_recruiter_badges "
        "WHERE user_id = $1 AND cycle_id = $2 ORDER BY recruits_count DESC",
        user_id, cycle_id,
    )
    return {
        "cycle_id": cycle_id,
        "recruits_count": count,
        "visits_count": visits,
        "tier": _tier_for_count(count),
        "next_tier": next_tier_hint(count),
        "badges": [{
            "tier": b["tier"], "recruits_count": int(b["recruits_count"]),
            "awarded_at": b["awarded_at"].isoformat(),
        } for b in badges],
    }


async def award_tier_badges(conn, user_id: str, cycle_id: str) -> list[str]:
    """Award all tier badges the user has reached, idempotent. Returns
    list of newly-awarded tier keys. Called after every credit insert."""
    count = int(await conn.fetchval(
        "SELECT COUNT(*) FROM crowdfunding_recruit_credits "
        "WHERE inviter_id = $1 AND cycle_id = $2",
        user_id, cycle_id,
    ) or 0)
    newly = []
    for t in RECRUITER_TIERS:
        if count < t["threshold"]:
            break
        already = await conn.fetchval(
            "SELECT 1 FROM crowdfunding_recruiter_badges "
            "WHERE user_id = $1 AND cycle_id = $2 AND tier = $3",
            user_id, cycle_id, t["key"],
        )
        if already:
            continue
        await conn.execute(
            """INSERT INTO crowdfunding_recruiter_badges
                 (user_id, cycle_id, tier, recruits_count)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT DO NOTHING""",
            user_id, cycle_id, t["key"], count,
        )
        newly.append(t["key"])
    return newly
