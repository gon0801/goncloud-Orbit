"""Agregados de presentacion desde la fuente del dashboard, sin nuevas consultas.

RESUMEN-ADS-02: las sumas, la cobertura y el ACoS salen de la MISMA agregacion
del Resumen (`app.dashboard_resumen`); aqui solo se formatea a la presentacion
que ya consumian las plantillas (dinero a 2 decimales, clics enteros).
"""

from decimal import Decimal

from app.dashboard_resumen import acos_texto, agrega_filas


def _total(filas: list[dict], campo: str) -> Decimal | None:
    valores = [Decimal(str(f[campo])) for f in filas if f.get(campo) is not None]
    return sum(valores, Decimal(0)) if valores else None


def kpis_serie(datos: dict) -> dict:
    """Suma solo dias observados de UNA plataforma; los huecos se declaran."""
    filas = datos["series"]
    agregado = agrega_filas(filas)
    acos = acos_texto(agregado.cost, agregado.ad_revenue) if agregado.dinero_alineado else None
    return {
        "cost": f"{agregado.cost:.2f}" if agregado.cost is not None else None,
        "ad_revenue": (f"{agregado.ad_revenue:.2f}" if agregado.ad_revenue is not None else None),
        "clicks": agregado.clicks,
        "acos": acos,
        "cobertura": {
            "cost": agregado.dias_cost,
            "ad_revenue": agregado.dias_ad_revenue,
            "clicks": agregado.dias_clicks,
            "acos": agregado.dias_pares,
        },
        "dias": len(filas),
    }


def kpis_inertes(items: list[dict]) -> dict:
    """Gasto observado separado por moneda; antiguedad desconocida explicita."""
    monedas = sorted({i["moneda"] for i in items if i.get("moneda")})
    gasto = {}
    for moneda in monedas:
        total = _total([i for i in items if i.get("moneda") == moneda], "gasto_90d")
        gasto[moneda] = f"{total:.2f}" if total is not None else None
    return {
        "hojas": len(items),
        "en_espera": sum(i.get("en_espera") is True for i in items),
        "sin_antiguedad": sum(i.get("en_espera") is None for i in items),
        "gasto": gasto,
    }


def clase_cambio(nuevo: str | None, anterior: str | None) -> str:
    """Color de una comparacion monetaria exacta, sin convertir a float."""
    if nuevo is None or anterior is None:
        return "mutado"
    diferencia = Decimal(nuevo) - Decimal(anterior)
    return "ok" if diferencia > 0 else "alerta" if diferencia < 0 else "mutado"
