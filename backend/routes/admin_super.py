"""Iter83 — Superadmin-only endpoints.

Only users with role='superadmin' may hit these routes. Every mutating
action is written to `admin_audit_log` with the actor + target + IP/UA
so the platform owner keeps a full audit trail independent of the normal
`audit_logs` table.

Also exposes the "dynamic admin URL" helper (GET /url-token) that returns
today's DDMMYY token the frontend uses as a gate for the /admin{DDMMYY}
path. This is a soft-obfuscation layer — the actual security comes from
the superadmin role + email 2FA + brute-force protection in /login.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from database import get_pool
from routes.auth import (
    get_current_user, hash_password, log_admin_action,
    require_superadmin, user_to_response,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/super", tags=["admin-super"])


ALLOWED_SUB_ROLES = {
    "content_moderator",
    "wallet_manager",
    "campaign_manager",
    "support_agent",
    "wheel_admin",
}


def _normalize_sub_roles(values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    out: List[str] = []
    seen = set()
    for v in values:
        s = (v or "").strip().lower()
        if s and s in ALLOWED_SUB_ROLES and s not in seen:
            out.append(s)
            seen.add(s)
    return out


# ── Dynamic URL token (today's DDMMYY) ──────────────────────────────────────
def _today_token() -> str:
    return datetime.now(timezone.utc).strftime("%d%m%y")


@router.get("/url-token")
async def url_token(request: Request):
    """Returns today's 6-digit DDMMYY token. Superadmin-only — the frontend
    fetches this right after authentication to resolve a safe redirect
    target, so the token never needs to live in a public config file."""
    await require_superadmin(request)
    return {"token": _today_token()}


# ── Admin CRUD ──────────────────────────────────────────────────────────────
class CreateAdminRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    sub_roles: List[str] = Field(default_factory=list)


class UpdateRolesRequest(BaseModel):
    sub_roles: List[str]


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8)


def _client_ctx(request: Request) -> tuple[str, str]:
    ip = request.client.host if request and request.client else ""
    ua = request.headers.get("user-agent", "") if request else ""
    return ip, ua


@router.get("/admins")
async def list_admins(request: Request):
    await require_superadmin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, email, first_name, last_name, role,
                   admin_sub_roles, is_active, created_at, last_seen,
                   force_password_change
            FROM users
            WHERE role IN ('admin', 'superadmin')
            ORDER BY role DESC, created_at ASC
        """)
    out = []
    for r in rows:
        d = dict(r)
        # asyncpg JSONB → may come back as str; make it a list either way.
        sub = d.get("admin_sub_roles")
        if isinstance(sub, str):
            try:
                sub = json.loads(sub)
            except Exception:
                sub = []
        d["admin_sub_roles"] = sub or []
        for dt_key in ("created_at", "last_seen"):
            if isinstance(d.get(dt_key), datetime):
                d[dt_key] = d[dt_key].isoformat()
        out.append(d)
    return {"admins": out}


@router.post("/admins", status_code=201)
async def create_admin(req: CreateAdminRequest, request: Request):
    actor = await require_superadmin(request)
    ip, ua = _client_ctx(request)
    email = req.email.lower().strip()
    sub_roles = _normalize_sub_roles(req.sub_roles)

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT user_id, role FROM users WHERE email = $1", email)
        if existing:
            # Upgrade path: promote an existing user account to admin instead
            # of refusing. Keeps the audit clean.
            if existing["role"] == "superadmin":
                raise HTTPException(status_code=400, detail="Impossible de modifier un superadmin")
            await conn.execute("""
                UPDATE users SET role = 'admin', admin_sub_roles = $1::jsonb,
                                 is_active = TRUE, is_verified = TRUE, email_verified = TRUE,
                                 force_password_change = TRUE
                WHERE email = $2
            """, json.dumps(sub_roles), email)
            if req.password:
                await conn.execute(
                    "UPDATE users SET password_hash = $1, password_changed_at = NOW() WHERE email = $2",
                    hash_password(req.password), email,
                )
            target_id = existing["user_id"]
            promoted = True
        else:
            target_id = f"admin_{uuid.uuid4().hex[:12]}"
            hashed = hash_password(req.password)
            await conn.execute("""
                INSERT INTO users (user_id, username, email, password_hash, first_name, last_name,
                                   role, admin_sub_roles, is_active, is_verified, email_verified,
                                   terms_accepted, terms_accepted_at, force_password_change,
                                   password_changed_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'admin', $7::jsonb, TRUE, TRUE, TRUE,
                        TRUE, NOW(), TRUE, NOW())
            """, target_id, f"admin_{uuid.uuid4().hex[:6]}", email, hashed,
               req.first_name.strip(), (req.last_name or "").strip(), json.dumps(sub_roles))
            await conn.execute(
                "INSERT INTO wallets (user_id, balance) VALUES ($1, 0.00) ON CONFLICT DO NOTHING",
                target_id,
            )
            promoted = False

        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", target_id)

    await log_admin_action(
        actor_id=actor["user_id"], actor_email=actor["email"],
        action="admin.create" if not promoted else "admin.promote",
        target_id=target_id, target_email=email,
        metadata={"sub_roles": sub_roles, "promoted_from_user": promoted},
        ip=ip, ua=ua,
    )
    return {"admin": user_to_response(dict(row))}


@router.patch("/admins/{user_id}/roles")
async def update_admin_roles(user_id: str, req: UpdateRolesRequest, request: Request):
    actor = await require_superadmin(request)
    ip, ua = _client_ctx(request)
    sub_roles = _normalize_sub_roles(req.sub_roles)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id, email, role FROM users WHERE user_id = $1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Admin introuvable")
        if target["role"] == "superadmin":
            raise HTTPException(status_code=400, detail="Impossible de modifier un superadmin")
        if target["role"] != "admin":
            raise HTTPException(status_code=400, detail="Cet utilisateur n'est pas admin")
        await conn.execute(
            "UPDATE users SET admin_sub_roles = $1::jsonb WHERE user_id = $2",
            json.dumps(sub_roles), user_id,
        )

    await log_admin_action(
        actor_id=actor["user_id"], actor_email=actor["email"],
        action="admin.update_roles",
        target_id=user_id, target_email=target["email"],
        metadata={"sub_roles": sub_roles}, ip=ip, ua=ua,
    )
    return {"user_id": user_id, "sub_roles": sub_roles}


@router.post("/admins/{user_id}/reset-password")
async def reset_admin_password(user_id: str, req: ResetPasswordRequest, request: Request):
    actor = await require_superadmin(request)
    ip, ua = _client_ctx(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id, email, role FROM users WHERE user_id = $1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Admin introuvable")
        if target["role"] == "superadmin" and target["user_id"] != actor["user_id"]:
            raise HTTPException(status_code=400, detail="Impossible de modifier un autre superadmin")
        await conn.execute("""
            UPDATE users SET password_hash = $1, force_password_change = TRUE,
                             password_changed_at = NOW()
            WHERE user_id = $2
        """, hash_password(req.new_password), user_id)

    await log_admin_action(
        actor_id=actor["user_id"], actor_email=actor["email"],
        action="admin.reset_password",
        target_id=user_id, target_email=target["email"],
        metadata={"forced_change_on_next_login": True}, ip=ip, ua=ua,
    )
    return {"status": "ok", "user_id": user_id}


@router.delete("/admins/{user_id}")
async def demote_admin(user_id: str, request: Request):
    """Demotes an admin back to role='user'. We never hard-delete the row
    so historical content / audits remain referentially sound."""
    actor = await require_superadmin(request)
    ip, ua = _client_ctx(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id, email, role FROM users WHERE user_id = $1", user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Admin introuvable")
        if target["role"] == "superadmin":
            raise HTTPException(status_code=400, detail="Impossible de rétrograder un superadmin")
        await conn.execute("""
            UPDATE users SET role = 'user', admin_sub_roles = '[]'::jsonb
            WHERE user_id = $1
        """, user_id)

    await log_admin_action(
        actor_id=actor["user_id"], actor_email=actor["email"],
        action="admin.demote",
        target_id=user_id, target_email=target["email"],
        ip=ip, ua=ua,
    )
    return {"status": "demoted", "user_id": user_id}


# ── Audit log ───────────────────────────────────────────────────────────────
@router.get("/audit-log")
async def get_audit_log(request: Request, limit: int = 100, offset: int = 0):
    await require_superadmin(request)
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, actor_id, actor_email, action, target_id, target_email,
                   metadata, ip_address, user_agent, created_at
            FROM admin_audit_log
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
        """, limit, offset)
        total = await conn.fetchval("SELECT COUNT(*) FROM admin_audit_log")
    out = []
    for r in rows:
        d = dict(r)
        meta = d.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        d["metadata"] = meta or {}
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return {"logs": out, "total": int(total or 0), "limit": limit, "offset": offset}


# ── Public helper: validate today's token (used by the login page guard) ────
@router.get("/url-check")
async def url_check(token: str):
    """Unauthenticated, rate-limit friendly endpoint that returns whether
    the provided `token` matches today's DDMMYY. Intended for the frontend
    guard before the superadmin login form renders. Never leaks whether
    the caller is a valid superadmin or not — only time-based truth."""
    ok = (token or "").strip() == _today_token()
    return {"valid": bool(ok)}


# ── Iter83 — Signup analytics (day / month / year) ──────────────────────────
@router.get("/signup-stats")
async def signup_stats(request: Request, granularity: str = "day", limit: int = 30):
    """Time-series of signups + activation rate, for the superadmin
    Analytics tab. Buckets by day (last `limit` days, default 30), month
    (last `limit` months, default 12), or year (last `limit` years,
    default 5). Also returns at-a-glance KPIs for 24h / 7d / 30d."""
    await require_superadmin(request)
    g = (granularity or "day").lower()
    if g not in ("day", "month", "year"):
        raise HTTPException(status_code=400, detail="granularity must be day|month|year")
    # Sensible defaults per granularity, clamped.
    defaults = {"day": 30, "month": 12, "year": 5}
    caps = {"day": 180, "month": 60, "year": 15}
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = defaults[g]
    if limit <= 0:
        limit = defaults[g]
    limit = min(limit, caps[g])

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Single rolled-up query per granularity. `generate_series` guarantees
        # zero-filled buckets so the chart never has gaps.
        if g == "day":
            rows = await conn.fetch(f"""
                WITH buckets AS (
                  SELECT date_trunc('day', NOW() - (i || ' day')::interval) AS bucket
                  FROM generate_series(0, {limit - 1}) AS i
                )
                SELECT b.bucket::date AS bucket,
                       COALESCE(COUNT(u.user_id), 0) AS signups,
                       COALESCE(SUM(CASE WHEN u.email_verified THEN 1 ELSE 0 END), 0) AS activated
                FROM buckets b
                LEFT JOIN users u
                  ON date_trunc('day', u.created_at) = b.bucket
                  AND u.role IN ('user', 'admin', 'superadmin')
                GROUP BY b.bucket
                ORDER BY b.bucket ASC
            """)
        elif g == "month":
            rows = await conn.fetch(f"""
                WITH buckets AS (
                  SELECT date_trunc('month', NOW() - (i || ' month')::interval) AS bucket
                  FROM generate_series(0, {limit - 1}) AS i
                )
                SELECT b.bucket::date AS bucket,
                       COALESCE(COUNT(u.user_id), 0) AS signups,
                       COALESCE(SUM(CASE WHEN u.email_verified THEN 1 ELSE 0 END), 0) AS activated
                FROM buckets b
                LEFT JOIN users u
                  ON date_trunc('month', u.created_at) = b.bucket
                GROUP BY b.bucket
                ORDER BY b.bucket ASC
            """)
        else:  # year
            rows = await conn.fetch(f"""
                WITH buckets AS (
                  SELECT date_trunc('year', NOW() - (i || ' year')::interval) AS bucket
                  FROM generate_series(0, {limit - 1}) AS i
                )
                SELECT b.bucket::date AS bucket,
                       COALESCE(COUNT(u.user_id), 0) AS signups,
                       COALESCE(SUM(CASE WHEN u.email_verified THEN 1 ELSE 0 END), 0) AS activated
                FROM buckets b
                LEFT JOIN users u
                  ON date_trunc('year', u.created_at) = b.bucket
                GROUP BY b.bucket
                ORDER BY b.bucket ASC
            """)

        # KPIs: 24h / 7d / 30d + total
        kpi = await conn.fetchrow("""
            SELECT
              SUM(CASE WHEN created_at >= NOW() - INTERVAL '1 day' THEN 1 ELSE 0 END)  AS s_24h,
              SUM(CASE WHEN created_at >= NOW() - INTERVAL '7 day' THEN 1 ELSE 0 END)  AS s_7d,
              SUM(CASE WHEN created_at >= NOW() - INTERVAL '30 day' THEN 1 ELSE 0 END) AS s_30d,
              SUM(CASE WHEN email_verified AND created_at >= NOW() - INTERVAL '1 day'  THEN 1 ELSE 0 END) AS a_24h,
              SUM(CASE WHEN email_verified AND created_at >= NOW() - INTERVAL '7 day'  THEN 1 ELSE 0 END) AS a_7d,
              SUM(CASE WHEN email_verified AND created_at >= NOW() - INTERVAL '30 day' THEN 1 ELSE 0 END) AS a_30d,
              COUNT(*) AS total_users,
              SUM(CASE WHEN email_verified THEN 1 ELSE 0 END) AS total_activated
            FROM users
        """)

    def _rate(num, den):
        den = int(den or 0)
        return round((int(num or 0) * 100.0) / den, 1) if den else 0.0

    series = []
    for r in rows:
        b = r["bucket"]
        # Frontend-friendly label per granularity.
        if g == "day":
            label = b.strftime("%d %b")
        elif g == "month":
            label = b.strftime("%b %Y")
        else:
            label = b.strftime("%Y")
        series.append({
            "bucket": b.isoformat(),
            "label": label,
            "signups": int(r["signups"] or 0),
            "activated": int(r["activated"] or 0),
        })

    return {
        "granularity": g,
        "limit": limit,
        "series": series,
        "kpis": {
            "last_24h":  {"signups": int(kpi["s_24h"] or 0),
                          "activated": int(kpi["a_24h"] or 0),
                          "activation_rate": _rate(kpi["a_24h"], kpi["s_24h"])},
            "last_7d":   {"signups": int(kpi["s_7d"] or 0),
                          "activated": int(kpi["a_7d"] or 0),
                          "activation_rate": _rate(kpi["a_7d"], kpi["s_7d"])},
            "last_30d":  {"signups": int(kpi["s_30d"] or 0),
                          "activated": int(kpi["a_30d"] or 0),
                          "activation_rate": _rate(kpi["a_30d"], kpi["s_30d"])},
            "all_time":  {"signups": int(kpi["total_users"] or 0),
                          "activated": int(kpi["total_activated"] or 0),
                          "activation_rate": _rate(kpi["total_activated"], kpi["total_users"])},
        },
    }
