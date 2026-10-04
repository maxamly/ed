"""edx: safe multi-file edits with Python scripts (search and read with rg, grep and cat).

usage:
  edx [--dry] [--json] [--max-diff N] [--strict] [SCRIPT | -f FILE]   (script from stdin if omitted)
  edx diff
  edx undo [N | CHECKPOINT]
  edx spec                  print only the scripting reference above

Scripts run with `from ed import *` already done and commit at the end; any failure exits 1
and writes nothing. diff and spec never write.
"""
from __future__ import annotations

import argparse
import ast
import linecache
import os
import re
import sys

from . import txn
from .errors import EdError

USAGE = __doc__.strip()
STRICT_NAMES = {'open', 'exec', 'eval', 'compile', '__import__', 'os', 'subprocess', 'globals', 'getattr'}


class Usage(Exception):
    pass


COMMANDS: dict = {}


def subcommand(name: str):
    """Register a subcommand: `edx NAME ...` calls the function with the rest of the arguments."""
    def register(fn):
        COMMANDS[name] = fn
        return fn
    return register


class _Parser(argparse.ArgumentParser):
    """argparse, but errors go through our usage message (exit 2) instead of argparse's own."""

    def __init__(self, *args, **kw):
        super().__init__(*args, add_help=False, **kw)

    def error(self, message):
        raise Usage(message)


# ---------------------------------------------------------------- script mode


def strict_check(code: str, filename: str) -> None:
    """Reject imports and direct file/process access. A guard against accidents, not a sandbox."""
    try:
        tree = ast.parse(code, filename)
    except SyntaxError:
        return  # compile() reports it with a proper location
    for node in ast.walk(tree):
        bad = None
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bad = 'import'
        elif isinstance(node, ast.Name) and node.id in STRICT_NAMES:
            bad = node.id
        elif isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            bad = node.attr
        if bad:
            raise EdError(f'--strict: `{bad}` is not allowed (line {node.lineno}); '
                          'use E(), sh() and the other ed helpers instead')


class _DelSelections(ast.NodeTransformer):
    """`del sel` on a plain name only unbinds it in Python; in edx scripts it deletes the selected text."""

    def visit_Delete(self, node):
        names = [t for t in node.targets if isinstance(t, ast.Name)]
        if not names:
            return node
        calls = [ast.copy_location(ast.Expr(ast.Call(ast.Name('__ed_del__', ast.Load()),
                                                     [ast.Name(t.id, ast.Load())], [])), node) for t in names]
        return calls + [node]


def _ed_del(obj):
    from .select import Selection
    if isinstance(obj, Selection):
        obj.delete()


_DEL_LINE = re.compile(r'^(\s*)del\s+(.+?)\s*$')


def _parse(code: str, filename: str):
    """ast.parse, but `del e.lines(9, 14)` (a SyntaxError in Python) becomes `e.lines(9, 14).delete()`."""
    for _ in range(100):
        try:
            return ast.parse(code, filename)
        except SyntaxError as ex:
            if 'cannot delete function call' not in str(ex.msg) or not ex.lineno:
                raise
            lines = code.split('\n')
            m = _DEL_LINE.match(lines[ex.lineno - 1])
            try:
                single_call = m and isinstance(ast.parse(m.group(2), mode='eval').body, ast.Call)
            except SyntaxError:
                single_call = False
            if not single_call:
                raise
            lines[ex.lineno - 1] = f'{m.group(1)}({m.group(2)}).delete()'
            code = '\n'.join(lines)
    return ast.parse(code, filename)


def compile_script(code: str, filename: str):
    tree = ast.fix_missing_locations(_DelSelections().visit(_parse(code, filename)))
    return compile(tree, filename, 'exec')


def run_script(code: str, filename: str, strict: bool = False) -> int:
    import traceback
    txn.reset()
    if strict:
        try:
            strict_check(code, filename)
        except EdError as ex:
            txn.report_error(ex)
            return 1
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    try:
        compiled = compile_script(code, filename)
    except SyntaxError as ex:  # report the script's own line, not edx's internals
        txn.fail(''.join(traceback.format_exception_only(type(ex), ex)).rstrip())
        return 1
    ns = {'__name__': '__main__', '__file__': filename, '__builtins__': __builtins__, '__ed_del__': _ed_del,
          're': re}
    if not strict:
        from pathlib import Path
        ns['Path'] = Path
    exec('from ed import *', ns)
    try:
        exec(compiled, ns)
    except SystemExit as ex:
        if ex.code not in (None, 0):
            txn.abort()
            if txn._stack and not txn.json_mode():
                print('ed: script exited with an error; no files changed', file=sys.stderr)
            txn.reset()
            return ex.code if isinstance(ex.code, int) else 1
    except EdError as ex:
        txn.abort()
        txn.report_error(ex, ex.__traceback__)
        txn.reset()
        return 1
    except BaseException as ex:  # noqa: BLE001 - any script failure aborts the run
        acted = txn.abort()
        if txn.json_mode():
            txn.fail(''.join(traceback.format_exception_only(type(ex), ex)).strip())
        else:
            traceback.print_exception(type(ex), ex, ex.__traceback__.tb_next)  # without this frame
            if acted:
                print('ed: no files changed', file=sys.stderr)
        txn.reset()
        return 1
    return txn.finish()


def script_mode(args) -> int:
    p = _Parser(prog='edx')
    p.add_argument('--dry', action='store_true')
    p.add_argument('--json', action='store_true')
    p.add_argument('--strict', action='store_true')
    p.add_argument('--max-diff', type=int)
    p.add_argument('-f', dest='file')
    p.add_argument('script', nargs='?')
    o = p.parse_args(args)
    for flag, var in ((o.dry, 'ED_DRY'), (o.json, 'ED_JSON')):
        if flag:
            os.environ[var] = '1'
    if o.max_diff is not None:
        os.environ['ED_DIFF_LINES'] = str(o.max_diff)
    code, path, strict = o.script, o.file, o.strict
    if path is not None and code is not None:
        raise Usage('give either -f FILE or an inline script, not both')
    if path is not None:
        try:
            with open(path, encoding='utf-8') as f:
                code = f.read()
        except OSError as ex:
            raise Usage(f'cannot read {path}: {ex.strerror}') from None
        filename = path
    elif code is not None:
        filename = '<edx>'
    else:
        if sys.stdin.isatty():
            raise Usage('no script given')
        code, filename = sys.stdin.read(), '<stdin>'
    sys.argv = [filename]
    return run_script(code, filename, strict)


# ---------------------------------------------------------------- read-only subcommands


@subcommand('diff')
def cmd_diff(args) -> int:
    _Parser(prog='edx diff').parse_args(args)
    p = os.path.join(txn.state_dir(os.getcwd()), 'last.diff')
    if not os.path.exists(p):
        print('ed: no diff recorded yet', file=sys.stderr)
        return 1
    with open(p, encoding='utf-8') as f:
        sys.stdout.write(f.read())
    return 0


@subcommand('undo')
def cmd_undo(args) -> int:
    from .ops import undo
    p = _Parser(prog='edx undo')
    p.add_argument('to', nargs='?', default='1')
    to = p.parse_args(args).to
    txn.reset()
    try:
        undo(int(to) if to.isdigit() else to)
    except EdError as ex:
        txn.report_error(ex)
        txn.reset()
        return 1
    return txn.finish()


def _spec() -> str:
    with open(os.path.join(os.path.dirname(__file__), 'SPEC.md'), encoding='utf-8') as f:
        return f.read()


@subcommand('spec')
def cmd_spec(args) -> int:
    _Parser(prog='edx spec').parse_args(args)
    sys.stdout.write(_spec())
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args and args[0] in ('-h', '--help', 'help') or (not args and sys.stdin.isatty()):
            print(_spec().rstrip() + '\n\n---\n\n' + USAGE)
            return 0
        if args and args[0] in COMMANDS:
            cmd = COMMANDS[args[0]]
            try:
                return cmd(args[1:])
            except EdError as ex:
                txn.report_error(ex)
                return 1
            finally:
                if args[0] != 'undo':
                    txn.reset()
        return script_mode(args)
    except Usage as ex:
        print(f'edx: {ex}\n\n{USAGE}', file=sys.stderr)
        return 2
