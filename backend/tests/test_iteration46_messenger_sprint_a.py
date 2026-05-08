"""Iteration 46 — Sprint A Messenger tests (reply, forward, delivery ticks, socket stability).

Covers:
- POST /api/messages/conversations/{conv_id}/send with reply_to -> populated reply_to + reply_preview on read
- GET  /api/messages/conversations/{conv_id} -> reply_preview populated when reply_to set, null otherwise
- POST /api/messages/{msg_id}/forward -> is_forwarded=TRUE on new messages, counts correct, 404 if no access
- Forward silently skips target convs where user not a participant
- Socket.IO mark_delivered flips status 'sent' -> 'delivered' and emits messages_delivered
- Socket.IO mark_seen flips incoming sent/delivered -> 'seen' and emits messages_seen
- Socket.IO send_message with reply_to emits new_message with reply_preview populated
"""
import os
import asyncio
import pytest
import requests
import socketio

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
SOCKET_PATH = "/api/socket.io"

BOB = {"email": "bob@japap.com", "password": "Test1234!"}
CAROL = {"email": "carol@japap.com", "password": "Test1234!"}


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed: {email} -> {r.status_code} {r.text[:200]}"
    # Also extract access_token cookie for socket auth
    token = None
    for c in s.cookies:
        if c.name == "access_token":
            token = c.value
    return s, token


@pytest.fixture(scope="module")
def bob_session():
    return _login(BOB["email"], BOB["password"])


@pytest.fixture(scope="module")
def carol_session():
    return _login(CAROL["email"], CAROL["password"])


@pytest.fixture(scope="module")
def bob_carol_conv(bob_session):
    """Find or create a 1-1 conversation between bob and carol."""
    s, _ = bob_session
    # Fetch carol's user_id via search
    r = s.get(f"{BASE_URL}/api/users/search?q=carol", timeout=10)
    assert r.status_code == 200, r.text
    carol_uid = None
    for u in r.json():
        if (u.get("email") or "").lower() == CAROL["email"]:
            carol_uid = u["user_id"]
            break
        if (u.get("username") or "").lower().startswith("carol"):
            carol_uid = u["user_id"]
    assert carol_uid, "carol user not found via search"
    # Trigger conversation creation via send
    r2 = s.post(f"{BASE_URL}/api/messages/send",
                json={"to_user_id": carol_uid, "text": "seed-iter46"}, timeout=15)
    assert r2.status_code == 200, r2.text
    return r2.json()["conv_id"], carol_uid


# ---------- REST: reply / forward ----------
class TestReplyAndForward:
    def test_send_with_reply_to_populates_reply_preview(self, bob_session, bob_carol_conv):
        s, _ = bob_session
        conv_id, _ = bob_carol_conv
        # Send original
        orig = s.post(f"{BASE_URL}/api/messages/conversations/{conv_id}/send",
                      json={"text": "TEST_iter46_original"}, timeout=10)
        assert orig.status_code == 200, orig.text
        orig_id = orig.json()["msg_id"]
        # Reply
        rep = s.post(f"{BASE_URL}/api/messages/conversations/{conv_id}/send",
                     json={"text": "TEST_iter46_reply", "reply_to": orig_id}, timeout=10)
        assert rep.status_code == 200, rep.text
        rep_body = rep.json()
        assert rep_body["reply_to"] == orig_id
        # GET messages -> reply_preview populated
        g = s.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", timeout=10)
        assert g.status_code == 200
        msgs = g.json()
        found_reply = next((m for m in msgs if m["msg_id"] == rep_body["msg_id"]), None)
        found_orig = next((m for m in msgs if m["msg_id"] == orig_id), None)
        assert found_reply is not None
        assert found_reply["reply_preview"] is not None
        assert found_reply["reply_preview"]["msg_id"] == orig_id
        assert "TEST_iter46_original" in found_reply["reply_preview"]["text"]
        assert "sender_name" in found_reply["reply_preview"]
        # Non-reply message has reply_preview None
        assert found_orig is not None
        assert found_orig.get("reply_preview") is None

    def test_forward_to_own_conv_creates_is_forwarded_message(self, bob_session, bob_carol_conv):
        s, _ = bob_session
        conv_id, _ = bob_carol_conv
        # Create a msg to forward
        src = s.post(f"{BASE_URL}/api/messages/conversations/{conv_id}/send",
                     json={"text": "TEST_iter46_forwardable"}, timeout=10).json()
        # Forward to same conv (bob is a participant)
        r = s.post(f"{BASE_URL}/api/messages/{src['msg_id']}/forward",
                   json={"target_conv_ids": [conv_id]}, timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["forwarded"] == 1
        assert len(data["messages"]) == 1
        new_msg_id = data["messages"][0]["msg_id"]
        # GET and verify is_forwarded true on the new msg
        msgs = s.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", timeout=10).json()
        new_msg = next((m for m in msgs if m["msg_id"] == new_msg_id), None)
        assert new_msg is not None
        assert new_msg["is_forwarded"] is True
        assert "TEST_iter46_forwardable" in (new_msg["text"] or "")

    def test_forward_nonexistent_message_returns_404(self, bob_session, bob_carol_conv):
        s, _ = bob_session
        conv_id, _ = bob_carol_conv
        r = s.post(f"{BASE_URL}/api/messages/msg_doesnotexist/forward",
                   json={"target_conv_ids": [conv_id]}, timeout=10)
        assert r.status_code == 404

    def test_forward_silently_skips_convs_where_user_not_participant(self, bob_session, bob_carol_conv):
        """Forward to a conv where bob is not a participant (or non-existent) must be silently skipped,
        never errored. We use a bogus conv_id alongside the real one — only the real one should count."""
        sb, _ = bob_session
        conv_id, _ = bob_carol_conv
        src = sb.post(f"{BASE_URL}/api/messages/conversations/{conv_id}/send",
                      json={"text": "TEST_iter46_skip_probe"}, timeout=10).json()
        r = sb.post(f"{BASE_URL}/api/messages/{src['msg_id']}/forward",
                    json={"target_conv_ids": [conv_id, "conv_nonexistent_xyz"]}, timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["forwarded"] == 1
        assert all(m["conv_id"] == conv_id for m in data["messages"])


# ---------- Socket.IO: mark_delivered / mark_seen / send_message reply ----------
async def _connect(token):
    sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
    events = {"new_message": [], "messages_delivered": [], "messages_seen": []}

    @sio.on("new_message")
    async def _nm(d):
        events["new_message"].append(d)

    @sio.on("messages_delivered")
    async def _md(d):
        events["messages_delivered"].append(d)

    @sio.on("messages_seen")
    async def _ms(d):
        events["messages_seen"].append(d)

    await sio.connect(BASE_URL, socketio_path=SOCKET_PATH, transports=["websocket"], wait_timeout=10)
    await sio.emit("authenticate", {"token": token})
    await asyncio.sleep(0.4)
    return sio, events


async def _socket_reply_flow(bt, ct, sb, conv_id):
    bob_sio, bob_events = await _connect(bt)
    carol_sio, carol_events = await _connect(ct)
    try:
        await bob_sio.emit("join_conversation", {"conv_id": conv_id})
        await carol_sio.emit("join_conversation", {"conv_id": conv_id})
        await asyncio.sleep(0.3)
        orig = sb.post(f"{BASE_URL}/api/messages/conversations/{conv_id}/send",
                       json={"text": "TEST_iter46_sock_orig"}, timeout=10).json()
        carol_events["new_message"].clear()
        await bob_sio.emit("send_message",
                           {"conv_id": conv_id, "text": "TEST_iter46_sock_reply", "reply_to": orig["msg_id"]})
        await asyncio.sleep(1.2)
        replies = [m for m in carol_events["new_message"] if m.get("text") == "TEST_iter46_sock_reply"]
        assert len(replies) >= 1, f"carol did not receive reply; events={carol_events['new_message']}"
        assert replies[0].get("reply_preview") is not None
        assert replies[0]["reply_preview"]["msg_id"] == orig["msg_id"]
    finally:
        await bob_sio.disconnect()
        await carol_sio.disconnect()


def test_socket_send_with_reply_emits_preview(bob_session, carol_session, bob_carol_conv):
    conv_id, _ = bob_carol_conv
    sb, bt = bob_session
    sc, ct = carol_session
    if not bt or not ct:
        pytest.skip("missing access_token cookies for socket auth")
    asyncio.run(_socket_reply_flow(bt, ct, sb, conv_id))


async def _socket_delivery_flow(bt, ct, sb, conv_id):
    bob_sio, bob_events = await _connect(bt)
    carol_sio, carol_events = await _connect(ct)
    try:
        await bob_sio.emit("join_conversation", {"conv_id": conv_id})
        await carol_sio.emit("join_conversation", {"conv_id": conv_id})
        await asyncio.sleep(0.3)
        msg = sb.post(f"{BASE_URL}/api/messages/conversations/{conv_id}/send",
                      json={"text": "TEST_iter46_delivery"}, timeout=10).json()
        await asyncio.sleep(0.4)
        bob_events["messages_delivered"].clear()
        await carol_sio.emit("mark_delivered", {"conv_id": conv_id, "msg_ids": [msg["msg_id"]]})
        await asyncio.sleep(1.0)
        assert any(msg["msg_id"] in (e.get("msg_ids") or []) for e in bob_events["messages_delivered"]), \
            f"bob never got messages_delivered; events={bob_events['messages_delivered']}"
        bob_events["messages_seen"].clear()
        await carol_sio.emit("mark_seen", {"conv_id": conv_id})
        await asyncio.sleep(1.0)
        assert len(bob_events["messages_seen"]) >= 1, "bob never got messages_seen event"
        msgs = sb.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", timeout=10).json()
        target = next((m for m in msgs if m["msg_id"] == msg["msg_id"]), None)
        assert target is not None
        assert target["status"] == "seen", f"expected seen, got {target['status']}"
    finally:
        await bob_sio.disconnect()
        await carol_sio.disconnect()


def test_socket_mark_delivered_and_seen(bob_session, carol_session, bob_carol_conv):
    conv_id, _ = bob_carol_conv
    sb, bt = bob_session
    sc, ct = carol_session
    if not bt or not ct:
        pytest.skip("missing access_token cookies for socket auth")
    asyncio.run(_socket_delivery_flow(bt, ct, sb, conv_id))
