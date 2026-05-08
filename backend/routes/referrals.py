"""
JAPAP — Advanced Referral Program
==================================
Every parameter is admin-controllable through the `admin_settings` key/value
store; no literals in business logic. Flow:

1. User A shares code → User B signs up & applies code (`POST /apply`).
2. Referral is stored `pending`. IP + device are recorded for anti-fraud.
3. When User B meets activation conditions (OTP + qualifying action),
   `check_and_activate_referral()` fires the bonus:
     - credits referrer_bonus_usd + referee_bonus_usd (converted to each
       user's wallet currency) to their wallets
     - marks status='active' and logs to referral_rewards_log
4. Tier thresholds (e.g. 3/10/25 active referrals) unlock extra rewards
   (Pro days or wallet USD) claimable via `POST /claim`.

Anti-fraud: referrals per IP or device per day are capped; exceeding rows
are marked blocked=TRUE and never activate rewards.
"""
import os
import uuid
import secrets
import string
import json
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user
from services.settings_service import get_bool, get_int, get_float, get_setting, get_json


def _public_base_url(request: Optional[Request]) -> str:
    """Resolve the public-facing app base URL with the same priority ladder
    used elsewhere in the codebase:
      1. PUBLIC_APP_URL env (set in prod)
      2. Origin / Referer header of the caller (correct on preview)
      3. request.url.scheme + netloc (k8s ingress maps SPA + API at the same host)
      4. Hard fallback https://japapmessenger.com
    """
    base = (os.environ.get("PUBLIC_APP_URL") or "").strip()
    if base:
        return base.rstrip("/")
    if request is not None:
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        if origin:
            try:
                p = urlparse(origin)
                if p.scheme and p.netloc:
                    return f"{p.scheme}://{p.netloc}"
            except Exception:
                pass
        try:
            if request.url.netloc:
                return f"{request.url.scheme}://{request.url.netloc}"
        except Exception:
            pass
    return "https://japapmessenger.com"

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/referrals", tags=["referrals"])


# ---------- Helpers ----------
def generate_referral_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def ensure_referrals_utm_columns(conn) -> None:
    """Idempotent migration to add UTM tracking columns to the referrals
    table. Called from `apply_referral`, `register` and the email
    bootstrap so that we never have to maintain a separate migration step.
    """
    await conn.execute("""
        ALTER TABLE referrals
            ADD COLUMN IF NOT EXISTS utm_source   VARCHAR(40),
            ADD COLUMN IF NOT EXISTS utm_medium   VARCHAR(40),
            ADD COLUMN IF NOT EXISTS utm_campaign VARCHAR(80)
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_referrals_utm_source "
        "ON referrals (utm_source)"
    )


async def ensure_referral_code(conn, user_id: str) -> str:
    row = await conn.fetchrow("SELECT referral_code FROM users WHERE user_id = $1", user_id)
    if row and row['referral_code']:
        return row['referral_code']
    for _ in range(10):
        code = generate_referral_code()
        exists = await conn.fetchval("SELECT 1 FROM users WHERE referral_code = $1", code)
        if not exists:
            await conn.execute("UPDATE users SET referral_code = $1 WHERE user_id = $2", code, user_id)
            return code
    raise HTTPException(status_code=500, detail="Unable to generate unique referral code")


async def _get_rate_to_wallet_ccy(conn, wallet_ccy: str) -> Decimal:
    row = await conn.fetchrow(
        "SELECT rate_vs_usd FROM currency_rates WHERE code = $1", wallet_ccy.upper())
    if row and row['rate_vs_usd']:
        return Decimal(str(row['rate_vs_usd']))
    return Decimal("1")


async def _credit_wallet_bonus(conn, user_id: str, amount_usd: Decimal, reason: str,
                                role: str, referral_id: Optional[int] = None) -> dict:
    """Credit a wallet with `amount_usd` converted to the wallet's currency.
    Returns {currency, amount_local, tx_id}."""
    if amount_usd <= 0:
        return {"currency": "USD", "amount_local": "0", "tx_id": None}

    wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user_id)
    if not wallet:
        # Create wallet on the fly in USD if missing
        await conn.execute("""
            INSERT INTO wallets (user_id, balance, currency)
            VALUES ($1, 0, 'USD') ON CONFLICT (user_id) DO NOTHING
        """, user_id)
        wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user_id)
    wallet_ccy = (wallet['currency'] or 'USD').upper()
    rate = await _get_rate_to_wallet_ccy(conn, wallet_ccy)
    local = (amount_usd * rate).quantize(Decimal("0.01"))

    await conn.execute(
        "UPDATE wallets SET balance = balance + $1, updated_at = NOW() WHERE user_id = $2",
        local, user_id
    )
    tx_id = f"ref_{uuid.uuid4().hex[:12]}"
    await conn.execute("""
        INSERT INTO transactions (tx_id, to_user_id, type, amount, currency, status, notes)
        VALUES ($1, $2, 'referral_bonus', $3, $4, 'completed', $5)
    """, tx_id, user_id, local, wallet_ccy, reason)

    await conn.execute("""
        INSERT INTO referral_rewards_log
            (referral_id, user_id, role, reward_type, amount_usd, amount_local, currency, details)
        VALUES ($1, $2, $3, 'wallet', $4, $5, $6, $7::jsonb)
    """, referral_id, user_id, role, amount_usd, local, wallet_ccy,
       json.dumps({"reason": reason, "tx_id": tx_id}))

    return {"currency": wallet_ccy, "amount_local": str(local), "tx_id": tx_id}


async def _over_daily_cap(conn, ip: Optional[str], device: Optional[str]) -> tuple[bool, str]:
    """Check admin IP/device caps for today."""
    cap_ip = await get_int("referral_max_per_ip_per_day", 3)
    cap_dv = await get_int("referral_max_per_device_per_day", 3)
    today = datetime.now(timezone.utc).date()
    if ip and cap_ip > 0:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE ip_address = $1 AND created_at::date = $2",
            ip, today)
        if (n or 0) >= cap_ip:
            return True, "ip_limit_reached"
    if device and cap_dv > 0:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE device_id = $1 AND created_at::date = $2",
            device, today)
        if (n or 0) >= cap_dv:
            return True, "device_limit_reached"
    return False, ""


async def _default_tiers() -> list[dict]:
    tiers = await get_json("referral_tiers_json", [])
    if isinstance(tiers, list) and tiers:
        return tiers
    return [
        {"count": 3, "reward_type": "pro", "reward_value": 30, "label": "1 mois Pro"},
        {"count": 10, "reward_type": "pro", "reward_value": 90, "label": "3 mois Pro"},
    ]


async def get_active_boost() -> dict:
    """Return the currently-active boost event or {active: False}.

    A boost is active when `boost_enabled=true` AND the current UTC time falls
    within [boost_start_at, boost_end_at]. Empty dates mean "no limit" on
    that side. Invalid ISO strings are treated as absent.
    """
    if not await get_bool("boost_enabled", False):
        return {"active": False}
    from datetime import datetime as _dt
    now = datetime.now(timezone.utc)
    start_raw = (await get_setting("boost_start_at") or "").strip()
    end_raw = (await get_setting("boost_end_at") or "").strip()

    def _parse(s):
        if not s:
            return None
        try:
            return _dt.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    start = _parse(start_raw)
    end = _parse(end_raw)
    if start and now < start:
        return {"active": False, "starts_at": start.isoformat()}
    if end and now > end:
        return {"active": False, "ended_at": end.isoformat()}
    multiplier = await get_float("boost_multiplier", 1.0) or 1.0
    return {
        "active": True,
        "name": await get_setting("boost_name") or "Referral Boost",
        "multiplier": multiplier,
        "starts_at": start.isoformat() if start else None,
        "ends_at": end.isoformat() if end else None,
        "applies_to_referrer": await get_bool("boost_applies_to_referrer", True),
        "applies_to_referee": await get_bool("boost_applies_to_referee", True),
        "applies_to_tiers": await get_bool("boost_applies_to_tiers", False),
    }


async def _notify_invited_email(conn, *, referrer_id: str, referred_user: dict) -> None:
    """Send the 'filleul inscrit' email — fetches names + email of referrer.
    Best-effort, no throw."""
    try:
        from services.referral_emails import send_invited
        ref_user = await conn.fetchrow(
            "SELECT email, first_name FROM users WHERE user_id = $1", referrer_id,
        )
        if not ref_user or not ref_user["email"]:
            return
        await send_invited(
            conn,
            referrer_email=ref_user["email"],
            referrer_id=referrer_id,
            referrer_first_name=(ref_user["first_name"] or "").strip(),
            referred_first_name=(referred_user.get("first_name") or "").strip(),
            referred_id=referred_user.get("user_id"),
        )
    except Exception as e:
        logger.warning(f"referral.invited email failed: {e}")


async def _notify_activated_email(conn, *, referrer_id: str, referred_id: str,
                                  bonus_local: str, bonus_currency: str) -> None:
    """Send the 'filleul activé' email."""
    try:
        from services.referral_emails import send_activated
        ref_user = await conn.fetchrow(
            "SELECT email, first_name FROM users WHERE user_id = $1", referrer_id,
        )
        ree_user = await conn.fetchrow(
            "SELECT first_name FROM users WHERE user_id = $1", referred_id,
        )
        if not ref_user or not ref_user["email"]:
            return
        await send_activated(
            conn,
            referrer_email=ref_user["email"],
            referrer_id=referrer_id,
            referrer_first_name=(ref_user["first_name"] or "").strip(),
            referred_first_name=((ree_user["first_name"] if ree_user else "") or "").strip(),
            referred_id=referred_id,
            bonus_local=bonus_local,
            bonus_currency=bonus_currency,
        )
    except Exception as e:
        logger.warning(f"referral.activated email failed: {e}")


async def _notify_rewarded_email(conn, *, referrer_id: str, tier_label: str,
                                 reward_summary: str) -> None:
    """Send the 'palier débloqué' email."""
    try:
        from services.referral_emails import send_rewarded
        ref_user = await conn.fetchrow(
            "SELECT email, first_name FROM users WHERE user_id = $1", referrer_id,
        )
        if not ref_user or not ref_user["email"]:
            return
        await send_rewarded(
            conn,
            referrer_email=ref_user["email"],
            referrer_id=referrer_id,
            referrer_first_name=(ref_user["first_name"] or "").strip(),
            tier_label=tier_label,
            reward_summary=reward_summary,
        )
    except Exception as e:
        logger.warning(f"referral.rewarded email failed: {e}")


async def check_and_activate_referral(user_id: str):
    """Fired from post/message/wallet routes on qualifying actions.
    - Respects admin activation_requires_* flags.
    - Pays both referrer and referee bonuses in USD → wallet currency.
    - Skipped if the referral is blocked (fraud) or disabled module.
    """
    if not await get_bool("referral_enabled", True):
        return
    requires_otp = await get_bool("referral_activation_requires_otp", True)
    requires_action = await get_bool("referral_activation_requires_action", True)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT r.id, r.referrer_id, r.blocked, u.email_verified
            FROM referrals r
            JOIN users u ON u.user_id = r.referred_id
            WHERE r.referred_id = $1 AND r.status = 'pending'
            LIMIT 1
        """, user_id)
        if not row or row['blocked']:
            return
        if requires_otp and not row['email_verified']:
            return
        # Qualifying action is implicit: this helper is called *from* such actions.
        if not requires_action and requires_otp:
            pass  # OTP alone suffices
        # else we assume the caller already did an action

        # Load bonuses
        referrer_bonus = Decimal(str(await get_float("referral_referrer_bonus_usd", 0.0) or 0))
        referee_bonus = Decimal(str(await get_float("referral_referee_bonus_usd", 0.0) or 0))

        # Apply active boost multiplier (if any)
        boost = await get_active_boost()
        boost_mult = Decimal("1")
        if boost.get("active"):
            mult = Decimal(str(boost.get("multiplier", 1)))
            if boost.get("applies_to_referrer"):
                referrer_bonus = (referrer_bonus * mult).quantize(Decimal("0.0001"))
            if boost.get("applies_to_referee"):
                referee_bonus = (referee_bonus * mult).quantize(Decimal("0.0001"))
            boost_mult = mult

        # Capture the local-currency bonus for the email (best effort).
        bonus_local_str = "0"
        bonus_currency = "USD"
        async with conn.transaction():
            await conn.execute("""
                UPDATE referrals SET status = 'active', activated_at = NOW(),
                                      referrer_bonus_usd = $1, referee_bonus_usd = $2
                WHERE id = $3
            """, referrer_bonus, referee_bonus, row['id'])

            if referrer_bonus > 0:
                res = await _credit_wallet_bonus(
                    conn, row['referrer_id'], referrer_bonus,
                    "Bonus parrainage (parrain)", "referrer", row['id'])
                bonus_local_str = res.get("amount_local", "0")
                bonus_currency = res.get("currency", "USD")
            if referee_bonus > 0:
                await _credit_wallet_bonus(
                    conn, user_id, referee_bonus,
                    "Bonus parrainage (filleul)", "referee", row['id'])

            # Notify referrer
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'referral_activated', 'Filleul actif !', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", row['referrer_id'],
                 f"Un filleul vient d'être activé. {f'Bonus crédité : ${referrer_bonus}' if referrer_bonus > 0 else ''}".strip())
            if referee_bonus > 0:
                await conn.execute("""
                    INSERT INTO notifications (notif_id, user_id, type, title, message)
                    VALUES ($1, $2, 'referral_bonus_received', 'Bonus reçu', $3)
                """, f"notif_{uuid.uuid4().hex[:12]}", user_id,
                   f"Bienvenue ! Vous avez reçu ${referee_bonus} de bonus pour avoir rejoint JAPAP via un parrain.")

        logger.info(f"Referral activated: {row['referrer_id']} -> {user_id}  "
                    f"(referrer_bonus={referrer_bonus} USD, referee_bonus={referee_bonus} USD)")

        # iter110 — fire the activation email to the referrer (best-effort, dedup 24h).
        try:
            await _notify_activated_email(
                conn, referrer_id=row['referrer_id'], referred_id=user_id,
                bonus_local=bonus_local_str, bonus_currency=bonus_currency,
            )
        except Exception as _e:
            logger.warning(f"referral activated email skipped: {_e}")


# ==================================================================
# PUBLIC / USER ROUTES
# ==================================================================
@router.get("/boost")
async def current_boost(request: Request):
    """Return the currently-active Referral Boost Event (if any)."""
    await get_current_user(request)
    return await get_active_boost()


@router.get("/config")
async def referral_config(request: Request):
    """Public config snapshot for the user-facing page."""
    await get_current_user(request)
    return {
        "enabled": await get_bool("referral_enabled", True),
        "referrer_bonus_usd": await get_float("referral_referrer_bonus_usd", 0.0),
        "referee_bonus_usd": await get_float("referral_referee_bonus_usd", 0.0),
        "activation_requires_otp": await get_bool("referral_activation_requires_otp", True),
        "activation_requires_action": await get_bool("referral_activation_requires_action", True),
        "tiers": await _default_tiers(),
        "leaderboard_enabled": await get_bool("referral_leaderboard_enabled", True),
        "leaderboard_window": await get_setting("referral_leaderboard_window") or "weekly",
        "gamification_enabled": await get_bool("referral_gamification_enabled", True),
        "boost": await get_active_boost(),
    }


@router.get("/me")
async def get_my_referral(request: Request):
    user = await get_current_user(request)
    tiers = await _default_tiers()
    referrer_bonus = await get_float("referral_referrer_bonus_usd", 0.0)
    referee_bonus = await get_float("referral_referee_bonus_usd", 0.0)

    pool = await get_pool()
    async with pool.acquire() as conn:
        code = await ensure_referral_code(conn, user['user_id'])
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE status = 'active') AS active,
                COUNT(*) FILTER (WHERE status = 'rewarded') AS rewarded,
                COUNT(*) AS total,
                COALESCE(SUM(referrer_bonus_usd) FILTER (WHERE status IN ('active','rewarded')), 0) AS total_earned_usd
            FROM referrals WHERE referrer_id = $1 AND (blocked = FALSE OR blocked IS NULL)
        """, user['user_id'])
        active_count = (stats['active'] or 0) + (stats['rewarded'] or 0)
    # Progress through tiers
    next_tier = None
    for t in tiers:
        if active_count < int(t['count']):
            next_tier = t
            break
    progress_pct = 0
    if next_tier:
        prev = 0
        for t in tiers:
            if t['count'] == next_tier['count']:
                break
            prev = t['count']
        span = next_tier['count'] - prev
        progress_pct = min(100, int(((active_count - prev) / span) * 100)) if span > 0 else 0

    # Badges: any tier reached = badge
    badges = []
    for t in tiers:
        if active_count >= int(t['count']):
            badges.append({"tier": t['count'], "label": t.get('label') or f"Niveau {t['count']}"})

    base = _public_base_url(request)
    share_url = f"{base}/r/{code}"
    return {
        "referral_code": code,
        "share_url": share_url,
        "share_short_url": share_url,
        "share_long_url": f"{base}/register?ref={code}",
        "stats": {
            "pending": stats['pending'] or 0,
            "active": stats['active'] or 0,
            "rewarded": stats['rewarded'] or 0,
            "total": stats['total'] or 0,
            "total_earned_usd": str(stats['total_earned_usd'] or 0),
            "active_count": active_count,
        },
        "tiers": tiers,
        "next_tier": next_tier,
        "progress_to_next_pct": progress_pct,
        "bonuses": {
            "referrer_usd": referrer_bonus,
            "referee_usd": referee_bonus,
        },
        "badges": badges,
    }


@router.get("/list")
async def list_my_referrals(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = $1", user['user_id'])
        rows = await conn.fetch("""
            SELECT r.id, r.status, r.reward_given, r.created_at, r.activated_at,
                r.referrer_bonus_usd, r.blocked,
                u.user_id, u.username, u.first_name, u.last_name, u.avatar, u.email
            FROM referrals r
            JOIN users u ON u.user_id = r.referred_id
            WHERE r.referrer_id = $1
            ORDER BY r.created_at DESC LIMIT $2 OFFSET $3
        """, user['user_id'], limit, offset)
    items = [{
        "referral_id": r['id'],
        "status": r['status'],
        "blocked": bool(r['blocked']),
        "reward_given": r['reward_given'],
        "referrer_bonus_usd": str(r['referrer_bonus_usd'] or 0),
        "created_at": r['created_at'].isoformat(),
        "activated_at": r['activated_at'].isoformat() if r['activated_at'] else None,
        "friend": {
            "user_id": r['user_id'],
            "name": f"{r['first_name']} {r['last_name']}".strip() or r['username'],
            "username": r['username'],
            "avatar": r['avatar'] or '',
            "email_masked": (r['email'][:2] + '***@' + r['email'].split('@')[1]) if r['email'] else '',
        },
    } for r in rows]
    return {"referrals": items, "total": count, "page": page, "limit": limit}


class ValidateCodeRequest(BaseModel):
    code: str


@router.post("/validate-code")
async def validate_code(req: ValidateCodeRequest):
    code = (req.code or '').strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Code requis")
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT user_id, first_name, last_name, username FROM users WHERE referral_code = $1", code)
    if not user:
        raise HTTPException(status_code=404, detail="Code de parrainage invalide")
    return {"valid": True, "referrer_name": f"{user['first_name']} {user['last_name']}".strip() or user['username']}


# iter110 — Public landing page metadata for /p/{code}.
@router.get("/preview/{code}")
async def preview_code(code: str, request: Request):
    """Public — no auth — returns the data needed to render the conversion
    landing at /p/{code}. Includes the referrer name + avatar + bonuses
    (when the program is enabled). 404 if the code does not exist.
    """
    code = (code or "").strip().upper()
    if not code or len(code) > 16:
        raise HTTPException(status_code=400, detail="Code invalide")

    if not await get_bool("referral_enabled", True):
        raise HTTPException(status_code=503, detail="Le parrainage est temporairement indisponible.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        ref = await conn.fetchrow(
            """SELECT user_id, username, first_name, last_name, avatar, country_code, is_pro
                 FROM users WHERE referral_code = $1""",
            code,
        )
    if not ref:
        raise HTTPException(status_code=404, detail="Code de parrainage invalide")

    referrer_bonus = await get_float("referral_referrer_bonus_usd", 0.0)
    referee_bonus = await get_float("referral_referee_bonus_usd", 0.0)
    boost = await get_active_boost()
    base = _public_base_url(request)
    name = (f"{(ref['first_name'] or '').strip()} {(ref['last_name'] or '').strip()}").strip()
    if not name:
        name = ref["username"] or "Un ami"
    return {
        "code": code,
        "referrer": {
            "name": name,
            "first_name": (ref["first_name"] or "").strip(),
            "username": ref["username"],
            "avatar": ref["avatar"] or "",
            "country_code": (ref["country_code"] or "").upper()[:2],
            "is_pro": bool(ref["is_pro"]),
        },
        "bonuses": {
            "referrer_usd": referrer_bonus,
            "referee_usd": referee_bonus,
        },
        "boost": boost,
        "register_url": f"{base}/register?ref={code}",
        "share_url": f"{base}/r/{code}",
    }


class ApplyReferralRequest(BaseModel):
    code: str
    device_id: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None


@router.post("/apply")
async def apply_referral(req: ApplyReferralRequest, request: Request):
    if not await get_bool("referral_enabled", True):
        raise HTTPException(status_code=503, detail="Le parrainage est désactivé.")
    user = await get_current_user(request)
    code = (req.code or '').strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Code requis")

    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else None)
    # iter150 — prefer the real-client-IP helper (XFF aware) where possible.
    try:
        from utils.network import client_ip as _cip
        resolved = _cip(request)
        if resolved and resolved != "unknown":
            ip = resolved
    except Exception:
        pass
    device = (req.device_id or "")[:128] or None
    utm_source = (req.utm_source or "")[:40] or None
    utm_medium = (req.utm_medium or "")[:40] or None
    utm_campaign = (req.utm_campaign or "")[:80] or None

    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_referrals_utm_columns(conn)
        async with conn.transaction():
            existing = await conn.fetchrow("SELECT referred_by FROM users WHERE user_id = $1", user['user_id'])
            if existing and existing['referred_by']:
                raise HTTPException(status_code=400, detail="Vous êtes déjà parrainé")
            referrer = await conn.fetchrow("SELECT user_id FROM users WHERE referral_code = $1", code)
            if not referrer:
                raise HTTPException(status_code=404, detail="Code de parrainage invalide")
            if referrer['user_id'] == user['user_id']:
                raise HTTPException(status_code=400, detail="Auto-parrainage interdit")

            # Anti-fraud cap
            blocked_flag, reason = await _over_daily_cap(conn, ip, device)

            # iter150 — multi-signal anti-fraud scoring (additional layer
            # on top of the daily cap). Computes risk 0-100 from IP/device
            # velocity + co-location signals. Risk ≥ 80 forces a blocked
            # state so the referral lands in the admin review queue.
            fraud_signals: list = []
            try:
                from services.referral_fraud_service import score_referral
                report = await score_referral(conn, referrer['user_id'],
                                              user['user_id'], ip, device)
                fraud_signals = report.get("signals") or []
                if report.get("risk", 0) >= 80 and not blocked_flag:
                    blocked_flag = True
                    reason = (reason or "fraud_score") + ":high_risk"
                    logger.warning(
                        "referral.high_risk referrer=%s referred=%s risk=%s signals=%s",
                        referrer['user_id'], user['user_id'],
                        report.get("risk"), [s.get("code") for s in fraud_signals],
                    )
            except Exception as _e:
                logger.warning(f"referral fraud score failed: {_e}")

            await conn.execute("UPDATE users SET referred_by = $1 WHERE user_id = $2",
                               referrer['user_id'], user['user_id'])
            try:
                row = await conn.fetchrow("""
                    INSERT INTO referrals (referrer_id, referred_id, status, ip_address, device_id,
                                           blocked, blocked_reason,
                                           utm_source, utm_medium, utm_campaign)
                    VALUES ($1, $2, 'pending', $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                """, referrer['user_id'], user['user_id'], ip, device,
                   blocked_flag, reason if blocked_flag else None,
                   utm_source, utm_medium, utm_campaign)
            except Exception:
                raise HTTPException(status_code=400, detail="Parrainage déjà enregistré")

            if blocked_flag:
                logger.warning(f"Referral blocked on apply (user={user['user_id']} reason={reason})")
                return {"message": "Code appliqué mais en attente de validation anti-fraude.",
                        "referrer_id": referrer['user_id'], "blocked": True}

            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'referral_new', 'Nouveau filleul !', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", referrer['user_id'],
                 f"{user.get('first_name') or 'Un ami'} a utilisé votre code. Dès qu'il sera actif, votre bonus sera crédité.")
        # Fire the "filleul inscrit" email to the referrer (best-effort, dedup 24h).
        try:
            await _notify_invited_email(conn, referrer_id=referrer['user_id'],
                                        referred_user=user)
        except Exception as _e:
            logger.warning(f"referral invited email skipped: {_e}")
    return {"message": "Code de parrainage appliqué avec succès",
            "referrer_id": referrer['user_id'], "blocked": False}


class ClaimRequest(BaseModel):
    tier_count: int  # the required count that identifies the tier


@router.post("/claim")
async def claim_reward(req: ClaimRequest, request: Request):
    user = await get_current_user(request)
    tiers = await _default_tiers()
    matched = next((t for t in tiers if int(t['count']) == int(req.tier_count)), None)
    if not matched:
        raise HTTPException(status_code=400, detail="Palier introuvable")

    required = int(matched['count'])
    reward_type = matched.get('reward_type', 'pro')
    reward_value = matched.get('reward_value', 30)
    label = matched.get('label') or f"Palier {required}"

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch("""
                SELECT id FROM referrals
                WHERE referrer_id = $1 AND status = 'active' AND (blocked = FALSE OR blocked IS NULL)
                ORDER BY activated_at ASC NULLS LAST LIMIT $2
            """, user['user_id'], required)
            if len(rows) < required:
                raise HTTPException(status_code=400,
                                    detail=f"Il vous faut {required} filleul(s) actif(s). Actuels : {len(rows)}")

            ids = [r['id'] for r in rows]

            if reward_type == "wallet":
                amount_usd = Decimal(str(reward_value))
                boost = await get_active_boost()
                if boost.get("active") and boost.get("applies_to_tiers"):
                    amount_usd = (amount_usd * Decimal(str(boost.get("multiplier", 1)))
                                 ).quantize(Decimal("0.01"))
                res = await _credit_wallet_bonus(conn, user['user_id'], amount_usd,
                                                  f"Palier parrainage: {label}", "tier", None)
                detail = res
            elif reward_type == "pro":
                plan_id = await get_setting("referral_tier_plan_id") or "starter"
                plan = await conn.fetchrow("SELECT * FROM pro_plans WHERE plan_id = $1", plan_id)
                if not plan:
                    raise HTTPException(status_code=500, detail="Plan Pro pour palier introuvable")
                days = int(reward_value)
                now = datetime.now(timezone.utc)
                existing_sub = await conn.fetchrow("""
                    SELECT * FROM subscriptions WHERE user_id = $1 AND status = 'active' AND expires_at > NOW()
                    ORDER BY expires_at DESC LIMIT 1
                """, user['user_id'])
                if existing_sub:
                    new_expiry = existing_sub['expires_at'] + timedelta(days=days)
                    await conn.execute(
                        "UPDATE subscriptions SET expires_at = $1 WHERE id = $2",
                        new_expiry, existing_sub['id'])
                else:
                    new_expiry = now + timedelta(days=days)
                    await conn.execute("""
                        INSERT INTO subscriptions (user_id, plan_type, price, currency, status,
                            starts_at, expires_at, source, duration_days)
                        VALUES ($1, $2, 0, 'USD', 'active', $3, $4, 'referral_tier', $5)
                    """, user['user_id'], plan_id, now, new_expiry, days)
                await conn.execute("""
                    UPDATE users SET is_pro = TRUE, pro_type = $1, pro_expires_at = $2 WHERE user_id = $3
                """, plan['id'], new_expiry, user['user_id'])
                await conn.execute("""
                    INSERT INTO referral_rewards_log
                        (referral_id, user_id, role, reward_type, amount_usd, amount_local, currency, details)
                    VALUES ($1, $2, 'tier', 'pro', 0, 0, 'USD', $3::jsonb)
                """, None, user['user_id'],
                   json.dumps({"days": days, "plan_id": plan_id, "expiry": new_expiry.isoformat()}))
                detail = {"plan_id": plan_id, "days": days, "expiry": new_expiry.isoformat()}
            else:
                raise HTTPException(status_code=400, detail="Type de récompense inconnu")

            await conn.execute("""
                UPDATE referrals SET status = 'rewarded', reward_given = TRUE, reward_type = $1
                WHERE id = ANY($2::int[])
            """, reward_type, ids)

            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message)
                VALUES ($1, $2, 'referral_reward', 'Récompense parrainage !', $3)
            """, f"notif_{uuid.uuid4().hex[:12]}", user['user_id'],
                 f"Vous avez débloqué : {label} !")

            # iter110 — Send "palier débloqué" email to the user (best-effort).
            if reward_type == "wallet":
                _summary = f"+{detail.get('amount_local', '')} {detail.get('currency', '')}"
            elif reward_type == "pro":
                _summary = f"+{detail.get('days', '')} jours JAPAP Pro"
            else:
                _summary = label
            try:
                await _notify_rewarded_email(
                    conn, referrer_id=user['user_id'],
                    tier_label=label, reward_summary=_summary,
                )
            except Exception as _e:
                logger.warning(f"referral rewarded email skipped: {_e}")

            return {"message": f"Récompense débloquée : {label}", "reward": detail, "tier_count": required}


@router.get("/leaderboard")
async def referral_leaderboard(
    request: Request,
    window: Optional[str] = None,
    scope: str = "global",   # 'global' | 'country'
    country: str = "",       # ISO-2 — defaults to current user's country
    limit: int = 50,
):
    user = await get_current_user(request)
    if not await get_bool("referral_leaderboard_enabled", True):
        return {"enabled": False, "leaders": []}
    w = (window or await get_setting("referral_leaderboard_window") or "weekly").lower()
    if w not in ("weekly", "all_time"):
        w = "weekly"

    cc = (country or "").upper()[:2]
    if scope == "country" and not cc:
        cc = (user.get("country_code") or "").upper()[:2]
    limit = max(1, min(int(limit or 50), 200))
    weekly_extra = "AND r.activated_at > NOW() - INTERVAL '7 days'" if w == "weekly" else ""

    pool = await get_pool()
    async with pool.acquire() as conn:
        clauses = []
        params: list = []
        if scope == "country" and cc:
            params.append(cc)
            clauses.append(f"u.country_code = ${len(params)}")
        country_where = (" AND " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        sql = f"""
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.avatar,
                   u.country_code,
                   COUNT(r.id) FILTER (WHERE r.status IN ('active','rewarded') {weekly_extra}) AS cnt
              FROM users u JOIN referrals r ON r.referrer_id = u.user_id
             WHERE TRUE {country_where}
             GROUP BY u.user_id, u.username, u.first_name, u.last_name, u.avatar,
                      u.country_code
            HAVING COUNT(r.id) FILTER (WHERE r.status IN ('active','rewarded') {weekly_extra}) > 0
             ORDER BY cnt DESC LIMIT ${len(params)}
        """
        rows = await conn.fetch(sql, *params)

        # Compute requester's two ranks (global + own country) — independent
        # of the `scope` query-param, so the UI can display both.
        async def _my_rank(filter_country: str | None):
            ext = "AND r.activated_at > NOW() - INTERVAL '7 days'" if w == "weekly" else ""
            params2: list = [user["user_id"]]
            country_clause = ""
            if filter_country:
                params2.append(filter_country)
                country_clause = f"AND u.country_code = ${len(params2)}"
            my_cnt = await conn.fetchval(
                f"""SELECT COUNT(r.id) FILTER (WHERE r.status IN ('active','rewarded') {ext})
                       FROM referrals r JOIN users u ON u.user_id = r.referrer_id
                      WHERE u.user_id = $1 {country_clause}""",
                *params2,
            ) or 0
            if my_cnt == 0:
                return {"rank": None, "count": 0}
            country_clause_outer = ""
            params3: list = [my_cnt]
            if filter_country:
                params3.append(filter_country)
                country_clause_outer = f"AND u.country_code = ${len(params3)}"
            higher = await conn.fetchval(
                f"""SELECT COUNT(*) FROM (
                       SELECT u.user_id,
                              COUNT(r.id) FILTER (WHERE r.status IN ('active','rewarded') {ext}) AS c
                         FROM users u JOIN referrals r ON r.referrer_id = u.user_id
                        WHERE TRUE {country_clause_outer}
                        GROUP BY u.user_id
                       HAVING COUNT(r.id) FILTER (WHERE r.status IN ('active','rewarded') {ext}) > $1
                    ) t""",
                *params3,
            ) or 0
            return {"rank": int(higher) + 1, "count": int(my_cnt)}

        my_global = await _my_rank(None)
        my_country_cc = (user.get("country_code") or "").upper()[:2]
        my_country = await _my_rank(my_country_cc) if my_country_cc else {"rank": None, "count": 0}

    return {
        "enabled": True,
        "window": w,
        "scope": scope,
        "country": cc,
        "leaders": [{
            "rank": i + 1,
            "user_id": r["user_id"],
            "name": f"{(r['first_name'] or '').strip()} {(r['last_name'] or '').strip()}".strip() or r["username"],
            "avatar": r["avatar"] or "",
            "country_code": (r["country_code"] or "").upper()[:2],
            "active_count": int(r["cnt"] or 0),
        } for i, r in enumerate(rows)],
        "me": {
            "user_id": user["user_id"],
            "country_code": my_country_cc,
            "rank_global":  my_global["rank"],
            "rank_country": my_country["rank"],
            "active_count": my_global["count"],
        },
    }
