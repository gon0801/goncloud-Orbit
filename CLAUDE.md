# Orbit

Sistema **nuevo desde cero** (base de datos nueva) que reemplaza al stack de
motores de Amazon Ads (`goncloud-MCP-2`, cancelado 2026-08-21). No se reutiliza
código viejo; solo se migran credenciales y datos verificados.

**Leer primero:** `docs/CONTEXTO.md` — reglas de diseño innegociables, trampas
del dominio, qué se migra y qué no, fases. Las fuentes verbatim están en
`docs/traspaso/` (diseño v2 = fuente de verdad de reglas y umbrales).

Los planes de Orbit se corren con el motor (`autopiloto: motor`).

## Pruebas y revisiones
- Pruebas nuevas SOLO en 4 casos: dinero (pujas, precios, presupuestos, cobros), escribir datos de produccion
  (base del servidor, Amazon, Mercado Libre, Shopify, Odoo), instalar/borrar/deshacer en las maquinas, y una
  falla que ya paso en produccion (una prueba, no una bateria). Lo demas: basta comprobar que corre. Nunca
  pruebas de texto, de las pruebas ni mutantes. Un revisor no pide pruebas fuera de eso.
- Un revisor solo bloquea por seguridad, datos, el comportamiento pedido roto o un caso de los 4 sin su
  prueba, siempre con el comando que lo reproduce. Lo demas va en notas y se corrige en la misma ronda.
- Nunca subir contrasenas ni llaves a un repo.
