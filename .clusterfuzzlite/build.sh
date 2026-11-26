#!/bin/bash -eu
# Build every tests/fuzz/fuzz_*.py harness into $OUT (run by ClusterFuzzLite in
# the image from .clusterfuzzlite/Dockerfile, with $SRC/helios as the checkout).
cd "$SRC/helios"

# helios-ctl is loaded from a file at run time, so PyInstaller can't see what it
# imports: bundle it and its catalogs as data and name its imports explicitly.
data=(--add-data "pkgs/helios-ctl.py:helios")
for f in pkgs/*.json; do
  data+=(--add-data "$f:helios")
done
mapfile -t hidden < <(python3 -I - pkgs/helios-ctl.py <<'PY'
import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and not n.level}
for name in sorted(names - {"bcrypt"}):        # bcrypt is optional and unused by the harnesses
    print("--hidden-import=" + name)
PY
)

for fuzzer in tests/fuzz/fuzz_*.py; do
  name=$(basename "$fuzzer" .py)
  compile_python_fuzzer "$fuzzer" "${data[@]}" "${hidden[@]}"
  if [ -d "tests/fuzz/corpus/$name" ]; then
    (cd "tests/fuzz/corpus/$name" && zip -q -r "$OUT/${name}_seed_corpus.zip" .)
  fi
  dict="tests/fuzz/${name#fuzz_}.dict"
  if [ -f "$dict" ]; then
    cp "$dict" "$OUT/$name.dict"
  fi
done
