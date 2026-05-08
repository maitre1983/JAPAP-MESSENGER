"""
JAPAP CONNECT — Admin endpoints
===============================
Stats, paginated hotspot management, block/unblock, sponsor badge grant.
"""
import logging
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import require_admin
from routes.connect import _hotspot_dict

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/connect", tags=["admin-connect"])


class BlockRequest(BaseModel):
    reason: str = "Manual block by admin"


class SponsorRequest(BaseModel):
    sponsor_name: str
    is_sponsored: bool = True


class PremiumRequest(BaseModel):
    is_premium: bool


@router.post("/hotspots/{hotspot_id}/premium")
async def admin_set_premium(hotspot_id: str, req: PremiumRequest, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        await conn.execute(
            "UPDATE wifi_hotspots SET is_premium = $1 WHERE hotspot_id = $2",
            bool(req.is_premium), hotspot_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_connect_premium', 'wifi_hotspots', $2)
        """, admin['user_id'], f'{{"hotspot_id":"{hotspot_id}","is_premium":{str(req.is_premium).lower()}}}')
    return {"status": "ok"}


@router.get("/stats")
async def admin_stats(request: Request):
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow("""
            SELECT
                COUNT(*) AS hotspots_total,
                COUNT(*) FILTER (WHERE is_active = TRUE AND is_blocked = FALSE) AS hotspots_active,
                COUNT(*) FILTER (WHERE is_blocked = TRUE) AS hotspots_blocked,
                COUNT(*) FILTER (WHERE is_sponsored = TRUE) AS hotspots_sponsored
            FROM wifi_hotspots
        """)
        conns = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE started_at > NOW() - INTERVAL '24 hours') AS last_24h,
                COUNT(*) FILTER (WHERE status = 'active') AS live,
                COUNT(*) FILTER (WHERE blocked = TRUE) AS blocked,
                COALESCE(SUM(reward_usd), 0) AS rewarded_usd,
                COALESCE(SUM(reward_usd) FILTER (WHERE started_at > NOW() - INTERVAL '30 days'), 0) AS rewarded_30d_usd
            FROM wifi_connections
        """)
        top = await conn.fetch("""
            SELECT h.hotspot_id, h.alias, h.total_connections, h.total_rewarded_usd,
                   u.user_id, u.email, u.first_name, u.last_name, u.username
            FROM wifi_hotspots h
            LEFT JOIN users u ON u.user_id = h.owner_id
            ORDER BY h.total_connections DESC LIMIT 10
        """)
    return {
        "hotspots_total": totals['hotspots_total'] or 0,
        "hotspots_active": totals['hotspots_active'] or 0,
        "hotspots_blocked": totals['hotspots_blocked'] or 0,
        "hotspots_sponsored": totals['hotspots_sponsored'] or 0,
        "connections_total": conns['total'] or 0,
        "connections_24h": conns['last_24h'] or 0,
        "connections_live": conns['live'] or 0,
        "connections_blocked": conns['blocked'] or 0,
        "rewarded_all_time_usd": str(conns['rewarded_usd'] or 0),
        "rewarded_30d_usd": str(conns['rewarded_30d_usd'] or 0),
        "top_hotspots": [{
            "hotspot_id": r['hotspot_id'], "alias": r['alias'],
            "connections": r['total_connections'] or 0,
            "rewarded_usd": str(r['total_rewarded_usd'] or 0),
            "owner_email": r['email'],
            "owner_name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r['username'] or '—',
        } for r in top],
    }


@router.get("/hotspots")
async def admin_list(request: Request,
                      page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100),
                      search: Optional[str] = None, blocked: Optional[bool] = None,
                      type: Optional[str] = None, sponsored: Optional[bool] = None):
    await require_admin(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        base = """
            FROM wifi_hotspots h
            LEFT JOIN users u ON u.user_id = h.owner_id
            WHERE 1=1
        """
        params: list = []
        if search:
            params.append(f"%{search.lower()}%")
            base += f" AND (LOWER(h.alias) LIKE ${len(params)} OR LOWER(u.email) LIKE ${len(params)})"
        if blocked is not None:
            params.append(blocked); base += f" AND h.is_blocked = ${len(params)}"
        if type:
            params.append(type); base += f" AND h.type = ${len(params)}"
        if sponsored is not None:
            params.append(sponsored); base += f" AND h.is_sponsored = ${len(params)}"
        count = await conn.fetchval(f"SELECT COUNT(*) {base}", *params)
        q = params + [limit, offset]
        rows = await conn.fetch(f"""
            SELECT h.*, u.email AS owner_email, u.first_name AS owner_fn,
                   u.last_name AS owner_ln, u.username AS owner_username
            {base}
            ORDER BY h.created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}
        """, *q)
    items = []
    for r in rows:
        d = _hotspot_dict(r)
        d["owner"] = {
            "user_id": r['owner_id'], "email": r['owner_email'],
            "name": f"{r['owner_fn'] or ''} {r['owner_ln'] or ''}".strip() or r['owner_username'] or '—',
        }
        items.append(d)
    return {"hotspots": items, "total": count, "page": page, "limit": limit}


@router.post("/hotspots/{hotspot_id}/block")
async def admin_block(hotspot_id: str, req: BlockRequest, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        await conn.execute(
            "UPDATE wifi_hotspots SET is_blocked = TRUE, blocked_reason = $1 WHERE hotspot_id = $2",
            req.reason[:255], hotspot_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_connect_block', 'wifi_hotspots', $2)
        """, admin['user_id'], f'{{"hotspot_id":"{hotspot_id}","reason":"{req.reason}"}}')
    return {"status": "blocked"}


@router.post("/hotspots/{hotspot_id}/unblock")
async def admin_unblock(hotspot_id: str, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        await conn.execute(
            "UPDATE wifi_hotspots SET is_blocked = FALSE, blocked_reason = '' WHERE hotspot_id = $1",
            hotspot_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_connect_unblock', 'wifi_hotspots', $2)
        """, admin['user_id'], f'{{"hotspot_id":"{hotspot_id}"}}')
    return {"status": "unblocked"}


@router.get("/zones")
async def admin_zones(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    """Analytics grouped by zone. Used for the admin heatmap/analytics panel."""
    await require_admin(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        total = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT zone FROM wifi_hotspots WHERE zone <> '' GROUP BY zone
            ) z
        """) or 0
        rows = await conn.fetch("""
            SELECT zone, country_code,
                COUNT(*) AS hotspots,
                SUM(total_connections) AS connections,
                SUM(total_rewarded_usd) AS rewarded_usd,
                SUM(CASE WHEN is_premium THEN 1 ELSE 0 END) AS premium_count,
                SUM(CASE WHEN is_sponsored THEN 1 ELSE 0 END) AS partner_count
            FROM wifi_hotspots WHERE zone <> ''
            GROUP BY zone, country_code
            ORDER BY connections DESC NULLS LAST
            LIMIT $1 OFFSET $2
        """, limit, offset)
    return {
        "total": total, "page": page, "limit": limit,
        "zones": [{
            "zone": r['zone'] or "", "country_code": r['country_code'] or "",
            "hotspots": r['hotspots'] or 0,
            "connections": r['connections'] or 0,
            "rewarded_usd": str(r['rewarded_usd'] or 0),
            "premium_count": r['premium_count'] or 0,
            "partner_count": r['partner_count'] or 0,
        } for r in rows]
    }


@router.post("/hotspots/{hotspot_id}/sponsor")
async def admin_sponsor(hotspot_id: str, req: SponsorRequest, request: Request):
    admin = await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM wifi_hotspots WHERE hotspot_id = $1", hotspot_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        await conn.execute("""
            UPDATE wifi_hotspots SET is_sponsored = $1, sponsor_name = $2, type = CASE WHEN $1 THEN 'partner' ELSE type END
            WHERE hotspot_id = $3
        """, bool(req.is_sponsored), req.sponsor_name.strip()[:120], hotspot_id)
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'admin_connect_sponsor', 'wifi_hotspots', $2)
        """, admin['user_id'], f'{{"hotspot_id":"{hotspot_id}","sponsor":"{req.sponsor_name}"}}')
    return {"status": "ok"}
