"""
JAPAP — KYC AI pre-validation service (iter172, P0)
====================================================
Hybrid AI + heuristic pre-screen for KYC submissions.

LOCAL CHECKS (instant, no LLM call):
  • blur detection — Laplacian variance threshold via Pillow + numpy
  • dimensions / file size sanity
  • dominant aspect ratio plausibility

REMOTE CHECKS (one Gemini 2.5-Flash call per submission, ~1s):
  • face detected on selfie (yes/no)
  • document detected on ID photo (yes/no)
  • document type visibility match
  • OCR fields if available (name, id_number, expiry — never trusted blindly)
  • coherence score selfie-vs-id (qualitative)

Returns a `risk_score` in {low, medium, high} + `alerts` list.
**The AI NEVER auto-approves.** The result is shown alongside the human
admin review and is informational only.
"""
import asyncio
import base64
import io
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy imports — Pillow / numpy are heavy.
try:
    from PIL import Image, ImageFilter  # noqa
    import numpy as np
    _PIL_OK = True
except Exception as e:  # pragma: no cover — dependencies are installed
    _PIL_OK = False
    logger.warning(f"[kyc-ai] Pillow/numpy unavailable: {e}")

# Empirical thresholds — tuned for phone-camera phone-uploaded images.
BLUR_THRESHOLD = 60.0       # Laplacian variance < 60 → likely blurry
MIN_DIMENSION_PX = 400      # below this any document is unreadable
MAX_DIMENSION_PX = 8000     # above is suspicious / probably an oversize
MIN_FILE_BYTES = 30 * 1024  # < 30KB is probably a fake/screenshot
LLM_TIMEOUT_S = 12          # cap LLM call so submit never hangs forever


def _laplacian_variance(image_bytes: bytes) -> float:
    """Returns Laplacian variance — proxy for image sharpness. The lower
    the variance, the blurrier the image. Returns 0.0 on failure."""
    if not _PIL_OK:
        return 0.0
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        # downsample very large images for speed (we only need texture stats)
        if max(img.size) > 1600:
            img.thumbnail((1600, 1600))
        arr = np.asarray(img, dtype=np.float32)
        # Manual 3x3 Laplacian via shifted neighbours — avoids importing scipy.
        padded = np.pad(arr, 1, mode="edge")
        out = (
            padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2]
            + padded[1:-1, 2:] + (-4 * padded[1:-1, 1:-1])
        )
        return float(out.var())
    except Exception as e:
        logger.warning(f"[kyc-ai] laplacian_variance failed: {e}")
        return 0.0


def _local_check(image_bytes: bytes, label: str) -> dict:
    """Returns {ok, blur_variance, width, height, alerts:[...]}."""
    out = {
        "label": label,
        "ok": True, "blur_variance": 0.0,
        "width": 0, "height": 0, "size_kb": len(image_bytes) // 1024,
        "alerts": [],
    }
    if len(image_bytes) < MIN_FILE_BYTES:
        out["ok"] = False
        out["alerts"].append(f"Fichier trop petit ({out['size_kb']} KB) — probablement non lisible")
    if _PIL_OK:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            out["width"], out["height"] = img.size
            if min(img.size) < MIN_DIMENSION_PX:
                out["alerts"].append("Image trop basse résolution (texte risque d'être illisible)")
                out["ok"] = False
            if max(img.size) > MAX_DIMENSION_PX:
                out["alerts"].append("Image anormalement grande — vérifier authenticité")
        except Exception as e:
            out["ok"] = False
            out["alerts"].append(f"Image illisible: {e}")
            return out
    var = _laplacian_variance(image_bytes)
    out["blur_variance"] = round(var, 1)
    if var < BLUR_THRESHOLD and var > 0:
        out["alerts"].append(f"Image floue (netteté={out['blur_variance']:.0f} < {BLUR_THRESHOLD:.0f})")
        out["ok"] = False
    return out


_AI_PROMPT = """Tu es un expert KYC. Analyse les images jointes et réponds UNIQUEMENT avec un objet JSON valide (pas de markdown, pas de texte avant/après) avec les champs:

{
  "id_image": {
    "document_detected": bool,         // un document d'identité est-il visible ?
    "document_type_match": bool,       // le type correspond-il à "{id_type}" ? (cni / passport / permis)
    "name_visible": string|null,       // nom complet lisible ou null
    "id_number_visible": string|null,  // numéro lisible ou null (masque les caractères incertains avec ?)
    "expiry_visible": string|null,     // date d'expiration lisible ou null
    "alerts": [string]                 // liste courte de problèmes (français)
  },
  "selfie": {
    "face_detected": bool,
    "holding_id": bool,                // la personne tient-elle visiblement un document ?
    "alerts": [string]
  },
  "coherence": {
    "selfie_id_match_likely": bool,    // le visage du selfie ressemble-t-il à la photo du document ?
    "confidence": "low"|"medium"|"high",
    "alerts": [string]
  },
  "risk_score": "low"|"medium"|"high",
  "global_alerts": [string]            // alertes globales (ex: documents différents)
}

Pour {id_type}={id_type_label}. Sois strict mais pas paranoïaque. JSON UNIQUEMENT."""


def _strip_json(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from a model response that
    may include surrounding text or fenced markdown."""
    if not text:
        return None
    # Try direct parse first.
    try:
        return json.loads(text)
    except Exception:
        pass
    # Strip markdown fences.
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # Greedy curly match.
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


async def _gemini_kyc_audit(*, id_image_bytes: bytes,
                             selfie_bytes: bytes,
                             id_back_bytes: Optional[bytes],
                             id_type: str) -> dict:
    """Single Gemini 2.5-Flash call with all KYC images attached.

    Returns the parsed JSON or `{}` on any failure (NEVER raises).
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return {}
    try:
        from emergentintegrations.llm.chat import (
            LlmChat, UserMessage, ImageContent,
        )
    except Exception as e:
        logger.warning(f"[kyc-ai] emergentintegrations missing: {e}")
        return {}

    type_label = {
        "passport": "passeport",
        "national_id": "carte d'identité nationale (CNI)",
        "drivers_license": "permis de conduire",
    }.get(id_type, id_type)

    prompt = _AI_PROMPT.replace("{id_type}", id_type).replace(
        "{id_type_label}", type_label)

    contents = [
        ImageContent(image_base64=base64.b64encode(id_image_bytes).decode()),
        ImageContent(image_base64=base64.b64encode(selfie_bytes).decode()),
    ]
    if id_back_bytes:
        contents.insert(
            1, ImageContent(image_base64=base64.b64encode(id_back_bytes).decode()))

    chat = LlmChat(
        api_key=api_key, session_id=f"kyc_audit_{os.urandom(4).hex()}",
        system_message="Tu es un agent de pré-validation KYC. Sois précis et concis."
    ).with_model("gemini", "gemini-2.5-flash")

    msg = UserMessage(text=prompt, file_contents=contents)
    try:
        text = await asyncio.wait_for(chat.send_message(msg), timeout=LLM_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("[kyc-ai] Gemini timeout — falling back to local-only")
        return {}
    except Exception as e:
        logger.warning(f"[kyc-ai] Gemini call failed: {e}")
        return {}

    parsed = _strip_json(text or "")
    if not isinstance(parsed, dict):
        logger.warning(f"[kyc-ai] failed to parse JSON: {text[:200] if text else '<empty>'}")
        return {}
    return parsed


def _aggregate_risk(local_id: dict, local_back: Optional[dict],
                    local_selfie: dict, ai: dict) -> tuple[str, list[str]]:
    alerts: list[str] = []
    alerts.extend([f"Pièce: {a}" for a in local_id.get("alerts", [])])
    if local_back:
        alerts.extend([f"Verso: {a}" for a in local_back.get("alerts", [])])
    alerts.extend([f"Selfie: {a}" for a in local_selfie.get("alerts", [])])

    if ai:
        for f in ("id_image", "selfie", "coherence"):
            for a in (ai.get(f, {}) or {}).get("alerts", []):
                alerts.append(a)
        for a in ai.get("global_alerts", []):
            alerts.append(a)

    # Risk computation: any local hard-fail → high. Any AI flag (no doc/no
    # face/no match) → high. Some softer alerts → medium.
    has_hard_fail = (
        not local_id.get("ok") or not local_selfie.get("ok")
        or (local_back and not local_back.get("ok"))
    )
    ai_id = (ai or {}).get("id_image", {})
    ai_self = (ai or {}).get("selfie", {})
    ai_coh = (ai or {}).get("coherence", {})

    if not ai_id.get("document_detected", True):
        alerts.append("Document non détecté par l'IA")
        has_hard_fail = True
    if not ai_self.get("face_detected", True):
        alerts.append("Visage non détecté sur le selfie")
        has_hard_fail = True
    if ai_coh and ai_coh.get("selfie_id_match_likely") is False:
        alerts.append("Possible incohérence selfie ↔ pièce d'identité")
        has_hard_fail = True

    # Trust the AI's own risk_score as a hint — but always escalate from
    # local hard-fail.
    ai_risk = ai.get("risk_score") if ai else None
    if has_hard_fail:
        risk = "high"
    elif ai_risk in ("medium", "high"):
        risk = ai_risk
    elif alerts:
        risk = "medium"
    else:
        risk = "low"

    # de-dupe while preserving order
    seen = set()
    deduped: list[str] = []
    for a in alerts:
        if a and a not in seen:
            seen.add(a)
            deduped.append(a)
    return risk, deduped


async def analyze_kyc(*, id_image_bytes: bytes, selfie_bytes: bytes,
                      id_back_bytes: Optional[bytes] = None,
                      id_type: str = "national_id") -> dict:
    """Public entry — returns a JSON-serialisable audit summary.

    Shape:
      {
        "risk_score": "low"|"medium"|"high",
        "alerts": [str],
        "local": {id, back?, selfie},
        "ai": {raw Gemini JSON or {}},
        "version": "iter172",
      }
    """
    local_id = _local_check(id_image_bytes, "id")
    local_back = _local_check(id_back_bytes, "id_back") if id_back_bytes else None
    local_selfie = _local_check(selfie_bytes, "selfie")

    ai_payload = await _gemini_kyc_audit(
        id_image_bytes=id_image_bytes, selfie_bytes=selfie_bytes,
        id_back_bytes=id_back_bytes, id_type=id_type,
    )

    risk, alerts = _aggregate_risk(local_id, local_back, local_selfie, ai_payload)

    return {
        "version": "iter172",
        "risk_score": risk,
        "alerts": alerts,
        "local": {
            "id": local_id, "id_back": local_back, "selfie": local_selfie,
        },
        "ai": ai_payload,
    }
