"""
Admin Messaging Center — routes

Iter 64 — Phase 1a (backend-only). All endpoints are admin-gated.

Routes :
    GET    /api/admin/messaging/segments                — list + seed on first call
    POST   /api/admin/messaging/segments                — create
    PUT    /api/admin/messaging/segments/{id}           — update
    DELETE /api/admin/messaging/segments/{id}           — delete (non-system only)
    POST   /api/admin/messaging/segments/preview        — count + sample

    GET    /api/admin/messaging/templates               — list
    POST   /api/admin/messaging/templates               — create
    PUT    /api/admin/messaging/templates/{id}          — update
    DELETE /api/admin/messaging/templates/{id}          — delete

    GET    /api/admin/messaging/campaigns               — list
    POST   /api/admin/messaging/campaigns               — create (draft)
    GET    /api/admin/messaging/campaigns/{id}          — detail
    PUT    /api/admin/messaging/campaigns/{id}          — update (draft only)
    DELETE /api/admin/messaging/campaigns/{id}          — delete (draft only)
    POST   /api/admin/messaging/campaigns/{id}/test     — send test to admin
    POST   /api/admin/messaging/campaigns/{id}/send     — enqueue bulk send

    GET    /api/admin/messaging/analytics               — dashboard cards
    GET    /api/admin/messaging/analytics/campaigns/{id}— per-campaign metrics
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Literal
from fastapi import APIRouter, HTTPException, Request, Body
from pydantic import BaseModel, Field
from database import get_pool
from routes.auth import get_current_user
from services.segment_compiler import (
    compile_rules, count_recipients, fetch_recipients,
    SYSTEM_SEGMENTS, SegmentCompileError,
)
from services.email_renderer import render, build_context, wrap_html_for_delivery
from services.ai_template_generator import generate_template as _ai_generate_template
from middleware.rate_limit import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/messaging", tags=["admin-messaging"])


# ══════════════════════════════════════════════════════════════════════════
#  Email cleanliness filter — iter66-Controlled-Release
# ══════════════════════════════════════════════════════════════════════════
import re as _re

# Pragmatic RFC-ish email regex — rejects trailing dots, consecutive dots,
# missing TLD, obvious placeholders.
_VALID_EMAIL_RE = _re.compile(
    r"^(?!.*\.\.)[a-z0-9!#$%&'*+/=?^_`{|}~\-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~\-]+)*"
    r"@[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+$",
    _re.IGNORECASE,
)

# Known disposable / suspicious bot domains — block before send
_SUSPICIOUS_DOMAINS = {
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.org",
    "yopmail.com", "trashmail.com", "getnada.com", "throwawaymail.com",
    "maildrop.cc", "fakeinbox.com", "tempmailo.com", "dispostable.com",
    "sharklasers.com", "mintemail.com", "mailnesia.com", "mohmal.com",
    "emailondeck.com", "moakt.com",
}


def _is_clean_email(email: str) -> tuple[bool, str]:
    """Return (ok, reason). Checks format, length, domain reputation."""
    if not email or not isinstance(email, str):
        return False, "missing"
    e = email.strip().lower()
    if len(e) > 254:
        return False, "too_long"
    if not _VALID_EMAIL_RE.match(e):
        return False, "invalid_format"
    domain = e.rsplit("@", 1)[-1]
    if domain in _SUSPICIOUS_DOMAINS:
        return False, "disposable_domain"
    if any(w in e for w in ("noreply", "no-reply", "donotreply", "postmaster@", "mailer-daemon")):
        return False, "no_reply_address"
    return True, "ok"


async def _apply_cleanliness_filter(recipients: list[dict]) -> tuple[list[dict], dict]:
    """Strip unsendable recipients; returns (kept, stats).

    Filters applied:
      1. Invalid email format / too long
      2. Disposable/suspicious domains
      3. Bot-like `noreply@` addresses
      4. In-batch duplicate emails (case-insensitive)
      5. Banned users (users.is_banned = TRUE if the column exists)
      6. Suspended users (users.status = 'suspended' if present)
    """
    stats = {"invalid_format": 0, "disposable_domain": 0, "no_reply_address": 0,
             "too_long": 0, "missing": 0, "duplicate": 0, "banned": 0}
    out: list[dict] = []
    seen: set[str] = set()
    for u in recipients:
        em = (u.get("email") or "").strip().lower()
        ok, reason = _is_clean_email(em)
        if not ok:
            stats[reason] = stats.get(reason, 0) + 1
            continue
        if em in seen:
            stats["duplicate"] += 1
            continue
        if u.get("is_banned") or u.get("status") == "suspended":
            stats["banned"] += 1
            continue
        seen.add(em)
        out.append(u)
    return out, stats


async def _require_admin(request: Request):
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def _audit(conn, actor: str, action: str, resource: str, details: dict):
    try:
        await conn.execute(
            "INSERT INTO audit_logs (user_id, action, resource, details) VALUES ($1,$2,$3,$4)",
            actor, action, resource, json.dumps(details),
        )
    except Exception as e:
        logger.warning(f"audit log insert failed: {e}")


_EMAIL_RE = __import__("re").compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~.\-]+@[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+$", __import__("re").IGNORECASE,
)


async def _merge_individual_targets(conn, req: "CampaignIn") -> list | None:
    """Persist both kinds of individual targets in the single `individual_user_ids`
    JSONB column as a list of mixed entries:
      - `user_id` strings (matched users)
      - `{"email": "..."}` objects (external emails)
    Send-time code expands them accordingly.

    Dedup rules:
      - user_id strings are deduplicated among themselves
      - external emails are dropped if they collide with an already-selected user_id
        (resolved via DB lookup) or with another external email
      - invalid emails are silently dropped
    """
    out: list = []
    seen_uids: set = set()
    seen_emails: set = set()

    uids_in = [str(u).strip() for u in (req.individual_user_ids or []) if str(u).strip()]
    # Resolve emails for user_ids so we can dedup external emails against them
    known_emails: set = set()
    if uids_in:
        rows = await conn.fetch(
            "SELECT user_id, email FROM users WHERE user_id = ANY($1::text[])",
            uids_in,
        )
        known_emails = {r["email"].lower() for r in rows if r["email"]}

    for u in uids_in:
        if u not in seen_uids:
            out.append(u)
            seen_uids.add(u)

    for em in (req.individual_emails or []):
        e = str(em).strip().lower()
        if not e:
            continue
        if not _EMAIL_RE.match(e):
            continue  # silently drop invalid addresses
        if e in seen_emails or e in known_emails:
            continue  # already covered by a user_id or earlier email
        out.append({"email": e})
        seen_emails.add(e)
    return out or None


# ══════════════════════════════════════════════════════════════════════════
#  USER SEARCH — for individual targeting
# ══════════════════════════════════════════════════════════════════════════

@router.get("/users/search")
async def search_users(request: Request, q: str = "", limit: int = 15):
    """Live search over email / username / first_name / last_name.

    Returns minimal JSON per user + subscription flag so the UI can warn
    on unsubscribed targets. Admin-gated. Max 25 results.
    """
    await _require_admin(request)
    needle = (q or "").strip()
    if not needle:
        return {"items": []}
    limit = max(1, min(int(limit), 25))
    pattern = f"%{needle.lower()}%"
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT user_id, email, username, first_name, last_name,
                      is_pro, is_active, email_subscribed,
                      migration_pending, COALESCE(connect_points, 0) AS connect_points,
                      updated_at
               FROM users
               WHERE is_active = TRUE
                 AND email IS NOT NULL AND email <> ''
                 AND (LOWER(email)      LIKE $1
                   OR LOWER(username)   LIKE $1
                   OR LOWER(first_name) LIKE $1
                   OR LOWER(last_name)  LIKE $1)
               ORDER BY
                 CASE WHEN LOWER(email) = $2 THEN 0 ELSE 1 END,
                 updated_at DESC NULLS LAST
               LIMIT $3""",
            pattern, needle.lower(), limit,
        )
    items = []
    for r in rows:
        items.append({
            "user_id": r["user_id"],
            "email": r["email"],
            "username": r["username"] or "",
            "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["username"] or r["email"],
            "is_pro": bool(r["is_pro"]),
            "is_active": bool(r["is_active"]),
            "email_subscribed": r["email_subscribed"] is not False,
            "migration_pending": bool(r["migration_pending"]),
        })
    return {"items": items}


# ══════════════════════════════════════════════════════════════════════════
#  SEGMENTS
# ══════════════════════════════════════════════════════════════════════════

class SegmentIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = ""
    rules: List[dict] = []


async def _seed_system_segments(conn):
    existing = set(r["segment_id"] for r in await conn.fetch(
        "SELECT segment_id FROM email_segments WHERE is_system = TRUE"))
    for seg_id, name, desc, rules in SYSTEM_SEGMENTS:
        if seg_id in existing:
            continue
        try:
            est = await count_recipients(conn, rules)
        except Exception:
            est = 0
        await conn.execute(
            """INSERT INTO email_segments
                 (segment_id, name, description, rules, is_system, estimated_count, estimated_at)
               VALUES ($1,$2,$3,$4,TRUE,$5,NOW())
               ON CONFLICT (segment_id) DO NOTHING""",
            seg_id, name, desc, json.dumps(rules), est,
        )


@router.get("/segments")
async def list_segments(request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed_system_segments(conn)
        rows = await conn.fetch(
            "SELECT segment_id, name, description, rules, is_system, "
            "       estimated_count, estimated_at, created_at "
            "FROM email_segments ORDER BY is_system DESC, created_at DESC"
        )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("rules"), str):
            d["rules"] = json.loads(d["rules"])
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        d["estimated_at"] = d["estimated_at"].isoformat() if d.get("estimated_at") else None
        out.append(d)
    return {"items": out}


@router.post("/segments")
async def create_segment(req: SegmentIn, request: Request):
    admin = await _require_admin(request)
    seg_id = f"seg_{uuid.uuid4().hex[:12]}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            est = await count_recipients(conn, req.rules)
        except SegmentCompileError as e:
            raise HTTPException(status_code=400, detail=str(e))
        await conn.execute(
            """INSERT INTO email_segments
                 (segment_id, name, description, rules, is_system,
                  estimated_count, estimated_at, created_by)
               VALUES ($1,$2,$3,$4,FALSE,$5,NOW(),$6)""",
            seg_id, req.name, req.description or "", json.dumps(req.rules), est, admin["user_id"],
        )
        await _audit(conn, admin["user_id"], "messaging.segment.create", seg_id,
                     {"name": req.name, "estimated_count": est})
    return {"segment_id": seg_id, "estimated_count": est}


@router.put("/segments/{segment_id}")
async def update_segment(segment_id: str, req: SegmentIn, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_system FROM email_segments WHERE segment_id = $1", segment_id)
        if not row:
            raise HTTPException(status_code=404, detail="Segment introuvable")
        if row["is_system"]:
            raise HTTPException(status_code=403, detail="Segment système non modifiable")
        try:
            est = await count_recipients(conn, req.rules)
        except SegmentCompileError as e:
            raise HTTPException(status_code=400, detail=str(e))
        await conn.execute(
            """UPDATE email_segments
               SET name = $1, description = $2, rules = $3,
                   estimated_count = $4, estimated_at = NOW(), updated_at = NOW()
               WHERE segment_id = $5""",
            req.name, req.description or "", json.dumps(req.rules), est, segment_id,
        )
        await _audit(conn, admin["user_id"], "messaging.segment.update", segment_id,
                     {"estimated_count": est})
    return {"ok": True, "estimated_count": est}


@router.delete("/segments/{segment_id}")
async def delete_segment(segment_id: str, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_system FROM email_segments WHERE segment_id = $1", segment_id)
        if not row:
            raise HTTPException(status_code=404, detail="Segment introuvable")
        if row["is_system"]:
            raise HTTPException(status_code=403, detail="Segment système non supprimable")
        await conn.execute("DELETE FROM email_segments WHERE segment_id = $1", segment_id)
        await _audit(conn, admin["user_id"], "messaging.segment.delete", segment_id, {})
    return {"deleted": True}


class SegmentPreviewRequest(BaseModel):
    rules: List[dict] = []
    segment_id: Optional[str] = None
    sample_size: int = Field(5, ge=0, le=50)


@router.post("/segments/preview")
async def segment_preview(req: SegmentPreviewRequest, request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rules = req.rules
        if req.segment_id and not rules:
            row = await conn.fetchrow(
                "SELECT rules FROM email_segments WHERE segment_id = $1", req.segment_id)
            if not row:
                raise HTTPException(status_code=404, detail="Segment introuvable")
            rules = row["rules"] if isinstance(row["rules"], list) else json.loads(row["rules"])
        try:
            total = await count_recipients(conn, rules)
            sample = []
            if req.sample_size > 0:
                sample_rows = await fetch_recipients(conn, rules, limit=req.sample_size)
                sample = [{
                    "user_id": r["user_id"],
                    "email": r["email"],
                    "name": f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip()
                             or r.get("username") or r["email"],
                } for r in sample_rows]
        except SegmentCompileError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"count": total, "sample": sample}


# ══════════════════════════════════════════════════════════════════════════
#  TEMPLATES
# ══════════════════════════════════════════════════════════════════════════

class TemplateIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    language: str = "fr"
    subject: str = Field(..., min_length=1, max_length=200)
    preview_text: Optional[str] = ""
    body_html: str = Field(..., min_length=1)
    body_text: Optional[str] = ""
    cta_label: Optional[str] = ""
    cta_url: Optional[str] = ""
    category: str = "custom"
    source: Literal["manual", "ai"] = "manual"
    ai_prompt: Optional[dict] = None


@router.get("/templates")
async def list_templates(request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT template_id, name, language, subject, preview_text, body_html, "
            "body_text, cta_label, cta_url, category, source, ai_prompt, "
            "created_at, updated_at FROM email_templates ORDER BY updated_at DESC"
        )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("ai_prompt"), str):
            try:
                d["ai_prompt"] = json.loads(d["ai_prompt"])
            except Exception:
                d["ai_prompt"] = None
        for k in ("created_at", "updated_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        out.append(d)
    return {"items": out}


@router.post("/templates")
async def create_template(req: TemplateIn, request: Request):
    admin = await _require_admin(request)
    template_id = f"tpl_{uuid.uuid4().hex[:12]}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO email_templates
                 (template_id, name, language, subject, preview_text, body_html, body_text,
                  cta_label, cta_url, category, source, ai_prompt, created_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
            template_id, req.name, req.language, req.subject, req.preview_text or "",
            req.body_html, req.body_text or "", req.cta_label or "", req.cta_url or "",
            req.category, req.source,
            json.dumps(req.ai_prompt) if req.ai_prompt else None,
            admin["user_id"],
        )
        await _audit(conn, admin["user_id"], "messaging.template.create", template_id,
                     {"name": req.name, "source": req.source})
    return {"template_id": template_id}


@router.put("/templates/{template_id}")
async def update_template(template_id: str, req: TemplateIn, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM email_templates WHERE template_id = $1", template_id)
        if not row:
            raise HTTPException(status_code=404, detail="Template introuvable")
        await conn.execute(
            """UPDATE email_templates SET
                 name=$1, language=$2, subject=$3, preview_text=$4, body_html=$5,
                 body_text=$6, cta_label=$7, cta_url=$8, category=$9, source=$10,
                 ai_prompt=$11, updated_at=NOW()
               WHERE template_id = $12""",
            req.name, req.language, req.subject, req.preview_text or "", req.body_html,
            req.body_text or "", req.cta_label or "", req.cta_url or "", req.category, req.source,
            json.dumps(req.ai_prompt) if req.ai_prompt else None,
            template_id,
        )
        await _audit(conn, admin["user_id"], "messaging.template.update", template_id, {})
    return {"ok": True}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM email_templates WHERE template_id = $1", template_id)
        await _audit(conn, admin["user_id"], "messaging.template.delete", template_id, {})
    return {"deleted": True}


# ── AI generate (Claude Sonnet 4.5) ──────────────────────────────────────

class AIGenerateRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=500)
    audience: str = Field("", max_length=200)
    tone: str = Field("warm", max_length=50)
    language: str = Field("fr", max_length=8)
    cta_type: str = Field("primary", max_length=50)
    extra_context: str = Field("", max_length=1000)


@router.post("/templates/generate-ai")
async def templates_generate_ai(req: AIGenerateRequest, request: Request):
    """Generate a template draft via Claude Sonnet 4.5. Admin can edit
    before saving — we do NOT persist the result automatically."""
    admin = await _require_admin(request)
    try:
        data = await _ai_generate_template(
            goal=req.goal, audience=req.audience, tone=req.tone,
            language=req.language, cta_type=req.cta_type,
            extra_context=req.extra_context,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _audit(conn, admin["user_id"], "messaging.template.ai_generate", "",
                     {"goal": req.goal, "tone": req.tone, "language": req.language})
    return {
        "template": data,
        "ai_prompt": {
            "goal": req.goal, "audience": req.audience, "tone": req.tone,
            "language": req.language, "cta_type": req.cta_type,
            "extra_context": req.extra_context,
        },
    }


# ══════════════════════════════════════════════════════════════════════════
#  CAMPAIGNS
# ══════════════════════════════════════════════════════════════════════════

class CampaignIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    template_id: Optional[str] = None
    subject: str = Field(..., min_length=1, max_length=200)
    preview_text: Optional[str] = ""
    body_html: str = Field(..., min_length=1)
    body_text: Optional[str] = ""
    cta_label: Optional[str] = ""
    cta_url: Optional[str] = ""
    language: str = "fr"
    segment_id: Optional[str] = None
    individual_user_ids: Optional[List[str]] = None
    individual_emails: Optional[List[str]] = None   # NEW: external/arbitrary emails


@router.get("/campaigns")
async def list_campaigns(request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT campaign_id, name, status, language, subject, segment_id, "
            "sent_count, delivered_count, opened_count, clicked_count, "
            "bounced_count, unsub_count, created_at, completed_at "
            "FROM email_campaigns ORDER BY created_at DESC LIMIT 200"
        )
    out = []
    for r in rows:
        d = dict(r)
        for k in ("created_at", "completed_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        out.append(d)
    return {"items": out}


@router.post("/campaigns")
async def create_campaign(req: CampaignIn, request: Request):
    admin = await _require_admin(request)
    cid = f"cmp_{uuid.uuid4().hex[:12]}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        merged_targets = await _merge_individual_targets(conn, req)
        await conn.execute(
            """INSERT INTO email_campaigns
                 (campaign_id, name, status, template_id, subject, preview_text,
                  body_html, body_text, cta_label, cta_url, language,
                  segment_id, individual_user_ids, created_by)
               VALUES ($1,$2,'draft',$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
            cid, req.name, req.template_id, req.subject, req.preview_text or "",
            req.body_html, req.body_text or "", req.cta_label or "", req.cta_url or "",
            req.language, req.segment_id,
            json.dumps(merged_targets) if merged_targets else None,
            admin["user_id"],
        )
        await _audit(conn, admin["user_id"], "messaging.campaign.create", cid,
                     {"name": req.name, "segment_id": req.segment_id})
    return {"campaign_id": cid}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM email_campaigns WHERE campaign_id = $1", campaign_id)
        if not row:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
    d = dict(row)
    for k in ("created_at", "updated_at", "scheduled_at", "started_at", "completed_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    for k in ("segment_rules_snapshot", "individual_user_ids"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


@router.put("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, req: CampaignIn, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM email_campaigns WHERE campaign_id = $1", campaign_id)
        if not row:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
        if row["status"] != "draft":
            raise HTTPException(status_code=400, detail="Seules les campagnes en brouillon peuvent être modifiées")
        merged_targets = await _merge_individual_targets(conn, req)
        await conn.execute(
            """UPDATE email_campaigns SET
                 name=$1, template_id=$2, subject=$3, preview_text=$4,
                 body_html=$5, body_text=$6, cta_label=$7, cta_url=$8, language=$9,
                 segment_id=$10, individual_user_ids=$11, updated_at=NOW()
               WHERE campaign_id=$12""",
            req.name, req.template_id, req.subject, req.preview_text or "",
            req.body_html, req.body_text or "", req.cta_label or "", req.cta_url or "",
            req.language, req.segment_id,
            json.dumps(merged_targets) if merged_targets else None,
            campaign_id,
        )
        await _audit(conn, admin["user_id"], "messaging.campaign.update", campaign_id, {})
    return {"ok": True}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str, request: Request):
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM email_campaigns WHERE campaign_id = $1", campaign_id)
        if not row:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
        if row["status"] != "draft":
            raise HTTPException(status_code=400, detail="Seules les campagnes en brouillon peuvent être supprimées")
        await conn.execute("DELETE FROM email_campaigns WHERE campaign_id = $1", campaign_id)
        await _audit(conn, admin["user_id"], "messaging.campaign.delete", campaign_id, {})
    return {"deleted": True}


class CampaignTestRequest(BaseModel):
    recipient_email: Optional[str] = None  # defaults to admin's own email


@router.post("/campaigns/{campaign_id}/test")
async def test_campaign(campaign_id: str, req: CampaignTestRequest, request: Request):
    """Send ONE test email to the admin (or an override email). Does NOT
    touch the queue, does NOT increment campaign counters, does NOT mark
    the campaign as sent. Audit-logged."""
    admin = await _require_admin(request)
    pool = await get_pool()
    to_email = req.recipient_email or admin.get("email")
    if not to_email:
        raise HTTPException(status_code=400, detail="Aucune adresse de destination")

    async with pool.acquire() as conn:
        c = await conn.fetchrow(
            "SELECT * FROM email_campaigns WHERE campaign_id = $1", campaign_id)
        if not c:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
        admin_row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1", admin["user_id"])
        ctx = await build_context(conn, dict(admin_row), campaign_id=campaign_id)

    subject = render(c["subject"], ctx, html_safe=False)
    body_html_user = render(c["body_html"], ctx, html_safe=False)
    body_text = render(c["body_text"] or "", ctx, html_safe=False)
    full_html = wrap_html_for_delivery(
        body_html_user, ctx, c["cta_label"] or "", c["cta_url"] or "")

    from services.email_service import send_email
    ok = await send_email(
        to=to_email,
        subject=f"[TEST] {subject}",
        html=full_html,
        text=body_text,
    )
    async with pool.acquire() as conn:
        await _audit(conn, admin["user_id"], "messaging.campaign.test", campaign_id,
                     {"recipient": to_email, "ok": ok})
    return {"ok": ok, "to": to_email}


class CampaignSendRequest(BaseModel):
    confirm: bool = False
    force: bool = False  # Required to bypass the audience cap


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(campaign_id: str, req: CampaignSendRequest, request: Request):
    """Snapshot audience → enqueue one row per recipient → mark campaign as
    'sending'. The background worker drains the queue asynchronously.

    Guard-rails:
      • `confirm=true` is mandatory.
      • Audience is filtered through `_apply_cleanliness_filter()` (strips
        invalid/disposable/duplicate/banned recipients).
      • Filtered audience size is capped by admin setting
        `messaging_max_audience_per_campaign` (default 200). Sends above the
        cap require `force=true` explicitly in the request body.
      • Rate-limited to 5 sends per admin per minute.
    """
    admin = await _require_admin(request)
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Confirmation explicite requise (confirm=true)")

    # ── DB-backed rate limiter (5 sends / min / admin) ─────────────────────
    # Uses audit_logs timestamps. Safe across worker restarts, no in-memory state.
    pool = await get_pool()
    async with pool.acquire() as _rlc:
        recent = await _rlc.fetchval(
            "SELECT COUNT(*) FROM audit_logs "
            "WHERE user_id = $1 AND action = 'messaging.campaign.send' "
            "AND created_at > NOW() - INTERVAL '60 seconds'",
            admin["user_id"],
        )
        if int(recent or 0) >= 5:
            raise HTTPException(
                status_code=429,
                detail="Rate limit atteint (5 envois/min). Réessayez dans 60s.",
            )

    async with pool.acquire() as conn:
        c = await conn.fetchrow(
            "SELECT * FROM email_campaigns WHERE campaign_id = $1", campaign_id)
        if not c:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
        if c["status"] not in ("draft", "paused"):
            raise HTTPException(status_code=400, detail=f"Statut invalide : {c['status']}")

        # Resolve audience
        recipients: list = []
        external_emails: list = []
        rules_snapshot: list = []
        if c["individual_user_ids"]:
            raw = c["individual_user_ids"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            user_ids = [x for x in raw if isinstance(x, str)]
            external_emails = [x["email"].lower() for x in raw
                               if isinstance(x, dict) and x.get("email")]
            if user_ids:
                rows = await conn.fetch(
                    "SELECT u.user_id, u.email, u.first_name, u.last_name, u.username, "
                    "u.country, u.language, COALESCE(u.connect_points,0) AS connect_points, "
                    "u.is_pro, u.updated_at, u.country_code "
                    "FROM users u WHERE u.user_id = ANY($1::text[]) "
                    "AND u.is_active = TRUE AND u.email IS NOT NULL AND u.email <> '' "
                    "AND (u.email_subscribed IS NULL OR u.email_subscribed = TRUE)",
                    user_ids,
                )
                recipients = [dict(r) for r in rows]
            # External emails: try to match a user — otherwise send as anonymous
            for em in external_emails:
                known = await conn.fetchrow(
                    "SELECT u.user_id, u.email, u.first_name, u.last_name, u.username, "
                    "u.country, u.language, COALESCE(u.connect_points,0) AS connect_points, "
                    "u.is_pro, u.updated_at, u.country_code "
                    "FROM users u WHERE u.email = $1 AND u.is_active = TRUE "
                    "AND (u.email_subscribed IS NULL OR u.email_subscribed = TRUE)",
                    em,
                )
                if known:
                    if not any(x["user_id"] == known["user_id"] for x in recipients):
                        recipients.append(dict(known))
                else:
                    # Anonymous external recipient
                    recipients.append({
                        "user_id": None, "email": em, "first_name": "", "last_name": "",
                        "username": None, "country": None, "language": "fr",
                        "connect_points": 0, "is_pro": False, "updated_at": None,
                        "country_code": None,
                    })
        elif c["segment_id"]:
            seg = await conn.fetchrow(
                "SELECT rules FROM email_segments WHERE segment_id = $1", c["segment_id"])
            if not seg:
                raise HTTPException(status_code=400, detail="Segment introuvable")
            rules_snapshot = seg["rules"] if isinstance(seg["rules"], list) else json.loads(seg["rules"])
            # fetch_recipients returns needed fields already
            from services.segment_compiler import fetch_recipients as _fr
            # We need updated_at too — re-fetch with extra column
            where_sql, params = compile_rules(rules_snapshot)
            sql = (
                "SELECT u.user_id, u.email, u.first_name, u.last_name, u.username, "
                "u.country, u.language, COALESCE(u.connect_points, 0) AS connect_points, "
                "u.is_pro, u.updated_at, u.country_code "
                "FROM users u "
                "WHERE u.is_active = TRUE "
                "AND u.email IS NOT NULL AND u.email <> '' "
                "AND (u.email_subscribed = TRUE OR u.email_subscribed IS NULL)"
                + where_sql
            )
            rows = await conn.fetch(sql, *params)
            recipients = [dict(r) for r in rows]
        else:
            raise HTTPException(status_code=400, detail="Audience requise (segment_id, individual_user_ids ou individual_emails)")

        if not recipients:
            raise HTTPException(status_code=400, detail="Audience vide — aucune adresse correspondante")

        # ── Cleanliness filter (iter66-Controlled-Release) ──────────────────
        # Attach `is_banned` + `status` fields defensively (may not exist).
        for u in recipients:
            if "is_banned" not in u:
                u["is_banned"] = False
            if "status" not in u:
                u["status"] = None
        kept, filter_stats = await _apply_cleanliness_filter(recipients)
        dropped = len(recipients) - len(kept)
        if not kept:
            raise HTTPException(
                status_code=400,
                detail=f"Audience vide après filtrage. Dropped: {filter_stats}",
            )
        recipients = kept

        # ── Audience cap ────────────────────────────────────────────────────
        from services.settings_service import get_int as _gi
        cap = max(1, await _gi("messaging_max_audience_per_campaign", 200))
        if len(recipients) > cap and not req.force:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Audience ({len(recipients)}) dépasse le plafond de {cap}. "
                    f"Ajoutez `force=true` pour forcer. "
                    f"Filtrage a retiré {dropped} entrée(s): {filter_stats}"
                ),
            )

        # Flip status first so concurrent sends can't race
        await conn.execute(
            """UPDATE email_campaigns SET status='sending', started_at = NOW(),
                   segment_rules_snapshot = $1, confirmed_by = $2
               WHERE campaign_id = $3""",
            json.dumps(rules_snapshot), admin["user_id"], campaign_id,
        )

        # Enqueue (idempotent thanks to UNIQUE(campaign_id, recipient_user_id))
        enqueued = 0
        for u in recipients:
            ctx = await build_context(conn, u, campaign_id=campaign_id)
            subject = render(c["subject"], ctx, html_safe=False)
            body_html_user = render(c["body_html"], ctx, html_safe=False)
            body_text = render(c["body_text"] or "", ctx, html_safe=False)
            full_html = wrap_html_for_delivery(
                body_html_user, ctx, c["cta_label"] or "", c["cta_url"] or "")
            try:
                if u["user_id"] is None:
                    # Anonymous external recipient — no unique-index dedup
                    # available, so guard by explicit SELECT first.
                    already = await conn.fetchval(
                        "SELECT 1 FROM email_send_queue "
                        "WHERE campaign_id = $1 AND recipient_user_id IS NULL "
                        "AND recipient_email = $2 LIMIT 1",
                        campaign_id, u["email"],
                    )
                    if already:
                        continue
                    await conn.execute(
                        """INSERT INTO email_send_queue
                             (campaign_id, recipient_user_id, recipient_email,
                              rendered_subject, rendered_html, rendered_text, status)
                           VALUES ($1, NULL, $2, $3, $4, $5, 'pending')""",
                        campaign_id, u["email"], subject, full_html, body_text,
                    )
                else:
                    await conn.execute(
                        """INSERT INTO email_send_queue
                             (campaign_id, recipient_user_id, recipient_email,
                              rendered_subject, rendered_html, rendered_text, status)
                           VALUES ($1,$2,$3,$4,$5,$6,'pending')
                           ON CONFLICT (campaign_id, recipient_user_id) DO NOTHING""",
                        campaign_id, u["user_id"], u["email"],
                        subject, full_html, body_text,
                    )
                enqueued += 1
            except Exception as e:
                logger.warning(f"enqueue skip for recipient {u.get('email')}: {e}")

        await _audit(conn, admin["user_id"], "messaging.campaign.send", campaign_id,
                     {"enqueued": enqueued, "audience_size": len(recipients),
                      "dropped_filter": dropped, "filter_stats": filter_stats,
                      "cap": cap, "forced": bool(req.force)})

    return {"status": "sending", "enqueued": enqueued,
            "audience_size": len(recipients),
            "dropped_by_filter": dropped, "filter_stats": filter_stats,
            "cap_applied": cap, "forced": bool(req.force)}


# ══════════════════════════════════════════════════════════════════════════
#  TEMPLATE → DIRECT SEND (iter94 — Go-Live quick win)
# ══════════════════════════════════════════════════════════════════════════
#
# Allows an admin to pick a template, pick an audience, and send in ONE click
# without manually creating a draft campaign first.
#
# Batch-splitting is built-in for the special "Migration JAPAP 1.0 → 4.0"
# audience: it is ALWAYS served as 5000-user slices ordered by user_id so the
# admin can progressively drain the 28k legacy users through Resend without
# blowing the bounce rate / spam score.
# ══════════════════════════════════════════════════════════════════════════

_MIGRATION_SEG_ID = "seg_migration_1to4"
_MIGRATION_BATCH_SIZE = 5000


async def _background_enqueue_recipients(campaign_id: str, tpl: dict, recipients: list):
    """Background fire-and-forget task that builds per-user contexts,
    renders the template, and bulk-inserts the queue rows.
    Runs OUTSIDE the request lifecycle so the admin gets an immediate
    response even for 5000-recipient migration batches.

    Optimisation: all the campaign-level context (logo URL, CTA, app URL,
    tracking pixel base) is resolved ONCE up-front. Per-user we only
    compute first_name, email, unsubscribe_url, tracking_pixel. No
    per-user DB roundtrip → enqueuing 5000 users takes ~3s instead of 3+min.
    """
    import asyncio as _asyncio
    import traceback as _tb
    from services.email_renderer import (
        _resolve_logo_url, _frontend_url, _homepage_url,
        build_unsubscribe_url, build_tracking_pixel,
    )
    logger.info(
        "Background enqueue START for campaign %s (recipients=%d)",
        campaign_id, len(recipients),
    )
    try:
        # ── Campaign-constant context (computed once) ──
        logo_url = await _resolve_logo_url()
        app_url = _frontend_url()
        homepage_url = _homepage_url()

        subject_tpl = tpl["subject"]
        body_html_tpl = tpl["body_html"]
        body_text_tpl = tpl["body_text"] or ""
        cta_label = tpl["cta_label"] or ""
        cta_url = tpl["cta_url"] or ""

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows: list = []
            for idx, u in enumerate(recipients):
                try:
                    uid = u.get("user_id")
                    ctx = {
                        "first_name": (u.get("first_name") or u.get("username") or "Utilisateur"),
                        "last_name": u.get("last_name") or "",
                        "email": u.get("email") or "",
                        "country": u.get("country") or u.get("country_code") or "",
                        "language": u.get("language") or "",
                        "plan_name": "Pro" if u.get("is_pro") else "Free",
                        "referral_count": 0,
                        "wallet_balance": "0",
                        "pending_tasks": 0,
                        "last_active_days": 0,
                        "connect_points": int(u.get("connect_points") or 0),
                        "unsubscribe_url": build_unsubscribe_url(uid) if uid else "",
                        "campaign_id": campaign_id,
                        "app_url": app_url,
                        "logo_url": logo_url,
                        "homepage_url": homepage_url,
                        "tracking_pixel": build_tracking_pixel(campaign_id, uid) if uid else "",
                        "cta_url": cta_url,
                        "cta_label": cta_label,
                    }
                    subject = render(subject_tpl, ctx, html_safe=False)
                    body_html_user = render(body_html_tpl, ctx, html_safe=False)
                    body_text = render(body_text_tpl, ctx, html_safe=False)
                    full_html = wrap_html_for_delivery(body_html_user, ctx, cta_label, cta_url)
                    rows.append(
                        (campaign_id, uid, u["email"], subject, full_html, body_text)
                    )
                except Exception as e_row:
                    logger.warning(
                        "bg enqueue row %d skipped for user %s: %s",
                        idx, u.get("user_id"), e_row,
                    )
                # Yield control every 200 users so we don't starve the event loop.
                if (idx + 1) % 200 == 0:
                    await _asyncio.sleep(0)
            if rows:
                # Chunk the bulk insert to avoid PG packet-size limits on
                # 5000 × HTML rendered bodies.
                CHUNK = 500
                for i in range(0, len(rows), CHUNK):
                    await conn.executemany(
                        """INSERT INTO email_send_queue
                             (campaign_id, recipient_user_id, recipient_email,
                              rendered_subject, rendered_html, rendered_text, status)
                           VALUES ($1,$2,$3,$4,$5,$6,'pending')
                           ON CONFLICT (campaign_id, recipient_user_id) DO NOTHING""",
                        rows[i:i + CHUNK],
                    )
            logger.info(
                "Background enqueue COMPLETE for campaign %s: %d rows inserted",
                campaign_id, len(rows),
            )
    except Exception as e:
        logger.error(
            "Background enqueue FAILED for campaign %s: %s\n%s",
            campaign_id, e, _tb.format_exc(),
        )
        # Flag campaign as failed so the admin sees the issue in the dashboard
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE email_campaigns SET status='failed' WHERE campaign_id=$1",
                    campaign_id,
                )
        except Exception:
            pass


async def _ensure_campaign_batch_columns(conn):
    """Idempotent ALTER to add batch tracking columns to email_campaigns."""
    await conn.execute("""
        ALTER TABLE email_campaigns
          ADD COLUMN IF NOT EXISTS batch_key TEXT,
          ADD COLUMN IF NOT EXISTS batch_index INTEGER,
          ADD COLUMN IF NOT EXISTS batch_total INTEGER;
    """)
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS email_campaigns_batch_key_uniq
          ON email_campaigns (batch_key)
          WHERE batch_key IS NOT NULL;
    """)


async def _resolve_segment_recipients(conn, segment_id: str):
    """Compile segment rules and return the FULL ordered recipient list
    (user_id ascending). Used for batching — do NOT stream (need stable
    ORDER BY for consistent batch slicing across admin sessions).
    """
    seg = await conn.fetchrow(
        "SELECT rules FROM email_segments WHERE segment_id = $1", segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment introuvable")
    rules = seg["rules"] if isinstance(seg["rules"], list) else json.loads(seg["rules"])
    where_sql, params = compile_rules(rules)
    sql = (
        "SELECT u.user_id, u.email, u.first_name, u.last_name, u.username, "
        "u.country, u.language, COALESCE(u.connect_points, 0) AS connect_points, "
        "u.is_pro, u.updated_at, u.country_code "
        "FROM users u "
        "WHERE u.is_active = TRUE "
        "AND u.email IS NOT NULL AND u.email <> '' "
        "AND (u.email_subscribed = TRUE OR u.email_subscribed IS NULL)"
        + where_sql +
        " ORDER BY u.user_id ASC"
    )
    rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows], rules


async def _migration_batch_status(conn):
    """Return the per-batch status list for the Migration 1→4 audience.

    Status values (per batch):
        • not_sent — no campaign has ever been created for this batch
        • pending|sending|sent|failed|paused — latest campaign's status

    Performance: uses COUNT(*) instead of fetching the full 28k-row audience
    (the full list is only needed at actual send time — see send-to-audience).
    """
    # Cheap COUNT(*) — avoids materialising 28k user rows just to show the
    # list of 6 batch labels on /audience-options.
    seg = await conn.fetchrow(
        "SELECT rules FROM email_segments WHERE segment_id = $1", _MIGRATION_SEG_ID)
    if not seg:
        return []
    rules = seg["rules"] if isinstance(seg["rules"], list) else json.loads(seg["rules"])
    where_sql, params = compile_rules(rules)
    total = await conn.fetchval(
        "SELECT COUNT(*) FROM users u "
        "WHERE u.is_active = TRUE "
        "AND u.email IS NOT NULL AND u.email <> '' "
        "AND (u.email_subscribed = TRUE OR u.email_subscribed IS NULL)"
        + where_sql,
        *params,
    )
    total = int(total or 0)
    if total == 0:
        return []
    n_batches = (total + _MIGRATION_BATCH_SIZE - 1) // _MIGRATION_BATCH_SIZE

    # Build batch_key → latest campaign status
    rows = await conn.fetch(
        """SELECT batch_key, status, campaign_id, sent_count, bounced_count,
                  started_at, completed_at
             FROM email_campaigns
             WHERE batch_key LIKE $1
             ORDER BY created_at DESC""",
        f"{_MIGRATION_SEG_ID}:%",
    )
    seen: dict = {}
    for r in rows:
        if r["batch_key"] not in seen:
            d = dict(r)
            for k in ("started_at", "completed_at"):
                if d.get(k):
                    d[k] = d[k].isoformat()
            seen[r["batch_key"]] = d

    batches: list = []
    for i in range(n_batches):
        start = i * _MIGRATION_BATCH_SIZE
        end = min(start + _MIGRATION_BATCH_SIZE, total)
        size = end - start
        key = f"{_MIGRATION_SEG_ID}:batch_{i + 1:03d}"
        state = seen.get(key) or {"status": "not_sent"}
        # Label: "Migration JAPAP 1.0 → 4.0 — Batch 1 / 5000"
        if i == n_batches - 1 and size < _MIGRATION_BATCH_SIZE:
            label_size = f"restant ({size})"
        else:
            label_size = f"{size}"
        batches.append({
            "batch_key": key,
            "batch_index": i + 1,
            "batch_total": n_batches,
            "size": size,
            "label": f"Migration JAPAP 1.0 → 4.0 — Batch {i + 1} / {label_size}",
            "status": state.get("status", "not_sent"),
            "campaign_id": state.get("campaign_id"),
            "sent_count": int(state.get("sent_count") or 0),
            "bounced_count": int(state.get("bounced_count") or 0),
            "started_at": state.get("started_at"),
            "completed_at": state.get("completed_at"),
        })
    return batches


@router.get("/audience-options")
async def audience_options(request: Request):
    """Returns all the audiences an admin can pick when sending a template.

    Shape:
        {
          "segments": [ {segment_id, name, description, estimated_count, is_system}, ... ],
          "migration_batches": [ {batch_key, batch_index, label, size, status, ...}, ... ]
        }
    """
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_campaign_batch_columns(conn)
        await _seed_system_segments(conn)
        rows = await conn.fetch(
            "SELECT segment_id, name, description, is_system, estimated_count "
            "FROM email_segments ORDER BY is_system DESC, name ASC"
        )
        segments = []
        for r in rows:
            # Skip the special migration segment from the flat list — it is
            # exposed separately as batches to avoid "send to 28k in 1 click".
            if r["segment_id"] == _MIGRATION_SEG_ID:
                continue
            segments.append({
                "segment_id": r["segment_id"],
                "name": r["name"],
                "description": r["description"],
                "is_system": bool(r["is_system"]),
                "estimated_count": int(r["estimated_count"] or 0),
            })
        batches = await _migration_batch_status(conn)
    return {"segments": segments, "migration_batches": batches}


class SendToAudienceRequest(BaseModel):
    segment_id: Optional[str] = None
    batch_key: Optional[str] = None           # required for migration batches
    confirm: bool = False
    force: bool = False                        # bypass audience-cap on non-batch sends


@router.post("/templates/{template_id}/send-to-audience")
async def template_send_to_audience(
    template_id: str,
    req: SendToAudienceRequest,
    request: Request,
):
    """Create an ephemeral campaign from a template and enqueue it.

    Guard-rails:
      • `confirm=true` mandatory.
      • `batch_key` mandatory whenever segment_id == seg_migration_1to4.
      • Double-send protection: a batch_key can only be consumed once while
        its latest campaign is in {pending, sending, sent}.
      • Rate-limit inherited from /campaigns/{id}/send (5/min).
    """
    admin = await _require_admin(request)
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Confirmation explicite requise (confirm=true)")
    if not req.segment_id:
        raise HTTPException(status_code=400, detail="segment_id est requis")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_campaign_batch_columns(conn)

        tpl = await conn.fetchrow(
            "SELECT template_id, name, subject, preview_text, body_html, body_text, "
            "cta_label, cta_url, language FROM email_templates WHERE template_id = $1",
            template_id,
        )
        if not tpl:
            raise HTTPException(status_code=404, detail="Template introuvable")

        # Resolve & slice the audience.
        all_recipients, rules_snapshot = await _resolve_segment_recipients(conn, req.segment_id)

        is_migration = req.segment_id == _MIGRATION_SEG_ID
        batch_key: Optional[str] = None
        batch_index: Optional[int] = None
        batch_total: Optional[int] = None
        campaign_display_name = tpl["name"]

        if is_migration:
            if not req.batch_key:
                raise HTTPException(
                    status_code=400,
                    detail="batch_key requis pour l'audience Migration 1.0 → 4.0",
                )
            if not req.batch_key.startswith(f"{_MIGRATION_SEG_ID}:batch_"):
                raise HTTPException(status_code=400, detail="batch_key invalide")
            try:
                batch_index = int(req.batch_key.split("_")[-1])
            except Exception:
                raise HTTPException(status_code=400, detail="batch_key invalide")

            total = len(all_recipients)
            batch_total = (total + _MIGRATION_BATCH_SIZE - 1) // _MIGRATION_BATCH_SIZE
            if batch_index < 1 or batch_index > batch_total:
                raise HTTPException(
                    status_code=400,
                    detail=f"batch_index hors borne (1..{batch_total})",
                )

            start = (batch_index - 1) * _MIGRATION_BATCH_SIZE
            end = min(start + _MIGRATION_BATCH_SIZE, total)
            recipients = all_recipients[start:end]
            batch_key = req.batch_key
            campaign_display_name = (
                f"Migration JAPAP 1.0 → 4.0 — Batch {batch_index}/{batch_total} — {tpl['name']}"
            )

            # Double-send protection
            existing = await conn.fetchrow(
                "SELECT campaign_id, status FROM email_campaigns "
                "WHERE batch_key = $1 AND status IN ('pending','sending','sent') "
                "ORDER BY created_at DESC LIMIT 1",
                batch_key,
            )
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Ce batch a déjà été envoyé (campaign_id={existing['campaign_id']}, "
                        f"status={existing['status']}). Double-envoi bloqué."
                    ),
                )
        else:
            recipients = all_recipients

        # Cleanliness filter
        for u in recipients:
            u.setdefault("is_banned", False)
            u.setdefault("status", None)
        kept, filter_stats = await _apply_cleanliness_filter(recipients)
        dropped = len(recipients) - len(kept)
        recipients = kept
        if not recipients:
            raise HTTPException(
                status_code=400,
                detail=f"Audience vide après filtrage: {filter_stats}",
            )

        # Non-batch audience cap (migration batches bypass — they're already
        # explicitly 5k chunks chosen by the admin).
        if not is_migration:
            from services.settings_service import get_int as _gi
            cap = max(1, await _gi("messaging_max_audience_per_campaign", 200))
            if len(recipients) > cap and not req.force:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Audience ({len(recipients)}) dépasse le plafond de {cap}. "
                        f"Ajoutez force=true pour forcer."
                    ),
                )

        # Create campaign row in status='sending' directly.
        cid = f"cmp_{uuid.uuid4().hex[:12]}"
        try:
            await conn.execute(
                """INSERT INTO email_campaigns
                     (campaign_id, name, status, template_id, subject, preview_text,
                      body_html, body_text, cta_label, cta_url, language,
                      segment_id, segment_rules_snapshot, created_by, confirmed_by,
                      started_at, batch_key, batch_index, batch_total)
                   VALUES ($1,$2,'sending',$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                           NOW(),$15,$16,$17)""",
                cid, campaign_display_name, tpl["template_id"], tpl["subject"],
                tpl["preview_text"] or "", tpl["body_html"], tpl["body_text"] or "",
                tpl["cta_label"] or "", tpl["cta_url"] or "", tpl["language"] or "fr",
                req.segment_id, json.dumps(rules_snapshot),
                admin["user_id"], admin["user_id"],
                batch_key, batch_index, batch_total,
            )
        except Exception as e:
            # Unique constraint violated → another admin clicked simultaneously
            if "email_campaigns_batch_key_uniq" in str(e):
                raise HTTPException(
                    status_code=409,
                    detail="Ce batch vient d'être envoyé par un autre administrateur.",
                )
            raise

        # Enqueue recipients asynchronously — the admin gets an immediate
        # response while the worker fan-out fills the queue in the background.
        # Large migration batches (5000 users) can take 30-90s to render,
        # which exceeds the 60s ingress timeout.
        import asyncio as _asyncio
        _asyncio.create_task(
            _background_enqueue_recipients(cid, dict(tpl), recipients)
        )
        enqueued = len(recipients)  # optimistic — actual count in dashboard

        await _audit(
            conn, admin["user_id"], "messaging.template.send_to_audience", cid,
            {
                "template_id": template_id,
                "segment_id": req.segment_id,
                "batch_key": batch_key,
                "batch_index": batch_index,
                "batch_total": batch_total,
                "audience_size": len(recipients),
                "dropped_filter": dropped,
                "enqueued": enqueued,
            },
        )

    return {
        "campaign_id": cid,
        "status": "sending",
        "audience_size": len(recipients),
        "dropped_by_filter": dropped,
        "enqueued": enqueued,
        "batch_key": batch_key,
        "batch_index": batch_index,
        "batch_total": batch_total,
    }


# ══════════════════════════════════════════════════════════════════════════
#  ANALYTICS (basic cards for Phase 1)
# ══════════════════════════════════════════════════════════════════════════

@router.get("/analytics")
async def analytics_dashboard(request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        cards = await conn.fetchrow("""
            SELECT
              COALESCE((SELECT COUNT(*) FROM email_campaigns), 0) AS campaigns_total,
              COALESCE((SELECT COUNT(*) FROM email_campaigns WHERE status='sent'), 0) AS campaigns_sent,
              COALESCE((SELECT COUNT(*) FROM email_campaigns WHERE status='sending'), 0) AS campaigns_sending,
              COALESCE((SELECT COUNT(*) FROM email_logs WHERE event='sent'), 0) AS total_sent,
              COALESCE((SELECT COUNT(*) FROM email_logs WHERE event='delivered'), 0) AS total_delivered,
              COALESCE((SELECT COUNT(*) FROM email_logs WHERE event='opened'), 0) AS total_opened,
              COALESCE((SELECT COUNT(*) FROM email_logs WHERE event='clicked'), 0) AS total_clicked,
              COALESCE((SELECT COUNT(*) FROM email_logs WHERE event='bounced'), 0) AS total_bounced,
              COALESCE((SELECT COUNT(*) FROM email_logs WHERE event='unsubscribed'), 0) AS total_unsub,
              COALESCE((SELECT COUNT(*) FROM email_send_queue WHERE status='pending'), 0) AS queue_pending,
              COALESCE((SELECT COUNT(*) FROM email_send_queue WHERE status='failed'), 0) AS queue_failed,
              COALESCE((SELECT COUNT(*) FROM users WHERE email_subscribed = FALSE), 0) AS unsubscribed_users
        """)
    return dict(cards)


@router.get("/analytics/campaigns/{campaign_id}")
async def analytics_campaign(campaign_id: str, request: Request):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        c = await conn.fetchrow(
            """SELECT campaign_id, name, status, started_at, completed_at,
                      sent_count, delivered_count, opened_count, clicked_count,
                      bounced_count, unsub_count
               FROM email_campaigns WHERE campaign_id = $1""",
            campaign_id,
        )
        if not c:
            raise HTTPException(status_code=404, detail="Campagne introuvable")
        events = await conn.fetch(
            """SELECT event, COUNT(*) AS n FROM email_logs
               WHERE campaign_id = $1 GROUP BY event""",
            campaign_id,
        )
    d = dict(c)
    for k in ("started_at", "completed_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    d["events"] = {r["event"]: int(r["n"]) for r in events}
    return d



# ══════════════════════════════════════════════════════════════════════════
#  BATCH SCALE & SAFETY (iter82) — runtime controls + queue observability
# ══════════════════════════════════════════════════════════════════════════
class BatchSettingsUpdate(BaseModel):
    real_send_enabled: Optional[bool] = None
    max_audience_per_campaign: Optional[int] = Field(None, ge=1, le=100000)
    worker_rate_per_minute: Optional[int] = Field(None, ge=1, le=10000)
    batch_size: Optional[int] = Field(None, ge=1, le=500)


@router.get("/batch/status")
async def batch_status(request: Request):
    """Live queue stats + current batch-scale settings (admin-only)."""
    await _require_admin(request)
    from services.settings_service import get_bool, get_int
    pool = await get_pool()
    async with pool.acquire() as conn:
        q = await conn.fetchrow(
            """
            SELECT
              COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END), 0) AS pending,
              COALESCE(SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END), 0) AS sent,
              COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0) AS failed,
              COALESCE(SUM(CASE WHEN status='pending' AND locked_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS locked,
              COUNT(*) AS total
            FROM email_send_queue
            """,
        )
        recent_sent_1h = await conn.fetchval(
            "SELECT COUNT(*) FROM email_send_queue "
            "WHERE status='sent' AND sent_at > NOW() - INTERVAL '1 hour'",
        )
        active_sending = await conn.fetchval(
            "SELECT COUNT(*) FROM email_campaigns WHERE status='sending'",
        )
        oldest_pending = await conn.fetchval(
            "SELECT MIN(created_at) FROM email_send_queue WHERE status='pending'",
        )
    return {
        "queue": {
            "pending": int(q["pending"] or 0),
            "locked": int(q["locked"] or 0),
            "sent": int(q["sent"] or 0),
            "failed": int(q["failed"] or 0),
            "total": int(q["total"] or 0),
            "oldest_pending_at": oldest_pending.isoformat() if oldest_pending else None,
        },
        "throughput": {
            "sent_last_hour": int(recent_sent_1h or 0),
            "active_campaigns_sending": int(active_sending or 0),
        },
        "settings": {
            "real_send_enabled": await get_bool("messaging_real_send_enabled", False),
            "max_audience_per_campaign": await get_int("messaging_max_audience_per_campaign", 1000),
            "worker_rate_per_minute": await get_int("messaging_worker_rate_per_minute", 60),
            "batch_size": await get_int("messaging_batch_size", 25),
        },
    }


@router.put("/batch/settings")
async def batch_update_settings(req: BatchSettingsUpdate, request: Request):
    """Partial update of batch-scale + safety settings. Audit-logged."""
    admin = await _require_admin(request)
    from services.settings_service import set_setting
    payload = req.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=400, detail="Aucune modification fournie")

    mapping = {
        "real_send_enabled": "messaging_real_send_enabled",
        "max_audience_per_campaign": "messaging_max_audience_per_campaign",
        "worker_rate_per_minute": "messaging_worker_rate_per_minute",
        "batch_size": "messaging_batch_size",
    }
    updated: dict = {}
    for k, v in payload.items():
        db_key = mapping[k]
        if isinstance(v, bool):
            await set_setting(db_key, "true" if v else "false")
        else:
            await set_setting(db_key, str(v))
        updated[db_key] = v

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _audit(conn, admin["user_id"], "messaging.batch.settings_update",
                     "admin_settings", {"updated": updated})
    return {"status": "ok", "updated": updated}


@router.post("/batch/requeue-failed")
async def batch_requeue_failed(request: Request):
    """Move failed rows back to 'pending' so the worker can retry them.
    Useful after fixing a transient provider issue. Capped at 500/call."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            """UPDATE email_send_queue
               SET status='pending', attempt_count=0, error_msg=NULL,
                   locked_by=NULL, locked_at=NULL
               WHERE id IN (
                 SELECT id FROM email_send_queue WHERE status='failed'
                 ORDER BY id DESC LIMIT 500
               )""",
        )
        # res is like "UPDATE N"
        try:
            n = int(res.split()[-1])
        except Exception:
            n = 0
        await _audit(conn, admin["user_id"], "messaging.batch.requeue_failed",
                     "email_send_queue", {"requeued": n})
    return {"status": "ok", "requeued": n}
