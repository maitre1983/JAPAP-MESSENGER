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
    """Returns {rate, source, fetched_at} where source ∈ {"manual",
    "live", "cache", "fallback"}. The frontend uses `source` to decide
    whether to display the "live" timestamp."""
    # 1. Manual admin override.
    manual = await _admin_rate("hubtel_usd_ghs_rate")
    if manual is not None:
        return {"rate": manual, "source": "manual", "fetched_at": None}

    # 2. Cache (still warm).
    now = datetime.now(timezone.utc)
    if _CACHE["rate"] and _CACHE["fetched_at"] and (now - _CACHE["fetched_at"]) < _CACHE_TTL:
        return {"rate": _CACHE["rate"], "source": "cache",
                "fetched_at": _CACHE["fetched_at"].isoformat()}

    # 3. Live fetch.
    live = await _fetch_live_rate()
    if live is not None:
        _CACHE.update({"rate": live, "fetched_at": now, "source": "live"})
        return {"rate": live, "source": "live", "fetched_at": now.isoformat()}

    # 4. Fallback.
    fallback = await _admin_rate("hubtel_usd_ghs_fallback_rate")
    rate = fallback if fallback is not None else _DEFAULT_FALLBACK
    return {"rate": rate, "source": "fallback", "fetched_at": None}


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
