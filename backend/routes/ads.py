"""
JAPAP Ads — Sponsored posts / reels / products + admin banners
==============================================================
Advertisers create a campaign by choosing a target (post, reel, product or
raw banner), setting a budget in USD and a CPM (cost per 1000 impressions).
The amount is debited from their wallet (local currency via `currency_rates`)
and escrowed on the campaign. As impressions/clicks happen, `spent_usd`
increases; when spent ≥ budget or end_at passes, the campaign stops serving.

- Admin approves pending campaigns (moderation).
- Public endpoint `GET /api/ads/serve` returns the ad to display alongside
  feed/reel/marketplace content (weighted random by remaining budget).
"""
import uuid
import json as _json
import logging
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field
from typing import Optional, Literal
from database import get_pool
from routes.auth import get_current_user
from services.settings_service import get_bool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ads", tags=["ads"])


class CreateAdRequest(BaseModel):
    target_type: Literal["post", "reel", "product", "banner"]
    target_id: Optional[str] = ""
    title: str = ""
    image_url: str = ""
    cta_url: str = ""
    budget_usd: float = Field(..., gt=0)
    cpm_usd: float = Field(default=1.0, gt=0)
    country_code: Optional[str] = ""
    days: int = Field(default=7, ge=1, le=60)


class ApproveAdRequest(BaseModel):
    approve: bool = True
    reason: str = ""


# ───────────────────────────────────────────────────────────────────────
# Advertiser APIs
# ───────────────────────────────────────────────────────────────────────
@router.post("/campaigns")
async def create_campaign(req: CreateAdRequest, request: Request):
    """Advertiser creates a campaign. Debits budget from their wallet (in local
    currency, converted from USD). Status starts 'pending' until admin approves."""
    user = await get_current_user(request)
    if not await get_bool("ads_enabled", True):
        raise HTTPException(status_code=503, detail="JAPAP Publicité est désactivé.")
    if req.target_type != "banner" and not req.target_id:
        raise HTTPException(status_code=400, detail="target_id requis pour ce type de publicité.")

    pool = await get_pool()
    campaign_id = f"ad_{uuid.uuid4().hex[:12]}"
    budget = Decimal(str(req.budget_usd))
    start_at = datetime.now(timezone.utc)
    end_at = start_at + timedelta(days=req.days)

    async with pool.acquire() as conn:
        # Validate target exists when specified
        if req.target_type == "post" and req.target_id:
            ok = await conn.fetchval("SELECT 1 FROM posts WHERE post_id = $1 AND user_id = $2",
                                      req.target_id, user['user_id'])
            if not ok:
                raise HTTPException(status_code=404, detail="Post introuvable ou non autorisé.")
        elif req.target_type == "reel" and req.target_id:
            ok = await conn.fetchval("SELECT 1 FROM reels WHERE reel_id = $1 AND user_id = $2",
                                      req.target_id, user['user_id'])
            if not ok:
                raise HTTPException(status_code=404, detail="Reel introuvable ou non autorisé.")
        elif req.target_type == "product" and req.target_id:
            ok = await conn.fetchval("SELECT 1 FROM products WHERE product_id = $1 AND seller_id = $2",
                                      req.target_id, user['user_id'])
            if not ok:
                raise HTTPException(status_code=404, detail="Produit introuvable ou non autorisé.")

        # Debit wallet in local currency
        wallet = await conn.fetchrow(
            "SELECT balance, currency FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
        if not wallet:
            raise HTTPException(status_code=400, detail="Wallet introuvable.")
        wallet_ccy = (wallet['currency'] or 'USD').upper()
        rate_row = await conn.fetchrow(
            "SELECT rate_vs_usd FROM currency_rates WHERE code = $1", wallet_ccy)
        rate = Decimal(str(rate_row['rate_vs_usd'])) if rate_row and wallet_ccy != "USD" else Decimal("1")
        local_cost = (budget * rate).quantize(Decimal("0.01"))
        if wallet['balance'] < local_cost:
            raise HTTPException(status_code=402, detail=f"Solde insuffisant. Coût : {local_cost} {wallet_ccy}")

        async with conn.transaction():
            await conn.execute(
                "UPDATE wallets SET balance = balance - $1, updated_at = NOW() WHERE user_id = $2",
                local_cost, user['user_id'])
            tx_id = f"ad_{uuid.uuid4().hex[:12]}"
            await conn.execute("""
                INSERT INTO transactions (tx_id, from_user_id, type, amount, currency, status, notes)
                VALUES ($1, $2, 'ad_campaign', $3, $4, 'completed', $5)
            """, tx_id, user['user_id'], local_cost, wallet_ccy,
               f"Publicité {req.target_type} — budget ${budget}")
            await conn.execute("""
                INSERT INTO ad_campaigns (campaign_id, owner_id, target_type, target_id, title,
                    image_url, cta_url, budget_usd, cpm_usd, status, country_code, start_at, end_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', $10, $11, $12)
            """, campaign_id, user['user_id'], req.target_type, req.target_id or None,
               (req.title or '').strip()[:200], (req.image_url or '').strip()[:500],
               (req.cta_url or '').strip()[:500], budget, Decimal(str(req.cpm_usd)),
               (req.country_code or '').upper()[:2], start_at, end_at)
    return {"campaign_id": campaign_id, "status": "pending", "local_cost": str(local_cost), "currency": wallet_ccy}


@router.get("/campaigns/mine")
async def my_campaigns(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM ad_campaigns WHERE owner_id = $1 ORDER BY created_at DESC
        """, user['user_id'])
    return [_serialize(r) for r in rows]


# ───────────────────────────────────────────────────────────────────────
# Admin APIs
# ───────────────────────────────────────────────────────────────────────
@router.get("/admin/campaigns")
async def admin_list_campaigns(request: Request, status: Optional[str] = None,
                                page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    user = await get_current_user(request)
    if user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin uniquement")
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch("""
                SELECT c.*, u.username, u.email FROM ad_campaigns c
                JOIN users u ON u.user_id = c.owner_id
                WHERE c.status = $1 ORDER BY c.created_at DESC LIMIT $2 OFFSET $3
            """, status, limit, offset)
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM ad_campaigns WHERE status = $1", status)
        else:
            rows = await conn.fetch("""
                SELECT c.*, u.username, u.email FROM ad_campaigns c
                JOIN users u ON u.user_id = c.owner_id
                ORDER BY c.created_at DESC LIMIT $1 OFFSET $2
            """, limit, offset)
            total = await conn.fetchval("SELECT COUNT(*) FROM ad_campaigns")
    return {"items": [_serialize(r) for r in rows], "total": total, "page": page, "limit": limit}


@router.post("/admin/campaigns/{campaign_id}/approve")
async def admin_approve(campaign_id: str, req: ApproveAdRequest, request: Request):
    user = await get_current_user(request)
    if user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin uniquement")
    new_status = 'approved' if req.approve else 'rejected'
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT owner_id, status FROM ad_campaigns WHERE campaign_id = $1", campaign_id)
        if not row:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
        if row['status'] not in ('pending',):
            raise HTTPException(status_code=400, detail=f"Statut actuel: {row['status']}")
        await conn.execute(
            "UPDATE ad_campaigns SET status = $1 WHERE campaign_id = $2",
            new_status, campaign_id)
        await conn.execute("""
            INSERT INTO notifications (notif_id, user_id, type, title, message)
            VALUES ($1, $2, 'ad_review', $3, $4)
        """, f"notif_{uuid.uuid4().hex[:12]}", row['owner_id'],
           "Publicité approuvée" if req.approve else "Publicité refusée",
           (req.reason or '')[:255])
    return {"status": new_status}


# ───────────────────────────────────────────────────────────────────────
# Serving (public — no auth required for impressions tracking)
# ───────────────────────────────────────────────────────────────────────
@router.get("/serve")
async def serve_ad(request: Request, slot: str = Query("feed", regex="^(feed|reel|marketplace|banner)$")):
    """Pick one running campaign to display on the given slot. Returns {} if no match."""
    if not await get_bool("ads_enabled", True):
        return {}
    user = await get_current_user(request)
    slot_to_type = {
        "feed": "post", "reel": "reel", "marketplace": "product", "banner": "banner",
    }
    target_type = slot_to_type[slot]
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM ad_campaigns
            WHERE status = 'approved' AND target_type = $1
              AND (end_at IS NULL OR end_at > NOW())
              AND (start_at IS NULL OR start_at <= NOW())
              AND spent_usd < budget_usd
            LIMIT 50
        """, target_type)
        if not rows:
            return {}
        # Weighted random by remaining budget
        remaining = [(r, float(r['budget_usd'] or 0) - float(r['spent_usd'] or 0)) for r in rows]
        total = sum(w for _, w in remaining) or 1
        rnd = random.uniform(0, total)
        acc = 0
        picked = rows[0]
        for r, w in remaining:
            acc += w
            if acc >= rnd:
                picked = r
                break
        # Log impression + charge cpm
        cpm = Decimal(str(picked['cpm_usd'] or 0))
        cost = (cpm / Decimal(1000)).quantize(Decimal("0.0001"))
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO ad_events (campaign_id, user_id, event_type) VALUES ($1, $2, 'impression')",
                picked['campaign_id'], (user or {}).get('user_id'))
            await conn.execute("""
                UPDATE ad_campaigns
                SET impressions = impressions + 1, spent_usd = spent_usd + $1,
                    status = CASE WHEN spent_usd + $1 >= budget_usd THEN 'ended' ELSE status END
                WHERE campaign_id = $2
            """, cost, picked['campaign_id'])
    return _serialize(picked)


@router.post("/campaigns/{campaign_id}/click")
async def log_click(campaign_id: str, request: Request):
    """Track a click on a served ad (idempotency intentionally skipped — cheap)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM ad_campaigns WHERE campaign_id = $1", campaign_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
        await conn.execute(
            "INSERT INTO ad_events (campaign_id, user_id, event_type) VALUES ($1, $2, 'click')",
            campaign_id, user['user_id'])
        await conn.execute(
            "UPDATE ad_campaigns SET clicks = clicks + 1 WHERE campaign_id = $1", campaign_id)
    return {"status": "ok"}


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────
def _serialize(r) -> dict:
    d = dict(r)
    d['budget_usd'] = str(d['budget_usd'])
    d['spent_usd'] = str(d['spent_usd'])
    d['cpm_usd'] = str(d['cpm_usd'])
    for k in ['start_at', 'end_at', 'created_at']:
        if d.get(k):
            d[k] = d[k].isoformat()
    return d
