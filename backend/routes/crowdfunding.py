"""
JAPAP — Crowdfunding viral module (iter142, P0 reset)
=====================================================

Vote-based competition where one user creates one project and the
community votes. Winner = first project to reach `votes_to_win` once
votes are open. Cycle is admin-controlled (no auto-restart).

Hard rules (CEO P0):
  - 1 user → 1 active project per cycle (UNIQUE partial index in DB)
  - 1 user → 1 vote per project (UNIQUE in DB)
  - Account must be ≥ N days old AND meet minimum activity score
  - Votes BLOCKED until threshold_projects is reached (auto-opens)
  - Winner detected and rewarded ATOMICALLY in `cast_vote` — zero delay
  - New cycle is admin-only (no auto-restart)
"""
from __future__ import annotations

import os
import re
import uuid
import hashlib
import logging
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Literal, List

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from database import get_pool
from routes.auth import get_current_user
from services.settings_service import get_setting, get_json, set_setting, get_bool
from services import crowdfunding_share_card
from services import engagement_ai_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/crowdfunding", tags=["crowdfunding"])
_limiter = Limiter(key_func=get_remote_address)


# ─── Constants ────────────────────────────────────────────────────────────
VALID_CATEGORIES = {"business", "health", "community", "education",
                    "emergency", "art", "tech", "sport"}

CATEGORIES_META = [
    {"id": "business",  "name": "Business / Startup", "icon": "briefcase",       "color": "#4A90E2"},
    {"id": "tech",      "name": "Tech",               "icon": "cpu",             "color": "#7C3AED"},
    {"id": "art",       "name": "Art & Culture",      "icon": "palette",         "color": "#EC4899"},
    {"id": "health",    "name": "Santé",              "icon": "heart",           "color": "#E01C2E"},
    {"id": "education", "name": "Éducation",          "icon": "graduation-cap",  "color": "#F59E0B"},
    {"id": "sport",     "name": "Sport",              "icon": "trophy",          "color": "#10B981"},
    {"id": "community", "name": "Communautaire",      "icon": "users",           "color": "#22C55E"},
    {"id": "emergency", "name": "Urgence",            "icon": "siren",           "color": "#DC2626"},
]

DEFAULT_THRESHOLD_PROJECTS = 50
DEFAULT_VOTES_TO_WIN = 100
DEFAULT_REWARD_AMOUNT = Decimal("50000")
DEFAULT_REWARD_CURRENCY = "XAF"
DEFAULT_MIN_ACCOUNT_AGE_DAYS = 7
DEFAULT_MIN_ACTIVITY_SCORE = 50
DEFAULT_REQUIRED_ACTIONS = {"posts": 1, "likes": 5, "transactions": 1}
# iter239w — Cycle duration & terms version
DEFAULT_CYCLE_DURATION_DAYS = 30
TERMS_CURRENT_VERSION = "v1"
# iter239w — Auto-approve toggle (admin-controlled, NO hardcode)
DEFAULT_AUTO_APPROVE_PROJECTS = False


# ─── Helpers ──────────────────────────────────────────────────────────────
def _slugify(title: str) -> str:
    s = (title or "").lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    s = s[:80] or "projet"
    suffix = uuid.uuid4().hex[:6]
    return f"{s}-{suffix}"


def _hash_ip(request: Request) -> str:
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or request.headers.get("x-real-ip", "")
          or (request.client.host if request.client else ""))
    return hashlib.sha256(ip.encode()).hexdigest()[:32] if ip else ""


def _hash_ua(request: Request) -> str:
    ua = request.headers.get("user-agent", "")
    return hashlib.sha256(ua.encode()).hexdigest()[:32] if ua else ""


async def _is_admin(request: Request) -> bool:
    try:
        u = await get_current_user(request)
        return bool(u and u.get("role") in ("admin", "superadmin"))
    except Exception:
        return False


async def _require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin requis.")
    return user


# ─── Cycle helpers ────────────────────────────────────────────────────────
async def _get_active_cycle(conn) -> Optional[dict]:
    row = await conn.fetchrow(
        "SELECT * FROM crowdfunding_cycles WHERE status = 'active' "
        "ORDER BY started_at DESC LIMIT 1"
    )
    return dict(row) if row else None


async def _ensure_active_cycle(conn) -> Optional[dict]:
    """Bootstrap the very first cycle if NO cycle has EVER existed. Once a
    cycle has been completed/archived, the system stays in 'no active'
    state until the admin manually starts a new one (CEO P0: no auto
    restart)."""
    cycle = await _get_active_cycle(conn)
    if cycle:
        return cycle
    # Only bootstrap if the table is completely empty.
    any_cycle = await conn.fetchval("SELECT 1 FROM crowdfunding_cycles LIMIT 1")
    if any_cycle:
        return None
    cycle_id = f"cycle_{uuid.uuid4().hex[:12]}"
    threshold = int(await get_setting("crowdfunding_threshold_projects",
                                      DEFAULT_THRESHOLD_PROJECTS) or DEFAULT_THRESHOLD_PROJECTS)
    votes_to_win = int(await get_setting("crowdfunding_votes_to_win",
                                         DEFAULT_VOTES_TO_WIN) or DEFAULT_VOTES_TO_WIN)
    reward_amount = Decimal(str(await get_setting("crowdfunding_reward_amount",
                                                  DEFAULT_REWARD_AMOUNT) or DEFAULT_REWARD_AMOUNT))
    reward_currency = str(await get_setting("crowdfunding_reward_currency",
                                            DEFAULT_REWARD_CURRENCY) or DEFAULT_REWARD_CURRENCY)
    # iter239w — Durée par défaut + ended_at calculé.
    duration_days = int(await get_setting("crowdfunding_default_cycle_duration_days",
                                          DEFAULT_CYCLE_DURATION_DAYS) or DEFAULT_CYCLE_DURATION_DAYS)
    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(days=duration_days)
    await conn.execute(
        """INSERT INTO crowdfunding_cycles
           (cycle_id, cycle_number, status, threshold_projects, votes_to_win,
            reward_amount, reward_currency, votes_open, started_at, ended_at,
            duration_days)
           VALUES ($1, 1, 'active', $2, $3, $4, $5, FALSE, $6, $7, $8)""",
        cycle_id, threshold, votes_to_win, reward_amount, reward_currency,
        now, ends_at, duration_days,
    )
    return await _get_active_cycle(conn)


async def _maybe_open_votes(conn, cycle: dict) -> dict:
    """Atomic-ish: if active cycle has reached threshold_projects → open votes
    and persist `votes_opened_at`. Returns the (possibly updated) cycle."""
    if cycle.get("votes_open"):
        return cycle
    cnt = await conn.fetchval(
        "SELECT COUNT(*) FROM crowdfunding_projects "
        "WHERE cycle_id = $1 AND status IN ('active','winner')",
        cycle["cycle_id"],
    )
    if cnt >= int(cycle["threshold_projects"]):
        await conn.execute(
            "UPDATE crowdfunding_cycles SET votes_open = TRUE, "
            "votes_opened_at = NOW() WHERE cycle_id = $1 AND votes_open = FALSE",
            cycle["cycle_id"],
        )
        cycle["votes_open"] = True
        cycle["votes_opened_at"] = datetime.now(timezone.utc)
        logger.info(f"[crowdfunding] Votes OPENED for cycle {cycle['cycle_id']} (projects={cnt})")
    return cycle


# ─── Eligibility ──────────────────────────────────────────────────────────
async def _eligibility_check(conn, user_id: str) -> dict:
    """Returns {eligible: bool, reasons: [str], stats: {...}, score: int}.
    Configurable thresholds live in admin_settings."""
    min_age_days = int(await get_setting("crowdfunding_min_account_age_days",
                                         DEFAULT_MIN_ACCOUNT_AGE_DAYS) or DEFAULT_MIN_ACCOUNT_AGE_DAYS)
    min_score = int(await get_setting("crowdfunding_min_activity_score",
                                      DEFAULT_MIN_ACTIVITY_SCORE) or DEFAULT_MIN_ACTIVITY_SCORE)
    req_actions = await get_json("crowdfunding_required_actions",
                                 DEFAULT_REQUIRED_ACTIONS) or DEFAULT_REQUIRED_ACTIONS

    user = await conn.fetchrow(
        "SELECT created_at FROM users WHERE user_id = $1", user_id,
    )
    if not user:
        return {"eligible": False, "reasons": ["Utilisateur introuvable."],
                "stats": {}, "score": 0,
                "thresholds": {"min_age_days": min_age_days,
                               "min_score": min_score,
                               "required_actions": req_actions}}

    age_days = (datetime.now(timezone.utc) - user["created_at"]).days

    # Activity counters — best-effort, missing tables return 0.
    async def _count(sql: str, *args) -> int:
        try:
            return int(await conn.fetchval(sql, *args) or 0)
        except Exception:
            return 0

    posts = await _count("SELECT COUNT(*) FROM posts WHERE user_id = $1", user_id)
    likes_given = await _count(
        "SELECT COUNT(*) FROM post_likes WHERE user_id = $1", user_id)
    comments = await _count(
        "SELECT COUNT(*) FROM post_comments WHERE user_id = $1", user_id)
    transactions = await _count(
        "SELECT COUNT(*) FROM transactions WHERE from_user_id = $1 OR to_user_id = $1",
        user_id,
    )

    stats = {
        "account_age_days": age_days,
        "posts": posts, "likes": likes_given,
        "comments": comments, "transactions": transactions,
    }
    # Simple weighted score
    score = posts * 10 + likes_given * 2 + comments * 3 + transactions * 5

    reasons = []
    if age_days < min_age_days:
        reasons.append(f"Ton compte doit avoir au moins {min_age_days} jours "
                       f"(actuel: {age_days}).")
    if score < min_score:
        reasons.append(f"Score d'activité insuffisant ({score}/{min_score}).")
    for k, target in (req_actions or {}).items():
        try:
            target = int(target)
        except (TypeError, ValueError):
            continue
        if int(stats.get(k, 0)) < target:
            label = {"posts": "post(s)", "likes": "like(s) donné(s)",
                     "comments": "commentaire(s)",
                     "transactions": "transaction(s)"}.get(k, k)
            reasons.append(f"Il te manque {target - int(stats.get(k,0))} {label}.")

    return {
        "eligible": len(reasons) == 0,
        "reasons": reasons,
        "stats": stats,
        "score": score,
        "thresholds": {
            "min_age_days": min_age_days,
            "min_score": min_score,
            "required_actions": req_actions,
        },
    }


# ─── Pydantic models ──────────────────────────────────────────────────────
class CreateProjectRequest(BaseModel):
    title: str = Field(..., min_length=4, max_length=160)
    description: str = Field(..., min_length=20, max_length=4000)
    objective: str = Field("", max_length=2000)
    category: Literal["business", "tech", "art", "health", "education",
                      "sport", "community", "emergency"] = "community"
    image_url: str = Field("", max_length=500)
    country_code: str = Field("", max_length=4)
    duration_days: int = Field(30, ge=7, le=180)
    # iter239w — Acceptation explicite des conditions obligatoires.
    # Le frontend doit envoyer terms_accepted=True après scroll + checkbox.
    terms_accepted: bool = Field(False)
    terms_version: str = Field(TERMS_CURRENT_VERSION, max_length=10)


class AdminCycleConfig(BaseModel):
    threshold_projects: Optional[int] = Field(None, ge=2, le=10000)
    votes_to_win: Optional[int] = Field(None, ge=2, le=1_000_000)
    # iter239w — Alias accepté: minimum_votes_required (admin-side terminology)
    minimum_votes_required: Optional[int] = Field(None, ge=2, le=1_000_000)
    reward_amount: Optional[float] = Field(None, ge=0)
    reward_currency: Optional[str] = Field(None, max_length=10)
    # iter239w — Dates configurables par admin à tout moment
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_days: Optional[int] = Field(None, ge=1, le=3650)
    notes: Optional[str] = None


class StartCycleRequest(BaseModel):
    threshold_projects: int = Field(DEFAULT_THRESHOLD_PROJECTS, ge=2, le=10000)
    votes_to_win: int = Field(DEFAULT_VOTES_TO_WIN, ge=2, le=1_000_000)
    # iter239w — Alias terminologie admin
    minimum_votes_required: Optional[int] = Field(None, ge=2, le=1_000_000)
    reward_amount: float = Field(float(DEFAULT_REWARD_AMOUNT), ge=0)
    reward_currency: str = "XAF"
    # iter239w — Dates configurables (sinon défaut: now() + duration_days)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_days: int = Field(DEFAULT_CYCLE_DURATION_DAYS, ge=1, le=3650)
    notes: str = ""


# ─── Public endpoints ─────────────────────────────────────────────────────
@router.get("/categories")
async def list_categories():
    return CATEGORIES_META


@router.get("/state")
async def get_state(request: Request):
    """Global heartbeat — used by every screen to know:
    - is the current cycle active / votes_open
    - how many projects are in (vs threshold)
    - reward & rules summary
    Fully public so anonymous landing pages can show the counter."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        cycle = await _ensure_active_cycle(conn)
        if not cycle:
            # Cycle clôturé — admin doit relancer.
            last = await conn.fetchrow(
                """SELECT * FROM crowdfunding_cycles
                    ORDER BY started_at DESC LIMIT 1"""
            )
            return {
                "cycle": None,
                "between_cycles": True,
                "last_cycle": (
                    {
                        "cycle_number": int(last["cycle_number"]),
                        "status": last["status"],
                        "winner_project_id": last["winner_project_id"],
                        "winner_user_id": last["winner_user_id"],
                        "ended_at": last["ended_at"].isoformat() if last["ended_at"] else None,
                    }
                    if last else None
                ),
                "projects_count": 0,
                "projects_remaining_to_open": 0,
            }
        # Re-check open condition cheaply at every state read.
        cycle = await _maybe_open_votes(conn, cycle)
        projects_count = await conn.fetchval(
            "SELECT COUNT(*) FROM crowdfunding_projects "
            "WHERE cycle_id = $1 AND status IN ('active','winner')",
            cycle["cycle_id"],
        )

    return {
        "cycle": {
            "cycle_id": cycle["cycle_id"],
            "cycle_number": cycle["cycle_number"],
            "status": cycle["status"],
            "votes_open": bool(cycle["votes_open"]),
            "votes_opened_at": cycle["votes_opened_at"].isoformat() if cycle["votes_opened_at"] else None,
            "threshold_projects": int(cycle["threshold_projects"]),
            "votes_to_win": int(cycle["votes_to_win"]),
            # iter239w — alias terminologie cible
            "minimum_votes_required": int(cycle["votes_to_win"]),
            "reward_amount": str(cycle["reward_amount"]),
            "reward_currency": cycle["reward_currency"],
            "started_at": cycle["started_at"].isoformat() if cycle["started_at"] else None,
            "ended_at": cycle["ended_at"].isoformat() if cycle.get("ended_at") else None,
            "duration_days": int(cycle["duration_days"]) if cycle.get("duration_days") else None,
            "winner_project_id": cycle["winner_project_id"],
        },
        "between_cycles": False,
        "projects_count": int(projects_count),
        "projects_remaining_to_open": max(
            0, int(cycle["threshold_projects"]) - int(projects_count)
        ),
    }


def _project_dict(row: dict, *, voted_by_me: bool = False) -> dict:
    return {
        "project_id": row["project_id"],
        "slug": row["slug"],
        "cycle_id": row["cycle_id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "description": row["description"],
        "objective": row.get("objective", "") or "",
        "category": row["category"],
        "image_url": row.get("image_url", "") or "",
        "country_code": row.get("country_code", "") or "",
        "duration_days": int(row["duration_days"]),
        "ends_at": row["ends_at"].isoformat() if row.get("ends_at") else None,
        "votes_count": int(row["votes_count"]),
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "won_at": row["won_at"].isoformat() if row.get("won_at") else None,
        # iter239w — Champs admin/modération (lecture seule côté public)
        "suspension_reason": row.get("suspension_reason") or "",
        "moderation_reason": row.get("moderation_reason") or "",
        "terms_accepted_at": row["terms_accepted_at"].isoformat() if row.get("terms_accepted_at") else None,
        "terms_version": row.get("terms_version") or "",
        "voted_by_me": bool(voted_by_me),
        # Owner labels — joined in queries below
        "owner_name": row.get("owner_name") or "",
        "owner_avatar": row.get("owner_avatar") or "",
    }


@router.get("/projects")
async def list_projects(
    request: Request,
    sort: Literal["votes", "recent"] = "votes",
    country: Optional[str] = Query(None, max_length=4),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        cycle = await _ensure_active_cycle(conn)
        if not cycle:
            return []
        where = "p.cycle_id = $1 AND p.status IN ('active','winner')"
        args: list = [cycle["cycle_id"]]
        if country:
            args.append(country.upper()[:2])
            where += f" AND UPPER(p.country_code) = ${len(args)}"
        order = "p.votes_count DESC, p.created_at DESC" if sort == "votes" \
                else "p.created_at DESC"
        args.extend([limit, offset])
        sql = f"""
            SELECT p.*, u.first_name, u.last_name, u.username, u.avatar
              FROM crowdfunding_projects p
              JOIN users u ON u.user_id = p.user_id
             WHERE {where}
             ORDER BY {order}
             LIMIT ${len(args)-1} OFFSET ${len(args)}
        """
        rows = await conn.fetch(sql, *args)

        # Optional voted_by_me hint when caller is authenticated
        voted_set = set()
        try:
            me = await get_current_user(request)
            if me and rows:
                pids = [r["project_id"] for r in rows]
                voted = await conn.fetch(
                    "SELECT project_id FROM crowdfunding_votes "
                    "WHERE user_id = $1 AND project_id = ANY($2::varchar[])",
                    me["user_id"], pids,
                )
                voted_set = {v["project_id"] for v in voted}
        except Exception:
            pass

    out = []
    for r in rows:
        d = dict(r)
        d["owner_name"] = (
            f"{d.get('first_name','') or ''} {d.get('last_name','') or ''}".strip()
            or d.get("username") or "Anonyme"
        )
        d["owner_avatar"] = d.get("avatar") or ""
        out.append(_project_dict(d, voted_by_me=r["project_id"] in voted_set))
    return out


@router.get("/projects/{slug}")
async def get_project(slug: str, request: Request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT p.*, u.first_name, u.last_name, u.username, u.avatar,
                      u.country_code AS user_country
                 FROM crowdfunding_projects p
                 JOIN users u ON u.user_id = p.user_id
                WHERE p.slug = $1""",
            slug,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        # Compute global rank (within cycle)
        rank = await conn.fetchval(
            """SELECT 1 + COUNT(*) FROM crowdfunding_projects
                WHERE cycle_id = $1 AND status IN ('active','winner')
                  AND (votes_count > $2
                       OR (votes_count = $2 AND created_at < $3))""",
            row["cycle_id"], row["votes_count"], row["created_at"],
        )

        voted_by_me = False
        try:
            me = await get_current_user(request)
            if me:
                voted_by_me = await conn.fetchval(
                    "SELECT 1 FROM crowdfunding_votes WHERE user_id = $1 AND project_id = $2",
                    me["user_id"], row["project_id"],
                ) is not None
        except Exception:
            pass

    d = dict(row)
    d["owner_name"] = (
        f"{d.get('first_name','') or ''} {d.get('last_name','') or ''}".strip()
        or d.get("username") or "Anonyme"
    )
    d["owner_avatar"] = d.get("avatar") or ""
    out = _project_dict(d, voted_by_me=voted_by_me)
    out["rank"] = int(rank or 0)
    return out


# ─── Viral share — OG cards + WhatsApp text ──────────────────────────────
def _frontend_base() -> str:
    return (os.environ.get("FRONTEND_URL")
            or os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")


async def _project_share_payload(conn, slug: str) -> dict:
    row = await conn.fetchrow(
        """SELECT p.*, u.first_name, u.last_name, u.username, u.avatar
             FROM crowdfunding_projects p
             JOIN users u ON u.user_id = p.user_id
            WHERE p.slug = $1""",
        slug,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    cycle = await conn.fetchrow(
        "SELECT cycle_number, votes_to_win FROM crowdfunding_cycles WHERE cycle_id = $1",
        row["cycle_id"],
    )
    owner_name = (
        f"{row.get('first_name','') or ''} {row.get('last_name','') or ''}".strip()
        or row.get("username") or "Anonyme"
    )
    return {
        "slug": slug,
        "title": row["title"],
        "owner_name": owner_name,
        "country_code": row["country_code"] or "",
        "votes_count": int(row["votes_count"]),
        "votes_to_win": int(cycle["votes_to_win"] if cycle else 100),
        "cycle_number": int(cycle["cycle_number"] if cycle else 1),
        "project_image_url": row["image_url"] or "",
    }


@router.get("/projects/{slug}/share")
async def project_share_links(slug: str, request: Request):
    """Returns ready-to-use share URLs (WhatsApp pre-filled message,
    Telegram, X, OG share URL with rich preview, direct landing URL).

    iter169 — Every shareable URL now embeds `?ref={inviter_id}` so the
    visit-tracking endpoint can attribute clicks back to the inviter.
    The OG endpoint receives the same query string so it can pass the
    ref into the redirect to the landing page.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        info = await _project_share_payload(conn, slug)

    base = _frontend_base()
    # iter169 — best-effort attribution: when the sharer is logged-in,
    # tag every outbound link with their user_id. Anonymous viewers
    # cannot recruit (no inviter) — that's expected.
    inviter_id = ""
    try:
        viewer = await get_current_user(request)
        inviter_id = (viewer or {}).get("user_id", "") or ""
    except Exception:
        inviter_id = ""
    ref_qs = f"?ref={inviter_id}" if inviter_id else ""

    landing_url = (f"{base}/crowdfunding/p/{slug}{ref_qs}" if base
                   else f"/crowdfunding/p/{slug}{ref_qs}")
    # Visit-tracking endpoint that records the click and 303-redirects
    # to the landing. We point share targets at this URL so every shared
    # WhatsApp/Telegram/Twitter click goes through the tracker.
    visit_tracker = (f"{base}/api/crowdfunding/projects/{slug}/visit{ref_qs}"
                     if base else f"/api/crowdfunding/projects/{slug}/visit{ref_qs}")
    share_url = f"{base}/api/og/crowdfunding/{slug}" if base else f"/api/og/crowdfunding/{slug}"
    light_story_url = f"{base}/api/crowdfunding/projects/{slug}/share-card?format=story&tier=light" if base else f"/api/crowdfunding/projects/{slug}/share-card?format=story&tier=light"
    light_landscape_url = f"{base}/api/crowdfunding/projects/{slug}/share-card?format=landscape&tier=light" if base else f"/api/crowdfunding/projects/{slug}/share-card?format=landscape&tier=light"
    hd_story_url = f"{base}/api/crowdfunding/projects/{slug}/share-card?format=story&tier=hd" if base else f"/api/crowdfunding/projects/{slug}/share-card?format=story&tier=hd"
    hd_landscape_url = f"{base}/api/crowdfunding/projects/{slug}/share-card?format=landscape&tier=hd" if base else f"/api/crowdfunding/projects/{slug}/share-card?format=landscape&tier=hd"

    text = (
        f"Aide-moi à atteindre {info['votes_to_win']} votes sur JAPAP ❤️\n"
        f"« {info['title']} » — {info['votes_count']}/{info['votes_to_win']} votes\n"
        f"Clique et vote en 1 sec : {visit_tracker}"
    )
    return {
        "slug": slug,
        "title": info["title"],
        "owner_name": info["owner_name"],
        "votes_count": info["votes_count"],
        "votes_to_win": info["votes_to_win"],
        "landing_url": landing_url,
        "share_url": share_url,
        "visit_tracker_url": visit_tracker,  # iter169 — exposed for clients
        "inviter_id": inviter_id,
        # Default = light WebP (mobile-first)
        "png_story_url": light_story_url,
        "png_landscape_url": light_landscape_url,
        "card_story_url_light": light_story_url,
        "card_landscape_url_light": light_landscape_url,
        "card_story_url_hd": hd_story_url,
        "card_landscape_url_hd": hd_landscape_url,
        "share_text": text,
        "whatsapp_url": f"https://wa.me/?text={_url_encode(text)}",
        "telegram_url": f"https://t.me/share/url?url={_url_encode(visit_tracker)}&text={_url_encode(text)}",
        "twitter_url": f"https://twitter.com/intent/tweet?text={_url_encode(text)}",
    }


def _url_encode(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def _negotiate_image_format(request: Request, requested: str) -> str:
    """Pick output encoding: explicit query param wins, else use Accept
    header (WhatsApp/Telegram/iOS Safari all advertise image/webp now)."""
    if requested in ("webp", "jpeg", "jpg"):
        return "jpeg" if requested == "jpg" else requested
    accept = (request.headers.get("Accept") or "").lower()
    if "image/webp" in accept:
        return "webp"
    return "jpeg"


@router.get("/projects/{slug}/share-card.png", include_in_schema=False)
@router.get("/projects/{slug}/share-card")
async def project_share_card(
    slug: str,
    request: Request,
    format: Literal["story", "landscape"] = Query("story"),
    tier: Literal["light", "hd"] = Query("light"),
    fmt: Literal["webp", "jpeg", "jpg", "auto"] = Query("auto"),
):
    """Renders the viral share card.

    Defaults are mobile-first:
      - tier=light (smaller dimensions, ~50-120 KB)
      - fmt=auto (WebP if Accept header allows, else JPEG)

    HD opt-in via `?tier=hd` (still under 200 KB target).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        info = await _project_share_payload(conn, slug)

    base = _frontend_base()
    vote_url = f"{base}/api/og/crowdfunding/{slug}" if base else f"/api/og/crowdfunding/{slug}"
    info.pop("slug", None)

    out_fmt = _negotiate_image_format(request, fmt)

    try:
        if format == "landscape":
            data = crowdfunding_share_card.render_landscape_card(
                vote_url=vote_url, tier=tier, fmt=out_fmt, **info,
            )
        else:
            data = crowdfunding_share_card.render_story_card(
                vote_url=vote_url, tier=tier, fmt=out_fmt, **info,
            )
    except Exception as e:
        logger.error(f"[crowdfunding] share card render failed slug={slug}: {e}")
        raise HTTPException(status_code=500, detail="Card rendering failed.")
    media = "image/webp" if out_fmt == "webp" else "image/jpeg"
    return Response(
        content=data, media_type=media,
        headers={
            "Cache-Control": "public, max-age=600",
            "Vary": "Accept",
            "Content-Disposition": f'inline; filename="japap-{slug}-{format}-{tier}.{out_fmt}"',
        },
    )


# ─── Authenticated endpoints ──────────────────────────────────────────────
@router.get("/me")
async def my_dashboard(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        cycle = await _ensure_active_cycle(conn)
        if not cycle:
            elig = await _eligibility_check(conn, user["user_id"])
            return {
                "cycle_id": None,
                "between_cycles": True,
                "votes_open": False,
                "votes_to_win": 0,
                "votes_given_this_cycle": 0,
                "eligibility": elig,
                "project": None,
            }
        proj = await conn.fetchrow(
            """SELECT p.*, u.first_name, u.last_name, u.username, u.avatar
                 FROM crowdfunding_projects p
                 JOIN users u ON u.user_id = p.user_id
                WHERE p.user_id = $1 AND p.cycle_id = $2
                  AND p.status IN ('active','winner','pending_review','suspended')""",
            user["user_id"], cycle["cycle_id"],
        )
        elig = await _eligibility_check(conn, user["user_id"])
        votes_given = await conn.fetchval(
            "SELECT COUNT(*) FROM crowdfunding_votes WHERE user_id = $1 AND cycle_id = $2",
            user["user_id"], cycle["cycle_id"],
        )

    out: dict = {
        "cycle_id": cycle["cycle_id"],
        "votes_open": bool(cycle["votes_open"]),
        "votes_to_win": int(cycle["votes_to_win"]),
        "votes_given_this_cycle": int(votes_given or 0),
        "eligibility": elig,
        "project": None,
    }
    if proj:
        d = dict(proj)
        d["owner_name"] = (
            f"{d.get('first_name','') or ''} {d.get('last_name','') or ''}".strip()
            or d.get("username") or "Anonyme"
        )
        d["owner_avatar"] = d.get("avatar") or ""
        out["project"] = _project_dict(d)
        # progress %
        out["project"]["progress_pct"] = min(
            100, int(round(int(d["votes_count"]) / max(1, int(cycle["votes_to_win"])) * 100))
        )
        # rank
        async with pool.acquire() as conn:
            rank = await conn.fetchval(
                """SELECT 1 + COUNT(*) FROM crowdfunding_projects
                    WHERE cycle_id = $1 AND status IN ('active','winner')
                      AND (votes_count > $2
                           OR (votes_count = $2 AND created_at < $3))""",
                cycle["cycle_id"], d["votes_count"], d["created_at"],
            )
        out["project"]["rank"] = int(rank or 0)
    return out


@router.post("/projects", status_code=201)
@_limiter.limit("5/hour", exempt_when=lambda: bool(
    os.environ.get("MATH_CAPTCHA_TEST_BYPASS_TOKEN") or
    os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN")
))
async def create_project(req: CreateProjectRequest, request: Request):
    user = await get_current_user(request)
    # iter239w — Conditions de participation OBLIGATOIRES.
    if not req.terms_accepted:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "TERMS_NOT_ACCEPTED",
                "message": "Tu dois accepter les conditions de participation avant de soumettre ton projet.",
            },
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        cycle = await _ensure_active_cycle(conn)
        if not cycle:
            raise HTTPException(
                status_code=409,
                detail="Aucun cycle actif. Reviens quand un nouveau cycle sera lancé.",
            )

        # Duplicate guard (DB partial UNIQUE catches race conditions, but
        # we surface a friendlier error here).
        existing = await conn.fetchval(
            """SELECT project_id FROM crowdfunding_projects
                WHERE user_id = $1 AND cycle_id = $2
                  AND status IN ('active','winner','pending_review','suspended')""",
            user["user_id"], cycle["cycle_id"],
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail="Tu as déjà un projet actif dans ce cycle. Un seul projet par utilisateur.",
            )

        # Eligibility
        elig = await _eligibility_check(conn, user["user_id"])
        if not elig["eligible"]:
            raise HTTPException(
                status_code=403,
                detail={"message": "Tu n'es pas encore éligible.", **elig},
            )

        # iter239w — Statut initial : 'pending_review' sauf si admin a activé
        # l'auto-approbation via settings. Zéro hardcode.
        auto_approve = await get_bool("crowdfunding_auto_approve_projects",
                                       DEFAULT_AUTO_APPROVE_PROJECTS)
        initial_status = "active" if auto_approve else "pending_review"

        # Slug + insert
        slug = _slugify(req.title)
        # Cosmetic uniqueness retry (DB UNIQUE gives 23505 if collision)
        for _ in range(3):
            taken = await conn.fetchval(
                "SELECT 1 FROM crowdfunding_projects WHERE slug = $1", slug
            )
            if not taken:
                break
            slug = _slugify(req.title)

        project_id = f"prj_{uuid.uuid4().hex[:14]}"
        ends_at = datetime.now(timezone.utc) + timedelta(days=int(req.duration_days))

        try:
            await conn.execute(
                """INSERT INTO crowdfunding_projects
                    (project_id, slug, cycle_id, user_id, title, description,
                     objective, category, image_url, country_code,
                     duration_days, ends_at, status,
                     terms_accepted_at, terms_version)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                           NOW(), $14)""",
                project_id, slug, cycle["cycle_id"], user["user_id"],
                req.title.strip(), req.description.strip(),
                (req.objective or "").strip(), req.category,
                (req.image_url or "").strip(),
                (req.country_code or user.get("country_code") or "").upper()[:2],
                int(req.duration_days), ends_at, initial_status,
                (req.terms_version or TERMS_CURRENT_VERSION)[:10],
            )
        except asyncpg_unique_violation():  # pragma: no cover
            raise HTTPException(status_code=409,
                                detail="Conflit — réessaie dans une seconde.")

        # Maybe open votes if this insert hit the threshold (only counts
        # 'active' or 'winner' projects, NOT 'pending_review').
        cycle = await _maybe_open_votes(conn, cycle)

    logger.info(f"[crowdfunding] project created user={user['user_id']} "
                f"slug={slug} status={initial_status}")
    return {"project_id": project_id, "slug": slug,
            "status": initial_status,
            "votes_open": cycle["votes_open"]}


def asyncpg_unique_violation():
    # asyncpg.exceptions.UniqueViolationError, imported lazily so module
    # loads even if asyncpg isn't on the path during static analysis.
    import asyncpg
    return asyncpg.exceptions.UniqueViolationError



# ──────────────────────────────────────────────────────────────────────────
#  iter142E — P3 add-ons: project lifecycle (owner edit/delete + admin)
# ──────────────────────────────────────────────────────────────────────────

class UpdateProjectRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=4, max_length=160)
    description: Optional[str] = Field(None, min_length=20, max_length=4000)
    objective: Optional[str] = Field(None, max_length=2000)
    category: Optional[str] = Field(None, max_length=32)
    image_url: Optional[str] = Field(None, max_length=500)
    country_code: Optional[str] = Field(None, max_length=4)


@router.put("/projects/{slug}")
async def update_my_project(slug: str, req: UpdateProjectRequest, request: Request):
    """Owner-only update — allowed ONLY while votes have not started."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        proj = await conn.fetchrow(
            """SELECT p.*, c.votes_open
                 FROM crowdfunding_projects p
                 JOIN crowdfunding_cycles c ON c.cycle_id = p.cycle_id
                WHERE p.slug = $1""",
            slug,
        )
        if not proj:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        if proj["user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="Tu n'es pas l'auteur de ce projet.")
        if proj["status"] != "active":
            raise HTTPException(status_code=409, detail="Projet non modifiable.")
        if proj["votes_open"] or int(proj["votes_count"]) > 0:
            raise HTTPException(
                status_code=409,
                detail="Modification impossible : les votes ont commencé.",
            )

        fields = req.model_dump(exclude_unset=True)
        if not fields:
            raise HTTPException(status_code=400, detail="Aucun changement fourni.")
        if "country_code" in fields and fields["country_code"]:
            fields["country_code"] = fields["country_code"].upper()[:2]

        sets, args = [], []
        for k, v in fields.items():
            args.append(v)
            sets.append(f"{k} = ${len(args)}")
        args.append(slug)
        await conn.execute(
            f"UPDATE crowdfunding_projects SET {', '.join(sets)}, updated_at = NOW() "
            f"WHERE slug = ${len(args)}",
            *args,
        )
        await engagement_ai_engine.track_event(
            conn, user["user_id"], "create_project",
            project_id=proj["project_id"], cycle_id=proj["cycle_id"],
            metadata={"action": "update", "fields": list(fields.keys())},
        )
    return {"ok": True, "slug": slug, "updated_fields": list(fields.keys())}


@router.delete("/projects/{slug}")
async def delete_my_project(slug: str, request: Request):
    """Owner soft-delete — allowed ONLY while votes locked AND no votes cast."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        proj = await conn.fetchrow(
            """SELECT p.*, c.votes_open
                 FROM crowdfunding_projects p
                 JOIN crowdfunding_cycles c ON c.cycle_id = p.cycle_id
                WHERE p.slug = $1""",
            slug,
        )
        if not proj:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        if proj["user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="Tu n'es pas l'auteur de ce projet.")
        if proj["status"] != "active":
            raise HTTPException(status_code=409, detail="Projet non supprimable dans cet état.")
        if proj["votes_open"] or int(proj["votes_count"]) > 0:
            raise HTTPException(
                status_code=409,
                detail="Suppression impossible : votes ouverts ou déjà reçus.",
            )
        await conn.execute(
            "UPDATE crowdfunding_projects SET status='cancelled', updated_at=NOW() "
            "WHERE slug=$1", slug,
        )
        logger.info(f"[crowdfunding] owner={user['user_id']} cancelled project slug={slug}")
    return {"ok": True, "slug": slug, "status": "cancelled"}


@router.delete("/admin/projects/{slug}")
async def admin_delete_project(slug: str, request: Request):
    """Admin force-delete — allowed at any time, even after votes."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT project_id, status, user_id FROM crowdfunding_projects WHERE slug=$1",
            slug,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        if row["status"] == "winner":
            raise HTTPException(
                status_code=409,
                detail="Refus : ce projet a déjà gagné. Utilise un autre flux.",
            )
        await conn.execute(
            "UPDATE crowdfunding_projects SET status='deleted', updated_at=NOW() "
            "WHERE slug=$1", slug,
        )
        logger.warning(
            f"[crowdfunding][ADMIN] admin={admin['user_id']} deleted project slug={slug} "
            f"(was status={row['status']}, owner={row['user_id']})"
        )
    return {"ok": True, "slug": slug, "status": "deleted"}


class DisqualifyRequest(BaseModel):
    reason: str = Field("", max_length=500)


@router.post("/admin/projects/{slug}/disqualify")
async def admin_disqualify_project(slug: str, req: DisqualifyRequest,
                                   request: Request):
    """Admin disqualification — keeps the project visible (status='disqualified'),
    excluded from rankings + votes, with public reason."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT project_id, status, user_id FROM crowdfunding_projects WHERE slug=$1",
            slug,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        if row["status"] in ("winner", "deleted"):
            raise HTTPException(
                status_code=409,
                detail="Disqualification impossible dans cet état.",
            )
        await conn.execute(
            """UPDATE crowdfunding_projects
                  SET status='disqualified',
                      moderation_reason = $2,
                      updated_at = NOW()
                WHERE slug = $1""",
            slug, (req.reason or "")[:500],
        )
        logger.warning(
            f"[crowdfunding][ADMIN] admin={admin['user_id']} disqualified slug={slug} "
            f"reason={req.reason!r}"
        )
    return {"ok": True, "slug": slug, "status": "disqualified", "reason": req.reason}



@router.post("/projects/{slug}/vote")
@_limiter.limit("30/minute")
async def cast_vote(slug: str, request: Request):
    """Atomic vote + winner-detection + reward credit.

    Order of operations inside a single connection:
      1. Lookup project FOR UPDATE (locks the row)
      2. Refuse if cycle.votes_open is False
      3. Refuse if voter == owner
      4. INSERT into crowdfunding_votes (UNIQUE blocks double vote)
      5. UPDATE projects SET votes_count = votes_count + 1
      6. If new count >= votes_to_win AND no winner yet → declare winner +
         credit wallet + mark cycle complete (no auto new cycle)."""
    user = await get_current_user(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            project = await conn.fetchrow(
                "SELECT * FROM crowdfunding_projects WHERE slug = $1 FOR UPDATE",
                slug,
            )
            if not project:
                raise HTTPException(status_code=404, detail="Projet introuvable.")
            if project["status"] != "active":
                raise HTTPException(status_code=409,
                                    detail=f"Projet {project['status']}.")
            if project["user_id"] == user["user_id"]:
                raise HTTPException(status_code=400,
                                    detail="Tu ne peux pas voter pour ton propre projet.")

            cycle = await conn.fetchrow(
                "SELECT * FROM crowdfunding_cycles WHERE cycle_id = $1 FOR UPDATE",
                project["cycle_id"],
            )
            cycle_d = dict(cycle)
            cycle_d = await _maybe_open_votes(conn, cycle_d)
            if not cycle_d["votes_open"]:
                projects_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM crowdfunding_projects "
                    "WHERE cycle_id = $1 AND status IN ('active','winner')",
                    cycle["cycle_id"],
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "VOTES_NOT_OPEN",
                        "message": (f"Les votes ouvriront quand le seuil de "
                                    f"{cycle['threshold_projects']} projets sera atteint."),
                        "projects_count": int(projects_count),
                        "threshold_projects": int(cycle["threshold_projects"]),
                    },
                )

            # Insert vote (UNIQUE catches double)
            try:
                vote_row = await conn.fetchrow(
                    """INSERT INTO crowdfunding_votes
                        (project_id, user_id, cycle_id, ip_hash, user_agent_hash, country_code)
                       VALUES ($1,$2,$3,$4,$5,$6)
                       RETURNING id""",
                    project["project_id"], user["user_id"], cycle["cycle_id"],
                    _hash_ip(request), _hash_ua(request),
                    (user.get("country_code") or "").upper()[:2],
                )
                vote_id = vote_row["id"] if vote_row else None
            except Exception as e:
                if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
                    raise HTTPException(status_code=409,
                                        detail="Tu as déjà voté pour ce projet.")
                raise

            # iter169 — attempt recruiter attribution. Anti-self-credit
            # rules (own project, own user) are enforced inside the
            # service. Idempotent per (cycle, inviter, recruit). Failure
            # to credit must NEVER abort the vote — it's a side-channel.
            recruit_credit = {"credited": False, "inviter_id": None,
                              "reason": "skipped"}
            try:
                from services.crowdfunding_recruit_service import (
                    try_credit_recruit, award_tier_badges,
                )
                recruit_credit = await try_credit_recruit(
                    conn,
                    cycle_id=cycle["cycle_id"],
                    recruit_user_id=user["user_id"],
                    vote_id=vote_id,
                    project_id=project["project_id"],
                    project_owner_id=project["user_id"],
                    ip_hash=_hash_ip(request),
                )
                if recruit_credit["credited"]:
                    newly_tiers = await award_tier_badges(
                        conn, recruit_credit["inviter_id"], cycle["cycle_id"])
                    # iter170 — dispatch in-app + email notification for
                    # each new tier reached. Best-effort, never blocks vote.
                    if newly_tiers:
                        try:
                            from services.crowdfunding_recruit_notify import (
                                notify_tier_awarded,
                            )
                            for t_key in newly_tiers:
                                await notify_tier_awarded(
                                    conn, user_id=recruit_credit["inviter_id"],
                                    cycle_id=cycle["cycle_id"], tier_key=t_key,
                                )
                        except Exception as e:
                            logger.warning(f"[crowdfunding] tier notif failed: {e}")
            except Exception as e:
                logger.warning(f"[crowdfunding] recruit credit failed: {e}")

            new_count = int(project["votes_count"]) + 1
            await conn.execute(
                "UPDATE crowdfunding_projects SET votes_count = $1 WHERE project_id = $2",
                new_count, project["project_id"],
            )

            # iter239w — Plus de victoire immédiate. Le gagnant est déterminé
            # uniquement à la clôture du cycle (worker auto_close_expired_cycles
            # ou trigger admin), sur la base du projet avec le plus de votes
            # AYANT atteint minimum_votes_required (ex-votes_to_win).
            won = False
            reward_tx_id = None

    # iter142E: proactive engagement-state invalidation. Reset the project
    # owner's cooldown so his next /engagement/me call yields a fresh
    # message ("Tu viens de recevoir un vote — on accélère"). Same for the
    # voter (so his momentum_score is recomputed quickly).
    try:
        pool2 = await get_pool()
        async with pool2.acquire() as conn2:
            await conn2.execute(
                """UPDATE crowdfunding_engagement_state
                      SET last_message_at = NULL, cooldown_until = NULL
                    WHERE user_id IN ($1, $2)""",
                project["user_id"], user["user_id"],
            )
            await engagement_ai_engine.track_event(
                conn2, user["user_id"], "vote",
                project_id=project["project_id"], cycle_id=cycle["cycle_id"],
            )
    except Exception as e:
        logger.warning(f"[crowdfunding] post-vote engagement refresh skipped: {e}")

    return {
        "ok": True,
        "votes_count": new_count,
        "won": won,
        "reward_tx_id": reward_tx_id,
        "votes_to_win": int(cycle["votes_to_win"]),
        "progress_pct": min(100, int(round(new_count / max(1, int(cycle["votes_to_win"])) * 100))),
    }


async def _credit_winner(conn, project, cycle) -> str:
    """Credit the winner's JAPAP wallet directly (no transfer fee). Returns
    the tx_id. Wallet is auto-created if missing (same pattern as
    routes/wallet.py.send_money)."""
    user_id = project["user_id"]
    amount = Decimal(str(cycle["reward_amount"]))
    currency = cycle["reward_currency"]
    tx_id = f"tx_cf_{uuid.uuid4().hex[:14]}"

    # Make sure wallet exists
    await conn.execute(
        "INSERT INTO wallets (user_id, balance, currency) VALUES ($1, 0, $2) "
        "ON CONFLICT (user_id) DO NOTHING",
        user_id, currency,
    )
    await conn.execute(
        "UPDATE wallets SET balance = balance + $1 WHERE user_id = $2",
        amount, user_id,
    )
    await conn.execute(
        """INSERT INTO transactions
            (tx_id, from_user_id, to_user_id, amount, currency, type,
             status, notes, created_at)
           VALUES ($1, NULL, $2, $3, $4, 'crowdfunding_reward', 'completed', $5, NOW())""",
        tx_id, user_id, amount, currency,
        f"Récompense Crowdfunding cycle {cycle['cycle_id']} — projet {project['slug']}",
    )

    # Best-effort notification + badge — savepoint isolates failures so they
    # never abort the parent transaction (the wallet credit must NEVER roll
    # back due to a missing optional table/column).
    try:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                   VALUES ($1, $2, 'crowdfunding_winner', $3, $4, $5::jsonb)""",
                f"notif_{uuid.uuid4().hex[:14]}", user_id,
                "🎉 Tu as gagné le challenge JAPAP !",
                f"Bravo ! Tu reçois {amount} {currency} dans ton wallet.",
                f'{{"project_id":"{project["project_id"]}","tx_id":"{tx_id}"}}',
            )
    except Exception as e:
        logger.warning(f"[crowdfunding] winner notification skipped: {e}")
    try:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO user_badges (user_id, badge_id, earned_at)
                     VALUES ($1, 'crowdfunding_winner', NOW())
                   ON CONFLICT (user_id, badge_id) DO NOTHING""",
                user_id,
            )
    except Exception as e:
        logger.warning(f"[crowdfunding] winner badge skipped: {e}")

    return tx_id


# ─── Leaderboard ──────────────────────────────────────────────────────────
@router.get("/leaderboard")
async def leaderboard(
    scope: Literal["global", "country"] = "global",
    country: Optional[str] = Query(None, max_length=4),
    limit: int = Query(20, ge=1, le=100),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        cycle = await _ensure_active_cycle(conn)
        if not cycle:
            return []
        where = "p.cycle_id = $1 AND p.status IN ('active','winner')"
        args: list = [cycle["cycle_id"]]
        if scope == "country" and country:
            args.append(country.upper()[:2])
            where += f" AND UPPER(p.country_code) = ${len(args)}"
        args.append(limit)
        sql = f"""
            SELECT p.project_id, p.slug, p.title, p.votes_count, p.country_code,
                   p.image_url, p.status,
                   u.first_name, u.last_name, u.username, u.avatar
              FROM crowdfunding_projects p
              JOIN users u ON u.user_id = p.user_id
             WHERE {where}
             ORDER BY p.votes_count DESC, p.created_at ASC
             LIMIT ${len(args)}
        """
        rows = await conn.fetch(sql, *args)
    return [
        {
            "rank": i + 1,
            "project_id": r["project_id"],
            "slug": r["slug"],
            "title": r["title"],
            "votes_count": int(r["votes_count"]),
            "country_code": r["country_code"] or "",
            "image_url": r["image_url"] or "",
            "status": r["status"],
            "owner_name": (
                f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
                or r["username"] or "Anonyme"
            ),
            "owner_avatar": r["avatar"] or "",
        }
        for i, r in enumerate(rows)
    ]


# ─── Admin endpoints ──────────────────────────────────────────────────────
@router.post("/admin/cycles", status_code=201)
async def admin_start_cycle(req: StartCycleRequest, request: Request):
    """Manually start a NEW cycle. Requires admin. The previous active
    cycle (if any) MUST already be 'completed' or will be force-archived
    here. Resets votes/projects via the new cycle_id (old data preserved
    for history)."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await _get_active_cycle(conn)
            if current and current["status"] == "active":
                # Archive in-flight cycle so the new one becomes the only active.
                await conn.execute(
                    "UPDATE crowdfunding_cycles SET status = 'archived', "
                    "ended_at = NOW() WHERE cycle_id = $1",
                    current["cycle_id"],
                )

            number = await conn.fetchval(
                "SELECT COALESCE(MAX(cycle_number),0)+1 FROM crowdfunding_cycles"
            )
            cycle_id = f"cycle_{uuid.uuid4().hex[:12]}"
            # iter239w — minimum_votes_required alias supplante votes_to_win si fourni
            votes_to_win = int(req.minimum_votes_required or req.votes_to_win)
            now = datetime.now(timezone.utc)
            started_at = req.started_at or now
            ended_at = req.ended_at or (started_at + timedelta(days=int(req.duration_days)))
            await conn.execute(
                """INSERT INTO crowdfunding_cycles
                    (cycle_id, cycle_number, status, threshold_projects,
                     votes_to_win, reward_amount, reward_currency,
                     votes_open, created_by_admin, notes,
                     started_at, ended_at, duration_days)
                   VALUES ($1,$2,'active',$3,$4,$5,$6,FALSE,$7,$8,$9,$10,$11)""",
                cycle_id, number, req.threshold_projects, votes_to_win,
                Decimal(str(req.reward_amount)), req.reward_currency,
                admin["user_id"], req.notes,
                started_at, ended_at, int(req.duration_days),
            )
    return {
        "cycle_id": cycle_id, "cycle_number": number, "status": "active",
        "started_at": started_at.isoformat(), "ended_at": ended_at.isoformat(),
        "minimum_votes_required": votes_to_win,
    }


@router.put("/admin/cycles/active")
async def admin_update_active_cycle(req: AdminCycleConfig, request: Request):
    """Tweak the active cycle's parameters (threshold, minimum votes, reward,
    dates). iter239w: dates configurables à tout moment tant que pas de gagnant."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        cycle = await _get_active_cycle(conn)
        if not cycle:
            raise HTTPException(status_code=404, detail="Aucun cycle actif.")
        if cycle["winner_project_id"]:
            raise HTTPException(status_code=409,
                                detail="Cycle déjà clôturé (gagnant désigné).")
        sets, args = [], []
        if req.threshold_projects is not None:
            sets.append(f"threshold_projects = ${len(args)+1}")
            args.append(int(req.threshold_projects))
        # iter239w — minimum_votes_required alias prioritaire
        mvr = req.minimum_votes_required if req.minimum_votes_required is not None else req.votes_to_win
        if mvr is not None:
            sets.append(f"votes_to_win = ${len(args)+1}")
            args.append(int(mvr))
        if req.reward_amount is not None:
            sets.append(f"reward_amount = ${len(args)+1}")
            args.append(Decimal(str(req.reward_amount)))
        if req.reward_currency is not None:
            sets.append(f"reward_currency = ${len(args)+1}")
            args.append(req.reward_currency)
        if req.started_at is not None:
            sets.append(f"started_at = ${len(args)+1}")
            args.append(req.started_at)
        if req.ended_at is not None:
            sets.append(f"ended_at = ${len(args)+1}")
            args.append(req.ended_at)
        if req.duration_days is not None:
            sets.append(f"duration_days = ${len(args)+1}")
            args.append(int(req.duration_days))
        if req.notes is not None:
            sets.append(f"notes = ${len(args)+1}")
            args.append(req.notes)
        if not sets:
            raise HTTPException(status_code=400, detail="Aucun champ à mettre à jour.")
        args.append(cycle["cycle_id"])
        await conn.execute(
            f"UPDATE crowdfunding_cycles SET {', '.join(sets)} "
            f"WHERE cycle_id = ${len(args)}",
            *args,
        )
    return {"ok": True}


@router.get("/admin/cycles")
async def admin_list_cycles(request: Request, limit: int = Query(50, ge=1, le=500)):
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT cycle_id, cycle_number, status, threshold_projects,
                      votes_to_win, reward_amount, reward_currency,
                      votes_open, votes_opened_at, started_at, ended_at,
                      winner_project_id, winner_user_id, notes
                 FROM crowdfunding_cycles
                 ORDER BY started_at DESC LIMIT $1""",
            limit,
        )
    return [
        {
            **{k: (v.isoformat() if isinstance(v, datetime) else
                   (str(v) if isinstance(v, Decimal) else v))
               for k, v in dict(r).items()}
        }
        for r in rows
    ]


@router.get("/admin/settings")
async def admin_get_settings(request: Request):
    await _require_admin(request)
    return {
        "min_account_age_days": int(await get_setting(
            "crowdfunding_min_account_age_days",
            DEFAULT_MIN_ACCOUNT_AGE_DAYS) or DEFAULT_MIN_ACCOUNT_AGE_DAYS),
        "min_activity_score": int(await get_setting(
            "crowdfunding_min_activity_score",
            DEFAULT_MIN_ACTIVITY_SCORE) or DEFAULT_MIN_ACTIVITY_SCORE),
        "required_actions": await get_json(
            "crowdfunding_required_actions",
            DEFAULT_REQUIRED_ACTIONS) or DEFAULT_REQUIRED_ACTIONS,
        "default_threshold_projects": int(await get_setting(
            "crowdfunding_threshold_projects",
            DEFAULT_THRESHOLD_PROJECTS) or DEFAULT_THRESHOLD_PROJECTS),
        "default_votes_to_win": int(await get_setting(
            "crowdfunding_votes_to_win",
            DEFAULT_VOTES_TO_WIN) or DEFAULT_VOTES_TO_WIN),
        # iter239w — alias
        "default_minimum_votes_required": int(await get_setting(
            "crowdfunding_votes_to_win",
            DEFAULT_VOTES_TO_WIN) or DEFAULT_VOTES_TO_WIN),
        "default_reward_amount": str(await get_setting(
            "crowdfunding_reward_amount",
            DEFAULT_REWARD_AMOUNT) or DEFAULT_REWARD_AMOUNT),
        "default_reward_currency": str(await get_setting(
            "crowdfunding_reward_currency",
            DEFAULT_REWARD_CURRENCY) or DEFAULT_REWARD_CURRENCY),
        "default_cycle_duration_days": int(await get_setting(
            "crowdfunding_default_cycle_duration_days",
            DEFAULT_CYCLE_DURATION_DAYS) or DEFAULT_CYCLE_DURATION_DAYS),
        "auto_approve_projects": await get_bool(
            "crowdfunding_auto_approve_projects",
            DEFAULT_AUTO_APPROVE_PROJECTS),
        "terms_current_version": TERMS_CURRENT_VERSION,
    }


class AdminUpdateSettings(BaseModel):
    min_account_age_days: Optional[int] = Field(None, ge=0, le=365)
    min_activity_score: Optional[int] = Field(None, ge=0, le=10000)
    required_actions: Optional[dict] = None
    default_threshold_projects: Optional[int] = Field(None, ge=2, le=10000)
    default_votes_to_win: Optional[int] = Field(None, ge=2, le=1_000_000)
    # iter239w — alias prioritaire
    default_minimum_votes_required: Optional[int] = Field(None, ge=2, le=1_000_000)
    default_reward_amount: Optional[float] = Field(None, ge=0)
    default_reward_currency: Optional[str] = Field(None, max_length=10)
    default_cycle_duration_days: Optional[int] = Field(None, ge=1, le=3650)
    auto_approve_projects: Optional[bool] = None


@router.put("/admin/settings")
async def admin_update_settings(req: AdminUpdateSettings, request: Request):
    await _require_admin(request)
    if req.min_account_age_days is not None:
        await set_setting("crowdfunding_min_account_age_days",
                          int(req.min_account_age_days))
    if req.min_activity_score is not None:
        await set_setting("crowdfunding_min_activity_score",
                          int(req.min_activity_score))
    if req.required_actions is not None:
        await set_setting("crowdfunding_required_actions",
                          req.required_actions)
    if req.default_threshold_projects is not None:
        await set_setting("crowdfunding_threshold_projects",
                          int(req.default_threshold_projects))
    # iter239w — minimum_votes_required alias prioritaire
    mvr = req.default_minimum_votes_required if req.default_minimum_votes_required is not None else req.default_votes_to_win
    if mvr is not None:
        await set_setting("crowdfunding_votes_to_win", int(mvr))
    if req.default_reward_amount is not None:
        await set_setting("crowdfunding_reward_amount",
                          str(req.default_reward_amount))
    if req.default_reward_currency is not None:
        await set_setting("crowdfunding_reward_currency",
                          req.default_reward_currency)
    if req.default_cycle_duration_days is not None:
        await set_setting("crowdfunding_default_cycle_duration_days",
                          int(req.default_cycle_duration_days))
    if req.auto_approve_projects is not None:
        await set_setting("crowdfunding_auto_approve_projects",
                          bool(req.auto_approve_projects))
    return await admin_get_settings(request)


# ──────────────────────────────────────────────────────────────────────────
#  iter142D — P3 Engagement IA engine
#  Behaviour-aware banner + event tracking + message performance loop.
# ──────────────────────────────────────────────────────────────────────────

class TrackEventRequest(BaseModel):
    event_type: str = Field(..., min_length=2, max_length=32)
    project_id: Optional[str] = None
    cycle_id: Optional[str] = None
    rank_before: Optional[int] = None
    rank_after: Optional[int] = None
    time_spent: int = 0
    source: str = "direct"
    metadata: Optional[dict] = None


@router.post("/events")
async def track_engagement_event(req: TrackEventRequest, request: Request):
    """Lightweight tracker — never blocks UX, fire-and-forget on the FE side."""
    user = await get_current_user(request)
    if req.event_type not in {
        "view", "vote", "share", "invite", "visit_generated",
        "create_project", "click_message", "dismiss_message", "session",
    }:
        raise HTTPException(status_code=400, detail="event_type invalide")
    pool = await get_pool()
    async with pool.acquire() as conn:
        evt_id = await engagement_ai_engine.track_event(
            conn, user["user_id"], req.event_type,
            project_id=req.project_id, cycle_id=req.cycle_id,
            rank_before=req.rank_before, rank_after=req.rank_after,
            time_spent=req.time_spent, source=req.source,
            metadata=req.metadata,
        )
    return {"event_id": evt_id, "ok": True}


@router.get("/engagement/me")
async def get_my_engagement(request: Request, with_llm: bool = Query(True)):
    """Return the live engagement payload for the current user.

    Falls back to a 'cold' dummy state for anonymous visitors so the FE can
    display a generic onboarding banner without an extra round-trip."""
    try:
        user = await get_current_user(request)
    except Exception:
        return {
            "user_id": None, "state": "cold", "ui_mode": "calm",
            "urgency_score": 0, "frustration_score": 0, "momentum_score": 0,
            "engagement_score": 0, "engagement_category": "low",
            "next_best_action": "vote",
            "message": "Découvre les projets en lice — vote pour celui qui t'inspire ❤️",
            "message_id": "anon_v1", "context": {}, "rank": 0, "rival": None,
            "vote_velocity_10m": 0,
            "share_performance": {
                "last_share_at": None, "last_share_votes": 0,
                "last_share_clicks": 0, "conversion_rate": 0.0,
                "best_channel": None,
            },
            "cooldown_until": None, "llm_personalised": False,
        }
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await engagement_ai_engine.compute_user_engagement_state(
            conn, user["user_id"], with_llm=with_llm,
        )


@router.get("/share-performance/me")
async def my_share_performance(request: Request):
    """Standalone endpoint requested by the CEO — for cheap polling on the
    MyDashboard widget without firing the full engagement pipeline."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await engagement_ai_engine.get_share_performance(
            conn, user["user_id"],
        )


@router.get("/rival")
async def my_rival(request: Request):
    """Quick lookup for the project just above the user (live rivalry)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        proj = await conn.fetchrow(
            """SELECT * FROM crowdfunding_projects
                WHERE user_id=$1 AND status IN ('active','winner')
                ORDER BY created_at DESC LIMIT 1""",
            user["user_id"],
        )
        if not proj:
            return {"rival": None, "rank": 0,
                    "reason": "Pas de projet actif."}
        info = await engagement_ai_engine._project_rank_and_rival(
            conn, dict(proj),
        )
        velocity = await engagement_ai_engine.get_vote_velocity(
            conn, proj["project_id"],
        )
    if not info.get("rival_name"):
        return {"rival": None, "rank": info["rank"],
                "vote_velocity_10m": velocity,
                "reason": "Tu es 1er — défends ta position !"}
    return {
        "rival": {
            "name": info["rival_name"],
            "votes": info["rival_votes"],
            "slug": info["rival_slug"],
            "trend": info["trend"],
        },
        "rank": info["rank"],
        "gap": int(info["rival_votes"]) - int(proj["votes_count"]),
        "my_votes": int(proj["votes_count"]),
        "vote_velocity_10m": velocity,
    }


class FeedbackRequest(BaseModel):
    message_id: str = Field(..., min_length=2, max_length=64)
    action: Literal["clicked", "dismissed", "shared"]


@router.post("/engagement/feedback")
async def engagement_feedback(req: FeedbackRequest, request: Request):
    await get_current_user(request)  # require auth
    pool = await get_pool()
    async with pool.acquire() as conn:
        await engagement_ai_engine.record_feedback(conn, req.message_id, req.action)
    return {"ok": True}


@router.get("/admin/engagement/messages")
async def admin_message_performance(request: Request,
                                    state: Optional[str] = Query(None)):
    """Top performing messages (admin only) — sort by conversion rate."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT message_id, state, variant_text, shown_count,
                      clicked_count, dismissed_count, shared_count,
                      last_shown_at,
                      CASE WHEN shown_count > 0
                           THEN (clicked_count + shared_count)::float / shown_count
                           ELSE 0 END AS conversion_rate
                 FROM crowdfunding_message_performance
                WHERE ($1::text IS NULL OR state = $1)
                ORDER BY conversion_rate DESC, shown_count DESC""",
            state,
        )
    return [
        {
            "message_id": r["message_id"],
            "state": r["state"],
            "variant_text": r["variant_text"],
            "shown_count": int(r["shown_count"]),
            "clicked_count": int(r["clicked_count"]),
            "dismissed_count": int(r["dismissed_count"]),
            "shared_count": int(r["shared_count"]),
            "conversion_rate": round(float(r["conversion_rate"]), 4),
            "last_shown_at": r["last_shown_at"].isoformat() if r["last_shown_at"] else None,
        }
        for r in rows
    ]




# ╔══════════════════════════════════════════════════════════════════╗
# ║ iter169 — Crowdfunding recruiter tracking (P1)                   ║
# ╚══════════════════════════════════════════════════════════════════╝
@router.get("/projects/{slug}/visit")
async def record_share_visit(slug: str, request: Request,
                              ref: str = Query("", description="Inviter user_id"),
                              src: str = Query("", description="utm_source")):
    """Logs a visit triggered by a shared invite link, then redirects to
    the canonical landing page. The frontend should append `?ref={user_id}`
    to every shareable URL it generates (already done by /share endpoint
    via iter169 patch).

    Anti-fraud: capped at 3 visits per (ip_hash, inviter, cycle).
    """
    from fastapi.responses import RedirectResponse
    from services.crowdfunding_recruit_service import record_invite_visit

    pool = await get_pool()
    landing = f"/crowdfunding/p/{slug}"
    async with pool.acquire() as conn:
        project = await conn.fetchrow(
            "SELECT project_id, cycle_id FROM crowdfunding_projects WHERE slug = $1",
            slug,
        )
        if not project:
            return RedirectResponse(landing, status_code=303)

        # Best-effort identify the visitor if a session cookie is present.
        viewer_id = None
        try:
            viewer = await get_current_user(request)
            viewer_id = viewer["user_id"] if viewer else None
        except Exception:
            viewer_id = None

        await record_invite_visit(
            conn,
            cycle_id=project["cycle_id"],
            inviter_id=(ref or "")[:64],
            project_slug=slug,
            visitor_user_id=viewer_id,
            ip_hash=_hash_ip(request),
            user_agent_hash=_hash_ua(request),
            utm_source=(src or "")[:40] or None,
        )

    return RedirectResponse(landing, status_code=303)


@router.get("/recruiter/leaderboard")
async def recruiter_leaderboard(request: Request,
                                  cycle_id: Optional[str] = Query(None),
                                  limit: int = Query(50, ge=1, le=200)):
    """Top recruiters for the active (or specified) cycle. Always shows
    the viewer's own rank in `me` even if they're outside the top-N."""
    from services.crowdfunding_recruit_service import cycle_leaderboard
    pool = await get_pool()
    viewer_id = None
    try:
        viewer = await get_current_user(request)
        viewer_id = viewer["user_id"] if viewer else None
    except Exception:
        pass
    async with pool.acquire() as conn:
        if not cycle_id:
            cid = await conn.fetchval(
                "SELECT cycle_id FROM crowdfunding_cycles "
                "WHERE status = 'active' ORDER BY started_at DESC LIMIT 1")
            if not cid:
                return {"items": [], "me": None, "cycle_id": None}
            cycle_id = str(cid)
        data = await cycle_leaderboard(conn, cycle_id, limit=limit,
                                        viewer_id=viewer_id)
    return {**data, "cycle_id": cycle_id}


@router.get("/recruiter/me")
async def my_recruiter_progress(request: Request,
                                  cycle_id: Optional[str] = Query(None)):
    """Personal recruiter progression card for the active cycle."""
    user = await get_current_user(request)
    from services.crowdfunding_recruit_service import my_progress
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not cycle_id:
            cid = await conn.fetchval(
                "SELECT cycle_id FROM crowdfunding_cycles "
                "WHERE status = 'active' ORDER BY started_at DESC LIMIT 1")
            if not cid:
                return {"cycle_id": None, "recruits_count": 0,
                        "visits_count": 0, "tier": None,
                        "next_tier": None, "badges": []}
            cycle_id = str(cid)
        return await my_progress(conn, user["user_id"], cycle_id)


# ──────────────────────────────────────────────────────────────────────────
# iter239w — Refonte logique de victoire + admin total
# ──────────────────────────────────────────────────────────────────────────

class ProjectModerationRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


async def _notify_owner(conn, user_id: str, *, title: str, message: str,
                        notif_type: str, data: str = "{}"):
    """Best-effort in-app notification for project owner. Never raises."""
    try:
        await conn.execute(
            """INSERT INTO notifications (notif_id, user_id, type, title, message, data)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
            f"notif_{uuid.uuid4().hex[:14]}", user_id, notif_type, title, message, data,
        )
    except Exception as e:
        logger.warning(f"[crowdfunding] notify_owner skipped: {e}")


@router.post("/admin/projects/{slug}/approve")
async def admin_approve_project(slug: str, request: Request):
    """iter239w — Approuve un projet en attente (pending_review → active)."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT project_id, status, user_id, title FROM crowdfunding_projects WHERE slug=$1",
            slug,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        if row["status"] != "pending_review":
            raise HTTPException(
                status_code=409,
                detail=f"Projet non en attente (statut actuel: {row['status']}).",
            )
        await conn.execute(
            """UPDATE crowdfunding_projects
                  SET status='active',
                      reviewed_at = NOW(),
                      reviewed_by = $1,
                      updated_at = NOW()
                WHERE slug = $2""",
            admin["user_id"], slug,
        )
        cycle = await _get_active_cycle(conn)
        if cycle:
            await _maybe_open_votes(conn, cycle)
        await _notify_owner(
            conn, row["user_id"],
            title="Ton projet a ete approuve",
            message=f"Ton projet \u00ab {row['title']} \u00bb est maintenant visible.",
            notif_type="crowdfunding_approved",
            data=f'{{"slug":"{slug}"}}',
        )
        logger.warning(f"[crowdfunding][ADMIN] admin={admin['user_id']} approved slug={slug}")
    return {"ok": True, "slug": slug, "status": "active"}


@router.post("/admin/projects/{slug}/suspend")
async def admin_suspend_project(slug: str, req: ProjectModerationRequest,
                                request: Request):
    """iter239w — Suspension temporaire (invisible publique, recuperable)."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT project_id, status, user_id, title FROM crowdfunding_projects WHERE slug=$1",
            slug,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        if row["status"] not in ("active", "pending_review"):
            raise HTTPException(
                status_code=409,
                detail=f"Suspension impossible (statut: {row['status']}).",
            )
        await conn.execute(
            """UPDATE crowdfunding_projects
                  SET status='suspended',
                      suspended_at = NOW(),
                      suspended_by = $1,
                      suspension_reason = $2,
                      updated_at = NOW()
                WHERE slug = $3""",
            admin["user_id"], req.reason, slug,
        )
        await _notify_owner(
            conn, row["user_id"],
            title="Ton projet a ete suspendu",
            message=f"\u00ab {row['title']} \u00bb \u2014 motif : {req.reason}",
            notif_type="crowdfunding_suspended",
            data=f'{{"slug":"{slug}"}}',
        )
        logger.warning(f"[crowdfunding][ADMIN] admin={admin['user_id']} "
                       f"suspended slug={slug} reason={req.reason!r}")
    return {"ok": True, "slug": slug, "status": "suspended", "reason": req.reason}


@router.post("/admin/projects/{slug}/reactivate")
async def admin_reactivate_project(slug: str, request: Request):
    """iter239w — Reactive un projet suspendu (suspended -> active)."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT project_id, status, user_id, title FROM crowdfunding_projects WHERE slug=$1",
            slug,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        if row["status"] != "suspended":
            raise HTTPException(
                status_code=409,
                detail=f"Reactivation impossible (statut: {row['status']}).",
            )
        await conn.execute(
            """UPDATE crowdfunding_projects
                  SET status='active',
                      suspended_at = NULL,
                      suspended_by = NULL,
                      suspension_reason = NULL,
                      updated_at = NOW()
                WHERE slug = $1""",
            slug,
        )
        cycle = await _get_active_cycle(conn)
        if cycle:
            await _maybe_open_votes(conn, cycle)
        await _notify_owner(
            conn, row["user_id"],
            title="Ton projet a ete reactive",
            message=f"\u00ab {row['title']} \u00bb est de nouveau visible.",
            notif_type="crowdfunding_reactivated",
            data=f'{{"slug":"{slug}"}}',
        )
        logger.warning(f"[crowdfunding][ADMIN] admin={admin['user_id']} reactivated slug={slug}")
    return {"ok": True, "slug": slug, "status": "active"}


@router.delete("/admin/projects/{slug}/force-delete")
async def admin_force_delete_project(slug: str, req: ProjectModerationRequest,
                                     request: Request):
    """iter239w — Suppression definitive admin (tout statut sauf 'winner')."""
    admin = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT project_id, status, user_id, title FROM crowdfunding_projects WHERE slug=$1",
            slug,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Projet introuvable.")
        if row["status"] == "winner":
            raise HTTPException(
                status_code=409,
                detail="Refus : ce projet a deja gagne. Suppression interdite.",
            )
        await conn.execute(
            """UPDATE crowdfunding_projects
                  SET status='deleted',
                      moderation_reason = $1,
                      reviewed_at = NOW(),
                      reviewed_by = $2,
                      updated_at = NOW()
                WHERE slug = $3""",
            req.reason, admin["user_id"], slug,
        )
        await _notify_owner(
            conn, row["user_id"],
            title="Ton projet a ete supprime",
            message=f"\u00ab {row['title']} \u00bb \u2014 motif : {req.reason}",
            notif_type="crowdfunding_deleted",
            data=f'{{"slug":"{slug}"}}',
        )
        logger.warning(f"[crowdfunding][ADMIN] admin={admin['user_id']} "
                       f"force-deleted slug={slug} reason={req.reason!r}")
    return {"ok": True, "slug": slug, "status": "deleted", "reason": req.reason}


@router.get("/admin/projects")
async def admin_list_all_projects(
    request: Request,
    status: Optional[str] = Query(None, max_length=32),
    cycle_id: Optional[str] = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """iter239w — Liste admin de TOUS les projets (tous statuts, tous cycles)."""
    await _require_admin(request)
    pool = await get_pool()
    where_parts = []
    args: list = []
    if status:
        args.append(status)
        where_parts.append(f"p.status = ${len(args)}")
    if cycle_id:
        args.append(cycle_id)
        where_parts.append(f"p.cycle_id = ${len(args)}")
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    args.extend([limit, offset])
    sql = f"""
        SELECT p.project_id, p.slug, p.cycle_id, p.user_id, p.title,
               p.category, p.country_code, p.votes_count, p.status,
               p.suspended_at, p.suspension_reason, p.moderation_reason,
               p.terms_accepted_at, p.terms_version,
               p.created_at, p.updated_at,
               u.first_name, u.last_name, u.username
          FROM crowdfunding_projects p
          JOIN users u ON u.user_id = p.user_id
          {where_sql}
         ORDER BY p.created_at DESC
         LIMIT ${len(args)-1} OFFSET ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    out = []
    for r in rows:
        d = dict(r)
        out.append({
            "project_id": d["project_id"], "slug": d["slug"],
            "cycle_id": d["cycle_id"], "user_id": d["user_id"],
            "title": d["title"], "category": d["category"],
            "country_code": d["country_code"] or "",
            "votes_count": int(d["votes_count"]),
            "status": d["status"],
            "suspended_at": d["suspended_at"].isoformat() if d["suspended_at"] else None,
            "suspension_reason": d["suspension_reason"] or "",
            "moderation_reason": d["moderation_reason"] or "",
            "terms_accepted_at": d["terms_accepted_at"].isoformat() if d["terms_accepted_at"] else None,
            "terms_version": d["terms_version"] or "",
            "created_at": d["created_at"].isoformat(),
            "updated_at": d["updated_at"].isoformat() if d["updated_at"] else None,
            "owner_name": (
                f"{d.get('first_name','') or ''} {d.get('last_name','') or ''}".strip()
                or d.get("username") or "Anonyme"
            ),
        })
    return out


# ─── Closing logic ────────────────────────────────────────────────────────
async def close_cycle_and_determine_winner(cycle_id: str) -> dict:
    """iter239w — Cloture atomique d'un cycle.
       Renvoie {result: 'winner'|'no_winner'|'noop', ...}.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cycle = await conn.fetchrow(
                "SELECT * FROM crowdfunding_cycles WHERE cycle_id = $1 FOR UPDATE",
                cycle_id,
            )
            if not cycle:
                return {"result": "noop", "reason": "cycle_not_found"}
            if cycle["status"] != "active":
                return {"result": "noop", "reason": "cycle_not_active",
                        "status": cycle["status"]}

            minimum_votes = int(cycle["votes_to_win"])
            reward_amount = Decimal(str(cycle["reward_amount"]))
            cycle_number = int(cycle["cycle_number"])

            winner = await conn.fetchrow(
                """SELECT project_id, user_id, votes_count, slug, title
                     FROM crowdfunding_projects
                    WHERE cycle_id = $1
                      AND status = 'active'
                      AND votes_count >= $2
                    ORDER BY votes_count DESC, created_at ASC
                    LIMIT 1""",
                cycle_id, minimum_votes,
            )

            if not winner:
                await conn.execute(
                    """UPDATE crowdfunding_cycles
                          SET status='completed', ended_at=COALESCE(ended_at, NOW()),
                              notes = COALESCE(NULLIF(notes,''),'') || ' [auto-closed: no project reached minimum]'
                        WHERE cycle_id = $1""",
                    cycle_id,
                )
                await conn.execute(
                    """UPDATE crowdfunding_projects
                          SET status='expired', updated_at=NOW()
                        WHERE cycle_id = $1 AND status = 'active'""",
                    cycle_id,
                )
                logger.warning(
                    f"[crowdfunding] cycle={cycle_id} closed WITHOUT winner "
                    f"(min_required={minimum_votes})"
                )
                return {"result": "no_winner", "cycle_id": cycle_id,
                        "minimum_votes": minimum_votes}

            reward_tx_id = await _credit_winner(conn, dict(winner), dict(cycle))
            await conn.execute(
                """UPDATE crowdfunding_projects
                      SET status='winner', won_at=NOW(), reward_tx_id=$1,
                          updated_at=NOW()
                    WHERE project_id = $2""",
                reward_tx_id, winner["project_id"],
            )
            await conn.execute(
                """UPDATE crowdfunding_projects
                      SET status='expired', updated_at=NOW()
                    WHERE cycle_id = $1 AND status='active' AND project_id != $2""",
                cycle_id, winner["project_id"],
            )
            await conn.execute(
                """UPDATE crowdfunding_cycles
                      SET status='completed', ended_at=COALESCE(ended_at, NOW()),
                          winner_project_id=$1, winner_user_id=$2
                    WHERE cycle_id = $3""",
                winner["project_id"], winner["user_id"], cycle_id,
            )
            await _notify_owner(
                conn, winner["user_id"],
                title=f"Tu as gagne le cycle #{cycle_number} !",
                message=f"\u00ab {winner['title']} \u00bb remporte {reward_amount} {cycle['reward_currency']}. "
                        f"Verifie ton wallet. Pour retirer, ton KYC doit etre approuve.",
                notif_type="crowdfunding_winner",
                data=f'{{"slug":"{winner["slug"]}","tx_id":"{reward_tx_id}"}}',
            )
            logger.warning(
                f"[crowdfunding] cycle={cycle_id} CLOSED -- winner={winner['slug']} "
                f"({winner['votes_count']} votes, reward={reward_amount} {cycle['reward_currency']})"
            )
            return {
                "result": "winner",
                "cycle_id": cycle_id,
                "winner_project_id": winner["project_id"],
                "winner_user_id": winner["user_id"],
                "winner_slug": winner["slug"],
                "votes_count": int(winner["votes_count"]),
                "reward_tx_id": reward_tx_id,
            }


@router.post("/admin/cycles/{cycle_id}/close")
async def admin_close_cycle(cycle_id: str, request: Request):
    """iter239w — Force la cloture immediate d'un cycle (sans attendre ended_at)."""
    await _require_admin(request)
    return await close_cycle_and_determine_winner(cycle_id)
