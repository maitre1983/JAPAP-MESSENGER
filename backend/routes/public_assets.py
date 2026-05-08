"""
Public static assets served by the backend for use inside HTML emails.

These routes are intentionally unauthenticated, cacheable for a year and
return the correct Content-Type so that Gmail / Outlook / Apple Mail
reliably fetch and display them.

The only reason we host these assets through the API instead of directly
from the frontend CDN is that the frontend preview URL is not guaranteed
to stay the same across fork/deploy cycles, while the API endpoint is a
stable part of the backend contract.
"""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/assets", tags=["assets"])

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/logo.png")
async def logo_png():
    path = _ASSETS_DIR / "japap-logo.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Logo not found")
    return FileResponse(
        str(path),
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Access-Control-Allow-Origin": "*",
        },
    )
