# ORBIT 19 / 0.2 — Migraciones reservadas y rollback

Cerrado 2026-09-06 21:31 UTC (`confirmacion.md`). 0018 sellada. 0019
aplica en A.1; 0020 en B.1.

0018 queda sellada. No se edita.

## Reserva de numeros

| N | Alcance | Quien la aplica | Condicion |
|---|---|---|---|
| **0019** | Grupo/publicacion v2: PK por listing, margen nullable, origen de objetivo | A.1 | D2/D3 cerrados |
| **0020** | Tabla append-only de metricas de producto anunciado (Ads) | B.1 | fuente Ads **verificada** en 0.3 |
| **0021** | (opcional) snapshot de disponibilidad comercial | B.3 | fuente stock verificada; si no, no se reserva en firme |

Si 0.3 deja Ads `no_verificada`, **0020 no se escribe**. No hay hueco
vacio a proposito: el numero se confirma al abrir B.1.

Hoy el ultimo archivo en `migrations/` es `0018_fabrica_campanas.sql`.
A.1 toma **0019** porque es el siguiente archivo libre (F1 ya aplico 0018;
F2 no ha escrito SQL). FABRICA 01 F2 **ya no reserva 0019**: su migracion
de `harvest_job` se numera al aplicar F2, posterior a 0019/0020 (y 0021
si B.3 existe). Verificado 2026-09-06: no hay `0019_*.sql` en el arbol.

## 0019 — cambios previstos (no implementados)

`campana_grupo_producto`:

- PK pasa de `(grupo_id, product_id)` a `(grupo_id, listing_id)`.
- UNIQUE `(grupo_id, product_id, listing_id)` innecesario si listing_id
  ya identifica. UNIQUE `(grupo_id, seller_sku)` para no duplicar el
  mismo SKU en el grupo.
- `margen_neto_pct` pasa a NULL permitido.
- Trigger `campana_grupo_producto_listing` se conserva: listing del
  producto y de la plataforma del grupo, seller_sku coincidente.

`campana_grupo`:

- `target_procedencia` sigue NOT NULL (texto).
- `fraccion` y `target_derivado_pct` pasan a NULL permitido cuando
  `origen=manual_lanzamiento`.
- Columna nueva `target_origen TEXT NOT NULL DEFAULT 'margen_medido'`
  con CHECK `IN ('margen_medido','manual_lanzamiento')`.
- `target_acos_pct` sigue NOT NULL > 0 (siempre hay un objetivo
  explicito, medido o manual).

CHECK: si origen=margen_medido entonces fraccion y derivado NOT NULL.
Si origen=manual_lanzamiento entonces fraccion y derivado NULL.

Filas v1 existentes (hoy cero) cumplen el default margen_medido.

## Rollback / deshabilitar creacion v2

No restaurar binario v1 puro: un lote v2 quedaria ileible.

Mecanismo propuesto (cerrar prueba en A.1):

1. Setting `fabrica.creacion` en `config_version` = `v1` | `v2`.
   Ausente = `v1` (no se inventa v2 por default).
2. Con `v1`: API/CLI rechazan `listing_ids` y `--listing-ids` (422) y
   no insertan `schema_version=2`. GET catalogo puede seguir mostrando
   publicaciones (lectura).
3. Lector, `--registrar`, `--reconciliar`, `--desarmar` y
   `POST /lotes/{lote}/pausar|reconciliar|registrar` aceptan v1 y v2
   **siempre**, independiente del setting.
4. Ensayo A.5: lote v2 parcial (pasos applied + failed) se pausa y
   reconcilia despues de poner `fabrica.creacion=v1`.

Cerrar 0.2 exige este mecanismo **escrito y con prueba prevista**
(A.1/A.5), no solo D1–D4 confirmadas. Confirmar negocio no sustituye
reserva 0019, lector v1/v2 ni reversa que conserve recuperacion v2.

Reversa de 0019: no se plantea DROP. Es expansiva. Si hay que volver
atras, se deja de crear v2; las tablas se quedan. El lector v2 sigue
sirviendo lotes ya escritos.

## Lotes recuperables hoy

Cero. El ensayo de rollback se hara con lote de staging/sonda, no con
produccion existente.
