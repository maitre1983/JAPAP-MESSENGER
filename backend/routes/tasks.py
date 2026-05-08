"""
JAPAP Tasks — global, cross-conversation action layer
=====================================================

Flattens all `call_summary` structured messages into a per-user view of
action items assigned to them. Tasks are NEVER duplicated: we read them
straight from `messages.structured_data.action_items[]` on every request,
so mutations (toggle/reassign) via the existing
    PATCH /api/calls/summary/action-items/{msg_id}/{item_id}
endpoints automatically reflect everywhere in real time.

Endpoints :
    GET /api/tasks/my              filtered + paginated list
    GET /api/tasks/my/count        small-payload badge counter
"""
from datetime import datetime, timezone
from typing import Optional, Literal
import logging

from fastapi import APIRouter, HTTPException, Request, Query

from routes.auth import get_current_user
from database import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ───── helpers ──────────────────────────────────────────────────────────────

def _jb(x):
    """Coerce a JSONB-ish value (str / dict / list / None) to a Python obj."""
    if x is None:
        return None
    if isinstance(x, (dict, list)):
        return x
    try:
        import json as _json
        return _json.loads(x)
    except Exception:
        return None


async def _fetch_flattened(conn, user_id: str, status: str, limit: int, offset: int):
    """Stream every `call_summary` message where the current user is an
    assignee of at least one action_item, newest first, and flatten to task
    rows. Uses a jsonb EXISTS predicate for performance.
    """
    rows = await conn.fetch(
        """
        SELECT m.msg_id, m.conv_id, m.structured_data, m.created_at, m.updated_at,
               m.call_session_id, m.sender_id,
               c.title AS conv_title, c.type AS conv_type
        FROM messages m
        LEFT JOIN conversations c ON c.conv_id = m.conv_id
        WHERE m.message_type = 'call_summary'
          AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(m.structured_data->'action_items') AS ai
            WHERE ai->>'who_user_id' = $1
          )
        ORDER BY m.created_at DESC
        """,
        user_id,
    )

    # Conversation title fallback: for DMs we want the peer's name, not ''.
    dm_peer_name_cache: dict[str, str] = {}
    peer_ids_needed: set[str] = set()
    for r in rows:
        if (r['conv_type'] or '') == 'direct' and not (r['conv_title'] or ''):
            # we'll look up the peer later in a batched query
            peer_ids_needed.add(r['conv_id'])
    if peer_ids_needed:
        peers = await conn.fetch(
            """
            SELECT cp.conv_id, u.user_id, u.first_name, u.last_name, u.username
            FROM conversation_participants cp
            JOIN users u ON u.user_id = cp.user_id
            WHERE cp.conv_id = ANY($1::text[]) AND cp.user_id <> $2
            """,
            list(peer_ids_needed), user_id,
        )
        for p in peers:
            name = f"{p['first_name'] or ''} {p['last_name'] or ''}".strip() or p['username'] or 'Direct'
            dm_peer_name_cache[p['conv_id']] = name

    flat = []
    for r in rows:
        sd = _jb(r['structured_data']) or {}
        items = sd.get('action_items') or []
        conv_title = (r['conv_title'] or dm_peer_name_cache.get(r['conv_id'], '')).strip() or 'Conversation'
        for ai in items:
            if not isinstance(ai, dict):
                continue
            if ai.get('who_user_id') != user_id:
                continue
            is_done = bool(ai.get('done'))
            if status == 'pending' and is_done:
                continue
            if status == 'done' and not is_done:
                continue
            flat.append({
                "item_id": ai.get('id') or '',
                "msg_id": r['msg_id'],
                "conv_id": r['conv_id'],
                "conv_title": conv_title,
                "conv_type": r['conv_type'] or 'direct',
                "call_session_id": r['call_session_id'],
                "what": ai.get('what') or '',
                "due": ai.get('due') or '',
                "done": is_done,
                "done_by_user_id": ai.get('done_by_user_id'),
                "done_at": ai.get('done_at'),
                "who_user_id": ai.get('who_user_id'),
                "who_text": ai.get('who_text') or '',
                "created_at": r['created_at'].isoformat() if r['created_at'] else None,
                "updated_at": r['updated_at'].isoformat() if r['updated_at'] else None,
                "summary_preview": (sd.get('summary') or '')[:120],
            })
    total = len(flat)
    return flat[offset: offset + limit], total


# ───── routes ───────────────────────────────────────────────────────────────

@router.get("/my")
async def my_tasks(
    request: Request,
    status: Literal["all", "pending", "done"] = Query("all"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Return the current user's tasks, flattened from every call_summary
    message across all conversations they participate in.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        items, total = await _fetch_flattened(conn, user['user_id'], status, limit, offset)
    return {
        "items": items,
        "total": total,
        "status": status,
        "limit": limit,
        "offset": offset,
    }


@router.get("/my/count")
async def my_tasks_count(request: Request):
    """Tiny payload for the sidebar / profile badge (pending count)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.structured_data
            FROM messages m
            WHERE m.message_type = 'call_summary'
              AND EXISTS (
                SELECT 1 FROM jsonb_array_elements(m.structured_data->'action_items') AS ai
                WHERE ai->>'who_user_id' = $1
              )
            """,
            user['user_id'],
        )
    pending = done = 0
    for r in rows:
        sd = _jb(r['structured_data']) or {}
        for ai in sd.get('action_items') or []:
            if not isinstance(ai, dict) or ai.get('who_user_id') != user['user_id']:
                continue
            if ai.get('done'):
                done += 1
            else:
                pending += 1
    return {"pending": pending, "done": done, "total": pending + done}
