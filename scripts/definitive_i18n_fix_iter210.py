"""iter210 — DEFINITIVE i18n fix.

Strategy:
  1. For every function/arrow that uses bare `t(...)` but doesn't declare
     `const { t } = useTranslation();` AND doesn't receive `t` as a
     parameter, INJECT the hook call at the top of the body.
  2. Convert top-level `i18n.t(...)` constants from iter208 into the factory
     pattern (`const getX = (t) => ({...t(...)...})`) and update consumers.
  3. Ensure every file that uses `useTranslation` imports it.

This is the CEO-mandated fix. No `i18n.t()` at module level. Every `t(...)`
call sits inside a function that has access to the hook's `t`.
"""
import os
import re

TARGETS_ROOT = "/app/frontend/src"


# ─────────────────────────────────────────────────────────────────────
# Phase 1: inject `useTranslation()` into broken functions.
# ─────────────────────────────────────────────────────────────────────
def find_function_bodies(src):
    """Yield (name, params_str, body_open_idx, body_close_idx)."""
    out = []
    # function NAME(...) { body }
    for m in re.finditer(r'\b(?:export\s+(?:default\s+)?)?function\s+(\w+)\s*\(', src):
        params_start = m.end()
        i = params_start
        depth = 1
        while i < len(src) and depth > 0:
            if src[i] == '(': depth += 1
            elif src[i] == ')': depth -= 1
            i += 1
        params = src[params_start:i-1]
        while i < len(src) and src[i] in ' \t\r\n':
            i += 1
        if i < len(src) and src[i] == '{':
            d = 0
            j = i
            while j < len(src):
                if src[j] == '{': d += 1
                elif src[j] == '}':
                    d -= 1
                    if d == 0:
                        out.append((m.group(1), params, i, j))
                        break
                j += 1

    # const NAME = (...) => { body } or `= props => {`
    for m in re.finditer(
        r'\b(?:const|let|var)\s+(\w+)\s*=\s*(\([^)]*\)|\w+)\s*=>\s*\{',
        src,
    ):
        params = m.group(2)
        if params.startswith('(') and params.endswith(')'):
            params = params[1:-1]
        i = m.end() - 1
        d = 0
        j = i
        while j < len(src):
            if src[j] == '{': d += 1
            elif src[j] == '}':
                d -= 1
                if d == 0:
                    out.append((m.group(1), params, i, j))
                    break
            j += 1
    return out


def has_use_translation_in_body(body):
    return bool(re.search(
        r'const\s*\{\s*[^}]*\bt\b[^}]*\}\s*=\s*useTranslation\s*\(',
        body,
    ))


def t_is_in_params(params):
    return bool(re.search(r'\bt\b', params))


def uses_bare_t(body):
    return bool(re.search(r'(?<![\w.\$])t\(', body))


def ensure_use_translation_import(src):
    if "useTranslation" in src and "react-i18next" in src:
        return src
    # add after the first react-related import
    if re.search(r"import\s+[^;]*from\s+['\"]react['\"]", src):
        return re.sub(
            r"(import\s+[^;]*from\s+['\"]react['\"];?)",
            r"\1\nimport { useTranslation } from 'react-i18next';",
            src, count=1,
        )
    # Otherwise prepend
    return "import { useTranslation } from 'react-i18next';\n" + src


def remove_i18next_import(src):
    """Remove `import i18n from 'i18next';` lines."""
    return re.sub(
        r"^\s*import\s+i18n\s+from\s+['\"]i18next['\"];?\s*\n",
        "",
        src, flags=re.M,
    )


def phase1_inject(src: str) -> tuple[str, int]:
    """Inject const { t } = useTranslation(); into every broken function."""
    bodies = find_function_bodies(src)
    # Compute injections in original src indices (no overlap; nested bodies
    # are children of outer bodies so we do them all simultaneously by
    # reverse-sorting the open indices).
    injections = []
    for name, params, body_open, body_close in bodies:
        body = src[body_open + 1:body_close]
        if not uses_bare_t(body):
            continue
        if has_use_translation_in_body(body):
            continue
        if t_is_in_params(params):
            continue
        injections.append((body_open + 1, name))

    # Sort by index desc — apply injections from end to keep offsets valid.
    injections.sort(key=lambda x: -x[0])
    for idx, _name in injections:
        src = (
            src[:idx]
            + "\n  const { t } = useTranslation();"
            + src[idx:]
        )
    return src, len(injections)


# ─────────────────────────────────────────────────────────────────────
# Phase 2: convert top-level `i18n.t(...)` constants to factory pattern.
# ─────────────────────────────────────────────────────────────────────
def find_top_level_decls_with_i18n_t(src):
    """Return list of (name, decl_start_line_idx, decl_end_line_idx, kind)
    for top-level `const NAME = { ... i18n.t(...) ... };` (and arrays).
    """
    out = []
    lines = src.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^((?:export\s+)?(?:const|let|var))\s+([A-Za-z_$][\w$]*)\s*=\s*([\{\[])", ln)
        if not m:
            i += 1; continue
        name = m.group(2)
        opener = m.group(3)
        closer = "}" if opener == "{" else "]"
        depth = 0
        seen = False
        for j in range(i, len(lines)):
            for ch in lines[j]:
                if ch == opener:
                    depth += 1; seen = True
                elif ch == closer:
                    depth -= 1
            if seen and depth == 0:
                break
        else:
            i += 1; continue
        block = "\n".join(lines[i:j+1])
        if re.search(r"\bi18n\.t\(", block):
            out.append((name, i, j + 1, opener))
        i = j + 1
    return out


def phase2_factorize(src: str) -> tuple[str, list[str]]:
    """Convert top-level i18n.t constants into (t)=> factories."""
    decls = find_top_level_decls_with_i18n_t(src)
    if not decls:
        return src, []
    lines = src.split("\n")
    factory_names = []
    for name, start, end, opener in reversed(decls):
        block = "\n".join(lines[start:end])
        # Replace the opening `const NAME = {` with `const getNAME = (t) => ({`
        new_name = "get" + name[0].upper() + name[1:].lower() if name.isupper() else (
            "get" + name[0].upper() + name[1:]
        )
        # Avoid double 'get'
        if new_name == name:
            new_name = name + "_"
        if opener == "{":
            new_block = re.sub(
                rf"^((?:export\s+)?(?:const|let|var))\s+{re.escape(name)}\s*=\s*\{{",
                rf"\1 {new_name} = (t) => ({{",
                block,
            )
            new_block = re.sub(r"\}\s*;\s*$", "});", new_block)
        else:
            new_block = re.sub(
                rf"^((?:export\s+)?(?:const|let|var))\s+{re.escape(name)}\s*=\s*\[",
                rf"\1 {new_name} = (t) => ([",
                block,
            )
            new_block = re.sub(r"\]\s*;\s*$", "]);", new_block)
        # Inside the block, replace `i18n.t(` with `t(`.
        new_block = re.sub(r"\bi18n\.t\(", "t(", new_block)
        lines[start:end] = new_block.split("\n")
        factory_names.append((name, new_name))
    src = "\n".join(lines)

    # Now update every consumer of NAME → getNAME(t).
    # We rename usages site-by-site, but ONLY where `t` is in scope.
    # Heuristic: for each function body that uses NAME and has access to t
    # (either via useTranslation or inherited closure), insert
    # `const NAME = getNAME(t);` at the top, instead of replacing every site.
    bodies = find_function_bodies(src)
    bodies.sort(key=lambda x: -x[2])  # outer-most last so we inject inner-most first

    # We want to inject at the start of each body that references any of the
    # renamed names. But we only inject ONCE per body per name.
    src_new = src
    pattern_for_old_names = re.compile(
        r'\b(' + '|'.join(re.escape(o) for o, _ in factory_names) + r')\b'
    )

    inserted_log = set()  # (body_open_idx, old_name)

    # We re-find bodies on the rebuilt src each iteration to keep indices valid.
    def reapply():
        return find_function_bodies(src_new)

    for old_name, factory in factory_names:
        # For each body that USES `old_name`, inject:
        #   `const old_name = factory(t);` after the opening `{`
        # (the body's `t` is either in params or via useTranslation).
        bodies = reapply()
        bodies.sort(key=lambda x: -x[2])  # process inner functions first (higher idx)
        for body_name, params, bopen, bclose in bodies:
            body = src_new[bopen + 1:bclose]
            # Skip if body doesn't reference the old name
            if not re.search(rf'\b{re.escape(old_name)}\b', body):
                continue
            # Skip if body is the factory's own definition (top-level)
            if re.search(rf'\b{re.escape(factory)}\s*=\s*\(t\)', body):
                continue
            # Skip if already has the binding
            if re.search(rf'const\s+{re.escape(old_name)}\s*=\s*{re.escape(factory)}\(t\)', body):
                continue
            # Ensure t is available in this body
            t_available = (
                t_is_in_params(params)
                or has_use_translation_in_body(body)
                or _ancestor_has_t(src_new, bopen)
            )
            if not t_available:
                # try to inject useTranslation if this looks like a React component
                # i.e., body returns JSX (heuristic: body contains `return (` or `<` near top)
                # For safety just inject useTranslation regardless.
                src_new = (
                    src_new[:bopen + 1]
                    + "\n  const { t } = useTranslation();"
                    + src_new[bopen + 1:]
                )
                # Recompute body indices on the fly: easiest to break and restart.
                break
            # Insert the binding.
            injection = f"\n  const {old_name} = {factory}(t);"
            src_new = src_new[:bopen + 1] + injection + src_new[bopen + 1:]
            inserted_log.add((bopen, old_name))
            break  # restart loop with updated indices to avoid stale offsets
        # Continue to next factory_name; bodies are recomputed.

    return src_new, [n for n, _ in factory_names]


def _ancestor_has_t(src, idx):
    """Cheap check: walk up the source from idx and see if there's a
    `useTranslation()` declaration in any enclosing function body."""
    above = src[:idx]
    return bool(re.search(
        r'const\s*\{\s*[^}]*\bt\b[^}]*\}\s*=\s*useTranslation\s*\(',
        above,
    ))


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────
def transform_file(path: str) -> dict:
    with open(path) as f:
        src = f.read()
    orig = src

    # PHASE 1: inject useTranslation() into broken functions.
    src, n_injected = phase1_inject(src)

    # PHASE 2: factorize top-level i18n.t constants.
    src, factories = phase2_factorize(src)

    if factories:
        # Drop `import i18n from 'i18next';`
        src = remove_i18next_import(src)

    # Always ensure useTranslation import if anywhere we injected the hook.
    if n_injected > 0 or factories:
        src = ensure_use_translation_import(src)

    if src != orig:
        with open(path, "w") as f:
            f.write(src)
    return {"injections": n_injected, "factories": factories}


def main():
    targets = []
    for root, dirs, files in os.walk(TARGETS_ROOT):
        if 'node_modules' in root: continue
        for f in files:
            if f.endswith(('.js', '.jsx')):
                targets.append(os.path.join(root, f))

    total_inj = 0
    total_fac = 0
    changed = 0
    for p in targets:
        info = transform_file(p)
        if info["injections"] > 0 or info["factories"]:
            changed += 1
            total_inj += info["injections"]
            total_fac += len(info["factories"])
            facs = ', '.join(f"{o}→{n}" for o, n in [
                (orig, "get" + (orig[0].upper() + orig[1:].lower() if orig.isupper() else orig[0].upper() + orig[1:]))
                for orig in info["factories"]
            ]) if info["factories"] else ""
            print(f"  ✓ {p}: +{info['injections']} useTranslation injections{', factories=[' + str(info['factories']) + ']' if info['factories'] else ''}")
    print(f"\nTotal: {changed} files updated · {total_inj} hook injections · {total_fac} factories created")


if __name__ == "__main__":
    main()
