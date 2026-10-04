from ed import *
from pathlib import Path
pages = sorted(p.stem for p in Path('src/pages').glob('*.tsx'))
r = E('src/router.tsx')
r['// imports:start':'// imports:end'] = ''.join(
    f"import {n} from './pages/{n}'\n" for n in pages)
r['// routes:start':'// routes:end'] = ''.join(
    f"  {{ path: '/{n.lower()}', element: <{n} /> }},\n" for n in pages)
