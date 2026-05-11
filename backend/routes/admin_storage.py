"""
iter239d — Admin storage panel (R2 media bucket stats + migration trigger).

Routes (admin-only):
  • GET  /api/admin/storage/stats           → R2 bucket size + file count + local fallback stats
  • POST /api/admin/storage/migrate-to-r2   → kick off the local→R2 migration in background
  • GET  /api/admin/storage/migration-status→ poll the background task progress

Strictly additive. Does not touch the existing recordings bucket helpers.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Request

from routes.auth import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/storage", tags=["admin_storage"])

UPLOAD_DIR = Path("/app/backend/uploads")

# In-process migration state. A redeploy resets it — fine, the migration is
# idempotent and the admin can simply re-trigger.
_migration_state: dict = {
    "running": False,
    "started_at": None,
    "ended_at": None,
    "result": None,
    "started_by": None,
}


def _local_stats() -> dict:
    if not UPLOAD_DIR.is_dir():
        return {"file_count": 0, "size_bytes": 0, "size_mb": 0.0}
    count = 0
    size = 0
    for p in UPLOAD_DIR.rglob("*"):
        if p.is_file():
            count += 1
            size += p.stat().st_size
    return {"file_count": count, "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2)}


@router.get("/stats")
async def admin_storage_stats(request: Request):
    await require_admin(request)
    from services.r2_storage_service import list_media_stats
    return {
        "local": _local_stats() | {"path": str(UPLOAD_DIR)},
        "r2": list_media_stats(),
        "migration": _public_migration_view(),
    }


def _public_migration_view() -> dict:
    return {
        "running": _migration_state["running"],
        "started_at": _migration_state["started_at"],
        "ended_at": _migration_state["ended_at"],
        "started_by": _migration_state["started_by"],
        "result": _migration_state["result"],
    }


@router.post("/migrate-to-r2")
async def admin_storage_migrate_to_r2(request: Request):
    admin = await require_admin(request)
    if _migration_state["running"]:
        return {"status": "already_running", "state": _public_migration_view()}

    from datetime import datetime, timezone
    from services.r2_storage_service import migrate_local_uploads_to_r2

    _migration_state["running"] = True
    _migration_state["started_at"] = datetime.now(timezone.utc).isoformat()
    _migration_state["ended_at"] = None
    _migration_state["result"] = None
    _migration_state["started_by"] = admin.get("user_id")

    async def _run():
        try:
            res = await migrate_local_uploads_to_r2(str(UPLOAD_DIR) + "/")
            _migration_state["result"] = res
            logger.info("[r2-migrate] finished: %s", res)
        except Exception as e:  # noqa: BLE001
            _migration_state["result"] = {"error": f"{type(e).__name__}: {e}"}
            logger.error("[r2-migrate] crashed: %s", e)
        finally:
            _migration_state["running"] = False
            _migration_state["ended_at"] = datetime.now(timezone.utc).isoformat()

    asyncio.create_task(_run())
    return {"status": "started", "state": _public_migration_view()}


@router.get("/migration-status")
async def admin_storage_migration_status(request: Request):
    await require_admin(request)
    return _public_migration_view()


@router.get("/diagnostics")
async def admin_storage_diagnostics(request: Request):
    """iter239f — Post-deploy verification endpoint. Reports the runtime
    state of every dependency this iteration relies on:

      • ffmpeg / ffprobe presence + version  (video transcoding)
      • R2 env vars present (5 keys)
      • R2 bucket reachable (list_media_stats)
      • Pillow + pillow-avif-plugin importable (AVIF variants)
      • Local upload dir writable

    Returns a flat dict — green keys are healthy, red ones surface a clear
    `error` field for the admin. Designed to be smoke-tested after every
    production redeploy."""
    await require_admin(request)
    import os
    import shutil
    import subprocess

    report: dict = {}

    # ── 1. ffmpeg ────────────────────────────────────────────────────────
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    ffmpeg_version = None
    if ffmpeg_path:
        try:
            out = subprocess.run([ffmpeg_path, "-version"], capture_output=True,
                                  text=True, timeout=5).stdout
            ffmpeg_version = out.splitlines()[0] if out else None
        except Exception:
            pass
    report["ffmpeg"] = {
        "ok": bool(ffmpeg_path and ffprobe_path),
        "ffmpeg_path": ffmpeg_path,
        "ffprobe_path": ffprobe_path,
        "version": ffmpeg_version,
    }

    # ── 2. R2 env vars ───────────────────────────────────────────────────
    r2_env_keys = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                   "R2_BUCKET_NAME", "R2_PUBLIC_URL"]
    r2_env: dict = {}
    for k in r2_env_keys:
        v = os.environ.get(k, "")
        # NEVER return the actual secret — only presence + length hint.
        r2_env[k] = {"present": bool(v), "length": len(v)} if v else {"present": False}
    report["r2_env"] = {
        "ok": all(os.environ.get(k) for k in r2_env_keys),
        "keys": r2_env,
    }

    # ── 3. R2 bucket reachability ───────────────────────────────────────
    try:
        from services.r2_storage_service import list_media_stats
        report["r2_bucket"] = list_media_stats()
    except Exception as e:  # noqa: BLE001
        report["r2_bucket"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── 4. Pillow + AVIF plugin ─────────────────────────────────────────
    pil_ok = False
    pil_version = None
    avif_ok = False
    try:
        from PIL import Image  # noqa: PLC0415
        pil_ok = True
        pil_version = Image.__version__
    except ImportError as e:
        report["pil_error"] = str(e)
    try:
        import pillow_avif  # noqa: F401, PLC0415
        avif_ok = True
    except ImportError:
        pass
    report["pillow"] = {
        "ok": pil_ok, "version": pil_version,
        "avif_plugin": avif_ok,
    }

    # ── 5. Local upload dir ─────────────────────────────────────────────
    try:
        local = _local_stats()
        report["local_uploads"] = local | {"path": str(UPLOAD_DIR),
                                            "writable": os.access(UPLOAD_DIR, os.W_OK)}
    except Exception as e:  # noqa: BLE001
        report["local_uploads"] = {"ok": False, "error": str(e)}

    # ── 6. Overall verdict ──────────────────────────────────────────────
    report["overall_ok"] = bool(
        report["ffmpeg"].get("ok")
        and report["r2_env"].get("ok")
        and report["r2_bucket"].get("ok")
        and report["pillow"].get("ok")
    )
    return report


admin_storage_router = router
__all__ = ["router", "admin_storage_router"]
