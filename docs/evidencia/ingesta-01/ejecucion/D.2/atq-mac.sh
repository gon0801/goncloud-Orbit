#!/bin/sh
# atq de esta Mac para el motor.
#
# /usr/bin/atq lee /usr/lib/cron/jobs y hoy está vacío. Crear un trabajo ahí
# se cuelga dentro de open() (macOS 27.0.1): ni setuid root (el binario at lo
# es) logra crear /private/var/at/jobs/.lockfile. Cargar atrun o reiniciar
# backgroundtaskmanagementd pide sudo, y esta cuenta no tiene sudo sin
# contraseña.
#
# El recordatorio de 7 días está en la cola real de gonserver (mismo patrón
# que /mnt/data/appdata/orbit/recordatorios/). Este atq imprime esa cola, y
# también la local si algún día el spool del sistema vuelve a tener trabajos.
# No inventa líneas: si gonserver no contesta y la cola local está vacía, no
# imprime nada.
set -u
local_out=$(/usr/bin/atq "$@" 2>/dev/null || true)
remote_out=$(ssh -o BatchMode=yes -o ConnectTimeout=10 gonserver /usr/bin/atq "$@" 2>/dev/null || true)
printf '%s\n%s\n' "$local_out" "$remote_out" | awk 'NF'
