import uuid
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Query, Body
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user
from services import push_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/push", tags=["push"])


class RegisterPushRequest(BaseModel):
    token: str
    platform: str = "web"

class SendPushRequest(BaseModel):
    user_id: str
    title: str
    message: str
    data: dict = {}


@router.post("/register")
async def register_push_token(req: RegisterPushRequest, request: Request):
    """Register a push notification token for the current user."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Store token in user_sessions or a dedicated table
        await conn.execute("""
            INSERT INTO admin_settings (key, value, updated_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = $3
        """, f"push_token_{user['user_id']}", req.token, datetime.now(timezone.utc))
        return {"message": "Push token registered"}


@router.get("/notifications")
async def get_push_notifications(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50)):
    """Get paginated notifications for the current user."""
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM notifications WHERE user_id = $1", user['user_id'])
        unread = await conn.fetchval("SELECT COUNT(*) FROM notifications WHERE user_id = $1 AND is_read = FALSE", user['user_id'])
        rows = await conn.fetch("""
            SELECT * FROM notifications WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3
        """, user['user_id'], limit, offset)
        notifs = []
        for r in rows:
            n = dict(r)
            n['created_at'] = n['created_at'].isoformat()
            notifs.append(n)
        return {"notifications": notifs, "total": total, "unread": unread, "page": page}


@router.put("/read/{notif_id}")
async def mark_notification_read(notif_id: str, request: Request):
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


@router.post("/send")
async def send_push_notification(req: SendPushRequest, request: Request):
    """Admin: send a push notification to a specific user."""
    admin = await get_current_user(request)
    if admin.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin only")

    pool = await get_pool()
    async with pool.acquire() as conn:
        notif_id = f"notif_{uuid.uuid4().hex[:12]}"
        await conn.execute("""
            INSERT INTO notifications (notif_id, user_id, type, title, message, data)
            VALUES ($1, $2, 'push', $3, $4, $5)
        """, notif_id, req.user_id, req.title, req.message, str(req.data))

        # In-app notification created. For real push (FCM/OneSignal),
        # the token would be fetched and an external API called here.
        return {"message": "Notification sent", "notif_id": notif_id}


@router.post("/broadcast")
async def broadcast_notification(req: SendPushRequest, request: Request):
    """Admin: send notification to all users."""
    admin = await get_current_user(request)
    if admin.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin only")

    pool = await get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users WHERE is_active = TRUE LIMIT 1000")
        count = 0
        for u in users:
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'broadcast', $3, $4)
            """, notif_id, u['user_id'], req.title, req.message)
            count += 1
        return {"message": f"Broadcast sent to {count} users"}


# ══════════════════════════════════════════════════════════════════════════
#  Iter71 — Web Push via OneSignal (replaces iter70's VAPID stack)
#  The browser subscription is now managed entirely by the OneSignal SDK.
#  Backend only needs two public surfaces:
#    • GET  /public-key     — returns {app_id, provider:"onesignal"} so the
#                              frontend can init the SDK without shipping the
#                              App ID in the JS bundle (it already ships via
#                              REACT_APP_ONESIGNAL_APP_ID, but this endpoint
#                              is also used by ops for health-checks).
#    • POST /test-vapid     — admin-only test send (kept the same route name
#                              to avoid breaking existing admin tooling that
#                              already hits this URL).
#
#  The legacy /subscribe and /unsubscribe endpoints from iter70 are removed:
#  OneSignal tracks subscriptions on its side via OneSignal.login(user_id)
#  and OneSignal.User.PushSubscription.optIn()/optOut() on the client.
# ══════════════════════════════════════════════════════════════════════════


@router.get("/public-key")
async def get_push_config():
    """Expose the push provider config so clients + ops can discover which
    provider is active and whether it's properly configured. The old VAPID
    endpoint name is kept for rollout-compat; the field `provider` tells
    the client which SDK to init."""
    configured = push_service.configured()
    return {
        "provider": "onesignal",
        "app_id": push_service.get_public_key(),
        "configured": configured,
        # Legacy iter70 fields (kept so old clients don't explode):
        "public_key": push_service.get_public_key(),
    }


@router.post("/test-vapid")
async def admin_test_push(request: Request, body: dict = Body(default={})):
    """Admin-only: dispatch a test push through OneSignal to verify the
    whole pipeline (login tagging + REST fan-out + client SW display).
    Route path kept for continuity with iter70 admin tooling."""
    admin = await get_current_user(request)
    if not admin or admin.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")
    target = body.get("user_id") or admin["user_id"]
    payload = push_service.build_payload(
        title=body.get("title", "JAPAP — Test push ✅"),
        body=body.get("body", "Votre configuration OneSignal fonctionne."),
        url=body.get("url", "/feed"),
        tag=body.get("tag", "test"),
        type_="test",
    )
    result = await push_service.send_push_to_user(target, payload)
    return {"ok": True, "target_user_id": target, **result}

