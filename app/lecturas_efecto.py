"""Efecto y episodio economico del ultimo cambio de bid (BIDS 02 M.2 / ORBIT 07 M5).

Extraido de `app/lecturas_caso.py` (Ola D1) manteniendo el invariante SOLO SELECT
y sin dependencias de `app.apply` ni `app.ads`. Centraliza ademas el constructor
declarativo `_cols_ventana` con parametros nombrados compartido por `lecturas_caso`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING, Any, Protocol

from app.optimizer.caso import (
    DIAS_ECONOMIA_CAMBIO,
    DIAS_MADUREZ_ECONOMIA,
    DIAS_VIGENCIA_ECONOMIA_CAMBIO,
    MOTIVO_ECONOMIA_CAMBIO_EN_VENTANA,
    MOTIVO_ECONOMIA_CAMBIO_POSTERIOR,
    MOTIVO_ECONOMIA_COBERTURA_INCOMPLETA,
    MOTIVO_ECONOMIA_EVIDENCIA_VENCIDA,
    MOTIVO_ECONOMIA_POST_INMADURO,
    PRECISION,
    CambioBid,
    EfectoCambio,
    EpisodioEconomico,
    VentanaEconomica,
)
from app.optimizer.goals import COOLDOWN
from app.optimizer.windows import DIAS_FRESCURA_BIDS, AgregadoMetricas

if TYPE_CHECKING:
    from app.lecturas_caso import LecturasPlataforma

DIAS_EFECTO = COOLDOWN.days  # pre7/post7 de EfectoCambio
DIAS_INMADUROS_HASTA = 1
DIAS_PRECIO_PREVIO = 30  # tramo de precio anterior al cambio


class Conexion(Protocol):
    def execute(self, sql: str, params: tuple | Mapping[str, Any] = ()) -> Any: ...


def _rango_ventana(prefijo: str, *, con_cerca: bool = False) -> str:
    inicio = f"greatest(%({prefijo}_ini)s, h.cerca)" if con_cerca else f"%({prefijo}_ini)s"
    return f"FILTER (WHERE v.metric_date BETWEEN {inicio} AND %({prefijo}_fin)s)"


def _col_metrica_ventana(
    prefijo: str, columna: str, *, es_bigint: bool = False, con_cerca: bool = False
) -> str:
    filtro = _rango_ventana(prefijo, con_cerca=con_cerca)
    cast = "::bigint" if es_bigint else ""
    return (
        f"CASE WHEN bool_and(v.{columna} IS NOT NULL AND v.{columna} >= 0) {filtro} "
        f"THEN sum(v.{columna}) {filtro}{cast} END"
    )


def _cols_ventana(
    prefijo: str, columnas: tuple[tuple[str, bool], ...], *, con_cerca: bool = False
) -> str:
    partes = [f"count(*) {_rango_ventana(prefijo, con_cerca=con_cerca)}"]
    partes.extend(
        _col_metrica_ventana(prefijo, col, es_bigint=es_bi, con_cerca=con_cerca)
        for col, es_bi in columnas
    )
    return ",\n       ".join(partes)


_COLS_ECON: tuple[tuple[str, bool], ...] = (
    ("cost", False),
    ("ad_revenue", False),
    ("revenue_same_sku", False),
    ("orders", True),
    ("clicks", True),
)

_SELECT_EFECTO_BASE = ",\n       ".join(
    (
        _cols_ventana("pre7", (("impressions", True), ("clicks", True))),
        _cols_ventana("post", (("impressions", True),)),
        _cols_ventana("postp", (("clicks", True), ("cost", False))),
        _cols_ventana("prep", (("clicks", True), ("cost", False))),
        _cols_ventana("ven", (("orders", True),)),
    )
)

_FROM_WHERE_EFECTO = (
    "\n  FROM {fuente}\n"
    " WHERE v.ad_entity_id = %(hoja_id)s\n"
    "   AND v.metric_currency = %(moneda)s\n"
    "   AND v.metric_date BETWEEN %(desde)s AND %(hasta)s\n"
)

_SQL_EFECTO = "SELECT " + _SELECT_EFECTO_BASE + _FROM_WHERE_EFECTO

_SQL_EFECTO_ECONOMIA = (
    "SELECT "
    + _SELECT_EFECTO_BASE
    + ",\n       "
    + _cols_ventana("epre", _COLS_ECON)
    + ",\n       "
    + _cols_ventana("epost", _COLS_ECON)
    + _FROM_WHERE_EFECTO
)


def _fuente(visto_el: dt.datetime | None) -> tuple[str, tuple]:
    """Fragmento FROM posicional y sus parametros: lo ultimo, o lo visible entonces."""
    if visto_el is None:
        return "v_metric_latest v", ()
    return "metrics_as_of(%s) v", (visto_el,)


def _fuente_nombrada(visto_el: dt.datetime | None) -> tuple[str, dict[str, Any]]:
    """Fragmento FROM con parametro nombrado para consultas `_cols_ventana`."""
    if visto_el is None:
        return "v_metric_latest v", {}
    return "metrics_as_of(%(visto_el)s) v", {"visto_el": visto_el}


def _completo(desde: dt.date, hasta: dt.date, dias: frozenset[dt.date]) -> bool:
    """Todo dia del rango tuvo ingesta (un rango vacio vale completo)."""
    dia = desde
    while dia <= hasta:
        if dia not in dias:
            return False
        dia += dt.timedelta(days=1)
    return True


def _mide(
    filas: int,
    valor: Any,
    desde: dt.date,
    hasta: dt.date,
    dias: frozenset[dt.date],
    cero: Any,
) -> Any:
    """Una metrica del efecto: hueco = None; rango vacio = cero tipado;
    con filas = suma (veneno intacto)."""
    if not _completo(desde, hasta, dias):
        return None
    if filas == 0:
        return cero
    return valor


@dataclass(frozen=True)
class _EfectoLeido:
    """La consulta unica del efecto devuelve tambien las dos ventanas
    economicas (M5) para congelarlas en el episodio sin otra consulta.
    Apagada la regla, las dos ventanas son None y se ejecuta el SQL historico."""

    efecto: EfectoCambio
    pre_economico: VentanaEconomica | None
    post_economico: VentanaEconomica | None


def _lee_efecto(
    conn: Conexion,
    lect: LecturasPlataforma,
    hoja_id: int,
    cambios: tuple[CambioBid, ...],
    cortes: AgregadoMetricas | None,
) -> _EfectoLeido:
    """Efecto del ultimo cambio de la hoja (UNA consulta). Los rangos son
    los del prototipo (exporta M.1); `dias_post` cuenta dias con ingesta.
    CLARIDAD 01 K: `vendia` se mide en la ventana de CORTES de la pausa
    (windows.ventana_cortes), no en los 90 dias previos al cambio: una venta
    vieja no regresa la puja de una keyword sin pedidos. Sin cortes, None.
    Con la regla encendida, la MISMA consulta trae las ventanas economicas de
    SIETE fechas completas sin el dia del cambio: [x-7, x-1] y [x+1, x+7].
    Apagada, se ejecuta el SQL historico de 13 columnas."""
    uno = dt.timedelta(days=1)
    x = cambios[-1].fecha
    d = lect._d
    dias = lect._dias_con_ingesta
    ayer = d - dt.timedelta(days=DIAS_INMADUROS_HASTA)
    dias_post = sum(1 for n in range((ayer - x).days) if x + uno * (n + 1) in dias)
    k = min(DIAS_EFECTO, dias_post)
    fresco = d - dt.timedelta(days=DIAS_FRESCURA_BIDS)
    pre7 = (x - dt.timedelta(days=DIAS_EFECTO), x - uno)
    post = (x + uno, x + uno * k)
    postp = (x + uno, fresco)
    previo = (
        cambios[-2].fecha + uno if len(cambios) > 1 else x - dt.timedelta(days=DIAS_PRECIO_PREVIO)
    )
    prep = (max(previo, x - dt.timedelta(days=DIAS_PRECIO_PREVIO)), x - uno)
    # Sin cortes, un rango vacio: el FILTER de pedidos no cuenta filas.
    ven = (cortes.window_start, cortes.window_end) if cortes is not None else (x, x - uno)
    econ_pre = (x - dt.timedelta(days=DIAS_ECONOMIA_CAMBIO), x - uno)
    econ_post = (x + uno, x + dt.timedelta(days=DIAS_ECONOMIA_CAMBIO))
    fuente, pv = _fuente_nombrada(lect._visto_el)
    params: dict[str, Any] = {
        **pv,
        "pre7_ini": pre7[0],
        "pre7_fin": pre7[1],
        "post_ini": post[0],
        "post_fin": post[1],
        "postp_ini": postp[0],
        "postp_fin": postp[1],
        "prep_ini": prep[0],
        "prep_fin": prep[1],
        "ven_ini": ven[0],
        "ven_fin": ven[1],
        "hoja_id": hoja_id,
        "moneda": lect.economia.moneda,
        "desde": min(pre7[0], prep[0], econ_pre[0], ven[0]),
    }
    if lect._version_economia is not None:
        sql = _SQL_EFECTO_ECONOMIA
        params.update(
            epre_ini=econ_pre[0],
            epre_fin=econ_pre[1],
            epost_ini=econ_post[0],
            epost_fin=econ_post[1],
            hasta=max(fresco, econ_post[1]),
        )
    else:
        sql = _SQL_EFECTO
        params["hasta"] = max(fresco, post[1])
    fila = conn.execute(sql.format(fuente=fuente), params).fetchone()
    (
        n_pre7,
        i_pre7,
        c_pre7,
        n_post,
        i_post,
        n_postp,
        c_postp,
        g_postp,
        n_prep,
        c_prep,
        g_prep,
        n_ven,
        p_ven,
    ) = fila[:13]
    vendidos = None if cortes is None else _mide(n_ven, p_ven, ven[0], ven[1], dias, 0)
    efecto = EfectoCambio(
        dias_post=dias_post,
        impresiones_pre7=_mide(n_pre7, i_pre7, pre7[0], pre7[1], dias, 0),
        clics_pre7=_mide(n_pre7, c_pre7, pre7[0], pre7[1], dias, 0),
        impresiones_post=_mide(n_post, i_post, post[0], post[1], dias, 0),
        dias_post_trafico=k,
        clics_post=_mide(n_postp, c_postp, postp[0], postp[1], dias, 0),
        gasto_post=_mide(n_postp, g_postp, postp[0], postp[1], dias, Decimal(0)),
        clics_pre=_mide(n_prep, c_prep, prep[0], prep[1], dias, 0),
        gasto_pre=_mide(n_prep, g_prep, prep[0], prep[1], dias, Decimal(0)),
        vendia=None if vendidos is None else vendidos >= 1,
    )
    if lect._version_economia is None:
        return _EfectoLeido(efecto=efecto, pre_economico=None, post_economico=None)
    (
        n_econ_pre,
        g_econ_pre,
        r_econ_pre,
        rs_econ_pre,
        o_econ_pre,
        c_econ_pre,
        n_econ_post,
        g_econ_post,
        r_econ_post,
        rs_econ_post,
        o_econ_post,
        c_econ_post,
    ) = fila[13:]
    return _EfectoLeido(
        efecto=efecto,
        pre_economico=_ventana_economica(
            n_econ_pre,
            econ_pre,
            dias,
            gasto=g_econ_pre,
            venta=r_econ_pre,
            venta_mismo_sku=rs_econ_pre,
            pedidos=o_econ_pre,
            clics=c_econ_pre,
        ),
        post_economico=_ventana_economica(
            n_econ_post,
            econ_post,
            dias,
            gasto=g_econ_post,
            venta=r_econ_post,
            venta_mismo_sku=rs_econ_post,
            pedidos=o_econ_post,
            clics=c_econ_post,
        ),
    )


def _ventana_economica(
    filas: int,
    rango: tuple[dt.date, dt.date],
    dias: frozenset[dt.date],
    *,
    gasto: Any,
    venta: Any,
    venta_mismo_sku: Any,
    pedidos: Any,
    clics: Any,
) -> VentanaEconomica:
    desde, hasta = rango
    return VentanaEconomica(
        desde=desde,
        hasta=hasta,
        gasto=_mide(filas, gasto, desde, hasta, dias, Decimal(0)),
        venta=_mide(filas, venta, desde, hasta, dias, Decimal(0)),
        venta_mismo_sku=_mide(filas, venta_mismo_sku, desde, hasta, dias, Decimal(0)),
        pedidos=_mide(filas, pedidos, desde, hasta, dias, 0),
        clics=_mide(filas, clics, desde, hasta, dias, 0),
    )


def _episodio_economico(
    lect: LecturasPlataforma,
    *,
    cambios: tuple[CambioBid, ...],
    pre: VentanaEconomica,
    post: VentanaEconomica,
    margen_pct: Decimal | None,
    campana_id: int | None,
    ad_group_id: int | None,
) -> EpisodioEconomico | None:
    """Episodio del ULTIMO cambio de bid real (no un ajuste de campana).

    `estado` separa madurez/comparabilidad de `aplicable`: un cambio de puja,
    presupuesto, placement, precio o alcance posterior deja el episodio como
    historia. Un evento DENTRO de [x-7, x+7] contaminaria la comparacion y la
    marca incomparable, aunque el episodio siga sin cambios posteriores. El
    episodio tambien caduca a los 31 dias del cambio (14 despues de madurar):
    mas viejo no gobierna la repeticion y R16 decide como antes.
    """
    reales = [
        i
        for i, cambio in enumerate(cambios)
        if cambio.bid_antes != cambio.bid_despues and cambio.origen != "ajuste_de_campana"
    ]
    if not reales:
        return None
    indice = reales[-1]
    cambio = cambios[indice]
    x = cambio.fecha
    d = lect._d
    version = lect._version_economia
    assert version is not None
    eventos = [otro.fecha for i, otro in enumerate(cambios) if i != indice]
    if campana_id is not None:
        eventos.extend(lect._eventos_campana.get(campana_id, ()))
    if ad_group_id is not None:
        eventos.extend(lect._eventos_grupo.get(ad_group_id, ()))
    desde = x - dt.timedelta(days=DIAS_ECONOMIA_CAMBIO)
    hasta = x + dt.timedelta(days=DIAS_ECONOMIA_CAMBIO)
    en_ventana = any(desde <= evento <= hasta for evento in eventos)
    posteriores = any(evento > x for evento in eventos)
    vencida = d >= x + dt.timedelta(days=DIAS_VIGENCIA_ECONOMIA_CAMBIO)
    aplicable = not posteriores and not vencida
    if aplicable:
        motivo_aplicabilidad = None
    elif vencida:
        motivo_aplicabilidad = MOTIVO_ECONOMIA_EVIDENCIA_VENCIDA
    else:
        motivo_aplicabilidad = MOTIVO_ECONOMIA_CAMBIO_POSTERIOR
    madura = hasta <= d - dt.timedelta(days=DIAS_MADUREZ_ECONOMIA)
    if not madura:
        estado = "inmaduro"
        motivo = MOTIVO_ECONOMIA_POST_INMADURO
    elif en_ventana:
        estado = "incomparable"
        motivo = MOTIVO_ECONOMIA_CAMBIO_EN_VENTANA
    elif not _ventanas_medidas(pre, post):
        estado = "incomparable"
        motivo = MOTIVO_ECONOMIA_COBERTURA_INCOMPLETA
    else:
        estado = "maduro"
        motivo = None
    return EpisodioEconomico(
        version=version,
        fecha_cambio=x,
        moneda=lect.economia.moneda,
        pre=pre,
        post=post,
        estado=estado,
        motivo=motivo,
        aplicable=aplicable,
        motivo_aplicabilidad=motivo_aplicabilidad,
        margen_pct=margen_pct,
        contribucion_pre=_contribucion(pre, margen_pct),
        contribucion_post=_contribucion(post, margen_pct),
    )


def _ventanas_medidas(pre: VentanaEconomica, post: VentanaEconomica) -> bool:
    """Las cuatro metricas que la regla necesita, conocidas en ambas ventanas.

    `venta_mismo_sku` se conserva como evidencia pero no condiciona: el
    margen del producto ya resolvio su compatibilidad y la regla usa la venta
    total del reporte de ads.
    """
    campos = ("gasto", "venta", "pedidos", "clics")
    return all(getattr(pre, campo) is not None for campo in campos) and all(
        getattr(post, campo) is not None for campo in campos
    )


def _contribucion(ventana: VentanaEconomica, margen_pct: Decimal | None) -> Decimal | None:
    if margen_pct is None or ventana.venta is None or ventana.gasto is None:
        return None
    with localcontext(prec=PRECISION):
        return ventana.venta * margen_pct / Decimal(100) - ventana.gasto
