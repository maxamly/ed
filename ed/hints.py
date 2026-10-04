"""Error hints: where a position is, and which lines look most like an anchor that was not found."""
from __future__ import annotations

import difflib
import heapq

from .files import line_no


def loc(buf, pos: int) -> str:
    """'path:line| text' for a position, used in error messages."""
    n = line_no(buf.text, pos)
    line = buf.text.splitlines()[n - 1] if buf.text else ''
    return f'{buf.path}:{n}| {line.strip()[:100]}'


def _squash(s: str) -> str:
    return ' '.join(s.split())


def near(buf, needle: str, start: int = 0, end: int | None = None, k: int = 3, shortlist: int = 120):
    """Up to k (score, lineno, text) windows of buf most similar to needle, whitespace-insensitive.

    A cheap character-pair (bigram) similarity picks a shortlist; only those get the slower, precise
    SequenceMatcher score. This keeps error messages fast on large files."""
    text = buf.text
    end = len(text) if end is None else end
    first = line_no(text, start)
    lines = text[start:end].split('\n')
    n = max(1, needle.strip('\n').count('\n') + 1)
    target = _squash(needle).lower()
    if not target:
        return []
    tb = set(zip(target, target[1:])) or {(target, '')}
    rough = []
    for i in range(len(lines)):
        cand = _squash('\n'.join(lines[i:i + n]) if n > 1 else lines[i]).lower()
        if cand:
            cb = set(zip(cand, cand[1:])) or {(cand, '')}
            common = len(tb & cb)
            if common:
                rough.append((2 * common / (len(tb) + len(cb)), -i, cand))
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(target)
    scored = []
    for _, neg_i, cand in heapq.nlargest(shortlist, rough):
        sm.set_seq1(cand)
        r = sm.ratio()
        if target in cand or cand in target:
            r = max(r, 0.6 + 0.4 * min(len(cand), len(target)) / max(len(cand), len(target)))
        if r >= 0.45:
            scored.append((r, first - neg_i, lines[-neg_i]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:k]


def near_lines(buf, needle, start=0, end=None, k=3) -> list[str]:
    return [f'    {buf.path}:{n}| {t.strip()[:100]}' for _, n, t in near(buf, needle, start, end, k)]
