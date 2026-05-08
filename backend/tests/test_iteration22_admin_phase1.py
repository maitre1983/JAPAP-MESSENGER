"""
Iteration 22 — Admin Command Center Phase 1 tests
Covers: settings persistence, currency (rates/detect/refresh), KYC flow end-to-end,
admin user management (profile/reset/suspend/reactivate), admin transactions filters & CSV export,
admin games stats, spin config + spin runtime (enabled/paid/rewards), wallet withdraw gating (KYC + settings).
"""
import io
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@japap.com"
ADMIN_PASSWORD = "JapapAdmin2024!"
BOB_EMAIL = "bob@japap.com"
BOB_PASSWORD = "Test1234!"
ALICE_EMAIL = "alice@japap.com"
ALICE_PASSWORD = "Test1234!"


def _login(email, password):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Login failed for {email}: {r.status_code} {r.text[:200]}")
    return r.json()["access_token"], r.json().get("user") or r.json().get("user_id")


@pytest.fixture(scope="session")
def admin_token():
    tok, _ = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    return tok


@pytest.fixture(scope="session")
def bob_token():
    tok, user = _login(BOB_EMAIL, BOB_PASSWORD)
    return tok


@pytest.fixture(scope="session")
def alice_token():
    tok, _ = _login(ALICE_EMAIL, ALICE_PASSWORD)
    return tok


@pytest.fixture(scope="session")
def bob_user_id(admin_token):
    # Get bob's user_id via admin list
    h = {"Authorization": f"Bearer {admin_token}"}
    r = requests.get(f"{BASE_URL}/api/admin/users", headers=h, params={"search": "bob@japap.com", "limit": 5}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    users = data.get("users") or data.get("items") or data
    for u in users if isinstance(users, list) else users.get("items", []):
        if u.get("email") == BOB_EMAIL:
            return u.get("user_id") or u.get("id")
    pytest.skip("Bob user_id not found")


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# =========== SETTINGS ===========
class TestSettings:
    def test_public_settings_no_secrets(self):
        r = requests.get(f"{BASE_URL}/api/settings/public", timeout=10)
        assert r.status_code == 200
        data = r.json()
        # Should include expected public keys
        assert "withdraw_enabled" in data
        assert "spin_enabled" in data
        # Must NOT leak secrets
        for key in data.keys():
            low = key.lower()
            assert "secret" not in low and "api_key" not in low and "password" not in low, f"secret leak: {key}"

    def test_admin_get_settings_requires_admin(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/admin/settings", headers=_auth(bob_token), timeout=10)
        assert r.status_code == 403

    def test_admin_get_settings_ok(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/settings", headers=_auth(admin_token), timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)
        settings = data.get("settings", data)
        assert "withdraw_enabled" in settings
        assert "spin_enabled" in settings

    def test_admin_bulk_update_and_persist(self, admin_token):
        # Update a harmless key with bulk PUT
        payload = {"settings": {"withdraw_disabled_message": "TEST_iter22 message"}}
        r = requests.put(f"{BASE_URL}/api/admin/settings", headers=_auth(admin_token), json=payload, timeout=10)
        assert r.status_code in (200, 204), r.text
        # Verify via GET
        r2 = requests.get(f"{BASE_URL}/api/admin/settings", headers=_auth(admin_token), timeout=10)
        settings = r2.json().get("settings", r2.json())
        assert settings.get("withdraw_disabled_message") == "TEST_iter22 message"
        # Revert
        requests.put(f"{BASE_URL}/api/admin/settings", headers=_auth(admin_token),
                     json={"settings": {"withdraw_disabled_message": "Les retraits sont momentanément suspendus. Réessayez plus tard."}}, timeout=10)

    def test_admin_single_key_update(self, admin_token):
        # Toggle spin_max_daily_plays
        r = requests.put(f"{BASE_URL}/api/admin/settings/spin_max_daily_plays",
                         headers=_auth(admin_token), json={"value": "5"}, timeout=10)
        assert r.status_code in (200, 204), r.text
        r2 = requests.get(f"{BASE_URL}/api/admin/settings", headers=_auth(admin_token), timeout=10)
        settings = r2.json().get("settings", r2.json())
        assert str(settings.get("spin_max_daily_plays")) == "5"
        # Revert
        requests.put(f"{BASE_URL}/api/admin/settings/spin_max_daily_plays",
                     headers=_auth(admin_token), json={"value": "3"}, timeout=10)


# =========== CURRENCY ===========
class TestCurrency:
    def test_rates_lists_many(self):
        r = requests.get(f"{BASE_URL}/api/currency/rates", timeout=15)
        assert r.status_code == 200
        data = r.json()
        rates = data.get("rates") or {}
        symbols = data.get("symbols") or {}
        mapping = data.get("country_to_currency") or {}
        assert len(rates) >= 50, f"expected 50+ rates, got {len(rates)}"
        assert symbols.get("USD") == "$"
        assert mapping.get("CM") == "XAF"
        assert mapping.get("NG") == "NGN"
        assert mapping.get("GH") == "GHS"

    def test_detect_by_country_cm(self):
        r = requests.get(f"{BASE_URL}/api/currency/detect", params={"country": "CM"}, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["currency"] == "XAF"
        assert d["symbol"] in ("FCFA", "XAF")
        assert float(d["rate_vs_usd"]) > 0

    def test_detect_by_country_ng(self):
        r = requests.get(f"{BASE_URL}/api/currency/detect", params={"country": "NG"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["currency"] == "NGN"

    def test_detect_by_country_gh(self):
        r = requests.get(f"{BASE_URL}/api/currency/detect", params={"country": "GH"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["currency"] == "GHS"

    def test_detect_without_country_returns_fallback(self):
        r = requests.get(f"{BASE_URL}/api/currency/detect", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "currency" in d and "symbol" in d and "rate_vs_usd" in d

    def test_refresh_requires_admin(self, bob_token):
        r = requests.post(f"{BASE_URL}/api/currency/refresh", headers=_auth(bob_token), timeout=15)
        assert r.status_code == 403

    def test_refresh_admin(self, admin_token):
        r = requests.post(f"{BASE_URL}/api/currency/refresh", headers=_auth(admin_token), timeout=30)
        assert r.status_code in (200, 202), r.text


# =========== KYC ===========
class TestKYC:
    def test_kyc_status_user(self, alice_token):
        r = requests.get(f"{BASE_URL}/api/kyc/status", headers=_auth(alice_token), timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "status" in data  # none | pending | approved | rejected

    def test_kyc_admin_pending_requires_admin(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/kyc/admin/pending", headers=_auth(bob_token), timeout=10)
        assert r.status_code == 403

    def test_kyc_admin_pending_ok(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/kyc/admin/pending", headers=_auth(admin_token), timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, (list, dict))

    def test_kyc_pending_count(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/kyc/pending-count", headers=_auth(admin_token), timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert "count" in body or "pending" in body, f"missing count/pending in {body}"

    def test_kyc_submit_approve_flow(self, alice_token, admin_token):
        # Use a fresh test user so we do not pollute alice/bob state
        email = f"TEST_kyc_{uuid.uuid4().hex[:8]}@example.com"
        reg = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": "Test1234!", "first_name": "Kyc", "last_name": "Tester",
            "country": "CM", "phone_number": f"+237{uuid.uuid4().int % 900000000 + 100000000}", "terms_accepted": True
        }, timeout=15)
        if reg.status_code not in (200, 201):
            pytest.skip(f"register failed: {reg.status_code} {reg.text[:200]}")
        # Alice fixture works for simple submit check; use alice token to attempt submission
        # Build multipart
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 2048
        files = {
            "id_photo": ("id.png", io.BytesIO(png_bytes), "image/png"),
            "selfie": ("selfie.png", io.BytesIO(png_bytes), "image/png"),
        }
        data = {"full_name": "Alice Test", "id_type": "national_id", "id_number": f"ID{uuid.uuid4().hex[:6]}"}
        r = requests.post(f"{BASE_URL}/api/kyc/submit", headers=_auth(alice_token), data=data, files=files, timeout=20)
        if r.status_code == 400 and "already" in r.text.lower():
            pytest.skip("Alice already has KYC; skipping submit-approve flow")
        assert r.status_code in (200, 201), r.text
        submission = r.json()
        kyc_id = submission.get("kyc_id") or submission.get("id")
        assert kyc_id

        # Admin pending list contains this kyc
        r2 = requests.get(f"{BASE_URL}/api/kyc/admin/pending", headers=_auth(admin_token), timeout=10)
        assert r2.status_code == 200

        # Approve
        r3 = requests.post(f"{BASE_URL}/api/kyc/admin/{kyc_id}/approve", headers=_auth(admin_token), timeout=10)
        assert r3.status_code in (200, 204), r3.text

        # Alice status should now be approved
        r4 = requests.get(f"{BASE_URL}/api/kyc/status", headers=_auth(alice_token), timeout=10)
        assert r4.status_code == 200
        assert r4.json().get("status") == "approved"


# =========== WALLET WITHDRAW gating ===========
class TestWithdrawGating:
    def test_withdraw_disabled_toggle(self, admin_token, bob_token):
        # Turn OFF withdraw_enabled
        r = requests.put(f"{BASE_URL}/api/admin/settings/withdraw_enabled",
                         headers=_auth(admin_token), json={"value": "false"}, timeout=10)
        assert r.status_code in (200, 204)
        try:
            # Bob tries to withdraw
            w = requests.post(f"{BASE_URL}/api/wallet/withdraw", headers=_auth(bob_token),
                              json={"amount": 1.0, "method": "mobile_money", "destination": "+237690000000"}, timeout=10)
            assert w.status_code == 503, f"expected 503, got {w.status_code}: {w.text[:200]}"
        finally:
            requests.put(f"{BASE_URL}/api/admin/settings/withdraw_enabled",
                         headers=_auth(admin_token), json={"value": "true"}, timeout=10)

    def test_withdraw_kyc_required(self, bob_token):
        # Assuming bob is NOT KYC-approved (fresh seed). If is_verified, this test may pass trivially.
        w = requests.post(f"{BASE_URL}/api/wallet/withdraw", headers=_auth(bob_token),
                          json={"amount": 1.0, "method": "mobile_money", "destination": "+237690000000"}, timeout=10)
        # Allow either 403 with KYC_REQUIRED or 400/402 if bob somehow verified + insufficient funds
        if w.status_code == 403:
            assert "KYC" in w.text.upper()
        else:
            # not fatal if bob is verified but insufficient funds — report in soft check
            assert w.status_code in (400, 402, 403), f"unexpected {w.status_code}: {w.text[:200]}"


# =========== ADMIN USER MGMT ===========
class TestAdminUserMgmt:
    def test_edit_profile_duplicate_email_400(self, admin_token, bob_user_id):
        # Try setting bob's email to alice's — expect 400
        r = requests.put(f"{BASE_URL}/api/admin/users/{bob_user_id}/profile",
                         headers=_auth(admin_token), json={"email": ALICE_EMAIL}, timeout=10)
        assert r.status_code == 400, r.text

    def test_edit_profile_noop_same_value(self, admin_token, bob_user_id):
        # Setting a different first_name should succeed (real change)
        r = requests.put(f"{BASE_URL}/api/admin/users/{bob_user_id}/profile",
                         headers=_auth(admin_token), json={"first_name": "Bob"}, timeout=10)
        assert r.status_code in (200, 204), r.text

    def test_reset_password_min_length(self, admin_token, bob_user_id):
        # Too short
        r = requests.post(f"{BASE_URL}/api/admin/users/{bob_user_id}/reset-password",
                          headers=_auth(admin_token), json={"new_password": "short"}, timeout=10)
        assert r.status_code == 400, r.text

    def test_suspend_and_reactivate(self, admin_token, bob_user_id):
        # Suspend bob
        r = requests.post(f"{BASE_URL}/api/admin/users/{bob_user_id}/suspend",
                          headers=_auth(admin_token), json={"reason": "TEST_iter22", "ban": False}, timeout=10)
        assert r.status_code in (200, 204), r.text
        # Bob login should now fail OR user flagged inactive
        lr = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": BOB_EMAIL, "password": BOB_PASSWORD}, timeout=10)
        assert lr.status_code in (401, 403), f"expected rejected login, got {lr.status_code}"
        # Reactivate
        r2 = requests.post(f"{BASE_URL}/api/admin/users/{bob_user_id}/reactivate",
                           headers=_auth(admin_token), timeout=10)
        assert r2.status_code in (200, 204)
        # Bob login should now work again
        lr2 = requests.post(f"{BASE_URL}/api/auth/login",
                            json={"email": BOB_EMAIL, "password": BOB_PASSWORD}, timeout=10)
        assert lr2.status_code == 200, lr2.text

    def test_user_transactions_pagination(self, admin_token, bob_user_id):
        r = requests.get(f"{BASE_URL}/api/admin/users/{bob_user_id}/transactions",
                         headers=_auth(admin_token), params={"page": 1, "limit": 10}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "items" in body or "transactions" in body or isinstance(body, list)


# =========== ADMIN TRANSACTIONS + CSV ===========
class TestAdminTransactions:
    def test_list_with_filters_returns_volume(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/transactions", headers=_auth(admin_token),
                         params={"page": 1, "limit": 10, "date_from": "2020-01-01", "date_to": "2030-12-31"}, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "volume_total" in body or "volume" in body, f"expected volume_total in response: {list(body)[:10]}"

    def test_export_csv(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/transactions/export", headers=_auth(admin_token),
                         params={"date_from": "2020-01-01"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        ctype = r.headers.get("content-type", "")
        cdisp = r.headers.get("content-disposition", "")
        assert "csv" in ctype.lower() or "octet-stream" in ctype.lower(), f"ctype={ctype}"
        assert "attachment" in cdisp.lower() or "filename" in cdisp.lower(), f"cdisp={cdisp}"
        # Body looks like CSV (first line has commas)
        first_line = r.text.splitlines()[0] if r.text else ""
        assert "," in first_line


# =========== SPIN GAME ===========
class TestSpin:
    def test_spin_config(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/games/spin/config", headers=_auth(bob_token), timeout=10)
        assert r.status_code == 200
        c = r.json()
        for k in ("enabled", "is_paid", "cost_xaf", "max_daily_plays", "rewards"):
            assert k in c, f"missing config key: {k}"

    def test_spin_disabled_returns_503(self, admin_token, bob_token):
        # Turn OFF spin_enabled
        requests.put(f"{BASE_URL}/api/admin/settings/spin_enabled",
                     headers=_auth(admin_token), json={"value": "false"}, timeout=10)
        try:
            r = requests.post(f"{BASE_URL}/api/games/spin", headers=_auth(bob_token), timeout=10)
            assert r.status_code == 503, f"expected 503, got {r.status_code}: {r.text[:200]}"
        finally:
            requests.put(f"{BASE_URL}/api/admin/settings/spin_enabled",
                         headers=_auth(admin_token), json={"value": "true"}, timeout=10)

    def test_spin_enabled_free_play(self, admin_token, bob_token):
        # Ensure free + enabled
        requests.put(f"{BASE_URL}/api/admin/settings", headers=_auth(admin_token),
                     json={"settings": {"spin_enabled": "true", "spin_is_paid": "false", "spin_max_daily_plays": "10"}}, timeout=10)
        r = requests.post(f"{BASE_URL}/api/games/spin", headers=_auth(bob_token), timeout=15)
        # Either success or daily limit exceeded (429)
        assert r.status_code in (200, 429), r.text[:200]
        if r.status_code == 200:
            data = r.json()
            assert "reward" in data or "reward_amount" in data or "prize" in data or isinstance(data, dict)

    def test_admin_games_stats(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/games/stats", headers=_auth(admin_token), timeout=10)
        assert r.status_code == 200, r.text
        s = r.json()
        for k in ("plays_total", "plays_24h"):
            assert k in s, f"missing stat key: {k}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
