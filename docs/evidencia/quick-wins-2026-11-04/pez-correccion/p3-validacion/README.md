# PEZ 01 P3: lote operativo y reversa

Implementación: `tools/corrige_meli_skus.py`. No se ejecutó contra producción.
El lote comercial de nueve correspondencias y sus costos reales lo genera el
lead por separado; los importes de las pruebas son datos sintéticos locales.

## Uso

Desde la raíz del repo y con `ORBIT_DSN_ADMIN` cargado por el procedimiento
operativo, sin imprimirlo ni pasarlo como argumento:

```bash
uv run --frozen python -m tools.corrige_meli_skus lote.json
uv run --frozen python -m tools.corrige_meli_skus lote.json --apply --respaldo /ruta/privada/nueva.json
uv run --frozen python -m tools.corrige_meli_skus --revertir /ruta/privada/nueva.json
```

El primer comando es un ensayo sin escrituras de datos; toma locks breves para
comprobar las mismas condiciones que la aplicación. `--dry-run` es equivalente.
No se acepta `--respaldo` fuera de `--apply`; la reversa recibe solamente el
respaldo existente. No hay HTTP ni consultas a Odoo/accounting/bridge.

Formato del lote:

```json
{
  "version": 1,
  "filas": [
    {
      "listing_id": 123,
      "item_id": "MLM_ID_VERIFICADO",
      "variante_ext": "VARIANTE_VERIFICADA",
      "seller_sku": "SKU_PUBLICADO",
      "product_id_antes": null,
      "activo_antes": true,
      "product_id_destino": 456,
      "odoo_sku": "SKU_CANONICO_SET",
      "costo": {
        "id": 789,
        "importe": "77.4700",
        "moneda": "MXN",
        "includes_tax": false,
        "valid_from": "2020-01-01",
        "valid_to": null
      },
      "evidencia": "referencia a identidad y costo verificados"
    }
  ]
}
```

El ejemplo ilustra una fila ficticia: el lote ejecutable exige exactamente nueve,
con claves miembro y seller_sku distintos. `variante_ext` puede ser vacío para
una publicación sin variantes. El importe debe ser cadena decimal positiva,
representable en NUMERIC(14,4); no se admiten floats, ceros ni costos ausentes.

## Garantías y límites

- Una sola transacción con conexión propia autocommit: primero valida las nueve
  filas, después inserta nueve aliases y cambia solamente `product_id` de los
  nueve miembros. No cambia SKU, actividad, visto_at, listing ancla ni costos.
- Comprueba item/listing/variante, cardinalidad global de cada SKU, miembro
  con actividad igual a `activo_antes` booleano explícito, antes sin producto
  y alias ausente; producto destino activo con SKU
  canónico esperado, sin conflicto de SKU exacto; costo único vigente a la fecha
  UTC, con ID, importe Decimal, moneda, IVA y límites de vigencia esperados.
- Locks en orden fijo sobre `product`, `sku_cost`, `listing`, `meli_sku_alias`
  y `listing_miembro`, además de filas leídas: impiden que aparezcan SKU,
  miembros, aliases o vigencias entre la comprobación y el commit. Son locks
  breves de tabla, tomados por `public.meli_sku_alias_bloquear()`: función
  SECURITY DEFINER sin argumentos ni DML, SQL fijo y objetos cualificados.
  Solo app_admin puede ejecutarla; no se otorga UPDATE de producto ni costo.
  app_admin recibe únicamente UPDATE(product_id) del miembro, además de los
  permisos del alias. Los locks pueden bloquear temporalmente una ingesta; esperan
  como máximo 5 segundos por lock y 15 segundos por sentencia. La conexión
  tiene timeout de 15 segundos. Ante contención se aborta y se repite después.
- Respaldo creado con `O_EXCL`, modo 0600, fsync de contenido y directorio
  antes del COMMIT. Incluye lote, fotografía anterior y aplicada, aliases
  creados con su fecha, y SHA-256 canónico verificado por la reversa. El hash
  visible en la salida corresponde al JSON sin el campo `sha256`, no al
  archivo completo. Nunca sobrescribe un respaldo existente. Si el commit
  falla después de persistir el respaldo, el archivo puede quedar conservado;
  no implica que se aplicó: la reversa exige el estado aplicado en la base.
- Repetir aplicación o reversa aborta explícitamente sin nuevas escrituras.
  La reversa valida todo antes de tocar nada y solo restaura los NULL previos
  y quita los aliases exactos creados por el lote. Una nueva ingesta que haya
  cambiado `visto_at`, un cambio de ancla o de alias hace abortar la reversa;
  no se pisan cambios posteriores. Requiere revisión antes de otro intento.
- Las credenciales solo se leen del entorno y se registran para redacción.
  Nunca se almacenan en lote/respaldo, argumentos ni salida.

## Comprobación local

`pytest.txt`: **7 passed** en PostgreSQL local desechable. Cada caso crea y borra
su propia base con las migraciones del catálogo y la 0075; no usa datos del
servidor. El setup se hace con el dueño de la base y toda llamada operativa
se ejecuta con `SET ROLE app_admin`, sin UPDATE de producto ni sku_cost. Cubre ensayo sin cambios, aplicación de nueve y conservación de
la décima fila, SKU/visto_at/anclas/costos intactos, dos miembros inactivos
que siguen inactivos al aplicar y revertir, respaldo privado,
reintento rechazado y reversa completa. Las derivas de SKU, costo, duplicado,
conflicto canónico y respaldo ya existente abortan sin cambios parciales;
la reversa rechaza una observación posterior sin pisarla.

```bash
ORBIT_TEST_DSN=postgresql://orbit@127.0.0.1:55435/postgres uv run --frozen python -m pytest -q tests/test_corrige_meli_skus.py
uv run --frozen ruff check tools/corrige_meli_skus.py tests/test_corrige_meli_skus.py
python3 -m py_compile tools/corrige_meli_skus.py tests/test_corrige_meli_skus.py
pre-commit run --all-files
```

Se observó el fallo inicial por herramienta todavía ausente antes de
implementarla. La primera prueba con app_admin también demostró el fallo de
permisos de LOCK TABLE sobre product; se corrigió con el helper de locks y
permisos mínimos, manteniendo la prueba en el rol real. No se reclama regresión demostrada contra un escritor anterior:
esta herramienta y su operación son nuevas. Ruff, compilación y pre-commit
pasaron. La revisión de secretos explícita incluye los archivos nuevos.

Corrección de revisión: `actividad-rojo.txt` demuestra que la implementación
anterior rechazaba dos miembros inactivos del lote autorizado. El lote ahora
exige `activo_antes` booleano explícito y lo compara como precondición; la
operación sigue escribiendo únicamente `product_id`, nunca reactiva miembros.
El caso de aplicación/reversa recorre siete activos y dos inactivos y compara
todos los campos ajenos a product_id antes/después.
