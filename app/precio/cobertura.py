"""Recuadro de cobertura del catalogo (REPRICING 01 A.7, S10).

Puro: recibe filas y produce el recuadro por plataforma, sin I/O, sin
reloj y sin red. La ecuacion que cuadra (S10, decision 14):

    activas = evaluadas + no_evaluadas + sin_goal + fuera_de_alcance

Orden de clasificacion (una publicacion, un bucket): primero lo
estructural (`fuera_de_alcance` con la fase que la habilita), luego lo
temporal (`catalogo_desactualizado`), luego `sin_listing` (activa sin
fila en `listing`: se cuenta, no se esconde), luego `canal_sin_dato`
(en Amazon el canal desconocido no es un default a `sin_goal`: el motor
no puede evaluar sin canal, tenga o no goal), luego `sin_goal`
(trabajo del dueno: aparece listada con precio y canal, no oculta), y
al final el goal con su decision del dia. `max_dias` entra como
argumento (lo lee `fuentes.py` de la config); este modulo no conoce
claves de config.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "RESULTADOS_EVALUADOS",
    "UNIVERSO_APAGADO",
    "FilaPublicacion",
    "DetalleSinGoal",
    "Recuadro",
    "armar_recuadro",
    "cuadra_exact",
    "aviso_puente",
]

RESULTADOS_EVALUADOS = ("subir", "bajar", "mantener", "frenado", "goal_inalcanzable")
UNIVERSO_APAGADO = "universo_apagado"


@dataclass(frozen=True)
class FilaPublicacion:
    """Una publicacion activa de la plataforma, ya cruzada y resuelta.

    `listing_id` es nulo cuando la activa canonica no tiene fila en
    `listing` (F3): cuenta como `no_evaluadas["sin_listing"]`.
    """

    listing_id: int | None
    seller_sku: str
    platform: str
    canal: str | None
    precio: Decimal | None
    moneda: str | None
    dias_sin_reportar: int
    tiene_goal: bool
    resultado_hoy: str | None
    motivo_hoy: str | None


@dataclass(frozen=True)
class DetalleSinGoal:
    """Una publicacion sin goal: listada con precio y canal (S10)."""

    sku: str
    precio: Decimal | None
    moneda: str | None
    canal: str | None
    listing_id: int | None = None


@dataclass(frozen=True)
class Recuadro:
    """Recuadro S10 por plataforma. `no_evaluadas` y `fuera_de_alcance`
    son tuplas de pares `(motivo_o_fase, conteo)`."""

    platform: str
    activas: int
    evaluadas: int
    no_evaluadas: tuple[tuple[str, int], ...]
    sin_goal: tuple[DetalleSinGoal, ...]
    fuera_de_alcance: tuple[tuple[str, int], ...]
    avisos: tuple[str, ...]


def _universo_apagado(
    fila: FilaPublicacion, universos: Mapping[tuple[str, str | None], bool] | None
) -> str | None:
    """`universo_apagado` cuando el modo del universo (plataforma/canal)
    esta apagado. Sin mapa (`None`: config vieja sin `precio_modo_global`)
    nada esta apagado (fail-open al comportamiento de hoy)."""
    if universos is None:
        return None
    if universos.get((fila.platform, fila.canal), False):
        return None
    return UNIVERSO_APAGADO


def _motivo_no_evaluada(fila: FilaPublicacion, max_dias: int) -> str | None:
    if fila.dias_sin_reportar > max_dias:
        return "catalogo_desactualizado"
    if fila.listing_id is None:
        return "sin_listing"
    if fila.canal is None:
        return "canal_sin_dato"
    if not fila.tiene_goal:
        return None
    if fila.resultado_hoy is None:
        return "sin_decision"
    if fila.resultado_hoy == "no_evaluado":
        return fila.motivo_hoy or "motivo_ausente"
    if fila.resultado_hoy not in RESULTADOS_EVALUADOS:
        return f"resultado_desconocido:{fila.resultado_hoy}"
    return None


def armar_recuadro(
    filas: list[FilaPublicacion],
    *,
    platform: str,
    max_dias: int,
    universos: Mapping[tuple[str, str | None], bool] | None = None,
) -> Recuadro:
    """Clasifica las activas de `platform` en el recuadro S10.

    `universos` mapea `(plataforma, canal)` a encendido (`True`); lo arma
    `fuentes` con `config.leer_config_motor`. `None` = config vieja: todo
    encendido (comportamiento de hoy).
    """
    propias = [f for f in filas if f.platform == platform]
    evaluadas = 0
    no_evaluadas: dict[str, int] = {}
    sin_goal: list[DetalleSinGoal] = []
    fuera: dict[str, int] = {}
    for fila in propias:
        apagado = _universo_apagado(fila, universos)
        if apagado is not None:
            fuera[apagado] = fuera.get(apagado, 0) + 1
            continue
        motivo = _motivo_no_evaluada(fila, max_dias)
        if motivo == "catalogo_desactualizado":
            no_evaluadas[motivo] = no_evaluadas.get(motivo, 0) + 1
            continue
        if motivo is None and not fila.tiene_goal:
            sin_goal.append(
                DetalleSinGoal(
                    sku=fila.seller_sku,
                    precio=fila.precio,
                    moneda=fila.moneda,
                    canal=fila.canal,
                    listing_id=fila.listing_id,
                )
            )
            continue
        if motivo is None:
            evaluadas += 1
            continue
        no_evaluadas[motivo] = no_evaluadas.get(motivo, 0) + 1
    avisos: list[str] = []
    if no_evaluadas.get("catalogo_desactualizado"):
        avisos.append(
            f"aviso: {no_evaluadas['catalogo_desactualizado']} publicaciones sin"
            f" reportar hace mas de {max_dias} dias (catalogo_desactualizado)"
        )
    return Recuadro(
        platform=platform,
        activas=len(propias),
        evaluadas=evaluadas,
        no_evaluadas=tuple(no_evaluadas.items()),
        sin_goal=tuple(sin_goal),
        fuera_de_alcance=tuple(fuera.items()),
        avisos=tuple(avisos),
    )


def cuadra_exact(rec: Recuadro) -> bool:
    """La ecuacion S10 cierra al entero, sin tolerancia."""
    return rec.activas == (
        rec.evaluadas
        + sum(n for _, n in rec.no_evaluadas)
        + len(rec.sin_goal)
        + sum(n for _, n in rec.fuera_de_alcance)
    )


def aviso_puente(*, activas: int, puente: int) -> str | None:
    """Contraste con la cuenta del bridge: diferencia mayor al 5 % se avisa."""
    if activas == 0:
        if puente == 0:
            return None
        return f"aviso: puente bridge={puente} vs canonica=0 (sin activas canonicas)"
    diferencia = abs(puente - activas) / activas * 100
    if diferencia > 5:
        return f"aviso: puente bridge={puente} vs canonica={activas} ({diferencia:.1f}% > 5%)"
    return None
