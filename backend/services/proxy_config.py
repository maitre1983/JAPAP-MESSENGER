"""
iter239a4 — Shared Fixie proxy helper (STRICTLY ADDITIVE).

All outbound calls to whitelisted vendor APIs (Paystack, Hubtel, FX
provider) need to exit through the Fixie static-IP proxy so the vendor
sees a fixed source IP. This module centralises the env-var lookup so
all services use the same configuration.

httpx accepts the `proxies={"http://": url, "https://": url}` shape
(plural form is deprecated but still works in httpx ≤ 0.27; newer
versions accept `proxy=url`). We return the dict form so existing
Hubtel code continues to work unchanged.

For `requests` library compatibility, also expose `get_proxies_requests()`
which returns `{"http": url, "https": url}` (singular keys).

Set the env var `FIXIE_URL` (already in `/app/backend/.env`):
  FIXIE_URL=http://fixie:eYNfPeo0IplrX2d@criterium.usefixie.com:80
"""
from __future__ import annotations

import os


def get_proxies() -> dict:
    """httpx-compatible proxies mapping (legacy format for httpx ≤ 0.27).
    For httpx ≥ 0.28, callers should use `get_proxy_url()` with the
    `proxy=` kwarg instead. Empty dict when Fixie not configured."""
    fixie = os.environ.get("FIXIE_URL")
    if not fixie:
        return {}
    return {"http://": fixie, "https://": fixie}


def get_proxy_url() -> str | None:
    """Returns the single Fixie URL or None. Compatible with httpx ≥ 0.28
    (`httpx.AsyncClient(proxy=url)`) and matches the `proxies` shape used
    by the `requests` library when passed as `{"http": url, "https": url}`."""
    return os.environ.get("FIXIE_URL") or None


def get_hubtel_proxy() -> str | None:
    """iter239k — Returns a Fixie proxy URL that forces egress through the
    single IP whitelisted by Hubtel for `rmp.hubtel.com`. Fixie's default
    hostname (`criterium.usefixie.com`) load-balances between 2 IPs:

      • `52.5.155.132` → whitelisted on Hubtel (both rmp + smp)
      • `52.87.82.133` → whitelisted on smp.hubtel.com ONLY (rmp blocks it)

    By pointing the proxy URL at the whitelisted IP directly we bypass the
    DNS load-balancing and guarantee deterministic egress. ALL Hubtel
    callers (deposit, withdraw, verify, cron, admin test) must use this
    helper. Paystack and FX continue to use `get_proxy_url()` (any IP).

    Override via env: `FIXIE_URL_HUBTEL` (full URL with creds). If unset,
    falls back to `FIXIE_URL` so dev/test setups without the override
    still work."""
    forced = os.environ.get("FIXIE_URL_HUBTEL")
    if forced:
        return forced
    return os.environ.get("FIXIE_URL") or None


def get_proxies_requests() -> dict:
    """`requests`-compatible proxies mapping (singular http/https keys)."""
    fixie = os.environ.get("FIXIE_URL")
    if not fixie:
        return {}
    return {"http": fixie, "https": fixie}


__all__ = ["get_proxies", "get_proxy_url", "get_proxies_requests", "get_hubtel_proxy"]
