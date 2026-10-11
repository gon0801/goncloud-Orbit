"""Ajustes del dueno (BIDS 02, V.3): `planea_ajuste` puro y su huella.

Todo aqui es puro (sin Postgres ni Amazon): el plan sale de la
configuracion vigente y el ajuste pedido. La escritura vive en
`app/apply.py` (`aplica_ajuste_campana`).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.ads.campana_config import ConfigCampana
from app.campana_ajustes import (
    CLASES_SELLADAS,
    CambiarAjusteUbicacion,
    CambiarPresupuesto,
    LimitarFueraDeAmazon,
    planea_ajuste,
)


def _vigente(**cambios) -> ConfigCampana:
    base = dict(
        campana_externa="93529333080113",
        presupuesto_diario=Decimal("100"),
        moneda="MXN",
        estrategia_puja="LEGACY_FOR_SALES",
        ajuste_top_pct=0,
        ajuste_resto_pct=0,
        ajuste_producto_pct=40,
        fuera_de_amazon=None,
    )
    base.update(cambios)
    return ConfigCampana(**base)


def _planea(ajuste, vigente=None, campana_id=7, plataforma="amazon_mx"):
    return planea_ajuste(
        vigente or _vigente(), ajuste, campana_id=campana_id, plataforma=plataforma
    )


def test_clases_selladas_son_las_tres_sondas():
    assert frozenset({"fuera_de_amazon", "ajuste_ubicacion", "presupuesto"}) == CLASES_SELLADAS


def test_presupuesto_cambia_solo_el_presupuesto():
    plan = _planea(CambiarPresupuesto(presupuesto_diario=Decimal("120")))
    assert plan.clase == "presupuesto"
    assert plan.despues == replace(plan.antes, presupuesto_diario=Decimal("120"))
    assert plan.antes.presupuesto_diario == Decimal("100")
    assert "120" in plan.frase and "100" in plan.frase


def test_ubicacion_cambia_solo_su_campo():
    plan = _planea(
        CambiarAjusteUbicacion(ubicacion="paginas_de_producto", porcentaje=0),
        campana_id=7,
    )
    assert plan.clase == "ajuste_ubicacion"
    assert plan.despues.ajuste_producto_pct == 0
    assert plan.despues.ajuste_top_pct == 0
    assert "páginas de producto" in plan.frase


def test_fuera_de_amazon_fija_minimize_spend():
    plan = _planea(LimitarFueraDeAmazon())
    assert plan.clase == "fuera_de_amazon"
    assert "MINIMIZE_SPEND" in (plan.despues.fuera_de_amazon or "")
    assert "fuera de Amazon" in plan.frase


def test_ajuste_que_no_cambia_nada_levanta():
    with pytest.raises(ValueError, match="no cambia nada"):
        _planea(CambiarPresupuesto(presupuesto_diario=Decimal("100")))
    with pytest.raises(ValueError, match="no cambia nada"):
        _planea(CambiarAjusteUbicacion(ubicacion="paginas_de_producto", porcentaje=40))
    with pytest.raises(ValueError, match="no cambia nada"):
        _planea(
            LimitarFueraDeAmazon(),
            vigente=_vigente(
                fuera_de_amazon='{"offAmazonBudgetControlStrategy": "MINIMIZE_SPEND"}'
            ),
        )


def test_porcentaje_fuera_de_rango_levanta():
    with pytest.raises(ValueError, match="0..900"):
        _planea(CambiarAjusteUbicacion(ubicacion="paginas_de_producto", porcentaje=901))
    with pytest.raises(ValueError, match="0..900"):
        _planea(CambiarAjusteUbicacion(ubicacion="paginas_de_producto", porcentaje=-1))
    with pytest.raises(ValueError, match="ubicacion"):
        _planea(CambiarAjusteUbicacion(ubicacion="fuera_de_amazon", porcentaje=10))


def test_presupuesto_cero_o_negativo_levanta():
    with pytest.raises(ValueError, match="presupuesto"):
        _planea(CambiarPresupuesto(presupuesto_diario=Decimal("0")))
    with pytest.raises(ValueError, match="presupuesto"):
        _planea(CambiarPresupuesto(presupuesto_diario=Decimal("-5")))


def test_clase_sin_sonda_sellada_levanta_antes_de_todo():
    class AjusteFuturo:
        pass

    with pytest.raises(ValueError, match="sin sonda sellada"):
        planea_ajuste(_vigente(), AjusteFuturo(), campana_id=7, plataforma="amazon_mx")


def test_huella_cambia_campo_por_campo():
    base = _planea(CambiarPresupuesto(presupuesto_diario=Decimal("120")))
    variantes = [
        _planea(CambiarPresupuesto(presupuesto_diario=Decimal("120")), campana_id=8),
        _planea(CambiarPresupuesto(presupuesto_diario=Decimal("120")), plataforma="amazon_us"),
        _planea(CambiarPresupuesto(presupuesto_diario=Decimal("121"))),
        _planea(
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
            vigente=_vigente(presupuesto_diario=Decimal("90")),
        ),
        _planea(CambiarAjusteUbicacion(ubicacion="paginas_de_producto", porcentaje=0)),
    ]
    huellas = {base.huella()} | {p.huella() for p in variantes}
    assert len(huellas) == 6
    assert len(base.huella()) == 64


def test_huella_estable_ante_mismo_plan():
    plan1 = _planea(CambiarPresupuesto(presupuesto_diario=Decimal("120")))
    plan2 = _planea(CambiarPresupuesto(presupuesto_diario=Decimal("120")))
    assert plan1.huella() == plan2.huella()


def test_presupuesto_nan_o_infinito_levanta():
    """Panel: NaN/Inf no son presupuestos (Decimal('NaN') <= 0 es False)."""
    with pytest.raises(ValueError, match="presupuesto"):
        _planea(CambiarPresupuesto(presupuesto_diario=Decimal("NaN")))
    with pytest.raises(ValueError, match="presupuesto"):
        _planea(CambiarPresupuesto(presupuesto_diario=Decimal("Infinity")))


def test_presupuesto_se_cuantiza_a_centavos():
    """Panel: 100.125 se vuelve 100.12 (lo que Amazon ecoa; precede _bid)."""
    plan = _planea(CambiarPresupuesto(presupuesto_diario=Decimal("100.125")))
    assert plan.despues.presupuesto_diario == Decimal("100.12")
    assert "100.12" in plan.frase


def test_presupuesto_sin_previo_estrena_moneda_del_perfil():
    """Panel: vigente sin presupuesto ni moneda -> destino con MXN (si no,
    el readback trae moneda y el destino no: divergencia determinista)."""
    plan = _planea(
        CambiarPresupuesto(presupuesto_diario=Decimal("120")),
        vigente=_vigente(presupuesto_diario=None, moneda=None),
    )
    assert plan.despues.moneda == "MXN"
    assert "120 MXN" in plan.frase


def test_ubicacion_sin_dynamicBidding_previo_se_rechaza():
    """Panel: forma que V.0 no sondo (el PUT mezclaria y divergiria)."""
    with pytest.raises(ValueError, match="dynamicBidding"):
        _planea(
            CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=50),
            vigente=_vigente(
                estrategia_puja=None,
                ajuste_top_pct=None,
                ajuste_resto_pct=None,
                ajuste_producto_pct=None,
            ),
        )


def test_coincide_clase_mira_solo_lo_que_la_clase_mueve():
    """Panel: normalizacion de Amazon en campos ajenos no diverge."""
    from app.campana_ajustes import coincide_clase

    destino = _planea(CambiarPresupuesto(presupuesto_diario=Decimal("120"))).despues
    assert coincide_clase("presupuesto", replace(destino, estrategia_puja="OTRA"), destino)
    assert not coincide_clase(
        "presupuesto", replace(destino, presupuesto_diario=Decimal("121")), destino
    )
    assert not coincide_clase("presupuesto", replace(destino, campana_externa="1"), destino)
    ubi = _planea(CambiarAjusteUbicacion(ubicacion="paginas_de_producto", porcentaje=0)).despues
    assert coincide_clase("ajuste_ubicacion", replace(ubi, presupuesto_diario=Decimal("999")), ubi)
    assert not coincide_clase("ajuste_ubicacion", replace(ubi, ajuste_producto_pct=10), ubi)
    fuera = _planea(LimitarFueraDeAmazon()).despues
    assert coincide_clase("fuera_de_amazon", replace(fuera, moneda="USD"), fuera)
    assert not coincide_clase("fuera_de_amazon", replace(fuera, fuera_de_amazon=None), fuera)
