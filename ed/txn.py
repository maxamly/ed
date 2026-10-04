"""Transactions: in-memory buffers, atomic commit, ensure + rollback, undo journal, output."""
from __future__ import annotations

import atexit
import difflib
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time

from .errors import EdError
from .files import FileBuf, _clean, decode, expand_braces, git_listing, glob_files, glob_regex, is_glob

DIFF_BUDGET = 200
ENSURE_TAIL = 2000


def _env_flag(name: str) -> bool:
    return os.environ.get(name, '').strip().lower() not in ('', '0', 'false', 'no')


def json_mode() -> bool:
    return _env_flag('ED_JSON') or '--json' in sys.argv


def dry_mode() -> bool:
    return _env_flag('ED_DRY') or '--dry' in sys.argv


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _umask_mode() -> int:
    m = os.umask(0)
    os.umask(m)
    return 0o666 & ~m


def state_dir(root: str) -> str:
    d = root
    while True:
        if os.path.exists(os.path.join(d, '.git')):
            break
        up = os.path.dirname(d)
        if up == d:
            d = root
            break
        d = up
    return os.path.join(d, '.ed')


def _ensure_state_dir(root: str) -> str:
    sd = state_dir(root)
    os.makedirs(os.path.join(sd, 'journal'), exist_ok=True)
    gi = os.path.join(sd, '.gitignore')
    if not os.path.exists(gi):
        with open(gi, 'w') as f:
            f.write('*\n')
    return sd


def new_journal_entry(root: str):
    """Create the next numbered journal directory; returns (id, path)."""
    jdir = os.path.join(_ensure_state_dir(root), 'journal')
    ids = sorted(int(x) for x in os.listdir(jdir) if x.isdigit())
    eid = (ids[-1] + 1) if ids else 1
    edir = os.path.join(jdir, f'{eid:06d}')
    os.makedirs(edir)
    return eid, edir


def fail(msg: str) -> None:
    """Print a failed run: JSON in --json mode, else the message and 'no files changed' on stderr."""
    if json_mode():
        print(json.dumps({'ok': False, 'error': msg, 'files': [], 'diff': ''}), flush=True)
    else:
        print(f'{msg}\ned: no files changed', file=sys.stderr, flush=True)


def atomic_write(path: str, data: bytes, mode) -> None:
    import tempfile  # imported lazily: most edx runs never write
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.' + os.path.basename(path) + '.', suffix='.ed~')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, _umask_mode() if mode is None else mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------- the transaction


class Tx:
    def __init__(self, parent=None):
        self.root = os.getcwd()
        self.parent = parent
        self.bufs: dict = {}     # current path -> FileBuf
        self.all: list = []      # every buffer touched, including removed ones
        self.gone: set = set()   # paths removed or moved away in this transaction
        self.ensures: list = []
        self.actions = 0
        self.aborted = False
        self.undoes: list = []
        self.notes: list = []
        self._git = False  # git_listing(root), computed on first glob; None outside a git repo

    # ---- paths and buffers

    def key(self, path) -> str:
        path = os.fspath(path)
        if os.path.isabs(path):
            rel = os.path.relpath(path, self.root)
            path = path if rel.startswith('..') else rel
        return _clean(path)

    def acted(self):
        self.actions += 1

    def note(self, msg: str):
        if msg not in self.notes:
            self.notes.append(msg)

    def get(self, path) -> FileBuf:
        k = self.key(path)
        b = self.bufs.get(k)
        if b is not None:
            return b
        if k in self.gone:
            raise EdError(f'{k} was removed or moved away earlier in this script')
        try:
            b = FileBuf.load(k, self.root)
        except EdError as ex:
            hint = self._similar(k)
            raise EdError(str(ex) + (f'\n  did you mean: {", ".join(hint)}' if hint else '')) from None
        self.bufs[k] = b
        self.all.append(b)
        return b

    def _similar(self, k):
        d = os.path.dirname(k)
        try:
            names = os.listdir(os.path.join(self.root, d) if d else self.root)
        except OSError:
            return []
        close = difflib.get_close_matches(os.path.basename(k), names, n=3, cutoff=0.6)
        return [os.path.join(d, n) if d else n for n in close]

    def expand(self, pats) -> list:
        pos, neg = [], []
        for p in pats:
            (neg if p.startswith('!') else pos).append(p.lstrip('!') if p.startswith('!') else p)
        neg_rx = [glob_regex(_clean(x)) for n in neg for x in expand_braces(n)]
        neg_dirs = [_clean(n).rstrip('/') + '/' for n in neg if not is_glob(n)]
        out = []
        for p in pos:
            for q in expand_braces(p):
                q = self.key(q) if not is_glob(q) else _clean(q)
                if not is_glob(q):
                    out.append(q)
                    continue
                if self._git is False:
                    self._git = git_listing(self.root)  # once per transaction: disk only changes at commit
                found = [f for f in glob_files(q, self.root, self._git) if f not in self.gone]
                rxq = glob_regex(q)
                found += [k for k in self.bufs if rxq.match(k) and k not in found]
                out.extend(found)
        out = [p for p in dict.fromkeys(out)
               if not any(r.match(p) for r in neg_rx) and not any(p.startswith(d) for d in neg_dirs)]
        return sorted(out) if any(is_glob(p) for p in pos) else out

    def create(self, path, text, overwrite=False):
        k = self.key(path)
        if k in self.bufs or (k not in self.gone and os.path.exists(os.path.join(self.root, k))):
            if not overwrite:
                raise EdError(f'create: {k} already exists (pass overwrite=True to replace it)')
            b = self.get(k)
            b.text = b.norm(text)
            return b
        b = FileBuf.new(k, self.root, text.replace('\r\n', '\n'))
        self.bufs[k] = b
        self.all.append(b)
        self.gone.discard(k)
        return b

    def mv(self, src, dst):
        s, d = self.key(src), self.key(dst)
        b = self.get(s)
        if d in self.bufs or (d not in self.gone and os.path.exists(os.path.join(self.root, d))):
            raise EdError(f'mv: {d} already exists')
        del self.bufs[s]
        self.gone.add(s)
        self.gone.discard(d)
        b.path = d
        self.bufs[d] = b
        return b

    def rm(self, path):
        k = self.key(path)
        b = self.get(k)
        del self.bufs[k]
        self.gone.add(k)
        b.path = None
        return b

    # ---- diff

    def changed(self) -> list:
        return [b for b in self.all if b.changed]

    def diff(self):
        parts, added, removed, files = [], 0, 0, []
        for b in sorted(self.changed(), key=lambda b: b.path or b.orig_path):
            old = b.orig_text if not b.created else ''
            new = '' if b.deleted else (decode(b.raw_override)[0] if b.raw_override is not None
                                        and b.text == b._override_text else b.final_text())
            if b.created:
                status, a, z = 'created', '/dev/null', 'b/' + b.path
            elif b.deleted:
                status, a, z = 'deleted', 'a/' + b.orig_path, '/dev/null'
            elif b.moved:
                status, a, z = 'renamed', 'a/' + b.orig_path, 'b/' + b.path
            else:
                status, a, z = 'modified', 'a/' + b.path, 'b/' + b.path
            ol, nl = old.splitlines(True), new.splitlines(True)
            if b.deleted:
                body = [f'--- {a}\n', f'+++ {z}\n', f'@@ deleted, {len(ol)} lines @@\n']
                plus, minus = 0, len(ol)
            else:
                body = list(difflib.unified_diff(ol, nl, a, z, n=3))
                if not body and b.moved:
                    body = [f'rename {b.orig_path} -> {b.path}\n']
                plus = sum(1 for x in body if x.startswith('+') and not x.startswith('+++'))
                minus = sum(1 for x in body if x.startswith('-') and not x.startswith('---'))
            for i, x in enumerate(body):
                if not x.endswith('\n'):
                    body[i] = x + '\n\\ No newline at end of file\n'
            kept = _fidelity(b)
            if kept and body:
                body.insert(0, f'# {b.path}: {kept} kept\n')
            parts.extend(body)
            added, removed = added + plus, removed + minus
            f = {'path': b.path or b.orig_path, 'status': status, 'added': plus, 'removed': minus}
            if b.moved:
                f['from'] = b.orig_path
            files.append(f)
        return ''.join(parts), files, added, removed

    # ---- commit

    def _check_stale(self, changed):
        for b in changed:
            if b.created:
                full = b.abspath()
                if os.path.exists(full):
                    raise EdError(f'{b.path} appeared on disk while the script ran; nothing written')
                continue
            full = b.abspath(b.orig_path)
            try:
                with open(full, 'rb') as f:
                    now = f.read()
            except FileNotFoundError:
                raise EdError(f'{b.orig_path} was deleted by someone else while the script ran; '
                              'nothing written') from None
            if now != b.orig_bytes:
                raise EdError(f'{b.orig_path} changed on disk since it was read (stale read); '
                              'nothing written, rerun the script')
            if b.moved and os.path.exists(b.abspath()):
                raise EdError(f'mv: {b.path} already exists; nothing written')

    def _write(self, changed):
        """Apply every change on disk; returns undo steps. On any OSError, restores everything first."""
        undo = []
        try:
            for b in changed:
                if not b.deleted:  # write the new content at the (possibly new) path
                    dst, made = b.abspath(), []
                    d = os.path.dirname(dst)
                    while d and not os.path.isdir(d):
                        made.append(d)
                        d = os.path.dirname(d)
                    atomic_write(dst, b.final_bytes(), b.mode)
                    if b.created or b.moved:
                        undo.append(lambda dst=dst, made=made: (os.unlink(dst),
                                    [os.rmdir(x) for x in made if not os.listdir(x)]))
                    else:
                        undo.append(lambda dst=dst, b=b: atomic_write(dst, b.orig_bytes, b.mode))
                if b.deleted or b.moved:  # the old path goes away
                    src = b.abspath(b.orig_path)
                    os.unlink(src)
                    undo.append(lambda src=src, b=b: atomic_write(src, b.orig_bytes, b.mode))
        except OSError as ex:
            _rollback(undo)
            raise EdError(f'write failed ({ex}); every file restored') from None
        return undo

    def _journal(self, changed, kind='run'):
        eid, edir = new_journal_entry(self.root)
        jdir = os.path.dirname(edir)
        ids = sorted(int(x) for x in os.listdir(jdir) if x.isdigit())
        files = []
        for i, b in enumerate(changed):
            rec = {'path': b.abspath() if not b.deleted else None,
                   'orig': b.abspath(b.orig_path) if not b.created else None,
                   'mode': b.mode,
                   'after': None if b.deleted else _sha(b.final_bytes())}
            if not b.created:
                with open(os.path.join(edir, f'{i}.orig'), 'wb') as f:
                    f.write(b.orig_bytes)
                rec['blob'] = f'{i}.orig'
            files.append(rec)
        man = {'id': eid, 'time': time.time(), 'kind': kind, 'undoes': self.undoes, 'files': files}
        with open(os.path.join(edir, 'manifest.json'), 'w') as f:
            json.dump(man, f)
        for old in ids[:-49]:
            _rmtree(os.path.join(jdir, f'{old:06d}'))
        return edir

    def commit(self) -> dict:
        """Write everything, run ensure checks, roll back on failure. Raises EdError on failure."""
        changed = self.changed()
        diff, files, added, removed = self.diff()
        res = {'ok': True, 'dry': dry_mode(), 'files': files, 'added': added, 'removed': removed,
               'diff': diff, 'ensure': [], 'error': None}
        if self.notes:
            res['notes'] = list(self.notes)
        if diff:
            try:
                sd = _ensure_state_dir(self.root)
                with open(os.path.join(sd, 'last.diff'), 'w', encoding='utf-8') as f:
                    f.write(diff)
            except OSError:
                pass
        if res['dry']:
            self._report(res)
            return res
        self._check_stale(changed)
        edir = self._journal(changed, 'undo' if self.undoes else 'run') if changed else None
        undo = self._write(changed)
        changed_paths = [b.path for b in changed if not b.deleted]
        for cmd in self.ensures:
            if '{changed}' in cmd and not changed_paths:
                res['ensure'].append({'cmd': cmd, 'ok': True, 'skipped': True})
                continue
            run = cmd.replace('{changed}', ' '.join(shlex.quote(p) for p in changed_paths))
            t0 = time.time()
            p = subprocess.run(run, shell=True, cwd=self.root, capture_output=True, text=True,
                               errors='replace')
            out = (p.stdout + p.stderr)[-ENSURE_TAIL:]
            ok = p.returncode == 0
            res['ensure'].append({'cmd': run, 'ok': ok, 'code': p.returncode,
                                  'seconds': round(time.time() - t0, 2), **({} if ok else {'output': out})})
            if not ok:
                _rollback(undo)
                if edir:
                    _rmtree(edir)
                res['ok'] = False
                res['error'] = f'ensure failed: {run} (exit {p.returncode}); every change rolled back'
                self._report(res)
                raise EdError(res['error'], res)
        self._sync_parent(changed)
        self._report(res)
        return res

    def _sync_parent(self, changed):
        par = self.parent
        if par is None:
            return
        for b in changed:
            for p in (b.orig_path, b.path):
                pb = par.bufs.get(p)
                if pb is not None and not pb.changed:
                    del par.bufs[p]
                    par.all.remove(pb)

    # ---- output

    def _report(self, res):
        if json_mode():
            print(json.dumps(res))
            return
        out = []
        diff = res['diff']
        if diff:
            lines = diff.splitlines()
            budget = int(os.environ.get('ED_DIFF_LINES', DIFF_BUDGET))
            if len(lines) > budget:
                lines = lines[:budget] + [f'... +{len(lines) - budget} more lines, run `edx diff`']
            out.append('\n'.join(lines))
        out.extend(f'ed: {n}' for n in res.get('notes', []))
        n = len(res['files'])
        if res['dry']:
            summary = (f'ed: dry run, would change {n} file{"s" * (n != 1)} (+{res["added"]} -{res["removed"]}); '
                       f'nothing written' + (', ensure skipped' if self.ensures else ''))
        elif not res['ok']:
            summary = ''
        elif n:
            summary = f'ed: {n} file{"s" * (n != 1)} changed (+{res["added"]} -{res["removed"]})'
        elif self.actions:
            summary = 'ed: no changes'
        else:
            summary = ''
        for e in res['ensure']:
            if e.get('skipped'):
                summary += f'\ned: ensure skipped (no changed files): {e["cmd"]}'
            elif e['ok']:
                summary += f'; ensure ok: {e["cmd"]}'
        if summary:
            out.append(summary.strip('\n'))
        if out:
            print('\n'.join(out), flush=True)
        if not res['ok']:
            failed = res['ensure'][-1]
            tail = failed.get('output', '').rstrip()
            print(f'ed: {res["error"]}' + (f'\n{tail}' if tail else ''), file=sys.stderr, flush=True)


def _fidelity(b) -> str:
    """Traits of an existing file that ed preserved, for the diff header ('' if unremarkable)."""
    if b.created or b.deleted:
        return ''
    traits = []
    if b.newline == '\r\n':
        traits.append('CRLF')
    elif b.newline is None:
        traits.append('mixed line endings')
    if b.bom:
        traits.append('BOM')
    if b.enc not in ('utf-8',):
        traits.append(b.enc)
    if b.orig_text and not b.orig_text.endswith('\n'):
        traits.append('no final newline')
    return ', '.join(traits)


def _rollback(undo):
    for fn in reversed(undo):
        try:
            fn()
        except OSError as ex:  # keep restoring the rest
            print(f'ed: rollback step failed: {ex}', file=sys.stderr)


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------- transaction stack and hooks

_stack: list = []
_hooks = False


def current() -> Tx:
    if not _stack:
        _stack.append(Tx())
        _install_hooks()
    return _stack[-1]


def push() -> Tx:
    tx = Tx(parent=current())
    _stack.append(tx)
    return tx


def pop(commit: bool):
    tx = _stack.pop()
    if commit and not tx.aborted:
        tx.commit()


def reset():
    """Forget all pending edits (used by tests)."""
    del _stack[:]


def report_error(ev, tb=None) -> None:
    """Print a failure the way agents should see it: the script line, the reason, and no traceback."""
    if len(ev.args) > 1 and isinstance(ev.args[1], dict):
        return  # commit() already reported it (ensure failure)
    import traceback  # imported lazily: it is slow to load and only needed on errors
    where = ''
    pkg = os.path.dirname(os.path.abspath(__file__)) + os.sep
    for fs in reversed(traceback.extract_tb(tb) if tb else []):
        if not os.path.abspath(fs.filename).startswith(pkg):
            src = (fs.line or '').strip()
            where = f' at line {fs.lineno}' + (f' `{src[:80]}`' if src else '')
            break
    msg = str(ev.args[0]) if ev.args else str(ev)
    fail(msg + where if json_mode() else f'ed: error{where}:\n{msg}')


def abort() -> bool:
    """Mark every open transaction failed; True if any edit had been attempted."""
    for tx in _stack:
        tx.aborted = True
    return any(tx.actions or tx.changed() for tx in _stack)


def finish() -> int:
    """Commit the outermost transaction (script end). Returns the exit code."""
    if not _stack:
        return 0
    tx = _stack[0]
    try:
        if tx.aborted:
            return 1
        tx.commit()
        return 0
    except EdError as ex:
        report_error(ex)
        return 1
    finally:
        _stack.clear()


def _install_hooks():
    global _hooks
    if _hooks:
        return
    _hooks = True
    prev = sys.excepthook

    def hook(et, ev, tb):
        acted = abort()
        if isinstance(ev, EdError):
            report_error(ev, tb)
            return
        prev(et, ev, tb)
        if acted and not json_mode():
            print('ed: no files changed', file=sys.stderr)

    sys.excepthook = hook
    atexit.register(_at_exit)


def _at_exit():
    if _stack and not _stack[0].aborted and finish():
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
