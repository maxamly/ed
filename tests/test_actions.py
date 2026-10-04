"""Every action in the design doc's action table."""
import pytest

from ed import E, EdError, rx, swap

from .conftest import commit, read, write


def file(text, name='a.ts'):
    write(name, text)
    return E(name)


def test_replace_and_selection_value(tmp):
    e = file('a = 1\nb = 2\n')
    e['a = 1'] = e['b = 2']  # a selection as value means its text
    commit()
    assert read('a.ts') == 'b = 2\nb = 2\n'


def test_insert_after_operator_and_method(tmp):
    e = file('x\n')
    e['x'] += 'y'
    e['xy'].after('z')
    commit()
    assert read('a.ts') == 'xyz\n'


def test_held_selection_iadd_applies_once(tmp):
    e = file('x\n')
    s = e['x']
    s += '!'
    commit()
    assert read('a.ts') == 'x!\n'


def test_insert_before(tmp):
    e = file('b\n')
    e['b'].before('a')
    commit()
    assert read('a.ts') == 'ab\n'


def test_add_line_inserts_once(tmp):
    e = file("import b from 'b'\nrun()\n")
    e[:'import'] |= "import a from 'a'"
    e[:'import'] |= "import a from 'a'"
    e.bottom |= 'done()'
    commit()
    assert read('a.ts') == "import a from 'a'\nimport b from 'b'\nrun()\ndone()\n"


def test_add_line_checks_whole_file_not_selection(tmp):
    e = file("x\nimport a from 'a'\n")
    e.top |= "import a from 'a'"
    assert commit()['files'] == []


def test_add_line_skips_files_that_have_it_without_resolving(tmp):
    write('b.ts', 'no anchor here, but has: KEEP\n')
    write('c.ts', 'anchor\n')
    E('b.ts', 'c.ts')['anchor'] |= 'KEEP'
    commit()
    assert read('c.ts') == 'anchor\nKEEP\n'


def test_add_line_after_mid_line_selection_goes_below_that_line(tmp):
    e = file('a()\nb()\n')
    e['a('] |= 'c()'
    commit()
    assert read('a.ts') == 'a()\nc()\nb()\n'


def test_add_line_at_bottom_without_trailing_newline(tmp):
    e = file('a=1')
    e.bottom |= 'b=2'
    commit()
    assert read('a.ts') == 'a=1\nb=2'


def test_add_line_method(tmp):
    e = file('a\n')
    e.top.add_line('z')
    commit()
    assert read('a.ts') == 'z\na\n'


def test_add_line_reindents_to_the_selection(tmp):
    e = file('const c = {\n  retries: 3,\n  timeoutMs: 5000,\n}\n')
    e['timeoutMs: 5000,'] |= 'debug: false,'
    commit()
    assert read('a.ts') == 'const c = {\n  retries: 3,\n  timeoutMs: 5000,\n  debug: false,\n}\n'


def test_add_line_after_an_opener_goes_inside(tmp):
    e = file('def f():\n    a()\n', 'a.py')
    e['def f():'] |= 'log()'
    commit()
    assert read('a.py') == 'def f():\n    log()\n    a()\n'


def test_add_line_into_marker_slice_and_multi_line_block(tmp):
    e = file('const r = [\n  // s\n  1,\n  // e\n]\n')
    e['// s':'// e'] |= """
        {
          two: 2,
        },
    """
    commit()
    assert read('a.ts') == 'const r = [\n  // s\n  1,\n  {\n    two: 2,\n  },\n  // e\n]\n'


def test_add_line_into_empty_marker_slice_matches_closing_marker(tmp):
    e = file('[\n    // s\n    // e\n]\n')
    e['// s':'// e'] |= 'x,'
    commit()
    assert read('a.ts') == '[\n    // s\n    x,\n    // e\n]\n'


def test_add_line_at_bottom_is_top_level(tmp):
    e = file('def f():\n    return 1\n', 'a.py')
    e.bottom |= 'main()'
    commit()
    assert read('a.py') == 'def f():\n    return 1\nmain()\n'


def test_add_line_skip_ignores_indentation_and_is_reported(tmp):
    e = file('x = {\n    debug: false,\n}\n')
    e['x = {'] |= 'debug: false,'
    res = commit()
    assert res['files'] == [] and res['notes'] == ['|= skipped a.ts: already has it at line 2']


def test_delete_operator_and_method(tmp):
    e = file('a\n// x\nb  // y\nc\n')
    del e['// x'].line
    e['  // y'].delete()
    commit()
    assert read('a.ts') == 'a\nb\nc\n'


def test_delete_whole_block_collapses_blank_lines(tmp):
    e = file('a()\n\nfunction f() {\n  x()\n}\n\nb()\n')
    del e['function f'].block
    commit()
    assert read('a.ts') == 'a()\n\nb()\n'


def test_delete_last_line_keeps_missing_trailing_newline(tmp):
    e = file('a\nb')
    del e['b'].line
    commit()
    assert read('a.ts') == 'a'


def test_delete_inner_of_markers_keeps_markers_on_their_lines(tmp):
    e = file('// s\nx\ny\n// e\n')
    del e['// s':'// e'].inner
    commit()
    assert read('a.ts') == '// s\n// e\n'


def test_delete_mid_line_is_exact(tmp):
    e = file('f(a, b)\n')
    del e[', b']
    commit()
    assert read('a.ts') == 'f(a)\n'


def test_indented_inside_block(tmp):
    e = file('function f() {\n    a()\n}\n')
    e['function f'].indented('''
        if (x) {
          y()
        }
    ''')
    commit()
    assert read('a.ts') == 'function f() {\n    if (x) {\n      y()\n    }\n    a()\n}\n'


def test_indented_after_statement_and_before(tmp):
    e = file('def f():\n    a()\n    b()\n', 'a.py')
    e['a()'].indented('log()')
    e['b()'].indented('first()', before=True)
    commit()
    assert read('a.py') == 'def f():\n    a()\n    log()\n    first()\n    b()\n'


def test_indented_into_empty_python_block_uses_file_unit(tmp):
    e = file('class A:\n    def f(self):\n        pass\n\ndef g():\n', 'a.py')
    e['def g'].indented('return 1')
    commit()
    assert read('a.py').endswith('def g():\n    return 1\n')


def test_indented_is_idempotent(tmp):
    e = file('def f():\n    a()\n', 'a.py')
    e['def f'].indented('log()')
    e['def f'].indented('log()')
    e['a()'].indented('first()', before=True)
    e['a()'].indented('first()', before=True)
    commit()
    assert read('a.py') == 'def f():\n    log()\n    first()\n    a()\n'


def test_move_within_file(tmp):
    e = file('function a() {\n  1\n}\n\nfunction b() {\n  2\n}\n')
    e['function a'].block.move(after=e['function b'].block)
    commit()
    assert read('a.ts') == 'function b() {\n  2\n}\nfunction a() {\n  1\n}\n'


def test_move_to_other_file_before(tmp):
    write('b.ts', 'const z = 1\n')
    e = file('const a = 1\nconst b = 2\n')
    e['const b = 2'].line.move(before=E('b.ts')['const z'])
    commit()
    assert read('a.ts') == 'const a = 1\n'
    assert read('b.ts') == 'const b = 2\nconst z = 1\n'


def test_move_mid_line_is_exact(tmp):
    e = file('f(a, b)\n')
    e['a, '].move(after=e['b'])
    commit()
    assert read('a.ts') == 'f(ba, )\n'


def test_move_into_itself_fails(tmp):
    e = file('function a() {\n  x\n}\n')
    with pytest.raises(EdError, match='inside the moved text'):
        e['function a'].block.move(after=e['x'])


def test_swap(tmp):
    write('b.ts', 'B\n')
    e = file('x = 1\ny = 2\n')
    swap(e['x = 1'], e['y = 2'])
    swap(e['x = 1'], E('b.ts')['B'])
    commit()
    assert read('a.ts') == 'y = 2\nB\n' and read('b.ts') == 'x = 1\n'


def test_swap_needs_single_ranges(tmp):
    e = file('a a b\n')
    with pytest.raises(EdError, match='one range on each side'):
        swap(e.all['a'], e['b'])


def test_lines_sort_uniq_filter_map(tmp):
    e = file('# s\nc\na\nb\na\n# e\ntail\n')
    region = e['# s':'# e'].inner
    region.lines.uniq()
    region.lines.sort()
    region.lines.filter(lambda ln: ln != 'b')
    region.lines.map(str.upper)
    commit()
    assert read('a.ts') == '# s\nA\nC\n# e\ntail\n'


def test_lines_sort_reverse_and_key(tmp):
    e = file('bb\na\nccc\n')
    e.lines.sort(key=len, reverse=True)
    commit()
    assert read('a.ts') == 'ccc\nbb\na\n'


def test_lines_iteration(tmp):
    e = file('x\ny\n')
    assert list(e.lines) == ['x', 'y']


def test_text_and_count(tmp):
    e = file('a b a\n')
    assert e['b'].text == 'b' and e.all['a'].count == 2 and e['a'].count == 2
    assert e.all['a'].texts == ['a', 'a'] and len(e.all['a']) == 2 and not e.opt['z']
    with pytest.raises(EdError, match='exactly one range'):
        e.all['a'].text


def test_each_escape_hatch(tmp):
    write('a.ts', 'one\n')
    write('b.ts', 'two\n')
    E('*.ts').each(lambda path, text: text.upper() if path == 'a.ts' else None)
    commit()
    assert read('a.ts') == 'ONE\n' and read('b.ts') == 'two\n'


def test_nth_methods_exist_for_operators(tmp):
    for method, arg, want in (('put', 'a', 'x a x\n'), ('after', 'a', 'x xa x\n'), ('before', 'a', 'x ax x\n'),
                              ('add_line', 'a', 'x x x\na\n'), ('delete', None, 'x  x\n')):
        e = file('x x x\n')
        m = getattr(e['x'].nth(2), method)
        m(arg) if arg is not None else m()
        commit()
        assert read('a.ts') == want, method


def test_multiple_ranges_in_one_action(tmp):
    e = file('a1 a2 a3\n')
    e.all[rx(r'a(\d)')] = r'b\1'
    commit()
    assert read('a.ts') == 'b1 b2 b3\n'


def test_overlapping_ranges_fail(tmp):
    e = file('aaa\n')
    with pytest.raises(EdError, match='overlapping'):
        E('b.ts').create('f { g { } }\n')
        E('b.ts').all[rx(r'\w \{')].block.put('x')


def test_assigning_unknown_attribute_is_an_error(tmp):
    e = file('a\n')
    with pytest.raises(AttributeError):
        e['a'].innr = 'x'


def test_create_mv_rm(tmp):
    write('old.ts', 'old\n')
    write('keep.ts', 'k\n')
    E('src/new.ts').create('export const n = 1\n')
    from ed import mv, rm
    mv('keep.ts', 'lib/kept.ts')
    E('lib/kept.ts')['k'] = 'kk'
    rm('old.ts')
    res = commit()
    assert read('src/new.ts') == 'export const n = 1\n'
    assert read('lib/kept.ts') == 'kk\n'
    import os
    assert not os.path.exists('old.ts') and not os.path.exists('keep.ts')
    assert sorted(f['status'] for f in res['files']) == ['created', 'deleted', 'renamed']


def test_create_refuses_to_overwrite(tmp):
    write('a.ts', 'a\n')
    with pytest.raises(EdError, match='already exists'):
        E('a.ts').create('b')
    E('a.ts').create('b\n', overwrite=True)
    commit()
    assert read('a.ts') == 'b\n'


def test_globs_see_files_created_in_the_run(tmp):
    E('src/a.ts').create('x\n')
    assert E('src/*.ts').paths == ['src/a.ts']


def test_edit_after_rm_fails(tmp):
    write('a.ts', 'a\n')
    from ed import rm
    rm('a.ts')
    with pytest.raises(EdError, match='removed or moved away'):
        E('a.ts')['a'].text
