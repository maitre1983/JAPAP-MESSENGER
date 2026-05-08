"""iter160 — Wallet canonical USD + auto-deposits regression test suite.

Covers the P0 refactor (iter158-159):
  • /wallet/balance returns balance_usd + display_amount + display_currency
  • /wallet/display-currency accepts/rejects properly
  • /wallet/deposit/conversion-preview returns provider_amount/exchange_rate
    correctly for hubtel_card and usdt_trc20
  • /wallet/deposit persists amount_usd / provider / display_currency / display_amount
  • Manual admin deposit approval / rejection are DISABLED (410)
  • DB sanity: all wallets are stored in USD
  • Worker has 24h auto-expire logic for pending deposits
"""
import os
import asyncio
import pytest
import requests
import asyncpg
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]
BYPASS = "JAPAP_E2E_BYPASS_2026"

USER_EMAIL = "bob@japap.com"
USER_PASSWORD = "Test1234!"
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"


# ---------- Fixtures ----------

@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def user_token(session):
    r = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": USER_EMAIL, "password": USER_PASSWORD,
        "captcha_id": BYPASS, "captcha_answer": "0",
    })
    if r.status_code != 200:
        pytest.skip(f"User login failed: {r.status_code} {r.text[:200]}")
    return r.json().get("access_token") or r.json().get("token")


@pytest.fixture(scope="module")
def admin_token(session):
    r = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
        "captcha_id": BYPASS, "captcha_answer": "0",
    })
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    return r.json().get("access_token") or r.json().get("token")


@pytest.fixture
def auth_h(user_token):
    return {"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"}


@pytest.fixture
def admin_h(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# ---------- /wallet/balance ----------

class TestBalance:
    def test_balance_returns_usd_canonical_fields(self, auth_h):
        r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=auth_h)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("balance_usd", "currency", "display_amount", "display_currency",
                  "balance", "is_locked"):
            assert k in d, f"Missing key {k} in balance response: {d}"
        assert d["currency"] == "USD"
        # legacy `balance` must mirror balance_usd
        assert d["balance"] == d["balance_usd"]
        # numeric strings
        float(d["balance_usd"])
        float(d["display_amount"])


# ---------- /wallet/display-currency ----------

class TestDisplayCurrency:
    @pytest.mark.parametrize("code", ["USD", "LOCAL", "XAF", "GHS", "EUR"])
    def test_accepts_valid(self, auth_h, code):
        r = requests.post(f"{BASE_URL}/api/wallet/display-currency",
                          headers=auth_h, json={"display_currency": code})
        assert r.status_code == 200, f"{code}: {r.text}"
        d = r.json()
        assert "display_currency" in d
        if code == "LOCAL":
            assert d["display_currency"] == "local"
        else:
            assert d["display_currency"] == code

    @pytest.mark.parametrize("code", ["123", "us", "USDT", "abcd", "$$"])
    def test_rejects_invalid(self, auth_h, code):
        r = requests.post(f"{BASE_URL}/api/wallet/display-currency",
                          headers=auth_h, json={"display_currency": code})
        assert r.status_code == 400, f"{code} should be rejected, got {r.status_code} {r.text}"


# ---------- /wallet/deposit/conversion-preview ----------

class TestConversionPreview:
    def test_hubtel_card_usd_to_ghs(self, auth_h):
        r = requests.get(
            f"{BASE_URL}/api/wallet/deposit/conversion-preview",
            params={"amount": 10, "method": "hubtel_card"},
            headers=auth_h,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["provider"] == "hubtel"
        assert d["provider_currency"] == "GHS"
        # 10 USD must convert to >10 GHS at any sane rate
        assert float(d["provider_amount"]) > 10
        assert float(d["exchange_rate"]) > 1
        # Reasonable sanity range for GHS rate (5..20 since 2024-26)
        assert 5 < float(d["exchange_rate"]) < 30

    def test_usdt_trc20_one_to_one(self, auth_h):
        r = requests.get(
            f"{BASE_URL}/api/wallet/deposit/conversion-preview",
            params={"amount": 10, "method": "usdt_trc20"},
            headers=auth_h,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["provider"] == "nowpayments"
        assert d["provider_currency"] == "USDT"
        assert d["exchange_rate"] == "1.0000"

    @pytest.mark.parametrize("amount", [-1, 0, 99999, 10001])
    def test_invalid_amount_rejected(self, auth_h, amount):
        r = requests.get(
            f"{BASE_URL}/api/wallet/deposit/conversion-preview",
            params={"amount": amount, "method": "hubtel_card"},
            headers=auth_h,
        )
        assert r.status_code == 400, f"amount={amount}: got {r.status_code} {r.text}"


# ---------- /wallet/deposit (creation) ----------

class TestDepositCreation:
    def test_usdt_deposit_persists_canonical_fields(self, auth_h):
        r = requests.post(
            f"{BASE_URL}/api/wallet/deposit",
            headers=auth_h,
            json={"amount": 10, "method": "usdt_trc20", "notes": "TEST_iter160", "reference": ""},
        )
        if r.status_code == 503 and "désactivée" in r.text:
            pytest.skip("usdt_trc20 disabled in admin settings — skip canonical-fields check")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "pending"
        assert d["method"] == "usdt_trc20"
        assert d["amount_usd"] in ("10", "10.00", "10.0", "10.000000")
        tx_id = d["tx_id"]

        # DB sanity check
        async def _check():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT amount_usd, provider, currency, display_currency, "
                    "display_amount, status, type FROM transactions WHERE tx_id=$1",
                    tx_id,
                )
                return dict(row) if row else None
            finally:
                await conn.close()

        row = asyncio.get_event_loop().run_until_complete(_check())
        assert row is not None, f"tx {tx_id} not found in DB"
        assert row["type"] == "deposit"
        assert row["status"] == "pending"
        assert row["currency"] == "USD"
        assert row["display_currency"] == "USD"
        assert Decimal(row["amount_usd"]) == Decimal("10")
        assert Decimal(row["display_amount"]) == Decimal("10")
        assert row["provider"] == "nowpayments"

    def test_hubtel_deposit_persists_provider_conversion(self, auth_h):
        r = requests.post(
            f"{BASE_URL}/api/wallet/deposit",
            headers=auth_h,
            json={"amount": 5, "method": "hubtel_card", "notes": "TEST_iter160_hubtel", "reference": ""},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "pending"
        assert d["method"] == "hubtel_card"
        assert "checkout_url" in d
        # provider_amount/exchange_rate may be either real Hubtel or stub
        tx_id = d["tx_id"]

        async def _check():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT amount_usd, provider, currency, provider_currency, "
                    "provider_amount, exchange_rate, status FROM transactions "
                    "WHERE tx_id=$1",
                    tx_id,
                )
                return dict(row) if row else None
            finally:
                await conn.close()

        row = asyncio.get_event_loop().run_until_complete(_check())
        assert row is not None, f"tx {tx_id} not found"
        assert row["status"] == "pending"
        assert row["currency"] == "USD"
        assert row["provider"] == "hubtel"
        assert Decimal(row["amount_usd"]) == Decimal("5")
        # If the Hubtel call really happened, provider_currency must be GHS
        # and provider_amount > 0. If the stub kicked in (no creds), fields
        # may be NULL — that's acceptable for the dev environment.
        if row["provider_currency"]:
            assert row["provider_currency"] == "GHS"
            assert float(row["provider_amount"]) > 0
            assert float(row["exchange_rate"]) > 0

    def test_deposit_status_endpoint(self, auth_h):
        # Create then query status (uses hubtel_card since usdt may be disabled)
        r = requests.post(
            f"{BASE_URL}/api/wallet/deposit",
            headers=auth_h,
            json={"amount": 12, "method": "hubtel_card", "notes": "TEST_iter160_status", "reference": ""},
        )
        assert r.status_code == 200, r.text
        tx_id = r.json()["tx_id"]
        s = requests.get(f"{BASE_URL}/api/wallet/deposit/{tx_id}/status", headers=auth_h)
        assert s.status_code == 200, s.text
        body = s.json()
        # Endpoint returns tx_status (legacy) + payment_status (live probe).
        assert body.get("tx_status") == "pending"
        assert body.get("is_paid") is False


# ---------- Admin manual approval is DISABLED ----------

class TestAdminManualApprovalDisabled:
    def _create_pending(self, auth_h):
        r = requests.post(
            f"{BASE_URL}/api/wallet/deposit",
            headers=auth_h,
            json={"amount": 7, "method": "hubtel_card", "notes": "TEST_iter160_admin", "reference": ""},
        )
        assert r.status_code == 200, r.text
        return r.json()["tx_id"]

    def test_admin_approve_returns_410(self, auth_h, admin_h):
        tx_id = self._create_pending(auth_h)
        r = requests.post(
            f"{BASE_URL}/api/admin/wallet/deposits/{tx_id}/approve",
            headers=admin_h, json={"reason": "test"},
        )
        # The endpoint MUST not credit anything. 410/404/403 all acceptable.
        assert r.status_code in (404, 410, 403, 405), (
            f"Manual approval still possible! status={r.status_code} body={r.text[:300]}"
        )
        if r.status_code == 410:
            assert "MANUAL_DEPOSIT_APPROVAL_DISABLED" in r.text

    def test_admin_reject_returns_410(self, auth_h, admin_h):
        tx_id = self._create_pending(auth_h)
        r = requests.post(
            f"{BASE_URL}/api/admin/wallet/deposits/{tx_id}/reject",
            headers=admin_h, json={"reason": "test"},
        )
        assert r.status_code in (404, 410, 403, 405), (
            f"Manual rejection still possible! status={r.status_code} body={r.text[:300]}"
        )

    def test_admin_validate_endpoint_does_not_exist(self, admin_h):
        # Generic catch — any old "validate" route shape must be absent
        for path in (
            "/api/admin/deposits/validate",
            "/api/admin/wallet/deposits/validate",
            "/api/admin/wallet/deposit/validate",
        ):
            r = requests.post(f"{BASE_URL}{path}", headers=admin_h, json={"tx_id": "fake"})
            assert r.status_code in (404, 405), (
                f"{path} should not exist but returned {r.status_code}"
            )


# ---------- DB sanity ----------

class TestDbSanity:
    def test_all_wallets_currency_usd(self):
        async def _check():
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch("SELECT DISTINCT currency FROM wallets")
                return [r["currency"] for r in rows]
            finally:
                await conn.close()

        currencies = asyncio.get_event_loop().run_until_complete(_check())
        assert currencies == ["USD"], f"Expected only USD, got {currencies}"

    def test_worker_has_24h_ttl_logic(self):
        # Static check on the worker source — the test_finish requires the
        # logic to exist. We don't actually run the worker here.
        path = "/app/backend/services/payment_verify_retry_worker.py"
        with open(path) as f:
            src = f.read()
        assert "DEPOSIT_TTL_HOURS" in src
        assert "_sweep_expired_deposits" in src
        assert "INTERVAL" in src and "hours" in src
        assert "status = 'expired'" in src or "'expired'" in src


# ---------- /wallet/transactions ----------

class TestTransactionsListing:
    def test_transactions_returns_amount_usd_and_display_amount(self, auth_h):
        # Ensure at least one tx exists
        requests.post(
            f"{BASE_URL}/api/wallet/deposit", headers=auth_h,
            json={"amount": 3, "method": "usdt_trc20",
                  "notes": "TEST_iter160_list", "reference": ""},
        )
        r = requests.get(f"{BASE_URL}/api/wallet/transactions?limit=5", headers=auth_h)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "transactions" in d
        assert len(d["transactions"]) > 0
        tx = d["transactions"][0]
        # amount_usd must always be present on rows created post-iter158
        for k in ("amount", "amount_usd", "display_view"):
            assert k in tx, f"Missing key {k} in tx: {tx.keys()}"


# ---------- Cleanup ----------

@pytest.fixture(scope="module", autouse=True)
def _cleanup_test_deposits():
    yield

    async def _purge():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(
                "DELETE FROM transactions WHERE notes LIKE '%TEST_iter160%' "
                "AND status = 'pending'"
            )
        finally:
            await conn.close()

    try:
        asyncio.get_event_loop().run_until_complete(_purge())
    except Exception:
        pass
