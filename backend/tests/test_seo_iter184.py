"""
iter184 — SEO Phase A tests
============================
Validates: robots.txt, sitemap index/sub-sitemaps, prerendered HTML for
products/users/posts, crawler middleware UA detection, slug helper.
"""
import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_URL = os.environ.get("TEST_API_URL") or "http://localhost:8001"


async def main():
    async with httpx.AsyncClient(base_url=API_URL, timeout=30.0) as c:
        # [1] robots.txt
        r = await c.get("/api/seo/robots.txt")
        assert r.status_code == 200
        body = r.text
        assert "User-agent: *" in body and "Sitemap:" in body
        assert "Disallow: /api/" in body and "Disallow: /admin" in body
        print(f"[1] robots.txt OK ({len(body)} chars)")

        # [2] sitemap index
        r = await c.get("/api/seo/sitemap.xml")
        assert r.status_code == 200
        assert "<sitemapindex" in r.text
        assert "sitemap-products.xml" in r.text
        assert r.headers.get("content-type", "").startswith("application/xml")
        print("[2] sitemap.xml index OK")

        # [3] sitemap-static
        r = await c.get("/api/seo/sitemap-static.xml")
        assert r.status_code == 200 and "<urlset" in r.text
        assert "japapmessenger.com/" in r.text or "<loc>" in r.text
        print("[3] sitemap-static.xml OK")

        # [4] sitemap-products — must contain at least 1 url with slug
        r = await c.get("/api/seo/sitemap-products.xml")
        assert r.status_code == 200
        assert "/marketplace/p/" in r.text
        assert "<priority>0.8</priority>" in r.text
        print("[4] sitemap-products.xml OK")

        # [5] sitemap-users
        r = await c.get("/api/seo/sitemap-users.xml")
        assert r.status_code == 200 and "<urlset" in r.text
        print("[5] sitemap-users.xml OK")

        # [6] sitemap-posts
        r = await c.get("/api/seo/sitemap-posts.xml")
        assert r.status_code == 200 and "<urlset" in r.text
        print("[6] sitemap-posts.xml OK")

        # Pick a real product
        import asyncpg
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        prod_id = await conn.fetchval(
            "SELECT product_id FROM products WHERE status='active' LIMIT 1")
        post_id = await conn.fetchval(
            "SELECT post_id FROM posts WHERE COALESCE(visibility,'public')='public' LIMIT 1")
        username = await conn.fetchval(
            "SELECT username FROM users WHERE username IS NOT NULL "
            "AND length(trim(username)) >= 3 LIMIT 1")
        await conn.close()

        # [7] /api/seo/product/{id}
        r = await c.get(f"/api/seo/product/{prod_id}")
        assert r.status_code == 200
        assert '<meta property="og:type" content="product"' in r.text
        assert '<meta property="og:title"' in r.text
        assert '<meta property="og:image"' in r.text
        assert '"@type":"Product"' in r.text  # JSON-LD
        assert "<link rel=\"canonical\"" in r.text
        print(f"[7] /api/seo/product/{prod_id} OK (full OG + JSON-LD)")

        # [8] product not found
        r = await c.get("/api/seo/product/prod_NONEXISTENT_X")
        assert r.status_code == 404
        print("[8] non-existent product → 404 OK")

        # [9] /api/seo/user/{handle}
        if username:
            r = await c.get(f"/api/seo/user/{username}")
            assert r.status_code == 200
            assert '<meta property="og:type" content="profile"' in r.text
            assert '"@type":"Person"' in r.text
            print(f"[9] /api/seo/user/{username} OK")
        else:
            print("[9] skipped (no public username)")

        # [10] /api/seo/post/{id}
        if post_id:
            r = await c.get(f"/api/seo/post/{post_id}")
            assert r.status_code == 200
            assert '<meta property="og:type" content="article"' in r.text
            print(f"[10] /api/seo/post/{post_id} OK")
        else:
            print("[10] skipped (no public post)")

        # [11] crawler middleware — Googlebot UA on /marketplace/p/{id}
        r = await c.get(f"/marketplace/p/{prod_id}",
                         headers={"User-Agent":
                                  "Mozilla/5.0 (compatible; Googlebot/2.1; "
                                  "+http://www.google.com/bot.html)"})
        assert r.status_code == 200
        # The middleware should have served prerendered HTML, not the React app
        assert '<meta property="og:type" content="product"' in r.text \
            or '"@type":"Product"' in r.text
        print(f"[11] crawler middleware Googlebot → prerendered OK")

        # [12] crawler middleware — Facebook bot
        r = await c.get(f"/marketplace/p/{prod_id}",
                         headers={"User-Agent":
                                  "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"})
        assert r.status_code == 200
        assert '<meta property="og:type"' in r.text
        print("[12] crawler middleware FB bot → prerendered OK")

        # [13] crawler middleware — REAL user (Chrome) does NOT get prerender
        # — falls through to whatever else listens on /, which on the bare
        # backend is 404. The key assertion is: response shouldn't contain
        # our minimal SEO body marker.
        r = await c.get(f"/marketplace/p/{prod_id}",
                         headers={"User-Agent":
                                  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"})
        # Backend has no React, so this returns 404 — that's fine, in prod K8s
        # the ingress sends humans to the React app. We just check the bot HTML
        # was NOT served.
        assert '<meta property="og:type" content="product"' not in r.text
        print(f"[13] real Chrome → fall-through (no prerender) OK")

        # [14] slug helper
        from services.seo_slug import slugify, product_canonical_url
        assert slugify("iPhone 13 — 128GB Étoile") == "iphone-13-128gb-etoile"
        assert slugify("¡Hola Mundo!") == "hola-mundo"
        assert slugify("") == ""
        url = product_canonical_url("prod_123", "Mon Produit Génial")
        assert url.endswith("/marketplace/p/prod_123/mon-produit-genial")
        print("[14] slugify + canonical URL builder OK")

    print("\n✅ iter184 — All 14 assertions PASS")


if __name__ == "__main__":
    asyncio.run(main())
