"""Senal de ventas por producto (REPRICING 01 A.2, S4 #5).

`u15` = unidades de `[hoy-15, hoy-1]`; `u60` = las de `[hoy-75, hoy-16]`,
inclusive en los dos extremos. Los rangos de `precio_fechas_excluidas`
no cuentan: ni suman unidades ni cuentan dias, y el promedio se escala
por dias contados. `perdiendo = u15 < (u60 / n60 * n15) * (1 - caida)`
con `<` estricto, solo con racha completa (`senal_dias` corridas).

Orden de cortocircuito (S4 no fija orden entre las guardas; este es el
sellado): cobertura del ledger -> historia -> ventana 60 no vacia ->
`u60_min` -> `n15` -> inventario/listing por dia -> caida -> racha. La
primera guarda que falla decide el `sin_dato`; `u15/u60/n15/n60` se
guardan igual.

Solo los dias CONTADOS pasan las guardas de inventario y listing: un dia
excluido es como si no existiera para la senal.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.precio.config import ConfigPrecio
from app.precio.tipos import SenalVentas, VentasInsumos

__all__ = ["evaluar_senal"]


def _en_rango(dia: date, rangos: tuple[tuple[date, date], ...]) -> bool:
    return any(desde <= dia <= hasta for desde, hasta in rangos)


def evaluar_senal(
    insumos: VentasInsumos,
    *,
    hoy: date,
    config: ConfigPrecio,
    racha_previa: int,
) -> SenalVentas:
    """Senal `perdiendo_ventas` de S4 #5, pura sobre filas ya leidas."""
    if not isinstance(racha_previa, int) or isinstance(racha_previa, bool) or racha_previa < 0:
        raise ValueError(f"racha_previa invalida: {racha_previa!r}")

    dias15 = [hoy - timedelta(days=d) for d in range(1, 16)]
    dias60 = [hoy - timedelta(days=d) for d in range(16, 76)]

    cubierto = insumos.dia_cubierto_hasta
    if cubierto is None or (hoy - cubierto).days > 3:
        return SenalVentas("sin_dato", "ledger_hueco", 0, 0, 0, 0, 0)
    contados15 = [d for d in dias15 if d <= cubierto and not _en_rango(d, config.fechas_excluidas)]
    contados60 = [d for d in dias60 if d <= cubierto and not _en_rango(d, config.fechas_excluidas)]

    por_dia: dict[date, int] = {}
    for dia, qty in insumos.ventas:
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
            raise ValueError(f"venta invalida en {dia}: {qty!r}")
        por_dia[dia] = por_dia.get(dia, 0) + qty
    u15 = sum(por_dia.get(d, 0) for d in contados15)
    u60 = sum(por_dia.get(d, 0) for d in contados60)
    n15 = len(contados15)
    n60 = len(contados60)

    def sin_dato(submotivo: str, racha: int = 0) -> SenalVentas:
        return SenalVentas("sin_dato", submotivo, u15, u60, n15, n60, racha)

    if insumos.primera_venta is None or (hoy - insumos.primera_venta).days < 75:
        return sin_dato("historia_corta")
    if n60 == 0:
        return sin_dato("ventana_60_excluida")
    if u60 < config.u60_min:
        return sin_dato("u60_bajo_minimo")
    if n15 < 10:
        return sin_dato("n15_insuficiente")

    inventario = dict(insumos.inventario)
    activo = dict(insumos.listing_activo)
    for dia in contados15:
        qty = inventario.get(dia)
        if qty is None:
            return sin_dato("dia_sin_observacion_inventario")
        if qty <= 0:
            return sin_dato("dia_sin_stock")
        if dia not in activo:
            return sin_dato("dia_sin_estado_listing")
        if not activo[dia]:
            return sin_dato("listing_inactivo")
    esperado = Decimal(u60) * Decimal(n15) / Decimal(n60) * (Decimal(1) - config.caida_ventas_pct)
    if not Decimal(u15) < esperado:
        return SenalVentas("no_perdiendo", None, u15, u60, n15, n60, 0)
    racha = racha_previa + 1
    if racha < config.senal_dias:
        return sin_dato("racha_incompleta", racha)
    return SenalVentas("perdiendo", None, u15, u60, n15, n60, racha)
