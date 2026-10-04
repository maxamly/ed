"""The edx command: script mode, flags and read-only subcommands."""
import json
import os
import subprocess
import sys

import pytest

from ed.cli import strict_check
from ed.errors import EdError

from .conftest import ROOT, read, snapshot, write


def edx(cwd, *args, stdin=None, env=None):
    e = dict(os.environ, PYTHONPATH=str(ROOT))
    for k in ('ED_DRY', 'ED_JSON', 'ED_DIFF_LINES'):
        e.pop(k, None)
    e['PATH'] = f"{os.path.join(cwd, 'bin')}{os.pathsep}{e['PATH']}"
    e.update(env or {})
    return subprocess.run([sys.executable, '-m', 'ed', *args], cwd=cwd, input=stdin, env=e,
                          capture_output=True, text=True)


def test_script_from_stdin_with_implicit_import(tmp):
    write('a.ts', 'a\n')
    p = edx(tmp, stdin="E('a.ts')['a'] = 'b'\n")
    assert p.returncode == 0, p.stderr
    assert read('a.ts') == 'b\n' and p.stdout.endswith('ed: 1 file changed (+1 -1)\n')


def test_del_on_a_variable_deletes_the_selection(tmp):
    write('a.ts', 'function a() {\n  1\n}\n\nfunction b() {\n  2\n}\n')
    p = edx(tmp, stdin="e = E('a.ts')\nfn = e['function a'].block\ncode = fn.text\ndel fn\n"
                        "e['function b'].block.after('\\n\\n' + code)\nx = 1\ndel x\n")
    assert p.returncode == 0, p.stderr
    assert read('a.ts') == 'function b() {\n  2\n}\n\nfunction a() {\n  1\n}\n'


def test_del_on_a_call_deletes_the_selection(tmp):
    write('a.ts', 'a\nb\nc\nd\n')
    p = edx(tmp, stdin="e = E('a.ts')\nif True:\n    del e.lines(2, 3)\n")
    assert p.returncode == 0, p.stderr
    assert read('a.ts') == 'a\nd\n'


def test_del_on_a_tuple_with_a_call_is_still_a_syntax_error(tmp):
    write('a.ts', 'a\n')
    p = edx(tmp, stdin="x = 1\ndel x, E('a.ts').lines(1)\n")
    assert p.returncode == 1 and 'SyntaxError' in p.stderr and read('a.ts') == 'a\n'


def test_syntax_error_still_reports_its_line(tmp):
    p = edx(tmp, stdin="x = 1\nE('a.ts')[\n")
    assert p.returncode == 1 and 'line 2' in p.stderr and 'SyntaxError' in p.stderr
    assert 'cli.py' not in p.stderr and 'Traceback' not in p.stderr


def test_re_and_path_are_preimported(tmp):
    write('a.ts', 'x.y\n')
    p = edx(tmp, stdin="E('a.ts')[rx(re.escape('x.y'))] = 'ok'\nprint(Path('a.ts').exists())\n")
    assert p.returncode == 0, p.stderr
    assert read('a.ts') == 'ok\n' and 'True' in p.stdout
    p = edx(tmp, '--strict', stdin="print(Path)\n")
    assert p.returncode == 1 and 'NameError' in p.stderr


def test_one_liner_argument(tmp):
    write('a.ts', 'foo(1)\n')
    p = edx(tmp, "E('*.ts').all['foo('] = 'bar('")
    assert p.returncode == 0 and read('a.ts') == 'bar(1)\n'


def test_script_from_file_with_dry(tmp):
    write('a.ts', 'a\n')
    write('../migrate.py', "E('a.ts')['a'] = 'b'\n")
    p = edx(tmp, '--dry', '-f', '../migrate.py')
    assert p.returncode == 0 and read('a.ts') == 'a\n' and '+b' in p.stdout and 'dry run' in p.stdout


def test_worked_example_through_edx(repo):
    p = edx(repo, stdin="e = E('src/**/*.{ts,tsx}', '!src/workers/**').having('console.log(')\n"
                        "e.all['console.log('] = 'logger.debug('\n"
                        "e[:'import'] |= \"import { logger } from '@/lib/logger'\"\n"
                        "ensure('tsc --noEmit')\n")
    assert p.returncode == 0, p.stderr
    assert 'ensure ok: tsc --noEmit' in p.stdout


def test_failure_exits_nonzero_and_writes_nothing(tmp):
    write('a.ts', 'a\n')
    write('b.ts', 'b\n')
    before = snapshot(tmp)
    p = edx(tmp, stdin="E('a.ts')['a'] = 'x'\nE('b.ts')['zzz'] = 'y'\n")
    assert p.returncode == 1 and snapshot(tmp) == before
    assert "ed: error at line 2 `E('b.ts')['zzz'] = 'y'`" in p.stderr


def test_python_error_shows_script_traceback(tmp):
    write('a.ts', 'a\n')
    p = edx(tmp, stdin="E('a.ts')['a'] = 'x'\nundefined_name\n")
    assert p.returncode == 1 and read('a.ts') == 'a\n'
    assert 'File "<stdin>", line 2' in p.stderr and 'NameError' in p.stderr
    assert 'cli.py' not in p.stderr and 'no files changed' in p.stderr


def test_sys_exit_nonzero_aborts(tmp):
    write('a.ts', 'a\n')
    p = edx(tmp, stdin="import sys\nE('a.ts')['a'] = 'x'\nsys.exit(3)\n")
    assert p.returncode == 3 and read('a.ts') == 'a\n'


def test_ensure_failure_through_edx(tmp):
    write('a.ts', 'a\n')
    p = edx(tmp, stdin="E('a.ts')['a'] = 'x'\nensure('echo nope; exit 4')\n")
    assert p.returncode == 1 and read('a.ts') == 'a\n' and 'nope' in p.stderr


def test_json_flag(tmp):
    write('a.ts', 'a\n')
    p = edx(tmp, '--json', stdin="E('a.ts')['a'] = 'b'\n")
    res = json.loads(p.stdout)
    assert res['ok'] and res['files'][0]['path'] == 'a.ts'
    p = edx(tmp, '--json', stdin="E('a.ts')['zzz'] = 'b'\n")
    assert p.returncode == 1 and not json.loads(p.stdout)['ok']
    p = edx(tmp, '--json', stdin="1/0\n")
    assert p.returncode == 1 and 'ZeroDivisionError' in json.loads(p.stdout)['error']


def test_max_diff(tmp):
    write('a.ts', 'a\nb\nc\nd\n')
    p = edx(tmp, '--max-diff', '2', stdin="E('a.ts').all[rx('^.')] = 'x'\n")
    assert p.stdout.splitlines()[2].startswith('... +') and 'run `edx diff`' in p.stdout


def test_strict_rejects_imports_and_escapes(tmp):
    write('a.ts', 'a\n')
    for bad in ('import os', "open('a.ts', 'w')", "eval('1')", "exec('x=1')", 'os.remove("a")',
                "__import__('os')", "E.__init__.__globals__", 'from subprocess import run'):
        p = edx(tmp, '--strict', stdin=bad + '\n')
        assert p.returncode == 1 and '--strict' in p.stderr, bad
    p = edx(tmp, '--strict', stdin="E('a.ts')['a'] = 'b'\nsh('true')\n")
    assert p.returncode == 0 and read('a.ts') == 'b\n'


def test_strict_check_unit():
    with pytest.raises(EdError, match='line 2'):
        strict_check("x = 1\nimport os\n", '<t>')


def test_usage_errors(tmp):
    assert edx(tmp, '--nope').returncode == 2
    assert edx(tmp, '-f').returncode == 2
    assert edx(tmp, '--max-diff', 'x', stdin='').returncode == 2
    assert edx(tmp, '-f', 'missing.py').returncode == 2
    assert edx(tmp, '--help').returncode == 0


def test_help_includes_the_scripting_reference(tmp):
    out = edx(tmp, '--help').stdout
    assert out.startswith((ROOT / 'ed' / 'SPEC.md').read_text().rstrip())
    assert 'strictly between' in out and '\nusage:\n  edx [--dry]' in out
    assert edx(tmp, 'help').stdout == out and edx(tmp, '-h').stdout == out


# ---- read-only subcommands


def test_read_commands_are_gone(tmp):
    for cmd in ('view', 'find'):
        p = edx(tmp, cmd, 'a.ts')
        assert p.returncode != 0  # treated as a (bad) script, never as a read command


def test_diff_never_writes(tmp):
    write('a.ts', 'a\n')
    before = snapshot(tmp)
    edx(tmp, 'diff')
    assert snapshot(tmp) == before and not os.path.exists('.ed')


def test_diff_and_undo(tmp):
    write('a.ts', ''.join(f'{i}\n' for i in range(400)))
    edx(tmp, stdin="E('a.ts').all[rx(r'^\\d')] = 'x'\n")
    p = edx(tmp, 'diff')
    assert p.returncode == 0 and len(p.stdout.splitlines()) > 400
    p = edx(tmp, 'undo')
    assert p.returncode == 0 and read('a.ts').startswith('0\n1\n')
    p = edx(tmp, 'undo')
    assert p.returncode == 1 and 'nothing to undo' in p.stderr


def test_spec_subcommand_and_size():
    p = subprocess.run([sys.executable, '-m', 'ed', 'spec'], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=str(ROOT)))
    assert p.stdout == (ROOT / 'ed' / 'SPEC.md').read_text()
    assert len(p.stdout) < 2600  # ~600-700 tokens: small enough for a system prompt
