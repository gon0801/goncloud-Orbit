#!/usr/bin/env bash
# Reversa del despliegue por el motor (AUTO-03 B.2). Uso: MOTOR_SHA=<sha> bash rollback.sh
# Toma el candado y corre `revierte` (comun.sh): deshace solo lo anotado en ultimo.env por ese
# MOTOR_SHA. Sale 0 si no habia nada que deshacer; con ESTADO=revertido sale 1 y con
# ESTADO=rollback_incompleto sale 2. Correrlo otra vez continua una reversa cortada.
set -uo pipefail
. "$(dirname "$0")/comun.sh"

SHA=$(git rev-parse --verify "${MOTOR_SHA:?falta MOTOR_SHA}^{commit}") || exit 1

rc=0
toma_candado || rc=$?
if [ "$rc" = 75 ]; then echo "candado ocupado ${ESPERA}s: no lei ni revierto nada"; exit 75; fi
[ "$rc" = 0 ] || { echo "ERROR: no pude tomar el candado" >&2; exit 1; }
revierte
