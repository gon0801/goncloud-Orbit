# D.7: scripts del despliegue de la seccion 9

Los cuatro scripts parten de los de D.6 ya probados en produccion y
les quitan las migraciones: este bloque solo mueve codigo (apply,
pantalla_dinero, campana_config, api_dashboard, rejuega_niveles).

## Que trae cada uno

- `ensayo.sh`: corre el ciclo completo en simulacion contra una
  base desechable (desplegar y rollback) y exige el esquema exacto
  de partida tras los dos: el despliegue no deja huella en la base.
  El modo real esta bloqueado a proposito: sin migraciones no hay
  nada que ensayar contra produccion que la simulacion no cubra.
- `desplegar.sh`: codigo del SHA aprobado con md5 por archivo de la
  seccion. La guarda de migraciones no aplica ninguna: aborta si
  alguna `_bids02_` del SHA no esta aplicada (la aplica el D que la
  trajo). Vigia de proceso y de ciclos antes de recrear; el crontab
  no se instala: se imprime su huella antes y despues y se aborta si
  cambia (R13-E). El aplicadas del sello queda vacio a proposito.
- `checklist.sh`: las comprobaciones heredadas que no dependen de
  migraciones, mas el JSON de busquedas por mercado (moneda y
  plataforma, R13-E) con cada gasto en dos decimales (R13-K), y los
  conteos de campana_ajuste y ads_campana_config_observation iguales
  antes y despues (el checklist no escribe).
- `rollback.sh`: restaura el codigo de `predeploy-<sello>`. Aborta
  si el aplicadas del sello trae alguna migracion: ese sello no es
  de un despliegue D.7. El DSN se lee del `.env` del servidor sin
  depender de que `orbit-app-1` este arriba (R13-L).

Las salidas de las corridas (esta carpeta, `*-sim.txt`) se guardan
con su linea `exit=` al final (R13-M); lo mismo haran las de la
corrida real post-merge.

## Simulacion contra la base local

Requisitos previos: `psql` y `pg_dump` en el PATH (en esta maquina
salen de `/opt/homebrew/bin`) y el Postgres local del puerto 5433
arriba. Bases: `orbit_copia_d7` (esquema completo vigente, con la
0071 y la 0072 aplicadas) y `orbit_sim_d7` (clon para el despliegue),
con una fila de busquedas sembrada para que el gasto de dos
decimales se mida. El checklist pide la app por HTTP, asi que antes
del tercer comando se levanta la app local contra `orbit_sim_d7` en
el puerto 8012 y se deja corriendo hasta terminar la reversa:

```bash
export PATH="/opt/homebrew/bin:$PATH"
ORBIT_DSN_READ=postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d7 \
  ORBIT_DSN_ADMIN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d7 \
  ORBIT_SECRETS_DIR=$(mktemp -d) \
  .venv/bin/python -m uvicorn app.main:app --port 8012
```

Comandos, desde la raiz del repo (el SHA es el HEAD ya mergeado y el
STAMP lo imprime desplegar.sh al final de su salida):

```bash
ORBIT_SIMULACION=1 ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_d7 \
  bash docs/evidencia/bids-02/ejecucion/D.7/ensayo.sh $(git rev-parse HEAD)
ORBIT_SIMULACION=1 ORBIT_SIM_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d7 \
  bash docs/evidencia/bids-02/ejecucion/D.7/desplegar.sh $(git rev-parse HEAD)
ORBIT_SIMULACION=1 ORBIT_SIM_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d7 \
  ORBIT_SIM_API=http://127.0.0.1:8012 bash docs/evidencia/bids-02/ejecucion/D.7/checklist.sh $(grep -o 'STAMP=[0-9-]*' docs/evidencia/bids-02/ejecucion/D.7/desplegar-sim.txt | tail -1 | cut -d= -f2)
ORBIT_SIMULACION=1 ORBIT_SIM_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d7 \
  bash docs/evidencia/bids-02/ejecucion/D.7/rollback.sh $(grep -o 'STAMP=[0-9-]*' docs/evidencia/bids-02/ejecucion/D.7/desplegar-sim.txt | tail -1 | cut -d= -f2)
```

Resultados guardados en esta carpeta: ensayo `ENSAYO OK` (exit=0,
el esquema queda identico tras desplegar y tras la reversa);
desplegar exit=0 (crontab intacto; en simulacion el md5 solo
verifica que la extraccion del respaldo quedo integra, la
comparacion contra el codigo del servidor corre en la corrida real);
checklist con 0 fallas y 1 sin medir (exit=4: US no trae filas con
gasto en la base de simulacion, asi que su formato de gasto no se
mide; la moneda y la plataforma de los dos mercados si); rollback
exit=0 con el crontab intacto.

La primera corrida del ensayo aborto en la guarda de migraciones:
la base de partida aun no traia la 0071 ni la 0072, y D.7 no las
aplica. Se aplicaron a las dos bases de simulacion (son las que ya
estan en produccion desde D.6) y la corrida siguiente dio OK.
