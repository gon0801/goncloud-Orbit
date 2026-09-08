"""Contrato puro de sugerencias de puja para campañas Sponsored Products."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from app.optimizer.goals import DEFAULTS_POR_MONEDA


class RecomendacionIncompleta(ValueError):
    """Amazon omitio o devolvio ilegible una sugerencia solicitada."""


@dataclass(frozen=True, order=True)
class Expresion:
    tipo: str
    valor: str | None = None

    def como_json(self) -> dict[str, str]:
        salida = {"type": self.tipo}
        if self.valor is not None:
            salida["value"] = self.valor
        return salida


@dataclass(frozen=True)
class Recomendacion:
    expresion: Expresion
    minimo: Decimal
    sugerido: Decimal
    maximo: Decimal


@dataclass(frozen=True)
class ResultadoRol:
    bid: Decimal | None
    recomendaciones: tuple[Recomendacion, ...]
    faltantes: tuple[Expresion, ...] = ()


def recomendaciones_como_json(recomendaciones: tuple[Recomendacion, ...]) -> list[dict]:
    return [
        {
            "tipo": r.expresion.tipo,
            "valor": r.expresion.valor,
            "minimo": str(r.minimo),
            "sugerido": str(r.sugerido),
            "maximo": str(r.maximo),
        }
        for r in recomendaciones
    ]


def recomendaciones_desde_json(filas: list[dict] | tuple) -> tuple[Recomendacion, ...]:
    return tuple(
        Recomendacion(
            Expresion(fila["tipo"], fila.get("valor")),
            Decimal(fila["minimo"]),
            Decimal(fila["sugerido"]),
            Decimal(fila["maximo"]),
        )
        for fila in filas
    )


def payload(asins: tuple[str, ...], expresiones: tuple[Expresion, ...]) -> dict:
    """Cuerpo v4 determinista para un ad group nuevo."""
    return {
        "recommendationType": "BIDS_FOR_NEW_AD_GROUP",
        "asins": sorted(set(asins)),
        # La fabrica crea las campanas con LEGACY_FOR_SALES; la sugerencia
        # debe calcularse para esa misma estrategia (un numero, una fuente).
        "bidding": {"strategy": "LEGACY_FOR_SALES"},
        "targetingExpressions": [expresion.como_json() for expresion in expresiones],
    }


def _decimal(valor: object, expresion: Expresion) -> Decimal:
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise RecomendacionIncompleta(
            f"bid ilegible para {expresion.tipo} {expresion.valor or ''}".strip()
        ) from None
    if not numero.is_finite() or numero <= 0:
        raise RecomendacionIncompleta(
            f"bid invalido para {expresion.tipo} {expresion.valor or ''}".strip()
        )
    return numero


def _clave(datos: object) -> Expresion | None:
    if not isinstance(datos, dict) or not isinstance(datos.get("type"), str):
        return None
    valor = datos.get("value")
    if valor is not None and not isinstance(valor, str):
        return None
    return Expresion(datos["type"], valor)


def interpretar(
    datos: object,
    solicitadas: tuple[Expresion, ...],
    *,
    exigir_todas: bool = True,
) -> tuple[Recomendacion, ...]:
    """Extrae la terna bajo/medio/alto y exige respuesta para cada expresion."""
    encontradas: dict[Expresion, Recomendacion] = {}
    grupos = datos.get("bidRecommendations") if isinstance(datos, dict) else None
    for grupo in grupos if isinstance(grupos, list) else ():
        filas = (
            grupo.get("bidRecommendationsForTargetingExpressions")
            if isinstance(grupo, dict)
            else None
        )
        for fila in filas if isinstance(filas, list) else ():
            if not isinstance(fila, dict):
                continue
            expresion = _clave(fila.get("targetingExpression"))
            valores = fila.get("bidValues")
            if expresion is None or not isinstance(valores, list) or len(valores) != 3:
                continue
            sugeridos = [v.get("suggestedBid") if isinstance(v, dict) else None for v in valores]
            try:
                numeros = tuple(_decimal(valor, expresion) for valor in sugeridos)
            except RecomendacionIncompleta:
                continue
            encontradas[expresion] = Recomendacion(expresion, *numeros)

    faltantes = [expresion for expresion in solicitadas if expresion not in encontradas]
    if exigir_todas and faltantes:
        detalle = ", ".join(f"{expresion.tipo}:{expresion.valor or '-'}" for expresion in faltantes)
        raise RecomendacionIncompleta(f"Amazon no sugirio bid para: {detalle}")
    return tuple(encontradas[expresion] for expresion in solicitadas if expresion in encontradas)


def bid_inicial(recomendaciones: tuple[Recomendacion, ...], moneda: str) -> Decimal:
    """Mediana de sugerencias, limitada por los topes existentes del motor."""
    if not recomendaciones:
        raise RecomendacionIncompleta("sin recomendaciones para calcular bid inicial")
    try:
        piso, techo = DEFAULTS_POR_MONEDA[moneda]
    except KeyError:
        raise RecomendacionIncompleta(f"moneda sin limites de bid: {moneda}") from None
    ordenados = sorted(r.sugerido for r in recomendaciones)
    centro = len(ordenados) // 2
    mediana = (
        ordenados[centro]
        if len(ordenados) % 2
        else (ordenados[centro - 1] + ordenados[centro]) / Decimal(2)
    )
    limitado = min(max(mediana, piso), techo)
    return limitado.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def bid_objetivo(recomendacion: Recomendacion, moneda: str) -> Decimal:
    """Aplica al sugerido individual los limites monetarios del motor."""
    try:
        piso, techo = DEFAULTS_POR_MONEDA[moneda]
    except KeyError:
        raise RecomendacionIncompleta(f"moneda sin limites de bid: {moneda}") from None
    return min(max(recomendacion.sugerido, piso), techo).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )


def error_parametro_amazon(
    recomendaciones: tuple[Recomendacion, ...], bid: Decimal, budget: Decimal, moneda: str
) -> str | None:
    """Valida evidencia, mediana y que cada objetivo pueda comprar un clic."""
    if not recomendaciones:
        return "sin recomendaciones"
    for recomendacion in recomendaciones:
        terna = (recomendacion.minimo, recomendacion.sugerido, recomendacion.maximo)
        if not all(v.is_finite() and v > 0 for v in terna) or not (
            terna[0] <= terna[1] <= terna[2]
        ):
            return "terna invalida"
    if bid != bid_inicial(recomendaciones, moneda):
        return "no coincide con la mediana firmada"
    if any(budget < bid_objetivo(r, moneda) for r in recomendaciones):
        return "budget menor que un bid de objetivo sugerido"
    return None


def consultar_roles(
    cliente,
    *,
    profile_id: str | int,
    moneda: str,
    asins: tuple[str, ...],
    expresiones_por_rol: dict[str, tuple[Expresion, ...]],
) -> dict[str, ResultadoRol]:
    """Consulta por familia; un rol incompleto queda sin bid, nunca con fallback."""
    familias = (
        ("category_exact", "category_phrase", "category_broad"),
        ("product_targeting",),
        ("auto_discovery",),
    )
    datos_por_rol = {}
    for familia in familias:
        roles = [rol for rol in familia if rol in expresiones_por_rol and expresiones_por_rol[rol]]
        if not roles:
            continue
        expresiones = tuple(
            dict.fromkeys(expresion for rol in roles for expresion in expresiones_por_rol[rol])
        )
        respuesta = cliente.recommend_bids(payload(asins, expresiones), profile_id=profile_id)
        datos = respuesta.json()
        datos_por_rol.update({rol: datos for rol in roles})
    salida = {}
    for rol, expresiones in expresiones_por_rol.items():
        if not expresiones:
            salida[rol] = ResultadoRol(None, (), ())
            continue
        recomendaciones = interpretar(datos_por_rol[rol], expresiones, exigir_todas=False)
        presentes = {r.expresion for r in recomendaciones}
        faltantes = tuple(expresion for expresion in expresiones if expresion not in presentes)
        bid = bid_inicial(recomendaciones, moneda) if not faltantes else None
        salida[rol] = ResultadoRol(bid, recomendaciones, faltantes)
    return salida
