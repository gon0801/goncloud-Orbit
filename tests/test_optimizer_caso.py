"""Tests de app/optimizer/caso.py (BIDS 02 M.1): estructuras, derivados y freeze JSON.

Tambien aloja las fabricas de casos que usa test_optimizer_politica.py.
Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
import typing
from decimal import Decimal
from pathlib import Path

import pytest

from app.optimizer.caso import (
    ESQUEMA_PESOS,
    BidVigente,
    CambioBid,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    EvidenciaNivel,
    InsumosPausa,
    OrigenCambio,
    PrecioVentana,
    Tramo,
    Trayectoria,
)
from app.optimizer.windows import AgregadoMetricas

RAIZ = Path(__file__).resolve().parents[1]
DORADA = RAIZ / "tests" / "fixtures" / "politica_dorada_a1.json"
HOJA_2963 = RAIZ / "tests" / "fixtures" / "politica_2963_a2.json"


# ---------------------------------------------------------------------------
# Fabricas compartidas con test_optimizer_politica.py
# ---------------------------------------------------------------------------


def tramo(clics=0, pedidos=0, venta="0", gasto="0", impresiones=0):
    return Tramo(
        clics=clics,
        pedidos=pedidos,
        venta=Decimal(venta) if venta is not None else None,
        gasto=Decimal(gasto) if gasto is not None else None,
        impresiones=impresiones,
    )


def nivel(reciente=None, antiguo=None, esquema=ESQUEMA_PESOS):
    return EvidenciaNivel(
        reciente=reciente if reciente is not None else tramo(),
        antiguo=antiguo if antiguo is not None else tramo(),
        esquema=esquema,
    )


def economia_mx(
    target="20.72",
    equilibrio="41.43",
    concluir="350",
    conf_rec="0.80",
    conf_sub="0.70",
):
    return Economia(
        plataforma=EconomiaPlataforma(
            moneda="MXN",
            equilibrio_acos_pct=Decimal(equilibrio) if equilibrio is not None else None,
            gasto_para_concluir=Decimal(concluir),
            confianza_recorte=Decimal(conf_rec),
            confianza_subida=Decimal(conf_sub),
        ),
        target_acos_pct=Decimal(target),
    )


def economia_us(target="28.34", equilibrio="31.49", concluir="36"):
    return Economia(
        plataforma=EconomiaPlataforma(
            moneda="USD",
            equilibrio_acos_pct=Decimal(equilibrio),
            gasto_para_concluir=Decimal(concluir),
            confianza_recorte=Decimal("0.80"),
            confianza_subida=Decimal("0.70"),
        ),
        target_acos_pct=Decimal(target),
    )


def cambio(fecha=dt.date(2026, 9, 29), antes="12.00", despues="10.00", origen="motor"):
    return CambioBid(
        fecha=fecha,
        bid_antes=Decimal(antes),
        bid_despues=Decimal(despues),
        origen=origen,
    )


def efecto(
    dias_post=10,
    pre_impr=5000,
    pre_clics=50,
    post_impr=100,
    dias_trafico=7,
    clics_post=25,
    gasto_post="100",
    clics_pre=40,
    gasto_pre="120",
    vendia=True,
):
    return EfectoCambio(
        dias_post=dias_post,
        impresiones_pre7=pre_impr,
        clics_pre7=pre_clics,
        impresiones_post=post_impr,
        dias_post_trafico=dias_trafico,
        clics_post=clics_post,
        gasto_post=Decimal(gasto_post) if gasto_post is not None else None,
        clics_pre=clics_pre,
        gasto_pre=Decimal(gasto_pre) if gasto_pre is not None else None,
        vendia=vendia,
    )


def trayectoria(cambios=(), efecto_ultimo=None):
    return Trayectoria(cambios=tuple(cambios), efecto=efecto_ultimo)


def cortes(
    orders=0,
    clicks=120,
    cost="500",
    revenue="0",
    moneda="MXN",
    desde=dt.date(2026, 9, 20),
    dias=10,
):
    fechas = tuple(desde + dt.timedelta(days=i) for i in range(dias))
    return AgregadoMetricas(
        window_start=fechas[0],
        window_end=fechas[-1],
        fechas=fechas,
        metric_currency=moneda,
        cost=Decimal(cost),
        ad_revenue=Decimal(revenue),
        revenue_same_sku=Decimal("0"),
        impressions=5000,
        clicks=clicks,
        orders=orders,
        observed_at_max=dt.datetime(2026, 9, 30, 8, 40, tzinfo=dt.UTC),
    )


def fabrica_caso(**ajas):
    base = dict(
        plataforma="amazon_mx",
        hoja_id=1001,
        ad_group_id=2001,
        bid=BidVigente(Decimal("10.00"), "MXN", Decimal("0.50"), Decimal("50.00")),
        economia=economia_mx(),
        propia=nivel(),
        pedidos_inmaduros=0,
        grupo=None,
        cuenta=None,
        precio=PrecioVentana(Decimal("0"), 0),
        trayectoria=trayectoria(),
        pausa=InsumosPausa(None, 100, Decimal("350"), None, None),
        ventana_desde=dt.date(2026, 7, 11),
        ventana_hasta=dt.date(2026, 9, 29),
        observado_al=None,
    )
    base.update(ajas)
    return CasoHoja(**base)


def caso_rico():
    """Un caso con todo lleno: grupo, cuenta, precio, trayectoria y cortes."""
    return fabrica_caso(
        hoja_id=2963,
        propia=nivel(
            tramo(clics=200, pedidos=2, venta="1000", gasto="800", impresiones=9000),
            tramo(clics=100, pedidos=1, venta="500", gasto="400", impresiones=4500),
        ),
        pedidos_inmaduros=1,
        grupo=nivel(tramo(clics=300, pedidos=4, venta="2000", gasto="900", impresiones=12000)),
        cuenta=nivel(
            tramo(
                clics=10000,
                pedidos=100,
                venta="100000",
                gasto="9000",
                impresiones=400000,
            )
        ),
        precio=PrecioVentana(Decimal("300"), 60),
        trayectoria=trayectoria([cambio(dt.date(2026, 9, 9), "12.00", "10.00", "motor")], efecto()),
        pausa=InsumosPausa(cortes(), 100, Decimal("350"), None, None),
        observado_al=dt.datetime(2026, 10, 1, 8, 40, tzinfo=dt.UTC),
    )


# ---------------------------------------------------------------------------
# Freeze: roundtrip exacto
# ---------------------------------------------------------------------------


def test_roundtrip_caso_minimo():
    caso = fabrica_caso()
    assert CasoHoja.desde_json(caso.como_json()) == caso


def test_roundtrip_caso_rico():
    caso = caso_rico()
    assert CasoHoja.desde_json(caso.como_json()) == caso


def test_roundtrip_caso_raro():
    caso = fabrica_caso(
        plataforma="amazon_us",
        hoja_id=4924,
        ad_group_id=None,
        bid=BidVigente(None, None, Decimal("0.10"), Decimal("2.00")),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda="USD",
                equilibrio_acos_pct=None,
                gasto_para_concluir=Decimal("36"),
                confianza_recorte=Decimal("0.80"),
                confianza_subida=Decimal("0.70"),
            ),
            target_acos_pct=Decimal("28.34"),
        ),
        propia=nivel(
            tramo(clics=None, pedidos=0, venta=None, gasto="12.5", impresiones=None),
            tramo(),
        ),
        pedidos_inmaduros=None,
        precio=PrecioVentana(None, None),
        trayectoria=trayectoria(
            [
                cambio(dt.date(2026, 9, 1), "0.60", "0.40", "motor"),
                cambio(dt.date(2026, 9, 20), "0.40", "0.40", "ajuste_de_campana"),
            ],
            efecto(vendia=None, gasto_post=None, gasto_pre=None),
        ),
        pausa=InsumosPausa(
            AgregadoMetricas(
                window_start=dt.date(2026, 9, 20),
                window_end=dt.date(2026, 9, 29),
                fechas=(),
                metric_currency=None,
                cost=None,
                ad_revenue=None,
                revenue_same_sku=None,
                impressions=None,
                clicks=None,
                orders=None,
                observed_at_max=None,
            ),
            0,
            Decimal("0"),
            Decimal("1.5"),
            "economic_pause_v1",
        ),
        observado_al=dt.datetime(2026, 10, 1, 8, 40),
    )
    assert CasoHoja.desde_json(caso.como_json()) == caso


def test_roundtrip_fixtures():
    import json

    for ruta in (DORADA, HOJA_2963):
        bruto = json.loads(ruta.read_text(encoding="utf-8"))
        assert bruto["casos"], f"fixture vacio: {ruta.name}"
        for entrada in bruto["casos"]:
            caso = CasoHoja.desde_json(entrada["caso"])
            assert CasoHoja.desde_json(caso.como_json()) == caso


def test_json_formato_decimales_fechas_y_nones():
    bruto = caso_rico().como_json()
    assert bruto["bid"]["valor"] == "10.00"
    assert bruto["ventana_desde"] == "2026-07-11"
    assert bruto["observado_al"] == "2026-10-01T08:40:00+00:00"
    minimo = fabrica_caso().como_json()
    assert minimo["grupo"] is None
    assert minimo["pausa"]["cortes"] is None
    assert minimo["economia"]["plataforma"]["moneda"] == "MXN"


# ---------------------------------------------------------------------------
# Frontera: desde_json rechaza todo lo ajeno con ValueError
# ---------------------------------------------------------------------------


def test_desde_json_rechaza_clave_faltante():
    bruto = caso_rico().como_json()
    del bruto["hoja_id"]
    with pytest.raises(ValueError):
        CasoHoja.desde_json(bruto)
    bruto = caso_rico().como_json()
    del bruto["propia"]["reciente"]["gasto"]
    with pytest.raises(ValueError):
        CasoHoja.desde_json(bruto)
    with pytest.raises(ValueError):
        CasoHoja.desde_json({"plataforma": "amazon_mx"})


def test_desde_json_rechaza_tipo_ajeno():
    malos = []
    bruto = caso_rico().como_json()
    bruto["hoja_id"] = "1001"
    malos.append(bruto)
    bruto = caso_rico().como_json()
    bruto["bid"]["valor"] = 10.0
    malos.append(bruto)
    bruto = caso_rico().como_json()
    bruto["ventana_desde"] = "29-09-2026"
    malos.append(bruto)
    bruto = caso_rico().como_json()
    bruto["trayectoria"]["cambios"][0]["origen"] = "dueno"
    malos.append(bruto)
    bruto = caso_rico().como_json()
    bruto["plataforma"] = "mercado_libre"
    malos.append(bruto)
    bruto = caso_rico().como_json()
    bruto["trayectoria"]["efecto"]["vendia"] = 1
    malos.append(bruto)
    bruto = caso_rico().como_json()
    bruto["propia"]["reciente"]["pedidos"] = 2.0
    malos.append(bruto)
    for malo in malos:
        with pytest.raises(ValueError):
            CasoHoja.desde_json(malo)
    with pytest.raises(ValueError):
        CasoHoja.desde_json([])


def test_desde_json_rechaza_clave_extra():
    bruto = caso_rico().como_json()
    bruto["futuro"] = 1
    with pytest.raises(ValueError):
        CasoHoja.desde_json(bruto)


# ---------------------------------------------------------------------------
# Derivados de EvidenciaNivel
# ---------------------------------------------------------------------------


def test_derivados_ponderan_antiguo_a_la_mitad():
    ev = nivel(
        tramo(clics=200, pedidos=2, venta="1000", gasto="800"),
        tramo(clics=100, pedidos=1, venta="500", gasto="400"),
    )
    assert ev.pedidos_pesados() == Decimal("2.5")
    assert ev.clics_pesados() == Decimal("250")
    assert ev.venta_pesada() == Decimal("1250")
    assert ev.gasto_pesado() == Decimal("1000")
    assert ev.pedidos_crudos() == 3
    assert ev.gasto_crudo() == Decimal("1200")
    assert ev.clics_crudos() == 300
    assert ev.venta_cruda() == Decimal("1500")


def test_derivados_propagan_none_de_cualquier_tramo():
    lleno = tramo(clics=200, pedidos=2, venta="1000", gasto="800", impresiones=9)
    assert nivel(tramo(pedidos=None), lleno).pedidos_pesados() is None
    assert nivel(lleno, tramo(pedidos=None)).pedidos_crudos() is None
    assert nivel(tramo(clics=None), lleno).clics_pesados() is None
    assert nivel(lleno, tramo(clics=None)).clics_crudos() is None
    assert nivel(tramo(venta=None), lleno).venta_pesada() is None
    assert nivel(lleno, tramo(venta=None)).venta_cruda() is None
    assert nivel(tramo(gasto=None), lleno).gasto_pesado() is None
    assert nivel(lleno, tramo(gasto=None)).gasto_crudo() is None


# ---------------------------------------------------------------------------
# Trayectoria: direccion, ultimo, piso y razon de trafico
# ---------------------------------------------------------------------------


def test_direccion_subida_bajada_y_ajuste():
    assert cambio(antes="10.00", despues="12.00").direccion == 1
    assert cambio(antes="12.00", despues="10.00").direccion == -1
    assert cambio(antes="10.00", despues="10.00").direccion == 1


def test_ultimo_none_sin_cambios():
    assert trayectoria().ultimo is None
    cambios = [cambio(dt.date(2026, 9, 1)), cambio(dt.date(2026, 9, 9))]
    assert trayectoria(cambios, efecto()).ultimo == cambios[-1]


def test_piso_aprendido_es_el_maximo_desde_el_que_se_regreso():
    cambios = [
        cambio(dt.date(2026, 9, 1), "9.74", "3.73", "motor"),
        cambio(dt.date(2026, 9, 5), "3.73", "5.00", "regreso_del_dueno"),
        cambio(dt.date(2026, 9, 9), "5.00", "4.00", "motor"),
        cambio(dt.date(2026, 9, 15), "4.00", "4.50", "regreso_por_desplome"),
    ]
    assert trayectoria(cambios, efecto()).piso_aprendido == Decimal("4.00")


def test_piso_aprendido_none_sin_regresos():
    cambios = [cambio(dt.date(2026, 9, 1), "9.74", "3.73", "motor")]
    assert trayectoria(cambios, efecto()).piso_aprendido is None
    assert trayectoria().piso_aprendido is None


def test_razon_trafico_compara_dias_promedio():
    assert efecto(pre_impr=7000, post_impr=700, dias_trafico=7).razon_trafico() == Decimal("0.1")


def test_razon_trafico_none_sin_base_o_sin_dias():
    assert efecto(pre_impr=0, dias_trafico=7).razon_trafico() is None
    assert efecto(pre_impr=5000, dias_trafico=0).razon_trafico() is None
    assert efecto(pre_impr=None, dias_trafico=7).razon_trafico() is None
    assert efecto(post_impr=None, dias_trafico=7).razon_trafico() is None


def test_origen_incluye_ajuste_de_campana():
    assert "ajuste_de_campana" in typing.get_args(OrigenCambio)
