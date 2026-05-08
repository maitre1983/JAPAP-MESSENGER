import bcrypt
import jwt
import os
import uuid
import random
import secrets
import logging
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from database import get_pool
from services.email_service import send_otp_email, send_password_reset_email, send_password_reset_email_detailed
from middleware.turnstile import verify_turnstile
from services.math_captcha import issue_captcha, verify_captcha, issue_human_cookie
from utils.network import client_ip as _client_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_ALGORITHM = "HS256"

# Security: cookies must be served over HTTPS in production. In preview /
# Kubernetes ingress HTTPS is always terminated upstream, so enable `secure`
# whenever the APP_ENV is not explicitly "local". The admin can still disable
# via COOKIE_SECURE=false if running behind a plain-HTTP reverse proxy.
def _cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "true").lower() not in ("false", "0", "no")

def _cookie_samesite() -> str:
    # "none" only works with Secure=True; default to "lax" which covers the
    # main CSRF surface while keeping top-level navigation auth'd.
    return os.environ.get("COOKIE_SAMESITE", "lax").lower()

def get_jwt_secret():
    return os.environ["JWT_SECRET"]

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    # iter236 — bump access TTL from 60 to 480 minutes (8h) to eliminate
    # mid-flow "Session révoquée" disruptions on long admin sessions.
    payload = {"sub": user_id, "email": email,
               "iat": int(now.timestamp()),
               "exp": now + timedelta(minutes=480), "type": "access"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str, jti: Optional[str] = None,
                          ttl_days: int = 7) -> tuple[str, str]:
    """Returns (token, jti). Each refresh carries a unique JTI so it can be
    revoked individually. Rotation + single-use replay-protection is
    enforced in /api/auth/refresh.

    iter146 — `ttl_days` is variable to support trusted-device long-lived
    sessions (90d vs default 7d).
    """
    from services.security_service import new_jti as _new_jti
    jti = jti or _new_jti()
    payload = {
        "sub": user_id, "jti": jti, "type": "refresh",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": datetime.now(timezone.utc) + timedelta(days=ttl_days),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM), jti

def set_auth_cookies(response: Response, access_token: str, refresh_token: str,
                     refresh_ttl_days: int = 7, persist: bool = True):
    # iter237 — `persist=False` issues SESSION cookies (no max-age) so the
    # browser drops them when the tab/window closes. This implements the
    # "Se souvenir de moi" opt-in: unchecked → no auto-relogin on next visit.
    sec = _cookie_secure()
    ss = _cookie_samesite()
    access_kwargs = {"httponly": True, "secure": sec, "samesite": ss, "path": "/"}
    refresh_kwargs = dict(access_kwargs)
    if persist:
        access_kwargs["max_age"] = 28800
        refresh_kwargs["max_age"] = int(refresh_ttl_days * 86400)
    response.set_cookie(key="access_token", value=access_token, **access_kwargs)
    response.set_cookie(key="refresh_token", value=refresh_token, **refresh_kwargs)

async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        # Try session_token for Google OAuth
        session_token = request.cookies.get("session_token")
        if session_token:
            return await get_user_from_session(session_token)
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        pool = await get_pool()
        async with pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", payload["sub"])
            if not user:
                raise HTTPException(status_code=401, detail="User not found")
            # Iter 61 — force-logout: reject tokens minted before the password
            # was changed (by the user or by an admin).
            pw_changed = user["password_changed_at"] if "password_changed_at" in user.keys() else None
            iat = payload.get("iat")
            if pw_changed and iat is not None:
                if iat < int(pw_changed.replace(tzinfo=timezone.utc).timestamp()):
                    raise HTTPException(status_code=401, detail="Session révoquée. Reconnectez-vous.")
            return dict(user)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_admin(request: Request):
    """Authenticate and enforce admin role. Returns the admin user dict.
    Iter83: both 'admin' and 'superadmin' roles are accepted — superadmin
    is a strict superset of admin (inherits every admin capability)."""
    user = await get_current_user(request)
    if user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_superadmin(request: Request):
    """Only users with role='superadmin' may pass. Used for admin management
    (create / update roles / delete) and audit access."""
    user = await get_current_user(request)
    if user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return user


def require_sub_role(*allowed_sub_roles: str):
    """FastAPI dependency factory — grants access if the caller is superadmin
    OR an admin whose `admin_sub_roles` JSONB array contains at least one of
    the allowed values. Usage:

        @router.get("/wallet-ops", dependencies=[Depends(require_sub_role('wallet_manager'))])
    """
    async def _dep(request: Request):
        user = await get_current_user(request)
        role = user.get("role")
        if role == "superadmin":
            return user
        if role == "admin":
            subs = user.get("admin_sub_roles") or []
            # asyncpg returns JSONB as dict/list; tolerate str JSON too.
            if isinstance(subs, str):
                import json as _json
                try:
                    subs = _json.loads(subs)
                except Exception:
                    subs = []
            if any(sr in subs for sr in allowed_sub_roles):
                return user
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    return _dep

async def get_user_from_session(session_token: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        session = await conn.fetchrow("SELECT * FROM user_sessions WHERE session_token = $1", session_token)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid session")
        if session['expires_at'].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Session expired")
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", session['user_id'])
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return dict(user)

def user_to_response(user: dict) -> dict:
    exclude = {'password_hash'}
    result = {}
    for k, v in user.items():
        if k in exclude:
            continue
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    # Expose onboarding flag consistently across endpoints (login + /me + register).
    # Falsy timestamp (None) means onboarding not yet completed.
    if 'onboarding_completed_at' in user:
        result['onboarding_completed'] = bool(user['onboarding_completed_at'])
    elif 'onboarding_completed' not in result:
        result['onboarding_completed'] = False
    return result


class RegisterRequest(BaseModel):
    # Iter83 — every personal field except `referral_code` is strictly
    # required. Pydantic enforces "must be present + min length" so the
    # backend rejects spoofed clients that try to bypass the frontend UI.
    email: EmailStr
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    country_code: str = Field(..., min_length=2, max_length=2)
    phone_number: str = Field(..., min_length=7, max_length=32)
    terms_accepted: bool = False
    referral_code: str = ""  # only truly optional field
    # iter110 — UTM tracking captured from the share-link landing.
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    # iter73: 2-letter ISO hint from `navigator.language` on the signup page.
    # Used (alongside proxy country + Accept-Language) to pick the UI locale
    # automatically so new users see JAPAP in their tongue from second 0.
    detected_lang: Optional[str] = None
    # iter74: 3-letter ISO-4217 hint for the user's wallet currency. Usually
    # resolved from the frontend by calling /api/currency/detect — then the
    # backend merges it with the proxy country header as a fallback.
    detected_currency: Optional[str] = None
    # iter94 — Cloudflare Turnstile token (cf-turnstile-response). Validated
    # server-side via middleware/turnstile.py. Optional at the schema level
    # for backward compatibility; enforcement is in the endpoint when the
    # secret key is provisioned.
    # iter141ter — replaced as the primary anti-bot by `captcha_id` +
    # `captcha_answer` (math captcha). Turnstile fields kept optional for
    # transitional clients but the endpoints only require the math captcha.
    turnstile_token: Optional[str] = None
    captcha_id: Optional[str] = None
    captcha_answer: Optional[str] = None


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    code: str


class ResendOtpRequest(BaseModel):
    email: EmailStr

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    turnstile_token: Optional[str] = None
    captcha_id: Optional[str] = None
    captcha_answer: Optional[str] = None
    # iter237 — When False (default), cookies are issued WITHOUT max-age
    # so the browser drops them on close (true session cookies). The user
    # must re-authenticate on next visit.
    remember_me: Optional[bool] = False

class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    turnstile_token: Optional[str] = None
    captcha_id: Optional[str] = None
    captcha_answer: Optional[str] = None

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
    captcha_id: Optional[str] = None
    captcha_answer: Optional[str] = None

class SessionRequest(BaseModel):
    session_id: str


class UpdatePreferencesRequest(BaseModel):
    preferred_lang: Optional[str] = None
    # iter74: lets the user change their wallet display currency from the
    # Profile page without touching the server via another endpoint.
    preferred_currency: Optional[str] = None


from constants import SUPPORTED_LANGS as _SUPPORTED_LANGS


def _gen_otp() -> str:
    return f"{random.randint(0, 999999):06d}"


async def _issue_otp(conn, email: str, purpose: str = "register") -> str:
    """Generate a 6-digit OTP, store with 10-min expiry, rate-limit 1/minute."""
    now = datetime.now(timezone.utc)
    recent = await conn.fetchrow("""
        SELECT created_at FROM email_otps WHERE email = $1 AND purpose = $2
        ORDER BY created_at DESC LIMIT 1
    """, email, purpose)
    if recent and (now - recent['created_at'].replace(tzinfo=timezone.utc)).total_seconds() < 60:
        raise HTTPException(status_code=429, detail="Please wait 60 seconds before requesting another code")
    code = _gen_otp()
    expires = now + timedelta(minutes=10)
    await conn.execute("""
        INSERT INTO email_otps (email, code, purpose, expires_at) VALUES ($1, $2, $3, $4)
    """, email, code, purpose, expires)
    return code


@router.get("/captcha")
async def get_captcha(request: Request):
    """iter141ter — Public endpoint that returns a fresh math captcha.
    The frontend calls this on mount of any auth page (login / register /
    forgot-password / reset-password) and forwards the captcha_id +
    captcha_answer with the auth submission. Stateless HMAC token,
    10-min TTL, no DB required.

    iter141quater — when the caller already has a valid `japap_human`
    cookie we still return a captcha (so a logout/login cycle Just Works
    even with a hostile network) but we surface `required: false` so the
    frontend can skip rendering the input.
    """
    from services.math_captcha import has_valid_human_cookie
    import os as _os
    # iter237b — If captcha is globally disabled via env, signal to the
    # frontend to skip rendering the widget AND submit empty values.
    if _os.environ.get("CAPTCHA_ENABLED", "true").strip().lower() in ("false", "0", "no", "off"):
        return {"captcha_id": "", "question": "", "expires_at": "", "required": False, "disabled": True}
    payload = issue_captcha()
    payload["required"] = not has_valid_human_cookie(request)
    payload["disabled"] = False
    return payload


@router.post("/register")
async def register(req: RegisterRequest, request: Request, response: Response):
    """Register a new user. Creates the account as INACTIVE (email_verified=FALSE)
    and sends a 6-digit OTP to the provided email. User must call /verify-otp to
    activate the account and receive auth tokens.
    """
    # iter141ter — Math captcha replaces Turnstile as the primary anti-bot
    # check on this endpoint. Turnstile remains optional for legacy clients
    # but the math captcha is now mandatory.
    verify_captcha(req.captcha_id, req.captcha_answer, request)
    # iter141quater — issue silent humanity cookie so subsequent auth
    # submissions from this device skip the captcha entirely.
    issue_human_cookie(response)

    if not req.terms_accepted:
        raise HTTPException(status_code=400, detail="Vous devez accepter les Termes et Conditions")

    email = req.email.lower().strip()
    # iter237f — Country must be a strict ISO-2 code. Truncating arbitrary
    # input to [:2] produces garbage ('United States' → 'UN'). We accept the
    # value only when its raw form is exactly 2 alpha chars; otherwise keep
    # empty and let downstream defaults kick in. Frontend signup form already
    # sends ISO-2 from a dropdown, so this is a server-side safety net.
    _raw_cc = (req.country_code or "").strip().upper()
    country = _raw_cc if (len(_raw_cc) == 2 and _raw_cc.isalpha()) else ""
    phone = (req.phone_number or "").strip()

    # iter73: resolve preferred_lang from the signal-richest source so that
    # on first login the user already sees JAPAP in their language without
    # having to touch the switcher.
    from services.language_detector import detect_user_language
    proxy_country = (
        request.headers.get("cf-ipcountry")
        or request.headers.get("x-country-code")
        or request.headers.get("x-appengine-country")
    )
    preferred_lang = detect_user_language(
        detected_lang=req.detected_lang,
        country_code=country,
        proxy_country=proxy_country,
        accept_language=request.headers.get("accept-language", ""),
    )

    # iter74: resolve preferred_currency with the same priority ladder so
    # the user's wallet is pre-tuned to their real-life currency from the
    # first login. Falls back to None → CurrencyProvider defaults to USD.
    from services.currency_detector import detect_user_currency
    preferred_currency = detect_user_currency(
        detected_currency=req.detected_currency,
        country_code=country,
        proxy_country=proxy_country,
    )

    pool = await get_pool()
    referrer_user_id_for_email: Optional[str] = None
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT user_id, email_verified FROM users WHERE email = $1", email)
        if existing and existing['email_verified']:
            raise HTTPException(status_code=400, detail="Email already registered")

        if existing:
            # SECURITY: do NOT overwrite the password/profile of an existing unverified
            # user — anyone knowing the email could take over the account. Just re-send
            # the OTP so the legitimate owner can finish signing up.
            user_id = existing['user_id']
            code = await _issue_otp(conn, email, purpose="register")
        else:
            user_id = f"user_{uuid.uuid4().hex[:12]}"
            username = email.split("@")[0] + uuid.uuid4().hex[:4]
            hashed = hash_password(req.password)
            await conn.execute("""
                INSERT INTO users (user_id, username, email, password_hash, first_name, last_name,
                                   role, is_active, terms_accepted, terms_accepted_at,
                                   country, country_code, phone_number, email_verified, preferred_lang, preferred_currency)
                VALUES ($1, $2, $3, $4, $5, $6, 'user', FALSE, TRUE, $7, $8, $8, $9, FALSE, $10, $11)
            """, user_id, username, email, hashed, req.first_name, req.last_name,
               datetime.now(timezone.utc), country or None, phone, preferred_lang, preferred_currency)
            await conn.execute("INSERT INTO wallets (user_id, balance) VALUES ($1, 0.00)", user_id)

            # Referral linkage (iter110: capture UTM source/medium/campaign).
            if req.referral_code:
                code_ref = req.referral_code.strip().upper()
                referrer = await conn.fetchrow("SELECT user_id FROM users WHERE referral_code = $1", code_ref)
                if referrer and referrer['user_id'] != user_id:
                    await conn.execute("UPDATE users SET referred_by = $1 WHERE user_id = $2",
                                       referrer['user_id'], user_id)
                    try:
                        from routes.referrals import ensure_referrals_utm_columns
                        await ensure_referrals_utm_columns(conn)
                        await conn.execute("""
                            INSERT INTO referrals (referrer_id, referred_id, status,
                                                   utm_source, utm_medium, utm_campaign)
                            VALUES ($1, $2, 'pending', $3, $4, $5)
                        """, referrer['user_id'], user_id,
                           (req.utm_source or "")[:40] or None,
                           (req.utm_medium or "")[:40] or None,
                           (req.utm_campaign or "")[:80] or None)
                        referrer_user_id_for_email = referrer['user_id']
                    except Exception as e:
                        logger.warning(f"Referral insert failed: {e}")

            code = await _issue_otp(conn, email, purpose="register")
        # Outside the transaction: best-effort dispatch the "filleul inscrit"
        # email to the referrer. Dedup is handled inside the email service.
        if referrer_user_id_for_email:
            try:
                from routes.referrals import _notify_invited_email
                async with pool.acquire() as conn2:
                    await _notify_invited_email(
                        conn2,
                        referrer_id=referrer_user_id_for_email,
                        referred_user={"user_id": user_id,
                                       "first_name": req.first_name},
                    )
            except Exception as _e:
                logger.warning(f"referral.invited dispatch failed: {_e}")

    # Send email (non-blocking fail)
    await send_otp_email(email, code, purpose="Verify your JAPAP account")

    return {
        "status": "otp_sent",
        "email": email,
        "message": "A 6-digit verification code has been sent to your email. Enter it to activate your account.",
    }


@router.post("/verify-otp")
async def verify_otp(req: VerifyOtpRequest, request: Request, response: Response):
    """Verify the OTP and activate the user account. Issues auth tokens on success."""
    email = req.email.lower().strip()
    code = (req.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Code requis")

    pool = await get_pool()
    async with pool.acquire() as conn:
        otp = await conn.fetchrow("""
            SELECT id, code, attempts, used, expires_at FROM email_otps
            WHERE email = $1 AND purpose = 'register' AND used = FALSE
            ORDER BY created_at DESC LIMIT 1
        """, email)
        if not otp:
            raise HTTPException(status_code=400, detail="Aucun code actif pour cet email")
        if otp['expires_at'].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Code expiré, demandez-en un nouveau")
        if otp['attempts'] >= 5:
            raise HTTPException(status_code=429, detail="Trop de tentatives, demandez un nouveau code")
        if otp['code'] != code:
            await conn.execute("UPDATE email_otps SET attempts = attempts + 1 WHERE id = $1", otp['id'])
            raise HTTPException(status_code=400, detail="Code invalide")

        await conn.execute("UPDATE email_otps SET used = TRUE WHERE id = $1", otp['id'])
        user = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
        if not user:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable")
        await conn.execute("""
            UPDATE users SET email_verified = TRUE, is_active = TRUE, is_online = TRUE,
                             last_seen = $1, updated_at = $1 WHERE user_id = $2
        """, datetime.now(timezone.utc), user['user_id'])
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user['user_id'])

    access_token = create_access_token(user['user_id'], user['email'])
    refresh_token, rt_jti = create_refresh_token(user['user_id'])
    set_auth_cookies(response, access_token, refresh_token)
    # iter82 — track this session (first device record for the user).
    try:
        from services.security_service import upsert_active_session, log_security_event
        ip = _client_ip(request) if request else ""
        ua = request.headers.get("user-agent", "") if request else ""
        await upsert_active_session(user['user_id'], rt_jti, ip, ua)
        await log_security_event(user['user_id'], "auth.verify_otp_success",
                                 ip=ip, ua=ua)
    except Exception as _e:
        logger.warning(f"session tracking failed: {_e}")
    # iter109 — Now that the user is verified, kick the referral activation
    # check. If the admin set `referral_activation_requires_action=false`,
    # the bonus is credited immediately on email verification.
    try:
        from routes.referrals import check_and_activate_referral
        await check_and_activate_referral(user['user_id'])
    except Exception as _e:
        logger.warning(f"referral activation skipped after verify-otp: {_e}")
    return {"user": user_to_response(dict(user)), "access_token": access_token}


@router.post("/resend-otp")
async def resend_otp(req: ResendOtpRequest):
    email = req.email.lower().strip()
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT user_id, email_verified FROM users WHERE email = $1", email)
        if not user:
            # Avoid email enumeration
            return {"status": "otp_sent"}
        if user['email_verified']:
            raise HTTPException(status_code=400, detail="Email déjà vérifié")
        code = await _issue_otp(conn, email, purpose="register")
    await send_otp_email(email, code, purpose="Verify your JAPAP account")
    return {"status": "otp_sent"}


# ── Iter83 — Superadmin email 2FA completion ────────────────────────────────
@router.post("/verify-2fa")
async def verify_2fa(req: VerifyOtpRequest, request: Request, response: Response):
    """Second factor for superadmin logins. Takes (email, code) issued by
    /login when role='superadmin'. On success, issues the auth cookies and
    returns the user payload exactly like /login would for a normal user.

    Rate-limit: the same per-OTP `attempts` counter used by /verify-otp
    applies (5 tries per code, 10-min TTL, 1-per-minute resend guard)."""
    email = req.email.lower().strip()
    code = (req.code or "").strip()
    if not code or not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="Code invalide")

    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
        if not user or user['role'] != 'superadmin':
            # Neutral error — never reveal that the email isn't a superadmin.
            raise HTTPException(status_code=400, detail="Code invalide")

        otp = await conn.fetchrow("""
            SELECT id, code, attempts, expires_at FROM email_otps
            WHERE email = $1 AND purpose = 'login_2fa' AND used = FALSE
            ORDER BY created_at DESC LIMIT 1
        """, email)
        if not otp:
            raise HTTPException(status_code=400, detail="Aucun code actif")
        if otp['expires_at'].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Code expiré, connectez-vous à nouveau")
        if otp['attempts'] >= 5:
            raise HTTPException(status_code=429, detail="Trop de tentatives — recommencez la connexion")
        if otp['code'] != code:
            await conn.execute("UPDATE email_otps SET attempts = attempts + 1 WHERE id = $1", otp['id'])
            raise HTTPException(status_code=400, detail="Code invalide")

        await conn.execute("UPDATE email_otps SET used = TRUE WHERE id = $1", otp['id'])
        await conn.execute(
            "UPDATE users SET last_seen = $1, is_online = TRUE WHERE user_id = $2",
            datetime.now(timezone.utc), user['user_id'],
        )

    access_token = create_access_token(user['user_id'], user['email'])
    refresh_token, rt_jti = create_refresh_token(user['user_id'])
    set_auth_cookies(response, access_token, refresh_token)

    # Full audit trail for superadmin login success
    ip = _client_ip(request) if request else ""
    ua = request.headers.get("user-agent", "") if request else ""
    try:
        from services.security_service import upsert_active_session, log_security_event
        await upsert_active_session(user['user_id'], rt_jti, ip, ua)
        await log_security_event(
            user['user_id'], "auth.superadmin_login_success",
            severity="warning", ip=ip, ua=ua,
        )
    except Exception as _e:
        logger.warning(f"superadmin session tracking failed: {_e}")

    try:
        await log_admin_action(
            actor_id=user['user_id'], actor_email=user['email'],
            action="superadmin.login", ip=ip, ua=ua,
        )
    except Exception:
        pass

    return {"user": user_to_response(dict(user)), "access_token": access_token}


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    # iter141ter — Math captcha (replaces Turnstile).
    verify_captcha(req.captcha_id, req.captcha_answer, request)
    # iter141quater — refresh humanity cookie on every successful captcha
    # check so it stays warm for active users.
    issue_human_cookie(response)

    pool = await get_pool()
    ip = _client_ip(request)
    identifier = f"{ip}:{req.email.lower()}"
    
    async with pool.acquire() as conn:
        attempt = await conn.fetchrow("SELECT * FROM login_attempts WHERE identifier = $1", identifier)
        if attempt and attempt['locked_until'] and attempt['locked_until'].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
        
        user = await conn.fetchrow("SELECT * FROM users WHERE email = $1", req.email.lower())
        # iter146 — BULLETPROOF legacy detection.
        # MIGRATION_RESET_REQUIRED is RESERVED for accounts imported from
        # JAPAP 1.0 ETL and never reconnected since. We require ALL THREE
        # invariants before triggering the prompt:
        #   (a) `legacy_id IS NOT NULL` — only the ETL import sets this,
        #       guaranteeing a brand-new /register account is never legacy
        #       even if some other column got mis-flipped.
        #   (b) `is_legacy_account = TRUE` — explicit canonical flag.
        #   (c) `migration_completed = FALSE` — user has not set a fresh
        #       JAPAP 4.0 password yet.
        # If ANY of (a)/(b)/(c) is false, fall through to the normal
        # password-verification path. This makes it IMPOSSIBLE for a
        # post-launch user to receive the migration prompt.
        if user:
            has_legacy_id = user.get("legacy_id") is not None
            is_legacy_flag = bool(user.get("is_legacy_account"))
            migration_completed = (bool(user["migration_completed"])
                                   if "migration_completed" in user.keys() and user["migration_completed"] is not None
                                   else True)
            needs_migration_reset = has_legacy_id and is_legacy_flag and not migration_completed
            if needs_migration_reset:
                # Migrated legacy account — force password reset. Do NOT
                # attempt to verify the placeholder hash, and do NOT record
                # a brute-force attempt (this is an expected flow).
                # iter152 — return via JSONResponse (not HTTPException) so
                # the humanity cookie set just above survives the response
                # path. HTTPException wraps the request in a fresh
                # Response object that drops our `Set-Cookie` headers,
                # which is why the migration-banner CTA was hitting
                # "Captcha requis" on the next /forgot-password call.
                from fastapi.responses import JSONResponse
                logger.info(
                    "auth.migration_reset_required user=%s legacy_id=%s is_legacy=%s mig_completed=%s",
                    user.get("user_id"), user.get("legacy_id"),
                    is_legacy_flag, migration_completed,
                )
                resp = JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            "MIGRATION_RESET_REQUIRED:Votre compte a été migré vers JAPAP 4.0. "
                            "Veuillez définir un nouveau mot de passe."
                        ),
                    },
                )
                # Copy over the Set-Cookie headers our shared `response`
                # accumulated (humanity cookie + any other auth-flow
                # cookies). Going through the Response object's raw
                # headers list keeps the SameSite/Secure flags intact.
                for h_name, h_value in response.raw_headers:
                    if h_name.lower() == b"set-cookie":
                        resp.raw_headers.append((h_name, h_value))
                return resp
        if not user or not user['password_hash']:
            # Record failed attempt
            if attempt:
                new_attempts = attempt['attempts'] + 1
                locked = datetime.now(timezone.utc) + timedelta(minutes=15) if new_attempts >= 5 else None
                await conn.execute("UPDATE login_attempts SET attempts = $1, last_attempt = $2, locked_until = $3 WHERE identifier = $4",
                                   new_attempts, datetime.now(timezone.utc), locked, identifier)
            else:
                await conn.execute("INSERT INTO login_attempts (identifier, attempts, last_attempt) VALUES ($1, 1, $2)",
                                   identifier, datetime.now(timezone.utc))
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        # Legacy password rehashing: try bcrypt first, then try legacy formats
        # (restricted — legacy WoWonder migration hashes).
        password_valid = False
        needs_rehash = False
        try:
            password_valid = verify_password(req.password, user['password_hash'])
        except Exception:
            pass

        # SECURITY (iter82 audit) — unsalted MD5/SHA1/SHA256 hashes are
        # only accepted when the user is flagged as a legacy migrant.
        # Otherwise any DB-leak of bcrypt hashes would be bypassable by an
        # attacker simply pre-computing these fallbacks. Once authenticated,
        # the hash is rehashed to bcrypt and the flag is cleared.
        # iter141ter — accept both `migration_pending` (legacy) AND
        # `is_legacy_account` so the new flag system remains the source
        # of truth.
        is_legacy_user = (bool(user.get("is_legacy_account"))
                          or bool(user.get("migration_pending"))
                          or bool(user.get("legacy_id")))
        if not password_valid and is_legacy_user:
            import hashlib
            legacy_hashes = {
                hashlib.md5(req.password.encode()).hexdigest(),
                hashlib.sha1(req.password.encode()).hexdigest(),
                hashlib.sha256(req.password.encode()).hexdigest(),
            }
            if user['password_hash'] in legacy_hashes:
                password_valid = True
                needs_rehash = True
        
        if not password_valid:
            if attempt:
                new_attempts = attempt['attempts'] + 1
                locked = datetime.now(timezone.utc) + timedelta(minutes=15) if new_attempts >= 5 else None
                await conn.execute("UPDATE login_attempts SET attempts = $1, last_attempt = $2, locked_until = $3 WHERE identifier = $4",
                                   new_attempts, datetime.now(timezone.utc), locked, identifier)
            else:
                await conn.execute("INSERT INTO login_attempts (identifier, attempts, last_attempt) VALUES ($1, 1, $2)",
                                   identifier, datetime.now(timezone.utc))
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        if not user['is_active']:
            raise HTTPException(status_code=403, detail="Account is deactivated")
        
        # Rehash legacy password to bcrypt
        if needs_rehash:
            new_hash = hash_password(req.password)
            await conn.execute("UPDATE users SET password_hash = $1 WHERE user_id = $2", new_hash, user['user_id'])
            logger.info(f"Legacy password rehashed for user {user['user_id']}")
        
        await conn.execute("DELETE FROM login_attempts WHERE identifier = $1", identifier)

        # Iter83 — Superadmin MUST clear a second factor (email OTP) before
        # tokens are ever issued. We keep password verification here, clear
        # the brute-force counter, but instead of minting JWTs we send a
        # 6-digit code and flip a flag the client must satisfy via
        # /api/auth/verify-2fa. No session side-effect happens yet.
        if user['role'] == 'superadmin':
            try:
                code = await _issue_otp(conn, user['email'], purpose="login_2fa")
            except HTTPException as exc:
                # If already throttled (429) surface it as-is so the client
                # waits the 60s cooldown rather than getting a generic error.
                raise exc
            await send_otp_email(
                user['email'], code,
                purpose="JAPAP — Superadmin login verification",
            )
            try:
                from services.security_service import log_security_event
                await log_security_event(
                    user['user_id'], "auth.superadmin_2fa_challenge",
                    severity="warning", ip=ip, ua=request.headers.get("user-agent", ""),
                )
            except Exception:
                pass
            return {
                "status": "otp_required",
                "email": user['email'],
                "message": "Un code de vérification a été envoyé à votre email.",
            }

        await conn.execute("UPDATE users SET last_seen = $1, is_online = TRUE WHERE user_id = $2", datetime.now(timezone.utc), user['user_id'])

        # iter146 — Trusted Device flow.
        # Bump the per-(user, device) counter BEFORE issuing tokens so we can
        # decide between a 7-day or 90-day refresh cookie.
        ua_for_trust = request.headers.get("user-agent", "") if request else ""
        td_info = {"is_trusted": False, "newly_trusted": False,
                   "fingerprint": None, "successful_logins_count": 1}
        try:
            from services.trusted_device_service import (
                record_successful_login, TRUSTED_REFRESH_TTL_DAYS,
                DEFAULT_REFRESH_TTL_DAYS,
            )
            td_info = await record_successful_login(user['user_id'], ip, ua_for_trust)
        except Exception as _e:
            logger.warning(f"trusted_device record failed: {_e}")
            TRUSTED_REFRESH_TTL_DAYS = 90
            DEFAULT_REFRESH_TTL_DAYS = 7
        refresh_ttl_days = (TRUSTED_REFRESH_TTL_DAYS
                            if td_info.get("is_trusted")
                            else DEFAULT_REFRESH_TTL_DAYS)

        access_token = create_access_token(user['user_id'], user['email'])
        refresh_token, rt_jti = create_refresh_token(user['user_id'],
                                                     ttl_days=refresh_ttl_days)
        # iter237 — persist=False issues session cookies if the user did NOT
        # tick "Se souvenir de moi". Closing the browser then logs them out.
        set_auth_cookies(response, access_token, refresh_token,
                         refresh_ttl_days=refresh_ttl_days,
                         persist=bool(req.remember_me))

        await conn.execute("""
            INSERT INTO audit_logs (user_id, action, resource, details, ip_address)
            VALUES ($1, 'login', 'auth', '{}', $2)
        """, user['user_id'], ip)

        # iter82 — session tracking + new-device detection
        try:
            from services.security_service import (
                upsert_active_session, detect_new_device, log_security_event,
            )
            ua = request.headers.get("user-agent", "") if request else ""
            is_new, fp = await detect_new_device(user['user_id'], ip, ua)
            await upsert_active_session(user['user_id'], rt_jti, ip, ua)
            await log_security_event(
                user['user_id'],
                "auth.login_new_device" if is_new else "auth.login",
                severity="warning" if is_new else "info",
                ip=ip, ua=ua, details={"fingerprint": fp},
            )
            if is_new:
                # Best-effort push/email notification — never blocks login.
                try:
                    from services.push_service import send_push_to_user
                    await send_push_to_user(
                        user['user_id'],
                        title="Nouvelle connexion JAPAP",
                        body=f"Une connexion a été détectée depuis une nouvelle adresse IP ({ip}). Si ce n'est pas vous, changez votre mot de passe immédiatement.",
                        data={"type": "security_new_device"},
                    )
                except Exception:
                    pass
        except Exception as _e:
            logger.warning(f"session tracking (login) failed: {_e}")

        return {
            "user": user_to_response(dict(user)),
            "access_token": access_token,
            # iter146 — surface the trusted-device state to the frontend so it
            # can display a "Cet appareil est désormais reconnu" toast on the
            # transition login.
            "device": {
                "is_trusted": bool(td_info.get("is_trusted")),
                "newly_trusted": bool(td_info.get("newly_trusted")),
                "successful_logins_count": int(td_info.get("successful_logins_count", 1)),
                "refresh_ttl_days": int(refresh_ttl_days),
            },
        }


@router.post("/logout")
async def logout(request: Request, response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    response.delete_cookie("session_token", path="/")
    
    try:
        user = await get_current_user(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET is_online = FALSE WHERE user_id = $1", user['user_id'])
            await conn.execute("DELETE FROM user_sessions WHERE user_id = $1", user['user_id'])
    except Exception:
        pass
    
    return {"message": "Logged out"}


@router.get("/me")
async def get_me(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        wallet = await conn.fetchrow("SELECT balance, currency FROM wallets WHERE user_id = $1", user['user_id'])
        onboarding = await conn.fetchval(
            "SELECT onboarding_completed_at FROM users WHERE user_id = $1", user['user_id']
        )
        # iter173 — surface KYC-approved flag so the UI can render the
        # "✅ Identité vérifiée" trust badge on the user's own profile too.
        kyc_verified = bool(await conn.fetchval(
            "SELECT 1 FROM kyc_verifications WHERE user_id = $1 "
            "AND status = 'approved' LIMIT 1", user['user_id']))
    resp = user_to_response(user)
    resp['kyc_verified'] = kyc_verified
    if wallet:
        resp['wallet_balance'] = str(wallet['balance'])
        resp['wallet_currency'] = wallet['currency']
    resp['onboarding_completed'] = bool(onboarding)
    return resp


@router.post("/onboarding/complete")
async def complete_onboarding(request: Request):
    """Marks the user as having finished onboarding (skipped or completed).
    Used to prevent the modal from re-appearing on subsequent logins."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET onboarding_completed_at = NOW() WHERE user_id = $1",
            user['user_id'],
        )
    return {"onboarding_completed": True}


@router.put("/preferences")
async def update_preferences(req: UpdatePreferencesRequest, request: Request):
    """Update user-level preferences.
    - preferred_lang: 2-letter ISO, controls BOTH UI language and auto-translation.
                      Pass '' (empty) to disable auto-translation.
    - preferred_currency: 3-letter ISO-4217, controls wallet display currency.
                          Pass '' to fall back to USD.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if req.preferred_lang is not None:
            lang = (req.preferred_lang or '').lower().strip()
            if lang and lang not in _SUPPORTED_LANGS:
                raise HTTPException(status_code=400, detail=f"Langue non supportée: {lang}")
            await conn.execute(
                "UPDATE users SET preferred_lang = $1, updated_at = $2 WHERE user_id = $3",
                lang or None, datetime.now(timezone.utc), user['user_id'])
        if req.preferred_currency is not None:
            from routes.currency import CURRENCY_SYMBOLS, FALLBACK_RATES
            ccy = (req.preferred_currency or '').upper().strip()
            if ccy and ccy not in CURRENCY_SYMBOLS and ccy not in FALLBACK_RATES:
                raise HTTPException(status_code=400, detail=f"Devise non supportée: {ccy}")
            await conn.execute(
                "UPDATE users SET preferred_currency = $1, updated_at = $2 WHERE user_id = $3",
                ccy or None, datetime.now(timezone.utc), user['user_id'])
    return {
        "preferred_lang": req.preferred_lang if req.preferred_lang else None,
        "preferred_currency": req.preferred_currency if req.preferred_currency else None,
        "status": "ok",
    }


@router.post("/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        old_jti = payload.get("jti")
        user_id = payload["sub"]

        # iter82 — reject reuse of revoked / already-rotated tokens. This is
        # the replay-detection signal: if someone presents an old JTI, it
        # likely means the refresh cookie was leaked. We revoke ALL the
        # user's active sessions and force a clean re-login.
        from services.security_service import (
            is_jti_revoked, revoke_jti, rotate_session_jti, new_jti as _new_jti,
            log_security_event, revoke_all_user_jtis,
        )
        if old_jti and await is_jti_revoked(old_jti):
            await log_security_event(
                user_id, "auth.refresh_replay_detected",
                severity="critical",
                ip=_client_ip(request),
                ua=request.headers.get("user-agent", ""),
                details={"jti": old_jti},
            )
            await revoke_all_user_jtis(user_id, reason="replay_detected")
            raise HTTPException(status_code=401, detail="Session révoquée (replay détecté)")

        pool = await get_pool()
        async with pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not user:
                raise HTTPException(status_code=401, detail="User not found")

        # Rotate — revoke the old JTI, mint a new refresh token with a new JTI
        if old_jti:
            await revoke_jti(old_jti, user_id, reason="rotated")

        # iter146 — extend trusted-device long-lived sessions on rotation.
        try:
            from services.trusted_device_service import get_refresh_ttl_days
            ip_for_trust = _client_ip(request)
            ua_for_trust = request.headers.get("user-agent", "")
            refresh_ttl_days = await get_refresh_ttl_days(user_id, ip_for_trust, ua_for_trust)
        except Exception:
            refresh_ttl_days = 7

        new_refresh, new_jti = create_refresh_token(user_id, ttl_days=refresh_ttl_days)
        if old_jti:
            await rotate_session_jti(old_jti, new_jti)

        access_token = create_access_token(user['user_id'], user['email'])
        sec = _cookie_secure()
        ss = _cookie_samesite()
        response.set_cookie(key="access_token", value=access_token, httponly=True,
                            secure=sec, samesite=ss, max_age=28800, path="/")
        response.set_cookie(key="refresh_token", value=new_refresh, httponly=True,
                            secure=sec, samesite=ss,
                            max_age=int(refresh_ttl_days * 86400), path="/")
        return {"access_token": access_token}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest, request: Request, response: Response):
    # iter141ter — Math captcha (replaces Turnstile).
    verify_captcha(req.captcha_id, req.captcha_answer, request)
    # iter141quater — set humanity cookie so subsequent auth flows skip captcha.
    issue_human_cookie(response)

    # iter154 — Hard email-validation gate. Refuse anything that's:
    #   • not a syntactically valid address (RFC-5322-lite)
    #   • from a disposable / throwaway provider
    #   • a known hard-bounce / spam-complaint address
    # This protects the domain reputation against another wave of
    # bounces and is the key pillar of the on-demand-only policy.
    from utils.email_validation import gating_reason
    addr = (req.email or "").strip().lower()

    # iter152 — anti-spam cooldown on a per-(email,IP) basis. Prevents the
    # same client hammering /forgot-password while the user is already
    # waiting for an email. 30s window matches the frontend cooldown.
    pool = await get_pool()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # iter154 — gate must be checked AFTER pool is acquired (needs
        # the connection to query email_logs).
        reason = await gating_reason(conn, addr)
        if reason == "invalid_format":
            return {
                "message": "Adresse email invalide. Vérifie l'orthographe.",
                "delivery": {"status": "invalid", "message_id": "", "ok": False,
                             "reason": "invalid_format"},
                "cooldown_seconds": 0,
            }
        if reason == "disposable_domain":
            return {
                "message": ("Ce domaine email n'est pas accepté pour des "
                            "raisons de sécurité. Utilise une adresse "
                            "personnelle (Gmail, Outlook, Yahoo…)."),
                "delivery": {"status": "blocked", "message_id": "", "ok": False,
                             "reason": "disposable_domain"},
                "cooldown_seconds": 0,
            }
        if reason == "hard_bounced":
            return {
                "message": ("Cette adresse n'a pas pu être délivrée par le passé. "
                            "Vérifie qu'elle est correcte ou contacte le support."),
                "delivery": {"status": "blocked", "message_id": "", "ok": False,
                             "reason": "hard_bounced"},
                "cooldown_seconds": 0,
            }

        # Cleanup expired tokens once per minute (cheap).
        await conn.execute(
            """DELETE FROM password_reset_tokens
                WHERE expires_at < NOW() - INTERVAL '7 days'""",
        )
        user = await conn.fetchrow("SELECT user_id, email FROM users WHERE email = $1", addr)
        # Anti-enumeration: same shape of response whether or not the user
        # exists. Deliverability is reported in `delivery` for the legit
        # caller; an attacker can't tell from the response if the email
        # was a real account or not.
        if not user:
            return {
                "message": "Si cet email existe, un lien de réinitialisation a été envoyé.",
                "delivery": {"status": "queued", "message_id": "", "ok": True},
                "cooldown_seconds": 30,
            }

        # Check cooldown — 30s between same email's reset emails. Returns
        # the *truthful* "already_sent_recently" so the UI can show "Lien
        # déjà envoyé, vérifie ta boîte" instead of re-sending.
        recent = await conn.fetchrow(
            """SELECT created_at FROM password_reset_tokens
                WHERE user_id = $1 AND created_at > NOW() - INTERVAL '30 seconds'
                ORDER BY created_at DESC LIMIT 1""",
            user["user_id"],
        )
        if recent:
            elapsed = (now - recent["created_at"].replace(tzinfo=timezone.utc)).total_seconds()
            wait = max(1, int(30 - elapsed))
            return {
                "message": f"Un lien a déjà été envoyé. Réessaie dans {wait}s ou vérifie tes spams.",
                "delivery": {"status": "cooldown", "message_id": "", "ok": True},
                "cooldown_seconds": wait,
            }

        token = secrets.token_urlsafe(32)
        expires = now + timedelta(hours=1)
        await conn.execute("""
            INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES ($1, $2, $3)
        """, user['user_id'], token, expires)

    # Build reset URL from request origin, fall back to FRONTEND_URL env var
    frontend = os.environ.get("FRONTEND_URL") or str(request.base_url).rstrip("/").replace("/api", "")
    reset_url = f"{frontend}/reset-password?token={token}"

    # iter152 — use the *_detailed variant so we know whether Resend
    # actually accepted the email. Surface the real status to the caller.
    delivery = await send_password_reset_email_detailed(addr, reset_url)
    if delivery.get("ok"):
        logger.info(
            "auth.password_reset.sent email=%s message_id=%s",
            addr, delivery.get("message_id"),
        )
    else:
        logger.warning(
            "auth.password_reset.failed email=%s status=%s error=%s",
            addr, delivery.get("status_code"), delivery.get("error"),
        )
    return {
        "message": "Si cet email existe, un lien de réinitialisation a été envoyé.",
        "delivery": {
            "status": "sent" if delivery.get("ok") else "failed",
            "message_id": delivery.get("message_id", ""),
            "ok": bool(delivery.get("ok")),
            # iter154 — when the provider explicitly rejected (e.g.
            # 4xx), surface a stable reason code so the UI can offer
            # "corriger l'email" instead of telling the user to wait.
            "reason": "" if delivery.get("ok") else "provider_rejected",
        },
        "cooldown_seconds": 30,
    }


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, request: Request):
    # iter141ter — Math captcha (anti-bot on the reset flow). Optional —
    # if not provided we still let through (the secure password-reset
    # token from the email is the primary guard) but we encourage
    # frontends to send it for defence in depth.
    if req.captcha_id or req.captcha_answer:
        verify_captcha(req.captcha_id, req.captcha_answer, request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        reset = await conn.fetchrow(
            "SELECT * FROM password_reset_tokens WHERE token = $1 AND used = FALSE", req.token)
        if not reset:
            raise HTTPException(status_code=400, detail="Invalid or expired token")
        if reset['expires_at'].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Token expired")
        
        hashed = hash_password(req.new_password)
        # iter141ter — Mark migration as completed (both flags) so the
        # account moves out of the migration funnel atomically.
        await conn.execute(
            "UPDATE users SET password_hash = $1, password_changed_at = $2, "
            "updated_at = $2, migration_pending = FALSE, migration_completed = TRUE "
            "WHERE user_id = $3",
            hashed, datetime.now(timezone.utc), reset['user_id'])
        await conn.execute("UPDATE password_reset_tokens SET used = TRUE WHERE token = $1", req.token)

        # iter146 — security: a successful password reset MUST drop trusted
        # devices so the next login starts fresh and the threat actor
        # holding an old refresh cookie is kicked out.
        try:
            from services.trusted_device_service import untrust_all
            await untrust_all(reset['user_id'])
        except Exception as _e:
            logger.warning(f"trusted_device.untrust_all failed after reset: {_e}")
        try:
            from services.security_service import revoke_all_user_jtis
            await revoke_all_user_jtis(reset['user_id'], reason="password_reset")
        except Exception as _e:
            logger.warning(f"revoke_all_user_jtis failed after reset: {_e}")

        return {"message": "Password reset successful"}


# REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
@router.post("/google/session")
async def google_session(req: SessionRequest, response: Response):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
            headers={"X-Session-ID": req.session_id}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid session")
        data = resp.json()
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT * FROM users WHERE email = $1", data['email'].lower())
        
        if existing:
            user_id = existing['user_id']
            await conn.execute("""
                UPDATE users SET google_id = $1, avatar = COALESCE(NULLIF($2, ''), avatar),
                first_name = COALESCE(NULLIF($3, ''), first_name), social_login = TRUE, 
                is_online = TRUE, last_seen = $4, updated_at = $4 WHERE user_id = $5
            """, data.get('id', ''), data.get('picture', ''), data.get('name', ''),
               datetime.now(timezone.utc), user_id)
        else:
            user_id = f"user_{uuid.uuid4().hex[:12]}"
            name_parts = data.get('name', '').split(' ', 1)
            first_name = name_parts[0] if name_parts else ''
            last_name = name_parts[1] if len(name_parts) > 1 else ''
            username = data['email'].lower().split("@")[0] + uuid.uuid4().hex[:4]
            
            await conn.execute("""
                INSERT INTO users (user_id, username, email, first_name, last_name, avatar, google_id, social_login, role, is_active, is_online, last_seen)
                VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, 'user', TRUE, TRUE, $8)
            """, user_id, username, data['email'].lower(), first_name, last_name,
               data.get('picture', ''), data.get('id', ''), datetime.now(timezone.utc))
            
            await conn.execute("INSERT INTO wallets (user_id, balance) VALUES ($1, 0.00)", user_id)
        
        session_token = data.get('session_token', secrets.token_urlsafe(32))
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        await conn.execute("""
            INSERT INTO user_sessions (user_id, session_token, expires_at)
            VALUES ($1, $2, $3)
        """, user_id, session_token, expires)
        
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        
        access_token = create_access_token(user_id, user['email'])
        refresh_tok, rt_jti = create_refresh_token(user_id)
        set_auth_cookies(response, access_token, refresh_tok)
        response.set_cookie(key="session_token", value=session_token, httponly=True, secure=True, samesite="none", max_age=604800, path="/")
        # iter82 — session tracking for Google-login users
        try:
            from services.security_service import upsert_active_session
            await upsert_active_session(user_id, rt_jti, "", "")
        except Exception:
            pass

        return {"user": user_to_response(dict(user)), "access_token": access_token}


async def seed_admin():
    pool = await get_pool()
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@japap.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "JapapAdmin2024!")
    
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT * FROM users WHERE email = $1", admin_email)
        if not existing:
            user_id = f"admin_{uuid.uuid4().hex[:12]}"
            hashed = hash_password(admin_password)
            await conn.execute("""
                INSERT INTO users (user_id, username, email, password_hash, first_name, last_name,
                                   role, is_active, is_verified, email_verified, terms_accepted, terms_accepted_at)
                VALUES ($1, 'admin', $2, $3, 'Admin', 'JAPAP', 'admin', TRUE, TRUE, TRUE, TRUE, NOW())
            """, user_id, admin_email, hashed)
            await conn.execute("INSERT INTO wallets (user_id, balance) VALUES ($1, 0.00)", user_id)
            logger.info(f"Admin seeded: {admin_email}")
        else:
            # Idempotent: force admin to be fully active + verified every boot so that
            # the register-upsert branch can never take over the account via the email.
            updates = []
            _admin_pw_ok = False
            try:
                _admin_pw_ok = verify_password(admin_password, existing['password_hash'] or '')
            except Exception:
                _admin_pw_ok = False
            if not _admin_pw_ok:
                hashed = hash_password(admin_password)
                await conn.execute("UPDATE users SET password_hash = $1 WHERE email = $2", hashed, admin_email)
                updates.append("password")
            await conn.execute("""
                UPDATE users SET email_verified = TRUE, is_active = TRUE, is_verified = TRUE,
                                 terms_accepted = TRUE,
                                 terms_accepted_at = COALESCE(terms_accepted_at, NOW())
                WHERE email = $1
            """, admin_email)
            if updates:
                logger.info(f"Admin {admin_email} updated: {updates}")


# ── Iter83 — Superadmin seed + admin audit log helper ───────────────────────
async def seed_superadmin():
    """Idempotent seed for the platform-level superadmin. Elevates the
    existing account if its role is anything other than 'superadmin', and
    refreshes the password when .env changes. Always flags the account as
    email-verified + active to stop the register/upsert branch from ever
    claiming it."""
    pool = await get_pool()
    sa_email = os.environ.get("SUPERADMIN_EMAIL", "emileparfait2003@gmail.com").lower().strip()
    sa_password = os.environ.get("SUPERADMIN_PASSWORD", "Gerard0103@")

    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT * FROM users WHERE email = $1", sa_email)
        if not existing:
            user_id = f"superadmin_{uuid.uuid4().hex[:10]}"
            hashed = hash_password(sa_password)
            await conn.execute("""
                INSERT INTO users (user_id, username, email, password_hash, first_name, last_name,
                                   role, is_active, is_verified, email_verified,
                                   terms_accepted, terms_accepted_at, admin_sub_roles)
                VALUES ($1, $2, $3, $4, 'Super', 'Admin', 'superadmin',
                        TRUE, TRUE, TRUE, TRUE, NOW(), '[]'::jsonb)
            """, user_id, f"superadmin_{uuid.uuid4().hex[:6]}", sa_email, hashed)
            await conn.execute("INSERT INTO wallets (user_id, balance) VALUES ($1, 0.00) ON CONFLICT DO NOTHING", user_id)
            logger.warning(f"SUPERADMIN seeded: {sa_email}")
        else:
            await conn.execute("""
                UPDATE users SET role = 'superadmin', is_active = TRUE, is_verified = TRUE,
                                 email_verified = TRUE, terms_accepted = TRUE,
                                 terms_accepted_at = COALESCE(terms_accepted_at, NOW()),
                                 migration_pending = FALSE, migration_completed = TRUE
                WHERE email = $1
            """, sa_email)
            # Tolerate pre-existing non-bcrypt hashes: bcrypt.checkpw raises
            # ValueError on legacy/empty hashes, so we treat any exception as
            # "needs rehash" rather than crashing startup.
            needs_new_hash = True
            try:
                if verify_password(sa_password, existing['password_hash'] or ''):
                    needs_new_hash = False
            except Exception:
                needs_new_hash = True
            if needs_new_hash:
                hashed = hash_password(sa_password)
                await conn.execute("UPDATE users SET password_hash = $1, password_changed_at = NOW() WHERE email = $2", hashed, sa_email)
                logger.warning(f"SUPERADMIN {sa_email} password rotated from env")


async def log_admin_action(*, actor_id: str, actor_email: str, action: str,
                           target_id: Optional[str] = None,
                           target_email: Optional[str] = None,
                           metadata: Optional[dict] = None,
                           ip: str = "", ua: str = "") -> None:
    """Append a row to admin_audit_log. Best-effort: never raises so callers
    can safely wrap critical paths."""
    try:
        import json as _json
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO admin_audit_log (actor_id, actor_email, action,
                                             target_id, target_email, metadata,
                                             ip_address, user_agent)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
            """, actor_id, actor_email, action, target_id, target_email,
               _json.dumps(metadata or {}), ip[:64], (ua or "")[:500])
    except Exception as e:
        logger.warning(f"admin_audit_log insert failed: {e}")



# ── iter146 — Trusted Devices user-facing endpoints ──────────────────────
@router.get("/devices")
async def list_devices(request: Request):
    """Return the caller's trusted-device history (current device flagged)."""
    user = await get_current_user(request)
    from services.trusted_device_service import list_trusted_devices
    from services.security_service import device_fingerprint
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
    current_fp = device_fingerprint(ip, ua)
    devices = await list_trusted_devices(user["user_id"])
    for d in devices:
        d["is_current"] = (d["fingerprint"] == current_fp)
    return {"devices": devices, "current_fingerprint": current_fp}


class UntrustDeviceRequest(BaseModel):
    fingerprint: str


@router.post("/devices/untrust")
async def untrust_device_endpoint(req: UntrustDeviceRequest, request: Request):
    """Remove trust from a specific device (the user's "this isn't me" CTA).
    Does NOT revoke the existing refresh token — caller can subsequently call
    `/api/auth/logout` if they want to kill the session."""
    user = await get_current_user(request)
    from services.trusted_device_service import untrust_device
    ok = await untrust_device(user["user_id"], (req.fingerprint or "").strip())
    return {"untrusted": bool(ok)}
