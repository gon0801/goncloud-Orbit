"""Tests de la integracion del apply en el ciclo (`app.cycle`) — ORBIT 04, 2.4.

DB temporal con el patron de test_cycle (0001+0002 aplicadas; corre contra el
Postgres real del tunel con ORBIT_TEST_DSN, skip fail-closed si no) + HTTP
100% mock (`httpx.MockTransport`): CERO escrituras vivas a Amazon, ni siquiera
el token LWA. Los "secretos" son SIEMPRE falsos.

DoD de la tarea, un candado por test (regla 9 en cada uno):

1. ZOMBIE Y SUCESOR concurrentes -> SOLO UNO muta (decision 11, la mejora que
   cycle.py anunciaba): el zombie que perdio el lock (owner cambiado en la
   tabla) es abortado por el ownership-check SIN ningun HTTP de mutacion
   (transport espia en 0); el sucesor aplica normal.
2. LEASE PERDIDO a mitad de fase -> la siguiente mutacion NO sale: nota
   apply_abortado_owner en notes, status degraded, decisiones intactas.
3. LOCK SE LIBERA DESPUES DEL APPLY (decision 11): durante TODA la fase de
   apply el lock sigue siendo nuestro (verificado por otra SESION desde un
   hook del transport); al terminar el ciclo, liberado.
4. ESCALERA SHADOW -> CERO HTTP re-verificado tras el flip del flag (sellado
   22): ni la fabrica se construye (una fabrica que reviente lo demuestra);
   los cortes quedan encolados modo shadow.
5. GUARD status='running' en el cierre: segundo _cierra_envelope sobre un
   envelope ya cerrado -> 0 filas, warning, NO pisa (jamas sobre-cerrar).
6. E2E LIVE: ciclo con escalera live, bids elegibles y cortes -> bids
   aplicados (HTTP mock con readback), cortes encolados live, applied_count
   en el envelope del ciclo EJECUTOR y notes con los contadores del apply.
7. ENVELOPE LIVE + GOAL SHADOW (el residual de cycle.py): la decision se
   persiste, el aplicador NO aplica ese bid (cero HTTP) y encola_cortes lo
   marca shadow — el modo efectivo se re-resuelve POR DECISION.
8. SKIP veto_pendiente (sellado 5): clave de efecto en vuelo -> el ciclo NO
   re-decide esa clave (contador en notes); claves distintas avanzan.
9. SIN PERFIL ACEPTADO: la fase de apply aborta fail-closed con nota
   apply_error, CERO HTTP de mutacion y el ciclo SIGUE (decisiones intactas).

RE-SELLADO contra el probe 2.5 (corrida autorizada del dueno 2026-08-26,
ledger apply_attempt ids 1-20, log out/smoke-apply-20260826.log): el
readback vive por LIST (GET directo retirado, 403) y el PUT viaja en el
contenedor del recurso con bid NUMERO.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from test_cycle import (
    DECIDED_AT,
    JOB_KEY,
    SQL13,
    SQL15,
    _config_version,
    _entidad,
    _estado,
    _metrica,
    _rango,
    _siembra_maestra,
)
from test_schema import SQL, SQL2, SQL3, _postgres_obligatorio_ausente, _test_dsn

from app import cycle as ciclo
from app.ads.config import AdsCredentials
from app.apply import Aplicador

SQL14 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0014_keyword_archivo_manual.sql"
).read_text(encoding="utf-8")

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"
FAKE_PROFILE_US = 404040

OWNER = "test-host:24"

PERFIL_US = {
    "profileId": FAKE_PROFILE_US,
    "countryCode": "US",
    "currencyCode": "USD",
    "accountInfo": {"type": "seller", "name": "Tienda US", "validPaymentMethod": True},
}
PERFIL_MX = {
    "profileId": 414141,
    "countryCode": "MX",
    "currencyCode": "MXN",
    "accountInfo": {"type": "seller", "name": "Tienda MX", "validPaymentMethod": True},
}

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@pytest.fixture
def secrets_falsos(monkeypatch):
    """from_secrets_dir devuelve credenciales FALSAS: la fabrica REAL del
    ciclo (perfil via GET /v2/profiles) corre sin tocar el secrets dir."""
    monkeypatch.setattr(
        AdsCredentials,
        "from_secrets_dir",
        classmethod(
            lambda cls, _dir=None: AdsCredentials(
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
                refresh_token=FAKE_REFRESH_TOKEN,
            )
        ),
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

    def conectar_extra(autocommit: bool = True):
        """Conexion adicional a la MISMA DB temporal (otra sesion: verificacion
        del lock desde fuera de la sesion del ciclo). `autocommit=False` sirve
        para reproducir la conexion de PRODUCCION (app.db.connect no usa
        autocommit y el CLI cierra sin commit)."""
        return psycopg.connect(dsn, dbname=db, autocommit=autocommit)

    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # 0001: roles, esquema sellado, grants
        conn.execute(SQL2)  # 0002: cola de cortes, ledger, sellos de quota
        conn.execute(SQL3)  # 0003: ads_optimizer_goal sin DEFAULT en piso/techo
        conn.execute(SQL13)  # 0013 (BIDS 01): la guarda entidad_inerte lee la vista en TX2
        conn.execute(SQL15)  # 0015 (ORBIT 06 2.3): el peldano margen lee su vista en TX2
        conn.execute(SQL14)  # 0014 (BIDS 01 2.2): _paso_keyword lee el ledger anti-duplicado
        yield conn, conectar_extra
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# Helpers: config live con caps, segunda keyword solo-bid, corrida del ciclo
# ---------------------------------------------------------------------------


def _config_live_caps(conn, *, escalera: str = "live") -> int:
    """Config MAS RECIENTE que la de _siembra_maestra (gana por id DESC) con
    la escalera y los caps del dia: sin cap de bid, consume_quota fail-closed
    y el E2E no aplicaria nada."""
    return _config_version(
        conn,
        {
            "ads_optimizer_mode": escalera,
            "ads_apply_cap_amazon_us_bid": 10,
            "ads_apply_cap_amazon_us_pause": 2,
            "ads_apply_cap_amazon_us_negative": 5,
            "ads_apply_cap_amazon_us_harvest": 2,
        },
    )


def _obs(fecha: dt.date) -> dt.datetime:
    """observed_at de una observacion de la kw2 (misma convencion _obs)."""
    return dt.datetime(fecha.year, fecha.month, fecha.day, 1, tzinfo=dt.UTC)


def _siembra_kw2_bid_puro(conn, run_id, kw2) -> None:
    """Segunda keyword 100% BID en su PROPIO campaign/ad_group (no toca la
    evidencia del grupo maestro): la ventana de CORTES queda en clicks 0
    (jamas pause) y la de BIDS conserva la hemorragia 36/100/orders 1 ->
    banda -25, mismo golden 1.00 -> 0.75."""
    for fecha in _rango(dt.date(2026, 7, 14), dt.date(2026, 7, 17)):
        _metrica(
            conn,
            run_id,
            kw2,
            fecha,
            _obs(fecha),
            cost="0.25",
            ad_revenue="0.50",
            clicks=0,
            orders=0,
        )
    for i, fecha in enumerate(_rango(dt.date(2026, 7, 18), dt.date(2026, 8, 12))):
        _metrica(
            conn,
            run_id,
            kw2,
            fecha,
            _obs(fecha),
            cost="1.00",
            ad_revenue="2.50",
            clicks=0,
            orders=1 if i == 0 else 0,
        )
    for fecha in _rango(dt.date(2026, 8, 13), dt.date(2026, 8, 16)):
        _metrica(
            conn,
            run_id,
            kw2,
            fecha,
            _obs(fecha),
            cost="2.50",
            ad_revenue="8.75",
            clicks=6,
            orders=0,
            # BIDS 01: hoja servida -> impressions reales (espera un bid).
            impressions=60,
        )
    for fecha in _rango(dt.date(2026, 8, 17), dt.date(2026, 8, 19)):
        _metrica(
            conn,
            run_id,
            kw2,
            fecha,
            _obs(fecha),
            cost="0.10",
            ad_revenue="0.10",
            clicks=1,
            orders=0,
            impressions=10,
        )


def _agrega_kw2(conn, ids: dict) -> int:
    """Agrega la segunda keyword bid (campaign/ad_group propios + estado)."""
    camp2 = _entidad(conn, "amazon_us", "campaign", "9003")
    ag2 = _entidad(conn, "amazon_us", "ad_group", "9103", parent=camp2)
    kw2 = _entidad(
        conn, "amazon_us", "keyword", "9203", parent=ag2, match_type="EXACT", keyword_text="kw2 bid"
    )
    synced = DECIDED_AT - dt.timedelta(hours=4)
    _estado(conn, camp2, synced_at=synced)
    _estado(conn, ag2, synced_at=synced)
    _estado(conn, kw2, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
    run_id = conn.execute("SELECT id FROM ingest_run LIMIT 1").fetchone()[0]
    _siembra_kw2_bid_puro(conn, run_id, kw2)
    return kw2


def _corre(conn, *, factory=None, owner=OWNER):
    kwargs = {"aplicador_factory": factory} if factory is not None else {}
    return ciclo.corre_ciclo(
        conn,
        platform="amazon_us",
        owner=owner,
        decided_at=DECIDED_AT,
        heartbeat_cada=1,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Mock de la API de Ads: token + /v2/profiles + PUT bid + GET readback
# ---------------------------------------------------------------------------


def _handler(bids: dict[str, str] | None = None, *, perfiles: str = "US", al_request=None):
    """Handler MockTransport: token LWA (api.amazon.com, fuera del conteo),
    GET /v2/profiles (`perfiles` = "US" acepta amazon_us; "MX" no deja NINGUN
    perfil aceptado para la plataforma), PUT de bid (deja el remoto con lo
    escrito) y GET de readback. `al_request(request)` es el hook de mitad de
    fase (flip del owner / inspeccion del lock) y corre ANTES de servir."""
    remoto = dict(bids or {})
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access-1", "expires_in": 3600})
        vistos.append(request)
        if al_request is not None:
            al_request(request)
        if request.method == "GET" and request.url.path == "/v2/profiles":
            data = [PERFIL_MX] if perfiles == "MX" else [PERFIL_US]
            return httpx.Response(200, json={"profiles": data})
        if request.method == "PUT":
            # Contenedor del recurso + bid NUMERO (probe 2.5, apply_attempt 3/6).
            body = json.loads(request.content)
            obj = body["keywords"][0] if "keywords" in body else body["targetingClauses"][0]
            ext = str(obj.get("keywordId") or obj.get("targetId"))
            remoto[ext] = obj["bid"]
            return httpx.Response(207, json={"ack": obj})
        if request.method == "POST" and request.url.path.endswith("/list"):
            # Readback por LIST (probe 2.5, apply_attempt 4-5: GET retirado).
            contenedor, campo = (
                ("targetingClauses", "targetId")
                if request.url.path == "/sp/targets/list"
                else ("keywords", "keywordId")
            )
            filas = [{campo: ext, "bid": remoto[ext]} for ext in remoto]
            return httpx.Response(200, json={contenedor: filas})
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    return handler, vistos


def _mutaciones(vistos: list[httpx.Request]) -> list[httpx.Request]:
    # Los /list son lecturas (readback por LIST del probe 2.5), no mutaciones.
    return [r for r in vistos if r.method != "GET" and not r.url.path.endswith("/list")]


def _puts(vistos: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in vistos if r.method == "PUT"]


def _fabrica_real_mock(handler):
    """La fabrica REAL del ciclo (_aplicador_real: perfil via GET /v2/profiles
    + evaluar_perfiles, MISMA fuente del sync) con MockTransport inyectado."""

    def fabrica(conn, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return ciclo._aplicador_real(conn, **kwargs)

    return fabrica


def _fabrica_directa(handler):
    """Fabrica que construye el Aplicador a mano (sin resolver perfil): para
    tests donde el perfil no es el sujeto."""

    def fabrica(conn, *, platform, cycle_id_ejecutor, owner, job_key, tick, transport=None):
        return Aplicador(
            conn,
            platform=platform,
            profile_id=FAKE_PROFILE_US,
            credentials=AdsCredentials(
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
                refresh_token=FAKE_REFRESH_TOKEN,
            ),
            cycle_id_ejecutor=cycle_id_ejecutor,
            owner=owner,
            job_key=job_key,
            tick=tick,
            guard_http=tick,
            transport=httpx.MockTransport(handler),
            sleep=lambda seconds: None,
        )

    return fabrica


# ---------------------------------------------------------------------------
# 1. Zombie y sucesor concurrentes -> solo uno muta (regla 9, decision 11)
# ---------------------------------------------------------------------------


@_skip_db
def test_zombie_y_sucesor_concurrentes_solo_uno_muta(secrets_falsos):
    """El zombie perdio el lock (el sucesor reclamo tras el TTL) y lo descubre
    en el ownership-check PRE-HTTP: aborta SIN nigun HTTP de mutacion (regla
    9: sin el check, el zombie habria hecho el PUT tambien — dos procesos
    escribiendo a Amazon). El sucesor aplica normal y NO re-decide las claves
    que el zombie dejo en vuelo (skip veto_pendiente)."""
    with _db_temporal("orbit_cyc_zombie") as (conn, _c):
        _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        handler, vistos = _handler({"9201": "0.75", "9203": "0.75"})

        def fabrica_zombie(conn, **kwargs):
            # El zombie pierde el lease MIENTRAS corre (sucesor reclamo el
            # lock tras el TTL): el owner de la fila ya NO es el nuestro.
            conn.execute(
                "UPDATE ads_optimizer_lock SET owner = 'sucesor' WHERE job_key = %s", (JOB_KEY,)
            )
            kwargs["transport"] = httpx.MockTransport(handler)
            return ciclo._aplicador_real(conn, **kwargs)

        res_z = _corre(conn, factory=fabrica_zombie, owner="zombie:1")

        # El zombie NO libero el lock del sucesor (liberacion por owner)
        assert (
            conn.execute(
                "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()[0]
            == "sucesor"
        )
        # Las decisiones del zombie quedan intactas (4: bid/pause/negative/harvest)
        assert (
            conn.execute(
                "SELECT count(*) FROM decision WHERE cycle_id = %s", (res_z.cycle_id,)
            ).fetchone()[0]
            == 4
        )
        notas_z = json.loads(res_z.notes)
        assert res_z.status == "degraded"
        assert notas_z["apply"]["apply_abortado_owner"] is True
        # CERO HTTP de mutacion del zombie: el unico request es el GET de
        # perfiles de la fabrica (lectura, sin guard todavia)
        assert _mutaciones(vistos) == []
        assert [r.url.path for r in vistos] == ["/v2/profiles"]

        # El sucesor reclama (TTL vencido) y aplica NORMAL
        conn.execute(
            "UPDATE ads_optimizer_lock SET heartbeat_at = now() - interval '31 minutes'"
            " WHERE job_key = %s",
            (JOB_KEY,),
        )
        res_s = _corre(conn, factory=_fabrica_real_mock(handler), owner="sucesor")

        assert res_s.status == "done"
        assert len(_puts(vistos)) == 1, "solo el sucesor muto; el zombie jamas salio por HTTP"
        assert json.loads(res_s.notes)["apply"]["bids_aplicados"] == 1
        # El sucesor NO re-decide las claves que el zombie dejo en vuelo: la
        # pause de kw_pause y los terminos de la negative y del HARVEST
        # (term_cut: negative y harvest CHOCAN por el mismo termino, sellado 4)
        notas_s = json.loads(res_s.notes)
        assert notas_s["skips"]["entidad"].get("veto_pendiente") == 1  # pause de kw_pause
        assert notas_s["skips"]["termino"].get("veto_pendiente") == 2  # tortugas + buena yarda
        assert notas_s["apply"]["cortes_encolados"] == {"live": 0, "shadow": 0, "choques": 0}
        # ADV-04: el rastro del zombie (ledger sin sello, su PUT jamas salio)
        # lo cierra el reconciliador de bids del sucesor — el mock de Amazon
        # ya tenia 0.75, el GET fresco CONFIRMA sin nuevo HTTP de mutacion.
        assert notas_s["apply"]["bids_reconciliados"] == {"confirmados": 1, "fallidos": 0}
        assert (
            conn.execute(
                "SELECT applied_count FROM optimizer_cycle WHERE id = %s", (res_s.cycle_id,)
            ).fetchone()[0]
            == 2
        ), "1 bid aplicado por el sucesor + 1 reconciliado del zombie (ejecutor)"


# ---------------------------------------------------------------------------
# 2. Lease perdido a mitad de fase -> la siguiente mutacion no sale
# ---------------------------------------------------------------------------


@_skip_db
def test_lease_perdido_a_medida_aborta_sin_http(secrets_falsos):
    """El owner cambia a MITAD de la fase (hook del transport, tras el readback
    del PRIMER bid): la mutacion del SEGUNDO bid no sale — el aborto es
    fail-closed, la nota apply_abortado_owner viaja en notes, status degraded
    y las decisiones del ciclo quedan intactas."""
    with _db_temporal("orbit_cyc_lease") as (conn, _c):
        ids = _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        _agrega_kw2(conn, ids)
        flip = {"hecho": False}

        def al_request(request: httpx.Request) -> None:
            # Tras el readback por LIST del primer bid (PUT+LIST completos), el
            # sucesor reclama el lock: el zombie recien lo descubre en el
            # PROXIMO HTTP (probe 2.5: el GET directo esta retirado).
            if (
                not flip["hecho"]
                and request.method == "POST"
                and request.url.path == "/sp/keywords/list"
            ):
                flip["hecho"] = True
                conn.execute(
                    "UPDATE ads_optimizer_lock SET owner = 'sucesor' WHERE job_key = %s",
                    (JOB_KEY,),
                )

        handler, vistos = _handler({"9201": "0.75", "9203": "0.75"}, al_request=al_request)
        res = _corre(conn, factory=_fabrica_real_mock(handler))

        assert res.status == "degraded"
        notas = json.loads(res.notes)
        assert notas["apply"]["apply_abortado_owner"] is True
        assert flip["hecho"]  # el primer bid completo SI salio
        assert len(_puts(vistos)) == 1, "la mutacion del segundo bid NUNCA salio"
        # Decisiones intactas: los 2 bids del ciclo siguen en decision
        assert (
            conn.execute(
                "SELECT count(*) FROM decision WHERE cycle_id = %s AND kind = 'bid'",
                (res.cycle_id,),
            ).fetchone()[0]
            == 2
        )
        # El segundo bid queda como rastro en vuelo: ledger sin sello (la fila
        # ES el rastro; la reconciliacion del ciclo siguiente decide)
        assert (
            conn.execute("SELECT count(*) FROM apply_attempt WHERE finished_at IS NULL").fetchone()[
                0
            ]
            == 1
        )


# ---------------------------------------------------------------------------
# 3. Lock se libera DESPUES del apply (decision 11)
# ---------------------------------------------------------------------------


@_skip_db
def test_lock_se_libera_despues_del_apply(secrets_falsos):
    with _db_temporal("orbit_cyc_lockpos") as (conn, conectar):
        _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        vigilados: list[str | None] = []
        extra = conectar()  # OTRA sesion: el lock debe ser visible y NUESTRO

        def al_request(request: httpx.Request) -> None:
            fila = extra.execute(
                "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()
            vigilados.append(fila[0] if fila is not None else None)

        handler, vistos = _handler({"9201": "0.75"}, al_request=al_request)
        res = _corre(conn, factory=_fabrica_real_mock(handler))
        extra.close()

        assert res.status == "done"
        # Regla 9: si el lock se soltara ANTES del apply (el bug que la
        # decision 11 corrige), la otra sesion veria owner ajeno/NULL.
        assert vigilados and set(vigilados) == {OWNER}
        assert len(_puts(vistos)) == 1  # el apply corrio DENTRO del lock
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()[0]
            == 0
        ), "al terminar el ciclo, el lock queda liberado"


# ---------------------------------------------------------------------------
# 4. Escalera shadow -> cero HTTP re-verificado tras el flip (sellado 22)
# ---------------------------------------------------------------------------


@_skip_db
def test_escalera_shadow_cero_http_tras_el_flip():
    with _db_temporal("orbit_cyc_sh0") as (conn, _c):
        _siembra_maestra(conn)  # escalera shadow por defecto

        def fabrica_explota(*_args, **_kwargs):
            raise AssertionError("la fabrica NO se llama en shadow: cero construccion de cliente")

        res = _corre(conn, factory=fabrica_explota)

        assert res.status == "done"
        notas = json.loads(res.notes)
        assert notas["degradacion_live"] is None  # shadow por escalera, sin nota
        assert notas["apply"]["cortes_encolados"] == {"live": 0, "shadow": 3, "choques": 0}
        filas = conn.execute(
            "SELECT modo, estado, count(*) FROM apply_queue GROUP BY modo, estado"
        ).fetchall()
        assert filas == [("shadow", "pending_veto", 3)]
        for tabla in ("apply_attempt", "decision_application", "apply_quota_state"):
            assert conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] == 0, tabla


def test_flip_hay_modulo_apply_live_ya_no_degrada():
    """Sellado 22: el flag se enciende en la tarea de integracion (2.4) —
    resuelve_modo YA NO degrada live->shadow. Regla 9: con el flag en False
    (el mundo pre-2.4) este assert revienta."""
    from app.optimizer import goals as g

    assert g.HAY_MODULO_APPLY is True
    assert g.resuelve_modo("live", "live") == g.ModoEfectivo(modo="live", nota=None)
    assert g.resuelve_modo("shadow", "live") == g.ModoEfectivo(modo="shadow", nota=None)


# ---------------------------------------------------------------------------
# 5. Guard status='running' en el cierre: doble cierre no pisa
# ---------------------------------------------------------------------------


@_skip_db
def test_guard_running_segundo_cierre_no_pisa(caplog):
    with _db_temporal("orbit_cyc_guard") as (conn, _c):
        ciclo_id = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform)"
            " VALUES ('ads_optimizer', 'shadow', 'amazon_us') RETURNING id"
        ).fetchone()[0]

        with caplog.at_level(logging.WARNING, logger="app.cycle"):
            ok1 = ciclo._cierra_envelope(conn, ciclo_id, "done", 0, '{"a": 1}')
            ok2 = ciclo._cierra_envelope(conn, ciclo_id, "failed", 0, '{"a": 2}')

        assert ok1 is True
        assert ok2 is False, "0 filas: alguien ya cerro — warning y NO pisar"
        fila = conn.execute(
            "SELECT status, notes, finished_at IS NOT NULL FROM optimizer_cycle WHERE id = %s",
            (ciclo_id,),
        ).fetchone()
        assert fila == ("done", '{"a": 1}', True)
        assert "ya no esta 'running'" in caplog.text


# ---------------------------------------------------------------------------
# 6. E2E live: bids aplicados + cortes encolados + applied_count + notes
# ---------------------------------------------------------------------------


@_skip_db
def test_e2e_live_bids_aplicados_y_cortes_encolados(secrets_falsos):
    with _db_temporal("orbit_cyc_e2e") as (conn, _c):
        ids = _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        handler, vistos = _handler({"9201": "0.75"})
        res = _corre(conn, factory=_fabrica_real_mock(handler))

        assert res.status == "done"
        # El envelope del ciclo EJECUTOR: mode live, 1 bid aplicado
        env = conn.execute(
            "SELECT mode, applied_count, status FROM optimizer_cycle WHERE id = %s",
            (res.cycle_id,),
        ).fetchone()
        assert env == ("live", 1, "done")
        # UNA mutacion + UN readback (token, /v2/profiles y /list no son
        # mutaciones); el PUT viaja en el contenedor con bid NUMERO (probe 2.5)
        assert [json.loads(p.content) for p in _puts(vistos)] == [
            {"keywords": [{"keywordId": "9201", "bid": 0.75}]}
        ]
        assert (
            conn.execute("SELECT count(*) FROM apply_attempt WHERE resultado = 'ok'").fetchone()[0]
            == 1
        )
        # Cortes del ciclo encolados LIVE (pause + negative + harvest; el
        # orden por id sigue al de los terminos, que va por search_term)
        cola = sorted(conn.execute("SELECT kind, modo, estado FROM apply_queue").fetchall())
        assert cola == [
            ("harvest", "live", "pending_veto"),
            ("negative", "live", "pending_veto"),
            ("pause", "live", "pending_veto"),
        ]
        # Notes con los contadores del apply (vocabulario cerrado)
        notas = json.loads(res.notes)
        assert notas["apply"]["bids_aplicados"] == 1
        assert notas["apply"]["bids_descartados"] == 0
        assert notas["apply"]["cortes_encolados"]["live"] == 3
        assert notas["apply"]["cortes_liberados"]["liberadas"] == 0
        # Cache con lo LEIDO y resumen sellado al ciclo ejecutor
        assert conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s",
            (ids["kw_bid"],),
        ).fetchone()[0] == Decimal("0.75")
        dec = conn.execute(
            "SELECT id FROM decision WHERE cycle_id = %s AND kind = 'bid'", (res.cycle_id,)
        ).fetchone()[0]
        assert (
            conn.execute(
                "SELECT applied_cycle_id FROM decision_application WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            == res.cycle_id
        )


# ---------------------------------------------------------------------------
# 7. Envelope live + goal shadow (el residual de cycle.py)
# ---------------------------------------------------------------------------


@_skip_db
def test_envelope_live_goal_shadow_no_aplica_ni_encola_live(secrets_falsos):
    with _db_temporal("orbit_cyc_resid") as (conn, _c):
        ids = _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        conn.execute("UPDATE ads_optimizer_goal SET mode = 'shadow'")  # goal shadow, envelope live
        handler, vistos = _handler({"9201": "0.75"})
        res = _corre(conn, factory=_fabrica_real_mock(handler))

        assert res.status == "done"
        # La decision SIGUE existiendo, congelada con el modo del ENVELOPE
        dec = conn.execute(
            "SELECT inputs FROM decision WHERE cycle_id = %s AND kind = 'bid'", (res.cycle_id,)
        ).fetchone()
        assert dec[0]["modo"] == "live"
        # CERO HTTP de ese goal: el unico request es el GET de perfiles
        assert _mutaciones(vistos) == []
        assert [r.url.path for r in vistos] == ["/v2/profiles"]
        assert conn.execute("SELECT count(*) FROM apply_attempt", ()).fetchone()[0] == 0
        # encola_cortes lo marca SHADOW (modo efectivo por decision)
        assert conn.execute("SELECT DISTINCT modo FROM apply_queue").fetchone()[0] == "shadow"
        notas = json.loads(res.notes)
        assert notas["apply"]["cortes_encolados"] == {"live": 0, "shadow": 3, "choques": 0}
        assert notas["apply"]["bids_aplicados"] == 0
        assert conn.execute(
            "SELECT current_bid FROM ad_entity_state WHERE ad_entity_id = %s",
            (ids["kw_bid"],),
        ).fetchone()[0] == Decimal("1.00"), "el cache no se toca: el bid nunca salio"


# ---------------------------------------------------------------------------
# 8. Skip veto_pendiente por clave de efecto (sellado 5)
# ---------------------------------------------------------------------------


@_skip_db
def test_skip_veto_pendiente_por_clave_de_efecto():
    with _db_temporal("orbit_cyc_veto") as (conn, _c):
        ids = _siembra_maestra(conn)  # shadow: el bloqueo es por clave, no por modo
        # Filas EN VUELO de un ciclo anterior: pause de kw_pause (entity_cut)
        # y negative del termino (term_cut). El ciclo NO debe re-decidirlas.
        ciclo_viejo = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform)"
            " VALUES ('ads_optimizer', 'live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        config_id = conn.execute("SELECT id FROM config_version LIMIT 1").fetchone()[0]

        def _decision_vieja(entidad: int, kind: str, term: str | None) -> int:
            dec = DECIDED_AT - dt.timedelta(days=3)
            # madurez del esquema: window_end <= decided_at - 10d
            return conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
                " config_version_id, data_observed_at, window_start, window_end, search_term,"
                " inputs) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '{}') RETURNING id",
                (
                    ciclo_viejo,
                    entidad,
                    kind,
                    dec,
                    config_id,
                    dec,
                    dec.date() - dt.timedelta(days=60),
                    dec.date() - dt.timedelta(days=30),
                    term,
                ),
            ).fetchone()[0]

        for entidad, kind, term in (
            (ids["kw_pause"], "pause", None),
            (ids["ag"], "negative", "tortugas ninja calzas"),
        ):
            conn.execute(
                "INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term,"
                " decision_id, modo, estado, vence_el, request_payload)"
                " VALUES ('amazon_us', %s, %s, %s, %s, 'live', 'pending_veto', %s, '{}')",
                (entidad, kind, term, _decision_vieja(entidad, kind, term), DECIDED_AT),
            )

        res = _corre(conn)

        assert res.status == "done"
        notas = json.loads(res.notes)
        assert notas["skips"]["entidad"].get("veto_pendiente") == 1
        assert notas["skips"]["termino"].get("veto_pendiente") == 1
        # La clave bloqueada NO se re-decide; las distintas avanzan
        decisiones = conn.execute(
            "SELECT kind, search_term FROM decision WHERE cycle_id = %s ORDER BY id",
            (res.cycle_id,),
        ).fetchall()
        assert ("pause", None) not in decisiones
        assert ("negative", "tortugas ninja calzas") not in decisiones
        assert [(k, t) for k, t in decisiones] == [
            ("bid", None),
            ("harvest", "buena yarda"),
        ]
        # Solo el corte de la clave LIBRE llego a la cola
        assert conn.execute("SELECT count(*) FROM apply_queue").fetchone()[0] == 3
        assert notas["apply"]["cortes_encolados"]["shadow"] == 1


# ---------------------------------------------------------------------------
# 9. Sin perfil aceptado -> fase aborta fail-closed, el ciclo sigue
# ---------------------------------------------------------------------------


@_skip_db
def test_sin_perfil_aceptado_aborta_fail_closed(secrets_falsos):
    with _db_temporal("orbit_cyc_noperf") as (conn, _c):
        _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        handler, vistos = _handler(perfiles="MX")  # ningun perfil aceptado para amazon_us
        res = _corre(conn, factory=_fabrica_real_mock(handler))

        assert res.status == "degraded"
        notas = json.loads(res.notes)
        assert "perfil" in notas["apply"]["apply_error"]
        # CERO HTTP de mutacion: solo el GET /v2/profiles de la fabrica
        assert _mutaciones(vistos) == []
        assert [r.url.path for r in vistos] == ["/v2/profiles"]
        # El ciclo SIGUE: decisiones ya commitadas y cortes encolados (live)
        assert (
            conn.execute(
                "SELECT count(*) FROM decision WHERE cycle_id = %s", (res.cycle_id,)
            ).fetchone()[0]
            == 4
        )
        assert (
            conn.execute("SELECT count(*) FROM apply_queue WHERE modo = 'live'").fetchone()[0] == 3
        )
        assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0] == 0
        # El lock tambien se libera en el camino degradado
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()[0]
            == 0
        )


# ---------------------------------------------------------------------------
# ADV-01 (P0, review adversaria de phase 2): la fase de apply CIERRA sus
# transacciones — TX4 real y sello persisten con la conexion de PRODUCCION
# ---------------------------------------------------------------------------


@_skip_db
def test_ciclo_shadow_persisten_cola_y_sello_con_conexion_sin_autocommit():
    """Conexion COMO PRODUCCION (app.db.connect: sin autocommit; app.cli:
    conn.close() en el finally SIN commit). TODO lo escrito por la fase de
    apply (filas de apply_queue + notes['apply']) debe seguir ahi visto desde
    OTRA conexion tras el close. Regla 9: contra el codigo que deja TX4 y el
    sello en una transaccion abierta que el close revierte, la cola shadow
    queda en 0 filas y el envelope sin notes['apply'] — este test revienta
    (hallazgo ADV-01: la cola shadow de produccion quedaba SIEMPRE vacia)."""
    with _db_temporal("orbit_cyc_tx4") as (setup, conectar):
        _siembra_maestra(setup)  # escalera shadow: el ciclo decide 3 cortes
        conn = conectar(autocommit=False)  # como app.db.connect + app.cli
        conn.execute("SET TIME ZONE 'UTC'")
        res = ciclo.corre_ciclo(
            conn,
            platform="amazon_us",
            owner="prod:tx4",
            decided_at=DECIDED_AT,
            heartbeat_cada=1,
        )
        assert res.status == "done"
        conn.close()  # el finally del CLI: SIN commit

        ver = conectar()
        filas = ver.execute("SELECT estado, count(*) FROM apply_queue GROUP BY estado").fetchall()
        env = ver.execute(
            "SELECT status, notes FROM optimizer_cycle WHERE id = %s", (res.cycle_id,)
        ).fetchone()
        ver.close()

        assert filas == [("pending_veto", 3)], (
            "la cola shadow persiste tras el close del CLI (dueno puede vetar)"
        )
        assert env[0] == "done"
        assert '"apply"' in (env[1] or ""), "el sello notes['apply'] persiste, no solo en memoria"
        notas = json.loads(env[1])
        assert notas["apply"]["cortes_encolados"]["shadow"] == 3


@_skip_db
def test_live_fabrica_aborta_senal_degraded_persistida():
    """Ciclo LIVE con la fabrica abortando (p.ej. secrets ilegibles): la senal
    fail-closed (status 'degraded' + nota apply_error) debe quedar PERSISTIDA
    — Salud no puede mostrar ciclos 'done' sanos mientras el motor no aplica.
    Regla 9: contra el codigo sin commits de la fase, el envelope persiste
    'done' SIN apply_error (la senal se revierte en el close) y este test
    revienta (hallazgo ADV-01c)."""
    with _db_temporal("orbit_cyc_tx4ab") as (setup, conectar):
        _siembra_maestra(setup, escalera="live")
        _config_live_caps(setup)

        def fabrica_que_aborta(*_args, **_kwargs):
            raise ciclo.SinPerfilAplicar("secrets ilegibles (simulado)")

        conn = conectar(autocommit=False)  # como app.db.connect + app.cli
        conn.execute("SET TIME ZONE 'UTC'")
        res = _corre(conn, factory=fabrica_que_aborta)
        assert res.status == "degraded"  # la senal in-memory
        conn.close()  # el finally del CLI: SIN commit

        ver = conectar()
        env = ver.execute(
            "SELECT status, notes FROM optimizer_cycle WHERE id = %s", (res.cycle_id,)
        ).fetchone()
        cola = ver.execute(
            "SELECT count(*) FROM apply_queue WHERE modo = 'live' AND estado = 'pending_veto'"
        ).fetchone()[0]
        ver.close()

        assert env[0] == "degraded", "la degradacion persiste: jamas 'done' silencioso"
        notas = json.loads(env[1])
        assert "apply_error" in notas["apply"], "la nota apply_error persiste"
        assert cola == 3, "el encolado live tambien persiste"


# ---------------------------------------------------------------------------
# ADV-09 (P2): el sello de falla NO pisa el rastro del sucesor
# ---------------------------------------------------------------------------


@_skip_db
def test_sello_fallido_no_pisa_el_rastro_del_sucesor(caplog):
    """Un zombie que despierta y revienta NO puede sobreescribir el envelope
    que el RASTRO del sucesor ya cerro 'failed' con su nota (mismo guard
    status='running' que _cierra_envelope y _sella_apply). Regla 9: sin el
    guard, el UPDATE pisa notes con el cuerpo parcial del zombie y la nota
    'rastro' (unico indicio de que hubo dos procesos) desaparece."""
    with _db_temporal("orbit_cyc_sello") as (conn, _c):
        ciclo_id = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform)"
            " VALUES ('ads_optimizer', 'shadow', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        # El sucesor gano el claim tras el TTL y cerro el rastro del zombie.
        conn.execute(
            "UPDATE optimizer_cycle SET status = 'failed', finished_at = now(), notes ="
            " 'rastro: ciclo muerto (lock expirado, reclamado por sucesor:9)'"
            " WHERE id = %s",
            (ciclo_id,),
        )

        with caplog.at_level(logging.WARNING, logger="app.cycle"):
            ciclo._sello_fallido(
                conn, ciclo_id, RuntimeError("boom del zombie"), ciclo._Contadores(), []
            )

        fila = conn.execute(
            "SELECT status, notes FROM optimizer_cycle WHERE id = %s", (ciclo_id,)
        ).fetchone()
        assert fila[0] == "failed"
        assert "rastro: ciclo muerto" in fila[1], "el sello del zombie NO pisa el rastro"
        assert "boom" not in fila[1]
