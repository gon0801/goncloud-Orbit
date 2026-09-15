"""Visibilidad y aviso del destino de harvest (FABRICA 02, A.6).

Sobre `db_f2` + `_semilla_grupo` (el banco de `test_api_dashboard` no carga
0018/0038): `notes.harvest_destino` del ciclo, `harvest_job` en el feed de
decisiones, `destino` + `hermanas` en /cortes y el aviso en flanco por
campana de grupo. El DSN de lectura de los endpoints se deriva de
`_test_dsn()` + `conn.info.dbname` (hallazgo PR #267: nunca un DSN fijo).
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from test_api_dashboard import (
    _ciclo as _ciclo_d,
)
from test_api_dashboard import (
    _config_version as _config_version_d,
)
from test_api_dashboard import (
    _decision as _decision_d,
)
from test_apply_harvest import TERMINO, _job_en
from test_cycle import _siembra_terminos
from test_fabrica_f2 import _semilla_grupo, db_f2
from test_harvest_destino import _base_ciclo, _campana_suelta, _corre
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import api_dashboard as dash
from app.dashboard_pagina import _SQL_DECISIONES_TOTAL
from app.main import app

PLATFORM = "amazon_us"

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _goal_campana_sin_terna(conn, camp: int) -> None:
    """Goal de campana habilitado SIN config de harvest (estado 1 del
    trigger de 0038: los tres campos NULL)."""
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode) VALUES ('campaign', %s, 55, 0.10, 2.50,"
        " 'USD', NULL, NULL, NULL, true, 'live')",
        (camp,),
    )


# ---------------------------------------------------------------------------
# A.6 bloque 2: notes.harvest_destino del ciclo
# ---------------------------------------------------------------------------


@_skip_db
def test_a6_notes_trae_resueltos_por_grupo_y_sin_saltos():
    """Tras el ciclo, notes.harvest_destino cuenta por ad group evaluado:
    los 4 discovery resuelven por grupo (la exacta-origen salta con
    origen_es_destino y no cuenta) y no hay saltos de grupo."""
    with db_f2("orbit_a6_notas") as conn:
        _base_ciclo(conn, con_terna=True)
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        bloque = json.loads(res.notes)["harvest_destino"]
        assert bloque["resueltos"] == {"grupo": 4, "excepcion": 0, "terna": 0}
        assert bloque["saltos_grupo"] == {}


@_skip_db
def test_a6_terna_distinta_deja_salto_de_grupo_en_notes():
    """El escenario de test_a1_terna_campaign_distinta: la phrase con terna
    de goal propio a otro destino deja saltos_grupo[camp] =
    destino_inconsistente y no cuenta como resuelto."""
    with db_f2("orbit_a6_salto") as conn:
        gpo, run = _base_ciclo(conn, con_terna=True)
        phrase = gpo["roles"]["category_phrase"]
        conn.execute(
            "UPDATE ads_optimizer_goal SET harvest_campaign_id = '8001',"
            " harvest_ad_group_id = '8101' WHERE scope = 'campaign' AND ad_entity_id = %s",
            (phrase["camp"],),
        )
        _siembra_terminos(conn, run, phrase["ag"])
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        bloque = json.loads(res.notes)["harvest_destino"]
        assert bloque["resueltos"]["grupo"] == 3
        assert bloque["saltos_grupo"] == {str(phrase["camp"]): "destino_inconsistente"}
        skips = json.loads(res.notes)["skips"]["termino"]
        assert skips.get("destino_inconsistente") == 1


@_skip_db
def test_a6_campana_suelta_salta_en_skips_pero_no_en_saltos_grupo():
    """Una campana suelta sin nada es el estado normal de hoy: su
    sin_destino_de_harvest vive en skips.termino, JAMAS en saltos_grupo
    (ahi solo entran campanas DE GRUPO)."""
    with db_f2("orbit_a6_suelta") as conn:
        _base_ciclo(conn, con_terna=True)
        camp, ag = _campana_suelta(conn)
        _goal_campana_sin_terna(conn, camp)
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        notes = json.loads(res.notes)
        assert notes["harvest_destino"]["saltos_grupo"] == {}
        assert "sin_destino_de_harvest" not in notes["harvest_destino"]["saltos_grupo"].values()
        assert ag is not None  # la suelta existe y se evaluo sin grupo


@_skip_db
def test_a6_campana_suelta_con_termino_salta_sin_aviso_de_grupo():
    """Con un termino harvestable, la suelta sin nada salta con
    sin_destino_de_harvest en skips.termino — y sigue sin entrar a
    saltos_grupo (el aviso en flanco es solo para campanas de grupo)."""
    with db_f2("orbit_a6_sueltat") as conn:
        _gpo, run = _base_ciclo(conn, con_terna=True)
        camp, ag = _campana_suelta(conn)
        _goal_campana_sin_terna(conn, camp)
        _siembra_terminos(conn, run, ag)
        res = _corre(conn)
        assert res.status in ("done", "degraded")
        notes = json.loads(res.notes)
        assert notes["skips"]["termino"].get("sin_destino_de_harvest") == 1
        assert notes["harvest_destino"]["saltos_grupo"] == {}


# ---------------------------------------------------------------------------
# A.6 bloque 3: fase del job en el feed de decisiones
# ---------------------------------------------------------------------------


def _siembra_feed(conn) -> dict:
    """ciclo + config + grupo + decision harvest sobre la phrase + decision
    bid sin job (los helpers son de test_api_dashboard: solo tocan 0001,
    que esta en ORDEN_F2)."""
    ciclo = _ciclo_d(conn, platform=PLATFORM)
    config = _config_version_d(conn, {})
    gpo = _semilla_grupo(conn, platform=PLATFORM)
    phrase = gpo["roles"]["category_phrase"]
    dec_harv = _decision_d(
        conn,
        ciclo,
        phrase["ag"],
        kind="harvest",
        config_id=config,
        inputs={"motivo": "x", "target_acos_pct_usado": "20.00"},
        search_term=TERMINO,  # el trigger exige job.termino == decision.termino
        moneda="USD",
        window_end=dt.date(2026, 8, 1),
    )
    dec_bid = _decision_d(
        conn,
        ciclo,
        phrase["ag"],
        kind="bid",
        config_id=config,
        inputs={"motivo": "sin_banda", "target_acos_pct_usado": "20.00"},
        old_value=Decimal("1.00"),
        new_value=Decimal("1.00"),
        moneda="USD",
    )
    return {
        "ciclo": ciclo,
        "gpo": gpo,
        "phrase_ag": phrase["ag"],
        "dec_harv": dec_harv,
        "dec_bid": dec_bid,
    }


def _cliente_f2(conn, monkeypatch) -> TestClient:
    """TestClient contra la db_f2 (DSN derivado de _test_dsn() + dbname:
    hallazgo PR #267, nunca un DSN fijo)."""
    monkeypatch.setenv("ORBIT_DSN_READ", make_conninfo(_test_dsn(), dbname=conn.info.dbname))
    return TestClient(app)


@_skip_db
def test_a6_feed_muestra_fase_del_job_con_hermanas_pendientes(monkeypatch):
    """El item harvest del feed trae harvest_job con fase_es exacto para
    hermanas_negadas y las pendientes del external_ids; la decision bid
    (sin job) trae None."""
    with db_f2("orbit_a6_feed") as conn:
        s = _siembra_feed(conn)
        jid = _job_en(
            conn,
            s["dec_harv"],
            s["phrase_ag"],
            "hermanas_negadas",
            external_ids={
                "hermanas_pendientes": {"product_targeting": "pt_no_acepta_negative_keyword"}
            },
        )
        items = _cliente_f2(conn, monkeypatch).get("/api/dashboard/decisiones").json()["items"]
        item = next(i for i in items if i["id"] == s["dec_harv"])
        assert item["harvest_job"] == {
            "id": jid,
            "fase": "hermanas_negadas",
            "fase_es": "Negando el termino en las campanas hermanas",
            "hermanas_pendientes": {"product_targeting": "pt_no_acepta_negative_keyword"},
        }
        assert next(i for i in items if i["id"] == s["dec_bid"])["harvest_job"] is None


@_skip_db
def test_a6_feed_job_done_sin_pendientes_trae_dict_vacio(monkeypatch):
    """Job done con hermanas_pendientes {}: fase Aplicado y dict vacio
    (no None: el job si existe y se leyo)."""
    with db_f2("orbit_a6_feeddone") as conn:
        s = _siembra_feed(conn)
        jid = _job_en(
            conn,
            s["dec_harv"],
            s["phrase_ag"],
            "done",
            external_ids={"hermanas_pendientes": {}},
        )
        items = _cliente_f2(conn, monkeypatch).get("/api/dashboard/decisiones").json()["items"]
        assert next(i for i in items if i["id"] == s["dec_harv"])["harvest_job"] == {
            "id": jid,
            "fase": "done",
            "fase_es": "Aplicado",
            "hermanas_pendientes": {},
        }


def test_a6_fases_harvest_vocabulario_completo_y_traducido():
    """FASES_ES_HARVEST cubre las seis fases del CHECK (0001 + 0038) y cada
    etiqueta difiere del id (mata al mutante que deja hermanas_negadas
    fuera o la traduce con el crudo)."""
    assert set(dash.FASES_ES_HARVEST) == {
        "pending",
        "negative_created",
        "exact_created",
        "hermanas_negadas",
        "done",
        "failed",
    }
    for fase, etiqueta in dash.FASES_ES_HARVEST.items():
        assert etiqueta != fase, fase


@_skip_db
def test_a6_total_decisiones_no_cambia_con_job():
    """_SQL_DECISIONES_TOTAL cuenta decisiones, no jobs: dos decisiones y
    un job sembrado cuentan 2 (el LATERAL vive solo en el SELECT)."""
    with db_f2("orbit_a6_total") as conn:
        s = _siembra_feed(conn)
        antes = conn.execute(_SQL_DECISIONES_TOTAL).fetchone()[0]
        _job_en(conn, s["dec_harv"], s["phrase_ag"], "hermanas_negadas")
        despues = conn.execute(_SQL_DECISIONES_TOTAL).fetchone()[0]
        assert (antes, despues) == (2, 2)


@_skip_db
def test_a6_pagina_decisiones_muestra_etiqueta_del_job(monkeypatch):
    """La pagina /decisiones (HTML) muestra la etiqueta de la fase y el id
    del job en la celda Kind."""
    with db_f2("orbit_a6_pagdec") as conn:
        s = _siembra_feed(conn)
        jid = _job_en(
            conn,
            s["dec_harv"],
            s["phrase_ag"],
            "hermanas_negadas",
            external_ids={
                "hermanas_pendientes": {"product_targeting": "pt_no_acepta_negative_keyword"}
            },
        )
        html = _cliente_f2(conn, monkeypatch).get("/decisiones").text
        assert "Negando el termino en las campanas hermanas" in html
        assert f"job #{jid}" in html
        assert "product_targeting: pt_no_acepta_negative_keyword" in html
