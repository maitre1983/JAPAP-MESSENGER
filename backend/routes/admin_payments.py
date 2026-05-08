"""
iter207 — /api/admin/payments/hubtel/* — EAA-style Hubtel admin endpoints.

Admin-only (require superadmin/admin role).
    GET   /api/admin/payments/hubtel/config         → read config (masked)
    POST  /api/admin/payments/hubtel/config         → save partial config
    POST  /api/admin/payments/hubtel/test           → test connection
    GET   /api/admin/payments/hubtel/methods/{cc}   → available methods by country
    GET   /api/admin/payments/hubtel/exchange-rate  → live USD→local preview
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routes.admin import require_admin
from services.hubtel_service import (
    HubtelAPIError,
    HubtelConfigError,
    convert_usd_to_local,
    get_available_methods,
    get_config,
    save_config,
    test_connection,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/payments", tags=["admin", "payments"])


@router.get("/hubtel/config")
async def admin_get_hubtel_config(request: Request):
    await require_admin(request)
    return await get_config(mask=True)


class HubtelConfigUpdate(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    merchant_account: Optional[str] = None
    callback_url: Optional[str] = None
    return_url: Optional[str] = None
    cancel_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    enabled: Optional[bool] = None
    sandbox_mode: Optional[bool] = None
    environment: Optional[str] = None      # "sandbox" | "production"
    min_deposit: Optional[float] = None
    max_deposit: Optional[float] = None
    fee_percent: Optional[float] = None


@router.post("/hubtel/config")
async def admin_save_hubtel_config(body: HubtelConfigUpdate, request: Request):
    await require_admin(request)
    data = {k: v for k, v in body.dict().items() if v is not None}
    result = await save_config(data)
    # Return the (masked) updated config so the UI refreshes immediately.
    return {**result, "config": await get_config(mask=True)}


@router.post("/hubtel/test")
async def admin_test_hubtel(request: Request):
    await require_admin(request)
    return await test_connection()


@router.get("/hubtel/methods/{country_code}")
async def admin_hubtel_methods(country_code: str, request: Request):
    await require_admin(request)
    return {
        "country": country_code.upper(),
        "methods": await get_available_methods(country_code),
    }


@router.get("/hubtel/exchange-rate")
async def admin_hubtel_exchange_rate(
    request: Request, amount_usd: float = 1.0, currency: str = "GHS",
):
    await require_admin(request)
    return await convert_usd_to_local(amount_usd, currency)
