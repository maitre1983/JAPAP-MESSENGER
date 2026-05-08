"""
JAPAP Messenger — Realtime Notification Broadcaster
Central utility to push Socket.io events to online users.
Priority levels: 'high' (tip, money), 'medium' (comment), 'low' (like).
Coalescing: low-priority likes grouped per post within 3 seconds (future).

Iter70: high-priority events also trigger a Web Push fan-out so the user
is notified even when the tab is closed / phone is sleeping. The push is
best-effort: if pywebpush isn't configured, every helper below still works
over Socket.IO as before — no regression for realtime-only traffic.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level references set from server.py at startup
_sio = None
_connected_users = None


def init_realtime(sio_instance, connected_users_ref):
    """Called once from server.py startup to wire Socket.IO."""
    global _sio, _connected_users
    _sio = sio_instance
    _connected_users = connected_users_ref


def _user_sids(user_id: str):
    if not _connected_users:
        return []
    return list(_connected_users.get(user_id, []))


def _user_is_online(user_id: str) -> bool:
    return bool(_user_sids(user_id))


def _fire_and_forget_push(user_id: str, payload: dict) -> None:
    """Schedule a Web Push without awaiting — never blocks the caller.

    Iter71: delegates to `services.push_service.schedule_push`, which now
    targets OneSignal by External ID (our internal user_id). Kept as a
    thin indirection so all call sites below stay unchanged after the
    VAPID → OneSignal migration."""
    try:
        from services.push_service import schedule_push
        schedule_push(user_id, payload)
    except Exception as e:
        logger.warning(f"push schedule failed user={user_id}: {e}")


async def push_to_user(user_id: str, event: str, payload: dict):
    """Emit a Socket.IO event to all sids of a user (no-op if offline)."""
    if not _sio:
        return
    sids = _user_sids(user_id)
    for s in sids:
        try:
            await _sio.emit(event, payload, room=s)
        except Exception as e:
            logger.warning(f"realtime emit failed: {e}")


async def notify_tip(recipient_id: str, sender: dict, amount: str, target_type: str, target_id: str, message: str = ""):
    await push_to_user(recipient_id, "notify_tip", {
        "type": "tip",
        "priority": "high",
        "sender": sender,
        "amount": amount,
        "target_type": target_type,
        "target_id": target_id,
        "message": message,
    })
    # Web Push: tips are high-value, push even if the user is currently online
    # (some users use JAPAP across devices).
    sender_name = (sender or {}).get("display_name") or (sender or {}).get("username") or "Quelqu'un"
    _fire_and_forget_push(recipient_id, {
        "title": f"💸 {sender_name} t'a envoyé un tip",
        "body": f"{amount} — {message[:80]}" if message else str(amount),
        "url": f"/{target_type}/{target_id}" if target_type and target_id else "/feed",
        "tag": f"tip:{target_type}:{target_id}",
        "type": "tip",
        "icon": "/pwa-icon-192.png",
        "badge": "/favicon-32.png",
    })


async def notify_like(recipient_id: str, sender: dict, target_type: str, target_id: str):
    # Low-priority: socket only, no Web Push (notification pollution).
    await push_to_user(recipient_id, "notify_like", {
        "type": "like",
        "priority": "low",
        "sender": sender,
        "target_type": target_type,
        "target_id": target_id,
    })


async def notify_comment(recipient_id: str, sender: dict, target_type: str, target_id: str, text: str):
    await push_to_user(recipient_id, "notify_comment", {
        "type": "comment",
        "priority": "medium",
        "sender": sender,
        "target_type": target_type,
        "target_id": target_id,
        "preview": text[:140],
    })
    # Web Push only if the user is offline (socket-disconnected).
    if not _user_is_online(recipient_id):
        sender_name = (sender or {}).get("display_name") or (sender or {}).get("username") or "Quelqu'un"
        _fire_and_forget_push(recipient_id, {
            "title": f"💬 {sender_name} a commenté",
            "body": text[:140],
            "url": f"/{target_type}/{target_id}" if target_type and target_id else "/feed",
            "tag": f"comment:{target_type}:{target_id}",
            "type": "comment",
            "icon": "/pwa-icon-192.png",
            "badge": "/favicon-32.png",
        })


async def notify_money(recipient_id: str, sender: dict, amount: str, note: str = ""):
    """In-chat money transfer notification."""
    await push_to_user(recipient_id, "notify_money", {
        "type": "money",
        "priority": "high",
        "sender": sender,
        "amount": amount,
        "note": note,
    })
    sender_name = (sender or {}).get("display_name") or (sender or {}).get("username") or "Quelqu'un"
    _fire_and_forget_push(recipient_id, {
        "title": f"💰 {sender_name} t'a envoyé {amount}",
        "body": note[:140] if note else "Ouvre le chat pour voir le virement.",
        "url": "/chat",
        "tag": f"money:{recipient_id}",
        "type": "money",
        "icon": "/pwa-icon-192.png",
        "badge": "/favicon-32.png",
    })


async def notify_connect_revshare(recipient_id: str, amount_usd: str, amount_local: str,
                                   currency: str, plan_name: str, pct: float = 2.0):
    """A 2% revshare credit landed on a Connect hotspot owner's wallet."""
    await push_to_user(recipient_id, "notify_connect_revshare", {
        "type": "connect_revshare",
        "priority": "high",
        "amount_usd": str(amount_usd),
        "amount_local": str(amount_local) if amount_local is not None else None,
        "currency": currency or "USD",
        "plan_name": plan_name or "",
        "pct": float(pct or 0),
    })
    local = f"{amount_local} {currency}" if amount_local else f"${amount_usd}"
    _fire_and_forget_push(recipient_id, {
        "title": f"🎉 Revshare Connect : +{local}",
        "body": f"Plan {plan_name or ''} · {pct}% revshare".strip(),
        "url": "/wallet",
        "tag": "revshare",
        "type": "connect_revshare",
        "icon": "/pwa-icon-192.png",
        "badge": "/favicon-32.png",
    })


async def notify_new_message_offline(recipient_id: str, sender_name: str, preview: str, conv_id: str):
    """Web Push a chat DM/group message to a user ONLY when they're offline.
    Called from messaging.py after the socket emit — does NOT emit a second
    socket event (the caller already did that)."""
    if _user_is_online(recipient_id):
        return
    _fire_and_forget_push(recipient_id, {
        "title": f"💬 {sender_name}",
        "body": preview[:140] or "Nouveau message",
        "url": f"/chat/{conv_id}" if conv_id else "/chat",
        "tag": f"chat:{conv_id}",
        "type": "message",
        "icon": "/pwa-icon-192.png",
        "badge": "/favicon-32.png",
    })


