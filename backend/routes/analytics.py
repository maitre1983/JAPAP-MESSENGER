"""
Lightweight analytics events store — used by client-side instrumentation.
Generic endpoint : POST /api/analytics/event  { name, props }
Writes to `analytics_events` table (auto-migrated below).
"""
import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional, Any, Dict
import json as _json

from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class EventPayload(BaseModel):
    name: str
    props: Optional[Dict[str, Any]] = None


@router.post("/event")
async def track_event(payload: EventPayload, request: Request):
    user = await get_current_user(request)
    if not payload.name or len(payload.name) > 80:
        raise HTTPException(status_code=400, detail="Event name required (≤80 chars).")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO analytics_events (user_id, name, props)
            VALUES ($1, $2, $3::jsonb)
        """, user['user_id'], payload.name, _json.dumps(payload.props or {}))
    return {"ok": True}


@router.get("/summary")
async def summary(request: Request, name: Optional[str] = None, limit: int = 20):
    """Admin-facing quick summary of event counts for the last 30 days."""
    from routes.admin import require_admin
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if name:
            rows = await conn.fetch("""
                SELECT DATE_TRUNC('day', created_at) AS day, COUNT(*) AS n
                FROM analytics_events
                WHERE name = $1 AND created_at > NOW() - INTERVAL '30 days'
                GROUP BY day ORDER BY day DESC
            """, name)
            return {"name": name,
                    "days": [{"day": r['day'].isoformat(), "count": int(r['n'])} for r in rows]}
        rows = await conn.fetch("""
            SELECT name, COUNT(*) AS n
            FROM analytics_events
            WHERE created_at > NOW() - INTERVAL '30 days'
            GROUP BY name ORDER BY n DESC LIMIT $1
        """, limit)
        return {"top_events": [{"name": r['name'], "count": int(r['n'])} for r in rows]}
