#!/usr/bin/env bash
# ORBIT · fase 10 · repricing-01 · E.0a
#
# Prueba de los dos candados de correr.sh, sin tocar produccion: copia el
# corredor a un directorio temporal, siembra una consulta con una fuga y
# exige que el corredor salga 1 con su mensaje ATORADO ANTES de conectar
# (sin salidas/CORRIDA.txt: la corrida nunca empezo). Luego comprueba que las consultas reales de
# consultas/ pasan los dos filtros. Sale 0 solo si todo eso se cumple.
#
#   bash docs/evidencia/repricing-01/E.0/prueba-candados.sh
#
# El corredor se copia con un `ssh` falso delante en el PATH: si un
# candado fallara y el corredor intentara conectar, el ssh falso deja una
# marca y la prueba sale roja en vez de tocar goncloud.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fallas=0

probar_fuga() {  # $1 = nombre, $2 = contenido de la consulta sembrada, $3 = texto esperado
    local t
    t="$(mktemp -d)"
    mkdir -p "$t/consultas" "$t/bin"
    cp "$DIR/correr.sh" "$t/"
    printf '#!/usr/bin/env bash\ntouch "%s/SSH_LLAMADO"\nexit 99\n' "$t" > "$t/bin/ssh"
    chmod +x "$t/bin/ssh"
    printf '%s\n' "$2" > "$t/consultas/99-fuga.sql"
    set +e
    salida="$(PATH="$t/bin:$PATH" bash "$t/correr.sh" 2>&1)"
    rc=$?
    set -e
    if [ "$rc" -eq 1 ] && printf '%s' "$salida" | grep -q "$3" \
        && [ ! -e "$t/SSH_LLAMADO" ] && [ ! -e "$t/salidas/CORRIDA.txt" ]; then
        echo "ROJO COMO DEBE: $1 (rc=1, sin conexion)"
    else
        echo "FALLA: $1 no fue rechazada antes de conectar (rc=$rc)"
        fallas=$((fallas + 1))
    fi
}

probar_fuga "palabra de escritura" "select 1; delete from ledger_event;" "palabra de escritura prohibida"
probar_fuga "metacomando de psql" 'select 1;
\! echo fuga' "diagonal invertida"
probar_fuga "metacomando a mitad de linea" 'select 1 \g' "diagonal invertida"

if grep -liE '\b(insert|update|delete|truncate|alter|drop|create|grant|copy)\b' "$DIR"/consultas/*.sql \
    || grep -lF '\' "$DIR"/consultas/*.sql; then
    echo "FALLA: una consulta real de consultas/ no pasa los candados (ver arriba)"
    fallas=$((fallas + 1))
else
    echo "VERDE: las consultas reales pasan los dos candados"
fi

if [ "$fallas" -ne 0 ]; then
    echo "ROJO: $fallas prueba(s) de candado fallaron" >&2
    exit 1
fi
echo "LISTO: los dos candados de correr.sh muerden y las consultas reales pasan"
