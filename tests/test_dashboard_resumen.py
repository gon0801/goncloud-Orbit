"""Pruebas monetarias del Resumen multicanal (RESUMEN-ADS-02).

Solo lo que mueve dinero o cambia el contrato del rango:

- Moneda NATIVA por canal y separacion MXN/USD (nunca un total mezclado).
- ACoS/ROAS desde SUMAS, no promediando ratios diarios.
- Cero conocido vs dato faltante (`None`), jamas un cero inventado.
- MA7 con calentamiento y ventana incompleta -> `None`.
- Ventana visible que termina en D-1 de la zona local MINIMA (no UTC),
  recorte de `hasta` y topes 4xx (incluye fechas fuera del calendario).
- Variacion solo comparable con la misma cobertura de dias.
- Totales por moneda sin sumar metricas que un canal no publica.

No hay pruebas de texto ni de la interfaz: la API real se verifica contra la
base desechable (docs/evidencia/resumen-ads-multicanal/02-contrato).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app import dashboard_resumen as resumen
from app.optimizer.bid import PLATAFORMAS_MONEDA

HOY = dt.date(2026, 10, 31)
UN_DIA = dt.timedelta(days=1)


def _dia(fecha: dt.date, cost=None, revenue=None, clicks=None, orders=None) -> resumen.Dia:
    return resumen.Dia(
        fecha=fecha,
        cost=None if cost is None else Decimal(cost),
        ad_revenue=None if revenue is None else Decimal(revenue),
        clicks=clicks,
        orders=orders,
    )


def test_catalogo_moneda_nativa_sin_ampliar_el_optimizador():
    """Amazon conserva la moneda sellada del motor; MeLi es MXN y NO entra a
    `PLATAFORMAS_MONEDA` (el Resumen tiene catalogo propio)."""
    assert {nombre: canal.moneda for nombre, canal in resumen.CANALES.items()} == {
        "amazon_mx": "MXN",
        "amazon_us": "USD",
        "meli": "MXN",
    }
    for plataforma in ("amazon_mx", "amazon_us"):
        assert resumen.CANALES[plataforma].moneda == PLATAFORMAS_MONEDA[plataforma]
    assert "meli" not in PLATAFORMAS_MONEDA


def test_meli_no_hereda_la_madurez_d8_de_amazon():
    """MeLi sin madurez publicada: la respuesta lo declara, no la inventa."""
    amazon = resumen.CANALES["amazon_mx"]
    meli = resumen.CANALES["meli"]
    assert amazon.dias_inmaduros == resumen.DIAS_INMADUROS
    assert meli.dias_inmaduros is None and meli.motivo_sin_madurez
    assert resumen._inmaduro(HOY - UN_DIA, HOY, meli) is None
    assert resumen._inmaduro(HOY - UN_DIA, HOY, amazon) is True


def test_ventana_periodos_terminan_en_d1_y_comparacion_igual():
    for periodo in (7, 30, 90):
        ventana = resumen.ventana_resumen(periodo=periodo, desde=None, hasta=None, hoy=HOY)
        assert ventana.hasta == HOY - UN_DIA
        assert ventana.dias == periodo == (ventana.hasta - ventana.desde).days + 1
        assert ventana.anterior_hasta == ventana.desde - UN_DIA
        assert (ventana.anterior_hasta - ventana.anterior_desde).days + 1 == periodo


def test_hoy_es_la_zona_local_minima_y_d1_queda_cerrado_en_todos():
    """A las 23:15 CDMX del 31-oct (05:15Z del 1-nov) el Resumen no sirve el
    dia en curso: hoy=2026-10-31 y periodo 7 termina en 2026-10-30."""
    canales = ("amazon_mx", "amazon_us", "meli")
    instante = dt.datetime(2026, 11, 1, 5, 15, tzinfo=dt.UTC)
    assert resumen.hoy_por_canal(canales, instante) == {
        "amazon_mx": dt.date(2026, 10, 31),
        "amazon_us": dt.date(2026, 10, 31),
        "meli": dt.date(2026, 10, 31),
    }
    hoy = resumen.hoy_resumen(canales, instante)
    assert hoy == dt.date(2026, 10, 31)
    ventana = resumen.ventana_resumen(periodo=7, desde=None, hasta=None, hoy=hoy)
    assert ventana.desde == dt.date(2026, 10, 24)
    assert ventana.hasta == dt.date(2026, 10, 30)

    # 06:30Z del 1-nov: CDMX ya es 1-nov pero Los Angeles sigue en 31-oct.
    # El Resumen toma la fecha MINIMA para que D-1 este cerrado en todos.
    instante_min = dt.datetime(2026, 11, 1, 6, 30, tzinfo=dt.UTC)
    assert resumen.hoy_por_canal(canales, instante_min)["amazon_mx"] == dt.date(2026, 11, 1)
    assert resumen.hoy_resumen(canales, instante_min) == dt.date(2026, 10, 31)


def test_ventana_personalizada_recorta_hoy_y_rechaza_rangos_invalidos():
    """`hasta >= hoy` se RECORTA a D-1 con declaracion; invertido o >366 -> error."""
    ventana = resumen.ventana_resumen(periodo=30, desde=dt.date(2026, 10, 1), hasta=HOY, hoy=HOY)
    assert ventana.hasta == HOY - UN_DIA
    assert ventana.hasta_solicitado == HOY
    assert ventana.recortado is True
    assert ventana.periodo == "personalizado"

    with pytest.raises(ValueError):
        resumen.ventana_resumen(
            periodo=30, desde=dt.date(2026, 10, 20), hasta=dt.date(2026, 10, 10), hoy=HOY
        )
    with pytest.raises(ValueError):
        resumen.ventana_resumen(
            periodo=30, desde=HOY - dt.timedelta(days=367), hasta=HOY - UN_DIA, hoy=HOY
        )
    with pytest.raises(ValueError):
        resumen.ventana_resumen(periodo=30, desde=dt.date(2026, 10, 1), hasta=None, hoy=HOY)
    with pytest.raises(ValueError):
        resumen.ventana_resumen(periodo=14, desde=None, hasta=None, hoy=HOY)


@pytest.mark.parametrize(
    ("desde", "hasta"),
    [
        (dt.date(1, 1, 1), dt.date(1, 1, 7)),
        # Solo el calentamiento del MA7 (desde - 6) desborda: antes daba 500.
        (dt.date(1, 1, 2), dt.date(1, 1, 2)),
        (dt.date(1, 1, 6), dt.date(1, 1, 6)),
        (dt.date(1, 1, 4), dt.date(1, 1, 5)),
    ],
)
def test_ventana_rechaza_fechas_fuera_del_calendario(desde, hasta):
    """Una fecha absurda es 4xx, jamas un OverflowError (500) en la consulta:
    la validacion cubre el periodo anterior Y el calentamiento del MA7."""
    with pytest.raises(ValueError):
        resumen.ventana_resumen(periodo=30, desde=desde, hasta=hasta, hoy=HOY)


def test_acos_y_roas_salen_de_sumas_no_del_promedio_de_ratios():
    """Dos dias con pesos distintos: el promedio de ratios miente; las sumas no."""
    acos = resumen.totales_dias(
        [
            _dia(HOY - 2 * UN_DIA, "10.0000", "100.0000", 1),
            _dia(HOY - UN_DIA, "10.0000", "1000.0000", 1),
        ],
        2,
    )
    # promedio de ACoS diarios: (10 + 1)/2 = 5.5; sumas: 20/1100 = 1.8181...
    assert acos["acos"] == "1.82"
    assert acos["dinero_alineado"] is True

    roas = resumen.totales_dias(
        [
            _dia(HOY - 2 * UN_DIA, "10.0000", "100.0000", 1),
            _dia(HOY - UN_DIA, "1000.0000", "100.0000", 1),
        ],
        2,
    )
    # promedio de ROAS diarios: (10 + 0.1)/2 = 5.05; sumas: 200/1010
    assert roas["roas"] == "0.20"


def test_cero_conocido_vs_dato_faltante_y_denominadores_en_cero():
    fecha = HOY - UN_DIA
    cero = resumen.arma_dias({fecha: (Decimal("0.0000"), Decimal("0.0000"), 0, 0)}, fecha, fecha)[0]
    fila = resumen.fila_serie(cero, None, resumen.CANALES["amazon_mx"], HOY)
    assert fila["cost"] == "0.0000" and fila["ad_revenue"] == "0.0000"
    assert fila["clicks"] == 0 and fila["orders"] == 0
    assert fila["sin_ventas"] is True and fila["acos"] is None and fila["roas"] is None

    hueco = resumen.arma_dias({}, fecha, fecha)[0]
    fila_hueco = resumen.fila_serie(hueco, None, resumen.CANALES["amazon_mx"], HOY)
    assert fila_hueco["cost"] is None and fila_hueco["ad_revenue"] is None
    assert fila_hueco["sin_ventas"] is False and fila_hueco["acos"] is None

    # Gasto sin venta: ACoS None (infinito) y ROAS 0 real (la venta conocida
    # es cero; el denominador del ROAS, el gasto, no es cero).
    assert resumen.acos_texto(Decimal("5"), Decimal("0")) is None
    assert resumen.roas_texto(Decimal("5"), Decimal("0")) == "0.00"
    # ACoS cero real (gasto 0 con venta) y ROAS sin gasto -> None.
    assert resumen.acos_texto(Decimal("0"), Decimal("10")) == "0.00"
    assert resumen.roas_texto(Decimal("0"), Decimal("10")) is None


def test_totales_no_inventan_ceros_ni_mezclan_dias_desalineados():
    """Gasto en un dia y venta en otro: las sumas existen, el ratio NO."""
    totales = resumen.totales_dias(
        [
            _dia(HOY - 2 * UN_DIA, "10.0000", None, 1),
            _dia(HOY - UN_DIA, None, "100.0000", 1),
        ],
        2,
    )
    assert totales["cost"] == "10.0000" and totales["ad_revenue"] == "100.0000"
    assert totales["acos"] is None and totales["roas"] is None
    assert totales["dinero_alineado"] is False

    # Sin ningun dia con dato: todo None, jamas 0.
    vacio = resumen.totales_dias(resumen.arma_dias({}, HOY - UN_DIA, HOY), 2)
    assert vacio["cost"] is None and vacio["ad_revenue"] is None
    assert vacio["clicks"] is None and vacio["acos"] is None

    # "Sin ventas" solo se afirma con la venta cubierta en TODO el rango.
    parcial = resumen.totales_dias(
        [_dia(HOY - UN_DIA, "5.0000", "0.0000", 1), resumen.arma_dias({}, HOY, HOY)[0]], 2
    )
    assert parcial["sin_ventas"] is False


def test_cobertura_cuenta_dias_con_dato_y_faltantes():
    dias = resumen.arma_dias(
        {
            HOY - 3 * UN_DIA: (Decimal("1.0000"), Decimal("4.0000"), 2, 1),
            HOY - 2 * UN_DIA: (Decimal("2.0000"), Decimal("8.0000"), 4, 2),
        },
        HOY - 4 * UN_DIA,
        HOY - UN_DIA,
    )
    cobertura = resumen.cobertura(dias, 4)
    assert cobertura["dias_rango"] == 4
    assert cobertura["dias_con_dato"] == 2
    assert cobertura["dias_faltantes"] == 2
    assert cobertura["por_metrica"] == {
        "cost": 2,
        "ad_revenue": 2,
        "clicks": 2,
        "orders": 2,
    }
    assert cobertura["completo"] is False


def test_serie_un_punto_por_dia_y_ma7_con_calentamiento():
    """30 dias visibles = 30 puntos; el MA7 es la media diaria con los 6 previos."""
    desde = HOY - dt.timedelta(days=30)
    hasta = HOY - UN_DIA
    por_fecha = {}
    fecha = desde - dt.timedelta(days=resumen.DIAS_CALENTAMIENTO)
    while fecha <= hasta:
        por_fecha[fecha] = (
            Decimal("2.0000"),
            Decimal("10.0000"),
            1,
            1,
        )
        fecha += UN_DIA
    serie = resumen.serie_publica(por_fecha, desde, hasta, resumen.CANALES["amazon_mx"], HOY)
    assert len(serie) == 30
    assert serie[0]["fecha"] == desde.isoformat()
    assert serie[-1]["fecha"] == hasta.isoformat()
    assert serie[0]["ma7"]["cost"] == "2.0000"  # media diaria (7 x 2.0000 / 7)
    assert serie[0]["ma7"]["ad_revenue"] == "10.0000"
    assert serie[0]["ma7"]["clicks"] == "1.00"
    assert serie[0]["ma7"]["orders"] == "1.00"
    assert serie[0]["ma7"]["completo"] is True
    assert serie[0]["ma7"]["acos"] == "20.00"
    assert serie[0]["ma7"]["roas"] == "5.00"


def test_ma7_con_un_dia_desconocido_es_none_no_cero():
    desde = HOY - dt.timedelta(days=10)
    hasta = HOY - UN_DIA
    por_fecha = {}
    fecha = desde - dt.timedelta(days=resumen.DIAS_CALENTAMIENTO)
    while fecha <= hasta:
        por_fecha[fecha] = (Decimal("1.0000"), Decimal("2.0000"), 1, 1)
        fecha += UN_DIA
    hueco = desde - dt.timedelta(days=3)
    del por_fecha[hueco]
    serie = resumen.serie_publica(por_fecha, desde, hasta, resumen.CANALES["amazon_mx"], HOY)
    primera = serie[0]
    assert primera["fecha"] == desde.isoformat()
    assert primera["ma7"]["cost"] is None
    assert primera["ma7"]["ad_revenue"] is None
    assert primera["ma7"]["clicks"] is None
    assert primera["ma7"]["acos"] is None and primera["ma7"]["roas"] is None
    assert primera["ma7"]["completo"] is False
    # Cuatro dias despues el hueco ya salio de la ventana: MA7 completo.
    assert serie[4]["ma7"]["cost"] == "1.0000"


def test_dia_observado_antes_del_cierre_se_marca_sin_cambiar_valores():
    """MeLi copiado a las 23:02 CDMX del 30-oct: la serie lo marca
    `cerrado: false` y la cobertura lo lista, pero las cifras siguen iguales."""
    fecha = dt.date(2026, 10, 30)
    # 05:02:55Z del 31-oct = 23:02:55 CDMX del 30-oct (antes del cierre).
    dia = resumen.Dia(
        fecha=fecha,
        cost=Decimal("171.5900"),
        ad_revenue=Decimal("989.0000"),
        clicks=1,
        orders=1,
        observed_at=dt.datetime(2026, 10, 31, 5, 2, 55, tzinfo=dt.UTC),
    )
    meli = resumen.CANALES["meli"]
    fila = resumen.fila_serie(dia, None, meli, HOY)
    assert fila["cerrado"] is False
    assert fila["motivo_cierre"] == "observado_antes_del_cierre"
    assert fila["cost"] == "171.5900" and fila["ad_revenue"] == "989.0000"
    assert fila["acos"] == "17.35"

    cubierta = resumen.cobertura([dia], 1, meli)
    assert cubierta["dias_con_dato"] == 1
    assert cubierta["cierre"]["dias_antes_del_cierre"] == 1
    assert cubierta["cierre"]["fechas_antes_del_cierre"] == ["2026-10-30"]
    assert cubierta["cierre"]["todos_cerrados"] is False

    # La misma observacion ya despues del cierre (06:00Z del 31-oct en CDMX).
    dia_cerrado = resumen.Dia(
        fecha=fecha,
        cost=dia.cost,
        ad_revenue=dia.ad_revenue,
        clicks=dia.clicks,
        orders=dia.orders,
        observed_at=dt.datetime(2026, 10, 31, 6, 0, tzinfo=dt.UTC),
    )
    assert resumen.fila_serie(dia_cerrado, None, meli, HOY)["cerrado"] is True
    assert resumen.fila_serie(dia_cerrado, None, meli, HOY)["motivo_cierre"] is None

    # En Los Angeles el dia cierra una hora mas tarde (07:00Z): 06:30Z aun no.
    amazon_us = resumen.CANALES["amazon_us"]
    dia_us = resumen.Dia(
        fecha=fecha,
        cost=dia.cost,
        ad_revenue=dia.ad_revenue,
        clicks=dia.clicks,
        orders=dia.orders,
        observed_at=dt.datetime(2026, 10, 31, 6, 30, tzinfo=dt.UTC),
    )
    assert resumen.fila_serie(dia_us, None, amazon_us, HOY)["cerrado"] is False
    # Un dia sin observacion no se juzga.
    assert (
        resumen.fila_serie(resumen.arma_dias({}, fecha, fecha)[0], None, meli, HOY)["cerrado"]
        is None
    )


def _bloque(moneda: str, dias: list[resumen.Dia], dias_rango: int) -> dict:
    return {
        "moneda": moneda,
        "dias": dias_rango,
        "totals": resumen.totales_dias(dias, dias_rango),
        "coverage": resumen.cobertura(dias, dias_rango),
    }


def test_variacion_es_none_si_la_cobertura_de_los_periodos_difiere():
    """Dos dias con dato contra uno: el porcentaje contaria el hueco como cero."""
    actual = resumen.totales_dias(
        [
            _dia(HOY - 2 * UN_DIA, "10.0000", "100.0000", 1),
            _dia(HOY - UN_DIA, "10.0000", "100.0000", 1),
        ],
        7,
    )
    anterior = resumen.totales_dias([_dia(HOY - 9 * UN_DIA, "10.0000", "100.0000", 1)], 7)
    var = resumen.variacion(actual, anterior)
    assert var["comparable"] is False
    assert var["motivo"] == "cobertura_distinta"
    assert var["cost_pct"] is None
    assert var["ad_revenue_pct"] is None
    assert var["clicks_pct"] is None
    assert var["acos_pp"] is None and var["roas_delta"] is None

    # Misma cobertura: la variacion si se publica.
    igual = resumen.variacion(actual, actual)
    assert igual["comparable"] is True and igual["motivo"] is None
    assert igual["cost_pct"] == "0.00"


def test_variacion_exige_cobertura_por_metrica_no_solo_dias_con_dato():
    """Mismos dias con dato, gasto `10, null` vs `10, 10`: no se compara.

    Comparar las sumas incompletas publicaria `cost_pct = -50.00`. Cada
    metrica exige SU cobertura igual y completa; ACoS/ROAS exigen dinero
    alineado. `comparable` sigue declarando la cobertura global.
    """
    actual = resumen.totales_dias(
        [
            _dia(HOY - 2 * UN_DIA, "10.0000", "10.0000", 1),
            _dia(HOY - UN_DIA, None, "10.0000", 1),
        ],
        2,
    )
    anterior = resumen.totales_dias(
        [
            _dia(HOY - 4 * UN_DIA, "10.0000", "10.0000", 1),
            _dia(HOY - 3 * UN_DIA, "10.0000", "10.0000", 1),
        ],
        2,
    )
    assert actual["dias_con_dato"] == anterior["dias_con_dato"] == 2
    assert actual["dias_cost"] == 1 and anterior["dias_cost"] == 2

    var = resumen.variacion(actual, anterior)
    assert var["comparable"] is True and var["motivo"] is None
    assert var["cost_pct"] is None
    assert var["motivo_por_metrica"]["cost_pct"] == "cobertura_distinta"
    # Las metricas con la MISMA cobertura si se comparan.
    assert var["ad_revenue_pct"] == "0.00"
    assert var["clicks_pct"] == "0.00"
    assert var["motivo_por_metrica"]["ad_revenue_pct"] is None
    # ACoS/ROAS no se comparan: la cobertura de gasto difiere entre periodos
    # (el motivo es el de la cobertura, no el del dinero alineado).
    assert var["acos_pp"] is None and var["roas_delta"] is None
    assert var["motivo_por_metrica"]["acos_pp"] == "cobertura_distinta"


def test_totales_por_moneda_separan_mxn_de_usd():
    bloques = {
        "amazon_mx": _bloque("MXN", [_dia(HOY - UN_DIA, "10.0000", "100.0000", 2, 1)], 1),
        "meli": _bloque("MXN", [_dia(HOY - UN_DIA, "5.0000", "20.0000", 1)], 1),
        "amazon_us": _bloque("USD", [_dia(HOY - UN_DIA, "7.0000", "0.0000", 1, 0)], 1),
    }
    totales = {bloque["moneda"]: bloque for bloque in resumen.totales_por_moneda(bloques)}
    assert set(totales) == {"MXN", "USD"}
    assert totales["MXN"]["cost"] == "15.0000"
    assert totales["MXN"]["ad_revenue"] == "120.0000"
    assert totales["MXN"]["acos"] == "12.50"
    assert totales["MXN"]["roas"] == "8.00"
    assert totales["MXN"]["canales"] == ["amazon_mx", "meli"]
    # MeLi no publica `orders`: el grupo MXN NO inventa la suma de Amazon MX.
    assert totales["MXN"]["clicks"] == 3
    assert totales["MXN"]["orders"] is None
    assert totales["MXN"]["cobertura"]["orders"] is False
    assert totales["MXN"]["completo"] is False
    # USD jamas se suma al MXN.
    assert totales["USD"]["cost"] == "7.0000" and totales["USD"]["acos"] is None
    assert totales["USD"]["sin_ventas"] is True
    assert totales["USD"]["orders"] == 0 and totales["USD"]["completo"] is True


def test_totales_por_moneda_no_publican_completo_con_dias_faltantes():
    """Amazon MX con 6/7 dias: el grupo MXN no esta completo y su ratio es None."""
    seis = [
        _dia(HOY - dt.timedelta(days=atras), "10.0000", "100.0000", 1, 1) for atras in range(1, 7)
    ]
    siete = [
        _dia(HOY - dt.timedelta(days=atras), "5.0000", "20.0000", 1, 1) for atras in range(1, 8)
    ]
    bloques = {
        "amazon_mx": _bloque("MXN", seis, 7),
        "meli": _bloque("MXN", siete, 7),
    }
    mxn = resumen.totales_por_moneda(bloques)[0]
    assert mxn["cost"] == "95.0000"
    assert mxn["cobertura"]["dias"] is False
    assert mxn["completo"] is False
    assert mxn["acos"] is None and mxn["roas"] is None


def test_totales_por_moneda_exigen_dinero_en_cada_dia_aunque_haya_clics():
    """Un dia con clics y dinero desconocido no completa el agregado monetario."""
    amazon = _bloque(
        "MXN",
        [
            _dia(HOY - 2 * UN_DIA, "10.0000", "100.0000", 1, 1),
            _dia(HOY - UN_DIA, None, None, 1, 1),
        ],
        2,
    )
    meli = _bloque(
        "MXN",
        [
            _dia(HOY - 2 * UN_DIA, "20.0000", "40.0000", 1),
            _dia(HOY - UN_DIA, "20.0000", "40.0000", 1),
        ],
        2,
    )
    for canales, cost, revenue in (
        ({"amazon_mx": amazon, "meli": meli}, "50.0000", "180.0000"),
        ({"amazon_mx": amazon}, "10.0000", "100.0000"),
    ):
        grupo = resumen.totales_por_moneda(canales)[0]
        assert grupo["acos"] is None and grupo["roas"] is None
        assert grupo["cost"] == cost and grupo["ad_revenue"] == revenue
        assert grupo["cobertura"]["dias"] is True
        assert grupo["cobertura"]["clicks"] is True
        assert grupo["cobertura"]["cost"] is False
        assert grupo["cobertura"]["ad_revenue"] is False
        assert grupo["completo"] is False


def test_variacion_misma_cobertura_parcial_en_fechas_distintas_es_none():
    """3 dias con dato por periodo y gasto en 2, pero en fechas distintas.

    Las dos coberturas de gasto empatan (2 de 3) y ninguna es completa:
    publicar `20` contra `20` daria un `0.00` falso. El motivo es
    `cobertura_incompleta`, no `cobertura_distinta`.
    """
    actual = resumen.totales_dias(
        [
            _dia(HOY - 3 * UN_DIA, None, "100.0000", 1),
            _dia(HOY - 2 * UN_DIA, "10.0000", "100.0000", 1),
            _dia(HOY - UN_DIA, "10.0000", "100.0000", 1),
        ],
        3,
    )
    anterior = resumen.totales_dias(
        [
            _dia(HOY - 6 * UN_DIA, None, "100.0000", 1),
            _dia(HOY - 5 * UN_DIA, "10.0000", "100.0000", 1),
            _dia(HOY - 4 * UN_DIA, "10.0000", "100.0000", 1),
        ],
        3,
    )
    assert actual["dias_cost"] == anterior["dias_cost"] == 2
    assert actual["dias_con_dato"] == anterior["dias_con_dato"] == 3

    var = resumen.variacion(actual, anterior)
    assert var["comparable"] is True and var["motivo"] is None
    assert var["cost_pct"] is None
    assert var["motivo_por_metrica"]["cost_pct"] == "cobertura_incompleta"
    # Venta y clics cubren los 3 dias: su variacion si se publica.
    assert var["ad_revenue_pct"] == "0.00" and var["clicks_pct"] == "0.00"
    assert var["acos_pp"] is None and var["roas_delta"] is None
    assert var["motivo_por_metrica"]["acos_pp"] == "cobertura_incompleta"
    assert var["motivo_por_metrica"]["roas_delta"] == "cobertura_incompleta"


def test_acos_prioriza_la_cobertura_sobre_el_dinero_alineado():
    """Un periodo sin datos es `cobertura_distinta`, no `dinero_no_alineado`.

    Con los DOS periodos vacios no hay cobertura que comparar ni sumas que
    alinear: el `None` viene del valor y el motivo queda `None`.
    """
    con_dato = resumen.totales_dias([_dia(HOY - UN_DIA, "10.0000", "100.0000", 1)], 7)
    vacio = resumen.totales_dias(resumen.arma_dias({}, HOY - 8 * UN_DIA, HOY - 2 * UN_DIA), 7)

    var = resumen.variacion(con_dato, vacio)
    assert var["comparable"] is False and var["motivo"] == "cobertura_distinta"
    assert var["acos_pp"] is None and var["roas_delta"] is None
    assert var["motivo_por_metrica"]["acos_pp"] == "cobertura_distinta"
    assert var["motivo_por_metrica"]["roas_delta"] == "cobertura_distinta"

    var_vacia = resumen.variacion(vacio, vacio)
    assert var_vacia["comparable"] is True
    assert var_vacia["acos_pp"] is None and var_vacia["roas_delta"] is None
    assert var_vacia["motivo_por_metrica"]["acos_pp"] is None
    assert var_vacia["motivo_por_metrica"]["roas_delta"] is None
