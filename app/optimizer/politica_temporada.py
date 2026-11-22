"""Helpers puros de temporada y recuperacion (R6r) para `app/optimizer/politica.py`.

Modulo hermano extraido en Ola 3C para mantener `politica.py` legible y con
holgura bajo el presupuesto de tamano (900 lineas), sin crear ciclos de import:
`politica.py` importa de aqui y este modulo nunca importa `politica.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from app.optimizer import proteccion as subproteccion
from app.optimizer import temporada
from app.optimizer.caso import CasoHoja, InsumosProteccion
from app.optimizer.recuperacion import presupuesto_topado

MOTIVO_ENTRA_BAJA = "entra_temporada_baja"
MOTIVO_AJUSTA_BAJA = "ajusta_temporada_baja"
MOTIVO_SALE_BAJA = "sale_temporada_baja"
MOTIVOS_TEMPORADA = (MOTIVO_ENTRA_BAJA, MOTIVO_AJUSTA_BAJA, MOTIVO_SALE_BAJA)
# TEMPORADA 01 C: la subida rapida (R6r) y su freno de presupuesto.
MOTIVO_PRESUPUESTO_TOPADO = "presupuesto_topado"
# TEMPORADA 01 D: los retenidos de la baja, el techo de subidas y el regreso
# que espera por campana topada (solo notes; no es veredicto final).
MOTIVO_TEMPORADA_BAJA = "temporada_baja"
MOTIVO_TECHO_DE_BAJA = "techo_de_baja"
MOTIVO_REGRESO_ESPERA_PRESUPUESTO = "regreso_espera_presupuesto"


@dataclass(frozen=True)
class _Recupera:
    """Candidato interno de R6r (TEMPORADA 01 C): sube directo al nivel que la
    evidencia sostiene (ya en centavos, topado por techo_conocido, techo del
    goal y techo_de_baja). No sale de decide: se convierte en Regresar despues
    de P, BUD y sus frenos de salto."""

    destino: Decimal


def caso_para_juicio(caso: CasoHoja) -> CasoHoja:
    """TEMPORADA 01 D (decision 4): deriva la meta y el equilibrio del juicio
    R3-R13 (alta: max(base, meta_alta), libre por encima del equilibrio). Fuera
    de alta es identidad (byte a byte lo de hoy)."""
    return replace(
        caso,
        economia=replace(
            caso.economia,
            target_acos_pct=temporada.meta_del_juicio(caso),
            plataforma=replace(
                caso.economia.plataforma,
                equilibrio_acos_pct=temporada.equilibrio_del_juicio(caso),
            ),
        ),
    )


def recorte_retenido_en_baja(
    caso: CasoHoja,
    factor: Decimal,
    motivo: str,
    *,
    motivos_r3: tuple[str, ...],
) -> bool:
    """TEMPORADA 01 D (decision 7): en baja SOLO R3 recorta (vende y pierde
    dinero); R4, R9 y R11 quedan retenidos."""
    t = caso.temporada
    return (
        t is not None and t.frontera.fase_hoja == "baja" and factor < 0 and motivo not in motivos_r3
    )


def subida_en_espera(proteccion: InsumosProteccion | None, motivo_bloqueado: str | None) -> bool:
    """True si una subida debe esperar: la pausa se suprimio porque hay una
    venta reciente conocida (espera activa), no porque el dato faltara.

    El marcador `frena_subidas` deja intacta la semantica de las decisiones
    historicas, que no lo llevan (regla de replay)."""
    return (
        proteccion is not None
        and proteccion.frena_subidas
        and motivo_bloqueado in (subproteccion.MOTIVO_PROTEGIDA, subproteccion.MOTIVO_INDETERMINADA)
    )


def motivo_freno_subida_normal(
    caso: CasoHoja,
    factor: Decimal,
    motivo_bloqueado: str | None,
    *,
    motivo_espera_venta: str,
) -> str | None:
    """P y BUD de la subida normal de R6 (TEMPORADA 01 C), en el orden sellado:
    espera por venta reciente (ARREGLOS 01) y luego campana topada o sin dato
    de presupuesto (BUD; solo con lectura presente)."""
    if factor <= 0:
        return None
    if subida_en_espera(caso.pausa.proteccion, motivo_bloqueado):
        return motivo_espera_venta
    if caso.presupuesto is not None and presupuesto_topado(caso) in (True, None):
        return MOTIVO_PRESUPUESTO_TOPADO
    return None


def motivo_freno_recupera(
    caso: CasoHoja,
    ultimo,
    efecto,
    *,
    freno_trayectoria_subida: Callable[[], str | None],
    clics_nuevos: int,
    dias_salida: int,
    motivo_dato_faltante: str,
    motivo_esperando_precio: str,
) -> str | None:
    """Frenos del salto R6r (TEMPORADA 01 C). Primer salto tras un recorte:
    solo R14. Segundo salto seguido (ultimo cambio fue subida): pasa por los
    mismos frenos que R6. Ultimo cambio de temporada: sin freno (solo R2)."""
    if ultimo.origen == "temporada":
        return None
    if ultimo.direccion != -1:
        return freno_trayectoria_subida()
    if efecto.clics_post is None:
        return motivo_dato_faltante
    if efecto.clics_post < clics_nuevos and efecto.dias_post < dias_salida:
        return motivo_esperando_precio
    return None


def calcula_paso_a_centavo(
    bid,
    destino: Decimal,
    *,
    min_delta: Decimal,
    clamp_min: Decimal,
    clamp_max: Decimal,
    motivo_dato_faltante: str,
    motivo_delta_bajo: str,
    motivo_rango_bloquea: str,
) -> tuple[Decimal, Decimal] | str:
    """Calcula `(factor, nuevo)` para un destino ya en centavos (entrada o
    ajuste de baja) o devuelve el motivo de `Mantener` si una guarda frena."""
    valor = bid.valor
    if valor is None or valor <= 0:
        return motivo_dato_faltante
    nuevo = min(max(destino, bid.piso), bid.techo)
    if abs(nuevo - valor) < min_delta:
        return motivo_delta_bajo
    delta = nuevo - valor
    magnitud_ok = delta <= clamp_max * valor and (-delta <= -clamp_min * valor)
    if not magnitud_ok:
        return motivo_rango_bloquea
    return (nuevo / valor - 1, nuevo)


def accion_rte_pendiente(
    caso: CasoHoja,
    p: object,
    *,
    min_delta: Decimal,
    clamp_min: Decimal,
    clamp_max: Decimal,
    motivo_dato_faltante: str,
    motivo_delta_bajo: str,
    motivo_rango_bloquea: str,
) -> tuple[str, ...] | None:
    """RTe de temporada (TEMPORADA 01 B/D): entrada de la baja o ajuste de una
    reduccion editada. Devuelve una tupla accion (`mover`/`regresar`/`mantener`)
    o None si el camino normal debe seguir."""
    if isinstance(p, temporada.EntradaPendiente):
        res = calcula_paso_a_centavo(
            caso.bid,
            p.destino,
            min_delta=min_delta,
            clamp_min=clamp_min,
            clamp_max=clamp_max,
            motivo_dato_faltante=motivo_dato_faltante,
            motivo_delta_bajo=motivo_delta_bajo,
            motivo_rango_bloquea=motivo_rango_bloquea,
        )
        if isinstance(res, str):
            return ("mantener", res)
        return ("mover", res[0], res[1], MOTIVO_ENTRA_BAJA)
    if isinstance(p, temporada.AjustePendiente):
        valor = caso.bid.valor
        if valor is not None and p.destino < valor:
            res = calcula_paso_a_centavo(
                caso.bid,
                p.destino,
                min_delta=min_delta,
                clamp_min=clamp_min,
                clamp_max=clamp_max,
                motivo_dato_faltante=motivo_dato_faltante,
                motivo_delta_bajo=motivo_delta_bajo,
                motivo_rango_bloquea=motivo_rango_bloquea,
            )
            if isinstance(res, str):
                return ("mantener", res)
            return ("mover", res[0], res[1], MOTIVO_AJUSTA_BAJA)
        if presupuesto_topado(caso) is True:
            return None
        return ("regresar", p.destino, MOTIVO_AJUSTA_BAJA)
    return None
