# D-I — la prueba nueva falla sin el arreglo y lo demás se comprobó contra PG desechable

SHA previo: `a6ffa3251136af769751cf29356f382b4da6f3ef` (origin/master al abrir el bloque, ya con D-H y su 0087).

## test_arreglos03_di_venta_de_us_guarda_su_importe_en_dolares (D-I.1)

Método: en la rama, con solo `tests/test_ledger_sync.py` cambiado (antes de tocar `app/ledger.py` y antes de la migración):

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_ledger_sync.py -k arreglos03_di_
1 failed, 24 deselected in 1.74s
```

Falla por comportamiento: el evento de la venta US no trae el importe en dólares.

```
>       assert (exacta.amount_original, exacta.amount_original_currency, exacta.fx_fecha) == (
E       AttributeError: 'EventoLedger' object has no attribute 'amount_original'
tests\test_ledger_sync.py:285: AttributeError
```

Qué discrimina: los cuatro casos de la sección «ORIG» (venta US `exact` -> `125.0000 USD` con su día; venta US
`nearest_prior` -> sin día; cargo US con `orig_currency` -> `(None, 'USD', día)`; venta MX -> las tres vacías) y,
además, que cada valor caiga en su columna del INSERT (`_COLS` contra `_params`): un orden cruzado de
`amount_original`, `amount_original_currency` y `fx_fecha` en la tupla de parámetros la pone en rojo.

## prueba.py (D-I.4)

Con el código de `a6ffa32` (copia de `app/` con `git archive a6ffa32 app` en una carpeta temporal):

```
ImportError: cannot import name '_original_de' from 'app.ledger'
```

Con el código del bloque: `D-I desplegado`, salida 0.

## Migración 0088 contra PG16 desechable (127.0.0.1:55433)

Base nueva con todas las migraciones del repo (menos 0011 y reversas), una corrida real de `sync_ledger` con cuatro
filas y luego la reversa (script temporal fuera del repo):

```
sonda con migracion: True
sync ok: True escritas: 4 saltadas: 0
   ('us-sale-1', 'sale', Decimal('2332.5000'), 'MXN', Decimal('125.0000'), 'USD', datetime.date(2026, 11, 12))
   ('us-sale-2', 'sale', Decimal('2332.5000'), 'MXN', Decimal('125.0000'), 'USD', None)
   ('us-fee-1', 'fee', Decimal('-349.8800'), 'MXN', None, 'USD', datetime.date(2026, 11, 12))
   ('mx-sale-1', 'sale', Decimal('980.0000'), 'MXN', None, None, None)
  rechazado original sin moneda -> ledger_original_con_moneda
  rechazado misma moneda -> ledger_original_otra_moneda
  rechazado otro signo -> ledger_original_mismo_signo
sonda tras reversa: False
constraints ledger_original_* tras reversa: 0
```

## ventas-usd.sh (D-I.5)

En WSL, con un `docker` falso que manda el `psql` al PG desechable y un `envia_recordatorio.py` falso que solo imprime
(nada toca goncloud): sin ventas US con dólares sale `f` y el aviso «Todavía no hay ventas de Estados Unidos con su
importe en dólares»; con una venta sale `t` y «Ya hay ventas de Estados Unidos con su importe en dólares»; con la
columna renombrada la lectura falla y el aviso dice «FALLÓ (psql salió con 3)» con el error de psql.
