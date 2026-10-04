"""File sets (E), patterns (rx, w, ws) and lazily resolved selections."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import actions as A
from . import txn
from .errors import EdError
from .files import comment_style, expand_braces, glob_regex, indent_of, is_glob, line_end, line_no, line_start
from .hints import loc, near_lines

# ---------------------------------------------------------------- patterns


class Rx:
    """A regex pattern. `template` = replacement strings may use \\1 / \\g<name>."""

    def __init__(self, regex, template=True, desc=None, group=0):
        self.regex = regex
        self.group = group  # which group's span is the selected range
        self.template = template
        self.desc = desc or f'rx({regex.pattern!r})'

    def __repr__(self):
        return self.desc


_FLAGS = {'i': re.I, 's': re.S, 'x': re.X, 'a': re.A, 'm': re.M}


def rx(pattern, flags=0) -> Rx:
    """Regex selector. Multiline (^/$ per line) by default; flags may be an int or letters like 'is'."""
    if isinstance(pattern, Rx):
        return pattern
    if isinstance(flags, str):
        bad = set(flags) - set(_FLAGS)
        if bad:
            raise EdError(f'rx(): unknown flag(s) {"".join(sorted(bad))!r}; use letters from "imsxa"')
        flags = sum(_FLAGS[c] for c in flags)
    if isinstance(pattern, re.Pattern):
        return Rx(pattern)
    return Rx(re.compile(pattern, flags | re.M))


def w(word: str) -> Rx:
    """Whole-word literal match."""
    return Rx(re.compile(r'(?<!\w)' + re.escape(word) + r'(?!\w)'), False, f'w({word!r})')


def ln(text: str) -> Rx:
    """A whole line: matches only a line whose text (ignoring indentation) is exactly `text`."""
    lines = [re.escape(x.strip()) for x in text.strip('\n').split('\n')]
    body = r'[ \t\r]*\n[ \t]*'.join(lines)
    return Rx(re.compile(r'^[ \t]*(' + body + r')[ \t\r]*$', re.M), False, f'ln({text!r})', group=1)


def ws(text: str) -> Rx:
    """Whitespace-insensitive literal match."""
    tokens = re.findall(r'\s+|\w+|[^\w\s]', text.strip())
    out, prev_word, gap = '', False, False
    for tok in tokens:
        if tok.isspace():
            gap = True
            continue
        word = bool(re.match(r'\w', tok))
        if out:
            out += r'\s+' if (gap and word and prev_word) else r'\s*'
        out += re.escape(tok)
        prev_word, gap = word, False
    return Rx(re.compile(out), False, f'ws({text!r})')


def _desc(pat) -> str:
    return repr(pat) if isinstance(pat, str) else getattr(pat, 'desc', repr(pat))


def _norm_needle(buf, s: str) -> str:
    return s if buf.newline is None else s.replace('\r\n', '\n')


def find_all(buf, pat, start: int, end: int):
    """Non-overlapping matches of pat in buf.text[start:end] as (s, e, re.Match | None)."""
    t = buf.text
    if isinstance(pat, Rx):
        return [(*m.span(pat.group), m) for m in pat.regex.finditer(t, start, end)]
    if not isinstance(pat, str):
        raise TypeError(f'selector must be str, rx(), w() or ws(), not {type(pat).__name__}')
    if pat == '':
        raise EdError("empty anchor ''")
    needle = _norm_needle(buf, pat)
    out, i = [], t.find(needle, start, end)
    while i != -1:
        out.append((i, i + len(needle), None))
        i = t.find(needle, i + len(needle), end)
    return out


def _is_template(pat) -> bool:
    return isinstance(pat, Rx) and pat.template


# ---------------------------------------------------------------- ranges and steps


@dataclass(slots=True, eq=False)
class R:
    """One selected range of a file. inner/outer bounds feed .inner and .outer (default: the range)."""
    buf: object
    start: int
    end: int
    istart: int | None = None
    iend: int | None = None
    m: re.Match | None = None    # the regex match, for \\1 templates and callables
    tmpl: bool = False           # replacement strings are re templates
    region: bool = False         # a region body: replacements end with a newline
    ostart: int | None = None
    oend: int | None = None

    def __post_init__(self):
        self.istart = self.start if self.istart is None else self.istart
        self.iend = self.end if self.iend is None else self.iend
        self.ostart = self.start if self.ostart is None else self.ostart
        self.oend = self.end if self.oend is None else self.oend

    @property
    def text(self):
        return self.buf.text[self.start:self.end]

    def key(self):
        return (id(self.buf), self.start, self.end)


def _where(p: R) -> str:
    if p.start == 0 and p.end == len(p.buf.text):
        return p.buf.path
    a, b = line_no(p.buf.text, p.start), line_no(p.buf.text, max(p.start, p.end - 1))
    return f'{p.buf.path} (inside the selection at lines {a}-{b})'


def _not_found(pat, p: R, what='anchor'):
    msg = f'{what} {_desc(pat)} not found in {_where(p)}'
    needle = pat if isinstance(pat, str) else getattr(pat, 'regex', None) and pat.regex.pattern
    if isinstance(pat, Rx) and pat.desc.startswith(('w(', 'ws(', 'ln(')):
        needle = pat.desc.split('(', 1)[1][1:-2]
    sugg = near_lines(p.buf, needle or '', p.start, p.end) if needle else []
    if sugg:
        msg += '\n  closest:\n' + '\n'.join(sugg)
        msg += "\n  hint: copy the text exactly, or use ws('...') to ignore whitespace"
    return EdError(msg)


def _ambiguous(pat, p: R, ms):
    shown = '\n'.join('    ' + loc(p.buf, s) for s, _, _ in ms[:5])
    more = f'\n    ... and {len(ms) - 5} more' if len(ms) > 5 else ''
    hint = 'use a longer anchor, .nth(k), .all[...] or .opt[...]'
    if isinstance(pat, str) and '\n' not in pat.strip():
        t = p.buf.text
        whole = [s for s, _, _ in ms if t[line_start(t, s):line_end(t, s)].strip() == pat.strip()]
        if len(whole) == 1:
            hint = f'only line {line_no(t, whole[0])} is exactly this text: use ln({pat.strip()!r})'
    return EdError(f'{_desc(pat)} matches {len(ms)} times in {_where(p)}, expected exactly 1:\n'
                   f'{shown}{more}\n  hint: {hint}')


class Step:
    """One link in a selection chain (e['a'].block.inner is [Find, Bracket, Inner]): parent ranges in,
    child ranges out. `picks` marks anchor steps whose match count .nth() can re-target."""
    picks = False

    def run(self, parents, lenient=False, write=False):
        raise NotImplementedError


def _pick(ms, mode, nth, pat, p, lenient):
    if mode == 'nth':
        i = nth - 1 if nth > 0 else len(ms) + nth
        if not 0 <= i < len(ms):
            raise EdError(f'.nth({nth}): {_desc(pat)} matches only {len(ms)} time{"s" * (len(ms) != 1)} in {_where(p)}')
        return [ms[i]]
    if mode == 'all' or lenient:
        return ms
    if not ms:
        if mode == 'opt':
            return []
        raise _not_found(pat, p)
    if len(ms) > 1:
        raise _ambiguous(pat, p, ms)
    return ms


class Find(Step):
    picks = True

    def __init__(self, pat, mode='one', nth=None):
        self.pat, self.mode, self.nth = pat, mode, nth

    def renth(self, n):
        return Find(self.pat, 'nth', n)

    def run(self, parents, lenient=False, write=False):
        out = []
        for p in parents:
            ms = _pick(find_all(p.buf, self.pat, p.start, p.end), self.mode, self.nth, self.pat, p, lenient)
            tm = _is_template(self.pat)
            out.extend(R(p.buf, s, e, m=m, tmpl=tm) for s, e, m in ms)
        return out


def _after_anchor(t: str, end: int) -> int:
    """Where 'between' starts: the next line if the start anchor ends its line, else right after it."""
    if end > 0 and t[end - 1] == '\n':
        return end
    le = line_end(t, end)
    if t[end:le].strip() == '':
        return le + 1 if le < len(t) else le
    return end


def _before_anchor(t: str, start: int, floor: int) -> int:
    """Where 'between' ends: the start of the end anchor's line if it begins that line, else right before it."""
    ls = line_start(t, start)
    return ls if ls >= floor and t[ls:start].strip() == '' else start


class Slice(Step):
    """e['a':'b'] = the text strictly between the anchors (whole lines when they sit on their own lines).
    .outer = from a (included) up to b (excluded), e.g. a function up to the next one."""
    picks = True

    def __init__(self, a, b, mode='one', nth=None):
        self.a, self.b, self.mode, self.nth = a, b, mode, nth

    def renth(self, n):
        return Slice(self.a, self.b, 'nth', n)

    def run(self, parents, lenient=False, write=False, bare=False):
        out = []
        for p in parents:
            t = p.buf.text
            if self.a is None:
                if self.b is None:
                    out.append(R(p.buf, p.start, p.end))
                    continue
                bm = find_all(p.buf, self.b, p.start, p.end)
                if not bm:
                    if self.mode == 'opt' or (lenient and self.mode != 'nth'):
                        continue
                    raise _not_found(self.b, p, 'end anchor')
                bs = bm[0][0]
                end = _before_anchor(t, bs, p.start)
                out.append(R(p.buf, p.start, end, ostart=p.start, oend=end))
                continue
            starts = find_all(p.buf, self.a, p.start, p.end)
            pairs, nxt = [], p.start
            for s, e, _ in starts:
                if s < nxt:
                    continue
                if self.b is None:
                    pairs.append((s, e, p.end, p.end))
                    nxt = p.end
                    continue
                bm = find_all(p.buf, self.b, e, p.end)
                if not bm:
                    if self.mode in ('one', 'nth') and not lenient:
                        raise _not_found(self.b, R(p.buf, e, p.end), f'end anchor (after {_desc(self.a)})')
                    break
                pairs.append((s, e, bm[0][0], bm[0][1]))
                nxt = bm[0][0]
            ms = _pick([(s, e, (bs, be)) for s, e, bs, be in pairs], self.mode, self.nth, self.a, p, lenient)
            for s, e, (bs, be) in ms:
                if bare and not lenient:
                    self._check_unambiguous(p.buf, s, e, bs)
                start = _after_anchor(t, e)
                end = _before_anchor(t, bs, start) if self.b is not None else p.end
                start = min(start, end)
                oend = _before_anchor(t, bs, s) if self.b is not None else p.end
                out.append(R(p.buf, start, end, ostart=s, oend=oend))
        return out


    def _check_unambiguous(self, buf, s, e, bs):
        """Fail closed when 'between' would cut the start anchor's line in half across several lines."""
        t = buf.text
        mid_line = not (t[e - 1] == '\n' or t[e:line_end(t, e)].strip() == '')
        if mid_line and '\n' in t[e:bs].rstrip():
            b = '' if self.b is None else _desc(self.b)
            raise EdError(f'e[{_desc(self.a)}:{b}] spans lines and {_desc(self.a)} has code after it on its '
                          f'line ({loc(buf, s)}), so "between" would cut that line in half.\n'
                          f'  say .outer (from a up to b, e.g. a whole function) or .inner (strictly between)')


class Outer(Step):
    def run(self, parents, lenient=False, write=False):
        return [R(p.buf, p.ostart, p.oend) for p in parents]


class Edge(Step):
    def __init__(self, bottom):
        self.bottom = bottom

    def run(self, parents, lenient=False, write=False):
        return [R(p.buf, p.end, p.end) if self.bottom else R(p.buf, p.start, p.start) for p in parents]


class Inner(Step):
    def run(self, parents, lenient=False, write=False):
        return [R(p.buf, p.istart, p.iend) for p in parents]


class Line(Step):
    def run(self, parents, lenient=False, write=False):
        out = []
        for p in parents:
            t = p.buf.text
            s = line_start(t, p.start)
            last = p.end - 1 if p.end > p.start else p.start
            le = line_end(t, max(last, s))
            e = le + 1 if le < len(t) else le
            out.append(R(p.buf, s, e, s, le))
        return out


_PAIRS = {'block': ('{', '}'), 'paren': ('(', ')'), 'bracket': ('[', ']')}


def _bracket_err(kind, p, what):
    o, c = _PAIRS[kind]
    return EdError(f'.{kind}: {what} for {loc(p.buf, p.start)}\n'
                   f"  note: bracket counting is naive (ignores strings/comments); "
                   f"use explicit anchors e['start':'end'].outer instead")


class Bracket(Step):
    def __init__(self, kind):
        self.kind = kind

    def run(self, parents, lenient=False, write=False):
        o, c = _PAIRS[self.kind]
        out = []
        for p in parents:
            t = p.buf.text
            i = self._opener(t, p, o, c)
            depth, j = 0, i
            while j < len(t):
                ch = t[j]
                if ch == o:
                    depth += 1
                elif ch == c:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            else:
                raise _bracket_err(self.kind, p, f"unbalanced '{o}' (no matching '{c}')")
            out.append(R(p.buf, p.start, j + 1, i + 1, j))
        return out

    def _opener(self, t, p, o, c):
        if self.kind != 'block':
            stop = line_end(t, max(p.start, p.end - 1))
            i = t.find(o, p.start, stop)
            if i == -1:
                raise _bracket_err(self.kind, p, f"no '{o}' on the anchor line")
            return i
        depth = 0
        for i in range(p.start, len(t)):
            ch = t[i]
            if ch in '([':
                depth += 1
            elif ch in ')]':
                depth -= 1
            elif depth <= 0 and ch == '{':
                return i
            elif depth <= 0 and ch in ';}':
                break
        raise _bracket_err(self.kind, p, "no '{' after the anchor (use .indent for indentation blocks)")


_TAG_NAME = re.compile(r'<([A-Za-z][\w.:-]*)')


def _scan_open_tag(t, i):
    """From just after '<Name', return (index after '>', self_closing)."""
    depth, quote = 0, None
    while i < len(t):
        ch = t[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        elif depth == 0 and ch in '"\'':
            quote = ch
        elif depth == 0 and ch == '>':
            return i + 1, t[i - 1] == '/'
        i += 1
    return -1, False


class Tag(Step):
    def run(self, parents, lenient=False, write=False):
        out = []
        for p in parents:
            t = p.buf.text
            i = t.find('<', p.start, line_end(t, p.start))
            m = _TAG_NAME.match(t, i) if i != -1 else None
            if not m:
                raise EdError(f'.tag: no <Tag at {loc(p.buf, p.start)}')
            name = m.group(1)
            end_open, sc = _scan_open_tag(t, m.end())
            if end_open == -1:
                raise EdError(f'.tag: unterminated <{name} at {loc(p.buf, i)}')
            if sc:
                out.append(R(p.buf, p.start, end_open, end_open, end_open))
                continue
            tag_rx = re.compile(r'<(/?)' + re.escape(name) + r'(?=[\s/>])')
            depth, pos = 1, end_open
            while True:
                m2 = tag_rx.search(t, pos)
                if not m2:
                    raise EdError(f'.tag: no closing </{name}> for {loc(p.buf, i)}\n'
                                  "  note: tag counting is naive; use explicit anchors e['<A':'</A>'].outer instead")
                if m2.group(1):
                    depth -= 1
                    gt = t.find('>', m2.end())
                    pos = gt + 1
                    if depth == 0:
                        out.append(R(p.buf, p.start, pos, end_open, m2.start()))
                        break
                else:
                    e2, sc2 = _scan_open_tag(t, m2.end())
                    if e2 == -1:
                        raise EdError(f'.tag: unterminated <{name} at {loc(p.buf, m2.start())}')
                    depth += 0 if sc2 else 1
                    pos = e2
        return out


class Indent(Step):
    def run(self, parents, lenient=False, write=False):
        out = []
        for p in parents:
            t = p.buf.text
            le = line_end(t, p.start)
            base = len(indent_of(t[line_start(t, p.start):le]).expandtabs(4))
            last, pos = le, le
            while pos < len(t):
                s = pos + 1
                e = line_end(t, s)
                ln = t[s:e]
                if ln.strip() and len(indent_of(ln).expandtabs(4)) <= base:
                    break
                if ln.strip():
                    last = e
                pos = e
                if e >= len(t):
                    break
            inner_s = le + 1 if last > le else le
            out.append(R(p.buf, p.start, last, inner_s, last))
        return out


class Lines(Step):
    def __init__(self, a, b):
        if a < 1 or b < a:
            raise EdError(f'lines({a}, {b}): line numbers are 1-based and inclusive')
        self.a, self.b = a, b

    def run(self, parents, lenient=False, write=False):
        out = []
        for p in parents:
            t = p.buf.text
            pos, n = line_start(t, p.start), 1
            s = None
            while True:
                if n == self.a:
                    s = pos
                le = line_end(t, pos)
                if n == self.b:
                    e = le + 1 if le < len(t) else le
                    out.append(R(p.buf, s, e, s, le))
                    break
                if le >= len(t) or le + 1 > p.end or (le + 1 == len(t) and t.endswith('\n')):
                    raise EdError(f'lines({self.a}, {self.b}): {_where(p)} has only {n} lines')
                pos, n = le + 1, n + 1
        return out


_MARK = r'^[ \t]*(?://|#|/\*|<!--|--|;;)[ \t]*'


class Region(Step):
    def __init__(self, name):
        self.name = name

    def run(self, parents, lenient=False, write=False):
        out = []
        start_rx = re.compile(_MARK + r'region:' + re.escape(self.name) + r'(?![\w-])[^\n]*', re.M)
        end_rx = re.compile(_MARK + r'endregion\b[^\n]*', re.M)
        for p in parents:
            buf, t = p.buf, p.buf.text
            ms = list(start_rx.finditer(t, p.start, p.end))
            if len(ms) > 1:
                raise _ambiguous(f'region:{self.name}', p, [(m.start(), 0, 0) for m in ms])
            if not ms:
                if not write:
                    continue
                o, c = comment_style(buf.path)
                pre = '' if not t else ('\n' if t.endswith('\n') else '\n\n')
                if t.endswith('\n') and not t.endswith('\n\n'):
                    pre = '\n'
                block = f'{pre}{o} region:{self.name}{c}\n{o} endregion{c}\n'
                buf.text = t + block
                t = buf.text
                ms = [start_rx.search(t, len(t) - len(block))]
            m = ms[0]
            em = end_rx.search(t, m.end())
            if not em:
                raise EdError(f'region:{self.name} in {buf.path} has no endregion marker')
            s = m.end() + 1 if m.end() < len(t) else m.end()
            e = em.start()
            out.append(R(buf, s, e, s, e, region=True))
        return out


# ---------------------------------------------------------------- selections


def _step_for(key, mode='one'):
    if isinstance(key, slice):
        if key.step is not None:
            raise EdError('slices take two anchors: e["a":"b"]')
        return Slice(key.start, key.stop, mode)
    if isinstance(key, int):
        raise EdError(f'e[{key}]: integer index not supported; use .lines({key}, {key}) or .nth({key})')
    return Find(key, mode)


def _done(v) -> bool:
    return isinstance(v, Selection) and v._done


class _Items:
    """`x[key] = value` replaces and `del x[key]` deletes, for anything whose x[key] is a selection."""

    def __setitem__(self, key, value):
        if not _done(value):  # `x[k] += t` hands back the already-applied selection
            self[key].put(value)

    def __delitem__(self, key):
        self[key].delete()


class Mode(_Items):
    """`.all[...]` / `.opt[...]`: same selectors with a different match-count rule."""

    def __init__(self, sel, mode):
        self._sel, self._mode = sel, mode

    def __getitem__(self, key):
        return self._sel._with(_step_for(key, self._mode))


class LinesView:
    """`sel.lines(a, b)` selects relative lines; `sel.lines.sort()` etc. rewrite lines in place."""

    def __init__(self, sel):
        self._sel = sel

    def __call__(self, a, b=None):
        return self._sel._with(Lines(a, a if b is None else b))

    def __iter__(self):
        for r in self._sel._resolve():
            _, lines, _, _ = _split(r)
            yield from lines

    def _op(self, fn):
        edits = []
        for r in self._sel._resolve():
            lead, lines, nl, tail = _split(r)
            new = fn(list(lines))
            body = '\n'.join(new) + ('\n' if nl and new else '')
            edits.append((r.buf, r.start, r.end, lead + body + tail))
        return self._sel._apply(edits)

    def sort(self, key=None, reverse=False):
        return self._op(lambda ls: sorted(ls, key=key, reverse=reverse))

    def uniq(self):
        return self._op(lambda ls: list(dict.fromkeys(ls)))

    def filter(self, fn):
        return self._op(lambda ls: [ln for ln in ls if fn(ln)])

    def map(self, fn):
        return self._op(lambda ls: [fn(ln) for ln in ls])


def _split(r: R):
    """Split a range into (partial lead, complete lines, body ends with newline, partial tail)."""
    t, s, e = r.buf.text, r.start, r.end
    seg = t[s:e]
    lead = ''
    if s > 0 and t[s - 1] != '\n':
        i = seg.find('\n')
        if i == -1:
            return seg, [], False, ''
        lead, seg = seg[:i + 1], seg[i + 1:]
    tail = ''
    j = seg.rfind('\n')
    last = seg[j + 1:]
    if last and not (e == len(t) or t[e] == '\n'):
        tail, seg = last, seg[:j + 1]
    nl = seg.endswith('\n')
    lines = seg[:-1].split('\n') if nl else (seg.split('\n') if seg else [])
    return lead, lines, nl, tail


def _transform(step_cls, *args):
    def get(self):
        return self._with(step_cls(*args))

    def set_(self, value):
        if not _done(value):
            get(self).put(value)

    def del_(self):
        get(self).delete()

    return property(get, set_, del_)


class Selection(_Items):
    """A lazy description of ranges: re-resolved against current text by every action."""

    def __init__(self, src, steps=()):
        object.__setattr__(self, '_src', src)
        object.__setattr__(self, '_steps', tuple(steps))
        object.__setattr__(self, '_done', False)

    def __setattr__(self, name, value):
        if name.startswith('_') or isinstance(getattr(type(self), name, None), property):
            return object.__setattr__(self, name, value)
        raise AttributeError(f'selections have no attribute {name!r} to assign')

    # ---- building

    def _with(self, step):
        return Selection(self._src, self._steps + (step,))

    def _tx(self):
        return txn.current()

    def __getitem__(self, key):
        return self._with(_step_for(key))

    @property
    def all(self):
        return Mode(self, 'all')

    @property
    def opt(self):
        return Mode(self, 'opt')

    def nth(self, n: int):
        """Only the n-th (1-based; negative from the end) match of the last anchor."""
        if not n:
            raise EdError('.nth() is 1-based: use .nth(1) for the first match, .nth(-1) for the last')
        for i in range(len(self._steps) - 1, -1, -1):
            if self._steps[i].picks:
                steps = list(self._steps)
                steps[i] = steps[i].renth(n)
                return Selection(self._src, steps)
        raise EdError('.nth() needs an anchor before it')

    inner = _transform(Inner)
    outer = _transform(Outer)
    line = _transform(Line)
    block = _transform(Bracket, 'block')
    paren = _transform(Bracket, 'paren')
    bracket = _transform(Bracket, 'bracket')
    tag = _transform(Tag)
    indent = _transform(Indent)

    @property
    def lines(self):
        return LinesView(self)

    def region(self, name: str):
        """Text between '<comment> region:name' and '<comment> endregion'; created at the bottom on write."""
        return self._with(Region(name))

    # ---- resolving

    def _resolve(self, lenient=False, write=False, only=None):
        ranges = self._src._roots(only)
        last_pick = max((i for i, s in enumerate(self._steps) if s.picks), default=-1)
        for i, step in enumerate(self._steps):
            kw = {}
            if isinstance(step, Slice):
                nxt = self._steps[i + 1] if i + 1 < len(self._steps) else None
                kw['bare'] = not isinstance(nxt, (Inner, Outer))
            ranges = step.run(ranges, lenient=lenient and i == last_pick, write=write, **kw)
        seen, out = set(), []
        for r in ranges:
            if r.key() not in seen:
                seen.add(r.key())
                out.append(r)
        return out

    @property
    def text(self) -> str:
        rs = self._resolve()
        if len(rs) == 1:
            return rs[0].text
        if not rs and self._steps and isinstance(self._steps[-1], Region):
            return ''
        raise EdError(f'.text needs exactly one range, got {len(rs)}; use .texts for a list')

    @text.setter
    def text(self, value):
        if not _done(value):
            self.put(value)

    @property
    def texts(self) -> list:
        return [r.text for r in self._resolve()]

    @property
    def count(self) -> int:
        return len(self._resolve(lenient=True))

    def __len__(self):
        return self.count

    def __bool__(self):
        return self.count > 0

    def __str__(self):
        return self.text

    def __repr__(self):
        try:
            rs = self._resolve(lenient=True)
            files = sorted({r.buf.path for r in rs})
            return f'<selection: {len(rs)} range(s) in {", ".join(files) or "no files"}>'
        except Exception as ex:  # noqa: BLE001 - repr must not raise
            return f'<selection: {ex}>'

    # ---- actions

    def _finish(self):
        object.__setattr__(self, '_done', True)
        return self

    def _apply(self, edits):
        A.apply(edits)
        self._tx().acted()
        return self._finish()

    def _value(self, v, r: R) -> str:
        if isinstance(v, Selection):
            v = v.text
        if callable(v):
            v = v(r.m if (r.tmpl and r.m is not None) else r.text)
            if v is None:
                return r.text
        if not isinstance(v, str):
            v = str(v)
        if r.tmpl and r.m is not None:
            v = re.sub(r'[\x01-\x08]', lambda c: '\\g<%d>' % ord(c.group()), v)
            v = r.m.expand(v)
        return v

    def put(self, value):
        """Replace every range with value (str, \\1 template for rx, or callable)."""
        edits = []
        for r in self._resolve(write=True):
            v = self._value(value, r)
            if r.region and v and not v.endswith('\n'):
                v += '\n'
            edits.append((r.buf, r.start, r.end, v))
        return self._apply(edits)

    def after(self, text):
        return self._apply([(r.buf, r.end, r.end, self._value(text, r)) for r in self._resolve(write=True)])

    def before(self, text):
        return self._apply([(r.buf, r.start, r.start, self._value(text, r)) for r in self._resolve(write=True)])

    def __iadd__(self, text):
        return self.after(text)

    def add_line(self, text: str):
        """Insert lines below the selection, re-indented to fit, unless the file already has them."""
        tx = self._tx()
        block = A.reindent(str(text), '')
        need = []
        for b in self._src._bufs():
            at = A.find_lines(b.text, block)
            if at:
                tx.note(f'|= skipped {b.path}: already has it at line {at}')
            else:
                need.append(b)
        edits, done = [], set()
        for r in (self._resolve(write=True, only=need) if need else []):
            if id(r.buf) in done:
                continue
            done.add(id(r.buf))
            t = r.buf.text
            pos, ind = _below(t, r)
            edits.append((r.buf, pos, pos, A.as_line(t, pos, A.reindent(block, ind))))
        return self._apply(edits)

    def __ior__(self, line):
        return self.add_line(line)

    def delete(self):
        """Delete every range; whole-line ranges take their line break and a doubled blank line."""
        edits = []
        for r in self._resolve(write=True):
            s, e = (r.start, r.end) if r.region else A.tidy_delete(r.buf.text, r.start, r.end)
            edits.append((r.buf, s, e, ''))
        return self._apply(edits)

    def indented(self, text: str, before: bool = False):
        """Insert text as lines below (or above) the selection, re-indented; no-op if already there."""
        edits = []
        for r in self._resolve(write=True):
            t = r.buf.text
            first = t[line_start(t, r.start):line_end(t, r.start)]
            if before:
                pos, ind = line_start(t, r.start), indent_of(first)
            else:
                pos, ind = _below(t, r)
            chunk = A.as_line(t, pos, A.reindent(self._value(text, r), ind))
            if (t[pos - len(chunk):pos] if before else t[pos:pos + len(chunk)]) == chunk:
                continue  # already there: reruns stay idempotent
            edits.append((r.buf, pos, pos, chunk))
        return self._apply(edits)

    def move(self, after=None, before=None):
        """Move the selected text next to another selection (same or another file)."""
        if (after is None) == (before is None):
            raise EdError('.move() needs exactly one of after= or before=')
        target = after if after is not None else before
        src = self._resolve(write=True)
        if not src:
            raise EdError('.move(): nothing selected')
        dst = target._resolve(write=True)
        if len(dst) != 1:
            raise EdError(f'.move(): target must be exactly one range, got {len(dst)}')
        d = dst[0]
        cuts, chunks = [], []
        for r in src:
            s, e = A.tidy_delete(r.buf.text, r.start, r.end)
            if r.buf is d.buf and s < d.end and d.start < e:
                raise EdError('.move(): target is inside the moved text')
            cuts.append((r.buf, s, e, ''))
            cs, ce = A.tidy_delete(r.buf.text, r.start, r.end, collapse=False)
            whole = A.is_whole_lines(r.buf.text, cs, ce)
            chunks.append((r.buf.text[cs:ce] if whole else r.text, whole))
        t = d.buf.text
        whole = all(wh for _, wh in chunks)
        text = ''.join(c if c.endswith('\n') or not whole else c + '\n' for c, _ in chunks)
        if whole:
            pos = A.line_pos(t, d.end if after is not None else d.start, after=after is not None)
            text = A.as_line(t, pos, text)
        else:
            pos = d.end if after is not None else d.start
        return self._apply(cuts + [(d.buf, pos, pos, text)])


def _below(t: str, r: R):
    """Insertion point and indentation for new lines that go below a selection."""
    if r.start == r.end and (r.end == len(t) or r.end == 0 or t[r.end - 1] == '\n'):
        if r.end == len(t):
            return r.end, ''
        nxt = next((ln for ln in t[r.end:].split('\n') if ln.strip()), '')
        return r.end, indent_of(nxt)  # empty selection on a line boundary: match what follows
    last_s = line_start(t, max(r.start, r.end - 1))
    last = t[last_s:line_end(t, last_s)]
    pos = A.line_pos(t, max(r.end, last_s + len(last)), after=True)
    ind = indent_of(last)
    if last.rstrip().endswith(('{', '(', '[', ':')):
        nxt = next((ln for ln in t[pos:].split('\n') if ln.strip()), '')
        inner = indent_of(nxt)
        ind = inner if len(inner) > len(ind) else ind + A.indent_unit(t, r.buf.path)
    return pos, ind


def swap(a: Selection, b: Selection):
    """Exchange the text of two single-range selections."""
    ra, rb = a._resolve(write=True), b._resolve(write=True)
    if len(ra) != 1 or len(rb) != 1:
        raise EdError(f'swap() needs one range on each side, got {len(ra)} and {len(rb)}')
    x, y = ra[0], rb[0]
    a._apply([(x.buf, x.start, x.end, y.text), (y.buf, y.start, y.end, x.text)])


# ---------------------------------------------------------------- file sets


def _flatten(items):
    for it in items:
        if isinstance(it, (str, os.PathLike)):
            yield os.fspath(it)
        else:
            yield from _flatten(it)


class E(_Items):
    """A set of files: E('src/**/*.{ts,tsx}', '!src/gen/**'). Index it to select text."""

    def __init__(self, *patterns):
        pats = [p for p in _flatten(patterns) if p]
        if not pats:
            raise EdError('E() needs at least one path or glob')
        self._desc = ', '.join(pats)
        self._paths = txn.current().expand(pats)
        self._globbed = any(is_glob(p) for p in pats if not p.startswith('!'))
        self._filtered = False

    @classmethod
    def _of(cls, paths, desc, filtered=True):
        e = cls.__new__(cls)
        e._paths, e._desc, e._globbed, e._filtered = list(paths), desc, True, filtered
        return e

    # ---- file set

    def _bufs(self, only=None):
        tx = txn.current()
        if not self._paths and not self._filtered:
            raise EdError(f'no files matched {self._desc}')
        bufs = [tx.get(p) for p in self._paths]
        bufs = [b for b in bufs if not b.binary or not self._globbed]
        for b in bufs:
            if b.binary:
                raise EdError(f'{b.path} looks binary; ed edits text files only')
        if only is not None:
            ids = {id(b) for b in only}
            bufs = [b for b in bufs if id(b) in ids]
        return bufs

    def paths_unloaded(self) -> list:
        """The matched paths without reading the files (for handing them to another tool)."""
        if not self._paths and not self._filtered:
            raise EdError(f'no files matched {self._desc}')
        return list(self._paths)

    def _roots(self, only=None):
        return [R(b, 0, len(b.text)) for b in self._bufs(only)]

    @property
    def paths(self) -> list:
        return [b.path for b in self._bufs()]

    @property
    def path(self) -> str:
        ps = self.paths
        if len(ps) != 1:
            raise EdError(f'.path needs exactly one file, got {len(ps)}')
        return ps[0]

    def __iter__(self):
        for b in self._bufs():
            yield E._of([b.path], b.path)

    def __len__(self):
        return len(self._paths)

    def __call__(self, pattern: str):
        rxs = [glob_regex(p) for p in expand_braces(pattern)]
        keep = [p for p in self._paths
                if p == pattern or p.endswith('/' + pattern) or any(r.match(p) for r in rxs)]
        if not keep:
            raise EdError(f'{pattern!r} matches none of the {len(self._paths)} files in E({self._desc})')
        return E._of(keep, pattern)

    def _filter(self, pat, want: bool):
        has = (lambda t: pat.regex.search(t) is not None) if isinstance(pat, Rx) else (lambda t: pat in t)
        return E._of([b.path for b in self._bufs() if has(b.text) == want], self._desc)

    def having(self, pat):
        """Only files whose current text contains pat (evaluated now)."""
        return self._filter(pat, True)

    def lacking(self, pat):
        return self._filter(pat, False)

    # ---- selection entry points

    @property
    def _sel(self):
        return Selection(self)

    def __getitem__(self, key):
        return self._sel[key]

    def __getattr__(self, name):
        # .all .opt .lines .region .text ... are those of the selection covering every whole file
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self._sel, name)

    def _edge(bottom):
        def get(self):
            return self._sel._with(Edge(bottom))

        def set_(self, value):
            if not _done(value):
                get(self).put(value)

        return property(get, set_)

    top = _edge(False)
    bottom = _edge(True)
    del _edge

    @property
    def count(self) -> int:
        return len(self._bufs())

    def each(self, fn):
        """Escape hatch: fn(path, text) -> new text (or None to leave the file alone)."""
        for b in self._bufs():
            new = fn(b.path, b.text)
            if new is not None:
                b.text = b.norm(new)
        txn.current().acted()
        return self

    def create(self, text: str = '', overwrite: bool = False):
        """Create the file(s) with text; part of the transaction."""
        tx = txn.current()
        for p in self._paths:
            tx.create(p, text, overwrite)
        tx.acted()
        return self

    # ---- explicit transaction scope

    def __enter__(self):
        txn.push()
        return self

    def __exit__(self, et, ev, tb):
        txn.pop(commit=et is None)
        return False

    def __repr__(self):
        return f'E({self._desc}: {len(self._paths)} files)'
