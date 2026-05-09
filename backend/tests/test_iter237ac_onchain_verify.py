"""iter237ac — Backend tests for on-chain auto-verification of USDT deposits.

Covers:
  • Service unit tests:
      - detect_network_from_notes() for TRC20/BEP20/BSC/non-USDT
      - verify_usdt_deposit('trc20', fake_hash, amount) -> not_found
      - verify_usdt_deposit('bep20', fake_hash, amount) -> not_found (BSC RPC)
      - verify_usdt_deposit('ethereum', ...) -> config_missing / unknown_network
  • Endpoint integration tests:
      - PATCH /api/wallet/deposit/{tx_id}/hash with fake hash
        -> 200, credited=False, status=pending, verification key present
      - Idempotent: 2 calls -> both 200, status remains pending
      - Wallet balance NOT changed after fake-hash submission
      - Response shape contract (verification dict, credited bool, status str)
  • Regression: short hash -> 400; non-owner -> 404; non-USDT method -> 400
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from decimal import Decimal

import pytest
import requests

# Ensure backend/ is on sys.path so we can import services.usdt_onchain_verify
sys.path.insert(0, "/app/backend")

# Load backend env so JAPAP_TRC20_ADDRESS / JAPAP_BEP20_ADDRESS are set
try:
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
except Exception:
    pass

from services.usdt_onchain_verify import (  # noqa: E402
    detect_network_from_notes,
    verify_usdt_deposit,
)

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
BYPASS = "JAPAP_E2E_BYPASS_2026"

ALICE = {"email": "alice@japap.com", "password": "Alice2026!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}

# Plausible-looking but definitely-not-real hashes
FAKE_TRC20_HASH = "f" * 64  # 64 hex chars (TRON style)
FAKE_BEP20_HASH = "0x" + "e" * 64  # 66 chars (EVM style)
SHORT_HASH = "abc"

_TOKEN_CACHE: dict = {}


def _login(creds: dict) -> str:
    key = creds["email"]
    if key in _TOKEN_CACHE:
        return _TOKEN_CACHE[key]
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={**creds, "captcha_id": BYPASS, "captcha_answer": "0"},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=120,
            )
            if r.status_code == 200:
                tok = r.json()["access_token"]
                _TOKEN_CACHE[key] = tok
                return tok
            last_err = f"{r.status_code} {r.text[:120]}"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        time.sleep(5 * (attempt + 1))
    pytest.skip(f"Login unavailable for {key}: {last_err}")


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _create_usdt_deposit(token: str, method: str = "usdt_trc20", amount: float = 5.0) -> str:
    r = requests.post(
        f"{BASE_URL}/api/wallet/deposit",
        headers=_h(token),
        json={"amount": amount, "method": method, "notes": "iter237ac test", "reference": ""},
        timeout=15,
    )
    assert r.status_code == 200, f"deposit init failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    assert data.get("status") == "pending"
    return data["tx_id"]


def _get_balance(token: str) -> Decimal:
    r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=_h(token), timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    return Decimal(str(body.get("balance") or body.get("balance_usd") or 0))


# ───────────── Service-level (unit) tests ─────────────

class TestDetectNetwork:
    def test_trc20_detected(self):
        assert detect_network_from_notes("[USDT (TRC20) manuel] payment") == "trc20"
        assert detect_network_from_notes("Lower trc20 should also match".upper().lower() or "trc20") == "trc20"

    def test_bep20_detected(self):
        assert detect_network_from_notes("[USDT (BEP20) manuel] xx") == "bep20"

    def test_bsc_keyword_maps_to_bep20(self):
        assert detect_network_from_notes("Sent via BSC network") == "bep20"

    def test_non_usdt_returns_none(self):
        assert detect_network_from_notes("Hubtel mobile money payment") is None
        assert detect_network_from_notes("Wave Senegal transfer") is None
        assert detect_network_from_notes("Orange Money OM") is None
        assert detect_network_from_notes("") is None
        assert detect_network_from_notes(None) is None  # type: ignore[arg-type]


class TestVerifyUsdtDeposit:
    """Async unit tests for verify_usdt_deposit. Uses a fresh event loop each
    test to avoid pytest-asyncio plugin requirement."""

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_trc20_fake_hash_not_found(self):
        result = self._run(verify_usdt_deposit("trc20", FAKE_TRC20_HASH, Decimal("5")))
        assert isinstance(result, dict)
        assert result.get("verified") is False
        # Tronscan may return 'not_found' or rate-limit with 'error' — both acceptable
        assert result.get("status") in ("not_found", "error", "no_transfer", "wrong_recipient")
        assert "reason" in result and isinstance(result["reason"], str)

    def test_bep20_fake_hash_not_found(self):
        result = self._run(verify_usdt_deposit("bep20", FAKE_BEP20_HASH, Decimal("5")))
        assert isinstance(result, dict)
        assert result.get("verified") is False
        # Public BSC RPC: a fake but well-formed hash returns null receipt → not_found
        assert result.get("status") in ("not_found", "error")
        assert "reason" in result

    def test_ethereum_unsupported(self):
        # No JAPAP_ETH_ADDRESS env var + ethereum branch isn't in _get_japap_address.
        result = self._run(verify_usdt_deposit("ethereum", FAKE_BEP20_HASH, Decimal("5")))
        assert result.get("verified") is False
        # _get_japap_address returns None for ethereum → config_missing
        assert result.get("status") in ("config_missing", "unknown_network")

    def test_unknown_network(self):
        result = self._run(verify_usdt_deposit("foobar", FAKE_BEP20_HASH, Decimal("5")))
        assert result.get("verified") is False
        assert result.get("status") in ("config_missing", "unknown_network")

    def test_verified_field_always_present(self):
        for net in ("trc20", "bep20", "ethereum", ""):
            r = self._run(verify_usdt_deposit(net, FAKE_BEP20_HASH, Decimal("5")))
            assert "verified" in r and isinstance(r["verified"], bool)
            assert "status" in r and isinstance(r["status"], str)


# ───────────── Endpoint-level integration tests ─────────────

@pytest.fixture(scope="session")
def alice_token() -> str:
    return _login(ALICE)


@pytest.fixture(scope="session")
def bob_token() -> str:
    return _login(BOB)


@pytest.fixture
def alice_trc20_tx(alice_token: str) -> str:
    return _create_usdt_deposit(alice_token, "usdt_trc20", 5.0)


@pytest.fixture
def alice_bep20_tx(alice_token: str) -> str:
    return _create_usdt_deposit(alice_token, "usdt_bep20", 5.0)


class TestPatchHashTrc20FakeHash:
    """Negative path: pending USDT TRC20 deposit + fake hash → credited=False."""

    def test_response_shape(self, alice_token: str, alice_trc20_tx: str):
        r = requests.patch(
            f"{BASE_URL}/api/wallet/deposit/{alice_trc20_tx}/hash",
            headers=_h(alice_token),
            json={"tx_hash": FAKE_TRC20_HASH},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Required keys per iter237ac contract
        assert "credited" in body and isinstance(body["credited"], bool)
        assert body["credited"] is False
        assert body.get("status") in ("pending", "completed")
        assert body["status"] == "pending"
        assert "verification" in body and isinstance(body["verification"], dict)
        v = body["verification"]
        assert "verified" in v and v["verified"] is False
        assert "status" in v and isinstance(v["status"], str)
        assert v["status"] in ("not_found", "error", "no_transfer", "wrong_recipient",
                                "amount_too_low", "config_missing", "unknown_network")
        # 'message' string
        assert isinstance(body.get("message", ""), str)

    def test_balance_unchanged(self, alice_token: str, alice_trc20_tx: str):
        before = _get_balance(alice_token)
        r = requests.patch(
            f"{BASE_URL}/api/wallet/deposit/{alice_trc20_tx}/hash",
            headers=_h(alice_token),
            json={"tx_hash": FAKE_TRC20_HASH},
            timeout=20,
        )
        assert r.status_code == 200
        after = _get_balance(alice_token)
        assert after == before, f"balance changed unexpectedly: {before} → {after}"

    def test_db_status_remains_pending(self, alice_token: str, alice_trc20_tx: str):
        requests.patch(
            f"{BASE_URL}/api/wallet/deposit/{alice_trc20_tx}/hash",
            headers=_h(alice_token),
            json={"tx_hash": FAKE_TRC20_HASH},
            timeout=20,
        )
        r = requests.get(
            f"{BASE_URL}/api/wallet/transactions?limit=50",
            headers=_h(alice_token), timeout=10,
        )
        assert r.status_code == 200
        txs = r.json() if isinstance(r.json(), list) else r.json().get("transactions", [])
        match = next((t for t in txs if t.get("tx_id") == alice_trc20_tx), None)
        assert match is not None
        assert match.get("status") == "pending"
        assert (match.get("reference") or "").lower() == FAKE_TRC20_HASH.lower()


class TestPatchHashBep20FakeHash:
    """Negative path: pending USDT BEP20 deposit + fake hash → credited=False."""

    def test_response_shape(self, alice_token: str, alice_bep20_tx: str):
        r = requests.patch(
            f"{BASE_URL}/api/wallet/deposit/{alice_bep20_tx}/hash",
            headers=_h(alice_token),
            json={"tx_hash": FAKE_BEP20_HASH},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("credited") is False
        assert body.get("status") == "pending"
        v = body.get("verification") or {}
        assert v.get("verified") is False
        # BSC RPC returns null for unknown receipts → 'not_found'
        assert v.get("status") in ("not_found", "error")


class TestPatchHashIdempotent:
    def test_two_calls_same_hash(self, alice_token: str, alice_trc20_tx: str):
        last = None
        for _ in range(2):
            r = requests.patch(
                f"{BASE_URL}/api/wallet/deposit/{alice_trc20_tx}/hash",
                headers=_h(alice_token),
                json={"tx_hash": FAKE_TRC20_HASH},
                timeout=20,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("credited") is False
            assert body.get("status") == "pending"
            last = body
        assert last is not None


class TestRegressionPatchHash:
    def test_short_hash_400(self, alice_token: str, alice_trc20_tx: str):
        r = requests.patch(
            f"{BASE_URL}/api/wallet/deposit/{alice_trc20_tx}/hash",
            headers=_h(alice_token),
            json={"tx_hash": SHORT_HASH},
            timeout=10,
        )
        assert r.status_code == 400

    def test_non_owner_404(self, alice_token: str, bob_token: str):
        tx = _create_usdt_deposit(alice_token, "usdt_trc20", 5.0)
        r = requests.patch(
            f"{BASE_URL}/api/wallet/deposit/{tx}/hash",
            headers=_h(bob_token),
            json={"tx_hash": FAKE_TRC20_HASH},
            timeout=10,
        )
        assert r.status_code == 404

    def test_unknown_tx_404(self, alice_token: str):
        r = requests.patch(
            f"{BASE_URL}/api/wallet/deposit/dep_doesnotexist_iter237ac/hash",
            headers=_h(alice_token),
            json={"tx_hash": FAKE_TRC20_HASH},
            timeout=10,
        )
        assert r.status_code == 404

    def test_non_usdt_method_400(self, alice_token: str):
        # Reuse the helper from iter248: any pending non-USDT deposit
        try:
            requests.post(
                f"{BASE_URL}/api/wallet/deposit",
                headers=_h(alice_token),
                json={"amount": 1.0, "method": "mobile_money",
                      "notes": "iter237ac-non-usdt", "phone_number": "0241234567"},
                timeout=15,
            )
        except Exception:
            pass

        r = requests.get(f"{BASE_URL}/api/wallet/transactions?limit=50",
                         headers=_h(alice_token), timeout=10)
        txs = r.json() if isinstance(r.json(), list) else r.json().get("transactions", [])
        target = next((t for t in txs
                       if t.get("type") == "deposit" and t.get("status") == "pending"
                       and "USDT" not in (t.get("notes") or "").upper()), None)
        if not target:
            pytest.skip("No non-USDT pending deposit available")
        r2 = requests.patch(
            f"{BASE_URL}/api/wallet/deposit/{target['tx_id']}/hash",
            headers=_h(alice_token),
            json={"tx_hash": FAKE_TRC20_HASH},
            timeout=10,
        )
        assert r2.status_code == 400
