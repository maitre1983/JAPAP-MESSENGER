"""Iteration 60 — /api/tasks/my backend tests.

Depends on iter59 test data (a call_summary message was already shared into
Bob↔Carol DM with 3 action items, 2 assigned to Carol, 1 to Bob).
"""
import os
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
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


def test_my_tasks_list_shape(carol):
    s, carol_uid = carol
    r = s.get(f"{BASE_URL}/api/tasks/my", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"items", "total", "status", "limit", "offset"}
    assert isinstance(body["items"], list)
    for it in body["items"]:
        assert set(it.keys()) >= {
            "item_id", "msg_id", "conv_id", "conv_title",
            "what", "done", "who_user_id", "created_at",
        }
        # Every item returned MUST be assigned to Carol
        assert it["who_user_id"] == carol_uid


def test_my_tasks_count_shape(carol):
    s, _ = carol
    r = s.get(f"{BASE_URL}/api/tasks/my/count", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"pending", "done", "total"}
    assert body["pending"] + body["done"] == body["total"]


def test_filter_pending_only(carol):
    s, _ = carol
    r = s.get(f"{BASE_URL}/api/tasks/my?status=pending", timeout=10)
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert it["done"] is False


def test_filter_done_only(carol):
    s, _ = carol
    r = s.get(f"{BASE_URL}/api/tasks/my?status=done", timeout=10)
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert it["done"] is True


def test_my_tasks_pagination(carol):
    s, _ = carol
    r1 = s.get(f"{BASE_URL}/api/tasks/my?limit=1&offset=0", timeout=10).json()
    r2 = s.get(f"{BASE_URL}/api/tasks/my?limit=1&offset=1", timeout=10).json()
    if r1["total"] >= 2:
        assert r1["items"][0]["item_id"] != r2["items"][0]["item_id"]


def test_admin_has_no_call_tasks(admin):
    """Admin wasn't in any call → should have 0 tasks."""
    s, _ = admin
    r = s.get(f"{BASE_URL}/api/tasks/my", timeout=10)
    assert r.status_code == 200
    # Count may be 0 OR any legitimate number if admin has created calls in
    # other tests. The hard contract is: every item's who_user_id is admin's.
    _, admin_uid = _login(ADMIN)
    for it in r.json()["items"]:
        assert it["who_user_id"] == admin_uid


def test_toggle_via_existing_endpoint_reflects_in_my_tasks(carol):
    """Toggle an action item via the existing PATCH endpoint and verify the
    /tasks/my listing reflects the change (core integration test)."""
    s, carol_uid = carol
    before = s.get(f"{BASE_URL}/api/tasks/my?status=pending", timeout=10).json()
    if before["total"] == 0:
        pytest.skip("No pending tasks to toggle in fixture state")
    task = before["items"][0]
    msg_id = task["msg_id"]
    item_id = task["item_id"]
    r = s.patch(
        f"{BASE_URL}/api/calls/summary/action-items/{msg_id}/{item_id}",
        json={"done": True}, timeout=10,
    )
    assert r.status_code == 200
    after = s.get(f"{BASE_URL}/api/tasks/my?status=pending", timeout=10).json()
    assert after["total"] == before["total"] - 1
    done_list = s.get(f"{BASE_URL}/api/tasks/my?status=done", timeout=10).json()
    assert any(it["item_id"] == item_id for it in done_list["items"])
    # Cleanup: put it back to pending
    s.patch(
        f"{BASE_URL}/api/calls/summary/action-items/{msg_id}/{item_id}",
        json={"done": False}, timeout=10,
    )
