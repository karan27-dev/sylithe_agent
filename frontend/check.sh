#!/usr/bin/env bash
# Parse the frontend the way the BROWSER does.
#
# `node --check` parses as a CommonJS script. index.html loads main.js with
# type="module", and the two grammars differ - so --check has twice now passed
# a file the browser refused to run, and the page came up blank with no error
# anywhere a person would look. Once was a duplicated const; once was a
# fragment left behind by an edit that cut an array in half.
#
# Copying to .mjs forces module parsing. Bare imports are rewritten to a stub
# so resolution does not fail on browser-only paths like /static/md.js.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
printf 'export const render=s=>s;export const esc=s=>String(s);\n' > "$tmp/md.js"

fail=0
for f in main.js admin.js md.js; do
  [ -f "$f" ] || continue
  sed 's|"/static/md.js[^"]*"|"./md.js"|' "$f" > "$tmp/${f%.js}.mjs"
  if out=$(node --check "$tmp/${f%.js}.mjs" 2>&1) \
     && out=$(node --input-type=module --eval "
        import('file://$tmp/${f%.js}.mjs').catch(e=>{
          if(!/Cannot find module|document is not defined|is not defined/.test(e.message)){
            console.error(e.message); process.exit(1);
          }
        })" 2>&1); then
    echo "  ok   $f"
  else
    echo "  FAIL $f"
    echo "$out" | head -3 | sed 's/^/       /'
    fail=1
  fi
done

# A duplicate top-level declaration is a module-level parse error that kills
# the whole file before one line runs, and it is the easiest thing to
# reintroduce by appending to a file.
python3 - "$@" <<'PY'
import re, sys, pathlib
bad = 0
for name in ("main.js", "admin.js"):
    p = pathlib.Path(name)
    if not p.exists():
        continue
    seen = {}
    for i, line in enumerate(p.read_text().splitlines(), 1):
        m = re.match(r'(?:const|let|var|function|async function|class)\s+([A-Za-z_$][\w$]*)', line)
        if m and not line.startswith((" ", "\t")):
            seen.setdefault(m.group(1), []).append(i)
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    if dup:
        print(f"  FAIL {name}: duplicate top-level names {dup}")
        bad = 1
    else:
        print(f"  ok   {name}: no duplicate top-level names")
sys.exit(bad)
PY
exit $fail
