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
from test_api_dashboard import (
    _encola_corte as _encola_corte_d,
)
from test_apply_harvest import TERMINO, _job_en
from test_cycle import _config_version, _siembra_terminos
from test_fabrica_f2 import _semilla_grupo, db_f2
from test_harvest_destino import _base_ciclo, _campana_suelta, _corre
from test_notifica import _canal
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


@_skip_db
def test_a6_ciclo_skipped_no_persiste_harvest_destino():
    """Residual del review de #274 (mutante que persistia la clave en un
    ciclo skipped): con la escalera off el ciclo queda skipped sin correr
    el recorrido y sus notes NO llevan harvest_destino (mismo criterio
    que target: solo cuando el recorrido corrio; el flanco del aviso ya
    cuenta con la ausencia de la clave)."""
    with db_f2("orbit_a6_skipped") as conn:
        _config_version(conn, {"ads_optimizer_mode": "off"})
        res = _corre(conn)
        assert res.status == "skipped"
        notes = json.loads(res.notes)
        assert notes["motivo_skip"] == "escalera_off"
        assert "harvest_destino" not in notes


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


# ---------------------------------------------------------------------------
# A.6 bloque 4: /cortes con destino y hermanas
# ---------------------------------------------------------------------------


def _siembra_corte_harvest(
    conn, *, resuelto_por="grupo", motivo=None, con_goal=True, rol_origen="category_phrase"
):
    """Decision harvest sobre el rol de origen con inputs.goal.harvest
    congelado (forma de _goal_json) + fila encolada en la cola de veto."""
    ciclo = _ciclo_d(conn, platform=PLATFORM)
    config = _config_version_d(conn, {})
    gpo = _semilla_grupo(conn, platform=PLATFORM)
    phrase = gpo["roles"][rol_origen]
    exacta = gpo["roles"]["category_exact"]
    harvest = (
        {
            "campaign_id": exacta["camp_ext"],
            "ad_group_id": exacta["ag_ext"],
            "default_bid": "11.62",
            "moneda": "USD",
            "resuelto_por": resuelto_por,
            "grupo_id": gpo["grupo_id"],
            "motivo": motivo,
        }
        if con_goal
        else None
    )
    inputs = {
        "motivo": "x",
        "target_acos_pct_usado": "20.00",
        "termino": {
            "search_term": TERMINO,
            "cost": "28.2200",
            "ad_revenue": "203.2000",
            "clicks": 63,
            "orders": 2,
            "fechas_distintas": 9,
            "moneda": "USD",
        },
    }
    if harvest is not None:
        inputs["goal"] = {"harvest": harvest}
    dec = _decision_d(
        conn,
        ciclo,
        phrase["ag"],
        kind="harvest",
        config_id=config,
        inputs=inputs,
        search_term=TERMINO,
        moneda="USD",
        window_end=dt.date(2026, 8, 1),
    )
    _encola_corte_d(conn, PLATFORM, phrase["ag"], "harvest", dec, TERMINO)
    return {"gpo": gpo, "phrase": phrase, "exacta": exacta, "dec": dec}


@_skip_db
def test_a6_cortes_harvest_grupo_trae_destino_y_tres_hermanas(monkeypatch):
    """Harvest resuelto por grupo: destino con resuelto_por grupo y hermanas
    = las 3 discovery sin la exacta ni la phrase de origen, en orden
    canonico; nombre = external (la semilla no pone name) salvo UPDATE."""
    with db_f2("orbit_a6_cortes") as conn:
        s = _siembra_corte_harvest(conn)
        conn.execute(
            "UPDATE ad_entity SET name = 'Auto Discovery MX' WHERE id = %s",
            (s["gpo"]["roles"]["auto_discovery"]["camp"],),
        )
        items = _cliente_f2(conn, monkeypatch).get("/api/dashboard/cortes").json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["destino"]["resuelto_por"] == "grupo"
        assert item["destino"]["grupo_id"] == s["gpo"]["grupo_id"]
        assert item["destino"]["motivo_es"] is None
        assert [(h["rol"], h["campaign_id"]) for h in item["hermanas"]] == [
            ("auto_discovery", s["gpo"]["roles"]["auto_discovery"]["camp_ext"]),
            ("category_broad", s["gpo"]["roles"]["category_broad"]["camp_ext"]),
            ("product_targeting", s["gpo"]["roles"]["product_targeting"]["camp_ext"]),
        ]
        assert item["hermanas"][0]["nombre"] == "Auto Discovery MX"
        assert item["hermanas"][1]["nombre"] == s["gpo"]["roles"]["category_broad"]["camp_ext"]


@_skip_db
def test_a6_cortes_hermanas_orden_canonico_no_alfabetico(monkeypatch):
    """Con origen auto_discovery, el orden canonico (phrase antes que
    broad) difiere del alfabetico (broad antes que phrase): el endpoint
    sigue a ROLES_DISCOVERY, la fuente unica importada en el test como
    oraculo. Mata 'ordenar por rol alfabetico' y 'copiar el orden'."""
    from app.apply_harvest import ROLES_DISCOVERY

    with db_f2("orbit_a6_corteso") as conn:
        _siembra_corte_harvest(conn, rol_origen="auto_discovery")
        items = _cliente_f2(conn, monkeypatch).get("/api/dashboard/cortes").json()["items"]
        assert len(items) == 1
        esperado = [r for r in ROLES_DISCOVERY if r != "auto_discovery"]
        assert esperado == ["category_phrase", "category_broad", "product_targeting"]
        assert [h["rol"] for h in items[0]["hermanas"]] == esperado
        assert "category_exact" not in [h["rol"] for h in items[0]["hermanas"]]
        assert "auto_discovery" not in [h["rol"] for h in items[0]["hermanas"]]


@_skip_db
def test_a6_cortes_harvest_terna_sin_hermanas_y_motivo_traducido(monkeypatch):
    """Harvest resuelto por terna: hermanas None y motivo_es traducido
    exacto (migracion_pendiente)."""
    with db_f2("orbit_a6_cortest") as conn:
        _siembra_corte_harvest(conn, resuelto_por="terna", motivo="migracion_pendiente")
        items = _cliente_f2(conn, monkeypatch).get("/api/dashboard/cortes").json()["items"]
        assert len(items) == 1
        assert items[0]["hermanas"] is None
        assert items[0]["destino"]["motivo_es"] == (
            "Destino por terna del goal (migracion a grupo o excepcion pendiente)"
        )


@_skip_db
def test_a6_cortes_harvest_sin_goal_sin_destino_ni_hermanas(monkeypatch):
    """Harvest sin goal en inputs (decisiones pre-F2): destino y hermanas
    None (los test_cortes_ui01_* sobre _db_temporal siguen verdes)."""
    with db_f2("orbit_a6_cortessg") as conn:
        _siembra_corte_harvest(conn, con_goal=False)
        items = _cliente_f2(conn, monkeypatch).get("/api/dashboard/cortes").json()["items"]
        assert len(items) == 1
        assert items[0]["destino"] is None
        assert items[0]["hermanas"] is None


@_skip_db
def test_a6_cortes_hermanas_query_rota_no_tumba_la_pantalla(monkeypatch, caplog):
    """Query de hermanas rota -> hermanas None + warning, status 200. Mata
    al mutante que devuelve [] (la pantalla diria 'no hay hermanas')."""
    import logging

    with db_f2("orbit_a6_cortesrot") as conn:
        _siembra_corte_harvest(conn)
        monkeypatch.setattr(dash, "_SQL_HERMANAS_GRUPO", "SELECT no_existe FROM tampoco_existe")
        with caplog.at_level(logging.WARNING, logger="app.api_dashboard"):
            resp = _cliente_f2(conn, monkeypatch).get("/api/dashboard/cortes")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["hermanas"] is None
        assert "ilegibles" in caplog.text


@_skip_db
def test_a6_pagina_cortes_nombra_hermanas_y_escapa_nombre(monkeypatch):
    """La pagina /cortes nombra las hermanas en la fila y en la
    confirmacion del rechazo; un name con <script> sale escapado."""
    with db_f2("orbit_a6_pagcort") as conn:
        s = _siembra_corte_harvest(conn)
        conn.execute(
            "UPDATE ad_entity SET name = %s WHERE id = %s",
            (
                "<script>alert('xss')</script>",
                s["gpo"]["roles"]["auto_discovery"]["camp"],
            ),
        )
        html = _cliente_f2(conn, monkeypatch).get("/cortes").text
        assert "se negara tambien en:" in html
        assert "category_broad" in html and "product_targeting" in html
        assert "Tampoco se negara en:" in html
        assert "<script>alert('xss')</script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# A.6 bloque 5: aviso en flanco por campana de grupo
# ---------------------------------------------------------------------------


def _avisos_destino(mensajes: list) -> list:
    """Solo los mensajes del aviso de destino (el digest y los de encola
    son otros mensajes del mismo canal)."""
    return [m for m in mensajes if "ALERTA harvest de grupo sin destino" in m["text"]]


def _terna_de(conn, camp: int, camp_ext: str, ag_ext: str) -> None:
    conn.execute(
        "UPDATE ads_optimizer_goal SET harvest_campaign_id = %s,"
        " harvest_ad_group_id = %s WHERE scope = 'campaign' AND ad_entity_id = %s",
        (camp_ext, ag_ext, camp),
    )


@_skip_db
def test_a6_flanco_avisa_una_vez_por_racha(tmp_path, monkeypatch):
    """Flanco por campana: 1er ciclo con el salto -> 1 aviso; 2do sin
    cambios -> 0; corregido -> 0 y saltos vacios; roto de nuevo -> 1."""
    with db_f2("orbit_a6_flanco") as conn, _canal(tmp_path, monkeypatch) as mensajes:
        gpo, run = _base_ciclo(conn, con_terna=True)
        phrase = gpo["roles"]["category_phrase"]
        exacta = gpo["roles"]["category_exact"]
        _terna_de(conn, phrase["camp"], "8001", "8101")
        _siembra_terminos(conn, run, phrase["ag"])

        res1 = _corre(conn)
        assert res1.status in ("done", "degraded")
        assert len(_avisos_destino(mensajes)) == 1
        assert f"(#{phrase['camp']}, rol category_phrase)" in _avisos_destino(mensajes)[0]["text"]
        assert "motivo: destino_inconsistente" in _avisos_destino(mensajes)[0]["text"]

        # Dos ciclos sin la clave entre medias no rompen la racha: el
        # flanco busca el PRIMER notes con harvest_destino. El skipped mata
        # al mutante sin-filtro-de-status; el done pre-A.6, al mutante que
        # toma el primero sin buscar (LIMIT-1 ciego): cualquiera de los dos
        # veria un ciclo sin clave y re-avisaria.
        for status, mode in (("skipped", "off"), ("done", "shadow")):
            conn.execute(
                "INSERT INTO optimizer_cycle (motor, mode, platform, status, notes)"
                " VALUES ('ads_optimizer', %s, 'amazon_us', %s, %s)",
                (mode, status, json.dumps({"skips": {"entidad": {}, "termino": {}}})),
            )
        res2 = _corre(conn)
        assert res2.status in ("done", "degraded")
        assert len(_avisos_destino(mensajes)) == 1, "la misma racha no re-avisa"

        _terna_de(conn, phrase["camp"], exacta["camp_ext"], exacta["ag_ext"])
        res3 = _corre(conn)
        assert len(_avisos_destino(mensajes)) == 1
        assert json.loads(res3.notes)["harvest_destino"]["saltos_grupo"] == {}

        _terna_de(conn, phrase["camp"], "8001", "8101")
        res4 = _corre(conn)
        assert res4.status in ("done", "degraded")
        assert len(_avisos_destino(mensajes)) == 2, "el salto reaparecido vuelve a avisar"


@_skip_db
def test_a6_flanco_suelta_nunca_avisa(tmp_path, monkeypatch):
    """La campana suelta con sin_destino_de_harvest: 0 avisos en todas
    las corridas (el flanco es solo para campanas de grupo)."""
    with db_f2("orbit_a6_flancos") as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _gpo, run = _base_ciclo(conn, con_terna=True)
        camp, ag = _campana_suelta(conn)
        _goal_campana_sin_terna(conn, camp)
        _siembra_terminos(conn, run, ag)
        _corre(conn)
        _corre(conn)
        assert _avisos_destino(mensajes) == []


@_skip_db
def test_a6_flanco_grupo_sin_exacta_cuatro_avisos_una_vez(tmp_path, monkeypatch):
    """Grupo sin fila category_exact (el trigger no bloquea el DELETE con
    goals en estado 1): las 4 discovery saltan con sin_destino_de_harvest
    -> 4 avisos la primera vez, 0 la segunda."""
    with db_f2("orbit_a6_flancoe") as conn, _canal(tmp_path, monkeypatch) as mensajes:
        gpo, _run = _base_ciclo(conn, con_terna=False)
        conn.execute(
            "DELETE FROM campana_grupo_rol WHERE grupo_id = %s AND rol = 'category_exact'",
            (gpo["grupo_id"],),
        )
        res1 = _corre(conn)
        assert res1.status in ("done", "degraded")
        avisos = _avisos_destino(mensajes)
        assert len(avisos) == 4
        assert all("motivo: sin_destino_de_harvest" in m["text"] for m in avisos)
        saltos = json.loads(res1.notes)["harvest_destino"]["saltos_grupo"]
        assert len(saltos) == 4
        _corre(conn)
        assert len(_avisos_destino(mensajes)) == 4, "la misma racha no re-avisa"
