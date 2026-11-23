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
AMBIGUO = "fallo:readback_ambiguo"
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


def _directa(amazon):
    """Fabrica que construye el Aplicador contra la API simulada, sin resolver
    perfil: para las pruebas donde el perfil no es el sujeto."""

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
            tick=tick,
            guard_http=tick,
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
    ):
        self.bids: dict[str, str] = dict(bids or {})
        self.estados: dict[str, str] = dict(estados or {})
        self.vistos: list[httpx.Request] = []
        self.puts_bid: list[dict] = []
        self.puts_estado: list[dict] = []
        self.rechaza_bids = rechaza_bids
        self.falla_primer_put = falla_primer_put
        self.aplica_al_fallar = aplica_al_fallar
        self.al_put_bid = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access-1", "expires_in": 3600})
        self.vistos.append(request)
        cuerpo = json.loads(request.content) if request.content else {}
        if request.method == "GET" and request.url.path == "/v2/profiles":
            return httpx.Response(200, json={"profiles": [PERFIL_US]})
        if request.method == "POST" and request.url.path == "/sp/keywords/list":
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
    intenta y se aplica, y la primera queda incierta: sellada como ambigua y sin
    desenlace, porque solo una lectura puede resolverla, nunca otro PUT. El ciclo
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
            "SELECT resultado, ack FROM apply_attempt WHERE decision_id = %s", (incierta,)
        ).fetchone() == (AMBIGUO, None)

        res2 = _corre(conn, factory=_directa(amazon))

        assert _desenlace(conn, incierta) == "perdida", "la conciliacion la resuelve leyendo"
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
        assert ciclo.motivo_de_salto(conn, "amazon_us") is None, (
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
        assert ciclo.motivo_de_salto(conn, "amazon_us") is None, (
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
        assert ciclo.motivo_de_salto(conn, "amazon_us") is None, (
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
        salto = ciclo.motivo_de_salto(conn, "amazon_us")
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
        assert ciclo.motivo_de_salto(conn, "amazon_us") is None, (
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
