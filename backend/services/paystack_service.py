"""
iter238 — Paystack Ghana service helpers (STRICTLY ADDITIVE).

Does NOT modify the existing wallet / payment routes. Provides all the
helpers needed by `routes/paystack.py`:

  • Credentials (admin DB → env fallback)
  • USD → GHS FX (manual admin → live API → fallback admin → 14.50)
  • Webhook HMAC-SHA512 signature verification
  • JAPAP-prefixed reference generator
  • Deposit limits (admin-configurable)

Patterns mirror `services/hubtel_fx.py` + `services/hubtel_momo.py`
(async, in-memory FX cache 1h, async DB settings via settings_service).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from services.settings_service import get_setting

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = "https://api.paystack.co"
_FX_CACHE: dict = {"rate": None, "fetched_at": None}
_FX_CACHE_TTL = timedelta(hours=1)
_FX_DEFAULT_FALLBACK = 14.50
_FX_TIMEOUT = 5.0


# ───────────────────────── Settings helpers ─────────────────────────
async def _setting(key: str, default: str | None = None) -> str | None:
    """admin_settings → env fallback. Empty string is treated as unset."""
    value = await get_setting(key)
    if value is not None and str(value).strip() != "":
        return value
    return default


async def get_paystack_secret() -> str | None:
    return await _setting("paystack_secret_key",
                          os.environ.get("PAYSTACK_SECRET_KEY"))


async def get_paystack_public() -> str | None:
    return await _setting("paystack_public_key",
                          os.environ.get("PAYSTACK_PUBLIC_KEY"))


async def is_paystack_enabled() -> bool:
    """Toggle `paystack_enabled` (default TRUE). Set to false to block all
    Paystack endpoints with a 403 method_disabled response."""
    raw = await get_setting("paystack_enabled")
    if raw is None or str(raw).strip() == "":
        return True  # default ON
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


async def get_deposit_limits() -> dict:
    raw_min = await _setting("paystack_deposit_min", "1.00")
    raw_max = await _setting("paystack_deposit_max", "5000.00")
    try:
        return {"min": float(raw_min), "max": float(raw_max)}
    except (TypeError, ValueError):
        return {"min": 1.00, "max": 5000.00}


# ───────────────────────── Reference + HMAC ────────────────────────
def generate_reference() -> str:
    """`JAPAP-XXXXXXXXXXXXXXXXXXXX` (26 chars total). Paystack allows
    up to 100 chars for `reference`, we stay well below."""
    return "JAPAP-" + uuid.uuid4().hex[:20].upper()


def verify_webhook_signature(payload_bytes: bytes, signature: str,
                              secret: str) -> bool:
    """HMAC-SHA512 of the raw body, hex-digest comparison. Constant-time."""
    if not signature or not secret:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ───────────────────────────── FX (USD → GHS) ──────────────────────
async def _admin_rate(key: str) -> float | None:
    raw = await get_setting(key)
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


async def _fetch_live_rate() -> float | None:
    try:
        async with httpx.AsyncClient(timeout=_FX_TIMEOUT) as client:
            r = await client.get("https://open.er-api.com/v6/latest/USD")
        if r.status_code != 200:
            return None
        data = r.json() or {}
        rate = data.get("rates", {}).get("GHS")
        if not rate:
            return None
        rate_f = float(rate)
        return rate_f if rate_f > 0 else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[paystack-fx] live fetch failed: %s", e)
        return None


async def get_usd_to_ghs_info() -> dict:
    """iter239a3 — Delegates to the centralized FX service so Paystack
    and Hubtel MoMo display the same rate.

    Public shape preserved: `{rate, source, fetched_at}`. The legacy
    `paystack_usd_ghs_rate` and `paystack_usd_ghs_fallback_rate` admin
    keys remain honored as part of the chain (priority 2 and 7
    respectively); the new global `usd_ghs_rate` takes precedence."""
    from services.fx_service import get_usd_to_ghs_info as _global
    info = await _global()
    return {
        "rate": info["rate"],
        "source": info["source"],
        "fetched_at": info.get("fetched_at"),
    }


async def convert_usd_to_ghs(amount_usd: float) -> dict:
    """Returns {rate, rate_source, amount_usd, amount_ghs, amount_pesewas}.
    Pesewas = GHS × 100 (Paystack expects amount in the smallest unit)."""
    info = await get_usd_to_ghs_info()
    rate = float(info["rate"])
    amount_ghs = round(float(amount_usd) * rate, 2)
    amount_pesewas = int(round(amount_ghs * 100))
    return {
        "rate": rate,
        "rate_source": info["source"],
        "fetched_at": info.get("fetched_at"),
        "amount_usd": float(amount_usd),
        "amount_ghs": amount_ghs,
        "amount_pesewas": amount_pesewas,
    }


__all__ = [
    "PAYSTACK_BASE_URL",
    "get_paystack_secret", "get_paystack_public",
    "is_paystack_enabled", "get_deposit_limits",
    "generate_reference", "verify_webhook_signature",
    "get_usd_to_ghs_info", "convert_usd_to_ghs",
]
