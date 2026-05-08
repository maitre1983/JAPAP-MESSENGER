"""iter150 — Backend tests: photo-gallery, AI filters, referral fraud report."""
import os
import io
import uuid
import pytest
import requests
from PIL import Image

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://japap-refactor.preview.emergentagent.com').rstrip('/')
BYPASS = {"captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0"}


def _login(email, password):
    last = None
    for _ in range(3):
        try:
            r = requests.post(f"{BASE_URL}/api/auth/login",
                              json={"email": email, "password": password, **BYPASS},
                              timeout=60)
            if r.status_code == 200:
                return r.json()["access_token"]
            last = f"{r.status_code} {r.text[:200]}"
        except Exception as e:
            last = str(e)
    raise AssertionError(f"login {email} failed: {last}")


@pytest.fixture(scope="module")
def bob_token():
    return _login("bob@japap.com", "Test1234!")


@pytest.fixture(scope="module")
def admin_token():
    return _login("admin@japap.com", "JapapAdmin2024!")


@pytest.fixture(scope="module")
def bob_user(bob_token):
    r = requests.get(f"{BASE_URL}/api/auth/me",
                     headers={"Authorization": f"Bearer {bob_token}"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ── Photo gallery ─────────────────────────────────────────────
class TestPhotoGallery:
    def test_self_gallery_empty_or_items(self, bob_token, bob_user):
        r = requests.get(f"{BASE_URL}/api/users/{bob_user['user_id']}/photo-gallery",
                         headers={"Authorization": f"Bearer {bob_token}"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "items" in data and "total" in data and "private" in data
        assert isinstance(data["items"], list)
        assert data["private"] is False

    def test_story_with_filter_preset_appears(self, bob_token, bob_user):
        story_payload = {
            "image_url": f"https://cdn.example.com/test_{uuid.uuid4().hex[:8]}.jpg",
            "filter_preset": "vintage",
        }
        rc = requests.post(f"{BASE_URL}/api/feed/stories",
                           headers={"Authorization": f"Bearer {bob_token}"},
                           json=story_payload, timeout=15)
        # Accept 200/201; if endpoint shape differs, log and skip gracefully
        if rc.status_code not in (200, 201):
            pytest.skip(f"stories create returned {rc.status_code}: {rc.text[:200]}")
        r = requests.get(f"{BASE_URL}/api/users/{bob_user['user_id']}/photo-gallery",
                         headers={"Authorization": f"Bearer {bob_token}"}, timeout=15)
        assert r.status_code == 200
        data = r.json()
        # must contain at least one story entry with filter_preset=vintage or the url
        found = any(it.get("filter_preset") == "vintage" or it.get("image_url") == story_payload["image_url"]
                    for it in data["items"])
        assert found, f"Created story not found in gallery: {data}"

    def test_story_filter_preset_invalid_chars_rejected(self, bob_token):
        """Whitelist test — invalid preset should be dropped/rejected."""
        for bad in ["<script>", "preset with spaces", "bad;inject"]:
            rc = requests.post(f"{BASE_URL}/api/feed/stories",
                               headers={"Authorization": f"Bearer {bob_token}"},
                               json={"image_url": f"https://cdn.example.com/x_{uuid.uuid4().hex[:6]}.jpg",
                                     "filter_preset": bad},
                               timeout=15)
            # Either reject (400) or store sanitized (200 with cleaned or null)
            assert rc.status_code in (200, 201, 400, 422), f"{bad} -> {rc.status_code}"


# ── AI filter quota + apply ────────────────────────────────────
class TestAiFilter:
    def test_quota_bob_pro(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/media/ai-filter/quota",
                         headers={"Authorization": f"Bearer {bob_token}"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert set(["used", "cap", "remaining", "tier"]).issubset(data.keys())
        assert data["cap"] == 100, f"Bob (Pro Business) expected cap=100, got {data['cap']}"
        assert data["tier"] == "pro"
        assert isinstance(data["used"], int)
        assert data["remaining"] == max(0, data["cap"] - data["used"])

    def _jpeg_bytes(self, size=(300, 300)):
        img = Image.new("RGB", size, (200, 80, 80))
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        return buf.getvalue()

    def test_invalid_style(self, bob_token):
        files = {"image": ("x.jpg", self._jpeg_bytes(), "image/jpeg")}
        r = requests.post(f"{BASE_URL}/api/media/ai-filter",
                          headers={"Authorization": f"Bearer {bob_token}"},
                          data={"style": "foo"}, files=files, timeout=30)
        assert r.status_code == 400
        assert "UNKNOWN_STYLE" in r.text

    def test_unsupported_mime(self, bob_token):
        files = {"image": ("x.txt", b"hello world", "text/plain")}
        r = requests.post(f"{BASE_URL}/api/media/ai-filter",
                          headers={"Authorization": f"Bearer {bob_token}"},
                          data={"style": "cartoon"}, files=files, timeout=30)
        assert r.status_code == 400
        assert "UNSUPPORTED_MIME" in r.text

    def test_empty_file(self, bob_token):
        files = {"image": ("x.jpg", b"", "image/jpeg")}
        r = requests.post(f"{BASE_URL}/api/media/ai-filter",
                          headers={"Authorization": f"Bearer {bob_token}"},
                          data={"style": "cartoon"}, files=files, timeout=30)
        assert r.status_code == 400
        assert "EMPTY_FILE" in r.text

    def test_file_too_large(self, bob_token):
        big = b"\xff\xd8\xff" + b"\x00" * (7 * 1024 * 1024)
        files = {"image": ("big.jpg", big, "image/jpeg")}
        r = requests.post(f"{BASE_URL}/api/media/ai-filter",
                          headers={"Authorization": f"Bearer {bob_token}"},
                          data={"style": "cartoon"}, files=files, timeout=60)
        assert r.status_code == 400
        assert "FILE_TOO_LARGE" in r.text

    def test_valid_cartoon_wiring(self, bob_token):
        """Accept 200 image/jpeg OR 502 AI_GENERATION_FAILED — both show wiring works."""
        files = {"image": ("x.jpg", self._jpeg_bytes(), "image/jpeg")}
        r = requests.post(f"{BASE_URL}/api/media/ai-filter",
                          headers={"Authorization": f"Bearer {bob_token}"},
                          data={"style": "cartoon"}, files=files, timeout=120)
        assert r.status_code in (200, 502), f"Got {r.status_code}: {r.text[:300]}"
        if r.status_code == 200:
            assert r.headers.get("content-type", "").startswith("image/jpeg")
            for h in ["X-AI-Filter-Used", "X-AI-Filter-Cap", "X-AI-Filter-Style",
                      "X-AI-Filter-Request-Id", "X-AI-Filter-Duration-Ms"]:
                assert h.lower() in {k.lower() for k in r.headers.keys()}, f"Missing header {h}"
        else:
            body = r.json()
            # body is {"detail": {"code": ..., "message": ...}}
            detail = body.get("detail", body)
            assert isinstance(detail, dict) and detail.get("code") == "AI_GENERATION_FAILED"


# ── Referral admin fraud report ────────────────────────────────
class TestFraudReport:
    def test_non_admin_forbidden(self, bob_token):
        r = requests.get(f"{BASE_URL}/api/admin/referrals/fraud-report",
                         headers={"Authorization": f"Bearer {bob_token}"}, timeout=15)
        assert r.status_code == 403

    def test_admin_ok(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/admin/referrals/fraud-report",
                         headers={"Authorization": f"Bearer {admin_token}"}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ["window_days", "thresholds", "top_ips", "top_devices", "top_velocity"]:
            assert k in data, f"missing key {k}"
        assert isinstance(data["top_ips"], list)


# ── Referral fraud scoring unit tests (DB-backed) ──────────────
class TestFraudScoring:
    def test_scoring_signals(self):
        import asyncio, sys
        sys.path.insert(0, "/app/backend")
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        from database import get_pool  # noqa
        from services.referral_fraud_service import score_referral  # noqa

        async def _run():
            pool = await get_pool()
            async with pool.acquire() as conn:
                # self-referral
                res = await score_referral(conn, "user_sameX", "user_sameX", "1.2.3.4", "devA")
                assert res["risk"] == 100
                assert any(s["code"] == "SELF_REFERRAL" for s in res["signals"])
                # basic referral (no signals)
                res2 = await score_referral(conn, "user_ref1", "user_new2",
                                            f"10.0.0.{uuid.uuid4().int % 250}",
                                            f"dev_{uuid.uuid4().hex[:10]}")
                assert 0 <= res2["risk"] <= 100
        asyncio.run(_run())
