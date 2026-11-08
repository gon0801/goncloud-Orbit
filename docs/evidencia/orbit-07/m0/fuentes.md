# M0: fuentes de la proteccion de venta reciente

Consulta: 2026-11-07 (UTC). Base: `f86ed7dcd77f0af7b540217c3df9422ebe5c7154`.

| Fuente | Papel en M1 | Ventana / grano | Notas |
|---|---|---|---|
| `app/lecturas_caso.py` `_SQL_PRECIO` | Lee pedidos D-9..D-1 y su cobertura (la misma ventana inmadura del caso) | Por `ad_entity_id` (keyword/product_target) y plataforma | `DIAS_INMADUROS_DESDE=9`; el número ya existe en producción |
| `v_metric_latest` (`migrations/0001_initial.sql`) | Ultima observacion por (entidad, fecha) | `metric_date`, `observed_at`, `orders`, `metric_currency` | Append-only: corregir es insertar otra observacion |
| `ad_entity` | Identidad y plataforma de la hoja | `kind IN ('keyword','product_target')` | La lectura se limita a plataformas `amazon_mx`/`amazon_us` |
| `app/optimizer/windows.py` `ventana_cortes` | Ventana madura de la pausa | 30 dias inclusive, fin `min(max-3, D-10)` | Al vencer el episodio, el ancla ya entra a esta ventana |
| `app/apply_cola.py` `_SQL_VENCIDAS` / `_SQL_CLAVES_BLOQUEADAS` | Filas de corte en vuelo y released | `apply_queue` por plataforma | M1 distingue `released` de veto/applying |
| `app/optimizer/politica.py` R0/R8 | Pausa por umbral y venta reciente historica | Caso puro | R0 decidia antes que R8: la diferencia que M1 corrige |
| `app/apply_cola.py` `_revalida_pause` | Re-decision al liberar | Evidencia fresca al reloj de liberacion | M1 le pasa la misma subpolitica pura |
| `proteccion_venta_reciente` (`migrations/0081_orbit07_proteccion_venta.sql`) | Episodio append-only | `(platform, ad_entity_id, version, ancla)` | Ancla y vencimiento fijos; cierre derivado |

## Fuentes de fases posteriores (inventario, sin construir)

- `app/ads/reports.py`: ingesta de reportes y watermark; base de la cobertura.
- `app/evaluacion_catalogo.py`: colapso por identidad y madurez por fecha (M3/M4).
- `app/economia_observada.py`: economia por producto (M2).
- `app/impulso_io.py`: impulso abierto y su vigia (M3/M4).
- `app/retiro_anuncios.py`: unico PUT y reversa del retiro (M4).

## Evidencia de produccion y limite de acceso

La revision independiente Astra ejecuto el SELECT inicial con `orbit_read` y
`READ ONLY` (2026-11-08). Devolvio dos hojas por mercado con pedidos recientes,
lectura completa D-9..D-1 y cero pedidos en la ventana fija D-39..D-10; el
resultado redactado esta en
[`produccion-2026-11-08.json`](produccion-2026-11-08.json). Acredita que el
disparador de M1 existe en produccion. No acredita pausas elegibles ni perdidas:
la ventana fija no es el contrato por hoja `min(max(metric_date)-3, D-10)` y la
consulta no aplica todos los filtros del ciclo. La version corregida queda en
[`consulta-pedidos-recientes.sql`](consulta-pedidos-recientes.sql); su
ejecucion con el contrato real sigue pendiente porque este encargo no tiene
acceso de lectura a produccion (el buzon solo habilita `ci-github`). No se
inventaron resultados de produccion.
