# D.2: evidencia del paquete (REPRICING 02, seccion 3)

Paquete: `desplegar.sh`, `rollback.sh`, `checklist.sh`, `ensayo.sh`.
Lleva S.1 a S.6, el carril G, la 0058 y (desviacion 1) la 0059.
Rama `rp02/sec3-seguridad-goals` sobre G.3 (`08e47ec`).

## Estado de produccion leido el dia del paquete (solo lectura)

- 0058: NO aplicada (`count(*) = 0` en los dos CHECK).
- 0059: NO aplicada (el cuerpo de `precio_retencion_coherente()` es el
  roto de la 0054: `LIKE '%v_corr_fecha%' = f`).
- `precio_modo_global = shadow` en la config vigente.
- Cron de gon, linea 54 (la baja de D.2, caracter por caracter):

  `10 13 * * * /usr/bin/flock -n /tmp/precio-corrida.lock docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1`

## Desviaciones con razon

1. D.2 aplica la 0059 ANTES de la 0058. La guia dice que D.2 lleva
   solo la 0058, pero produccion no trae la 0059 (R03k, PR #453) y el
   codigo de S.2+ exige el trigger reparado (retener sin el levanta
   error de Postgres, no retiene). El orden 0059->0058 lo siguen
   `ensayo.sh` y `desplegar.sh`; `rollback.sh` revierte en inverso.
   Si al desplegar la 0059 ya esta aplicada, `desplegar.sh` la salta
   sola (`aplicada` da t) y aplica solo la 0058: el mismo paquete
   sirve en los dos estados.
2. `precio --reporte` en 7v antes de instalar el cron. La guia no lo
   pide; D.4 corre su comando una vez con la imagen nueva antes de
   instalar su linea y D.2 hace lo mismo con el comando que sus
   lineas invocan (`--reporte` es solo lectura: sin decisiones
   imprime vacio y sale 0).

## Salidas guardadas (todas con su linea exit= al final)

- `ensayo-salida.txt` (exit=0): `ensayo.sh` corrido de verdad contra
  el esquema de prod: aplica 0059+0058 (el candado propio de la 0059
  corrio adentro: 1 positivo + 4 rechazos, 0 filas zz), senales
  2/t, reversas en inverso con esquema EXACTO al base sin filtros
  (la reversa de la 0059 restaura byte-exacto el cuerpo de la 0054,
  verificado en el paquete) y reaplicacion identica.
- `desplegar-simulacion.txt` (exit=0): `desplegar.sh HEAD` con
  `ORBIT_SIMULACION=1` contra `orbit_sim_D2` (esquema de prod +
  `config_version` real): 0059+0058 pendientes y aplicadas, md5 OK,
  reporte 0, cron reemplazado (1 baja + 2 altas) y verificado.
- `checklist-simulacion.txt` (exit=0): `checklist.sh` en simulacion
  contra app local (`uvicorn`, puerto 8012) sobre `orbit_sim_D2`:
  0 fallas, 0 sin medir (health, CHECK, trigger, modos en shadow,
  /precios con modos + seguridad + goals, cron sin vieja y con 2,
  resumen sellado).
- `rollback-simulacion.txt` (exit=0): `rollback.sh` en simulacion:
  2 lineas fuera, vieja restaurada, reversas aplicadas,
  `checks_0058=0 trigger_reparado=f`.
- `guard-cron-falla-salida.txt` (exit=1): prueba del guard NUEVO de
  7b. Con `ORBIT_D2_SIM_FALLA_CRON=1` (solo simulacion) un escritor
  ajeno mete una linea entre la instalacion y la verificacion: el
  guard ve 3 altas en vez de 2, RESTAURA el respaldo y aborta. La
  verificacion posterior (`diff` respaldo vs crontab) confirma que
  el crontab quedo igual al respaldo (`RESTAURADO-OK`).

Las bases `orbit_sim_D2` y `orbit_sim_D2_guard` se borraron al
terminar cada corrida (verificado: 0 bases `orbit_%` locales).

## Notas

- La guarda `solo_previsto` de D.2 permite EXACTAMENTE 1 baja (la
  vieja) + 2 altas (las de `precio-spapi.lock` del `DEPLOY.md` del
  SHA). Otra cosa no se instala (1c) o se restaura y aborta (7b).
- `ORBIT_D2_SIM_FALLA_CRON` solo existe en simulacion y solo sirve
  para la prueba del guard; en modo real no hace nada.
- Sin `mutantes.md`: no se agregaron pruebas nuevas (carril
  `tdd:skip:ops` del plan). La prueba del guard es una corrida de
  simulacion con falla sembrada, guardada arriba: no es un test
  commiteado con logica que mutar, asi que no se inventan mutantes.
- Que "el resumen diario llega" (DoD de D.2) se verifica al dia
  siguiente del despliegue; ver `checklist-antes-del-deploy.txt`.
