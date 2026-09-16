# Cleanup — como dejar la maquina como estaba

Despues del Drive:

1. Parar y borrar el cluster temporal:
   export LC_ALL=en_US.UTF-8
   pg_ctl -D "$PGDATA" stop -m fast && rm -rf "$PGDATA" "$SOCK"
   (pg_isready -h 127.0.0.1 -p 5433 debe dar "no response")
2. Borrar el dir de secretos vacios creado para ORBIT_SECRETS_DIR:
   rm -rf "$ORBIT_SECRETS_DIR"
3. Borrar el venv desechable si se creo en /tmp:
   rm -rf /tmp/orbit-verify-venv
