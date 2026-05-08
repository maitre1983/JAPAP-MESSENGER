"""
JAPAP — Dynamic currency service
================================
- Internal base currency is configurable (defaults to USD).
- Rates are cached in the `currency_rates` table, refreshed from
  `exchangerate.host` (free, no API key required). Falls back to a hardcoded
  table if the upstream call fails.
- Country → currency mapping covers every supported country. Unknown
  countries fall back to the base currency (USD).

Endpoints:
- GET /api/currency/rates          (cached; refreshes if older than 6h)
- GET /api/currency/detect         (returns {country, currency, symbol, rate})
- POST /api/currency/refresh       (admin-only; forces refresh)
"""
import logging
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request
from database import get_pool
from routes.auth import require_admin
from services.settings_service import get_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/currency", tags=["currency"])

# ISO-3166 country code → local currency code
COUNTRY_TO_CURRENCY: dict[str, str] = {
    # West/Central Africa CFA
    "CM": "XAF", "GA": "XAF", "CG": "XAF", "CD": "CDF", "CF": "XAF", "TD": "XAF", "GQ": "XAF",
    "BJ": "XOF", "BF": "XOF", "CI": "XOF", "GW": "XOF", "ML": "XOF", "NE": "XOF", "SN": "XOF", "TG": "XOF",
    # Africa local currencies
    "NG": "NGN", "GH": "GHS", "KE": "KES", "UG": "UGX", "TZ": "TZS", "RW": "RWF",
    "ET": "ETB", "ZA": "ZAR", "MA": "MAD", "DZ": "DZD", "TN": "TND", "EG": "EGP",
    "SD": "SDG", "ZW": "ZWL", "AO": "AOA", "MZ": "MZN", "MG": "MGA", "MU": "MUR",
    "BI": "BIF", "DJ": "DJF", "ER": "ERN", "GM": "GMD", "LR": "LRD", "SL": "SLE",
    "LY": "LYD", "LS": "LSL", "SZ": "SZL", "NA": "NAD", "BW": "BWP", "ZM": "ZMW", "MW": "MWK",
    "SO": "SOS", "SS": "SSP", "CV": "CVE", "KM": "KMF", "ST": "STN",
    # Europe EUR
    "DE": "EUR", "FR": "EUR", "ES": "EUR", "IT": "EUR", "PT": "EUR", "NL": "EUR",
    "BE": "EUR", "IE": "EUR", "AT": "EUR", "FI": "EUR", "GR": "EUR", "LU": "EUR",
    "MT": "EUR", "CY": "EUR", "SK": "EUR", "SI": "EUR", "EE": "EUR", "LV": "EUR",
    "LT": "EUR", "HR": "EUR", "MC": "EUR", "AD": "EUR", "SM": "EUR", "VA": "EUR",
    "GB": "GBP", "CH": "CHF", "SE": "SEK", "NO": "NOK", "DK": "DKK",
    "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON", "BG": "BGN",
    "RS": "RSD", "UA": "UAH", "RU": "RUB", "TR": "TRY", "IS": "ISK",
    # Americas
    "US": "USD", "CA": "CAD", "MX": "MXN", "BR": "BRL", "AR": "ARS", "CL": "CLP",
    "CO": "COP", "PE": "PEN", "VE": "VES", "UY": "UYU", "PY": "PYG", "BO": "BOB",
    "EC": "USD", "GT": "GTQ", "HN": "HNL", "NI": "NIO", "CR": "CRC", "PA": "PAB",
    "DO": "DOP", "CU": "CUP", "HT": "HTG", "JM": "JMD", "TT": "TTD", "BS": "BSD",
    "BB": "BBD", "BZ": "BZD", "SR": "SRD", "GY": "GYD",
    # Asia
    "IN": "INR", "CN": "CNY", "JP": "JPY", "KR": "KRW", "ID": "IDR", "TH": "THB",
    "VN": "VND", "PH": "PHP", "MY": "MYR", "SG": "SGD", "PK": "PKR", "BD": "BDT",
    "LK": "LKR", "NP": "NPR", "MM": "MMK", "KH": "KHR", "LA": "LAK",
    "AE": "AED", "SA": "SAR", "QA": "QAR", "KW": "KWD", "BH": "BHD", "OM": "OMR",
    "IQ": "IQD", "IR": "IRR", "IL": "ILS", "JO": "JOD", "LB": "LBP", "SY": "SYP",
    "YE": "YER", "HK": "HKD", "TW": "TWD", "MO": "MOP", "MN": "MNT", "AF": "AFN",
    "UZ": "UZS", "KZ": "KZT", "KG": "KGS", "TJ": "TJS", "TM": "TMT", "AZ": "AZN",
    "AM": "AMD", "GE": "GEL", "BT": "BTN", "MV": "MVR",
    # Oceania
    "AU": "AUD", "NZ": "NZD", "FJ": "FJD", "PG": "PGK", "SB": "SBD", "VU": "VUV",
    "WS": "WST", "TO": "TOP",
}

CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$", "EUR": "€", "GBP": "£", "XAF": "FCFA", "XOF": "CFA", "NGN": "₦",
    "GHS": "GH₵", "KES": "KSh", "UGX": "USh", "TZS": "TSh", "RWF": "FRw",
    "ETB": "Br", "ZAR": "R", "MAD": "MAD", "EGP": "E£", "INR": "₹", "CNY": "¥",
    "JPY": "¥", "KRW": "₩", "CAD": "C$", "AUD": "A$", "BRL": "R$", "MXN": "$",
    "CHF": "Fr", "SEK": "kr", "NOK": "kr", "DKK": "kr", "TRY": "₺", "RUB": "₽",
    "AED": "د.إ", "SAR": "﷼", "ILS": "₪", "HKD": "HK$", "SGD": "S$", "THB": "฿",
    "IDR": "Rp", "MYR": "RM", "PHP": "₱", "VND": "₫", "PKR": "₨", "BDT": "৳",
}

# Hardcoded fallback rates (per 1 USD) — used if upstream API fails.
# Approximate Q1 2026 values. Refreshed live when possible.
FALLBACK_RATES: dict[str, float] = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "XAF": 605.0, "XOF": 605.0, "NGN": 1560.0,
    "GHS": 15.5, "KES": 129.0, "UGX": 3850.0, "TZS": 2550.0, "RWF": 1380.0,
    "ETB": 124.0, "ZAR": 18.2, "MAD": 9.9, "EGP": 49.5, "INR": 87.5, "CNY": 7.25,
    "JPY": 156.0, "KRW": 1440.0, "CAD": 1.42, "AUD": 1.53, "BRL": 5.95, "MXN": 20.3,
    "CHF": 0.89, "SEK": 10.6, "NOK": 11.2, "DKK": 6.88, "TRY": 35.4, "RUB": 100.0,
    "AED": 3.67, "SAR": 3.75, "ILS": 3.58, "HKD": 7.78, "SGD": 1.35, "THB": 34.9,
    "IDR": 16200.0, "MYR": 4.70, "PHP": 58.8, "VND": 25300.0, "PKR": 280.0,
    "BDT": 123.0, "CDF": 2850.0, "BWP": 13.9, "NAD": 18.2, "MUR": 46.5, "MZN": 64.0,
    "AOA": 910.0, "ZMW": 27.2, "MWK": 1740.0, "SOS": 572.0, "LYD": 4.87, "DZD": 136.0,
    "TND": 3.18, "LKR": 292.0, "NPR": 140.0, "MMK": 2100.0, "KHR": 4100.0,
    "LAK": 21800.0, "TWD": 32.6, "MOP": 8.0, "AFN": 74.0,
    "UZS": 12900.0, "KZT": 515.0, "PLN": 4.07, "CZK": 24.1, "HUF": 392.0,
    "RON": 4.57, "BGN": 1.80, "UAH": 41.5, "ISK": 140.0,
    "COP": 4050.0, "PEN": 3.76, "CLP": 1010.0, "ARS": 1020.0, "VES": 60.0,
}


async def _ensure_rates_table_hot():
    """Ensure currency_rates table has data. Seeds fallback if empty."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM currency_rates")
        if count and count > 0:
            return
        logger.info("Seeding currency rates from fallback table (%d entries)", len(FALLBACK_RATES))
        for code, rate in FALLBACK_RATES.items():
            await conn.execute("""
                INSERT INTO currency_rates (code, rate_vs_usd, source, updated_at)
                VALUES ($1, $2, 'fallback', NOW())
                ON CONFLICT (code) DO NOTHING
            """, code, rate)


async def _refresh_rates_from_api():
    """Fetch live rates from exchangerate.host (free, no key). Updates DB."""
    url = "https://api.exchangerate.host/latest?base=USD"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
        if r.status_code != 200:
            logger.warning("exchangerate.host status=%s", r.status_code)
            return False
        data = r.json()
        rates = data.get("rates") or {}
        if not rates:
            return False
        pool = await get_pool()
        async with pool.acquire() as conn:
            for code, rate in rates.items():
                if not isinstance(rate, (int, float)) or rate <= 0:
                    continue
                await conn.execute("""
                    INSERT INTO currency_rates (code, rate_vs_usd, source, updated_at)
                    VALUES ($1, $2, 'exchangerate.host', NOW())
                    ON CONFLICT (code) DO UPDATE SET
                        rate_vs_usd = EXCLUDED.rate_vs_usd,
                        source = 'exchangerate.host',
                        updated_at = NOW()
                """, code, float(rate))
        logger.info("Refreshed %d currency rates from exchangerate.host", len(rates))
        return True
    except Exception as e:
        logger.warning("Rate refresh failed: %s", e)
        return False


async def _rates_are_stale(max_age_h: int = 6) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT MAX(updated_at) AS m FROM currency_rates")
    if not row or not row["m"]:
        return True
    age = datetime.now(timezone.utc) - row["m"].replace(tzinfo=timezone.utc)
    return age > timedelta(hours=max_age_h)


async def _get_rate(code: str) -> float:
    code = (code or "USD").upper()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT rate_vs_usd FROM currency_rates WHERE code = $1", code)
    if row and row["rate_vs_usd"]:
        return float(row["rate_vs_usd"])
    return float(FALLBACK_RATES.get(code, 1.0))


@router.get("/rates")
async def get_rates():
    """Cached currency rates. Auto-refreshes if stale (>6h)."""
    await _ensure_rates_table_hot()
    if await _rates_are_stale():
        await _refresh_rates_from_api()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT code, rate_vs_usd, source, updated_at FROM currency_rates ORDER BY code")
    return {
        "base": "USD",
        "rates": {r["code"]: float(r["rate_vs_usd"]) for r in rows},
        "symbols": CURRENCY_SYMBOLS,
        "country_to_currency": COUNTRY_TO_CURRENCY,
        "updated_at": max((r["updated_at"] for r in rows), default=datetime.now(timezone.utc)).isoformat() if rows else None,
    }


@router.get("/detect")
async def detect_currency(request: Request, country: str | None = None):
    """Detect user's local currency. Honors admin `currency_force` and
    `currency_detection_enabled` flags. If detection is disabled → USD.
    `country` query param overrides IP detection (used by logged-in users
    whose profile country is known).
    """
    from services.settings_service import get_bool
    detect_enabled = await get_bool("currency_detection_enabled", True)
    force = (await get_setting("currency_force") or "").upper().strip()

    if force:
        rate = await _get_rate(force)
        return {
            "country": None, "currency": force,
            "symbol": CURRENCY_SYMBOLS.get(force, force), "rate_vs_usd": rate,
            "source": "forced_by_admin",
        }

    if not detect_enabled:
        rate = await _get_rate("USD")
        return {"country": None, "currency": "USD", "symbol": "$", "rate_vs_usd": rate, "source": "detection_disabled"}

    # Country detection: explicit param > Cloudflare header > remote call
    cc = (country or "").upper().strip()
    if not cc:
        cc = (request.headers.get("cf-ipcountry") or "").upper().strip()
    if not cc:
        # Best-effort: ask ipapi.co without failing on outage
        ip = request.client.host if request.client else None
        if ip and ip not in ("127.0.0.1", "localhost", "unknown"):
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    r = await client.get(f"https://ipapi.co/{ip}/country/")
                if r.status_code == 200:
                    cc = r.text.strip().upper()[:2]
            except Exception:
                cc = ""
    currency = COUNTRY_TO_CURRENCY.get(cc, "USD")
    rate = await _get_rate(currency)
    return {
        "country": cc or None,
        "currency": currency,
        "symbol": CURRENCY_SYMBOLS.get(currency, currency),
        "rate_vs_usd": rate,
        "source": "ip" if cc else "fallback_usd",
    }


@router.post("/refresh")
async def force_refresh(request: Request):
    await require_admin(request)
    await _ensure_rates_table_hot()
    ok = await _refresh_rates_from_api()
    return {"status": "ok" if ok else "failed"}
