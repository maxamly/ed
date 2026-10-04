"""File buffers (with byte-exact fidelity), glob expansion and small text helpers."""
from __future__ import annotations

import codecs
import os
import re
import stat
import subprocess

from .errors import EdError

# ---------------------------------------------------------------- text helpers

def line_start(text: str, pos: int) -> int:
    return text.rfind('\n', 0, pos) + 1


def line_end(text: str, pos: int) -> int:
    """Position of the '\\n' ending the line containing pos (or len(text))."""
    i = text.find('\n', pos)
    return len(text) if i == -1 else i


def line_no(text: str, pos: int) -> int:
    return text.count('\n', 0, pos) + 1


def indent_of(line: str) -> str:
    return line[:len(line) - len(line.lstrip(' \t'))]


# ---------------------------------------------------------------- comments

_COMMENTS = {}
for _exts, _style in (
    ('ts tsx js jsx mjs cjs mts cts java c h cc cpp hpp cs go rs swift kt kts scala dart php '
     'scss less groovy gradle proto zig v', ('//', '')),
    ('py pyi sh bash zsh fish rb pl r yml yaml toml env conf cfg ini properties mk '
     'dockerfile makefile nix tf hcl cmake ex exs jl ps1', ('#', '')),
    ('css', ('/*', ' */')),
    ('html htm xml svg md mdx vue svelte astro', ('<!--', ' -->')),
    ('sql lua hs elm ada', ('--', '')),
    ('clj cljs lisp el scm', (';;', '')),
):
    for _e in _exts.split():
        _COMMENTS[_e] = _style


def comment_style(path: str):
    base = os.path.basename(path).lower()
    if base.startswith('.env'):
        return ('#', '')
    ext = base.rsplit('.', 1)[-1] if '.' in base.lstrip('.') else base.lstrip('.')
    return _COMMENTS.get(ext, ('#', ''))


# ---------------------------------------------------------------- file buffer

_BOMS = ((codecs.BOM_UTF32_LE, 'utf-32-le'), (codecs.BOM_UTF32_BE, 'utf-32-be'),
         (codecs.BOM_UTF8, 'utf-8'), (codecs.BOM_UTF16_LE, 'utf-16-le'),
         (codecs.BOM_UTF16_BE, 'utf-16-be'))


def decode(data: bytes):
    """bytes -> (text with '\\n' newlines, encoding, bom, newline). Exact round trip via encode()."""
    bom = b''
    enc = None
    for b, e in _BOMS:
        if data.startswith(b):
            bom, enc = b, e
            break
    body = data[len(bom):]
    if enc is None:
        try:
            text = body.decode('utf-8')
            enc = 'utf-8'
        except UnicodeDecodeError:
            text = body.decode('latin-1')
            enc = 'latin-1'
    else:
        text = body.decode(enc)
    crlf = text.count('\r\n')
    lf = text.count('\n') - crlf
    if crlf and not lf:
        newline = '\r\n'
        text = text.replace('\r\n', '\n')
    elif crlf and lf:
        newline = None  # mixed: keep text raw so untouched lines round-trip
    else:
        newline = '\n'
    return text, enc, bom, newline


def encode(text: str, enc: str, bom: bytes, newline) -> bytes:
    if newline == '\r\n':
        text = text.replace('\n', '\r\n')
    return bom + text.encode(enc)


class FileBuf:
    """One file inside a transaction. `text` always uses the file's own newline model."""

    def __init__(self, path: str, absdir: str):
        self.path = path            # current display path; None once removed
        self.orig_path = None       # path on disk at load time; None if created in this run
        self.absdir = absdir
        self.orig_bytes = None
        self.orig_text = ''
        self.text = ''
        self.enc, self.bom, self.newline = 'utf-8', b'', '\n'
        self.mode = None
        self.stat_key = None
        self.binary = False
        self.raw_override = None    # exact bytes to write (undo), valid while text is unchanged
        self._override_text = None

    @classmethod
    def load(cls, path: str, absdir: str) -> 'FileBuf':
        b = cls(path, absdir)
        full = b.abspath(path)
        try:
            with open(full, 'rb') as f:
                data = f.read()
            st = os.stat(full)
        except FileNotFoundError:
            raise EdError(f'file not found: {path}') from None
        except IsADirectoryError:
            raise EdError(f'is a directory: {path}') from None
        b.orig_path = path
        b.orig_bytes = data
        b.mode = stat.S_IMODE(st.st_mode)
        b.stat_key = (st.st_size, st.st_mtime_ns)
        b.binary = b'\0' in data[:8192] and not data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
        b.text, b.enc, b.bom, b.newline = decode(data)
        b.orig_text = b.text
        return b

    @classmethod
    def new(cls, path: str, absdir: str, text: str) -> 'FileBuf':
        b = cls(path, absdir)
        b.text = text
        return b

    def abspath(self, path=None) -> str:
        p = os.path.join(self.absdir, path if path is not None else self.path)
        return os.path.realpath(p) if os.path.islink(p) else p

    def norm(self, s: str) -> str:
        """Normalise inserted text to this buffer's newline model."""
        s = s.replace('\r\n', '\n')
        if self.newline is None:  # mixed file: use its majority style
            crlf = self.orig_text.count('\r\n')
            if crlf * 2 > self.orig_text.count('\n'):
                s = s.replace('\n', '\r\n')
        return s

    def final_text(self) -> str:
        t = self.text
        if self.orig_path is not None and self.orig_text.endswith('\n') and t and not t.endswith('\n'):
            t += '\n'  # rule 8: keep the trailing newline the file had
        return t

    def final_bytes(self) -> bytes:
        if self.raw_override is not None and self.text == self._override_text:
            return self.raw_override
        return encode(self.final_text(), self.enc, self.bom, self.newline)

    def restore(self, data: bytes, mode=None):
        """Make this buffer write exactly `data` (used by undo)."""
        self.text, self.enc, self.bom, self.newline = decode(data)
        self.raw_override, self._override_text = data, self.text
        if mode is not None:
            self.mode = mode

    @property
    def created(self) -> bool:
        return self.orig_path is None

    @property
    def deleted(self) -> bool:
        return self.path is None

    @property
    def moved(self) -> bool:
        return self.orig_path is not None and self.path is not None and self.path != self.orig_path

    @property
    def modified(self) -> bool:
        if self.path is None or self.orig_path is None:
            return False
        return self.final_bytes() != self.orig_bytes

    @property
    def changed(self) -> bool:
        if self.created:
            return not self.deleted
        return self.deleted or self.moved or self.modified


# ---------------------------------------------------------------- globbing

_ALWAYS_SKIP = {'.git', '.ed'}  # version-control internals and ed's own journal
_GLOB_CHARS = re.compile(r'[*?\[]')


def expand_braces(pat: str):
    m = re.search(r'\{([^{}]*,[^{}]*)\}', pat)
    if not m:
        return [pat]
    out = []
    for alt in m.group(1).split(','):
        out.extend(expand_braces(pat[:m.start()] + alt + pat[m.end():]))
    return out


def glob_regex(pat: str):
    i, out = 0, ''
    while i < len(pat):
        c = pat[i]
        if pat.startswith('**/', i):
            out += '(?:.*/)?'
            i += 3
            continue
        if pat.startswith('**', i):
            out += '.*'
            i += 2
            continue
        if c == '*':
            out += '[^/]*'
        elif c == '?':
            out += '[^/]'
        elif c == '[':
            j = pat.find(']', i + 1)
            if j == -1:
                out += re.escape(c)
            else:
                cls = pat[i + 1:j].replace('\\', '\\\\')
                if cls.startswith('!'):
                    cls = '^' + cls[1:]
                out += '[' + cls + ']'
                i = j
        else:
            out += re.escape(c)
        i += 1
    return re.compile(out + r'\Z')


def _clean(p: str) -> str:
    p = os.path.normpath(p).replace(os.sep, '/')
    return p[2:] if p.startswith('./') else p


def is_glob(pat: str) -> bool:
    return bool(_GLOB_CHARS.search(pat)) or bool(re.search(r'\{[^{}]*,[^{}]*\}', pat))


def git_listing(root: str):
    """Files git would show under root (tracked + untracked, minus .gitignore'd), or None outside git."""
    try:
        p = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                           cwd=root, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return [f for f in p.stdout.decode('utf-8', 'surrogateescape').split('\0') if f]


def _hidden(path: str) -> bool:
    return any(part.startswith('.') for part in path.split('/'))


def glob_files(pattern: str, root: str, listing=None):
    """Expand one positive pattern (already brace-expanded) relative to root.

    listing: the git_listing() of root, so a git repo is filtered by its .gitignore instead of by
    guessing directory names; None walks the tree, skipping only .git and .ed.
    Dotfiles match only when the pattern names a dot segment (shell-glob convention)."""
    pattern = _clean(pattern)
    if not _GLOB_CHARS.search(pattern):
        return [pattern] if os.path.isfile(os.path.join(root, pattern)) else []
    rx = glob_regex(pattern)
    want_hidden = '/.' in '/' + pattern
    if listing is not None:
        return [f for f in listing
                if rx.match(f) and (want_hidden or not _hidden(f)) and os.path.isfile(os.path.join(root, f))]
    base = []
    for part in pattern.split('/'):
        if _GLOB_CHARS.search(part):
            break
        base.append(part)
    start = os.path.join(root, '/'.join(base)) if base else root
    out = []
    if not os.path.isdir(start):
        return out
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        rel = os.path.relpath(dirpath, root).replace(os.sep, '/')
        rel = '' if rel == '.' else rel + '/'
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _ALWAYS_SKIP and (want_hidden or not d.startswith('.')))
        out.extend(rel + fn for fn in filenames
                   if (want_hidden or not fn.startswith('.')) and rx.match(rel + fn))
    return out


def match_any(path: str, regexes) -> bool:
    return any(r.match(path) for r in regexes)
