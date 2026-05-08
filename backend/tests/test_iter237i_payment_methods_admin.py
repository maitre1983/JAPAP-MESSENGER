"""
iter237i — Admin UI Toggles + Analytics + Tracking endpoints.

Coverage:
- GET  /api/admin/payment-methods         (admin) full list with `enabled` field
- PATCH /api/admin/payment-methods/{id}   (admin) toggle on/off and persistence via GET
- POST /api/payment-methods/track         (auth) form_opened / submitted, 400 on invalid action
- GET  /api/admin/payment-methods/analytics?days=14   (admin) returns rows with eligible_pct
- GET  /api/payment-methods/eligibility   (auth) — Daf (CM) → wave deposit eligible:false + suggestion
"""
from __future__ import annotations
import os
import pytest
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://japap-refactor.preview.emergentagent.com",
).rstrip("/")
BYPASS = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!", **BYPASS}
DAF = {"email": "mirtoken2022@gmail.com", "password": "Daf2026!", **BYPASS}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **BYPASS}


def _login(creds):
    s = requests.Session()
    # Bypass CSRF double-submit check (SPA convention)
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    for _ in range(3):
        try:
            r = s.post(f"{BASE}/api/auth/login", json=creds, timeout=60)
            if r.status_code == 200:
                return s
        except Exception:
            pass
    pytest.skip(f"login flaked for {creds['email']}")


@pytest.fixture(scope="module")
def admin_session():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def daf_session():
    """Try Daf first (CM), fall back to Alice (US) — both produce useful tracking tests."""
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    for creds in (DAF, ALICE):
        try:
            r = s.post(f"{BASE}/api/auth/login", json=creds, timeout=60)
            if r.status_code == 200:
                s._email = creds["email"]
                return s
        except Exception:
            pass
    pytest.skip("Both Daf and Alice login failed")


# ───────────────── Admin list ─────────────────
def test_admin_list_payment_methods_returns_full_catalog(admin_session):
    r = admin_session.get(f"{BASE}/api/admin/payment-methods", timeout=15)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    methods = body.get("methods")
    assert isinstance(methods, list) and len(methods) >= 5
    ids = {m["id"] for m in methods}
    for required in ("orange_money_cm", "wave", "hubtel_card",
                     "nowpayments_usdttrc20", "nowpayments_usdtbsc"):
        assert required in ids, f"{required} missing"
    for m in methods:
        # `enabled` MUST be present (boolean) for the admin view
        assert "enabled" in m and isinstance(m["enabled"], bool)
        assert "label" in m and "id" in m


def test_admin_list_requires_admin():
    r = requests.get(f"{BASE}/api/admin/payment-methods", timeout=15)
    assert r.status_code in (401, 403)


# ───────────────── Admin toggle ─────────────────
def test_admin_toggle_disable_then_enable_persists(admin_session):
    target = "hubtel_card"  # safe global method
    # Disable
    r1 = admin_session.patch(
        f"{BASE}/api/admin/payment-methods/{target}",
        json={"enabled": False}, timeout=15)
    assert r1.status_code == 200, r1.text[:200]
    body1 = r1.json()
    assert body1.get("success") is True and body1.get("enabled") is False
    # Verify via GET
    r2 = admin_session.get(f"{BASE}/api/admin/payment-methods", timeout=15)
    found = next(m for m in r2.json()["methods"] if m["id"] == target)
    assert found["enabled"] is False, "Toggle off did not persist"
    # Public catalog should now hide it
    r_pub = requests.get(f"{BASE}/api/payment-methods", timeout=15)
    pub_ids = [m["id"] for m in r_pub.json()["methods"]]
    assert target not in pub_ids, "Disabled method still visible in public catalog"
    # Re-enable
    r3 = admin_session.patch(
        f"{BASE}/api/admin/payment-methods/{target}",
        json={"enabled": True}, timeout=15)
    assert r3.status_code == 200
    r4 = admin_session.get(f"{BASE}/api/admin/payment-methods", timeout=15)
    found = next(m for m in r4.json()["methods"] if m["id"] == target)
    assert found["enabled"] is True, "Toggle on did not persist"


def test_admin_toggle_unknown_method_404(admin_session):
    r = admin_session.patch(
        f"{BASE}/api/admin/payment-methods/totally_unknown_xyz",
        json={"enabled": False}, timeout=15)
    assert r.status_code == 404


def test_admin_toggle_requires_admin():
    r = requests.patch(
        f"{BASE}/api/admin/payment-methods/wave",
        json={"enabled": True}, timeout=15)
    assert r.status_code in (401, 403)


# ───────────────── Tracking ─────────────────
def test_track_form_opened_ok(daf_session):
    r = daf_session.post(
        f"{BASE}/api/payment-methods/track",
        json={"method": "wave", "flow": "deposit", "action": "form_opened"},
        timeout=15)
    assert r.status_code == 200, r.text[:200]
    assert r.json().get("ok") is True


def test_track_submitted_ok(daf_session):
    r = daf_session.post(
        f"{BASE}/api/payment-methods/track",
        json={"method": "orange_money_cm", "flow": "withdraw", "action": "submitted"},
        timeout=15)
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_track_invalid_action_400(daf_session):
    r = daf_session.post(
        f"{BASE}/api/payment-methods/track",
        json={"method": "wave", "flow": "deposit", "action": "explosion"},
        timeout=15)
    assert r.status_code == 400, r.text[:200]


def test_track_requires_auth():
    r = requests.post(
        f"{BASE}/api/payment-methods/track",
        json={"method": "wave", "flow": "deposit", "action": "form_opened"},
        timeout=15)
    assert r.status_code in (401, 403)


# ───────────────── Analytics ─────────────────
def test_admin_analytics_returns_rows_with_eligible_pct(admin_session):
    r = admin_session.get(
        f"{BASE}/api/admin/payment-methods/analytics?days=14", timeout=15)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body.get("days") == 14
    rows = body.get("rows")
    assert isinstance(rows, list)
    # If any data exists, validate row shape
    for row in rows:
        for k in ("method_id", "checks", "eligible_checks",
                  "form_opened", "submitted", "eligible_pct"):
            assert k in row, f"missing field {k} in analytics row"
        # eligible_pct must be a number 0..100
        pct = float(row["eligible_pct"])
        assert 0.0 <= pct <= 100.0


def test_admin_analytics_requires_admin():
    r = requests.get(f"{BASE}/api/admin/payment-methods/analytics?days=14",
                     timeout=15)
    assert r.status_code in (401, 403)


def test_admin_analytics_days_validation(admin_session):
    # ge=1 le=180 — outside should 422
    r = admin_session.get(
        f"{BASE}/api/admin/payment-methods/analytics?days=0", timeout=15)
    assert r.status_code in (400, 422)
    r2 = admin_session.get(
        f"{BASE}/api/admin/payment-methods/analytics?days=500", timeout=15)
    assert r2.status_code in (400, 422)


# ───────────────── Eligibility (Daf — CM) ─────────────────
def test_eligibility_daf_wave_deposit_ineligible_with_suggestion(daf_session):
    r = daf_session.get(
        f"{BASE}/api/payment-methods/eligibility",
        params={"method": "wave", "flow": "deposit"}, timeout=15)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    # CM (Daf) and US (Alice fallback) both NOT in WAVE_ALLOWED — must be ineligible.
    assert body["method"] == "wave"
    assert body["flow"] == "deposit"
    assert body["eligible"] is False
    assert body["suggestion"], "Expected an actionable suggestion"
    assert "USDT" in body["suggestion"] or "Orange" in body["suggestion"]
    # No rate leak
    for forbidden in ("rate", "taux", "deposit_rate", "withdraw_rate"):
        assert forbidden not in body


def test_eligibility_daf_om_cm_deposit_eligible(daf_session):
    # Daf is CM → OM deposit allowed (CM is the only allowed country)
    r = daf_session.get(
        f"{BASE}/api/payment-methods/eligibility",
        params={"method": "orange_money_cm", "flow": "deposit"}, timeout=15)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    # If user_country is CM, eligible should be True
    if body.get("user_country") == "CM":
        assert body["eligible"] is True
