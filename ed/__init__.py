"""ed: safe, short, multi-file text edits for coding agents.  See SPEC.md."""
from .errors import EdError
from .ops import checkpoint, ensure, expect, mv, rm, sh, undo
from .select import E, Selection, ln, rx, swap, w, ws

__all__ = ['E', 'rx', 'w', 'ws', 'ln', 'sh', 'ensure', 'expect', 'swap', 'mv', 'rm', 'checkpoint',
           'undo', 'EdError']
__version__ = '0.1.0'

from .txn import _install_hooks as _hooks

_hooks()
