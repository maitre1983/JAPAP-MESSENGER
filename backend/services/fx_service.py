"""
iter239a3 — Centralized USD↔FX rate service (STRICTLY ADDITIVE).

Single source of truth for any USD↔foreign-currency conversion used by
the wallet (Paystack GHS, Hubtel MoMo GHS, future XOF/XAF, etc.).

USD → GHS priority chain:
  1. `system_settings.usd_ghs_rate`               (global admin override)
  2. `system_settings.paystack_usd_ghs_rate`      (legacy Paystack manual rate)
  3. `system_settings.hubtel_usd_ghs_rate`        (legacy Hubtel manual rate)
  4. In-memory cache (1 hour TTL)                 (last successful live fetch)
  5. open.er-api.com/v6/latest/USD                (free live API, no key)
  6. `system_settings.usd_ghs_fallback_rate`      (admin fallback)
  7. `system_settings.paystack_usd_ghs_fallback_rate` (legacy)
  8. `system_settings.hubtel_usd_ghs_fallback_rate`   (legacy)
  9. 14.50                                        (hard-coded last resort)

The cache is module-level (single process). Multi-pod deployments each
maintain their own cache; that's fine — open.er-api.com returns the same
value regardless of where we ask.

Existing services (`hubtel_fx`, `paystack_service`) can opt in by
delegating to `get_usd_to_ghs_info()` here, keeping their existing
public API for backwards compatibility.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from services.settings_service import get_setting

logger = logging.getLogger(__name__)

_CACHE: dict = {"rate": None, "fetched_at": None}
_CACHE_TTL = timedelta(hours=1)
_DEFAULT_FALLBACK = 14.50
_TIMEOUT = 5.0


async def _admin_rate(key: str) -> float | None:
    raw = await get_setting(key)
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


async def _live() -> float | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get("https://open.er-api.com/v6/latest/USD")
        if r.status_code != 200:
            return None
        rate = (r.json() or {}).get("rates", {}).get("GHS")
        v = float(rate) if rate else None
        return v if v and v > 0 else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[fx-service] live fetch failed: %s", e)
        return None


async def get_usd_to_ghs_info() -> dict:
    """Returns `{rate: float, source: str, fetched_at: str | None, key: str | None}`.

    `key` reveals which `system_settings` key supplied the value when the
    source is "manual" or "fallback" — useful for the admin UI to show
    which legacy key is still in use and prompt cleanup."""
    # 1-3 — Manual admin overrides, global first, then legacy.
    for k in ("usd_ghs_rate", "paystack_usd_ghs_rate", "hubtel_usd_ghs_rate"):
        v = await _admin_rate(k)
        if v is not None:
            return {"rate": v, "source": "manual", "fetched_at": None, "key": k}

    # 4 — Cache.
    now = datetime.now(timezone.utc)
    if (_CACHE["rate"] and _CACHE["fetched_at"]
            and (now - _CACHE["fetched_at"]) < _CACHE_TTL):
        return {"rate": _CACHE["rate"], "source": "cache",
                "fetched_at": _CACHE["fetched_at"].isoformat(), "key": None}

    # 5 — Live.
    live = await _live()
    if live is not None:
        _CACHE.update({"rate": live, "fetched_at": now})
        return {"rate": live, "source": "live",
                "fetched_at": now.isoformat(), "key": None}

    # 6-8 — Admin fallback chain.
    for k in ("usd_ghs_fallback_rate",
              "paystack_usd_ghs_fallback_rate",
              "hubtel_usd_ghs_fallback_rate"):
        v = await _admin_rate(k)
        if v is not None:
            return {"rate": v, "source": "fallback",
                    "fetched_at": None, "key": k}

    # 9 — Hard-coded last resort.
    return {"rate": _DEFAULT_FALLBACK, "source": "fallback",
            "fetched_at": None, "key": None}


async def get_usd_to_ghs_rate() -> float:
    info = await get_usd_to_ghs_info()
    return float(info["rate"])


async def convert_usd_to_ghs(amount_usd: float) -> dict:
    info = await get_usd_to_ghs_info()
    amount_ghs = round(float(amount_usd) * info["rate"], 2)
    return {
        "rate": info["rate"],
        "source": info["source"],
        "fetched_at": info.get("fetched_at"),
        "key": info.get("key"),
        "amount_usd": float(amount_usd),
        "amount_ghs": amount_ghs,
    }


__all__ = [
    "get_usd_to_ghs_info",
    "get_usd_to_ghs_rate",
    "convert_usd_to_ghs",
]
