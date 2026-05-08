"""
iter212 — Open Graph link preview service.
==========================================

Given any http(s) URL, fetch the HTML head and extract `og:*` + `twitter:*`
+ `<title>` + favicon so the frontend can render a rich link card
(Facebook/LinkedIn style).

Cached 24h in PostgreSQL (`og_cache` table) to avoid re-scraping popular
domains. SSRF protected (blocks private IPs, localhost, non-http schemes).

Public API (all async):
    get_preview(url: str) -> dict
        {
          "url":         str,   # final URL after following redirects
          "title":       str,
          "description": str,
          "image":       str,   # absolute URL
          "site_name":   str,
          "favicon":     str,   # absolute URL
          "domain":      str,   # e.g. "nytimes.com"
          "fetched_at":  iso-8601 str,
          "cache_hit":   bool,
        }
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(hours=24)
MAX_BYTES = 512 * 1024  # 512 KB head is more than enough for meta tags
REQUEST_TIMEOUT = 8.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; JAPAP-LinkPreview/1.0; "
    "+https://japapmessenger.com)"
)


# ─────────────────────────────────────────────────────────────────────
# SSRF guard
# ─────────────────────────────────────────────────────────────────────
_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "metadata.google.internal",  # GCP metadata
    "169.254.169.254",           # AWS metadata
}


def _is_private_ip(host: str) -> bool:
    """Return True if host resolves to a private / loopback / link-local IP."""
    try:
        # Handle raw IPs first
        try:
            ip = ipaddress.ip_address(host)
            return (
                ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast
            )
        except ValueError:
            pass
        # DNS-resolve and check all returned addresses.
        infos = socket.getaddrinfo(host, None)
        for family, _type, _proto, _canon, sockaddr in infos:
            addr = sockaddr[0]
            ip = ipaddress.ip_address(addr)
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
                return True
        return False
    except Exception:
        # Fail closed — if we can't resolve, block it.
        return True


def _validate_url(url: str) -> str:
    """Validate + normalize the URL. Raises ValueError on bad input."""
    if not url or not isinstance(url, str):
        raise ValueError("url is required")
    url = url.strip()
    if len(url) > 2048:
        raise ValueError("url too long (max 2048 chars)")
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("only http/https URLs are allowed")
    if not p.hostname:
        raise ValueError("url has no host")
    host = p.hostname.lower()
    if host in _BLOCKED_HOSTS or host.endswith(".local"):
        raise ValueError("host is blocked")
    if _is_private_ip(host):
        raise ValueError("private / internal IPs are blocked")
    return url


# ─────────────────────────────────────────────────────────────────────
# Regex-based meta extractor (no BeautifulSoup dependency)
# ─────────────────────────────────────────────────────────────────────
# We match <meta ... name|property="..." content="..."> in either attribute
# order. Case-insensitive.
_META_RE = re.compile(
    r"<meta\b[^>]*?(?:(?:name|property)\s*=\s*['\"]([^'\"]+)['\"][^>]*?"
    r"content\s*=\s*['\"]([^'\"]*)['\"]|"
    r"content\s*=\s*['\"]([^'\"]*)['\"][^>]*?(?:name|property)"
    r"\s*=\s*['\"]([^'\"]+)['\"])[^>]*?>",
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_FAVICON_RE = re.compile(
    r"<link\b[^>]*?rel\s*=\s*['\"](?:shortcut\s+icon|icon|apple-touch-icon)['\"][^>]*?"
    r"href\s*=\s*['\"]([^'\"]+)['\"]|"
    r"<link\b[^>]*?href\s*=\s*['\"]([^'\"]+)['\"][^>]*?rel\s*=\s*"
    r"['\"](?:shortcut\s+icon|icon|apple-touch-icon)['\"]",
    re.IGNORECASE | re.DOTALL,
)
_CHARSET_RE = re.compile(
    r"<meta\b[^>]*?charset\s*=\s*['\"]?([\w-]+)", re.IGNORECASE,
)


def _parse_head(html: str, final_url: str) -> dict:
    """Extract OG/Twitter/title/favicon from HTML head."""
    # Trim to the first ~64 KB (most meta tags live above the fold). This
    # also protects against catastrophic regex backtracking on huge pages.
    head = html[:65536]
    meta: dict[str, str] = {}
    for m in _META_RE.finditer(head):
        name1, content1, content2, name2 = m.groups()
        name = (name1 or name2 or "").lower().strip()
        content = (content1 if content1 is not None else content2) or ""
        if name and name not in meta:
            meta[name] = unescape(content.strip())

    # <title>
    title_m = _TITLE_RE.search(head)
    title_plain = unescape(re.sub(r"\s+", " ", title_m.group(1)).strip()) if title_m else ""

    # Favicon (first match of either variant)
    favicon = ""
    fm = _FAVICON_RE.search(head)
    if fm:
        favicon = fm.group(1) or fm.group(2) or ""
    if favicon:
        favicon = urljoin(final_url, favicon)
    else:
        # Default /favicon.ico
        p = urlparse(final_url)
        favicon = f"{p.scheme}://{p.netloc}/favicon.ico"

    # Build result with fallbacks
    title = (
        meta.get("og:title") or meta.get("twitter:title") or title_plain or ""
    )
    description = (
        meta.get("og:description") or meta.get("twitter:description")
        or meta.get("description") or ""
    )
    image = (
        meta.get("og:image:secure_url") or meta.get("og:image")
        or meta.get("twitter:image:src") or meta.get("twitter:image") or ""
    )
    if image:
        image = urljoin(final_url, image)
    site_name = meta.get("og:site_name") or ""
    domain = urlparse(final_url).hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]

    return {
        "url":         final_url,
        "title":       title[:300],
        "description": description[:500],
        "image":       image,
        "site_name":   site_name[:100],
        "favicon":     favicon,
        "domain":      domain,
    }


# ─────────────────────────────────────────────────────────────────────
# DB cache
# ─────────────────────────────────────────────────────────────────────
async def _ensure_cache_table():
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS og_cache (
                url_hash    varchar(64) PRIMARY KEY,
                url         text NOT NULL,
                data        jsonb NOT NULL,
                status      varchar(16) DEFAULT 'ok',
                fetched_at  timestamptz DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_og_cache_fetched
                ON og_cache(fetched_at DESC);
        """)


def _url_hash(url: str) -> str:
    import hashlib
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


async def _read_cache(url: str) -> dict | None:
    from database import get_pool
    pool = await get_pool()
    h = _url_hash(url)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT data, status, fetched_at FROM og_cache WHERE url_hash=$1",
            h,
        )
    if not row:
        return None
    age = datetime.now(timezone.utc) - row["fetched_at"]
    if age > CACHE_TTL:
        return None
    data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
    data["cache_hit"] = True
    data["fetched_at"] = row["fetched_at"].isoformat()
    return data


async def _write_cache(url: str, data: dict, status: str = "ok"):
    from database import get_pool
    pool = await get_pool()
    h = _url_hash(url)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO og_cache (url_hash, url, data, status, fetched_at)
            VALUES ($1, $2, $3::jsonb, $4, NOW())
            ON CONFLICT (url_hash) DO UPDATE
                SET data = EXCLUDED.data,
                    status = EXCLUDED.status,
                    fetched_at = NOW()
        """, h, url[:2048], json.dumps(data, ensure_ascii=False), status)


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
async def get_preview(url: str, *, force_refresh: bool = False) -> dict:
    """Return OG preview for a URL, hitting DB cache first (24h TTL)."""
    url = _validate_url(url)
    await _ensure_cache_table()

    if not force_refresh:
        cached = await _read_cache(url)
        if cached:
            return cached

    headers = {
        "User-Agent":      USER_AGENT,
        "Accept":          "text/html,application/xhtml+xml",
        "Accept-Language": "en,fr;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            max_redirects=5,
            headers=headers,
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        logger.info(f"[og] fetch failed for {url}: {e}")
        fallback = _fallback(url)
        await _write_cache(url, fallback, status="error")
        fallback["cache_hit"] = False
        return fallback

    # SSRF re-check: follow_redirects might have landed on a private host.
    final_url = str(resp.url)
    try:
        _validate_url(final_url)
    except ValueError as e:
        logger.warning(f"[og] redirect target blocked for {url}: {e}")
        fallback = _fallback(url)
        await _write_cache(url, fallback, status="blocked")
        fallback["cache_hit"] = False
        return fallback

    ct = (resp.headers.get("content-type") or "").lower()
    if resp.status_code >= 400 or "text/html" not in ct:
        fallback = _fallback(final_url)
        await _write_cache(url, fallback, status=f"http_{resp.status_code}")
        fallback["cache_hit"] = False
        return fallback

    # Decode (limit bytes)
    body = resp.content[:MAX_BYTES]
    encoding = resp.encoding or "utf-8"
    try:
        html = body.decode(encoding, errors="replace")
    except (LookupError, ValueError):
        html = body.decode("utf-8", errors="replace")
    # Respect <meta charset="..."> if different.
    cm = _CHARSET_RE.search(html[:4096])
    if cm and cm.group(1).lower() not in encoding.lower():
        try:
            html = body.decode(cm.group(1), errors="replace")
        except LookupError:
            pass

    try:
        data = _parse_head(html, final_url)
    except Exception as e:
        logger.exception(f"[og] parse error for {url}: {e}")
        data = _fallback(final_url)

    data["fetched_at"] = datetime.now(timezone.utc).isoformat()
    await _write_cache(url, data, status="ok")
    data["cache_hit"] = False
    return data


def _fallback(url: str) -> dict:
    """Minimal preview when we can't fetch / parse the page."""
    p = urlparse(url)
    host = p.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return {
        "url":         url,
        "title":       host,
        "description": "",
        "image":       "",
        "site_name":   host,
        "favicon":     f"{p.scheme}://{p.netloc}/favicon.ico" if p.netloc else "",
        "domain":      host,
        "fetched_at":  datetime.now(timezone.utc).isoformat(),
    }


async def get_preview_many(urls: list[str], *, limit: int = 8) -> list[dict]:
    """Batch helper: fetch up to `limit` previews concurrently."""
    urls = urls[:limit]
    tasks = [get_preview(u) for u in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for url, r in zip(urls, results):
        if isinstance(r, Exception):
            logger.info(f"[og] batch error for {url}: {r}")
            fb = _fallback(url)
            fb["cache_hit"] = False
            fb["fetched_at"] = datetime.now(timezone.utc).isoformat()
            out.append(fb)
        else:
            out.append(r)
    return out
