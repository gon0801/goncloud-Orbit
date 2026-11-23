# Controles confirmados y actas residuales (BASE.3)

Bloque `BASE` de `plans/arreglos-05.md`, fila `BASE.3`. Salida completa en [acepta.txt](acepta.txt), que genera [acepta.sh](acepta.sh). PG 16.15 del laboratorio. El SHA de la corrida esta en la cabecera de `acepta.txt`.

`acepta.sh` valida que el DSN sea el del laboratorio antes de abrir cualquier conexion. Un comando cuenta como fallo si sale distinto de 0 o si, siendo de pytest, su resumen no muestra al menos el numero esperado de casos pasados sin omisiones ni errores.

## Comandos de la fila

| Que | Comando | Resultado |
|---|---|---|
| BASE.0 | `git merge-base --is-ancestor origin/master HEAD && (for f in ...; do git cat-file -e "origin/master:$f" \|\| exit 1; done)` | salida 0 |
| Control 01 | `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh T writes test_01` | 4 passed, salida 0 |
| Controles 08 y D2 | `... T pricing 'test_08 or test_D2'` | 2 passed, salida 0 |
| Control 09 | `... T ingest test_09` | 1 passed, salida 0 |
| Banda manual 01X | `CI=1 PYTHONPATH=.:tests uv run --frozen --python 3.12 python -m pytest -q tests/test_config_write.py -k arreglos01_x` | 1 passed, salida 0 |
| Controles TEMPORADA B.2, E y F | `PYTHONPATH=.:tests uv run --frozen --python 3.12 python -m pytest -q tests -k 'temporada01_b_ or temporada01_e_ or temporada01_f_'` | 34 passed, salida 0 |

Los controles 01, 08, 09 y D2 siguen verdes y no se reimplementan. La banda manual de Ajustes ya esta corregida por 01X y queda como control del bloque I. Los comandos de `comprobar.sh` salen con el entorno sin `MSYS_NO_PATHCONV` y `MSYS2_ARG_CONV_EXCL`; ver [diferencias.md](diferencias.md).

## Control de TEMPORADA B.2 que no pasa

Las pruebas `test_temporada01_b_` pasan, pero no cubren el orden del ciclo. El control que si lo mide es la reproduccion legada de Ciclo 4:

```bash
PYTHONPATH=.:tests uv run --frozen --python 3.12 python -m pytest -q -rs -p no:cacheprovider \
  docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/test_revision_ciclo.py -k reconciliacion_corre_despues_de_decidir
```

Falla con `AssertionError: el mismo ciclo salta la hoja por 'bid en vuelo' y luego confirma ese bid`, `assert not (1 and 1)`. Es la diferencia D-1 y pertenece al bloque A. BASE la registra y no la corrige.

## Actas residuales de la base integrada

"Recuperar" aqui es localizar el acta y, cuando la lectura de base la acredita, dejar la prueba. Ninguna entrega se rehace.

| Plan | Acta residual segun ORDEN-IMPLEMENTACION | Estado en master | Que hace BASE |
|---|---|---|---|
| ARREGLOS 01 | "Consolidar actas y sondas pendientes; A, C y U ya tienen evidencia operativa propia". | Ocho bloques no tienen carpeta de evidencia: `E`, `F`, `G`, `I`, `K`, `M`, `W` y `Y`. Sus filas no piden operacion en produccion. Solo traen migraciones `F.2` (0083) y `Y.2` (0085). | Las sondas de 0083, 0084 y 0085 dan `True` en produccion ([previa.txt](previa.txt)). Queda acreditado que estan aplicadas. |
| ARREGLOS 02 | "0086 existe en Git; acreditar su aplicacion con esquema/sonda y resultado D.4". | `docs/evidencia/arreglos-02/D/` trae `antes.txt`, las cuatro consultas y `despues.sh`. No trae la salida de la corrida posterior al despliegue. `diferencia.md` explica un ajuste de diseno de D.2, no es ese resultado. | La sonda de 0086 da `True`. La aplicacion queda acreditada. El resultado de D.4 sigue abierto: el script lleva escritos los valores del dia de D.3 y vencen a los tres dias, asi que correrlo hoy no prueba nada. |
| DISEÑOS 01 | "Comparaciones locales o de copia no sustituyen las actas productivas A.4, B.4 y C.3". | Existen `docs/evidencia/disenos-01/{A,B,C}/comparacion.md`. Las tres comparan el "antes" de produccion con un "despues" tomado de una copia en solo lectura o del template renderizado. El cotejo contra produccion despues del despliegue no esta registrado. | Sigue abierta. Es una comparacion de pantalla, no de base, y las tres pantallas cambiaron despues con 03, 04 y TEMPORADA. Un cotejo contra el "antes" del 12 de noviembre ya no tendria el sentido de su fila. Lo retoma el recorrido UI del bloque Z o el dueno. |
| ORBIT 07, CLARIDAD 01 | "Consolidar recibos residuales". | Fuera del alcance de 05. ORDEN los deja como seguimientos separados. | Nada. |

## Otras reproducciones legadas del ciclo

En el mismo archivo, F1, F2 y F3 fallan por su asercion original sobre `2639ff8f`: son la linea roja del bloque A. F5 ya no corre porque `_paso` exige `centavos` desde ARREGLOS 02. Detalle en [diferencias.md](diferencias.md).
