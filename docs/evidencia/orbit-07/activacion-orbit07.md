# ORBIT 07: activacion y reversa de las mejoras

Receta preparada para el cierre autorizado por el usuario: integrar, desplegar
y activar las mejoras, conservando los modos de pausa y limites del dueno.
El resultado de su ejecucion se registra en la PR #547; este documento describe
el procedimiento y por si solo no acredita que se haya ejecutado.

## Precondiciones

1. El SHA desplegado contiene el codigo y la migracion
   `0081_orbit07_proteccion_venta.sql`; `desplegar.sh` y `checklist.sh`
   salieron verdes para ese SHA.
2. `config_version` tiene una fila vigente legible por `app_admin`.
3. No se toca `ads_producto_pausa_<plat>` ni sus limites
   (`ads_producto_pausa_clics_<plat>`, `ads_producto_pausa_gasto_<plat>`).
4. No se enciende `ads_producto_meta_<plat>`: la activacion conserva el valor
   vigente. Si la meta no estaba en `live`, M2 queda inerte hasta que el dueno
   la encienda desde su pantalla.

## Valores exactos (verificados en el codigo)

| Mejora | Clave | Valor que enciende | Reversa |
|---|---|---|---|
| M1, proteccion por venta reciente | `ads_proteccion_venta` (global, sin sufijo de plataforma) | `proteccion_venta_v1` | quitar la clave (`off`) |
| M2, economia directa de producto | `ads_producto_economia_<plat>` | `producto_directo_v1` | `auto03` o quitar la clave |
| M5/M6, economia del ultimo cambio | `ads_bid_economia_<plat>` | `economia_cambio_v2` | quitar la clave (`off`) |
| M4, pausa por venta reciente del producto | politica `v3` (default del codigo) | sin clave | no aplica |

`ads_proteccion_venta` es global: `app/optimizer/proteccion.py` la lee sin
sufijo de plataforma. No existe `ads_proteccion_venta_amazon_us` ni
`..._amazon_mx` en este codigo; escribirlas no encenderia nada.

## Activacion

Cada llamada crea una `config_version` nueva (append-only) copiando la vigente
entera y cambiando solo las claves pedidas. El escritor es
`config_write.guarda_config` via `app.cli productos-modo`: toma un lock de
aplicacion, exige que la base leida siga siendo la vigente y devuelve
`ConfigObsoleta` (sin escribir) si otra persona guardo antes. Para reintentar,
repetir el comando: vuelve a leer la config vigente.

```bash
# US: enciende M2 y M5/M6 en esa plataforma y M1 (global) una sola vez.
docker exec orbit-app-1 python -m app.cli productos-modo --platform amazon_us \
  --economia producto_directo_v1 \
  --bid-economia economia_cambio_v2 \
  --proteccion proteccion_venta_v1

# MX: enciende M2 y M5/M6; M1 ya quedo global con la llamada anterior.
docker exec orbit-app-1 python -m app.cli productos-modo --platform amazon_mx \
  --economia producto_directo_v1 \
  --bid-economia economia_cambio_v2
```

`economia_cambio_v1` sigue siendo un valor valido para las decisiones que ya
se juzgaron con esa version: el replay lee el episodio congelado y no lo
recalcula. Solo los episodios nuevos usan la version que diga el setting.

M4 no lleva comando: es la politica `v3` que corre por defecto. No tiene un
interruptor propio: revertirla requiere un nuevo commit que restaure la politica
anterior, con revision, CI y despliegue. La reversa de M1/M2/M5/M6 de abajo no
revierte M4. Se conserva la tabla 0081 y su historial si ya contiene episodios.

## Reversa

Cada comando crea otra `config_version`, conservando el resto de los settings y
sin borrar decisiones, evaluaciones ni episodios.

```bash
docker exec orbit-app-1 python -m app.cli productos-modo --platform amazon_us \
  --economia off \
  --bid-economia off \
  --proteccion off

docker exec orbit-app-1 python -m app.cli productos-modo --platform amazon_mx \
  --economia off \
  --bid-economia off
```

`off` quita la clave; no se escribe el literal `off` (el motor lo rechazaria
como config corrupta). La historia queda intacta: cada decision conserva su
`inputs.caso` congelado y el replay la reproduce.

Si ademas se revierte el codigo con `rollback.sh`, ejecutar primero los dos
comandos de apagado anteriores, mientras el CLI nuevo sigue instalado. El CLI
anterior no reconoce esas opciones y `rollback.sh` solo restaura las claves
de meta y pausa de su diario. Sin ese apagado, las claves ORBIT 07 seguirian
guardadas y un despliegue posterior de este codigo volveria a activarlas.
Antes de empezar, confirma que la version desplegada ya incluye
`descarta_esperas_proteccion` (el codigo de este bloque); con el codigo
anterior apagar no descarta nada y las esperas se aplicarian de golpe. Deten
la creacion de ciclos (cron) y espera a que termine el ciclo activo antes del
apagado, para que no quede una decision a medio camino.

Apagar la proteccion con el CLI nuevo descarta, en la misma transaccion, las
pausas `released` que estaban esperando por venta reciente: quedan `discarded`
con `discard_motivo = 'proteccion_apagada'`, sin cobrar cuota ni mandar HTTP.
El ciclo siguiente las vuelve a proponer con su veto de 48 h si siguen
calificando. Por eso ese apagado va antes de `rollback.sh`: el codigo anterior
no las descarta y las aplicaria de golpe. Si la configuracion ya estaba
apagada, el comando informa `sin cambios` y no descarta; en ese caso verifica
que no quede ninguna fila `released` con su `espera_proteccion` en
`decision_sin_aplicar`, o deja correr un ciclo con la proteccion apagada (que
hace ese mismo descarte) antes del `rollback.sh`.

Si M1 ya abrio episodios, la reversa de 0081 aborta para conservarlos.
`rollback.sh` restaura primero el codigo y el SHA anterior, pero deja
`rollback_incompleto` (salida 2) por la migracion conservada. Comprobar salud,
codigo y configuracion antes de declararlo operativo; no borrar episodios
para forzar el cierre del diario. Para desactivar M1/M2/M5/M6 normalmente
bastan los comandos anteriores, conservando el codigo que lee su historia.

## Comprobacion

```sql
SELECT id, label,
       settings ->> 'ads_proteccion_venta' AS proteccion,
       settings ->> 'ads_producto_economia_amazon_us' AS economia_us,
       settings ->> 'ads_producto_economia_amazon_mx' AS economia_mx,
       settings ->> 'ads_bid_economia_amazon_us' AS bid_economia_us,
       settings ->> 'ads_bid_economia_amazon_mx' AS bid_economia_mx,
       settings ->> 'ads_producto_pausa_amazon_us' AS pausa_us,
       settings ->> 'ads_producto_pausa_amazon_mx' AS pausa_mx
  FROM config_version
 ORDER BY id DESC
 LIMIT 3;
```

En el primer ciclo despues de la activacion se revisa el envelope
(`optimizer_cycle.notes`), la cola (`apply_queue`) y la salud del motor. Ante
un defecto comprobado se corre la reversa; no se espera a completar un
experimento.
