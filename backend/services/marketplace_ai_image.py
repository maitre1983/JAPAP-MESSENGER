"""
iter181 — Marketplace AI Image Generation/Enhancement (Nano Banana)
====================================================================
3 modes (tous pilotés par emergentintegrations + EMERGENT_LLM_KEY) :

  • generate(prompt)             → image depuis un texte libre (fiche produit pro)
  • enhance(bytes, instruction)  → améliore photo existante (net, lumière studio)
  • bg_swap(bytes, scene_preset) → remplace l'arrière-plan par une scène ciblée

Retourne toujours `bytes` PNG/JPEG que le pipeline marketplace existant
re-compresse en 3 formats (thumb/full/hd).

IMPORTANT :
  - Aucun hardcoding : modèle, presets et quotas viennent de settings_service.
  - Fallback gracieux : toute erreur → `raise AIImageError(reason)` qui est
    catchée par la route et renvoie 502 — jamais 500. L'upload manuel reste
    toujours possible pour le vendeur.
  - Jamais de log base64 complet (CEO — context window).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class AIImageError(Exception):
    """Raised when Nano Banana call fails or returns no image."""
    pass


def _model_id() -> str:
    # Live-read, zero hardcode. Falls back to the latest Nano Banana model id.
    return os.environ.get("MKT_AI_MODEL") or "gemini-3.1-flash-image-preview"


async def _call_nano_banana(prompt: str, ref_image_bytes: Optional[bytes] = None,
                              *, timeout_s: float = 30.0) -> bytes:
    """Low-level wrapper around emergentintegrations LlmChat for
    image generation / editing. Returns raw image bytes (PNG/JPEG)."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise AIImageError("EMERGENT_LLM_KEY missing — AI image generation disabled.")

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    except Exception as e:
        raise AIImageError(f"emergentintegrations unavailable: {e}")

    import uuid as _uuid
    chat = LlmChat(
        api_key=api_key,
        session_id=f"mkt-ai-{_uuid.uuid4().hex[:12]}",
        system_message=("You are a professional e-commerce product photographer."
                        " Always return a single high-quality product image,"
                        " clean composition, no watermark, no text overlay."),
    )
    chat.with_model("gemini", _model_id()).with_params(modalities=["image", "text"])

    kwargs = {"text": prompt}
    if ref_image_bytes:
        b64 = base64.b64encode(ref_image_bytes).decode("utf-8")
        kwargs["file_contents"] = [ImageContent(b64)]
    msg = UserMessage(**kwargs)

    try:
        text, images = await asyncio.wait_for(
            chat.send_message_multimodal_response(msg), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise AIImageError("Nano Banana timeout — réessaie dans un instant.")
    except Exception as e:
        # Do NOT log raw base64 / full request body. Short message only.
        logger.warning(f"[mkt-ai] Nano Banana error: {type(e).__name__}: {str(e)[:200]}")
        raise AIImageError(f"Nano Banana error: {type(e).__name__}")

    if not images:
        # text is a short safety message from Gemini (e.g. "can't generate")
        preview = (text or "").strip()[:160]
        raise AIImageError(f"Aucune image générée. {preview}".strip())

    first = images[0]
    try:
        return base64.b64decode(first["data"])
    except Exception as e:
        raise AIImageError(f"Invalid image payload: {e}")


# ─────────────────────────── 3 PUBLIC FLOWS ───────────────────────────
_GENERATE_TEMPLATE = (
    "Create a professional e-commerce product photo of: {prompt}."
    " Soft studio lighting, clean white seamless background, 45-degree angle,"
    " sharp details, photorealistic, no text or logo overlays."
)


async def generate_from_prompt(prompt: str) -> bytes:
    prompt = (prompt or "").strip()
    if len(prompt) < 5:
        raise AIImageError("Prompt trop court (min 5 caractères).")
    if len(prompt) > 500:
        prompt = prompt[:500]
    full = _GENERATE_TEMPLATE.format(prompt=prompt)
    return await _call_nano_banana(full)


_ENHANCE_TEMPLATE = (
    "Enhance this product photo for an e-commerce catalog."
    " Improve sharpness, color balance, and lighting. Keep the product"
    " identical (same shape, colors, branding). Remove noise and shadows,"
    " make the background cleaner. {extra}"
)


async def enhance_image(image_bytes: bytes, extra_instruction: str = "") -> bytes:
    if not image_bytes or len(image_bytes) < 1024:
        raise AIImageError("Image d'entrée invalide.")
    extra = (extra_instruction or "").strip()[:200]
    prompt = _ENHANCE_TEMPLATE.format(extra=extra).strip()
    return await _call_nano_banana(prompt, ref_image_bytes=image_bytes)


# Presets live-read via settings (see settings_service default) but we ship
# reasonable defaults so the feature works out-of-the-box.
DEFAULT_BG_PRESETS = {
    "studio_white":  "plain pure-white seamless studio background",
    "studio_black":  "plain pure-black seamless studio background",
    "lifestyle":     "warm minimal lifestyle interior, wooden table, soft daylight",
    "outdoor":       "outdoor scene, soft natural light, shallow depth of field",
    "luxury":        "luxury velvet display surface, dramatic rim light, dark gradient",
    "marble":        "clean white marble countertop with soft window light",
}

_BG_TEMPLATE = (
    "Replace the background of this product image with: {scene}."
    " Keep the product EXACTLY the same (shape, colors, branding, proportions)."
    " Ensure clean separation, realistic shadow, photorealistic integration."
)


async def bg_swap(image_bytes: bytes, scene: str) -> bytes:
    if not image_bytes or len(image_bytes) < 1024:
        raise AIImageError("Image d'entrée invalide.")
    scene = (scene or "").strip()
    if not scene:
        raise AIImageError("Scène vide.")
    prompt = _BG_TEMPLATE.format(scene=scene)
    return await _call_nano_banana(prompt, ref_image_bytes=image_bytes)
