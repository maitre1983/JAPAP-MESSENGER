"""
JAPAP — Social notifications (iter78)
======================================
Centralised helper that sends **both** an in-app notification row and (if the
recipient opted-in) an OneSignal web-push.

Every social event (follow, follow_accept, like, comment, message) flows
through one of these helpers so we can:
  - respect the user's per-event opt-ins (`notify_follow`, …)
  - throttle (same actor → same target → same event within 60s = dedup)
  - extend to batched digests later without touching call sites
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from database import get_pool
from services.push_service import send_push_to_user

logger = logging.getLogger(__name__)


# ── Event types — kept stable so the mobile/web clients can route them. ──
EVT_FOLLOW = "social.follow"
EVT_FOLLOW_REQUEST = "social.follow_request"
EVT_FOLLOW_ACCEPTED = "social.follow_accepted"
EVT_POST_LIKE = "social.post_like"
EVT_POST_COMMENT = "social.post_comment"
EVT_MESSAGE = "social.message"

# Map event → column on `users` that gates the push.
PREF_COLUMN = {
    EVT_FOLLOW: "notify_follow",
    EVT_FOLLOW_REQUEST: "notify_follow",
    EVT_FOLLOW_ACCEPTED: "notify_follow_accept",
    EVT_POST_LIKE: "notify_likes",
    EVT_POST_COMMENT: "notify_comments",
    EVT_MESSAGE: "notify_messages",
}


async def _dedupe_recent(conn, actor_id: str, target_id: str, event_type: str, window_sec: int = 60) -> bool:
    """Return True if an identical notification was recorded within `window_sec`."""
    since = datetime.now(timezone.utc) - timedelta(seconds=window_sec)
    # We store actor_id inside the JSONB `data` column since the legacy
    # `notifications` schema doesn't have a dedicated column.
    row = await conn.fetchrow(
        """SELECT 1 FROM notifications
           WHERE user_id = $1 AND type = $2 AND created_at >= $3
           AND data ->> 'actor_id' = $4
           LIMIT 1""",
        target_id, event_type, since, actor_id,
    )
    return bool(row)


async def send_social_notification(
    event_type: str,
    actor: dict,
    target_user_id: str,
    title: str,
    body: str,
    *,
    deep_link: Optional[str] = None,
    extra_data: Optional[dict] = None,
) -> None:
    """Persist an in-app row + fire an OneSignal push (if opted-in).

    Never raises — notifications are best-effort, a DB glitch here should
    not block the main mutation (follow/like/comment/…).
    """
    try:
        pool = await get_pool()
        pref_col = PREF_COLUMN.get(event_type)
        async with pool.acquire() as conn:
            # Guard against self-notifications.
            if actor and actor.get('user_id') == target_user_id:
                return
            # Recipient opt-in check.
            if pref_col:
                opted_in = await conn.fetchval(
                    f"SELECT {pref_col} FROM users WHERE user_id = $1",
                    target_user_id,
                )
                if opted_in is False:
                    return
            # 60s dedup on (actor, target, type).
            if actor and await _dedupe_recent(conn, actor['user_id'], target_user_id, event_type):
                return
            # Persist the in-app row. The `notifications` table schema stores
            # title + message text columns and a JSONB `data` blob for any
            # extensions (actor, deep_link, follow_request_id, post_id, …).
            import json as _json
            import uuid as _uuid
            data_blob = {
                "actor_id": actor.get('user_id') if actor else None,
                "actor": {
                    "user_id": actor.get('user_id'),
                    "first_name": actor.get('first_name') or '',
                    "last_name": actor.get('last_name') or '',
                    "avatar": actor.get('avatar') or '',
                    "username": actor.get('username') or '',
                } if actor else None,
                "deep_link": deep_link,
                **(extra_data or {}),
            }
            await conn.execute(
                """INSERT INTO notifications (notif_id, user_id, type, title, message, data, is_read, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, FALSE, NOW())""",
                f"notif_{_uuid.uuid4().hex[:16]}",
                target_user_id,
                event_type, title, body, _json.dumps(data_blob),
            )
        # iter164 — Realtime Socket.IO push for INSTANT bell-badge update.
        # Browsers connected via the realtime channel receive the new
        # notification without polling. Falls back gracefully when the
        # broadcaster isn't initialised (CLI tools / test scripts).
        try:
            from routes.realtime import push_to_user
            await push_to_user(target_user_id, "notification", {
                "type": event_type,
                "title": title,
                "body": body,
                "data": data_blob,
            })
        except Exception as _re:
            logger.debug(f"realtime emit (notification/{event_type}) failed: {_re}")
        # Push (best-effort, outside the DB transaction).
        try:
            await send_push_to_user(target_user_id, {
                "title": title,
                "body": body,
                "url": deep_link or "/notifications",
                "data": {"type": event_type, **(extra_data or {})},
            })
        except Exception as e:
            logger.debug(f"Push send failed for {target_user_id}: {e}")
    except Exception as e:
        logger.warning(f"send_social_notification({event_type}) failed: {e}")
