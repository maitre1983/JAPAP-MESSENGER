"""
JAPAP Messenger — Call routes
=============================
Backward-compatible with the legacy /initiate + /end + /history endpoints
used by the current WebRTC signaling (pre-LiveKit). New Sprint B/C/D
endpoints add :
    POST /api/calls/session           — create a LiveKit-backed session
    POST /api/calls/token             — mint a LiveKit JWT for the frontend
    POST /api/calls/{id}/join         — mark a user as joined (participants)
    POST /api/calls/{id}/leave        — mark a user as left
    POST /api/calls/{id}/record/start — start a composite egress to R2
    POST /api/calls/{id}/record/stop  — stop egress, enqueue AI pipeline
    GET  /api/calls/{id}/summary      — fetch transcript + summary
    GET  /api/calls/test-livekit      — admin only: verify LiveKit creds
    GET  /api/calls/test-r2           — admin only: verify R2 creds

All LiveKit calls go through services.livekit_service (abstracted). Until
credentials are set, token/record endpoints return 503 with a friendly FR
message so the UI can display "Appels non disponibles".
"""
import uuid
import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calls", tags=["calls"])


# ─── Legacy 1-1 WebRTC signaling (kept for backward compat) ────────────────
class InitiateCallRequest(BaseModel):
    callee_id: str
    type: str = "audio"  # audio | video


class EndCallRequest(BaseModel):
    call_id: str
    duration: int = 0
    status: str = "ended"  # ended | missed | rejected | failed


# ═══════════════════════════════════════════════════════════════════════
# iter193b — Client-side call telemetry (black box)
# ═══════════════════════════════════════════════════════════════════════
# Frontend posts one row per step of the call flow so we can reconstruct
# exactly what broke on a user's device. Schema is intentionally narrow:
# no media, no PII beyond what's needed to identify the call, no IP.
#
# Expected actions (CEO spec):
#   call_button_clicked · socket_not_connected · permission_prompt_opened
#   permission_granted · permission_denied · token_requested
#   livekit_connecting · livekit_connected · livekit_failed
#   call_accepted · call_ended
#
# Retention: logs older than 14 days are auto-pruned by cleanup cron.

class ClientCallLog(BaseModel):
    action: str
    call_id: Optional[str] = None
    room_id: Optional[str] = None
    error_name: Optional[str] = None
    error_message: Optional[str] = None
    # Free-form context (kept small — 2 KB max after JSON encoding)
    meta: Optional[dict] = None


async def _ensure_call_logs_schema(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS call_client_logs (
                id              bigserial PRIMARY KEY,
                user_id         varchar NOT NULL,
                action          varchar NOT NULL,
                call_id         varchar,
                room_id         varchar,
                device          varchar,      -- user agent + PWA flag
                browser         varchar,      -- parsed short form
                error_name      varchar,
                error_message   text,
                meta            jsonb,
                created_at      timestamptz DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ccl_created
                ON call_client_logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ccl_user_created
                ON call_client_logs(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ccl_call
                ON call_client_logs(call_id);
            CREATE INDEX IF NOT EXISTS idx_ccl_action
                ON call_client_logs(action, created_at DESC);
        """)


_CALL_LOG_ALLOWED = {
    "call_button_clicked",
    "socket_not_connected",
    "permission_prompt_opened",
    "permission_granted",
    "permission_denied",
    "token_requested",
    "livekit_connecting",
    "livekit_connected",
    "livekit_failed",
    "call_invite_sent",
    "call_incoming_received",
    "call_accepted",
    "call_rejected",
    "call_missed",
    "call_ended",
    "call_error",
    "ring_timeout",
    "remote_track_subscribed",
    "media_device_error",
}


def _short_browser(ua: str) -> str:
    """Best-effort user-agent parser. Keeps it short & readable."""
    if not ua:
        return ""
    s = ua.lower()
    is_pwa = "(wv)" in s or "; wv)" in s  # not definitive but hints
    if "iphone" in s or "ipad" in s:
        base = "iOS Safari" if "safari" in s and "chrome" not in s else "iOS Chrome" if "crios" in s else "iOS"
    elif "android" in s:
        base = "Android Chrome" if "chrome" in s and "edg" not in s else "Android"
    elif "edg/" in s:
        base = "Edge"
    elif "chrome/" in s:
        base = "Chrome"
    elif "firefox/" in s:
        base = "Firefox"
    elif "safari/" in s:
        base = "Safari"
    else:
        base = "Unknown"
    return base + (" PWA" if is_pwa else "")


@router.post("/logs/client", status_code=202)
async def call_log_client(payload: ClientCallLog, request: Request):
    """Client-side call telemetry sink. Best-effort, never blocks the UI."""
    try:
        user = await get_current_user(request)
    except HTTPException:
        # Still accept logs from barely-auth'd users — identify via "anon".
        user = {"user_id": "anon"}
    action = (payload.action or "").strip()
    if action not in _CALL_LOG_ALLOWED:
        # Don't reject hard — log an "unknown" row so we still see it in
        # the black box. Helps us spot typos or new action names quickly.
        action = f"unknown:{action[:40]}"
    ua = (request.headers.get("user-agent") or "")[:400]
    pool = await get_pool()
    await _ensure_call_logs_schema(pool)
    try:
        # Safely encode meta: if it serialises to > 2 KB, replace with a
        # truncation marker so we never ship malformed JSON to Postgres.
        try:
            meta_str = json.dumps(payload.meta or {}, default=str)
        except Exception:
            meta_str = "{}"
        if len(meta_str) > 2048:
            meta_str = json.dumps({
                "truncated": True,
                "original_size": len(meta_str),
            })
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO call_client_logs
                    (user_id, action, call_id, room_id, device, browser,
                     error_name, error_message, meta)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
            """,
                str(user.get("user_id") or "anon"),
                action,
                (payload.call_id or "")[:80] or None,
                (payload.room_id or "")[:120] or None,
                ua,
                _short_browser(ua),
                (payload.error_name or "")[:80] or None,
                (payload.error_message or "")[:500] or None,
                meta_str,
            )
    except Exception as e:
        # Never raise — telemetry must be fire-and-forget.
        logger.warning(f"[call-log] persist failed: {e}")
    return {"ok": True}


@router.get("/logs/admin")
async def call_logs_admin(
    request: Request,
    limit: int = 100,
    call_id: str = "",
    user_id: str = "",
    action: str = "",
    since_min: int = 0,
):
    """Admin-only viewer. Filter by call_id / user_id / action / recency."""
    user = await get_current_user(request)
    if not (user.get("is_admin") or user.get("role") in ("admin", "superadmin")):
        raise HTTPException(status_code=403, detail="Admin only")
    limit = max(1, min(500, int(limit or 100)))
    pool = await get_pool()
    await _ensure_call_logs_schema(pool)
    where, params = [], []
    if call_id:
        params.append(call_id)
        where.append(f"call_id = ${len(params)}")
    if user_id:
        params.append(user_id)
        where.append(f"user_id = ${len(params)}")
    if action:
        params.append(action)
        where.append(f"action = ${len(params)}")
    if since_min and since_min > 0:
        params.append(int(since_min))
        where.append(f"created_at > NOW() - (${len(params)} || ' minutes')::interval")
    sql = "SELECT * FROM call_client_logs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    params.append(limit)
    sql += f" ORDER BY id DESC LIMIT ${len(params)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        if isinstance(d.get("meta"), str):
            try:
                d["meta"] = json.loads(d["meta"])
            except Exception:
                pass
        out.append(d)
    return {"logs": out, "count": len(out)}




@router.post("/initiate")
async def initiate_call(req: InitiateCallRequest, request: Request):
    """Create a call record before signaling (stored even if not connected)."""
    user = await get_current_user(request)
    if req.callee_id == user['user_id']:
        raise HTTPException(status_code=400, detail="Impossible de s'appeler soi-même")
    if req.type not in ('audio', 'video'):
        raise HTTPException(status_code=400, detail="Type d'appel invalide")
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        callee = await conn.fetchrow("SELECT user_id, first_name, last_name, avatar, is_online FROM users WHERE user_id = $1", req.callee_id)
        if not callee:
            raise HTTPException(status_code=404, detail="Destinataire introuvable")
        
        call_id = f"call_{uuid.uuid4().hex[:16]}"
        await conn.execute("""
            INSERT INTO calls (call_id, caller_id, callee_id, type, status, started_at)
            VALUES ($1, $2, $3, $4, 'ringing', $5)
        """, call_id, user['user_id'], req.callee_id, req.type, datetime.now(timezone.utc))
        
        return {
            "call_id": call_id,
            "type": req.type,
            "callee": {
                "user_id": callee['user_id'],
                "name": f"{callee['first_name']} {callee['last_name']}".strip(),
                "avatar": callee['avatar'] or '',
                "is_online": callee['is_online'],
            },
        }


@router.post("/end")
async def end_call(req: EndCallRequest, request: Request):
    """Mark a call as ended/missed/rejected with duration."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        call = await conn.fetchrow("SELECT * FROM calls WHERE call_id = $1", req.call_id)
        if not call:
            raise HTTPException(status_code=404, detail="Appel introuvable")
        if call['caller_id'] != user['user_id'] and call['callee_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Non autorisé")
        
        status = req.status if req.status in ('ended', 'missed', 'rejected', 'failed') else 'ended'
        await conn.execute("""
            UPDATE calls SET status = $1, ended_at = $2, duration = $3 WHERE call_id = $4
        """, status, datetime.now(timezone.utc), max(0, req.duration), req.call_id)
        
        # Create a missed-call notification if callee never picked up
        if status in ('missed', 'rejected') and user['user_id'] == call['caller_id']:
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            call_type_fr = 'vidéo' if call['type'] == 'video' else 'audio'
            caller_name = f"{user['first_name']} {user['last_name']}".strip() or user['username']
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'call_missed', 'Appel manqué', $3)
            """, notif_id, call['callee_id'], f"Appel {call_type_fr} manqué de {caller_name}")
        
        return {"message": "Appel terminé", "status": status, "duration": req.duration}


@router.get("/history")
async def call_history(request: Request, limit: int = 50):
    """Unified call history — legacy 1-1 (calls table) + LiveKit sessions
    (call_sessions, group + p2p) + AI summary availability.

    Returned items are deduped : when a `call_sessions` row is linked to a
    legacy call via `call_id`, the session row wins (richer data). Rows are
    sorted by `started_at DESC` and capped by `limit`.

    Item shape :
        {
          "id": "<session_id or call_id>",
          "session_id": Optional[str],   # LiveKit session when present
          "call_id":    Optional[str],   # legacy id when present
          "kind": "p2p" | "group",
          "type": "audio" | "video",
          "direction": "outgoing" | "incoming" | null (group),
          "status": "ended" | "missed" | "rejected" | "failed" | "live" | "ringing",
          "duration": int (seconds),
          "started_at": iso,
          "ended_at":   iso | null,
          "other": { user_id, name, avatar } | null        # p2p only
          "participants": [ { user_id, name, avatar } ]    # group only
          "conv_id": Optional[str],
          "has_summary": bool,
          "summary_id":  Optional[str],
          "summary_status": Optional[str],
          "has_recording": bool,
        }
    """
    user = await get_current_user(request)
    uid = user['user_id']
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1) LiveKit sessions where the user is host OR participant
        session_rows = await conn.fetch("""
            SELECT DISTINCT cs.session_id, cs.call_id, cs.conv_id, cs.mode, cs.kind,
                   cs.host_user_id, cs.status, cs.started_at, cs.ended_at,
                   cs.duration_sec,
                   s.summary_id, s.status AS summary_status,
                   (SELECT COUNT(*) FROM call_recordings cr WHERE cr.session_id = cs.session_id) AS rec_count
            FROM call_sessions cs
            LEFT JOIN call_participants cp
              ON cp.session_id = cs.session_id AND cp.user_id = $1
            LEFT JOIN LATERAL (
              SELECT summary_id, status FROM call_summaries
              WHERE session_id = cs.session_id
              ORDER BY created_at DESC LIMIT 1
            ) s ON TRUE
            WHERE cs.host_user_id = $1 OR cp.user_id IS NOT NULL
            ORDER BY cs.started_at DESC
            LIMIT $2
        """, uid, limit)

        # 2) Legacy 1-1 calls — but skip any that are already linked to a session
        session_call_ids = {r['call_id'] for r in session_rows if r['call_id']}
        legacy_rows = await conn.fetch("""
            SELECT c.*,
                cu.first_name AS caller_first, cu.last_name AS caller_last, cu.avatar AS caller_avatar,
                cu.username  AS caller_username,
                cee.first_name AS callee_first, cee.last_name AS callee_last, cee.avatar AS callee_avatar,
                cee.username AS callee_username
            FROM calls c
            JOIN users cu  ON cu.user_id  = c.caller_id
            JOIN users cee ON cee.user_id = c.callee_id
            WHERE c.caller_id = $1 OR c.callee_id = $1
            ORDER BY c.started_at DESC
            LIMIT $2
        """, uid, limit)

        # 3) Collect group-call participants in bulk (single query, no N+1)
        group_session_ids = [r['session_id'] for r in session_rows if r['kind'] == 'group']
        participants_map: dict[str, list] = {}
        if group_session_ids:
            p_rows = await conn.fetch("""
                SELECT cp.session_id, u.user_id, u.first_name, u.last_name,
                       u.username, u.avatar
                FROM call_participants cp
                JOIN users u ON u.user_id = cp.user_id
                WHERE cp.session_id = ANY($1::text[])
            """, group_session_ids)
            for p in p_rows:
                participants_map.setdefault(p['session_id'], []).append({
                    'user_id': p['user_id'],
                    'name': f"{p['first_name'] or ''} {p['last_name'] or ''}".strip() or p['username'],
                    'avatar': p['avatar'] or '',
                })

        # 4) For p2p LiveKit sessions, fetch the "other" participant once
        p2p_other_map: dict[str, dict] = {}
        p2p_session_ids = [r['session_id'] for r in session_rows if r['kind'] == 'p2p']
        if p2p_session_ids:
            o_rows = await conn.fetch("""
                SELECT cp.session_id, u.user_id, u.first_name, u.last_name,
                       u.username, u.avatar
                FROM call_participants cp
                JOIN users u ON u.user_id = cp.user_id
                WHERE cp.session_id = ANY($1::text[]) AND cp.user_id <> $2
                ORDER BY cp.joined_at ASC
            """, p2p_session_ids, uid)
            for o in o_rows:
                # first match wins (earliest joiner other than me)
                p2p_other_map.setdefault(o['session_id'], {
                    'user_id': o['user_id'],
                    'name': f"{o['first_name'] or ''} {o['last_name'] or ''}".strip() or o['username'],
                    'avatar': o['avatar'] or '',
                })

    # Build unified list
    items: list[dict] = []

    for r in session_rows:
        is_group = r['kind'] == 'group'
        # Normalize status into the 5 canonical buckets
        raw = (r['status'] or 'ended').lower()
        if raw in ('live', 'ringing'):
            norm_status = raw
        elif raw in ('failed',):
            norm_status = 'failed'
        else:
            norm_status = 'ended'

        items.append({
            'id': r['session_id'],
            'session_id': r['session_id'],
            'call_id': r['call_id'],
            'kind': r['kind'],
            'type': r['mode'],
            'direction': (None if is_group
                          else ('outgoing' if r['host_user_id'] == uid else 'incoming')),
            'status': norm_status,
            'duration': int(r['duration_sec'] or 0),
            'started_at': r['started_at'].isoformat() if r['started_at'] else None,
            'ended_at': r['ended_at'].isoformat() if r['ended_at'] else None,
            'conv_id': r['conv_id'],
            'other': p2p_other_map.get(r['session_id']) if not is_group else None,
            'participants': participants_map.get(r['session_id'], []) if is_group else [],
            'has_summary': bool(r['summary_id']) and r['summary_status'] == 'ready',
            'summary_id': r['summary_id'],
            'summary_status': r['summary_status'],
            'has_recording': (r['rec_count'] or 0) > 0,
        })

    for r in legacy_rows:
        if r['call_id'] in session_call_ids:
            continue  # already covered by the session row
        is_outgoing = r['caller_id'] == uid
        other = {
            'user_id': r['callee_id'] if is_outgoing else r['caller_id'],
            'name': (f"{r['callee_first']} {r['callee_last']}".strip()
                     if is_outgoing else f"{r['caller_first']} {r['caller_last']}".strip())
                    or (r['callee_username'] if is_outgoing else r['caller_username'])
                    or 'Inconnu',
            'avatar': (r['callee_avatar'] if is_outgoing else r['caller_avatar']) or '',
        }
        items.append({
            'id': r['call_id'],
            'session_id': None,
            'call_id': r['call_id'],
            'kind': 'p2p',
            'type': r['type'],
            'direction': 'outgoing' if is_outgoing else 'incoming',
            'status': r['status'] if r['status'] in ('ended', 'missed', 'rejected', 'failed') else 'ended',
            'duration': r['duration'] or 0,
            'started_at': r['started_at'].isoformat() if r['started_at'] else None,
            'ended_at': r['ended_at'].isoformat() if r['ended_at'] else None,
            'conv_id': None,
            'other': other,
            'participants': [],
            'has_summary': False,
            'summary_id': None,
            'summary_status': None,
            'has_recording': False,
        })

    # Sort unified list by started_at desc, cap by limit
    items.sort(key=lambda x: x['started_at'] or '', reverse=True)
    return items[:limit]


# ═══════════════════════════════════════════════════════════════════════════
# Sprint B/C/D — LiveKit-backed sessions (audio/video/group/recording)
# ═══════════════════════════════════════════════════════════════════════════

class SessionCreateRequest(BaseModel):
    mode: str = "audio"            # audio | video
    kind: str = "p2p"              # p2p | group
    callee_id: Optional[str] = None   # required when kind == p2p
    conv_id: Optional[str] = None     # required when kind == group (group chat id)
    max_participants: int = 12


class TokenRequest(BaseModel):
    session_id: str


def _handle_lk_errors(fn_name: str):
    """Uniform error mapper for LiveKit + R2 routes."""
    def wrap(exc: Exception) -> HTTPException:
        from services.livekit_service import LiveKitConfigError, LiveKitAPIError
        from services.r2_storage_service import R2ConfigError
        if isinstance(exc, LiveKitConfigError):
            logger.info(f"{fn_name}: LiveKit not configured yet")
            return HTTPException(
                status_code=503,
                detail="Appels non disponibles — le serveur LiveKit n'est pas encore configuré.",
            )
        if isinstance(exc, R2ConfigError):
            logger.info(f"{fn_name}: R2 not configured yet")
            return HTTPException(
                status_code=503,
                detail="Enregistrement non disponible — stockage Cloudflare R2 non configuré.",
            )
        if isinstance(exc, LiveKitAPIError):
            return HTTPException(status_code=502, detail=f"Erreur LiveKit : {exc}")
        logger.exception(f"{fn_name} crashed")
        return HTTPException(status_code=500, detail=f"Erreur interne : {exc}")
    return wrap


@router.post("/session")
async def create_session(req: SessionCreateRequest, request: Request):
    """Create a LiveKit-backed session row + pre-create the room.

    Returns the session_id and the LiveKit room name. The frontend then
    calls POST /token to mint a JWT and join with the LiveKit JS SDK.
    """
    user = await get_current_user(request)
    from services.livekit_service import create_room
    if req.mode not in ("audio", "video"):
        raise HTTPException(status_code=400, detail="mode must be audio|video")
    if req.kind not in ("p2p", "group"):
        raise HTTPException(status_code=400, detail="kind must be p2p|group")
    if req.kind == "p2p" and not req.callee_id:
        raise HTTPException(status_code=400, detail="callee_id requis pour un appel p2p")
    if req.kind == "group" and not req.conv_id:
        raise HTTPException(status_code=400, detail="conv_id requis pour un appel groupe")

    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    room_name = f"japap_{session_id}"
    max_p = 2 if req.kind == "p2p" else max(2, min(12, req.max_participants))
    pool = await get_pool()
    err_map = _handle_lk_errors("create_session")
    try:
        await create_room(room_name, max_participants=max_p, empty_timeout=180)
    except Exception as e:
        raise err_map(e)
    async with pool.acquire() as conn:
        # For group calls, verify the caller is a member of the conv.
        if req.kind == "group":
            ok = await conn.fetchrow(
                "SELECT 1 FROM conversation_participants WHERE conv_id=$1 AND user_id=$2",
                req.conv_id, user['user_id'],
            )
            if not ok:
                raise HTTPException(status_code=403, detail="Vous n'êtes pas membre de cette conversation.")
        await conn.execute("""
            INSERT INTO call_sessions
                (session_id, conv_id, room_name, mode, kind,
                 host_user_id, status, max_participants, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, 'ringing', $7, $8)
        """, session_id, req.conv_id, room_name, req.mode, req.kind,
            user['user_id'], max_p,
            json.dumps({"callee_id": req.callee_id} if req.callee_id else {}),
        )
        await conn.execute("""
            INSERT INTO call_participants (session_id, user_id, role)
            VALUES ($1, $2, 'host') ON CONFLICT DO NOTHING
        """, session_id, user['user_id'])
    return {
        "session_id": session_id,
        "room_name": room_name,
        "mode": req.mode,
        "kind": req.kind,
        "max_participants": max_p,
    }


@router.post("/token")
async def mint_token(req: TokenRequest, request: Request):
    """Mint a LiveKit JWT for the caller to join the session's room.
    403 if the user isn't a participant of a group call, or the peer of a p2p."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sess = await conn.fetchrow(
            "SELECT * FROM call_sessions WHERE session_id = $1", req.session_id,
        )
        if not sess:
            raise HTTPException(status_code=404, detail="Session introuvable")
        if sess['status'] == 'ended':
            raise HTTPException(status_code=410, detail="Cet appel est déjà terminé.")
        # Authorization : host + callee (p2p) OR conv member (group)
        allowed = user['user_id'] == sess['host_user_id']
        if not allowed and sess['kind'] == 'p2p':
            meta = sess['metadata'] if isinstance(sess['metadata'], dict) else json.loads(sess['metadata'] or '{}')
            allowed = user['user_id'] == meta.get('callee_id')
        if not allowed and sess['kind'] == 'group':
            ok = await conn.fetchrow(
                "SELECT 1 FROM conversation_participants WHERE conv_id=$1 AND user_id=$2",
                sess['conv_id'], user['user_id'],
            )
            allowed = bool(ok)
        if not allowed:
            raise HTTPException(status_code=403, detail="Accès refusé à cet appel")
    from services.livekit_service import generate_access_token
    err_map = _handle_lk_errors("mint_token")
    try:
        payload = await generate_access_token(
            identity=user['user_id'],
            name=f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip() or user.get('username') or user['user_id'],
            room=sess['room_name'],
            can_publish=True, can_subscribe=True,
            ttl_seconds=3600,
        )
    except Exception as e:
        raise err_map(e)
    return payload


@router.post("/{session_id}/join")
async def mark_joined(session_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sess = await conn.fetchrow("SELECT * FROM call_sessions WHERE session_id=$1", session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session introuvable")
        await conn.execute("""
            INSERT INTO call_participants (session_id, user_id, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (session_id, user_id) DO UPDATE
                SET joined_at = now(), left_at = NULL
        """, session_id, user['user_id'],
            'host' if user['user_id'] == sess['host_user_id'] else 'member')
        if sess['status'] == 'ringing':
            await conn.execute(
                "UPDATE call_sessions SET status='live' WHERE session_id=$1", session_id,
            )
    return {"ok": True, "session_id": session_id}


@router.post("/{session_id}/leave")
async def mark_left(session_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE call_participants SET left_at = now() "
            "WHERE session_id=$1 AND user_id=$2 AND left_at IS NULL",
            session_id, user['user_id'],
        )
        # If host leaves (or last participant), end the session
        active = await conn.fetchval(
            "SELECT COUNT(*) FROM call_participants "
            "WHERE session_id=$1 AND left_at IS NULL", session_id,
        )
        sess = await conn.fetchrow(
            "SELECT host_user_id, started_at, status FROM call_sessions WHERE session_id=$1",
            session_id,
        )
        if sess and sess['status'] != 'ended' and (active == 0 or user['user_id'] == sess['host_user_id']):
            ended_at = datetime.now(timezone.utc)
            duration = int((ended_at - sess['started_at']).total_seconds()) if sess['started_at'] else 0
            await conn.execute(
                "UPDATE call_sessions SET status='ended', ended_at=$1, duration_sec=$2 "
                "WHERE session_id=$3", ended_at, duration, session_id,
            )
            try:
                from services.livekit_service import delete_room
                await delete_room(f"japap_{session_id}")
            except Exception as e:
                logger.warning(f"delete_room failed (non-fatal): {e}")
    return {"ok": True, "session_id": session_id}


# ───── Sprint D — Recording + AI summary ───────────────────────────────────

@router.post("/{session_id}/record/start")
async def record_start(session_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sess = await conn.fetchrow("SELECT * FROM call_sessions WHERE session_id=$1", session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session introuvable")
        if sess['host_user_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Seul l'hôte peut enregistrer")
        if sess['status'] == 'ended':
            raise HTTPException(status_code=410, detail="Appel terminé — enregistrement impossible")

    from services.livekit_service import start_room_composite_egress
    from services.r2_storage_service import get_r2_config, build_recording_key
    recording_id = f"rec_{uuid.uuid4().hex[:16]}"
    err_map = _handle_lk_errors("record_start")
    try:
        r2 = await get_r2_config()
        key = build_recording_key(session_id, recording_id, ext="mp4")
        egress = await start_room_composite_egress(
            room_name=f"japap_{session_id}",
            storage_provider="r2",
            bucket=r2["bucket"], key=key,
        )
    except Exception as e:
        raise err_map(e)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO call_recordings
                (recording_id, session_id, egress_id, status,
                 storage_provider, storage_bucket, storage_key, mime_type)
            VALUES ($1, $2, $3, 'active', 'r2', $4, $5, 'video/mp4')
        """, recording_id, session_id, egress['egress_id'],
            r2["bucket"], key)
    return {
        "recording_id": recording_id,
        "egress_id": egress["egress_id"],
        "status": "active",
    }


@router.post("/{session_id}/record/stop")
async def record_stop(session_id: str, request: Request, bg: BackgroundTasks):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sess = await conn.fetchrow("SELECT * FROM call_sessions WHERE session_id=$1", session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session introuvable")
        if sess['host_user_id'] != user['user_id']:
            raise HTTPException(status_code=403, detail="Seul l'hôte peut stopper")
        rec = await conn.fetchrow("""
            SELECT * FROM call_recordings
            WHERE session_id=$1 AND status IN ('starting','active')
            ORDER BY started_at DESC LIMIT 1
        """, session_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Aucun enregistrement actif")

    from services.livekit_service import stop_egress
    err_map = _handle_lk_errors("record_stop")
    try:
        await stop_egress(rec['egress_id'])
    except Exception as e:
        raise err_map(e)
    from services.r2_storage_service import build_public_url
    public_url = ""
    try:
        public_url = await build_public_url(rec['storage_key'])
    except Exception:
        pass
    # Pre-create the summary row synchronously so the client's first /summary
    # poll immediately sees status='pending' (not 'none'). The AI pipeline
    # below updates the same row through 'transcribing' → 'summarizing' → 'ready'.
    import uuid as _uuid
    summary_id = f"sum_{_uuid.uuid4().hex[:16]}"
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE call_recordings SET status='ready', finalized_at=$1, public_url=$2
            WHERE recording_id=$3
        """, datetime.now(timezone.utc), public_url, rec['recording_id'])
        await conn.execute("""
            INSERT INTO call_summaries (summary_id, session_id, recording_id, status)
            VALUES ($1, $2, $3, 'pending')
            ON CONFLICT (summary_id) DO NOTHING
        """, summary_id, sess['session_id'], rec['recording_id'])

    # Kick off the AI pipeline in the background so the client returns fast.
    recording_id = rec['recording_id']
    async def _run_ai():
        try:
            from services.call_ai_service import run_call_ai_pipeline
            await run_call_ai_pipeline(recording_id=recording_id, summary_id=summary_id)
        except Exception as e:
            logger.warning(f"AI pipeline failed for {recording_id}: {e}")
    bg.add_task(_run_ai)

    return {
        "recording_id": recording_id,
        "status": "ready",
        "public_url": public_url,
        "ai_pipeline": "queued",
        "summary_id": summary_id,
    }


@router.get("/{session_id}/summary")
async def get_summary(session_id: str, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sess = await conn.fetchrow("SELECT * FROM call_sessions WHERE session_id=$1", session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session introuvable")
        # Participants-only
        ok = await conn.fetchrow(
            "SELECT 1 FROM call_participants WHERE session_id=$1 AND user_id=$2",
            session_id, user['user_id'],
        )
        if not ok and user['user_id'] != sess['host_user_id']:
            raise HTTPException(status_code=403, detail="Accès refusé")
        summ = await conn.fetchrow("""
            SELECT * FROM call_summaries WHERE session_id=$1
            ORDER BY created_at DESC LIMIT 1
        """, session_id)
        rec = await conn.fetchrow("""
            SELECT recording_id, storage_key, public_url, duration_sec, status
            FROM call_recordings WHERE session_id=$1
            ORDER BY started_at DESC LIMIT 1
        """, session_id)
    if not summ:
        return {"status": "none", "session_id": session_id, "recording": dict(rec) if rec else None}
    out = dict(summ)
    for k in ("created_at", "completed_at"):
        if out.get(k):
            out[k] = out[k].isoformat()
    # JSONB fields come back as str in asyncpg — normalize
    for k in ("key_points", "decisions", "action_items"):
        if isinstance(out.get(k), str):
            try:
                out[k] = json.loads(out[k])
            except Exception:
                out[k] = []
    out["recording"] = dict(rec) if rec else None
    return out


# ─────────────────────────────────────────────────────────────────────────
# Iter 59 — Share call summary into a conversation as a structured message
# ─────────────────────────────────────────────────────────────────────────


class ShareSummaryRequest(BaseModel):
    conv_id: Optional[str] = None        # explicit target conv; defaults to call's conv_id
    include_transcript: bool = False     # if True, the recipient can expand it


def _normalize_who_to_user_id(who_text: str, participant_users: list) -> tuple[str | None, str]:
    """Auto-match a Claude-extracted `who` string to a concrete user_id among
    the call participants. Falls back to the original text if no hit.

    Matching priority (case-insensitive) :
        1. Exact username match
        2. first_name token match
        3. last_name token match
        4. "Bob Marley" → first+last concatenation
    """
    if not who_text:
        return None, ""
    wt = who_text.strip().lower()
    if not wt or wt in ("—", "-", "?", "n/a", "à définir", "unknown"):
        return None, who_text
    # Score each participant
    for p in participant_users:
        uname = (p.get('username') or '').lower()
        fn = (p.get('first_name') or '').lower()
        ln = (p.get('last_name') or '').lower()
        full = f"{fn} {ln}".strip()
        if wt == uname or wt == fn or wt == ln or wt == full:
            return p['user_id'], (p.get('display_name') or who_text)
    # Loose contains (e.g., "Bob" matches "Bob Marley")
    for p in participant_users:
        fn = (p.get('first_name') or '').lower()
        if fn and (wt == fn or wt.startswith(fn + ' ') or wt.endswith(' ' + fn)):
            return p['user_id'], (p.get('display_name') or who_text)
    return None, who_text


@router.post("/{session_id}/summary/share")
async def share_summary_to_conv(session_id: str, req: ShareSummaryRequest, request: Request):
    """Post the call's AI summary as a structured `call_summary` message in the
    target conversation.

    - Only a participant of the call can share.
    - `action_items[].who` is auto-matched to a participant user_id when possible
      (fallback: keep the raw text). Assignees receive a real-time push.
    - Access control for the checklist itself is enforced at toggle time:
        can_toggle = (caller == assigned_user_id) OR (caller was in call_participants)
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sess = await conn.fetchrow(
            "SELECT session_id, host_user_id, conv_id FROM call_sessions WHERE session_id=$1",
            session_id,
        )
        if not sess:
            raise HTTPException(status_code=404, detail="Session introuvable")
        was_participant = await conn.fetchrow(
            "SELECT 1 FROM call_participants WHERE session_id=$1 AND user_id=$2",
            session_id, user['user_id'],
        )
        if not was_participant and user['user_id'] != sess['host_user_id']:
            raise HTTPException(status_code=403, detail="Seuls les participants de l'appel peuvent partager le résumé.")
        summ = await conn.fetchrow(
            "SELECT * FROM call_summaries WHERE session_id=$1 AND status='ready' ORDER BY created_at DESC LIMIT 1",
            session_id,
        )
        if not summ:
            raise HTTPException(status_code=409, detail="Le résumé n'est pas encore prêt.")
        target_conv = req.conv_id or sess['conv_id']
        if not target_conv:
            raise HTTPException(status_code=400, detail="Aucune conversation cible — précisez conv_id pour les appels 1-1.")
        # Viewer must be a member of the target conv too
        member = await conn.fetchrow(
            "SELECT 1 FROM conversation_participants WHERE conv_id=$1 AND user_id=$2",
            target_conv, user['user_id'],
        )
        if not member:
            raise HTTPException(status_code=403, detail="Vous n'êtes pas membre de cette conversation.")
        # Fetch all call participants with their profile for auto-match
        participant_users = await conn.fetch("""
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar,
                   (COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')) AS display_name
            FROM call_participants cp JOIN users u ON u.user_id = cp.user_id
            WHERE cp.session_id = $1
        """, session_id)
        participant_list = [dict(r) for r in participant_users]
        participant_ids = [p['user_id'] for p in participant_list]

        # Normalize JSONB-ish lists
        def _jb(x):
            if isinstance(x, (list, dict)):
                return x
            try:
                return json.loads(x) if x else []
            except Exception:
                return []
        key_points = _jb(summ['key_points'])
        decisions = _jb(summ['decisions'])
        action_items_raw = _jb(summ['action_items'])

        # Build structured action items with auto-match + unique ids
        structured_items = []
        for idx, raw in enumerate(action_items_raw):
            if not isinstance(raw, dict):
                raw = {"what": str(raw)}
            who_text = raw.get('who') or ''
            who_user_id, who_display = _normalize_who_to_user_id(who_text, participant_list)
            structured_items.append({
                "id": f"ai_{uuid.uuid4().hex[:12]}",
                "who_user_id": who_user_id,
                "who_text": who_display or who_text or "",
                "what": raw.get('what') or '',
                "due": raw.get('due') or '',
                "done": False,
                "done_by_user_id": None,
                "done_at": None,
            })

        payload = {
            "type": "call_summary",
            "session_id": session_id,
            "host_user_id": sess['host_user_id'],
            "participant_ids": participant_ids,
            "summary": summ['summary'] or '',
            "key_points": key_points,
            "decisions": decisions,
            "action_items": structured_items,
            "transcript": (summ['transcript'] or '') if req.include_transcript else '',
            "language": summ['language'] or 'fr',
        }
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        await conn.execute("""
            INSERT INTO messages (msg_id, conv_id, sender_id, text, media,
                                  message_type, structured_data, call_session_id, status)
            VALUES ($1, $2, $3, $4, '', 'call_summary', $5::jsonb, $6, 'sent')
        """, msg_id, target_conv, user['user_id'],
             f"📋 Résumé d'appel — {summ['summary'][:80] if summ['summary'] else ''}",
             json.dumps(payload), session_id)

        # Broadcast + notify assignees
        from routes.realtime import push_to_user
        sender_user = await conn.fetchrow(
            "SELECT first_name, last_name, avatar FROM users WHERE user_id=$1", user['user_id'],
        )
        sender_name = f"{sender_user['first_name'] or ''} {sender_user['last_name'] or ''}".strip()
        new_message_payload = {
            "msg_id": msg_id,
            "conv_id": target_conv,
            "sender_id": user['user_id'],
            "sender_name": sender_name,
            "sender_avatar": sender_user['avatar'] or '',
            "text": f"📋 Résumé d'appel",
            "message_type": "call_summary",
            "structured_data": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        conv_members = await conn.fetch(
            "SELECT user_id FROM conversation_participants WHERE conv_id=$1",
            target_conv,
        )
        for m in conv_members:
            await push_to_user(m['user_id'], 'new_message', new_message_payload)
        # Assignee-specific task notifications
        for ai in structured_items:
            if ai.get('who_user_id') and ai['who_user_id'] != user['user_id']:
                await push_to_user(ai['who_user_id'], 'notification', {
                    "type": "call_task_assigned",
                    "title": "Nouvelle tâche issue d'un appel",
                    "body": ai['what'][:140],
                    "conv_id": target_conv,
                    "msg_id": msg_id,
                    "item_id": ai['id'],
                    "due": ai.get('due') or '',
                })

    return {"ok": True, "msg_id": msg_id, "conv_id": target_conv, "action_items": len(structured_items)}


class ToggleActionItemRequest(BaseModel):
    done: bool


@router.patch("/summary/action-items/{msg_id}/{item_id}")
async def toggle_action_item(msg_id: str, item_id: str, req: ToggleActionItemRequest, request: Request):
    """Flip the `done` flag of a single action item inside a `call_summary`
    structured message. Access control :
        - the assigned user (who_user_id), OR
        - any participant of the underlying call (call_session_id)
    anyone else → 403.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        msg = await conn.fetchrow(
            "SELECT msg_id, conv_id, message_type, structured_data, call_session_id "
            "FROM messages WHERE msg_id=$1",
            msg_id,
        )
        if not msg or msg['message_type'] != 'call_summary':
            raise HTTPException(status_code=404, detail="Message introuvable")
        sd = msg['structured_data']
        if isinstance(sd, str):
            try: sd = json.loads(sd)
            except Exception: sd = None
        if not sd:
            raise HTTPException(status_code=500, detail="Données du message corrompues")
        items = sd.get('action_items') or []
        target = next((it for it in items if it.get('id') == item_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Item introuvable")

        # Access : assignee OR call participant
        can_toggle = False
        if target.get('who_user_id') == user['user_id']:
            can_toggle = True
        else:
            if msg['call_session_id']:
                was_part = await conn.fetchrow(
                    "SELECT 1 FROM call_participants WHERE session_id=$1 AND user_id=$2",
                    msg['call_session_id'], user['user_id'],
                )
                if was_part:
                    can_toggle = True
        if not can_toggle:
            raise HTTPException(status_code=403, detail="Seuls l'assigné et les participants de l'appel peuvent cocher cette tâche.")

        # Mutate
        target['done'] = bool(req.done)
        target['done_by_user_id'] = user['user_id'] if req.done else None
        target['done_at'] = datetime.now(timezone.utc).isoformat() if req.done else None
        sd['action_items'] = items
        await conn.execute(
            "UPDATE messages SET structured_data=$1::jsonb, updated_at=NOW() WHERE msg_id=$2",
            json.dumps(sd), msg_id,
        )

        # Broadcast the updated message to conv members
        from routes.realtime import push_to_user
        update_payload = {
            "msg_id": msg_id,
            "conv_id": msg['conv_id'],
            "structured_data": sd,
        }
        conv_members = await conn.fetch(
            "SELECT user_id FROM conversation_participants WHERE conv_id=$1",
            msg['conv_id'],
        )
        for m in conv_members:
            await push_to_user(m['user_id'], 'message_updated', update_payload)

    return {"ok": True, "item": target}


class ReassignActionItemRequest(BaseModel):
    user_id: Optional[str] = None    # new assignee (None clears the assignment)
    who_text: Optional[str] = None   # free-text fallback when user_id is None


@router.patch("/summary/action-items/{msg_id}/{item_id}/assign")
async def reassign_action_item(msg_id: str, item_id: str, req: ReassignActionItemRequest, request: Request):
    """Re-assign an action item to a different participant. Only call participants
    can re-assign (to keep it simple and prevent chat-member-chaos). The new
    assignee must also be a call participant or left blank (free text).
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        msg = await conn.fetchrow(
            "SELECT msg_id, conv_id, message_type, structured_data, call_session_id "
            "FROM messages WHERE msg_id=$1",
            msg_id,
        )
        if not msg or msg['message_type'] != 'call_summary':
            raise HTTPException(status_code=404, detail="Message introuvable")
        if not msg['call_session_id']:
            raise HTTPException(status_code=400, detail="Message non lié à un appel")
        was_part = await conn.fetchrow(
            "SELECT 1 FROM call_participants WHERE session_id=$1 AND user_id=$2",
            msg['call_session_id'], user['user_id'],
        )
        if not was_part:
            raise HTTPException(status_code=403, detail="Seuls les participants de l'appel peuvent réassigner.")
        sd = msg['structured_data']
        if isinstance(sd, str):
            try: sd = json.loads(sd)
            except Exception: sd = None
        if not sd:
            raise HTTPException(status_code=500, detail="Données corrompues")
        items = sd.get('action_items') or []
        target = next((it for it in items if it.get('id') == item_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Item introuvable")
        new_user_id = req.user_id
        who_display = req.who_text or ''
        if new_user_id:
            # Must be a participant
            part_row = await conn.fetchrow("""
                SELECT u.user_id, u.first_name, u.last_name FROM call_participants cp
                JOIN users u ON u.user_id = cp.user_id
                WHERE cp.session_id = $1 AND u.user_id = $2
            """, msg['call_session_id'], new_user_id)
            if not part_row:
                raise HTTPException(status_code=400, detail="Utilisateur non participant à l'appel.")
            who_display = f"{part_row['first_name'] or ''} {part_row['last_name'] or ''}".strip()
        target['who_user_id'] = new_user_id
        target['who_text'] = who_display or target.get('who_text') or ''
        sd['action_items'] = items
        await conn.execute(
            "UPDATE messages SET structured_data=$1::jsonb, updated_at=NOW() WHERE msg_id=$2",
            json.dumps(sd), msg_id,
        )
        # Notify new assignee if any
        from routes.realtime import push_to_user
        if new_user_id and new_user_id != user['user_id']:
            await push_to_user(new_user_id, 'notification', {
                "type": "call_task_reassigned",
                "title": "Tâche qui vous est réassignée",
                "body": target.get('what', '')[:140],
                "conv_id": msg['conv_id'],
                "msg_id": msg_id,
                "item_id": item_id,
            })
        conv_members = await conn.fetch(
            "SELECT user_id FROM conversation_participants WHERE conv_id=$1",
            msg['conv_id'],
        )
        for m in conv_members:
            await push_to_user(m['user_id'], 'message_updated', {
                "msg_id": msg_id, "conv_id": msg['conv_id'], "structured_data": sd,
            })
    return {"ok": True, "item": target}


# ───── Admin health checks ──────────────────────────────────────────────────

@router.get("/test-livekit")
async def test_livekit(request: Request):
    from routes.admin import require_admin
    await require_admin(request)
    from services.livekit_service import test_connection
    return await test_connection()


@router.get("/test-r2")
async def test_r2(request: Request):
    from routes.admin import require_admin
    await require_admin(request)
    from services.r2_storage_service import test_connection
    return await test_connection()
