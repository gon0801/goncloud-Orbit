# Bloque I de ARREGLOS 05: Ajustes rechaza una cuota que no puede guardar

Base `a9885448`, rama `arreglos-05/i`, cabeza de implementacion `eb881707`.

| Archivo | Contenido |
|---|---|
| `estados.md` | Modelo por camino escrito antes del codigo (6 caminos y su dueno). |
| `resultado.md` | Filas I.1/I.2, que cambia, rojo/verde, acepta, foto RO, migracion, reversa y P2. |
| `rojo.txt` | `N i` contra el padre real: 4 failed, 15 passed. |
| `verde.txt` | `N i` en la cabeza: 19 passed. |
| `acepta.sh` | Lanzador de la aceptacion en tres partes. |
| `acepta.txt`, `acepta-1..3.txt` | Salida de la aceptacion: 0 comandos que no pasaron. |
| `previa.sql`, `lee_previa.sh`, `previa.txt` | Foto de produccion solo lectura de las cuotas vigentes. |
| `adverso.py`, `adverso.txt` | Caso adverso del formulario con Edge real (CDP): `1,000` no envia y muestra error. |
| `ui.md`, `ui/` | `orbit-verify` launch/doctor/drive-settings/cleanup y sus archivos. |
| `vecinos.md` | Suites vecinas, cobertura del contrato y fallos previos de esta PC. |
| `despliegue.txt`, `checklist.txt` | Fase 2: ensayo, despliegue real y checklist de `0244dc38`. |
| `prueba-real.txt` | `N i` y control 01X sobre el SHA integrado. |
| `previa-fase2.sql`, `lee_fase2.sh`, `previa-fase2.txt` | RO de esquema, sonda 0099 y cuotas vigentes. |
| `huella-codigo.sh`, `huella-codigo.txt` | md5 local/host/contenedor, SHA desplegado y marcas del bloque. |
| `ui-fase2.md`, `ui-fase2/`, `adverso-fase2.txt` | Recorrido y adverso de la fase 2. |

## Comando principal

```bash
uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N i
```

## Control 01X

```bash
CI=1 PYTHONPATH=.:tests uv run --frozen --python 3.12 python -m pytest -q \
  tests/test_config_write.py -k arreglos01_x
```

## Reversa

No hay DDL. `MOTOR_SHA=<sha> bash tools/despliegue/rollback.sh` revierte el
binario; no hay datos nuevos que deshacer.
