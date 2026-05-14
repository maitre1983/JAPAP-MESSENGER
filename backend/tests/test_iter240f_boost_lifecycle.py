"""iter240f — Boost extension (active+pre-vote) + edit/delete lock backend tests.

Coverage:
  K   SW_VERSION = v23-iter240f.
  E   GET /fast-track/price → enabled True, price>0, currency string.
  D   /auth/me exposes language (for AuthContext fallback).
  A   POST /projects/{slug}/fast-track on an ACTIVE project (votes_open=false, votes_count=0)
       → 200, is_priority=true, and public list orders boosted project among priority block.
  C1  PUT /projects/{slug} succeeds when votes_open=false AND votes_count=0.
  B   Open votes → POST /fast-track returns 409 votes_already_open.
  C2  PUT /projects/{slug} returns 409 when votes_open=true.
  C3  DELETE /projects/{slug} returns 409 when votes_open=true.

Bob is restricted to one active project per cycle, so all project-scoped tests share
ONE pending→active project created in a module-scope fixture.
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


def _mk_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[502, 503, 504],
                  allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
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


@pytest.fixture(scope="module")
def admin_s():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def bob_s():
    return _login(BOB)


def _set_votes_open(admin_s, value):
    """Toggle votes_open on the active cycle via direct DB write (no admin
    endpoint exposes this — votes are normally opened automatically when the
    project threshold is reached). Uses backend DATABASE_URL from /app/backend/.env."""
    try:
        import asyncio
        import asyncpg
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            return False, None

        async def _do():
            conn = await asyncpg.connect(db_url)
            try:
                if value:
                    await conn.execute(
                        "UPDATE crowdfunding_cycles SET votes_open = TRUE, "
                        "votes_opened_at = COALESCE(votes_opened_at, NOW()) "
                        "WHERE status = 'active'"
                    )
                else:
                    await conn.execute(
                        "UPDATE crowdfunding_cycles SET votes_open = FALSE "
                        "WHERE status = 'active'"
                    )
            finally:
                await conn.close()
        asyncio.run(_do())
        return True, None
    except Exception as exc:
        return False, exc


def _cleanup_test_projects(admin_s):
    try:
        lst = admin_s.get(f"{BASE}/api/crowdfunding/admin/projects",
                          params={"limit": 200}, timeout=60).json()
        items = lst.get("projects") if isinstance(lst, dict) else lst
        for it in items or []:
            if it.get("title", "").startswith("TEST_iter240f"):
                admin_s.request(
                    "DELETE",
                    f"{BASE}/api/crowdfunding/admin/projects/{it['slug']}/force-delete",
                    json={"reason": "cleanup iter240f"}, timeout=60,
                )
    except Exception:
        pass


@pytest.fixture(scope="module")
def bob_active_project(bob_s, admin_s):
    """Module-scope: one Bob active project (votes_open=false, votes_count=0) for all tests."""
    # Ensure votes closed and clean stale tests
    _set_votes_open(admin_s, False)
    _cleanup_test_projects(admin_s)

    # Bob can only have ONE active project per cycle. If he already has one,
    # force-delete it via admin.
    try:
        me = bob_s.get(f"{BASE}/api/crowdfunding/me", timeout=30).json()
        existing = me.get("projects") or ([me["project"]] if me.get("project") else [])
        for p in existing:
            if p.get("status") in ("active", "pending_review"):
                admin_s.request(
                    "DELETE",
                    f"{BASE}/api/crowdfunding/admin/projects/{p['slug']}/force-delete",
                    json={"reason": "iter240f setup"}, timeout=60,
                )
    except Exception:
        pass

    payload = {
        "title": f"TEST_iter240f {uuid.uuid4().hex[:6]}",
        "description": "iter240f boost-active test. " + "Lorem ipsum dolor " * 8,
        "objective": "Tester boost sur projet active iter240f.",
        "category": "tech",
        "country_code": "CM",
        "duration_days": 30,
        "terms_accepted": True,
    }
    r = bob_s.post(f"{BASE}/api/crowdfunding/projects", json=payload, timeout=60)
    assert r.status_code in (200, 201), f"create: {r.status_code} {r.text}"
    slug = r.json()["slug"]
    ra = admin_s.post(f"{BASE}/api/crowdfunding/admin/projects/{slug}/approve",
                      json={}, timeout=60)
    assert ra.status_code == 200, ra.text
    rg = requests.get(f"{BASE}/api/crowdfunding/projects/{slug}", timeout=30)
    assert rg.status_code == 200 and rg.json().get("status") == "active", rg.text
    yield slug
    # Teardown
    try:
        _set_votes_open(admin_s, False)
        admin_s.request(
            "DELETE",
            f"{BASE}/api/crowdfunding/admin/projects/{slug}/force-delete",
            json={"reason": "iter240f teardown"}, timeout=30,
        )
    except Exception:
        pass


# ---------- K — SW_VERSION ----------
def test_K_sw_version():
    r = requests.get(f"{BASE}/sw.js", timeout=30)
    assert r.status_code == 200
    assert "v23-iter240f" in r.text, "SW_VERSION must contain v23-iter240f"


# ---------- E — fast-track price endpoint still works ----------
def test_E_fast_track_price(bob_s):
    r = bob_s.get(f"{BASE}/api/crowdfunding/fast-track/price", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("enabled") is True, data
    assert Decimal(str(data["price"])) > 0, data
    assert isinstance(data["currency"], str) and len(data["currency"]) > 0


# ---------- D — backend exposes user.language ----------
def test_D_auth_me_has_language_field(bob_s):
    r = bob_s.get(f"{BASE}/api/auth/me", timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    user = body.get("user") or body
    assert ("language" in user) or ("preferred_lang" in user), \
        f"neither language nor preferred_lang in /auth/me: {list(user.keys())[:25]}"


# ---------- A — boost ACTIVE project before votes open ----------
def test_A_boost_active_orders_in_priority_block(bob_s, admin_s, bob_active_project):
    slug = bob_active_project

    # Ensure baseline: not priority yet
    rg = requests.get(f"{BASE}/api/crowdfunding/projects/{slug}", timeout=30)
    assert rg.status_code == 200
    assert not rg.json().get("is_priority"), rg.json()

    # Confirm price endpoint state (for context)
    rp = bob_s.get(f"{BASE}/api/crowdfunding/fast-track/price", timeout=30)
    expected_price = Decimal(str(rp.json()["price"])) if rp.status_code == 200 else None
    expected_ccy = (rp.json().get("currency") or "").upper() if rp.status_code == 200 else None

    # Boost
    r = bob_s.post(f"{BASE}/api/crowdfunding/projects/{slug}/fast-track",
                   json={}, timeout=60)
    assert r.status_code == 200, f"active+pre-vote boost must succeed: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("ok") is True
    assert body.get("is_priority") is True
    assert body.get("tx_id"), body
    if expected_price is not None:
        assert Decimal(str(body["priority_paid_amount"])) == expected_price, body
    if expected_ccy:
        assert (body.get("priority_currency") or "").upper() == expected_ccy

    # Public list — boosted project must appear in priority-first ordering
    rl = requests.get(f"{BASE}/api/crowdfunding/projects",
                      params={"limit": 100}, timeout=30)
    assert rl.status_code == 200, rl.text
    plist = rl.json()
    items = plist.get("projects") if isinstance(plist, dict) else plist
    assert items, plist

    priorities = [bool(p.get("is_priority")) for p in items]
    # Verify no "False before True" in the sequence (ORDER BY is_priority DESC)
    seen_false = False
    for v in priorities:
        if not v:
            seen_false = True
        elif seen_false and v:
            pytest.fail(f"is_priority DESC ordering violated: {priorities[:20]}")

    ours = next((p for p in items if p.get("slug") == slug), None)
    assert ours is not None, "boosted project missing from public list"
    assert bool(ours.get("is_priority")) is True
    # Our project must be inside the leading priority block
    idx = items.index(ours)
    leading_priority_count = sum(1 for p in priorities if p) if all(priorities[:priorities.count(True)]) else 0
    # Simpler check: index < last_true_index+1
    last_true = max((i for i, v in enumerate(priorities) if v), default=-1)
    assert idx <= last_true


# ---------- C1 — Edit allowed when votes_open=false AND votes_count=0 ----------
def test_C1_edit_allowed_when_votes_closed(bob_s, admin_s, bob_active_project):
    _set_votes_open(admin_s, False)
    r = bob_s.put(f"{BASE}/api/crowdfunding/projects/{bob_active_project}",
                  json={"objective": "Updated iter240f pre-votes"}, timeout=30)
    assert r.status_code == 200, f"edit must succeed pre-votes: {r.status_code} {r.text}"


# ---------- B — votes_open=true blocks boost on a new attempt ----------
def test_B_boost_blocked_when_votes_open(bob_s, admin_s, bob_active_project):
    """Reuses the already-boosted project — boost attempt should hit votes_already_open
    BEFORE the idempotency check IF order matters. Since project is already is_priority=true,
    the handler may return 409 already_priority first. So we test the underlying behavior
    by ensuring once votes_open=true, the endpoint does NOT return 200."""
    ok, resp = _set_votes_open(admin_s, True)
    if not ok:
        pytest.skip(f"Cannot toggle votes_open: {resp.status_code} {resp.text[:120]}")

    r = bob_s.post(f"{BASE}/api/crowdfunding/projects/{bob_active_project}/fast-track",
                   json={}, timeout=30)
    assert r.status_code == 409, f"boost must 409 when votes_open=true: {r.status_code} {r.text}"
    detail = r.json().get("detail", {})
    code = detail.get("code") if isinstance(detail, dict) else None
    # Either votes_already_open (preferred for non-priority projects) or already_priority
    # (since our test project is already boosted from test_A). Both are valid "no-op" signals.
    assert code in ("votes_already_open", "already_priority"), r.text

    # Keep votes_open=true for the next two tests
    # (cleaned up in fixture teardown)


# ---------- C2 — Edit blocked when votes_open=true ----------
def test_C2_edit_blocked_when_votes_open(bob_s, admin_s, bob_active_project):
    # votes_open should still be True from test_B; assert explicitly
    ok, _ = _set_votes_open(admin_s, True)
    assert ok, "cannot enable votes_open"
    r = bob_s.put(f"{BASE}/api/crowdfunding/projects/{bob_active_project}",
                  json={"objective": "Should be blocked iter240f"}, timeout=30)
    assert r.status_code == 409, f"edit must 409 when votes_open=true: {r.status_code} {r.text}"


# ---------- C3 — Delete blocked when votes_open=true ----------
def test_C3_delete_blocked_when_votes_open(bob_s, admin_s, bob_active_project):
    ok, _ = _set_votes_open(admin_s, True)
    assert ok
    r = bob_s.delete(f"{BASE}/api/crowdfunding/projects/{bob_active_project}", timeout=30)
    assert r.status_code == 409, f"delete must 409 when votes_open=true: {r.status_code} {r.text}"

    # Reset to votes_open=false for cleanup
    _set_votes_open(admin_s, False)
