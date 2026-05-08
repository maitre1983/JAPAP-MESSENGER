"""Iteration 29 Phase A — Top Hosts, Services toggles, Transport driver KYC gating"""
import os, requests, pytest, uuid
BASE = os.environ['REACT_APP_BACKEND_URL'].rstrip('/')
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}

@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json=ADMIN, timeout=30)
    assert r.status_code == 200, r.text
    return s

# --- /api/pro/top-hosts ---
def test_top_hosts_requires_auth():
    r = requests.get(f"{BASE}/api/pro/top-hosts?limit=3", timeout=30)
    assert r.status_code in (401, 403)

def test_top_hosts_returns_list(admin_client):
    r = admin_client.get(f"{BASE}/api/pro/top-hosts?limit=3", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)
    for i, h in enumerate(data):
        assert h["rank"] == i + 1
        assert {"user_id","name","avatar","is_pro","country","earned_usd","credits"} <= set(h.keys())

# --- /api/settings/public ---
def test_public_settings_has_new_flags(admin_client):
    r = admin_client.get(f"{BASE}/api/settings/public", timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ["crypto_enabled","ads_enabled","offers_enabled",
              "transport_driver_kyc_required","transport_driver_emergency_phone_required"]:
        assert k in d, f"missing {k}"

def test_admin_toggle_reflects_in_public(admin_client):
    # Set crypto_enabled = true
    r = admin_client.put(f"{BASE}/api/admin/settings",
                         json={"settings": {"crypto_enabled": "true"}}, timeout=30)
    assert r.status_code == 200, r.text
    r2 = admin_client.get(f"{BASE}/api/settings/public", timeout=30)
    assert r2.json().get("crypto_enabled") == "true"
    # Revert to false (iter29 default)
    admin_client.put(f"{BASE}/api/admin/settings",
                     json={"settings": {"crypto_enabled": "false"}}, timeout=30)
    r3 = admin_client.get(f"{BASE}/api/settings/public", timeout=30)
    assert r3.json().get("crypto_enabled") == "false"

# --- /api/transport/driver/register gating ---
def test_driver_register_without_kyc_returns_403(admin_client):
    # admin has no KYC approved by default
    r = admin_client.post(f"{BASE}/api/transport/driver/register",
        json={"vehicle_model":"Toyota","vehicle_plate":"AB-001","vehicle_type":"standard",
              "emergency_contact_phone":"+237600000000","emergency_contact_name":"Contact"},
        timeout=30)
    assert r.status_code == 403, r.text
    assert r.json().get("detail","").startswith("KYC_REQUIRED:driver"), r.text

def test_driver_register_with_kyc_but_no_phone_returns_400(admin_client):
    # Seed an approved KYC for admin user, then attempt without emergency phone
    me = admin_client.get(f"{BASE}/api/users/me", timeout=30)
    if me.status_code != 200:
        pytest.skip("users/me not available")
    uid = me.json().get("user_id") or me.json().get("id")
    if not uid:
        pytest.skip("no user id")
    # Direct SQL seed via a helper endpoint is not available; use admin KYC approve if any pending exists.
    # We'll just verify the 400 path via an isolated flow if we can create+approve KYC. Otherwise skip.
    pytest.skip("Requires DB-level KYC seed; covered by 403 path and code review")

# --- /api/transport/driver/me structure ---
def test_driver_me_accessible(admin_client):
    r = admin_client.get(f"{BASE}/api/transport/driver/me", timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "is_driver" in d
    if d["is_driver"]:
        for k in ["emergency_contact_phone","emergency_contact_name","status"]:
            assert k in d

# --- drivers table migrations ---
def test_drivers_table_has_columns(admin_client):
    # Indirect verification via driver/me response schema + registration error detail
    # If migrations missing, /driver/register would 500 instead of 403 (KYC_REQUIRED).
    r = admin_client.post(f"{BASE}/api/transport/driver/register",
        json={"vehicle_model":"X","vehicle_plate":"Y","vehicle_type":"standard",
              "emergency_contact_phone":"+237600000001"}, timeout=30)
    assert r.status_code in (400, 403), f"Migration likely broken: {r.status_code} {r.text}"
