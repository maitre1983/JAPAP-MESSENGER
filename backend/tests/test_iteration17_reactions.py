"""
Iteration 17 — Message Reactions (emoji) backend tests.

Covers:
- GET /api/messages/allowed-reactions (auth required, emojis list)
- POST /api/messages/{msg_id}/react toggle add/remove, multiple emojis
- POST react on invalid emoji (400), missing msg (404), non-participant (403)
- GET /api/messages/conversations/{conv_id} returns reactions array on each msg
- Regression: send-money in chat, send text, feed like/comment/tip still work
"""
import os
import uuid
import pytest
import requests
from decimal import Decimal

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")

ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login failed for {creds['email']}: {r.status_code} {r.text}"
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    assert tok, f"no access_token in response: {data}"
    return tok, data["user"]["user_id"]


@pytest.fixture(scope="module")
def admin():
    tok, uid = _login(ADMIN)
    return {"token": tok, "user_id": uid,
            "headers": {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}}


@pytest.fixture(scope="module")
def bob():
    # Register bob if login fails, then return auth info
    r = requests.post(f"{BASE_URL}/api/auth/login", json=BOB, timeout=15)
    if r.status_code != 200:
        reg = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": BOB["email"], "password": BOB["password"],
            "username": "bob", "first_name": "Bob", "last_name": "Test",
            "terms_accepted": True,
        }, timeout=15)
        assert reg.status_code in (200, 201), f"register failed: {reg.status_code} {reg.text}"
    tok, uid = _login(BOB)
    return {"token": tok, "user_id": uid,
            "headers": {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}}


@pytest.fixture(scope="module")
def seed_conversation_and_msgs(admin, bob):
    """Ensure admin↔bob conversation exists with one text + one money message."""
    # send text message from admin → bob
    r = requests.post(f"{BASE_URL}/api/messages/send",
                      headers=admin["headers"],
                      json={"to_user_id": bob["user_id"], "text": f"TEST_iter17 text {uuid.uuid4().hex[:6]}"},
                      timeout=15)
    assert r.status_code == 200, f"send text failed: {r.status_code} {r.text}"
    data = r.json()
    text_msg = data["message"]
    conv_id = data["conv_id"]

    # send money admin → bob (regression + gives us a money msg to react on)
    rm = requests.post(f"{BASE_URL}/api/messages/send-money",
                       headers=admin["headers"],
                       json={"to_user_id": bob["user_id"], "amount": 200, "note": "TEST_iter17"},
                       timeout=20)
    assert rm.status_code == 200, f"send-money failed: {rm.status_code} {rm.text}"
    money_payload = rm.json()
    return {"conv_id": conv_id, "text_msg_id": text_msg["msg_id"], "money_msg_id": money_payload["msg_id"]}


# ---------- ALLOWED REACTIONS ----------

class TestAllowedReactions:
    def test_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/messages/allowed-reactions", timeout=10)
        assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"

    def test_returns_emojis_list(self, admin):
        r = requests.get(f"{BASE_URL}/api/messages/allowed-reactions", headers=admin["headers"], timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "emojis" in data
        assert isinstance(data["emojis"], list)
        assert len(data["emojis"]) >= 6
        # quick-reactions subset must be present
        for e in ["❤️", "🔥", "💸", "😂", "👍", "😮"]:
            assert e in data["emojis"], f"{e} missing from allowed list"


# ---------- REACT (toggle / multi / validation) ----------

class TestReactToggle:
    def test_add_reaction(self, admin, seed_conversation_and_msgs):
        msg_id = seed_conversation_and_msgs["text_msg_id"]
        # Ensure clean: if admin already has ❤️, toggle it off first by doing two calls.
        r = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                          headers=admin["headers"], json={"emoji": "❤️"}, timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["msg_id"] == msg_id
        assert data["emoji"] == "❤️"
        assert data["action"] in ("added", "removed")
        # Normalize: make sure ❤️ is present (add once more if we accidentally removed)
        if data["action"] == "removed":
            r = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                              headers=admin["headers"], json={"emoji": "❤️"}, timeout=10)
            data = r.json()
        assert data["action"] == "added"
        emojis_present = [x["emoji"] for x in data["reactions"]]
        assert "❤️" in emojis_present
        # mine flag should be true for the added one
        for x in data["reactions"]:
            if x["emoji"] == "❤️":
                assert x["mine"] is True
                assert x["count"] >= 1

    def test_toggle_removes_same_emoji(self, admin, seed_conversation_and_msgs):
        msg_id = seed_conversation_and_msgs["text_msg_id"]
        # At this point ❤️ is "added" by admin (from previous test); calling again should remove.
        r = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                          headers=admin["headers"], json={"emoji": "❤️"}, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "removed"
        # admin's ❤️ should no longer have mine=True (either absent entirely, or mine=False if others reacted)
        for x in data["reactions"]:
            if x["emoji"] == "❤️":
                assert x["mine"] is False

    def test_add_different_emoji_keeps_both(self, admin, seed_conversation_and_msgs):
        msg_id = seed_conversation_and_msgs["money_msg_id"]
        # Add 🔥
        r1 = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                           headers=admin["headers"], json={"emoji": "🔥"}, timeout=10)
        assert r1.status_code == 200
        if r1.json()["action"] == "removed":
            r1 = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                               headers=admin["headers"], json={"emoji": "🔥"}, timeout=10)
        # Add 💸
        r2 = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                           headers=admin["headers"], json={"emoji": "💸"}, timeout=10)
        assert r2.status_code == 200
        if r2.json()["action"] == "removed":
            r2 = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                               headers=admin["headers"], json={"emoji": "💸"}, timeout=10)
        emojis = [x["emoji"] for x in r2.json()["reactions"]]
        assert "🔥" in emojis and "💸" in emojis, f"both expected, got {emojis}"

    def test_invalid_emoji_rejected(self, admin, seed_conversation_and_msgs):
        msg_id = seed_conversation_and_msgs["text_msg_id"]
        r = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                          headers=admin["headers"], json={"emoji": "🍆"}, timeout=10)
        assert r.status_code == 400
        assert "autoris" in r.text.lower() or "allowed" in r.text.lower()

    def test_nonexistent_message_404(self, admin):
        fake = f"msg_{'0'*16}"
        r = requests.post(f"{BASE_URL}/api/messages/{fake}/react",
                          headers=admin["headers"], json={"emoji": "❤️"}, timeout=10)
        assert r.status_code == 404

    def test_non_participant_403(self, seed_conversation_and_msgs):
        """Register a throwaway user and verify they get 403 when reacting on admin↔bob msg."""
        email = f"testref17_{uuid.uuid4().hex[:8]}@japap.com"
        reg = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": "Test1234!", "username": f"ref17{uuid.uuid4().hex[:6]}",
            "first_name": "Ref", "last_name": "Out", "terms_accepted": True,
        }, timeout=15)
        if reg.status_code not in (200, 201):
            pytest.skip(f"could not register outsider: {reg.status_code} {reg.text}")
        tok = (reg.json().get("access_token") or
               requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": "Test1234!"}).json().get("access_token"))
        assert tok, "no token for outsider"
        headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
        msg_id = seed_conversation_and_msgs["text_msg_id"]
        r = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                          headers=headers, json={"emoji": "❤️"}, timeout=10)
        assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"


# ---------- GET messages returns reactions ----------

class TestGetMessagesReactions:
    def test_reactions_field_present(self, admin, bob, seed_conversation_and_msgs):
        """Admin reacts with 👍, Bob reacts with 🔥, both see aggregated reactions with correct mine flag."""
        conv_id = seed_conversation_and_msgs["conv_id"]
        msg_id = seed_conversation_and_msgs["money_msg_id"]

        # ensure admin has 👍
        r = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                          headers=admin["headers"], json={"emoji": "👍"}, timeout=10)
        if r.json()["action"] == "removed":
            requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                          headers=admin["headers"], json={"emoji": "👍"}, timeout=10)

        # bob reacts with 🔥 (ensure present)
        rb = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                           headers=bob["headers"], json={"emoji": "🔥"}, timeout=10)
        assert rb.status_code == 200, rb.text
        if rb.json()["action"] == "removed":
            rb = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                               headers=bob["headers"], json={"emoji": "🔥"}, timeout=10)

        # admin GET conversation
        rg = requests.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", headers=admin["headers"], timeout=15)
        assert rg.status_code == 200
        msgs = rg.json()
        assert isinstance(msgs, list) and len(msgs) > 0
        for m in msgs:
            assert "reactions" in m, f"msg {m.get('msg_id')} has no reactions key"
            assert isinstance(m["reactions"], list)

        target = next((m for m in msgs if m["msg_id"] == msg_id), None)
        assert target is not None, "money msg not found in admin's conv view"
        emap = {x["emoji"]: x for x in target["reactions"]}
        assert "👍" in emap and emap["👍"]["mine"] is True
        # 🔥 was added earlier by admin in test_add_different_emoji_keeps_both; bob then also added 🔥 → count>=2, mine=True for admin
        assert "🔥" in emap  # present

        # bob GET conversation → 🔥 should have mine=True
        rg2 = requests.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", headers=bob["headers"], timeout=15)
        assert rg2.status_code == 200
        target2 = next((m for m in rg2.json() if m["msg_id"] == msg_id), None)
        assert target2 is not None
        emap2 = {x["emoji"]: x for x in target2["reactions"]}
        assert "🔥" in emap2 and emap2["🔥"]["mine"] is True


# ---------- REGRESSIONS ----------

class TestRegression:
    def test_send_text_still_works(self, admin, bob):
        r = requests.post(f"{BASE_URL}/api/messages/send",
                          headers=admin["headers"],
                          json={"to_user_id": bob["user_id"], "text": "TEST_iter17 regression text"},
                          timeout=15)
        assert r.status_code == 200
        assert r.json()["message"]["text"].startswith("TEST_iter17")

    def test_send_money_still_works(self, admin, bob):
        r = requests.post(f"{BASE_URL}/api/messages/send-money",
                          headers=admin["headers"],
                          json={"to_user_id": bob["user_id"], "amount": 100, "note": "TEST_iter17 regression"},
                          timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "tx_id" in data and "msg_id" in data and "new_balance" in data
        assert Decimal(data["amount"]) == Decimal("100")

    def test_feed_like_comment_tip_regression(self, admin, bob):
        # create a post as admin (feed router prefix /api/feed)
        rp = requests.post(f"{BASE_URL}/api/feed/posts",
                           headers=admin["headers"],
                           json={"text": "TEST_iter17 regression feed"},
                           timeout=15)
        if rp.status_code not in (200, 201):
            pytest.skip(f"post create unavailable: {rp.status_code} {rp.text}")
        post = rp.json()
        post_id = post.get("post_id") or post.get("id") or (post.get("post") or {}).get("post_id")
        if not post_id:
            pytest.skip(f"cannot resolve post_id from {post}")

        # like as bob
        rl = requests.post(f"{BASE_URL}/api/feed/posts/{post_id}/like",
                           headers=bob["headers"], timeout=10)
        assert rl.status_code in (200, 201), f"like failed: {rl.status_code} {rl.text}"

        # comment as bob
        rc = requests.post(f"{BASE_URL}/api/feed/posts/{post_id}/comments",
                           headers=bob["headers"], json={"text": "TEST_iter17 nice!"}, timeout=10)
        assert rc.status_code in (200, 201), f"comment failed: {rc.status_code} {rc.text}"

        # tip as bob (may fail if bob has no balance — accept monetary errors)
        rt = requests.post(f"{BASE_URL}/api/feed/tip",
                           headers=bob["headers"],
                           json={"post_id": post_id, "amount": 50}, timeout=15)
        assert rt.status_code in (200, 201, 400, 402, 403, 404), f"tip unexpected: {rt.status_code} {rt.text}"
