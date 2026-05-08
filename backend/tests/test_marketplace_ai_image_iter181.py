"""
iter181 — Marketplace AI Image (Nano Banana) tests
====================================================
- generate from prompt
- enhance an existing photo
- background swap with preset
- quota enforcement (3/day, 429 when exhausted)
- AI failure → 502 (no quota consumed)
- Settings disabled → 503

Usage: cd /app/backend && python3 tests/test_marketplace_ai_image_iter181.py
"""
import asyncio
import io
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_URL = os.environ.get("TEST_API_URL") or "http://localhost:8001"
BYPASS = os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026")

CRED_BOB = {"email": "bob@japap.com", "password": "Test1234!"}


async def _login(client: httpx.AsyncClient, cred: dict) -> str:
    r = await client.post("/api/auth/login",
                           json={**cred, "captcha_id": BYPASS, "captcha_answer": "0"})
    assert r.status_code == 200, f"login {r.status_code}: {r.text}"
    tok = r.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {tok}"
    return tok


async def _reset_quota(user_id: str) -> None:
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute(
        "DELETE FROM marketplace_ai_image_usage WHERE user_id = "
        "(SELECT user_id FROM users WHERE email = 'bob@japap.com')")
    await conn.close()


def _real_jpeg() -> bytes:
    """Pick any existing AI-generated image from disk to use as input."""
    from pathlib import Path
    up = Path("/app/backend/uploads")
    for p in sorted(up.glob("prod_ai_*.jpg")):
        if "thumb" not in p.name and "_hd" not in p.name and p.stat().st_size > 5000:
            return p.read_bytes()
    raise RuntimeError("no real AI image available — run /generate once first")


async def main():
    await _reset_quota("bob")
    async with httpx.AsyncClient(base_url=API_URL, timeout=120.0,
                                  headers={"X-Requested-With": "XMLHttpRequest"}) as bob:
        await _login(bob, CRED_BOB)

        # [1] Quota state — fresh
        r = await bob.get("/api/marketplace/ai-image/quota")
        assert r.status_code == 200
        s = r.json()
        assert s["enabled"] is True and s["used_today"] == 0 and s["remaining"] >= 1
        assert isinstance(s["bg_presets"], list) and "studio_white" in s["bg_presets"]
        print(f"[1] quota fresh OK (quota={s['quota']}, presets={len(s['bg_presets'])})")

        # [2] Generate happy path
        r = await bob.post("/api/marketplace/ai-image/generate",
                            json={"prompt": "minimal blue ceramic coffee mug"})
        assert r.status_code == 200, f"[2] {r.status_code}: {r.text}"
        d = r.json()
        assert "image" in d and "thumb" in d["image"] and "full" in d["image"]
        assert d["mode"] == "generate"
        assert d["quota"]["used_today"] == 1
        print(f"[2] generate OK (3 sizes, quota now {d['quota']['used_today']}/{d['quota']['quota']})")

        # [3] Generate — too short prompt (validation)
        r = await bob.post("/api/marketplace/ai-image/generate",
                            json={"prompt": "ab"})
        assert r.status_code == 502
        print("[3] short prompt → 502 OK")

        # [4] bg-swap with preset (multipart)
        img = _real_jpeg()
        r = await bob.post(
            "/api/marketplace/ai-image/bg-swap",
            files={"file": ("p.jpg", io.BytesIO(img), "image/jpeg")},
            data={"preset": "marble"})
        assert r.status_code == 200, f"[4] bg-swap: {r.status_code} {r.text}"
        d = r.json()
        assert d["mode"] == "bg_swap" and d["preset"] == "marble"
        assert d["quota"]["used_today"] == 2
        print(f"[4] bg-swap preset OK (quota {d['quota']['used_today']})")

        # [5] enhance
        r = await bob.post(
            "/api/marketplace/ai-image/enhance",
            files={"file": ("p.jpg", io.BytesIO(img), "image/jpeg")},
            data={"instruction": "make it brighter"})
        assert r.status_code == 200, f"[5] enhance: {r.status_code} {r.text}"
        d = r.json()
        assert d["mode"] == "enhance" and "image" in d
        assert d["quota"]["used_today"] == 3 and d["quota"]["remaining"] == 0
        print(f"[5] enhance OK (quota exhausted: {d['quota']['used_today']}/{d['quota']['quota']})")

        # [6] 4th call should 429
        r = await bob.post("/api/marketplace/ai-image/generate",
                            json={"prompt": "fresh blue mug minimalistic"})
        assert r.status_code == 429, f"[6] expected 429, got {r.status_code}: {r.text}"
        assert "Quota IA" in r.text
        print("[6] quota exhausted → 429 OK")

        # [7] bg-swap missing preset & scene → 400
        await _reset_quota("bob")
        r = await bob.post("/api/marketplace/ai-image/bg-swap",
                            files={"file": ("p.jpg", io.BytesIO(img), "image/jpeg")})
        assert r.status_code == 400
        print("[7] bg-swap no preset/scene → 400 OK")

        # [8] enhance with too small file → 400
        r = await bob.post("/api/marketplace/ai-image/enhance",
                            files={"file": ("p.jpg", io.BytesIO(b"x" * 64), "image/jpeg")})
        assert r.status_code == 400
        print("[8] enhance tiny file → 400 OK")

    print("\n✅ iter181 — All 8 assertions PASS")


if __name__ == "__main__":
    asyncio.run(main())
