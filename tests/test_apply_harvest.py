"""Tests del harvest real (`app/apply_harvest`) — ORBIT 04, task 2.3.

DB temporal con el patron de test_apply_cola (0001+0002 aplicadas; corre
contra el Postgres real del tunel con ORBIT_TEST_DSN, skip fail-closed si
no) + HTTP 100% mock (`httpx.MockTransport`): CERO escrituras vivas a
Amazon, ni siquiera el token LWA. Los "secretos" son SIEMPRE falsos.

DoD de la tarea, un candado por test (regla 9 en cada uno):

1. harvest_job NACE 'pending' AL LIBERAR, antes del primer POST: con cap 0
   (fail-closed) la fila del job existe en pending y CERO HTTP salio.
2. Harvest VETADO jamas crea harvest_job (el camino al-decidir esta
   prohibido: la COLA manda, el job es la ejecucion — sellado 13).
3. Harvest = 1 unidad de quota / 2 HTTPs de mutacion (sellado 8): dos
   consumos revientan este test; el ledger declara la unidad en el primer
   intento y el segundo HTTP sin cobro.
4. Bid sugerido (sellado 14): endpoint falla (403 MockTransport) → None →
   default del goal clampeado; sugerido > ceiling → ceiling; < floor →
   floor; la INTENCION con el bid EFECTIVO queda en el ledger PRE-POST
   (crash despues del POST sin intencion pre-POST dejaria sin rastro).
5. Fallo definitivo en negative_created → reversa automatica del negativo
   (ledger tipo reversa EXENTO) + fase failed + ALERTA ESTRUCTURADA.
6. Orden de reversa completa: keyword PRIMERO, negativo despues (regla 9:
   una implementacion invertida revienta la captura del transport).
7. Matriz del brief §6 celda por celda (MockTransport sembrando lo que
   Amazon "tiene"): pending sin nada → reintenta; negative_created con
   negative YA → avanza sin duplicar; keyword YA en destino → done; señuelo
   en OTRO ad group NO es "ya aplicada"; applying huerfano negative →
   veredicto de matriz (confirma / señuelo failed / ausente reintenta).
8. Reconciliacion: job de fila VETOEDA se cierra (la cola manda), y el job
   que espera quota por primera vez la cobra al ejecutar (sin recobro para
   el applying que ya cobro).

RE-SELLADO contra el probe 2.5 (corrida autorizada del dueno 2026-08-26,
ledger apply_attempt ids 1-20, log out/smoke-apply-20260826.log): acks 207
con success/error anidados por recurso, contenedores del body, bid NUMERO
en el wire, matchType NEGATIVE_EXACT/EXACT y el "delete" v3 por POST
/delete con filtro que ARCHIVA (state=ARCHIVED en el list posterior). El
endpoint de bid sugerido sigue NO pineado (log out/regla8-bidrec.log):
cualquier error devuelve None (fail-open al default sellado).
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
from app.apply_cola import fila_cola, libera_vencidos
from app.apply_harvest import (
    MOTIVO_FALLO_KEYWORD,
    MOTIVO_FALLO_NEGATIVE,
    MOTIVO_KEYWORD_AUSENTE,
    AlertaHarvest,
    aplica_harvest,
    bid_efectivo,
    bid_sugerido,
    reconcilia_harvest,
    reversa_harvest_completo,
    reversa_harvest_parcial,
)

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"
FAKE_PROFILE_US = 404040

# Identidad del seed: origen grupo 7101 / campana 7001; destino sellado del
# goal: campana 8001 / ad group 8101; señuelo en OTRO ad group 9999.
ORIGEN_GRUPO = "7101"
ORIGEN_CAMPANA = "7001"
DESTINO_CAMPANA = "8001"
DESTINO_GRUPO = "8101"
GRUPO_SENUELO = "9999"
TERMINO = "buen termino"

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# ---------------------------------------------------------------------------
# Patron _db_temporal de test_apply_cola (COPIADO; aplica 0001 + 0002)
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
# Semilla: config con cap de harvest, ciclos, entidades y goal con destino
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


def _estado(conn, entidad: int) -> None:
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " synced_at) VALUES (%s, 1.00, 'USD', 'ENABLED', now())",
        (entidad,),
    )


def _semilla(conn, *, caps: dict | None = None) -> dict:
    """Config vigente con el cap de harvest pedido (default 2), el ciclo que
    DECIDIO (hace 3d), el ciclo EJECUTOR live, campaign->ad_group->kw con
    state, campaña con state (CAMPANA ACTIVA 01: la cola exige campaña y
    grupo ENABLED al liberar) y
    el goal de plataforma con destino de harvest sellado (floor 0.10 /
    ceiling 2.50 USD, default 1.00 — el default EFECTIVO viaja congelado en
    decision.new_value). Desde la review adversaria (ADV-05) siembra ADEMAS
    observaciones del termino que CALIFICAN para harvest en la ventana fresca
    de liberacion: la re-validacion PRE-claim re-evalua la regla con esta
    evidencia (ver _termino_calificado)."""
    settings = (
        dict(caps)
        if caps is not None
        else {"ads_apply_cap_amazon_us_harvest": 2, "ads_apply_cap_amazon_us_negative": 5}
    )
    config_id = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-harvest', %s) RETURNING id",
        (Json(settings),),
    ).fetchone()[0]
    ciclo_dec = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    ciclo_ejec = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    camp = _entidad(conn, "campaign", ORIGEN_CAMPANA)
    ag = _entidad(conn, "ad_group", ORIGEN_GRUPO, parent=camp)
    kw = _entidad(conn, "keyword", "7201", parent=ag)
    for entidad in (camp, ag, kw):
        _estado(conn, entidad)
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode) VALUES ('platform', 'amazon_us', 55, 0.10,"
        " 2.50, 'USD', %s, %s, 1.00, true, 'live')",
        (DESTINO_CAMPANA, DESTINO_GRUPO),
    )
    run_id = _termino_calificado(conn, ag)
    return {
        "config": config_id,
        "ciclo_dec": ciclo_dec,
        "ciclo_ejec": ciclo_ejec,
        "camp": camp,
        "ag": ag,
        "kw": kw,
        "run": run_id,
    }


def _termino_calificado(conn, grupo: int, *, term: str = TERMINO, observed=None) -> int:
    """Siembra observaciones del termino que CALIFICAN para harvest en la
    ventana fresca de LIBERACION (la re-validacion de ADV-05 re-evalua la
    regla contra ellas): 12 fechas dentro de la ventana, orders 2 (>=
    HARVEST_ORDERS_MIN), ACoS 10% (muy bajo el tope 35), USD. Devuelve el
    ingest_run. `observed` permite sembrar revisiones bitemporales."""
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('test') RETURNING id"
    ).fetchone()[0]
    ahora = dt.datetime.now(dt.UTC)
    base = observed or (ahora - dt.timedelta(days=5))
    # La venta que HABILITA el harvest (orders >= 2), UNICA fila de su fecha
    # (sin empate bitemporal: el colapso DISTINCT ON es determinista).
    _termino_obs(
        conn,
        run_id,
        grupo,
        term,
        ahora.date() - dt.timedelta(days=40),
        clicks=3,
        cost="0.50",
        ad_revenue="5.00",
        orders=2,
        observed=base,
    )
    for fecha in _fechas(
        ahora.date() - dt.timedelta(days=39), ahora.date() - dt.timedelta(days=27)
    ):
        _termino_obs(
            conn,
            run_id,
            grupo,
            term,
            fecha,
            clicks=3,
            cost="0.50",
            ad_revenue="5.00",
            orders=0,
            observed=base,
        )
    return run_id


def _fechas(desde: dt.date, hasta: dt.date):
    dia = desde
    while dia <= hasta:
        yield dia
        dia += dt.timedelta(days=1)


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


def _decision_harvest(conn, ciclo: int, config_id: int, entidad: int, term: str = TERMINO) -> int:
    """Decision kind='harvest' con el default congelado (new_value 1.00 USD —
    sellado 14) y la madurez del esquema (window_end <= decided-10d)."""
    dec = dt.datetime.now(dt.UTC) - dt.timedelta(days=3)
    inputs = {
        "motor": "hygiene",
        "platform": "amazon_us",
        "modo": "live",
        "motivo": "harvest_umbral",
    }
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'harvest', %s, %s, %s - interval '1 day', %s - 60, %s - 30,"
        " %s, 1.00, 'USD', %s) RETURNING id",
        (ciclo, entidad, dec, config_id, dec, dec.date(), dec.date(), term, Json(inputs)),
    ).fetchone()[0]


def _decision_negative(conn, ciclo: int, config_id: int, entidad: int, term: str = TERMINO) -> int:
    """Decision kind='negative' (new_value NULL: un negativo no mueve dinero)
    — decision es append-only, el corte negative se siembra como fila nueva."""
    dec = dt.datetime.now(dt.UTC) - dt.timedelta(days=3)
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, inputs) VALUES (%s, %s,"
        " 'negative', %s, %s, %s - interval '1 day', %s - 60, %s - 30, %s, '{}'::jsonb)"
        " RETURNING id",
        (ciclo, entidad, dec, config_id, dec, dec.date(), dec.date(), term),
    ).fetchone()[0]


def _encola_fila(
    conn,
    decision: int,
    entidad: int,
    *,
    kind: str = "harvest",
    term=None,
    modo="live",
    vence=None,
    encolado=None,
) -> int:
    ahora = dt.datetime.now(dt.UTC)
    return conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term, decision_id,"
        " modo, estado, vence_el, encolado_at, request_payload) VALUES ('amazon_us', %s,"
        " %s, %s, %s, %s, 'pending_veto', %s, %s, '{}'::jsonb) RETURNING id",
        (
            entidad,
            kind,
            term,
            decision,
            modo,
            vence or (ahora - dt.timedelta(hours=1)),
            encolado or (ahora - dt.timedelta(days=3)),
        ),
    ).fetchone()[0]


def _libera_fila(conn, q_id: int) -> None:
    """Pone una fila en released (el hook reclama applying el mismo)."""
    conn.execute(
        "UPDATE apply_queue SET estado = 'released', released_at = now() WHERE id = %s", (q_id,)
    )


def _claim_fila(conn, q_id: int) -> None:
    conn.execute(
        "UPDATE apply_queue SET estado = 'applying', applying_at = now() WHERE id = %s", (q_id,)
    )


def _job_en(conn, decision: int, entidad: int, fase: str, *, external_ids=None) -> int:
    """Siembra un harvest_job en la fase pedida (nace pending por trigger y
    avanza por UPDATE — la progresion sellada de 0002)."""
    jid = conn.execute(
        "INSERT INTO harvest_job (decision_id, search_term, platform, ad_entity_id, fase)"
        " VALUES (%s, %s, 'amazon_us', %s, 'pending') RETURNING id",
        (decision, TERMINO, entidad),
    ).fetchone()[0]
    if fase in ("negative_created", "exact_created"):
        conn.execute(
            "UPDATE harvest_job SET fase = 'negative_created', updated_at = now() WHERE id = %s",
            (jid,),
        )
    if fase == "exact_created":
        conn.execute(
            "UPDATE harvest_job SET fase = 'exact_created', external_ids = %s,"
            " updated_at = now() WHERE id = %s",
            (Json(external_ids if external_ids is not None else {}), jid),
        )
    if fase == "failed":
        # Cierre directo (legal desde cualquier fase en vuelo por 0002).
        conn.execute(
            "UPDATE harvest_job SET fase = 'failed', updated_at = now() WHERE id = %s", (jid,)
        )
    return jid


def _fila_ledger_abierta(conn, decision: int) -> None:
    """Ledger sin sello: la fila de cola applying huerfana del negative."""
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
        " VALUES (%s, 1, 'normal', '{}'::jsonb, true)",
        (decision,),
    )


# ---------------------------------------------------------------------------
# Mock de la API de Ads con ESTADO (lo que Amazon "tiene" se siembra)
# ---------------------------------------------------------------------------


def _handler_harvest(
    *,
    negatives: list[dict] | None = None,
    keywords: list[dict] | None = None,
    bidrec_status: int = 403,
    bidrec_body: dict | None = None,
    fallo_keyword_status: int = 0,
    tumbar_keyword: bool = False,
    fallo_delete_keyword: int = 0,
    ack_negative_sin_id: bool = False,
    ack_keyword_sin_id: bool = False,
    ack_negative_con_error: bool = False,
    delete_keyword_rechazado: bool = False,
):
    """Handler MockTransport con ALMACEN y el shape REAL del probe 2.5
    (2026-08-26, ledger ids 1-20, log out/smoke-apply-20260826.log): el POST
    viaja como unica entrada del contenedor del recurso, el ack es 207 con
    success/error anidados (apply_attempt 13/16) y el "delete" v3 es POST
    /delete con filtro de ids que ARCHIVA (el item SIGUE en el list con
    state=ARCHIVED; apply_attempt 14/17) — la identidad viva lo ignora.
    `bidrec_status` controla el endpoint de bid sugerido (403 por defecto:
    PENDIENTE-DE-REGLA-8). `fallo_keyword_status` responde ese status en el
    POST de keyword (fallo DEFINITIVO >=400); `tumbar_keyword` rompe la red
    (crash ambiguo). Variante cross-review: `fallo_delete_keyword` responde
    ese status en el delete de keyword; `ack_*_sin_id` sirven el 207 SIN id
    en success (GK2: fases con ids reales). Cuenta TODOS los requests de la
    API (LWA fuera)."""
    neg_store = list(negatives or [])
    kw_store = list(keywords or [])
    vistos: list[httpx.Request] = []
    seq = iter(range(100, 999))

    def _archiva(store: list[dict], kid: str) -> None:
        # El "delete" v3 ARCHIVA (probe 2.5: 207 success + state=ARCHIVED en
        # el list posterior): el item SIGUE en el almacen del list.
        for item in store:
            if str(item.get("keywordId")) == str(kid):
                item["state"] = "ARCHIVED"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access-1", "expires_in": 3600})
        vistos.append(request)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/sp/negativeKeywords/list":
            return httpx.Response(
                200, json={"negativeKeywords": neg_store, "totalResults": len(neg_store)}
            )
        if path == "/sp/keywords/list":
            return httpx.Response(200, json={"keywords": kw_store, "totalResults": len(kw_store)})
        if "bidRecommendations" in path:
            if bidrec_status >= 400:
                return httpx.Response(bidrec_status, json={"message": "sigv4 requerido"})
            return httpx.Response(200, json=bidrec_body or {})
        if path == "/sp/negativeKeywords" and request.method == "POST":
            obj = body["negativeKeywords"][0]  # contenedor del recurso (probe 2.5)
            kid = f"n-{next(seq)}"
            if ack_negative_con_error:
                # CX2: 207 con la fila en error[] — rechazo por-item (shape del
                # probe 2.5): Amazon NO creo nada, el almacen no cambia.
                return httpx.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "error": [{"index": 0, "code": "DUPLICATE", "negativeKeywordId": kid}],
                            "success": [],
                        }
                    },
                )
            neg_store.append(
                {
                    "adGroupId": obj["adGroupId"],
                    "campaignId": obj["campaignId"],
                    "keywordId": kid,
                    "keywordText": obj["keywordText"],
                    "matchType": obj["matchType"],  # wire: NEGATIVE_EXACT
                    "state": "ENABLED",  # wire UPPER (apply_attempt 19-20)
                }
            )
            if ack_negative_sin_id:
                return httpx.Response(207, json={"negativeKeywords": {"error": [], "success": []}})
            return httpx.Response(
                207,
                json={
                    "negativeKeywords": {
                        "error": [],
                        "success": [{"index": 0, "negativeKeywordId": kid}],
                    }
                },
            )
        if path == "/sp/negativeKeywords/delete" and request.method == "POST":
            # Shape real (probe 2.5, apply_attempt 14): filtro de ids; archiva.
            kid = body["negativeKeywordIdFilter"]["include"][0]
            _archiva(neg_store, kid)
            return httpx.Response(
                207,
                json={
                    "negativeKeywords": {
                        "error": [],
                        "success": [{"index": 0, "negativeKeywordId": kid}],
                    }
                },
            )
        if path == "/sp/keywords" and request.method == "POST":
            if tumbar_keyword:
                raise httpx.ConnectError("crash simulado entre ledger y sello")
            if fallo_keyword_status:
                return httpx.Response(fallo_keyword_status, json={"code": "400"})
            obj = body["keywords"][0]  # contenedor del recurso (probe 2.5)
            kid = f"k-{next(seq)}"
            kw_store.append(
                {
                    "adGroupId": obj["adGroupId"],
                    "campaignId": obj["campaignId"],
                    "keywordId": kid,
                    "keywordText": obj["keywordText"],
                    "matchType": obj["matchType"],  # wire: EXACT
                    "state": "ENABLED",
                    "bid": obj["bid"],  # wire: NUMERO (apply_attempt 3)
                }
            )
            if ack_keyword_sin_id:
                return httpx.Response(207, json={"keywords": {"error": [], "success": []}})
            return httpx.Response(
                207,
                json={"keywords": {"error": [], "success": [{"index": 0, "keywordId": kid}]}},
            )
        if path == "/sp/keywords/delete" and request.method == "POST":
            if fallo_delete_keyword:
                return httpx.Response(fallo_delete_keyword, json={"code": "400"})
            kid = body["keywordIdFilter"]["include"][0]
            if delete_keyword_rechazado:
                # CX3: el delete responde 207 pero la fila viaja en error[]
                # (rechazo por-item: NO fue aceptado aunque el status sea 2xx).
                return httpx.Response(
                    207,
                    json={
                        "keywords": {
                            "error": [{"index": 0, "code": "NOT_FOUND", "keywordId": kid}],
                            "success": [],
                        }
                    },
                )
            _archiva(kw_store, kid)
            return httpx.Response(
                207,
                json={"keywords": {"error": [], "success": [{"index": 0, "keywordId": kid}]}},
            )
        raise AssertionError(f"request inesperado: {request.method} {path}")

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
        owner="test:harvest",
        job_key="ads_optimizer:amazon_us",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _mutaciones(vistos: list[httpx.Request]) -> list[httpx.Request]:
    """Solo los HTTP de MUTACION (los /list y el bid sugerido son lecturas)."""
    return [
        r
        for r in vistos
        if r.method in ("POST", "PUT", "DELETE") and not r.url.path.endswith("/list")
    ]


def _reconcilia(conn, handler, ciclo_ejec: int):
    """reconcilia_harvest con el aplicador del test (linea corta por repeticion)."""
    return reconcilia_harvest(conn, _aplicador(conn, handler, ciclo_ejec), "amazon_us")


def _payload_keyword(bid: str) -> dict:
    """Espejo del wire REAL (probe 2.5, apply_attempt 16): matchType EXACT +
    state ENABLED; el bid del LEDGER viaja quantizado string (_bid_payload,
    una sola fuente) — el wire lo serializa NUMERO (_bid_wire)."""
    return {
        "adGroupId": DESTINO_GRUPO,
        "campaignId": DESTINO_CAMPANA,
        "keywordText": TERMINO,
        "matchType": "EXACT",
        "state": "ENABLED",
        "bid": bid,
    }


def _payload_negative() -> dict:
    """Espejo del wire REAL (probe 2.5, apply_attempt 13): el matchType de
    negatives es el enum NEGATIVE_* y state es OBLIGATORIO (UPPER)."""
    return {
        "adGroupId": ORIGEN_GRUPO,
        "campaignId": ORIGEN_CAMPANA,
        "keywordText": TERMINO,
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }


# ---------------------------------------------------------------------------
# 1. El job nace 'pending' AL LIBERAR, antes del primer POST (sellado 13)
# ---------------------------------------------------------------------------


@_skip_db
def test_job_nace_pending_al_liberar_antes_del_primer_post():
    """Cap de harvest en 0 (fail-closed): el job NACE pending igual (primer
    paso del apply, antes de cualquier HTTP) y la fila queda released
    esperando quota. Regla 9: sin el INSERT pre-HTTP la fila del job no
    existiria y este test reventaria."""
    with _db_temporal("orbit_har_nace") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_harvest": 0})
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        handler, vistos = _handler_harvest()

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.sin_quota == 1 and res.liberadas == 1
        job = conn.execute("SELECT fase, decision_id FROM harvest_job").fetchall()
        assert job == [("pending", dec)], "el job nace pending ANTES del primer POST"
        estado = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert estado == "released", "sin quota la fila queda released (FIFO, vetable)"
        assert vistos == [], "cero HTTP: el cap 0 no quema ni token LWA"


# ---------------------------------------------------------------------------
# 2. Harvest VETADO jamas crea harvest_job (regla 9)
# ---------------------------------------------------------------------------


@_skip_db
def test_harvest_vetado_jamas_crea_harvest_job():
    """El dueno veta dentro de la ventana: la fila muere vetoed y NUNCA nace
    el job. Regla 9: una implementacion que creara el job al DECIDIR (el
    camino prohibido) dejaria la fila aqui y este test reventaria."""
    with _db_temporal("orbit_har_veto") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        _encola_fila(
            conn,
            dec,
            ids["ag"],
            term=TERMINO,
            vence=dt.datetime.now(dt.UTC) + dt.timedelta(days=20),
        )
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(), vetoed_by ="
                " 'dueno', vence_el = now() + interval '30 days' WHERE decision_id = %s",
                (dec,),
            )
        finally:
            conn.execute("RESET ROLE")
        handler, vistos = _handler_harvest()

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.liberadas == 0
        total = conn.execute("SELECT count(*) FROM harvest_job").fetchone()[0]
        assert total == 0, "harvest vetado JAMAS crea harvest_job (la COLA manda)"
        assert vistos == []


# ---------------------------------------------------------------------------
# 3. Harvest = 1 quota / 2 HTTPs de mutacion; ledger y sello completos
# ---------------------------------------------------------------------------


@_skip_db
def test_harvest_completo_1_quota_2_https():
    with _db_temporal("orbit_har_full") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        handler, vistos = _handler_harvest()

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.aplicadas == 1 and res.fallidas == 0 and res.sin_quota == 0
        muts = _mutaciones(vistos)
        assert [f"{r.method} {r.url.path}" for r in muts] == [
            "POST /sp/negativeKeywords",
            "POST /sp/keywords",
        ], "harvest = exactamente 2 HTTPs de mutacion (negativo + keyword)"
        quota = conn.execute(
            "SELECT used, cap FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:harvest",),
        ).fetchone()
        assert quota == (1, 2), "2 HTTPs, UNA unidad de quota (sellado 8; dos consumos revientan)"
        ledger = conn.execute(
            "SELECT seq, tipo, request_payload, quota_cobrada, resultado FROM apply_attempt"
            " WHERE decision_id = %s ORDER BY seq",
            (dec,),
        ).fetchall()
        assert ledger == [
            (1, "normal", _payload_negative(), True, "ok"),
            (2, "normal", _payload_keyword("1.00"), False, "ok"),
        ], "la unidad se declara en el PRIMER intento; el 2do HTTP ya pago"
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("done",)
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied"
        resumen = conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen == (True, ids["ciclo_ejec"]), "sello al confirmar con el ciclo EJECUTOR"
        # El POST de la keyword viaja al DESTINO sellado del goal: contenedor
        # del recurso + bid NUMERO (probe 2.5, apply_attempt 16); el LEDGER
        # congela el mismo objeto con el bid quantizado string (_payload_keyword).
        posts_kw = [r for r in muts if r.url.path == "/sp/keywords"]
        assert json.loads(posts_kw[0].content) == {
            "keywords": [{**_payload_keyword("1.00"), "bid": 1.0}]
        }


# ---------------------------------------------------------------------------
# 4. Bid sugerido: fail-open al default + clampeo sellado + intencion PRE-POST
# ---------------------------------------------------------------------------


@_skip_db
def test_bid_sugerido_403_deja_el_default_del_goal_clampeado():
    """El endpoint de bid sugerido responde 403 (PENDIENTE-DE-REGLA-8, log
    out/regla8-bidrec.log): fail-open SILENCIOSO → default congelado en
    decision.new_value (1.00, dentro de [0.10, 2.50]). Regla 9: una
    implementacion que reventara o inventara 0 dejaria otro bid."""
    with _db_temporal("orbit_har_b403") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        handler, vistos = _handler_harvest(bidrec_status=403)

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.aplicadas == 1
        bidrec = [r for r in vistos if "bidRecommendations" in r.url.path]
        assert len(bidrec) == 1, "la sugerencia se consulta al aplicar (sellado 14)"
        payload = conn.execute(
            "SELECT request_payload FROM apply_attempt WHERE decision_id = %s AND seq = 2",
            (dec,),
        ).fetchone()[0]
        assert payload["bid"] == "1.00", "sin sugerencia → default del goal (regla 3)"


def test_clampeo_sugerido_sobre_ceiling_y_bajo_floor():
    """Clampeo sellado [floor, ceiling] del goal. Regla 9: sin el clamp, el
    bid_efectivo dejaria pasar 15.00 y 0.01."""
    assert bid_efectivo(Decimal("15"), Decimal("1"), Decimal("0.10"), Decimal("2.50")) == (
        Decimal("2.50")
    ), "sugerido > ceiling → ceiling"
    assert bid_efectivo(Decimal("0.01"), Decimal("1"), Decimal("0.10"), Decimal("2.50")) == (
        Decimal("0.10")
    ), "sugerido < floor → floor"
    assert bid_efectivo(None, Decimal("1"), Decimal("0.10"), Decimal("2.50")) == Decimal("1")
    with pytest.raises(ValueError, match="regla 3"):
        bid_efectivo(None, None, Decimal("0.10"), Decimal("2.50"))


def test_bid_sugerido_falla_silencioso_y_parsea():
    """CUALQUIER error (403/404/5xx/red) → None silencioso (es una LECTURA:
    sin fila de ledger, logging debug). 200 con suggestedBid → Decimal."""
    cred = AdsCredentials(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        refresh_token=FAKE_REFRESH_TOKEN,
    )

    def _cliente(handler):
        from app.ads.write import AdsWriteClient

        return AdsWriteClient(
            cred,
            platform="amazon_us",
            profile_id=FAKE_PROFILE_US,
            modo_confirmado="live",
            transport=httpx.MockTransport(handler),
            sleep=lambda seconds: None,
        )

    for status in (403, 404, 500):
        handler, _v = _handler_harvest(bidrec_status=status)
        assert bid_sugerido(_cliente(handler), "326554650754331") is None, f"status {status}"

    handler, _v = _handler_harvest(bidrec_status=200, bidrec_body={"suggestedBid": "0.55"})
    assert bid_sugerido(_cliente(handler), "326554650754331") == Decimal("0.55")
    handler, _v = _handler_harvest(bidrec_status=200, bidrec_body={"nada": "aqui"})
    assert bid_sugerido(_cliente(handler), "326554650754331") is None, "body sin sugerencia"


@_skip_db
def test_intencion_con_bid_efectivo_en_ledger_pre_post_sobrevive_crash(monkeypatch):
    """Sugerido 15 → clampeado a 2.50; el POST de la keyword MUERE en la red
    (ambiguo): la INTENCION ya es durable en el ledger (finished_at NULL),
    el job quedo en negative_created y la cola en applying — la fila ES el
    rastro. Regla 9: escribir el ledger DESPUES del POST dejaria todo sin
    rastro y este test reventaria."""
    from app import apply_harvest

    with _db_temporal("orbit_har_crash") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        handler, vistos = _handler_harvest(tumbar_keyword=True)
        monkeypatch.setattr(
            apply_harvest, "bid_sugerido", lambda cliente, keyword_id=None: Decimal("15")
        )

        from app.ads.client import AdsApiError

        with pytest.raises(AdsApiError):
            libera_vencidos(
                conn,
                "amazon_us",
                ahora=dt.datetime.now(dt.UTC),
                aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
            )

        intento = conn.execute(
            "SELECT request_payload, finished_at FROM apply_attempt WHERE decision_id = %s"
            " AND seq = 2",
            (dec,),
        ).fetchone()
        assert intento is not None, "la intencion PRE-POST sobrevive al crash"
        assert intento[0] == _payload_keyword("2.50"), "bid EFECTIVO clampeado (15 → 2.50)"
        assert intento[1] is None, "sin sello: el ambiguo no se sella (sellado 8)"
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("negative_created",)
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applying", "applying huerfano: la reconciliacion decide (matriz §6)"
        # El POST de la keyword FUE emitido (murio en red sin respuesta): el
        # transport lo registra; lo que queda del intento es la fila del ledger.
        assert [f"{r.method} {r.url.path}" for r in _mutaciones(vistos)] == [
            "POST /sp/negativeKeywords",
            "POST /sp/keywords",
        ], "el negativo salio; la keyword se emitio y murio en red"


# ---------------------------------------------------------------------------
# 5. Fallo definitivo en negative_created → reversa automatica + failed + alerta
# ---------------------------------------------------------------------------


@_skip_db
def test_fallo_definitivo_negative_created_reversa_automatica_failed_alerta():
    """El POST de la keyword recibe 400 (rechazo DEFINITIVO): reversa
    automatica del negativo creado, job failed, cola failed y ALERTA
    ESTRUCTURADA (la senal que 3.3 consume). Regla 9: sin la reversa el
    termino quedaria bloqueado por un negativo que ya no sirve."""
    with _db_temporal("orbit_har_fail") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        handler, vistos = _handler_harvest(fallo_keyword_status=400)

        resultado = aplica_harvest(
            conn,
            _aplicador(conn, handler, ids["ciclo_ejec"]),
            fila_cola(conn, q),
            platform="amazon_us",
        )

        assert resultado.estado == "failed"
        assert isinstance(resultado.alerta, AlertaHarvest)
        assert resultado.alerta.motivo == MOTIVO_FALLO_KEYWORD
        assert resultado.alerta.decision_id == dec and resultado.alerta.search_term == TERMINO
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [json.loads(d.content) for d in deletes] == [
            {"negativeKeywordIdFilter": {"include": ["n-100"]}}
        ], "reversa automatica: POST /delete con filtro (probe 2.5, apply_attempt 14)"
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("failed",)
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "failed"
        filas = conn.execute(
            "SELECT tipo, quota_cobrada, resultado FROM apply_attempt WHERE decision_id = %s"
            " ORDER BY seq",
            (dec,),
        ).fetchall()
        assert [f[0] for f in filas] == ["normal", "normal", "reversa"], (
            "el ledger conserva el intento fallido Y la reversa"
        )
        assert [f[1] for f in filas] == [True, False, False], "reversa EXENTA (sellado 12)"
        assert filas[0][2] == "ok" and filas[1][2].startswith("fallo http"), (
            "el 400 definitivo se sella con la razon"
        )


# ---------------------------------------------------------------------------
# 6. Orden de reversa completa: keyword PRIMERO, negativo despues (regla 9)
# ---------------------------------------------------------------------------


@_skip_db
def test_orden_reversa_completa_keyword_primero_negativo_despues():
    """§7: harvest completo → delete keyword PRIMERO, delete del negativo
    DESPUES. Regla 9: el transport captura el ORDEN — una implementacion
    invertida revienta este test. Ledger tipo reversa, exentas ambas."""
    with _db_temporal("orbit_har_rev") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        handler, vistos = _handler_harvest()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        ok = reversa_harvest_completo(
            conn, aplicador._cliente(), dec, negative_id="n-1", keyword_id="k-1"
        )

        assert ok is True
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [r.url.path for r in deletes] == [
            "/sp/keywords/delete",
            "/sp/negativeKeywords/delete",
        ], "keyword PRIMERO, negativo despues (sellado 12; POST /delete v3)"
        filas = conn.execute(
            "SELECT tipo, quota_cobrada, resultado FROM apply_attempt ORDER BY seq"
        ).fetchall()
        assert filas == [("reversa", False, "ok"), ("reversa", False, "ok")]
        quota = conn.execute("SELECT count(*) FROM apply_quota_state").fetchone()[0]
        assert quota == 0, "reversas EXENTAS: no nace fila de quota"


@_skip_db
def test_reversa_parcial_borra_el_negativo_exenta():
    with _db_temporal("orbit_har_revp") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        handler, vistos = _handler_harvest()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        ok = reversa_harvest_parcial(conn, aplicador._cliente(), dec, negative_id="n-7")

        assert ok is True
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [json.loads(d.content) for d in deletes] == [
            {"negativeKeywordIdFilter": {"include": ["n-7"]}}
        ], "delete v3: POST /delete con filtro (probe 2.5)"
        filas = conn.execute(
            "SELECT tipo, quota_cobrada FROM apply_attempt ORDER BY seq"
        ).fetchall()
        assert filas == [("reversa", False)]


# ---------------------------------------------------------------------------
# 7. Matriz de reconciliacion §6 celda por celda (Amazon VIVO por lista)
# ---------------------------------------------------------------------------


@_skip_db
def test_matriz_job_pending_sin_nada_aplicado_reintenta_el_post():
    """Job pending + Amazon NO tiene el negativo → REINTENTA el POST (seguro:
    la fuente confirma que no esta) y el job avanza a done. La fila applying
    huerfana CONSERVA su cobro: no vuelve a consumir quota. Regla 9: sin el
    job pre-POST este caso dejaria un duplicado o un eterno."""
    with _db_temporal("orbit_har_m1") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(conn, dec, ids["ag"], "pending")
        handler, vistos = _handler_harvest()

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_done == 1 and resumen.jobs_failed == 0
        muts = _mutaciones(vistos)
        assert [f"{r.method} {r.url.path}" for r in muts] == [
            "POST /sp/negativeKeywords",
            "POST /sp/keywords",
        ], "reintenta el POST del negativo y completa la cadena"
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("done",)
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied"
        quota = conn.execute("SELECT count(*) FROM apply_quota_state").fetchone()[0]
        assert quota == 0, "el applying huerfano conserva su cobro: NO recobra"


@_skip_db
def test_matriz_negative_created_con_negative_ya_en_amazon_avanza_sin_duplicar():
    """Job negative_created + el negativo YA esta en Amazon (identidad
    completa origen): avanza SIN re-postear el negativo (el duplicado es el
    riesgo que la fase protege) y crea la keyword. Regla 9: una
    implementacion que re-postearia dejaria 2 POSTs de negativo."""
    with _db_temporal("orbit_har_m2") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(conn, dec, ids["ag"], "negative_created")
        handler, vistos = _handler_harvest(
            negatives=[
                {
                    "adGroupId": ORIGEN_GRUPO,
                    "campaignId": ORIGEN_CAMPANA,
                    "keywordId": "n-9",
                    "keywordText": TERMINO,
                    "matchType": "EXACT",
                    "state": "enabled",
                }
            ]
        )

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_done == 1
        posts_neg = [
            r
            for r in _mutaciones(vistos)
            if r.method == "POST" and r.url.path == "/sp/negativeKeywords"
        ]
        assert posts_neg == [], "el negativo YA esta: NO se duplica"
        posts_kw = [r for r in _mutaciones(vistos) if r.url.path == "/sp/keywords"]
        assert len(posts_kw) == 1, "la keyword destino SI se crea"
        ext = conn.execute(
            "SELECT external_ids FROM harvest_job WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert ext["negative_id"] == "n-9", "el id del negativo se toma de la EVIDENCIA viva"


@_skip_db
def test_matriz_negative_created_keyword_ya_en_destino_done_sin_mutaciones():
    """Negativo y keyword YA aplicados (crash post-POST pre-sello): done SIN
    ninguna mutacion nueva y sello del resumen. Regla 9: una implementacion
    que re-postearia la keyword duplicaria en Amazon."""
    with _db_temporal("orbit_har_m3") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(conn, dec, ids["ag"], "negative_created")
        handler, vistos = _handler_harvest(
            negatives=[
                {
                    "adGroupId": ORIGEN_GRUPO,
                    "campaignId": ORIGEN_CAMPANA,
                    "keywordId": "n-9",
                    "keywordText": TERMINO,
                    "matchType": "EXACT",
                    "state": "enabled",
                }
            ],
            keywords=[
                {
                    "adGroupId": DESTINO_GRUPO,
                    "campaignId": DESTINO_CAMPANA,
                    "keywordId": "k-7",
                    "keywordText": TERMINO,
                    "matchType": "EXACT",
                    "state": "enabled",
                    "bid": "1.00",
                }
            ],
        )

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_done == 1
        assert _mutaciones(vistos) == [], "ya estaba aplicada: cero mutaciones"
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("done",)
        resumen_dec = conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen_dec == (True, ids["ciclo_ejec"]), "confirmacion tardia: sello igual"


@_skip_db
def test_matriz_exact_created_keyword_ya_en_destino_done():
    with _db_temporal("orbit_har_m4") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(
            conn,
            dec,
            ids["ag"],
            "exact_created",
            external_ids={"negative_id": "n-9", "keyword_id": "k-7"},
        )
        handler, vistos = _handler_harvest(
            keywords=[
                {
                    "adGroupId": DESTINO_GRUPO,
                    "campaignId": DESTINO_CAMPANA,
                    "keywordId": "k-7",
                    "keywordText": TERMINO,
                    "matchType": "exact",
                    "state": "enabled",
                }
            ]
        )

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_done == 1 and resumen.jobs_failed == 0
        assert _mutaciones(vistos) == []
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied"


@_skip_db
def test_matriz_senuelo_en_otro_ad_group_no_es_ya_aplicada():
    """SENUelo: la keyword con el MISMO texto vive en OTRO ad group (9999):
    identidad INCOMPLETA → NO es "ya aplicada" → failed + reversa (keyword
    primero) + alerta. Regla 9: comparar solo el texto daria falso positivo
    y este test reventaria."""
    with _db_temporal("orbit_har_m5") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(
            conn,
            dec,
            ids["ag"],
            "exact_created",
            external_ids={"negative_id": "n-9", "keyword_id": "k-7"},
        )
        handler, vistos = _handler_harvest(
            keywords=[
                {
                    "adGroupId": GRUPO_SENUELO,
                    "campaignId": "9000",
                    "keywordId": "k-otro",
                    "keywordText": TERMINO,
                    "matchType": "exact",
                    "state": "enabled",
                }
            ]
        )

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_done == 0 and resumen.jobs_failed == 1
        assert resumen.alertas and resumen.alertas[0].motivo == MOTIVO_KEYWORD_AUSENTE
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("failed",)
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [r.url.path for r in deletes] == [
            "/sp/keywords/delete",
            "/sp/negativeKeywords/delete",
        ], "reversa completa: keyword PRIMERO, negativo despues"
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "failed"


@_skip_db
def test_matriz_applying_huerfano_negative_confirmado_por_identidad():
    """Cola applying huerfana kind negative: el negativo YA esta en Amazon
    con identidad completa → confirmar (sellar ledger pendiente, resumen,
    applied). Regla 9: el señuelo NO confirmaria (test siguiente)."""
    with _db_temporal("orbit_har_m6") as conn:
        ids = _semilla(conn)
        dec = _decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], kind="negative", term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _fila_ledger_abierta(conn, dec)
        handler, vistos = _handler_harvest(
            negatives=[
                {
                    "adGroupId": ORIGEN_GRUPO,
                    "campaignId": ORIGEN_CAMPANA,
                    "keywordId": "n-3",
                    "keywordText": TERMINO,
                    "matchType": "exact",
                    "state": "enabled",
                }
            ]
        )

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.negativas_confirmadas == 1
        assert _mutaciones(vistos) == [], "confirmar no muta nada"
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied"
        sello = conn.execute(
            "SELECT resultado, finished_at IS NOT NULL FROM apply_attempt WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert sello[1] is True, "el ledger huerfano se sella una vez"
        resumen_dec = conn.execute(
            "SELECT verify_ok FROM decision_application WHERE decision_id = %s", (dec,)
        ).fetchone()
        assert resumen_dec == (True,)


@_skip_db
def test_matriz_applying_huerfano_negative_senuelo_failed():
    """El negativo del termino existe SOLO en OTRO ad group (señuelo): NO
    confirma → failed. Regla 9: identidad sin adGroup daria falso aplicado."""
    with _db_temporal("orbit_har_m7") as conn:
        ids = _semilla(conn)
        dec = _decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], kind="negative", term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _fila_ledger_abierta(conn, dec)
        handler, vistos = _handler_harvest(
            negatives=[
                {
                    "adGroupId": GRUPO_SENUELO,
                    "campaignId": "9000",
                    "keywordId": "n-otro",
                    "keywordText": TERMINO,
                    "matchType": "exact",
                    "state": "enabled",
                }
            ]
        )

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.negativas_confirmadas == 0 and resumen.negativas_fallidas == 1
        assert _mutaciones(vistos) == []
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "failed"


@_skip_db
def test_matriz_applying_huerfano_negative_ausente_reintenta_bajo_tope():
    """El negativo NO esta en Amazon (ni en otro ad group): reintento del
    POST (nueva fila del ledger, sin recobro) → applied. Regla 9: el
    veredicto de matriz para el ausente NO es failed directo."""
    with _db_temporal("orbit_har_m8") as conn:
        ids = _semilla(conn)
        dec = _decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], kind="negative", term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _fila_ledger_abierta(conn, dec)
        handler, vistos = _handler_harvest()

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.negativas_confirmadas == 1
        posts = [r for r in _mutaciones(vistos) if r.method == "POST"]
        assert len(posts) == 1, "reintenta el POST del negativo"
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied"
        filas = conn.execute(
            "SELECT seq, tipo, quota_cobrada, resultado FROM apply_attempt WHERE decision_id = %s"
            " ORDER BY seq",
            (dec,),
        ).fetchall()
        assert filas == [
            (1, "normal", True, "ok:reconciliado"),
            (2, "normal", False, "ok"),
        ], "el huerfano se sella, el reintento es fila nueva sin recobro"


@_skip_db
def test_reconcilia_negative_tope_cuenta_solo_normales_reversa_exenta():
    """Bug PR27-2 (MEDIA): el tope-3 de _reconcilia_negativas contaba TODAS
    las filas del ledger (SELECT count(*) crudo), incluidas las REVERSAS que
    el sellado CX1/GK1 exime (apply._SQL_COUNT_INTENTOS: solo tipo 'normal').
    Con 2 normales + 1 reversa la copia cruda cerraba en 'fallo:tope_intentos'
    aunque _ledger permitia el reintento. Regla 9: contra el codigo viejo la
    cola cierra failed y NO nace el POST de reintento — ambos asserts
    reventaban."""
    with _db_temporal("orbit_har_t3rev") as conn:
        ids = _semilla(conn)
        dec = _decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], kind="negative", term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        # 2 intentos 'normal' sellados + 1 'reversa' (total 3 en el crudo;
        # solo 2 cuentan para el tope).
        for seq, tipo in ((1, "normal"), (2, "normal"), (3, "reversa")):
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada, resultado, finished_at) VALUES (%s, %s, %s, '{}'::jsonb,"
                " false, 'ok', now())",
                (dec, seq, tipo),
            )
        handler, vistos = _handler_harvest()

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.negativas_confirmadas == 1, (
            "2 normales + 1 reversa: el tope NO se alcanza — REINTENTA, no cierra"
        )
        posts = [r for r in _mutaciones(vistos) if r.method == "POST"]
        assert len(posts) == 1, "nace el reintento del negativo (fila nueva del ledger)"
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied"
        conteo = conn.execute(
            "SELECT count(*) FILTER (WHERE tipo = 'normal'), count(*) FROM apply_attempt"
            " WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert conteo == (3, 4), "el 3er normal nace del reintento; la reversa queda exenta"


# ---------------------------------------------------------------------------
# 8. La cola manda: veto cierra el job; quota perezosa SOLO la primera vez
# ---------------------------------------------------------------------------


@_skip_db
def test_reconcilia_cierra_job_de_fila_vetoada_la_cola_manda():
    """Job pending cuya fila de cola fue VETADA mientras esperaba quota: el
    job se cierra failed (cero HTTP) y libera la clave de efecto del
    harvest_job_en_vuelo. Regla 9: dejarlo eterno bloquearia el termino para
    siempre (el evil del sellado 13)."""
    with _db_temporal("orbit_har_vclose") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_harvest": 0})
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _job_en(conn, dec, ids["ag"], "pending")
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(), vetoed_by ="
                " 'dueno', vence_el = now() + interval '30 days' WHERE id = %s",
                (q,),
            )
        finally:
            conn.execute("RESET ROLE")
        handler, vistos = _handler_harvest()

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_cerrados_por_cola == 1 and resumen.jobs_done == 0
        assert vistos == [], "un job vetado JAMAS sale a Amazon"
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("failed",)


@_skip_db
def test_reconcilia_cobra_quota_la_primera_vez_y_respeta_el_veto_en_released():
    """Job pending con fila released (esperaba quota) y sin ledger: al
    reconciliar COBRA la unidad (primera vez), reclama la fila (el veto
    sigue pudiendo ganar la carrera del claim) y completa. Regla 9: cobrar
    dos veces la misma operacion logica romperia el cap."""
    with _db_temporal("orbit_har_lazy") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_harvest": 1})
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _job_en(conn, dec, ids["ag"], "pending")
        handler, vistos = _handler_harvest()

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_done == 1
        quota = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:harvest",),
        ).fetchone()
        assert quota == (1,), "cobra UNA vez: la operacion logica completa (2 HTTPs)"
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied", "la fila released se reclama antes del HTTP (veto respetado)"


# ===========================================================================
# Review adversaria de phase 2: re-validacion PRE-claim del harvest (ADV-05)
# y reconciliador de pausas applying huerfanas (ADV-03, matriz §6.1)
# ===========================================================================


@_skip_db
def test_revalida_harvest_sigue_calificando_aplica():
    """ADV-05 cara complementaria: con evidencia FRESCA que SIGUE calificando
    (orders >= 2, ACoS bajo el tope fresco) la cadena corre completa — la
    re-validacion re-evalua la regla, no la bloquea."""
    with _db_temporal("orbit_har_revok") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        handler, vistos = _handler_harvest()

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.aplicadas == 1 and res.descartadas == [] and res.fallidas == 0
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "applied"
        )
        assert [f"{r.method} {r.url.path}" for r in _mutaciones(vistos)] == [
            "POST /sp/negativeKeywords",
            "POST /sp/keywords",
        ]


@_skip_db
def test_revalida_harvest_descarta_por_revision_acos_sobre_tope():
    """ADV-05: revision bitemporal DURANTE la ventana de veto que sube el ACoS
    del termino sobre el tope → la regla re-evaluada ya no califica →
    discarded 'ya_no_califica' CON NOTA, cero HTTP, sin job. Regla 9: contra
    el harvest que no re-valida, la cadena correria sobre evidencia rancia y
    este test reventaria."""
    with _db_temporal("orbit_har_revacos") as conn:
        ids = _semilla(conn)
        d = dt.datetime.now(dt.UTC)
        # Revision de la fecha con la venta: el costo se dispara (ACoS > tope
        # 35) — el colapso DISTINCT ON elige la observacion MAS RECIENTE.
        _termino_obs(
            conn,
            ids["run"],
            ids["ag"],
            TERMINO,
            d.date() - dt.timedelta(days=40),
            clicks=3,
            cost="22.00",
            ad_revenue="5.00",
            orders=2,
            observed=d - dt.timedelta(hours=1),
        )
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        handler, vistos = _handler_harvest()

        res = libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
        )

        assert res.descartadas == ["ya_no_califica"]
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", "ya_no_califica")
        assert vistos == [], "jamas un HTTP sobre una decision rancia"
        assert conn.execute("SELECT count(*) FROM harvest_job").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# ADV-03: pausas applying huerfanas (fallo ambiguo 5xx/red en el PUT) —
# la fila §6.1 "Cola applying huerfana - pause" reconciliada por GET fresco
# ---------------------------------------------------------------------------


def _handler_pause_estado(estado: str):
    """Handler MockTransport minimo para el reconciliador de pausas: SOLO el
    LIST fresco de estado de la keyword (probe 2.5: el GET directo esta
    retirado, 403; la reconciliacion jamas re-muta). `estado` viaja en el
    vocabulario UPPER del wire (apply_attempt 19-20)."""
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            # OJO: el access token se REGISTRA como secreto y scrub redacta
            # sus ocurrencias en TODO el proceso — jamas un token de 1 char
            # (una "t" romperia a cualquier test posterior que aserte texto).
            return httpx.Response(200, json={"access_token": "fake-access-1", "expires_in": 3600})
        vistos.append(request)
        if request.method == "POST" and request.url.path == "/sp/keywords/list":
            return httpx.Response(200, json={"keywords": [{"keywordId": "7201", "state": estado}]})
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    return handler, vistos


def _pause_aplicando_huerfana(conn, ids) -> tuple[int, int]:
    """Siembra el escenario ADV-03: fila pause vencida, liberada, claimed y un
    fallo ambiguo tras el ledger PRE-HTTP (la fila queda applying con su fila
    de ledger SIN sello — el rastro). Devuelve (queue_id, decision_id)."""
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs) VALUES (%s, %s, 'pause',"
        " now() - interval '3 days', %s, now() - interval '4 days', CURRENT_DATE - 60,"
        " CURRENT_DATE - 30, '{}'::jsonb) RETURNING id",
        (ids["ciclo_dec"], ids["kw"], ids["config"]),
    ).fetchone()[0]
    q = _encola_fila(conn, dec, ids["kw"], kind="pause", term=None)
    _libera_fila(conn, q)
    _claim_fila(conn, q)
    _fila_ledger_abierta(conn, dec)
    return q, dec


@_skip_db
def test_reconcilia_pause_aplicando_huerfana_paused_confirma():
    """Celda PAUSED de la matriz §6.1: LIST fresco lee PAUSED (Amazon SI
    proceso el PUT ambiguo) → confirmar: ledger sellado ok:reconciliado,
    resumen con verify_ok + ciclo EJECUTOR, cache con lo LEIDO y fila
    applied. Regla 9: sin reconciliador de pausas la fila queda applying para
    siempre (clave bloqueada) y este test reventaria."""
    with _db_temporal("orbit_har_rpause") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        q, dec = _pause_aplicando_huerfana(conn, ids)
        handler, vistos = _handler_pause_estado("PAUSED")  # wire UPPER (probe 2.5)

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.pausas_confirmadas == 1 and resumen.pausas_fallidas == 0
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "applied"
        )
        ledger = conn.execute(
            "SELECT resultado, finished_at IS NOT NULL FROM apply_attempt WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert ledger == ("ok:reconciliado", True)
        resumen_dec = conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen_dec == (True, ids["ciclo_ejec"]), "sello al ciclo EJECUTOR"
        cache = conn.execute(
            "SELECT status FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert cache == "PAUSED", "cache con LO LEIDO (sellado 16; wire UPPER)"
        assert [f"{r.method} {r.url.path}" for r in vistos] == ["POST /sp/keywords/list"], (
            "la reconciliacion de pause JAMAS re-muta: solo el LIST fresco"
        )


@_skip_db
def test_reconcilia_pause_aplicando_huerfana_enabled_failed():
    """Celda ENABLED de la matriz §6.1 (wire UPPER): el PUT ambiguo JAMAS proceso (la
    keyword sigue viva) → failed: ledger sellado con el veredicto, fila
    failed, SIN resumen confirmado y SIN reactivacion_manual (no hubo pause
    propio verificado previo). Regla 9: sin reconciliador, la clave
    entity_cut queda bloqueada para siempre y este test reventaria."""
    with _db_temporal("orbit_har_rpausa2") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        q, dec = _pause_aplicando_huerfana(conn, ids)
        handler, _v = _handler_pause_estado("ENABLED")  # wire UPPER (probe 2.5)

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.pausas_confirmadas == 0 and resumen.pausas_fallidas == 1
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "failed"
        )
        ledger = conn.execute(
            "SELECT resultado, finished_at IS NOT NULL FROM apply_attempt WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert ledger[0].startswith("fallo:") and ledger[1] is True
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_application WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            == 0
        ), "failed: sin confirmacion"
        assert conn.execute("SELECT count(*) FROM reactivacion_manual").fetchone()[0] == 0


@_skip_db
def test_reconcilia_pause_enabled_con_pause_propio_marca_reactivacion():
    """Celda ENABLED + pause propio verificado: el dueno re-activo a mano ->
    failed + INSERT reactivacion_manual (gracia 7d, idempotente por PK --
    sellado 17): el motor no vuelve a cortar la entidad durante la gracia."""
    with _db_temporal("orbit_har_rpausa3") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        # Pause propio verificado PREVIO: decision de pause vieja (en SU
        # propio ciclo — el esquema exige decision unica por entidad/ciclo)
        # con decision_application verify_ok TRUE.
        ciclo_viejo = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec_vieja = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
            " data_observed_at, window_start, window_end, inputs) VALUES (%s, %s, 'pause',"
            " now() - interval '20 days', %s, now() - interval '21 days', CURRENT_DATE - 80,"
            " CURRENT_DATE - 50, '{}'::jsonb) RETURNING id",
            (ciclo_viejo, ids["kw"], ids["config"]),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack,"
            " verify_ok) VALUES (%s, now(), '{}'::jsonb, true)",
            (dec_vieja,),
        )
        q, _dec = _pause_aplicando_huerfana(conn, ids)
        handler, _v = _handler_pause_estado("ENABLED")  # wire UPPER (probe 2.5)

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.pausas_fallidas == 1
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "failed"
        )
        deteccion = conn.execute(
            "SELECT detectada_en FROM reactivacion_manual WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()
        assert deteccion is not None, "ENABLED + pause propio verificado abre la gracia 7d"


# ===========================================================================
# Cross-review del dueno (codex+grok+qwen, ORBIT 04 P2): tope-3 sin reversas,
# reuso de job sin abortar la tx, reversa completa de verdad, fases con ids
# REALES (fail-closed) y barrido de cola harvest huerfana
# ===========================================================================


@_skip_db
def test_reversa_automatica_borra_la_keyword_aun_sin_negative_id():
    """GK1 (cola): el negative_id no se puede resolver (ack sin id y ausente
    de la lista) → la keyword NACIDA se borra IGUAL (parcial: lo que nacio se
    revierte). Regla 9: el retorno temprano viejo dejaba la exacta huerfana
    en destino (0 DELETEs) y este test reventaria."""
    with _db_temporal("orbit_har_crneg") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(
            conn,
            dec,
            ids["ag"],
            "exact_created",
            external_ids={"keyword_id": "k-7"},  # sin negative_id
        )
        handler, vistos = _handler_harvest()  # Amazon NO tiene el negativo

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_failed == 1 and resumen.jobs_done == 0
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [r.url.path for r in deletes] == ["/sp/keywords/delete"], (
            "la keyword nacida se borra aunque el negativo no se pueda resolver"
        )
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("failed",)


@_skip_db
def test_reintento_released_sin_quota_reusa_el_job_sin_abortar_la_tx():
    """CX2: el INSERT del job choca el unico parcial (job en vuelo de la
    misma clave) DENTRO de una transaccion de produccion (SIN autocommit,
    como app.db.connect): el choque se absorbe (savepoint) y el SELECT del
    reuso NO revienta con InFailedSqlTransaction. Regla 9: el catch viejo
    hacia SELECT sobre la tx abortada."""
    with _db_temporal("orbit_har_reuse") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_harvest": 0})
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _job_en(conn, dec, ids["ag"], "pending")  # job en vuelo de la MISMA clave
        handler, vistos = _handler_harvest()
        conn.autocommit = False  # la conexion de produccion (app.db.connect)

        try:
            resultado = aplica_harvest(
                conn,
                _aplicador(conn, handler, ids["ciclo_ejec"]),
                fila_cola(conn, q),
                platform="amazon_us",
            )
        finally:
            conn.rollback()
            conn.autocommit = True

        assert resultado.estado == "sin_quota", "reuso limpio del job (sin quota)"
        jobs = conn.execute("SELECT count(*), max(fase::text) FROM harvest_job").fetchone()
        assert jobs == (1, "pending"), "el job en vuelo se CONTINUA, no se duplica"
        assert vistos == [], "cap 0: cero HTTP"


@_skip_db
def test_reversa_completa_keyword_falla_y_el_negativo_no_se_borra():
    """CX3: si el delete de la keyword FALLA (>=400), el delete del negativo
    JAMAS sale — la reversa se aborta (orden sellado §7: el termino volveria
    a competir en origen con la keyword muerta) y se reintenta en el ciclo
    siguiente. Regla 9: el codigo viejo corria el negativo igual."""
    with _db_temporal("orbit_har_revfail") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        handler, vistos = _handler_harvest(fallo_delete_keyword=400)
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        ok = reversa_harvest_completo(
            conn, aplicador._cliente(), dec, negative_id="n-1", keyword_id="k-1"
        )

        assert ok is False
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [r.url.path for r in deletes] == ["/sp/keywords/delete"], (
            "el delete del negativo JAMAS sale si la keyword no se borro"
        )
        filas = conn.execute("SELECT tipo, resultado FROM apply_attempt ORDER BY seq").fetchall()
        assert len(filas) == 1 and filas[0][0] == "reversa", "solo el intento de la keyword"
        assert filas[0][1].startswith("fallo http 400"), (
            "el ledger declara el fallo del delete de la keyword"
        )


@_skip_db
def test_paso_negative_con_ack_sin_id_falla_fail_closed():
    """GK2(a): el POST del negativo responde 2xx SIN id legible: el paso
    FALLA (fail-closed: el termino NO se cosecha sin cortarse en origen) —
    reversa best-effort, failed + alerta, y la keyword JAMAS se postea.
    Regla 9: avanzar de todos modos dejaba el job en negative_created sin
    evidencia del corte en origen."""
    with _db_temporal("orbit_har_ackn") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(conn, dec, ids["ag"], "pending")
        handler, vistos = _handler_harvest(ack_negative_sin_id=True)

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_failed == 1 and resumen.jobs_done == 0
        assert resumen.alertas and resumen.alertas[0].motivo == MOTIVO_FALLO_NEGATIVE
        posts_kw = [r for r in _mutaciones(vistos) if r.url.path == "/sp/keywords"]
        assert posts_kw == [], "la keyword JAMAS se postea sin el negativo cortado en origen"
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("failed",)
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND seq = 1", (dec,)
        ).fetchone()[0]
        assert resultado == "fallo:ack_sin_id"
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "failed"


@_skip_db
def test_paso_keyword_exige_negative_id_del_origen():
    """GK2(b): job en negative_created SIN negative_id y el negativo NO esta
    en el origen (lista vacia): FAIL-CLOSED, la keyword no se postea. Regla
    9: el codigo viejo creaba la keyword igual (cosecha sin corte en origen)
    y este test reventaria."""
    with _db_temporal("orbit_har_negid") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(conn, dec, ids["ag"], "negative_created")  # external_ids vacio
        handler, vistos = _handler_harvest()  # origen SIN el negativo

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_failed == 1 and resumen.jobs_done == 0
        assert resumen.alertas[0].motivo == MOTIVO_FALLO_NEGATIVE
        assert _mutaciones(vistos) == [], "sin negative_id NO se postea la keyword"
        job = conn.execute("SELECT fase FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()
        assert job == ("failed",)


@_skip_db
def test_paso_keyword_con_ack_sin_id_falla_y_revierte_ambos():
    """GK2(b/c): el POST de la keyword responde 2xx SIN id legible: fail-closed
    (failed + alerta) y la reversa completa borra keyword y negativo (el id de
    la keyword se resuelve por IDENTIDAD en el destino). Regla 9: avanzar sin
    id dejaba la keyword huerfana e irreversible."""
    with _db_temporal("orbit_har_ackk") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _job_en(
            conn,
            dec,
            ids["ag"],
            "negative_created",
            external_ids={"negative_id": "n-9"},
        )
        handler, vistos = _handler_harvest(
            ack_keyword_sin_id=True,
            negatives=[
                {
                    "adGroupId": ORIGEN_GRUPO,
                    "campaignId": ORIGEN_CAMPANA,
                    "keywordId": "n-9",
                    "keywordText": TERMINO,
                    "matchType": "exact",
                    "state": "enabled",
                }
            ],
        )

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.jobs_failed == 1 and resumen.jobs_done == 0
        assert resumen.alertas[0].motivo == MOTIVO_FALLO_KEYWORD
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [r.url.path for r in deletes] == [
            "/sp/keywords/delete",
            "/sp/negativeKeywords/delete",
        ], "reversa completa: keyword PRIMERO (identidad resuelta), negativo despues"
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'"
            " ORDER BY seq DESC LIMIT 1",
            (dec,),
        ).fetchone()[0]
        assert resultado == "fallo:ack_sin_id"


def test_id_de_ack_parsea_207_anidado_listas_y_shapes():
    """GK2(c): _id_de_ack TAMBIEN resuelve el shape 207 anidado
    ({"<recurso>": {"success": [...]}} — la forma que _parse_single_207 del
    diseno v2 parseaba) y listas de ids. Regla 9: solo claves top-level
    devolveria None para el 207 y estos asserts reventarian."""
    from app.apply_harvest import _id_de_ack

    assert _id_de_ack({"negativeKeywordId": "n-1"}, "negativeKeywordId") == "n-1"
    assert _id_de_ack({"negativeKeywordIdList": ["n-2"]}, "negativeKeywordId") == "n-2"
    assert _id_de_ack({"keywordIdList": ["k-8", "k-9"]}, "keywordId") == "k-8"
    # Shape 207: el id vive en el primer success (plano o bajo la key recurso).
    assert (
        _id_de_ack(
            {"negativeKeywords": {"success": [{"negativeKeywordId": "n-3"}], "error": []}},
            "negativeKeywordId",
        )
        == "n-3"
    )
    assert (
        _id_de_ack({"keywords": {"success": [{"keyword": {"keywordId": "k-9"}}]}}, "keywordId")
        == "k-9"
    )
    # Sin success o con error: NADA legible (regla 3: jamas inventado).
    assert _id_de_ack({"keywords": {"success": [], "error": [{"index": 0}]}}, "keywordId") is None
    assert _id_de_ack({"keywords": {"error": [{"index": 0}]}}, "keywordId") is None
    assert _id_de_ack({"nada": 1}, "keywordId") is None
    assert _id_de_ack("no-dict", "keywordId") is None


@_skip_db
def test_barrido_cierra_fila_harvest_applying_sin_job_vivo():
    """GK4: fila applying kind harvest cuyo job YA no esta en vuelo (job
    failed con la cola viva) queda a cargo del barrido de reconciliacion:
    se cierra failed con nota — la clave term_cut NO queda bloqueada para
    siempre. Regla 9: sin barrido la fila huerfana quedaba applying
    eternamente y este test reventaria."""
    with _db_temporal("orbit_har_huerf") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_harvest": 0})
        dec_huerfana = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q_huerfana = _encola_fila(conn, dec_huerfana, ids["ag"], term=TERMINO)
        _libera_fila(conn, q_huerfana)
        _claim_fila(conn, q_huerfana)
        _fila_ledger_abierta(conn, dec_huerfana)
        _job_en(conn, dec_huerfana, ids["ag"], "failed")  # job cerrado, cola viva
        handler, vistos = _handler_harvest()

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.harvest_huerfanas_cerradas == 1
        cola = conn.execute(
            "SELECT estado FROM apply_queue WHERE id = %s", (q_huerfana,)
        ).fetchone()[0]
        assert cola == "failed", "la clave de efecto queda LIBRE (no applying eterno)"
        sello = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s", (dec_huerfana,)
        ).fetchone()[0]
        assert sello == "fallo:huerfana_sin_job", "nota en el ledger del por que"
        assert vistos == [], "cierre administrativo: cero HTTP"


# ===========================================================================
# Probe 2.5 (corrida autorizada 2026-08-26, ledger probe ids 1-20, log
# out/smoke-apply-20260826.log): el matchType del WIRE de los lists es el enum
# NEGATIVE_EXACT/EXACT (UPPER, apply_attempt 10 y 16) y el "delete" v3 ARCHIVA
# (state=ARCHIVED en el list tras el POST /delete). Tests PUROS del matcher de
# identidad (regla 9: rojo demostrado en out/tdd-red-o4-shapes.log contra el
# casefold plano que no conocia el prefijo NEGATIVE_ ni ARCHIVED).
# ===========================================================================


def test_identidad_normaliza_negative_exact_del_wire():
    """El list de negatives trae matchType 'NEGATIVE_EXACT' (probe 2.5,
    apply_attempt 10): el matcher lo normaliza a exacto — sin la normalizacion
    el negativo YA CORTADO jamas se hallaria por identidad y la matriz §6.1
    re-postearia un duplicado. Regla 9: contra el casefold plano reventaba."""
    from app.apply_harvest import _identidad

    items = [
        {
            "adGroupId": ORIGEN_GRUPO,
            "keywordText": TERMINO,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
            "keywordId": "n-1",
        }
    ]
    assert _identidad(items, ORIGEN_GRUPO, TERMINO) is not None
    assert _identidad(items, ORIGEN_GRUPO, TERMINO)["keywordId"] == "n-1"


def test_identidad_ignora_archived_el_delete_archiva():
    """El POST /delete responde 207 pero el item SIGUE en el list con
    state=ARCHIVED (probe 2.5, readback final de las formas negative/keyword):
    operativamente muerto = AUSENTE para la identidad viva. Sin el filtro, la
    reconciliacion confirmaria un negativo que el dueno borro. Regla 9:
    contra el matcher con normalizacion PERO sin filtro de ARCHIVED, la fila
    archivada (que va PRIMERA) ganaria y este test reventaria."""
    from app.apply_harvest import _identidad

    items = [
        {  # "borrado" por POST /delete (207 success): SIGUE en el list
            "adGroupId": ORIGEN_GRUPO,
            "keywordText": TERMINO,
            "matchType": "NEGATIVE_EXACT",
            "state": "ARCHIVED",
            "keywordId": "n-1",
        },
        {  # el negativo VIVO de otra creacion
            "adGroupId": ORIGEN_GRUPO,
            "keywordText": TERMINO,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
            "keywordId": "n-2",
        },
    ]
    propio = _identidad(items, ORIGEN_GRUPO, TERMINO)
    assert propio is not None and propio["keywordId"] == "n-2", (
        "ARCHIVED es operativamente AUSENTE: la identidad devuelve la fila VIVA"
    )


# ===========================================================================
# Cross-review del dueno shapes (codex+qwen, out/cross-review-shapes-*.log):
# el 207 con la fila en error[] NO es exito automatico (CX2/CX3) y el
# senuelo exige OTRO ad group VIVO (CX4). Tests PUROS donde se puede (regla
# 9: rojo en out/tdd-red-shapes-cr.log); los de DB corren en CI (skip sin
# tunel, igual que el resto de la suite).
# ===========================================================================


def test_solo_en_otro_ad_group_exige_otro_ad_group_y_vivo():
    """CX4: un ARCHIVED del MISMO grupo (delete-archiva del probe 2.5) o una
    variante de otro matchType en el PROPIO grupo NO son senuelo — el
    reintento sigue vivo. Regla 9: el any() viejo contaba cualquiera de esos
    items y el corte moria 'fallo:senuelo_otro_ad_group' sin senuelo real."""
    from app.apply_harvest import _solo_en_otro_ad_group

    archived_mismo_grupo = [
        {
            "adGroupId": ORIGEN_GRUPO,
            "keywordText": TERMINO,
            "matchType": "NEGATIVE_EXACT",
            "state": "ARCHIVED",
            "keywordId": "n-1",
        }
    ]
    assert _solo_en_otro_ad_group(archived_mismo_grupo, ORIGEN_GRUPO, TERMINO) is False, (
        "archived del MISMO grupo: operativamente ausente, se REINTENTA"
    )
    phrase_mismo_grupo = [
        {
            "adGroupId": ORIGEN_GRUPO,
            "keywordText": TERMINO,
            "matchType": "NEGATIVE_PHRASE",
            "state": "ENABLED",
            "keywordId": "n-2",
        }
    ]
    assert _solo_en_otro_ad_group(phrase_mismo_grupo, ORIGEN_GRUPO, TERMINO) is False, (
        "vivo del MISMO grupo (otro matchType): no es senuelo"
    )
    vivo_otro_grupo = [
        {
            "adGroupId": GRUPO_SENUELO,
            "keywordText": TERMINO,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
            "keywordId": "n-3",
        }
    ]
    assert _solo_en_otro_ad_group(vivo_otro_grupo, ORIGEN_GRUPO, TERMINO) is True, (
        "vivo de OTRO ad group: ese SI es el senuelo de la matriz 6.1"
    )
    assert _solo_en_otro_ad_group([], ORIGEN_GRUPO, TERMINO) is False


def test_errores_de_ack_caza_el_rechazo_por_item_del_207():
    """CX2/CX3: el ack 207 anidado lleva error[]/success[] por recurso (shape
    sellado por el probe 2.5, apply_attempt 13-17): la fila RECHAZADA viaja
    en error[] y un 2xx NO es exito automatico. Regla 9: sin el lector, el
    rechazo por-item era invisible para el motor."""
    from app.apply_harvest import _errores_de_ack

    limpio = {"negativeKeywords": {"error": [], "success": [{"negativeKeywordId": "n-1"}]}}
    assert _errores_de_ack(limpio) == []
    rechazado = {
        "negativeKeywords": {
            "error": [{"index": 0, "code": "DUPLICATE", "negativeKeywordId": "n-1"}],
            "success": [],
        }
    }
    assert _errores_de_ack(rechazado) == rechazado["negativeKeywords"]["error"]
    # Shapes defensivos: sin anidado no hay rechazo legible (regla 3).
    assert _errores_de_ack({"nada": 1}) == []
    assert _errores_de_ack("no-dict") == []


def test_reversa_rechazada_exige_sin_error_e_id_propio():
    """CX3: el veredicto del ack del delete — rechazado si error[] trae la
    fila, o si el id de success[] NO es el del objeto borrado (fail-closed:
    borrar OTRA cosa tampoco confirma). Regla 9: sin el veredicto, cualquier
    2xx sellaba 'ok' y la reversa mentia."""
    from app.apply_harvest import _reversa_rechazada

    ok = {"keywords": {"error": [], "success": [{"index": 0, "keywordId": "k-1"}]}}
    assert _reversa_rechazada(ok, "keywordId", "k-1") is False
    rechazado = {
        "keywords": {
            "error": [{"index": 0, "code": "NOT_FOUND", "keywordId": "k-1"}],
            "success": [],
        }
    }
    assert _reversa_rechazada(rechazado, "keywordId", "k-1") is True
    id_ajeno = {"keywords": {"error": [], "success": [{"keywordId": "k-OTRO"}]}}
    assert _reversa_rechazada(id_ajeno, "keywordId", "k-1") is True, (
        "success con el id de OTRA cosa: fail-closed, no confirma"
    )
    sin_estructura = {"status": 200}
    assert _reversa_rechazada(sin_estructura, "keywordId", "k-1") is False, (
        "sin error[] legible el 2xx del delete sigue en pie"
    )


@_skip_db
def test_reconcilia_negative_con_207_rechazado_no_es_applied():
    """CX2: el reintento de la reconciliacion recibe 207 con la fila en
    error[]: NO se sella applied. Regla 9: el codigo viejo sellaba 'ok' +
    'ok:reconciliado' + resumen verify_ok TRUE con CUALQUIER 2xx — el
    rechazo por-item quedaba registrado como exito y estos asserts
    reventaban."""
    with _db_temporal("orbit_har_207e") as conn:
        ids = _semilla(conn)
        dec = _decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], kind="negative", term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _fila_ledger_abierta(conn, dec)
        handler, _v = _handler_harvest(ack_negative_con_error=True)

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.negativas_confirmadas == 0 and resumen.negativas_fallidas == 1
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "failed", "un rechazo por-item JAMAS es applied"
        # El reintento sella SU fila con el rechazo (la huerfana del crash
        # conserva resultado NULL: el rastro, mismo trato que el fallo >=400).
        reintento = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND seq = 2", (dec,)
        ).fetchone()[0]
        assert reintento.startswith("fallo:ack_con_error"), (
            "el ledger del reintento declara el RECHAZO con su cuerpo"
        )
        assert "DUPLICATE" in reintento, "el cuerpo del ack viaja en el resultado"
        confirmado = conn.execute(
            "SELECT verify_ok FROM decision_application WHERE decision_id = %s", (dec,)
        ).fetchone()
        assert confirmado is None, "sin resumen: no hay confirmacion de un rechazo"


@_skip_db
def test_reconcilia_negative_con_207_limpio_si_es_applied():
    """CX2 cara complementaria: el 207 limpio (error[] vacio + id en
    success[]) SI es applied — el veredicto nuevo no rompe el camino sano
    (regla 9: el reintento de la matriz 6.1 sigue confirmando)."""
    with _db_temporal("orbit_har_207l") as conn:
        ids = _semilla(conn)
        dec = _decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], kind="negative", term=TERMINO)
        _libera_fila(conn, q)
        _claim_fila(conn, q)
        _fila_ledger_abierta(conn, dec)
        handler, _v = _handler_harvest()

        resumen = _reconcilia(conn, handler, ids["ciclo_ejec"])

        assert resumen.negativas_confirmadas == 1
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
        assert cola == "applied"
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND seq = 2", (dec,)
        ).fetchone()[0]
        assert resultado == "ok"


@_skip_db
def test_reversa_completa_con_delete_rechazado_no_borra_el_negativo():
    """CX3: el delete de la keyword responde 207 con la fila en error[]: la
    reversa NO se confirma y el delete del negativo JAMAS sale (keyword
    primero, §7 — borrar el negativo con la keyword viva devolveria el
    termino a competir en origen Y destino). Regla 9: el codigo viejo sellaba
    'ok' cualquier 2xx, corria el negativo igual y devolvia True."""
    with _db_temporal("orbit_har_revrech") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        handler, vistos = _handler_harvest(delete_keyword_rechazado=True)
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        ok = reversa_harvest_completo(
            conn, aplicador._cliente(), dec, negative_id="n-1", keyword_id="k-1"
        )

        assert ok is False, "un delete rechazado por-item NO confirma la reversa"
        deletes = [r for r in _mutaciones(vistos) if r.url.path.endswith("/delete")]
        assert [r.url.path for r in deletes] == ["/sp/keywords/delete"], (
            "el delete del negativo JAMAS corre: la keyword sigue viva"
        )
        filas = conn.execute("SELECT tipo, resultado FROM apply_attempt ORDER BY seq").fetchall()
        assert len(filas) == 1 and filas[0][0] == "reversa"
        assert filas[0][1].startswith("fallo:reversa_rechazada"), (
            "el ledger declara el rechazo con el cuerpo del ack"
        )
