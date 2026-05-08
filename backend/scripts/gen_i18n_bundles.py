"""
JAPAP — One-shot i18n bundle generator (iter72).

Uses Claude Sonnet 4.5 via the Emergent LLM universal key to translate
`frontend/src/locales/en.json` into 9 target languages. The source is
kept as the single reference to prevent drift.

Run once:
    cd /app/backend && python scripts/gen_i18n_bundles.py

Idempotent: overwrites `frontend/src/locales/{code}.json` with the new
translation for every `TARGETS` entry. Safe to re-run after adding keys.
"""
import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

# Make backend modules importable when run from repo root too
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore

FRONTEND_LOCALES = Path("/app/frontend/src/locales")
SOURCE = FRONTEND_LOCALES / "en.json"

# code → (name, note for the translator)
TARGETS = {
    "pt": ("Portuguese",     "Target European Portuguese from Portugal (pt-PT). Use 'tu' form. Keep 'JAPAP' as-is."),
    "es": ("Spanish",        "Target neutral Latin-American Spanish. Use 'tú' form. Keep 'JAPAP' as-is."),
    "ar": ("Arabic",         "Target Modern Standard Arabic. Keep 'JAPAP' as-is (Latin). Write numerals in Arabic."),
    "sw": ("Swahili",        "Standard East-African Kiswahili (Tanzania/Kenya). Keep 'JAPAP' as-is."),
    "ln": ("Lingala",        "Kinshasa-style Lingala. Keep 'JAPAP' as-is. Favour short forms for mobile UI."),
    "yo": ("Yoruba",         "Standard Yorùbá with tonal marks. Keep 'JAPAP' as-is."),
    "hi": ("Hindi",          "Devanagari script, neutral everyday Hindi. Keep 'JAPAP' as-is."),
    "bn": ("Bengali",        "Standard Bengali/Bangla. Keep 'JAPAP' as-is."),
    "ta": ("Tamil",          "Standard Tamil script, formal but warm. Keep 'JAPAP' as-is."),
}


def _api_key() -> str:
    k = os.environ.get("EMERGENT_LLM_KEY", "")
    if not k:
        # Fallback: try to parse from /app/backend/.env directly
        env_path = Path("/app/backend/.env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("EMERGENT_LLM_KEY="):
                    k = line.split("=", 1)[1].strip()
                    break
    if not k:
        raise RuntimeError("EMERGENT_LLM_KEY is not available in environment")
    return k


def _sanitize(raw: str) -> str:
    """Strip code fences, if any."""
    if not raw:
        return raw
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return raw.strip()


def _validate_same_shape(src: dict, tgt: dict, path: str = "") -> list[str]:
    """Return a list of missing/extra-type keys so we catch LLM mistakes."""
    errors = []
    if type(src) is not type(tgt):
        errors.append(f"{path}: type mismatch ({type(src).__name__} → {type(tgt).__name__})")
        return errors
    if isinstance(src, dict):
        for k in src:
            if k not in tgt:
                errors.append(f"{path}.{k}: missing")
            else:
                errors.extend(_validate_same_shape(src[k], tgt[k], f"{path}.{k}"))
        for k in tgt:
            if k not in src:
                errors.append(f"{path}.{k}: unexpected extra key")
    return errors


async def translate_bundle(api_key: str, code: str, name: str, note: str, source_json: str) -> dict:
    system = (
        "You are a professional product-localization engineer for JAPAP, a mobile-first "
        "messenger super-app (chat, wallet, social feed, crypto staking, marketplace). "
        "Target an audience comfortable on mobile devices in Africa, Europe, LatAm and South Asia.\n\n"
        "Rules:\n"
        "1) Produce STRICT JSON only — no prose, no code fences, no comments. Top-level object only.\n"
        "2) Preserve the exact tree structure and every key from the source. Do NOT rename keys.\n"
        "3) Never translate the product name 'JAPAP' — keep as 'JAPAP' in Latin letters.\n"
        "4) Preserve placeholders like {count}, {name} — do not translate their contents.\n"
        "5) Keep emojis as-is. Keep punctuation natural for the target language.\n"
        "6) Translations must be professional, natural, and concise for mobile UI — not literal, not machine-ish.\n"
        "7) Use the polite-but-warm register common in consumer apps (Instagram / Stripe tone).\n"
    )
    user_msg = (
        f"Translate this JAPAP UI string bundle from English into **{name}** ({code}).\n"
        f"Locale notes: {note}\n\n"
        f"Return ONLY the translated JSON, same keys, same shape, no extras.\n\n"
        f"Source (en.json):\n{source_json}"
    )
    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"i18n_{code}_{uuid.uuid4().hex[:8]}",
            system_message=system,
        )
        .with_model("anthropic", "claude-sonnet-4-5-20250929")
    )
    raw = await chat.send_message(UserMessage(text=user_msg))
    cleaned = _sanitize(raw or "")
    return json.loads(cleaned)


async def main():
    api_key = _api_key()
    src = json.loads(SOURCE.read_text())
    src_json = json.dumps(src, ensure_ascii=False, indent=2)
    total = len(TARGETS)
    print(f"[i18n] Generating bundles for {total} languages via Claude Sonnet 4.5")
    # Fan out concurrently — Claude handles parallel chats comfortably.
    async def one(code, name, note):
        try:
            print(f"[i18n] → {code} ({name}) translating…")
            bundle = await translate_bundle(api_key, code, name, note, src_json)
            errors = _validate_same_shape(src, bundle)
            if errors:
                print(f"[i18n] ⚠️  {code}: shape errors → {errors[:3]}")
                return code, None
            out = FRONTEND_LOCALES / f"{code}.json"
            out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
            print(f"[i18n] ✅ {code} saved ({len(json.dumps(bundle))} bytes)")
            return code, bundle
        except Exception as e:
            print(f"[i18n] ❌ {code} failed: {e}")
            return code, None

    results = await asyncio.gather(
        *[one(c, n, note) for c, (n, note) in TARGETS.items()]
    )
    ok = [c for c, b in results if b]
    fail = [c for c, b in results if not b]
    print(f"\n[i18n] DONE — ok: {ok} | failed: {fail}")


if __name__ == "__main__":
    asyncio.run(main())
