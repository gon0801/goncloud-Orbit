# Evidencia del bloque A de ARREGLOS 05

Bloque de implementacion, `lane: release`. Base: `master` en
`149bf454f9dbd2bca90c23f84c2ad1ea7537b34c`. Rama `arreglos-05/a`.
Fecha: 2026-11-23 (America/Vancouver).

Esta carpeta convive con `docs/evidencia/arreglos-05/base/`, la foto de la fase
de diseno, que no cambia. `A/` no tiene hermana en minusculas.

| Fila | Archivos |
|---|---|
| Todas | [resultado.md](resultado.md): fila por fila, rojo y verde, vecinos, decisiones, limites. |
| A.1 a A.4 | Comandos y salidas en [acepta.txt](acepta.txt), que genera [acepta.sh](acepta.sh). |
| A.1 | [rojo.txt](rojo.txt): las regresiones contra el codigo anterior. |
| A.2, A.3, A.4 | [previa.sql](previa.sql), [previa.sh](previa.sh) y [previa.txt](previa.txt): lectura de produccion, solo conteos, fechas y nombres de objetos. |

## Entorno de esta PC

- Preambulo obligatorio antes de cada comando de aceptacion (ademas de quitar
  `MSYS_NO_PATHCONV` y `MSYS2_ARG_CONV_EXCL`, que `acepta.sh` hace solo):

  ```bash
  export PATH="/c/Users/ehven/.local/share/orbit-lab/bin:$PATH"
  ```

  `comprobar.sh` exporta `CI=1` y recolecta todo `tests/`;
  `tests/test_despliegue_motor.py` lanza `RuntimeError` al importar si `CI`
  esta definida y faltan `psql`, `pg_dump` o `flock`. Sin el directorio,
  `N a` sale 2 antes de ejecutar ninguna prueba del bloque (punto 4 de
  `BASE/diferencias.md`, que BASE anoto para el comando de 01X). Los accesos
  solo cubren esa comprobacion de presencia: B.5 no es ejecutable aqui porque
  los argumentos no traducen rutas de Windows dentro de WSL.
- Temporal propio de pytest (`TMPDIR`): el del sistema se comparte entre
  sesiones y pytest se cae al cerrar (punto 3 de `BASE/diferencias.md`).
- Sin `pre-commit`: antes de cada commit se corren a mano
  `gitleaks protect --staged --redact --no-banner` y la busqueda de marcadores
  de conflicto.

## Reproducir

Desde la raiz del checkout, con el preambulo de arriba y `ORBIT_TEST_DSN` en el
PG desechable:

```bash
bash docs/evidencia/arreglos-05/A/acepta.sh > docs/evidencia/arreglos-05/A/acepta.txt
```

`previa.sh` lee produccion. Solo se corre con la lectura de solo lectura
autorizada. `ORBIT_PREVIA_LEE=<script>` lo prueba en seco sin tocar produccion.
