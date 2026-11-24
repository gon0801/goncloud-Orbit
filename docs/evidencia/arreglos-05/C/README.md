# ARREGLOS 05 C: reposicion durable por fila

Evidencia del bloque C. El contrato es la seccion C de
[`../DISENO.md`](../DISENO.md); el plan, [`plans/arreglos-05.md`](../../../../plans/arreglos-05.md).

- [estados.md](estados.md): caminos nuevos y la casilla `estado x evento` que
  decide cada uno. Se escribio antes del primer commit de codigo.
- [resultado.md](resultado.md): por fila, impacto, reversa, decisiones,
  limites y seguimiento P2.
- [rojo.txt](rojo.txt): reproduccion contra el padre real `d0ed286e`.
- [acepta.sh](acepta.sh) y `acepta.txt` / `acepta-?.txt`: lanzador de las tres
  partes y sus salidas.
- [vecinos.md](vecinos.md): suites vecinas y conteos.
- [previa.sql](previa.sql), [previa.sh](previa.sh) y [previa.txt](previa.txt):
  lectura de solo lectura a produccion antes de la migracion.

Para repetir en esta PC:

```bash
export ORBIT_TEST_DSN=postgresql://orbit@127.0.0.1:55449/postgres
export UV_PROJECT_ENVIRONMENT=<venv del checkout principal>
bash docs/evidencia/arreglos-05/C/acepta.sh 1
bash docs/evidencia/arreglos-05/C/acepta.sh 2
bash docs/evidencia/arreglos-05/C/acepta.sh 3
```
