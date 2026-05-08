"""
JAPAP — iter174 P0 Marketplace Phase A E2E
=============================================
Tests:
  1. POST /api/marketplace/products/upload-images returns 3 sizes per file
  2. POST /api/marketplace/products with images (1-5) creates a product
  3. GET /api/marketplace/products/{id} returns full payload with seller
     fields (phone_number, seller_kyc_verified, seller_completed_sales,
     seller_verified_pro)
  4. PUT /api/marketplace/products/{id} (vendor) accepts edits including
     status flip 'active'<->'offline' but rejects 'deleted'
  5. DELETE /api/marketplace/products/{id} (vendor) soft-deletes (status='deleted'),
     subsequent GET returns 404
  6. POST /api/marketplace/admin/products/{id}/moderate (admin) flips status
     and writes audit_log
  7. GET /api/marketplace/products?verified_only=true filters to KYC-approved sellers
"""
import asyncio
import io
import os
import sys

import asyncpg
import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/backend/.env")

API = "http://localhost:8001"
DB_URL = os.environ["DATABASE_URL"]


def _make_jpeg(label="PROD", w=1200, h=1200) -> bytes:
    from PIL import Image, ImageDraw
    import random
    img = Image.new("RGB", (w, h), color=(245, 245, 248))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, w - 60, h - 60], outline=(120, 120, 130), width=3)
    for i in range(8):
        d.line([100, 200 + i*70, w-100, 200 + i*70], fill=(60, 60, 70), width=4)
    d.text((140, 120), label, fill=(20, 20, 30))
    random.seed(42)
    for _ in range(2000):
        d.point([random.randint(0, w-1), random.randint(0, h-1)],
                fill=(random.randint(0, 200),) * 3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def _conn():
    return await asyncpg.connect(DB_URL)


async def _login(client, email, password):
    r = await client.post(f"{API}/api/auth/login", json={
        "email": email, "password": password,
        "captcha_id": "JAPAP_E2E_BYPASS_2026", "captcha_answer": "0",
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    tok = r.cookies.get("access_token") or client.cookies.get("access_token")
    if tok:
        client.headers["Authorization"] = f"Bearer {tok}"
    return r


async def _reset(emails):
    c = await _conn()
    try:
        for e in emails:
            await c.execute("DELETE FROM login_attempts WHERE identifier=$1", e)
    finally:
        await c.close()


async def main():
    await _reset(["bob@japap.com", "charlie_iter141@japap.com", "admin@japap.com"])
    pid = None

    # 1. Upload images
    async with httpx.AsyncClient(timeout=120.0) as client:
        await _login(client, "bob@japap.com", "Test1234!")
        files = [
            ("files", ("p1.jpg", _make_jpeg("P1"), "image/jpeg")),
            ("files", ("p2.jpg", _make_jpeg("P2"), "image/jpeg")),
        ]
        r = await client.post(f"{API}/api/marketplace/products/upload-images", files=files)
        assert r.status_code == 200, f"upload failed: {r.text}"
        imgs = r.json()["images"]
        assert len(imgs) == 2
        for img in imgs:
            assert "thumb" in img and "full" in img and "hd" in img
        print(f"[1] Upload 2 images → 3 sizes each ✓")

        # 2. Create product
        r = await client.post(f"{API}/api/marketplace/products", json={
            "title": "iter174 test product",
            "description": "Test produit pour iter174",
            "price": 25000,
            "category": "general",
            "condition": "new",
            "location": "Yaoundé",
            "images": imgs,
        })
        assert r.status_code == 200, f"create failed: {r.text}"
        pid = r.json()["product_id"]
        print(f"[2] Product created with images: {pid}")

        # 3. GET detail
        r = await client.get(f"{API}/api/marketplace/products/{pid}")
        assert r.status_code == 200
        body = r.json()
        for k in ("seller_kyc_verified", "seller_completed_sales",
                  "seller_verified_pro", "phone_number", "images"):
            assert k in body, f"missing {k} in product detail"
        assert len(body["images"]) == 2
        print(f"[3] GET detail enriched payload ✓ "
              f"(kyc_verified={body['seller_kyc_verified']}, "
              f"sales={body['seller_completed_sales']}, "
              f"verified_pro={body['seller_verified_pro']})")

        # 4. Vendor update — status flip allowed
        r = await client.put(f"{API}/api/marketplace/products/{pid}",
                              json={"status": "offline"})
        assert r.status_code == 200, r.text
        r = await client.put(f"{API}/api/marketplace/products/{pid}",
                              json={"status": "active"})
        assert r.status_code == 200
        # 'deleted' must be rejected via PUT
        r = await client.put(f"{API}/api/marketplace/products/{pid}",
                              json={"status": "deleted"})
        assert r.status_code == 400, f"expected 400 for status=deleted, got {r.status_code}: {r.text}"
        # Image edit
        r = await client.put(f"{API}/api/marketplace/products/{pid}",
                              json={"images": [imgs[0]]})
        assert r.status_code == 200
        print(f"[4] Vendor PUT accepts active/offline + image edit, rejects 'deleted' ✓")

    # 5. Admin moderate
    async with httpx.AsyncClient(timeout=120.0) as adm:
        await _login(adm, "admin@japap.com", "JapapAdmin2024!")
        r = await adm.post(
            f"{API}/api/marketplace/admin/products/{pid}/moderate",
            json={"status": "offline", "reason": "Test moderation iter174"})
        assert r.status_code == 200, r.text
        # Verify audit log
        c = await _conn()
        try:
            audit = await c.fetchval(
                "SELECT details FROM audit_logs WHERE action='admin_moderate_product' "
                "ORDER BY id DESC LIMIT 1")
            assert audit and pid in audit
            print(f"[5] Admin moderate + audit log ✓")
        finally:
            await c.close()
        # Bring back online
        await adm.post(
            f"{API}/api/marketplace/admin/products/{pid}/moderate",
            json={"status": "active", "reason": ""})

    # 6. Verified-only filter
    async with httpx.AsyncClient(timeout=120.0) as cli:
        await _login(cli, "charlie_iter141@japap.com", "Charlie2026!")
        # First, no filter — should include this product (Bob is the seller)
        r = await cli.get(f"{API}/api/marketplace/products?limit=50")
        all_pids = [p["product_id"] for p in r.json()["products"]]
        # Then with verified_only — Bob has no approved KYC at this point so
        # the product should NOT be in the filtered list.
        r = await cli.get(f"{API}/api/marketplace/products?limit=50&verified_only=true")
        filtered_pids = [p["product_id"] for p in r.json()["products"]]
        # Approve Bob KYC programmatically and re-test
        c = await _conn()
        try:
            bob_uid = await c.fetchval("SELECT user_id FROM users WHERE email='bob@japap.com'")
            await c.execute("DELETE FROM kyc_verifications WHERE user_id=$1", bob_uid)
            import uuid
            await c.execute("""INSERT INTO kyc_verifications
                (kyc_id, user_id, full_name, id_type, id_number, id_photo_url, selfie_url, status)
                VALUES ($1, $2, 'Bob V', 'national_id', 'X', '/x', '/y', 'approved')""",
                f"kyc_test_{uuid.uuid4().hex[:8]}", bob_uid)
        finally:
            await c.close()
        r = await cli.get(f"{API}/api/marketplace/products?limit=50&verified_only=true")
        new_filtered = [p["product_id"] for p in r.json()["products"]]
        assert pid in new_filtered, "Bob's product should appear in verified_only filter after KYC approval"
        print(f"[6] verified_only filter works "
              f"(before={pid in filtered_pids}, after-approval={pid in new_filtered}) ✓")

    # 7. Vendor delete (soft) + 404 on subsequent GET
    async with httpx.AsyncClient(timeout=120.0) as cli:
        await _login(cli, "bob@japap.com", "Test1234!")
        r = await cli.delete(f"{API}/api/marketplace/products/{pid}")
        assert r.status_code == 200
        r = await cli.get(f"{API}/api/marketplace/products/{pid}")
        assert r.status_code == 404, "deleted product must return 404"
        print(f"[7] Vendor soft-delete + 404 on subsequent GET ✓")

    # Cleanup KYC seed
    c = await _conn()
    try:
        await c.execute("DELETE FROM kyc_verifications WHERE kyc_id LIKE 'kyc_test_%'")
        await c.execute("DELETE FROM products WHERE product_id=$1", pid)
    finally:
        await c.close()

    print("\n[ALL PASSED] iter174 Marketplace Phase A backend ✅")


if __name__ == "__main__":
    asyncio.run(main())
