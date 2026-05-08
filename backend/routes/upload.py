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
from fastapi.responses import FileResponse, Response
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
    logger.info(f"File uploaded by {user['user_id']}: {filename} "
                 f"({filepath.stat().st_size if filepath.exists() else 0} bytes)"
                 f"{' [video transcoded]' if is_vid else ''}")

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

        results.append({
            "file_id": file_id,
            "filename": filename,
            "original_name": file.filename,
            "url": f"/api/upload/files/{filename}",
            "size": filepath.stat().st_size if filepath.exists() else len(content),
            "type": "video" if is_vid else None,
            "thumbnail_url": thumbnail_url,
            "duration": duration,
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
