import glob, os, shutil, subprocess, sys, tempfile, difflib

IMPORT = "import { logger } from '@/lib/logger'"
paths = sorted({p for pat in ('src/**/*.ts', 'src/**/*.tsx')
                for p in glob.glob(pat, recursive=True)
                if not p.startswith('src/workers/')})
orig, new = {}, {}
for p in paths:
    s = open(p, encoding='utf-8').read()
    if 'console.log(' not in s:
        continue
    n = s.replace('console.log(', 'logger.debug(')
    if IMPORT not in n.splitlines():
        i = n.find('import')
        if i == -1:
            sys.exit(f'{p}: anchor "import" not found')
        n = n[:i] + IMPORT + '\n' + n[i:]
    orig[p], new[p] = s, n

def write(p, s):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or '.')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(s)
    shutil.copymode(p, tmp)
    os.replace(tmp, p)

for p, s in new.items():
    write(p, s)
r = subprocess.run(['tsc', '--noEmit'], capture_output=True, text=True)
if r.returncode != 0:
    for p, s in orig.items():
        write(p, s)
    print(r.stdout[-2000:])
    sys.exit('ensure failed: rolled back')
for p in new:
    sys.stdout.writelines(difflib.unified_diff(
        orig[p].splitlines(True), new[p].splitlines(True), p, p))
