import logging
from fastapi import APIRouter, Request, Query
from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/")
async def get_notifications(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM notifications WHERE user_id = $1", user['user_id'])
        unread = await conn.fetchval("SELECT COUNT(*) FROM notifications WHERE user_id = $1 AND is_read = FALSE", user['user_id'])
        rows = await conn.fetch("""
            SELECT * FROM notifications WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3
        """, user['user_id'], limit, offset)
        notifs = []
        for row in rows:
            n = dict(row)
            n['created_at'] = n['created_at'].isoformat()
            notifs.append(n)
        return {"notifications": notifs, "total": count, "unread": unread, "page": page}


@router.put("/read/{notif_id}")
async def mark_read(notif_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE notifications SET is_read = TRUE WHERE notif_id = $1 AND user_id = $2", notif_id, user['user_id'])
        return {"message": "Marked as read"}


@router.put("/read-all")
async def mark_all_read(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = $1", user['user_id'])
        return {"message": "All marked as read"}
