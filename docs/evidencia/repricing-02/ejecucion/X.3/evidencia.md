# X.3: evidencia de los scripts (REPRICING 02, seccion 4)

Scripts: `sembrar.sh`, `sonda.sh`, `encender.sh`, `apagar.sh`
(derivados de X.2 por `sed`: MX -> US, 8013 -> 8014; misma forma
M.0, mismas banderas). Los cuatro simulan por omision y escriben
solo con `--acepto-mutacion-real`; corren dentro de `orbit-app-1`
con la forma de la sonda de M.0 (el python viaja por stdin de
`docker exec -i`; el token se lee ahi de `$ORBIT_SECRETS_DIR/
api_write_token` y jamas se imprime ni sale del servidor). En
simulacion los POST van a `ORBIT_SIM_API` con `ORBIT_SIM_TOKEN`.

## Decisiones con razon

1. `--go` obligatorio en `encender.sh` y en `sonda.sh` (igual que
   X.2: el literal es del dueno, viaja en base64 de una linea y
   nunca se imprime; verificado ausente de todas las salidas).
2. `sonda.sh` voltea un goal shadow de conejillo a live y de vuelta
   a shadow en la misma corrida (reversa incluida; ver X.2).
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
- `sonda-simulacro.txt` (exit=0): ida + vuelta sobre el conejillo
  11, sin postear.
- `sonda-acepto-sim.txt` (exit=0): ida a live (el go abre) y
  vuelta a shadow (la reversa cierra); cola intacta.
- `encender-simulacro.txt` (exit=0): settings + 2 goals, sin
  postear.
- `encender-acepto-sim.txt` (exit=0): settings `ausente -> live`
  (global) y `shadow -> live` (universo, config 6) y 2 goals a
  live (`sembrados=2 cerrados=2`, huella `e8fe96fe8f846db7`).
- `apagar-acepto-sim.txt` (exit=0): universo `live -> shadow`
  (config 7; global intacto en live) y 2 goals a shadow.
  Round-trip completo.
