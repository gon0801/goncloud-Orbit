#!/bin/bash
# Cruzada de kimi por squash (R.1 parte 1), en un worktree aparte y detached.
# Cada squash se parte por archivos para que ningún trozo pase de ~58 KB (el
# script de la cruzada trunca a 60000 caracteres).
set -u
export PATH=/opt/homebrew/bin:/Users/dn/.local/bin:/Users/dn/bin:$PATH
WT=/Users/dn/dev/_wt/f11-kimi-r1
OUT=$(dirname "$0")
G=/opt/homebrew/bin/git
run() { # fila sha etiqueta archivos
  local fila=$1 sha=$2 tag=$3 archivos=$4
  $G -C $WT checkout -q --detach "$sha" || { echo "checkout $sha fallo" > "$OUT/kimi-$fila-$tag.md"; return; }
  local ini=$(date -u +%FT%TZ)
  /Users/dn/.local/bin/pwsh -NoProfile -File /Users/dn/quality-kit/cross-review.ps1 -Con kimi -Alcance last-commit \
     -RepoPath $WT -Archivos "$archivos" -TimeoutSec 900 > "$OUT/kimi-$fila-$tag.md" 2>&1
  local rc=$?
  printf '\n\n---\nsha=%s archivos=%s inicio=%s fin=%s exit=%s\n' "$sha" "$archivos" "$ini" "$(date -u +%FT%TZ)" "$rc" >> "$OUT/kimi-$fila-$tag.md"
  echo "$fila $tag exit=$rc"
}
run A.1 662db383ee517cccfac757cc815531ca70fc786a c1 "app/precio/goals_write.py,tools/precio_goal.py,tests/test_architecture.py"
run A.1 662db383ee517cccfac757cc815531ca70fc786a c2 "tests/test_precio_goals.py"
run A.1-r1 eeefb72720d7dc35c3e4335c32b92d2527d84404 c1 "app/precio/goals_write.py,tools/precio_goal.py,tests/test_architecture.py,tests/test_precio_goals.py"
run A.2 39cba88568c74dca2a826dd192df725b84cdd186 c1 "app/estimacion_fees.py,app/precio/__init__.py,app/precio/config.py,app/precio/objetivo.py,app/precio/reglas.py,app/precio/tipos.py,app/precio/ventas.py"
run A.2 39cba88568c74dca2a826dd192df725b84cdd186 c2 "tests/test_architecture.py"
run A.2 39cba88568c74dca2a826dd192df725b84cdd186 c3 "tests/test_precio_reglas.py"
run A.3 efc0555881b57e342811b37e6ce7c0aaf03e1489 c1 "app/spapi/precio_write.py,app/spapi/write_client.py,tools/precio_reversa.py"
run A.3 efc0555881b57e342811b37e6ce7c0aaf03e1489 c2 "tests/test_architecture.py,tests/test_spapi_write_client.py"
run A.3 efc0555881b57e342811b37e6ce7c0aaf03e1489 c3 "tests/test_precio_write.py"
run A.7 0d88cc818d62c89dd9b031dc472043bd52115b23 c1 "app/precio/cobertura.py,app/precio/fuentes.py,tools/precio_cobertura.py,tests/test_architecture.py,docs/evidencia/repricing-01/A.7/readback.sh,docs/evidencia/repricing-01/A.7/recuadro_desde_salidas.py,docs/evidencia/repricing-01/A.7/consultas"
run A.7 0d88cc818d62c89dd9b031dc472043bd52115b23 c2 "tests/test_precio_cobertura.py"
echo FIN
