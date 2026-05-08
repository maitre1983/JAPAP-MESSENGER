"""
NowPayments — server-side integration (crypto deposits).
=========================================================
Endpoints used :
  - POST   https://api.nowpayments.io/v1/invoice              → create hosted invoice
  - GET    https://api.nowpayments.io/v1/payment/{payment_id} → authoritative status check
  - GET    https://api.nowpayments.io/v1/status               → liveness/creds check

Docs : https://documenter.getpostman.com/view/7907941/2s93JusNJt

Credentials are read from `admin_settings` at runtime so the admin can rotate
them via the UI without restarting the server :
  - nowpayments_api_key
  - nowpayments_ipn_secret
  - nowpayments_environment ("sandbox" | "production")

Security pattern : the IPN webhook is verified twice — HMAC-SHA512 signature
on x-nowpayments-sig PLUS an authoritative GET /payment/{id} call before
crediting the wallet. Matches the Hubtel double-check pattern.
"""
import hashlib
import hmac
import json
import logging
from typing import Any, Optional

import httpx

from services.settings_service import get_setting

logger = logging.getLogger(__name__)

NOW_API_PROD = "https://api.nowpayments.io/v1"
NOW_API_SANDBOX = "https://api-sandbox.nowpayments.io/v1"


class NowPaymentsConfigError(Exception):
    """Raised when NowPayments credentials are missing / incomplete."""


class NowPaymentsAPIError(Exception):
    """Raised when NowPayments returns a non-success response."""


async def _get_config() -> dict:
    api_key = (await get_setting("nowpayments_api_key")) or ""
    ipn_secret = (await get_setting("nowpayments_ipn_secret")) or ""
    env = (await get_setting("nowpayments_environment")) or "production"
    if not api_key:
        raise NowPaymentsConfigError(
            "NowPayments non configuré : clé API manquante. "
            "Renseignez-la dans /admin → Paiements → Paramètres Paiement."
        )
    base_url = NOW_API_SANDBOX if env == "sandbox" else NOW_API_PROD
    return {
        "api_key": api_key,
        "ipn_secret": ipn_secret,
        "environment": env,
        "base_url": base_url,
    }


async def create_payment(
    *,
    tx_id: str,
    amount_usd: float,
    pay_currency: str,
    public_base_url: str,
) -> dict[str, Any]:
    """Create a NowPayments crypto PAYMENT (not invoice). The /v1/payment
    endpoint returns the actual wallet address + amount the rider must
    send — perfect to render a QR + display the deposit details inside our
    own UI (no redirect to a hosted page).

    Args:
        tx_id           : our internal transaction id (dep_xxx) → order_id.
        amount_usd      : amount in USD (NowPayments auto-converts).
        pay_currency    : "usdttrc20" | "usdtbsc" | ...
        public_base_url : backend public URL for IPN callback.

    Returns:
        {
          "payment_id":       "...",
          "payment_status":   "waiting" | ...,
          "pay_address":      "TXxxxxx",
          "pay_amount":       10.5,
          "pay_currency":     "usdttrc20",
          "price_amount":     10,
          "price_currency":   "usd",
          "expiration_estimate_date": "...",
          "raw":              {full body},
        }
    """
    cfg = await _get_config()
    payload = {
        "price_amount": float(amount_usd),
        "price_currency": "usd",
        "pay_currency": pay_currency,
        "order_id": tx_id,
        "order_description": f"JAPAP wallet deposit {amount_usd} USD",
        "ipn_callback_url": f"{public_base_url.rstrip('/')}/api/wallet/nowpayments/webhook",
        "is_fixed_rate": False,   # let NowPayments lock the rate at confirm
        "is_fee_paid_by_user": False,
    }
    headers = {
        "x-api-key": cfg["api_key"],
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{cfg['base_url']}/payment", json=payload, headers=headers,
            )
    except httpx.HTTPError as e:
        raise NowPaymentsAPIError(f"Réseau NowPayments indisponible : {e}") from e

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:300]}

    if resp.status_code >= 400:
        msg = (
            body.get("message") or body.get("errors")
            if isinstance(body, dict) else f"HTTP {resp.status_code}"
        ) or f"HTTP {resp.status_code}"
        raise NowPaymentsAPIError(f"NowPayments {resp.status_code} : {msg}")

    return {
        "payment_id":     str(body.get("payment_id") or ""),
        "payment_status": str(body.get("payment_status") or "waiting"),
        "pay_address":    str(body.get("pay_address") or ""),
        "pay_amount":     body.get("pay_amount"),
        "pay_currency":   str(body.get("pay_currency") or pay_currency),
        "price_amount":   body.get("price_amount"),
        "price_currency": str(body.get("price_currency") or "usd"),
        "expiration_estimate_date": str(body.get("expiration_estimate_date") or ""),
        "raw": body,
    }


async def create_invoice(
    *,
    tx_id: str,
    amount_usd: float,
    pay_currency: str,
    public_base_url: str,
    public_frontend_url: str = "",
) -> dict[str, Any]:
    """Creates a NowPayments hosted invoice and returns the checkout URL.

    Args:
        tx_id               : our internal transaction id (dep_xxx) → order_id.
        amount_usd          : amount in USD (NowPayments auto-converts).
        pay_currency        : "usdttrc20" | "usdtbsc" | ...
        public_base_url     : backend public URL for IPN callback (webhook).
        public_frontend_url : frontend public URL for success/cancel redirects.
                              Falls back to public_base_url if empty (preview env).

    Returns:
        {"invoice_url": "...", "invoice_id": "...", "raw": {...}}
    """
    cfg = await _get_config()
    # iter116 — Distinct callback (backend webhook) vs return (frontend)
    # URLs. In production the frontend host (japapmessenger.com) ≠ backend
    # host (api.japapmessenger.com), so success/cancel must point at FE.
    fe_url = (public_frontend_url or public_base_url).rstrip("/")
    payload = {
        "price_amount": float(amount_usd),
        "price_currency": "usd",
        "pay_currency": pay_currency,
        "order_id": tx_id,
        "order_description": f"JAPAP wallet deposit {amount_usd} USD",
        "ipn_callback_url": f"{public_base_url.rstrip('/')}/api/wallet/nowpayments/webhook",
        "success_url": f"{fe_url}/wallet?deposit=success&tx={tx_id}",
        "cancel_url": f"{fe_url}/wallet?deposit=cancelled&tx={tx_id}",
    }
    headers = {
        "x-api-key": cfg["api_key"],
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{cfg['base_url']}/invoice", json=payload, headers=headers,
            )
    except httpx.HTTPError as e:
        raise NowPaymentsAPIError(f"Réseau NowPayments indisponible : {e}") from e

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:300]}

    if resp.status_code >= 400:
        msg = (
            body.get("message") or body.get("errors")
            if isinstance(body, dict) else f"HTTP {resp.status_code}"
        ) or f"HTTP {resp.status_code}"
        raise NowPaymentsAPIError(f"NowPayments {resp.status_code} : {msg}")

    return {
        "invoice_url": body.get("invoice_url") or "",
        "invoice_id": str(body.get("id") or ""),
        "pay_address": body.get("pay_address") or "",
        "raw": body,
    }


async def verify_payment_status(payment_id: str) -> dict[str, Any]:
    """Independently verifies a payment's status with NowPayments.

    Called from the webhook handler AND the manual verify endpoint before
    crediting the wallet — so a spoofed webhook alone is NEVER sufficient.

    Returns:
        {
          "ok": bool,               # True iff we got a valid API response
          "status": "finished"|"waiting"|"confirming"|"confirmed"
                    |"partially_paid"|"failed"|"expired"|...,
          "is_paid": bool,          # True iff status == "finished"
          "actually_paid": float|None,
          "pay_currency": str,
          "order_id": str,          # our tx_id
          "raw": {...},
          "reason": str,
        }
    """
    cfg = await _get_config()
    headers = {"x-api-key": cfg["api_key"], "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{cfg['base_url']}/payment/{payment_id}", headers=headers,
            )
    except httpx.HTTPError as e:
        return {
            "ok": False, "status": "Unknown", "is_paid": False,
            "actually_paid": None, "pay_currency": "", "order_id": "",
            "raw": {}, "reason": f"Réseau NowPayments indisponible : {e}",
        }
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:300]}
    if resp.status_code >= 400:
        reason = (
            body.get("message") if isinstance(body, dict) else f"HTTP {resp.status_code}"
        ) or f"HTTP {resp.status_code}"
        return {
            "ok": False, "status": "Unknown", "is_paid": False,
            "actually_paid": None, "pay_currency": "", "order_id": "",
            "raw": body, "reason": f"NowPayments {resp.status_code} : {reason}",
        }
    status = str(body.get("payment_status") or "").lower()
    actually_paid = body.get("actually_paid")
    try:
        actually_paid = float(actually_paid) if actually_paid is not None else None
    except (TypeError, ValueError):
        actually_paid = None
    return {
        "ok": True,
        "status": status or "Unknown",
        "is_paid": status == "finished",
        "actually_paid": actually_paid,
        "pay_currency": str(body.get("pay_currency") or ""),
        "order_id": str(body.get("order_id") or ""),
        "raw": body,
        "reason": "",
    }


def verify_ipn_signature(raw_body: bytes, provided_sig: str, ipn_secret: str) -> bool:
    """Verifies the x-nowpayments-sig HMAC-SHA512 signature.

    NowPayments signs a sorted-keys JSON version of the body (not the raw body).
    See : https://documenter.getpostman.com/view/7907941/2s93JusNJt
    """
    if not ipn_secret or not provided_sig:
        return False
    try:
        parsed = json.loads(raw_body.decode() or "{}")
    except Exception:
        return False
    sorted_payload = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(
        ipn_secret.encode(), sorted_payload.encode(), hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, provided_sig)


async def test_connection() -> dict[str, Any]:
    """Lightweight credentials / network liveness check."""
    try:
        cfg = await _get_config()
    except NowPaymentsConfigError as e:
        return {"ok": False, "reason": str(e)}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            status_resp = await client.get(f"{cfg['base_url']}/status")
    except httpx.HTTPError as e:
        return {"ok": False, "reason": f"Réseau indisponible : {e}"}
    # /status is unauthenticated and always returns {"message":"OK"}. We also
    # hit /currencies (authenticated) to verify the api key actually works.
    headers = {"x-api-key": cfg["api_key"]}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            auth_resp = await client.get(f"{cfg['base_url']}/currencies", headers=headers)
    except httpx.HTTPError as e:
        return {"ok": False, "reason": f"Réseau indisponible : {e}"}
    return {
        "ok": status_resp.status_code == 200 and auth_resp.status_code == 200,
        "status_code": auth_resp.status_code,
        "liveness": status_resp.status_code == 200,
        "authenticated": auth_resp.status_code == 200,
        "environment": cfg["environment"],
    }
