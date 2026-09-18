#!/usr/bin/env bash
# ORBIT · fase 10 · repricing-01 · E.0a
#
# Prueba de los dos candados de correr.sh, sin tocar produccion. Cada caso
# copia el corredor a un directorio temporal con un `ssh` falso delante en
# el PATH (si el corredor llega a conectar, el ssh falso deja una marca y
# sale 99, y nunca habla con goncloud) y lo corre de verdad:
#
#   1. fugas sembradas (una por palabra de escritura del candado, una por
#      palabra de control de transaccion, `end` como sentencia y tres
#      metacomandos de psql): el corredor sale 1 con su ATORADO ANTES de
#      conectar (sin marca del ssh falso y sin salidas/CORRIDA.txt);
#   2. las consultas reales de consultas/: el corredor pasa los dos
#      candados y llega a conectar (marca del ssh falso), que es lo que
#      prueba que el filtro deja pasar lo legitimo sin duplicar su regex
#      aqui.
#
# Antes comprueba que correr.sh llama `ssh` por el PATH: si algun dia lo
# llama con ruta absoluta, el ssh falso dejaria de interceptarlo y esta
# prueba podria hablar con goncloud; entonces aborta sin correr nada.
#
#   bash docs/evidencia/repricing-01/E.0/prueba-candados.sh
#
# Sale 0 solo si todo eso se cumple.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fallas=0

# Precondicion: el corredor llama `ssh` por el PATH (no /usr/bin/ssh ni
# `command ssh`), o el ssh falso no lo intercepta.
if ! grep -qE '^[[:space:]]*\} \| ssh goncloud' "$DIR/correr.sh" \
    || grep -qE '(/[[:alnum:]_/]*ssh |command ssh)' "$DIR/correr.sh"; then
    echo "ATORADO: correr.sh ya no llama 'ssh goncloud' por el PATH; esta prueba no correria sin riesgo" >&2
    exit 1
fi

preparar() {  # imprime un directorio temporal con el corredor y el ssh falso
    local t
    t="$(mktemp -d)"
    mkdir -p "$t/consultas" "$t/bin"
    cp "$DIR/correr.sh" "$DIR/solo-select.py" "$t/"
    printf '#!/usr/bin/env bash\ncat > /dev/null\ntouch "%s/SSH_LLAMADO"\nexit 99\n' "$t" > "$t/bin/ssh"
    chmod +x "$t/bin/ssh"
    printf '%s' "$t"
}

probar_fuga() {  # $1 = nombre, $2 = contenido de la consulta sembrada, $3 = texto esperado
    local t salida rc
    t="$(preparar)"
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

# Una fuga por cada palabra del candado de escritura de correr.sh.
for palabra in insert update delete truncate alter drop create grant copy; do
    probar_fuga "palabra de escritura '$palabra'" "select 1; $palabra ledger_event;" "palabra de escritura prohibida"
done
# Una fuga por cada palabra de control de transaccion o escritura indirecta.
for palabra in commit rollback abort begin savepoint release into call execute prepare lock vacuum listen notify refresh reindex cluster discard reset merge comment security import load do set start transaction; do
    probar_fuga "control de transaccion '$palabra'" "select 1; $palabra x;" "control de transaccion"
done
probar_fuga "end como sentencia" "select 1;
end;" "select/with"
probar_fuga "end tras punto y coma" "select 1; end" "select/with"
probar_fuga "END WORK" "select 1; END WORK;" "select/with"
probar_fuga "END AND CHAIN" "select 1; END AND CHAIN;" "select/with"
probar_fuga "END con comentario de bloque" "select 1; END/*x*/;" "select/with"
probar_fuga "END con comentario de linea" "select 1; END --x
;" "select/with"
probar_fuga "revoke" "select 1; revoke select on x from y;" "select/with"
probar_fuga "dollar-quoting que esconde END WORK" "select \$\$ ' \$\$; END WORK; revoke select on x from y;" "select/with"
probar_fuga "dollar-quoting con tag" "select \$q\$ x \$q\$;" "select/with"
probar_fuga "dollar-quoting con tag no ASCII" "select \$é\$ ' \$é\$; END WORK; revoke select on x from y; -- '" "select/with"
probar_fuga "parametro posicional" "select \$1;" "select/with"
probar_fuga "string sin cerrar" "select 'abc;" "select/with"
probar_fuga "comentario de bloque sin cerrar" "select 1; /* sin cierre" "select/with"
probar_fuga "analyze" "select 1; analyze x;" "select/with"
probar_fuga "select into" "select 1 into t;" "control de transaccion"
probar_fuga "set_config" "select set_config('search_path', 'x', false);" "control de transaccion"
probar_fuga "metacomando de psql al inicio de linea" 'select 1;
\! echo fuga' "diagonal invertida"
probar_fuga "metacomando a mitad de linea" 'select 1 \g' "diagonal invertida"
probar_fuga "metacomando con espacios delante" '   \o /tmp/x' "diagonal invertida"

# Un select legitimo con case ... end en varias lineas (END solo en su
# linea) y comentarios con punto y coma NO es una fuga: llega a conectar.
t="$(preparar)"
printf '%s\n' "-- comentario con ; y la palabra end" "select" "    case" "        when 1 = 1 then 'a;b'" "        else 'c'" "    end" "    as x" "/* bloque ; end */" "from (select 1) s;" > "$t/consultas/98-case-multilinea.sql"
set +e
salida="$(PATH="$t/bin:$PATH" bash "$t/correr.sh" 2>&1)"
rc=$?
set -e
if [ -e "$t/SSH_LLAMADO" ]; then
    echo "VERDE: un select con case ... end en varias lineas pasa los candados (rc=$rc)"
else
    echo "FALLA: un select legitimo con case ... end multilinea fue rechazado (rc=$rc)"
    printf '%s\n' "$salida" | head -5
    fallas=$((fallas + 1))
fi

# Las consultas reales pasan los dos candados: el corredor llega a conectar.
t="$(preparar)"
cp "$DIR"/consultas/*.sql "$t/consultas/"
set +e
salida="$(PATH="$t/bin:$PATH" bash "$t/correr.sh" 2>&1)"
rc=$?
set -e
if [ -e "$t/SSH_LLAMADO" ] && ! printf '%s' "$salida" | grep -Eq 'prohibida|diagonal invertida|control de transaccion|select/with'; then
    echo "VERDE: las consultas reales pasan los dos candados (el corredor llego a conectar al ssh falso; rc=$rc)"
else
    echo "FALLA: las consultas reales no pasan los candados del corredor (rc=$rc)"
    printf '%s\n' "$salida" | head -5
    fallas=$((fallas + 1))
fi

if [ "$fallas" -ne 0 ]; then
    echo "ROJO: $fallas prueba(s) de candado fallaron" >&2
    exit 1
fi
echo "LISTO: los dos candados de correr.sh muerden y las consultas reales pasan"
