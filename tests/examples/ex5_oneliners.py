from ed import *
E('app/**/*.tsx').having(rx(r'\buse[A-Z]\w*\(')).top |= "'use client'\n"
E('src/**/*.tsx').having("from '@/ui/Modal'").all[w('isOpen')] = 'open'
del E('src/**/*.ts').all['// @debug'].line
del E('src/api/client.ts')['function legacyFetch'].block
E('.env', '.env.example').bottom |= 'SENTRY_DSN='
E('src/index.ts')['// exports:start':'// exports:end'].lines.sort()
