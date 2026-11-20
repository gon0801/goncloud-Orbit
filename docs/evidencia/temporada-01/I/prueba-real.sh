#!/usr/bin/env bash
# Prueba-real de la fila I de TEMPORADA 01 (plans/temporada-01.md), comando textual.
# Corre desde la raiz del repo en el commit mergeado:
#   bash docs/evidencia/temporada-01/I/prueba-real.sh
set -uo pipefail
echo "== comando textual de la fila I =="
echo 'echo "select platform, margen_vigente_pct from v_target_margen_plataforma" | bash docs/evidencia/arreglos-01/lee_prod.sh | grep -q amazon_us && bash docs/evidencia/arreglos-01/migracion_aplicada.sh temporada01_margen_vigente'
echo "== salida completa del SELECT de la vista en produccion (solo lectura) =="
echo "select platform, margen_vigente_pct from v_target_margen_plataforma" | bash docs/evidencia/arreglos-01/lee_prod.sh
echo "== pipeline textual con && (grep -q amazon_us && migracion_aplicada.sh) =="
echo "select platform, margen_vigente_pct from v_target_margen_plataforma" | bash docs/evidencia/arreglos-01/lee_prod.sh | grep -q amazon_us && bash docs/evidencia/arreglos-01/migracion_aplicada.sh temporada01_margen_vigente
echo "== exit del pipeline textual: $? =="
