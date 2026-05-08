"""iter212 — /api/og?url=... — Open Graph link preview endpoint.

Public, no auth (the scraped data is public web content anyway). Rate
limited by the global middleware. Returns a rich preview suitable for
rendering a link card in the Feed (Facebook/LinkedIn style).
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from services.og_preview_service import (
    get_preview, get_preview_many,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/og", tags=["og-preview"])


@router.get("")
async def og_preview(
    url: str = Query(..., min_length=8, max_length=2048, description="http(s) URL"),
    force: bool = Query(False, description="Bypass 24h cache"),
):
    """Return OG/Twitter/meta preview for a URL.

    Response:
        {
          "url":          "...",
          "title":        "...",
          "description":  "...",
          "image":        "https://...",
          "site_name":    "New York Times",
          "favicon":      "https://nytimes.com/favicon.ico",
          "domain":       "nytimes.com",
          "fetched_at":   "2026-05-03T22:00:00+00:00",
          "cache_hit":    true
        }
    """
    try:
        return await get_preview(url, force_refresh=force)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/batch")
async def og_preview_batch(
    urls: str = Query(..., description="Comma-separated URLs, max 8"),
):
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    if not url_list:
        raise HTTPException(status_code=400, detail="No URLs provided")
    try:
        return {"previews": await get_preview_many(url_list, limit=8)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
