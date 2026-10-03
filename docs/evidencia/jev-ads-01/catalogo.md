# Catálogo para Jev asesor — contrato propuesto

Base inspeccionada: `/Users/dn/dev/wt/jev-ads-design` @ `3cbfab5`. Solo lectura del repo; SQL de censo preparado, no ejecutado. Sin afirmación de cobertura productiva actual.

## Identidad y recorrido exacto

- `product(id PK, odoo_sku UNIQUE, name, active)` es identidad Odoo; `listing(id PK, product_id FK, platform, external_id, seller_sku)` identifica publicación, UNIQUE `(platform, external_id)` (`migrations/0001_initial.sql:95,106`). Un producto puede tener varios ASIN. No resolver por nombre.
- `ad_entity(id, platform, kind, external_id, parent_id FK, listing_id FK)` representa campaña → ad_group → product_ad (`0001:250`). **Solo product_ad tiene listing_id por la ingesta**; ad_group.listing_id no representa su catálogo (`app/ads/structure.py:66`). Join observado: anuncio `(platform, asin)` → listing `(platform, external_id)`.
- `campana_grupo_rol(grupo_id, rol, ad_entity_id campaña, ad_group_ad_entity_id)` guarda grupos explícitos; trigger valida clases y padre (`0018_fabrica_campanas.sql:96`). Para origen usar PK del ad_group; para destino resolver primero `(platform, kind='ad_group', external_id)` recibido del resolutor, validar padre campaign. Leer ambos conjuntos separadamente aunque compartan grupo.
- `campana_grupo_producto(grupo_id, product_id, listing_id, seller_sku, margen_neto_pct)` describe **selección al alta**, no anuncios actuales (`0018:140`). Atención migración posterior: PK vigente `(grupo_id, listing_id)`, SKU único por grupo, margen nullable (`0019_fabrica_grupo_publicacion_v2.sql:19`); no deduplicar por product_id perdiendo publicaciones.
- Catálogo observado de cada ad_group: hijos `kind='product_ad'` → LEFT JOIN estado → LEFT JOIN listing → LEFT JOIN product. Conservar filas sin vínculo y estado faltante. Comparar conjunto observado con selección al alta; la tabla del grupo no sustituye al universo observado.

## Completitud y tiempo

`ad_entity_state(status,synced_at)` es cache mutable, explícitamente no histórico (`0001:644`). Los anuncios archivados ya conocidos se actualizan si llegan explícitos; los ausentes de la respuesta no se tocan (`structure.py:203`). Por tanto, `COUNT(unmapped)=0` y estados frescos **no demuestran exhaustividad del listado**. Una corrida exitosa agregada tampoco demuestra por sí sola snapshot completo por grupo; hay skips y padres omitidos.

`product.name` se reemplaza durante costos (`app/costs.py:383`); no tiene versión/fecha en product. listing.product_id/SKU también pueden cambiar durante sync (`app/listings.py:31`). No reconstruir ficha histórica juntando una decisión vieja con nombres actuales. Congelar valores e IDs vistos, timestamps disponibles, estado y huella; etiquetar captura actual como tal. Para afirmar que ningún producto sirve hacen falta universo completo y fichas suficientes de todos. Sin prueba de universo, resultado global negativo queda insuficiente aunque todas las fichas conocidas discrepen. Un producto compatible sí aporta evidencia positiva sobre ese producto, sin identificar qué compró el cliente.

## Ficha manual mínima versionada

Propuesta: documentos JSON inmutables validados al cargar, uno por `listing_id` + revisión. `schema_version`, `listing_id`, `platform`, `external_id`, `product_id`, `revision`, `recorded_at`, `validated_at`, `validated_by`, `source_refs`, atributos explícitos (función, categoría, compatibilidades, medidas/unidades, exclusiones); valores desconocidos ausentes. IDs solo referencias: DB sigue siendo SSOT de identidad; loader comprueba el cruce y rechaza discordancia. No usar nombre como prueba de atributos ni inventar vigencia anterior a validación. Nombre capturado es auxiliar. Hash de bytes canónicos identifica revisión; no sobrescribir. Una ficha por producto puede reutilizarse solo con asignación explícita validada a cada publicación/variante.

Captura asesora: `catalog_snapshot_id/hash`, `captured_at`, origen y destino con IDs, todas las filas observadas y sus estados/synced_at, referencias de ficha + contenido congelado, incidencias `unmapped/missing_state/stale/missing_document/roster_unproven`. Disponibilidad numérica no sustituye compatibilidad. No calificar “completo” por presencia de una foto o product_type: SP-API listings conserva estado/product_type, no ficha comercial (`app/spapi/listings.py:81`).

## Interfaz draft/biblioteca v1

`fabrica_web.previsualizar` ya produce plan serializado + huella (`app/fabrica_web.py:140`); `_arma_plan` lee biblioteca y términos (`tools/fabrica_campanas.py:508,593`). Evaluación asesora aparte recibe **snapshot exacto del plan**, huella, publicaciones, semillas por rol, y snapshot de las filas de biblioteca usadas (id, tipo_producto, platform, texto, origen, updated_at si existe). Hoy `_biblioteca` lee solo textos (`:436`): ampliar solo lector asesor para procedencia; no modificar semillas silenciosamente.

Firma propuesta: `prepare_draft_assessment(conn, plan_snapshot, library_snapshot, document_versions) -> AssessmentInput`. Después `assess(input) -> assessment`, persistido aparte. UI presenta categorías/plantillas, sin texto generado por Jev ni filtro automático. Cambio en plan/fichas/biblioteca produce nueva huella; evaluación previa permanece histórica y se muestra desactualizada. GET del preview no llama a Jev; acción explícita de evaluación o worker separado. Para draft aún sin campaña, universo es la selección declarada del plan, no anuncios Amazon existentes; declarar esa diferencia.

## Censo agregado preparado (sin textos privados)

Ejecutar con lector y transacción read-only; no retorna SKU, ASIN, nombres ni textos. Estados conocidos se excluyen solo al interpretar, no mediante INNER JOIN que esconda faltantes.

```sql
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SELECT e.platform, count(*) AS anuncios,
 count(*) FILTER (WHERE s.status='ENABLED') AS enabled,
 count(*) FILTER (WHERE s.ad_entity_id IS NULL) AS sin_estado,
 count(*) FILTER (WHERE s.synced_at < now()-interval '48 hours') AS obsoletos,
 count(*) FILTER (WHERE l.id IS NULL) AS sin_listing,
 count(*) FILTER (WHERE p.id IS NULL) AS sin_producto,
 count(DISTINCT e.parent_id) AS ad_groups
FROM ad_entity e
LEFT JOIN ad_entity_state s ON s.ad_entity_id=e.id
LEFT JOIN listing l ON l.id=e.listing_id AND l.platform=e.platform
LEFT JOIN product p ON p.id=l.product_id
WHERE e.kind='product_ad'
GROUP BY e.platform;
SELECT g.platform, count(*) AS roles,
 count(*) FILTER (WHERE NOT EXISTS (
  SELECT 1 FROM ad_entity a
  WHERE a.parent_id=r.ad_group_ad_entity_id AND a.kind='product_ad'
 )) AS roles_sin_anuncios_observados
FROM campana_grupo_rol r JOIN campana_grupo g ON g.id=r.grupo_id
GROUP BY g.platform;
SELECT platform, count(*) AS publicaciones,
 count(DISTINCT product_id) AS productos,
 count(*) FILTER (WHERE seller_sku IS NULL OR btrim(seller_sku)='') AS sin_sku
FROM listing GROUP BY platform;
ROLLBACK;
```

Este censo estima carencias observadas; no certifica catálogo exhaustivo ni cuenta fichas propuestas todavía inexistentes.
