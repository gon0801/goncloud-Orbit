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
        p._SQL_ACTUALIZAR,
    ):
        assert pglast.parse_sql(query.replace("%s", "NULL"))
    assert pglast.parse_sql(_SQL_PROPUESTAS_CAMPANA.replace("%s", "NULL"))


def test_api_muestra_dinero_como_texto_y_solo_lee_propuestas():
    class Conn:
        row_factory = None

        def execute(self, sql, params):
            assert sql == _SQL_PROPUESTAS_CAMPANA
            assert params == ("open", "amazon_us", "amazon_us", 50)
            return self

        def fetchall(self):
            return [
                {
                    "id": 1,
                    "campaign_id": 3909,
                    "platform": "amazon_us",
                    "campaign_external_id": "123456",
                    "nombre": "A1U Exact",
                    "status": "open",
                    "risk_type": "exceso_economico",
                    "first_seen_at": None,
                    "last_seen_at": None,
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
                    "evidence": {"fuente_dinero": "spCampaigns"},
                }
            ]

    items = campaign_proposals(Conn(), platform="amazon_us", status="open", limit=50)
    assert len(items) == 1
    assert items[0]["cost"] == "246.0300"
    assert items[0]["revenue"] == "115.2000"
    assert items[0]["target_source"] == "margen_plataforma"
    assert items[0]["evidence"]["fuente_dinero"] == "spCampaigns"


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
