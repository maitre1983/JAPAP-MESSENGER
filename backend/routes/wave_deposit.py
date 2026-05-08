"""Wave West Africa — Deposit (iter235). Strictly additive."""
from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime, time as dtime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from routes.admin import require_admin
from services.mobile_money_common import (
    WAVE_ALLOWED_COUNTRIES_DEFAULT, WAVE_KEYS, WAVE_REF_PATTERN,
    get_pool, get_setting, get_user_country, new_id,
    get_client_ip, get_user_agent, enforce_rate_limit, write_ledger_pending,
)
from services.email_service import send_email

logger = logging.getLogger(__name__)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@japap.com")
router = APIRouter(prefix="/api", tags=["mobile_money_wave_deposit"])


async def _wave_enabled(conn) -> bool:
    return (await get_setting(conn, WAVE_KEYS["enabled"], "true") or "").lower() in ("1", "true", "yes", "on")


async def _wave_rate(conn) -> Decimal:
    return Decimal(str(await get_setting(conn, WAVE_KEYS["deposit_rate"], "605")))


async def _wave_allowed(conn) -> list[str]:
    raw = await get_setting(conn, WAVE_KEYS["allowed_countries"],
                            json.dumps(WAVE_ALLOWED_COUNTRIES_DEFAULT))
    try:
        v = json.loads(raw or "[]")
        return [c.upper() for c in v if isinstance(c, str)]
    except Exception:
        return list(WAVE_ALLOWED_COUNTRIES_DEFAULT)


async def _gate(conn, user_id: str) -> None:
    country = await get_user_country(conn, user_id)
    allowed = await _wave_allowed(conn)
    if country not in allowed:
        # iter237g — actionable French message listing alternatives.
        raise HTTPException(
            status_code=403,
            detail=("Wave est disponible au Burkina Faso, Côte d'Ivoire, "
                    "Mali, Niger, Sénégal, Gambie et Ouganda uniquement. "
                    "Utilise USDT (TRC20/BSC) ou Orange Money (CM) pour ton pays."),
        )
    if not await _wave_enabled(conn):
        raise HTTPException(status_code=403, detail="Méthode désactivée.")
    receiver = await get_setting(conn, WAVE_KEYS["receiver_num"], "")
    if not receiver:
        raise HTTPException(status_code=503, detail="Méthode non configurée.")


@router.get("/deposits/wave/info")
async def wave_dep_info(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _gate(conn, user["user_id"])
        return {
            "receiver_name":   await get_setting(conn, WAVE_KEYS["receiver_name"], "JAPAP Wave"),
            "receiver_number": await get_setting(conn, WAVE_KEYS["receiver_num"], ""),
            "ref_pattern_hint": "Ex: T_ABC123-XYZ789 (Sénégal) ou xot-24p35p8qg22d0 (CI). Le format varie selon ton pays.",
        }


class _QuoteIn(BaseModel):
    montant_usd: Decimal = Field(..., gt=0, le=Decimal("100000"))


@router.post("/deposits/wave/quote")
async def wave_dep_quote(req: _QuoteIn, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _gate(conn, user["user_id"])
        rate = await _wave_rate(conn)
    montant_xof = (req.montant_usd * rate).quantize(Decimal("1"))
    return {"montant_usd": float(req.montant_usd), "montant_xof": float(montant_xof)}


class _SubmitIn(BaseModel):
    montant_usd: Decimal = Field(..., gt=0, le=Decimal("100000"))
    numero_expediteur: str = Field(..., min_length=8, max_length=20)
    nom_expediteur: str = Field(..., min_length=2, max_length=120)
    date_tx: str
    heure_tx: str
    reference: str = Field(..., min_length=4, max_length=120)


@router.post("/deposits/wave/submit")
async def wave_dep_submit(req: _SubmitIn, request: Request):
    user = await get_current_user(request)
    # iter237z — Preserve the user's original casing (Wave Côte d'Ivoire IDs
    # are lowercase, e.g. xot-24p35p8qg22d0). Only trim + length-check.
    reference = req.reference.strip()[:120]
    if len(reference) < 6 or not WAVE_REF_PATTERN.match(reference):
        raise HTTPException(status_code=400,
            detail="Référence Wave trop courte ou invalide (min. 6 caractères, "
                   "lettres/chiffres/tirets/underscores).")
    try:
        date_tx = datetime.strptime(req.date_tx[:10], "%Y-%m-%d").date()
        hh, mm = req.heure_tx[:5].split(":")
        heure_tx = dtime(int(hh), int(mm))
    except Exception as e:
        raise HTTPException(status_code=400, detail="Date / heure invalides.") from e

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _gate(conn, user["user_id"])
        await enforce_rate_limit(
            conn, table="wave_deposits", user_id=user["user_id"],
            max_count=3, window_seconds=3600,
        )
        if await conn.fetchval(
                "SELECT id FROM wave_deposits WHERE reference=$1", reference):
            raise HTTPException(status_code=400, detail="Référence déjà utilisée.")
        rate = await _wave_rate(conn)
        montant_xof = (req.montant_usd * rate).quantize(Decimal("1"))
        dep_id = new_id("wvd")
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO wave_deposits (id, user_id, montant_usd, montant_xof,
                       taux_applique, numero_exp, nom_exp, date_tx, heure_tx, reference,
                       statut, ip_address, user_agent)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'PENDING',$11,$12)""",
                dep_id, user["user_id"], req.montant_usd, montant_xof, rate,
                req.numero_expediteur.strip(), req.nom_expediteur.strip(),
                date_tx, heure_tx, reference,
                get_client_ip(request), get_user_agent(request),
            )
            await write_ledger_pending(
                conn, user_id=user["user_id"], amount_usd=req.montant_usd,
                tx_type="wave_deposit", reference=dep_id,
                notes=f"Dépôt Wave (référence {reference}) — en attente",
                outgoing=False,
            )
    try:
        await send_email(
            to=ADMIN_EMAIL,
            subject=f"[Wave] Nouveau dépôt à vérifier — {req.montant_usd} USD",
            html=(f"<p>Nouveau dépôt Wave en attente.</p>"
                  f"<ul><li>User: {user['user_id']}</li>"
                  f"<li>Montant: {req.montant_usd} USD</li>"
                  f"<li>Numéro: {req.numero_expediteur}</li>"
                  f"<li>Nom: {req.nom_expediteur}</li>"
                  f"<li>Référence: {reference}</li></ul>"),
        )
    except Exception as e:
        logger.warning("[wave-dep] admin email failed: %s", e)
    return {"success": True, "id": dep_id, "statut": "PENDING"}


# ---------- Admin -----------------------------------------------------------
@router.get("/admin/wave/deposits")
async def admin_wave_dep_list(request: Request,
                               status: Optional[str] = Query(default=None),
                               page: int = Query(default=1, ge=1),
                               limit: int = Query(default=20, ge=1, le=100)):
    await require_admin(request)
    where = ""
    params: list = []
    if status and status.upper() in ("PENDING", "VERIFIED", "REJECTED"):
        where = "WHERE statut = $1"
        params.append(status.upper())
    offset = (page - 1) * limit
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM wave_deposits {where}", *params)
        rows = await conn.fetch(
            f"""SELECT d.*, u.username, u.email
                  FROM wave_deposits d LEFT JOIN users u ON u.user_id=d.user_id
                  {where} ORDER BY d.created_at DESC LIMIT {limit} OFFSET {offset}""",
            *params,
        )
    return {
        "total": int(total or 0),
        "deposits": [
            {**{k: r[k] for k in
                ("id", "user_id", "username", "email", "numero_exp", "nom_exp",
                 "reference", "statut", "motif_rejet")},
             "montant_usd": float(r["montant_usd"]),
             "montant_xof": float(r["montant_xof"]),
             "taux_applique": float(r["taux_applique"]),
             "date_tx": r["date_tx"].isoformat() if r["date_tx"] else None,
             "heure_tx": r["heure_tx"].isoformat() if r["heure_tx"] else None,
             "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


@router.patch("/admin/wave/deposits/{dep_id}/verify")
async def admin_wave_dep_verify(dep_id: str, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            d = await conn.fetchrow("SELECT * FROM wave_deposits WHERE id=$1 FOR UPDATE", dep_id)
            if not d: raise HTTPException(404, "Dépôt introuvable.")
            if d["statut"] != "PENDING": raise HTTPException(409, "Déjà traité.")
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at=NOW() WHERE user_id=$2",
                Decimal(str(d["montant_usd"])), d["user_id"],
            )
            await conn.execute(
                "UPDATE wave_deposits SET statut='VERIFIED', verified_by=$1, verified_at=NOW() WHERE id=$2",
                admin["user_id"], dep_id,
            )
            await conn.execute(
                "UPDATE transactions SET status='completed' "
                "WHERE reference=$1 AND type='wave_deposit' AND status='pending'",
                dep_id,
            )
            email = await conn.fetchval("SELECT email FROM users WHERE user_id=$1", d["user_id"])
    if email:
        try:
            await send_email(
                to=email,
                subject=f"Dépôt Wave crédité — {d['montant_usd']} USD",
                html=(f"<p>Ton dépôt Wave a été vérifié et crédité.</p>"
                      f"<p>Montant : <strong>{d['montant_usd']} USD</strong></p>"
                      f"<p>Référence : {d['reference']}</p>"),
            )
        except Exception as e:
            logger.warning("[wave-verify] email failed: %s", e)
    return {"success": True}


class _RejectIn(BaseModel):
    motif: str = Field(..., min_length=2, max_length=500)


@router.patch("/admin/wave/deposits/{dep_id}/reject")
async def admin_wave_dep_reject(dep_id: str, req: _RejectIn, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            d = await conn.fetchrow("SELECT * FROM wave_deposits WHERE id=$1 FOR UPDATE", dep_id)
            if not d: raise HTTPException(404, "Dépôt introuvable.")
            if d["statut"] != "PENDING": raise HTTPException(409, "Déjà traité.")
            await conn.execute(
                "UPDATE wave_deposits SET statut='REJECTED', motif_rejet=$1, "
                "verified_by=$2, verified_at=NOW() WHERE id=$3",
                req.motif[:500], admin["user_id"], dep_id,
            )
            await conn.execute(
                "UPDATE transactions SET status='rejected', notes=COALESCE(notes,'')||' — '||$1 "
                "WHERE reference=$2 AND type='wave_deposit' AND status='pending'",
                req.motif[:200], dep_id,
            )
            email = await conn.fetchval("SELECT email FROM users WHERE user_id=$1", d["user_id"])
    if email:
        try:
            await send_email(to=email, subject="Dépôt Wave rejeté",
                html=f"<p>Ton dépôt Wave a été rejeté.</p><p>Motif : {req.motif}</p>")
        except Exception as e:
            logger.warning("[wave-reject] email failed: %s", e)
    return {"success": True}


@router.get("/admin/wave/stats")
async def admin_wave_stats(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                  COALESCE(SUM(CASE WHEN created_at::date = CURRENT_DATE THEN montant_usd ELSE 0 END),0) AS day_usd,
                  COALESCE(SUM(CASE WHEN date_trunc('month', created_at) = date_trunc('month', NOW()) THEN montant_usd ELSE 0 END),0) AS month_usd,
                  COALESCE(SUM(CASE WHEN date_trunc('year',  created_at) = date_trunc('year',  NOW()) THEN montant_usd ELSE 0 END),0) AS year_usd,
                  COUNT(*) FILTER (WHERE statut='PENDING') AS pending
                 FROM wave_deposits
                WHERE statut IN ('VERIFIED','PENDING')""",
        )
    return {"day_usd": float(row["day_usd"] or 0),
            "month_usd": float(row["month_usd"] or 0),
            "year_usd": float(row["year_usd"] or 0),
            "pending": int(row["pending"] or 0)}


# ---------- Admin settings (rates, receiver, allowed countries, ON/OFF) -----
class _WaveSettings(BaseModel):
    deposit_rate:  Optional[Decimal] = None
    withdraw_rate: Optional[Decimal] = None
    withdraw_min:  Optional[Decimal] = None
    receiver_name: Optional[str]     = None
    receiver_num:  Optional[str]     = None
    allowed_countries: Optional[list[str]] = None
    enabled:       Optional[bool]    = None


@router.get("/admin/wave/settings")
async def admin_wave_settings_get(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {
            "deposit_rate":  float(await get_setting(conn, WAVE_KEYS["deposit_rate"], "605")),
            "withdraw_rate": float(await get_setting(conn, WAVE_KEYS["withdraw_rate"], "600")),
            "withdraw_min":  float(await get_setting(conn, WAVE_KEYS["withdraw_min"], "30")),
            "receiver_name": await get_setting(conn, WAVE_KEYS["receiver_name"], "JAPAP Wave"),
            "receiver_num":  await get_setting(conn, WAVE_KEYS["receiver_num"], ""),
            "allowed_countries": await _wave_allowed(conn),
            "enabled": (await get_setting(conn, WAVE_KEYS["enabled"], "true") or "").lower() in ("1","true","yes","on"),
        }


@router.patch("/admin/wave/settings")
async def admin_wave_settings_set(req: _WaveSettings, request: Request):
    await require_admin(request)
    pool = await get_pool()
    from services.mobile_money_common import set_setting
    async with pool.acquire() as conn:
        if req.deposit_rate is not None:
            await set_setting(conn, WAVE_KEYS["deposit_rate"], str(req.deposit_rate))
        if req.withdraw_rate is not None:
            await set_setting(conn, WAVE_KEYS["withdraw_rate"], str(req.withdraw_rate))
        if req.withdraw_min is not None:
            await set_setting(conn, WAVE_KEYS["withdraw_min"], str(req.withdraw_min))
        if req.receiver_name is not None:
            await set_setting(conn, WAVE_KEYS["receiver_name"], req.receiver_name[:120])
        if req.receiver_num is not None:
            await set_setting(conn, WAVE_KEYS["receiver_num"], req.receiver_num[:32])
        if req.allowed_countries is not None:
            cleaned = [c.upper()[:2] for c in req.allowed_countries if c and len(c) >= 2]
            await set_setting(conn, WAVE_KEYS["allowed_countries"], json.dumps(cleaned))
        if req.enabled is not None:
            await set_setting(conn, WAVE_KEYS["enabled"], "true" if req.enabled else "false")
    return {"success": True}


# Mirror endpoints for OM admin settings (kept here to avoid creating a 5th file)
class _OMSettings(BaseModel):
    deposit_rate:  Optional[Decimal] = None
    withdraw_rate: Optional[Decimal] = None
    withdraw_min:  Optional[Decimal] = None
    receiver_name: Optional[str]     = None
    receiver_num:  Optional[str]     = None
    enabled:       Optional[bool]    = None


@router.get("/admin/orange-money/settings")
async def admin_om_settings_get(request: Request):
    await require_admin(request)
    pool = await get_pool()
    from services.mobile_money_common import OM_KEYS
    async with pool.acquire() as conn:
        return {
            "deposit_rate":  float(await get_setting(conn, OM_KEYS["deposit_rate"], "605")),
            "withdraw_rate": float(await get_setting(conn, OM_KEYS["withdraw_rate"], "600")),
            "withdraw_min":  float(await get_setting(conn, OM_KEYS["withdraw_min"], "30")),
            "receiver_name": await get_setting(conn, OM_KEYS["receiver_name"], "New deal finances"),
            "receiver_num":  await get_setting(conn, OM_KEYS["receiver_num"], ""),
            "enabled": (await get_setting(conn, OM_KEYS["enabled"], "true") or "").lower() in ("1","true","yes","on"),
        }


@router.patch("/admin/orange-money/settings")
async def admin_om_settings_set(req: _OMSettings, request: Request):
    await require_admin(request)
    pool = await get_pool()
    from services.mobile_money_common import OM_KEYS, set_setting
    async with pool.acquire() as conn:
        if req.deposit_rate is not None: await set_setting(conn, OM_KEYS["deposit_rate"], str(req.deposit_rate))
        if req.withdraw_rate is not None: await set_setting(conn, OM_KEYS["withdraw_rate"], str(req.withdraw_rate))
        if req.withdraw_min is not None: await set_setting(conn, OM_KEYS["withdraw_min"], str(req.withdraw_min))
        if req.receiver_name is not None: await set_setting(conn, OM_KEYS["receiver_name"], req.receiver_name[:120])
        if req.receiver_num is not None:  await set_setting(conn, OM_KEYS["receiver_num"], req.receiver_num[:32])
        if req.enabled is not None:       await set_setting(conn, OM_KEYS["enabled"], "true" if req.enabled else "false")
    return {"success": True}


wave_deposit_router = router
