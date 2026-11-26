"""Tests de la politica pura de ubicaciones (`app/optimizer/ubicacion.py`)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.optimizer.caso import EconomiaPlataforma
from app.optimizer.temporada_config import CampanaTopada
from app.optimizer.ubicacion import (
    MOTIVO_ACOS_FUERTE,
    MOTIVO_CAMPANA_EN_IMPULSO,
    MOTIVO_CAMPANA_NO_ELEGIBLE,
    MOTIVO_CAMPANA_TOPADA,
    MOTIVO_DATOS_INCOMPLETOS,
    MOTIVO_DESCUADRE,
    MOTIVO_ENFRIAMIENTO,
    MOTIVO_ESTRATEGIA_DESCONOCIDA,
    MOTIVO_GASTO_SIN_VENTA,
    MOTIVO_GASTO_SIN_VENTA_DOBLE,
    MOTIVO_PRESUPUESTO_SIN_MEDIR,
    MOTIVO_SIN_DYNAMIC_BIDDING,
    MOTIVO_SIN_EVIDENCIA,
    MOTIVO_SIN_META,
    MOTIVO_SOLO_BAJADAS,
    MOTIVO_SUBIDA,
    MOTIVO_TEMPORADA_BAJA,
    CasoCampana,
    EvidenciaUbicacion,
    MantenerUbicacion,
    MetaCampana,
    MoverUbicacion,
    caso_a_json,
    caso_desde_json,
    decide_ubicacion,
    max_en_espera_desde_settings,
    modo_efectivo,
    modo_ubicacion_desde_settings,
    solo_bajadas_desde_settings,
    techo_ubicacion,
)

_D = Decimal
_HOY = dt.date(2026, 11, 26)
_DESDE = _HOY - dt.timedelta(days=32)
_HASTA = _HOY - dt.timedelta(days=3)


def _eco(recorte="0.80", subida="0.70", concluir="36") -> EconomiaPlataforma:
    return EconomiaPlataforma(
        moneda="USD",
        equilibrio_acos_pct=None,
        gasto_para_concluir=_D(concluir),
        confianza_recorte=_D(recorte),
        confianza_subida=_D(subida),
    )


def _ev(
    ubicacion: str,
    *,
    vigente: int = 0,
    clics: int | None = 40,
    gasto: str | None = "0",
    pedidos: int | None = 0,
    venta: str | None = "0",
    inmaduros: int | None = 0,
) -> EvidenciaUbicacion:
    return EvidenciaUbicacion(
        ubicacion=ubicacion,
        ajuste_vigente_pct=vigente,
        clics=clics,
        gasto=None if gasto is None else _D(gasto),
        pedidos=pedidos,
        venta=None if venta is None else _D(venta),
        pedidos_inmaduros=inmaduros,
    )


def _tres(**por_ubicacion) -> tuple[EvidenciaUbicacion, ...]:
    base = {
        "arriba_de_busqueda": {},
        "resto_de_busqueda": {},
        "paginas_de_producto": {},
    }
    for ubicacion, datos in por_ubicacion.items():
        base[ubicacion] = datos
    return tuple(_ev(u, **d) for u, d in base.items())


def _caso(ubicaciones=None, **over) -> CasoCampana:
    ubicaciones = ubicaciones if ubicaciones is not None else _tres()
    gastos = [e.gasto for e in ubicaciones]
    total = None if any(g is None for g in gastos) else sum(gastos, _D(0))
    datos = {
        "campana_id": 1,
        "plataforma": "amazon_us",
        "config_id": 10,
        "estrategia": "LEGACY_FOR_SALES",
        "ventana_desde": _DESDE,
        "ventana_hasta": _HASTA,
        "ubicaciones": ubicaciones,
        "meta": MetaCampana(_D("18"), _D("24"), ("goal_campana",)),
        "economia": _eco(),
        "gasto_campana": total,
        "gasto_placements_total": total,
        "placements_frescos": True,
        "dias_desde_ultimo_cambio": None,
        "ajuste_en_vuelo": False,
        "elegible": True,
        "en_impulso": False,
        "solo_bajadas": False,
        "dias_topados_7d": 0,
        "topada": CampanaTopada(_D("0.90"), 7),
        "fase_temporada": "normal",
    }
    datos.update(over)
    return CasoCampana(**datos)


def test_compuertas_de_campana_en_orden():
    assert decide_ubicacion(_caso(elegible=False)).motivo == MOTIVO_CAMPANA_NO_ELEGIBLE
    assert decide_ubicacion(_caso(en_impulso=True)).motivo == MOTIVO_CAMPANA_EN_IMPULSO
    assert decide_ubicacion(_caso(estrategia=None)).motivo == MOTIVO_SIN_DYNAMIC_BIDDING
    assert decide_ubicacion(_caso(estrategia="OTRA")).motivo == MOTIVO_ESTRATEGIA_DESCONOCIDA
    assert decide_ubicacion(_caso(ajuste_en_vuelo=True)).motivo == "ajuste_en_vuelo"
    assert decide_ubicacion(_caso(dias_desde_ultimo_cambio=13)).motivo == MOTIVO_ENFRIAMIENTO
    assert decide_ubicacion(_caso(dias_desde_ultimo_cambio=14)).motivo == MOTIVO_SIN_EVIDENCIA
    corta = _caso(ventana_desde=_HOY - dt.timedelta(days=10))
    assert decide_ubicacion(corta).motivo == MOTIVO_DATOS_INCOMPLETOS
    assert decide_ubicacion(_caso(placements_frescos=False)).motivo == MOTIVO_DATOS_INCOMPLETOS
    assert (
        decide_ubicacion(_caso(gasto_campana=_D("1"), gasto_placements_total=_D("0"))).motivo
        == MOTIVO_DESCUADRE
    )
    assert decide_ubicacion(_caso(meta=None)).motivo == MOTIVO_SIN_META


def test_recorte_por_acos_usa_meta_laxa_y_piso_cero():
    sin_recorte = _caso(
        _tres(arriba_de_busqueda={"vigente": 10, "pedidos": 40, "venta": "100", "gasto": "22"}),
        meta=MetaCampana(_D("18"), _D("24"), ("goal",)),
    )
    veredicto = decide_ubicacion(sin_recorte)
    assert isinstance(veredicto, MantenerUbicacion)
    assert veredicto.motivo == MOTIVO_SIN_EVIDENCIA

    fuerte = _caso(
        _tres(arriba_de_busqueda={"vigente": 10, "pedidos": 40, "venta": "100", "gasto": "38"}),
        meta=MetaCampana(_D("18"), _D("24"), ("goal",)),
    )
    veredicto = decide_ubicacion(fuerte)
    assert veredicto == MoverUbicacion(
        ubicacion="arriba_de_busqueda", de_pct=10, a_pct=0, motivo=MOTIVO_ACOS_FUERTE
    )


def test_recorte_gasto_sin_venta_respeta_venta_reciente_e_inmaduros_none():
    suave = _caso(
        _tres(resto_de_busqueda={"vigente": 10, "pedidos": 0, "venta": "0", "gasto": "40"}),
        economia=_eco(concluir="36"),
    )
    assert decide_ubicacion(suave) == MoverUbicacion(
        ubicacion="resto_de_busqueda", de_pct=10, a_pct=0, motivo=MOTIVO_GASTO_SIN_VENTA
    )

    doble = _caso(
        _tres(resto_de_busqueda={"vigente": 30, "pedidos": 0, "venta": "0", "gasto": "80"}),
        economia=_eco(concluir="36"),
    )
    assert decide_ubicacion(doble) == MoverUbicacion(
        ubicacion="resto_de_busqueda", de_pct=30, a_pct=5, motivo=MOTIVO_GASTO_SIN_VENTA_DOBLE
    )

    reciente = _caso(
        _tres(
            resto_de_busqueda={
                "vigente": 10,
                "pedidos": 0,
                "venta": "0",
                "gasto": "80",
                "inmaduros": 1,
            }
        )
    )
    assert decide_ubicacion(reciente).motivo == MOTIVO_SIN_EVIDENCIA

    sin_medir = _caso(
        _tres(
            resto_de_busqueda={
                "vigente": 10,
                "pedidos": 0,
                "venta": "0",
                "gasto": "80",
                "inmaduros": None,
            }
        )
    )
    assert decide_ubicacion(sin_medir).motivo == MOTIVO_SIN_EVIDENCIA


def test_recorte_gana_a_subida_en_misma_campana():
    caso = _caso(
        _tres(
            paginas_de_producto={
                "vigente": 40,
                "pedidos": 5,
                "venta": "100",
                "gasto": "118",
            },
            arriba_de_busqueda={"vigente": 0, "pedidos": 5, "venta": "100", "gasto": "5"},
            resto_de_busqueda={"vigente": 0, "clics": 20, "venta": "0", "gasto": "0"},
        )
    )
    assert decide_ubicacion(caso) == MoverUbicacion(
        ubicacion="paginas_de_producto",
        de_pct=40,
        a_pct=15,
        motivo=MOTIVO_ACOS_FUERTE,
    )


def test_subida_exige_3_pedidos_20_clics_ventaja_relativa_y_holgura_presupuesto():
    pocos = _caso(
        _tres(arriba_de_busqueda={"vigente": 0, "pedidos": 1, "venta": "100", "gasto": "10"})
    )
    assert decide_ubicacion(pocos).motivo == MOTIVO_SIN_EVIDENCIA

    sin_ventaja = _caso(
        _tres(
            arriba_de_busqueda={"vigente": 0, "pedidos": 4, "venta": "100", "gasto": "5"},
            resto_de_busqueda={"vigente": 0, "pedidos": 4, "venta": "100", "gasto": "5"},
            paginas_de_producto={"vigente": 0, "pedidos": 4, "venta": "100", "gasto": "5"},
        )
    )
    assert decide_ubicacion(sin_ventaja).motivo == MOTIVO_SIN_EVIDENCIA

    gana = _caso(
        _tres(
            arriba_de_busqueda={"vigente": 0, "pedidos": 4, "venta": "100", "gasto": "5"},
            resto_de_busqueda={"vigente": 0, "clics": 20, "venta": "10", "gasto": "2"},
            paginas_de_producto={"vigente": 0, "clics": 20, "venta": "10", "gasto": "2"},
        )
    )
    assert decide_ubicacion(gana) == MoverUbicacion(
        ubicacion="arriba_de_busqueda", de_pct=0, a_pct=15, motivo=MOTIVO_SUBIDA
    )

    base = _principal()
    assert (
        decide_ubicacion(_caso(base.ubicaciones, fase_temporada="baja")).motivo
        == MOTIVO_TEMPORADA_BAJA
    )
    assert (
        decide_ubicacion(_caso(base.ubicaciones, solo_bajadas=True)).motivo == MOTIVO_SOLO_BAJADAS
    )
    assert (
        decide_ubicacion(_caso(base.ubicaciones, dias_topados_7d=7)).motivo == MOTIVO_CAMPANA_TOPADA
    )
    assert (
        decide_ubicacion(_caso(base.ubicaciones, dias_topados_7d=None)).motivo
        == MOTIVO_PRESUPUESTO_SIN_MEDIR
    )


def _principal() -> CasoCampana:
    return _caso(
        _tres(
            arriba_de_busqueda={"vigente": 0, "pedidos": 4, "venta": "100", "gasto": "5"},
            resto_de_busqueda={"vigente": 0, "clics": 20, "venta": "10", "gasto": "2"},
            paginas_de_producto={"vigente": 0, "clics": 20, "venta": "10", "gasto": "2"},
        )
    )


def test_techo_por_estrategia_no_fuerza_bajada_de_vigente_alto():
    auto = _caso(
        _tres(
            arriba_de_busqueda={"vigente": 45, "pedidos": 4, "venta": "100", "gasto": "5"},
            resto_de_busqueda={"vigente": 0, "clics": 20, "venta": "10", "gasto": "2"},
            paginas_de_producto={"vigente": 40, "clics": 20, "venta": "10", "gasto": "2"},
        ),
        estrategia="AUTO_FOR_SALES",
    )
    assert decide_ubicacion(auto) == MoverUbicacion(
        ubicacion="arriba_de_busqueda", de_pct=45, a_pct=50, motivo=MOTIVO_SUBIDA
    )

    sin_evidencia = _caso(
        _tres(
            paginas_de_producto={"vigente": 40, "pedidos": 4, "venta": "100", "gasto": "20"},
            arriba_de_busqueda={"vigente": 0, "clics": None, "venta": None, "gasto": None},
            resto_de_busqueda={"vigente": 0, "clics": None, "venta": None, "gasto": None},
        ),
        estrategia="AUTO_FOR_SALES",
        gasto_campana=_D("20"),
        gasto_placements_total=_D("20"),
    )
    veredicto = decide_ubicacion(sin_evidencia)
    assert veredicto.motivo == MOTIVO_SIN_EVIDENCIA


def test_replay_roundtrip_json_determinista():
    caso = _principal()
    doc = caso_a_json(caso)
    reconstruido = caso_desde_json(doc)
    assert reconstruido == caso
    assert caso_a_json(reconstruido) == doc
    assert decide_ubicacion(reconstruido) == decide_ubicacion(caso)


def test_settings_fail_closed_y_defaults():
    assert modo_ubicacion_desde_settings({}, "amazon_mx") == "off"
    assert modo_ubicacion_desde_settings({"ads_ubicacion_amazon_mx": "shadow"}, "amazon_mx") == (
        "shadow"
    )
    with pytest.raises(ValueError):
        modo_ubicacion_desde_settings({"ads_ubicacion_amazon_mx": "medio"}, "amazon_mx")

    assert solo_bajadas_desde_settings({}, "amazon_us") is True
    assert solo_bajadas_desde_settings({}, "amazon_mx") is False
    assert (
        solo_bajadas_desde_settings({"ads_ubicacion_solo_bajadas_amazon_us": "false"}, "amazon_us")
        is False
    )
    with pytest.raises(ValueError):
        solo_bajadas_desde_settings({"ads_ubicacion_solo_bajadas_amazon_us": "si"}, "amazon_us")

    assert max_en_espera_desde_settings({}, "amazon_mx") == 6
    assert max_en_espera_desde_settings({}, "amazon_us") == 2
    assert (
        max_en_espera_desde_settings({"ads_ubicacion_max_en_espera_amazon_mx": "3"}, "amazon_mx")
        == 3
    )
    with pytest.raises(ValueError):
        max_en_espera_desde_settings({"ads_ubicacion_max_en_espera_amazon_mx": -1}, "amazon_mx")

    assert techo_ubicacion("AUTO_FOR_SALES", "arriba_de_busqueda") == 50
    assert techo_ubicacion("MANUAL", "arriba_de_busqueda") == 100
    with pytest.raises(ValueError):
        techo_ubicacion("OTRA", "arriba_de_busqueda")

    assert modo_efectivo("live", "shadow", "live") == "shadow"
    assert modo_efectivo("off", "live", "shadow") == "off"
    with pytest.raises(ValueError):
        modo_efectivo("live", "raro")
