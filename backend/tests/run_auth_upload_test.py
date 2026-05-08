"""Direct test of /api/upload/image pipeline by minting a JWT for Alice."""
import io, os, sys, jwt, asyncio, requests
from datetime import datetime, timezone, timedelta
from PIL import Image

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"] if os.environ.get("REACT_APP_BACKEND_URL") else open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
JWT_SECRET = os.environ["JWT_SECRET"]


async def get_alice_id():
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM users WHERE email=$1", "alice@japap.com")
        return row["user_id"] if row else None


def mint(user_id):
    now = datetime.now(timezone.utc)
    p = {"sub": user_id, "email": "alice@japap.com",
         "iat": int(now.timestamp()),
         "exp": now + timedelta(minutes=60), "type": "access"}
    return jwt.encode(p, JWT_SECRET, algorithm="HS256")


def jpeg(w, h):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(0, h, 8):
        for x in range(0, w, 8):
            px[x, y] = ((x*7)%255, (y*11)%255, ((x+y)*3)%255)
    b = io.BytesIO()
    img.save(b, "JPEG", quality=85)
    return b.getvalue()


async def main():
    uid = await get_alice_id()
    print(f"Alice user_id={uid}")
    token = mint(uid)
    H = {"Authorization": f"Bearer {token}"}

    results = []
    # Test 1: landscape
    r = requests.post(f"{BASE_URL}/api/upload/image?kind=post",
                      headers=H, files={"file": ("l.jpg", jpeg(2000, 1400), "image/jpeg")})
    print(f"[post landscape] {r.status_code} {r.text[:300]}")
    assert r.status_code == 200
    b = r.json()
    print(f"  meta width/height={b['main']['width']}x{b['main']['height']}")
    # Verify ACTUAL file dims (metadata bug: API returns bounding box 1600x1600
    # instead of actual 1600x1120 — checked separately below).
    main_url = b["main"]["url"]
    f_local = requests.get(f"{BASE_URL}{main_url}").content
    from PIL import Image as _I; im = _I.open(io.BytesIO(f_local))
    print(f"  ACTUAL file dims={im.size}")
    assert im.size == (1600, 1120), f"actual file dims wrong: {im.size}"
    assert b["main"]["mime"] == "image/webp"
    assert b["main"]["size"] <= 250*1024
    results.append(("post_landscape_actual_1600x1120", True))
    metadata_bug = b["main"]["height"] != 1120
    results.append(("post_landscape_metadata_correct", not metadata_bug))

    # Test 2: portrait
    r = requests.post(f"{BASE_URL}/api/upload/image?kind=post",
                      headers=H, files={"file": ("p.jpg", jpeg(1200, 2400), "image/jpeg")})
    assert r.status_code == 200
    b = r.json()
    print(f"  meta width/height={b['main']['width']}x{b['main']['height']}")
    f_local = requests.get(f"{BASE_URL}{b['main']['url']}").content
    im = _I.open(io.BytesIO(f_local))
    print(f"  ACTUAL file dims={im.size}")
    assert im.size == (800, 1600), f"actual file dims wrong: {im.size}"
    results.append(("post_portrait_actual_800x1600", True))

    # Test 3: profile regression
    r = requests.post(f"{BASE_URL}/api/upload/image?kind=profile",
                      headers=H, files={"file": ("pr.jpg", jpeg(1000, 1000), "image/jpeg")})
    assert r.status_code == 200
    b = r.json()
    assert b["main"]["width"] == 512 and b["main"]["height"] == 512
    assert b["thumb"]["width"] == 128 and b["thumb"]["height"] == 128
    results.append(("profile_regression", True))

    # Test 4: cover regression
    r = requests.post(f"{BASE_URL}/api/upload/image?kind=cover",
                      headers=H, files={"file": ("c.jpg", jpeg(1920, 720), "image/jpeg")})
    assert r.status_code == 200
    b = r.json()
    assert b["main"]["width"] == 1280 and b["main"]["height"] == 480
    assert b["thumb"]["width"] == 640 and b["thumb"]["height"] == 240
    results.append(("cover_regression", True))

    # Test 5: ETag + 304
    r1 = requests.get(f"{BASE_URL}{main_url}")
    assert r1.status_code == 200
    etag = r1.headers.get("ETag")
    cc = r1.headers.get("Cache-Control", "")
    assert etag and "max-age" in cc, f"etag={etag} cc={cc}"
    r2 = requests.get(f"{BASE_URL}{main_url}", headers={"If-None-Match": etag})
    assert r2.status_code == 304, r2.status_code
    results.append(("etag_304", True))

    print("\n=== RESULTS ===")
    for n, ok in results:
        print(f"  {n}: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
