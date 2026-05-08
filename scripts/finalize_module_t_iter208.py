"""iter208 — Final fix: revert factory pattern, use `i18n.t(...)` at module
level so top-level constants Just Work without needing useTranslation().

Steps per file:
  1. Add `import i18n from 'i18next';` after the `react-i18next` import.
  2. Revert `const X = (t) => ({...})` → `const X = { ... };` (and `])`).
  3. Inside the (former-factory) initializer, rewrite every `t('xxx')`
     into `i18n.t('xxx')`.
  4. Revert every `X(t)` consumer back to plain `X`.

This restores the natural object access pattern with zero changes to
React components. Translation is non-reactive on language change but
that's acceptable for status-label maps (a page reload applies new lang).
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
    "/app/frontend/src/pages/QuizChallengesPage.js",
    "/app/frontend/src/pages/SettingsPage.js",
    "/app/frontend/src/pages/admin/WheelFortuneAdminTab.jsx",
    "/app/frontend/src/components/profile/DisplayCurrencySelector.jsx",
]


def transform(src: str) -> tuple[str, list[str]]:
    factories: list[str] = []

    # 1. Find all `const NAME = (t) => ({ ... });` or `= (t) => ([ ... ]);`
    #    Convert each to plain object/array literal AND track NAME.
    def _fix_object(m):
        head = m.group(1)  # "const NAME = "
        name = m.group(2)
        factories.append(name)
        body_open = m.group(3)  # "({" or "(["
        is_obj = body_open == "({"
        return f"{head}{name} = " + ("{" if is_obj else "[")

    # Replace opening: `const NAME = (t) => ({` or `(t) => ([`
    src = re.sub(
        r"((?:export\s+)?(?:const|let|var)\s+)([A-Z][\w$]*)\s*=\s*\(t\)\s*=>\s*(\(\{|\(\[)",
        _fix_object, src,
    )
    if not factories:
        return src, []
    # Now we need to revert the matching closing pattern: `});` → `};`
    # and `]);` → `];`. We do this by walking each factory we converted and
    # finding its corresponding closing pattern. Simpler: replace ALL
    # standalone `});` or `]);` introduced by the previous codemod IFF
    # they're preceded by a balanced object/array starting at one of our
    # factory declarations.
    # Pragmatic shortcut: for each factory NAME, find its declaration line
    # and walk forward to find the matching `})` or `])` at the same depth.
    lines = src.split("\n")

    def replace_close_for(name: str) -> bool:
        # Locate declaration line
        for i, ln in enumerate(lines):
            if re.search(rf"^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(name)}\s*=\s*\{{", ln):
                kind = "obj"
                break
            if re.search(rf"^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(name)}\s*=\s*\[", ln):
                kind = "arr"
                break
        else:
            return False
        # Walk forward, balancing braces/brackets
        depth = 0
        seen_open = False
        for j in range(i, len(lines)):
            ln = lines[j]
            for ch in ln:
                if kind == "obj":
                    if ch == "{":
                        depth += 1; seen_open = True
                    elif ch == "}":
                        depth -= 1
                else:
                    if ch == "[":
                        depth += 1; seen_open = True
                    elif ch == "]":
                        depth -= 1
                if seen_open and depth == 0:
                    # End of literal at line j. Now check if line ends with `});` or `]);`
                    if kind == "obj" and re.search(r"\}\s*\)\s*;\s*$", lines[j]):
                        lines[j] = re.sub(r"\}\s*\)\s*;\s*$", "};", lines[j])
                    elif kind == "arr" and re.search(r"\]\s*\)\s*;\s*$", lines[j]):
                        lines[j] = re.sub(r"\]\s*\)\s*;\s*$", "];", lines[j])
                    return True
        return False

    for name in factories:
        replace_close_for(name)
    src = "\n".join(lines)

    # 2. Inside each former-factory initializer, replace `t('xxx')` with `i18n.t('xxx')`.
    #    We do this conservatively by scanning each top-level const/let/var
    #    decl whose name is in `factories`, isolating its body, and rewriting
    #    `\bt\(` → `i18n.t(` in that body only.
    new_chunks = []
    pos = 0
    for name in factories:
        m = re.search(rf"((?:export\s+)?(?:const|let|var)\s+{re.escape(name)}\s*=\s*[\{{\[])", src[pos:])
        if not m:
            continue
        decl_start = pos + m.start()
        decl_open = pos + m.end()  # right after `{` or `[`
        open_char = src[decl_open - 1]
        close_char = "}" if open_char == "{" else "]"
        # Walk to matching close
        depth = 1
        i = decl_open
        while i < len(src) and depth > 0:
            c = src[i]
            if c == open_char:
                depth += 1
            elif c == close_char:
                depth -= 1
            i += 1
        body_end = i  # past the close char
        head = src[:decl_start]
        body = src[decl_start:body_end]
        tail = src[body_end:]
        # Within body, replace bare t( with i18n.t(
        body_new = re.sub(r"(?<![\w.\$])t\(", "i18n.t(", body)
        src = head + body_new + tail
        pos = decl_start + len(body_new)
    # 3. Globally revert `NAME(t)` → `NAME` for our factory names.
    for name in factories:
        src = re.sub(rf"\b{re.escape(name)}\(t\)", name, src)

    # 4. Ensure `import i18n from 'i18next';` is present.
    if "from 'i18next'" not in src and "from \"i18next\"" not in src:
        # Insert after react-i18next import (or after first react import)
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

    return src, factories


def main():
    for p in TARGETS:
        if not os.path.exists(p):
            print(f"  ! missing {p}")
            continue
        with open(p) as f:
            old = f.read()
        new, facs = transform(old)
        if new != old:
            with open(p, "w") as f:
                f.write(new)
            print(f"  ✓ {os.path.basename(p):<45} reverted {len(facs)} factories ({', '.join(facs)})")
        else:
            print(f"  - {os.path.basename(p):<45} no changes")


if __name__ == "__main__":
    main()
