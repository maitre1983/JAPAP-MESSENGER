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


admin_storage_router = router
__all__ = ["router", "admin_storage_router"]
