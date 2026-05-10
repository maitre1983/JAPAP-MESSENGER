"""
iter237af — Hubtel Mobile Money endpoints (Ghana 🇬🇭).

Strict ADDITIVE module — does NOT modify the existing `wallet.py` or
`hubtel_service.py`. Mounts under `/api` and exposes:

  • GET  /api/wallet/hubtel-momo/convert   → live USD→GHS preview
  • GET  /api/wallet/hubtel-momo/limits    → admin-configured min/max
  • POST /api/wallet/deposit/hubtel-momo   → kicks off USSD prompt
  • POST /api/hubtel/callback/receive      → public, no auth (Hubtel webhook)
  • POST /api/wallet/withdraw/hubtel-momo  → debits user, sends MoMo
  • POST /api/hubtel/callback/send         → public, no auth (Hubtel webhook)

Security highlights:
  • Ghana-only (server-side `is_ghana_number` check, 403 otherwise)
  • Anti double-pending (1 deposit + 1 withdrawal per user per 30 min)
  • Atomic debit on withdrawal (`SELECT FOR UPDATE` + balance check)
  • Atomic refund on Hubtel rejection or callback failure
  • Wallet credited ONLY on `ResponseCode=0000` from Hubtel
  • Idempotent callbacks (status check + `SELECT FOR UPDATE`)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from database import get_pool
from routes.auth import get_current_user
from services.hubtel_fx import convert_usd_to_ghs, get_usd_to_ghs_info
from services.hubtel_momo import (
    detect_channel,
    generate_client_reference,
    get_callback_base_url,
    get_collection_account,
    get_deposit_limits,
    get_disbursement_account,
    get_hubtel_auth,
    get_proxies,
    get_withdrawal_limits,
    is_ghana_number,
    normalize_msisdn,
)


def _extract_hubtel_message(body: dict | None, fallback: str) -> str:
    """iter239a2 — Surface the actual Hubtel error to the user. Hubtel
    response shapes vary; we try the most common keys before falling
    back to a generic message. Sanitised to ≤ 250 chars."""
    if not isinstance(body, dict):
        return fallback
    candidates = [
        body.get("Description"),
        body.get("description"),
        body.get("Message"),
        body.get("message"),
        (body.get("Data") or {}).get("Description")
            if isinstance(body.get("Data"), dict) else None,
        (body.get("data") or {}).get("description")
            if isinstance(body.get("data"), dict) else None,
        body.get("raw"),
    ]
    for c in candidates:
        if c and isinstance(c, str) and c.strip():
            return c.strip()[:250]
    return fallback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["hubtel_momo"])

HUBTEL_RECEIVE_URL = "https://rmp.hubtel.com/merchantaccount/merchants/{account}/receive/mobilemoney"
HUBTEL_SEND_URL = "https://smp.hubtel.com/api/merchants/{account}/send/mobilemoney"
HUBTEL_TIMEOUT = 10.0
HUBTEL_RESPONSE_OK = "0001"        # accepted, awaiting USSD / processing
HUBTEL_RESPONSE_DONE = "0000"       # final success on the callback
ANTI_DUP_WINDOW = "30 minutes"


# ─────────────────────── Request / response models ──────────────
class DepositRequest(BaseModel):
    amount: float
    customer_msisdn: str
    customer_name: str


class WithdrawRequest(BaseModel):
    amount: float
    recipient_msisdn: str
    recipient_name: str


# ────────────────── Internal helpers (additive) ─────────────────
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


async def _has_pending(conn, user_id: str, tx_type: str) -> bool:
    """Returns True if the user already has a pending hubtel-momo
    transaction of the given type within the anti-dup window."""
    row = await conn.fetchrow(
        f"""SELECT tx_id FROM transactions
              WHERE to_user_id = $1
                AND provider = 'hubtel_momo'
                AND type = $2
                AND status = 'pending'
                AND created_at > NOW() - INTERVAL '{ANTI_DUP_WINDOW}'
              LIMIT 1""",
        user_id, tx_type,
    )
    return row is not None


# ──────────────────────────── Public endpoints ────────────────────────
@router.get("/wallet/hubtel-momo/convert")
async def hubtel_momo_convert(
    request: Request,
    amount_usd: float = Query(..., gt=0),
):
    """Live USD→GHS preview. Polled by the frontend with a debounce
    while the user types. Auth required so we don't expose the rate
    publicly to scrapers."""
    await get_current_user(request)
    fx = await convert_usd_to_ghs(amount_usd)
    return {
        "amount_usd": fx["amount_usd"],
        "amount_ghs": fx["amount_ghs"],
        "rate": fx["rate"],
        "rate_source": fx["source"],
        "fetched_at": fx.get("fetched_at"),
        "message": f"You will receive approximately {fx['amount_ghs']:.2f} GHS on your Mobile Money.",
    }


@router.get("/wallet/hubtel-momo/limits")
async def hubtel_momo_limits(request: Request):
    """Returns the admin-configured min/max in USD for both legs."""
    await get_current_user(request)
    deposit = await get_deposit_limits()
    withdrawal = await get_withdrawal_limits()
    fx = await get_usd_to_ghs_info()
    return {
        "deposit": deposit,
        "withdrawal": withdrawal,
        "fx": {"rate": fx["rate"], "source": fx["source"], "fetched_at": fx.get("fetched_at")},
    }


# ────────────────────────────── Deposit ────────────────────────────
@router.post("/wallet/deposit/hubtel-momo")
async def hubtel_momo_deposit(req: DepositRequest, request: Request):
    user = await get_current_user(request)
    msisdn = normalize_msisdn(req.customer_msisdn or "")
    name = (req.customer_name or "").strip()[:120]

    # 1. Ghana-only.
    if not is_ghana_number(msisdn):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "non_eligible",
                "message": "This payment method is reserved for Ghana customers (+233). "
                           "You are not eligible for this deposit method.",
            },
        )

    # 2. Limits.
    limits = await get_deposit_limits()
    if req.amount < limits["min"]:
        raise HTTPException(status_code=400, detail={
            "error": "amount_too_low",
            "message": f"The minimum Mobile Money deposit amount is {limits['min']:.2f} USD.",
        })
    if req.amount > limits["max"]:
        raise HTTPException(status_code=400, detail={
            "error": "amount_too_high",
            "message": f"The maximum Mobile Money deposit amount is {limits['max']:.2f} USD.",
        })

    # 3. Channel detection.
    channel = detect_channel(msisdn)
    if not channel:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_network",
            "message": "Unknown Ghana network. Please check your number.",
        })

    # 4. Anti-dup pending.
    pool = await get_pool()
    async with pool.acquire() as conn:
        if await _has_pending(conn, user["user_id"], "deposit"):
            raise HTTPException(status_code=409, detail={
                "error": "pending_exists",
                "message": "You already have a pending Mobile Money deposit. "
                           "Please wait for its confirmation before initiating a new one.",
            })

        # 5. FX + DB row + Hubtel call.
        fx = await convert_usd_to_ghs(req.amount)
        client_reference = generate_client_reference()
        tx_id = f"dep_{uuid.uuid4().hex[:12]}"
        notes = (
            f"[Hubtel MoMo] {fx['amount_ghs']:.2f} GHS @ {fx['rate']:.4f} USD/GHS "
            f"(source={fx['source']})"
        )
        await conn.execute(
            """INSERT INTO transactions
                 (tx_id, to_user_id, type, status, amount, amount_usd,
                  currency, provider, reference, notes,
                  display_currency, display_amount)
               VALUES ($1, $2, 'deposit', 'pending', $3, $3,
                       'USD', 'hubtel_momo', $4, $5,
                       'GHS', $6)""",
            tx_id, user["user_id"], Decimal(str(req.amount)),
            client_reference, notes, Decimal(str(fx["amount_ghs"])),
        )

    # Hubtel call OUTSIDE the conn block (httpx might be slow).
    auth = await get_hubtel_auth()
    account = await get_collection_account()
    callback_base = await get_callback_base_url()
    if not auth or not account:
        # Roll the freshly-inserted row to failed and bail out cleanly.
        async with pool.acquire() as conn:
            await conn.execute("UPDATE transactions SET status='failed' WHERE tx_id=$1", tx_id)
        raise HTTPException(status_code=500, detail={
            "error": "hubtel_misconfigured",
            "message": "Mobile Money service is not configured. Please contact support.",
        })

    payload = {
        "CustomerName": name or "Japap user",
        "CustomerMsisdn": msisdn,
        "Channel": channel,
        "Amount": fx["amount_ghs"],
        "PrimaryCallbackUrl": f"{callback_base}/api/hubtel/callback/receive",
        "Description": "Japap Messenger Deposit",
        "ClientReference": client_reference,
    }
    response_code: Optional[str] = None
    response_body = None
    try:
        async with httpx.AsyncClient(timeout=HUBTEL_TIMEOUT,
                                      proxies=get_proxies() or None) as client:
            r = await client.post(
                HUBTEL_RECEIVE_URL.format(account=account),
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        try:
            response_body = r.json()
        except Exception:
            response_body = {"raw": r.text[:500]}
        response_code = str(response_body.get("ResponseCode") or response_body.get("responseCode") or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("[hubtel-momo] deposit network error: %s", e)

    async with pool.acquire() as conn:
        if response_code != HUBTEL_RESPONSE_OK:
            await conn.execute("UPDATE transactions SET status='failed' WHERE tx_id=$1", tx_id)
            await _insert_audit(conn, user["user_id"], "hubtel_momo_deposit_init_failed",
                                {"tx_id": tx_id, "response": response_body or {}, "code": response_code})
            # iter239a2 — Surface Hubtel's actual error to the client so they
            # know whether to retry, re-enter the number, or contact support.
            logger.warning("[hubtel-momo] deposit init failed code=%s body=%s",
                           response_code, response_body)
            raise HTTPException(status_code=502, detail={
                "error": "hubtel_init_failed",
                "code": response_code or None,
                "message": _extract_hubtel_message(
                    response_body,
                    "The USSD prompt could not be sent. Please try again in a few moments."),
            })
        await _insert_audit(conn, user["user_id"], "hubtel_momo_deposit_initiated",
                            {"tx_id": tx_id, "channel": channel, "amount_ghs": fx["amount_ghs"]})

    return {
        "status": "pending",
        "tx_id": tx_id,
        "client_reference": client_reference,
        "message": "A USSD prompt will be sent to your phone. Please confirm the payment.",
        "amount_usd": float(req.amount),
        "amount_ghs": fx["amount_ghs"],
        "rate": fx["rate"],
    }


@router.post("/hubtel/callback/receive")
async def hubtel_callback_receive(request: Request):
    """Public webhook from Hubtel — no auth, idempotent."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    response_code = str(body.get("ResponseCode") or body.get("responseCode") or "")
    data = body.get("Data") or body.get("data") or {}
    client_reference = (
        body.get("ClientReference")
        or body.get("clientReference")
        or data.get("ClientReference")
        or data.get("clientReference")
        or ""
    )
    if not client_reference:
        return {"status": "ignored", "reason": "no_reference"}

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                """SELECT tx_id, to_user_id, amount, status FROM transactions
                     WHERE reference = $1 AND provider = 'hubtel_momo'
                       AND type = 'deposit' AND status = 'pending'
                     FOR UPDATE""",
                client_reference,
            )
            if not tx:
                return {"status": "ignored"}
            if response_code == HUBTEL_RESPONSE_DONE:
                await conn.execute(
                    "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                    tx["amount"], datetime.now(timezone.utc), tx["to_user_id"],
                )
                await conn.execute(
                    "UPDATE transactions SET status='completed' WHERE tx_id=$1",
                    tx["tx_id"],
                )
                await _insert_audit(conn, tx["to_user_id"],
                                    "hubtel_momo_deposit_completed",
                                    {"tx_id": tx["tx_id"], "reference": client_reference})
                await _insert_notification(
                    conn, tx["to_user_id"], "deposit_completed",
                    "Mobile Money deposit confirmed",
                    f"Your Mobile Money deposit of {tx['amount']} USD has been confirmed and credited.",
                    {"tx_id": tx["tx_id"], "provider": "hubtel_momo"},
                )
            else:
                await conn.execute(
                    "UPDATE transactions SET status='failed' WHERE tx_id=$1",
                    tx["tx_id"],
                )
                await _insert_audit(conn, tx["to_user_id"],
                                    "hubtel_momo_deposit_failed",
                                    {"tx_id": tx["tx_id"], "code": response_code})
                await _insert_notification(
                    conn, tx["to_user_id"], "deposit_failed",
                    "Mobile Money deposit failed",
                    "Your Mobile Money deposit failed. No amount has been debited.",
                    {"tx_id": tx["tx_id"], "provider": "hubtel_momo"},
                )
    return {"status": "ok"}


# ────────────────────────────── Withdrawal ────────────────────────
@router.post("/wallet/withdraw/hubtel-momo")
async def hubtel_momo_withdraw(req: WithdrawRequest, request: Request):
    user = await get_current_user(request)
    msisdn = (req.recipient_msisdn or "").strip()
    name = (req.recipient_name or "").strip()[:120]

    # 1. Ghana-only.
    if not is_ghana_number(msisdn):
        raise HTTPException(status_code=403, detail={
            "error": "non_eligible",
            "message": "This payment method is reserved for Ghana customers (+233). "
                       "You are not eligible for this withdrawal method.",
        })

    # 2. Limits.
    limits = await get_withdrawal_limits()
    if req.amount < limits["min"] or req.amount > limits["max"]:
        raise HTTPException(status_code=400, detail={
            "error": "amount_out_of_range",
            "message": f"The withdrawal amount must be between {limits['min']:.2f} and "
                       f"{limits['max']:.2f} USD.",
        })

    # 3. Channel.
    channel = detect_channel(msisdn)
    if not channel:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_network",
            "message": "Unknown Ghana network.",
        })

    pool = await get_pool()
    fx = await convert_usd_to_ghs(req.amount)
    client_reference = generate_client_reference()
    tx_id = f"wd_{uuid.uuid4().hex[:12]}"
    amount_dec = Decimal(str(req.amount))

    # 4 + 5 + 6: anti-dup + atomic debit + tx insert in a single transaction.
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await _has_pending(conn, user["user_id"], "withdrawal"):
                raise HTTPException(status_code=409, detail={
                    "error": "pending_exists",
                    "message": "You already have a pending Mobile Money withdrawal. "
                               "Please wait for its confirmation before initiating a new one.",
                })
            wallet = await conn.fetchrow(
                "SELECT balance FROM wallets WHERE user_id = $1 FOR UPDATE",
                user["user_id"],
            )
            if not wallet or Decimal(str(wallet["balance"])) < amount_dec:
                raise HTTPException(status_code=400, detail={
                    "error": "insufficient_funds",
                    "message": "Insufficient balance.",
                })
            await conn.execute(
                "UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                amount_dec, datetime.now(timezone.utc), user["user_id"],
            )
            notes = (
                f"[Hubtel MoMo] {fx['amount_ghs']:.2f} GHS @ {fx['rate']:.4f} USD/GHS "
                f"(source={fx['source']})"
            )
            await conn.execute(
                """INSERT INTO transactions
                     (tx_id, from_user_id, type, status, amount, amount_usd,
                      currency, provider, reference, notes,
                      display_currency, display_amount)
                   VALUES ($1, $2, 'withdrawal', 'pending', $3, $3,
                           'USD', 'hubtel_momo', $4, $5,
                           'GHS', $6)""",
                tx_id, user["user_id"], amount_dec,
                client_reference, notes, Decimal(str(fx["amount_ghs"])),
            )
            await _insert_audit(conn, user["user_id"],
                                "hubtel_momo_withdrawal_debit",
                                {"tx_id": tx_id, "amount_usd": float(req.amount)})

    # 7. Hubtel call OUTSIDE the conn block.
    auth = await get_hubtel_auth()
    account = await get_disbursement_account()
    callback_base = await get_callback_base_url()
    if not auth or not account:
        await _refund_withdrawal(pool, tx_id, user["user_id"], amount_dec,
                                  reason="hubtel_misconfigured")
        raise HTTPException(status_code=500, detail={
            "error": "hubtel_misconfigured",
            "message": "Le service Mobile Money n'est pas configuré. Contactez le support.",
        })

    payload = {
        "RecipientName": name or "Japap user",
        "RecipientMsisdn": msisdn,
        "Channel": channel,
        "Amount": fx["amount_ghs"],
        "PrimaryCallbackURL": f"{callback_base}/api/hubtel/callback/send",
        "Description": "Retrait Japap Messenger",
        "ClientReference": client_reference,
    }
    response_code = None
    response_body = None
    try:
        async with httpx.AsyncClient(timeout=HUBTEL_TIMEOUT,
                                      proxies=get_proxies() or None) as client:
            r = await client.post(
                HUBTEL_SEND_URL.format(account=account),
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        try:
            response_body = r.json()
        except Exception:
            response_body = {"raw": r.text[:500]}
        response_code = str(response_body.get("ResponseCode") or response_body.get("responseCode") or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("[hubtel-momo] withdrawal network error: %s", e)

    if response_code != HUBTEL_RESPONSE_OK:
        # Atomic refund — Hubtel never accepted the order.
        await _refund_withdrawal(pool, tx_id, user["user_id"], amount_dec,
                                  reason=f"hubtel_init_failed_{response_code}")
        # iter239a2 — Surface Hubtel's actual error.
        logger.warning("[hubtel-momo] withdrawal init failed code=%s body=%s",
                       response_code, response_body)
        raise HTTPException(status_code=502, detail={
            "error": "hubtel_init_failed",
            "code": response_code or None,
            "message": _extract_hubtel_message(
                response_body,
                "The withdrawal could not be initiated. Your balance has been refunded."),
        })

    return {
        "status": "pending",
        "tx_id": tx_id,
        "client_reference": client_reference,
        "message": "Your withdrawal is being processed.",
        "amount_usd": float(req.amount),
        "amount_ghs": fx["amount_ghs"],
        "rate": fx["rate"],
    }


async def _refund_withdrawal(pool, tx_id: str, user_id: str,
                              amount: Decimal, reason: str) -> None:
    """Atomic refund used both by the immediate-failure path and the
    callback path. Idempotent via status check."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT status FROM transactions WHERE tx_id=$1 FOR UPDATE", tx_id,
            )
            if not tx or tx["status"] != "pending":
                return
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                amount, datetime.now(timezone.utc), user_id,
            )
            await conn.execute(
                "UPDATE transactions SET status='failed' WHERE tx_id=$1", tx_id,
            )
            await _insert_audit(conn, user_id,
                                "hubtel_momo_withdrawal_refunded",
                                {"tx_id": tx_id, "reason": reason})
            await _insert_notification(
                conn, user_id, "withdrawal_failed",
                "Mobile Money withdrawal failed",
                "Your Mobile Money withdrawal failed. Your balance has been refunded automatically.",
                {"tx_id": tx_id, "provider": "hubtel_momo"},
            )


@router.post("/hubtel/callback/send")
async def hubtel_callback_send(request: Request):
    """Public webhook from Hubtel for disbursements — no auth, idempotent."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    response_code = str(body.get("ResponseCode") or body.get("responseCode") or "")
    data = body.get("Data") or body.get("data") or {}
    client_reference = (
        body.get("ClientReference")
        or body.get("clientReference")
        or data.get("ClientReference")
        or data.get("clientReference")
        or ""
    )
    if not client_reference:
        return {"status": "ignored", "reason": "no_reference"}

    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            """SELECT tx_id, from_user_id, amount, status FROM transactions
                 WHERE reference = $1 AND provider = 'hubtel_momo'
                   AND type = 'withdrawal' AND status = 'pending'""",
            client_reference,
        )
    if not tx:
        return {"status": "ignored"}

    if response_code == HUBTEL_RESPONSE_DONE:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Re-check inside the lock for idempotence.
                row = await conn.fetchrow(
                    "SELECT status FROM transactions WHERE tx_id=$1 FOR UPDATE",
                    tx["tx_id"],
                )
                if not row or row["status"] != "pending":
                    return {"status": "already_processed"}
                await conn.execute(
                    "UPDATE transactions SET status='completed' WHERE tx_id=$1",
                    tx["tx_id"],
                )
                await _insert_audit(conn, tx["from_user_id"],
                                    "hubtel_momo_withdrawal_completed",
                                    {"tx_id": tx["tx_id"], "reference": client_reference})
                await _insert_notification(
                    conn, tx["from_user_id"], "withdrawal_completed",
                    "Mobile Money withdrawal confirmed",
                    "Your Mobile Money withdrawal has been completed successfully.",
                    {"tx_id": tx["tx_id"], "provider": "hubtel_momo"},
                )
        return {"status": "ok"}

    # Failure → refund.
    await _refund_withdrawal(pool, tx["tx_id"], tx["from_user_id"],
                              Decimal(str(tx["amount"])),
                              reason=f"hubtel_callback_{response_code}")
    return {"status": "ok"}


# Public router instance imported by server.py.
hubtel_momo_router = router

__all__ = ["router", "hubtel_momo_router"]
