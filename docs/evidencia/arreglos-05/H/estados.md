# Modelo por camino del bloque H: 150 pujas ordinarias por ciclo

Escrito antes del primer commit de codigo. Base: `c9d31d87fd8ad89fc0492df9bd15a5f68c6bc2f7`
(master con BASE, A, B, C, D, E, F y G integrados y desplegados).

Contrato: `docs/evidencia/arreglos-05/DISENO.md`, seccion «H. Tope de 150 pujas
ordinarias» e «Integracion con TEMPORADA y migraciones». Fila del plan:
`H.1` y `H.2` de `plans/arreglos-05.md`.

## Que es el cupo

Un tope de **150 intentos ordinarios de puja (`kind='bid'`, `tipo='normal'`) por
ciclo de ORIGEN y plataforma**. No es un estado del cambio frente a Amazon: es
una precondicion de envio, igual que el cupo diario (`fuera_de_cap`). La fuente
es el ledger `apply_attempt` (join `decision` por `cycle_id`), nunca un contador
en memoria ni un numero nuevo.

- Cuenta todo intento `normal` durable, incluidos el incierto (sin sello), el
  rechazado y el de un crash antes/despues del HTTP: lo intentado consume.
- No cuentan las reversas (`tipo='reversa'`), ni las hermanas de cosecha, ni
  los cortes/pausas/reactivaciones (no son `kind='bid'`).
- Solo la **entrada** y el **regreso** de TEMPORADA quedan exentos, por su
  marcador CONGELADO en la decision: `inputs.motivo` en
  (`entra_temporada_baja`, `sale_temporada_baja`) **y**
  `inputs.temporada_episodio_id` presente. No es heuristica de estado: un
  `ajusta_temporada_baja` (ajuste a media baja) o un `Regresar` por
  `regreso_por_desplome` (subida ordinaria `Recupera`) cuentan. Si el marcador
  de temporada falta, la puja cuenta como ordinaria (contrato: «si faltan
  clasificadores/versiones de temporada, contar como ordinario o abstenerse»).

## Donde se reserva

En `Aplicador.aplica_bids` (`app/apply.py`), **despues** de las guardas locales
y de la lectura fresca, y **antes** del cupo diario, de la fila del ledger y
del HTTP:

1. `reserva_cupo_ciclo` (`app/apply_cuota.py`) bloquea la fila del ciclo de
   origen con `SELECT ... FOR UPDATE` (espera acotada y, si no la consigue,
   falla cerrado sin enviar) y cuenta los intentos ordinarios del origen.
2. La reserva y la intencion viven en la **misma transaccion**: el conteo y el
   `INSERT` de `apply_attempt` (via `_ledger`) se comprometen en el `commit`
   que `_ejecuta_mutacion` hace justo antes del PUT. Entre el conteo y ese
   `commit` nadie mas puede reservar del mismo origen (cerrojo de la fila del
   origen), y el `commit` hace visible el intento antes de que salga el HTTP.
3. Si el origen ya tiene 150, la puja no se envia: `decision_sin_aplicar` con
   motivo `tope_ciclo` (migracion `0099`) y detalle `{usados, tope}`, sin
   consumir cupo diario y sin abrir otro ciclo. Una puja exenta de TEMPORADA
   no consulta el tope ni toma el cerrojo: pasa al cupo diario como siempre.

El conteo y la intencion no salen del claim: el aplicador solo corre bajo el
claim del ciclo (fase de apply) y el cerrojo del origen solo serializa a dos
ejecutores del mismo origen (dos procesos, o un sucesor tras vencer el lease).

## La tabla de estados

El tope agotado es un hecho del ledger, hermano de `intentos_agotados` (el tope
de 3 intentos por puja). Se modela en la MISMA tabla unica de
`app/apply_estados.py`:

- Evento nuevo: `EventoCambio.CUPO_CICLO_AGOTADO`.
- Estado nuevo terminal: `EstadoCambio.CUPO_CICLO_AGOTADO`.
- Casilla `(pendiente, cupo_ciclo_agotado) -> no_tocar / cupo_ciclo_agotado`:
  la puja no se envia y queda terminada para ese origen.
- Casilla `(incierto, cupo_ciclo_agotado) -> leer_resultado / incierto`
  (`origen_incompleto=True`): un intento ya abierto se lee, nunca se reenvia;
  el tope no decide sobre el.
- `en_vuelo` y los terminales se comportan como con cualquier evento: no
  envian (la fila terminal nueva la cubre `_fila_terminal`).
- Clasificador `transicion_de_reserva_cupo(agotado=...)`: consulta la tabla con
  el hecho del cupo. Con lugar devuelve `(pendiente, nuevo_horario) -> enviar`;
  agotado devuelve la casilla del tope. El llamador ramifica solo por
  `transicion.accion`: `ENVIAR` es la unica accion que deja seguir al cupo
  diario y a la intencion; cualquier otra registra el motivo y corta.

La tabla pasa de 11 estados x 16 eventos (176 casillas) a 12 x 17 (204).

## Caminos (los seis del encargo)

| # | Hecho | Estado que lo lee | Evento | Accion de la tabla | Donde se reserva / desenlace |
|---|---|---|---|---|---|
| 1 | Ciclo con 151 pujas ordinarias elegibles | `pendiente` (150 ya intentadas) | `cupo_ciclo_agotado` | `no_tocar` -> `cupo_ciclo_agotado` | La 151 no toma cerrojo util ni gasta cupo diario: `decision_sin_aplicar(motivo='tope_ciclo')`. Las 150 vivas ya tienen su fila `apply_attempt` antes de cada PUT |
| 2 | 149 confirmadas + 1 ambigua | `pendiente` de la siguiente (conteo=150) | `cupo_ciclo_agotado` | `no_tocar` | El ambiguo (sin sello) ya consumio: el conteo incluye `finished_at IS NULL` y `fallo:readback_ambiguo` |
| 3 | Entrada o regreso de TEMPORADA | `pendiente`, pero exenta por marcador congelado | no consulta el tope; sigue `nuevo_horario` | `enviar` | No reserva contra el tope; el cupo diario y la prioridad de TEMPORADA siguen vigentes (`orden_bajo_cupo`) |
| 4 | Recuperacion ordinaria (`resume_of`) o cambio de ejecutor | `pendiente` del ORIGEN | `cupo_ciclo_agotado` si el origen llego a 150 | `no_tocar` | La reserva cuenta `decision.cycle_id = ciclo_origen`, no el ejecutor: no concede otro cupo |
| 5 | Dos procesos a la vez; caida antes o despues del HTTP | `pendiente` | `cupo_ciclo_agotado` | `no_tocar` | El cerrojo `FOR UPDATE` del origen serializa conteo + intencion; el intento se compromete antes del HTTP, asi que el segundo proceso lo ve. Caida pre-HTTP deja fila abierta que cuenta; post-HTTP deja fila sellada o ambigua que tambien cuenta |
| 6 | Reversas | n/a | n/a | n/a | `tipo='reversa'` no entra al conteo; una reversa no devuelve cupo |

## Integracion con TEMPORADA y cupos

- TEMPORADA conserva el dueno de su entrada, su regreso, sus umbrales y su
  prioridad: H solo usa su marcador para eximir.
- El cupo diario (`ads_apply_cap_<platform>_bid`) sigue siendo un limite
  adicional y se evalua DESPUES del tope (una puja rechazada por el tope no
  gasta cupo diario). `cap 0` o clave ausente siguen fallando cerrado.
- No se reducen cortes ni se excluye ninguna recuperacion ordinaria: los
  cortes no pasan por `aplica_bids`; la recuperacion pasa por el mismo conteo.
- No se abre un ciclo nuevo para eludir el tope: el plan del dia ya impide un
  segundo origen con TX3 comprometida, y un ejecutor de recuperacion no decide.

## Foto de produccion (antes de codificar)

`docs/evidencia/arreglos-05/H/previa.txt` (solo lectura, `lee_prod.sh`):
211 ciclos (179 live+done, 30 shadow+done, 2 off+skipped); 100 ciclos con
intentos `bid` normales; 503 intentos totales, maximo 45 por ciclo, mediana 2,
p90 10, **cero ciclos por encima de 50**; cero ambiguos; cero decisiones de
entrada/regreso de temporada con intento; 11 reversas; motivos en uso
`modo_no_live` (190) y `fuera_de_cap` (3). Sin backfill: el tope no cambia la
historia y ningun ciclo actual se acerca a 150.
