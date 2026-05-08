"""
iter218 — Upload + CSRF + post creation backend tests.

Validates the P0 blocker fix:
  A. Video transcode pipeline resolved via static_ffmpeg (no apt ffmpeg).
  B. /api/upload/ accepts .mov / .mp4 / .webm and transcodes to mp4.
  C. Non-video uploads rejected cleanly with 4xx (not 500).
  D. /api/feed/posts requires auth; text-only post works with Bearer token.
"""
import io
import os
import struct
import subprocess
import tempfile
import pytest
import requests
from pathlib import Path

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://japap-refactor.preview.emergentagent.com").rstrip("/")
BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")


# ─── fixtures ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"X-Requested-With": "XMLHttpRequest"})
    return s


@pytest.fixture(scope="module")
def bob_token(api):
    r = api.post(
        f"{BASE_URL}/api/auth/login",
        json={
            "email": "bob@japap.com",
            "password": "Test1234!",
            "captcha_id": BYPASS,
            "captcha_answer": "0",
        },
        timeout=90,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    assert tok, f"no token in login response: {data}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(bob_token):
    return {"Authorization": f"Bearer {bob_token}", "X-Requested-With": "XMLHttpRequest"}


STATIC_FFMPEG_ARM64 = "/root/.venv/lib/python3.11/site-packages/static_ffmpeg/bin/linux_arm64/ffmpeg"


def _gen_video(path: Path, fmt_args: list[str]):
    """Create a 2s test video locally using bundled ARM64 static_ffmpeg."""
    if path.exists() and path.stat().st_size > 1000:
        return
    cmd = [
        STATIC_FFMPEG_ARM64, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
    ] + fmt_args + [str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)


@pytest.fixture(scope="module")
def sample_mp4():
    p = Path("/tmp/iter218_tiny.mp4")
    _gen_video(p, ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"])
    assert p.stat().st_size > 0
    return p


@pytest.fixture(scope="module")
def sample_mov():
    p = Path("/tmp/iter218_tiny.mov")
    _gen_video(p, ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-f", "mov"])
    assert p.stat().st_size > 0
    return p


@pytest.fixture(scope="module")
def sample_webm():
    p = Path("/tmp/iter218_tiny.webm")
    _gen_video(p, ["-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus", "-shortest", "-f", "webm"])
    assert p.stat().st_size > 0
    return p


# ─── has_ffmpeg ──────────────────────────────────────────────────────────
def test_has_ffmpeg_in_process():
    """has_ffmpeg() path-existence check AND actual executability.
    iter218 CRITICAL BUG: on ARM64 containers, static_ffmpeg 2.13 ships
    an x86_64 binary into bin/linux/ (should be bin/linux_arm64/), so
    the binary path exists but fails with Exec format error at runtime.
    """
    import sys
    sys.path.insert(0, "/app/backend")
    from services.video_pipeline import has_ffmpeg, _FFMPEG_BIN, _FFPROBE_BIN  # noqa
    assert has_ffmpeg() is True, f"ffmpeg path missing: ff={_FFMPEG_BIN} fp={_FFPROBE_BIN}"
    # Validate the binary actually runs on this architecture.
    rc = subprocess.run([_FFMPEG_BIN, "-version"], capture_output=True, timeout=10).returncode
    assert rc == 0, (
        f"ffmpeg binary NOT executable on this architecture "
        f"(bin={_FFMPEG_BIN}, rc={rc}). "
        "This is the root cause of 'Erreur upload' / 'File is not a readable video'."
    )


# ─── Video uploads ───────────────────────────────────────────────────────
def _upload(api, headers, path: Path, mime: str):
    with open(path, "rb") as fh:
        files = {"file": (path.name, fh, mime)}
        return api.post(f"{BASE_URL}/api/upload/", headers=headers, files=files, timeout=180)


def test_upload_mp4(api, auth_headers, sample_mp4):
    r = _upload(api, auth_headers, sample_mp4, "video/mp4")
    assert r.status_code == 200, f"mp4 upload failed: {r.status_code} {r.text[:300]}"
    d = r.json()
    assert d.get("content_type") == "video/mp4"
    assert d.get("url", "").endswith(".mp4")
    assert d.get("duration") is not None
    assert float(d.get("duration", 0)) >= 0.5


def test_upload_mov_is_transcoded(api, auth_headers, sample_mov):
    """Key iter218 fix — iPhone .mov MUST be auto-transcoded to mp4."""
    r = _upload(api, auth_headers, sample_mov, "video/quicktime")
    assert r.status_code == 200, f"mov upload failed: {r.status_code} {r.text[:300]}"
    d = r.json()
    assert d.get("content_type") == "video/mp4", f"mov not transcoded: ct={d.get('content_type')}"
    assert d.get("url", "").endswith(".mp4"), f"final url not mp4: {d.get('url')}"
    assert d.get("thumbnail_url"), "thumbnail_url missing"
    assert float(d.get("duration", 0)) > 0, "duration missing"


def test_upload_webm(api, auth_headers, sample_webm):
    r = _upload(api, auth_headers, sample_webm, "video/webm")
    assert r.status_code == 200, f"webm upload failed: {r.status_code} {r.text[:300]}"
    d = r.json()
    assert d.get("content_type") == "video/mp4"
    assert d.get("url", "").endswith(".mp4")


def test_upload_txt_rejected_4xx(api, auth_headers):
    """Non-video/image .txt must be rejected with 4xx and explicit detail, NOT 500."""
    files = {"file": ("bad.txt", io.BytesIO(b"not a media file"), "text/plain")}
    r = api.post(f"{BASE_URL}/api/upload/", headers=auth_headers, files=files, timeout=60)
    assert 400 <= r.status_code < 500, f"expected 4xx, got {r.status_code}: {r.text[:200]}"
    # Explicit detail (not a raw stacktrace)
    try:
        detail = r.json().get("detail") or ""
    except Exception:
        detail = r.text
    assert detail, "no detail in rejection response"
    assert len(str(detail)) < 500, "detail looks like a stacktrace (too long)"


# ─── Feed post CRUD ──────────────────────────────────────────────────────
def test_post_requires_auth():
    # Fresh session to ensure no cookie from prior login leaks in.
    r = requests.post(
        f"{BASE_URL}/api/feed/posts",
        json={"text": "iter218 no-auth test"},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=90,
    )
    # 401 (bearer) or 403 (cookie-only CSRF) both acceptable
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text[:200]}"


def test_post_text_only_with_bearer(api, auth_headers):
    payload = {"text": "TEST_iter218 backend post creation"}
    r = api.post(f"{BASE_URL}/api/feed/posts", headers=auth_headers, json=payload, timeout=60)
    assert r.status_code in (200, 201), f"post create failed: {r.status_code} {r.text[:300]}"
    d = r.json()
    # Response shape check — post should be returned with the text
    text_match = (
        d.get("text") == payload["text"]
        or d.get("content") == payload["text"]
        or (d.get("post") or {}).get("text") == payload["text"]
        or "id" in d
        or "post_id" in d
    )
    assert text_match, f"unexpected response shape: {d}"
