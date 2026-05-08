"""
Quiz AI Validator — iter232.
============================
Second-pass validation of AI-generated questions via Claude.
Each question is scored 0-100 across: factual_accuracy, clarity,
linguistic_quality, difficulty_match. The aggregated `confidence` (mean)
gates insertion: < CONFIDENCE_MIN → rejected, > = MIN → accepted.

Stats are persisted in `quiz_ai_validation_stats` so the admin pool widget
can surface accept/reject ratios.
"""
from __future__ import annotations
import json
import logging
import os
import random
import uuid
from typing import Any, List

logger = logging.getLogger(__name__)

CONFIDENCE_MIN = 80  # questions below this are rejected.

_VALIDATION_PROMPT = """Tu es un correcteur expert en quiz pédagogique francophone pour un public africain adulte.
Évalue chacune de ces questions sur 4 critères, sur une échelle 0 à 100 :
1. **factual_accuracy** : la bonne réponse est-elle factuellement correcte ? (le critère le plus important)
2. **clarity** : la question et les options sont-elles claires, sans ambiguïté ?
3. **linguistic_quality** : orthographe et grammaire françaises correctes ?
4. **difficulty_match** : la difficulté annoncée (easy/medium/hard) est-elle cohérente ?

Réponds STRICTEMENT par un tableau JSON de la même longueur que l'entrée, dans le MÊME ORDRE.
Pour chaque question, retourne un objet :
{{
  "factual_accuracy": <0-100>,
  "clarity": <0-100>,
  "linguistic_quality": <0-100>,
  "difficulty_match": <0-100>,
  "rejection_reason": "<court motif si confidence < 80, sinon vide>"
}}

Questions à évaluer :
{batch}
"""


def _confidence(scores: dict) -> int:
    """Average of the 4 sub-scores, weighted: factual_accuracy ×2."""
    fa = int(scores.get("factual_accuracy", 0))
    cl = int(scores.get("clarity", 0))
    lg = int(scores.get("linguistic_quality", 0))
    df = int(scores.get("difficulty_match", 0))
    return round((2 * fa + cl + lg + df) / 5)


async def validate_questions(questions: List[dict],
                              model: str = "claude-sonnet-4-5-20250929"
                              ) -> List[dict]:
    """Annotate each question with `_validation` = {confidence, notes}.
    Returns the same list (in-place) — does NOT filter; caller decides.
    On LLM failure, falls back to confidence=0 (everything rejected) so
    we never insert un-validated content."""
    if not questions:
        return []
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        logger.warning("[quiz-validate] EMERGENT_LLM_KEY missing → reject all")
        for q in questions:
            q["_validation"] = {"confidence": 0, "notes": "no api key"}
        return questions

    # Compact representation passed to the LLM (don't leak internal fields).
    batch = [
        {"text": q["text"], "options": q["options"],
         "correct_index": q["correct_index"], "difficulty": q.get("difficulty")}
        for q in questions
    ]
    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"quiz-validate-{random.randint(1000, 99999)}",
            system_message=(
                "Tu es un correcteur sévère. Tu privilégies l'exactitude factuelle."
            ),
        ).with_model("anthropic", model)
    )
    try:
        raw = await chat.send_message(
            UserMessage(text=_VALIDATION_PROMPT.format(batch=json.dumps(batch, ensure_ascii=False)))
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[quiz-validate] LLM error: %s — rejecting all", e)
        for q in questions:
            q["_validation"] = {"confidence": 0, "notes": f"llm error: {e}"}
        return questions

    raw = (raw or "").strip()
    i, j = raw.find("["), raw.rfind("]")
    if i == -1 or j == -1:
        logger.warning("[quiz-validate] no JSON in response — rejecting all")
        for q in questions:
            q["_validation"] = {"confidence": 0, "notes": "no json in validator response"}
        return questions
    try:
        parsed = json.loads(raw[i:j + 1])
    except json.JSONDecodeError as e:
        logger.warning("[quiz-validate] bad JSON: %s — rejecting all", e)
        for q in questions:
            q["_validation"] = {"confidence": 0, "notes": f"bad json: {e}"}
        return questions

    # Align by index. If the validator returned fewer/more rows, fall back
    # to confidence=0 for orphans so we never accept un-validated rows.
    for idx, q in enumerate(questions):
        if idx >= len(parsed) or not isinstance(parsed[idx], dict):
            q["_validation"] = {"confidence": 0, "notes": "validator returned no row"}
            continue
        score_obj = parsed[idx]
        conf = _confidence(score_obj)
        notes_parts = [f"{k}={score_obj.get(k)}" for k in
                       ("factual_accuracy", "clarity", "linguistic_quality", "difficulty_match")]
        rej = (score_obj.get("rejection_reason") or "").strip()
        if rej:
            notes_parts.append(f"rejet: {rej}")
        q["_validation"] = {
            "confidence": conf,
            "notes": " | ".join(notes_parts),
        }
    return questions


def split_accepted(questions: List[dict]) -> tuple[List[dict], List[dict]]:
    """Partition validated questions into (accepted, rejected) based on
    confidence ≥ CONFIDENCE_MIN."""
    accepted, rejected = [], []
    for q in questions:
        v = q.get("_validation") or {}
        if int(v.get("confidence", 0)) >= CONFIDENCE_MIN:
            accepted.append(q)
        else:
            rejected.append(q)
    return accepted, rejected


async def record_validation_stats(conn, *, category: str,
                                   total_generated: int,
                                   accepted: List[dict],
                                   rejected: List[dict]) -> str:
    """Persist a row in `quiz_ai_validation_stats` for the admin dashboard."""
    batch_id = f"qvs_{uuid.uuid4().hex[:18]}"
    avg_conf = (
        sum(int((q.get("_validation") or {}).get("confidence", 0)) for q in (accepted + rejected))
        / max(1, len(accepted) + len(rejected))
    )
    rejection_reasons: dict[str, int] = {}
    for q in rejected:
        notes = (q.get("_validation") or {}).get("notes", "")
        # Bucket by short reason — anything after "rejet:" up to first '|'
        if "rejet:" in notes:
            short = notes.split("rejet:", 1)[1].split("|", 1)[0].strip()[:80]
            rejection_reasons[short] = rejection_reasons.get(short, 0) + 1
    await conn.execute(
        """INSERT INTO quiz_ai_validation_stats
            (batch_id, category, total_generated, accepted, rejected,
             avg_confidence, rejection_reasons, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())""",
        batch_id, category, total_generated, len(accepted), len(rejected),
        round(avg_conf, 2), json.dumps(rejection_reasons, ensure_ascii=False),
    )
    return batch_id


__all__ = ["validate_questions", "split_accepted", "record_validation_stats",
           "CONFIDENCE_MIN"]
