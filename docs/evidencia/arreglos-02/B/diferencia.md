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

**Corrección (F1 y su continuación F3).** La regla vive en
`app/optimizer/centavos.py` (`centavo_en_rango`): el bid decidido es un
centavo dentro de `[piso, techo]`. Se prefiere el centavo que no se aleja del
bid de hoy más que `destino`; si ese cae fuera del rango del goal, se toma el
del lado contrario, que obedece al goal aunque se aleje un centavo más (F3:
sin él, un bid que ya quedó fuera del rango por un cambio de goal se
congelaría para siempre, porque ningún redondeo hacia el bid de hoy cabría:
0.50 con techo 0.435 recorta a 0.43 y 0.10 con piso 0.118 sube a 0.12).
`None` solo cuando el rango no contiene ningún centavo (p. ej. piso 0.105 con
techo 0.108): el ajuste no sale (`Mantener(rango_bloquea_ajuste)`, motivo
existente del vocabulario cerrado). Los clamps de dirección y magnitud de
`_paso` siguen validando el resultado. La regla se aplica en los dos sitios
que redondean (`_paso` y R1): el patrón completo, no solo la línea anclada.
Con pisos y techos de centavos el comportamiento es idéntico al del diseño
(la comprobación del rango nunca dispara).

Prueba: `test_arreglos02_b_el_redondeo_no_sale_del_rango_del_goal`
(`tests/test_optimizer_politica.py`), roja contra `0e1fd46` con los dos casos
de arriba (`Mover(-0.12, 0.44)` sobre techo `0.435` y `Mover(0.15, 0.11)`
bajo piso `0.118`) y verde con la corrección: `Mover(-0.12, 0.43)` y
`Mover(0.15, 0.12)` dentro del rango, y `Mantener` solo con un rango sin
ningún centavo dentro.
