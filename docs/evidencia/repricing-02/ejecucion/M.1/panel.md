# Panel M.1: veredicto del lead

## Intencion

> M.1 ingiere el catalogo de Mercado Libre a diario: por publicacion
> activa, una fila en `listing` con el producto ancla, sus miembros
> cruzando `SELLER_SKU` contra `product.odoo_sku` y una observacion;
> sin mapeo no hay fila; cerrada no es activa; visitas fail-soft.

## Revisores

- Muse A (poteto-agent, workflow): 12 hallazgos.
- Muse B (poteto-agent, workflow): 12 hallazgos.
- GLM 5.3 (opencode): 8 hallazgos.
- GLM 5.2 (opencode): 10 hallazgos.
- GLM 4.7 (opencode): 7 hallazgos.

## Actuan (corregido antes del commit)

- Sin breaker: falla sistemica salia 0 (5/5, tres critical). Ahora
  falla honesto a los 5 seguidos y sale 1 sin observaciones.
- Cierre/pausa/demapeo dejaba miembros activos por siempre (5/5, tres
  critical). Ahora da de baja al observar no-activa y reconcilia
  ausentes del scan (salta con truncado).
- Error de DB por item abortaba la corrida entera (3/5, un critical).
  Ahora savepoint por item mas dedupe de ids del scan.
- Red dentro de la transaccion larga (4/5). Ahora fase fetch pura y
  escritura corta (patron `fetch_meli`/`sync_meli`).
- Upsert artesanal con carrera (5/5). Ahora `ON CONFLICT` con
  comparacion en Python y no-op real (patron `listings.py`).
- `ingest_run_id` siempre NULL (5/5, un critical). Ahora abre y sella
  run `meli_catalogo` y cada observacion lo cita.
- Caminos tristes sin pruebas (5/5). Ahora 8 pruebas: skip, breaker,
  exit 1, truncado, reconciliacion, transicion a cerrado, savepoint
  y vendedor invalido.
- `tipo`/`categoria` crudos al INSERT, `except` tragando
  `AttributeError` propio, visitas antes de validar estado, `ORDER BY`
  indeterminista (2-3/5 cada uno). Ahora coerceados, tupla acotada,
  visitas solo a activas y `ORDER BY odoo_sku`.

## Consideran (resueltos sin cambio o con nota)

- Ancla mutable a diario: es valor derivado y se recalcula; el
  DISTINCT lo vuelve no-op. Sin cambio.
- Buckets cuentan cerradas: diagnostico de mapeo metodo M.0, queda
  documentado. Sin cambio.
- Una variacion malformada tumba item y observacion: todo-o-nada
  honesto con skip contado y reintento manana. Sin cambio.

## Notan

- `assert` bajo `python -O`: desaparecio con el rewrite a `ON CONFLICT`.
- `_observa` con 13 kwargs: el `PlanItem` ya hila un objeto.
- `miembros_escritos` cuenta upserts: honesto (`visto_at` se reescribe).
- `seller_sku` con namespace Odoo: malentendido, se guarda el
  `SELLER_SKU` de la plataforma (igual al Odoo por construccion).
- Scan sin `status` quiza solo trae activas: supuesto sin verificar
  en construccion; el diseno queda correcto en ambas ramas.

## Descartan

Ninguno: todo lo no corregido quedo documentado arriba.

## Mapa de acuerdos

Los 5 revisores coincidieron en breaker, ciclo de vida, upsert
atomico, `ingest_run` y pruebas tristes; 4/5 en red-fuera-de-tx y
3/5 en error-DB-aislado. Ningun desacuerdo entre modelos: las
diferencias fueron solo de severidad (critical/warning/nit).
