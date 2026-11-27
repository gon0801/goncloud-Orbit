"""Regla PURA de la pausa economica v2 (`economic_pause_v2`).

Un dueno por estado (regla 1): la regla vive SOLO aqui y se invoca desde cinco
puntos (evaluacion del ciclo en `shadow`, `decide(caso)` en `live`,
`_proteccion_activa`, `revalida_pause` y el replay de
`tools/pausa_economica_verifica.py`). Cero IO: no importa psycopg, httpx, la
API de Amazon ni la capa de base.

`v2` es un SUBCONJUNTO de `v1` por construccion: su unica diferencia es
QUITAR pausas cuando la ventana reciente que no maduro tiene una venta que
salva, una recuperacion rentable o un dato no medible. La puerta madura es la
misma regla economica de `v1` (`cost > 3 * target * revenue` y exceso minimo),
asi que toda hoja que `v2` pausa tambien la pausaria `v1` (regla 6 intacta).

Regla exacta (plan P03, seccion 2):

1. Puerta madura: `cortes` (ventana de cortes de la hoja) completa, en moneda,
   con `cost`, `ad_revenue`, `orders` y `clicks` conocidos, finitos y `>= 0`;
   `cost > 3 * target * revenue / 100` y `exceso_mad >= exceso_minimo`.
2. Integridad de la ventana reciente `[cortes.window_end + 1d .. D - 1]`
   (regla 3, fail-closed): `completa`, misma moneda, `desde` pegada al fin de
   cortes, `hasta == D - 1`, y `cost`/`ad_revenue`/`orders` conocidos y `>= 0`.
3. Puerta combinada `[cortes.window_start .. D - 1]`: las dos condiciones de
   la puerta madura sobre la suma.
4. Guarda de recuperacion rentable reciente: con al menos un pedido y
   `cost_reciente <= target * revenue_reciente / 100` no se pausa.
5. Pasa 1-4 -> `pausar`/`pause_economica`; si no, `mantener` con el motivo del
   vocabulario cerrado. Los `mantener` JAMAS congelan el juicio de pujas.

Todos los montos son `Decimal` (regla 4); jamas `float`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from app.optimizer.caso import LecturaRecienteEconomica
from app.optimizer.windows import AgregadoMetricas

# Una sola fuente de las constantes de la regla economica (bid.py las
# re-exporta para no romper a sus consumidores historicos).
EXCESO_MINIMO: dict[str, Decimal] = {"USD": Decimal("80"), "MXN": Decimal("1000")}
MULT_PAUSE_ECONOMICA = Decimal("3")
POLITICA_PAUSE_ECONOMICA_V2 = "economic_pause_v2"
# Cortacircuito de anomalia por mercado y ciclo (solo `live`): un techo de 5
# pausas economicas en un ciclo delata una ingesta rota con ventas en cero.
TOPE_PAUSA_ECONOMICA_CICLO = 5

# Motivos (vocabulario CERRADO; los tests los fijan literal y el CHECK
# `pausa_economica_evaluacion_motivo_veredicto` es su espejo en la base).
MOTIVO_PAUSE_ECONOMICA = "pause_economica"
MOTIVO_SALVADA = "pause_economica_salvada_reciente"
MOTIVO_RECUPERANDO = "pause_economica_recuperando_reciente"
MOTIVO_INCOMPLETO = "pause_economica_reciente_incompleto"
MOTIVO_NO_CRUZA_MADURA = "pause_economica_no_cruza_madura"
MOTIVO_CORTES_INCOMPLETOS = "pause_economica_cortes_incompletos"
MOTIVOS_MANTENER = (
    MOTIVO_SALVADA,
    MOTIVO_RECUPERANDO,
    MOTIVO_INCOMPLETO,
    MOTIVO_NO_CRUZA_MADURA,
    MOTIVO_CORTES_INCOMPLETOS,
)
VEREDICTOS_PAUSA_ECONOMICA = ("mantener", "pausar")

# Configuracion por mercado (fail-closed: ausente o invalido = 'off').
CLAVE_PAUSA_ECONOMICA = "ads_pause_economica"
MODOS_PAUSA_ECONOMICA = ("off", "shadow", "live")
VERSION_ENTRADA = POLITICA_PAUSE_ECONOMICA_V2

_CIEN = Decimal("100")


@dataclass(frozen=True)
class EntradaPausaEconomica:
    """Insumos congelados que juzga la regla. Es lo que `entrada` guarda en
    `pausa_economica_evaluacion` y lo que el replay vuelve a ejecutar."""

    cortes: AgregadoMetricas | None
    reciente: LecturaRecienteEconomica | None
    moneda: str
    target_acos_pct: Decimal
    exceso_minimo: Decimal


@dataclass(frozen=True)
class VeredictoPausaEconomica:
    """Salida pura. Los montos viajan crudos para que la fila auditada no
    dependa de reparsear el JSON de la entrada."""

    veredicto: str
    motivo: str
    cortes_completa: bool
    cost_maduro: Decimal | None
    revenue_maduro: Decimal | None
    cost_reciente: Decimal | None
    revenue_reciente: Decimal | None
    exceso_combinado: Decimal | None


def _es_monto_valido(valor: Decimal | None) -> bool:
    return valor is not None and valor.is_finite() and valor >= 0


def _cortes_juzgables(cortes: AgregadoMetricas, moneda: str) -> bool:
    """Regla 6: la ventana madura tiene que estar completa y medida."""
    return (
        cortes.completa
        and cortes.metric_currency == moneda
        and _es_monto_valido(cortes.cost)
        and _es_monto_valido(cortes.ad_revenue)
        and cortes.orders is not None
        and cortes.orders >= 0
        and cortes.clicks is not None
        and cortes.clicks >= 0
    )


def _reciente_integra(
    reciente: LecturaRecienteEconomica | None, cortes: AgregadoMetricas, moneda: str
) -> bool:
    """Regla 3: la ventana reciente ausente, incompleta, desalineada, en otra
    moneda o con montos no medibles NO juzga (jamas se inventa un cero)."""
    if reciente is None:
        return False
    return (
        reciente.completa is True
        and reciente.moneda == moneda
        and reciente.desde == cortes.window_end + dt.timedelta(days=1)
        and reciente.desde <= reciente.hasta
        and reciente.hasta == reciente.evaluado_el - dt.timedelta(days=1)
        and _es_monto_valido(reciente.cost)
        and _es_monto_valido(reciente.ad_revenue)
        and reciente.orders is not None
        and reciente.orders >= 0
    )


def _cruza(
    cost: Decimal, revenue: Decimal, target_acos_pct: Decimal, exceso_minimo: Decimal
) -> bool:
    """Las DOS condiciones economicas, por multiplicacion exacta (sin dividir)."""
    return (
        cost > MULT_PAUSE_ECONOMICA * target_acos_pct * revenue / _CIEN
        and cost - target_acos_pct * revenue / _CIEN >= exceso_minimo
    )


def _veredicto(
    veredicto: str,
    motivo: str,
    cortes: AgregadoMetricas | None,
    exceso_combinado: Decimal | None = None,
) -> VeredictoPausaEconomica:
    cost_maduro = cortes.cost if cortes is not None else None
    revenue_maduro = cortes.ad_revenue if cortes is not None else None
    return VeredictoPausaEconomica(
        veredicto=veredicto,
        motivo=motivo,
        cortes_completa=bool(cortes is not None and cortes.completa),
        cost_maduro=cost_maduro,
        revenue_maduro=revenue_maduro,
        cost_reciente=None,
        revenue_reciente=None,
        exceso_combinado=exceso_combinado,
    )


def evalua_pausa_economica(entrada: EntradaPausaEconomica) -> VeredictoPausaEconomica:
    """La regla `v2` completa, determinista y sin IO."""
    cortes = entrada.cortes
    if cortes is None:
        return _veredicto("mantener", MOTIVO_CORTES_INCOMPLETOS, None)
    if not _cortes_juzgables(cortes, entrada.moneda):
        return _veredicto("mantener", MOTIVO_CORTES_INCOMPLETOS, cortes)
    reciente = entrada.reciente
    if not _reciente_integra(reciente, cortes, entrada.moneda):
        return _veredicto("mantener", MOTIVO_INCOMPLETO, cortes)
    assert reciente is not None and reciente.cost is not None
    assert reciente.ad_revenue is not None and reciente.orders is not None
    if not _cruza(cortes.cost, cortes.ad_revenue, entrada.target_acos_pct, entrada.exceso_minimo):
        return _veredicto("mantener", MOTIVO_NO_CRUZA_MADURA, cortes)
    cost_tot = cortes.cost + reciente.cost
    rev_tot = cortes.ad_revenue + reciente.ad_revenue
    exceso_tot = cost_tot - entrada.target_acos_pct * rev_tot / _CIEN
    if not _cruza(cost_tot, rev_tot, entrada.target_acos_pct, entrada.exceso_minimo):
        return _veredicto("mantener", MOTIVO_SALVADA, cortes, exceso_tot)
    if reciente.orders >= 1 and reciente.cost <= (
        entrada.target_acos_pct * reciente.ad_revenue / _CIEN
    ):
        return _veredicto("mantener", MOTIVO_RECUPERANDO, cortes, exceso_tot)
    veredicto = _veredicto("pausar", MOTIVO_PAUSE_ECONOMICA, cortes, exceso_tot)
    return VeredictoPausaEconomica(
        veredicto=veredicto.veredicto,
        motivo=veredicto.motivo,
        cortes_completa=veredicto.cortes_completa,
        cost_maduro=veredicto.cost_maduro,
        revenue_maduro=veredicto.revenue_maduro,
        cost_reciente=reciente.cost,
        revenue_reciente=reciente.ad_revenue,
        exceso_combinado=exceso_tot,
    )


# ---------------------------------------------------------------------------
# Codec JSON de la entrada (freeze del replay determinista)
# ---------------------------------------------------------------------------


def _monto_json(valor: Decimal | None) -> str | None:
    return None if valor is None else str(valor)


def _agregado_json(agregado: AgregadoMetricas | None) -> dict | None:
    if agregado is None:
        return None
    return {
        "window_start": agregado.window_start.isoformat(),
        "window_end": agregado.window_end.isoformat(),
        "fechas": [fecha.isoformat() for fecha in agregado.fechas],
        "metric_currency": agregado.metric_currency,
        "cost": _monto_json(agregado.cost),
        "ad_revenue": _monto_json(agregado.ad_revenue),
        "revenue_same_sku": _monto_json(agregado.revenue_same_sku),
        "impressions": agregado.impressions,
        "clicks": agregado.clicks,
        "orders": agregado.orders,
        "observed_at_max": (
            None if agregado.observed_at_max is None else agregado.observed_at_max.isoformat()
        ),
    }


def _reciente_json(reciente: LecturaRecienteEconomica | None) -> dict | None:
    if reciente is None:
        return None
    return {
        "desde": reciente.desde.isoformat(),
        "hasta": reciente.hasta.isoformat(),
        "evaluado_el": reciente.evaluado_el.isoformat(),
        "moneda": reciente.moneda,
        "completa": reciente.completa,
        "cost": _monto_json(reciente.cost),
        "ad_revenue": _monto_json(reciente.ad_revenue),
        "orders": reciente.orders,
    }


def entrada_a_json(entrada: EntradaPausaEconomica) -> dict:
    """Freeze unico de la entrada (Decimal como string, fechas ISO)."""
    return {
        "version": VERSION_ENTRADA,
        "moneda": entrada.moneda,
        "target_acos_pct": str(entrada.target_acos_pct),
        "exceso_minimo": str(entrada.exceso_minimo),
        "cortes": _agregado_json(entrada.cortes),
        "reciente": _reciente_json(entrada.reciente),
    }


def _fecha(datos: dict, clave: str) -> dt.date:
    valor = datos.get(clave)
    if not isinstance(valor, str):
        raise ValueError(f"entrada pausa economica: {clave} debe ser fecha ISO")
    try:
        return dt.date.fromisoformat(valor)
    except ValueError as exc:
        raise ValueError(f"entrada pausa economica: {clave} no es fecha ISO: {valor!r}") from exc


def _decimal(datos: dict, clave: str) -> Decimal:
    valor = datos.get(clave)
    if valor is None:
        raise ValueError(f"entrada pausa economica: falta {clave}")
    try:
        numero = Decimal(str(valor))
    except ArithmeticError as exc:
        raise ValueError(f"entrada pausa economica: {clave} no es decimal: {valor!r}") from exc
    if not numero.is_finite():
        raise ValueError(f"entrada pausa economica: {clave} no es finito: {valor!r}")
    return numero


def _opcional_decimal(datos: dict, clave: str) -> Decimal | None:
    return None if datos.get(clave) is None else _decimal(datos, clave)


def _opcional_entero(datos: dict, clave: str) -> int | None:
    valor = datos.get(clave)
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValueError(f"entrada pausa economica: {clave} debe ser entero: {valor!r}")
    return valor


def _agregado_desde_json(datos: dict | None) -> AgregadoMetricas | None:
    if datos is None:
        return None
    if not isinstance(datos, dict):
        raise ValueError("entrada pausa economica: cortes debe ser objeto")
    observado = datos.get("observed_at_max")
    return AgregadoMetricas(
        window_start=_fecha(datos, "window_start"),
        window_end=_fecha(datos, "window_end"),
        fechas=tuple(dt.date.fromisoformat(f) for f in datos.get("fechas") or ()),
        metric_currency=datos.get("metric_currency"),
        cost=_opcional_decimal(datos, "cost"),
        ad_revenue=_opcional_decimal(datos, "ad_revenue"),
        revenue_same_sku=_opcional_decimal(datos, "revenue_same_sku"),
        impressions=_opcional_entero(datos, "impressions"),
        clicks=_opcional_entero(datos, "clicks"),
        orders=_opcional_entero(datos, "orders"),
        observed_at_max=None if observado is None else dt.datetime.fromisoformat(observado),
    )


def _reciente_desde_json(datos: dict | None) -> LecturaRecienteEconomica | None:
    if datos is None:
        return None
    if not isinstance(datos, dict):
        raise ValueError("entrada pausa economica: reciente debe ser objeto")
    completa = datos.get("completa")
    if not isinstance(completa, bool):
        raise ValueError("entrada pausa economica: reciente.completa debe ser booleano")
    moneda = datos.get("moneda")
    if not isinstance(moneda, str):
        raise ValueError("entrada pausa economica: reciente.moneda debe ser texto")
    return LecturaRecienteEconomica(
        desde=_fecha(datos, "desde"),
        hasta=_fecha(datos, "hasta"),
        evaluado_el=_fecha(datos, "evaluado_el"),
        moneda=moneda,
        completa=completa,
        cost=_opcional_decimal(datos, "cost"),
        ad_revenue=_opcional_decimal(datos, "ad_revenue"),
        orders=_opcional_entero(datos, "orders"),
    )


def entrada_desde_json(datos: dict) -> EntradaPausaEconomica:
    """Inversa exacta de `entrada_a_json`; fail-closed en version y tipos."""
    if not isinstance(datos, dict):
        raise ValueError("entrada pausa economica: debe ser objeto")
    if datos.get("version") != VERSION_ENTRADA:
        raise ValueError(f"entrada pausa economica: version desconocida {datos.get('version')!r}")
    moneda = datos.get("moneda")
    if not isinstance(moneda, str) or moneda not in EXCESO_MINIMO:
        raise ValueError(f"entrada pausa economica: moneda invalida {moneda!r}")
    return EntradaPausaEconomica(
        cortes=_agregado_desde_json(datos.get("cortes")),
        reciente=_reciente_desde_json(datos.get("reciente")),
        moneda=moneda,
        target_acos_pct=_decimal(datos, "target_acos_pct"),
        exceso_minimo=_decimal(datos, "exceso_minimo"),
    )


# ---------------------------------------------------------------------------
# Configuracion por mercado
# ---------------------------------------------------------------------------


def clave_pausa_economica(platform: str) -> str:
    """Clave sellada por mercado: el encendido es mercado por mercado."""
    return f"{CLAVE_PAUSA_ECONOMICA}_{platform}"


def _valida_modo(modo: object) -> str:
    if not isinstance(modo, str) or modo not in MODOS_PAUSA_ECONOMICA:
        raise ValueError(
            f"modo de pausa economica fuera del vocabulario {{off, shadow, live}}: {modo!r}"
        )
    return modo


def modo_pausa_economica_desde_settings(settings, platform: str) -> str:
    """Modo del mercado. Fail-closed: ausente, NULL, bool, entero o texto
    invalido -> 'off' (jamas se enciende por un dato raro)."""
    valor = settings.get(clave_pausa_economica(platform))
    if isinstance(valor, str) and valor in MODOS_PAUSA_ECONOMICA:
        return valor
    return "off"


def aplica_modo_en_config(nuevo: dict, cambios: list[str], platform: str, modo: str) -> None:
    """Aplica el modo al dict de config y anota el cambio legible.

    PURA. Un modo invalido levanta `ValueError` (config_write lo envuelve en
    `SettingsInvalido`); `off` QUITA la clave, igual que `_aplica_ubicacion`."""
    validado = _valida_modo(modo)
    clave = clave_pausa_economica(platform)
    if validado == "off":
        if clave in nuevo:
            cambios.append(f"pausa economica {platform} {nuevo.pop(clave)} -> ausente")
    elif nuevo.get(clave) != validado:
        cambios.append(f"pausa economica {platform} {nuevo.get(clave)} -> {validado}")
        nuevo[clave] = validado
    if clave in nuevo:
        _valida_modo(nuevo[clave])
