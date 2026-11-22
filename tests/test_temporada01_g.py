"""TEMPORADA 01, bloque G: propuestas de presupuesto que aprueba el dueno.

`test_temporada01_g_propuestas_absolutas` (dinero) cubre los momentos del
diseno: baja 300 -> 210, fin de la baja 210 -> 300, alta 300 -> 375 una sola
vez, 250 a mano que no produce fin de baja y el tope humano de X.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from test_aplica_ajuste import _skip_db
from test_temporada01_b import MESES_PRUEBA

from app.optimizer import temporada
from app.optimizer.temporada_presupuesto import propuesta_presupuesto

CONFIG = {
    "ads_temporada": {
        "version": temporada.VERSION_TEMPORADA,
        "meses": list(MESES_PRUEBA),
        "reduccion_baja_pct": "30",
        "dias_preparacion": 21,
        "mult_cortes_baja": "2",
        "aumento_presupuesto_alta_pct": "25",
        "fin_anticipado": None,
        "mantener_pausadas": [],
    }
}
TOPADA = temporada.CampanaTopada(pct=Decimal("0.90"), dias=5)
TECHO_MXN = Decimal("10708")
CUENTA = dict(
    estacion=None,
    config=None,
    episodio=None,
    historial=(),
    hoy=dt.date(2027, 1, 10),
    inicio_bloque_alto=None,
    dias_topados_7d=None,
    topada=TOPADA,
    techo_humano=TECHO_MXN,
    moneda="MXN",
)


def _config():
    return temporada.desde_settings(CONFIG)


def _estacion(dia: dt.date):
    return temporada.resuelve(_config(), dia)


def _episodio(*, abierto: dt.date, cerrado: dt.date | None, id_: int = 1):
    return SimpleNamespace(id=id_, abierto_el=abierto, cerrado_el=cerrado)


def _propuesta(**ajas):
    datos = {
        **CUENTA,
        "config": _config(),
        "estacion": _estacion(ajas.get("hoy", CUENTA["hoy"])),
    }
    datos.update(ajas)
    datos["estacion"] = _estacion(datos["hoy"])
    return propuesta_presupuesto(**datos)


def test_temporada01_g_propuestas_absolutas():
    """Baja, fin, alta, mano del dueno y tope humano con montos absolutos."""
    baja = _propuesta(
        hoy=dt.date(2027, 1, 10),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=None),
        historial=((dt.date(2026, 12, 1), Decimal("300")),),
    )
    assert baja is not None
    assert (baja.actual, baja.propuesto, baja.motivo) == (
        Decimal("300"),
        Decimal("210.00"),
        "baja",
    )
    assert "bajar de 300.00 a 210.00 MXN al día" in baja.frase

    # Un cambio de presupuesto desde la apertura apaga la baja.
    assert (
        _propuesta(
            hoy=dt.date(2027, 1, 10),
            episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=None),
            historial=(
                (dt.date(2026, 12, 1), Decimal("300")),
                (dt.date(2027, 1, 5), Decimal("210")),
            ),
        )
        is None
    )

    fin = _propuesta(
        hoy=dt.date(2027, 2, 5),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
        historial=(
            (dt.date(2026, 12, 1), Decimal("300")),
            (dt.date(2027, 1, 5), Decimal("210")),
        ),
    )
    assert fin is not None
    assert (fin.propuesto, fin.motivo) == (Decimal("300"), "fin_de_baja")
    assert "subir de 210.00 a 300.00 MXN al día" in fin.frase

    # 250 a mano: no es la propuesta de baja, no hay fin de la baja.
    assert (
        _propuesta(
            hoy=dt.date(2027, 2, 5),
            episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
            historial=(
                (dt.date(2026, 12, 1), Decimal("300")),
                (dt.date(2027, 1, 5), Decimal("250")),
            ),
        )
        is None
    )

    alta = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=((dt.date(2027, 2, 5), Decimal("300")),),
        inicio_bloque_alto=dt.date(2027, 3, 1),
        dias_topados_7d=5,
    )
    assert alta is not None
    assert (alta.propuesto, alta.motivo) == (Decimal("375.00"), "alta_sin_presupuesto")
    assert "se queda sin presupuesto; subir de 300.00 a 375.00 MXN al día" in alta.frase

    # Ya hubo un aumento desde la preparacion: no se repite.
    assert (
        _propuesta(
            hoy=dt.date(2027, 3, 10),
            episodio=None,
            historial=(
                (dt.date(2027, 2, 5), Decimal("300")),
                (dt.date(2027, 3, 5), Decimal("375")),
            ),
            inicio_bloque_alto=dt.date(2027, 3, 1),
            dias_topados_7d=5,
        )
        is None
    )

    # Tope humano de X: 10,000 x 1.25 = 12,500 se queda en 10,708.
    tope = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=((dt.date(2027, 2, 5), Decimal("10000")),),
        inicio_bloque_alto=dt.date(2027, 3, 1),
        dias_topados_7d=5,
    )
    assert tope is not None
    assert tope.propuesto == TECHO_MXN
    assert "topado por el techo humano" in tope.frase


@_skip_db
def test_temporada01_g_presupuesto_misma_campana_en_orden(monkeypatch):
    """A aprueba 375 antes que B ordene 270; B termina primero. Al aplicar A,
    el candado por campana y campo ve la orden posterior aceptada: sin PUT,
    409 y el final es 270."""
    import json

    import httpx
    import pytest as _pytest
    from test_aplica_ajuste import (
        EXTERNA,
        _creds_fake,
        _db,
        _handler_campanas,
        _plan,
        _siembra,
    )

    from app import apply, apply_ajustes
    from app.ads.campana_config import config_vigente
    from app.campana_ajustes import CambiarPresupuesto

    with _db("orbit_t01_g_orden") as conn:
        camp, cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)

        # A quedo insertada (intencion) antes que B.
        plan_a = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("375")))
        a_id = conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, regresa_a)"
            " VALUES (%s, 'amazon_mx', 'presupuesto', %s, %s, %s, 'dueno',"
            " 'APLICAR AJUSTE', NULL) RETURNING id",
            (camp, cfg, json.dumps(plan_a.despues.como_json()), plan_a.huella() + "#a"),
        ).fetchone()[0]
        conn.commit()

        # B ordena 270 DESPUES y se aplica primero.
        plan_b = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("270")))
        apply_ajustes.aplica_ajuste_campana(
            conn,
            plan_b,
            huella=plan_b.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        assert config_vigente(conn, camp).presupuesto_diario == Decimal("270")

        cliente = apply._cliente_ajuste("amazon_mx", transport=httpx.MockTransport(handler))
        with _pytest.raises(apply_ajustes.AjusteEnCurso, match="Ya mandaste otra orden"):
            apply_ajustes._aplica_y_confirma(
                conn,
                cliente,
                "amazon_mx",
                "presupuesto",
                plan_a.despues,
                plan_a.ajuste,
                campana_id=camp,
                ajuste_id=a_id,
                previa=plan_a.antes,
            )

        assert len([r for r in vistos if r.method == "PUT"]) == 1, "A no envia"
        assert config_vigente(conn, camp).presupuesto_diario == Decimal("270")
        assert (
            conn.execute(
                "SELECT confirmado_el FROM campana_ajuste WHERE id = %s", (a_id,)
            ).fetchone()[0]
            is None
        ), "A queda sin confirmar"
