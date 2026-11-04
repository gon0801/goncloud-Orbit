# Evidencia M.2: abre ledger MeLi

## Alcance

- `app/ledger.py`: `"meli": "meli"` en mapa origen (ya no se excluye),
  huecos fuertes meli (sin sku / sin producto / sin cantidad),
  contadores `meli_sin_*` en ResultadoSync + linea CLI.
- `app/meli/ledger.py` (nuevo): `AtribuidorMeli` con guards-first
  (item_id externo_unico > sku unico > mapa), `close()`, registro default.
- `app/precio/envio.py`: `clasificar_cargo(..., platform)`; `fee`->"meli_envio"
  solo si platform=="meli"; `costo_de_orden` suma rama meli_envio por orden.
- `app/envio_muestras.py`: pasa plataforma al clasificador.
- Auditoria previa en `auditoria.md` (probes Q1-Q4, P1-P10 contra snapshot
  accounting; ISR meli ~2% validado vs control amazon 2.33%).

## Pruebas

`tests/test_precio02_meli_ledger.py`: 22 tests, 22 passed.
Vecinos verdes: test_ledger_sync, test_precio_envio, test_envio_muestras,
test_ledger_atribucion, test_precio02_atribucion (86 passed en conjunto).

## Mutantes

22/21+1 muertos, detalle en `mutantes.md`.

## Panel

4/4 revisores (Muse A, GLM 5.3, GLM 5.2, GLM 4.7b) + fixes aplicados.
Detalle en `panel.md`.

## Medicion

`medicion.md`: ISR MeLi 0.025 neto (insumo `isr_tasa` de M.3),
contrato campo2 `fee` solo-MeLi y forma real del `raw_payload`.

## Repro

`./comprobar.sh` (requiere Postgres loop en 5432 + ORBIT_TEST_DSN).
