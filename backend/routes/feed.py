import uuid
import logging
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user
from routes.referrals import check_and_activate_referral
from routes.realtime import notify_like, notify_comment

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feed", tags=["feed"])


class CreatePostRequest(BaseModel):
    text: str
    media: list = []
    filter_preset: str = ""  # iter150 — chosen filter preset id (mono/sepia/...)

class UpdatePostRequest(BaseModel):
    text: Optional[str] = None
    media: Optional[list] = None

class SharePostRequest(BaseModel):
    target: str                       # "feed" | "group" | "page" | "dm"
    target_id: Optional[str] = None   # group_id / page_id / conversation_id
    caption: str = ""

class CreateCommentRequest(BaseModel):
    text: str


# iter165 — Lightweight endpoint for the Ads composer + Profile picker.
# Returns the current viewer's own posts only, so the user can pick one to
# sponsor without the visibility/affinity filters of the global feed. Sorted
# strictly by recency (creation date desc), limited to 50 by default.
@router.get("/my-posts")
async def list_my_posts(request: Request,
                        page: int = Query(1, ge=1),
                        limit: int = Query(20, ge=1, le=50)):
    """Return the viewer's own posts (any visibility, including drafts).
    Used by the Ads sponsor flow to pick a post to promote.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM posts WHERE user_id = $1", user['user_id'])
        rows = await conn.fetch(
            """SELECT post_id, text, media, visibility, likes_count,
                      comments_count, shares_count, is_pinned,
                      created_at, updated_at
               FROM posts
               WHERE user_id = $1
               ORDER BY created_at DESC
               LIMIT $2 OFFSET $3""",
            user['user_id'], limit, offset,
        )
    items = []
    for r in rows:
        d = dict(r)
        d['created_at'] = d['created_at'].isoformat() if d.get('created_at') else None
        d['updated_at'] = d['updated_at'].isoformat() if d.get('updated_at') else None
        items.append(d)
    return {"items": items, "total": total or 0, "page": page, "limit": limit}


@router.get("/posts")
async def get_feed(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    sort: str = Query("smart", pattern="^(smart|recent)$"),
):
    """Feed endpoint with two modes:
    - sort='smart' (default): stats-based AI re-ranking combining recency,
      engagement (likes+comments+tips), and user affinity (posts from users the
      viewer has interacted with in the past 30 days get a boost).
    - sort='recent': chronological — backward compatible.

    Scoring formula (smart):
        score = recency * 0.45 + engagement * 0.35 + affinity * 0.20 + pinned_boost

    Recency:   exp(-hours_since_post / 48)         ∈ [0, 1]
    Engagement:log(1 + likes + 2*comments + 3*tips_count + 5*tips_total/1000) / 10  (clamped)
    Affinity:  1 if author is in viewer's "interacted with" set else 0
    Pinned:    +0.5 bonus
    """
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    viewer_id = user['user_id']
    # Visibility filter — evaluated inline in SQL.
    # A post is visible to the viewer when ANY of these holds:
    #   - the viewer is the author (sees own everything)
    #   - the post visibility is 'public' AND the author's account is 'public'
    #     OR the viewer follows the author (accepted)
    #   - the post visibility is 'friends' AND the viewer follows the author (accepted)
    # 'only_me' posts are filtered out for everyone except the author.
    viz_filter = """
        (
          p.user_id = $1
          OR (
            p.visibility = 'public' AND (
              u.account_visibility = 'public'
              OR EXISTS (SELECT 1 FROM user_follows f
                         WHERE f.followed_id = p.user_id
                         AND f.follower_id = $1
                         AND f.status = 'accepted')
            )
          )
          OR (
            p.visibility = 'friends' AND EXISTS (
              SELECT 1 FROM user_follows f
              WHERE f.followed_id = p.user_id
              AND f.follower_id = $1
              AND f.status = 'accepted'
            )
          )
        )
    """
    async with pool.acquire() as conn:
        count = await conn.fetchval(f"""
            SELECT COUNT(*)
            FROM posts p JOIN users u ON p.user_id = u.user_id
            WHERE {viz_filter}
        """, viewer_id)

        if sort == "recent":
            rows = await conn.fetch(f"""
                SELECT p.*, u.first_name, u.last_name, u.avatar, u.username, u.is_verified, u.is_pro,
                       EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id = p.post_id AND pl.user_id = $1) as is_liked,
                       NULL::float8 as score
                FROM posts p JOIN users u ON p.user_id = u.user_id
                WHERE {viz_filter}
                ORDER BY p.is_pinned DESC, p.created_at DESC
                LIMIT $2 OFFSET $3
            """, viewer_id, limit, offset)
        else:
            # Smart feed: compute score inline in PostgreSQL.
            # Affinity = authors I've (liked, commented, tipped, messaged) in past 30d.
            rows = await conn.fetch(f"""
                WITH affinity AS (
                    SELECT DISTINCT target_user_id AS uid FROM (
                        SELECT p2.user_id AS target_user_id
                        FROM post_likes pl
                        JOIN posts p2 ON p2.post_id = pl.post_id
                        WHERE pl.user_id = $1 AND pl.created_at > NOW() - INTERVAL '30 days'
                        UNION
                        SELECT p2.user_id
                        FROM post_comments c
                        JOIN posts p2 ON p2.post_id = c.post_id
                        WHERE c.user_id = $1 AND c.created_at > NOW() - INTERVAL '30 days'
                        UNION
                        SELECT t.recipient_id
                        FROM tips t
                        WHERE t.sender_id = $1 AND t.created_at > NOW() - INTERVAL '30 days'
                    ) s
                )
                SELECT p.*, u.first_name, u.last_name, u.avatar, u.username, u.is_verified, u.is_pro,
                       EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id = p.post_id AND pl.user_id = $1) as is_liked,
                       (
                          0.45 * EXP(- EXTRACT(EPOCH FROM (NOW() - p.created_at)) / (48.0 * 3600.0))
                          + 0.35 * LEAST(
                              1.0,
                              LN(1 + COALESCE(p.likes_count,0)
                                   + 2 * COALESCE(p.comments_count,0)
                                   + 3 * COALESCE(p.tips_count,0)
                                   + 5 * COALESCE(p.tips_total,0) / 1000.0
                              ) / 10.0
                            )
                          + 0.20 * CASE WHEN p.user_id IN (SELECT uid FROM affinity) THEN 1.0 ELSE 0.0 END
                          + CASE WHEN p.is_pinned THEN 0.5 ELSE 0.0 END
                          + CASE WHEN u.is_pro THEN 0.25 ELSE 0.0 END
                       ) AS score
                FROM posts p JOIN users u ON p.user_id = u.user_id
                WHERE {viz_filter}
                ORDER BY score DESC, p.created_at DESC
                LIMIT $2 OFFSET $3
            """, viewer_id, limit, offset)

        posts = []
        for r in rows:
            p = dict(r)
            p['created_at'] = p['created_at'].isoformat()
            p['updated_at'] = p['updated_at'].isoformat()
            if isinstance(p.get('media'), str):
                import json
                try: p['media'] = json.loads(p['media'])
                except: p['media'] = []
            if p.get('score') is not None:
                p['score'] = round(float(p['score']), 4)
            if isinstance(p.get('tips_total'), Decimal):
                p['tips_total'] = str(p['tips_total'])
            posts.append(p)
        return {"posts": posts, "total": count, "page": page, "sort": sort}


@router.post("/posts")
async def create_post(req: CreatePostRequest, request: Request):
    user = await get_current_user(request)
    # iter183 — UX P0 : autoriser un post avec un média seul (image / vidéo).
    # Refuser uniquement les posts complètement vides (ni texte, ni média).
    has_text = bool((req.text or "").strip())
    has_media = isinstance(req.media, list) and len(req.media) > 0
    if not has_text and not has_media:
        raise HTTPException(
            status_code=400,
            detail="Ajoute du texte, une photo ou une vidéo pour publier.")
    pool = await get_pool()
    post_id = f"post_{uuid.uuid4().hex[:12]}"
    import json
    media_json = json.dumps(req.media)
    # Resolve default per-post visibility from the user's preference. New
    # posts inherit `post_visibility_default` unless future per-post overrides
    # are sent on the request body.
    async with pool.acquire() as conn:
        pref = await conn.fetchval(
            "SELECT COALESCE(post_visibility_default, 'public') FROM users WHERE user_id = $1",
            user['user_id'],
        ) or 'public'
        visibility = pref
        # iter150 — sanitise the optional filter preset id (whitelist).
        preset = (req.filter_preset or "").strip().lower()
        if preset and not preset.replace("-", "").replace("_", "").isalnum():
            preset = ""
        await conn.execute("""
            INSERT INTO posts (post_id, user_id, text, media, visibility, filter_preset)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, post_id, user['user_id'], req.text, media_json, visibility,
             preset[:32] or None)
        # Only public posts contribute to the denormalised posts_count — we
        # don't want private 'only_me' journal entries inflating a user's
        # public-facing counter.
        if visibility == 'public':
            await conn.execute(
                "UPDATE users SET posts_count = posts_count + 1 WHERE user_id = $1",
                user['user_id'],
            )
        row = await conn.fetchrow("""
            SELECT p.*, u.first_name, u.last_name, u.avatar, u.username, u.is_verified, u.is_pro
            FROM posts p JOIN users u ON p.user_id = u.user_id WHERE p.post_id = $1
        """, post_id)
        p = dict(row)
        p['created_at'] = p['created_at'].isoformat()
        p['updated_at'] = p['updated_at'].isoformat()
        p['is_liked'] = False
    # Activate referral if applicable (first qualifying action)
    try:
        await check_and_activate_referral(user['user_id'])
    except Exception as e:
        logger.warning(f"Referral activation skipped: {e}")
    return p


@router.post("/posts/{post_id}/like")
async def toggle_like(post_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    liked = False
    owner_id = None
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM post_likes WHERE post_id = $1 AND user_id = $2", post_id, user['user_id'])
        if existing:
            await conn.execute("DELETE FROM post_likes WHERE post_id = $1 AND user_id = $2", post_id, user['user_id'])
            await conn.execute("UPDATE posts SET likes_count = GREATEST(likes_count - 1, 0) WHERE post_id = $1", post_id)
            return {"liked": False}
        else:
            await conn.execute("INSERT INTO post_likes (post_id, user_id) VALUES ($1, $2)", post_id, user['user_id'])
            await conn.execute("UPDATE posts SET likes_count = likes_count + 1 WHERE post_id = $1", post_id)
            liked = True
            owner = await conn.fetchrow("SELECT user_id FROM posts WHERE post_id = $1", post_id)
            owner_id = owner['user_id'] if owner else None
    # Realtime push (fire-and-forget, skip self-like)
    if liked and owner_id and owner_id != user['user_id']:
        try:
            await notify_like(
                recipient_id=owner_id,
                sender={
                    "user_id": user['user_id'],
                    "name": (f"{user['first_name']} {user['last_name']}".strip() or user.get('username', '')),
                    "avatar": user.get('avatar', '') or '',
                },
                target_type='post',
                target_id=post_id,
            )
        except Exception as e:
            logger.warning(f"notify_like failed: {e}")
    return {"liked": True}


@router.get("/posts/{post_id}")
async def get_single_post(post_id: str, request: Request):
    """Fetch a single post by ID — used for deep links (shareable URLs like /post/:id).
    Same shape as the feed endpoint so the frontend can reuse its render logic.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Same visibility predicate as the feed listing: self sees all own;
        # others only see public (when the account is public OR they follow)
        # or friends-visibility posts if they are an accepted follower.
        row = await conn.fetchrow("""
            SELECT p.*, u.first_name, u.last_name, u.avatar, u.username, u.is_verified, u.is_pro,
                   EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id = p.post_id AND pl.user_id = $1) as is_liked
            FROM posts p JOIN users u ON p.user_id = u.user_id
            WHERE p.post_id = $2
              AND (
                p.user_id = $1
                OR (p.visibility = 'public' AND (
                     u.account_visibility = 'public'
                     OR EXISTS (SELECT 1 FROM user_follows f
                                WHERE f.followed_id = p.user_id
                                AND f.follower_id = $1
                                AND f.status = 'accepted')
                   ))
                OR (p.visibility = 'friends' AND EXISTS (
                     SELECT 1 FROM user_follows f
                     WHERE f.followed_id = p.user_id
                     AND f.follower_id = $1
                     AND f.status = 'accepted'
                   ))
              )
        """, user['user_id'], post_id)
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")
        p = dict(row)
        p['created_at'] = p['created_at'].isoformat()
        p['updated_at'] = p['updated_at'].isoformat()
        if isinstance(p.get('media'), str):
            import json
            try: p['media'] = json.loads(p['media'])
            except: p['media'] = []
        if isinstance(p.get('tips_total'), Decimal):
            p['tips_total'] = str(p['tips_total'])
        return p


@router.get("/posts/{post_id}/comments")
async def get_comments(post_id: str, request: Request):
    await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.*, u.first_name, u.last_name, u.avatar, u.username
            FROM post_comments c JOIN users u ON c.user_id = u.user_id
            WHERE c.post_id = $1 ORDER BY c.created_at ASC
        """, post_id)
        return [dict(r) | {'created_at': r['created_at'].isoformat()} for r in rows]


@router.post("/posts/{post_id}/comments")
async def create_comment(post_id: str, req: CreateCommentRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    comment_id = f"cmt_{uuid.uuid4().hex[:12]}"
    owner_id = None
    async with pool.acquire() as conn:
        post = await conn.fetchrow("SELECT post_id, user_id FROM posts WHERE post_id = $1", post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        owner_id = post['user_id']
        await conn.execute("""
            INSERT INTO post_comments (comment_id, post_id, user_id, text) VALUES ($1, $2, $3, $4)
        """, comment_id, post_id, user['user_id'], req.text)
        await conn.execute("UPDATE posts SET comments_count = comments_count + 1 WHERE post_id = $1", post_id)
    # Realtime push (fire-and-forget, skip self)
    if owner_id and owner_id != user['user_id']:
        try:
            await notify_comment(
                recipient_id=owner_id,
                sender={
                    "user_id": user['user_id'],
                    "name": (f"{user['first_name']} {user['last_name']}".strip() or user.get('username', '')),
                    "avatar": user.get('avatar', '') or '',
                },
                target_type='post',
                target_id=post_id,
                text=req.text or '',
            )
        except Exception as e:
            logger.warning(f"notify_comment failed: {e}")
    return {"comment_id": comment_id, "text": req.text, "user_id": user['user_id'],
            "first_name": user['first_name'], "last_name": user['last_name']}


# ======================================================================
# REEL COMMENTS — iter215
# ======================================================================
# Reel comments live in their own table to keep `post_comments` clean and
# preserve referential integrity (posts and reels are different entities
# with their own retention rules). Schema is created idempotently on
# first hit so we don't need a separate migration script.

_reel_comments_ready = False


async def _ensure_reel_comments_table(conn):
    global _reel_comments_ready
    if _reel_comments_ready:
        return
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS reel_comments (
            comment_id  VARCHAR(64) PRIMARY KEY,
            reel_id     VARCHAR(64) NOT NULL,
            user_id     VARCHAR(64) NOT NULL,
            text        TEXT NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS reel_comments_reel_id_idx "
        "ON reel_comments(reel_id, created_at DESC)"
    )
    _reel_comments_ready = True


@router.get("/reels/{reel_id}/comments")
async def get_reel_comments(reel_id: str, request: Request):
    await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_reel_comments_table(conn)
        rows = await conn.fetch("""
            SELECT c.comment_id, c.reel_id, c.user_id, c.text, c.created_at,
                   u.first_name, u.last_name, u.avatar, u.username
            FROM reel_comments c
            JOIN users u ON c.user_id = u.user_id
            WHERE c.reel_id = $1
            ORDER BY c.created_at ASC
        """, reel_id)
    return [{
        **dict(r),
        'post_id': r['reel_id'],  # CommentSection key compatibility
        'created_at': r['created_at'].isoformat(),
    } for r in rows]


@router.post("/reels/{reel_id}/comments")
async def create_reel_comment(reel_id: str, req: CreateCommentRequest, request: Request):
    user = await get_current_user(request)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Le commentaire ne peut pas être vide.")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Commentaire trop long (max 2000 caractères).")
    pool = await get_pool()
    comment_id = f"cmt_{uuid.uuid4().hex[:12]}"
    owner_id = None
    async with pool.acquire() as conn:
        await _ensure_reel_comments_table(conn)
        reel = await conn.fetchrow("SELECT reel_id, user_id FROM reels WHERE reel_id = $1", reel_id)
        if not reel:
            raise HTTPException(status_code=404, detail="Reel introuvable")
        owner_id = reel['user_id']
        await conn.execute(
            "INSERT INTO reel_comments (comment_id, reel_id, user_id, text) VALUES ($1, $2, $3, $4)",
            comment_id, reel_id, user['user_id'], text,
        )
        await conn.execute(
            "UPDATE reels SET comments_count = comments_count + 1 WHERE reel_id = $1",
            reel_id,
        )
    # Realtime push to the reel owner (fire-and-forget, skip self).
    if owner_id and owner_id != user['user_id']:
        try:
            await notify_comment(
                recipient_id=owner_id,
                sender={
                    "user_id": user['user_id'],
                    "name": (f"{user['first_name']} {user['last_name']}".strip()
                             or user.get('username', '')),
                    "avatar": user.get('avatar', '') or '',
                },
                target_type='reel',
                target_id=reel_id,
                text=text,
            )
        except Exception as e:
            logger.warning(f"notify_comment(reel) failed: {e}")
    return {
        "comment_id": comment_id,
        "reel_id": reel_id,
        "post_id": reel_id,  # CommentSection compatibility
        "text": text,
        "user_id": user['user_id'],
        "first_name": user['first_name'],
        "last_name": user['last_name'],
        "username": user.get('username', ''),
        "avatar": user.get('avatar', '') or '',
    }


# ======================================================================
# POST MANAGEMENT — edit / delete / pin / share (Phase 1 refonte feed)
# ======================================================================


@router.put("/posts/{post_id}")
async def update_post(post_id: str, req: UpdatePostRequest, request: Request):
    """Owner (or admin) can edit text / media of their post."""
    user = await get_current_user(request)
    pool = await get_pool()
    import json as _json
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM posts WHERE post_id = $1", post_id)
        if not row:
            raise HTTPException(status_code=404, detail="Post introuvable")
        if row['user_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Vous ne pouvez modifier que vos propres publications.")
        updates, params = [], []
        if req.text is not None:
            updates.append(f"text = ${len(params) + 1}")
            params.append(req.text.strip())
        if req.media is not None:
            updates.append(f"media = ${len(params) + 1}::jsonb")
            params.append(_json.dumps(req.media))
        if not updates:
            raise HTTPException(status_code=400, detail="Aucune modification fournie.")
        updates.append("updated_at = NOW()")
        params.append(post_id)
        await conn.execute(
            f"UPDATE posts SET {', '.join(updates)} WHERE post_id = ${len(params)}",
            *params,
        )
        updated = await conn.fetchrow(
            """
            SELECT p.*, u.first_name, u.last_name, u.avatar, u.username, u.is_verified, u.is_pro,
                   EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id = p.post_id AND pl.user_id = $1) as is_liked
            FROM posts p JOIN users u ON p.user_id = u.user_id WHERE p.post_id = $2
            """,
            user['user_id'], post_id,
        )
    p = dict(updated)
    p['created_at'] = p['created_at'].isoformat()
    p['updated_at'] = p['updated_at'].isoformat()
    if isinstance(p.get('media'), str):
        try: p['media'] = _json.loads(p['media'])
        except Exception: p['media'] = []
    if isinstance(p.get('tips_total'), Decimal):
        p['tips_total'] = str(p['tips_total'])
    return p


@router.delete("/posts/{post_id}")
async def delete_post(post_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM posts WHERE post_id = $1", post_id)
        if not row:
            raise HTTPException(status_code=404, detail="Post introuvable")
        if row['user_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Vous ne pouvez supprimer que vos propres publications.")
        await conn.execute("DELETE FROM posts WHERE post_id = $1", post_id)
        # Mirror the deletion in the author's denormalized counter.
        await conn.execute(
            "UPDATE users SET posts_count = GREATEST(0, posts_count - 1) WHERE user_id = $1",
            row['user_id'],
        )
    return {"deleted": True, "post_id": post_id}


@router.post("/posts/{post_id}/pin")
async def toggle_pin(post_id: str, request: Request):
    """Owner pins/unpins their post. Pinned posts appear first on their profile
    and get a +0.5 bonus in the smart feed."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id, is_pinned FROM posts WHERE post_id = $1", post_id)
        if not row:
            raise HTTPException(status_code=404, detail="Post introuvable")
        if row['user_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Vous ne pouvez épingler que vos propres publications.")
        new_state = not row['is_pinned']
        await conn.execute(
            "UPDATE posts SET is_pinned = $1, updated_at = NOW() WHERE post_id = $2",
            new_state, post_id,
        )
    return {"post_id": post_id, "is_pinned": new_state}


_shares_column_ensured = False


async def _ensure_reels_shares_count():
    global _shares_column_ensured
    if _shares_column_ensured:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE reels ADD COLUMN IF NOT EXISTS shares_count INTEGER DEFAULT 0"
        )
    _shares_column_ensured = True


@router.post("/reels/{reel_id}/share")
async def share_reel(reel_id: str, request: Request):
    """iter215 — Bump share counter on a reel after the user opened
    the native share sheet (Web Share API) or copied the link to the
    clipboard. Best-effort tracking; idempotent enough for our needs.
    Schema is guarded by a one-time module-level flag so the ALTER
    TABLE only runs on the first call per process.
    """
    await get_current_user(request)
    await _ensure_reels_shares_count()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT reel_id FROM reels WHERE reel_id = $1", reel_id)
        if not row:
            raise HTTPException(status_code=404, detail="Reel introuvable")
        await conn.execute(
            "UPDATE reels SET shares_count = COALESCE(shares_count, 0) + 1 WHERE reel_id = $1",
            reel_id,
        )
    return {"shared": True, "reel_id": reel_id}


@router.post("/posts/{post_id}/share")
async def share_post(post_id: str, req: SharePostRequest, request: Request):
    """Share a post to feed / group / page / dm. Phase-1 behaviour:
      - target='feed' : increments shares_count and creates a share-type post
        that quotes the original.
      - target='dm'/'group'/'page' : records the share (increments counter)
        and returns the hook for Phase-3 to deliver to the right surface.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    if req.target not in ("feed", "group", "page", "dm", "external"):
        raise HTTPException(status_code=400, detail="Cible de partage invalide.")
    async with pool.acquire() as conn:
        orig = await conn.fetchrow("SELECT * FROM posts WHERE post_id = $1", post_id)
        if not orig:
            raise HTTPException(status_code=404, detail="Post introuvable")
        await conn.execute(
            "UPDATE posts SET shares_count = COALESCE(shares_count, 0) + 1 WHERE post_id = $1",
            post_id,
        )
        if req.target == "external":
            # WhatsApp / Facebook / Copy-link: we only bump the counter.
            # `caption` carries the platform name ('whatsapp'|'facebook'|'copy_link')
            # so analytics can slice by channel later.
            return {"shared": True, "target": "external", "channel": (req.caption or '').strip() or 'unknown'}
        if req.target == "feed":
            import json as _json
            new_id = f"post_{uuid.uuid4().hex[:12]}"
            media_payload = _json.dumps([
                {"type": "shared_post", "post_id": post_id, "user_id": orig['user_id']},
            ])
            await conn.execute(
                """
                INSERT INTO posts (post_id, user_id, text, media, type, visibility)
                VALUES ($1, $2, $3, $4::jsonb, 'share', 'public')
                """,
                new_id, user['user_id'], (req.caption or '').strip(), media_payload,
            )
            return {"shared": True, "target": "feed", "new_post_id": new_id}
        if req.target in ("group", "page") and req.target_id:
            import json as _json
            # Verify target exists & user has the right to post there.
            if req.target == "group":
                is_member = await conn.fetchval(
                    "SELECT 1 FROM group_members WHERE group_id=$1 AND user_id=$2",
                    req.target_id, user['user_id'],
                )
                if not is_member:
                    raise HTTPException(status_code=403, detail="Rejoignez le groupe pour partager")
            else:
                page = await conn.fetchrow("SELECT owner_id FROM social_pages WHERE page_id=$1", req.target_id)
                if not page:
                    raise HTTPException(status_code=404, detail="Page introuvable")
                if page['owner_id'] != user['user_id']:
                    raise HTTPException(status_code=403, detail="Seul le propriétaire peut publier sur cette page")
            new_id = f"post_{uuid.uuid4().hex[:12]}"
            media_payload = _json.dumps([
                {"type": "shared_post", "post_id": post_id, "user_id": orig['user_id']},
            ])
            target_col = "target_group_id" if req.target == "group" else "target_page_id"
            counter_tbl = "social_groups" if req.target == "group" else "social_pages"
            counter_col = "group_id" if req.target == "group" else "page_id"
            async with conn.transaction():
                await conn.execute(
                    f"""INSERT INTO posts (post_id, user_id, text, media, type, visibility, {target_col})
                        VALUES ($1, $2, $3, $4::jsonb, 'share', $5, $6)""",
                    new_id, user['user_id'], (req.caption or '').strip(), media_payload, req.target, req.target_id,
                )
                await conn.execute(
                    f"UPDATE {counter_tbl} SET posts_count = posts_count + 1 WHERE {counter_col}=$1",
                    req.target_id,
                )
            return {"shared": True, "target": req.target, "target_id": req.target_id, "new_post_id": new_id}
    # Non-feed targets are returned to the frontend so it can open the right
    # surface (DM composer, group/page picker). Phase-3 will wire the actual
    # delivery inside groups/pages.
    return {
        "shared": True,
        "target": req.target,
        "target_id": req.target_id,
        "post_id": post_id,
        "note": "Delivery to this surface will be completed in Phase 3 (Groups/Pages).",
    }
