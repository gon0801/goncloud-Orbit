# ORBIT 19 A.5 - cierre de integracion y despliegue

Fecha: 2026-09-07 UTC.

## Integracion y calidad

- PR de implementacion: [#187](https://github.com/gon0801/goncloud-Orbit/pull/187).
- Integrado en `master`: `78fcbd76a3f48c00824c5f90d3fdf4c5f86e1a91`.
- Suite completa de CI sobre el ultimo commit de la PR (`746b56a`):
  [run 34088493597](https://github.com/gon0801/goncloud-Orbit/actions/runs/34088493597),
  estado `success`. Ejecuta Python, pre-commit y los candados de calidad.
- No se ejecuto la suite completa localmente. Las comprobaciones locales fueron
  focales: 80 pruebas de fabrica, 44 de plan/UI, Ruff, `node --check` y
  `pre-commit run --all-files`.

La revision independiente correctiva emitio `APPROVE` sobre el rango
`911aaa2..746b56a`: cubre idempotencia CLI, rechazo previo a credenciales,
lectura/reconciliacion/pausa de lote v2 bajo configuracion v1, token y ausencia
de POST de creacion en la recuperacion. El detalle esta en
`../A.R/revision-independiente.md` y `correcciones-review.md`.

## Resguardo y publicacion

Antes de publicar se creo el resguardo:

```text
/mnt/data/appdata/orbit/backups/pre_orbit19_review_20260907-055815.tgz
SHA256 03bcb3d6118bb9d3b75990a0b79739632630ab375409e990abea421ed7d4a015
```

La aplicacion se reconstruyo desde `origin/master` integrado. Solo se actualizo
el servicio `orbit-app`; `bridge` y `accounting` no se tocaron. El contenedor
quedo en ejecucion y `GET /health` devolvio `{"status":"ok"}`.

El chequeo posterior de base devolvio:

```text
t|v1|0|0|0
```

Las columnas son, en orden: `target_origen` presente, interruptor
`fabrica.creacion`, lotes de fabrica, grupos y publicaciones de grupo. Conserva
la creacion en v1 y confirma que no se escribio ningun lote ni grupo durante
la validacion.

## Smoke sin gasto ni mutacion

Se hizo unicamente `GET /api/fabrica/catalogo` y `POST /api/fabrica/plan` para
la publicacion 1249, con modo `shadow`, objetivo manual de lanzamiento de
25.00 % y cinco presupuestos/pujas de preview. El resultado fue schema v2,
objetivo `manual_lanzamiento` y plan de cinco campanas, sin llamar a
`/api/fabrica/crear`. La interfaz encontro 249 productos y 342 publicaciones
seleccionables.

La traza del navegador contiene un solo POST de fabrica, hacia `/plan`; no hay
solicitudes a `/crear`. La base, verificada despues del recorrido, mantiene
los tres conteos en cero indicados arriba. No se creo, pauso ni modifico una
campana real.

## Capturas de produccion

- `selector-productivo.png`: publicacion seleccionable con margen e historial
  ausentes; se muestra el aviso pero no se bloquea el objetivo manual.
- `preview-productivo.png`: preview v2 con ACoS manual, procedencia explicita,
  margen `Sin dato` y el aviso de rentabilidad.

No se inicio la fase B.
