#!/usr/bin/env python3
"""
iter203 — Audit i18n complet
============================
Mesure HONNÊTE du scope de la traduction JAPAP :
  [A] Compte les `t('...')` calls dans tout src/
  [B] Estime les strings JSX hardcoded (>2 mots, A-Z, accents) qui devraient
      passer par t()
  [C] Diff des clés entre les 11 fichiers locales/{lang}.json (keys présentes
      dans fr.json mais absentes ailleurs)
  [D] Liste les pages les plus "polluées" (max hardcoded strings)

Utilise des heuristiques regex — n'est PAS parfait (peut compter du JSDoc) mais
donne un ordre de grandeur réaliste pour estimer le délai.
"""
import os
import re
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/app/frontend/src")
LOCALES = Path("/app/frontend/src/locales")
LANGS = ["en", "fr", "pt", "es", "ar", "sw", "ln", "yo", "hi", "bn", "ta"]

# t('foo'), t("foo.bar"), t(`foo`)
T_CALL_RX = re.compile(r"\bt\(\s*[\"'`]([a-zA-Z0-9_.\-]+)[\"'`]")

# Heuristic for hardcoded user-visible JSX strings:
# Quoted text that contains:
#  - at least 2 words
#  - majoritairement de l'alpha (humans), pas du code
#  - n'est pas un import path / className / data-testid
HARDCODED_RX = re.compile(
    r">([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][\wÀ-ÿ '’,!?:.\-]{4,80})<"
)


def find_files():
    out = []
    for root, dirs, files in os.walk(ROOT):
        # Skip locales/ themselves and tests
        dirs[:] = [d for d in dirs if d not in ("locales", "node_modules")]
        for f in files:
            if f.endswith((".js", ".jsx", ".tsx", ".ts")):
                out.append(Path(root) / f)
    return out


def count_t_calls(files):
    keys = Counter()
    files_with = 0
    for fp in files:
        try:
            txt = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        hits = T_CALL_RX.findall(txt)
        if hits:
            files_with += 1
            for h in hits:
                keys[h] += 1
    return keys, files_with


def find_hardcoded(files):
    """Estimate the number of hardcoded JSX text nodes per file."""
    by_file = Counter()
    for fp in files:
        try:
            txt = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        hits = HARDCODED_RX.findall(txt)
        # Filter false positives (numbers, single chars, keywords)
        clean = [h for h in hits if len(h.split()) >= 2 and not h.isdigit()]
        by_file[str(fp.relative_to(ROOT))] = len(clean)
    return by_file


def load_locale(lang):
    p = LOCALES / f"{lang}.json"
    if not p.exists():
        return None, set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [WARN] {lang}.json invalid JSON: {e}")
        return None, set()
    keys = set()

    def walk(prefix, obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = f"{prefix}.{k}" if prefix else k
                walk(key, v)
        else:
            keys.add(prefix)
    walk("", data)
    return data, keys


def main():
    files = find_files()
    print(f"[A] Analysing {len(files)} JS/JSX files in src/\n")

    t_keys, files_with_t = count_t_calls(files)
    print(f"  [A.1] Files using t(): {files_with_t} / {len(files)}  "
          f"({100 * files_with_t / max(len(files),1):.0f}%)")
    print(f"  [A.2] Total t() calls: {sum(t_keys.values())}")
    print(f"  [A.3] Distinct keys in code: {len(t_keys)}")

    print("\n[B] Hardcoded JSX strings (heuristic, ≥2 words):")
    by_file = find_hardcoded(files)
    total_hc = sum(by_file.values())
    files_with_hc = sum(1 for v in by_file.values() if v > 0)
    print(f"  [B.1] Total hardcoded snippets: ~{total_hc}")
    print(f"  [B.2] Files with hardcoded text: {files_with_hc}")
    print(f"  [B.3] Top 15 worst offenders:")
    for fp, n in by_file.most_common(15):
        print(f"        {n:>4}  {fp}")

    print("\n[C] Locale completeness vs fr.json (the canonical source):")
    fr_data, fr_keys = load_locale("fr")
    if fr_keys:
        print(f"  fr.json: {len(fr_keys)} keys (canonical)")
        for lang in LANGS:
            if lang == "fr":
                continue
            data, keys = load_locale(lang)
            if data is None:
                print(f"  {lang}.json: ❌ MISSING / INVALID JSON")
                continue
            missing = fr_keys - keys
            extra = keys - fr_keys
            pct = 100 * (1 - len(missing) / max(len(fr_keys), 1))
            mark = "✅" if not missing else "⚠️" if len(missing) < 50 else "❌"
            print(f"  {lang}.json: {len(keys):>5} keys  {pct:>5.1f}% complete "
                  f"({len(missing):>4} missing, {len(extra):>3} extra)  {mark}")

    print("\n[D] Top 10 distinct t() keys actually used in code:")
    for k, n in t_keys.most_common(10):
        present_in_fr = k in fr_keys
        flag = "" if present_in_fr else "  ⚠️ MISSING from fr.json"
        print(f"  {n:>5}× {k}{flag}")

    # Keys used in code but NOT in fr.json (broken keys)
    broken = [k for k in t_keys if k not in fr_keys]
    print(f"\n[E] Keys USED IN CODE but absent from fr.json: {len(broken)}")
    for k in broken[:15]:
        print(f"  - {k}")
    if len(broken) > 15:
        print(f"  … +{len(broken)-15} more")

    # Final summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    coverage = 100 * files_with_t / max(len(files), 1)
    print(f"i18n coverage : {coverage:.0f}% of files use t()")
    print(f"Hardcoded debt: ~{total_hc} JSX snippets to migrate to t()")
    print(f"Locale gaps   : {len(broken)} broken keys in code")
    if total_hc > 0:
        # Realistic effort: ~1 min/snippet × 11 langs (incl. translation review)
        effort_h = (total_hc * 1.5) / 60  # hours of dev work
        print(f"\nRealistic effort to reach 100% i18n :")
        print(f"  - Migrate hardcoded JSX     : ~{effort_h:.0f}h dev")
        print(f"  - Translate keys × 11 langs : ~{(total_hc * 11) / 100:.0f}h")
        print(f"  - QA visual review          : ~8h × 11 langs = 88h")
        print(f"  TOTAL ESTIMATE              : ~{effort_h + (total_hc*11)/100 + 88:.0f}h")


if __name__ == "__main__":
    main()
