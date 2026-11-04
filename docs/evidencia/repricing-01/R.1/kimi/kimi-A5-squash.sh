#!/bin/bash
# R.1 parte 1 para A.5: kimi sobre el squash 53c6067 (last-commit), partido por archivos.
set -u
export PATH=/opt/homebrew/bin:/Users/dn/.local/bin:/Users/dn/bin:$PATH
WT=/Users/dn/dev/_wt/f11-kimi-r1
OUT=$(dirname "$0")
run() { local fila=$1 tag=$2 archivos=$3
  local ini=$(date -u +%FT%TZ)
  /Users/dn/.local/bin/pwsh -NoProfile -File /Users/dn/quality-kit/cross-review.ps1 -Con kimi -Alcance last-commit \
     -RepoPath $WT -Archivos "$archivos" -TimeoutSec 1500 > "$OUT/kimi-$fila-$tag.md" 2>&1
  local rc=$?
  printf '\n\n---\nsha=%s archivos=%s inicio=%s fin=%s exit=%s\n' "$(git -C $WT rev-parse HEAD)" "$archivos" "$ini" "$(date -u +%FT%TZ)" "$rc" >> "$OUT/kimi-$fila-$tag.md"
  echo "$fila $tag exit=$rc"
}
run A.5 c1 "app/precio/corrida.py"
run A.5 c2 "app/cli.py,app/precio/cuota.py,app/precio/tipos.py,app/spapi/precio_write.py,docs/DEPLOY.md,tests/test_architecture.py,docs/evidencia/repricing-01/A.5/mutantes.md"
run A.5 c3 "tests/test_precio_corrida.py"
echo FIN
