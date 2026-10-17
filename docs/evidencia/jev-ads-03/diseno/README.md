# Evidencia del diseño de JEV ADS 03

Todo lo de esta carpeta se obtuvo en solo lectura (`orbit_read`) el
2026-10-17. Ninguna consulta escribe en producción ni en Amazon.

## Revisión a ciegas (la medición S.7 que JEV ADS 02 no corrió)

- **Muestra.** `muestra.sql` lee las señales vigentes y `sorteo.py` las
  sortea con la semilla 20261017. Las señales vigentes cambian cada corrida:
  los `senal_id` elegidos están en `revision-ciega.csv`. Preguntas "¿sirve?": 100, cada mercado con 50. En MX, 30
  `ajena` y 20 `relevante_sin_venta`. En US, 24 `ajena` (todas) y 26
  `relevante_sin_venta`. Preguntas "¿mover?": 35 `vende_en_otro` (US 13, MX 22).
- **A ciegas.** El dueño contestó en una página privada que no traía la
  lectura de Jev. La clave quedó fuera de la página.
- **Respuestas.** `revision-ciega.csv`, 135 de 135, contestadas de 22:18 a
  22:24 UTC.

| Medida | Resultado | Criterio de JEV ADS 02 |
| --- | --- | --- |
| Acuerdo en "¿sirve?", MX | 40/50 (80%) | 90% (S.8) |
| Acuerdo en "¿sirve?", US | 43/50 (86%) | 90% (S.8) |
| `ajena` confirmadas | 41/54 (76%) | 95% (E.2) |
| "Solo donde vende" | 6/35 (17%) | 90% (E.3) |

De las 13 `ajena` que el dueño no confirmó, 8 piden oro con quilates
("arras de oro 14k", "10k gold arras", "white gold"): 5 de MX y 3 de US. Una
más pide oro sin quilates ("arras de oro", US). Las otras 4 no son de oro:
"arras san judas", "lazo y arras de boda set", "unity coin set" y "arras
matrimoniales angelitos de mexico". Las arras del catálogo no son de 14k ni de
10k. Jev tiene razón sobre el producto.

## Las búsquedas de oro con quilates no venden en MX

`oro-con-quilates.sql`, del 2026-06-19 al 2026-10-16, última observación por
día. Los tres tipos son excluyentes. Salida en `oro-con-quilates.salida.txt`.

| Mercado | Tipo | Clics | Gasto | Ventas | Ventas por 100 clics |
| --- | --- | ---: | ---: | ---: | ---: |
| MX | Oro con quilates | 355 | 751.27 MXN | 2 | 0.56 |
| MX | Oro o dorado sin quilates | 1,134 | 2,821.95 MXN | 21 | 1.85 |
| MX | Resto | 12,540 | 43,452.32 MXN | 291 | 2.32 |
| US | Oro con quilates | 328 | 111.59 USD | 3 | 0.91 |
| US | Oro o dorado sin quilates | 860 | 301.34 USD | 8 | 0.93 |
| US | Resto | 11,329 | 5,911.10 USD | 104 | 0.92 |

Las 2 ventas de MX buscaban un baño de oro ("con baño de oro de 24 k",
"bañadas en oro blanco"). Las búsquedas de oro de verdad no vendieron. En US
convierten como el resto. Con esto, el dueño le da la razón a Jev en las cinco
de MX: el acuerdo de MX sube a 90% y las `ajena` confirmadas suben a 46/54
(85%).

## Lo que vale el bloqueo

Gasto en la ventana de cortes de cada señal vigente `ajena` del 2026-10-17: MX, 49
búsquedas y 976.37 MXN. US, 24 búsquedas y 38.63 USD.
