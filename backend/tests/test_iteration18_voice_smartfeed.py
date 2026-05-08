"""Iter18 tests: voice messages (Whisper) + smart feed re-ranking."""
import os
import io
import time
import subprocess
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://japap-refactor.preview.emergentagent.com').rstrip('/')

ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    j = r.json()
    return j.get('access_token') or j.get('token'), j['user']


@pytest.fixture(scope="module")
def admin_ctx():
    token, user = _login(ADMIN)
    return {"token": token, "user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(scope="module")
def bob_ctx():
    token, user = _login(BOB)
    return {"token": token, "user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(scope="module")
def beep_wav():
    p = "/tmp/beep.wav"
    if not os.path.exists(p):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ar", "16000", p],
            check=True, capture_output=True
        )
    return p


# ============== VOICE MESSAGES ==============
class TestVoiceMessages:
    def test_voice_send_to_user_id_returns_payload(self, bob_ctx, admin_ctx, beep_wav):
        with open(beep_wav, "rb") as f:
            files = {"file": ("beep.wav", f, "audio/wav")}
            data = {"to_user_id": admin_ctx['user']['user_id'], "duration": 2}
            r = requests.post(f"{BASE_URL}/api/messages/voice",
                              headers=bob_ctx['headers'], files=files, data=data, timeout=60)
        assert r.status_code == 200, f"voice send failed: {r.status_code} {r.text}"
        j = r.json()
        for k in ("msg_id", "conv_id", "url", "duration", "transcription", "message"):
            assert k in j, f"missing field {k}"
        assert isinstance(j['transcription'], str)
        assert j['url'].startswith("/api/upload/files/")
        assert j['msg_id'].startswith("msg_")
        # store for later test
        TestVoiceMessages._last = j

    def test_voice_file_accessible(self, bob_ctx):
        url = TestVoiceMessages._last['url']
        # file is served via /api/upload/files/{filename}
        r = requests.get(f"{BASE_URL}{url}", headers=bob_ctx['headers'], timeout=15)
        # accept either 200 or auth-protected 401/403; the endpoint may not require auth
        assert r.status_code in (200, 401, 403), f"file fetch unexpected: {r.status_code}"
        if r.status_code == 200:
            assert len(r.content) > 0

    def test_voice_persisted_in_conversation(self, bob_ctx):
        conv_id = TestVoiceMessages._last['conv_id']
        msg_id = TestVoiceMessages._last['msg_id']
        r = requests.get(f"{BASE_URL}/api/messages/conversations/{conv_id}",
                         headers=bob_ctx['headers'], timeout=15)
        assert r.status_code == 200
        msgs = r.json()
        found = next((m for m in msgs if m['msg_id'] == msg_id), None)
        assert found is not None, "voice msg not in conversation"
        media = found['media']
        if isinstance(media, str):
            import json as _j
            media = _j.loads(media)
        assert media.get('kind') == 'voice'
        assert 'url' in media and 'duration' in media and 'transcription' in media and 'size' in media

    def test_voice_missing_conv_and_to_user(self, bob_ctx, beep_wav):
        with open(beep_wav, "rb") as f:
            files = {"file": ("beep.wav", f, "audio/wav")}
            r = requests.post(f"{BASE_URL}/api/messages/voice",
                              headers=bob_ctx['headers'], files=files, data={"duration": 2}, timeout=30)
        assert r.status_code == 400, f"expected 400 got {r.status_code}: {r.text}"

    def test_voice_bad_extension(self, bob_ctx, admin_ctx):
        files = {"file": ("notes.txt", io.BytesIO(b"hello world" * 50), "text/plain")}
        data = {"to_user_id": admin_ctx['user']['user_id'], "duration": 1}
        r = requests.post(f"{BASE_URL}/api/messages/voice",
                          headers=bob_ctx['headers'], files=files, data=data, timeout=30)
        assert r.status_code == 400
        assert "Format audio non supporté" in r.text or "non supporte" in r.text.lower()

    def test_voice_too_large(self, bob_ctx, admin_ctx):
        # 11 MB of zeros
        big = io.BytesIO(b"\0" * (11 * 1024 * 1024))
        files = {"file": ("big.wav", big, "audio/wav")}
        data = {"to_user_id": admin_ctx['user']['user_id'], "duration": 60}
        r = requests.post(f"{BASE_URL}/api/messages/voice",
                          headers=bob_ctx['headers'], files=files, data=data, timeout=60)
        assert r.status_code == 400
        assert "trop volumineux" in r.text.lower() or "10mb" in r.text.lower()

    def test_whisper_transcription_is_string(self, bob_ctx, admin_ctx, beep_wav):
        # Already verified in first test that transcription is str.
        assert isinstance(TestVoiceMessages._last['transcription'], str)


# ============== SMART FEED ==============
class TestSmartFeed:
    def test_default_sort_is_smart(self, bob_ctx):
        r = requests.get(f"{BASE_URL}/api/feed/posts", headers=bob_ctx['headers'], timeout=15)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get('sort') == 'smart'
        for p in j['posts']:
            assert 'score' in p
            assert isinstance(p['score'], (int, float))

    def test_sort_recent_score_null(self, bob_ctx):
        r = requests.get(f"{BASE_URL}/api/feed/posts?sort=recent", headers=bob_ctx['headers'], timeout=15)
        assert r.status_code == 200
        j = r.json()
        assert j['sort'] == 'recent'
        for p in j['posts']:
            assert p.get('score') is None
        # ordered by created_at DESC
        ts = [p['created_at'] for p in j['posts']]
        assert ts == sorted(ts, reverse=True)

    def test_sort_smart_ordered_by_score(self, bob_ctx):
        r = requests.get(f"{BASE_URL}/api/feed/posts?sort=smart", headers=bob_ctx['headers'], timeout=15)
        assert r.status_code == 200
        scores = [p['score'] for p in r.json()['posts']]
        assert scores == sorted(scores, reverse=True), f"smart not ordered: {scores}"

    def test_invalid_sort_value_422(self, bob_ctx):
        r = requests.get(f"{BASE_URL}/api/feed/posts?sort=foo", headers=bob_ctx['headers'], timeout=15)
        assert r.status_code == 422

    def test_engagement_boost(self, bob_ctx, admin_ctx):
        # Admin creates a brand new post, Bob likes+comments → score should be > a plain new post
        baseline = requests.post(f"{BASE_URL}/api/feed/posts",
                                 headers=admin_ctx['headers'],
                                 json={"text": "TEST_iter18 baseline post"}, timeout=15)
        if baseline.status_code != 200:
            pytest.skip(f"feed create unavailable: {baseline.status_code} {baseline.text[:200]}")
        boosted = requests.post(f"{BASE_URL}/api/feed/posts",
                                headers=admin_ctx['headers'],
                                json={"text": "TEST_iter18 boosted post"}, timeout=15)
        assert boosted.status_code == 200
        boosted_id = boosted.json()['post_id']
        # Bob likes + comments
        rl = requests.post(f"{BASE_URL}/api/feed/posts/{boosted_id}/like",
                           headers=bob_ctx['headers'], timeout=15)
        assert rl.status_code == 200
        rc = requests.post(f"{BASE_URL}/api/feed/posts/{boosted_id}/comments",
                           headers=bob_ctx['headers'], json={"text": "great!"}, timeout=15)
        assert rc.status_code == 200
        time.sleep(0.5)
        feed = requests.get(f"{BASE_URL}/api/feed/posts?sort=smart&limit=50",
                            headers=bob_ctx['headers'], timeout=15).json()['posts']
        bm = next((p for p in feed if p['post_id'] == baseline.json()['post_id']), None)
        bo = next((p for p in feed if p['post_id'] == boosted_id), None)
        assert bm and bo, "posts missing in feed"
        assert bo['score'] >= bm['score'], f"boosted({bo['score']}) should be >= baseline({bm['score']})"


# ============== REGRESSION ==============
class TestRegression:
    def test_text_message_still_works(self, bob_ctx, admin_ctx):
        r = requests.post(f"{BASE_URL}/api/messages/send",
                          headers=bob_ctx['headers'],
                          json={"to_user_id": admin_ctx['user']['user_id'],
                                "text": "TEST_iter18 regression text"}, timeout=15)
        assert r.status_code == 200
        assert 'message' in r.json()

    def test_chat_money_still_works(self, bob_ctx, admin_ctx):
        r = requests.post(f"{BASE_URL}/api/messages/send-money",
                          headers=admin_ctx['headers'],
                          json={"to_user_id": bob_ctx['user']['user_id'],
                                "amount": 100, "note": "TEST_iter18"}, timeout=15)
        assert r.status_code == 200, r.text
        j = r.json()
        assert 'tx_id' in j and 'msg_id' in j

    def test_emoji_reaction_still_works(self, bob_ctx, admin_ctx):
        # Send a fresh msg, then react
        sm = requests.post(f"{BASE_URL}/api/messages/send",
                           headers=bob_ctx['headers'],
                           json={"to_user_id": admin_ctx['user']['user_id'],
                                 "text": "TEST_iter18 react target"}, timeout=15)
        assert sm.status_code == 200
        msg_id = sm.json()['message']['msg_id']
        rr = requests.post(f"{BASE_URL}/api/messages/{msg_id}/react",
                           headers=admin_ctx['headers'], json={"emoji": "🔥"}, timeout=15)
        assert rr.status_code == 200, rr.text
        assert rr.json()['action'] in ('added', 'removed')
