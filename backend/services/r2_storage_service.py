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
