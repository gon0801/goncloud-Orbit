# ARREGLOS 05 D: toda mutacion Ads pasa por el cliente protegido

Evidencia del bloque D. El contrato es la seccion «D-E. Escritor unico y
practicas vencidas» de [`../DISENO.md`](../DISENO.md), solo la parte D; el plan,
[`plans/arreglos-05.md`](../../../../plans/arreglos-05.md).

- [escritores.tsv](escritores.tsv): inventario de cada caller (archivo,
  funcion, metodo, ruta, perfil, modo, ledger, confirmacion por item y reversa)
  y el contraste con el `rg` que pide la fila D.1. Se escribio antes del primer
  commit de codigo.
- [caminos.md](caminos.md): las dos formas evaluadas, la sintesis, cada camino
  nuevo y como se decide (operacion del cliente, perfil y ruta sellados,
  intencion pre-HTTP, ACK y reversa). Se escribio antes del primer commit de
  codigo.
- [resultado.md](resultado.md): por fila, impacto, reversa, rojo/verde,
  decisiones, limites y seguimiento P2.
- [rojo.txt](rojo.txt): la regresion del bloque contra el padre real
  `7ef47542`.
- [acepta.sh](acepta.sh) y `acepta.txt` / `acepta-?.txt`: lanzador de las cuatro
  partes y sus salidas.

Para repetir en esta PC:

```bash
export ORBIT_TEST_DSN=postgresql://orbit@127.0.0.1:55449/postgres
export UV_PROJECT_ENVIRONMENT=<venv del checkout principal>
bash docs/evidencia/arreglos-05/D/acepta.sh 1
bash docs/evidencia/arreglos-05/D/acepta.sh 2
bash docs/evidencia/arreglos-05/D/acepta.sh 3
bash docs/evidencia/arreglos-05/D/acepta.sh 4
```

El bloque no trae migracion: `docs/DATABASE.md`, `migrations/` y el selector
del instalador quedan como los dejo C.
