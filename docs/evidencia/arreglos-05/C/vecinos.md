# Vecinos del bloque C

Todos contra el SHA de codigo `36372517`, con el PG16 desechable del
laboratorio y HTTP simulado. Ninguna suite empeora: los conteos son los mismos
del padre cuando la suite no toca C, y las que C toca quedan en verde.

| Suite | Resultado | Por que es vecina |
|---|---|---|
| `tests/test_arreglos05_c_reposicion.py` | 14 passed | Regresiones propias de C.2 y C.3, mas la migracion 0097 |
| `tests/test_despliegue_motor.py -k arreglos05` | 3 passed | Selector del instalador de A, B y C |
| `tests/test_arreglos05_a_estados.py` | 170 passed | La tabla sigue en 165 casillas; los adaptadores C no agregan estados |
| `tests/test_arreglos05_a_ciclo.py` | 57 passed | El ciclo del bloque A no cambia |
| `tests/test_arreglos05_b_reversas.py` | 39 passed | La tabla y los clasificadores B siguen igual |
| `tests/test_architecture.py` | 99 passed, 2 skipped | Presupuesto de 900 lineas e imports del cliente de escritura |
| `tests/test_archiva_inertes.py` | 47 passed | Dueno de la CLI `archiva_inertes`; se portaron las pruebas de reponer al protocolo nuevo |
| `tests/test_ads_archivar.py` | 30 passed | Product ads, el otro dueno de `app/ads/archivar.py` |
| `tests/test_apply_harvest.py` + `tests/test_reversa_harvest.py` | 103 passed | Cosecha y reversa manual, consumidoras del mismo resolver de identidad |
| `tests/test_apply.py` + `tests/test_api_write.py` | 114 passed | Aplicador de bids y frontera manual |
| `tests/test_schema.py` | 91 passed | Invariantes de esquema con la tabla nueva |
| `tests/test_apply_schema.py` | 26 passed | Candados del ledger del aplicador |

Los dos `skipped` de arquitectura son los declarados por la regla 99:2 (dos
pruebas que este sistema operativo no puede ejecutar). No hay omisiones,
errores de fixture ni colecciones vacias en las suites de C.
