"""
JAPAP — Currency Conversion Service (iter158)
==============================================
Centralised USD ↔ local-currency conversion. Single source of truth for
every wallet / deposit / withdrawal path.

Invariants:
  • Wallet balance, transaction `amount`, `fee` → ALWAYS stored in USD.
  • Display currency = user preference (`users.display_currency`), with
    "local" falling back to IP-detected country.
  • Provider currency = whatever the payment provider charges:
      - Hubtel    → GHS (Ghana Cedis)
      - NowPayments invoice → USD (provider converts to crypto itself)
  • Stored on `transactions`:
      - amount_usd (canonical)
      - provider_currency, provider_amount, exchange_rate (for audit)
      - display_currency, display_amount (what the user saw on checkout)

Two entry points are enough:
  convert(amount, from_code, to_code) -> Decimal
  usd_to(amount_usd, to_code) -> Decimal
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


# ──────────────────────────────────────────────────────────────────────
# Provider → default currency mapping (what the provider actually charges)
# ──────────────────────────────────────────────────────────────────────
PROVIDER_CURRENCY: dict[str, str] = {
    "hubtel": "GHS",       # Ghana Cedi — Hubtel is a Ghana-only gateway
    "nowpayments": "USD",  # NP invoices in USD, user pays USDT equivalent
}


async def get_rate_vs_usd(code: str) -> Decimal:
    """Return the current rate (1 USD = N `code`). Always falls back to
    hardcoded FALLBACK_RATES from routes/currency if the DB has no row.
    """
    from routes.currency import _get_rate
    code = (code or "USD").upper()
    if code == "USD":
        return Decimal("1")
    rate = await _get_rate(code)
    return Decimal(str(rate))


async def convert(amount: Decimal | float | int | str,
                  from_code: str, to_code: str,
                  rounding: int = 2) -> Decimal:
    """Convert `amount` from one currency to another via USD."""
    a = Decimal(str(amount))
    from_code = (from_code or "USD").upper()
    to_code = (to_code or "USD").upper()
    if from_code == to_code:
        return a.quantize(Decimal(10) ** -rounding, rounding=ROUND_HALF_UP)
    # Convert to USD first
    if from_code == "USD":
        usd = a
    else:
        rate_from = await get_rate_vs_usd(from_code)
        if rate_from <= 0:
            rate_from = Decimal("1")
        usd = a / rate_from
    # Then USD → target
    if to_code == "USD":
        result = usd
    else:
        rate_to = await get_rate_vs_usd(to_code)
        result = usd * rate_to
    return result.quantize(Decimal(10) ** -rounding, rounding=ROUND_HALF_UP)


async def usd_to(amount_usd: Decimal | float | int | str,
                 to_code: str, rounding: int = 2) -> Decimal:
    """Shortcut: convert an USD amount to another currency."""
    return await convert(amount_usd, "USD", to_code, rounding=rounding)


async def to_usd(amount: Decimal | float | int | str,
                 from_code: str, rounding: int = 6) -> Decimal:
    """Shortcut: convert any currency amount back to USD (high precision)."""
    return await convert(amount, from_code, "USD", rounding=rounding)


async def provider_context(provider: str,
                           amount_usd: Decimal | float | int | str,
                           ) -> dict:
    """Return the provider-side numbers to charge, for an incoming USD
    amount. Used when initiating a checkout so the provider debits the
    correct local amount rather than a dangerous "10 GHS == 10 USD" bug.

    Returns:
      {
        "provider":         str,
        "provider_currency": str,
        "provider_amount":   Decimal (charged by provider),
        "exchange_rate":     Decimal (1 USD = N provider_currency),
        "amount_usd":        Decimal (canonical),
      }
    """
    provider = (provider or "").lower()
    pc = PROVIDER_CURRENCY.get(provider, "USD")
    amount_usd = Decimal(str(amount_usd))
    rate = await get_rate_vs_usd(pc)
    provider_amount = (amount_usd * rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )
    return {
        "provider": provider,
        "provider_currency": pc,
        "provider_amount": provider_amount,
        "exchange_rate": rate.quantize(Decimal("0.0001")),
        "amount_usd": amount_usd.quantize(Decimal("0.01")),
    }


async def user_display_currency(user_id: Optional[str],
                                 fallback: str = "USD",
                                 request=None) -> str:
    """Resolve the currency the user has chosen to see their balance in.

    Lookup order:
      1. `users.display_currency` if set (explicit user preference)
      2. `users.country` → COUNTRY_TO_CURRENCY
      3. **iter161** — IP-based country detection via `request` headers
         (Cloudflare `cf-ipcountry` → ipapi.co fallback). The resolved
         country is **persisted** on `users.country` so subsequent
         balance loads are a single SQL read.
      4. `fallback` (default "USD")
    """
    if not user_id:
        return fallback.upper()
    from database import get_pool
    from routes.currency import COUNTRY_TO_CURRENCY
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT display_currency, country FROM users WHERE user_id = $1",
            user_id,
        )
    if not row:
        return fallback.upper()
    pref = (row["display_currency"] or "").upper().strip()
    if pref:
        return pref
    cc = (row["country"] or "").upper().strip()
    if cc in COUNTRY_TO_CURRENCY:
        return COUNTRY_TO_CURRENCY[cc]

    # iter161 — No stored country: try IP-based detection using request context.
    # "Local (auto)" CTA relies on this fallback. Without it, the toggle
    # silently stays on USD, which looks like the button is broken.
    if request is not None:
        try:
            detected_cc = await _detect_country_from_request(request)
        except Exception:
            detected_cc = ""
        if detected_cc and detected_cc in COUNTRY_TO_CURRENCY:
            # Persist for next time so we don't re-hit the IP API on every GET.
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET country = $1 WHERE user_id = $2 "
                        "AND (country IS NULL OR country = '')",
                        detected_cc, user_id,
                    )
            except Exception:
                pass
            return COUNTRY_TO_CURRENCY[detected_cc]
    return fallback.upper()


async def _detect_country_from_request(request) -> str:
    """Detect ISO-3166 country code from the incoming HTTP request.

    Order:
      1. Cloudflare `cf-ipcountry` header (zero-latency, prod behind CF)
      2. Real client IP (CF-Connecting-IP → X-Forwarded-For → X-Real-IP)
         resolved through the shared geo helper (`routes.geo._lookup_country_by_ip`).
      3. '' when none of the above yields a 2-letter code.
    """
    cc = (request.headers.get("cf-ipcountry") or "").upper().strip()
    if cc and cc != "XX" and len(cc) == 2 and cc.isalpha():
        return cc
    # Fallback: resolve a real client IP from proxy headers and hit the
    # multi-provider geo lookup (api.country.is, ipwho.is, freeipapi.com).
    try:
        from routes.geo import _client_ip, _lookup_country_by_ip
    except Exception:
        return ""
    ip = _client_ip(request)
    if not ip or ip.startswith(("10.", "127.", "192.168.", "172.", "169.254.")):
        return ""
    try:
        return await _lookup_country_by_ip(ip)
    except Exception:
        return ""
