"""
Rate-limit middleware (slowapi) — Connect v2.1 hardening.

Keys each request by authenticated user_id when a JWT is present, else by
client IP. Limits are declared per-endpoint via @limiter.limit("...").

Exposed:
    limiter                         — singleton Limiter
    install_rate_limiter(app)       — wires state + exception handler
"""
import os
import jwt as _jwt
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request


def _user_or_ip(request: Request) -> str:
    """Identify the caller for rate-limit bucketing.

    Tries to read `sub` / `user_id` from the Bearer JWT (unverified — we
    don't need integrity here, only a stable handle). Falls back to the
    client's public IP (honouring cf-connecting-ip when present).
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = _jwt.decode(
                auth[7:], options={"verify_signature": False, "verify_exp": False}
            )
            uid = payload.get("user_id") or payload.get("sub")
            if uid:
                return f"u:{uid}"
        except Exception:
            pass
    # Prefer cf-connecting-ip when deployed behind Cloudflare
    cf_ip = request.headers.get("cf-connecting-ip")
    return f"ip:{cf_ip or get_remote_address(request)}"


# Allow disabling rate limits in tests via env flag (tests often burst).
_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "false"

limiter = Limiter(
    key_func=_user_or_ip,
    enabled=_ENABLED,
    headers_enabled=False,
    default_limits=[],
)


def install_rate_limiter(app) -> None:
    """Wire the limiter onto the FastAPI app + register the 429 handler."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
