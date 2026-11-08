# CLARIDAD 01: causas encontradas (2026-11-08)

Origen: dos lectores probaron el entrenamiento `docs/manual-usuario/` contra producción
(`lector-novato.md`, `lector-esceptico.md`). Cuatro investigaciones de solo lectura sobre
`origin/master` 7cf7c9a y GET a `http://10.13.13.1:8010` dieron estas causas. Lo marcado
«inferido» lo confirma la primera fila del bloque que lo corrige.

## Jev no corre desde el 2026-10-21 (Gasto sin venta vacía)

- `72c440e` (2026-10-21, ingesta-01 A) agregó `metricas_listas.guarda(lector, ...)` en
  `app/jev_senales.py:808`. La guarda hace un SELECT sobre una conexión sin autocommit y deja
  una transacción abierta. `_leer_mundo` (`app/jev_senales.py:257-260`) hace después
  `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` y Postgres lo rechaza con
  `ActiveSqlTransaction`. El `except` de `main` (`:823-825`) solo imprime en el log y sale 1.
- Reproducido en local: `investigacion/repro_jev_transaccion.py <dsn>`.
- El ciclo se salva porque `corre_ciclo` hace `conn.commit()` al empezar (`app/cycle.py:3940`).
- Ninguna prueba lo vio: `tests/test_jev_senales.py` usa `--forzar` y
  `tests/test_metricas_listas.py:448-470` reemplaza `correr`.
- Salud dice «completa» porque muestra el motivo del último `fin` sin su fecha
  (`app/jev_salud.py:95-108`, `app/templates/salud.html:188-189`).
- Gasto sin venta dice que nada gasta sin vender aunque el cálculo esté viejo
  (`app/templates/gasto_sin_venta.html:69-70`).
- Por confirmar en el servidor: `grep -c ActiveSqlTransaction /mnt/data/appdata/orbit/logs/jev-senales.log`.

## Propuestas no dice la verdad

- `_SQL_CORTES_PENDIENTES` (`app/api_dashboard.py:1298-1318`) no lee `q.modo`, y `cortes()`
  (`:1536-1572`) no lo entrega. Las filas `shadow` nunca vencen ni se aplican
  (`app/apply_cola.py:198-203`, trigger `migrations/0002_apply.sql:202-209`), pero la celda dice
  «se aplica solo el …» (`app/templates/cortes.html:32`).
- Rechazar una fila de práctica sí bloquea de verdad: `_SQL_CLAVES_BLOQUEADAS`
  (`app/apply_cola.py:156-162`) no filtra por modo (inferido del SQL).
- El recuadro de rechazo dice «hasta el {vence_el}» (`cortes.html:80-81`), pero el bloqueo es
  `now() + dias` (`app/api_write.py:277-279`).
- `ETIQUETA_POR_KIND` (`app/api_dashboard.py:1334-1348`) etiqueta toda `pause` como «Apagar
  palabra», aunque la entidad sea un `product_ad`. `entidad_kind` ya viene en la consulta.
- El contador del menú cuenta también las de práctica (`app/static/js/shell.js:27`).
- `cortes.js:32` pinta la fecha ISO cruda y `:34` pinta «[object Object]» con un 422.
- El 8-nov había 31 pausas de product_ad en Propuestas. Casi seguro son de práctica porque la
  pausa estaba en práctica (`_modo_pausa_producto`, `app/apply_cola.py:518-536`; inferido).

## Subfamilia

- El campo es el `tipo_producto` y solo acepta `^[a-z0-9_]+$` (`app/api_fabrica.py:366`).
  «Arras para boda» da 422 con «Los datos no son válidos. Revisa los campos y la confirmación.»
  (`_RutaFabrica`, `app/api_fabrica.py:39-59`). Reproducido: `investigacion/repro_subfamilia_422.py`.
- El valor elige los negativos de la campaña auto y el `tipo_producto` del grupo que se crea
  (`app/impulso_io.py:333`, `tools/fabrica_campanas.py:172-181, 282-285, 683`). Un valor sin
  biblioteca lanza la auto sin negativos y abre una biblioteca huérfana. Es dinero.
- Los valores conocidos ya salen de `_SQL_TIPOS` (`app/fabrica_web.py:62-67`): hoy `kit_arras`
  en MX y ninguno en US. `familias.slug` (`app/familias.py:53-63`) ya normaliza nombres.

## Motor de precios con universo de más

- La Cobertura cuenta solo el catálogo BUYABLE (`app/api_precios.py:206-237`,
  `app/precio/cobertura.py:117-173`, `app/spapi/precio_mercado.py:57-72`). La lista «No
  evaluados» sale de todas las decisiones del día (`app/api_precios.py:376-387`).
- MX: 231 goals live decididos, 173 a la venta. 58 publicaciones no están a la venta y el motor
  igual decide sobre ellas (`_decidir_pendientes`, `app/precio/corrida.py:772`). Hoy las frena
  solo que su oferta esté vieja. US: 99 decididas, 92 a la venta.
- Riesgo de dinero (inferido): una publicación que no está a la venta con oferta reciente puede
  recibir un cambio de precio, y las evaluadas diluyen la compuerta (`compuerta.py:189-247`).
- La compuerta muestra las decisiones de la última corrida (`app/api_precios.py:185`), que repite
  el día y guarda 0 (`corrida.py:795`). El total del día está en `precio_compuerta_medicion`.
- TACoS es de la plataforma y del mes, copiado en cada fila (`app/api_precios.py:436-496`).
- US está sin evaluar: las 99 decisiones son `oferta_desactualizada`. Hipótesis, sin confirmar:
  la ingesta de ofertas de US está atrasada. Ese día `spapi_pricing` de US corrió bien y escribió
  167 filas, así que la causa puede estar en cómo el motor decide qué oferta es fresca (bloque F.1).
- SQL de confirmación (solo lectura) en el bloque E del plan.

## Inertes tarda 235 s

- Medido el 2026-11-08: `/inertes` 236.6 s y 234.9 s; `/api/dashboard/inertes` 235.5 s y 234.3 s.
  El resto del panel responde en 0.4 a 1.7 s.
- `_SQL_INERTES` lee `v_entidad_inerte` (migración 0013), que lee `v_metric_latest` tres veces.
  `v_metric_latest` es un `DISTINCT ON` sobre toda `ads_metric_observation` (0001:1159), que solo
  crece. Hipótesis: tres recorridos completos con ordenamiento. Falta el EXPLAIN en una copia.
- `tools/archiva_inertes.py` usa la misma vista y archiva en Amazon.

## Formato

- Unos 20 diccionarios sueltos traducen códigos con `.get(codigo, codigo)`; un código nuevo sale
  crudo sin aviso. Inventario completo en `inventario-formato.md`.
- `dinero_ui` (`app/ui.py:81`, 34 usos) no pone separador de miles. `ts_ui` (`app/ui.py:134`)
  deja la hora en UTC. 31 fechas se imprimen sin filtro.
- Decisiones `decisiones.html:21` y Settings `settings.html:28` imprimen la meta sin redondear.
- Skips de Salud: `tojson` ordena las claves y Chart.js salta etiquetas (`dashboard.js:119, 296`).
- Ruido: ciclos en orden ascendente (`pantalla_ruido._SQL_CICLOS`) y hojas sin nombre (`:189`).
- Keywords dañadas: las regresadas siguen como tarjeta (`keywords_danadas.html:37`).
- Línea base del detector (`investigacion/visibles_prototipo.py`), 8-nov: Ruido 26 códigos,
  Salud 16 códigos y 40 fechas con microsegundos, Campañas 14, Productos sin clics 12, Inertes 11,
  Decisiones 11 metas largas, Precios 45 y Resumen 127 cifras sin miles.
