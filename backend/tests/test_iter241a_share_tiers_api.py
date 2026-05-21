"""iter241a-share-tiers — API integration tests.

Validates new endpoints/fields added by the Power-Sharer tier feature:
  * GET  /api/forecast/settings/public  → new power_sharer_* fields
  * GET  /api/forecast/my-tier          → tier shape (auth)
  * GET  /api/forecast/my-referrals     → tier fields surfaced
  * GET  /api/users/profile/{user}      → is_forecast_influencer
  * PUT  /api/admin/forecast/settings   → admin updates threshold/% /window
  * Threshold = -1 flips any user → power_sharer (revert to 10 after)
"""

import os
import time
import pytest
import requests

BASE_URL = os.environ.get("TEST_BASE_URL") or os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8001").rstrip("/")
BYPASS = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
TIMEOUT = 60


def _get(url, **kw):
    last = None
    for _ in range(3):
        try:
            return requests.get(url, timeout=TIMEOUT, **kw)
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(2)
    raise last


def _put(url, **kw):
    last = None
    for _ in range(3):
        try:
            return requests.put(url, timeout=TIMEOUT, **kw)
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(2)
    raise last


def _post(url, **kw):
    last = None
    for _ in range(3):
        try:
            return requests.post(url, timeout=TIMEOUT, **kw)
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(2)
    raise last


# ---------- auth helpers ----------
def _login(email: str, pwd: str) -> str:
    r = _post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": pwd, **BYPASS},
        
    )
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:200]}"
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    assert tok, f"no token in login resp: {body}"
    return tok


@pytest.fixture(scope="session")
def bob_token():
    return _login("bob@japap.com", "Test1234!")


@pytest.fixture(scope="session")
def admin_token():
    return _login("admin@japap.com", "JapapAdmin2024!")


# ---------- public settings ----------
def test_forecast_public_settings_exposes_power_sharer_fields():
    r = _get(f"{BASE_URL}/api/forecast/settings/public")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "power_sharer_threshold" in data
    assert "power_sharer_commission_percent" in data
    assert "power_sharer_window_days" in data
    assert float(data["power_sharer_threshold"]) == 10
    assert float(data["power_sharer_commission_percent"]) == 15.0
    assert float(data["power_sharer_window_days"]) == 30


# ---------- my-tier shape ----------
def test_my_tier_shape_standard_default(bob_token):
    r = _get(
        f"{BASE_URL}/api/forecast/my-tier",
        headers={"Authorization": f"Bearer {bob_token}"},
        
    )
    assert r.status_code == 200, r.text
    d = r.json()
    for k in [
        "tier",
        "commission_percent",
        "standard_percent",
        "boosted_percent",
        "winning_referrals_window",
        "threshold",
        "window_days",
        "is_forecast_influencer",
    ]:
        assert k in d, f"missing key {k} in /my-tier: {d}"
    assert d["tier"] in ("standard", "power_sharer")
    # default state: bob has no winning refs → standard
    assert d["tier"] == "standard"
    assert float(d["commission_percent"]) == float(d["standard_percent"])
    assert float(d["standard_percent"]) == 10.0
    assert float(d["boosted_percent"]) == 15.0
    assert d["is_forecast_influencer"] is False


# ---------- my-referrals shape ----------
def test_my_referrals_includes_tier_fields(bob_token):
    r = _get(
        f"{BASE_URL}/api/forecast/my-referrals",
        headers={"Authorization": f"Bearer {bob_token}"},
        
    )
    assert r.status_code == 200, r.text
    d = r.json()
    for k in [
        "tier",
        "standard_percent",
        "boosted_percent",
        "threshold",
        "window_days",
        "winning_referrals_window",
        "is_forecast_influencer",
    ]:
        assert k in d, f"missing {k} in /my-referrals: list(keys)={list(d)}"


# ---------- public profile flag ----------
def test_public_profile_has_is_forecast_influencer():
    # admin@japap.com is a known seeded user
    r = _get(f"{BASE_URL}/api/users/profile/admin@japap.com")
    # accept either email-lookup or fallback to user_id lookup using bob's known id
    if r.status_code != 200:
        r = _get(f"{BASE_URL}/api/users/profile/user_a1b203440a53")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "is_forecast_influencer" in d, f"flag missing: keys={list(d)}"
    assert isinstance(d["is_forecast_influencer"], bool)


# ---------- admin update flips tier ----------
def test_admin_threshold_flip_to_power_sharer(admin_token, bob_token):
    # 1) set threshold to -1
    r = _put(
        f"{BASE_URL}/api/admin/forecast/settings",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "power_sharer_threshold": -1,
            "power_sharer_commission_percent": 15.0,
            "power_sharer_window_days": 30,
        },
        
    )
    assert r.status_code == 200, r.text

    try:
        # 2) bob's tier should now be power_sharer
        r2 = _get(
            f"{BASE_URL}/api/forecast/my-tier",
            headers={"Authorization": f"Bearer {bob_token}"},
            
        )
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["tier"] == "power_sharer", f"expected power_sharer, got {d2}"
        assert float(d2["commission_percent"]) == 15.0
    finally:
        # 3) ALWAYS revert
        r3 = _put(
            f"{BASE_URL}/api/admin/forecast/settings",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "power_sharer_threshold": 10,
                "power_sharer_commission_percent": 15.0,
                "power_sharer_window_days": 30,
            },
            
        )
        assert r3.status_code == 200, f"REVERT FAILED: {r3.text}"

    # 4) verify revert
    r4 = _get(
        f"{BASE_URL}/api/forecast/my-tier",
        headers={"Authorization": f"Bearer {bob_token}"},
        
    )
    assert r4.status_code == 200
    assert r4.json()["tier"] == "standard"


# ---------- regression: existing forecast endpoints still work ----------
def test_forecast_markets_list_still_works():
    r = _get(f"{BASE_URL}/api/forecast/markets")
    assert r.status_code == 200, r.text
    # response should be a list or {markets:[]}
    body = r.json()
    assert isinstance(body, (list, dict))


def test_forecast_my_bets_still_works(bob_token):
    r = _get(
        f"{BASE_URL}/api/forecast/my-bets",
        headers={"Authorization": f"Bearer {bob_token}"},
        
    )
    assert r.status_code == 200, r.text
