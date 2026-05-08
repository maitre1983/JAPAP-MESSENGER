"""
JAPAP PRO — Subscriptions, trials, duration discounts
======================================================
Plans: starter ($5), creator ($10), business ($30) — all in USD, 30d base.

Payment = wallet only (Phase 1). Wallet balance may be in any stored currency
(`wallets.currency`); the USD price is converted to that currency using the
live rate from `currency_rates`. Subscription stores the original/paid USD
amount, discount_pct and duration so the admin dashboard can produce accurate
revenue & conversion stats.

Duration multipliers (all admin-tunable):
  1 month  → 30 days, 0% discount
  3 months → 90 days, pro_discount_3m_pct (default 5%)
  12 months → 365 days, pro_discount_12m_pct (default 25%)
"""
import uuid
import logging
import json as _json
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional, Literal
from database import get_pool
from routes.auth import get_current_user
from services.settings_service import get_bool, get_int, get_float, get_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pro", tags=["pro"])

DURATION_MAP = {
    "1m": 30,
    "3m": 90,
    "12m": 365,
}


class SubscribeRequest(BaseModel):
    plan_id: str
    duration: Literal["1m", "3m", "12m"] = "1m"
    use_trial: bool = False


class CancelRequest(BaseModel):
    immediate: bool = False  # True = end now, False = cancel at period end


# -------- Helpers --------
async def _plan_row(conn, plan_id: str):
    return await conn.fetchrow(
        "SELECT * FROM pro_plans WHERE plan_id = $1 AND is_active = TRUE",
        plan_id
    )


async def _user_active_sub(conn, user_id: str):
    return await conn.fetchrow("""
        SELECT * FROM subscriptions
        WHERE user_id = $1 AND status = 'active' AND expires_at > NOW()
        ORDER BY expires_at DESC LIMIT 1
    """, user_id)


async def _has_used_trial(conn, user_id: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM subscriptions WHERE user_id = $1 AND is_trial = TRUE LIMIT 1",
        user_id
    )
    return bool(row)


async def _trial_allowed_for(conn, user_id: str, plan_id: str) -> tuple[bool, str, int]:
    """Return (allowed, reason, duration_days)."""
    if not await get_bool("pro_enabled", True):
        return False, "Module Pro désactivé", 0
    if not await get_bool("pro_trial_enabled", True):
        return False, "Essais gratuits désactivés", 0
    if await _has_used_trial(conn, user_id):
        return False, "Vous avez déjà utilisé votre essai gratuit", 0
    plans_setting = (await get_setting("pro_trial_plans") or "all").strip().lower()
    if plans_setting != "all":
        allowed_plans = {p.strip() for p in plans_setting.split(",") if p.strip()}
        if plan_id not in allowed_plans:
            return False, "Ce plan n'est pas éligible à l'essai gratuit", 0
    # Also honor pro_plans.trial_eligible flag
    plan = await _plan_row(conn, plan_id)
    if plan and not plan['trial_eligible']:
        return False, "Ce plan n'autorise pas l'essai gratuit", 0
    days = await get_int("pro_trial_days", 30)
    return True, "", max(1, days)


async def _discount_for(duration: str) -> int:
    if duration == "3m":
        return max(0, min(100, await get_int("pro_discount_3m_pct", 5)))
    if duration == "12m":
        return max(0, min(100, await get_int("pro_discount_12m_pct", 25)))
    return 0


def _price_quote(monthly_usd: Decimal, duration: str, discount_pct: int) -> dict:
    months = {"1m": 1, "3m": 3, "12m": 12}[duration]
    subtotal = monthly_usd * Decimal(months)
    discount = (subtotal * Decimal(discount_pct) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (subtotal - discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "months": months,
        "monthly_usd": str(monthly_usd),
        "subtotal_usd": str(subtotal.quantize(Decimal('0.01'))),
        "discount_pct": discount_pct,
        "discount_usd": str(discount),
        "total_usd": str(total),
    }


async def _duration_enabled(duration: str) -> bool:
    return await get_bool(f"pro_duration_{duration}_enabled", True)


# -------- Seed 3 default plans --------
DEFAULT_PLANS = [
    {
        "plan_id": "starter", "name": "Starter Pro", "tagline": "Démarrez en Pro",
        "price_usd": "5.00", "sort_order": 1,
        "features": [
            "Badge Pro sur votre profil",
            "Boost léger du feed",
            "0% de commission sur les tips reçus",
            "Statistiques basiques",
            "1 reel boosté / semaine",
        ],
        "limits": {"boosted_reels_per_week": 1, "feed_boost": 1.2, "tip_commission_pct": 0},
    },
    {
        "plan_id": "creator", "name": "Creator Pro", "tagline": "Pour les créateurs ambitieux",
        "price_usd": "10.00", "sort_order": 2,
        "features": [
            "Boost fort feed + reels",
            "Priorité de distribution des reels",
            "Statistiques avancées",
            "Monétisation avancée",
            "3 reels boostés / semaine",
            "Accès crowdfunding premium",
            "Lien externe sur votre profil",
        ],
        "limits": {"boosted_reels_per_week": 3, "feed_boost": 1.6, "reels_priority": True,
                   "crowdfunding_premium": True, "external_link": True, "tip_commission_pct": 0},
    },
    {
        "plan_id": "business", "name": "Business Pro", "tagline": "Visibilité maximale",
        "price_usd": "30.00", "sort_order": 3,
        "features": [
            "Visibilité maximale (feed + reels + marketplace)",
            "Priorité produits marketplace",
            "Commissions réduites sur les ventes",
            "Campagnes sponsorisées",
            "Analytics avancés",
            "Support prioritaire",
            "Badge vérifié",
        ],
        "limits": {"feed_boost": 2.0, "marketplace_priority": True,
                   "marketplace_commission_pct": 2, "sponsored_campaigns": True,
                   "analytics_advanced": True, "priority_support": True, "verified_badge": True,
                   "tip_commission_pct": 0},
    },
]


async def seed_plans():
    """Idempotent: insert defaults if the plan_id is missing. Never overwrites
    existing rows — admins may have edited prices/features already."""
    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        for p in DEFAULT_PLANS:
            existing = await conn.fetchrow("SELECT id FROM pro_plans WHERE plan_id = $1", p["plan_id"])
            if existing:
                continue
            await conn.execute("""
                INSERT INTO pro_plans (plan_id, name, tagline, price_usd, duration_days,
                                        features, limits, sort_order, is_active)
                VALUES ($1, $2, $3, $4, 30, $5::jsonb, $6::jsonb, $7, TRUE)
            """, p["plan_id"], p["name"], p["tagline"], Decimal(p["price_usd"]),
               json.dumps(p["features"]), json.dumps(p["limits"]), p["sort_order"])
            logger.info(f"Seeded pro plan: {p['plan_id']}")


# -------- Endpoints --------
def _parse_jsonb(v):
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return v
    try:
        return _json.loads(v)
    except Exception:
        return v


@router.get("/social-proof")
async def social_proof(request: Request):
    """Public social-proof widget data used by ProPage to highlight JAPAP Connect
    revshare payouts. Reads `referral_rewards_log WHERE role='connect_revshare'`
    for the last 30 days. The pct is read live from admin settings and can be
    changed at any time — the UI reflects it without redeploy.
    """
    await get_current_user(request)
    enabled = await get_bool("connect_revshare_pro_enabled", True)
    pct = await get_float("connect_revshare_pct", 2.0)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                COUNT(DISTINCT user_id) AS owner_count_30d,
                COALESCE(SUM(amount_usd), 0) AS total_usd_30d,
                COUNT(*) AS credits_30d,
                MAX(created_at) AS last_at
            FROM referral_rewards_log
            WHERE role = 'connect_revshare'
              AND created_at > NOW() - INTERVAL '30 days'
        """)
        all_time = await conn.fetchrow("""
            SELECT COUNT(DISTINCT user_id) AS owner_count_all,
                   COALESCE(SUM(amount_usd), 0) AS total_usd_all
            FROM referral_rewards_log
            WHERE role = 'connect_revshare'
        """)
    return {
        "enabled": bool(enabled),
        "pct": float(pct or 0),
        "last_30d": {
            "owner_count": int(row['owner_count_30d'] or 0),
            "total_usd": str(row['total_usd_30d'] or 0),
            "credits": int(row['credits_30d'] or 0),
            "last_at": row['last_at'].isoformat() if row['last_at'] else None,
        },
        "all_time": {
            "owner_count": int(all_time['owner_count_all'] or 0),
            "total_usd": str(all_time['total_usd_all'] or 0),
        },
    }


@router.get("/top-hosts")
async def top_hosts(request: Request, limit: int = Query(3, ge=1, le=10)):
    """Top N hotspot owners by revshare received in last 30 days. Public (auth required)
    data powering social-proof widgets. If fewer than 3 real hosts AND
    `top_hosts_seed_enabled`, fills with plausible ambassador placeholders so
    the widget is never empty on launch day. Removed automatically as soon as
    3 real hosts exist."""
    await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_pro, u.country,
                   SUM(l.amount_usd) AS earned_usd, COUNT(*) AS credits
            FROM referral_rewards_log l
            JOIN users u ON u.user_id = l.user_id
            WHERE l.role = 'connect_revshare'
              AND l.created_at > NOW() - INTERVAL '30 days'
            GROUP BY u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.is_pro, u.country
            ORDER BY earned_usd DESC
            LIMIT $1
        """, limit)
    result = [{
        "rank": i + 1,
        "user_id": r['user_id'],
        "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r['username'],
        "avatar": r['avatar'] or '',
        "is_pro": bool(r['is_pro']),
        "country": r['country'] or '',
        "earned_usd": str(r['earned_usd'] or 0),
        "credits": int(r['credits'] or 0),
        "seeded": False,
    } for i, r in enumerate(rows)]

    seed_enabled = await get_bool("top_hosts_seed_enabled", False)
    if seed_enabled and len(result) < 3:
        ambassadors = [
            {"name": "Kwame Mensah",     "country": "GH", "earned_usd": "24.50", "credits": 12},
            {"name": "Aisha Diallo",     "country": "SN", "earned_usd": "18.30", "credits": 9},
            {"name": "Jean-Paul Mbarga", "country": "CM", "earned_usd": "15.20", "credits": 7},
            {"name": "Chioma Okonkwo",   "country": "NG", "earned_usd": "12.80", "credits": 6},
            {"name": "Yassine Fofana",   "country": "CI", "earned_usd": "10.10", "credits": 5},
        ]
        existing_names = {r['name'] for r in result}
        for a in ambassadors:
            if a['name'] in existing_names:
                continue
            result.append({
                "rank": len(result) + 1, "user_id": f"seed_{len(result)}",
                "name": a['name'], "avatar": "", "is_pro": True, "country": a['country'],
                "earned_usd": a['earned_usd'], "credits": a['credits'], "seeded": True,
            })
            if len(result) >= limit:
                break
    return result




@router.get("/plans")
async def list_plans(request: Request):
    user = await get_current_user(request)
    pro_enabled = await get_bool("pro_enabled", True)
    discount_3m = await get_int("pro_discount_3m_pct", 5)
    discount_12m = await get_int("pro_discount_12m_pct", 25)
    # A/B test — variant B applies a global -20% on monthly price
    ab_variant = (await get_setting("pro_ab_variant") or "A").upper()
    ab_discount = Decimal("0.80") if ab_variant == "B" else Decimal("1.00")
    # Urgency banner config (returned so the frontend can display a sticky CTA)
    urgency_enabled = await get_bool("pro_urgency_enabled", False)
    urgency_title = await get_setting("pro_urgency_title") or ""
    urgency_subtitle = await get_setting("pro_urgency_subtitle") or ""
    urgency_ends_at = await get_setting("pro_urgency_ends_at") or ""
    urgency_discount = await get_int("pro_urgency_discount_pct", 0)
    durations_enabled = {
        "1m": await _duration_enabled("1m"),
        "3m": await _duration_enabled("3m"),
        "12m": await _duration_enabled("12m"),
    }

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM pro_plans WHERE is_active = TRUE ORDER BY sort_order ASC, price_usd ASC"
        )
        has_trial = not (await _has_used_trial(conn, user['user_id']))
        trial_enabled = await get_bool("pro_trial_enabled", True)
        trial_days = await get_int("pro_trial_days", 30)
        plans_setting = (await get_setting("pro_trial_plans") or "all").strip().lower()

        # FOMO: "Prix bloqué à vie pour les N premiers"
        first_1000_enabled = await get_bool("pro_first_1000_enabled", False)
        first_1000_cap = await get_int("pro_first_1000_cap", 1000)
        first_1000_claimed = 0
        first_1000_remaining = first_1000_cap
        if first_1000_enabled:
            first_1000_claimed = await conn.fetchval(
                "SELECT COUNT(*) FROM subscriptions WHERE status IN ('active','expired')"
            ) or 0
            first_1000_remaining = max(0, first_1000_cap - int(first_1000_claimed))

    plans = []
    for r in rows:
        trial_eligible = bool(r['trial_eligible'])
        if plans_setting != "all":
            allowed = {x.strip() for x in plans_setting.split(",") if x.strip()}
            if r['plan_id'] not in allowed:
                trial_eligible = False
        monthly = (Decimal(str(r['price_usd'])) * ab_discount).quantize(Decimal("0.01"))
        original_monthly = Decimal(str(r['price_usd']))
        plans.append({
            "plan_id": r['plan_id'],
            "name": r['name'],
            "tagline": r['tagline'] or "",
            "price_usd": str(monthly),
            "original_price_usd": str(original_monthly),
            "ab_variant": ab_variant,
            "ab_discount_pct": 20 if ab_variant == "B" else 0,
            "duration_days": r['duration_days'],
            "features": _parse_jsonb(r['features']),
            "limits": _parse_jsonb(r['limits']),
            "trial_eligible": trial_eligible,
            "sort_order": r['sort_order'],
            "quotes": {
                "1m": _price_quote(monthly, "1m", 0),
                "3m": _price_quote(monthly, "3m", discount_3m),
                "12m": _price_quote(monthly, "12m", discount_12m),
            },
        })

    # FOMO first-1000 urgency TAKES PRIORITY over a normal urgency when active
    if first_1000_enabled and first_1000_remaining > 0:
        urgency_enabled = True
        urgency_title = await get_setting("pro_first_1000_title") or "🔒 Prix bloqué à vie — pour les 1000 premiers"
        urgency_subtitle = (await get_setting("pro_first_1000_subtitle") or "") + \
            f" — Plus que {first_1000_remaining} places sur {first_1000_cap}."
        urgency_ends_at = ""

    return {
        "pro_enabled": pro_enabled,
        "trial": {
            "enabled": trial_enabled and has_trial,
            "days": trial_days,
            "already_used": not has_trial,
            "applies_to": plans_setting,
        },
        "durations_enabled": durations_enabled,
        "discounts": {"m3": discount_3m, "m12": discount_12m},
        "ab_variant": ab_variant,
        "urgency": {
            "enabled": bool(urgency_enabled),
            "title": urgency_title,
            "subtitle": urgency_subtitle,
            "ends_at": urgency_ends_at,
            "discount_pct": int(urgency_discount or 0),
        },
        "first_1000": {
            "enabled": first_1000_enabled,
            "cap": first_1000_cap,
            "claimed": int(first_1000_claimed),
            "remaining": first_1000_remaining,
            "progress_pct": round((int(first_1000_claimed) / first_1000_cap) * 100, 1) if first_1000_cap > 0 else 0,
        },
        "plans": plans,
    }


@router.get("/upsell-config")
async def upsell_config(request: Request):
    """Returns the smart-upsell config for the frontend. The FE keeps per-user
    block counters in localStorage and triggers the upsell modal when the
    threshold is reached (low-friction, no DB write per block).
    When `pro_launch_mode_enabled` is on AND the user is younger than
    `pro_launch_new_user_days` days, the threshold is lowered to the more
    aggressive `pro_launch_threshold` value."""
    user = await get_current_user(request)
    default_threshold = await get_int("pro_smart_upsell_threshold", 3)
    launch_mode = await get_bool("pro_launch_mode_enabled", False)
    launch_threshold = await get_int("pro_launch_threshold", 2)
    new_user_days = await get_int("pro_launch_new_user_days", 7)

    threshold = default_threshold
    is_new_user = False
    if launch_mode:
        pool = await get_pool()
        async with pool.acquire() as conn:
            created = await conn.fetchval(
                "SELECT created_at FROM users WHERE user_id = $1", user['user_id'])
        if created:
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=new_user_days)
            if created > cutoff:
                threshold = launch_threshold
                is_new_user = True
    return {
        "enabled": await get_bool("pro_smart_upsell_enabled", True),
        "threshold": threshold,
        "ab_variant": (await get_setting("pro_ab_variant") or "A").upper(),
        "launch_mode": bool(launch_mode),
        "is_new_user": is_new_user,
    }


@router.get("/status")
async def get_pro_status(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sub = await _user_active_sub(conn, user['user_id'])
        if not sub:
            # Keep users.is_pro flag in sync (expired)
            if user.get('is_pro'):
                await conn.execute(
                    "UPDATE users SET is_pro = FALSE, pro_type = 0, pro_expires_at = NULL WHERE user_id = $1",
                    user['user_id'])
            return {"is_pro": False}
        plan = await conn.fetchrow(
            "SELECT plan_id, name, features, limits FROM pro_plans WHERE plan_id = $1",
            sub['plan_type']
        )
        remaining = sub['expires_at'] - datetime.now(timezone.utc)
    return {
        "is_pro": True,
        "plan_id": sub['plan_type'],
        "plan_name": plan['name'] if plan else sub['plan_type'],
        "features": _parse_jsonb(plan['features']) if plan else [],
        "limits": _parse_jsonb(plan['limits']) if plan else {},
        "is_trial": bool(sub['is_trial']),
        "source": sub['source'] or "wallet",
        "starts_at": sub['starts_at'].isoformat(),
        "expires_at": sub['expires_at'].isoformat(),
        "days_remaining": max(0, remaining.days),
        "cancel_at_period_end": bool(sub['cancel_at_period_end']),
    }


@router.post("/subscribe")
async def subscribe(req: SubscribeRequest, request: Request):
    user = await get_current_user(request)
    if not await get_bool("pro_enabled", True):
        raise HTTPException(status_code=503, detail="Le module Pro est désactivé.")

    if req.duration not in DURATION_MAP:
        raise HTTPException(status_code=400, detail="Durée invalide")
    if not await _duration_enabled(req.duration):
        raise HTTPException(status_code=400, detail=f"La durée {req.duration} est désactivée")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            plan = await _plan_row(conn, req.plan_id)
            if not plan:
                raise HTTPException(status_code=404, detail="Plan introuvable")
            active = await _user_active_sub(conn, user['user_id'])
            if active:
                raise HTTPException(status_code=400, detail="Vous avez déjà un abonnement actif. Annulez avant d'en souscrire un nouveau.")

            # Apply A/B variant discount dynamically at checkout (iter31)
            ab_variant = (await get_setting("pro_ab_variant") or "A").upper()
            ab_multiplier = Decimal("0.80") if ab_variant == "B" else Decimal("1.00")
            monthly = (Decimal(str(plan['price_usd'])) * ab_multiplier).quantize(Decimal("0.01"))
            tx_id = f"pro_{uuid.uuid4().hex[:12]}"
            discount_pct = 0
            subtotal_usd = monthly * Decimal(DURATION_MAP[req.duration] // 30)
            paid_usd = subtotal_usd
            duration_days = plan['duration_days'] if req.duration == "1m" else DURATION_MAP[req.duration]

            # ---- TRIAL ----
            if req.use_trial:
                ok, reason, days = await _trial_allowed_for(conn, user['user_id'], req.plan_id)
                if not ok:
                    raise HTTPException(status_code=400, detail=reason)
                duration_days = days
                paid_usd = Decimal("0")
                subtotal_usd = Decimal("0")
                is_trial = True
                source = "trial"
            else:
                # ---- PAID ----
                is_trial = False
                source = "wallet"
                discount_pct = await _discount_for(req.duration)
                paid_usd = (subtotal_usd * (Decimal(100) - Decimal(discount_pct)) / Decimal(100)
                           ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Convert USD -> user's wallet currency for debit
                wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
                if not wallet:
                    raise HTTPException(status_code=404, detail="Wallet introuvable")
                if wallet['is_locked']:
                    raise HTTPException(status_code=403, detail="Wallet verrouillé")

                wallet_ccy = (wallet['currency'] or 'USD').upper()
                rate_row = await conn.fetchrow(
                    "SELECT rate_vs_usd FROM currency_rates WHERE code = $1", wallet_ccy)
                rate = Decimal(str(rate_row['rate_vs_usd'])) if rate_row else Decimal("1")
                amount_in_wallet_ccy = (paid_usd * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                if wallet['balance'] < amount_in_wallet_ccy:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Solde insuffisant ({amount_in_wallet_ccy} {wallet_ccy} requis)."
                    )

                await conn.execute(
                    "UPDATE wallets SET balance = balance - $1, updated_at = NOW() WHERE user_id = $2",
                    amount_in_wallet_ccy, user['user_id'])
                await conn.execute("""
                    INSERT INTO transactions (tx_id, from_user_id, type, amount, currency, status, notes)
                    VALUES ($1, $2, 'subscription', $3, $4, 'completed', $5)
                """, tx_id, user['user_id'], amount_in_wallet_ccy, wallet_ccy,
                   f"Abonnement {plan['name']} ({req.duration}, ${paid_usd})")

            # ---- Create subscription ----
            now = datetime.now(timezone.utc)
            expires = now + timedelta(days=duration_days)
            await conn.execute("""
                INSERT INTO subscriptions
                    (user_id, plan_type, price, currency, status, starts_at, expires_at,
                     is_trial, source, original_amount_usd, paid_amount_usd, discount_pct, duration_days)
                VALUES ($1, $2, $3, 'USD', 'active', $4, $5, $6, $7, $8, $9, $10, $11)
            """, user['user_id'], req.plan_id, paid_usd, now, expires,
               is_trial, source, subtotal_usd, paid_usd, discount_pct, duration_days)

            await conn.execute("""
                UPDATE users SET is_pro = TRUE, pro_type = $1, pro_expires_at = $2, updated_at = NOW() WHERE user_id = $3
            """, plan['id'], expires, user['user_id'])

            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'pro_activated', 'Abonnement Pro activé', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", user['user_id'],
               f"{'Essai gratuit' if is_trial else 'Abonnement'} {plan['name']} actif jusqu'au {expires.strftime('%d/%m/%Y')}")

            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'pro_subscribe', 'subscriptions', $2)
            """, user['user_id'],
               f'{{"plan":"{req.plan_id}","duration":"{req.duration}","is_trial":{str(is_trial).lower()},"paid_usd":"{paid_usd}","discount_pct":{discount_pct}}}')

    # Revenue share 2% to the hotspot owner who most recently brought this
    # user through the JAPAP Connect captive portal (admin-configurable).
    revshare_info = {}
    if not is_trial and paid_usd > 0:
        try:
            from routes.connect import credit_hotspot_owner_from_pro
            revshare_info = await credit_hotspot_owner_from_pro(
                user['user_id'], paid_usd, plan['name'], "pro_subscribe") or {}
        except Exception as e:
            logger.warning(f"Connect revshare skipped: {e}")

    return {
        "status": "activated",
        "plan": plan['name'],
        "plan_id": plan['plan_id'],
        "is_trial": is_trial,
        "duration": req.duration,
        "duration_days": duration_days,
        "discount_pct": discount_pct,
        "paid_usd": str(paid_usd),
        "original_usd": str(subtotal_usd),
        "expires_at": expires.isoformat(),
        "connect_revshare": revshare_info,
    }


@router.post("/cancel")
async def cancel(req: CancelRequest, request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sub = await _user_active_sub(conn, user['user_id'])
        if not sub:
            raise HTTPException(status_code=404, detail="Aucun abonnement actif")
        if req.immediate:
            await conn.execute("""
                UPDATE subscriptions SET status = 'cancelled', cancelled_at = NOW(),
                                         expires_at = NOW()
                WHERE id = $1
            """, sub['id'])
            await conn.execute("""
                UPDATE users SET is_pro = FALSE, pro_type = 0, pro_expires_at = NULL WHERE user_id = $1
            """, user['user_id'])
            msg = "Abonnement annulé immédiatement."
        else:
            await conn.execute(
                "UPDATE subscriptions SET cancel_at_period_end = TRUE WHERE id = $1", sub['id'])
            msg = f"L'abonnement ne sera pas renouvelé. Accès Pro jusqu'au {sub['expires_at'].strftime('%d/%m/%Y')}."
        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details)
            VALUES ($1, 'pro_cancel', 'subscriptions', $2)
        """, user['user_id'], f'{{"immediate": {str(req.immediate).lower()}}}')
    return {"status": "ok", "message": msg}
