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
def test_reaplica_tras_regreso_hace_put_nuevo(monkeypatch):
    """Panel: aplica->regresa->reaplica el mismo plan manda un tercer PUT
    (el early-return idempotente solo vale si el vigente sigue en destino)."""
    with _db("orbit_aj_reap") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        transporte = httpx.MockTransport(handler)
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=transporte,
        )
        apply.regresa_ajuste_campana(
            conn, ajuste_id=hecho.ajuste_id, actor="dueno", transport=transporte
        )
        plan2 = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        assert plan2.huella() == plan.huella()
        hecho2 = apply.aplica_ajuste_campana(
            conn,
            plan2,
            huella=plan2.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=transporte,
        )
        assert hecho2.confirmada is True
        assert hecho2.ajuste_id != hecho.ajuste_id
        assert [p["budget"]["budget"] for p in _puts(vistos)] == [120.0, 100.0, 120.0]


@_skip_db
def test_regreso_fallido_se_puede_reintentar(monkeypatch):
    """Panel: regreso con readback divergente deja pendiente reintentable
    (como el aplica); el reintento confirma y un tercero se rechaza."""
    with _db("orbit_aj_reint") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        handler_ok, _vistos = _handler_campanas(remoto)
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler_ok),
        )
        from dataclasses import replace

        divergente = replace(_vigente_config(), presupuesto_diario=Decimal("999"))
        handler_mal, _vistos_mal = _handler_campanas(remoto, divergente=divergente)
        with pytest.raises(apply.AjusteNoConfirmado):
            apply.regresa_ajuste_campana(
                conn,
                ajuste_id=hecho.ajuste_id,
                actor="dueno",
                transport=httpx.MockTransport(handler_mal),
            )
        handler_ok2, _vistos2 = _handler_campanas(remoto)
        regreso = apply.regresa_ajuste_campana(
            conn,
            ajuste_id=hecho.ajuste_id,
            actor="dueno",
            transport=httpx.MockTransport(handler_ok2),
        )
        assert regreso.confirmada is True
        with pytest.raises(apply.AjusteYaRegresado):
            apply.regresa_ajuste_campana(
                conn,
                ajuste_id=hecho.ajuste_id,
                actor="dueno",
                transport=httpx.MockTransport(handler_ok2),
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


def test_config_leida_elige_por_id_entre_varias():
    """Panel, unitario: el list trae otra campana primero; el readback
    elige la pedida por id (nunca items[0])."""
    from app.apply import _config_leida

    class _ListFake:
        def list_sellado(self, _path, _body):
            return httpx.Response(
                200,
                json={
                    "campaigns": [
                        {"campaignId": "1", "budget": {"budget": 999.0}},
                        {
                            "campaignId": EXTERNA,
                            "budget": {"budget": 120.0, "budgetType": "DAILY"},
                        },
                    ]
                },
            )

    leida = _config_leida(_ListFake(), "amazon_mx", EXTERNA)
    assert leida is not None
    assert leida.campana_externa == EXTERNA
    assert leida.presupuesto_diario == Decimal("120")


@_skip_db
def test_readback_cruza_por_id_y_no_por_items_0(monkeypatch):
    """Panel: list que trae otra campana primero no confirma (cruce por
    id); si solo trae la ajena, no hay falso exito."""
    with _db("orbit_aj_cruce") as conn:
        camp, _cfg = _siembra(conn)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))

        def handler_ajena(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "fake", "expires_in": 3600})
            if request.method == "GET" and request.url.path == "/v2/profiles":
                return httpx.Response(200, json=[PERFIL_MX_RAW])
            if request.method == "PUT" and request.url.path == "/sp/campaigns":
                return httpx.Response(207, json={"campaigns": [{"code": "200"}]})
            if request.method == "POST" and request.url.path == "/sp/campaigns/list":
                return httpx.Response(
                    200,
                    json={
                        "campaigns": [
                            {
                                "campaignId": "1",
                                "budget": {"budget": 120.0, "budgetType": "DAILY"},
                            }
                        ]
                    },
                )
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        with pytest.raises(apply.AjusteNoConfirmado):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler_ajena),
            )


@_skip_db
def test_pendiente_con_destino_observado_se_reconcilia_sin_put(monkeypatch):
    """Panel: PUT salio pero el readback fallo; el sync observo el
    destino: el reintento sella sin un segundo PUT."""
    with _db("orbit_aj_recon") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        handler_mal, _v = _handler_campanas(remoto, divergente=_vigente_config())
        with pytest.raises(apply.AjusteNoConfirmado):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler_mal),
            )
        # El sync observa lo que el PUT si aplico (= plan.despues).
        remoto[EXTERNA] = {"budget": {"budget": 120.0, "budgetType": "DAILY"}}
        despues = plan.despues
        conn.execute(
            "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
            " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
            " ajuste_resto_pct, ajuste_producto_pct, fuera_de_amazon)"
            " VALUES (%s, now(), %s, %s, %s, %s, %s, %s, %s)",
            (
                camp,
                despues.presupuesto_diario,
                despues.moneda,
                despues.estrategia_puja,
                despues.ajuste_top_pct,
                despues.ajuste_resto_pct,
                despues.ajuste_producto_pct,
                despues.fuera_de_amazon,
            ),
        )
        conn.commit()
        handler_ok, vistos = _handler_campanas(remoto)

        def _sin_put(request: httpx.Request) -> httpx.Response:
            assert request.method != "PUT", "la reconciliacion no hace PUT"
            return handler_ok(request)

        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(_sin_put),
        )
        assert hecho.confirmada is True
        assert _puts(vistos) == []


@_skip_db
def test_huella_ajena_no_confirma_otra_campana(monkeypatch):
    """Panel: huella de un plan de otra campana no confirma nada (422)."""
    with _db("orbit_aj_huella") as conn:
        camp, _cfg = _siembra(conn)
        camp_b = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
            " 'campaign', '00000000000001') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES"
            " (%s, 'ENABLED', now())",
            (camp_b,),
        )
        conn.execute(
            "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
            " presupuesto_diario, presupuesto_moneda) VALUES (%s, now(), 100, 'MXN')",
            (camp_b,),
        )
        _creds_fake(monkeypatch)
        plan_a = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        plan_b = _plan(conn, camp_b, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, _vistos = _handler_campanas(remoto)
        apply.aplica_ajuste_campana(
            conn,
            plan_a,
            huella=plan_a.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )

        def _nada(request: httpx.Request) -> httpx.Response:
            raise AssertionError("jamas debe salir a la red")

        with pytest.raises(ValueError, match="otra campana"):
            apply.aplica_ajuste_campana(
                conn,
                plan_b,
                huella=plan_a.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(_nada),
            )


@_skip_db
def test_doble_aplica_simultaneo_no_explota_ni_duplica_acto(monkeypatch):
    """Panel: dos hilos aplican el mismo plan a la vez: sin 500 ni
    UniqueViolation escapada, y como maximo dos filas (acto + reintento)."""
    import threading

    with _db("orbit_aj_race") as conn:
        camp, _cfg = _siembra(conn)
        _creds_fake(monkeypatch)
        dsn = conn.execute("SELECT current_database()").fetchone()[0]
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, _vistos = _handler_campanas(remoto)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        resultados: list = []

        def _corre():
            import os
            import time

            import psycopg

            base = os.environ["ORBIT_TEST_DSN"].rsplit("/", 1)[0]
            hilo_conn = psycopg.connect(f"{base}/{dsn}", autocommit=True)
            try:
                import app.apply as ap

                for _intento in range(10):
                    try:
                        hecho = ap.aplica_ajuste_campana(
                            hilo_conn,
                            plan,
                            huella=plan.huella(),
                            confirmacion="APLICAR AJUSTE",
                            actor="dueno",
                            transport=httpx.MockTransport(handler),
                        )
                        resultados.append(("ok", hecho.confirmada))
                        return
                    except ap.AjusteEnCurso:
                        time.sleep(0.05)
                resultados.append(("error", "reintentos agotados"))
            except Exception as exc:  # noqa: BLE001 - el test clasifica
                resultados.append(("error", type(exc).__name__))
            finally:
                hilo_conn.close()

        hilos = [threading.Thread(target=_corre) for _ in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=60)
        assert len(resultados) == 2
        for estado, _detalle in resultados:
            assert estado == "ok", resultados
        assert all(detalle is True for _, detalle in resultados)
        filas = conn.execute(
            "SELECT count(*) FROM campana_ajuste WHERE huella LIKE %s", (plan.huella() + "%",)
        ).fetchone()[0]
        assert filas <= 2, resultados


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
