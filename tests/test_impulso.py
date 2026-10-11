"""Plan, tope y veredicto puros del impulso (BIDS 02, I.2). Sin base."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from app import fabrica_plan as fp
from app import impulso as im

FECHA = dt.date(2026, 10, 9)


def _objetivo():
    return fp.ObjetivoPlanV2(
        "margen_medido", Decimal("19.10"), "m", Decimal("0.5"), Decimal("19.10")
    )


def _plan(**cambios):
    base = dict(
        plataforma="amazon_mx",
        listing_id=11,
        product_id=1,
        seller_sku="SS-1",
        asin="B0AAAAAAAA",
        subfamilia="aros",
        margen_neto_pct=Decimal("38.20"),
        fecha=FECHA,
        objetivo=_objetivo(),
        tope=im.TopeAprendizaje(
            monto=Decimal("350"),
            moneda="MXN",
            presupuesto_diario=im.presupuesto_diario(Decimal("350"), "MXN"),
        ),
        bid_por_rol={"category_exact": Decimal("5.00"), "auto_discovery": Decimal("5.00")},
        keywords_exact=("collar perro",),
        negatives=("gato",),
        retiros=(),
        bids_acotados=(),
    )
    base.update(cambios)
    return im.PlanImpulso(**base)


def _lectura(**cambios):
    base = dict(
        corte=FECHA,
        dias_observados=6,
        impresiones=1840,
        clics=14,
        gasto=Decimal("48"),
        pedidos=0,
    )
    base.update(cambios)
    return im.LecturaImpulso(**base)


def test_tope_350_mxn_da_diario_25():
    assert im.presupuesto_diario(Decimal("350"), "MXN") == Decimal("25")


def test_tope_no_divisible_da_diario_en_centavos():
    """R09-r2 B-15: el diario sale en centavos (Amazon y la UI no
    aceptan 28 decimales)."""
    assert im.presupuesto_diario(Decimal("36"), "USD") == Decimal("2.57")
    assert im.presupuesto_diario(Decimal("100"), "MXN") == Decimal("7.14")


def test_minimo_por_campana_sube_el_diario_y_adelanta_la_pausa(monkeypatch):
    monkeypatch.setattr(im, "PRESUPUESTO_MINIMO_CAMPANA", {"MXN": Decimal("20"), "USD": None})
    assert im.presupuesto_diario(Decimal("350"), "MXN") == Decimal("40")
    tope = im.TopeAprendizaje(monto=Decimal("350"), moneda="MXN", presupuesto_diario=Decimal("40"))
    assert im.veredicto_impulso(_lectura(gasto=Decimal("270")), tope) == im.VeredictoImpulso(
        "clics_sin_ventas", True, "pausar"
    )


def test_sin_minimo_el_diario_es_tope_entre_14():
    assert im.PRESUPUESTO_MINIMO_CAMPANA == {"MXN": None, "USD": None}
    assert im.presupuesto_diario(Decimal("350"), "MXN") == Decimal("25")


def test_pedido_gana_al_tope_agotado():
    tope = _plan().tope
    assert im.veredicto_impulso(
        _lectura(pedidos=1, gasto=Decimal("349")), tope
    ) == im.VeredictoImpulso("vende", True, "graduar")


def test_gasto_299_sigue_en_curso_y_300_pausa():
    tope = _plan().tope
    assert im.veredicto_impulso(_lectura(gasto=Decimal("299.99")), tope) == im.VeredictoImpulso(
        "en_curso", False, "seguir"
    )
    assert im.veredicto_impulso(_lectura(gasto=Decimal("300")), tope) == im.VeredictoImpulso(
        "clics_sin_ventas", True, "pausar"
    )


@pytest.mark.parametrize("campo", ["impresiones", "clics", "gasto", "pedidos"])
def test_dato_none_sigue_en_curso(campo):
    tope = _plan().tope
    valores = {"gasto": Decimal("349"), "dias_observados": 20, campo: None}
    assert im.veredicto_impulso(_lectura(**valores), tope) == im.VeredictoImpulso(
        "en_curso", False, "seguir"
    )


def test_plazo_vencido_sin_impresiones():
    tope = _plan().tope
    assert im.veredicto_impulso(
        _lectura(dias_observados=14, impresiones=1500, clics=10, gasto=Decimal("48")), tope
    ) == im.VeredictoImpulso("sin_impresiones", True, "pausar")


def test_plazo_vencido_con_impresiones_y_sin_clics():
    tope = _plan().tope
    assert im.veredicto_impulso(
        _lectura(dias_observados=14, impresiones=2500, clics=10, gasto=Decimal("48")), tope
    ) == im.VeredictoImpulso("impresiones_sin_clics", True, "pausar")


def test_plazo_vencido_con_clics_sin_agotar_sigue():
    tope = _plan().tope
    assert im.veredicto_impulso(
        _lectura(dias_observados=14, impresiones=2500, clics=25, gasto=Decimal("48")), tope
    ) == im.VeredictoImpulso("en_curso", False, "seguir")


def test_huella_cambia_por_campo():
    plan = _plan()
    base = plan.huella()
    assert plan.huella() == base
    for campo, valor in [
        ("plataforma", "amazon_us"),
        ("listing_id", 12),
        ("product_id", 2),
        ("seller_sku", "SS-2"),
        ("asin", "B0BBBBBBBB"),
        ("subfamilia", "collares"),
        ("margen_neto_pct", Decimal("40")),
        ("fecha", dt.date(2026, 10, 10)),
        ("bid_por_rol", {"category_exact": Decimal("6.00"), "auto_discovery": Decimal("5.00")}),
        ("keywords_exact", ("otro",)),
        ("negatives", ("perro",)),
        ("bids_acotados", ("category_exact",)),
    ]:
        assert replace(plan, **{campo: valor}).huella() != base, campo
    assert (
        replace(plan, tope=im.TopeAprendizaje(Decimal("360"), "MXN", Decimal("25"))).huella()
        != base
    )
    assert plan.como_json()["tope"]["monto"] == "350"
    assert plan.como_json()["schema_version"] == 3


def test_grupo_v2_exact_primero_mitad_sin_placement():
    grupo = im.grupo_v2_de_plan(_plan())
    pasos = [p for rol in im.ROLES_IMPULSO for p in fp.pasos_del_rol(grupo, rol)]
    campanas = [p for p in pasos if p.recurso == "campaign"]
    assert len(campanas) == 2
    assert campanas[0].payload["dynamicBidding"] == {"strategy": "LEGACY_FOR_SALES"}
    assert "placementBidding" not in campanas[0].payload["dynamicBidding"]
    assert campanas[0].payload["budget"]["budget"] == 12.5
    assert campanas[1].payload["budget"]["budget"] == 12.5
    assert "exact" in campanas[0].payload["name"].lower()


def test_bid_mayor_baja_al_diario_y_sale_en_acotados():
    plan = _plan(bid_por_rol={"category_exact": Decimal("15"), "auto_discovery": Decimal("10")})
    acotados, roles = im.acota_bids(plan.bid_por_rol, Decimal("12.50"))
    assert acotados == {"category_exact": Decimal("12.50"), "auto_discovery": Decimal("10")}
    assert roles == ("category_exact",)


def test_grupo_no_levanta_con_bid_mayor_que_diario():
    plan = _plan(bid_por_rol={"category_exact": Decimal("15"), "auto_discovery": Decimal("10")})
    grupo = im.grupo_v2_de_plan(plan)
    pasos = [p for rol in im.ROLES_IMPULSO for p in fp.pasos_del_rol(grupo, rol)]
    assert len([p for p in pasos if p.recurso == "campaign"]) == 2
