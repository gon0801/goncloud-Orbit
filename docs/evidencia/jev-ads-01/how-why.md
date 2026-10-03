# Jev Ads: how y why verificados

Solicitud: disenar Jev en Ads (negativos, harvest, semillas de biblioteca), primero how/why, prototipar dudas. Resenas fuera. Entregable: diseno revisable y evidencia, no activacion productiva. Base repo /Users/dn/dev/wt/jev-ads-design @3cbfab5, rama design/jev-ads-01. Arquitectura Python>=3.12/FastAPI/psycopg/httpx/Postgres, sin Redis ni nueva plataforma.

## Recorrido actual, verificado por how

- windows.py:327/415/529: agregado por ad_group+termino, ventanas maduras y None preservado. No producto comprado por termino.
- cycle.py:1669 _procesa_grupo ->1740 destino ->1747 hygiene ->1770 decision congelada.
- optimizer/hygiene.py:301 ASIN-like fuera;309 negativo solo cero pedidos y clics/gasto suficientes;340 harvest>=2 ordenes y ACoS<=min(35,target), duplicado EXACT por texto normalizado.
- optimizer/harvest_destino.py:177: category_exact del grupo, excepcion, terna; nunca por nombre; origen=destino invalido.
- apply_cola.py:482 cola de veto48h;855 revalida con misma hygiene;894 descarta negativo si vendio;1148/1210 liberacion, claim/cuota/ledger. La cola LIVE puede ejecutar SIN aprobacion positiva al vencer veto.
- apply_harvest.py:14-25 negativo origen ->keyword EXACT destino ->readback;1782/1814 confirma keyword y luego hermanas. Reversa keyword->hermanas propias->origen. NO insertar HTTP Jev entre estas operaciones.
- biblioteca.py:12-19 negativos de ruteo origen/hermanas NUNCA alimentan negative_biblioteca. fabrica_plan.py:367 reutiliza biblioteca+terminos con ventas, EXACT solo harvest.
- api_dashboard.py:1047/1216 lecturas cortes, templates/cortes.html:18 poca explicacion negativa. API GET debe ser solo lectura.
- product.name, listing external_id/SKU, ad_entity listing_id/parent_id existen. spapi/listings.py:81 conserva estado/product_type, no ficha comercial completa. Resolver conjunto completo de productos exige census; no suponer atributos disponibles ni identidad de compra.

## Motivos documentados, why

- docs/CONTEXTO.md:30-35 sistema viejo no actuaba por filtros excesivos. No Jev gate obligatorio por defecto.
- PR23/commit6968540: dueno reviso57 terminos; costo debe corresponder al valor producto, no piso universal; contrafactual historico28 cortes->1 (no resultado Jev).
- docs/CONTEXTO.md:164-189 enumera dinero perdido por dato faltante inventado, monedas, backtest contaminado y madurez. Fuente documental, no auditado vivo hoy.
- PR253: cosechar ganador a exacta y negar hermanas para dirigir trafico; no deshacer keyword confirmada por fallo hermana.
- PR258 destino/evidencia congelados representan lo realmente usado; revalidacion evita destino obsoleto.
- PR378 origen=destino bloqueo keyword real; IDs deben validarse por codigo, no por semantica.
- PR345/360 desenlace unico/auditable, ningun bloqueo invisible.
- GitHub Issues consultados: ninguno en repo. Pages Orbit/Jev/TypeSafe: sin resultados. No MCP chat/infra/error/warehouse. Docs y PRs no contienen evaluacion Jev ni beneficio probado.

## TypeSafe leido en docs oficiales

- POST https://api.typesafe.ai/v1/systemone state/model/questions; Choice categorias, Noul probabilidad si, Score rubrica ordinal. IDs preguntas no contexto para modelo.
- Jev solo texto, no genera prosa ni keywords; motivos UI plantillas/categorias respaldadas por datos.
- Pin jev-1.13.0; alias latest mutable. English mejor segun docs, probar MX/US. Duda != dato ausente; datos incompletos deben ser estado propio.
- Precio docs .042USD/M tokens entrada, salida gratis, no estimar costo real sin usage. Nunca secretos en repo/prototipo/evidencia.
- batch preguntas independientes sobre mismo estado en paralelo, no una respuesta input invisible otra. Literalidad, order bias Choice, prompt injection, aritmetica y mucho contexto son limites documentados.
- docs: https://docs.typesafe.ai/api ; /models ; /primitives/choice ; /confidence ; /model-jaggedness/jev-1.13 ; /patterns/fan-out .

## Brief del diseno

V1 asesor: evaluaciones trazables visibles, no cambia reglas economicas ni alimenta nuevos candidatos live. Los candidatos del ensayo son analisis sin autoridad. Consumo opcional no rompe motor si TypeSafe falla. Beneficio medible: revision correcta/desacuerdos trazables; sin promesa ahorro.
Se necesita decidir materializacion por decision vs cache por par termino/ficha. Relevancia de grupo: un producto compatible puede hacer busqueda valida, ausencia de una ficha impide afirmar que NINGUNO sirve. No multiplicar probabilidades como certeza conjunta. Origen y destino distintos.
La primera ficha puede ser fixture o documento validado manual con procedencia; no inferir atributos del nombre, ni identidad del parecido. Clarificar relacion fuera_categoria vs contradiccion explicita vs compatible vs insuficiente y calibracion.
Interfaces pequeñas, tipos discriminados missing/stale/failed/evaluated, congelado y version modelo/preguntas/ficha/contexto, ventanas temporales honestas. Retrys limitados solo externa sin repetir side effects, append-only observaciones, permisos separados.
