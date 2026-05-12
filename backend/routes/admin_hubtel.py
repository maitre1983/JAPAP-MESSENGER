"""
iter239b — Admin endpoints for Hubtel MoMo credentials (additive).

Routes (all under `/api/admin/hubtel`, admin-only):

  • GET  /settings           → 4 fields, secrets masked (`••••••XXXX`)
  • PUT  /settings           → bulk update, audit-logged
  • POST /test-credentials   → live ping against Hubtel via Fixie proxy

The 4 keys live in `admin_settings`:
    hubtel_api_id, hubtel_api_key,
    hubtel_collection_account, hubtel_disbursement_account

`services/hubtel_momo.py::get_hubtel_auth` already reads these with the
proper `base64(api_id:api_key)` encoding — so updates take effect on the
NEXT request (settings cache TTL = 60 s, but `set_setting` invalidates
it immediately).
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import get_pool
from routes.auth import require_admin
from services.proxy_config import get_hubtel_proxy
from services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/hubtel", tags=["admin_hubtel"])

# Which keys we manage here. `hubtel_api_key` and `hubtel_api_id` are
# considered SECRETS and masked in GET responses.
SECRET_KEYS = {"hubtel_api_id", "hubtel_api_key"}
ALL_KEYS = SECRET_KEYS | {"hubtel_collection_account", "hubtel_disbursement_account"}
MASK = "••••••"

# Same Hubtel Mobile Money receive endpoint used by deposits. We POST an
# intentionally-invalid payload — we only care whether Hubtel:
#   • 401 / 4101 → auth wrong
#   • 4xx with a different code → auth OK, business or payload issue
#   • 200 0001/0000 → fully valid
HUBTEL_TEST_URL = "https://rmp.hubtel.com/merchantaccount/merchants/{account}/receive/mobilemoney"
HUBTEL_TEST_TIMEOUT = 10.0


def _mask(value: str | None) -> str:
    if not value:
        return ""
    v = str(value)
    if len(v) <= 4:
        return MASK
    return f"{MASK}{v[-4:]}"


class HubtelSettingsUpdate(BaseModel):
    hubtel_api_id: str | None = None
    hubtel_api_key: str | None = None
    hubtel_collection_account: str | None = None
    hubtel_disbursement_account: str | None = None


class HubtelTestRequest(BaseModel):
    # Optional overrides — if provided, we test these values WITHOUT saving.
    # If omitted, we test the currently-saved settings. Allows the admin to
    # try new credentials before persisting them.
    api_id: str | None = None
    api_key: str | None = None
    collection_account: str | None = None


async def _audit(admin: dict, key: str, before: str | None, after: str | None) -> None:
    is_secret = key in SECRET_KEYS
    before_safe = "***" if is_secret else (before or "")
    after_safe  = "***" if is_secret else (after  or "")
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO audit_logs (user_id, action, resource, details)
                   VALUES ($1, 'admin_setting_updated', 'system_settings', $2)""",
                admin.get("user_id"),
                json.dumps({"key": key, "before": before_safe, "after": after_safe,
                            "source": "admin_hubtel"}),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[admin-hubtel] audit failed for %s: %s", key, e)


@router.get("/settings")
async def admin_hubtel_get_settings(request: Request):
    """Returns the four Hubtel keys. Secrets are masked. The full value of
    `collection`/`disbursement` accounts is returned as-is (not sensitive
    — they're Hubtel merchant numbers, displayed everywhere in the dash)."""
    await require_admin(request)
    api_id      = await get_setting("hubtel_api_id") or ""
    api_key     = await get_setting("hubtel_api_key") or ""
    collection  = await get_setting("hubtel_collection_account") or ""
    disburse    = await get_setting("hubtel_disbursement_account") or ""
    return {
        "hubtel_api_id":               _mask(api_id),
        "hubtel_api_key":              _mask(api_key),
        "hubtel_collection_account":   collection,
        "hubtel_disbursement_account": disburse,
        "configured": {
            "hubtel_api_id":               bool(api_id.strip()),
            "hubtel_api_key":              bool(api_key.strip()),
            "hubtel_collection_account":   bool(collection.strip()),
            "hubtel_disbursement_account": bool(disburse.strip()),
        },
    }


@router.put("/settings")
async def admin_hubtel_update_settings(req: HubtelSettingsUpdate, request: Request):
    admin = await require_admin(request)
    updates: list[str] = []
    payload = req.model_dump(exclude_none=True)
    for key, value in payload.items():
        if key not in ALL_KEYS:
            continue
        if isinstance(value, str) and value.startswith(MASK):
            # Masked echo from the UI → do not overwrite the real value.
            continue
        value = (value or "").strip()
        if not value and key in SECRET_KEYS:
            # Never wipe secrets via a blank submission — admin must explicitly
            # set a new value.
            continue
        before = await get_setting(key)
        await set_setting(key, value)
        await _audit(admin, key, before, value)
        updates.append(key)
    return {"status": "ok", "updated": updates}


@router.post("/test-credentials")
async def admin_hubtel_test_credentials(req: HubtelTestRequest, request: Request):
    """Issues a real POST to Hubtel with the supplied (or saved) credentials.
    Reports back Hubtel's actual ResponseCode + Description so the admin
    knows exactly what's wrong (4101 = wrong keys-for-business, etc.)."""
    await require_admin(request)

    api_id = (req.api_id or "").strip() or (await get_setting("hubtel_api_id") or "").strip()
    api_key_raw = (req.api_key or "").strip()
    if not api_key_raw or api_key_raw.startswith(MASK):
        api_key_raw = (await get_setting("hubtel_api_key") or "").strip()
    account = (req.collection_account or "").strip() or \
              (await get_setting("hubtel_collection_account") or "").strip()

    if not api_id or not api_key_raw or not account:
        raise HTTPException(status_code=400, detail={
            "error": "missing_credentials",
            "message": "API ID, API Key, and Collection Account are required.",
            "missing": {
                "api_id":             not bool(api_id),
                "api_key":            not bool(api_key_raw),
                "collection_account": not bool(account),
            },
        })

    # Build Basic auth. Same logic as `services/hubtel_momo.get_hubtel_auth`
    # so the test exactly mirrors what the real deposit flow will send.
    if ":" in api_key_raw:
        api_key_raw = api_key_raw.split(":", 1)[1]
    raw = f"{api_id}:{api_key_raw}".encode("utf-8")
    basic = base64.b64encode(raw).decode("ascii")

    # Use a clearly-test payload — small amount, fake msisdn — Hubtel will
    # validate the auth + business binding BEFORE checking the payload, so
    # we'll get the auth error (4101) immediately even with a bogus number.
    payload = {
        "CustomerName": "JAPAP credential test",
        "CustomerMsisdn": "233241234567",
        "Channel": "mtn-gh",
        "Amount": 0.01,
        "PrimaryCallbackUrl": "https://japapmessenger.com/api/hubtel/callback/receive",
        "Description": "Japap credential validation (do not honour)",
        "ClientReference": "japap_test_credentials",
    }
    url = HUBTEL_TEST_URL.format(account=account)
    logger.info(
        "[admin-hubtel-test] POST %s | account=%s | auth_prefix=%s... | id_len=%s | key_len=%s",
        url, account, basic[:8], len(api_id), len(api_key_raw),
    )

    http_status: int | None = None
    response_body: dict | None = None
    network_error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=HUBTEL_TEST_TIMEOUT,
                                      proxy=get_hubtel_proxy()) as client:
            r = await client.post(
                url,
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        http_status = r.status_code
        try:
            response_body = r.json()
        except Exception:
            response_body = {"raw": r.text[:500]}
    except Exception as e:  # noqa: BLE001
        network_error = str(e)
        logger.error("[admin-hubtel-test] network error: %s", e)

    if network_error:
        return {
            "ok": False,
            "verdict": "network_error",
            "http_status": None,
            "code": None,
            "message": f"Network error contacting Hubtel: {network_error}",
            "raw": None,
        }

    code = str((response_body or {}).get("ResponseCode")
               or (response_body or {}).get("responseCode") or "")
    description = (
        (response_body or {}).get("Description")
        or (response_body or {}).get("description")
        or (response_body or {}).get("Message")
        or (response_body or {}).get("message")
        or ""
    )

    # Verdict — based on Hubtel's documented codes.
    # 0001 = accepted/processing (best case — full path validated).
    # 0000 = completed (very rare on a test call but still a green light).
    # 4101 = "Client request keys do not match API keys on business" (creds wrong).
    # 4xxx with HTTP 401/403 = auth issue.
    # Anything else with HTTP 200/4xx and a parseable code = credentials look OK,
    # but the request itself was rejected (channel, amount, etc.).
    if code in ("0000", "0001"):
        verdict = "ok"
        ok = True
        msg = f"Credentials are valid. Hubtel accepted the test request (code {code})."
    elif http_status in (401, 403) or code in ("4101", "4100", "4102", "401", "403"):
        verdict = "auth_failed"
        ok = False
        msg = f"Hubtel rejected the credentials (code {code or http_status}): {description or 'authentication failed'}"
    elif http_status == 404:
        verdict = "account_not_found"
        ok = False
        msg = f"Hubtel could not find the collection account `{account}` (HTTP 404). Verify the merchant number."
    else:
        verdict = "rejected_other"
        ok = False
        msg = f"Hubtel responded with code {code or http_status}: {description or 'unknown rejection'}"

    logger.info(
        "[admin-hubtel-test] verdict=%s | http=%s | code=%s | desc=%s",
        verdict, http_status, code, description,
    )

    return {
        "ok": ok,
        "verdict": verdict,
        "http_status": http_status,
        "code": code or None,
        "message": msg,
        "description": description or None,
        # Raw body for debugging, capped to keep the JSON small.
        "raw": response_body,
    }


admin_hubtel_router = router


# ─────────────────── iter239c — admin manual credit ─────────────────────
class ManualCreditRequest(BaseModel):
    external_tx_id: str = Field(..., min_length=1, max_length=120)
    note:           str = Field(..., min_length=10, max_length=500)


@router.post("/momo/credit-manual/{tx_id}")
async def admin_hubtel_momo_credit_manual(
    tx_id: str, req: ManualCreditRequest, request: Request,
):
    """iter239c — Admin force-credits a pending Hubtel MoMo deposit.
    Used when Hubtel never sent the callback but the admin has confirmed
    the payment on the Hubtel dashboard. Stores the Hubtel
    ExternalTransactionId + admin note in `transactions.notes`, writes an
    audit log row, and notifies the user.

    SAFETY: only operates on `provider='hubtel_momo' AND type='deposit'
    AND status='pending'` rows. Refuses to touch a completed/failed tx
    (idempotent, returns 409)."""
    admin = await require_admin(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                """SELECT tx_id, to_user_id, amount, status, reference, notes, provider, type
                     FROM transactions WHERE tx_id = $1 FOR UPDATE""",
                tx_id,
            )
            if not tx:
                raise HTTPException(status_code=404, detail="Transaction introuvable")
            if tx["provider"] != "hubtel_momo" or tx["type"] != "deposit":
                raise HTTPException(status_code=400, detail={
                    "error": "wrong_kind",
                    "message": "This endpoint only handles Hubtel MoMo deposits.",
                })
            if tx["status"] != "pending":
                raise HTTPException(status_code=409, detail={
                    "error": "not_pending",
                    "current_status": tx["status"],
                    "message": f"Transaction is already in status `{tx['status']}` — refusing to credit again.",
                })

            # Atomic credit + audit + notify.
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                Decimal(str(tx["amount"])), datetime.now(timezone.utc), tx["to_user_id"],
            )
            new_notes = (tx["notes"] or "")
            tag = (f"manual_credit_by_admin={admin['user_id']} | "
                   f"external_tx_id={req.external_tx_id.strip()} | "
                   f"note={req.note.strip()[:300]}")
            new_notes = (new_notes + " | " + tag).strip(" |") if new_notes else tag
            await conn.execute(
                "UPDATE transactions SET status='completed', notes=$2 WHERE tx_id=$1",
                tx_id, new_notes,
            )
            await conn.execute(
                """INSERT INTO audit_logs (user_id, action, resource, details)
                   VALUES ($1, 'hubtel_momo_manual_credit', 'wallet', $2)""",
                admin["user_id"],
                json.dumps({
                    "tx_id": tx_id,
                    "credited_user_id": tx["to_user_id"],
                    "amount_usd": float(tx["amount"]),
                    "reference": tx["reference"],
                    "external_tx_id": req.external_tx_id.strip(),
                    "note": req.note.strip()[:300],
                }),
            )
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                   VALUES ($1, $2, 'deposit_completed', 'Mobile Money deposit credited',
                           $3, $4)""",
                notif_id, tx["to_user_id"],
                f"Your Mobile Money deposit of {tx['amount']} USD has been credited.",
                json.dumps({"tx_id": tx_id, "provider": "hubtel_momo",
                            "via": "admin_manual",
                            "external_tx_id": req.external_tx_id.strip()}),
            )

    logger.info(
        "[admin-hubtel] manual credit | tx_id=%s | user=%s | amount=%s USD | "
        "external_tx_id=%s | by_admin=%s",
        tx_id, tx["to_user_id"], tx["amount"],
        req.external_tx_id.strip(), admin["user_id"],
    )
    return {
        "status": "credited",
        "tx_id": tx_id,
        "amount_usd": float(tx["amount"]),
        "external_tx_id": req.external_tx_id.strip(),
    }


__all__ = ["router", "admin_hubtel_router"]