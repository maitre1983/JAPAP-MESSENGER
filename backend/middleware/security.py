"""
JAPAP — Global security middleware (iter82 hardening).

Adds per-response security headers that match the OWASP Secure Headers
project recommendations plus a soft CSRF guard on state-changing routes.

Headers applied on EVERY response
---------------------------------
  Strict-Transport-Security: enforce HTTPS for 6 months
  X-Frame-Options: DENY (no clickjacking)
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: minimal browser feature surface
  Cross-Origin-Opener-Policy: same-origin
  X-XSS-Protection: 0  (legacy header disabled — modern browsers use CSP)

CSRF guard
----------
Cookie-based auth (access_token / refresh_token) is vulnerable to classic
CSRF if a cross-origin form posts to JAPAP with the cookies attached.
`SameSite=Lax` covers most of the surface but NOT all (e.g. top-level
navigations with POST in some browsers, iframes targeting the main window).

We therefore require EITHER:
  • an `Authorization: Bearer <token>` header (JS clients), OR
  • a custom `X-Requested-With: XMLHttpRequest` header (our SPA sets it), OR
  • an `X-CSRF-Token` header whose value matches the `csrf_token` cookie
    (double-submit pattern).

Any state-changing request (POST / PUT / PATCH / DELETE) that relies on
cookies and lacks these markers is rejected with 403.

Routes that are entered from a browser directly (webhooks, OAuth return,
Svix signature, etc.) are explicitly exempt via `_CSRF_EXEMPT_PREFIXES`.
"""
from __future__ import annotations
import logging
import secrets
import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


_SECURITY_HEADERS = {
    # 6 months + includeSubDomains. Preload can be enabled once the domain
    # is confirmed on the HSTS preload list.
    "Strict-Transport-Security": "max-age=15552000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Permissions-Policy — minimal allow set. Extend as new features require.
    "Permissions-Policy": (
        "camera=(self), microphone=(self), geolocation=(self), "
        "payment=(self), usb=(), bluetooth=(), accelerometer=(), "
        "gyroscope=(), magnetometer=(), interest-cohort=()"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    # Legacy header that now causes more harm than good; explicitly disable.
    "X-XSS-Protection": "0",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject the standard OWASP security headers on every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for k, v in _SECURITY_HEADERS.items():
            # Respect headers that a specific endpoint may have already set.
            response.headers.setdefault(k, v)
        return response


# ----------------------------------------------------------------------------
# CSRF guard
# ----------------------------------------------------------------------------

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# Routes that legitimately receive cookie-authenticated requests without
# the SPA's custom header (browser redirects, 3rd-party webhooks, etc.).
_CSRF_EXEMPT_PREFIXES = (
    "/api/email-tracking/webhook",          # Resend → Svix signature-verified
    "/api/notifications/webhook",            # external provider webhooks
    "/api/auth/google/session",              # OAuth return (session_id in JSON)
    "/api/auth/register",                    # first-touch, no cookie yet
    "/api/auth/login",                       # first-touch, no cookie yet
    "/api/auth/verify-otp",                  # first-touch
    "/api/auth/resend-otp",                  # first-touch
    "/api/auth/forgot-password",             # first-touch
    "/api/auth/reset-password",              # first-touch, token in body
    "/api/connect/",                          # hotspot QR consume (anon)
    "/api/socket.io",                        # socket.io polling
    "/api/staking/webhook",                  # on-chain sync webhook
    # iter161 — payment provider webhooks: HMAC-signature verified.
    # Hubtel/NowPayments servers never carry JAPAP session cookies, so this
    # exemption is defensive; but it prevents any edge-case where a stray
    # cookie attaches (e.g. manual curl re-tests from a logged-in terminal).
    "/api/wallet/hubtel/webhook",
    "/api/wallet/nowpayments/webhook",
)


def _disabled() -> bool:
    return os.environ.get("CSRF_PROTECTION", "true").lower() in ("false", "0", "no")


class CsrfMiddleware(BaseHTTPMiddleware):
    """
    Reject state-changing requests that rely on cookie auth without
    providing an anti-CSRF marker.

    Markers accepted (any one of them disables the 403):
      • `Authorization: Bearer` header
      • `X-Requested-With: XMLHttpRequest` header (SPA convention)
      • `X-CSRF-Token` header matching the `csrf_token` cookie
    """

    async def dispatch(self, request: Request, call_next):
        if _disabled():
            return await call_next(request)

        method = request.method.upper()
        if method in _SAFE_METHODS:
            response = await call_next(request)
            # First-touch: set csrf_token cookie if missing.
            if "csrf_token" not in request.cookies:
                token = secrets.token_urlsafe(32)
                secure = os.environ.get("COOKIE_SECURE", "true").lower() not in ("false", "0", "no")
                response.set_cookie(
                    key="csrf_token", value=token,
                    httponly=False,         # JS must read it to echo back
                    secure=secure, samesite="lax", max_age=604800, path="/",
                )
            return response

        path = request.url.path
        if any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES):
            return await call_next(request)

        # If the request is bearer-authenticated (JS client explicitly sets
        # the header), CSRF is not a concern — browsers don't attach it
        # automatically.
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return await call_next(request)

        # SPA convention — lightweight and effective.
        xrw = request.headers.get("x-requested-with", "")
        if xrw.strip().lower() == "xmlhttprequest":
            return await call_next(request)

        # Double-submit — strict form
        token_hdr = request.headers.get("x-csrf-token", "")
        token_cookie = request.cookies.get("csrf_token", "")
        if token_hdr and token_cookie and secrets.compare_digest(token_hdr, token_cookie):
            return await call_next(request)

        # If the request is neither cookie-authenticated nor bearer-
        # authenticated, let the downstream auth check return 401.
        # CSRF only applies to cookie-authenticated requests.
        if not request.cookies.get("access_token") and not request.cookies.get("session_token"):
            return await call_next(request)

        logger.warning(
            "CSRF guard rejected %s %s from %s (ua=%s)",
            method, path,
            request.client.host if request.client else "?",
            request.headers.get("user-agent", "?")[:80],
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF protection: missing or invalid token"},
        )
