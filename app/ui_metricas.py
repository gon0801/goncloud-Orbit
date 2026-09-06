"""Agregados de presentacion desde la fuente del dashboard, sin nuevas consultas."""

from decimal import ROUND_HALF_UP, Decimal


def _total(filas: list[dict], campo: str) -> Decimal | None:
    valores = [Decimal(str(f[campo])) for f in filas if f.get(campo) is not None]
    return sum(valores, Decimal(0)) if valores else None


def kpis_serie(datos: dict) -> dict:
    """Suma solo dias observados de UNA plataforma; los huecos se declaran."""
    filas = datos["series"]
    cost, revenue = _total(filas, "cost"), _total(filas, "ad_revenue")
    clicks = _total(filas, "clicks")
    cobertura = {
        campo: sum(f.get(campo) is not None for f in filas)
        for campo in ("cost", "ad_revenue", "clicks")
    }
    compatibles = all((f.get("cost") is None) == (f.get("ad_revenue") is None) for f in filas)
    acos = cost / revenue * 100 if compatibles and cost is not None and revenue else None
    cobertura["acos"] = sum(
        f.get("cost") is not None and f.get("ad_revenue") is not None for f in filas
    )
    return {
        "cost": f"{cost:.2f}" if cost is not None else None,
        "ad_revenue": f"{revenue:.2f}" if revenue is not None else None,
        "clicks": int(clicks) if clicks is not None else None,
        "acos": str(acos.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if acos is not None
        else None,
        "cobertura": cobertura,
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
