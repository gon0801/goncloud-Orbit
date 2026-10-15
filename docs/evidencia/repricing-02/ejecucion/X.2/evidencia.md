# X.2: evidencia de los scripts (REPRICING 02, seccion 4)

Scripts: `sembrar.sh`, `sonda.sh`, `encender.sh`, `apagar.sh`
(X.2 SI lleva `sonda.sh`: X.1 no la necesitaba porque el camino de
escritura ya se probo en A.4; FBM estrena universo y la camara del go
se prueba aparte). Los cuatro simulan por omision y escriben solo con
`--acepto-mutacion-real`; corren dentro de `orbit-app-1` con la forma
de la sonda de M.0 (el python viaja por stdin de `docker exec -i`; el
token se lee ahi de `$ORBIT_SECRETS_DIR/api_write_token` y jamas se
imprime ni sale del servidor). En simulacion los POST van a
`ORBIT_SIM_API` con `ORBIT_SIM_TOKEN`.

## Decisiones con razon

1. `--go` obligatorio en `encender.sh` y en `sonda.sh`. El plan no
   fija el texto del go literal de la fila (es "el literal del
   dueno", como en `tools/precio_goal.py`): los scripts lo exigen por
   bandera y no traen default. Sin go no hay encendido ni sonda. El
   literal viaja en base64 de una linea (`-w0`, C-W3) por el ssh y
   en el cuerpo del POST; nunca se imprime (verificado: el literal
   usado en la sim esta ausente de todas las salidas).
2. `sonda.sh` voltea un goal shadow de conejillo a live y de vuelta
   a shadow en la misma corrida (la reversa va incluida). No existe
   un "goal de control" como entidad en el dominio: el conejillo es
   el primer goal shadow del universo. Si la ida falla, el go no
   abrio; si la vuelta falla, el script avisa que el conejillo quedo
   en live y como regresarlo.
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
- `sonda-simulacro.txt` (exit=0): por omision no postea nada;
  muestra la ida (live con go) y la vuelta (shadow sin go) sobre
  el conejillo 11.
- `sonda-acepto-sim.txt` (exit=0): la ida volteo al conejillo a
  live (el go abre) y la vuelta lo regreso a shadow (la reversa
  cierra); la cola quedo intacta (`11|shadow 12|shadow
  13|sin-goal 14|sin-goal`).
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
