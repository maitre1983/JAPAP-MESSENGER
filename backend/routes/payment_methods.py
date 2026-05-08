"""
iter237h — Payment methods catalog & eligibility endpoint (additif).

P1 — `GET /api/payment-methods/eligibility?method=<id>` returns whether the
     calling user can submit a deposit/withdrawal for that method, plus an
     actionable suggestion if not.
P2 — `GET /api/payment-methods` exposes the catalog table for UI rendering
     so adding a method (MTN/Moov/M-Pesa) becomes 1 SQL INSERT, 0 code.

Both endpoints are READ-ONLY and additive — no existing route is changed.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from routes.auth import get_current_user
from routes.admin import require_admin
from services.mobile_money_common import (
    OM_DEPOSIT_BLOCKED_COUNTRIES, OM_ALLOWED_COUNTRIES,
    WAVE_ALLOWED_COUNTRIES_DEFAULT, WAVE_KEYS,
    get_pool, get_user_country, get_setting,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["payment-methods"])

# ---------------------------------------------------------------------------
# DB seed — payment_methods catalog (idempotent, read-only at runtime).
# ---------------------------------------------------------------------------
_SEED = [
    ("orange_money_cm",          "Orange Money",              "🟠", "#FF6600",
     "Cameroun uniquement — numéros +237",
     "CM", True,  1),
    ("wave",                     "Wave",                      "🌊", "#1B9CFC",
     "Burkina Faso · Côte d'Ivoire · Mali · Niger · Sénégal · Gambie · Ouganda",
     "BF,CI,ML,NE,SN,GM,UG", True, 2),
    ("hubtel_card",              "Carte bancaire (Hubtel)",   "💳", "#0F056B",
     "Disponible dans tous les pays", "ALL", True, 3),
    ("nowpayments_usdttrc20",    "USDT (TRC20)",              "⚡", "#26A17B",
     "Disponible dans tous les pays", "ALL", True, 4),
    ("nowpayments_usdtbsc",      "USDT (BEP20)",              "⚡", "#F0B90B",
     "Disponible dans tous les pays", "ALL", True, 5),
]


async def _ensure_payment_methods_table(conn) -> None:
    await conn.execute("""
      CREATE TABLE IF NOT EXISTS payment_methods (
        id                   VARCHAR(64) PRIMARY KEY,
        label                VARCHAR(100) NOT NULL,
        icon                 VARCHAR(10),
        color                VARCHAR(20),
        availability_text    TEXT,
        restricted_countries VARCHAR(200),
        enabled              BOOLEAN DEFAULT TRUE,
        display_order        INTEGER DEFAULT 0,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    """)
    for row in _SEED:
        await conn.execute("""
            INSERT INTO payment_methods
              (id, label, icon, color, availability_text,
               restricted_countries, enabled, display_order)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (id) DO NOTHING
        """, *row)


# iter237i — P2: analytics table for "Vérifier mon éligibilité" /
# form_opened / submitted events. Best-effort, never blocking.
async def _ensure_analytics_table(conn) -> None:
    await conn.execute("""
      CREATE TABLE IF NOT EXISTS payment_method_analytics (
        id            BIGSERIAL PRIMARY KEY,
        user_id       VARCHAR(80),
        method_id     VARCHAR(64) NOT NULL,
        flow          VARCHAR(20) NOT NULL,
        action        VARCHAR(30) NOT NULL,
        eligible      BOOLEAN,
        user_country  VARCHAR(5),
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    """)
    await conn.execute("""
      CREATE INDEX IF NOT EXISTS ix_pma_method_action_created
      ON payment_method_analytics(method_id, action, created_at)
    """)


async def _track_event(user_id: str, method_id: str, flow: str, action: str,
                        eligible: Optional[bool], user_country: Optional[str]) -> None:
    """Best-effort analytics insert. Never raises (logs at warning level)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _ensure_analytics_table(conn)
            await conn.execute("""
              INSERT INTO payment_method_analytics
                (user_id, method_id, flow, action, eligible, user_country)
              VALUES ($1, $2, $3, $4, $5, $6)
            """, user_id, method_id, flow, action, eligible, (user_country or None))
    except Exception as e:
        logger.warning("[iter237i] analytics insert failed: %s", e)


# ---------------------------------------------------------------------------
# P2 — Catalog endpoint
# ---------------------------------------------------------------------------
@router.get("/payment-methods")
async def list_payment_methods(request: Request,
                                category: Optional[str] = Query(default=None,
                                                                regex="^(deposit|withdraw)$")):
    """Returns the public catalog. `category` filter is currently a hint only;
    in practice all entries support both deposit and withdraw, except crypto
    methods which are deposit-only by design (handled client-side)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_payment_methods_table(conn)
        rows = await conn.fetch("""
          SELECT id, label, icon, color, availability_text,
                 restricted_countries, enabled, display_order
          FROM payment_methods
          WHERE enabled = TRUE
          ORDER BY display_order, id
        """)
    out = []
    for r in rows:
        countries = (r["restricted_countries"] or "").upper()
        out.append({
            "id": r["id"], "label": r["label"], "icon": r["icon"],
            "color": r["color"], "availability": r["availability_text"],
            "restricted_countries": countries,
            "is_global": countries == "ALL",
            "display_order": r["display_order"],
        })
    return {"methods": out}


# ---------------------------------------------------------------------------
# P1 — Per-user eligibility check
# ---------------------------------------------------------------------------
async def _wave_allowed_countries(conn) -> list[str]:
    import json
    raw = await get_setting(conn, WAVE_KEYS["allowed_countries"],
                            json.dumps(WAVE_ALLOWED_COUNTRIES_DEFAULT))
    try:
        out = json.loads(raw)
        return [c.upper() for c in out if isinstance(c, str)]
    except Exception:
        return list(WAVE_ALLOWED_COUNTRIES_DEFAULT)


_SUGGESTIONS = {
    "orange_money_cm_dep":  ("Utilise USDT (TRC20/BSC) ou Carte bancaire "
                              "pour déposer depuis ton pays."),
    "orange_money_cm_with": ("Utilise USDT pour retirer depuis ton pays. "
                              "Le retrait OM est réservé au Cameroun (+237)."),
    "wave_dep":             ("Utilise USDT ou Orange Money (CM) pour déposer "
                              "depuis ton pays."),
    "wave_with":            ("Utilise USDT pour retirer depuis ton pays. "
                              "Wave est réservé à BF/CI/ML/NE/SN/GM/UG."),
    "global":               "Cette méthode est disponible dans tous les pays.",
}


@router.get("/payment-methods/eligibility")
async def check_eligibility(request: Request,
                             method: str = Query(..., min_length=2, max_length=64),
                             flow: str = Query("deposit", regex="^(deposit|withdraw)$")):
    """Returns {eligible, user_country, method, flow, suggestion}.

    Frontend calls this from the "Vérifier mon éligibilité" button to give the
    user a yes/no answer in one click without engaging the form.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        country = await get_user_country(conn, user["user_id"])

    method = method.strip().lower()
    eligible = False
    suggestion = ""

    if method == "orange_money_cm":
        if flow == "deposit":
            eligible = bool(country) and country not in OM_DEPOSIT_BLOCKED_COUNTRIES
            suggestion = "" if eligible else _SUGGESTIONS["orange_money_cm_dep"]
        else:  # withdraw
            # Phone number presence is checked at submit time — for the
            # eligibility hint we only check country.
            eligible = country in OM_ALLOWED_COUNTRIES
            suggestion = "" if eligible else _SUGGESTIONS["orange_money_cm_with"]
    elif method == "wave":
        async with pool.acquire() as conn:
            allowed = await _wave_allowed_countries(conn)
        eligible = country in allowed
        suggestion = "" if eligible else (_SUGGESTIONS["wave_dep"] if flow == "deposit"
                                           else _SUGGESTIONS["wave_with"])
    elif method in ("hubtel_card", "nowpayments_usdttrc20", "nowpayments_usdtbsc"):
        eligible = True  # global methods
        suggestion = ""
    else:
        raise HTTPException(status_code=404, detail="Méthode inconnue.")

    # iter237i — Best-effort analytics event (non-blocking).
    await _track_event(user["user_id"], method, flow, "check_eligibility",
                       eligible, country)

    return {
        "method": method, "flow": flow, "eligible": eligible,
        "user_country": country or "",
        "suggestion": suggestion,
    }


# ---------------------------------------------------------------------------
# iter237i — Best-effort tracking endpoint for frontend events that can't
# be inferred server-side (form_opened, submitted-from-frontend perspective).
# ---------------------------------------------------------------------------
class _TrackIn(BaseModel):
    method: str
    flow: str = "deposit"
    action: str  # "form_opened" | "submitted"


@router.post("/payment-methods/track")
async def track_event(req: _TrackIn, request: Request):
    user = await get_current_user(request)
    if req.action not in ("form_opened", "submitted"):
        raise HTTPException(status_code=400, detail="action must be form_opened or submitted")
    pool = await get_pool()
    async with pool.acquire() as conn:
        country = await get_user_country(conn, user["user_id"])
    await _track_event(user["user_id"], req.method.strip().lower(), req.flow,
                       req.action, None, country)
    return {"ok": True}


# ---------------------------------------------------------------------------
# iter237i — Admin analytics dashboard endpoint.
# ---------------------------------------------------------------------------
@router.get("/admin/payment-methods/analytics")
async def admin_payment_method_analytics(request: Request,
                                          days: int = Query(default=14, ge=1, le=180)):
    """Returns aggregate metrics per method over the last N days:
    {
      method_id,
      checks (total check_eligibility events),
      eligible_checks,
      eligible_pct,
      form_opened, submitted
    }
    """
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_analytics_table(conn)
        rows = await conn.fetch(f"""
          SELECT method_id,
                 COUNT(*) FILTER (WHERE action='check_eligibility')                 AS checks,
                 COUNT(*) FILTER (WHERE action='check_eligibility' AND eligible=TRUE) AS eligible_checks,
                 COUNT(*) FILTER (WHERE action='form_opened')                       AS form_opened,
                 COUNT(*) FILTER (WHERE action='submitted')                         AS submitted
          FROM payment_method_analytics
          WHERE created_at > NOW() - INTERVAL '{int(days)} days'
          GROUP BY method_id
          ORDER BY checks DESC
        """)
    out = []
    for r in rows:
        d = dict(r)
        c = d["checks"] or 0
        d["eligible_pct"] = round((d["eligible_checks"] / c) * 100, 1) if c else 0.0
        out.append(d)
    return {"days": days, "rows": out}


payment_methods_router = router


# ---------------------------------------------------------------------------
# iter237i — P1 : Admin toggle enabled à chaud (no redeploy required).
# ---------------------------------------------------------------------------


class _ToggleIn(BaseModel):
    enabled: bool


@router.patch("/admin/payment-methods/{method_id}")
async def admin_toggle_payment_method(method_id: str, body: _ToggleIn, request: Request):
    """Activer/désactiver une méthode sans redéploiement.

    Usage : PATCH /api/admin/payment-methods/orange_money_cm  body={"enabled": false}
    """
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_payment_methods_table(conn)
        res = await conn.execute(
            "UPDATE payment_methods SET enabled = $1 WHERE id = $2",
            body.enabled, method_id,
        )
    if res.endswith(" 0"):
        raise HTTPException(status_code=404, detail="Méthode introuvable.")
    logger.info("[iter237i] admin toggled %s → enabled=%s", method_id, body.enabled)
    return {"success": True, "method_id": method_id, "enabled": body.enabled}


@router.get("/admin/payment-methods")
async def admin_list_payment_methods(request: Request):
    """Liste complète (incluant les désactivés) pour le panel admin."""
    await require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_payment_methods_table(conn)
        rows = await conn.fetch("""
            SELECT id, label, icon, color, availability_text,
                   restricted_countries, enabled, display_order
            FROM payment_methods
            ORDER BY display_order, id
        """)
    return {"methods": [dict(r) for r in rows]}
