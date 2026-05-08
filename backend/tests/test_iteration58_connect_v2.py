"""Iteration 58 — Connect v2 (Hybrid Gate Model) backend tests.

Covers the new WiFi-gating endpoints :
    PUT    /api/connect/hotspots/{id}/wifi         Owner sets SSID+password
    DELETE /api/connect/hotspots/{id}/wifi         Owner clears credentials
    POST   /api/connect/hotspots/{id}/qr           Owner mints a 60s nonce
    POST   /api/connect/access/redeem              User exchanges nonce → pw
    GET    /api/connect/access/{id}/password       User re-reveals (rate-limited)
    GET    /api/connect/hotspots/{id}/live-stats   Social counter

The test uses BOB as hotspot owner and CAROL as the scanning user.
"""
import os
import time
import uuid
import pytest
import requests

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


@pytest.fixture(scope="module")
def disable_access_gating(admin):
    """Make sure the tests run even if the admin turned gating ON.
    The preflight we care about here is correctness, not monetization rules."""
    s, _ = admin
    s.put(f"{BASE_URL}/api/admin/settings",
          json={"settings": {
              "connect_min_pro_to_access": "none",
              "connect_min_pro_to_share": "none",
              "connect_pro_required_to_share": False,
              "connect_pro_bypass_user_caps": True,
              "connect_max_connections_per_user_per_hotspot_per_day": 5,
              "connect_qr_max_active_per_hotspot": 100,
          }}, timeout=15)
    yield


@pytest.fixture(scope="module")
def hotspot(bob, disable_access_gating):
    s, bob_uid = bob
    r = s.post(f"{BASE_URL}/api/connect/hotspots", json={
        "alias": f"Test Cafe {uuid.uuid4().hex[:6]}",
        "description": "Sprint D+ preflight",
        "latitude": 5.3599,
        "longitude": -4.0082,
        "address": "Abidjan",
        "type": "user",
        "country_code": "CI",
    }, timeout=15)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["hotspot_id"].startswith("hs_")
    # POST returns minimal {status, hotspot_id, zone} — fetch full row for detail
    h = s.get(f"{BASE_URL}/api/connect/hotspots/{created['hotspot_id']}", timeout=10).json()
    assert h["wifi_configured"] is False
    yield h
    # Cleanup — owner-only delete
    s.delete(f"{BASE_URL}/api/connect/hotspots/{h['hotspot_id']}", timeout=10)


# ─── 1. WiFi credentials set / clear (owner-only) ──────────────────────────

def test_set_wifi_owner_ok(bob, hotspot):
    s, _ = bob
    r = s.put(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/wifi",
              json={"ssid": "CafeAbidjan", "password": "Secret_Abidjan_2026!", "security_type": "WPA2"},
              timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"ok": True, "ssid": "CafeAbidjan", "security_type": "WPA2", "wifi_configured": True}


def test_set_wifi_non_owner_forbidden(carol, hotspot):
    s, _ = carol
    r = s.put(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/wifi",
              json={"ssid": "Hacker", "password": "bad"}, timeout=15)
    assert r.status_code == 403


def test_hotspot_never_leaks_password_in_get(bob, hotspot):
    """GET /hotspots/{id} must expose wifi_configured:true but NEVER the pw."""
    s, _ = bob
    r = s.get(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("wifi_configured") is True
    assert body.get("ssid") == "CafeAbidjan"
    # None of these shall appear — we only expose what's safe
    assert "password" not in body
    assert "wifi_password_encrypted" not in body


# ─── 2. QR generation (owner-only + 60s TTL) ────────────────────────────────

def test_generate_qr_owner_ok(bob, hotspot):
    s, _ = bob
    r = s.post(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/qr", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "nonce" in body and len(body["nonce"]) >= 16
    assert body["ttl_seconds"] == 60
    assert body["deeplink"].startswith("japap://connect/access?nonce=")
    assert "/connect/redeem?nonce=" in body["redeem_url"]


def test_generate_qr_non_owner_forbidden(carol, hotspot):
    s, _ = carol
    r = s.post(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/qr", timeout=10)
    assert r.status_code == 403


def test_generate_qr_without_wifi_config(bob):
    """Hotspot with no wifi configured yet → 400."""
    s, _ = bob
    r = s.post(f"{BASE_URL}/api/connect/hotspots", json={
        "alias": f"Empty {uuid.uuid4().hex[:6]}",
        "latitude": 5.0, "longitude": -4.0, "type": "user", "country_code": "CI",
    }, timeout=15)
    assert r.status_code == 200, r.text
    hsid = r.json()["hotspot_id"]
    try:
        r = s.post(f"{BASE_URL}/api/connect/hotspots/{hsid}/qr", timeout=10)
        assert r.status_code == 400
        assert "WiFi" in r.json().get("detail", "")
    finally:
        s.delete(f"{BASE_URL}/api/connect/hotspots/{hsid}", timeout=10)


# ─── 3. Redeem : happy path + error matrix ──────────────────────────────────

def _mint_nonce(bob_session, hotspot_id):
    r = bob_session.post(f"{BASE_URL}/api/connect/hotspots/{hotspot_id}/qr", timeout=10)
    assert r.status_code == 200
    return r.json()["nonce"]


def test_redeem_happy_path_returns_plaintext_password(bob, carol, hotspot):
    s_carol, _ = carol
    nonce = _mint_nonce(bob[0], hotspot["hotspot_id"])
    r = s_carol.post(f"{BASE_URL}/api/connect/access/redeem",
                     json={"nonce": nonce, "device_id": "pytest-device-a"}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ssid"] == "CafeAbidjan"
    assert body["password"] == "Secret_Abidjan_2026!"   # ← Fernet round-trip works
    assert body["security_type"] == "WPA2"
    assert body["hide_after_seconds"] == 90
    assert body["max_reveals"] == 3
    assert body["connection_id"].startswith("wc_")


def test_redeem_unknown_nonce_404(carol):
    s, _ = carol
    r = s.post(f"{BASE_URL}/api/connect/access/redeem",
               json={"nonce": "x" * 40}, timeout=10)
    assert r.status_code == 404


def test_redeem_consumed_nonce_410(bob, carol, hotspot):
    """Consume a fresh nonce then try to redeem it again from a different user."""
    nonce = _mint_nonce(bob[0], hotspot["hotspot_id"])
    s_carol, _ = carol
    r1 = s_carol.post(f"{BASE_URL}/api/connect/access/redeem",
                      json={"nonce": nonce, "device_id": "pytest-device-b"}, timeout=15)
    assert r1.status_code == 200, r1.text
    # Same user retries → 409 (already consumed, but by you)
    r2 = s_carol.post(f"{BASE_URL}/api/connect/access/redeem",
                      json={"nonce": nonce, "device_id": "pytest-device-b"}, timeout=10)
    assert r2.status_code == 409
    # A different user (admin) tries → 410
    s_admin = requests.Session()
    s_admin.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=10)
    r3 = s_admin.post(f"{BASE_URL}/api/connect/access/redeem",
                      json={"nonce": nonce}, timeout=10)
    assert r3.status_code == 410


def test_redeem_self_hotspot_blocked(bob, hotspot):
    """Owner cannot redeem their own hotspot."""
    s_bob, _ = bob
    nonce = _mint_nonce(s_bob, hotspot["hotspot_id"])
    r = s_bob.post(f"{BASE_URL}/api/connect/access/redeem",
                   json={"nonce": nonce}, timeout=10)
    assert r.status_code == 400


# ─── 4. Re-reveal endpoint ──────────────────────────────────────────────────

def test_reveal_password_rate_limited(bob, carol, hotspot):
    s_carol, _ = carol
    nonce = _mint_nonce(bob[0], hotspot["hotspot_id"])
    r = s_carol.post(f"{BASE_URL}/api/connect/access/redeem",
                     json={"nonce": nonce, "device_id": "pytest-device-c"}, timeout=15)
    assert r.status_code == 200, r.text
    cid = r.json()["connection_id"]
    # Initial redeem counts as 1 reveal. The API allows up to 3 total.
    r1 = s_carol.get(f"{BASE_URL}/api/connect/access/{cid}/password", timeout=10)
    assert r1.status_code == 200, r1.text
    assert r1.json()["reveals_used"] == 2
    r2 = s_carol.get(f"{BASE_URL}/api/connect/access/{cid}/password", timeout=10)
    assert r2.status_code == 200
    assert r2.json()["reveals_used"] == 3
    r3 = s_carol.get(f"{BASE_URL}/api/connect/access/{cid}/password", timeout=10)
    assert r3.status_code == 429


def test_reveal_password_stranger_forbidden(bob, carol, hotspot):
    s_carol, _ = carol
    nonce = _mint_nonce(bob[0], hotspot["hotspot_id"])
    r = s_carol.post(f"{BASE_URL}/api/connect/access/redeem",
                     json={"nonce": nonce, "device_id": "pytest-device-d"}, timeout=15)
    cid = r.json()["connection_id"]
    # Bob (owner, but NOT the connection owner) tries to see the password
    s_bob, _ = bob
    rf = s_bob.get(f"{BASE_URL}/api/connect/access/{cid}/password", timeout=10)
    assert rf.status_code == 403


# ─── 5. Social live-stats counter ───────────────────────────────────────────

def test_live_stats_after_redeem(bob, carol, hotspot):
    s_carol, _ = carol
    nonce = _mint_nonce(bob[0], hotspot["hotspot_id"])
    s_carol.post(f"{BASE_URL}/api/connect/access/redeem",
                 json={"nonce": nonce, "device_id": "pytest-device-e"}, timeout=15)
    r = s_carol.get(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/live-stats", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["connected_now"], int)
    assert isinstance(body["connected_today"], int)
    assert isinstance(body["connected_last_hour"], int)
    # At least Carol's 'active' session should show up
    assert body["connected_now"] >= 1
    assert body["connected_today"] >= 1


# ─── 6. DELETE clears the password ──────────────────────────────────────────

def test_delete_wifi_clears_config(bob, hotspot):
    s, _ = bob
    r = s.delete(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/wifi", timeout=10)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "wifi_configured": False}
    # Verify GET reflects it
    r2 = s.get(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}", timeout=10)
    assert r2.json()["wifi_configured"] is False
    # New QR now 400
    r3 = s.post(f"{BASE_URL}/api/connect/hotspots/{hotspot['hotspot_id']}/qr", timeout=10)
    assert r3.status_code == 400
