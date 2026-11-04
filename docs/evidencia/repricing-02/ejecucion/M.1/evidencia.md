# M.1: catalogo diario de Mercado Libre

Fila **M.1** de `plans/repricing-02.md`, seccion 5. `app/meli/catalogo.py`
con su `main` (`python -m app.meli.catalogo`, DSN `ORBIT_DSN_INGEST`):
por publicacion activa inserta o actualiza `listing` con el producto
ancla, sus filas de `listing_miembro` y una fila de
`meli_publicacion_observation`. El SKU sale del atributo `SELLER_SKU`
(con `include_attributes=all`, leccion M.0) y se cruza exacto contra
`product.odoo_sku`.

## Comprueba (conteos de mapeo)

Medidos por la sonda M.0 sobre las 30 publicaciones escaneadas en
produccion (`ejecucion/M.0/README.md`):

- Con todos sus miembros mapeados: 24.
- Con alguno sin mapear: 6.
- Sin ninguno mapeado: 0.

Los tres conteos salen tambien en el stdout de cada corrida
(`todos/alguno/ninguno`) y en `ingest_run.skip_reason` via los skips.

## Decisiones

- Observacion por item leido (activo o no); `listing` + miembros solo
  para `active` con al menos un miembro mapeado. Lo no activo ya
  existente se congela en identidad y da de baja sus miembros.
- El ancla es el miembro mapeado de SKU menor y se recalcula por
  corrida: es valor derivado, no identidad independiente.
- Los buckets de mapeo se cuentan sobre todas las observadas (metodo
  M.0), no solo las que entran a `listing`: diagnostico de mapeo.
- Red primero y escritura despues (patron `fetch_meli`/`sync_meli`):
  la transaccion de escritura es corta, con savepoint por item.
- Falla honesto tras 5 fallos seguidos (como `fetch_meli`) y sale 1
  si el scan trae items pero no queda ninguna observacion.
- Reconciliacion: `listing` meli ausente del scan da de baja sus
  miembros (la fila se conserva: cuelga historial). Se salta con scan
  truncado. Si el scan solo trae activas (defecto probable de la API,
  sin verificar en construccion), esta es la via que da de baja los
  cierres; si trae todas, los da de baja la observacion `closed`.
  Ambas ramas quedan cubiertas.
- Cada corrida abre y sella `ingest_run` (`source = 'meli_catalogo'`),
  y cada observacion cita su `run_id`.
- Visitas (`time_window` de 1 dia) solo para activas, fail-soft a
  NULL contado. Precio de la raiz (M.0: repite el de las variantes),
  stock suma de variantes o raiz, NULL si falta un dato.

## Pruebas

`tests/test_precio02_meli_catalogo.py`, 20 pruebas, cliente falso con
la forma de la sonda M.0. Las 4 del DoD mas: `include_attributes=all`,
idempotencia con contadores honestos (`nuevos/actualizados`),
desactivacion de variacion, ancla con no-mapeado primero, visitas
caidas, skip por item con run sellado, breaker honesto, exit 1 con
todo fallado, truncado sin reconciliacion, ausencia reconciliada,
transicion activo→cerrado, error de escritura aislado y vendedor sin
id. Mutantes en `mutantes.md` (20/20 en rojo). Panel en `panel.md`.
