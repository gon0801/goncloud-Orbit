# Rescate de fichas y juicios de Jev (jev-ads-02)

## Qué pasó

En producción (base `orbit` del contenedor `orbit-db-1`, servidor `goncloud`) tres
tablas de Jev quedaron vacías el 2026-10-06 entre las 17:03:45 y las 17:04:04 UTC.
Medido en solo lectura el 2026-10-12:

| Tabla | En producción hoy | En el respaldo del 2026-10-06 03:30 UTC |
| --- | ---: | ---: |
| `jev_ficha_version` | 0 | 868 |
| `jev_ficha_revocacion` | 0 | 0 |
| `jev_revision` | 10 o más, todas `sujeto_tipo = 'lote'` | 24, ninguna `lote` |
| `jev_par_evento` | 0 | 11272 |

El respaldo diario rota a los 14 días (el del 2026-10-06 se borraba el 2026-10-20),
así que lo primero fue apartarlo. Las cuatro tablas son append-only: aceptan INSERT y
rechazan UPDATE, DELETE y TRUNCATE, así que la carga no tiene reversa — por eso el
ensayo va antes y la carga es una sola transacción: o entra todo, o no entra nada.

## Scripts

Todo se corre con `cd` a esta carpeta. Los scripts corren en la Mac y hacen todo por
`ssh goncloud`; ningún dato de filas sale del servidor.

- `apartar.sh` — copia `backups/orbit_2026-10-06` a `backups/apartado_jev_2026-10-06`
  (nombre fuera del patrón de rotación), pasando por una `.parcial` y verificando el
  sha256 del `.dump` antes de renombrar y otra vez al final. Ya se corrió una vez;
  es seguro correrlo de nuevo (si el apartado ya existe, solo verifica).
  Última línea: `APARTADO OK <sha256>` o `APARTADO MAL`.
- `rescate.sh <ensayo|prod> [--acepto-escribir-en-produccion]` — el rescate:
  1. **Origen**: verifica el sha256 del `.dump` apartado y deja la base temporal
     `orbit_rescate_jev` restaurada con exactamente 868 / 0 / 24 / 11272 filas y 0
     revisiones `lote` (si los conteos no dan, corre `limpiar.sh` y reintenta una vez).
  2. **Destino**: con `ensayo`, borra y recrea `orbit_rescate_ensayo` con el respaldo
     diario más nuevo; con `prod`, exige la bandera y un `ENSAYO_SALIDA` con
     `RESCATE ensayo OK` de menos de 2 horas, y el destino es `orbit`.
  3. **Guardas** (si alguna falla, no escribe): tablas vacías salvo revisiones `lote`
     — o la carga ya completa, caso en que no carga y solo compara hashes —; los 11
     triggers de usuario en `tgenabled = 'O'`; columnas idénticas a las esperadas en
     destino y en `orbit_rescate_jev`; candado `/tmp/jev-senales.lock` abrible.
  4. **Hashes esperados** (md5 por tabla, con zona UTC fija) calculados en el origen.
  5. **Datos**: un `mktemp` en el servidor (permisos 600) con `pg_dump --data-only` de
     `jev_ficha_version` y `jev_revision` más un `COPY` de `jev_par_evento` ordenado
     por tipo (`intencion`, `resultado`, `reutilizacion`) para respetar el trigger de
     encadenado. `jev_ficha_revocacion` no se carga (0 filas en el respaldo) y así su
     secuencia no se toca. El archivo se borra con `rm -f` al terminar.
  6. **Carga** en UNA transacción: `BEGIN`, zona/fecha UTC, `LOCK TABLE`, foto de las
     revisiones `lote` en una tabla temporal, `DISABLE TRIGGER jev_revision_tiempos`
     (el único, para conservar `created_at`), los datos, `ENABLE TRIGGER`, un `DO` que
     hace `RAISE EXCEPTION` si los conteos, los tres hashes, la foto `lote` o los
     triggers no cuadran, y `COMMIT`. Con `prod` la carga corre bajo
     `flock -n` del candado del job `jev-senales` (si el job está corriendo sale con
     código 75 y no escribe).
  7. **Segunda lectura** de los tres hashes en el destino, ya después del `COMMIT`.
  8. Última línea: `RESCATE <modo> OK`, `RESCATE <modo> FALLA: <causa>`,
     `RESCATE <modo> FALLA: JOB CORRIENDO` (código 75) o
     `RESCATE <modo> CARGADO CON DIFERENCIA: <tabla>`.
- `limpiar.sh` — borra solo las bases temporales `orbit_rescate_jev` y
  `orbit_rescate_ensayo`, por esos dos nombres exactos. No toca la carpeta apartada.

## Cómo se corre

```bash
bash apartar.sh                                  # una vez (ya hecho; ver apartar.salida.txt)
bash rescate.sh ensayo > ensayo.salida.txt 2>&1  # ensayo: debe cerrar en RESCATE ensayo OK
# produccion, solo con el PR mergeado y aprobado y el ensayo de menos de 2h:
ENSAYO_SALIDA=<salida del ensayo> bash rescate.sh prod --acepto-escribir-en-produccion
bash limpiar.sh                                  # al final, con el rescate confirmado
```

Las salidas que quedan en el repo traen solo conteos, hashes y nombres de tabla y de
trigger; de un error de la base se guarda solo su primera línea.
