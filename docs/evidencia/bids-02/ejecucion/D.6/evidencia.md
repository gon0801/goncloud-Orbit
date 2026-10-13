# D.6: scripts del despliegue del remate

Los cuatro scripts parten de los de D.4 ya probados en produccion y
suman las correcciones de la ronda de despliegue anterior (R13-E/L/M).

## Que trae cada uno

- `ensayo.sh`: aplica la 0071 y la 0072 sobre una copia del esquema,
  verifica las senales de cada fila (`permisos.sql` de R09-E y R09-J),
  aplica las reversas en orden inverso, exige el esquema identico y
  reaplica.
- `desplegar.sh`: migraciones 0071/0072 (y toda `_bids02_` pendiente),
  codigo del SHA aprobado con md5 por archivo, vigia de humo sin
  impulsos. No instala linea de cron: imprime la huella (md5) y el
  numero de lineas del crontab de `gon` antes y despues y aborta si
  cambian (R13-E).
- `checklist.sh`: las comprobaciones de D.4 conservadas, mas el GRANT
  de la 0071 y el candado de la 0072, y el JSON de la pantalla de
  busquedas por mercado (`busquedas-amazon_mx.json` y
  `busquedas-amazon_us.json` en este dir) con la moneda de su mercado
  en cada renglon (R13-E).
- `rollback.sh`: restaura el codigo de `predeploy-<sello>` y aplica
  las reversas en orden inverso. Lee el DSN del `.env` del servidor
  sin depender de que `orbit-app-1` este arriba (R13-L); el crontab
  no se toca y su huella se verifica igual.

Las salidas de las corridas (esta carpeta, `*-sim.txt`) se guardan
con su linea `exit=` al final (R13-M); lo mismo haran las de la
corrida real post-merge.

## Simulacion contra la base local

Bases: `orbit_copia_d6` (esquema completo sin la 0071 ni la 0072) y
`orbit_sim_d6` (clon para el despliegue). Comandos:

```bash
ORBIT_SIMULACION=1 ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_d6 \
  bash docs/evidencia/bids-02/ejecucion/D.6/ensayo.sh
ORBIT_SIMULACION=1 ORBIT_SIM_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d6 \
  bash docs/evidencia/bids-02/ejecucion/D.6/desplegar.sh <SHA>
ORBIT_SIMULACION=1 ORBIT_SIM_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d6 \
  ORBIT_SIM_API=http://127.0.0.1:8012 bash docs/evidencia/bids-02/ejecucion/D.6/checklist.sh <STAMP>
ORBIT_SIMULACION=1 ORBIT_SIM_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d6 \
  bash docs/evidencia/bids-02/ejecucion/D.6/rollback.sh <STAMP>
```

Resultados guardados en esta carpeta: ensayo `ENSAYO OK` (exit=0);
desplegar exit=0 (aplico la 0071 y la 0072 y las anoto para el
rollback); checklist con 0 fallas y 3 sin medir (exit=4: el candidato
del dashboard y los dos JSON de busquedas son insumos que el dueno
aporta tras el despliegue, no fallas del script); rollback exit=0 con
el candado fuera, la vista legible y la huella del crontab intacta.

## Correccion de la segunda relectura (R14-r2)

La 0071 no se detectaba aplicada con `has_table_privilege`, porque en
produccion la vista ya se lee por default privileges y el despliegue
la daba por aplicada sin correrla. La migracion ahora deja una marca
(el comentario de la vista) y la deteccion, el checklist y la reversa
usan esa marca. La reversa quita la marca y el privilegio; si la vista
deja de ser legible porque el `REVOKE` tambien quito el privilegio que
venia de los default privileges, el rollback vuelve a otorgar el
`SELECT` suelto y lo deja dicho en su salida. Las salidas de
simulacion de esta carpeta se repitieron con este codigo: el
despliegue anota la 0071 y la 0072, y el rollback reversa las dos y
termina con la vista legible tras reotorgar el `SELECT`.
