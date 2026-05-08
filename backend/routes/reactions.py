"""
Quick emoji reactions — Phase 5 polish.
Users can react to any post with one of the 6 quick emojis.
Toggle behaviour: re-reacting with the same emoji removes it.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from database import get_pool
from routes.auth import get_current_user

router = APIRouter(prefix="/api/feed", tags=["reactions"])

QUICK_EMOJIS = ["❤️", "😂", "😮", "😢", "👏", "🔥"]


class ReactRequest(BaseModel):
    emoji: str


@router.post("/posts/{post_id}/react")
async def react(post_id: str, req: ReactRequest, request: Request):
    user = await get_current_user(request)
    if req.emoji not in QUICK_EMOJIS:
        raise HTTPException(status_code=400, detail=f"Emoji non supporté. Choix: {QUICK_EMOJIS}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM posts WHERE post_id=$1", post_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Post introuvable")
        # Toggle this emoji — delete existing (any emoji) first to ensure 1 per user.
        await conn.execute(
            "DELETE FROM post_reactions WHERE post_id=$1 AND user_id=$2",
            post_id, user['user_id'],
        )
        await conn.execute(
            "INSERT INTO post_reactions (post_id, user_id, emoji) VALUES ($1, $2, $3)",
            post_id, user['user_id'], req.emoji,
        )
        counts = await conn.fetch(
            "SELECT emoji, COUNT(*) AS n FROM post_reactions WHERE post_id=$1 GROUP BY emoji",
            post_id,
        )
    return {
        "reacted": True,
        "emoji": req.emoji,
        "counts": {r['emoji']: int(r['n']) for r in counts},
    }


@router.delete("/posts/{post_id}/react")
async def unreact(post_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM post_reactions WHERE post_id=$1 AND user_id=$2",
            post_id, user['user_id'],
        )
        counts = await conn.fetch(
            "SELECT emoji, COUNT(*) AS n FROM post_reactions WHERE post_id=$1 GROUP BY emoji",
            post_id,
        )
    removed = not res.endswith(" 0")
    return {
        "removed": removed,
        "counts": {r['emoji']: int(r['n']) for r in counts},
    }


@router.get("/posts/{post_id}/reactions")
async def list_reactions(post_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT emoji, COUNT(*) AS n FROM post_reactions WHERE post_id=$1 GROUP BY emoji",
            post_id,
        )
        my = await conn.fetchval(
            "SELECT emoji FROM post_reactions WHERE post_id=$1 AND user_id=$2",
            post_id, user['user_id'],
        )
    return {
        "counts": {r['emoji']: int(r['n']) for r in rows},
        "my_emoji": my,
    }
