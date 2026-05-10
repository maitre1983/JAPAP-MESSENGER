"""
JAPAP — Admin Settings REST API
===============================
- GET  /api/admin/settings              → full settings dict (admin-only)
- PUT  /api/admin/settings              → bulk update ({ key: value, ... })
- PUT  /api/admin/settings/{key}        → single-key update ({ value })
- GET  /api/settings/public             → read-only subset, safe for any caller
"""
import json
import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Any

from database import get_pool
from services.settings_service import get_all, get_setting, set_setting, get_public, DEFAULTS, PUBLIC_KEYS
from routes.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

# Secret keys: the admin UI shows them masked ("••••••••last4") to avoid
# shoulder-surfing / screenshot leaks. Admin can still overwrite them via PUT.
SECRET_KEYS = {
    "hubtel_client_id", "hubtel_client_secret", "hubtel_merchant_account",
    "hubtel_webhook_secret",
    "nowpayments_api_key", "nowpayments_ipn_secret",
    # LiveKit + R2 (Sprint B/C/D)
    "livekit_api_key", "livekit_api_secret",
    "r2_access_key_id", "r2_secret_access_key",
}
MASK_PREFIX = "••••••••"   # sentinel — PUT requests with this prefix are ignored


def _mask(value: str) -> str:
    if not value:
        return ""
    v = str(value)
    if len(v) <= 4:
        return MASK_PREFIX
    return f"{MASK_PREFIX}{v[-4:]}"


class SettingUpdate(BaseModel):
    value: Any


class BulkSettings(BaseModel):
    settings: dict[str, Any]


# iter239a — audit-log helper (additive). Records every admin write so we can
# answer "who toggled what, when?" later. Best-effort: never blocks the write.
async def _audit_settings_change(admin_user: dict, key: str,
                                  before: Any, after: Any) -> None:
    if key in SECRET_KEYS:
        # Never log secret values — only the fact that they were updated.
        before_safe, after_safe = "***", "***"
    else:
        before_safe = str(before) if before is not None else None
        after_safe  = str(after)  if after  is not None else None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO audit_logs (user_id, action, resource, details)
                   VALUES ($1, 'admin_setting_updated', 'system_settings', $2)""",
                admin_user.get("user_id"),
                json.dumps({"key": key, "before": before_safe, "after": after_safe}),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[admin-settings-audit] failed to record %s: %s", key, e)


@router.get("/api/admin/settings")
async def admin_get_settings(request: Request):
    await require_admin(request)
    data = await get_all()
    # Mask sensitive values before sending to the admin UI.
    masked = {
        k: (_mask(v) if k in SECRET_KEYS and v else v)
        for k, v in data.items()
    }
    # Tell the UI which secrets are actually configured (non-empty) so it can
    # render a "Configuré" chip without leaking the value.
    configured = {k: bool((data.get(k) or "").strip()) for k in SECRET_KEYS}
    return {
        "settings": masked,
        "defaults": DEFAULTS,
        "public_keys": sorted(PUBLIC_KEYS),
        "secret_keys": sorted(SECRET_KEYS),
        "secret_configured": configured,
    }


@router.put("/api/admin/settings")
async def admin_update_settings(req: BulkSettings, request: Request):
    admin = await require_admin(request)
    updated = []
    for k, v in (req.settings or {}).items():
        if not k or not isinstance(k, str):
            continue
        # Ignore masked echoes ("••••••••") — only persist real values.
        if k in SECRET_KEYS and isinstance(v, str) and v.startswith(MASK_PREFIX):
            continue
        before = await get_setting(k)
        await set_setting(k, v)
        await _audit_settings_change(admin, k, before, v)
        updated.append(k)
    return {"status": "ok", "updated": updated}


@router.put("/api/admin/settings/{key}")
async def admin_update_setting(key: str, req: SettingUpdate, request: Request):
    admin = await require_admin(request)
    if not key.strip():
        raise HTTPException(status_code=400, detail="Key required")
    # Block masked echoes for secret keys.
    if key in SECRET_KEYS and isinstance(req.value, str) and req.value.startswith(MASK_PREFIX):
        return {"status": "ok", "key": key, "ignored_mask_echo": True}
    before = await get_setting(key)
    await set_setting(key, req.value)
    await _audit_settings_change(admin, key, before, req.value)
    return {"status": "ok", "key": key}


@router.get("/api/settings/public")
async def public_settings():
    return await get_public()
