"""Tests del nucleo del aplicador (`app/apply`) — ORBIT 04, task 2.1.

DB temporal con el patron de test_apply_schema (0001+0002 aplicadas; corre
contra el Postgres real del tunel con ORBIT_TEST_DSN, skip fail-closed si no)
+ HTTP 100% mock (`httpx.MockTransport`): a Amazon NO sale ninguna llamada
real, ni siquiera el token LWA. Los "secretos" son SIEMPRE falsos.

DoD de la tarea, un test por candado (regla 9 en cada uno):

1. Escalera shadow -> CERO HTTP (transport espia cuenta 0 requests, ni el
   token: sin re-resolucion por decision el cliente se construiria y el
   espia lo contaria).
2. Envelope live + goal shadow -> NO aplica: JAMAS filtra por inputs.modo ni
   cycle.mode (el residual sellado de cycle.py; una implementacion que
   filtrara por el modo del ciclo aplicaria).
3. Crash entre ledger y HTTP: la fila del ledger sin sello ES el rastro y la
   reconciliacion la VE; la decision NO esta aplicada.
4. No existe 4o intento: 3 filas de ledger -> el aplicador salta sin HTTP.
5. Bid descartado bajo cap no reaparece: cap 1, dos elegibles -> 1 HTTP, el
   otro descartado con motivo; re-llamar NO lo reintenta.
6. Cache actualizado post-readback con LO LEIDO (distinto al enviado): sin
   esto el ciclo siguiente decide sobre el bid viejo (sellado 16).
7. Terna parcial revienta: confirmed_at sin platform_ack -> CHECK del esquema.
8. applied_count por ciclo EJECUTOR: decision nacida en ciclo A shadow,
   aplicada en ciclo B live -> applied_cycle_id == B y applied_count de B.
9. Quota: consumo atomico bajo cap; cap agotado -> False; config sin clave ->
   False y NO nace fila (fail-closed); reversa EXENTA.
10. Reversa de bid: HTTP con old_value, ledger tipo reversa exento, readback
    sella, cache con lo leido.
11. AdsApiErrorMutacion: >=400 lleva el cuerpo (redactado) y el ledger
    resultado lo conserva; la decision NO se marca aplicada.
12. get_sellado usa el scope SELLADO de la instancia (header presente).
13. Orden sellado de bids bajo cap: banda_menos_25 > banda_menos_12 >
    banda_mas_15, dentro de cada banda por costo de la ventana DESC.
14. Secuencia sellada completa de un bid ok (ledger pre-HTTP con payload
    EXACTO, quota, ack, verify, resumen, cache, applied_count).

PENDIENTES del probe autorizado 2.5 (brief APPLY §13, sellado 23): el path y
el shape del readback (contenedor `keywords`/`targets`, campo `bid`) son
SUPUESTOS de estos tests contra MockTransport; el probe fija los shapes
reales y los re-sella.
"""

from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from decimal import Decimal

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import SQL, SQL2, _postgres_obligatorio_ausente, _test_dsn

from app.ads.config import AdsCredentials
from app.ads.write import AdsApiErrorMutacion, AdsWriteClient
from app.apply import (
    MOTIVO_FALLO_HTTP,
    MOTIVO_FUERA_DE_CAP,
    MOTIVO_MODO_NO_LIVE,
    MOTIVO_TOPE_INTENTOS,
    MOTIVO_YA_APLICADA,
    Aplicador,
    DecisionBid,
    bids_del_ciclo,
    consume_quota,
    intentos_sin_sello,
    motor_quota,
    orden_bids,
    reversa_bid,
)

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"
FAKE_PROFILE_US = 404040

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _fake_credentials() -> AdsCredentials:
    return AdsCredentials(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        refresh_token=FAKE_REFRESH_TOKEN,
    )


def _token_response(n: int = 1) -> httpx.Response:
    return httpx.Response(200, json={"access_token": f"fake-access-{n}", "expires_in": 3600})


# ---------------------------------------------------------------------------
# Patron _db_temporal de test_apply_schema (COPIADO; aplica 0001 + 0002)
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


# ---------------------------------------------------------------------------
# Semilla: config con caps + ciclos + entidades + goal + decision bid
# ---------------------------------------------------------------------------


def _entidad(conn, kind: str, external: str, parent=None) -> int:
    # keyword exige match_type/keyword_text coherentes (CHECK del esquema).
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


def _semilla(
    conn,
    *,
    caps: dict | None = None,
    goal_mode: str = "live",
    goal_enabled: bool = True,
    con_goal: bool = True,
    mode_ciclo_dec: str = "shadow",
) -> dict:
    """Config vigente con los caps pedidos (default amplio), el ciclo que
    DECIDIO (mode_ciclo_dec: el residual shadow->aplicada-en-live), el ciclo
    EJECUTOR live, campaign->ad_group->dos keywords con state, y el goal de
    plataforma."""
    settings = dict(caps) if caps is not None else {"ads_apply_cap_amazon_us_bid": 10}
    config_id = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-apply', %s) RETURNING id",
        (Json(settings),),
    ).fetchone()[0]
    ciclo_dec = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES (%s, 'amazon_us') RETURNING id",
        (mode_ciclo_dec,),
    ).fetchone()[0]
    ciclo_ejec = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    camp = _entidad(conn, "campaign", "7001")
    ag = _entidad(conn, "ad_group", "7101", parent=camp)
    kw = _entidad(conn, "keyword", "7201", parent=ag)
    kw2 = _entidad(conn, "keyword", "7202", parent=ag)
    for entidad in (kw, kw2):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
            " synced_at) VALUES (%s, 1.00, 'USD', 'ENABLED', now())",
            (entidad,),
        )
    if con_goal:
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_currency,"
            " enabled, mode) VALUES ('platform', 'amazon_us', 55, 'USD', %s, %s)",
            (goal_enabled, goal_mode),
        )
    return {
        "config": config_id,
        "ciclo_dec": ciclo_dec,
        "ciclo_ejec": ciclo_ejec,
        "camp": camp,
        "ag": ag,
        "kw": kw,
        "kw2": kw2,
    }


def _decision_bid(
    conn,
    ciclo: int,
    config_id: int,
    entidad: int,
    *,
    motivo: str = "banda_menos_25",
    cost: str = "120.50",
    old: str = "1.00",
    new: str = "0.85",
    modo: str = "live",
) -> int:
    """Decision kind='bid' con inputs congelados al shape del ciclo (regla 4:
    Decimals como string). El aplicador JAMAS lee inputs.modo (residual)."""
    inputs = {
        "motor": "bid",
        "platform": "amazon_us",
        "ventanas": {
            "bids": {"cost": cost, "ad_revenue": "40.00", "clicks": 30, "orders": 3},
            "cortes": {"cost": cost, "ad_revenue": "40.00", "clicks": 30, "orders": 0},
        },
        "goal": {
            "scope": "platform",
            "target_acos_pct": "55",
            "bid_floor": "0.10",
            "bid_ceiling": "2.50",
            "harvest": None,
        },
        "target_acos_pct_usado": "55",
        "bid_actual": old,
        "bid_moneda": "USD",
        "factor": "-0.25",
        "motivo": motivo,
        "modo": modo,
    }
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " data_observed_at, window_start, window_end, old_value, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'bid', %s, now() - interval '40 days', CURRENT_DATE - 60,"
        " CURRENT_DATE - 30, %s, %s, 'USD', %s) RETURNING id",
        (ciclo, entidad, config_id, Decimal(old), Decimal(new), Json(inputs)),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Mock de la API de Ads: token + mutacion + readback (transport espia)
# ---------------------------------------------------------------------------


def _handler_api(remoto: dict[str, str], *, desviado: dict[str, str] | None = None):
    """Handler MockTransport: cuenta TODOS los requests de la API (el token LWA
    no pasa por aqui, va a api.amazon.com). El PUT deja a Amazon con lo
    escrito (`remoto`); el GET del readback devuelve `desviado` si la clave
    esta (test de cache con lo LEIDO != enviado) y sino `remoto`."""
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        vistos.append(request)
        if request.method == "PUT":
            body = json.loads(request.content)
            ext = str(body.get("keywordId") or body.get("targetId"))
            remoto[ext] = body["bid"]
            return httpx.Response(200, json={"ack": body})
        if request.method == "GET":
            ext = request.url.params.get("keywordId") or request.url.params.get("targetId")
            contenedor = "targets" if request.url.path == "/sp/targets" else "keywords"
            campo = "targetId" if contenedor == "targets" else "keywordId"
            bid = (desviado or {}).get(ext, remoto[ext])
            return httpx.Response(200, json={contenedor: [{campo: ext, "bid": bid}]})
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    return handler, vistos


def _aplicador(conn, handler, cycle_id: int) -> Aplicador:
    return Aplicador(
        conn,
        platform="amazon_us",
        profile_id=FAKE_PROFILE_US,
        credentials=_fake_credentials(),
        cycle_id_ejecutor=cycle_id,
        owner="test:apply",
        job_key="ads_optimizer:amazon_us",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _write_client(handler) -> AdsWriteClient:
    return AdsWriteClient(
        _fake_credentials(),
        platform="amazon_us",
        profile_id=FAKE_PROFILE_US,
        modo_confirmado="live",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _puts(vistos: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in vistos if r.method == "PUT"]


def _gets(vistos: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in vistos if r.method == "GET"]


# ---------------------------------------------------------------------------
# 1. Escalera shadow -> CERO HTTP (regla 9)
# ---------------------------------------------------------------------------


@_skip_db
def test_escalera_shadow_cero_http():
    """La escalera global en shadow: ni token LWA. Regla 9: sin la
    re-resolucion por decision (p.ej. confiando en inputs.modo='live'), el
    cliente se construiria y el espia contaria requests."""
    with _db_temporal("orbit_apply_sh0") as conn:
        ids = _semilla(conn)
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], modo="live")
        handler, vistos = _handler_api({"7201": "0.85"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="shadow")

        assert res.skips == [MOTIVO_MODO_NO_LIVE]
        assert res.aplicadas == 0
        assert res.descartadas == []
        assert vistos == [], "escalera shadow: cero requests a la API de Ads"
        for tabla in ("apply_attempt", "decision_application", "apply_quota_state"):
            assert conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] == 0, tabla
        assert conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0] == Decimal("1.00")


# ---------------------------------------------------------------------------
# 2. Envelope live + goal shadow/off/disabled/sin goal -> NO aplica (regla 9)
# ---------------------------------------------------------------------------


@_skip_db
@pytest.mark.parametrize(
    "kwargs",
    [
        {"goal_mode": "shadow"},  # el residual sellado de cycle.py
        {"goal_mode": "off"},
        {"goal_enabled": False},
        {"con_goal": False},
    ],
    ids=["goal-shadow", "goal-off", "goal-disabled", "sin-goal"],
)
def test_envelope_live_con_goal_no_live_no_aplica(kwargs):
    """La decision nacio con inputs.modo='live' en un ciclo 'live' (todo lo
    congelado dice live), pero el goal HOY no llega a live: el aplicador
    re-resuelve y NO aplica. Regla 9: una implementacion que filtrara por
    inputs.modo o cycle.mode aplicaria."""
    with _db_temporal("orbit_apply_gl0") as conn:
        ids = _semilla(conn, mode_ciclo_dec="live", **kwargs)
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], modo="live")
        handler, vistos = _handler_api({"7201": "0.85"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.skips == [MOTIVO_MODO_NO_LIVE]
        assert vistos == []
        assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM decision_application").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 3. Crash entre ledger y HTTP: el rastro existe y la decision no esta aplicada
# ---------------------------------------------------------------------------


@_skip_db
def test_crash_entre_ledger_y_http_deja_rastro_visible():
    """Se siembra una fila del ledger SIN sello (el crash entre INSERT y
    HTTP): la reconciliacion la VE (finished_at IS NULL) y la decision NO
    esta aplicada (sin decision_application)."""
    with _db_temporal("orbit_apply_crash") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 1, 'normal', %s, true)",
            (dec, Json({"keywordId": "7201", "bid": "0.85"})),
        )

        sin_sello = intentos_sin_sello(conn)

        assert [fila[1] for fila in sin_sello] == [dec], "la fila sin sello ES el rastro"
        assert sin_sello[0][2] == 1 and sin_sello[0][3] == "normal"
        aplicada = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM decision_application WHERE decision_id = %s)", (dec,)
        ).fetchone()[0]
        assert aplicada is False


# ---------------------------------------------------------------------------
# 4. No existe 4o intento (COUNT del ledger)
# ---------------------------------------------------------------------------


@_skip_db
def test_no_existe_cuarto_intento():
    with _db_temporal("orbit_apply_tope") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        for seq in (1, 2, 3):
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada) VALUES (%s, %s, 'normal', '{}'::jsonb, true)",
                (dec, seq),
            )
        handler, vistos = _handler_api({"7201": "0.85"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.skips == [MOTIVO_TOPE_INTENTOS]
        assert vistos == [], "tope de 3 intentos: ni se construye el cliente para ella"
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_attempt WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            == 3
        ), "no nace 4a fila"


# ---------------------------------------------------------------------------
# 5. Bid descartado bajo cap no reaparece
# ---------------------------------------------------------------------------


@_skip_db
def test_bid_descartado_bajo_cap_no_reaparece():
    with _db_temporal("orbit_apply_cap") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_bid": 1})
        dec1 = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], cost="120.50")
        dec2 = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw2"], cost="80.00")
        handler, vistos = _handler_api({"7201": "0.85", "7202": "0.85"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.orden == [dec1, dec2], "hemorragia primero: mismo motivo, cost DESC"
        assert res.aplicadas == 1
        assert res.descartadas == [MOTIVO_FUERA_DE_CAP]
        assert [json.loads(p.content)["keywordId"] for p in _puts(vistos)] == ["7201"]
        assert len(_gets(vistos)) == 1

        # Re-llamar: el aplicado salta (ya_aplicada) y el descartado NO se
        # reintenta (bids fuera de cap = DESCARTADOS, sellado 8).
        res2 = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res2.skips == [MOTIVO_YA_APLICADA]
        assert res2.descartadas == [MOTIVO_FUERA_DE_CAP]
        assert len(_puts(vistos)) == 1, "el descartado jamas genera HTTP"
        fila = conn.execute(
            "SELECT used, cap FROM apply_quota_state WHERE motor = %s",
            (motor_quota("amazon_us", "bid"),),
        ).fetchone()
        assert fila == (1, 1)
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_attempt WHERE decision_id = %s", (dec2,)
            ).fetchone()[0]
            == 0
        ), "el descartado no deja fila de ledger"


# ---------------------------------------------------------------------------
# 6. Cache actualizado post-readback con LO LEIDO
# ---------------------------------------------------------------------------


@_skip_db
def test_cache_actualizado_post_readback_con_lo_leido():
    """El readback devuelve 0.90 cuando se pidio 0.85: el cache queda con LO
    LEIDO (divergencia incluida), jamas lo enviado. Regla 9: sin este UPDATE,
    el ciclo siguiente calcularia sobre el bid viejo (sellado 16)."""
    with _db_temporal("orbit_apply_rb") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], new="0.85")
        handler, vistos = _handler_api({"7201": "0.85"}, desviado={"7201": "0.90"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 0, "divergencia: verify_ok false, no cuenta como aplicada"
        cache = conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert cache == Decimal("0.90"), "el cache lleva LO LEIDO del readback"
        resumen = conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen == (False, None)
        assert (
            conn.execute(
                "SELECT applied_count FROM optimizer_cycle WHERE id = %s", (ids["ciclo_ejec"],)
            ).fetchone()[0]
            is None
        )
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert resultado == "fallo:divergencia_readback", (
            "QW1: la divergencia sella SIEMPRE la misma etiqueta (antes 'ok')"
        )


# ---------------------------------------------------------------------------
# 7. Terna parcial revienta (esquema)
# ---------------------------------------------------------------------------


@_skip_db
def test_terna_parcial_revienta():
    with _db_temporal("orbit_apply_terna") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        conn.execute("INSERT INTO decision_application (decision_id) VALUES (%s)", (dec,))

        # confirmed_at sin platform_ack: "confien en mi" sin el readback.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE decision_application SET confirmed_at = now() WHERE decision_id = %s",
                (dec,),
            )
        # verify_ok sin confirmed_at: enfriaria sin readback.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE decision_application SET verify_ok = true WHERE decision_id = %s", (dec,)
            )
        # La terna COMPLETA si pasa.
        conn.execute(
            "UPDATE decision_application SET confirmed_at = now(), platform_ack = '{}'::jsonb,"
            " verify_ok = true WHERE decision_id = %s",
            (dec,),
        )


# ---------------------------------------------------------------------------
# 8. applied_count por ciclo EJECUTOR
# ---------------------------------------------------------------------------


@_skip_db
def test_applied_count_y_applied_cycle_por_ciclo_ejecutor():
    """Decision nacida en ciclo A shadow (inputs.modo shadow), aplicada en el
    ciclo B live: applied_cycle_id == B y applied_count cuadra por ejecutor."""
    with _db_temporal("orbit_apply_ejec") as conn:
        ids = _semilla(conn, mode_ciclo_dec="shadow")
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], modo="shadow")
        handler, vistos = _handler_api({"7201": "0.85"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 1
        assert len(_puts(vistos)) == 1
        assert (
            conn.execute(
                "SELECT applied_cycle_id FROM decision_application WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            == ids["ciclo_ejec"]
        )
        ejec = conn.execute(
            "SELECT applied_count FROM optimizer_cycle WHERE id = %s", (ids["ciclo_ejec"],)
        ).fetchone()[0]
        decide = conn.execute(
            "SELECT applied_count FROM optimizer_cycle WHERE id = %s", (ids["ciclo_dec"],)
        ).fetchone()[0]
        assert (ejec, decide) == (1, None)


# ---------------------------------------------------------------------------
# 9. Quota atomica: bajo cap, agotada, sin clave (fail-closed)
# ---------------------------------------------------------------------------


@_skip_db
def test_quota_consume_bajo_cap_agotada_y_sin_clave_fail_closed():
    with _db_temporal("orbit_apply_q") as conn:
        _semilla(conn, caps={"ads_apply_cap_amazon_us_bid": 2})

        assert motor_quota("amazon_us", "bid") == "ads_optimizer:amazon_us:bid"
        assert consume_quota(conn, "amazon_us", "bid") is True
        assert consume_quota(conn, "amazon_us", "bid") is True
        assert consume_quota(conn, "amazon_us", "bid") is False, "cap agotado"

        fila = conn.execute(
            "SELECT used, cap, quota_date = (now() AT TIME ZONE 'UTC')::date"
            " FROM apply_quota_state WHERE motor = %s",
            (motor_quota("amazon_us", "bid"),),
        ).fetchone()
        assert fila == (2, 2, True)

        # Config NUEVA mas reciente SIN claves: fail-closed, no nace fila.
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('sin caps', '{}'::jsonb)"
        )
        assert consume_quota(conn, "amazon_us", "bid") is False
        assert consume_quota(conn, "amazon_us", "negative") is False
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_quota_state WHERE motor = %s",
                (motor_quota("amazon_us", "negative"),),
            ).fetchone()[0]
            == 0
        ), "sin clave NO nace fila del dia"


# ---------------------------------------------------------------------------
# 10. Reversa de bid: old_value, ledger exento, readback, cache
# ---------------------------------------------------------------------------


@_skip_db
def test_reversa_de_bid_exenta_de_quota():
    with _db_temporal("orbit_apply_rev") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_bid": 1})
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], old="1.00", new="0.85")
        handler, vistos = _handler_api({"7201": "1.00"})  # tras la reversa, Amazon vuelve a 1.00
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])
        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")
        assert res.aplicadas == 1

        ok = reversa_bid(conn, _write_client(handler), bids_del_ciclo(conn, ids["ciclo_dec"])[0])

        assert ok is True
        assert [json.loads(p.content)["bid"] for p in _puts(vistos)] == ["0.85", "1.00"]
        filas = conn.execute(
            "SELECT tipo, quota_cobrada, resultado FROM apply_attempt ORDER BY seq"
        ).fetchall()
        assert filas == [("normal", True, "ok"), ("reversa", False, "ok")], "reversa EXENTA"
        used = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = %s",
            (motor_quota("amazon_us", "bid"),),
        ).fetchone()[0]
        assert used == 1, "la reversa NO consume quota (sellado 12)"
        cache = conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert cache == Decimal("1.00"), "cache con lo LEIDO del readback de la reversa"


# ---------------------------------------------------------------------------
# 11. AdsApiErrorMutacion: cuerpo en la excepcion y en el ledger
# ---------------------------------------------------------------------------


def test_write_mutacion_400_levanta_ads_api_error_mutacion_con_cuerpo_redactado():
    """>=400 hoy pierde el body (AdsApiError solo lleva status+metodo+path);
    AdsApiErrorMutacion conserva un snippet SANEADO: el access token de LWA
    esta registrado como secreto y el cuerpo lo ecoa -> scrub lo redacta."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(400, json={"detail": "INVALID_BID", "eco_token": "fake-access-1"})

    client = _write_client(handler)
    with pytest.raises(AdsApiErrorMutacion) as ei:
        client.actualizar_bid_keyword(101, Decimal("0.75"), "USD")

    assert isinstance(ei.value, Exception)
    assert ei.value.status == 400
    assert ei.value.method == "PUT"
    assert ei.value.path == "/sp/keywords"
    assert "INVALID_BID" in ei.value.cuerpo
    assert "fake-access-1" not in ei.value.cuerpo, "el cuerpo va redactado (scrub)"
    assert len(ei.value.cuerpo) <= 500


@_skip_db
def test_aplicador_sella_fallo_http_en_ledger_y_no_marca_aplicada():
    with _db_temporal("orbit_apply_err") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        vistos: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token_response()
            vistos.append(request)
            assert request.method == "PUT", "sin readback tras un rechazo >=400"
            return httpx.Response(400, json={"detail": "INVALID_BID"})

        ap = _aplicador(conn, handler, ids["ciclo_ejec"])
        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.skips == [MOTIVO_FALLO_HTTP]
        assert res.aplicadas == 0
        fila = conn.execute(
            "SELECT resultado, ack, finished_at FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchone()
        assert "INVALID_BID" in fila[0], "el ledger conserva el cuerpo del rechazo"
        assert fila[1] is None and fila[2] is not None
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_application WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            == 0
        ), "la decision NO se marca aplicada"


# ---------------------------------------------------------------------------
# 12. get_sellado usa el scope de la instancia
# ---------------------------------------------------------------------------


def test_get_sellado_usa_el_scope_de_la_instancia():
    """El re-check del aplicador JAMAS pasa un profile a mano: get_sellado es
    la unica puerta de lectura con el scope sellado del constructor."""
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(200, json={"keywords": []})

    client = _write_client(handler)
    resp = client.get_sellado("/sp/keywords", params={"keywordId": "7201"})

    assert resp.status_code == 200
    api = vistos[1]
    assert api.method == "GET"
    assert api.url.path == "/sp/keywords"
    assert api.url.params["keywordId"] == "7201"
    assert api.headers["Amazon-Advertising-API-Scope"] == str(FAKE_PROFILE_US)


# ---------------------------------------------------------------------------
# 13. Orden sellado de bids bajo cap (pure, sin DB)
# ---------------------------------------------------------------------------


def _bid_manual(id_: int, motivo: str, cost: str | None) -> DecisionBid:
    return DecisionBid(
        id=id_,
        ad_entity_id=id_,
        old_value=Decimal("1.00"),
        new_value=Decimal("0.75"),
        value_currency="USD",
        inputs={"motivo": motivo, "ventanas": {"cortes": {"cost": cost}}},
    )


def test_orden_bids_prioridad_de_hemorragia_sellada():
    d25_100 = _bid_manual(1, "banda_menos_25", "100")
    d25_200 = _bid_manual(2, "banda_menos_25", "200")
    d12_999 = _bid_manual(3, "banda_menos_12", "999")
    d15_500 = _bid_manual(4, "banda_mas_15", "500")

    orden = orden_bids([d15_500, d12_999, d25_100, d25_200])

    assert [d.id for d in orden] == [2, 1, 3, 4], (
        "banda_menos_25 > banda_menos_12 > banda_mas_15; dentro de banda, cost DESC"
    )

    # cost None (regla 3: costo desconocido) queda al final de SU banda; un
    # motivo fuera de las bandas (no existe en kind bid) queda al final.
    d25_none = _bid_manual(5, "banda_menos_25", None)
    assert [d.id for d in orden_bids([d25_none, d25_100])] == [1, 5]
    raro = _bid_manual(6, "otro_cosa", "9999")
    assert [d.id for d in orden_bids([raro, d15_500])] == [4, 6]


# ---------------------------------------------------------------------------
# 14. Secuencia sellada completa de un bid ok
# ---------------------------------------------------------------------------


@_skip_db
def test_secuencia_sellada_bid_ok():
    with _db_temporal("orbit_apply_ok") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        ticks: list[int] = []
        handler, vistos = _handler_api({"7201": "0.85"})
        ap = Aplicador(
            conn,
            platform="amazon_us",
            profile_id=FAKE_PROFILE_US,
            credentials=_fake_credentials(),
            cycle_id_ejecutor=ids["ciclo_ejec"],
            owner="test:apply",
            job_key="ads_optimizer:amazon_us",
            transport=httpx.MockTransport(handler),
            sleep=lambda seconds: None,
            tick=lambda: ticks.append(1),
        )

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 1 and res.skips == [] and res.descartadas == []
        assert len(ticks) >= 2, "heartbeat DURANTE mutacion y readback"
        # El ledger nacio PRE-HTTP con el payload EXACTO y quota cobrada.
        intento = conn.execute(
            "SELECT seq, tipo, request_payload, quota_cobrada, ack, resultado, finished_at"
            " FROM apply_attempt WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert intento[0] == 1
        assert intento[1] == "normal"
        assert intento[2] == {"keywordId": "7201", "bid": "0.85"}
        assert intento[3] is True
        assert intento[4] == {"ack": {"keywordId": "7201", "bid": "0.85"}}
        assert intento[5] == "ok"
        assert intento[6] is not None
        # HTTP: UNA mutacion (quantizado a 2 dec) + UN readback con el MISMO scope.
        assert [json.loads(p.content) for p in _puts(vistos)] == [
            {"keywordId": "7201", "bid": "0.85"}
        ]
        assert len(_gets(vistos)) == 1
        assert _gets(vistos)[0].headers["Amazon-Advertising-API-Scope"] == str(FAKE_PROFILE_US)
        # Resumen: la terna junta + applied_cycle_id AL CONFIRMAR.
        resumen = conn.execute(
            "SELECT confirmed_at, platform_ack, verify_ok, applied_cycle_id"
            " FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen[0] is not None
        assert resumen[1] == {"ack": {"keywordId": "7201", "bid": "0.85"}}
        assert resumen[2] is True
        assert resumen[3] == ids["ciclo_ejec"]
        # Cache: lo LEIDO.
        cache = conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert cache == Decimal("0.85")


# ===========================================================================
# Review adversaria de phase 2 (ADV-04): reconciliacion del ledger de BIDS
# sin sello — matriz §6.1 "Ledger sin sello - bid", antes sin caller
# ===========================================================================


@_skip_db
def test_reconcilia_bids_get_igual_confirma():
    """Veredicto GET == pedido: el PUT ambiguo SI proceso → confirmar — sello
    'ok:reconciliado', resumen con verify_ok + applied_cycle_id del EJECUTOR y
    cache con lo LEIDO. HTTP: SOLO el GET de readback (jamas re-mutar). Regla
    9: contra el codigo sin caller de intentos_sin_sello, la fila zombie
    miente para siempre en el ledger y este test reventaria."""
    from app.apply import reconcilia_bids

    with _db_temporal("orbit_apply_rbid1") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], new="0.85")
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 1, 'normal', %s, true)",
            (dec, Json({"keywordId": "7201", "bid": "0.85"})),
        )
        handler, vistos = _handler_api({"7201": "0.85"})

        confirmadas, fallidas = reconcilia_bids(conn, _aplicador(conn, handler, ids["ciclo_ejec"]))

        assert (confirmadas, fallidas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, finished_at IS NOT NULL FROM apply_attempt WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert fila == ("ok:reconciliado", True)
        resumen = conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen == (True, ids["ciclo_ejec"])
        cache = conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert cache == Decimal("0.85"), "cache con lo LEIDO del GET fresco (sellado 16)"
        assert _puts(vistos) == [], "la reconciliacion JAMAS re-muta"
        assert len(_gets(vistos)) == 1


@_skip_db
def test_reconcilia_bids_divergencia_reintenta_bajo_tope_y_falla():
    """Veredicto GET != pedido (divergencia): la fila original se sella
    'fallo:divergencia_readback', se REINTENTA bajo tope (fila nueva + PUT) y
    la divergencia persistente sella tambien el reintento — verify_ok FALSE,
    sin applied_cycle_id, cache con LO LEIDO."""
    from app.apply import reconcilia_bids

    with _db_temporal("orbit_apply_rbid2") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], new="0.85")
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 1, 'normal', %s, true)",
            (dec, Json({"keywordId": "7201", "bid": "0.85"})),
        )
        handler, vistos = _handler_api({"7201": "0.85"}, desviado={"7201": "0.90"})

        confirmadas, fallidas = reconcilia_bids(conn, _aplicador(conn, handler, ids["ciclo_ejec"]))

        assert (confirmadas, fallidas) == (0, 1)
        filas = conn.execute(
            "SELECT seq, resultado, quota_cobrada FROM apply_attempt WHERE decision_id = %s"
            " ORDER BY seq",
            (dec,),
        ).fetchall()
        assert [f[0] for f in filas] == [1, 2], "el reintento nace como fila nueva del ledger"
        assert all(f[1] == "fallo:divergencia_readback" for f in filas)
        assert filas[1][2] is False, "el reintento NO recobra (la unidad ya estaba cobrada)"
        resumen = conn.execute(
            "SELECT verify_ok, applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert resumen == (False, None), "divergencia: no cuenta como aplicada ni enfria"
        cache = conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert cache == Decimal("0.90"), "cache con LO LEIDO (divergencia incluida)"
        assert len(_puts(vistos)) == 1, "UN reintento del tope (no un bucle)"


@_skip_db
def test_reconcilia_bids_ambiguo_falla_sin_reintento():
    """Veredicto ambiguo (GET 5xx agotado): failed SIN reintento (conserva su
    cobro, matriz §6.1) — la fila se sella con el veredicto y NO nace PUT ni
    fila nueva; sin resumen, cache intacto."""
    from app.apply import reconcilia_bids

    with _db_temporal("orbit_apply_rbid3") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], new="0.85")
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 1, 'normal', %s, true)",
            (dec, Json({"keywordId": "7201", "bid": "0.85"})),
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token_response()
            if request.method == "GET":
                return httpx.Response(503, json={"message": "boom"})
            raise AssertionError("el ambiguo JAMAS re-muta: no puede haber PUT")

        confirmadas, fallidas = reconcilia_bids(conn, _aplicador(conn, handler, ids["ciclo_ejec"]))

        assert (confirmadas, fallidas) == (0, 1)
        filas = conn.execute(
            "SELECT count(*), count(finished_at) FROM apply_attempt WHERE decision_id = %s",
            (dec,),
        ).fetchone()
        assert filas == (1, 1), "la fila se cierra; SIN fila nueva de reintento"
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert resultado == "fallo:readback_ambiguo"
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_application WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            == 0
        )
        cache = conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert cache == Decimal("1.00"), "sin lectura no hay evidencia: el cache no se toca"


# ===========================================================================
# ADV-07: el write client de produccion SI duerme el backoff de los 429
# ===========================================================================


# ===========================================================================
# Cross-review del dueno (codex+grok+qwen, ORBIT 04 P2): tope-3 solo cuenta
# 'normal', readback de OTRA entidad, etiqueta unificada de divergencia,
# tope ANTES del cobro, ack 2xx saneado y divergencia observable
# ===========================================================================


@_skip_db
def test_tope_3_cuenta_solo_intentos_normal_las_reversas_no_consumen():
    """CX1/GK1: un harvest completo deja 2 filas 'normal' y su reversa
    completa otras 2 'reversa'; el reintento normal SIGUE cabiendo (el tope
    cuenta intentos de aplicacion, las reversas son el mecanismo de seguridad
    y jamas consumen presupuesto de intentos). Regla 9: con el COUNT sin
    filtro el 4o paso devolvia None y este test reventaria."""
    from app import apply as apply_mod

    with _db_temporal("orbit_apply_t3n") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        for seq, tipo in ((1, "normal"), (2, "normal"), (3, "reversa"), (4, "reversa")):
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada) VALUES (%s, %s, %s, '{}'::jsonb, false)",
                (dec, seq, tipo),
            )

        id_attempt = apply_mod._ledger(
            conn, dec, "normal", {"keywordId": "7201", "bid": "0.85"}, quota_cobrada=False
        )

        assert id_attempt is not None, "2 reversas NO agotan el tope-3 de normales"
        fila = conn.execute(
            "SELECT seq, tipo FROM apply_attempt WHERE id = %s", (id_attempt,)
        ).fetchone()
        assert fila == (3, "normal"), "el seq sigue el conteo de SOLO normales"


@_skip_db
def test_readback_que_devuelve_otra_entidad_no_confirma_ni_toca_cache():
    """CX6/GK8: el GET del readback responde la fila de OTRA keyword (id
    distinto): NO es evidencia de esta decision — verify_ok False, cache
    INTACTO y sin applied. Regla 9: el filas[0] sin cruce de id confirmaria
    el bid de otra entidad y pisaria el cache con su valor."""
    with _db_temporal("orbit_apply_otra") as conn:
        ids = _semilla(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], new="0.85")
        vistos: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token_response()
            vistos.append(request)
            if request.method == "PUT":
                return httpx.Response(200, json={"ack": json.loads(request.content)})
            assert request.method == "GET", "solo PUT + readback GET"
            # Respuesta de OTRA entidad: el id NO es el pedido.
            return httpx.Response(200, json={"keywords": [{"keywordId": "9999", "bid": "0.85"}]})

        ap = _aplicador(conn, handler, ids["ciclo_ejec"])
        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 0
        assert len(_puts(vistos)) == 1 and len(_gets(vistos)) == 1
        cache = conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s", (ids["kw"],)
        ).fetchone()[0]
        assert cache == Decimal("1.00"), "cache INTACTO: la fila leida era de OTRA entidad"
        resumen = conn.execute(
            "SELECT verify_ok FROM decision_application WHERE decision_id = %s", (dec,)
        ).fetchone()
        assert resumen == (False,)
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert resultado == "fallo:readback_sin_bid", "sin evidencia de ESTA entidad"


@_skip_db
def test_divergencia_readback_sella_la_misma_etiqueta_en_apply_y_reversa():
    """QW1: la divergencia de readback sella SIEMPRE
    'fallo:divergencia_readback' — _ejecuta_mutacion y reversa_bid ponian
    'ok' con verify_ok False (la misma condicion con etiquetas distintas
    segun el camino). Regla 9: contra el 'ok' viejo ambos asserts revientan."""
    with _db_temporal("orbit_apply_etq") as conn:
        ids = _semilla(conn)
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], old="1.00", new="0.85")
        handler, _v = _handler_api({"7201": "0.85"}, desviado={"7201": "0.90"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")
        ok_reversa = reversa_bid(
            conn, _write_client(handler), bids_del_ciclo(conn, ids["ciclo_dec"])[0]
        )

        assert ok_reversa is False, "la reversa tambien diverge (Amazon queda en 0.90)"
        filas = conn.execute("SELECT tipo, resultado FROM apply_attempt ORDER BY seq").fetchall()
        assert [f[0] for f in filas] == ["normal", "reversa"]
        assert all(f[1] == "fallo:divergencia_readback" for f in filas), (
            "etiqueta UNIFICADA en ambos caminos"
        )


@_skip_db
def test_tope_se_chequea_antes_del_cobro_quota_intacta():
    """GK5/QW2: decision ya a tope de 3 normales → skip SIN quemar la unidad
    de quota (la decision ya a tope no genera HTTP ni fila de quota). Regla
    9: el orden viejo (consume_quota primero) dejaria used=1 en
    apply_quota_state sin intento alguno."""
    with _db_temporal("orbit_apply_tqc") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_bid": 10})
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        for seq in (1, 2, 3):
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada) VALUES (%s, %s, 'normal', '{}'::jsonb, true)",
                (dec, seq),
            )
        handler, vistos = _handler_api({"7201": "0.85"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.skips == [MOTIVO_TOPE_INTENTOS]
        assert vistos == [], "a tope: ni token LWA"
        quota = conn.execute(
            "SELECT count(*) FROM apply_quota_state WHERE motor = %s",
            (motor_quota("amazon_us", "bid"),),
        ).fetchone()[0]
        assert quota == 0, "la unidad NO se quema en una decision ya a tope"


@_skip_db
def test_ack_2xx_con_secreto_queda_redactado_en_el_ledger():
    """GK9: el body del ack SIEMPRE pasa por scrub antes del ledger (solo el
    camino >=400 redactaba). El mock ecoa el token LWA (registrado como
    secreto por el cliente) en un 2xx. Regla 9: sin el scrub, el token
    quedaria en apply_attempt.ack y este test reventaria."""
    with _db_temporal("orbit_apply_scrub") as conn:
        ids = _semilla(conn)
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], new="0.85")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token_response()
            if request.method == "PUT":
                return httpx.Response(
                    200, json={"ack": json.loads(request.content), "eco_token": "fake-access-1"}
                )
            ext = request.url.params.get("keywordId")
            return httpx.Response(200, json={"keywords": [{"keywordId": ext, "bid": "0.85"}]})

        ap = _aplicador(conn, handler, ids["ciclo_ejec"])
        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 1
        ack = conn.execute("SELECT ack::text FROM apply_attempt").fetchone()[0]
        assert "fake-access-1" not in ack, "el ack del ledger va redactado (scrub SIEMPRE)"
        assert "REDACTED" in ack


@_skip_db
def test_divergencia_de_readback_es_observable_en_el_resultado():
    """GK10/QW4: la decision con readback divergente (HTTP 200 pero Amazon
    quedo con OTRO bid) cuenta en el campo propio del ResultadoAplicador —
    antes desaparecia de aplicadas/skips/descartadas (la mutacion SALIO y no
    era visible). Regla 9: sin el contador, divergencias == 0 reventaria."""
    with _db_temporal("orbit_apply_divo") as conn:
        ids = _semilla(conn)
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], new="0.85")
        handler, _v = _handler_api({"7201": "0.85"}, desviado={"7201": "0.90"})
        ap = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 0 and res.skips == [] and res.descartadas == []
        assert res.divergencias == 1, "campo propio: la divergencia es observable"


@_skip_db
def test_write_client_de_produccion_duerme_el_backoff_de_429():
    """El Aplicador SIN sleep inyectado (como _aplicador_real) le deja al
    write client su time.sleep REAL: un 429 reintenta CON backoff. Regla 9:
    contra el lambda no-op de produccion, el sleep del cliente NO es
    time.sleep y el reintento sale back-to-back (elapsed ~0) — ambos asserts
    reventarian (hallazgo ADV-07: la rampa del dia 1 quemaria cap con
    throttling transitorio)."""
    import time as _time

    with _db_temporal("orbit_apply_slp") as conn:
        ids = _semilla(conn)
        _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], new="0.85")
        puts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token_response()
            if request.method == "PUT":
                puts["n"] += 1
                if puts["n"] == 1:
                    # Sin Retry-After: el backoff exponencial real manda.
                    return httpx.Response(429, json={"message": "throttled"})
                return httpx.Response(200, json={"ack": "reintento"})
            ext = request.url.params.get("keywordId")
            return httpx.Response(200, json={"keywords": [{"keywordId": ext, "bid": "0.85"}]})

        ap = Aplicador(
            conn,
            platform="amazon_us",
            profile_id=FAKE_PROFILE_US,
            credentials=_fake_credentials(),
            cycle_id_ejecutor=ids["ciclo_ejec"],
            owner="test:sleep",
            job_key="ads_optimizer:amazon_us",
            transport=httpx.MockTransport(handler),
            # SIN sleep: exactamente como _aplicador_real en produccion.
        )
        cliente = ap._cliente()
        assert cliente._sleep is _time.sleep, (
            "produccion: el sleep del write client es el REAL (jamas un no-op)"
        )

        t0 = _time.monotonic()
        res = ap.aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")
        elapsed = _time.monotonic() - t0

        assert res.aplicadas == 1
        assert puts["n"] == 2, "el 429 se reintento (SIN recobrar quota)"
        assert elapsed >= 1.0, "el reintento DUERME: backoff del intento 1 = 1.0s + jitter"
        used = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = %s",
            (motor_quota("amazon_us", "bid"),),
        ).fetchone()[0]
        assert used == 1, "el 429 reintentado es el MISMO intento del ledger (sin recobro)"
