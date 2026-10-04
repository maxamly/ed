"""Every selector in the design doc's selector table."""
import pytest

from ed import E, EdError, ln, rx, sh, w, ws

from .conftest import commit, read, write

SRC = """\
import a from 'a'
const x = 1
function load(n) {
  if (n) {
    return [1, 2]
  }
  return call(x, (y) => y)
}
const x2 = 2
"""


@pytest.fixture
def f(tmp):
    write('f.ts', SRC)
    return E('f.ts')


# ---- counting modes


def test_exact_requires_one(f):
    assert f['function load'].text == 'function load'
    with pytest.raises(EdError, match="'nope' not found in f.ts"):
        f['nope'].text
    with pytest.raises(EdError, match=r"'const x' matches 2 times in f.ts, expected exactly 1"):
        f['const x'].text


def test_all_allows_zero_and_many(f):
    assert f.all['const x'].count == 2
    assert f.all['nope'].count == 0
    f.all['nope'] = 'x'  # zero matches is fine
    f.all['const '] = 'let '
    commit()
    assert read('f.ts').count('let ') == 2


def test_opt_is_zero_or_one(f):
    assert f.opt['nope'].count == 0
    assert f.opt['function'].text == 'function'
    f.opt['nope'] = 'x'
    with pytest.raises(EdError, match='matches 2 times'):
        f.opt['const x'].put('y')


def test_nth(f):
    f['const x'].nth(2).put('let y')
    f['return'].nth(-1).after(' /*last*/')
    commit()
    s = read('f.ts')
    assert 'const x = 1' in s and 'let y2 = 2' in s and 'return /*last*/ call' in s
    with pytest.raises(EdError, match=r'use \.nth\(1\) for the first match, \.nth\(-1\) for the last'):
        E('f.ts').all['return'].nth(0).text
    with pytest.raises(EdError, match=r'\.nth\(5\).*only 2 times'):
        E('f.ts').all['return'].nth(5).text


# ---- slices


def test_slice_is_strictly_between(f):
    assert f['if (n)':'return call'].inner.text == ' {\n    return [1, 2]\n  }\n'
    assert f['if (n)':'return call'].outer.text == 'if (n) {\n    return [1, 2]\n  }\n'
    assert f['call(':')'].text == 'x, (y'


def test_ambiguous_slice_fails_closed(f):
    with pytest.raises(EdError, match=r"(?s)spans lines and 'if \(n\)' has code after it.*say \.outer.*\.inner"):
        f['if (n)':'return call'].text
    with pytest.raises(EdError, match='spans lines'):
        del f['function load':'const x2']
    assert f['function load':'const x2'].outer.text.startswith('function load(n) {')
    assert f.all['if (n)':'return call'].count == 1  # counting never raises


def test_slice_with_anchors_on_their_own_lines_is_whole_lines(tmp):
    write('m.ts', 'a\n  // start\n  x\n  y\n  // end\nb\n')
    e = E('m.ts')
    assert e['// start':'// end'].text == '  x\n  y\n'
    e['// start':'// end'] = '  z\n'
    commit()
    assert read('m.ts') == 'a\n  // start\n  z\n  // end\nb\n'


def test_outer_cuts_a_function_up_to_the_next_one(tmp):
    write('c.ts', 'a()\n\nfunction old() {\n  x()\n}\n\nexport function next() {}\n')
    del E('c.ts')['function old':'export function next'].outer
    commit()
    assert read('c.ts') == 'a()\n\nexport function next() {}\n'


def test_slice_end_is_first_after_start(f):
    assert f['function load':'return'].inner.text == '(n) {\n  if (n) {\n'


def test_open_slices(f):
    assert f[:'const x ='].text == "import a from 'a'\n"
    assert f['const x2':].text == ' = 2\n'
    assert f['return call':].inner.text.startswith('(x, (y)')
    assert f['return call':].outer.text == 'return call(x, (y) => y)\n}\nconst x2 = 2\n'
    assert f[:].text == SRC


def test_top_and_bottom_are_zero_width(f):
    f.top += '// top\n'
    f.bottom += '// bottom\n'
    commit()
    s = read('f.ts')
    assert s.startswith('// top\nimport') and s.endswith('const x2 = 2\n// bottom\n')


def test_lines_are_one_based_inclusive(f):
    assert f.lines(2, 3).text == 'const x = 1\nfunction load(n) {\n'
    assert f.lines(9).text == 'const x2 = 2\n'
    with pytest.raises(EdError, match='only 9 lines'):
        f.lines(9, 10).text


def test_lines_relative_to_selection(f):
    assert f['function load'].block.lines(2).text == '  if (n) {\n'


# ---- pattern kinds


def test_regex_groups_and_templates(f):
    f.all[rx(r'const (\w+) = (\d)')] = r'let \1: number = \2'
    commit()
    s = read('f.ts')
    assert 'let x: number = 1' in s and 'let x2: number = 2' in s


def test_regex_template_without_raw_string(f):
    f.all[rx(r'const (\w+)')] = 'var \1'  # '\1' is chr(1) in a non-raw string; still works
    commit()
    assert 'var x = 1' in read('f.ts')


def test_regex_callable_gets_match(f):
    f.all[rx(r'\d')] = lambda m: str(int(m[0]) * 10)
    commit()
    assert 'return [10, 20]' in read('f.ts')


def test_plain_callable_gets_text(f):
    f['function load'] = str.upper
    commit()
    assert 'FUNCTION LOAD(n)' in read('f.ts')


def test_regex_flags(f):
    assert f.all[rx('CONST', 'i')].count == 2


def test_whole_word(f):
    assert f.all['x'].count > 2
    assert f.all[w('x')].count == 2
    f.all[w('x')] = 'z'
    commit()
    s = read('f.ts')
    assert 'const z = 1' in s and 'call(z,' in s and 'x2' in s


def test_whole_word_replacement_is_literal(f):
    f[w('x2')] = r'a\1'
    commit()
    assert r'const a\1 = 2' in read('f.ts')


def test_whitespace_insensitive(f):
    assert f[ws('function   load ( n ){')].text == 'function load(n) {'
    assert f[ws('if (n) { return [1,2] }')].text == 'if (n) {\n    return [1, 2]\n  }'
    with pytest.raises(EdError):
        f[ws('functionload')].text


def test_whole_line_anchor(tmp):
    write('a.css', '.card {\n  gap: 1px;\n}\n.page .card {\n  gap: 2px;\n}\n')
    e = E('a.css')
    assert e.all['.card {'].count == 2
    assert e[ln('.card {')].text == '.card {'
    assert e[ln('  .card {  ')].block.all[rx(r'\d+px')].texts == ['1px']
    e[ln('gap: 2px;')] = 'gap: 3px;'  # indentation is kept: the range starts after it
    commit()
    assert '\n  gap: 3px;\n' in read('a.css')


def test_whole_line_anchor_multi_line_and_ambiguity(tmp):
    write('b.ts', '}\n  return x\n}\nfoo()\n  return x\n}\n')
    assert E('b.ts')[ln('return x\n}\nfoo()')].text == 'return x\n}\nfoo()'
    with pytest.raises(EdError, match='matches 2 times'):
        E('b.ts')[ln('return x')].text


def test_ambiguous_anchor_suggests_ln_when_one_match_is_the_whole_line(tmp):
    write('a.css', '.card {\n}\n.page .card {\n}\n')
    with pytest.raises(EdError, match=r"only line 1 is exactly this text: use ln\('\.card \{'\)"):
        E('a.css')['.card {'].text


def test_hash_text_is_a_plain_literal(tmp):
    write('a.css', 'a { color: #fff; }\n')
    assert E('a.css')['#fff'].text == '#fff'


# ---- structure


def test_line_expands_to_full_lines(f):
    assert f['return [1'].line.text == '    return [1, 2]\n'
    assert f['if (n)':'return call'].inner.line.text == '  if (n) {\n    return [1, 2]\n  }\n'
    assert f['if (n)':'return call'].outer.line.text == '  if (n) {\n    return [1, 2]\n  }\n'


def test_block_counts_braces(f):
    assert f['function load'].block.text == SRC[SRC.index('function'):SRC.index('}\nconst x2') + 1]
    assert f['if (n)'].block.inner.text == '\n    return [1, 2]\n  '


def test_block_skips_braces_inside_parens(tmp):
    write('a.ts', 'function f(o = {a: 1}) {\n  return o\n}\n')
    assert E('a.ts')['function f'].block.inner.text == '\n  return o\n'


def test_block_without_brace_fails_closed(tmp):
    write('a.ts', 'type A = string;\nfunction g() {}\n')
    with pytest.raises(EdError, match=r"no '\{' after the anchor"):
        E('a.ts')['type A'].block.text


def test_paren_and_bracket(f):
    assert f['call'].paren.text == 'call(x, (y) => y)'
    assert f['call'].paren.inner.text == 'x, (y) => y'
    assert f['return ['].bracket.text == 'return [1, 2]'
    assert f['return ['].bracket.inner.text == '1, 2'


def test_unbalanced_brackets_fail_closed(tmp):
    write('a.ts', 'function f() {\n  if (x) {\n')
    with pytest.raises(EdError, match=r"(?s)unbalanced.*explicit anchors"):
        E('a.ts')['function f'].block.text


def test_tag_counts_nested_and_self_closing(tmp):
    write('a.tsx', '<Box a={() => x > 1}>\n  <Box />\n  <Box b="1">in</Box>\n</Box>\n<Box/>\n')
    e = E('a.tsx')
    assert e['<Box a'].tag.text == '<Box a={() => x > 1}>\n  <Box />\n  <Box b="1">in</Box>\n</Box>'
    assert e['<Box b'].tag.inner.text == 'in'
    assert e['<Box/>'].tag.text == '<Box/>'


def test_indent_block(tmp):
    write('a.py', 'def f():\n    a = 1\n\n    if a:\n        b()\n\ndef g():\n    pass\n')
    e = E('a.py')
    assert e['def f'].indent.text == 'def f():\n    a = 1\n\n    if a:\n        b()'
    assert e['def f'].indent.inner.text == '    a = 1\n\n    if a:\n        b()'
    assert e['if a'].indent.text == 'if a:\n        b()'


def test_search_inside_selection(f):
    assert f['function load'].block['return [1, 2]'].text == 'return [1, 2]'
    assert f['function load'].block.all['return'].count == 2
    with pytest.raises(EdError, match=r'inside the selection at lines 3-8'):
        f['function load'].block['const'].text


def test_uniqueness_is_per_selection_range(tmp):
    write('a.ts', 'function a() {\n  return 1\n}\nfunction b() {\n  return 2\n}\n')
    E('a.ts').all['function'].block['return'] = 'yield'
    commit()
    assert read('a.ts').count('yield') == 2


# ---- regions


def test_region_created_then_rewritten_in_place(tmp):
    write('a.ts', 'const a = 1\n')
    E('a.ts').region('gen').put('const b = 2')
    commit()
    assert read('a.ts') == 'const a = 1\n\n// region:gen\nconst b = 2\n// endregion\n'
    E('a.ts').region('gen').put('const c = 3\n')
    commit()
    assert read('a.ts') == 'const a = 1\n\n// region:gen\nconst c = 3\n// endregion\n'


def test_region_comment_style_by_extension(tmp):
    for name, start in (('a.py', '# region:x'), ('a.css', '/* region:x */'), ('a.md', '<!-- region:x -->'),
                        ('.env', '# region:x')):
        write(name, 'x\n')
        E(name).region('x').put('y')
    commit()
    for name, start in (('a.py', '# region:x'), ('a.css', '/* region:x */'), ('a.md', '<!-- region:x -->'),
                        ('.env', '# region:x')):
        assert start in read(name)


def test_region_read_missing_is_empty(tmp):
    write('a.ts', 'x\n')
    assert E('a.ts').region('none').text == ''


def test_region_finds_existing_markers_of_any_style(tmp):
    write('a.ts', 'a\n  // region:imports keep\n  old\n  // endregion\nb\n')
    E('a.ts').region('imports').put('  new\n')
    commit()
    assert read('a.ts') == 'a\n  // region:imports keep\n  new\n  // endregion\nb\n'


# ---- file sets


def test_globs_braces_and_exclusions(repo):
    e = E('src/**/*.{ts,tsx}', '!src/workers/**', '!src/pages')
    assert 'src/workers/sync.ts' not in e.paths and 'src/api/client.ts' in e.paths
    assert not any(p.startswith('src/pages/') for p in e.paths)
    assert 'src/components/Header.tsx' in e.paths


def _git_repo(d):
    import subprocess
    import shutil
    shutil.rmtree(d / '.git')
    subprocess.run(['git', 'init', '-q'], cwd=d, check=True)
    for f, text in {'.gitignore': 'build/\nnode_modules/\n.env\n', 'src/a.ts': 'a\n', 'venv/__init__.py': 'v\n',
                    'build/x.ts': 'x\n', 'node_modules/m/i.ts': 'm\n', '.hidden/h.ts': 'h\n', '.env': 'K=1\n',
                    'gone.ts': 'g\n'}.items():
        write(d / f, text)
    subprocess.run(['git', 'add', '-A'], cwd=d, check=True)
    subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'x'], cwd=d, check=True)
    (d / 'gone.ts').unlink()          # tracked but deleted: must not show up
    write(d / 'new.ts', 'n\n')        # untracked, not ignored: must show up


def test_globs_in_git_repo_follow_gitignore_not_folder_names(tmp):
    _git_repo(tmp)
    assert E('**/*.ts').paths == ['new.ts', 'src/a.ts']
    assert E('**/*.py').paths == ['venv/__init__.py']   # a real folder named venv is not skipped
    assert E('.hidden/*.ts').paths == ['.hidden/h.ts']  # dot paths need a pattern that names them
    assert len(E('.env*')) == 0                         # gitignored: globs skip it, like rg
    assert E('.env').text == 'K=1\n'                   # naming the file always works
    assert E('build/x.ts').text == 'x\n'


def test_globs_outside_git_skip_only_git_internals(tmp):
    import shutil
    shutil.rmtree(tmp / '.git')
    for f in ('venv/a.py', 'node_modules/m/i.py', '__pycache__/c.py', 'src/b.py', '.git/hooks/h.py'):
        write(tmp / f, 'x\n')
    assert E('**/*.py').paths == ['__pycache__/c.py', 'node_modules/m/i.py', 'src/b.py', 'venv/a.py']


def test_hidden_files_need_explicit_pattern(repo):
    assert '.env' not in E('*').paths
    assert E('.env*').paths == ['.env', '.env.example']


def test_having_lacking_and_narrowing(repo):
    e = E('src/**/*.ts')
    assert e.having('console.log(').paths == ['src/api/client.ts', 'src/legacy/bom.ts', 'src/workers/sync.ts']
    assert e.having(rx(r'^export \{', 'm')).paths == ['src/index.ts']
    assert 'src/api/client.ts' not in e.lacking('console.log(').paths
    assert e('client.ts').paths == ['src/api/client.ts']
    assert e('src/lib/*.ts').paths == ['src/lib/logger.ts', 'src/lib/time.ts']
    with pytest.raises(EdError, match='matches none'):
        e('nope.ts')


def test_having_is_evaluated_once(repo):
    e = E('src/**/*.ts').having('console.log(')
    e.all['console.log('] = 'logger.debug('
    assert len(e.paths) == 3  # still the same files after the first edit


def test_iterate_per_file(repo):
    seen = [f.path for f in E('src/lib/*.ts')]
    assert seen == ['src/lib/logger.ts', 'src/lib/time.ts']


def test_paths_from_shell(repo):
    e = E(*sh('ls src/lib/*.ts'))
    assert e.paths == ['src/lib/logger.ts', 'src/lib/time.ts']


def test_glob_matching_nothing_fails_on_use(repo):
    e = E('src/**/*.rs')
    with pytest.raises(EdError, match=r'no files matched src/\*\*/\*\.rs'):
        e.all['x'] = 'y'


def test_empty_after_having_is_a_no_op(repo):
    E('src/**/*.ts').having('NOT THERE').all['x'] = 'y'
    assert commit()['files'] == []


def test_missing_file_suggests_names(repo):
    with pytest.raises(EdError, match=r'file not found: src/lib/loger.ts\n  did you mean: src/lib/logger.ts'):
        E('src/lib/loger.ts')['x'].text
