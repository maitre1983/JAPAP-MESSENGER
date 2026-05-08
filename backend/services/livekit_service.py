"""
LiveKit service — abstraction layer for audio/video calls.

This module is designed to be plugged in as soon as the admin provides:
    - LIVEKIT_API_KEY
    - LIVEKIT_API_SECRET
    - LIVEKIT_WS_URL
These settings are read at runtime from admin_settings (or env as fallback).
When the credentials are not yet configured, every call-management function
raises `LiveKitConfigError` — the routes catch this and return a friendly
503 so the UI can show "Appels non disponibles".

Used by:
    - /api/calls/token          → generate a JWT for the frontend SDK
    - /api/calls/record         → start/stop egress to R2
    - server.py                 → (future) webhook handler for room events
"""
import os
import time
import logging
from datetime import timedelta
from typing import Any

import httpx

from services.settings_service import get_setting

logger = logging.getLogger(__name__)


class LiveKitConfigError(Exception):
    """Raised when LiveKit credentials are missing / incomplete."""


class LiveKitAPIError(Exception):
    """Raised when the LiveKit server API returns a non-success response."""


async def _get_config() -> dict[str, str]:
    api_key = (await get_setting("livekit_api_key")) or os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = (await get_setting("livekit_api_secret")) or os.environ.get("LIVEKIT_API_SECRET", "")
    ws_url = (await get_setting("livekit_ws_url")) or os.environ.get("LIVEKIT_WS_URL", "")
    if not (api_key and api_secret and ws_url):
        raise LiveKitConfigError(
            "LiveKit non configuré. Renseignez api_key/api_secret/ws_url dans "
            "Admin → Paiements → Paramètres (ou LIVEKIT_* dans .env)."
        )
    return {"api_key": api_key, "api_secret": api_secret, "ws_url": ws_url}


def _rest_base(ws_url: str) -> str:
    """Convert wss://xxx.livekit.cloud → https://xxx.livekit.cloud for REST calls."""
    return ws_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


async def generate_access_token(
    *,
    identity: str,
    name: str,
    room: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
    ttl_seconds: int = 3600,
    metadata: str = "",
) -> dict[str, str]:
    """Generate a JWT that the frontend SDK uses to join a LiveKit room.

    The token embeds:
      - identity : stable user_id (LiveKit uses it as Participant.identity)
      - name     : display name
      - grants   : room-specific permissions (publish/subscribe/recordings)
      - exp      : 1h by default
    """
    cfg = await _get_config()
    try:
        # livekit-api (sync, tiny, no network) — imported lazily so missing
        # package doesn't block the whole app.
        from livekit import api as lk_api
    except ImportError as e:
        raise LiveKitConfigError(
            f"Le SDK livekit-api n'est pas installé : {e}. "
            f"Exécutez `pip install livekit-api` puis redémarrez le backend."
        )
    token = (
        lk_api.AccessToken(cfg["api_key"], cfg["api_secret"])
        .with_identity(identity)
        .with_name(name)
        .with_metadata(metadata)
        .with_grants(
            lk_api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=can_publish,
                can_subscribe=can_subscribe,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(seconds=ttl_seconds))
    )
    return {
        "token": token.to_jwt(),
        "ws_url": cfg["ws_url"],
        "room": room,
        "identity": identity,
        "expires_at": int(time.time()) + ttl_seconds,
    }


async def create_room(room_name: str, *, max_participants: int = 12, empty_timeout: int = 180) -> dict[str, Any]:
    """Pre-create a LiveKit room. Safe to call even if the room already exists
    (LiveKit is idempotent on name). Returns the room metadata."""
    cfg = await _get_config()
    try:
        from livekit import api as lk_api
    except ImportError as e:
        raise LiveKitConfigError(str(e))
    service = lk_api.LiveKitAPI(_rest_base(cfg["ws_url"]), cfg["api_key"], cfg["api_secret"])
    try:
        room = await service.room.create_room(
            lk_api.CreateRoomRequest(
                name=room_name,
                empty_timeout=empty_timeout,
                max_participants=max_participants,
            )
        )
        return {
            "sid": room.sid,
            "name": room.name,
            "max_participants": room.max_participants,
            "creation_time": room.creation_time,
        }
    finally:
        await service.aclose()


async def delete_room(room_name: str) -> None:
    cfg = await _get_config()
    try:
        from livekit import api as lk_api
    except ImportError as e:
        raise LiveKitConfigError(str(e))
    service = lk_api.LiveKitAPI(_rest_base(cfg["ws_url"]), cfg["api_key"], cfg["api_secret"])
    try:
        await service.room.delete_room(lk_api.DeleteRoomRequest(room=room_name))
    finally:
        await service.aclose()


async def start_room_composite_egress(
    *,
    room_name: str,
    storage_provider: str = "r2",
    bucket: str = "",
    key: str = "",
) -> dict[str, Any]:
    """Kick off a LiveKit Composite Egress to record audio+video of the room
    as a single MP4 file and upload it to Cloudflare R2 (S3-compatible API).

    Returns the egress id so the backend can poll or receive webhook events.
    Sprint D uses this to trigger recording from the UI.
    """
    cfg = await _get_config()
    try:
        from livekit import api as lk_api
    except ImportError as e:
        raise LiveKitConfigError(str(e))
    # R2 credentials (Sprint D) — resolved lazily to avoid blocking audio calls.
    r2_cfg = await _load_r2_config()
    service = lk_api.LiveKitAPI(_rest_base(cfg["ws_url"]), cfg["api_key"], cfg["api_secret"])
    try:
        request = lk_api.RoomCompositeEgressRequest(
            room_name=room_name,
            file_outputs=[
                lk_api.EncodedFileOutput(
                    file_type=lk_api.EncodedFileType.MP4,
                    filepath=key,
                    s3=lk_api.S3Upload(
                        access_key=r2_cfg["access_key"],
                        secret=r2_cfg["secret"],
                        region="auto",            # R2 uses "auto"
                        bucket=bucket,
                        endpoint=r2_cfg["endpoint"],
                        force_path_style=True,
                    ),
                )
            ],
        )
        info = await service.egress.start_room_composite_egress(request)
        return {
            "egress_id": info.egress_id,
            "status": str(info.status),
            "room": room_name,
            "storage_provider": storage_provider,
            "bucket": bucket,
            "key": key,
        }
    finally:
        await service.aclose()


async def stop_egress(egress_id: str) -> dict[str, Any]:
    cfg = await _get_config()
    try:
        from livekit import api as lk_api
    except ImportError as e:
        raise LiveKitConfigError(str(e))
    service = lk_api.LiveKitAPI(_rest_base(cfg["ws_url"]), cfg["api_key"], cfg["api_secret"])
    try:
        info = await service.egress.stop_egress(lk_api.StopEgressRequest(egress_id=egress_id))
        return {"egress_id": info.egress_id, "status": str(info.status)}
    finally:
        await service.aclose()


async def _load_r2_config() -> dict[str, str]:
    """Import locally to avoid a hard dep at module load."""
    from services.r2_storage_service import get_r2_config
    return await get_r2_config()


async def test_connection() -> dict[str, Any]:
    """Admin-only : verify the creds by creating + deleting a test room."""
    try:
        cfg = await _get_config()
    except LiveKitConfigError as e:
        return {"ok": False, "reason": str(e)}
    try:
        from livekit import api as lk_api  # noqa: F401
    except ImportError as e:
        return {"ok": False, "reason": f"SDK non installé : {e}"}
    test_room = f"healthcheck_{int(time.time())}"
    try:
        await create_room(test_room, max_participants=2, empty_timeout=10)
        await delete_room(test_room)
        return {"ok": True, "ws_url": cfg["ws_url"]}
    except httpx.HTTPError as e:
        return {"ok": False, "reason": f"Réseau indisponible : {e}"}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
