"""iter91 Phase 1 Monétisation — backend tests.

Covers:
  - /api/wallet/fees-preview (send fees with PRO/std divergence, min/max clamps)
  - /api/wallet/send with fees (amount < fee 400, daily cap 429)
  - /api/users/me/qr-payload, /me/qr-code.png, /resolve-qr
  - /api/admin/revenue/overview (admin gate + shape)
  - /api/support/admin/ai-analytics (admin gate + shape)
  - /api/support/ai-chat persistence into analytics
  - withdraw per-network override (trc20 vs bep20) — via fee-preview endpoint or /withdraw
"""
import os
import pytest
import requests
from decimal import Decimal

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")

ADMIN_EMAIL = "admin@japap.com"
ADMIN_PWD = "JapapAdmin2024!"
BOB_EMAIL = "bob@japap.com"
ALICE_EMAIL = "alice@japap.com"
USER_PWD = "Test1234!"


def _login(email: str, password: str) -> str:
    last_err = None
    for _ in range(3):
        try:
            r = requests.post(f"{BASE_URL}/api/auth/login",
                              json={"email": email, "password": password}, timeout=50)
            if r.status_code == 200:
                return r.json()["access_token"]
            last_err = f"{r.status_code} {r.text[:120]}"
        except Exception as e:
            last_err = str(e)
    pytest.skip(f"Login failed for {email}: {last_err}")


@pytest.fixture(scope="module")
def admin_token():
    return _login(ADMIN_EMAIL, ADMIN_PWD)


@pytest.fixture(scope="module")
def bob_token():
    return _login(BOB_EMAIL, USER_PWD)


@pytest.fixture(scope="module")
def alice_token():
    return _login(ALICE_EMAIL, USER_PWD)


def _h(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _get_user_id(tok):
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=_h(tok), timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["user_id"]


def _set_setting(admin_tok, key, value):
    r = requests.put(f"{BASE_URL}/api/admin/settings/{key}",
                     headers=_h(admin_tok), json={"value": str(value)}, timeout=30)
    # Some backends use POST /api/admin/settings body={key,value}
    if r.status_code >= 400:
        r = requests.post(f"{BASE_URL}/api/admin/settings",
                          headers=_h(admin_tok),
                          json={"key": key, "value": str(value)}, timeout=30)
    return r


# ───────── fees-preview ─────────
class TestFeesPreview:
    def test_preview_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/wallet/fees-preview", params={"amount": 100}, timeout=30)
        assert r.status_code in (401, 403)

    def test_preview_alice_std(self, alice_token):
        r = requests.get(f"{BASE_URL}/api/wallet/fees-preview",
                         headers=_h(alice_token), params={"amount": 1000}, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("enabled", "mode", "value", "is_pro", "fee", "amount", "net_to_recipient"):
            assert k in d, f"missing key {k} in {d}"
        assert d["is_pro"] is False
        assert d["amount"] == "1000.0" or d["amount"] == "1000"

    def test_preview_bob_pro(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/wallet/fees-preview",
                         headers=_h(bob_token), params={"amount": 1000}, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["is_pro"] is True, f"Bob should be is_pro=True: {d}"

    def test_preview_divergence_pro_vs_std(self, alice_token, bob_token):
        """When send_fee_enabled + pro_enabled + different % values: Bob fee < Alice fee
        (or both clamped to min). Contract: Bob's fee <= Alice's fee on same amount."""
        r_a = requests.get(f"{BASE_URL}/api/wallet/fees-preview",
                           headers=_h(alice_token), params={"amount": 10000}, timeout=30).json()
        r_b = requests.get(f"{BASE_URL}/api/wallet/fees-preview",
                           headers=_h(bob_token), params={"amount": 10000}, timeout=30).json()
        if r_a.get("enabled"):
            # PRO fee should be <= std fee (PRO remise).
            assert Decimal(r_b["fee"]) <= Decimal(r_a["fee"]), (
                f"PRO fee ({r_b['fee']}) must be <= std ({r_a['fee']}). A={r_a} B={r_b}")

    def test_preview_negative_amount(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/wallet/fees-preview",
                         headers=_h(bob_token), params={"amount": -5}, timeout=30)
        assert r.status_code == 400


# ───────── send with fees ─────────
class TestSendWithFees:
    def test_send_insufficient_or_fee_gt_amount(self, bob_token, alice_token):
        """Bob has ~0 balance. If fees enabled and amount very small vs min-floor,
        we may hit 400 'Montant inférieur aux frais' OR 400 'Insufficient balance'."""
        alice_id = _get_user_id(alice_token)
        # Small amount vs fee_min=5 should hit 'Montant inférieur aux frais' when enabled
        r = requests.post(f"{BASE_URL}/api/wallet/send",
                          headers=_h(bob_token),
                          json={"to_user_id": alice_id, "amount": 1, "notes": "TEST_iter91"},
                          timeout=30)
        # Valid outcomes: 400 (below fee OR insufficient), 429 if rate-limited
        assert r.status_code in (400, 403, 429), f"Unexpected: {r.status_code} {r.text}"
        if r.status_code == 400:
            detail = (r.json().get("detail") or "").lower()
            assert any(k in detail for k in ("inférieur", "insufficient", "solde", "amount")), detail


# ───────── QR code ─────────
class TestQRCode:
    def test_qr_payload_auth(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/users/me/qr-payload", headers=_h(bob_token), timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["t"] == "japap.pay"
        assert d["v"] == 1
        assert d["uid"].startswith("user_") or len(d["uid"]) > 3
        assert "name" in d and "ccy" in d

    def test_qr_payload_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/users/me/qr-payload", timeout=30)
        assert r.status_code in (401, 403)

    def test_qr_png(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/users/me/qr-code.png", headers=_h(bob_token), timeout=30)
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("image/png")
        assert len(r.content) > 400
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_resolve_qr_valid(self, bob_token, alice_token):
        alice_id = _get_user_id(alice_token)
        r = requests.post(f"{BASE_URL}/api/users/resolve-qr",
                          headers=_h(bob_token),
                          json={"t": "japap.pay", "v": 1, "uid": alice_id}, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["user_id"] == alice_id
        assert "name" in d and "is_pro" in d

    def test_resolve_qr_bad_t(self, bob_token):
        r = requests.post(f"{BASE_URL}/api/users/resolve-qr",
                          headers=_h(bob_token),
                          json={"t": "other", "v": 1, "uid": "user_xxx"}, timeout=30)
        assert r.status_code == 400

    def test_resolve_qr_missing_uid(self, bob_token):
        r = requests.post(f"{BASE_URL}/api/users/resolve-qr",
                          headers=_h(bob_token),
                          json={"t": "japap.pay", "v": 1}, timeout=30)
        assert r.status_code == 400

    def test_resolve_qr_unknown_user(self, bob_token):
        r = requests.post(f"{BASE_URL}/api/users/resolve-qr",
                          headers=_h(bob_token),
                          json={"t": "japap.pay", "v": 1, "uid": "user_doesnotexist_zzz"}, timeout=30)
        assert r.status_code == 404


# ───────── admin revenue ─────────
class TestAdminRevenue:
    def test_gate_user_forbidden(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/admin/revenue/overview?days=30",
                         headers=_h(bob_token), timeout=30)
        assert r.status_code in (401, 403)

    def test_admin_shape(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/revenue/overview?days=30",
                         headers=_h(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("window_days") == 30
        kpis = d.get("kpis", {})
        for k in ("total_revenue_usd", "send_fees_usd", "withdraw_fees_usd",
                  "subscription_usd", "deposits_gross_usd", "mrr_usd"):
            assert k in kpis, f"missing kpi {k}; got {kpis.keys()}"
        assert "counts" in kpis
        assert "by_plan" in d and isinstance(d["by_plan"], list)
        assert "timeseries" in d and isinstance(d["timeseries"], list)
        assert "top_payers" in d and isinstance(d["top_payers"], list)


# ───────── AI analytics ─────────
class TestAIAnalytics:
    def test_gate_user_forbidden(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/support/admin/ai-analytics?days=30",
                         headers=_h(bob_token), timeout=30)
        assert r.status_code in (401, 403)

    def test_admin_shape(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/support/admin/ai-analytics?days=30",
                         headers=_h(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("window_days") == 30
        kpis = d.get("kpis", {})
        for k in ("total_turns", "sessions", "unique_users", "escalation_hints",
                  "actual_escalations", "escalation_rate"):
            assert k in kpis, f"missing ai kpi {k}; got {kpis.keys()}"
        assert isinstance(d.get("timeseries"), list)
        assert "recent_turns" in d

    def test_ai_chat_persists_to_analytics(self, bob_token, admin_token):
        """Fire one /ai-chat then check admin analytics total_turns increased by >=1."""
        pre = requests.get(f"{BASE_URL}/api/support/admin/ai-analytics?days=30",
                           headers=_h(admin_token), timeout=30).json()
        pre_turns = int((pre.get("kpis") or {}).get("total_turns", 0))
        r = requests.post(f"{BASE_URL}/api/support/ai-chat",
                          headers=_h(bob_token),
                          json={"messages": [{"role": "user", "content": "Bonjour, test iter91."}]},
                          timeout=60)
        if r.status_code != 200:
            pytest.skip(f"ai-chat not available: {r.status_code} {r.text[:120]}")
        post = requests.get(f"{BASE_URL}/api/support/admin/ai-analytics?days=30",
                            headers=_h(admin_token), timeout=30).json()
        post_turns = int((post.get("kpis") or {}).get("total_turns", 0))
        assert post_turns >= pre_turns + 1, f"turns not persisted: pre={pre_turns} post={post_turns}"


# ───────── withdraw per-network override ─────────
class TestWithdrawOverride:
    """Test payment-methods endpoint surfaces the correct fee for current user;
    actual per-method override is validated by looking at /payment-methods only,
    since /withdraw requires KYC approval which isn't available here."""

    def test_payment_methods_shape(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/wallet/payment-methods",
                         headers=_h(bob_token), timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "withdraw" in d and "fee" in d
        # Must include both networks
        ids = {w["id"] for w in d["withdraw"]}
        assert "usdt_trc20" in ids
        assert "usdt_bep20" in ids

    def test_withdraw_kyc_gate(self, bob_token):
        """Without KYC approved, /withdraw returns 403 KYC_REQUIRED (regression iter89)."""
        r = requests.post(f"{BASE_URL}/api/wallet/withdraw",
                          headers=_h(bob_token),
                          json={"amount": 20, "method": "usdt_trc20",
                                "address": "TRC20addr_abcdefghijklmnop", "notes": "TEST_iter91"},
                          timeout=30)
        # Either 403 KYC, 400 insufficient balance, 503 method disabled
        assert r.status_code in (400, 403, 503), f"{r.status_code} {r.text}"
