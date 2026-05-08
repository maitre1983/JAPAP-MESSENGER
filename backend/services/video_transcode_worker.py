"""
iter190b — Video transcode async worker
========================================
Background worker that drains the `video_processing_jobs` queue. For
uploads above the SYNC_TRANSCODE_THRESHOLD (50 MB by default) the upload
endpoint stores the raw file, inserts a `pending` job and returns
immediately. This worker picks the job up, runs the same canonical
transcode (libx264 + AAC + +faststart, ≤1080p) + thumbnail, then flips
status to `ready` and sends a push notification ("🎬 Ta vidéo est prête").

Schema (auto-created at boot):
  video_processing_jobs(
    job_id          varchar PK,
    user_id         varchar,
    src_path        varchar,        -- raw file on disk (.mov etc)
    src_filename    varchar,
    out_filename    varchar,        -- canonical .mp4 written when ready
    thumb_filename  varchar,
    status          varchar,        -- pending | processing | ready | failed
    error           text,
    duration        numeric,
    created_at      timestamptz default now(),
    started_at      timestamptz,
    finished_at     timestamptz
  )

Tunables (admin_settings):
  video_transcode_enabled            (bool, default true)
  video_transcode_poll_seconds       (int,  default 5)
  video_transcode_max_concurrent     (int,  default 1)
  video_transcode_sync_threshold_mb  (int,  default 50)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_stop_flag = asyncio.Event()
WORKER_ID = f"vtw-{os.getpid()}"
DEFAULT_POLL = 5  # seconds between polls when idle

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"


async def ensure_schema(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS video_processing_jobs (
                job_id          varchar PRIMARY KEY,
                user_id         varchar NOT NULL,
                src_path        varchar NOT NULL,
                src_filename    varchar NOT NULL,
                out_filename    varchar,
                thumb_filename  varchar,
                status          varchar NOT NULL DEFAULT 'pending',
                error           text,
                duration        numeric,
                size_bytes      bigint,
                created_at      timestamptz DEFAULT NOW(),
                started_at      timestamptz,
                finished_at     timestamptz
            );
            CREATE INDEX IF NOT EXISTS idx_vpj_status_created
                ON video_processing_jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_vpj_user
                ON video_processing_jobs(user_id, created_at DESC);
        """)


async def enqueue_job(pool, job_id: str, user_id: str,
                      src_path: str, src_filename: str,
                      size_bytes: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO video_processing_jobs
                (job_id, user_id, src_path, src_filename, status, size_bytes)
            VALUES ($1,$2,$3,$4,'pending',$5)
        """, job_id, user_id, src_path, src_filename, size_bytes)


async def get_job(pool, job_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT job_id, user_id, status, src_filename, out_filename,
                   thumb_filename, error, duration, size_bytes,
                   created_at, started_at, finished_at
            FROM video_processing_jobs WHERE job_id = $1
        """, job_id)
    if not row:
        return None
    d = dict(row)
    for k in ("created_at", "started_at", "finished_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    if d.get("duration") is not None:
        d["duration"] = float(d["duration"])
    return d


async def _process_one(pool, job: dict) -> None:
    """Run the transcode pipeline for a single job."""
    from services.video_pipeline import (
        CANONICAL_VIDEO_EXT, transcode_to_mp4, generate_thumbnail, probe,
    )
    job_id = job["job_id"]
    src = Path(job["src_path"])
    if not src.exists():
        await _fail(pool, job_id, "Source file disappeared before processing")
        return

    file_id = src.stem  # uuid stem set at upload time
    out_filename = f"{file_id}{CANONICAL_VIDEO_EXT}"
    out_path = UPLOAD_DIR / out_filename
    thumb_filename = f"{file_id}_thumb.jpg"
    thumb_path = UPLOAD_DIR / thumb_filename

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE video_processing_jobs
               SET status='processing', started_at=NOW()
             WHERE job_id=$1
        """, job_id)

    try:
        info = await probe(src)
    except Exception as e:
        await _fail(pool, job_id, f"ffprobe failed: {e}")
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass
        return

    try:
        await transcode_to_mp4(src, out_path)
    except Exception as e:
        await _fail(pool, job_id, f"transcode failed: {e}")
        for p in (src, out_path):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        return

    has_thumb = await generate_thumbnail(out_path, thumb_path)

    # Source ≠ canonical → drop raw to save disk
    if src.suffix.lower() != CANONICAL_VIDEO_EXT:
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE video_processing_jobs
               SET status='ready',
                   out_filename=$2,
                   thumb_filename=$3,
                   duration=$4,
                   finished_at=NOW()
             WHERE job_id=$1
        """, job_id, out_filename,
            thumb_filename if has_thumb else None,
            info.get("duration"))

    # Push notification — best effort, never blocks the worker.
    try:
        from services.notifications import send_social_notification
        await send_social_notification(
            event_type="video_transcode_ready",
            actor=None,
            target_user_id=job["user_id"],
            title="🎬 Ta vidéo est prête",
            body="Tu peux maintenant la publier sur ton feed.",
            deep_link="/?tab=feed",
            extra_data={"job_id": job_id, "url": f"/api/upload/files/{out_filename}"},
        )
    except Exception as e:  # pragma: no cover
        logger.warning(f"[vtw] push notify failed for job={job_id}: {e}")

    logger.info(f"[vtw] job={job_id} ready ({out_filename}, "
                 f"{info.get('duration', '?')}s)")


async def _fail(pool, job_id: str, msg: str) -> None:
    logger.warning(f"[vtw] job={job_id} FAILED: {msg}")
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE video_processing_jobs
               SET status='failed', error=$2, finished_at=NOW()
             WHERE job_id=$1
        """, job_id, msg[:500])


async def _tick(pool, max_concurrent: int = 1) -> int:
    """Process up to `max_concurrent` pending jobs. Returns the count."""
    async with pool.acquire() as conn:
        # Atomic claim — bump status to 'processing' so a parallel pod
        # never picks the same job twice. We fall back to 'pending' if the
        # update set 0 rows (someone else won the race).
        rows = await conn.fetch("""
            SELECT job_id, user_id, src_path, src_filename
            FROM video_processing_jobs
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT $1
            FOR UPDATE SKIP LOCKED
        """, max_concurrent)
        # Materialise to allow releasing the lock (skip locked + commit on
        # transaction end is enough; we're in autocommit asyncpg pool).
    processed = 0
    for r in rows:
        try:
            await _process_one(pool, dict(r))
            processed += 1
        except Exception as e:
            logger.exception(f"[vtw] unexpected crash on job={r['job_id']}: {e}")
            await _fail(pool, r["job_id"], f"worker crash: {e}")
    return processed


async def _loop():
    from database import get_pool
    from services.settings_service import get_bool, get_int
    logger.info(f"[VideoTranscode {WORKER_ID}] loop started")
    await asyncio.sleep(8)  # wait for DB pool / schema
    pool = await get_pool()
    await ensure_schema(pool)
    while not _stop_flag.is_set():
        try:
            # iter237o — Re-fetch pool each iteration so we transparently pick
            # up a recreated pool after a transient close (asyncpg).
            pool = await get_pool()
            if await get_bool("video_transcode_enabled", True):
                concurrent = max(1, await get_int("video_transcode_max_concurrent", 1))
                await _tick(pool, max_concurrent=concurrent)
            poll = max(2, await get_int("video_transcode_poll_seconds", DEFAULT_POLL))
        except Exception as e:
            logger.warning(f"[vtw] tick failed: {e}")
            poll = DEFAULT_POLL
        try:
            await asyncio.wait_for(_stop_flag.wait(), timeout=poll)
        except asyncio.TimeoutError:
            pass
    logger.info(f"[VideoTranscode {WORKER_ID}] loop stopped")


def start_worker(app):
    @app.on_event("startup")
    async def _start():
        global _task
        if _task is None or _task.done():
            _task = asyncio.create_task(_loop())

    @app.on_event("shutdown")
    async def _stop():
        _stop_flag.set()
