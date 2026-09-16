# Cleanup - como dejar la maquina como estaba

Despues del Drive. Los dirs de abajo son los mismos que Launch.md creo con
`mktemp -d` en el paso 1 y el paso 3. Los guards de abajo existen
precisamente para el caso en que el shell hereda export viejas (de otra
sesion, de dotfiles, de un agente anterior): si el valor no matchea el
mktemp de este procedimiento, Cleanup REFUSA borrar y te lo dice en la cara.
Regla general: cada variable se valida contra SU prefix ANTES de pg_ctl y
antes de cualquier rm. No existe la forma corta con globs globales
(`rm -rf /tmp/orbit-pgdata.* /tmp/orbit-pgsock.* /tmp/orbit-secrets.*`): un
glob asi alcanzaria los dirs de otra corrida paralela.

1. Parar el cluster temporal - PRIMERO se valida PGDATA contra su prefix y
   solo si matchea se ejecuta pg_ctl (un PGDATA heredado de otro shell
   podria apuntar a un cluster ajeno: validarlo es lo que evita pararlo):

   ```bash
   case "$PGDATA" in
     /tmp/orbit-pgdata.*)
       pg_ctl -D "$PGDATA" stop -m fast
       pg_isready -h 127.0.0.1 -p 5433   # debe dar "no response"
       ;;
     *)
       echo "REFUSADO: \$PGDATA='$PGDATA' no es un mktemp de Launch; no toco el cluster" >&2
       ;;
   esac
   ```

2. Borrar los dirs temporales - cada variable contra su propio prefix, con
   el guard ANTES de cada rm:

   ```bash
   borrar_si_launch() {
     local val
     eval val=\"\$$1\"
     case "$val" in
       "$2"*) rm -rf "$val" ;;
       *) echo "REFUSADO: \$$1='$val' no matchea el prefix '$2*'; no borro nada" >&2 ;;
     esac
   }
   borrar_si_launch PGDATA            /tmp/orbit-pgdata.
   borrar_si_launch SOCK              /tmp/orbit-pgsock.
   borrar_si_launch ORBIT_SECRETS_DIR /tmp/orbit-secrets.
   ```

   El dir de secretos es el VACIO creado para ORBIT_SECRETS_DIR en el paso 3
   de Launch. El default de produccion (`/mnt/data/appdata/orbit/secrets`)
   NUNCA matchea el prefix `/tmp/orbit-secrets.*`, asi que el guard lo rechaza
   de fabrica.

3. Borrar el venv desechable si se creo en /tmp:

   ```bash
   rm -rf /tmp/orbit-verify-venv   # solo si lo creaste vos en esta corrida
