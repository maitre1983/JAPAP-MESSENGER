"""
iter239a4 — Admin FX cache refresh endpoint (STRICTLY ADDITIVE).

  POST /api/admin/fx/refresh-cache    (admin/superadmin only)

Wipes the in-memory FX cache so the next conversion request goes back
through the full priority chain (admin manual → live → fallback). Useful
when ops want to verify a freshly-set `usd_ghs_rate` immediately without
waiting for the 1-hour TTL or the 60-second settings cache.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from routes.auth import require_admin
from services.fx_service import reset_cache, get_usd_to_ghs_info

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/fx", tags=["admin_fx"])


@router.post("/refresh-cache")
async def refresh_fx_cache(request: Request):
    await require_admin(request)
    reset_cache()
    info = await get_usd_to_ghs_info()
    return {
        "status": "ok",
        "cache": "cleared",
        "current": info,
    }


admin_fx_router = router

__all__ = ["router", "admin_fx_router"]
