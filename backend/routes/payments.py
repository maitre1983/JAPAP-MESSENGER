"""
iter207 — /api/payments/hubtel/* — EAA-style Hubtel integration endpoints.

Public (no auth required for callback):
    POST /api/payments/hubtel/initiate           (auth required)
    POST /api/payments/hubtel/callback           (webhook, public)
    GET  /api/payments/hubtel/return/success     (redirect page, public)
    GET  /api/payments/hubtel/return/cancelled   (redirect page, public)
    GET  /api/payments/hubtel/config             (public-safe: enabled + min/max/fee only)

CEO directive (iter207): clone the EAA Hubtel integration model. The legacy
`/api/wallet/hubtel/webhook` alias below delegates to the same EAA
`process_callback` so Hubtel merchant dashboards registered under the old
URL keep working.
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from routes.auth import get_current_user
from services.hubtel_service import (
    HubtelAPIError,
    HubtelConfigError,
    create_deposit,
    get_config,
    get_available_methods,
    process_callback,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])


# ─────────────────────────────────────────────────────────────────────
# 1. INITIATE — user-auth'd endpoint that creates a deposit session.
# ─────────────────────────────────────────────────────────────────────
class HubtelInitiateRequest(BaseModel):
    amount_usd: float
    currency: str = "GHS"          # provider-side currency
    phone: Optional[str] = None
    description: Optional[str] = None


@router.post("/hubtel/initiate")
async def hubtel_initiate(req: HubtelInitiateRequest, request: Request):
    user = await get_current_user(request)
    try:
        res = await create_deposit(
            user_id=user["user_id"],
            amount_usd=float(req.amount_usd),
            currency=req.currency or "GHS",
            phone=req.phone or user.get("phone") or user.get("phone_number") or "",
            description=req.description or f"Dépôt JAPAP - {req.amount_usd} USD",
            payee_name=user.get("name") or user.get("username") or "",
            payee_email=user.get("email") or "",
        )
    except HubtelConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HubtelAPIError as e:
        logger.error(f"[hubtel] initiate error for {user['user_id']}: {e}")
        raise HTTPException(status_code=502, detail=f"Erreur Hubtel : {e}")
    # Public response — strip internal fields (raw).
    return {
        "ok":            res["ok"],
        "tx_id":         res["tx_id"],
        "payment_id":    res["payment_id"],
        "checkout_url":  res["checkout_url"],
        "amount_usd":    res["amount_usd"],
        "amount_local":  res["amount_local"],
        "currency":      res["currency"],
        "exchange_rate": res["exchange_rate"],
    }


# ─────────────────────────────────────────────────────────────────────
# 2. CALLBACK — webhook from Hubtel. Public, no auth.
# ─────────────────────────────────────────────────────────────────────
@router.post("/hubtel/callback")
async def hubtel_callback(request: Request):
    """Hubtel Online Checkout IPN/webhook.
    Idempotent. Credits wallet when ResponseCode == "0000" (EAA spec).
    """
    raw_body = await request.body()
    import json as _json
    try:
        payload = _json.loads(raw_body.decode() or "{}")
    except Exception as e:
        logger.warning(f"[hubtel] callback invalid JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    signature = (
        request.headers.get("X-Auth-Signature")
        or request.headers.get("x-auth-signature")
        or request.headers.get("X-Hubtel-Signature")
        or ""
    )
    result = await process_callback(payload, raw_body=raw_body, signature=signature)
    if not result.get("ok"):
        # Still return 200 for transient non-fatal errors so Hubtel doesn't
        # retry against e.g. a missing reference. Only reject signatures.
        if result.get("status") == "invalid_signature":
            raise HTTPException(status_code=401, detail="Invalid signature")
        # 404 missing tx → 404 so dashboards show the diagnostic.
        if result.get("status") in ("not_found", "missing_reference"):
            return JSONResponse(status_code=404, content=result)
    return result


# ─────────────────────────────────────────────────────────────────────
# 3. RETURN pages — user-facing redirect after checkout.
# ─────────────────────────────────────────────────────────────────────
def _fe_url() -> str:
    return (
        os.environ.get("FRONTEND_URL")
        or os.environ.get("PUBLIC_FRONTEND_URL")
        or os.environ.get("PUBLIC_BASE_URL")
        or ""
    ).rstrip("/")


def _render_return_html(title: str, status: str, tx_id: str, color: str, emoji: str) -> str:
    fe = _fe_url()
    wallet_cta = f'<a href="{fe}/wallet" class="btn">Retour au wallet</a>' if fe else ""
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JAPAP — {title}</title>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #F3F4F6; color: #111827; display: grid; place-items: center;
          min-height: 100dvh; padding: 24px; }}
  .card {{ background: #fff; border-radius: 20px; padding: 32px; max-width: 480px;
           box-shadow: 0 10px 30px rgba(0,0,0,.08); text-align: center; }}
  .emoji {{ font-size: 56px; line-height: 1; margin-bottom: 12px; }}
  h1 {{ font-size: 22px; margin: 0 0 8px; color: {color}; }}
  p {{ color: #6B7280; font-size: 14px; margin: 8px 0; }}
  .tx {{ font-family: monospace; font-size: 12px; color: #9CA3AF; word-break: break-all; }}
  .btn {{ display: inline-block; margin-top: 16px; padding: 10px 18px;
          background: #5B21B6; color: #fff; text-decoration: none; border-radius: 10px;
          font-weight: 600; font-size: 14px; }}
</style></head>
<body><div class="card" data-testid="hubtel-return-{status}">
  <div class="emoji">{emoji}</div>
  <h1>{title}</h1>
  <p>Statut : <strong>{status}</strong></p>
  <p class="tx">Transaction : {tx_id or '—'}</p>
  {wallet_cta}
  <script>
    setTimeout(function() {{
      var fe = {repr(fe)};
      if (fe) window.location.href = fe + '/wallet/deposit/return?tx=' + encodeURIComponent({repr(tx_id)}) + '&status={status}';
    }}, 2500);
  </script>
</div></body></html>"""


@router.get("/hubtel/return/success")
async def hubtel_return_success(tx: str = ""):
    return HTMLResponse(
        _render_return_html("Paiement confirmé", "success", tx, "#10B981", "✅")
    )


@router.get("/hubtel/return/cancelled")
async def hubtel_return_cancelled(tx: str = ""):
    return HTMLResponse(
        _render_return_html("Paiement annulé", "cancelled", tx, "#F59E0B", "⚠️")
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Public config / methods (NOT sensitive; enabled + min/max/fee + methods)
# ─────────────────────────────────────────────────────────────────────
@router.get("/hubtel/config")
async def hubtel_public_config():
    cfg = await get_config(mask=True)
    return {
        "enabled":      cfg["enabled"],
        "sandbox_mode": cfg["sandbox_mode"],
        "min_deposit":  cfg["min_deposit"],
        "max_deposit":  cfg["max_deposit"],
        "fee_percent":  cfg["fee_percent"],
        "configured":   cfg.get("configured", {}),
    }


@router.get("/hubtel/methods/{country_code}")
async def hubtel_methods(country_code: str):
    return {"country": country_code.upper(), "methods": await get_available_methods(country_code)}


# ─────────────────────────────────────────────────────────────────────
# 5. Public exchange-rate preview (used by deposit currency selector)
# ─────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone
from services.hubtel_service import convert_usd_to_local


@router.get("/hubtel/exchange-rate")
async def hubtel_exchange_rate_public(currency: str = "GHS", amount_usd: float = 1.0):
    """Public USD→local rate preview for the deposit modal.

    Returns:
        {
          "currency":     "XAF",
          "rate":         655.957,
          "amount_local": 13119.14,
          "amount_usd":   20.0,
          "last_updated": "2026-05-03T22:00:00Z",
        }
    """
    if amount_usd <= 0:
        amount_usd = 1.0
    res = await convert_usd_to_local(amount_usd, currency)
    return {
        **res,
        "amount_usd":   round(float(amount_usd), 2),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# 6. Supported currencies (for the dropdown)
# ─────────────────────────────────────────────────────────────────────
@router.get("/hubtel/currencies")
async def hubtel_currencies():
    """List of supported deposit currencies + country→currency mapping
    so the frontend can pick a sensible default per user.country.
    """
    currencies = [
        {"code": "USD", "name": "US Dollar",          "symbol": "$",   "flag": "🇺🇸"},
        {"code": "GHS", "name": "Ghana Cedi",          "symbol": "₵",   "flag": "🇬🇭"},
        {"code": "XOF", "name": "CFA West",            "symbol": "FCFA","flag": "🇨🇮"},
        {"code": "XAF", "name": "CFA Central",         "symbol": "FCFA","flag": "🇨🇲"},
        {"code": "NGN", "name": "Naira",               "symbol": "₦",   "flag": "🇳🇬"},
        {"code": "KES", "name": "Kenya Shilling",      "symbol": "KSh", "flag": "🇰🇪"},
        {"code": "EUR", "name": "Euro",                "symbol": "€",   "flag": "🇪🇺"},
        {"code": "GBP", "name": "British Pound",       "symbol": "£",   "flag": "🇬🇧"},
        {"code": "MAD", "name": "Moroccan Dirham",     "symbol": "DH",  "flag": "🇲🇦"},
        {"code": "EGP", "name": "Egyptian Pound",      "symbol": "E£",  "flag": "🇪🇬"},
        {"code": "INR", "name": "Indian Rupee",        "symbol": "₹",   "flag": "🇮🇳"},
    ]
    country_to_currency = {
        "GH": "GHS",
        "CI": "XOF", "SN": "XOF", "BF": "XOF", "ML": "XOF",
        "TG": "XOF", "BJ": "XOF", "NE": "XOF",
        "CM": "XAF", "GA": "XAF", "TD": "XAF", "CF": "XAF", "CG": "XAF", "GQ": "XAF",
        "NG": "NGN",
        "KE": "KES",
        "MA": "MAD",
        "EG": "EGP",
        "FR": "EUR", "BE": "EUR", "DE": "EUR", "IT": "EUR", "ES": "EUR",
        "PT": "EUR", "NL": "EUR", "IE": "EUR",
        "GB": "GBP", "UK": "GBP",
        "IN": "INR",
        "US": "USD", "CA": "USD",
    }
    return {"currencies": currencies, "country_to_currency": country_to_currency}
