"""Senal de ventas por unidad (REPRICING 02 S.7).

Dos evidencias en orden; la primera que tiene dato decide:

1. `unidades`: la regla vigente u15 (`[hoy-15, hoy-1]`) contra u60
   (`[hoy-75, hoy-16]`) sobre ventas de la UNIDAD, con
   `DiaUnidad.disponibilidad` en lugar de inventario FBA. Los rangos de
   `precio_fechas_excluidas` no cuentan (ni suman ni cuentan dias).
   `perdiendo = u15 < (u60 / n60 * n15) * (1 - caida)` con `<` estricto,
   solo con racha completa (`senal_dias` corridas).
2. `trafico`: CTR de los dias posteriores a `ultima_subida` contra los
   28 previos. Exige `trafico_min` exposiciones en cada lado y una caida
   mayor a `caida_ventas_pct` que pase una prueba de dos proporciones
   (z > 2). Solo mira DESPUES de una subida propia; sin subida no hay
   antes/despues y la respuesta honesta es no mirar.

Sin dato en las dos, manda el submotivo de unidades; si el trafico
aplico pero quedo corto de exposiciones, `evidencia_insuficiente`.

Orden de cortocircuito: cobertura del ledger -> ventas sin atribuir ->
historia -> evidencia unidades (ventana 60, `u60_min`, `n15`,
disponibilidad por dia contado, caida, racha) -> evidencia trafico. La
primera guarda estructural decide el `sin_dato`; `u15/u60/n15/n60` se
guardan igual (salvo `ledger_hueco` y `venta_sin_publicacion`, que
salen en ceros porque el dato no es confiable).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from app.precio.config import ConfigPrecio
from app.precio.puerto import HistoriaUnidad
from app.precio.tipos import SenalVentas

__all__ = ["evaluar_senal", "evaluar_cohorte", "LecturaCohorte"]


@dataclass(frozen=True)
class LecturaCohorte:
    """Subidos contra control, en conjunto. AVISO, nunca accion."""

    subidos: int
    control: int
    caida_relativa: Decimal | None  # None = conjunto demasiado chico
    alarma: bool


def _tasa_grupo(historias: Sequence[HistoriaUnidad], hoy: date) -> tuple[int, int]:
    """`(u14, u60)` del grupo: 14 dias contra los 60 previos."""
    u14 = u60 = 0
    for h in historias:
        for d in h.dias:
            hace = (hoy - d.dia).days
            if 1 <= hace <= 14:
                u14 += d.unidades
            elif 15 <= hace <= 74:
                u60 += d.unidades
    return (u14, u60)


def evaluar_cohorte(
    subidos: Sequence[HistoriaUnidad],
    control: Sequence[HistoriaUnidad],
    *,
    hoy: date,
    min_unidades: int,
    caida_pct: Decimal,
) -> LecturaCohorte:
    """Tasa 14d/60d de cada grupo; alarma si subidos cae mas que control.

    `caida_relativa = 1 - tasa_sub/tasa_ctrl`. Si algun grupo no llega a
    `min_unidades` en 60 dias (o el control no tiene tasa), no hay con que
    comparar: `caida_relativa` None y sin alarma.
    """
    u14_sub, u60_sub = _tasa_grupo(subidos, hoy)
    u14_ctrl, u60_ctrl = _tasa_grupo(control, hoy)
    base = LecturaCohorte(len(subidos), len(control), None, False)
    if u60_sub < min_unidades or u60_ctrl < min_unidades:
        return base
    tasa_sub = (Decimal(u14_sub) / 14) / (Decimal(u60_sub) / 60)
    tasa_ctrl = (Decimal(u14_ctrl) / 14) / (Decimal(u60_ctrl) / 60)
    if tasa_ctrl <= 0:
        return base
    caida = 1 - tasa_sub / tasa_ctrl
    return LecturaCohorte(len(subidos), len(control), caida, caida > caida_pct)


def _en_rango(dia: date, rangos: tuple[tuple[date, date], ...]) -> bool:
    return any(desde <= dia <= hasta for desde, hasta in rangos)


def _trafico(
    historia: HistoriaUnidad, *, subida: date, hoy: date, minimo: int
) -> tuple[int, int, int, int] | None:
    """`(clics_pre, exp_pre, clics_post, exp_post)` o None si no alcanza.

    Pre = `[subida-28, subida-1]`; post = `(subida, hoy-1]` (el dia en
    curso se descarta). Solo cuentan los dias con exposiciones Y clics
    (un `None` no es cero: el dia se salta, no se inventa).
    """
    x_pre = n_pre = x_post = n_post = 0
    for d in historia.dias:
        if d.exposiciones is None or d.clics is None:
            continue
        for campo, valor in (("exposiciones", d.exposiciones), ("clics", d.clics)):
            if not isinstance(valor, int) or isinstance(valor, bool) or valor < 0:
                raise ValueError(f"trafico invalido en {d.dia}: {campo}={valor!r}")
        if subida - timedelta(days=28) <= d.dia <= subida - timedelta(days=1):
            x_pre += d.clics
            n_pre += d.exposiciones
        elif subida < d.dia <= hoy - timedelta(days=1):
            x_post += d.clics
            n_post += d.exposiciones
    if n_pre < minimo or n_post < minimo:
        return None
    return (x_pre, n_pre, x_post, n_post)


def _caida_significativa(
    x_pre: int, n_pre: int, x_post: int, n_post: int, caida_min: Decimal
) -> bool:
    """Caida relativa > `caida_min` con z > 2 de dos proporciones.

    Exacto con `Fraction` (el candado prohibe floats en el motor): z > 2
    equivale a `(p1-p2)^2 > 4*p*(1-p)*(1/n1+1/n2)` con `p1 > p2`.
    """
    p_pre = Fraction(x_pre, n_pre)
    p_post = Fraction(x_post, n_post)
    if p_pre <= 0 or (p_pre - p_post) / p_pre <= caida_min:
        return False
    p = Fraction(x_pre + x_post, n_pre + n_post)
    num = (p_pre - p_post) ** 2
    den = p * (1 - p) * (Fraction(1, n_pre) + Fraction(1, n_post))
    return den > 0 and num > 4 * den


def evaluar_senal(
    historia: HistoriaUnidad,
    *,
    hoy: date,
    config: ConfigPrecio,
    racha_previa: int,
    ultima_subida: date | None,
    trafico_min: int,
) -> SenalVentas:
    """Senal `perdiendo_ventas`, pura sobre la historia de la unidad."""
    if not isinstance(racha_previa, int) or isinstance(racha_previa, bool) or racha_previa < 0:
        raise ValueError(f"racha_previa invalida: {racha_previa!r}")
    if not isinstance(trafico_min, int) or isinstance(trafico_min, bool) or trafico_min <= 0:
        raise ValueError(f"trafico_min invalido: {trafico_min!r}")

    dias15 = [hoy - timedelta(days=d) for d in range(1, 16)]
    dias60 = [hoy - timedelta(days=d) for d in range(16, 76)]

    cubierto = historia.ledger_cubierto_hasta
    if cubierto is None or (hoy - cubierto).days > 3:
        return SenalVentas("sin_dato", "ledger_hueco", None, 0, 0, 0, 0, 0)
    contados15 = [d for d in dias15 if d <= cubierto and not _en_rango(d, config.fechas_excluidas)]
    contados60 = [d for d in dias60 if not _en_rango(d, config.fechas_excluidas)]

    por_dia: dict[date, int] = {}
    disp: dict[date, str] = {}
    for d in historia.dias:
        if not isinstance(d.unidades, int) or isinstance(d.unidades, bool) or d.unidades < 0:
            raise ValueError(f"venta invalida en {d.dia}: {d.unidades!r}")
        por_dia[d.dia] = por_dia.get(d.dia, 0) + d.unidades
        disp[d.dia] = d.disponibilidad
    u15 = sum(por_dia.get(d, 0) for d in contados15)
    u60 = sum(por_dia.get(d, 0) for d in contados60)
    n15 = len(contados15)
    n60 = len(contados60)

    def sin_dato(submotivo: str, racha: int = 0) -> SenalVentas:
        return SenalVentas("sin_dato", submotivo, None, u15, u60, n15, n60, racha)

    if historia.sin_atribuir > 0:
        return SenalVentas("sin_dato", "venta_sin_publicacion", None, 0, 0, 0, 0, 0)
    if historia.primera_venta is None or (hoy - historia.primera_venta).days < 75:
        return sin_dato("historia_corta")

    fallo: tuple[str, int] | None = None
    if n60 == 0:
        fallo = ("ventana_60_excluida", 0)
    elif u60 < config.u60_min:
        fallo = ("u60_bajo_minimo", 0)
    elif n15 < 10:
        fallo = ("n15_insuficiente", 0)
    else:
        for dia in contados15:
            valor = disp.get(dia)
            if valor != "disponible":
                fallo = (
                    ("dia_no_disponible", 0)
                    if valor == "no_disponible"
                    else ("dia_sin_observacion", 0)
                )
                break
    if fallo is None:
        esperado = (
            Decimal(u60) * Decimal(n15) / Decimal(n60) * (Decimal(1) - config.caida_ventas_pct)
        )
        if not Decimal(u15) < esperado:
            return SenalVentas("no_perdiendo", None, "unidades", u15, u60, n15, n60, 0)
        racha = racha_previa + 1
        if racha >= config.senal_dias:
            return SenalVentas("perdiendo", None, "unidades", u15, u60, n15, n60, racha)
        fallo = ("racha_incompleta", racha)

    if ultima_subida is None:
        return sin_dato(*fallo)
    trafico = _trafico(historia, subida=ultima_subida, hoy=hoy, minimo=trafico_min)
    if trafico is None:
        return sin_dato("evidencia_insuficiente", fallo[1])
    x_pre, n_pre, x_post, n_post = trafico
    if _caida_significativa(x_pre, n_pre, x_post, n_post, config.caida_ventas_pct):
        return SenalVentas("perdiendo", None, "trafico", u15, u60, n15, n60, racha_previa + 1)
    return SenalVentas("no_perdiendo", None, "trafico", u15, u60, n15, n60, 0)
