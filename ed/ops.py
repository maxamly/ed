"""Module-level helpers: checks, shell, files, undo journal."""
from __future__ import annotations

import json
import os
import subprocess
import time

from .errors import EdError

from .txn import _sha, current, dry_mode, new_journal_entry, state_dir
from .hints import loc


def ensure(cmd: str):
    """Run cmd after writing; if it fails every change is rolled back. {changed} = touched files."""
    current().ensures.append(cmd)


def expect(sel, n: int):
    """Fail now unless the selection (or a number) equals n."""
    got = sel if isinstance(sel, int) else sel.count
    if got != n:
        where = ''
        if not isinstance(sel, int):
            rs = sel._resolve(lenient=True)
            where = ''.join(f'\n    {loc(r.buf, r.start)}' for r in rs[:5])
            if len(rs) > 5:
                where += f'\n    ... and {len(rs) - 5} more'
        raise EdError(f'expect: wanted {n} matches, found {got}{where}')
    return got


def sh(cmd: str, check: bool = True) -> list:
    """Run a shell command; return its stdout as a list of non-empty lines."""
    p = subprocess.run(cmd, shell=True, cwd=current().root, capture_output=True, text=True, errors='replace')
    if check and p.returncode != 0:
        raise EdError(f'sh: {cmd} exited {p.returncode}\n{(p.stdout + p.stderr)[-2000:]}')
    return [ln for ln in p.stdout.splitlines() if ln.strip()]


def mv(src, dst):
    """Rename a file inside the transaction."""
    current().mv(src, dst)
    current().acted()


def rm(*paths):
    """Delete files (paths or globs) inside the transaction."""
    from .select import E
    tx = current()
    for p in E(*paths)._paths:
        tx.rm(p)
    tx.acted()


# ---------------------------------------------------------------- journal


def _entries(root):
    jdir = os.path.join(state_dir(root), 'journal')
    if not os.path.isdir(jdir):
        return []
    out = []
    for name in sorted(os.listdir(jdir)):
        mf = os.path.join(jdir, name, 'manifest.json')
        if name.isdigit() and os.path.exists(mf):
            with open(mf) as f:
                m = json.load(f)
            m['dir'] = os.path.join(jdir, name)
            out.append(m)
    return out


def checkpoint(name: str = 'checkpoint'):
    """Mark the current state; undo(name) later reverts every run after this point."""
    if dry_mode():
        return
    eid, edir = new_journal_entry(current().root)
    with open(os.path.join(edir, 'manifest.json'), 'w') as f:
        json.dump({'id': eid, 'time': time.time(), 'kind': 'checkpoint', 'name': name, 'undoes': [], 'files': []}, f)


def undo(to=1):
    """Revert the last `to` runs, or every run since checkpoint(name) when `to` is a name."""
    tx = current()
    ents = _entries(tx.root)
    undone = {i for e in ents for i in e.get('undoes', [])}
    runs = [e for e in ents if e['kind'] == 'run' and e['id'] not in undone]
    if isinstance(to, str):
        cps = [e for e in ents if e['kind'] == 'checkpoint' and e.get('name') == to]
        if not cps:
            raise EdError(f'undo: no checkpoint named {to!r}')
        pick = [e for e in runs if e['id'] > cps[-1]['id']]
    else:
        pick = runs[-to:] if to > 0 else []
    if not pick:
        raise EdError('undo: nothing to undo')
    seen = set()
    for e in reversed(pick):
        for rec in e['files']:
            _revert(tx, e, rec, seen)
    tx.undoes.extend(e['id'] for e in pick)
    tx.acted()


def _rel(tx, p):
    return tx.key(p) if p else None


def _revert(tx, entry, rec, seen):
    path, orig = _rel(tx, rec['path']), _rel(tx, rec['orig'])
    blob = None
    if rec.get('blob'):
        with open(os.path.join(entry['dir'], rec['blob']), 'rb') as f:
            blob = f.read()
    if path and path not in seen:
        full = os.path.join(tx.root, path)
        try:
            with open(full, 'rb') as f:
                now = f.read()
        except FileNotFoundError:
            now = None
        if now is None or _sha(now) != rec['after']:
            raise EdError(f'undo: {path} was changed after that run; refusing to overwrite it')
    if path:
        seen.add(path)
    if orig:
        seen.add(orig)
    if path is None:  # the run deleted orig: bring it back
        b = tx.create(orig, '')
        b.restore(blob, rec.get('mode'))
    elif orig is None:  # the run created path: remove it
        tx.rm(path)
    else:
        if path != orig:
            tx.mv(path, orig)
        tx.get(orig).restore(blob, rec.get('mode'))


__all__ = ['ensure', 'expect', 'sh', 'mv', 'rm', 'checkpoint', 'undo']
