#!/usr/bin/env python3
"""iter210 — CI guard: fail-fast on any i18n regression that would crash
JAPAP in production (the iter205/206/208 class of bugs).

Runs three independent audits:

  [A] Every file calling `t('...')` / `t(\`...\`)` MUST import either
      `useTranslation` (from react-i18next) or `i18n` (from i18next).
  [B] No top-level `const/let/var/export const NAME = t('...')` (or
      object-property `key: t('...')` at indent 0) — that would crash at
      module load with `t is not defined`.
  [C] No function body that calls bare `t(...)` without either declaring
      `const { t } = useTranslation()` inside, receiving `t` as a parameter,
      or inheriting `t` from an ancestor scope.

Exits non-zero on ANY violation. Intended to be called from GitHub Actions
on every PR touching `frontend/src/**/*.{js,jsx,ts,tsx}`.

Usage:
    python3 scripts/ci_audit_i18n.py              # full scan + report
    python3 scripts/ci_audit_i18n.py --json       # machine-readable output

Exit codes:
    0  all clean
    1  audit A failed (missing import)
    2  audit B failed (module-level t())
    3  audit C failed (bare t() without hook in scope)
    4  multiple audits failed
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "frontend" / "src"


# ─────────────────────────────────────────────────────────────────────
# AST helper: find every function/arrow body with its params + indices.
# ─────────────────────────────────────────────────────────────────────
def find_function_bodies(src: str) -> list[tuple[str, str, int, int]]:
    out: list[tuple[str, str, int, int]] = []

    for m in re.finditer(
        r"\b(?:export\s+(?:default\s+)?)?function\s+(\w+)\s*\(", src,
    ):
        i = m.end()
        depth = 1
        while i < len(src) and depth > 0:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        params = src[m.end():i - 1]
        while i < len(src) and src[i] in " \t\r\n":
            i += 1
        if i < len(src) and src[i] == "{":
            d = 0
            j = i
            while j < len(src):
                if src[j] == "{":
                    d += 1
                elif src[j] == "}":
                    d -= 1
                    if d == 0:
                        out.append((m.group(1), params, i + 1, j))
                        break
                j += 1

    for m in re.finditer(
        r"\b(?:const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)|\w+)\s*=>\s*\{", src,
    ):
        params = m.group(2)
        if params.startswith("("):
            params = params[1:-1]
        i = m.end() - 1
        d = 0
        j = i
        while j < len(src):
            if src[j] == "{":
                d += 1
            elif src[j] == "}":
                d -= 1
                if d == 0:
                    out.append((m.group(1), params, i + 1, j))
                    break
            j += 1
    return out


USE_TRANSLATION_RE = re.compile(
    r"const\s*\{\s*[^}]*\bt\b[^}]*\}\s*=\s*useTranslation\s*\(",
)
BARE_T_CALL_RE = re.compile(r"(?<![\w.\$])t\(['`]")
PARAM_T_RE = re.compile(r"\bt\b")


# ─────────────────────────────────────────────────────────────────────
# Audits
# ─────────────────────────────────────────────────────────────────────
def iter_js_files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in (".js", ".jsx", ".ts", ".tsx"):
            if "node_modules" in p.parts or "__tests__" in p.parts:
                continue
            yield p


def audit_a(files: list[Path]) -> list[str]:
    """[A] t() usage without i18n import."""
    violations = []
    for p in files:
        src = p.read_text()
        if not re.search(r"\bt\(['`]", src):
            continue
        has_import = bool(re.search(
            r"useTranslation|import\s+i18n|from\s+['\"]i18next['\"]", src,
        ))
        if not has_import:
            violations.append(str(p))
    return violations


def audit_b(files: list[Path]) -> list[tuple[str, int, str]]:
    """[B] top-level t() calls at module scope."""
    violations = []
    for p in files:
        src = p.read_text()
        depth = 0
        in_str = None
        in_tpl = False
        in_lc = False
        in_bc = False
        i = 0
        while i < len(src):
            c = src[i]
            n = src[i + 1] if i + 1 < len(src) else ""
            if in_lc:
                if c == "\n":
                    in_lc = False
                i += 1; continue
            if in_bc:
                if c == "*" and n == "/":
                    in_bc = False; i += 2; continue
                i += 1; continue
            if in_str:
                if c == "\\": i += 2; continue
                if c == in_str: in_str = None
                i += 1; continue
            if in_tpl:
                if c == "\\": i += 2; continue
                if c == "`": in_tpl = False
                i += 1; continue
            if c == "/" and n == "/": in_lc = True; i += 2; continue
            if c == "/" and n == "*": in_bc = True; i += 2; continue
            if c in ("'", '"'): in_str = c; i += 1; continue
            if c == "`": in_tpl = True; i += 1; continue
            if c == "{": depth += 1
            elif c == "}": depth -= 1
            # at module depth=0, flag `t(` that isn't part of an identifier
            # (so `i18n.t(` is allowed since `.` is non-word and prev char
            # check filters it out via the `(?<![\w.\$])` regex emulation).
            if depth == 0 and c == "t" and n == "(":
                prev = src[i - 1] if i > 0 else " "
                if not re.match(r"[\w$.]", prev):
                    line_no = src[:i].count("\n") + 1
                    line_start = src.rfind("\n", 0, i) + 1
                    line_end = src.find("\n", i)
                    line = src[line_start:line_end]
                    stripped = line.lstrip()
                    if not stripped.startswith(("//", "*", "import")):
                        violations.append((str(p), line_no, line.strip()[:120]))
            i += 1
    return violations


def audit_c(files: list[Path]) -> list[tuple[str, int, str]]:
    """[C] function body uses bare t() without useTranslation/param/closure."""
    violations = []
    for p in files:
        src = p.read_text()
        for name, params, s, e in find_function_bodies(src):
            body = src[s:e]
            if not BARE_T_CALL_RE.search(body):
                continue
            if USE_TRANSLATION_RE.search(body):
                continue
            if PARAM_T_RE.search(params):
                continue
            # Inherit from ancestor scope?
            above = src[:s]
            if USE_TRANSLATION_RE.search(above):
                continue
            line_no = src[:s].count("\n") + 1
            violations.append((str(p), line_no, name))
    return violations


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--root", default=str(ROOT), help="frontend src dir")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"✗ root not found: {root}", file=sys.stderr)
        sys.exit(5)

    files = list(iter_js_files(root))
    a = audit_a(files)
    b = audit_b(files)
    c = audit_c(files)

    if args.json:
        print(json.dumps({
            "scanned_files": len(files),
            "audit_a_missing_import": a,
            "audit_b_module_level_t": [
                {"file": f, "line": l, "code": code} for f, l, code in b
            ],
            "audit_c_broken_functions": [
                {"file": f, "line": l, "fn": n} for f, l, n in c
            ],
            "total_violations": len(a) + len(b) + len(c),
        }, ensure_ascii=False, indent=2))
    else:
        print("=" * 70)
        print(f"JAPAP i18n CI audit — scanned {len(files)} files under {root}")
        print("=" * 70)

        print(f"\n[A] Fichiers t() sans import i18n/useTranslation : {len(a)}")
        for f in a[:30]:
            print(f"    ✗ {f}")
        if len(a) > 30:
            print(f"    … and {len(a)-30} more")

        print(f"\n[B] t() au niveau module (top-level) : {len(b)}")
        for f, l, code in b[:30]:
            print(f"    ✗ {f}:{l}  {code}")
        if len(b) > 30:
            print(f"    … and {len(b)-30} more")

        print(f"\n[C] Fonctions cassées (bare t() sans hook/param/ancestor) : {len(c)}")
        for f, l, n in c[:30]:
            print(f"    ✗ {f}:{l}  fn {n}")
        if len(c) > 30:
            print(f"    … and {len(c)-30} more")

        print()
        if not a and not b and not c:
            print("✅ AUDIT CEO A/B/C — 0 violation. Safe to merge.")
        else:
            print(f"✗ {len(a)+len(b)+len(c)} violation(s) — blocking merge.")
            print("  Fix strategy:")
            print("  • [A] add `import { useTranslation } from 'react-i18next';`")
            print("  • [B] convert top-level consts to factory: `const getX = (t) => ({...})`")
            print("  • [C] inject `const { t } = useTranslation();` at top of function body")
            print("        OR pass `t` as parameter from caller.")

    code = 0
    if a and b and c:
        code = 4
    elif a:
        code = 1
    elif b:
        code = 2
    elif c:
        code = 3
    sys.exit(code)


if __name__ == "__main__":
    main()
