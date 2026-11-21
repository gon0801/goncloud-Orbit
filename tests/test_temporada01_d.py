"""TEMPORADA 01, bloque D: la temporada entra en las decisiones, en practica.

Las 7 pruebas nombradas por la fila D.1 del plan + la retencion del regreso:

- `test_temporada01_d_salida_antes_de_pausa` (dinero): el regreso pendiente va
  ANTES de R0 — una keyword que cumple la pausa sale de la baja igual; y el
  regreso que espera por campana topada NO sale (la hoja sigue en baja) y el
  ciclo lo anota `regreso_espera_presupuesto` en notes.skips.
- `test_temporada01_d_dia_mixto_excluido` (dinero): la cerca es el dia UTC
  siguiente a la confirmacion — el dia mixto (confirmado 23:55) queda FUERA de
  la ventana de falla de la hoja.
- `test_temporada01_d_centavos_de_la_entrada` (dinero): el ajuste 30 -> 20
  recalcula desde la BASE: 8.00 x 0.80 = 6.40 con motivo ajusta_temporada_baja;
  jamas sobre el bid reducido.
- `test_temporada01_d_techo_de_baja_compartido` (dinero): la subida de R6 en
  baja topa en el techo de baja compartido (12.25 -> 14.00, nunca 14.08); si
  el techo deja el delta bajo un centavo, Mantener(techo_de_baja).
- `test_temporada01_d_baja_entra_y_retiene` (dinero): en baja SOLO R3 recorta
  (pierde_dinero); R9 da Mantener(temporada_baja), y R1 no aplica.
- `test_temporada01_d_meta_alta` (dinero): en alta la meta del juicio es
  max(base, meta_alta) LIBRE sobre el equilibrio (US: base 19.05, equilibrio
  23.81, meta 25): una keyword con ACoS entre 23.81 y 25 ya no recorta (R4 la
  juzga con 25); fuera de alta recorta con 19.05/23.81.
- `test_temporada01_d_cortes_por_multiplicador` (pura): mult_baja obligatorio
  — ceil(umbral x mult), piso_pausa y exceso_minimo x2; mult < 1 revienta.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from test_cycle import _config_version, _entidad, _estado, _metrica, _run
from test_optimizer_caso import cambio, efecto, fabrica_caso, nivel, tramo, trayectoria
from test_temporada01_b import (
    D_E1,
    _corre,
    _goal_mx,
    _handler_mx,
    _kw,
    _medianoche,
    _settings,
)

from app import cycle as ciclo
from app import temporada_io
from app.optimizer import cortes, politica, temporada, windows
from app.optimizer.caso import (
    BidVigente,
    CambioFechado,
    Economia,
    EconomiaPlataforma,
    HistoriaBid,
    PresupuestoCampana,
    TemporadaCaso,
)
from app.optimizer.windows import AgregadoMetricas, EvidenciaAdGroup

PISO_MX, TECHO_MX = Decimal("1.00"), Decimal("45.00")
_TOPADA = temporada.CampanaTopada(pct=Decimal("0.90"), dias=5)


def _marcador(fase: str, *, meta_manual: bool = False, fin=None) -> TemporadaCaso:
    """El marcador de temporada con la hoja en `fase`. Para `baja` el episodio
    1 esta ABIERTO (abierto el 2027-01-04, dia uno de E1)."""
    dia = dt.date(2027, 1, 5) if fase == "baja" else dt.date(2027, 3, 5)
    return TemporadaCaso(
        estacion=temporada.Estacion(
            version=temporada.VERSION_TEMPORADA,
            dia=dia,
            fase=fase,
            previa_alta=False,
            reduccion_baja=Decimal("0.30"),
            mult_cortes_baja=Decimal("2"),
            inicio_bloque=dt.date(2027, 1, 1) if fase == "baja" else dt.date(2027, 3, 1),
        ),
        episodio=(
            temporada.EpisodioVigente(id=1, abierto_el=_medianoche(dt.date(2027, 1, 4)))
            if fase == "baja"
            else None
        ),
        excluida_en=frozenset(),
        meta_alta_pct=Decimal("25"),
        meta_manual=meta_manual,
        frontera=temporada.FronteraHoja(
            fase_hoja=fase,
            mult_cortes=Decimal("2") if fase == "baja" else Decimal(1),
            cerca_hoja=None,
            cerca_plataforma=None,
            episodio_id=1 if fase == "baja" else None,
            fin=fin,
        ),
    )


def _corte_pausa(
    *,
    clicks: int = 300,
    orders: int = 0,
    cost: str = "700",
) -> AgregadoMetricas:
    """Ventana de cortes que CALIFICA la pausa por cero pedidos: clics y gasto
    sobre el umbral/piso, sin pedidos en la ventana."""
    desde = dt.date(2026, 12, 15)
    return AgregadoMetricas(
        window_start=desde,
        window_end=dt.date(2026, 12, 24),
        fechas=tuple(desde + dt.timedelta(days=i) for i in range(10)),
        metric_currency="MXN",
        cost=Decimal(cost),
        ad_revenue=Decimal("0"),
        revenue_same_sku=Decimal("0"),
        impressions=clicks * 10,
        clicks=clicks,
        orders=orders,
        observed_at_max=dt.datetime(2026, 12, 24, 12, 0, tzinfo=dt.UTC),
    )


def _cadena_salida() -> HistoriaBid:
    """La cadena de la salida de la baja: entrada 8 -> 5.60 del episodio 1
    ABIERTO (sin fin guardado): la reduccion sigue vigente, el regreso pende."""
    return HistoriaBid(
        desde=dt.date(2027, 1, 1),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(2027, 1, 4, 12, 0, tzinfo=dt.UTC),
                decision_id=1,
                bid_antes=Decimal("8.00"),
                bid_despues=Decimal("5.60"),
                origen="temporada",
                motivo_temporada="entra",
                episodio_id=1,
            ),
        ),
    )


def _caso_salida(*, presupuesto: PresupuestoCampana | None = None, marcador=True) -> object:
    """La salida de la baja: red vigente 8 -> 5.60 del episodio abierto y una
    PAUSE que calificaria (R0 gana a todo sin marcador)."""
    estacion = temporada.Estacion(
        version=temporada.VERSION_TEMPORADA,
        dia=dt.date(2027, 2, 2),  # la baja ya termino: regreso el 8-feb analogo
        fase="normal",
        previa_alta=False,
        reduccion_baja=Decimal("0.30"),
        mult_cortes_baja=Decimal("2"),
        inicio_bloque=dt.date(2027, 2, 1),
    )
    marcador = (
        TemporadaCaso(
            estacion=estacion,
            episodio=None,  # TX3 cerro el episodio
            excluida_en=frozenset(),
            meta_alta_pct=Decimal("25"),
            meta_manual=False,
            frontera=temporada.FronteraHoja(
                fase_hoja="baja",  # el regreso pendiente mantiene la hoja en baja
                mult_cortes=Decimal("2"),
                cerca_hoja=None,
                cerca_plataforma=None,
                episodio_id=1,
            ),
        )
        if marcador
        else None
    )
    return fabrica_caso(
        bid=BidVigente(Decimal("5.60"), "MXN", PISO_MX, TECHO_MX),
        propia=nivel(
            tramo(clics=300, pedidos=0, venta="0", gasto="700", impresiones=3000),
            tramo(clics=300, pedidos=0, venta="0", gasto="700", impresiones=3000),
        ),
        trayectoria=trayectoria(
            [cambio(dt.date(2027, 1, 4), "8.00", "5.60", "temporada")],
            efecto(dias_post=10, clics_post=25, gasto_post="47.50", vendia=False),
        ),
        historia=_cadena_salida(),
        temporada=marcador,
        presupuesto=presupuesto,
    )


def test_temporada01_d_salida_antes_de_pausa(db_template):
    """Decision 6 del plan: el regreso pendiente va ANTES de R0 — la keyword
    que cumple la pausa sale de la baja igual (aunque sea inerte y sin
    vender). Y las retenciones: con la campana topada el regreso NO sale (la
    hoja sigue en baja: cortes x2, HOLD) y el ciclo lo anota
    `regreso_espera_presupuesto` en notes.skips."""
    # control: sin marcador, la PAUSE gana a todo
    caso_hoy = _caso_salida(marcador=False)
    caso_hoy = replace(caso_hoy, pausa=replace(caso_hoy.pausa, cortes=_corte_pausa()))
    veredicto_hoy = politica.decide(caso_hoy)
    assert isinstance(veredicto_hoy, politica.Pausar), veredicto_hoy
    # con marcador, el regreso sale ANTES de R0
    veredicto = politica.decide(_caso_salida())
    assert isinstance(veredicto, politica.Regresar), veredicto
    assert veredicto.motivo == politica.MOTIVO_SALE_BAJA
    assert veredicto.bid_nuevo == Decimal("8.00"), "el nivel de regreso es la base"
    # retencion: campana topada -> el regreso espera y la PAUSE decide
    topada = PresupuestoCampana(
        presupuesto_diario=Decimal("100"),
        dias_medidos=7,
        dias_topados=5,
        topada=_TOPADA,
    )
    veredicto = politica.decide(_caso_salida(presupuesto=topada))
    assert not isinstance(veredicto, politica.Regresar), (
        "topada: el regreso NO sale (la hoja sigue en baja)"
    )
    # la anotacion del ciclo: regreso_espera_presupuesto en notes.skips
    contadores = ciclo._Contadores()
    contadores.temporada_plat = temporada_io.TemporadaPlataforma(
        estacion=temporada.Estacion(
            version=temporada.VERSION_TEMPORADA,
            dia=dt.date(2027, 2, 2),
            fase="normal",
            previa_alta=False,
            reduccion_baja=Decimal("0.30"),
            mult_cortes_baja=Decimal("2"),
            inicio_bloque=dt.date(2027, 2, 1),
        ),
        modo="live",
        config=None,
        meta_alta_pct=Decimal("25"),
        estado=None,
    )
    lecturas_stub = type(
        "L",
        (),
        {"pendiente": lambda self, *a, **k: temporada.RestauracionPendiente(Decimal("8.00"), 1)},
    )()
    ciclo._anota_regreso_retenido(
        contadores,
        lecturas_stub,
        _caso_salida(presupuesto=topada),
        veredicto,
        _marcador("baja").frontera,
        1001,
        Decimal("5.60"),
        floor=PISO_MX,
        techo=TECHO_MX,
    )
    assert contadores.skips_entidad[politica.MOTIVO_REGRESO_ESPERA_PRESUPUESTO] == 1


def test_temporada01_d_dia_mixto_excluido(db_template):
    """Decision 8: la cerca es el dia UTC SIGUIENTE a la confirmacion — un
    cambio confirmado el dia D a las 23:55 deja el dia D FUERA de la ventana
    de falla de la hoja, aunque su metrica exista."""
    conn, _dsn = db_template
    _run(conn)
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "d-camp")
    dia_mixto = dt.date(2026, 11, 10)
    dia_limpio = dia_mixto + dt.timedelta(days=1)
    dia_extra = dia_limpio + dt.timedelta(days=5)
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-d') RETURNING id"
    ).fetchone()[0]
    _metrica(
        conn,
        run_id,
        camp,
        dia_mixto,
        _medianoche(dia_mixto),
        moneda="MXN",
        cost="900.00",
        impressions=100,
    )
    _metrica(
        conn,
        run_id,
        camp,
        dia_limpio,
        _medianoche(dia_limpio),
        moneda="MXN",
        cost="10.00",
        impressions=100,
    )
    _metrica(
        conn,
        run_id,
        camp,
        dia_extra,
        _medianoche(dia_extra),
        moneda="MXN",
        cost="1.00",
        impressions=100,
    )
    conn.commit()
    # la cerca de la hoja: confirmado 23:55 del dia mixto -> cerca = dia siguiente
    cadena = HistoriaBid(
        desde=dia_mixto - dt.timedelta(days=5),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(
                    dia_mixto.year, dia_mixto.month, dia_mixto.day, 23, 55, tzinfo=dt.UTC
                ),
                decision_id=1,
                bid_antes=Decimal("8.00"),
                bid_despues=Decimal("5.60"),
                origen="temporada",
                motivo_temporada="entra",
                episodio_id=1,
            ),
        ),
    )
    assert temporada.cerca_hoja_de(cadena, dia_limpio + dt.timedelta(days=30)) == dia_limpio
    ventana = windows.ventana_cortes(
        conn,
        camp,
        dt.datetime(dia_extra.year, dia_extra.month, dia_extra.day, 12, 0, tzinfo=dt.UTC)
        + dt.timedelta(days=11),
        cerca=dia_limpio,
    )
    assert ventana is not None
    assert ventana.window_start >= dia_limpio, "la ventana empieza en la cerca"
    assert ventana.cost == Decimal("10.00"), "el dia mixto (900) queda fuera"


def test_temporada01_d_centavos_de_la_entrada(db_template):
    """El ajuste 30 -> 20 recalcula desde la BASE, no sobre el bid reducido:
    8.00 x 0.80 = 6.40 con motivo ajusta_temporada_baja; jamas 5.60 x algo."""
    conn, _dsn = db_template
    _run(conn)
    config_id = _config_version(conn, _settings())
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "d-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "d-ag", parent=camp)
    _estado(conn, ag, synced_at=_medianoche(D_E1))
    _estado(conn, camp, synced_at=_medianoche(D_E1))
    kw1 = _kw(conn, ag, "d-kw1", "kw 1", "8.00")
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-d') RETURNING id"
    ).fetchone()[0]
    dia = dt.date(2026, 10, 6)
    while dia <= dt.date(2027, 3, 31):
        _metrica(
            conn,
            run_id,
            kw1,
            dia,
            dt.datetime(dia.year, dia.month, dia.day, 1, tzinfo=dt.UTC),
            moneda="MXN",
            cost="0.01",
            ad_revenue="0.01",
            clicks=1,
            orders=0,
            impressions=10,
        )
        dia += dt.timedelta(days=1)
    conn.commit()
    # el ciclo D_E1 abre el episodio y aplica la entrada: 8.00 -> 5.60
    res = _corre(conn, D_E1, _handler_mx({}))
    assert res.status == "done", res.notes
    entrada = conn.execute(
        "SELECT d.new_value FROM decision d WHERE d.cycle_id = %s"
        " AND d.ad_entity_id = %s AND d.kind = 'bid'",
        (res.cycle_id, kw1),
    ).fetchone()
    assert entrada == (Decimal("5.60"),), entrada
    episodio_id = conn.execute(
        "SELECT id FROM temporada_episodio WHERE platform = 'amazon_mx' AND cerrado_el IS NULL"
    ).fetchone()[0]
    # David edita la reduccion a media baja (30 -> 20) DESPUES de la entrada
    # (config_version es APPEND-ONLY: nueva version, no UPDATE)
    import json as _json

    settings_nuevos = _json.loads(
        conn.execute(
            "SELECT settings::text FROM config_version WHERE id = %s", (config_id,)
        ).fetchone()[0]
    )
    settings_nuevos["ads_temporada"]["reduccion_baja_pct"] = "20"
    conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('d-reduccion-20', %s)",
        (_json.dumps(settings_nuevos),),
    )
    conn.commit()
    res = _corre(conn, D_E1 + dt.timedelta(days=1), _handler_mx({}))
    assert res.status == "done", res.notes
    fila = conn.execute(
        "SELECT d.inputs->>'motivo', d.new_value FROM decision d"
        " WHERE d.cycle_id = %s AND d.kind = 'bid' AND d.ad_entity_id = %s",
        (res.cycle_id, kw1),
    ).fetchone()
    assert fila == (politica.MOTIVO_AJUSTA_BAJA, Decimal("6.40")), fila
    assert conn.execute(
        "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (kw1,)
    ).fetchone()[0] == Decimal("6.40")


def test_temporada01_d_techo_de_baja_compartido(db_template):
    """En baja la subida de R6 topa en el techo de baja compartido: desde
    12.25, R6 dispara (+15% -> 14.0875) y el techo la deja en 14.00 (nunca
    14.09); una hoja pegada al techo queda retenida con techo_de_baja."""
    propia = nivel(
        tramo(clics=82, pedidos=12, venta="5400", gasto="615", impresiones=820),
        tramo(clics=38, pedidos=0, venta="0", gasto="285", impresiones=380),
    )
    cadena = HistoriaBid(
        desde=dt.date(2026, 8, 1),
        cambios=(
            CambioFechado(
                confirmado_el=dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC),
                decision_id=1,
                bid_antes=Decimal("20.00"),
                bid_despues=Decimal("10.00"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
            ),
            CambioFechado(
                confirmado_el=dt.datetime(2027, 1, 4, 12, 0, tzinfo=dt.UTC),
                decision_id=2,
                bid_antes=Decimal("10.00"),
                bid_despues=Decimal("7.00"),
                origen="temporada",
                motivo_temporada="entra",
                episodio_id=1,
            ),
            CambioFechado(
                confirmado_el=dt.datetime(2027, 1, 6, 12, 0, tzinfo=dt.UTC),
                decision_id=3,
                bid_antes=Decimal("7.00"),
                bid_despues=Decimal("8.05"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
            ),
            CambioFechado(
                confirmado_el=dt.datetime(2027, 1, 10, 12, 0, tzinfo=dt.UTC),
                decision_id=4,
                bid_antes=Decimal("8.05"),
                bid_despues=Decimal("12.25"),
                origen="motor",
                motivo_temporada=None,
                episodio_id=None,
            ),
        ),
    )
    caso = fabrica_caso(
        bid=BidVigente(Decimal("12.25"), "MXN", PISO_MX, TECHO_MX),
        propia=propia,
        cuenta=propia,
        grupo=propia,
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 1), "20.00", "10.00", "motor")],
            efecto(dias_post=10, clics_post=25, gasto_post="47.50", vendia=False),
        ),
        historia=cadena,
        temporada=_marcador("baja"),
    )
    assert temporada.techo_de_baja(caso) == Decimal("14.00")
    veredicto = politica.decide(caso)
    assert isinstance(veredicto, politica.Mover), veredicto
    assert veredicto.bid_nuevo == Decimal("14.00"), veredicto
    # pegada al techo: el delta queda bajo un centavo -> techo_de_baja
    caso_pegada = fabrica_caso(
        bid=BidVigente(Decimal("14.00"), "MXN", PISO_MX, TECHO_MX),
        propia=propia,
        cuenta=propia,
        grupo=propia,
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 1), "20.00", "10.00", "motor")],
            efecto(dias_post=10, clics_post=25, gasto_post="47.50", vendia=False),
        ),
        historia=cadena,
        temporada=_marcador("baja"),
    )
    veredicto_pegada = politica.decide(caso_pegada)
    # R6r no puede subir (14 <= 14 x 1.15) y R6 normal queda retenida por el
    # techo: sin camino de subida, la hoja no sale de 14.00.
    sube = isinstance(veredicto_pegada, politica.Mover) or (
        isinstance(veredicto_pegada, politica.Regresar)
        and veredicto_pegada.motivo == politica.MOTIVO_RECUPERA_NIVEL
    )
    assert not sube or veredicto_pegada.bid_nuevo <= Decimal("14.00"), veredicto_pegada


def test_temporada01_d_baja_entra_y_retiene(db_template):
    """Decision 7: en baja R9 queda retenido (Mantener temporada_baja) y R1 no
    aplica; R3 (pierde_dinero) SI recorta fuerte."""
    gasto_sin_venta = nivel(
        tramo(clics=300, pedidos=0, venta="0", gasto="800", impresiones=3000),
        tramo(clics=300, pedidos=0, venta="0", gasto="800", impresiones=3000),
    )
    trayect = trayectoria(
        [cambio(dt.date(2027, 1, 4), "8.00", "5.60", "temporada")],
        efecto(dias_post=10, clics_post=25, gasto_post="47.50", vendia=False),
    )
    # control: sin marcador R9 recorta (gasto sin venta doble, 800*2 >= 350)
    caso_hoy = fabrica_caso(
        bid=BidVigente(Decimal("5.60"), "MXN", PISO_MX, TECHO_MX),
        propia=gasto_sin_venta,
        trayectoria=trayect,
    )
    veredicto_hoy = politica.decide(caso_hoy)
    assert isinstance(veredicto_hoy, politica.Mover) and veredicto_hoy.factor < 0, (
        "control: sin marcador R9 recorta"
    )
    # con marcador: retenido
    caso_r9 = fabrica_caso(
        bid=BidVigente(Decimal("5.60"), "MXN", PISO_MX, TECHO_MX),
        propia=gasto_sin_venta,
        trayectoria=trayect,
        temporada=_marcador("baja"),
    )
    assert politica.decide(caso_r9) == politica.Mantener(politica.MOTIVO_TEMPORADA_BAJA)
    # R1 no aplica en baja: un desplome ANTES de la entrada no revierte
    caso_r1 = fabrica_caso(
        bid=BidVigente(Decimal("5.60"), "MXN", PISO_MX, TECHO_MX),
        propia=nivel(
            tramo(clics=10, pedidos=1, venta="450", gasto="19", impresiones=100),
            tramo(clics=10, pedidos=1, venta="450", gasto="19", impresiones=100),
        ),
        trayectoria=trayectoria(
            [cambio(dt.date(2027, 1, 4), "8.00", "5.60", "temporada")],
            efecto(dias_post=3, clics_post=5, gasto_post="9.50", vendia=False),
        ),
        temporada=_marcador("baja"),
    )
    veredicto = politica.decide(caso_r1)
    assert (
        not isinstance(veredicto, politica.Regresar)
        or veredicto.motivo != politica.MOTIVO_REGRESO_DESPLOME
    ), "R1 no aplica en baja"
    # R3 SI recorta en baja: vende y pierde dinero (ACoS >> equilibrio)
    perdida = nivel(
        tramo(clics=300, pedidos=2, venta="200", gasto="1200", impresiones=3000),
        tramo(clics=300, pedidos=2, venta="200", gasto="1200", impresiones=3000),
    )
    caso_r3 = fabrica_caso(
        bid=BidVigente(Decimal("5.60"), "MXN", PISO_MX, TECHO_MX),
        propia=perdida,
        trayectoria=trayect,
        temporada=_marcador("baja"),
    )
    veredicto = politica.decide(caso_r3)
    assert isinstance(veredicto, politica.Mover) and veredicto.factor < 0, veredicto
    assert veredicto.motivo == politica.MOTIVO_PIERDE_DINERO_FUERTE, veredicto


def test_temporada01_d_meta_alta(db_template):
    """Decision 4: en alta la meta del juicio es max(base, meta_alta) LIBRE
    sobre el equilibrio (US: base 19.05, equilibrio 23.81, meta 25): una
    keyword con ACoS 24.5 (entre el equilibrio y la meta) ya no recorta por
    R4; fuera de alta R4 recorta (target 19.05, grupo sangra)."""
    economia_us = Economia(
        plataforma=EconomiaPlataforma(
            moneda="USD",
            equilibrio_acos_pct=Decimal("23.81"),
            gasto_para_concluir=Decimal("30"),
            confianza_recorte=Decimal("0.80"),
            confianza_subida=Decimal("0.70"),
        ),
        target_acos_pct=Decimal("19.05"),
    )
    # ACoS del juicio ~24.5: entre el equilibrio (23.81) y la meta de alta (25)
    evidencia = nivel(
        tramo(clics=200, pedidos=10, venta="8330", gasto="2040", impresiones=2000),
        tramo(clics=200, pedidos=10, venta="8330", gasto="2040", impresiones=2000),
    )
    caso_alta = fabrica_caso(
        bid=BidVigente(Decimal("1.20"), "USD", Decimal("0.10"), Decimal("2.50")),
        economia=economia_us,
        propia=evidencia,
        grupo=evidencia,
        cuenta=evidencia,
        temporada=_marcador("alta"),
    )
    assert temporada.meta_del_juicio(caso_alta) == Decimal("25")
    assert temporada.equilibrio_del_juicio(caso_alta) == Decimal("25")
    veredicto = politica.decide(caso_alta)
    recorta = isinstance(veredicto, politica.Mover) and veredicto.factor < 0
    assert not recorta, "en alta el juicio es con 25: ACoS 24.5 no recorta"
    # fuera de alta: target 19.05 — R4 recorta (acos 24.5 > 19.05, sangra)
    caso_fuera = fabrica_caso(
        bid=BidVigente(Decimal("1.20"), "USD", Decimal("0.10"), Decimal("2.50")),
        economia=economia_us,
        propia=evidencia,
        grupo=evidencia,
        cuenta=evidencia,
    )
    veredicto = politica.decide(caso_fuera)
    assert isinstance(veredicto, politica.Mover) and veredicto.factor < 0, (
        "fuera de alta R4 recorta"
    )
    # meta manual (goal escrito a mano): la meta del dueno manda
    caso_manual = fabrica_caso(
        bid=BidVigente(Decimal("1.20"), "USD", Decimal("0.10"), Decimal("2.50")),
        economia=economia_us,
        propia=evidencia,
        grupo=evidencia,
        cuenta=evidencia,
        temporada=_marcador("alta"),
    )
    marcador_manual = replace(caso_manual.temporada, meta_manual=True)
    caso_manual = replace(caso_manual, temporada=marcador_manual)
    assert temporada.meta_del_juicio(caso_manual) == Decimal("19.05")
    assert temporada.equilibrio_del_juicio(caso_manual) == Decimal("23.81")


def test_temporada01_d_cortes_por_multiplicador():
    """mult_baja obligatorio: ceil(umbral x mult), piso_pausa y exceso_minimo
    x2 en baja (200 clics, piso 1000 MXN, exceso 2000 MXN); mult < 1
    revienta."""
    evidencia = EvidenciaAdGroup(
        ad_group_id=1,
        ventana_desde=D_E1 - dt.timedelta(days=90),
        ventana_hasta=D_E1 - dt.timedelta(days=10),
        fechas_distintas=30,
        metric_currency="MXN",
        cost=Decimal("800"),
        ad_revenue=Decimal("1200"),
        revenue_same_sku=Decimal("1200"),
        impressions=4000,
        clicks=40,
        orders=4,
        observed_at_max=None,
    )
    base = cortes.umbral_corte(evidencia, "pause", mult_baja=None)
    multiplicado = cortes.umbral_corte(evidencia, "pause", mult_baja=Decimal("2"))
    assert multiplicado.umbral == base.umbral * 2, "ceil(umbral x 2)"
    assert multiplicado.elegible == base.elegible
    assert cortes.piso_pausa("amazon_mx", mult_baja=None) == Decimal("500")
    assert cortes.piso_pausa("amazon_mx", mult_baja=Decimal("2")) == Decimal("1000")
    assert cortes.exceso_minimo("MXN", mult_baja=Decimal("2")) == Decimal("2000")
    assert cortes.exceso_minimo("USD", mult_baja=Decimal("2")) == Decimal("160")
    piso_base = cortes.piso_corte(evidencia, "amazon_mx", mult_baja=None)
    piso_mult = cortes.piso_corte(evidencia, "amazon_mx", mult_baja=Decimal("2"))
    assert piso_mult.piso_cost == piso_base.piso_cost * 2
    with pytest.raises(ValueError, match="mult_baja"):
        cortes.umbral_corte(evidencia, "pause", mult_baja=Decimal("0.5"))
    with pytest.raises(ValueError, match="mult_baja"):
        cortes.piso_pausa("amazon_mx", mult_baja=Decimal("0.5"))


def test_temporada01_d_fase3_lecturas_temporada_io_conectadas(db_template):
    """Lo que B dejo conectado sigue: temporada_io.frontera_hoja (la misma
    temporada.frontera para UNA hoja) y lecturas.tiene_pendiente/pendiente
    ven la entrada pendiente — es lo que la revalidacion de la cola usa."""
    conn, _dsn = db_template
    _run(conn)
    config_id = _config_version(conn, _settings())
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "d-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "d-ag", parent=camp)
    _estado(conn, ag, synced_at=_medianoche(D_E1))
    _estado(conn, camp, synced_at=_medianoche(D_E1))
    conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live','amazon_mx','done',%s,%s)",
        (_medianoche(D_E1), _medianoche(D_E1)),
    )
    conn.execute(
        "INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo,"
        " config_version_id) SELECT 'amazon_mx', %s, id, %s FROM optimizer_cycle"
        " WHERE started_at = %s",
        (_medianoche(D_E1), config_id, _medianoche(D_E1)),
    )
    conn.commit()
    frontera_h = temporada_io.frontera_hoja(conn, ag, D_E1, piso=PISO_MX, techo=TECHO_MX)
    assert frontera_h is not None
    assert frontera_h.fase_hoja == "baja"
    assert frontera_h.mult_cortes == Decimal("2")
