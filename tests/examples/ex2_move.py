from ed import *
src, dst = E('src/utils/date.ts'), E('src/lib/time.ts')
code = src['export function formatDate'].block.text
del src['export function formatDate'].block
dst.bottom += '\n' + code

def fix(m):
    rest = [n.strip() for n in (m[1] + m[2]).split(',') if n.strip()]
    keep = f"import {{ {', '.join(rest)} }} from '@/utils/date'\n" if rest else ''
    return keep + "import { formatDate } from '@/lib/time'"

E('src/**/*.{ts,tsx}').all[rx(
    r"import \{([^}]*)\bformatDate\b([^}]*)\} from '@/utils/date'")] = fix
ensure('tsc --noEmit')
