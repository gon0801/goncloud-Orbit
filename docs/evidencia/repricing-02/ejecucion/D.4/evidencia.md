# D.4: evidencia del paquete (REPRICING 02, seccion 5)

Paquete: `desplegar.sh` (corre `politicas.sh` en el paso 4b),
`politicas.sh`, `rollback.sh`, `checklist.sh` y `ensayo.sh`. Lleva el
carril M.1-M.4, la 0073, la politica `meli/meli`, el catalogo diario,
el job de muestras meli y MeLi en el cron (4 lineas, lock propio).
Rama `rp02/sec5-meli`. Test de las lineas:
`tests/test_precio_corrida.py::test_linea_crontab_meli_en_deploy`.

## Estado de produccion leido el dia del paquete (solo lectura)

Ver `checklist-antes-del-deploy.txt`:

- 0073: NO aplicada (`count(*) = 0` en la rama meli).
- Cron de gon, lineas 54-57 (las 4 de D.3; D.4 no las toca).
- Politicas meli vigentes: 0; FBA vigente: 1 (fuente de la copia,
  con las 7 llaves que exige `_validar_politica`, verificadas).
- `precio_modo_global = shadow`, `{"amazon_mx/fba": "shadow"}`.
- Muestras meli: 0 filas.

## Decisiones con razon

1. `ensayo.sh` SI aplica: el encargo lo pedia solo si M usaba la
   0056, pero M uso la 0073; la guia ("Despliegues": `ensayo.sh` si
   hay migracion) manda y la 0073 lo es.
2. `solo_previsto` exige 0 bajas + 4 altas exactas (D.4 solo agrega).
   `agregar_cron` aborta si el crontab trae 1-3 de las 4 (a medias).
3. El preflight, las guardas y la reversa cuentan `app.cli`,
   `app.envio_muestras` Y `app.meli.catalogo`.
4. `7v` corre `precio --reporte` de meli; si no sale 0, el cron queda
   sin instalar.
5. La guarda 1b de la forma tiene dos modos (U1 del panel): con la
   0073 pendiente (deploy inicial) EXIGE `pendiente_sonda` (si
   llegara sellada, la primera corrida escribiria sin sonda); con la
   0073 aplicada (redespliegue) acepta cualquiera y la anuncia: es
   el reintento o el PR del sello de X.4 (que cambia SOLO la
   constante, sin tocar guardas).
6. `politicas.sh` va APARTE (el plan lo pide) y `desplegar.sh` lo
   corre en 4b; idempotente por label (dos corridas no duplican).
7. `docs/DEPLOY.md`: M.4 escribio horarios/log distintos del plan
   (12:35/12:40, `precio-corrida.log`); este paquete los deja
   palabra por palabra del plan (11:30, 12:25, 13:10 + repasos,
   `precio-meli.log`) y actualiza la tabla UTC + el parrafo que decia
   que meli no se agendaba. El test pinza las 4.
8. El checklist sale 3 hasta la primera corrida del job de muestras
   y de la estimacion (muestras meli + escenarios meli de hoy), 0
   despues. La seccion 7 (R05-r3 B2) compara las activas del
   catalogo contra las activas vistas por la corrida
   `meli_catalogo` terminada de hoy: 0 si cuadran, FALLA si
   difieren, NO MEDIDO sin corrida (las sin miembros mapeados se
   cuentan aparte, no fallan). DoD de D.4 medido por el checklist;
   nadie le pide nada al dueno.
9. La reversa NO restaura el respaldo completo del crontab (E7: el
   actual manda), NO restaura viejas (D.4 no quito ninguna) y NO toca
   la politica meli (datos, no esquema; el codigo viejo la ignora).
10. Sim: `TMPDIR` varia entre shells; el estado sim vive en
    `${TMPDIR:-/tmp}/orbit-d4-sim` del shell que corre (ver rutas en
    cada salida). Entre las pruebas U1 (commit sellado colgante) y
    las sims principales se borro ese estado (`rm -rf`): por eso
    `redespliegue-sellado-simulacion.txt` re-siembra el crontab en
    el mismo minuto que `desplegar-simulacion.txt` (GLM-7).

## Salidas guardadas (todas con su linea exit= al final)

Base 100 % SINTETICA: `orbit_sim_D4` = ORDEN59 de los tests + 0055
(como prod: SIN 0073) + `config_version` (modos a shadow) + 1
politica FBA sintetica. App local (`uvicorn`, puerto 8015). La base
se borro al terminar. DSN enmascarado a `orbit:***@` en scripts y
salidas (la conexion usa el real). Nota fiel: la FBA sintetica de
esta sim nacio con 4 llaves (las 2 de validez fiscal las trae la FBA
real, verificadas arriba; la copia `settings || overrides` las
preserva); la semilla en `/tmp` ya quedo con la forma completa.

- `ensayo-salida.txt` (exit=0): `ensayo.sh` contra el esquema de prod
  (SOLO esquema, cero filas): aplica 0073 (senal 1), reversa con
  esquema EXACTO al base y reaplicacion identica.
- `desplegar-simulacion.txt` (exit=0): `desplegar.sh HEAD` en sim:
  guardas 1b (forma sin sellar OK), 0073 pendiente y aplicada,
  `politicas.sh` con 1 politica meli (fuente FBA = 1), md5 OK,
  reporte meli 0, cron agregado (0 bajas + 4 altas) y verificado por
  la ruta 7b.
- `desplegar-simulacion-reintento.txt` (exit=0): segunda corrida:
  `ya aplicada`, `politicas.sh` sin duplicar (1 politica), `cron ya
  instalado`, aplicadas/predeploy conservados, `nada que instalar
  en 7b`. Idempotencia real.
- `checklist-simulacion-antes.txt` (exit=3, regrabado r3): 2 FALLAS
  exactas (las de hoy, solo seccion 6) + cobertura NO MEDIDA (sin
  corrida de hoy), antes de sembrar corridas.
- `checklist-simulacion.txt` (exit=0, regrabado r3): tras sembrar
  muestra + escenario disponible meli de hoy (con
  `session_replication_role = replica`: la coherencia referencial
  no es lo que se prueba; el escenario trae fees partidos,
  `logistica_origen = 'politica'` y refs sinteticas 1) + corrida
  `meli_catalogo` de hoy con 2 obs activas (MLM1 con 2 miembros,
  MLM2 raiz): 0 fallas, 0 sin medir, cobertura
  `api_activas=2 catalogo=2 [CUADRA]` (la corrida es id 2: la 0054
  consume el 1 en su autonegacion y lo borra).
- `checklist-simulacion-cobertura-falla.txt` (exit=1, nuevo r3):
  sobre la base del `despues` + MLM9 rancia (obs `active` de ayer,
  ausente de la corrida de hoy): 1 FALLA exacta (seccion 7,
  `sobran_en_catalogo: MLM9`), 0 sin medir. Asi se ve la
  cobertura cuando no cuadra.
- `rollback-simulacion.txt` (exit=0): 4 lineas fuera (crontab de
  vuelta a 5), reversa aplicada, `senal_0073=0`, politica intacta.
- `guard-cron-falla-salida.txt` (exit=1): con
  `ORBIT_D4_SIM_FALLA_CRON=1` un escritor ajeno mete una linea tras
  instalar: el guard ve 5 altas en vez de 4, RESTAURA el respaldo y
  aborta (`diff` respaldo vs crontab: iguales, RESTAURADO-OK).
- `guard-forma-inicial-salida.txt` (exit=1, U1): `desplegar.sh` con
  un SHA sellado (commit colgante de prueba, solo cambia la
  constante) sobre base SIN 0073: aborta en 1b ANTES de tocar nada.
- `redespliegue-sellado-simulacion.txt` (exit=0, U1): el mismo SHA
  sellado sobre base CON 0073: pasa 1b como redespliegue y termina
  LISTO (asi correra el PR del sello de X.4).
