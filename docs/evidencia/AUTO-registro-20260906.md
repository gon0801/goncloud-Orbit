# Registro AUTO-01..10 — estado comprobado 2026-09-06

Evidencia histórica del registro del backlog oficial (hoy vive en
`plans/ROADMAP.md`). No es estado vivo: actualizar la evidencia
antes de planificar o ejecutar cada AUTO.

Spec skip reason (del registro original): inventario documental de
pendientes; no se fijan reglas, umbrales, APIs ni cambios de
comportamiento. Los planes futuros deben definirlos.

## Estado comprobado al registrar

Consulta de producción del 2026-09-06, aproximadamente 23:49 UTC,
con `orbit_read` y transacciones READ ONLY:

- Configuración 15: optimizador live; goals habilitados en live
  para MX y US.
- Desde 2026-09-02: 78 ajustes de bid con
  `decision_application.verify_ok=true` (47 MX, 31 US), sobre
  keywords y product targets.
- En ese periodo: ninguna pausa ni negativo automático aplicado
  registrado; una propuesta harvest live, rechazada; `harvest_job`
  sin filas.
- `fabrica_lote`: cero filas. Las herramientas manuales no
  equivalen a un motor automático y estos conteos no incluyen todos
  sus actos históricos.
- Los ciclos del 2026-09-06 terminaron done con cero acciones
  nuevas. Falta de acción no demuestra avería: se registraron
  cooldown, evidencia insuficiente y condiciones de ajuste no
  cumplidas.

Gasto/clics de la sonda Ads MX están conciliados; ventas/compras
entre reportes siguen sin conciliar, con causa no demostrada.

## Fuentes citadas al registrar

- ORBIT 05: seguimiento del primer harvest automático completo
  (AUTO-01 referencia ese pendiente, no lo duplica).
- FABRICA 01, sección F2: harvest por grupos y bibliotecas
  (AUTO-02 solicita el plan futuro de F2).
- BIDS 01: herramientas manuales y guardas de archivado.
- Módulos avanzados: base de precios, promociones y reputación.
- Plan ORBIT 19, PR #185: catálogo abierto y comparación. Estos
  pendientes no se agregan a sus 17 tareas.
- Contexto del proyecto: una fuente por número, datos ausentes
  explícitos, dinero con moneda, madurez, conciliación externa
  y reversa.
