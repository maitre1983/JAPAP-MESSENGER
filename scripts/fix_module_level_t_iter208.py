"""iter208 — Fix module-level t() calls (broken from iter205/206 codemod).

Strategy: convert each problematic top-level `const X = { ... t('xxx') ... }`
into a factory function `const X = (t) => ({ ... t('xxx') ... })`, then
update every usage site `X[k]` / `X.k` to `X(t)[k]` / `X(t).k`.

For the rare case where a top-level CONST is referenced from another file,
this would break — but our audit showed all problematic constants are
file-local (used only inside the same component file).
"""
import os
import re
import sys

PROBLEM_FILES = [
    "/app/frontend/src/pages/MarketplaceAdsPage.js",
    "/app/frontend/src/pages/MarketplaceProductPage.js",
    "/app/frontend/src/pages/AdsUserPage.js",
    "/app/frontend/src/pages/PublicRideTrackPage.js",
    "/app/frontend/src/pages/admin/AdminErrorMonitorTab.jsx",
    "/app/frontend/src/pages/admin/AdsAdminTab.jsx",
    "/app/frontend/src/pages/admin/TransportPricingAdminTab.jsx",
    "/app/frontend/src/pages/admin/SupportAdminTab.jsx",
    "/app/frontend/src/pages/admin/PaymentsAdminTab.jsx",
    "/app/frontend/src/components/TranslateButton.jsx",
    "/app/frontend/src/components/wallet/NowPaymentsDepositCard.jsx",
    "/app/frontend/src/components/transport/RideLifecyclePanel.jsx",
    "/app/frontend/src/components/transport/DriverKycForm.jsx",
]


def find_problem_constants(src: str) -> list[str]:
    """Return list of top-level const names whose initializer contains a t() call."""
    out = []
    lines = src.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # Top-level const/let/var declaration
        m = re.match(r"^(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", line)
        if not m:
            i += 1
            continue
        # Collect block until balanced braces / ;
        decl_start = i
        depth = 0
        chunk = []
        for j in range(i, len(lines)):
            ln = lines[j]
            chunk.append(ln)
            depth += ln.count("{") + ln.count("(") + ln.count("[")
            depth -= ln.count("}") + ln.count(")") + ln.count("]")
            if depth <= 0 and (ln.rstrip().endswith(";") or ln.rstrip().endswith("}") or ln.rstrip().endswith(")")):
                break
        block = "\n".join(chunk)
        if re.search(r"\bt\(['`]", block):
            out.append((m.group(2), decl_start, decl_start + len(chunk)))
        i = decl_start + len(chunk)
    return out


def transform_file(path: str) -> tuple[int, list[str]]:
    with open(path) as f:
        src = f.read()
    consts = find_problem_constants(src)
    if not consts:
        return 0, []
    lines = src.split("\n")
    edits = 0
    transformed_names = []

    # 1. Convert each top-level const initializer to a factory.
    # Process in reverse so line numbers remain stable.
    for name, start, end in reversed(consts):
        decl = "\n".join(lines[start:end])
        # Match `const NAME = { ... }` or `const NAME = [ ... ]`
        new_decl = re.sub(
            rf"^(const|let|var)\s+({re.escape(name)})\s*=\s*\{{",
            r"\1 \2 = (t) => ({",
            decl,
        )
        if new_decl != decl:
            # Replace closing `};` (or just `}`) at same depth
            # Find last `}` followed by optional `;` and replace with `});`.
            new_decl = re.sub(r"\}\s*;\s*$", "});", new_decl)
            # Special: handle the case where decl is single-line and ends with `}`
            if new_decl == decl or not re.search(r"\}\s*\)\s*;", new_decl):
                new_decl = re.sub(r"\}(?=\s*$)", "})", new_decl)
            lines[start:end] = new_decl.split("\n")
            transformed_names.append(name)
            edits += 1
            continue
        # Maybe array initializer: const X = [ ... ]
        new_decl = re.sub(
            rf"^(const|let|var)\s+({re.escape(name)})\s*=\s*\[",
            r"\1 \2 = (t) => ([",
            decl,
        )
        if new_decl != decl:
            new_decl = re.sub(r"\]\s*;\s*$", "]);", new_decl)
            lines[start:end] = new_decl.split("\n")
            transformed_names.append(name)
            edits += 1

    if not transformed_names:
        return 0, []

    src_new = "\n".join(lines)

    # 2. Update every usage of NAME (read access) to NAME(t).
    # Heuristic: replace `NAME[` / `NAME.` / `Object.keys(NAME)` etc., but
    # ONLY if it's not the declaration line itself.
    # We avoid touching the declaration we just made: `const NAME = (t) =>`.
    for name in transformed_names:
        # We must not touch:
        #   - `const NAME = (t) =>`
        # Strategy: match boundary `\bNAME\b` then replace with `NAME(t)`.
        # Skip occurrences immediately followed by ` =` (assignment) or that
        # are part of import lines.
        def repl(m, _n=name):
            # Skip if part of LHS assignment / decl
            full = m.string
            after = full[m.end():m.end() + 4]
            if re.match(r"\s*=\s*[^=]", after):  # `NAME = ...`
                return m.group(0)
            if re.match(r"\s*\(t\)", after):  # already converted: `NAME(t)`
                return m.group(0)
            return f"{_n}(t)"

        # First pass: lookbehind to ensure NAME is not preceded by import noise
        # We'll use a careful regex.
        pattern = re.compile(rf"(?<![\w$.]){re.escape(name)}\b")
        # Walk line by line so we can keep declaration intact.
        new_lines = []
        for ln in src_new.split("\n"):
            # Skip the modified declaration line itself
            if re.search(rf"\b(const|let|var)\s+{re.escape(name)}\s*=\s*\(t\)\s*=>", ln):
                new_lines.append(ln)
                continue
            new_lines.append(pattern.sub(repl, ln))
        src_new = "\n".join(new_lines)

    if src_new != src:
        with open(path, "w") as f:
            f.write(src_new)
    return edits, transformed_names


def main():
    total = 0
    for path in PROBLEM_FILES:
        if not os.path.exists(path):
            print(f"  ! missing: {path}")
            continue
        n, names = transform_file(path)
        if n:
            print(f"  ✓ {path}: {n} const(s) → factory(t): {', '.join(names)}")
            total += n
        else:
            print(f"  - {path}: no changes")
    print(f"\nTotal: {total} top-level constants converted to (t)=> factories")


if __name__ == "__main__":
    main()
