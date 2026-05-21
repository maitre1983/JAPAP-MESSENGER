"""iter241a — Forecast admin routes (CRUD + lifecycle + metrics).

All routes require `require_admin`. IA endpoints (suggest_markets,
detect_result, abuse_check) are stubbed for forward-compat — they return 501
until iter241b wires the Claude integration."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from routes.admin import require_admin
from services import forecast_service as svc

router = APIRouter(prefix="/api/admin/forecast", tags=["forecast_admin"])


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@router.get("/settings")
async def get_settings(request: Request):
    admin = await require_admin(request)  # noqa: F841
    return await svc.get_settings()


class SettingsUpdate(BaseModel):
    module_enabled: Optional[bool] = None
    token_usd_enabled: Optional[bool] = None
    token_mir_enabled: Optional[bool] = None
    token_limo_enabled: Optional[bool] = None
    token_usdt_enabled: Optional[bool] = None
    ai_suggestions_enabled: Optional[bool] = None
    ai_result_detection_enabled: Optional[bool] = None
    ai_abuse_detection_enabled: Optional[bool] = None
    default_min_bet: Optional[float] = None
    default_max_bet: Optional[float] = None
    default_max_bet_per_user: Optional[float] = None
    default_max_exposure: Optional[float] = None
    default_platform_fee_percent: Optional[float] = None


@router.put("/settings")
async def put_settings(payload: SettingsUpdate, request: Request):
    admin = await require_admin(request)
    return await svc.update_settings(
        payload.model_dump(exclude_none=True),
        admin_id=admin["user_id"],
    )


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------
@router.get("/markets")
async def list_markets(request: Request,
                        status: Optional[str] = Query(default=None),
                        limit: int = Query(default=100, ge=1, le=500),
                        offset: int = Query(default=0, ge=0)):
    await require_admin(request)
    return {"markets": await svc.admin_list_markets(status, limit, offset)}


class OptionIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    multiplier: float = Field(default=2.0, gt=1.0, le=100.0)


class MarketCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=500)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="world")
    market_type: str = Field(default="binary")
    closes_at: str  # ISO datetime — asyncpg parses str → timestamptz
    source_label: str = Field(default="", max_length=255)
    source_url: str = Field(default="", max_length=500)
    options: list[OptionIn]
    min_bet: Optional[float] = None
    max_bet: Optional[float] = None
    max_bet_per_user: Optional[float] = None
    max_exposure: Optional[float] = None
    platform_fee_percent: Optional[float] = None


@router.post("/markets")
async def create_market(payload: MarketCreate, request: Request):
    admin = await require_admin(request)
    from datetime import datetime as _dt
    # Accept ISO with or without 'Z'.
    closes_at = payload.closes_at.replace("Z", "+00:00") if payload.closes_at.endswith("Z") \
        else payload.closes_at
    try:
        closes_dt = _dt.fromisoformat(closes_at)
    except ValueError:
        raise HTTPException(status_code=422, detail="closes_at doit être au format ISO 8601.")
    body = payload.model_dump()
    body["closes_at"] = closes_dt
    return await svc.admin_create_market(body, admin_id=admin["user_id"])


@router.post("/markets/{market_id}/{action}")
async def market_action(market_id: str, action: str, request: Request):
    """action ∈ {activate, pause, resume, close, cancel}."""
    await require_admin(request)
    return await svc.admin_set_market_status(market_id, action)


class ResolvePayload(BaseModel):
    winning_option_id: str = Field(..., min_length=3, max_length=64)
    result_notes: str = Field(default="", max_length=2000)
    source_reference: str = Field(default="", max_length=500)


@router.post("/markets/{market_id}/resolve")
async def resolve_market(market_id: str, payload: ResolvePayload, request: Request):
    admin = await require_admin(request)
    return await svc.admin_resolve_market(
        market_id=market_id,
        winning_option_id=payload.winning_option_id,
        result_notes=payload.result_notes,
        source_reference=payload.source_reference,
        admin_id=admin["user_id"],
    )


@router.get("/markets/{market_id}/exposure")
async def market_exposure(market_id: str, request: Request):
    await require_admin(request)
    return await svc.admin_market_exposure(market_id)


@router.get("/metrics")
async def metrics(request: Request):
    await require_admin(request)
    return await svc.admin_metrics()


# ---------------------------------------------------------------------------
# iter241b stubs — surface the endpoints so the admin UI doesn't 404 if
# someone clicks the AI buttons in advance.
# ---------------------------------------------------------------------------
@router.post("/ai/suggest-markets")
async def ai_suggest_markets(request: Request):
    await require_admin(request)
    raise HTTPException(status_code=501,
                         detail="IA suggestions disponibles en iter241b.")


@router.post("/ai/detect-result/{market_id}")
async def ai_detect_result(market_id: str, request: Request):
    await require_admin(request)
    raise HTTPException(status_code=501,
                         detail="IA détection de résultat disponible en iter241b.")


@router.post("/ai/check-abuse/{market_id}")
async def ai_check_abuse(market_id: str, request: Request):
    await require_admin(request)
    raise HTTPException(status_code=501,
                         detail="IA détection d'abus disponible en iter241b.")
