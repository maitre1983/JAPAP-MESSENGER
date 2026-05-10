"""
iter238 — Payment method toggle middleware (STRICTLY ADDITIVE).

Intercepts the existing NowPayments + Hubtel-card endpoints and returns
`403 method_disabled` if the corresponding `system_settings` toggle is
off. Webhook paths are NEVER blocked (we must keep accepting webhooks
even if the admin disables the method mid-flight, to avoid losing
money on already-initiated transactions).

iter239a — Extended:
  • Gates 5 more methods (paystack, hubtel_momo, orange_money, wave).
  • Body-level gate on `POST /api/wallet/deposit` + `POST /api/wallet/withdraw`
    for USDT manual (method='usdt_trc20'|'usdt_bep20') since those share
    a single endpoint that dispatches by body `method`.

ZERO modification of the routes themselves — this middleware sits in
front of FastAPI's router and short-circuits gated paths.
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from services.settings_service import get_setting

logger = logging.getLogger(__name__)


# URL-prefix gates: (path_prefix, setting_key, default_when_unset).
_GATES: list[tuple[str, str, bool]] = [
    # iter238 — first batch.
    ("/api/wallet/nowpayments/",          "nowpayments_enabled",  False),
    ("/api/payments/hubtel/initiate",     "hubtel_card_enabled",  False),
    ("/api/payments/hubtel/methods/",     "hubtel_card_enabled",  False),
    ("/api/payments/hubtel/exchange-rate","hubtel_card_enabled",  False),
    # iter239a — paystack / hubtel-momo / OM / wave.
    ("/api/paystack/",                    "paystack_enabled",     True),
    ("/api/wallet/hubtel-momo/",          "hubtel_momo_enabled",  True),
    ("/api/wallet/deposit/hubtel-momo",   "hubtel_momo_enabled",  True),
    ("/api/wallet/withdraw/hubtel-momo",  "hubtel_momo_enabled",  True),
    ("/api/deposits/orange-money/",       "orange_money_enabled", True),
    ("/api/withdrawals/orange-money/",    "orange_money_enabled", True),
    ("/api/deposits/wave/",               "wave_enabled",         True),
    ("/api/withdrawals/wave/",            "wave_enabled",         True),
]

# Body-level gates: routes that dispatch on a JSON body `method` field.
# Maps a fixed (path, http_method) → list of (body_method_value, setting_key, default).
_BODY_GATES: dict[tuple[str, str], list[tuple[str, str, bool]]] = {
    ("/api/wallet/deposit", "POST"): [
        ("usdt_trc20", "usdt_manual_enabled", True),
        ("usdt_bep20", "usdt_manual_enabled", True),
    ],
    ("/api/wallet/withdraw", "POST"): [
        ("usdt_trc20", "usdt_manual_enabled", True),
        ("usdt_bep20", "usdt_manual_enabled", True),
    ],
}


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


def _disabled_response() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "error": "method_disabled",
            "message": "This payment method is currently unavailable.",
        },
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

        # 1) URL-prefix gates.
        for prefix, setting_key, default in _GATES:
            if path.startswith(prefix):
                raw = await get_setting(setting_key)
                if not _coerce_bool(raw, default):
                    logger.info("[payment-toggles] blocked %s (key=%s)",
                                path, setting_key)
                    return _disabled_response()
                break

        # 2) Body-level gates (USDT manual on /deposit + /withdraw).
        body_gates = _BODY_GATES.get((path, request.method))
        if body_gates:
            try:
                body_bytes = await request.body()
                # Stash the bytes so downstream handlers can still read them.
                # Starlette caches in `request._body` automatically when
                # request.body() is awaited; downstream `await request.body()`
                # / `request.json()` will reuse the cache.
                payload = json.loads(body_bytes) if body_bytes else {}
            except Exception:
                payload = {}
            method_field = (payload.get("method") or "").strip()
            for body_method, setting_key, default in body_gates:
                if method_field == body_method:
                    raw = await get_setting(setting_key)
                    if not _coerce_bool(raw, default):
                        logger.info(
                            "[payment-toggles] blocked body-gate %s method=%s (key=%s)",
                            path, body_method, setting_key,
                        )
                        return _disabled_response()
                    break

        return await call_next(request)


__all__ = ["PaymentTogglesMiddleware"]
