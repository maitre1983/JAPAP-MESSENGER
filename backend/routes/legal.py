"""
iter237n — Routes légales (CGU + CGJ + RGPD).

Strictement additif. Le acceptance tracker est une garantie de preuve : la
Société peut produire à tout moment l'horodatage exact d'acceptation des
documents par chaque utilisateur.

Endpoints (préfixe /api/legal) :
  GET  /status         → { cgu_accepted_at, cgje_accepted_at, privacy_accepted_at }
  POST /accept-cgu     → idempotent (sets timestamp once)
  POST /accept-cgje    → idempotent (PRÉ-REQUIS au paid daily challenge)
  POST /accept-privacy → idempotent
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request

from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/legal", tags=["legal"])


_ddl_done = False
_DDL = [
    # iter237n — Acceptance tracking. NULL = not yet accepted.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS cgu_accepted_at TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS cgje_accepted_at TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS privacy_accepted_at TIMESTAMPTZ",
    # iter237n — Backfill : users who explicitly accepted T&C at registration
    # (`terms_accepted_at`) implicitly accepted both CGU + Privacy.
    "UPDATE users SET cgu_accepted_at = COALESCE(cgu_accepted_at, terms_accepted_at) "
    "WHERE terms_accepted_at IS NOT NULL AND cgu_accepted_at IS NULL",
    "UPDATE users SET privacy_accepted_at = COALESCE(privacy_accepted_at, terms_accepted_at) "
    "WHERE terms_accepted_at IS NOT NULL AND privacy_accepted_at IS NULL",
]


async def _ensure_ddl(conn) -> None:
    global _ddl_done
    if _ddl_done:
        return
    for s in _DDL:
        await conn.execute(s)
    _ddl_done = True


async def _row(conn, uid: str):
    await _ensure_ddl(conn)
    return await conn.fetchrow(
        "SELECT cgu_accepted_at, cgje_accepted_at, privacy_accepted_at "
        "FROM users WHERE user_id = $1",
        uid,
    )


def _iso(v):
    return v.isoformat() if v else None


@router.get("/status")
async def legal_status(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await _row(conn, user["user_id"])
        if not r:
            raise HTTPException(404, "User not found")
    return {
        "cgu_accepted_at":     _iso(r["cgu_accepted_at"]),
        "cgje_accepted_at":    _iso(r["cgje_accepted_at"]),
        "privacy_accepted_at": _iso(r["privacy_accepted_at"]),
        "cgu_accepted":        r["cgu_accepted_at"] is not None,
        "cgje_accepted":       r["cgje_accepted_at"] is not None,
        "privacy_accepted":    r["privacy_accepted_at"] is not None,
    }


async def _accept_field(uid: str, column: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_ddl(conn)
        await conn.execute(
            f"UPDATE users SET {column} = COALESCE({column}, NOW()) "
            "WHERE user_id = $1",
            uid,
        )
        ts = await conn.fetchval(
            f"SELECT {column} FROM users WHERE user_id = $1", uid,
        )
    return {"accepted_at": _iso(ts), "field": column}


@router.post("/accept-cgu")
async def accept_cgu(request: Request):
    user = await get_current_user(request)
    return await _accept_field(user["user_id"], "cgu_accepted_at")


@router.post("/accept-cgje")
async def accept_cgje(request: Request):
    user = await get_current_user(request)
    return await _accept_field(user["user_id"], "cgje_accepted_at")


@router.post("/accept-privacy")
async def accept_privacy(request: Request):
    user = await get_current_user(request)
    return await _accept_field(user["user_id"], "privacy_accepted_at")


legal_router = router
