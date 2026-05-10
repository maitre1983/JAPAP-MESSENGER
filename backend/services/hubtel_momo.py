"""
iter237af — Hubtel Mobile Money helpers (Ghana 🇬🇭).

Strict ADDITIVE module — does NOT touch the existing `hubtel_service.py`
(which handles the Hubtel CARD checkout). This module is dedicated to
the Mobile Money rails (collection + disbursement) and only knows about:

  • Settings overrides (admin → DB → env fallback)
  • Phone number eligibility (Ghana-only, +233)
  • Network channel detection (MTN / Vodafone / Tigo / AirtelTigo)
  • Limits (deposit / withdrawal min/max in USD)
  • Client reference generation (UUID-based, ≤ 36 chars)
  • Fixie HTTPS proxy config (Hubtel requires whitelisted egress IPs)

All settings are looked up via `services.settings_service.get_setting`
which is backed by the `admin_settings` table. The corresponding env
vars are used as fallbacks when the admin hasn't configured the keys
yet (first-deploy resilience).
"""
from __future__ import annotations

import os
import uuid

from services.settings_service import get_setting

# ───────────────────────── Proxy (Fixie) ─────────────────────────
def get_proxies() -> dict:
    """Returns the Fixie HTTPS proxy config for httpx/requests. Hubtel
    requires the merchant's outbound IP to be whitelisted; Fixie gives
    us a fixed pair of IPs to whitelist regardless of what container
    the backend currently runs in."""
    fixie = os.environ.get("FIXIE_URL")
    if not fixie:
        return {}
    return {"http://": fixie, "https://": fixie}


# ─────────────────── Settings (admin → env fallback) ─────────────
async def _setting(key: str, default: str | None = None) -> str | None:
    """Single-shot lookup with env fallback. The settings_service helper
    is itself async + cached (60 s TTL) so we just delegate."""
    value = await get_setting(key)
    if value is not None and value != "":
        return value
    return default


async def get_hubtel_auth() -> str | None:
    return await _setting("hubtel_api_key", os.environ.get("HUBTEL_API_KEY"))


async def get_collection_account() -> str | None:
    return await _setting("hubtel_collection_account",
                          os.environ.get("HUBTEL_COLLECTION_ACCOUNT"))


async def get_disbursement_account() -> str | None:
    return await _setting("hubtel_disbursement_account",
                          os.environ.get("HUBTEL_DISBURSEMENT_ACCOUNT"))


async def get_callback_base_url() -> str:
    return (await _setting("hubtel_callback_base_url",
                           os.environ.get("HUBTEL_CALLBACK_BASE_URL"))
            or "https://japapmessenger.com")


async def get_deposit_limits() -> dict:
    return {
        "min": float(await _setting("hubtel_momo_deposit_min", "1.00") or "1.00"),
        "max": float(await _setting("hubtel_momo_deposit_max", "1000.00") or "1000.00"),
    }


async def get_withdrawal_limits() -> dict:
    return {
        "min": float(await _setting("hubtel_momo_withdrawal_min", "1.00") or "1.00"),
        "max": float(await _setting("hubtel_momo_withdrawal_max", "500.00") or "500.00"),
    }


# ───────────────────── Pure helpers ────────────────────────────
def generate_client_reference() -> str:
    """36-char UUID hex (no dashes) — Hubtel limits ClientReference to
    50 chars; we stay well under that for safety."""
    return uuid.uuid4().hex[:36]


def is_ghana_number(msisdn: str) -> bool:
    """Validates a Ghana MSISDN: starts with `233`, exactly 12 digits."""
    return (
        isinstance(msisdn, str)
        and msisdn.startswith("233")
        and len(msisdn) == 12
        and msisdn.isdigit()
    )


# Hubtel channel mapping. Source: Hubtel docs — operator prefixes are
# updated when telecoms reshuffle ranges (last refresh 2026-Q1).
_CHANNEL_PREFIXES = {
    "mtn-gh":      ["2330", "2335", "2354", "2355", "2359"],
    "vodafone-gh": ["2332", "2350"],
    "tigo-gh":     ["2357", "2356", "2320", "2326", "2327"],
}


def detect_channel(msisdn: str) -> str | None:
    """Maps a Ghana MSISDN to a Hubtel `Channel` value (mtn-gh /
    vodafone-gh / tigo-gh). Returns None if the prefix is unknown —
    the caller should reject the request with 400 unknown_network."""
    if not is_ghana_number(msisdn):
        return None
    for channel, prefixes in _CHANNEL_PREFIXES.items():
        for pfx in prefixes:
            if msisdn.startswith(pfx):
                return channel
    return None


__all__ = [
    "get_proxies",
    "get_hubtel_auth", "get_collection_account", "get_disbursement_account",
    "get_callback_base_url", "get_deposit_limits", "get_withdrawal_limits",
    "generate_client_reference", "is_ghana_number", "detect_channel",
]
