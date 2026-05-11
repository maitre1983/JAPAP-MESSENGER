"""
iter239h — Admin endpoints for the Vendor Health Dashboard.

Read-only routes:
  • GET  /api/admin/vendor-health/status   → snapshot of every vendor
  • POST /api/admin/vendor-health/refresh  → forced re-run of all pings

The 5-min cron is started lazily on the first GET (or by the server
startup hook) — see services/vendor_health.vendor_health_loop.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request

from routes.auth import require_admin
from services.vendor_health import (
    get_vendor_state,
    run_all_checks,
    vendor_health_loop,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/vendor-health", tags=["admin_vendor_health"])

_loop_started = False


def _ensure_loop_started() -> None:
    """Start the background cron exactly once per process."""
    global _loop_started
    if _loop_started:
        return
    _loop_started = True
    try:
        asyncio.create_task(vendor_health_loop())
        logger.info("[vendor-health] background loop scheduled")
    except RuntimeError:
        # No running loop yet — server.py startup hook will do it.
        _loop_started = False


@router.get("/status")
async def admin_vendor_health_status(request: Request):
    await require_admin(request)
    _ensure_loop_started()
    state = get_vendor_state()
    # If state is empty (first call before the cron warmed up), kick a
    # synchronous run so the admin sees results immediately.
    if not state:
        state = await run_all_checks()
    return {
        "vendors": state,
        "interval_seconds": 300,
        "thresholds_ms": {"slow": 1500, "timeout": 8000},
    }


@router.post("/refresh")
async def admin_vendor_health_refresh(request: Request):
    """Forced re-run — useful when the admin makes a config change and
    wants to verify immediately without waiting for the next 5-min tick."""
    await require_admin(request)
    _ensure_loop_started()
    state = await run_all_checks()
    return {"status": "refreshed", "vendors": state}


admin_vendor_health_router = router
__all__ = ["router", "admin_vendor_health_router"]
