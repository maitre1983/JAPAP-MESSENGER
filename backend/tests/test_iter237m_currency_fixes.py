"""iter237m — Regression tests for the 3 corrections of 07/02/2026.

Coverage:
  * /api/messages/send-money stores currency='USD' (was defaulting to XAF
    via the column DEFAULT)
  * Minimum amount is 0.10 USD (was 50 XAF)
  * The transactions table column DEFAULT for `currency` is now 'USD' (DDL
    upgrade applied at startup via dcq_paid._ensure_ddl).
  * Frontend bareme cells (paid daily challenge) all carry data-testid
    `paid-bareme-pct-${key}` and `paid-bareme-delta-${key}` (asserted by
    static source-grep so we never regress the polished 3-column table).
"""
from __future__ import annotations

import os
import json
import asyncio
import urllib.request
import urllib.error
from pathlib import Path

API = os.environ.get("API_BASE", "http://0.0.0.0:8001")
ALICE = ("alice@japap.com", "Alice2026!")
ADMIN = ("admin@japap.com", "JapapAdmin2024!")
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}


def _http(method, path, *, token=None, json_body=None):
    url = f"{API}{path}"
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def _login(email, pwd):
    code, data = _http("POST", "/api/auth/login",
                        json_body={"email": email, "password": pwd, **CAPTCHA})
    assert code == 200, data
    return data["access_token"], data["user"]["user_id"]


async def _query(sql, *args):
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await conn.close()


def test_send_money_min_usd_threshold():
    """0.05 USD must be rejected with 400."""
    tok_a, _ = _login(*ALICE)
    _, admin_uid = _login(*ADMIN)
    code, body = _http("POST", "/api/messages/send-money",
                        token=tok_a,
                        json_body={"to_user_id": admin_uid, "amount": 0.05})
    assert code == 400, body
    assert "0.10 USD" in body.get("detail", "")


def test_send_money_persists_in_usd():
    """A successful chat-money transfer stores currency='USD' and the
    response carries currency='USD'."""
    tok_a, _ = _login(*ALICE)
    _, admin_uid = _login(*ADMIN)
    code, body = _http("POST", "/api/messages/send-money",
                        token=tok_a,
                        json_body={"to_user_id": admin_uid, "amount": 0.50,
                                   "note": "iter237m regression"})
    assert code == 200, body
    assert body.get("currency") == "USD", body
    # DB-level: last chat_money tx for Alice → admin
    row = asyncio.run(_query(
        "SELECT currency FROM transactions WHERE type='chat_money' "
        "ORDER BY created_at DESC LIMIT 1"))
    assert row is not None
    assert row["currency"] == "USD"


def test_transactions_currency_default_is_usd():
    """The column DEFAULT for transactions.currency must be 'USD' to prevent
    legacy fallbacks. dcq_paid._ensure_ddl applies the ALTER on startup."""
    row = asyncio.run(_query(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name='transactions' AND column_name='currency'"))
    assert row is not None
    assert "USD" in (row["column_default"] or ""), row["column_default"]


def test_paid_daily_bareme_three_columns_in_source():
    """The 3-column polished bareme (Score | %mise | Δ Wallet) must remain
    in the modal source. We assert a few testids and the column header
    strings — pure static check, no browser needed."""
    src = Path("/app/frontend/src/components/games/PaidDailyChallengeFlow.jsx").read_text(encoding="utf-8")
    assert 'paid-bareme-pct-' in src, "missing per-row %mise testid"
    assert 'paid-bareme-delta-' in src, "missing per-row Δ Wallet testid"
    assert 'Δ Wallet' in src or 'Δ\u00a0Wallet' in src, \
        "missing 'Δ Wallet' header — the new polished table is gone"
    assert '% mise' in src, "missing '% mise' header"


def test_chat_money_modal_says_usd_in_source():
    """The send-money modal label must be 'Montant (USD)' and the quick chips
    must be USD denominations [1, 2, 5, 10]."""
    src = Path("/app/frontend/src/pages/ChatPage.js").read_text(encoding="utf-8")
    assert "Montant (USD)" in src, "label still in XAF"
    assert "[1, 2, 5, 10].map" in src, "quick chips are not USD denominations"
    assert "0.10 USD" in src, "min amount UI hint not in USD"
    assert "WalletDepositCurrencySelector" in src, \
        "local-currency preview component is not wired into ChatPage"


if __name__ == "__main__":
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(0 if failed == 0 else 1)
