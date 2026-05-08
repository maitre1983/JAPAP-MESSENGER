"""
AI Assistant — text improvement via Emergent LLM Key (Claude Sonnet 4.5).
=========================================================================
Powers the "✨ Améliorer avec IA" button in the feed composer.
Actions: suggest | correct | rephrase | generate
Kept intentionally tiny and low-latency: prompts are <500 tokens, responses
capped via system instructions to remain short-form.
"""
import logging
import os
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Literal

from routes.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])

_EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")

ACTIONS = {
    "suggest": (
        "Tu es un assistant d'écriture pour un réseau social. "
        "Propose une version améliorée, plus accrocheuse et courte (≤ 280 caractères) "
        "du texte fourni. Garde le ton et la langue d'origine. "
        "Réponds uniquement avec le texte final, sans préambule ni guillemets."
    ),
    "correct": (
        "Tu es un correcteur orthographique et grammatical. "
        "Corrige les fautes d'orthographe, de grammaire et de ponctuation du texte fourni. "
        "Garde le sens, le ton et la langue d'origine. "
        "Réponds uniquement avec le texte corrigé, sans commentaire."
    ),
    "rephrase": (
        "Tu es un assistant d'écriture. Reformule le texte fourni de manière plus fluide "
        "et naturelle tout en conservant le sens exact et la langue d'origine. "
        "Reste court (≤ 280 caractères). Réponds uniquement avec la reformulation."
    ),
    "generate": (
        "Tu es un assistant d'écriture pour un réseau social. "
        "À partir du sujet ou mot-clé fourni, génère un post engageant, authentique et court "
        "(≤ 280 caractères) dans la langue dominante du prompt (français par défaut). "
        "Ajoute 1-2 emojis pertinents si approprié. "
        "Réponds uniquement avec le post final, sans préambule."
    ),
}


class ImproveRequest(BaseModel):
    text: str
    action: Literal["suggest", "correct", "rephrase", "generate"]


@router.post("/improve-text")
async def improve_text(req: ImproveRequest, request: Request):
    user = await get_current_user(request)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Le texte est requis.")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Texte trop long (max 2000 caractères).")
    if not _EMERGENT_KEY:
        raise HTTPException(status_code=503, detail="IA indisponible — clé non configurée.")

    system_message = ACTIONS[req.action]
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=_EMERGENT_KEY,
            session_id=f"ai_improve_{user['user_id']}_{req.action}",
            system_message=system_message,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")
        reply = await chat.send_message(UserMessage(text=text))
        improved = (reply or "").strip().strip('"').strip()
        if not improved:
            raise HTTPException(status_code=502, detail="Réponse IA vide.")
        return {"action": req.action, "improved": improved}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"AI improve-text error: {e}")
        raise HTTPException(status_code=502, detail=f"Erreur IA : {e}")
