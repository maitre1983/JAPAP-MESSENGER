"""Retry script using a different LLM provider (OpenAI) via the same
universal key. Used for languages where Claude keeps returning 502.
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_i18n_bundles import (  # noqa: E402
    _api_key, _sanitize, _validate_same_shape, FRONTEND_LOCALES, SOURCE, TARGETS
)
from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore


async def translate_with_openai(api_key, code, name, note, source_json):
    system = (
        "You are a professional product-localization engineer for JAPAP, a mobile-first "
        "messenger super-app. Return STRICT JSON only — no prose, no code fences. "
        "Preserve the exact tree structure and every key from the source. "
        "Keep the product name 'JAPAP' as-is. Preserve {placeholders}. Keep emojis. "
        "Produce natural, concise, professional translations for mobile UI."
    )
    user_msg = (
        f"Translate this JAPAP UI string bundle from English into {name} ({code}).\n"
        f"Locale notes: {note}\n\n"
        f"Return ONLY the translated JSON.\n\n"
        f"Source (en.json):\n{source_json}"
    )
    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"i18n_openai_{code}_{uuid.uuid4().hex[:8]}",
            system_message=system,
        )
        .with_model("openai", "gpt-4o")
    )
    raw = await chat.send_message(UserMessage(text=user_msg))
    cleaned = _sanitize(raw or "")
    return json.loads(cleaned)


async def main(codes):
    api_key = _api_key()
    src = json.loads(SOURCE.read_text())
    src_json = json.dumps(src, ensure_ascii=False, indent=2)
    for c in codes:
        if c not in TARGETS:
            continue
        name, note = TARGETS[c]
        for attempt in range(3):
            try:
                print(f"[i18n-openai] {c} attempt {attempt + 1}…", flush=True)
                bundle = await translate_with_openai(api_key, c, name, note, src_json)
                errs = _validate_same_shape(src, bundle)
                if errs:
                    print(f"[i18n-openai]   shape errors: {errs[:2]}")
                    continue
                out = FRONTEND_LOCALES / f"{c}.json"
                out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
                print(f"[i18n-openai] ✅ {c} saved")
                break
            except Exception as e:
                print(f"[i18n-openai]   err: {e}", flush=True)
                await asyncio.sleep(3)


if __name__ == "__main__":
    codes = sys.argv[1:] or ["yo", "ta"]
    asyncio.run(main(codes))
