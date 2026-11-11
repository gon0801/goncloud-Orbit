#!/usr/bin/env bash
# Prueba-real del bloque J (ARREGLOS 01): la guarda de "estructura vieja" mide
# la ultima corrida ok del sync de estructura que levanto acta por plataforma
# (ads_listado_plataforma), no ad_entity_state.synced_at, columna que tambien
# escriben los aplicadores despues de cada readback.
# Solo lectura. Sale 0 solo si el codigo desplegado devuelve, por plataforma,
# exactamente la fecha del ultimo sync bueno leida de produccion.
set -euo pipefail
aqui=$(cd "$(dirname "$0")" && pwd)
script=$(mktemp)
trap 'rm -f "$script"' EXIT

sql_acta=$(cat <<'SQL'
SELECT p.platform, max(r.started_at)
  FROM ingest_run r
  JOIN ads_listado_plataforma p ON p.ingest_run_id = r.id
 WHERE r.source = 'amazon_ads_structure_v2'
   AND r.ok
 GROUP BY 1
 ORDER BY 1
SQL
)
sql_aplicador=$(cat <<'SQL'
SELECT e.platform, max(s.synced_at)
  FROM ad_entity_state s
  JOIN ad_entity e ON e.id = s.ad_entity_id
 GROUP BY 1
 ORDER BY 1
SQL
)

acta=$(printf '%s\n' "$sql_acta" | bash "$aqui/../lee_prod.sh")
if [ -z "$acta" ]; then
    echo "sin filas: ningun sync de estructura ok dejo acta por plataforma" >&2
    exit 1
fi
aplicador=$(printf '%s\n' "$sql_aplicador" | bash "$aqui/../lee_prod.sh")

if [ "$acta" = "$aplicador" ]; then
    {
        echo "hoy coinciden: max(ads_listado_plataforma.started_at) y"
        echo "max(ad_entity_state.synced_at) dan la misma fecha por plataforma."
        echo "La prueba no puede distinguir al escritor por datos; queda como"
        echo "verificacion de que el codigo desplegado devuelve esa fecha."
        printf '%s\n' "$acta"
    } > "$aqui/nota.txt"
fi

cat > "$script" <<'PY'
from app.optimizer import windows

print(windows._SQL_SYNC_PLATAFORMA.strip())
PY
sql_desplegado=$(bash "$aqui/../en_prod.sh" "$script")
if [ -z "$sql_desplegado" ]; then
    echo "sin SQL: el codigo desplegado no expone _SQL_SYNC_PLATAFORMA" >&2
    exit 1
fi

fallo=0
while IFS='|' read -r plataforma fecha; do
    [ -n "${plataforma:-}" ] || continue
    consulta=$(printf '%s\n' "$sql_desplegado" | sed "s/%s/'$plataforma'/g")
    fecha_desplegada=$(printf '%s\n' "$consulta" | bash "$aqui/../lee_prod.sh")
    if [ "$fecha_desplegada" = "$fecha" ]; then
        echo "$plataforma: el codigo desplegado devuelve el sync de $fecha"
    else
        echo "$plataforma: el codigo desplegado devuelve '$fecha_desplegada' y el ultimo sync es $fecha" >&2
        fallo=1
    fi
done <<< "$acta"
exit "$fallo"
