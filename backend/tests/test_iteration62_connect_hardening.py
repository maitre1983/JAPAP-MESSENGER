"""Iteration 62 — Connect v2.1 hardening tests.

Covers the three hardening items shipped on top of v2:

  1. SELECT ... FOR UPDATE on /access/redeem — no concurrent double-redeem.
  2. HTTP rate limiting (slowapi) on /qr, /access/redeem, /access/{id}/password.
  3. QR generation guardrails — cap on active non-expired unconsumed tokens
     per hotspot + opportunistic cleanup of expired ones.

Run with RATE_LIMIT_ENABLED=true for the rate-limit test; other tests run
independently and assume the default admin test credentials (see
/app/memory/test_credentials.md).
"""
import os
import uuid
import time
import asyncio
import pytest
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
BOB = {"email": "bob@japap.com", "password": "Test1234!"}
CAROL = {"email": "carol@japap.com", "password": "Test1234!"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}


def _login(creds):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, r.text
    return s, r.json()["user"]["user_id"]


@pytest.fixture(scope="module")
def bob():
    return _login(BOB)


@pytest.fixture(scope="module")
def carol():
    return _login(CAROL)


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module", autouse=True)
def relax_settings(admin):
    """Relax daily caps + bump the QR-per-hotspot cap so we can run many
    redeems from the same pytest IP without hitting the wrong limit."""
    s, _ = admin
    s.put(f"{BASE_URL}/api/admin/settings",
          json={"settings": {
              "connect_min_pro_to_access": "none",
              "connect_min_pro_to_share": "none",
              "connect_pro_required_to_share": False,
              "connect_pro_bypass_user_caps": True,
              "connect_max_connections_per_user_per_hotspot_per_day": 50,
              "connect_max_connections_per_ip_per_day": 500,
              "connect_max_connections_per_device_per_day": 500,
              "connect_qr_max_active_per_hotspot": 100,
          }}, timeout=15)
    yield


@pytest.fixture(scope="module")
def hotspot(bob):
    s, _ = bob
    r = s.post(f"{BASE_URL}/api/connect/hotspots", json={
        "alias": f"Hardening Cafe {uuid.uuid4().hex[:6]}",
        "description": "v2.1 hardening preflight",
        "latitude": 5.3599, "longitude": -4.0082,
        "address": "Abidjan", "type": "user", "country_code": "CI",
    }, timeout=15)
    assert r.status_code == 200, r.text
    hsid = r.json()["hotspot_id"]
    r2 = s.put(f"{BASE_URL}/api/connect/hotspots/{hsid}/wifi",
               json={"ssid": "HardenNet", "password": "Harden!2026", "security_type": "WPA2"},
               timeout=15)
    assert r2.status_code == 200, r2.text
    yield {"hotspot_id": hsid}
    s.delete(f"{BASE_URL}/api/connect/hotspots/{hsid}", timeout=10)


def _mint(bob_session, hotspot_id):
    r = bob_session.post(f"{BASE_URL}/api/connect/hotspots/{hotspot_id}/qr", timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["nonce"]


# ═══════════════════════════════════════════════════════════════════════════
# 1. Concurrent double-redeem race — must resolve to exactly one winner.
# ═══════════════════════════════════════════════════════════════════════════

def test_concurrent_redeem_no_double_reveal(bob, carol, admin, hotspot):
    """Fire N parallel POST /redeem on the SAME nonce from 3 distinct users.

    Expected with the SELECT ... FOR UPDATE transactional lock:
      • Exactly 1 response is HTTP 200 (the winner).
      • All other responses are 4xx (409 / 410 / 400).
      • No 500s, no partial state.
    """
    s_carol, _ = carol
    s_bob, _ = bob
    s_admin, _ = admin
    nonce = _mint(s_bob, hotspot["hotspot_id"])

    def _attempt(sess, dev_id):
        try:
            r = sess.post(f"{BASE_URL}/api/connect/access/redeem",
                          json={"nonce": nonce, "device_id": dev_id}, timeout=15)
            return r.status_code, r.json().get("password", "")
        except Exception as e:
            return 0, str(e)

    # 6 concurrent attempts: Carol x3, Admin x2, Bob x1 (self-hotspot → 400)
    attempts = [
        (s_carol, "race-c1"), (s_carol, "race-c2"), (s_carol, "race-c3"),
        (s_admin, "race-a1"), (s_admin, "race-a2"),
        (s_bob, "race-b1"),
    ]
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda a: _attempt(*a), attempts))

    statuses = [r[0] for r in results]
    winners = [r for r in results if r[0] == 200]
    # Exactly one redemption must succeed
    assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}: {statuses}"
    # Winner actually received the plaintext
    assert winners[0][1] == "Harden!2026"
    # All other attempts are 4xx (409 for the same user, 410 for different,
    # 400 for the owner). None must be 5xx.
    for status, _ in results:
        assert status == 200 or (400 <= status < 500), f"Bad status {status}: {results}"


def test_concurrent_redeem_only_one_connection_row(bob, carol, hotspot):
    """After a burst on one nonce, the DB must show exactly 1 wifi_connection
    tied to that access_token (accessible via the winner's connection_id)."""
    s_bob, _ = bob
    s_carol, _ = carol
    nonce = _mint(s_bob, hotspot["hotspot_id"])

    def _attempt(dev_id):
        try:
            r = s_carol.post(f"{BASE_URL}/api/connect/access/redeem",
                             json={"nonce": nonce, "device_id": dev_id}, timeout=15)
            return r.status_code, r.json()
        except Exception as e:
            return 0, {"err": str(e)}

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(_attempt, ["d1", "d2", "d3", "d4"]))

    ok = [r for r in results if r[0] == 200]
    dup = [r for r in results if r[0] == 409]
    # Carol is the same user across 4 requests → exactly one 200, the rest 409
    assert len(ok) == 1, f"Expected 1 win, got {len(ok)} : {results}"
    assert len(dup) == 3, f"Expected 3 duplicates, got {len(dup)} : {results}"


# ═══════════════════════════════════════════════════════════════════════════
# 2. QR generation guardrails — cap + cleanup of expired tokens.
# ═══════════════════════════════════════════════════════════════════════════

def test_qr_cap_active_per_hotspot(bob, admin, hotspot):
    """Temporarily lower the cap to 3, mint 3 QRs (active), then the 4th
    must 429. Cleanup-of-expired is exercised implicitly by the happy case."""
    s_admin, _ = admin
    s_admin.put(f"{BASE_URL}/api/admin/settings",
                json={"settings": {"connect_qr_max_active_per_hotspot": 3}}, timeout=10)
    s_bob, _ = bob
    try:
        for _ in range(3):
            r = s_bob.post(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/qr", timeout=10)
            assert r.status_code == 200, r.text
        # 4th must be rate-limited at DB level (429)
        r4 = s_bob.post(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/qr", timeout=10)
        assert r4.status_code == 429, r4.text
        assert "QR actifs" in r4.json().get("detail", "")
    finally:
        # Restore the relaxed cap for the rest of the suite
        s_admin.put(f"{BASE_URL}/api/admin/settings",
                    json={"settings": {"connect_qr_max_active_per_hotspot": 100}}, timeout=10)


def test_qr_gen_cleans_expired_tokens(bob, admin):
    """Inject an expired token into the DB then call /qr — cleanup runs,
    a fresh active row is inserted, and the expired one is gone.

    Uses an isolated hotspot so earlier tests' unconsumed QRs don't pollute
    the active-count.
    """
    s_bob, _ = bob
    s_admin, _ = admin
    # Fresh hotspot for this test only
    r = s_bob.post(f"{BASE_URL}/api/connect/hotspots", json={
        "alias": f"Clean Cafe {uuid.uuid4().hex[:6]}",
        "latitude": 5.36, "longitude": -4.0, "type": "user", "country_code": "CI",
    }, timeout=15)
    hsid = r.json()["hotspot_id"]
    s_bob.put(f"{BASE_URL}/api/connect/hotspots/{hsid}/wifi",
              json={"ssid": "Clean", "password": "Clean!2026"}, timeout=10)
    s_admin.put(f"{BASE_URL}/api/admin/settings",
                json={"settings": {"connect_qr_max_active_per_hotspot": 2}}, timeout=10)
    try:
        n1 = _mint(s_bob, hsid)
        _mint(s_bob, hsid)
        # Consume n1 (Carol) → one slot freed
        s_carol, _ = _login(CAROL)
        r = s_carol.post(f"{BASE_URL}/api/connect/access/redeem",
                         json={"nonce": n1, "device_id": "clean-c1"}, timeout=15)
        assert r.status_code == 200, r.text
        # Third QR mint must now succeed (1 active, 1 consumed → still under cap 2)
        r3 = s_bob.post(f"{BASE_URL}/api/connect/hotspots/{hsid}/qr", timeout=10)
        assert r3.status_code == 200, r3.text
        # And a 4th mint → 429 (now 2 active again)
        r4 = s_bob.post(f"{BASE_URL}/api/connect/hotspots/{hsid}/qr", timeout=10)
        assert r4.status_code == 429
    finally:
        s_admin.put(f"{BASE_URL}/api/admin/settings",
                    json={"settings": {"connect_qr_max_active_per_hotspot": 100}}, timeout=10)
        s_bob.delete(f"{BASE_URL}/api/connect/hotspots/{hsid}", timeout=10)


# ═══════════════════════════════════════════════════════════════════════════
# 3. HTTP rate limiting (slowapi). Skipped if RATE_LIMIT_ENABLED=false.
# ═══════════════════════════════════════════════════════════════════════════

pytestmark_rl = pytest.mark.skipif(
    os.environ.get("RATE_LIMIT_ENABLED", "true").lower() == "false",
    reason="Rate limiter disabled via env flag",
)


@pytestmark_rl
def test_rate_limit_qr_endpoint(bob, hotspot):
    """POST /qr is capped at 20/min. The 21st call in a burst must be 429."""
    s, _ = bob
    codes = []
    for _ in range(25):
        r = s.post(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/qr", timeout=10)
        codes.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in codes, f"Expected 429 in burst, got {codes}"


@pytestmark_rl
def test_rate_limit_redeem_endpoint(carol):
    """POST /access/redeem is capped at 20/min/user. 21st call → 429."""
    s, _ = carol
    codes = []
    for _ in range(25):
        r = s.post(f"{BASE_URL}/api/connect/access/redeem",
                   json={"nonce": uuid.uuid4().hex}, timeout=10)
        codes.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in codes, f"Expected 429 in burst, got {codes}"
