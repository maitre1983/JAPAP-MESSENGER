"""
JAPAP — Currency auto-detection for signup (iter74).

Counterpart of `language_detector.py`. Same priority ladder, same
proxy headers — the two together mean a new user coming from Lagos
sees JAPAP in Yoruba, with their wallet pre-tuned to NGN, from
second 0.

Priority order:
  1. `detected_currency` sent from the frontend (derived from
     navigator/Intl or from our `/api/currency/detect` endpoint).
  2. User-supplied `country_code` on the signup form.
  3. Upstream proxy headers (CF-IPCountry, X-Country-Code, etc.).
  4. Fallback: `USD`.

The single source of truth for the country→currency mapping lives in
`routes.currency.COUNTRY_TO_CURRENCY`. We intentionally DON'T redefine
it here — drift would be a long-term bug magnet.
"""
from __future__ import annotations

from typing import Optional


def _normalise_currency(code: Optional[str]) -> str:
    if not code:
        return ""
    c = str(code).upper().strip()
    return c if len(c) == 3 and c.isalpha() else ""


def _country_to_currency(country: str) -> Optional[str]:
    """Deferred import of the mapping to avoid a circular import when this
    module is pulled from routes/auth.py."""
    from routes.currency import COUNTRY_TO_CURRENCY
    cc = (country or "").upper().strip()[:2]
    if not cc:
        return None
    return COUNTRY_TO_CURRENCY.get(cc)


def detect_user_currency(
    *,
    detected_currency: Optional[str] = None,
    country_code: Optional[str] = None,
    proxy_country: Optional[str] = None,
) -> Optional[str]:
    """Pick the best preferred_currency for a new signup.
    Returns an ISO-4217 code or None if nothing sensible was inferred."""
    # 1) Explicit hint from the frontend (already validated 3-letter ISO)
    c = _normalise_currency(detected_currency)
    if c:
        # Only honor codes we actually know about — otherwise we'd later
        # silently fail every FX conversion.
        from routes.currency import CURRENCY_SYMBOLS, FALLBACK_RATES
        if c in CURRENCY_SYMBOLS or c in FALLBACK_RATES:
            return c

    # 2) User-supplied country
    if country_code:
        ccy = _country_to_currency(country_code)
        if ccy:
            return ccy

    # 3) Proxy-injected country header
    if proxy_country and len(proxy_country) == 2 and proxy_country.isalpha():
        ccy = _country_to_currency(proxy_country)
        if ccy:
            return ccy

    return None
