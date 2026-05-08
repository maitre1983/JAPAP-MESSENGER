"""iter208 — Fix top-level constant arrays/objects using t() at module level.

Targets files where top-level `const X = [...]` or `const X = {...}` literals
contain `t(...)` calls. Replaces with `i18n.t(...)` and ensures import.
"""
import os
import re

TARGETS = [
    "/app/frontend/src/pages/QuizChallengesPage.js",
    "/app/frontend/src/pages/SettingsPage.js",
    "/app/frontend/src/pages/admin/WheelFortuneAdminTab.jsx",
    "/app/frontend/src/components/profile/DisplayCurrencySelector.jsx",
]


def find_top_level_decl_with_t(src: str):
    """Yield (start_idx, end_idx) for top-level declarations whose body
    contains a t( call.
    """
    out = []
    lines = src.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(?:export\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[\{\[]", line)
        if not m:
            i += 1
            continue
        # Walk forward to balanced close.
        opener = "{" if "= {" in line or "={" in line else "["
        closer = "}" if opener == "{" else "]"
        depth = 0
        seen_open = False
        for j in range(i, len(lines)):
            for ch in lines[j]:
                if ch == opener:
                    depth += 1
                    seen_open = True
                elif ch == closer:
                    depth -= 1
            if seen_open and depth == 0:
                break
        else:
            i += 1; continue
        block = "\n".join(lines[i:j+1])
        if re.search(r"(?<![\w.\$])t\(['`]", block):
            out.append((i, j + 1))
        i = j + 1
    return out


def transform(src: str) -> tuple[str, int]:
    decls = find_top_level_decl_with_t(src)
    if not decls:
        return src, 0
    lines = src.split("\n")
    n = 0
    for start, end in reversed(decls):
        block = "\n".join(lines[start:end])
        new_block = re.sub(r"(?<![\w.\$])t\(", "i18n.t(", block)
        if new_block != block:
            lines[start:end] = new_block.split("\n")
            n += 1
    src = "\n".join(lines)
    if n and "from 'i18next'" not in src and "from \"i18next\"" not in src:
        if "from 'react-i18next'" in src:
            src = re.sub(
                r"(import\s+[^;]*\sfrom\s+['\"]react-i18next['\"];?)",
                r"\1\nimport i18n from 'i18next';",
                src, count=1,
            )
        else:
            src = re.sub(
                r"(import\s+[^;]*\sfrom\s+['\"]react['\"];?)",
                r"\1\nimport i18n from 'i18next';",
                src, count=1,
            )
    return src, n


def main():
    for p in TARGETS:
        if not os.path.exists(p):
            print(f"  ! missing {p}"); continue
        with open(p) as f:
            old = f.read()
        new, n = transform(old)
        if new != old:
            with open(p, "w") as f:
                f.write(new)
            print(f"  ✓ {os.path.basename(p):<45} {n} top-level decl(s) rewired")
        else:
            print(f"  - {os.path.basename(p):<45} no changes")


if __name__ == "__main__":
    main()
