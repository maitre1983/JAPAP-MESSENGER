import uuid
import json
import logging
import os
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Query, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user
from routes.realtime import notify_money, push_to_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/messages", tags=["messages"])


class SendMessageRequest(BaseModel):
    to_user_id: str
    text: str
    media: str = ""

class ConversationMessageRequest(BaseModel):
    text: str
    media: str = ""
    reply_to: Optional[str] = None
    # iter237v — echo back to the optimistic sender for in-place replacement.
    client_msg_id: Optional[str] = None

class CreateGroupRequest(BaseModel):
    title: str
    member_ids: list
    description: str = ""

class AddMemberRequest(BaseModel):
    user_id: str


class SendMoneyInChatRequest(BaseModel):
    to_user_id: str
    amount: float
    note: str = ""


class ReactRequest(BaseModel):
    emoji: str


# Curated whitelist of modern emojis (prevents DB bloat/abuse)
ALLOWED_REACTIONS = {
    "❤️", "🔥", "💸", "😂", "👍", "😮", "😢", "🙏", "🎉", "💯",
    "👏", "💪", "🚀", "🤑", "💎", "✨", "😍", "🤝", "👌", "🫶",
}


async def _reactions_for_messages(conn, msg_ids: list, viewer_id: str):
    """Return {msg_id: [{emoji, count, mine}]}."""
    if not msg_ids:
        return {}
    rows = await conn.fetch("""
        SELECT msg_id, emoji, COUNT(*) AS count,
               BOOL_OR(user_id = $2) AS mine
        FROM message_reactions
        WHERE msg_id = ANY($1::varchar[])
        GROUP BY msg_id, emoji
        ORDER BY count DESC
    """, msg_ids, viewer_id)
    out = {}
    for r in rows:
        out.setdefault(r['msg_id'], []).append({
            'emoji': r['emoji'],
            'count': r['count'],
            'mine': bool(r['mine']),
        })
    return out


async def get_or_create_conversation(conn, user1_id: str, user2_id: str):
    row = await conn.fetchrow("""
        SELECT c.conv_id FROM conversations c
        JOIN conversation_participants cp1 ON c.conv_id = cp1.conv_id AND cp1.user_id = $1
        JOIN conversation_participants cp2 ON c.conv_id = cp2.conv_id AND cp2.user_id = $2
        WHERE c.type = 'direct' LIMIT 1
    """, user1_id, user2_id)
    
    if row:
        return row['conv_id']
    
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    await conn.execute("INSERT INTO conversations (conv_id, type, created_by) VALUES ($1, 'direct', $2)", conv_id, user1_id)
    await conn.execute("INSERT INTO conversation_participants (conv_id, user_id) VALUES ($1, $2)", conv_id, user1_id)
    await conn.execute("INSERT INTO conversation_participants (conv_id, user_id) VALUES ($1, $2)", conv_id, user2_id)
    return conv_id


@router.get("/conversations")
async def get_conversations(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        convs = await conn.fetch("""
            SELECT c.conv_id, c.type, c.title, c.updated_at,
                   (SELECT COUNT(*) FROM messages m 
                    WHERE m.conv_id = c.conv_id AND m.status = 'sent' AND m.sender_id != $1
                    AND m.created_at > COALESCE(
                        (SELECT last_read_at FROM conversation_participants WHERE conv_id = c.conv_id AND user_id = $1), 
                        '1970-01-01'
                    )) as unread_count
            FROM conversations c
            JOIN conversation_participants cp ON c.conv_id = cp.conv_id AND cp.user_id = $1
            ORDER BY c.updated_at DESC
        """, user['user_id'])
        
        result = []
        for conv in convs:
            conv_dict = dict(conv)
            conv_dict['updated_at'] = conv_dict['updated_at'].isoformat()
            
            participants = await conn.fetch("""
                SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_online, u.last_seen
                FROM conversation_participants cp JOIN users u ON cp.user_id = u.user_id
                WHERE cp.conv_id = $1 AND u.user_id != $2
            """, conv['conv_id'], user['user_id'])
            # iter237u — Serialise `last_seen` to ISO so the frontend can
            # render a "Vu il y a 14 min" / "Vu hier à 14h" hint when the
            # peer is offline.
            conv_dict['participants'] = [
                {**dict(p), 'last_seen': p['last_seen'].isoformat() if p['last_seen'] else None}
                for p in participants
            ]
            
            last_msg = await conn.fetchrow("""
                SELECT msg_id, sender_id, text, media, created_at FROM messages
                WHERE conv_id = $1 ORDER BY created_at DESC LIMIT 1
            """, conv['conv_id'])
            if last_msg:
                lm = dict(last_msg)
                lm['created_at'] = lm['created_at'].isoformat()
                conv_dict['last_message'] = lm
            else:
                conv_dict['last_message'] = None
            
            result.append(conv_dict)
        return result


@router.get("/conversations/{conv_id}")
async def get_messages(conv_id: str, request: Request, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=100)):
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        participant = await conn.fetchrow(
            "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2", conv_id, user['user_id'])
        if not participant:
            raise HTTPException(status_code=403, detail="Not in this conversation")
        
        msgs = await conn.fetch("""
            SELECT m.*, u.first_name as sender_name, u.avatar as sender_avatar
            FROM messages m JOIN users u ON m.sender_id = u.user_id
            WHERE m.conv_id = $1 ORDER BY m.created_at DESC LIMIT $2 OFFSET $3
        """, conv_id, limit, offset)
        
        await conn.execute("""
            UPDATE conversation_participants SET last_read_at = $1
            WHERE conv_id = $2 AND user_id = $3
        """, datetime.now(timezone.utc), conv_id, user['user_id'])
        
        result = []
        for msg in msgs:
            m = dict(msg)
            m['created_at'] = m['created_at'].isoformat()
            m['updated_at'] = m['updated_at'].isoformat()
            # Iter 59 — JSONB comes back as str from asyncpg (no codec set)
            if m.get('structured_data'):
                try:
                    m['structured_data'] = m['structured_data'] if isinstance(m['structured_data'], (dict, list)) else json.loads(m['structured_data'])
                except Exception:
                    m['structured_data'] = None
            result.append(m)
        result = list(reversed(result))
        # Batch-load reply previews for messages that quote another one.
        reply_ids = list({m['reply_to'] for m in result if m.get('reply_to')})
        preview_map = {}
        if reply_ids:
            qrows = await conn.fetch("""
                SELECT m.msg_id, m.text, m.media, m.sender_id,
                       u.first_name, u.last_name
                FROM messages m JOIN users u ON m.sender_id = u.user_id
                WHERE m.msg_id = ANY($1::varchar[])
            """, reply_ids)
            for q in qrows:
                preview_map[q['msg_id']] = {
                    'msg_id': q['msg_id'],
                    'text': (q['text'] or '')[:140],
                    'media': q['media'] or '',
                    'sender_id': q['sender_id'],
                    'sender_name': f"{q['first_name'] or ''} {q['last_name'] or ''}".strip(),
                }
        for m in result:
            m['reply_preview'] = preview_map.get(m.get('reply_to')) if m.get('reply_to') else None
        # Compute can_view_source for forwarded messages — true iff the viewer
        # is a participant of the original conversation (so clicking "Voir dans
        # la conversation" won't 403).
        fwd_conv_ids = list({m['forwarded_from_conv_id'] for m in result if m.get('forwarded_from_conv_id')})
        accessible_src_convs = set()
        if fwd_conv_ids:
            acc_rows = await conn.fetch(
                "SELECT conv_id FROM conversation_participants WHERE user_id = $1 AND conv_id = ANY($2::varchar[])",
                user['user_id'], fwd_conv_ids,
            )
            accessible_src_convs = {r['conv_id'] for r in acc_rows}
        for m in result:
            if m.get('forwarded_from_conv_id'):
                m['can_view_source'] = m['forwarded_from_conv_id'] in accessible_src_convs
            else:
                m['can_view_source'] = False
        # Attach reactions
        reactions_map = await _reactions_for_messages(conn, [m['msg_id'] for m in result], user['user_id'])
        for m in result:
            m['reactions'] = reactions_map.get(m['msg_id'], [])
        return result


@router.post("/send")
async def send_message(req: SendMessageRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1 AND is_active = TRUE", req.to_user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        
        conv_id = await get_or_create_conversation(conn, user['user_id'], req.to_user_id)
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        
        await conn.execute("""
            INSERT INTO messages (msg_id, conv_id, sender_id, text, media, status)
            VALUES ($1, $2, $3, $4, $5, 'sent')
        """, msg_id, conv_id, user['user_id'], req.text, req.media)
        
        await conn.execute("UPDATE conversations SET updated_at = $1 WHERE conv_id = $2", datetime.now(timezone.utc), conv_id)
        
        msg = await conn.fetchrow("SELECT * FROM messages WHERE msg_id = $1", msg_id)
        result = dict(msg)
        result['created_at'] = result['created_at'].isoformat()
        result['updated_at'] = result['updated_at'].isoformat()
        result['sender_name'] = f"{user['first_name']} {user['last_name']}".strip()
        result['sender_avatar'] = user.get('avatar', '')
        
        return {"message": result, "conv_id": conv_id}


@router.post("/conversations/{conv_id}/send")
async def send_to_conversation(conv_id: str, req: ConversationMessageRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        participant = await conn.fetchrow(
            "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2", conv_id, user['user_id'])
        if not participant:
            raise HTTPException(status_code=403, detail="Not in this conversation")
        
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        await conn.execute("""
            INSERT INTO messages (msg_id, conv_id, sender_id, text, media, reply_to, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'sent')
        """, msg_id, conv_id, user['user_id'], req.text, req.media, req.reply_to)
        
        await conn.execute("UPDATE conversations SET updated_at = $1 WHERE conv_id = $2", datetime.now(timezone.utc), conv_id)
        
        msg = await conn.fetchrow("SELECT * FROM messages WHERE msg_id = $1", msg_id)
        result = dict(msg)
        result['created_at'] = result['created_at'].isoformat()
        result['updated_at'] = result['updated_at'].isoformat()
        result['sender_name'] = f"{user['first_name']} {user['last_name']}".strip()
        result['sender_avatar'] = user.get('avatar', '')
        # iter237v — echo client_msg_id for optimistic-UI replacement.
        result['client_msg_id'] = req.client_msg_id

        # iter237v — also broadcast on the socket room so peers receive it
        # in real time (the REST endpoint is now used as a fallback when
        # the sender's own socket round-trip is failing in production).
        try:
            from server import sio  # lazy import to avoid circular dep at module-load
            await sio.emit('new_message', result, room=conv_id)
        except Exception:
            pass

        return result


class ForwardRequest(BaseModel):
    target_conv_ids: list
    extra_text: str = ""  # optional short note prepended


async def _fetch_full_message(msg_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT m.*, u.first_name, u.last_name, u.avatar
            FROM messages m JOIN users u ON m.sender_id = u.user_id
            WHERE m.msg_id = $1
        """, msg_id)
        if not row:
            return None
        d = dict(row)
        d['sender_name'] = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
        d['sender_avatar'] = row['avatar'] or ''
        d.pop('first_name', None)
        d.pop('last_name', None)
        d.pop('avatar', None)
        for k in ('created_at', 'updated_at'):
            if d.get(k):
                d[k] = d[k].isoformat()
        return d


@router.post("/{msg_id}/forward")
async def forward_message(msg_id: str, req: ForwardRequest, request: Request):
    """Forward a message to one or more conversations. Works across 1-1 and
    groups. Preserves text + media of the original message. Marks the new
    messages with is_forwarded=TRUE and propagates forward_depth so the UI
    can display how many times the message has been re-shared."""
    user = await get_current_user(request)
    if not req.target_conv_ids:
        raise HTTPException(status_code=400, detail="target_conv_ids required")
    targets = [str(c) for c in req.target_conv_ids[:20]]
    pool = await get_pool()
    delivered = []
    async with pool.acquire() as conn:
        src = await conn.fetchrow("""
            SELECT m.msg_id, m.text, m.media, m.conv_id, m.forward_depth
            FROM messages m
            JOIN conversation_participants cp
                ON cp.conv_id = m.conv_id AND cp.user_id = $2
            WHERE m.msg_id = $1
        """, msg_id, user['user_id'])
        if not src:
            raise HTTPException(status_code=404, detail="Message not found or access denied")
        for tgt_conv in targets:
            ok = await conn.fetchrow(
                "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2",
                tgt_conv, user['user_id'],
            )
            if not ok:
                continue
            new_msg_id = f"msg_{uuid.uuid4().hex[:16]}"
            combined_text = (req.extra_text + ("\n" if req.extra_text else "") + (src['text'] or '')).strip()
            new_depth = int(src['forward_depth'] or 0) + 1
            await conn.execute("""
                INSERT INTO messages
                    (msg_id, conv_id, sender_id, text, media, is_forwarded,
                     forwarded_from_msg_id, forwarded_from_conv_id, forward_depth, status)
                VALUES ($1, $2, $3, $4, $5, TRUE, $6, $7, $8, 'sent')
            """, new_msg_id, tgt_conv, user['user_id'],
                 combined_text, src['media'] or '',
                 src['msg_id'], src['conv_id'], new_depth)
            await conn.execute("UPDATE conversations SET updated_at = $1 WHERE conv_id = $2",
                datetime.now(timezone.utc), tgt_conv)
            delivered.append({"conv_id": tgt_conv, "msg_id": new_msg_id})
    # Fanout via Socket.io so recipients see it live
    try:
        from server import sio
        for d in delivered:
            mrow = await _fetch_full_message(d['msg_id'])
            if mrow:
                await sio.emit('new_message', mrow, room=d['conv_id'])
    except Exception as e:
        logger.warning(f"Forward socket fanout skipped: {e}")
    return {"forwarded": len(delivered), "messages": delivered}


@router.get("/{msg_id}/forward-chain")
async def get_forward_chain(msg_id: str, request: Request):
    """Walk the forwarding chain upstream from this message.
    Returns hops [earliest → latest] each with its conv_id, sender, a short
    text preview, created_at, and a `viewable` flag (true iff the caller is a
    participant of that conv). Capped at 10 hops to prevent accidental loops.
    Privacy: text/sender is only revealed for hops the user is allowed to see.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    hops = []
    visited = set()
    cursor = msg_id
    async with pool.acquire() as conn:
        for _ in range(10):
            if not cursor or cursor in visited:
                break
            visited.add(cursor)
            row = await conn.fetchrow("""
                SELECT m.msg_id, m.conv_id, m.text, m.media, m.created_at,
                       m.forwarded_from_msg_id, m.forward_depth, m.sender_id,
                       u.first_name, u.last_name,
                       EXISTS(
                         SELECT 1 FROM conversation_participants p
                         WHERE p.conv_id = m.conv_id AND p.user_id = $2
                       ) AS viewable
                FROM messages m
                JOIN users u ON m.sender_id = u.user_id
                WHERE m.msg_id = $1
            """, cursor, user['user_id'])
            if not row:
                break
            viewable = bool(row['viewable'])
            hops.append({
                'msg_id': row['msg_id'],
                'conv_id': row['conv_id'] if viewable else None,
                'forward_depth': row['forward_depth'] or 0,
                'created_at': row['created_at'].isoformat() if row['created_at'] else '',
                'sender_name': (
                    f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
                    if viewable else 'Privé'
                ),
                'text': ((row['text'] or '')[:140]) if viewable else '',
                'has_media': bool(row['media']) if viewable else False,
                'viewable': viewable,
            })
            cursor = row['forwarded_from_msg_id']
        # Oldest → newest ordering for the UI
        hops.reverse()
    return {"msg_id": msg_id, "hops": hops}





@router.post("/groups")
async def create_group(req: CreateGroupRequest, request: Request):
    user = await get_current_user(request)
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Group title required")
    if len(req.member_ids) < 1:
        raise HTTPException(status_code=400, detail="At least 1 member required")
    
    pool = await get_pool()
    conv_id = f"grp_{uuid.uuid4().hex[:12]}"
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO conversations (conv_id, type, title, description, created_by)
            VALUES ($1, 'group', $2, $3, $4)
        """, conv_id, req.title, req.description, user['user_id'])
        
        # Add creator as admin
        await conn.execute("""
            INSERT INTO conversation_participants (conv_id, user_id, role) VALUES ($1, $2, 'admin')
        """, conv_id, user['user_id'])
        
        # Add members
        added = 0
        for mid in req.member_ids:
            exists = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1 AND is_active = TRUE", mid)
            if exists and mid != user['user_id']:
                await conn.execute("""
                    INSERT INTO conversation_participants (conv_id, user_id, role) VALUES ($1, $2, 'member')
                    ON CONFLICT DO NOTHING
                """, conv_id, mid)
                added += 1
        
        # System message
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        await conn.execute("""
            INSERT INTO messages (msg_id, conv_id, sender_id, text, status)
            VALUES ($1, $2, $3, $4, 'sent')
        """, msg_id, conv_id, user['user_id'], f"Groupe '{req.title}' cree avec {added + 1} membres")
        
        return {"conv_id": conv_id, "title": req.title, "members": added + 1}


@router.post("/groups/{conv_id}/members")
async def add_group_member(conv_id: str, req: AddMemberRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        conv = await conn.fetchrow("SELECT * FROM conversations WHERE conv_id = $1 AND type = 'group'", conv_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Group not found")
        
        participant = await conn.fetchrow(
            "SELECT * FROM conversation_participants WHERE conv_id = $1 AND user_id = $2", conv_id, user['user_id'])
        if not participant:
            raise HTTPException(status_code=403, detail="Not in this group")
        
        target = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1 AND is_active = TRUE", req.user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        
        await conn.execute("""
            INSERT INTO conversation_participants (conv_id, user_id, role) VALUES ($1, $2, 'member')
            ON CONFLICT DO NOTHING
        """, conv_id, req.user_id)
        
        return {"message": "Member added"}


@router.delete("/groups/{conv_id}/members/{member_id}")
async def remove_group_member(conv_id: str, member_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        participant = await conn.fetchrow(
            "SELECT * FROM conversation_participants WHERE conv_id = $1 AND user_id = $2 AND role = 'admin'", conv_id, user['user_id'])
        if not participant:
            raise HTTPException(status_code=403, detail="Admin access required")
        
        await conn.execute("DELETE FROM conversation_participants WHERE conv_id = $1 AND user_id = $2", conv_id, member_id)
        return {"message": "Member removed"}


@router.get("/groups/{conv_id}/members")
async def get_group_members(conv_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        participant = await conn.fetchrow(
            "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2", conv_id, user['user_id'])
        if not participant:
            raise HTTPException(status_code=403, detail="Not in this group")
        
        members = await conn.fetch("""
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_online, u.last_seen, cp.role
            FROM conversation_participants cp JOIN users u ON cp.user_id = u.user_id
            WHERE cp.conv_id = $1 ORDER BY cp.role DESC, u.first_name ASC
        """, conv_id)
        return [
            {**dict(m), 'last_seen': m['last_seen'].isoformat() if m['last_seen'] else None}
            for m in members
        ]



@router.post("/send-money")
async def send_money_in_chat(req: SendMoneyInChatRequest, request: Request):
    """P2P wallet transfer routed through a 1-1 conversation.
    Debits sender, credits recipient, inserts a special 'money' message into the conv,
    emits Socket.IO new_message to the conv room and notify_money to the recipient.
    """
    user = await get_current_user(request)
    if req.to_user_id == user['user_id']:
        raise HTTPException(status_code=400, detail="Impossible d'envoyer à soi-même")
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Montant invalide")
    # iter237m — Wallets are now USD canonical. Min = 0.10 USD (~50 XAF historical equiv).
    if req.amount < Decimal('0.10'):
        raise HTTPException(status_code=400, detail="Montant minimum 0.10 USD")

    amount = Decimal(str(req.amount))
    note = (req.note or '').strip()[:200]

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                "SELECT user_id, first_name, last_name, username, avatar FROM users WHERE user_id = $1 AND is_active = TRUE",
                req.to_user_id)
            if not target:
                raise HTTPException(status_code=404, detail="Destinataire introuvable")

            sender_wallet = await conn.fetchrow(
                "SELECT balance, currency, is_locked FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
            if not sender_wallet:
                raise HTTPException(status_code=404, detail="Wallet introuvable")
            if sender_wallet['is_locked']:
                raise HTTPException(status_code=403, detail="Wallet bloqué")
            if sender_wallet['balance'] < amount:
                raise HTTPException(status_code=400, detail="Solde insuffisant")

            receiver_wallet = await conn.fetchrow(
                "SELECT balance FROM wallets WHERE user_id = $1 FOR UPDATE", req.to_user_id)
            if not receiver_wallet:
                raise HTTPException(status_code=404, detail="Wallet destinataire introuvable")

            now = datetime.now(timezone.utc)
            await conn.execute("UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                               amount, now, user['user_id'])
            await conn.execute("UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                               amount, now, req.to_user_id)

            tx_id = f"cm_{uuid.uuid4().hex[:14]}"
            # iter237m — Force USD currency on chat-money tx (was defaulting to XAF
            # because of the column DEFAULT 'XAF'). Wallets are USD canonical.
            await conn.execute("""
                INSERT INTO transactions (tx_id, from_user_id, to_user_id, type, amount, currency, status, notes)
                VALUES ($1, $2, $3, 'chat_money', $4, 'USD', 'completed', $5)
            """, tx_id, user['user_id'], req.to_user_id, amount, note)

            conv_id = await get_or_create_conversation(conn, user['user_id'], req.to_user_id)

            # iter237m — Display currency must reflect wallet (USD), not the legacy XAF fallback.
            currency = sender_wallet['currency'] or 'USD'
            media_payload = json.dumps({
                "kind": "money",
                "amount": str(amount),
                "currency": currency,
                "note": note,
                "tx_id": tx_id,
            })
            msg_id = f"msg_{uuid.uuid4().hex[:16]}"
            msg_text = f"\U0001F4B8 {amount} {currency}" + (f" — {note}" if note else "")
            await conn.execute("""
                INSERT INTO messages (msg_id, conv_id, sender_id, text, media, status)
                VALUES ($1, $2, $3, $4, $5, 'sent')
            """, msg_id, conv_id, user['user_id'], msg_text, media_payload)
            await conn.execute("UPDATE conversations SET updated_at = $1 WHERE conv_id = $2", now, conv_id)

            new_balance = await conn.fetchval("SELECT balance FROM wallets WHERE user_id = $1", user['user_id'])

    sender_name = (f"{user['first_name']} {user['last_name']}".strip() or user.get('username', ''))
    message_payload = {
        'msg_id': msg_id,
        'conv_id': conv_id,
        'sender_id': user['user_id'],
        'sender_name': sender_name,
        'sender_avatar': user.get('avatar', '') or '',
        'text': msg_text,
        'media': media_payload,
        'status': 'sent',
        'created_at': now.isoformat(),
    }

    # Broadcast Socket.IO events (outside transaction, best-effort)
    try:
        await push_to_user(user['user_id'], 'new_message', message_payload)
        await push_to_user(req.to_user_id, 'new_message', message_payload)
        await notify_money(
            recipient_id=req.to_user_id,
            sender={
                'user_id': user['user_id'],
                'name': sender_name,
                'avatar': user.get('avatar', '') or '',
            },
            amount=str(amount),
            note=note,
        )
    except Exception as e:
        logger.warning(f"chat-money realtime push failed: {e}")

    return {
        'tx_id': tx_id,
        'conv_id': conv_id,
        'msg_id': msg_id,
        'amount': str(amount),
        'currency': currency,
        'new_balance': str(new_balance),
        'message': message_payload,
    }


@router.post("/{msg_id}/react")
async def react_to_message(msg_id: str, req: ReactRequest, request: Request):
    """Toggle an emoji reaction on a message. If the user already reacted with
    the same emoji, it is removed. Otherwise it is added. Returns the aggregated
    reaction counts for the message and broadcasts via Socket.IO.
    """
    user = await get_current_user(request)
    emoji = (req.emoji or '').strip()
    if emoji not in ALLOWED_REACTIONS:
        raise HTTPException(status_code=400, detail="Emoji non autorisé")

    pool = await get_pool()
    async with pool.acquire() as conn:
        msg = await conn.fetchrow("SELECT msg_id, conv_id, sender_id FROM messages WHERE msg_id = $1", msg_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Message introuvable")
        participant = await conn.fetchrow(
            "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2",
            msg['conv_id'], user['user_id'])
        if not participant:
            raise HTTPException(status_code=403, detail="Not in this conversation")

        # Toggle: remove if exists, else insert
        existing = await conn.fetchrow(
            "SELECT id FROM message_reactions WHERE msg_id = $1 AND user_id = $2 AND emoji = $3",
            msg_id, user['user_id'], emoji)
        if existing:
            await conn.execute("DELETE FROM message_reactions WHERE id = $1", existing['id'])
            action = 'removed'
        else:
            await conn.execute("""
                INSERT INTO message_reactions (msg_id, user_id, emoji) VALUES ($1, $2, $3)
                ON CONFLICT (msg_id, user_id, emoji) DO NOTHING
            """, msg_id, user['user_id'], emoji)
            action = 'added'

        # Aggregated counts for this message
        reactions_map = await _reactions_for_messages(conn, [msg_id], user['user_id'])
        reactions = reactions_map.get(msg_id, [])

    # Realtime broadcast to conv room (best-effort; both parties see updated chips)
    try:
        from routes.realtime import _sio
        if _sio:
            await _sio.emit('message_reaction', {
                'msg_id': msg_id,
                'conv_id': msg['conv_id'],
                'reactions': reactions,
                'actor_id': user['user_id'],
                'action': action,
                'emoji': emoji,
            }, room=msg['conv_id'])
    except Exception as e:
        logger.warning(f"message_reaction emit failed: {e}")

    return {'msg_id': msg_id, 'action': action, 'emoji': emoji, 'reactions': reactions}


@router.get("/allowed-reactions")
async def get_allowed_reactions(request: Request):
    """Expose the whitelist so frontend stays in sync without hardcoding."""
    await get_current_user(request)
    return {'emojis': list(ALLOWED_REACTIONS)}



# ============== VOICE MESSAGES + WHISPER TRANSCRIPTION ==============
_UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
_UPLOAD_DIR.mkdir(exist_ok=True)

_VOICE_ALLOWED_EXTS = {'.webm', '.mp3', '.mp4', '.m4a', '.wav', '.ogg', '.mpeg', '.mpga'}
_VOICE_MAX_SIZE = 10 * 1024 * 1024  # 10MB — Whisper supports up to 25MB but keep UX snappy
_VOICE_DEFAULT_LANGUAGE = "fr"  # JAPAP est en français


async def _transcribe_audio(filepath: Path, language: str = _VOICE_DEFAULT_LANGUAGE) -> str:
    """Call OpenAI Whisper via emergentintegrations. Returns text or '' on failure."""
    try:
        from emergentintegrations.llm.openai import OpenAISpeechToText
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        if not api_key:
            logger.warning("EMERGENT_LLM_KEY not set — skipping voice transcription")
            return ""
        stt = OpenAISpeechToText(api_key=api_key)
        with open(filepath, "rb") as audio_file:
            response = await stt.transcribe(
                file=audio_file,
                model="whisper-1",
                response_format="text",
                language=language,
            )
        # response may be a string (response_format=text) or object with .text
        if isinstance(response, str):
            return response.strip()
        return (getattr(response, "text", "") or "").strip()
    except Exception as e:
        logger.warning(f"Whisper transcription failed: {e}")
        return ""


@router.post("/voice")
async def send_voice_message(
    request: Request,
    file: UploadFile = File(...),
    conv_id: Optional[str] = Form(None),
    to_user_id: Optional[str] = Form(None),
    duration: int = Form(0),
):
    """Send a voice message in a conversation. Uploads audio, triggers Whisper
    transcription, inserts a message with media=JSON (kind='voice'), broadcasts
    via Socket.IO. Either conv_id OR to_user_id must be provided.
    """
    user = await get_current_user(request)
    ext = Path(file.filename or 'voice.webm').suffix.lower() or '.webm'
    if ext not in _VOICE_ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Format audio non supporté: {ext}")

    content = await file.read()
    if len(content) > _VOICE_MAX_SIZE:
        raise HTTPException(status_code=400, detail="Fichier audio trop volumineux (max 10MB)")
    if len(content) < 256:
        raise HTTPException(status_code=400, detail="Fichier audio trop court")

    file_id = uuid.uuid4().hex[:16]
    filename = f"voice_{file_id}{ext}"
    filepath = _UPLOAD_DIR / filename
    with open(filepath, 'wb') as f:
        f.write(content)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Resolve conversation
        if not conv_id:
            if not to_user_id:
                raise HTTPException(status_code=400, detail="conv_id ou to_user_id requis")
            target = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1 AND is_active = TRUE", to_user_id)
            if not target:
                raise HTTPException(status_code=404, detail="Destinataire introuvable")
            conv_id = await get_or_create_conversation(conn, user['user_id'], to_user_id)
        else:
            participant = await conn.fetchrow(
                "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2",
                conv_id, user['user_id'])
            if not participant:
                raise HTTPException(status_code=403, detail="Not in this conversation")

    # Transcribe (best-effort, outside DB transaction so we don't hold a connection)
    transcription = await _transcribe_audio(filepath, language=_VOICE_DEFAULT_LANGUAGE)

    # Insert message
    voice_url = f"/api/upload/files/{filename}"
    media_payload = json.dumps({
        "kind": "voice",
        "url": voice_url,
        "duration": max(0, int(duration)),
        "transcription": transcription,
        "size": len(content),
    })
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    msg_text = f"\U0001F3A4 Message vocal ({max(1, int(duration))}s)"

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO messages (msg_id, conv_id, sender_id, text, media, status)
                VALUES ($1, $2, $3, $4, $5, 'sent')
            """, msg_id, conv_id, user['user_id'], msg_text, media_payload)
            await conn.execute("UPDATE conversations SET updated_at = $1 WHERE conv_id = $2",
                               datetime.now(timezone.utc), conv_id)

    sender_name = (f"{user['first_name']} {user['last_name']}".strip() or user.get('username', ''))
    message_payload = {
        'msg_id': msg_id,
        'conv_id': conv_id,
        'sender_id': user['user_id'],
        'sender_name': sender_name,
        'sender_avatar': user.get('avatar', '') or '',
        'text': msg_text,
        'media': media_payload,
        'status': 'sent',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'reactions': [],
    }

    # Broadcast Socket.IO (best-effort)
    try:
        await push_to_user(user['user_id'], 'new_message', message_payload)
        await push_to_user(to_user_id or '', 'new_message', message_payload) if to_user_id else None
        # For groups or when we only have conv_id, broadcast to conv room:
        from routes.realtime import _sio
        if _sio:
            await _sio.emit('new_message', message_payload, room=conv_id)
    except Exception as e:
        logger.warning(f"voice broadcast failed: {e}")

    return {
        'msg_id': msg_id,
        'conv_id': conv_id,
        'url': voice_url,
        'duration': max(0, int(duration)),
        'transcription': transcription,
        'message': message_payload,
    }



# ============== LIVE TRANSLATION (Claude via Emergent key) ==============
from constants import SUPPORTED_LANGS as _TRANSLATE_SUPPORTED_SET, LANG_NAMES as _LANG_NAMES
_TRANSLATE_SUPPORTED = {k: _LANG_NAMES[k] for k in _TRANSLATE_SUPPORTED_SET}


class TranslateRequest(BaseModel):
    target_lang: str = "fr"


def _extract_translatable(msg_row, media_payload: Optional[dict]) -> str:
    """Prefer voice transcription for voice messages; else use text.
    Skip money messages — amounts don't need translation.
    """
    if media_payload and media_payload.get('kind') == 'money':
        return ''
    if media_payload and media_payload.get('kind') == 'voice':
        return (media_payload.get('transcription') or '').strip()
    return (msg_row['text'] or '').strip()


async def _call_translate_llm(text: str, target_lang_code: str, target_lang_name: str) -> tuple[str, str]:
    """Returns (translated_text, detected_source_lang_code). Empty on failure."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        logger.warning("EMERGENT_LLM_KEY not set — cannot translate")
        return "", ""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        system_prompt = (
            "You are a professional pan-African translator for the JAPAP Messenger app. "
            f"Translate the user's text into {target_lang_name} ({target_lang_code}). "
            "Preserve emojis, punctuation, names, currency amounts, and slang register. "
            "If the text is already in the target language, return it unchanged. "
            "Output ONLY a valid JSON object on a single line: "
            '{"detected":"<ISO-639-1 code>","text":"<translated text>"} '
            "— no markdown, no code fences, no commentary."
        )
        chat = LlmChat(
            api_key=api_key,
            session_id=f"japap-translate-{uuid.uuid4().hex[:8]}",
            system_message=system_prompt,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        response = await chat.send_message(UserMessage(text=text))
        raw = (response or "").strip()
        # Robust JSON parse — tolerate stray markdown fences just in case
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        try:
            data = json.loads(raw)
            return (data.get("text") or "").strip(), (data.get("detected") or "").strip()
        except Exception:
            # If the model returned plain text, use it as the translation
            return raw, ""
    except Exception as e:
        logger.warning(f"Translate LLM failed: {e}")
        return "", ""


@router.post("/{msg_id}/translate")
async def translate_message(msg_id: str, req: TranslateRequest, request: Request):
    """Translate a message (text or voice transcription) using Claude Sonnet
    via the Emergent LLM key. Cached per (msg_id, target_lang) for free re-fetch.
    """
    user = await get_current_user(request)
    target = (req.target_lang or 'fr').lower().strip()
    if target not in _TRANSLATE_SUPPORTED:
        raise HTTPException(status_code=400, detail=f"Langue non supportée: {target}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        msg = await conn.fetchrow("""
            SELECT msg_id, conv_id, sender_id, text, media FROM messages WHERE msg_id = $1
        """, msg_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Message introuvable")
        participant = await conn.fetchrow(
            "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2",
            msg['conv_id'], user['user_id'])
        if not participant:
            raise HTTPException(status_code=403, detail="Not in this conversation")

        media_payload = None
        if msg['media'] and isinstance(msg['media'], str) and msg['media'].startswith('{'):
            try: media_payload = json.loads(msg['media'])
            except Exception: media_payload = None

        source_text = _extract_translatable(msg, media_payload)
        if not source_text:
            raise HTTPException(status_code=400, detail="Rien à traduire pour ce message")

        # Cache lookup
        cached = await conn.fetchrow("""
            SELECT translated_text, detected_lang, source_text FROM message_translations
            WHERE msg_id = $1 AND target_lang = $2
        """, msg_id, target)
        if cached:
            return {
                'msg_id': msg_id,
                'target_lang': target,
                'translated_text': cached['translated_text'],
                'detected_lang': cached['detected_lang'] or '',
                'source_text': cached['source_text'],
                'cached': True,
            }

    # Call LLM outside DB connection (can be slow)
    translated, detected = await _call_translate_llm(
        text=source_text,
        target_lang_code=target,
        target_lang_name=_TRANSLATE_SUPPORTED[target],
    )
    if not translated:
        raise HTTPException(status_code=502, detail="Service de traduction indisponible")

    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO message_translations (msg_id, target_lang, source_text, translated_text, detected_lang)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (msg_id, target_lang) DO UPDATE SET
                    translated_text = EXCLUDED.translated_text,
                    detected_lang = EXCLUDED.detected_lang
            """, msg_id, target, source_text, translated, detected)
        except Exception as e:
            logger.warning(f"translation cache write failed: {e}")

    return {
        'msg_id': msg_id,
        'target_lang': target,
        'translated_text': translated,
        'detected_lang': detected,
        'source_text': source_text,
        'cached': False,
    }


@router.get("/supported-languages")
async def get_supported_translation_languages(request: Request):
    await get_current_user(request)
    return {'languages': [{'code': k, 'name': v} for k, v in _TRANSLATE_SUPPORTED.items()]}



# ============== VOICE TL;DR SUMMARY (long voice messages) ==============
class SummarizeRequest(BaseModel):
    target_lang: str = "en"


async def _call_summary_llm(transcription: str, target_lang_code: str, target_lang_name: str) -> str:
    """2-line summary of a long voice transcription via Claude Sonnet 4.5."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return ""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        system_prompt = (
            f"You are a concise summarizer for the JAPAP Messenger app. "
            f"Summarize the following voice-message transcription in AT MOST 2 short sentences "
            f"(~200 characters total) in {target_lang_name} ({target_lang_code}). "
            "Keep key facts: who/what/when/amounts. No greetings, no filler. "
            "Output ONLY the summary text, no prefix, no quotes, no markdown."
        )
        chat = LlmChat(
            api_key=api_key,
            session_id=f"japap-summary-{uuid.uuid4().hex[:8]}",
            system_message=system_prompt,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        response = await chat.send_message(UserMessage(text=transcription))
        return (response or "").strip().strip('"').strip()
    except Exception as e:
        logger.warning(f"Summary LLM failed: {e}")
        return ""


@router.post("/{msg_id}/summarize")
async def summarize_voice(msg_id: str, req: SummarizeRequest, request: Request):
    """Generate/fetch a TL;DR summary of a voice message's transcription.
    Only available for voice messages whose duration >= 30s OR transcription > 400 chars.
    Cached per (msg_id, target_lang) by reusing the message_translations table with a
    '_sum' suffix on target_lang (e.g., 'en_sum') — simple, no extra schema.
    """
    user = await get_current_user(request)
    target = (req.target_lang or 'en').lower().strip()
    if target not in _TRANSLATE_SUPPORTED_SET:
        raise HTTPException(status_code=400, detail=f"Langue non supportée: {target}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        msg = await conn.fetchrow("SELECT msg_id, conv_id, media FROM messages WHERE msg_id = $1", msg_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Message introuvable")
        participant = await conn.fetchrow(
            "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2",
            msg['conv_id'], user['user_id'])
        if not participant:
            raise HTTPException(status_code=403, detail="Not in this conversation")

        media_payload = None
        if msg['media'] and isinstance(msg['media'], str) and msg['media'].startswith('{'):
            try: media_payload = json.loads(msg['media'])
            except Exception: media_payload = None
        if not media_payload or media_payload.get('kind') != 'voice':
            raise HTTPException(status_code=400, detail="Résumé disponible uniquement pour les messages vocaux")

        transcription = (media_payload.get('transcription') or '').strip()
        duration = int(media_payload.get('duration') or 0)
        if not transcription:
            raise HTTPException(status_code=400, detail="Aucune transcription à résumer")
        if duration < 30 and len(transcription) < 400:
            raise HTTPException(status_code=400, detail="Message trop court pour un résumé (>= 30s requis)")

        cache_key = f"{target}_sum"
        cached = await conn.fetchrow("""
            SELECT translated_text FROM message_translations
            WHERE msg_id = $1 AND target_lang = $2
        """, msg_id, cache_key)
        if cached:
            return {
                'msg_id': msg_id,
                'target_lang': target,
                'summary': cached['translated_text'],
                'duration': duration,
                'cached': True,
            }

    # Call LLM outside DB block
    summary = await _call_summary_llm(transcription, target, _TRANSLATE_SUPPORTED[target])
    if not summary:
        raise HTTPException(status_code=502, detail="Service de résumé indisponible")

    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO message_translations (msg_id, target_lang, source_text, translated_text)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (msg_id, target_lang) DO UPDATE SET translated_text = EXCLUDED.translated_text
            """, msg_id, cache_key, transcription, summary)
        except Exception as e:
            logger.warning(f"summary cache write failed: {e}")

    return {
        'msg_id': msg_id,
        'target_lang': target,
        'summary': summary,
        'duration': duration,
        'cached': False,
    }

