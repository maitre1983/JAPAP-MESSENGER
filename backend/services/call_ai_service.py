"""
Call AI service — Sprint D (transcription + summary).

Uses the Emergent LLM Key for:
    - OpenAI Whisper → audio transcription (fr/en/other)
    - Claude Sonnet  → structured summary (key points, decisions, action items)

Pipeline (called asynchronously after a recording finalizes) :
    1. Fetch audio from R2 (or stream directly from Egress URL)
    2. Whisper → full transcript text
    3. Claude → JSON { summary, key_points[], decisions[], action_items[] }
    4. Persist in call_summaries table
"""
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class CallAIConfigError(Exception):
    """Emergent LLM key not available."""


def _emergent_key() -> str:
    key = os.environ.get("EMERGENT_LLM_KEY", "")
    if not key:
        raise CallAIConfigError("EMERGENT_LLM_KEY manquante dans l'environnement.")
    return key


async def transcribe_audio(audio_url: str, language: str = "fr") -> str:
    """Download a remote audio file (R2 presigned URL) to a temp file and run
    it through OpenAI Whisper via emergentintegrations (universal key).

    Notes :
        - emergentintegrations' STT wrapper requires a LOCAL file path, not a
          URL — we therefore stream the object to a tempfile.
        - LiveKit Egress produces mp4 (video+audio) which Whisper accepts.
        - Whisper caps at 25 MB per request; very long calls need chunking
          (deferred to a follow-up iteration).
    """
    try:
        from emergentintegrations.llm.openai.speech_to_text import OpenAISpeechToText  # type: ignore
    except ImportError:
        raise CallAIConfigError(
            "emergentintegrations non installé. `pip install emergentintegrations "
            "--extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/`"
        )
    import httpx, tempfile, os as _os
    # Stream-download to a tempfile (guess ext from URL path — default .mp4)
    ext = ".mp4"
    for cand in (".mp4", ".m4a", ".mp3", ".wav", ".webm"):
        if cand in audio_url.lower():
            ext = cand
            break
    tmp_path = tempfile.mkstemp(suffix=ext)[1]
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", audio_url) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                        fh.write(chunk)
        size = _os.path.getsize(tmp_path)
        if size > 25 * 1024 * 1024:
            raise CallAIConfigError(
                f"Audio trop volumineux pour Whisper ({size // (1024*1024)} MB > 25 MB)."
                " Découpage nécessaire (à implémenter)."
            )
        stt = OpenAISpeechToText(api_key=_emergent_key())
        with open(tmp_path, "rb") as audio_fh:
            resp = await stt.transcribe(
                file=audio_fh,
                model="whisper-1",
                response_format="text",
                language=language or None,
            )
        # LiteLLM returns either a string (response_format='text') or an object with .text
        if isinstance(resp, str):
            return resp.strip()
        return (getattr(resp, "text", None) or str(resp)).strip()
    finally:
        try: _os.unlink(tmp_path)
        except Exception: pass


async def summarize_transcript(transcript: str, language: str = "fr") -> dict[str, Any]:
    """Structured summarization via Claude Sonnet.
    Output shape :
        {
          "summary": "…",
          "key_points": ["…", "…"],
          "decisions": ["…"],
          "action_items": [{"who": "…", "what": "…", "due": "…"}],
        }
    """
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
    except ImportError:
        raise CallAIConfigError("emergentintegrations non installé.")

    system = (
        "Tu es un assistant qui synthétise des conversations audio (appels). "
        "Réponds STRICTEMENT en JSON valide avec les clés suivantes :\n"
        "  summary       : string, 2-4 phrases, dans la langue de la conversation\n"
        "  key_points    : array de strings (max 6)\n"
        "  decisions     : array de strings (ce qui a été décidé — vide si rien)\n"
        "  action_items  : array d'objets {who, what, due} (due est optionnel)\n"
        f"Langue de sortie : {language}.\n"
        "Si la transcription est vide ou incompréhensible, renvoie des champs vides."
    )
    chat = LlmChat(
        api_key=_emergent_key(),
        session_id=f"call_summary_{os.urandom(4).hex()}",
        system_message=system,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    msg = UserMessage(text=f"Transcript à résumer (brut) :\n\n{transcript[:20000]}")
    raw = await chat.send_message(msg)
    # Extract JSON even if wrapped in ```json fences
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Claude returned non-JSON summary, returning raw.")
        return {
            "summary": text[:2000],
            "key_points": [], "decisions": [], "action_items": [],
        }
    return {
        "summary": str(parsed.get("summary", ""))[:2000],
        "key_points": list(parsed.get("key_points", []))[:6],
        "decisions": list(parsed.get("decisions", []))[:10],
        "action_items": list(parsed.get("action_items", []))[:10],
    }


async def run_call_ai_pipeline(*, recording_id: str, summary_id: str | None = None) -> dict[str, Any]:
    """End-to-end orchestrator invoked from a background task after a recording
    finalizes. Updates call_summaries row in place. Returns the final state.

    If `summary_id` is provided (record/stop pre-creates it), we update that
    row; otherwise we create a fresh row. This keeps the polling UX smooth
    (no 'none' flash between record/stop and the first status update).
    """
    from database import get_pool
    from services.r2_storage_service import generate_presigned_get_url
    pool = await get_pool()
    async with pool.acquire() as conn:
        rec = await conn.fetchrow(
            "SELECT recording_id, session_id, storage_key, storage_provider "
            "FROM call_recordings WHERE recording_id = $1", recording_id,
        )
        if not rec:
            return {"status": "missing", "recording_id": recording_id}
        session_id = rec['session_id']
        if summary_id:
            # Pre-created row (happy path) — just mark it 'transcribing'
            await conn.execute(
                "UPDATE call_summaries SET status = 'transcribing' WHERE summary_id = $1",
                summary_id,
            )
        else:
            import uuid
            summary_id = f"sum_{uuid.uuid4().hex[:16]}"
            await conn.execute("""
                INSERT INTO call_summaries (summary_id, session_id, recording_id, status)
                VALUES ($1, $2, $3, 'transcribing')
                ON CONFLICT (summary_id) DO NOTHING
            """, summary_id, session_id, recording_id)
    # Generate a short-lived URL Whisper can download from
    try:
        audio_url = await generate_presigned_get_url(rec['storage_key'], expires_in=3600)
    except Exception as e:
        await _mark_failed(summary_id, f"R2 presign error: {e}")
        raise
    # 1) Transcribe
    try:
        transcript = await transcribe_audio(audio_url)
    except Exception as e:
        await _mark_failed(summary_id, f"Whisper: {e}")
        raise
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE call_summaries SET transcript = $1, status = 'summarizing' WHERE summary_id = $2",
            transcript, summary_id,
        )
    # 2) Summarize
    try:
        structured = await summarize_transcript(transcript)
    except Exception as e:
        await _mark_failed(summary_id, f"Claude: {e}")
        raise
    from datetime import datetime, timezone
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE call_summaries SET
              summary = $1, key_points = $2::jsonb, decisions = $3::jsonb,
              action_items = $4::jsonb, status = 'ready',
              model = $5, completed_at = $6
            WHERE summary_id = $7
        """,
            structured["summary"],
            json.dumps(structured["key_points"]),
            json.dumps(structured["decisions"]),
            json.dumps(structured["action_items"]),
            "claude-sonnet-4-5",
            datetime.now(timezone.utc),
            summary_id,
        )
    return {"status": "ready", "summary_id": summary_id, "session_id": session_id}


async def _mark_failed(summary_id: str, err: str) -> None:
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE call_summaries SET status = 'failed', error_msg = $1 WHERE summary_id = $2",
            err[:500], summary_id,
        )
