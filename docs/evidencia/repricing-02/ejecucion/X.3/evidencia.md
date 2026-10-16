# X.3: evidencia de los scripts (REPRICING 02, seccion 4)

Scripts: `sembrar.sh`, `sonda.sh`, `encender.sh`, `apagar.sh`
(derivados de X.2 por `sed`: MX -> US, 8013 -> 8014).
`sembrar.sh`, `encender.sh` y `apagar.sh` corren dentro de
`orbit-app-1` con la forma de la sonda de M.0 (el python viaja por
stdin de `docker exec -i`; el token se lee ahi de
`$ORBIT_SECRETS_DIR/api_write_token` y jamas se imprime ni sale del
servidor); en simulacion los POST van a `ORBIT_SIM_API` con
`ORBIT_SIM_TOKEN`. `sonda.sh` corre `tools/precio_sonda.py` dentro
de `orbit-app-1` (`docker exec`, con `ORBIT_DSN_DECIDE` de ahi); en
simulacion corre local con mercado de archivo. Los cuatro simulan
por omision y escriben solo con `--acepto-mutacion-real`.

## Decisiones con razon

1. El go literal de la fila LO FIJA EL PLAN (`plan repricing-02
   X.3`, plan:154): `sonda.sh` lo escribe y no lo pide por bandera
   (igual que X.2); `encender.sh` sigue pidiendo `--go`. El
   literal de la sonda nunca se imprime (verificado: ausente de
   `sonda-simulacro.txt` y `sonda-acepto-sim.txt`).
2. `sonda.sh` mueve al conejillo un centavo y lo revierte, con
   `tools/precio_sonda.py --acepto-mutacion-real --go` (guia:1042),
   y guarda el vivo del conejillo antes/despues + control
   antes/despues (ver X.2).
3. `encender.sh` sube el global a live SOLO si aun no esta en live.
   `apagar.sh` regresa universo + goals a shadow sin tocar el
   global (ver X.2).
4. `encender.sh` FRENA sin goals shadow y si
   `ads_bid_politica_amazon_us` trae un valor distinto de ausente o
   `niveles_v3` (verificado ANTES de todo, tambien en simulacro).
   En la sim la llave esta ausente.
5. C-W2: la seleccion es US FBM. En US la FBA queda fuera POR
   CONSTRUCCION: (`amazon_us`, `fba`) no esta en el registro de
   universos (`universo_no_soportado`) y ningun escenario FBA llega
   a `disponible`. La testigo 14 (FBA con oferta) entra a la
   seleccion como sin-canal y el plan la bloquea (`sin_escenario`);
   nunca se siembra ni se voltea. En X.2 la testigo equivalente
   queda fuera por el filtro de canal; aqui por el plan: los dos
   caminos frenan antes de escribir.

## Salidas guardadas (todas con su linea exit= al final)

Base `orbit_sim_X3_98032`: 100 % SINTETICA (C-W6, repo publico:
nada copiado de prod). Esquema = ORDEN59 de los tests + 0055 (el
full-forward exige datos prod en la 0011) + semilla propia:
settings banda 10-60 (valores de los tests), 4 listings falsos
`amazon_us` con ids 11-14, escenarios+oferta+fees sinteticos
(cuenta de los tests: C=40 MXN, P=29.99 USD, F=6.6 USD, muestra
170 MXN, FX 17.0 de hoy). App local (`uvicorn`, puerto 8014). La
base se borro al terminar (verificado: 0 bases `orbit_%` locales).

Semilla (ids falsos, escenario de hoy):

- 11, 12 (FBM, m=34.7325 %): se siembran en el acepto.
- 13 (sin escenario): demo de bloqueo (BLOQUEO + OMITIDA).
- 14 (FBA con oferta, escenario `incompleta` por
  `universo_no_soportado`): C-W2 exige que nadie la toque (el plan
  la bloquea; sigue `sin-goal` en todas las colas de estado).

- `sembrar-simulacro.txt` (exit=0): por omision no postea nada;
  seleccion `[11,12,13,14]` (13 y 14 entran sin canal; el plan los
  bloquea) + settings previo.
- `sembrar-bloqueo-sim.txt` (exit=0): unidad 13 explicita: 1 fila,
  1 `BLOQUEO ... sin_escenario`, `sembrados=0`, 1 `OMITIDA`.
  Universo en shadow (config 5).
- `sembrar-acepto-sim.txt` (exit=0) + `plan-simulacion.json`: plan
  de 4 filas guardado byte-igual, `sembrados=2 cerrados=0` + 2
  omitidas (13 y 14). Huella `7a0308a2d28d5c4a`.
- `sonda-simulacro.txt` (exit=0, base `orbit_sim_X3r3` + mercado
  sim `orbit-sonda-X3.json`): dry-run del conejillo 11 (`[plan]
  ... 29.99 -> 30.00 -> 29.99`, cero PATCH) + control 12 (50.00
  USD). Sin sku ni go literal en la salida.
- `sonda-acepto-sim.txt` (exit=0, misma base): ida aceptada a
  30.00 y vuelta aceptada a 29.99; vivo intacto y control 50.00
  intacto. Sin sku ni go literal.
- `encender-simulacro.txt` (exit=0): settings + 2 goals, sin
  postear.
- `encender-acepto-sim.txt` (exit=0): settings `ausente -> live`
  (global) y `shadow -> live` (universo, config 6) y 2 goals a
  live (`sembrados=2 cerrados=2`, huella `e8fe96fe8f846db7`).
- `apagar-acepto-sim.txt` (exit=0): universo `live -> shadow`
  (config 7; global intacto en live) y 2 goals a shadow.
  Round-trip completo.

## R05d: filtro sin oferta viva (2026-10-16)

Igual que X.1: `sembrar.sh` excluye y lista `EXCLUIDA`, `leer.sh`
(nuevo) mide el paso 2 con el filtro en el denominador (solo lee,
sale 0 al medir, veredicto en `LECTURA:`). En FBM puro no hay filas
de inventario y el filtro no muerde; donde hay fila (SKUs con pasado
FBA) si. Simulacro de siembra de hoy (solo lectura): 176 candidatas,
56 excluidas, 120 a postear (salida completa fuera del repo).
`leer.sh`: FRENADO sin lote (universo sin sembrar), exit=1.

## R05d-r1: regla FBM del filtro (2026-10-16)

Mismo cambio que X.2 (VEREDICTO-R05d-r1, AVISO PARA EL OPERADOR): en
FBM basta SIN PRECIO PROPIO OBSERVADO para `EXCLUIDA` (el inventario
FBA no existe para FBM y ya no se pide); FBA (X.1) y MeLi (X.4) siguen
con "inventario 0 Y sin precio". Detalle y prueba discriminante en la
seccion R05d-r1 de `X.2/evidencia.md` y en
`tests/test_precio02_escenario_fbm.py`.
