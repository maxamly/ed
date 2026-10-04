# ed: edit files with `edx`

```bash
edx <<'ED'
e = E('src/**/*.{ts,tsx}', '!src/gen/**').having('console.log(')
e.all['console.log('] = 'logger.debug('
e[:'import'] |= "import { logger } from '@/lib/logger'"
ensure('tsc --noEmit')
ED
```
`from ed import *` is implied. Edits apply in order and are written at the end; any error writes nothing (exit 1). Prints one diff. Encoding, BOM, CRLF and final newline are kept automatically. `--dry` previews; `edx undo` reverts.
**Files**: `E('a.ts', 'src/**/*.py', '!x/**')` (globs skip gitignored files; named files always work), `E(*sh('git diff --name-only'))`, `.having(t)` / `.lacking(t)`, `e('api.ts')` narrows.

**Select**: `e['t']` must match once per file (errors show the nearest lines).
`e.all['t']` 0+ · `e.opt['t']` 0–1 · `.nth(2)` (-1 = last)
`e['a':'b']` strictly between a and the next b (whole lines if the anchors sit on their own lines; markers stay;
if a has code after it on its line and the slice spans lines, say `.inner` or `.outer`)
`.outer` = from a up to b (a included, b excluded) · `e[:'a']` `e['a':]` `e[:]` · `e.top` `e.bottom`
`e.lines(40, 55)` · `e.region('n')` inside `// region:n … // endregion` (auto-created)
`rx(r'v(\d+)', 'i')` · `w('id')` whole word · `ws('a = 1')` any whitespace · `ln('.card {')` whole line only
`.line` whole lines · `.block` to matching `}` · `.paren` `.bracket` · `.tag` · `.indent` (indented body)
Whole function/element: `e['function f'].block`, `e['def f'].indent`, `e['<footer>'].tag`; not a slice to the next one.
`sel['x']` searches inside sel. Bracket counting is naive; if it fails use `e['start':'end'].outer`.

**Act** (a simple edit is one line, `E('a.ts')['old'] = 'new'`; the diff is the check, so skip `expect`/`ensure` for it)
`sel = 'new'` replace (rx: `r'\1'`; or a callable taking `re.Match`/str)
`sel |= code` adds line(s) below sel, re-indented to fit, unless the file already has them: use it for new lines
`del sel` deletes (whole lines cleanly; also `del e.lines(9, 14)`) · `sel += t` / `.before(t)` insert literal text · `.move(after=sel2)` · `swap(a, b)`
`.lines.sort()` `.uniq()` `.filter(fn)` `.map(fn)` · `.text` `.count` · `e.each(lambda path, text: new)`
`E('new.ts').create(s)` `mv(a, b)` `rm(p)` · after `.nth()` use `.put() .add_line() .delete()`

**Check**: `ensure('cmd {changed}')` runs after writing; failure restores all. `expect(sel, n)`.

Search and read with `rg`, `grep` and `cat`; edx is for edits. `edx diff` shows the full last diff.
