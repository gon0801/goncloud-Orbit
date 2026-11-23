# Lectura previa de produccion e invariantes demostrados (BASE.2)

Bloque `BASE` de `plans/arreglos-05.md`, fila `BASE.2`. Solo lectura.

- Consulta: [previa.sql](previa.sql). Lanzador: [previa.sh](previa.sh), que pasa cada bloque por `docs/evidencia/arreglos-01/lee_prod.sh`.
- Resultado: [previa.txt](previa.txt), 38 bloques, cero errores. Rol `orbit_read`, `transaction_read_only = on`, PostgreSQL 16.15. Solo conteos, modos y fechas.
- SHA del lanzador: `2639ff8f`. Corrida del 2026-11-23 (UTC). La hora exacta esta en el bloque `momento_utc`.

## Invariantes demostrados

Cada fila es un hecho medido. El bloque de la ultima columna depende de el y no debe suponerlo.

| Bloque | Invariante demostrado | Valor observado |
|---|---|---|
| A | Todo envelope historico esta cerrado y en estado terminal. | 0 ciclos con `finished_at` nulo. Estados: `done` 205, `skipped` 2. |
| A | El contador de decisiones no se contradice con las filas. | En los 207 ciclos, `decisions_count > 0` coincide con tener filas en `decision`. 64 ciclos live `done` sin decisiones. |
| A | Hay decisiones live sin desenlace registrado. | 2 ciclos live `done` con decisiones y sin `decision_application` ni `decision_sin_aplicar`. |
| A | No hay claim tomado ni intento abierto. | `ads_optimizer_lock` vacia. Las 540 filas de `apply_attempt` tienen `finished_at` y `ack`. |
| B | No hay negative confirmado por conciliacion. | 0 filas con resultado `ok:reconciliado`. Igual que la foto de diseno. |
| B | Toda aplicacion tiene ACK y verificacion. | 515 filas en `decision_application`, todas con `platform_ack` y `verify_ok`. |
| C | La tabla madre de la reposicion esta vacia y la nueva no existe. | `keyword_archivo_manual` sin filas. 0 IDs duplicados. `keyword_reposicion_intento` ausente. |
| E | Las practicas shadow pendientes de veto estan vencidas. | 31 filas `shadow` `pending_veto`, todas con `vence_el` 2026-11-10 08:40 UTC. |
| E | Hoy no hay clave con historia live y practica shadow en vuelo. | 0 claves. La unica fila live en vuelo vence 2026-11-23 09:40 UTC. |
| F, G | Los inputs congelados tienen formas distintas por epoca. | 2784 decisiones `bid`. Solo 63 traen `caso` (desde TEMPORADA I) y ninguna trae `inputs.caso.version`. `target_snapshot` aparece en 612 con procedencia `margen_plataforma` y en 61 con `goal_campana`. |
| H | El uso real del cupo esta lejos de 150. | Maximo de intentos `bid` `normal` por ciclo: 45 en `amazon_mx` y 24 en `amazon_us`. Promedio 6 y 5. Topes configurados en 1,000,000. |
| H | No hay decisiones de temporada que clasificar. | 0 decisiones con motivo `entra`, `ajusta` o `sale`. Las cuatro tablas `temporada_*` tienen 0 filas. |
| I, J | Los modos en uso son los de la tabla de abajo. | 60 versiones de `config_version`. |

## Modos en uso

| Clave | Valor |
|---|---|
| `precio_modo_global` | `shadow` |
| `precio_modo_universo` | `live` en los cuatro universos. El modo efectivo sigue shadow. |
| `ads_temporada_amazon_mx`, `ads_temporada_amazon_us` | `live` |
| `ads_bid_politica_*` | `niveles_v3` |
| `ads_producto_meta_*` | `live` |
| `ads_producto_pausa_*` | `shadow` |
| `ads_producto_economia_*` | `producto_directo_v1` |
| `ads_apply_cap_*` | `1000000` en bid, pause, harvest y negative, por plataforma |
| `ads_recuperacion_*` | sin clave en la configuracion vigente |

Goals: 1 live en `amazon_mx`, 1 live en `amazon_us` y 7 live sin plataforma.

## Comparacion con la foto de diseno

La foto de `docs/evidencia/arreglos-05/base/estado-prod.txt` es del 2026-11-16 01:43 UTC. Lo que cambia:

- Ciclos `live` `done`: de 159 a 175. `shadow` `done` y `off` `skipped` sin cambio.
- Cola: aparecio 1 fila `live` `pending_veto`. Las demas cuentas son iguales, incluidas las 31 shadow pendientes de veto.
- Configuracion: aparecen `ads_temporada_amazon_mx` y `ads_temporada_amazon_us` en `live`. Las demas claves de modo y los topes son iguales.
- Esquema: cuatro tablas `temporada_*` nuevas, con 0 filas.

Lo que no cambia: reposicion vacia, cero negatives reconciliados, precios en shadow efectivo, pausa por producto en practica.

## Migraciones aplicadas y numero libre

Las doce migraciones de la base integrada dan `True` en su sonda `sonda-aplicada` (bloques `sonda_*` de `previa.txt`): 0083, 0084 y 0085 (ARREGLOS 01), 0086 (ARREGLOS 02), 0087 a 0091 (ARREGLOS 03), 0092 y las dos 0093 (TEMPORADA 01 y formula S4).

**Numero libre: `0094`.** `0093` esta tomado dos veces en master. El instalador (`migraciones_de` en `tools/despliegue/comun.sh`) aplica por nombre de archivo, y cada 0093 tiene su sonda y su reversa propias. La primera migracion de ARREGLOS 05 sale con `0094_`, su `0094_reversa_` y su cabecera `-- sonda-aplicada:`, salvo que otro cambio integrado antes tome el numero.
