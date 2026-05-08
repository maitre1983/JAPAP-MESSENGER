"""
JAPAP Messenger — Reels + Stories + Tip routes
Unified feed interactions: short videos, ephemeral stories, wallet tips
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user
from routes.realtime import notify_tip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feed", tags=["feed-extended"])


# ============================================================
# REELS
# ============================================================
class CreateReelRequest(BaseModel):
    video_url: str
    thumbnail_url: str = ""
    caption: str = ""
    duration: int = 0
    music_title: str = ""


@router.post("/reels")
async def create_reel(req: CreateReelRequest, request: Request):
    user = await get_current_user(request)
    if not req.video_url:
        raise HTTPException(status_code=400, detail="Vidéo requise")
    if req.duration > 60:
        raise HTTPException(status_code=400, detail="Durée max 60 secondes")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            reel_id = f"reel_{uuid.uuid4().hex[:12]}"
            post_id = f"post_{uuid.uuid4().hex[:12]}"
            import json
            # Create linked post of type reel
            await conn.execute("""
                INSERT INTO posts (post_id, user_id, text, media, type, visibility)
                VALUES ($1, $2, $3, $4::jsonb, 'reel', 'public')
            """, post_id, user['user_id'], req.caption,
                 json.dumps([{"type": "video", "url": req.video_url, "thumbnail": req.thumbnail_url}]))
            await conn.execute("""
                INSERT INTO reels (reel_id, post_id, user_id, video_url, thumbnail_url,
                    caption, duration, music_title)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, reel_id, post_id, user['user_id'], req.video_url, req.thumbnail_url,
                 req.caption, req.duration, req.music_title[:255])
            return {"reel_id": reel_id, "post_id": post_id, "message": "Reel publié"}




# iter165 — Viewer's own reels list, used by the Ads sponsor flow & Profile.
# Excludes reels of other users, strict recency ordering. Returns a `items`
# wrapper so the frontend can rely on the same shape as `/feed/my-posts`.
@router.get("/reels/my")
async def list_my_reels(request: Request,
                        page: int = Query(1, ge=1),
                        limit: int = Query(20, ge=1, le=50)):
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM reels WHERE user_id = $1", user['user_id'])
        rows = await conn.fetch(
            """SELECT reel_id, post_id, video_url, thumbnail_url, caption,
                      duration, music_title, views_count, likes_count,
                      comments_count, tips_total, created_at
               FROM reels
               WHERE user_id = $1
               ORDER BY created_at DESC
               LIMIT $2 OFFSET $3""",
            user['user_id'], limit, offset,
        )
    items = []
    for r in rows:
        d = dict(r)
        d['tips_total'] = str(d['tips_total']) if d.get('tips_total') is not None else "0"
        d['created_at'] = d['created_at'].isoformat() if d.get('created_at') else None
        items.append(d)
    return {"items": items, "total": total or 0, "page": page, "limit": limit}


@router.get("/reels")
async def list_reels(request: Request, page: int = 1, limit: int = 10):
    """TikTok-style reels feed. Ordering: recent + engagement."""
    user = await get_current_user(request)
    if page < 1: page = 1
    if limit > 30: limit = 30
    offset = (page - 1) * limit
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.*, u.first_name, u.last_name, u.avatar, u.username, u.is_verified, u.is_pro,
                EXISTS(SELECT 1 FROM post_likes WHERE post_id = r.post_id AND user_id = $1) AS is_liked
            FROM reels r
            JOIN users u ON r.user_id = u.user_id
            ORDER BY
                (r.likes_count + r.views_count * 0.1 + r.tips_total * 0.5) * 
                (1.0 / (1 + EXTRACT(EPOCH FROM (NOW() - r.created_at)) / 86400.0)) DESC,
                r.created_at DESC
            LIMIT $2 OFFSET $3
        """, user['user_id'], limit, offset)
        out = []
        for r in rows:
            d = dict(r)
            d['tips_total'] = str(d['tips_total'])
            d['created_at'] = d['created_at'].isoformat()
            out.append({
                'reel_id': d['reel_id'],
                'post_id': d['post_id'],
                'video_url': d['video_url'],
                'thumbnail_url': d['thumbnail_url'],
                'caption': d['caption'],
                'duration': d['duration'],
                'music_title': d['music_title'],
                'views_count': d['views_count'],
                'likes_count': d['likes_count'],
                'comments_count': d['comments_count'],
                'tips_total': d['tips_total'],
                'shares_count': d.get('shares_count') or 0,  # iter215
                'is_liked': d['is_liked'],
                'created_at': d['created_at'],
                'user': {
                    'user_id': d['user_id'],
                    'name': f"{d['first_name']} {d['last_name']}".strip() or d['username'],
                    'username': d['username'],
                    'avatar': d['avatar'] or '',
                    'is_verified': d['is_verified'],
                    'is_pro': d['is_pro'],
                },
            })
        return out


@router.get("/reels/{reel_id}")
async def get_reel(reel_id: str, request: Request):
    """iter217 — Single reel fetch for the deep-link flow
    (`GET /reels/{reel_id}` triggered from the URL sent over WhatsApp /
    iMessage / Twitter share). Same shape as items in `list_reels` so
    the frontend can prepend it to the in-memory list.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow("""
            SELECT r.*, u.first_name, u.last_name, u.avatar, u.username,
                   u.is_verified, u.is_pro,
                   EXISTS(SELECT 1 FROM post_likes WHERE post_id = r.post_id
                          AND user_id = $1) AS is_liked
            FROM reels r JOIN users u ON r.user_id = u.user_id
            WHERE r.reel_id = $2
        """, user['user_id'], reel_id)
    if not r:
        raise HTTPException(status_code=404, detail="Reel introuvable")
    d = dict(r)
    return {
        'reel_id':        d['reel_id'],
        'post_id':        d['post_id'],
        'video_url':      d['video_url'],
        'thumbnail_url':  d['thumbnail_url'],
        'caption':        d['caption'],
        'duration':       d['duration'],
        'music_title':    d['music_title'],
        'views_count':    d['views_count'],
        'likes_count':    d['likes_count'],
        'comments_count': d['comments_count'],
        'tips_total':     str(d['tips_total']),
        'shares_count':   d.get('shares_count') or 0,
        'is_liked':       d['is_liked'],
        'created_at':     d['created_at'].isoformat(),
        'user': {
            'user_id':     d['user_id'],
            'name':        f"{d['first_name']} {d['last_name']}".strip() or d['username'],
            'username':    d['username'],
            'avatar':      d['avatar'] or '',
            'is_verified': d['is_verified'],
            'is_pro':      d['is_pro'],
        },
    }


@router.post("/reels/{reel_id}/view")
async def view_reel(reel_id: str, request: Request, watched_seconds: int = 0):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM reel_views WHERE reel_id = $1 AND user_id = $2",
            reel_id, user['user_id'])
        if not exists:
            await conn.execute("""
                INSERT INTO reel_views (reel_id, user_id, watched_seconds)
                VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
            """, reel_id, user['user_id'], max(0, watched_seconds))
            await conn.execute("UPDATE reels SET views_count = views_count + 1 WHERE reel_id = $1", reel_id)
        return {"ok": True}


@router.post("/reels/{reel_id}/like")
async def toggle_reel_like(reel_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        reel = await conn.fetchrow("SELECT post_id FROM reels WHERE reel_id = $1", reel_id)
        if not reel:
            raise HTTPException(status_code=404, detail="Reel introuvable")
        existing = await conn.fetchval(
            "SELECT 1 FROM post_likes WHERE post_id = $1 AND user_id = $2",
            reel['post_id'], user['user_id'])
        async with conn.transaction():
            if existing:
                await conn.execute("DELETE FROM post_likes WHERE post_id = $1 AND user_id = $2",
                                   reel['post_id'], user['user_id'])
                await conn.execute("UPDATE reels SET likes_count = GREATEST(0, likes_count - 1) WHERE reel_id = $1", reel_id)
                await conn.execute("UPDATE posts SET likes_count = GREATEST(0, likes_count - 1) WHERE post_id = $1", reel['post_id'])
                return {"liked": False}
            else:
                await conn.execute("INSERT INTO post_likes (post_id, user_id) VALUES ($1, $2)",
                                   reel['post_id'], user['user_id'])
                await conn.execute("UPDATE reels SET likes_count = likes_count + 1 WHERE reel_id = $1", reel_id)
                await conn.execute("UPDATE posts SET likes_count = likes_count + 1 WHERE post_id = $1", reel['post_id'])
                return {"liked": True}


# ============================================================
# STORIES (24h expiration)
# ============================================================
class CreateStoryRequest(BaseModel):
    image_url: str = ""
    text: str = ""
    background_color: str = "#0F056B"
    filter_preset: str = ""  # iter150 — chosen filter preset id (mono/sepia/vivid/...)


@router.post("/stories")
async def create_story(req: CreateStoryRequest, request: Request):
    user = await get_current_user(request)
    if not req.image_url and not req.text.strip():
        raise HTTPException(status_code=400, detail="Image ou texte requis")
    pool = await get_pool()
    async with pool.acquire() as conn:
        story_id = f"story_{uuid.uuid4().hex[:12]}"
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        # Whitelist the preset id to avoid user-controlled string injection.
        preset = (req.filter_preset or "").strip().lower()
        if preset and not preset.replace("-", "").replace("_", "").isalnum():
            preset = ""
        await conn.execute("""
            INSERT INTO stories (story_id, user_id, image_url, text, background_color,
                                  expires_at, filter_preset)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, story_id, user['user_id'], req.image_url, req.text.strip()[:500],
             req.background_color[:20], expires_at, preset[:32] or None)
        return {"story_id": story_id, "expires_at": expires_at.isoformat()}


@router.get("/stories")
async def list_active_stories(request: Request):
    """Grouped by user. Returns each user's active stories (non-expired)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.*, u.first_name, u.last_name, u.avatar, u.username,
                EXISTS(SELECT 1 FROM story_views WHERE story_id = s.story_id AND user_id = $1) AS is_viewed
            FROM stories s
            JOIN users u ON s.user_id = u.user_id
            WHERE s.expires_at > NOW()
            ORDER BY s.user_id, s.created_at ASC
        """, user['user_id'])
        # Group by user
        groups = {}
        for r in rows:
            uid = r['user_id']
            if uid not in groups:
                groups[uid] = {
                    'user_id': uid,
                    'name': f"{r['first_name']} {r['last_name']}".strip() or r['username'],
                    'avatar': r['avatar'] or '',
                    'stories': [],
                    'all_viewed': True,
                }
            groups[uid]['stories'].append({
                'story_id': r['story_id'],
                'image_url': r['image_url'],
                'text': r['text'],
                'background_color': r['background_color'],
                'views_count': r['views_count'],
                'expires_at': r['expires_at'].isoformat(),
                'created_at': r['created_at'].isoformat(),
                'is_viewed': r['is_viewed'],
            })
            if not r['is_viewed']:
                groups[uid]['all_viewed'] = False
        # Put current user first, then unseen first
        result = list(groups.values())
        result.sort(key=lambda g: (g['user_id'] != user['user_id'], g['all_viewed']))
        return result


@router.post("/stories/{story_id}/view")
async def view_story(story_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM story_views WHERE story_id = $1 AND user_id = $2",
            story_id, user['user_id'])
        if not exists:
            await conn.execute("""
                INSERT INTO story_views (story_id, user_id) VALUES ($1, $2)
                ON CONFLICT DO NOTHING
            """, story_id, user['user_id'])
            await conn.execute("UPDATE stories SET views_count = views_count + 1 WHERE story_id = $1", story_id)
        return {"ok": True}


@router.delete("/stories/{story_id}")
async def delete_story(story_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        story = await conn.fetchrow("SELECT user_id FROM stories WHERE story_id = $1", story_id)
        if not story:
            raise HTTPException(status_code=404, detail="Story introuvable")
        if story['user_id'] != user['user_id'] and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Non autorisé")
        await conn.execute("DELETE FROM stories WHERE story_id = $1", story_id)
        return {"deleted": True}


# ============================================================
# TIP (wallet transfer to content creator)
# ============================================================
class TipRequest(BaseModel):
    target_type: str  # 'post' | 'reel'
    target_id: str
    amount: float
    message: str = ""


@router.post("/tip")
async def send_tip(req: TipRequest, request: Request):
    user = await get_current_user(request)
    if req.target_type not in ('post', 'reel'):
        raise HTTPException(status_code=400, detail="target_type invalide")
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Montant doit être positif")
    if req.amount < 50:
        raise HTTPException(status_code=400, detail="Montant minimum 50 XAF")
    amount = Decimal(str(req.amount))

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Locate recipient
            if req.target_type == 'post':
                target = await conn.fetchrow("SELECT user_id FROM posts WHERE post_id = $1", req.target_id)
            else:
                target = await conn.fetchrow("SELECT user_id FROM reels WHERE reel_id = $1", req.target_id)
            if not target:
                raise HTTPException(status_code=404, detail="Contenu introuvable")
            recipient_id = target['user_id']
            if recipient_id == user['user_id']:
                raise HTTPException(status_code=400, detail="Impossible de se tipper soi-même")

            wallet = await conn.fetchrow("SELECT balance FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
            if not wallet or wallet['balance'] < amount:
                raise HTTPException(status_code=400, detail="Solde insuffisant")

            # Transfer
            await conn.execute("UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                               amount, datetime.now(timezone.utc), user['user_id'])
            await conn.execute("UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                               amount, datetime.now(timezone.utc), recipient_id)

            # Record tip
            tip_id = f"tip_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO tips (tip_id, sender_id, recipient_id, target_type, target_id, amount, message)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, tip_id, user['user_id'], recipient_id, req.target_type, req.target_id, amount, req.message[:500])

            # Update target counters
            if req.target_type == 'post':
                await conn.execute("""
                    UPDATE posts SET tips_total = tips_total + $1, tips_count = tips_count + 1 WHERE post_id = $2
                """, amount, req.target_id)
            else:
                await conn.execute("""
                    UPDATE reels SET tips_total = tips_total + $1 WHERE reel_id = $2
                """, amount, req.target_id)

            # Transaction record
            tx_id = f"tp_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO transactions (tx_id, from_user_id, to_user_id, type, amount, status, notes, reference)
                VALUES ($1, $2, $3, 'tip', $4, 'completed', $5, $6)
            """, tx_id, user['user_id'], recipient_id, amount,
                 f"Tip {req.target_type}", f"{req.target_type}:{req.target_id}")

            # Notification to recipient
            sender_name = f"{user['first_name']} {user['last_name']}".strip() or user['username']
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'tip_received', 'Tip reçu !', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", recipient_id,
                 f"{sender_name} vous a envoyé {amount} XAF" + (f" : \"{req.message}\"" if req.message else ""))

            result = {
                "tip_id": tip_id,
                "amount": str(amount),
                "recipient_id": recipient_id,
                "message": "Tip envoyé !",
            }
    # Realtime push (outside transaction, best-effort)
    try:
        await notify_tip(
            recipient_id=recipient_id,
            sender={
                "user_id": user['user_id'],
                "name": (f"{user['first_name']} {user['last_name']}".strip() or user['username']),
                "avatar": user.get('avatar', '') or '',
            },
            amount=str(amount),
            target_type=req.target_type,
            target_id=req.target_id,
            message=req.message or "",
        )
    except Exception as e:
        logger.warning(f"notify_tip failed: {e}")
    return result


@router.get("/tips/received")
async def tips_received(request: Request, limit: int = 50):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT t.*, u.first_name, u.last_name, u.avatar, u.username
            FROM tips t JOIN users u ON t.sender_id = u.user_id
            WHERE t.recipient_id = $1 ORDER BY t.created_at DESC LIMIT $2
        """, user['user_id'], limit)
        return [
            {
                'tip_id': r['tip_id'],
                'sender': {
                    'user_id': r['sender_id'],
                    'name': f"{r['first_name']} {r['last_name']}".strip() or r['username'],
                    'avatar': r['avatar'] or '',
                },
                'target_type': r['target_type'],
                'target_id': r['target_id'],
                'amount': str(r['amount']),
                'message': r['message'],
                'created_at': r['created_at'].isoformat(),
            } for r in rows
        ]
