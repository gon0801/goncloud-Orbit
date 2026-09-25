"""Cierre humano de propuestas de campana (ADS PROTECCION C.5).

DoD fila 103 + runbook H6.3/Decisiones 2: el dueno pausa en Amazon o
descarta; Orbit cierra solo con readback PAUSED mismo campaignId/profile,
sin escribir PAUSE/RESUME de campana. Descarte por endpoint autenticado
x-orbit-token en app/api_write.py con rol DB minimo.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import httpx
import pytest
from test_api_write import TOKEN, _db_con_rol_admin, _secrets_token
from test_cycle import _db_temporal, _entidad, _estado, _metrica, _run
from test_propuestas_campana import _propuesta_abierta, _sincroniza
from test_schema import _postgres_obligatorio_ausente

from app import propuestas_campana as p

RAIZ = Path(__file__).resolve().parents[1]
SQL40 = (RAIZ / "migrations" / "0040_ads_report_result.sql").read_text(encoding="utf-8")
SQL43 = (RAIZ / "migrations" / "0043_ads_propuesta_descarte_admin.sql").read_text(encoding="utf-8")

DECIDIDO = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
KW_C4 = dict(platform="amazon_us", settings={}, target_margen=None)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _metricas_caras(conn, camp, run):
    for day in range(2, 10):
        fecha = dt.date(2026, 9, day)
        _metrica(conn, run, camp, fecha, DECIDIDO - dt.timedelta(days=1), cost="15", ad_revenue="0")
    _metrica(
        conn,
        run,
        camp,
        dt.date(2026, 9, 23),
        DECIDIDO - dt.timedelta(hours=1),
        cost="1",
        ad_revenue="0",
    )


def _campana_cara(conn, external="3909", *, con_goal=True):
    """Campana con metricas que abren propuesta open via el motor."""
    camp = _entidad(conn, "amazon_us", "campaign", external)
    _estado(conn, camp, synced_at=DECIDIDO - dt.timedelta(hours=2))
    if con_goal:
        conn.execute(
            """INSERT INTO ads_optimizer_goal
                 (scope, platform, target_acos_pct, bid_floor, bid_ceiling,
                  bid_currency, enabled, mode)
                 VALUES ('platform', 'amazon_us', 20, 0.10, 2.50, 'USD', true, 'shadow')"""
        )
    _metricas_caras(conn, camp, _run(conn))
    return camp


def _claves_json(valor) -> set[str]:
    if isinstance(valor, dict):
        claves = set(valor)
        for sub in valor.values():
            claves |= _claves_json(sub)
        return claves
    if isinstance(valor, list):
        claves = set()
        for sub in valor:
            claves |= _claves_json(sub)
        return claves
    return set()


@_skip_db
def test_descartar_cierra_open_con_actor_y_no_toca_otras():
    """C.5: descartar pasa open->dismissed con actor y rastro; otras intactas."""
    with _db_temporal("orbit_c5_descarta") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        otra = _entidad(conn, "amazon_us", "campaign", "3910")
        prop = _propuesta_abierta(conn, camp)
        otra_prop = _propuesta_abierta(conn, otra, external="3910")
        resp = p.descartar_propuesta(conn, prop, "david")
        assert resp["id"] == prop
        assert resp["status"] == "dismissed"
        assert resp["close_reason"] == "descarte_manual"
        fila = conn.execute(
            "SELECT status, closed_at IS NOT NULL, close_reason,"
            " close_evidence->>'actor' FROM ads_campaign_proposal WHERE id = %s",
            (prop,),
        ).fetchone()
        assert fila == ("dismissed", True, "descarte_manual", "david")
        assert conn.execute(
            "SELECT status FROM ads_campaign_proposal WHERE id = %s", (otra_prop,)
        ).fetchone() == ("open",)


@_skip_db
def test_descartar_repetido_no_duplica_y_avisa_cierre():
    """C.5: segundo descarte (o fila terminal) -> PropuestaYaCerrada, sin cambios."""
    with _db_temporal("orbit_c5_doble") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        prop = _propuesta_abierta(conn, camp)
        p.descartar_propuesta(conn, prop, "david")
        antes = conn.execute(
            "SELECT closed_at FROM ads_campaign_proposal WHERE id = %s", (prop,)
        ).fetchone()
        with pytest.raises(p.PropuestaYaCerrada):
            p.descartar_propuesta(conn, prop, "david")
        despues = conn.execute(
            "SELECT closed_at FROM ads_campaign_proposal WHERE id = %s", (prop,)
        ).fetchone()
        assert antes == despues
        assert conn.execute("SELECT count(*) FROM ads_campaign_proposal").fetchone() == (1,)


@_skip_db
def test_descartar_inexistente_y_actor_vacio():
    """C.5: propuesta inexistente -> PropuestaInexistente; actor vacio -> ValueError."""
    with _db_temporal("orbit_c5_404") as (conn, _extra):
        with pytest.raises(p.PropuestaInexistente):
            p.descartar_propuesta(conn, 424242, "david")
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        prop = _propuesta_abierta(conn, camp)
        with pytest.raises(ValueError, match="actor"):
            p.descartar_propuesta(conn, prop, "   ")


@_skip_db
def test_descartar_no_muta_amazon():
    """C.5: descartar hace cero HTTP (Amazon o no): con httpx bloqueado pasa."""
    with _db_temporal("orbit_c5_nohttp") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        prop = _propuesta_abierta(conn, camp)

        def _revienta(*args, **kwargs):
            raise AssertionError("descartar hizo HTTP")

        orig_send = httpx.Client.send
        orig_asend = httpx.AsyncClient.send
        httpx.Client.send = _revienta
        httpx.AsyncClient.send = _revienta
        try:
            resp = p.descartar_propuesta(conn, prop, "david")
        finally:
            httpx.Client.send = orig_send
            httpx.AsyncClient.send = orig_asend
        assert resp["status"] == "dismissed"


def test_modulo_propuestas_sin_cliente_amazon_en_runtime():
    """C.5: cero llamadas de mutacion de campana por construccion — el modulo
    de propuestas no importa clientes Amazon en runtime (ni write ni read)."""
    arbol = ast.parse((RAIZ / "app" / "propuestas_campana.py").read_text(encoding="utf-8"))
    importados = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)
    assert not {m for m in importados if m.startswith("app.ads.")}, importados


@_skip_db
def test_endpoint_descartar_auth_ciclo_y_errores(tmp_path, monkeypatch):
    """C.5: endpoint x-orbit-token: 401 sin token, 200+shape, 404, 409 doble."""
    from fastapi.testclient import TestClient

    from app.main import app

    _secrets_token(tmp_path, monkeypatch)
    with _db_con_rol_admin("orbit_c5_ep") as (semilla, dsn_admin, _lectura):
        semilla.execute(SQL43)
        camp = _entidad(semilla, "amazon_us", "campaign", "3909")
        prop = _propuesta_abierta(semilla, camp)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        cliente = TestClient(app, raise_server_exceptions=False)
        ruta = f"/api/ads-optimizer/propuestas-campana/{prop}/descartar"
        resp = cliente.post(ruta, json={"actor": "david"})
        assert resp.status_code == 401
        resp = cliente.post(ruta, json={"actor": "david"}, headers={"x-orbit-token": TOKEN})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "dismissed"
        assert resp.json()["id"] == prop
        resp = cliente.post(
            "/api/ads-optimizer/propuestas-campana/424242/descartar",
            json={"actor": "david"},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 404
        resp = cliente.post(ruta, json={"actor": "david"}, headers={"x-orbit-token": TOKEN})
        assert resp.status_code == 409
        # C3: actor en blanco pasa pydantic (min_length=1) y lo caza la
        # funcion -> 422, nunca 500 (aun sobre propuesta ya cerrada: el
        # actor se valida primero).
        resp = cliente.post(ruta, json={"actor": "   "}, headers={"x-orbit-token": TOKEN})
        assert resp.status_code == 422, resp.text


@_skip_db
def test_grant_admin_minimo_solo_cierre():
    """C.5: 0043 da a app_admin UPDATE solo en 5 columnas de cierre; sin
    INSERT ni UPDATE de dinero/evidencia de medicion."""
    with _db_temporal("orbit_c5_grant") as (conn, _extra):
        conn.execute(SQL43)
        for columna in ("status", "closed_at", "last_seen_at", "close_reason", "close_evidence"):
            assert (
                conn.execute(
                    "SELECT has_column_privilege("
                    "'app_admin', 'ads_campaign_proposal', %s, 'UPDATE')",
                    (columna,),
                ).fetchone()[0]
                is True
            )
        assert (
            conn.execute(
                "SELECT has_table_privilege('app_admin', 'ads_campaign_proposal', 'INSERT')"
            ).fetchone()[0]
            is False
        )
        for columna in ("cost", "revenue", "excess", "campaign_id"):
            assert (
                conn.execute(
                    "SELECT has_column_privilege("
                    "'app_admin', 'ads_campaign_proposal', %s, 'UPDATE')",
                    (columna,),
                ).fetchone()[0]
                is False
            )


@_skip_db
def test_paused_externo_cierra_con_snapshot_sin_autor():
    """C.5: open + readback PAUSED -> paused_external con snapshot y fecha;
    la evidencia NO infiere autor ni causalidad."""
    with _db_temporal("orbit_c5_ext") as (conn, _extra):
        camp = _campana_cara(conn)
        assert _sincroniza(conn, decidido=DECIDIDO, **KW_C4) == {"exceso_economico": 1}
        pausado = DECIDIDO + dt.timedelta(minutes=5)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED', synced_at = %s WHERE ad_entity_id = %s",
            (pausado, camp),
        )
        _sincroniza(conn, decidido=pausado, **KW_C4)
        fila = conn.execute(
            "SELECT status, closed_at, close_reason, close_evidence,"
            " campaign_status, status_synced_at FROM ads_campaign_proposal"
        ).fetchone()
        assert fila[0] == "paused_external"
        assert fila[1] is not None
        assert fila[2] == "estado_pausado_externo"
        assert fila[4] == "PAUSED"
        assert fila[5] == pausado
        cierre = fila[3]["cierre"]
        assert cierre["campaign_status"] == "PAUSED"
        assert cierre["campaign_id"] == "3909"
        assert "profile_id" in cierre
        prohibidas = {"actor", "autor", "dueno", "dueño", "causa", "causalidad", "culpa", "quien"}
        assert not (_claves_json(fila[3]) & prohibidas)


@_skip_db
def test_profile_id_se_resuelve_de_ads_report_result():
    """C.5: al cerrar por PAUSED externo, profile_id sale del ultimo written
    de la plataforma (0040); sin fuente queda NULL (regla 3)."""
    with _db_temporal("orbit_c5_prof") as (conn, _extra):
        camp = _campana_cara(conn)
        run = _run(conn)
        conn.execute(
            "INSERT INTO ads_report_result (ingest_run_id, profile_id, platform,"
            " report_name, status) VALUES (%s, 777, 'amazon_mx', 'campanas', 'written')",
            (run,),
        )
        conn.execute(
            "INSERT INTO ads_report_result (ingest_run_id, profile_id, platform,"
            " report_name, status) VALUES (%s, 4242, 'amazon_us', 'campanas', 'written')",
            (run,),
        )
        assert _sincroniza(conn, decidido=DECIDIDO, **KW_C4) == {"exceso_economico": 1}
        pausado = DECIDIDO + dt.timedelta(minutes=5)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED', synced_at = %s WHERE ad_entity_id = %s",
            (pausado, camp),
        )
        _sincroniza(conn, decidido=pausado, **KW_C4)
        fila = conn.execute("SELECT status, profile_id FROM ads_campaign_proposal").fetchone()
        assert fila == ("paused_external", 4242)


@_skip_db
def test_profile_id_null_sin_fuente():
    """C.5: sin ads_report_result escrito, profile_id queda NULL (no inventado)."""
    with _db_temporal("orbit_c5_noprof") as (conn, _extra):
        camp = _campana_cara(conn)
        assert _sincroniza(conn, decidido=DECIDIDO, **KW_C4) == {"exceso_economico": 1}
        pausado = DECIDIDO + dt.timedelta(minutes=5)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED', synced_at = %s WHERE ad_entity_id = %s",
            (pausado, camp),
        )
        _sincroniza(conn, decidido=pausado, **KW_C4)
        assert conn.execute("SELECT profile_id FROM ads_campaign_proposal").fetchone() == (None,)


@_skip_db
def test_revisada_con_enabled_sigue_pendiente():
    """C.5: propuesta revisada con campana ENABLED sigue open (nada autocierra)."""
    with _db_temporal("orbit_c5_open") as (conn, _extra):
        _camp = _campana_cara(conn)
        assert _sincroniza(conn, decidido=DECIDIDO, **KW_C4) == {"exceso_economico": 1}
        _sincroniza(conn, decidido=DECIDIDO + dt.timedelta(minutes=5), **KW_C4)
        assert conn.execute("SELECT status, closed_at FROM ads_campaign_proposal").fetchone() == (
            "open",
            None,
        )


@_skip_db
def test_cron_repetido_no_duplica_ni_reabre():
    """C.5: descartada + syncs repetidos: una fila, dismissed; paused_external
    + PAUSED repetido: mantener, sin segunda fila."""
    with _db_temporal("orbit_c5_repite") as (conn, _extra):
        _camp = _campana_cara(conn)
        assert _sincroniza(conn, decidido=DECIDIDO, **KW_C4) == {"exceso_economico": 1}
        prop = conn.execute("SELECT id FROM ads_campaign_proposal").fetchone()[0]
        p.descartar_propuesta(conn, prop, "david")
        _sincroniza(conn, decidido=DECIDIDO + dt.timedelta(minutes=5), **KW_C4)
        _sincroniza(conn, decidido=DECIDIDO + dt.timedelta(minutes=6), **KW_C4)
        assert conn.execute("SELECT status FROM ads_campaign_proposal").fetchall() == [
            ("dismissed",)
        ]
        # Segundo episodio: PAUSED externo repetido no duplica.
        camp2 = _campana_cara(conn, "3911", con_goal=False)
        _sincroniza(conn, decidido=DECIDIDO + dt.timedelta(minutes=7), **KW_C4)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED', synced_at = %s WHERE ad_entity_id = %s",
            (DECIDIDO + dt.timedelta(minutes=8), camp2),
        )
        _sincroniza(conn, decidido=DECIDIDO + dt.timedelta(minutes=9), **KW_C4)
        _sincroniza(conn, decidido=DECIDIDO + dt.timedelta(minutes=10), **KW_C4)
        filas = conn.execute(
            "SELECT status FROM ads_campaign_proposal WHERE campaign_id = %s", (camp2,)
        ).fetchall()
        assert filas == [("paused_external",)]


@_skip_db
def test_cola_descendiente_no_aplica_tras_paused():
    """C.5: campana PAUSED (readback) -> la cola de sus hojas no aplica:
    discard pre-claim con campana_no_enabled, cero mutacion."""
    from test_apply_cola import (
        _aplicador,
        _decision_corte,
        _encola_fila,
        _handler_cortes,
        _mutaciones,
        _payload_pause,
        _semilla,
    )
    from test_apply_cola import (
        _db_temporal as _db_cola,
    )

    from app.apply import MOTIVO_CAMPANA_NO_ENABLED
    from app.apply_cola import libera_vencidos

    with _db_cola("orbit_c5_cola") as conn:
        ids = _semilla(conn)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s",
            (ids["camp"],),
        )
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, vistos = _handler_cortes()
        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=ids["ahora"],
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )
        assert res.descartadas == [MOTIVO_CAMPANA_NO_ENABLED]
        assert res.aplicadas == 0
        assert _mutaciones(vistos) == []
        assert conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone() == (
            "discarded",
        )


def test_migracion_0043_parsea():
    """C.5: 0043 parsea como SQL."""
    import pglast

    assert pglast.parse_sql(SQL43)
    assert "0043" in SQL43


def test_descartar_auth_query_no_autentica_y_cuerpo_invalido_422(tmp_path, monkeypatch):
    """C.5: el token solo viaja en header (query JAMAS autentica, sellado 18);
    actor vacio o largo -> 422 antes de tocar la DB."""
    from fastapi.testclient import TestClient

    import app.api_write as api_write
    from app.main import app

    _secrets_token(tmp_path, monkeypatch)
    # El cuerpo valida despues de abrir la conexion (orden del veto): DSN
    # falso + conn falsa para llegar al 422 sin Postgres.
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://fake:***@127.0.0.1:5432/fake")

    class _ConnFake:
        def close(self):
            pass

    monkeypatch.setattr(api_write, "connect", lambda dsn, **kw: _ConnFake())
    cliente = TestClient(app, raise_server_exceptions=False)
    ruta = "/api/ads-optimizer/propuestas-campana/7/descartar"
    assert cliente.post(ruta, json={"actor": "david"}).status_code == 401
    assert (
        cliente.post(ruta + "?x-orbit-token=" + TOKEN, json={"actor": "david"}).status_code == 401
    )
    for cuerpo in ({"actor": ""}, {"actor": "x" * 201}, {}):
        resp = cliente.post(ruta, json=cuerpo, headers={"x-orbit-token": TOKEN})
        assert resp.status_code == 422, (cuerpo, resp.text)


def test_queries_cierre_parsean_en_postgres():
    """C.5: el SQL nuevo del cierre parsea (descartar, estado, perfil)."""
    import pglast

    for query in (
        p._SQL_DESCARTAR,
        p._SQL_ESTADO_PROPUESTA,
        p._SQL_PROFILE_POR_PLATAFORMA,
    ):
        assert pglast.parse_sql(query.replace("%s", "NULL"))


def test_cero_ruta_sp_campaigns_en_camino_de_escritura():
    """C.5: cero llamadas de mutacion de campana — el cliente de escritura y
    los aplicadores no nombran el recurso /sp/campaigns (solo keywords,
    targets, negativos y productAds; la estructura se LEE en structure_api)."""
    for rel in (
        "app/ads/write.py",
        "app/apply.py",
        "app/apply_cola.py",
        "app/apply_harvest.py",
        "app/apply_harvest_reconciliacion.py",
        "app/propuestas_campana.py",
    ):
        fuente = (RAIZ / rel).read_text(encoding="utf-8")
        assert "/sp/campaigns" not in fuente, f"{rel} toca el recurso campana"


@_skip_db
def test_descartada_requiere_ventana_limpia_para_episodio_nuevo():
    """C.5: tras el descarte el riesgo repetido mantiene (sin duplicar); una
    ventana sin riesgo marca reset y el riesgo siguiente abre episodio nuevo."""
    from test_cycle import _db_temporal as _db_motor

    with _db_motor("orbit_c5_renace") as (conn, _extra):
        _camp = _campana_cara(conn)
        assert _sincroniza(conn, decidido=DECIDIDO, **KW_C4) == {"exceso_economico": 1}
        prop = conn.execute("SELECT id FROM ads_campaign_proposal").fetchone()[0]
        p.descartar_propuesta(conn, prop, "david")
        _sincroniza(conn, decidido=DECIDIDO + dt.timedelta(minutes=5), **KW_C4)
        assert conn.execute("SELECT status FROM ads_campaign_proposal").fetchall() == [
            ("dismissed",)
        ]


def test_cortes_muestra_descartar_solo_en_open():
    """C.5: la pantalla ofrece Descartar solo en propuestas open (canal
    visible); las cerradas muestran em-dash, sin form."""
    from test_ui import _ctx_cortes

    from app import ui

    ctx = _ctx_cortes()
    ctx["propuestas_campana"] = [
        {
            "id": 5,
            "platform": "amazon_us",
            "campaign_external_id": "123456",
            "nombre": "A1U Exact",
            "status": "open",
            "motivo": "exceso_economico",
            "cost": "642.6500",
            "sales30d": "477.4000",
            "currency": "USD",
            "target_pct": "17.56",
            "target_source": "goal_campana",
            "excess": "558.8000",
            "acos_pct": "134.61",
            "window_start": "2026-08-16",
            "window_end": "2026-09-14",
            "campaign_status": "ENABLED",
            "aviso_estado": "pending",
            "riesgo_ignorando_estado": False,
        },
        {
            "id": 6,
            "platform": "amazon_us",
            "campaign_external_id": "123457",
            "nombre": "AU2 Exact",
            "status": "paused_external",
            "motivo": "campana_no_enabled",
            "cost": "200.0000",
            "sales30d": "0.0000",
            "currency": "USD",
            "target_pct": "20.00",
            "target_source": "goal_plataforma",
            "excess": "200.0000",
            "acos_pct": None,
            "window_start": "2026-08-16",
            "window_end": "2026-09-14",
            "campaign_status": "PAUSED",
            "aviso_estado": "pending",
            "riesgo_ignorando_estado": True,
        },
    ]
    html = ui.templates.env.get_template("cortes.html").render(**ctx)
    assert html.count('data-descartar="') == 1
    assert 'data-descartar="5"' in html
    assert "Confirmar descarte" in html
    assert "NO toca Amazon" in html
