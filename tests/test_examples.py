"""Acceptance: every worked example in docs/design.md runs as written against the fixture repo."""
import re

import pytest

from .conftest import EXAMPLES, ROOT, read, run, snapshot

EXAMPLE_FILES = {1: 'ex1_logger.py', 2: 'ex2_move.py', 3: 'ex3_css.py', 4: 'ex4_regions.py',
                 5: 'ex5_oneliners.py'}


def doc_examples():
    doc = (ROOT / 'docs' / 'design.md').read_text()
    body = doc[doc.index('## Worked examples'):doc.index('## Implementation plan')]
    out = {}
    for m in re.finditer(r'### (\d)\..*?```python\n(.*?)```', body, re.S):
        out[int(m.group(1))] = m.group(2)
    return out


@pytest.mark.skipif(not (ROOT / 'docs' / 'design.md').exists(), reason='design doc not in this checkout')
@pytest.mark.parametrize('n', [1, 2, 3, 4, 5])
def test_example_files_match_the_doc(n):
    code = (EXAMPLES / EXAMPLE_FILES[n]).read_text()
    doc = doc_examples()[n]
    if not doc.startswith('from ed import *'):
        doc = 'from ed import *\n' + doc
    assert code == doc


def ex(repo, n, **kw):
    return run(repo, script=EXAMPLES / EXAMPLE_FILES[n], **kw)


def test_ex1_logger_migration(repo):
    before = snapshot(repo)
    p = ex(repo, 1)
    assert p.returncode == 0, p.stderr
    assert 'ensure ok: tsc --noEmit' in p.stdout
    client = read(repo / 'src/api/client.ts')
    assert client.startswith("import { logger } from '@/lib/logger'\nimport { formatDate")
    assert 'console.log(' not in client and client.count('logger.debug(') == 2
    assert read(repo / 'src/components/Header.tsx').startswith("import { logger }")
    # excluded and non-matching files are untouched
    for f in ('src/workers/sync.ts', 'src/lib/logger.ts', 'src/components/Footer.tsx'):
        assert (repo / f).read_bytes() == before[f]
    # BOM + CRLF file keeps both, including on the inserted line
    raw = (repo / 'src/legacy/bom.ts').read_bytes()
    assert raw.startswith(b"\xef\xbb\xbfimport { logger } from '@/lib/logger'\r\nimport { now }")
    assert b'logger.debug("tick", now())\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b'')


def test_ex1_rerun_is_a_no_op(repo):
    assert ex(repo, 1).returncode == 0
    once = snapshot(repo)
    p = ex(repo, 1)
    assert p.returncode == 0, p.stderr
    assert snapshot(repo) == once
    assert '---' not in p.stdout


def test_ex1_matches_the_plain_python_version(repo, tmp_path):
    import shutil
    other = tmp_path / 'plain'
    shutil.copytree(repo, other)
    assert ex(repo, 1).returncode == 0
    p = run(other, script=EXAMPLES / 'ex1_plain.py')
    assert p.returncode == 0, p.stderr
    a, b = snapshot(repo), snapshot(other)
    differ = sorted(k for k in a if a[k] != b.get(k))
    # the only difference: plain Python loses the BOM file's CRLF line endings
    assert differ == ['src/legacy/bom.ts']
    assert b'\r\n' not in b['src/legacy/bom.ts'] and b'\r\n' in a['src/legacy/bom.ts']


def test_ex1_rolls_back_when_tsc_fails(repo):
    (repo / 'src/lib/logger.ts').write_text('export const notTheLogger = 1\n')
    before = snapshot(repo)
    p = ex(repo, 1)
    assert p.returncode == 1
    assert "Module '@/lib/logger' has no exported member 'logger'" in p.stderr
    assert 'rolled back' in p.stderr
    assert snapshot(repo) == before


def test_ex2_move_function_and_fix_imports(repo):
    p = ex(repo, 2)
    assert p.returncode == 0, p.stdout + p.stderr
    date = read(repo / 'src/utils/date.ts')
    assert date == "export function parseDate(s: string): Date {\n  return new Date(s)\n}\n"
    time_ = read(repo / 'src/lib/time.ts')
    assert time_.endswith("}\n\nexport function formatDate(d: Date): string {\n  const y = d.getFullYear()\n"
                          "  const m = String(d.getMonth() + 1).padStart(2, '0')\n  return `${y}-${m}`\n}\n")
    assert read(repo / 'src/api/client.ts').startswith(
        "import { parseDate } from '@/utils/date'\nimport { formatDate } from '@/lib/time'\n\n")
    assert read(repo / 'src/components/Header.tsx').startswith("import { formatDate } from '@/lib/time'\n\n")


def test_ex2_rerun_fails_closed(repo):
    assert ex(repo, 2).returncode == 0
    once = snapshot(repo)
    p = ex(repo, 2)
    assert p.returncode == 1
    assert "'export function formatDate' not found in src/utils/date.ts" in p.stderr
    assert 'no files changed' in p.stderr
    assert snapshot(repo) == once


def test_ex3_regex_inside_blocks(repo):
    p = ex(repo, 3)
    assert p.returncode == 0, p.stderr
    css = read(repo / 'src/styles/card.css')
    assert 'padding: 1rem;' in css and 'margin: 1.5rem 0.5rem;' in css
    assert 'border: 0.0625rem solid #ccc;' in css and 'gap: 0.75rem;' in css
    assert 'font-size: 20px;' in css  # .card-title is not a .card block
    once = snapshot(repo)
    assert ex(repo, 3).returncode == 0 and snapshot(repo) == once


def test_ex4_marker_regions(repo):
    p = ex(repo, 4)
    assert p.returncode == 0, p.stderr
    assert read(repo / 'src/router.tsx') == (
        "// imports:start\n"
        "import About from './pages/About'\nimport Contact from './pages/Contact'\nimport Home from './pages/Home'\n"
        "// imports:end\n\nexport const routes = [\n  // routes:start\n"
        "  { path: '/about', element: <About /> },\n  { path: '/contact', element: <Contact /> },\n"
        "  { path: '/home', element: <Home /> },\n  // routes:end\n]\n")
    once = snapshot(repo)
    p = ex(repo, 4)
    assert p.returncode == 0 and snapshot(repo) == once and 'ed: no changes' in p.stdout


def test_ex5_one_liners(repo):
    p = ex(repo, 5)
    assert p.returncode == 0, p.stderr
    assert read(repo / 'app/page.tsx').startswith("'use client'\nimport { useState }")
    assert read(repo / 'app/layout.tsx').startswith('export default')  # no hooks
    assert read(repo / 'app/settings/page.tsx').count("'use client'") == 1  # already there
    dialog = read(repo / 'src/components/Dialog.tsx')
    assert 'const open = true' in dialog and '<Modal open={open}' in dialog and 'isOpen' not in dialog
    client = read(repo / 'src/api/client.ts')
    assert '@debug' not in client and 'legacyFetch' not in client
    assert '}\n\nexport function stamp' in client  # no doubled blank line left behind
    assert (repo / '.env').read_bytes() == b'API_URL=https://example.test\nDEBUG=1\nSENTRY_DSN='
    assert (repo / '.env.example').read_bytes() == b'API_URL=\r\nDEBUG=\r\nSENTRY_DSN=\r\n'
    assert read(repo / 'src/index.ts') == (
        "// exports:start\nexport { Dialog } from './components/Dialog'\n"
        "export { Footer } from './components/Footer'\nexport { Header } from './components/Header'\n"
        "export { stamp } from './api/client'\n// exports:end\n")


def test_ex5_idempotent_lines_rerun_cleanly(repo):
    assert ex(repo, 5).returncode == 0
    once = snapshot(repo)
    code = (EXAMPLES / EXAMPLE_FILES[5]).read_text().replace(
        "del E('src/api/client.ts')['function legacyFetch'].block\n", '')
    p = run(repo, code)
    assert p.returncode == 0, p.stderr
    assert snapshot(repo) == once
    # the non-idempotent line fails closed instead of guessing
    p = ex(repo, 5)
    assert p.returncode == 1 and snapshot(repo) == once
