"""
iter238 — Paystack Ghana endpoints (STRICTLY ADDITIVE).

Routes:
  GET  /api/paystack/convert?amount_usd=10        (auth)
  GET  /api/paystack/limits                       (auth)
  POST /api/paystack/deposit/initialize           (auth)
  GET  /api/paystack/callback?reference=...       (public — redirect)
  POST /api/paystack/webhook                      (public — HMAC signed)

Security guarantees (mirror `routes/hubtel_momo.py`):
  • Method gating: 403 method_disabled if `paystack_enabled=false`.
  • Admin min/max limits in USD.
  • Anti-dup pending deposit (30 minutes per user).
  • Transaction row inserted BEFORE the Paystack call.
  • Wallet credited only after Paystack returns status=success.
  • Atomic credit (SELECT FOR UPDATE) — webhook + callback both safe.
  • Webhook signature verification (HMAC-SHA512) mandatory.

Does NOT modify any existing route or table.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import get_current_user
from services.paystack_service import (
    PAYSTACK_BASE_URL,
    convert_usd_to_ghs,
    generate_reference,
    get_deposit_limits,
    get_paystack_secret,
    get_usd_to_ghs_info,
    is_paystack_enabled,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paystack", tags=["paystack"])

PAYSTACK_TIMEOUT = 10.0
ANTI_DUP_WINDOW = "30 minutes"
PROVIDER_KEY = "paystack"
FRONTEND_BASE_URL_DEFAULT = "https://japapmessenger.com"


# ───────────────────────── Models ─────────────────────────
class DepositInitRequest(BaseModel):
    amount_usd: float = Field(..., gt=0)


# ───────────────────────── Helpers ────────────────────────
async def _ensure_enabled() -> None:
    """Raises 403 method_disabled if the admin toggle is off. Called by
    every Paystack endpoint EXCEPT the webhook (webhooks must always be
    accepted to avoid losing payments mid-toggle)."""
    if not await is_paystack_enabled():
        raise HTTPException(status_code=403, detail={
            "error": "method_disabled",
            "message": "This payment method is currently unavailable.",
        })


async def _insert_notification(conn, user_id: str, ntype: str, title: str,
                                message: str, data: dict) -> None:
    notif_id = f"notif_{uuid.uuid4().hex[:12]}"
    await conn.execute(
        """INSERT INTO notifications (notif_id, user_id, type, title, message, data)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        notif_id, user_id, ntype, title, message, json.dumps(data),
    )


async def _insert_audit(conn, user_id: str, action: str, details: dict) -> None:
    await conn.execute(
        """INSERT INTO audit_logs (user_id, action, resource, details)
           VALUES ($1, $2, 'wallet', $3)""",
        user_id, action, json.dumps(details),
    )


async def _has_pending_deposit(conn, user_id: str) -> bool:
    row = await conn.fetchrow(
        f"""SELECT tx_id FROM transactions
              WHERE to_user_id = $1
                AND provider = $2
                AND type = 'deposit'
                AND status = 'pending'
                AND created_at > NOW() - INTERVAL '{ANTI_DUP_WINDOW}'
              LIMIT 1""",
        user_id, PROVIDER_KEY,
    )
    return row is not None


async def _frontend_base() -> str:
    """Where to redirect the user after the Paystack hosted page. We
    intentionally bypass `paystack_*` keys to allow ops to override at
    runtime without redeploy."""
    from services.settings_service import get_setting
    return (await get_setting("paystack_frontend_base_url")) or FRONTEND_BASE_URL_DEFAULT


async def _credit_wallet_atomic(conn, reference: str) -> dict | None:
    """Returns {tx_id, user_id, amount, already_processed} or None if no
    matching pending tx. Atomic via SELECT FOR UPDATE."""
    tx = await conn.fetchrow(
        """SELECT tx_id, to_user_id, amount, status
             FROM transactions
            WHERE reference = $1 AND provider = $2
              AND type = 'deposit'
            FOR UPDATE""",
        reference, PROVIDER_KEY,
    )
    if not tx:
        return None
    if tx["status"] != "pending":
        # Already processed by the other path (webhook or callback).
        return {"already_processed": True, "tx_id": tx["tx_id"],
                "user_id": tx["to_user_id"], "amount": tx["amount"]}
    await conn.execute(
        "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
        tx["amount"], datetime.now(timezone.utc), tx["to_user_id"],
    )
    await conn.execute(
        "UPDATE transactions SET status='completed' WHERE tx_id=$1",
        tx["tx_id"],
    )
    return {"already_processed": False, "tx_id": tx["tx_id"],
            "user_id": tx["to_user_id"], "amount": tx["amount"]}


async def _mark_failed(conn, reference: str) -> None:
    await conn.execute(
        """UPDATE transactions SET status='failed'
            WHERE reference=$1 AND provider=$2 AND status='pending'""",
        reference, PROVIDER_KEY,
    )


# ───────────────────────── Endpoints ──────────────────────
@router.get("/convert")
async def paystack_convert(
    request: Request,
    amount_usd: float = Query(..., gt=0),
):
    """Live USD→GHS preview. Polled by the frontend with a debounce."""
    await get_current_user(request)
    await _ensure_enabled()
    fx = await convert_usd_to_ghs(amount_usd)
    return {
        "amount_usd": fx["amount_usd"],
        "amount_ghs": fx["amount_ghs"],
        "amount_pesewas": fx["amount_pesewas"],
        "rate": fx["rate"],
        "rate_source": fx["rate_source"],
        "fetched_at": fx.get("fetched_at"),
    }


@router.get("/limits")
async def paystack_limits(request: Request):
    """Admin-configured deposit min/max + current live FX info."""
    await get_current_user(request)
    await _ensure_enabled()
    limits = await get_deposit_limits()
    fx = await get_usd_to_ghs_info()
    return {
        "deposit": limits,
        "fx": {
            "rate": fx["rate"],
            "source": fx["source"],
            "fetched_at": fx.get("fetched_at"),
        },
    }


@router.post("/deposit/initialize")
async def paystack_deposit_initialize(req: DepositInitRequest, request: Request):
    """Step 1: validate, lock the tx row, then call Paystack /initialize.

    Returns `{authorization_url, reference, amount_usd, amount_ghs, rate}`.
    The frontend redirects the user to `authorization_url`.
    """
    user = await get_current_user(request)
    await _ensure_enabled()

    # 1. Limits.
    limits = await get_deposit_limits()
    if req.amount_usd < limits["min"]:
        raise HTTPException(status_code=400, detail={
            "error": "amount_too_low",
            "message": f"Minimum: {limits['min']:.2f} USD",
        })
    if req.amount_usd > limits["max"]:
        raise HTTPException(status_code=400, detail={
            "error": "amount_too_high",
            "message": f"Maximum: {limits['max']:.2f} USD",
        })

    # 2. Secret key present.
    secret = await get_paystack_secret()
    if not secret:
        raise HTTPException(status_code=500, detail={
            "error": "paystack_misconfigured",
            "message": "Payment service is not configured. Please contact support.",
        })

    # 3. Anti-dup + tx insert (atomic per-user).
    pool = await get_pool()
    fx = await convert_usd_to_ghs(req.amount_usd)
    reference = generate_reference()
    tx_id = f"dep_{uuid.uuid4().hex[:12]}"
    amount_dec = Decimal(str(req.amount_usd))
    notes = (
        f"[Paystack] {fx['amount_ghs']:.2f} GHS @ {fx['rate']:.4f} USD/GHS "
        f"(source={fx['rate_source']})"
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            if await _has_pending_deposit(conn, user["user_id"]):
                raise HTTPException(status_code=409, detail={
                    "error": "pending_exists",
                    "message": "You already have a pending deposit. "
                               "Please wait for confirmation.",
                })
            await conn.execute(
                """INSERT INTO transactions
                     (tx_id, to_user_id, type, status, amount, amount_usd,
                      currency, provider, reference, notes,
                      display_currency, display_amount)
                   VALUES ($1, $2, 'deposit', 'pending', $3, $3,
                           'USD', $4, $5, $6,
                           'GHS', $7)""",
                tx_id, user["user_id"], amount_dec,
                PROVIDER_KEY, reference, notes,
                Decimal(str(fx["amount_ghs"])),
            )
            await _insert_audit(conn, user["user_id"],
                                "paystack_deposit_initiated",
                                {"tx_id": tx_id, "reference": reference,
                                 "amount_usd": float(req.amount_usd),
                                 "amount_ghs": fx["amount_ghs"]})

    # 4. Paystack call (outside the conn block — keeps the lock window tiny).
    callback_url = f"{await _frontend_base()}/api/paystack/callback"
    payload = {
        "email": user.get("email") or f"user-{user['user_id']}@japapmessenger.com",
        "amount": fx["amount_pesewas"],
        "currency": "GHS",
        "reference": reference,
        "callback_url": callback_url,
        "metadata": {
            "user_id": user["user_id"],
            "amount_usd": float(req.amount_usd),
            "amount_ghs": fx["amount_ghs"],
            "rate": fx["rate"],
            "japap_reference": reference,
        },
    }
    response_body: dict | None = None
    response_status: bool = False
    try:
        async with httpx.AsyncClient(timeout=PAYSTACK_TIMEOUT) as client:
            r = await client.post(
                f"{PAYSTACK_BASE_URL}/transaction/initialize",
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        try:
            response_body = r.json()
        except Exception:
            response_body = {"raw": r.text[:500]}
        response_status = bool(response_body.get("status"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[paystack] initialize network error: %s", e)

    if not response_status:
        # Mark the tx as failed so the anti-dup window doesn't trap the user.
        async with pool.acquire() as conn:
            await _mark_failed(conn, reference)
        raise HTTPException(status_code=502, detail={
            "error": "paystack_init_failed",
            "message": (response_body or {}).get("message")
                       or "Could not initiate the payment. Please try again.",
        })

    data = response_body.get("data") or {}
    return {
        "status": "pending",
        "authorization_url": data.get("authorization_url"),
        "access_code": data.get("access_code"),
        "reference": reference,
        "tx_id": tx_id,
        "amount_usd": float(req.amount_usd),
        "amount_ghs": fx["amount_ghs"],
        "amount_pesewas": fx["amount_pesewas"],
        "rate": fx["rate"],
    }


@router.get("/callback")
async def paystack_callback(request: Request,
                             reference: str | None = Query(None),
                             trxref: str | None = Query(None)):
    """Public redirect handler. Paystack sends the user here after the
    hosted page closes. We verify with Paystack and redirect to the
    frontend with `?status=success|failed|error`.
    """
    base = await _frontend_base()
    ref = reference or trxref
    if not ref:
        return RedirectResponse(url=f"{base}/wallet/paystack/result?status=error",
                                 status_code=302)

    secret = await get_paystack_secret()
    if not secret:
        return RedirectResponse(url=f"{base}/wallet/paystack/result?status=error",
                                 status_code=302)

    # Verify with Paystack — authoritative source.
    tx_data = None
    try:
        async with httpx.AsyncClient(timeout=PAYSTACK_TIMEOUT) as client:
            r = await client.get(
                f"{PAYSTACK_BASE_URL}/transaction/verify/{ref}",
                headers={"Authorization": f"Bearer {secret}"},
            )
        body = r.json() or {}
        if body.get("status") is True:
            tx_data = body.get("data") or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[paystack] callback verify error: %s", e)

    if not tx_data:
        return RedirectResponse(url=f"{base}/wallet/paystack/result?status=error",
                                 status_code=302)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if tx_data.get("status") == "success":
                result = await _credit_wallet_atomic(conn, ref)
                if result is None:
                    return RedirectResponse(
                        url=f"{base}/wallet/paystack/result?status=error",
                        status_code=302)
                if not result["already_processed"]:
                    await _insert_audit(
                        conn, result["user_id"],
                        "paystack_callback_deposit_completed",
                        {"tx_id": result["tx_id"], "reference": ref})
                    await _insert_notification(
                        conn, result["user_id"], "deposit_completed",
                        "Paystack deposit confirmed",
                        f"Your deposit of {result['amount']} USD has been "
                        f"confirmed and credited.",
                        {"tx_id": result["tx_id"], "provider": PROVIDER_KEY},
                    )
                amount_usd = float(result["amount"])
                return RedirectResponse(
                    url=f"{base}/wallet/paystack/result?status=success&amount_usd={amount_usd}",
                    status_code=302)
            else:
                await _mark_failed(conn, ref)
                return RedirectResponse(
                    url=f"{base}/wallet/paystack/result?status=failed",
                    status_code=302)


@router.post("/webhook")
async def paystack_webhook(request: Request):
    """Public — Paystack POSTs here on every transaction event. We MUST:
       1. Verify HMAC-SHA512 signature (raw bytes).
       2. On `charge.success`, credit atomically (SELECT FOR UPDATE).

    Webhook is NOT gated by `paystack_enabled` — even if the admin
    disables Paystack mid-flight we still want to credit in-flight
    transactions instead of dropping money."""
    payload_bytes = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    secret = await get_paystack_secret()
    if not secret or not verify_webhook_signature(payload_bytes, signature, secret):
        raise HTTPException(status_code=401, detail="invalid_signature")

    try:
        event = json.loads(payload_bytes.decode("utf-8")) if payload_bytes else {}
    except Exception:
        event = {}

    event_type = event.get("event")
    data = event.get("data") or {}
    reference = data.get("reference")

    if event_type == "charge.success" and reference:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                result = await _credit_wallet_atomic(conn, reference)
                if result and not result["already_processed"]:
                    await _insert_audit(
                        conn, result["user_id"],
                        "paystack_webhook_deposit_completed",
                        {"tx_id": result["tx_id"], "reference": reference})
                    await _insert_notification(
                        conn, result["user_id"], "deposit_completed",
                        "Paystack deposit confirmed",
                        f"Your deposit of {result['amount']} USD has been "
                        f"confirmed and credited.",
                        {"tx_id": result["tx_id"], "provider": PROVIDER_KEY},
                    )

    return {"status": "ok"}


paystack_router = router

__all__ = ["router", "paystack_router"]
