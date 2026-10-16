# M.4: adaptador MeLi + cliente escritura (MercadoMeli + MeliWriteClient)

## Alcance

Espejo MeLi del adaptador Amazon: `MercadoMeli` (vitrina/lectura +
escritura por sonda) y `MeliWriteClient` (PUT validado, sin loop).
Mudanzas para compartir lector: `MetaGoal`/`Lectura` a `puerto`,
`ventas_unidad`/`cambios_previos`/`ultima_observacion`/`historial` a
`fuentes`, `oferta_para_cotizar` a `insumos`, `spapi` adelgazado
(857 -> 670 lineas), `refrescar_tokens_meli` publico. `cli.py` compone
el mercado meli; `DEPLOY.md` documenta el cron D.4.

## Archivos

- `app/meli/precio_mercado.py`: `MercadoMeli`, `historia_meli_unidad`
  (nuevo, ~582 lineas).
- `app/meli/write_client.py`: `MeliWriteClient`, `validar_put_item`,
  `FORMA_ESCRITURA_MELI=pendiente_sonda` (reescrito).
- `app/precio/puerto.py`, `app/precio/fuentes.py`,
  `app/estimacion_insumos.py`, `app/spapi/precio_mercado.py`,
  `app/reputacion_clientes.py`, `app/cli.py`: mudanzas + conexion.
- `app/estimacion_repository.py`: allowlist 1398 con razon.
- `tests/test_precio02_mercado_meli.py` (nuevo, 32 tests),
  `tests/test_arq_precio_m.py` (11 candados).

## Decisiones selladas

- Escritor espejo de SP-API: 401 -> 1 refresh + reintento, 429 -> 1
  espera (Retry-After, default 1, tope 60), sin loop; `Acuse` nunca
  levanta; credenciales solo inyectadas (se componen en `cli.py`).
- `sonda` solo la ORIGINA `tools/precio_sonda.py`; el unico reenvio
  puro (`sonda=sonda`) es el adaptador MeLi (candado AST).
- INNER con catalogo es intencional: M.1 escribe obs + listing +
  miembros en una transaccion; huerfano = corrupcion, no se lee.
- Fee corrupto sale como `ValueError` (envuelve `InvalidOperation`,
  `KeyError`, `TypeError`), espejo de Amazon.
- B1: fallo interno marca la unidad (`escenario_incoherente`) sin
  tumbar el lote; error de BD (`psycopg.Error`) se re-levanta.

## Verificacion

- Ruff check + format: limpio (`comprobar.sh`).
- M.4: 43 passed (32 mercado + 11 arq).
- Vecinos: 372 passed (catalogo, ledger, cotizador, escenario,
  0073, mercado, corrida, architecture) + 85 reputacion.
- Mutantes: 36/36 muertos, 0 anclas rotas (`mutantes.md`).
- Adenda CodeRabbit (cierre): CR-2b (+2 tests, mutantes 38/38).
- Panel A+B: APROBADA sin bloqueantes; 3 obs A + 7 falsos negativos
  B triageados y aplicados (`panel.md`).
