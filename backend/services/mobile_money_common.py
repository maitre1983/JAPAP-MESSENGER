"""
Mobile Money — shared helpers (iter235).
========================================
Common utilities for Orange Money (CM) and Wave (West Africa) integrations.
Strictly additive: imported only by the new routes/services that ship with
this iteration. Never patches existing modules.
"""
from __future__ import annotations
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, Request

from database import get_pool

# --- Country gates -----------------------------------------------------------
# iter236 — Orange Money rules:
#   • Deposits  : visible for ALL users EXCEPT those in BLOCKED list (Ghana
#                 has its own Hubtel flow). Server enforces this gate.
#   • Withdrawals: still restricted to country == CM (and phone +237).
OM_ALLOWED_COUNTRIES = {"CM"}              # withdrawal-only gate (legacy)
OM_DEPOSIT_BLOCKED_COUNTRIES = {"GH"}      # iter236 — deposit gate
WAVE_ALLOWED_COUNTRIES_DEFAULT = ["BF", "CI", "ML", "NE", "SN", "GM", "UG"]
# iter237z — Wave reference format varies by country (e.g. SN: T_ABC123-XYZ789,
# CI: xot-24p35p8qg22d0, others have their own formats). We only enforce a
# minimum length + non-empty constraint and let the agent verify the actual
# transaction in the Wave dashboard. Accept any printable chars >= 6 long.
WAVE_REF_PATTERN = re.compile(r"^[\w\-]{6,120}$")


# --- Settings keys (all server-side, never exposed in user responses) -------
OM_KEYS = {
    "deposit_rate":  "om_deposit_rate_usd_xaf",
    "withdraw_rate": "om_withdraw_rate_usd_xaf",
    "withdraw_min":  "om_withdraw_min_usd",
    "receiver_name": "om_receiver_name",
    "receiver_num":  "om_receiver_number",
    "enabled":       "om_enabled",
}
WAVE_KEYS = {
    "deposit_rate":  "wave_deposit_rate_usd_xof",
    "withdraw_rate": "wave_withdraw_rate_usd_xof",
    "withdraw_min":  "wave_withdraw_min_usd",
    "receiver_name": "wave_receiver_name",
    "receiver_num":  "wave_receiver_number",
    "allowed_countries": "wave_allowed_countries",
    "enabled":       "wave_enabled",
}


# --- DDL for the platform_settings KV (additive, idempotent) ----------------
async def ensure_settings_table(conn) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS platform_settings (
              key VARCHAR(80) PRIMARY KEY,
              value TEXT,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
           )"""
    )


async def get_setting(conn, key: str, default: Optional[str] = None) -> Optional[str]:
    await ensure_settings_table(conn)
    v = await conn.fetchval("SELECT value FROM platform_settings WHERE key = $1", key)
    return v if v is not None else default


async def set_setting(conn, key: str, value: str) -> None:
    await ensure_settings_table(conn)
    await conn.execute(
        """INSERT INTO platform_settings (key, value) VALUES ($1, $2)
             ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
        key, str(value),
    )


# --- ID + validators --------------------------------------------------------
def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:18]}"


def get_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") or ""
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "0.0.0.0")[:64]


def get_user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:300]


# --- Approximate name matching (anti-fraud on withdrawals) ------------------
def _normalise(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return {w for w in re.split(r"\W+", s) if len(w) >= 3}


def names_match(account_name: str, withdrawal_name: str) -> bool:
    """Returns True if the two names share at least one word ≥3 chars after
    diacritic-stripping + lowercasing. Defensive default: True if either side
    is empty (so we don't block users with unset display names)."""
    a, b = _normalise(account_name), _normalise(withdrawal_name)
    if not a or not b:
        return True
    return bool(a & b)


# --- Rate limiting (DB-backed, no Redis dep) --------------------------------
async def enforce_rate_limit(conn, *, table: str, user_id: str,
                              max_count: int, window_seconds: int) -> None:
    """Counts rows from `table` for this user_id in the last `window_seconds`.
    Raises 429 if `max_count` is reached. Tables involved are user-owned
    (orange_money_*/wave_*) and have a `created_at` + `user_id` index.
    """
    n = await conn.fetchval(
        f"SELECT COUNT(*) FROM {table} WHERE user_id = $1 "
        "AND created_at > NOW() - ($2 || ' seconds')::interval",
        user_id, str(int(window_seconds)),
    )
    if int(n or 0) >= max_count:
        raise HTTPException(
            status_code=429,
            detail=("Trop de soumissions récentes. Réessaie plus tard."),
        )


# --- Wallet ledger insert (uses existing transactions table) ----------------
def _new_tx_id() -> str:
    return f"mm_{uuid.uuid4().hex[:18]}"


async def write_ledger_pending(conn, *, user_id: str, amount_usd: Decimal,
                                tx_type: str, reference: str, notes: str,
                                outgoing: bool) -> str:
    """Pending entry visible in the user's wallet history. We never touch
    the wallets balance for deposits (they're credited only after admin
    verification). For withdrawals, the calling withdraw flow handles the
    balance debit explicitly inside its transaction.
    """
    tx_id = _new_tx_id()
    from_id = user_id if outgoing else None
    to_id = None if outgoing else user_id
    await conn.execute(
        """INSERT INTO transactions (tx_id, from_user_id, to_user_id, type,
                                     amount, currency, status, reference, notes, created_at)
           VALUES ($1, $2, $3, $4, $5, 'USD', 'pending', $6, $7, NOW())""",
        tx_id, from_id, to_id, tx_type, amount_usd, reference[:120], notes[:500],
    )
    return tx_id


async def settle_ledger(conn, tx_id: str, status: str) -> None:
    """Status transition for a pending ledger entry written by write_ledger_pending.
    `status` ∈ {'completed','rejected'}. No-ops if the row was already settled."""
    if status not in ("completed", "rejected"):
        return
    await conn.execute(
        "UPDATE transactions SET status=$1 WHERE tx_id=$2 AND status='pending'",
        status, tx_id,
    )


# --- Country lookup --------------------------------------------------------
async def get_user_country(conn, user_id: str) -> str:
    """Return the user's country as an ISO-2 code, uppercased.

    iter237e — Historical data inconsistency : the `country` text column
    contains free-form values (e.g. 'Cameroun', '', 'GH' from typos) while
    `country_code` is the canonical ISO-2 from the signup form. We prefer
    `country_code` and fall back on a length-2 `country` only when the
    canonical one is missing. This unblocks legitimate CM users whose
    `country` got corrupted (Daf : country='GH', country_code='CM').
    """
    row = await conn.fetchrow(
        "SELECT country, country_code FROM users WHERE user_id = $1", user_id,
    )
    if not row:
        return ""
    cc = (row["country_code"] or "").strip().upper()
    if cc and len(cc) == 2:
        return cc
    raw = (row["country"] or "").strip().upper()
    # Only trust `country` if it's already a 2-letter ISO code.
    return raw if len(raw) == 2 else ""


__all__ = [
    "OM_ALLOWED_COUNTRIES", "OM_DEPOSIT_BLOCKED_COUNTRIES",
    "WAVE_ALLOWED_COUNTRIES_DEFAULT",
    "WAVE_REF_PATTERN", "OM_KEYS", "WAVE_KEYS",
    "get_setting", "set_setting", "ensure_settings_table",
    "new_id", "get_client_ip", "get_user_agent",
    "names_match", "enforce_rate_limit",
    "write_ledger_pending", "settle_ledger", "get_user_country",
    "get_pool",
]
