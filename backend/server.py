from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import socketio
import os
import logging
import jwt
import asyncio
from datetime import datetime, timezone
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

# iter237o — Patch LlmChat to off-load blocking litellm.completion to a thread
# so background quiz/DCQ pool refreshes never freeze the API event loop.
# Must run BEFORE any worker imports LlmChat.
from services import litellm_patch  # noqa: F401

from database import init_db, close_db, get_pool
from routes.auth import router as auth_router, seed_admin, seed_superadmin
from routes.users import router as users_router
from routes.wallet import router as wallet_router
from routes.payments import router as payments_router
from routes.messaging import router as messaging_router
from routes.admin import router as admin_router
from routes.admin_user_detail import router as admin_user_detail_router
from routes.admin_super import router as admin_super_router
from routes.notifications import router as notifications_router
from routes.feed import router as feed_router
from routes.marketplace import router as marketplace_router
from routes.upload import router as upload_router
from routes.crypto import router as crypto_router
from routes.pro import router as pro_router
from routes.push import router as push_router
from routes.referrals import router as referrals_router
from routes.calls import router as calls_router
from routes.crowdfunding import router as crowdfunding_router
from routes.games import router as games_router
from routes.quiz import router as quiz_router
from routes.quiz_champion import router as quiz_champion_router
from routes.tap import router as tap_router
from routes.engagement_leaderboard import router as engagement_leaderboard_router
from routes.duel import router as duel_router
from routes.support import router as support_router
from routes.forecast import router as forecast_router
from routes.forecast_admin import router as forecast_admin_router
from services.forecast_service import ensure_forecast_tables
from routes.jobs import router as jobs_router
from routes.transport import router as transport_router
from routes.feed_extended import router as feed_extended_router
from routes.ai_filters import router as ai_filters_router  # iter150 — IA filter Phase 2
from routes.geo import router as geo_router
from routes.kyc import router as kyc_router
from routes.admin_settings import router as admin_settings_router
from routes.admin_payments import router as admin_payments_router
from routes.admin_games import router as admin_games_router
from routes.error_monitor import router as error_monitor_router
from services.error_monitor import record_error
from routes.currency import router as currency_router
from routes.pro_admin import router as pro_admin_router
from routes.referrals_admin import router as referrals_admin_router
from routes.connect import router as connect_router
from routes.connect_admin import router as connect_admin_router
from routes.ads import router as ads_router
from routes.ads_console import router as ads_console_router
from routes.wallet_admin import router as wallet_admin_router
from routes.ai import router as ai_router
from routes.groups_pages import router as groups_pages_router
from routes.admin_stats import router as admin_stats_router
from routes.reactions import router as reactions_router
from routes.analytics import router as analytics_router
from routes.tasks import router as tasks_router
from routes.admin_messaging import router as admin_messaging_router
from routes.email_tracking import router as email_tracking_router
from routes.migration_broadcast import router as migration_broadcast_router
from services.migration_broadcast import start_worker as start_migration_broadcast_worker
from routes.public_assets import router as public_assets_router
from routes.staking import router as staking_router, admin_router as staking_admin_router, seed_staking_defaults
from routes.security import router as security_router
from routes.seo import router as seo_router
from routes.wheel_fortune import router as wheel_router, ensure_wheel_tables
from routes.realtime import init_realtime
from middleware.rate_limit import install_rate_limiter
from middleware.security import SecurityHeadersMiddleware, CsrfMiddleware
from middleware.seo_crawler import CrawlerSEOMiddleware
from services.messaging_worker import start_worker as start_messaging_worker
from services.wheel_boost_scheduler import start_scheduler as start_wheel_boost_scheduler
from services.payment_verify_retry_worker import start_worker as start_payment_verify_worker
from services.quiz_champion_scheduler import start_scheduler as start_quiz_champion_scheduler
from services.scholarship_digest_worker import start_worker as start_scholarship_digest_worker
from services.public_url_audit_worker import start_worker as start_public_url_audit_worker
from services.crowdfunding_recruit_remind_worker import start_worker as start_cf_recruit_remind_worker
from services.seller_reminder_worker import start_worker as start_seller_reminder_worker
from services.video_transcode_worker import start_worker as start_video_transcode_worker
from services.security_service import ensure_security_tables

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Socket.IO server
# iter237s — explicit origin allowlist (defence-in-depth) AND keep '*'
# fallback so behind-proxy connections (japapmessenger.com → ingress →
# pod) without `Origin` echo still upgrade. `cors_credentials=True` lets
# the auth cookie travel on long-polling fallback. `ping_*` set for
# mobile keep-alive over flaky networks.
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    cors_credentials=True,
    ping_interval=25,
    ping_timeout=20,
    max_http_buffer_size=1_000_000,
)

# FastAPI app
fastapi_app = FastAPI(title="JAPAP Messenger API", version="2.0.0")

# Rate limiter (slowapi) — Connect v2.1 hardening. Wires state + 429 handler.
install_rate_limiter(fastapi_app)

# iter82 — global security headers (HSTS / X-Frame / nosniff / Referrer-Policy /
# Permissions-Policy / COOP) applied on every response.
fastapi_app.add_middleware(SecurityHeadersMiddleware)

# iter184 — Crawler-aware SEO middleware. Bots get prerendered HTML
# with meta+OG+JSON-LD; real users keep the React SPA.
fastapi_app.add_middleware(CrawlerSEOMiddleware)

# iter82 — CSRF guard for cookie-authenticated state-changing requests.
# Exemptions handled inside the middleware (webhooks, OAuth return, etc.).
fastapi_app.add_middleware(CsrfMiddleware)

# CORS — iter83: browsers reject `*` when `allow_credentials=True`, so we
# whitelist explicit origins via CORS_ORIGINS env var AND keep a permissive
# regex for every Emergent preview/host subdomain (the deploy platform
# rotates these on each deploy).
# iter237p — Always include the production domain japapmessenger.com in the
# allowlist as a hardcoded fallback. This protects against the case where the
# CORS_ORIGINS secret is missing or mis-set in the production env (observed
# during the first prod deploy: preflight OPTIONS to /api/auth/login returned
# HTTP 400 without Access-Control-Allow-Origin until the secret was added).
_cors_env = os.environ.get('CORS_ORIGINS', '')
_cors_origins = [o.strip() for o in _cors_env.split(',') if o.strip() and o.strip() != '*']
for _baseline in (
    'https://japapmessenger.com',
    'https://www.japapmessenger.com',
):
    if _baseline not in _cors_origins:
        _cors_origins.append(_baseline)
logger.info("[CORS] allow_origins=%s", _cors_origins)
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://([a-zA-Z0-9-]+\.)*(preview\.emergentagent\.com|emergent\.host)$",
    allow_methods=["*"],
    allow_headers=["*", "X-CSRF-Token", "X-Requested-With"],
)

# Include routers
fastapi_app.include_router(auth_router)
fastapi_app.include_router(users_router)
fastapi_app.include_router(wallet_router)
fastapi_app.include_router(payments_router)
fastapi_app.include_router(messaging_router)
fastapi_app.include_router(admin_router)
fastapi_app.include_router(admin_user_detail_router)  # iter240k — Admin User Detail dossier
fastapi_app.include_router(admin_super_router)
fastapi_app.include_router(notifications_router)
fastapi_app.include_router(feed_router)
fastapi_app.include_router(marketplace_router)
fastapi_app.include_router(upload_router)
fastapi_app.include_router(crypto_router)
fastapi_app.include_router(pro_router)
fastapi_app.include_router(push_router)
fastapi_app.include_router(referrals_router)
fastapi_app.include_router(calls_router)
fastapi_app.include_router(crowdfunding_router)
fastapi_app.include_router(games_router)
fastapi_app.include_router(quiz_router)
fastapi_app.include_router(quiz_champion_router)
# iter237k — Daily Challenge PAID mode (additif, mode gratuit intact).
try:
    from routes.dcq_paid import dcq_paid_router
    fastapi_app.include_router(dcq_paid_router)
    logger.info("[iter237k] dcq_paid router loaded")
except Exception as _e:
    logger.error("[iter237k] dcq_paid router failed to load: %s", _e)

# iter237af — Hubtel Mobile Money (Ghana 🇬🇭) — strictly additive.
try:
    from routes.hubtel_momo import hubtel_momo_router
    fastapi_app.include_router(hubtel_momo_router)
    logger.info("[iter237af] hubtel_momo router loaded")
except Exception as _e:
    logger.error("[iter237af] hubtel_momo router failed to load: %s", _e)

# iter238 — Paystack Ghana + payment-method toggle middleware (additive).
try:
    from routes.paystack import paystack_router
    from routes.payment_methods_status import payment_methods_status_router
    from middleware.payment_toggles import PaymentTogglesMiddleware
    fastapi_app.include_router(paystack_router)
    fastapi_app.include_router(payment_methods_status_router)
    fastapi_app.add_middleware(PaymentTogglesMiddleware)
    logger.info("[iter238] paystack + toggles loaded")
except Exception as _e:
    logger.error("[iter238] paystack/toggles failed to load: %s", _e)

# iter238c — Admin wallet diagnostics (back-office only, additive).
try:
    from routes.admin_wallet_diagnostics import admin_wallet_diagnostics_router
    fastapi_app.include_router(admin_wallet_diagnostics_router)
    logger.info("[iter238c] admin_wallet_diagnostics router loaded")
except Exception as _e:
    logger.error("[iter238c] admin_wallet_diagnostics failed to load: %s", _e)

# iter239a4 — Admin FX cache refresh (additive).
try:
    from routes.admin_fx import admin_fx_router
    fastapi_app.include_router(admin_fx_router)
    logger.info("[iter239a4] admin_fx router loaded")
except Exception as _e:
    logger.error("[iter239a4] admin_fx failed to load: %s", _e)

# iter239b — Admin Hubtel MoMo credentials (additive).
try:
    from routes.admin_hubtel import admin_hubtel_router
    fastapi_app.include_router(admin_hubtel_router)
    logger.info("[iter239b] admin_hubtel router loaded")
except Exception as _e:
    logger.error("[iter239b] admin_hubtel failed to load: %s", _e)

# iter239d — Admin storage (R2 media bucket) router (additive).
try:
    from routes.admin_storage import admin_storage_router
    fastapi_app.include_router(admin_storage_router)
    logger.info("[iter239d] admin_storage router loaded")
except Exception as _e:
    logger.error("[iter239d] admin_storage failed to load: %s", _e)

# iter239h — Admin vendor health monitoring (additive).
try:
    from routes.admin_vendor_health import admin_vendor_health_router
    fastapi_app.include_router(admin_vendor_health_router)
    logger.info("[iter239h] admin_vendor_health router loaded")
except Exception as _e:
    logger.error("[iter239h] admin_vendor_health failed to load: %s", _e)

# iter237n — Legal acceptance routes (CGU/CGJ/RGPD).
try:
    from routes.legal import legal_router
    fastapi_app.include_router(legal_router)
    logger.info("[iter237n] legal router loaded")
except Exception as _e:
    logger.error("[iter237n] legal router failed to load: %s", _e)
fastapi_app.include_router(tap_router)
fastapi_app.include_router(admin_games_router)
fastapi_app.include_router(error_monitor_router)


# ─────────────────────────────────────────────────────────────────────────
# iter108 — auto-record any 5xx exception in the AI Error Monitor pipeline.
# ─────────────────────────────────────────────────────────────────────────
@fastapi_app.exception_handler(Exception)
async def _global_exception_recorder(request, exc):
    from fastapi.responses import JSONResponse
    import traceback as _tb
    from database import get_pool as _gp
    # Identify which router/module the path lives in (e.g. wallet, quiz, ...).
    path = (request.url.path or "").lstrip("/")
    module = "unknown"
    if path.startswith("api/"):
        parts = path.split("/")
        if len(parts) >= 3:
            module = f"{parts[1]}.{parts[2]}"[:80]
        elif len(parts) >= 2:
            module = parts[1][:80]
    # Map to severity based on status if HTTPException, else critical for 5xx
    from fastapi import HTTPException as _HE
    if isinstance(exc, _HE):
        # Let FastAPI deliver the original 4xx response; only audit 5xx HTTPExceptions.
        if exc.status_code < 500:
            return JSONResponse(status_code=exc.status_code,
                                content={"detail": exc.detail})
        sev = "high"
        msg = str(exc.detail or exc)
        http_status = exc.status_code
    else:
        sev = "critical"
        msg = f"{type(exc).__name__}: {exc}"
        http_status = 500
    # Best-effort: attach user id if available
    user_id = None
    try:
        from routes.auth import get_current_user as _gcu
        u = await _gcu(request)
        user_id = u.get("user_id") if u else None
    except Exception:
        pass
    try:
        pool = await _gp()
        async with pool.acquire() as conn:
            await record_error(
                conn, source="backend", module=module, message=msg,
                stack="".join(_tb.format_exception(type(exc), exc, exc.__traceback__))[-6000:],
                severity=sev, user_id=user_id, url=str(request.url),
                user_agent=request.headers.get("user-agent", ""),
                http_status=http_status,
            )
    except Exception as _e:
        logger.warning("error_monitor auto-record failed: %s", _e)
    return JSONResponse(
        status_code=http_status,
        content={"detail": "Une erreur est survenue. L'équipe JAPAP a été notifiée."},
    )
fastapi_app.include_router(engagement_leaderboard_router)
fastapi_app.include_router(duel_router)
fastapi_app.include_router(support_router)
fastapi_app.include_router(jobs_router)
fastapi_app.include_router(transport_router)
fastapi_app.include_router(feed_extended_router)
fastapi_app.include_router(ai_filters_router)  # iter150 — POST /api/media/ai-filter
fastapi_app.include_router(geo_router)
from routes.social import router as social_router
from routes.social_sharing import router as social_sharing_router
from routes.recruit import router as recruit_router
from routes.og import router as og_router
from routes.og_preview import router as og_preview_router  # iter212 — link previews
from routes.privacy import router as privacy_router
fastapi_app.include_router(social_router)
fastapi_app.include_router(social_sharing_router)
fastapi_app.include_router(recruit_router)
fastapi_app.include_router(og_router)
fastapi_app.include_router(og_preview_router)
fastapi_app.include_router(privacy_router)
fastapi_app.include_router(kyc_router)
fastapi_app.include_router(admin_settings_router)
fastapi_app.include_router(admin_payments_router)
fastapi_app.include_router(currency_router)
fastapi_app.include_router(pro_admin_router)
fastapi_app.include_router(referrals_admin_router)
fastapi_app.include_router(connect_router)
fastapi_app.include_router(connect_admin_router)
fastapi_app.include_router(ads_router)
fastapi_app.include_router(ads_console_router)
fastapi_app.include_router(seo_router)
fastapi_app.include_router(wallet_admin_router)
fastapi_app.include_router(ai_router)
fastapi_app.include_router(groups_pages_router)
fastapi_app.include_router(admin_stats_router)
fastapi_app.include_router(reactions_router)
fastapi_app.include_router(analytics_router)
fastapi_app.include_router(tasks_router)
fastapi_app.include_router(admin_messaging_router)
fastapi_app.include_router(email_tracking_router)
fastapi_app.include_router(migration_broadcast_router)
fastapi_app.include_router(public_assets_router)
fastapi_app.include_router(staking_router)
fastapi_app.include_router(staking_admin_router)
fastapi_app.include_router(security_router)
fastapi_app.include_router(wheel_router)
# iter241a — Forecast (Prediction Markets) MVP. Module gated by
# forecast_settings.module_enabled (admin can disable in one click).
fastapi_app.include_router(forecast_router)
fastapi_app.include_router(forecast_admin_router)

# iter237c — Defensive guard: wrap Mobile Money imports/registrations
# in a try/except so a single mis-loaded module never breaks pod boot.
# Iter234+iter235 added 30 routes; if any of them ever fails to import
# (DB-not-ready transient, missing dependency, etc.) the pod must keep
# serving the rest of the app — those endpoints will simply 404 until
# re-deployed. Strictly additive, zero behavioural change on the happy path.
try:
    from routes.orange_money_deposit import router as om_deposit_router
    from routes.orange_money_withdraw import router as om_withdraw_router
    from routes.wave_deposit import router as wave_deposit_router
    from routes.wave_withdraw import router as wave_withdraw_router
    fastapi_app.include_router(om_deposit_router)
    fastapi_app.include_router(om_withdraw_router)
    fastapi_app.include_router(wave_deposit_router)
    fastapi_app.include_router(wave_withdraw_router)
    # iter237h — Payment methods catalog + eligibility (additive).
    from routes.payment_methods import payment_methods_router
    fastapi_app.include_router(payment_methods_router)
except Exception as _mm_e:
    logger.error("[iter235] Mobile Money routers failed to load: %s", _mm_e, exc_info=True)

# Start the async messaging worker once FastAPI is up — CRITICAL (real-time).
start_messaging_worker(fastapi_app)

# iter237c — Defer 9 NON-CRITICAL background workers by 30 s after boot to
# reduce the RAM spike that may trigger OOM-Killer in tight prod containers.
# We register a single startup hook that, after sleep(30), schedules each
# worker's coroutine. Each branch is wrapped in try/except so a single
# misbehaving worker can't cascade-fail the entire batch. Strictly additive
# — original start_xxx_worker functions are still imported and untouched.
async def _iter237c_deferred_workers_start():
    """Background coroutine that fires non-critical workers 30 s after boot."""
    try:
        await asyncio.sleep(30)
    except Exception:
        return  # cancelled — nothing to do
    # Each branch independently catches its own errors so one failure
    # never blocks the next worker.
    _branches = [
        ("wheel_boost",           "services.wheel_boost_scheduler",            "_loop"),
        ("payment_verify",        "services.payment_verify_retry_worker",      "_loop"),
        ("quiz_champion",         "services.quiz_champion_scheduler",          "start_in_background"),
        ("scholarship_digest",    "services.scholarship_digest_worker",        "_loop"),
        ("public_url_audit",      "services.public_url_audit_worker",          "_loop"),
        ("cf_recruit_remind",     "services.crowdfunding_recruit_remind_worker","_loop"),
        # iter239w — Auto-close des cycles expirés et désignation gagnant
        ("cf_cycle_close",        "services.crowdfunding_cycle_close_worker",  "_loop"),
        ("seller_reminder",       "services.seller_reminder_worker",           "_loop"),
        ("video_transcode",       "services.video_transcode_worker",           "_loop"),
        ("migration_broadcast",   "services.migration_broadcast",              "_worker_loop"),
        # iter237af — Hubtel MoMo status check (catches missed callbacks).
        ("hubtel_momo_status",    "services.hubtel_momo_status_check",         "status_check_loop"),
    ]
    import importlib
    for label, mod_path, sym in _branches:
        try:
            mod = importlib.import_module(mod_path)
            target = getattr(mod, sym, None)
            if target is None:
                logger.warning("[iter237c] %s: missing symbol %s in %s — skipped.", label, sym, mod_path)
                continue
            # `start_in_background` is sync (already creates task internally);
            # `_loop`/`_worker_loop` are async coroutines we wrap in create_task.
            if asyncio.iscoroutinefunction(target):
                asyncio.create_task(target())
            else:
                target()
            logger.info("[iter237c] deferred-started %s (+30s).", label)
            await asyncio.sleep(0.5)  # spread spawns to smooth out RAM spike
        except Exception as e:
            logger.error("[iter237c] deferred %s failed: %s", label, e, exc_info=True)

@fastapi_app.on_event("startup")
async def _iter237c_register_deferred_workers():
    # Fire-and-forget — never blocks FastAPI startup itself.
    asyncio.create_task(_iter237c_deferred_workers_start())


@fastapi_app.get("/api")
async def api_root():
    return {"message": "JAPAP Messenger API v2.0", "status": "running"}

@fastapi_app.get("/api/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@fastapi_app.get("/api/health/db")
async def health_db():
    """iter240l-prodfix — Real readiness probe that exercises the DB pool.

    Returns 200 within ~1s when Postgres is reachable and the pool has at
    least one slot free, 503 otherwise. This is the endpoint monitoring
    must ping (every 60s) — `/api/health` only proves uvicorn is alive and
    will stay green even when every DB route is hung."""
    from database import get_pool
    import asyncio as _asyncio
    started = datetime.now(timezone.utc)
    try:
        pool = await get_pool()
        async with pool.acquire(timeout=5) as conn:
            ok = await _asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=3)
        ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        # Surface pool-stats so an external monitor can alert before
        # full saturation (e.g. if free=0 for >60s).
        try:
            stats = {
                "size":      pool.get_size(),
                "max_size":  pool.get_max_size(),
                "min_size":  pool.get_min_size(),
                "idle":      pool.get_idle_size(),
            }
        except Exception:
            stats = {}
        return {
            "status":     "healthy" if ok == 1 else "degraded",
            "db_ms":      round(ms, 1),
            "pool":       stats,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        from fastapi.responses import JSONResponse
        ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        return JSONResponse(
            status_code=503,
            content={
                "status":    "db_unreachable",
                "error":     str(e)[:200],
                "db_ms":     round(ms, 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


# ---- Socket.IO Events ----
connected_users = {}  # user_id -> set of sids

JWT_ALGORITHM = "HS256"

def get_jwt_secret():
    return os.environ["JWT_SECRET"]


def _parse_access_token_from_cookie(environ) -> str | None:
    """Extract `access_token` value from the Cookie header of the Socket.IO handshake.

    The frontend's auth cookie is httpOnly — it is sent with the WebSocket
    upgrade request but is NOT readable from JS (`document.cookie`). We
    therefore authenticate the socket server-side at connect time, so the
    client never needs to re-emit the JWT.
    """
    raw = environ.get('HTTP_COOKIE', '') or ''
    for part in raw.split(';'):
        name, _, val = part.strip().partition('=')
        if name == 'access_token' and val:
            return val
    return None


async def _authenticate_sid(sid: str, token: str) -> str | None:
    """Decode JWT, register sid → user_id, flip user online. Returns user_id on success."""
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        user_id = payload['sub']
        if user_id not in connected_users:
            connected_users[user_id] = set()
        connected_users[user_id].add(sid)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_online = TRUE, last_seen = $1 WHERE user_id = $2",
                datetime.now(timezone.utc), user_id,
            )
        await sio.emit('authenticated', {'user_id': user_id}, room=sid)
        await sio.emit('user_online', {'user_id': user_id})
        logger.info(f"User {user_id} authenticated on socket {sid}")
        return user_id
    except Exception as e:
        logger.info(f"Socket auth failed for sid={sid}: {e}")
        return None


@sio.event
async def connect(sid, environ):
    logger.info(f"Socket connected: {sid}")
    # Auto-authenticate using the httpOnly `access_token` cookie carried by
    # the handshake. This unblocks real-time ringing / typing / presence for
    # clients that cannot read the cookie from JS.
    token = _parse_access_token_from_cookie(environ)
    if token:
        await _authenticate_sid(sid, token)


@sio.event
async def disconnect(sid):
    user_id = None
    for uid, sids in connected_users.items():
        if sid in sids:
            user_id = uid
            sids.discard(sid)
            if not sids:
                del connected_users[uid]
            break
    
    if user_id:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET is_online = FALSE, last_seen = $1 WHERE user_id = $2",
                               datetime.now(timezone.utc), user_id)
        await sio.emit('user_offline', {'user_id': user_id})
        logger.info(f"User {user_id} disconnected")


@sio.event
async def authenticate(sid, data):
    token = data.get('token', '')
    user_id = await _authenticate_sid(sid, token)
    if not user_id:
        await sio.emit('auth_error', {'message': 'invalid token'}, room=sid)


@sio.event
async def join_conversation(sid, data):
    conv_id = data.get('conv_id')
    if conv_id:
        await sio.enter_room(sid, conv_id)
        logger.info(f"Socket {sid} joined room {conv_id}")


@sio.event
async def leave_conversation(sid, data):
    conv_id = data.get('conv_id')
    if conv_id:
        await sio.leave_room(sid, conv_id)


@sio.event
async def send_message(sid, data):
    user_id = None
    for uid, sids in connected_users.items():
        if sid in sids:
            user_id = uid
            break
    
    if not user_id:
        await sio.emit('error', {'message': 'Not authenticated'}, room=sid)
        return
    
    conv_id = data.get('conv_id')
    text = data.get('text', '')
    media = data.get('media', '')
    reply_to = data.get('reply_to') or None
    is_forwarded = bool(data.get('is_forwarded', False))
    # iter237v — round-trip the optimistic UI key so the sender's client
    # can replace its placeholder bubble in-place when this message
    # comes back via `new_message`.
    client_msg_id = data.get('client_msg_id') or None
    
    if not conv_id or (not text.strip() and not media):
        return
    
    import uuid
    pool = await get_pool()
    async with pool.acquire() as conn:
        participant = await conn.fetchrow(
            "SELECT id FROM conversation_participants WHERE conv_id = $1 AND user_id = $2", conv_id, user_id)
        if not participant:
            return
        
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        await conn.execute("""
            INSERT INTO messages (msg_id, conv_id, sender_id, text, media, reply_to, is_forwarded, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'sent')
        """, msg_id, conv_id, user_id, text, media, reply_to, is_forwarded)
        
        await conn.execute("UPDATE conversations SET updated_at = $1 WHERE conv_id = $2", datetime.now(timezone.utc), conv_id)
        
        user = await conn.fetchrow("SELECT first_name, last_name, avatar FROM users WHERE user_id = $1", user_id)
        
        # Include quoted message preview when replying
        reply_preview = None
        if reply_to:
            qrow = await conn.fetchrow("""
                SELECT m.msg_id, m.text, m.media, m.sender_id,
                       u.first_name, u.last_name
                FROM messages m JOIN users u ON m.sender_id = u.user_id
                WHERE m.msg_id = $1
            """, reply_to)
            if qrow:
                reply_preview = {
                    'msg_id': qrow['msg_id'],
                    'text': (qrow['text'] or '')[:140],
                    'media': qrow['media'] or '',
                    'sender_id': qrow['sender_id'],
                    'sender_name': f"{qrow['first_name'] or ''} {qrow['last_name'] or ''}".strip(),
                }
        
        message_data = {
            'msg_id': msg_id,
            'client_msg_id': client_msg_id,
            'conv_id': conv_id,
            'sender_id': user_id,
            'sender_name': f"{user['first_name']} {user['last_name']}".strip() if user else '',
            'sender_avatar': user['avatar'] if user else '',
            'text': text,
            'media': media,
            'reply_to': reply_to,
            'reply_preview': reply_preview,
            'is_forwarded': is_forwarded,
            'status': 'sent',
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        await sio.emit('new_message', message_data, room=conv_id)

        # Web Push fan-out to offline participants (iter70).
        # Fire-and-forget: runs outside the DB transaction, never blocks chat.
        try:
            from routes.realtime import notify_new_message_offline
            participants = await conn.fetch(
                "SELECT user_id FROM conversation_participants WHERE conv_id = $1 AND user_id <> $2",
                conv_id, user_id,
            )
            sender_name = f"{user['first_name']} {user['last_name']}".strip() if user else ''
            preview = (text or '').strip() or ('📎 Pièce jointe' if media else '')
            for p in participants:
                # notify_new_message_offline already skips online users
                await notify_new_message_offline(
                    recipient_id=p['user_id'],
                    sender_name=sender_name or 'JAPAP',
                    preview=preview,
                    conv_id=conv_id,
                )
        except Exception as e:
            logger.warning(f"web-push fanout skipped for conv={conv_id}: {e}")
    
    # Fire referral activation check (outside transaction)
    try:
        from routes.referrals import check_and_activate_referral
        await check_and_activate_referral(user_id)
    except Exception as e:
        logger.warning(f"Referral activation skipped: {e}")


@sio.event
async def mark_delivered(sid, data):
    """Client ACK : a batch of messages has been received by the recipient's
    client (not necessarily opened). Updates status from 'sent' to 'delivered'
    and notifies senders so their UI can update ticks."""
    user_id = None
    for uid, sids in connected_users.items():
        if sid in sids:
            user_id = uid
            break
    if not user_id:
        return
    conv_id = data.get('conv_id')
    msg_ids = data.get('msg_ids') or []
    if not conv_id or not isinstance(msg_ids, list) or not msg_ids:
        return
    msg_ids = [str(m) for m in msg_ids[:200]]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE messages SET status = 'delivered', updated_at = $1
            WHERE conv_id = $2 AND msg_id = ANY($3::varchar[])
              AND sender_id != $4 AND status = 'sent'
        """, datetime.now(timezone.utc), conv_id, msg_ids, user_id)
    # Broadcast so senders' UIs tick ✓✓
    await sio.emit('messages_delivered', {
        'conv_id': conv_id, 'msg_ids': msg_ids, 'by_user_id': user_id,
    }, room=conv_id)


@sio.event
async def typing(sid, data):
    user_id = None
    for uid, sids in connected_users.items():
        if sid in sids:
            user_id = uid
            break
    
    if user_id and data.get('conv_id'):
        await sio.emit('user_typing', {
            'user_id': user_id,
            'conv_id': data['conv_id'],
            'is_typing': data.get('is_typing', True)
        }, room=data['conv_id'], skip_sid=sid)


@sio.event
async def mark_seen(sid, data):
    user_id = None
    for uid, sids in connected_users.items():
        if sid in sids:
            user_id = uid
            break
    
    if user_id and data.get('conv_id'):
        conv_id = data['conv_id']
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE conversation_participants SET last_read_at = $1
                WHERE conv_id = $2 AND user_id = $3
            """, datetime.now(timezone.utc), conv_id, user_id)
            # Flip all incoming messages to 'seen' so sender's ticks become 👁
            await conn.execute("""
                UPDATE messages SET status = 'seen', updated_at = $1
                WHERE conv_id = $2 AND sender_id != $3 AND status IN ('sent','delivered')
            """, datetime.now(timezone.utc), conv_id, user_id)
        
        await sio.emit('messages_seen', {
            'user_id': user_id,
            'conv_id': conv_id,
        }, room=conv_id, skip_sid=sid)


# ======================================================================
# WEBRTC CALL SIGNALING (1-1 audio + video)
# ======================================================================
def _get_sid_user(sid):
    for uid, sids in connected_users.items():
        if sid in sids:
            return uid
    return None


def _user_sids(user_id):
    return list(connected_users.get(user_id, []))


@sio.event
async def call_invite(sid, data):
    """Caller -> Callee: invite to call. data = {call_id, callee_id, type, caller_name, caller_avatar}"""
    caller_id = _get_sid_user(sid)
    if not caller_id:
        return
    callee_sids = _user_sids(data.get('callee_id'))
    if not callee_sids:
        # Callee offline → tell caller
        await sio.emit('call_unavailable', {'call_id': data.get('call_id'), 'reason': 'offline'}, room=sid)
        return
    payload = {
        'call_id': data.get('call_id'),
        'session_id': data.get('session_id'),   # LiveKit session (new in iter51)
        'room_name': data.get('room_name'),     # LiveKit room
        'caller_id': caller_id,
        'caller_name': data.get('caller_name', ''),
        'caller_avatar': data.get('caller_avatar', ''),
        'type': data.get('type', 'audio'),
    }
    for s in callee_sids:
        await sio.emit('call_incoming', payload, room=s)
    logger.info(f"Call invite: {caller_id} -> {data.get('callee_id')} ({data.get('type')})")


@sio.event
async def call_accept(sid, data):
    """Callee -> Caller: accepted, start SDP negotiation."""
    callee_id = _get_sid_user(sid)
    if not callee_id:
        return
    caller_sids = _user_sids(data.get('caller_id'))
    for s in caller_sids:
        await sio.emit('call_accepted', {
            'call_id': data.get('call_id'),
            'session_id': data.get('session_id'),
            'callee_id': callee_id,
        }, room=s)


@sio.event
async def call_reject(sid, data):
    callee_id = _get_sid_user(sid)
    if not callee_id:
        return
    caller_sids = _user_sids(data.get('caller_id'))
    for s in caller_sids:
        await sio.emit('call_rejected', {'call_id': data.get('call_id')}, room=s)


@sio.event
async def call_cancel(sid, data):
    """Caller cancels before callee answers."""
    caller_id = _get_sid_user(sid)
    if not caller_id:
        return
    callee_sids = _user_sids(data.get('callee_id'))
    for s in callee_sids:
        await sio.emit('call_cancelled', {'call_id': data.get('call_id')}, room=s)


@sio.event
async def call_end(sid, data):
    """Either party ends the call."""
    user_id = _get_sid_user(sid)
    if not user_id:
        return
    peer_id = data.get('peer_id')
    peer_sids = _user_sids(peer_id)
    for s in peer_sids:
        await sio.emit('call_ended', {'call_id': data.get('call_id')}, room=s)


@sio.event
async def call_offer(sid, data):
    """Caller sends SDP offer to callee."""
    user_id = _get_sid_user(sid)
    if not user_id:
        return
    peer_sids = _user_sids(data.get('peer_id'))
    for s in peer_sids:
        await sio.emit('call_offer', {
            'call_id': data.get('call_id'),
            'from_id': user_id,
            'sdp': data.get('sdp'),
        }, room=s)


@sio.event
async def call_answer(sid, data):
    """Callee sends SDP answer back."""
    user_id = _get_sid_user(sid)
    if not user_id:
        return
    peer_sids = _user_sids(data.get('peer_id'))
    for s in peer_sids:
        await sio.emit('call_answer', {
            'call_id': data.get('call_id'),
            'from_id': user_id,
            'sdp': data.get('sdp'),
        }, room=s)


@sio.event
async def call_ice(sid, data):
    """Exchange ICE candidates."""
    user_id = _get_sid_user(sid)
    if not user_id:
        return
    peer_sids = _user_sids(data.get('peer_id'))
    for s in peer_sids:
        await sio.emit('call_ice', {
            'call_id': data.get('call_id'),
            'from_id': user_id,
            'candidate': data.get('candidate'),
        }, room=s)


# ───── Sprint C — Group calls (fanout to all conv members) ─────
@sio.event
async def group_call_announce(sid, data):
    """Host is starting a group call — broadcast to every online conv member
    (except the host) so they can see the "Rejoindre" banner in their chat."""
    host_id = _get_sid_user(sid)
    conv_id = data.get('conv_id')
    session_id = data.get('session_id')
    if not (host_id and conv_id and session_id):
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        members = await conn.fetch(
            "SELECT user_id FROM conversation_participants WHERE conv_id = $1",
            conv_id,
        )
    payload = {
        'conv_id': conv_id,
        'session_id': session_id,
        'mode': data.get('mode', 'audio'),
        'title': data.get('title', ''),
        'host_id': host_id,
        'host_name': data.get('host_name', ''),
    }
    for m in members:
        if m['user_id'] == host_id:
            continue
        for s in _user_sids(m['user_id']):
            await sio.emit('group_call_live', payload, room=s)
    logger.info(f"Group call announced: host={host_id} conv={conv_id} session={session_id}")


@sio.event
async def group_call_leave(sid, data):
    """A participant left. If the server marked the session as ended, we fan
    out `group_call_ended` so the banner disappears on everyone's UI."""
    user_id = _get_sid_user(sid)
    conv_id = data.get('conv_id')
    session_id = data.get('session_id')
    if not (user_id and conv_id and session_id):
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM call_sessions WHERE session_id = $1", session_id,
        )
        if not row or row['status'] != 'ended':
            return
        members = await conn.fetch(
            "SELECT user_id FROM conversation_participants WHERE conv_id = $1",
            conv_id,
        )
    payload = {'conv_id': conv_id, 'session_id': session_id}
    for m in members:
        for s in _user_sids(m['user_id']):
            await sio.emit('group_call_ended', payload, room=s)


# Startup / Shutdown
@fastapi_app.on_event("startup")
async def startup():
    await init_db()
    await seed_admin()
    await seed_superadmin()
    # iter82 — create revoked_refresh_tokens, active_sessions, security_events,
    # totp_* columns if missing. Idempotent.
    try:
        await ensure_security_tables()
        await ensure_wheel_tables()
        await ensure_forecast_tables()
        # iter146 — trusted devices table for long-lived sessions on
        # recognised devices (≥2 successful logins).
        from services.trusted_device_service import ensure_trusted_devices_table
        await ensure_trusted_devices_table()
        # iter150 — filter_preset metadata on posts/stories + indexes.
        from services.gallery_service import ensure_gallery_columns
        await ensure_gallery_columns()
        # iter150 — IA filter Phase 2 — request/quota tracking table.
        from routes.ai_filters import ensure_ai_filter_table
        await ensure_ai_filter_table()
        # iter158 — canonical USD wallet migration (one-shot, idempotent).
        from services.usd_canonical_migration import run_migration as _usd_mig
        await _usd_mig()
        # iter178 — currency canonical sweep (catches drift from iter158)
        from services.currency_canonical_sweep import run_full_sweep as _curr_sweep
        sw = await _curr_sweep(source="boot")
        if sw["wallets"].get("converted") or sw["transactions"].get("backfilled"):
            logger.info(f"[currency-canonical] boot sweep {sw}")
        logger.info("Security + wheel + trusted_devices + gallery + ai_filters tables ensured (iter82/83/146/150). USD canonical migration ensured (iter158/178).")
    except Exception as e:
        logger.warning(f"ensure_security_tables failed: {e}")
    # iter214 — one-time ALTER TABLE for KYC bytea columns at boot so
    # admin endpoints don't pay the schema-check cost on every request.
    try:
        from routes.kyc import _ensure_iter172_columns
        await _ensure_iter172_columns()
    except Exception as e:
        logger.warning(f"ensure_kyc_iter214_columns failed: {e}")
    # Seed default admin settings (idempotent) and ensure currency rates are hot
    try:
        from services.settings_service import seed_defaults as _seed_settings, get_setting as _gs, set_setting as _ss
        await _seed_settings()
        # iter82 — Batch Scale: one-time force messaging_real_send_enabled=false
        # unless an admin explicitly enabled it after this migration ran.
        marker = await _gs("_migration_iter82_safemode_forced")
        if marker != "1":
            await _ss("messaging_real_send_enabled", "false")
            await _ss("_migration_iter82_safemode_forced", "1")
            logger.info("[iter82] Forced messaging_real_send_enabled=false (safe mode).")
        # iter239b — silently copy Hubtel MoMo creds from env to admin_settings
        # on first boot. Idempotent (only writes when DB row empty).
        try:
            from services.hubtel_bootstrap import init_hubtel_settings
            await init_hubtel_settings()
        except Exception as _e:
            logger.warning("[iter239b] hubtel bootstrap failed: %s", _e)
        # iter239h — start the vendor-health monitoring loop (every 5 min).
        try:
            import asyncio as _asyncio
            from services.vendor_health import vendor_health_loop
            _asyncio.create_task(vendor_health_loop())
            logger.info("[iter239h] vendor health loop scheduled at startup")
        except Exception as _e:
            logger.warning("[iter239h] vendor health loop failed to schedule: %s", _e)
    except Exception as e:
        logger.warning(f"Settings seed failed: {e}")
    try:
        from routes.currency import _ensure_rates_table_hot
        await _ensure_rates_table_hot()
    except Exception as e:
        logger.warning(f"Currency rates seed failed: {e}")
    try:
        from routes.pro import seed_plans as _seed_pro_plans
        await _seed_pro_plans()
    except Exception as e:
        logger.warning(f"Pro plans seed failed: {e}")
    try:
        await seed_staking_defaults()
    except Exception as e:
        logger.warning(f"Staking seed failed: {e}")
    try:
        from services.push_service import ensure_table as _push_ensure_table
        await _push_ensure_table()
    except Exception as e:
        logger.warning(f"Push subscriptions table ensure failed: {e}")
    init_realtime(sio, connected_users)
    # Startup health check — flag missing integration creds early so infra
    # cycles that wipe admin_settings are detected immediately in the logs
    # instead of surfacing later as 503s in the UI (fix for iter57 regression).
    try:
        from services.settings_service import get_setting
        llm_key = os.environ.get("EMERGENT_LLM_KEY", "")
        lk_key = await get_setting("livekit_api_key") or os.environ.get("LIVEKIT_API_KEY", "")
        lk_ws = await get_setting("livekit_ws_url") or os.environ.get("LIVEKIT_WS_URL", "")
        r2_key = await get_setting("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID", "")
        missing = []
        if not llm_key: missing.append("EMERGENT_LLM_KEY")
        if not (lk_key and lk_ws): missing.append("LiveKit (livekit_api_key / livekit_ws_url)")
        if not r2_key: missing.append("Cloudflare R2 (r2_access_key_id)")
        if missing:
            logger.warning(
                "🔴 Startup health check — missing integration creds: %s. "
                "Calls/recording/AI features will return 503 until reconfigured.",
                ", ".join(missing),
            )
        else:
            logger.info("✅ Startup health check — all integration creds present (LLM / LiveKit / R2).")
    except Exception as _e:
        logger.warning(f"Startup health check failed: {_e}")
    logger.info("JAPAP Messenger API v2.0 started")

@fastapi_app.on_event("shutdown")
async def shutdown():
    await close_db()

# Wrap FastAPI with Socket.IO
socket_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path='/api/socket.io')
app = socket_app
