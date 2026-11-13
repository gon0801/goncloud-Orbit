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
#
# La comparacion con la tolerancia es decimal exacta sobre el texto que devuelve
# la vista (numeric de precision arbitraria): estar dentro es
# ref - tolerancia <= margen <= ref + tolerancia, comparado digito a digito.
# El doble de awk daria 46.70 - 45.50 = 1.2000000000000028 > 1.2 y rechazaria
# un margen justo en la frontera (h_d7489cb0).
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
  # Aritmetica decimal exacta sobre cadenas: sin dobles en la decision.
  function partes(x, p) {
    split(x, p, ".")
    if (!(2 in p)) p[2] = ""
  }
  function cmp(x, y,   xp, yp, xs, ys, xi, yi, a, b, r) {
    xs = (x ~ /^-/) ? -1 : 1
    ys = (y ~ /^-/) ? -1 : 1
    if (xs != ys) return (xs < ys) ? -1 : 1
    sub(/^-/, "", x)
    sub(/^-/, "", y)
    partes(x, xp)
    partes(y, yp)
    while (length(xp[2]) < length(yp[2])) xp[2] = xp[2] "0"
    while (length(yp[2]) < length(xp[2])) yp[2] = yp[2] "0"
    xi = xp[1]
    yi = yp[1]
    sub(/^0+/, "", xi)
    sub(/^0+/, "", yi)
    if (xi == "") xi = "0"
    if (yi == "") yi = "0"
    if (length(xi) != length(yi))
      r = (length(xi) < length(yi)) ? -1 : 1
    else {
      a = xi xp[2]
      b = yi yp[2]
      if (a == b) r = 0
      else r = (a < b) ? -1 : 1
    }
    return (xs < 0) ? -r : r
  }
  function suma(a, b,   ap, bp, frac, lleva, i, d) {
    partes(a, ap)
    partes(b, bp)
    while (length(ap[2]) < length(bp[2])) ap[2] = ap[2] "0"
    while (length(bp[2]) < length(ap[2])) bp[2] = bp[2] "0"
    frac = ""
    lleva = 0
    for (i = length(ap[2]); i >= 1; i--) {
      d = substr(ap[2], i, 1) + substr(bp[2], i, 1) + lleva
      lleva = int(d / 10)
      frac = (d % 10) frac
    }
    if (frac == "") frac = "0"
    return (ap[1] + bp[1] + lleva) "." frac
  }
  function resta(a, b,   ap, bp, frac, presta, i, da, db) {
    partes(a, ap)
    partes(b, bp)
    while (length(ap[2]) < length(bp[2])) ap[2] = ap[2] "0"
    while (length(bp[2]) < length(ap[2])) bp[2] = bp[2] "0"
    frac = ""
    presta = 0
    for (i = length(ap[2]); i >= 1; i--) {
      da = substr(ap[2], i, 1) - presta
      db = substr(bp[2], i, 1)
      if (da < db) {
        da += 10
        presta = 1
      } else presta = 0
      frac = (da - db) frac
    }
    if (frac == "") frac = "0"
    return (ap[1] - bp[1] - presta) "." frac
  }
  $1 == "amazon_mx" || $1 == "amazon_us" {
    if ($1 == "amazon_mx") { ref = refmx; hay_mx = 1 } else { ref = refus; hay_us = 1 }
    if ($3 > 3) vencidas = 1
    if ($2 !~ /^-?[0-9]+(\.[0-9]+)?$/) {
      print $1 " margen no es un numero: [" $2 "]"
      mal = 1
      next
    }
    tolerancia = ($3 <= 1) ? "1.2" : "2.0"
    inf = resta(ref, tolerancia)
    sup = suma(ref, tolerancia)
    dif = $2 - ref
    if (dif < 0) dif = -dif
    printf "%s margen=%.2f referencia=%.2f diferencia=%.2f dias=%s tolerancia=%.1f\n", \
           $1, $2, ref, dif, $3, tolerancia
    if (cmp($2, inf) < 0 || cmp($2, sup) > 0) mal = 1
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
