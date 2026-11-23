# Evidencia del bloque BASE de ARREGLOS 05

Bloque documental, `lane: fast`. No despliega y no cambia `app/`, `tools/` ni `migrations/`.
Base: `master` en `33844bb365d707e523fc7865dd56668d69047dc4`. El unico commit que toca algo
ejecutable es `2639ff8f`, y solo porta fixtures de `revision/`. Fecha: 2026-11-22 (America/Vancouver).

Esta carpeta convive con `docs/evidencia/arreglos-05/base/`, la foto de la fase de diseno,
que no cambia. Solo difieren en mayusculas; en Windows son la misma carpeta en disco.

| Fila | Archivos |
|---|---|
| BASE.0 | [contratos.md](contratos.md) con el cotejo de firmas, generado por [firmas.py](firmas.py). |
| BASE.1 | [clasificacion.md](clasificacion.md) y su herramienta [clasifica.py](clasifica.py). Corrida completa en [resultados.xml](resultados.xml) y [resultados.log](resultados.log). Vecinos en [vecinos.md](vecinos.md). |
| BASE.2 | [invariantes.md](invariantes.md), consulta [previa.sql](previa.sql), lanzador [previa.sh](previa.sh) y resultado [previa.txt](previa.txt). |
| BASE.3 | [actas.md](actas.md). Comandos y salidas en [acepta.txt](acepta.txt), que genera [acepta.sh](acepta.sh). |
| Todas | [diferencias.md](diferencias.md): diferencias de contrato contra DISENO, fixtures portados y entorno de esta PC. |

## Para el coordinador

- El bloque A recibe la diferencia D-1. La conciliacion del ledger sigue despues de decidir. DISENO la daba por hecha por TEMPORADA B.2 y no es asi.
- Los bloques B y H reciben D-2 y D-3.
- La primera migracion de 05 sale con el numero `0094`.
- En el shell de esta PC hay que quitar `MSYS_NO_PATHCONV` y `MSYS2_ARG_CONV_EXCL` antes de usar `comprobar.sh`. `acepta.sh` y su modo `ALL` las quitan solos.

## Reproducir

Desde la raiz del checkout, con `ORBIT_TEST_DSN` en el PG desechable:

```bash
bash docs/evidencia/arreglos-05/BASE/acepta.sh > acepta.txt
bash docs/evidencia/arreglos-05/BASE/acepta.sh ALL resultados.xml > resultados.log 2>&1
uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/BASE/clasifica.py \
  docs/evidencia/arreglos-05/revision/acceptance.xml resultados.xml
uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/BASE/firmas.py
```

El runner completo entra por `acepta.sh ALL` y no por `comprobar.sh ALL` a secas. Llamado a secas corre en el shell del operador, toma el `python` del PATH y su guarda solo compara host, puerto y base: no ve una `PGHOSTADDR` heredada, con la que libpq conecta las pruebas a otra direccion. El modo `ALL` aplica las guardas de la aceptacion y usa el Python 3.12 del proyecto. `resultados.xml` debe ser un archivo nuevo.

`previa.sh` lee produccion. Solo se corre con la lectura de solo lectura autorizada.
