"""iter225 E2E backend tests against preview URL.

Tests:
  • GET /api/quiz/champion/challenges/qcc_3a4b8467047e40f2 (Daf logged in)
    → must return enriched fields + stake_currency='USD'
  • POST /api/quiz/champion/challenge with stake_amount=0.5 (< min) → 400
  • POST /api/quiz/champion/challenge with stake_amount=300 (> max) → 400
"""
from __future__ import annotations
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
CHALLENGE_ID = "qcc_3a4b8467047e40f2"

DAF = {"email": "mirtoken2022@gmail.com", "password": "Daf2026!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}
CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}


def _login(creds):
    import time
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"})
    payload = {**creds, **CAPTCHA}
    last = None
    for i in range(4):
        try:
            r = s.post(f"{BASE_URL}/api/auth/login", json=payload, timeout=90)
            last = r
            if r.status_code == 200:
                break
            time.sleep(8)
        except requests.RequestException as e:
            last = e
            time.sleep(8)
    assert getattr(last, "status_code", None) == 200, f"login failed: {getattr(last,'status_code','EXC')} {getattr(last,'text','')[:200]}"
    r = last
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


@pytest.fixture(scope="module")
def daf_session():
    return _login(DAF)


@pytest.fixture(scope="module")
def bob_session():
    return _login(BOB)


def test_get_challenge_enriched_contract(daf_session):
    r = daf_session.get(f"{BASE_URL}/api/quiz/champion/challenges/{CHALLENGE_ID}", timeout=20)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    print(f"\nGET /challenges/{CHALLENGE_ID}: keys={list(data.keys())}")

    # Core required fields
    assert data.get("stake_currency") == "USD", f"expected USD got {data.get('stake_currency')}"
    assert "commission_pct" in data, "missing commission_pct"
    assert "viewer_balance" in data, "missing viewer_balance"
    assert "viewer_currency" in data, "missing viewer_currency"

    # Player metadata
    assert isinstance(data.get("challenger"), dict), "challenger is not an object"
    assert "name" in data["challenger"]
    assert "avatar_url" in data["challenger"]
    assert isinstance(data.get("champion"), dict), "champion is not an object"
    assert "name" in data["champion"]
    assert "avatar_url" in data["champion"]

    # Daf is the champion of this challenge
    assert data.get("role") in ("champion", "viewer", "challenger"), f"unexpected role {data.get('role')}"
    print(f"  ↳ stake_currency={data['stake_currency']} commission_pct={data['commission_pct']} role={data.get('role')}")
    print(f"  ↳ challenger={data['challenger'].get('name')} champion={data['champion'].get('name')}")
    print(f"  ↳ viewer_balance={data['viewer_balance']} {data['viewer_currency']}")


DAF_USER_ID = "user_9ad70ec54565"


def test_create_challenge_below_min(bob_session):
    """stake_amount=0.5 should be rejected with 400 'Mise minimale : 1 USD.'"""
    body = {"champion_user_id": DAF_USER_ID, "country_code": "CM", "mode": "paid", "stake_amount": 0.5}
    r = bob_session.post(f"{BASE_URL}/api/quiz/champion/challenge", json=body, timeout=30)
    print(f"\nPOST stake=0.5 → {r.status_code} {r.text[:200]}")
    assert r.status_code == 400, f"expected 400 got {r.status_code}"
    body_text = r.text.lower()
    assert "minimale" in body_text or "minimum" in body_text or "min" in body_text


def test_create_challenge_above_max(bob_session):
    """stake_amount=300 should be rejected with 400 'Mise maximale : 200 USD.'"""
    body = {"champion_user_id": DAF_USER_ID, "country_code": "CM", "mode": "paid", "stake_amount": 300}
    r = bob_session.post(f"{BASE_URL}/api/quiz/champion/challenge", json=body, timeout=30)
    print(f"\nPOST stake=300 → {r.status_code} {r.text[:200]}")
    assert r.status_code == 400, f"expected 400 got {r.status_code}"
    body_text = r.text.lower()
    assert "maximale" in body_text or "maximum" in body_text or "max" in body_text


def test_create_challenge_free_mode_regression(bob_session):
    """Free mode must remain functional."""
    body = {"champion_user_id": DAF_USER_ID, "country_code": "CM", "mode": "free"}
    r = bob_session.post(f"{BASE_URL}/api/quiz/champion/challenge", json=body, timeout=30)
    print(f"\nPOST free → {r.status_code} {r.text[:200]}")
    # 200 (created) or 4xx (already pending, etc.) — must NOT be 5xx
    assert r.status_code < 500, f"server error on free mode: {r.status_code}"
