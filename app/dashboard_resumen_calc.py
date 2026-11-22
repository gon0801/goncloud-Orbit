"""Calculos puros del Resumen de Ads multicanal (RESUMEN ADS 02).

Extraido de `app/dashboard_resumen.py` (Ola F1): agregaciones, ratios ACoS/ROAS
en Decimal exacto, serie diaria con MA7, cobertura, cierre por zona horaria,
variacion contra periodo anterior y totales agrupados por moneda. Sin SQL.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.api_common import _dec_str
from app.dia_cerrado import fin_del_dia

DIAS_CALENTAMIENTO = 6
DIAS_MA7 = 7
_CUATRO_DEC = Decimal("0.0001")
_DOS_DEC = Decimal("0.01")
_METRICAS_TOTALES = ("cost", "ad_revenue", "clicks", "orders")


@dataclass(frozen=True)
class Canal:
    """Un canal del Resumen: moneda NATIVA y madurez.

    La zona en la que la fuente cierra su dia de reporte es la de
    `app.dia_cerrado.ZONAS[plataforma]`. El "hoy" del Resumen es la fecha
    local MINIMA entre los canales pedidos, para que D-1 este cerrado en
    todos. `dias_inmaduros=None` significa "la fuente no publica madurez": el
    contrato lo declara y jamas se rellena con la ventana de Amazon.
    """

    plataforma: str
    moneda: str
    dias_inmaduros: int | None
    motivo_sin_madurez: str | None


@dataclass(frozen=True)
class Dia:
    """Una fecha del rango con sus sumas de campana. `None` = sin dato ese dia."""

    fecha: dt.date
    cost: Decimal | None
    ad_revenue: Decimal | None
    clicks: int | None
    orders: int | None
    observed_at: dt.datetime | None = None


@dataclass(frozen=True)
class Agregado:
    """Sumas del rango junto con la cobertura observada de cada metrica."""

    cost: Decimal | None
    ad_revenue: Decimal | None
    clicks: int | None
    orders: int | None
    dias_cost: int
    dias_ad_revenue: int
    dias_clicks: int
    dias_orders: int
    dias_pares: int
    dias_con_dato: int

    @property
    def dinero_alineado(self) -> bool:
        return (
            self.dias_cost > 0
            and self.dias_cost == self.dias_ad_revenue
            and self.dias_cost == self.dias_pares
        )


def _decimal(valor) -> Decimal | None:
    if valor is None:
        return None
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def _entero(valor) -> int | None:
    return None if valor is None else int(valor)


def agrega_filas(filas: Iterable[Mapping]) -> Agregado:
    """Suma SOLO los valores conocidos y declara la cobertura de cada uno."""
    cost = Decimal(0)
    ad_revenue = Decimal(0)
    clicks = 0
    orders = 0
    dias_cost = dias_ad_revenue = dias_clicks = dias_orders = 0
    dias_pares = dias_con_dato = 0
    for fila in filas:
        valor_cost = _decimal(fila.get("cost"))
        valor_revenue = _decimal(fila.get("ad_revenue"))
        valor_clicks = _entero(fila.get("clicks"))
        valor_orders = _entero(fila.get("orders"))
        if valor_cost is not None:
            cost += valor_cost
            dias_cost += 1
        if valor_revenue is not None:
            ad_revenue += valor_revenue
            dias_ad_revenue += 1
        if valor_clicks is not None:
            clicks += valor_clicks
            dias_clicks += 1
        if valor_orders is not None:
            orders += valor_orders
            dias_orders += 1
        if valor_cost is not None and valor_revenue is not None:
            dias_pares += 1
        if any(
            valor is not None for valor in (valor_cost, valor_revenue, valor_clicks, valor_orders)
        ):
            dias_con_dato += 1
    return Agregado(
        cost=cost if dias_cost else None,
        ad_revenue=ad_revenue if dias_ad_revenue else None,
        clicks=clicks if dias_clicks else None,
        orders=orders if dias_orders else None,
        dias_cost=dias_cost,
        dias_ad_revenue=dias_ad_revenue,
        dias_clicks=dias_clicks,
        dias_orders=dias_orders,
        dias_pares=dias_pares,
        dias_con_dato=dias_con_dato,
    )


def acos_texto(cost: Decimal | None, ad_revenue: Decimal | None) -> str | None:
    """ACoS de DOS SUMAS: `cost/revenue*100`, 2 decimales, STRING."""
    if cost is None or ad_revenue is None or ad_revenue == 0:
        return None
    ratio = (cost / ad_revenue * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(ratio)


def roas_texto(cost: Decimal | None, ad_revenue: Decimal | None) -> str | None:
    """ROAS de DOS SUMAS: `revenue/cost`, 2 decimales, STRING."""
    if cost is None or ad_revenue is None or cost == 0:
        return None
    ratio = (ad_revenue / cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(ratio)


def totales_dias(dias: Sequence[Dia], dias_rango: int) -> dict:
    """Totales del rango desde las sumas (regla 6)."""
    agregado = agrega_filas(
        {
            "cost": dia.cost,
            "ad_revenue": dia.ad_revenue,
            "clicks": dia.clicks,
            "orders": dia.orders,
        }
        for dia in dias
    )
    alineado = agregado.dinero_alineado
    return {
        "cost": _dec_str(agregado.cost),
        "ad_revenue": _dec_str(agregado.ad_revenue),
        "clicks": agregado.clicks,
        "orders": agregado.orders,
        "acos": acos_texto(agregado.cost, agregado.ad_revenue) if alineado else None,
        "roas": roas_texto(agregado.cost, agregado.ad_revenue) if alineado else None,
        "sin_ventas": (
            agregado.ad_revenue is not None
            and agregado.ad_revenue == 0
            and agregado.dias_ad_revenue == dias_rango
        ),
        "dias_con_dato": agregado.dias_con_dato,
        "dias_cost": agregado.dias_cost,
        "dias_ad_revenue": agregado.dias_ad_revenue,
        "dias_clicks": agregado.dias_clicks,
        "dinero_alineado": alineado,
    }


def arma_dias(por_fecha: Mapping[dt.date, tuple], desde: dt.date, hasta: dt.date) -> list[Dia]:
    """Spine completo [desde, hasta]: fecha sin fila -> todo `None` (jamas 0)."""
    dias: list[Dia] = []
    fecha = desde
    while fecha <= hasta:
        fila = por_fecha.get(fecha)
        dias.append(
            Dia(
                fecha=fecha,
                cost=None if fila is None else fila[0],
                ad_revenue=None if fila is None else fila[1],
                clicks=None if fila is None else fila[2],
                orders=None if fila is None else fila[3],
                observed_at=None if fila is None or len(fila) < 5 else fila[4],
            )
        )
        fecha += dt.timedelta(days=1)
    return dias


def _suma_ventana(valores: Sequence) -> Decimal | int | None:
    if any(valor is None for valor in valores):
        return None
    return sum(valores)


def _promedio_ventana(valor, cuantia: Decimal):
    if valor is None:
        return None
    return (Decimal(valor) / DIAS_MA7).quantize(cuantia, rounding=ROUND_HALF_UP)


def _ma7(ventana: Sequence[Dia]) -> dict:
    cost = _suma_ventana([dia.cost for dia in ventana])
    ad_revenue = _suma_ventana([dia.ad_revenue for dia in ventana])
    clicks = _suma_ventana([dia.clicks for dia in ventana])
    orders = _suma_ventana([dia.orders for dia in ventana])
    return {
        "cost": _dec_str(_promedio_ventana(cost, _CUATRO_DEC)),
        "ad_revenue": _dec_str(_promedio_ventana(ad_revenue, _CUATRO_DEC)),
        "clicks": _dec_str(_promedio_ventana(clicks, _DOS_DEC)),
        "orders": _dec_str(_promedio_ventana(orders, _DOS_DEC)),
        "acos": acos_texto(cost, ad_revenue),
        "roas": roas_texto(cost, ad_revenue),
        "completo": all(dia.cost is not None and dia.ad_revenue is not None for dia in ventana),
    }


def _inmaduro(fecha: dt.date, hoy: dt.date, canal: Canal) -> bool | None:
    if canal.dias_inmaduros is None:
        return None
    return hoy - dt.timedelta(days=canal.dias_inmaduros) <= fecha <= hoy - dt.timedelta(days=1)


def _cerrado(dia: Dia, canal: Canal) -> bool | None:
    if dia.observed_at is None:
        return None
    observado = dia.observed_at
    if observado.tzinfo is None:
        observado = observado.replace(tzinfo=dt.UTC)
    return observado >= fin_del_dia(canal.plataforma, dia.fecha)


def fila_serie(dia: Dia, ma7: dict | None, canal: Canal, hoy: dt.date) -> dict:
    cerrado = _cerrado(dia, canal)
    return {
        "fecha": dia.fecha.isoformat(),
        "cost": _dec_str(dia.cost),
        "ad_revenue": _dec_str(dia.ad_revenue),
        "clicks": dia.clicks,
        "orders": dia.orders,
        "acos": acos_texto(dia.cost, dia.ad_revenue),
        "roas": roas_texto(dia.cost, dia.ad_revenue),
        "sin_ventas": dia.ad_revenue is not None and dia.ad_revenue == 0,
        "inmaduro": _inmaduro(dia.fecha, hoy, canal),
        "cerrado": cerrado,
        "motivo_cierre": None if cerrado is not False else "observado_antes_del_cierre",
        "ma7": ma7,
    }


def serie_publica(
    por_fecha: Mapping[dt.date, tuple],
    desde: dt.date,
    hasta: dt.date,
    canal: Canal,
    hoy: dt.date,
) -> list[dict]:
    """Un punto por dia del rango visible; MA7 con 6 dias de calentamiento."""
    inicio_ma7 = desde - dt.timedelta(days=DIAS_CALENTAMIENTO)
    por_dia = {dia.fecha: dia for dia in arma_dias(por_fecha, inicio_ma7, hasta)}
    filas: list[dict] = []
    fecha = desde
    while fecha <= hasta:
        ventana = [
            por_dia[fecha - dt.timedelta(days=atras)] for atras in range(DIAS_CALENTAMIENTO + 1)
        ]
        filas.append(fila_serie(por_dia[fecha], _ma7(ventana), canal, hoy))
        fecha += dt.timedelta(days=1)
    return filas


def cierre_cobertura(dias: Sequence[Dia], canal: Canal) -> dict:
    observados = [dia for dia in dias if dia.observed_at is not None]
    antes = [dia.fecha for dia in observados if _cerrado(dia, canal) is False]
    return {
        "dias_observados": len(observados),
        "dias_antes_del_cierre": len(antes),
        "fechas_antes_del_cierre": [fecha.isoformat() for fecha in antes],
        "todos_cerrados": not antes,
    }


def cobertura(dias: Sequence[Dia], dias_rango: int, canal: Canal | None = None) -> dict:
    agregado = agrega_filas(
        {
            "cost": dia.cost,
            "ad_revenue": dia.ad_revenue,
            "clicks": dia.clicks,
            "orders": dia.orders,
        }
        for dia in dias
    )
    salida = {
        "dias_rango": dias_rango,
        "dias_con_dato": agregado.dias_con_dato,
        "dias_faltantes": dias_rango - agregado.dias_con_dato,
        "por_metrica": {
            "cost": agregado.dias_cost,
            "ad_revenue": agregado.dias_ad_revenue,
            "clicks": agregado.dias_clicks,
            "orders": agregado.dias_orders,
        },
        "completo": agregado.dias_con_dato == dias_rango,
    }
    if canal is not None:
        salida["cierre"] = cierre_cobertura(dias, canal)
    return salida


def _pct(nuevo, anterior) -> str | None:
    if nuevo is None or anterior is None:
        return None
    valor_nuevo, valor_anterior = _decimal(nuevo), _decimal(anterior)
    if valor_nuevo is None or valor_anterior is None or valor_anterior == 0:
        return None
    variacion = ((valor_nuevo - valor_anterior) / valor_anterior * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return str(variacion)


def _cobertura_variacion(actual: Mapping, anterior: Mapping, campo: str) -> tuple[bool, str | None]:
    dias_actual = actual.get("dias_con_dato")
    dias_anterior = anterior.get("dias_con_dato")
    cob_actual = actual.get(campo)
    cob_anterior = anterior.get(campo)
    if dias_actual is None or dias_anterior is None or cob_actual is None or cob_anterior is None:
        return False, "cobertura_desconocida"
    if cob_actual != cob_anterior:
        return False, "cobertura_distinta"
    if cob_actual != dias_actual or cob_anterior != dias_anterior:
        return False, "cobertura_incompleta"
    return True, None


def _dinero_variacion(actual: Mapping, anterior: Mapping) -> tuple[bool, str | None]:
    for campo in ("dias_cost", "dias_ad_revenue"):
        publicado, motivo = _cobertura_variacion(actual, anterior, campo)
        if not publicado:
            return False, motivo
    if not actual.get("dinero_alineado") or not anterior.get("dinero_alineado"):
        if not actual.get("dias_cost") and not anterior.get("dias_cost"):
            return False, None
        return False, "dinero_no_alineado"
    return True, None


def variacion(actual: Mapping, anterior: Mapping) -> dict:
    """Cambio contra el periodo anterior: % en volumen y puntos en ACoS."""
    dias_actual = actual.get("dias_con_dato")
    dias_anterior = anterior.get("dias_con_dato")
    comparable = dias_actual is not None and dias_actual == dias_anterior
    acos_actual, acos_anterior = _decimal(actual.get("acos")), _decimal(anterior.get("acos"))
    roas_actual, roas_anterior = _decimal(actual.get("roas")), _decimal(anterior.get("roas"))
    cost_publica, cost_motivo = _cobertura_variacion(actual, anterior, "dias_cost")
    revenue_publica, revenue_motivo = _cobertura_variacion(actual, anterior, "dias_ad_revenue")
    clicks_publica, clicks_motivo = _cobertura_variacion(actual, anterior, "dias_clicks")
    dinero_publica, dinero_motivo = _dinero_variacion(actual, anterior)
    dos_dec = Decimal("0.01")
    return {
        "cost_pct": _pct(actual.get("cost"), anterior.get("cost")) if cost_publica else None,
        "ad_revenue_pct": (
            _pct(actual.get("ad_revenue"), anterior.get("ad_revenue")) if revenue_publica else None
        ),
        "clicks_pct": (
            _pct(actual.get("clicks"), anterior.get("clicks")) if clicks_publica else None
        ),
        "acos_pp": (
            None
            if not dinero_publica or acos_actual is None or acos_anterior is None
            else str((acos_actual - acos_anterior).quantize(dos_dec, rounding=ROUND_HALF_UP))
        ),
        "roas_delta": (
            None
            if not dinero_publica or roas_actual is None or roas_anterior is None
            else str((roas_actual - roas_anterior).quantize(dos_dec, rounding=ROUND_HALF_UP))
        ),
        "comparable": comparable,
        "motivo": None if comparable else "cobertura_distinta",
        "motivo_por_metrica": {
            "cost_pct": cost_motivo,
            "ad_revenue_pct": revenue_motivo,
            "clicks_pct": clicks_motivo,
            "acos_pp": dinero_motivo,
            "roas_delta": dinero_motivo,
        },
    }


def _suma_conocidos(valores: Sequence) -> Decimal | None:
    conocidos = [_decimal(valor) for valor in valores]
    conocidos = [valor for valor in conocidos if valor is not None]
    return sum(conocidos) if conocidos else None


def _suma_grupo(totales: Sequence[Mapping], metrica: str) -> Decimal | None:
    valores = [total.get(metrica) for total in totales]
    if any(valor is None for valor in valores):
        return None
    return _suma_conocidos(valores)


def totales_por_moneda(bloques: Mapping[str, Mapping]) -> list[dict]:
    """Totales multi-canal AGRUPADOS por moneda (jamas una moneda mezclada)."""
    grupos: dict[str, list[tuple[str, Mapping]]] = {}
    for nombre, bloque in bloques.items():
        cobertura_canal = bloque.get("coverage")
        if (
            "dias" not in bloque
            or not isinstance(cobertura_canal, Mapping)
            or "completo" not in cobertura_canal
            or not isinstance(cobertura_canal.get("por_metrica"), Mapping)
            or not set(_METRICAS_TOTALES).issubset(cobertura_canal["por_metrica"])
        ):
            raise ValueError(
                f"el bloque {nombre} requiere dias y coverage con completo y "
                "por_metrica para cost, ad_revenue, clicks y orders"
            )
        grupos.setdefault(bloque["moneda"], []).append((nombre, bloque))
    salida: list[dict] = []
    for moneda in sorted(grupos):
        miembros = grupos[moneda]
        totales = [bloque["totals"] for _nombre, bloque in miembros]
        dias_completos = all(bloque["coverage"]["completo"] for _nombre, bloque in miembros)
        cobertura_grupo = {
            metrica: all(
                bloque["coverage"]["por_metrica"][metrica] == bloque["dias"]
                for _nombre, bloque in miembros
            )
            for metrica in _METRICAS_TOTALES
        }
        cobertura_grupo["dias"] = dias_completos
        completo = all(cobertura_grupo.values())
        alineado = (
            cobertura_grupo["cost"]
            and cobertura_grupo["ad_revenue"]
            and dias_completos
            and all(bool(total.get("dinero_alineado")) for total in totales)
        )
        cost = _suma_grupo(totales, "cost")
        ad_revenue = _suma_grupo(totales, "ad_revenue")
        clicks = _suma_grupo(totales, "clicks")
        orders = _suma_grupo(totales, "orders")
        salida.append(
            {
                "moneda": moneda,
                "canales": [nombre for nombre, _bloque in miembros],
                "canales_sin_dato": [
                    nombre
                    for nombre, bloque in miembros
                    if bloque["totals"].get("cost") is None
                    and bloque["totals"].get("ad_revenue") is None
                ],
                "cost": _dec_str(cost),
                "ad_revenue": _dec_str(ad_revenue),
                "clicks": None if clicks is None else int(clicks),
                "orders": None if orders is None else int(orders),
                "acos": acos_texto(cost, ad_revenue) if alineado else None,
                "roas": roas_texto(cost, ad_revenue) if alineado else None,
                "sin_ventas": (
                    alineado
                    and ad_revenue is not None
                    and ad_revenue == 0
                    and all(bool(total.get("sin_ventas")) for total in totales)
                ),
                "cobertura": cobertura_grupo,
                "completo": completo,
            }
        )
    return salida
