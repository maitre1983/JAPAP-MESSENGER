"""
iter238c — Admin Wallet Diagnostics (STRICTLY ADDITIVE).

  GET /api/admin/wallet/diagnostics?user_id=XXX  (admin/superadmin only)

Returns the same eligibility data that previously leaked through the
"🔍 Debug admin" banner on /wallet (now removed in iter238c). Strictly
back-office — never accessible to regular users; the user_id is passed
as a query param so admins can troubleshoot any user's MoMo eligibility
without impersonation.

Mirrors the (read-only) logic from `WalletPage.js` :
  • Detects the country from `country_code` then `country` (2-char ISO)
  • Computes the same `eligible*` flags used by the UI
  • Adds wallet balance + role for full context

Does NOT modify any existing route, table, or behavior.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from database import get_pool
from routes.auth import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin_wallet_diagnostics"])

# Mirror of the WalletPage.js constant. Kept here for backend-side
# computation; there is intentionally no shared module since both lists
# describe the SAME static business rule.
_WAVE_COUNTRIES = {"BF", "CI", "ML", "NE", "SN", "GM", "UG"}


def _norm_iso(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip().upper()
    return s if len(s) == 2 else ""


@router.get("/api/admin/wallet/diagnostics")
async def admin_wallet_diagnostics(
    request: Request,
    user_id: str = Query(..., min_length=1),
):
    await require_admin(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        u = await conn.fetchrow(
            """SELECT user_id, username, email, country, country_code,
                      phone_number, role, language
                 FROM users WHERE user_id = $1""",
            user_id,
        )
    if not u:
        raise HTTPException(status_code=404, detail="user_not_found")

    cc_iso = _norm_iso(u["country_code"])
    cc_raw = _norm_iso(u["country"])
    cc = cc_iso or cc_raw
    phone = (u["phone_number"] or "").strip()

    eligible_om_deposit = bool(cc) and cc != "GH"
    eligible_om_withdraw = (cc == "CM") and phone.startswith("+237")
    eligible_wave = cc in _WAVE_COUNTRIES

    # Wallet balance — best-effort, does not fail the diagnostic if missing.
    balance: float | None = None
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT balance FROM wallets WHERE user_id = $1", user_id,
            )
            if row:
                balance = float(row["balance"])
        except Exception:  # noqa: BLE001
            balance = None

    return {
        "user": {
            "user_id": u["user_id"],
            "username": u["username"],
            "email": u["email"],
            "role": u["role"],
            "language": u["language"],
        },
        "country": {
            "resolved": cc or None,
            "country_code": cc_iso or None,
            "country_raw": cc_raw or None,
        },
        "phone": phone or None,
        "wallet": {"balance_usd": balance},
        "eligibility": {
            "orange_money_deposit": eligible_om_deposit,
            "orange_money_withdraw": eligible_om_withdraw,
            "wave": eligible_wave,
        },
    }


admin_wallet_diagnostics_router = router

__all__ = ["router", "admin_wallet_diagnostics_router"]
