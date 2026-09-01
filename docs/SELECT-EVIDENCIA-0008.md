# Evidencia SELECT — migración 0008 (precio multilisting US: MIN marcado)

Regla 8 del diseño: la migración se verificó contra el dato real de
producción (goncloud, 2026-09-01) con la definición nueva montada como
TEMP view (`v_ent_0008`) ANTES de aplicarla. Script: `out/_verify_0008.py`
(gitignorado), corrido dentro del contenedor `orbit-app-1` con
`ORBIT_DSN_READ`.

## Diagnóstico previo (por qué US publicaba cero)

Medido en prod el mismo día (scripts `out/_us_*.py`):

- En campañas ENABLED de US: 609 product_ads vivos, **0 sin listing, 0 sin
  producto** — el `catalogo_parcial` NO era mapeo.
- Los 920 product_ads US sin mapear viven en 24 campañas PAUSED, sin gasto
  en la ventana de 90 días.
- Causa real: los productos 120 (`NH-BLA-BRO-VBU-PLA`, ASINs B09QC3X991 a
  $106.20 y B0CR6YYSHP a $118.00) y 356 (`NH-PERS-ITA-VBU-PLA`, ASINs
  B0B36NHWY5 a $95.58 y B0CKB2413S a $106.20) tienen dos listings a precios
  distintos. El candado "un solo precio por producto" (0006/0007) los dejaba
  sin `price_i` → 273 entidades ausentes, ~**4,908.02 USD** de gasto maduro
  de 90 días escondido.

## Verificación de la definición nueva (TEMP view, antes de aplicar)

```
amazon_mx: diff ida=0 vuelta=0 (16.7s)     # MX intacta, fila por fila
amazon_us: diff ida=273 vuelta=0 (14.5s)   # US solo GANA las 273
US: nuevas=273, marcadas multilisting=273  # todas llevan la marca
US: nuevas SIN producto 120/356 en su grupo = 0
totales: MX vieja=108 nueva=108 marcadas=0
         US vieja=0   nueva=273 marcadas=273
```

- **MX: dif simétrica EXCEPT = 0 filas** en ambos sentidos sobre las 20
  columnas de la interfaz 0007 (la columna nueva queda fuera del diff).
- **US: las únicas filas nuevas son exactamente las 273** que el candado
  viejo excluía; todas marcadas y todas de grupos que contienen los
  productos 120/356. Cero efecto fuera del objetivo.
- Timing: ~15-17s por plataforma incluyendo ambos EXCEPT (la vista sola
  sigue en el rango de la 0007, ~2.5s).

## Reversa

La interfaz nueva AGREGA una columna, y PostgreSQL no permite quitar
columnas con `CREATE OR REPLACE`: la reversa es
`DROP VIEW v_contribucion_entidad CASCADE` (tumba tambien
`v_contribucion_cobertura`, que la referencia) y re-aplicar la 0007
completa (recrea ambas vistas).
