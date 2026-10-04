import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ed import txn

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / 'tests' / 'fixture'
EXAMPLES = ROOT / 'tests' / 'examples'


@pytest.fixture
def repo(tmp_path, monkeypatch):
    d = tmp_path / 'repo'
    shutil.copytree(FIXTURE, d, symlinks=True)
    (d / '.git').mkdir()
    monkeypatch.chdir(d)
    monkeypatch.setenv('PATH', f"{d / 'bin'}{os.pathsep}{os.environ['PATH']}")
    for k in ('ED_DRY', 'ED_JSON', 'ED_DIFF_LINES'):
        monkeypatch.delenv(k, raising=False)
    txn.reset()
    yield d
    txn.reset()


@pytest.fixture
def tmp(tmp_path, monkeypatch):
    """An empty working directory for small hand-written files."""
    d = tmp_path / 'w'
    d.mkdir()
    (d / '.git').mkdir()
    monkeypatch.chdir(d)
    for k in ('ED_DRY', 'ED_JSON', 'ED_DIFF_LINES'):
        monkeypatch.delenv(k, raising=False)
    txn.reset()
    yield d
    txn.reset()


def write(path, text, mode='w', newline=''):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        with open(p, mode, encoding='utf-8', newline=newline) as f:
            f.write(text)
    return p


def read(path):
    return Path(path).read_bytes().decode('utf-8')


def commit():
    """Commit the pending in-process transaction (what atexit does for scripts)."""
    try:
        return txn.current().commit()
    finally:
        txn.reset()


def run(cwd, code=None, script=None, args=(), env=None):
    """Run an ed script in a subprocess, exactly as an agent would."""
    if script is None:
        script = Path(cwd).parent / 'script.py'
        script.write_text(textwrap.dedent(code))
    e = dict(os.environ)
    e['PYTHONPATH'] = str(ROOT)
    e['PATH'] = f"{Path(cwd) / 'bin'}{os.pathsep}{e['PATH']}"
    for k in ('ED_DRY', 'ED_JSON', 'ED_DIFF_LINES'):
        e.pop(k, None)
    e.update(env or {})
    return subprocess.run([sys.executable, str(script), *args], cwd=cwd, env=e,
                          capture_output=True, text=True)


def snapshot(d):
    d = Path(d)
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob('*'))
            if p.is_file() and '.ed' not in p.relative_to(d).parts and '.git' not in p.relative_to(d).parts}
