"""iter210 hotfix — Reorder TDZ: every `const FOO = getFoo(t);` line must
appear AFTER `const { t } = useTranslation();` within the same function
body. The previous codemod injected at top of body unconditionally,
landing BEFORE the hook destructure on lines that already had one.
"""
import os
import re

ROOT = "/app/frontend/src"


def fix_file(path: str) -> int:
    with open(path) as f:
        src = f.read()
    orig = src
    lines = src.split("\n")
    n_fixes = 0

    # Walk lines top-down. When we encounter a function body, look for
    # the pattern within that body where `const X = getX(t);` precedes
    # `const { t } = useTranslation();`. If so, swap.
    # Simpler approach: regex-rewrite triplets like:
    #     const FOO = getFoo(t);
    #     const { t } = useTranslation();
    # → swap so useTranslation comes first.

    new_lines = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        m_factory = re.match(r"(\s*)const\s+([A-Za-z_$][\w$]*)\s*=\s*[A-Za-z_$][\w$]*\(t\)\s*;\s*$", cur)
        if m_factory:
            # Look forward up to 5 lines for `const { t } = useTranslation()`
            for j in range(i + 1, min(i + 6, len(lines))):
                if re.search(r"const\s*\{\s*[^}]*\bt\b[^}]*\}\s*=\s*useTranslation\s*\(", lines[j]):
                    # Move useTranslation line BEFORE the factory line
                    use_line = lines[j]
                    # Pop use_line from j, insert at i
                    new_lines.append(use_line)
                    # All lines between i and j (exclusive of j) get appended after
                    for k in range(i, j):
                        new_lines.append(lines[k])
                    i = j + 1
                    n_fixes += 1
                    break
            else:
                new_lines.append(cur)
                i += 1
        else:
            new_lines.append(cur)
            i += 1

    src = "\n".join(new_lines)
    if src != orig:
        with open(path, "w") as f:
            f.write(src)
    return n_fixes


def main():
    total = 0
    for root, dirs, files in os.walk(ROOT):
        if "node_modules" in root: continue
        for f in files:
            if f.endswith((".js", ".jsx")):
                path = os.path.join(root, f)
                n = fix_file(path)
                if n:
                    total += n
                    print(f"  ✓ {path}: {n} TDZ fix(es)")
    print(f"\nTotal: {total} TDZ violations resolved")


if __name__ == "__main__":
    main()
