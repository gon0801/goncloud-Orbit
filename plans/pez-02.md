# PEZ 02: revision de valoracion y medidas (P13)

Estado: REVISION DOCUMENTADA. Costos resueltos; pendiente confirmar diametro fisico de PEZ antes de publicar cambios. El usuario autorizo «ok hazlo» despues de identificar P13
como revision aparte de PEZ 01, ya cerrado. Alcance: explicar diferencias
de costo/medidas con fuentes reales, y dejar la correccion demostrada y
reviewable. No confundir revision con recalculo financiero autorizado.

## Contrato y comprobacion

- Nueve variantes SET verificadas en PEZ 01; sus correspondencias persistentes
  y vigencias canonicas no se cambian por inferencia.
- Rastrear `standard_price` publicado frente a BOM/componentes, operaciones,
  unidades, variantes, compania y fuente del calculo instalado en Odoo.
- Consultar Odoo/accounting/MeLi solo lectura. No ejecutar `compute_price`,
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
| R2. Medidas | DATO PENDIENTE | Matriz por variante identifica significado de cada medida y texto sustentado o dato fisico faltante. |
| R3. Cierre y correccion | DOCUMENTADO | Revision independiente, gitleaks, evidencia guardada y ROADMAP actualizado; solo se aplica una correccion demostrada dentro del alcance autorizado. |

Evidencia: `docs/evidencia/quick-wins-2026-11-04/pez-p13/`.
La investigacion se reparte con GPT Sol: Odoo/valoracion y medidas por separado;
el host concilia historia accounting y mantiene el registro.

## Resultado del 5-nov-2026 UTC

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
