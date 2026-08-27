"""Tests del router de ESCRITURA `/api/ads-optimizer` (ORBIT 04, task 3.1).

Contrato sellado (plans/orbit-04.md decisiones 18, 20, 12, 4; docs/APPLY.md §10):

1. AUTH: token estatico en `<ORBIT_SECRETS_DIR>/api_write_token` (0600 en el
   server), SOLO header `x-orbit-token`, comparado con `hmac.compare_digest`.
   La query string JAMAS autentica (con test). Sin secrets dir / sin archivo /
   archivo vacio -> 503 FAIL-CLOSED con mensaje GENERICO (ADV-4: la razon
   especifica solo va al logger, no da oraculo sin autenticar), jamas
   fail-open. Sin `ORBIT_DSN_ADMIN` -> 503 (mensaje del DSN, estandar del
   repo). El guard de token corre ANTES de abrir el DSN (sin token Y sin DSN
   -> 401, no 503).
2. VETO: transicion a `vetoed` con actor (`vetoed_by`), `vence_el` editable
   al vetar (default 30d, sellado 3). Corre con el DSN admin (el trigger de
   0002 exige pg_has_role(current_user, 'app_admin')): la integracion crea un
   rol LOGIN temporal miembro de app_admin. Estados: pending_veto/released
   vetables; applying -> 409 (SOLO applying es punto de no retorno, sellado
   4); terminal -> 409; inexistente -> 404.
3. REVERSAS: `apply.reversa_manual` (regla 7, sellado 12) despachada por
   endpoints; mapeo de errores: ReversaNoAplicada -> 409,
   ReversaInexistente -> 404, NegativeIdNoResoluble -> 422, ReversaYaHecha ->
   409 "ya revertida" (ADV-3: una reversa es UNA por decision — segunda
   llamada sin segundo HTTP). El negative_id del DELETE sale SIEMPRE del ack
   del ledger de ESA fila (ADV-2: el body JAMAS lo acepta — regla 1, una
   fuente; borrar el negativo de OTRO con un id a mano es imposible).
   `transport` es la puerta de tests del camino HTTP (cero escrituras vivas:
   todo MockTransport, mismo patron que test_apply_cola).
4. PANTALLA /cortes: consume el endpoint GET /api/dashboard/cortes (regla 22,
   la UI no reimplementa queries); XSS del search_term cubierto (regla 9).

La integracion PG (skip fail-closed sin Postgres, patron test_apply_schema)
aplica 0001+0002 sobre una DB temporal; el rol admin temporal se suelta en el
teardown (REVOKE + DROP ROLE).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets as mod_secrets
import socket
from contextlib import contextmanager

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json
from test_schema import SQL, SQL2, _postgres_obligatorio_ausente, _test_dsn

from app import apply
from app.ads.config import AdsCredentials
from app.main import app

TOKEN = "token-escritura-de-test-314159"

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# ---------------------------------------------------------------------------
# Helpers de secrets (el token del archivo, jamas en el repo)
# ---------------------------------------------------------------------------


def _secrets_token(tmp_path, monkeypatch, token: str = TOKEN) -> None:
    """Siembra <tmp>/secrets/api_write_token y apunta ORBIT_SECRETS_DIR ahi."""
    d = tmp_path / "secrets"
    d.mkdir(exist_ok=True)
    (d / "api_write_token").write_text(token, encoding="utf-8")
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))


# ---------------------------------------------------------------------------
# UNIT (sin Postgres, corren siempre): auth fail-closed + superficie
# ---------------------------------------------------------------------------


def test_sin_header_token_401(tmp_path, monkeypatch):
    """DoD: sin el header x-orbit-token -> 401 (la dependencia revienta ANTES
    de abrir el DSN: no requiere Postgres)."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post("/api/ads-optimizer/veto", json={"queue_id": 1, "actor": "dueno"})
    assert resp.status_code == 401
    assert "x-orbit-token" in resp.json()["detail"]


def test_query_string_no_autentica(tmp_path, monkeypatch):
    """DoD: el token correcto en la query string (incluso con el MISMO nombre
    del header) NO autentica: solo header, jamas fail-open por query."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post(
        "/api/ads-optimizer/veto",
        params={"x-orbit-token": TOKEN, "token": TOKEN},
        json={"queue_id": 1, "actor": "dueno"},
    )
    assert resp.status_code == 401


def test_token_incorrecto_401(tmp_path, monkeypatch):
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post(
        "/api/ads-optimizer/veto",
        json={"queue_id": 1, "actor": "dueno"},
        headers={"x-orbit-token": "no-es-el-token"},
    )
    assert resp.status_code == 401


# ADV-4: TODOS los 503 de configuracion del token comparten UN detalle
# generico — la razon especifica (sin dir / sin archivo / vacio / ilegible)
# va SOLO al logger.warning (scrubbed): un caller sin autenticar no recibe
# un oraculo del estado interno del server. El 503 de ORBIT_DSN_ADMIN no se
# toca (mensaje estandar del repo para DSNs).
DETAIL_TOKEN_503 = "escrituras no disponibles: configuracion de token incompleta"


def test_sin_secrets_dir_503_fail_closed_mensaje_generico(monkeypatch):
    """DoD: sin ORBIT_SECRETS_DIR -> 503 fail-closed con el detalle generico
    (ADV-4: la razon especifica NO viaja en la respuesta)."""
    monkeypatch.delenv("ORBIT_SECRETS_DIR", raising=False)
    resp = TestClient(app).post("/api/ads-optimizer/veto", json={"queue_id": 1, "actor": "dueno"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == DETAIL_TOKEN_503
    assert "ORBIT_SECRETS_DIR" not in resp.json()["detail"]


def test_dir_sin_archivo_de_token_503_mensaje_generico(tmp_path, monkeypatch):
    """DoD: secrets dir presente pero SIN el archivo api_write_token -> 503
    generico (ADV-4: sin nombrar el archivo en la respuesta)."""
    d = tmp_path / "secrets-vacios"
    d.mkdir()
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))
    resp = TestClient(app).post("/api/ads-optimizer/veto", json={"queue_id": 1, "actor": "dueno"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == DETAIL_TOKEN_503
    assert "api_write_token" not in resp.json()["detail"]


def test_token_vacio_503_mensaje_generico(tmp_path, monkeypatch):
    """DoD: archivo presente pero VACIO -> 503 generico (ADV-4)."""
    d = tmp_path / "secrets-vacio"
    d.mkdir()
    (d / "api_write_token").write_text("   \n", encoding="utf-8")
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))
    resp = TestClient(app).post("/api/ads-optimizer/veto", json={"queue_id": 1, "actor": "dueno"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == DETAIL_TOKEN_503


def test_token_ok_sin_dsn_admin_503(tmp_path, monkeypatch):
    """DoD: token correcto PERO sin ORBIT_DSN_ADMIN -> 503 fail-closed con
    mensaje claro (la escritura no puede operar sin el DSN admin)."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post(
        "/api/ads-optimizer/veto",
        json={"queue_id": 1, "actor": "dueno"},
        headers={"x-orbit-token": TOKEN},
    )
    assert resp.status_code == 503
    assert "ORBIT_DSN_ADMIN" in resp.json()["detail"]


def test_sin_token_y_sin_dsn_es_401_no_503(tmp_path, monkeypatch):
    """Orden de guards: sin token Y sin DSN -> 401 (el guard de auth corre
    ANTES de abrir la conexion; si el DSN se abriera primero seria 503)."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post("/api/ads-optimizer/veto", json={"queue_id": 1, "actor": "dueno"})
    assert resp.status_code == 401


def test_cuerpo_invalido_sin_token_sigue_siendo_401(tmp_path, monkeypatch):
    """Sin token, incluso un cuerpo invalido (queue_id 0) responde 401: el
    guard de auth va antes que la validacion del body."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post("/api/ads-optimizer/veto", json={"queue_id": 0, "actor": ""})
    assert resp.status_code == 401


def test_cuerpo_invalido_con_token_422(tmp_path, monkeypatch):
    """Con token correcto, el cuerpo valida los rangos sellados: queue_id >= 1,
    actor 1..200, dias 1..365."""
    import app.api_write as api_write

    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://fake:fake@127.0.0.1:5432/fake")

    class _ConnFake:
        def close(self):
            pass

    monkeypatch.setattr(api_write, "connect", lambda dsn, **kw: _ConnFake())
    cliente = TestClient(app)
    cuerpos = (
        {"queue_id": 0, "actor": "dueno"},
        {"queue_id": 1, "actor": ""},
        {"queue_id": 1, "actor": "x" * 201},
        {"queue_id": 1, "actor": "dueno", "dias": 0},
        {"queue_id": 1, "actor": "dueno", "dias": 366},
    )
    for cuerpo in cuerpos:
        resp = cliente.post(
            "/api/ads-optimizer/veto", json=cuerpo, headers={"x-orbit-token": TOKEN}
        )
        assert resp.status_code == 422, f"{cuerpo} deberia ser 422, no {resp.status_code}"


def test_toda_ruta_post_declara_exige_token_antes_de_la_conexion():
    """Candado de superficie: cada ruta POST del router de escritura declara
    `exige_token` como dependencia y ANTES que `_conexion_escritura` (guard de
    auth antes de abrir el DSN; introspeccion de dependant.dependencies)."""
    from fastapi.routing import APIRoute

    import app.api_write as api_write

    posts = [
        r for r in api_write.router.routes if isinstance(r, APIRoute) and set(r.methods) == {"POST"}
    ]
    assert posts, "el router de escritura no registro rutas POST"
    for ruta in posts:
        llamadas = [d.call for d in ruta.dependant.dependencies]
        assert api_write.exige_token in llamadas, f"{ruta.path} sin la dependencia exige_token"
        assert api_write._conexion_escritura in llamadas, (
            f"{ruta.path} sin la dependencia ConexionEscritura"
        )
        assert llamadas.index(api_write.exige_token) < llamadas.index(
            api_write._conexion_escritura
        ), f"{ruta.path}: el guard de auth va ANTES de abrir el DSN"


# ---------------------------------------------------------------------------
# UNIT: endpoints de reversa (conn falsa + reversa_manual monkeypatcheada)
# ---------------------------------------------------------------------------


class _ConnFake:
    def close(self):
        pass


def _reversa_monkeypatcheada(monkeypatch, tmp_path, resultado=None, error=None, captura=None):
    """Prepara el entorno del endpoint de reversa SIN Postgres: token al dia,
    connect devolviendo una conn falsa y reversa_manual espiada (resultado
    fijo o excepcion)."""
    import app.api_write as api_write

    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://fake:fake@127.0.0.1:5432/fake")
    monkeypatch.setattr(api_write, "connect", lambda dsn, **kw: _ConnFake())

    def _reversa(conn, *, tipo, **kw):
        if captura is not None:
            captura.clear()
            captura["tipo"] = tipo
            captura.update(kw)
        if error is not None:
            raise error
        return resultado

    monkeypatch.setattr(apply, "reversa_manual", _reversa)


def test_reversa_endpoints_pasando_tipo_e_identificadores(tmp_path, monkeypatch):
    """Cada endpoint despacha a reversa_manual con su tipo y su identificador
    (decision_id para bid; queue_id para pause y negative). ADV-2: el body de
    negative JAMAS acepta negative_id — el id sale del ledger, una fuente
    (regla 1); un extra en el body se ignora, no viaja al despacho."""
    captura: dict = {}
    _reversa_monkeypatcheada(
        monkeypatch, tmp_path, resultado={"tipo": "x", "confirmada": True}, captura=captura
    )
    cliente = TestClient(app)
    cabecera = {"x-orbit-token": TOKEN}

    r = cliente.post("/api/ads-optimizer/reversa/bid", json={"decision_id": 5}, headers=cabecera)
    assert r.status_code == 200
    assert captura == {"tipo": "bid", "decision_id": 5}

    r = cliente.post("/api/ads-optimizer/reversa/pause", json={"queue_id": 7}, headers=cabecera)
    assert r.status_code == 200
    assert captura == {"tipo": "pause", "queue_id": 7}

    r = cliente.post("/api/ads-optimizer/reversa/negative", json={"queue_id": 9}, headers=cabecera)
    assert r.status_code == 200
    assert captura == {"tipo": "negative", "queue_id": 9}

    # ADV-2 (regla 9): un negative_id en el body se IGNORA — contra el codigo
    # que lo reenviaba al despacho, este assert reventaba.
    r = cliente.post(
        "/api/ads-optimizer/reversa/negative",
        json={"queue_id": 9, "negative_id": "AJENO-999"},
        headers=cabecera,
    )
    assert r.status_code == 200
    assert captura == {"tipo": "negative", "queue_id": 9}


def test_reversa_endpoints_mapean_errores_409_404_422_ya_revertida(tmp_path, monkeypatch):
    """Mapeo sellado: ReversaNoAplicada -> 409, ReversaInexistente -> 404,
    NegativeIdNoResoluble -> 422, ReversaYaHecha -> 409 "ya revertida" (ADV-3);
    y la respuesta 200 lleva confirmada: bool."""
    for error, codigo, fragmento in (
        (apply.ReversaNoAplicada("no aplicada"), 409, None),
        (apply.ReversaInexistente("no existe"), 404, None),
        (apply.NegativeIdNoResoluble("sin id"), 422, None),
        (apply.ReversaYaHecha("ya revertida: decision 1"), 409, "ya revertida"),
    ):
        _reversa_monkeypatcheada(monkeypatch, tmp_path, error=error)
        resp = TestClient(app).post(
            "/api/ads-optimizer/reversa/bid",
            json={"decision_id": 1},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == codigo, f"{type(error).__name__} -> {resp.status_code}"
        if fragmento is not None:
            assert fragmento in resp.json()["detail"]

    _reversa_monkeypatcheada(
        monkeypatch, tmp_path, resultado={"tipo": "bid", "decision_id": 1, "confirmada": False}
    )
    resp = TestClient(app).post(
        "/api/ads-optimizer/reversa/bid",
        json={"decision_id": 1},
        headers={"x-orbit-token": TOKEN},
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["confirmada"] is False, (
        "confirmada false = reversa sellada como fallo en el ledger (el detalle vive ahi)"
    )


def test_reversa_sin_token_401(tmp_path, monkeypatch):
    _reversa_monkeypatcheada(monkeypatch, tmp_path, resultado={"tipo": "bid", "confirmada": True})
    resp = TestClient(app).post("/api/ads-optimizer/reversa/bid", json={"decision_id": 1})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# INTEGRACION PG16 (skip fail-closed): DB temporal + rol LOGIN admin temporal
# ---------------------------------------------------------------------------


@contextmanager
def _db_con_rol_admin(prefijo: str, *, con_decide: bool = False):
    """DB temporal (0001+0002) + rol LOGIN temporal miembro de app_admin.

    Yields (conn_semillas, dsn_admin, dsn_lectura): la conn siembra como
    orbit_test (dueno de las tablas de la DB temporal), el dsn_admin apunta a
    la MISMA DB con el rol temporal (miembro de app_admin, como orbit_admin en
    produccion) y el dsn_lectura es el del rol de test (lectura del dashboard).
    El rol se crea con CREATEROLE de orbit_test y se suelta en el teardown
    (REVOKE + DROP ROLE).

    `con_decide` (hallazgo del test de endpoint de reversa): las REVERSAS
    insertan filas de ledger y el INSERT de apply_attempt es SOLO de
    app_decide en 0002 — el DSN de escritura necesita AMBAS membresias para
    el camino reversa. DECLARADO para 4.1 (env por servicio): las migraciones
    no se tocan aqui."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    rol = f"orbit_wtest_{mod_secrets.token_hex(4)}"
    password = mod_secrets.token_urlsafe(24)
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # 0001: roles, esquema sellado, grants
        conn.execute(SQL2)  # 0002: cola de cortes, ledger, sellos de quota
        # CREATE ROLE es utility statement: NO admite parametros posicionales
        # (revienta con syntax error en $1); la password va como sql.Literal
        # (composicion segura de psycopg, no interpolacion de strings).
        admin.execute(
            pgsql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                pgsql.Identifier(rol), pgsql.Literal(password)
            )
        )
        admin.execute(pgsql.SQL("GRANT app_admin TO {}").format(pgsql.Identifier(rol)))
        if con_decide:
            admin.execute(pgsql.SQL("GRANT app_decide TO {}").format(pgsql.Identifier(rol)))
        yield (
            conn,
            make_conninfo(dsn, dbname=db, user=rol, password=password),
            make_conninfo(dsn, dbname=db),
        )
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        if con_decide:
            admin.execute(pgsql.SQL("REVOKE app_decide FROM {}").format(pgsql.Identifier(rol)))
        admin.execute(pgsql.SQL("REVOKE app_admin FROM {}").format(pgsql.Identifier(rol)))
        admin.execute(pgsql.SQL("DROP ROLE {}").format(pgsql.Identifier(rol)))
        admin.close()


def _entidad(conn, kind: str, external: str, parent=None) -> int:
    # keyword exige match_type/keyword_text por el CHECK ad_entity_keyword_coherente
    if kind == "keyword":
        return conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id,"
            " match_type, keyword_text) VALUES ('amazon_us', %s, %s, %s, 'EXACT', %s)"
            " RETURNING id",
            (kind, external, parent, f"kw {external}"),
        ).fetchone()[0]
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES ('amazon_us', %s, %s, %s) RETURNING id",
        (kind, external, parent),
    ).fetchone()[0]


def _semilla_veto(conn) -> dict:
    """Ciclo + campaign->ad_group->keyword + helpers de decision/cola/estados."""
    config = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-veto', '{}'::jsonb) RETURNING id"
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    camp = _entidad(conn, "campaign", "7001")
    ag = _entidad(conn, "ad_group", "7101", parent=camp)
    kw = _entidad(conn, "keyword", "7201", parent=ag)
    kw2 = _entidad(conn, "keyword", "7202", parent=ag)

    def decision(kind, entidad, term=None, *, old=None, new=None, moneda=None) -> int:
        return conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
            " data_observed_at, window_start, window_end, search_term, old_value,"
            " new_value, value_currency, inputs)"
            " VALUES (%s, %s, %s, %s, now() - interval '40 days', CURRENT_DATE - 60,"
            " CURRENT_DATE - 30, %s, %s, %s, %s, '{}'::jsonb) RETURNING id",
            (ciclo, entidad, kind, config, term, old, new, moneda),
        ).fetchone()[0]

    def encola(dec_id, entidad, kind, term=None) -> int:
        return conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term,"
            " decision_id, modo, estado, vence_el, request_payload)"
            " VALUES ('amazon_us', %s, %s, %s, %s, 'live', 'pending_veto',"
            " now() + interval '48 hours', '{}'::jsonb) RETURNING id",
            (entidad, kind, term, dec_id),
        ).fetchone()[0]

    def avanza(q, estado) -> None:
        sello = {
            "released": "released_at",
            "applying": "applying_at",
            "applied": "applied_at",
            "failed": "failed_at",
        }[estado]
        conn.execute(
            f"UPDATE apply_queue SET estado = %s, {sello} = now() WHERE id = %s", (estado, q)
        )

    return {
        "ciclo": ciclo,
        "ag": ag,
        "kw": kw,
        "kw2": kw2,
        "decision": decision,
        "encola": encola,
        "avanza": avanza,
    }


@_skip_db
def test_veto_sobre_pending_veto_con_actor_y_vence_el_default_30(tmp_path, monkeypatch):
    """DoD: veto sobre fila pending_veto -> 200; la fila queda vetoed con
    vetoed_by=actor y vence_el = vetoed_at + 30d (default sellado 3)."""
    with _db_con_rol_admin("orbit_w_veto1") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        q = s["encola"](s["decision"]("pause", s["kw"]), s["kw"], "pause")
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)

        resp = TestClient(app).post(
            "/api/ads-optimizer/veto",
            json={"queue_id": q, "actor": "dueno"},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert cuerpo["estado"] == "vetoed"
        assert cuerpo["vetoed_by"] == "dueno"
        fila = conn.execute(
            "SELECT estado, vetoed_by, vence_el - vetoed_at FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila[0] == "vetoed"
        assert fila[1] == "dueno"
        # now() es estable dentro de la sentencia: la diferencia es EXACTA.
        assert fila[2] == dt.timedelta(days=30), "el default del veto durable es 30d (sellado 3)"


@_skip_db
def test_veto_dias_editable_se_distingue_del_default(tmp_path, monkeypatch):
    """DoD: dias editable al vetar: 7 produce un vence_el distinguible del
    default 30 (mismo reloj de now() dentro de la sentencia)."""
    with _db_con_rol_admin("orbit_w_veto2") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        q1 = s["encola"](s["decision"]("pause", s["kw"]), s["kw"], "pause")
        q2 = s["encola"](
            s["decision"]("negative", s["ag"], term="zapato blanco"),
            s["ag"],
            "negative",
            "zapato blanco",
        )
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        cliente = TestClient(app)

        r1 = cliente.post(
            "/api/ads-optimizer/veto",
            json={"queue_id": q1, "actor": "dueno", "dias": 7},
            headers={"x-orbit-token": TOKEN},
        )
        r2 = cliente.post(
            "/api/ads-optimizer/veto",
            json={"queue_id": q2, "actor": "dueno"},
            headers={"x-orbit-token": TOKEN},
        )
        assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
        delta7 = dt.datetime.fromisoformat(r1.json()["vence_el"]) - dt.datetime.fromisoformat(
            r1.json()["vetoed_at"]
        )
        delta30 = dt.datetime.fromisoformat(r2.json()["vence_el"]) - dt.datetime.fromisoformat(
            r2.json()["vetoed_at"]
        )
        assert delta7 == dt.timedelta(days=7)
        assert delta30 == dt.timedelta(days=30)


@_skip_db
def test_veto_sobre_released_tambien_procede(tmp_path, monkeypatch):
    """DoD: released sigue vetable (espera quota FIFO, r2 grok)."""
    with _db_con_rol_admin("orbit_w_veto3") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        q = s["encola"](s["decision"]("pause", s["kw"]), s["kw"], "pause")
        s["avanza"](q, "released")
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)

        resp = TestClient(app).post(
            "/api/ads-optimizer/veto",
            json={"queue_id": q, "actor": "dueno"},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json()["estado"] == "vetoed"


@_skip_db
def test_veto_sobre_applying_409_en_vuelo(tmp_path, monkeypatch):
    """DoD: applying es punto de no retorno (sellado 4): 409 con el mensaje
    de en vuelo, jamas un veto silencioso."""
    with _db_con_rol_admin("orbit_w_veto4") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        q = s["encola"](s["decision"]("pause", s["kw"]), s["kw"], "pause")
        s["avanza"](q, "released")
        s["avanza"](q, "applying")
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)

        resp = TestClient(app).post(
            "/api/ads-optimizer/veto",
            json={"queue_id": q, "actor": "dueno"},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 409
        assert "en vuelo" in resp.json()["detail"]
        estado = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert estado == "applying", "el veto rechazado no toca la fila"


@_skip_db
def test_veto_sobre_terminal_409_e_inexistente_404(tmp_path, monkeypatch):
    with _db_con_rol_admin("orbit_w_veto5") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        q = s["encola"](s["decision"]("pause", s["kw"]), s["kw"], "pause")
        for estado in ("released", "applying", "applied"):
            s["avanza"](q, estado)  # applied es terminal
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        cliente = TestClient(app)
        cabecera = {"x-orbit-token": TOKEN}

        resp = cliente.post(
            "/api/ads-optimizer/veto", json={"queue_id": q, "actor": "dueno"}, headers=cabecera
        )
        assert resp.status_code == 409
        assert "terminal" in resp.json()["detail"]

        resp = cliente.post(
            "/api/ads-optimizer/veto", json={"queue_id": 99999, "actor": "dueno"}, headers=cabecera
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# INTEGRACION: pantalla de cortes (endpoint GET del dashboard + pagina /cortes)
# ---------------------------------------------------------------------------


@_skip_db
def test_cortes_dashboard_y_pantalla_con_xss(tmp_path, monkeypatch):
    """DoD UI: GET /api/dashboard/cortes devuelve SOLO pending_veto/released
    ordenadas por vence_el; la pagina /cortes renderiza con data-pantalla y el
    search_term (`<script>...`, el vector XSS real) queda ESCAPADO (regla 9)."""
    with _db_con_rol_admin("orbit_w_cortes") as (conn, dsn_admin, dsn_lectura):
        s = _semilla_veto(conn)
        payload = '<script>alert("xss")</script>'
        q1 = s["encola"](
            s["decision"]("negative", s["ag"], term=payload), s["ag"], "negative", payload
        )
        q2 = s["encola"](s["decision"]("pause", s["kw"]), s["kw"], "pause")
        s["avanza"](q2, "released")
        q3 = s["encola"](
            s["decision"]("negative", s["ag"], term="otro"), s["ag"], "negative", "otro"
        )
        for estado in ("released", "applying", "applied"):
            s["avanza"](q3, estado)  # terminal: JAMAS aparece

        monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
        cliente = TestClient(app)

        r = cliente.get("/api/dashboard/cortes")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [i["id"] for i in items] == [q1, q2], "orden por vence_el, sin terminales"
        fila_xss = items[0]
        assert fila_xss["search_term"] == payload
        assert fila_xss["estado"] == "pending_veto"
        assert fila_xss["external_id"] == "7101"
        assert fila_xss["familia"] == "term_cut"
        assert fila_xss["decision_id"] is not None
        assert all(i["estado"] in ("pending_veto", "released") for i in items)

        html = cliente.get("/cortes").text
        assert 'data-pantalla="cortes"' in html
        assert payload not in html, "el search_term crudo JAMAS viaja al navegador"
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# INTEGRACION: reversa_manual (PG + MockTransport, cero HTTP vivo)
# ---------------------------------------------------------------------------

PERFIL_US_RAW = {
    "profileId": 404040,
    "countryCode": "US",
    "currencyCode": "USD",
    "accountInfo": {"type": "seller", "name": "Test US", "validPaymentMethod": True},
}


def _handler_reversas():
    """Amazon mock del camino completo de reversa_manual: LWA + /v2/profiles
    (perfil US aceptado: MISMA resolucion del ciclo) + PUT de estado/bid +
    GET de readback + DELETE de negative. `vistos` registra cada request."""
    vistos: list[httpx.Request] = []
    remoto: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access-1", "expires_in": 3600})
        vistos.append(request)
        path, metodo = request.url.path, request.method
        if metodo == "GET" and path == "/v2/profiles":
            return httpx.Response(200, json=[PERFIL_US_RAW])
        body = json.loads(request.content) if request.content else {}
        if metodo == "GET":
            ext = request.url.params.get("keywordId") or request.url.params.get("targetId")
            fila: dict = {"keywordId": ext, "state": "enabled"}
            if ext in remoto and remoto[ext].startswith("bid:"):
                fila = {"keywordId": ext, "bid": remoto[ext].removeprefix("bid:")}
            return httpx.Response(200, json={"keywords": [fila]})
        if metodo == "PUT":
            ext = str(body.get("keywordId") or body.get("targetId"))
            if "state" in body:
                remoto[ext] = body["state"]
            else:
                remoto[ext] = f"bid:{body['bid']}"
            return httpx.Response(200, json={"ack": body})
        if metodo == "DELETE":
            return httpx.Response(200, json={"keywordId": body.get("keywordId"), "deleted": True})
        raise AssertionError(f"request inesperado: {metodo} {path}")

    return handler, vistos, remoto


def _creds_fake(monkeypatch) -> None:
    creds = AdsCredentials(
        client_id="fake-client-id",
        client_secret="fake-client-secret",
        refresh_token="fake-refresh-token",
    )
    monkeypatch.setattr(
        AdsCredentials, "from_secrets_dir", classmethod(lambda cls, *a, **kw: creds)
    )


def _mutaciones(vistos: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in vistos if r.method != "GET"]


@_skip_db
def test_reversa_manual_pause_sobre_fila_applied(tmp_path, monkeypatch):
    """Fila applied -> reversa_manual(tipo=pause) resuelve perfil por /v2/profiles
    (camino del ciclo), hace el HTTP de resume y devuelve confirmada True; el
    ledger queda con fila reversa EXENTA de quota (sellado 12)."""
    with _db_con_rol_admin("orbit_w_rev1") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        dec = s["decision"]("pause", s["kw"])
        q = s["encola"](dec, s["kw"], "pause")
        for estado in ("released", "applying", "applied"):
            s["avanza"](q, estado)
        handler, vistos, _remoto = _handler_reversas()
        _creds_fake(monkeypatch)

        resultado = apply.reversa_manual(
            conn, tipo="pause", queue_id=q, transport=httpx.MockTransport(handler)
        )

        assert resultado == {"tipo": "pause", "queue_id": q, "confirmada": True}
        puts = [r for r in _mutaciones(vistos) if r.method == "PUT"]
        assert [json.loads(p.content) for p in puts] == [{"keywordId": "7201", "state": "enabled"}]
        ledger = conn.execute(
            "SELECT tipo, quota_cobrada, resultado FROM apply_attempt WHERE decision_id = %s",
            (dec,),
        ).fetchall()
        assert ledger == [("reversa", False, "ok")], "reversa exenta de quota (sellado 12)"
        # una reversa NO limpia el cooldown ni re-escribe el resumen (sellado 12)
        resumen = conn.execute(
            "SELECT count(*) FROM decision_application WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert resumen == 0


@_skip_db
def test_reversa_manual_pause_fila_no_applied_revienta(tmp_path, monkeypatch):
    with _db_con_rol_admin("orbit_w_rev2") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        dec = s["decision"]("pause", s["kw"])
        q = s["encola"](dec, s["kw"], "pause")  # sigue pending_veto
        handler, _v, _r = _handler_reversas()
        _creds_fake(monkeypatch)

        with pytest.raises(apply.ReversaNoAplicada):
            apply.reversa_manual(
                conn, tipo="pause", queue_id=q, transport=httpx.MockTransport(handler)
            )


@_skip_db
def test_reversa_manual_bid_confirmada_y_no_aplicada(tmp_path, monkeypatch):
    """tipo=bid: exige decision_application.applied_cycle_id; con el resumen
    sellado hace el PUT con old_value y el readback confirma."""
    with _db_con_rol_admin("orbit_w_rev3") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        dec1 = s["decision"]("bid", s["kw"], old=1.00, new=0.88, moneda="USD")
        # segunda keyword: el schema sella UNA decision por entidad y ciclo
        dec2 = s["decision"]("bid", s["kw2"], old=1.00, new=0.77, moneda="USD")
        # dec1 confirmada aplicada; dec2 sin resumen
        conn.execute(
            "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack,"
            " verify_ok, applied_cycle_id) VALUES (%s, now(), %s, true, %s)",
            (dec1, Json({"ok": True}), s["ciclo"]),
        )
        handler, vistos, _remoto = _handler_reversas()
        _creds_fake(monkeypatch)

        resultado = apply.reversa_manual(
            conn, tipo="bid", decision_id=dec1, transport=httpx.MockTransport(handler)
        )
        assert resultado == {"tipo": "bid", "decision_id": dec1, "confirmada": True}
        puts = [r for r in _mutaciones(vistos) if r.method == "PUT"]
        assert [json.loads(p.content) for p in puts] == [{"keywordId": "7201", "bid": "1.00"}]

        with pytest.raises(apply.ReversaNoAplicada):
            apply.reversa_manual(
                conn, tipo="bid", decision_id=dec2, transport=httpx.MockTransport(handler)
            )


@_skip_db
def test_reversa_manual_negative_resuelve_id_del_ledger(tmp_path, monkeypatch):
    """negative_id None -> se resuelve del ULTIMO intento normal resultado ok
    del ledger, parseando el ack con el MISMO helper del camino de apply_cola."""
    with _db_con_rol_admin("orbit_w_rev4") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        dec = s["decision"]("negative", s["ag"], term="zapato blanco")
        q = s["encola"](dec, s["ag"], "negative", "zapato blanco")
        for estado in ("released", "applying", "applied"):
            s["avanza"](q, estado)
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
            " ack, resultado, finished_at)"
            " VALUES (%s, 1, 'normal', '{}'::jsonb, true, %s, 'ok', now())",
            (dec, Json({"negativeKeywordId": "n-77"})),
        )
        handler, vistos, _r = _handler_reversas()
        _creds_fake(monkeypatch)

        resultado = apply.reversa_manual(
            conn, tipo="negative", queue_id=q, transport=httpx.MockTransport(handler)
        )

        assert resultado["confirmada"] is True
        assert resultado["negative_id"] == "n-77"
        deletes = [r for r in _mutaciones(vistos) if r.method == "DELETE"]
        assert [json.loads(d.content) for d in deletes] == [{"keywordId": "n-77"}]


@_skip_db
def test_reversa_manual_negative_sin_ack_resoluble(tmp_path, monkeypatch):
    """Sin intento ok con ack legible -> NegativeIdNoResoluble (regla 3: el id
    jamas se inventa; el endpoint lo mapea a 422)."""
    with _db_con_rol_admin("orbit_w_rev5") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        dec = s["decision"]("negative", s["ag"], term="zapato blanco")
        q = s["encola"](dec, s["ag"], "negative", "zapato blanco")
        for estado in ("released", "applying", "applied"):
            s["avanza"](q, estado)
        # ledger SIN filas: nada de donde resolver el id
        handler, _v, _r = _handler_reversas()
        _creds_fake(monkeypatch)

        with pytest.raises(apply.NegativeIdNoResoluble):
            apply.reversa_manual(
                conn, tipo="negative", queue_id=q, transport=httpx.MockTransport(handler)
            )


@_skip_db
def test_reversa_manual_fila_o_decision_inexistente(tmp_path, monkeypatch):
    with _db_con_rol_admin("orbit_w_rev6") as (conn, dsn_admin, _dsn_l):
        handler, _v, _r = _handler_reversas()
        _creds_fake(monkeypatch)
        with pytest.raises(apply.ReversaInexistente):
            apply.reversa_manual(
                conn, tipo="pause", queue_id=99999, transport=httpx.MockTransport(handler)
            )
        with pytest.raises(apply.ReversaInexistente):
            apply.reversa_manual(
                conn, tipo="bid", decision_id=99999, transport=httpx.MockTransport(handler)
            )


# ---------------------------------------------------------------------------
# ADV-2/ADV-3 (cross-review de la task): el id del DELETE es del ledger y una
# reversa es UNA por decision — ambos demostrados fallando contra el codigo
# que aceptaba negative_id del body / repetia la reversa sin tope.
# ---------------------------------------------------------------------------


@_skip_db
def test_reversa_negative_por_endpoint_borra_el_id_del_ledger_no_el_del_body(tmp_path, monkeypatch):
    """ADV-2: el negative_id del body viajaba VERBATIM al DELETE de Amazon —
    permitia borrar un negativo AJENO con un id a mano. Tras el fix el body ya
    no acepta negative_id (pydantic ignora extras): el DELETE sale SIEMPRE del
    ack del ledger de ESA fila (regla 1, una fuente)."""
    with _db_con_rol_admin("orbit_w_adv2", con_decide=True) as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        dec = s["decision"]("negative", s["ag"], term="zapato blanco")
        q = s["encola"](dec, s["ag"], "negative", "zapato blanco")
        for estado in ("released", "applying", "applied"):
            s["avanza"](q, estado)
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
            " ack, resultado, finished_at)"
            " VALUES (%s, 1, 'normal', '{}'::jsonb, true, %s, 'ok', now())",
            (dec, Json({"negativeKeywordId": "n-propio-123"})),
        )
        handler, vistos, _r = _handler_reversas()
        # El endpoint NO recibe transport (produccion): la puerta de mock del
        # camino REAL es la construccion del cliente (_cliente_reversa).
        from app.ads.write import AdsWriteClient

        creds = AdsCredentials(
            client_id="fake-client-id",
            client_secret="fake-client-secret",
            refresh_token="fake-refresh-token",
        )

        def _cliente_mock(platform, *, transport):
            return AdsWriteClient(
                creds,
                platform=platform,
                profile_id=PERFIL_US_RAW["profileId"],
                modo_confirmado="live",
                transport=httpx.MockTransport(handler),
                sleep=lambda seconds: None,
            )

        monkeypatch.setattr(apply, "_cliente_reversa", _cliente_mock)
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)

        resp = TestClient(app).post(
            "/api/ads-optimizer/reversa/negative",
            json={"queue_id": q, "negative_id": "AJENO-999"},
            headers={"x-orbit-token": TOKEN},
        )

        assert resp.status_code == 200, resp.text
        deletes = [r for r in _mutaciones(vistos) if r.method == "DELETE"]
        assert [json.loads(d.content) for d in deletes] == [{"keywordId": "n-propio-123"}], (
            "el id del DELETE es SIEMPRE el del ledger de ESA fila, jamas el del body"
        )
        assert resp.json()["negative_id"] == "n-propio-123"


@_skip_db
def test_reversa_manual_no_repite_una_reversa_confirmada(tmp_path, monkeypatch):
    """ADV-3: la fila queda 'applied' y las reversas estan exentas de quota y
    del tope-3 — nada impedia llamar /reversa/* en loop con HTTP real
    ilimitado. Tras el fix, una reversa confirmada (tipo 'reversa' resultado
    'ok') bloquea la segunda llamada con ReversaYaHecha ANTES de despachar
    HTTP (regla 7: una reversa es UNA por decision)."""
    with _db_con_rol_admin("orbit_w_adv3") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        dec = s["decision"]("pause", s["kw"])
        q = s["encola"](dec, s["kw"], "pause")
        for estado in ("released", "applying", "applied"):
            s["avanza"](q, estado)
        handler, vistos, _r = _handler_reversas()
        _creds_fake(monkeypatch)
        transport = httpx.MockTransport(handler)

        primera = apply.reversa_manual(conn, tipo="pause", queue_id=q, transport=transport)
        assert primera["confirmada"] is True

        with pytest.raises(apply.ReversaYaHecha, match="ya revertida"):
            apply.reversa_manual(conn, tipo="pause", queue_id=q, transport=transport)

        puts = [r for r in _mutaciones(vistos) if r.method == "PUT"]
        assert len(puts) == 1, "la segunda llamada NO despacha un segundo HTTP"


@_skip_db
def test_reversa_manual_reversa_fallida_puede_reintentarse(tmp_path, monkeypatch):
    """La cara complementaria del ADV-3: solo una reversa CONFIRMADA (resultado
    'ok') bloquea — una reversa sellada como fallo se puede reintentar (el
    candado no sobre-bloquea el camino de recuperacion)."""
    with _db_con_rol_admin("orbit_w_adv3b") as (conn, dsn_admin, _dsn_l):
        s = _semilla_veto(conn)
        dec = s["decision"]("pause", s["kw"])
        q = s["encola"](dec, s["kw"], "pause")
        for estado in ("released", "applying", "applied"):
            s["avanza"](q, estado)
        # una reversa previa que FALLO (divergencia de readback): sello no-ok
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
            " ack, resultado, finished_at)"
            " VALUES (%s, 1, 'reversa', '{}'::jsonb, false, NULL,"
            " 'fallo:readback_sin_estado', now())",
            (dec,),
        )
        handler, vistos, _r = _handler_reversas()
        _creds_fake(monkeypatch)

        resultado = apply.reversa_manual(
            conn, tipo="pause", queue_id=q, transport=httpx.MockTransport(handler)
        )

        assert resultado["confirmada"] is True, "la reversa fallida NO bloquea el reintento"
        puts = [r for r in _mutaciones(vistos) if r.method == "PUT"]
        assert len(puts) == 1
