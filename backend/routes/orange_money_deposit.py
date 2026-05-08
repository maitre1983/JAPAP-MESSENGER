"""
Orange Money Cameroon — Deposit (iter235).
==========================================
User-facing & admin endpoints for OM (CM) deposits. Strictly additive.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, time as dtime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from routes.admin import require_admin
from services.mobile_money_common import (
    OM_ALLOWED_COUNTRIES, OM_DEPOSIT_BLOCKED_COUNTRIES, OM_KEYS,
    get_pool, get_setting, get_user_country,
    new_id, get_client_ip, get_user_agent, enforce_rate_limit,
    write_ledger_pending, settle_ledger,
)
from services.email_service import send_email

logger = logging.getLogger(__name__)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@japap.com")
router = APIRouter(prefix="/api", tags=["mobile_money_orange_deposit"])


# -- helpers -----------------------------------------------------------------
async def _om_enabled(conn) -> bool:
    val = (await get_setting(conn, OM_KEYS["enabled"], "true") or "").lower()
    return val in ("1", "true", "yes", "on")


async def _om_deposit_rate(conn) -> Decimal:
    return Decimal(str(await get_setting(conn, OM_KEYS["deposit_rate"], "605")))


async def _om_receiver(conn) -> dict:
    return {
        "name":   await get_setting(conn, OM_KEYS["receiver_name"], "New deal finances"),
        "number": await get_setting(conn, OM_KEYS["receiver_num"], ""),
    }


# iter237g — Centralised, actionable French gate message so the toast on
# the frontend is self-explanatory ("use USDT or card") when an ineligible
# user submits.
_OM_DEP_403 = ("Orange Money est réservé aux numéros Cameroun (+237). "
               "Utilise USDT (TRC20/BSC) ou Carte bancaire pour ton pays.")


# ---------------------------------------------------------------------------
# Public surface — only available to CM users.
# ---------------------------------------------------------------------------
@router.get("/deposits/orange-money/info")
async def om_deposit_info(request: Request):
    """Receiver number + name shown on the deposit form (NEVER the rate)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        country = await get_user_country(conn, user["user_id"])
        if country in OM_DEPOSIT_BLOCKED_COUNTRIES:
            raise HTTPException(status_code=403, detail=_OM_DEP_403)
        if not await _om_enabled(conn):
            raise HTTPException(status_code=403, detail="Méthode désactivée.")
        recv = await _om_receiver(conn)
        if not recv["number"]:
            raise HTTPException(status_code=503, detail="Méthode non configurée.")
        return {"receiver_name": recv["name"], "receiver_number": recv["number"]}


class _QuoteIn(BaseModel):
    montant_usd: Decimal = Field(..., gt=0, le=Decimal("100000"))


@router.post("/deposits/orange-money/quote")
async def om_deposit_quote(req: _QuoteIn, request: Request):
    """Returns the XAF amount the user must send. Rate stays hidden."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        country = await get_user_country(conn, user["user_id"])
        if country in OM_DEPOSIT_BLOCKED_COUNTRIES:
            raise HTTPException(status_code=403, detail=_OM_DEP_403)
        if not await _om_enabled(conn):
            raise HTTPException(status_code=403, detail="Méthode désactivée.")
        rate = await _om_deposit_rate(conn)
    montant_xaf = (req.montant_usd * rate).quantize(Decimal("1"))
    return {"montant_usd": float(req.montant_usd), "montant_xaf": float(montant_xaf)}


class _SubmitIn(BaseModel):
    montant_usd: Decimal = Field(..., gt=0, le=Decimal("100000"))
    numero_expediteur: str = Field(..., min_length=8, max_length=20)
    date_tx: str
    heure_tx: str
    reference: str = Field(..., min_length=4, max_length=120)


@router.post("/deposits/orange-money/submit")
async def om_deposit_submit(req: _SubmitIn, request: Request):
    user = await get_current_user(request)
    # parse & sanitise dates client-side-friendly formats
    try:
        date_tx = datetime.strptime(req.date_tx[:10], "%Y-%m-%d").date()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Date invalide (YYYY-MM-DD).") from e
    try:
        hh, mm = req.heure_tx[:5].split(":")
        heure_tx = dtime(int(hh), int(mm))
    except Exception as e:
        raise HTTPException(status_code=400, detail="Heure invalide (HH:MM).") from e
    numero = req.numero_expediteur.strip()
    reference = req.reference.strip().upper()[:120]

    pool = await get_pool()
    async with pool.acquire() as conn:
        country = await get_user_country(conn, user["user_id"])
        if country in OM_DEPOSIT_BLOCKED_COUNTRIES:
            raise HTTPException(status_code=403, detail=_OM_DEP_403)
        if not await _om_enabled(conn):
            raise HTTPException(status_code=403, detail="Méthode désactivée.")

        await enforce_rate_limit(
            conn, table="orange_money_deposits", user_id=user["user_id"],
            max_count=3, window_seconds=3600,
        )

        # uniqueness
        dup = await conn.fetchval(
            "SELECT id FROM orange_money_deposits WHERE reference = $1", reference,
        )
        if dup:
            raise HTTPException(status_code=400, detail="Cette référence a déjà été utilisée.")

        rate = await _om_deposit_rate(conn)
        montant_xaf = (req.montant_usd * rate).quantize(Decimal("1"))
        dep_id = new_id("omd")

        async with conn.transaction():
            await conn.execute(
                """INSERT INTO orange_money_deposits
                     (id, user_id, montant_usd, montant_xaf, taux_applique,
                      numero_exp, date_tx, heure_tx, reference, statut,
                      ip_address, user_agent)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'PENDING',
                           $10, $11)""",
                dep_id, user["user_id"], req.montant_usd, montant_xaf, rate,
                numero, date_tx, heure_tx, reference,
                get_client_ip(request), get_user_agent(request),
            )
            # User-visible ledger entry (pending — credited on verify).
            await write_ledger_pending(
                conn, user_id=user["user_id"], amount_usd=req.montant_usd,
                tx_type="om_deposit", reference=dep_id,
                notes=f"Dépôt Orange Money (référence {reference}) — en attente de vérification",
                outgoing=False,
            )

    # admin notification (non-blocking failure)
    try:
        # iter237d — Audit OM : l'admin doit voir le montant FCFA et le taux
        # appliqué directement dans l'email pour comparer avec ce qui apparaît
        # sur son compte Orange Money. Ces champs ne sont JAMAIS envoyés à
        # l'utilisateur (cf. emails verify/reject — montant_usd + reference uniquement).
        await send_email(
            to=ADMIN_EMAIL,
            subject=f"[OM] Nouveau dépôt à vérifier — {req.montant_usd} USD",
            html=(f"<p>Nouveau dépôt Orange Money en attente.</p>"
                  f"<ul><li>User: <code>{user['user_id']}</code></li>"
                  f"<li>Montant: <strong>{req.montant_usd} USD</strong></li>"
                  f"<li>Équivalent FCFA attendu: <strong>{int(montant_xaf):,} XAF</strong></li>"
                  f"<li>Taux appliqué: <strong>{rate} XAF/USD</strong> (configurable via Admin → Paiements)</li>"
                  f"<li>Numéro expéditeur: {numero}</li>"
                  f"<li>Référence: {reference}</li>"
                  f"<li>Date / Heure tx: {date_tx} {heure_tx}</li></ul>"
                  f"<p>Voir l'admin → Paiements → Paramètres de paiement.</p>"),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[om-deposit] admin email failed: %s", e)

    return {"success": True, "id": dep_id, "statut": "PENDING"}


# ---------------------------------------------------------------------------
# Admin surface
# ---------------------------------------------------------------------------
@router.get("/admin/orange-money/deposits")
async def admin_om_deposits_list(request: Request,
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
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM orange_money_deposits {where}", *params,
        )
        rows = await conn.fetch(
            f"""SELECT d.*, u.username, u.email
                  FROM orange_money_deposits d
                  LEFT JOIN users u ON u.user_id = d.user_id
                  {where}
                 ORDER BY d.created_at DESC
                 LIMIT {limit} OFFSET {offset}""",
            *params,
        )
    return {
        "total": int(total or 0),
        "deposits": [
            {
                "id":            r["id"],
                "user_id":       r["user_id"],
                "username":      r["username"],
                "email":         r["email"],
                "montant_usd":   float(r["montant_usd"]),
                "montant_xaf":   float(r["montant_xaf"]),
                "taux_applique": float(r["taux_applique"]),
                "numero_exp":    r["numero_exp"],
                "date_tx":       r["date_tx"].isoformat() if r["date_tx"] else None,
                "heure_tx":      r["heure_tx"].isoformat() if r["heure_tx"] else None,
                "reference":     r["reference"],
                "statut":        r["statut"],
                "motif_rejet":   r["motif_rejet"],
                "verified_by":   r["verified_by"],
                "verified_at":   r["verified_at"].isoformat() if r["verified_at"] else None,
                "created_at":    r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


@router.patch("/admin/orange-money/deposits/{dep_id}/verify")
async def admin_om_verify(dep_id: str, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            d = await conn.fetchrow(
                "SELECT * FROM orange_money_deposits WHERE id = $1 FOR UPDATE", dep_id,
            )
            if not d:
                raise HTTPException(status_code=404, detail="Dépôt introuvable.")
            if d["statut"] != "PENDING":
                raise HTTPException(status_code=409, detail="Déjà traité.")
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = NOW() "
                "WHERE user_id = $2",
                Decimal(str(d["montant_usd"])), d["user_id"],
            )
            await conn.execute(
                "UPDATE orange_money_deposits SET statut='VERIFIED', "
                "verified_by=$1, verified_at=NOW() WHERE id=$2",
                admin["user_id"], dep_id,
            )
            # Settle the user-visible ledger row.
            await conn.execute(
                "UPDATE transactions SET status='completed' "
                "WHERE reference=$1 AND type='om_deposit' AND status='pending'",
                dep_id,
            )
            user_email = await conn.fetchval(
                "SELECT email FROM users WHERE user_id=$1", d["user_id"],
            )
    if user_email:
        try:
            await send_email(
                to=user_email,
                subject=f"Dépôt Orange Money crédité — {d['montant_usd']} USD",
                html=(f"<p>Bonjour,</p><p>Ton dépôt Orange Money a été vérifié et "
                      f"crédité sur ton wallet JAPAP.</p>"
                      f"<p><strong>Montant : {d['montant_usd']} USD</strong></p>"
                      f"<p>Référence : {d['reference']}</p>"),
            )
        except Exception as e:
            logger.warning("[om-verify] user email failed: %s", e)
    return {"success": True}


class _RejectIn(BaseModel):
    motif: str = Field(..., min_length=2, max_length=500)


@router.patch("/admin/orange-money/deposits/{dep_id}/reject")
async def admin_om_reject(dep_id: str, req: _RejectIn, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            d = await conn.fetchrow(
                "SELECT * FROM orange_money_deposits WHERE id = $1 FOR UPDATE", dep_id,
            )
            if not d:
                raise HTTPException(status_code=404, detail="Dépôt introuvable.")
            if d["statut"] != "PENDING":
                raise HTTPException(status_code=409, detail="Déjà traité.")
            await conn.execute(
                "UPDATE orange_money_deposits SET statut='REJECTED', motif_rejet=$1, "
                "verified_by=$2, verified_at=NOW() WHERE id=$3",
                req.motif[:500], admin["user_id"], dep_id,
            )
            await conn.execute(
                "UPDATE transactions SET status='rejected', notes=COALESCE(notes,'')||' — '||$1 "
                "WHERE reference=$2 AND type='om_deposit' AND status='pending'",
                req.motif[:200], dep_id,
            )
            user_email = await conn.fetchval(
                "SELECT email FROM users WHERE user_id=$1", d["user_id"],
            )
    if user_email:
        try:
            await send_email(
                to=user_email,
                subject="Dépôt Orange Money rejeté",
                html=(f"<p>Bonjour,</p><p>Ton dépôt Orange Money a été rejeté.</p>"
                      f"<p><strong>Motif :</strong> {req.motif}</p>"
                      f"<p>Référence : {d['reference']}</p>"),
            )
        except Exception as e:
            logger.warning("[om-reject] user email failed: %s", e)
    return {"success": True}


@router.get("/admin/orange-money/stats")
async def admin_om_stats(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                  COALESCE(SUM(CASE WHEN created_at::date = CURRENT_DATE THEN montant_usd ELSE 0 END),0) AS day_usd,
                  COALESCE(SUM(CASE WHEN date_trunc('month', created_at) = date_trunc('month', NOW()) THEN montant_usd ELSE 0 END),0) AS month_usd,
                  COALESCE(SUM(CASE WHEN date_trunc('year', created_at)  = date_trunc('year', NOW())  THEN montant_usd ELSE 0 END),0) AS year_usd,
                  COUNT(*) FILTER (WHERE statut='PENDING') AS pending
                 FROM orange_money_deposits
                WHERE statut='VERIFIED' OR statut='PENDING'""",
        )
    return {
        "day_usd":   float(row["day_usd"] or 0),
        "month_usd": float(row["month_usd"] or 0),
        "year_usd":  float(row["year_usd"] or 0),
        "pending":   int(row["pending"] or 0),
    }


orange_money_deposit_router = router
