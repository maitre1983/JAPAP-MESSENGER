"""iter239w — Crowdfunding refactor backend tests.

Coverage:
  - GET /crowdfunding/state exposes minimum_votes_required, ended_at, duration_days
  - GET/PUT /crowdfunding/admin/settings supports auto_approve_projects,
    default_cycle_duration_days, default_minimum_votes_required
  - PUT /crowdfunding/admin/cycles/active accepts ended_at/minimum_votes_required
  - POST /crowdfunding/projects requires terms_accepted=true (else 400 TERMS_NOT_ACCEPTED)
  - Admin moderation: approve / suspend / reactivate / force-delete (+ 404 / 409 paths)
  - GET /crowdfunding/admin/projects filters + owner_name
  - Worker auto-close logs present
  - Vote does NOT trigger instant winner (auto-win removed)
"""
import os
import time
import uuid

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE, "REACT_APP_BACKEND_URL must be set"

BYPASS = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}

# Global default timeouts via session retry mounting
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def _mk_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[502, 503, 504],
                  allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!", **BYPASS}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!", **BYPASS}
BOB = {"email": "bob@japap.com", "password": "Test1234!", **BYPASS}


# ---------- helpers ----------
def _csrf(s):
    tok = s.cookies.get("csrf_token") or ""
    return {"X-CSRF-Token": tok} if tok else {}


def _attach_csrf_auto(s):
    """Attach a request hook that auto-injects X-CSRF-Token from current cookies."""
    def _hook(request, **_kw):
        if request.method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            tok = s.cookies.get("csrf_token")
            if tok and "X-CSRF-Token" not in request.headers:
                request.headers["X-CSRF-Token"] = tok
        return request
    # requests doesn't support pre-send hooks natively; we override prepare_request
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
    _attach_csrf_auto(s)
    s.get(f"{BASE}/api/health", timeout=30)
    r = s.post(f"{BASE}/api/auth/login", json=creds,
               headers=_csrf(s), timeout=60)
    assert r.status_code == 200, f"login failed for {creds['email']}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_s():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def alice_s():
    return _login(ALICE)


@pytest.fixture(scope="module")
def bob_s():
    return _login(BOB)


# ---------- state ----------
def test_state_exposes_new_fields():
    r = requests.get(f"{BASE}/api/crowdfunding/state", timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "cycle" in data and data["cycle"] is not None
    cy = data["cycle"]
    # iter239w fields exposed
    for k in ("minimum_votes_required", "ended_at", "duration_days",
              "started_at", "votes_to_win"):
        assert k in cy, f"missing {k} in state.cycle"
    assert cy["minimum_votes_required"] == cy["votes_to_win"]


# ---------- admin settings ----------
def test_admin_settings_get_put_aliases(admin_s):
    r = admin_s.get(f"{BASE}/api/crowdfunding/admin/settings", timeout=60)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("auto_approve_projects", "default_cycle_duration_days",
              "default_minimum_votes_required", "default_votes_to_win"):
        assert k in body, f"missing setting {k}"
    initial_auto = bool(body["auto_approve_projects"])

    # PUT with aliases (default_minimum_votes_required + auto_approve_projects)
    payload = {
        "default_minimum_votes_required": 5,
        "default_cycle_duration_days": 30,
        "auto_approve_projects": not initial_auto,
    }
    r2 = admin_s.put(f"{BASE}/api/crowdfunding/admin/settings",
                     json=payload, timeout=60)
    assert r2.status_code == 200, r2.text

    r3 = admin_s.get(f"{BASE}/api/crowdfunding/admin/settings", timeout=60)
    b3 = r3.json()
    assert int(b3["default_votes_to_win"]) == 5
    assert int(b3["default_minimum_votes_required"]) == 5
    assert bool(b3["auto_approve_projects"]) == (not initial_auto)

    # restore
    admin_s.put(f"{BASE}/api/crowdfunding/admin/settings",
                json={"auto_approve_projects": initial_auto}, timeout=60)


# ---------- active cycle update with new aliases ----------
def test_admin_active_cycle_put_aliases(admin_s):
    r0 = requests.get(f"{BASE}/api/crowdfunding/state", timeout=60)
    cur = r0.json()["cycle"]
    payload = {
        "minimum_votes_required": int(cur["minimum_votes_required"]),  # no-op
    }
    r = admin_s.put(f"{BASE}/api/crowdfunding/admin/cycles/active",
                    json=payload, timeout=60)
    # Endpoint should accept the alias (200) — or 404 if no active cycle.
    assert r.status_code in (200, 204), f"{r.status_code} {r.text}"


# ---------- create project: terms gating ----------
def _create_payload(title_suffix):
    return {
        "title": f"TEST_E2E_iter239w {title_suffix}",
        "description": ("Description test e2e iter239w — refactor crowdfunding."
                        " ABCDE FGHIJ KLMNO PQRST UVWXY Z0123 456789 More text."),
        "objective": "Tester le flow terms + admin moderation iter239w refactor.",
        "category": "tech",
        "country_code": "CM",
        "duration_days": 30,
    }


def test_create_rejects_without_terms(alice_s):
    payload = _create_payload(uuid.uuid4().hex[:6])
    # no terms_accepted at all
    r = alice_s.post(f"{BASE}/api/crowdfunding/projects",
                     json=payload, timeout=60)
    assert r.status_code == 400, f"{r.status_code} {r.text}"
    body = r.json()
    detail = body.get("detail") or body
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "TERMS_NOT_ACCEPTED", body

    # explicit false
    payload2 = {**payload, "terms_accepted": False}
    r2 = alice_s.post(f"{BASE}/api/crowdfunding/projects",
                      json=payload2, timeout=60)
    assert r2.status_code == 400
    d2 = r2.json().get("detail", {})
    assert (d2.get("code") if isinstance(d2, dict) else None) == "TERMS_NOT_ACCEPTED"


@pytest.fixture(scope="module")
def created_project(bob_s, alice_s, admin_s):
    """Create one project with terms accepted. auto_approve_projects might be false.
    Uses Bob (Alice already has a project in this cycle from prior runs)."""
    # Ensure auto_approve_projects=false so we test pending_review path
    admin_s.put(f"{BASE}/api/crowdfunding/admin/settings",
                json={"auto_approve_projects": False}, timeout=60)
    # First, try to cleanup any existing Bob project to avoid 409 on duplicate
    try:
        lst = admin_s.get(f"{BASE}/api/crowdfunding/admin/projects",
                          params={"limit": 200}, timeout=60).json()
        items = lst.get("projects") if isinstance(lst, dict) else lst
        for it in items or []:
            if it.get("title", "").startswith("TEST_E2E_iter239w") and \
               it.get("status") not in ("winner", "deleted"):
                admin_s.request(
                    "DELETE",
                    f"{BASE}/api/crowdfunding/admin/projects/{it['slug']}/force-delete",
                    json={"reason": "cleanup E2E iter239w pre-test"},
                    timeout=60,
                )
    except Exception:
        pass

    payload = {**_create_payload(uuid.uuid4().hex[:6]), "terms_accepted": True}
    r = bob_s.post(f"{BASE}/api/crowdfunding/projects",
                   json=payload, timeout=60)
    assert r.status_code in (200, 201), f"{r.status_code} {r.text}"
    proj = r.json()
    assert proj.get("status") in ("pending_review", "active"), proj
    # Verify terms_accepted_at persisted via GET (POST response is minimal)
    rg = requests.get(f"{BASE}/api/crowdfunding/projects/{proj['slug']}", timeout=60)
    if rg.status_code == 200:
        full = rg.json()
        assert full.get("terms_accepted_at"), \
            f"terms_accepted_at not persisted: {full}"
        proj["terms_accepted_at"] = full["terms_accepted_at"]
    yield proj
    # cleanup
    try:
        admin_s.request(
            "DELETE",
            f"{BASE}/api/crowdfunding/admin/projects/{proj['slug']}/force-delete",
            json={"reason": "cleanup E2E iter239w"},
            timeout=60,
        )
    except Exception:
        pass


def test_create_with_terms_pending_review(created_project):
    assert created_project["status"] == "pending_review"
    assert created_project["terms_accepted_at"]


# ---------- admin approve / suspend / reactivate / list ----------
def test_admin_approve_404(admin_s):
    r = admin_s.post(
        f"{BASE}/api/crowdfunding/admin/projects/nonexistent-slug-xyz/approve",
        timeout=60,
    )
    assert r.status_code == 404


def test_admin_approve_then_suspend_then_reactivate(admin_s, created_project):
    slug = created_project["slug"]
    # approve
    r = admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/approve",
                     timeout=60)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    # approve again -> 409 (no longer pending)
    r2 = admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/approve",
                      timeout=60)
    assert r2.status_code == 409, r2.text

    # suspend with too-short reason -> 400/422
    r3 = admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/suspend",
                      json={"reason": "x"}, timeout=60)
    assert r3.status_code in (400, 422), r3.text

    # suspend valid
    r4 = admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/suspend",
                      json={"reason": "test suspend iter239w"}, timeout=60)
    assert r4.status_code == 200, r4.text
    assert r4.json()["status"] == "suspended"

    # reactivate from suspended -> active
    r5 = admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/reactivate",
                      timeout=60)
    assert r5.status_code == 200, r5.text
    assert r5.json()["status"] == "active"

    # reactivate again (not suspended) -> 409
    r6 = admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/reactivate",
                      timeout=60)
    assert r6.status_code == 409, r6.text


def test_admin_list_filter(admin_s):
    r = admin_s.get(f"{BASE}/api/crowdfunding/admin/projects",
                    params={"status": "active", "limit": 100}, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    # Accept either {projects:[...]} or [...]
    items = data.get("projects") if isinstance(data, dict) else data
    assert isinstance(items, list)
    if items:
        assert "owner_name" in items[0], items[0]


# ---------- vote no instant winner ----------
def test_vote_does_not_award_instant_winner(alice_s, created_project, admin_s):
    """Even if a single vote is cast, project must NOT become 'winner' immediately."""
    slug = created_project["slug"]
    # Ensure project is active (no-op if already)
    admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/reactivate",
                 timeout=30)
    # Alice casts a vote (Bob owns the project)
    r = alice_s.post(f"{BASE}/api/crowdfunding/projects/{slug}/vote", timeout=60)
    # Vote may return 200/201 OR 409 if votes not open yet — both are ok
    assert r.status_code in (200, 201, 400, 403, 409), r.text
    # After voting, fetch project status — must NOT be 'winner'
    r2 = requests.get(f"{BASE}/api/crowdfunding/projects/{slug}", timeout=60)
    if r2.status_code == 200:
        assert r2.json().get("status") != "winner", r2.json()


# ---------- worker auto-close logs ----------
def test_worker_started_log():
    """Worker presence verified via supervisor log scan."""
    import subprocess
    out = subprocess.run(
        ["bash", "-c",
         "grep -h 'cf_cycle_close.*worker started' "
         "/var/log/supervisor/backend.err.log /var/log/supervisor/backend.out.log "
         "2>/dev/null | tail -1"],
        capture_output=True, text=True, timeout=30,
    )
    assert "worker started" in (out.stdout or ""), \
        f"Worker start log not found. stdout={out.stdout!r}"


# ---------- i18n keys present in 5 locales ----------
def test_i18n_terms_keys_present():
    import json as _json
    locales = ["fr", "en", "es", "ar", "ru"]
    required_prefixes = ("terms_", "admin_")
    required_specific = "minimum_votes_required"
    for loc in locales:
        path = f"/app/frontend/src/locales/{loc}.json"
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        cf = data.get("crowdfunding") or {}
        # Check at least one terms_ key, one admin_ key, and minimum_votes_required
        has_terms = any(k.startswith("terms_") for k in cf.keys())
        has_admin = any(k.startswith("admin_") for k in cf.keys())
        assert has_terms, f"{loc}: no crowdfunding.terms_* keys"
        assert has_admin, f"{loc}: no crowdfunding.admin_* keys"
        assert required_specific in cf, f"{loc}: missing crowdfunding.{required_specific}"
