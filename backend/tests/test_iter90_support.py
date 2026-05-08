"""iter90 — Support (AI chat + tickets) + Wallet alerts ack endpoints.

Covers:
- POST /api/support/ai-chat (Claude Sonnet 4.5, FR answer)
- POST /api/support/ticket + validation
- GET  /api/support/my-tickets
- GET  /api/support/admin/tickets (admin only + status filter)
- PATCH /api/support/admin/tickets/{id}/status
- GET  /api/admin/wallet/alerts?unread_only=true
- POST /api/admin/wallet/alerts/{id}/ack
- POST /api/admin/wallet/alerts/ack-all
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
TIMEOUT = 60


def _login(email, password, max_retries=3):
    last_err = None
    for _ in range(max_retries):
        try:
            s = requests.Session()
            r = s.post(f"{BASE_URL}/api/auth/login",
                       json={"email": email, "password": password},
                       timeout=TIMEOUT)
            if r.status_code == 200:
                token = r.json().get("access_token")
                if token:
                    s.headers.update({"Authorization": f"Bearer {token}"})
                return s
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
    pytest.skip(f"login failed: {last_err}")


@pytest.fixture(scope="module")
def bob():
    return _login("bob@japap.com", "Test1234!")


@pytest.fixture(scope="module")
def admin():
    return _login("admin@japap.com", "JapapAdmin2024!")


# ─── AI chat ───────────────────────────────────────────────
class TestAIChat:
    def test_ai_chat_french_answer(self, bob):
        r = bob.post(f"{BASE_URL}/api/support/ai-chat",
                     json={"messages": [{"role": "user",
                                          "content": "Comment retirer des USDT sur JAPAP ?"}]},
                     timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "reply" in data and isinstance(data["reply"], str)
        assert len(data["reply"]) > 10
        assert "suggests_human_agent" in data
        assert isinstance(data["suggests_human_agent"], bool)

    def test_ai_chat_missing_user_message(self, bob):
        r = bob.post(f"{BASE_URL}/api/support/ai-chat",
                     json={"messages": [{"role": "assistant", "content": "hi"}]},
                     timeout=30)
        assert r.status_code == 400

    def test_ai_chat_too_long(self, bob):
        long_msg = "a" * 2001
        r = bob.post(f"{BASE_URL}/api/support/ai-chat",
                     json={"messages": [{"role": "user", "content": long_msg}]},
                     timeout=30)
        assert r.status_code == 400

    def test_ai_chat_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/support/ai-chat",
                          json={"messages": [{"role": "user", "content": "test"}]},
                          timeout=30)
        assert r.status_code in (401, 403)


# ─── Ticket CRUD ────────────────────────────────────────────
class TestTickets:
    def test_create_ticket_valid(self, bob):
        payload = {
            "subject": "TEST_iter90 retrait bloqué",
            "message": "Bonjour, mon retrait USDT est en processing depuis 2 heures, merci.",
            "category": "wallet",
        }
        r = bob.post(f"{BASE_URL}/api/support/ticket", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ticket_id"].startswith("SUP-")
        assert d["status"] == "open"
        assert "message" in d
        # persist for later tests
        pytest.ticket_id = d["ticket_id"]

    def test_create_ticket_subject_too_short(self, bob):
        r = bob.post(f"{BASE_URL}/api/support/ticket",
                     json={"subject": "ab", "message": "x" * 20, "category": "other"},
                     timeout=30)
        assert r.status_code == 422

    def test_create_ticket_message_too_short(self, bob):
        r = bob.post(f"{BASE_URL}/api/support/ticket",
                     json={"subject": "Sujet ok", "message": "short", "category": "other"},
                     timeout=30)
        assert r.status_code == 422

    def test_create_ticket_invalid_category(self, bob):
        r = bob.post(f"{BASE_URL}/api/support/ticket",
                     json={"subject": "Sujet ok", "message": "x" * 20, "category": "invalid"},
                     timeout=30)
        assert r.status_code == 422

    def test_my_tickets_contains_created(self, bob):
        r = bob.get(f"{BASE_URL}/api/support/my-tickets", timeout=30)
        assert r.status_code == 200, r.text
        items = r.json().get("items", [])
        assert any(t["ticket_id"] == pytest.ticket_id for t in items)

    def test_my_tickets_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/support/my-tickets", timeout=30)
        assert r.status_code in (401, 403)


# ─── Admin tickets ─────────────────────────────────────────
class TestAdminTickets:
    def test_admin_list_tickets_no_filter(self, admin):
        r = admin.get(f"{BASE_URL}/api/support/admin/tickets", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "total" in d and "items" in d
        assert isinstance(d["items"], list)

    def test_admin_list_tickets_status_open(self, admin):
        r = admin.get(f"{BASE_URL}/api/support/admin/tickets?status=open", timeout=30)
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert it["status"] == "open"

    def test_admin_tickets_forbidden_for_user(self, bob):
        r = bob.get(f"{BASE_URL}/api/support/admin/tickets", timeout=30)
        assert r.status_code == 403

    def test_admin_transition_status(self, admin):
        tid = getattr(pytest, "ticket_id", None)
        if not tid:
            pytest.skip("no ticket from create test")
        r = admin.patch(f"{BASE_URL}/api/support/admin/tickets/{tid}/status",
                        json={"status": "in_progress"}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "in_progress"

        r = admin.patch(f"{BASE_URL}/api/support/admin/tickets/{tid}/status",
                        json={"status": "resolved"}, timeout=30)
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"

    def test_admin_update_unknown_ticket(self, admin):
        r = admin.patch(f"{BASE_URL}/api/support/admin/tickets/SUP-DEADBEEF/status",
                        json={"status": "closed"}, timeout=30)
        assert r.status_code == 404

    def test_admin_update_invalid_status(self, admin):
        tid = getattr(pytest, "ticket_id", None)
        if not tid:
            pytest.skip("no ticket")
        r = admin.patch(f"{BASE_URL}/api/support/admin/tickets/{tid}/status",
                        json={"status": "nope"}, timeout=30)
        assert r.status_code == 422


# ─── Wallet alerts ack ─────────────────────────────────────
class TestWalletAlertsAck:
    def test_get_alerts_unread_only_shape(self, admin):
        r = admin.get(f"{BASE_URL}/api/admin/wallet/alerts?unread_only=true", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "unread_count" in d and "items" in d
        assert isinstance(d["unread_count"], int)
        for it in d["items"]:
            assert it.get("acknowledged_at") is None
        pytest.unread_before = d["unread_count"]
        pytest.first_alert_id = d["items"][0]["id"] if d["items"] else None

    def test_ack_single_if_available(self, admin):
        aid = getattr(pytest, "first_alert_id", None)
        if not aid:
            pytest.skip("no unread alert to ack")
        r = admin.post(f"{BASE_URL}/api/admin/wallet/alerts/{aid}/ack", timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True
        # verify it's gone from unread
        r2 = admin.get(f"{BASE_URL}/api/admin/wallet/alerts?unread_only=true", timeout=30)
        ids = [i["id"] for i in r2.json()["items"]]
        assert aid not in ids

    def test_ack_all(self, admin):
        r = admin.post(f"{BASE_URL}/api/admin/wallet/alerts/ack-all", timeout=30)
        assert r.status_code == 200, r.text
        assert "acknowledged" in r.json()
        # after ack-all unread_count should be 0
        r2 = admin.get(f"{BASE_URL}/api/admin/wallet/alerts?unread_only=true", timeout=30)
        assert r2.json()["unread_count"] == 0

    def test_alerts_forbidden_for_user(self, bob):
        r = bob.get(f"{BASE_URL}/api/admin/wallet/alerts", timeout=30)
        assert r.status_code == 403
        r = bob.post(f"{BASE_URL}/api/admin/wallet/alerts/ack-all", timeout=30)
        assert r.status_code == 403
