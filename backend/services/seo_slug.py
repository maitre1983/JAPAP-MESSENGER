"""
iter184 — SEO Phase A (slug helper + canonical URL builder)
============================================================
Tiny utility to slugify titles for SEO-friendly URLs. Idempotent and
ASCII-safe (no `unicode-slugify` dependency). Examples :

    >>> slugify("iPhone 13 — 128GB Étoile")
    'iphone-13-128gb-etoile'
    >>> slugify("¡Hola Mundo!  ")
    'hola-mundo'
"""
from __future__ import annotations

import re
import unicodedata


_SLUG_BAD_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 80) -> str:
    if not text:
        return ""
    # NFKD strip diacritics → ASCII
    norm = unicodedata.normalize("NFKD", text)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_BAD_CHARS.sub("-", ascii_only).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug


def public_app_url() -> str:
    """Return the configured public-facing URL (no trailing slash)."""
    import os
    url = (os.environ.get("PUBLIC_APP_URL")
           or os.environ.get("REACT_APP_BACKEND_URL")
           or "https://japapmessenger.com")
    return url.rstrip("/")


def product_canonical_url(product_id: str, title: str | None = None) -> str:
    base = public_app_url()
    s = slugify(title or "")
    return f"{base}/marketplace/p/{product_id}/{s}" if s else f"{base}/marketplace/p/{product_id}"


def user_canonical_url(username: str | None, user_id: str) -> str:
    base = public_app_url()
    if username and re.match(r"^[A-Za-z0-9_]{3,32}$", username):
        return f"{base}/u/{username}"
    return f"{base}/user/{user_id}"


def post_canonical_url(post_id: str, text: str | None = None) -> str:
    base = public_app_url()
    s = slugify((text or "")[:80])
    return f"{base}/post/{post_id}/{s}" if s else f"{base}/post/{post_id}"
