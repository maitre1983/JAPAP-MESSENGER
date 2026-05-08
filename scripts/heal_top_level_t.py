#!/usr/bin/env python3
"""
iter206 healer — un-migrate module-level t() calls the codemod injected into
non-React files (constants, utils, services, reducers, module-level arrays…).

Strategy :
  - For each .js/.jsx file using `t('...')`, walk AST-like to find t() calls
    that are at module-top (not nested in any function declaration).
  - For each such top-level t() call, read the key in the FR locale file to
    recover the original French text and substitute it back inline.
  - Safely leave in-component t() calls intact.

Uses only stdlib. Targets only the files the first agent already flagged.
"""
import json
import re
from pathlib import Path

ROOT = Path("/app/frontend/src")
FR = json.loads((ROOT / "locales/fr.json").read_text(encoding="utf-8"))

TARGETS = [
    "services/mediaFilters.js",
    "pages/QuizChallengesPage.js",
    "pages/admin/TransportPricingAdminTab.jsx",
    "pages/admin/SupportAdminTab.jsx",
    "pages/admin/PaymentsAdminTab.jsx",
    "components/profile/DisplayCurrencySelector.jsx",
    "components/admin/messaging/messagingApi.js",
    "components/ui/pagination.jsx",  # ui component is fine but uses t at module level
]


def resolve(dotted_key):
    cur = FR
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


def unmigrate_module_level(src):
    """Walk brace depth and only replace t('...') at depth 0."""
    out = []
    i = 0
    depth = 0
    in_str = None
    in_comment = False
    replaced = 0
    while i < len(src):
        c = src[i]
        c2 = src[i:i+2]
        # Handle string literals
        if not in_str and not in_comment:
            if c2 == "//":
                # line comment — skip to \n
                j = src.find("\n", i)
                out.append(src[i:j if j!=-1 else len(src)])
                i = j if j!=-1 else len(src)
                continue
            if c2 == "/*":
                j = src.find("*/", i)
                out.append(src[i:j+2 if j!=-1 else len(src)])
                i = j+2 if j!=-1 else len(src)
                continue
            if c in '"\'`':
                in_str = c
                out.append(c); i += 1; continue
            if c == "{":
                depth += 1
                out.append(c); i += 1; continue
            if c == "}":
                depth -= 1
                out.append(c); i += 1; continue
            # At depth 0 only: look for t('ns.key') or t("ns.key")
            if depth == 0 and c == "t":
                m = re.match(r"t\(\s*['\"]([a-zA-Z0-9_.\-]+)['\"]\s*\)", src[i:])
                if m:
                    key = m.group(1)
                    val = resolve(key)
                    if val is not None:
                        # Escape for JS string literal with "
                        safe = val.replace("\\", "\\\\").replace('"', '\\"')
                        out.append(f'"{safe}"')
                        i += m.end()
                        replaced += 1
                        continue
            out.append(c); i += 1; continue
        else:
            # inside string
            if c == "\\":
                out.append(src[i:i+2]); i += 2; continue
            if c == in_str:
                in_str = None
            out.append(c); i += 1; continue
    return "".join(out), replaced


# Additionally: if a file still has `const { t } = useTranslation();` at
# module level (outside any function), it's broken. But those live inside
# React functions already so we leave them.

def process(path):
    full = ROOT / path
    if not full.exists():
        print(f"  [skip] {path} not found")
        return
    src = full.read_text(encoding="utf-8")
    if "t(" not in src:
        return
    new_src, n = unmigrate_module_level(src)
    if n:
        # Remove now-unused useTranslation import if no t( remains in file
        if "useTranslation" in new_src and " t(" not in new_src and "t(" not in new_src:
            new_src = re.sub(r"^import \{ useTranslation \} from 'react-i18next';\n", "",
                             new_src, count=1, flags=re.M)
        full.write_text(new_src, encoding="utf-8")
        print(f"  ✅ {path}: un-migrated {n} module-level t() → inline string")
    else:
        print(f"  ℹ️  {path}: no module-level t() calls (already clean)")


if __name__ == "__main__":
    print("iter206 healer — un-migrating module-level t() calls")
    for t in TARGETS:
        process(t)
    print("\nDone.")
