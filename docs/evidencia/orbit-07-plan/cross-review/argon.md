El dueño quiere mejorar las decisiones del motor publicitario de Orbit para proteger ventas recientes ante pausas prematuras, fijar metas y límites económicos individuales por producto, acotar el gasto agregado de aprendizaje por producto, pausar anuncios individuales cuando ese aprendizaje se agote sin ventas y evaluar cambios de puja con datos económicos comparables, activando cada mejora tras su verificación técnica sin pilotos largos ni esperas estadísticas previas. Esta revisión evalúa el plan `plans/orbit-07-motor-decisiones.md` antes de implementar y reporta los defectos verificados contra el código y el esquema actuales.

Aplico **principle-prove-it-works** ejecutando sondas reales contra los módulos vigentes para cada hallazgo. Aplico **principle-boundary-discipline** al verificar el aislamiento puro de `app/optimizer/` frente a `psycopg` y el esquema JSON cerrado de `CasoHoja`. Aplico **principle-model-the-domain** al contrastar las máquinas de estado de `apply_queue`, `anuncio_retiro` y `target_acos_ciclo` con los estados que el plan propone. Aplico **principle-fix-root-causes** al rastrear los bloqueos de permisos y clamps hasta su definición en migraciones y funciones puras. Aplico **unslop** para redactar el informe directo y sin relleno.

---

### Dictamen asesor

**No apto para implementar sin corregir antes 6 defectos estructurales.**

La secuencia `M0 → M1 → M2 → M3 → M4 → M5+M6` respeta la instrucción del dueño de activar mejoras sin pilotos largos ni cohortes experimentales. Sin embargo, el plan contiene contradicciones directas con el código y con los candados de PostgreSQL que provocarían:
1. que el caso insignia de M2 (`8 % × 0.5 = 4 %`) salte a `10 %` o quede con juicio de pérdida (`R3`) inoperante;
2. que M3 y M4 fallen con `InsufficientPrivilege` en producción o rompan los candados de `test_architecture.py` y `0002_apply.sql`;
3. que el alcance por anuncio en grupos compartidos (M4) sea irrecuperable desde `ads_product_metric_observation` porque la ingesta fusiona filas por `(asin, sku, fecha)` y descarta `adGroupId`;
4. que el contrato de persistencia y FK de M2/M1/M5 rompa la pureza de `replay.reproduce` y el esquema cerrado de `CasoHoja.desde_json`;
5. que la "espera de protección" en la cola (M1) bloquee las pujas de la hoja durante hasta 10 días y deje la decisión como `huerfana` si no se registra en `decision_sin_aplicar`.

---

### Hallazgos verificados con evidencia

#### H1 (ALTA). M2: el caso `8 % × 0.5 = 4 %` salta a `10 %` al converger o bajar, y su punto de equilibrio individual no entra a `R3` ni a `estado_grupo`

* **Referencias.**
  * Plan: `plans/orbit-07-motor-decisiones.md:189,191,428` (pide `min(fraccion_configurada × margen_producto, MARGEN_BANDA_MAX)` con el caso `8 % × 0.5 = 4 %` sin elevarlo al piso de `10 %`, y mover juntos meta y punto de equilibrio para no dejar el equilibrio promedio dentro del juicio de pérdida).
  * Código: `app/optimizer/goals.py:569,860-933` (`MARGEN_BANDA_MIN = Decimal("10")`; `_converge_al_destino` y `resuelve_target_margen` ejecutan `ultimo = min(max(ultimo, MARGEN_BANDA_MIN), MARGEN_BANDA_MAX)` antes de calcular el paso de `0.5`); `app/optimizer/caso.py:200-220` (`equilibrio_acos_pct` vive en `EconomiaPlataforma`, no por hoja); `app/cycle.py:2107,2376-2383` (`equilibrio_acos_pct` se puebla una sola vez por plataforma y solo cuando `procedencia == "margen_plataforma"`); `app/optimizer/politica.py:303-309,457-485` (`R3` lee `economia.plataforma.equilibrio_acos_pct`, pero `R4` y `estado_grupo` comparan contra `1.15 × target`).
* **Comando reproducible.**
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
import datetime as dt
from decimal import Decimal
from app.optimizer import politica, caso, goals
from test_optimizer_caso import fabrica_caso, nivel, tramo

print("1) Convergencia desde ultimo=4.0:",
      goals._converge_al_destino(ultimo=Decimal("4.0"), destino=Decimal("20.0"), motivo="sin_margen"))
med_6 = goals.MedicionMargen(Decimal("6.0"), Decimal("0.98"), 45, Decimal("10000"),
                             dt.datetime(2026, 11, 5, tzinfo=dt.UTC), "MXN",
                             dt.date(2026, 2, 20), dt.date(2026, 10, 22))
print("2) Paso cuando margen baja de 8% a 6% (ultimo=4.0):",
      goals.resuelve_target_margen(med_6, Decimal("0.5"), dt.date(2026, 11, 6), ultimo=Decimal("4.0"), setting=Decimal("20.0"), dias_min=30))

propia = nivel(tramo(clics=200, pedidos=10, venta="10000", gasto="2500", impresiones=9000),
               tramo(clics=100, pedidos=5, venta="5000", gasto="1250", impresiones=4500))
precio = caso.PrecioVentana(Decimal("1500"), 120) # ACoS = 25%
for etiqueta, eq in (("eq_plataforma=41.43", Decimal("41.43")), ("eq_none=None", None), ("eq_producto=8.00", Decimal("8.00"))):
    c = fabrica_caso(propia=propia, precio=precio, economia=caso.Economia(
        plataforma=caso.EconomiaPlataforma("MXN", eq, Decimal("350"), Decimal("0.80"), Decimal("0.70")),
        target_acos_pct=Decimal("4.00"),
    ))
    print(f"3) R3 con target=4.00 y {etiqueta}:", politica.decide(c))
PY
```
* **Salida observada.**
```text
1) Convergencia desde ultimo=4.0: ResolucionMargen(aplicado=Decimal('10.5'), derivado=None, motivo='sin_margen', convergiendo=True)
2) Paso cuando margen baja de 8% a 6% (ultimo=4.0): ResolucionMargen(aplicado=Decimal('10'), derivado=Decimal('3.00'), motivo=None, convergiendo=False)
3) R3 con target=4.00 y eq_plataforma=41.43: Mantener(motivo='vende_dentro_del_margen')
3) R3 con target=4.00 y eq_none=None:         Mantener(motivo='vende_dentro_del_margen')
3) R3 con target=4.00 y eq_producto=8.00:    Mover(factor=Decimal('-0.25'), bid_nuevo=Decimal('7.5000'), motivo='pierde_dinero_fuerte', nivel='hoja')
```
* **Resultado exigido por la intención.** Un producto con margen `8 %` y meta `4 %` debe conservar su meta sin rebotar a `10 %`/`10.5 %` al converger o caminar a `0.5` por ciclo, y una hoja con ACoS `25 %` sobre ese producto (perdiendo `17` puntos de margen) debe recortarse por `R3` (`pierde_dinero_fuerte`), no quedarse en `Mantener(vende_dentro_del_margen)`.
* **Impacto económico y operativo.** Medido en la sonda: dejar `equilibrio_acos_pct` en `EconomiaPlataforma` (que se construye una vez en `lee_plataforma` antes de recorrer hojas) impide que `R3` recorte hojas que pierden dinero contra el margen de su producto (o de su familia, bug ya latente en `margen_familia`). Y reutilizar `_converge_al_destino` / `resuelve_target_margen` sin quitar el pre-clamp `MARGEN_BANDA_MIN = 10` del ancla eleva de golpe un target de `4 %` a `10.5 %` en cuanto un día falle la frescura del ledger.
* **Corrección mínima del plan.** Especificar en M2 que: (1) el punto de equilibrio viaja por hoja en `Economia` (no solo en `EconomiaPlataforma`), poblado desde el margen del peldaño ganador cuando provenga de margen (`margen_producto`, `margen_familia`, `margen_plataforma`); (2) el paso y la convergencia del peldaño producto no aplican el pre-clamp inferior `MARGEN_BANDA_MIN = 10` al ancla `ultimo`; y (3) ampliar el `CHECK target_acos_ciclo_procedencia_check` (`0063`) y `PELDANOS_CASCADA` en la misma migración de M2.

---

#### H2 (ALTA). M3 y M4: `app_decide` tiene `REVOKE SELECT` explícito sobre `impulso`, `impulso_lectura`, `anuncio_retiro`, `anuncio_reposicion` y `anuncio_retiro_intencion`, y el cliente de escritura está prohibido fuera de `app/apply.py`

* **Referencias.**
  * Plan: `plans/orbit-07-motor-decisiones.md:233,263,270-272` (M3 exige en el ciclo excluir impulsos abiertos; M4 exige que el ciclo y `apply_cola` encolen, revaliden y ejecuten `pause_product_ad` mientras "`retiro_anuncios` conserva el único PUT y la reconciliación", vinculando decisión e intención antes del HTTP).
  * Código y esquema: `migrations/0065_bids02_impulso.sql:155,168` (`REVOKE SELECT ON impulso, impulso_lectura FROM app_ingest, app_decide` y candado `DO $$` que aborta si `app_decide` tiene `SELECT`); `migrations/0066_bids02_retiro_anuncios.sql:100,113` (`REVOKE SELECT ON anuncio_retiro, anuncio_reposicion FROM app_ingest, app_decide`); `migrations/0069_bids02_retiro_intencion.sql:98,110` (`REVOKE SELECT ON anuncio_retiro_intencion FROM app_ingest, app_decide`); `tests/test_architecture.py:321-366` (`PERMITIDOS_IMPORTAR_ADS_WRITE` solo permite `app/apply.py` y `app/ads/archivar.py`); `app/retiro_anuncios.py:291` (`archivar.preparar_escritor` construye su propio `AdsWriteClient` sin el `_TransportGuardado` de `owner`/`job_key` del ciclo).
* **Comando reproducible.**
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
from pathlib import Path
from test_architecture import PERMITIDOS_IMPORTAR_ADS_WRITE

for mig in ("0065_bids02_impulso.sql", "0066_bids02_retiro_anuncios.sql", "0069_bids02_retiro_intencion.sql"):
    lineas = [l.strip() for l in Path("migrations", mig).read_text().splitlines() if "app_decide" in l]
    print(mig, "->", lineas)
print("PERMITIDOS_IMPORTAR_ADS_WRITE ->", sorted(PERMITIDOS_IMPORTAR_ADS_WRITE))
PY
```
* **Salida observada.**
```text
0065_bids02_impulso.sql -> ['REVOKE SELECT ON impulso, impulso_lectura FROM app_ingest, app_decide;', "OR has_table_privilege('app_decide', 'impulso', 'SELECT')"]
0066_bids02_retiro_anuncios.sql -> ['REVOKE SELECT ON anuncio_retiro, anuncio_reposicion FROM app_ingest, app_decide;', "OR has_table_privilege('app_decide', 'anuncio_retiro', 'SELECT')"]
0069_bids02_retiro_intencion.sql -> ['REVOKE SELECT ON anuncio_retiro_intencion FROM app_ingest, app_decide;', "OR has_table_privilege('app_decide', 'anuncio_retiro_intencion', 'SELECT')"]
PERMITIDOS_IMPORTAR_ADS_WRITE -> ['app/ads/archivar.py', 'app/apply.py', 'app/bloqueo_dueno_efecto.py', 'tools/smoke_apply.py']
```
* **Resultado exigido por la intención.** El ciclo (`python -m app.cli cycle`, que corre en producción con `ORBIT_DSN_DECIDE` -> rol `app_decide`) debe poder excluir impulsos abiertos en M2/M3 y ejecutar/reconciliar retiros de anuncios desde la cola en M4 sin reventar por permisos ni perder el guard de lease (`ApplyAbortado`) del ciclo.
* **Impacto económico y operativo.**
  1. En M3 (que ni siquiera prevé migración obligatoria, línea 224), cualquier `SELECT` sobre `impulso` o `impulso_lectura` desde el ciclo en producción abortará TX2 con `psycopg.errors.InsufficientPrivilege`, tumbando el ciclo entero de la plataforma.
  2. En M4, `app_decide` no tiene `SELECT` ni `INSERT`/`UPDATE` sobre `anuncio_retiro_intencion`, `anuncio_retiro` ni `anuncio_reposicion`. Además, si `retiro_anuncios` usa `archivar.preparar_escritor` durante el ciclo, ejecuta HTTP fuera del `_TransportGuardado` del `Aplicador`, violando el ownership-check pre-HTTP (decisión 11 de `cycle.py:144`).
* **Corrección mínima del plan.**
  1. En M2/M3, incluir migración de permisos que otorgue `GRANT SELECT ON impulso, impulso_lectura, v_impulso_estado TO app_decide` (y actualice el test de privilegios de `0065`).
  2. En M4, otorgar en migración `GRANT SELECT, INSERT ON anuncio_retiro, anuncio_retiro_intencion TO app_decide` y `GRANT UPDATE (cerrada_el, detalle, retiro_id) ON anuncio_retiro_intencion TO app_decide`, además de inyectar `aplicador._cliente()` como `escritor` en `retiro_anuncios` para que todo PUT durante el ciclo pase por el `_TransportGuardado` del lock del ciclo.

---

#### H3 (ALTA). M4: `ads_product_metric_observation` no conserva `adGroupId` ni permite saber qué anuncio de un grupo compartido aportó actividad, y un impulso que ya tuvo impresiones retira los anuncios viejos antes de M4

* **Referencias.**
  * Plan: `plans/orbit-07-motor-decisiones.md:102,267-269` (M0 mide anuncios vinculados y grupos compartidos; M4 exige "Encolar únicamente anuncios que contribuyeron al alcance comprobado. En grupos compartidos, retirar esos anuncios individuales sin bajar la puja común").
  * Código y esquema: `migrations/0020_ads_producto_metrica.sql:19-38` (la tabla `ads_product_metric_observation` guarda `campaign_ids JSONB` y `ad_ids JSONB`, pero **no** tiene columna `ad_group_ids`, y fusiona por `(platform, advertised_asin, advertised_sku, metric_date)`); `app/ads/reports.py:1052-1090` (`_fusionar_fila_producto` suma todas las campañas de ese `asin/sku/fecha` y descarta `adGroupId`); `app/ads/structure_plan.py:98-127` (`ad_entity` para `kind='product_ad'` no guarda `seller_sku`, solo `listing_id` resuelto por `asin`, mientras que varios `listing` o anuncios pueden compartir ASIN/SKU en distintos ad groups).
* **Comando reproducible.**
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
from app.ads import reports
from decimal import Decimal
from collections import Counter
import datetime as dt

filas = [
    {"date": "2026-09-03", "advertisedAsin": "B0AAAAAAA1", "advertisedSku": "SKU-A",
     "campaignId": 100, "adGroupId": 1001, "adId": 11, "impressions": 50, "clicks": 5,
     "cost": 10.0, "purchases30d": 0, "sales30d": 0.0, "purchasesSameSku30d": 0, "attributedSalesSameSku30d": 0.0},
    {"date": "2026-09-03", "advertisedAsin": "B0AAAAAAA1", "advertisedSku": "SKU-A",
     "campaignId": 100, "adGroupId": 1002, "adId": 12, "impressions": 50, "clicks": 5,
     "cost": 10.0, "purchases30d": 1, "sales30d": 200.0, "purchasesSameSku30d": 1, "attributedSalesSameSku30d": 200.0},
]
plan, skips = reports._planea_filas_productos(
    reports.PRODUCTOS_CFG, filas, hoy=dt.date(2026, 9, 4),
    fecha_ini=dt.date(2026, 9, 1), fecha_fin=dt.date(2026, 9, 3)
)
print("Fila fusionada en ads_product_metric_observation:", plan[0])
PY
```
* **Salida observada.**
```text
Fila fusionada en ads_product_metric_observation: _FilaProducto(asin='B0AAAAAAA1', sku='SKU-A', metric_date=datetime.date(2026, 9, 3), impressions=100, clicks=10, cost=Decimal('20.0'), purchases30d=Decimal('1'), sales30d=Decimal('200.0'), purchases_same_sku30d=Decimal('1'), attributed_sales_same_sku_30d=Decimal('200.0'), campaign_ids=['100'], ad_ids=['11', '12'])
```
* **Resultado exigido por la intención.** Poder identificar de forma verificable qué `product_ad` contribuyó al gasto/clics comprobados y verificar en M2/M4 si un ad group exclusivo tuvo halo o gasto de ese producto.
* **Impacto económico y operativo.**
  1. Como `_planea_filas_productos` suma todos los anuncios del mismo `(asin, sku, fecha)` en una sola fila y une `ad_ids=['11', '12']`, si el anuncio `11` gastó `200 MXN` con `0` ventas y el anuncio `12` gastó `10 MXN` con `1` venta, o si `11` tuvo `1` impresión y `12` gastó `500 MXN`, ambos aparecen juntos en `ad_ids` de esa fecha sin desglose de gasto ni clics por `ad_id` ni `ad_group_id`.
  2. Además, en M2 (línea 192: "Exigir atribución compatible con el margen durante la ventana de las hojas. Si hay halo sin margen atribuible..."), `ads_metric_observation` (que sí está a grano hoja `keyword`/`product_target`) **ya tiene** las columnas `ad_revenue` y `revenue_same_sku` (`migrations/0001_initial.sql:294-295`), pero `_SQL_TRAMOS` en `app/lecturas_caso.py:62-105` **no lee `revenue_same_sku`**. Si M2 intenta comprobar el halo de la ventana de 90 días de las hojas mirando `ads_product_metric_observation` (que solo tiene 77 fechas desde `2026-08-08` y está a grano producto, no ad group), ninguna hoja de 90 días calificará jamás; mientras que si lee `revenue_same_sku` de `ads_metric_observation` en `lecturas_caso.py`, tiene los 90 días exactos por hoja y por ad group.
* **Corrección mínima del plan.**
  1. En M2, comprobar la ausencia de halo de la ventana de las hojas directamente con `revenue_same_sku == ad_revenue` en `ads_metric_observation` (ampliando `_SQL_TRAMOS` y `Tramo` en `lecturas_caso.py`/`caso.py`), en vez de exigir 90 días en `ads_product_metric_observation`.
  2. En M4, declarar que como `ads_product_metric_observation` consolida por `(platform, asin, sku, metric_date)` y la condición de pausa exige `purchases30d == 0` y `sales30d == 0` para **todo el producto** en la ventana, "contribuir al alcance comprobado" significa aparecer en `ad_ids` de las observaciones del producto en esa ventana y pertenecer a un anuncio activo (`ad_entity` `kind='product_ad'`) con `first_seen_at <= ventana_desde`.

---

#### H4 (ALTA). M1, M2, M3 y M5: el contrato de freeze y la FK a `evaluacion_producto_ciclo` chocan con la pureza de `replay.reproduce(inputs)` y el validador estricto de `CasoHoja.desde_json`

* **Referencias.**
  * Plan: `plans/orbit-07-motor-decisiones.md:151,184,193,224,305,351` (M2 pide: "persistir una evaluación append-only y referenciarla desde las decisiones. El replay usa la evaluación congelada" y AUTO-03 §Persistencia línea 330: "`decision` referencia esa evaluación inmutable mediante FK. No se copian tres veces sus insumos en tablas independientes. El replay resuelve esa referencia"; M1, M3 y M5 extienden `caso.py` y `replay.py` conservando las eras anteriores).
  * Código: `app/optimizer/replay.py:122` (`def reproduce(inputs: dict) -> tuple[str | None, Decimal | None, str | None]:` recibe **únicamente** el `dict` `decision.inputs`, sin conexión a base de datos); `tests/test_architecture.py:38,226-243` (`test_motor_puro_sin_io` prohíbe `psycopg` y `app.db` en `app/optimizer/replay.py`); `app/optimizer/caso.py:24,415-439` (`POLITICA_BID = "niveles_v3"` y `_objeto(datos, _CLAVES_CASO, "caso")` exige `set(datos) == claves`, lanzando `ValueError` si hay una clave de más o de menos).
* **Comando reproducible.**
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
import inspect
from app.optimizer import caso, replay
from test_optimizer_caso import fabrica_caso

print("Firma reproduce:", inspect.signature(replay.reproduce))
base_json = fabrica_caso().como_json()
try:
    caso.CasoHoja.desde_json({**base_json, "campo_nuevo": None})
except ValueError as exc:
    print("Fallo con clave nueva en CasoHoja.desde_json:", exc)
PY
```
* **Salida observada.**
```text
Firma reproduce: (inputs: 'dict') -> 'tuple[str | None, Decimal | None, str | None]'
Fallo con clave nueva en CasoHoja.desde_json: caso: claves distintas a ['ad_group_id', 'bid', 'cuenta', 'economia', 'grupo', 'hoja_id', 'observado_al', 'pausa', 'pedidos_inmaduros', 'plataforma', 'precio', 'propia', 'trayectoria', 'ventana_desde', 'ventana_hasta']
```
* **Resultado exigido por la intención.** `replay.reproduce(decision.inputs)` debe seguir siendo una función pura sin IO capaz de reproducir tanto las decisiones históricas de `niveles_v3` ya guardadas en producción como las nuevas decisiones de M1, M2, M3 y M5+M6.
* **Impacto económico y operativo.**
  1. Si `decision` solo guarda una FK a `evaluacion_producto_ciclo` sin embeber en `decision.inputs["caso"]` los valores resueltos que `decide(caso)` consume, `replay.reproduce(inputs)` no puede resolver la FK porque no tiene `conn` ni puede importar `psycopg` (`test_motor_puro_sin_io`).
  2. Si M1, M2, M3 o M5 agregan campos a `CasoHoja`, `Economia`, `InsumosPausa` o `EfectoCambio`, `CasoHoja.desde_json` fallará con `ValueError` al rejugar cualquier fila histórica de `niveles_v3` que no tenga las claves nuevas (rompiendo `_SQL_GUARDADOS` en `tools/rejuega_niveles.py:418-427`).
* **Corrección mínima del plan.**
  1. En M2, especificar que aunque `decision` tenga FK a `evaluacion_producto_ciclo` para trazabilidad SQL, los valores que consume `decide(caso)` (target, equilibrio, tope de aprendizaje agotado) viajan **también dentro de `decision.inputs["caso"]`** para que `replay.reproduce(inputs)` siga siendo pura y sin IO.
  2. En M1–M6, especificar que `CasoHoja.desde_json` (y `_economia_desde`, `_pausa_desde`, `_efecto_desde`) debe aceptar tanto el conjunto de claves histórico de `niveles_v3` (rellenando los campos nuevos con `None`/default histórico) como el conjunto nuevo, o despachar por versión explícita en `inputs["politica"]`.

---

#### H5 (MEDIA). M1: mantener una pausa en espera dentro de `apply_queue` bloquea los ajustes de bid de esa hoja en `cycle.py` y rompe `v_decision_huerfana` si se descarta antes de liberar o no registra `decision_sin_aplicar`

* **Referencias.**
  * Plan: `plans/orbit-07-motor-decisiones.md:146-152,165` ("Usar el mismo evaluador puro al decidir y al revalidar. Distinguir explícitamente aplicar, esperar y descartar. Una espera de protección no cobra cuota, no entra en `applying` y no reinicia el veto de 48 horas").
  * Código y esquema: `app/apply_cola.py:1073-1178` (`libera_vencidos` primero transiciona `pending_veto -> released` y revalida sobre `released`; toda fila que queda en `released` esperando —como `espera_target` o `sin_quota`— debe llamar a `apply.registra_sin_aplicar(conn, fila.decision_id, cycle_id, motivo)` con un motivo dentro del `CHECK decision_sin_aplicar_motivo_check` de `0045`); `app/apply_cola.py:140-146` (`_SQL_CLAVES_BLOQUEADAS` incluye toda fila en `pending_veto` o `released`); `app/cycle.py:1673-1683` (`if (entidad_id, "entity_cut", None) in bloqueadas: contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1; return`).
* **Comando reproducible.**
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
from app import apply
from pathlib import Path

print("Motivos permitidos en decision_sin_aplicar:", apply.MOTIVOS_SIN_APLICAR)
lineas = Path("app/cycle.py").read_text().splitlines()[1673:1683]
print("Comportamiento de cycle.py ante fila en apply_queue (pending_veto o released):")
for l in lineas:
    print(" ", l)
PY
```
* **Salida observada.**
```text
Motivos permitidos en decision_sin_aplicar: ('modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora', 'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota', 'espera_target', 'sin_respuesta', 'perdida', 'choque_clave')
Comportamiento de cycle.py ante fila en apply_queue (pending_veto o released):
      if (entidad_id, "entity_cut", None) in bloqueadas:
          # 2.2 sellado 5 / 2.4: clave de efecto en vuelo (fila NO terminal o
          # veto vigente) — el ciclo NO re-decide esa clave. Salta la entidad
          # ENTERA del motor de bids (decide da pause o juicio en una
          # sola llamada; prohibir solo el pause exigiria inventar un umbral,
          # regla 3 — declarado en el docstring del modulo). CAMPANA ACTIVA 01
          # · 1.6: va DESPUES de los gates — una hoja en campana/grupo no
          # ENABLED se cuenta con el motivo del ANCESTRO, no como veto.
          contadores.skips_entidad[MOTIVO_VETO_PENDIENTE] += 1
```
* **Resultado exigido por la intención.**
  1. Cuando el pedido reciente se conoce **al decidir** en el ciclo, `decide(caso)` no emite `Pausar`: se abstiene de la pausa por cero ventas y permite evaluar el bid de la hoja (p. ej. recortar `-12 %` o `-25 %` si sangra, o mantener por `venta_reciente`).
  2. Cuando la pausa ya estaba encolada y el pedido reciente aparece **durante las 48h de veto** (al revalidar en `libera_vencidos`), dejar la fila en `released` esperando hasta 10 días: (a) exige registrar un motivo válido en `decision_sin_aplicar` (y ampliar el `CHECK decision_sin_aplicar_motivo_check` de `0045` + `MOTIVOS_SIN_APLICAR`), y (b) mientras la fila permanezca en `released` en `apply_queue`, `cycle.py:1673` saltará la hoja entera con `veto_pendiente` en cada ciclo diario, impidiendo cualquier ajuste de bid o regreso por desplome durante toda la espera.
* **Impacto económico y operativo.** Si en la revalidación una venta reciente deja la fila `released` esperando hasta 10 días en `apply_queue` en lugar de descartarla (`discarded` con motivo) y dejar que el episodio por hoja gobierne los ciclos siguientes, la hoja queda congelada en `veto_pendiente` sin poder ajustar su puja ni regresar por desplome durante toda la espera.
* **Corrección mínima del plan.** Aclarar en M1 dos opciones limpias para la revalidación en `apply_cola.py`: o bien (opción A, más simple y coherente con `_procesa_decisora`) si al liberar aparece una venta reciente que abre/mantiene protección, la fila de `apply_queue` pasa a `discarded` (`MOTIVO_VENTA_RECIENTE`) liberando la clave `entity_cut` para que el motor de bids opere sobre la hoja mientras dura el episodio de protección, y al vencer el episodio un ciclo futuro vuelve a proponer la pausa si el pedido se corrigió a cero; o bien (opción B, si se conserva en `released`) ampliar `MOTIVOS_SIN_APLICAR` + migración del `CHECK` de `decision_sin_aplicar` y documentar que la hoja no moverá bids mientras la fila espere en `released`.

---

#### H6 (MEDIA). M4: los candados de esquema y consumidor de `anuncio_retiro` y `apply_queue` impiden insertar `pause_product_ad` sin ampliar 6 contratos acoplados

* **Referencias.**
  * Plan: `plans/orbit-07-motor-decisiones.md:263,270,272` ("Introducir la decisión propuesta `pause_product_ad` y ampliar esquema, permisos, mapas y consumidores afectados en una migración nueva... Restaurar solo anuncios existentes retirados y confirmados por esta política. No reactivar pausas ajenas ni recrear anuncios archivados").
  * Código y esquema:
    1. `migrations/0066_bids02_retiro_anuncios.sql:38` y `0069:40` (`CHECK (motivo = ANY (ARRAY['impulso', 'sin_pedidos', 'subfamilia']))`);
    2. `app/pantalla_productos.py:293-300` (`_SQL_RETIROS_ABIERTOS_SUELTOS` selecciona `WHERE r.platform = %s AND r.impulso_id IS NULL` y los expone en la UI para el botón manual `REGRESAR N ANUNCIOS` de `api_fabrica.py:726`, donde `repone` en `retiro_anuncios.py:514` **sí recrea** anuncios `ARCHIVED`);
    3. `migrations/0001_initial.sql:40,874,898,941` (`decision_kind`, `decision_termino_coherente`, `decision_unica_entidad_ciclo`, `decision_madurez_corte`);
    4. `migrations/0002_apply.sql:55-93,455-481` (`apply_queue.familia` generada solo para `pause`/`negative`/`harvest`, `apply_queue_solo_cortes`, y `apply_cap_de_config(p_motor)` junto con el test `tests/test_apply_schema.py:1118` que exige `v_decision_huerfana` == `KINDS_QUOTA`).
* **Comando reproducible.**
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
from pathlib import Path
from app import retiro_anuncios, apply

print("MotivoRetiro en retiro_anuncios.py:", retiro_anuncios.MotivoRetiro)
print("repone recrea ARCHIVED en retiro_anuncios.py:514-518:")
for l in Path("app/retiro_anuncios.py").read_text().splitlines()[513:519]:
    print(" ", l)
print("KINDS_QUOTA en apply.py:", apply.KINDS_QUOTA)
PY
```
* **Salida observada.**
```text
MotivoRetiro en retiro_anuncios.py: typing.Literal['impulso', 'sin_pedidos', 'subfamilia']
repone recrea ARCHIVED en retiro_anuncios.py:514-518:
      if estado == "ARCHIVED":
          res = archivar.reponer_anuncios(escritor, [reversa], ejecutar=True)
          if not res or res[0].resultado != "repuesto":
              detalle = res[0].detalle if res else "sin resultado"
              raise RuntimeError(f"reposicion de {ad_id} no confirmada: {detalle}")
KINDS_QUOTA en apply.py: ('bid', 'pause', 'negative', 'harvest')
```
* **Resultado exigido por la intención.** Que un retiro automático de M4 no pueda ser recreado como anuncio nuevo si fue archivado en Amazon, y que la migración de M4 no rompa `test_apply_schema.py`, `test_preflight_1_4.py` ni `decision_madurez_corte`.
* **Impacto económico y operativo.**
  1. En PostgreSQL, `ALTER TYPE decision_kind ADD VALUE 'pause_product_ad'` no puede usarse dentro de la misma transacción en CHECKs, vistas (`v_decision_huerfana`) ni columnas generadas (`apply_queue.familia`), igual que ocurrió en `0004_ad_entity_kind_product_ad.sql`. Si M4 intenta meter todo en una sola migración con `BEGIN ... COMMIT`, PostgreSQL aborta con `UnsafeNewEnumValueUsage`.
  2. Como `_SQL_RETIROS_ABIERTOS_SUELTOS` filtra solo `impulso_id IS NULL`, los retiros de M4 aparecerán en `/productos-sin-clics` y si el dueño pulsa reponer desde ahí sobre un anuncio archivado, `repone()` actual ejecutará `POST /sp/productAds` recreando el anuncio salvo que `repone()` bloquee la recreación para el motivo de M4.
  3. Si `pause_product_ad` consume la reserva de cuota `pause` existente ("con una sola reserva por anuncio", AUTO-03 línea 338) en vez de crear un 5.º kind de quota, hay que declarar explícitamente si `v_decision_huerfana` incluye `pause_product_ad` y cómo se actualiza `test_kinds_de_la_vista_huerfana_espejan_kinds_quota` en `tests/test_apply_schema.py:1118`.
* **Corrección mínima del plan.** Especificar en M4: (1) separar en dos migraciones el `ALTER TYPE decision_kind ADD VALUE 'pause_product_ad'` de los `ALTER TABLE`/`CREATE OR REPLACE VIEW` que usan el literal (patrón `0004`); (2) especificar si `pause_product_ad` usa la cuota `pause` o una clave `ads_apply_cap_<platform>_pause_product_ad` propia, actualizando `v_decision_huerfana`; y (3) hacer que `repone()` rechace recrear `ARCHIVED` cuando el retiro sea de M4 (`producto_sin_ventas`) o sin flag explícito de recreación.

---

#### H7 (MEDIA). M5: `v_cambio_bid` proyecta hoy el `current_bid` actual en cambios históricos de campaña y `_lee_efecto` hace `1` consulta por hoja con cambio

* **Referencias.**
  * Plan: `plans/orbit-07-motor-decisiones.md:305,310-311,330` (M5 pide: "Reutilizar `v_cambio_bid` y las métricas point-in-time... Marcar cambios posteriores de puja, presupuesto, placement, precio o alcance que invaliden la comparación... Usar solo observaciones disponibles en el instante evaluado. No reconstruir el pasado con margen actual, estado actual de identidad o datos corregidos después", y medir presupuesto de consultas).
  * Código y esquema: `migrations/0067_bids02_campana_ajuste.sql:91-100` (la 3.ª rama de `v_cambio_bid` hace `JOIN v_hoja_activa h ON h.campana_id = a.campana_id` leyendo `h.current_bid` y `ad_entity_state.status = 'ENABLED'` **actuales**, no los del instante `visto_el`); `app/lecturas_caso.py:336-340,362-420` (`LecturasPlataforma.caso` ejecuta `_SQL_EFECTO` una vez por cada hoja con cambio en 90 días y solo pide `impressions`, `clicks`, `cost` y `orders`, sin `ad_revenue` ni `revenue_same_sku`).
* **Comando reproducible.**
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /Users/dn/dev/goncloud-Orbit/.venv/bin/python - <<'PY'
from pathlib import Path
from app import lecturas_caso

print("Rama 3 de v_cambio_bid en 0067:")
for l in Path("migrations/0067_bids02_campana_ajuste.sql").read_text().splitlines()[90:101]:
    print(" ", l)
print("Columnas leidas hoy en _SQL_EFECTO:")
for l in lecturas_caso._SQL_EFECTO.splitlines():
    if "sum(" in l:
        print(" ", l.strip())
PY
```
* **Salida observada.**
```text
Rama 3 de v_cambio_bid en 0067:
  SELECT h.hoja_id,
         a.confirmado_el,
         h.current_bid,
         h.current_bid,
         h.bid_currency,
         'ajuste_de_campana',
         NULL::bigint
    FROM campana_ajuste a
    JOIN v_hoja_activa h ON h.campana_id = a.campana_id AND h.current_bid IS NOT NULL
   WHERE a.confirmado_el IS NOT NULL AND a.clase = 'ajuste_ubicacion';
Columnas leidas hoy en _SQL_EFECTO:
  THEN sum(v.impressions) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
  THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
  THEN sum(v.impressions) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
  THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
  THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
  THEN sum(v.clicks) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END,
  THEN sum(v.cost) FILTER (WHERE v.metric_date BETWEEN %s AND %s) END,
  THEN sum(v.orders) FILTER (WHERE v.metric_date BETWEEN %s AND %s)::bigint END
```
* **Resultado exigido por la intención.** En M5, leer ventas (`ad_revenue` y `revenue_same_sku`) maduras antes y después del cambio y detectar cambios intermedios de campaña/presupuesto/placement/precio sin contaminar evaluaciones pasadas con el estado actual de `v_hoja_activa` ni multiplicar consultas N+1 por hoja.
* **Impacto económico y operativo.**
  1. `_SQL_EFECTO` hoy no lee `ad_revenue` ni `revenue_same_sku`, y `postp` va desde `x + 1` hasta `D - 3` (`DIAS_FRESCURA_BIDS = 3`), **no** hasta `D - 10` (madurez de 10 días que exige M5 en línea 313: "Siete días posteriores completos con diez días de madurez llegan como pronto en D+17"). Si M5 modifica `post` o `postp` en `EfectoCambio`, romperá R1, R15, R16 y `_precio_de_hoy` para cambios con entre 4 y 16 días de edad; M5 necesita campos separados en `EfectoCambio` para la ventana económica madura (`[x+1, x+7]` cuando `D - (x+7) >= 10`).
  2. La 3.ª rama de `v_cambio_bid` ignora `clase IN ('presupuesto', 'fuera_de_amazon')` de `campana_ajuste` y tampoco lee `ads_campana_config_observation` (cambios hechos en consola de Amazon detectados por el sync) ni `precio_cambio` (cambios de precio aplicados por Repricing).
* **Corrección mínima del plan.** Especificar en M5: (1) mantener intactos los rangos de tráfico (`post`) y CPC (`postp`) de `EfectoCambio` y agregar campos propios para la ventana económica madura pre/post (`gasto`, `venta`, `venta_mismo_sku`, `pedidos` en `[x-7, x-1]` y `[x+1, x+7]` solo cuando `x + 7 <= D - 10`); y (2) leer en lote por plataforma los eventos de invalidación (`ads_campana_config_observation`, `campana_ajuste` y `precio_cambio` confirmado) con `observed_at / confirmado_el <= visto_el` en lugar de depender solo de la 3.ª rama actual de `v_cambio_bid`.

---

### Hipótesis o limitaciones sin verificar

1. **Cobertura real en producción (inferencia / límite de entorno).** Por las reglas de esta revisión no se consultó la base de producción. Según `docs/evidencia/auto-03/diseno/lectura-2026-10-25.md`, solo había 7 productos en MX y 3 en US con margen individual positivo, y 77 fechas en `ads_product_metric_observation`. Si M2 exige comprobar 90 días en `ads_product_metric_observation` en vez de usar `ads_metric_observation.revenue_same_sku` (ver H3), la cobertura inicial de M2 será `0` productos hasta que el historial cumpla 90 días más 30 de maduración.
2. **Interacción entre `CLICS_NUEVOS = 20` (R15) y recortes repetidos en M6 (inferencia).** En `app/optimizer/politica.py:358`, `_freno_trayectoria` exige `efecto.clics_post >= 20` antes de evaluar `R16` (`razon_trafico < 0.70`). Si en M6 se quiere permitir repetir un recorte tras `D+17` en hojas caras de bajo volumen que conservaron ventas y mejoraron economía pero tuvieron menos de 20 clics en la ventana post, mantener el mínimo de 20 clics (`M6`, línea 350) impedirá que la regla nueva dispare en esas hojas. M0 debe medir cuántos episodios con `D+17` tienen `< 20` clics post.

---

### Comprobaciones ejecutadas

1. `graphify query "motor decisiones pujas pausas ads optimizer"` y `graphify query "apply_cola retiro_anuncios economia_observada goals lecturas_caso replay eras"` sobre `graphify-out/graph.json`.
2. Reproducción exacta de la sonda del Apéndice A (`plans/orbit-07-motor-decisiones.md:402-424`) y verificación contra `docs/evidencia/orbit-07-plan/pausa-venta-reciente.json`.
3. Sondas en Python (`/Users/dn/dev/goncloud-Orbit/.venv/bin/python` con `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests`, sin tocar archivos ni red):
   * Verificación de `goals._converge_al_destino`, `goals.resuelve_target_margen` y `politica.decide` para el caso `8 % × 0.5 = 4 %` y acoplamiento de `equilibrio_acos_pct` (H1).
   * Verificación de `REVOKE SELECT ... FROM app_ingest, app_decide` en migraciones `0065`, `0066` y `0069`, y `PERMITIDOS_IMPORTAR_ADS_WRITE` en `tests/test_architecture.py` (H2).
   * Verificación de fusión y pérdida de `adGroupId` en `app.ads.reports._planea_filas_productos` (H3).
   * Verificación de firma pura de `replay.reproduce` y rechazo de claves nuevas/ausentes en `CasoHoja.desde_json` (H4).
   * Verificación de bloqueo de entidad en `cycle.py` por `_SQL_CLAVES_BLOQUEADAS` y vocabulario cerrado `MOTIVOS_SIN_APLICAR` (H5).
   * Verificación de `retiro_anuncios.repone`, `KINDS_QUOTA` y `v_cambio_bid` / `_SQL_EFECTO` (H6, H7).
