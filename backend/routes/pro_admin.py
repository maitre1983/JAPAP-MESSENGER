"""
JAPAP PRO — Admin endpoints
===========================
- CRUD plans (activate/deactivate, edit price/features/limits/trial eligibility)
- Grant / revoke / extend subscriptions manually
- List subscribers (pagination, filter by plan/status/trial)
- Dashboard stats: per-plan counts, revenue, trial→paid conversion, expired
"""
import uuid
import json
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Any
from database import get_pool
from routes.auth import require_admin
from routes.pro import _parse_jsonb

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/pro", tags=["admin-pro"])


class PlanUpsert(BaseModel):
    plan_id: str
    name: Optional[str] = None
    tagline: Optional[str] = None
    price_usd: Optional[float] = None
    duration_days: Optional[int] = None
    features: Optional[list[str]] = None
    limits: Optional[dict[str, Any]] = None
    trial_eligible: Optional[bool] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class GrantRequest(BaseModel):
    user_id: str
    plan_id: str
    days: int = 30
    note: str = ""


class ExtendRequest(BaseModel):
    days: int


# ---------------- Plans CRUD ----------------
@router.get("/plans")
async def admin_list_plans(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM pro_plans ORDER BY sort_order ASC, price_usd ASC"
        )
    return [
        {
            "id": r['id'], "plan_id": r['plan_id'], "name": r['name'],
            "tagline": r['tagline'] or "", "price_usd": str(r['price_usd']),
            "duration_days": r['duration_days'],
            "features": _parse_jsonb(r['features']),
            "limits": _parse_jsonb(r['limits']),
            "trial_eligible": r['trial_eligible'],
            "sort_order": r['sort_order'], "is_active": r['is_active'],
            "created_at": r['created_at'].isoformat(),
        } for r in rows
    ]


@router.put("/plans/{plan_id}")
async def admin_upsert_plan(plan_id: str, req: PlanUpsert, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM pro_plans WHERE plan_id = $1", plan_id)
        if existing:
            updates, values = [], []
            def add(col, val):
                values.append(val); updates.append(f"{col} = ${len(values)}")
            if req.name is not None: add("name", req.name.strip()[:64])
            if req.tagline is not None: add("tagline", req.tagline.strip()[:255])
            if req.price_usd is not None: add("price_usd", Decimal(str(req.price_usd)))
            if req.duration_days is not None: add("duration_days", int(req.duration_days))
            if req.features is not None: add("features", json.dumps(req.features))
            if req.limits is not None: add("limits", json.dumps(req.limits))
            if req.trial_eligible is not None: add("trial_eligible", bool(req.trial_eligible))
            if req.sort_order is not None: add("sort_order", int(req.sort_order))
            if req.is_active is not None: add("is_active", bool(req.is_active))
            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")
            values.append(datetime.now(timezone.utc)); updates.append(f"updated_at = ${len(values)}")
            values.append(plan_id)
            # Ensure JSONB casts for the two JSON columns we store as strings
            sql = f"UPDATE pro_plans SET {', '.join(updates)} WHERE plan_id = ${len(values)}"
            # Cast JSON placeholders
            sql = sql.replace("features = $", "features = $")  # asyncpg handles JSONB via ::jsonb but we pass str
            # We need to explicitly cast features/limits via ::jsonb if they were set
            for col in ("features", "limits"):
                sql = sql.replace(f"{col} = $", f"{col} = $").replace(
                    f"{col} = $", f"{col} = $", 1
                )
            await conn.execute(sql, *values)
            # Post-fix: since asyncpg doesn't auto-cast str→jsonb, run dedicated updates for those
            if req.features is not None:
                await conn.execute("UPDATE pro_plans SET features = $1::jsonb WHERE plan_id = $2",
                                    json.dumps(req.features), plan_id)
            if req.limits is not None:
                await conn.execute("UPDATE pro_plans SET limits = $1::jsonb WHERE plan_id = $2",
                                    json.dumps(req.limits), plan_id)
        else:
            # Create new plan
            await conn.execute("""
                INSERT INTO pro_plans (plan_id, name, tagline, price_usd, duration_days,
                                       features, limits, trial_eligible, sort_order, is_active)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10)
            """, plan_id, (req.name or plan_id)[:64], (req.tagline or "")[:255],
               Decimal(str(req.price_usd or 0)), int(req.duration_days or 30),
               json.dumps(req.features or []), json.dumps(req.limits or {}),
               bool(req.trial_eligible if req.trial_eligible is not None else True),
               int(req.sort_order or 0), bool(req.is_active if req.is_active is not None else True))

        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_pro_plan_upsert', 'pro_plans', $2)
        """, admin['user_id'], f'{{"plan_id":"{plan_id}"}}')
    return {"status": "ok", "plan_id": plan_id}


# ---------------- Subscriptions management ----------------
@router.get("/subscribers")
async def admin_list_subscribers(
    request: Request,
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100),
    plan_id: Optional[str] = None, status: Optional[str] = None,
    is_trial: Optional[bool] = None, search: Optional[str] = None,
):
    await require_admin(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        base = """
            FROM subscriptions s
            JOIN users u ON u.user_id = s.user_id
            WHERE 1=1
        """
        params: list = []
        if plan_id:
            params.append(plan_id); base += f" AND s.plan_type = ${len(params)}"
        if status:
            params.append(status); base += f" AND s.status = ${len(params)}"
        if is_trial is not None:
            params.append(is_trial); base += f" AND s.is_trial = ${len(params)}"
        if search:
            params.append(f"%{search.lower()}%")
            base += f" AND (LOWER(u.email) LIKE ${len(params)} OR LOWER(u.username) LIKE ${len(params)})"

        count = await conn.fetchval(f"SELECT COUNT(*) {base}", *params)
        params_q = params + [limit, offset]
        rows = await conn.fetch(f"""
            SELECT s.*, u.email, u.first_name, u.last_name, u.username, u.avatar
            {base}
            ORDER BY s.created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}
        """, *params_q)

    subs = []
    for r in rows:
        subs.append({
            "id": r['id'], "user_id": r['user_id'],
            "email": r['email'], "name": f"{r['first_name']} {r['last_name']}".strip() or r['username'],
            "avatar": r['avatar'] or "",
            "plan_id": r['plan_type'], "status": r['status'],
            "is_trial": bool(r['is_trial']), "source": r['source'] or "wallet",
            "original_usd": str(r['original_amount_usd'] or 0),
            "paid_usd": str(r['paid_amount_usd'] or 0),
            "discount_pct": r['discount_pct'] or 0,
            "duration_days": r['duration_days'] or 30,
            "cancel_at_period_end": bool(r['cancel_at_period_end']),
            "starts_at": r['starts_at'].isoformat(),
            "expires_at": r['expires_at'].isoformat() if r['expires_at'] else None,
            "cancelled_at": r['cancelled_at'].isoformat() if r['cancelled_at'] else None,
            "created_at": r['created_at'].isoformat(),
        })
    return {"subscribers": subs, "total": count, "page": page, "limit": limit}


@router.post("/grant")
async def admin_grant(req: GrantRequest, request: Request):
    admin = await require_admin(request)
    if req.days <= 0:
        raise HTTPException(status_code=400, detail="days must be positive")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", req.user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            plan = await conn.fetchrow("SELECT * FROM pro_plans WHERE plan_id = $1", req.plan_id)
            if not plan:
                raise HTTPException(status_code=404, detail="Plan not found")
            # If user has an active sub, expire it first
            await conn.execute("""
                UPDATE subscriptions SET status = 'superseded' WHERE user_id = $1 AND status = 'active'
            """, req.user_id)
            now = datetime.now(timezone.utc)
            expires = now + timedelta(days=req.days)
            await conn.execute("""
                INSERT INTO subscriptions
                    (user_id, plan_type, price, currency, status, starts_at, expires_at,
                     is_trial, source, original_amount_usd, paid_amount_usd, discount_pct, duration_days)
                VALUES ($1, $2, 0, 'USD', 'active', $3, $4, FALSE, 'admin_grant', 0, 0, 0, $5)
            """, req.user_id, req.plan_id, now, expires, req.days)
            await conn.execute("""
                UPDATE users SET is_pro = TRUE, pro_type = $1, pro_expires_at = $2, updated_at = NOW() WHERE user_id = $3
            """, plan['id'], expires, req.user_id)
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'pro_granted', 'Abonnement Pro offert', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", req.user_id,
               f"{plan['name']} vous a été offert par l'équipe JAPAP jusqu'au {expires.strftime('%d/%m/%Y')}. {req.note}".strip())
            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'admin_pro_grant', 'subscriptions', $2)
            """, admin['user_id'],
               f'{{"target":"{req.user_id}","plan":"{req.plan_id}","days":{req.days}}}')
    return {"status": "granted", "expires_at": expires.isoformat()}


@router.post("/revoke/{user_id}")
async def admin_revoke(user_id: str, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sub = await conn.fetchrow("""
            SELECT id FROM subscriptions WHERE user_id = $1 AND status = 'active' ORDER BY expires_at DESC LIMIT 1
        """, user_id)
        if not sub:
            raise HTTPException(status_code=404, detail="No active subscription")
        await conn.execute("""
            UPDATE subscriptions SET status = 'revoked', cancelled_at = NOW(), expires_at = NOW() WHERE id = $1
        """, sub['id'])
        await conn.execute("""
            UPDATE users SET is_pro = FALSE, pro_type = 0, pro_expires_at = NULL WHERE user_id = $1
        """, user_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_pro_revoke', 'subscriptions', $2)
        """, admin['user_id'], f'{{"target":"{user_id}"}}')
    return {"status": "revoked"}


@router.post("/extend/{user_id}")
async def admin_extend(user_id: str, req: ExtendRequest, request: Request):
    admin = await require_admin(request)
    if req.days <= 0:
        raise HTTPException(status_code=400, detail="days must be positive")
    pool = await get_pool()
    async with pool.acquire() as conn:
        sub = await conn.fetchrow("""
            SELECT id, expires_at FROM subscriptions WHERE user_id = $1 AND status = 'active' ORDER BY expires_at DESC LIMIT 1
        """, user_id)
        if not sub:
            raise HTTPException(status_code=404, detail="No active subscription to extend")
        new_expiry = sub['expires_at'] + timedelta(days=req.days)
        await conn.execute("UPDATE subscriptions SET expires_at = $1 WHERE id = $2", new_expiry, sub['id'])
        await conn.execute("UPDATE users SET pro_expires_at = $1 WHERE user_id = $2", new_expiry, user_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_pro_extend', 'subscriptions', $2)
        """, admin['user_id'], f'{{"target":"{user_id}","days":{req.days}}}')
    return {"status": "extended", "new_expiry": new_expiry.isoformat()}


# ---------------- Stats ----------------
@router.get("/stats")
async def admin_pro_stats(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        active_by_plan = await conn.fetch("""
            SELECT plan_type, COUNT(*) AS c
            FROM subscriptions WHERE status = 'active' AND expires_at > NOW()
            GROUP BY plan_type
        """)
        active_trials = await conn.fetchval("""
            SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND is_trial = TRUE AND expires_at > NOW()
        """)
        total_active = await conn.fetchval("""
            SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND expires_at > NOW()
        """)
        revenue_all_time = await conn.fetchval("""
            SELECT COALESCE(SUM(paid_amount_usd), 0) FROM subscriptions
            WHERE is_trial = FALSE AND source = 'wallet'
        """)
        revenue_30d = await conn.fetchval("""
            SELECT COALESCE(SUM(paid_amount_usd), 0) FROM subscriptions
            WHERE is_trial = FALSE AND source = 'wallet' AND created_at > NOW() - INTERVAL '30 days'
        """)
        trials_total = await conn.fetchval("SELECT COUNT(*) FROM subscriptions WHERE is_trial = TRUE")
        trials_converted = await conn.fetchval("""
            SELECT COUNT(DISTINCT s1.user_id)
            FROM subscriptions s1
            JOIN subscriptions s2 ON s1.user_id = s2.user_id
              AND s2.is_trial = FALSE AND s2.source = 'wallet' AND s2.created_at > s1.created_at
            WHERE s1.is_trial = TRUE
        """)
        expired = await conn.fetchval("""
            SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND expires_at <= NOW()
        """)
        recent = await conn.fetch("""
            SELECT s.plan_type, s.is_trial, s.paid_amount_usd, s.created_at, u.email, u.first_name, u.last_name
            FROM subscriptions s JOIN users u ON u.user_id = s.user_id
            ORDER BY s.created_at DESC LIMIT 10
        """)
    by_plan = {r['plan_type']: r['c'] for r in active_by_plan}
    conversion = (float(trials_converted or 0) / float(trials_total) * 100) if trials_total else 0.0
    return {
        "active_total": total_active or 0,
        "active_by_plan": by_plan,
        "active_trials": active_trials or 0,
        "revenue_all_time_usd": str(revenue_all_time or 0),
        "revenue_30d_usd": str(revenue_30d or 0),
        "trials_total": trials_total or 0,
        "trials_converted": trials_converted or 0,
        "conversion_pct": round(conversion, 2),
        "expired_not_updated": expired or 0,
        "recent": [
            {
                "plan_id": r['plan_type'], "is_trial": bool(r['is_trial']),
                "paid_usd": str(r['paid_amount_usd'] or 0),
                "email": r['email'], "name": f"{r['first_name']} {r['last_name']}".strip(),
                "created_at": r['created_at'].isoformat(),
            } for r in recent
        ],
    }


# ---------------- Background expiration sync ----------------
@router.post("/expire-now")
async def admin_sync_expirations(request: Request):
    """Mark expired active subs as expired + update users.is_pro. Idempotent."""
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch("""
                UPDATE subscriptions SET status = 'expired'
                WHERE status = 'active' AND expires_at <= NOW()
                RETURNING user_id
            """)
            uids = {r['user_id'] for r in rows}
            for uid in uids:
                remaining = await conn.fetchrow("""
                    SELECT 1 FROM subscriptions WHERE user_id = $1 AND status = 'active' AND expires_at > NOW() LIMIT 1
                """, uid)
                if not remaining:
                    await conn.execute("""
                        UPDATE users SET is_pro = FALSE, pro_type = 0, pro_expires_at = NULL WHERE user_id = $1
                    """, uid)
    return {"status": "ok", "expired_count": len(uids)}
