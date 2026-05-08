"""
JAPAP — Referral admin endpoints
================================
- Stats dashboard (totals, rewards distributed, conversion)
- Paginated list with filters and block/unblock controls
- Tier editor is via /api/admin/settings → referral_tiers_json
- Manual reminder kick-off for inactive referees
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/referrals", tags=["admin-referrals"])


class BlockRequest(BaseModel):
    reason: str = "Manual block by admin"


@router.get("/stats")
async def admin_ref_stats(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE status = 'active') AS active,
                COUNT(*) FILTER (WHERE status = 'rewarded') AS rewarded,
                COUNT(*) FILTER (WHERE blocked = TRUE) AS blocked,
                COALESCE(SUM(referrer_bonus_usd + referee_bonus_usd)
                         FILTER (WHERE status IN ('active','rewarded')), 0) AS total_bonus_usd
            FROM referrals
        """)
        last_7d = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE created_at > NOW() - INTERVAL '7 days'")
        rewards_30d = await conn.fetchval("""
            SELECT COALESCE(SUM(amount_usd), 0) FROM referral_rewards_log
            WHERE created_at > NOW() - INTERVAL '30 days'
        """)
        top = await conn.fetch("""
            SELECT u.user_id, u.email, u.first_name, u.last_name, u.username,
                COUNT(r.id) FILTER (WHERE r.status IN ('active','rewarded')) AS active_count,
                COALESCE(SUM(r.referrer_bonus_usd), 0) AS earned_usd
            FROM users u JOIN referrals r ON r.referrer_id = u.user_id
            GROUP BY u.user_id, u.email, u.first_name, u.last_name, u.username
            HAVING COUNT(r.id) FILTER (WHERE r.status IN ('active','rewarded')) > 0
            ORDER BY active_count DESC LIMIT 10
        """)
        inactive = await conn.fetchval("""
            SELECT COUNT(*) FROM referrals
            WHERE status = 'pending' AND (blocked = FALSE OR blocked IS NULL)
              AND created_at < NOW() - INTERVAL '7 days'
        """)
    return {
        "total": totals['total'] or 0,
        "pending": totals['pending'] or 0,
        "active": totals['active'] or 0,
        "rewarded": totals['rewarded'] or 0,
        "blocked": totals['blocked'] or 0,
        "last_7d": last_7d or 0,
        "total_bonus_usd": str(totals['total_bonus_usd'] or 0),
        "rewards_30d_usd": str(rewards_30d or 0),
        "inactive_referees_7d": inactive or 0,
        "top_referrers": [{
            "user_id": r['user_id'],
            "name": f"{r['first_name']} {r['last_name']}".strip() or r['username'],
            "email": r['email'],
            "active_count": r['active_count'],
            "earned_usd": str(r['earned_usd'] or 0),
        } for r in top],
    }


@router.get("/list")
async def admin_ref_list(
    request: Request,
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None, blocked: Optional[bool] = None,
    search: Optional[str] = None,
):
    await require_admin(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        base = """
            FROM referrals r
            JOIN users u1 ON u1.user_id = r.referrer_id
            JOIN users u2 ON u2.user_id = r.referred_id
            WHERE 1=1
        """
        params: list = []
        if status:
            params.append(status); base += f" AND r.status = ${len(params)}"
        if blocked is not None:
            params.append(blocked); base += f" AND r.blocked = ${len(params)}"
        if search:
            params.append(f"%{search.lower()}%")
            base += f" AND (LOWER(u1.email) LIKE ${len(params)} OR LOWER(u2.email) LIKE ${len(params)})"
        count = await conn.fetchval(f"SELECT COUNT(*) {base}", *params)
        q = params + [limit, offset]
        rows = await conn.fetch(f"""
            SELECT r.*,
                u1.email AS ref_email, u1.username AS ref_username, u1.first_name AS ref_fn, u1.last_name AS ref_ln,
                u2.email AS ree_email, u2.username AS ree_username, u2.first_name AS ree_fn, u2.last_name AS ree_ln
            {base}
            ORDER BY r.created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}
        """, *q)
    items = [{
        "id": r['id'], "status": r['status'],
        "blocked": bool(r['blocked']), "blocked_reason": r['blocked_reason'],
        "ip_address": r['ip_address'] or "",
        "device_id": r['device_id'] or "",
        "referrer": {"user_id": r['referrer_id'], "email": r['ref_email'],
                     "name": f"{r['ref_fn']} {r['ref_ln']}".strip() or r['ref_username']},
        "referee": {"user_id": r['referred_id'], "email": r['ree_email'],
                    "name": f"{r['ree_fn']} {r['ree_ln']}".strip() or r['ree_username']},
        "referrer_bonus_usd": str(r['referrer_bonus_usd'] or 0),
        "referee_bonus_usd": str(r['referee_bonus_usd'] or 0),
        "reward_given": r['reward_given'],
        "created_at": r['created_at'].isoformat(),
        "activated_at": r['activated_at'].isoformat() if r['activated_at'] else None,
    } for r in rows]
    return {"referrals": items, "total": count, "page": page, "limit": limit}


@router.post("/{referral_id}/block")
async def admin_block(referral_id: int, req: BlockRequest, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT id FROM referrals WHERE id = $1", referral_id)
        if not r:
            raise HTTPException(status_code=404, detail="Referral not found")
        await conn.execute(
            "UPDATE referrals SET blocked = TRUE, blocked_reason = $1 WHERE id = $2",
            req.reason[:255], referral_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_referral_block', 'referrals', $2)
        """, admin['user_id'], f'{{"referral_id":{referral_id},"reason":"{req.reason}"}}')
    return {"status": "blocked"}


@router.post("/{referral_id}/unblock")
async def admin_unblock(referral_id: int, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT id FROM referrals WHERE id = $1", referral_id)
        if not r:
            raise HTTPException(status_code=404, detail="Referral not found")
        await conn.execute(
            "UPDATE referrals SET blocked = FALSE, blocked_reason = NULL WHERE id = $1", referral_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_referral_unblock', 'referrals', $2)
        """, admin['user_id'], f'{{"referral_id":{referral_id}}}')
    return {"status": "unblocked"}


@router.post("/send-reminders")
async def admin_send_reminders(request: Request):
    """Send an in-app reminder to referees who signed up >7d ago and never
    activated their referral. Respects `referral_reminder_enabled` toggle.
    Idempotent per referral (uses `reminder_sent_at`)."""
    from services.settings_service import get_bool, get_int
    admin = await require_admin(request)
    if not await get_bool("referral_reminder_enabled", True):
        return {"sent": 0, "skipped_reason": "reminder_disabled"}
    delay = await get_int("referral_reminder_delay_days", 7)
    pool = await get_pool()
    sent = 0
    async with pool.acquire() as conn:
        pending = await conn.fetch("""
            SELECT id, referred_id, referrer_id FROM referrals
            WHERE status = 'pending' AND (blocked = FALSE OR blocked IS NULL)
              AND reminder_sent_at IS NULL
              AND created_at < NOW() - ($1::int || ' days')::interval
            LIMIT 500
        """, delay)
        for r in pending:
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'referral_reminder', 'Activez votre compte',
                        'Terminez votre inscription pour débloquer votre bonus de bienvenue et votre parrain vous remerciera !')
            """, f"notif_{uuid.uuid4().hex[:12]}", r['referred_id'])
            await conn.execute("UPDATE referrals SET reminder_sent_at = NOW() WHERE id = $1", r['id'])
            sent += 1
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_referral_reminders', 'referrals', $2)
        """, admin['user_id'], f'{{"sent":{sent}}}')
    return {"sent": sent}



# ── iter150 — Referral Anti-fraud report ────────────────────────────
@router.get("/fraud-report")
async def admin_ref_fraud_report(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(100, ge=10, le=500),
):
    """High-level fraud signals (IP velocity, device co-location,
    referrer velocity) over a rolling window. Read-only — admin decides
    actions in a separate endpoint."""
    await require_admin(request)
    from services.referral_fraud_service import fraud_report
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await fraud_report(conn, days=days, limit=limit)
