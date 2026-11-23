# ARREGLOS 05 — evidencia del bloque BASE

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
- En el shell de esta PC hay que quitar `MSYS_NO_PATHCONV` y `MSYS2_ARG_CONV_EXCL` antes de usar `comprobar.sh`.

## Reproducir

Desde la raiz del checkout, con `ORBIT_TEST_DSN` en el PG desechable:

```bash
bash docs/evidencia/arreglos-05/BASE/acepta.sh > acepta.txt
ORBIT_ACCEPTANCE_XML=$PWD/resultados.xml bash docs/evidencia/arreglos-05/comprobar.sh ALL
uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/BASE/clasifica.py \
  docs/evidencia/arreglos-05/revision/acceptance.xml resultados.xml
uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/BASE/firmas.py
```

`previa.sh` lee produccion. Solo se corre con la lectura de solo lectura autorizada.
