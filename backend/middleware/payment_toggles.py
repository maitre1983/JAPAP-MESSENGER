"""
iter238 — Payment method toggle middleware (STRICTLY ADDITIVE).

Intercepts the existing NowPayments + Hubtel-card endpoints and returns
`403 method_disabled` if the corresponding `system_settings` toggle is
off. Webhook paths are NEVER blocked (we must keep accepting webhooks
even if the admin disables the method mid-flight, to avoid losing
money on already-initiated transactions).

ZERO modification of the routes themselves — this middleware sits in
front of FastAPI's router and short-circuits gated paths.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from services.settings_service import get_setting

logger = logging.getLogger(__name__)


# Paths that should be gated by the corresponding toggle. Prefix match.
# Webhooks (paths containing "/webhook" or "/callback") are exempt —
# enforced explicitly in `_is_exempt` below.
_GATES: list[tuple[str, str, bool]] = [
    # (path_prefix, setting_key, default_when_unset)
    ("/api/wallet/nowpayments/", "nowpayments_enabled", False),
    ("/api/payments/hubtel/initiate", "hubtel_card_enabled", False),
    ("/api/payments/hubtel/methods/", "hubtel_card_enabled", False),
    ("/api/payments/hubtel/exchange-rate", "hubtel_card_enabled", False),
]


def _coerce_bool(raw: str | None, default: bool) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _is_exempt(path: str) -> bool:
    """Webhooks / callbacks / return URLs are ALWAYS allowed through.
    Otherwise an in-flight transaction would silently fail to credit."""
    p = path.lower()
    return (
        "/webhook" in p
        or "/callback" in p
        or "/return/" in p
    )


class PaymentTogglesMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if _is_exempt(path):
            return await call_next(request)

        for prefix, setting_key, default in _GATES:
            if path.startswith(prefix):
                raw = await get_setting(setting_key)
                if not _coerce_bool(raw, default):
                    logger.info(
                        "[payment-toggles] blocked %s (key=%s)",
                        path, setting_key,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "method_disabled",
                            "message": "This payment method is currently unavailable.",
                        },
                    )
                break

        return await call_next(request)


__all__ = ["PaymentTogglesMiddleware"]
