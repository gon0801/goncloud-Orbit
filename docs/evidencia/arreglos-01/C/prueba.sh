#!/usr/bin/env bash
# Prueba-real del bloque C (ARREGLOS 01): cada cola 10, 11 o 26 que llego a
# applied tiene su reversa confirmada en el ledger (una reversa es UNA por
# decision: apply_attempt con tipo='reversa' y resultado='ok').
# Sale 0 solo si no queda ninguna cola aplicada sin reversa confirmada.
set -euo pipefail
aqui=$(cd "$(dirname "$0")" && pwd)

sql=$(cat <<'SQL'
select q.id,
       q.decision_id,
       coalesce((
           select a.resultado
             from apply_attempt a
            where a.decision_id = q.decision_id
              and a.tipo = 'reversa'
            order by a.seq desc
            limit 1
       ), 'sin reversa')
  from apply_queue q
 where q.id in (10, 11, 26)
   and q.estado = 'applied'
 order by q.id
SQL
)

filas=$(printf '%s\n' "$sql" | bash "$aqui/../lee_prod.sh")
fallo=0
while IFS='|' read -r cola decision resultado; do
    [ -n "${cola:-}" ] || continue
    if [ "$resultado" = "ok" ]; then
        echo "cola $cola (decision $decision): reversa confirmada"
    else
        echo "cola $cola (decision $decision): SIN reversa confirmada ($resultado)"
        fallo=1
    fi
done <<< "$filas"
exit "$fallo"
