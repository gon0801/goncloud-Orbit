# Vecinos del bloque B

Cada grupo corrió en serie sobre el PG desechable, en primer plano y con
`timeout`. Entorno: el de `comprobar.sh` (`CI=1`, `PYTHONPATH=.;tests`, temporal
propio) y el preambulo de `README.md`. Los conteos son la ultima linea de
pytest de cada corrida.

Que cambio y que no se decidio comparando contra el padre: las suites con
fallos se corrieron tambien en un worktree separado en `23ba2385`, con el
mismo interprete y el mismo entorno. Un fallo es del bloque solo si aparece en
la rama y no en el padre.

## Sin fallos

| Grupo | Archivos | Resultado | Corrido sobre |
|---|---|---|---|
| Modelo de estados | `test_arreglos05_a_estados.py` | 170 passed | arbol final |
| Bloque A | `test_arreglos05_a_ciclo.py` | 57 passed | arbol final |
| Arquitectura | `test_architecture.py` | 99 passed, 2 skipped | arbol final |
| Apply y API de escritura | `test_apply.py`, `test_api_write.py` | 65 y 49 passed | arbol final |
| Cola, cosecha y su reversa manual | `test_apply_cola.py`, `test_apply_harvest.py`, `test_reversa_harvest.py` | 44, 73 y 30 passed | antes de `71cb4dae`; `acepta.sh` los repite |
| Ciclo | `test_cycle.py`, `test_cycle_apply.py`, `test_cycle_pause_cooldown.py`, `test_cycle_target_ciclo.py` | 103 passed | antes de `71cb4dae`; dos de ellos se repiten |
| Temporada A a I | `test_temporada01_a.py` a `test_temporada01_i.py` | 85 y 43 passed | arbol final |
| Esquema y migraciones | `test_apply_schema.py`, `test_schema.py`, `test_migracion_0061.py` a `0072.py` | 145 passed | arbol final |
| Cliente de escritura, ajuste, archivo, vistas | `test_ads_write.py`, `test_aplica_ajuste.py`, `test_archiva_inertes.py`, `test_bids02_vistas.py`, `test_bloqueo_dueno.py` | 159 passed | arbol final |
| Ledgers y metricas | `test_lecturas_caso.py`, `test_ledger_atribucion.py`, `test_ledger_sync.py`, `test_metricas_listas.py` | 73 passed | arbol final |
| Precio, preflight, pausa de producto, propuestas, retiro | `test_precio_corrida.py`, `test_precio02_*.py`, `test_preflight_1_4.py`, `test_productos_pausa.py`, `test_propuestas_campana.py`, `test_retiro_contrato.py` | 289 passed | arbol final |
| Cron | `test_avisos_campana_cron.py`, `test_impulso_vigia_cron.py`, `test_ingesta_cron.py`, `test_jev_senales_cron.py`, `test_placements_cron.py` | 12 passed | arbol final |
| Bloqueo del dueno | `test_bloqueo_dueno_efecto.py` | 40 passed | antes de `71cb4dae` |
| Fabrica F2 | `test_fabrica_f2.py`, `test_fabrica_f2_visibilidad.py` | sin fallos | antes de `71cb4dae` |
| Pausa de producto | `test_apply_producto.py` | sin fallos | antes de `71cb4dae` |

Las dos omisiones de arquitectura son de plataforma: este sistema operativo no
crea archivos ilegibles (`test_architecture.py:1613` y `:1626`). Ya se omitian
en `master`.

## Con fallos previos, identicos en el padre

| Archivo | En la rama | En el padre `23ba2385` | Motivo |
|---|---|---|---|
| `test_fabrica_f2_hermanas.py` | 24 failed | 24 failed | `relation "temporada_efecto" does not exist`: su lista de migraciones no trae la 0093. Dos casos fallan con `KeyError: 'hermanas'`, tambien en el padre. |
| `test_fabrica_f2_biblioteca.py` | 16 failed | 16 failed | el mismo `temporada_efecto`. |
| `test_harvest_destino.py` | 3 failed | 3 known | el mismo `temporada_efecto`. |
| `test_d3_adversary.py` | 2 failed | 2 failed | `KeyError: 'SELECT id, settings FROM config_version ...'`: su arnes de SQL fijo no conoce esa consulta. |
| `test_notifica.py` | 3 failed | 3 known | de la lista de 31 de `master`. |
| `test_orbit07_m1.py` | 1 failed | 1 known | `umbral_corte() missing 1 required keyword-only argument: 'mult_baja'`. |

Los conjuntos se compararon nombre por nombre: ningun caso falla en la rama
sin fallar tambien en el padre, y al reves. La lista de 31 de `master` no
traia `test_fabrica_f2_biblioteca.py` ni `test_d3_adversary.py`; en esta PC
fallan igual en `master`.

## Fixtures portados al contrato nuevo

Cada cambio conserva la asercion de usuario de su prueba. El motivo comun es
que la reversa y la creacion releen antes de escribir, y los dobles de HTTP no
respondian esa lectura.

| Archivo | Que cambio |
|---|---|
| `test_apply_harvest.py` | `_fila_ledger_abierta` siembra el intento abierto con su `ausencia_previa`. `_handler_lista_bc` admite objetos vivos en la lista que no es su objetivo. El negativo de un doble pasa a los enums reales del wire. Dos pruebas que esperaban un DELETE a ciegas de una keyword que la lectura no ve viva pasan a no esperarlo; una que resolia el negativo por un booleano sin id pasa a borrar solo la keyword propia acreditada. Dos arneses de SQL fijo conocen el intento abierto. |
| `test_reversa_harvest.py` | cuatro dobles responden la lectura previa con la keyword propia viva; el LIST ambiguo o truncado falla recien en el readback, despues del delete, como decian sus docstrings. Un conteo pasa de 4 a 8 lecturas: una antes de cada delete y su readback. |
| `test_apply_cola.py` | el doble tiene ruta para la lista de negatives; la prueba del 207 rechazado parte de un negative aplicado y vivo. |
| `test_apply.py` | el doble del regreso del dueno modela lo que Amazon tiene en cada hoja. La prueba de la etiqueta unificada deja de esperar un envio a ciegas tras un apply que divergio; ese contrato lo conserva `test_arreglos05_b_reversa_de_bid_que_diverge_al_volver_sella_la_etiqueta_unificada`. |
| `test_api_write.py` | el doble responde la lista de negatives; las filas de negative de la siembra traen el payload del wire; Amazon arranca con el bid aplicado. |
| `test_arreglos05_a_ciclo.py` | la reversa automatica de cosecha lee la keyword y el negativo propios vivos antes del DELETE que recibe 403. |
| `test_bloqueo_dueno_efecto.py` | el doble responde la lectura previa (vacia y completa) y la prueba del duplicado conserva su 207 con error por item. |
| `test_d3_adversary.py` | su arnes conoce el intento abierto con evidencia. |
| Listas explicitas de migraciones | `test_api_write.py`, `test_apply.py`, `test_apply_cola.py`, `test_apply_harvest.py`, `test_apply_schema.py`, `test_cycle.py`, `test_cycle_apply.py`, `test_fabrica_f2.py` (`ORDEN_F2`) y `test_bloqueo_dueno_efecto.py` aplican la 0095. |

## Fuera del alcance, anotado

`tests/test_despliegue_motor.py` tiene un banco (`banco`) que no corre en esta
PC: su fixture hace `git checkout -b concronorbit2 ConCronOrbit` en un
repositorio temporal y falla al preparar la prueba. No toca migraciones. Las
dos pruebas del selector de migraciones (la del bloque A y la de este) pasan.
