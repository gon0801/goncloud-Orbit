#!/usr/bin/env bash
# ARREGLOS 02 D.4: comprueba en produccion (solo lectura, via arreglos-01/lee_prod.sh)
# que el margen de v_target_margen_plataforma ya no cuenta la comision estimada
# duplicada. Compara amazon_mx y amazon_us contra el margen sin duplicado medido
# el dia de D.3 (antes.txt); meli no se compara. La vista se mueve sola, por eso
# la tolerancia depende de los dias transcurridos: 1.2 puntos con 0 o 1 dia,
# 2.0 con 2 o 3.
#
# Sale 0 si las dos plataformas quedan dentro de la tolerancia (diferencia
# absoluta, sin redondear); 1 si alguna queda fuera, falta su fila o su margen
# no es un numero; 2 si no se pudo leer produccion o pasaron mas de 3 dias
# (cifras de referencia vencidas).
set -uo pipefail
AQUI=$(cd "$(dirname "$0")" && pwd)
LEE="$AQUI/../../arreglos-01/lee_prod.sh"

# Medidos el 2026-11-13 (docs/evidencia/arreglos-02/D/antes.txt, q_margen):
REF_MX=45.50
REF_US=28.99
DIA=2026-11-13

salida=$(echo "SELECT platform, margen_neto_pct, CURRENT_DATE - DATE '$DIA' FROM v_target_margen_plataforma" | bash "$LEE" 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$salida" ]; then
  echo "no se pudo leer produccion" >&2
  exit 2
fi

printf '%s\n' "$salida" | awk -F'|' -v refmx="$REF_MX" -v refus="$REF_US" '
  $1 == "amazon_mx" || $1 == "amazon_us" {
    if ($1 == "amazon_mx") { ref = refmx; hay_mx = 1 } else { ref = refus; hay_us = 1 }
    if ($3 > 3) vencidas = 1
    if ($2 !~ /^-?[0-9]+(\.[0-9]+)?$/) {
      print $1 " margen no es un numero: [" $2 "]"
      mal = 1
      next
    }
    diferencia = $2 - ref
    if (diferencia < 0) diferencia = -diferencia
    tolerancia = ($3 <= 1) ? 1.2 : 2.0
    printf "%s margen=%.2f referencia=%.2f diferencia=%.2f dias=%s tolerancia=%.1f\n", \
           $1, $2, ref, diferencia, $3, tolerancia
    if (diferencia > tolerancia) mal = 1
  }
  END {
    if (vencidas) {
      print "cifras de referencia vencidas"
      exit 3
    }
    if (!hay_mx) { print "falta la fila de amazon_mx"; mal = 1 }
    if (!hay_us) { print "falta la fila de amazon_us"; mal = 1 }
    exit mal ? 1 : 0
  }
'
case $? in
  0) exit 0 ;;
  3) exit 2 ;;
  *) exit 1 ;;
esac
