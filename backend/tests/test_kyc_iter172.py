"""
JAPAP — iter172 P0 KYC overhaul E2E
=====================================
Verifies:
  1. Backend submit accepts dynamic recto/verso based on id_type:
     - passport: only id_photo + selfie (no id_back)
     - national_id/drivers_license: id_photo + id_back_photo + selfie REQUIRED
  2. Submit triggers compression (full ≤1024px JPEG + preview ≤480px JPEG)
  3. AI pre-validation runs (ai_risk_score in {low, medium, high}) and
     ai_alerts populated when applicable.
  4. Admin /pending payload includes id_back_photo_url, all preview_*_url,
     ai_risk_score, ai_alerts.
  5. /api/upload/files/{name} serves the compressed JPEG correctly.
  6. CRITICAL: AI never auto-approves — pending stays pending.
"""
import asyncio
import io
import os
import sys

import asyncpg
import httpx
from dotenv import load_dotenv
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

API = "http://localhost:8001"
DB_URL = os.environ["DATABASE_URL"]


def _make_test_jpeg(width: int = 1600, height: int = 1100,
                     text_overlay: str = "TEST") -> bytes:
    """Generate a non-blank JPEG with synthetic 'document-like' content
    so blur and content-dimension heuristics treat it as plausible."""
    from PIL import ImageDraw
    img = Image.new("RGB", (width, height), color=(245, 245, 248))
    d = ImageDraw.Draw(img)
    # Draw a card-like rectangle
    d.rectangle([60, 60, width - 60, height - 60], outline=(120, 120, 130), width=3)
    # Random "text" lines for noise
    for i in range(8):
        y = 200 + i * 80
        d.line([100, y, width - 100, y], fill=(60, 60, 70), width=4)
        d.line([100, y + 35, width - 200, y + 35], fill=(120, 120, 130), width=2)
    d.text((140, 120), text_overlay, fill=(20, 20, 30))
    # noise dot pattern → high Laplacian variance (not blurry)
    import random
    random.seed(42)
    for _ in range(2000):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        d.point([x, y], fill=(random.randint(0, 200),) * 3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def _conn():
    return await asyncpg.connect(DB_URL)


async def _login_with_token(client, email, password):
    r = await client.post(f"{API}/api/auth/login", json={
        "email": email, "password": password,
        "captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0",
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    tok = r.cookies.get("access_token") or client.cookies.get("access_token")
    if tok:
        client.headers["Authorization"] = f"Bearer {tok}"
    return r


async def _reset_kyc(user_email: str):
    c = await _conn()
    try:
        uid = await c.fetchval("SELECT user_id FROM users WHERE email=$1", user_email)
        if uid:
            await c.execute("DELETE FROM kyc_verifications WHERE user_id=$1", uid)
        await c.execute("DELETE FROM login_attempts WHERE identifier=$1", user_email)
        return uid
    finally:
        await c.close()


async def main():
    bob_uid = await _reset_kyc("bob@japap.com")
    charlie_uid = await _reset_kyc("charlie_iter141@japap.com")
    assert bob_uid and charlie_uid

    id_jpeg = _make_test_jpeg(text_overlay="CARTE D'IDENTITE")
    id_back_jpeg = _make_test_jpeg(text_overlay="VERSO")
    selfie_jpeg = _make_test_jpeg(width=1200, height=1600, text_overlay="SELFIE")

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Login Bob
        await _login_with_token(client, "bob@japap.com", "Test1234!")

        # ── 1. CNI without verso → expect 400
        files = {
            "id_photo": ("id.jpg", id_jpeg, "image/jpeg"),
            "selfie":   ("selfie.jpg", selfie_jpeg, "image/jpeg"),
        }
        data = {"full_name": "Bob Test", "id_type": "national_id", "id_number": "AB123456"}
        r = await client.post(f"{API}/api/kyc/submit", data=data, files=files)
        assert r.status_code == 400, f"expected 400 (verso required), got {r.status_code}: {r.text}"
        assert "verso" in r.text.lower()
        print(f"[1] CNI without verso → 400 ✓")

        # ── 2. CNI with verso → 200 + AI score
        files = {
            "id_photo":      ("id.jpg",      id_jpeg,      "image/jpeg"),
            "id_back_photo": ("idback.jpg",  id_back_jpeg, "image/jpeg"),
            "selfie":        ("selfie.jpg",  selfie_jpeg,  "image/jpeg"),
        }
        r = await client.post(f"{API}/api/kyc/submit", data=data, files=files)
        assert r.status_code == 200, f"submit failed: {r.text}"
        body = r.json()
        assert body["status"] == "pending", "submit must always be pending (no auto-approve)"
        assert body["ai_risk_score"] in ("low", "medium", "high"), \
            f"unexpected risk_score: {body['ai_risk_score']}"
        assert "ai_alerts" in body
        print(f"[2] CNI submit OK — status=pending, risk={body['ai_risk_score']}, "
              f"alerts={len(body['ai_alerts'])}")

        # ── 3. Files are compressed (<1024px) and preview exists in DB
        c = await _conn()
        try:
            row = await c.fetchrow(
                "SELECT id_photo_url, id_back_photo_url, selfie_url, "
                "preview_id_url, preview_id_back_url, preview_selfie_url, "
                "ai_risk_score, ai_alerts, ai_payload, status "
                "FROM kyc_verifications WHERE user_id=$1 ORDER BY created_at DESC LIMIT 1",
                bob_uid)
            assert row, "row not found"
            assert row["status"] == "pending"
            for k in ("id_photo_url", "id_back_photo_url", "selfie_url",
                       "preview_id_url", "preview_id_back_url", "preview_selfie_url"):
                assert row[k], f"missing {k}"
            assert row["ai_risk_score"] in ("low", "medium", "high")
            print(f"[3] DB row complete: recto+verso+selfie + previews + ai_risk={row['ai_risk_score']}")
        finally:
            await c.close()

        # ── 4. /api/upload/files/{name} returns the compressed JPEG (≤1024 wide)
        full_name = row["id_photo_url"].rsplit("/", 1)[-1]
        r = await client.get(f"{API}/api/upload/files/{full_name}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")
        full_img = Image.open(io.BytesIO(r.content))
        assert full_img.width <= 1024, f"compression failed, width={full_img.width}"
        print(f"[4] full image served {full_img.size} (≤1024w) ✓")

        prev_name = row["preview_id_url"].rsplit("/", 1)[-1]
        r = await client.get(f"{API}/api/upload/files/{prev_name}")
        assert r.status_code == 200
        prev_img = Image.open(io.BytesIO(r.content))
        assert prev_img.width <= 480, f"preview too large {prev_img.width}"
        print(f"[5] preview image served {prev_img.size} (≤480w) ✓")

        # ── 6. Passport flow → no id_back required
        await _reset_kyc("charlie_iter141@japap.com")
        # Use a fresh client to clear bob's auth header & switch user.
    async with httpx.AsyncClient(timeout=120.0) as client2:
        await _login_with_token(client2, "charlie_iter141@japap.com", "Charlie2026!")
        files = {
            "id_photo": ("passport.jpg", id_jpeg, "image/jpeg"),
            "selfie":   ("selfie.jpg",   selfie_jpeg, "image/jpeg"),
        }
        data = {"full_name": "Charlie Test", "id_type": "passport", "id_number": "PA9988"}
        r = await client2.post(f"{API}/api/kyc/submit", data=data, files=files)
        assert r.status_code == 200, f"passport submit failed: {r.text}"
        body = r.json()
        assert body["status"] == "pending"
        print(f"[6] Passport submit (no verso) OK — risk={body['ai_risk_score']}")

    # ── 7. Admin /pending payload includes new fields + recto/verso
    async with httpx.AsyncClient(timeout=120.0) as admin_client:
        await _login_with_token(admin_client, "admin@japap.com", "JapapAdmin2024!")
        r = await admin_client.get(f"{API}/api/kyc/admin/pending")
        assert r.status_code == 200, f"admin/pending failed: {r.status_code} {r.text}"
        subs = r.json()["submissions"]
        assert len(subs) >= 2, f"expected ≥2 pending submissions, got {len(subs)}"
        bob_row = next((s for s in subs if s["user_id"] == bob_uid), None)
        assert bob_row, "Bob's KYC missing in admin list"
        for k in ("id_photo_url", "id_back_photo_url", "selfie_url",
                  "preview_id_url", "preview_id_back_url", "preview_selfie_url",
                  "ai_risk_score", "ai_alerts"):
            assert k in bob_row, f"admin payload missing {k}"
        # Charlie passport row → id_back_photo_url MUST be None
        charlie_row = next((s for s in subs if s["user_id"] == charlie_uid), None)
        assert charlie_row, "Charlie's KYC missing"
        assert charlie_row["id_back_photo_url"] is None, \
            "passport KYC must NOT have id_back_photo_url"
        print(f"[7] Admin /pending OK — bob has verso, charlie does not ✓")

    # Cleanup
    await _reset_kyc("bob@japap.com")
    await _reset_kyc("charlie_iter141@japap.com")
    print("\n[ALL PASSED] iter172 KYC overhaul backend ✅")


if __name__ == "__main__":
    asyncio.run(main())
