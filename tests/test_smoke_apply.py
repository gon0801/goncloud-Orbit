"""Tests del probe autorizado `tools/smoke_apply` — ORBIT 04, task 2.5.

La tarea esta marcada [tdd:skip:probe-produccion]: la CORRIDA real contra
Amazon la autoriza el dueno y la coordina el lead. Estos tests cubren la
LOGICA de la herramienta (regla 9): las puertas de autorizacion, el
fail-closed de la campana allowlisted, la secuencia ledger-probe PRE-HTTP ->
HTTP -> ack -> readback -> reversa -> verificacion de neto cero, y la
evidencia JSON saneada. DB temporal con el patron de test_apply (0001+0002
aplicadas contra el Postgres real del tunel con ORBIT_TEST_DSN, skip
fail-closed si no) + HTTP 100% mock (`httpx.MockTransport`): a Amazon NO
sale ninguna llamada real, ni siquiera el token LWA.

Candados (regla 9 en cada uno):

1. Autorizacion ausente (env vacio/no definido) -> exit != 0 SIN abrir nada:
   espia que from_secrets_dir y connect JAMAS corren.
2. Flag --acepto-mutacion-real requerido (dos capas: env + flag).
3. La campana JAMAS se acepta por flag/env: argparse rechaza --campana.
4. Campana allowlisted SOLO desde config_version VIGENTE; sin clave ->
   fail-closed ANTES de tocar credenciales; config nueva sin clave la pisa.
5. bid_keyword: fila probe PRE-HTTP (regla 9: el handler del mock VE la fila
   en la base ANTES de despachar el PUT) con payload EXACTO y sello.
6. Neto cero: la reversa restaura el bid ORIGINAL LEIDO (no nuevo-0.01).
7. negative y keyword: create+delete NETO CERO sobre termino basura.
8. --forma todas corre LAS CUATRO en orden (8 filas probe).
9. JAMAS consume quota: quota_cobrada=false en TODAS las filas probe y
   apply_quota_state queda VACIA (con caps sembrados).
10. Fallo a mitad (readback 500) -> reversa best-effort (el bid vuelve al
    original) + exit != 0.
11. Evidencia JSON sin secretos: un ack que ecoa el access token sale
    REDACTED (scrub).
12. Selectores puros: primera keyword EXACT de la campana CON bid (salta
    PHRASE, otras campanas y sin bid); primer target con bid; primer ad
    group.

RE-SELLADO contra el probe 2.5 (corrida autorizada del dueno 2026-08-26,
ledger apply_attempt ids 1-20, log out/smoke-apply-20260826.log): el mock
sirve los shapes REALES verificados (contenedores del body, enums UPPER,
bid NUMERO, ack 207 anidado, delete v3 por POST /delete que ARCHIVA y
readback por LIST — el GET directo sp responde 403, retirado).
"""

from __future__ import annotations

import json
import os
import socket
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import SQL, SQL2, _postgres_obligatorio_ausente, _test_dsn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import smoke_apply as sa  # noqa: E402

from app.ads.config import AdsCredentials  # noqa: E402
from app.ads.write import AdsWriteClient  # noqa: E402

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"
FAKE_PROFILE_US = 404040

# Identidad del "Amazon" del mock: campana allowlisted 7001 con su ad group
# 7101, una keyword EXACT con bid (7201) y un senuelo PHRASE (7202); la
# keyword EXACT 7299 pertenece a OTRA campana (9001) y el target 7301 si es
# de la 7001.
CAMPANA = "7001"
GRUPO = "7101"
# Items del list con el WIRE REAL del probe 2.5 (2026-08-26, apply_attempt
# 19-20): matchType/state UPPER y bid NUMERO.
KW_EXACTA = {
    "keywordId": "7201",
    "campaignId": CAMPANA,
    "adGroupId": GRUPO,
    "keywordText": "kw exacta de la campana",
    "matchType": "EXACT",
    "state": "ENABLED",
    "bid": 1.23,
}
KW_PHRASE = {
    "keywordId": "7202",
    "campaignId": CAMPANA,
    "adGroupId": GRUPO,
    "keywordText": "kw phrase de la campana",
    "matchType": "PHRASE",
    "state": "ENABLED",
    "bid": 2.00,
}
KW_OTRA_CAMPANA = {
    "keywordId": "7299",
    "campaignId": "9001",
    "adGroupId": "9101",
    "keywordText": "kw exacta de otra campana",
    "matchType": "EXACT",
    "state": "ENABLED",
    "bid": 9.99,
}
TARGET = {
    "targetId": "7301",
    "campaignId": CAMPANA,
    "adGroupId": GRUPO,
    "state": "ENABLED",
    "bid": 2.34,
}
AD_GROUP = {"adGroupId": GRUPO, "campaignId": CAMPANA, "name": "grupo smoke", "state": "ENABLED"}

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# ---------------------------------------------------------------------------
# Patron _db_temporal de test_apply (COPIADO; aplica 0001 + 0002)
# ---------------------------------------------------------------------------


@contextmanager
def _db_temporal(prefijo: str):
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # 0001: roles, esquema sellado, grants
        conn.execute(SQL2)  # 0002: cola de cortes, ledger, sellos de quota
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _semilla_config(conn, *, con_campaña: bool = True, auth: str | None = None) -> int:
    """Config vigente con la clave de campana smoke + un cap (para probar que
    el probe JAMAS toca quota aunque existan caps). `auth` siembra ADEMAS la
    clave ads_smoke_auth (CX5: el token efimero se compara contra ELLA)."""
    settings: dict = {"ads_apply_cap_amazon_us_bid": 5}
    if con_campaña:
        settings["ads_smoke_campaign_amazon_us"] = CAMPANA
    if auth is not None:
        settings["ads_smoke_auth"] = auth
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-smoke', %s) RETURNING id",
        (Json(settings),),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Mock del "Amazon" del smoke: lists + mutaciones con estado remoto
# ---------------------------------------------------------------------------


def _token_response(n: int = 1) -> httpx.Response:
    return httpx.Response(200, json={"access_token": f"fake-access-{n}", "expires_in": 3600})


def _handler_smoke(estado: dict, *, fallar_readback_bid: bool = False, eco_token: bool = False):
    """Handler MockTransport del perfil US con el WIRE REAL del probe 2.5
    (2026-08-26, ledger ids 1-20, log out/smoke-apply-20260826.log): los
    CUATRO lists con sus contenedores verificados, mutaciones de write.py en
    el contenedor del recurso (una entrada), ack 207 con success/error
    anidados y "delete" v3 por POST /delete con filtro que ARCHIVA (el item
    SIGUE en el list con state=ARCHIVED — la identidad viva lo ignora).
    `fallar_readback_bid` tumba el LIST de readback DESPUES de la primera
    mutacion (el readback ya no es GET: retirado, 403). `vistos` registra
    cada request para asserts de orden.
    """
    vistos: list[httpx.Request] = []
    proximo_id = [8000]
    hubo_mutacion = [False]

    def _eco(ack: dict) -> dict:
        if eco_token:
            ack["eco"] = "fake-access-1"
        return ack

    def _207(recurso: str, campo: str, nuevo: str) -> dict:
        # Ack 207 real (apply_attempt 13 y 16): success/error anidados.
        return {recurso: {"error": [], "success": [{"index": 0, campo: nuevo}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        vistos.append(request)
        path, metodo = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if metodo == "POST" and path.endswith("/list"):
            if fallar_readback_bid and hubo_mutacion[0]:
                return httpx.Response(500, json={"detail": "boom readback"})
            contenedor = {
                "/sp/keywords/list": "keywords",
                "/sp/targets/list": "targetingClauses",
                "/sp/adGroups/list": "adGroups",
                "/sp/negativeKeywords/list": "negativeKeywords",
            }[path]
            return httpx.Response(
                200, json={contenedor: estado[contenedor], "totalResults": len(estado[contenedor])}
            )
        if metodo == "GET":
            raise AssertionError("GET directo de sp: retirado (probe 2.5, 403)")
        if metodo == "PUT":
            # Contenedor del recurso (apply_attempt 1/18): UNA entrada.
            es_target = "targetingClauses" in body
            obj = body["targetingClauses"][0] if es_target else body["keywords"][0]
            campo = "targetId" if es_target else "keywordId"
            ext = str(obj[campo])
            for k in estado["keywords"] + estado["targetingClauses"]:
                if str(k.get(campo)) == ext:
                    k["bid"] = obj["bid"]  # bid NUMERO (apply_attempt 3)
            hubo_mutacion[0] = True
            recurso = "targetingClauses" if es_target else "keywords"
            return httpx.Response(207, json=_eco(_207(recurso, campo, ext)))
        if metodo == "POST" and path in ("/sp/keywords", "/sp/negativeKeywords"):
            es_negative = path == "/sp/negativeKeywords"
            contenedor = "negativeKeywords" if es_negative else "keywords"
            obj = body[contenedor][0]  # contenedor del recurso (probe 2.5)
            campo = "keywordId"
            proximo_id[0] += 1
            nuevo = str(int(proximo_id[0]))
            item = {
                campo: nuevo,
                "adGroupId": str(obj["adGroupId"]),
                "campaignId": str(obj["campaignId"]),
                "keywordText": obj["keywordText"],
                "matchType": obj.get("matchType"),  # wire: NEGATIVE_EXACT/EXACT
                "state": "ENABLED",  # wire UPPER (apply_attempt 19-20)
            }
            if not es_negative:
                item["bid"] = obj["bid"]  # NUMERO (apply_attempt 3)
            estado[contenedor].append(item)
            hubo_mutacion[0] = True
            return httpx.Response(200, json=_eco(_207(contenedor, campo, nuevo)))
        if metodo == "POST" and path.endswith("/delete"):
            # Delete v3 real (apply_attempt 14/17): filtro de ids; ARCHIVA.
            es_negative = path == "/sp/negativeKeywords/delete"
            filtro = "negativeKeywordIdFilter" if es_negative else "keywordIdFilter"
            ext = str(body[filtro]["include"][0])
            for contenedor in ("keywords", "negativeKeywords"):
                for k in estado[contenedor]:
                    if str(k.get("keywordId")) == ext:
                        k["state"] = "ARCHIVED"  # el item SIGUE en el list
            recurso = "negativeKeywords" if es_negative else "keywords"
            return httpx.Response(200, json=_eco(_207(recurso, "keywordId", ext)))
        raise AssertionError(f"request inesperado: {metodo} {path}")

    return handler, vistos


def _estado_inicial() -> dict:
    return {
        "keywords": [dict(KW_EXACTA), dict(KW_PHRASE), dict(KW_OTRA_CAMPANA)],
        "targetingClauses": [dict(TARGET)],
        "adGroups": [dict(AD_GROUP)],
        "negativeKeywords": [],
    }


def _creds() -> AdsCredentials:
    return AdsCredentials(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        refresh_token=FAKE_REFRESH_TOKEN,
    )


def _ctx(conn, handler):
    cliente = AdsWriteClient(
        _creds(),
        platform="amazon_us",
        profile_id=FAKE_PROFILE_US,
        modo_confirmado="live",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )
    return sa.ContextoSmoke(
        conn=conn,
        cliente=cliente,
        platform="amazon_us",
        campana=CAMPANA,
        profile_id=str(FAKE_PROFILE_US),
    )


def _lineas_json(stdout: str) -> list[dict]:
    return [json.loads(linea) for linea in stdout.splitlines() if linea.startswith("{")]


# ---------------------------------------------------------------------------
# 1-3. Puertas de autorizacion: nada corre por accidente (regla 9)
# ---------------------------------------------------------------------------


def test_autorizacion_ausente_no_abre_nada(monkeypatch, capsys):
    """Sin ORBIT_SMOKE_AUTH (o vacia/blanca): exit != 0 y NI conexion NI
    credenciales se tocan. Regla 9: sin la puerta ANTES de todo, un main que
    conectara primero dejaria el probe a un env olvidado de distancia."""
    llamadas = {"connect": 0, "secrets": 0}

    def _connect_espia(*a, **kw):
        llamadas["connect"] += 1

    def _secrets_espia(cls, *a, **kw):
        llamadas["secrets"] += 1
        return _creds()

    monkeypatch.setattr(sa, "connect", _connect_espia)
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(_secrets_espia))
    for env in (None, "", "   "):
        monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://u:p@127.0.0.1:5432/orbit")
        if env is None:
            monkeypatch.delenv(sa.AUTORIZACION_ENV, raising=False)
        else:
            monkeypatch.setenv(sa.AUTORIZACION_ENV, env)
        rc = sa.main(
            ["--forma", "bid_keyword", "--platform", "amazon_us", "--acepto-mutacion-real"]
        )
        assert rc == 2, f"env={env!r}: sin autorizacion efimera el probe no arranca"
        err = capsys.readouterr().err
        assert sa.AUTORIZACION_ENV in err
    assert llamadas == {"connect": 0, "secrets": 0}, "la puerta corre ANTES de abrir nada"


def test_flag_acepto_mutacion_real_requerido(monkeypatch, capsys):
    llamadas = {"connect": 0, "secrets": 0}
    monkeypatch.setattr(sa, "connect", lambda *a, **kw: llamadas.__setitem__("connect", 1))
    monkeypatch.setenv(sa.AUTORIZACION_ENV, "token-efimero-del-dueno")
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://u:p@127.0.0.1:5432/orbit")

    rc = sa.main(
        ["--forma", "bid_keyword", "--platform", "amazon_us"]
    )  # sin --acepto-mutacion-real

    assert rc == 2
    assert "--acepto-mutacion-real" in capsys.readouterr().err
    assert llamadas["connect"] == 0


def test_campana_jamas_se_acepta_por_flag():
    """La campana sacrificable vive SOLO en config (ceremonia de admin): el
    CLI ni siquiera ofrece el argumento. Regla 9: un --campana agregado
    manana romperia esta prueba."""
    with pytest.raises(SystemExit):
        sa.parse_args(["--forma", "bid_keyword", "--acepto-mutacion-real", "--campana", "7001"])


# ---------------------------------------------------------------------------
# 4. Campana allowlisted: solo config vigente, fail-closed (regla 9)
# ---------------------------------------------------------------------------


@_skip_db
def test_campana_allowlisted_solo_desde_config_vigente():
    with _db_temporal("orbit_smoke_cfg") as conn:
        _semilla_config(conn)
        assert sa.campana_allowlisted(conn, "amazon_us") == CAMPANA
        # Una config NUEVA (mas reciente) SIN la clave la pisa: fail-closed.
        conn.execute("INSERT INTO config_version (label, settings) VALUES ('nueva', '{}'::jsonb)")
        assert sa.campana_allowlisted(conn, "amazon_us") is None
        # Clave corrupta (no string): config ROTA es ruido, no silencio.
        conn.execute(
            "INSERT INTO config_version (label, settings)"
            " VALUES ('corrupta', '{\"ads_smoke_campaign_amazon_us\": 7001}'::jsonb)"
        )
        with pytest.raises(ValueError, match="ads_smoke_campaign_amazon_us"):
            sa.campana_allowlisted(conn, "amazon_us")


@_skip_db
def test_sin_clave_campaña_fail_closed_antes_de_credenciales(monkeypatch, capsys):
    """Config vigente SIN la clave: exit != 0 y from_secrets_dir JAMAS corre
    (la campana se resuelve ANTES de tocar secrets/HTTP)."""
    with _db_temporal("orbit_smoke_fc") as conn:
        _semilla_config(conn, con_campaña=False, auth="token-efimero")
        monkeypatch.setenv(sa.AUTORIZACION_ENV, "token-efimero")
        monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://u:p@127.0.0.1:5432/orbit")
        monkeypatch.setattr(sa, "connect", lambda *a, **kw: conn)
        secreto_tocado = []

        def _espia(cls, *a, **kw):
            secreto_tocado.append(1)
            return _creds()

        monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(_espia))

        rc = sa.main(
            ["--forma", "bid_keyword", "--acepto-mutacion-real", "--platform", "amazon_us"]
        )

        assert rc == 2
        assert "ads_smoke_campaign_amazon_us" in capsys.readouterr().err
        assert secreto_tocado == [], "la campana se resuelve ANTES de las credenciales"


# ---------------------------------------------------------------------------
# 3b. CX5 (cross-review): la capa 1 compara contra ads_smoke_auth de la
# config VIGENTE — cualquier string no-vacio ya NO basta
# ---------------------------------------------------------------------------


@_skip_db
def test_auth_diferente_de_la_clave_config_no_abre_credenciales(monkeypatch, capsys):
    """CX5: ORBIT_SMOKE_AUTH se compara con compare_digest contra la clave
    ads_smoke_auth de la config VIGENTE (sembrada con la misma ceremonia que
    la campana): un token DISTINTO → exit != 0 ANTES de tocar credenciales.
    Regla 9: el env no-vacio solo (codigo viejo) pasaba la capa 1."""
    with _db_temporal("orbit_smoke_auth2") as conn:
        _semilla_config(conn, auth="token-bueno")
        monkeypatch.setenv(sa.AUTORIZACION_ENV, "token-malo")
        monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://u:p@127.0.0.1:5432/orbit")
        monkeypatch.setattr(sa, "connect", lambda *a, **kw: conn)
        secreto_tocado = []

        def _espia(cls, *a, **kw):
            secreto_tocado.append(1)
            return _creds()

        monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(_espia))

        rc = sa.main(
            ["--forma", "bid_keyword", "--acepto-mutacion-real", "--platform", "amazon_us"]
        )

        assert rc == 2
        assert "ads_smoke_auth" in capsys.readouterr().err
        assert secreto_tocado == [], "el rechazo corre ANTES de las credenciales"


@_skip_db
def test_auth_sin_clave_en_config_fail_closed(monkeypatch, capsys):
    """CX5: config vigente SIN ads_smoke_auth → no hay contra que comparar:
    exit != 0 ANTES de credenciales (fail-closed, jamas env-solo)."""
    with _db_temporal("orbit_smoke_auth3") as conn:
        _semilla_config(conn)  # con campana, SIN ads_smoke_auth
        monkeypatch.setenv(sa.AUTORIZACION_ENV, "lo-que-sea")
        monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://u:p@127.0.0.1:5432/orbit")
        monkeypatch.setattr(sa, "connect", lambda *a, **kw: conn)
        secreto_tocado = []

        def _espia(cls, *a, **kw):
            secreto_tocado.append(1)
            return _creds()

        monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(_espia))

        rc = sa.main(
            ["--forma", "bid_keyword", "--acepto-mutacion-real", "--platform", "amazon_us"]
        )

        assert rc == 2
        assert "ads_smoke_auth" in capsys.readouterr().err
        assert secreto_tocado == []


@_skip_db
def test_auth_coincidente_pasa_la_capa_1(monkeypatch, capsys):
    """CX5 cara complementaria: el token que COINCIDE con ads_smoke_auth pasa
    la capa 1 (la corrida sigue hasta la puerta de campana — sembrada SIN
    clave aqui para que el test no abra credenciales ni HTTP)."""
    with _db_temporal("orbit_smoke_auth4") as conn:
        _semilla_config(conn, con_campaña=False, auth="token-bueno")
        monkeypatch.setenv(sa.AUTORIZACION_ENV, "token-bueno")
        monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://u:p@127.0.0.1:5432/orbit")
        monkeypatch.setattr(sa, "connect", lambda *a, **kw: conn)
        secreto_tocado = []

        def _espia(cls, *a, **kw):
            secreto_tocado.append(1)
            return _creds()

        monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(_espia))

        rc = sa.main(
            ["--forma", "bid_keyword", "--acepto-mutacion-real", "--platform", "amazon_us"]
        )

        assert rc == 2, "fallo por la campana (NO por el auth): la capa 1 paso"
        err = capsys.readouterr().err
        assert "ads_smoke_campaign_amazon_us" in err
        assert "ads_smoke_auth" not in err, "el auth coincidio: no es el motivo del rechazo"
        assert secreto_tocado == []


# ---------------------------------------------------------------------------
# 5-8. Las formas: ledger probe PRE-HTTP, sellado, neto cero, orden
# ---------------------------------------------------------------------------


@_skip_db
def test_bid_keyword_ledger_probe_pre_http_y_sellado():
    """Regla 9: el handler del mock mira la base AL DESPACHAR el PUT y exige
    que la fila probe YA exista (una implementacion HTTP-primero revienta
    aqui). Ademas: payload EXACTO, tipo probe, decision NULL, exenta, sellada."""
    with _db_temporal("orbit_smoke_bkw") as conn:
        _semilla_config(conn)
        estado = _estado_inicial()
        vistas_pre_http = []
        base, _ = _handler_smoke(estado)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token_response()
            if request.method == "PUT":
                vistas_pre_http.append(
                    conn.execute(
                        "SELECT count(*) FROM apply_attempt WHERE tipo = 'probe'"
                    ).fetchone()[0]
                )
            return base(request)

        rc = sa.corre_formas(_ctx(conn, handler), ["bid_keyword"])

        assert rc == 0
        assert vistas_pre_http == [1, 2], "cada PUT sale CON su fila probe ya nacida"
        filas = conn.execute(
            "SELECT decision_id, tipo, request_payload, quota_cobrada, ack, resultado,"
            " finished_at FROM apply_attempt ORDER BY id"
        ).fetchall()
        assert len(filas) == 2, "mutacion + reversa"
        for fila in filas:
            assert fila[0] is None and fila[1] == "probe" and fila[3] is False
            assert fila[4] is not None and fila[5] is not None and fila[6] is not None
        assert filas[0][2] == {"keywordId": "7201", "bid": "1.24"}, "payload EXACTO (+0.01)"
        assert filas[1][2] == {"keywordId": "7201", "bid": "1.23"}, "reversa con lo ORIGINAL LEIDO"


@_skip_db
def test_bid_keyword_neto_cero_restaura_lo_leido(capsys):
    with _db_temporal("orbit_smoke_nz") as conn:
        _semilla_config(conn)
        estado = _estado_inicial()
        handler, _ = _handler_smoke(estado)

        rc = sa.corre_formas(_ctx(conn, handler), ["bid_keyword"])

        assert rc == 0
        assert estado["keywords"][0]["bid"] == 1.23, "el Amazon remoto queda como estaba"
        ev = _lineas_json(capsys.readouterr().out)[0]
        assert ev["forma"] == "bid_keyword" and ev["ok"] is True and ev["neto_cero"] is True
        assert ev["pasos"][0]["bid_original"] == "1.23"


@_skip_db
def test_bid_target_neto_cero(capsys):
    with _db_temporal("orbit_smoke_btg") as conn:
        _semilla_config(conn)
        estado = _estado_inicial()
        handler, _ = _handler_smoke(estado)

        rc = sa.corre_formas(_ctx(conn, handler), ["bid_target"])

        assert rc == 0
        assert estado["targetingClauses"][0]["bid"] == 2.34
        ev = _lineas_json(capsys.readouterr().out)[0]
        assert ev["forma"] == "bid_target" and ev["neto_cero"] is True
        payloads = conn.execute("SELECT request_payload FROM apply_attempt ORDER BY id").fetchall()
        assert payloads[0][0] == {"targetId": "7301", "bid": "2.35"}
        assert payloads[1][0] == {"targetId": "7301", "bid": "2.34"}


@_skip_db
def test_negative_create_delete_neto_cero(capsys):
    with _db_temporal("orbit_smoke_neg") as conn:
        _semilla_config(conn)
        estado = _estado_inicial()
        handler, _ = _handler_smoke(estado)

        rc = sa.corre_formas(_ctx(conn, handler), ["negative"])

        assert rc == 0
        # Neto cero con el wire REAL: el "delete" v3 ARCHIVA — el negativo
        # basura queda operativamente MUERTO (state=ARCHIVED, ausente para la
        # identidad viva), no removido del list (probe 2.5, apply_attempt 14).
        assert [k["state"] for k in estado["negativeKeywords"]] == ["ARCHIVED"], (
            "neto cero: el negativo basura quedo archivado (operativamente muerto)"
        )
        ev = _lineas_json(capsys.readouterr().out)[0]
        assert ev["forma"] == "negative" and ev["ok"] is True and ev["neto_cero"] is True
        # Identidad completa: el POST y el DELETE viven en el ad group de la
        # campana allowlisted, sobre el termino basura de la corrida; enums y
        # filtro del wire REAL (apply_attempt 13-14).
        post, delete = (
            f[0]
            for f in conn.execute(
                "SELECT request_payload FROM apply_attempt ORDER BY id"
            ).fetchall()
        )
        assert post["adGroupId"] == GRUPO and post["campaignId"] == CAMPANA
        assert post["matchType"] == "NEGATIVE_EXACT" and post["state"] == "ENABLED"
        assert "zzsmoke" in post["keywordText"]
        assert set(delete) == {"negativeKeywordIdFilter"}


@_skip_db
def test_keyword_create_delete_neto_cero(capsys):
    """El corazon del harvest: POST + DELETE de keyword EXACT con NETO CERO.
    El bid del POST sale de una fuente REAL (el bid LEIDO de la primera
    keyword EXACT de la campana), jamas una constante inventada (regla 3)."""
    with _db_temporal("orbit_smoke_kwd") as conn:
        _semilla_config(conn)
        estado = _estado_inicial()
        handler, _ = _handler_smoke(estado)

        rc = sa.corre_formas(_ctx(conn, handler), ["keyword"])

        assert rc == 0
        # La keyword basura quedo ARCHIVED (delete v3): viva siguen las 3.
        assert len([k for k in estado["keywords"] if k["state"] != "ARCHIVED"]) == 3, (
            "la keyword basura quedo operativamente muerta (ARCHIVED)"
        )
        ev = _lineas_json(capsys.readouterr().out)[0]
        assert ev["forma"] == "keyword" and ev["ok"] is True and ev["neto_cero"] is True
        post = conn.execute("SELECT request_payload FROM apply_attempt ORDER BY id").fetchone()[0]
        assert post["bid"] == "1.23", "bid LEIDO de la primera EXACT (regla 3)"
        assert post["matchType"] == "EXACT" and post["state"] == "ENABLED"
        assert post["adGroupId"] == GRUPO


@_skip_db
def test_forma_todas_corre_las_cuatro_en_orden():
    with _db_temporal("orbit_smoke_todas") as conn:
        _semilla_config(conn)
        estado = _estado_inicial()
        handler, _ = _handler_smoke(estado)

        rc = sa.corre_formas(_ctx(conn, handler), list(sa.FORMAS))

        assert rc == 0
        tipos = conn.execute(
            "SELECT seq, tipo, quota_cobrada FROM apply_attempt ORDER BY id"
        ).fetchall()
        assert [f[0] for f in tipos] == list(range(1, 9)), "8 filas: 2 por forma"
        assert all(f[1] == "probe" and f[2] is False for f in tipos)
        # Estado final == estado inicial en TODO el remoto VIVO: el delete
        # v3 ARCHIVA (probe 2.5) — los items basura siguen en el list pero
        # operativamente muertos (la identidad viva los ignora).
        vivo = lambda items: [k for k in items if k.get("state") != "ARCHIVED"]  # noqa: E731
        assert vivo(estado["keywords"]) == _estado_inicial()["keywords"]
        assert vivo(estado["targetingClauses"]) == _estado_inicial()["targetingClauses"]
        assert vivo(estado["negativeKeywords"]) == []


@_skip_db
def test_jamas_consume_quota():
    """El probe es EXENTO por diseno (decision 23): con caps sembrados en la
    config, TODAS las filas nacen quota_cobrada=false y apply_quota_state
    queda VACIA. Regla 9: un probe que llamara consume_quota dejaria fila."""
    with _db_temporal("orbit_smoke_qta") as conn:
        _semilla_config(conn)
        handler, _ = _handler_smoke(_estado_inicial())

        rc = sa.corre_formas(_ctx(conn, handler), list(sa.FORMAS))

        assert rc == 0
        assert conn.execute("SELECT count(*) FROM apply_quota_state").fetchone()[0] == 0
        cobradas = conn.execute(
            "SELECT count(*) FROM apply_attempt WHERE quota_cobrada"
        ).fetchone()[0]
        assert cobradas == 0


# ---------------------------------------------------------------------------
# 9-10. Fallo a mitad: reversa best-effort + exit != 0 (fail-closed)
# ---------------------------------------------------------------------------


@_skip_db
def test_fallo_a_mitad_reversa_best_effort_y_exit_no_cero(capsys):
    """El PUT del bid ACEPTA, el readback revienta (500 ambiguo): la forma
    corre la reversa best-effort (el bid remoto vuelve al ORIGINAL), imprime
    el estado y sale != 0. Fail-closed: jamas 'continuamos igual'."""
    with _db_temporal("orbit_smoke_fail") as conn:
        _semilla_config(conn)
        estado = _estado_inicial()
        handler, _ = _handler_smoke(estado, fallar_readback_bid=True)

        rc = sa.corre_formas(_ctx(conn, handler), ["bid_keyword"])

        assert rc != 0
        assert estado["keywords"][0]["bid"] == 1.23, "reversa best-effort restaura el original"
        ev = _lineas_json(capsys.readouterr().out)[0]
        assert ev["ok"] is False and ev["forma"] == "bid_keyword"
        resultado_reversa = conn.execute(
            "SELECT resultado FROM apply_attempt ORDER BY id"
        ).fetchall()
        assert len(resultado_reversa) == 2, "la reversa dejo SU fila probe (best-effort)"


@_skip_db
def test_forma_todas_detiene_en_la_primera_falla():
    """--forma todas es fail-closed: la primera forma que falla DETIENE la
    corrida (las siguientes no corren: si un shape esta podrido, correr mas
    formas multiplica el riesgo, no la evidencia)."""
    with _db_temporal("orbit_smoke_stop") as conn:
        _semilla_config(conn)
        estado = _estado_inicial()
        base, _ = _handler_smoke(estado)

        def handler(request: httpx.Request) -> httpx.Response:
            # bid_keyword es la primera forma: su PUT falla 400.
            if request.method == "PUT" and request.url.path == "/sp/keywords":
                return httpx.Response(400, json={"detail": "INVALID_BID"})
            return base(request)

        rc = sa.corre_formas(_ctx(conn, handler), list(sa.FORMAS))

        assert rc != 0
        # Solo la forma que fallo dejo filas; bid_target/negative/keyword no corrieron.
        seqs = conn.execute("SELECT seq FROM apply_attempt ORDER BY id").fetchall()
        assert [s[0] for s in seqs] == [1], "las formas siguientes NO corren"
        assert estado["targetingClauses"][0]["bid"] == 2.34
        assert estado["negativeKeywords"] == []


# ---------------------------------------------------------------------------
# 11. Evidencia sin secretos
# ---------------------------------------------------------------------------


@_skip_db
def test_evidencia_json_sin_secretos(capsys):
    """Un ack que ECOA el access token (registrado por el cliente como
    secreto) sale REDACTADO en la evidencia: scrub es la ultima linea."""
    with _db_temporal("orbit_smoke_scrub") as conn:
        _semilla_config(conn)
        handler, _ = _handler_smoke(_estado_inicial(), eco_token=True)

        rc = sa.corre_formas(_ctx(conn, handler), ["bid_keyword"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "fake-access-1" not in out
        assert "REDACTED" in out


# ---------------------------------------------------------------------------
# 12. Selectores puros (sin DB ni HTTP)
# ---------------------------------------------------------------------------


def test_selectores_puros():
    kws = [KW_PHRASE, KW_OTRA_CAMPANA, KW_EXACTA, {**KW_EXACTA, "keywordId": "7203", "bid": None}]
    assert sa.primer_keyword_exacta_con_bid(kws, CAMPANA)["keywordId"] == "7201"
    assert sa.primer_keyword_exacta_con_bid(kws, "9001")["keywordId"] == "7299"
    assert sa.primer_keyword_exacta_con_bid(kws, "8888") is None
    assert (
        sa.primer_target_con_bid(_estado_inicial()["targetingClauses"], CAMPANA)["targetId"]
        == "7301"
    )
    assert sa.primer_target_con_bid(_estado_inicial()["targetingClauses"], "8888") is None
    assert (
        sa.primer_ad_group_de_campana(_estado_inicial()["adGroups"], CAMPANA)["adGroupId"] == GRUPO
    )
    assert sa.primer_ad_group_de_campana(_estado_inicial()["adGroups"], "8888") is None


def test_readback_bid_con_id_ajeno_no_confirma():
    """Reviewer P2 (post cross-review): el readback del smoke CRUZA el id de
    la fila leida contra el pedido — una respuesta con el id de OTRA entidad
    JAMAS devuelve su bid (id_cruzado False). Sin el cruce, filas[0] de una
    respuesta multi-entidad revertiria al bid ajeno y el neto-cero mentiria
    (regla 9: el mock devuelve el senuelo primero)."""

    class _ClienteSenuelo:
        def list_objects(self, path, body, profile_id=None):
            return httpx.Response(
                200,
                json={
                    "keywords": [
                        {"keywordId": "999", "bid": "7.77"},  # senuelo: otra entidad
                        {"keywordId": "321", "bid": "1.23"},
                    ]
                },
            )

    class _Ctx:
        cliente = _ClienteSenuelo()
        profile_id = "1"

    bid, paso = sa._paso_readback_bid(_Ctx(), es_keyword=True, ext="321")
    assert bid == Decimal("1.23"), "la fila de la entidad PEDIDA gana sobre el senuelo"
    assert paso["id_cruzado"] is True

    bid_ajeno, paso_ajeno = sa._paso_readback_bid(_Ctx(), es_keyword=True, ext="777")
    assert bid_ajeno is None, "un id que no esta en la respuesta JAMAS confirma"
    assert paso_ajeno["id_cruzado"] is False


# ---------------------------------------------------------------------------
# CR4 (CodeRabbit PR #27): el id del item creado puede venir bajo OTRO nombre
# (HIPOTESIS_SHAPES["campo_id_ack"]: keywordId; alternativa negativeKeywordId)
# — indexar creado["keywordId"] directo dejaba el termino basura VIVO: el
# KeyError subia al except de main DESPUES del POST y ANTES del DELETE.
# Tests SIN DB: el ledger probe se sirve de una conn stub (el candado es la
# LOGICA de la forma, no la persistencia).
# ---------------------------------------------------------------------------


class _ConnStub:
    """conn minima para nace/sella_probe sin DB: execute devuelve self (su
    fetchone da el attempt id) y commit es no-op."""

    def __init__(self):
        self.seq = 0

    def execute(self, _sql, _params=None):
        self.seq += 1
        return self

    def fetchone(self):
        return (self.seq,)

    def commit(self):
        pass


def _ctx_sin_db(handler) -> sa.ContextoSmoke:
    cliente = AdsWriteClient(
        _creds(),
        platform="amazon_us",
        profile_id=FAKE_PROFILE_US,
        modo_confirmado="live",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )
    return sa.ContextoSmoke(
        conn=_ConnStub(),
        cliente=cliente,
        platform="amazon_us",
        campana=CAMPANA,
        profile_id=str(FAKE_PROFILE_US),
    )


def _handler_negative_campo_id(estado: dict, campo_id: str | None):
    """Amazon del smoke donde el item creado en /sp/negativeKeywords guarda su
    id bajo `campo_id` (None = shape totalmente desconocido: el item NO trae
    ningun campo de id). El resto delega al handler base."""
    base, vistos = _handler_smoke(estado)
    proximo_id = [8000]

    def handler(request: httpx.Request) -> httpx.Response:
        path, metodo = request.url.path, request.method
        if metodo == "POST" and path == "/sp/negativeKeywords":
            # El objeto viaja como unica entrada del contenedor del recurso
            # (shape real fijado por el probe 2.5; antes objeto desnudo).
            body = json.loads(request.content)["negativeKeywords"][0]
            proximo_id[0] += 1
            nuevo = str(proximo_id[0])
            item = {
                "adGroupId": str(body["adGroupId"]),
                "campaignId": str(body["campaignId"]),
                "keywordText": body["keywordText"],
                "matchType": body.get("matchType"),
                "state": "enabled",
            }
            if campo_id is not None:
                item[campo_id] = nuevo
            estado["negativeKeywords"].append(item)
            return httpx.Response(200, json={(campo_id or "id"): nuevo})
        if metodo == "POST" and path == "/sp/negativeKeywords/delete":
            # Shape real (probe 2.5): POST /delete con filtro de ids.
            ext = str(json.loads(request.content)["negativeKeywordIdFilter"]["include"][0])
            estado["negativeKeywords"] = [
                k for k in estado["negativeKeywords"] if str(k.get(campo_id or "\0")) != ext
            ]
            return httpx.Response(200, json={"keywordId": ext, "deleted": True})
        return base(request)

    return handler, vistos


def test_negative_create_delete_con_campo_negativeKeywordId_completa_el_delete():
    """CR4: el list del readback devuelve el item con `negativeKeywordId` (NO
    `keywordId`): la forma lo reconoce, completa el DELETE con ese id y queda
    NETO CERO, cero excepcion. Regla 9: contra el codigo viejo
    creado["keywordId"] reventaba con KeyError tras el POST."""
    estado = _estado_inicial()
    handler, _ = _handler_negative_campo_id(estado, "negativeKeywordId")

    ev = sa.corre_forma(_ctx_sin_db(handler), "negative")

    assert ev["ok"] is True and ev["neto_cero"] is True
    assert estado["negativeKeywords"] == [], "neto cero con el id bajo OTRO nombre"
    delete = [p for p in ev["pasos"] if p["paso"] == "http_delete"]
    assert len(delete) == 1 and delete[0]["ok"] is True


def test_negative_create_delete_shape_desconocido_declara_residuo_sin_keyerror():
    """CR4 cara fea: el item hallado por identidad NO trae keywordId NI
    negativeKeywordId — no hay id para borrar. La forma JAMAS revienta con
    KeyError: registra los campos REALES del item en el paso de readback y
    cierra con error explicito de RESIDUO POSIBLE (se revisa a mano)."""
    estado = _estado_inicial()
    handler, _ = _handler_negative_campo_id(estado, None)

    ev = sa.corre_forma(_ctx_sin_db(handler), "negative")

    assert ev["ok"] is False and ev["neto_cero"] is False
    assert "RESIDUO POSIBLE" in ev["error"], "el residuo se declara, jamas se esconde"
    readback = [p for p in ev["pasos"] if p["paso"] == "readback_create"][0]
    assert readback["hallado"] is True and readback["id_creado"] is None
    assert readback["campos"] == sorted(estado["negativeKeywords"][0]), (
        "los campos reales del item quedan en la evidencia (re-sello del shape)"
    )
    assert len(estado["negativeKeywords"]) == 1, "sin id no hay DELETE: el basura queda (declarado)"


# ===========================================================================
# Cross-review del dueno shapes (codex+qwen): el readback de bids del smoke
# PAGINA (CX6 — la seleccion ya paginaba con listar_paginado; el readback
# quedo en la primera pagina y reportaria falsos fallos con la entidad
# elegida en pagina 2+). Test PURO (regla 9: rojo en out/tdd-red-shapes-cr.log
# contra el readback de UNA pagina).
# ===========================================================================


def test_readback_bid_del_smoke_pagina_hasta_hallar_la_entidad():
    """CX6: la entidad perturbada vive en la pagina 2 del LIST: el readback
    del smoke la HALLA (id cruzado). Regla 9: el readback de UNA pagina
    devolvia None con id_cruzado False y el probe reportaria un falso fallo
    aunque la reversa hubiera sido correcta."""

    class _ClientePaginado:
        def __init__(self):
            self.requests = []

        def list_objects(self, path, body, profile_id=None):
            self.requests.append(dict(body))
            if body.get("nextToken"):
                return httpx.Response(200, json={"keywords": [{"keywordId": "321", "bid": "1.23"}]})
            return httpx.Response(
                200,
                json={
                    "keywords": [{"keywordId": "999", "bid": "7.77"}],
                    "nextToken": "2",
                },
            )

    class _Ctx:
        cliente = _ClientePaginado()
        profile_id = "1"

    bid, paso = sa._paso_readback_bid(_Ctx(), es_keyword=True, ext="321")
    assert bid == Decimal("1.23"), "la entidad de la pagina 2 NO es ausente"
    assert paso["id_cruzado"] is True
