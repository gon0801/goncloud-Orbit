"""ARREGLOS 05 H: el tope de 150 pujas ordinarias por ciclo de origen.

Cada ciclo puede intentar hasta 150 pujas normales; una recuperacion no vuelve
a abrir el cupo. Lo intentado consume (ambiguo, rechazado o crash), las reversas
no, y solo la entrada/regreso de TEMPORADA quedan exentas por su marcador
congelado en la decision.

Base de datos desechable con la cadena canonica de migraciones (incluye 0099) y
HTTP 100% simulado con `httpx.MockTransport`: a Amazon no sale ninguna llamada.
"""

from __future__ import annotations

import json
import threading

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_apply import (
    FAKE_PROFILE_US,
    _decision_bid,
    _fake_credentials,
    _handler_api,
    _kw_con_state,
    _puts,
    _semilla,
)
from test_schema import _postgres_obligatorio_ausente

from app import apply_cuota
from app.apply import (
    MOTIVO_FUERA_DE_CAP,
    Aplicador,
    bids_del_ciclo,
    bids_recuperables,
)
from tests.helpers.pg import db_desde_template

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

# Contrato literal (el vocabulario es del esquema y de la app, no del test).
TOPE = 150
MOTIVO_TOPE = "tope_ciclo"
MOTIVOS_EXENTOS = ("entra_temporada_baja", "sale_temporada_baja")
JOB_KEY = "ads_optimizer:amazon_us"


# ---------------------------------------------------------------------------
# Semilla: ciclo origen + decisiones bid + intentos del ledger
# ---------------------------------------------------------------------------


def _inputs(motivo: str, *, veredicto: str = "Mover", episodio: int | None = None) -> dict:
    inputs = {
        "motor": "bid",
        "platform": "amazon_us",
        "modo": "live",
        "motivo": motivo,
        "veredicto_kind": veredicto,
        "factor": "-0.25",
        "bid_actual": "1.00",
        "bid_moneda": "USD",
        "target_acos_pct_usado": "55",
    }
    if episodio is not None:
        inputs["temporada_episodio_id"] = episodio
    return inputs


def _decision(
    conn, ciclo: int, config_id: int, entidad: int, *, motivo: str, episodio: int | None = None
) -> int:
    veredicto = "Regresar" if motivo == "sale_temporada_baja" else "Mover"
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " data_observed_at, window_start, window_end, old_value, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'bid', %s, now() - interval '40 days', CURRENT_DATE - 60,"
        " CURRENT_DATE - 30, 1.00, 0.85, 'USD', %s) RETURNING id",
        (ciclo, entidad, config_id, Json(_inputs(motivo, veredicto=veredicto, episodio=episodio))),
    ).fetchone()[0]


def _lote(
    conn,
    ids: dict,
    n: int,
    *,
    motivo: str = "banda_menos_25",
    episodio: int | None = None,
    base: int = 9100,
    ciclo: int | None = None,
) -> tuple[list[int], dict[str, str]]:
    """`n` decisiones bid del ciclo pedido, una por keyword con state."""
    ciclo = ids["ciclo_dec"] if ciclo is None else ciclo
    decisiones: list[int] = []
    remoto: dict[str, str] = {}
    for i in range(n):
        externo = str(base + i)
        kw = _kw_con_state(conn, ids["ag"], externo)
        if motivo == "banda_menos_25" and episodio is None:
            dec = _decision_bid(conn, ciclo, ids["config"], kw)
        else:
            dec = _decision(
                conn,
                ciclo,
                ids["config"],
                kw,
                motivo=motivo,
                episodio=episodio,
            )
        decisiones.append(dec)
        remoto[externo] = "1.00"
    return decisiones, remoto


def _intento(
    conn,
    decision_id: int,
    *,
    tipo: str = "normal",
    resultado: str | None = "ok",
    abierto: bool = False,
) -> int:
    """Fila del ledger como la deja el aplicador: sellada o abierta (pre-HTTP)."""
    seq = conn.execute(
        "SELECT coalesce(max(seq), 0) + 1 FROM apply_attempt WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()[0]
    if abierto:
        return conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, %s, %s, %s, true) RETURNING id",
            (decision_id, seq, tipo, Json({"keywordId": "9", "bid": "0.85"})),
        ).fetchone()[0]
    return conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
        " resultado, finished_at) VALUES (%s, %s, %s, %s, true, %s, now()) RETURNING id",
        (decision_id, seq, tipo, Json({"keywordId": "9", "bid": "0.85"}), resultado),
    ).fetchone()[0]


def _siembra(conn, *, caps: dict | None = None) -> dict:
    return _semilla(conn, caps=caps or {"ads_apply_cap_amazon_us_bid": 1000})


def _toma_claim(conn, owner: str = "test:h") -> None:
    conn.execute(
        "INSERT INTO ads_optimizer_lock (job_key, owner, claimed_at, heartbeat_at)"
        " VALUES (%s, %s, now(), now())"
        " ON CONFLICT (job_key) DO UPDATE SET owner = EXCLUDED.owner,"
        " claimed_at = EXCLUDED.claimed_at, heartbeat_at = EXCLUDED.heartbeat_at",
        (JOB_KEY, owner),
    )
    conn.commit()


def _aplicador(
    conn,
    handler,
    *,
    ejecutor: int,
    origen: int | None = None,
    owner: str = "test:h",
) -> Aplicador:
    return Aplicador(
        conn,
        platform="amazon_us",
        profile_id=FAKE_PROFILE_US,
        credentials=_fake_credentials(),
        cycle_id_ejecutor=ejecutor,
        owner=owner,
        job_key=JOB_KEY,
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
        ciclo_origen=origen,
    )


def _intentos(conn, *, ciclo: int, tipo: str = "normal") -> int:
    return conn.execute(
        "SELECT count(*) FROM apply_attempt a JOIN decision d ON d.id = a.decision_id"
        " WHERE d.cycle_id = %s AND d.kind = 'bid' AND a.tipo = %s",
        (ciclo, tipo),
    ).fetchone()[0]


def _sin_aplicar(conn, decision_id: int) -> tuple | None:
    return conn.execute(
        "SELECT motivo, detalle FROM decision_sin_aplicar WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()


def _sella_origen(conn, ciclo: int) -> None:
    """Cierra el origen como la TX3: done, con conteo y sello de decisiones."""
    conn.execute(
        "UPDATE optimizer_cycle SET status = 'done', finished_at = now(),"
        " decisions_count = (SELECT count(*) FROM decision WHERE cycle_id = %s),"
        " decision_committed_at = now() WHERE id = %s",
        (ciclo, ciclo),
    )
    conn.commit()


def _ejecutor(conn, origen: int) -> int:
    ejecutor = conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform, status, resume_of)"
        " VALUES ('ads_optimizer', 'live', 'amazon_us', 'running', %s) RETURNING id",
        (origen,),
    ).fetchone()[0]
    conn.commit()
    return ejecutor


def _handler_que_cae(remoto: dict[str, str], *, en: str):
    """Handler con el shape del probe: LIST normal, PUT 207 y la caida pedida.

    `en='put'` revienta antes de que el PUT salga (despues de la lectura fresca);
    `en='readback'` revienta en la lectura de vuelta, ya con el PUT enviado."""
    listas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "x", "expires_in": 3600})
        if request.method == "PUT":
            if en == "put":
                raise RuntimeError("caida antes de que el PUT salga")
            obj = json.loads(request.content)["keywords"][0]
            remoto[str(obj["keywordId"])] = str(obj["bid"])
            return httpx.Response(207, json={"ack": obj})
        listas["n"] += 1
        if en == "readback" and listas["n"] > 1:
            raise RuntimeError("caida despues del PUT")
        filas = [{"keywordId": ext, "bid": bid, "state": "ENABLED"} for ext, bid in remoto.items()]
        return httpx.Response(200, json={"keywords": filas, "totalResults": len(filas)})

    return handler


# ---------------------------------------------------------------------------
# H.1: reserva e intencion; el tope cuenta del ledger
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_h_151_ordinarias_intentan_150_y_la_151_lleva_el_motivo():
    """151 pujas ordinarias elegibles: como maximo 150 intentos y la 151 recibe
    `tope_ciclo` sin abrir otro ciclo ni gastar cupo diario."""
    with db_desde_template("orbit_a05h_151") as (conn, _dsn):
        ids = _siembra(conn)
        decisiones, remoto = _lote(conn, ids, TOPE + 1)
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert len(decisiones) == 151
        assert res.aplicadas == TOPE
        assert res.descartadas == [MOTIVO_TOPE], "solo la 151 se queda sin intento"
        assert len(_puts(vistos)) == TOPE, "150 PUT, no 151"
        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == TOPE
        fila = _sin_aplicar(conn, decisiones[-1])
        assert fila is not None, "la 151 no puede quedar sin desenlace auditable"
        assert fila[0] == MOTIVO_TOPE
        assert fila[1] == {"usados": TOPE, "tope": TOPE}, fila[1]
        assert (
            conn.execute(
                "SELECT count(*) FROM optimizer_cycle WHERE motor = 'ads_optimizer'"
            ).fetchone()[0]
            == 2
        ), "no se abre otro ciclo para eludir el tope"


@_skip_db
def test_arreglos05_h_149_confirmadas_mas_una_ambigua_agotan_el_cupo():
    """149 selladas + 1 ambigua (sin sello) = 150: lo intentado consume."""
    with db_desde_template("orbit_a05h_ambiguo") as (conn, _dsn):
        ids = _siembra(conn)
        decisiones, remoto = _lote(conn, ids, TOPE + 1)
        for dec in decisiones[: TOPE - 1]:
            _intento(conn, dec)
        ambigua = _intento(conn, decisiones[TOPE - 1], abierto=True)
        assert ambigua > 0
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 0
        assert res.descartadas == [MOTIVO_TOPE]
        assert _puts(vistos) == [], "cero HTTP: el cupo ya esta agotado"
        assert _sin_aplicar(conn, decisiones[-1])[0] == MOTIVO_TOPE


@_skip_db
def test_arreglos05_h_149_intentos_permiten_el_intento_150():
    """Con 149 intentos del origen, la siguiente puja ordinaria entra y su fila
    del ledger nace antes del HTTP (150 exactos)."""
    with db_desde_template("orbit_a05h_150") as (conn, _dsn):
        ids = _siembra(conn)
        decisiones, remoto = _lote(conn, ids, TOPE)
        for dec in decisiones[: TOPE - 1]:
            _intento(conn, dec)
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids([bids_del_ciclo(conn, ids["ciclo_dec"])[-1]], escalera_global="live")

        assert res.aplicadas == 1
        assert res.descartadas == []
        assert len(_puts(vistos)) == 1
        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == TOPE


@_skip_db
def test_arreglos05_h_intento_rechazado_y_ambiguo_sellado_tambien_cuentan():
    """Un intento sellado como rechazo y uno sellado con lectura ambigua
    consumen igual que el confirmado."""
    with db_desde_template("orbit_a05h_sellados") as (conn, _dsn):
        ids = _siembra(conn)
        decisiones, remoto = _lote(conn, ids, TOPE + 1)
        for dec in decisiones[: TOPE - 2]:
            _intento(conn, dec)
        _intento(conn, decisiones[TOPE - 2], resultado="fallo http 400: rechazo")
        _intento(conn, decisiones[TOPE - 1], resultado="fallo:readback_ambiguo")
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.descartadas == [MOTIVO_TOPE]
        assert _puts(vistos) == []
        assert _sin_aplicar(conn, decisiones[-1])[0] == MOTIVO_TOPE


# ---------------------------------------------------------------------------
# H.2: TEMPORADA exenta por marcador, recuperacion y reversas
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_h_entrada_y_regreso_de_temporada_quedan_fuera_del_cupo():
    """Con el cupo agotado, la entrada y el regreso de TEMPORADA se envian
    igual (marcador congelado); la ordinaria del mismo lote se rechaza."""
    with db_desde_template("orbit_a05h_temp") as (conn, _dsn):
        ids = _siembra(conn)
        ordinarias, remoto = _lote(conn, ids, TOPE)
        for dec in ordinarias:
            _intento(conn, dec)
        entrada, remoto_e = _lote(
            conn, ids, 1, motivo="entra_temporada_baja", episodio=7, base=9600
        )
        regreso, remoto_r = _lote(conn, ids, 1, motivo="sale_temporada_baja", episodio=7, base=9700)
        pendiente, remoto_p = _lote(conn, ids, 1, base=9800)
        remoto.update({**remoto_e, **remoto_r, **remoto_p})
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 2, "entrada y regreso no compiten por el cupo"
        assert res.descartadas == [MOTIVO_TOPE]
        assert len(_puts(vistos)) == 2
        assert _sin_aplicar(conn, pendiente[0])[0] == MOTIVO_TOPE
        assert _sin_aplicar(conn, entrada[0]) is None
        assert _sin_aplicar(conn, regreso[0]) is None
        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == TOPE + 2


@_skip_db
def test_arreglos05_h_ajuste_y_recupera_ordinaria_cuentan():
    """Un ajuste a media baja y un `Regresar` ordinario (Recupera) no son
    transiciones de TEMPORADA: con el cupo agotado no se envian."""
    with db_desde_template("orbit_a05h_ajuste") as (conn, _dsn):
        ids = _siembra(conn)
        ordinarias, remoto = _lote(conn, ids, TOPE)
        for dec in ordinarias:
            _intento(conn, dec)
        ajuste, remoto_a = _lote(
            conn, ids, 1, motivo="ajusta_temporada_baja", episodio=7, base=9650
        )
        recupera, remoto_r = _lote(conn, ids, 1, motivo="regreso_por_desplome", base=9750)
        remoto.update({**remoto_a, **remoto_r})
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 0
        assert res.descartadas == [MOTIVO_TOPE, MOTIVO_TOPE]
        assert _puts(vistos) == []
        assert _sin_aplicar(conn, ajuste[0])[0] == MOTIVO_TOPE
        assert _sin_aplicar(conn, recupera[0])[0] == MOTIVO_TOPE


@_skip_db
def test_arreglos05_h_recuperacion_no_concede_otro_cupo():
    """Dos origenes: uno con 150 intentos (el ejecutor no envia) y otro con 149
    (el ejecutor completa el 150 contra su origen, no contra si mismo)."""
    with db_desde_template("orbit_a05h_recupera") as (conn, _dsn):
        ids = _siembra(conn)
        lleno, remoto = _lote(conn, ids, TOPE + 1)
        for dec in lleno[:TOPE]:
            _intento(conn, dec)
        _sella_origen(conn, ids["ciclo_dec"])
        ejecutor_lleno = _ejecutor(conn, ids["ciclo_dec"])

        origen_b = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform)"
            " VALUES ('ads_optimizer', 'live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        parcial, remoto_b = _lote(conn, ids, TOPE, base=9500, ciclo=origen_b)
        for dec in parcial[: TOPE - 1]:
            _intento(conn, dec)
        _sella_origen(conn, origen_b)
        ejecutor_parcial = _ejecutor(conn, origen_b)
        remoto.update(remoto_b)
        _toma_claim(conn)

        handler, vistos = _handler_api(remoto)
        res = _aplicador(
            conn, handler, ejecutor=ejecutor_lleno, origen=ids["ciclo_dec"]
        ).aplica_bids(bids_recuperables(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 0
        assert res.descartadas == [MOTIVO_TOPE]
        assert _puts(vistos) == []
        assert conn.execute(
            "SELECT cycle_id, motivo FROM decision_sin_aplicar WHERE decision_id = %s",
            (lleno[-1],),
        ).fetchone() == (ejecutor_lleno, MOTIVO_TOPE), "el desenlace lo registra el ejecutor"

        handler_b, vistos_b = _handler_api(remoto)
        res_b = _aplicador(conn, handler_b, ejecutor=ejecutor_parcial, origen=origen_b).aplica_bids(
            bids_recuperables(conn, origen_b), escalera_global="live"
        )

        assert res_b.aplicadas == 1
        assert len(_puts(vistos_b)) == 1
        assert _intentos(conn, ciclo=origen_b) == TOPE
        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == TOPE


@_skip_db
def test_arreglos05_h_reversas_no_consumen_cupo():
    """Una reversa no es un intento ordinario: 149 normales + reversas siguen
    dejando entrar la numero 150."""
    with db_desde_template("orbit_a05h_reversa") as (conn, _dsn):
        ids = _siembra(conn)
        decisiones, remoto = _lote(conn, ids, TOPE)
        for dec in decisiones[: TOPE - 1]:
            _intento(conn, dec)
        for dec in decisiones[:3]:
            _intento(conn, dec, tipo="reversa")
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids([bids_del_ciclo(conn, ids["ciclo_dec"])[-1]], escalera_global="live")

        assert res.aplicadas == 1
        assert len(_puts(vistos)) == 1
        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == TOPE
        assert _intentos(conn, ciclo=ids["ciclo_dec"], tipo="reversa") == 3


# ---------------------------------------------------------------------------
# Concurrencia y caidas
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_h_dos_procesos_no_superan_150():
    """Dos aplicadores del MISMO origen, en dos conexiones: el cerrojo del
    origen serializa conteo + intencion y nunca nacen 151 intentos."""
    with db_desde_template("orbit_a05h_dos") as (conn, dsn):
        ids = _siembra(conn)
        decisiones, remoto = _lote(conn, ids, TOPE + 1)
        for dec in decisiones[: TOPE - 1]:
            _intento(conn, dec)
        _toma_claim(conn)
        pendientes = bids_del_ciclo(conn, ids["ciclo_dec"])[TOPE - 1 :]

        otra = psycopg.connect(dsn)
        otra.execute("SET TIME ZONE 'UTC'")
        barrera = threading.Barrier(2)
        resultados: dict[str, object] = {}

        def corre(nombre: str, conexion, decision, owner: str) -> None:
            try:
                handler, vistos = _handler_api(dict(remoto))
                ap = _aplicador(
                    conexion,
                    handler,
                    ejecutor=ids["ciclo_ejec"],
                    origen=ids["ciclo_dec"],
                    owner=owner,
                )
                barrera.wait(timeout=10)
                resultados[nombre] = (
                    ap.aplica_bids([decision], escalera_global="live"),
                    len(_puts(vistos)),
                )
            except BaseException as exc:  # noqa: BLE001 - se re-lanza en el hilo principal
                resultados[nombre] = exc

        hilos = [
            threading.Thread(target=corre, args=("a", conn, pendientes[0], "test:h")),
            threading.Thread(target=corre, args=("b", otra, pendientes[1], "test:h2")),
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)
        otra.close()

        for nombre in ("a", "b"):
            assert not isinstance(resultados[nombre], BaseException), resultados[nombre]
        aplicadas = sum(r[0].aplicadas for r in resultados.values())
        put = sum(r[1] for r in resultados.values())
        descartadas = [r[0].descartadas for r in resultados.values()]
        assert aplicadas == 1, descartadas
        assert put == 1, descartadas
        assert sorted(m for d in descartadas for m in d) == [MOTIVO_TOPE]
        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == TOPE


@_skip_db
def test_arreglos05_h_caida_antes_del_http_consume_cupo():
    """La fila del ledger se compromete antes del PUT: una caida antes del HTTP
    deja el intento abierto y la siguiente puja ya no entra."""
    with db_desde_template("orbit_a05h_caida_pre") as (conn, _dsn):
        ids = _siembra(conn)
        decisiones, remoto = _lote(conn, ids, TOPE + 1)
        for dec in decisiones[: TOPE - 1]:
            _intento(conn, dec)

        with pytest.raises(RuntimeError, match="caida antes"):
            _aplicador(
                conn,
                _handler_que_cae(remoto, en="put"),
                ejecutor=ids["ciclo_ejec"],
                origen=ids["ciclo_dec"],
            ).aplica_bids(
                [bids_del_ciclo(conn, ids["ciclo_dec"])[TOPE - 1]], escalera_global="live"
            )

        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == TOPE, "el intento de la caida cuenta"
        abiertos = conn.execute(
            "SELECT count(*) FROM apply_attempt WHERE finished_at IS NULL"
        ).fetchone()[0]
        assert abiertos == 1, "el intento de la caida queda abierto y cuenta"

        handler, vistos = _handler_api(remoto)
        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids([bids_del_ciclo(conn, ids["ciclo_dec"])[TOPE]], escalera_global="live")
        assert res.descartadas == [MOTIVO_TOPE]
        assert _puts(vistos) == []


@_skip_db
def test_arreglos05_h_caida_despues_del_http_tambien_consume_cupo():
    """Si la lectura de vuelta se cae, el intento ya salio: cuenta para el
    tope y no se repite."""
    with db_desde_template("orbit_a05h_caida_post") as (conn, _dsn):
        ids = _siembra(conn)
        decisiones, remoto = _lote(conn, ids, TOPE + 1)
        for dec in decisiones[: TOPE - 1]:
            _intento(conn, dec)

        with pytest.raises(RuntimeError, match="caida despues"):
            _aplicador(
                conn,
                _handler_que_cae(remoto, en="readback"),
                ejecutor=ids["ciclo_ejec"],
                origen=ids["ciclo_dec"],
            ).aplica_bids(
                [bids_del_ciclo(conn, ids["ciclo_dec"])[TOPE - 1]], escalera_global="live"
            )

        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == TOPE, "el PUT ya salio: cuenta"
        handler, vistos = _handler_api(remoto)
        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids([bids_del_ciclo(conn, ids["ciclo_dec"])[TOPE]], escalera_global="live")
        assert res.descartadas == [MOTIVO_TOPE]
        assert _puts(vistos) == []


# ---------------------------------------------------------------------------
# Cupo diario y prioridad de TEMPORADA siguen vigentes
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_h_cupo_diario_y_prioridad_de_temporada_siguen_vigentes():
    """Con cupo diario 1, la entrada de TEMPORADA (prioridad propria) entra
    primero y la ordinaria queda fuera del cupo diario, no del tope."""
    with db_desde_template("orbit_a05h_diario") as (conn, _dsn):
        ids = _siembra(conn, caps={"ads_apply_cap_amazon_us_bid": 1})
        ordinaria, remoto_o = _lote(conn, ids, 1, base=9200)
        entrada, remoto_e = _lote(
            conn, ids, 1, motivo="entra_temporada_baja", episodio=3, base=9300
        )
        remoto = {**remoto_o, **remoto_e}
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 1
        assert res.descartadas == [MOTIVO_FUERA_DE_CAP]
        assert _sin_aplicar(conn, entrada[0]) is None
        assert _sin_aplicar(conn, ordinaria[0])[0] == MOTIVO_FUERA_DE_CAP
        assert len(_puts(vistos)) == 1


@_skip_db
def test_arreglos05_h_cupo_diario_apagado_tambien_frena_la_exenta():
    """El cupo diario manda: con cap 0 ni la entrada de TEMPORADA se envia."""
    with db_desde_template("orbit_a05h_cap0") as (conn, _dsn):
        ids = _siembra(conn, caps={"ads_apply_cap_amazon_us_bid": 0})
        entrada, remoto = _lote(conn, ids, 1, motivo="entra_temporada_baja", episodio=3, base=9400)
        _toma_claim(conn)
        handler, vistos = _handler_api(remoto)

        res = _aplicador(
            conn, handler, ejecutor=ids["ciclo_ejec"], origen=ids["ciclo_dec"]
        ).aplica_bids(bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")

        assert res.aplicadas == 0
        assert res.descartadas == [MOTIVO_FUERA_DE_CAP]
        assert _puts(vistos) == []
        assert _sin_aplicar(conn, entrada[0])[0] == MOTIVO_FUERA_DE_CAP
        assert _intentos(conn, ciclo=ids["ciclo_dec"]) == 0


def test_arreglos05_h_fuente_unica_del_tope_y_del_motivo():
    """El tope y el motivo son constantes de la app; el vocabulario cerrado los
    incluye y el marcador de temporada exento es el congelado por TEMPORADA."""
    assert apply_cuota.TOPE_PUJAS_ORDINARIAS_CICLO == TOPE
    assert apply_cuota.MOTIVO_TOPE_CICLO == MOTIVO_TOPE
    assert MOTIVO_TOPE in apply_cuota.MOTIVOS_SIN_APLICAR
    assert apply_cuota.puja_exenta_del_cupo(_inputs("entra_temporada_baja", episodio=1))
    assert apply_cuota.puja_exenta_del_cupo(_inputs("sale_temporada_baja", episodio=1))
    assert not apply_cuota.puja_exenta_del_cupo(_inputs("entra_temporada_baja"))
    assert not apply_cuota.puja_exenta_del_cupo(_inputs("ajusta_temporada_baja", episodio=1))
    assert not apply_cuota.puja_exenta_del_cupo(_inputs("banda_menos_25"))
