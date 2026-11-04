# X.2: evidencia de los scripts (REPRICING 02, seccion 4)

Scripts: `sembrar.sh`, `sonda.sh`, `encender.sh`, `apagar.sh`
(X.2 SI lleva `sonda.sh`: X.1 no la necesitaba porque el camino de
escritura ya se probo en A.4; FBM estrena universo y la escritura de
un centavo con su reversa se prueba aparte, criterio 3 del plan).
Los cuatro simulan por omision y escriben solo con
`--acepto-mutacion-real`. `sembrar.sh`, `encender.sh` y `apagar.sh`
corren dentro de `orbit-app-1` con la forma de la sonda de M.0 (el
python viaja por stdin de `docker exec -i`; el token se lee ahi de
`$ORBIT_SECRETS_DIR/api_write_token` y jamas se imprime ni sale del
servidor); en simulacion los POST van a `ORBIT_SIM_API` con
`ORBIT_SIM_TOKEN`. `sonda.sh` corre `tools/precio_sonda.py` dentro
de `orbit-app-1` (`docker exec`, con `ORBIT_DSN_DECIDE` de ahi); en
simulacion corre local con mercado de archivo.

## Decisiones con razon

1. El go literal de la fila LO FIJA EL PLAN (`plan repricing-02
   X.2`, plan:154): `sonda.sh` lo escribe y no lo pide por bandera.
   `encender.sh` sigue pidiendo `--go` (el dueno pasa el mismo
   literal). El literal de la sonda viaja como argv de
   `tools/precio_sonda.py` dentro del servidor y nunca se imprime
   (verificado: ausente de `sonda-simulacro.txt` y
   `sonda-acepto-sim.txt`); el de `encender.sh` viaja en base64 de
   una linea (`-w0`, C-W3) por el ssh y en el cuerpo del POST.
2. `sonda.sh` mueve al conejillo (primer goal shadow MX FBM con
   seller_sku) un centavo sobre su precio vivo y lo revierte, con
   `tools/precio_sonda.py --acepto-mutacion-real --go` (guia:1042).
   Guarda el vivo del conejillo antes y despues (debe quedar igual)
   y el de una publicacion de control antes y despues (no cambia).
   FRENA sin 2 goals shadow con sku (sin conejillo o sin control
   avisa correr `sembrar.sh`). Si la sonda falla, avisa revisar si
   el precio quedo movido.
3. `encender.sh` sube el global a live SOLO si aun no esta en live
   (X.1 ya pudo encenderlo: no se pisa sin motivo). El universo va
   a live siempre. `apagar.sh` regresa el universo Y los goals a
   shadow; `precio_modo_global` NO se toca (otros universos pueden
   seguir vivos). El universo va primero (frena al motor) y luego
   los goals por bloques de 500.
4. `encender.sh` FRENA sin goals shadow que pasar. Encender sin
   siembra previa seria un exito que miente (el motor no moveria
   nada): avisa correr `sembrar.sh` primero. Igual si
   `ads_bid_politica_amazon_mx` trae un valor distinto de ausente o
   `niveles_v3` (verificacion de solo-lectura ANTES de todo, tambien
   en simulacro). En la sim la llave esta ausente (documenta ese
   camino; X.1 documento `niveles_v3`).
5. C-W2: la seleccion es MX FBM, no toda la plataforma. El filtro
   de canal es el escenario `disponible` mas reciente (`fbm`; sin
   escenario entra y el plan lo bloquea, nunca se voltea a ciegas).
   La FBA queda fuera de sembrar/encender/apagar (testigo 14 en las
   colas de estado).

## Salidas guardadas (todas con su linea exit= al final)

Base `orbit_sim_X2_93290`: 100 % SINTETICA (C-W6, repo publico:
nada copiado de prod). Esquema = ORDEN59 de los tests + 0055 (el
full-forward exige datos prod en la 0011) + semilla propia:
settings banda 10-60 (valores de los tests, no de prod), 4 listings
falsos `amazon_mx` con ids 11-14, escenarios+oferta+fees sinteticos
(cuenta de los tests: C=40, P=116, F=5, muestra 30). App local
(`uvicorn`, puerto 8013). La base se borro al terminar (verificado:
0 bases `orbit_%` locales).

Semilla (ids falsos, escenario de hoy):

- 11, 12 (FBM, m=22.5 %): se siembran en el acepto.
- 13 (sin escenario): demo de bloqueo (BLOQUEO + OMITIDA).
- 14 (FBA con escenario disponible, m=52.5 %): C-W2 exige que
  sembrar/encender/apagar NO la toquen (sigue `sin-goal` en todas
  las colas de estado).

- `sembrar-simulacro.txt` (exit=0): por omision no postea nada; el
  estado quedo intacto. Resuelve la seleccion MX FBM (`[11,12,13]`:
  la FBA 14 queda fuera) y muestra el settings previo (el universo
  no tiene entrada: sin el quedaria apagado).
- `sembrar-bloqueo-sim.txt` (exit=0): siembra con `--acepto` de la
  unidad 13 (sin escenario, ids explicitos): 1 fila, 1 `BLOQUEO ...
  sin_escenario` con motivo, `sembrados=0` y 1 `OMITIDA` (B-C1: la
  omision se lista). El universo quedo en shadow (config 5).
- `sembrar-acepto-sim.txt` (exit=0) + `plan-simulacion.json`:
  siembra con `--acepto` del universo MX FBM: plan de 3 filas
  guardado byte-igual (`diff` PLAN_JSON vs archivo: iguales),
  `sembrados=2 cerrados=0` + 1 omitida (13). La FBA 14 sigue
  `sin-goal` (cola de estado real). Huella `962896eb2d31c06f`
  (determinista: dos corridas en bases frescas dan la misma).
- `sonda-simulacro.txt` (exit=0, base `orbit_sim_X2r3` + mercado
  sim `orbit-sonda-X2.json`): dry-run del conejillo 11 (`[plan]
  ... 116.00 -> 116.01 -> 116.00`, cero PATCH) + lectura del
  control 12 (200.00 MXN). Sin sku ni go literal en la salida.
- `sonda-acepto-sim.txt` (exit=0, misma base): ida aceptada a
  116.01 y vuelta aceptada a 116.00; vivo antes = vivo despues
  (116.00) y control 200.00 intacto. Sin sku ni go literal.
- `encender-simulacro.txt` (exit=0): por omision no postea nada;
  muestra settings (global ausente -> live + universo live) y los
  2 goals que pasarian.
- `encender-acepto-sim.txt` (exit=0): settings `ausente -> live`
  (global) y `shadow -> live` (universo, config 6) y 2 goals a
  live (`sembrados=2 cerrados=2`, huella `9d9c7adbe5b6e538`);
  estado `global=live univ=live`, 11+12 en live y 14 intacta
  `sin-goal` (cola real, C-W2). El go viajo en base64 de una
  linea (`-w0`, C-W3) y nunca se imprime (verificado: ausente
  en las salidas).
- `apagar-acepto-sim.txt` (exit=0): universo `live -> shadow`
  (config 7; global intacto en live) y 2 goals a shadow; estado
  `global=live univ=shadow`, 14 intacta. Round-trip completo.

## R05d: filtro sin oferta viva (2026-10-16)

Igual que X.1: `sembrar.sh` excluye y lista `EXCLUIDA`, `leer.sh`
(nuevo) mide el paso 2 con el filtro en el denominador (solo lee,
sale 0 al medir, veredicto en `LECTURA:`). En FBM puro no hay filas
de inventario y el filtro no muerde; donde hay fila (SKUs con pasado
FBA) si. Simulacro de siembra de hoy (solo lectura): 121 candidatas,
24 excluidas, 97 a postear (salida completa fuera del repo).
`leer.sh`: FRENADO sin lote (universo sin sembrar), exit=1.

## R05d-r1: regla FBM del filtro (2026-10-16)

VEREDICTO-R05d-r1 (AVISO PARA EL OPERADOR): en FBM la regla
"inventario 0 Y sin precio" solo mordia a los SKUs con fila en
`spapi_inventario_observation` (los que conservan inventario de un
pasado FBA); los FBM puros no tienen fila, el `= 0` da NULL y sus
unidades sin precio propio entraban a la siembra y al denominador.
Desde este cambio, en X.2 (MX FBM) y X.3 (US FBM) basta SIN PRECIO
PROPIO OBSERVADO para `EXCLUIDA`: `sembrar.sh` ya no consulta
inventario y `leer.sh` excluye del denominador solo con la puerta S2
(NOT EXISTS precio propio el dia D). En FBA (X.1) y MeLi (X.4) la
regla sigue siendo "inventario 0 Y sin precio" (sus scripts no se
tocaron). Prueba discriminante: `tests/test_precio02_escenario_fbm.py`
corre el SQL literal de los cuatro scripts contra una base temporal
(la r1 falla contra el SQL anterior: el caso FBM sin fila de
inventario no se excluia).
