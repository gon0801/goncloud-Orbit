"""Tests de la cola de cortes (`app/apply_cola`) — ORBIT 04, task 2.2.

DB temporal con el patron de test_apply (0001+0002 aplicadas; corre contra el
Postgres real del tunel con ORBIT_TEST_DSN, skip fail-closed si no) + HTTP 100%
mock (`httpx.MockTransport`): CERO escrituras vivas a Amazon, ni siquiera el
token LWA. Los "secretos" son SIEMPRE falsos.

DoD de la tarea, un candado por test (regla 9 en cada uno):

1. Invariante corte<->cola demostrado fallando: 3 decisiones de corte
   (pause+negative+harvest) -> 3 filas en cola. Una implementacion que solo
   encolara negative dejaria 2 y el test revienta.
2. Shadow-mark: goal shadow -> filas modo='shadow' (el dueno practica el
   veto con candidatos reales).
3. Choque del unico parcial por clave de efecto: la decision igual deja fila
   en decision y el choque se registra en el resumen (semantica declarada).
4. UNA FILA SHADOW JAMAS LIBERA: libera_vencidos la ignora (sigue
   pending_veto, cero HTTP). El lado del trigger ya esta testeado en
   test_apply_schema; aqui el lado de la APP.
5. Skip por clave de efecto: en-vuelo y veto VIGENTE bloquean; veto VENCIDO
   no bloquea (el motor re-propone); skip_por_clave devuelve el subconjunto.
6. Carreras: veto en released gana o pierde LIMPIO contra claim (monkeypatch
   del consume_quota que vetA en el medio: el perdedor ve 0 filas, no aplica,
   cero HTTP); veto contra applying -> revienta (en vuelo).
7. Re-validacion negative descarta al que VENDIO en la ventana fresca
   (revision bitemporal: observacion nueva con orders>0 para una fecha DENTRO
   de la ventana — regla 9: solo cambia la evidencia).
8. Re-validacion negative descarta al que YA NO ALCANZA el umbral adaptativo
   fresco (evidencia del grupo pasa a elegible-alta entre decidir y liberar).
9. Re-validacion PAUSE descarta por umbral adaptativo fresco (caso separado
   del negative, regla 9: solo cambia la evidencia del grupo).
10. Descarte JAMAS post-cobro: el discard ocurre PRE-claim y PRE-quota (0 HTTP
    para el descartado; la quota del dia queda intacta para el).
11. Cap agotado -> cortes esperan FIFO (la MAS VIEJA se aplica primero) y la
    que espera SIGUE vetable (veto posterior procede).
12. Reversas pause/negative: HTTP correcto (resume / delete), ledger tipo
    reversa EXENTO de quota; secuencia aplicada completa (queue applied,
    resumen, applied_cycle_id del EJECUTOR).
13. Gracia/reactivacion: re-check con entidad ENABLED tras pause propio ->
    fila en reactivacion_manual (idempotente por PK) y discard del corte; la
    gracia activa descarta el corte SIGUIENTE sin re-insertar.

RE-SELLADO contra el probe 2.5 (corrida autorizada del dueno 2026-08-26,
ledger apply_attempt ids 1-20, log out/smoke-apply-20260826.log): el
readback de estado vive por LIST (GET directo retirado, 403) con states del
wire UPPER (ENABLED/PAUSED/ARCHIVED), el ack del POST de negatives es 207
con success/error anidados y el delete v3 es POST /delete con filtro. El
harvest (1 quota / 2 HTTPs, job al liberar) vive desde 2.3 en
app/apply_harvest con sus tests en test_apply_harvest.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from contextlib import contextmanager
from decimal import Decimal

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import SQL, SQL2, SQL3, _postgres_obligatorio_ausente, _test_dsn

from app.ads.config import AdsCredentials
from app.apply import Aplicador
from app.apply_cola import (
    MOTIVO_REACTIVACION_MANUAL,
    MOTIVO_VENDIO_EN_VENTANA,
    MOTIVO_YA_NO_CALIFICA,
    VENTANA_VETO,
    claves_bloqueadas,
    encola_cortes,
    libera_vencidos,
    reversa_negative,
    reversa_pause,
    skip_por_clave,
)

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"
FAKE_PROFILE_US = 404040

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
        conn.execute(SQL3)  # 0003: ads_optimizer_goal sin DEFAULT en piso/techo
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# Reloj FIJO por test (el modulo jamas esconde un now() en decisiones)
# ---------------------------------------------------------------------------


def _reloj() -> dt.datetime:
    """Ahora tz-aware: el reloj de LIBERACION. El de decision va 3 dias atras
    (la ventana de veto de 48h ya vencio: vence_el = decided + 48h < ahora)."""
    return dt.datetime.now(dt.UTC)


def _fechas(desde: dt.date, hasta: dt.date):
    dia = desde
    while dia <= hasta:
        yield dia
        dia += dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# Semilla: config con caps, ciclos, entidades, goal y observaciones
# ---------------------------------------------------------------------------


def _entidad(conn, kind: str, external: str, parent=None) -> int:
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


def _estado(conn, entidad: int, *, status: str = "ENABLED") -> None:
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " synced_at) VALUES (%s, 1.00, 'USD', %s, now())",
        (entidad, status),
    )


def _semilla(conn, *, caps: dict | None = None, goal_mode: str = "live") -> dict:
    """Config vigente con los caps pedidos, el ciclo que DECIDIO (hace 3d), el
    ciclo EJECUTOR live, campaign->ad_group->kw+kw2 con state (grupo tambien:
    la escalera de los cortes de termino exige state del GRUPO, la entidad que
    decide el ciclo) y el goal de plataforma."""
    settings = (
        dict(caps)
        if caps is not None
        else {
            "ads_apply_cap_amazon_us_pause": 2,
            "ads_apply_cap_amazon_us_negative": 5,
        }
    )
    ahora = _reloj()
    config_id = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-cola', %s) RETURNING id",
        (Json(settings),),
    ).fetchone()[0]
    ciclo_dec = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    ciclo_ejec = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test') RETURNING id"
    ).fetchone()[0]
    camp = _entidad(conn, "campaign", "7001")
    ag = _entidad(conn, "ad_group", "7101", parent=camp)
    kw = _entidad(conn, "keyword", "7201", parent=ag)
    kw2 = _entidad(conn, "keyword", "7202", parent=ag)
    for entidad in (ag, kw, kw2):
        _estado(conn, entidad)
    conn.execute(
        # Bounds EXPLICITOS (0003 quito el DEFAULT 0.10/2.50 de la DB; USD).
        "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, enabled, mode)"
        " VALUES ('platform', 'amazon_us', 55, 0.10, 2.50, 'USD', true, %s)",
        (goal_mode,),
    )
    return {
        "config": config_id,
        "ciclo_dec": ciclo_dec,
        "ciclo_ejec": ciclo_ejec,
        "run": run_id,
        "camp": camp,
        "ag": ag,
        "kw": kw,
        "kw2": kw2,
        "ahora": ahora,
    }


def _metrica(
    conn, run_id, entidad, fecha, *, clicks=0, cost=0, orders=0, ad_revenue=None, observed=None
) -> None:
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, 'USD', %s, %s, NULL, %s, %s, %s)",
        (
            entidad,
            fecha,
            observed or (dt.datetime.now(dt.UTC) - dt.timedelta(days=5)),
            Decimal(cost) if cost is not None else None,
            Decimal(ad_revenue) if ad_revenue is not None else None,
            clicks,
            orders,
            run_id,
        ),
    )


def _termino_obs(
    conn, run_id, grupo, term, fecha, *, clicks=0, cost=0, orders=0, ad_revenue=None, observed=None
) -> None:
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term,"
        " metric_date, observed_at, metric_currency, cost, clicks, orders, ad_revenue,"
        " is_asin_like, ingest_run_id) VALUES ('amazon_us', %s, %s, %s, %s, 'USD', %s, %s,"
        " %s, %s, false, %s)",
        (
            grupo,
            term,
            fecha,
            observed or (dt.datetime.now(dt.UTC) - dt.timedelta(days=5)),
            Decimal(cost) if cost is not None else None,
            clicks,
            orders,
            Decimal(ad_revenue) if ad_revenue is not None else None,
            run_id,
        ),
    )


def _decision_corte(
    conn,
    ciclo: int,
    config_id: int,
    entidad: int,
    kind: str,
    *,
    term=None,
    valor=None,
    moneda=None,
    decided_at: dt.datetime | None = None,
) -> int:
    """Decision de corte con la madurez del esquema (window_end <= decided-10d)
    y inputs congelados al shape del ciclo (el aplicador JAMAS los reusa para
    re-validar: la re-validacion RE-RESUELVE con evidencia fresca)."""
    dec = decided_at or (dt.datetime.now(dt.UTC) - dt.timedelta(days=3))
    inputs = {
        "motor": "bid" if kind == "pause" else "hygiene",
        "platform": "amazon_us",
        "modo": "live",
        "motivo": f"{kind}_umbral",
        # Dato VERBATIM del fixture (el aplicador jamas lo reusa): el umbral
        # pause representa una decision VIGENTE (100 desde CORTES 03).
        "corte": {"umbral_clicks_usado": 20 if kind != "pause" else 100, "elegible": False},
    }
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, new_value, value_currency,"
        " inputs) VALUES (%s, %s, %s, %s, %s, %s - interval '1 day', %s - 60, %s - 30, %s,"
        " %s, %s, %s) RETURNING id",
        (
            ciclo,
            entidad,
            kind,
            dec,
            config_id,
            dec,
            dec.date(),
            dec.date(),
            term,
            Decimal(valor) if valor is not None else None,
            moneda,
            Json(inputs),
        ),
    ).fetchone()[0]


def _encola_fila(
    conn,
    decision: int,
    entidad: int,
    kind: str,
    *,
    term=None,
    modo="live",
    vence=None,
    encolado=None,
    payload=None,
) -> int:
    ahora = dt.datetime.now(dt.UTC)
    return conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term, decision_id,"
        " modo, estado, vence_el, encolado_at, request_payload) VALUES ('amazon_us', %s, %s,"
        " %s, %s, %s, 'pending_veto', %s, %s, %s) RETURNING id",
        (
            entidad,
            kind,
            term,
            decision,
            modo,
            vence or (ahora - dt.timedelta(hours=1)),
            encolado or (ahora - dt.timedelta(days=3)),
            Json(payload if payload is not None else {}),
        ),
    ).fetchone()[0]


def _payload_pause(ext: str) -> dict:
    return {"keywordId": ext, "state": "PAUSED"}


def _payload_negative(grupo_ext: str, camp_ext: str, term: str) -> dict:
    # Espejo del wire REAL (probe 2.5, apply_attempt 13): matchType es el enum
    # NEGATIVE_* y state es OBLIGATORIO (enum UPPER).
    return {
        "adGroupId": grupo_ext,
        "campaignId": camp_ext,
        "keywordText": term,
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }


# ---------------------------------------------------------------------------
# Mock de la API de Ads: token + LIST de estado + PUT/POST/delete (transport
# espia) — shape REAL del probe 2.5 (2026-08-26, ledger ids 1-20, log
# out/smoke-apply-20260826.log)
# ---------------------------------------------------------------------------


def _handler_cortes(
    estados: dict[str, str] | None = None,
    *,
    get_404: tuple[str, ...] = (),
    ack_negative_sin_id: bool = False,
    delete_rechazado: bool = False,
):
    """Handler MockTransport: el readback de estado vivo es el POST de LISTA
    con states del wire UPPER — ENABLED/PAUSED/ARCHIVED (probe 2.5, apply_attempt
    19-20; el GET directo esta retirado, 403) — el PUT viaja como unica
    entrada del contenedor del recurso y el delete v3 es POST /delete con
    filtro. `estados` es el estado REMOTO por external_id; el PUT lo
    actualiza (el REQUEST ya viaja UPPER — sello 2026-08-27). Variante GK3:
    `get_404` hace que el LIST muera 404 (entidad muerta).
    `ack_negative_sin_id` sirve el 207 SIN id en success (CX4/GK6). Cuenta
    TODOS los requests de la API (el token LWA va a api.amazon.com, fuera)."""
    remoto = {"7201": "ENABLED", "7202": "ENABLED"} | dict(estados or {})
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access-1", "expires_in": 3600})
        vistos.append(request)
        body = json.loads(request.content) if request.content else {}
        if request.method == "POST" and request.url.path.endswith("/list"):
            if set(get_404) & set(remoto):
                return httpx.Response(404, json={"message": "not found"})
            contenedor, campo = (
                ("targetingClauses", "targetId")
                if request.url.path == "/sp/targets/list"
                else ("keywords", "keywordId")
            )
            filas = [{campo: ext, "state": remoto[ext]} for ext in remoto]
            return httpx.Response(200, json={contenedor: filas, "totalResults": len(filas)})
        if request.method == "PUT":
            # Contenedor del recurso (probe 2.5, apply_attempt 1): una entrada.
            obj = body["keywords"][0] if "keywords" in body else body["targetingClauses"][0]
            ext = str(obj.get("keywordId") or obj.get("targetId"))
            # El REQUEST ya lleva el enum UPPER (sello 2026-08-27 de la
            # corrida de reactivacion): el LIST responde el mismo wire.
            remoto[ext] = {"PAUSED": "PAUSED", "ENABLED": "ENABLED"}.get(obj["state"], obj["state"])
            return httpx.Response(207, json={"ack": obj})
        if request.method == "POST" and request.url.path == "/sp/negativeKeywords":
            # Ack 207 con success/error anidados por recurso (probe 2.5,
            # apply_attempt 13): el id vive en el primer success.
            if ack_negative_sin_id:
                return httpx.Response(207, json={"negativeKeywords": {"error": [], "success": []}})
            return httpx.Response(
                207,
                json={
                    "negativeKeywords": {
                        "error": [],
                        "success": [{"index": 0, "negativeKeywordId": "n-1"}],
                    }
                },
            )
        if request.method == "POST" and request.url.path == "/sp/negativeKeywords/delete":
            if delete_rechazado:
                # CX3: 207 con la fila en error[] — el rechazo por-item del
                # shape real del probe 2.5 (la fila viaja en error, no success).
                return httpx.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "error": [
                                {"index": 0, "code": "NOT_FOUND", "negativeKeywordId": "n-1"}
                            ],
                            "success": [],
                        }
                    },
                )
            return httpx.Response(
                207,
                json={
                    "negativeKeywords": {
                        "error": [],
                        "success": [{"index": 0, "negativeKeywordId": "n-1"}],
                    }
                },
            )
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    return handler, vistos


def _aplicador(conn, handler, ciclo_ejec: int) -> Aplicador:
    return Aplicador(
        conn,
        platform="amazon_us",
        profile_id=FAKE_PROFILE_US,
        credentials=AdsCredentials(
            client_id=FAKE_CLIENT_ID,
            client_secret=FAKE_CLIENT_SECRET,
            refresh_token=FAKE_REFRESH_TOKEN,
        ),
        cycle_id_ejecutor=ciclo_ejec,
        owner="test:cola",
        job_key="ads_optimizer:amazon_us",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _mutaciones(vistos: list[httpx.Request]) -> list[httpx.Request]:
    """Solo los HTTP de MUTACION (los /list son lecturas: el readback por LIST
    del probe 2.5 no es una mutacion)."""
    return [r for r in vistos if r.method != "GET" and not r.url.path.endswith("/list")]


# ---------------------------------------------------------------------------
# 1. Invariante corte<->cola (regla 9)
# ---------------------------------------------------------------------------


@_skip_db
def test_toda_decision_de_corte_del_ciclo_deja_fila_en_cola():
    """3 decisiones de corte (pause + negative + harvest) -> 3 filas. Regla 9:
    una implementacion que solo encolara negative dejaria 2 filas y este test
    reventaria (el invariante jamas deja un corte huerfano en decision)."""
    with _db_temporal("orbit_cola_inv") as conn:
        ids = _semilla(conn)
        _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        _decision_corte(
            conn,
            ids["ciclo_dec"],
            ids["config"],
            ids["ag"],
            "harvest",
            term="buen termino",
            valor=0.75,
            moneda="USD",
        )
        ahora = ids["ahora"]
        handler, _v = _handler_cortes()

        resumen = encola_cortes(
            conn,
            _aplicador(conn, handler, ids["ciclo_ejec"]),
            ids["ciclo_dec"],
            modo_envelope="live",
            ahora=ahora,
        )

        filas = conn.execute(
            "SELECT kind, familia, search_term, modo, estado, vence_el, request_payload"
            " FROM apply_queue ORDER BY id"
        ).fetchall()
        assert resumen.encoladas_live == 3 and resumen.encoladas_shadow == 0
        assert resumen.choques == []
        assert len(filas) == 3, "toda decision de corte deja fila (pause incluida)"
        assert [f[0] for f in filas] == ["pause", "negative", "harvest"]
        assert [f[1] for f in filas] == ["entity_cut", "term_cut", "term_cut"]
        assert (filas[0][2], filas[1][2], filas[2][2]) == (None, "zapato blanco", "buen termino")
        assert all(f[3] == "live" and f[4] == "pending_veto" for f in filas)
        for f in filas:
            assert f[5] == ahora + VENTANA_VETO, "ventana de veto de 48h al encolar"
        assert filas[0][6] == _payload_pause("7201")
        assert filas[1][6] == _payload_negative("7101", "7001", "zapato blanco")


# ---------------------------------------------------------------------------
# 2. Shadow-mark por goal (regla 9)
# ---------------------------------------------------------------------------


@_skip_db
def test_goal_shadow_encola_marcado_shadow():
    """El goal HOY no llega a live -> las filas nacen modo='shadow' para que
    el dueno practique el veto. Regla 9: una implementacion que encolara live
    mirando inputs.modo='live' (el congelado del ciclo) dejaria modo live."""
    with _db_temporal("orbit_cola_sh") as conn:
        ids = _semilla(conn, goal_mode="shadow")
        _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        handler, _v = _handler_cortes()

        resumen = encola_cortes(
            conn,
            _aplicador(conn, handler, ids["ciclo_ejec"]),
            ids["ciclo_dec"],
            modo_envelope="live",
            ahora=ids["ahora"],
        )

        assert resumen.encoladas_live == 0 and resumen.encoladas_shadow == 1
        modo = conn.execute("SELECT modo FROM apply_queue").fetchone()[0]
        assert modo == "shadow"


# ---------------------------------------------------------------------------
# 3. Choque del unico parcial: se registra en el resumen (semantica declarada)
# ---------------------------------------------------------------------------


@_skip_db
def test_choque_de_clave_en_vuelo_se_registra_y_la_decision_queda():
    """La clave ya esta en vuelo: el INSERT choca el unico parcial. La decision
    NO se borra (la fila en decision es append-only) y el choque vive en el
    resumen — el skip por clave va en el CICLO siguiente, antes de decidir."""
    with _db_temporal("orbit_cola_cho") as conn:
        ids = _semilla(conn)
        dec1 = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        _encola_fila(conn, dec1, ids["kw"], "pause", payload=_payload_pause("7201"))
        ciclo2 = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec2 = _decision_corte(conn, ciclo2, ids["config"], ids["kw"], "pause")
        handler, _v = _handler_cortes()

        resumen = encola_cortes(
            conn,
            _aplicador(conn, handler, ids["ciclo_ejec"]),
            ciclo2,
            modo_envelope="live",
            ahora=ids["ahora"],
        )

        assert resumen.encoladas_live == 0
        assert len(resumen.choques) == 1 and str(dec2) in resumen.choques[0]
        total = conn.execute("SELECT count(*) FROM apply_queue").fetchone()[0]
        assert total == 1, "el choque no duplica la clave en vuelo"
        existe = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM decision WHERE id = %s)", (dec2,)
        ).fetchone()[0]
        assert existe is True, "la decision sigue en decision (append-only)"


# ---------------------------------------------------------------------------
# 4. Una fila shadow JAMAS libera (lado de la app)
# ---------------------------------------------------------------------------


@_skip_db
def test_fila_shadow_jamas_libera():
    with _db_temporal("orbit_cola_shjl") as conn:
        ids = _semilla(conn)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(
            conn, dec, ids["kw"], "pause", modo="shadow", payload=_payload_pause("7201")
        )
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=ids["ahora"],
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.liberadas == 0 and res.aplicadas == 0 and res.descartadas == []
        estado = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert estado == "pending_veto", "la fila shadow no se toca"
        assert vistos == [], "cero HTTP de una fila shadow"


# ---------------------------------------------------------------------------
# 5. Skip por clave de efecto: en-vuelo y veto vigente bloquean
# ---------------------------------------------------------------------------


@_skip_db
def test_claves_bloqueadas_envuelo_veto_vigente_y_veto_vencido_libera():
    with _db_temporal("orbit_cola_skip") as conn:
        ids = _semilla(conn)
        ahora = ids["ahora"]
        dec1 = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        _encola_fila(conn, dec1, ids["kw"], "pause", payload=_payload_pause("7201"))
        dec2 = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="termino uno"
        )
        q_veto_vigente = _encola_fila(
            conn,
            dec2,
            ids["ag"],
            "negative",
            term="termino uno",
            vence=ahora + dt.timedelta(days=20),
        )
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                " vetoed_by = 'dueno', vence_el = %s WHERE id = %s",
                (ahora + dt.timedelta(days=20), q_veto_vigente),
            )
        finally:
            conn.execute("RESET ROLE")
        dec3 = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="termino dos"
        )
        q_veto_vencido = _encola_fila(
            conn,
            dec3,
            ids["ag"],
            "negative",
            term="termino dos",
            vence=ahora - dt.timedelta(days=1),
        )
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                " vetoed_by = 'dueno', vence_el = %s WHERE id = %s",
                (ahora - dt.timedelta(days=1), q_veto_vencido),
            )
        finally:
            conn.execute("RESET ROLE")

        bloqueadas = claves_bloqueadas(conn, "amazon_us", ahora)

        assert (ids["kw"], "entity_cut", None) in bloqueadas, "en-vuelo bloquea"
        assert (ids["ag"], "term_cut", "termino uno") in bloqueadas, "veto vigente bloquea"
        assert (ids["ag"], "term_cut", "termino dos") not in bloqueadas, (
            "veto VENCIDO no bloquea: el motor re-propone con fila nueva"
        )
        candidatos = {
            (ids["kw"], "entity_cut", None),
            (ids["ag"], "term_cut", "termino uno"),
            (ids["ag"], "term_cut", "termino dos"),
        }
        assert skip_por_clave(conn, "amazon_us", candidatos, ahora=ahora) == {
            (ids["kw"], "entity_cut", None),
            (ids["ag"], "term_cut", "termino uno"),
        }, "skip_por_clave devuelve el subconjunto bloqueado de los candidatos"


# ---------------------------------------------------------------------------
# 6. Carreras: veto en released contra claim; veto contra applying
# ---------------------------------------------------------------------------


@_skip_db
def test_veto_en_released_gana_limpio_contra_claim(monkeypatch):
    """El veto llega ENTRE la liberacion y el claim (inyectado via el cobro de
    quota): el claim atomico UPDATE ... WHERE estado='released' ve 0 filas y NO
    aplica — cero HTTP, cero ledger. El perdedor pierde LIMPIO."""
    import app.apply_cola
    from app.apply_cola import (  # noqa: F401 - existencia del simbolo
        consume_quota_y_sello as _cqs,
    )

    with _db_temporal("orbit_cola_carrera") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        d = ids["ahora"]
        # CORTES 03: 15 fechas x 7 = 105 clicks (>= 100) y cost 45 (>= 40):
        # la re-validacion debe dejar pasar la fila hasta la carrera del claim.
        for fecha in _fechas(d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)):
            _metrica(conn, ids["run"], ids["kw"], fecha, clicks=7, cost=3, orders=0)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, vistos = _handler_cortes()

        def quota_que_veta(conn, platform, kind):
            # El admin veta TODA fila released en el instante del cobro: la
            # carrera que el claim atomico tiene que perder limpio.
            conn.execute("SET ROLE app_admin")
            try:
                conn.execute(
                    "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                    " vetoed_by = 'dueno', vence_el = now() + interval '30 days'"
                    " WHERE estado = 'released'"
                )
            finally:
                conn.execute("RESET ROLE")
            return _cqs(conn, platform, kind)

        monkeypatch.setattr(app.apply_cola, "consume_quota_y_sello", quota_que_veta)

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=ids["ahora"],
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.liberadas == 1 and res.carreras_perdidas == 1
        assert res.aplicadas == 0
        fila = conn.execute(
            "SELECT estado, vetoed_by FROM apply_queue WHERE decision_id = %s", (dec,)
        ).fetchone()
        assert fila == ("vetoed", "dueno"), "el veto gano la carrera"
        assert _mutaciones(vistos) == [], "el perdedor del claim NO aplica"
        assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0] == 0

        # Veto contra una fila EN VUELO (applying): rechazado por schema.
        dec2 = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw2"], "pause")
        q2 = _encola_fila(conn, dec2, ids["kw2"], "pause", payload=_payload_pause("7202"))
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = now() WHERE id = %s", (q2,)
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = now() WHERE id = %s", (q2,)
        )
        conn.execute("SET ROLE app_admin")
        try:
            with pytest.raises(psycopg.errors.CheckViolation, match="en vuelo|applying"):
                conn.execute(
                    "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                    " vetoed_by = 'dueno' WHERE id = %s",
                    (q2,),
                )
        finally:
            conn.execute("RESET ROLE")


# ---------------------------------------------------------------------------
# 7. Re-validacion negative: al que VENDIO en la ventana fresca
# ---------------------------------------------------------------------------


@_skip_db
def test_revalida_negative_descarta_al_que_vendio_en_ventana_fresca():
    """La venta entra como observacion NUEVA (bitemporal) de una fecha DENTRO
    de la ventana fresca: el termino ya no califica (orders>0) -> discard
    'vendio_en_ventana'. Regla 9: entre decidir y liberar SOLO cambio la
    evidencia; nada mas se toco."""
    with _db_temporal("orbit_cola_vendio") as conn:
        ids = _semilla(conn)
        d = ids["ahora"]
        for fecha in _fechas(d.date() - dt.timedelta(days=40), d.date() - dt.timedelta(days=23)):
            _termino_obs(
                conn, ids["run"], ids["ag"], "zapato blanco", fecha, clicks=4, cost=4, orders=0
            )
        # La revision que llega DURANTE la ventana de veto: la misma fecha,
        # observada despues — el colapso DISTINCT ON elige la mas reciente.
        _termino_obs(
            conn,
            ids["run"],
            ids["ag"],
            "zapato blanco",
            d.date() - dt.timedelta(days=30),
            clicks=4,
            cost=4,
            orders=1,
            ad_revenue=20,
            observed=d - dt.timedelta(hours=1),
        )
        dec = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        q = _encola_fila(
            conn,
            dec,
            ids["ag"],
            "negative",
            term="zapato blanco",
            payload=_payload_negative("7101", "7001", "zapato blanco"),
        )
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.descartadas == [MOTIVO_VENDIO_EN_VENTANA]
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", MOTIVO_VENDIO_EN_VENTANA)
        assert _mutaciones(vistos) == [], "un descartado JAMAS genera HTTP de mutacion"
        sin_quota = conn.execute(
            "SELECT count(*) FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:negative",),
        ).fetchone()[0]
        assert sin_quota == 0, "el descartado no toco la quota del dia"


# ---------------------------------------------------------------------------
# 8. Re-validacion negative: umbral adaptativo fresco ya no alcanza
# ---------------------------------------------------------------------------


@_skip_db
def test_revalida_negative_descarta_por_umbral_adaptativo_fresco():
    """Al decidir el grupo no era elegible (fallback 40: clicks 60 >= 40). Al
    liberar, la evidencia fresca del grupo (hojas nuevas en las 2 fechas que
    la ventana de decidir excluyia) es elegible-alta: umbral 158 > clicks 60
    -> discard 'ya_no_califica'. SOLO cambio la evidencia (regla 9)."""
    with _db_temporal("orbit_cola_negum") as conn:
        ids = _semilla(conn)
        d = ids["ahora"]
        # Hoja kw: 15 fechas maduras (evidencia de grupo sin ventas).
        for fecha in _fechas(d.date() - dt.timedelta(days=30), d.date() - dt.timedelta(days=16)):
            _metrica(conn, ids["run"], ids["kw"], fecha, clicks=2, cost=1, orders=0)
        # Hoja kw2: SOLO las 2 fechas que la ventana de EVIDENCIA de decidir
        # (D_dec-10 = D_lib-12) excluyia — al liberar hacen el grupo elegible.
        for fecha in (d.date() - dt.timedelta(days=11), d.date() - dt.timedelta(days=10)):
            _metrica(
                conn, ids["run"], ids["kw2"], fecha, clicks=300, cost=30, orders=3, ad_revenue=30
            )
        # 630 clicks / 6 orders -> expected 105 -> umbral ceil(105*1.5)=158.
        for fecha in _fechas(d.date() - dt.timedelta(days=40), d.date() - dt.timedelta(days=23)):
            _termino_obs(
                conn, ids["run"], ids["ag"], "zapato blanco", fecha, clicks=4, cost=4, orders=0
            )
        dec = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        q = _encola_fila(
            conn,
            dec,
            ids["ag"],
            "negative",
            term="zapato blanco",
            payload=_payload_negative("7101", "7001", "zapato blanco"),
        )
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.descartadas == [MOTIVO_YA_NO_CALIFICA]
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", MOTIVO_YA_NO_CALIFICA)
        assert _mutaciones(vistos) == []


# ---------------------------------------------------------------------------
# 9. Re-validacion PAUSE: umbral adaptativo fresco ya no alcanza (caso separado)
# ---------------------------------------------------------------------------


@_skip_db
def test_revalida_pause_descarta_por_umbral_adaptativo_fresco():
    """Pause sobre kw: al decidir calificaba (fallback 100 con CORTES 03,
    clicks de la ventana 105 >= 100 con cost 45 >= 40). Al liberar la
    evidencia del grupo es elegible-alta (hojas nuevas fuera de la ventana de
    decidir): umbral 182 > 105 -> discard. Caso SEPARADO del negative (regla
    9) con el mismo mecanismo de evidencia."""
    with _db_temporal("orbit_cola_pausum") as conn:
        ids = _semilla(conn)
        d = ids["ahora"]
        # Re-siembra CORTES 03: 15 fechas x 7 = 105 clicks (>= 100) y cost
        # 15 x 3 = 45 (>= 40) para que la decision SEA viable al decidir.
        for fecha in _fechas(d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)):
            _metrica(conn, ids["run"], ids["kw"], fecha, clicks=7, cost=3, orders=0)
        for fecha in (d.date() - dt.timedelta(days=11), d.date() - dt.timedelta(days=10)):
            _metrica(
                conn, ids["run"], ids["kw2"], fecha, clicks=300, cost=30, orders=3, ad_revenue=30
            )
        # 726 clicks / 6 orders -> expected 121 -> umbral ceil(121*1.5)=182;
        # la ventana de cortes fresca de kw ve 15 fechas x 7 = 105 clicks < 182.
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.descartadas == [MOTIVO_YA_NO_CALIFICA]
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", MOTIVO_YA_NO_CALIFICA)
        assert _mutaciones(vistos) == []


# ---------------------------------------------------------------------------
# 10. Descarte JAMAS post-cobro (orden sellado)
# ---------------------------------------------------------------------------


@_skip_db
def test_descarte_jamas_post_cobro_quota_intacta_para_el_descartado():
    """Dos vencidas FIFO: la primera VENDIO (discard PRE-claim), la segunda
    califica y aplica. La quota se cobra DESPUES del discard exitoso: el
    descartado deja 0 HTTP y la quota del dia refleja SOLO al aplicado."""
    with _db_temporal("orbit_cola_dcobro") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_negative": 1})
        d = ids["ahora"]
        for fecha in _fechas(d.date() - dt.timedelta(days=40), d.date() - dt.timedelta(days=23)):
            _termino_obs(
                conn, ids["run"], ids["ag"], "zapato blanco", fecha, clicks=4, cost=4, orders=0
            )
            _termino_obs(
                conn, ids["run"], ids["ag"], "otro termino", fecha, clicks=4, cost=4, orders=0
            )
        _termino_obs(
            conn,
            ids["run"],
            ids["ag"],
            "zapato blanco",
            d.date() - dt.timedelta(days=30),
            clicks=4,
            cost=4,
            orders=1,
            ad_revenue=20,
            observed=d - dt.timedelta(hours=1),
        )
        dec_vendio = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        _encola_fila(
            conn,
            dec_vendio,
            ids["ag"],
            "negative",
            term="zapato blanco",
            encolado=d - dt.timedelta(days=3),
            payload=_payload_negative("7101", "7001", "zapato blanco"),
        )
        dec_ok = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="otro termino"
        )
        q_ok = _encola_fila(
            conn,
            dec_ok,
            ids["ag"],
            "negative",
            term="otro termino",
            encolado=d - dt.timedelta(days=2),
            payload=_payload_negative("7101", "7001", "otro termino"),
        )
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.descartadas == [MOTIVO_VENDIO_EN_VENTANA] and res.aplicadas == 1
        posts = [r for r in _mutaciones(vistos) if r.method == "POST"]
        assert len(posts) == 1
        assert json.loads(posts[0].content)["negativeKeywords"][0]["keywordText"] == (
            "otro termino"
        ), "el POST viaja en el contenedor del recurso (probe 2.5)"
        quota = conn.execute(
            "SELECT used, cap FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:negative",),
        ).fetchone()
        assert quota == (1, 1), "la quota del descartado queda intacta (usada por el aplicado)"
        estado_ok = conn.execute(
            "SELECT estado FROM apply_queue WHERE id = %s", (q_ok,)
        ).fetchone()[0]
        assert estado_ok == "applied"


# ---------------------------------------------------------------------------
# 11. Cap agotado: cortes esperan FIFO, la mas vieja primero, y siguen vetables
# ---------------------------------------------------------------------------


@_skip_db
def test_cap_agotado_espera_fifo_la_mas_vieja_y_sigue_vetable():
    with _db_temporal("orbit_cola_fifo") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 1})
        d = ids["ahora"]
        for entidad in (ids["kw"], ids["kw2"]):
            for fecha in _fechas(
                d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)
            ):
                # CORTES 03: 15 fechas x 7 = 105 clicks (>= 100) y cost 45
                # (>= 40): la pause debe SOBREVIVIR la re-validacion.
                _metrica(conn, ids["run"], entidad, fecha, clicks=7, cost=3, orders=0)
        dec1 = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q1 = _encola_fila(
            conn,
            dec1,
            ids["kw"],
            "pause",
            encolado=d - dt.timedelta(days=3),
            payload=_payload_pause("7201"),
        )
        ciclo2 = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec2 = _decision_corte(conn, ciclo2, ids["config"], ids["kw2"], "pause")
        q2 = _encola_fila(
            conn,
            dec2,
            ids["kw2"],
            "pause",
            encolado=d - dt.timedelta(days=2),
            payload=_payload_pause("7202"),
        )
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.aplicadas == 1 and res.sin_quota == 1
        puts = [r for r in _mutaciones(vistos) if r.method == "PUT"]
        assert [json.loads(p.content)["keywords"][0]["keywordId"] for p in puts] == ["7201"], (
            "cap 1: la MAS VIEJA (encolada_at menor) se aplica primero"
        )
        estados = conn.execute(
            "SELECT estado FROM apply_queue WHERE id IN (%s, %s) ORDER BY id", (q1, q2)
        ).fetchall()
        assert [e[0] for e in estados] == ["applied", "released"], (
            "la que espero por quota queda en released (SIGUE vetable)"
        )
        # La que espero sigue vetable: el veto del dueno procede sobre ella.
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                " vetoed_by = 'dueno', vence_el = now() + interval '30 days' WHERE id = %s",
                (q2,),
            )
        finally:
            conn.execute("RESET ROLE")
        vetoada = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q2,)).fetchone()[0]
        assert vetoada == "vetoed"


# ---------------------------------------------------------------------------
# 12. Secuencia aplicada completa + reversas pause/negative (exentas)
# ---------------------------------------------------------------------------


@_skip_db
def test_pause_se_aplica_y_reversa_pause_resume_exenta_de_quota():
    with _db_temporal("orbit_cola_revp") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 1})
        d = ids["ahora"]
        # CORTES 03: 15 fechas x 7 = 105 clicks (>= 100) y cost 45 (>= 40):
        # la pause debe SOBREVIVIR la re-validacion.
        for fecha in _fechas(d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)):
            _metrica(conn, ids["run"], ids["kw"], fecha, clicks=7, cost=3, orders=0)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = libera_vencidos(conn, "amazon_us", ahora=d, aplicador=aplicador)

        assert res.aplicadas == 1 and res.descartadas == []
        fila = conn.execute(
            "SELECT estado, applied_at IS NOT NULL FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("applied", True)
        resumen = conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen == (True, ids["ciclo_ejec"]), "applied_cycle_id = ciclo EJECUTOR"
        ejec = conn.execute(
            "SELECT applied_count FROM optimizer_cycle WHERE id = %s", (ids["ciclo_ejec"],)
        ).fetchone()[0]
        assert ejec == 1
        intento = conn.execute(
            "SELECT seq, tipo, request_payload, quota_cobrada, resultado FROM apply_attempt"
            " WHERE decision_id = %s ORDER BY seq",
            (dec,),
        ).fetchall()
        assert intento == [(1, "normal", _payload_pause("7201"), True, "ok")]

        from app.apply_cola import fila_cola

        ok = reversa_pause(conn, aplicador._cliente(), fila_cola(conn, q), tick=lambda: None)

        assert ok is True
        puts = [r for r in _mutaciones(vistos) if r.method == "PUT"]
        assert [json.loads(p.content) for p in puts] == [
            {"keywords": [{"keywordId": "7201", "state": "PAUSED"}]},
            {"keywords": [{"keywordId": "7201", "state": "ENABLED"}]},
        ], "contenedor del recurso (probe 2.5); la reversa resume la keyword"
        filas = conn.execute(
            "SELECT tipo, quota_cobrada, resultado FROM apply_attempt ORDER BY seq"
        ).fetchall()
        assert filas == [("normal", True, "ok"), ("reversa", False, "ok")], "reversa EXENTA"
        used = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:pause",),
        ).fetchone()[0]
        assert used == 1, "la reversa NO consume quota (sellado 12)"
        estado = conn.execute(
            "SELECT status FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert estado == "ENABLED", "cache con LO LEIDO del readback (wire UPPER)"


@_skip_db
def test_pause_con_readback_divergente_no_sella_ok_en_el_ledger():
    """Bug PR27-3 (BAJA): si el LIST fresco devuelve un estado legible que NO
    es PAUSED (Amazon no proceso el pause), la fila cerraba failed con
    verify_ok=False PERO el ledger sellaba resultado='ok' — afirmaba exito
    donde hubo divergencia (bids ya usan 'fallo:divergencia_readback', QW1; la
    reconciliacion de pausas, 'fallo:reconciliado_enabled'). Regla 9: contra
    el codigo viejo el resultado era 'ok' y el assert reventaba."""
    with _db_temporal("orbit_cola_divp") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 1})
        d = ids["ahora"]
        # CORTES 03: 15 fechas x 7 = 105 clicks (>= 100) y cost 45 (>= 40):
        # la pause debe SOBREVIVIR la re-validacion y llegar al PUT.
        for fecha in _fechas(d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)):
            _metrica(conn, ids["run"], ids["kw"], fecha, clicks=7, cost=3, orders=0)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        vistos: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            # Amazon NO procesa el pause: el PUT sale 200 pero el estado remoto
            # JAMAS cambia — el LIST fresco sigue devolviendo 'ENABLED'.
            if request.url.host == "api.amazon.com":
                return httpx.Response(
                    200, json={"access_token": "fake-access-1", "expires_in": 3600}
                )
            vistos.append(request)
            if request.method == "POST" and request.url.path.endswith("/list"):
                # Readback por LIST (probe 2.5: GET retirado), state del wire.
                return httpx.Response(
                    200, json={"keywords": [{"keywordId": "7201", "state": "ENABLED"}]}
                )
            if request.method == "PUT":
                return httpx.Response(207, json={"ack": json.loads(request.content)})
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
        res = libera_vencidos(conn, "amazon_us", ahora=d, aplicador=aplicador)

        assert res.aplicadas == 0
        fila = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()
        assert fila == ("failed",), "divergencia: la fila NO es applied"
        resumen = conn.execute(
            "SELECT verify_ok FROM decision_application WHERE decision_id = %s", (dec,)
        ).fetchone()
        assert resumen == (False,)
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert resultado == "fallo:divergencia_readback", (
            "estado legible distinto al pedido: el ledger JAMAS sella 'ok'"
        )
        estado = conn.execute(
            "SELECT status FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert estado == "ENABLED", "cache con LO LEIDO del readback (sellado 16)"


@_skip_db
def test_negative_se_aplica_y_reversa_negative_delete_exenta_de_quota():
    with _db_temporal("orbit_cola_revn") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_negative": 1})
        d = ids["ahora"]
        for fecha in _fechas(d.date() - dt.timedelta(days=40), d.date() - dt.timedelta(days=23)):
            _termino_obs(
                conn, ids["run"], ids["ag"], "zapato blanco", fecha, clicks=4, cost=4, orders=0
            )
        dec = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        q = _encola_fila(
            conn,
            dec,
            ids["ag"],
            "negative",
            term="zapato blanco",
            payload=_payload_negative("7101", "7001", "zapato blanco"),
        )
        handler, vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = libera_vencidos(conn, "amazon_us", ahora=d, aplicador=aplicador)

        assert res.aplicadas == 1
        posts = [r for r in _mutaciones(vistos) if r.url.path == "/sp/negativeKeywords"]
        assert json.loads(posts[0].content) == {
            "negativeKeywords": [_payload_negative("7101", "7001", "zapato blanco")]
        }, "contenedor del recurso (probe 2.5, apply_attempt 13)"

        from app.apply_cola import fila_cola

        ok = reversa_negative(
            conn, aplicador._cliente(), fila_cola(conn, q), "n-1", tick=lambda: None
        )

        assert ok is True
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [json.loads(x.content) for x in deletes] == [
            {"negativeKeywordIdFilter": {"include": ["n-1"]}}
        ], "delete v3: POST /delete con filtro (probe 2.5, apply_attempt 14)"
        filas = conn.execute(
            "SELECT tipo, quota_cobrada, resultado FROM apply_attempt ORDER BY seq"
        ).fetchall()
        assert filas == [("normal", True, "ok"), ("reversa", False, "ok")], "reversa EXENTA"
        used = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:negative",),
        ).fetchone()[0]
        assert used == 1, "la reversa NO consume quota"


# ---------------------------------------------------------------------------
# 13. Gracia de reactivacion: deteccion en el re-check + idempotencia
# ---------------------------------------------------------------------------


@_skip_db
def test_recheck_enabled_tras_pause_propio_marca_reactivacion_y_descarta():
    """Re-check con entidad ENABLED + pause propio verificado antes -> INSERT
    reactivacion_manual (idempotente por PK) y el corte SE descarta (gracia
    7d: el motor no vuelve a cortar). Un segundo corte de la misma entidad
    dentro de la gracia se descarta SIN re-insertar la deteccion."""
    with _db_temporal("orbit_cola_reac") as conn:
        ids = _semilla(conn)
        d = ids["ahora"]
        for fecha in _fechas(d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)):
            _metrica(conn, ids["run"], ids["kw"], fecha, clicks=5, cost=2, orders=0)
        # Pause propio verificado ANTES: decision de un ciclo viejo con
        # decision_application verify_ok TRUE.
        ciclo_viejo = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec_vieja = _decision_corte(
            conn,
            ciclo_viejo,
            ids["config"],
            ids["kw"],
            "pause",
            decided_at=d - dt.timedelta(days=20),
        )
        conn.execute(
            "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack,"
            " verify_ok) VALUES (%s, now(), '{}'::jsonb, true)",
            (dec_vieja,),
        )
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, vistos = _handler_cortes()  # estado remoto: ENABLED

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.descartadas == [MOTIVO_REACTIVACION_MANUAL]
        deteccion = conn.execute(
            "SELECT detectada_en FROM reactivacion_manual WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()
        assert deteccion is not None, "la deteccion abre la gracia de 7d"
        assert _mutaciones(vistos) == [], "no se corta una entidad en gracia"
        # Segundo corte de la MISMA entidad dentro de la gracia: discard sin
        # nueva deteccion (PK idempotente, detectada_en NO se mueve).
        ciclo3 = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec3 = _decision_corte(conn, ciclo3, ids["config"], ids["kw"], "pause")
        _encola_fila(conn, dec3, ids["kw"], "pause", payload=_payload_pause("7201"))
        vistos.clear()

        res2 = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res2.descartadas == [MOTIVO_REACTIVACION_MANUAL]
        total = conn.execute(
            "SELECT count(*), min(detectada_en) FROM reactivacion_manual"
        ).fetchone()
        assert total[0] == 1, "idempotente por PK"
        assert total[1] == deteccion[0], "detectada_en jamas se mueve (append-only)"
        assert _mutaciones(vistos) == []


# ---------------------------------------------------------------------------
# 14. ADV-02 (review adversaria): fila released que espero quota se REINTENTA
# al ciclo siguiente — misma secuencia, sin contar carrera
# ---------------------------------------------------------------------------


@_skip_db
def test_released_sin_quota_se_reintenta_al_dia_siguiente(monkeypatch):
    """Dia 1 con cap agotado -> la fila joven queda released (FIFO, vetable).
    Dia 2 con quota renovada -> el barrido la VE (ya liberada), la re-valida
    con evidencia fresca, cobra quota, reclama y aplica. Una fila ya released
    NO se re-libera y leerla JAMAS cuenta como carrera perdida. Regla 9:
    contra el _SQL_VENCIDAS que solo selecciona pending_veto, la fila queda
    released PARA SIEMPRE (clave de efecto bloqueada) y este test reventaria
    (hallazgo ADV-02: el corte muere en silencio)."""
    import app.apply_cola

    with _db_temporal("orbit_cola_rtry") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 1})
        d = ids["ahora"]
        for entidad in (ids["kw"], ids["kw2"]):
            for fecha in _fechas(
                d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)
            ):
                # CORTES 03: 15 fechas x 7 = 105 clicks (>= 100) y cost 45
                # (>= 40): la pause debe SOBREVIVIR la re-validacion.
                _metrica(conn, ids["run"], entidad, fecha, clicks=7, cost=3, orders=0)
        dec1 = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q1 = _encola_fila(
            conn,
            dec1,
            ids["kw"],
            "pause",
            encolado=d - dt.timedelta(days=3),
            payload=_payload_pause("7201"),
        )
        ciclo2 = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec2 = _decision_corte(conn, ciclo2, ids["config"], ids["kw2"], "pause")
        q2 = _encola_fila(
            conn,
            dec2,
            ids["kw2"],
            "pause",
            encolado=d - dt.timedelta(days=2),
            payload=_payload_pause("7202"),
        )
        handler, vistos = _handler_cortes()

        # Dia 1 (quota REAL): la mas vieja aplica, la joven queda released.
        res1 = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )
        assert res1.aplicadas == 1 and res1.sin_quota == 1
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q2,)).fetchone()[0]
            == "released"
        )

        # Dia 2: quota_date ancla al now() de la DB (un solo dia REAL por
        # test), asi que la renovacion entra por la MISMA puerta de quota; la
        # secuencia posterior (re-validacion + claim + HTTP) corre real.
        d2 = d + dt.timedelta(days=1)
        # preflight 1.4: el cobro va por consume_quota_y_sello (usada,
        # saturada) — la renovacion exitosa NO es transicion de cap.
        monkeypatch.setattr(
            app.apply_cola, "consume_quota_y_sello", lambda *_a, **_k: (True, False)
        )
        vistos.clear()
        res2 = libera_vencidos(
            conn, "amazon_us", ahora=d2, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res2.liberadas == 0, "una fila ya released NO se vuelve a liberar"
        assert res2.carreras_perdidas == 0, "leer una fila ya released NO es una carrera"
        assert res2.aplicadas == 1, "con quota renovada la fila waiting aplica"
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q2,)).fetchone()[0]
            == "applied"
        )
        puts = [r for r in _mutaciones(vistos) if r.method == "PUT"]
        assert [json.loads(p.content)["keywords"][0]["keywordId"] for p in puts] == ["7202"]
        # La clave de efecto queda LIBRE de nuevo (el motor puede re-decidir).
        assert (ids["kw2"], "entity_cut", None) not in claves_bloqueadas(conn, "amazon_us", d2)
        # La fila del dia 1 sigue aplicada (idempotente, no se re-toca).
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q1,)).fetchone()[0]
            == "applied"
        )


# ---------------------------------------------------------------------------
# 15. ADV-05 (review adversaria): re-validacion PRE-claim del HARVEST
# ---------------------------------------------------------------------------


@_skip_db
def test_revalida_harvest_sin_observaciones_frescas_descarta():
    """El harvest TAMBIEN se re-evalua contra la regla con evidencia FRESCA al
    reloj de LIBERACION (sellado 6: 'jamas se corta por silencio contra la
    regla'). Sin observaciones del termino en la ventana fresca no califica
    (regla 3: ausencia, jamas ceros inventados) -> discarded 'ya_no_califica'
    con CERO HTTP y sin job. Regla 9: contra el `return None` de harvest en
    _revalida, la cadena correria igual sobre evidencia rancia (el caso caro
    de ADV-05: POST del negativo + keyword con bid) y este test reventaria."""
    with _db_temporal("orbit_cola_hrev") as conn:
        ids = _semilla(
            conn,
            caps={"ads_apply_cap_amazon_us_harvest": 2, "ads_apply_cap_amazon_us_negative": 5},
        )
        d = ids["ahora"]
        # SIN _termino_obs: el termino no existe en la ventana fresca.
        dec = _decision_corte(
            conn,
            ids["ciclo_dec"],
            ids["config"],
            ids["ag"],
            "harvest",
            term="buen termino",
            valor=0.75,
            moneda="USD",
        )
        q = _encola_fila(conn, dec, ids["ag"], "harvest", term="buen termino", payload={})
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.descartadas == [MOTIVO_YA_NO_CALIFICA]
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", MOTIVO_YA_NO_CALIFICA)
        assert vistos == [], "un descartado jamas genera HTTP"
        assert conn.execute("SELECT count(*) FROM harvest_job").fetchone()[0] == 0, (
            "sin calificar no nace job (la cola manda)"
        )


# ===========================================================================
# Cross-review del dueno (codex+grok+qwen, ORBIT 04 P2): GET muerto en la
# re-validacion NO aborta el barrido y el ack del negative SIN id no es applied
# ===========================================================================


@_skip_db
def test_get_de_revalida_muerto_no_aborta_el_barrido():
    """GK3: el LIST fresco de la re-validacion de UNA pause muere (404,
    entidad muerta): ESA fila queda released CON NOTA y el barrido SIGUE el
    FIFO con las demas — el ciclo NO degrada por una entidad muerta. Regla
    9: la AdsApiError sin capturar abortaba libera_vencidos entero (la
    negative de atras jamas se procesaba) y este test reventaria."""
    with _db_temporal("orbit_cola_get404") as conn:
        ids = _semilla(
            conn,
            caps={"ads_apply_cap_amazon_us_pause": 2, "ads_apply_cap_amazon_us_negative": 5},
        )
        d = ids["ahora"]
        # La PAUSE (encolada PRIMERA: muere en su GET) y la NEGATIVE (segunda:
        # califica con evidencia fresca y SI tiene que aplicarse).
        dec_pause = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q_pause = _encola_fila(conn, dec_pause, ids["kw"], "pause", payload=_payload_pause("7201"))
        for fecha in _fechas(d.date() - dt.timedelta(days=40), d.date() - dt.timedelta(days=23)):
            _termino_obs(
                conn, ids["run"], ids["ag"], "zapato blanco", fecha, clicks=4, cost=4, orders=0
            )
        dec_neg = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        q_neg = _encola_fila(
            conn,
            dec_neg,
            ids["ag"],
            "negative",
            term="zapato blanco",
            encolado=d - dt.timedelta(days=2),
            payload=_payload_negative("7101", "7001", "zapato blanco"),
        )
        handler, vistos = _handler_cortes(get_404=("7201",))  # LIST muere 404

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.revalida_sin_respuesta == 1, "la nota del LIST muerto vive en el resumen"
        assert res.aplicadas == 1 and res.fallidas == 0, "la negative SI se proceso"
        pause = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q_pause,)).fetchone()[
            0
        ]
        assert pause == "released", "la fila del LIST muerto queda released (vetable, reintenta)"
        neg = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q_neg,)).fetchone()[0]
        assert neg == "applied"
        posts = [r for r in _mutaciones(vistos) if r.method == "POST"]
        assert len(posts) == 1, "el POST de la negative SI salio (FIFO no abortado)"


@_skip_db
def test_negative_con_ack_2xx_sin_id_no_es_applied():
    """CX4/GK6: el POST del negative responde 2xx con body SIN id resuelto:
    NO es applied — verify_ok False, ledger 'fallo:ack_sin_id' y fila failed
    (la clave de efecto NO se libera como applied). Regla 9: el verify viejo
    (isinstance(ack, dict)) era SIEMPRE True y este test reventaria."""
    with _db_temporal("orbit_cola_ackv") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_negative": 1})
        d = ids["ahora"]
        for fecha in _fechas(d.date() - dt.timedelta(days=40), d.date() - dt.timedelta(days=23)):
            _termino_obs(
                conn, ids["run"], ids["ag"], "zapato blanco", fecha, clicks=4, cost=4, orders=0
            )
        dec = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        q = _encola_fila(
            conn,
            dec,
            ids["ag"],
            "negative",
            term="zapato blanco",
            payload=_payload_negative("7101", "7001", "zapato blanco"),
        )
        handler, _v = _handler_cortes(ack_negative_sin_id=True)

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.aplicadas == 0 and res.fallidas == 1
        fila = conn.execute(
            "SELECT estado, failed_at IS NOT NULL FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("failed", True), "la clave NO se libera como applied"
        resumen = conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen == (False, None), "aplica SOLO con evidencia del ack"
        ledger = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert ledger == "fallo:ack_sin_id"


# ===========================================================================
# Probe 2.5 (corrida autorizada 2026-08-26, ledger probe ids 1-20, log
# out/smoke-apply-20260826.log): el readback de estado vivo vive por LIST y el
# enum del WIRE de la respuesta es UPPER — ENABLED/PAUSED/ARCHIVED
# (apply_attempt 19-20: targets list con ENABLED y PAUSED; 'userPaused' NO
# existe ni en la RESPUESTA ni en el REQUEST del PUT — el REQUEST quedo
# SELLADO UPPER el 2026-08-27). Tests PUROS
# (sin DB; regla 9: rojo demostrado en out/tdd-red-o4-shapes.log contra el
# lector viejo de filas[0] con states lower).
# ===========================================================================


def test_estado_leido_por_list_cruza_id_y_trae_el_wire_upper():
    """El list trae TODAS las filas: el estado se lee SOLO de la fila cuyo id
    cruza, en el vocabulario UPPER del wire real. Regla 9: el lector viejo
    (filas[0], states 'enabled'/'userPaused' lower) devolvia None con el
    senuelo primero y desconocia 'PAUSED'/'ARCHIVED'."""
    from app.apply import (
        ESTADO_WIRE_ARCHIVED,
        ESTADO_WIRE_ENABLED,
        ESTADO_WIRE_PAUSED,
        _estado_leido,
    )

    resp = httpx.Response(
        200,
        json={
            "keywords": [
                {"keywordId": "9999", "state": "PAUSED"},  # senuelo: OTRA entidad
                {"keywordId": "7201", "state": "ENABLED"},  # la pedida, SEGUNDA
            ]
        },
    )
    assert _estado_leido(resp, "keywords", "keywordId", "7201") == "ENABLED"
    assert _estado_leido(resp, "keywords", "keywordId", "7777") is None
    # Vocabulario del WIRE verificado (log del probe): UPPER, sin 'userPaused'.
    assert (ESTADO_WIRE_ENABLED, ESTADO_WIRE_PAUSED, ESTADO_WIRE_ARCHIVED) == (
        "ENABLED",
        "PAUSED",
        "ARCHIVED",
    )


def test_estado_archived_no_confirma_entidad_viva():
    """El "delete" v3 ARCHIVA (probe 2.5: state=ARCHIVED en el list tras el
    POST /delete con 207 success): una entidad ARCHIVED esta operativamente
    MUERTA — el estado vivo del pause SOLO acepta ENABLED del wire, jamas
    'enabled' lower ni cualquier otra cosa. Regla 9: contra el comparador
    viejo (estado != 'enabled'), un ARCHIVED lower-case pasaba por vivo."""
    from app.apply import ESTADO_WIRE_ARCHIVED, ESTADO_WIRE_ENABLED, _estado_leido

    resp = httpx.Response(
        200, json={"keywords": [{"keywordId": "7201", "state": ESTADO_WIRE_ARCHIVED}]}
    )
    estado = _estado_leido(resp, "keywords", "keywordId", "7201")
    assert estado == "ARCHIVED"
    assert estado != ESTADO_WIRE_ENABLED, "ARCHIVED NO es vivo: el corte es moot"


# ===========================================================================
# Cross-review del dueno shapes (codex+qwen, out/cross-review-shapes-*.log):
# literales del pause a UNA fuente (QW2), reversas verificadas (CX3/QW6) y
# readback de estado PAGINADO (CX1/QW1 — el body {} solo ve la primera
# pagina de una cuenta con 1334 keywords / 549 targets).
# ===========================================================================


def test_payload_pause_usa_las_constantes_de_write_una_sola_fuente(monkeypatch):
    """QW2: el state del REQUEST del pause/resume vive SOLO en write.py
    (ESTADO_PUT_*); apply lo re-exporta (el unico que puede importar write)
    y la cola lo usa. Regla 9: contra los literales sueltos (a) el re-export
    no existia (AttributeError) y (b) el sentinel NUNCA llegaba al payload —
    cambiar write.py dejaba el ledger del pause con el enum viejo."""
    from app import apply
    from app.ads.write import ESTADO_PUT_ENABLED, ESTADO_PUT_PAUSED
    from app.apply_cola import _payload_pause

    assert apply.ESTADO_PUT_PAUSED == ESTADO_PUT_PAUSED, "apply re-exporta la constante"
    assert apply.ESTADO_PUT_ENABLED == ESTADO_PUT_ENABLED
    assert _payload_pause("keyword", "7201") == {"keywordId": "7201", "state": ESTADO_PUT_PAUSED}
    # La corrida real del pause fija el enum tocando UN lugar (write.py): el
    # payload del ledger la sigue SIN editar apply_cola.
    monkeypatch.setattr(apply, "ESTADO_PUT_PAUSED", "PAUSED")
    assert _payload_pause("keyword", "7201")["state"] == "PAUSED", "el payload vive de la constante"


@_skip_db
def test_reversa_pause_con_readback_que_sigue_paused_sella_divergencia():
    """QW6 (mismo bug que PR27-3 en el camino principal): un resume cuyo
    readback sigue PAUSED NO sella 'ok' — la reversa no surtio efecto.
    Regla 9: el codigo viejo sellaba resultado='ok' con cualquier estado
    legible (solo devolvia False) y el assert del ledger reventaba."""
    with _db_temporal("orbit_cola_rdiv") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 1})
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        vistos: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            # El PUT del resume sale 207... pero Amazon NO lo procesa: el LIST
            # fresco sigue devolviendo PAUSED (wire UPPER, probe 2.5).
            if request.url.host == "api.amazon.com":
                return httpx.Response(
                    200, json={"access_token": "fake-access-1", "expires_in": 3600}
                )
            vistos.append(request)
            if request.method == "POST" and request.url.path.endswith("/list"):
                return httpx.Response(
                    200, json={"keywords": [{"keywordId": "7201", "state": "PAUSED"}]}
                )
            if request.method == "PUT":
                return httpx.Response(207, json={"ack": json.loads(request.content)})
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        from app.apply_cola import fila_cola

        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
        ok = reversa_pause(conn, aplicador._cliente(), fila_cola(conn, q), tick=lambda: None)

        assert ok is False, "un readback PAUSED NO confirma la reversa"
        filas = conn.execute(
            "SELECT tipo, resultado FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchall()
        assert filas == [("reversa", "fallo:divergencia_readback")], (
            "el ledger JAMAS sella 'ok' sin el estado pedido (regla de PR27-3)"
        )
        estado = conn.execute(
            "SELECT status FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert estado == "PAUSED", "cache con LO LEIDO del readback (sellado 16)"


@_skip_db
def test_reversa_negative_con_207_rechazado_no_sella_ok():
    """CX3: el delete de la reversa responde 207 con la fila en error[]
    (rechazo por-item del shape real del probe 2.5): la reversa NO se
    confirma. Regla 9: el codigo viejo sellaba 'ok' con cualquier 2xx y
    devolvia True — el negativo quedaba vivo con el ledger mintiendo."""
    with _db_temporal("orbit_cola_rneg") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_negative": 1})
        dec = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        q = _encola_fila(
            conn,
            dec,
            ids["ag"],
            "negative",
            term="zapato blanco",
            payload=_payload_negative("7101", "7001", "zapato blanco"),
        )
        handler, _v = _handler_cortes(delete_rechazado=True)

        from app.apply_cola import fila_cola

        ok = reversa_negative(
            conn, _aplicador(conn, handler, ids["ciclo_ejec"])._cliente(), fila_cola(conn, q), "n-1"
        )

        assert ok is False, "un 207 con error[] NO es una reversa confirmada"
        filas = conn.execute(
            "SELECT tipo, resultado FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchall()
        assert filas == [
            (
                "reversa",
                "fallo:reversa_rechazada: {'negativeKeywords': "
                "{'error': [{'index': 0, 'code': 'NOT_FOUND', "
                "'negativeKeywordId': 'n-1'}], 'success': []}}",
            )
        ], "el ledger sella el rechazo CON el cuerpo del ack"


@_skip_db
def test_pause_cuya_entidad_vive_en_la_pagina_dos_del_list_aplica():
    """CX1/QW1 lado pausas: la keyword vive en la pagina 2 del LIST de estado
    (cuenta real: 1334 keywords). Regla 9: con la lectora de UNA pagina el
    re-check de estado vivo leia None y el corte moria discard
    'entidad_no_viva' (cero HTTP de mutacion) — estos asserts reventaban."""
    with _db_temporal("orbit_cola_lp2") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 1})
        d = ids["ahora"]
        # CORTES 03: 15 fechas x 7 = 105 clicks (>= 100) y cost 45 (>= 40):
        # la pause debe SOBREVIVIR la re-validacion.
        for fecha in _fechas(d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)):
            _metrica(conn, ids["run"], ids["kw"], fecha, clicks=7, cost=3, orders=0)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        vistos: list[httpx.Request] = []
        remoto = {"7201": "ENABLED"}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(
                    200, json={"access_token": "fake-access-1", "expires_in": 3600}
                )
            vistos.append(request)
            body = json.loads(request.content) if request.content else {}
            if request.method == "POST" and request.url.path.endswith("/list"):
                # Pagina 1: el senuelo 9999 + nextToken; pagina 2: la pedida.
                if body.get("nextToken"):
                    return httpx.Response(
                        200,
                        json={
                            "keywords": [{"keywordId": "7201", "state": remoto["7201"]}],
                            "totalResults": 2,
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "keywords": [{"keywordId": "9999", "state": "ENABLED"}],
                        "nextToken": "2",
                        "totalResults": 2,
                    },
                )
            if request.method == "PUT":
                obj = body["keywords"][0]
                ext = str(obj.get("keywordId"))
                remoto[ext] = {"PAUSED": "PAUSED", "ENABLED": "ENABLED"}.get(
                    obj["state"], obj["state"]
                )
                return httpx.Response(207, json={"ack": obj})
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.aplicadas == 1 and res.descartadas == [], (
            "la entidad de la pagina 2 NO es 'entidad_no_viva': el corte aplica"
        )
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied"
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert resultado == "ok"
