# Triage de NO BLOQUEANTE / VERIFICAR — cross-review B5-r1 (G1-G6)

Cierre de ledger B6b (2026-10-04). Fuente: `ejecucion/B5-r1/reporte-g1..g6*.txt`.

- Los 6 reportes contienen **100 hallazgos no bloqueantes** (17 G1 + 20 G2 +
  16 G3 + 14 G4 + 15 G5 + 18 G6): los 8 BLOQUEANTE quedan fuera (7 corregidos
  en B5-r2/r3/r4, squash `58d1ea4`; B6 refutado en VEREDICTO-B5-r1 y convertido
  en la fila R18).
- El VEREDICTO-B5-r1 citó "83"; el conteo completo de los mismos reportes da
  100. Aquí va el censo entero: **100/100 con destino**, ninguno sin resolver.
- Destinos: fila R# de "Seguimientos de la revisión" en `plans/jev-ads-01.md`;
  `resuelto` con el SHA o la ronda que lo demuestra; `descartado` con la razón
  en una línea. Los "VERIFICAR" respondidos por inspección de código citan
  archivo:línea del HEAD `8d8cdca`.

## G1 — dominio (`app/jev_ads.py`, `app/jev_juicios.py`)

| # | Hallazgo (resumen) | Destino |
| --- | --- | --- |
| G1-2 | Validaciones estructurales de `componer` corren tras confirmar revisión y pagar HTTP. | descartado: solo alcanzable con censo malformado a mano (ValueError estructural, bug del llamador); si el corte de R14 toca el orden, se reevalúa ahí. |
| G1-3 | Censo con 2 listings del mismo producto: misma ficha para ambos o `ficha_ausente` perpetuo. | R8. |
| G1-4 | `OverflowError` con entero JSON enorme (10^400) en probabilidades. | descartado: adversarial; la intención queda confirmada y la reanudación reintenta con presupuesto; sin corrupción. |
| G1-5 | `evaluar` sin commit cuando ningún par llega a intención; VERIFICAR el llamador. | resuelto: el CLI corre dentro de `with connect(dsn)` y psycopg3 confirma al salir del bloque (`tools/jev_ads.py:113`); el orden sellado confirma por paso dentro de `evaluar`. |
| G1-6 | Retomar tras ficha vencida cae en "otro payload". | R9. |
| G1-7 | `pares_de`: presupuesto agotado antes de `_exito_previo` pierde reutilización. | descartado: el par queda `juicio_ausente` y la reanudación con presupuesto nuevo lo reutiliza sin HTTP; desajuste transitorio sin decisión encima. |
| G1-8 | Par fallido en origen se vuelve a pagar en destino. | descartado: a lo sumo un par extra por revisión tras fallo del proveedor; el presupuesto acota; "V1 no reintenta" se mantiene dentro del lote. |
| G1-9 | `request_sha256` no incluye opciones/versión y usa forma canónica. | resuelto en `58d1ea4` (B5-r2): prueba con valor literal sensible al contenido; el docstring ya no promete opciones/versión. |
| G1-10 | `_enriquecer` no pisa un `ficha_version_id` viejo sin ficha vigente. | descartado: `censo_grupo` siempre emite `ficha_version_id=None` (NOTAS B2-r1, §Decisiones); el id viejo solo existe en censos de prueba. |
| G1-11 | VERIFICAR `row_factory` de la conexión que llega a `evaluar`. | descartado: el único llamador de `evaluar` es la CLI con tuplas por defecto; el dashboard solo lee y ya se adapta con `_FilasPosicionales` (`app/jev_ads.py:749`); R2 fija la fábrica del asesor en el camino de producción. |
| G1-12 | Sin bloqueo por solicitud; VERIFICAR unicidad (revision, par, ordinal, tipo). | descartado, mitad resuelta: la UNIQUE de `0049` hace fallar al duplicado (`224449f`); el doble HTTP concurrente exige dos operadores con la misma solicitud, imposible hasta R2. |
| G1-13 | VERIFICAR argmax de `choice` y umbral de `confidence`. | descartado: el diseño no exige argmax ni umbral (solo Choice con probabilidades validadas); el umbral se decide con datos del piloto (fila 3.1). |
| G1-14 | Coherencia de semillas no compara `plan_canonico`/`fuentes_semillas`. | descartado: `plan_sha256` se compara en `_abrir_revision` (`app/jev_ads.py:595`) y lo produce `fabrica_plan` desde el canónico; verificar el hash en el asesor duplicaría la fuente (regla 2). |
| G1-15 | Detalle de `respuesta_invalida` sin límite de longitud. | R12 (la pantalla muestra ese detalle; truncar y rotular van con R12; el escape ya está cubierto por autoescape, `app/ui.py:59`). |
| G1-16 | `_vigencia_de_miembros` ignora ficha nueva para miembro sin ficha y cambio de contrato. | R3 (ampliar la vigencia en el bloque de harvest). |
| G1-17 | `leer` N+1; VERIFICAR `created_at` con default. | R13 (el conteo); el default existe desde `224449f` (`0049`) y R1 corrige su valor. |
| G1-18 | Hash de término duplicado vista/transporte; `FichaVersion` falso en la vista. | R14 (unificar helpers al partir el módulo; el `FichaVersion` de la vista jamás llega a `clave_de` ni `pedir`). |

## G2 — catálogo y migración (`app/jev_catalogo.py`, `migrations/`)

| # | Hallazgo (resumen) | Destino |
| --- | --- | --- |
| G2-1 | Trigger `jev_par_evento_encadenado` no compara ordinal; dos resultados por intención. | descartado: el asesor nunca escribe dos resultados en una intención (un intento, un resultado) y la vista toma el éxito por par (B4-r2). |
| G2-2 | `revocar_ficha` sin savepoint aborta la transacción previa. | R7 (ampliar a `revocar_ficha`). |
| G2-3 | `ya_existia=True` con la ficha ya revocada; el contenido no se re-aprueba. | descartado: el hash incluye `observado_at` (`app/jev_catalogo.py:76`); re-aprobar es publicar una versión nueva, conservador y correcto. |
| G2-4 | `registrar_ficha` SELECT+INSERT sin ON CONFLICT. | R7. |
| G2-5 | Corrección no reemplaza a la previa; empate de `created_at` no determinista. | descartado: `ficha_vigente` elige la última por `observado_at`/`created_at` (predicado único, B4-r3 F1); el empate exige dos INSERT en la misma transacción, imposible por CLI. |
| G2-6 | JSON `null` cuenta como valor presente en el discriminador. | descartado: el asesor solo escribe respuestas validadas o error con código; `null` exige escritura manual. |
| G2-7 | `ficha_version_ids` sin verificación de existencia. | descartado: solo escritura manual; el asesor congela las fichas del censo enriquecido y la vigencia marca Obsoleta lo desconocido. |
| G2-8 | `decided_at`/`captured_at` nullable. | descartado: toda escritura pasa por el asesor, que llena ambas; endurecer exige migración sin incidente que la justifique. |
| G2-9 | Encabezado de `0049` promete `<= created_at` y el trigger usa `clock_timestamp()`. | R1 (el COMMENT se re-emite corregido en la migración nueva; `0049` no se edita). |
| G2-10 | La base acepta fichas que el lector no puede cargar. | descartado: la ficha la aprueba una persona; una ficha malformada produce error visible y se corrige revocando (append-only). |
| G2-11 | Typo "jam el parecido" en `COMMENT ON TABLE jev_ficha_version`. | R1 (re-emisión del COMMENT en la migración nueva). |
| G2-12 | Truthiness vs `is not None` en `censo_grupo`. | descartado: cosmético; los ids del LEFT JOIN nunca son 0. |
| G2-13 | Censo vacío indistinguible de grupo inexistente. | descartado: censo vacío da Indeterminado y `exhaustivo=False` es constante en grupos Amazon (NOTAS B2-r1); avisar "no existe" es ergonomía de R2. |
| G2-14 | Datetimes sin zona y fecha de revocación futura. | descartado: los llamadores internos operan en UTC fijo (trigger/conexión); revocar con fecha futura revoca hoy, coherente con append-only. |
| G2-15 | Reversa `0049` sin LOCK TABLE; REVOKE fuera del IF EXISTS. | descartado: la reversa corre solo en rollback asistido y sin escritores (ensayada en B2, NOTAS); la ventana exige un INSERT confirmado durante el DROP. |
| G2-16 | VERIFICAR que el runner excluya `*_reversa_*`. | descartado: convención de deploy documentada (AGENTS.md: las `*_reversa_*` no van al deploy); no hay runner que las numere. |
| G2-17 | VERIFICAR SELECT de `app_admin` sobre `listing`. | resuelto: `GRANT SELECT ON ALL TABLES IN SCHEMA public TO ... app_admin` de 0001 (`migrations/0001_initial.sql:1460`) cubre listing/product; el trigger corre como invocador y las pruebas con `SET ROLE app_admin` pasan. |
| G2-18 | VERIFICAR una fila por `ad_entity_id`. | resuelto: `ad_entity_id BIGINT PRIMARY KEY` (`migrations/0001_initial.sql:645`); es cache declarado, no historial. |
| G2-19 | VERIFICAR row_factory de tuplas en el módulo. | descartado, mismo criterio que G1-11. |
| G2-20 | VERIFICAR si alguien lee `product` (grant sobrante). | descartado: nadie lo lee hoy (el censo va por `product_ad`/`listing`); retirar el grant exige migración y `app_jev` es NOLOGIN sin credenciales Ads; se revisa si R2 toca grants. |

## G3 — adaptador y CLI (`tools/`, `tests/test_jev_cli.py`)

| # | Hallazgo (resumen) | Destino |
| --- | --- | --- |
| G3-3 | `imprimir(..., file=)` revienta con un imprimir inyectado sin `file=`. | descartado: la firma con `file=` es la del imprimir real; cosmético. |
| G3-4 | `main` sin try/except: tracebacks sin scrub y exit 1 indistinguible de presupuesto agotado. | R2 (códigos de salida y manejo de errores del camino de producción). |
| G3-5 | `scrub` reexportado sin uso. | descartado: reexport consciente vía `__all__`; sin efecto en conducta. |
| G3-6 | VERIFICAR el rol real del CLI (hoy `app_admin` no escribe revisiones por diseño). | R2 (el camino de producción define rol y grants: hoy `app_jev` es NOLOGIN y el CLI usa `ORBIT_DSN_ADMIN`). |
| G3-7 | VERIFICAR `leer_api_key` sin clave y Jev apagado con `pedir` inyectado. | resuelto: (a) prueba de ruta canónica en `e3ba6b9` (B3-r3); (b) la batería corre sin clave real con `pedir` inyectado (CI verde B3-B6a). |
| G3-8 | El dry-run no coincide con `--aplicar`. | R2 (dry-run fiel, con ARCHIVED y presupuesto). |
| G3-9 | `_imprimir_resultado` usa `hasattr`; VERIFICAR que `Indeterminado` no tenga `miembros_totales`. | resuelto por inspección: `Indeterminado` solo lleva `motivos` (`app/jev_ads.py:181`); ramas de impresión cosméticas. |
| G3-10 | `--presupuesto` acepta 0/negativos; términos vacíos o repetidos. | R2 (validación de entradas del CLI). |
| G3-11 | `test_producto_sin_ficha...` no verifica lo que promete el nombre. | descartado: los motivos del dominio los fija `tests/test_jev_ads.py` (B2-r1); aserciones extra cosméticas. |
| G3-12 | `ordinal_nuevo_t2` calculado sin filtrar por t2. | descartado: el ordinal es por par (UNIQUE de `0049`); assert débil sin efecto (los mutantes de reanudación mueren). |
| G3-13 | `pytest.raises(ValueError)` sin `match=`. | descartado: higiene; los mutantes de B3-r2 mueren sin `match=`. |
| G3-14 | `test_sin_clave_jev_apagado` con `eventos >= 1`; "apagado" indistinguible de caída. | descartado: `sin_api_key` produce cero HTTP y el lote no arranca (B3); distinguirlo en el evento persistido no cambia decisión alguna. |
| G3-15 | `_hechos_de_archivo` convierte con `str()` sin validar. | descartado, mismo criterio que G2-10 (visible y reversible por revocación). |
| G3-16 | `--hechos` inexistente lanza OSError como traceback. | descartado: termina con exit != 0; mensaje limpio cosmético. |
| G3-17 | `_fecha` acepta cualquier offset; VERIFICAR que `hash_ficha` normalice a UTC. | resuelto: `hash_ficha` normaliza con `_utc_iso` (`app/jev_catalogo.py:52,76,79`); la fecha del CLI es entrada humana, no parte del hash crudo. |
| G3-18 | VERIFICAR que existan tests del CLI `tools/jev_fichas.py`. | resuelto: `tests/test_jev_catalogo.py:904,943` ejercen registrar en seco, `--aplicar` y doble revocación (NOTAS B2-r1). |

## G4 — pantallas (`/cortes`, fábrica, plantillas)

| # | Hallazgo (resumen) | Destino |
| --- | --- | --- |
| G4-2 | VERIFICAR modo de la conexión de `cortes()` ante fallo de `leer`. | resuelto en `a470866` (verificado en B4-r1): la conexión de lectura es autocommit (`app/api.py:133`); un fallo de `leer` no aborta la pantalla. |
| G4-3 | Rama de degradación sin prueba y no visible para el operador. | R12. |
| G4-4 | `test_export_semillas...` sin aserciones sobre vendedores/exactos/negativos. | R2 (el consumidor real del export llega con el pegamento CLI; ahí se endurecen las aserciones). |
| G4-5 | VERIFICAR que `exportar_semillas` use la misma ventana que `planificar`. | R2 (contrato del export y su consumidor). |
| G4-6 | `terminos_a_cotejar` pierde el rol del término; VERIFICAR `product_id` nunca None. | R2 (el rótulo del rol viaja con el pegamento CLI); el None se descarta: viene de columnas NOT NULL con FK. |
| G4-7 | VERIFICAR desempates temporales de las pruebas. | resuelto: los mutantes de orden de B4-r2/r3 mueren con `created_at` por sentencia (autocommit) y las baterías de B4/B5/B6a corrieron repetidas en verde. |
| G4-8 | VERIFICAR la guardia "cero HTTP" y el `AssertionError` tragado. | resuelto por inspección: `leer` jamás llama transporte (GET no llama a Jev, contrato del plan); la guardia es cinturón y tirantes. |
| G4-9 | VERIFICAR `decision_id` nulo o repetido en `_SQL_CORTES_PENDIENTES`. | resuelto: el JOIN con `decision` es INNER (descarta nulos) y `leer` devuelve un dict (`app/jev_ads.py:906`), donde los repetidos colapsan. |
| G4-10 | Macro `veredicto` dentro del bucle; VERIFICAR autoescape. | R12 (limpieza de plantilla junto con los rótulos); autoescape confirmado: `app/ui.py:59` (Jinja2Templates con autoescape=True). |
| G4-11 | `assert "error" not in html` frágil. | descartado: cosmético; la regla real (sin error económico como tal) tiene aserción propia en el mismo test. |
| G4-12 | Código muerto y duplicación en pruebas. | descartado: higiene sin efecto en discriminación ni en prod. |
| G4-13 | `verify/Launch.md` deja el comentario "64 con headers". | descartado aquí: `verify/` fuera del alcance de este cierre; el conteo se re-verifica en el despliegue (DoD 2.3). |
| G4-14 | `app/jev_ads.py` en `ALLOWLIST_TAMANO`. | R14. |
| G4-15 | VERIFICAR auth de `/export-semillas` y `/asesoria/{huella}`. | R2, confirmado por inspección: el router no trae dependencies globales (`app/api_fabrica.py:60`) y esos endpoints no piden token (`:306,:314`); R2 define perímetro/token antes del pegamento CLI. |

## G5 — pruebas de dominio (`tests/test_jev_ads.py`, `tests/test_jev_juicios.py`)

| # | Hallazgo (resumen) | Destino |
| --- | --- | --- |
| G5-4 | Secretos solo probados en timeout; typo "estatos". | descartado: `scrub` cubre los caminos (B3-r2); parametrización extra y typo cosméticos. |
| G5-5 | Redirección no probada con transporte real; VERIFICAR `follow_redirects=False` y nombres de campo. | resuelto por inspección: `follow_redirects=False` (`app/jev_juicios.py:178`); los nombres de campo vienen del prototipo sellado de B0 (`prototipo/resultado-live.json`). |
| G5-6 | Faltan casos de validación estricta. | descartado en lo cubierto: probabilidad negativa, categoría extra, NaN y confidence fuera de rango ya se rechazan (`app/jev_juicios.py:302-338`); el argmax va con G1-13 (descartado); cuerpo no-JSON y 4xx cosméticos. |
| G5-7 | Sin multibyte ni frontera límite/límite+1. | descartado: el corte es por bytes (`app/jev_juicios.py:241`); frontera cosmética. |
| G5-8 | Orden de opciones y criterios invertidos juntos. | R16. |
| G5-9 | `juicio.clave` con aserción débil. | descartado: la reutilización por clave la ejercen los mutantes de B3; la igualdad literal es redundante. |
| G5-10 | Sin caso `estados=()` con `satisface` ni PAUSED. | descartado: el clasificador de estados quedó fijado por mutantes (B2-r2/r3); PAUSED vive en `ESTADOS_ACTIVOS` (B2-r2); casos extra cosméticos. |
| G5-11 | VERIFICAR que el censo real nunca emita miembros sin anuncios. | resuelto por inspección: los miembros nacen del JOIN con `product_ad` (`app/jev_catalogo.py:260-265`); un miembro trae siempre >= 1 anuncio. |
| G5-12 | VERIFICAR `ficha_ausente` para juicio huérfano. | resuelto en `224449f`: decisión de diseño documentada en NOTAS B2-r1 (§Decisiones). |
| G5-13 | Test cuyo nombre promete lo contrario de lo que afirma. | descartado: renombrar no cambia discriminación. |
| G5-14 | Cuatro `raises(ValueError)` sin `match`. | descartado, mismo criterio que G3-13. |
| G5-15 | Muestras ASIN donde `match` y `fullmatch` difieren. | resuelto en B2-r2 (squash `224449f`): el mutante `fullmatch`→`search` muere en dos pruebas. |
| G5-16 | Helper `_juicio` con probabilidades incoherentes. | descartado: el fixture no alimenta probabilidades a la composición; cosmético. |
| G5-17 | VERIFICAR que `DEFAULT_SECRETS_DIR` sea absoluto. | resuelto por inspección: ruta absoluta (`app/ads/config.py:18`); el hueco de la variable vacía es R11. |
| G5-18 | VERIFICAR si el positivo no exhaustivo queda indistinguible. | descartado: la cobertura viaja en `HayCompatible` y `exhaustivo=False` es constante en grupos Amazon (NOTAS B2-r1); el diseño no exige marcar el universo en el positivo. |

## G6 — pruebas de catálogo (`tests/test_jev_catalogo.py`)

| # | Hallazgo (resumen) | Destino |
| --- | --- | --- |
| G6-2 | Aserción tautológica en `test_cli_registra_seco_y_aplica`. | descartado: la fila en la base la afirman las pruebas de registro idempotente (B2-r1); la salida es cosmética. |
| G6-3 | `raises(CheckViolation)` con dos sentencias. | descartado: la preparación es válida por construcción; higiene. |
| G6-4 | VERIFICAR la reversa con roles globales y bases paralelas. | descartado: la suite no usa xdist y CI corre jobs en contenedores separados; la reversa se ensaya en DB desechable (B2). |
| G6-5 | VERIFICAR grants por tabla bajo `SET ROLE app_jev`. | descartado aquí: lo cubre la consulta de permisos del despliegue (DoD 2.3 exige evidencia de permisos). |
| G6-6 | `skip` si no es superusuario en la prueba de privilegios. | descartado: la suite corre con Postgres local obligatorio; el skip nunca disparó en B2-B6a. |
| G6-7 | `FichaFaltante` inyectada a mano en la prueba de negativo universal. | resuelto: el censo sin producto lo ejercen las pruebas del LEFT JOIN (NOTAS B2-r1: anuncio sin `listing_id` se conserva). |
| G6-8 | VERIFICAR bomba de tiempo con `VENCE=2026-11-02`. | resuelto por inspección: la fecha fija solo vive en registrar (`tests/test_jev_catalogo.py:925,964`), que compara contra `observado_at` literal; `evaluar`/`leer` toman `ahora` inyectable (`app/jev_ads.py:442`). |
| G6-9 | Captura con reloj del cliente vs `clock_timestamp()` del servidor. | R1 (la prueba de captura pasa a `SELECT clock_timestamp()` con la migración nueva). |
| G6-10 | Hash sin vector dorado. | descartado: el hash nunca se recomputa sobre filas viejas; la invariancia y la idempotencia fijan la canonicalización. |
| G6-11 | TRUNCATE no ejercido en integración. | descartado: `prohibir_mutacion` cubre TRUNCATE (NOTAS B2-r1) y no existe camino que trunque. |
| G6-12 | FKs de ficha y de `jev_par_evento` no ejercidas. | descartado: el trigger de cobertura muerde antes (defensa en profundidad, NOTAS B2-r1); higiene. |
| G6-13 | VERIFICAR dos resultados por intención y rechazo de reutilización entre revisiones. | R4 (el rechazo entre revisiones queda decidido en R4); dos resultados es imposible por el camino del asesor (G2-1); el caso negativo de otra clave, descartado (higiene). |
| G6-14 | VERIFICAR si la asimetría de `revocar` sin `--aplicar` es deliberada. | R5. |
| G6-15 | VERIFICAR censo vacío sin `exhaustivo=True`. | resuelto: `exhaustivo=False` es constante en grupos Amazon (NOTAS B2-r1); censo vacío da Indeterminado, nunca `NingunoCompatible`. |
| G6-16 | `_dsn_jev` con `rsplit`; `__import__('os')` en pruebas. | descartado: helper de pruebas, sin camino en prod. |
| G6-17 | Bordes de `ficha_vigente` sin prueba. | descartado: bordes sin camino distinto en datos reales; el desempate de correcciones va con G2-5 (descartado). |
| G6-18 | Estáticos que comparan texto exacto. | descartado: primera línea de defensa; la real es `test_roles_de_minimo_privilegio` (así se refutó B6 en B5-r1). |
| G6-19 | `utcoffset()` solo refleja el SET TIME ZONE de la sesión. | descartado: el `SET TIME ZONE 'UTC'` de la conexión ES el contrato (trigger con UTC fijado); la aserción distingue `timestamptz` de `timestamp`. |

## Recuento

- G1 17, G2 20, G3 16, G4 14, G5 15, G6 18 = **100**.
- A filas del plan: R1 x3, R2 x8, R3 x1, R4 x1, R5 x1, R7 x2, R8 x1, R9 x1,
  R12 x3, R13 x1, R14 x2, R16 x1 = 25 ítems.
- Resueltos con evidencia o inspección: 20.
- Descartados con razón: 55.
