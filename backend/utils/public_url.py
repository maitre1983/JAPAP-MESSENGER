"""
JAPAP — Public URL helper (iter168)
====================================
Single source of truth for the public-facing JAPAP app URL used in:
  • transactional/digest emails (Resend)
  • share cards (PNG watermarks)
  • OG / deep links

Resolution order (most → least specific):
  1. `PUBLIC_APP_URL` env var (production override — e.g. https://japapmessenger.com)
  2. `FRONTEND_URL` env var (preview/staging)
  3. Request `Origin` / `Referer` header (correct when called from a known browser)
  4. Request scheme + netloc (k8s ingress fallback)
  5. Hard default https://japapmessenger.com

WHY a centralised helper?
The CEO escalated a P0 in iter168: emails contained
`https://japap.app/...` which damaged user trust. The bug came from a
hardcoded fallback in `quiz_champion.py` that bypassed the env. By
funnelling every URL build through this helper we guarantee no email,
share card or push payload leaks an outdated domain ever again.
"""
import os
from typing import Optional
from urllib.parse import urlparse


_HARD_FALLBACK = "https://japapmessenger.com"


def public_base_url(request=None) -> str:
    """Resolve the canonical public app URL. Always returns a value
    (the hard fallback if nothing else is set). The result NEVER ends
    with a trailing slash so callers can do `f"{base}/{path}"` safely.
    """
    for env_name in ("PUBLIC_APP_URL", "FRONTEND_URL"):
        v = (os.environ.get(env_name) or "").strip()
        if v:
            return v.rstrip("/")
    if request is not None:
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        if origin:
            try:
                p = urlparse(origin)
                if p.scheme and p.netloc:
                    return f"{p.scheme}://{p.netloc}"
            except Exception:
                pass
        try:
            if request.url.netloc:
                return f"{request.url.scheme}://{request.url.netloc}"
        except Exception:
            pass
    return _HARD_FALLBACK


def public_url(path: str = "", request=None) -> str:
    """Convenience: build a full URL by joining the base with `path`.
    `path` may or may not start with '/'."""
    base = public_base_url(request)
    if not path:
        return base
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def short_domain() -> str:
    """Display-only host, used in share-card watermarks and email
    footers (e.g. 'japapmessenger.com'). No protocol, no path."""
    base = public_base_url(None)
    try:
        return urlparse(base).netloc or _HARD_FALLBACK.replace("https://", "")
    except Exception:
        return _HARD_FALLBACK.replace("https://", "")


def is_legacy_domain(url: str) -> bool:
    """Sentinel — used by tests and audit jobs to flag URLs that still
    point at the deprecated `japap.app` host."""
    if not url:
        return False
    try:
        return (urlparse(url).netloc or "").lower() in ("japap.app", "www.japap.app")
    except Exception:
        return False
