"""
AI template generator — Claude Sonnet 4.5 via emergentintegrations.

Returns a structured email template JSON from a campaign brief :
  {goal, audience, tone, language, cta_type, extra_context?}
→ {subject, preview_text, body_html, body_text, cta_label}
"""
from __future__ import annotations
import json
import logging
import os
import re
import uuid
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT_FR = """Tu es un expert en copywriting d'email marketing B2C pour une super-app
africaine (JAPAP : messagerie, appels vidéo, wallet crypto, Wi-Fi Connect).
Ton rôle : générer des templates d'email courts, actionnables, mobile-first,
qui respectent la voix de marque JAPAP (chaleureuse, directe, sans jargon).

RÈGLES IMPERATIVES :
1. Réponds UNIQUEMENT en JSON strict, pas de markdown, pas de texte libre.
2. Structure exacte :
   {
     "subject": "…",           // 60 caractères max, peut inclure un emoji
     "preview_text": "…",      // 100 caractères max, complémentaire au subject
     "body_html": "…",         // HTML inline (balises <p>, <ul>, <li>, <strong>), 600 mots max
     "body_text": "…",         // version plaintext, même message
     "cta_label": "…"          // 3-5 mots, verbe d'action
   }
3. Tu peux utiliser ces placeholders (encadrés de doubles accolades) :
   {{first_name}}, {{email}}, {{wallet_balance}}, {{referral_count}},
   {{pending_tasks}}, {{last_active_days}}, {{plan_name}}, {{country}},
   {{connect_points}}, {{app_url}}.
4. PAS d'inline CSS lourd. PAS d'images. PAS de <html>/<body> — juste le
   contenu du body.
5. Pas de surpromesse, pas de majuscules bloquées, pas de points d'exclamation multiples.
"""

_SYSTEM_PROMPT_EN = """You are an expert email copywriter for JAPAP, an African super-app
(messaging, video calls, crypto wallet, Wi-Fi Connect).
Generate short, action-oriented, mobile-first email templates. Brand voice:
warm, direct, no jargon.

STRICT RULES:
1. Respond ONLY in strict JSON, no markdown, no prose.
2. Schema:
   { "subject": "...", "preview_text": "...", "body_html": "...",
     "body_text": "...", "cta_label": "..." }
3. You may use placeholders: {{first_name}}, {{email}}, {{wallet_balance}},
   {{referral_count}}, {{pending_tasks}}, {{last_active_days}}, {{plan_name}},
   {{country}}, {{connect_points}}, {{app_url}}.
4. Body HTML: only <p>, <ul>, <li>, <strong>. No <html>/<body>.
5. No ALL-CAPS, no multiple exclamation marks.
"""


def _sanitize_json(raw: str) -> str:
    """LLMs sometimes wrap JSON in ```json ... ``` even with strict instructions.
    Strip fences if present and return the outermost object."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start:end + 1]
    return raw.strip()


async def generate_template(*, goal: str, audience: str = "", tone: str = "warm",
                            language: str = "fr", cta_type: str = "primary",
                            extra_context: str = "") -> dict:
    """Call Claude Sonnet 4.5 with a structured brief → parsed template JSON."""
    api_key = os.environ.get("EMERGENT_LLM_KEY", "").strip()
    if not api_key:
        raise RuntimeError("EMERGENT_LLM_KEY is not configured")

    system = _SYSTEM_PROMPT_FR if language.lower().startswith("fr") else _SYSTEM_PROMPT_EN
    brief = (
        f"Brief de campagne :\n"
        f"- Objectif : {goal}\n"
        f"- Audience : {audience or 'tous les utilisateurs'}\n"
        f"- Ton : {tone}\n"
        f"- Langue : {language}\n"
        f"- Type de CTA : {cta_type}\n"
    )
    if extra_context:
        brief += f"- Contexte additionnel : {extra_context}\n"
    brief += "\nGénère l'email en JSON strict."

    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"msg_ai_{uuid.uuid4().hex[:10]}",
            system_message=system,
        )
        .with_model("anthropic", "claude-sonnet-4-5-20250929")
    )
    try:
        response_text = await chat.send_message(UserMessage(text=brief))
    except Exception as e:
        logger.exception("AI template generation call failed")
        raise RuntimeError(f"LLM call failed: {e}")

    cleaned = _sanitize_json(response_text or "")
    try:
        parsed = json.loads(cleaned)
    except Exception as e:
        logger.warning("AI template JSON parse failed. raw=%r", response_text[:300])
        raise RuntimeError(f"LLM returned non-JSON: {e}")

    # Validate shape + clip lengths
    required = ("subject", "preview_text", "body_html", "body_text", "cta_label")
    for k in required:
        if k not in parsed:
            raise RuntimeError(f"LLM response missing field: {k}")
    out = {
        "subject": str(parsed["subject"])[:200].strip(),
        "preview_text": str(parsed["preview_text"])[:160].strip(),
        "body_html": str(parsed["body_html"]).strip(),
        "body_text": str(parsed["body_text"]).strip(),
        "cta_label": str(parsed["cta_label"])[:80].strip(),
    }
    return out
