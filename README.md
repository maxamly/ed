# ed / edx

Safe, short, multi-file text edits for coding agents. A pure-Python library (stdlib only, Python 3.10+)
with an `edx` command on top. Agents already edit files with `python3 <<'PY'` heredocs. `ed` keeps that
habit and makes the safe version the short version:
- every anchor must match exactly once
- edits apply in memory and are written atomically at the end
- `ensure('tsc --noEmit')` rolls everything back if it fails
- reruns of `|=` and regions are no-ops
- encoding, BOM, CRLF, trailing newlines and file modes survive
- the output is one diff

```bash
edx <<'ED'
e = E('src/**/*.{ts,tsx}', '!src/workers/**').having('console.log(')
e.all['console.log('] = 'logger.debug('
e[:'import'] |= "import { logger } from '@/lib/logger'"
ensure('tsc --noEmit')
ED
```

The plain-Python version with the same guarantees is about 35 lines.

## Install and run

```bash
uvx --from edx-cli edx --help
```

The PyPI names `ed` and `edx` are both taken, so the distribution is `edx-cli` (not published yet) and the
command is `edx`. It never shadows `/bin/ed`. From a checkout:

```bash
uv run edx --help
```

Library use keeps working: `python3 - <<'PY'` with `from ed import *` commits at exit.

## Command line

| | |
| --- | --- |
| `edx 'SCRIPT'`, `edx <<'ED' … ED`, `edx -f FILE` | run a script (`from ed import *` implied) |
| `--dry` | print the diff, write nothing |
| `--json` | machine-readable result (also `ED_JSON=1`) |
| `--max-diff N` | diff budget in lines (default 200) |
| `--strict` | reject `import`, `open`, `exec`, `eval`, `os`, `subprocess`, dunder access; a guard against accidents, not a sandbox |
| `edx diff` | full diff of the last run |
| `edx undo [N\|CHECKPOINT]` | revert runs from the `.ed/` journal |
| `edx spec` | print the cheat sheet |

`diff` and `spec` never write, so a harness can allowlist them. `edx` doesn't search or read files: use
`rg`, `grep` and `cat` for that.

## The API

[ed/SPEC.md](ed/SPEC.md) is the ~770-token cheat sheet meant for an agent's system prompt (`edx --help`
prints it). It covers every selector, action and helper.

Behaviour worth knowing:

- **Which files a glob sees:** in a git repo, what git sees (tracked and untracked files, minus
  `.gitignore`), like `rg`. Outside git, everything except `.git` and `.ed`. No folder is skipped
  for its name. Dotfiles match only when the pattern names them (`.env*`), and a file named
  explicitly (`E('.env')`) is always used, even if it's gitignored.
- **Selections are lazy.** Every action re-resolves its selector against the current in-memory text, so
  each edit sees the previous one. `e.having(...)` and globs are evaluated once, when called.
- **Uniqueness is per range.** `e['x']` must match once in every file. Inside a selection (`sel['x']`) it
  must match once in every range of that selection.
- **`e['a':'b']`** is the text strictly between `a` (which must be unique) and the first `b` after it.
  When an anchor sits on its own line, the slice is the whole lines in between, so assigning to it
  rewrites a marker block and keeps the markers. `.outer` runs from `a` (included) up to `b`
  (excluded), for cutting a function up to the next one.
- **`del`** removes whole lines when the range covers a line's only content, and collapses the blank-line
  pair a deleted block leaves behind. `sel = ''` is always exact.
- **`|=`** adds lines below the selection, re-indented to fit: after an opener (`{`, `:`) they go
  inside, and in an empty marker slice they match the closing marker. It skips files that already
  have those lines (ignoring indentation) and prints a note saying where. `+=`, `.before()` and
  `.after()` insert literal text.
- **The diff header names preserved traits** (`# a.ts: CRLF, BOM, no final newline kept`).
- **Stale reads:** if a file changed on disk after `ed` read it, the commit refuses and writes nothing.
- **Bracket counting is naive** (it ignores strings and comments) by design. When it miscounts, use
  explicit anchors.

## Development

```bash
uv run --with pytest pytest
```

`tests/fixture/` is a small TS/CSS/env repo with CRLF and BOM files, plus a stand-in `bin/tsc` that catches
missing imports, unresolved exports and unbalanced brackets. `tests/test_examples.py` runs the worked
examples in `tests/examples/` against it.
