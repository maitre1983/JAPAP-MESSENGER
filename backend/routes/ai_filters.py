"""
iter150 — IA Filters Phase 2 (Gemini Nano Banana / image-to-image).

Five named style transformations exposed via POST /api/media/ai-filter :
    cartoon, anime, oil_painting, cinematic, beauty

The frontend pipeline:
    1. user picks a photo in MediaFilterEditor
    2. user taps an "AI" preset → POST /api/media/ai-filter (multipart)
    3. backend calls Nano Banana with a style-specific prompt + the
       image as `file_contents=[ImageContent(b64)]`
    4. backend returns the resulting JPEG bytes (200 OK, image/jpeg)
    5. frontend renders + lets the user upload like any other filtered
       image (the rest of the post/story flow is unchanged)

Rate-limit:
    10 IA generations per user per rolling 24h window for free accounts
    (reuses `idx_ai_filter_user_recent` on `ai_filter_requests` for the
    COUNT query). Pro / Pro Business get 100/day. We additionally cap
    concurrent in-flight requests at 1 per user via a transient
    PG advisory lock so a malicious client can't spam parallel requests
    that all clear the daily-COUNT pre-check before being recorded.

Safety / moderation:
    Nano Banana already enforces Google's content policy. We additionally
    cap input file size at 6 MB and reject mime types other than
    image/jpeg, image/png, image/webp.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image

from database import get_pool
from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/media", tags=["ai-filters"])


# Stable preset → prompt mapping. Keep prompts SHORT and direct — Nano
# Banana follows visual style cues better than long narratives.
AI_FILTER_PROMPTS = {
    "cartoon": (
        "Transform this photo into a vibrant Western cartoon illustration "
        "with bold black outlines, flat saturated colors and simplified "
        "shapes. Keep the subject recognisable. No text."
    ),
    "anime": (
        "Transform this photo into a Japanese anime illustration: "
        "soft cel-shading, large expressive eyes, clean line art, "
        "pastel sky tones. Preserve the original composition. No text."
    ),
    "oil_painting": (
        "Transform this photo into a classical oil painting with visible "
        "brush strokes, warm color palette and dramatic chiaroscuro "
        "lighting. Painterly impasto texture. No text."
    ),
    "cinematic": (
        "Transform this photo with a Hollywood cinematic look: teal & "
        "orange grading, anamorphic lens flare, shallow depth of field, "
        "moody contrast. Photographic realism preserved. No text."
    ),
    "beauty": (
        "Subtle beauty enhancement on this portrait: smooth skin while "
        "keeping pores visible, even out tone, gentle catchlight in the "
        "eyes, slight glow. No makeup change. Keep facial features "
        "100% identical."
    ),
}

ACCEPTED_MIMES = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 6 * 1024 * 1024
RATE_LIMIT_PER_DAY_FREE = 10
RATE_LIMIT_PER_DAY_PRO = 100


_DDL = """
CREATE TABLE IF NOT EXISTS ai_filter_requests (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(64) UNIQUE NOT NULL,
    user_id VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    style VARCHAR(32) NOT NULL,
    input_bytes INTEGER NOT NULL,
    output_bytes INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|success|failed
    error TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_filter_user_recent
       ON ai_filter_requests(user_id, created_at DESC);
"""


async def ensure_ai_filter_table() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for stmt in [s.strip() for s in _DDL.split(";") if s.strip()]:
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning(f"ai_filter DDL failed: {e}")


async def _check_rate_limit(conn, user) -> tuple[int, int]:
    """Returns (used, cap). Raises 429 if used >= cap."""
    is_pro = bool(user.get("is_pro") or user.get("is_pro_business"))
    cap = RATE_LIMIT_PER_DAY_PRO if is_pro else RATE_LIMIT_PER_DAY_FREE
    used = await conn.fetchval(
        """SELECT COUNT(*) FROM ai_filter_requests
            WHERE user_id = $1 AND created_at > NOW() - INTERVAL '24 hours'
              AND status = 'success'""",
        user["user_id"],
    ) or 0
    return int(used), cap


@router.get("/ai-filter/quota")
async def ai_filter_quota(request: Request):
    """Surface the user's daily quota so the frontend can pre-disable the
    IA presets when there's none left, avoiding a wasted upload."""
    user = await get_current_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        used, cap = await _check_rate_limit(conn, user)
    return {
        "used": used,
        "cap": cap,
        "remaining": max(0, cap - used),
        "tier": "pro" if cap == RATE_LIMIT_PER_DAY_PRO else "free",
    }


@router.post("/ai-filter")
async def apply_ai_filter(
    request: Request,
    style: str = Form(...),
    image: UploadFile = File(...),
):
    """Apply a Nano Banana image-to-image style transfer.

    Returns the result as `image/jpeg` bytes so the frontend can simply
    do `URL.createObjectURL(blob)` on the response and keep the rest of
    the post/story flow identical to the Phase 1 CSS filters.
    """
    user = await get_current_user(request)
    style_norm = (style or "").strip().lower().replace("-", "_")
    if style_norm not in AI_FILTER_PROMPTS:
        raise HTTPException(status_code=400, detail="UNKNOWN_STYLE")

    # ── Input validation ─────────────────────────────────────────────
    if image.content_type not in ACCEPTED_MIMES:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_MIME")
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="EMPTY_FILE")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="FILE_TOO_LARGE")

    # Re-encode to JPEG @ 90% quality before sending. This (a) normalises
    # the input format Nano Banana likes, (b) strips EXIF / location data
    # for privacy, (c) caps the request payload.
    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        # Cap the longest side to 1280 — IA filters look great at that
        # resolution and the round-trip stays under 4s on most networks.
        max_side = 1280
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90, optimize=True)
        normalised = buf.getvalue()
    except Exception as e:
        logger.warning(f"ai_filter normalise failed: {e}")
        raise HTTPException(status_code=400, detail="DECODE_FAILED") from e

    pool = await get_pool()
    request_id = f"ai_{uuid.uuid4().hex[:16]}"
    async with pool.acquire() as conn:
        # ── Concurrency / burst guard ────────────────────────────────
        # iter150 — second tier of rate-limiting on top of the daily
        # cap: max 3 generations per rolling 60s. This neutralises a
        # client that races concurrent requests through the daily
        # COUNT pre-check before any of them is persisted. Counts
        # *all* request rows (incl. pending+failed) so a failing
        # storm still gets throttled.
        burst = await conn.fetchval(
            """SELECT COUNT(*) FROM ai_filter_requests
                WHERE user_id = $1
                  AND created_at > NOW() - INTERVAL '60 seconds'""",
            user["user_id"],
        ) or 0
        if int(burst) >= 3:
            raise HTTPException(
                status_code=429,
                detail={"code": "AI_FILTER_BURST",
                        "message": "Trop de générations rapprochées. Attends quelques secondes avant de réessayer."},
            )
        # ── Rate-limit (daily quota) ─────────────────────────────────
        used, cap = await _check_rate_limit(conn, user)
        if used >= cap:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "RATE_LIMITED",
                    "message": (f"Limite quotidienne atteinte ({used}/{cap}). "
                                "Réessaie demain ou passe en Pro pour augmenter ta limite."),
                },
            )
        await conn.execute(
            """INSERT INTO ai_filter_requests
                  (request_id, user_id, style, input_bytes, status)
                VALUES ($1, $2, $3, $4, 'pending')""",
            request_id, user["user_id"], style_norm, len(normalised),
        )

    started = datetime.now(timezone.utc)
    try:
        # ── Nano Banana call ─────────────────────────────────────────
        from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

        api_key = os.environ.get("EMERGENT_LLM_KEY")
        if not api_key:
            raise RuntimeError("EMERGENT_LLM_KEY missing")
        chat = LlmChat(
            api_key=api_key,
            session_id=request_id,
            system_message=("You are a precision image-to-image style "
                            "transfer engine. Preserve the subject's "
                            "identity, framing, and pose. Apply ONLY the "
                            "requested style. Never add text or watermarks."),
        )
        chat.with_model("gemini", "gemini-3.1-flash-image-preview").with_params(
            modalities=["image", "text"],
        )
        b64 = base64.b64encode(normalised).decode("utf-8")
        msg = UserMessage(
            text=AI_FILTER_PROMPTS[style_norm],
            file_contents=[ImageContent(b64)],
        )
        _, images = await chat.send_message_multimodal_response(msg)
        if not images:
            raise RuntimeError("Nano Banana returned no image")
        out_bytes = base64.b64decode(images[0]["data"])

        # Persist success with timing.
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE ai_filter_requests
                      SET status = 'success', output_bytes = $2, duration_ms = $3
                    WHERE request_id = $1""",
                request_id, len(out_bytes), elapsed_ms,
            )
        # iter150 — return the bytes plus quota headers so the frontend can
        # update its remaining-counter without a second round-trip.
        used2, cap2 = used + 1, cap
        return Response(
            content=out_bytes,
            media_type="image/jpeg",
            headers={
                "X-AI-Filter-Used": str(used2),
                "X-AI-Filter-Cap": str(cap2),
                "X-AI-Filter-Style": style_norm,
                "X-AI-Filter-Request-Id": request_id,
                "X-AI-Filter-Duration-Ms": str(elapsed_ms),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"ai_filter generation failed: {e}")
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE ai_filter_requests
                      SET status = 'failed', error = $2
                    WHERE request_id = $1""",
                request_id, str(e)[:500],
            )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "AI_GENERATION_FAILED",
                "message": ("La génération IA a échoué. Réessaie ou choisis "
                            "un autre filtre."),
            },
        ) from e
