"""The ten behaviour rules, plus stale reads, the undo journal and the CLI."""
import json
import os
import stat

import pytest

from ed import E, EdError, ensure, expect, rx

from .conftest import commit, read, run, snapshot, write

# ---- rule 1: lazy, ordered edits; nothing on disk before commit


def test_edits_apply_in_order_and_stay_in_memory(tmp):
    write('a.ts', 'a\n')
    e = E('a.ts')
    e['a'] = 'b'
    e['b'] = 'c'  # sees the previous edit
    assert read('a.ts') == 'a\n'
    commit()
    assert read('a.ts') == 'c\n'


def test_selections_re_resolve_against_current_text(tmp):
    write('a.ts', 'x\ny\n')
    e = E('a.ts')
    sel = e['y']
    e.top += 'new\n'
    sel += '!'
    commit()
    assert read('a.ts') == 'new\nx\ny!\n'


# ---- rule 2: commit at exit or end of with-block, atomically


def test_commit_at_exit(tmp):
    write('a.ts', 'a\n')
    p = run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\n")
    assert p.returncode == 0 and read('a.ts') == 'b\n'
    assert p.stdout.endswith('ed: 1 file changed (+1 -1)\n')


def test_with_block_commits_at_end(tmp):
    write('a.ts', 'a\n')
    p = run(tmp, """\
        from ed import *
        with E('a.ts') as e:
            e['a'] = 'b'
        print('after block:', open('a.ts').read().strip())
        """)
    assert 'after block: b' in p.stdout


def test_with_block_discards_on_exception(tmp):
    write('a.ts', 'a\n')
    with pytest.raises(ValueError):
        with E('a.ts') as e:
            e['a'] = 'b'
            raise ValueError
    assert read('a.ts') == 'a\n'


def test_write_is_atomic_and_keeps_mode(tmp):
    p = write('run.sh', '#!/bin/sh\necho a\n')
    os.chmod(p, 0o755)
    E('run.sh')['echo a'] = 'echo b'
    commit()
    assert stat.S_IMODE(os.stat('run.sh').st_mode) == 0o755
    assert [f for f in os.listdir('.') if f.endswith('.ed~')] == []


def test_symlink_target_is_written_link_kept(tmp):
    write('real.ts', 'a\n')
    os.symlink('real.ts', 'link.ts')
    E('link.ts')['a'] = 'b'
    commit()
    assert os.path.islink('link.ts') and read('real.ts') == 'b\n'


# ---- rule 3: ensure runs against written files, rolls back everything on failure


def test_ensure_sees_written_files(tmp):
    write('a.ts', 'a\n')
    E('a.ts')['a'] = 'b'
    ensure("grep -q b a.ts")
    res = commit()
    assert res['ensure'][0]['ok']


def test_ensure_failure_restores_edits_creates_moves_deletes(tmp):
    write('a.ts', 'a\n')
    write('m.ts', 'm\n')
    write('d.ts', 'd\n')
    before = snapshot(tmp)
    p = run(tmp, """\
        from ed import *
        E('a.ts')['a'] = 'b'
        E('new/n.ts').create('n')
        mv('m.ts', 'moved.ts')
        rm('d.ts')
        ensure('python3 -c "print(\\'x\\' * 3000 + \\'END\\'); raise SystemExit(3)"')
        """)
    assert p.returncode == 1
    assert snapshot(tmp) == before and not os.path.exists('new')
    assert 'ensure failed' in p.stderr and 'exit 3' in p.stderr and 'rolled back' in p.stderr
    tail = p.stderr.split('rolled back\n', 1)[1]
    assert tail.rstrip().endswith('END') and 1990 <= len(tail.strip()) <= 2000


def test_ensure_changed_placeholder(tmp):
    write('a.ts', 'a\n')
    write('b.ts', 'b\n')
    E('a.ts')['a'] = 'z'
    ensure('test "{changed}" = "a.ts"')
    assert commit()['ensure'][0]['ok']


def test_ensure_changed_skipped_when_nothing_changed(tmp):
    write('a.ts', 'a\n')
    E('a.ts').all['nope'] = 'z'
    ensure('false {changed}')
    res = commit()
    assert res['ok'] and res['ensure'][0]['skipped']


# ---- rule 4: fail closed


def test_missing_anchor_in_second_file_changes_nothing(tmp):
    write('a.ts', 'x\n')
    write('b.ts', 'y\n')
    before = snapshot(tmp)
    p = run(tmp, "from ed import *\nE('a.ts', 'b.ts')['x'] = 'z'\n")
    assert p.returncode == 1 and snapshot(tmp) == before
    assert "'x' not found in b.ts" in p.stderr and 'no files changed' in p.stderr
    assert 'ed: error at line 2 `E(' in p.stderr


def test_failure_after_earlier_edits_changes_nothing(tmp):
    write('a.ts', 'x\n')
    before = snapshot(tmp)
    p = run(tmp, "from ed import *\nE('a.ts')['x'] = 'y'\nE('a.ts')['nope'] = 'z'\n")
    assert p.returncode == 1 and snapshot(tmp) == before


def test_python_exception_changes_nothing(tmp):
    write('a.ts', 'x\n')
    p = run(tmp, "from ed import *\nE('a.ts')['x'] = 'y'\n1/0\n")
    assert p.returncode == 1 and read('a.ts') == 'x\n'
    assert 'ZeroDivisionError' in p.stderr and 'no files changed' in p.stderr


def test_expect(tmp):
    write('a.ts', 'log() log()\n')
    assert expect(E('a.ts').all['log()'], 2) == 2
    with pytest.raises(EdError, match=r'expect: wanted 3 matches, found 2\n    a.ts:1'):
        expect(E('a.ts').all['log()'], 3)


def test_failed_expect_in_script_changes_nothing(tmp):
    write('a.ts', 'a\n')
    p = run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\nexpect(E('a.ts').all['b'], 2)\n")
    assert p.returncode == 1 and read('a.ts') == 'a\n'


def test_unbalanced_brackets_change_nothing(tmp):
    write('a.ts', 'ok\nfunction f() {\n')
    p = run(tmp, "from ed import *\nE('a.ts')['ok'] = 'k'\ndel E('a.ts')['function f'].block\n")
    assert p.returncode == 1 and read('a.ts') == 'ok\nfunction f() {\n'


def test_failure_inside_one_action_leaves_other_files_untouched_in_memory(tmp):
    write('a.ts', 'x\n')
    write('b.ts', 'q\n')
    with pytest.raises(EdError):
        E('a.ts', 'b.ts')['x'] = 'y'
    from ed import txn
    assert txn.current().get('a.ts').text == 'x\n'


# ---- rule 5: helpful errors


def test_missing_anchor_shows_three_nearest(tmp):
    write('a.ts', 'function loadUser(id) {\n  return db.get(id)\n}\nfunction loadUsers() {}\n'
                  'function saveUser(u) {}\n')
    with pytest.raises(EdError) as ex:
        E('a.ts')['function  loadUser(id){'].text
    msg = str(ex.value)
    lines = [ln for ln in msg.splitlines() if ln.startswith('    a.ts:')]
    assert len(lines) == 3
    assert lines[0] == '    a.ts:1| function loadUser(id) {'
    assert "ws('...')" in msg


def test_ambiguous_anchor_lists_locations(tmp):
    write('a.ts', 'f()\nf()\n')
    with pytest.raises(EdError, match=r"(?s)matches 2 times.*a.ts:1\| f\(\).*a.ts:2\| f\(\).*\.nth\(k\)"):
        E('a.ts')['f()'].text


# ---- rule 6: uniqueness per file


def test_uniqueness_applies_to_every_file(tmp):
    write('a.ts', 'x\n')
    write('b.ts', 'x x\n')
    with pytest.raises(EdError, match='matches 2 times in b.ts'):
        E('*.ts')['x'] = 'y'


# ---- rule 7: idempotency


def test_rerun_of_add_line_and_region_produces_no_diff(tmp):
    write('a.ts', 'import b\n')
    code = """\
        from ed import *
        e = E('a.ts')
        e[:'import'] |= 'import a'
        e.region('gen').put('x = 1')
        """
    assert run(tmp, code).returncode == 0
    once = snapshot(tmp)
    p = run(tmp, code)
    assert p.returncode == 0 and snapshot(tmp) == once
    assert p.stdout == 'ed: |= skipped a.ts: already has it at line 1\ned: no changes\n'


# ---- rule 8: file fidelity


def test_crlf_preserved_including_inserted_text(tmp):
    write('a.ts', b'a\r\nb\r\n')
    E('a.ts')['a'] += '\nmid'
    E('a.ts').bottom |= 'c'
    commit()
    assert open('a.ts', 'rb').read() == b'a\r\nmid\r\nb\r\nc\r\n'


def test_diff_header_names_what_was_kept(tmp):
    write('a.ts', b'\xef\xbb\xbfa\r\nb')
    write('plain.ts', 'a\n')
    E('a.ts')['a'] = 'x'
    E('plain.ts')['a'] = 'x'
    diff = commit()['diff']
    assert diff.startswith('# a.ts: CRLF, BOM, no final newline kept\n--- a/a.ts')
    assert '# plain.ts' not in diff


def test_bom_preserved(tmp):
    write('a.ts', b'\xef\xbb\xbfx\n')
    E('a.ts')['x'] = 'y'
    commit()
    assert open('a.ts', 'rb').read() == b'\xef\xbb\xbfy\n'


def test_utf16_with_bom_preserved(tmp):
    write('a.txt', '﻿hello\n'.encode('utf-16-le'))
    E('a.txt')['hello'] = 'world'
    commit()
    assert open('a.txt', 'rb').read() == '﻿world\n'.encode('utf-16-le')


def test_latin1_preserved(tmp):
    write('a.txt', 'caf\xe9 = 1\n'.encode('latin-1'))
    E('a.txt')['1'] = '2'
    commit()
    assert open('a.txt', 'rb').read() == 'caf\xe9 = 2\n'.encode('latin-1')


def test_trailing_newline_kept_when_edit_drops_it(tmp):
    write('a.ts', 'a\n')
    E('a.ts')[:] = 'b'
    commit()
    assert read('a.ts') == 'b\n'


def test_missing_trailing_newline_stays_missing(tmp):
    write('a.ts', 'a')
    E('a.ts')['a'] = 'b'
    commit()
    assert read('a.ts') == 'b'


def test_mixed_newlines_untouched_lines_round_trip(tmp):
    write('a.ts', b'a\r\nb\nc\r\n')
    E('a.ts')['b'] = 'B'
    commit()
    assert open('a.ts', 'rb').read() == b'a\r\nB\nc\r\n'


def test_binary_files_skipped_by_globs(tmp):
    write('a.bin', b'\x00\x01x')
    write('a.txt', 'x\n')
    assert E('a.*').paths == ['a.txt']
    with pytest.raises(EdError, match='looks binary'):
        E('a.bin')['x'].text


# ---- rule 9: naive bracket counting


def test_bracket_counting_ignores_strings_by_design(tmp):
    write('a.ts', "function f() {\n  const s = '}'\n  return s\n}\n")
    assert E('a.ts')['function f'].block.text == "function f() {\n  const s = '}"
    # the documented way out: explicit anchors
    assert E('a.ts')['function f':'\n}\n'].outer.text.endswith('return s')


# ---- rule 10: output


def test_diff_is_budgeted(tmp):
    write('a.ts', ''.join(f'line{i}\n' for i in range(300)))
    p = run(tmp, "from ed import *\nE('a.ts').all[rx(r'^line')] = 'L'\n")
    out = p.stdout.splitlines()
    assert len(out) == 202 and '+403 more lines, run `edx diff`' in out[200]
    assert out[-1] == 'ed: 1 file changed (+300 -300)'
    full = run(tmp, "import sys\nfrom ed.cli import main\nsys.exit(main(['diff']))\n")
    assert len(full.stdout.splitlines()) == 603


def test_diff_budget_env(tmp):
    write('a.ts', 'a\nb\nc\n')
    p = run(tmp, "from ed import *\nE('a.ts').all[rx('^.')] = 'x'\n", env={'ED_DIFF_LINES': '3'})
    assert '... +' in p.stdout


def test_json_output(tmp):
    write('a.ts', 'a\n')
    for kw in ({'args': ['--json']}, {'env': {'ED_JSON': '1'}}):
        p = run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\nE('a.ts')['b'] = 'a'\n"
                     "E('a.ts')['a'] = 'c'\n", **kw)
        res = json.loads(p.stdout)
        assert res['ok'] and res['files'] == [{'path': 'a.ts', 'status': 'modified', 'added': 1, 'removed': 1}]
        assert '+c' in res['diff'] and 'output' not in res
        write('a.ts', 'a\n')


def test_json_error(tmp):
    write('a.ts', 'a\n')
    p = run(tmp, "from ed import *\nE('a.ts')['zzz'] = 'b'\n", args=['--json'])
    res = json.loads(p.stdout)
    assert p.returncode == 1 and not res['ok'] and "'zzz' not found" in res['error']


def test_json_ensure_failure(tmp):
    write('a.ts', 'a\n')
    p = run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\nensure('echo boom; false')\n", env={'ED_JSON': '1'})
    res = json.loads(p.stdout)
    assert p.returncode == 1 and not res['ok'] and res['ensure'][0]['output'].strip() == 'boom'
    assert read('a.ts') == 'a\n'


def test_dry_run(tmp):
    write('a.ts', 'a\n')
    p = run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\nensure('false')\n", env={'ED_DRY': '1'})
    assert p.returncode == 0 and read('a.ts') == 'a\n'
    assert '+b' in p.stdout and 'dry run' in p.stdout and 'ensure skipped' in p.stdout


def test_quiet_when_nothing_attempted(tmp):
    write('a.ts', 'a\n')
    p = run(tmp, "from ed import *\nprint(E('a.ts')['a'].text)\n")
    assert p.stdout == 'a\n'


# ---- stale reads


def test_stale_read_refuses_to_write(tmp):
    write('a.ts', 'a\n')
    E('a.ts')['a'] = 'b'
    write('a.ts', 'someone else\n')
    with pytest.raises(EdError, match='changed on disk since it was read'):
        commit()
    assert read('a.ts') == 'someone else\n'


# ---- journal: checkpoint and undo


def test_undo_last_run(tmp):
    write('a.ts', 'a\n')
    write('gone.ts', 'g\n')
    assert run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\nE('n.ts').create('n\\n')\nrm('gone.ts')\n"
                    "mv('a.ts', 'moved.ts')\n").returncode == 0
    p = run(tmp, "from ed import *\nundo()\n")
    assert p.returncode == 0, p.stderr
    assert read('a.ts') == 'a\n' and read('gone.ts') == 'g\n'
    assert not os.path.exists('n.ts') and not os.path.exists('moved.ts')


def test_undo_twice_reverts_two_runs_not_redo(tmp):
    write('a.ts', '0\n')
    for i in (1, 2):
        run(tmp, f"from ed import *\nE('a.ts')['{i - 1}'] = '{i}'\n")
    run(tmp, "from ed import *\nundo()\n")
    assert read('a.ts') == '1\n'
    run(tmp, "from ed import *\nundo()\n")
    assert read('a.ts') == '0\n'
    p = run(tmp, "from ed import *\nundo()\n")
    assert p.returncode == 1 and 'nothing to undo' in p.stderr


def test_undo_to_checkpoint(tmp):
    write('a.ts', '0\n')
    run(tmp, "from ed import *\nE('a.ts')['0'] = '1'\n")
    run(tmp, "from ed import *\ncheckpoint('before-refactor')\nE('a.ts')['1'] = '2'\n")
    run(tmp, "from ed import *\nE('a.ts')['2'] = '3'\n")
    run(tmp, "from ed import *\nundo('before-refactor')\n")
    assert read('a.ts') == '1\n'


def test_undo_refuses_when_file_changed_since(tmp):
    write('a.ts', 'a\n')
    run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\n")
    write('a.ts', 'manual\n')
    p = run(tmp, "from ed import *\nundo()\n")
    assert p.returncode == 1 and 'changed after that run' in p.stderr and read('a.ts') == 'manual\n'


def test_undo_restores_exact_bytes(tmp):
    write('a.ts', b'\xef\xbb\xbfa\r\nb\r\n')
    run(tmp, "from ed import *\nE('a.ts')['a'] = 'x'\n")
    run(tmp, "from ed import *\nundo()\n")
    assert open('a.ts', 'rb').read() == b'\xef\xbb\xbfa\r\nb\r\n'


def test_undo_cli(tmp):
    write('a.ts', 'a\n')
    run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\n")
    p = run(tmp, "import sys\nfrom ed.cli import main\nsys.exit(main(['undo']))\n")
    assert p.returncode == 0 and read('a.ts') == 'a\n' and '-b' in p.stdout


def test_failed_ensure_leaves_no_journal_entry(tmp):
    write('a.ts', '0\n')
    run(tmp, "from ed import *\nE('a.ts')['0'] = '1'\n")
    run(tmp, "from ed import *\nE('a.ts')['1'] = '2'\nensure('false')\n")
    run(tmp, "from ed import *\nundo()\n")
    assert read('a.ts') == '0\n'


def test_state_dir_ignores_itself(tmp):
    write('a.ts', 'a\n')
    run(tmp, "from ed import *\nE('a.ts')['a'] = 'b'\n")
    assert open('.ed/.gitignore').read() == '*\n'
