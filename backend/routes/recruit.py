"""
JAPAP — Recruit/Viral routes (iter141seven)
============================================

Exposes:
  GET  /api/recruit/leaderboard            — public top recruiters (rolling window)
  GET  /api/recruit/me                     — caller's recruiting stats + badges
  GET  /api/admin/recruit/settings         — admin: read all tunables
  PUT  /api/admin/recruit/settings         — admin: update tunables
"""
from __future__ import annotations
import logging
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional

from database import get_pool
from routes.auth import get_current_user
from services.recruit_service import (
    get_recruit_settings,
    update_recruit_settings,
    get_recruit_leaderboard,
    get_my_recruit_stats,
    ensure_recruit_ddl,
    RECRUIT_DEFAULTS,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["recruit"])


# ───────────────────────────────────────────────────────────────────────
#  Public — leaderboard (no auth required)
# ───────────────────────────────────────────────────────────────────────

@router.get("/api/recruit/leaderboard")
async def recruit_leaderboard(
    period_days: Optional[int] = Query(None, ge=1, le=365),
    limit: Optional[int] = Query(None, ge=1, le=100),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_recruit_ddl(conn)
        items = await get_recruit_leaderboard(conn, period_days=period_days, limit=limit)
        cfg = await get_recruit_settings()
    return {
        "items": items,
        "period_days": int(period_days or cfg["recruit_leaderboard_period_days"]),
        "size": int(limit or cfg["recruit_leaderboard_size"]),
        "settings": {
            "recruit_per_friend_points": cfg["recruit_per_friend_points"],
            "recruit_buzz_threshold": cfg["recruit_buzz_threshold"],
            "recruit_buzz_bonus_points": cfg["recruit_buzz_bonus_points"],
            "recruit_buzz_badge_label": cfg["recruit_buzz_badge_label"],
            "recruit_buzz_badge_emoji": cfg["recruit_buzz_badge_emoji"],
        },
    }


@router.get("/api/recruit/me")
async def recruit_me(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_recruit_ddl(conn)
        return await get_my_recruit_stats(conn, user["user_id"])


# ───────────────────────────────────────────────────────────────────────
#  Admin — settings CRUD (superadmin only)
# ───────────────────────────────────────────────────────────────────────

class RecruitSettingsUpdate(BaseModel):
    recruit_enabled: Optional[bool] = None
    recruit_per_friend_points: Optional[int] = None
    recruit_buzz_threshold: Optional[int] = None
    recruit_buzz_bonus_points: Optional[int] = None
    recruit_buzz_badge_label: Optional[str] = None
    recruit_buzz_badge_emoji: Optional[str] = None
    recruit_leaderboard_period_days: Optional[int] = None
    recruit_leaderboard_size: Optional[int] = None


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Accès refusé")
    return user


@router.get("/api/admin/recruit/settings")
async def admin_get_recruit_settings(request: Request):
    await _require_admin(request)
    cfg = await get_recruit_settings()
    return {"settings": cfg, "defaults": RECRUIT_DEFAULTS}


@router.put("/api/admin/recruit/settings")
async def admin_update_recruit_settings(req: RecruitSettingsUpdate, request: Request):
    admin = await _require_admin(request)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"settings": await get_recruit_settings(), "updated_keys": []}
    try:
        cfg = await update_recruit_settings(updates, admin_id=admin.get("user_id", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"settings": cfg, "updated_keys": list(updates.keys())}
