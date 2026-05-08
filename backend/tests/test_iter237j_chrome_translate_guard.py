"""iter237j — Regression test: Chrome auto-translate guards must stay in
public/index.html. Removing them would re-introduce the production crash
"Failed to execute 'insertBefore' on 'Node'" reported on japapmessenger.com
for users in India who let Chrome translate FR→EN.

We assert presence of:
  • <html lang="fr" translate="no">
  • <meta name="google" content="notranslate"> in <head>
  • <div id="root" ... translate="no" ... class="notranslate">
"""
from pathlib import Path
import re

INDEX_HTML = Path("/app/frontend/public/index.html")


def test_index_html_has_translate_no_on_html_root():
    src = INDEX_HTML.read_text(encoding="utf-8")
    assert re.search(r'<html\b[^>]*\btranslate="no"', src), \
        '<html> must carry translate="no" to disable Chrome auto-translate'


def test_index_html_has_google_notranslate_meta():
    src = INDEX_HTML.read_text(encoding="utf-8")
    assert re.search(r'<meta\s+name="google"\s+content="notranslate"', src), \
        '<meta name="google" content="notranslate"> required to suppress ' \
        "the Chrome 'Translate this page?' prompt that breaks React reconciliation"


def test_index_html_root_is_notranslate():
    src = INDEX_HTML.read_text(encoding="utf-8")
    # Match `<div id="root" … translate="no" … class="notranslate" …>` in any
    # attribute order.
    m = re.search(r'<div[^>]*id="root"[^>]*>', src)
    assert m, '<div id="root"> not found in index.html'
    div = m.group(0)
    assert 'translate="no"' in div, \
        '#root must carry translate="no" so Chrome skips its subtree'
    assert 'notranslate' in div, \
        '#root must carry the notranslate class as a belt-and-suspenders signal'


def test_publicchallengepage_uses_login_route_not_signin():
    """iter237j — Same iteration also fixed the broken /signin redirect that
    sent users from /c/{cid} into /feed via the wildcard, dropping the
    deep-link intent. Make sure nobody re-introduces /signin in active code.
    (Comments mentioning /signin to document the old bug are OK.)"""
    src = Path("/app/frontend/src/pages/PublicChallengePage.js").read_text(encoding="utf-8")
    # Strip JS line comments so we only check executable code.
    code_only = re.sub(r"//.*", "", src)
    assert "/signin" not in code_only, \
        "/signin is not a registered route (App.js exposes /login). Use " \
        "navigate('/login', { state: { from: `/c/${cid}` } }) so the " \
        "post-login redirectTo brings the user back to the challenge."
    assert "navigate('/login'" in code_only, \
        "Expected navigate('/login', { state: { from: `/c/...` } }) in claim()"
