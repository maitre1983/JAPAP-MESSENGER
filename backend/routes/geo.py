"""
JAPAP — Geo-IP Detection + Countries List
==========================================
Returns the visitor's country (via Cloudflare / X-Forwarded-For headers, falling
back to the public ipapi.co service) and the suggested UI language based on it.
Also exposes the full countries list for the registration form.
"""
import logging
import httpx
from fastapi import APIRouter, Request

from constants import COUNTRIES, COUNTRY_TO_LANG

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/geo", tags=["geo"])


def _client_ip(request: Request) -> str:
    """Extract the real client IP honoring common proxy headers."""
    # Cloudflare
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    # Standard proxy chain
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else ""


@router.get("/detect")
async def detect_geo(request: Request):
    """Detect the visitor's country + suggested language.
    Prefers the CF-IPCountry header (zero-latency, already resolved by Cloudflare).
    Falls back to ipapi.co for pods without Cloudflare. Never raises — always returns
    a best-effort result so the UI can pre-fill the registration form.
    """
    # Cloudflare resolves the country server-side and ships it in this header.
    cf_country = (request.headers.get("cf-ipcountry") or "").upper().strip()
    country = cf_country if cf_country and cf_country != "XX" else ""
    ip = _client_ip(request)

    if not country and ip and not ip.startswith(("10.", "127.", "192.168.", "172.")):
        country = await _lookup_country_by_ip(ip)

    suggested_lang = COUNTRY_TO_LANG.get(country, "en")
    return {
        "country_code": country or "",
        "suggested_lang": suggested_lang,
        "ip": ip,
    }


async def _lookup_country_by_ip(ip: str) -> str:
    """Best-effort country lookup via free public geo-IP services.
    Tries providers in order and returns the first 2-letter ISO code found.
    Never raises — returns empty string on total failure.
    """
    # Providers that return a plain JSON with a country field, no API key required.
    # 1) api.country.is — minimal, reliable, no stated rate limit
    # 2) ipwho.is — 10k/month free
    # 3) freeipapi.com — generous free tier
    providers = [
        ("https://api.country.is/{ip}", "country"),
        ("https://ipwho.is/{ip}", "country_code"),
        ("https://freeipapi.com/api/json/{ip}", "countryCode"),
    ]
    for url_tpl, field in providers:
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                r = await client.get(url_tpl.format(ip=ip))
                if r.status_code == 200:
                    data = r.json()
                    c = str(data.get(field, "") or "").upper().strip()
                    if len(c) == 2 and c.isalpha():
                        return c
        except Exception as e:
            logger.debug(f"geo provider {url_tpl} failed: {e}")
            continue
    return ""


@router.get("/countries")
async def list_countries():
    """Return the full 195-country list for the registration form."""
    return {"countries": COUNTRIES}
