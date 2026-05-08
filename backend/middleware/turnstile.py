"""
Cloudflare Turnstile verification helper.

Used to protect public-facing auth endpoints (register / login / forgot-password)
against automated bot traffic.

Usage:
    from middleware.turnstile import verify_turnstile

    await verify_turnstile(token, request)

Behaviour:
    • If TURNSTILE_SECRET_KEY is not set → fail-open (dev mode) + log warning.
    • Otherwise POSTs to Cloudflare's siteverify API and raises 401 on failure.
    • Network errors → 503 (so the client can retry instead of being banned).
    • Timeout 8s to avoid blocking auth flows on Cloudflare latency.
"""
import os
import logging
import httpx
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _secret() -> str:
    return (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()


def is_turnstile_enabled() -> bool:
    """Turnstile is active only when the secret key is actually provisioned."""
    return bool(_secret())


async def verify_turnstile(token: str | None, request: Request) -> bool:
    """
    Validates a Turnstile token against Cloudflare's siteverify endpoint.

    Args:
        token: The cf-turnstile-response value from the client widget.
        request: The FastAPI Request (used to extract the real client IP).

    Returns:
        True when the token is valid. Raises HTTPException otherwise.

    Raises:
        HTTPException 400 — token missing while Turnstile is enabled.
        HTTPException 401 — token rejected by Cloudflare.
        HTTPException 503 — Cloudflare is unreachable / timing out.
    """
    secret = _secret()

    # Test bypass (env-gated): allows automated E2E suites to call protected
    # endpoints without solving a real Turnstile widget. The bypass is OFF by
    # default and must be explicitly armed via TURNSTILE_TEST_BYPASS_TOKEN.
    # When armed, the client must send EXACTLY that token value as the
    # turnstile_token to be allowed through. Never log the token.
    bypass = (os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN") or "").strip()
    if bypass and token and token.strip() == bypass:
        logger.warning("Turnstile test-bypass token accepted (E2E mode).")
        return True

    # Dev fail-open: if the operator hasn't provisioned the secret, skip check.
    # This keeps local dev frictionless while prod is hardened by default.
    if not secret:
        logger.warning(
            "Turnstile disabled (TURNSTILE_SECRET_KEY not set) — "
            "allowing request through. DO NOT ship this to production."
        )
        return True

    if not token or not token.strip():
        raise HTTPException(
            status_code=400,
            detail="Turnstile verification required. Please retry.",
        )

    # Extract the real client IP (behind Kubernetes ingress / Cloudflare).
    client_ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )

    payload = {
        "secret": secret,
        "response": token.strip(),
    }
    if client_ip:
        payload["remoteip"] = client_ip

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(_SITEVERIFY_URL, data=payload)
        resp.raise_for_status()
        result = resp.json()
    except httpx.TimeoutException:
        logger.error("Turnstile siteverify timeout for ip=%s", client_ip)
        raise HTTPException(
            status_code=503,
            detail="Verification service temporarily unavailable. Please retry.",
        )
    except Exception as e:
        logger.error("Turnstile siteverify error: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Verification service temporarily unavailable. Please retry.",
        )

    if not result.get("success"):
        error_codes = result.get("error-codes", [])
        logger.warning(
            "Turnstile rejected token: codes=%s ip=%s",
            error_codes,
            client_ip,
        )
        raise HTTPException(
            status_code=401,
            detail="Bot protection failed. Please refresh the page and retry.",
        )

    return True
