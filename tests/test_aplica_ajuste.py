"""Aplica y regresa ajustes de campana (BIDS 02, V.3): PG + MockTransport,
cero HTTP vivo. Patron de `test_api_write.py` (reversa_manual)."""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import apply
from app.ads.campana_config import ConfigCampana
from app.ads.config import AdsCredentials
from app.campana_ajustes import (
    CambiarAjusteUbicacion,
    CambiarPresupuesto,
    LimitarFueraDeAmazon,
    planea_ajuste,
)

ROOT = Path(__file__).resolve().parents[1]
ORDEN = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0060_bids02_base_lectura.sql",
    "0061_bids02_campana_config.sql",
    "0067_bids02_campana_ajuste.sql",
)
EXTERNA = "93529333080113"
PERFIL_MX_RAW = {
    "profileId": 101010,
    "countryCode": "MX",
    "currencyCode": "MXN",
    "accountInfo": {"type": "seller", "name": "Test MX", "validPaymentMethod": True},
}

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def _db(prefijo: str):
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _vigente_config() -> ConfigCampana:
    return ConfigCampana(
        campana_externa=EXTERNA,
        presupuesto_diario=Decimal("100"),
        moneda="MXN",
        estrategia_puja="LEGACY_FOR_SALES",
        ajuste_top_pct=0,
        ajuste_resto_pct=0,
        ajuste_producto_pct=40,
        fuera_de_amazon=None,
    )


def _plan(conn, camp: int, ajuste):
    """Planea desde la vigente LEIDA (como la ruta: lo leido manda)."""
    from app.ads.campana_config import config_vigente

    return planea_ajuste(
        config_vigente(conn, camp), ajuste, campana_id=camp, plataforma="amazon_mx"
    )


def _siembra(conn, config: ConfigCampana | None = None) -> tuple[int, int]:
    """Campana MX ENABLED + una observacion de config. Devuelve
    (campana_id, config_id)."""
    from app.ads.campana_config import guarda_config

    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
        " 'campaign', %s) RETURNING id",
        (EXTERNA,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES"
        " (%s, 'ENABLED', now())",
        (camp,),
    )
    guarda_config(conn, "amazon_mx", [config or _vigente_config()], dt.datetime.now(dt.UTC))
    cfg = conn.execute(
        "SELECT id FROM ads_campana_config_observation WHERE ad_entity_id = %s", (camp,)
    ).fetchone()[0]
    return camp, cfg


def _creds_fake(monkeypatch) -> None:
    creds = AdsCredentials(
        client_id="fake-client-id",
        client_secret="fake-client-secret",
        refresh_token="fake-refresh-token",
    )
    monkeypatch.setattr(
        AdsCredentials, "from_secrets_dir", classmethod(lambda cls, *a, **kw: creds)
    )


def _payload_list(config: ConfigCampana) -> dict:
    """Payload con el shape del list de campanas para una config."""
    from app.ads.campana_config import cuerpo_put_campana

    return cuerpo_put_campana(config)


def _handler_campanas(remoto: dict, *, divergente: ConfigCampana | None = None):
    """Amazon mock: token + profiles MX + PUT campaigns (aplica al remoto)
    + readback por POST /sp/campaigns/list. Con `divergente`, el list
    devuelve otra config (Amazon no aplico)."""
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})
        vistos.append(request)
        path, metodo = request.url.path, request.method
        if metodo == "GET" and path == "/v2/profiles":
            return httpx.Response(200, json=[PERFIL_MX_RAW])
        if metodo == "PUT" and path == "/sp/campaigns":
            obj = json.loads(request.content)["campaigns"][0]
            remoto[str(obj["campaignId"])] = {k: v for k, v in obj.items() if k != "campaignId"}
            return httpx.Response(207, json={"campaigns": [{"code": "200"}]})
        if metodo == "POST" and path == "/sp/campaigns/list":
            ext = json.loads(request.content)["campaignIdFilter"]["include"][0]
            cuerpo = remoto[str(ext)]
            if divergente is not None:
                cuerpo = {k: v for k, v in _payload_list(divergente).items() if k != "campaignId"}
            return httpx.Response(200, json={"campaigns": [{"campaignId": str(ext), **cuerpo}]})
        raise AssertionError(f"request inesperado: {metodo} {path}")

    return handler, vistos


def _puts(vistos: list[httpx.Request]) -> list[dict]:
    return [json.loads(r.content)["campaigns"][0] for r in vistos if r.method == "PUT"]


@_skip_db
def test_aplica_presupuesto_confirma_y_guarda_vigente(monkeypatch):
    with _db("orbit_aj_aplica") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(
            conn,
            camp,
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
        )
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        assert hecho.confirmada is True
        assert [p["budget"]["budget"] for p in _puts(vistos)] == [120.0]
        fila = conn.execute(
            "SELECT clase, confirmado_el IS NOT NULL, go_literal FROM campana_ajuste WHERE id = %s",
            (hecho.ajuste_id,),
        ).fetchone()
        assert fila == ("presupuesto", True, "APLICAR AJUSTE")
        from app.ads.campana_config import config_vigente

        assert config_vigente(conn, camp).presupuesto_diario == Decimal("120")


@_skip_db
def test_segunda_vez_misma_huella_no_repite_put(monkeypatch):
    with _db("orbit_aj_idem") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(
            conn,
            camp,
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
        )
        kwargs: dict = dict(
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        primero = apply.aplica_ajuste_campana(conn, plan, **kwargs)
        segundo = apply.aplica_ajuste_campana(conn, plan, **kwargs)
        assert segundo.ajuste_id == primero.ajuste_id
        assert segundo.confirmada is True
        assert len(_puts(vistos)) == 1, "idempotente: un solo PUT por huella"
        assert conn.execute("SELECT count(*) FROM campana_ajuste").fetchone()[0] == 1


@_skip_db
def test_huella_vieja_devuelve_plan_nuevo_sin_http(monkeypatch):
    with _db("orbit_aj_vieja") as conn:
        camp, _cfg = _siembra(conn)
        plan = _plan(
            conn,
            camp,
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
        )
        _creds_fake(monkeypatch)

        def _nada(request: httpx.Request) -> httpx.Response:
            raise AssertionError("jamas debe salir a la red")

        from dataclasses import replace

        from app.ads.campana_config import guarda_config

        guarda_config(
            conn,
            "amazon_mx",
            [replace(_vigente_config(), presupuesto_diario=Decimal("110"))],
            dt.datetime.now(dt.UTC),
        )
        with pytest.raises(apply.HuellaDesactualizada) as exc:
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(_nada),
            )
        assert exc.value.plan_nuevo.despues.presupuesto_diario == Decimal("120")
        assert exc.value.plan_nuevo.antes.presupuesto_diario == Decimal("110")


@_skip_db
def test_literal_distinto_no_toca_nada(monkeypatch):
    with _db("orbit_aj_lit") as conn:
        camp, _cfg = _siembra(conn)
        plan = _plan(
            conn,
            camp,
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
        )

        def _nada(request: httpx.Request) -> httpx.Response:
            raise AssertionError("jamas debe salir a la red")

        with pytest.raises(apply.ConfirmacionInvalida):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="aplicar ajuste",
                actor="dueno",
                transport=httpx.MockTransport(_nada),
            )
        assert conn.execute("SELECT count(*) FROM campana_ajuste").fetchone()[0] == 0


@_skip_db
def test_put_rechazado_deja_fila_pendiente_y_manda_una_campana(monkeypatch):
    """PUT rechazado (207 con errores): propaga, la fila YA existe
    pendiente (pre-HTTP) y el PUT trae una sola campana sin `state`."""
    with _db("orbit_aj_rech") as conn:
        camp, _cfg = _siembra(conn)
        vistos: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})
            vistos.append(request)
            path, metodo = request.url.path, request.method
            if metodo == "GET" and path == "/v2/profiles":
                return httpx.Response(200, json=[PERFIL_MX_RAW])
            if metodo == "PUT" and path == "/sp/campaigns":
                return httpx.Response(
                    207, json={"campaigns": [{"code": "400", "description": "nope"}]}
                )
            raise AssertionError(f"request inesperado: {metodo} {path}")

        _creds_fake(monkeypatch)
        plan = _plan(
            conn,
            camp,
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
        )
        with pytest.raises(apply.AdsApiErrorMutacion, match="nope"):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )
        (put,) = [r for r in vistos if r.method == "PUT"]
        cuerpo = json.loads(put.content)["campaigns"]
        assert len(cuerpo) == 1
        assert "state" not in cuerpo[0]
        fila = conn.execute(
            "SELECT confirmado_el FROM campana_ajuste WHERE huella = %s", (plan.huella(),)
        ).fetchone()
        assert fila is not None and fila[0] is None


@_skip_db
def test_readback_divergente_deja_sin_confirmar(monkeypatch):
    with _db("orbit_aj_div") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto, divergente=_vigente_config())
        _creds_fake(monkeypatch)
        plan = _plan(
            conn,
            camp,
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
        )
        with pytest.raises(apply.AjusteNoConfirmado):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )
        fila = conn.execute(
            "SELECT confirmado_el FROM campana_ajuste WHERE huella = %s", (plan.huella(),)
        ).fetchone()
        assert fila[0] is None
        assert len(_puts(vistos)) == 1


@_skip_db
def test_regresa_aplica_antes_y_segundo_regreso_rechazado(monkeypatch):
    with _db("orbit_aj_reg") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(
            conn,
            camp,
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
        )
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        regreso = apply.regresa_ajuste_campana(
            conn,
            ajuste_id=hecho.ajuste_id,
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        assert regreso.confirmada is True
        assert [p["budget"]["budget"] for p in _puts(vistos)] == [120.0, 100.0]
        fila = conn.execute(
            "SELECT regresa_a, go_literal, confirmado_el IS NOT NULL FROM campana_ajuste"
            " WHERE id = %s",
            (regreso.ajuste_id,),
        ).fetchone()
        assert fila == (hecho.ajuste_id, "REGRESAR AJUSTE", True)
        from app.ads.campana_config import config_vigente

        assert config_vigente(conn, camp).presupuesto_diario == Decimal("100")
        with pytest.raises(apply.AjusteYaRegresado):
            apply.regresa_ajuste_campana(
                conn,
                ajuste_id=hecho.ajuste_id,
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )


@_skip_db
def test_regresa_inexistente_da_404(monkeypatch):
    with _db("orbit_aj_404") as conn:
        _creds_fake(monkeypatch)

        def _nada(request: httpx.Request) -> httpx.Response:
            raise AssertionError("jamas debe salir a la red")

        with pytest.raises(apply.AjusteInexistente):
            apply.regresa_ajuste_campana(
                conn, ajuste_id=999, actor="dueno", transport=httpx.MockTransport(_nada)
            )


@_skip_db
def test_regresa_fuera_de_amazon_rechazado_sin_http(monkeypatch):
    """V.0 no sello el regreso de fuera_de_amazon: ni con la fila
    confirmada hay regreso (la pantalla tampoco presenta el boton)."""
    with _db("orbit_aj_fuerareg") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {}}
        handler, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, LimitarFueraDeAmazon())
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        assert hecho.confirmada is True

        def _nada(request: httpx.Request) -> httpx.Response:
            raise AssertionError("jamas debe salir a la red")

        with pytest.raises(apply.AjusteSinRegreso, match="no tiene regreso sellado"):
            apply.regresa_ajuste_campana(
                conn,
                ajuste_id=hecho.ajuste_id,
                actor="dueno",
                transport=httpx.MockTransport(_nada),
            )


@_skip_db
def test_aplica_ajuste_ubicacion_por_put_y_readback(monkeypatch):
    with _db("orbit_aj_ubi") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(
            conn,
            camp,
            CambiarAjusteUbicacion(ubicacion="paginas_de_producto", porcentaje=0),
        )
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        assert hecho.confirmada is True
        (put,) = _puts(vistos)
        assert put["dynamicBidding"]["placementBidding"][2] == {
            "placement": "PLACEMENT_PRODUCT_PAGE",
            "percentage": 0,
        }
        from app.ads.campana_config import config_vigente

        assert config_vigente(conn, camp).ajuste_producto_pct == 0


@_skip_db
def test_aplica_fuera_de_amazon_por_put_y_readback(monkeypatch):
    with _db("orbit_aj_fuera") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(
            conn,
            camp,
            LimitarFueraDeAmazon(),
        )
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        assert hecho.confirmada is True
        (put,) = _puts(vistos)
        assert put["offAmazonSettings"] == {"offAmazonBudgetControlStrategy": "MINIMIZE_SPEND"}
