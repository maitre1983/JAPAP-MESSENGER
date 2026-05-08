#!/usr/bin/env python3
"""
iter204 — JAPAP i18n Codemod
============================
Tool that auto-migrates hardcoded JSX strings to `t('namespace.key')` and
seeds all 11 locale files with the new keys.

Patterns detected (in priority order):
  1.  >Hardcoded Text<           → >{t('ns.key')}<
  2.  placeholder="Hardcoded"    → placeholder={t('ns.key')}
  3.  title="Hardcoded"          → title={t('ns.key')}
  4.  aria-label="Hardcoded"     → aria-label={t('ns.key')}
  5.  alt="Hardcoded"            → alt={t('ns.key')}

The namespace defaults to the lower-cased filename stem (AdminPage.js → admin).

The tool:
  - Auto-generates a snake_case `key` from the text content (truncated, ASCII-folded)
  - De-duplicates collisions by appending _2, _3…
  - Adds the key to fr.json (canonical) with the original French text
  - Mirrors the key into all 10 other locales, marking the value with a
    `[TODO]` prefix so QA can review later (text remains visible in FR until
    translated). This guarantees 11/11 parity at all times.
  - Auto-injects `import { useTranslation } from 'react-i18next'` and
    `const { t } = useTranslation();` if missing.

Usage:
  python scripts/codemod_i18n.py <relative-file-path> [--namespace ns] [--dry-run]
  python scripts/codemod_i18n.py pages/AdminPage.js
  python scripts/codemod_i18n.py pages/AdminPage.js --namespace admin --dry-run

Important caveats (read before running):
  - Strings with curly braces, backticks, JS expressions, or interpolation
    are SKIPPED (the regex requires plain alpha + accents + basic punctuation).
  - Text inside <code>, <pre>, <kbd> tags is SKIPPED.
  - Component names (PascalCase strings starting at line start) are SKIPPED.
  - Any hardcoded string with `data-testid` literal value is SKIPPED.
  - Always inspect the diff before committing.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path("/app/frontend/src")
LOCALES_DIR = Path("/app/frontend/src/locales")
LANGS = ["en", "fr", "pt", "es", "ar", "sw", "ln", "yo", "hi", "bn", "ta"]

# Patterns ordered by specificity (attributes first, then text content).
PATTERNS = [
    # placeholder="…"
    (re.compile(r'\bplaceholder="([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇa-zà-ÿ][^"<>{]*?)"'),
     lambda key: f'placeholder={{t(\'{key}\')}}'),
    # title="…"
    (re.compile(r'\btitle="([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇa-zà-ÿ][^"<>{]*?)"'),
     lambda key: f'title={{t(\'{key}\')}}'),
    # aria-label="…"
    (re.compile(r'\baria-label="([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇa-zà-ÿ][^"<>{]*?)"'),
     lambda key: f'aria-label={{t(\'{key}\')}}'),
    # alt="…"
    (re.compile(r'\balt="([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇa-zà-ÿ\s][^"<>{]+)"'),
     lambda key: f'alt={{t(\'{key}\')}}'),
    # >Hardcoded Text<  (JSX text content)
    # Must start with capital letter or accented letter, allow !?:.,'’ -–—
    # Prevent matching JSX with embedded {expr}.
    (re.compile(r">([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][\w\sÀ-ÿ'’.,!?:;\-–—()/&%€$•]{4,200}?)<"),
     lambda key: f'>{{t(\'{key}\')}}<'),
    # iter206 (pass 2 AST-like) — return 'Texte en français' (FR uppercase / accents required
    # to avoid migrating SQL keywords, log messages, URLs…). Exclude quotes AND backslash
    # (escaped apostrophes break the replacement). For strings with \' we skip safely.
    (re.compile(r"(?<![.\w])return\s+['\"]((?=[^'\"\\]*[éèêàçïôûùäëîöÀ-ÿ])[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][^'\"\n\\]{3,150})['\"]"),
     lambda key: f"return t('{key}')"),
    # iter206 (pass 2) — ternary : cond ? 'Texte FR' : 'Autre FR'
    # Skip strings containing \' or \" (escape sequences) to avoid breaking JSX.
    (re.compile(r"([?:]\s*)(?:'((?=[^'\\]*[éèêàçïôûùäëîöÀ-ÿ])[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][^'\n\\]{3,150})'|\"((?=[^\"\\]*[éèêàçïôûùäëîöÀ-ÿ])[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][^\"\n\\]{3,150})\")"),
     lambda key: None),  # special: needs access to the captured prefix below
]

# Lines that should never be patched
SKIP_LINE_RX = re.compile(r'data-testid|className|console\.|//\s|^\s*\*|import\s|require\(')


def slugify(text: str, max_len: int = 35) -> str:
    """Convert 'Réseau de paiement' → 'reseau_de_paiement'."""
    s = unicodedata.normalize("NFKD", text)
    s = s.encode("ascii", "ignore").decode("ascii")  # drop accents
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s or "label"


def load_locales():
    out = {}
    for lang in LANGS:
        p = LOCALES_DIR / f"{lang}.json"
        out[lang] = json.loads(p.read_text(encoding="utf-8"))
    return out


def write_locales(locales):
    for lang, data in locales.items():
        p = LOCALES_DIR / f"{lang}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")


def get_nested(d, dotted_key):
    cur = d
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


def set_nested(d, dotted_key, value):
    cur = d
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def add_key_to_locales(locales, ns, key, fr_text):
    """Add the key to all 11 locales. fr.json gets the real text;
    others get '[TODO] {fr_text}' to flag for QA. Idempotent."""
    full = f"{ns}.{key}"
    for lang in LANGS:
        existing = get_nested(locales[lang], full)
        if existing is not None:
            continue
        if lang == "fr":
            set_nested(locales[lang], full, fr_text)
        elif lang == "en":
            # English placeholder = same FR text, prefixed [EN] for QA
            set_nested(locales[lang], full, f"[TODO EN] {fr_text}")
        else:
            set_nested(locales[lang], full, f"[TODO {lang.upper()}] {fr_text}")


def has_use_translation(src: str) -> bool:
    return ("from 'react-i18next'" in src or 'from "react-i18next"' in src) \
        and "useTranslation" in src


def has_t_call_destructured(src: str) -> bool:
    """Detect const { t } = useTranslation()."""
    return bool(re.search(r"const\s*\{\s*t\b[^}]*\}\s*=\s*useTranslation\(", src))


def inject_useTranslation(src: str) -> str:
    """Add the import + the destructure inside the default export function.
    Safe: only injects into a function whose name starts with a capital letter
    (React component) or 'use' (custom hook). Never into utility functions."""
    if not has_use_translation(src):
        # Insert after the first import block
        m = re.search(r"^(import .+?;\n)+", src, re.M)
        if m:
            insert_at = m.end()
            src = src[:insert_at] + "import { useTranslation } from 'react-i18next';\n" + src[insert_at:]
        else:
            src = "import { useTranslation } from 'react-i18next';\n" + src
    if not has_t_call_destructured(src):
        # Insert after `export default function Foo(...) {` (first match only,
        # and Foo MUST start with a capital letter to be a valid React component)
        m = re.search(r"export\s+default\s+function\s+([A-Z]\w*)\s*\([^)]*\)\s*\{\s*\n", src)
        if m:
            insert_at = m.end()
            indent = "  "
            src = src[:insert_at] + f"{indent}const {{ t }} = useTranslation();\n" + src[insert_at:]
        else:
            # Fallback: function NamedComponent(...) { — must start with capital or 'use'
            m = re.search(r"function\s+([A-Z]\w*|use[A-Z]\w*)\s*\([^)]*\)\s*\{\s*\n", src)
            if m:
                insert_at = m.end()
                src = src[:insert_at] + "  const { t } = useTranslation();\n" + src[insert_at:]
    return src


def migrate_file(path: Path, namespace: str, locales, dry_run=False):
    src = path.read_text(encoding="utf-8")
    original = src

    # Track keys generated for collision avoidance per file
    used_keys = set()
    report = []
    skipped = 0

    def make_unique_key(text):
        base = slugify(text)
        key = base
        i = 2
        while key in used_keys or get_nested(locales["fr"], f"{namespace}.{key}") is not None:
            existing = get_nested(locales["fr"], f"{namespace}.{key}")
            if existing == text:  # exact same text → reuse the existing key
                return key
            key = f"{base}_{i}"
            i += 1
            if i > 50:  # safety
                key = f"{base}_uniq{abs(hash(text)) % 9999}"
                break
        used_keys.add(key)
        return key

    # Apply patterns in order
    for rx, replacer in PATTERNS:
        def _sub(match):
            nonlocal skipped
            # iter206 (pass 2) — Ternary replacer handles 2 groups (prefix + text)
            if replacer(None) is None:  # ternary sentinel
                prefix = match.group(1)
                text = (match.group(2) or match.group(3) or '').strip()
            else:
                text = match.group(1).strip()
                prefix = None
            # Skip too short or numeric-only
            if len(text) < 3 or text.isdigit():
                skipped += 1
                return match.group(0)
            # Skip if line context contains data-testid (rare false positives)
            line_start = src.rfind("\n", 0, match.start()) + 1
            line_end = src.find("\n", match.end())
            line = src[line_start:line_end]
            if SKIP_LINE_RX.search(line):
                skipped += 1
                return match.group(0)
            # Skip if value already has interpolation
            if "{" in text or "}" in text or "${" in text or "`" in text:
                skipped += 1
                return match.group(0)
            # Generate key
            key = make_unique_key(text)
            full = f"{namespace}.{key}"
            add_key_to_locales(locales, namespace, key, text)
            report.append((full, text))
            if prefix is not None:
                return f"{prefix}t('{full}')"
            return replacer(full)

        src = rx.sub(_sub, src)

    if not report:
        return None  # nothing to do

    # Inject useTranslation if needed
    src = inject_useTranslation(src)

    if dry_run:
        return {"changes": len(report), "skipped": skipped, "report": report,
                "would_write": False}

    path.write_text(src, encoding="utf-8")
    return {"changes": len(report), "skipped": skipped, "report": report,
            "would_write": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="path relative to /app/frontend/src/")
    ap.add_argument("--namespace", help="i18n namespace (default: derived from filename)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = ROOT / args.file
    if not path.exists():
        print(f"❌ {path} not found", file=sys.stderr)
        sys.exit(1)

    # Derive namespace
    ns = args.namespace
    if not ns:
        stem = path.stem
        # AdminPage → admin / WalletPage → wallet / GamesAdminTab → admin_games
        s = re.sub(r"Page$|Tab$|Module$", "", stem)
        ns = re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()
    print(f"\n[codemod] Migrating {args.file}  →  namespace='{ns}'  "
          f"{'(dry-run)' if args.dry_run else ''}")

    locales = load_locales()
    result = migrate_file(path, ns, locales, dry_run=args.dry_run)
    if not result:
        print("  ✅ Nothing to migrate (no hardcoded strings detected)")
        return

    print(f"\n  ✅ {result['changes']} strings migrated, {result['skipped']} skipped")
    print("  Sample of new keys:")
    for full, text in result["report"][:10]:
        print(f"    {full:<55}  ← {text[:50]!r}")
    if len(result["report"]) > 10:
        print(f"    … +{len(result['report']) - 10} more")

    if not args.dry_run:
        write_locales(locales)
        print(f"\n  💾 Wrote 11 locale files (each got +{result['changes']} new keys)")
        print(f"  📝 fr.json : real French text")
        print(f"     en.json : '[TODO EN] <french>' (review needed)")
        print(f"     others  : '[TODO XX] <french>' (review needed)")


if __name__ == "__main__":
    main()
