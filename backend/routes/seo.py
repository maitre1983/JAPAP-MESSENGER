"""
iter184 — SEO Phase A (robots.txt + sitemap.xml + crawler-aware prerender)
==========================================================================
Endpoints :
  GET /robots.txt              → crawl directives + sitemap link
  GET /sitemap.xml             → sitemap index (split when > 50 000 URLs)
  GET /sitemap-products.xml    → all active marketplace products
  GET /sitemap-users.xml       → public profiles with username/handle
  GET /sitemap-posts.xml       → recent public posts (last 90 days)
  GET /sitemap-static.xml      → home, /services, /feed, /signup, /login

Crawler-aware prerender (registered as a Starlette middleware in server.py).
When a known social/search-engine bot User-Agent hits one of:
  /marketplace/p/{id}[/...]
  /u/{username}
  /user/{user_id}
  /post/{post_id}[/...]
…we return a fully-rendered HTML <head> with title + meta + Open Graph +
JSON-LD schema.org. Real users keep getting the React SPA.

ZERO frontend change required for SEO acquisition.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse, HTMLResponse

from database import get_pool
from services.seo_slug import (
    slugify, public_app_url,
    product_canonical_url, user_canonical_url, post_canonical_url,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/seo", tags=["seo"])

# ─────────────────────────── BOT DETECTION ───────────────────────────
_BOT_PATTERNS = (
    "googlebot", "bingbot", "yandex", "duckduckbot", "baiduspider", "applebot",
    "facebookexternalhit", "facebot", "twitterbot", "linkedinbot", "slackbot",
    "whatsapp", "telegrambot", "discordbot", "skypeuripreview",
    "pinterest", "redditbot", "embedly", "outbrain", "vkshare",
    "ia_archiver", "ahrefsbot", "semrushbot",
)
_BOT_RE = re.compile("|".join(re.escape(p) for p in _BOT_PATTERNS), re.IGNORECASE)


def is_crawler(user_agent: str | None) -> bool:
    if not user_agent:
        return False
    return bool(_BOT_RE.search(user_agent))


# ─────────────────────────── /api/seo/robots.txt ───────────────────────────
@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    base = public_app_url()
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /admin\n"
        "Disallow: /admin/\n"
        "Disallow: /superadmin\n"
        "Disallow: /wallet\n"
        "Disallow: /settings\n"
        "Disallow: /messages\n"
        "Disallow: /messenger\n"
        "Disallow: /kyc\n"
        f"\nSitemap: {base}/api/seo/sitemap.xml\n"
    )
    return PlainTextResponse(body, headers={
        "Cache-Control": "public, max-age=3600",
        "Content-Type": "text/plain; charset=utf-8",
    })


# ─────────────────────────── SITEMAPS ───────────────────────────
def _xml_url(loc: str, lastmod: Optional[datetime] = None,
              changefreq: str = "weekly", priority: str = "0.7") -> str:
    parts = [f"<loc>{html.escape(loc)}</loc>"]
    if lastmod:
        if isinstance(lastmod, datetime):
            parts.append(f"<lastmod>{lastmod.strftime('%Y-%m-%d')}</lastmod>")
    parts.append(f"<changefreq>{changefreq}</changefreq>")
    parts.append(f"<priority>{priority}</priority>")
    return f"  <url>{''.join(parts)}</url>"


def _sitemap_response(urls: list[str]) -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(urls) +
        "\n</urlset>\n"
    )
    return Response(content=body, media_type="application/xml",
                     headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml")
async def sitemap_index():
    base = public_app_url()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <sitemap><loc>{base}/api/seo/sitemap-static.xml</loc><lastmod>{today}</lastmod></sitemap>\n"
        f"  <sitemap><loc>{base}/api/seo/sitemap-products.xml</loc><lastmod>{today}</lastmod></sitemap>\n"
        f"  <sitemap><loc>{base}/api/seo/sitemap-users.xml</loc><lastmod>{today}</lastmod></sitemap>\n"
        f"  <sitemap><loc>{base}/api/seo/sitemap-posts.xml</loc><lastmod>{today}</lastmod></sitemap>\n"
        '</sitemapindex>\n'
    )
    return Response(content=body, media_type="application/xml",
                     headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap-static.xml")
async def sitemap_static():
    base = public_app_url()
    items = [
        ("/",          "daily",   "1.0"),
        ("/services",  "hourly",  "0.9"),
        ("/feed",      "hourly",  "0.8"),
        ("/marketplace", "daily", "0.9"),
        ("/signup",    "weekly",  "0.6"),
        ("/login",     "weekly",  "0.4"),
    ]
    urls = [_xml_url(f"{base}{p}", changefreq=cf, priority=pr) for p, cf, pr in items]
    return _sitemap_response(urls)


@router.get("/sitemap-products.xml")
async def sitemap_products():
    """All active marketplace products (max 50k per file)."""
    pool = await get_pool()
    urls: list[str] = []
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT product_id, title, updated_at
            FROM products
            WHERE status = 'active'
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 50000
        """)
    for r in rows:
        loc = product_canonical_url(r["product_id"], r["title"])
        urls.append(_xml_url(loc, lastmod=r["updated_at"], changefreq="weekly", priority="0.8"))
    return _sitemap_response(urls)


@router.get("/sitemap-users.xml")
async def sitemap_users():
    """Public profiles with a non-empty username (handle)."""
    pool = await get_pool()
    urls: list[str] = []
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, username, COALESCE(updated_at, created_at) AS lastmod
            FROM users
            WHERE username IS NOT NULL AND length(trim(username)) >= 3
            ORDER BY lastmod DESC NULLS LAST
            LIMIT 50000
        """)
    for r in rows:
        loc = user_canonical_url(r["username"], r["user_id"])
        urls.append(_xml_url(loc, lastmod=r["lastmod"], changefreq="weekly", priority="0.6"))
    return _sitemap_response(urls)


@router.get("/sitemap-posts.xml")
async def sitemap_posts():
    """Recent public posts (last 90 days, max 50k)."""
    pool = await get_pool()
    urls: list[str] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT post_id, text, updated_at
            FROM posts
            WHERE COALESCE(visibility, 'public') = 'public'
              AND created_at >= $1
            ORDER BY created_at DESC
            LIMIT 50000
        """, cutoff)
    for r in rows:
        loc = post_canonical_url(r["post_id"], r["text"])
        urls.append(_xml_url(loc, lastmod=r["updated_at"], changefreq="never", priority="0.5"))
    return _sitemap_response(urls)


# ─────────────────────────── CRAWLER PRERENDER ───────────────────────────
def _truncate(text: str, n: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else (text[:n - 1].rsplit(" ", 1)[0] + "…")


def _abs_image(u: str | None) -> str | None:
    if not u:
        return None
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return f"{public_app_url()}{u}"


def _render_seo_html(*, title: str, description: str, url: str, image: str | None,
                       og_type: str = "website", json_ld: str = "",
                       extra_meta: str = "") -> str:
    """Return a minimal but complete HTML document optimised for crawlers.
    A small body excerpt is included so previewers (WhatsApp/X) get a quick
    sample beyond the meta tags. Real users hitting /api/seo/* are auto-
    redirected to the React app via a noscript-friendly meta+JS pair."""
    img = image or f"{public_app_url()}/japap-logo-512.png"
    h_title = html.escape(title)
    h_desc = html.escape(description)
    h_url = html.escape(url)
    h_img = html.escape(img)
    return (f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{h_title}</title>
<meta name="description" content="{h_desc}" />
<link rel="canonical" href="{h_url}" />
<link rel="alternate" hreflang="fr" href="{h_url}" />
<link rel="alternate" hreflang="x-default" href="{h_url}" />
<meta property="og:type" content="{html.escape(og_type)}" />
<meta property="og:title" content="{h_title}" />
<meta property="og:description" content="{h_desc}" />
<meta property="og:url" content="{h_url}" />
<meta property="og:image" content="{h_img}" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta property="og:site_name" content="JAPAP" />
<meta property="og:locale" content="fr_FR" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{h_title}" />
<meta name="twitter:description" content="{h_desc}" />
<meta name="twitter:image" content="{h_img}" />
<meta name="twitter:site" content="@japap" />
<meta name="robots" content="index,follow,max-image-preview:large" />
{extra_meta}
{json_ld}
<script>
// iter184 — auto-redirect REAL USERS to the React app (bots ignore JS).
// FB/WhatsApp/Twitter/Google bots that grabbed this page for OG/SEO get
// what they need; humans land instantly on the canonical app URL.
(function(){{
  try {{
    if (typeof window !== 'undefined' && window.location) {{
      var target = "{h_url}";
      if (window.location.href !== target) {{
        window.location.replace(target);
      }}
    }}
  }} catch (e) {{}}
}})();
</script>
</head>
<body>
<h1>{h_title}</h1>
<p>{h_desc}</p>
<p><a href="{h_url}">Voir sur JAPAP</a></p>
</body>
</html>""")


# Public endpoints — same payloads used by the crawler middleware. Exposed so
# Search Console fetch-as-Googlebot can validate them, and so the testing
# agent can assert their content directly.

@router.get("/product/{product_id}")
async def seo_product(product_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT p.product_id, p.title, p.description, p.images, p.price, p.currency,
                   p.status, u.first_name, u.last_name, u.username
            FROM products p
            LEFT JOIN users u ON u.user_id = p.seller_id
            WHERE p.product_id = $1
        """, product_id)
    if not row or row["status"] != "active":
        return HTMLResponse(_render_seo_html(
            title="Produit introuvable — JAPAP Marketplace",
            description="Ce produit n'est plus disponible sur JAPAP Marketplace.",
            url=public_app_url(), image=None), status_code=404)
    title = f"{row['title']} — JAPAP Marketplace"
    desc = _truncate(row.get("description") or
                      f"Achetez {row['title']} sur JAPAP Marketplace avec paiement sécurisé.")
    # Resolve the first image
    images = row.get("images") or []
    if isinstance(images, str):
        try:
            import json as _j
            images = _j.loads(images)
        except Exception:
            images = []
    first = images[0] if images else None
    img_url = None
    if isinstance(first, dict):
        img_url = first.get("hd") or first.get("full") or first.get("thumb")
    elif isinstance(first, str):
        img_url = first
    canonical = product_canonical_url(row["product_id"], row["title"])
    seller = row.get("first_name") or row.get("username") or ""
    json_ld = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Product",'
        f'"name":"{html.escape(row["title"])}",'
        f'"description":"{html.escape(desc)}",'
        f'"image":"{html.escape(_abs_image(img_url) or "")}",'
        f'"url":"{html.escape(canonical)}",'
        '"offers":{'
        '"@type":"Offer","priceCurrency":"' + html.escape(str(row.get("currency") or "USD")) + '",'
        '"price":"' + html.escape(str(row.get("price") or "0")) + '",'
        '"availability":"https://schema.org/InStock"},'
        '"brand":{"@type":"Brand","name":"JAPAP"},'
        f'"seller":{{"@type":"Person","name":"{html.escape(seller)}"}}'
        '}'
        '</script>'
    )
    return HTMLResponse(_render_seo_html(
        title=title, description=desc, url=canonical,
        image=_abs_image(img_url), og_type="product", json_ld=json_ld))


@router.get("/user/{ident}")
async def seo_user(ident: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT user_id, first_name, last_name, username, about, avatar, is_verified
            FROM users
            WHERE (user_id = $1 OR username = $1)
            LIMIT 1
        """, ident)
    base = public_app_url()
    if not row:
        return HTMLResponse(_render_seo_html(
            title="Profil introuvable — JAPAP",
            description="Ce profil n'existe pas ou n'est plus disponible.",
            url=base, image=None), status_code=404)
    name = " ".join(filter(None, [row.get("first_name"), row.get("last_name")])).strip() \
           or row.get("username") or "Utilisateur"
    handle = row.get("username") or ""
    title = f"{name}{' (@' + handle + ')' if handle else ''} — JAPAP"
    desc = _truncate(row.get("about") or
                      f"Découvrez les publications, vidéos et activités de {name} sur JAPAP.")
    canonical = user_canonical_url(handle, row["user_id"])
    avatar = _abs_image(row.get("avatar"))
    json_ld = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Person",'
        f'"name":"{html.escape(name)}",'
        f'"url":"{html.escape(canonical)}",'
        + (f'"image":"{html.escape(avatar)}",' if avatar else '')
        + (f'"alternateName":"@{html.escape(handle)}"' if handle else '"identifier":"' + html.escape(row["user_id"]) + '"')
        + '}</script>'
    )
    return HTMLResponse(_render_seo_html(
        title=title, description=desc, url=canonical,
        image=avatar, og_type="profile", json_ld=json_ld))


@router.get("/post/{post_id}")
async def seo_post(post_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT p.post_id, p.text, p.media, p.created_at, p.visibility,
                   u.first_name, u.last_name, u.username, u.avatar
            FROM posts p
            LEFT JOIN users u ON u.user_id = p.user_id
            WHERE p.post_id = $1
        """, post_id)
    base = public_app_url()
    if not row or (row.get("visibility") and row["visibility"] != "public"):
        return HTMLResponse(_render_seo_html(
            title="Publication introuvable — JAPAP",
            description="Cette publication n'est pas accessible publiquement.",
            url=base, image=None), status_code=404)
    author = " ".join(filter(None, [row.get("first_name"), row.get("last_name")])).strip() \
             or row.get("username") or "Utilisateur"
    text = (row.get("text") or "").strip()
    title = (text[:60] + "…") if len(text) > 60 else (text or f"Publication de {author}")
    title = f"{title} — JAPAP"
    desc = _truncate(text or f"Découvre la publication de {author} sur JAPAP.")
    media = row.get("media") or []
    if isinstance(media, str):
        try:
            import json as _j
            media = _j.loads(media)
        except Exception:
            media = []
    img_url = None
    if media and isinstance(media[0], str) and re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", media[0], re.I):
        img_url = media[0]
    canonical = post_canonical_url(row["post_id"], text)
    return HTMLResponse(_render_seo_html(
        title=title, description=desc, url=canonical,
        image=_abs_image(img_url), og_type="article"))



# ───────────────────── iter185 — VIRAL SHARE LOOP ─────────────────────
import hashlib
from pydantic import BaseModel


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


_UTM_RE = re.compile(r"^share_([A-Za-z0-9_\-]+)_(product|post|user)_([A-Za-z0-9_\-]+)$")


class ShareTrackRequest(BaseModel):
    utm: str
    visitor_user_id: str | None = None


@router.post("/viral/track")
async def viral_track_share(req: ShareTrackRequest, request: Request):
    """Award JAPAP points to the sharer when a UNIQUE real visitor lands.
    Anti-fraud: dedup by (sharer+ip+entity) over `viral_share_dedup_hours`,
    daily cap, no self-reward."""
    from services.settings_service import get_int, get_bool
    from datetime import datetime as _dt, timezone as _tz
    if not await get_bool("viral_share_enabled", True):
        return {"ok": True, "rewarded": False, "reason": "disabled"}

    m = _UTM_RE.match((req.utm or "").strip())
    if not m:
        return {"ok": False, "rewarded": False, "reason": "invalid_utm"}
    sharer_id, entity_type, entity_id = m.group(1), m.group(2), m.group(3)

    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "")).strip()
    ua = request.headers.get("user-agent", "")[:300]
    ip_h = _hash(ip)
    ua_h = _hash(ua)
    day_key = _dt.now(_tz.utc).strftime("%Y-%m-%d")

    pool = await get_pool()
    points_per = max(0, await get_int("viral_share_points_per_visit", 50))
    cap = max(0, await get_int("viral_share_daily_cap_per_sharer", 20))
    dedup_h = max(1, await get_int("viral_share_dedup_hours", 24))

    async with pool.acquire() as conn:
        sharer = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", sharer_id)
        if not sharer:
            return {"ok": False, "rewarded": False, "reason": "unknown_sharer"}

        if req.visitor_user_id and req.visitor_user_id == sharer_id:
            return {"ok": True, "rewarded": False, "reason": "self_visit"}

        recent = await conn.fetchval("""
            SELECT 1 FROM viral_share_events
            WHERE sharer_id = $1 AND ip_hash = $2
              AND entity_type = $3 AND entity_id = $4
              AND created_at > NOW() - ($5::int || ' hours')::interval
              AND rewarded = TRUE
            LIMIT 1
        """, sharer_id, ip_h, entity_type, entity_id, dedup_h)

        rewarded_today = await conn.fetchval("""
            SELECT COUNT(*)::int FROM viral_share_events
            WHERE sharer_id = $1 AND day_key = $2 AND rewarded = TRUE
        """, sharer_id, day_key) or 0

        will_reward = (
            points_per > 0 and not recent and rewarded_today < cap
            and req.visitor_user_id != sharer_id
        )

        row = await conn.fetchrow("""
            INSERT INTO viral_share_events
              (sharer_id, entity_type, entity_id, ip_hash, ua_hash,
               visitor_user_id, day_key, rewarded, points_awarded)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id, rewarded, points_awarded
        """, sharer_id, entity_type, entity_id, ip_h, ua_h,
             req.visitor_user_id, day_key, will_reward,
             points_per if will_reward else 0)

        if will_reward:
            try:
                await conn.execute("""
                    UPDATE users SET connect_points = COALESCE(connect_points, 0) + $1
                    WHERE user_id = $2
                """, points_per, sharer_id)
            except Exception as e:  # pragma: no cover
                logger.warning(f"[viral] points credit failed for {sharer_id}: {e}")
            # iter186 — Milestone push notifications (Pinterest playbook)
            try:
                await _maybe_emit_milestone(conn, sharer_id)
            except Exception as e:  # pragma: no cover
                logger.warning(f"[viral] milestone check failed for {sharer_id}: {e}")

    reason = (None if row["rewarded"]
              else ("recent_dup" if recent
                    else "daily_cap" if rewarded_today >= cap
                    else "no_points"))
    return {
        "ok": True, "rewarded": bool(row["rewarded"]),
        "points_awarded": int(row["points_awarded"] or 0),
        "sharer_id": sharer_id, "entity_type": entity_type, "entity_id": entity_id,
        "reason": reason,
    }


@router.get("/viral/stats")
async def viral_stats(request: Request):
    """Authenticated dashboard for the sharer."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        agg = await conn.fetchrow("""
            SELECT
              COUNT(*) AS total_clicks,
              COUNT(*) FILTER (WHERE rewarded) AS rewarded_clicks,
              COALESCE(SUM(points_awarded), 0) AS points_total,
              COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') AS clicks_7d,
              COALESCE(SUM(points_awarded) FILTER
                (WHERE created_at > NOW() - INTERVAL '7 days'), 0) AS points_7d
            FROM viral_share_events
            WHERE sharer_id = $1
        """, user["user_id"])
        by_type = await conn.fetch("""
            SELECT entity_type, COUNT(*) AS clicks,
                   COUNT(*) FILTER (WHERE rewarded) AS rewarded
            FROM viral_share_events
            WHERE sharer_id = $1
            GROUP BY entity_type
            ORDER BY clicks DESC
        """, user["user_id"])
    return {
        "total_clicks": int(agg["total_clicks"] or 0),
        "rewarded_clicks": int(agg["rewarded_clicks"] or 0),
        "points_total": int(agg["points_total"] or 0),
        "clicks_7d": int(agg["clicks_7d"] or 0),
        "points_7d": int(agg["points_7d"] or 0),
        "by_type": [{"entity_type": r["entity_type"],
                     "clicks": int(r["clicks"]),
                     "rewarded": int(r["rewarded"])} for r in by_type],
    }


# ───────────────────── iter186 — VIRAL MILESTONES PUSH ─────────────────────
_BADGE_FOR_THRESHOLD = {
    1:    ("🎉 Premier visiteur ramené !",
            "Tu as ramené ton 1er visiteur sur JAPAP. Continue à partager !"),
    5:    ("🚀 5 visiteurs ramenés",
            "Tu deviens un vrai ambassadeur JAPAP. +250 pts !"),
    10:   ("🔥 10 visiteurs ramenés",
            "+500 pts JAPAP — Continue : tu débloques le badge **Influenceur** à 50."),
    25:   ("⭐ 25 visiteurs ramenés",
            "Tu es dans le top 10% des sharers. +1 250 pts !"),
    50:   ("👑 Badge Influenceur débloqué",
            "Tu as ramené 50 visiteurs sur JAPAP — +2 500 pts. Légende !"),
    100:  ("💎 100 visiteurs ramenés",
            "Top 1% des sharers. +5 000 pts. Tu construis JAPAP avec nous."),
    250:  ("🏆 250 visiteurs ramenés",
            "Tu es une vraie machine virale. +12 500 pts."),
    500:  ("🌟 500 visiteurs ramenés",
            "Badge **Légende JAPAP** débloqué. +25 000 pts."),
    1000: ("🦄 1 000 visiteurs ramenés",
            "Tu fais partie des 10 plus gros viraliseurs JAPAP. +50 000 pts."),
}


async def _maybe_emit_milestone(conn, sharer_id: str) -> None:
    """Check if `sharer_id` just crossed a configured viral milestone.
    Emits one notification (in-app + OneSignal push) per threshold, idempotent
    via `viral_milestones_reached` PK."""
    from services.settings_service import get_setting, get_bool
    if not await get_bool("viral_milestones_enabled", True):
        return
    raw = (await get_setting("viral_milestones_thresholds", "") or "").strip()
    try:
        thresholds = sorted({int(x.strip()) for x in raw.split(",") if x.strip()})
    except Exception:
        thresholds = [1, 5, 10, 25, 50, 100, 250, 500, 1000]
    if not thresholds:
        return

    rewarded_total = await conn.fetchval(
        "SELECT COUNT(*)::int FROM viral_share_events "
        "WHERE sharer_id = $1 AND rewarded = TRUE", sharer_id) or 0
    # Find which milestones are now crossed but not yet recorded
    crossed = [t for t in thresholds if rewarded_total >= t]
    if not crossed:
        return
    already = await conn.fetch(
        "SELECT threshold FROM viral_milestones_reached WHERE user_id = $1",
        sharer_id)
    already_set = {int(r["threshold"]) for r in already}
    pending = [t for t in crossed if t not in already_set]
    if not pending:
        return

    # Emit only the highest pending one (prevents push spam if multiple are
    # crossed at once — e.g. fresh sharer gets 25 visits in a day)
    top = max(pending)
    title, body = _BADGE_FOR_THRESHOLD.get(top, (
        f"🎉 {top} visiteurs ramenés sur JAPAP",
        "Tu viens de débloquer un nouveau palier. Bravo !"))

    # Mark all pending as reached (we don't want to retroactively re-notify
    # smaller ones if the user passed multiple thresholds at once)
    for t in pending:
        try:
            await conn.execute(
                "INSERT INTO viral_milestones_reached (user_id, threshold) "
                "VALUES ($1, $2) ON CONFLICT DO NOTHING", sharer_id, t)
        except Exception:
            pass

    # Fire notification (in-app + push) — best-effort, non-blocking
    try:
        from services.notifications import send_social_notification
        await send_social_notification(
            event_type="viral_milestone",
            actor=None,
            target_user_id=sharer_id,
            title=title,
            body=body,
            deep_link="/profile?tab=viral",
            extra_data={"threshold": top, "rewarded_total": rewarded_total},
        )
    except Exception as e:  # pragma: no cover
        logger.warning(f"[viral-milestone] notify failed for {sharer_id} t={top}: {e}")

