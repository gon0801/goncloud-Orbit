# PEZ 02: revision de valoracion y medidas (P13)

Estado: PARCIAL, 8/9 descripciones publicadas y comprobadas el 5-nov-2026 UTC.
El usuario confirmo «22mm» para PEZ. Costos y medidas resueltos; solo queda
MLM2727437671, inhabilitada por MeLi desde el 31-oct UTC por categoria incorrecta.
La API rechazo su edicion (HTTP 400); no se recategorizo ni reactivo.
El usuario autorizo «ok hazlo» despues de identificar P13 como revision aparte
de PEZ 01, ya cerrado. No se recalcularon costos ni cambiaron correspondencias.

## Contrato y comprobacion

- Nueve variantes SET verificadas en PEZ 01; sus correspondencias persistentes
  y vigencias canonicas no se cambian por inferencia.
- Rastrear `standard_price` publicado frente a BOM/componentes, operaciones,
  unidades, variantes, compania y fuente del calculo instalado en Odoo.
- Durante la investigacion, consultar Odoo/accounting/MeLi solo lectura. No ejecutar `compute_price`,
  sincronizadores, motores ni metodos de escritura para investigar.
- Conciliar historia disponible sin convertir `write_date` en fecha de cambio
  del costo. Dinero se compara con Decimal y la precision existente.
- Las medidas digitales se separan de una medicion fisica. No reemplazar un
  diametro solo porque aparezca en el nombre de otro producto o componente.
- Una correccion de fuente requiere evidencia exacta, alcance y reversa; si
  falta un dato fisico o una decision de valoracion, dejar la pregunta concreta.
- No tocar bridge/accounting ni precio, inventario, SKU o metas de las plataformas.

| Tarea | Estado | Criterio |
|---|---|---|
| R1. Valoracion | DONE | Causa sustentada con calculo real instalado e historia/fuentes; diferencia y limite explicitos. |
| R2. Medidas | DONE | Matriz por variante identifica significado de cada medida y texto sustentado o dato fisico faltante. |
| R3. Cierre y correccion | PARCIAL 8/9; bloqueo externo | Revision independiente, gitleaks, evidencia guardada y ROADMAP actualizado; solo se aplica una correccion demostrada dentro del alcance autorizado. |

Evidencia: `docs/evidencia/quick-wins-2026-11-04/pez-p13/`.
La investigacion se reparte con GPT Sol: Odoo/valoracion y medidas por separado;
el host concilia historia accounting y mantiene el registro.

## Resultado de la investigacion, previo a la confirmacion del usuario

La diferencia monetaria concilia exactamente 9/9 con la bajada del costo de
cofres por recepcion AVCO del 24-ago; los SET phantom retienen su valor del
18-ago y no tienen capas de valoracion propias encontradas. El BOM actual
con precios historicos reproduce el importe, sin demostrar receta ni autor
original. No hay error de ingesta probado ni recosteo productivo justificado.

Medidas: nueve descripciones compartidas afectan 54 variantes; 51 recetas
homologas usan familia 22 y tres de Italia familia 19. PEZ figura como 22
nominal, sin diametro fisico acreditado. Borradores minimos guardados; no se
publicaron. Pregunta al usuario sobre el diametro real pendiente de respuesta.

Revision independiente GPT Sol aprobada; conciliacion y borradores comprobados.
[Informe y evidencia](../docs/evidencia/quick-wins-2026-11-04/pez-p13/README.md).

## Publicacion autorizada tras confirmar 22 mm

El usuario respondio «22mm» a la pregunta sobre el diametro real de las arras
PEZ. Queda resuelto el dato que condicionaba el borrador; las instrucciones
anteriores «ok hazlo» autorizan completar esta correccion. La confirmacion
se conserva en la evidencia de publicacion, sin atribuirla a una medicion
realizada por el agente.

Alcance exacto: nueve `PUT /items/{id}/description`, cuerpo exclusivamente
`plain_text`. Se retiran los tres fragmentos de diametro universal de cada
item y se agrega una nota para Sello cristiano (PEZ) de 22 mm en su lugar.
El resto del texto se conserva literalmente. La descripcion es compartida
por 54 variantes, pero no se asignan medidas a los otros modelos.

Antes de escribir se comprueban vendedor del pase, ID, SKU y estado de cada
variante, y el texto original. Respaldo durable privado en el servidor antes del
primer PUT. Reversa implementada y comprobada antes de aplicacion; no pisa
una descripcion editada por un tercero. Sin reintentos ciegos ante respuesta
incierta: se consulta el texto vivo. No se renueva el token del bridge ni
se cambia precio, estado, inventario, SKU, Odoo o costos.

Pruebas de la herramienta y revision independiente antes de publicar;
lectura posterior de MeLi para comprobar los nueve textos y datos ajenos.
El cierre de plan y evidencia se integra con CI verde.

## Resultado de publicacion

Ocho descripciones corregidas y leidas directamente de MeLi; nota de 22 mm
exclusiva de PEZ. Precios, existencias, SKU, estados y los 54 registros de
variantes permanecen iguales. MeLi renovo fechas de actualizacion/expiracion
y normalizo doce TAB a espacios en una descripcion; no hubo otro cambio.

MLM2727437671 / NH-GAM-AZU-PEZ-PLA conserva descripcion original y
`under_review/forbidden`. Moderacion `DOMAIN_WRONG_CATEG_V2` del 31-oct UTC,
sin `REMEDY`: «Estaba en una categoría incorrecta». P13 queda abierto solo
por esa baja. No forzar el estado esperado del lote para reintentar; cualquier
recategorizacion o publicacion nueva requiere alcance y fuente separados.

Herramienta y reversa comprobadas: 24 pruebas focalizadas, revision GPT Sol
aprobada, respaldo original inmutable. La reversa restaura contenido; MeLi
puede normalizar TAB y renovar fechas. [Ejecucion, evidencia y reversa](../docs/evidencia/quick-wins-2026-11-04/pez-p13/publicacion/README.md).
