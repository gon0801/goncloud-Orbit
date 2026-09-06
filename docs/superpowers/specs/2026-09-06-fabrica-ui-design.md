# FABRICA UI 01 — Crear campanas desde Orbit

El dueno pidio crear campanas desde el dashboard: el CLI ya existe, pero
`/campanas` solo permite consultar. Se implementa el flujo previamente aprobado:
seleccionar productos, configurar las cinco campanas del grupo, revisar y confirmar.
La sonda real de Amazon sigue diferida por instruccion explicita del dueno.

## Alcance y decisiones

- Boton **Crear campañas** en `/campanas`, pantalla `/campanas/nuevas`.
- Amazon MX/US; cinco roles existentes, sin duplicar reglas del motor.
- Productos reales, margen y causa de exclusion visibles. Sin inventar margenes,
  presupuestos, bids ni semillas. Productos con multiples listings no elegibles en F1.
  **Enmienda ORBIT 19 / 0.2 (2026-09-06):** F1 vigente hasta A.4. Contrato nuevo:
  checkbox por listing; multilisting y sin margen son seleccionables; preview
  declara margen 0 / negativo / inferior al objetivo; API v1 `productos` se
  conserva, v2 usa `listing_ids` + `objetivo`. Spec catalogo abierto.
- Presupuestos y bids se capturan como strings decimales, moneda de la plataforma.
- Modo shadow/live explicito: ambos crean campanas ACTIVAS y pueden gastar;
  shadow solo observa los ajustes del optimizador. La pantalla explica la diferencia.
- Previsualizacion sin HTTP Amazon ni escrituras. Muestra nombres, productos,
  semillas, target y procedencia, presupuestos por campana y suma diaria, existentes.
- Crear exige token existente SOLO en header y confirmacion `CREAR 5 CAMPAÑAS`.
  Token solo en memoria, nunca URL/storage. Cualquier cambio invalida el preview.
- Historial y detalle persistentes, con pasos y errores claros; permite reconciliar,
  completar registro y pausar las campanas conocidas del lote con confirmacion.
- Creacion sin reintentos de POST externos: mismo plan produce lote estable
  `web-<huella>`. Lock asesor PostgreSQL por lote serializa todas las operaciones
  web; un lote existente se consulta, nunca se vuelve a crear. No nuevas colas.
- El servidor recalcula y verifica la huella antes de crear. Cambios de datos o fecha
  exigen previsualizar otra vez. Un error o desconexion conserva el lote para consulta.
- Se reutiliza `tools/fabrica_campanas.py` como unico motor IO, importado estaticamente
  por adaptador web. Docker incluye ese archivo. `_mutar` admite `lote=` opcional;
  CLI conserva conducta. No migracion, subprocess, segundo motor ni cambio de reglas.

## Contrato API

Prefijo `/api/fabrica`. Lectura usa ConexionLectura, mutaciones exige_token primero.

`POST /plan`: `{plataforma,tipo_producto,nombre_base,productos:[int],modo,
parametros:{category_exact:{budget:string,bid:string},category_phrase:{...},
category_broad:{...},product_targeting:{...},auto_discovery:{...}}}`.
Valida extras, duplicados, identificadores, limites y decimales finitos.
Devuelve `{huella,lote,plan,campanas:[{rol,nombre,budget,bid}],
presupuesto_diario_total,existentes}`. `plan` es `fp.plan_como_json` sin alterar.

`GET /catalogo?plataforma=amazon_mx`: `{plataforma,moneda,
productos:[{id,sku,margen_neto_pct:string|null,elegible:boolean,motivo:string|null}],
tipos_producto:[string]}`. Incluye todos los productos de la plataforma, orden SKU.

`POST /crear`: `{solicitud:<body /plan>,huella,confirmacion}`. Auth + confirmacion
literal + comprobacion de huella; devuelve el mismo detalle de lote que GET.

`GET /lotes?plataforma=amazon_mx`: `{items:[<detalle sin pasos>]}` ultimos 50.
`GET /lotes/{lote}`: `{lote,plataforma,estado,detalle,created_at,finished_at,
plan,pasos:[{orden,rol,recurso,external_id,estado,readback_estado}]}`.
No expone token ni payloads/acks crudos; errores redactados.

`POST /lotes/{lote}/{accion}`: accion `pausar|reconciliar|registrar`, body
`{confirmacion}`; literales `PAUSAR GRUPO`, `RECONCILIAR GRUPO`, `REGISTRAR GRUPO`.
Reutiliza `_desarmar`, `_reconciliar_cmd`, `_registrar_cmd`; devuelve detalle actualizado.
404 inexistente, 409 lock ocupado/huella desactualizada/precondicion, 422 datos invalidos,
503 configuracion indisponible. Errores de operacion incluyen `detail:{mensaje,lote}`.

## Evidencia previa y aceptacion

SELECT de produccion antes de tests: MX 342 listings/249 productos/141 margenes;
US 176 listings/119 productos/53 margenes; cero SKU seller faltantes; fabrica_lote vacia.
Por tanto no se asume relacion producto-listing 1:1 ni margen para todos.

Pruebas de API con PostgreSQL real y frontera Amazon simulada: preview sin escritura,
token antes de abrir admin, huella obsoleta, doble envio, concurrencia, error con lote
recuperable, pausa/reconciliacion/registro sin recreacion. UI en navegador: entrada
visible, elegir productos, revisar, invalidar preview, confirmar, errores y consulta.
Validacion productiva exclusivamente GET/preview; ninguna sonda ni mutacion Amazon.
Suite completa una vez en CI del PR; tests focalizados locales, Ruff y pre-commit.
