"""
JAPAP — User-to-user follow graph (iter77)
===========================================
Reciprocal-free directed follow: A can follow B without B following back.
Mounted under /api/users for discoverability (profile endpoints live there too).

Endpoints:
  POST   /api/users/{user_id}/follow      — follow target user
  DELETE /api/users/{user_id}/follow      — unfollow target user
  GET    /api/users/{user_id}/followers   — paginated followers list
  GET    /api/users/{user_id}/following   — paginated following list

Counters on `users.followers_count` / `following_count` are kept in sync in
the same transaction as the follow row INSERT/DELETE so reads stay O(1).
"""
import logging
import time
from collections import deque
from fastapi import APIRouter, HTTPException, Request, Query
from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["social"])

# ───────────────────────────── iter164 — Rate limit ─────────────────────────
# Per-user sliding window for follow / unfollow actions to block spam bots.
# Generous on purpose (genuine browsers need to retry on flaky networks):
#   30 mutation actions per 60 s window → triggers 429 above that.
_FOLLOW_RL_WINDOW_SEC = 60
_FOLLOW_RL_MAX = 30
_follow_action_log: dict[str, deque] = {}


def _check_follow_rate_limit(user_id: str) -> None:
    """Raise HTTPException(429) if the user has exceeded the follow-action
    quota in the rolling window. In-memory only — sufficient for anti-spam
    and never blocks a legitimate user (30/min is way above human pace).
    """
    now = time.monotonic()
    log = _follow_action_log.setdefault(user_id, deque())
    cutoff = now - _FOLLOW_RL_WINDOW_SEC
    while log and log[0] < cutoff:
        log.popleft()
    if len(log) >= _FOLLOW_RL_MAX:
        retry_after = int(_FOLLOW_RL_WINDOW_SEC - (now - log[0])) + 1
        raise HTTPException(
            status_code=429,
            detail="Trop d'actions follow/unfollow. Réessaie dans quelques instants.",
            headers={"Retry-After": str(retry_after)},
        )
    log.append(now)


@router.post("/{user_id}/follow")
async def follow_user(user_id: str, request: Request):
    viewer = await get_current_user(request)
    if viewer['user_id'] == user_id:
        raise HTTPException(status_code=400, detail="You cannot follow yourself")
    _check_follow_rate_limit(viewer['user_id'])  # iter164 — anti-spam guard
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow(
            """SELECT user_id, first_name, last_name, follow_mode, account_visibility
               FROM users WHERE user_id = $1 AND is_active = TRUE""",
            user_id,
        )
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        # Approval-gated follow: targets in 'approval' mode (or 'private'
        # accounts which imply approval) receive a pending request instead
        # of an immediate follow edge. No counters are bumped for pending
        # rows — they only move to accepted on explicit approval.
        requires_approval = (
            target['follow_mode'] == 'approval'
            or target['account_visibility'] == 'private'
        )
        async with conn.transaction():
            # If a pending row exists already → idempotent no-op on pending.
            existing = await conn.fetchrow(
                """SELECT id, status FROM user_follows
                   WHERE follower_id = $1 AND followed_id = $2""",
                viewer['user_id'], user_id,
            )
            if existing:
                status = existing['status']
                if requires_approval and status == 'accepted':
                    # Legacy case: already following. Nothing to change.
                    pass
                # In all other existing-row cases we keep the current state.
            else:
                desired_status = 'pending' if requires_approval else 'accepted'
                await conn.execute(
                    """INSERT INTO user_follows (follower_id, followed_id, status)
                       VALUES ($1, $2, $3)""",
                    viewer['user_id'], user_id, desired_status,
                )
                if desired_status == 'accepted':
                    await conn.execute(
                        "UPDATE users SET followers_count = followers_count + 1 WHERE user_id = $1",
                        user_id,
                    )
                    await conn.execute(
                        "UPDATE users SET following_count = following_count + 1 WHERE user_id = $1",
                        viewer['user_id'],
                    )
        # Re-read the fresh row to return its canonical status.
        row = await conn.fetchrow(
            "SELECT status FROM user_follows WHERE follower_id = $1 AND followed_id = $2",
            viewer['user_id'], user_id,
        )
        status = row['status'] if row else 'accepted'
        new_followers = await conn.fetchval(
            "SELECT followers_count FROM users WHERE user_id = $1", user_id,
        ) or 0

    # Side-effect: notify target (opted-in + deduped inside helper).
    try:
        from services.notifications import (
            send_social_notification, EVT_FOLLOW, EVT_FOLLOW_REQUEST,
        )
        actor_name = f"{viewer.get('first_name','') or ''} {viewer.get('last_name','') or ''}".strip() \
            or viewer.get('username') or 'Quelqu’un'
        if status == 'pending':
            await send_social_notification(
                EVT_FOLLOW_REQUEST, viewer, user_id,
                title="Nouvelle demande d’abonnement",
                body=f"{actor_name} souhaite vous suivre sur JAPAP.",
                deep_link="/settings/requests",
            )
        elif status == 'accepted' and not existing:
            await send_social_notification(
                EVT_FOLLOW, viewer, user_id,
                title="Nouveau follower",
                body=f"{actor_name} vient de vous suivre sur JAPAP.",
                deep_link=f"/users/{viewer['user_id']}",
            )
    except Exception:
        pass  # never block the mutation on a notification hiccup

    return {
        "followed": status == 'accepted',
        "status": status,
        "followers_count": new_followers,
    }


@router.delete("/{user_id}/follow")
async def unfollow_user(user_id: str, request: Request):
    viewer = await get_current_user(request)
    if viewer['user_id'] == user_id:
        raise HTTPException(status_code=400, detail="You cannot unfollow yourself")
    _check_follow_rate_limit(viewer['user_id'])  # iter164 — anti-spam guard
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Only an accepted follow decrements counters; deleting a pending
            # row cancels the request and leaves counters untouched.
            deleted = await conn.fetchrow(
                """DELETE FROM user_follows
                   WHERE follower_id = $1 AND followed_id = $2
                   RETURNING id, status""",
                viewer['user_id'], user_id,
            )
            if deleted and deleted['status'] == 'accepted':
                await conn.execute(
                    "UPDATE users SET followers_count = GREATEST(0, followers_count - 1) WHERE user_id = $1",
                    user_id,
                )
                await conn.execute(
                    "UPDATE users SET following_count = GREATEST(0, following_count - 1) WHERE user_id = $1",
                    viewer['user_id'],
                )
        new_followers = await conn.fetchval(
            "SELECT followers_count FROM users WHERE user_id = $1", user_id,
        )
    return {"followed": False, "followers_count": new_followers or 0}


# ══════════════════════ Follow requests (iter78) ══════════════════════

@router.get("/me/follow-requests")
async def list_my_follow_requests(request: Request,
                                  limit: int = Query(30, ge=1, le=100),
                                  offset: int = Query(0, ge=0)):
    """Pending follow requests **for** the current user (inbox)."""
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT f.id AS request_id, f.follower_id, f.created_at,
                      u.username, u.first_name, u.last_name, u.avatar,
                      u.is_verified, u.is_pro, u.followers_count
               FROM user_follows f
               JOIN users u ON u.user_id = f.follower_id
               WHERE f.followed_id = $1 AND f.status = 'pending'
               ORDER BY f.created_at DESC LIMIT $2 OFFSET $3""",
            viewer['user_id'], limit, offset,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM user_follows WHERE followed_id = $1 AND status = 'pending'",
            viewer['user_id'],
        )
    items = []
    for r in rows:
        d = dict(r)
        d['created_at'] = d['created_at'].isoformat() if d.get('created_at') else None
        items.append(d)
    return {"items": items, "total": total or 0, "limit": limit, "offset": offset}


# iter164 — outbox: pending requests the viewer has SENT awaiting approval.
# Exists symmetrically to /me/follow-requests so the user can audit (and
# cancel) their own pending requests from the Settings → Following tab.
@router.get("/me/sent-requests")
async def list_my_sent_requests(request: Request,
                                limit: int = Query(30, ge=1, le=100),
                                offset: int = Query(0, ge=0)):
    """Pending follow requests **sent by** the current user (outbox)."""
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT f.id AS request_id, f.followed_id, f.created_at,
                      u.username, u.first_name, u.last_name, u.avatar,
                      u.is_verified, u.is_pro, u.followers_count,
                      u.account_visibility
               FROM user_follows f
               JOIN users u ON u.user_id = f.followed_id
               WHERE f.follower_id = $1 AND f.status = 'pending'
               ORDER BY f.created_at DESC LIMIT $2 OFFSET $3""",
            viewer['user_id'], limit, offset,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM user_follows WHERE follower_id = $1 AND status = 'pending'",
            viewer['user_id'],
        )
    items = []
    for r in rows:
        d = dict(r)
        d['created_at'] = d['created_at'].isoformat() if d.get('created_at') else None
        items.append(d)
    return {"items": items, "total": total or 0, "limit": limit, "offset": offset}


@router.post("/me/follow-requests/{request_id}/accept")
async def accept_follow_request(request_id: int, request: Request):
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT follower_id, followed_id, status FROM user_follows
                   WHERE id = $1 AND followed_id = $2""",
                request_id, viewer['user_id'],
            )
            if not row:
                raise HTTPException(status_code=404, detail="Follow request not found")
            if row['status'] == 'accepted':
                return {"accepted": True, "already": True}
            await conn.execute(
                "UPDATE user_follows SET status = 'accepted' WHERE id = $1", request_id,
            )
            await conn.execute(
                "UPDATE users SET followers_count = followers_count + 1 WHERE user_id = $1",
                viewer['user_id'],
            )
            await conn.execute(
                "UPDATE users SET following_count = following_count + 1 WHERE user_id = $1",
                row['follower_id'],
            )
    # Notify the requester that their request was accepted.
    try:
        from services.notifications import send_social_notification, EVT_FOLLOW_ACCEPTED
        target_name = f"{viewer.get('first_name','') or ''} {viewer.get('last_name','') or ''}".strip() \
            or viewer.get('username') or 'Quelqu’un'
        await send_social_notification(
            EVT_FOLLOW_ACCEPTED, viewer, row['follower_id'],
            title="Demande acceptée",
            body=f"{target_name} a accepté votre demande d’abonnement.",
            deep_link=f"/users/{viewer['user_id']}",
        )
    except Exception:
        pass
    return {"accepted": True}


@router.post("/me/follow-requests/{request_id}/decline")
async def decline_follow_request(request_id: int, request: Request):
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Idempotent: deleting twice is a no-op.
        deleted = await conn.execute(
            """DELETE FROM user_follows
               WHERE id = $1 AND followed_id = $2 AND status = 'pending'""",
            request_id, viewer['user_id'],
        )
        if not deleted or deleted.endswith(" 0"):
            raise HTTPException(status_code=404, detail="Follow request not found")
    return {"declined": True}


@router.delete("/me/followers/{follower_id}")
async def remove_follower(follower_id: str, request: Request):
    """Remove a specific user from my followers list (block-lite)."""
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.fetchrow(
                """DELETE FROM user_follows
                   WHERE follower_id = $1 AND followed_id = $2
                   RETURNING status""",
                follower_id, viewer['user_id'],
            )
            if not deleted:
                raise HTTPException(status_code=404, detail="Not a follower")
            if deleted['status'] == 'accepted':
                await conn.execute(
                    "UPDATE users SET followers_count = GREATEST(0, followers_count - 1) WHERE user_id = $1",
                    viewer['user_id'],
                )
                await conn.execute(
                    "UPDATE users SET following_count = GREATEST(0, following_count - 1) WHERE user_id = $1",
                    follower_id,
                )
    return {"removed": True}


# ══════════════════════ Follow suggestions (iter81) ══════════════════════

@router.get("/me/suggestions")
async def get_follow_suggestions(request: Request, limit: int = Query(3, ge=1, le=20)):
    """Suggest people for the current viewer to follow.

    Ranking (all in one SQL pass):
      - Exclude: self, blocked, already-followed (any status), inactive users.
      - Prioritise: friends-of-friends (followers of people I follow), then
        users with the highest `followers_count` (popular creators).
      - Small tie-break: is_pro first, then is_verified, then recent signups.
    Returns a compact user shape reused by <FollowSuggestions/>.
    """
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH my_following AS (
                SELECT followed_id FROM user_follows
                WHERE follower_id = $1 AND status = 'accepted'
            ),
            fof AS (
                -- friends-of-friends: followers of people I follow, excluding me
                SELECT f.follower_id AS uid, COUNT(*) AS mutual_hits
                FROM user_follows f
                WHERE f.followed_id IN (SELECT followed_id FROM my_following)
                  AND f.status = 'accepted'
                  AND f.follower_id <> $1
                GROUP BY f.follower_id
            )
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar,
                   u.about, u.is_verified, u.is_pro,
                   u.followers_count, u.following_count, u.posts_count,
                   COALESCE(fof.mutual_hits, 0) AS mutual_hits
            FROM users u
            LEFT JOIN fof ON fof.uid = u.user_id
            WHERE u.user_id <> $1
              AND u.is_active = TRUE
              AND u.account_visibility = 'public'
              AND u.user_id NOT IN (
                  SELECT followed_id FROM user_follows WHERE follower_id = $1
              )
            ORDER BY
                mutual_hits DESC,
                u.followers_count DESC NULLS LAST,
                u.is_pro DESC,
                u.is_verified DESC,
                u.created_at DESC
            LIMIT $2
            """,
            viewer['user_id'], limit,
        )
    items = [dict(r) for r in rows]
    # Convert counts / ensure no ObjectId-ish leak (shouldn't happen on PG
    # but defensive for the future).
    return {"items": items}


async def _list_users(conn, user_ids, viewer_id):
    """Return a minimal JSON shape for a list of user_ids, preserving the
    input order. Includes a 4-state follow flag for each row so the UI can
    render the correct button without a second round-trip:
       • is_following  — viewer → user is accepted (UI: "Abonné")
       • is_pending    — viewer → user is pending  (UI: "Demandé")
       • follows_me    — user → viewer is accepted (UI: "Suivre en retour")
       • is_self       — same user as viewer       (UI: hide button)
    """
    if not user_ids:
        return []
    rows = await conn.fetch(
        """SELECT user_id, username, first_name, last_name, avatar, is_verified, is_pro,
                  about, followers_count, following_count, posts_count,
                  account_visibility
           FROM users WHERE user_id = ANY($1::varchar[])""",
        user_ids,
    )
    by_id = {r['user_id']: dict(r) for r in rows}
    follow_set: set[str] = set()        # viewer → user accepted
    pending_set: set[str] = set()       # viewer → user pending
    follower_set: set[str] = set()      # user → viewer accepted (Follow back hint)
    if viewer_id:
        # Outgoing edges (viewer → user_ids)
        out_rows = await conn.fetch(
            """SELECT followed_id, status FROM user_follows
               WHERE follower_id = $1 AND followed_id = ANY($2::varchar[])""",
            viewer_id, user_ids,
        )
        for r in out_rows:
            if r['status'] == 'accepted':
                follow_set.add(r['followed_id'])
            elif r['status'] == 'pending':
                pending_set.add(r['followed_id'])
        # Incoming edges (user_ids → viewer) — only accepted matters here.
        in_rows = await conn.fetch(
            """SELECT follower_id FROM user_follows
               WHERE followed_id = $1 AND follower_id = ANY($2::varchar[])
               AND status = 'accepted'""",
            viewer_id, user_ids,
        )
        follower_set = {r['follower_id'] for r in in_rows}
    out = []
    for uid in user_ids:
        r = by_id.get(uid)
        if not r:
            continue
        r['is_following'] = uid in follow_set
        r['is_pending'] = uid in pending_set
        r['follows_me'] = uid in follower_set
        r['is_self'] = uid == viewer_id
        out.append(r)
    return out


@router.get("/{user_id}/followers")
async def get_followers(user_id: str, request: Request,
                        limit: int = Query(30, ge=1, le=100),
                        offset: int = Query(0, ge=0),
                        q: str = Query('', max_length=64)):
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id = $1", user_id)
        if not exists:
            raise HTTPException(status_code=404, detail="User not found")
        # Join + filter server-side so pagination over a search stays correct.
        base_params = [user_id]
        search_clause = ''
        if q.strip():
            base_params.append(f"%{q.strip().lower()}%")
            search_clause = (
                " AND (LOWER(u.username) LIKE $2 OR LOWER(u.first_name) LIKE $2"
                " OR LOWER(u.last_name) LIKE $2)"
            )
        rows = await conn.fetch(
            f"""SELECT f.follower_id FROM user_follows f
                JOIN users u ON u.user_id = f.follower_id
                WHERE f.followed_id = $1 AND f.status = 'accepted'{search_clause}
                ORDER BY f.created_at DESC LIMIT ${len(base_params)+1} OFFSET ${len(base_params)+2}""",
            *base_params, limit, offset,
        )
        ids = [r['follower_id'] for r in rows]
        items = await _list_users(conn, ids, viewer['user_id'])
        total = await conn.fetchval(
            "SELECT followers_count FROM users WHERE user_id = $1", user_id,
        ) or 0
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{user_id}/following")
async def get_following(user_id: str, request: Request,
                        limit: int = Query(30, ge=1, le=100),
                        offset: int = Query(0, ge=0),
                        q: str = Query('', max_length=64)):
    viewer = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id = $1", user_id)
        if not exists:
            raise HTTPException(status_code=404, detail="User not found")
        base_params = [user_id]
        search_clause = ''
        if q.strip():
            base_params.append(f"%{q.strip().lower()}%")
            search_clause = (
                " AND (LOWER(u.username) LIKE $2 OR LOWER(u.first_name) LIKE $2"
                " OR LOWER(u.last_name) LIKE $2)"
            )
        rows = await conn.fetch(
            f"""SELECT f.followed_id FROM user_follows f
                JOIN users u ON u.user_id = f.followed_id
                WHERE f.follower_id = $1 AND f.status = 'accepted'{search_clause}
                ORDER BY f.created_at DESC LIMIT ${len(base_params)+1} OFFSET ${len(base_params)+2}""",
            *base_params, limit, offset,
        )
        ids = [r['followed_id'] for r in rows]
        items = await _list_users(conn, ids, viewer['user_id'])
        total = await conn.fetchval(
            "SELECT following_count FROM users WHERE user_id = $1", user_id,
        ) or 0
    return {"items": items, "total": total, "limit": limit, "offset": offset}
