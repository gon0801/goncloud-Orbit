# ORBIT 17 — Brief para GLM: tarea 6 de FABRICA 01

Implementa **solo la tarea 6** de `plans/fabrica-01.md`: la herramienta que lee la
base y presenta una simulacion completa del grupo de cinco campanas con su huella.
Una rama, un PR contra `master`. Las mutaciones y su reversa se entregaran juntas
en las tareas 7–9; esta entrega termina en un dry-run utilizable.

## Base y lectura

Las tareas 4–5 quedaron fusionadas mediante **PR #174**, commit
`a54a15f8a70b68aededb01b191b65355d677b59c`, con CI verde. Revision final: 66 tests
con Postgres real, sin skips; Ruff y pre-commit verdes. Incluye el filtro ASIN de
negativos y el test del INSERT de goals en MXN/USD.

**Despliegue verificado:** 2026-09-06 03:23 UTC, contenedor `orbit-app-1`
recreado (`fc3cbf0b6b81`), imagen `3b6cbfaffc2b`. 68 archivos del contenedor
identicos al commit, health OK, `crea_goal` importable, filtro ASIN y target puro
comprobados. Base y cron sin cambios; el esquema `fabrica_lote` ya existia.
Respaldo y reporte del despliegue en el servidor:
`/mnt/data/appdata/orbit/backups/deploy-174-20260906T032019Z/verified.json`.

Lee, en este orden:

1. `AGENTS.md`, `docs/CONTEXTO.md` y `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`.
2. `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md`.
3. `plans/fabrica-01.md`: restricciones globales, tarea 6 COMPLETA, interfaces de
   tareas 7–10 y evidencia de tareas 1–5.
4. Los archivos reales `app/fabrica_plan.py`, `app/goals_write.py`,
   `migrations/0018_fabrica_campanas.sql`, `tests/test_fabrica_migracion.py`,
   `tests/test_fabrica_plan.py` y `tests/test_architecture.py`.

Los bloques del plan son la guia de implementacion y contienen codigo y tests.
Los archivos fusionados son la fuente de las firmas actuales: no los reemplaces
con copias antiguas del plan. En particular, la vista devuelve margen en la
moneda del ledger (MXN tambien para US); el porcentaje NO se filtra por USD.

```bash
git fetch origin
git switch -c fabrica-01-6-dry-run origin/master
git log -3 --oneline
```

Comprueba que la rama contenga #174 y las tareas 2–3. Preserva archivos ajenos.
Si falta una dependencia, identifica la discrepancia antes de programar.

## Entrega y archivos

| Archivo | Trabajo |
|---|---|
| `tools/fabrica_campanas.py` | Crear: consultas, validacion, argumentos CLI, salida y huella. Un solo archivo, ejecutable por stdin. |
| `tests/test_fabrica_campanas.py` | Crear: SQL con Postgres real, dry-run sin HTTP y ejecucion del CLI. |
| `tests/test_architecture.py` | Solo si hace falta incorporar el candado del guard final o proteger la lectura de esta entrega. |
| `plans/fabrica-01.md` | Decisiones/evidencia de tarea 6, logs rojos y estado al cumplir el DoD. |
| `docs/CHAT-CONTEXT.md` | Nota de negocio sobre la simulacion disponible; ORBIT 17 sigue en curso. |

Mantiene los nombres y firmas de `Interfaces / Produces` de la tarea 6:
`_dsn_read`, `_fraccion`, `_productos`, `_terminos_producto`, `_biblioteca`,
`_existentes`, `_arma_plan`, `_lote_nuevo`, `_log`, `Abortar`, `_parser`, `main`
y los SQL `_SQL_TERMINOS` / `_SQL_TERMINOS_EXACT`. Las tareas 7–9 los consumen.

## Comportamiento que debe quedar probado

- **Solo lectura de DB y cero HTTP**, incluido OAuth, perfiles y LIST de Amazon.
  Usa `ORBIT_DSN_READ`; el dry-run no necesita credenciales de Amazon ni DSN de
  escritura. No crea filas en ledger, grupos, goals ni bibliotecas.
- Argumentos de creacion explicitos: plataforma, etiqueta, nombre, productos,
  `--modo shadow|live` y los diez montos (bid y budget por cada rol).
  Valida con las funciones del nucleo; no inventes defaults ni dupliques formulas.
- Fraccion desde la ultima `config_version` para la plataforma seleccionada.
  Ausente o corrupta: error explicito; nunca sustituir por 0.5.
- Productos: todos los IDs pedidos deben existir y tener un unico listing en
  la plataforma, `seller_sku` no vacio y margen medible. Aborta nombrando al
  producto si falta algun dato o tiene varios listings. No elijas uno a ciegas.
- Target del grupo y semillas a traves de `app.fabrica_plan`, conservando su
  filtro ASIN. Un dato faltante no es cero ni una constante de respaldo.
- Historial de terminos: solo ad groups de campanas vinculadas a los listings
  solicitados por product ads, sin UNION del grano campana. Colapsa por entidad,
  termino y fecha; ultima `observed_at`, con `source_report_id DESC NULLS LAST`
  para desempatar. Una reobservacion reemplaza el dato del dia al agregar.
- Dos consultas separadas para terminos: historial `[D-105, D-15)` y candidatos
  exact `[D-39, D-9)` (hasta D-10 inclusive). El margen por producto lo entrega
  la vista 0018 con su propia ventana: no lo recalcules desde los terminos.
- Fija el dia de referencia en UTC tambien en SQL. El bloque del plan usa
  `CURRENT_DATE`; ajustalo al contrato UTC del proyecto y documenta el cambio.
  No permitas que la zona horaria de la sesion cambie las ventanas silenciosamente.
- Biblioteca filtrada por plataforma y tipo; keywords con orders >= 1.
  Campanas existentes se reportan con su estado; no se pausan ni se adoptan.
- Salida: cinco roles en su orden fijo, nombres, moneda, budgets, bids, modo,
  productos/SKUs, target con procedencia y cantidad de semillas (tambien cero),
  campanas existentes, huella y evento JSON de dry-run.
- Dinero Decimal para calcular y strings en JSON persistible. Cierra la conexion
  de lectura en exito y en error; no dejes abierta una transaccion al terminar.
- Las opciones futuras (`--acepto-mutacion-real`, `--desarmar`, `--reconciliar`)
  deben rechazarse de forma explicita en esta entrega, sin HTTP ni escritura.
  Usa los rechazos del plan como comportamiento completo de esta fase, no
  respuestas de exito ficticias. No agregues imports de escritura sin uso ni
  `noqa: F401` para reservar codigo de tareas futuras.
- Todas las funciones deben estar definidas ANTES de invocar `main()`; el bloque
  `if __name__ == "__main__":` es lo ultimo. Prueba ejecucion por archivo y por
  stdin, ademas de importar el modulo.

## Proceso y pruebas

Usa `superpowers:executing-plans` si esta disponible. Ejecuta tu mismo la tarea.
Escribe decisiones `D-GLM-6-N` ANTES del codigo en la seccion de evidencia del plan.
Reusa la evidencia de produccion ya registrada; no accedas a goncloud, Amazon ni
AppFlowy. El lead lleva deploy y tracker.

1. Escribe los tests de la tarea 6 y demuestra el rojo contra el codigo anterior.
2. Implementa el minimo para el dry-run completo y registra los ajustes mecanicos
   necesarios para usar fixtures e interfaces actuales.
3. Ejecuta contra **Postgres 16 real**: no reemplaces las consultas por mocks.
   Cubre MXN y USD, productos incompletos y multi-listing, plataforma/tipo ajenos,
   colapso bitemporal, desempate de reportes, grano campana excluido, limites de
   ambas ventanas y biblioteca. Usa fechas UTC coherentes en fixtures y SQL.
4. Prueba el dry-run con HTTP prohibido (un intento debe hacer fallar el test),
   sin DSN ADMIN/INGEST ni credenciales de Amazon. Comprueba la salida y la huella.
   Incluye ejecucion completa del CLI sobre la DB de pruebas; las pruebas con
   conexion falsa no sustituyen a este caso.
5. Demuestra que cada regresion nueva discrimina: corre el test contra el codigo
   anterior o altera temporalmente la logica relevante, nunca la asercion del test.
   Registra el rojo y restaura la logica antes de la verificacion final.

Si una discrepancia exige cambiar una regla o una interfaz sellada, informa al
lead con evidencia y continua las partes independientes. No amplies alcance a
la mutacion, reversa, cambios del motor, migraciones o F2.

## Criterio de cierre

Con el entorno del proyecto activado y `ORBIT_TEST_DSN` de la base de pruebas:

```bash
python -m pytest tests/test_fabrica_campanas.py tests/test_fabrica_plan.py tests/test_architecture.py -q -rs
ruff check .
ruff format --check tools/fabrica_campanas.py tests/test_fabrica_campanas.py tests/test_architecture.py
pre-commit run --all-files
git diff --check
git log --oneline origin/master..HEAD
```

Sin skips de SQL. Reporta conteos reales: el conteo esperado del texto historico
del plan quedo desactualizado. Suite completa en CI segun la politica del repo;
ningun hook omitido, nunca `--no-verify`. No cambies los candados para ocultar fallos.

PR: `feat(fabrica): plan desde la base y dry-run con huella (tarea 6)`.
Commit principal: `feat(fabrica): simulacion de campanas desde la base con huella`.
Usa autoria real de tu ejecucion, sin copiar trailers de otra sesion.

Entrega enlace del PR, SHA base, decisiones/desviaciones, logs rojos, comandos y
resultados finales, estado de CI y ejemplo de salida con datos sinteticos.
Marca tarea 6 completada solo al cumplir su DoD; ORBIT 17 completo sigue en curso.
