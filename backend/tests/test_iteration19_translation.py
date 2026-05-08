"""Iteration 19 — Live translation via Claude Sonnet 4.5 (Emergent LLM key).
Covers: GET /supported-languages, POST /{msg_id}/translate, caching, errors,
voice transcription translation, regression on reactions/voice/chat-money/text.
"""
import os
import io
import time
import json
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    j = r.json()
    return j["access_token"], j["user"]["user_id"]


@pytest.fixture(scope="module")
def admin_ctx():
    tok, uid = _login(ADMIN)
    return {"headers": {"Authorization": f"Bearer {tok}"}, "user_id": uid}


@pytest.fixture(scope="module")
def bob_ctx():
    tok, uid = _login(BOB)
    return {"headers": {"Authorization": f"Bearer {tok}"}, "user_id": uid}


@pytest.fixture(scope="module")
def english_msg(admin_ctx, bob_ctx):
    """Admin sends an English text message to Bob — Bob will translate it."""
    text = "Hey brother! How are you doing today? See you at 7pm at the restaurant 🔥"
    r = requests.post(
        f"{BASE_URL}/api/messages/send",
        headers=admin_ctx["headers"],
        json={"to_user_id": bob_ctx["user_id"], "text": text},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    j = r.json()
    return {"msg_id": j["message"]["msg_id"], "conv_id": j["conv_id"], "text": text}


# ---------------- SUPPORTED LANGUAGES ----------------
def test_supported_languages(bob_ctx):
    r = requests.get(f"{BASE_URL}/api/messages/supported-languages",
                     headers=bob_ctx["headers"], timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "languages" in data
    codes = {lg["code"] for lg in data["languages"]}
    expected = {"fr", "en", "pt", "es", "ar", "sw", "ln", "yo"}
    assert expected.issubset(codes), f"missing langs: {expected - codes}"
    assert len(data["languages"]) == 8
    for lg in data["languages"]:
        assert "name" in lg and lg["name"]


# ---------------- TRANSLATE HAPPY PATH ----------------
def test_translate_en_to_fr_fresh(bob_ctx, english_msg):
    r = requests.post(
        f"{BASE_URL}/api/messages/{english_msg['msg_id']}/translate",
        headers=bob_ctx["headers"], json={"target_lang": "fr"}, timeout=20,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["msg_id"] == english_msg["msg_id"]
    assert d["target_lang"] == "fr"
    assert d["translated_text"], "empty translation"
    # Should be French — heuristic check: contains common FR words
    txt_lower = d["translated_text"].lower()
    assert any(w in txt_lower for w in ["bonjour", "salut", "frère", "comment", "vas", "restaurant", "ce soir", "19h", "aujourd"]), \
        f"translation doesn't look French: {d['translated_text']}"
    # Should NOT equal source
    assert d["translated_text"].strip() != english_msg["text"].strip()
    assert d["cached"] is False
    # detected_lang should be 'en' (Claude usually detects)
    assert d.get("detected_lang", "").lower() in ("en", "")  # tolerate empty


def test_translate_en_to_fr_cached(bob_ctx, english_msg):
    """Second call must return cached=True with same text, no extra LLM call."""
    t0 = time.time()
    r = requests.post(
        f"{BASE_URL}/api/messages/{english_msg['msg_id']}/translate",
        headers=bob_ctx["headers"], json={"target_lang": "fr"}, timeout=10,
    )
    elapsed = time.time() - t0
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cached"] is True
    assert d["translated_text"]
    # Cached should be fast (<3s)
    assert elapsed < 5.0, f"cached call too slow: {elapsed:.2f}s"


def test_translate_en_to_sw_fresh(bob_ctx, english_msg):
    r = requests.post(
        f"{BASE_URL}/api/messages/{english_msg['msg_id']}/translate",
        headers=bob_ctx["headers"], json={"target_lang": "sw"}, timeout=20,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["target_lang"] == "sw"
    assert d["translated_text"]
    assert d["cached"] is False


# ---------------- TRANSLATE ERROR PATHS ----------------
def test_translate_unsupported_lang(bob_ctx, english_msg):
    r = requests.post(
        f"{BASE_URL}/api/messages/{english_msg['msg_id']}/translate",
        headers=bob_ctx["headers"], json={"target_lang": "xx"}, timeout=10,
    )
    assert r.status_code == 400
    assert "non support" in r.json().get("detail", "").lower() or "supported" in r.json().get("detail", "").lower()


def test_translate_nonexistent_msg(bob_ctx):
    r = requests.post(
        f"{BASE_URL}/api/messages/msg_doesnotexist123/translate",
        headers=bob_ctx["headers"], json={"target_lang": "fr"}, timeout=10,
    )
    assert r.status_code == 404


def test_translate_not_participant(english_msg):
    """Create a 3rd user/login who is not in the conv. We don't have a 3rd seeded
    user, so we register one on the fly."""
    suffix = f"trtest{int(time.time())}"
    reg = requests.post(f"{BASE_URL}/api/auth/register", json={
        "username": suffix,
        "email": f"{suffix}@japap.com",
        "password": "Test1234!",
        "first_name": "Tr",
        "last_name": "Test",
    }, timeout=15)
    if reg.status_code not in (200, 201):
        pytest.skip(f"cannot register 3rd user: {reg.status_code} {reg.text}")
    j = reg.json()
    tok = j.get("access_token") or j.get("token")
    if not tok:
        # try login
        tok, _ = _login({"email": f"{suffix}@japap.com", "password": "Test1234!"})
    r = requests.post(
        f"{BASE_URL}/api/messages/{english_msg['msg_id']}/translate",
        headers={"Authorization": f"Bearer {tok}"},
        json={"target_lang": "fr"}, timeout=10,
    )
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"


def test_translate_money_message(admin_ctx, bob_ctx):
    """Send 50 XAF in chat then try translate → 400 'Rien à traduire'."""
    r = requests.post(
        f"{BASE_URL}/api/messages/send-money",
        headers=admin_ctx["headers"],
        json={"to_user_id": bob_ctx["user_id"], "amount": 50, "note": ""},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"chat-money send failed: {r.status_code} {r.text}")
    msg_id = r.json()["msg_id"]
    tr = requests.post(
        f"{BASE_URL}/api/messages/{msg_id}/translate",
        headers=bob_ctx["headers"], json={"target_lang": "fr"}, timeout=10,
    )
    assert tr.status_code == 400
    assert "rien" in tr.json().get("detail", "").lower() or "traduire" in tr.json().get("detail", "").lower()


# ---------------- VOICE MESSAGE TRANSLATION ----------------
@pytest.fixture(scope="module")
def voice_msg(admin_ctx, bob_ctx):
    """Send a voice message and ensure it has a transcription. We use a tiny webm
    payload — Whisper may hallucinate or return empty; if empty, skip the voice tests."""
    # Generate a small webm placeholder (won't be valid audio but server requires >=256 bytes)
    audio_bytes = b"OggS" + b"\x00" * 600  # at least 256 bytes
    files = {"file": ("voice_test.webm", io.BytesIO(audio_bytes), "audio/webm")}
    data = {"to_user_id": bob_ctx["user_id"], "duration": "3"}
    r = requests.post(
        f"{BASE_URL}/api/messages/voice",
        headers=admin_ctx["headers"], files=files, data=data, timeout=30,
    )
    if r.status_code != 200:
        pytest.skip(f"voice send failed: {r.status_code} {r.text}")
    j = r.json()
    if not (j.get("transcription") or "").strip():
        pytest.skip("voice transcription empty — cannot test translation")
    return j


def test_translate_voice_transcription(bob_ctx, voice_msg):
    msg_id = voice_msg["msg_id"]
    r = requests.post(
        f"{BASE_URL}/api/messages/{msg_id}/translate",
        headers=bob_ctx["headers"], json={"target_lang": "en"}, timeout=20,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["translated_text"]
    assert d["source_text"].strip() == voice_msg["transcription"].strip()
    assert d["cached"] is False
    # second call cached
    r2 = requests.post(
        f"{BASE_URL}/api/messages/{msg_id}/translate",
        headers=bob_ctx["headers"], json={"target_lang": "en"}, timeout=10,
    )
    assert r2.status_code == 200
    assert r2.json()["cached"] is True


# ---------------- REGRESSION ----------------
def test_regression_text_send(admin_ctx, bob_ctx):
    r = requests.post(f"{BASE_URL}/api/messages/send",
                      headers=admin_ctx["headers"],
                      json={"to_user_id": bob_ctx["user_id"], "text": "TEST_iter19 regression"},
                      timeout=10)
    assert r.status_code == 200
    assert r.json()["message"]["text"] == "TEST_iter19 regression"


def test_regression_react(admin_ctx, bob_ctx, english_msg):
    r = requests.post(f"{BASE_URL}/api/messages/{english_msg['msg_id']}/react",
                      headers=bob_ctx["headers"], json={"emoji": "🔥"}, timeout=10)
    assert r.status_code == 200
    assert r.json()["msg_id"] == english_msg["msg_id"]
    # toggle off to leave state clean
    requests.post(f"{BASE_URL}/api/messages/{english_msg['msg_id']}/react",
                  headers=bob_ctx["headers"], json={"emoji": "🔥"}, timeout=10)


def test_regression_smart_feed(bob_ctx):
    r = requests.get(f"{BASE_URL}/api/feed/posts?sort=smart&limit=10",
                     headers=bob_ctx["headers"], timeout=15)
    assert r.status_code == 200
    j = r.json()
    # Accept either {posts:[...]} or [...]
    posts = j.get("posts") if isinstance(j, dict) else j
    assert isinstance(posts, list)


def test_regression_conversations_list(bob_ctx):
    r = requests.get(f"{BASE_URL}/api/messages/conversations",
                     headers=bob_ctx["headers"], timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
