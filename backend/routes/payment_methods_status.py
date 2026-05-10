"""
iter238 — Public read endpoint for the per-method admin toggles.

GET /api/wallet/payment-methods/status   (auth)
  → { paystack: bool, hubtel_card: bool, hubtel_momo: bool,
      nowpayments: bool, orange_money: bool, wave: bool,
      usdt_manual: bool }

Reads `system_settings` (admin_settings table) keys named
`{method}_enabled` with default TRUE. The frontend uses this to hide
methods that the admin has just toggled off without a redeploy.

ADDITIVE — does NOT touch the existing /api/wallet/payment-methods
catalog endpoint.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from routes.auth import get_current_user
from services.settings_service import get_setting

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payment_methods_status"])

# (method key, default ON?). When default ON, an unset value returns True.
_METHOD_TOGGLES: list[tuple[str, bool]] = [
    ("paystack",     True),
    ("hubtel_card",  False),  # default OFF — per iter238 spec
    ("hubtel_momo",  True),
    ("nowpayments",  False),  # default OFF — per iter238 spec
    ("orange_money", True),
    ("wave",         True),
    ("usdt_manual",  True),
]


def _coerce_bool(raw: str | None, default: bool) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@router.get("/api/wallet/payment-methods/status")
async def payment_methods_status(request: Request):
    await get_current_user(request)
    out: dict[str, bool] = {}
    for key, default in _METHOD_TOGGLES:
        raw = await get_setting(f"{key}_enabled")
        out[key] = _coerce_bool(raw, default)
    return out


payment_methods_status_router = router

__all__ = ["router", "payment_methods_status_router"]
