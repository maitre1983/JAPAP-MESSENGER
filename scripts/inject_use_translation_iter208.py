"""iter208 — Phase B (v2): inject `const { t } = useTranslation();` into
every React function component that uses one of the (t)=> factories.

Robust approach: walk through the file, locate every `function Foo(...) {`
or `const Foo = (...) => {` header where Foo starts uppercase. Walk the
brace-balanced body, and if it references any factory call but lacks a
`useTranslation()` line, insert one right after the opening `{`.
"""
import os
import re

TARGETS = [
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


def find_factory_names(src: str) -> list[str]:
    return list(set(
        re.findall(r"^(?:const|let|var|export\s+const|export\s+default\s+const)\s+([A-Z][\w$]*)\s*=\s*\(t\)\s*=>", src, re.M)
    ))


def find_brace_end(src: str, open_idx: int) -> int:
    """Naive brace matcher (tolerates strings)."""
    depth = 0
    in_str = None
    in_tpl = False
    in_line_cmt = False
    in_blk_cmt = False
    i = open_idx
    while i < len(src):
        ch = src[i]
        nxt = src[i+1] if i+1 < len(src) else ""
        if in_line_cmt:
            if ch == "\n":
                in_line_cmt = False
            i += 1; continue
        if in_blk_cmt:
            if ch == "*" and nxt == "/":
                in_blk_cmt = False
                i += 2; continue
            i += 1; continue
        if in_str:
            if ch == "\\":
                i += 2; continue
            if ch == in_str:
                in_str = None
            i += 1; continue
        if in_tpl:
            if ch == "\\":
                i += 2; continue
            if ch == "`":
                in_tpl = False
            i += 1; continue
        if ch == "/" and nxt == "/":
            in_line_cmt = True; i += 2; continue
        if ch == "/" and nxt == "*":
            in_blk_cmt = True; i += 2; continue
        if ch in ("'", '"'):
            in_str = ch; i += 1; continue
        if ch == "`":
            in_tpl = True; i += 1; continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def find_component_bodies(src: str) -> list[tuple[str, int, int]]:
    """Return list of (name, body_open_idx, body_close_idx) for every
    function component declaration. body_open_idx points to `{`."""
    out = []

    # 1. function Name(...)
    for m in re.finditer(r"\b(?:export\s+default\s+)?function\s+([A-Z][\w$]*)\s*\(", src):
        # skip past matching `)` then to `{`
        i = m.end()
        depth = 1
        while i < len(src) and depth > 0:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        # Now i is right after `)`; skip whitespace then expect `{`
        while i < len(src) and src[i] in " \t\r\n":
            i += 1
        if i < len(src) and src[i] == "{":
            end = find_brace_end(src, i)
            if end > 0:
                out.append((m.group(1), i, end))

    # 2. const Name = (...) => { … } or = props => {…}
    for m in re.finditer(r"\b(?:export\s+default\s+|export\s+)?(?:const|let|var)\s+([A-Z][\w$]*)\s*=\s*", src):
        name = m.group(1)
        i = m.end()
        # accept React.memo(...) → recurse one level
        # We just need to find the closest `=>` followed by `{`
        # Limit search to next 800 chars to avoid O(n^2)
        slice_ = src[i:i + 800]
        a = slice_.find("=>")
        if a < 0:
            continue
        j = i + a + 2
        while j < len(src) and src[j] in " \t\r\n":
            j += 1
        if j < len(src) and src[j] == "{":
            end = find_brace_end(src, j)
            if end > 0:
                out.append((name, j, end))
    return out


def patch_file(path: str) -> dict:
    with open(path) as f:
        src = f.read()
    factories = find_factory_names(src)
    if not factories:
        return {"factories": 0}

    if "useTranslation" not in src:
        src = re.sub(
            r"(import\s+[^;]*from\s+['\"]react['\"];?\s*)",
            r"\1\nimport { useTranslation } from 'react-i18next';\n",
            src, count=1,
        )

    comps = find_component_bodies(src)
    if not comps:
        with open(path, "w") as f:
            f.write(src)
        return {"factories": len(factories), "components": 0, "patched": 0}

    # Identify which components need patching (in original src for stability;
    # we apply edits in reverse order on the indices).
    fac_pat = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in factories) + r")\(t\)")
    has_ut_pat = re.compile(r"const\s*\{\s*[^}]*\bt\b[^}]*\}\s*=\s*useTranslation\s*\(")

    edits = []
    for name, s, e in comps:
        body = src[s + 1:e]
        if not fac_pat.search(body):
            continue
        if has_ut_pat.search(body):
            continue
        # We must also avoid double-injection if two component declarations
        # overlap (e.g. inner function inside outer).
        edits.append((s + 1, name))

    # Sort by index desc so insert offsets remain stable.
    edits.sort(key=lambda x: -x[0])
    patched = 0
    for idx, name in edits:
        injection = "\n  const { t } = useTranslation();"
        src = src[:idx] + injection + src[idx:]
        patched += 1

    with open(path, "w") as f:
        f.write(src)
    return {"factories": len(factories), "components": len(comps), "patched": patched}


def main():
    for p in TARGETS:
        if not os.path.exists(p):
            print(f"  ! missing {p}")
            continue
        info = patch_file(p)
        print(f"  {os.path.basename(p):<45} {info}")


if __name__ == "__main__":
    main()
