import uuid
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
from database import get_pool
from routes.auth import get_current_user
from routes.referrals import check_and_activate_referral
from routes.kyc import is_user_kyc_approved
from services.settings_service import get_bool, get_setting, get_float, get_json
from middleware.rate_limit import limiter as _limiter
from services.admin_alerts import (
    trigger_large_withdraw_alert,
    trigger_withdraw_without_kyc,
    trigger_send_spam,
    SEND_SPAM_COUNT,
    SEND_SPAM_WINDOW_MINUTES,
)
from services.ops_notifications import notify_deposit, notify_withdraw
from services.push_service import send_push_to_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/wallet", tags=["wallet"])


DEPOSIT_METHODS = {
    "usdt_trc20": {"label": "USDT (TRC20) manuel", "icon": "₮", "icon_url": "/crypto/usdt.png", "chain_icon_url": "/crypto/tron.png", "chain": "TRON"},
    "usdt_bep20": {"label": "USDT (BEP20) manuel", "icon": "₮", "icon_url": "/crypto/usdt.png", "chain_icon_url": "/crypto/bnb.png", "chain": "BSC"},
    "hubtel_card": {"label": "Carte bancaire via Hubtel", "icon": "💳", "icon_url": "", "chain_icon_url": "", "chain": ""},
    "nowpayments_usdttrc20": {"label": "USDT (TRC20) auto via NowPayments", "icon": "⚡", "icon_url": "/crypto/usdt.png", "chain_icon_url": "/crypto/tron.png", "chain": "TRON"},
    "nowpayments_usdtbsc": {"label": "USDT (BEP20) auto via NowPayments", "icon": "⚡", "icon_url": "/crypto/usdt.png", "chain_icon_url": "/crypto/bnb.png", "chain": "BSC"},
}
WITHDRAW_METHODS = {
    "usdt_trc20": {"label": "USDT (TRC20)", "icon": "₮", "icon_url": "/crypto/usdt.png", "chain_icon_url": "/crypto/tron.png", "chain": "TRON"},
    "usdt_bep20": {"label": "USDT (BEP20)", "icon": "₮", "icon_url": "/crypto/usdt.png", "chain_icon_url": "/crypto/bnb.png", "chain": "BSC"},
}


async def _user_active_plan_id(conn, user_id: str) -> str | None:
    """Returns the active Pro plan_id for this user, or None if not Pro.
    Checks `subscriptions.plan_type` (active + unexpired)."""
    row = await conn.fetchrow("""
        SELECT plan_type FROM subscriptions
        WHERE user_id = $1 AND status = 'active' AND expires_at > NOW()
        ORDER BY expires_at DESC LIMIT 1
    """, user_id)
    return row['plan_type'] if row else None


def _credit_usd(tx) -> Decimal:
    """iter158 — Return the USD amount to credit on the wallet.

    Priority:
      1. `tx.amount_usd` — canonical, populated at checkout creation.
      2. `tx.amount`     — legacy rows + non-deposit flows (already USD).
    Never reads the transaction `currency` label — that only describes
    what the user saw, not what we owe them.
    """
    try:
        v = tx["amount_usd"]
    except (KeyError, TypeError):
        v = None
    if v is None:
        v = tx["amount"]
    if not isinstance(v, Decimal):
        v = Decimal(str(v))
    return v


async def _resolve_withdraw_fee(conn, user_id: str, method: str | None = None) -> dict:
    """Returns the effective withdraw fee config for this user.
    Order of precedence:
      1) Per-network override (withdraw_fee_value_<method>) if > 0.
      2) Per-plan override from `withdraw_fee_by_plan_json` if user is Pro.
      3) PRO blanket override (withdraw_fee_value_pro) if user is_pro and > 0.
      4) Global default `withdraw_fee_mode` / `withdraw_fee_value`.
    Returns {"mode": "percent"|"flat", "value": float, "plan_id": str|None, "source": str}.
    """
    default_mode = (await get_setting("withdraw_fee_mode") or "percent").lower()
    default_value = float(await get_setting("withdraw_fee_value") or "0")

    # 1) Per-network override
    if method:
        meth_key = f"withdraw_fee_value_{method.replace('usdt_', '')}"  # trc20 / bep20
        try:
            meth_val = float(await get_setting(meth_key) or "0")
        except Exception:
            meth_val = 0.0
        if meth_val > 0:
            return {"mode": default_mode, "value": meth_val, "plan_id": None, "source": "network"}

    plan_id = await _user_active_plan_id(conn, user_id)
    if plan_id:
        by_plan = await get_json("withdraw_fee_by_plan_json", {}) or {}
        entry = by_plan.get(plan_id)
        if isinstance(entry, dict) and "mode" in entry and "value" in entry:
            mode = str(entry["mode"]).lower()
            if mode not in ("percent", "flat"):
                mode = default_mode
            try:
                value = float(entry["value"])
            except (TypeError, ValueError):
                value = default_value
            return {"mode": mode, "value": value, "plan_id": plan_id, "source": "plan"}

    # 3) PRO blanket remise
    try:
        is_pro = bool(await conn.fetchval("SELECT is_pro FROM users WHERE user_id = $1", user_id))
    except Exception:
        is_pro = False
    if is_pro:
        try:
            pro_val = float(await get_setting("withdraw_fee_value_pro") or "0")
        except Exception:
            pro_val = 0.0
        if pro_val > 0:
            return {"mode": default_mode, "value": pro_val, "plan_id": plan_id, "source": "pro_blanket"}

    return {"mode": default_mode, "value": default_value, "plan_id": plan_id, "source": "default"}


class SendMoneyRequest(BaseModel):
    to_user_id: str
    amount: float
    notes: str = ""
    # iter141eight — idempotency. Frontend MUST generate a unique uuid per
    # confirm-click and send the same value on retries so the backend can
    # short-circuit a duplicate transfer (network glitch, double-tap…).
    idempotency_key: Optional[str] = None


class DepositRequest(BaseModel):
    amount: float
    method: str = "usdt_trc20"
    reference: str = ""     # Used as tx_hash for crypto, session_id for Hubtel
    notes: str = ""


class WithdrawRequest(BaseModel):
    amount: float
    method: str = "usdt_trc20"
    address: str = ""       # USDT wallet address (required)
    notes: str = ""


@router.get("/payment-methods")
async def payment_methods(request: Request):
    """Returns enabled deposit/withdraw methods + fee config. Frontend reads this
    to render the wallet UI dynamically — no hardcoded method list. The `fee`
    block reflects the CURRENT user's effective fee (Pro plan override if any)."""
    user = await get_current_user(request)
    # admin can disable methods individually
    dep_enabled = {
        k: await get_bool(f"deposit_{k}_enabled", True) for k in DEPOSIT_METHODS
    }
    wd_enabled = {
        k: await get_bool(f"withdraw_{k}_enabled", True) for k in WITHDRAW_METHODS
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        fee_resolved = await _resolve_withdraw_fee(conn, user['user_id'])
    min_amount = float(await get_setting("withdraw_min_amount_usd") or 0)

    # Best Pro tier fee preview (for upsell banner to non-Pro users).
    best_pro = None
    if fee_resolved["source"] == "default":
        by_plan = await get_json("withdraw_fee_by_plan_json", {}) or {}
        best = None
        for pid, entry in by_plan.items():
            if not isinstance(entry, dict):
                continue
            mode = str(entry.get("mode", "")).lower()
            try:
                val = float(entry.get("value", 0))
            except (TypeError, ValueError):
                continue
            if mode not in ("percent", "flat"):
                continue
            # Lower percent value = better deal (use 0% as floor). For `flat`,
            # we can't reliably compare without a reference amount, so we only
            # recommend 'percent' plans vs the current default when it is percent.
            if best is None or val < best["value"]:
                best = {"plan_id": pid, "mode": mode, "value": val}
        # Only surface the upsell if it strictly beats the default fee.
        if best and (
            fee_resolved["mode"] == "percent" and best["mode"] == "percent"
            and best["value"] < fee_resolved["value"]
        ):
            # Attach a human label (e.g., "Business Pro")
            try:
                async with pool.acquire() as conn2:
                    plan_row = await conn2.fetchrow(
                        "SELECT name, price_usd FROM pro_plans WHERE plan_id = $1",
                        best["plan_id"]
                    )
                if plan_row:
                    best["plan_name"] = plan_row["name"]
                    best["price_usd"] = float(plan_row["price_usd"])
            except Exception:
                pass
            best_pro = best

    return {
        "deposit": [
            {"id": k, **v, "enabled": dep_enabled.get(k, True)}
            for k, v in DEPOSIT_METHODS.items()
        ],
        "withdraw": [
            {"id": k, **v, "enabled": wd_enabled.get(k, True)}
            for k, v in WITHDRAW_METHODS.items()
        ],
        "fee": {
            "mode": fee_resolved["mode"],
            "value": fee_resolved["value"],
            "label": ("%" if fee_resolved["mode"] == "percent" else "USDT"),
            "source": fee_resolved["source"],          # "plan" | "default"
            "plan_id": fee_resolved["plan_id"],        # user's active Pro plan or null
        },
        "best_pro_fee": best_pro,  # null if user already Pro or nothing better
        "min_withdraw_usd": min_amount,
    }


@router.get("/deposit/conversion-preview")
async def deposit_conversion_preview(
    amount: float,
    method: str,
    request: Request,
):
    """iter159 — Live preview of the provider amount for a given USD deposit.

    Called by the deposit form while the user types. Returns what the
    provider will actually charge (Hubtel → GHS, NowPayments → USDT
    amount calculated by NP) so the user knows *before* clicking pay.

    Never makes a paid external call to NowPayments (we only hit the
    local rates cache). For USDT estimates we use the `to_usd` rate
    which is ≈1 in practice.
    """
    user = await get_current_user(request)  # noqa: F841 — auth guard only
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Montant invalide")
    if amount > 10000:
        raise HTTPException(status_code=400, detail="Montant maximum dépassé")

    from services.currency_conversion import provider_context, usd_to
    # Detect provider from method slug used by /deposit endpoint.
    if method in ("card", "mobile_money") or method.startswith("hubtel"):
        ctx = await provider_context("hubtel", amount)
        return {
            "amount_usd": str(ctx["amount_usd"]),
            "provider": "hubtel",
            "provider_currency": ctx["provider_currency"],
            "provider_amount": str(ctx["provider_amount"]),
            "exchange_rate": str(ctx["exchange_rate"]),
            "display_note": (
                f"Hubtel débitera {ctx['provider_amount']} "
                f"{ctx['provider_currency']} "
                f"(1 USD ≈ {ctx['exchange_rate']} GHS)."
            ),
        }
    if method.startswith(("nowpayments_", "usdt_")):
        # NowPayments invoices in USD; the user pays the equivalent in
        # crypto. We return a USDT estimate (1 USDT ≈ 1 USD) so the UI
        # shows an accurate order of magnitude — the exact USDT figure
        # depends on live crypto rates fixed by NP at checkout time.
        usdt_estimate = await usd_to(amount, "USD")  # identity (1:1)
        return {
            "amount_usd": str(usdt_estimate),
            "provider": "nowpayments",
            "provider_currency": "USDT",
            "provider_amount": str(usdt_estimate),
            "exchange_rate": "1.0000",
            "display_note": (
                f"NowPayments créera une facture de {amount} USD. "
                f"Tu paieras ≈ {usdt_estimate} USDT (le taux exact "
                "est fixé par NowPayments au moment du checkout)."
            ),
        }
    # Internal / unsupported methods → no conversion.
    return {
        "amount_usd": str(amount),
        "provider": "internal",
        "provider_currency": "USD",
        "provider_amount": str(amount),
        "exchange_rate": "1.0000",
        "display_note": "Aucune conversion — transfert interne USD.",
    }


class DisplayCurrencyRequest(BaseModel):
    display_currency: str  # USD / local (auto-detect) / XAF / GHS / EUR / …


@router.post("/display-currency")
async def set_display_currency(req: DisplayCurrencyRequest, request: Request):
    """iter158 — User preference for balance display currency.

    The wallet balance itself ALWAYS stays in USD. This only controls
    how the app displays it. Accepted values:
      • "USD"    — show raw USD number
      • "local"  — auto-detect from IP / country (default)
      • any ISO4217 code — explicit override
    """
    user = await get_current_user(request)
    code = (req.display_currency or "").strip().upper()
    # "LOCAL" is stored as NULL → auto-detect at read time.
    if code == "LOCAL" or code == "":
        code = None
    elif len(code) != 3 or not code.isalpha():
        raise HTTPException(status_code=400, detail="Code ISO 4217 invalide")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET display_currency = $1 WHERE user_id = $2",
            code, user["user_id"],
        )
    return {"display_currency": code or "local"}


@router.get("/balance")
async def get_balance(request: Request):
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1", user['user_id'])
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")
    # iter158 — `balance` is ALWAYS stored in USD. Return the USD amount +
    # the user's preferred display amount (local currency via IP or
    # explicit preference). Frontend decides which one to show.
    # iter161 — pass `request` so "Local (auto)" can fall back to IP geoloc
    # when the user has no stored country/display_currency preference.
    from services.currency_conversion import user_display_currency, usd_to, get_rate_vs_usd
    balance_usd = wallet["balance"]
    display_ccy = await user_display_currency(
        user["user_id"], fallback="USD", request=request,
    )
    if display_ccy == "USD":
        display_amount = balance_usd
        fx_rate = "1"
    else:
        display_amount = await usd_to(balance_usd, display_ccy, rounding=2)
        fx_rate = str(await get_rate_vs_usd(display_ccy))
    return {
        "user_id": user["user_id"],
        # canonical (CEO mandate iter178)
        "balance_usd": str(balance_usd),
        "currency": "USD",
        # display (local or USD depending on user pref)
        "display_amount": str(display_amount),
        "display_currency": display_ccy,
        # iter178 — explicit local fields per CEO spec
        "balance_local": str(display_amount),
        "currency_local": display_ccy,
        "fx_rate": fx_rate,
        # legacy field kept for backward compat with older frontend builds
        "balance": str(balance_usd),
        "is_locked": wallet["is_locked"],
    }


@router.get("/hubtel/test-connection")
async def hubtel_test_connection(request: Request):
    """Admin-only: verifies Hubtel credentials by hitting their initiate endpoint."""
    from routes.admin import require_admin
    await require_admin(request)
    from services.hubtel_service import test_connection
    return await test_connection()


@router.get("/hubtel/server-ip")
async def hubtel_server_ip(request: Request):
    """Admin-only: returns our server's public IP (to send to retail@hubtel.com
    for whitelisting — required to use Hubtel's Transaction Status Check API)."""
    from routes.admin import require_admin
    await require_admin(request)
    import httpx
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get("https://api.ipify.org?format=json")
            ip = r.json().get("ip", "unknown")
        except Exception as e:
            ip = f"(impossible de résoudre: {e})"
    return {
        "server_ip": ip,
        "instructions": (
            "Envoyez cette IP à retail@hubtel.com en leur demandant de la whitelister "
            "sur votre compte Hubtel pour l'API Transaction Status Check (rmsc.hubtel.com). "
            "Sans whitelist, les dépôts seront crédités via webhook mais marqués 'unverified'."
        ),
    }


# ────────────────────────────────────────────────────────────────────────
# iter191 — Hubtel debug & audit endpoints (admin-only).
# These give the CEO an EAA-vs-JAPAP diff tool: every initiate + webhook
# call is persisted in `hubtel_call_logs` (request, response, status, ms).
# ────────────────────────────────────────────────────────────────────────
@router.get("/admin/hubtel/logs")
async def admin_hubtel_logs(request: Request, limit: int = 50, kind: str = ""):
    """List the most recent Hubtel API calls + webhooks for diagnosis."""
    user = await get_current_user(request)
    if not (user.get('is_admin') or user.get('role') in ('admin', 'superadmin')):
        raise HTTPException(status_code=403, detail="Admin only")
    limit = max(1, min(200, int(limit or 50)))
    pool = await get_pool()
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
            )""")
        if kind:
            rows = await conn.fetch("""
                SELECT id, kind, tx_id, request, response_status, response,
                       error, took_ms, created_at
                FROM hubtel_call_logs WHERE kind=$2
                ORDER BY id DESC LIMIT $1
            """, limit, kind)
        else:
            rows = await conn.fetch("""
                SELECT id, kind, tx_id, request, response_status, response,
                       error, took_ms, created_at
                FROM hubtel_call_logs
                ORDER BY id DESC LIMIT $1
            """, limit)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        for k in ("request", "response"):
            v = d.get(k)
            if isinstance(v, str):
                try:
                    import json as _json
                    d[k] = _json.loads(v)
                except Exception:
                    pass
        out.append(d)
    return {"logs": out, "count": len(out)}


@router.post("/admin/hubtel/debug-initiate")
async def admin_hubtel_debug_initiate(request: Request):
    """Run a tiny live initiate and return the FULL Hubtel response so the
    admin can compare ours with EAA field-by-field. Body (optional):
    {"amount_usd": 0.05, "description": "..."}
    """
    user = await get_current_user(request)
    if not (user.get('is_admin') or user.get('role') in ('admin', 'superadmin')):
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        body = await request.json()
    except Exception:
        body = {}
    amount_usd = float(body.get("amount_usd") or 0.05)
    description = str(body.get("description") or f"JAPAP debug {uuid.uuid4().hex[:6]}")[:128]
    public_base = (
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("REACT_APP_BACKEND_URL")
        or str(request.base_url).rstrip("/")
    )
    public_frontend = (
        os.environ.get("FRONTEND_URL")
        or os.environ.get("PUBLIC_FRONTEND_URL")
        or public_base
    )
    tx_id = f"debug_{uuid.uuid4().hex[:12]}"
    from services.hubtel_service import (
        initiate_checkout, HubtelConfigError, HubtelAPIError,
    )
    try:
        ck = await initiate_checkout(
            tx_id=tx_id, amount=amount_usd,
            description=description,
            public_base_url=public_base,
            public_frontend_url=public_frontend,
        )
    except HubtelConfigError as e:
        raise HTTPException(status_code=400, detail=f"Config Hubtel: {e}")
    except HubtelAPIError as e:
        raise HTTPException(status_code=502, detail=f"Hubtel API: {e}")
    return {
        "ok": True,
        "tx_id": tx_id,
        "amount_usd": amount_usd,
        "provider_amount_ghs": ck.get("provider_amount"),
        "exchange_rate": ck.get("exchange_rate"),
        "checkout_url": ck.get("checkout_url"),
        "checkout_direct_url": ck.get("checkout_direct_url"),
        "checkout_tx_id": ck.get("checkout_tx_id"),
        "raw_response": ck.get("raw"),
        "explainer": (
            "Open `checkout_url` in a browser. If the page loads but the "
            "MoMo prompt is not delivered after entering your number, the "
            "issue is the Hubtel merchant account configuration (MoMo "
            "channel activation) — NOT JAPAP code. Contact retail@hubtel.com."
        ),
    }




@router.post("/hubtel/verify/{tx_id}")
async def hubtel_verify_tx(tx_id: str, request: Request):
    """Manually re-verify a Hubtel deposit against Hubtel's status API and credit
    the wallet if the payment is confirmed. Useful when a user says "I paid but
    my wallet is not credited" (e.g., webhook lost). Owner-or-admin only."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT * FROM transactions WHERE tx_id = $1 AND type = 'deposit'",
            tx_id,
        )
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction introuvable")
        if tx['to_user_id'] != user['user_id'] and not user.get('is_admin'):
            raise HTTPException(status_code=403, detail="Accès refusé")
        if tx['status'] == 'completed':
            return {"status": "already_completed", "tx_id": tx_id}

        from services.hubtel_service import (
            verify_transaction_status, HubtelConfigError, HubtelAPIError,
        )
        try:
            verification = await verify_transaction_status(client_reference=tx_id)
        except HubtelConfigError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except HubtelAPIError as e:
            raise HTTPException(status_code=502, detail=str(e))

        if not verification["ok"]:
            return {
                "status": "check_failed",
                "tx_id": tx_id,
                "reason": verification.get("reason"),
                "hubtel_status": verification.get("status"),
            }
        if not verification["is_paid"]:
            return {
                "status": "not_paid_yet",
                "tx_id": tx_id,
                "hubtel_status": verification.get("status"),
            }

        # Hubtel says Paid — credit now.
        provider_ref = verification.get("provider_ref") or tx['reference']
        async with conn.transaction():
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                tx['amount'], datetime.now(timezone.utc), tx['to_user_id'],
            )
            await conn.execute(
                "UPDATE transactions SET status = 'completed', reference = $1 WHERE tx_id = $2",
                provider_ref, tx_id,
            )
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """
                INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                VALUES ($1, $2, 'deposit_completed', 'Dépôt confirmé',
                        $3, $4)
                """,
                notif_id, tx['to_user_id'],
                f"Votre dépôt de {tx['amount']} USD a été crédité.",
                f'{{"tx_id": "{tx_id}", "provider": "hubtel", "ref": "{provider_ref}", "method": "manual_verify"}}',
            )
        return {"status": "completed", "tx_id": tx_id, "verified": True, "hubtel_status": verification["status"]}



@router.get("/transactions")
async def get_transactions(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), type: Optional[str] = None):
    user = await get_current_user(request)
    pool = await get_pool()
    offset = (page - 1) * limit
    async with pool.acquire() as conn:
        base_query = "FROM transactions WHERE (from_user_id = $1 OR to_user_id = $1)"
        params = [user['user_id']]
        if type:
            base_query += f" AND type = ${len(params)+1}"
            params.append(type)
        
        count = await conn.fetchval(f"SELECT COUNT(*) {base_query}", *params)
        params_with_limit = params + [limit, offset]
        rows = await conn.fetch(
            f"SELECT * {base_query} ORDER BY created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}",
            *params_with_limit
        )
        
        transactions = []
        # iter158 — include display-currency equivalent on each row so the
        # frontend can render the user's preferred view without a second
        # round-trip. `amount` and `amount_usd` remain authoritative (USD).
        from services.currency_conversion import user_display_currency, usd_to
        display_ccy = await user_display_currency(user["user_id"], fallback="USD")
        for row in rows:
            tx = dict(row)
            tx["amount"] = str(tx["amount"])
            tx["fee"] = str(tx["fee"])
            tx["created_at"] = tx["created_at"].isoformat()
            if tx.get("amount_usd") is not None:
                tx["amount_usd"] = str(tx["amount_usd"])
            if tx.get("provider_amount") is not None:
                tx["provider_amount"] = str(tx["provider_amount"])
            if tx.get("exchange_rate") is not None:
                tx["exchange_rate"] = str(tx["exchange_rate"])
            if tx.get("display_amount") is not None:
                tx["display_amount"] = str(tx["display_amount"])
            # live display equivalent using the user's current preference
            base = tx.get("amount_usd") or tx["amount"]
            tx["display_view"] = {
                "currency": display_ccy,
                "amount": str(await usd_to(base, display_ccy)
                              if display_ccy != "USD" else base),
            }
            transactions.append(tx)

        return {"transactions": transactions, "total": count, "page": page, "limit": limit}


@router.get("/fees-preview")
async def fees_preview(amount: float = Query(...), request: Request = None):
    """Preview the transfer fee that will be applied to a `send` of `amount`.
    Public (requires auth) — consumed by the frontend send form."""
    user = await get_current_user(request)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    fee = Decimal("0.00")
    enabled = await get_bool("send_fee_enabled", False)
    is_pro = bool(user.get("is_pro"))
    pro_enabled = await get_bool("send_fee_pro_enabled", True)
    mode = (await get_setting("send_fee_mode")) or "percent"
    raw_val = await get_setting(
        "send_fee_pro_value" if (is_pro and pro_enabled) else "send_fee_value"
    )
    try:
        val = Decimal(str(raw_val)) if raw_val not in (None, "") else Decimal("0")
    except Exception:
        val = Decimal("0")
    if enabled and not (is_pro and not pro_enabled):
        amt = Decimal(str(amount))
        if mode == "percent":
            fee = (amt * val / Decimal("100")).quantize(Decimal("0.01"))
        else:
            fee = val.quantize(Decimal("0.01"))
        try:
            floor = Decimal(str(await get_setting("send_fee_min") or "0"))
        except Exception:
            floor = Decimal("0")
        try:
            cap = Decimal(str(await get_setting("send_fee_max") or "0"))
        except Exception:
            cap = Decimal("0")
        if floor > 0 and fee < floor:
            fee = floor
        if cap > 0 and fee > cap:
            fee = cap
    return {
        "enabled": bool(enabled),
        "mode": mode,
        "value": str(val),
        "is_pro": is_pro,
        "fee": str(fee),
        "amount": str(amount),
        "net_to_recipient": str(Decimal(str(amount)) - fee),
    }


@router.post("/send")
@_limiter.limit("5/minute")
async def send_money(req: SendMoneyRequest, request: Request):
    user = await get_current_user(request)
    if req.to_user_id == user['user_id']:
        raise HTTPException(status_code=400, detail="Cannot send money to yourself")
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # iter141eight — explicit recipient existence check up-front so we
    # surface a clean 404 with the actual user_id we received (helps debug).
    pool = await get_pool()
    async with pool.acquire() as conn:
        target_user = await conn.fetchrow(
            "SELECT user_id, is_active, first_name, last_name, username FROM users WHERE user_id = $1",
            req.to_user_id,
        )
        if not target_user:
            logger.warning("[wallet.send] recipient_id NOT FOUND in users table: %r (sender=%s)",
                           req.to_user_id, user['user_id'])
            raise HTTPException(status_code=404, detail="Destinataire introuvable. Sélectionne un utilisateur dans la liste de recherche.")
        if not target_user['is_active']:
            raise HTTPException(status_code=403, detail="Le compte du destinataire est suspendu.")

        # iter141eight — idempotency : if the same key was used in the last
        # 10 minutes for the same sender + same recipient, return the prior
        # transaction unchanged. Stored on transactions.notes JSON tail so
        # we don't need a new column.
        if req.idempotency_key:
            existing = await conn.fetchrow(
                """SELECT tx_id, amount, fee, status, created_at FROM transactions
                    WHERE from_user_id = $1 AND to_user_id = $2 AND type = 'send'
                      AND notes LIKE '%idem:' || $3 || '%'
                      AND created_at > NOW() - INTERVAL '10 minutes'
                    ORDER BY created_at DESC LIMIT 1""",
                user['user_id'], req.to_user_id, req.idempotency_key,
            )
            if existing:
                logger.info("[wallet.send] idempotent replay tx=%s key=%s",
                            existing["tx_id"], req.idempotency_key)
                bal = await conn.fetchval(
                    "SELECT balance FROM wallets WHERE user_id = $1", user["user_id"],
                )
                return {
                    "message": "Money sent (idempotent)",
                    "tx_id": existing["tx_id"],
                    "new_balance": str(bal or 0),
                    "amount": str(existing["amount"]),
                    "fee": str(existing["fee"] or 0),
                    "net_to_recipient": str(Decimal(str(existing["amount"])) - Decimal(str(existing["fee"] or 0))),
                    "idempotent": True,
                }

        # Proactive spam detection (before holding the row locks)
        spam_count = await conn.fetchval(
            """SELECT COUNT(*) FROM transactions
               WHERE from_user_id = $1 AND type = 'send'
                 AND created_at > NOW() - ($2 || ' minutes')::interval""",
            user['user_id'], str(SEND_SPAM_WINDOW_MINUTES),
        ) or 0
        if int(spam_count) + 1 >= SEND_SPAM_COUNT:
            # Fire-and-forget alert (does not block the request)
            await trigger_send_spam(user['user_id'], int(spam_count) + 1)

        async with conn.transaction():
            # iter141eight — auto-heal: ensure both wallet rows exist before
            # we lock them. ON CONFLICT keeps existing balances intact.
            await conn.execute(
                "INSERT INTO wallets (user_id, balance, currency) VALUES ($1, 0.00, 'USD') ON CONFLICT (user_id) DO NOTHING",
                user['user_id'],
            )
            await conn.execute(
                "INSERT INTO wallets (user_id, balance, currency) VALUES ($1, 0.00, 'USD') ON CONFLICT (user_id) DO NOTHING",
                req.to_user_id,
            )

            sender_wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
            if not sender_wallet:
                raise HTTPException(status_code=404, detail="Sender wallet not found")
            if sender_wallet['is_locked']:
                raise HTTPException(status_code=403, detail="Your wallet is locked")

            amount = Decimal(str(req.amount))

            # ── Transfer fees (admin-configurable) ──────────────────────────
            # Keys (all in admin_settings):
            #   send_fee_enabled      bool (default false)
            #   send_fee_mode         "percent" | "flat"
            #   send_fee_value        numeric (e.g. "1" = 1% OR 50 XAF flat)
            #   send_fee_min          numeric (absolute floor, XAF)
            #   send_fee_max          numeric (absolute cap, XAF)
            #   send_fee_pro_enabled  bool — when false, PRO users pay 0
            #   send_fee_pro_value    numeric — when enabled, overrides for PRO
            #   send_fee_daily_cap    numeric — kill switch amount/day/user
            fee = Decimal("0.00")
            if await get_bool("send_fee_enabled", False):
                is_pro = bool(sender_wallet.get("is_pro")) or bool(user.get("is_pro"))
                pro_enabled = await get_bool("send_fee_pro_enabled", True)
                if is_pro and not pro_enabled:
                    fee = Decimal("0.00")
                else:
                    mode = (await get_setting("send_fee_mode")) or "percent"
                    raw_val = await get_setting(
                        "send_fee_pro_value" if (is_pro and pro_enabled) else "send_fee_value"
                    )
                    try:
                        val = Decimal(str(raw_val)) if raw_val not in (None, "") else Decimal("0")
                    except Exception:
                        val = Decimal("0")
                    if mode == "percent":
                        fee = (amount * val / Decimal("100")).quantize(Decimal("0.01"))
                    else:
                        fee = val.quantize(Decimal("0.01"))
                    # Min / max clamps
                    try:
                        floor = Decimal(str(await get_setting("send_fee_min") or "0"))
                    except Exception:
                        floor = Decimal("0")
                    try:
                        cap = Decimal(str(await get_setting("send_fee_max") or "0"))
                    except Exception:
                        cap = Decimal("0")
                    if floor > 0 and fee < floor:
                        fee = floor
                    if cap > 0 and fee > cap:
                        fee = cap
                    if fee >= amount:
                        raise HTTPException(status_code=400, detail="Montant inférieur aux frais.")
                # Daily cap check (XAF equivalent simple — uses same currency)
                try:
                    daily_cap = Decimal(str(await get_setting("send_daily_cap_amount") or "0"))
                except Exception:
                    daily_cap = Decimal("0")
                if daily_cap > 0:
                    sent_today = await conn.fetchval(
                        """SELECT COALESCE(SUM(amount),0) FROM transactions
                           WHERE from_user_id = $1 AND type = 'send' AND status = 'completed'
                             AND created_at > NOW() - INTERVAL '24 hours'""",
                        user['user_id'],
                    ) or Decimal("0")
                    if (Decimal(str(sent_today)) + amount) > daily_cap:
                        raise HTTPException(
                            status_code=429,
                            detail=f"Plafond journalier de transferts atteint ({daily_cap}).",
                        )

            total_debit = amount  # sender pays full amount; fee retained by house
            net_to_recipient = amount - fee

            if sender_wallet['balance'] < total_debit:
                raise HTTPException(status_code=400, detail="Insufficient balance")

            receiver_wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", req.to_user_id)
            if not receiver_wallet:
                # iter141eight — should not happen now (we INSERT ON CONFLICT
                # above) but if it does, log loudly so we can audit.
                logger.error("[wallet.send] CRITICAL: wallet missing AFTER auto-heal user=%s",
                             req.to_user_id)
                raise HTTPException(status_code=404, detail="Recipient wallet not found")

            await conn.execute("UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                               total_debit, datetime.now(timezone.utc), user['user_id'])
            await conn.execute("UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                               net_to_recipient, datetime.now(timezone.utc), req.to_user_id)

            tx_id = f"tx_{uuid.uuid4().hex[:16]}"
            # Embed idempotency_key in notes so future retries can find it.
            notes_full = (req.notes or "").strip()
            if req.idempotency_key:
                notes_full = (notes_full + f" [idem:{req.idempotency_key}]").strip()
            # iter237m — Wallets are USD canonical; force currency to wallet's
            # currency so the history doesn't fall back to the column DEFAULT 'XAF'.
            tx_currency = sender_wallet.get('currency') or 'USD'
            await conn.execute("""
                INSERT INTO transactions (tx_id, from_user_id, to_user_id, type, amount, fee, currency, status, notes)
                VALUES ($1, $2, $3, 'send', $4, $5, $6, 'completed', $7)
            """, tx_id, user['user_id'], req.to_user_id, amount, fee, tx_currency, notes_full)

            # House fee book-entry (purely accounting, no wallet — simplifies ledger)
            if fee > 0:
                await conn.execute("""
                    INSERT INTO transactions (tx_id, from_user_id, to_user_id, type, amount, currency, status, notes)
                    VALUES ($1, $2, NULL, 'fee_send', $3, $4, 'completed', $5)
                """, f"feeS_{uuid.uuid4().hex[:14]}", user['user_id'], fee, tx_currency,
                   f"Send fee on {tx_id}")

            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            sender_name = f"{user['first_name']} {user['last_name']}".strip()
            await conn.execute("""
                INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                VALUES ($1, $2, 'money_received', 'Money Received', $3, $4)
            """, notif_id, req.to_user_id, f"{sender_name} sent you {net_to_recipient} {sender_wallet['currency']}",
               f'{{"tx_id": "{tx_id}", "amount": "{net_to_recipient}", "from": "{user["user_id"]}"}}')

            # iter141eight — richer audit row for fraud investigation.
            client_ip = (request.headers.get("cf-connecting-ip")
                         or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                         or (request.client.host if request.client else ""))
            ua = request.headers.get("user-agent", "")[:300]
            import json as _json
            audit_payload = _json.dumps({
                "to": req.to_user_id,
                "amount": str(amount),
                "fee": str(fee),
                "net": str(net_to_recipient),
                "tx_id": tx_id,
                "idempotency_key": req.idempotency_key or None,
                "ip": client_ip,
                "ua": ua,
            })
            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'send_money', 'wallet', $2)
            """, user['user_id'], audit_payload)

            new_balance = await conn.fetchval("SELECT balance FROM wallets WHERE user_id = $1", user['user_id'])
            result = {
                "message": "Money sent",
                "tx_id": tx_id,
                "new_balance": str(new_balance),
                "amount": str(amount),
                "fee": str(fee),
                "net_to_recipient": str(net_to_recipient),
            }
    try:
        await check_and_activate_referral(user['user_id'])
    except Exception as e:
        logger.warning(f"Referral activation skipped: {e}")
    return result


# ──────────────────────────────────────────────────────────────────────────
#  iter141nine — "Demander à recevoir" (Payment Requests)
#  A user creates a request → gets a shareable link + WhatsApp prefilled
#  message + QR. The payer lands on /pay/<id>, signs in if needed, and
#  fulfills via the existing send_money flow. This is a powerful viral
#  acquisition lever combined with the Recruteur reward system.
# ──────────────────────────────────────────────────────────────────────────

class CreatePaymentRequestBody(BaseModel):
    amount: float
    note: str = ""
    expires_in_hours: Optional[int] = 168  # 7 days default


def _build_pay_link(request_id: str, request: Request) -> str:
    """Return the public URL the recipient opens. Prefer the configured
    public origin (FRONTEND_URL / PUBLIC_APP_URL) so the link is shareable
    on WhatsApp / social — falling back to forwarded-proto headers (kube
    ingress) and finally the raw request URL as a last resort."""
    origin = (
        os.environ.get("FRONTEND_URL", "").rstrip("/")
        or os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
    )
    if not origin:
        proto = (request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
                 or request.url.scheme)
        host = (request.headers.get("x-forwarded-host", "").split(",")[0].strip()
                or request.headers.get("host", "")
                or request.url.netloc)
        origin = f"{proto}://{host}"
    return f"{origin}/pay/{request_id}"


def _build_share_link(request_id: str, request: Request) -> str:
    """iter141nineE — return the OG-rich URL used in WhatsApp / iMessage /
    SMS shares. Social scrapers fetch this URL, parse the meta tags, and
    render a preview card (requester name + amount + note). Real humans
    hit the same URL and are meta-refreshed to /pay/<id> in ~50ms."""
    base = _build_pay_link(request_id, request)
    # Replace the SPA path with the OG endpoint while keeping the same origin.
    origin = base.rsplit("/pay/", 1)[0]
    return f"{origin}/api/og/pay/{request_id}"


@router.post("/payment-requests")
@_limiter.limit("20/minute")
async def create_payment_request(
    body: CreatePaymentRequestBody, request: Request
):
    user = await get_current_user(request)
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Le montant doit être positif.")
    if body.amount > 10_000_000:
        raise HTTPException(status_code=400, detail="Montant trop élevé.")
    note = (body.note or "").strip()[:240]
    hours = max(1, min(720, int(body.expires_in_hours or 168)))  # 1h..30d
    request_id = f"pr_{uuid.uuid4().hex[:18]}"

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Make sure requester has a wallet (so currency is known + ready to receive)
        await conn.execute(
            "INSERT INTO wallets (user_id, balance, currency) VALUES ($1, 0.00, 'XAF') ON CONFLICT (user_id) DO NOTHING",
            user["user_id"],
        )
        wallet = await conn.fetchrow(
            "SELECT currency FROM wallets WHERE user_id = $1", user["user_id"],
        )
        currency = (wallet["currency"] if wallet else "XAF") or "XAF"
        await conn.execute(
            """INSERT INTO payment_requests
                (request_id, requester_id, amount, currency, note, status, expires_at)
               VALUES ($1, $2, $3, $4, $5, 'pending', NOW() + ($6 || ' hours')::interval)""",
            request_id, user["user_id"], Decimal(str(body.amount)), currency,
            note, str(hours),
        )

    pay_url = _build_pay_link(request_id, request)
    # iter141nineE — share via the /api/og/pay/<id> URL so WhatsApp /
    # iMessage / SMS scrapers fetch a rich preview (requester name +
    # amount + note) before the recipient even taps. Real users hit
    # the OG endpoint and meta-refresh redirects them to /pay/<id>
    # within ~50ms.
    share_url = _build_share_link(request_id, request)
    sender_name = (
        f"{user.get('first_name','') or ''} {user.get('last_name','') or ''}".strip()
        or user.get("username") or "un utilisateur JAPAP"
    )
    wa_text = (
        f"Salut 👋, je te demande {body.amount} {currency}"
        f"{' pour ' + note if note else ''}. "
        f"Paie-moi en 1 clic sur JAPAP : {share_url}"
    )
    return {
        "request_id": request_id,
        "amount": str(Decimal(str(body.amount))),
        "currency": currency,
        "note": note,
        "status": "pending",
        "expires_in_hours": hours,
        "pay_url": pay_url,
        "share_url": share_url,
        "qr_url": f"/api/wallet/payment-requests/{request_id}/qr.png",
        "share_text": wa_text,
        "whatsapp_url": f"https://wa.me/?text={_url_quote(wa_text)}",
        "requester": {
            "user_id": user["user_id"],
            "name": sender_name,
            "avatar": user.get("avatar") or "",
        },
    }


def _url_quote(s: str) -> str:
    """Tiny helper — avoid pulling urllib at module top for clarity."""
    from urllib.parse import quote
    return quote(s, safe="")


@router.get("/payment-requests/{request_id}")
async def get_payment_request(request_id: str, request: Request):
    """PUBLIC preview — no auth needed. Returns just enough info to render
    the /pay/<id> landing page (requester name+avatar, amount, note, status).
    Returning sensitive data here would be a privacy leak."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT pr.request_id, pr.requester_id, pr.amount, pr.currency,
                      pr.note, pr.status, pr.expires_at, pr.fulfilled_tx_id,
                      pr.fulfilled_at, pr.created_at,
                      u.first_name, u.last_name, u.username, u.avatar
               FROM payment_requests pr
               JOIN users u ON u.user_id = pr.requester_id
               WHERE pr.request_id = $1""",
            request_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Demande introuvable.")

    status = row["status"]
    if (status == "pending" and row["expires_at"]
            and row["expires_at"] < datetime.now(timezone.utc)):
        status = "expired"
    name = (
        f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
        or row["username"] or "JAPAP user"
    )
    return {
        "request_id": row["request_id"],
        "amount": str(row["amount"]),
        "currency": row["currency"],
        "note": row["note"] or "",
        "status": status,
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "fulfilled_tx_id": row["fulfilled_tx_id"],
        "fulfilled_at": row["fulfilled_at"].isoformat() if row["fulfilled_at"] else None,
        "created_at": row["created_at"].isoformat(),
        "requester": {
            "user_id": row["requester_id"],
            "name": name,
            "avatar": row["avatar"] or "",
        },
    }


@router.get("/payment-requests/{request_id}/qr.png")
async def payment_request_qr(request_id: str, request: Request):
    """Render a 512×512 PNG QR pointing at the public /pay/<id> URL."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM payment_requests WHERE request_id = $1", request_id,
        )
    if not exists:
        raise HTTPException(status_code=404, detail="Demande introuvable.")
    pay_url = _build_pay_link(request_id, request)
    try:
        import io
        import qrcode
        from fastapi import Response
        img = qrcode.make(pay_url, box_size=12, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception as e:
        logger.warning("payment-request QR render failed: %s", e)
        raise HTTPException(status_code=500, detail="QR indisponible")


@router.post("/payment-requests/{request_id}/fulfill")
@_limiter.limit("5/minute")
async def fulfill_payment_request(request_id: str, request: Request):
    """Pay an outstanding payment request. Reuses the same locks/fees/idempotency
    semantics as `/wallet/send` — we just resolve the recipient + amount from
    the stored request. The payer must be authenticated."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payment_requests WHERE request_id = $1 FOR UPDATE",
            request_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Demande introuvable.")
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail=f"Demande déjà {row['status']}.")
        if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc):
            await conn.execute(
                "UPDATE payment_requests SET status = 'expired' WHERE request_id = $1",
                request_id,
            )
            raise HTTPException(status_code=410, detail="Demande expirée.")
        if row["requester_id"] == user["user_id"]:
            raise HTTPException(status_code=400, detail="Tu ne peux pas payer ta propre demande.")

    # Reuse existing send_money logic — we forge a SendMoneyRequest with the
    # request_id used as the idempotency key (so even if the user double-taps
    # "Pay now", the same fulfillment is replayed safely).
    send_req = SendMoneyRequest(
        to_user_id=row["requester_id"],
        amount=float(row["amount"]),
        notes=(f"Demande #{request_id}"
               + (f" — {row['note']}" if row["note"] else "")),
        idempotency_key=f"pay_req_{request_id}",
    )
    result = await send_money(send_req, request)

    # Mark the request as paid and link the tx_id (only if not already set
    # by an idempotent replay — UPDATE is idempotent here either way).
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE payment_requests
                  SET status = 'paid',
                      fulfilled_tx_id = $1,
                      fulfilled_by = $2,
                      fulfilled_at = COALESCE(fulfilled_at, NOW())
                WHERE request_id = $3 AND status IN ('pending', 'paid')""",
            result.get("tx_id"), user["user_id"], request_id,
        )
    result["payment_request_id"] = request_id

    # Fire-and-forget OneSignal push to the requester so they're nudged
    # back into the app the moment their request gets paid — closes the
    # virality → engagement loop ("Bob a payé ta demande de 5 000 XAF !").
    try:
        payer_name = (
            f"{user.get('first_name','') or ''} {user.get('last_name','') or ''}".strip()
            or user.get("username") or "Quelqu'un"
        )
        amount_str = result.get("net_to_recipient") or result.get("amount") or str(row["amount"])
        ccy = row["currency"]
        await send_push_to_user(
            row["requester_id"],
            {
                "title": f"{payer_name} a payé ta demande 💸",
                "body": f"Tu as reçu {amount_str} {ccy} sur ton wallet JAPAP.",
                "url": "/wallet",
                "tag": f"pay-req:{request_id}",
                "type": "money",
                "extra": {
                    "request_id": request_id,
                    "tx_id": result.get("tx_id"),
                    "amount": str(amount_str),
                    "currency": ccy,
                },
            },
        )
    except Exception as e:
        logger.warning("payment-request push notification skipped: %s", e)

    return result


@router.get("/payment-requests")
async def list_my_payment_requests(
    request: Request,
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Return the calling user's last N requests (so they can re-share or
    cancel from the wallet UI). Filter by status if provided."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """SELECT request_id, amount, currency, note, status,
                          fulfilled_tx_id, fulfilled_at, expires_at, created_at
                   FROM payment_requests
                   WHERE requester_id = $1 AND status = $2
                   ORDER BY created_at DESC LIMIT $3""",
                user["user_id"], status, limit,
            )
        else:
            rows = await conn.fetch(
                """SELECT request_id, amount, currency, note, status,
                          fulfilled_tx_id, fulfilled_at, expires_at, created_at
                   FROM payment_requests
                   WHERE requester_id = $1
                   ORDER BY created_at DESC LIMIT $2""",
                user["user_id"], limit,
            )
    return [
        {
            "request_id": r["request_id"],
            "amount": str(r["amount"]),
            "currency": r["currency"],
            "note": r["note"] or "",
            "status": r["status"],
            "fulfilled_tx_id": r["fulfilled_tx_id"],
            "fulfilled_at": r["fulfilled_at"].isoformat() if r["fulfilled_at"] else None,
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.delete("/payment-requests/{request_id}")
async def cancel_payment_request(request_id: str, request: Request):
    """Allow the requester to cancel a pending request (e.g. typo'd amount)."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT requester_id, status FROM payment_requests WHERE request_id = $1",
            request_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Demande introuvable.")
        if row["requester_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="Tu ne peux annuler que tes propres demandes.")
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail=f"Demande déjà {row['status']}.")
        await conn.execute(
            "UPDATE payment_requests SET status = 'cancelled' WHERE request_id = $1",
            request_id,
        )
    return {"status": "cancelled", "request_id": request_id}


@router.post("/deposit")
async def deposit(req: DepositRequest, request: Request):
    """Create a deposit request via one of the 3 supported methods:
    - usdt_trc20 / usdt_bep20 : awaits manual confirmation by admin (tx hash in reference)
    - hubtel_card : card payment through Hubtel Checkout (external API, MOCKED here)

    Deposits are designed to be AUTOMATIC once the corresponding webhook is
    wired (Hubtel callback, NowPayments IPN). Until then, they stay 'pending'
    and the admin can approve them manually.
    """
    user = await get_current_user(request)
    # Global kill switch first
    if not await get_bool("deposits_enabled", True):
        msg = await get_setting("deposit_disabled_message") or "Dépôts désactivés."
        raise HTTPException(status_code=503, detail=msg)
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if req.method not in DEPOSIT_METHODS:
        raise HTTPException(status_code=400, detail=f"Méthode invalide. Disponibles: {', '.join(DEPOSIT_METHODS)}")
    if not await get_bool(f"deposit_{req.method}_enabled", True):
        raise HTTPException(status_code=503, detail=f"Méthode {req.method} temporairement désactivée.")
    min_dep = float(await get_setting("deposit_min_amount_usd") or 0)
    if min_dep and float(req.amount) < min_dep:
        raise HTTPException(status_code=400, detail=f"Minimum dépôt : ${min_dep}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        wallet = await conn.fetchrow("SELECT currency FROM wallets WHERE user_id = $1", user['user_id'])
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet introuvable")

        amount_usd = Decimal(str(req.amount))
        tx_id = f"dep_{uuid.uuid4().hex[:16]}"

        # For all methods: create a PENDING deposit record. The balance is credited
        # only after admin approval (crypto) or callback confirmation (Hubtel).
        # iter158 — ALWAYS persist `amount_usd` (canonical) and `provider`.
        provider_tag = "hubtel" if req.method in ("card", "mobile_money") else (
            "nowpayments" if req.method.startswith(("nowpayments_", "usdt_"))
            else "internal"
        )
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO transactions (tx_id, to_user_id, type, amount, currency,
                                          status, notes, reference,
                                          amount_usd, provider, display_currency, display_amount)
                VALUES ($1, $2, 'deposit', $3, 'USD', 'pending', $4, $5,
                        $3, $6, 'USD', $3)
            """, tx_id, user['user_id'], amount_usd,
               f"[{DEPOSIT_METHODS[req.method]['label']}] {req.notes}", req.reference[:255],
               provider_tag)
            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'deposit_requested', 'wallet', $2)
            """, user['user_id'],
               f'{{"amount_usd": "{amount_usd}", "method": "{req.method}", "tx_id": "{tx_id}"}}')

    # Ops notification — fire-and-forget, never blocks the request.
    notify_deposit(
        user_id=user['user_id'],
        user_email=user.get('email', ''),
        user_name=user.get('name') or user.get('username') or user.get('email', ''),
        amount=float(amount_usd),
        method=req.method,
        tx_id=tx_id,
        status="pending",
    )

    # --- Mocked integration responses ---------------------------------------
    if req.method in ("usdt_trc20", "usdt_bep20"):
        chain = DEPOSIT_METHODS[req.method]['chain']
        # Admin-configurable deposit addresses (same address per chain).
        address = await get_setting(f"deposit_address_{req.method}") or ""
        return {
            "tx_id": tx_id, "status": "pending",
            "method": req.method, "chain": chain,
            "amount_usd": str(amount_usd),
            "address": address or "CONFIGURE_DEPOSIT_ADDRESS",
            "instruction": (
                f"Envoyez exactement {amount_usd} USDT à l'adresse ci-dessus sur le réseau {chain}. "
                "Collez le hash de transaction dans 'reference' puis attendez la confirmation admin. "
                "Dépôt en cours : si après 10 minutes votre compte n'est pas crédité, "
                "contactez le support depot@japapmessenger.com avec votre code hash."
            ),
        }
    if req.method == "hubtel_card":
        # Real Hubtel Online Checkout integration (iter42).
        # If credentials are not yet configured by the admin, falls back to a
        # dev stub so local development remains unblocked.
        from services.hubtel_service import (
            initiate_checkout, HubtelConfigError, HubtelAPIError,
        )
        public_base = (
            os.environ.get("PUBLIC_BASE_URL")
            or os.environ.get("REACT_APP_BACKEND_URL")
            or str(request.base_url).rstrip("/")
        )
        # iter116 — distinct frontend URL for user-facing return/cancel.
        public_frontend = (
            os.environ.get("FRONTEND_URL")
            or os.environ.get("PUBLIC_FRONTEND_URL")
            or public_base
        )
        try:
            ck = await initiate_checkout(
                tx_id=tx_id,
                amount=float(amount_usd),
                description=f"JAPAP wallet deposit {amount_usd} USD",
                public_base_url=public_base,
                public_frontend_url=public_frontend,
                # iter191 — Pre-fill payee info so Hubtel's checkout page
                # auto-selects the MoMo channel and triggers the prompt
                # without the user retyping their phone number.
                payee_name=user.get("name") or user.get("username") or "",
                payee_email=user.get("email") or "",
                payee_phone=user.get("phone") or "",
            )
            # Store the Hubtel transaction id + conversion audit on our row
            # (iter158 — provider_currency/amount & exchange_rate enable
            # later reconciliation). Failures here must never block the
            # checkout flow — the webhook will complete the audit anyway.
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE transactions SET
                          reference = $1,
                          provider = 'hubtel',
                          amount_usd = $2,
                          provider_currency = 'GHS',
                          provider_amount = $3,
                          exchange_rate = $4
                        WHERE tx_id = $5""",
                    ck.get("checkout_tx_id") or "",
                    float(amount_usd),
                    ck.get("provider_amount") or 0,
                    ck.get("exchange_rate") or 0,
                    tx_id,
                )
            return {
                "tx_id": tx_id, "status": "pending",
                "method": req.method,
                "amount_usd": str(amount_usd),
                "provider_currency": ck.get("provider_currency", "GHS"),
                "provider_amount": ck.get("provider_amount", 0),
                "exchange_rate": ck.get("exchange_rate", 0),
                "checkout_url": ck["checkout_url"],
                "checkout_direct_url": ck.get("checkout_direct_url", ""),
                "provider_tx_id": ck.get("checkout_tx_id", ""),
                "instruction": (
                    "Dépôt en cours. Si après 10 minutes votre compte n'est pas crédité, "
                    "veuillez contacter le support par mail : depot@japapmessenger.com "
                    "en joignant votre capture d'écran de paiement Hubtel."
                ),
            }
        except HubtelConfigError as e:
            logger.warning(f"Hubtel not configured, using stub: {e}")
            return {
                "tx_id": tx_id, "status": "pending",
                "method": req.method,
                "amount_usd": str(amount_usd),
                "checkout_url": f"https://hubtel-mock.local/checkout/{tx_id}",
                "mocked": True,
                "instruction": "⚠️ Hubtel n'est pas encore configuré. Configurez vos clés dans /admin → Paiements.",
            }
        except HubtelAPIError as e:
            logger.error(f"Hubtel API error for {tx_id}: {e}")
            raise HTTPException(status_code=502, detail=f"Erreur Hubtel : {e}")
    if req.method.startswith("nowpayments_"):
        # NowPayments crypto deposit — we use POST /payment (not /invoice)
        # so we get a real `pay_address` + `pay_amount` we can render as QR
        # inside our own UI (no redirect to a hosted page).
        from services.nowpayments_service import (
            create_payment, NowPaymentsConfigError, NowPaymentsAPIError,
        )
        pay_currency = "usdttrc20" if req.method == "nowpayments_usdttrc20" else "usdtbsc"
        public_base = (
            os.environ.get("PUBLIC_BASE_URL")
            or os.environ.get("REACT_APP_BACKEND_URL")
            or str(request.base_url).rstrip("/")
        )
        try:
            pay = await create_payment(
                tx_id=tx_id,
                amount_usd=float(amount_usd),
                pay_currency=pay_currency,
                public_base_url=public_base,
            )
            if pay.get("payment_id"):
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE transactions SET reference = $1 WHERE tx_id = $2",
                        pay["payment_id"], tx_id,
                    )
            return {
                "tx_id": tx_id,
                "status": "pending",
                "method": req.method,
                "amount_usd": str(amount_usd),
                # NowPayments full payload (frontend renders QR + address + amount)
                "payment_id":     pay["payment_id"],
                "payment_status": pay["payment_status"],
                "pay_address":    pay["pay_address"],
                "pay_amount":     str(pay["pay_amount"]) if pay["pay_amount"] is not None else "",
                "pay_currency":   pay["pay_currency"],
                "price_amount":   str(pay["price_amount"]) if pay["price_amount"] is not None else str(amount_usd),
                "price_currency": pay["price_currency"],
                "expiration_estimate_date": pay["expiration_estimate_date"],
                "provider_tx_id": pay["payment_id"],
                "instruction": (
                    f"Envoyez exactement {pay['pay_amount']} {pay['pay_currency'].upper()} "
                    f"à l'adresse ci-dessous. Le compte sera crédité automatiquement après "
                    f"confirmation réseau (1-15 min selon la chaîne). "
                    f"En cas de retard >15 min, contactez depot@japapmessenger.com."
                ),
            }
        except NowPaymentsConfigError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except NowPaymentsAPIError as e:
            logger.error(f"NowPayments API error for {tx_id}: {e}")
            # Surface NP minimum-amount errors with a friendly message — the
            # default /v1/payment minimum is ~$10 for USDT TRC20/BEP20.
            err = str(e)
            if "minimal" in err.lower() or "minimum" in err.lower():
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Montant trop faible pour un dépôt USDT. "
                        "NowPayments exige un minimum d'environ 10 USD pour ce token. "
                        "Veuillez réessayer avec un montant plus élevé."
                    ),
                )
            raise HTTPException(status_code=502, detail=f"Erreur NowPayments : {err}")
    raise HTTPException(status_code=400, detail="Unreachable")


@router.post("/withdraw")
async def withdraw(req: WithdrawRequest, request: Request):
    """USDT TRC20 or BEP20 withdrawals only. Fee is applied according to admin
    settings (`withdraw_fee_mode` = 'percent' | 'flat', `withdraw_fee_value`)."""
    user = await get_current_user(request)
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if req.method not in WITHDRAW_METHODS:
        raise HTTPException(status_code=400, detail=f"Méthode invalide. Disponibles: {', '.join(WITHDRAW_METHODS)}")
    if not await get_bool(f"withdraw_{req.method}_enabled", True):
        raise HTTPException(status_code=503, detail=f"Méthode {req.method} temporairement désactivée.")
    if not req.address or len(req.address.strip()) < 10:
        raise HTTPException(status_code=400, detail="Adresse USDT invalide.")

    # --- Admin-gated checks -------------------------------------------------
    if not await get_bool("withdraw_enabled", True):
        msg = await get_setting("withdraw_disabled_message") or "Les retraits sont temporairement désactivés."
        raise HTTPException(status_code=503, detail=msg)
    manual_enabled = await get_bool("manual_withdraw_enabled", True)
    auto_enabled = await get_bool("auto_withdraw_enabled", False)
    if not manual_enabled and not auto_enabled:
        raise HTTPException(status_code=503, detail="Aucun mode de retrait n'est actif. Contactez le support.")
    # Auto mode is preferred when enabled, but falls back to manual if SDK not ready.
    processing_mode = "auto" if auto_enabled else "manual"
    min_amount = await get_float("withdraw_min_amount_usd", 0.0)
    if min_amount > 0 and float(req.amount) < min_amount:
        raise HTTPException(status_code=400, detail=f"Minimum retrait: ${min_amount}")

    # --- Compute fee dynamically (per-user Pro plan override if any) --------
    pool = await get_pool()
    async with pool.acquire() as conn:
        fee_resolved = await _resolve_withdraw_fee(conn, user['user_id'], method=req.method)
    fee_mode = fee_resolved["mode"]
    fee_value = Decimal(str(fee_resolved["value"]))
    amount = Decimal(str(req.amount))
    if fee_mode == "flat":
        fee = fee_value
    else:
        fee = (amount * fee_value / Decimal("100")).quantize(Decimal("0.0001"))
    fee = max(Decimal("0"), fee)
    if fee >= amount:
        raise HTTPException(status_code=400, detail="Montant inférieur aux frais.")
    net_amount = amount - fee
    # ------------------------------------------------------------------------

    async with pool.acquire() as conn:
        if await get_bool("kyc_required_for_withdraw", True):
            if not await is_user_kyc_approved(conn, user['user_id']):
                # Alert admins of the attempt, fire-and-forget
                await trigger_withdraw_without_kyc(user['user_id'], float(amount))
                raise HTTPException(
                    status_code=403,
                    detail="KYC_REQUIRED: Complétez et faites valider votre KYC avant de retirer."
                )
        async with conn.transaction():
            wallet = await conn.fetchrow("SELECT * FROM wallets WHERE user_id = $1 FOR UPDATE", user['user_id'])
            if not wallet:
                raise HTTPException(status_code=404, detail="Wallet introuvable")
            if wallet['is_locked']:
                raise HTTPException(status_code=403, detail="Wallet verrouillé")
            if wallet['balance'] < amount:
                raise HTTPException(status_code=400, detail="Solde insuffisant")

            tx_id = f"wdr_{uuid.uuid4().hex[:16]}"
            # In 'auto' mode status starts at 'processing' (queued for SDK dispatch).
            # In 'manual' mode status is 'pending' (awaits admin approval).
            initial_status = "processing" if processing_mode == "auto" else "pending"
            await conn.execute(
                "UPDATE wallets SET balance = balance - $1, updated_at = $2 WHERE user_id = $3",
                amount, datetime.now(timezone.utc), user['user_id'])
            await conn.execute("""
                INSERT INTO transactions (tx_id, from_user_id, type, amount, fee, currency, status, notes, reference)
                VALUES ($1, $2, 'withdrawal', $3, $4, 'USD', $5, $6, $7)
            """, tx_id, user['user_id'], amount, fee, initial_status,
               f"[{WITHDRAW_METHODS[req.method]['label']}] mode={processing_mode} addr={req.address[:60]} notes={req.notes}",
               req.address[:255])
            await conn.execute("""
                INSERT INTO audit_logs (user_id, action, resource, details)
                VALUES ($1, 'withdrawal', 'wallet', $2)
            """, user['user_id'],
               f'{{"amount": "{amount}", "fee": "{fee}", "net": "{net_amount}", "method": "{req.method}", "mode": "{processing_mode}", "address": "{req.address[:32]}", "tx_id": "{tx_id}"}}')
            new_balance = await conn.fetchval(
                "SELECT balance FROM wallets WHERE user_id = $1", user['user_id'])
    # Outside the DB lock: alert admins on large withdrawals (>$500)
    try:
        await trigger_large_withdraw_alert(user['user_id'], float(amount), req.method)
    except Exception as e:
        logger.warning("large_withdraw alert skipped: %s", e)
    # Ops inbox notification (fire-and-forget)
    notify_withdraw(
        user_id=user['user_id'],
        user_email=user.get('email', ''),
        user_name=user.get('name') or user.get('username') or user.get('email', ''),
        amount=float(amount), fee=float(fee), net=float(net_amount),
        method=req.method, address=req.address,
        tx_id=tx_id, status=initial_status, processing_mode=processing_mode,
    )
    instruction = (
        "Retrait en cours de traitement automatique. Vous recevrez une notification à la finalisation."
        if processing_mode == "auto"
        else "Votre retrait est en attente de validation par l'admin. Les USDT nets seront envoyés à l'adresse fournie."
    )
    return {
        "message": "Demande de retrait enregistrée",
        "tx_id": tx_id, "new_balance": str(new_balance),
        "amount_usd": str(amount), "fee_usd": str(fee), "net_usd": str(net_amount),
        "fee_mode": fee_mode, "fee_value": str(fee_value),
        "fee_source": fee_resolved["source"],     # "plan" | "default"
        "plan_id": fee_resolved["plan_id"],
        "chain": WITHDRAW_METHODS[req.method]['chain'],
        "address": req.address,
        "processing_mode": processing_mode,       # "auto" | "manual"
        "status": initial_status,                 # "processing" | "pending"
        "instruction": instruction,
    }


@router.get("/deposit/{tx_id}/status")
async def deposit_status(tx_id: str, request: Request):
    """Polling endpoint used by the deposit modal to refresh the live
    NowPayments status without waiting for the IPN webhook. Returns
    {tx_status, payment_status, actually_paid, is_paid}.
    """
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            """SELECT tx_id, type, status, amount, currency, reference, notes, to_user_id
                 FROM transactions WHERE tx_id = $1""",
            tx_id,
        )
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction introuvable")
    if tx["to_user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Non autorisé")
    out = {
        "tx_id": tx_id,
        "tx_status": tx["status"],
        "amount": str(tx["amount"]),
        "currency": tx["currency"],
        "is_paid": tx["status"] == "completed",
        "payment_status": tx["status"],
        "actually_paid": None,
    }
    # iter156 — Probe the live provider status for BOTH providers (not
    # just NowPayments). This makes the polling modal on WalletPage
    # converge to "completed" within a few seconds of the real payment,
    # even if the webhook is slow or blocked.
    notes_lower = (tx["notes"] or "").lower()
    payment_id = tx["reference"] or ""
    if tx["status"] == "pending" and payment_id:
        if "nowpayments" in notes_lower:
            try:
                from services.nowpayments_service import verify_payment_status
                v = await verify_payment_status(payment_id)
                if v.get("ok"):
                    out["payment_status"] = v["status"]
                    out["actually_paid"] = v["actually_paid"]
                    out["is_paid"] = v["is_paid"]
            except Exception as _e:
                logger.warning("deposit_status NP probe failed for %s: %s", tx_id, _e)
        elif "hubtel" in notes_lower:
            try:
                from services.hubtel_service import verify_transaction_status
                v = await verify_transaction_status(checkout_id=payment_id)
                if v.get("ok"):
                    out["payment_status"] = v.get("status") or "Unknown"
                    out["is_paid"] = bool(v.get("is_paid"))
            except Exception as _e:
                logger.warning("deposit_status Hubtel probe failed for %s: %s", tx_id, _e)
    return out


# iter119 — Force-verify (P0 paiements) ────────────────────────────────────
# Triggered by the user-facing "J'ai payé" button. Idempotent: re-runs the
# authoritative verify_payment_status / verify_transaction_status flow and
# credits the wallet immediately on confirmation. Bypasses the webhook.
class SubmitDepositHashRequest(BaseModel):
    tx_hash: str


# iter237ab — Late hash submission for manual USDT deposits.
# After the user initiates a `usdt_*` deposit, they leave the page,
# send the funds from their crypto wallet and need a way to come back
# and paste the on-chain tx_hash so the admin/auto-verifier can credit
# their wallet. The `reference` column stores the hash (manual flow).
@router.post("/deposit/{tx_id}/verify-preview")
async def verify_deposit_hash_preview(tx_id: str, req: SubmitDepositHashRequest, request: Request):
    """iter237ad — Read-only on-chain probe used while the user is typing
    their hash. Same verification logic as `/hash` but NEVER touches the
    DB (no UPDATE on transactions, no credit on wallets). The frontend
    polls this endpoint with a debounce so the user sees a real-time
    "🔍 Recherche on-chain → ✅ Transaction trouvée X USDT confirmés"
    indicator before clicking "Confirmer". Magic UX without WebSockets.

    Returns: {ready: bool, verification: {verified, status, reason, ...}}
    `ready=true` means the hash has been validated AND the user can hit
    the confirm button to receive an instant credit.
    """
    user = await get_current_user(request)
    tx_hash = (req.tx_hash or "").strip()
    # Permissive lower-bound: probe as soon as the user pastes something
    # that looks like a hash (>= 32 chars). Lighter than the strict 16
    # guard on the write endpoint — keeps the polling silent on partial
    # input.
    if len(tx_hash) < 32 or len(tx_hash) > 200:
        return {"ready": False,
                "verification": {"verified": False, "status": "too_short",
                                 "reason": "Hash incomplet."}}

    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT to_user_id, type, status, amount, amount_usd, notes "
            "FROM transactions WHERE tx_id = $1",
            tx_id,
        )
    if not tx or tx["to_user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Dépôt introuvable.")
    if tx["type"] != "deposit" or tx["status"] != "pending":
        return {"ready": False,
                "verification": {"verified": False, "status": "not_pending",
                                 "reason": "Ce dépôt n'est plus en attente."}}
    notes = tx["notes"] or ""
    if not (("USDT" in notes.upper()) or ("[USDT" in notes)):
        return {"ready": False,
                "verification": {"verified": False, "status": "not_usdt",
                                 "reason": "Pas un dépôt USDT manuel."}}

    from services.usdt_onchain_verify import (
        verify_usdt_deposit, detect_network_from_notes,
    )
    network = detect_network_from_notes(notes)
    expected_amount = Decimal(str(tx["amount_usd"] or tx["amount"] or 0))
    if not network or expected_amount <= 0:
        return {"ready": False,
                "verification": {"verified": False, "status": "skipped",
                                 "reason": "Réseau ou montant indéterminé."}}
    onchain = await verify_usdt_deposit(network, tx_hash, expected_amount)
    return {"ready": bool(onchain.get("verified")), "verification": onchain}


@router.patch("/deposit/{tx_id}/hash")
async def submit_deposit_hash(tx_id: str, req: SubmitDepositHashRequest, request: Request):
    """Attach (or update) the blockchain tx_hash on a pending USDT deposit.

    Rules:
      • Deposit must belong to the current user.
      • Deposit must be `pending` and method must be `usdt_*`.
      • The hash is stored in `transactions.reference` (existing schema).
      • Notifies ops so the admin sees the freshly attached hash.
      • Idempotent: same hash twice → 200, no-op.
    """
    user = await get_current_user(request)
    tx_hash = (req.tx_hash or "").strip()
    # Loose validation: blockchain tx hashes are typically 64-66 chars
    # (TRON: 64 hex; BSC/EVM: 0x + 64 hex). We accept 32-100 chars to be
    # forgiving on edge cases (some explorers prefix differently).
    if len(tx_hash) < 16 or len(tx_hash) > 200:
        raise HTTPException(status_code=400, detail="Hash de transaction invalide (16-200 caractères).")

    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT tx_id, to_user_id, type, status, amount, amount_usd, reference, notes "
            "FROM transactions WHERE tx_id = $1",
            tx_id,
        )
        if not tx or tx["to_user_id"] != user["user_id"]:
            raise HTTPException(status_code=404, detail="Dépôt introuvable.")
        if tx["type"] != "deposit":
            raise HTTPException(status_code=400, detail="Cette transaction n'est pas un dépôt.")
        if tx["status"] != "pending":
            raise HTTPException(status_code=400, detail="Ce dépôt n'est plus en attente.")
        # Only manual USDT deposits use `reference` as the on-chain hash.
        notes = tx["notes"] or ""
        is_usdt_manual = ("USDT" in notes.upper()) or ("[USDT" in notes)
        if not is_usdt_manual:
            raise HTTPException(status_code=400, detail="Hash applicable uniquement aux dépôts USDT manuels.")

        await conn.execute(
            "UPDATE transactions SET reference = $1 WHERE tx_id = $2",
            tx_hash[:255], tx_id,
        )
        await conn.execute(
            """INSERT INTO audit_logs (user_id, action, resource, details)
                 VALUES ($1, 'deposit_hash_submitted', 'wallet', $2)""",
            user["user_id"], f'{{"tx_id": "{tx_id}", "hash_len": {len(tx_hash)}}}',
        )

    # iter237ac — On-chain auto-verification. We run this OUTSIDE the
    # connection block above (httpx call could be slow) and acquire a
    # fresh connection only if verification succeeds. Failure modes (
    # network down / not_found / wrong_recipient / amount_too_low) leave
    # the deposit `pending` — the admin will continue handling it via the
    # existing manual review flow. Best-effort, never blocks the user.
    from services.usdt_onchain_verify import (
        verify_usdt_deposit, detect_network_from_notes,
    )
    network = detect_network_from_notes(notes)
    expected_amount = Decimal(str(tx["amount_usd"] or tx["amount"] or 0))
    onchain = {"verified": False, "status": "skipped"}
    if network and expected_amount > 0:
        onchain = await verify_usdt_deposit(network, tx_hash, expected_amount)
        logger.info("[deposit-onchain] tx=%s network=%s verified=%s status=%s",
                    tx_id, network, onchain.get("verified"), onchain.get("status"))

    credited = False
    if onchain.get("verified"):
        # Verified → credit atomically. Use a fresh transaction so the
        # UPDATE+INSERT pair stays consistent. If a concurrent request
        # already credited (e.g. admin clicked "Approve" at the same time),
        # the status check before the UPDATE makes us a no-op.
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status, amount, to_user_id FROM transactions "
                    "WHERE tx_id = $1 FOR UPDATE",
                    tx_id,
                )
                if row and row["status"] == "pending":
                    await conn.execute(
                        "UPDATE wallets SET balance = balance + $1, updated_at = $2 "
                        "WHERE user_id = $3",
                        row["amount"], datetime.now(timezone.utc), row["to_user_id"],
                    )
                    await conn.execute(
                        "UPDATE transactions SET status = 'completed' WHERE tx_id = $1",
                        tx_id,
                    )
                    notif_id = f"notif_{uuid.uuid4().hex[:12]}"
                    await conn.execute(
                        """INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                           VALUES ($1, $2, 'deposit_completed', 'Dépôt confirmé', $3, $4)""",
                        notif_id, row["to_user_id"],
                        f"Votre dépôt de {row['amount']} USD a été crédité (vérification on-chain).",
                        f'{{"tx_id": "{tx_id}", "provider": "usdt_onchain", '
                        f'"network": "{network}", "received_amount": "{onchain.get("received_amount", "")}"}}',
                    )
                    await conn.execute(
                        """INSERT INTO audit_logs (user_id, action, resource, details)
                           VALUES ($1, 'deposit_auto_credited', 'wallet', $2)""",
                        row["to_user_id"],
                        f'{{"tx_id": "{tx_id}", "network": "{network}", '
                        f'"received_amount": "{onchain.get("received_amount", "")}"}}',
                    )
                    credited = True

    # Best-effort ops notification — the admin now has the hash needed
    # to verify on-chain (or the auto-verification already credited).
    try:
        notify_deposit(
            user_id=user["user_id"],
            user_email=user.get("email", ""),
            user_name=user.get("name") or user.get("username") or user.get("email", ""),
            amount=float(tx["amount_usd"] or tx["amount"] or 0),
            method="usdt_manual_hash_submitted" if not credited else "usdt_auto_verified",
            tx_id=tx_id,
            status="completed" if credited else "pending",
        )
    except Exception:  # noqa: BLE001
        pass  # never block the user

    if credited:
        return {
            "success": True, "tx_id": tx_id,
            "credited": True,
            "status": "completed",
            "verification": onchain,
            "message": "Dépôt vérifié on-chain et crédité instantanément ! ⚡",
        }
    return {
        "success": True, "tx_id": tx_id,
        "credited": False,
        "status": "pending",
        "verification": onchain,
        "message": "Hash enregistré. Vérification en cours.",
    }


@router.post("/deposit/{tx_id}/force-verify")
async def deposit_force_verify(tx_id: str, request: Request):
    """Force a verify-now on the provider API for the given deposit.

    Returns:
      • {credited: true, status: "completed"} when the provider confirms
        the payment AND we just credited the wallet.
      • {credited: false, status: <provider status>, ...} otherwise.

    User-rate-limited: max 1 call per 10s per (user, tx_id) to protect
    the provider APIs (NowPayments + Hubtel charge per call).
    """
    user = await get_current_user(request)
    pool = await get_pool()
    from services.payment_health import (
        measure_verify, mark_retry_resolved, schedule_verify_retry,
    )

    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            """SELECT tx_id, type, status, amount, reference, notes, to_user_id,
                      created_at
                 FROM transactions WHERE tx_id = $1 AND type='deposit'""",
            tx_id,
        )
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction introuvable")
    if tx["to_user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="Non autorisé")
    if tx["status"] == "completed":
        return {"credited": True, "status": "completed",
                "tx_id": tx_id, "already": True}

    # Best-effort: detect provider from notes (set at /deposit creation).
    notes_lower = (tx["notes"] or "").lower()
    if "nowpayments" in notes_lower:
        provider, ref = "nowpayments", tx["reference"] or ""
    elif "hubtel" in notes_lower:
        provider, ref = "hubtel", tx["reference"] or ""
    else:
        raise HTTPException(status_code=400,
                            detail="Provider inconnu pour cette transaction.")

    if not ref:
        raise HTTPException(
            status_code=400,
            detail="Aucune référence provider — impossible de vérifier.")

    # Verify
    try:
        if provider == "nowpayments":
            from services.nowpayments_service import (
                verify_payment_status, NowPaymentsConfigError,
            )
            try:
                async with measure_verify("nowpayments", tx_id) as _m:
                    v = await verify_payment_status(ref)
                    _m["ok"] = bool(v.get("ok"))
                    _m["is_paid"] = bool(v.get("is_paid"))
            except NowPaymentsConfigError as e:
                raise HTTPException(status_code=503, detail=str(e))
        else:  # hubtel
            from services.hubtel_service import (
                verify_transaction_status, HubtelConfigError,
            )
            try:
                async with measure_verify("hubtel", tx_id) as _m:
                    v = await verify_transaction_status(
                        client_reference=tx_id, checkout_id=ref)
                    _m["ok"] = bool(v.get("ok"))
                    _m["is_paid"] = bool(v.get("is_paid"))
            except HubtelConfigError as e:
                raise HTTPException(status_code=503, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        # API error → don't lose the request, schedule for retry queue.
        await schedule_verify_retry(provider, tx_id, provider_ref=ref,
                                    reason=f"force_verify_crash: {e}")
        raise HTTPException(status_code=502,
                            detail=f"Échec vérification provider : {e}")

    if not v.get("ok"):
        await schedule_verify_retry(provider, tx_id, provider_ref=ref,
                                    reason=f"force_verify_api_unavailable: {v.get('reason')}")
        return {"credited": False, "status": "pending",
                "reason": v.get("reason") or "API indisponible — retry programmé."}

    if not v.get("is_paid"):
        return {"credited": False, "status": v.get("status") or "pending",
                "reason": "Paiement non encore confirmé par le provider."}

    # Authoritative success → credit + idempotent.
    async with pool.acquire() as conn:
        async with conn.transaction():
            cur = await conn.fetchrow(
                "SELECT status FROM transactions WHERE tx_id=$1 FOR UPDATE", tx_id)
            if cur and cur["status"] == "completed":
                return {"credited": True, "status": "completed",
                        "tx_id": tx_id, "already": True}
            await conn.execute(
                "UPDATE wallets SET balance=balance+$1, updated_at=$2 WHERE user_id=$3",
                _credit_usd(tx), datetime.now(timezone.utc), tx["to_user_id"],
            )
            await conn.execute(
                """UPDATE transactions
                      SET status='completed',
                          admin_notes=COALESCE(admin_notes,'')||' [user-force-verify]'
                    WHERE tx_id=$1""",
                tx_id,
            )
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """INSERT INTO notifications
                       (notif_id, user_id, type, title, message, data)
                       VALUES ($1, $2, 'deposit_completed',
                               'Dépôt confirmé', $3, $4)""",
                notif_id, tx["to_user_id"],
                f"Votre dépôt de {tx['amount']} USD a été crédité.",
                f'{{"tx_id": "{tx_id}", "provider": "{provider}", "via": "force_verify"}}',
            )
    await mark_retry_resolved(provider, tx_id, "credited via force-verify")
    logger.warning(f"[force-verify] CREDITED {provider}:{tx_id} by user {user['user_id']}")
    return {"credited": True, "status": "completed", "tx_id": tx_id, "via": "force_verify"}


# --- Payment webhooks ------------------------------------------------------
# These endpoints are called by Hubtel / NowPayments when a payment
# status changes. They are idempotent and safely re-runnable (a transaction
# already 'completed' will simply be acknowledged).
# Signature verification is enabled when the corresponding admin secret is set.


@router.post("/hubtel/webhook")
async def hubtel_webhook(request: Request):
    """Hubtel Online Checkout callback.
    Accepts Hubtel's native payload format:
        {
          "ResponseCode": "0000",
          "Status": "Success",
          "Data": {
            "CheckoutId": "...",
            "SalesInvoiceId": "...",
            "ClientReference": "dep_xxx",
            "Status": "Success"|"Failed"|"Unpaid",
            "Amount": 10.0,
            "Description": "...",
            ...
          }
        }
    Also tolerates a legacy/simple shape {tx_id, status, hubtel_ref, amount}
    for internal testing. Credits the user's wallet when Data.Status == "Success".

    If `hubtel_webhook_secret` is configured, verifies HMAC-SHA256 of the raw
    body against the `X-Auth-Signature` header (case-insensitive). When the
    secret is not configured, runs in DEV mode and accepts any payload — to
    be tightened once the admin pastes the real secret via the admin UI.
    """
    body = await request.body()
    # iter116 — Helper: ship webhook failures to AI Error Monitor (best-effort).
    async def _track_ipn_error(severity: str, message: str, *,
                               http_status: Optional[int] = None,
                               extra_stack: str = "") -> None:
        try:
            from services.error_monitor import record_error
            from database import get_pool as _gp
            _pool = await _gp()
            async with _pool.acquire() as _c:
                await record_error(
                    _c, source="backend", module="wallet.hubtel.ipn",
                    message=message[:500], stack=extra_stack[:2000],
                    severity=severity,
                    user_agent=request.headers.get("user-agent", "")[:255],
                    url=str(request.url)[:500],
                    http_status=http_status,
                )
        except Exception as _e:
            logger.warning(f"AI Error Monitor (hubtel.ipn) failed: {_e}")

    # --- Signature check -----------------------------------------------------
    secret = (await get_setting("hubtel_webhook_secret")) or ""
    if secret:
        import hmac
        import hashlib
        sig = (
            request.headers.get("X-Auth-Signature")
            or request.headers.get("x-auth-signature")
            or request.headers.get("X-Hubtel-Signature")
            or ""
        )
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not sig or not hmac.compare_digest(sig, expected):
            logger.warning(f"Hubtel webhook: invalid signature (got={sig[:12]}…)")
            await _track_ipn_error(
                "high",
                f"Hubtel IPN HMAC mismatch (sig_prefix={sig[:8] or 'none'})",
                http_status=401,
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # --- Parse payload (native Hubtel + legacy fallback) ---------------------
    import json as _json
    try:
        payload = _json.loads(body.decode() or "{}")
    except Exception as _je:
        await _track_ipn_error("high",
                               f"Hubtel IPN JSON parse error: {_je}",
                               http_status=400)
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    data = payload.get("Data") or payload.get("data") or {}
    # ClientReference echoes our tx_id (dep_xxx). Fallback on legacy field.
    tx_id = (
        data.get("ClientReference")
        or data.get("clientReference")
        or payload.get("tx_id")
        or ""
    )
    # Status can be on the inner Data or at the top level for the legacy shape.
    raw_status = str(
        data.get("Status")
        or data.get("status")
        or payload.get("status")
        or ""
    ).strip().lower()
    provider_ref = str(
        data.get("CheckoutId")
        or data.get("SalesInvoiceId")
        or data.get("TransactionId")
        or payload.get("hubtel_ref")
        or ""
    )
    if not tx_id:
        await _track_ipn_error("high",
                               "Hubtel IPN missing ClientReference / tx_id",
                               http_status=400)
        raise HTTPException(status_code=400, detail="Missing ClientReference / tx_id")

    logger.info(f"Hubtel webhook: tx_id={tx_id} status={raw_status} ref={provider_ref}")
    # iter191 — Persist the raw webhook for audit / EAA-vs-JAPAP diff.
    try:
        from services.hubtel_service import _persist_call_log
        await _persist_call_log(
            await get_pool(), kind="webhook", tx_id=tx_id,
            request_payload={
                "headers": {k: v for k, v in request.headers.items()
                             if k.lower() not in ("authorization", "cookie")},
                "raw_body_truncated": (body.decode(errors="ignore")[:2000]
                                        if isinstance(body, (bytes, bytearray)) else str(body)[:2000]),
                "parsed": payload,
            },
            response_status=200,
            response_body={"tx_id": tx_id, "status": raw_status,
                            "provider_ref": provider_ref},
            took_ms=0,
        )
    except Exception as _e:
        logger.warning(f"[hubtel] webhook log persist failed: {_e}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT * FROM transactions WHERE tx_id = $1 AND type = 'deposit'",
            tx_id,
        )
        if not tx:
            await _track_ipn_error(
                "medium",
                f"Hubtel IPN tx not found (tx_id={tx_id}, status={raw_status})",
                http_status=404,
            )
            raise HTTPException(status_code=404, detail="Transaction not found")
        if tx['status'] == 'completed':
            return {"status": "already_completed", "tx_id": tx_id}

        # Hubtel uses "Success"/"Paid" for confirmed payments. Anything else
        # (Failed, Cancelled, Unpaid, Expired...) is a non-success state.
        is_paid = raw_status in ("success", "paid", "completed", "finished")
        is_final_fail = raw_status in (
            "failed", "cancelled", "canceled", "unpaid", "expired", "rejected",
        )

        # --- STRICT INDEPENDENT VERIFICATION WITH HUBTEL --------------------
        # Security policy: NO credit is issued without an authoritative
        # confirmation from Hubtel's Transaction Status Check API. A spoofed
        # webhook alone is NEVER sufficient.
        if is_paid:
            from services.hubtel_service import (
                verify_transaction_status, HubtelConfigError,
            )
            from services.payment_health import (
                measure_verify, schedule_verify_retry, mark_retry_resolved,
            )
            try:
                async with measure_verify("hubtel", tx_id) as _m:
                    verification = await verify_transaction_status(
                        client_reference=tx_id,
                        checkout_id=provider_ref,
                    )
                    _m["ok"] = bool(verification.get("ok"))
                    _m["is_paid"] = bool(verification.get("is_paid"))
                    _m["http_status"] = verification.get("http_status")
            except HubtelConfigError:
                # Admin keys missing — we cannot verify, so we MUST NOT credit.
                logger.error(f"Hubtel not configured — refusing to credit {tx_id}")
                await _track_ipn_error(
                    "critical",
                    f"Hubtel IPN reçu mais clés admin absentes — crédit bloqué (tx={tx_id})",
                    http_status=503,
                )
                await conn.execute(
                    "UPDATE transactions SET admin_notes = $1 WHERE tx_id = $2",
                    f"Webhook reçu ({raw_status}) mais clés Hubtel non configurées — crédit refusé par sécurité.",
                    tx_id,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Hubtel non configuré — impossible de vérifier le paiement.",
                )
            except Exception as e:
                logger.error(f"Hubtel status verification crashed for {tx_id}: {e}")
                await _track_ipn_error(
                    "high",
                    f"Hubtel verify_transaction_status crash (tx={tx_id}): {e}",
                    http_status=502,
                    extra_stack=repr(e),
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"Impossible de vérifier le paiement auprès de Hubtel : {e}",
                )

            # Strict decision matrix:
            # - verify ok + is_paid=True  → CREDIT (verified)
            # - verify ok + is_paid=False → REJECT (webhook is spoofed or premature)
            # - verify not ok (API unavailable / whitelist / 400 not-found)
            #                              → DO NOT CREDIT, keep pending, log
            if not verification["ok"]:
                logger.warning(
                    f"Hubtel status API unavailable for {tx_id} "
                    f"({verification.get('reason')}) — KEEPING PENDING, no credit. "
                    f"Whitelist required : {verification.get('whitelist_required')}."
                )
                await _track_ipn_error(
                    "high",
                    f"Hubtel status API indisponible pour {tx_id}: "
                    f"{verification.get('reason')} (whitelist_required="
                    f"{verification.get('whitelist_required')})",
                )
                # iter117 — Auto-retry queue: revisit when API is back online.
                await schedule_verify_retry(
                    "hubtel", tx_id, provider_ref=provider_ref,
                    reason=f"api_unavailable: {verification.get('reason')}",
                )
                await conn.execute(
                    "UPDATE transactions SET admin_notes = $1 WHERE tx_id = $2",
                    (
                        f"Webhook 'Paid' reçu mais API de vérification Hubtel indisponible : "
                        f"{verification.get('reason')}. "
                        f"Crédit différé — l'utilisateur peut déclencher /hubtel/verify ou "
                        f"un admin peut approuver manuellement."
                    ),
                    tx_id,
                )
                return {
                    "status": "pending_verification",
                    "tx_id": tx_id,
                    "reason": verification.get("reason"),
                    "whitelist_required": verification.get("whitelist_required", False),
                }
            if not verification["is_paid"]:
                logger.warning(
                    f"Hubtel webhook claimed Paid for {tx_id} but status API "
                    f"returned {verification.get('status')}. REFUSING (spoof/early)."
                )
                await _track_ipn_error(
                    "critical",
                    f"Hubtel IPN affirme Paid mais API renvoie "
                    f"{verification.get('status')} — spoof/early détecté (tx={tx_id})",
                )
                await conn.execute(
                    "UPDATE transactions SET admin_notes = $1 WHERE tx_id = $2",
                    f"Webhook 'Paid' mais Hubtel confirme status={verification.get('status')} — crédit refusé.",
                    tx_id,
                )
                return {
                    "status": "unverified",
                    "tx_id": tx_id,
                    "webhook_said": raw_status,
                    "hubtel_actual": verification.get("status"),
                    "reason": "Hubtel indique que la transaction n'est pas payée.",
                }

            # All good — Hubtel authoritatively confirmed the payment.
            provider_ref = verification.get("provider_ref") or provider_ref
            async with conn.transaction():
                await conn.execute(
                    "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                    _credit_usd(tx), datetime.now(timezone.utc), tx['to_user_id'],
                )
                await conn.execute(
                    "UPDATE transactions SET status = 'completed', reference = $1, admin_notes = $2 WHERE tx_id = $3",
                    provider_ref or tx['reference'],
                    "auto-verified via Hubtel Transaction Status API",
                    tx_id,
                )
                notif_id = f"notif_{uuid.uuid4().hex[:12]}"
                await conn.execute(
                    """
                    INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                    VALUES ($1, $2, 'deposit_completed', 'Dépôt confirmé',
                            $3, $4)
                    """,
                    notif_id, tx['to_user_id'],
                    f"Votre dépôt de {_credit_usd(tx)} USD a été crédité.",
                    f'{{"tx_id": "{tx_id}", "provider": "hubtel", "ref": "{provider_ref}"}}',
                )
            # iter117 — clear any pending retry once we credited.
            await mark_retry_resolved("hubtel", tx_id, "credited via webhook")
            return {"status": "completed", "tx_id": tx_id, "verified": True}

        if is_final_fail:
            await conn.execute(
                "UPDATE transactions SET status = 'rejected', admin_notes = $1 WHERE tx_id = $2",
                f"Hubtel webhook: {raw_status} (ref={provider_ref})", tx_id,
            )
            return {"status": "rejected", "tx_id": tx_id, "reason": raw_status}

        # Any other intermediate state (pending/processing…) — keep pending.
        return {"status": "pending", "tx_id": tx_id, "reported": raw_status}


class NowPaymentsWebhookPayload(BaseModel):
    payment_id: str = ""
    payment_status: str = ""      # "finished" | "failed" | "expired" | "confirming" | ...
    order_id: str = ""            # our tx_id
    pay_amount: Optional[float] = None
    pay_currency: str = ""


@router.post("/nowpayments/webhook")
async def nowpayments_webhook(request: Request):
    """NowPayments IPN callback — double-verified.
    Security layers :
      1. HMAC-SHA512 signature on x-nowpayments-sig (sorted-keys JSON body).
      2. Independent GET /payment/{id} call to NowPayments API before crediting.
    Credit is NEVER issued from the webhook alone — Hubtel-style strict policy.
    """
    from services.nowpayments_service import (
        verify_ipn_signature, verify_payment_status,
        NowPaymentsConfigError,
    )
    body_raw = await request.body()
    # iter116 — Helper: ship NowPayments webhook failures to AI Error Monitor.
    async def _track_npn_error(severity: str, message: str, *,
                                http_status: Optional[int] = None,
                                extra_stack: str = "") -> None:
        try:
            from services.error_monitor import record_error
            from database import get_pool as _gp
            _pool = await _gp()
            async with _pool.acquire() as _c:
                await record_error(
                    _c, source="backend", module="wallet.nowpayments.ipn",
                    message=message[:500], stack=extra_stack[:2000],
                    severity=severity,
                    user_agent=request.headers.get("user-agent", "")[:255],
                    url=str(request.url)[:500],
                    http_status=http_status,
                )
        except Exception as _e:
            logger.warning(f"AI Error Monitor (nowpayments.ipn) failed: {_e}")

    import json as _json
    try:
        payload_dict = _json.loads(body_raw.decode() or "{}")
    except Exception as _je:
        await _track_npn_error("high",
                                f"NowPayments IPN JSON parse error: {_je}",
                                http_status=400)
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    secret = (await get_setting("nowpayments_ipn_secret")) or ""
    if secret:
        sig = (
            request.headers.get("x-nowpayments-sig")
            or request.headers.get("X-NowPayments-Sig")
            or ""
        )
        if not verify_ipn_signature(body_raw, sig, secret):
            logger.warning(f"NowPayments webhook: invalid signature (got={sig[:12]}…)")
            await _track_npn_error(
                "high",
                f"NowPayments IPN HMAC mismatch (sig_prefix={sig[:8] or 'none'})",
                http_status=401,
            )
            raise HTTPException(status_code=401, detail="Invalid IPN signature")

    order_id = str(payload_dict.get("order_id") or "")
    payment_id = str(payload_dict.get("payment_id") or payload_dict.get("id") or "")
    raw_status = str(payload_dict.get("payment_status") or "").lower()
    if not order_id:
        await _track_npn_error("high",
                                "NowPayments IPN missing order_id",
                                http_status=400)
        raise HTTPException(status_code=400, detail="Missing order_id")

    logger.info(f"NowPayments webhook: order_id={order_id} payment_id={payment_id} status={raw_status}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT * FROM transactions WHERE tx_id = $1 AND type = 'deposit'",
            order_id,
        )
        if not tx:
            await _track_npn_error(
                "medium",
                f"NowPayments IPN tx not found (order_id={order_id}, status={raw_status})",
                http_status=404,
            )
            raise HTTPException(status_code=404, detail="Transaction not found")
        if tx['status'] == 'completed':
            return {"status": "already_completed", "tx_id": order_id}

        # Non-paid states — no verification needed.
        if raw_status in ("failed", "expired"):
            await conn.execute(
                "UPDATE transactions SET status = 'rejected', admin_notes = $1 WHERE tx_id = $2",
                f"NowPayments IPN: {raw_status}", order_id,
            )
            return {"status": "rejected", "tx_id": order_id, "reason": raw_status}
        if raw_status != "finished":
            # confirming / waiting / partially_paid — stay pending
            return {"status": "pending", "tx_id": order_id, "reported": raw_status}

        # --- STRICT INDEPENDENT VERIFICATION --------------------------------
        # Webhook claims finished → poll NowPayments directly to confirm.
        if not payment_id:
            logger.warning(f"NowPayments webhook has no payment_id for {order_id}")
            return {
                "status": "pending_verification",
                "tx_id": order_id,
                "reason": "Webhook sans payment_id — impossible de vérifier.",
            }
        try:
            from services.payment_health import (
                measure_verify, schedule_verify_retry, mark_retry_resolved,
            )
            async with measure_verify("nowpayments", order_id) as _m:
                verification = await verify_payment_status(payment_id)
                _m["ok"] = bool(verification.get("ok"))
                _m["is_paid"] = bool(verification.get("is_paid"))
                _m["http_status"] = verification.get("http_status")
        except NowPaymentsConfigError:
            logger.error(f"NowPayments not configured — refusing to credit {order_id}")
            await _track_npn_error(
                "critical",
                f"NowPayments IPN reçu mais clés admin absentes — crédit bloqué (order={order_id})",
                http_status=503,
            )
            raise HTTPException(
                status_code=503,
                detail="NowPayments non configuré — impossible de vérifier.",
            )
        except Exception as e:
            logger.error(f"NowPayments status verification crashed for {order_id}: {e}")
            await _track_npn_error(
                "high",
                f"NowPayments verify_payment_status crash (order={order_id}): {e}",
                http_status=502,
                extra_stack=repr(e),
            )
            raise HTTPException(
                status_code=502,
                detail=f"Impossible de vérifier le paiement : {e}",
            )

        if not verification["ok"]:
            logger.warning(
                f"NowPayments status API unavailable for {order_id} "
                f"({verification.get('reason')}) — KEEPING PENDING."
            )
            await _track_npn_error(
                "high",
                f"NowPayments status API indisponible pour {order_id}: "
                f"{verification.get('reason')}",
            )
            # iter117 — Auto-retry once API is back.
            await schedule_verify_retry(
                "nowpayments", order_id, provider_ref=payment_id,
                reason=f"api_unavailable: {verification.get('reason')}",
            )
            await conn.execute(
                "UPDATE transactions SET admin_notes = $1 WHERE tx_id = $2",
                (
                    f"Webhook 'finished' reçu mais API NowPayments indisponible : "
                    f"{verification.get('reason')}. Crédit différé."
                ),
                order_id,
            )
            return {
                "status": "pending_verification",
                "tx_id": order_id,
                "reason": verification.get("reason"),
            }
        if not verification["is_paid"]:
            logger.warning(
                f"NowPayments webhook claimed finished for {order_id} but status API "
                f"returned {verification.get('status')}. REFUSING."
            )
            await _track_npn_error(
                "critical",
                f"NowPayments IPN affirme finished mais API renvoie "
                f"{verification.get('status')} — spoof/early détecté (order={order_id})",
            )
            await conn.execute(
                "UPDATE transactions SET admin_notes = $1 WHERE tx_id = $2",
                f"Webhook 'finished' mais NowPayments confirme status={verification.get('status')} — crédit refusé.",
                order_id,
            )
            return {
                "status": "unverified",
                "tx_id": order_id,
                "nowpayments_actual": verification.get("status"),
                "reason": "NowPayments indique que la transaction n'est pas finalisée.",
            }

        # Authoritative confirmation — credit the wallet.
        async with conn.transaction():
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                _credit_usd(tx), datetime.now(timezone.utc), tx['to_user_id'],
            )
            await conn.execute(
                "UPDATE transactions SET status = 'completed', reference = $1, admin_notes = $2 WHERE tx_id = $3",
                payment_id or tx['reference'],
                "auto-verified via NowPayments /payment status API",
                order_id,
            )
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """
                INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                VALUES ($1, $2, 'deposit_completed', 'Dépôt confirmé', $3, $4)
                """,
                notif_id, tx['to_user_id'],
                f"Votre dépôt de {tx['amount']} USD a été crédité.",
                f'{{"tx_id": "{order_id}", "provider": "nowpayments", "ref": "{payment_id}"}}',
            )
        # iter117 — clear pending retry once credited.
        await mark_retry_resolved("nowpayments", order_id, "credited via webhook")
        return {"status": "completed", "tx_id": order_id, "verified": True}


@router.get("/nowpayments/test-connection")
async def nowpayments_test_connection(request: Request):
    """Admin-only: verifies NowPayments credentials."""
    from routes.admin import require_admin
    await require_admin(request)
    from services.nowpayments_service import test_connection
    return await test_connection()


@router.post("/nowpayments/verify/{tx_id}")
async def nowpayments_verify_tx(tx_id: str, request: Request):
    """Manually re-verify a NowPayments deposit. Owner-or-admin only."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT * FROM transactions WHERE tx_id = $1 AND type = 'deposit'",
            tx_id,
        )
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction introuvable")
        if tx['to_user_id'] != user['user_id'] and not user.get('is_admin'):
            raise HTTPException(status_code=403, detail="Accès refusé")
        if tx['status'] == 'completed':
            return {"status": "already_completed", "tx_id": tx_id}

        from services.nowpayments_service import (
            verify_payment_status, NowPaymentsConfigError, NowPaymentsAPIError,
        )
        payment_id = tx['reference'] or ""
        if not payment_id:
            raise HTTPException(status_code=400, detail="Pas d'ID de paiement NowPayments enregistré pour cette transaction.")
        try:
            verification = await verify_payment_status(payment_id)
        except NowPaymentsConfigError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except NowPaymentsAPIError as e:
            raise HTTPException(status_code=502, detail=str(e))

        if not verification["ok"]:
            return {
                "status": "check_failed",
                "tx_id": tx_id,
                "reason": verification.get("reason"),
                "nowpayments_status": verification.get("status"),
            }
        if not verification["is_paid"]:
            return {
                "status": "not_paid_yet",
                "tx_id": tx_id,
                "nowpayments_status": verification.get("status"),
            }

        async with conn.transaction():
            await conn.execute(
                "UPDATE wallets SET balance = balance + $1, updated_at = $2 WHERE user_id = $3",
                _credit_usd(tx), datetime.now(timezone.utc), tx['to_user_id'],
            )
            await conn.execute(
                "UPDATE transactions SET status = 'completed', reference = $1, admin_notes = $2 WHERE tx_id = $3",
                payment_id, "manually re-verified via NowPayments API", tx_id,
            )
            notif_id = f"notif_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """
                INSERT INTO notifications (notif_id, user_id, type, title, message, data)
                VALUES ($1, $2, 'deposit_completed', 'Dépôt confirmé', $3, $4)
                """,
                notif_id, tx['to_user_id'],
                f"Votre dépôt de {tx['amount']} USD a été crédité.",
                f'{{"tx_id": "{tx_id}", "provider": "nowpayments", "ref": "{payment_id}", "method": "manual_verify"}}',
            )
        return {"status": "completed", "tx_id": tx_id, "verified": True, "nowpayments_status": verification["status"]}



# ══════════════════════════════════════════════════════════════════════════
#  iter117 — Payment Health cockpit (admin)
# ══════════════════════════════════════════════════════════════════════════
@router.get("/admin/payment-health")
async def admin_payment_health(request: Request, hours: int = 24):
    """Aggregated Payment Health view for the admin cockpit:
       • verify_payment_status latency (p50/p95/max) per provider
       • IPN error counts per provider (24h window)
       • Top 5 IPN/QR error groups
       • Transactions stuck in pending_verification
       • Retry queue stats (due / scheduled / abandoned)
    """
    from routes.auth import require_admin as _ra
    from services.payment_health import build_cockpit
    await _ra(request)
    hours = max(1, min(int(hours or 24), 168))   # clamp 1h..7d
    return await build_cockpit(window_hours=hours)


@router.post("/admin/payment-health/digest")
async def admin_payment_health_digest(request: Request, force: bool = False):
    """Manually trigger a Payment Health digest e-mail to OPS_INBOX_EMAIL.
    Useful for testing the daily report or sending an on-demand snapshot.

    iter151 — without `?force=true`, calling this twice the same day is a
    no-op (the persistent guard short-circuits). Pass `force=true` to
    re-send anyway (e.g., ops needs an updated snapshot mid-day).
    """
    from routes.auth import require_admin as _ra
    from services.payment_health import send_daily_digest
    await _ra(request)
    return await send_daily_digest(force=bool(force), worker_id="admin")


@router.post("/admin/payment-health/retry/{provider}/{tx_id}")
async def admin_payment_health_force_retry(provider: str, tx_id: str,
                                            request: Request):
    """Force a verify-retry to run NOW for a stuck transaction.
    Returns the retry outcome (credited / api_unavailable / etc.)."""
    from routes.auth import require_admin as _ra
    await _ra(request)
    if provider not in ("hubtel", "nowpayments"):
        raise HTTPException(status_code=400, detail="Invalid provider")
    pool = await get_pool()
    async with pool.acquire() as conn:
        tx = await conn.fetchrow(
            "SELECT reference FROM transactions WHERE tx_id=$1", tx_id)
    provider_ref = (tx["reference"] if tx else "") or ""
    from services.payment_verify_retry_worker import _retry_one
    ok, reason = await _retry_one(provider, tx_id, provider_ref)
    return {"ok": ok, "reason": reason, "tx_id": tx_id, "provider": provider}
