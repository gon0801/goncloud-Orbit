"""TEMPORADA 01, bloque E: lectura fresca del bid y la mano del dueno.

- `test_temporada01_e_mano_del_dueno_excluye` (escribe en Amazon): la entrada
  8.00 -> 5.60 de tres keywords encuentra en Amazon 7.00 porque el dueno movio
  el bid entre la decision y el envio. El aplicador NO manda ningun PUT, deja
  `bid_cambiado_fuera` en decision_sin_aplicar, guarda la exclusion del
  episodio con el bid visto y el cache queda con lo leido. Al dia siguiente no
  hay entrada y el bid sigue en 7.00; la keyword con evidencia de R4 recorta
  con las reglas de hoy (sin HOLD de la baja y con mult 1, no x2) y la que
  tiene evidencia de subida no recibe `Recupera`.
- `test_temporada01_e_excluida_sin_recupera_control` (dinero): el mismo caso
  con UNA diferencia, la exclusion del episodio; sin ella `decide` devuelve el
  salto de R6r, con ella no.

La lectura fresca corre en el ciclo real de MX (Amazon simulado); el control
puro aisla la puerta de R6r porque con el bid del dueno (7.00) el techo no deja
aire para un salto y la prueba de punta a punta solo podria pasar por el techo.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

from test_cycle import _config_version, _entidad, _estado, _metrica, _run
from test_temporada01_b import (
    D_E1,
    _corre,
    _goal_mx,
    _handler_mx,
    _kw,
    _medianoche,
    _settings,
    _siembra_metricas,
)
from test_temporada01_c import _evidencia_caso_d, _evidencia_cuenta

from app.optimizer import politica, recuperacion


def _handler_e(remoto: dict[str, str]):
    """Amazon simulado de test_temporada01_b + la lista de requests vistos:

    el PUT de un bid y el readback por LIST. Contar los PUT es la evidencia de
    'cero PUT' cuando el dueno movio el bid.
    """
    base = _handler_mx(remoto)
    vistos: list[str] = []

    def handler(request):
        vistos.append(request.method)
        return base(request)

    return handler, vistos


def _metricas_r4(conn, kw: int, desde: dt.date, corte: dt.date, hasta: dt.date) -> None:
    """Evidencia de R4 (grupo_sangra_vendedora) del ciclo real: 3 pedidos
    pesados con ACoS ~37 % (sobre la meta 20, debajo del equilibrio) y el
    gasto del grupo sobre 1.15 x meta x venta. Hora 2 sobre la siembra
    neutral para que mande por observed_at."""
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test-e-r4') RETURNING id"
    ).fetchone()[0]
    dias = (hasta - desde).days + 1
    for i in range(dias):
        dia = desde + dt.timedelta(days=i)
        maduro = dia <= corte
        pedido = maduro and (corte - dia).days < 3
        _metrica(
            conn,
            run_id,
            kw,
            dia,
            dt.datetime(dia.year, dia.month, dia.day, 2, tzinfo=dt.UTC),
            moneda="MXN",
            cost="2.00" if maduro else "0.00",
            ad_revenue="100.00" if pedido else "0.00",
            clicks=1 if maduro else 0,
            orders=1 if pedido else 0,
            impressions=100,
        )


def _decisiones_bid(
    conn, cycle_id: int
) -> list[tuple[int, str, Decimal | None, str | None, str | None]]:
    """Las decisiones bid del ciclo: (hoja, motivo, nuevo, mult_cortes,
    fase_hoja) con lo congelado del caso."""
    return conn.execute(
        "SELECT d.ad_entity_id, d.inputs->>'motivo', d.new_value,"
        "       d.inputs->'caso'->'temporada'->'frontera'->>'mult_cortes',"
        "       d.inputs->'caso'->'temporada'->'frontera'->>'fase_hoja'"
        "  FROM decision d"
        " WHERE d.cycle_id = %s AND d.kind = 'bid'"
        " ORDER BY d.ad_entity_id",
        (cycle_id,),
    ).fetchall()


def _bid_cache(conn, kw: int) -> Decimal:
    return conn.execute(
        "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (kw,)
    ).fetchone()[0]


def test_temporada01_e_mano_del_dueno_excluye(db_template):
    """Fila E.1 de punta a punta: la lectura fresca frena el PUT, la exclusion
    del episodio queda escrita y el dia siguiente juzga la hoja con las reglas
    de hoy, sin entrada, sin regreso y sin Recupera."""
    conn, _dsn = db_template
    _run(conn)
    _config_version(conn, {**_settings(), "ads_bid_recuperacion_amazon_mx": "live"})
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "e-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "e-ag", parent=camp)
    camp_r4 = _entidad(conn, "amazon_mx", "campaign", "e-camp-r4")
    ag_r4 = _entidad(conn, "amazon_mx", "ad_group", "e-ag-r4", parent=camp_r4)
    for entidad in (ag, camp, ag_r4, camp_r4):
        _estado(conn, entidad, synced_at=_medianoche(D_E1))

    kw_mano = _kw(conn, ag, "e-mano", "kw mano", "8.00")
    kw_rec = _kw(conn, ag, "e-rec", "kw rec", "8.00")
    kw_r4 = _kw(conn, ag_r4, "e-r4", "kw r4", "8.00")
    # La previa de cuenta en CVR 3 % que el caso D necesita para su m*: hoja de
    # relleno en OTRO ad group, fuera del juicio.
    ag_cuenta = _entidad(conn, "amazon_mx", "ad_group", "e-ag-cuenta", parent=camp)
    _estado(conn, ag_cuenta, synced_at=_medianoche(D_E1))
    kw_cuenta = _kw(conn, ag_cuenta, "e-cuenta", "kw cuenta", "6.00")
    conn.execute(
        "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s", (kw_cuenta,)
    )
    # Presupuesto medido de las dos campanas: sin el, BUD no alcanza a medir la
    # racha y toda subida queda retenida como presupuesto_topado.
    for campana in (camp, camp_r4):
        conn.execute(
            "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
            " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
            (campana, _medianoche(D_E1) - dt.timedelta(days=15)),
        )
    _siembra_metricas(conn, [kw_mano, kw_rec, kw_r4, kw_cuenta])
    desde = D_E1 - dt.timedelta(days=90)
    corte = D_E1 - dt.timedelta(days=10)
    hasta = D_E1 - dt.timedelta(days=1)
    _evidencia_caso_d(conn, kw_rec, desde, corte, hasta)
    _evidencia_cuenta(conn, kw_cuenta, desde, hasta)
    _metricas_r4(conn, kw_r4, desde, corte, hasta)
    conn.commit()

    # El dueno movio las tres a 7.00 en Amazon; el cache (lo que Orbit vio al
    # decidir) sigue en 8.00.
    handler, vistos = _handler_e(
        {"e-mano": "7.00", "e-rec": "7.00", "e-r4": "7.00", "e-cuenta": "6.00"}
    )
    res = _corre(conn, D_E1, handler)
    assert res.status == "done", res.notes

    entradas = conn.execute(
        "SELECT d.ad_entity_id, d.new_value, d.inputs->>'temporada_episodio_id'"
        "  FROM decision d"
        " WHERE d.cycle_id = %s AND d.inputs->>'motivo' = 'entra_temporada_baja'"
        " ORDER BY d.ad_entity_id",
        (res.cycle_id,),
    ).fetchall()
    assert [f[0] for f in entradas] == sorted([kw_mano, kw_rec, kw_r4]), entradas
    assert [f[1] for f in entradas] == [Decimal("5.60")] * 3, "entrada 8.00 x 0.70"
    assert all(f[2] is not None for f in entradas), "la entrada va estampada con su episodio"
    assert vistos.count("PUT") == 0, "la lectura fresca frena el PUT del destino viejo"

    sin_aplicar = conn.execute(
        "SELECT d.ad_entity_id, sa.motivo, sa.detalle->>'leido'"
        "  FROM decision_sin_aplicar sa JOIN decision d ON d.id = sa.decision_id"
        " ORDER BY d.ad_entity_id"
    ).fetchall()
    assert [(f[1], f[2]) for f in sin_aplicar] == [("bid_cambiado_fuera", "7.00")] * 3, sin_aplicar
    assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0] == 0, (
        "sin PUT no nace fila del ledger"
    )
    assert conn.execute("SELECT count(*) FROM decision_application").fetchone()[0] == 0, (
        "el destino viejo no se aplica"
    )

    exclusiones = conn.execute(
        "SELECT x.ad_entity_id, x.motivo, x.bid_visto, x.decision_id, x.episodio_id,"
        "       (SELECT desenlace FROM temporada_efecto ef"
        "         WHERE ef.decision_id = x.decision_id)"
        "  FROM temporada_exclusion x"
        " ORDER BY x.ad_entity_id"
    ).fetchall()
    assert len(exclusiones) == 3, exclusiones
    for hoja, motivo, bid_visto, decision_id, episodio_id, desenlace in exclusiones:
        assert motivo == "mano_del_dueno"
        assert bid_visto == Decimal("7.00")
        assert desenlace == "mano_del_dueno", "el efecto cierra como mano del dueno"
        assert episodio_id is not None, "la exclusion vive en el episodio abierto"
        assert (
            decision_id
            == conn.execute(
                "SELECT id FROM decision WHERE cycle_id = %s AND ad_entity_id = %s"
                " AND kind = 'bid'",
                (res.cycle_id, hoja),
            ).fetchone()[0]
        )
    assert all(_bid_cache(conn, kw) == Decimal("7.00") for kw in (kw_mano, kw_rec, kw_r4)), (
        "el cache queda con LO LEIDO, no con lo enviado ni con lo esperado"
    )

    # Dia siguiente, misma baja: ninguna entrada (ni regreso) para una keyword
    # que es terreno del dueno en ese episodio.
    handler_2, vistos_2 = _handler_e(
        {"e-mano": "7.00", "e-rec": "7.00", "e-r4": "7.00", "e-cuenta": "6.00"}
    )
    res_2 = _corre(conn, D_E1 + dt.timedelta(days=1), handler_2)
    assert res_2.status == "done", res_2.notes
    decisiones_2 = {
        hoja: (motivo, nuevo, mult, fase)
        for hoja, motivo, nuevo, mult, fase in _decisiones_bid(conn, res_2.cycle_id)
    }
    assert not any(
        motivo in ("entra_temporada_baja", "ajusta_temporada_baja", "sale_temporada_baja")
        for motivo, _nuevo, _mult, _fase in decisiones_2.values()
    ), decisiones_2
    assert not any(
        motivo == recuperacion.MOTIVO_RECUPERA_NIVEL for motivo, _n, _m, _f in decisiones_2.values()
    ), "la subida rapida es terreno del dueno: no hay Recupera"

    assert kw_mano not in decisiones_2, "sin evidencia no hay decision y el bid no se mueve"
    assert _bid_cache(conn, kw_mano) == Decimal("7.00")
    motivo_r4, nuevo_r4, mult_r4, fase_r4 = decisiones_2[kw_r4]
    assert motivo_r4 == politica.MOTIVO_GRUPO_PIERDE_VENDEDORA, decisiones_2
    assert nuevo_r4 == Decimal("6.16"), "R4 de hoy: 7.00 x 0.88, sin recorte extra"
    assert mult_r4 == "1", "terreno del dueno: sin el doble de exigencia de la baja"
    assert fase_r4 == "normal", "lo juzgan las reglas de hoy, no las de la baja"
    assert (
        conn.execute(
            "SELECT count(*) FROM decision_sin_aplicar sa JOIN decision d"
            " ON d.id = sa.decision_id WHERE d.cycle_id = %s AND d.ad_entity_id = %s",
            (res_2.cycle_id, kw_r4),
        ).fetchone()[0]
        == 0
    ), "el recorte de R4 si sale"
    assert _bid_cache(conn, kw_r4) == Decimal("6.16")

    assert kw_rec in decisiones_2, "la evidencia de subida se juzga con las reglas de hoy"
    motivo_rec, nuevo_rec, mult_rec, fase_rec = decisiones_2[kw_rec]
    assert motivo_rec == politica.MOTIVO_BAJO_TARGET, "R6 de hoy: 7.00 x 1.15"
    assert nuevo_rec == Decimal("8.05")
    assert (mult_rec, fase_rec) == ("1", "normal"), "sin las reglas de la baja"
    assert vistos_2.count("PUT") == 2, "salen el recorte de R4 y la subida de R6"


def test_temporada01_e_excluida_sin_recupera_control():
    """Control de una sola diferencia: la misma hoja reducida en la baja y la
    misma evidencia; sin la exclusion del episodio R6r salta, con ella no."""
    from test_temporada01_c import _cadena_baja, _caso_baja, _nivel_caso_b

    caso = replace(
        _caso_baja(_cadena_baja("20.00", "10.00"), "7.00"),
        grupo=_nivel_caso_b(),
        cuenta=_nivel_caso_b(),
    )
    assert recuperacion.destino_recuperacion(
        caso, a_subida=Decimal(6), arg_subida=Decimal("1.00")
    ) == Decimal("14.00"), "sin exclusion la evidencia sostiene el salto de R6r"
    assert isinstance(politica.decide(caso), politica.Regresar)

    excluido = replace(
        caso,
        temporada=replace(
            caso.temporada,
            excluida_en=frozenset({1}),
            frontera=replace(caso.temporada.frontera, fase_hoja="normal", mult_cortes=Decimal(1)),
        ),
    )
    assert (
        recuperacion.destino_recuperacion(excluido, a_subida=Decimal(6), arg_subida=Decimal("1.00"))
        is None
    )
    assert not isinstance(politica.decide(excluido), politica.Regresar)
