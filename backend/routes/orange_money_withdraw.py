"""
Orange Money Cameroon — Withdrawal (iter235). Strictly additive.
"""
from __future__ import annotations
import logging
import os
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field
from typing import Optional

from routes.auth import get_current_user
from routes.admin import require_admin
from services.mobile_money_common import (
    OM_ALLOWED_COUNTRIES, OM_KEYS, get_pool, get_setting, get_user_country,
    new_id, get_client_ip, get_user_agent, names_match, enforce_rate_limit,
    write_ledger_pending, settle_ledger,
)
from services.email_service import send_email

logger = logging.getLogger(__name__)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@japap.com")
router = APIRouter(prefix="/api", tags=["mobile_money_orange_withdraw"])


async def _om_enabled(conn) -> bool:
    val = (await get_setting(conn, OM_KEYS["enabled"], "true") or "").lower()
    return val in ("1", "true", "yes", "on")


async def _om_withdraw_rate(conn) -> Decimal:
    return Decimal(str(await get_setting(conn, OM_KEYS["withdraw_rate"], "600")))


async def _om_min(conn) -> Decimal:
    return Decimal(str(await get_setting(conn, OM_KEYS["withdraw_min"], "30")))


class _QuoteIn(BaseModel):
    montant_usd: Decimal = Field(..., gt=0)


# iter237g — Actionable French gate for OM withdrawal restrictions.
_OM_W_403 = ("Le retrait Orange Money est réservé aux comptes Cameroun "
             "avec un numéro +237 vérifié. Utilise USDT pour retirer "
             "depuis un autre pays.")


@router.post("/withdrawals/orange-money/quote")
async def om_w_quote(req: _QuoteIn, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if (await get_user_country(conn, user["user_id"])) not in OM_ALLOWED_COUNTRIES:
            raise HTTPException(status_code=403, detail=_OM_W_403)
        if not await _om_enabled(conn):
            raise HTTPException(status_code=403, detail="Méthode désactivée.")
        rate = await _om_withdraw_rate(conn)
        minimum = await _om_min(conn)
    montant_xaf = (req.montant_usd * rate).quantize(Decimal("1"))
    return {"montant_usd": float(req.montant_usd), "montant_xaf": float(montant_xaf),
            "min_usd": float(minimum)}


class _SubmitIn(BaseModel):
    montant_usd: Decimal = Field(..., gt=0)
    numero_om: str = Field(..., min_length=8, max_length=20)
    nom_titulaire: str = Field(..., min_length=2, max_length=120)


@router.post("/withdrawals/orange-money/submit")
async def om_w_submit(req: _SubmitIn, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        country = await get_user_country(conn, user["user_id"])
        if country not in OM_ALLOWED_COUNTRIES:
            raise HTTPException(status_code=403, detail=_OM_W_403)
        if not await _om_enabled(conn):
            raise HTTPException(status_code=403, detail="Méthode désactivée.")

        u = await conn.fetchrow(
            "SELECT first_name, last_name, username, phone_number FROM users WHERE user_id=$1",
            user["user_id"],
        )
        if not u or not (u["phone_number"] or "").startswith("+237"):
            raise HTTPException(status_code=403, detail=_OM_W_403)
        full_name = " ".join(filter(None, [u["first_name"], u["last_name"]])) or u["username"] or ""
        if not names_match(full_name, req.nom_titulaire):
            raise HTTPException(
                status_code=400,
                detail="Le nom du titulaire ne correspond pas à votre compte.",
            )

        await enforce_rate_limit(
            conn, table="orange_money_withdrawals", user_id=user["user_id"],
            max_count=3, window_seconds=24 * 3600,
        )
        # Block second concurrent pending withdrawal.
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM orange_money_withdrawals "
            "WHERE user_id=$1 AND statut='PENDING'",
            user["user_id"],
        )
        if int(pending or 0) > 0:
            raise HTTPException(status_code=409,
                detail="Un retrait Orange Money est déjà en attente.")

        rate = await _om_withdraw_rate(conn)
        minimum = await _om_min(conn)
        if req.montant_usd < minimum:
            raise HTTPException(status_code=400,
                detail=f"Montant minimum : {minimum} USD.")

        async with conn.transaction():
            w = await conn.fetchrow(
                "SELECT balance, currency, is_locked FROM wallets WHERE user_id=$1 FOR UPDATE",
                user["user_id"],
            )
            if not w:
                raise HTTPException(status_code=404, detail="Portefeuille introuvable.")
            if w["is_locked"]:
                raise HTTPException(status_code=403, detail="Portefeuille verrouillé.")
            if (w["currency"] or "USD").upper() != "USD":
                raise HTTPException(status_code=400, detail="Portefeuille non USD.")
            solde_avant = Decimal(str(w["balance"]))
            if solde_avant < req.montant_usd:
                gap = (req.montant_usd - solde_avant).quantize(Decimal("0.01"))
                raise HTTPException(status_code=402,
                    detail=f"Solde insuffisant (manque {gap} USD).")
            solde_apres = solde_avant - req.montant_usd
            await conn.execute(
                "UPDATE wallets SET balance=$1, updated_at=NOW() WHERE user_id=$2",
                solde_apres, user["user_id"],
            )
            wid = new_id("omw")
            montant_xaf = (req.montant_usd * rate).quantize(Decimal("1"))
            await conn.execute(
                """INSERT INTO orange_money_withdrawals
                     (id, user_id, montant_usd, montant_xaf, taux_applique,
                      numero_om, nom_titulaire, statut, solde_avant, solde_apres,
                      ip_address, user_agent)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, 'PENDING', $8, $9, $10, $11)""",
                wid, user["user_id"], req.montant_usd, montant_xaf, rate,
                req.numero_om.strip(), req.nom_titulaire.strip(),
                solde_avant, solde_apres,
                get_client_ip(request), get_user_agent(request),
            )
            await write_ledger_pending(
                conn, user_id=user["user_id"], amount_usd=req.montant_usd,
                tx_type="om_withdrawal", reference=wid,
                notes=f"Retrait Orange Money vers {req.numero_om} — en attente",
                outgoing=True,
            )
    try:
        # iter237d — Audit OM : afficher montant FCFA + taux dans l'email admin
        # pour comparer avec ce qui sera envoyé sur le téléphone destinataire.
        # Ces champs ne quittent jamais la sphère admin (cf. emails utilisateur).
        await send_email(
            to=ADMIN_EMAIL,
            subject=f"[OM] Nouveau retrait à traiter — {req.montant_usd} USD",
            html=(f"<p>Nouveau retrait Orange Money en attente.</p>"
                  f"<ul><li>User: <code>{user['user_id']}</code> ({full_name})</li>"
                  f"<li>Montant à débiter: <strong>{req.montant_usd} USD</strong></li>"
                  f"<li>Montant à envoyer: <strong>{int(montant_xaf):,} XAF</strong></li>"
                  f"<li>Taux appliqué: <strong>{rate} XAF/USD</strong> (configurable via Admin → Paiements)</li>"
                  f"<li>Numéro OM destinataire: {req.numero_om}</li>"
                  f"<li>Titulaire: {req.nom_titulaire}</li>"
                  f"<li>Solde avant / après: {solde_avant} → {solde_apres} USD</li></ul>"),
        )
    except Exception as e:
        logger.warning("[om-withdraw] admin email failed: %s", e)
    return {"success": True, "id": wid, "statut": "PENDING"}


@router.get("/withdrawals/orange-money/my")
async def om_w_my(request: Request, page: int = Query(default=1, ge=1),
                   limit: int = Query(default=20, ge=1, le=100)):
    user = await get_current_user(request)
    offset = (page - 1) * limit
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, montant_usd, numero_om, nom_titulaire, statut,
                      motif_rejet, created_at, processed_at
                 FROM orange_money_withdrawals
                WHERE user_id = $1
             ORDER BY created_at DESC
                LIMIT $2 OFFSET $3""",
            user["user_id"], limit, offset,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM orange_money_withdrawals WHERE user_id=$1",
            user["user_id"],
        )
    return {
        "total": int(total or 0),
        "items": [
            {
                "id":            r["id"],
                "montant_usd":   float(r["montant_usd"]),
                "numero_om":     r["numero_om"],
                "nom_titulaire": r["nom_titulaire"],
                "statut":        r["statut"],
                "motif_rejet":   r["motif_rejet"],
                "created_at":    r["created_at"].isoformat() if r["created_at"] else None,
                "processed_at":  r["processed_at"].isoformat() if r["processed_at"] else None,
            }
            for r in rows
        ],
    }


# -- Admin -------------------------------------------------------------------
@router.get("/admin/orange-money/withdrawals")
async def admin_om_w_list(request: Request,
                           status: Optional[str] = Query(default=None),
                           page: int = Query(default=1, ge=1),
                           limit: int = Query(default=20, ge=1, le=100)):
    await require_admin(request)
    where = ""
    params: list = []
    if status and status.upper() in ("PENDING", "SENT", "REJECTED"):
        where = "WHERE statut = $1"
        params.append(status.upper())
    offset = (page - 1) * limit
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM orange_money_withdrawals {where}", *params,
        )
        rows = await conn.fetch(
            f"""SELECT w.*, u.username, u.email
                  FROM orange_money_withdrawals w
                  LEFT JOIN users u ON u.user_id = w.user_id
                  {where}
                 ORDER BY w.created_at DESC
                 LIMIT {limit} OFFSET {offset}""",
            *params,
        )
    return {
        "total": int(total or 0),
        "withdrawals": [
            {
                "id":            r["id"],
                "user_id":       r["user_id"],
                "username":      r["username"],
                "email":         r["email"],
                "montant_usd":   float(r["montant_usd"]),
                "montant_xaf":   float(r["montant_xaf"]),
                "taux_applique": float(r["taux_applique"]),
                "numero_om":     r["numero_om"],
                "nom_titulaire": r["nom_titulaire"],
                "statut":        r["statut"],
                "motif_rejet":   r["motif_rejet"],
                "solde_avant":   float(r["solde_avant"]),
                "solde_apres":   float(r["solde_apres"]),
                "processed_by":  r["processed_by"],
                "processed_at":  r["processed_at"].isoformat() if r["processed_at"] else None,
                "created_at":    r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


@router.patch("/admin/orange-money/withdrawals/{wid}/sent")
async def admin_om_w_sent(wid: str, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(
                "SELECT * FROM orange_money_withdrawals WHERE id=$1 FOR UPDATE", wid,
            )
            if not r:
                raise HTTPException(status_code=404, detail="Retrait introuvable.")
            if r["statut"] != "PENDING":
                raise HTTPException(status_code=409, detail="Déjà traité.")
            await conn.execute(
                "UPDATE orange_money_withdrawals SET statut='SENT', "
                "processed_by=$1, processed_at=NOW() WHERE id=$2",
                admin["user_id"], wid,
            )
            await conn.execute(
                "UPDATE transactions SET status='completed' "
                "WHERE reference=$1 AND type='om_withdrawal' AND status='pending'",
                wid,
            )
            email = await conn.fetchval("SELECT email FROM users WHERE user_id=$1", r["user_id"])
    if email:
        try:
            await send_email(
                to=email,
                subject=f"Retrait Orange Money envoyé — {r['montant_usd']} USD",
                html=(f"<p>Bonjour,</p><p>Ton retrait Orange Money de "
                      f"<strong>{r['montant_usd']} USD</strong> a été envoyé "
                      f"sur le numéro {r['numero_om']}.</p>"),
            )
        except Exception as e:
            logger.warning("[om-w-sent] email failed: %s", e)
    return {"success": True}


class _RejectIn(BaseModel):
    motif: str = Field(..., min_length=2, max_length=500)


@router.patch("/admin/orange-money/withdrawals/{wid}/reject")
async def admin_om_w_reject(wid: str, req: _RejectIn, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(
                "SELECT * FROM orange_money_withdrawals WHERE id=$1 FOR UPDATE", wid,
            )
            if not r:
                raise HTTPException(status_code=404, detail="Retrait introuvable.")
            if r["statut"] != "PENDING":
                raise HTTPException(status_code=409, detail="Déjà traité.")
            # AUTO RECREDIT
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at=NOW() WHERE user_id=$2",
                Decimal(str(r["montant_usd"])), r["user_id"],
            )
            await conn.execute(
                "UPDATE orange_money_withdrawals SET statut='REJECTED', motif_rejet=$1, "
                "processed_by=$2, processed_at=NOW() WHERE id=$3",
                req.motif[:500], admin["user_id"], wid,
            )
            await conn.execute(
                "UPDATE transactions SET status='rejected', "
                "notes=COALESCE(notes,'')||' — Recrédit auto: '||$1 "
                "WHERE reference=$2 AND type='om_withdrawal' AND status='pending'",
                req.motif[:200], wid,
            )
            email = await conn.fetchval("SELECT email FROM users WHERE user_id=$1", r["user_id"])
    if email:
        try:
            await send_email(
                to=email,
                subject="Retrait Orange Money rejeté — Recrédit auto",
                html=(f"<p>Bonjour,</p><p>Ton retrait Orange Money de "
                      f"<strong>{r['montant_usd']} USD</strong> a été rejeté.</p>"
                      f"<p><strong>Motif :</strong> {req.motif}</p>"
                      f"<p>Le montant a été automatiquement recrédité sur ton wallet.</p>"),
            )
        except Exception as e:
            logger.warning("[om-w-reject] email failed: %s", e)
    return {"success": True}


orange_money_withdraw_router = router
