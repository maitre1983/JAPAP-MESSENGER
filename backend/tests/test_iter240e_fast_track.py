"""iter240e — Fast-track moderation + Jury badge backend tests.

Coverage:
  FEATURE A — GET /api/crowdfunding/fast-track/price (auth Bob) → 200 + enabled/price/currency.
  FEATURE B — Bob creates pending_review project, /crowdfunding/me returns is_priority=false.
  FEATURE C — POST /projects/{slug}/fast-track 200 → wallet debited 500 XAF + is_priority=true.
  FEATURE D — Second fast-track → 409 already_priority.
  FEATURE E — /crowdfunding/me returns is_priority=true + priority_paid_at.
  FEATURE F — Admin list returns the boosted project at the TOP (is_priority DESC).
  FEATURE G — Admin Settings GET/PUT for fast_track_enabled/price/currency.
  FEATURE H — Insufficient balance returns 400 insufficient_balance.
  FEATURE I — Jury badge GET /jury/me (granted then revoked).
  FEATURE K — SW_VERSION = v23-iter240e.
"""
import os
import uuid
from decimal import Decimal

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE, "REACT_APP_BACKEND_URL must be set"

BYPASS = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!", **BYPASS}
BOB = {"email": "bob@japap.com", "password": "Test1234!", **BYPASS}


# ---------- helpers ----------
def _mk_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[502, 503, 504],
                  allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    # auto-csrf hook
    orig_prep = s.prepare_request
    def _prep(req):
        pr = orig_prep(req)
        if pr.method in ("POST", "PUT", "PATCH", "DELETE"):
            tok = s.cookies.get("csrf_token")
            if tok and "X-CSRF-Token" not in pr.headers:
                pr.headers["X-CSRF-Token"] = tok
        return pr
    s.prepare_request = _prep
    return s


def _login(creds):
    s = _mk_session()
    s.get(f"{BASE}/api/health", timeout=30)
    r = s.post(f"{BASE}/api/auth/login", json=creds, timeout=60)
    assert r.status_code == 200, f"login {creds['email']}: {r.status_code} {r.text}"
    return s


def _register(email, password):
    s = _mk_session()
    s.get(f"{BASE}/api/health", timeout=30)
    # request OTP
    r = s.post(f"{BASE}/api/auth/register", json={
        "email": email, "password": password, "name": "fresh_iter240e",
        **BYPASS,
    }, timeout=60)
    if r.status_code in (200, 201):
        return s, r
    return s, r


@pytest.fixture(scope="module")
def admin_s():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def bob_s():
    return _login(BOB)


@pytest.fixture(scope="module")
def bob_id(bob_s):
    r = bob_s.get(f"{BASE}/api/auth/me", timeout=30)
    assert r.status_code == 200, r.text
    return r.json().get("user_id") or r.json().get("user", {}).get("user_id")


# ---------- FEATURE K — SW_VERSION ----------
def test_K_sw_version():
    r = requests.get(f"{BASE}/sw.js", timeout=30)
    assert r.status_code == 200, r.status_code
    assert "v23-iter240e" in r.text, "SW_VERSION must be v23-iter240e"


# ---------- FEATURE A — fast-track price endpoint ----------
def test_A_fast_track_price(bob_s):
    r = bob_s.get(f"{BASE}/api/crowdfunding/fast-track/price", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("enabled") is True, data
    assert "price" in data and "currency" in data
    # Default 500 XAF unless admin changed it
    assert Decimal(str(data["price"])) > 0
    assert data["currency"].upper() == "XAF"


def test_A_fast_track_price_requires_auth():
    r = requests.get(f"{BASE}/api/crowdfunding/fast-track/price", timeout=30)
    assert r.status_code in (401, 403), r.status_code


# ---------- ensure admin settings consistent BEFORE testing ----------
@pytest.fixture(scope="module", autouse=True)
def _ensure_fasttrack_settings(admin_s):
    # ensure defaults: enabled=true, price=500, currency=XAF, auto_approve=false
    r = admin_s.put(f"{BASE}/api/crowdfunding/admin/settings", json={
        "fast_track_enabled": True,
        "fast_track_price": 500,
        "fast_track_currency": "XAF",
        "auto_approve_projects": False,
    }, timeout=60)
    assert r.status_code in (200, 204), r.text


# ---------- FEATURE G — Admin settings persist ----------
def test_G_admin_settings_fast_track(admin_s):
    # Read
    r = admin_s.get(f"{BASE}/api/crowdfunding/admin/settings", timeout=30)
    assert r.status_code == 200, r.text
    s0 = r.json()
    assert "fast_track_enabled" in s0
    assert "fast_track_price" in s0
    assert "fast_track_currency" in s0
    assert s0["fast_track_currency"].upper() == "XAF"

    # Update price=1000
    r2 = admin_s.put(f"{BASE}/api/crowdfunding/admin/settings", json={
        "fast_track_price": 1000,
    }, timeout=60)
    assert r2.status_code in (200, 204), r2.text

    r3 = admin_s.get(f"{BASE}/api/crowdfunding/admin/settings", timeout=30)
    assert r3.status_code == 200
    assert str(r3.json()["fast_track_price"]).startswith("1000"), r3.json()

    # Reset to 500
    r4 = admin_s.put(f"{BASE}/api/crowdfunding/admin/settings", json={
        "fast_track_price": 500,
    }, timeout=60)
    assert r4.status_code in (200, 204)


# ---------- shared fixture: a fresh pending_review project owned by Bob ----------
@pytest.fixture(scope="module")
def bob_pending_project(bob_s, admin_s):
    # Cleanup any prior TEST_iter240e project
    try:
        lst = admin_s.get(f"{BASE}/api/crowdfunding/admin/projects",
                          params={"limit": 200}, timeout=60).json()
        items = lst.get("projects") if isinstance(lst, dict) else lst
        for it in items or []:
            if it.get("title", "").startswith("TEST_iter240e") and \
               it.get("status") not in ("winner", "deleted"):
                admin_s.request(
                    "DELETE",
                    f"{BASE}/api/crowdfunding/admin/projects/{it['slug']}/force-delete",
                    json={"reason": "cleanup iter240e"}, timeout=60,
                )
    except Exception:
        pass

    payload = {
        "title": f"TEST_iter240e {uuid.uuid4().hex[:6]}",
        "description": "Project for iter240e fast-track regression. " + "Lorem ipsum dolor sit amet " * 5,
        "objective": "Tester fast-track iter240e.",
        "category": "tech",
        "country_code": "CM",
        "duration_days": 30,
        "terms_accepted": True,
    }
    r = bob_s.post(f"{BASE}/api/crowdfunding/projects", json=payload, timeout=60)
    assert r.status_code in (200, 201), r.text
    proj = r.json()
    assert proj.get("status") == "pending_review", proj
    return proj


# ---------- FEATURE B — /crowdfunding/me returns pending project with is_priority=false ----------
def test_B_me_returns_pending_not_priority(bob_s, bob_pending_project):
    r = bob_s.get(f"{BASE}/api/crowdfunding/me", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    # Find our project
    projects = data.get("projects") or ([data["project"]] if data.get("project") else [])
    target = next((p for p in projects if p.get("slug") == bob_pending_project["slug"]), None)
    assert target is not None, f"project not found in /me: {data}"
    assert target.get("status") == "pending_review"
    assert target.get("is_priority") is False, target
    assert target.get("priority_paid_at") in (None, "", False), target


# ---------- FEATURE C — fast-track boost succeeds + debits wallet ----------
def _wallet_balance(s):
    r = s.get(f"{BASE}/api/wallet/balance", timeout=30)
    if r.status_code != 200:
        r = s.get(f"{BASE}/api/wallet", timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    bal = body.get("balance")
    if bal is None and "wallet" in body:
        bal = body["wallet"].get("balance")
    if bal is None:
        bal = body.get("data", {}).get("balance")
    assert bal is not None, body
    return Decimal(str(bal))


def test_C_fast_track_success_debits_wallet(bob_s, bob_pending_project):
    bal_before = _wallet_balance(bob_s)
    slug = bob_pending_project["slug"]
    r = bob_s.post(f"{BASE}/api/crowdfunding/projects/{slug}/fast-track",
                   json={}, timeout=60)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("is_priority") is True
    assert body.get("priority_currency") == "XAF"
    assert Decimal(str(body.get("priority_paid_amount"))) == Decimal("500")
    assert body.get("tx_id"), body

    bal_after = _wallet_balance(bob_s)
    assert bal_before - bal_after == Decimal("500"), \
        f"wallet not debited 500: before={bal_before} after={bal_after}"


# ---------- FEATURE D — idempotence: second boost → 409 already_priority ----------
def test_D_fast_track_already_priority(bob_s, bob_pending_project):
    slug = bob_pending_project["slug"]
    r = bob_s.post(f"{BASE}/api/crowdfunding/projects/{slug}/fast-track",
                   json={}, timeout=60)
    assert r.status_code == 409, r.text
    detail = r.json().get("detail", {})
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "already_priority", r.text


# ---------- FEATURE E — /me returns is_priority=true + priority_paid_at ----------
def test_E_me_returns_priority_true(bob_s, bob_pending_project):
    r = bob_s.get(f"{BASE}/api/crowdfunding/me", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    projects = data.get("projects") or ([data["project"]] if data.get("project") else [])
    target = next((p for p in projects if p.get("slug") == bob_pending_project["slug"]), None)
    assert target is not None
    assert target.get("is_priority") is True, target
    assert target.get("priority_paid_at"), target


# ---------- FEATURE F — admin list shows boosted project at top ----------
def test_F_admin_list_priority_first(admin_s, bob_pending_project):
    r = admin_s.get(f"{BASE}/api/crowdfunding/admin/projects",
                    params={"status": "pending_review", "limit": 200},
                    timeout=60)
    assert r.status_code == 200, r.text
    body = r.json()
    items = body.get("projects") if isinstance(body, dict) else body
    assert items, body
    # boosted project must be first (or at least the first row with is_priority True)
    priorities = [bool(p.get("is_priority")) for p in items]
    # Our boosted one MUST be in the priority block; ordering guarantee: all True before any False
    last_true_idx = -1
    first_false_idx = len(items)
    for i, p in enumerate(priorities):
        if p and last_true_idx < i:
            last_true_idx = i
        if not p and first_false_idx == len(items):
            first_false_idx = i
    assert last_true_idx < first_false_idx, \
        f"is_priority DESC order violated: {priorities}"

    boosted = next((p for p in items if p.get("slug") == bob_pending_project["slug"]), None)
    assert boosted is not None, "boosted project missing from admin pending list"
    assert boosted.get("is_priority") is True
    # It must be in the top section (before any non-priority)
    idx = items.index(boosted)
    assert idx <= last_true_idx


# ---------- FEATURE H — insufficient balance ----------
def test_H_insufficient_balance(admin_s):
    """Create a fresh user with no balance, create a project, then attempt boost."""
    email = f"testft_{uuid.uuid4().hex[:10]}@japap.com"
    pwd = "FreshTest2026!"
    s, r = _register(email, pwd)
    if r.status_code in (200, 201):
        body = r.json()
        # If OTP required, skip — registration without verify cannot login
        if body.get("requires_otp") or body.get("requires_verification"):
            pytest.skip("Registration requires OTP — cannot test fresh user without OTP fetch")
    else:
        pytest.skip(f"Could not register fresh user: {r.status_code} {r.text[:120]}")

    # Try to login
    r2 = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": pwd, **BYPASS}, timeout=30)
    if r2.status_code != 200:
        pytest.skip(f"Fresh user cannot login (needs OTP verify): {r2.status_code}")

    # Create project
    payload = {
        "title": f"TEST_iter240e_freshH {uuid.uuid4().hex[:6]}",
        "description": "Fresh user H test. " + "x" * 100,
        "objective": "Test insufficient balance.",
        "category": "tech",
        "country_code": "CM",
        "duration_days": 30,
        "terms_accepted": True,
    }
    rc = s.post(f"{BASE}/api/crowdfunding/projects", json=payload, timeout=60)
    if rc.status_code not in (200, 201):
        pytest.skip(f"Fresh user could not create project: {rc.status_code} {rc.text[:120]}")
    slug = rc.json()["slug"]

    # Try to boost
    rb = s.post(f"{BASE}/api/crowdfunding/projects/{slug}/fast-track", json={}, timeout=30)
    assert rb.status_code == 400, rb.text
    detail = rb.json().get("detail", {})
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "insufficient_balance", rb.text

    # Cleanup
    try:
        admin_s.request("DELETE",
                        f"{BASE}/api/crowdfunding/admin/projects/{slug}/force-delete",
                        json={"reason": "cleanup iter240e H"}, timeout=30)
    except Exception:
        pass


# ---------- FEATURE I — Jury badge: grant + GET /jury/me + revoke ----------
def test_I_jury_grant_me_revoke(admin_s, bob_s, bob_id):
    # GET before grant — could be either is_jury=true or false depending on history
    r0 = bob_s.get(f"{BASE}/api/crowdfunding/jury/me", timeout=30)
    assert r0.status_code == 200, r0.text
    before = r0.json()

    # Grant
    rg = admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/grant",
                      json={"user_id": bob_id}, timeout=30)
    if rg.status_code == 409:
        # Already active — fine, proceed
        pass
    else:
        assert rg.status_code == 200, rg.text

    r1 = bob_s.get(f"{BASE}/api/crowdfunding/jury/me", timeout=30)
    assert r1.status_code == 200, r1.text
    after = r1.json()
    assert after.get("is_jury") is True, after

    # Certificate endpoint reachable
    rc = bob_s.get(f"{BASE}/api/crowdfunding/jury/certificate/{bob_id}.png",
                   timeout=30, allow_redirects=False)
    assert rc.status_code in (200, 302), rc.status_code

    # Revoke to leave DB clean
    rr = admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/{bob_id}/revoke",
                      json={"reason": "iter240e test cleanup"}, timeout=30)
    assert rr.status_code in (200, 204), rr.text


# ---------- FEATURE L — Regression: admin approve still works ----------
def test_L_regression_admin_approve(admin_s, bob_pending_project):
    """Approve the boosted project → should transition to active and disappear from pending."""
    slug = bob_pending_project["slug"]
    r = admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/approve",
                     json={}, timeout=60)
    assert r.status_code == 200, r.text
    # Verify status
    rg = requests.get(f"{BASE}/api/crowdfunding/projects/{slug}", timeout=30)
    assert rg.status_code == 200, rg.text
    assert rg.json().get("status") == "active", rg.json()
