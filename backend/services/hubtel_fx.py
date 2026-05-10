"""
iter237af — USD → GHS exchange rate service.

Priority chain when fetching the rate:

  1. `hubtel_usd_ghs_rate`         — manual admin override (DB).
  2. In-memory cache (TTL 1 hour)  — last successful live fetch.
  3. ExchangeRate-API live fetch    — open.er-api.com/v6/latest/USD (no key).
  4. `hubtel_usd_ghs_fallback_rate` — admin-configured fallback, default 14.50.

Rate source is exposed in the public response so the frontend can show
"Taux live actuel : 1 USD = 14.83 GHS (mis à jour il y a 12 min)".

The cache is a module-level dict — single-process resilience. Multi-pod
deployments will each maintain their own cache; that's fine since the
upstream API returns the same value regardless of where we ask.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import httpx

from services.settings_service import get_setting

logger = logging.getLogger(__name__)

_CACHE: dict = {"rate": None, "fetched_at": None, "source": None}
_CACHE_TTL = timedelta(hours=1)
_DEFAULT_FALLBACK = 14.50
_TIMEOUT_SECONDS = 5.0


async def _admin_rate(key: str) -> float | None:
    """Reads an admin-configured rate. Returns None if unset or invalid."""
    raw = await get_setting(key)
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


async def _fetch_live_rate() -> float | None:
    """Calls the open ExchangeRate-API. 5 s timeout. Returns None on any
    failure so the caller can fall through to the next priority."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
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
        logger.warning("[hubtel-fx] live fetch failed: %s", e)
        return None


async def get_usd_to_ghs_info() -> dict:
    """iter239a3 — Delegates to the centralized FX service so all USD↔GHS
    flows (Hubtel MoMo, Paystack, anything else) share the same priority
    chain and the same in-memory cache. The legacy `hubtel_usd_ghs_rate`
    and `hubtel_usd_ghs_fallback_rate` admin keys remain honored as part
    of that chain.

    Public shape unchanged: `{rate, source, fetched_at}` — callers in
    `routes/hubtel_momo.py` keep working with zero changes."""
    from services.fx_service import get_usd_to_ghs_info as _global
    info = await _global()
    # Strip the new `key` field for backwards compat — older callers
    # only know about {rate, source, fetched_at}.
    return {
        "rate": info["rate"],
        "source": info["source"],
        "fetched_at": info.get("fetched_at"),
    }


async def get_usd_to_ghs_rate() -> float:
    """Convenience accessor returning just the float rate."""
    info = await get_usd_to_ghs_info()
    return float(info["rate"])


async def convert_usd_to_ghs(amount_usd: float) -> dict:
    """Converts USD → GHS. Returns {rate, source, fetched_at,
    amount_usd, amount_ghs}."""
    info = await get_usd_to_ghs_info()
    amount_ghs = round(float(amount_usd) * info["rate"], 2)
    return {
        "rate": info["rate"],
        "source": info["source"],
        "fetched_at": info.get("fetched_at"),
        "amount_usd": float(amount_usd),
        "amount_ghs": amount_ghs,
    }


__all__ = ["get_usd_to_ghs_info", "get_usd_to_ghs_rate", "convert_usd_to_ghs"]
