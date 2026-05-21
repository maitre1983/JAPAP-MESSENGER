"""iter241a — Forecast user-facing routes (read + place bet).

All routes are PUBLIC by default — bet placement requires auth. Module is
gated by `forecast_settings.module_enabled` so the admin can kill the entire
feature in one click without redeploying.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from services import forecast_service as svc

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


class BetRequest(BaseModel):
    option_id: str = Field(..., min_length=3, max_length=64)
    token_type: str = Field(default="usd", min_length=2, max_length=10)
    stake_amount: float = Field(..., gt=0)


@router.get("/settings/public")
async def public_settings():
    """Slimmed-down settings the client needs to render the UI — no admin-only
    flags like AI toggles leak here. Safe for unauthenticated users."""
    s = await svc.get_settings()
    return {
        "module_enabled":    bool(s.get("module_enabled")),
        "token_usd_enabled": bool(s.get("token_usd_enabled")),
        "default_min_bet":   float(s.get("default_min_bet") or 1),
        "default_max_bet":   float(s.get("default_max_bet") or 10000),
        "categories":        list(svc.VALID_CATEGORIES),
    }


@router.get("/markets")
async def list_markets(category: Optional[str] = Query(default=None),
                        status: str = Query(default="active",
                                             regex="^(active|closed|resolved|cancelled|draft)$"),
                        limit: int = Query(default=50, ge=1, le=200),
                        offset: int = Query(default=0, ge=0)):
    await svc._require_module_enabled()
    return {"markets": await svc.list_markets(category, status, limit, offset)}


@router.get("/markets/{market_id}")
async def get_market(market_id: str):
    await svc._require_module_enabled()
    m = await svc.get_market(market_id)
    if not m:
        raise HTTPException(status_code=404, detail="Marché introuvable.")
    return m


@router.post("/markets/{market_id}/bet")
async def place_bet(market_id: str, req: BetRequest, request: Request):
    user = await get_current_user(request)
    return await svc.place_bet(
        user_id=user["user_id"],
        market_id=market_id,
        option_id=req.option_id,
        token_type=req.token_type,
        stake_amount=req.stake_amount,
    )


@router.get("/my-bets")
async def my_bets(request: Request,
                   status: Optional[str] = Query(default=None,
                                                  regex="^(placed|won|lost|refunded|cancelled)$"),
                   limit: int = Query(default=50, ge=1, le=200),
                   offset: int = Query(default=0, ge=0)):
    user = await get_current_user(request)
    return {"bets": await svc.list_my_bets(user["user_id"], status, limit, offset)}
