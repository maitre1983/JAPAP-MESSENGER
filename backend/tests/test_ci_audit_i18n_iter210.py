"""iter210 — regression test for the i18n CI audit script.
Run with: pytest backend/tests/test_ci_audit_i18n_iter210.py -v
"""
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path("/app/scripts/ci_audit_i18n.py")


def _run(root: Path) -> tuple[int, str]:
    res = subprocess.run(
        ["python3", str(SCRIPT), "--root", str(root)],
        capture_output=True, text=True,
    )
    return res.returncode, res.stdout + res.stderr


def test_clean_project_exits_zero():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Clean file with proper pattern
        (root / "Comp.jsx").write_text("""
import { useTranslation } from 'react-i18next';
export default function Comp() {
  const { t } = useTranslation();
  return <div>{t('hello')}</div>;
}
""")
        code, out = _run(root)
    assert code == 0, out


def test_missing_import_fails_audit_a():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Bad.jsx").write_text("""
function Bad() { return <div>{t('foo')}</div>; }
""")
        code, out = _run(root)
    assert code == 1, out
    assert "Fichiers t() sans import i18n" in out
    assert "1" in out


def test_module_level_t_fails_audit_b():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Has import (passes A) but calls t() at module level (fails B)
        (root / "Bad.jsx").write_text("""
import { useTranslation } from 'react-i18next';
const BAD_LABEL = t('status.active');
""")
        code, out = _run(root)
    assert code == 2, out
    assert "t() au niveau module" in out


def test_broken_function_fails_audit_c():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Bad.jsx").write_text("""
import { useTranslation } from 'react-i18next';
function SubComp() {
  return <div>{t('will.crash')}</div>;
}
""")
        code, out = _run(root)
    assert code == 3, out
    assert "Fonctions cassées" in out
    assert "SubComp" in out


def test_i18n_dot_t_at_module_level_is_allowed():
    """i18n.t() is legitimate and must NOT be flagged."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Ok.jsx").write_text("""
import i18n from 'i18next';
const LABEL = i18n.t('foo.bar');
""")
        code, out = _run(root)
    assert code == 0, out


def test_t_as_parameter_is_allowed():
    """Factory function receiving t as param must NOT be flagged."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Ok.jsx").write_text("""
import { useTranslation } from 'react-i18next';
const getLabels = (t) => ({ active: t('status.active') });
function Comp() {
  const { t } = useTranslation();
  return <div>{getLabels(t).active}</div>;
}
""")
        code, out = _run(root)
    assert code == 0, out


def test_inherited_t_from_ancestor_is_allowed():
    """Inner helper can reference `t` via closure if an ancestor has it."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Ok.jsx").write_text("""
import { useTranslation } from 'react-i18next';
function Outer() {
  const { t } = useTranslation();
  const helper = () => t('closure.ok');
  return <div>{helper()}</div>;
}
""")
        code, out = _run(root)
    assert code == 0, out


def test_real_japap_src_is_clean():
    """Regression sentinel: the real /app/frontend/src must be green."""
    real_root = Path("/app/frontend/src")
    if not real_root.exists():
        return  # skip if not running in JAPAP repo
    code, out = _run(real_root)
    assert code == 0, (
        "Real /app/frontend/src has i18n violations — "
        "this is a live production regression.\n\n" + out
    )
