# Contrato relevante

- `ad_metric` contiene filas de grano `campaign`, `keyword` y `product_target`;
  las hojas ya estan incluidas en el total de campana.
- Cada metrica puede tener varios vintages append-only identificados por
  `observed_at`; un informe debe elegir uno por entidad y fecha para su as-of.
- Dinero sale del API como string decimal junto con moneda, nunca float.
- Revenue faltante queda `null`, no cero.
- El dia en curso UTC es parcial y se excluye de resumenes decisorios.
- El driver liga `%s` a parametros; esa forma no concatena texto del usuario.
