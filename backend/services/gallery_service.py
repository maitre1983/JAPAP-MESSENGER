"""
iter150 — Profile photo gallery + media filter metadata.

Adds the lightweight columns needed to:
  • surface the chosen filter preset on each photo (badge in gallery)
  • aggregate a user's filtered photos across `posts` + `stories` for the
    new `/api/users/{user_id}/photo-gallery` endpoint

Migrations are idempotent (`ADD COLUMN IF NOT EXISTS`) so they're safe to
re-run on every backend boot.
"""
from __future__ import annotations

import logging
from database import get_pool

logger = logging.getLogger(__name__)


_DDL = [
    "ALTER TABLE posts   ADD COLUMN IF NOT EXISTS filter_preset VARCHAR(32)",
    "ALTER TABLE stories ADD COLUMN IF NOT EXISTS filter_preset VARCHAR(32)",
    "CREATE INDEX IF NOT EXISTS idx_posts_filter_preset   ON posts(filter_preset)   WHERE filter_preset IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_stories_filter_preset ON stories(filter_preset) WHERE filter_preset IS NOT NULL",
]


async def ensure_gallery_columns() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for stmt in _DDL:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"gallery DDL failed: {stmt!r} → {e}")
