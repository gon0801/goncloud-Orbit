# Modelo de estados del bloque C (ARREGLOS 05): reposicion durable por fila

Base medida: `d0ed286e6048d38db45abe49006a20f25d2b3cdf` (master con BASE, A y B).
Contrato: seccion C de [DISENO.md](../DISENO.md). Tabla unica de transiciones:
[`app/apply_estados.py`](../../../../app/apply_estados.py), que no cambia de
estados ni de eventos. Este bloque agrega dos adaptadores de hechos en ese mismo
modulo; ninguna rama decide fuera de la tabla.

## Lo que se agrega y lo que no

No se agregan estados: los 11 del bloque B cubren la reposicion. Se agrega un
evento, `RECHAZO_IDENTIDAD_DEFINITIVO`, y sus 11 casillas (la tabla pasa de 165
a 176). Se agregan en `app/apply_estados.py`:

- `RESULTADO_REPOSICION_RECHAZO = "fallo:rechazo"`: un 4xx determinista del
  POST de creacion o del DELETE de reversa. No cierra la operacion del archivo:
  el archivo sigue en `applied` (o en `repuesto` para la reversa) y admite otro
  intento registrado.
- `RESULTADO_REPOSICION_IDENTIDAD_OCUPADA = "fallo:identidad_ocupada"`: la
  lectura completa previa mostro viva una identidad que no es la del intento;
  no se envia y no se adopta (estado `ajeno`).
- `resultado_terminal_reposicion(resultado)`: traduce el ultimo resultado
  sellado a lo que `estado_inicial_desde_hechos` considera terminal. Un
  `fallo:rechazo` no es terminal; `ok`, `fallo:entidad_*` y
  `RESULTADO_IDENTIDAD_SUSTITUIDA` si lo son.
- `evento_desde_ack_identidad(ack)`: si el ACK durable de un intento abierto
  trae 401/403, devuelve `RECHAZO_IDENTIDAD_DEFINITIVO`; un 5xx o una respuesta
  perdida no lo traen y siguen inciertos.
- `evento_reposicion_desde_lectura(...)`: aplica el resolver unico
  `evento_desde_lectura_objeto` y agrega dos hechos de la reposicion:
  1. Si el ID propio del ACK no aparece en una lectura COMPLETA, el evento es
     `identidad_dudosa`, nunca `no_aplicado_en_amazon`: la omision no autoriza
     otro POST.
  2. Si la lectura confirma el ID propio, la fila debe conservar campana y bid
     del payload exacto; si no cuadra, `identidad_dudosa`.

## Hechos y estado inicial

El estado de una operacion (`reponer` o `reversa`) sobre una fila de
`keyword_archivo_manual` se deriva con `estado_inicial_desde_hechos`:

- `en_vuelo_ajeno=False`: la concurrencia se resuelve antes, con el indice
  unico parcial de intentos abiertos y el lease por fila. Si el lease se pierde
  al renovar, se consulta `(INCIERTO, DUENO_DESPLAZADO)`.
- `tiene_intento_abierto`: existe una fila sin `finished_at` para
  `(archivo_id, tipo)`.
- `resultado_terminal`: `resultado_terminal_reposicion(ultimo resultado
  sellado)`.
- `intentos_totales`: filas del tipo en el ledger; el tope
  `TOPE_INTENTOS_CAMBIO=3` se reutiliza para no reintentar un rechazo
  indefinidamente.

## Caminos nuevos y su casilla

Cada camino termina en una accion de la tabla; el llamador ramifica solo por
`transicion.accion`, nunca por `transicion.estado_siguiente`.

| # | Camino | Estado de partida | Evento | Casilla -> accion |
|---|---|---|---|---|
| 1 | Claim por fila, intencion confirmada y visible desde otra conexion; POST; ACK con ID; readback por ID; sello de intento y de archivo en la misma transaccion | `PENDIENTE` | `NUEVO_HORARIO` | `(PENDIENTE, NUEVO_HORARIO) -> ENVIAR` deja el intento en `INCIERTO`; la lectura de vuelta pide `(INCIERTO, OK_CONFIRMADO) -> SELLAR_APLICADO` |
| 2a | Caida entre intencion y POST | `INCIERTO` | `NUEVO_HORARIO` | `(INCIERTO, NUEVO_HORARIO) -> LEER_RESULTADO`: solo lectura; sin ACK y sin objeto vivo, `(INCIERTO, NO_APLICADO_EN_AMAZON) -> CONTAR_INTENTO` |
| 2b | Caida entre POST y ACK | `INCIERTO` | `NUEVO_HORARIO` | Igual que 2a. Con un vivo de la identidad y sin ID propio, `identidad_dudosa`: nunca se adopta |
| 2c | Caida entre ACK y readback | `INCIERTO` | `NUEVO_HORARIO` | `LEER_RESULTADO`; el ACK y el ID ya son durables y la lectura va por ese ID |
| 2d | Caida entre readback y sello | `INCIERTO` | `NUEVO_HORARIO` | `LEER_RESULTADO`; la lectura repite y el sello es `NULL -> valor` una sola vez |
| 3a | Respuesta ok con ID | `INCIERTO` | `OK_CONFIRMADO` | `SELLAR_APLICADO` (intento `ok` y archivo `repuesto`, misma transaccion) |
| 3b | 4xx determinista | `INCIERTO` | `ERROR_4XX_DETERMINISTA` | `SELLAR_RECHAZADO` (`fallo:rechazo`); la siguiente corrida deriva `PENDIENTE` y permite otro intento registrado |
| 3c | 5xx, red o timeout sin saber si aplico | `INCIERTO` | `ERROR_5XX_O_RED` | `MANTENER_INCIERTO`; el intento queda abierto y la corrida siguiente lee antes de reenviar |
| 3d | 401 o 403 en lectura o mutacion | `INCIERTO` | `RECHAZO_IDENTIDAD` | `ABORTAR_FASE` con `propaga_aborto=True`; el ACK 401/403 queda durable y ninguna ruta sigue enviando |
| 3e | 401/403 ya guardado en el ACK de un intento abierto (credenciales corregidas, lease vencido) | `INCIERTO` | `RECHAZO_IDENTIDAD_DEFINITIVO` | `SELLAR_RECHAZADO -> RECHAZADO` con `sella_intento=True`: se sella el intento y se permite otro intento registrado; un 5xx sigue incierto |
| 4a | Readback por ID que omite el keyword | `INCIERTO` | `IDENTIDAD_DUDOSA` | `MANTENER_INCIERTO`; cero mutaciones a ciegas y cero POST nuevo |
| 4b | Lista parcial o truncada (lector estricto 04 B-C) | `INCIERTO` | `ERROR_5XX_O_RED` | `MANTENER_INCIERTO`; la lectura incompleta no prueba ausencia |
| 4c | ID sustituido por un ajeno con la misma identidad | `INCIERTO` | `IDENTIDAD_SUSTITUIDA` | `SELLAR_TERMINAL -> AJENO`; no se adopta ni se toca |
| 4d | Identidad ya viva antes de enviar (preexistente) | `PENDIENTE` | `NUEVO_HORARIO` y lectura previa | No se envia: se sella `fallo:identidad_ocupada`, que deriva `AJENO` en la siguiente corrida |
| 5 | Dos CLI a la vez sobre la misma fila | `INCIERTO` con lease vivo | — (indice unico parcial) | El segundo no inserta intento y no envia; si el lease vencio, toma el intento y solo concilia por lectura |
| 6 | Repetir `--reponer` con la intencion ya confirmada | `APLICADO` | `NUEVO_HORARIO` | `(APLICADO, NUEVO_HORARIO) -> NO_TOcar`; el archivo ya esta `repuesto` y el plan solo lee |
| 7a | Reversa propia confirmada | `PENDIENTE` (tipo `reversa`) | `NUEVO_HORARIO` | `ENVIAR`; el DELETE se confirma por readback del ID propio y `(INCIERTO, OK_CONFIRMADO) -> SELLAR_APLICADO` |
| 7b | Reversa ya hecha | `APLICADO` | `NUEVO_HORARIO` | `NO_TOcar`; cero HTTP |
| 7c | Caida en la reversa | `INCIERTO` | `NUEVO_HORARIO` | `LEER_RESULTADO`; si el ID sigue vivo NO se repite el envio, se mantiene incierto |
| 7d | Rechazo definitivo de la reversa | `INCIERTO` | `ERROR_4XX_DETERMINISTA` | `SELLAR_RECHAZADO`; otro intento registrado es posible |

## Invariantes que el bloque demuestra

1. La intencion (fila `keyword_reposicion_intento`) existe y es visible desde
   otra conexion antes de cualquier POST.
2. El ACK y su ID se guardan antes del readback; el sello es una sola vez
   (`NULL -> valor`) y el archivo se sella en la misma transaccion.
3. Un readback que omite el ID, una lista parcial o truncada, o un ID
   sustituido nunca habilitan otro POST. Solo una lectura completa que confirma
   el efecto cierra el intento.
4. Un rechazo definitivo no borra historia: se sella y admite otro intento
   registrado, con tope de 3.
5. Dos CLI concurrentes producen como maximo un POST incierto y un objeto
   PAUSED; el segundo solo concilia.
6. La reversa archiva solo el ID repuesto propio y no recrea ni devuelve
   identidad o historia a la keyword archivada original.
7. Ninguna fila de `keyword_reposicion_intento` se borra; el down conserva el
   historial.
8. Un 401/403 registrado en el ACK se sella como rechazo y permite reintentar;
   un 5xx o una respuesta perdida no habilitan otro envio sin lectura.
