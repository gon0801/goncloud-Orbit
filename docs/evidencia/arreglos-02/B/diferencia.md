# Diferencia con el diseño (bloque B)

## F1 de la revisión: el redondeo puede salir del piso o del techo del goal

El seudocódigo de la sección «B» del diseño (`docs/evidencia/arreglos-02/DISENO.md`)
pone el redondeo después del clamp de `[piso, techo]` y sin ninguna
comprobación más:

    # nuevo = min(max(valor * (1 + factor_c), bid.piso), bid.techo)     (como hoy)
    # if centavos: nuevo = centavo_hacia(nuevo, desde=valor)            (NUEVO)

La revisión del bloque (hallazgo F1 de DeepSeek sobre `app/optimizer/politica.py`,
SHA `0e1fd46`) halló que con un piso o techo del goal que no es múltiplo de un
centavo, ese redondeo deja el bid fuera del rango y rompe el invariante del
docstring de `Mover` («`bid_nuevo` está en [piso, techo]»):

- recorte con techo `0.435`: `0.50 × 0.88 = 0.44` → clamp a `0.435` →
  `ROUND_CEILING` → `0.44`, por ENCIMA del techo;
- subida con piso `0.118`: `0.10 × 1.15 = 0.115` → clamp a `0.118` →
  `ROUND_FLOOR` → `0.11`, por DEBAJO del piso.

Hoy producción trae pisos y techos de centavos (leído en los inputs congelados
de las 51 decisiones de B.4: `0.1000`/`2.5000` en amazon_us y `1.0000`/`45.0000`
en amazon_mx), así que con el dato de hoy el redondeo era identidad respecto
del rango. El esquema no garantiza centavos (NUMERIC), y el invariante
declarado manda.

**Corrección.** La regla vive en `app/optimizer/centavos.py`
(`centavo_en_rango`): el centavo de `centavo_hacia` vale solo si queda dentro
de `[piso, techo]`; si el redondeo hacia el bid de hoy lo saca del rango, el
otro centavo se aleja del bid de hoy más que el paso decidido, así que ningún
bid de Amazon cumple a la vez el paso y el rango: el ajuste no sale
(`Mantener(rango_bloquea_ajuste)`, motivo existente del vocabulario cerrado).
Se aplica en los dos sitios que redondean (`_paso` y R1): el patrón completo,
no solo la línea anclada. Con pisos y techos de centavos el comportamiento es
idéntico al del diseño (la comprobación del rango nunca dispara).

Prueba: `test_arreglos02_b_el_redondeo_no_sale_del_rango_del_goal`
(`tests/test_optimizer_politica.py`), roja contra `0e1fd46` con los dos casos
de arriba (`Mover(-0.12, 0.44)` sobre techo `0.435` y `Mover(0.15, 0.11)`
bajo piso `0.118`) y verde con la corrección.
