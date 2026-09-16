# Cleanup - como dejar la maquina como estaba

Despues del Drive. Los tres borros de abajo son los mismos dirs que Launch.md
creo con `mktemp -d` en el paso 1 y el paso 3. Los guards de abajo existen
precisamente para el caso en que el shell hereda export viejas (de otra
sesion, de dotfiles, de un agente anterior): si el valor no matchea un
mktemp de este procedimiento, Cleanup REFUSA borrar y te lo dice en la cara.

1. Parar y borrar el cluster temporal:

   ```bash
   export LC_ALL=en_US.UTF-8
   pg_ctl -D "$PGDATA" stop -m fast
   for v in PGDATA SOCK ORBIT_SECRETS_DIR; do
     eval val=\"\$$v\"
     case "$val" in
       /tmp/orbit-pgdata.*|/tmp/orbit-pgsock.*|/tmp/orbit-secrets.*)
         rm -rf "$val" ;;
       *)
         echo "REFUSADO: \$$v='$val' no es un mktemp de Launch; no borro nada" >&2 ;;
     esac
   done
   (pg_isready -h 127.0.0.1 -p 5433 debe dar "no response")
   ```

   Tambien vale la version corta si estas seguro de que el shell solo tiene
   los exports de Launch:
   `rm -rf /tmp/orbit-pgdata.* /tmp/orbit-pgsock.* /tmp/orbit-secrets.*`

2. Borrar el dir de secretos VACIO creado para ORBIT_SECRETS_DIR en el
   paso 3 de Launch - mismo guard del loop de arriba. El default de
   produccion (`/mnt/data/appdata/orbit/secrets`) NUNCA matchea el prefix
   `/tmp/orbit-secrets.*`, asi que el guard lo rechaza de fabrica.

3. Borrar el venv desechable si se creo en /tmp:

   ```bash
   rm -rf /tmp/orbit-verify-venv   # solo si lo creaste vos en esta corrida
   ```
