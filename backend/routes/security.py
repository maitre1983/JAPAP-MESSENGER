"""
JAPAP — /api/security/* endpoints (iter82).

User-facing security controls:
  • GET    /sessions                    — list active devices
  • DELETE /sessions/{session_id}       — logout a single device
  • POST   /logout-all                  — log out every device (incl. self)
  • GET    /events                      — recent security events for this user
  • GET    /csrf-token                  — fetch current CSRF token (SPA helper)
"""
from fastapi import APIRouter, HTTPException, Request, Response
from routes.auth import get_current_user
from services.security_service import (
    list_active_sessions, revoke_session, revoke_all_user_jtis,
    recent_security_events, log_security_event,
)

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/sessions")
async def my_sessions(request: Request):
    user = await get_current_user(request)
    return {"sessions": await list_active_sessions(user["user_id"])}


@router.delete("/sessions/{session_id}")
async def revoke_my_session(session_id: str, request: Request):
    user = await get_current_user(request)
    ok = await revoke_session(session_id, user["user_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Session introuvable")
    await log_security_event(
        user["user_id"], "auth.session_revoked",
        severity="info",
        ip=request.client.host if request.client else "",
        ua=request.headers.get("user-agent", ""),
        details={"session_id": session_id},
    )
    return {"status": "ok"}


@router.post("/logout-all")
async def logout_all_devices(request: Request, response: Response):
    user = await get_current_user(request)
    n = await revoke_all_user_jtis(user["user_id"], reason="user_logout_all")
    await log_security_event(
        user["user_id"], "auth.logout_all",
        severity="warning",
        ip=request.client.host if request.client else "",
        ua=request.headers.get("user-agent", ""),
        details={"revoked_count": n},
    )
    # Kill THIS session too — the user explicitly asked for it.
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    response.delete_cookie("session_token", path="/")
    return {"status": "ok", "revoked_count": n}


@router.get("/events")
async def my_security_events(request: Request, limit: int = 50):
    user = await get_current_user(request)
    return {"events": await recent_security_events(user["user_id"], limit=min(limit, 200))}


@router.get("/csrf-token")
async def get_csrf_token(request: Request):
    """Helper for the SPA to read the current CSRF token. The token is
    already set as a non-HttpOnly cookie by the CsrfMiddleware, but some
    environments (mobile webviews) make cookie reading awkward."""
    return {"csrf_token": request.cookies.get("csrf_token", "")}
