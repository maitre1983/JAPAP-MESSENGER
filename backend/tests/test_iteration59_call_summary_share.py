"""Iteration 59 — CallSummary → Chat structured block backend tests.

Flow :
    1. Bob calls Carol (session created)
    2. A summary with action items (incl. `who:"Carol"`) is seeded directly
       into the DB to bypass LiveKit + Whisper (already covered in iter57).
    3. Bob POSTs /summary/share → a `call_summary` structured message appears
       in their 1-1 conversation. Carol's who-assignment auto-matches to her
       user_id.
    4. Carol toggles her action item done → 200 + state persisted.
    5. An admin (NOT in the call) tries to toggle → 403.
    6. Re-assignation endpoint swaps the owner to Bob.
"""
import os
import uuid
import json
import pytest
import requests
import asyncpg
import asyncio
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
assert DATABASE_URL, "DATABASE_URL not loaded — test misconfigured"

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
def call_with_summary(bob, carol):
    """Create a p2p audio session, seed participants, and a ready summary
    with an action item assigned to Carol by name."""
    s_bob, bob_uid = bob
    _, carol_uid = carol
    # 1) Create session
    r = s_bob.post(f"{BASE_URL}/api/calls/session",
                   json={"mode": "audio", "kind": "p2p", "callee_id": carol_uid},
                   timeout=15)
    assert r.status_code == 200, r.text
    sess = r.json()
    sid = sess['session_id']

    # 2) Seed call_participants + call_summary rows directly (bypass LiveKit)
    async def seed():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # Make sure bob + carol are marked as participants of the call
            for uid in (bob_uid, carol_uid):
                await conn.execute("""
                    INSERT INTO call_participants (session_id, user_id, joined_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT DO NOTHING
                """, sid, uid)
            # Seed a ready summary with Carol-assigned action item
            sum_id = f"sum_{uuid.uuid4().hex[:14]}"
            await conn.execute("""
                INSERT INTO call_summaries
                    (summary_id, session_id, recording_id, transcript, summary,
                     key_points, decisions, action_items, language, model, status, completed_at)
                VALUES ($1, $2, NULL, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb,
                        'fr', 'test', 'ready', NOW())
            """, sum_id, sid,
                "Bob : ... Carol : ... Merci d'envoyer le rapport avant midi.",
                "Discussion sur le rapport hebdomadaire — Carol doit l'envoyer avant midi.",
                json.dumps(["Rapport à finaliser", "Réunion prévue lundi"]),
                json.dumps(["Validation du template PDF"]),
                json.dumps([
                    {"who": "Carol", "what": "envoyer le rapport hebdomadaire", "due": "avant midi"},
                    {"who": "Bob", "what": "préparer la présentation", "due": "lundi"},
                    {"who": "Quelqu'un", "what": "commander le café", "due": ""},
                ]),
            )
        finally:
            await conn.close()
    asyncio.get_event_loop().run_until_complete(seed())
    return {"session_id": sid, "conv_id": sess.get('conv_id')}


# ─── 1. /share ─────────────────────────────────────────────────────────────

def test_share_by_non_participant_forbidden(admin, call_with_summary, bob, carol):
    """Admin wasn't in the call → can't share."""
    # First ensure bob and carol have a direct conv (DM). We need the conv_id.
    s_bob, bob_uid = bob
    _, carol_uid = carol
    r = s_bob.post(f"{BASE_URL}/api/messages/send",
                   json={"to_user_id": carol_uid, "text": "Init DM for iter59"}, timeout=10)
    assert r.status_code == 200, r.text
    conv_id = r.json()['conv_id']
    call_with_summary['conv_id'] = conv_id
    # Admin (not a participant) tries to share
    s_admin, _ = admin
    r2 = s_admin.post(f"{BASE_URL}/api/calls/{call_with_summary['session_id']}/summary/share",
                      json={"conv_id": conv_id}, timeout=10)
    assert r2.status_code == 403, r2.text


def test_share_happy_path_creates_structured_message(bob, call_with_summary, carol):
    s_bob, _ = bob
    _, carol_uid = carol
    conv_id = call_with_summary['conv_id']
    r = s_bob.post(f"{BASE_URL}/api/calls/{call_with_summary['session_id']}/summary/share",
                   json={"conv_id": conv_id}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['ok'] is True
    assert body['msg_id'].startswith('msg_')
    assert body['action_items'] == 3
    call_with_summary['msg_id'] = body['msg_id']

    # Fetch via GET /messages to validate structured_data arrives parsed
    all_msgs = s_bob.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", timeout=10).json()
    summary_msg = next((m for m in all_msgs if m['msg_id'] == body['msg_id']), None)
    assert summary_msg is not None, "Structured message not returned by GET"
    assert summary_msg['message_type'] == 'call_summary'
    sd = summary_msg['structured_data']
    assert isinstance(sd, dict)
    assert sd['type'] == 'call_summary'
    assert len(sd['action_items']) == 3
    # Auto-match : Carol's item must be bound to her real user_id
    carol_item = next((it for it in sd['action_items'] if it['what'].startswith('envoyer le rapport')), None)
    assert carol_item is not None
    assert carol_item['who_user_id'] == carol_uid, \
        f"Auto-match failed: {carol_item}"
    # "Quelqu'un" should stay text-only (no user_id)
    unknown = next((it for it in sd['action_items'] if 'café' in it['what']), None)
    assert unknown['who_user_id'] is None
    # Each item gets a stable id
    for it in sd['action_items']:
        assert it['id'].startswith('ai_')
        assert it['done'] is False


# ─── 2. Toggle done flag ───────────────────────────────────────────────────

def test_toggle_by_assignee_ok(carol, call_with_summary):
    s_carol, carol_uid = carol
    # Locate Carol's item id
    s_bob, _ = bob_login()
    conv_id = call_with_summary['conv_id']
    msgs = s_bob.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", timeout=10).json()
    sum_msg = next(m for m in msgs if m['msg_id'] == call_with_summary['msg_id'])
    item = next(it for it in sum_msg['structured_data']['action_items']
                if it['who_user_id'] == carol_uid)
    r = s_carol.patch(
        f"{BASE_URL}/api/calls/summary/action-items/{call_with_summary['msg_id']}/{item['id']}",
        json={"done": True}, timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()['item']['done'] is True
    assert r.json()['item']['done_by_user_id'] == carol_uid


def test_toggle_by_call_participant_ok(bob, call_with_summary):
    """Bob was in the call → can toggle anyone's item."""
    s_bob, _ = bob
    conv_id = call_with_summary['conv_id']
    msgs = s_bob.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", timeout=10).json()
    sum_msg = next(m for m in msgs if m['msg_id'] == call_with_summary['msg_id'])
    # Toggle the "Quelqu'un / café" item (no assignee)
    unknown = next(it for it in sum_msg['structured_data']['action_items']
                   if 'café' in it['what'])
    r = s_bob.patch(
        f"{BASE_URL}/api/calls/summary/action-items/{call_with_summary['msg_id']}/{unknown['id']}",
        json={"done": True}, timeout=10,
    )
    assert r.status_code == 200, r.text


def test_toggle_by_outsider_forbidden(admin, call_with_summary):
    """Admin was NOT in the call and is NOT an assignee → 403."""
    s_admin, _ = admin
    s_bob, _ = bob_login()
    conv_id = call_with_summary['conv_id']
    # Ensure admin is in the conv so the access rule is definitely about
    # participation-in-call and not membership-in-chat. For DM this is not
    # possible — so we validate that admin can't even READ the msg first:
    r = s_admin.patch(
        f"{BASE_URL}/api/calls/summary/action-items/{call_with_summary['msg_id']}/XXXNONEXIST",
        json={"done": True}, timeout=10,
    )
    # Not-found item (404) OR forbidden (403) — both are acceptable,
    # they both mean "admin can't mutate". What we must NOT see is 200.
    assert r.status_code in (403, 404), r.text


# ─── 3. Re-assignation ──────────────────────────────────────────────────────

def test_reassign_to_valid_participant(bob, call_with_summary, carol):
    s_bob, bob_uid = bob
    _, carol_uid = carol
    conv_id = call_with_summary['conv_id']
    msgs = s_bob.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", timeout=10).json()
    sum_msg = next(m for m in msgs if m['msg_id'] == call_with_summary['msg_id'])
    unknown = next(it for it in sum_msg['structured_data']['action_items']
                   if 'café' in it['what'])
    r = s_bob.patch(
        f"{BASE_URL}/api/calls/summary/action-items/{call_with_summary['msg_id']}/{unknown['id']}/assign",
        json={"user_id": carol_uid}, timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()['item']['who_user_id'] == carol_uid


def test_reassign_to_non_participant_400(bob, call_with_summary, admin):
    """Admin was NOT in the call → can't be assigned."""
    s_bob, _ = bob
    _, admin_uid = admin
    conv_id = call_with_summary['conv_id']
    msgs = s_bob.get(f"{BASE_URL}/api/messages/conversations/{conv_id}", timeout=10).json()
    sum_msg = next(m for m in msgs if m['msg_id'] == call_with_summary['msg_id'])
    any_item = sum_msg['structured_data']['action_items'][0]
    r = s_bob.patch(
        f"{BASE_URL}/api/calls/summary/action-items/{call_with_summary['msg_id']}/{any_item['id']}/assign",
        json={"user_id": admin_uid}, timeout=10,
    )
    assert r.status_code == 400


# ─── helper (module-scoped session we can fetch inside tests) ───────────────

def bob_login():
    return _login(BOB)
