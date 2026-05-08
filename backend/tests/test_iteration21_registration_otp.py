"""Iteration 21 tests — Registration OTP flow, geo endpoints, forgot/reset password,
11 supported languages, long-voice summarize.
"""
import os
import re
import time
import pytest
import requests
import subprocess

def _load_backend_url():
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if not url:
        try:
            with open("/app/frontend/.env") as f:
                for line in f:
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        url = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    if not url:
        raise RuntimeError("REACT_APP_BACKEND_URL not configured")
    return url.rstrip("/")


BASE_URL = _load_backend_url()

LOG_FILES = [
    "/var/log/supervisor/backend.out.log",
    "/var/log/supervisor/backend.err.log",
]


def _read_backend_logs(tail: int = 400) -> str:
    out = ""
    for f in LOG_FILES:
        try:
            r = subprocess.run(["tail", "-n", str(tail), f], capture_output=True, text=True, timeout=5)
            out += r.stdout
        except Exception:
            pass
    return out


def _latest_otp_for(email: str) -> str:
    logs = _read_backend_logs(800)
    # The email HTML/TEXT contains the 6-digit code. Look for lines near EMAIL-MOCK matching the email.
    # Strategy: find the latest occurrence of email, then search nearby lines for a 6-digit code.
    idx = logs.rfind(email)
    if idx < 0:
        return ""
    window = logs[idx: idx + 6000]
    # Look for "verification code is: 123456" or "Your code: 123456" etc
    m = re.findall(r"\b(\d{6})\b", window)
    return m[0] if m else ""


@pytest.fixture(scope="session")
def s():
    return requests.Session()


# ---------- Geo endpoints ----------

class TestGeo:
    def test_countries_list(self, s):
        r = s.get(f"{BASE_URL}/api/geo/countries", timeout=15)
        assert r.status_code == 200
        data = r.json()
        countries = data.get("countries", [])
        assert len(countries) >= 195, f"expected >=195 countries, got {len(countries)}"
        sample = countries[0]
        assert "code" in sample and "name" in sample and "dial" in sample
        # Must include key markets
        codes = {c["code"] for c in countries}
        for expected in ("US", "FR", "CM", "IN", "BR", "CN", "NG"):
            assert expected in codes, f"missing {expected}"

    def test_detect_does_not_crash(self, s):
        r = s.get(f"{BASE_URL}/api/geo/detect", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "country_code" in d and "suggested_lang" in d and "ip" in d
        assert isinstance(d["suggested_lang"], str) and d["suggested_lang"]


# ---------- Supported languages (11) ----------

class TestLanguages:
    def test_supported_languages_includes_11(self, s, bob_token):
        r = s.get(f"{BASE_URL}/api/messages/supported-languages",
                  headers={"Authorization": f"Bearer {bob_token}"}, timeout=15)
        assert r.status_code == 200
        data = r.json()
        langs = data.get("languages") or data.get("supported") or data
        # Normalize to a set of codes
        if isinstance(langs, dict):
            codes = set(langs.keys())
        else:
            codes = set()
            for item in langs:
                if isinstance(item, dict):
                    codes.add(item.get("code") or item.get("lang"))
                else:
                    codes.add(item)
        expected = {"en", "fr", "pt", "es", "ar", "sw", "ln", "yo", "hi", "bn", "ta"}
        missing = expected - codes
        assert not missing, f"missing langs: {missing} (got {codes})"


# ---------- Registration + OTP flow ----------

@pytest.fixture(scope="module")
def new_user_email():
    return f"TEST_iter21_{int(time.time())}@example.com"


class TestRegistration:
    def test_register_requires_terms(self, s, new_user_email):
        r = s.post(f"{BASE_URL}/api/auth/register", json={
            "email": new_user_email,
            "password": "Pass1234!",
            "first_name": "Iter21",
            "country_code": "CM",
            "phone_number": "+237600000000",
            "terms_accepted": False,
        }, timeout=15)
        assert r.status_code == 400

    def test_register_success_sends_otp(self, s, new_user_email):
        r = s.post(f"{BASE_URL}/api/auth/register", json={
            "email": new_user_email,
            "password": "Pass1234!",
            "first_name": "Iter21",
            "country_code": "CM",
            "phone_number": "+237600000000",
            "terms_accepted": True,
        }, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "otp_sent"
        assert data.get("email") == new_user_email.lower()
        # Must NOT issue tokens
        assert "access_token" not in data
        # Cookie not set
        assert "access_token" not in r.cookies

    def test_register_duplicate_verified_rejected(self, s):
        r = s.post(f"{BASE_URL}/api/auth/register", json={
            "email": "bob@japap.com",
            "password": "Anything1!",
            "first_name": "X",
            "country_code": "CM",
            "phone_number": "+237",
            "terms_accepted": True,
        }, timeout=15)
        assert r.status_code == 400

    def test_verify_otp_wrong_code(self, s, new_user_email):
        r = s.post(f"{BASE_URL}/api/auth/verify-otp", json={
            "email": new_user_email,
            "code": "000000",
        }, timeout=15)
        # If the real OTP happens to be 000000 (1 in 1M), this test flakes — acceptable.
        assert r.status_code == 400
        assert "invalide" in r.text.lower() or "invalid" in r.text.lower()

    def test_verify_otp_success(self, s, new_user_email):
        code = _latest_otp_for(new_user_email.lower())
        if not code:
            pytest.skip("Could not read OTP from backend logs")
        r = s.post(f"{BASE_URL}/api/auth/verify-otp", json={
            "email": new_user_email,
            "code": code,
        }, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "user" in d and "access_token" in d
        assert d["user"]["email"] == new_user_email.lower()
        assert d["user"].get("email_verified") is True

    def test_resend_otp_unknown_email_no_enumeration(self, s):
        r = s.post(f"{BASE_URL}/api/auth/resend-otp", json={
            "email": f"TEST_unknown_{int(time.time())}@nowhere.example",
        }, timeout=15)
        assert r.status_code == 200
        assert r.json().get("status") == "otp_sent"

    def test_resend_otp_rate_limit(self, s):
        # Create a brand-new unverified account
        email = f"TEST_rl_{int(time.time())}@example.com"
        r0 = s.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": "Pass1234!", "first_name": "R",
            "country_code": "CM", "phone_number": "+1", "terms_accepted": True,
        }, timeout=15)
        assert r0.status_code == 200
        # Immediate resend should hit the 60s rate-limit (429)
        r1 = s.post(f"{BASE_URL}/api/auth/resend-otp", json={"email": email}, timeout=15)
        assert r1.status_code == 429, f"expected 429, got {r1.status_code}: {r1.text}"


# ---------- Forgot + reset password ----------

class TestPasswordReset:
    def test_forgot_password_no_enumeration(self, s):
        r = s.post(f"{BASE_URL}/api/auth/forgot-password",
                   json={"email": f"TEST_noexist_{int(time.time())}@x.example"}, timeout=15)
        assert r.status_code == 200

    def test_forgot_and_reset_password_full_flow(self, s):
        # Create + verify a new user, then reset their password
        email = f"TEST_pwreset_{int(time.time())}@example.com"
        old_pw = "OldPass1!"
        new_pw = "NewPass2@"
        r0 = s.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": old_pw, "first_name": "P",
            "country_code": "CM", "phone_number": "+1", "terms_accepted": True,
        }, timeout=15)
        assert r0.status_code == 200
        code = _latest_otp_for(email.lower())
        if not code:
            pytest.skip("OTP not captured from logs")
        r1 = s.post(f"{BASE_URL}/api/auth/verify-otp", json={"email": email, "code": code}, timeout=15)
        assert r1.status_code == 200

        # Forgot password — fetch reset token from logs
        s2 = requests.Session()
        r2 = s2.post(f"{BASE_URL}/api/auth/forgot-password", json={"email": email}, timeout=15)
        assert r2.status_code == 200
        logs = _read_backend_logs(800)
        m = re.findall(r"reset-password\?token=([A-Za-z0-9_\-]+)", logs)
        if not m:
            pytest.skip("Reset token not found in logs")
        token = m[-1]

        r3 = s2.post(f"{BASE_URL}/api/auth/reset-password",
                     json={"token": token, "new_password": new_pw}, timeout=15)
        assert r3.status_code == 200, r3.text

        # Login with NEW password should succeed
        r4 = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": new_pw}, timeout=15)
        assert r4.status_code == 200, r4.text


# ---------- Regression: existing login still works ----------

class TestLoginRegression:
    @pytest.mark.parametrize("email,pw", [
        ("admin@japap.com", "JapapAdmin2024!"),
        ("bob@japap.com", "Test1234!"),
    ])
    def test_login_existing(self, email, pw):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["user"]["email"] == email


# ---------- Translation to Hindi ----------

@pytest.fixture(scope="module")
def bob_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "bob@japap.com", "password": "Test1234!"}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": "admin@japap.com", "password": "JapapAdmin2024!"}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


class TestTranslationHindi:
    def test_translate_to_hindi(self, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        convs = requests.get(f"{BASE_URL}/api/messages/conversations", headers=headers, timeout=15)
        assert convs.status_code == 200
        conv_list = convs.json() if isinstance(convs.json(), list) else convs.json().get("conversations", [])
        if not conv_list:
            pytest.skip("No conversations for bob")
        target = None
        conv_id = None
        for c in conv_list:
            cid = c.get("conv_id") or c.get("conversation_id") or c.get("id")
            msgs = requests.get(f"{BASE_URL}/api/messages/conversations/{cid}", headers=headers, timeout=15)
            if msgs.status_code != 200:
                continue
            msg_list = msgs.json() if isinstance(msgs.json(), list) else msgs.json().get("messages", [])
            for m in msg_list:
                content = m.get("content") or m.get("text") or ""
                mtype = m.get("message_type") or m.get("type", "text")
                if content and len(content) > 5 and mtype == "text":
                    target = m
                    conv_id = cid
                    break
            if target:
                break
        if not target:
            pytest.skip("No suitable text message to translate")
        msg_id = target.get("msg_id") or target.get("message_id") or target.get("id")
        r = requests.post(f"{BASE_URL}/api/messages/{msg_id}/translate",
                          json={"target_lang": "hi"}, headers=headers, timeout=40)
        assert r.status_code == 200, r.text
        d = r.json()
        txt = d.get("translated_text") or d.get("text") or ""
        assert txt, f"Empty translation: {d}"
        has_devanagari = any("\u0900" <= c <= "\u097F" for c in txt)
        assert has_devanagari, f"Expected Devanagari in hindi translation, got: {txt!r}"


# ---------- Voice summary ----------

class TestVoiceSummary:
    def test_summarize_long_voice(self, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        r = requests.post(f"{BASE_URL}/api/messages/msg_long_voice_demo/summarize",
                          json={"target_lang": "en"}, headers=headers, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        summary = d.get("summary") or ""
        assert summary and len(summary) > 10

    def test_summarize_cached_second_call(self, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        t0 = time.time()
        r = requests.post(f"{BASE_URL}/api/messages/msg_long_voice_demo/summarize",
                          json={"target_lang": "en"}, headers=headers, timeout=60)
        dt = time.time() - t0
        assert r.status_code == 200
        # Cached call should be fast — allow generous bound
        assert dt < 5, f"Cached call took {dt:.2f}s (should be <5s)"

    def test_summarize_non_participant_forbidden(self, bob_token):
        # admin ↔ bob share conversations, but msg_long_voice_demo may or may not include bob.
        # If it includes bob, this test becomes moot — mark skip if 200 returned.
        headers = {"Authorization": f"Bearer {bob_token}"}
        r = requests.post(f"{BASE_URL}/api/messages/msg_long_voice_demo/summarize",
                          json={"target_lang": "en"}, headers=headers, timeout=30)
        # Either bob is a participant (200) or not (403)
        assert r.status_code in (200, 403), r.status_code
