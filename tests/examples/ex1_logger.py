from ed import *
e = E('src/**/*.{ts,tsx}', '!src/workers/**').having('console.log(')
e.all['console.log('] = 'logger.debug('
e[:'import'] |= "import { logger } from '@/lib/logger'"
ensure('tsc --noEmit')
