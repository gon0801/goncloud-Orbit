# E/0.2 — confirmación contra el HEAD para Jev Ads 01

Fecha UTC: 2026-10-03, 22:53-22:58. Rama `docs/jev-ads-b1`, HEAD
`fa2039ee94ddc0742f1f3895f267fccbc59c8aa9`. Todo lo que sigue es solo lectura
del árbol y de la base, con la receta del diseño (transacción
`REPEATABLE READ READ ONLY` que termina en `ROLLBACK`). Ningún término, SKU,
ASIN ni nombre sale en esta evidencia; solo agregados.

## 1. Mapa de archivos por área del plan

Verificado con `test -e` sobre el HEAD. Los módulos Jev no existen todavía:
se crean en 1.1 a 1.4 y eso es el estado esperado.

| Área | Archivo previsto | Estado en HEAD |
| --- | --- | --- |
| Dominio | `app/jev_ads.py` | NO existe (se crea en 1.1) |
| Dominio | `tests/test_jev_ads.py` | NO existe (1.1) |
| Catálogo | `app/jev_catalogo.py` | NO existe (se crea en 1.2) |
| Catálogo | `tests/test_jev_catalogo.py` | NO existe (1.2) |
| Jev | `app/jev_juicios.py` | NO existe (se crea en 1.3) |
| Jev | `tests/test_jev_juicios.py` | NO existe (1.3) |
| Persistencia | migración + pruebas de esquema | 0049 libre (ver §2) |
| Operación | `tools/jev_ads.py` | NO existe (se crea en 1.4) |
| Operación | `tests/test_jev_cli.py` | NO existe (1.4). Ojo: existe `tests/test_cli.py`, que prueba `app/cli.py`; es otro archivo |
| Cortes | `app/api_dashboard.py` | EXISTE |
| Cortes | `app/templates/cortes.html` | EXISTE |
| Cortes | `tests/test_api_dashboard.py` | EXISTE |
| Fábrica | `app/fabrica_web.py` | EXISTE |
| Fábrica | `app/api_fabrica.py` | EXISTE |
| Fábrica | pantalla: `app/templates/fabrica.html` + `app/static/js/fabrica.js` | EXISTEN (el plan pide localizarlas antes de editar: localizadas) |
| Fábrica | tests | EXISTEN: `tests/test_api_fabrica.py`, `tests/test_ui_fabrica.py`, `tests/test_fabrica_*.py` |

## 2. Siguiente número de migración: 0049, confirmado libre

```
ls migrations/ | tail -4
  0045_sin_aplicar_choque_clave.sql
  0046_target_acos_ciclo.sql
  0047_familias.sql
  0048_margen_familia.sql
ls migrations/0049_*  → sin coincidencias (0049 LIBRE)
```

`0047_familias.sql` y `0048_margen_familia.sql` están ocupadas en este HEAD.
No se creó la 0049 en este bloque. El texto del plan dice "próxima migración
libre después de `0046`" porque se escribió cuando 0047 estaba libre; el propio
plan resuelve el caso ("la migración toma el próximo número libre en ese
momento; no reservar 0047 por adelantado"). El número concreto para 1.2 será
**0049**. Es un cambio de dato, no de contrato, y queda documentado aquí antes
de codificar.

## 3. Esquema de roles vigente

`migrations/0001_initial.sql` (bloque 1443-1456) crea los cuatro roles grupo
`NOLOGIN`: `app_ingest` (sincronizadores), `app_decide` (motores),
`app_read` (dashboard, análisis, agentes externos) y `app_admin` (config
humana). Los `GRANT` repartidos por las migraciones: `app_ingest` 31,
`app_read` 26, `app_decide` 18, `app_admin` 11. Contra la base viva
(`select_mercados.salida.txt`, §1, sesión con rol efectivo `app_read`): los
LOGIN vigentes son `orbit_ingest` ∈ `app_ingest`, `orbit_decide` ∈
`app_decide`, `orbit_read` ∈ `app_read`, `orbit_admin` ∈ `app_admin` +
`app_decide`, y `orbit_test` ∈ los cuatro grupos (rol de pruebas, pendiente de
revocación según ORBIT 05 preflight). Para Jev, el plan exige un rol asesor
sin permisos sobre cola, ledger, decisiones, goals ni bibliotecas; ese rol
entra con la migración de 1.2 y hoy no existe, en línea con el estado "sin
implementar" del §1.

## 4. Procedencia de fichas y roster: `desconocido` donde no hay prueba

- **Fichas aprobadas reales: 0.** El registro de fichas se crea en 1.2
  (migración 0049); hoy no existe tabla ni comando administrativo. La fuente de
  cada ficha del piloto será la ficha manual mínima versionada del diseño
  (validada al cargar, hash por revisión), aún sin ejemplares. La base lo
  confirma: `select_mercados.salida.txt` §2 consulta `jev_ficha_version`,
  `jev_ficha_revocacion`, `jev_revision` y `jev_par_evento` y ninguna existe
  todavía.
- **Fichas sintéticas existentes:** `docs/evidencia/jev-ads-01/prototipo/fixtures.json`,
  16 casos author-written con `provenance: "synthetic author-written fixture
  v1"`. Prueban el contrato del prototipo, no productos reales.
- **Roster: `desconocido`.** El censo observa MX 342 publicaciones de 249
  productos y US 176 de 119, todas con producto; pero la sincronización no
  demuestra universo completo (estados ausentes y padres omitidos son
  posibles). Grupos con anuncios ENABLED/PAUSED y catálogo incompleto: MX 1 de
  33, US 24 de 48. Etiqueta aplicable: `roster_unproven`; el registro por
  mercado queda literalmente en `desconocido` en `select_mercados.salida.txt`
  §3 (MX y US).

## 5. Disponibilidad de casos reales para el piloto (SELECT por mercado)

Ejecutado dentro de `orbit-app-1` en `goncloud` vía SSH, con `app.db.connect` y
`ORBIT_DSN_READ`, transacción read-only con `ROLLBACK` final y
`statement_timeout` de 15 s. Script y salida completos en este directorio
(`censo_02.py`, `censo-readonly.json`, observado 2026-10-03T22:53Z). Criterios
de selección de casos para el piloto, sin enviar nada:

- **Negativos (higiene):** términos no-ASIN con costo > 0 y sin orders.
- **Harvest y semillas:** términos no-ASIN con orders ≥ 1. Los ASIN-like quedan
  fuera por la regla sealed own-ASIN de `search_term_observation`.

| Mercado | Términos distintos | ASIN-like | Con costo (candidatos negativo) | Con orders ≥ 1 (candidatos harvest/semilla) | Últimos 30 días | Rango observado |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| MX | 1,360 | 339 | 1,021 | 49 | 687 | 2026-06-19 a 2026-10-02 |
| US | 2,631 | 1,017 | 1,614 | 48 | 1,092 | 2026-06-19 a 2026-10-02 |

Bibliotecas y destinos, por mercado (ambas vacías hoy):

| Fuente | MX | US | Lectura |
| --- | ---: | ---: | --- |
| `negative_biblioteca` | 0 filas | 0 filas | casos de negativos de biblioteca: no disponibles; los casos del piloto saldrán de términos observados |
| `keyword_biblioteca` | 0 filas | 0 filas | semillas de biblioteca: no disponibles; candidatos reales = términos con orders ≥ 1 (49 MX, 48 US) |
| `harvest_excepcion` | 0 filas | 0 filas | destinos congelados: ninguno |
| Roles `campana_grupo_rol` | 5 roles, todos con anuncios observados | 0 roles | en US no hay grupo de fábrica que evaluar como destino; en MX los roles cubren auto_discovery, category phrase/broad/exact y product_targeting |
| Keywords en grupos (EP) | 377 | 412 | semillas ya vivas en campañas, revisables contra el nuevo plan sin recalcular históricas |

El conjunto real que el piloto podrá etiquetar (3.1) queda fijado por estos
criterios y denominadores: hasta 1,021 candidatos negativo en MX y 1,614 en US,
más 49 y 48 candidatos harvest/semilla, con la ventana de 30 días como recorte
fresco (687 y 1,092 términos). La selección final y el presupuesto son de 3.1;
aquí solo se confirma disponibilidad y criterios.

Hay además casos históricos ya decididos que el piloto puede re-etiquetar como
contraste (`decision` con kind negative/harvest, `select_mercados.salida.txt`
§5-6): MX 6 negativos sobre 2 términos (2026-08-24) y 8 harvest sobre 6
términos (2026-08-28 a 10-01); US 46 negativos sobre 11 términos y 3 harvest
sobre 2. En la ventana de 30 días quedan 5 harvest MX, 4 negativos US y 2
harvest US con ≥ 10 días de maduración. Los harvest de MX son los únicos con
casos recientes maduros; el histórico de negativos US es cuantioso pero ya
tiene más de 10 días.

## 6. Spec sin delta

`diff -q` entre `docs/superpowers/specs/2026-10-03-jev-ads-design.md` en este
HEAD y la copia del repo de diseño (`/Users/dn/dev/wt/jev-ads-design`) dio
archivos idénticos; ídem `plans/jev-ads-01.md`. El spec sigue **sin delta**.
La única desviación de datos es el número de migración (0049 en lugar del 0047
que el texto del plan vio al escribirse), previsto por el propio plan y
registrado en §2 antes de codificar.

## 7. Anomalía de ejecución: dos actores en el mismo worktree

Durante este bloque aparecieron en `ejecucion/0.1` y `ejecucion/0.2` archivos
que esta sesión no escribió (`fallo-pre-379.txt`, `pasa-en-head.txt`,
`mapa_archivos.txt`, `select_mercados.sql`, `select_mercados.txt`,
`select_mercados.salida.txt`), producidos entre 22:54 y 22:55 UTC por otro
agente que trabajó en paralelo sobre el mismo worktree y la misma base. Se
revisaron y son correctos y complementarios; este NOTAS los integra:

- `fallo-pre-379.txt`: reproduce el fallo de la prueba en un clon desechable
  en el estado previo a #379 (`edd154e`), demostrando el "falla antes" del DoD
  de 0.1 sin tocar este árbol.
- `pasa-en-head.txt`: la misma prueba pasa en HEAD, con datos de la PR #379
  (merge `8d4890adc442d4405d82d8a36707c743cf8a9bcc`, 2026-10-02) y el diff del
  arreglo.
- `mapa_archivos.txt`: mapa de existencia coherente con el §1 de este NOTAS
  (incluye el matiz de que `templates/cortes.html` suelto en la raíz no existe;
  la ruta real es `app/templates/`).
- `select_mercados.sql` y `select_mercados.salida.txt`: censo read-only como
  `app_read` con `ROLLBACK`, que aporta roles LOGIN reales, tablas Jev
  ausentes, roster `desconocido` y decisiones históricas etiquetables,
  integrados en §3, §4 y §5. La corrida previa `select_mercados.txt` usó el
  usuario `orbit` (más privilegiado de lo necesario); quedó como crudo
  histórico y la válida para citar es la de `app_read`.

Ningún archivo fue sobrescrito entre actores: este NOTAS es el único resumen y
no colisiona con los crudos. El commit de este bloque lleva todo el directorio.
Se reporta a quien revisa (VEREDICTO) para que evite un doble LISTO o doble
commit del mismo encargo.

## Comandos ejecutados

1. `git rev-parse HEAD` → `fa2039ee94ddc0742f1f3895f267fccbc59c8aa9`.
2. `test -e` por archivo del §1 (listado arriba).
3. `ls migrations/` y `ls migrations/0049_*` (sin coincidencias).
4. `sed -n '1443,1456p' migrations/0001_initial.sql` (bloque de roles).
5. `grep -rhn "GRANT" migrations/*.sql | grep -o "TO app_[a-z]*" | sort | uniq -c`
   → app_ingest 31, app_read 26, app_decide 18, app_admin 11.
6. `diff -q` spec y plan contra el repo de diseño → idénticos.
7. `ssh goncloud "docker exec -i orbit-app-1 python -" < censo_02.py`
   → `censo-readonly.json` (read-only, ROLLBACK final).
8. Comandos 1 a 7 del actor paralelo, registrados por él en
   `select_mercados.salida.txt` y `fallo-pre-379.txt` (ver §7).

## No enviado, no llamado

Este bloque no llamó a TypeSafe, no envió términos y no escribió en la base.
La corrida del censo quedó en transacción read-only con `ROLLBACK`.
