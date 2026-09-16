# Cleanup - como dejar la maquina como estaba

Despues del Drive. Los dirs de abajo son los mismos que Launch.md creo con
`mktemp -d` en el paso 1 y el paso 3. Los guards de abajo existen
precisamente para el caso en que el shell hereda export viejas (de otra
sesion, de dotfiles, de un agente anterior): si el valor no matchea el
mktemp de este procedimiento, Cleanup REFUSA borrar y te lo dice en la cara.
Regla general: cada variable se valida contra SU prefix ANTES de pg_ctl y
antes de cualquier rm, y el path se RESUELVE a su forma fisica antes de
actuar: el match textual del prefix no basta, porque un valor con `..`
matchea el case y el comando resolveria igual FUERA del sandbox (traversal
con `..`, sello #3). No existe la forma corta con globs globales
(`rm -rf /tmp/orbit-pgdata.* /tmp/orbit-pgsock.* /tmp/orbit-secrets.*`):
un glob asi alcanzaria los dirs de otra corrida paralela.

1. Parar el cluster temporal - PRIMERO se valida PGDATA contra su prefix y
   solo si matchea se ejecuta pg_ctl (un PGDATA heredado de otro shell
   podria apuntar a un cluster ajeno: validarlo es lo que evita pararlo).
   Antes de actuar, el path se resuelve (cd + pwd -P) y se re-chequea
   contra el prefix RESUELTO: si el dir no existe o resuelve fuera,
   REFUSA (traversal con `..`, sello #3):

   ```bash
   # Resuelve un path de DIRECTORIO a su forma fisica: cd + pwd -P sigue
   # symlinks y colapsa ..; si el dir no existe, falla y el guard refusa.
   resolver_dir() { cd "$1" 2>/dev/null && pwd -P; }
   # /tmp fisico: /tmp puede ser symlink (macOS: /private/tmp); los guards
   # comparan contra el prefix RESUELTO, no contra el literal
   tmp_real="$(resolver_dir /tmp)"

   case "$PGDATA" in
     /tmp/orbit-pgdata.*)
       if pgdata_real="$(resolver_dir "$PGDATA")"; then
         case "$pgdata_real" in
           "$tmp_real"/orbit-pgdata.*)
             pg_ctl -D "$pgdata_real" stop -m fast
             pg_isready -h 127.0.0.1 -p 5433   # debe dar "no response"
             ;;
           *)
             echo "REFUSADO: \$PGDATA='$PGDATA' resuelve a '$pgdata_real', fuera de \$tmp_real/orbit-pgdata.*; no toco el cluster" >&2
             ;;
         esac
       else
         echo "REFUSADO: \$PGDATA='$PGDATA' no se puede resolver (no existe o no es dir); no toco el cluster" >&2
       fi
       ;;
     *)
       echo "REFUSADO: \$PGDATA='$PGDATA' no es un mktemp de Launch; no toco el cluster" >&2
       ;;
   esac
   ```

2. Borrar los dirs temporales - cada variable contra su propio prefix, con
   el guard ANTES de cada rm. Ademas del match del prefix, el path se
   resuelve con resolver_dir (del paso 1) y se re-chequea contra el
   prefix RESUELTO antes de borrar: sin eso,
   `/tmp/orbit-pgdata.X/../../home/dn` matchea el case y el rm
   resolveria FUERA del sandbox (sello #3):

   ```bash
   borrar_si_launch() {
     local val resolved prefix_real
     eval val=\"\$$1\"
     case "$val" in
       "$2"*) ;;
       *) echo "REFUSADO: \$$1='$val' no matchea el prefix '$2*'; no borro nada" >&2; return ;;
     esac
     prefix_real="$(resolver_dir "$(dirname "$2")")/$(basename "$2")"
     if resolved="$(resolver_dir "$val")"; then
       case "$resolved" in
         "$prefix_real"*) rm -rf "$resolved" ;;
         *) echo "REFUSADO: \$$1='$val' resuelve a '$resolved', fuera de '$prefix_real*'; no borro nada" >&2 ;;
       esac
     else
       echo "REFUSADO: \$$1='$val' no se puede resolver (no existe o no es dir); no borro nada" >&2
     fi
   }
   borrar_si_launch PGDATA            /tmp/orbit-pgdata.
   borrar_si_launch SOCK              /tmp/orbit-pgsock.
   borrar_si_launch ORBIT_SECRETS_DIR /tmp/orbit-secrets.
   ```

   El dir de secretos es el VACIO creado para ORBIT_SECRETS_DIR en el paso 3
   de Launch. El default de produccion (`/mnt/data/appdata/orbit/secrets`)
   NUNCA matchea el prefix `/tmp/orbit-secrets.*`, asi que el guard lo
   rechaza de fabrica.

3. Borrar el venv desechable si se creo en /tmp:

   ```bash
   rm -rf /tmp/orbit-verify-venv   # solo si lo creaste vos en esta corrida
   ```
