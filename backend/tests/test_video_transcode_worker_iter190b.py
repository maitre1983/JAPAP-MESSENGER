"""
iter190b — Async video transcode worker tests
==============================================
Verifies the > 50 MB upload path:
  • Big .mov returns 200 immediately with status='processing' + job_id
  • GET /api/upload/video-job/{id} reflects pending → processing → ready
  • Worker eventually flips status to 'ready' with url + duration
  • Authorization: another user can't read someone else's job
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

API = os.environ.get("TEST_API_URL", "http://localhost:8001")
BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")


def _make_big_clip(out_path: str, size_mb: int = 70) -> bool:
    """Generate an uncompressible .mov above `size_mb` MB."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "nullsrc=size=1280x720:rate=30",
        "-filter_complex", "geq=random(1)*255:128:128",
        "-t", "5", "-c:v", "libx264", "-preset", "ultrafast",
        "-qp", "1", "-fs", f"{size_mb}M", out_path,
    ]
    return subprocess.run(cmd).returncode == 0


async def _login(c: httpx.AsyncClient, email: str, pw: str) -> str:
    r = await c.post("/api/auth/login", json={
        "email": email, "password": pw,
        "captcha_id": BYPASS, "captcha_answer": "0",
    })
    assert r.status_code == 200, f"login failed: {r.text[:200]}"
    return r.json()["access_token"]


async def main():
    if not shutil.which("ffmpeg"):
        print("✗ ffmpeg not installed")
        sys.exit(1)

    tmp = tempfile.mkdtemp(prefix="japap_iter190b_")
    big = f"{tmp}/big.mov"
    assert _make_big_clip(big), "ffmpeg failed to produce big sample"
    size_mb = os.path.getsize(big) / 1024 / 1024
    assert size_mb > 50, f"sample too small ({size_mb:.1f} MB) — async path won't trigger"
    print(f"  generated {size_mb:.1f} MB sample")

    async with httpx.AsyncClient(base_url=API, timeout=120.0) as c:
        bob_tok = await _login(c, "bob@japap.com", "Test1234!")
        alice_tok = await _login(c, "alice@japap.com", "Alice2026!")

        # ── CASE 1 — > 50 MB returns status='processing' instantly ──
        with open(big, "rb") as fh:
            r = await c.post("/api/upload/",
                              headers={"Authorization": f"Bearer {bob_tok}"},
                              files={"file": ("big.mov", fh, "video/quicktime")})
        assert r.status_code == 200, f"upload failed: {r.status_code} {r.text[:200]}"
        d = r.json()
        assert d["status"] == "processing", f"expected processing, got {d}"
        assert d["job_id"], "job_id missing"
        assert d["url"] is None, "url should be None until ready"
        assert d["poll_url"].endswith(d["job_id"])
        job_id = d["job_id"]
        print(f"  CASE 1 OK — uploaded {size_mb:.1f} MB → job={job_id} status=processing")

        # ── CASE 2 — polling eventually flips to ready ──
        ready = None
        for _ in range(15):  # up to 90 s
            await asyncio.sleep(6)
            r = await c.get(f"/api/upload/video-job/{job_id}",
                             headers={"Authorization": f"Bearer {bob_tok}"})
            assert r.status_code == 200, f"poll failed: {r.text[:200]}"
            j = r.json()
            if j["status"] == "ready":
                ready = j
                break
            if j["status"] == "failed":
                raise AssertionError(f"job failed: {j.get('error')}")
        assert ready, f"job did not finish within 90s"
        assert ready["url"] and ready["url"].endswith(".mp4")
        assert ready.get("duration") and float(ready["duration"]) > 0
        print(f"  CASE 2 OK — job ready: {ready['url']} | dur={ready['duration']}")

        # ── CASE 3 — another user can't read someone else's job ──
        r = await c.get(f"/api/upload/video-job/{job_id}",
                         headers={"Authorization": f"Bearer {alice_tok}"})
        assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text[:120]}"
        print(f"  CASE 3 OK — alice cannot read bob's job (403)")

        # ── CASE 4 — invalid job id is rejected ──
        r = await c.get("/api/upload/video-job/../etc/passwd",
                         headers={"Authorization": f"Bearer {bob_tok}"})
        assert r.status_code in (400, 404), f"expected 400/404, got {r.status_code}"
        r = await c.get(f"/api/upload/video-job/{'x' * 200}",
                         headers={"Authorization": f"Bearer {bob_tok}"})
        assert r.status_code == 400
        print(f"  CASE 4 OK — invalid job ids rejected")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n✅ iter190b — all 4 scenarios passed")


if __name__ == "__main__":
    asyncio.run(main())
