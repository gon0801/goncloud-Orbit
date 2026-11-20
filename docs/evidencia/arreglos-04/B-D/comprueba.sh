#!/usr/bin/env bash
# B-D.3: lectura final de solo lectura. Corre el lector estricto otra vez,
# vuelca el ledger aprobado/quitado con el rol de lectura y contrasta todo
# contra antes.txt, reparacion.txt y revisar.txt. Imprime el inventario final
# y sus conteos; sale distinto de cero si un origen retirado no tiene su
# reemplazo confirmado o si queda algo sin registrar.
set -uo pipefail
base="docs/evidencia/arreglos-04/B-D"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

bash docs/evidencia/arreglos-01/en_prod.sh "$base/lee_amazon.py" > "$tmp/final.txt"
lee_rc=$?
cat "$tmp/final.txt"

echo "select v.id, v.tipo, v.plataforma::text, coalesce(v.ad_group_id, -1), coalesce(v.termino_sha256, '-'), coalesce(v.senal_id::text, '-'), coalesce(ult.paso, '-'), coalesce(ult.negative_keyword_id, '-'), coalesce(ult.motivo, '-'), coalesce(v.origen_id::text, '-') from bloqueo_veredicto v left join lateral (select e.paso, e.negative_keyword_id, e.motivo from bloqueo_efecto e where e.veredicto_id = v.id order by e.created_at desc, e.id desc limit 1) ult on true where v.tipo in ('aprobado', 'quitado') order by v.created_at" \
  | bash docs/evidencia/arreglos-01/lee_prod.sh > "$tmp/ledger.txt"
ledger_rc=$?

if [ "$lee_rc" -ne 0 ] || [ "$ledger_rc" -ne 0 ]; then
  echo "FALLA|lectura final incompleta (lee=$lee_rc ledger=$ledger_rc)"
  exit 1
fi

uv run --frozen --python 3.12 python "$base/comprueba.py" "$base" "$tmp/final.txt" "$tmp/ledger.txt"
exit $?
