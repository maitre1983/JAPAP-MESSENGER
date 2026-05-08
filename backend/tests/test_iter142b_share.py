"""iter142B — Phase P1 viral share endpoints.

Validates :
  - GET /api/crowdfunding/projects/{slug}/share returns valid landing/share/whatsapp/png URLs
  - GET /api/crowdfunding/projects/{slug}/share-card.png renders 1080×1920 (story) and 1200×630 (landscape)
  - GET /api/og/crowdfunding/{slug} renders OG meta tags with og:image pointing to the landscape PNG

Run:
    cd /app/backend && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_iter142b_share.py -v -s
"""
import os
import io
import asyncio
import asyncpg
import pytest
import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv("/app/backend/.env")
DB_URL = os.environ["DATABASE_URL"]
API = os.environ.get("PUBLIC_API_URL", "http://localhost:8001")
BYPASS = (os.environ.get("MATH_CAPTCHA_TEST_BYPASS_TOKEN")
          or os.environ.get("TURNSTILE_TEST_BYPASS_TOKEN", "JAPAP_E2E_BYPASS_2026"))


def _login(email, password):
    r = requests.post(
        f"{API}/api/auth/login",
        json={"email": email, "password": password,
              "captcha_id": BYPASS, "captcha_answer": "x"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture(scope="module", autouse=True)
def setup():
    async def _r():
        c = await asyncpg.connect(DB_URL)
        await c.execute("TRUNCATE crowdfunding_votes CASCADE")
        await c.execute("TRUNCATE crowdfunding_projects CASCADE")
        await c.execute("TRUNCATE crowdfunding_cycles RESTART IDENTITY CASCADE")
        for k, v in [
            ("crowdfunding_threshold_projects", "2"),
            ("crowdfunding_min_account_age_days", "0"),
            ("crowdfunding_min_activity_score", "0"),
            ("crowdfunding_required_actions",
             '{"posts":0,"likes":0,"transactions":0}'),
        ]:
            await c.execute(
                "INSERT INTO admin_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                k, v,
            )
        await c.close()
    asyncio.get_event_loop().run_until_complete(_r())


@pytest.fixture(scope="module")
def slug():
    tok = _login("alice@japap.com", "Alice2026!")
    r = requests.post(
        f"{API}/api/crowdfunding/projects",
        json={
            "title": "Café solidaire viral",
            "description": "Description longue suffisante pour passer le min length de 20 caractères.",
            "category": "art", "country_code": "CM",
            "image_url": "https://images.unsplash.com/photo-1554118811-1e0d58224f24?w=400",
        },
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 201, r.text
    return r.json()["slug"]


def test_share_endpoint_returns_all_links(slug):
    r = requests.get(f"{API}/api/crowdfunding/projects/{slug}/share")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == slug
    assert body["title"]
    assert body["owner_name"]
    assert "votes" in body["share_text"].lower() or "vote" in body["share_text"].lower()
    assert body["whatsapp_url"].startswith("https://wa.me/")
    assert "/api/og/crowdfunding/" in body["share_url"]
    # iter142C: light tier WebP is the default
    assert "tier=light" in body["png_story_url"]
    assert "format=story" in body["png_story_url"]
    assert "tier=light" in body["png_landscape_url"]
    assert "format=landscape" in body["png_landscape_url"]
    assert "tier=hd" in body["card_story_url_hd"]


def test_share_card_story_light_webp_under_150kb(slug):
    """Light story WebP must be ≤150KB for fast 3G rendering."""
    r = requests.get(
        f"{API}/api/crowdfunding/projects/{slug}/share-card?format=story&tier=light",
        headers={"Accept": "image/webp,*/*;q=0.8"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/webp"
    kb = len(r.content) / 1024
    assert kb < 150, f"Story light WebP too heavy: {kb:.1f} KB"
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (720, 1280)


def test_share_card_landscape_light_webp_under_80kb(slug):
    """Landscape light WebP must be ≤80KB."""
    r = requests.get(
        f"{API}/api/crowdfunding/projects/{slug}/share-card?format=landscape&tier=light",
        headers={"Accept": "image/webp,*/*"},
        timeout=15,
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    kb = len(r.content) / 1024
    assert kb < 80, f"Landscape light WebP too heavy: {kb:.1f} KB"
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (800, 420)


def test_share_card_jpeg_fallback_via_accept_header(slug):
    """When the client doesn't advertise image/webp, fallback to JPEG."""
    r = requests.get(
        f"{API}/api/crowdfunding/projects/{slug}/share-card?format=landscape&tier=light",
        headers={"Accept": "image/jpeg"},
        timeout=15,
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    kb = len(r.content) / 1024
    assert kb < 100, f"Landscape light JPEG too heavy: {kb:.1f} KB"


def test_share_card_hd_under_200kb(slug):
    """HD tier must stay under 200KB (CEO budget)."""
    for fmt_path in ["?format=story&tier=hd", "?format=landscape&tier=hd"]:
        r = requests.get(
            f"{API}/api/crowdfunding/projects/{slug}/share-card{fmt_path}",
            headers={"Accept": "image/webp"},
            timeout=15,
        )
        assert r.status_code == 200
        kb = len(r.content) / 1024
        assert kb < 200, f"{fmt_path} HD too heavy: {kb:.1f} KB"


def test_share_card_legacy_png_path_still_works(slug):
    """Backwards-compat: /share-card.png path should still serve an image."""
    r = requests.get(
        f"{API}/api/crowdfunding/projects/{slug}/share-card.png?format=landscape&tier=light",
        headers={"Accept": "image/webp"},
        timeout=15,
    )
    assert r.status_code == 200
    assert r.headers["content-type"] in ("image/webp", "image/jpeg")


def test_og_endpoint_returns_rich_meta(slug):
    r = requests.get(
        f"{API}/api/og/crowdfunding/{slug}",
        headers={"User-Agent": "WhatsApp/2.0"},
        allow_redirects=False,
    )
    assert r.status_code == 200, r.text
    html = r.text
    assert 'og:image' in html
    # OG endpoint serves the JPEG light landscape (universal compat).
    assert f'/api/crowdfunding/projects/{slug}/share-card' in html
    assert 'tier=light' in html
    assert f'/crowdfunding/p/{slug}' in html  # redirect target
    assert 'og:title' in html
    assert 'votes' in html.lower()


def test_share_unknown_slug_returns_404():
    r = requests.get(f"{API}/api/crowdfunding/projects/no-such-slug/share")
    assert r.status_code == 404


def test_og_unknown_slug_returns_fallback():
    """OG endpoint must always serve a 200 HTML for crawlers, even if the
    slug is invalid — otherwise WhatsApp shows an ugly error."""
    r = requests.get(f"{API}/api/og/crowdfunding/no-such-slug")
    assert r.status_code == 200
    assert "introuvable" in r.text.lower() or "japap" in r.text.lower()
