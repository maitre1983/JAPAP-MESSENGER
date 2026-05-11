"""
iter239g — Batch regeneration of WebP/AVIF responsive variants for legacy
posts. Strictly additive: no destructive update — entries are only rewritten
when fresh variants succeed (idempotent on repeat runs).

Walks every post in `posts.media` and, for each image entry without
`small_url_avif/medium_url_avif/large_url_avif`, fetches the original
bytes (from local FS, then R2 fallback) and re-runs
`generate_srcset_variants` to produce the 6 size+format combinations.

Designed as a background asyncio.Task — the admin kicks it off, polls
progress via /api/admin/storage/regenerate-status. Errors per post are
captured but never abort the whole sweep.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from database import get_pool
from services.r2_storage_service import (
    R2_MEDIA_PUBLIC_URL,
    generate_srcset_variants,
)

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("/app/backend/uploads")
LEGACY_URL_RE = re.compile(r"^/api/upload/files/([\w\-.]+)$")

# Variant keys we expect on a fully-processed media entry. If ANY is
# missing, the entry is considered legacy and re-processed.
EXPECTED_VARIANT_KEYS = (
    "small_url", "medium_url", "large_url",
    "small_url_avif", "medium_url_avif", "large_url_avif",
)

# Background job state — single-process, in-memory. A redeploy resets it
# (acceptable: the job is idempotent and can be re-triggered).
_state: dict = {
    "running": False,
    "started_at": None,
    "ended_at": None,
    "started_by": None,
    "scanned_posts": 0,
    "total_posts": 0,
    "updated_posts": 0,
    "regenerated_entries": 0,
    "skipped_entries": 0,
    "failed_entries": 0,
    "errors": [],
    "current_post_id": None,
}


def get_state() -> dict:
    return dict(_state)


def _needs_regen(media_entry: Any) -> bool:
    """True if this entry is an image that lacks AVIF/WebP variants.
    Skips non-image entries (shared posts, videos, file links, etc.)."""
    if isinstance(media_entry, str):
        # Legacy `/api/upload/files/<name>` paths or bare URLs without variants.
        ext = Path(media_entry).suffix.lower()
        return ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
    if not isinstance(media_entry, dict):
        return False
    # Already an object — skip if not an image, or if all variant keys are present.
    if media_entry.get("type") and media_entry["type"] not in ("image", None):
        return False
    url = media_entry.get("url", "")
    if not isinstance(url, str) or not url:
        return False
    ext = Path(url.split("?")[0]).suffix.lower()
    if ext and ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"):
        return False
    return any(not media_entry.get(k) for k in EXPECTED_VARIANT_KEYS)


async def _fetch_image_bytes(url_or_path: str,
                              r2_suffix_index: dict[str, str] | None = None,
                              ) -> tuple[bytes, str] | None:
    """Fetch the source bytes + filename for a media entry. Tries:
      1. Local filesystem (`/app/backend/uploads/<name>`)
      2. R2 `get_object` via boto3 (bypasses the Cloudflare bot challenge
         that would block a plain httpx request from a server-side IP).
      3. Direct HTTP GET for non-R2 URLs (with a browser-like UA).
    Returns `(bytes, filename)` or None on failure."""
    # Case 1: legacy `/api/upload/files/<name>`
    m = LEGACY_URL_RE.match(url_or_path)
    r2_key: str | None = None
    if m:
        name = m.group(1)
        local = UPLOAD_DIR / name
        if local.is_file():
            try:
                return local.read_bytes(), name
            except Exception as e:  # noqa: BLE001
                logger.warning("[regen] local read failed for %s: %s", name, e)
        # Map original name → uuid-prefixed R2 key via the suffix index.
        if r2_suffix_index is not None:
            r2_key = r2_suffix_index.get(name)
        if not r2_key:
            r2_key = f"images/{name}"
    elif url_or_path.startswith(R2_MEDIA_PUBLIC_URL):
        # Strip the CDN host → bucket key.
        r2_key = url_or_path.replace(R2_MEDIA_PUBLIC_URL + "/", "", 1)
    # Try R2 directly (no proxy / no CF challenge).
    if r2_key:
        try:
            from services.r2_storage_service import _get_r2_media_client, R2_MEDIA_BUCKET
            obj = await asyncio.to_thread(
                lambda: _get_r2_media_client().get_object(
                    Bucket=R2_MEDIA_BUCKET, Key=r2_key))
            body = obj["Body"].read()
            if body:
                return body, Path(r2_key).name
        except Exception as e:  # noqa: BLE001
            logger.warning("[regen] R2 get_object failed for %s: %s", r2_key, e)

    # Case 3: not an R2 URL → plain HTTP fetch with a real browser UA so
    # any upstream WAF (Cloudflare bot challenge etc.) lets us through.
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; JAPAP-Regen/1.0)"},
            ) as client:
                r = await client.get(url_or_path)
            if r.status_code != 200 or not r.content:
                return None
            return r.content, Path(url_or_path.split("?")[0]).name
        except Exception as e:  # noqa: BLE001
            logger.warning("[regen] HTTP fetch failed for %s: %s", url_or_path, e)
    return None


def _build_r2_suffix_index() -> dict[str, str]:
    """Build `original-filename → r2-key` lookup from a single
    `list_objects_v2` sweep of the `images/` prefix. We index by the part
    after the LAST underscore so a migrated file `images/<uuid>_<name>` is
    findable by its `<name>`. Returns an empty dict on any failure (the
    caller will fall back to HTTP GET attempts that may still succeed)."""
    try:
        from services.r2_storage_service import _get_r2_media_client, R2_MEDIA_BUCKET
        client = _get_r2_media_client()
    except Exception as e:  # noqa: BLE001
        logger.warning("[regen] R2 index build failed (no client): %s", e)
        return {}
    index: dict[str, str] = {}
    token = None
    try:
        while True:
            kw = {"Bucket": R2_MEDIA_BUCKET, "Prefix": "images/", "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            resp = client.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                base = key.rsplit("/", 1)[-1]
                # Strip the `<uuid_hex>_` prefix added at upload time.
                if "_" in base:
                    bare = base.split("_", 1)[1]
                    # Keep the first occurrence — duplicates are unlikely
                    # because uploads are uuid-prefixed.
                    if bare not in index:
                        index[bare] = key
                # Also index the raw key for direct hits.
                index.setdefault(base, key)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
    except Exception as e:  # noqa: BLE001
        logger.warning("[regen] R2 index build failed (mid-sweep): %s", e)
    logger.info("[regen] R2 suffix index built: %d entries", len(index))
    return index


def _to_object(entry: Any, url: str, variants: dict) -> dict:
    """Promote a legacy string entry to a media-object that carries the
    new variants while preserving anything already on the object form."""
    if isinstance(entry, dict):
        return {**entry, **variants, "type": entry.get("type") or "image"}
    return {"url": url, "type": "image", **variants}


async def _regen_post(post_id: str, media: list,
                      r2_suffix_index: dict[str, str]
                      ) -> tuple[list, int, int, int]:
    """Process one post's media list. Returns
    `(new_media, regenerated, skipped, failed)`."""
    regenerated = 0
    skipped = 0
    failed = 0
    new_media: list = []
    for entry in media:
        if not _needs_regen(entry):
            new_media.append(entry)
            skipped += 1
            continue
        url = entry if isinstance(entry, str) else entry.get("url", "")
        if not url:
            new_media.append(entry)
            failed += 1
            continue
        fetched = await _fetch_image_bytes(url, r2_suffix_index)
        if not fetched:
            # Cannot regenerate (file gone) — keep the entry as-is so the
            # post still renders via legacy fallback.
            new_media.append(entry)
            failed += 1
            continue
        image_bytes, filename = fetched
        try:
            # `generate_srcset_variants` is sync (boto3) — offload to a
            # thread so we don't block the event loop on the upload.
            variants = await asyncio.to_thread(
                generate_srcset_variants, image_bytes, filename)
        except Exception as e:  # noqa: BLE001
            logger.warning("[regen] variant gen failed for %s: %s", url, e)
            new_media.append(entry)
            failed += 1
            continue
        if not variants:
            new_media.append(entry)
            failed += 1
            continue
        new_media.append(_to_object(entry, url, variants))
        regenerated += 1
    return new_media, regenerated, skipped, failed


async def regenerate_legacy_post_variants() -> dict:
    """Public entrypoint — single sweep over all posts. Caller is expected
    to spawn this with `asyncio.create_task` so the HTTP handler returns
    immediately."""
    _state["running"] = True
    from datetime import datetime, timezone
    _state["started_at"] = datetime.now(timezone.utc).isoformat()
    _state["ended_at"] = None
    _state["scanned_posts"] = 0
    _state["updated_posts"] = 0
    _state["regenerated_entries"] = 0
    _state["skipped_entries"] = 0
    _state["failed_entries"] = 0
    _state["errors"] = []
    pool = await get_pool()

    try:
        # Build a one-shot R2 keys index so we can resolve legacy
        # `<name>` references to their `<uuid>_<name>` R2 keys without
        # listing the bucket on every fetch.
        r2_suffix_index = await asyncio.to_thread(_build_r2_suffix_index)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT post_id, media FROM posts
                    WHERE media IS NOT NULL AND media != '[]'::jsonb
                    ORDER BY created_at DESC""",
            )
        _state["total_posts"] = len(rows)
        logger.info("[regen] starting sweep over %d posts (r2 index: %d entries)",
                    len(rows), len(r2_suffix_index))

        for row in rows:
            _state["current_post_id"] = row["post_id"]
            _state["scanned_posts"] += 1
            media = row["media"]
            if isinstance(media, str):
                try:
                    media = json.loads(media)
                except Exception:
                    continue
            if not isinstance(media, list):
                continue
            try:
                new_media, regen, skip, fail = await _regen_post(
                    row["post_id"], media, r2_suffix_index)
            except Exception as e:  # noqa: BLE001
                _state["errors"].append(f"{row['post_id']}: {e}")
                continue
            _state["regenerated_entries"] += regen
            _state["skipped_entries"] += skip
            _state["failed_entries"] += fail
            if regen > 0 and new_media != media:
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE posts SET media = $2 WHERE post_id = $1",
                            row["post_id"], json.dumps(new_media),
                        )
                    _state["updated_posts"] += 1
                except Exception as e:  # noqa: BLE001
                    _state["errors"].append(f"{row['post_id']} UPDATE: {e}")
            # Yield control periodically to keep the event loop healthy on
            # very large feeds; also prevents starving other background tasks.
            if _state["scanned_posts"] % 25 == 0:
                await asyncio.sleep(0)
        logger.info("[regen] finished — updated=%d regen=%d skipped=%d failed=%d",
                    _state["updated_posts"], _state["regenerated_entries"],
                    _state["skipped_entries"], _state["failed_entries"])
    finally:
        _state["running"] = False
        _state["current_post_id"] = None
        from datetime import datetime, timezone
        _state["ended_at"] = datetime.now(timezone.utc).isoformat()
    return get_state()


__all__ = ["regenerate_legacy_post_variants", "get_state"]
