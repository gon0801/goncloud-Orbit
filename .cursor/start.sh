#!/usr/bin/env bash
# Arranque por-boot del entorno de Orbit: deja un Postgres 16 UTILIZABLE en
# localhost:5432 con el rol superuser `orbit`/`orbit`, que es exactamente lo
# que espera la suite de tests (tests/test_schema.py::_test_dsn ->
# "postgresql://orbit:orbit@localhost:5432/postgres") y el docker-compose del
# repo. Idempotente: se puede correr en cada boot sin duplicar nada.
set -euo pipefail

PG_VER=16
PG_CLUSTER=main

# El cluster lo suele crear el paquete al instalarse; si el base image no lo
# hizo (policy-rc.d en Docker), lo creamos aca. `-h` -> salida sin encabezado.
if ! pg_lsclusters -h 2>/dev/null | awk '{print $1, $2}' | grep -qx "$PG_VER $PG_CLUSTER"; then
  sudo pg_createcluster "$PG_VER" "$PG_CLUSTER"
fi

# Arranca el cluster (si ya esta online, pg_ctlcluster devuelve sin romper
# gracias al `|| true`; el readiness real lo confirma pg_isready abajo).
sudo pg_ctlcluster "$PG_VER" "$PG_CLUSTER" start || true

# Espera a que Postgres acepte conexiones antes de tocar roles (evita la
# carrera "arranque en progreso" del primer boot).
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q; then
    break
  fi
  sleep 1
done

# Rol de servicio `orbit`: LOGIN + SUPERUSER (la suite de integracion crea y
# tira bases y roles temporales, necesita superuser). Password de DESARROLLO
# local, no un secreto: la misma convencion que CI y docker-compose. Idempotente.
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'orbit'" | grep -qx 1; then
  sudo -u postgres psql -c "CREATE ROLE orbit WITH LOGIN SUPERUSER PASSWORD 'orbit';"
fi

echo "Postgres $PG_VER/$PG_CLUSTER listo en localhost:5432 (rol orbit)."
