"""
JAPAP — Ads Console (iter180)
=============================
Régie publicitaire interne — Wallet USD uniquement, zéro PSP.

Flow :
  1. Seller crée une campagne avec `budget_usd` → prélevé du wallet vers escrow campagne
  2. Feed /api/marketplace/feed injecte des slots sponsorisés (audience-filtered, budget-aware)
  3. Chaque impression consomme `cpm_rate / 1000` du `spent_usd`
  4. Chaque click consomme `cpc_rate` additionnel
  5. Budget consommé → `budget_usd - spent_usd` → `japap_treasury`
  6. Quand spent >= budget → status='completed' automatiquement

Audit complet via `ads_impressions` / `ads_clicks` + `transactions` ledger.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user, require_admin
from services.settings_service import get_bool, get_float, get_int
from utils.network import client_ip as _client_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ads_console", tags=["ads_console"])


def _hash_ip(ip: str) -> str:
    return hashlib.sha256((ip or "unknown").encode("utf-8")).hexdigest()[:32]


async def _user_age_and_country(user: dict):
    country = (user.get("country_code") or user.get("country") or "").upper().strip() or None
    if country and len(country) > 2:
        country = country[:2]
    age = None
    bd = user.get("birthday")
    if bd:
        try:
            from datetime import date as _d, datetime as _dt
            if isinstance(bd, str):
                bd = _dt.fromisoformat(bd[:10]).date()
            elif hasattr(bd, "date"):
                bd = bd.date()
            today = _d.today()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except Exception:
            age = None
    return age, country


# ─────────────────────────── CAMPAIGN MODELS ───────────────────────────
class CreateCampaignRequest(BaseModel):
    product_id: str
    name: Optional[str] = None
    budget_usd: float = Field(..., gt=0)
    daily_budget_usd: Optional[float] = None
    duration_days: int = Field(7, ge=1, le=60)
    is_global: bool = True
    target_countries: Optional[list[str]] = None
    age_min: Optional[int] = None
    age_max: Optional[int] = None


class CampaignUpdateRequest(BaseModel):
    status: Optional[str] = None  # 'paused' | 'active' | 'cancelled'
    name: Optional[str] = None


# ─────────────────────────── CREATE CAMPAIGN ───────────────────────────
@router.post("/campaigns")
async def create_campaign(req: CreateCampaignRequest, request: Request):
    """Create an ads campaign — prélève le budget du wallet USD de l'annonceur
    (escrow intern, reversé à japap_treasury au fil des impressions/clicks)."""
    user = await get_current_user(request)
    if not await get_bool("ads_enabled", True):
        raise HTTPException(status_code=503, detail="La régie Ads est désactivée.")
    min_budget = Decimal(str(await get_float("min_campaign_budget", 5.0)))
    max_days = await get_int("max_campaign_duration_days", 30)
    cpm_default = Decimal(str(await get_float("default_cpm_rate", 2.0)))
    cpc_default = Decimal(str(await get_float("default_cpc_rate", 0.10)))

    budget = Decimal(str(req.budget_usd)).quantize(Decimal("0.01"))
    if budget < min_budget:
        raise HTTPException(status_code=400, detail=f"Budget minimum : {min_budget} USD")
    days = min(req.duration_days, max_days)

    # Sanitize targeting
    targeting_on = await get_bool("targeting_enabled", True)
    allow_ctry = await get_bool("allow_country_filter", True) and targeting_on
    allow_age = await get_bool("allow_age_filter", True) and targeting_on
    ctries = []
    if allow_ctry and req.target_countries and not req.is_global:
        ctries = list({(c or "").upper().strip()[:2] for c in req.target_countries if c})[:50]
        ctries = [c for c in ctries if len(c) == 2]
    is_global_b = bool(req.is_global) or not ctries
    amin = int(req.age_min) if (allow_age and req.age_min is not None) else None
    amax = int(req.age_max) if (allow_age and req.age_max is not None) else None
    if amin is not None and amin < 13:
        amin = 13
    if amax is not None and amax > 99:
        amax = 99
    if amin is not None and amax is not None and amin > amax:
        amin, amax = amax, amin

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Verify product ownership
            prod = await conn.fetchrow(
                "SELECT * FROM products WHERE product_id = $1 AND seller_id = $2 AND status = 'active'",
                req.product_id, user["user_id"])
            if not prod:
                raise HTTPException(status_code=404, detail="Produit introuvable ou non possédé")

            # Debit wallet
            wallet = await conn.fetchrow(
                "SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user["user_id"])
            if not wallet:
                raise HTTPException(status_code=404, detail="Wallet introuvable")
            if wallet["is_locked"]:
                raise HTTPException(status_code=403, detail="Wallet verrouillé")
            if Decimal(str(wallet["balance"])) < budget:
                raise HTTPException(
                    status_code=400,
                    detail=f"INSUFFICIENT_BALANCE — {budget} USD requis")
            await conn.execute(
                "UPDATE wallets SET balance = balance - $1, updated_at = NOW() WHERE user_id = $2",
                budget, user["user_id"])

            campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc)
            end = now + timedelta(days=days)
            await conn.execute("""
                INSERT INTO ads_campaigns
                  (campaign_id, user_id, product_id, name, budget_usd,
                   daily_budget_usd, cpm_rate, cpc_rate, status,
                   start_date, end_date, is_global, target_countries, age_min, age_max)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'active',$9,$10,$11,$12,$13,$14)
            """, campaign_id, user["user_id"], req.product_id,
               req.name or f"Campagne {prod['title'][:50]}",
               budget, req.daily_budget_usd, cpm_default, cpc_default,
               now, end, is_global_b, ctries or None, amin, amax)

            tx_id = f"ads_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO transactions
                  (tx_id, from_user_id, type, amount, currency, status, notes, reference)
                VALUES ($1, $2, 'ads_campaign_fund', $3, 'USD', 'completed', $4, $5)
            """, tx_id, user["user_id"], budget,
               f"Financement campagne Ads · {prod['title'][:80]}",
               campaign_id)

            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'ads_campaign_create', 'ads', $2)
            """, user["user_id"],
               f'{{"campaign_id":"{campaign_id}","budget":"{budget}","days":{days}}}')

    return {
        "campaign_id": campaign_id,
        "product_id": req.product_id,
        "budget_usd": str(budget),
        "cpm_rate": str(cpm_default),
        "cpc_rate": str(cpc_default),
        "status": "active",
        "end_date": end.isoformat(),
        "is_global": is_global_b,
        "target_countries": ctries,
        "age_min": amin, "age_max": amax,
        "message": f"✅ Campagne activée — budget {budget} USD débité du wallet",
    }


# ─────────────────────────── LIST & UPDATE ───────────────────────────
@router.get("/campaigns/mine")
async def list_my_campaigns(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.*, p.title AS product_title, p.images AS product_images,
                   (SELECT COUNT(*) FROM ads_impressions WHERE campaign_id=c.campaign_id) AS impressions,
                   (SELECT COUNT(*) FROM ads_clicks WHERE campaign_id=c.campaign_id) AS clicks
            FROM ads_campaigns c
            LEFT JOIN products p ON p.product_id = c.product_id
            WHERE c.user_id = $1
            ORDER BY c.created_at DESC
        """, user["user_id"])
    out = []
    for r in rows:
        d = dict(r)
        for k in ("budget_usd", "daily_budget_usd", "spent_usd", "cpm_rate", "cpc_rate"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        d["start_date"] = d["start_date"].isoformat()
        d["end_date"] = d["end_date"].isoformat()
        d["created_at"] = d["created_at"].isoformat()
        imp = int(d.get("impressions") or 0)
        clk = int(d.get("clicks") or 0)
        d["ctr"] = round((clk / imp * 100), 2) if imp > 0 else 0
        d["impressions"] = imp
        d["clicks"] = clk
        out.append(d)
    return {"items": out, "total": len(out)}


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, req: CampaignUpdateRequest, request: Request):
    user = await get_current_user(request)
    if req.status and req.status not in ("active", "paused", "cancelled"):
        raise HTTPException(status_code=400, detail="Statut invalide")
    pool = await get_pool()
    async with pool.acquire() as conn:
        c = await conn.fetchrow(
            "SELECT * FROM ads_campaigns WHERE campaign_id=$1 AND user_id=$2",
            campaign_id, user["user_id"])
        if not c:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
        if c["status"] == "completed":
            raise HTTPException(status_code=409, detail="Campagne terminée — modification impossible")
        sets, vals = [], []
        i = 1
        if req.status:
            sets.append(f"status=${i}"); vals.append(req.status); i += 1
        if req.name:
            sets.append(f"name=${i}"); vals.append(req.name[:120]); i += 1
        if not sets:
            return {"ok": True, "unchanged": True}
        vals.append(campaign_id)
        await conn.execute(
            f"UPDATE ads_campaigns SET {', '.join(sets)} WHERE campaign_id=${i}",
            *vals)
    return {"ok": True, "message": f"Campagne {req.status or 'mise à jour'}"}


# ─────────────────────────── FEED INJECTION ───────────────────────────
async def pick_sponsored_for_user(conn, user: dict, *, limit: int = 3,
                                   exclude_campaign_ids: Optional[list[str]] = None):
    age, country = await _user_age_and_country(user)
    ex = exclude_campaign_ids or []
    rows = await conn.fetch("""
        SELECT c.*, p.title AS product_title, p.images AS product_images,
               p.price AS product_price, p.currency AS product_currency,
               p.seller_id AS product_seller_id
        FROM ads_campaigns c
        LEFT JOIN products p ON p.product_id = c.product_id
        WHERE c.status = 'active'
          AND c.end_date > NOW()
          AND c.spent_usd < c.budget_usd
          AND p.status = 'active'
          AND NOT (c.campaign_id = ANY($3::text[]))
          -- exclude the user's own campaigns (no self-impression)
          AND c.user_id != $5
          -- country fallback: accepted if global OR country matches OR user has no country
          AND (c.is_global = TRUE OR c.target_countries IS NULL
               OR cardinality(c.target_countries) = 0
               OR $1::text IS NULL
               OR $1::text = ANY(c.target_countries))
          -- age fallback
          AND ($2::int IS NULL OR c.age_min IS NULL OR $2::int >= c.age_min)
          AND ($2::int IS NULL OR c.age_max IS NULL OR $2::int <= c.age_max)
        ORDER BY RANDOM()
        LIMIT $4
    """, country, age, ex, limit, user.get("user_id") or "")
    out = []
    for r in rows:
        d = dict(r)
        d["budget_usd"] = str(d["budget_usd"])
        d["spent_usd"] = str(d["spent_usd"])
        d["cpm_rate"] = str(d["cpm_rate"] or 2.0)
        d["cpc_rate"] = str(d["cpc_rate"] or 0.10)
        d["product_price"] = str(d["product_price"]) if d.get("product_price") is not None else "0"
        if d["product_images"] and isinstance(d["product_images"], str):
            try:
                import json as _j
                d["product_images"] = _j.loads(d["product_images"] or "[]")
            except Exception:
                d["product_images"] = []
        d["start_date"] = d["start_date"].isoformat()
        d["end_date"] = d["end_date"].isoformat()
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


@router.get("/feed")
async def feed_sponsored(request: Request, limit: int = Query(3, ge=1, le=10)):
    """Retourne N slots sponsorisés filtrés pour cet utilisateur (NE consomme pas
    encore le budget — voir /impress pour log+debit)."""
    user = await get_current_user(request)
    if not await get_bool("ads_enabled", True):
        return {"items": []}
    pool = await get_pool()
    async with pool.acquire() as conn:
        items = await pick_sponsored_for_user(conn, user, limit=limit)
    return {"items": items, "total": len(items)}


# ─────────────────────────── IMPRESSION / CLICK ───────────────────────────
class ImpressionRequest(BaseModel):
    campaign_ids: list[str]


@router.post("/impress")
async def log_impressions(req: ImpressionRequest, request: Request):
    """Log des impressions et consomme le CPM du budget. À appeler quand des
    slots ads sont effectivement rendus dans le feed côté client."""
    user = await get_current_user(request)
    if not req.campaign_ids:
        return {"logged": 0}
    ip_h = _hash_ip(_client_ip(request))
    pool = await get_pool()
    logged = 0
    async with pool.acquire() as conn:
        for cid in set(req.campaign_ids[:20]):
            async with conn.transaction():
                c = await conn.fetchrow(
                    "SELECT * FROM ads_campaigns WHERE campaign_id=$1 AND status='active' "
                    "AND end_date > NOW() FOR UPDATE", cid)
                if not c or Decimal(str(c["spent_usd"])) >= Decimal(str(c["budget_usd"])):
                    continue
                # Log impression
                await conn.execute(
                    "INSERT INTO ads_impressions (campaign_id, user_id, ip_hash) VALUES ($1,$2,$3)",
                    cid, user["user_id"], ip_h)
                # CPM consumption: 1 impression = cpm_rate / 1000
                cpm = Decimal(str(c["cpm_rate"] or "2.0"))
                cost = (cpm / Decimal(1000)).quantize(Decimal("0.0001"))
                await _consume_budget(conn, cid, cost, "impression", user["user_id"])
                logged += 1
    return {"logged": logged}


class ClickRequest(BaseModel):
    campaign_id: str


@router.post("/click")
async def log_click(req: ClickRequest, request: Request):
    user = await get_current_user(request)
    ip_h = _hash_ip(_client_ip(request))
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            c = await conn.fetchrow(
                "SELECT * FROM ads_campaigns WHERE campaign_id=$1 AND status='active' "
                "AND end_date > NOW() FOR UPDATE", req.campaign_id)
            if not c:
                return {"ok": False, "reason": "campaign_inactive"}
            await conn.execute(
                "INSERT INTO ads_clicks (campaign_id, user_id, ip_hash) VALUES ($1,$2,$3)",
                req.campaign_id, user["user_id"], ip_h)
            cpc = Decimal(str(c["cpc_rate"] or "0.10"))
            await _consume_budget(conn, req.campaign_id, cpc, "click", user["user_id"])
    return {"ok": True, "campaign_id": req.campaign_id, "product_id": c["product_id"]}


async def _consume_budget(conn, campaign_id: str, cost: Decimal, kind: str, actor_id: str):
    """Atomic budget consumption: spent_usd += cost; if >= budget → complete;
    always log a `marketplace_commission` tx to japap_treasury (the advertiser
    wallet was pre-debited at campaign creation; now budget just flows to
    treasury as spent)."""
    await conn.execute(
        "UPDATE ads_campaigns SET spent_usd = spent_usd + $1 WHERE campaign_id=$2",
        cost, campaign_id)
    # If spent >= budget → complete
    await conn.execute("""
        UPDATE ads_campaigns SET status='completed'
        WHERE campaign_id=$1 AND spent_usd >= budget_usd AND status='active'
    """, campaign_id)
    # Treasury tx (cheap: one row per consumption event)
    tx_id = f"ads_{kind[:3]}_{uuid.uuid4().hex[:8]}"
    await conn.execute("""
        INSERT INTO transactions (tx_id, type, amount, currency, status, notes, reference)
        VALUES ($1, 'ads_spend', $2, 'USD', 'completed', $3, $4)
    """, tx_id, cost, f"Ads {kind} → japap_treasury", campaign_id)


# ─────────────────────────── ADMIN ───────────────────────────
@router.get("/admin/campaigns")
async def admin_list_campaigns(request: Request,
                                 status: Optional[str] = None,
                                 limit: int = Query(100, ge=1, le=500)):
    await require_admin(request)
    pool = await get_pool()
    q = "SELECT c.*, u.email, p.title AS product_title FROM ads_campaigns c " \
        "JOIN users u ON u.user_id=c.user_id " \
        "LEFT JOIN products p ON p.product_id = c.product_id WHERE 1=1 "
    args = []
    if status:
        q += f" AND c.status = ${len(args)+1}"; args.append(status)
    q += f" ORDER BY c.created_at DESC LIMIT ${len(args)+1}"; args.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(q, *args)
    out = []
    for r in rows:
        d = dict(r)
        d["budget_usd"] = str(d["budget_usd"])
        d["spent_usd"] = str(d["spent_usd"])
        d["start_date"] = d["start_date"].isoformat()
        d["end_date"] = d["end_date"].isoformat()
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return {"items": out, "total": len(out)}
