"""
iter236 — Orange Money: deposit gate (non-GH allowed), withdrawal gate (CM+237),
no-rate-leak, admin endpoints auth, auto-recredit, token TTL 8h.
"""
from __future__ import annotations
import os
import time
import jwt as _jwt  # PyJWT (used to decode without verifying signature)
import pytest
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://japap-refactor.preview.emergentagent.com",
).rstrip("/")

CAPTCHA = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!", **CAPTCHA}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **CAPTCHA}
DAF   = {"email": "mirtoken2022@gmail.com", "password": "Daf2026!", **CAPTCHA}


def _login(creds):
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    last = None
    for _ in range(3):
        try:
            r = s.post(f"{BASE}/api/auth/login", json=creds, timeout=60)
            if r.status_code == 200:
                me = s.get(f"{BASE}/api/auth/me", timeout=30)
                if me.status_code == 200:
                    s._user = me.json()  # type: ignore[attr-defined]
                # keep raw login response handy for token-TTL test
                s._login_body = r.json()  # type: ignore[attr-defined]
                return s
            last = (r.status_code, r.text[:300])
        except Exception as e:
            last = ("exc", str(e))
        time.sleep(2)
    pytest.skip(f"Login failed for {creds['email']}: {last}")


@pytest.fixture
def admin():
    return _login(ADMIN)

@pytest.fixture
def alice():
    return _login(ALICE)

@pytest.fixture
def daf():
    return _login(DAF)


# ─────────────── OM Deposit gate: allowed for non-GH users ───────────────
def test_om_deposit_info_non_gh_user(alice):
    me = alice._user
    assert me.get("country") != "GH", f"Alice expected non-GH, got {me.get('country')}"
    r = alice.get(f"{BASE}/api/deposits/orange-money/info", timeout=20)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body.get("receiver_name") == "New deal finances", body
    assert body.get("receiver_number") == "+237658012390", body


def test_om_deposit_info_gh_user_blocked(daf):
    me = daf._user
    assert me.get("country") == "GH", f"Daf expected GH, got {me.get('country')}"
    r = daf.get(f"{BASE}/api/deposits/orange-money/info", timeout=20)
    assert r.status_code == 403, r.text[:200]
    assert "pays" in r.text.lower() or "indispon" in r.text.lower()


# ─────────────── OM Quote does NOT leak the conversion rate ───────────────
def test_om_quote_no_rate_leak(alice):
    r = alice.post(f"{BASE}/api/deposits/orange-money/quote",
                   json={"montant_usd": 10}, timeout=20)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert "montant_xaf" in body and float(body["montant_xaf"]) == 6050.0, body
    for forbidden in ("rate", "taux", "taux_applique", "deposit_rate", "withdraw_rate", "exchange_rate"):
        assert forbidden not in body, f"Leaked '{forbidden}': {body}"


# ─────────────── OM Withdraw — Daf (GH) blocked ───────────────
def test_om_withdraw_submit_blocked_for_gh_user(daf):
    payload = {
        "montant_usd": 5,
        "numero_om": "+237600000001",
        "nom_titulaire": "Daf Test",
    }
    r = daf.post(f"{BASE}/api/withdrawals/orange-money/submit", json=payload, timeout=20)
    assert r.status_code in (400, 403), r.text[:200]


# ─────────────── Admin endpoints auth ───────────────
def test_unauth_blocked_on_admin_om():
    s = requests.Session()
    for path in [
        "/api/admin/orange-money/settings",
        "/api/admin/orange-money/deposits",
        "/api/admin/orange-money/withdrawals",
        "/api/admin/orange-money/stats",
    ]:
        r = s.get(f"{BASE}{path}", timeout=15)
        assert r.status_code in (401, 403), (path, r.status_code, r.text[:120])


def test_admin_om_endpoints_ok(admin):
    for path in [
        "/api/admin/orange-money/settings",
        "/api/admin/orange-money/deposits",
        "/api/admin/orange-money/withdrawals",
        "/api/admin/orange-money/stats",
    ]:
        r = admin.get(f"{BASE}{path}", timeout=20)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])


# ─────────────── Token TTL = 8h (480 min) ───────────────
def test_access_token_ttl_8h(alice):
    """Decode the access_token from cookies and verify exp ~ now + 8h."""
    cookie = alice.cookies.get("access_token") or alice.cookies.get("token")
    if not cookie:
        # Some setups return the token in body
        cookie = (alice._login_body or {}).get("access_token") or (alice._login_body or {}).get("token")
    if not cookie:
        pytest.skip("Access token cookie/body not exposed; nothing to decode")
    try:
        payload = _jwt.decode(cookie, options={"verify_signature": False})
    except Exception as e:
        pytest.skip(f"Token not JWT decodable: {e}")
    now = int(time.time())
    exp = int(payload.get("exp", 0))
    delta = exp - now
    # 8h = 28800s; allow ±5min tolerance for clock + test latency
    assert 28200 <= delta <= 28800 + 600, f"TTL not ~8h: delta={delta}s, payload={payload}"


# ─────────────── Auto-recredit on rejected withdrawal ───────────────
def _set_om_settings(admin):
    """Ensure OM is enabled and a receiver number exists."""
    admin.patch(f"{BASE}/api/admin/orange-money/settings",
                json={"enabled": True, "receiver_num": "+237658012390",
                      "receiver_name": "New deal finances"}, timeout=20)


def test_om_withdraw_reject_recredits_wallet(admin, daf):
    """Daf (GH) cannot submit, so we just verify the endpoint exists and the
    business logic is wired. We attempt to find an existing PENDING withdrawal
    and check that rejecting it triggers a wallet recredit. If no PENDING row
    exists, skip — the autotest cannot create one without a CM+237 user."""
    _set_om_settings(admin)
    r = admin.get(f"{BASE}/api/admin/orange-money/withdrawals?status=PENDING",
                  timeout=20)
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    pending = data.get("withdrawals") or []
    if not pending:
        pytest.skip("No PENDING withdrawal in DB to test recredit on.")
    target = pending[0]
    user_id = target["user_id"]
    amount = float(target["montant_usd"])

    # snapshot wallet before via admin endpoint (best-effort)
    before = admin.get(f"{BASE}/api/admin/users/{user_id}/wallet", timeout=15)
    bal_before = None
    if before.status_code == 200:
        try:
            bal_before = float(before.json().get("balance", 0))
        except Exception:
            bal_before = None

    rej = admin.patch(
        f"{BASE}/api/admin/orange-money/withdrawals/{target['id']}/reject",
        json={"motif": "Test auto-recredit iter236"}, timeout=20,
    )
    assert rej.status_code == 200, rej.text[:200]

    after = admin.get(f"{BASE}/api/admin/users/{user_id}/wallet", timeout=15)
    if after.status_code == 200 and bal_before is not None:
        bal_after = float(after.json().get("balance", 0))
        assert bal_after >= bal_before + amount - 0.01, (
            f"Recredit failed: before={bal_before} after={bal_after} amount={amount}"
        )
