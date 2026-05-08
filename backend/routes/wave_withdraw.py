"""Wave West Africa — Withdrawal (iter235). Strictly additive."""
from __future__ import annotations
import json
import logging
import os
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from routes.admin import require_admin
from services.mobile_money_common import (
    WAVE_ALLOWED_COUNTRIES_DEFAULT, WAVE_KEYS,
    get_pool, get_setting, get_user_country, new_id,
    get_client_ip, get_user_agent, names_match, enforce_rate_limit,
    write_ledger_pending,
)
from services.email_service import send_email

logger = logging.getLogger(__name__)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@japap.com")
router = APIRouter(prefix="/api", tags=["mobile_money_wave_withdraw"])


async def _wave_enabled(conn) -> bool:
    return (await get_setting(conn, WAVE_KEYS["enabled"], "true") or "").lower() in ("1","true","yes","on")


async def _wave_allowed(conn) -> list[str]:
    raw = await get_setting(conn, WAVE_KEYS["allowed_countries"],
                            json.dumps(WAVE_ALLOWED_COUNTRIES_DEFAULT))
    try:
        return [c.upper() for c in json.loads(raw or "[]") if isinstance(c, str)]
    except Exception:
        return list(WAVE_ALLOWED_COUNTRIES_DEFAULT)


async def _gate(conn, user_id: str) -> None:
    country = await get_user_country(conn, user_id)
    if country not in await _wave_allowed(conn):
        # iter237g — actionable French message.
        raise HTTPException(
            status_code=403,
            detail=("Le retrait Wave est disponible au Burkina Faso, "
                    "Côte d'Ivoire, Mali, Niger, Sénégal, Gambie et "
                    "Ouganda uniquement. Utilise USDT pour retirer "
                    "depuis un autre pays."),
        )
    if not await _wave_enabled(conn):
        raise HTTPException(status_code=403, detail="Méthode désactivée.")


class _QuoteIn(BaseModel):
    montant_usd: Decimal = Field(..., gt=0)


@router.post("/withdrawals/wave/quote")
async def wave_w_quote(req: _QuoteIn, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _gate(conn, user["user_id"])
        rate = Decimal(str(await get_setting(conn, WAVE_KEYS["withdraw_rate"], "600")))
        minimum = Decimal(str(await get_setting(conn, WAVE_KEYS["withdraw_min"], "30")))
    montant_xof = (req.montant_usd * rate).quantize(Decimal("1"))
    return {"montant_usd": float(req.montant_usd), "montant_xof": float(montant_xof),
            "min_usd": float(minimum)}


class _SubmitIn(BaseModel):
    montant_usd: Decimal = Field(..., gt=0)
    numero_wave: str = Field(..., min_length=8, max_length=20)
    nom_titulaire: str = Field(..., min_length=2, max_length=120)


@router.post("/withdrawals/wave/submit")
async def wave_w_submit(req: _SubmitIn, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _gate(conn, user["user_id"])
        u = await conn.fetchrow(
            "SELECT first_name, last_name, username FROM users WHERE user_id=$1",
            user["user_id"],
        )
        full_name = " ".join(filter(None, [u["first_name"] if u else "",
                                            u["last_name"] if u else ""])) \
                    or (u["username"] if u else "")
        if not names_match(full_name, req.nom_titulaire):
            raise HTTPException(status_code=400,
                detail="Le nom du titulaire ne correspond pas à votre compte.")
        await enforce_rate_limit(conn, table="wave_withdrawals", user_id=user["user_id"],
                                  max_count=3, window_seconds=24 * 3600)
        if int(await conn.fetchval(
                "SELECT COUNT(*) FROM wave_withdrawals WHERE user_id=$1 AND statut='PENDING'",
                user["user_id"],
        ) or 0) > 0:
            raise HTTPException(status_code=409, detail="Un retrait Wave est déjà en attente.")
        rate = Decimal(str(await get_setting(conn, WAVE_KEYS["withdraw_rate"], "600")))
        minimum = Decimal(str(await get_setting(conn, WAVE_KEYS["withdraw_min"], "30")))
        if req.montant_usd < minimum:
            raise HTTPException(status_code=400, detail=f"Montant minimum : {minimum} USD.")
        async with conn.transaction():
            w = await conn.fetchrow(
                "SELECT balance, currency, is_locked FROM wallets WHERE user_id=$1 FOR UPDATE",
                user["user_id"],
            )
            if not w: raise HTTPException(404, "Portefeuille introuvable.")
            if w["is_locked"]: raise HTTPException(403, "Portefeuille verrouillé.")
            solde_avant = Decimal(str(w["balance"]))
            if solde_avant < req.montant_usd:
                gap = (req.montant_usd - solde_avant).quantize(Decimal("0.01"))
                raise HTTPException(402, f"Solde insuffisant (manque {gap} USD).")
            solde_apres = solde_avant - req.montant_usd
            await conn.execute(
                "UPDATE wallets SET balance=$1, updated_at=NOW() WHERE user_id=$2",
                solde_apres, user["user_id"],
            )
            wid = new_id("wvw")
            montant_xof = (req.montant_usd * rate).quantize(Decimal("1"))
            await conn.execute(
                """INSERT INTO wave_withdrawals
                     (id, user_id, montant_usd, montant_xof, taux_applique,
                      numero_wave, nom_titulaire, statut, solde_avant, solde_apres,
                      ip_address, user_agent)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,'PENDING',$8,$9,$10,$11)""",
                wid, user["user_id"], req.montant_usd, montant_xof, rate,
                req.numero_wave.strip(), req.nom_titulaire.strip(),
                solde_avant, solde_apres,
                get_client_ip(request), get_user_agent(request),
            )
            await write_ledger_pending(
                conn, user_id=user["user_id"], amount_usd=req.montant_usd,
                tx_type="wave_withdrawal", reference=wid,
                notes=f"Retrait Wave vers {req.numero_wave} — en attente",
                outgoing=True,
            )
    try:
        await send_email(
            to=ADMIN_EMAIL,
            subject=f"[Wave] Nouveau retrait à traiter — {req.montant_usd} USD",
            html=(f"<p>Retrait Wave en attente.</p>"
                  f"<ul><li>User: {user['user_id']} ({full_name})</li>"
                  f"<li>Montant: {req.montant_usd} USD</li>"
                  f"<li>Numéro Wave: {req.numero_wave}</li>"
                  f"<li>Titulaire: {req.nom_titulaire}</li></ul>"),
        )
    except Exception as e:
        logger.warning("[wave-w] admin email failed: %s", e)
    return {"success": True, "id": wid, "statut": "PENDING"}


@router.get("/withdrawals/wave/my")
async def wave_w_my(request: Request, page: int = Query(default=1, ge=1),
                     limit: int = Query(default=20, ge=1, le=100)):
    user = await get_current_user(request)
    offset = (page - 1) * limit
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, montant_usd, numero_wave, nom_titulaire, statut, motif_rejet,
                      created_at, processed_at
                 FROM wave_withdrawals
                WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
            user["user_id"], limit, offset,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM wave_withdrawals WHERE user_id=$1", user["user_id"],
        )
    return {
        "total": int(total or 0),
        "items": [
            {"id": r["id"], "montant_usd": float(r["montant_usd"]),
             "numero_wave": r["numero_wave"], "nom_titulaire": r["nom_titulaire"],
             "statut": r["statut"], "motif_rejet": r["motif_rejet"],
             "created_at": r["created_at"].isoformat() if r["created_at"] else None,
             "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None}
            for r in rows
        ],
    }


# ---------- Admin ----------
@router.get("/admin/wave/withdrawals")
async def admin_wave_w_list(request: Request,
                             status: Optional[str] = Query(default=None),
                             page: int = Query(default=1, ge=1),
                             limit: int = Query(default=20, ge=1, le=100)):
    await require_admin(request)
    where = ""
    params: list = []
    if status and status.upper() in ("PENDING", "SENT", "REJECTED"):
        where = "WHERE statut=$1"; params.append(status.upper())
    offset = (page - 1) * limit
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM wave_withdrawals {where}", *params)
        rows = await conn.fetch(
            f"""SELECT w.*, u.username, u.email
                  FROM wave_withdrawals w LEFT JOIN users u ON u.user_id=w.user_id
                  {where} ORDER BY w.created_at DESC LIMIT {limit} OFFSET {offset}""",
            *params,
        )
    return {"total": int(total or 0),
            "withdrawals": [
                {**{k: r[k] for k in
                    ("id","user_id","username","email","numero_wave","nom_titulaire",
                     "statut","motif_rejet","processed_by")},
                 "montant_usd": float(r["montant_usd"]),
                 "montant_xof": float(r["montant_xof"]),
                 "taux_applique": float(r["taux_applique"]),
                 "solde_avant": float(r["solde_avant"]),
                 "solde_apres": float(r["solde_apres"]),
                 "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
                 "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ]}


@router.patch("/admin/wave/withdrawals/{wid}/sent")
async def admin_wave_w_sent(wid: str, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow("SELECT * FROM wave_withdrawals WHERE id=$1 FOR UPDATE", wid)
            if not r: raise HTTPException(404, "Retrait introuvable.")
            if r["statut"] != "PENDING": raise HTTPException(409, "Déjà traité.")
            await conn.execute(
                "UPDATE wave_withdrawals SET statut='SENT', processed_by=$1, processed_at=NOW() WHERE id=$2",
                admin["user_id"], wid,
            )
            await conn.execute(
                "UPDATE transactions SET status='completed' "
                "WHERE reference=$1 AND type='wave_withdrawal' AND status='pending'",
                wid,
            )
            email = await conn.fetchval("SELECT email FROM users WHERE user_id=$1", r["user_id"])
    if email:
        try:
            await send_email(to=email, subject=f"Retrait Wave envoyé — {r['montant_usd']} USD",
                html=f"<p>Ton retrait Wave de <strong>{r['montant_usd']} USD</strong> a été envoyé sur le numéro {r['numero_wave']}.</p>")
        except Exception as e:
            logger.warning("[wave-w-sent] email failed: %s", e)
    return {"success": True}


class _RejectIn(BaseModel):
    motif: str = Field(..., min_length=2, max_length=500)


@router.patch("/admin/wave/withdrawals/{wid}/reject")
async def admin_wave_w_reject(wid: str, req: _RejectIn, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow("SELECT * FROM wave_withdrawals WHERE id=$1 FOR UPDATE", wid)
            if not r: raise HTTPException(404, "Retrait introuvable.")
            if r["statut"] != "PENDING": raise HTTPException(409, "Déjà traité.")
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at=NOW() WHERE user_id=$2",
                Decimal(str(r["montant_usd"])), r["user_id"],
            )
            await conn.execute(
                "UPDATE wave_withdrawals SET statut='REJECTED', motif_rejet=$1, "
                "processed_by=$2, processed_at=NOW() WHERE id=$3",
                req.motif[:500], admin["user_id"], wid,
            )
            await conn.execute(
                "UPDATE transactions SET status='rejected', "
                "notes=COALESCE(notes,'')||' — Recrédit auto: '||$1 "
                "WHERE reference=$2 AND type='wave_withdrawal' AND status='pending'",
                req.motif[:200], wid,
            )
            email = await conn.fetchval("SELECT email FROM users WHERE user_id=$1", r["user_id"])
    if email:
        try:
            await send_email(to=email, subject="Retrait Wave rejeté — Recrédit auto",
                html=(f"<p>Ton retrait Wave a été rejeté.</p><p>Motif : {req.motif}</p>"
                      f"<p>Le montant <strong>{r['montant_usd']} USD</strong> a été recrédité sur ton wallet.</p>"))
        except Exception as e:
            logger.warning("[wave-w-reject] email failed: %s", e)
    return {"success": True}


wave_withdraw_router = router
