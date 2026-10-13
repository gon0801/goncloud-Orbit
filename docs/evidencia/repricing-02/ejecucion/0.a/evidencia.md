# 0.a: contratos sin cambio de comportamiento

Fecha: 2026-10-13 UTC. Rama `rp02/sec2-corte-0`, base `7908f73`.

## Qué se hizo

- `migrations/0053_precio02_canal_meli.sql` y su reversa (el valor `meli` del
  enum queda sin uso: Postgres no lo quita).
- `migrations/0054_precio02_base.sql` y su reversa: las 7 tablas nuevas
  (`listing_miembro`, `ledger_event_atribucion`,
  `meli_publicacion_observation`, `precio_corrida`,
  `precio_compuerta_medicion`, `precio_retencion`,
  `precio_compuerta_liberacion`), las 4 vistas nuevas, los 6 triggers
  nuevos, los tres `CREATE OR REPLACE` con los predicados de
  `precio_decision_coherente`, el bloque DO de candado con sus casos y el
  INSERT de config. Verificada en vacio y sobre copia con datos de
  produccion.
- `app/precio/puerto.py` (seccion B completa, con `miembro_que_manda`).
- `app/precio/tipos.py`: seccion A (aritmetica de `Importe` con
  `MonedasMezcladas`, `TasaFx`/`Convertido`/`convertir`, envio con origen,
  `Fees` partidos, `Tasas`, `Cuenta`, vocabulario nuevo) y
  `MOTIVOS_NO_EVALUADO = MOTIVOS_ESTIMACION | propios del motor`.
  `CambioPrevio` gana `sin_efecto: bool = False`.
- `app/estimacion_venta.py`: export `MOTIVOS_ESTIMACION` (duena de sus
  motivos; la prueba de ast exige el superconjunto).
- `app/precio/config.py`: `ConfigMotor` + `leer_config_motor` con las claves
  de la seccion H y sus cotas, y `modo_de` (universo sin entrada = off). Las
  reglas puras siguen en `ConfigPrecio`: exigirle las claves nuevas a
  `leer_config` romperia la bateria existente sin que el codigo de hoy las
  lea (desviacion declarada en el PR).
- Un solo lector por clave numerica: `goals_write`, `fuentes`, `corrida` y
  `notifica` delegan en los ayudantes de `config.py`. `cuota.validar_cap`
  no se toca (se borra en S.1).
- `app/precio_mercados.py` (raiz de composicion) y
  `app/estimacion_universo.py` (cuatro entradas registradas), con los
  modulos dueno ya creados aunque vacios: `app/spapi/precio_mercado.py`,
  `app/estimacion_amazon.py`, `app/ledger_atribucion.py` y en `app/meli/`:
  `precio_mercado.py`, `write_client.py`, `catalogo.py`, `estimacion.py` y
  `ledger.py`.
- `app/ledger.py`: despacho de la atribucion por plataforma con el protocolo
  `Atribuidor`; la ingesta inserta en `ledger_event_atribucion` con
  `ON CONFLICT DO NOTHING`, atribuye toda venta del plan (tambien la ya
  ingerida, por su llave de dedupe) y cuenta `venta_sin_atribuir` en
  skip_reason sin sumar a `rows_skipped`. En 0.a los dos atribuidores
  devuelven None y MeLi sigue excluida.
- `/precios` partido: el bloque de precios de `app/api_dashboard.py` vive en
  `app/api_precios.py` (mismo prefijo `/api/dashboard`); el dashboard
  reexporta los cinco nombres que usan `/salud` y las pruebas.
  `precios.html` incluye `_precios_seguridad.html` (lo construye S.3) y
  `_precios_goals.html` (el contenido de hoy).
- `tests/test_architecture.py`: candado que deja a `app/precio_mercados.py`
  como unico importador de los dos adaptadores, con su fuga sembrada.
- `tests/test_arq_precio_{f,m,g,s}.py` creados y agregados al paso de
  guardas del job `rapido` de `.github/workflows/quality.yml`.
- `verify/Launch.md` y `verify/Doctor.md`: 85 tablas BASE y 24 vistas.

## Comprobaciones

Bateria completa con base, sin saltos:

	PATH="/opt/homebrew/bin:/usr/bin:/bin" ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres" \
		PYTHONPATH=. .venv/bin/python -m pytest -q -rs
	4942 passed, 1 warning in 408.40s

`-rs` no reporta ningun skip (los 22 de la primera corrida eran del PATH
restringido del shell: node y pg_dump viven en /opt/homebrew/bin).
`tests/test_precio_corrida.py` y `tests/test_precio_pantalla.py` pasan sin
editarlos. La linea base de master `e972fcc` era 4895 passed.

Rojo inicial de las pruebas nuevas (regla 9), contra el codigo de 7908f73:

	PYTHONPATH=. .venv/bin/python -m pytest tests/test_precio02_contratos.py tests/test_precio02_base.py tests/test_ledger_atribucion.py -q
	ImportError: cannot import name 'precio_mercados' from 'app'
	ModuleNotFoundError: No module named 'app.ledger_atribucion'

Ensayo sobre copia con datos de produccion: `ensayo.sh` (salida en
`ensayo-salida.txt`). Antes: muestras=0, config=29, decisiones=114.
Despues: config=30 (una fila mas, modo global `shadow`, conserva todas las
claves de la previa) y decisiones=114. La 0053 y la 0054 aplican en dos
corridas de psql separadas. La reversa de la 0054 devuelve el esquema
identico al previo (diff de `pg_dump --schema-only` con bloques GRANT
ordenados: 0 diferencias).

Mutantes: `mutantes.md` (un mutante por prueba nueva, incluidos los cinco
obligatorios de esta seccion; todos en rojo).
