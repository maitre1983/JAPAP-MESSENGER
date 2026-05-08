"""
Hubtel Online Checkout — JAPAP server-side integration.
========================================================
iter207 — CEO mandate: CLONE EAA Hubtel model exactly.

    • Endpoint  : POST https://payproxyapi.hubtel.com/items/initiate
    • Auth      : HTTP Basic — base64(client_id:client_secret)
    • Callback  : POST /api/payments/hubtel/callback
                  ResponseCode == "0000" → credit wallet idempotent
    • Return    : /api/payments/hubtel/return/success?tx=…
                  /api/payments/hubtel/return/cancelled?tx=…
    • USD → local dynamic conversion (GHS/XOF/XAF/NGN/KES/EUR/…)
    • Admin config endpoints with credential masking.

Public API (all async):
    get_config(mask=False)                 -> dict
    save_config(data)                      -> dict (partial update, preserves masked)
    get_exchange_rates()                   -> dict[str, float]
    convert_usd_to_local(amount_usd, cur)  -> float
    get_available_methods(country_code)    -> list[dict]
    create_deposit(user_id, amount_usd, currency, phone=None, ...) -> dict
    process_callback(payload)              -> dict
    test_connection()                      -> dict
    initiate_checkout(...)                 -> dict    (kept for legacy wallet.py flow)
    verify_transaction_status(...)         -> dict    (kept for legacy flow — IP-whitelisted)
"""
from __future__ import annotations

import base64
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

from services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)

HUBTEL_INITIATE_URL = "https://payproxyapi.hubtel.com/items/initiate"
# Transaction Status Check API — requires IP whitelisting by Hubtel.
HUBTEL_STATUS_BASE_URL = "https://rmsc.hubtel.com/v1/merchantaccount/merchants"

# ─────────────────────────────────────────────────────────────────────
# Config keys stored in admin_settings (services/settings_service.py)
# ─────────────────────────────────────────────────────────────────────
_CFG_KEYS = {
    "client_id":           "hubtel_client_id",
    "client_secret":       "hubtel_client_secret",
    "merchant_account":    "hubtel_merchant_account",
    "callback_url":        "hubtel_callback_url_override",
    "return_url":          "hubtel_return_url_override",
    "cancel_url":          "hubtel_cancel_url_override",
    "enabled":             "hubtel_enabled",
    "sandbox_mode":        "hubtel_environment",   # "sandbox" | "production"
    "min_deposit":         "hubtel_min_deposit_usd",
    "max_deposit":         "hubtel_max_deposit_usd",
    "fee_percent":         "hubtel_fee_percent",
    "webhook_secret":      "hubtel_webhook_secret",
}


class HubtelConfigError(Exception):
    """Raised when Hubtel credentials are missing / incomplete."""


class HubtelAPIError(Exception):
    """Raised when Hubtel returns a non-success response."""


# ─────────────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────────────
def _basic_auth_header(client_id: str, client_secret: str) -> str:
    token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {token}"


def _mask(value: str) -> str:
    """EAA-style masking: keep first 4 + '********' + last 4 chars."""
    if not value:
        return ""
    v = str(value)
    if len(v) <= 8:
        # For short values, pad with the 8-star placeholder to keep
        # _is_masked detection reliable on round-trips.
        return "********"
    return f"{v[:4]}********{v[-4:]}"


def _is_masked(v: str) -> bool:
    """Detect if an incoming value is the masked form returned by get_config.
    Admin UIs typically POST the unchanged masked string back — we must
    preserve the existing stored value instead of overwriting with mask.
    Accepts both the long form ('abcd********wxyz') and the short form
    ('********') used for values shorter than 9 chars.
    """
    if not isinstance(v, str):
        return False
    return "********" in v or set(v) == {"*"}


def _normalize_msisdn_gh(phone: str) -> str:
    """Normalize a Ghana mobile number to Hubtel's expected `233XXXXXXXXX`."""
    if not phone:
        return ""
    digits = "".join(c for c in str(phone) if c.isdigit())
    if digits.startswith("233") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "233" + digits[1:]
    if len(digits) == 9:
        return "233" + digits
    return digits


# ─────────────────────────────────────────────────────────────────────
# Config (EAA: get_config / save_config)
# ─────────────────────────────────────────────────────────────────────
async def get_config(mask: bool = False) -> dict:
    """Return full Hubtel config. When mask=True, credentials are masked."""
    raw: dict[str, Any] = {}
    for k, setting_key in _CFG_KEYS.items():
        raw[k] = (await get_setting(setting_key)) or ""

    # Type coercion
    def _bool(v, default=False):
        return str(v).lower() in ("1", "true", "yes", "on") if v != "" else default

    def _num(v, default):
        try:
            return float(v) if v != "" else default
        except (TypeError, ValueError):
            return default

    result = {
        "type":              "hubtel",
        "client_id":         raw["client_id"],
        "client_secret":     raw["client_secret"],
        "merchant_account":  raw["merchant_account"],
        "callback_url":      raw["callback_url"],
        "return_url":        raw["return_url"],
        "cancel_url":        raw["cancel_url"],
        "webhook_secret":    raw["webhook_secret"],
        "enabled":           _bool(raw["enabled"], False),
        "sandbox_mode":      (str(raw["sandbox_mode"]).lower() != "production"),
        "environment":       raw["sandbox_mode"] or "sandbox",
        "min_deposit":       _num(raw["min_deposit"], 1.0),
        "max_deposit":       _num(raw["max_deposit"], 10000.0),
        "fee_percent":       _num(raw["fee_percent"], 1.5),
    }
    if mask:
        result["client_id"]        = _mask(result["client_id"])
        result["client_secret"]    = _mask(result["client_secret"])
        result["webhook_secret"]   = _mask(result["webhook_secret"])
        # merchant_account is semi-sensitive; keep last 4 visible.
        result["merchant_account"] = _mask(result["merchant_account"]) if result["merchant_account"] else ""
        result["configured"] = {
            "client_id":        bool(raw["client_id"]),
            "client_secret":    bool(raw["client_secret"]),
            "merchant_account": bool(raw["merchant_account"]),
            "webhook_secret":   bool(raw["webhook_secret"]),
        }
    return result


async def save_config(data: dict) -> dict:
    """Save partial config. Masked values are preserved (not overwritten)."""
    if not isinstance(data, dict):
        raise ValueError("data must be a dict")

    reverse_map = {
        "client_id":         _CFG_KEYS["client_id"],
        "client_secret":     _CFG_KEYS["client_secret"],
        "merchant_account":  _CFG_KEYS["merchant_account"],
        "callback_url":      _CFG_KEYS["callback_url"],
        "return_url":        _CFG_KEYS["return_url"],
        "cancel_url":        _CFG_KEYS["cancel_url"],
        "webhook_secret":    _CFG_KEYS["webhook_secret"],
        "enabled":           _CFG_KEYS["enabled"],
        "sandbox_mode":      _CFG_KEYS["sandbox_mode"],
        "environment":       _CFG_KEYS["sandbox_mode"],
        "min_deposit":       _CFG_KEYS["min_deposit"],
        "max_deposit":       _CFG_KEYS["max_deposit"],
        "fee_percent":       _CFG_KEYS["fee_percent"],
    }
    saved = []
    for field, setting_key in reverse_map.items():
        if field not in data:
            continue
        value = data[field]
        # Preserve existing if value is masked placeholder
        if isinstance(value, str) and _is_masked(value):
            continue
        # sandbox_mode → environment mapping (handle bool BEFORE generic bool→str)
        if field == "sandbox_mode" and isinstance(value, bool):
            value = "sandbox" if value else "production"
        elif field == "environment" and isinstance(value, str):
            value = value.strip().lower() if value.strip().lower() in ("sandbox", "production") else "sandbox"
        # Booleans → lowercase string (other fields)
        elif isinstance(value, bool):
            value = "true" if value else "false"
        if value is None:
            value = ""
        await set_setting(setting_key, str(value))
        saved.append(field)
    return {"ok": True, "saved": saved}


async def _require_config() -> dict:
    cfg = await get_config(mask=False)
    missing = [k for k in ("client_id", "client_secret", "merchant_account") if not cfg.get(k)]
    if missing:
        raise HubtelConfigError(
            f"Hubtel non configuré (clés manquantes: {', '.join(missing)}). "
            "Renseignez-les dans /admin → Paiements → Paramètres Paiement."
        )
    return cfg


# ─────────────────────────────────────────────────────────────────────
# Currency conversion (EAA: get_exchange_rates / convert_usd_to_local)
# ─────────────────────────────────────────────────────────────────────
_RATE_CACHE: dict[str, Any] = {"at": None, "rates": {}}
_CACHE_TTL_SECONDS = 3600  # 1 hour


async def get_exchange_rates() -> dict[str, float]:
    """Return cached USD→local rates. Refreshes from exchangerate-api every 1h.

    Falls back to the JAPAP local `currency_rates` table (seeded from
    FALLBACK_RATES) if the upstream API is unreachable.
    """
    now = datetime.now(timezone.utc)
    cached_at = _RATE_CACHE.get("at")
    if cached_at and _RATE_CACHE["rates"] and (now - cached_at).total_seconds() < _CACHE_TTL_SECONDS:
        return dict(_RATE_CACHE["rates"])

    # Try upstream (public endpoint, no key required).
    rates: dict[str, float] = {}
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get("https://api.exchangerate-api.com/v4/latest/USD")
        if r.status_code == 200:
            body = r.json() or {}
            up = body.get("rates") or {}
            # Keep everything as float. Enforce USD==1.
            for k, v in up.items():
                try:
                    rates[str(k).upper()] = float(v)
                except (TypeError, ValueError):
                    continue
            rates["USD"] = 1.0
    except Exception as e:
        logger.warning(f"[hubtel] exchange-rate-api unavailable: {e}")

    # Merge with local fallback (JAPAP FALLBACK_RATES from currency_rates table).
    if not rates:
        try:
            from database import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT code, rate_vs_usd FROM currency_rates")
            for r in rows:
                try:
                    rates[str(r["code"]).upper()] = float(r["rate_vs_usd"])
                except (TypeError, ValueError):
                    continue
            rates.setdefault("USD", 1.0)
        except Exception as e:
            logger.warning(f"[hubtel] currency_rates table unavailable: {e}")

    if rates:
        _RATE_CACHE["at"] = now
        _RATE_CACHE["rates"] = rates
    return dict(rates)


async def convert_usd_to_local(amount_usd: float, currency: str) -> dict:
    """Convert a USD amount to a local currency. Returns
        {amount_local, rate, currency}. Currency defaults to USD on unknown code.
    """
    cur = (currency or "USD").upper()
    amt = float(amount_usd)
    if cur == "USD":
        return {"amount_local": round(amt, 2), "rate": 1.0, "currency": "USD"}
    rates = await get_exchange_rates()
    rate = rates.get(cur)
    if rate is None or rate <= 0:
        # Fall back to JAPAP's currency_conversion service (uses currency_rates DB)
        try:
            from services.currency_conversion import usd_to
            amount_local = float(await usd_to(amt, cur))
            rate = amount_local / amt if amt else 0.0
            return {"amount_local": round(amount_local, 2), "rate": round(rate, 6), "currency": cur}
        except Exception:
            return {"amount_local": round(amt, 2), "rate": 1.0, "currency": "USD"}
    amount_local = round(amt * float(rate), 2)
    return {"amount_local": amount_local, "rate": float(rate), "currency": cur}


# ─────────────────────────────────────────────────────────────────────
# Payment methods (EAA: get_available_methods)
# ─────────────────────────────────────────────────────────────────────
async def get_available_methods(country_code: str) -> list[dict]:
    cc = (country_code or "").upper()
    methods: list[dict] = [
        {
            "code": "card",
            "name": "Carte Bancaire (Visa / Mastercard)",
            "channel": "card",
            "icon": "💳",
            "requires_phone": False,
        },
    ]
    if cc == "GH":
        methods += [
            {"code": "mtn_momo",      "name": "MTN Mobile Money",   "channel": "mtn-gh",      "icon": "📱", "requires_phone": True},
            {"code": "vodafone_cash", "name": "Vodafone Cash",       "channel": "vodafone-gh", "icon": "📱", "requires_phone": True},
            {"code": "airteltigo",    "name": "AirtelTigo Money",    "channel": "tigo-gh",     "icon": "📱", "requires_phone": True},
        ]
    return methods


# ─────────────────────────────────────────────────────────────────────
# Audit logs (persisted, best-effort, never raises)
# ─────────────────────────────────────────────────────────────────────
async def _persist_call_log(
    pool, *, kind: str, tx_id: str, request_payload: dict,
    response_status: int, response_body, took_ms: int, error: str = "",
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS hubtel_call_logs (
                    id              bigserial PRIMARY KEY,
                    kind            varchar NOT NULL,
                    tx_id           varchar,
                    request         jsonb,
                    response_status int,
                    response        jsonb,
                    error           text,
                    took_ms         int,
                    created_at      timestamptz DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_hubtel_log_created
                    ON hubtel_call_logs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_hubtel_log_tx
                    ON hubtel_call_logs(tx_id);
            """)
            import json as _json
            await conn.execute("""
                INSERT INTO hubtel_call_logs
                    (kind, tx_id, request, response_status, response, error, took_ms)
                VALUES ($1,$2,$3::jsonb,$4,$5::jsonb,$6,$7)
            """, kind, tx_id,
                _json.dumps(request_payload, default=str),
                response_status,
                _json.dumps(response_body, default=str)
                    if not isinstance(response_body, str)
                    else _json.dumps({"raw": response_body[:2000]}),
                error, took_ms)
    except Exception as e:  # pragma: no cover
        logger.warning(f"[hubtel] call log persist failed: {e}")


# ─────────────────────────────────────────────────────────────────────
# Public: create_deposit — EAA-style orchestration
# ─────────────────────────────────────────────────────────────────────
def _resolve_urls(cfg: dict) -> tuple[str, str, str]:
    """Resolve callback, return, cancel URLs using ENV → admin config → fallback."""
    env_base    = (os.environ.get("IPN_CALLBACK_BASE_URL") or "").rstrip("/")
    public_base = (
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("REACT_APP_BACKEND_URL")
        or env_base
        or ""
    ).rstrip("/")

    cb = (
        os.environ.get("HUBTEL_CALLBACK_URL")
        or (cfg.get("callback_url") or "").strip()
        or (f"{public_base}/api/payments/hubtel/callback" if public_base else "")
    )
    rt = (
        os.environ.get("HUBTEL_RETURN_URL")
        or (cfg.get("return_url") or "").strip()
        or (f"{public_base}/api/payments/hubtel/return/success" if public_base else "")
    )
    cn = (
        os.environ.get("HUBTEL_CANCEL_URL")
        or (cfg.get("cancel_url") or "").strip()
        or (f"{public_base}/api/payments/hubtel/return/cancelled" if public_base else "")
    )
    return cb, rt, cn


def _client_reference(user_id: str) -> str:
    """EAA format: JAPAP-HUB-{user_id[:8]}-{uuid[:8]}."""
    return f"JAPAP-HUB-{(user_id or 'anon')[:8]}-{uuid.uuid4().hex[:8]}"


def _extract_checkout_url(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    # direct keys
    for k in ("checkoutUrl", "checkoutDirectUrl", "paylinkUrl",
             "checkout_url", "checkoutdirecturl", "checkout_direct_url"):
        v = data.get(k)
        if v:
            return str(v)
    # nested `data.*`
    inner = data.get("data") or data.get("Data") or {}
    if isinstance(inner, dict):
        for k in ("checkoutUrl", "checkoutDirectUrl", "paylinkUrl"):
            v = inner.get(k)
            if v:
                return str(v)
    return ""


async def create_deposit(
    user_id: str,
    amount_usd: float,
    currency: str = "GHS",
    phone: Optional[str] = None,
    *,
    description: str = "",
    payee_name: str = "",
    payee_email: str = "",
) -> dict:
    """Create a pending deposit, call Hubtel /items/initiate, persist the
    transaction row, and return the checkout URL.

    Args:
        user_id    : JAPAP user_id (stored on transactions.to_user_id).
        amount_usd : canonical USD amount (≥ min_deposit).
        currency   : provider-side currency, e.g. "GHS" for Ghana Hubtel.
        phone      : optional buyer phone (enables MoMo prompt).

    Returns:
        {
          "ok": True,
          "tx_id":         "dep_xxx",
          "checkout_url":  "https://…",
          "payment_id":    "dep_xxx",
          "amount_usd":    10.0,
          "amount_local":  155.0,
          "currency":      "GHS",
          "exchange_rate": 15.5,
          "raw":           { … }
        }
    """
    from database import get_pool

    cfg = await _require_config()
    if not cfg["enabled"]:
        raise HubtelConfigError(
            "Hubtel est désactivé. Activez-le dans /admin → Paiements → Paramètres Paiement."
        )

    amt = float(amount_usd)
    if amt <= 0:
        raise ValueError("amount_usd must be > 0")
    if amt < float(cfg["min_deposit"]):
        raise ValueError(f"Montant inférieur au minimum ({cfg['min_deposit']} USD).")
    if amt > float(cfg["max_deposit"]):
        raise ValueError(f"Montant supérieur au maximum ({cfg['max_deposit']} USD).")

    currency = (currency or "GHS").upper()
    conv = await convert_usd_to_local(amt, currency)
    provider_amount = float(conv["amount_local"])
    rate = float(conv["rate"])
    callback_url, return_url, cancel_url = _resolve_urls(cfg)
    if not callback_url:
        raise HubtelConfigError(
            "PUBLIC_BASE_URL non configuré — impossible de fournir un callbackUrl à Hubtel."
        )

    # Guard against localhost / private IP callback URLs.
    _bad_hosts = ("localhost", "127.0.0.1", "0.0.0.0", "::1", ".local",
                   "10.", "192.168.", "172.16.", "172.17.", "172.18.",
                   "172.19.", "172.2", "172.30.", "172.31.")
    cb_lower = callback_url.lower()
    if any(b in cb_lower for b in _bad_hosts) or cb_lower.startswith("http://"):
        raise HubtelConfigError(
            f"callbackUrl invalide ({callback_url}). Hubtel ne pourra pas notifier le serveur. "
            "Définissez PUBLIC_BASE_URL=https://votre-domaine.com ou hubtel_callback_url_override."
        )

    # Persist a pending transaction row BEFORE calling Hubtel so the
    # webhook can always match via clientReference, even if the API reply
    # is lost due to a network blip.
    pool = await get_pool()
    tx_id = _client_reference(user_id)
    desc = (description or f"Dépôt JAPAP - {amt} USD")[:128]

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO transactions
                (tx_id, from_user_id, to_user_id, type, amount, currency, status,
                 notes, reference, created_at)
            VALUES ($1, NULL, $2, 'deposit', $3, 'USD', 'pending', $4, $5, NOW())
            ON CONFLICT (tx_id) DO NOTHING
        """, tx_id, user_id, Decimal(str(amt)), desc, "")
        # Extended audit fields (amount_usd, provider, etc. — conditional ALTER)
        try:
            await conn.execute("""
                UPDATE transactions
                   SET amount_usd = $1,
                       provider = 'hubtel',
                       provider_currency = $2,
                       provider_amount = $3,
                       exchange_rate = $4
                 WHERE tx_id = $5
            """, Decimal(str(amt)), currency, Decimal(str(provider_amount)),
                 Decimal(str(rate)), tx_id)
        except Exception:
            # Columns may not exist on older schemas; ignore.
            pass

    # Build Hubtel payload (EAA spec)
    payload: dict[str, Any] = {
        "totalAmount":           provider_amount,
        "description":           desc,
        "callbackUrl":           callback_url,
        "returnUrl":             f"{return_url}{'&' if '?' in return_url else '?'}tx={tx_id}",
        "cancellationUrl":       f"{cancel_url}{'&' if '?' in cancel_url else '?'}tx={tx_id}",
        "merchantAccountNumber": cfg["merchant_account"],
        "clientReference":       tx_id,
    }
    if payee_name:
        payload["payeeName"] = payee_name[:80]
    if payee_email:
        payload["payeeEmail"] = payee_email[:120]
    if phone:
        payload["customerMsisdn"]   = _normalize_msisdn_gh(phone)
        payload["payeeMobileNumber"] = _normalize_msisdn_gh(phone)

    headers = {
        "Authorization": _basic_auth_header(cfg["client_id"], cfg["client_secret"]),
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    import time
    t0 = time.monotonic()
    logger.info(
        f"[hubtel] POST /items/initiate tx={tx_id} amount_usd={amt} "
        f"local={provider_amount}{currency} merchant={cfg['merchant_account']}"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(HUBTEL_INITIATE_URL, json=payload, headers=headers)
    except httpx.HTTPError as e:
        await _persist_call_log(pool, kind="initiate", tx_id=tx_id,
                                request_payload=payload,
                                response_status=0, response_body={},
                                took_ms=int((time.monotonic() - t0) * 1000),
                                error=f"network: {e}")
        raise HubtelAPIError(f"Réseau Hubtel indisponible : {e}") from e

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    took_ms = int((time.monotonic() - t0) * 1000)
    await _persist_call_log(pool, kind="initiate", tx_id=tx_id,
                             request_payload=payload,
                             response_status=resp.status_code,
                             response_body=body, took_ms=took_ms)
    logger.info(f"[hubtel] initiate response tx={tx_id} status={resp.status_code} "
                 f"took_ms={took_ms}")

    if resp.status_code >= 400:
        msg = (body.get("message") or body.get("Message") or body.get("error")
               or f"HTTP {resp.status_code}") if isinstance(body, dict) else str(body)
        raise HubtelAPIError(f"Hubtel {resp.status_code} : {msg}")

    rcode = (body.get("responseCode") or body.get("ResponseCode") or "") if isinstance(body, dict) else ""
    status = (body.get("status") or body.get("Status") or "") if isinstance(body, dict) else ""
    if str(rcode) not in ("0000", "00") and str(status).lower() != "success":
        raise HubtelAPIError(f"Hubtel refusé : status={status} code={rcode} body={body}")

    checkout_url = _extract_checkout_url(body) or _extract_checkout_url(body.get("data") or {})
    if not checkout_url:
        raise HubtelAPIError(f"Hubtel n'a pas retourné de checkoutUrl. body={body}")

    # Persist provider tx id on our row (useful for reconciliation).
    inner = (body.get("data") or body.get("Data") or {}) if isinstance(body, dict) else {}
    provider_tx = (
        inner.get("checkoutId") or inner.get("CheckoutId")
        or inner.get("checkoutTransactionId") or inner.get("transactionId") or ""
    ) if isinstance(inner, dict) else ""
    if provider_tx:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE transactions SET reference = $1 WHERE tx_id = $2",
                    str(provider_tx), tx_id,
                )
        except Exception:
            pass

    return {
        "ok":            True,
        "tx_id":         tx_id,
        "payment_id":    tx_id,
        "checkout_url":  checkout_url,
        "amount_usd":    round(amt, 2),
        "amount_local":  provider_amount,
        "currency":      currency,
        "exchange_rate": rate,
        "provider_tx":   str(provider_tx) if provider_tx else "",
        "raw":           body,
    }


# ─────────────────────────────────────────────────────────────────────
# Public: process_callback — EAA-style webhook handler
# ─────────────────────────────────────────────────────────────────────
async def process_callback(payload: dict, raw_body: bytes = b"", signature: str = "") -> dict:
    """Process an incoming Hubtel webhook.

    ResponseCode == "0000" → credit wallet (idempotent).
    ResponseCode == "0001" → leave pending.
    other                  → mark failed.

    Optional HMAC-SHA256 signature check if `hubtel_webhook_secret` is set.

    Returns:
      {
        "ok":       bool,
        "status":   "completed" | "pending" | "failed" | "already_completed",
        "tx_id":    str,
        "reason":   str,
      }
    """
    from database import get_pool

    # Optional HMAC-SHA256 signature check
    secret = (await get_setting("hubtel_webhook_secret")) or ""
    if secret and raw_body:
        import hmac
        import hashlib
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            logger.warning(f"[hubtel] webhook invalid signature prefix={signature[:8]}")
            return {"ok": False, "status": "invalid_signature",
                    "tx_id": "", "reason": "Invalid signature"}

    # Hubtel webhook shape tolerant: top-level or nested `Data`.
    data = payload.get("Data") or payload.get("data") or payload
    if not isinstance(data, dict):
        data = {}
    tx_id = (
        payload.get("ClientReference") or payload.get("clientReference")
        or data.get("ClientReference")  or data.get("clientReference")
        or ""
    )
    rcode = str(
        payload.get("ResponseCode") or payload.get("responseCode")
        or data.get("ResponseCode") or data.get("responseCode")
        or ""
    ).strip()
    provider_ref = (
        payload.get("TransactionId") or payload.get("transactionId")
        or data.get("TransactionId")  or data.get("transactionId")
        or data.get("CheckoutId")     or data.get("checkoutId")
        or ""
    )
    raw_status = str(
        payload.get("Status") or data.get("Status") or data.get("status") or ""
    ).strip().lower()

    if not tx_id:
        return {"ok": False, "status": "missing_reference", "tx_id": "",
                "reason": "ClientReference manquant"}

    pool = await get_pool()
    # Audit log
    await _persist_call_log(
        pool, kind="webhook", tx_id=tx_id,
        request_payload={"parsed": payload, "rcode": rcode,
                          "provider_ref": str(provider_ref)},
        response_status=200, response_body={}, took_ms=0,
    )

    # EAA decision matrix: ResponseCode "0000" → completed.
    if rcode == "0000" or raw_status in ("success", "paid", "completed", "finished"):
        new_status = "completed"
    elif rcode == "0001" or raw_status in ("pending", "processing"):
        new_status = "pending"
    else:
        new_status = "failed"

    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT * FROM transactions WHERE tx_id = $1 AND type = 'deposit'",
            tx_id,
        )
        if not tx:
            return {"ok": False, "status": "not_found", "tx_id": tx_id,
                    "reason": f"Transaction {tx_id} introuvable"}

        # Idempotence: if already completed, return early.
        if tx["status"] == "completed":
            return {"ok": True, "status": "already_completed", "tx_id": tx_id,
                    "reason": "already credited"}

        if new_status == "failed":
            await conn.execute(
                "UPDATE transactions SET status = 'rejected', admin_notes = $1 WHERE tx_id = $2",
                f"Hubtel callback: rcode={rcode} status={raw_status} ref={provider_ref}",
                tx_id,
            )
            return {"ok": True, "status": "failed", "tx_id": tx_id,
                    "reason": f"rcode={rcode}"}

        if new_status == "pending":
            return {"ok": True, "status": "pending", "tx_id": tx_id,
                    "reason": f"rcode={rcode}"}

        # ────── credit wallet (atomic) ──────
        credit_usd = tx.get("amount_usd") if tx.get("amount_usd") is not None else tx["amount"]
        credit_usd = Decimal(str(credit_usd))
        async with conn.transaction():
            # Ensure wallet exists
            await conn.execute("""
                INSERT INTO wallets (user_id, balance, currency)
                VALUES ($1, 0, 'USD')
                ON CONFLICT (user_id) DO NOTHING
            """, tx["to_user_id"])
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = NOW() WHERE user_id = $2",
                credit_usd, tx["to_user_id"],
            )
            await conn.execute(
                """UPDATE transactions
                      SET status = 'completed',
                          reference = COALESCE(NULLIF($1, ''), reference),
                          admin_notes = $2
                    WHERE tx_id = $3""",
                str(provider_ref or ""),
                f"Auto-credited via Hubtel callback (rcode={rcode})",
                tx_id,
            )
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                       VALUES ($1, $2, 'deposit_completed', 'Dépôt confirmé', $3, $4)""",
                notif_id, tx["to_user_id"],
                f"Votre dépôt de {credit_usd} USD a été crédité.",
                f'{{"tx_id": "{tx_id}", "provider": "hubtel", "ref": "{provider_ref}"}}',
            )
    return {"ok": True, "status": "completed", "tx_id": tx_id,
            "reason": "credited"}


# ─────────────────────────────────────────────────────────────────────
# Legacy initiate_checkout / verify_transaction_status / test_connection
# (kept for backward compatibility with routes/wallet.py deposit flow)
# ─────────────────────────────────────────────────────────────────────
async def _get_legacy_config() -> dict:
    """Legacy shape expected by initiate_checkout (kept for wallet.py)."""
    cfg = await _require_config()
    return {
        "client_id":        cfg["client_id"],
        "client_secret":    cfg["client_secret"],
        "merchant_account": cfg["merchant_account"],
        "environment":      cfg["environment"] or "sandbox",
    }


async def initiate_checkout(
    *,
    tx_id: str,
    amount: float,
    description: str,
    public_base_url: str,
    public_frontend_url: str = "",
    payee_name: str = "",
    payee_email: str = "",
    payee_phone: str = "",
) -> dict[str, Any]:
    """Legacy entry point (wallet.py deposit flow).
    Converts USD → GHS and POSTs to /items/initiate with the classic payload.
    """
    cfg = await _get_legacy_config()
    from services.currency_conversion import provider_context
    ctx = await provider_context("hubtel", amount)
    ghs_amount = float(ctx["provider_amount"])
    rate = float(ctx["exchange_rate"])

    fe_url = (public_frontend_url or public_base_url).rstrip("/")
    override_cb  = await get_setting("hubtel_callback_url_override")
    override_ret = await get_setting("hubtel_return_url_override")
    callback_url = (override_cb or "").strip() or f"{public_base_url.rstrip('/')}/api/payments/hubtel/callback"
    if (override_ret or "").strip():
        ret_base = override_ret.strip()
        sep = "&" if "?" in ret_base else "?"
        return_url = f"{ret_base}{sep}tx={tx_id}"
    else:
        return_url = f"{fe_url}/wallet/deposit/return?tx={tx_id}"

    _bad_hosts = ("localhost", "127.0.0.1", "0.0.0.0", "::1", ".local",
                   "10.", "192.168.", "172.16.", "172.17.", "172.18.",
                   "172.19.", "172.2", "172.30.", "172.31.")
    cb_lower = callback_url.lower()
    if any(b in cb_lower for b in _bad_hosts) or cb_lower.startswith("http://"):
        raise HubtelConfigError(
            f"PUBLIC_BASE_URL invalide pour le webhook Hubtel : {public_base_url!r}. "
            "Hubtel ne pourra pas notifier le serveur (localhost/IP privée/HTTP). "
            "Définissez PUBLIC_BASE_URL=https://votre-domaine.com dans /app/backend/.env."
        )

    payload: dict[str, Any] = {
        "totalAmount":           ghs_amount,
        "description":           description[:128] if description else f"JAPAP wallet deposit {tx_id} ({amount} USD)",
        "callbackUrl":           callback_url,
        "returnUrl":             return_url,
        "cancellationUrl":       f"{fe_url}/wallet/deposit/return?tx={tx_id}&cancelled=1",
        "merchantAccountNumber": cfg["merchant_account"],
        "clientReference":       tx_id,
    }
    if payee_name:
        payload["payeeName"] = payee_name[:80]
    if payee_email:
        payload["payeeEmail"] = payee_email[:120]
    if payee_phone:
        payload["payeeMobileNumber"] = _normalize_msisdn_gh(payee_phone)

    headers = {
        "Authorization": _basic_auth_header(cfg["client_id"], cfg["client_secret"]),
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    import time
    t0 = time.monotonic()
    logger.info(f"[hubtel] (legacy) POST /items/initiate tx={tx_id} usd={amount} ghs={ghs_amount}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(HUBTEL_INITIATE_URL, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise HubtelAPIError(f"Réseau Hubtel indisponible : {e}") from e

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    took_ms = int((time.monotonic() - t0) * 1000)
    try:
        from database import get_pool
        pool = await get_pool()
        await _persist_call_log(pool, kind="initiate", tx_id=tx_id,
                                 request_payload=payload,
                                 response_status=resp.status_code,
                                 response_body=body, took_ms=took_ms)
    except Exception:
        pass

    if resp.status_code >= 400:
        code = (body.get("responseCode") or body.get("statusCode") or resp.status_code) if isinstance(body, dict) else resp.status_code
        msg = (body.get("message") or body.get("Message") or body.get("error") or "identifiants invalides ou service indisponible") if isinstance(body, dict) else str(body)
        raise HubtelAPIError(f"Hubtel {resp.status_code} [{code}] : {msg}")

    status = (body.get("status") if isinstance(body, dict) else "")
    rcode = (body.get("responseCode") if isinstance(body, dict) else "")
    data = (body.get("data") if isinstance(body, dict) else {}) or {}
    if str(status).lower() != "success" or str(rcode) not in ("0000", "00"):
        raise HubtelAPIError(f"Hubtel refusé : status={status} code={rcode} body={body}")

    return {
        "checkout_url":          data.get("checkoutUrl") or data.get("checkout_url") or "",
        "checkout_direct_url":   data.get("checkoutDirectUrl") or "",
        "checkout_tx_id":        (data.get("checkoutId")
                                   or data.get("checkoutTransactionId")
                                   or data.get("transactionId") or ""),
        "provider_currency":     "GHS",
        "provider_amount":       ghs_amount,
        "exchange_rate":         rate,
        "amount_usd":            float(amount),
        "raw":                   body,
    }


async def test_connection() -> dict[str, Any]:
    """Lightweight credentials check: calls /items/initiate with an obviously
    invalid payload. Any 2xx or 4xx response (not 401) means credentials are
    accepted.
    """
    try:
        cfg = await _get_legacy_config()
    except HubtelConfigError as e:
        return {"ok": False, "reason": str(e)}
    headers = {
        "Authorization": _basic_auth_header(cfg["client_id"], cfg["client_secret"]),
        "Content-Type":  "application/json",
    }
    dummy = {
        "totalAmount":           0.01,
        "description":           "JAPAP creds test (no real transaction)",
        "callbackUrl":           "https://example.invalid/cb",
        "returnUrl":             "https://example.invalid/ok",
        "cancellationUrl":       "https://example.invalid/cancel",
        "merchantAccountNumber": cfg["merchant_account"],
        "clientReference":       "conn_test_" + os.urandom(4).hex(),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(HUBTEL_INITIATE_URL, json=dummy, headers=headers)
        ok = resp.status_code < 500 and resp.status_code != 401
        data: Any = {}
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:300]}
        return {
            "ok":                bool(ok),
            "status_code":       resp.status_code,
            "provider_response": data,
            "environment":       cfg["environment"],
        }
    except httpx.HTTPError as e:
        return {"ok": False, "reason": f"Réseau indisponible: {e}"}


async def verify_transaction_status(
    *,
    checkout_id: str = "",
    client_reference: str = "",
) -> dict[str, Any]:
    """Legacy IP-whitelisted verification. Kept for wallet.hubtel_webhook
    (old strict path). The new /api/payments/hubtel/callback does not rely
    on this endpoint — it trusts ResponseCode=0000 per EAA spec.
    """
    if not checkout_id and not client_reference:
        raise ValueError("At least one of checkout_id / client_reference is required")
    cfg = await _get_legacy_config()
    merchant = cfg["merchant_account"]
    txn_id = checkout_id or client_reference
    url = f"{HUBTEL_STATUS_BASE_URL}/{merchant}/transactions/status?hubtelTransactionId={txn_id}"
    headers = {
        "Authorization": _basic_auth_header(cfg["client_id"], cfg["client_secret"]),
        "Accept":        "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        return {"ok": False, "status": "Unknown", "is_paid": False,
                "amount": None, "provider_ref": "",
                "raw": {}, "reason": f"Réseau Hubtel indisponible : {e}",
                "whitelist_required": False}
    if resp.status_code == 403:
        return {"ok": False, "status": "Unknown", "is_paid": False,
                "amount": None, "provider_ref": "", "raw": resp.text[:300],
                "reason": "Hubtel 403 — IP non whitelistée (retail@hubtel.com).",
                "whitelist_required": True}
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:300]}
    if resp.status_code >= 400:
        reason = (body.get("message") or body.get("Message") if isinstance(body, dict) else f"HTTP {resp.status_code}") or f"HTTP {resp.status_code}"
        return {"ok": False, "status": "Unknown", "is_paid": False,
                "amount": None, "provider_ref": "",
                "raw": body, "reason": f"Hubtel {resp.status_code} : {reason}",
                "whitelist_required": False}
    data_list = body.get("Data") or body.get("data") if isinstance(body, dict) else []
    if isinstance(data_list, dict):
        data = data_list
    elif isinstance(data_list, list) and data_list:
        data = data_list[0]
    else:
        data = {}
    raw_status = str(
        data.get("TransactionStatus") or data.get("InvoiceStatus")
        or data.get("transactionStatus") or data.get("status") or "Unknown"
    ).strip()
    is_paid = raw_status.lower() in ("paid", "success", "successful", "completed", "finished")
    amount = data.get("Amount") or data.get("amount")
    try:
        amount = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount = None
    return {"ok": True, "status": raw_status, "is_paid": is_paid,
            "amount": amount,
            "provider_ref": str(data.get("TransactionId") or data.get("transactionId")
                               or data.get("CheckoutId") or ""),
            "raw": body, "reason": "",
            "whitelist_required": False}
