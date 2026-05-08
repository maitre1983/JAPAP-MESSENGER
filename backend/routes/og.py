"""
JAPAP — Open Graph preview renderer (iter77)
=============================================
`GET /api/og/post/{post_id}` — returns a minimal HTML document with the right
Open Graph + Twitter-Card meta tags so WhatsApp, Facebook, Twitter/X, Slack,
Discord and LinkedIn all render a rich preview (title, description, image)
when a shared JAPAP post URL is unfurled.

Real users who click this URL are instantly redirected to the SPA via a
`<meta http-equiv=refresh>` tag — the intermediate HTML is only a few hundred
bytes so the perceived latency is minimal.

This endpoint is intentionally public (no auth) because social-network
scrapers never send credentials. We only expose the fields that are already
visible on a public feed post.
"""
import html
import logging
import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from database import get_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/og", tags=["og"])


def _frontend_base(request: Request) -> str:
    """Resolve the public frontend origin for building the SPA redirect target."""
    env_url = (
        os.environ.get("FRONTEND_URL")
        or os.environ.get("PUBLIC_FRONTEND_URL")
        or os.environ.get("PUBLIC_APP_URL")
        or os.environ.get("REACT_APP_BACKEND_URL")
    )
    if env_url:
        return env_url.rstrip("/")
    # Fallback: reconstruct from incoming request (useful in local dev).
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", "")
    return f"{scheme}://{host}" if host else ""


def _first_image(media) -> str | None:
    """Extract the first renderable image URL from a post's media column.
    Accepts the list shapes we support in the feed:
      [{"type":"image","url":"..."}]  |  ["/api/upload/..."]  |  "json-string"
    """
    if not media:
        return None
    if isinstance(media, str):
        try:
            import json as _json
            media = _json.loads(media)
        except Exception:
            return None
    if not isinstance(media, list):
        return None
    for m in media:
        if isinstance(m, dict):
            url = m.get('url') or ''
            mtype = (m.get('type') or '').lower()
            if url and mtype in ('', 'image'):
                return url
        elif isinstance(m, str):
            if m.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                return m
    return None


@router.get("/post/{post_id}", response_class=HTMLResponse)
async def og_post_preview(post_id: str, request: Request):
    """Public OG preview page.
    - Scrapers read the meta tags and build the preview card.
    - Real users' browsers execute the meta refresh and land on the SPA.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT p.post_id, p.text, p.media, p.created_at,
                      p.likes_count, p.comments_count,
                      u.first_name, u.last_name, u.username, u.avatar, u.is_verified
               FROM posts p JOIN users u ON p.user_id = u.user_id
               WHERE p.post_id = $1 AND p.visibility = 'public'""",
            post_id,
        )

    frontend = _frontend_base(request)
    target_url = f"{frontend}/post/{post_id}" if frontend else f"/post/{post_id}"
    canonical = f"{frontend}/api/og/post/{post_id}" if frontend else f"/api/og/post/{post_id}"

    if not row:
        # Still render a valid OG page — some scrapers cache permanent 404s.
        title_txt = "JAPAP — Publication introuvable"
        desc_txt = "Cette publication JAPAP n'est plus disponible."
        image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""
        safe_target = target_url
    else:
        author = (f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
                  or row['username'] or 'Utilisateur')
        title_txt = f"{author} sur JAPAP"
        raw_text = (row['text'] or '').strip()
        if raw_text:
            # Keep previews readable; WhatsApp truncates anyway ~300 chars.
            desc_txt = raw_text if len(raw_text) <= 280 else raw_text[:277] + '…'
        else:
            desc_txt = f"Rejoignez {author} sur JAPAP, la super-app africaine."
        image_rel = _first_image(row['media'])
        if image_rel:
            # Resolve to absolute URL if stored relatively.
            if image_rel.startswith('http'):
                image_url = image_rel
            elif frontend:
                image_url = f"{frontend}{image_rel if image_rel.startswith('/') else '/' + image_rel}"
            else:
                image_url = image_rel
        else:
            image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""
        safe_target = target_url

    # HTML-escape every untrusted string before injection.
    title_esc = html.escape(title_txt, quote=True)
    desc_esc = html.escape(desc_txt, quote=True)
    image_esc = html.escape(image_url, quote=True)
    url_esc = html.escape(canonical, quote=True)
    target_esc = html.escape(safe_target, quote=True)

    body = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{title_esc}</title>
<meta name="description" content="{desc_esc}">

<!-- Open Graph / Facebook / WhatsApp -->
<meta property="og:type" content="article">
<meta property="og:site_name" content="JAPAP">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{desc_esc}">
<meta property="og:image" content="{image_esc}">
<meta property="og:image:alt" content="{title_esc}">
<meta property="og:url" content="{url_esc}">
<meta property="og:locale" content="fr_FR">

<!-- Twitter / X -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title_esc}">
<meta name="twitter:description" content="{desc_esc}">
<meta name="twitter:image" content="{image_esc}">

<!-- Canonical + redirect for real users -->
<link rel="canonical" href="{url_esc}">
<meta http-equiv="refresh" content="0;url={target_esc}">
<script>window.location.replace({target_esc!r});</script>

<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background:#0F056B; color:#fff; margin:0;
         min-height:100vh; display:flex; align-items:center; justify-content:center;
         text-align:center; padding:24px; }}
  .card {{ max-width: 440px; }}
  .title {{ font-size: 22px; font-weight: 700; margin-bottom: 12px; }}
  .desc {{ opacity: .8; font-size: 15px; line-height: 1.5; margin-bottom: 20px;
           white-space: pre-wrap; }}
  a.cta {{ display: inline-block; background: #E01C2E; color: #fff; padding: 12px 22px;
           border-radius: 999px; font-weight: 700; text-decoration: none; }}
</style>
</head>
<body>
<noscript>
<div class="card">
  <div class="title">{title_esc}</div>
  <p class="desc">{desc_esc}</p>
  <a class="cta" href="{target_esc}">Ouvrir sur JAPAP</a>
</div>
</noscript>
</body>
</html>
"""
    # Short cache so scraper revisits pick up edited posts quickly, but still
    # spare the DB when the same URL is shared in bulk.
    headers = {"Cache-Control": "public, max-age=300"}
    return HTMLResponse(content=body, headers=headers)


# ──────────────────────────────────────────────────────────────────────────
#  iter217 — Reels OG preview (TikTok-style viral deep links)
#  Used as the WhatsApp/iMessage/Twitter/Discord share URL so social
#  scrapers fetch a rich preview (creator + caption + thumbnail + the
#  video itself for og:video). On iMessage iOS this displays the actual
#  Reel as a playable inline preview — exactly what makes TikTok/Reels
#  links go viral. Real users meta-refresh into the SPA /reels/{id}.
# ──────────────────────────────────────────────────────────────────────────

@router.get("/reel/{reel_id}", response_class=HTMLResponse)
async def og_reel_preview(reel_id: str, request: Request):
    """Public OG preview page for a single Reel.
    - Scrapers read og:title, og:description, og:image (thumbnail) and
      og:video to build the rich card.
    - Real users hit the meta refresh + JS replace and land on
      /reels/{reel_id} in the SPA, deep-linking into the right Reel.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT r.reel_id, r.video_url, r.thumbnail_url, r.caption,
                      r.duration, r.likes_count, r.views_count, r.created_at,
                      u.first_name, u.last_name, u.username, u.avatar
               FROM reels r JOIN users u ON r.user_id = u.user_id
               WHERE r.reel_id = $1""",
            reel_id,
        )

    frontend = _frontend_base(request)
    target_url = f"{frontend}/reels/{reel_id}" if frontend else f"/reels/{reel_id}"
    canonical = f"{frontend}/api/og/reel/{reel_id}" if frontend else f"/api/og/reel/{reel_id}"

    def _absolutize(u: str) -> str:
        if not u:
            return ""
        if u.startswith("http"):
            return u
        if not frontend:
            return u
        return f"{frontend}{u if u.startswith('/') else '/' + u}"

    if not row:
        title_txt = "JAPAP — Reel introuvable"
        desc_txt = "Ce Reel JAPAP n'est plus disponible."
        image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""
        video_url = ""
    else:
        author = (f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
                  or row['username'] or 'Utilisateur')
        title_txt = f"{author} sur JAPAP Reels"
        raw_caption = (row['caption'] or '').strip()
        if raw_caption:
            desc_txt = raw_caption if len(raw_caption) <= 280 else raw_caption[:277] + '…'
        else:
            desc_txt = (
                f"Regarde ce Reel de {author} sur JAPAP — "
                f"{row['views_count'] or 0} vues, {row['likes_count'] or 0} likes."
            )
        image_url = _absolutize(row['thumbnail_url'])
        if not image_url:
            image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""
        video_url = _absolutize(row['video_url'])

    title_esc = html.escape(title_txt, quote=True)
    desc_esc = html.escape(desc_txt, quote=True)
    image_esc = html.escape(image_url, quote=True)
    video_esc = html.escape(video_url, quote=True)
    url_esc = html.escape(canonical, quote=True)
    target_esc = html.escape(target_url, quote=True)

    # og:video tags trigger the inline video preview on iMessage,
    # Twitter/X (player card), Telegram and Discord. We expose both
    # the .mp4 URL (most universal) and the secure_url variant so
    # crawlers behind https-only policies (Twitter) accept it.
    video_meta = ""
    if video_url:
        video_meta = f"""
<meta property="og:video" content="{video_esc}">
<meta property="og:video:url" content="{video_esc}">
<meta property="og:video:secure_url" content="{video_esc}">
<meta property="og:video:type" content="video/mp4">
<meta property="og:video:width" content="720">
<meta property="og:video:height" content="1280">
<meta name="twitter:card" content="player">
<meta name="twitter:player" content="{video_esc}">
<meta name="twitter:player:width" content="720">
<meta name="twitter:player:height" content="1280">
<meta name="twitter:player:stream" content="{video_esc}">
<meta name="twitter:player:stream:content_type" content="video/mp4">"""
    else:
        video_meta = '<meta name="twitter:card" content="summary_large_image">'

    body = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{title_esc}</title>
<meta name="description" content="{desc_esc}">

<!-- Open Graph / Facebook / WhatsApp -->
<meta property="og:type" content="video.other">
<meta property="og:site_name" content="JAPAP">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{desc_esc}">
<meta property="og:image" content="{image_esc}">
<meta property="og:image:alt" content="{title_esc}">
<meta property="og:image:width" content="720">
<meta property="og:image:height" content="1280">
<meta property="og:url" content="{url_esc}">
<meta property="og:locale" content="fr_FR">
{video_meta}

<!-- Twitter / X (extras for video player card) -->
<meta name="twitter:title" content="{title_esc}">
<meta name="twitter:description" content="{desc_esc}">
<meta name="twitter:image" content="{image_esc}">

<!-- Canonical + redirect for real users -->
<link rel="canonical" href="{url_esc}">
<meta http-equiv="refresh" content="0;url={target_esc}">
<script>window.location.replace({target_esc!r});</script>

<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background:#0F056B; color:#fff; margin:0;
         min-height:100vh; display:flex; align-items:center; justify-content:center;
         text-align:center; padding:24px; }}
  .card {{ max-width: 440px; }}
  .thumb {{ width: 220px; aspect-ratio: 9/16; background:#000; border-radius: 16px;
            margin: 0 auto 18px; background-size: cover; background-position: center; }}
  .title {{ font-size: 22px; font-weight: 700; margin-bottom: 12px; }}
  .desc {{ opacity: .8; font-size: 15px; line-height: 1.5; margin-bottom: 20px;
           white-space: pre-wrap; }}
  a.cta {{ display: inline-block; background: #E01C2E; color: #fff; padding: 12px 22px;
           border-radius: 999px; font-weight: 700; text-decoration: none; }}
</style>
</head>
<body>
<noscript>
<div class="card">
  <div class="thumb" style="background-image:url('{image_esc}');"></div>
  <div class="title">{title_esc}</div>
  <p class="desc">{desc_esc}</p>
  <a class="cta" href="{target_esc}">Ouvrir sur JAPAP</a>
</div>
</noscript>
</body>
</html>
"""
    headers = {"Cache-Control": "public, max-age=300"}
    return HTMLResponse(content=body, headers=headers)


# ──────────────────────────────────────────────────────────────────────────
#  iter141nineE — Payment Request OG preview
#  Used as the WhatsApp/iMessage/SMS share URL so social scrapers fetch a
#  rich preview (requester name + amount + note) BEFORE the user even
#  clicks. Real users land here for ~50ms then meta-refresh to /pay/<id>.
# ──────────────────────────────────────────────────────────────────────────

@router.get("/pay/{request_id}", response_class=HTMLResponse)
async def og_pay_preview(request_id: str, request: Request):
    """OG-rich landing for a payment request. Public — scrapers don't auth."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT pr.amount, pr.currency, pr.note, pr.status,
                      u.first_name, u.last_name, u.username, u.avatar
               FROM payment_requests pr
               JOIN users u ON u.user_id = pr.requester_id
               WHERE pr.request_id = $1""",
            request_id,
        )

    frontend = _frontend_base(request)
    target_url = f"{frontend}/pay/{request_id}" if frontend else f"/pay/{request_id}"
    canonical = f"{frontend}/api/og/pay/{request_id}" if frontend else f"/api/og/pay/{request_id}"

    if not row:
        title_txt = "JAPAP — Demande introuvable"
        desc_txt = "Cette demande de paiement n'est plus disponible."
        image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""
    else:
        author = (f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
                  or row['username'] or 'JAPAP user')
        amt = f"{float(row['amount']):,.0f}".replace(",", " ")
        title_txt = f"{author} te demande {amt} {row['currency']}"
        if row['status'] == 'paid':
            title_txt = f"✅ Demande de {author} déjà payée"
        elif row['status'] == 'cancelled':
            title_txt = f"❌ Demande de {author} annulée"
        elif row['status'] == 'expired':
            title_txt = f"⌛ Demande de {author} expirée"
        note = (row['note'] or '').strip()
        if note:
            desc_txt = f"« {note} » — Paie en 1 clic via JAPAP Wallet."
        else:
            desc_txt = "Paie en 1 clic via JAPAP Wallet — la super-app africaine."
        avatar = (row['avatar'] or '').strip()
        if avatar.startswith('http'):
            image_url = avatar
        elif avatar and frontend:
            image_url = f"{frontend}{avatar if avatar.startswith('/') else '/' + avatar}"
        else:
            image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""

    title_esc = html.escape(title_txt, quote=True)
    desc_esc = html.escape(desc_txt, quote=True)
    image_esc = html.escape(image_url, quote=True)
    url_esc = html.escape(canonical, quote=True)
    target_esc = html.escape(target_url, quote=True)

    body = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{title_esc}</title>
<meta name="description" content="{desc_esc}">

<!-- Open Graph / Facebook / WhatsApp / iMessage / Slack / Discord -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="JAPAP Wallet">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{desc_esc}">
<meta property="og:image" content="{image_esc}">
<meta property="og:image:alt" content="{title_esc}">
<meta property="og:url" content="{url_esc}">
<meta property="og:locale" content="fr_FR">

<!-- Twitter / X -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title_esc}">
<meta name="twitter:description" content="{desc_esc}">
<meta name="twitter:image" content="{image_esc}">

<!-- Universal Links / App Links — when native JAPAP apps ship, these
     trigger "Open with JAPAP" prompts on iOS Camera + Android. The
     associated AASA / assetlinks.json files at /.well-known/ already
     declare that /pay/* paths belong to the app. -->

<link rel="canonical" href="{url_esc}">
<meta http-equiv="refresh" content="0;url={target_esc}">
<script>window.location.replace({target_esc!r});</script>

<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background:#0F056B; color:#fff; margin:0;
         min-height:100vh; display:flex; align-items:center; justify-content:center;
         text-align:center; padding:24px; }}
  .card {{ max-width: 440px; }}
  .title {{ font-size: 22px; font-weight: 700; margin-bottom: 12px; }}
  .desc {{ opacity: .8; font-size: 15px; line-height: 1.5; margin-bottom: 20px;
           white-space: pre-wrap; }}
  a.cta {{ display: inline-block; background: #E01C2E; color: #fff; padding: 12px 22px;
           border-radius: 999px; font-weight: 700; text-decoration: none; }}
</style>
</head>
<body>
<noscript>
<div class="card">
  <div class="title">{title_esc}</div>
  <p class="desc">{desc_esc}</p>
  <a class="cta" href="{target_esc}">Payer sur JAPAP</a>
</div>
</noscript>
</body>
</html>
"""
    headers = {"Cache-Control": "public, max-age=120"}
    return HTMLResponse(content=body, headers=headers)


# ──────────────────────────────────────────────────────────────────────────
#  iter141nineJ — Hotspot OG preview (Stories / WhatsApp / iMessage)
#  Returns rich meta tags so a shared `/api/og/connect/<id>` URL renders
#  a preview card on every social scraper. The 1.91:1 image is reused
#  from the share-card PNG endpoint (cropped to 1200×630-ish by clients).
# ──────────────────────────────────────────────────────────────────────────

@router.get("/connect/{hotspot_id}", response_class=HTMLResponse)
async def og_connect_preview(hotspot_id: str, request: Request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT alias, address, country_code, type FROM wifi_hotspots
               WHERE hotspot_id = $1""",
            hotspot_id,
        )

    frontend = _frontend_base(request)
    target = f"{frontend}/connect/h/{hotspot_id}" if frontend else f"/connect/h/{hotspot_id}"
    canonical = f"{frontend}/api/og/connect/{hotspot_id}" if frontend else f"/api/og/connect/{hotspot_id}"

    if not row:
        title_txt = "JAPAP — Hotspot introuvable"
        desc_txt = "Ce hotspot WiFi n'est plus disponible."
        image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""
    else:
        title_txt = f"📶 {row['alias']} — WiFi via JAPAP"
        desc_txt = (
            (row['address'].strip() + " · " if row['address'] else "")
            + "Scanne le QR et connecte-toi en 1 clic."
        )
        image_url = f"{frontend}/api/connect/hotspots/{hotspot_id}/share-card.png" if frontend else ""

    title_esc = html.escape(title_txt, quote=True)
    desc_esc = html.escape(desc_txt, quote=True)
    image_esc = html.escape(image_url, quote=True)
    url_esc = html.escape(canonical, quote=True)
    target_esc = html.escape(target, quote=True)

    body = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{title_esc}</title>
<meta name="description" content="{desc_esc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="JAPAP Connect">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{desc_esc}">
<meta property="og:image" content="{image_esc}">
<meta property="og:image:alt" content="{title_esc}">
<meta property="og:url" content="{url_esc}">
<meta property="og:locale" content="fr_FR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title_esc}">
<meta name="twitter:description" content="{desc_esc}">
<meta name="twitter:image" content="{image_esc}">
<link rel="canonical" href="{url_esc}">
<meta http-equiv="refresh" content="0;url={target_esc}">
<script>window.location.replace({target_esc!r});</script>
<style>body{{font-family:-apple-system,sans-serif;background:#0F056B;color:#fff;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:24px}}.t{{font-size:22px;font-weight:700;margin-bottom:12px}}a.cta{{display:inline-block;background:#E01C2E;color:#fff;padding:12px 22px;border-radius:999px;font-weight:700;text-decoration:none}}</style>
</head>
<body><noscript><div><div class="t">{title_esc}</div><p>{desc_esc}</p><a class="cta" href="{target_esc}">Ouvrir sur JAPAP</a></div></noscript></body>
</html>
"""
    return HTMLResponse(content=body, headers={"Cache-Control": "public, max-age=120"})


# ──────────────────────────────────────────────────────────────────────────
#  iter142B — Crowdfunding viral OG preview (Phase P1)
#  Rich preview card for every project share — converts viewers into voters.
# ──────────────────────────────────────────────────────────────────────────

@router.get("/crowdfunding/{slug}", response_class=HTMLResponse)
async def og_crowdfunding_preview(slug: str, request: Request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT p.title, p.description, p.image_url, p.country_code,
                      p.votes_count, p.cycle_id,
                      u.first_name, u.last_name, u.username, u.avatar
                 FROM crowdfunding_projects p
                 JOIN users u ON u.user_id = p.user_id
                WHERE p.slug = $1""",
            slug,
        )
        cycle_row = None
        if row:
            cycle_row = await conn.fetchrow(
                "SELECT cycle_number, votes_to_win FROM crowdfunding_cycles WHERE cycle_id = $1",
                row["cycle_id"],
            )

    frontend = _frontend_base(request)
    target = f"{frontend}/crowdfunding/p/{slug}" if frontend else f"/crowdfunding/p/{slug}"
    canonical = f"{frontend}/api/og/crowdfunding/{slug}" if frontend else f"/api/og/crowdfunding/{slug}"

    if not row:
        title_txt = "JAPAP — Projet introuvable"
        desc_txt = "Ce projet n'existe plus."
        image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""
    else:
        owner_name = (
            f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
            or row['username'] or "Anonyme"
        )
        votes_to_win = int(cycle_row["votes_to_win"] if cycle_row else 100)
        title_txt = f"❤️ Aide {owner_name} à gagner — JAPAP"
        desc_txt = (
            f"« {row['title']} » · {row['votes_count']}/{votes_to_win} votes · "
            f"Clique et vote en 1 sec."
        )
        # JPEG light is universally compatible with WhatsApp/Twitter/iOS
        # unfurlers AND ≤ 150 KB on 3G.
        image_url = f"{frontend}/api/crowdfunding/projects/{slug}/share-card?format=landscape&tier=light&fmt=jpeg" if frontend else ""

    title_esc = html.escape(title_txt, quote=True)
    desc_esc = html.escape(desc_txt, quote=True)
    image_esc = html.escape(image_url, quote=True)
    url_esc = html.escape(canonical, quote=True)
    target_esc = html.escape(target, quote=True)

    body = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{title_esc}</title>
<meta name="description" content="{desc_esc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="JAPAP Crowdfunding">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{desc_esc}">
<meta property="og:image" content="{image_esc}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{title_esc}">
<meta property="og:url" content="{url_esc}">
<meta property="og:locale" content="fr_FR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title_esc}">
<meta name="twitter:description" content="{desc_esc}">
<meta name="twitter:image" content="{image_esc}">
<link rel="canonical" href="{url_esc}">
<meta http-equiv="refresh" content="0;url={target_esc}">
<script>window.location.replace({target_esc!r});</script>
<style>body{{font-family:-apple-system,sans-serif;background:linear-gradient(135deg,#0F056B 0%,#E01C2E 100%);color:#fff;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:24px}}.t{{font-size:22px;font-weight:700;margin-bottom:12px}}a.cta{{display:inline-block;background:#FBBF24;color:#0F056B;padding:14px 28px;border-radius:999px;font-weight:700;text-decoration:none;margin-top:12px}}</style>
</head>
<body><noscript><div><div class="t">{title_esc}</div><p>{desc_esc}</p><a class="cta" href="{target_esc}">Voter maintenant</a></div></noscript></body>
</html>
"""
    return HTMLResponse(content=body, headers={"Cache-Control": "public, max-age=120"})



# ──────────────────────────────────────────────────────────────────────────
#  iter229 — Quiz Open Challenge OG preview (`/c/{cid}`)
#  Public OG-rich landing so WhatsApp / iMessage / Twitter unfurl shared
#  challenge links into a card with the challenger's avatar + stake.
#  Real users meta-refresh into /c/{cid} (the SPA route) ~50ms later.
# ──────────────────────────────────────────────────────────────────────────

@router.get("/challenge/{cid}", response_class=HTMLResponse)
async def og_challenge_preview(cid: str, request: Request):
    """Public OG preview for a Quiz Open Challenge. Score-free by design."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        ch = await conn.fetchrow(
            """SELECT challenge_id, challenger_user_id, mode, stake_amount,
                      stake_currency, status, expires_at
               FROM quiz_champion_challenges
               WHERE challenge_id = $1""",
            cid,
        )
        creator = None
        if ch:
            creator = await conn.fetchrow(
                "SELECT first_name, last_name, username, avatar FROM users WHERE user_id = $1",
                ch["challenger_user_id"],
            )

    frontend = _frontend_base(request)
    target_url = f"{frontend}/c/{cid}" if frontend else f"/c/{cid}"
    canonical = f"{frontend}/api/og/challenge/{cid}" if frontend else f"/api/og/challenge/{cid}"

    if not ch:
        title_txt = "JAPAP — Défi introuvable"
        desc_txt = "Ce défi Quiz n'est plus disponible."
        image_url = f"{frontend}/pwa-icon-512.png" if frontend else ""
    else:
        author = (
            (creator and (creator["first_name"] or creator["username"]).strip())
            or "Un joueur"
        )
        is_paid = ch["mode"] == "paid"
        stake = float(ch["stake_amount"] or 0)
        ccy = ch["stake_currency"] or "USD"
        title_txt = (
            f"⚔️ {author} te défie · {stake:.0f} {ccy}"
            if is_paid else f"⚔️ {author} te défie sur JAPAP Quiz"
        )
        if is_paid:
            pot = stake * 2
            desc_txt = (
                f"5 questions · Pot total {pot:.0f} {ccy} · "
                f"Bats son score et empoche les gains. Lien valable 24h."
            )
        else:
            desc_txt = (
                f"5 questions sur JAPAP Quiz · Mode gratuit · "
                f"Bats le score de {author} ! Lien valable 24h."
            )
        image_url = (
            (creator and creator["avatar"]) or
            (f"{frontend}/pwa-icon-512.png" if frontend else "")
        )

    title_esc  = html.escape(title_txt, quote=True)
    desc_esc   = html.escape(desc_txt,  quote=True)
    image_esc  = html.escape(image_url or "", quote=True)
    url_esc    = html.escape(canonical, quote=True)
    target_esc = html.escape(target_url, quote=True)

    body = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{title_esc}</title>
<meta name="description" content="{desc_esc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="JAPAP">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{desc_esc}">
<meta property="og:image" content="{image_esc}">
<meta property="og:image:alt" content="{title_esc}">
<meta property="og:image:width" content="512">
<meta property="og:image:height" content="512">
<meta property="og:url" content="{url_esc}">
<meta property="og:locale" content="fr_FR">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title_esc}">
<meta name="twitter:description" content="{desc_esc}">
<meta name="twitter:image" content="{image_esc}">
<link rel="canonical" href="{url_esc}">
<meta http-equiv="refresh" content="0;url={target_esc}">
<script>window.location.replace({target_esc!r});</script>
<style>body{{font-family:-apple-system,sans-serif;background:linear-gradient(135deg,#0F056B,#E01C2E);color:#fff;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:24px}}.t{{font-size:22px;font-weight:700;margin-bottom:12px}}a.cta{{display:inline-block;background:#FFD700;color:#0F056B;padding:14px 28px;border-radius:999px;font-weight:700;text-decoration:none;margin-top:12px}}</style>
</head>
<body><noscript><div><div class="t">{title_esc}</div><p>{desc_esc}</p><a class="cta" href="{target_esc}">Accepter le défi</a></div></noscript></body>
</html>
"""
    return HTMLResponse(content=body, headers={"Cache-Control": "public, max-age=120"})
