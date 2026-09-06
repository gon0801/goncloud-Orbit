# Clave privada: revision

Hallazgos reales:

1. Bloqueante: falta fijar el grano/kind de `ad_metric`; sumar campaign junto
   con hojas keyword/target duplica costo e ingreso.
2. Bloqueante: `COALESCE(SUM(ad_revenue), 0)` convierte ausencia en venta cero.
3. Bloqueante: serializar Decimal monetario como float pierde el contrato exacto.
4. Mayor: no excluye/prohibe el dia en curso; `hasta` puede incluir dato parcial.
5. Mayor: no colapsa vintages por as-of antes de sumar y puede contar varias
   observaciones de la misma metrica.

Partes correctas que sirven como control de falsas alarmas:

- los parametros van ligados, no hay inyeccion SQL;
- agrupa y expone moneda, no suma MXN con USD en una fila;
- es lectura y no necesita una reversa de mutacion;
- el ORDER BY no es por si mismo un problema de exactitud.

No exigir Redis, cache, endpoint de escritura ni refactor ajeno al diff.
