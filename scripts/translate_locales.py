#!/usr/bin/env python3
"""
iter205 — Auto-translate [TODO XX] markers in JAPAP locales via Emergent LLM
============================================================================
Reads each non-FR locale file, finds every value matching `[TODO XX] {french}`,
and replaces it with a real Gemini-generated translation.

Strategy:
  - Group keys per locale, batch them in chunks of 30 (one LLM round-trip
    returns 30 short translations as a JSON list — much cheaper than 1-by-1).
  - Use Gemini 2.5-flash (cheapest + accurate enough for short UI strings).
  - Keep `{{count}}` ICU placeholders intact via the system prompt.
  - Regenerate a per-locale audit report listing each (key, fr, lang).
  - On API failure for a chunk, fall back to per-key calls with retry.

Run:
  python3 scripts/translate_locales.py            # all 10 langs
  python3 scripts/translate_locales.py --langs en,pt,es
  python3 scripts/translate_locales.py --dry-run

Exit codes: 0 = all translated, 2 = some fallbacks remained
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

# Force load env before any imports that touch it
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from emergentintegrations.llm.chat import LlmChat, UserMessage  # noqa: E402

LOCALES = Path("/app/frontend/src/locales")
REPORTS = Path("/app/scripts/translation_reports")
REPORTS.mkdir(exist_ok=True)

LANG_NAMES = {
    "en": "English",
    "pt": "Portuguese (European)",
    "es": "Spanish (Latin America)",
    "ar": "Modern Standard Arabic",
    "sw": "Swahili (East African)",
    "ln": "Lingala",
    "yo": "Yoruba",
    "hi": "Hindi",
    "bn": "Bengali",
    "ta": "Tamil",
}

TODO_RX = re.compile(r"^\[TODO ([A-Z]{2})\]\s*(.+)$", re.S)
CHUNK_SIZE = 25
KEY = os.environ.get("EMERGENT_LLM_KEY", "")
assert KEY, "EMERGENT_LLM_KEY missing in /app/backend/.env"


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            out.update(flatten(v, full))
    elif isinstance(obj, str):
        out[prefix] = obj
    return out


def set_nested(d, dotted, value):
    cur = d
    parts = dotted.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def system_message_for(lang_code: str) -> str:
    target = LANG_NAMES[lang_code]
    return (
        f"You are a professional translator for a mobile fintech super-app "
        f"called JAPAP (think a mix of WhatsApp + Stripe + Vinted, used in "
        f"Africa). Translate French UI strings into {target}.\n\n"
        f"RULES:\n"
        f"1. Output ONLY a JSON array of strings, in the SAME order as the "
        f"input array. No markdown, no explanation, no keys.\n"
        f"2. Preserve placeholders EXACTLY: `{{{{count}}}}`, `{{{{name}}}}`, "
        f"`{{0}}`, `${{var}}`, `%s`, etc. Never translate inside braces.\n"
        f"3. Keep newlines `\\n` intact.\n"
        f"4. Use natural, concise UI language (button labels, tooltips, "
        f"toast messages). Match the casing convention of {target}.\n"
        f"5. Common terms: JAPAP = brand name (never translate), wallet, "
        f"KYC, USD = keep as-is unless target language has a standard "
        f"equivalent.\n"
        f"6. Do NOT add any prefix like '[TODO]' or markdown.\n\n"
        f"Translate this JSON array of French strings into {target}, returning "
        f"ONLY the JSON array of translations:"
    )


async def translate_chunk(lang: str, french_strs: list[str], retries=2):
    """Returns list of translations same length as input, or raises."""
    if not french_strs:
        return []
    sys_msg = system_message_for(lang)
    payload = json.dumps(french_strs, ensure_ascii=False)
    last_err = None
    for attempt in range(retries + 1):
        try:
            chat = LlmChat(
                api_key=KEY,
                session_id=f"translate-{lang}-{attempt}",
                system_message=sys_msg,
            ).with_model("gemini", "gemini-2.5-flash")
            resp = await chat.send_message(UserMessage(text=payload))
            text = (resp or "").strip()
            # Remove possible markdown fence
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            arr = json.loads(text)
            if not isinstance(arr, list) or len(arr) != len(french_strs):
                raise ValueError(
                    f"chunk size mismatch: got {len(arr) if isinstance(arr, list) else type(arr).__name__} "
                    f"expected {len(french_strs)}")
            # Sanity: cast everything to str
            return [str(x) for x in arr]
        except Exception as e:
            last_err = e
            await asyncio.sleep(1.5 * (attempt + 1))
    raise last_err  # type: ignore


async def translate_locale(lang: str, dry_run=False, only_keys=None):
    p = LOCALES / f"{lang}.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    flat = flatten(data)
    todo_pairs = []  # (key, french)
    for k, v in flat.items():
        m = TODO_RX.match(v)
        if m:
            tag, fr_text = m.group(1), m.group(2)
            if tag.upper() != lang.upper():
                continue
            if only_keys and k not in only_keys:
                continue
            todo_pairs.append((k, fr_text))

    if not todo_pairs:
        print(f"  {lang}.json: nothing to translate")
        return {"lang": lang, "translated": 0, "report": []}

    print(f"  {lang}.json: translating {len(todo_pairs)} keys "
          f"in {(len(todo_pairs) + CHUNK_SIZE - 1) // CHUNK_SIZE} chunks…")

    report = []
    failures = 0
    for i in range(0, len(todo_pairs), CHUNK_SIZE):
        chunk = todo_pairs[i: i + CHUNK_SIZE]
        keys = [k for k, _ in chunk]
        fr_texts = [t for _, t in chunk]
        try:
            translations = await translate_chunk(lang, fr_texts)
        except Exception as e:
            short = str(e)[:80]
            print(f"    [WARN] chunk {i // CHUNK_SIZE} failed: {short} → falling back per-key")
            translations = []
            for txt in fr_texts:
                try:
                    one = await translate_chunk(lang, [txt])
                    translations.append(one[0])
                except Exception as e2:
                    short2 = str(e2)[:60]
                    print(f"      [FAIL] '{txt[:40]}' → {short2}")
                    translations.append(f"[TODO {lang.upper()}] {txt}")
                    failures += 1

        for (key, fr), tr in zip(chunk, translations):
            if not dry_run:
                set_nested(data, key, tr)
            report.append({"key": key, "fr": fr, "translation": tr,
                           "ok": not tr.startswith(f"[TODO {lang.upper()}]")})

        print(f"    chunk {i // CHUNK_SIZE + 1}/"
              f"{(len(todo_pairs) + CHUNK_SIZE - 1) // CHUNK_SIZE} done "
              f"({len([r for r in report if r['ok']])} good)")

    if not dry_run:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    # Save report
    rp = REPORTS / f"{lang}.json"
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8")
    print(f"  {lang}.json: ✅ {len(report) - failures}/{len(report)} translated, "
          f"report → {rp}")
    return {"lang": lang, "translated": len(report) - failures,
            "failures": failures, "report": report}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", default="",
                    help="comma-separated list (default: all 10 non-FR)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-keys", default="",
                    help="comma-separated keys to translate (debug)")
    args = ap.parse_args()

    langs = [x.strip() for x in args.langs.split(",") if x.strip()] \
        if args.langs else list(LANG_NAMES.keys())
    only_keys = set(x.strip() for x in args.only_keys.split(",") if x.strip()) \
        if args.only_keys else None

    print("\n[i18n auto-translate] target locales:", ", ".join(langs))
    if args.dry_run:
        print("[DRY-RUN] no files will be written")
    print()

    summary = []
    for lang in langs:
        if lang == "fr":
            continue
        if lang not in LANG_NAMES:
            print(f"  [skip] {lang} not in known locales")
            continue
        try:
            res = await translate_locale(lang, dry_run=args.dry_run,
                                         only_keys=only_keys)
            summary.append(res)
        except Exception as e:
            print(f"  [{lang}] FATAL: {e}")
            summary.append({"lang": lang, "translated": 0, "fatal": str(e)})

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    total_ok = sum(s.get("translated", 0) for s in summary)
    total_fail = sum(s.get("failures", 0) for s in summary)
    for s in summary:
        flag = "✅" if not s.get("failures") and not s.get("fatal") else "⚠️"
        msg = f"{s.get('translated', 0)} translated"
        if s.get("failures"): msg += f", {s['failures']} fallback"
        if s.get("fatal"): msg += f", FATAL: {s['fatal'][:50]}"
        print(f"  {flag} {s['lang']:<3} {msg}")
    print(f"\nTotal: {total_ok} translations across {len(summary)} languages "
          f"({total_fail} fallbacks)")
    print(f"Reports: {REPORTS}/")
    sys.exit(2 if total_fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
