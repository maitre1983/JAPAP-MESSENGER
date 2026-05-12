"""
Upload route — hardened iter82 (security audit).
iter92 — Added smart image pipeline (/image endpoint):
  • Auto-resize to target dimensions (profile 512×512, cover 1280×480)
  • Thumbnail generation (profile 128×128, cover 640×240)
  • WebP primary with JPEG fallback
  • Aggressive compression targeting ≤100 KB profile / ≤200 KB cover
  • Full EXIF strip & color-mode normalization
iter190 — Tolerant video upload (P0 / CEO mandate):
  • Accept ANY common container (.mov / .mp4 / .avi / .mkv / .webm /
    .3gp / .m4v / .hevc / .h265 / .mpg / .ts / .flv / .wmv).
  • Server-side ffprobe validation (replaces magic-byte sniff for video).
  • Auto-transcode to canonical .mp4 (H.264 + AAC + +faststart, ≤1080p).
  • Auto-thumbnail JPEG sidecar at t=1s.
"""
import io
import uuid
import os
import logging
import imghdr
import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from routes.auth import get_current_user
from services.video_pipeline import (
    SUPPORTED_VIDEO_EXTS, CANONICAL_VIDEO_EXT, CANONICAL_VIDEO_CT,
    is_video_ext, has_ffmpeg,
    probe as video_probe, transcode_to_mp4, generate_thumbnail,
)
from services.video_transcode_worker import enqueue_job, get_job as get_video_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/upload", tags=["upload"])

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_IMAGE_SIZE = 10 * 1024 * 1024              # 10 MB for images / docs
MAX_VIDEO_SIZE = 200 * 1024 * 1024             # 200 MB for videos (raw, pre-transcode)
ASYNC_TRANSCODE_THRESHOLD = 50 * 1024 * 1024   # >50 MB → background worker (iter190b)
MAX_FILE_SIZE = MAX_IMAGE_SIZE                 # back-compat alias

# Canonical (extension → magic-byte prefixes) map. Anything that doesn't
# match one of these is rejected, even if the extension is in the allow-list.
_EXT_MAGIC = {
    ".jpg":  [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png":  [b"\x89PNG\r\n\x1a\n"],
    ".gif":  [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],  # followed by "WEBP" at offset 8
    ".mp4":  [b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x20ftyp"],
    ".webm": [b"\x1a\x45\xdf\xa3"],
    ".mp3":  [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
    ".wav":  [b"RIFF"],
    ".ogg":  [b"OggS"],
    ".pdf":  [b"%PDF-"],
}
# Image / audio / doc extensions go through the magic-byte sniff. Video
# extensions are validated by ffprobe (services.video_pipeline) instead —
# their containers (ftyp boxes for ISO BMFF, RIFF for AVI, EBML for MKV…)
# carry too many byte-offset variants for a hand-rolled sniff.
_SAFE_EXTS = set(_EXT_MAGIC.keys()) | SUPPORTED_VIDEO_EXTS
_ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    # Tolerant video allow-list — every container we transcode in
    # services.video_pipeline. Browsers occasionally send weird Strings
    # like "video/x-quicktime" so we keep the list explicit but wide.
    "video/mp4", "video/quicktime", "video/x-msvideo",
    "video/x-matroska", "video/webm", "video/3gpp", "video/3gpp2",
    "video/x-m4v", "video/m4v", "video/mpeg", "video/mp2t",
    "video/x-flv", "video/x-ms-wmv", "video/hevc", "video/H265",
    "audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg",
    "application/pdf", "application/octet-stream",  # some clients send this
}


def _sniff_is_safe(ext: str, content: bytes) -> bool:
    """True iff the actual bytes look like the claimed extension."""
    if not content:
        return False
    prefixes = _EXT_MAGIC.get(ext, [])
    if any(content.startswith(p) for p in prefixes):
        # Webp/wav additionally carry a format code at offset 8
        if ext == ".webp":
            return content[8:12] == b"WEBP"
        if ext == ".wav":
            return content[8:12] == b"WAVE"
        return True
    # Images: last-chance sniff via stdlib imghdr (covers edge-case JPEG variants)
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return imghdr.what(None, h=content) in {"jpeg", "png", "gif", "webp"}
    return False


def _strip_image_exif(ext: str, content: bytes) -> bytes:
    """Return a re-encoded version of the image that strips EXIF / malicious
    side-chunks. Falls back to original content if Pillow isn't available
    or the re-encode fails."""
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        return content
    try:
        from PIL import Image  # lazy import
    except Exception:
        return content
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
        out = io.BytesIO()
        fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}[ext]
        # Preserve mode for PNG transparency
        if fmt == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(out, format=fmt, quality=85, optimize=True)
        return out.getvalue()
    except Exception as e:
        logger.warning(f"EXIF strip failed for {ext}: {e}")
        return content


@router.post("/")
async def upload_file(request: Request, file: UploadFile = File(...)):
    user = await get_current_user(request)

    # 1. Extension allow-list
    original = file.filename or ""
    ext = Path(original).suffix.lower()
    if ext not in _SAFE_EXTS:
        raise HTTPException(status_code=400, detail=f"File type not allowed: {ext or '(none)'}")

    is_vid = is_video_ext(ext)
    max_size = MAX_VIDEO_SIZE if is_vid else MAX_IMAGE_SIZE

    # 2. Server-seen content-type allow-list (client value is untrusted but
    #    provides a cheap sanity check). Videos get a wide allow-list because
    #    iOS/Android send half a dozen variants for the same container.
    client_ct = (file.content_type or "").lower()
    if client_ct and not is_vid and client_ct not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Content-type not allowed: {client_ct}")
    # For videos, accept anything that starts with "video/" — ffprobe
    # downstream is the source of truth, not the browser-supplied string.
    if client_ct and is_vid and not (
        client_ct in _ALLOWED_CONTENT_TYPES or client_ct.startswith("video/")
    ):
        raise HTTPException(status_code=400, detail=f"Content-type not allowed: {client_ct}")

    # 3. Size check
    content = await file.read()
    if len(content) > max_size:
        mb = max_size // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"File too large (max {mb}MB)")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # 4. Magic-byte sniff for non-video. Videos get validated by ffprobe
    #    AFTER we write the raw bytes to disk (the sniff is too brittle for
    #    .mov / .avi / .mkv variants).
    if not is_vid and not _sniff_is_safe(ext, content):
        logger.warning(f"Rejected upload: magic mismatch ext={ext} ct={client_ct} user={user.get('user_id')}")
        raise HTTPException(status_code=400, detail="File content does not match extension")

    # 5. EXIF strip for images
    content = _strip_image_exif(ext, content)

    # 6. Random server-side filename (never trust user's)
    file_id = uuid.uuid4().hex[:16]
    filename = f"{file_id}{ext}"
    filepath = UPLOAD_DIR / filename

    # Defence-in-depth — resolve & confirm we stay inside UPLOAD_DIR
    try:
        if filepath.resolve().parent != UPLOAD_DIR.resolve():
            raise HTTPException(status_code=400, detail="Invalid upload path")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid upload path")

    with open(filepath, "wb") as f:
        f.write(content)
    try:
        os.chmod(filepath, 0o644)
    except Exception:
        pass

    # ── 7. iter190 — Video transcode pipeline ─────────────────────────
    # Validate via ffprobe; if the file isn't a real parseable video we
    # delete it and reject. Otherwise transcode to canonical .mp4 with
    # +faststart so it streams instantly across all browsers, including
    # iOS Safari. iter190b — files above ASYNC_TRANSCODE_THRESHOLD
    # (default 50 MB) are queued for the background worker so the HTTP
    # request returns instantly with status='processing'.
    thumbnail_url = None
    duration = None
    job_id_async = None
    if is_vid:
        if not has_ffmpeg():
            logger.error("[upload] ffmpeg/ffprobe not installed — cannot accept videos")
            try:
                filepath.unlink(missing_ok=True)
            except Exception:
                pass
            raise HTTPException(
                status_code=503,
                detail="Video processing temporarily unavailable",
            )
        try:
            info = await video_probe(filepath)
        except Exception as e:
            logger.warning(f"[upload] ffprobe rejected {filename}: {e}")
            try:
                filepath.unlink(missing_ok=True)
            except Exception:
                pass
            raise HTTPException(
                status_code=400,
                detail="The file is not a readable video.",
            )
        duration = info.get("duration")

        # iter190b — Async path for large videos. The worker
        # (services.video_transcode_worker) picks up the pending row and
        # transcodes off the request thread. The frontend polls
        # /api/upload/video-job/{job_id} until status='ready'.
        size_bytes = filepath.stat().st_size
        if size_bytes >= ASYNC_TRANSCODE_THRESHOLD:
            try:
                from database import get_pool
                pool = await get_pool()
                job_id_async = file_id
                await enqueue_job(
                    pool, job_id=job_id_async,
                    user_id=user["user_id"],
                    src_path=str(filepath),
                    src_filename=filename,
                    size_bytes=size_bytes,
                )
            except Exception as e:
                logger.error(f"[upload] enqueue async transcode failed: {e}")
                try:
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=500,
                    detail="Could not queue the video for processing.",
                )
            return {
                "file_id": file_id,
                "job_id": job_id_async,
                "status": "processing",
                "filename": None,
                "original_name": original,
                "url": None,                # filled in once status='ready'
                "size": size_bytes,
                "content_type": CANONICAL_VIDEO_CT,
                "type": "video",
                "thumbnail_url": None,
                "duration": duration,
                "poll_url": f"/api/upload/video-job/{job_id_async}",
            }

        # Synchronous path (≤ 50 MB) — same canonical transcode + thumb.
        # iter218 fix: when the upload is already a .mp4 the source path
        # and target path collided (ffmpeg refuses "Output same as
        # Input"). We now transcode to a sibling `.transcoded.mp4` and
        # atomically rename on success — works for ALL container types.
        out_filename = f"{file_id}{CANONICAL_VIDEO_EXT}"
        out_path = UPLOAD_DIR / out_filename
        if filepath == out_path:
            tmp_out = UPLOAD_DIR / f"{file_id}.transcoded{CANONICAL_VIDEO_EXT}"
        else:
            tmp_out = out_path
        try:
            await transcode_to_mp4(filepath, tmp_out)
        except Exception as e:
            logger.error(f"[upload] transcode failed for {filename}: {e}")
            try:
                filepath.unlink(missing_ok=True)
                tmp_out.unlink(missing_ok=True)
            except Exception:
                pass
            raise HTTPException(
                status_code=500,
                detail="Video processing failed. Try a different file.",
            )

        if tmp_out != out_path:
            try:
                filepath.unlink(missing_ok=True)
                tmp_out.replace(out_path)
            except Exception as e:
                logger.error(f"[upload] atomic swap failed: {e}")
                raise HTTPException(
                    status_code=500,
                    detail="Video processing failed. Try a different file.",
                )

        # Thumbnail (best-effort — never blocks publish)
        thumb_filename = f"{file_id}_thumb.jpg"
        thumb_path = UPLOAD_DIR / thumb_filename
        if await generate_thumbnail(out_path, thumb_path):
            thumbnail_url = f"/api/upload/files/{thumb_filename}"

        # Original was non-mp4: delete raw to save disk and switch the
        # response to the canonical .mp4 file.
        if ext != CANONICAL_VIDEO_EXT:
            try:
                filepath.unlink(missing_ok=True)
            except Exception:
                pass
            filename, filepath, ext = out_filename, out_path, CANONICAL_VIDEO_EXT
        # ext == .mp4 case: filename/path already correct after the swap above.

    file_url = f"/api/upload/files/{filename}"
    # iter239d — Try Cloudflare R2 upload (persistent). Local file remains as
    # a fallback (ephemeral). Frontend URL is replaced with the R2 public URL
    # so subsequent requests never hit our pod for static media.
    r2_url = None
    # iter239i — Compress video thumbnail JPG → WebP before R2 upload (~60% smaller).
    # Best-effort: if WebP compression fails, fall back to the original JPG.
    if thumbnail_url and (UPLOAD_DIR / f"{file_id}_thumb.jpg").exists():
        jpg_path = UPLOAD_DIR / f"{file_id}_thumb.jpg"
        webp_path = UPLOAD_DIR / f"{file_id}_thumb.webp"
        webp_ok = False
        try:
            from services.r2_storage_service import compress_to_webp
            jpg_bytes = jpg_path.read_bytes()
            webp_bytes = compress_to_webp(jpg_bytes, max_size=1080, quality=85)
            # `compress_to_webp` returns original bytes on failure; only treat
            # it as a real WebP if the buffer differs from the source JPG.
            if webp_bytes and webp_bytes != jpg_bytes:
                webp_path.write_bytes(webp_bytes)
                webp_ok = True
        except Exception as _e:  # noqa: BLE001
            logger.warning("[thumb-webp] compression failed: %s", _e)
        try:
            from services.r2_storage_service import upload_media_file_to_r2
            if webp_ok:
                r2_thumb_url = upload_media_file_to_r2(
                    webp_path, folder="thumbnails",
                    content_type="image/webp",
                )
            else:
                r2_thumb_url = upload_media_file_to_r2(
                    jpg_path, folder="thumbnails",
                    content_type="image/jpeg",
                )
            thumbnail_url = r2_thumb_url
        except Exception as _e:  # noqa: BLE001
            logger.warning("[r2-upload] thumbnail upload failed: %s", _e)
    try:
        from services.r2_storage_service import upload_media_file_to_r2
        from services.r2_storage_service import _folder_for_extension
        r2_folder = "videos" if is_vid else _folder_for_extension(ext)
        r2_url = upload_media_file_to_r2(filepath, folder=r2_folder)
        file_url = r2_url
        logger.info("[r2-upload] %s -> %s", filename, r2_url)
    except Exception as _e:  # noqa: BLE001
        logger.warning("[r2-upload] failed for %s, keeping local fallback: %s",
                       filename, _e)
    # iter239e — Generate responsive WebP variants for images (small / medium /
    # large) so the frontend can pick the right size per device with srcset.
    # Best-effort: failures keep the original URL as fallback. No-op for videos.
    variants: dict[str, str] = {}
    if not is_vid:
        try:
            from services.r2_storage_service import generate_srcset_variants
            variants = generate_srcset_variants(content, filename)
        except Exception as _e:  # noqa: BLE001
            logger.warning("[srcset] variants generation failed for %s: %s",
                           filename, _e)
    logger.info(f"File uploaded by {user['user_id']}: {filename} "
                 f"({filepath.stat().st_size if filepath.exists() else 0} bytes)"
                 f"{' [video transcoded]' if is_vid else ''}"
                 f"{' [r2]' if r2_url else ' [local-only]'}"
                 f"{' [srcset:%d]' % len(variants) if variants else ''}")

    return {
        "file_id": file_id,
        "filename": filename,
        "original_name": original,
        "url": file_url,
        "size": filepath.stat().st_size if filepath.exists() else len(content),
        "content_type": CANONICAL_VIDEO_CT if is_vid else file.content_type,
        "type": "video" if is_vid else None,
        "thumbnail_url": thumbnail_url,
        "duration": duration,
        # iter239e — responsive variants (only keys that succeeded).
        # Frontend uses these to build `<img srcset>`; falls back to `url` if
        # any variant is missing.
        **variants,
    }


@router.post("/multiple")
async def upload_multiple(request: Request, files: list[UploadFile] = File(...)):
    user = await get_current_user(request)
    results = []
    for file in files[:5]:  # Max 5 files
        ext = Path(file.filename or "").suffix.lower()
        if ext not in _SAFE_EXTS:
            continue
        is_vid = is_video_ext(ext)
        max_size = MAX_VIDEO_SIZE if is_vid else MAX_IMAGE_SIZE

        client_ct = (file.content_type or "").lower()
        if client_ct and not is_vid and client_ct not in _ALLOWED_CONTENT_TYPES:
            continue
        if client_ct and is_vid and not (
            client_ct in _ALLOWED_CONTENT_TYPES or client_ct.startswith("video/")
        ):
            continue

        content = await file.read()
        if not content or len(content) > max_size:
            continue
        # Magic-byte sniff for non-video; video is validated by ffprobe below.
        if not is_vid and not _sniff_is_safe(ext, content):
            logger.warning(f"Rejected (batch) upload: magic mismatch ext={ext} user={user.get('user_id')}")
            continue
        content = _strip_image_exif(ext, content)
        file_id = uuid.uuid4().hex[:16]
        filename = f"{file_id}{ext}"
        filepath = UPLOAD_DIR / filename
        try:
            if filepath.resolve().parent != UPLOAD_DIR.resolve():
                continue
        except Exception:
            continue
        with open(filepath, "wb") as f:
            f.write(content)

        thumbnail_url = None
        duration = None
        if is_vid:
            if not has_ffmpeg():
                try:
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            try:
                info = await video_probe(filepath)
                duration = info.get("duration")
            except Exception as e:
                logger.warning(f"[upload-multi] ffprobe rejected {filename}: {e}")
                try:
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            out_filename = f"{file_id}{CANONICAL_VIDEO_EXT}"
            out_path = UPLOAD_DIR / out_filename
            try:
                await transcode_to_mp4(filepath, out_path)
            except Exception as e:
                logger.error(f"[upload-multi] transcode failed for {filename}: {e}")
                try:
                    filepath.unlink(missing_ok=True)
                    out_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            thumb_filename = f"{file_id}_thumb.jpg"
            thumb_path = UPLOAD_DIR / thumb_filename
            if await generate_thumbnail(out_path, thumb_path):
                thumbnail_url = f"/api/upload/files/{thumb_filename}"
            if ext != CANONICAL_VIDEO_EXT:
                try:
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass
                filename, filepath = out_filename, out_path

        # iter239d — Push to R2 after the file is finalized.
        r2_url = None
        try:
            from services.r2_storage_service import (
                upload_media_file_to_r2, _folder_for_extension)
            r2_folder = "videos" if is_vid else _folder_for_extension(filepath.suffix)
            r2_url = upload_media_file_to_r2(filepath, folder=r2_folder)
            if thumbnail_url and (UPLOAD_DIR / f"{file_id}_thumb.jpg").exists():
                # iter239i — WebP-compress the thumbnail before R2 upload.
                jpg_path = UPLOAD_DIR / f"{file_id}_thumb.jpg"
                webp_path = UPLOAD_DIR / f"{file_id}_thumb.webp"
                webp_ok = False
                try:
                    from services.r2_storage_service import compress_to_webp
                    jpg_bytes = jpg_path.read_bytes()
                    webp_bytes = compress_to_webp(jpg_bytes, max_size=1080, quality=85)
                    if webp_bytes and webp_bytes != jpg_bytes:
                        webp_path.write_bytes(webp_bytes)
                        webp_ok = True
                except Exception as _e:  # noqa: BLE001
                    logger.warning("[thumb-webp-multi] compression failed: %s", _e)
                try:
                    if webp_ok:
                        thumbnail_url = upload_media_file_to_r2(
                            webp_path, folder="thumbnails",
                            content_type="image/webp")
                    else:
                        thumbnail_url = upload_media_file_to_r2(
                            jpg_path, folder="thumbnails",
                            content_type="image/jpeg")
                except Exception as _e:  # noqa: BLE001
                    logger.warning("[r2-multi] thumb upload failed: %s", _e)
        except Exception as _e:  # noqa: BLE001
            logger.warning("[r2-multi] upload failed for %s, local fallback: %s",
                           filename, _e)
        # iter239e — srcset variants for non-video uploads.
        variants: dict[str, str] = {}
        if not is_vid:
            try:
                from services.r2_storage_service import generate_srcset_variants
                variants = generate_srcset_variants(content, filename)
            except Exception as _e:  # noqa: BLE001
                logger.warning("[srcset-multi] failed for %s: %s", filename, _e)
        results.append({
            "file_id": file_id,
            "filename": filename,
            "original_name": file.filename,
            "url": r2_url or f"/api/upload/files/{filename}",
            "size": filepath.stat().st_size if filepath.exists() else len(content),
            "type": "video" if is_vid else None,
            "thumbnail_url": thumbnail_url,
            "duration": duration,
            **variants,
        })
    return results


# ────────────────────────────────────────────────────────────────────────
# iter190b — Async video transcode job polling
# ────────────────────────────────────────────────────────────────────────
@router.get("/video-job/{job_id}")
async def video_job_status(job_id: str, request: Request):
    """Frontend polls this endpoint while a large-video transcode runs.

    Status flow: pending → processing → ready (or failed).
    On `ready`: response contains `url` + `thumbnail_url` + `duration`,
    ready to be embedded in a feed post.
    """
    user = await get_current_user(request)
    # Light validation — alphanum/underscore only, length matches uuid hex.
    if not job_id or not job_id.replace("_", "").isalnum() or len(job_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid job id")
    from database import get_pool
    pool = await get_pool()
    job = await get_video_job(pool, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Owner-only access (admins bypass).
    if job["user_id"] != user["user_id"] and user.get("role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Forbidden")

    out = {
        "job_id": job["job_id"],
        "status": job["status"],
        "duration": job.get("duration"),
        "size_bytes": job.get("size_bytes"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
    }
    if job["status"] == "ready" and job.get("out_filename"):
        out["url"] = f"/api/upload/files/{job['out_filename']}"
        if job.get("thumb_filename"):
            out["thumbnail_url"] = f"/api/upload/files/{job['thumb_filename']}"
    return out



@router.get("/files/{filename}")
async def serve_file(request: Request, filename: str, fmt: str = ""):
    # 1. Reject traversal / slashes BEFORE hitting the filesystem.
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    ext = Path(filename).suffix.lower()
    if ext not in _SAFE_EXTS:
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = UPLOAD_DIR / filename
    # iter239d — Backwards-compatibility for legacy URLs `/api/upload/files/<name>`:
    # if the file no longer exists on local disk (e.g. pod recycled) but a
    # matching object exists in R2, redirect there. Probes the most likely R2
    # folder by file extension. 301 = permanent (browser & CDN can cache).
    if not filepath.is_file():
        try:
            from services.r2_storage_service import (
                media_object_exists, _folder_for_extension, R2_MEDIA_PUBLIC_URL)
            folder = _folder_for_extension(ext)
            # R2 keys carry a `<uuid>_<originalname>` prefix added at upload
            # time, so a direct `head_object` on `<folder>/<filename>` likely
            # misses. We try the exact name first (some pre-iter239d files
            # may have been migrated under their bare name) then bail.
            if media_object_exists(folder, filename):
                return RedirectResponse(
                    f"{R2_MEDIA_PUBLIC_URL}/{folder}/{filename}", status_code=301)
        except Exception as _e:  # noqa: BLE001
            logger.debug("[serve-file] R2 fallback skipped for %s: %s", filename, _e)

        # iter239t — Last-chance fallback for KYC legacy URLs that leaked into
        # browser caches / stale Service Workers. If the missing filename
        # matches a row in `kyc_verifications` (any of id_photo_url /
        # id_back_photo_url / selfie_url / *_preview), serve the bytes
        # directly from the corresponding BYTEA column. This rescues the
        # `/api/upload/files/d8ba46f6e9df460a.webp` 404s reported in prod.
        try:
            from database import get_pool
            legacy_path = f"/api/upload/files/{filename}"
            pool = await get_pool()
            async with pool.acquire() as conn:
                kyc_row = await conn.fetchrow(
                    """
                    SELECT
                      id_photo_bytes,      id_photo_url,
                      id_back_photo_bytes, id_back_photo_url,
                      selfie_bytes,        selfie_url,
                      preview_id_bytes,      preview_id_url,
                      preview_id_back_bytes, preview_id_back_url,
                      preview_selfie_bytes,  preview_selfie_url
                    FROM kyc_verifications
                    WHERE id_photo_url       = $1
                       OR id_back_photo_url  = $1
                       OR selfie_url         = $1
                       OR preview_id_url     = $1
                       OR preview_id_back_url= $1
                       OR preview_selfie_url = $1
                    LIMIT 1
                    """,
                    legacy_path,
                )
            if kyc_row is not None:
                # Find the column that matches this URL and serve its bytes.
                pairs = [
                    ("id_photo_url",       "id_photo_bytes"),
                    ("id_back_photo_url",  "id_back_photo_bytes"),
                    ("selfie_url",         "selfie_bytes"),
                    ("preview_id_url",     "preview_id_bytes"),
                    ("preview_id_back_url","preview_id_back_bytes"),
                    ("preview_selfie_url", "preview_selfie_bytes"),
                ]
                for url_col, bytes_col in pairs:
                    if kyc_row[url_col] == legacy_path and kyc_row[bytes_col]:
                        return Response(
                            content=kyc_row[bytes_col],
                            media_type="image/jpeg",
                            headers={
                                "Cache-Control": "private, max-age=3600",
                                "X-Japap-Source": "kyc-legacy-recovery",
                            },
                        )
        except Exception as _e:  # noqa: BLE001
            logger.debug("[serve-file] KYC recovery skipped for %s: %s", filename, _e)

        # iter239u — Same recovery flow for user avatars / covers. The
        # legacy URL `/api/upload/files/{hash}.webp` is the source of the
        # `d8ba46f6e9df460a.webp` 404 reported in prod (it's the admin's
        # own avatar). When the disk file is gone, we try R2 one more time
        # under the `avatars` folder by hash, then fall back to a 1×1
        # transparent SVG pixel so the broken-image icon never appears in
        # the admin lists and elsewhere (better UX than a red ❌).
        try:
            from services.r2_storage_service import (
                media_object_exists as _r2_exists,
                R2_MEDIA_PUBLIC_URL as _R2_BASE,
            )
            for folder in ("profile", "cover", "general"):
                if _r2_exists(folder, filename):
                    return RedirectResponse(
                        f"{_R2_BASE}/{folder}/{filename}", status_code=301)
        except Exception as _e:  # noqa: BLE001
            logger.debug("[serve-file] R2 deep-probe skipped for %s: %s", filename, _e)

        # iter239u — Check if this legacy path is referenced anywhere in
        # the users table. If yes, serve a graceful tiny fallback so the
        # UI doesn't show a broken-image icon. We don't have BYTEA in the
        # users table (only URL strings), so we just acknowledge that the
        # ressource is known + irrecoverable, and send a 1x1 PNG instead
        # of 404. This avoids the user-facing "❌" visible across many
        # admin/profile pages while keeping the file genuinely gone.
        try:
            from database import get_pool as _gp
            _legacy_path = f"/api/upload/files/{filename}"
            _pool = await _gp()
            async with _pool.acquire() as _conn:
                _u = await _conn.fetchrow(
                    """
                    SELECT user_id FROM users
                    WHERE avatar = $1 OR avatar_thumb = $1
                       OR cover = $1 OR cover_image = $1
                       OR cover_image_mobile = $1
                    LIMIT 1
                    """,
                    _legacy_path,
                )
            if _u is not None:
                # 1x1 transparent PNG (67 bytes) — safe fallback that the
                # browser treats as a valid image and silently lets the
                # initial-fallback layer (e.g. <div>{name[0]}</div>) show.
                _TINY_PNG = bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                    "890000000d49444154789c63f8cf00000000050001"
                    "ff5b9b4f0000000049454e44ae426082"
                )
                return Response(
                    content=_TINY_PNG,
                    media_type="image/png",
                    headers={
                        "Cache-Control": "public, max-age=3600",
                        "X-Japap-Source": "avatar-missing-tinypng",
                    },
                )
        except Exception as _e:  # noqa: BLE001
            logger.debug("[serve-file] avatar tinypng skipped for %s: %s",
                         filename, _e)
    # 2. Defence-in-depth — resolve and confirm parent is exactly UPLOAD_DIR.
    try:
        real = filepath.resolve()
        if real.parent != UPLOAD_DIR.resolve() or not real.is_file():
            raise HTTPException(status_code=404, detail="File not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    # iter92 — JPEG fallback for legacy iOS Safari (<14) that can't decode WebP.
    cache_headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    served_path = filepath
    media_type = None
    if fmt == "jpg" and ext == ".webp":
        jpg_path = filepath.with_suffix(".fallback.jpg")
        if not jpg_path.is_file():
            try:
                from PIL import Image as _I
                im = _I.open(filepath)
                if im.mode not in ("RGB",):
                    im = im.convert("RGB")
                im.save(jpg_path, format="JPEG", quality=82, optimize=True)
            except Exception as e:
                logger.warning("JPEG fallback generation failed for %s: %s", filename, e)
                jpg_path = filepath  # silent fallback to WebP
        served_path = jpg_path
        media_type = "image/jpeg" if jpg_path != filepath else None

    # iter92-cache — ETag + 304 Not Modified.
    # The file's content is immutable (UUID filename → new file = new URL) so
    # we derive a strong ETag from the on-disk content.
    import hashlib
    try:
        with open(served_path, "rb") as _f:
            etag_val = '"' + hashlib.md5(_f.read()).hexdigest() + '"'
    except Exception:
        etag_val = None

    if etag_val:
        # Starlette normalises header names to lower-case.
        if_none_match = request.headers.get("if-none-match", "").strip()
        if if_none_match:
            # Support multi-etag "a","b" and the wildcard "*".
            candidates = {c.strip() for c in if_none_match.split(",")}
            if etag_val in candidates or "*" in candidates:
                return Response(
                    status_code=304,
                    headers={**cache_headers, "ETag": etag_val},
                )
        cache_headers["ETag"] = etag_val

    return FileResponse(
        served_path,
        headers=cache_headers,
        media_type=media_type,
    )



# ────────────────────────────────────────────────────────────────────────
# iter92 — Smart image pipeline for profile / cover photos
# ────────────────────────────────────────────────────────────────────────
#
# Goals (per product brief):
#   Profile:  accept ≤ 1024×1024 → resize to 512×512 + thumb 128×128, ≤ 100 KB
#   Cover:    accept ≤ 1920×720 → resize to 1280×480 + mobile 640×240,   ≤ 200 KB
#   Tech:     WebP primary (JPEG fallback), strip EXIF, mobile-optimized.
# ────────────────────────────────────────────────────────────────────────

# kind → (max_in_w, max_in_h, main_w, main_h, thumb_w, thumb_h, target_kb, crop_mode)
_IMAGE_KINDS = {
    "profile": (1024, 1024, 512, 512, 128, 128, 100, "cover_square"),
    "cover":   (1920, 720,  1280, 480, 640, 240, 200, "cover_rect"),
    # iter94 — Feed post images. Accept large (common phone capture is 4032×3024),
    # downscale to 1600px on longest side, generate 400px thumbnail for grids.
    # No forced aspect — posts can be portrait/landscape/square.
    "post":    (4032, 4032, 1600, 1600, 400, 400, 250, "fit"),
    # iter97 — Driver KYC documents (license card, ID card, selfie with license).
    # Accept large captures, downscale to 1600 longest side. 300KB budget so the
    # admin can clearly read license numbers / expiry dates / serial codes.
    # Square 600×600 thumbnail used in the admin review grid.
    "driver_doc": (4032, 4032, 1600, 1600, 600, 600, 300, "fit"),
}


def _normalize_mode(img):
    """Convert any Pillow mode (P, RGBA, LA, CMYK…) to RGB for uniform encoding."""
    from PIL import Image as _I
    if img.mode == "RGBA":
        bg = _I.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    if img.mode not in ("RGB",):
        return img.convert("RGB")
    return img


def _cover_resize(img, tw: int, th: int):
    """Resize covering the target box (center-crop), preserving aspect ratio."""
    from PIL import Image as _I
    sw, sh = img.size
    target_ratio = tw / th
    src_ratio = sw / sh
    if src_ratio > target_ratio:
        new_w = int(sh * target_ratio)
        left = (sw - new_w) // 2
        img = img.crop((left, 0, left + new_w, sh))
    elif src_ratio < target_ratio:
        new_h = int(sw / target_ratio)
        top = (sh - new_h) // 2
        img = img.crop((0, top, sw, top + new_h))
    return img.resize((tw, th), _I.LANCZOS)


def _encode_bounded(img, fmt: str, target_bytes: int) -> bytes:
    """Encode repeatedly with decreasing quality until size ≤ target_bytes
    OR quality floor is reached. Returns best candidate."""
    best = None
    for q in (92, 85, 78, 70, 62, 55, 48):
        out = io.BytesIO()
        kwargs = {"format": fmt, "optimize": True, "quality": q}
        if fmt == "WEBP":
            kwargs["method"] = 6
        img.save(out, **kwargs)
        data = out.getvalue()
        best = data
        if len(data) <= target_bytes:
            return data
    return best or b""


def _process_avatar_or_cover(content: bytes, kind: str):
    """Pipeline: decode → downscale-to-max → normalize → cover-crop main + thumb
    → encode under size budget. Returns bytes+ext+mime for each output."""
    from PIL import Image as _I
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {list(_IMAGE_KINDS)}")
    max_iw, max_ih, mw, mh, tw, th, target_kb, _ = _IMAGE_KINDS[kind]
    try:
        img = _I.open(io.BytesIO(content))
        img.load()
    except Exception:
        raise HTTPException(status_code=400, detail="Image file is corrupt or unsupported")

    src_w, src_h = img.size
    if src_w > max_iw or src_h > max_ih:
        img.thumbnail((max_iw, max_ih), _I.LANCZOS)

    img = _normalize_mode(img)

    # iter94 — "fit" crop mode for feed posts: preserve source aspect ratio,
    # just downscale the longest side to the target. The thumbnail is always
    # center-cropped to the target square (used for grid previews).
    crop_mode = _IMAGE_KINDS[kind][7]
    if crop_mode == "fit":
        # Fit main within mw × mh (no forced crop — keeps portrait/landscape).
        main_img = img.copy()
        main_img.thumbnail((mw, mh), _I.LANCZOS)
        thumb_img = _cover_resize(img, tw, th)
    else:
        main_img = _cover_resize(img, mw, mh)
        thumb_img = _cover_resize(img, tw, th)

    def _encode(pil_img):
        target_bytes = target_kb * 1024
        try:
            data = _encode_bounded(pil_img, "WEBP", target_bytes)
            if data:
                return data, "webp", "image/webp"
        except Exception as e:
            logger.warning(f"WebP encode failed, falling back to JPEG: {e}")
        data = _encode_bounded(pil_img, "JPEG", target_bytes)
        return data, "jpg", "image/jpeg"

    m_bytes, m_ext, m_mime = _encode(main_img)
    t_bytes, t_ext, t_mime = _encode(thumb_img)
    return {
        "main":  {"bytes": m_bytes,  "ext": m_ext, "mime": m_mime},
        "thumb": {"bytes": t_bytes,  "ext": t_ext, "mime": t_mime},
        "dims":  {"main": list(main_img.size), "thumb": list(thumb_img.size), "source": [src_w, src_h]},
    }


@router.post("/image")
async def upload_image(
    request: Request,
    kind: str = Query(..., pattern="^(profile|cover|post|driver_doc)$"),
    file: UploadFile = File(...),
):
    """Smart image upload for profile / cover / post photos.
    Returns both main + thumbnail URLs after full server-side optimization.
    """
    user = await get_current_user(request)

    original = file.filename or ""
    ext_in = Path(original).suffix.lower()
    if ext_in not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise HTTPException(status_code=400, detail=f"Image type not allowed: {ext_in or '(none)'}")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    if not _sniff_is_safe(ext_in, content):
        raise HTTPException(status_code=400, detail="File content does not match extension")

    processed = _process_avatar_or_cover(content, kind)

    def _persist(blob: dict) -> str:
        file_id = uuid.uuid4().hex[:16]
        filename = f"{file_id}.{blob['ext']}"
        fp = UPLOAD_DIR / filename
        if fp.resolve().parent != UPLOAD_DIR.resolve():
            raise HTTPException(status_code=400, detail="Invalid upload path")
        with open(fp, "wb") as f:
            f.write(blob["bytes"])
        try:
            os.chmod(fp, 0o644)
        except Exception:
            pass
        return f"/api/upload/files/{filename}"

    main_url = _persist(processed["main"])
    thumb_url = _persist(processed["thumb"])
    # iter239d — Push to R2 and use the R2 public URL when available so
    # avatars/covers survive pod recycles.
    try:
        from services.r2_storage_service import upload_media_file_to_r2
        main_local = UPLOAD_DIR / Path(main_url).name
        thumb_local = UPLOAD_DIR / Path(thumb_url).name
        if main_local.is_file():
            main_url = upload_media_file_to_r2(
                main_local, folder=("avatars" if kind == "profile" else
                                    "covers" if kind == "cover" else "images"))
        if thumb_local.is_file():
            thumb_url = upload_media_file_to_r2(
                thumb_local, folder="thumbnails", content_type="image/jpeg")
    except Exception as _e:  # noqa: BLE001
        logger.warning("[r2-image] upload failed, keeping local: %s", _e)
    # iter239e — srcset variants for `kind=post` images (avatar/cover
    # already produce a single appropriately-sized image, no benefit).
    variants: dict[str, str] = {}
    if kind == "post":
        try:
            from services.r2_storage_service import generate_srcset_variants
            variants = generate_srcset_variants(
                processed["main"]["bytes"],
                f"{Path(original).stem}.{processed['main']['ext']}",
            )
        except Exception as _e:  # noqa: BLE001
            logger.warning("[srcset-image] failed: %s", _e)
    logger.info(
        "Smart image uploaded by %s: kind=%s source=%sx%s main=%s (%d B) thumb=%s (%d B)",
        user.get("user_id"), kind,
        processed["dims"]["source"][0], processed["dims"]["source"][1],
        processed["main"]["ext"], len(processed["main"]["bytes"]),
        processed["thumb"]["ext"], len(processed["thumb"]["bytes"]),
    )
    return {
        "kind": kind,
        "main": {
            "url": main_url, "size": len(processed["main"]["bytes"]),
            "mime": processed["main"]["mime"],
            "width": processed["dims"]["main"][0],
            "height": processed["dims"]["main"][1],
            **variants,
        },
        "thumb": {
            "url": thumb_url, "size": len(processed["thumb"]["bytes"]),
            "mime": processed["thumb"]["mime"],
            "width": processed["dims"]["thumb"][0],
            "height": processed["dims"]["thumb"][1],
        },
        "source": {
            "width": processed["dims"]["source"][0],
            "height": processed["dims"]["source"][1],
            "original_name": original,
        },
    }
