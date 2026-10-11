"""Aplica y regresa ajustes de campana (BIDS 02, V.3): PG + MockTransport,
cero HTTP vivo. Patron de `test_api_write.py` (reversa_manual)."""

from __future__ import annotations

import copy
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


def _handler_mezcla(remoto: dict):
    """Amazon mock con la semantica sellada por V.0: el PUT aplica solo
    las llaves que trae (lo demas queda); placement 0 borra; el list
    devuelve la campana entera."""
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
            camp = remoto[str(obj["campaignId"])]
            for llave, valor in obj.items():
                if llave == "campaignId":
                    continue
                if llave == "dynamicBidding":
                    db = camp.setdefault("dynamicBidding", {})
                    if "strategy" in valor:
                        db["strategy"] = valor["strategy"]
                    actuales = {
                        e["placement"]: e["percentage"] for e in db.get("placementBidding", [])
                    }
                    for e in valor.get("placementBidding", []):
                        if e["percentage"] == 0:
                            actuales.pop(e["placement"], None)
                        else:
                            actuales[e["placement"]] = e["percentage"]
                    db["placementBidding"] = [
                        {"percentage": v, "placement": k} for k, v in actuales.items()
                    ]
                else:
                    camp[llave] = valor
            return httpx.Response(207, json={"campaigns": [{"code": "200"}]})
        if metodo == "POST" and path == "/sp/campaigns/list":
            ext = json.loads(request.content)["campaignIdFilter"]["include"][0]
            cuerpo = copy.deepcopy(remoto[str(ext)])
            return httpx.Response(200, json={"campaigns": [{"campaignId": str(ext), **cuerpo}]})
        raise AssertionError(f"request inesperado: {metodo} {path}")

    return handler, vistos


@contextmanager
def _db_dsn(prefijo: str):
    """Como _db, pero entrega (conn, dsn_db) para abrir mas conexiones
    contra la misma base (rutas reales: sin autocommit)."""
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
        yield conn, f"{dsn.rsplit('/', 1)[0]}/{db}"
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _observa(conn, camp: int, config: ConfigCampana) -> None:
    """Lo que haria el sync: guarda la config que lee de Amazon."""
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
        " ajuste_resto_pct, ajuste_producto_pct, fuera_de_amazon)"
        " VALUES (%s, now(), %s, %s, %s, %s, %s, %s, %s)",
        (
            camp,
            config.presupuesto_diario,
            config.moneda,
            config.estrategia_puja,
            config.ajuste_top_pct,
            config.ajuste_resto_pct,
            config.ajuste_producto_pct,
            config.fuera_de_amazon,
        ),
    )


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
    """B9: dos hilos aplican el mismo plan a la vez con conexiones como
    la ruta (sin autocommit): ambos confirman (reintentando el 409),
    sale UN solo PUT y queda una sola fila."""
    import threading

    with _db_dsn("orbit_aj_race") as (conn, dsn_db):
        camp, _cfg = _siembra(conn)
        _creds_fake(monkeypatch)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        resultados: list = []

        def _corre():
            import time

            import psycopg

            hilo_conn = psycopg.connect(dsn_db)
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
                        resultados.append(("ok", hecho.confirmada, hecho.ajuste_id))
                        return
                    except ap.AjusteEnCurso:
                        hilo_conn.rollback()
                        time.sleep(0.05)
                resultados.append(("error", "reintentos agotados", None))
            except Exception as exc:  # noqa: BLE001 - el test clasifica
                resultados.append(("error", type(exc).__name__, None))
            finally:
                hilo_conn.close()

        hilos = [threading.Thread(target=_corre) for _ in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=60)
        assert len(resultados) == 2
        for estado, _detalle, _aid in resultados:
            assert estado == "ok", resultados
        assert all(detalle is True for _, detalle, _aid in resultados)
        assert resultados[0][2] == resultados[1][2]
        assert len(_puts(vistos)) == 1, resultados
        filas = conn.execute("SELECT count(*) FROM campana_ajuste").fetchone()[0]
        assert filas == 1, resultados


@_skip_db
def test_carrera_insert_mismo_instante_pierde_con_409(monkeypatch):
    """B9/M15: los dos hilos pasan el SELECT sin ver fila e insertan a
    la vez (barrera en profiles, entre el SELECT y el INSERT): el
    perdedor recibe AjusteEnCurso (409), nunca un hecho confirmada
    sobre la fila pendiente del ganador; UN solo PUT."""
    import threading

    with _db_dsn("orbit_aj_m15") as (conn, dsn_db):
        camp, _cfg = _siembra(conn)
        _creds_fake(monkeypatch)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        base, vistos = _handler_campanas(remoto)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        barrera = threading.Barrier(2, timeout=30)
        suelta_put = threading.Event()
        resultados: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/v2/profiles":
                barrera.wait()
            if request.method == "PUT":
                assert suelta_put.wait(timeout=30), "el PUT quedo bloqueado"
            return base(request)

        def _corre():
            import psycopg

            hilo_conn = psycopg.connect(dsn_db)
            try:
                import app.apply as ap

                hecho = ap.aplica_ajuste_campana(
                    hilo_conn,
                    plan,
                    huella=plan.huella(),
                    confirmacion="APLICAR AJUSTE",
                    actor="dueno",
                    transport=httpx.MockTransport(handler),
                )
                resultados.append(("ok", hecho.confirmada))
            except Exception as exc:  # noqa: BLE001 - el test clasifica
                resultados.append(("error", type(exc).__name__))
            finally:
                hilo_conn.close()

        hilos = [threading.Thread(target=_corre) for _ in range(2)]
        for h in hilos:
            h.start()
        import time as _time

        limite = _time.monotonic() + 20
        while len(resultados) < 1 and _time.monotonic() < limite:
            _time.sleep(0.05)
        assert resultados == [("error", "AjusteEnCurso")], resultados
        suelta_put.set()
        for h in hilos:
            h.join(timeout=60)
        assert sorted(resultados) == [("error", "AjusteEnCurso"), ("ok", True)], resultados
        assert len(_puts(vistos)) == 1, resultados


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
        assert put["dynamicBidding"]["placementBidding"] == [
            {
                "placement": "PLACEMENT_PRODUCT_PAGE",
                "percentage": 0,
            }
        ]
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


@_skip_db
def test_reconcilia_aplica_sin_autocommit_y_cierra_sin_commit(monkeypatch):
    """B3/M03: PUT aplicado + sync que observo el destino: la
    reconciliacion sella DURABLE aunque quien llama cierre sin commit
    (como _conexion_escritura de la ruta)."""
    with _db_dsn("orbit_aj_b3a") as (conn, dsn_db):
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
        _observa(conn, camp, plan.despues)
        handler_ok, vistos = _handler_campanas(remoto)

        def _sin_put(request: httpx.Request) -> httpx.Response:
            assert request.method != "PUT", "la reconciliacion no hace PUT"
            return handler_ok(request)

        c2 = psycopg.connect(dsn_db)
        try:
            hecho = apply.aplica_ajuste_campana(
                c2,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(_sin_put),
            )
            assert hecho.confirmada is True
        finally:
            c2.rollback()
            c2.close()
        assert _puts(vistos) == []
        confirmada = conn.execute(
            "SELECT confirmado_el IS NOT NULL FROM campana_ajuste WHERE huella = %s",
            (plan.huella(),),
        ).fetchone()[0]
        assert confirmada is True


@_skip_db
def test_reconcilia_regreso_sin_autocommit_y_cierra_sin_commit(monkeypatch):
    """B3/M17: lo mismo para el regreso (hijo pendiente + sync que
    observo el antes)."""
    with _db_dsn("orbit_aj_b3r") as (conn, dsn_db):
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        handler, _vistos = _handler_campanas(remoto)
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        handler_mal, _v = _handler_campanas(remoto, divergente=plan.despues)
        with pytest.raises(apply.AjusteNoConfirmado):
            apply.regresa_ajuste_campana(
                conn,
                ajuste_id=hecho.ajuste_id,
                actor="dueno",
                transport=httpx.MockTransport(handler_mal),
            )
        _observa(conn, camp, plan.antes)
        handler_ok, vistos = _handler_campanas(remoto)

        def _sin_put(request: httpx.Request) -> httpx.Response:
            assert request.method != "PUT", "la reconciliacion no hace PUT"
            return handler_ok(request)

        c2 = psycopg.connect(dsn_db)
        try:
            reintento = apply.regresa_ajuste_campana(
                c2,
                ajuste_id=hecho.ajuste_id,
                actor="dueno",
                transport=httpx.MockTransport(_sin_put),
            )
            assert reintento.confirmada is True
        finally:
            c2.rollback()
            c2.close()
        assert _puts(vistos) == []
        confirmada = conn.execute(
            "SELECT confirmado_el IS NOT NULL FROM campana_ajuste WHERE regresa_a = %s",
            (hecho.ajuste_id,),
        ).fetchone()[0]
        assert confirmada is True


@_skip_db
def test_reaplicar_y_regresar_otra_vez_no_choca_huella(monkeypatch):
    """B8: aplicar, regresar, re-aplicar, regresar: el segundo regreso
    confirma (nunca 500 por UNIQUE(huella))."""
    with _db("orbit_aj_b8") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        transporte = httpx.MockTransport(handler)

        def _aplica(monto: str):
            plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal(monto)))
            return apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=transporte,
            )

        a1 = _aplica("120")
        r1 = apply.regresa_ajuste_campana(
            conn, ajuste_id=a1.ajuste_id, actor="dueno", transport=transporte
        )
        assert r1.confirmada is True
        a2 = _aplica("120")
        assert a2.ajuste_id != a1.ajuste_id
        r2 = apply.regresa_ajuste_campana(
            conn, ajuste_id=a2.ajuste_id, actor="dueno", transport=transporte
        )
        assert r2.confirmada is True
        assert remoto[EXTERNA]["budget"]["budget"] == 100.0


@_skip_db
def test_regresar_un_regreso_encadena_sin_500(monkeypatch):
    """B8: regresar un regreso rehace el ajuste; tres eslabones seguidos
    confirman (nunca 500 por UNIQUE(huella))."""
    with _db("orbit_aj_b8e") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        transporte = httpx.MockTransport(handler)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=transporte,
        )
        previos = [120.0, 100.0, 120.0]
        actual = hecho.ajuste_id
        for esperado in previos:
            assert remoto[EXTERNA]["budget"]["budget"] == esperado
            paso = apply.regresa_ajuste_campana(
                conn, ajuste_id=actual, actor="dueno", transport=transporte
            )
            assert paso.confirmada is True
            actual = paso.ajuste_id
        assert remoto[EXTERNA]["budget"]["budget"] == 100.0


def _remoto_b1(budget: float, top: int | None, producto: int = 40) -> dict:
    placements = []
    if top is not None:
        placements.append({"placement": "PLACEMENT_TOP", "percentage": top})
    placements.append({"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": producto})
    return {
        EXTERNA: {
            "budget": {"budget": budget, "budgetType": "DAILY"},
            "dynamicBidding": {
                "strategy": "LEGACY_FOR_SALES",
                "placementBidding": placements,
            },
        }
    }


@_skip_db
def test_put_lleva_solo_la_llave_de_la_clase(monkeypatch):
    """B1: cada clase manda su llave sola, con la forma que sello su
    sonda (presupuesto: budget; ubicacion: dynamicBidding; fuera:
    offAmazonSettings)."""
    with _db("orbit_aj_b1k") as conn:
        camp, _cfg = _siembra(conn)
        remoto = _remoto_b1(100.0, None)
        handler, vistos = _handler_mezcla(remoto)
        _creds_fake(monkeypatch)
        transporte = httpx.MockTransport(handler)

        def _aplica(ajuste):
            plan = _plan(conn, camp, ajuste)
            return apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=transporte,
            )

        assert _aplica(CambiarPresupuesto(presupuesto_diario=Decimal("120"))).confirmada
        assert _aplica(
            CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=50)
        ).confirmada
        assert _aplica(LimitarFueraDeAmazon()).confirmada
        llaves = [sorted(k for k in p if k != "campaignId") for p in _puts(vistos)]
        assert llaves == [["budget"], ["dynamicBidding"], ["offAmazonSettings"]]


@_skip_db
def test_ajuste_ubicacion_no_revierte_presupuesto_de_consola(monkeypatch):
    """B1 (deriva 1): el dueno subio el presupuesto en la consola tras
    el sync; un ajuste de ubicacion no lo revierte."""
    with _db("orbit_aj_b1a") as conn:
        camp, _cfg = _siembra(conn)
        remoto = _remoto_b1(300.0, None)
        handler, vistos = _handler_mezcla(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(
            conn, camp, CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=20)
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
        assert "budget" not in put
        assert remoto[EXTERNA]["budget"]["budget"] == 300.0


@_skip_db
def test_ajuste_presupuesto_no_revierte_ubicacion_de_consola(monkeypatch):
    """B1 (deriva 2): el dueno puso +50 % arriba en la consola tras el
    sync; un ajuste de presupuesto no lo borra."""
    with _db("orbit_aj_b1b") as conn:
        camp, _cfg = _siembra(conn)
        remoto = _remoto_b1(100.0, 50)
        handler, vistos = _handler_mezcla(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
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
        assert "dynamicBidding" not in put
        tops = {
            p["placement"]: p["percentage"]
            for p in remoto[EXTERNA]["dynamicBidding"]["placementBidding"]
        }
        assert tops["PLACEMENT_TOP"] == 50


@_skip_db
def test_dos_clases_a_la_vez_dejan_los_dos_cambios(monkeypatch):
    """B1 (07): presupuesto + ubicacion planeados con el mismo vigente;
    el PUT de ubicacion sale despues de confirmar el presupuesto y no
    lo pisa: Amazon queda con los dos cambios."""
    import threading

    with _db_dsn("orbit_aj_b1c") as (conn, dsn_db):
        camp, _cfg = _siembra(conn)
        remoto = _remoto_b1(100.0, None)
        base, vistos = _handler_mezcla(remoto)
        _creds_fake(monkeypatch)
        pa = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        pb = _plan(
            conn, camp, CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=50)
        )
        b_en_put = threading.Event()
        a_termino = threading.Event()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                obj = json.loads(request.content)["campaigns"][0]
                es_b = any(
                    p["placement"] == "PLACEMENT_TOP" and p["percentage"] == 50
                    for p in obj.get("dynamicBidding", {}).get("placementBidding", [])
                )
                if es_b:
                    b_en_put.set()
                    assert a_termino.wait(timeout=30)
            return base(request)

        salidas: dict = {}

        def _tiro_b():
            import psycopg

            c = psycopg.connect(dsn_db)
            try:
                hecho = apply.aplica_ajuste_campana(
                    c,
                    pb,
                    huella=pb.huella(),
                    confirmacion="APLICAR AJUSTE",
                    actor="dueno",
                    transport=httpx.MockTransport(handler),
                )
                salidas["b"] = hecho.confirmada
            finally:
                c.close()

        hb = threading.Thread(target=_tiro_b)
        hb.start()
        assert b_en_put.wait(timeout=30)
        hecho_a = apply.aplica_ajuste_campana(
            conn,
            pa,
            huella=pa.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        a_termino.set()
        hb.join(timeout=60)
        assert hecho_a.confirmada is True
        assert salidas == {"b": True}
        assert remoto[EXTERNA]["budget"]["budget"] == 120.0
        tops = {
            p["placement"]: p["percentage"]
            for p in remoto[EXTERNA]["dynamicBidding"]["placementBidding"]
        }
        assert tops["PLACEMENT_TOP"] == 50


@_skip_db
def test_regresos_cruzados_solo_mueven_su_clase(monkeypatch):
    """B2 (seccion G): A = presupuesto 100->200, B = top +50. Regresar
    A deja el +50 % de B; regresar B no toca el presupuesto."""
    with _db("orbit_aj_b2g") as conn:
        camp, _cfg = _siembra(conn)
        remoto = _remoto_b1(100.0, None)
        handler, vistos = _handler_mezcla(remoto)
        _creds_fake(monkeypatch)
        transporte = httpx.MockTransport(handler)

        def _aplica(ajuste):
            plan = _plan(conn, camp, ajuste)
            return apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=transporte,
            )

        a1 = _aplica(CambiarPresupuesto(presupuesto_diario=Decimal("200")))
        a2 = _aplica(CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=50))
        r1 = apply.regresa_ajuste_campana(
            conn, ajuste_id=a1.ajuste_id, actor="dueno", transport=transporte
        )
        assert r1.confirmada is True
        put_a = _puts(vistos)[-1]
        assert sorted(k for k in put_a if k != "campaignId") == ["budget"]
        tops = {
            p["placement"]: p["percentage"]
            for p in remoto[EXTERNA]["dynamicBidding"]["placementBidding"]
        }
        assert tops["PLACEMENT_TOP"] == 50
        r2 = apply.regresa_ajuste_campana(
            conn, ajuste_id=a2.ajuste_id, actor="dueno", transport=transporte
        )
        assert r2.confirmada is True
        put_b = _puts(vistos)[-1]
        assert "budget" not in put_b
        assert remoto[EXTERNA]["budget"]["budget"] == 100.0


@_skip_db
def test_readback_sin_dynamic_bidding_conserva_vigente_previo(monkeypatch):
    """B1/mezcla: el PUT parcial de presupuesto no mueve placements; si
    el list no los trae, el vigente conserva strategy y tops previos."""
    with _db("orbit_aj_b1m") as conn:
        from app.ads.campana_config import config_vigente

        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        assert hecho.confirmada is True
        vigente = config_vigente(conn, camp)
        assert vigente.presupuesto_diario == Decimal("120")
        assert vigente.estrategia_puja == "LEGACY_FOR_SALES"
        assert (vigente.ajuste_top_pct, vigente.ajuste_producto_pct) == (0, 40)


@_skip_db
def test_regreso_con_vigente_ya_en_antes_no_manda_put(monkeypatch):
    """M10: el vigente volvio a antes por fuera (el sync observo un
    cambio manual): regresar responde AjusteYaRegresado sin PUT."""
    with _db("orbit_aj_m10") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        transporte = httpx.MockTransport(handler)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=transporte,
        )
        _observa(conn, camp, plan.antes)
        with pytest.raises(apply.AjusteYaRegresado):
            apply.regresa_ajuste_campana(
                conn, ajuste_id=hecho.ajuste_id, actor="dueno", transport=transporte
            )
        assert len(_puts(vistos)) == 1


@_skip_db
def test_aplica_campana_de_otra_plataforma_rechazado(monkeypatch):
    """M06: un plan cuya plataforma no es la de la campana no pasa."""
    with _db("orbit_aj_m06") as conn:
        from app.ads.campana_config import config_vigente

        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = planea_ajuste(
            config_vigente(conn, camp),
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
            campana_id=camp,
            plataforma="amazon_us",
        )
        with pytest.raises(ValueError, match="no es de amazon_us"):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )


@_skip_db
def test_regresa_ajuste_sin_confirmar_rechazado(monkeypatch):
    """M08: regresar un ajuste pendiente (PUT salio, readback fallo)
    responde AjusteNoConfirmado, sin segundo PUT."""
    with _db("orbit_aj_m08") as conn:
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
        (ajuste_id,) = conn.execute("SELECT id FROM campana_ajuste").fetchone()
        handler_ok, vistos = _handler_campanas(remoto)
        with pytest.raises(apply.AjusteNoConfirmado):
            apply.regresa_ajuste_campana(
                conn,
                ajuste_id=ajuste_id,
                actor="dueno",
                transport=httpx.MockTransport(handler_ok),
            )
        assert _puts(vistos) == []


@_skip_db
def test_aplica_entidad_que_no_es_campana_rechazado(monkeypatch):
    """M12: aplicar sobre una entidad que no es campana no pasa."""
    with _db("orbit_aj_m12") as conn:
        from app.ads.campana_config import config_vigente

        camp, _cfg = _siembra(conn)
        kw = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, match_type,"
            " keyword_text) VALUES ('amazon_mx', 'keyword', 'k-m12', 'EXACT', 't')"
            " RETURNING id"
        ).fetchone()[0]
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = planea_ajuste(
            config_vigente(conn, camp),
            CambiarPresupuesto(presupuesto_diario=Decimal("120")),
            campana_id=kw,
            plataforma="amazon_mx",
        )
        with pytest.raises(ValueError, match="no es de amazon_mx"):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )


@_skip_db
def test_put_207_con_error_de_tope_aborta(monkeypatch):
    """M42: un 207 con errores de tope lanza (no cuenta como exito)."""
    with _db("orbit_aj_m42") as conn:
        from app.ads.write import AdsApiErrorMutacion

        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        base, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                return httpx.Response(
                    207, json={"errors": [{"errorType": "topeError", "index": 0}]}
                )
            return base(request)

        with pytest.raises(AdsApiErrorMutacion):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )


@_skip_db
def test_put_207_con_error_anidado_aborta(monkeypatch):
    """M43: un 207 con errores anidados por item lanza."""
    with _db("orbit_aj_m43") as conn:
        from app.ads.write import AdsApiErrorMutacion

        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        base, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                return httpx.Response(
                    207,
                    json={"campaigns": [{"errors": [{"errorType": "budgetError"}], "index": 0}]},
                )
            return base(request)

        with pytest.raises(AdsApiErrorMutacion):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )


@_skip_db
def test_fila_visible_desde_otra_conexion_cuando_sale_el_put(monkeypatch):
    """B10: la intencion pre-HTTP es durable: cuando sale el PUT, otra
    conexion ya ve la fila pendiente (M01 muere aqui tambien)."""
    with _db_dsn("orbit_aj_put") as (conn, dsn_db):
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        base, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        visto: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                with psycopg.connect(dsn_db, autocommit=True) as otra:
                    visto["filas"] = otra.execute(
                        "SELECT id, confirmado_el IS NOT NULL FROM campana_ajuste"
                    ).fetchall()
            return base(request)

        c2 = psycopg.connect(dsn_db)
        try:
            hecho = apply.aplica_ajuste_campana(
                c2,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )
        finally:
            c2.close()
        assert visto["filas"] == [(hecho.ajuste_id, False)]


@_skip_db
def test_put_500_sale_como_error_de_amazon(monkeypatch):
    """No-bloq 3: PUT con 500 persistente lanza AdsApiError (502 por
    HTTP via _ERRORES_AJUSTE), no 500."""
    with _db("orbit_aj_e500") as conn:
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        base, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                return httpx.Response(500, json={"message": "caido"})
            return base(request)

        with pytest.raises(apply.AdsApiError):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )


@_skip_db
def test_readback_500_sale_como_error_de_amazon(monkeypatch):
    """No-bloq 3: readback con 500 persistente lanza AdsApiError (502
    por HTTP), con la fila pendiente reintentable."""
    with _db("orbit_aj_r500") as conn:
        import time

        camp, _cfg = _siembra(conn)
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        base, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path == "/sp/campaigns/list":
                return httpx.Response(500, json={"message": "caido"})
            return base(request)

        with pytest.raises(apply.AdsApiError):
            apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )
        fila = conn.execute(
            "SELECT confirmado_el IS NULL FROM campana_ajuste WHERE huella = %s",
            (plan.huella(),),
        ).fetchone()[0]
        assert fila is True


@_skip_db
def test_fila_regreso_visible_desde_otra_conexion_cuando_sale_el_put(monkeypatch):
    """M02: la intencion pre-HTTP del regreso es durable: cuando sale
    su PUT, otra conexion ya ve la fila hija pendiente."""
    with _db_dsn("orbit_aj_rput") as (conn, dsn_db):
        camp, _cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        base, _vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("120")))
        hecho = apply.aplica_ajuste_campana(
            conn,
            plan,
            huella=plan.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(base),
        )
        visto: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                with psycopg.connect(dsn_db, autocommit=True) as otra:
                    visto["filas"] = otra.execute(
                        "SELECT id, regresa_a, confirmado_el IS NOT NULL FROM campana_ajuste"
                        " WHERE regresa_a IS NOT NULL"
                    ).fetchall()
            return base(request)

        c2 = psycopg.connect(dsn_db)
        try:
            hijo = apply.regresa_ajuste_campana(
                c2,
                ajuste_id=hecho.ajuste_id,
                actor="dueno",
                transport=httpx.MockTransport(handler),
            )
        finally:
            c2.close()
        assert visto["filas"] == [(hijo.ajuste_id, hecho.ajuste_id, False)]


def _remoto_b11(estrategia="LEGACY_FOR_SALES", top=0, resto=0, producto=40):
    return {
        EXTERNA: {
            "budget": {"budget": 100.0, "budgetType": "DAILY"},
            "dynamicBidding": {
                "strategy": estrategia,
                "placementBidding": [
                    {"placement": "PLACEMENT_TOP", "percentage": top},
                    {"placement": "PLACEMENT_REST_OF_SEARCH", "percentage": resto},
                    {"placement": "PLACEMENT_PRODUCT_PAGE", "percentage": producto},
                ],
            },
        }
    }


def _tops_b11(remoto):
    return {
        p["placement"]: p["percentage"]
        for p in remoto[EXTERNA]["dynamicBidding"]["placementBidding"]
    }


@_skip_db
def test_ubicacion_no_toca_otro_placement_de_consola(monkeypatch):
    """B11: el dueno puso +30 % en resto en la consola tras el sync;
    cambiar arriba de busqueda manda SOLO ese placement."""
    with _db("orbit_aj_b11a") as conn:
        camp, _cfg = _siembra(conn)
        remoto = _remoto_b11(resto=30)
        handler, vistos = _handler_mezcla(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(
            conn, camp, CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=20)
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
        assert put["dynamicBidding"]["placementBidding"] == [
            {"placement": "PLACEMENT_TOP", "percentage": 20}
        ]
        assert _tops_b11(remoto)["PLACEMENT_REST_OF_SEARCH"] == 30


@_skip_db
def test_ubicacion_no_toca_strategy_de_consola(monkeypatch):
    """B11: el dueno cambio la estrategia en la consola tras el sync;
    el PUT lleva la actual (leida antes del PUT), no la del sync."""
    with _db("orbit_aj_b11b") as conn:
        camp, _cfg = _siembra(conn)
        remoto = _remoto_b11(estrategia="AUTO_FOR_SALES")
        handler, vistos = _handler_mezcla(remoto)
        _creds_fake(monkeypatch)
        plan = _plan(
            conn, camp, CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=20)
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
        assert put["dynamicBidding"]["strategy"] == "AUTO_FOR_SALES"
        assert remoto[EXTERNA]["dynamicBidding"]["strategy"] == "AUTO_FOR_SALES"


@_skip_db
def test_regresar_ubicacion_solo_restaura_su_placement(monkeypatch):
    """B11: A = top 0->20, B = resto 0->30. Regresar A deja el +30 % de
    B; regresar B no vuelve a subir A."""
    with _db("orbit_aj_b11c") as conn:
        camp, _cfg = _siembra(conn)
        remoto = _remoto_b11()
        handler, vistos = _handler_mezcla(remoto)
        _creds_fake(monkeypatch)
        transporte = httpx.MockTransport(handler)

        def _aplica(ubicacion, pct):
            plan = _plan(conn, camp, CambiarAjusteUbicacion(ubicacion=ubicacion, porcentaje=pct))
            return apply.aplica_ajuste_campana(
                conn,
                plan,
                huella=plan.huella(),
                confirmacion="APLICAR AJUSTE",
                actor="dueno",
                transport=transporte,
            )

        a1 = _aplica("arriba_de_busqueda", 20)
        a2 = _aplica("resto_de_busqueda", 30)
        r1 = apply.regresa_ajuste_campana(
            conn, ajuste_id=a1.ajuste_id, actor="dueno", transport=transporte
        )
        assert r1.confirmada is True
        assert _puts(vistos)[-1]["dynamicBidding"]["placementBidding"] == [
            {"placement": "PLACEMENT_TOP", "percentage": 0}
        ]
        assert _tops_b11(remoto)["PLACEMENT_REST_OF_SEARCH"] == 30
        r2 = apply.regresa_ajuste_campana(
            conn, ajuste_id=a2.ajuste_id, actor="dueno", transport=transporte
        )
        assert r2.confirmada is True
        assert _puts(vistos)[-1]["dynamicBidding"]["placementBidding"] == [
            {"placement": "PLACEMENT_REST_OF_SEARCH", "percentage": 0}
        ]
        assert _tops_b11(remoto).get("PLACEMENT_TOP", 0) == 0
