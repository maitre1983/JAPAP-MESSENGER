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
# iter239a4 — Delegated to shared `services.proxy_config` so all
# vendor-API services (Paystack, Hubtel, FX) use a single helper.
from services.proxy_config import get_proxies  # noqa: F401  (re-export)


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


# Hubtel channel mapping. Source: NCA Ghana number plan (5-char prefixes
# = `233` + first 2 digits of the subscriber number). Aligned with the
# frontend `OPERATOR_PREFIXES` table in `HubtelMomoWidget.jsx`. Last
# refresh: iter239a2 — 2026-05-10 (added 23358 to AirtelTigo).
_CHANNEL_PREFIXES = {
    "mtn-gh":      ["23324", "23325", "23353", "23354", "23355", "23359"],
    "vodafone-gh": ["23320", "23350"],
    "tigo-gh":     ["23326", "23327", "23356", "23357", "23358"],
}


def normalize_msisdn(msisdn: str) -> str:
    """
    iter239a2 — Normalises any Ghana MSISDN to international format
    `233XXXXXXXXX` (12 digits). Accepts:
      • 233XXXXXXXXX  (already international) → unchanged
      • +233XXXXXXXXX (with leading +)         → strips the +
      • 0XXXXXXXXX    (local 10-digit format)  → replaces 0 with 233

    Spaces, dashes, and parentheses are stripped first. Non-Ghana inputs
    pass through unchanged (caller validates with `is_ghana_number`).
    """
    if not isinstance(msisdn, str):
        return ""
    s = msisdn.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if s.startswith("+233"):
        return s[1:]
    if s.startswith("0") and len(s) == 10 and s[1:].isdigit():
        return "233" + s[1:]
    return s


def is_ghana_number(msisdn: str) -> bool:
    """Validates a Ghana MSISDN: starts with `233`, exactly 12 digits.
    Auto-normalises common variants before checking (iter239a2)."""
    s = normalize_msisdn(msisdn)
    return (
        isinstance(s, str)
        and s.startswith("233")
        and len(s) == 12
        and s.isdigit()
    )


def detect_channel(msisdn: str) -> str | None:
    """Maps a Ghana MSISDN to a Hubtel `Channel` value (mtn-gh /
    vodafone-gh / tigo-gh). Returns None if the prefix is unknown —
    the caller should reject the request with 400 unknown_network.
    iter239a2: auto-normalises local-format numbers first."""
    s = normalize_msisdn(msisdn)
    if not is_ghana_number(s):
        return None
    for channel, prefixes in _CHANNEL_PREFIXES.items():
        for pfx in prefixes:
            if s.startswith(pfx):
                return channel
    return None


__all__ = [
    "get_proxies",
    "get_hubtel_auth", "get_collection_account", "get_disbursement_account",
    "get_callback_base_url", "get_deposit_limits", "get_withdrawal_limits",
    "generate_client_reference",
    "normalize_msisdn", "is_ghana_number", "detect_channel",
]
