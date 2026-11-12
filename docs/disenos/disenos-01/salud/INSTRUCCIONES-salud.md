# Orbit — Rediseño de la página Salud (`/salud`)

Archivos: `salud-diseno.png` (cómo debe verse, pestaña Amazon US abierta) y `salud-diseno.dc.html` (formato del editor de diseño: **no es HTML para pegar**; úsalo como referencia de estructura, textos, colores y medidas). Sistema visual igual al de las demás páginas (ver `INSTRUCCIONES-resumen.md`).

**Nota técnica:** las tablas del diseño están hechas con `div` en grid por una limitación del editor. En la implementación usa `<table>` reales.

## Reglas generales
1. **Solo cambia la presentación.** Mismos datos y textos que hoy.
2. Los datos del diseño son los reales del 11 nov, fijos como muestra.
3. No se pierde nada. Todo lo que hoy aparece por plataforma sigue estando: chips (Completo, En vivo), "Últimos datos de métricas · Estructura sincronizada", Ingesta Ads principal (tabla completa y fuente), Lo que se saltó en el último ciclo, Último ciclo, Avisos de propuesta, Destino de harvest, Cambios permitidos hoy (tabla), SP-API (tabla con las 7 columnas), Precios, Avisos de campaña, Últimos 14 ciclos (tabla). Y la sección "Jev: señal por búsqueda en cada grupo" con sus 4 líneas.

## Estructura
1. **Encabezado**: subtítulo "Último ciclo, los 14 anteriores y lo que se saltó", título "Salud".
2. **Resumen por plataforma** — **NUEVO**. Una tarjeta por plataforma con chips, línea de datos y una lista de chequeo con valores **calculados** de los datos existentes:
   - Ingesta Ads: "N de M reportes escritos · métrica AAAA-MM-DD · edad".
   - SP-API: "N de M fuentes ok · X filas saltadas" (suma de la columna Saltadas).
   - Último ciclo: id, fecha, decisiones, aplicadas.
   - Últimos 14 ciclos: "N de 14 completos · X decisiones" (suma).
   - Avisos de campaña: cuántos "se queda sin presupuesto" y cuántos "no limita".
   - Avisos de propuesta: texto actual.
   - Punto verde/ámbar: ámbar **solo** si hay filas saltadas o avisos de "sin presupuesto". No es un umbral nuevo: es "hay o no hay". Si un estado no es "Escrito", "ok" o "Completo", preguntar cómo marcarlo.
3. **Detalle con pestañas por plataforma** (antes se veían seguidas):
   - **Últimos 14 ciclos**: barras de decisiones por ciclo, del más antiguo al más reciente, con el número arriba; tabla completa plegada en "Ver tabla de ciclos".
   - **Avisos de campaña** agrupados por el inicio del texto: "Se queda sin presupuesto" y "El presupuesto no limita".
   - **Lo que se saltó en el último ciclo**: dos columnas con barras horizontales, motivo (`motivo_es`) y conteo, con total por columna. Sustituye al canvas de Chart.js y usa el mismo JSON (`datos-skips-<plataforma>`). Los títulos "Entidades" y "Términos" salen de las claves `entidad` / `termino`: **confirmar con el dueño** el nombre que quiere mostrar.
   - **Ingesta Ads principal** y **SP-API**: tablas completas. En SP-API, "Saltadas" > 0 en ámbar.
   - **Último ciclo**, **Cambios permitidos hoy** y **Precios** en tres tarjetas.
4. **Jev**: tarjeta oscura al final con las 4 líneas tal cual.

## Antes de desplegar
- Comparar cada valor con la página actual para las dos plataformas.
- Si aparece Mercado Libre u otra plataforma, debe salir otra tarjeta y otra pestaña.
- Revisar en móvil.
