# Orbit — Rediseño de la página Contribución (`/contribucion`)

Archivos: `contribucion-diseno.png` (cómo debe verse) y `contribucion-diseno.dc.html` (formato del editor de diseño: **no es HTML para pegar**; úsalo como referencia de estructura, textos, colores y medidas). Sistema visual igual al de las demás páginas (ver `INSTRUCCIONES-resumen.md`).

**Nota técnica:** la tabla del diseño está hecha con `div` en grid por una limitación del editor. En la implementación usa `<table>` real.

## Reglas generales
1. **Solo cambia la presentación.** Mismos datos y textos. La página sigue siendo **"no decisoria"** y eso debe seguir visible.
2. Los datos del diseño son los reales (90 días cerrados: 2026-07-31 .. 2026-10-28), fijos como muestra.
3. No se pierde nada: título, explicación ("Son 90 días que terminan hace 15…"), por plataforma el rango de fechas y la leyenda ("contribucion pre-cargos · no decisoria · MONEDA · el tipo de cambio aproximado se marca en la fila"). Por fila: campaña, contribución "mín .. máx" (o "— (motivo)"), fechas (o "—") y los chips de la fila ("precio min multilisting" en ámbar, "Tipo de cambio aproximado: del último día con dato" en gris).

## Estructura
1. **Encabezado**: subtítulo "antes de cargos (90 días cerrados)", título "Contribución por campaña", y el párrafo explicativo.
2. **Tarjeta por plataforma** — **NUEVO**: número de campañas, rango de fechas, barra segmentada y conteo por clase (abajo), leyenda.
3. Una línea que explica las barras y las clases.
4. **Tabla por plataforma**: Campaña · Contribución (texto + clase) · **barra de rango** — **NUEVO** · Datos (fechas + chips).

## Clase por signo (solo presentación)
- **Positiva**: mín > 0. Verde `#0F766E` / barra `#14B8A6`.
- **Negativa**: máx < 0. Rojo ladrillo `#9A3412` / barra `#C2410C`.
- **Cruza 0**: mín ≤ 0 ≤ máx. Gris pizarra `#334155` / barra `#64748B`.
- **Sin dato**: no hay rango (ej. "catálogo incompleto", "sin ventas en el libro para repartir").

## Barra de rango
- Va del mínimo al máximo; si mín = máx, un punto.
- Línea vertical en 0 en todas las filas; eje con el mínimo, el 0 y el máximo de la escala.
- Escala por plataforma: de min(0, menor mínimo) hasta el mayor máximo, redondeados.
- Si el mayor máximo es más de 3 veces el segundo (hoy: AC - Category Exact - MX, 122,380.55), la escala se recorta y esa barra termina en el borde con "▸ valor real". **Confirmar con el dueño** si prefiere este recorte o una escala logarítmica.

## Antes de desplegar
- Comparar cada fila con la página actual.
- Comprobar que una fila con mín = máx se ve como punto y que las filas sin dato no dibujan barra.
- Revisar en móvil (la tabla hace scroll horizontal dentro de su caja).
