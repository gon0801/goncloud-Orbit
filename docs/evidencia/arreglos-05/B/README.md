# Evidencia del bloque B de ARREGLOS 05

Bloque de implementacion, `lane: release`. Base: `master` en
`23ba2385c7394ef5f9693e2802dbe9bb640ac791`. Rama `arreglos-05/b`.
Fecha: 2026-11-24 (America/Vancouver). Codigo verificado: `5fbb78ef`.

Esta carpeta convive con `docs/evidencia/arreglos-05/base/`, la foto de la fase
de diseno, que no cambia. `B/` no tiene hermana en minusculas.

| Fila | Archivos |
|---|---|
| Todas | [resultado.md](resultado.md): fila por fila, rojo y verde, vecinos, lectura previa, decisiones, limites, recorrido previo al empuje y como se regresa. |
| B.1 a B.3 | Comandos y salidas en [acepta.txt](acepta.txt), que genera [acepta.sh](acepta.sh). |
| B.1 a B.3 | [rojo.txt](rojo.txt): las regresiones contra el codigo anterior. |
| B.1 a B.3 | [previa.sql](previa.sql), [previa.sh](previa.sh) y [previa.txt](previa.txt): lectura previa de produccion, solo conteos, fechas y nombres de objetos. |
| Vecinos | [vecinos.md](vecinos.md): cada grupo, su conteo y su linea base en el padre. |
| Modelo | [../A/estados.md](../A/estados.md), seccion «Ampliacion del bloque B». |

## Entorno de esta PC

- Preambulo obligatorio antes de cada comando de aceptacion (ademas de quitar
  `MSYS_NO_PATHCONV` y `MSYS2_ARG_CONV_EXCL`, que `acepta.sh` hace solo):

  ```bash
  export PATH="/c/Users/ehven/.local/share/orbit-lab/bin:$PATH"
  ```

  `comprobar.sh` exporta `CI=1` y recolecta todo `tests/`;
  `tests/test_despliegue_motor.py` lanza `RuntimeError` al importar si `CI`
  esta definida y faltan `psql`, `pg_dump` o `flock`. Los accesos solo cubren
  esa comprobacion de presencia.
- Temporal propio de pytest (`TMPDIR`), creado por `acepta.sh`.
- Sin `pre-commit`: antes de cada commit se corren a mano
  `gitleaks protect --staged --redact --no-banner`, `git diff --cached --check`
  y la busqueda de marcadores de conflicto.
- Con `core.ignorecase=true`, compruebar despues de cada `git add` que
  `git ls-files docs/evidencia/arreglos-05 | tr 'A-Z' 'a-z' | sort | uniq -d`
  no imprime nada.

## Reproducir

Desde la raiz del checkout, con el preambulo de arriba y `ORBIT_TEST_DSN` en el
PG desechable. `acepta.sh` va en tres partes para que ninguna pase de ocho
minutos en esta PC:

```bash
bash docs/evidencia/arreglos-05/B/acepta.sh 1 > docs/evidencia/arreglos-05/B/acepta-1.txt
bash docs/evidencia/arreglos-05/B/acepta.sh 2 > docs/evidencia/arreglos-05/B/acepta-2.txt
bash docs/evidencia/arreglos-05/B/acepta.sh 3 > docs/evidencia/arreglos-05/B/acepta-3.txt
cat docs/evidencia/arreglos-05/B/acepta-{1,2,3}.txt > docs/evidencia/arreglos-05/B/acepta.txt
```

Correlo con el arbol limpio: el encabezado lo registra. La unica omision que
admite son dos pruebas de arquitectura que este sistema operativo no puede
ejecutar.

`previa.sh` lee produccion. Solo se corre con la lectura de solo lectura
autorizada. `ORBIT_PREVIA_LEE=<script>` lo prueba en seco sin tocar produccion.
