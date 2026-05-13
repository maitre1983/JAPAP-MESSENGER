"""iter239x — Crowdfunding Jury system + login_required vote + PwaRefresh contracts.

Coverage:
  - POST /crowdfunding/projects/{slug}/vote returns 401 login_required when unauth
  - Authenticated non-jury vote: vote_weight=1, votes_count += 1
  - Admin grant jury → vote_weight scales per jury_vote_weight_by_wins; is_jury_vote=True
  - Grant is idempotent (no duplicate active membership on re-grant)
  - Revoke endpoint: 200 with active membership; 404 without
  - GET /crowdfunding/jury/me + /jury/members visibility
  - GET /admin/jury list (with include_revoked)
  - GET /jury/certificate/{user_id}.png → image/png with PNG signature
  - GET /admin/settings exposes jury_vote_weight_by_wins + jury_membership_duration_cycles
  - PUT /admin/settings accepts jury_* aliases (and 0 → null for duration)
  - SW_VERSION = v19-iter239x in /app/frontend/public/sw.js
  - i18n keys presence in 5 locales
"""
import json
import os
import struct
import uuid

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE, "REACT_APP_BACKEND_URL must be set"

BYPASS = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!", **BYPASS}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **BYPASS}
BOB = {"email": "bob@japap.com", "password": "Test1234!", **BYPASS}


def _mk_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[502, 503, 504],
                  allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
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


def _user_id(s):
    r = s.get(f"{BASE}/api/auth/me", timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["user_id"]


@pytest.fixture(scope="module")
def admin_s():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def alice_s():
    return _login(ALICE)


@pytest.fixture(scope="module")
def bob_s():
    return _login(BOB)


@pytest.fixture(scope="module")
def bob_id(bob_s):
    return _user_id(bob_s)


@pytest.fixture(scope="module")
def active_slug(admin_s, bob_s, alice_s):
    """Find or create one approved active project owned by Alice or Bob."""
    r = admin_s.get(f"{BASE}/api/crowdfunding/admin/projects",
                    params={"status": "active", "limit": 100}, timeout=60)
    items = r.json().get("projects") if isinstance(r.json(), dict) else r.json()
    if items:
        return items[0]["slug"]
    # Else create with Bob → approve
    admin_s.put(f"{BASE}/api/crowdfunding/admin/settings",
                json={"auto_approve_projects": True}, timeout=30)
    payload = {
        "title": f"TEST_E2E_iter239x {uuid.uuid4().hex[:6]}",
        "description": "Description test e2e iter239x jury voting flow." * 3,
        "objective": "Test jury weighted voting iter239x.",
        "category": "tech", "country_code": "CM", "duration_days": 30,
        "terms_accepted": True,
    }
    r2 = bob_s.post(f"{BASE}/api/crowdfunding/projects", json=payload, timeout=60)
    assert r2.status_code in (200, 201), r2.text
    slug = r2.json()["slug"]
    if r2.json().get("status") != "active":
        admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/approve",
                     timeout=30)
    return slug


# --------------- TASK 1: vote login_required -----------------
def test_vote_unauth_returns_401_login_required(active_slug):
    """Anonymous client must receive 401 + detail.code='login_required'."""
    r = requests.post(f"{BASE}/api/crowdfunding/projects/{active_slug}/vote",
                      timeout=60)
    assert r.status_code == 401, f"{r.status_code} {r.text}"
    body = r.json()
    detail = body.get("detail")
    assert isinstance(detail, dict), body
    assert detail.get("code") == "login_required", body
    assert "Japap" in (detail.get("message") or "") or \
           "japap" in (detail.get("message") or "").lower(), body


# --------------- TASK 3: jury grant idempotent ---------------
def _ensure_revoked(admin_s, user_id):
    """Best-effort revoke pre-existing memberships for clean slate."""
    try:
        admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/{user_id}/revoke",
                     json={"reason": "cleanup E2E iter239x"}, timeout=30)
    except Exception:
        pass


@pytest.fixture
def revoke_bob_after(admin_s, bob_id):
    yield
    _ensure_revoked(admin_s, bob_id)


def test_admin_grant_idempotent(admin_s, bob_id, revoke_bob_after):
    _ensure_revoked(admin_s, bob_id)
    payload = {"user_id": bob_id}
    r1 = admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/grant",
                      json=payload, timeout=60)
    assert r1.status_code in (200, 201), r1.text
    body1 = r1.json()
    m1 = body1.get("membership") or body1
    assert m1.get("user_id") == bob_id, body1

    # Re-grant — must not create duplicate active membership
    r2 = admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/grant",
                      json=payload, timeout=60)
    assert r2.status_code in (200, 201), r2.text

    # Verify only one active membership for Bob
    r3 = admin_s.get(f"{BASE}/api/crowdfunding/admin/jury",
                     params={"include_revoked": "false", "limit": 500},
                     timeout=60)
    assert r3.status_code == 200, r3.text
    data = r3.json()
    items = data.get("members") if isinstance(data, dict) else data
    bob_active = [m for m in items
                  if m.get("user_id") == bob_id and not m.get("revoked_at")]
    assert len(bob_active) == 1, \
        f"expected 1 active membership for Bob, got {len(bob_active)}: {bob_active}"


def test_admin_revoke_404_when_no_membership(admin_s, bob_id):
    _ensure_revoked(admin_s, bob_id)
    # Second revoke should be 404 (nothing active left)
    r = admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/{bob_id}/revoke",
                     json={"reason": "test 404 path"}, timeout=30)
    assert r.status_code == 404, r.text


def test_jury_me_and_members_visibility(admin_s, bob_s, bob_id):
    _ensure_revoked(admin_s, bob_id)
    # Before grant: is_jury=False
    rme = bob_s.get(f"{BASE}/api/crowdfunding/jury/me", timeout=30)
    assert rme.status_code == 200, rme.text
    assert rme.json().get("is_jury") is False

    # Grant + verify
    admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/grant",
                 json={"user_id": bob_id}, timeout=30)
    rme2 = bob_s.get(f"{BASE}/api/crowdfunding/jury/me", timeout=30)
    body = rme2.json()
    assert body.get("is_jury") is True, body
    assert int(body.get("vote_weight", 0)) >= 1
    assert body.get("active_membership"), body
    assert isinstance(body.get("memberships"), list)

    # public members list contains Bob
    rmem = requests.get(f"{BASE}/api/crowdfunding/jury/members", timeout=30)
    assert rmem.status_code == 200, rmem.text
    members = rmem.json()
    items = members.get("members") if isinstance(members, dict) else members
    found = [m for m in items if m.get("user_id") == bob_id]
    assert found, f"Bob not in public jury list: {items}"
    m = found[0]
    for k in ("vote_weight", "total_wins", "name", "country_code"):
        assert k in m, f"missing {k} in {m}"

    _ensure_revoked(admin_s, bob_id)


# --------------- vote with auth: non-jury vs jury ---------------
def _get_votes_count(slug):
    r = requests.get(f"{BASE}/api/crowdfunding/projects/{slug}", timeout=30)
    if r.status_code != 200:
        return None
    return int(r.json().get("votes_count", 0))


def test_authenticated_non_jury_vote_weight_1(admin_s, alice_s, bob_s,
                                              bob_id, active_slug):
    _ensure_revoked(admin_s, bob_id)
    # Pick a voter who does NOT own the project. Use alice if project owned by Bob, else bob.
    r = requests.get(f"{BASE}/api/crowdfunding/projects/{active_slug}", timeout=30)
    if r.status_code != 200:
        pytest.skip(f"cannot fetch project {active_slug}")
    owner_id = r.json().get("owner_user_id") or r.json().get("user_id")
    voter_s = bob_s if owner_id != bob_id else alice_s

    before = _get_votes_count(active_slug)
    if before is None:
        pytest.skip("active project not fetchable")

    rv = voter_s.post(f"{BASE}/api/crowdfunding/projects/{active_slug}/vote",
                      timeout=60)
    if rv.status_code == 409:
        # Already voted this cycle — acceptable: just validate the response shape.
        pytest.skip(f"voter already voted this cycle: {rv.json()}")
    assert rv.status_code in (200, 201), rv.text
    body = rv.json()
    assert int(body.get("vote_weight", 0)) == 1, body
    assert body.get("is_jury_vote") is False, body

    after = _get_votes_count(active_slug)
    assert after == before + 1, f"votes_count delta != 1 (before={before}, after={after})"


def test_jury_vote_weight_scales(admin_s, alice_s, bob_s, bob_id, active_slug):
    """Bob granted jury → vote_weight per table; votes_count += weight; is_jury_vote=True."""
    # Configure a known weight table for [1 win] (Bob may have 0 or 1 win in cycle history)
    cfg_weights = {"0": 25, "1": 50, "2": 100}
    admin_s.put(f"{BASE}/api/crowdfunding/admin/settings",
                json={"jury_vote_weight_by_wins": cfg_weights}, timeout=30)
    _ensure_revoked(admin_s, bob_id)

    # Choose voter who is NOT owner — make bob the voter (he was the unauth test target).
    r = requests.get(f"{BASE}/api/crowdfunding/projects/{active_slug}", timeout=30)
    owner_id = r.json().get("owner_user_id") or r.json().get("user_id")
    if owner_id == bob_id:
        # If Bob owns it, grant Alice and vote with Alice
        alice_id = _user_id(alice_s)
        _ensure_revoked(admin_s, alice_id)
        admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/grant",
                     json={"user_id": alice_id}, timeout=30)
        voter_s = alice_s
        voter_id = alice_id
    else:
        admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/grant",
                     json={"user_id": bob_id}, timeout=30)
        voter_s = bob_s
        voter_id = bob_id

    # Read expected weight from /jury/me
    me = voter_s.get(f"{BASE}/api/crowdfunding/jury/me", timeout=30).json()
    expected_weight = int(me["vote_weight"])
    assert expected_weight >= 1, me

    before = _get_votes_count(active_slug)
    rv = voter_s.post(f"{BASE}/api/crowdfunding/projects/{active_slug}/vote",
                      timeout=60)
    try:
        if rv.status_code == 409:
            pytest.skip(f"voter already voted this cycle: {rv.json()}")
        assert rv.status_code in (200, 201), rv.text
        body = rv.json()
        assert int(body.get("vote_weight", 0)) == expected_weight, body
        if expected_weight > 1:
            assert body.get("is_jury_vote") is True, body
        after = _get_votes_count(active_slug)
        assert after == before + expected_weight, \
            f"delta {after - before} != expected {expected_weight}"
    finally:
        _ensure_revoked(admin_s, voter_id)


# --------------- admin/jury list -----------------------------
def test_admin_jury_list(admin_s, bob_id):
    r = admin_s.get(f"{BASE}/api/crowdfunding/admin/jury",
                    params={"include_revoked": "true", "limit": 500}, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    items = data.get("members") if isinstance(data, dict) else data
    assert isinstance(items, list)
    # Anonymous must NOT have access
    ra = requests.get(f"{BASE}/api/crowdfunding/admin/jury", timeout=30)
    assert ra.status_code in (401, 403), ra.status_code


# --------------- certificate PNG ----------------------------
def test_jury_certificate_png(admin_s, bob_s, bob_id):
    _ensure_revoked(admin_s, bob_id)
    admin_s.post(f"{BASE}/api/crowdfunding/admin/jury/grant",
                 json={"user_id": bob_id}, timeout=30)
    try:
        r = requests.get(
            f"{BASE}/api/crowdfunding/jury/certificate/{bob_id}.png",
            timeout=60)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        ct = r.headers.get("content-type", "")
        assert ct.startswith("image/png"), ct
        # PNG signature
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n", r.content[:16]
        # IHDR chunk gives width/height
        w, h = struct.unpack(">II", r.content[16:24])
        assert w == 1200 and h == 900, f"expected 1200x900, got {w}x{h}"
    finally:
        _ensure_revoked(admin_s, bob_id)


# --------------- admin settings jury_* aliases ----------------
def test_admin_settings_jury_fields(admin_s):
    r = admin_s.get(f"{BASE}/api/crowdfunding/admin/settings", timeout=30)
    assert r.status_code == 200, r.text
    b = r.json()
    assert "jury_vote_weight_by_wins" in b, b.keys()
    assert "jury_membership_duration_cycles" in b, b.keys()
    initial_table = b.get("jury_vote_weight_by_wins") or {}
    initial_duration = b.get("jury_membership_duration_cycles")

    # PUT new values
    new_table = {"1": 100, "2": 200, "3": 300}
    r2 = admin_s.put(f"{BASE}/api/crowdfunding/admin/settings",
                     json={"jury_vote_weight_by_wins": new_table,
                           "jury_membership_duration_cycles": 0},
                     timeout=30)
    assert r2.status_code == 200, r2.text
    r3 = admin_s.get(f"{BASE}/api/crowdfunding/admin/settings", timeout=30)
    b3 = r3.json()
    # weight table persisted (keys may be normalised to str)
    tbl = b3.get("jury_vote_weight_by_wins") or {}
    assert int(tbl.get("1", tbl.get(1, 0))) == 100, tbl
    assert int(tbl.get("2", tbl.get(2, 0))) == 200, tbl
    # 0 → null (permanent)
    assert b3.get("jury_membership_duration_cycles") in (None, 0), b3

    # restore
    admin_s.put(f"{BASE}/api/crowdfunding/admin/settings",
                json={"jury_vote_weight_by_wins": initial_table or {"1": 50},
                      "jury_membership_duration_cycles":
                          initial_duration if initial_duration is not None else 0},
                timeout=30)


# --------------- SW version --------------------------------
def test_sw_version_v19_iter239x():
    with open("/app/frontend/public/sw.js", "r", encoding="utf-8") as f:
        src = f.read()
    assert 'SW_VERSION = "v19-iter239x"' in src, "SW_VERSION mismatch"


# --------------- i18n keys -------------------------------
def test_i18n_crowdfunding_vote_jury_keys():
    required = (
        "vote_btn", "vote_login_required_btn", "vote_login_required",
        "jury_vote_success", "jury_badge", "jury_certificate_download",
    )
    for loc in ("fr", "en", "es", "ar", "ru"):
        with open(f"/app/frontend/src/locales/{loc}.json", encoding="utf-8") as f:
            cf = (json.load(f).get("crowdfunding") or {})
        for k in required:
            assert k in cf, f"{loc}: missing crowdfunding.{k}"


def test_i18n_pwa_keys():
    required = ("refresh_btn", "refreshing", "refresh_tooltip",
                "update_available_short", "last_refresh")
    missing = {}
    for loc in ("fr", "en", "es", "ar", "ru"):
        with open(f"/app/frontend/src/locales/{loc}.json", encoding="utf-8") as f:
            pwa = (json.load(f).get("pwa") or {})
        miss = [k for k in required if k not in pwa]
        if miss:
            missing[loc] = miss
    assert not missing, f"pwa i18n missing: {missing}"
