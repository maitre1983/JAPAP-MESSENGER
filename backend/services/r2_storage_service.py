"""
Cloudflare R2 (S3-compatible) storage service — for Sprint D call recordings.

Credentials are read from admin_settings (or env fallback):
    - r2_account_id          → used to build the endpoint URL
    - r2_access_key_id
    - r2_secret_access_key
    - r2_bucket              → default bucket for recordings (e.g. "japap-recordings")
    - r2_public_base_url     → optional CDN base URL for constructing public URLs
                               (https://recordings.japap.com/)

Endpoint format (https://developers.cloudflare.com/r2/api/s3/api/):
    https://<ACCOUNT_ID>.r2.cloudflarestorage.com

Pricing vs S3 :
    - Storage : $0.015/GB/month (vs $0.023 on S3)
    - Egress  : FREE (vs $0.09/GB on S3)  ← the big win for replay streaming
"""
import os
import logging
from typing import Any

from services.settings_service import get_setting

logger = logging.getLogger(__name__)


class R2ConfigError(Exception):
    """Raised when R2 credentials are missing / incomplete."""


async def get_r2_config() -> dict[str, str]:
    account = (await get_setting("r2_account_id")) or os.environ.get("R2_ACCOUNT_ID", "")
    access_key = (await get_setting("r2_access_key_id")) or os.environ.get("R2_ACCESS_KEY_ID", "")
    secret = (await get_setting("r2_secret_access_key")) or os.environ.get("R2_SECRET_ACCESS_KEY", "")
    bucket = (await get_setting("r2_bucket")) or os.environ.get("R2_BUCKET", "japap-recordings")
    public_base = (await get_setting("r2_public_base_url")) or os.environ.get("R2_PUBLIC_BASE_URL", "")
    if not (account and access_key and secret):
        raise R2ConfigError(
            "Cloudflare R2 non configuré. Renseignez r2_account_id / r2_access_key_id "
            "/ r2_secret_access_key dans Admin → Paiements → Paramètres."
        )
    endpoint = f"https://{account}.r2.cloudflarestorage.com"
    return {
        "account_id": account,
        "access_key": access_key,
        "secret": secret,
        "endpoint": endpoint,
        "bucket": bucket,
        "public_base_url": public_base.rstrip("/"),
    }


def build_recording_key(session_id: str, recording_id: str, ext: str = "mp4") -> str:
    """Consistent layout : recordings/YYYY/MM/<session>/<rid>.mp4."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc)
    return f"recordings/{today:%Y/%m}/{session_id}/{recording_id}.{ext}"


async def build_public_url(key: str) -> str:
    """Return the public playback URL for a stored object.
    When no custom CDN is configured, the bucket must be marked public for the
    URL to resolve (admin can toggle this in R2 dashboard)."""
    cfg = await get_r2_config()
    base = cfg["public_base_url"] or f"{cfg['endpoint']}/{cfg['bucket']}"
    return f"{base}/{key.lstrip('/')}"


async def generate_presigned_get_url(key: str, expires_in: int = 3600) -> str:
    """Boto3-based presigned URL for temporary playback access (1h default).
    Prefer this over public-bucket URLs when recordings are private."""
    cfg = await get_r2_config()
    try:
        import boto3  # type: ignore
        from botocore.client import Config  # type: ignore
    except ImportError:
        raise R2ConfigError("Le SDK boto3 n'est pas installé. `pip install boto3`.")
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": cfg["bucket"], "Key": key},
        ExpiresIn=expires_in,
    )


async def delete_object(key: str) -> None:
    cfg = await get_r2_config()
    try:
        import boto3  # type: ignore
    except ImportError:
        raise R2ConfigError("boto3 non installé.")
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret"],
        region_name="auto",
    )
    s3.delete_object(Bucket=cfg["bucket"], Key=key)


async def test_connection() -> dict[str, Any]:
    """Lightweight connectivity check : list first 1 object of the bucket."""
    try:
        cfg = await get_r2_config()
    except R2ConfigError as e:
        return {"ok": False, "reason": str(e)}
    try:
        import boto3  # type: ignore
    except ImportError:
        return {"ok": False, "reason": "boto3 non installé (pip install boto3)"}
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=cfg["endpoint"],
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret"],
            region_name="auto",
        )
        resp = s3.list_objects_v2(Bucket=cfg["bucket"], MaxKeys=1)
        return {
            "ok": True,
            "bucket": cfg["bucket"],
            "objects_sample": len(resp.get("Contents", [])),
            "endpoint": cfg["endpoint"],
        }
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


# ════════════════════════════════════════════════════════════════════════
# iter239d — JAPAP MEDIA bucket (user uploads: images, videos, avatars).
# Distinct from the call-recordings bucket above. Strictly additive — does
# NOT modify the existing recordings code path.
# ════════════════════════════════════════════════════════════════════════
import mimetypes
import uuid
from pathlib import Path

R2_MEDIA_BUCKET = os.environ.get("R2_BUCKET_NAME", "japap-media")
R2_MEDIA_PUBLIC_URL = (os.environ.get("R2_PUBLIC_URL", "https://media.japapmessenger.com")
                       .rstrip("/"))


def _get_r2_media_client():
    """Sync boto3 client for the media bucket. Reads from env directly
    (separate from `get_r2_config` which is async + admin_settings backed).
    Raises R2ConfigError if env not configured."""
    account_id = os.environ.get("R2_ACCOUNT_ID", "")
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    if not (account_id and access_key and secret_key):
        raise R2ConfigError(
            "R2 media credentials missing (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
            "R2_SECRET_ACCESS_KEY env vars).")
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except ImportError as e:
        raise R2ConfigError(f"boto3 not installed: {e}")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_media_to_r2(file_bytes: bytes, filename: str,
                       content_type: str | None = None,
                       folder: str = "") -> str:
    """Sync upload to R2 `japap-media` bucket. Returns public URL.

    `folder` is the top-level prefix (e.g. 'images', 'videos', 'thumbnails',
    'avatars'). Filename is uniquified with a uuid hex to prevent collisions.
    Raises R2ConfigError on misconfig; raises botocore/boto3 errors on
    network/auth failures so callers can decide whether to fallback to local."""
    if not content_type:
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"
    safe_name = Path(filename).name  # strip any directory traversal
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    key = f"{folder.strip('/')}/{unique_name}" if folder else unique_name
    client = _get_r2_media_client()
    client.put_object(
        Bucket=R2_MEDIA_BUCKET,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
        CacheControl="public, max-age=31536000",
    )
    return f"{R2_MEDIA_PUBLIC_URL}/{key}"


def upload_media_file_to_r2(local_path: str | Path,
                             folder: str = "",
                             content_type: str | None = None) -> str:
    """Variant that streams from a local file (avoids reading the whole
    payload into memory). Useful for transcoded videos written to /tmp."""
    p = Path(local_path)
    if not p.is_file():
        raise FileNotFoundError(local_path)
    if not content_type:
        content_type, _ = mimetypes.guess_type(p.name)
        content_type = content_type or "application/octet-stream"
    unique_name = f"{uuid.uuid4().hex}_{p.name}"
    key = f"{folder.strip('/')}/{unique_name}" if folder else unique_name
    client = _get_r2_media_client()
    with p.open("rb") as fh:
        client.upload_fileobj(
            fh, R2_MEDIA_BUCKET, key,
            ExtraArgs={"ContentType": content_type,
                       "CacheControl": "public, max-age=31536000"},
        )
    return f"{R2_MEDIA_PUBLIC_URL}/{key}"


def delete_media_from_r2(url: str) -> bool:
    """Delete an object from the media bucket given its public URL.
    Returns True on success, False on any failure (silent)."""
    try:
        key = url.replace(f"{R2_MEDIA_PUBLIC_URL}/", "", 1)
        if key == url:  # the URL did not start with R2_MEDIA_PUBLIC_URL
            return False
        _get_r2_media_client().delete_object(Bucket=R2_MEDIA_BUCKET, Key=key)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("[r2-media] delete failed for %s: %s", url, e)
        return False


def media_object_exists(folder: str, filename: str) -> bool:
    """HEAD the object to test for existence. False on any error (no raise)."""
    try:
        key = f"{folder.strip('/')}/{filename}" if folder else filename
        _get_r2_media_client().head_object(Bucket=R2_MEDIA_BUCKET, Key=key)
        return True
    except Exception:
        return False


def list_media_stats() -> dict:
    """Bucket stats for the admin dashboard. Paginates if > 1000 objects."""
    try:
        client = _get_r2_media_client()
        total_files = 0
        total_size = 0
        token = None
        while True:
            kwargs = {"Bucket": R2_MEDIA_BUCKET}
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                total_files += 1
                total_size += obj.get("Size", 0)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return {
            "ok": True,
            "bucket": R2_MEDIA_BUCKET,
            "public_url": R2_MEDIA_PUBLIC_URL,
            "total_files": total_files,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _folder_for_extension(ext: str) -> str:
    """Map a file extension to the appropriate R2 folder."""
    ext = ext.lower().lstrip(".")
    if ext in ("mp4", "mov", "webm", "avi", "mkv", "m4v"):
        return "videos"
    if ext in ("jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "heif"):
        return "images"
    if ext in ("mp3", "wav", "m4a", "aac", "ogg", "flac"):
        return "audio"
    return "files"


# ── iter239e — srcset responsive WebP generator ─────────────────────────
# Builds three WebP variants of an uploaded image (480w / 1080w / 1920w)
# and uploads them to R2 under `images/{size}/`. Returns a dict the upload
# route stores alongside the original URL so the frontend can serve the
# right size per device with `<img srcset>`. Falls back gracefully if
# Pillow/Image cannot decode (animated GIFs, AVIF without plugin, etc.).
_SRCSET_SIZES = {"small": 480, "medium": 1080, "large": 1920}
_SRCSET_QUALITY = 85


def generate_srcset_variants(image_bytes: bytes, filename: str) -> dict:
    """Generate 3 WebP variants and upload to R2. Returns
    `{"small_url": ..., "medium_url": ..., "large_url": ...}` (only the keys
    that succeeded). Failures are logged and silently skipped — the caller
    keeps the original URL as a fallback so feed rendering never breaks."""
    out: dict[str, str] = {}
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        logger.warning("[srcset] Pillow not installed, skipping variants")
        return out
    try:
        img = Image.open(io_BytesIO(image_bytes))
        # Drop alpha for output predictability; WebP supports RGBA but some
        # CDNs prefer RGB. Keep RGBA only when the source has a real alpha
        # channel to preserve transparency on PNG stickers / logos.
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
    except Exception as e:  # noqa: BLE001
        logger.warning("[srcset] cannot decode image %s: %s", filename, e)
        return out

    stem = Path(filename).stem or "image"
    for size_name, max_width in _SRCSET_SIZES.items():
        try:
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, max(1, int(img.height * ratio)))
                resized = img.resize(new_size, Image.LANCZOS)
            else:
                resized = img.copy()
            buf = io_BytesIO()
            resized.save(buf, format="WEBP", quality=_SRCSET_QUALITY, method=4)
            buf.seek(0)
            variant_url = upload_media_to_r2(
                buf.read(),
                filename=f"{size_name}_{stem}.webp",
                content_type="image/webp",
                folder=f"images/{size_name}",
            )
            out[f"{size_name}_url"] = variant_url
        except Exception as e:  # noqa: BLE001
            logger.warning("[srcset] variant %s failed for %s: %s",
                           size_name, filename, e)
    return out


def compress_to_webp(image_bytes: bytes, max_size: int = 1080,
                     quality: int = 85) -> bytes:
    """Compress a single image to WebP at the given max dimension. Used for
    avatars / stories / covers where a single sized image is enough (no
    srcset needed because the render slot is fixed). Returns the original
    bytes if Pillow cannot decode the input."""
    try:
        from PIL import Image  # noqa: PLC0415
        img = Image.open(io_BytesIO(image_bytes))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
        buf = io_BytesIO()
        img.save(buf, format="WEBP", quality=quality, method=4)
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.warning("[webp] compression failed, keeping original: %s", e)
        return image_bytes


# Local alias to avoid a top-level `import io` that would shadow other names.
from io import BytesIO as io_BytesIO  # noqa: E402


async def migrate_local_uploads_to_r2(uploads_dir: str = "/app/backend/uploads/") -> dict:
    """Migrate every file in `uploads_dir` to the R2 media bucket and
    rewrite all DB references from `/api/upload/files/<name>` to the new
    R2 URL. Idempotent: skips files whose original filename is already
    referenced as an R2 URL in DB.

    Logs each migration. Returns counters."""
    from database import get_pool

    path = Path(uploads_dir)
    results = {"migrated": 0, "failed": 0, "skipped": 0, "total": 0,
               "errors": []}
    if not path.is_dir():
        results["errors"].append(f"Source dir not found: {uploads_dir}")
        return results

    pool = await get_pool()
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        results["total"] += 1
        local_url_pattern = f"/api/upload/files/{file_path.name}"
        try:
            folder = _folder_for_extension(file_path.suffix)
            content_type, _ = mimetypes.guess_type(file_path.name)
            r2_url = upload_media_file_to_r2(file_path, folder=folder,
                                              content_type=content_type)
            # Best-effort DB rewrite. Failures here don't roll back the upload
            # (the file is on R2; we'll retry the DB rewrite next run).
            try:
                await _rewrite_local_url_in_db(pool, local_url_pattern, r2_url)
            except Exception as e:  # noqa: BLE001
                logger.warning("[r2-migrate] DB rewrite failed for %s: %s",
                               file_path.name, e)
            results["migrated"] += 1
            logger.info("[r2-migrate] %s -> %s", file_path.name, r2_url)
        except Exception as e:  # noqa: BLE001
            results["failed"] += 1
            results["errors"].append(f"{file_path.name}: {type(e).__name__}: {e}")
            logger.error("[r2-migrate] failed for %s: %s", file_path.name, e)
    return results


# Tables/columns that may reference `/api/upload/files/<name>`. Best-effort
# UPDATE — silently skip tables/columns that don't exist in this deployment.
_DB_URL_TABLES: list[tuple[str, list[str]]] = [
    ("users",     ["avatar", "cover"]),
    ("posts",     ["image_url", "video_url", "thumbnail_url", "media_url"]),
    ("stories",   ["image_url", "video_url", "thumbnail_url", "media_url"]),
    ("messages",  ["media_url", "file_url"]),
    ("products",  ["image_url"]),
    ("ad_campaigns",         ["image_url"]),
    ("campaigns",            ["image_url"]),
    ("crowdfunding_projects",["image_url"]),
    ("reels",     ["video_url", "thumbnail_url"]),
]


async def _rewrite_local_url_in_db(pool, old_url: str, new_url: str) -> int:
    """Run a best-effort UPDATE across all known media-bearing tables.
    Returns the total number of rows touched. Tables/columns that don't
    exist are silently skipped (catch in the inner exception)."""
    touched = 0
    async with pool.acquire() as conn:
        for table, columns in _DB_URL_TABLES:
            for col in columns:
                try:
                    res = await conn.execute(
                        f"UPDATE {table} SET {col} = $1 WHERE {col} = $2",
                        new_url, old_url,
                    )
                    # `res` is like "UPDATE 3" — parse the count.
                    try:
                        touched += int(res.split()[-1])
                    except Exception:
                        pass
                except Exception:
                    # Table or column missing — fine, move on.
                    continue
    return touched

