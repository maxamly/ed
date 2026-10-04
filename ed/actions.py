"""Low-level edit primitives: batched, overlap-checked application; tidy deletes; line-aware inserts."""
from __future__ import annotations

import re
import textwrap

from .errors import EdError
from .files import indent_of, line_end, line_no, line_start


def apply(edits) -> None:
    """Apply [(buf, start, end, new_text)] atomically: validate every buffer first, then mutate."""
    groups: dict = {}
    for buf, s, e, new in edits:
        groups.setdefault(id(buf), (buf, []))[1].append((s, e, new))
    plans = []
    for buf, lst in groups.values():
        lst = sorted(dict.fromkeys(lst), key=lambda x: (x[0], x[1]))
        for (s1, e1, _), (s2, e2, _) in zip(lst, lst[1:]):
            if s2 < e1:
                raise EdError(f'overlapping edits in {buf.path} at line {line_no(buf.text, s2)}: '
                              'two ranges of the selection overlap; narrow it')
        plans.append((buf, lst))
    for buf, lst in plans:
        t = buf.text
        out, pos = [], 0
        for s, e, new in lst:
            out.append(t[pos:s])
            out.append(buf.norm(new))
            pos = e
        out.append(t[pos:])
        buf.text = ''.join(out)


def tidy_delete(t: str, s: int, e: int, collapse: bool = True):
    """Widen a deletion so it removes whole lines instead of leaving blank or dangling ones."""
    if s >= e:
        return s, e
    multi = t.count('\n', s, e) >= 1
    end_at_boundary = t[e - 1] == '\n' or e == len(t)
    if t[s] == '\n' and t[e - 1] == '\n' and s > 0 and t[s - 1] != '\n' and e - s > 1:
        return s + 1, e  # e.g. the inner of two marker lines: keep the markers on separate lines
    ls = line_start(t, s)
    if t[ls:s].strip() != '':
        return s, e
    if t[e - 1] == '\n':
        ne = e
    else:
        le = line_end(t, e)
        if t[e:le].strip() != '':
            return s, e
        ne = le + 1 if le < len(t) else le
    ns = ls
    if ne == len(t) and not t.endswith('\n') and ns > 0:
        ns -= 1  # deleting the last line of a file without trailing newline
    if collapse and (multi or not end_at_boundary):
        prev_blank = ns == 0 or (ns >= 2 and t[ns - 2] == '\n')
        if prev_blank and ne < len(t) and t[ne] == '\n':
            ne += 1  # collapse the blank line pair left behind
        elif ne == len(t) and ns >= 2 and t[ns - 2] == '\n':
            ns -= 1
    return ns, ne


def is_whole_lines(t: str, s: int, e: int) -> bool:
    return s < e and (s == 0 or t[s - 1] == '\n') and (t[e - 1] == '\n' or e == len(t))


def line_pos(t: str, pos: int, after: bool) -> int:
    """Nearest line boundary: start of pos's line (before) or just past its end (after)."""
    if not after:
        return line_start(t, pos)
    if pos == 0 or t[pos - 1] == '\n':
        return pos
    le = line_end(t, pos)
    return le + 1 if le < len(t) else le


def as_line(t: str, pos: int, chunk: str) -> str:
    """Wrap chunk (whole lines) so inserting it at pos keeps it on its own lines."""
    if not chunk.endswith('\n'):
        chunk += '\n'
    if pos > 0 and t[pos - 1] != '\n':
        chunk = '\n' + chunk
        if pos == len(t):
            chunk = chunk[:-1]
    return chunk


def find_lines(t: str, chunk: str) -> int:
    """1-based line where chunk's lines (indentation-insensitive) appear in t, else 0."""
    want = [ln.strip() for ln in chunk.rstrip('\n').split('\n')]
    have = [ln.strip() for ln in t.split('\n')]
    n = len(want)
    for i in range(len(have) - n + 1):
        if have[i:i + n] == want:
            return i + 1
    return 0


def indent_unit(t: str, path: str = '') -> str:
    counts: dict = {}
    prev = 0
    for ln in t.split('\n'):
        if not ln.strip():
            continue
        ind = indent_of(ln)
        if ind.startswith('\t'):
            return '\t'
        d = len(ind) - prev
        if d > 0:
            counts[d] = counts.get(d, 0) + 1
        prev = len(ind)
    if counts:
        return ' ' * max(counts, key=lambda k: (counts[k], -k))
    return '    ' if path.endswith(('.py', '.pyi')) else '  '


def reindent(text: str, indent: str) -> str:
    text = re.sub(r'\A(?:[ \t]*\n)+', '', text.replace('\r\n', '\n')).rstrip()
    text = textwrap.dedent(text)
    return ''.join((indent + ln if ln.strip() else '') + '\n' for ln in text.split('\n'))
