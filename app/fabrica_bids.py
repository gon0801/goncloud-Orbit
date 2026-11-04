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
    # Procedencia del sugerido, firmada en huella y plan: la que devolvió
    # Amazon o el promedio del rol (decisión del dueño 2026-09-09).
    fuente: str = "amazon_v4"


@dataclass(frozen=True)
class ResultadoRol:
    bid: Decimal | None
    recomendaciones: tuple[Recomendacion, ...]
    faltantes: tuple[Expresion, ...] = ()
    # Promedio aplicado a las faltantes del rol; None si no se promedió nada.
    promedio: Decimal | None = None


def recomendaciones_como_json(recomendaciones: tuple[Recomendacion, ...]) -> list[dict]:
    return [
        {
            "tipo": r.expresion.tipo,
            "valor": r.expresion.valor,
            "minimo": str(r.minimo),
            "sugerido": str(r.sugerido),
            "maximo": str(r.maximo),
            "fuente": r.fuente,
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
            # Planes firmados antes del promedio no traen fuente: todos eran
            # cobertura completa de Amazon (compat hacia atrás).
            fila.get("fuente", "amazon_v4") if isinstance(fila, dict) else "amazon_v4",
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


def promedio_rol(recomendaciones: tuple[Recomendacion, ...], moneda: str) -> Decimal:
    """Promedio simple de los sugeridos del rol, acotado por piso y techo.

    Decisión del dueño 2026-09-09: las expresiones sin sugerencia usan el
    promedio del rol en vez de pasar a manual. Sin sugerencias no hay nada
    que promediar (regla 3) y se lanza: el rol sigue manual.
    """
    if not recomendaciones:
        raise RecomendacionIncompleta("sin sugerencias para promediar el rol")
    try:
        piso, techo = DEFAULTS_POR_MONEDA[moneda]
    except KeyError:
        raise RecomendacionIncompleta(f"moneda sin limites de bid: {moneda}") from None
    media = sum(r.sugerido for r in recomendaciones) / len(recomendaciones)
    return min(max(media, piso), techo).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def error_parametro_amazon(
    recomendaciones: tuple[Recomendacion, ...], bid: Decimal, budget: Decimal, moneda: str
) -> str | None:
    """Valida evidencia, mediana y que cada objetivo pueda comprar un clic.

    La mediana firmada se calcula sobre las sugerencias reales de Amazon:
    las promediadas acompañan con su fuente explícita, no entran a la mediana.
    """
    if not recomendaciones:
        return "sin recomendaciones"
    if any(r.fuente not in ("amazon_v4", "promedio_rol") for r in recomendaciones):
        return "fuente de recomendacion invalida"
    reales = tuple(r for r in recomendaciones if r.fuente == "amazon_v4")
    if not reales:
        return "sin recomendaciones de Amazon"
    esperado = promedio_rol(reales, moneda)
    if any(
        (r.minimo, r.sugerido, r.maximo) != (esperado, esperado, esperado)
        for r in recomendaciones
        if r.fuente == "promedio_rol"
    ):
        return "promedio del rol no coincide con las sugerencias reales"
    for recomendacion in recomendaciones:
        terna = (recomendacion.minimo, recomendacion.sugerido, recomendacion.maximo)
        if not all(v.is_finite() and v > 0 for v in terna) or not (
            terna[0] <= terna[1] <= terna[2]
        ):
            return "terna invalida"
    if bid != bid_inicial(reales, moneda):
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
    """Consulta por familia; cada expresión sin sugerencia usa el promedio del
    rol (decisión del dueño 2026-09-09). Solo el rol con CERO sugerencias
    queda sin bid (manual): no hay nada que promediar, regla 3."""
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
            salida[rol] = ResultadoRol(None, (), (), None)
            continue
        reales = interpretar(datos_por_rol[rol], expresiones, exigir_todas=False)
        if not reales:
            salida[rol] = ResultadoRol(None, (), tuple(expresiones), None)
            continue
        por_expresion = {r.expresion: r for r in reales}
        faltantes = tuple(e for e in expresiones if e not in por_expresion)
        if not faltantes:
            salida[rol] = ResultadoRol(bid_inicial(reales, moneda), reales, (), None)
            continue
        promedio = promedio_rol(reales, moneda)
        completas = tuple(
            por_expresion[e]
            if e in por_expresion
            else Recomendacion(e, promedio, promedio, promedio, "promedio_rol")
            for e in expresiones
        )
        # defaultBid conserva la mediana de las sugerencias reales de Amazon.
        salida[rol] = ResultadoRol(bid_inicial(reales, moneda), completas, (), promedio)
    return salida
