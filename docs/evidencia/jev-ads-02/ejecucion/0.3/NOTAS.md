# 0.3 — Tarifa de TypeSafe y costo del piloto y del prototipo (2026-10-12)

## Tarifa

| Campo | Valor |
| --- | --- |
| Modelo | `jev-1.13.0` |
| Precio | 0.042 USD por millón de tokens de entrada. 42 USD por mil millones. |
| Salida | 0 USD. La página cobra por token de entrada y deja la salida en cero. |
| Fuente | [Models](https://docs.typesafe.ai/models), tabla Current models. Texto leído: "Price (per Btok / per Mtok): $42 / $0.042" y "Charged per input token. Output tokens are free." |
| Fecha de consulta | 2026-10-12 |

La misma cifra estaba en `docs/evidencia/jev-ads-01/how-why.md` como precio de documentación, sin confirmar. Esta lectura de la tabla la confirma.

## Costo del piloto

Usage del dataset `docs/evidencia/jev-ads-01/ejecucion/3.1/dataset-piloto.csv`, 648 filas. La suma de `tokens_entrada` es 643,945 y la de `tokens_salida` es 33,930. Esos totales son los de `ejecucion/3.1/REPORTE.md`.

Cálculo con `Decimal`, en USD:

```text
643945 * Decimal("0.042") / Decimal("1000000") = 0.02704569
```

La salida suma 0 USD.

## Costo del prototipo

15 solicitudes, las de `diseno/consultas/prototipo-jev-analisis.sql`. Consulta y salida: `prototipo-usage.sql` y `prototipo-usage.salida.txt`, lectura con `orbit_read` el 2026-10-12.

| Fuente | Eventos resultado | Tokens de entrada | Tokens de salida |
| --- | ---: | ---: | ---: |
| Cruce de la consulta de análisis, por término | 4,811 | 6,106,876 | 251,894 |
| Suma directa sobre `jev_par_evento` | 4,811 | 6,106,876 | 251,894 |

Las dos sumas coinciden. De los 4,811 resultados, 4,802 traen respuesta y 9 traen error. Esos 9 no traen `usage`. Ninguna fila con `usage` dejó fuera `input_tokens` o `output_tokens` (`usage_incompleto` = 0 en la relectura del 2026-10-13; los totales no se movieron). Ningún resultado quedó fuera del censo de su solicitud, y ningún término está repetido dentro de una solicitud.

`prototipos.md` decía unos 6.1 M de tokens de entrada. La suma es 6,106,876. Contra 6,100,000 hay 6,876 tokens de más, que es el redondeo de "unos 6.1 M". No apareció otro bloque de usage.

```text
6106876 * Decimal("0.042") / Decimal("1000000") = 0.256488792
```

La salida, 251,894 tokens, suma 0 USD.

## Método

El dinero se multiplicó con `Decimal`. La suma en Postgres usa `numeric`, no `float`. La unidad es el token de `usage.input_tokens` y `usage.output_tokens` en `jev_par_evento.usage`, solo filas `tipo = 'resultado'`.

## Límites

- La tarifa es la de lista pública. Si la cuenta tiene un descuento, esta cuenta no lo aplica.
- La misma página avisa que los límites de ritmo cambian sin previo aviso. Al consultarla decían 100,000 tokens por segundo y 80 solicitudes por segundo.
- Los 9 resultados con error no traen usage. Si el proveedor cobró esa entrada, no está en los 6,106,876.
- El costo de salida es 0 porque la tarifa lo dice, y los tokens de salida sí se contaron.
