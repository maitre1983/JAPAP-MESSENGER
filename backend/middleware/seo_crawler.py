"""
iter184 — SEO Phase A — Crawler-aware Starlette middleware
==========================================================
Pour les User-Agents reconnus comme bots (Google, Bing, FB, WA, Twitter…),
on intercepte 3 familles de routes "publiques contenu" et on retourne du
HTML pré-rendu (meta + OG + JSON-LD). Les vrais utilisateurs continuent à
recevoir l'app React (le proxy Kubernetes route /* vers le frontend).

Routes interceptées :
  /marketplace/p/{id}[/...]
  /u/{username}            ← nouveau format SEO
  /user/{user_id}          ← legacy
  /post/{post_id}[/...]
  /                        ← homepage seulement pour bots (titre + OG)
"""
from __future__ import annotations

import logging
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular deps at startup
_RE_PRODUCT = re.compile(r"^/marketplace/p/([A-Za-z0-9_\-]+)(?:/.*)?$")
_RE_USER_HANDLE = re.compile(r"^/u/([A-Za-z0-9_]{3,32})/?$")
_RE_USER_ID = re.compile(r"^/user/([A-Za-z0-9_\-]+)/?$")
_RE_POST = re.compile(r"^/post/([A-Za-z0-9_\-]+)(?:/.*)?$")


async def _seo_homepage_html() -> str:
    from services.seo_slug import public_app_url
    from routes.seo import _render_seo_html
    base = public_app_url()
    title = "JAPAP — Réseau social, Marketplace et Wallet sans frontières"
    desc = ("Rejoins JAPAP : feed social, marketplace sécurisé, wallet USD, "
            "crowdfunding viral et IA intégrée. La super-app fintech & sociale.")
    json_ld = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"WebSite",'
        f'"name":"JAPAP","url":"{base}",'
        f'"potentialAction":{{"@type":"SearchAction",'
        f'"target":"{base}/services?search={{search_term_string}}",'
        '"query-input":"required name=search_term_string"}}}'
        '</script>'
    )
    return _render_seo_html(title=title, description=desc, url=base,
                              image=f"{base}/og-default.jpg", json_ld=json_ld)


class CrawlerSEOMiddleware(BaseHTTPMiddleware):
    """If a known bot UA hits a content URL, serve prerendered HTML."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path or "/"
        # Skip everything else first — fast path.
        if (path.startswith("/api/")
                or path.startswith("/static/")
                or path.startswith("/sitemap")
                or path == "/robots.txt"):
            return await call_next(request)

        ua = request.headers.get("user-agent", "")
        from routes.seo import is_crawler
        if not is_crawler(ua):
            return await call_next(request)

        try:
            # Product page
            m = _RE_PRODUCT.match(path)
            if m:
                from routes.seo import seo_product
                return await seo_product(m.group(1))
            # /u/{handle}
            m = _RE_USER_HANDLE.match(path)
            if m:
                from routes.seo import seo_user
                return await seo_user(m.group(1))
            # /user/{id}
            m = _RE_USER_ID.match(path)
            if m:
                from routes.seo import seo_user
                return await seo_user(m.group(1))
            # /post/{id}
            m = _RE_POST.match(path)
            if m:
                from routes.seo import seo_post
                return await seo_post(m.group(1))
            # Homepage — give the bot something useful
            if path in ("/", "/feed", "/services", "/marketplace"):
                html = await _seo_homepage_html()
                return Response(content=html, media_type="text/html",
                                 headers={"Cache-Control": "public, max-age=900"})
        except Exception as e:  # pragma: no cover
            logger.warning(f"[seo-mw] failed to prerender {path}: {e}")

        # Fallback — let the bot hit the real app.
        return await call_next(request)
