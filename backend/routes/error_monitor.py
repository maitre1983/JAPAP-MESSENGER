"""
AI Error Monitor — REST endpoints.
====================================

Frontend report:
  POST /api/errors/report  (auth optional, throttled)

Admin:
  GET  /api/admin/errors                 — paginated + filtered list
  GET  /api/admin/errors/{signature}     — single group + last 20 events
  POST /api/admin/errors/{signature}/{action}     — investigate/fix/ignore/reopen
  POST /api/admin/errors/{signature}/ai-suggest   — Claude RCA
"""
import logging
import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user, log_admin_action
from services.error_monitor import (
    ensure_errors_ddl, record_error, list_groups,
    update_group_status, ai_suggest_fix,
    VALID_SEVERITIES, VALID_SOURCES,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple in-memory rate limiter for the public /errors/report endpoint —
# 50 reports per IP per minute is plenty for genuine FE crashes.
_REPORT_BUCKET: dict[str, list[float]] = defaultdict(list)
_REPORT_LIMIT = 50
_REPORT_WINDOW_S = 60.0


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "anon"


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès admin requis")
    return user


class ErrorReportRequest(BaseModel):
    source: str = Field("frontend", pattern="^(frontend|backend)$")
    module: str = Field("unknown", max_length=80)
    message: str = Field(..., max_length=2000)
    stack: str = Field("", max_length=8000)
    severity: str = Field("medium", pattern="^(low|medium|high|critical)$")
    url: str = Field("", max_length=500)
    user_agent: str = Field("", max_length=255)
    http_status: Optional[int] = None
    request_id: str = Field("", max_length=64)


@router.post("/api/errors/report")
async def report_error(req: ErrorReportRequest, request: Request):
    """Public endpoint used by the FE error boundary + global axios
    interceptor to ship errors back to the server. Auth is OPTIONAL —
    we attach the user_id when available to compute affected_users.
    """
    ip = _client_ip(request)
    bucket = _REPORT_BUCKET[ip]
    now = time.time()
    while bucket and bucket[0] < now - _REPORT_WINDOW_S:
        bucket.pop(0)
    if len(bucket) >= _REPORT_LIMIT:
        raise HTTPException(status_code=429, detail="Trop de rapports d'erreur.")
    bucket.append(now)

    user_id = None
    try:
        user = await get_current_user(request)
        user_id = user.get("user_id")
    except HTTPException:
        pass  # report from unauthenticated user is still valid (login form crash, etc.)

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await record_error(
            conn,
            source=req.source, module=req.module, message=req.message,
            stack=req.stack, severity=req.severity,
            user_id=user_id, url=req.url, user_agent=req.user_agent,
            http_status=req.http_status, request_id=req.request_id,
        )
    return result


@router.get("/api/admin/errors")
async def admin_list_errors(
    request: Request,
    status: str = "",
    severity: str = "",
    module: str = "",
    source: str = "",
    since_days: int = 30,
    limit: int = 100,
    offset: int = 0,
):
    await _require_admin(request)
    if since_days < 1 or since_days > 365:
        raise HTTPException(status_code=400, detail="since_days doit être entre 1 et 365")
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit doit être entre 1 et 500")
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await list_groups(
                conn, status=status, severity=severity, module=module,
                source=source, since_days=since_days, limit=limit, offset=offset,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/admin/errors/{signature}")
async def admin_get_error(signature: str, request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_errors_ddl(conn)
        grp = await conn.fetchrow(
            "SELECT * FROM error_groups WHERE signature = $1", signature,
        )
        if not grp:
            raise HTTPException(status_code=404, detail="Groupe d'erreurs introuvable")
        events = await conn.fetch(
            """SELECT id, occurred_at, source, module, severity, message,
                       url, http_status, user_id
                  FROM error_events WHERE signature = $1
                  ORDER BY occurred_at DESC LIMIT 20""",
            signature,
        )
    from services.error_monitor import _group_to_dict
    return {
        "group": _group_to_dict(grp),
        "events": [
            {
                "id": int(e["id"]),
                "occurred_at": e["occurred_at"].isoformat(),
                "source": e["source"],
                "module": e["module"],
                "severity": e["severity"],
                "message": e["message"],
                "url": e["url"] or "",
                "http_status": e["http_status"],
                "user_id": e["user_id"] or "",
            } for e in events
        ],
    }


VALID_ACTIONS = {
    "investigate": "investigating",
    "fix":         "fixed",
    "ignore":      "ignored",
    "reopen":      "open",
}


# iter138 — Bulk action endpoint for the "post-fix-campaign cleanup" flow.
# Use case: agent ships a wave of fixes (e.g. iter134 + iter136), all old
# groups are now stale → admin marks them all `fixed` in one click. The
# `before_iso` cutoff filters by `last_seen` so freshly-recurring groups
# stay open.

class BulkActionRequest(BaseModel):
    action: str = Field(..., pattern="^(investigate|fix|ignore|reopen)$")
    statuses: list[str] = Field(default_factory=lambda: ["open"])
    severities: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    before_iso: str = ""           # mark only groups whose last_seen is BEFORE this
    signatures: list[str] = Field(default_factory=list)  # explicit override


@router.post("/api/admin/errors/bulk-action")
async def admin_bulk_action(req: BulkActionRequest, request: Request):
    """Apply an action to many groups at once. Returns counts."""
    admin = await _require_admin(request)
    new_status = VALID_ACTIONS[req.action]
    pool = await get_pool()
    matched = 0
    updated = 0
    async with pool.acquire() as conn:
        await ensure_errors_ddl(conn)
        # Build the dynamic SELECT to pick affected signatures.
        clauses: list[str] = []
        params: list = []
        idx = 1
        if req.signatures:
            clauses.append(f"signature = ANY(${idx}::text[])")
            params.append(req.signatures); idx += 1
        else:
            if req.statuses:
                clauses.append(f"status = ANY(${idx}::text[])")
                params.append(req.statuses); idx += 1
            if req.severities:
                clauses.append(f"severity = ANY(${idx}::text[])")
                params.append(req.severities); idx += 1
            if req.sources:
                clauses.append(f"source = ANY(${idx}::text[])")
                params.append(req.sources); idx += 1
            if req.modules:
                clauses.append(f"module = ANY(${idx}::text[])")
                params.append(req.modules); idx += 1
            if req.before_iso:
                try:
                    from datetime import datetime as _dt
                    cutoff = _dt.fromisoformat(req.before_iso.replace("Z", "+00:00"))
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=f"before_iso invalide: {e}") from e
                clauses.append(f"last_seen < ${idx}")
                params.append(cutoff); idx += 1
        where = " AND ".join(clauses) if clauses else "TRUE"
        rows = await conn.fetch(f"SELECT signature FROM error_groups WHERE {where}", *params)
        matched = len(rows)
        for r in rows:
            try:
                await update_group_status(conn, r["signature"], new_status, admin["user_id"])
                updated += 1
            except (ValueError, Exception):
                continue
    await log_admin_action(
        actor_id=admin["user_id"], actor_email=admin.get("email", ""),
        action=f"errors.bulk_{req.action}",
        metadata={"matched": matched, "updated": updated, "filters": req.model_dump()},
    )
    return {"matched": matched, "updated": updated, "new_status": new_status}


@router.post("/api/admin/errors/{signature}/{action}")
async def admin_action(signature: str, action: str, request: Request):
    admin = await _require_admin(request)
    if action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Action invalide ({list(VALID_ACTIONS.keys())})")
    new_status = VALID_ACTIONS[action]
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            updated = await update_group_status(
                conn, signature, new_status, admin["user_id"],
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not updated:
            raise HTTPException(status_code=404, detail="Groupe d'erreurs introuvable")
    await log_admin_action(
        actor_id=admin["user_id"],
        actor_email=admin.get("email", ""),
        action=f"errors.{action}",
        metadata={"signature": signature, "new_status": new_status},
    )
    return updated


@router.post("/api/admin/errors/{signature}/ai-suggest")
async def admin_ai_suggest(signature: str, request: Request):
    """Ask Claude Sonnet 4.5 for a RCA + fix hint based on the last 5 events
    of the group. Result persisted to error_groups.ai_suggestion."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            sug = await ai_suggest_fix(conn, signature)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
    await log_admin_action(
        actor_id=admin["user_id"],
        actor_email=admin.get("email", ""),
        action="errors.ai_suggest",
        metadata={"signature": signature},
    )
    return {"signature": signature, "ai_suggestion": sug}
