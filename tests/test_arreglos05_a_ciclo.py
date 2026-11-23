"""ARREGLOS 05 A: el ciclo completa sus decisiones sin duplicarlas.

Regresiones del bloque A de `plans/arreglos-05.md` (seccion A de DISENO). Cada
prueba corre el ciclo real sobre una base desechable con TODAS las migraciones
canonicas y la API de Ads simulada con `httpx.MockTransport`: cero HTTP vivo y
secretos siempre falsos. Las semillas son las de `test_cycle` y
`test_cycle_apply`.

Mapa contra las reproducciones legadas de
`docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/test_revision_ciclo.py`:
F1 -> 5xx aislado, F2 -> degradado recuperable, F3 -> lock expirado con running,
F4 -> concilia antes de decidir. Las demas cubren el contrato que el legado no
tenia: lote vacio comprometido, ejecutor tras una caida, dos workers, cambio de
dia y la migracion 0094.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from test_cycle import DECIDED_AT, JOB_KEY
from test_cycle_apply import (
    OWNER,
    PERFIL_US,
    _agrega_kw2,
    _config_live_caps,
    _fabrica_real_mock,
    _handler,
    _puts,
    _siembra_maestra,
    secrets_falsos,  # noqa: F401 - fixture de la fabrica real del ciclo (credenciales falsas)
)
from test_schema import _postgres_obligatorio_ausente

from app import cycle as ciclo
from tests.helpers.pg import db_desde_template

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
VENCIDA = "vigencia_vencida"
DIA = dt.timedelta(days=1)
KW_BID, KW_PAUSA, KW2 = "9201", "9202", "9203"
REMOTO_INICIAL = {"bids": {KW_BID: "1.00", KW2: "1.00"}, "estados": {KW_PAUSA: "ENABLED"}}


class _Muerte(BaseException):
    """El proceso muere: nada del ciclo alcanza a manejarlo ni a sellar la fase."""


@contextmanager
def _db(prefijo: str):
    """Base desechable con la cadena canonica. `extra()` abre otra conexion a
    la misma base: es el observador de una segunda sesion y la conexion de un
    segundo worker."""
    with db_desde_template(prefijo, autocommit=True) as (conn, dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        yield conn, lambda: psycopg.connect(dsn, autocommit=True)


def _corre(conn, *, factory=None, owner=OWNER, ahora=DECIDED_AT):
    kwargs = {"aplicador_factory": factory} if factory is not None else {}
    return ciclo.corre_ciclo(
        conn, platform="amazon_us", owner=owner, decided_at=ahora, heartbeat_cada=1, **kwargs
    )


def _atiende(conn, *, factory=None, owner=OWNER, ahora=DECIDED_AT, forzar=False):
    kwargs = {"aplicador_factory": factory} if factory is not None else {}
    return ciclo.atender_plataforma(
        conn,
        "amazon_us",
        owner=owner,
        ahora=ahora,
        heartbeat_cada=1,
        forzar=forzar,
        **kwargs,
    )


def _directa(amazon, *, con_guard=True):
    """Fabrica que construye el Aplicador contra la API simulada, sin resolver
    perfil: para las pruebas donde el perfil no es el sujeto. Con `con_guard`
    False el aplicador no recibe el guard de ownership."""

    def fabrica(conn, *, platform, cycle_id_ejecutor, owner, job_key, tick, transport=None):
        from test_cycle_apply import (
            FAKE_CLIENT_ID,
            FAKE_CLIENT_SECRET,
            FAKE_PROFILE_US,
            FAKE_REFRESH_TOKEN,
        )

        from app.ads.config import AdsCredentials
        from app.apply import Aplicador

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
            tick=tick if con_guard else None,
            guard_http=tick if con_guard else None,
            transport=httpx.MockTransport(amazon),
            sleep=lambda seconds: None,
        )

    return fabrica


class _Amazon:
    """API de Ads simulada. `bids` y `estados` son lo que Amazon tiene por id
    externo; cada PUT de puja o de estado lo actualiza. `vistos` registra cada
    request salvo el token LWA. `rechaza_bids` responde 400 (rechazo definitivo)
    a toda puja; `falla_primer_put` responde 500 a la primera, aplicándola solo
    con `aplica_al_fallar` (el caso que la conciliación existe para resolver)."""

    def __init__(
        self,
        *,
        bids=None,
        estados=None,
        rechaza_bids=False,
        falla_primer_put=False,
        aplica_al_fallar=False,
        rechaza_identidad: int | None = None,
        rechaza_lectura: str | None = None,
    ):
        self.bids: dict[str, str] = dict(bids or {})
        self.estados: dict[str, str] = dict(estados or {})
        self.vistos: list[httpx.Request] = []
        self.puts_bid: list[dict] = []
        self.puts_estado: list[dict] = []
        self.rechaza_bids = rechaza_bids
        self.falla_primer_put = falla_primer_put
        self.aplica_al_fallar = aplica_al_fallar
        self.rechaza_identidad = rechaza_identidad
        self.al_rechazo_identidad = None
        self.rechaza_lectura = rechaza_lectura
        self.al_put_bid = None
        self.al_list = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access-1", "expires_in": 3600})
        self.vistos.append(request)
        cuerpo = json.loads(request.content) if request.content else {}
        if request.method == "GET" and request.url.path == "/v2/profiles":
            return httpx.Response(200, json={"profiles": [PERFIL_US]})
        if request.method == "POST" and request.url.path == "/sp/keywords/list":
            if self.rechaza_lectura == "antes" or (
                self.rechaza_lectura == "despues" and self.puts_bid
            ):
                # 403 en una lectura: Amazon rechaza la identidad o los
                # permisos de toda la corrida.
                self.vistos.append(request)
                return httpx.Response(403, json={"code": "ACCESS_DENIED"})
            if self.al_list is not None and self.puts_bid:
                self.al_list(request)
            filas = [
                {
                    "keywordId": ext,
                    "bid": self.bids.get(ext),
                    "state": self.estados.get(ext, "ENABLED"),
                }
                for ext in sorted(set(self.bids) | set(self.estados))
            ]
            return httpx.Response(200, json={"keywords": filas})
        if request.method == "PUT" and request.url.path == "/sp/keywords":
            obj = cuerpo["keywords"][0]
            ext = str(obj["keywordId"])
            if self.rechaza_identidad is not None:
                # 401/403: Amazon rechaza la identidad o los permisos de toda
                # la corrida. No aplica nada; el envio queda registrado.
                (self.puts_bid if "bid" in obj else self.puts_estado).append(obj)
                if self.al_rechazo_identidad is not None:
                    self.al_rechazo_identidad()
                return httpx.Response(self.rechaza_identidad, json={"code": "ACCESS_DENIED"})
            if "bid" in obj:
                self.puts_bid.append(obj)
                if self.al_put_bid is not None:
                    self.al_put_bid(obj)
                if self.falla_primer_put and len(self.puts_bid) == 1:
                    if self.aplica_al_fallar:
                        self.bids[ext] = obj["bid"]
                    return httpx.Response(500, json={"code": "INTERNAL_ERROR"})
                if self.rechaza_bids:
                    return httpx.Response(400, json={"code": "INVALID_ARGUMENT"})
                self.bids[ext] = obj["bid"]
            else:
                self.puts_estado.append(obj)
                self.estados[ext] = obj["state"]
            return httpx.Response(207, json={"keywords": {"success": [obj]}})
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    def pujas_enviadas(self) -> list[str]:
        return [str(obj["keywordId"]) for obj in self.puts_bid]


def _semilla_live(conn) -> dict:
    ids = _siembra_maestra(conn, escalera="live")
    _config_live_caps(conn)
    return ids


def _sello(conn, cycle_id: int):
    """(sello de TX3, origen referido, status, decisions_count, mode) leidos sin
    nombrar las columnas nuevas: en un esquema sin la 0094 devuelven None."""
    return conn.execute(
        "SELECT to_jsonb(c) ->> 'decision_committed_at', to_jsonb(c) ->> 'resume_of',"
        " status, decisions_count, mode FROM optimizer_cycle c WHERE id = %s",
        (cycle_id,),
    ).fetchone()


def _decision(conn, cycle_id: int, kind: str, external: str | None = None) -> int:
    fila = conn.execute(
        "SELECT d.id FROM decision d JOIN ad_entity e ON e.id = d.ad_entity_id"
        " WHERE d.cycle_id = %s AND d.kind = %s"
        " AND (%s::text IS NULL OR e.external_id = %s) ORDER BY d.id LIMIT 1",
        (cycle_id, kind, external, external),
    ).fetchone()
    assert fila is not None, (
        f"el ciclo {cycle_id} no decidio {kind} de {external or 'cualquier hoja'}"
    )
    return fila[0]


def _desenlace(conn, decision_id: int) -> str | None:
    """aplicada:<verify_ok>, la lista de motivos no aplicados, o None si todavia
    no tiene desenlace."""
    aplicada = conn.execute(
        "SELECT verify_ok FROM decision_application WHERE decision_id = %s", (decision_id,)
    ).fetchone()
    if aplicada is not None:
        return f"aplicada:{aplicada[0]}"
    return conn.execute(
        "SELECT string_agg(motivo, ',' ORDER BY cycle_id) FROM decision_sin_aplicar"
        " WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()[0]


def _aplicadas_del_ciclo(conn, cycle_id: int) -> set[str]:
    return {
        fila[0]
        for fila in conn.execute(
            "SELECT e.external_id FROM decision_application da"
            " JOIN decision d ON d.id = da.decision_id JOIN ad_entity e ON e.id = d.ad_entity_id"
            " WHERE d.cycle_id = %s AND da.verify_ok IS TRUE",
            (cycle_id,),
        ).fetchall()
    }


def _muere_en_apply(monkeypatch) -> None:
    def muere(*_args, **_kwargs):
        raise _Muerte("el proceso murio antes de la fase de apply")

    monkeypatch.setattr(ciclo, "_fase_apply", muere)


@_skip_db
def test_arreglos05_a_5xx_en_el_primer_bid_no_frena_el_segundo():
    """F1 (pujas): Amazon responde 500 a la PRIMERA puja del lote. La segunda se
    intenta y se aplica, y la primera queda incierta: su fila del ledger queda
    sin sello (el rastro de siempre) y la decision sin desenlace, porque solo una
    lectura puede resolverla, nunca otro PUT. El ciclo
    siguiente la resuelve leyendo: Amazon no la aplico, asi que queda perdida y
    la decision conserva un unico intento."""
    with _db("orbit_a05_5xx") as (conn, _extra):
        ids = _semilla_live(conn)
        _agrega_kw2(conn, ids)
        amazon = _Amazon(**REMOTO_INICIAL, falla_primer_put=True)
        res = _corre(conn, factory=_directa(amazon))

        assert "apply_error" not in json.loads(res.notes).get("apply", {}), (
            "un 500 de una puja ya no aborta la fase de apply"
        )
        assert len(amazon.puts_bid) == 2, "las dos pujas del lote se intentan"
        assert res.status == "done"
        fallida = amazon.pujas_enviadas()[0]
        assert _aplicadas_del_ciclo(conn, res.cycle_id) == {KW_BID, KW2} - {fallida}, (
            "la puja que viene despues del 500 se aplica; la incierta no se da por buena"
        )
        incierta = _decision(conn, res.cycle_id, "bid", external=fallida)
        assert _desenlace(conn, incierta) is None, "la puja incierta aun no tiene desenlace"
        assert conn.execute(
            "SELECT finished_at, resultado FROM apply_attempt WHERE decision_id = %s",
            (incierta,),
        ).fetchone() == (None, None), "el intento incierto queda sin sellar"

        res2 = _corre(conn, factory=_directa(amazon))

        # La conciliacion la resuelve leyendo: Amazon conservo el bid anterior,
        # asi que queda como divergencia de readback (no aplicada).
        assert _desenlace(conn, incierta) == "aplicada:False", (
            "la conciliacion la resuelve leyendo, sin reenviar"
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'",
                (incierta,),
            ).fetchone()[0]
            == 1
        ), "la misma decision conserva un unico intento"
        assert res2.cycle_id != res.cycle_id and res2.status == "done"


@_skip_db
def test_arreglos05_a_5xx_en_un_bid_no_frena_la_liberacion_de_cortes():
    """F1 (cola): un ciclo anterior encolo una pausa y su veto de 48 h ya
    vencio. Amazon responde 500 a la unica puja elegible del ciclo. La cola de
    cortes corre igual: la pausa vencida sale de `pending_veto`. Antes el 500
    abortaba la fase entera y la pausa esperaba al dia siguiente."""
    with _db("orbit_a05_5xxcola") as (conn, _extra):
        _semilla_live(conn)
        anterior = _Amazon(**REMOTO_INICIAL, rechaza_bids=True)
        res0 = _corre(
            conn, factory=_directa(anterior), ahora=DECIDED_AT - 26 * dt.timedelta(hours=1)
        )
        pausa = conn.execute(
            "SELECT q.id FROM apply_queue q JOIN decision d ON d.id = q.decision_id"
            " WHERE d.cycle_id = %s AND q.kind = 'pause'",
            (res0.cycle_id,),
        ).fetchone()
        assert pausa is not None, "el ciclo anterior encolo la pausa"
        # Los terminos quedan vetados: esta prueba solo necesita la pausa.
        conn.execute(
            "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(), vetoed_by = 'test',"
            " vence_el = now() + interval '30 days' WHERE kind <> 'pause'"
        )
        hoy = DECIDED_AT + DIA
        assert conn.execute(
            "SELECT vence_el <= %s AND estado = 'pending_veto' FROM apply_queue WHERE id = %s",
            (hoy, pausa[0]),
        ).fetchone() == (True,), "el veto de la pausa ya vencio al correr este ciclo"

        amazon = _Amazon(**REMOTO_INICIAL, falla_primer_put=True)
        res = _corre(conn, factory=_directa(amazon), ahora=hoy)

        notas = json.loads(res.notes)
        assert amazon.pujas_enviadas() == [KW_BID], "la unica puja elegible se intento"
        assert "apply_error" not in notas.get("apply", {}), (
            "un 500 de una puja ya no aborta la fase de apply"
        )
        assert res.status == "done"
        liberados = notas["apply"].get("cortes_liberados", {})
        assert liberados.get("liberadas") == 1, "la cola de cortes corre despues del 500"
        assert _estado_cola(conn, pausa[0]) == "applied"
        assert amazon.estados[KW_PAUSA] == "PAUSED", "la pausa vencida se aplica"
        assert liberados.get("aplicadas") == 1 and liberados.get("fallidas") == 0


def _estado_cola(conn, queue_id: int) -> str:
    return conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (queue_id,)).fetchone()[0]


@_skip_db
@pytest.mark.usefixtures("secrets_falsos")
def test_arreglos05_a_degraded_por_apply_abre_un_ejecutor_en_el_siguiente_horario():
    """F2: la fabrica del aplicador no encuentra perfil aceptado. El ciclo queda
    degradado con sus decisiones intactas y el siguiente horario ABRE un
    ejecutor ligado a ese origen: no vuelve a decidir, aplica lo pendiente y su
    resumen muestra el origen."""
    with _db("orbit_a05_degr") as (conn, _extra):
        _semilla_live(conn)
        sin_perfil, _vistos = _handler(perfiles="MX")
        res1 = _corre(conn, factory=_fabrica_real_mock(sin_perfil))

        assert res1.status == "degraded" and res1.decisions_count == 4
        origen = res1.cycle_id
        assert conn.execute("SELECT count(*) FROM decision_application").fetchone()[0] == 0, (
            "sin perfil no se aplico nada",
        )
        assert ciclo.motivo_de_salto(conn, "amazon_us", hoy=DECIDED_AT.date()) is None, (
            "el siguiente horario debe poder completar lo pendiente"
        )

        handler, vistos = _handler({KW_BID: "1.00"})
        res2 = _atiende(conn, factory=_fabrica_real_mock(handler))

        assert res2.ciclo is not None, "el horario siguiente abre un ejecutor"
        ejecutor = res2.ciclo
        assert ejecutor.resume_of == origen
        assert _sello(conn, ejecutor.cycle_id)[1:] == (str(origen), "done", 0, "live"), (
            "el ejecutor referencia directo al origen, no decide y termina"
        )
        assert conn.execute("SELECT count(*) FROM decision").fetchone()[0] == 4, (
            "cero decisiones duplicadas: el ejecutor no vuelve a decidir",
        )
        assert len(_puts(vistos)) == 1, "aplica la puja pendiente una sola vez"
        puja = _decision(conn, origen, "bid", external=KW_BID)
        assert conn.execute(
            "SELECT applied_cycle_id FROM decision_application WHERE decision_id = %s", (puja,)
        ).fetchone() == (ejecutor.cycle_id,), "applied_cycle_id conserva el ejecutor"
        notas = json.loads(ejecutor.notes)
        assert notas["resume_of"] == origen
        assert notas["apply"]["bids_aplicados"] == 1


@_skip_db
def test_arreglos05_a_lock_expirado_con_running_reciente_no_bloquea_el_horario():
    """F3 y caida ANTES de TX3: el proceso de las 08:40 murio y dejo el envelope
    `running`. Su lock ya vencio. El horario siguiente corre y decide de nuevo
    (origen nuevo: el muerto nunca comprometio decisiones) y el huerfano queda
    con el rastro. La edad de 3 h ya no decide nada; manda el claim."""
    with _db("orbit_a05_huerf") as (conn, _extra):
        _semilla_live(conn)
        muerto = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform, started_at, status)"
            " VALUES ('ads_optimizer', 'live', 'amazon_us', now() - interval '2 hours',"
            " 'running') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_optimizer_lock (job_key, owner, claimed_at, heartbeat_at)"
            " VALUES (%s, 'muerto:1', now() - interval '2 hours', now() - interval '2 hours')",
            (JOB_KEY,),
        )
        assert ciclo.motivo_de_salto(conn, "amazon_us", hoy=DECIDED_AT.date()) is None, (
            "un running huerfano con lock vencido no bloquea el horario"
        )

        amazon = _Amazon(**REMOTO_INICIAL)
        res = _atiende(conn, factory=_directa(amazon))

        assert res.ciclo is not None and res.ciclo.status == "done"
        assert res.ciclo.resume_of is None, "es un origen nuevo, no un ejecutor"
        assert res.ciclo.decisions_count == 4
        rastro = conn.execute(
            "SELECT status, notes FROM optimizer_cycle WHERE id = %s", (muerto,)
        ).fetchone()
        assert rastro[0] == "failed" and "rastro" in rastro[1], (
            "el huerfano queda cerrado con el rastro del claim que lo reemplazo"
        )
        assert _sello(conn, muerto)[0] is None, "el muerto nunca comprometio TX3"
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()[0]
            == 0
        ), "al terminar, el lock queda libre"


@_skip_db
def test_arreglos05_a_caida_despues_de_tx3_abre_ejecutor_sin_decidir_de_nuevo(monkeypatch):
    """Caida DESPUES de TX3: las decisiones ya estan comprometidas pero nada se
    aplico ni se encolo. El horario siguiente no abre otra ronda de decisiones:
    abre un ejecutor del origen y completa lo pendiente."""
    with _db("orbit_a05_postx3") as (conn, _extra):
        _semilla_live(conn)
        _muere_en_apply(monkeypatch)
        with pytest.raises(_Muerte):
            _corre(conn)
        monkeypatch.undo()
        origen = conn.execute("SELECT max(id) FROM optimizer_cycle").fetchone()[0]
        assert conn.execute(
            "SELECT status, decisions_count FROM optimizer_cycle WHERE id = %s", (origen,)
        ).fetchone() == ("done", 4)
        assert _sello(conn, origen)[0] is not None, "TX3 quedo comprometida"
        assert conn.execute("SELECT count(*) FROM apply_queue").fetchone()[0] == 0, (
            "la caida fue antes de encolar los cortes"
        )
        assert ciclo.motivo_de_salto(conn, "amazon_us", hoy=DECIDED_AT.date()) is None, (
            "quedan decisiones por completar: el horario siguiente corre"
        )

        amazon = _Amazon(**REMOTO_INICIAL)
        res = _atiende(conn, factory=_directa(amazon))

        assert res.ciclo is not None and res.ciclo.resume_of == origen
        assert conn.execute("SELECT count(*) FROM decision").fetchone()[0] == 4
        assert amazon.pujas_enviadas() == [KW_BID]
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_queue WHERE estado = 'pending_veto' AND modo = 'live'"
            ).fetchone()[0]
            == 3
        ), "el ejecutor encola los cortes que la caida dejo sin fila"


@_skip_db
def test_arreglos05_a_concilia_antes_de_decidir():
    """F4: Amazon APLICO una puja pero respondio 500. En el ciclo siguiente la
    conciliacion corre antes de decidir: la hoja ya no se salta como «bid en
    vuelo», se decide desde el bid real y la puja no se reenvia."""
    with _db("orbit_a05_recon") as (conn, _extra):
        _semilla_live(conn)
        amazon = _Amazon(**REMOTO_INICIAL, falla_primer_put=True, aplica_al_fallar=True)
        res1 = _corre(conn, factory=_directa(amazon))
        puja = _decision(conn, res1.cycle_id, "bid", external=KW_BID)

        res2 = _corre(conn, factory=_directa(amazon))

        notas = json.loads(res2.notes)
        confirmados = notas["apply"]["bids_reconciliados"]["confirmados"]
        en_vuelo = notas["skips"]["entidad"].get("bid_en_vuelo", 0)
        assert not (en_vuelo and confirmados), (
            "el mismo ciclo salta la hoja por 'bid en vuelo' y despues confirma ese bid"
        )
        assert confirmados == 1, "lo que Amazon ya aplico se confirma leyendo"
        assert en_vuelo == 0, "la hoja conciliada entra a la decision del ciclo"
        assert amazon.pujas_enviadas() == [KW_BID], "la puja incierta no se reenvia"
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'",
                (puja,),
            ).fetchone()[0]
            == 1
        ), "la misma decision tiene un solo intento"
        assert _desenlace(conn, puja) == "aplicada:True"
        assert conn.execute(
            "SELECT s.current_bid FROM ad_entity_state s"
            " JOIN decision d ON d.ad_entity_id = s.ad_entity_id WHERE d.id = %s",
            (puja,),
        ).fetchone() == (Decimal("0.88"),), "el cache queda con el bid que Amazon tiene"


@_skip_db
def test_arreglos05_a_lote_valido_vacio_deja_tx3_comprometida():
    """Un ciclo que no decide nada porque nada elegible queda (hojas en cooldown
    o con su clave en cola) NO es una caida: TX3 queda comprometida, se ve desde
    otra conexion y el dia no se vuelve a decidir."""
    with _db("orbit_a05_vacio") as (conn, extra):
        _semilla_live(conn)
        amazon = _Amazon(**REMOTO_INICIAL)
        _corre(conn, factory=_directa(amazon))

        res = _corre(conn, factory=_directa(amazon))

        assert res.status == "done" and res.decisions_count == 0, (
            "el segundo ciclo del dia no tiene nada elegible que decidir"
        )
        with extra() as otra:
            sello = _sello(otra, res.cycle_id)
        assert sello[0] is not None, "TX3 comprometida aunque el lote valido este vacio"
        assert sello[1] is None
        salto = ciclo.motivo_de_salto(conn, "amazon_us", hoy=DECIDED_AT.date())
        assert salto is not None and "ya hay un ciclo done hoy" in salto


@_skip_db
def test_arreglos05_a_owner_vencido_con_respuesta_retenerida_no_sella_ni_repite():
    """Dos workers. El primero pierde el lock MIENTRAS Amazon le retiene la
    respuesta de un PUT que si aplico. Ese owner vencido no sella ni manda nada
    nuevo; el sucesor concilia esa puja con una lectura (cero PUT repetido, cero
    cuota nueva) y aplica solo lo que nunca se intento. Nadie duplica decisiones."""
    with _db("orbit_a05_dos") as (conn, extra):
        ids = _semilla_live(conn)
        _agrega_kw2(conn, ids)
        llego, liberar = threading.Event(), threading.Event()

        def retener(_obj: dict) -> None:
            llego.set()
            assert liberar.wait(timeout=30), "el test nunca libero la respuesta"

        amazon = _Amazon(**REMOTO_INICIAL)
        amazon.al_put_bid = retener
        zombi: dict = {}

        def correr_zombi() -> None:
            conexion = extra()
            try:
                zombi["resultado"] = _corre(conexion, factory=_directa(amazon), owner="zombie:1")
            except BaseException as exc:  # noqa: BLE001 - el test inspecciona la excepcion
                zombi["error"] = exc
            finally:
                conexion.close()

        hilo = threading.Thread(target=correr_zombi, daemon=True)
        hilo.start()
        assert llego.wait(timeout=30), "el zombie nunca envio su PUT"
        conn.execute(
            "UPDATE ads_optimizer_lock SET owner = 'sucesor',"
            " claimed_at = now() - interval '31 minutes',"
            " heartbeat_at = now() - interval '31 minutes' WHERE job_key = %s",
            (JOB_KEY,),
        )
        liberar.set()
        hilo.join(timeout=60)
        assert not hilo.is_alive() and "error" not in zombi, zombi.get("error")

        res_z = zombi["resultado"]
        origen = res_z.cycle_id
        assert json.loads(res_z.notes)["apply"].get("apply_abortado_owner") is True
        assert len(amazon.puts_bid) == 1 and amazon.puts_estado == [], (
            "tras perder el lock el zombie no manda nada nuevo"
        )
        # El orden de cupo decide que hoja salio primero; la prueba no lo supone.
        en_vuelo = _decision(conn, origen, "bid", external=amazon.pujas_enviadas()[0])
        assert conn.execute(
            "SELECT finished_at, resultado FROM apply_attempt WHERE decision_id = %s", (en_vuelo,)
        ).fetchone() == (None, None), "el owner vencido no sella el intento"
        assert _desenlace(conn, en_vuelo) is None
        assert ciclo.motivo_de_salto(conn, "amazon_us", hoy=DECIDED_AT.date()) is None, (
            "quedan decisiones del origen por completar"
        )

        res_s = _atiende(conn, factory=_directa(amazon), owner="sucesor")

        assert res_s.ciclo is not None and res_s.ciclo.resume_of == origen
        notas = json.loads(res_s.ciclo.notes)
        assert notas["apply"]["bids_reconciliados"] == {"confirmados": 1, "fallidos": 0}
        assert amazon.pujas_enviadas() == [KW_BID, KW2] or amazon.pujas_enviadas() == [
            KW2,
            KW_BID,
        ], "el sucesor solo manda lo que nunca se intento: la puja incierta se concilio leyendo"
        assert conn.execute("SELECT count(*) FROM decision").fetchone()[0] == 5
        assert conn.execute(
            "SELECT applied_cycle_id FROM decision_application WHERE decision_id = %s",
            (en_vuelo,),
        ).fetchone() == (res_s.ciclo.cycle_id,)
        assert (
            conn.execute(
                "SELECT used FROM apply_quota_state WHERE motor = 'ads_optimizer:amazon_us:bid'"
            ).fetchone()[0]
            == 2
        ), "la recuperacion no abre un segundo cupo"


@_skip_db
def test_arreglos05_a_al_dia_siguiente_concilia_y_vence_lo_no_intentado(monkeypatch):
    """Cambio de dia. El intento ambiguo de ayer se concilia leyendo. Lo que
    nunca se intento deja de ser elegible con un motivo auditable, sin borrar
    decisiones, y el ciclo nuevo decide con datos frescos. La historia sin sello
    de la migracion no se toca."""
    with _db("orbit_a05_dia") as (conn, _extra):
        _semilla_live(conn)
        _muere_en_apply(monkeypatch)
        with pytest.raises(_Muerte):
            _corre(conn)
        monkeypatch.undo()
        origen = conn.execute("SELECT max(id) FROM optimizer_cycle").fetchone()[0]
        # Una puja SI salio y Amazon la aplico, pero el proceso murio antes del
        # sello: es el intento ambiguo de ayer.
        salio = _decision(conn, origen, "bid", external=KW_BID)
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 1, 'normal', %s::jsonb, true)",
            (salio, json.dumps({"keywordId": KW_BID, "bid": "0.88"})),
        )
        sin_intento = [_decision(conn, origen, kind) for kind in ("pause", "negative", "harvest")]
        # Historia sin sello: ciclo cerrado antes de la migracion, con una
        # decision sin desenlace ni intento.
        config = conn.execute("SELECT id FROM config_version ORDER BY id DESC LIMIT 1").fetchone()
        viejo = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform, started_at, finished_at,"
            " decisions_count, status) VALUES ('ads_optimizer', 'live', 'amazon_us',"
            " now() - interval '40 days', now() - interval '40 days', 1, 'done') RETURNING id"
        ).fetchone()[0]
        historica = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
            " data_observed_at, window_start, window_end, old_value, new_value, value_currency,"
            " inputs) SELECT %s, d.ad_entity_id, 'bid', now() - interval '40 days', %s,"
            " now() - interval '80 days', now() - interval '100 days', now() - interval '70 days',"
            " 1.00, 0.90, 'USD', '{}'::jsonb FROM decision d WHERE d.id = %s RETURNING id",
            (viejo, config[0], salio),
        ).fetchone()[0]

        amazon = _Amazon(bids={KW_BID: "0.88"}, estados={KW_PAUSA: "ENABLED"})
        res = _corre(conn, factory=_directa(amazon), ahora=DECIDED_AT + DIA)

        assert json.loads(res.notes)["apply"]["bids_reconciliados"]["confirmados"] == 1, (
            "el intento ambiguo de ayer se concilia leyendo"
        )
        assert _desenlace(conn, salio) == "aplicada:True"
        vencidas = conn.execute(
            "SELECT s.decision_id, s.cycle_id, s.detalle ->> 'origen' FROM decision_sin_aplicar s"
            " WHERE s.motivo = %s ORDER BY s.decision_id",
            (VENCIDA,),
        ).fetchall()
        assert sorted(fila[0] for fila in vencidas) == sorted(sin_intento), (
            "solo lo que nunca se intento vence, con motivo auditable"
        )
        assert {fila[1] for fila in vencidas} == {res.cycle_id}
        assert {fila[2] for fila in vencidas} == {str(origen)}
        assert _desenlace(conn, historica) is None, "la historia sin sello no se reclasifica"
        assert (
            conn.execute("SELECT count(*) FROM decision WHERE cycle_id = %s", (origen,)).fetchone()[
                0
            ]
            == 4
        ), "no se borra ninguna decision"
        assert res.decisions_count > 0, "el ciclo nuevo decide con datos frescos"
        assert (
            conn.execute(
                "SELECT count(*) FROM v_decision_huerfana WHERE cycle_id = %s", (origen,)
            ).fetchone()[0]
            == 0
        )


def _migracion(nombre: str) -> Path:
    return MIGRACIONES / nombre


def _sonda_de(ruta: Path) -> str:
    """El SELECT de la cabecera `-- sonda-aplicada:` que lee el instalador."""
    cabecera = next(
        cruda
        for cruda in ruta.read_text(encoding="utf-8").splitlines()[:10]
        if cruda.startswith("-- sonda-aplicada:")
    )
    return cabecera.split("-- sonda-aplicada:", 1)[1].strip().rstrip(";")


def _columnas_nuevas(conn) -> set[str]:
    return {
        fila[0]
        for fila in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'optimizer_cycle'"
            " AND column_name IN ('decision_committed_at', 'resume_of')"
        ).fetchall()
    }


@_skip_db
def test_arreglos05_a_migracion_sello_y_origen_ida_reversa_e_ida():
    """0094 con el rol real del motor: el sello nace solo en el cierre de TX3 y
    tiene una sola transicion; el origen del ejecutor se fija en el INSERT, sin
    cadenas y de la misma plataforma; solo la columna del sello se puede
    actualizar. La reversa conserva ciclos e intentos, rechaza borrar historia
    que ya use el motivo nuevo y deja la migracion aplicable de nuevo."""
    ida = _migracion("0094_arreglos05_ciclo_origen.sql")
    reversa = _migracion("0094_reversa_arreglos05_ciclo_origen.sql")
    assert ida.exists() and reversa.exists(), "falta la migracion 0094 o su reversa"
    with _db("orbit_a05_mig") as (conn, _extra):
        assert _columnas_nuevas(conn) == {"decision_committed_at", "resume_of"}
        assert conn.execute(_sonda_de(ida)).fetchone() == (True,), "la sonda da true aplicada"
        assert conn.execute(
            "SELECT has_column_privilege('app_decide', 'public.optimizer_cycle',"
            " 'decision_committed_at', 'UPDATE'),"
            " has_column_privilege('app_decide', 'public.optimizer_cycle', 'resume_of', 'UPDATE'),"
            " has_table_privilege('app_decide', 'public.optimizer_cycle', 'UPDATE')"
        ).fetchone() == (True, False, False), (
            "solo la columna del sello se puede actualizar; el permiso general no se amplia"
        )
        check = (
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid ="
            " 'public.decision_sin_aplicar'::regclass"
            " AND conname = 'decision_sin_aplicar_motivo_check'"
        )
        assert VENCIDA in conn.execute(check).fetchone()[0]

        conn.execute("SET ROLE app_decide")
        try:
            origen = conn.execute(
                "INSERT INTO optimizer_cycle (motor, mode, platform) VALUES"
                " ('ads_optimizer', 'live', 'amazon_us') RETURNING id"
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation, match="INSERT"):
                conn.execute(
                    "INSERT INTO optimizer_cycle (motor, mode, platform, decision_committed_at)"
                    " VALUES ('ads_optimizer', 'live', 'amazon_us', now())"
                )
            with pytest.raises(psycopg.errors.CheckViolation, match="TX3"):
                conn.execute(
                    "INSERT INTO optimizer_cycle (motor, mode, platform, resume_of) VALUES"
                    " ('ads_optimizer', 'live', 'amazon_us', %s)",
                    (origen,),
                )
            cerrar = (
                "UPDATE optimizer_cycle SET status = 'done', finished_at = now(),"
                " decisions_count = 0, notes = '{}'"
            )
            with pytest.raises(psycopg.errors.CheckViolation, match="cierre"):
                conn.execute(
                    "UPDATE optimizer_cycle SET decision_committed_at = now() WHERE id = %s",
                    (origen,),
                )
            conn.execute(cerrar + ", decision_committed_at = now() WHERE id = %s", (origen,))
            with pytest.raises(psycopg.errors.CheckViolation, match="una sola transicion"):
                conn.execute(
                    cerrar + ", decision_committed_at = now() WHERE id = %s AND status = 'done'",
                    (origen,),
                )
            with pytest.raises(psycopg.errors.CheckViolation, match="una sola transicion"):
                conn.execute(
                    "UPDATE optimizer_cycle SET decision_committed_at = NULL WHERE id = %s",
                    (origen,),
                )
            # El binario anterior cierra y re-sella su ciclo sin conocer el sello:
            # sigue funcionando (un rollback no lo rompe).
            conn.execute("UPDATE optimizer_cycle SET notes = '{\"n\": 1}' WHERE id = %s", (origen,))
            ejecutor = conn.execute(
                "INSERT INTO optimizer_cycle (motor, mode, platform, resume_of) VALUES"
                " ('ads_optimizer', 'live', 'amazon_us', %s) RETURNING id",
                (origen,),
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE optimizer_cycle SET resume_of = NULL WHERE id = %s", (ejecutor,)
                )
            with pytest.raises(psycopg.errors.CheckViolation, match="origen"):
                conn.execute(
                    "INSERT INTO optimizer_cycle (motor, mode, platform, resume_of) VALUES"
                    " ('ads_optimizer', 'live', 'amazon_us', %s)",
                    (ejecutor,),
                )
            with pytest.raises(psycopg.errors.CheckViolation, match="misma plataforma"):
                conn.execute(
                    "INSERT INTO optimizer_cycle (motor, mode, platform, resume_of) VALUES"
                    " ('ads_optimizer', 'live', 'amazon_mx', %s)",
                    (origen,),
                )
        finally:
            conn.execute("RESET ROLE")
        abierto = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform) VALUES"
            " ('ads_optimizer', 'live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation, match="futuro"):
            conn.execute(
                "UPDATE optimizer_cycle SET status = 'done', finished_at = now(),"
                " decision_committed_at = now() + interval '1 hour' WHERE id = %s",
                (abierto,),
            )
        assert conn.execute(
            "SELECT status FROM optimizer_cycle WHERE id = %s", (abierto,)
        ).fetchone() == ("running",), "el UPDATE rechazado no deja cambios a medias"
        with pytest.raises(psycopg.errors.CheckViolation, match="INSERT"):
            conn.execute("UPDATE optimizer_cycle SET resume_of = NULL WHERE id = %s", (ejecutor,))
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (NULL, 1, 'probe', '{}', false)"
        )
        conn.execute(
            "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo) VALUES (%s, %s, %s)",
            (_decision_de_prueba(conn, origen), ejecutor, VENCIDA),
        )
        intentos = conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0]

        with pytest.raises(psycopg.Error, match=VENCIDA):
            conn.execute(reversa.read_text(encoding="utf-8"))
        conn.execute("DELETE FROM decision_sin_aplicar WHERE motivo = %s", (VENCIDA,))
        conn.execute(reversa.read_text(encoding="utf-8"))

        assert _columnas_nuevas(conn) == set()
        assert conn.execute(_sonda_de(ida)).fetchone() == (False,), "la sonda da false revertida"
        assert (
            conn.execute(
                "SELECT count(*) FROM optimizer_cycle WHERE id IN (%s, %s)", (origen, ejecutor)
            ).fetchone()[0]
            == 2
        ), "la reversa conserva los ciclos"
        assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0] == intentos, (
            "la reversa conserva los intentos"
        )
        assert VENCIDA not in conn.execute(check).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo) VALUES"
                " (%s, %s, %s)",
                (_decision_de_prueba(conn, origen), ejecutor, VENCIDA),
            )
        conn.execute(ida.read_text(encoding="utf-8"))
        assert _columnas_nuevas(conn) == {"decision_committed_at", "resume_of"}
        assert conn.execute(_sonda_de(ida)).fetchone() == (True,)


def _decision_de_prueba(conn, cycle_id: int) -> int:
    fila = conn.execute(
        "SELECT id FROM decision WHERE cycle_id = %s LIMIT 1", (cycle_id,)
    ).fetchone()
    if fila is not None:
        return fila[0]
    config = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-a05', '{}') RETURNING id"
    ).fetchone()[0]
    entidad = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, match_type, keyword_text)"
        " VALUES ('amazon_us', 'keyword', 'a05-kw', 'EXACT', 'a05') RETURNING id"
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id, data_observed_at,"
        " window_start, window_end, old_value, new_value, value_currency, inputs) VALUES"
        " (%s, %s, 'bid', %s, now(), now() - interval '30 days', now() - interval '1 day',"
        " 1.00, 0.90, 'USD', '{}'::jsonb) RETURNING id",
        (cycle_id, entidad, config),
    ).fetchone()[0]


@_skip_db
def test_arreglos05_a_fallo_de_identidad_detiene_todas_las_mutaciones():
    """Control (ya verde antes del bloque): un fallo global de identidad (LWA
    rechaza el token) no se aisla por entidad. Ninguna mutacion sale, ninguna
    fila de la cola se reclama y las decisiones quedan intactas."""
    with _db("orbit_a05_ident") as (conn, _extra):
        ids = _semilla_live(conn)
        _agrega_kw2(conn, ids)
        amazon = _Amazon(**REMOTO_INICIAL)

        def sin_token(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(400, json={"error": "invalid_client"})
            return amazon(request)

        res = _corre(conn, factory=_directa(sin_token))

        assert res.status == "degraded" and res.decisions_count == 5
        assert "apply_error" in json.loads(res.notes)["apply"]
        assert amazon.puts_bid == [] and amazon.puts_estado == []
        assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_queue WHERE estado IN ('applying', 'applied')"
            ).fetchone()[0]
            == 0
        )


@_skip_db
def test_arreglos05_a_ejecutor_libera_pausa_economica_con_el_target_del_origen():
    """Un ejecutor de recuperacion no decide, asi que sus notas no traen
    `target`. La revalidacion de una pausa economica debe leer el target del
    ORIGEN: leida del ejecutor la pausa queda en espera y el horario siguiente
    no completa lo pendiente (revision del PR #746). Los intentos y la
    confirmacion siguen siendo del ejecutor."""
    from test_apply_cola import (
        _db_temporal as _db_cola,
    )
    from test_apply_cola import (
        _decision_corte,
        _encola_fila,
        _fechas,
        _handler_cortes,
        _metrica,
        _payload_pause,
        _semilla,
    )

    from app import apply_cola, lock
    from app.optimizer import goals as g

    with _db_cola("orbit_a05_econ") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 1, "ads_pause_economica": True})
        d = ids["ahora"]
        conn.execute(
            "UPDATE ads_optimizer_goal SET target_acos_pct = 20 WHERE platform = 'amazon_us'"
        )
        origen = ids["ciclo_ejec"]
        conn.execute(
            "UPDATE optimizer_cycle SET status = 'done', finished_at = now(),"
            " decision_committed_at = now(), notes = %s WHERE id = %s",
            (json.dumps({"target": {"target_aplicado": None}}), origen),
        )
        ejecutor = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform, resume_of)"
            " VALUES ('live', 'amazon_us', %s) RETURNING id",
            (origen,),
        ).fetchone()[0]
        for i, fecha in enumerate(
            _fechas(d.date() - dt.timedelta(days=20), d.date() - dt.timedelta(days=11))
        ):
            _metrica(
                conn,
                ids["run"],
                ids["kw"],
                fecha,
                clicks=5,
                cost=10 if i == 0 else 15,
                orders=1 if i == 0 else 0,
                ad_revenue=100 if i == 0 else 0,
            )
        dec = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause", motivo="pause_economica"
        )
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, _vistos = _handler_cortes()
        # La cerca del sello exige la fila del claim: el ejecutor lo tiene.
        assert lock.claim_lock(conn, "ads_optimizer:amazon_us", "test:cola") is not None

        conciliacion = ciclo._fase_reconciliacion(
            conn,
            modo=g.ModoEfectivo("live", None),
            platform="amazon_us",
            cycle_id_ejecutor=ejecutor,
            job_key="ads_optimizer:amazon_us",
            owner="ejecutor:1",
            guard=lambda: None,
            aplicador_factory=lambda c, **kw: __import__("test_apply_cola")._aplicador(
                c, handler, kw["cycle_id_ejecutor"]
            ),
            origen=origen,
        )

        aplicador = conciliacion.aplicador
        assert aplicador is not None and conciliacion.notas.get("apply_error") is None
        assert aplicador.cycle_id_ejecutor == ejecutor, "los intentos son del ejecutor"
        assert aplicador.ciclo_origen == origen, "el contexto de decision es el del origen"
        resultado = apply_cola.libera_vencidos(
            conn, "amazon_us", ahora=d, aplicador=aplicador, cycle_id=ejecutor
        )
        assert resultado.aplicadas == 1 and resultado.espera_target == 0, (
            "la pausa economica pendiente del origen se completa en la recuperacion"
        )
        assert conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone() == (
            "applied",
        )
        assert conn.execute(
            "SELECT applied_cycle_id FROM decision_application WHERE decision_id = %s", (dec,)
        ).fetchone() == (ejecutor,)


@_skip_db
def test_arreglos05_a_ejecutor_no_repite_decisiones_que_ya_tienen_desenlace(monkeypatch):
    """Revision del PR #746: el ejecutor carga todas las decisiones del origen.
    Las que ya tienen desenlace sin intento (una puja descartada por
    `modo_no_live`, un corte cerrado por `choque_clave`) no se reevaluan ni se
    reencolan aunque sus condiciones hayan cambiado; solo se completa lo que
    sigue sin desenlace."""
    with _db("orbit_a05_desenlace") as (conn, _extra):
        ids = _semilla_live(conn)
        _agrega_kw2(conn, ids)
        _muere_en_apply(monkeypatch)
        with pytest.raises(_Muerte):
            _corre(conn)
        monkeypatch.undo()
        origen = conn.execute("SELECT max(id) FROM optimizer_cycle").fetchone()[0]
        descartada = _decision(conn, origen, "bid", external=KW2)
        choca = _decision(conn, origen, "pause")
        conn.execute(
            "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
            " VALUES (%s, %s, 'modo_no_live'), (%s, %s, 'choque_clave')",
            (descartada, origen, choca, origen),
        )

        amazon = _Amazon(**REMOTO_INICIAL)
        res = _atiende(conn, factory=_directa(amazon))

        assert res.ciclo is not None and res.ciclo.resume_of == origen
        assert KW2 not in amazon.pujas_enviadas(), "la puja con desenlace no se reevalua"
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_attempt WHERE decision_id = %s", (descartada,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_queue WHERE decision_id = %s", (choca,)
            ).fetchone()[0]
            == 0
        ), "el corte cerrado por choque no se reencola"
        assert KW_BID in amazon.pujas_enviadas(), "lo que sigue sin desenlace se completa"


@_skip_db
def test_arreglos05_a_interrumpido_tras_tx3_abre_ejecutor_sin_huerfanas(monkeypatch):
    """Revision del PR #746: si la interrupcion dejo las fases de cortes y
    reactivaciones sin correr, el plan abre un ejecutor aunque ninguna decision
    este huerfana (la vista no ve reactivaciones ni cortes en cola). Con las
    fases completas, un corte con el veto abierto no abre nada."""
    with _db("orbit_a05_fases") as (conn, _extra):
        _semilla_live(conn)
        _muere_en_apply(monkeypatch)
        with pytest.raises(_Muerte):
            _corre(conn)
        monkeypatch.undo()
        origen = conn.execute("SELECT max(id) FROM optimizer_cycle").fetchone()[0]
        conn.execute(
            "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
            " SELECT id, %s, 'sin_respuesta' FROM decision WHERE cycle_id = %s",
            (origen, origen),
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM v_decision_huerfana WHERE cycle_id = %s", (origen,)
            ).fetchone()[0]
            == 0
        )
        assert ciclo.motivo_de_salto(conn, "amazon_us", hoy=DECIDED_AT.date()) is None, (
            "quedan fases sin correr"
        )

        amazon = _Amazon(**REMOTO_INICIAL)
        res = _atiende(conn, factory=_directa(amazon))

        assert res.ciclo is not None and res.ciclo.resume_of == origen
        notas = json.loads(res.ciclo.notes)["apply"]
        assert "cortes_liberados" in notas and "reactivaciones" in notas
        assert amazon.pujas_enviadas() == [], "nada sin desenlace: ninguna puja sale"
        assert ciclo.motivo_de_salto(conn, "amazon_us", hoy=DECIDED_AT.date()) is not None, (
            "ya no hace falta otro"
        )


def _zombi_con_list_retenido(extra, *, con_guard):
    """Corre el ciclo en un hilo y retiene el LIST de verificacion que viene
    despues del PUT. Devuelve (llego, liberar, amazon, zombi, hilo)."""
    llego, liberar = threading.Event(), threading.Event()

    def retener(_request: httpx.Request) -> None:
        llego.set()
        assert liberar.wait(timeout=30), "el test nunca libero la lectura"

    amazon = _Amazon(**REMOTO_INICIAL)
    amazon.al_list = retener
    zombi: dict = {}

    def correr() -> None:
        conexion = extra()
        try:
            zombi["r"] = _corre(
                conexion, factory=_directa(amazon, con_guard=con_guard), owner="zombie:1"
            )
        except BaseException as exc:  # noqa: BLE001 - el test inspecciona la excepcion
            zombi["error"] = exc
        finally:
            conexion.close()

    return llego, liberar, amazon, zombi, threading.Thread(target=correr, daemon=True)


@_skip_db
@pytest.mark.parametrize("con_guard", [True, False])
@pytest.mark.parametrize("desplaza", ["otro_dueno", "sin_fila"])
def test_arreglos05_a_dueno_desplazado_durante_la_lectura_de_vuelta_no_sella(con_guard, desplaza):
    """Revision del PR #746: el claim cambia de dueno MIENTRAS vuela el LIST de
    verificacion (el PUT ya volvio y Amazon lo aplico). El dueno anterior no
    sella aunque su lectura termine bien. Con `con_guard` False el aplicador no
    tiene el aviso pre-HTTP: lo detiene la cerca del sello sobre la fila del
    claim. `sin_fila`: el sucesor tomo y solto el claim entre el aviso anterior
    y la cerca, asi que la fila ya no existe; sin fila tampoco es dueno."""
    with _db("orbit_a05_cerca") as (conn, extra):
        _semilla_live(conn)
        llego, liberar, amazon, zombi, hilo = _zombi_con_list_retenido(extra, con_guard=con_guard)
        hilo.start()
        assert llego.wait(timeout=30), "el zombie nunca llego a la lectura de vuelta"
        if desplaza == "sin_fila":
            conn.execute("DELETE FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,))
        else:
            conn.execute(
                "UPDATE ads_optimizer_lock SET owner = 'sucesor',"
                " claimed_at = now() - interval '31 minutes',"
                " heartbeat_at = now() - interval '31 minutes' WHERE job_key = %s",
                (JOB_KEY,),
            )
        liberar.set()
        hilo.join(timeout=60)
        assert not hilo.is_alive() and "error" not in zombi, zombi.get("error")
        assert amazon.pujas_enviadas() == [KW_BID]
        puja = _decision(conn, zombi["r"].cycle_id, "bid", external=KW_BID)
        assert conn.execute(
            "SELECT finished_at, resultado FROM apply_attempt WHERE decision_id = %s", (puja,)
        ).fetchone() == (None, None), "el dueno desplazado no sella"
        assert _desenlace(conn, puja) is None


@_skip_db
def test_arreglos05_a_origen_decidido_con_reloj_de_ayer_no_cuenta_como_el_de_hoy(monkeypatch):
    """Revision del PR #746: el sello de TX3 lleva el reloj de la DECISION, no
    el de la base. Un ciclo corrido con el reloj de ayer no es la decision de
    hoy: el horario de hoy abre un origen nuevo y lo de ayer no sale a Amazon
    (vence sin borrarse)."""
    with _db("orbit_a05_reloj") as (conn, _extra):
        _semilla_live(conn)
        ayer = DECIDED_AT - DIA
        _muere_en_apply(monkeypatch)
        with pytest.raises(_Muerte):
            _corre(conn, ahora=ayer)
        monkeypatch.undo()
        ayer_id = conn.execute("SELECT max(id) FROM optimizer_cycle").fetchone()[0]
        decididas = conn.execute(
            "SELECT count(*) FROM decision WHERE cycle_id = %s", (ayer_id,)
        ).fetchone()[0]
        assert decididas > 0, "el ciclo de ayer si decidio"

        amazon = _Amazon(**REMOTO_INICIAL)
        res = _atiende(conn, factory=_directa(amazon), ahora=DECIDED_AT)

        assert res.ciclo is not None and res.ciclo.resume_of is None, (
            "hoy se decide de nuevo; no se recuperan propuestas de ayer"
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_attempt a JOIN decision d ON d.id = a.decision_id"
                " WHERE d.cycle_id = %s",
                (ayer_id,),
            ).fetchone()[0]
            == 0
        ), "nada de ayer se intenta"
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_sin_aplicar s"
                " JOIN decision d ON d.id = s.decision_id"
                " WHERE d.cycle_id = %s AND s.motivo = %s",
                (ayer_id, VENCIDA),
            ).fetchone()[0]
            == decididas
        ), "lo de ayer vence con motivo auditable"


@_skip_db
def test_arreglos05_a_ejecutor_no_aplica_pausa_con_la_evaluacion_del_origen(monkeypatch):
    """Revision del PR #746: una pausa economica decidida por margen de
    producto llega a un ejecutor de recuperacion con el veto ya vencido. La
    revalidacion toma el target de las notas del ORIGEN, pero no su evaluacion
    de producto: tiene horas y, despues de decidir, el producto perdio su
    costo vigente. Con la evaluacion del origen la pausa sale a Amazon aunque
    ya no califique; sin una elegibilidad que se pueda comprobar, la pausa
    espera al ciclo que si evalua."""

    from test_apply_cola import _aplicador, _encola_fila, _handler_cortes, _payload_pause
    from test_cycle import _config_version, _run
    from test_orbit07_m2 import (
        _db as _db_producto,
    )
    from test_orbit07_m2 import (
        _metrica_economica,
        _mundo,
        _pausa_de,
        _producto,
        _ventas_sin_costo,
    )
    from test_productos_meta_ciclo import _settings

    from app import apply_cola, lock
    from app.optimizer import goals as g

    with _db_producto("orbit_a05_eval_origen") as conn:
        ids = _mundo(conn)
        _config_version(
            conn,
            {
                **_settings(conn),
                "ads_optimizer_mode": "live",
                "ads_pause_economica": True,
                "ads_apply_cap_amazon_us_pause": 2,
            },
        )
        _metrica_economica(conn, _run(conn), ids["kw"])
        _muere_en_apply(monkeypatch)
        with pytest.raises(_Muerte):
            _corre(conn)
        monkeypatch.undo()
        origen = conn.execute("SELECT max(id) FROM optimizer_cycle").fetchone()[0]
        pausa = _pausa_de(conn, origen, ids["kw"])
        assert pausa is not None and pausa["target_procedencia"] == "margen_producto", (
            "el origen decide la pausa economica por margen de producto"
        )
        assert Decimal(pausa["target_acos_pct_usado"]) == Decimal("4"), (
            "con su costo vigente el origen la decide al 4 %"
        )
        ext = conn.execute(
            "SELECT external_id FROM ad_entity WHERE id = %s", (ids["kw"],)
        ).fetchone()[0]
        ejecutor = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform, resume_of)"
            " VALUES ('live', 'amazon_us', %s) RETURNING id",
            (origen,),
        ).fetchone()[0]
        q = _encola_fila(
            conn,
            pausa["decision_id"],
            ids["kw"],
            "pause",
            vence=DECIDED_AT,
            encolado=DECIDED_AT,
            payload=_payload_pause(ext),
        )
        # Despues de decidir, el producto pierde su costo vigente sin cambiar
        # de grupo ni de huella: el margen deja de medir.
        _ventas_sin_costo(conn, _producto(conn))
        conn.execute(
            "UPDATE sku_cost SET valid_to = CURRENT_DATE WHERE product_id = %s",
            (_producto(conn),),
        )
        assert (
            conn.execute(
                "SELECT margen_neto_pct FROM v_margen_producto WHERE product_id = %s",
                (_producto(conn),),
            ).fetchone()[0]
            is None
        ), "a la hora de recuperar, el producto ya no tiene margen vigente"

        # La cerca del sello exige la fila del claim: el ejecutor lo tiene.
        assert lock.claim_lock(conn, "ads_optimizer:amazon_us", "test:cola") is not None
        handler, vistos = _handler_cortes({ext: "ENABLED"})
        conciliacion = ciclo._fase_reconciliacion(
            conn,
            modo=g.ModoEfectivo("live", None),
            platform="amazon_us",
            cycle_id_ejecutor=ejecutor,
            job_key="ads_optimizer:amazon_us",
            owner="ejecutor:1",
            guard=lambda: None,
            aplicador_factory=lambda c, **kw: _aplicador(c, handler, kw["cycle_id_ejecutor"]),
            origen=origen,
        )
        aplicador = conciliacion.aplicador
        assert aplicador is not None and conciliacion.notas.get("apply_error") is None
        assert aplicador.ciclo_origen == origen, "se revalida contra el ciclo que decidio"

        resultado = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=DECIDED_AT + dt.timedelta(hours=1),
            aplicador=aplicador,
            cycle_id=ejecutor,
        )

        assert [r for r in vistos if r.method == "PUT"] == [], (
            "la pausa no sale a Amazon sin una elegibilidad comprobable"
        )
        assert (resultado.aplicadas, resultado.espera_target) == (0, 1), (
            "sin evaluacion vigente la pausa economica espera"
        )
        estado = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()
        assert estado == ("released",), "la fila sigue en cola para el ciclo que si evalua"
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_application WHERE decision_id = %s",
                (pausa["decision_id"],),
            ).fetchone()[0]
            == 0
        )
        notas_ejecutor = conn.execute(
            "SELECT notes FROM optimizer_cycle WHERE id = %s", (ejecutor,)
        ).fetchone()[0]
        [evidencia] = json.loads(notas_ejecutor)["revalidaciones_economicas"]
        assert evidencia["resultado"] == "espera_target"
        assert evidencia["economia"]["producto"]["motivo"] == "sin_evaluacion_fresca"


@_skip_db
@pytest.mark.parametrize("estado", [401, 403])
def test_arreglos05_a_rechazo_de_identidad_detiene_todas_las_mutaciones(estado):
    """Revision de Astra del PR #746: un 403, o un 401 que sigue despues de
    refrescar el token, no es el rechazo de UNA puja: Amazon rechaza la
    identidad o los permisos de toda la corrida. Es fallo global. La puja queda
    sellada con su rechazo y no sale ninguna mutacion mas, ni de las pujas que
    faltan ni de las fases posteriores."""
    with _db("orbit_a05_identidad") as (conn, _extra):
        ids = _semilla_live(conn)
        _agrega_kw2(conn, ids)
        amazon = _Amazon(**REMOTO_INICIAL, rechaza_identidad=estado)

        res = _corre(conn, factory=_directa(amazon))

        assert res.status == "degraded" and res.decisions_count == 5
        notas = json.loads(res.notes)["apply"]
        assert "apply_error" in notas and "apply_abortado_owner" not in notas
        enviadas = amazon.pujas_enviadas()
        assert len(enviadas) >= 1 and len(set(enviadas)) == 1, "tras el rechazo no sale otra puja"
        primera = _decision(conn, res.cycle_id, "bid", external=enviadas[0])
        sin_enviar = ({KW_BID, KW2} - set(enviadas)).pop()
        assert amazon.puts_estado == [], "las fases posteriores no corren"
        assert "cortes_liberados" not in notas and "reactivaciones" not in notas
        assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0] == 1, (
            "solo nace el intento de la primera puja"
        )
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s", (primera,)
        ).fetchone()[0]
        assert resultado.startswith(f"fallo http {estado}"), "el rechazo queda en el ledger"
        # Revision de Devin: la puja rechazada se cierra en la misma transaccion
        # que el sello. Sin desenlace quedaria huerfana (tiene intento: no se
        # reintenta, no se concilia y no vence) y el plan abriria un ejecutor en
        # cada horario del dia.
        assert _desenlace(conn, primera) == "fallo_http", "la puja rechazada se cierra"
        assert _desenlace(conn, _decision(conn, res.cycle_id, "bid", external=sin_enviar)) is None
        # Se restauran los permisos: el horario siguiente abre UN ejecutor, que
        # completa la otra puja, no repite la rechazada y cierra el origen.
        restaurado = _Amazon(**REMOTO_INICIAL)
        rec = _atiende(conn, factory=_directa(restaurado))
        assert rec.ciclo is not None and rec.ciclo.resume_of == res.cycle_id
        assert restaurado.pujas_enviadas() == [sin_enviar], "solo sale la puja que faltaba"
        assert _desenlace(conn, _decision(conn, res.cycle_id, "bid", external=sin_enviar)) == (
            "aplicada:True"
        )
        assert ciclo.motivo_de_salto(conn, "amazon_us", hoy=DECIDED_AT.date()) is not None, (
            "al terminar el resto, el plan deja de abrir ejecutores"
        )


@_skip_db
def test_arreglos05_a_rechazo_de_identidad_no_se_aisla_por_fila(monkeypatch):
    """Revision de Astra del PR #746: los aisladores por fila dejan pasar un
    5xx, la red o un rechazo 400 de ESA fila. Un 401/403 de identidad o
    permisos no es un desenlace de la fila: se propaga y la fase se detiene."""
    from app import apply_cola
    from app.ads.write import AdsApiErrorMutacion

    def rechazo(status: int) -> AdsApiErrorMutacion:
        return AdsApiErrorMutacion(
            f"status={status}: PUT /sp/keywords",
            cuerpo="{}",
            status=status,
            method="PUT",
            path="/sp/keywords",
        )

    with _db("orbit_a05_ident_fila") as (conn, _extra):
        _semilla_live(conn)

        def corte(*_args, **_kwargs):
            raise rechazo(403)

        monkeypatch.setattr(apply_cola, "_ejecuta_rama", corte)
        with pytest.raises(AdsApiErrorMutacion, match="status=403"):
            apply_cola._aisla_corte(conn, object(), object(), "pause_hoja")

        def harvest(*_args, **_kwargs):
            raise rechazo(401)

        monkeypatch.setattr(apply_cola, "_procesa_harvest_liberada", harvest)
        with pytest.raises(AdsApiErrorMutacion, match="status=401"):
            apply_cola._aisla_harvest(conn, object(), "amazon_us", object(), [], [], None)

        def corte_de_la_hoja(*_args, **_kwargs):
            raise rechazo(400)

        monkeypatch.setattr(apply_cola, "_ejecuta_rama", corte_de_la_hoja)
        assert apply_cola._aisla_corte(conn, object(), object(), "pause_hoja") == "failed", (
            "el rechazo de esa hoja sigue aislado"
        )


@_skip_db
def test_arreglos05_a_reversa_incierta_se_resuelve_por_lectura_antes_de_reenviar():
    """Revision de Astra del PR #746: Amazon habilito la hoja pero la respuesta
    se perdio (5xx). Queda un intento `reversa` sin sello y el cache sigue en
    PAUSED, asi que un ejecutor del mismo origen vuelve a recorrer sus resumes.
    Antes de cobrar cuota y volver a enviar, ese intento se resuelve con una
    lectura. Si la hoja ya esta ENABLED se sella y se actualiza el cache sin
    otro PUT; si sigue PAUSED, el intento viejo se sella como no aplicado y la
    reactivacion sigue su curso."""
    from test_apply_cola import _aplicador, _handler_cortes, _reloj, _semilla
    from test_cycle_apply import _db_temporal
    from test_temporada01_f import (
        _config,
        _episodio,
        _nueva_kw,
        _pausa_baja,
        _pausado,
        estacion_dia,
    )

    from app import apply_cola, lock, temporada_io
    from app.optimizer import temporada

    def cache(hoja: int) -> str:
        return conn.execute(
            "SELECT status FROM ad_entity_state WHERE ad_entity_id = %s", (hoja,)
        ).fetchone()[0]

    def cuota() -> int:
        return conn.execute("SELECT coalesce(sum(used), 0) FROM apply_quota_state").fetchone()[0]

    def intentos(pausa: int) -> list[tuple]:
        return conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'"
            " ORDER BY seq",
            (pausa,),
        ).fetchall()

    with _db_temporal("orbit_a05_reversa") as (conn, _extra):
        ids = _semilla(conn)
        ahora = _reloj()
        config_id, settings = _config(conn)
        cerrado = _episodio(conn, config_id, ids["ciclo_ejec"], ahora, cerrado=True)
        pausas = {}
        for hoja in (ids["kw"], ids["kw2"]):
            pausa, _q = _pausa_baja(
                conn,
                ciclo_id=ids["ciclo_dec"],
                config_id=config_id,
                hoja=hoja,
                ahora=ahora,
                episodio=cerrado,
            )
            _pausado(conn, hoja)
            pausas[hoja] = pausa
        # Dos hojas mas: una que el LIST de recuperacion omitira y otra ARCHIVED.
        kw3 = _nueva_kw(conn, ids["ag"], "7203")
        kw4 = _nueva_kw(conn, ids["ag"], "7204")
        for hoja in (kw3, kw4):
            pausa, _q = _pausa_baja(
                conn,
                ciclo_id=ids["ciclo_dec"],
                config_id=config_id,
                hoja=hoja,
                ahora=ahora,
                episodio=cerrado,
            )
            _pausado(conn, hoja)
            pausas[hoja] = pausa
        [primera, segunda, tercera, cuarta] = temporada_io.reactivaciones_pendientes(
            conn, "amazon_us", frozenset()
        )
        assert (primera.ad_entity_id, segunda.ad_entity_id) == (ids["kw"], ids["kw2"])
        assert (tercera.ad_entity_id, cuarta.ad_entity_id) == (kw3, kw4)
        estacion = temporada.resuelve(temporada.desde_settings(settings), estacion_dia())
        ciclo_resume = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        for pendiente in (primera, segunda, tercera, cuarta):
            propuesta = ciclo.pendiente_reactivacion(pendiente, estacion, decidido_el=ahora)
            conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
                " data_observed_at, window_start, window_end, inputs)"
                " VALUES (%s, %s, 'resume', %s, %s, %s, %s, %s, %s)",
                (
                    ciclo_resume,
                    pendiente.ad_entity_id,
                    ahora,
                    config_id,
                    propuesta.data_observed_at,
                    propuesta.window_start,
                    propuesta.window_end,
                    json.dumps(propuesta.inputs),
                ),
            )
        resumes = apply_cola.resumes_del_ciclo(conn, ciclo_resume)
        assert len(resumes) == 4
        # La cerca del sello exige la fila del claim: el ejecutor lo tiene.
        assert lock.claim_lock(conn, "ads_optimizer:amazon_us", "test:cola") is not None

        # Primera pasada: los cuatro PUT salen y Amazon pierde las respuestas.
        base, _vistos = _handler_cortes(
            estados={"7201": "PAUSED", "7202": "PAUSED", "7203": "PAUSED", "7204": "PAUSED"}
        )
        perdidos: list[httpx.Request] = []

        def pierde_la_respuesta(request: httpx.Request) -> httpx.Response:
            if request.method == "PUT":
                perdidos.append(request)
                return httpx.Response(500, json={"code": "INTERNAL_ERROR"})
            return base(request)

        res1 = apply_cola.aplica_reactivaciones(
            conn, _aplicador(conn, pierde_la_respuesta, ids["ciclo_ejec"]), resumes, tick=None
        )
        assert (res1.confirmadas, res1.fallidas) == (0, 4), res1
        assert len(perdidos) == 4, "los cuatro PUT de reactivacion si salieron"
        for hoja in pausas:
            assert [f[0] for f in intentos(pausas[hoja])] == [None], "quedan sin sello"
            assert cache(hoja) == "PAUSED", "el cache no llego a actualizarse"
        cuota1 = cuota()
        assert cuota1 == 4

        # El ejecutor del mismo origen vuelve a recorrer sus resumes. Amazon SI
        # habilito la primera hoja; la segunda sigue pausada; la tercera no
        # aparece en el LIST; la cuarta esta ARCHIVED.
        amazon, vistos = _handler_cortes(
            estados={"7201": "ENABLED", "7202": "PAUSED", "7204": "ARCHIVED"}
        )
        res2 = apply_cola.aplica_reactivaciones(
            conn, _aplicador(conn, amazon, ids["ciclo_ejec"]), resumes, tick=None
        )
        enviados = [
            json.loads(r.content)["keywords"][0]["keywordId"] for r in vistos if r.method == "PUT"
        ]
        assert enviados == ["7202"], "solo la hoja que sigue pausada se reenvia"
        assert (res2.confirmadas, res2.fallidas, res2.sin_quota) == (2, 2, 0), res2
        assert [f[0] for f in intentos(pausas[ids["kw"]])] == ["ok"]
        assert [f[0] for f in intentos(pausas[ids["kw2"]])] == ["ok:no_aplicado", "ok"]
        assert [f[0] for f in intentos(pausas[kw3])] == [None], "sin lectura no se sella"
        assert [f[0] for f in intentos(pausas[kw4])] == ["fallo:entidad_archived"]
        assert cache(ids["kw"]) == "ENABLED" and cache(ids["kw2"]) == "ENABLED"
        assert cache(kw3) == "PAUSED" and cache(kw4) == "ARCHIVED"
        assert cuota() == cuota1 + 1, "solo la hoja que sigue pausada cobra cuota"
        # La guarda de la reversa manual reconoce la reversa conciliada (sella
        # `ok`): un segundo intento manual no vuelve a enviar. La que sigue
        # abierta, no.
        from app import apply_reversa

        assert apply_reversa._reversa_ya_hecha(conn, pausas[ids["kw"]])
        assert not apply_reversa._reversa_ya_hecha(conn, pausas[kw3])


@_skip_db
def test_arreglos05_a_tx3_no_se_compromete_si_el_claim_cambio_de_dueno(monkeypatch):
    """Revision de Astra del PR #746: el TTL vence con la TX3 del anterior
    todavia abierta. El sucesor toma el claim y consulta el plan: su SELECT no
    ve el sello (sin commit) y abrira otro origen. Si la TX3 del anterior
    cerraba antes del rastro del sucesor, ambos decidian el mismo dia. Dentro
    de la TX3 se comprueba y bloquea la fila del claim antes de sellar: la TX3
    espera a que el sucesor comprometa su claim, ve el dueno nuevo y no se
    compromete."""
    with _db("orbit_a05_tx3_claim") as (conn, extra):
        _semilla_live(conn)
        dentro_tx3, seguir = threading.Event(), threading.Event()
        original = ciclo._inserta_decisiones
        anterior: dict = {}

        def inserta_y_espera(*args, **kwargs):
            ids = original(*args, **kwargs)
            anterior["ciclo"] = args[1]
            dentro_tx3.set()
            assert seguir.wait(timeout=30), "el test nunca libero la TX3"
            return ids

        monkeypatch.setattr(ciclo, "_inserta_decisiones", inserta_y_espera)

        def correr() -> None:
            conexion = extra()
            try:
                _corre(
                    conexion,
                    factory=_directa(_Amazon(**REMOTO_INICIAL)),
                    owner="anterior:1",
                )
            except BaseException as exc:  # noqa: BLE001 - el test inspecciona la excepcion
                anterior["error"] = exc
            finally:
                conexion.close()

        hilo = threading.Thread(target=correr, daemon=True)
        hilo.start()
        assert dentro_tx3.wait(timeout=30), "el anterior nunca llego a su TX3"
        # El TTL vence. El sucesor toma el claim y lee el plan bajo el, en una
        # transaccion todavia abierta: su SELECT no ve el sello del anterior.
        conn.execute(
            "UPDATE ads_optimizer_lock SET heartbeat_at = now() - interval '31 minutes'"
            " WHERE job_key = %s",
            (JOB_KEY,),
        )
        with conn.transaction():
            assert ciclo._toma_claim(conn, JOB_KEY, "sucesor:1")
            plan = ciclo._plan_de_hoy(conn, "amazon_us", hoy=DECIDED_AT.date())
            assert plan.origen is None, "el sucesor no ve el sello: abrira su propio origen"
            seguir.set()
            hilo.join(timeout=3)
            assert hilo.is_alive(), "la TX3 del anterior espera a que el sucesor comprometa"
        hilo.join(timeout=30)
        assert not hilo.is_alive()
        assert isinstance(anterior.get("error"), ciclo.CicloOcupado), anterior.get("error")
        sello, decisiones = conn.execute(
            "SELECT to_jsonb(c) ->> 'decision_committed_at',"
            " (SELECT count(*) FROM decision d WHERE d.cycle_id = c.id)"
            " FROM optimizer_cycle c WHERE id = %s",
            (anterior["ciclo"],),
        ).fetchone()
        assert sello is None and decisiones == 0, "la TX3 del anterior no dejo sello ni decisiones"


def _termina_en(segundos: int, accion) -> dict:
    """Corre `accion` en un hilo y espera `segundos`. Devuelve su caja (`r` o
    `error`) o hace fallar si no termino: eso es un cuelgue."""
    caja: dict = {}

    def correr() -> None:
        try:
            caja["r"] = accion()
        except BaseException as exc:  # noqa: BLE001 - el test inspecciona la excepcion
            caja["error"] = exc

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    hilo.join(timeout=segundos)
    assert not hilo.is_alive(), f"no termino en {segundos} s: se colgo esperando un cerrojo"
    return caja


def _aqui(caja: dict) -> None:
    """Vuelve a lanzar en el hilo del test la excepcion que tuvo la accion."""
    if "error" in caja:
        raise caja["error"]


@_skip_db
def test_arreglos05_a_ninguna_espera_del_claim_es_infinita():
    """Revision del PR #746: si una conexion queda con la transaccion abierta
    reteniendo la fila del claim, nada del ciclo puede esperar para siempre.
    El claim, la TX3 y la cerca esperan con tope y tratan el cerrojo ocupado
    como claim no probado; el latido falla rapido (el TTL es el respaldo)."""
    from app import apply, lock

    with _db("orbit_a05_tope") as (conn, extra):
        _semilla_live(conn)
        assert lock.claim_lock(conn, JOB_KEY, OWNER) is not None
        aplicador = _directa(_Amazon(**REMOTO_INICIAL))(
            conn,
            platform="amazon_us",
            cycle_id_ejecutor=1,
            owner=OWNER,
            job_key=JOB_KEY,
            tick=lambda: None,
        )
        hb = ciclo._abre_heartbeat(conn)
        assert hb is not None
        retenedora = extra()
        try:
            with retenedora.transaction():
                retenedora.execute(
                    "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s FOR UPDATE",
                    (JOB_KEY,),
                )
                latido = _termina_en(15, lambda: lock.latir_lock(hb, JOB_KEY, OWNER))
                _aqui(latido)  # fail-open: regresa, no se cuelga

                def cerca() -> None:
                    with conn.transaction():
                        aplicador.exige_dueno()

                with pytest.raises(apply.DuenoDelClaimCambio):
                    _aqui(_termina_en(15, cerca))

                def tx3() -> None:
                    with conn.transaction():
                        ciclo._compromete_tx3(
                            conn,
                            1,
                            "done",
                            0,
                            "{}",
                            decided_at=DECIDED_AT,
                            job_key=JOB_KEY,
                            owner=OWNER,
                        )

                with pytest.raises(ciclo.CicloOcupado):
                    _aqui(_termina_en(15, tx3))

                def claim() -> None:
                    ciclo.atender_plataforma(
                        conn, "amazon_us", owner="sucesor:1", ahora=DECIDED_AT, heartbeat_cada=1
                    )

                with pytest.raises(ciclo.CicloOcupado):
                    _aqui(_termina_en(15, claim))
        finally:
            retenedora.close()
            hb.close()


@_skip_db
@pytest.mark.parametrize("cuando", ["antes", "despues"])
def test_arreglos05_a_rechazo_de_identidad_en_una_lectura_detiene_el_lote(cuando):
    """Revision de Astra del PR #746: el 403, o el 401 que sigue despues de
    refrescar el token, en una LECTURA (el LIST previo al PUT o el de
    verificacion) llega como AdsApiError y se trataba como fallo de ESA puja:
    el lote segui con la siguiente. Es fallo global. El intento cuyo PUT ya
    salio queda abierto, sin sello, y no sale ninguna mutacion mas."""
    with _db("orbit_a05_ident_lectura") as (conn, _extra):
        ids = _semilla_live(conn)
        _agrega_kw2(conn, ids)
        amazon = _Amazon(**REMOTO_INICIAL, rechaza_lectura=cuando)

        res = _corre(conn, factory=_directa(amazon))

        assert res.status == "degraded"
        notas = json.loads(res.notes)["apply"]
        assert "apply_error" in notas
        assert "cortes_liberados" not in notas, "las fases posteriores no corren"
        assert len(amazon.puts_bid) == (0 if cuando == "antes" else 1), (
            "tras el rechazo no sale otra puja"
        )
        assert amazon.puts_estado == []
        intentos = conn.execute("SELECT count(*), count(finished_at) FROM apply_attempt").fetchone()
        assert intentos == ((0, 0) if cuando == "antes" else (1, 0)), (
            "el PUT que llego a salir queda abierto, sin sello"
        )
        pujas = conn.execute(
            "SELECT id FROM decision WHERE cycle_id = %s AND kind = 'bid'", (res.cycle_id,)
        ).fetchall()
        assert len(pujas) == 2 and all(_desenlace(conn, p[0]) is None for p in pujas)


def test_arreglos05_a_rechazo_de_identidad_en_la_recuperacion_de_cosecha(monkeypatch):
    """Revision de Devin del PR #746: la recuperacion de cosecha llama al mismo
    ejecutor que aplica una cosecha nueva. Un 401/403 de ese ejecutor, o de una
    de sus lecturas, se trataba como ambiguo y el ciclo seguia mutando. Se
    propaga; un 5xx sigue siendo ambiguo y el trabajo queda, tal cual, para el
    ciclo siguiente."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock

    from test_apply_harvest import _aplicador

    from app import apply, apply_cola
    from app import apply_harvest_reconciliacion as recuperacion
    from app.ads.client import AdsApiError
    from app.ads.write import AdsApiErrorMutacion

    estado = {"job": "exact_created", "cola": "applying"}

    def execute(sql, params=None):
        filas: list = []
        if sql == recuperacion._SQL_JOBS_EN_VUELO:
            filas = [(53, 90)]
        elif sql == recuperacion._SQL_HARVEST_APLICANDO and estado["cola"] == "applying":
            filas = [(90, 53)]
        elif sql == recuperacion._SQL_JOB_EN_VUELO_DE:
            filas = [(True,)]
        elif sql == recuperacion._ejecucion._SQL_CLAIM:
            filas = [(1,)]
        elif "UPDATE apply_queue SET estado = %s" in sql:
            estado["cola"] = params[0]
        return SimpleNamespace(fetchall=lambda: filas, fetchone=lambda: filas[0] if filas else None)

    conn = Mock()
    conn.transaction.side_effect = nullcontext
    conn.execute.side_effect = execute
    monkeypatch.setattr(apply, "gate_ancestros", lambda _c, _e: None)
    monkeypatch.setattr(apply_cola, "_modo_efectivo_corte", lambda *_a, **_k: "live")
    job = SimpleNamespace(id=53, decision_id=90, ad_entity_id=1, fase="exact_created")
    monkeypatch.setattr(recuperacion._ejecucion, "_job_de_fila", lambda _fila: job)
    monkeypatch.setattr(recuperacion, "_cola_de", lambda _conn, _decision: (90, "applying"))
    alcance: list = []
    aplicador = _aplicador(conn, lambda request: None, 7)

    def rechazado(*_args, **_kwargs):
        alcance.append(1)
        raise AdsApiErrorMutacion(
            "status=403: POST /sp/keywords",
            cuerpo="{}",
            status=403,
            method="POST",
            path="/sp/keywords",
        )

    monkeypatch.setattr(recuperacion._ejecucion, "_continua_job", rechazado)
    with pytest.raises(AdsApiErrorMutacion, match="status=403"):
        recuperacion.reconcilia_harvest(conn, aplicador, "amazon_us")
    assert alcance == [1], "la recuperacion llamo al ejecutor del trabajo"
    assert estado == {"job": "exact_created", "cola": "applying"}, "el trabajo queda como estaba"

    def ambiguo(*_args, **_kwargs):
        error = AdsApiError("status=500: POST /sp/keywords")
        error.status = 500
        raise error

    monkeypatch.setattr(recuperacion._ejecucion, "_continua_job", ambiguo)
    resumen = recuperacion.reconcilia_harvest(conn, aplicador, "amazon_us")
    assert resumen.jobs_done == 0, "un 5xx no se trata como fallo global"


@_skip_db
def test_arreglos05_a_reversa_manual_con_rechazo_de_identidad_no_es_un_500(monkeypatch):
    """Revision de Devin del PR #746: con un 401/403 la reversa MANUAL
    propagaba la excepcion (lo exige el barrido automatico, que detiene sus
    mutaciones) y la API respondia 500. En la frontera manual es un resultado:
    confirmada False. Otros errores de la API siguen propagandose."""
    from test_apply_cola import _reloj, _semilla
    from test_cycle_apply import _db_temporal
    from test_temporada01_f import _config, _pausa_baja

    from app import apply_cola
    from app import apply_reversa as manual
    from app.ads.client import AdsApiError
    from app.ads.write import AdsApiErrorMutacion

    def rechazo(status: int) -> AdsApiErrorMutacion:
        return AdsApiErrorMutacion(
            f"status={status}: PUT /sp/keywords",
            cuerpo="{}",
            status=status,
            method="PUT",
            path="/sp/keywords",
        )

    with _db_temporal("orbit_a05_reversa_manual") as (conn, _extra):
        ids = _semilla(conn)
        config_id, _settings = _config(conn)
        _pausa, fila = _pausa_baja(
            conn, ciclo_id=ids["ciclo_dec"], config_id=config_id, hoja=ids["kw"], ahora=_reloj()
        )
        monkeypatch.setattr(manual, "_negative_id_del_ledger", lambda _conn, _decision: "n-1")

        def rechazada(*_args, **_kwargs):
            raise rechazo(403)

        monkeypatch.setattr(apply_cola, "reversa_pause", rechazada)
        monkeypatch.setattr(apply_cola, "reversa_negative", rechazada)
        for tipo in ("pause", "negative"):
            assert (
                manual.reversa_manual(
                    conn,
                    tipo=tipo,
                    queue_id=fila,
                    cliente_reversa=lambda *_a, **_k: object(),
                    reversa_bid_fn=lambda *_a, **_k: True,
                )["confirmada"]
                is False
            )

        def falla(*_args, **_kwargs):
            error = AdsApiError("status=500: PUT /sp/keywords")
            error.status = 500
            raise error

        monkeypatch.setattr(apply_cola, "reversa_pause", falla)
        with pytest.raises(AdsApiError):
            manual.reversa_manual(
                conn,
                tipo="pause",
                queue_id=fila,
                cliente_reversa=lambda *_a, **_k: object(),
                reversa_bid_fn=lambda *_a, **_k: True,
            )


@_skip_db
def test_arreglos05_a_la_puja_rechazada_no_se_cierra_sin_el_claim(monkeypatch):
    """Revision de Devin del PR #746: en el camino del 401/403 el dueno se
    validaba con el aviso previo, que lee sin bloquear, y el desenlace se
    escribia sin la cerca. Si el claim cambiaba entre ese aviso y la
    escritura, el ciclo desplazado cerraba la puja. Ahora el cierre pasa por
    la cerca: el intento queda abierto y nada se cierra."""
    from app import apply

    with _db("orbit_a05_cierre_sin_claim") as (conn, _extra):
        _semilla_live(conn)
        amazon = _Amazon(**REMOTO_INICIAL, rechaza_identidad=403)
        senal: dict = {}
        amazon.al_rechazo_identidad = lambda: senal.__setitem__("visto", True)
        aviso_previo = apply._tick

        def aviso_y_pierde(fn):
            aviso_previo(fn)
            if senal.pop("visto", None):
                # El sucesor toma el claim justo despues del aviso previo.
                conn.execute(
                    "UPDATE ads_optimizer_lock SET owner = 'sucesor' WHERE job_key = %s",
                    (JOB_KEY,),
                )

        monkeypatch.setattr(apply, "_tick", aviso_y_pierde)

        res = _corre(conn, factory=_directa(amazon))

        notas = json.loads(res.notes)["apply"]
        assert notas.get("apply_abortado_owner") is True, notas
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_sin_aplicar WHERE motivo = 'fallo_http'"
            ).fetchone()[0]
            == 0
        ), "el ciclo desplazado no cierra la puja"
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_attempt WHERE finished_at IS NOT NULL"
            ).fetchone()[0]
            == 0
        ), "el intento queda abierto, sin sello"
