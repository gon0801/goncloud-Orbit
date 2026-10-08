# Lectores frescos del plan y la guía (2026-10-08)

Dos lectores sin contexto ejecutaron [el plan](../../../../plans/repricing-02.md)
y [la guía de construcción](../../../../plans/repricing-02-construccion.md) sobre
el commit `72b06d8`. Uno hizo de quien construye y listó cada pregunta que
tendría que hacerle a alguien. El otro buscó demostrar que el plan falla. El
segundo aplicó `datos.sql` sobre un Postgres 16 local con las migraciones 0001 a
0052 y borró la base al terminar. Ninguno tocó producción ni llamó a Amazon o a
Mercado Libre.

Este archivo guarda qué encontraron, el comando que lo probó y cómo se corrigió.
Sirve para dos cosas: explicar por qué una regla del plan es como es, y evitar
que el siguiente lector vuelva a gastar en lo ya comprobado.

## Hallazgos que habrían hecho fallar la construcción

Todos se corrigieron en una sola ronda.

| # | Hallazgo | Comando que lo probó | Corrección |
| --- | --- | --- | --- |
| 1 | La 0054 exigía columnas que el código desplegado no escribe. Un CHECK `NOT VALID` perdona las filas viejas, no los INSERT nuevos. Desde D.1 la corrida no habría guardado decisiones y, hasta D.3, la estimación no habría guardado escenarios. | INSERT con las columnas de `corrida.persistir_decision` sobre la base con `datos.sql` aplicado: `violates check constraint "precio_decision_l_exige_origen"`. `grep -n "INSERT INTO estimacion_escenario" -A 6 app/estimacion_repository.py` no trae `logistica_origen`. | La 0054 no exige ninguna columna nueva. Los dos CHECK de `precio_decision` van en la 0058 (S.1) y los dos de `estimacion_escenario` en la 0055 (F.3). 0.a prueba que los INSERT de hoy siguen entrando. |
| 2 | El paso 0.b pedía decidir sobre filas guardadas, mantener el cupo y no tocar aserciones. No caben juntas: el cupo reescribe la decisión antes de guardarla y `precio_decision` no admite UPDATE. | `grep -n "_cuota_reescrita\|repartir_cupo(" app/precio/corrida.py`. `grep -n '"cuota"' tests/test_precio_corrida.py` da cinco aserciones `mantener(cuota)`. | 0.b conserva el orden de hoy detrás del `Mercado`. Guardar al decidir pasa a S.1, con el borrado del cupo. |
| 3 | Las comprobaciones de "sale igual que antes" no se podían correr: `precio --platform amazon_mx` arma clientes reales, y una decisión por unidad y día hace que la segunda corrida no decida. | `sed -n 468,486p app/cli.py`. `grep -n "precio_decision_unica_por_dia" migrations/0039_precio.sql`. | `comparar.sh` con dos bases locales y una lista fija de columnas. En producción, `precio --reporte`. |
| 4 | La regla de envío no nombraba `finance:MFNPostageFee`, que es casi todo el envío FBM de México: 649 órdenes habrían quedado sin costo. | `grep -rn MFNPostageFee plans/repricing-02*.md docs/evidencia/repricing-02` vacío. `sed -n 444,451p docs/evidencia/repricing-01/E.0/veredicto.md`. | `costo_de_orden = max(labman_label, shipping_label, mfn_postage) + shipping_hb`. |
| 5 | S.7 colgaba solo de 0.b y se desplegaba antes que F. La señal habría salido `sin_dato` para todo MX FBA en el primer encendido. | La vista `v_precio_venta_unidad` hace LEFT JOIN contra una tabla que llena F.1. | S.7 depende de F.1 y F.4 y se despliega en D.3. Hasta entonces sigue la señal por producto. |
| 6 | "Solo 0.a edita `tests/test_architecture.py`" era imposible. Los candados nuevos por carril no corrían en el PR. | Borrar `cuota.py` en una copia y correr `pytest -q tests/test_architecture.py -k excepcion_por_nombre`: rojo. `grep -n pytest .github/workflows/quality.yml`. | Lo editan 0.a, 0.b y el carril S. 0.a agrega `tests/test_arq_precio_*.py` al job `rapido`. |
| 7 | El apagador no tenía camino en `/settings`: esa pantalla no edita ninguna clave `precio_*`. | `grep -n "precio_" app/config_write.py` vacío. | S.2 edita `config_write.py`, la ruta de config, `settings.html` y `settings.js`, con su DoD. |
| 8 | Las lecturas F.0 y M.0 no tenían herramienta. `ProductFeesClient` fija `IsAmazonFulfilled` en verdadero. Correr la sonda de Mercado Libre fuera del contenedor habría dejado inservible el token de producción. | `PYTHONPATH=. python tools/sonda_spapi.py --help` no ofrece fees. `grep -n IsAmazonFulfilled app/estimacion_fees.py`. `sed -n 184,203p app/reputacion_clientes.py`. | Cada lectura tiene su script y su comando, dentro de `orbit-app-1`. |
| 9 | "Una copia con las filas de producción" no tenía comando. El ensayo de referencia copia solo el esquema, y sin filas la siembra de config inserta cero. | `grep -n pg_dump docs/evidencia/jev-ads-02/ejecucion/S.3/ensayo.sh` trae `--schema-only`. | El ensayo carga datos con `pg_dump --data-only` y exige tres conteos. |
| 10 | El cron se contradecía entre D.2, D.3 y D.4, sus líneas no corrían en el servidor y dos jobs no tenían comando. Una prueba fija la línea real. | `sed -n 1437,1455p tests/test_precio_corrida.py`. | Líneas reales por despliegue. El job de muestras y el catálogo tienen su propio `main`. S.3 actualiza la prueba. |
| 11 | X.4 era circular: la sonda sellaba la forma, pero el cliente no escribía hasta que la forma estuviera sellada. | `grep -n "pendiente_sonda\|EscrituraNoDisponible" docs/evidencia/repricing-02/diseno/bosquejo.py`. | M.4 escribe la forma candidata. Solo la sonda puede escribir con ella. |
| 12 | La prueba de que el motor conoce todos los motivos de la estimación se cumplía por construcción. | Los motivos son literales sueltos en seis módulos; no hay export. | La prueba recorre los literales con `ast`. |
| 13 | G.1 exigía los mismos resultados que antes y cambiaba tres comportamientos que las pruebas fijan. | `sed -n 1553,1575p tests/test_precio_goals.py`. | Los tres cambios quedan declarados y sus pruebas se reescriben. |
| 14 | Las políticas de los universos nuevos no tenían valores. | `git grep -ln "INSERT INTO estimacion_politica_version"` solo da la 0029 y pruebas. | Tabla "Políticas por universo" en el plan. |

## Hallazgos menores, también corregidos

- `datos.sql` describe los triggers en comentarios y trae la siembra de config
  comentada. La guía ahora lista lo que la 0054 debe escribir de verdad.
- Varios pasos editaban archivos de otro carril o declarados intocables. La guía
  tiene ahora la tabla "Quién edita cada archivo compartido".
- Las dos compuertas `git grep` daban verde con el paso a medias. Sus patrones
  se ampliaron.
- La batería local sale verde sin Postgres porque las pruebas con base se
  saltan. La guía da el comando con `ORBIT_TEST_DSN` y `-rs`.
- Las herramientas de `tools/` no corren sin `PYTHONPATH=.`.
- "FBM: 102 y 100" era en realidad "sin canal conocido".
- El criterio "95 % en tolerancia" compara la cuenta consigo misma. El plan lo
  dice y deja la reproducción a mano como la comprobación que prueba la cuenta.
- La prueba "rechaza `live` bajo `shadow`" ya pasaba con la 0039. Ahora prueba
  también lo nuevo.
- `frenado(api_error)` se alcanza en la cuarta corrida, no en la tercera.

## Verificado y correcto

No hace falta volver a comprobarlo sobre `72b06d8`:

- `datos.sql` aplica limpio sobre las migraciones 0001 a 0052 en Postgres 16:
  7 tablas nuevas, sin choque de nombres, permisos y secuencias válidos.
- La 0053 y la 0054 necesitan dos corridas de `psql`: usar el valor nuevo del
  enum en la misma corrida da `unsafe use of new value`.
- Reeditar un goal el mismo día entra con el índice parcial.
- El CHECK `precio_muestra_origen_coherente` rechaza una muestra `familia` sin
  donantes.
- Los permisos de §11: `app_decide` no inserta en `precio_compuerta_liberacion`
  y `app_admin` no inserta en `precio_retencion` ni en `precio_corrida`.
- `PYTHONPATH=. python -m pytest -q tests/test_architecture.py`: 96 passed.
- `python -m py_compile docs/evidencia/repricing-02/diseno/bosquejo.py` compila.
- Cada símbolo que la guía dice que "vas a encontrar" en 0.a, 0.b y S.1 existe
  donde se cita.
- Cada archivo marcado como nuevo no existe todavía.
- El grafo de dependencias del plan no tiene ciclos.
- Los números de los DoD se alcanzan: 80 de 158 pasa de 0.50 y 79 no. 60 de 158
  pasa de 0.30. `max(449, 450) + 80` da 530.
- La última migración es la 0052. Los números 0053, 0054, 0055 y 0058 están
  libres.

## Lo que ningún lector pudo comprobar

Son hechos de producción. Los comprueban las lecturas F.0 y M.0 y los ensayos de
cada despliegue:

- Que `precio_envio_muestra` está vacía.
- Que Product Fees responde `Success` para una oferta FBM.
- Los permisos del token de Mercado Libre y de dónde sale el SKU por variante.
- Si un listing FBM sin existencias deja de ser `BUYABLE`.
