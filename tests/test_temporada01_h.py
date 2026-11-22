"""TEMPORADA 01, bloque H: la practica compara la subida rapida (R6r).

La decision 12 del diseno pide que en practica el caso que decide vaya SIN
marcadores y se decida un segundo caso CON ellos (temporada, historia,
recuperacion y presupuesto); solo las diferencias se guardan en
`notes.temporada`. Hasta el bloque H la ruta de recuperacion solo se armaba con
el interruptor en `live`, asi que la practica nunca comparaba R6r y H.1 no
podia revisar `recupera_nivel`, m* ni el gasto extra antes de encender.

Esta prueba corre un ciclo real con `ads_temporada_amazon_mx=shadow` y
`ads_bid_recuperacion_amazon_mx=shadow`:

- el caso que decide va sin marcadores (el ciclo sigue decidiendo lo de hoy);
- la sombra decide con marcadores y registra la diferencia `recupera_nivel`
  con su destino;
- el salto de R6r NO se envia a Amazon en practica.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from test_cycle import _config_version, _entidad, _estado, _run
from test_temporada01_b import (
    _cambio_confirmado,
    _corre,
    _goal_mx,
    _handler_mx,
    _kw,
    _medianoche,
    _settings,
    _siembra_metricas,
)
from test_temporada01_c import _evidencia_caso_d, _evidencia_cuenta

DIA = dt.date(2027, 2, 15)  # febrero: normal (la entrada de la baja no estorba)


def test_temporada01_h_practica_compara_recuperacion(db_template):
    """Con la temporada y la subida rapida en practica, el ciclo anota el
    `recupera_nivel` que R6r habria decidido y no lo envia."""
    conn, _dsn = db_template
    _run(conn)
    config_id = _config_version(
        conn,
        {
            **_settings(modo_temporada="shadow"),
            "ads_bid_recuperacion_amazon_mx": "shadow",
        },
    )
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "h-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "h-ag", parent=camp)
    _estado(conn, ag, synced_at=_medianoche(DIA))
    _estado(conn, camp, synced_at=_medianoche(DIA))
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
        (camp, _medianoche(DIA) - dt.timedelta(days=15)),
    )
    kw1 = _kw(conn, ag, "h-kw1", "kw 1", "2.16")
    _siembra_metricas(conn, [kw1])
    _evidencia_caso_d(
        conn,
        kw1,
        DIA - dt.timedelta(days=90),
        DIA - dt.timedelta(days=10),
        DIA - dt.timedelta(days=1),
    )
    ag2 = _entidad(conn, "amazon_mx", "ad_group", "h-ag2", parent=camp)
    _estado(conn, ag2, synced_at=_medianoche(DIA))
    kw_cuenta = _kw(conn, ag2, "h-kw-cuenta", "kw cuenta", "6.00")
    _siembra_metricas(conn, [kw_cuenta])
    _evidencia_cuenta(conn, kw_cuenta, DIA - dt.timedelta(days=90), DIA - dt.timedelta(days=10))
    _cambio_confirmado(
        conn,
        kw1,
        config_id,
        dia=DIA - dt.timedelta(days=40),
        antes="8.00",
        despues="2.16",
        motivo="pierde_dinero_fuerte",
    )
    conn.commit()

    handler = _handler_mx({"h-kw1": "2.16", "h-kw-cuenta": "6.00"})
    res = _corre(conn, DIA, handler)
    assert res.status == "done", res.notes

    # 1) El caso que decide NO lleva marcadores (la practica no cambia lo de hoy).
    congelado = conn.execute(
        "SELECT inputs->'caso' FROM decision WHERE cycle_id = %s AND ad_entity_id = %s",
        (res.cycle_id, kw1),
    ).fetchone()
    if congelado is not None:
        caso = congelado[0]
        assert "recuperacion" not in caso, caso
        assert "temporada" not in caso, caso
        assert "historia" not in caso, caso

    # 2) Nada de R6r se envia: la decision real jamas lleva el motivo.
    motivos = [
        fila[0]
        for fila in conn.execute(
            "SELECT d.inputs->>'motivo' FROM decision d WHERE d.cycle_id = %s AND d.kind = 'bid'",
            (res.cycle_id,),
        ).fetchall()
    ]
    assert "recupera_nivel" not in motivos, motivos
    vigente = conn.execute(
        "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (kw1,)
    ).fetchone()[0]
    assert vigente < Decimal("3.00"), vigente

    # 3) La sombra SI lo decide y lo anota, con su destino.
    notas = json.loads(res.notes)
    seccion = notas["temporada"]
    assert seccion["comparadas"] >= 1, seccion
    difs = [d for d in seccion["diferencias"] if d["hoja_id"] == kw1]
    assert len(difs) == 1, difs
    dif = difs[0]
    assert dif["motivo_sombra"] == "recupera_nivel", dif
    assert dif["sombra"] == "Regresar", dif
    assert dif["motivo_real"] != "recupera_nivel", dif
    destino = Decimal(dif["destino_sombra"])
    assert destino == Decimal("7.10"), dif
    assert destino > Decimal("2.16") * Decimal("1.15"), "el salto supera un paso de R6"
