# D.3: evidencia del paquete (REPRICING 02, seccion 4)

Paquete: `desplegar.sh`, `rollback.sh`, `checklist.sh`, `ensayo.sh` y
`politicas.sh` (este ultimo se corre tras el deploy, con DSN admin).
Lleva el carril F.1-F.4, S.7, la 0055, las politicas FBM, el job de
muestras y US en el cron. Rama `rp02/sec4-fbm-us`.

## Estado de produccion leido el dia del paquete (solo lectura)

Ver `checklist-antes-del-deploy.txt`:

- 0055: NO aplicada (`count(*) = 0` en los dos CHECK).
- Cron de gon, lineas 54-55 (las 2 bajas de D.3).
- Politicas FBM vigentes: 0; FBA vigente: 1 (fuente de la copia).
- `precio_modo_global = shadow`, `{"amazon_mx/fba": "shadow"}` (X.1 sin
  encender: lo sube el primer universo que encienda).
- Muestras: 0 filas.

## Decisiones con razon

1. `solo_previsto` exige 2 bajas + 4 altas exactas (las 2 de D.2 fuera;
   2 de precios MX+US + 2 de muestras dentro).
2. El preflight y la reversa cuentan `app.cli` Y `app.envio_muestras`.
3. `7v` corre `precio --reporte` de MX y US (las dos plataformas del
   cron); si alguno no sale 0, el cron queda sin instalar.
4. `politicas.sh` va tras el deploy (paso manual con DSN admin, no
   dentro de `desplegar.sh`); idempotente por label (dos corridas no
   duplican).
5. El checklist sale 3 hasta la primera corrida del job de muestras y
   de la estimacion (muestras MX, muestras US, escenarios FBM MX+US
   de hoy), 0 despues. La cobertura de MX y US se exige con exit 0 de
   `tools/precio_cobertura.py` por plataforma.
6. La reversa NO restaura el respaldo completo del crontab (E7: el
   actual manda) y NO toca las politicas FBM (datos, no esquema; el
   codigo viejo las ignora).
7. Sim: `TMPDIR` varia entre shells; el estado sim vive en
   `${TMPDIR:-/tmp}/orbit-d3-sim` del shell que corre (ver rutas en
   cada salida).

## Salidas guardadas (todas con su linea exit= al final)

Base 100 % SINTETICA: `orbit_sim_D3` = ORDEN59 de los tests SIN 0055
(pendiente, como en prod) + `config_version` de los tests (modos a
shadow) + 1 politica FBA sintetica. App local (`uvicorn`, puerto
8012). La base se borro al terminar. DSN enmascarado a `orbit:***@`
en scripts y salidas (la conexion usa el real).

- `ensayo-salida.txt` (exit=0): `ensayo.sh` contra el esquema de prod
  (SOLO esquema, cero filas): aplica 0055 (senal 2), reversa con
  esquema EXACTO al base sin filtros y reaplicacion identica.
- `desplegar-simulacion.txt` (exit=0): `desplegar.sh HEAD` en sim:
  0055 pendiente y aplicada, md5 OK, reporte MX+US 0, cron
  reemplazado (2 bajas + 4 altas) y verificado por la ruta 7b.
- `desplegar-simulacion-reintento.txt` (exit=0): segunda corrida:
  `ya aplicada`, `cron ya instalado`, aplicadas/predeploy
  conservados, `nada que instalar en 7b`. Idempotencia real.
- `politicas-simulacion.txt` (exit=0): `politicas.sh` dos veces en
  sim: 2 politicas FBM vigentes, 0 duplicadas.
- `checklist-simulacion-antes.txt` (exit=1): 3 FALLAS exactas (las de
  hoy) y 0 sin medir, antes de sembrar corridas.
- `checklist-simulacion.txt` (exit=0): tras sembrar muestras MX+US y
  escenarios FBM MX+US de hoy (con `session_replication_role =
  replica`: la coherencia referencial no es lo que se prueba, los
  CHECK 0055 si): 0 fallas, 0 sin medir.
- `rollback-simulacion.txt` (exit=0): 4 lineas fuera, 2 viejas
  restauradas, reversa aplicada, `checks_0055=0`.
- `guard-cron-falla-salida.txt` (exit=1): con
  `ORBIT_D3_SIM_FALLA_CRON=1` un escritor ajeno mete una linea tras
  instalar: el guard ve 5 altas en vez de 4, RESTAURA el respaldo y
  aborta (`diff` respaldo vs crontab: iguales, RESTAURADO-OK).
