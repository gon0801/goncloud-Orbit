# Situacion

Orbit debe proponer precios para 18,000 listings de Amazon MX/US y Mercado
Libre. Ya usa Python, FastAPI, PostgreSQL 16 y crons; no hay Redis ni cola. Hay
precio actual, costo y stock por listing, pero costo o inventario pueden faltar
y cada observacion lleva fecha. Amazon permite escritura de precio; Mercado
Libre queda proposal-only hasta tener autorizacion operacional.

Reglas iniciales:

- no bajar del margen minimo por moneda;
- stock cero o dato faltante produce abstencion visible;
- poco stock puede subir precio, exceso de stock puede proponer una baja;
- mas de 50 cambios requieren confirmacion humana;
- toda aplicacion debe registrar valor anterior, propuesta, fuente de datos y
  resultado leido de vuelta;
- ninguna escritura se enciende sin reversa y reconciliacion;
- dos ciclos concurrentes no pueden aplicar dos precios al mismo listing.

Disena la capacidad y sus limites. No se pide inventar formulas finales ni
integrar un proveedor nuevo.
