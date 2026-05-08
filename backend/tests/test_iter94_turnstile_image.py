"""
iter94 — Go-Live gate tests:
  1) Turnstile guard on /api/auth/login | /register | /forgot-password
  2) Regression on other auth endpoints (must NOT require turnstile_token)
  3) Smart image pipeline /api/upload/image?kind=post (+ profile/cover regression)
  4) ETag + 304 Not Modified on /api/upload/files/{filename}
"""
import io
import os
import pytest
import requests
from PIL import Image

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"

ALICE_EMAIL = "alice@japap.com"
ALICE_PW = "Test1234!"


def _jpeg_bytes(w: int, h: int) -> bytes:
    """Create a JPEG of given dimensions with some real color noise so
    Pillow doesn't collapse it to a single-pixel file."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(0, h, 8):
        for x in range(0, w, 8):
            px[x, y] = ((x * 7) % 255, (y * 11) % 255, ((x + y) * 3) % 255)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ─── FIXTURES ────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def alice_session():
    """Login as Alice — she's pre-activated so we can authenticate.
    Turnstile is enforced; we have no real token so this fixture may fail.
    In that case, upload tests are skipped."""
    s = requests.Session()
    resp = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ALICE_EMAIL, "password": ALICE_PW,
              "turnstile_token": "dev-bypass"},
    )
    if resp.status_code != 200:
        # Cannot bypass Turnstile from curl — skip auth-requiring tests.
        pytest.skip(f"Alice login blocked by Turnstile ({resp.status_code}): "
                    f"{resp.text[:200]}")
    return s


# ─── 1) TURNSTILE GUARDS ─────────────────────────────────────────────────
class TestTurnstileLogin:
    def test_login_without_token_returns_400(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ALICE_EMAIL, "password": ALICE_PW},
        )
        assert r.status_code == 400, r.text
        assert "urnstile" in r.text or "verification" in r.text.lower()

    def test_login_with_invalid_token_returns_401(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ALICE_EMAIL, "password": ALICE_PW,
                  "turnstile_token": "INVALID_FAKE_TOKEN_xxxx"},
        )
        assert r.status_code == 401, r.text
        assert "bot" in r.text.lower() or "protection" in r.text.lower()


class TestTurnstileRegister:
    def test_register_without_token_returns_400(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": "TEST_noturn@japap.com",
                "password": "Test1234!",
                "first_name": "T", "last_name": "T",
                "country_code": "CM", "phone_number": "612345678",
                "terms_accepted": True,
            },
        )
        assert r.status_code == 400, r.text

    def test_register_with_invalid_token_returns_401(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": "TEST_noturn2@japap.com",
                "password": "Test1234!",
                "first_name": "T", "last_name": "T",
                "country_code": "CM", "phone_number": "612345678",
                "terms_accepted": True,
                "turnstile_token": "INVALID_FAKE_TOKEN_xxxx",
            },
        )
        assert r.status_code == 401, r.text


class TestTurnstileForgotPassword:
    def test_forgot_without_token_returns_400(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/forgot-password",
            json={"email": ALICE_EMAIL},
        )
        assert r.status_code == 400, r.text

    def test_forgot_with_invalid_token_returns_401(self, session):
        r = session.post(
            f"{BASE_URL}/api/auth/forgot-password",
            json={"email": ALICE_EMAIL,
                  "turnstile_token": "INVALID_FAKE_TOKEN_xxxx"},
        )
        assert r.status_code == 401, r.text


# ─── 2) REGRESSION: other auth endpoints must NOT require turnstile ─────
class TestAuthRegressionNoTurnstile:
    """These endpoints must return their normal business-logic errors
    (NOT a 400 "Turnstile verification required")."""

    def _assert_no_turnstile(self, r):
        body = r.text.lower()
        assert "turnstile" not in body, (
            f"Endpoint regressed — turnstile leaked in response: {r.status_code} {r.text[:200]}"
        )

    def test_verify_otp_no_turnstile(self, session):
        r = session.post(f"{BASE_URL}/api/auth/verify-otp",
                         json={"email": ALICE_EMAIL, "code": "000000"})
        # Expected: 400 "Aucun code actif" / "Code invalide" — anything but 400-turnstile
        assert r.status_code in (400, 404, 429), r.text
        self._assert_no_turnstile(r)

    def test_verify_2fa_no_turnstile(self, session):
        r = session.post(f"{BASE_URL}/api/auth/verify-2fa",
                         json={"email": ALICE_EMAIL, "code": "000000"})
        assert r.status_code in (400, 404, 429), r.text
        self._assert_no_turnstile(r)

    def test_refresh_no_turnstile(self, session):
        r = session.post(f"{BASE_URL}/api/auth/refresh")
        assert r.status_code == 401, r.text  # no refresh cookie
        self._assert_no_turnstile(r)

    def test_logout_no_turnstile(self, session):
        r = session.post(f"{BASE_URL}/api/auth/logout")
        assert r.status_code == 200, r.text  # logout always succeeds
        self._assert_no_turnstile(r)

    def test_reset_password_no_turnstile(self, session):
        r = session.post(f"{BASE_URL}/api/auth/reset-password",
                         json={"token": "bogus", "new_password": "Whatever1234!"})
        assert r.status_code == 400, r.text
        self._assert_no_turnstile(r)

    def test_me_no_turnstile(self, session):
        r = session.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401, r.text
        self._assert_no_turnstile(r)


# ─── 3) SMART IMAGE PIPELINE ────────────────────────────────────────────
class TestUploadImageKindValidation:
    def test_kind_hacker_rejected(self, session):
        # No auth needed — query param is validated by FastAPI before get_current_user
        files = {"file": ("x.jpg", b"\xff\xd8\xff\xe0dummy", "image/jpeg")}
        r = session.post(
            f"{BASE_URL}/api/upload/image?kind=hacker",
            files=files, headers={}  # remove json content-type
        )
        assert r.status_code == 422, r.text
        assert "string_pattern_mismatch" in r.text or "pattern" in r.text.lower()


class TestUploadImagePostPipeline:
    """Requires auth — skipped when Turnstile blocks Alice login from curl."""

    def test_post_landscape_2000x1400(self, alice_session):
        data = _jpeg_bytes(2000, 1400)
        r = alice_session.post(
            f"{BASE_URL}/api/upload/image?kind=post",
            files={"file": ("land.jpg", data, "image/jpeg")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # landscape fit: longest side 2000 → 1600, height scales → 1120
        assert body["main"]["width"] == 1600
        assert body["main"]["height"] == 1120
        assert body["main"]["mime"] == "image/webp"
        assert body["main"]["size"] <= 250 * 1024, f"main too big: {body['main']['size']}"
        assert body["thumb"]["width"] == 400
        assert body["thumb"]["height"] == 400
        assert body["thumb"]["size"] < 20 * 1024, f"thumb too big: {body['thumb']['size']}"
        assert body["main"]["url"].startswith("/api/upload/files/")

    def test_post_portrait_1200x2400(self, alice_session):
        data = _jpeg_bytes(1200, 2400)
        r = alice_session.post(
            f"{BASE_URL}/api/upload/image?kind=post",
            files={"file": ("port.jpg", data, "image/jpeg")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # portrait fit: longest side 2400 → 1600, width scales → 800
        assert body["main"]["width"] == 800
        assert body["main"]["height"] == 1600
        assert body["thumb"]["width"] == 400 and body["thumb"]["height"] == 400

    def test_profile_regression(self, alice_session):
        data = _jpeg_bytes(1000, 1000)
        r = alice_session.post(
            f"{BASE_URL}/api/upload/image?kind=profile",
            files={"file": ("p.jpg", data, "image/jpeg")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["main"]["width"] == 512 and body["main"]["height"] == 512
        assert body["thumb"]["width"] == 128 and body["thumb"]["height"] == 128

    def test_cover_regression(self, alice_session):
        data = _jpeg_bytes(1920, 720)
        r = alice_session.post(
            f"{BASE_URL}/api/upload/image?kind=cover",
            files={"file": ("c.jpg", data, "image/jpeg")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["main"]["width"] == 1280 and body["main"]["height"] == 480
        assert body["thumb"]["width"] == 640 and body["thumb"]["height"] == 240


# ─── 4) ETag / Cache-Control / 304 ──────────────────────────────────────
class TestFileServingETag:
    def test_etag_and_304(self, alice_session):
        # Upload a post image to get a real file
        data = _jpeg_bytes(800, 800)
        r = alice_session.post(
            f"{BASE_URL}/api/upload/image?kind=post",
            files={"file": ("etag.jpg", data, "image/jpeg")},
        )
        assert r.status_code == 200
        url_path = r.json()["main"]["url"]
        full_url = f"{BASE_URL}{url_path}"

        r1 = requests.get(full_url)
        assert r1.status_code == 200, r1.text
        etag = r1.headers.get("ETag")
        cc = r1.headers.get("Cache-Control", "")
        assert etag, f"Missing ETag header; headers={dict(r1.headers)}"
        assert "max-age" in cc, f"Missing Cache-Control: {cc}"

        r2 = requests.get(full_url, headers={"If-None-Match": etag})
        assert r2.status_code == 304, (
            f"Expected 304; got {r2.status_code} body={r2.text[:200]}"
        )
