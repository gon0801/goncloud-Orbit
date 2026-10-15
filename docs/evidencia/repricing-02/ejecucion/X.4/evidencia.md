# X.4: evidencia de los scripts (REPRICING 02, seccion 5)

Scripts: `sembrar.sh`, `sonda.sh`, `encender.sh`, `apagar.sh`
(derivados de X.3: plataforma `meli`, universo `meli/meli`, settings
por `/api/ads-optimizer/settings/amazon_mx`, sin interruptor de
bids). `sembrar.sh`, `encender.sh` y `apagar.sh` corren dentro de
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
   X.4`, plan:155): `sonda.sh` lo escribe y no lo pide por bandera
   (igual que X.2/X.3); `encender.sh` sigue pidiendo `--go`. El
   literal de la sonda nunca se imprime (verificado: ausente de
   `sonda-simulacro.txt` y `sonda-acepto-sim.txt`).
2. `sonda.sh` mueve al conejillo un centavo y lo revierte, con
   `tools/precio_sonda.py --acepto-mutacion-real --go` (guia:1044),
   y guarda el vivo del conejillo antes/despues + control
   antes/despues. Es la UNICA que escribe con la forma sin sellar;
   si pasa, un PR cambia solo `FORMA_ESCRITURA_MELI` y D.4 se
   redespliega antes de `encender.sh` (tres intentos maximo).
3. `encender.sh` sube el global a live SOLO si aun no esta en live.
   `apagar.sh` regresa universo + goals a shadow sin tocar el
   global. Sin goals shadow que pasar, `encender.sh` FRENA.
4. MeLi NO tiene interruptor de bids (el ciclo de bids es solo
   Amazon): `encender.sh` no verifica ninguna llave de bids.
5. MeLi tiene un solo canal: la seleccion es toda la plataforma
   (sin escenario entra y el plan lo bloquea, nunca se siembra).
6. La semilla usa la politica meli COMPLETA (las 7 llaves que exige
   `_validar_politica`, forma de la FBA real verificada en prod):
   con la forma corta los escenarios salen `incompleta` por
   `politica_invalida` y nada se siembra (visto en la primera
   siembra, corregido re-sembrando).

## Salidas guardadas (todas con su linea exit= al final)

Base `orbit_sim_X4`: 100 % SINTETICA (repo publico: nada copiado de
prod). Esquema = ORDEN59 de los tests + 0055 + 0073 + semilla propia:
settings motor (banda 10-60, SIN modos: los ponen los scripts), FBA
sintetica + `repricing02_meli` completa, 3 listings falsos `meli`
(ids 11-13), escenarios `disponible` de hoy (cuenta de M.3:
P=979.11, m=23.9354 %). App local (`uvicorn`, puerto 8016). La base
se borro al terminar (verificado: 0 bases `orbit_sim_*` locales).

Semilla (escenarios de hoy):

- 11 (multi-miembro, m=23.9354 %), 12 (m=23.9354 %): se siembran.
- 13 (sin escenario): demo de bloqueo (BLOQUEO + OMITIDA).

- `sembrar-simulacro.txt` (exit=0): por omision no postea nada;
  seleccion `[11,12,13]` + settings previo.
- `sembrar-bloqueo-sim.txt` (exit=0): unidad 13 explicita: 1 fila,
  1 `BLOQUEO ... sin_escenario`, `sembrados=0`, 1 `OMITIDA`.
  Universo en shadow (config 5).
- `sembrar-acepto-sim.txt` (exit=0) + `plan-simulacion.json`: plan
  de 3 filas guardado byte-igual, `sembrados=2 cerrados=0` + 1
  omitida (13). Huella `cee6808fcb22a07d`.
- `sonda-simulacro.txt` (exit=0, mercado sim `orbit-sonda-X4.json`):
  dry-run del conejillo 11 (`[plan] ... 979.11 -> 979.12 ->
  979.11`, cero PUT) + control 12 (500.00 MXN). Sin go literal.
- `sonda-acepto-sim.txt` (exit=0, misma base): ida aceptada a
  979.12 y vuelta aceptada a 979.11; vivo intacto y control 500.00
  intacto. Sin go literal.
- `encender-simulacro.txt` (exit=0): settings + 2 goals, sin
  postear.
- `encender-acepto-sim.txt` (exit=0): settings `ausente -> live`
  (global) y `shadow -> live` (universo, config 6) y 2 goals a
  live (`sembrados=2 cerrados=2`, huella `a8c62f8ebf41ef47`).
- `apagar-simulacro.txt` (exit=0): tras el acepto, sin goals live:
  solo el universo, sin postear.
- `apagar-acepto-sim.txt` (exit=0): universo `live -> shadow`
  (config 7; global intacto en live) y 2 goals a shadow.
  Round-trip completo.
