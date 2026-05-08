"""
JAPAP — Language auto-detection for signup (iter73).

Priority order used by `detect_user_language()`:
  1. `detected_lang` sent from the frontend (navigator.language → 2-letter ISO).
     Only honored if it's in our 11-language whitelist.
  2. User-supplied `country_code` on the signup form (e.g., "NG", "CD").
  3. Upstream proxy headers (CF-IPCountry for Cloudflare, X-Country-Code for
     some ingress configs). These are trusted only when the header value is
     exactly 2 ASCII letters — otherwise ignored.
  4. HTTP `Accept-Language` header parsed for the first supported locale.
  5. Fallback: `fr` (JAPAP's historical default — most users are FR-speaking).

The mapping below targets JAPAP's real user distribution (CFA Central/West
Africa, East Africa, South Asia, LatAm, Europe). When multiple languages
are spoken in one country, we pick the dominant one for mobile-first
consumer apps, not the constitutional one.
"""
from __future__ import annotations

from typing import Optional

from constants import SUPPORTED_LANGS

# ── Country → default UI language ────────────────────────────────────────
# Coverage: every country whose users we've historically seen, plus top-of-
# funnel markets. Unlisted countries fall through to Accept-Language.
COUNTRY_LANG = {
    # Francophone Africa (core market)
    "CM": "fr", "CI": "fr", "SN": "fr", "BF": "fr", "ML": "fr", "TG": "fr",
    "BJ": "fr", "NE": "fr", "GA": "fr", "CG": "fr", "CD": "fr", "TD": "fr",
    "GN": "fr", "MG": "fr", "DJ": "fr", "CF": "fr", "RW": "fr", "BI": "fr",
    "MR": "fr", "KM": "fr", "MU": "fr", "SC": "fr",
    # France + Belgium + French-speaking Europe
    "FR": "fr", "BE": "fr", "MC": "fr", "LU": "fr",
    # Lingala-dominant hubs (Kinshasa, Brazzaville).
    # Default stays FR for DRC/Congo because it's the lingua franca of UI;
    # we leave LN as an opt-in via the switcher.
    # Anglophone Africa
    "NG": "yo",        # Yoruba is dominant in Lagos/SW; users can switch to EN
    "GH": "en", "KE": "en", "UG": "en", "ZA": "en", "ZM": "en", "ZW": "en",
    "MW": "en", "NA": "en", "BW": "en", "LS": "en", "SZ": "en", "SL": "en",
    "LR": "en", "GM": "en", "SS": "en", "ET": "en", "ER": "en", "SO": "en",
    # Swahili-dominant
    "TZ": "sw", "KE_SW": "sw",  # KE double-mapped: primary EN but SW option
    # Lusophone
    "PT": "pt", "BR": "pt", "AO": "pt", "MZ": "pt", "CV": "pt", "GW": "pt",
    "ST": "pt", "TL": "pt",
    # Hispanophone
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "PE": "es", "VE": "es",
    "CL": "es", "EC": "es", "BO": "es", "PY": "es", "UY": "es", "CR": "es",
    "PA": "es", "DO": "es", "GT": "es", "HN": "es", "NI": "es", "SV": "es",
    "CU": "es",
    # Arabic-speaking (MENA)
    "SA": "ar", "EG": "ar", "MA": "ar", "DZ": "ar", "TN": "ar", "LY": "ar",
    "SD": "ar", "AE": "ar", "QA": "ar", "KW": "ar", "BH": "ar", "OM": "ar",
    "JO": "ar", "LB": "ar", "SY": "ar", "IQ": "ar", "YE": "ar", "PS": "ar",
    # South Asia
    "IN": "hi",        # Hindi as dominant default for India
    "BD": "bn",        # Bengali
    "LK": "ta",        # Tamil-speaking North/East; users can switch
    # Anglophone defaults
    "GB": "en", "US": "en", "CA": "en", "AU": "en", "NZ": "en", "IE": "en",
}


def _normalise(code: Optional[str]) -> str:
    if not code:
        return ""
    return str(code).lower().strip()[:2]


def _first_supported(codes: list[str]) -> Optional[str]:
    for c in codes:
        n = _normalise(c)
        if n and n in SUPPORTED_LANGS:
            return n
    return None


def _parse_accept_language(header: str) -> list[str]:
    """Parse the `Accept-Language: fr-FR,fr;q=0.9,en;q=0.8,…` header and
    return the 2-letter codes in priority order. Ignores q= weighting —
    we only need the first supported hit."""
    if not header:
        return []
    out: list[str] = []
    for part in header.split(","):
        tag = part.split(";", 1)[0].strip()
        short = tag.split("-", 1)[0].lower()
        if short and short not in out:
            out.append(short)
    return out


def detect_user_language(
    *,
    detected_lang: Optional[str] = None,
    country_code: Optional[str] = None,
    proxy_country: Optional[str] = None,
    accept_language: Optional[str] = None,
) -> Optional[str]:
    """Pick the best UI language for a new signup.
    Returns a 2-letter code guaranteed to be in SUPPORTED_LANGS, or None
    if nothing sensible could be derived (caller should then keep its
    existing default)."""
    # 1) Explicit hint from the browser
    if detected_lang:
        n = _normalise(detected_lang)
        if n in SUPPORTED_LANGS:
            return n
    # 2) User-supplied country
    if country_code:
        n = COUNTRY_LANG.get(country_code.upper().strip()[:2])
        if n and n in SUPPORTED_LANGS:
            return n
    # 3) Proxy/CDN-injected country
    if proxy_country and len(proxy_country) == 2 and proxy_country.isalpha():
        n = COUNTRY_LANG.get(proxy_country.upper())
        if n and n in SUPPORTED_LANGS:
            return n
    # 4) Accept-Language fallback
    if accept_language:
        preferred = _first_supported(_parse_accept_language(accept_language))
        if preferred:
            return preferred
    return None


def language_from_request_headers(headers) -> Optional[str]:
    """Convenience wrapper used by routes — reads standard proxy + AL
    headers from a FastAPI `Request.headers` object."""
    proxy = (
        headers.get("cf-ipcountry")
        or headers.get("x-country-code")
        or headers.get("x-appengine-country")
    )
    al = headers.get("accept-language", "")
    return detect_user_language(proxy_country=proxy, accept_language=al)
