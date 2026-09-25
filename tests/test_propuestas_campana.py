"""Regresiones de las propuestas economicas de campana (ADS PROTECCION C.4)."""

import datetime as dt
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pglast
import psycopg
import pytest
from test_cycle import (
    _config_version,
    _corre,
    _db_temporal,
    _entidad,
    _estado,
    _metrica,
    _obs,
    _run,
    _siembra_maestra,
)
from test_schema import _postgres_obligatorio_ausente

from app import propuestas_campana as p
from app.api import _SQL_PROPUESTAS_CAMPANA, campaign_proposals

FECHA = dt.date(2026, 9, 9)


def _sincroniza(conn, *, decidido, **kw):
    leidas = p.lee_evaluaciones(conn, decidido=decidido, **kw)
    return p.guarda_evaluaciones(conn, leidas, decidido)


def _dato(cost, revenue, *, moneda="USD", fechas=7, status="ENABLED", target="20"):
    return p.DatoCampana(
        campaign_id=3909,
        platform="amazon_us",
        external_id="123456",
        nombre="A1U Exact",
        status=status,
        status_synced_at=dt.datetime(2026, 9, 24, tzinfo=dt.UTC),
        window_start=FECHA - dt.timedelta(days=29),
        window_end=FECHA,
        fechas_distintas=fechas,
        cost=Decimal(cost) if cost is not None else None,
        revenue=Decimal(revenue) if revenue is not None else None,
        currency=moneda,
        observed_at=dt.datetime(2026, 9, 23, tzinfo=dt.UTC),
        target_pct=Decimal(target) if target is not None else None,
        target_source="margen_plataforma" if target is not None else None,
        goal_enabled=True,
    )


def test_a1u_au2_y_cero_ventas_medido_cruzan_limite():
    # Ventana real de campaña 21-ago..19-sep: A1U y AU2 (USD).
    for cost, revenue, target in (
        ("642.65", "477.40", "17.563"),
        ("416.40", "256.00", "17.563"),
        ("80", "0", "20"),
    ):
        resultado = p.evalua(_dato(cost, revenue, target=target))
        assert resultado.riesgo is True
        assert resultado.motivo == "exceso_economico"
    assert p.evalua(_dato("79.99", "0")).riesgo is False


def test_venta_tardia_target_y_estado_externo_revalidan():
    assert p.evalua(_dato("100", "100")).riesgo is True
    assert p.evalua(_dato("100", "500")).riesgo is False
    assert p.evalua(_dato("100", "100", target="60")).riesgo is False
    assert p.evalua(_dato("200", "0", status="PAUSED")).riesgo is False
    estado_viejo = _dato("200", "0", status="PAUSED")
    assert (
        p.evalua(estado_viejo, decidido=dt.datetime(2026, 9, 27, tzinfo=dt.UTC)).motivo
        == "estado_vencido"
    )


def test_none_moneda_inmadurez_y_goal_inhabilitado_abstienen():
    assert p.evalua(_dato("100", None)).motivo == "revenue_ausente"
    assert p.evalua(_dato(None, "0")).motivo == "cost_ausente"
    assert p.evalua(_dato("100", "0", moneda="MXN")).motivo == "moneda_invalida"
    assert p.evalua(_dato("100", "0", fechas=6)).motivo == "ventana_incompleta"
    assert p.evalua(_dato("100", "0", target=None)).motivo == "target_ausente"
    assert p.evalua(_dato("100", "0", status="ARCHIVED")).motivo == "campana_no_enabled"
    assert p.evalua(replace(_dato("100", "0"), goal_enabled=False)).motivo == "goal_disabled"
    pausada_optout = replace(_dato("100", "0", status="PAUSED"), goal_enabled=False)
    assert p.evalua(pausada_optout).motivo == "campana_no_enabled"
    inmaduro = replace(_dato("100", "0"), window_end=dt.date(2026, 9, 15))
    assert (
        p.evalua(inmaduro, decidido=dt.datetime(2026, 9, 24, tzinfo=dt.UTC)).motivo
        == "ventana_inmadura"
    )


def test_episodio_cerrado_no_reabre_hasta_ventana_sin_riesgo():
    assert p.transicion(None, riesgo=True, status="ENABLED", reset=False) == "abrir"
    assert p.transicion("open", riesgo=True, status="ENABLED", reset=False) == "actualizar"
    assert p.transicion("dismissed", riesgo=True, status="ENABLED", reset=False) == "mantener"
    assert p.transicion("dismissed", riesgo=False, status="ENABLED", reset=False) == "reiniciar"
    assert p.transicion("dismissed", riesgo=True, status="ENABLED", reset=True) == "abrir"
    assert (
        p.transicion("open", riesgo=True, status="PAUSED", reset=False) == "cerrar_estado_pausado"
    )
    # B2: PAUSED con riesgo (sombra) deja fila visible, no accionable.
    assert p.transicion(None, riesgo=True, status="PAUSED", reset=False) == "registrar_pausado"
    assert p.transicion(None, riesgo=False, status="PAUSED", reset=False) == "mantener"
    assert (
        p.transicion("paused_observed", riesgo=True, status="PAUSED", reset=False)
        == "actualizar_pausado"
    )
    assert p.transicion("paused_external", riesgo=True, status="PAUSED", reset=False) == "mantener"
    # Obs5: reactivada con riesgo abre episodio nuevo; paused_external solo
    # se toca para eso.
    assert p.transicion("paused_observed", riesgo=True, status="ENABLED", reset=False) == "abrir"
    assert p.transicion("paused_external", riesgo=True, status="ENABLED", reset=False) == "abrir"
    assert (
        p.transicion("paused_external", riesgo=False, status="ENABLED", reset=False) == "reiniciar"
    )


def test_migracion_y_queries_parsean_en_postgres():
    sql = (
        Path(__file__).resolve().parents[1] / "migrations" / "0042_ads_campaign_proposal.sql"
    ).read_text(encoding="utf-8")
    assert pglast.parse_sql(sql)
    assert "target_pct NUMERIC NOT NULL" in sql
    for query in (
        p._SQL_CAMPANAS,
        p._SQL_MAX_FECHA_ASOF,
        p._SQL_AGREGADO_ASOF,
        p._SQL_ULTIMO,
        p._SQL_INSERTAR,
        p._SQL_INSERTAR_PAUSADO,
        p._SQL_ACTUALIZAR,
        p._SQL_ACTUALIZAR_PAUSADO,
        p._SQL_ACTUALIZAR_ESTADO,
        p._SQL_AVISOS_PENDIENTES,
        p._SQL_MARCA_AVISO,
    ):
        assert pglast.parse_sql(query.replace("%s", "NULL"))
    assert pglast.parse_sql(_SQL_PROPUESTAS_CAMPANA.replace("%s", "NULL"))


def test_api_muestra_dinero_como_texto_y_solo_lee_propuestas():
    class Conn:
        row_factory = None

        def execute(self, sql, params):
            assert sql == _SQL_PROPUESTAS_CAMPANA
            assert params == ("open", "open", "amazon_us", "amazon_us", 50)
            return self

        def fetchall(self):
            return [
                {
                    "id": 1,
                    "campaign_id": 3909,
                    "platform": "amazon_us",
                    "campaign_external_id": "123456",
                    "profile_id": None,
                    "nombre": "A1U Exact",
                    "status": "open",
                    "risk_type": "exceso_economico",
                    "first_seen_at": None,
                    "last_seen_at": None,
                    "closed_at": None,
                    "close_reason": None,
                    "window_start": FECHA,
                    "window_end": FECHA,
                    "observed_at": None,
                    "cost": Decimal("246.0300"),
                    "revenue": Decimal("115.2000"),
                    "currency": "USD",
                    "target_pct": Decimal("17.56"),
                    "target_source": "margen_plataforma",
                    "excess": Decimal("225.8028"),
                    "acos_pct": Decimal("213.5677"),
                    "campaign_status": "ENABLED",
                    "status_synced_at": None,
                    "aviso_estado": "pending",
                    "aviso_intentos": 0,
                    "aviso_enviado_at": None,
                    "evidence": {"fuente_dinero": "spCampaigns", "motivo": "exceso_economico"},
                }
            ]

    items = campaign_proposals(Conn(), platform="amazon_us", status="open", limit=50)
    assert len(items) == 1
    assert items[0]["cost"] == "246.0300"
    assert items[0]["revenue"] == "115.2000"
    assert items[0]["target_source"] == "margen_plataforma"
    assert items[0]["evidence"]["fuente_dinero"] == "spCampaigns"
    assert items[0]["motivo"] == "exceso_economico"
    assert items[0]["sales30d"] == "115.2000"
    assert items[0]["close_reason"] is None
    assert items[0]["aviso_estado"] == "pending"


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_una_propuesta_por_episodio_con_venta_tardia_y_reaparicion():
    decidido = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
    with _db_temporal("orbit_propuesta_c4") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        _estado(conn, camp, synced_at=decidido - dt.timedelta(hours=2))
        conn.execute(
            """INSERT INTO ads_optimizer_goal
                 (scope, platform, target_acos_pct, bid_floor, bid_ceiling,
                  bid_currency, enabled, mode)
                 VALUES ('platform', 'amazon_us', 20, 0.10, 2.50, 'USD', true, 'shadow')"""
        )
        run = _run(conn)
        for day in range(2, 10):
            fecha = dt.date(2026, 9, day)
            _metrica(
                conn, run, camp, fecha, decidido - dt.timedelta(days=1), cost="15", ad_revenue="0"
            )
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 9, 23),
            decidido - dt.timedelta(hours=1),
            cost="1",
            ad_revenue="0",
        )
        kw = dict(platform="amazon_us", settings={}, target_margen=None)
        assert _sincroniza(conn, decidido=decidido, **kw) == {"exceso_economico": 1}
        _sincroniza(conn, decidido=decidido, **kw)
        primera = conn.execute(
            "SELECT id, status, cost, revenue, target_pct, target_source, window_end "
            "FROM ads_campaign_proposal"
        ).fetchall()
        assert len(primera) == 1
        assert primera[0][1:] == (
            "open",
            Decimal("120.0000"),
            Decimal("0.0000"),
            Decimal("20.00"),
            "goal_plataforma",
            dt.date(2026, 9, 14),
        )
        # Nueva atribucion de venta: el ultimo vintage de la MISMA fecha deja
        # de cruzar, y el episodio se cierra sin crear una segunda propuesta.
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 9, 2),
            decidido + dt.timedelta(minutes=1),
            cost="15",
            ad_revenue="700",
        )
        _sincroniza(conn, decidido=decidido, **kw)
        assert conn.execute("SELECT status FROM ads_campaign_proposal").fetchone() == ("open",)
        _sincroniza(conn, decidido=decidido + dt.timedelta(minutes=2), **kw)
        assert conn.execute(
            "SELECT status, reset_at IS NOT NULL FROM ads_campaign_proposal"
        ).fetchone() == ("resolved", True)
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 9, 2),
            decidido + dt.timedelta(minutes=3),
            cost="15",
            ad_revenue="0",
        )
        _sincroniza(conn, decidido=decidido + dt.timedelta(minutes=4), **kw)
        assert conn.execute("SELECT status FROM ads_campaign_proposal ORDER BY id").fetchall() == [
            ("resolved",),
            ("open",),
        ]
        pausado = decidido + dt.timedelta(minutes=5)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED', synced_at = %s WHERE ad_entity_id = %s",
            (pausado, camp),
        )
        _sincroniza(conn, decidido=pausado, **kw)
        _sincroniza(conn, decidido=pausado + dt.timedelta(minutes=1), **kw)
        assert conn.execute("SELECT status FROM ads_campaign_proposal ORDER BY id").fetchall() == [
            ("resolved",),
            ("paused_observed",),
        ]
        cierre = conn.execute(
            "SELECT close_reason, close_evidence FROM ads_campaign_proposal "
            "WHERE status = 'paused_observed'"
        ).fetchone()
        assert cierre[0] == "estado_pausado_observado"
        assert cierre[1]["policy_version"] == "ads-proteccion-01-c1"
        assert cierre[1]["limite_relativo"] == "3"
        assert cierre[1]["limite_exceso"] == "80"


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_ciclo_congela_propuesta_en_tx2_aunque_target_cambie_en_tx3(monkeypatch):
    from app import cycle as ciclo

    with _db_temporal("orbit_propuesta_tx") as (conn, _extra):
        ids = _siembra_maestra(conn)
        # B3: el ciclo solo evalua propuestas con el flag prendido
        # (config_version es append-only: version nueva, no UPDATE).
        _config_version(conn, {"ads_optimizer_mode": "shadow", "ads_propuestas_campana": True})
        run = _run(conn)
        for day in range(5, 13):
            fecha = dt.date(2026, 8, day)
            _metrica(
                conn,
                run,
                ids["camp"],
                fecha,
                _obs(fecha),
                cost="15",
                ad_revenue="100" if day == 5 else "0",
            )
        _metrica(
            conn,
            run,
            ids["camp"],
            dt.date(2026, 8, 19),
            _obs(dt.date(2026, 8, 19)),
            cost="1",
            ad_revenue="0",
        )
        original = ciclo._inserta_decisiones

        def cambia_target_tras_lectura(conn_tx, *args):
            original(conn_tx, *args)
            conn_tx.execute(
                "UPDATE ads_optimizer_goal SET target_acos_pct = 60 WHERE scope = 'platform'"
            )

        monkeypatch.setattr(ciclo, "_inserta_decisiones", cambia_target_tras_lectura)
        _corre(conn)
        fila = conn.execute(
            "SELECT target_pct, target_source, cost, revenue FROM ads_campaign_proposal"
            " WHERE campaign_id = %s AND status = 'open'",
            (ids["camp"],),
        ).fetchone()
        assert fila == (
            Decimal("25.00"),
            "goal_plataforma",
            Decimal("120.0000"),
            Decimal("100.0000"),
        )
        # B1: el ciclo envia el aviso (canal deshabilitado en tests -> sent).
        assert conn.execute(
            "SELECT aviso_estado FROM ads_campaign_proposal WHERE campaign_id = %s",
            (ids["camp"],),
        ).fetchone() == ("sent",)


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_permisos_app_decide_escribe_y_app_read_solo_lee():
    decidido = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
    with _db_temporal("orbit_propuesta_role") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        dato = replace(
            _dato("100", "0"),
            campaign_id=camp,
            status_synced_at=decidido - dt.timedelta(hours=2),
        )
        try:
            conn.execute("SET ROLE app_decide")
        except psycopg.errors.InsufficientPrivilege:
            if os.environ.get("CI") or os.environ.get("ORBIT_TEST_DSN"):
                raise AssertionError(
                    "DSN sin membresia app_decide: prueba de grants sin ejercer"
                ) from None
            pytest.skip("DSN local sin membresia app_decide")
        try:
            p._persiste(conn, p.evalua(dato, decidido=decidido), decidido)
        finally:
            conn.execute("RESET ROLE")
        conn.execute("SET ROLE app_read")
        try:
            assert conn.execute("SELECT count(*) FROM ads_campaign_proposal").fetchone() == (1,)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE ads_campaign_proposal SET status = 'dismissed'")
        finally:
            conn.execute("RESET ROLE")


def test_flag_fail_closed():
    """C.4 B3: solo JSON true habilita las propuestas de campana."""
    from app.optimizer import goals as g

    assert g.propuestas_campana_desde_settings({}) is False
    assert g.propuestas_campana_desde_settings({"ads_propuestas_campana": True}) is True
    assert g.propuestas_campana_desde_settings({"ads_propuestas_campana": False}) is False
    assert g.propuestas_campana_desde_settings({"ads_propuestas_campana": None}) is False
    assert g.propuestas_campana_desde_settings({"ads_propuestas_campana": "true"}) is False
    assert g.propuestas_campana_desde_settings({"ads_propuestas_campana": 1}) is False


def test_acos_se_congela_a_dos_decimales():
    """Obs4: 134.6145789694176790950984499 se guarda como 134.61."""
    resultado = p.evalua(_dato("642.65", "477.40", target="17.563"))
    assert resultado.acos_pct == Decimal("134.61")


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_flag_apagado_no_evalua_ni_persiste():
    """C.4 B3: ciclo sin flag no deja filas ni siquiera con campana cara."""
    from app import cycle as ciclo  # noqa: F401 - el _corre corre el ciclo real

    with _db_temporal("orbit_propuesta_off") as (conn, _extra):
        ids = _siembra_maestra(conn)
        run = _run(conn)
        for day in range(5, 13):
            fecha = dt.date(2026, 8, day)
            _metrica(
                conn,
                run,
                ids["camp"],
                fecha,
                _obs(fecha),
                cost="15",
                ad_revenue="100" if day == 5 else "0",
            )
        _metrica(
            conn,
            run,
            ids["camp"],
            dt.date(2026, 8, 19),
            _obs(dt.date(2026, 8, 19)),
            cost="1",
            ad_revenue="0",
        )
        _corre(conn)
        assert conn.execute("SELECT count(*) FROM ads_campaign_proposal").fetchone() == (0,)


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_pausada_con_riesgo_deja_fila_visible_no_accionable():
    """B2: PAUSED cara (A1U/AU2) aparece como paused_observed con estado y
    motivo; jamas como open."""
    decidido = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
    with _db_temporal("orbit_propuesta_paused") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        _estado(conn, camp, synced_at=decidido - dt.timedelta(hours=2))
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s", (camp,)
        )
        dato = replace(
            _dato("200", "0", status="PAUSED"),
            campaign_id=camp,
            status_synced_at=decidido - dt.timedelta(hours=2),
        )
        evaluacion = p.evalua(dato, decidido=decidido)
        assert evaluacion.riesgo is False  # DoD C.4: la real no cruza
        p._persiste(conn, evaluacion, decidido)
        fila = conn.execute(
            "SELECT status, close_reason, aviso_estado, cost, revenue, campaign_status,"
            " evidence FROM ads_campaign_proposal"
        ).fetchone()
        assert fila[0] == "paused_observed"
        assert fila[1] == "estado_pausado_observado"
        assert fila[2] == "pending"
        assert fila[3] == Decimal("200.0000")
        assert fila[4] == Decimal("0.0000")
        assert fila[5] == "PAUSED"
        assert fila[6]["motivo"] == "campana_no_enabled"
        assert fila[6]["riesgo_ignorando_estado"] is True
        # Cron repetido: actualiza, no duplica.
        p._persiste(conn, evaluacion, decidido + dt.timedelta(minutes=1))
        assert conn.execute("SELECT count(*) FROM ads_campaign_proposal").fetchone() == (1,)


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_reactivada_con_riesgo_abre_episodio_nuevo():
    """Obs5: PAUSED->ENABLED con riesgo abre open (episodio nuevo)."""
    decidido = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
    with _db_temporal("orbit_propuesta_react") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        _estado(conn, camp, synced_at=decidido - dt.timedelta(hours=2))
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s", (camp,)
        )
        pausada = replace(
            _dato("200", "0", status="PAUSED"),
            campaign_id=camp,
            status_synced_at=decidido - dt.timedelta(hours=2),
        )
        p._persiste(conn, p.evalua(pausada, decidido=decidido), decidido)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'ENABLED' WHERE ad_entity_id = %s", (camp,)
        )
        viva = replace(pausada, status="ENABLED")
        p._persiste(conn, p.evalua(viva, decidido=decidido), decidido)
        assert conn.execute("SELECT status FROM ads_campaign_proposal ORDER BY id").fetchall() == [
            ("paused_observed",),
            ("open",),
        ]


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_abierta_con_archived_refresca_evidencia_sin_cerrar():
    """Obs5: open + ARCHIVED conserva status y actualiza snapshot."""
    decidido = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
    with _db_temporal("orbit_propuesta_arch") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        _estado(conn, camp, synced_at=decidido - dt.timedelta(hours=2))
        viva = replace(
            _dato("200", "0"),
            campaign_id=camp,
            status_synced_at=decidido - dt.timedelta(hours=2),
        )
        p._persiste(conn, p.evalua(viva, decidido=decidido), decidido)
        archivada = replace(viva, status="ARCHIVED")
        p._persiste(conn, p.evalua(archivada, decidido=decidido), decidido)
        fila = conn.execute(
            "SELECT status, campaign_status, evidence FROM ads_campaign_proposal"
        ).fetchone()
        assert fila[0] == "open"
        assert fila[1] == "ARCHIVED"
        assert fila[2]["motivo"] == "campana_no_enabled"


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_endpoint_real_muestra_todas_con_motivo_y_cierre(tmp_path):
    """Obs2/B2: endpoint contra PG real (no fake): default trae open +
    paused_observed, con motivo, sales30d, cierre y aviso."""
    decidido = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
    with _db_temporal("orbit_propuesta_api") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        _estado(conn, camp, synced_at=decidido - dt.timedelta(hours=2))
        viva = replace(
            _dato("200", "0"),
            campaign_id=camp,
            status_synced_at=decidido - dt.timedelta(hours=2),
        )
        p._persiste(conn, p.evalua(viva, decidido=decidido), decidido)
        camp2 = _entidad(conn, "amazon_us", "campaign", "3910")
        _estado(conn, camp2, synced_at=decidido - dt.timedelta(hours=2))
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s", (camp2,)
        )
        pausada = replace(
            _dato("200", "0", status="PAUSED"),
            campaign_id=camp2,
            external_id="3910",
            status_synced_at=decidido - dt.timedelta(hours=2),
        )
        p._persiste(conn, p.evalua(pausada, decidido=decidido), decidido)
        items = campaign_proposals(conn)
        assert {i["status"] for i in items} == {"open", "paused_observed"}
        abierta = next(i for i in items if i["status"] == "open")
        assert abierta["motivo"] == "exceso_economico"
        assert abierta["sales30d"] == "0.0000"
        assert abierta["close_reason"] is None
        assert abierta["aviso_estado"] == "pending"
        assert abierta["profile_id"] is None
        assert [i["status"] for i in campaign_proposals(conn, status="open")] == ["open"]


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_aviso_se_marca_sent_y_fallo_reintenta_sin_duplicar(tmp_path, monkeypatch):
    """B1: HTTP 200 -> sent; fallo -> pending+intento; reintento envia solo
    la pendiente (sin duplicar la ya enviada)."""
    from test_notifica import _canal

    from app import notifica

    decidido = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
    with _db_temporal("orbit_propuesta_aviso") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        _estado(conn, camp, synced_at=decidido - dt.timedelta(hours=2))
        viva = replace(
            _dato("200", "0"),
            campaign_id=camp,
            external_id="3909",
            status_synced_at=decidido - dt.timedelta(hours=2),
        )
        p._persiste(conn, p.evalua(viva, decidido=decidido), decidido)
        prop_id = conn.execute("SELECT id FROM ads_campaign_proposal").fetchone()[0]
        with _canal(tmp_path, monkeypatch, status=500) as mensajes:
            resultado = p.envia_avisos_pendientes(conn)
        assert resultado == {"enviados": [], "fallos": [prop_id]}
        assert len(mensajes) == 1
        assert conn.execute(
            "SELECT aviso_estado, aviso_intentos, aviso_enviado_at FROM ads_campaign_proposal"
        ).fetchone() == ("pending", 1, None)
        with _canal(tmp_path, monkeypatch) as mensajes2:
            resultado = p.envia_avisos_pendientes(conn)
        assert resultado == {"enviados": [prop_id], "fallos": []}
        assert len(mensajes2) == 1
        texto = mensajes2[0]["text"]
        assert "propuesta de campana" in texto
        assert "3909" in texto
        assert "200.00 USD" in texto
        fila = conn.execute(
            "SELECT aviso_estado, aviso_intentos, aviso_enviado_at IS NOT NULL"
            " FROM ads_campaign_proposal"
        ).fetchone()
        assert fila == ("sent", 2, True)
        # Tercer ciclo: nada pendiente, cero HTTP.
        with _canal(tmp_path, monkeypatch) as mensajes3:
            assert p.envia_avisos_pendientes(conn) == {"enviados": [], "fallos": []}
        assert mensajes3 == []
        assert notifica.canal_activo() in (True, False)  # el _canal restaura


def test_mensaje_propuesta_no_sugiere_pausa_automatica():
    """B1: el texto declara pausa manual y trae ventana + motivo economico."""
    from app import notifica

    aviso = notifica.PropuestaCampanaNueva(
        proposal_id=7,
        platform="amazon_us",
        campaign_external_id="123456",
        nombre="A1U Exact",
        first_seen_at=dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC),
        window_start=dt.date(2026, 8, 16),
        window_end=dt.date(2026, 9, 14),
        cost=Decimal("642.65"),
        revenue=Decimal("477.40"),
        currency="USD",
        target_pct=Decimal("17.563"),
        target_source="goal_campana",
        excess=Decimal("558.80"),
        acos_pct=Decimal("134.61"),
    )
    texto = notifica.aviso_propuesta_campana_nueva(aviso)
    assert "pausar a mano" in texto
    assert "123456" in texto and "A1U Exact" in texto
    assert "642.65 USD" in texto and "goal_campana" in texto
    assert "2026-08-16..2026-09-14" in texto


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres de prueba")
def test_cascada_unificada_bloquea_plataforma_con_goal_campana_sin_target():
    """Obs3/M6-M7: goal de campana sin target bloquea al de plataforma y el
    margen decide (valor y procedencia de UNA llamada)."""
    from decimal import Decimal as D

    decidido = dt.datetime(2026, 9, 24, 8, 40, tzinfo=dt.UTC)
    with _db_temporal("orbit_propuesta_casc") as (conn, _extra):
        camp = _entidad(conn, "amazon_us", "campaign", "3909")
        _estado(conn, camp, synced_at=decidido - dt.timedelta(hours=2))
        conn.execute(
            """INSERT INTO ads_optimizer_goal
                 (scope, platform, target_acos_pct, bid_floor, bid_ceiling,
                  bid_currency, enabled, mode)
                 VALUES ('platform', 'amazon_us', 20, 0.10, 2.50, 'USD', true, 'live')"""
        )
        conn.execute(
            """INSERT INTO ads_optimizer_goal
                 (scope, ad_entity_id, target_acos_pct, bid_floor, bid_ceiling,
                  bid_currency, enabled, mode)
                 VALUES ('campaign', %s, NULL, 0.10, 2.50, 'USD', true, 'live')""",
            (camp,),
        )
        run = _run(conn)
        for day in range(2, 10):
            fecha = dt.date(2026, 9, day)
            _metrica(
                conn, run, camp, fecha, decidido - dt.timedelta(days=1), cost="15", ad_revenue="0"
            )
        _metrica(
            conn,
            run,
            camp,
            dt.date(2026, 9, 23),
            decidido - dt.timedelta(hours=1),
            cost="1",
            ad_revenue="0",
        )
        (evaluacion,) = p.lee_evaluaciones(
            conn, platform="amazon_us", decidido=decidido, settings={}, target_margen=D("33")
        )
        assert evaluacion.dato.target_pct == D("33")
        assert evaluacion.dato.target_source == "margen_plataforma"
