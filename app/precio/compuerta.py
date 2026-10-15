"""Compuerta del motor de precios (REPRICING 02, bosquejo E).

Apagador (`modo_efectivo`), fusible por universo, insumo sistemico y corte
por errores. Puro: sin I/O, sin reloj, sin red.

Desviacion declarada del bosquejo: `evaluar` recibe `ConfigMotor`, no
`ConfigPrecio` (el corte 0.a partio la config y las claves del fusible
viven en el motor; `config.py` es contrato sellado).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.precio.config import ConfigMotor

__all__ = [
    "modo_efectivo",
    "InsumoVigilado",
    "INSUMOS_VIGILADOS",
    "Intencion",
    "Anterior",
    "Deriva",
    "Abierta",
    "Retenida",
    "Veredicto",
    "es_nueva",
    "salto",
    "armar_deriva",
    "evaluar",
    "Cortacircuito",
]

_ORDEN_MODO = {"off": 0, "shadow": 1, "live": 2}


def modo_efectivo(global_: str, universo: str, goal: str) -> str:
    """El menor de los tres en `off < shadow < live`.

    Copia del reticulo de Ads (`app/optimizer/goals.py::modo_efectivo`),
    no importado. Vocabulario cerrado: fuera de el, `ValueError` que
    nombra el lugar.
    """
    for nombre, modo in (("global", global_), ("universo", universo), ("goal", goal)):
        if modo not in _ORDEN_MODO:
            raise ValueError(
                f"{nombre} fuera del vocabulario sellado {{off, shadow, live}}: {modo!r}"
            )
    return min((global_, universo, goal), key=_ORDEN_MODO.__getitem__)


InsumoVigilado = Literal["costo", "envio", "retencion", "fx", "fee_fijo", "fee_variable"]

# Orden canonico: desempata cuando dos insumos disparan (gana el de mas
# saltos; en empate, el primero de esta lista).
INSUMOS_VIGILADOS: tuple[str, ...] = (
    "costo",
    "envio",
    "retencion",
    "fx",
    "fee_fijo",
    "fee_variable",
)


@dataclass(frozen=True)
class Intencion:
    """Una decision `subir`/`bajar` del dia, vista por el fusible.

    `nueva`: la decision anterior de la unidad NO venia en camino (no era
    subir/bajar ni `mantener(cooldown)` bajo el mismo goal). Un segundo escalon
    hacia el mismo goal no es intencion nueva: por eso las olas de 7 dias no
    vuelven a saltar (G8).
    `liberada`: su retencion pertenece a una corrida que el dueno ya solto.
    """

    decision_id: int
    listing_id: int
    canal: str
    nueva: bool
    liberada: bool


@dataclass(frozen=True)
class Anterior:
    """Lo unico que el fusible mira de la decision previa de la unidad."""

    resultado: str
    motivo: str | None
    goal_id: int | None


@dataclass(frozen=True)
class Deriva:
    """Cuantas unidades vieron saltar UN insumo desde su decision anterior (G4)."""

    canal: str
    insumo: str
    comparables: int
    con_salto: int


@dataclass(frozen=True)
class Abierta:
    medidas: int
    nuevas: int


@dataclass(frozen=True)
class Retenida:
    """Hoy no se aplica nada. `detalle` va al aviso, sin costo ni margen."""

    causa: Literal["movimiento_masivo", "insumo_sistemico"]
    medidas: int
    nuevas: int
    insumo: str | None
    detalle: str


Veredicto = Abierta | Retenida


def es_nueva(anterior: Anterior | None, *, goal_id: int | None) -> bool:
    """Si la intencion de hoy cuenta como nueva para el fusible.

    Sin decision previa, o con goal desconocido en cualquier lado, es
    nueva. Solo deja de serlo cuando la anterior venia en camino hacia el
    MISMO goal (`goal_id` igual): `subir`/`bajar`, o `mantener` con motivo
    `cooldown` (la ola de 7 dias entre escalones).
    """
    if anterior is None or goal_id is None or anterior.goal_id is None:
        return True
    if anterior.goal_id != goal_id:
        return True
    if anterior.resultado in ("subir", "bajar"):
        return False
    return not (anterior.resultado == "mantener" and anterior.motivo == "cooldown")


def salto(antes: Decimal | None, ahora: Decimal | None, *, pct: Decimal) -> bool | None:
    """Si el insumo salto entre la decision previa y la de hoy.

    `None` = no comparable (falta un lado: regla 3, nunca inventado). El
    umbral es ESTRICTO (`> pct`, no `>=`): igual al umbral no salta. Quien
    llama ya emparejo monedas (distinta moneda = no comparable).
    """
    if antes is None or ahora is None:
        return None
    if antes == 0:
        return ahora != 0
    return abs(ahora - antes) / abs(antes) > pct


def armar_deriva(
    canal: str,
    insumo: str,
    pares: Sequence[tuple[Decimal | None, Decimal | None, bool]],
    *,
    pct: Decimal,
) -> Deriva:
    """Una `Deriva` desde los pares `(antes, ahora, liberada)` del universo.

    Las liberadas no cuentan en ninguno de los dos contadores (su corrida
    ya fue suelta: volverian a disparar lo soltado). Sin comparables, la
    deriva no dispara (`0 > frac * 0` es falso).
    """
    comparables = 0
    con_salto = 0
    for antes, ahora, liberada in pares:
        if liberada:
            continue
        veredicto = salto(antes, ahora, pct=pct)
        if veredicto is None:
            continue
        comparables += 1
        con_salto += veredicto
    return Deriva(canal=canal, insumo=insumo, comparables=comparables, con_salto=con_salto)


def _dispara(con_salto: int, base: int, *, frac: Decimal, minimo: int) -> bool:
    """Mayor ESTRICTO sobre la fraccion MAS minimo de movimientos."""
    return con_salto > frac * base and con_salto >= minimo


def evaluar(
    intenciones: Sequence[Intencion],
    *,
    medidas: int,
    derivas: Sequence[Deriva],
    config: ConfigMotor,
) -> Veredicto:
    """El fusible de D1 y el chequeo de insumo sistemico, para UN universo.

    `medidas` = unidades del universo con decision de hoy que SI tiene cuenta
    (`m_actual`), shadow y live juntas (la sombra es fiel). Un `no_evaluado`
    es "no se sabe", no "no quiere moverse": no diluye el denominador
    (quien llama ya lo excluyo).
    `movimiento_masivo` si las nuevas no liberadas superan
    `fusible_frac * medidas` (mayor ESTRICTO) con el minimo de movimientos.
    `insumo_sistemico` si alguna deriva dispara igual; gana al movimiento
    (dice QUE insumo) y entre insumos gana el de mas saltos. Las liberadas
    no cuentan en ninguno de los dos.
    """
    nuevas = sum(1 for i in intenciones if i.nueva and not i.liberada)
    candidatas = [
        d
        for d in derivas
        if _dispara(
            d.con_salto,
            d.comparables,
            frac=config.insumo_salto_frac,
            minimo=config.fusible_min_movimientos,
        )
    ]
    if candidatas:
        ganadora = max(
            candidatas,
            key=lambda d: (d.con_salto, -INSUMOS_VIGILADOS.index(d.insumo)),
        )
        return Retenida(
            causa="insumo_sistemico",
            medidas=medidas,
            nuevas=nuevas,
            insumo=ganadora.insumo,
            detalle=(
                f"{ganadora.insumo} salto en {ganadora.con_salto} de"
                f" {ganadora.comparables} comparables"
                f" (salto {config.insumo_salto_pct}, frac {config.insumo_salto_frac})"
            ),
        )
    if _dispara(nuevas, medidas, frac=config.fusible_frac, minimo=config.fusible_min_movimientos):
        return Retenida(
            causa="movimiento_masivo",
            medidas=medidas,
            nuevas=nuevas,
            insumo=None,
            detalle=(
                f"{nuevas} intenciones nuevas sobre {medidas} medidas"
                f" (fusible {config.fusible_frac}, minimo"
                f" {config.fusible_min_movimientos})"
            ),
        )
    return Abierta(medidas=medidas, nuevas=nuevas)


_ESTADOS_CORTE_EXITO = ("enviado", "confirmado")


@dataclass(frozen=True)
class Cortacircuito:
    """Corte por errores de escritura seguidos dentro de una corrida (G5).

    Valor inmutable: `tras(resultado)` devuelve el siguiente. `abierto` tras
    `limite` errores seguidos; un exito (`enviado`/`confirmado`) lo reinicia.
    `saltado` no mueve el contador: ni prueba que la plataforma falle ni que
    se recupero (decision S.3, fijada en prueba).
    """

    seguidos: int
    limite: int

    def __post_init__(self) -> None:
        if self.seguidos < 0:
            raise ValueError(f"cortacircuito con seguidos negativo: {self.seguidos}")
        if self.limite < 1:
            raise ValueError(f"cortacircuito con limite menor a 1: {self.limite}")

    @property
    def abierto(self) -> bool:
        return self.seguidos >= self.limite

    def tras(self, estado: Literal["enviado", "confirmado", "error", "saltado"]) -> Cortacircuito:
        if estado in _ESTADOS_CORTE_EXITO:
            return Cortacircuito(seguidos=0, limite=self.limite)
        if estado == "error":
            return Cortacircuito(seguidos=self.seguidos + 1, limite=self.limite)
        if estado == "saltado":
            return self
        raise ValueError(f"estado fuera de vocabulario: {estado!r}")
