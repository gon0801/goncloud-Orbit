"""Avisos diarios de campana (BIDS 02, V.4). Puro: sin DB ni red.

Tres clases: ubicacion que gasta sin vender, campana sin presupuesto y
presupuesto expuesto. Un mensaje por plataforma y clase, una linea por
caso. Los textos jamas dicen costo, margen ni target.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

from app.avisos_campana import Aviso, avisos_del_dia, mensaje_clase
from app.pantalla_dinero import FilaCampana, FilaUbicacion

PROHIBIDAS = ("costo", "margen", "target")


def _ubi(**cambios):
    fila = FilaUbicacion(
        ubicacion="fuera_de_amazon",
        gasto=Decimal("1735"),
        clics=1127,
        pedidos=0,
        venta=Decimal("0"),
        cpc=Decimal("1.54"),
        conversion_pct=Decimal("0.0"),
        acos_pct=None,
        parte_del_gasto_pct=Decimal("10.0"),
        gasta_sin_vender=True,
    )
    return fila if not cambios else replace(fila, **cambios)


def _camp(**cambios):
    fila = FilaCampana(
        campana_id=7,
        nombre="Campana 7",
        presupuesto_diario=Decimal("100"),
        gasto_medio_diario=Decimal("43"),
        uso_presupuesto_pct=Decimal("43.0"),
        estrategia="fija",
        ajustes_ubicacion=(),
        gasto_fuera_de_amazon=None,
        dias_al_tope_7d=5,
    )
    return fila if not cambios else replace(fila, **cambios)


def _avisos(ubis=(), camps=(), plataforma="amazon_mx", umbral=Decimal("350")):
    return avisos_del_dia(
        tuple(ubis), tuple(camps), plataforma=plataforma, gasto_para_concluir=umbral
    )


def test_ubicacion_en_el_umbral_sin_pedidos_avisa_con_frase():
    (aviso,) = _avisos(ubis=[_ubi()])
    assert aviso.clase == "ubicacion_gasta_sin_vender"
    assert aviso.campana_id is None
    assert aviso.plataforma == "amazon_mx"
    assert aviso.frase == "Fuera de Amazon: 1,735 MXN en 30 días, 1,127 clics, ningún pedido."


def test_ubicacion_con_pedido_o_gasto_none_o_bajo_no_avisa():
    assert _avisos(ubis=[_ubi(pedidos=1)]) == ()
    assert _avisos(ubis=[_ubi(gasto=None, gasta_sin_vender=False)]) == ()
    assert _avisos(ubis=[_ubi(gasto=Decimal("349.99"), gasta_sin_vender=True)]) == ()


def test_ubicacion_justo_en_el_umbral_avisa():
    (aviso,) = _avisos(ubis=[_ubi(gasto=Decimal("350"))])
    assert aviso.clase == "ubicacion_gasta_sin_vender"


def test_ubicacion_clics_none_pinta_guion_en_frase():
    (aviso,) = _avisos(ubis=[_ubi(clics=None)])
    assert aviso.frase == "Fuera de Amazon: 1,735 MXN en 30 días, — clics, ningún pedido."


def test_campana_5_dias_al_tope_avisa_con_frase():
    (aviso,) = _avisos(camps=[_camp()])
    assert aviso.clase == "campana_sin_presupuesto"
    assert aviso.campana_id == 7
    assert aviso.frase == (
        "Campana 7: se queda sin presupuesto. Usó 90 % o más en 5 de los últimos 7 días."
    )


def test_campana_4_dias_o_sin_tope_no_avisa():
    assert _avisos(camps=[_camp(dias_al_tope_7d=4)]) == ()
    assert _avisos(camps=[_camp(dias_al_tope_7d=None)]) == ()


def test_presupuesto_mayor_a_10_veces_avisa_con_frase():
    (aviso,) = _avisos(
        camps=[
            _camp(
                presupuesto_diario=Decimal("5354"),
                gasto_medio_diario=Decimal("43"),
                dias_al_tope_7d=0,
            )
        ]
    )
    assert aviso.clase == "presupuesto_expuesto"
    assert aviso.frase == "Campana 7: este presupuesto no limita. 5,354 MXN al día y gasta 43."


def test_presupuesto_10_veces_o_menos_o_sin_gasto_no_avisa():
    assert _avisos(camps=[_camp(presupuesto_diario=Decimal("430"), dias_al_tope_7d=0)]) == ()
    assert _avisos(camps=[_camp(gasto_medio_diario=None, dias_al_tope_7d=0)]) == ()
    assert _avisos(camps=[_camp(gasto_medio_diario=Decimal("0"), dias_al_tope_7d=0)]) == ()
    assert _avisos(camps=[_camp(presupuesto_diario=None, dias_al_tope_7d=0)]) == ()


def test_los_tres_avisos_salen_ordenados_y_sin_palabras_prohibidas():
    avisos = _avisos(
        ubis=[_ubi()],
        camps=[
            _camp(campana_id=8, nombre="Tope", dias_al_tope_7d=5),
            _camp(
                campana_id=9,
                nombre="Holgada",
                presupuesto_diario=Decimal("5354"),
                gasto_medio_diario=Decimal("43"),
                dias_al_tope_7d=0,
            ),
        ],
    )
    assert [a.clase for a in avisos] == [
        "ubicacion_gasta_sin_vender",
        "campana_sin_presupuesto",
        "presupuesto_expuesto",
    ]
    for aviso in avisos:
        assert isinstance(aviso, Aviso)
        assert aviso.plataforma == "amazon_mx"
        assert not any(palabra in aviso.frase for palabra in PROHIBIDAS)


def test_mensaje_por_clase_una_linea_por_caso():
    avisos = _avisos(
        ubis=[_ubi()],
        camps=[
            _camp(),
            _camp(campana_id=8, nombre="Tope"),
            _camp(campana_id=9, nombre="Tope 2"),
        ],
    )
    texto = mensaje_clase("amazon_mx", "campana_sin_presupuesto", avisos)
    assert texto.splitlines() == [
        "Campana 7: se queda sin presupuesto. Usó 90 % o más en 5 de los últimos 7 días.",
        "Tope: se queda sin presupuesto. Usó 90 % o más en 5 de los últimos 7 días.",
        "Tope 2: se queda sin presupuesto. Usó 90 % o más en 5 de los últimos 7 días.",
    ]


def test_mensaje_sin_casos_da_cadena_vacia():
    assert mensaje_clase("amazon_mx", "campana_sin_presupuesto", ()) == ""


def test_mensaje_solo_trae_su_plataforma_y_su_clase():
    avisos = _avisos(camps=[_camp()], plataforma="amazon_us")
    assert mensaje_clase("amazon_mx", "campana_sin_presupuesto", avisos) == ""
    assert mensaje_clase("amazon_us", "presupuesto_expuesto", avisos) == ""


def test_aviso_propio_dentro_de_la_ventana_de_7_dias():
    """V.3: ajuste de ubicacion hace 3 dias -> aviso con los dias."""
    from app.avisos_campana import avisos_ajuste_propio

    assert (
        avisos_ajuste_propio("ajuste_ubicacion", dt.date(2026, 10, 8), dt.date(2026, 10, 10))
        == "Orbit movió el precio hace 3 días; espera al día 7 para juzgarlo."
    )


def test_aviso_propio_fuera_de_ventana_u_otra_clase_da_none():
    from app.avisos_campana import avisos_ajuste_propio

    assert (
        avisos_ajuste_propio("ajuste_ubicacion", dt.date(2026, 10, 1), dt.date(2026, 10, 10))
        is None
    )
    assert avisos_ajuste_propio("presupuesto", dt.date(2026, 10, 8), dt.date(2026, 10, 10)) is None
