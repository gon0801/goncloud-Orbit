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

import pytest
from test_cycle import _config_version, _entidad, _estado, _run
from test_temporada01_b import (
    D_E1,
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

from app.optimizer.caso import CasoHoja
from app.optimizer.politica import decide

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
    _siembra_metricas(conn, [camp, kw1])
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

    # 4) El caso de la sombra queda congelado y rejuega el MISMO veredicto y
    # destino: sin el, un recupera_nivel no es reproducible (sus topes y su
    # redondeo esconden el multiplicador de R6r).
    assert dif["caso_sombra"]["recuperacion"], dif["caso_sombra"]
    assert dif["caso_sombra"]["temporada"], dif["caso_sombra"]
    veredicto_sombra = decide(CasoHoja.desde_json(dif["caso_sombra"]))
    assert type(veredicto_sombra).__name__ == "Regresar", veredicto_sombra
    assert veredicto_sombra.motivo == "recupera_nivel", veredicto_sombra
    assert veredicto_sombra.bid_nuevo == destino, veredicto_sombra


@pytest.mark.parametrize("modo_recuperacion", ["shadow", "live"])
def test_temporada01_h_practica_no_pierde_la_mano_del_dueno(db_template, modo_recuperacion):
    """Temporada en vivo + subida rapida con memoria de 90 dias: el historial que
    decide la temporada NO se acorta con esa memoria, asi que la entrada no
    recorta una puja que el dueno cambio y el sync ya leyo.

    Sin el arreglo, la lectura de 90 dias pierde el cambio de Orbit de hace 120
    dias, `dueno_al_final` no se detecta y la entrada baja 7.00 a 4.90 (Devin,
    PR 730).

    El caso `live` importa de verdad: con temporada y subida rapida en `live` es
    la config que quedo encendida en H.2, y la subida rapida sigue siendo la que
    acorta la lectura salvo que la temporada mande. R6r aplica su propia memoria
    en `techo_conocido`, asi que la lectura compartida no necesita acortarla."""
    conn, _dsn = db_template
    _run(conn)
    config_id = _config_version(
        conn,
        {
            **_settings(modo_temporada="live"),
            "ads_bid_recuperacion_amazon_mx": modo_recuperacion,
            "ads_bid_recuperacion_memoria_dias_amazon_mx": 90,
        },
    )
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "h2-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "h2-ag", parent=camp)
    _estado(conn, ag, synced_at=_medianoche(D_E1))
    _estado(conn, camp, synced_at=_medianoche(D_E1))
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
        (camp, _medianoche(D_E1) - dt.timedelta(days=15)),
    )
    # Orbit dejo la puja en 8.00 hace 120 dias; despues el dueno la puso en 7.00
    # en Amazon y el sync ya trae ese valor (sin fila en el ledger de Orbit).
    kw = _kw(conn, ag, "h2-kw", "kw dueno", "7.00")
    _siembra_metricas(conn, [camp, kw])
    _cambio_confirmado(
        conn,
        kw,
        config_id,
        dia=D_E1 - dt.timedelta(days=120),
        antes="6.00",
        despues="8.00",
        motivo="bajo_target",
    )
    conn.commit()

    handler = _handler_mx({"h2-kw": "7.00"})
    res = _corre(conn, D_E1, handler)
    assert res.status == "done", res.notes

    entradas = [
        fila[0]
        for fila in conn.execute(
            "SELECT d.ad_entity_id FROM decision d"
            " WHERE d.cycle_id = %s AND d.kind = 'bid'"
            "   AND d.inputs->>'motivo' = 'entra_temporada_baja'",
            (res.cycle_id,),
        ).fetchall()
    ]
    assert kw not in entradas, "la entrada recorto la puja del dueno"
    vigente = conn.execute(
        "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (kw,)
    ).fetchone()[0]
    assert vigente == Decimal("7.00"), vigente


@pytest.mark.parametrize("modo_temporada", [None, "live"])
def test_temporada01_h_practica_compara_recuperacion_sin_temporada_en_shadow(
    db_template, modo_temporada
):
    """La subida rapida en practica se compara tambien con la temporada apagada
    o en vivo: `casos()` genera el caso de sombra con la pausa base y
    `notes.temporada` guarda la diferencia aunque no haya temporada que contar."""
    conn, _dsn = db_template
    _run(conn)
    _config_version(
        conn,
        {
            **_settings(modo_temporada=modo_temporada),
            "ads_bid_recuperacion_amazon_mx": "shadow",
        },
    )
    _goal_mx(conn)
    camp = _entidad(conn, "amazon_mx", "campaign", "h3-camp")
    ag = _entidad(conn, "amazon_mx", "ad_group", "h3-ag", parent=camp)
    _estado(conn, ag, synced_at=_medianoche(DIA))
    _estado(conn, camp, synced_at=_medianoche(DIA))
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda) VALUES (%s, %s, 1000.00, 'MXN')",
        (camp, _medianoche(DIA) - dt.timedelta(days=15)),
    )
    kw = _kw(conn, ag, "h3-kw", "kw 3", "2.16")
    _siembra_metricas(conn, [camp, kw])
    _evidencia_caso_d(
        conn,
        kw,
        DIA - dt.timedelta(days=90),
        DIA - dt.timedelta(days=10),
        DIA - dt.timedelta(days=1),
    )
    _cambio_confirmado(
        conn,
        kw,
        conn.execute("SELECT max(id) FROM config_version").fetchone()[0],
        dia=DIA - dt.timedelta(days=40),
        antes="8.00",
        despues="2.16",
        motivo="pierde_dinero_fuerte",
    )
    conn.commit()

    res = _corre(conn, DIA, _handler_mx({"h3-kw": "2.16"}))
    assert res.status == "done", res.notes

    motivos = [
        fila[0]
        for fila in conn.execute(
            "SELECT d.inputs->>'motivo' FROM decision d"
            " WHERE d.cycle_id = %s AND d.kind = 'bid' AND d.ad_entity_id = %s",
            (res.cycle_id, kw),
        ).fetchall()
    ]
    assert "recupera_nivel" not in motivos, motivos

    notas = json.loads(res.notes)
    seccion = notas["temporada"]
    assert seccion["comparadas"] >= 1, seccion
    difs = [d for d in seccion["diferencias"] if d["hoja_id"] == kw]
    assert len(difs) == 1, difs
    assert difs[0]["motivo_sombra"] == "recupera_nivel", difs[0]
    # El destino es el salto topado por el techo conocido (8.00); lo que importa
    # es que R6r se evaluo y supero un paso de R6.
    assert Decimal(difs[0]["destino_sombra"]) > Decimal("2.16") * Decimal("1.15"), difs[0]
    assert Decimal(difs[0]["destino_sombra"]) <= Decimal("8.00"), difs[0]
