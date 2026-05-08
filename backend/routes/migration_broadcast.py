"""
JAPAP — Migration Broadcast admin routes (iter153)
==================================================
Admin-only endpoints driving the legacy-user migration email campaign.

POST   /api/admin/migration/broadcast              — create+populate a campaign
GET    /api/admin/migration/broadcast              — list recent campaigns
GET    /api/admin/migration/broadcast/{cid}        — single campaign + stats
POST   /api/admin/migration/broadcast/{cid}/start  — flip to 'running' now
POST   /api/admin/migration/broadcast/{cid}/pause  — pause the worker
POST   /api/admin/migration/broadcast/{cid}/resume — resume from pause
POST   /api/admin/migration/broadcast/{cid}/stop   — terminal stop
GET    /api/admin/migration/broadcast/{cid}/targets?status=&limit=&offset=

All endpoints require admin or superadmin auth.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field

from routes.auth import require_admin, log_admin_action
from services import migration_broadcast as mb

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/migration", tags=["admin-migration-broadcast"])


class CreateBroadcastRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=160)
    start_at: str = Field(..., description="ISO 8601 timestamp (UTC). Worker auto-starts at this moment.")
    daily_limit: int = Field(900, ge=1, le=5000)


@router.post("/broadcast")
async def create_broadcast(req: CreateBroadcastRequest, request: Request):
    admin = await require_admin(request)
    try:
        # Accept "Z" suffix and assume UTC if naive.
        raw = req.start_at.replace("Z", "+00:00")
        start = datetime.fromisoformat(raw)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail="start_at doit être au format ISO 8601 (ex: 2026-04-30T00:00:00Z).")
    try:
        camp = await mb.create_campaign(
            name=req.name,
            start_at=start,
            daily_limit=req.daily_limit,
            created_by=admin.get("user_id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # iter154 — kill switch.
        msg = str(e)
        if msg.startswith("BROADCAST_DISABLED"):
            raise HTTPException(status_code=410, detail=msg)
        raise
    # Auto-flip to running so the worker picks it up at start_at.
    await mb.set_status(camp["campaign_id"], "running")
    camp["status"] = "running"
    await log_admin_action(
        actor_id=admin.get("user_id"), actor_email=admin.get("email", ""),
        action="migration_broadcast.create",
        metadata={"campaign_id": camp["campaign_id"],
                  "daily_limit": req.daily_limit,
                  "start_at": start.isoformat(),
                  "total_targets": camp.get("total_targets", 0)},
    )
    return camp


@router.get("/broadcast")
async def list_broadcasts(request: Request):
    await require_admin(request)
    return {
        "campaigns": await mb.list_campaigns(),
        # iter154 — surface the kill-switch state so the admin UI can show
        # the "Mode broadcast désactivé" banner and disable creation.
        "broadcast_enabled": mb.BROADCAST_ENABLED,
    }


@router.get("/broadcast/{campaign_id}")
async def get_broadcast(campaign_id: str, request: Request):
    await require_admin(request)
    camp = await mb.get_campaign(campaign_id)
    if not camp:
        raise HTTPException(status_code=404, detail="Campagne introuvable")
    return camp


@router.post("/broadcast/{campaign_id}/start")
async def start_broadcast(campaign_id: str, request: Request):
    admin = await require_admin(request)
    try:
        ok = await mb.set_status(campaign_id, "running")
    except RuntimeError as e:
        if str(e).startswith("BROADCAST_DISABLED"):
            raise HTTPException(status_code=410, detail=str(e))
        raise
    if not ok:
        raise HTTPException(status_code=404, detail="Campagne introuvable")
    await log_admin_action(actor_id=admin.get("user_id"),
                           actor_email=admin.get("email", ""),
                           action="migration_broadcast.start",
                           metadata={"campaign_id": campaign_id})
    return {"ok": True, "status": "running"}


@router.post("/broadcast/{campaign_id}/pause")
async def pause_broadcast(campaign_id: str, request: Request):
    admin = await require_admin(request)
    ok = await mb.set_status(campaign_id, "paused")
    if not ok:
        raise HTTPException(status_code=404, detail="Campagne introuvable")
    await log_admin_action(actor_id=admin.get("user_id"),
                           actor_email=admin.get("email", ""),
                           action="migration_broadcast.pause",
                           metadata={"campaign_id": campaign_id})
    return {"ok": True, "status": "paused"}


@router.post("/broadcast/{campaign_id}/resume")
async def resume_broadcast(campaign_id: str, request: Request):
    admin = await require_admin(request)
    try:
        ok = await mb.set_status(campaign_id, "running")
    except RuntimeError as e:
        if str(e).startswith("BROADCAST_DISABLED"):
            raise HTTPException(status_code=410, detail=str(e))
        raise
    if not ok:
        raise HTTPException(status_code=404, detail="Campagne introuvable")
    await log_admin_action(actor_id=admin.get("user_id"),
                           actor_email=admin.get("email", ""),
                           action="migration_broadcast.resume",
                           metadata={"campaign_id": campaign_id})
    return {"ok": True, "status": "running"}


@router.post("/broadcast/{campaign_id}/stop")
async def stop_broadcast(campaign_id: str, request: Request):
    admin = await require_admin(request)
    ok = await mb.set_status(campaign_id, "stopped")
    if not ok:
        raise HTTPException(status_code=404, detail="Campagne introuvable")
    await log_admin_action(actor_id=admin.get("user_id"),
                           actor_email=admin.get("email", ""),
                           action="migration_broadcast.stop",
                           metadata={"campaign_id": campaign_id})
    return {"ok": True, "status": "stopped"}


@router.get("/broadcast/{campaign_id}/targets")
async def list_targets(
    campaign_id: str,
    request: Request,
    status: Optional[str] = Query(None,
        description="pending|sending|sent|delivered|opened|clicked|bounced|failed"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    await require_admin(request)
    rows = await mb.list_targets(campaign_id, status=status, limit=limit, offset=offset)
    return {"targets": rows, "limit": limit, "offset": offset, "count": len(rows)}


# ── iter155 — Smart legacy list classification + cleanup ──────────────
@router.get("/legacy-classification")
async def legacy_classification(request: Request):
    """Preview-only — count legacy users per email tier (1/2/3).

    Tier 1 — Gmail / Outlook / Yahoo / iCloud / Proton / mainstream ISPs.
    Tier 2 — Other syntactically-valid domains (kept for sends).
    Tier 3 — Risky : invalid format / disposable / hard-bounced.

    Returns a structured report the admin UI can render before deciding
    to apply the cleanup. Read-only — does NOT mutate any user.

    iter155 — bulk implementation: preload the bounce set once and
    classify in pure Python instead of one DB query per row.
    """
    await require_admin(request)
    rows, bounce_set = await _legacy_rows_and_bounces()
    counters = {"tier1": 0, "tier2": 0, "tier3": 0,
                "tier3_invalid_format": 0,
                "tier3_disposable_domain": 0,
                "tier3_hard_bounced": 0}
    samples = {1: [], 2: [], 3: []}
    for r in rows:
        info = _classify_local(r["email"], bounce_set)
        tier = info["tier"]
        counters[f"tier{tier}"] += 1
        if tier == 3 and info["reason"]:
            counters[f"tier3_{info['reason']}"] = counters.get(
                f"tier3_{info['reason']}", 0) + 1
        if len(samples[tier]) < 5:
            samples[tier].append({
                "email": r["email"],
                "domain": info["domain"],
                "reason": info["reason"],
            })
    total = counters["tier1"] + counters["tier2"] + counters["tier3"]
    return {
        "total_legacy_pending": total,
        "tiers": counters,
        "deliverable": counters["tier1"] + counters["tier2"],
        "excluded": counters["tier3"],
        "samples": samples,
    }


class CleanupRequest(BaseModel):
    dry_run: bool = True


@router.post("/legacy-cleanup")
async def legacy_cleanup(req: CleanupRequest, request: Request):
    """Apply the iter155 smart cleanup: every Tier-3 legacy user has
    `is_legacy_account=FALSE` + `email_subscribed=FALSE` set so they:
      • can never be picked up by a future broadcast targeting query,
      • receive no transactional mail until they opt back in.

    The user account itself is preserved (no DELETE) so the admin can
    still inspect it via `/api/admin/users-by-balance` etc.

    `dry_run=true` (default) → only counts what *would* be purged.
    `dry_run=false`          → actually mutates the rows.
    """
    admin = await require_admin(request)
    rows, bounce_set = await _legacy_rows_and_bounces()
    purged = 0
    by_reason = {"invalid_format": 0, "disposable_domain": 0,
                 "hard_bounced": 0}
    tier3_user_ids: list[str] = []
    for r in rows:
        info = _classify_local(r["email"], bounce_set)
        if info["tier"] == 3:
            purged += 1
            by_reason[info["reason"]] = by_reason.get(info["reason"], 0) + 1
            tier3_user_ids.append(r["user_id"])

    remaining = None
    if not req.dry_run and tier3_user_ids:
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Bulk update — single statement, even for 28k IDs.
            await conn.execute(
                """UPDATE users
                      SET is_legacy_account = FALSE,
                          email_subscribed = FALSE,
                          updated_at = NOW()
                    WHERE user_id = ANY($1::varchar[])""",
                tier3_user_ids,
            )
        await log_admin_action(
            actor_id=admin.get("user_id"),
            actor_email=admin.get("email", ""),
            action="migration_broadcast.legacy_cleanup",
            metadata={"purged": purged, "by_reason": by_reason},
        )
        # Recount the remaining Tier 1+2 eligible after purge.
        rows2, bounce_set2 = await _legacy_rows_and_bounces()
        remaining = sum(1 for r in rows2
                        if _classify_local(r["email"], bounce_set2)["tier"] in (1, 2))

    return {
        "dry_run": bool(req.dry_run),
        "purged": purged,
        "by_reason": by_reason,
        "remaining_eligible": remaining,
    }


async def _legacy_rows_and_bounces() -> tuple[list, set]:
    """Bulk-load (legacy users, hard-bounce emails) in two queries."""
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT user_id, email FROM users
                WHERE legacy_id IS NOT NULL
                  AND is_legacy_account = TRUE
                  AND migration_completed = FALSE
                  AND COALESCE(email_subscribed, TRUE) = TRUE"""
        )
        bounces = await conn.fetch(
            """SELECT DISTINCT LOWER(email) AS e FROM email_logs
                WHERE event IN ('bounced', 'complained')"""
        )
    bounce_set = {b["e"] for b in bounces if b["e"]}
    return rows, bounce_set


def _classify_local(email: str, bounce_set: set) -> dict:
    """Pure-Python tier classification using a preloaded bounce set.

    Equivalent to `utils.email_validation.classify_email` but without
    the per-row DB roundtrip — meant for bulk passes (28k+ users).
    """
    from utils.email_validation import (
        is_valid_email_format, _DISPOSABLE_DOMAINS,  # noqa: F401
        TIER1_DOMAINS, domain_of,
    )
    if not is_valid_email_format(email or ""):
        return {"tier": 3, "reason": "invalid_format",
                "tier_label": "risky", "domain": ""}
    d = domain_of(email)
    if d in _DISPOSABLE_DOMAINS:
        return {"tier": 3, "reason": "disposable_domain",
                "tier_label": "risky", "domain": d}
    if (email or "").strip().lower() in bounce_set:
        return {"tier": 3, "reason": "hard_bounced",
                "tier_label": "risky", "domain": d}
    if d in TIER1_DOMAINS:
        return {"tier": 1, "reason": "", "tier_label": "mainstream", "domain": d}
    return {"tier": 2, "reason": "", "tier_label": "other_valid", "domain": d}
