"""Plan de goals puro: Pedidos -> Plan (REPRICING 02 G.1, bosquejo G).

Las protecciones (`_referencia`, `_componente`, `_detalle_fee`,
`_guardas_del_plan`, `UMBRAL_SALTO`) viven aqui, en un solo lugar y sin
base: `planear` recibe candidatos ya leidos
(`leer_candidatos` de `goals_write`) y devuelve el plan con su huella.
Quien escribe es `goals_write.aplicar_plan`.

Unidades (dinero con moneda; fracciones sin `%`):
- `goal`, `m_hoy` y `goal_actual` son FRACCIONES (`0.30` = 30 %).
- `m_pct` de la lectura va en POR CIENTO, como `contribucion_pct`.
- El plan muestra lo que se escribe: el goal propuesto va cuantizado a 4
  decimales (la escala de `precio_goal.margen_goal_pct`); `m_hoy` conserva
  su precision de origen.

Bloqueos, primero que pega gana: `sin_cambio` > `sin_escenario` >
`fuera_de_banda` > `salto_mayor_25` > `live_sin_escenario`.
`confirmable` vale exactamente cuando confirmar deja una fila escribible:
el borde de banda de "margen de hoy" si, un goal a mano fuera de banda no
(la base lo rechazaria), y un salto si.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Literal

from app.estimacion_venta import DetalleFee
from app.precio.objetivo import (
    ErrorObjetivo,
    derivar_ref_fijo,
    precio_estrella,
    techo_centavo,
)
from app.precio.puerto import UnidadPrecio
from app.precio.tipos import Importe

__all__ = [
    "UMBRAL_SALTO",
    "MODOS_SIEMBRA",
    "Pedido",
    "PedidoInvalido",
    "GoalVigente",
    "LecturaHoy",
    "Candidato",
    "PedidoMargenDeHoy",
    "PedidoValor",
    "ValorPorUnidad",
    "PedidoValores",
    "PedidoModo",
    "PedidoCierre",
    "Bloqueo",
    "FilaPlan",
    "PlanGoals",
    "planear",
]

# Salto maximo de P* sobre P_actual sin confirmacion.
UMBRAL_SALTO = Decimal("0.25")

MODOS_SIEMBRA = ("shadow", "live")

_CUATRO = Decimal("0.0001")


class PedidoInvalido(ValueError):
    """El pedido o su cobertura no cierra. Trae `(listing_id, platform)`
    cuando el problema es de una fila, para que quien llama reporte la
    linea; sin fila (pedido mal formado) vienen en None."""

    def __init__(
        self, mensaje: str, *, listing_id: int | None = None, platform: str | None = None
    ) -> None:
        super().__init__(mensaje)
        self.listing_id = listing_id
        self.platform = platform


@dataclass(frozen=True)
class GoalVigente:
    """El goal que la unidad tiene hoy (lo leyo `leer_candidatos`)."""

    id: int
    goal: Decimal
    mode: str

    def __post_init__(self) -> None:
        if not isinstance(self.goal, Decimal):
            raise PedidoInvalido(f"goal vigente no es Decimal: {self.goal!r}")
        if self.mode not in MODOS_SIEMBRA:
            raise PedidoInvalido(f"mode invalido: {self.mode!r} (vocabulario: shadow|live)")


@dataclass(frozen=True)
class LecturaHoy:
    """La cuenta de hoy de la unidad, ya leida (sin base).

    Son las tres filas que `_referencia` del tool leia (escenario
    disponible mas reciente + oferta + fee), sin calcular: `None` donde no
    hay dato, JSON crudo donde lo hay. `planear` calcula encima.
    """

    m_pct: Decimal | None
    p_actual: Decimal | None
    moneda: str | None
    componentes: list | None
    fee_total: Decimal | None
    fee_quoted: Decimal | None
    fee_detalles: list | None
    fee_estado: str | None


@dataclass(frozen=True)
class Candidato:
    """Una unidad a la que se le puede poner goal, con su cuenta de hoy."""

    unidad: UnidadPrecio
    seller_sku: str | None
    goal_vigente: GoalVigente | None
    lectura: LecturaHoy | None


@dataclass(frozen=True)
class PedidoMargenDeHoy:
    """D6: goal = margen de hoy de cada unidad."""

    mode: str

    def __post_init__(self) -> None:
        _validar_modo(self.mode)


@dataclass(frozen=True)
class PedidoValor:
    """D7 en bloque: el dueno escribe UN goal para toda la seleccion."""

    goal: Decimal
    mode: str

    def __post_init__(self) -> None:
        _validar_goal(self.goal)
        _validar_modo(self.mode)


@dataclass(frozen=True)
class ValorPorUnidad:
    """Una meta a mano para una unidad (elemento de `PedidoValores`)."""

    listing_id: int
    platform: str
    goal: Decimal

    def __post_init__(self) -> None:
        _validar_goal(self.goal)


@dataclass(frozen=True)
class PedidoValores:
    """D7 por lote: el dueno escribio un goal por unidad (CSV, pantalla).

    El bosquejo trae solo `PedidoValor` singular; el lote del tool trae un
    goal distinto por fila y el go es atomico, asi que el pedido plural
    existe al lado del singular (el singular es "este valor a todas").
    """

    goals: tuple[ValorPorUnidad, ...]
    mode: str

    def __post_init__(self) -> None:
        _validar_modo(self.mode)
        vistos: set[tuple[int, str]] = set()
        for valor in self.goals:
            clave = (valor.listing_id, valor.platform)
            if clave in vistos:
                raise PedidoInvalido(
                    f"goal repetida para (listing_id, platform) = {clave}",
                    listing_id=valor.listing_id,
                    platform=valor.platform,
                )
            vistos.add(clave)


@dataclass(frozen=True)
class PedidoModo:
    """Pasar a `live` (o volver a `shadow`) conservando el goal vigente."""

    mode: str

    def __post_init__(self) -> None:
        _validar_modo(self.mode)


@dataclass(frozen=True)
class PedidoCierre:
    """Cerrar el goal vigente (no siembra nada)."""

    pass


Pedido = PedidoMargenDeHoy | PedidoValor | PedidoValores | PedidoModo | PedidoCierre

Bloqueo = Literal[
    "sin_escenario",  # no hay margen de hoy que copiar; se lista con su motivo
    "fuera_de_banda",  # margen de hoy fuera de la banda: NO se recorta en silencio
    "salto_mayor_25",  # P* se aleja mas de 25 % del precio actual
    "sin_referencia",  # B-C2: live a mano sin P* (el 25 % no puede correr)
    "live_sin_escenario",  # no se enciende lo que el motor no puede evaluar
    "sin_cambio",  # ya tiene ese goal y ese modo
]


def _validar_modo(mode: str) -> None:
    if mode not in MODOS_SIEMBRA:
        raise PedidoInvalido(f"mode invalido: {mode!r} (vocabulario: shadow|live)")


def _validar_goal(goal: Decimal) -> None:
    if not isinstance(goal, Decimal):
        raise PedidoInvalido(f"goal no es Decimal: {goal!r}")
    if not goal.is_finite() or goal <= 0:
        raise PedidoInvalido(f"goal no positivo: {goal!r}")


@dataclass(frozen=True)
class FilaPlan:
    """Lo que la pantalla y el CLI muestran antes de escribir.

    `bloqueo` con `confirmable=True` se destraba confirmando la fila;
    con `False` la fila no se puede escribir. `goal_propuesto` en
    `fuera_de_banda` es el borde de la banda: confirmarlo es decidir mover
    el precio, y por eso pide confirmacion aparte. `motivo` dice por que
    no hay P* cuando `p_estrella` es None.
    """

    listing_id: int
    plataforma: str
    seller_sku: str | None
    accion: Literal["sembrar", "reemplazar", "cerrar", "nada"]
    m_hoy: Decimal | None
    goal_id: int | None
    goal_actual: Decimal | None
    goal_propuesto: Decimal | None
    p_actual: Importe | None
    p_estrella: Importe | None
    envio_origen: str | None
    bloqueo: Bloqueo | None
    confirmable: bool
    motivo: str | None


@dataclass(frozen=True)
class PlanGoals:
    """`huella` = hash de las filas ordenadas. La vista previa la entrega
    y `aplicar_plan` la exige: si algo cambio entre mirar y aceptar, no
    escribe."""

    pedido: Pedido
    filas: tuple[FilaPlan, ...]
    huella: str


def _detalle_fee(nodo: dict) -> DetalleFee:
    # JSON normalizado de `parsear_respuesta_fees` a arbol.
    tasa = nodo.get("tax_amount")
    return DetalleFee(
        fee_type=nodo["fee_type"],
        final_fee=Decimal(str(nodo["final_fee"])),
        tax_amount=None if tasa is None else Decimal(str(tasa)),
        included_fee_details=tuple(
            _detalle_fee(hijo) for hijo in nodo.get("included_fee_details") or []
        ),
    )


def _componente(componentes: list, nombre: str) -> Decimal:
    # Mismo mensaje, `ValueError` en vez de `PrecioGoalInvalido` (este
    # modulo no importa al escritor). La red de `_referencia` lo vuelve
    # `referencia_sin_derivar`, igual que antes.
    for item in componentes:
        if item.get("nombre") == nombre:
            return Decimal(str(item["importe_normalizado"]))
    raise ValueError(f"escenario sin componente {nombre}")


def _m_hoy_de(lectura: LecturaHoy | None) -> Decimal | None:
    if lectura is None or lectura.m_pct is None:
        return None
    return lectura.m_pct / 100


def _referencia(
    lectura: LecturaHoy | None, goal: Decimal | None
) -> tuple[Importe | None, Importe | None, str | None]:
    """`(p_estrella, p_actual, motivo)`; `None` donde no hay dato.

    La `_referencia` del tool sin SQL: mismos motivos, mismo orden. Con
    goal None no hay P* que valuar (fila sin propuesta).
    """
    if lectura is None:
        return (None, None, "sin_escenario")
    if lectura.p_actual is None or lectura.moneda is None:
        return (None, None, "oferta_ausente")
    p_actual = Importe(lectura.p_actual, lectura.moneda)
    if lectura.fee_total is None or lectura.fee_estado != "success":
        return (None, p_actual, "fee_no_utilizable")
    if goal is None:
        return (None, p_actual, None)
    try:
        detalles = tuple(_detalle_fee(nodo) for nodo in lectura.fee_detalles)
        ref, fijo = derivar_ref_fijo(detalles, lectura.fee_total, lectura.fee_quoted)
        costo = _componente(lectura.componentes, "costo_normalizado")
        ingreso = _componente(lectura.componentes, "ingreso_normalizado")
        envio = _componente(lectura.componentes, "logistica")
        isr_importe = _componente(lectura.componentes, "isr")
        precio_bruto = _componente(lectura.componentes, "precio_bruto")
        if ingreso <= 0:
            raise ValueError("escenario con ingreso no positivo")
        tasa_isr = isr_importe / ingreso
        divisor_iva = precio_bruto / ingreso
        estrella = precio_estrella(
            costo, fijo, envio, tasa_isr, goal, divisor_iva, divisor_iva != 1, ref
        )
    except (
        ErrorObjetivo,
        InvalidOperation,
        ArithmeticError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return (None, p_actual, "referencia_sin_derivar")
    return (Importe(techo_centavo(estrella), lectura.moneda), p_actual, None)


def _huella(pedido: Pedido, filas: tuple[FilaPlan, ...]) -> str:
    # La `_huella` del tool: `sembrar` y `cerrar` conservan su formato al
    # byte (el contrato congela la huella vieja). `reemplazar` espeja sus
    # dos escrituras (cerrar el vigente + sembrar el nuevo) y `nada` solo
    # su identidad: cualquier cambio de dato entre mirar y aceptar mueve
    # la huella.
    partes = []
    for fila in filas:
        if fila.accion == "cerrar":
            partes.append(f"cerrar:{fila.listing_id}:{fila.plataforma}:{fila.goal_id}")
        elif fila.accion == "nada":
            partes.append(f"nada:{fila.listing_id}:{fila.plataforma}")
        elif fila.accion == "reemplazar":
            partes.append(f"cerrar:{fila.listing_id}:{fila.plataforma}:{fila.goal_id}")
            partes.append(
                f"siembra:{fila.listing_id}:{fila.plataforma}:{fila.goal_propuesto}:{pedido.mode}"
            )
        else:
            partes.append(
                f"siembra:{fila.listing_id}:{fila.plataforma}:{fila.goal_propuesto}:{pedido.mode}"
            )
    return hashlib.sha256("|".join(sorted(partes)).encode()).hexdigest()[:16]


def _validar_banda(banda: tuple[Decimal, Decimal]) -> tuple[Decimal, Decimal]:
    try:
        minimo, maximo = banda
    except (TypeError, ValueError) as exc:
        raise PedidoInvalido(f"banda no es (min, max): {banda!r}") from exc
    if (
        not isinstance(minimo, Decimal)
        or not isinstance(maximo, Decimal)
        or not (Decimal(0) < minimo < maximo < Decimal(1))
    ):
        raise PedidoInvalido(
            f"banda incoherente: exige 0 < min < max < 1 (min={minimo!r}, max={maximo!r})"
        )
    return (minimo, maximo)


def _metas_por_unidad(
    pedido: PedidoValores, candidatos: tuple[Candidato, ...]
) -> dict[tuple[int, str], Decimal]:
    metas = {(v.listing_id, v.platform): v.goal for v in pedido.goals}
    for cand in candidatos:
        clave = (cand.unidad.listing_id, cand.unidad.plataforma)
        if clave not in metas:
            raise PedidoInvalido(
                f"sin goal pedida para listing {clave[0]} en {clave[1]}",
                listing_id=clave[0],
                platform=clave[1],
            )
    pedidas = {(c.unidad.listing_id, c.unidad.plataforma) for c in candidatos}
    ajenas = sorted(set(metas) - pedidas)
    if ajenas:
        raise PedidoInvalido(f"goals pedidas fuera del plan: {ajenas}")
    return metas


def _cuantizar_goal(goal: Decimal) -> Decimal:
    # El plan muestra lo que se escribe: 4 decimales, la escala de la
    # columna (misma paridad que `fraccion_desde_porcentaje`, explicita).
    return goal.quantize(_CUATRO, rounding=ROUND_HALF_EVEN)


def _borde(minimo: Decimal, maximo: Decimal, m_hoy: Decimal) -> Decimal:
    # El borde propuesto nunca sale de la banda: el piso sube, el techo baja.
    if m_hoy < minimo:
        return minimo.quantize(_CUATRO, rounding=ROUND_CEILING)
    return maximo.quantize(_CUATRO, rounding=ROUND_FLOOR)


def _fila(
    cand: Candidato,
    *,
    pedido: Pedido,
    banda: tuple[Decimal, Decimal],
    metas: dict[tuple[int, str], Decimal] | None,
) -> FilaPlan:
    unidad = cand.unidad
    lid, plat = unidad.listing_id, unidad.plataforma
    vigente = cand.goal_vigente
    goal_id = None if vigente is None else vigente.id
    goal_actual = None if vigente is None else vigente.goal
    if isinstance(pedido, PedidoCierre):
        if vigente is None:
            raise PedidoInvalido(
                f"sin goal vigente para listing {lid} en {plat}: nada que cerrar",
                listing_id=lid,
                platform=plat,
            )
        return FilaPlan(
            listing_id=lid,
            plataforma=plat,
            seller_sku=cand.seller_sku,
            accion="cerrar",
            m_hoy=None,
            goal_id=goal_id,
            goal_actual=goal_actual,
            goal_propuesto=None,
            p_actual=None,
            p_estrella=None,
            envio_origen=None,
            bloqueo=None,
            confirmable=False,
            motivo=None,
        )
    if isinstance(pedido, PedidoModo):
        if vigente is None:
            raise PedidoInvalido(
                f"sin goal vigente para listing {lid} en {plat}: nada que pasar a {pedido.mode}",
                listing_id=lid,
                platform=plat,
            )
        natural: Decimal | None = vigente.goal
    elif isinstance(pedido, PedidoValor):
        natural = pedido.goal
    elif isinstance(pedido, PedidoValores):
        assert metas is not None
        natural = metas[(lid, plat)]
    elif isinstance(pedido, PedidoMargenDeHoy):
        m = _m_hoy_de(cand.lectura)
        natural = None if m is None else _cuantizar_goal(m)
    else:
        raise TypeError(f"pedido desconocido: {type(pedido).__name__}")
    minimo, maximo = banda
    fuera = natural is not None and not minimo <= natural <= maximo
    if fuera and isinstance(pedido, PedidoMargenDeHoy):
        assert natural is not None
        m_hoy = _m_hoy_de(cand.lectura)
        assert m_hoy is not None
        propuesto: Decimal | None = _borde(minimo, maximo, m_hoy)
    else:
        propuesto = None if natural is None else _cuantizar_goal(natural)
    estrella, p_actual, motivo = _referencia(cand.lectura, propuesto)
    m_hoy = _m_hoy_de(cand.lectura)
    if (
        propuesto is not None
        and vigente is not None
        and propuesto == vigente.goal
        and pedido.mode == vigente.mode
    ):
        return FilaPlan(
            listing_id=lid,
            plataforma=plat,
            seller_sku=cand.seller_sku,
            accion="nada",
            m_hoy=m_hoy,
            goal_id=goal_id,
            goal_actual=goal_actual,
            goal_propuesto=propuesto,
            p_actual=p_actual,
            p_estrella=estrella,
            envio_origen=None,
            bloqueo="sin_cambio",
            confirmable=False,
            motivo=motivo,
        )
    bloqueo: Bloqueo | None = None
    confirmable = False
    if isinstance(pedido, PedidoMargenDeHoy) and propuesto is None:
        bloqueo, confirmable = "sin_escenario", False
    elif fuera:
        bloqueo, confirmable = "fuera_de_banda", isinstance(pedido, PedidoMargenDeHoy)
    elif (
        isinstance(pedido, (PedidoValor, PedidoValores, PedidoMargenDeHoy))
        and estrella is not None
        and p_actual is not None
        and p_actual.valor > 0
        and abs(estrella.valor - p_actual.valor) > UMBRAL_SALTO * p_actual.valor
    ):
        bloqueo, confirmable = "salto_mayor_25", True
    elif (
        isinstance(pedido, (PedidoValor, PedidoValores))
        and pedido.mode == "live"
        and estrella is None
    ):
        # B-C2: el goal a mano en live sin P* de referencia (motivo en
        # `motivo`: sin_escenario, oferta_ausente, fee_no_utilizable o
        # referencia_sin_derivar) no salta el chequeo 25 % en silencio:
        # se escribe solo confirmado. En shadow sigue aviso sin bloqueo.
        bloqueo, confirmable = "sin_referencia", True
    elif pedido.mode == "live" and m_hoy is None:
        bloqueo, confirmable = "live_sin_escenario", False
    return FilaPlan(
        listing_id=lid,
        plataforma=plat,
        seller_sku=cand.seller_sku,
        accion="reemplazar" if vigente is not None else "sembrar",
        m_hoy=m_hoy,
        goal_id=goal_id,
        goal_actual=goal_actual,
        goal_propuesto=propuesto,
        p_actual=p_actual,
        p_estrella=estrella,
        envio_origen=None,
        bloqueo=bloqueo,
        confirmable=confirmable,
        motivo=motivo,
    )


def planear(
    candidatos: tuple[Candidato, ...] | list[Candidato],
    *,
    pedido: Pedido,
    banda: tuple[Decimal, Decimal],
) -> PlanGoals:
    """El plan del pedido sobre los candidatos, con su huella.

    Puro: no toca la base. Levanta `PedidoInvalido` cuando el pedido no
    cierra (cobertura incompleta, cierre o modo sin vigente); los
    problemas por fila (banda, salto, escenario) salen como `bloqueo` en
    la fila, nunca como excepcion.
    """
    minimo, maximo = _validar_banda(banda)
    metas = (
        _metas_por_unidad(pedido, tuple(candidatos)) if isinstance(pedido, PedidoValores) else None
    )
    filas = tuple(
        _fila(cand, pedido=pedido, banda=(minimo, maximo), metas=metas) for cand in candidatos
    )
    return PlanGoals(pedido=pedido, filas=filas, huella=_huella(pedido, filas))
