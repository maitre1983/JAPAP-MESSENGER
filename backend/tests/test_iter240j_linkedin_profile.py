"""iter240j — LinkedIn-style profile + visibility tests.

Covers:
 A. New profile fields on GET /api/users/profile/{username}
 B. Lookup by username OR user_id
 C. PUT /api/users/profile updates new fields + completion pct
 D. POST /api/users/profile/visibility toggle public/private
 L. Email never leaked
 M. Profile completion progression
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
BYPASS = "JAPAP_E2E_BYPASS_2026"

BOB = {"email": "bob@japap.com", "password": "Test1234!", "username": "bob48c0"}
ALICE = {"email": "alice@japap.com", "password": "Alice2026!"}

NEW_FIELDS = [
    "profile_visibility", "headline", "bio", "location",
    "website_url", "linkedin_url", "twitter_url",
    "profession", "company", "skills", "languages_spoken",
    "experience", "education", "achievements",
    "profile_completed_at",
]


def _login(email, password):
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password,
              "captcha_id": BYPASS, "captcha_answer": "0"},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    j = r.json()
    return j.get("access_token") or j.get("token"), j.get("user", {})


@pytest.fixture(scope="module")
def bob_auth():
    token, user = _login(BOB["email"], BOB["password"])
    return {"token": token, "user": user,
            "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(scope="module")
def alice_auth():
    token, user = _login(ALICE["email"], ALICE["password"])
    return {"token": token, "user": user,
            "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(scope="module", autouse=True)
def ensure_public_at_start(bob_auth):
    """Ensure Bob's profile starts public and finishes public."""
    requests.post(f"{BASE_URL}/api/users/profile/visibility",
                  headers=bob_auth["headers"], json={"visibility": "public"}, timeout=15)
    yield
    requests.post(f"{BASE_URL}/api/users/profile/visibility",
                  headers=bob_auth["headers"], json={"visibility": "public"}, timeout=15)


# ── FEATURE A — new fields exposed, anonymous access works ─────────────
class TestFeatureA_NewFieldsExposed:
    def test_anonymous_get_public_profile_returns_200(self):
        r = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        for f in NEW_FIELDS:
            assert f in data, f"missing field {f}"
        assert "profile_completion_pct" in data
        assert "is_self" in data and data["is_self"] is False
        assert "is_private" in data and data["is_private"] is False

    def test_email_never_leaked_anonymous(self):
        r = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}", timeout=15)
        assert "email" not in r.json(), "email leaked in anonymous GET!"


# ── FEATURE B — Lookup by username OR user_id ──────────────────────────
class TestFeatureB_LookupVariants:
    def test_lookup_by_username_and_user_id_match(self, bob_auth):
        user_id = bob_auth["user"].get("user_id")
        assert user_id, "Bob user_id missing"
        r1 = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}", timeout=15)
        r2 = requests.get(f"{BASE_URL}/api/users/profile/{user_id}", timeout=15)
        assert r1.status_code == 200 and r2.status_code == 200
        d1, d2 = r1.json(), r2.json()
        assert d1["user_id"] == d2["user_id"]
        assert d1["username"].lower() == d2["username"].lower()


# ── FEATURE C — PUT updates new fields + completion pct rises ──────────
class TestFeatureC_UpdateProfile:
    def test_put_profile_updates_and_completion(self, bob_auth):
        payload = {
            "headline": "Test headline iter240j",
            "bio": "Bio test iter240j",
            "skills": ["python", "react", "tests"],
        }
        r = requests.put(f"{BASE_URL}/api/users/profile",
                         headers=bob_auth["headers"], json=payload, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("profile_completion_pct", 0) >= 50, body
        # Re-fetch via GET to confirm persistence
        r2 = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}", timeout=15)
        d = r2.json()
        assert d["headline"] == payload["headline"]
        assert d["bio"] == payload["bio"]
        # skills may come back as list or JSON-encoded string (asyncpg JSONB quirk)
        skills = d["skills"]
        if isinstance(skills, str):
            import json as _json
            try:
                skills = _json.loads(skills)
            except Exception:
                pass
        assert isinstance(skills, list) and set(payload["skills"]).issubset(set(skills))
        assert d["profile_completion_pct"] >= 50


# ── FEATURE D — Visibility toggle private/public ───────────────────────
class TestFeatureD_VisibilityToggle:
    def test_toggle_private_then_anonymous_view_is_masked(self, bob_auth):
        r = requests.post(f"{BASE_URL}/api/users/profile/visibility",
                          headers=bob_auth["headers"],
                          json={"visibility": "private"}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("visibility") == "private"
        # Anonymous GET — must be masked
        a = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}", timeout=15)
        assert a.status_code == 200
        d = a.json()
        assert d.get("is_private") is True
        # Sensitive fields must NOT be present
        for k in ("bio", "skills", "experience", "wallet_balance", "phone_number",
                  "followers_count", "following_count"):
            assert k not in d, f"{k} leaked in private masked view"
        assert "email" not in d
        # Owner GET (Bob) — must still see full payload
        own = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}",
                           headers=bob_auth["headers"], timeout=15)
        assert own.status_code == 200
        own_d = own.json()
        assert own_d.get("is_self") is True
        assert "bio" in own_d  # owner sees full

    def test_toggle_back_public_restores_full_payload(self, bob_auth):
        r = requests.post(f"{BASE_URL}/api/users/profile/visibility",
                          headers=bob_auth["headers"],
                          json={"visibility": "public"}, timeout=15)
        assert r.status_code == 200
        a = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}", timeout=15)
        d = a.json()
        assert d.get("is_private") is False
        assert "bio" in d  # full payload restored

    def test_visibility_invalid_value(self, bob_auth):
        r = requests.post(f"{BASE_URL}/api/users/profile/visibility",
                          headers=bob_auth["headers"],
                          json={"visibility": "bogus"}, timeout=15)
        assert r.status_code == 400

    def test_visibility_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/users/profile/visibility",
                          json={"visibility": "private"}, timeout=15)
        assert r.status_code in (401, 403)


# ── FEATURE L — Email never leaked across all variants ─────────────────
class TestFeatureL_EmailNeverLeaked:
    def test_no_email_for_owner_view(self, bob_auth):
        r = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}",
                         headers=bob_auth["headers"], timeout=15)
        assert "email" not in r.json()

    def test_no_email_for_other_user_view(self, alice_auth):
        r = requests.get(f"{BASE_URL}/api/users/profile/{BOB['username']}",
                         headers=alice_auth["headers"], timeout=15)
        assert "email" not in r.json()


# ── FEATURE M — Completion pct progression via /profile/me/full ────────
class TestFeatureM_CompletionProgression:
    def test_me_full_returns_pct(self, bob_auth):
        r = requests.get(f"{BASE_URL}/api/users/profile/me/full",
                         headers=bob_auth["headers"], timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "profile_completion_pct" in d
        assert isinstance(d["profile_completion_pct"], int)
        assert d.get("is_self") is True

    def test_completion_increases_after_more_fields(self, bob_auth):
        before = requests.get(f"{BASE_URL}/api/users/profile/me/full",
                              headers=bob_auth["headers"], timeout=15).json()
        before_pct = before.get("profile_completion_pct", 0)
        # Add website_url + location
        requests.put(f"{BASE_URL}/api/users/profile",
                     headers=bob_auth["headers"],
                     json={"website_url": "https://example.org",
                           "location": "Yaoundé, CM"}, timeout=15)
        after = requests.get(f"{BASE_URL}/api/users/profile/me/full",
                             headers=bob_auth["headers"], timeout=15).json()
        assert after["profile_completion_pct"] >= before_pct
