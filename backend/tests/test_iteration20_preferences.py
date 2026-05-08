"""Iteration 20 — Auto-translation Preferences (preferred_lang).
Covers PUT /api/auth/preferences validation + persistence, /me + /login include
preferred_lang, regression on /messages/{id}/translate (cached + fresh).
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN = {"email": "admin@japap.com", "password": "JapapAdmin2024!"}
BOB = {"email": "bob@japap.com", "password": "Test1234!"}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def admin_ctx():
    j = _login(ADMIN)
    return {"headers": {"Authorization": f"Bearer {j['access_token']}"},
            "user": j["user"]}


@pytest.fixture(scope="module")
def bob_ctx():
    j = _login(BOB)
    return {"headers": {"Authorization": f"Bearer {j['access_token']}"},
            "user": j["user"]}


# ---------- /api/auth/login response includes preferred_lang ----------
def test_login_response_includes_preferred_lang(bob_ctx):
    user = bob_ctx["user"]
    assert "preferred_lang" in user, f"login response missing preferred_lang: keys={list(user.keys())}"


def test_login_admin_includes_preferred_lang(admin_ctx):
    assert "preferred_lang" in admin_ctx["user"]


# ---------- /api/auth/me includes preferred_lang ----------
def test_me_includes_preferred_lang(bob_ctx):
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=bob_ctx["headers"], timeout=10)
    assert r.status_code == 200, r.text
    me = r.json()
    assert "preferred_lang" in me
    # Must be string or null
    assert me["preferred_lang"] is None or isinstance(me["preferred_lang"], str)


# ---------- PUT /api/auth/preferences happy path ----------
def test_set_preferred_lang_fr(bob_ctx):
    r = requests.put(f"{BASE_URL}/api/auth/preferences",
                     headers=bob_ctx["headers"],
                     json={"preferred_lang": "fr"}, timeout=10)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("preferred_lang") == "fr"
    # Verify persistence via /me
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=bob_ctx["headers"], timeout=10).json()
    assert me["preferred_lang"] == "fr"


def test_set_preferred_lang_en(bob_ctx):
    r = requests.put(f"{BASE_URL}/api/auth/preferences",
                     headers=bob_ctx["headers"],
                     json={"preferred_lang": "en"}, timeout=10)
    assert r.status_code == 200
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=bob_ctx["headers"], timeout=10).json()
    assert me["preferred_lang"] == "en"


# ---------- PUT /api/auth/preferences validation ----------
def test_set_unsupported_lang_returns_400(bob_ctx):
    r = requests.put(f"{BASE_URL}/api/auth/preferences",
                     headers=bob_ctx["headers"],
                     json={"preferred_lang": "zz"}, timeout=10)
    assert r.status_code == 400
    detail = r.json().get("detail", "").lower()
    assert "non support" in detail or "supported" in detail or "langue" in detail


def test_set_empty_lang_disables(bob_ctx):
    r = requests.put(f"{BASE_URL}/api/auth/preferences",
                     headers=bob_ctx["headers"],
                     json={"preferred_lang": ""}, timeout=10)
    assert r.status_code == 200
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=bob_ctx["headers"], timeout=10).json()
    assert me["preferred_lang"] in (None, "")


def test_unauthenticated_preferences_blocked():
    r = requests.put(f"{BASE_URL}/api/auth/preferences",
                     json={"preferred_lang": "fr"}, timeout=10)
    assert r.status_code in (401, 403)


def test_supported_langs_all_accepted(bob_ctx):
    """All 8 supported langs from messaging.py whitelist."""
    for lang in ["fr", "en", "pt", "es", "ar", "sw", "ln", "yo"]:
        r = requests.put(f"{BASE_URL}/api/auth/preferences",
                         headers=bob_ctx["headers"],
                         json={"preferred_lang": lang}, timeout=10)
        assert r.status_code == 200, f"{lang} rejected: {r.text}"


# ---------- Restore Bob to fr (matches credentials note) ----------
def test_restore_bob_to_fr(bob_ctx):
    r = requests.put(f"{BASE_URL}/api/auth/preferences",
                     headers=bob_ctx["headers"],
                     json={"preferred_lang": "fr"}, timeout=10)
    assert r.status_code == 200


# ---------- REGRESSION: /messages/{id}/translate still works ----------
@pytest.fixture(scope="module")
def english_msg(admin_ctx, bob_ctx):
    text = "Hello brother, see you tonight at 7pm at the restaurant!"
    r = requests.post(f"{BASE_URL}/api/messages/send",
                      headers=admin_ctx["headers"],
                      json={"to_user_id": bob_ctx["user"]["user_id"], "text": text},
                      timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["message"]["msg_id"]


def test_translate_fresh(bob_ctx, english_msg):
    r = requests.post(f"{BASE_URL}/api/messages/{english_msg}/translate",
                      headers=bob_ctx["headers"],
                      json={"target_lang": "fr"}, timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["translated_text"]
    assert d["target_lang"] == "fr"


def test_translate_cached(bob_ctx, english_msg):
    t0 = time.time()
    r = requests.post(f"{BASE_URL}/api/messages/{english_msg}/translate",
                      headers=bob_ctx["headers"],
                      json={"target_lang": "fr"}, timeout=10)
    elapsed = time.time() - t0
    assert r.status_code == 200
    j = r.json()
    assert j["cached"] is True
    assert elapsed < 5.0


def test_supported_languages_endpoint(bob_ctx):
    r = requests.get(f"{BASE_URL}/api/messages/supported-languages",
                     headers=bob_ctx["headers"], timeout=10)
    assert r.status_code == 200
    codes = {lg["code"] for lg in r.json()["languages"]}
    assert codes == {"fr", "en", "pt", "es", "ar", "sw", "ln", "yo"}
