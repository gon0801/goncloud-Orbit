# Diferencias entre el codigo integrado y el diseno (BASE)

Bloque `BASE` de `plans/arreglos-05.md`. SHA cotejado: `33844bb3`. Las lineas son de ese SHA.
Los bloques A a K y Z consumen esta lista antes de implementar. BASE no cambia codigo de
`app/`, `tools/` ni `migrations/`, asi que cada diferencia queda asignada a su bloque.

## Diferencias de contrato

| Id | Que supone DISENO | Que hay en `33844bb3` | Bloque |
|---|---|---|---|
| D-1 | TEMPORADA B.2 es el unico dueno de mover la conciliacion antes de TX2 y 05 solo lo verifica (DISENO, seccion A; fila A.2 del plan). | La conciliacion del ledger sigue despues de decidir. `reconcilia_harvest` y `reconcilia_bids` se llaman en `app/cycle_apply.py:240` y `:243`, dentro de `_fase_apply`, que `_corre_fases` invoca en `app/cycle.py:631` tras `_fase_lecturas` (546) y la TX3 (575-629). El diseno de TEMPORADA las deja "como hoy" en su fase d. La reproduccion legada `test_reconciliacion_corre_despues_de_decidir` (Ciclo 4) falla con `assert not (1 and 1)` ([acepta.txt](acepta.txt)). En produccion 16 de 515 aplicaciones las confirmo un ciclo distinto del que decidio ([invariantes.md](invariantes.md)). | A |
| D-2 | La reversa de bid usa el valor leido antes del PUT, aportado por TEMP E y congelado (seccion B). | La lectura previa existe (`app/apply.py:363-395`) pero el valor no se guarda en `apply_attempt`, que solo tiene `tipo`, `request_payload`, `ack`, `resultado` y tiempos. Deja rastro en `decision_sin_aplicar.detalle.leido` y en `temporada_exclusion.bid_visto` solo cuando la puja cambio por fuera. | B |
| D-3 | El cupo de 150 excluye entrada y regreso "por marcador congelado" y cuenta `tipo=normal` (seccion H). | El ledger no tiene tipo propio de temporada. `apply_attempt.tipo` es `normal`, `reversa`, `probe` o `hermana`, y toda puja entra por `_ledger(..., "normal", ...)` (`app/apply.py:436`). El marcador congelado es `decision.inputs->>'motivo'` con los tres valores de `temporada_io.MOTIVOS_TEMPORADA` y `inputs.temporada_episodio_id`; `temporada_efecto.tipo` es `entra`, `ajusta` o `sale`. H define si `ajusta` cuenta como entrada o regreso. | H |
| D-4 | "Numeracion libre al rebasar; 0086 ya esta ocupada". | `0093` esta tomado dos veces en master: `0093_estimacion_s4_caja_real_mx.sql` (#699) y `0093_temporada01_episodio.sql` (TEMPORADA B). El instalador aplica por nombre de archivo y cada una tiene su sonda y su reversa, asi que funcionan. El numero libre es `0094`. | A (primera migracion) |
| D-5 | Los comandos `T` usan los fixtures con la firma vigente. | Tres caminos publicos habian cambiado y se portaron en `2639ff8f` sin tocar aserciones. Ver "Fixtures portados". | cerrado en BASE |

## Fixtures portados

| Caso | Que cambio en el codigo | Commit | Porte |
|---|---|---|---|
| TN05, TN06, TN09, TN14, TNS1 (`revision/ingest.py`) | `app.reputacion_clientes` desaparecio. `ClienteMeli` y `MeliCredentials` viven en `app.meli.client` con el mismo constructor. | 98858999 | Importacion desde `app.meli.client`. |
| TN07 (`revision/test_accept_pricing.py`) | Lo mismo, mas la politica S4 que siembra 0093 para `meli/meli`, y `_evento_cotizacion`, que paso a `app.precio.decision_io`. | 98858999, 66bfa977, 26b56fab | Mismo cambio de importacion. La politica S3 de prueba solo se siembra si la cadena no trae una abierta. La llamada sale de su modulo actual. |
| TN11 (`revision/writes.py`) | El SQL del ledger de archivo paso de `tools/archiva_inertes.py` a `app/ads/archivar_inertes.py`, byte a byte igual. El CLI sigue entrando por `tools`. | e5f9a2cf | Importacion de `_SQL_INSERT_PLANEADO` y `_SQL_SELLA_APPLIED` desde el motor. |

Los originales estan en `revision/historico/` y coinciden con `manifest.json`: `ingest.py` y `ui.py` ya estaban, y BASE anadio `writes.py` y `test_accept_pricing.py` (recuperado de `cab8d562`, que es donde existe con el hash del manifiesto). El manifiesto no cambia.

## Reproducciones legadas del ciclo

`docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/test_revision_ciclo.py` sigue corriendo contra el codigo integrado. F1, F2 y F3 fallan por su asercion original y son la linea roja del bloque A: un 5xx deja un bid sin intentar, el ciclo `degraded` impide el reintento del dia (`motivo_de_salto`, `app/cycle.py:167-176`) y un `running` de menos de 3 h bloquea el horario siguiente. F4 es la D-1. F5 (`test_piso_aprendido_se_cruza_por_redondeo_de_amazon`) ya no corre: `_paso` exige `centavos` desde ARREGLOS 02. Es la falla 1 de politica, cerrada por 02 B y C, y BASE no la porta.

## Entorno de esta PC (Git Bash sobre Windows)

1. **Variables MSYS del shell de sesion.** Trae `MSYS_NO_PATHCONV=1` y `MSYS2_ARG_CONV_EXCL=*`. Apagan la conversion de rutas y `comprobar.sh` le pasa a Python rutas `/c/...`: pytest sale con 4 y cero casos. Hay que quitarlas (`env -u MSYS_NO_PATHCONV -u MSYS2_ARG_CONV_EXCL`). `acepta.sh` lo hace solo.
2. **Mayusculas de la carpeta.** Con `core.ignorecase=true`, `git add` con ruta `BASE/` registra `base/` y mezclaria esta evidencia con la foto de diseno. Los archivos se registran con `git hash-object -w` y `git update-index --add --cacheinfo`. Comprobacion: `git ls-files docs/evidencia/arreglos-05 | tr 'A-Z' 'a-z' | sort | uniq -d` no imprime nada.
3. **Temporal de pytest compartido.** Varias sesiones usan `pytest-of-ehven`. Si otra esta corriendo, pytest se cae al cerrar por `pytest-current` y la salida es 1 aunque las pruebas pasen. `acepta.sh` usa un `TMPDIR` propio. Otra opcion es `--basetemp`.
4. **`CI=1` exige `psql`.** `tests/test_despliegue_motor.py` aborta la recoleccion con `CI=1` si no esta `psql`, que esta PC no tiene. El comando de 01X solo recolecta `tests/test_config_write.py` y pasa. Los controles TEMPORADA no llevan `CI=1`.
5. **Sin `pre-commit`.** No esta instalado, el repo no tiene ganchos y Windows bloquea por politica de control de aplicaciones el ejecutable que baja `uvx`. Los dos ganchos de `.pre-commit-config.yaml` se corrieron a mano: `gitleaks protect --staged` con la misma version del gancho (8.30.1) y la busqueda de marcadores de conflicto. Ambos limpios.
6. **PG del laboratorio.** `127.0.0.1:55449` es el contenedor `orbit-review-current-pg` (`postgres:16`, 16.15) en el Docker de WSL. Ya estaba arriba y lo comparten varias sesiones. Las corridas son en serie.
7. **`lee_prod.sh` ejecuta una sentencia por llamada.** `previa.sh` parte `previa.sql` en bloques y hace una llamada por bloque.

## Correcciones despues de la revision automatica del PR

Las siete observaciones eran ciertas y tocaban solo a las herramientas de esta carpeta. Cinco salieron en la primera ronda y una en cada una de las dos siguientes.

| Herramienta | Defecto | Correccion y prueba |
|---|---|---|
| `acepta.sh`, BASE.0 | La cadena terminaba en `echo`, asi que devolvia 0 aunque la comprobacion de Git fallara. | El `echo` va con `&&`. En un repositorio desechable la cadena nueva sale 1 cuando `origin/master` no es ancestro y cuando falta un archivo, y 0 en orden. La anterior salia 0 en los tres casos. |
| `acepta.sh`, arranque | Abria la conexion para imprimir la version de PG antes de validar el DSN, y los comandos de pytest directo no pasan por la guarda de `comprobar.sh`. | Guarda al inicio. El destino se confirma con el parser de psycopg (`conninfo_to_dict`) y se exige la forma de URL sin consulta ni fragmento, que es la unica que entiende `tests/helpers/pg.py`. Con un DSN en pares `clave=valor`, con otro puerto, con otro host o vacio sale 2 con su mensaje y sin linea de PG. |
| `acepta.sh`, guarda | `urlsplit` no ve que libpq puede cambiar el destino con una consulta en la URL (`?hostaddr=`, `?service=`) o con una variable `PG*`. El parser de psycopg tampoco expone esas variables. | Se rechaza cualquier consulta o fragmento y, antes de conectar, se quitan del entorno las ocho variables `PG*` que pueden cambiar el servidor, elegir un servicio o alterar la sesion (`PGHOST`, `PGHOSTADDR`, `PGPORT`, `PGDATABASE`, `PGSERVICE`, `PGSERVICEFILE`, `PGLOADBALANCEHOSTS`, `PGOPTIONS`). Las de credencial (`PGPASSWORD`, `PGPASSFILE`) se conservan. Probado contra la guarda anterior: la anterior aceptaba `?port=1`, `?hostaddr=`, `?service=` y `#x`; la nueva las rechaza y acepta el DSN del laboratorio con o sin clave. |
| `acepta.sh`, controles | Un control con pruebas omitidas salia 0 y se daba por bueno. | `resumen_limpio` rechaza un resumen de pytest con `skipped`, `error`, `failed` o sin casos. Probado con once resumenes. |
| `clasifica.py` | Solo miraba los casos presentes en la corrida nueva. Una corrida incompleta salia 0. | Compara los nombres de ambos XML y sale 1 si falta uno de la base. Con un caso retirado sale 1 y lo nombra; con un XML vacio reporta 31 ausentes. |
| `previa.sql` | Atribuia el desenlace sin aplicar al ciclo ejecutor, igual que hace `registra_sin_aplicar`, mientras las aplicaciones se atribuyen al ciclo que decidio. | El `EXISTS` une `decision_sin_aplicar` con `decision` por `decision_id`. El bloque nuevo `desenlaces_con_ciclo_distinto` mide cuantos difieren. En una base desechable con un ejecutor distinto, la consulta vieja atribuye al ejecutor y la nueva al origen. En produccion los conteos por ciclo no cambian: las 193 filas llevan el ciclo de la decision. |
| `acepta.sh`, entorno | Quitar todas las `PG*` tambien borraba `PGPASSWORD` y `PGPASSFILE`, que no cambian el destino y pueden ser la unica forma de autenticar. | Solo se quitan las ocho variables de destino, servicio y sesion. Con doce variables exportadas quedan `PGPASSWORD`, `PGPASSFILE`, `PGSSLMODE` y `PGUSER`. El rechazo de DSN no cambia. |

## Notas que no son bloqueo

- `plans/ORDEN-IMPLEMENTACION.md` abre con la cola "ARREGLOS 04, TEMPORADA 01, ARREGLOS 05" y dice `manifest.active = arreglos-04`. Ya esta desactualizada y BASE no la toca. Z la actualiza al cerrar.
- En produccion `inputs.caso` no trae clave `version` en ninguna decision congelada (63 de 63). Las claves de version que si existen son `cooldown_policy_version`, `inversion_policy_version`, `politica_bandas_usada` y `economic_policy`. Lo mide F antes de versionar.
