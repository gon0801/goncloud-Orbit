# X.1: evidencia de los scripts (REPRICING 02, seccion 3)

Scripts: `sembrar.sh`, `encender.sh`, `apagar.sh` (X.1 NO lleva
`sonda.sh`: el camino de escritura ya se probo en A.4). Los tres
simulan por omision y escriben solo con `--acepto-mutacion-real`;
corren dentro de `orbit-app-1` con la forma de la sonda de M.0 (el
python viaja por stdin de `docker exec -i`; el token se lee ahi de
`$ORBIT_SECRETS_DIR/api_write_token` y jamas se imprime ni sale del
servidor). En simulacion los POST van a `ORBIT_SIM_API` con
`ORBIT_SIM_TOKEN`.

## Decisiones con razon

1. `--go` obligatorio en `encender.sh`. El plan no fija el texto del
   go literal de la fila (es "el literal del dueno", como en
   `tools/precio_goal.py`): el script lo exige por bandera y no trae
   default. Sin go no hay encendido. El literal viaja en base64 de
   una linea (`-w0`, C-W3) por el ssh y en el cuerpo del POST; nunca
   se imprime (verificado: el literal usado en la sim esta ausente
   de todas las salidas).
2. `apagar.sh` regresa el universo Y los goals a shadow. La guia lo
   define como "la reversa de `encender.sh`": una reversa que dejara
   los goals en live no revertiria el encendido. El universo va
   primero (frena al motor) y luego los goals por bloques de 500.
   `precio_modo_global` NO se toca (otros universos pueden seguir
   vivos).
3. `encender.sh` FRENA sin goals shadow que pasar. Encender sin
   siembra previa seria un exito que miente (el motor no moveria
   nada): avisa correr `sembrar.sh` primero. Igual si
   `ads_bid_politica_amazon_mx` trae un valor distinto de ausente o
   `niveles_v3` (verificacion de solo-lectura ANTES de todo, tambien
   en simulacro).

## Salidas guardadas (todas con su linea exit= al final)

Base `orbit_sim_X1_<pid>`: 100 % SINTETICA (C-W6, repo publico: nada
copiado de prod). Esquema = subconjunto ORDEN59 de los tests (el
full-forward exige datos prod en la 0011) + semilla propia: settings
shadow/banda 10-60 (valores de los tests, no de prod), 5 listings
falsos `amazon_mx` con ids 90001-90005, escenarios+oferta+fees
sinteticos (cuenta de los tests: C=40, P=116, F=15) y 1 goal shadow
pre-sembrado en la FBM. App local (`uvicorn`, puerto 8012). La base se
borro al terminar (verificado: 0 bases `orbit_%` locales). SKUs
saneados a `<sku>` con la regla de F.0 (por `sed`, mecanico; la huella
no cubre SKUs: sigue correspondiendo al contenido). DSN enmascarado a
`orbit:***@` donde se muestra (la conexion usa el real).

Semilla (ids claramente falsos):

- 90001 (FBA, m=31 %), 90002 (FBA, m=47 %): se siembran en el acepto.
- 90003 (sin escenario): demo de bloqueo (BLOQUEO + OMITIDA).
- 90004 (FBM con escenario, goal shadow pre-sembrado): C-W2 exige que
  encender/apagar NO la toquen (testigo en las colas de estado).
- 90005 (FBM con escenario, sin goal): C-W2 exige que sembrar NO la
  toque (sigue `sin-goal` tras el acepto).

- `sembrar-simulacro.txt`, `encender-simulacro.txt`,
  `apagar-simulacro.txt` (exit=0): por omision no postean nada; el
  estado quedo intacto. El simulacro de sembrar resuelve la seleccion
  MX FBA (`[90001,90002,90003]`: la FBM 90005 queda fuera).
- `sembrar-bloqueo-sim.txt` (exit=0): siembra con `--acepto` de la
  unidad 90003 (sin escenario, ids explicitos): 1 fila, 1
  `BLOQUEO ... sin_escenario` con motivo, `sembrados=0` y 1 `OMITIDA`
  (B-C1: la omision se lista).
- `sembrar-acepto-sim.txt` (exit=0) + `plan-simulacion.json`:
  siembra con `--acepto` del universo MX FBA: plan de 3 filas
  guardado byte-igual (`diff` PLAN_JSON vs archivo: iguales),
  `sembrados=2 cerrados=0` + 1 omitida (90003). La FBM 90005 sigue
  `sin-goal` (cola de estado real). Huella `787a7168f22438b7`
  (determinista: dos corridas en bases frescas dan la misma).
- `encender-acepto-sim.txt` (exit=0): settings `shadow -> live`
  (global + universo) y 2 goals a live (`sembrados=2 cerrados=2`,
  huella `0be654e1b1349578`); estado `global=live univ=live`,
  90001+90002 en live y 90004 intacta en shadow (cola real, C-W2).
  El go viajo en base64 de una linea (`-w0`, C-W3) y nunca se
  imprime (verificado: ausente en las salidas).
- `apagar-acepto-sim.txt` (exit=0): universo `live -> shadow`
  (global intacto en live) y 2 goals a shadow; estado `global=live
  univ=shadow`, 90004 intacta. Round-trip completo.

Sin `mutantes.md`: no se agregaron pruebas nuevas (carril
`tdd:skip:ops` del plan). Las simulaciones son corridas guardadas,
no tests commiteados con logica que mutar.

## Fix CodeRabbit post-PR (ruta real, 2026-10-15)

`docker exec` no propaga el entorno del cliente: la ruta real
(`X_*='...' docker exec ...`) dejaba al python sin variables
(`KeyError`; la sim no lo detecta porque corre local). Los tres
scripts ahora hacen `export X_*=...; docker exec -i -e X_* ...`.
Validado con dobles locales (`/tmp/prueba-x1-docker-e.sh`: patron
viejo ve 0 vars, nuevo ve las 6) + `bash -n` en los tres. La ruta
sim no cambio: las salidas de arriba siguen valiendo.
