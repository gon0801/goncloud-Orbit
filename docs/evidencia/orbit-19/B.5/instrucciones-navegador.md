# ORBIT 19 / B.5 — Como reproducir el comparador en navegador

Sembrado local que reproduce EXACTAMENTE los casos AC6/AC8/AC10 y los
fixtures clave de 0.4 §8. Ninguna escritura a Amazon: la DB es local y el
comparador solo hace GET.

## 1. Sembrar la DB local

Requisitos: Postgres local escuchando en `localhost:5432` (usuario
`orbit`/`orbit`, el de los tests) y el venv del repo.

```bash
cd /Users/dn/dev/goncloud-Orbit
PYTHONPATH=. .venv/bin/python tools/semilla_comparador.py
```

Salida esperada (resumen de lo sembrado):

```
DB lista: orbit_b5_comparador
  L1: ACoS 50% SIN etiqueta (dos grupos, targets 25 y 30); provisional; economia compartida con L2
  L2: Sin datos (ausencia de Ads); mismo producto que L1
  L3: ACoS 25% = objetivo 25% => Dentro del objetivo; muestra 1 fecha / 1 compra
  L4: ACoS 25% = objetivo 25% => Dentro del objetivo; muestra 10 fechas / 100 compras
  L5: Gasto sin ventas (sales30d=0 OBSERVADO, ACoS null); margen negativo con motivo
  L6: Muestra limitada de margen (12 dias) aparte; ACoS 30% => Por encima del objetivo
  disponibilidad: L4 stock 0 OBSERVADO; L3 positivo con frescura vieja (30d);
                  L1 positivo fresco; L2/L5/L6 desconocido; Featured Offer Sin verificar
```

El script es re-ejecutable: borra y recrea `orbit_b5_comparador`.

## 2. Levantar la app

```bash
cd /Users/dn/dev/goncloud-Orbit
ORBIT_DSN_READ=postgresql://orbit:orbit@localhost:5432/orbit_b5_comparador \
  PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --port 8765
```

## 3. URL exacta de la vista

```
http://localhost:8765/campanas/nuevas#fabrica-comparador
```

La seccion "Comparar publicaciones" esta entre el formulario (paso 1) y la
revision del plan (paso 2). Plataforma: Amazon Mexico.

## 4. Que verificar y capturar

| Caso | Donde mirar | Esperado |
|---|---|---|
| AC6 igual ACoS | B0BBBBBBB01 y B0BBBBBBB02 | Ambas "Dentro del objetivo" con ACoS 25 %; muestras "1 fecha / 1 compra" vs "10 fechas / 100 compras"; conteos visibles junto al resultado |
| AC6 ausencia vs 0 | B0AAAAAAA02 vs B0CCCCCCC01 | Ausencia = "Sin datos"; cero OBSERVADO = "Gasto sin ventas" con ACoS "Sin dato" |
| AC8 stock | B0BBBBBBB02 (0 FBA), B0BBBBBBB01 (positivo, frescura 30d vieja), B0AAAAAAA02 (Desconocido) | Tres estados distinguibles; ninguna bloquea seleccion |
| Featured Offer | Todas las filas | Siempre "Featured Offer: Sin verificar" |
| AC10 orden/filtros | Selecciona una publicacion en el paso 1, luego cambia orden (8 metricas) y filtro | La casilla sigue marcada y la fila muestra "Seleccionada"; cero POST en la pestana Network |
| Objetivo/grano/ventana/muestra | Resumen superior de la seccion | Ventana Ads, grano, objetivo del grupo y regla D4 siempre visibles |
| Distintos objetivos | B0AAAAAAA01 | ACoS 50 % SIN etiqueta dentro/fuera (dos grupos con targets 25 y 30); nunca un promedio |
| Muestra limitada (D4) | B0DDDDDDD01 | "Limitada: 12 dias con venta, margen ... (no entra al orden)" APARTE del margen maduro; no afecta el sort |
| Margen negativo | B0CCCCCCC01 | "Margen negativo." como aviso; sigue seleccionable |
| Mismo producto | B0AAAAAAA01 y B0AAAAAAA02 | Misma economia de producto (total financiero compartido, no duplicado) |
| Provisional | B0AAAAAAA01 y B0BBBBBBB02 | "(provisional)" cuando la evidencia 30d no esta madura |
| Sin datos Ads | B0AAAAAAA02 | Etiqueta "Sin datos"; la etiqueta "Por probar" NO existe en la UI |

### Contadores HTTP (cero escrituras)

Con la pestana Network abierta: al cargar, ordenar y filtrar el comparador
solo se emiten GET a `/api/fabrica/evaluacion`. Ningun POST comercial se
dispara automaticamente (`/crear` y `/lotes/{id}/{accion}` exigen token y
frase de confirmacion explicita).

## 5. Apagar

`Ctrl-C` en uvicorn. Para limpiar la DB: `dropdb orbit_b5_comparador` (o
re-ejecutar el sembrado).
