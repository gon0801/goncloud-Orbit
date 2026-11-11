"""ORBIT 07 M1: recorridos de la proteccion de venta reciente.

Ciclo: una hoja con pedido reciente no recibe pausa; la subpolitica abre un
episodio con ancla y vencimiento fijos. Cola: la pausa ya encolada espera sin
cobrar cuota y conserva su fila; al vencer con el ancla corregida a cero, la
pausa procede. PostgreSQL desechable; Amazon es el transporte falso de
test_apply_cola. Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from test_apply_cola import (
    _aplicador,
    _decision_corte,
    _encola_fila,
    _fechas,
    _handler_cortes,
    _metrica,
    _mutaciones,
    _payload_pause,
    _semilla,
)
from test_apply_cola import (
    _db_temporal as _db_cola,
)
from test_cycle import (
    DECIDED_AT,
    _corre,
    _decisions_de,
    _entidad,
    _estado,
    _obs,
    _rango,
    _siembra_maestra,
    _siembra_r9,
)
from test_cycle import _db_temporal as _db_ciclo
from test_cycle import (
    _metrica as _metrica_ciclo,
)
from test_schema import _test_dsn

from app import apply_cola, cli_productos
from app import cycle as ciclo
from app.optimizer import cortes as cortes_mod
from app.optimizer import goals as g
from app.optimizer import proteccion as subproteccion
from app.optimizer import windows
from app.optimizer.caso import EpisodioProteccion
from app.proteccion_venta import CacheProteccion, lee_cache

_skip_db = pytest.mark.skipif(
    __import__("test_schema")._postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _pausa_de(conn, cycle_id: int, entidad: int):
    return conn.execute(
        "SELECT id, inputs FROM decision WHERE cycle_id = %s AND ad_entity_id = %s"
        " AND kind = 'pause'",
        (cycle_id, entidad),
    ).fetchall()


def _episodios(conn, entidad: int):
    return conn.execute(
        "SELECT ancla, vence_el FROM proteccion_venta_reciente"
        " WHERE ad_entity_id = %s ORDER BY ancla",
        (entidad,),
    ).fetchall()


def _used_pause(conn) -> int:
    fila = conn.execute(
        "SELECT used FROM apply_quota_state WHERE motor = %s",
        ("ads_optimizer:amazon_us:pause",),
    ).fetchone()
    return fila[0] if fila is not None else 0


@_skip_db
def test_ciclo_con_venta_reciente_no_pausa_y_abre_episodio():
    """El kw_pause maduro calificaria la pausa por umbral; el pedido de 08-20
    la suprime, el juicio de la hoja sigue y el episodio queda persistido."""
    with _db_ciclo("orbit_m1_ciclo") as (conn, _c):
        ids = _siembra_maestra(conn, settings={"ads_proteccion_venta": "proteccion_venta_v1"})
        run = _run(conn)
        # Cobertura completa de D-9..D-1 (08-13..08-21) con el pedido el 08-20.
        for fecha in _fechas(dt.date(2026, 8, 20), dt.date(2026, 8, 21)):
            _metrica_ciclo(
                conn,
                run,
                ids["kw_pause"],
                fecha,
                _obs(fecha),
                cost="0.10",
                ad_revenue="0.10",
                clicks=1,
                orders=0,
                impressions=10,
            )
        _metrica_ciclo(
            conn,
            run,
            ids["kw_pause"],
            dt.date(2026, 8, 20),
            _obs(dt.date(2026, 8, 21)),
            cost="0.10",
            ad_revenue="1.00",
            clicks=1,
            orders=1,
            impressions=10,
        )

        res = _corre(conn)

        assert res.status == "done"
        assert _pausa_de(conn, res.cycle_id, ids["kw_pause"]) == []
        assert _episodios(conn, ids["kw_pause"]) == [(dt.date(2026, 8, 20), dt.date(2026, 8, 30))]
        assert "venta_reciente" in res.notes


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[
        0
    ]


@_skip_db
def test_ciclo_permite_la_puja_durante_la_espera_sin_duplicar_la_pausa():
    """Una fila released de pausa con episodio activo ya no congela la hoja:
    el mismo evaluador mueve la puja y el ciclo NO crea otra pausa."""
    with _db_ciclo("orbit_m1_ciclo_puja") as (conn, _c):
        ids = _siembra_r9(conn)
        kw = ids["kw"]
        config = conn.execute("SELECT id FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[
            0
        ]
        dec = _decision_corte(
            conn,
            _ciclo_id(conn),
            config,
            kw,
            "pause",
            decided_at=DECIDED_AT - dt.timedelta(days=3),
        )
        conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo,"
            " estado, vence_el, request_payload) VALUES ('amazon_us', %s, 'pause', %s,"
            " 'live', 'pending_veto', %s, '{}'::jsonb)",
            (kw, dec, DECIDED_AT - dt.timedelta(days=1)),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = %s WHERE decision_id = %s",
            (DECIDED_AT - dt.timedelta(hours=2), dec),
        )
        conn.execute(
            "INSERT INTO proteccion_venta_reciente (platform, ad_entity_id, version,"
            " ancla, vence_el, abierta_el, observado_el) VALUES ('amazon_us', %s,"
            " 'proteccion_venta_v1', %s, %s, %s, %s)",
            (
                kw,
                dt.date(2026, 8, 13),
                dt.date(2026, 8, 23),
                DECIDED_AT,
                DECIDED_AT - dt.timedelta(hours=1),
            ),
        )

        res = _corre(conn)

        assert res.status == "done"
        filas = _decisions_de(conn, res.cycle_id)
        kinds = {(f[0], f[1]) for f in filas}
        assert (kw, "bid") in kinds, "la puja se evaluo y se movio"
        assert (kw, "pause") not in kinds, "no se duplica la pausa"
        bides = [f for f in filas if f[0] == kw and f[1] == "bid"]
        assert bides[0][9]["motivo"] == "gasto_sin_venta"
        assert bides[0][9]["factor"] == "-0.12"


def _ciclo_id(conn) -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]


@_skip_db
def test_cola_espera_la_proteccion_sin_cuota_ni_claim():
    with _db_cola("orbit_m1_cola_espera") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        d = ids["ahora"]
        _siembra_maduros_y_recientes(conn, ids, d)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, _vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=True,
        )

        assert res.espera_proteccion == 1
        assert res.aplicadas == 0 and res.sin_quota == 0
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("released", None)
        assert _episodios(conn, ids["kw"]) == [
            (d.date() - dt.timedelta(days=4), d.date() + dt.timedelta(days=6))
        ]
        assert _used_pause(conn) == 0, "esperar no cobra cuota"
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_application WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            == 0
        )
        sin_aplicar = conn.execute(
            "SELECT motivo FROM decision_sin_aplicar WHERE decision_id = %s", (dec,)
        ).fetchall()
        assert ("espera_proteccion",) in sin_aplicar


@_skip_db
def test_arreglos01_i_apagar_proteccion_descarta_la_espera():
    """D-H1: una pausa released que esperaba por venta reciente se descarta al
    apagar la proteccion, ANTES del claim: queda discarded/proteccion_apagada,
    sin cobro de cuota, sin PUT y sin decision_application. Sin el arreglo, el
    mismo barrido la revalida con la semantica historica y la aplica de golpe."""
    with _db_cola("orbit_m1_cola_apagada_descarta") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        d = ids["ahora"]
        _siembra_maduros_y_recientes(conn, ids, d)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=True,
        )
        assert res.espera_proteccion == 1
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "released"
        )

        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=False,
        )

        assert res.descartadas == ["proteccion_apagada"]
        assert res.aplicadas == 0 and res.sin_quota == 0
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", "proteccion_apagada")
        assert _used_pause(conn) == 0, "descartar no cobra cuota"
        assert _mutaciones(vistos) == [], "descartar no manda ningun PUT"
        assert (
            conn.execute(
                "SELECT count(*) FROM decision_application WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            == 0
        )


def _args_proteccion_off():
    return SimpleNamespace(
        platform="amazon_us",
        meta=None,
        pausa=None,
        clics=None,
        gasto=None,
        economia=None,
        bid_economia=None,
        proteccion="off",
    )


def _dsn_admin_de(conn) -> str:
    db = conn.execute("SELECT current_database()").fetchone()[0]
    return psycopg.conninfo.make_conninfo(_test_dsn(), dbname=db)


@_skip_db
def test_arreglos01_i_descarte_solo_si_el_ultimo_motivo_es_la_espera():
    """F1: una espera_proteccion historica no basta. Si un ciclo posterior dejo
    otro motivo como el ULTIMO de la decision (aqui sin_quota), apagar la
    proteccion no descarta la fila: se procesa con la semantica historica."""
    with _db_cola("orbit_m1_ultimo_motivo") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        d = ids["ahora"]
        _siembra_maduros_y_recientes(conn, ids, d)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, _vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
        apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=True,
        )
        ciclo_posterior = _ciclo_id(conn)
        conn.execute(
            "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo)"
            " VALUES (%s, %s, 'sin_quota')",
            (dec, ciclo_posterior),
        )

        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=False,
        )

        assert res.descartadas == [], "la espera vieja no se descarta"
        assert res.aplicadas == 1
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "applied"
        )
        assert _used_pause(conn) == 1


@_skip_db
@pytest.mark.parametrize("motivo", ["sin_respuesta", "espera_target"])
def test_arreglos01_i_descarte_ignora_motivos_transitorios(motivo):
    """F5: un LIST de re-validacion muerto (sin_respuesta) o una espera sin
    target confiable (espera_target) no tapan la espera por proteccion. Si el
    ultimo motivo CONCLUYENTE fue espera_proteccion, apagar la proteccion
    descarta la pausa."""
    with _db_cola(f"orbit_m1_transitorio_{motivo}") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        d = ids["ahora"]
        _siembra_maduros_y_recientes(conn, ids, d)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, _vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
        apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=True,
        )
        ciclo_posterior = _ciclo_id(conn)
        conn.execute(
            "INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo) VALUES (%s, %s, %s)",
            (dec, ciclo_posterior, motivo),
        )

        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=False,
        )

        assert res.descartadas == ["proteccion_apagada"]
        assert res.aplicadas == 0
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "discarded"
        )
        assert _used_pause(conn) == 0


@_skip_db
def test_arreglos01_i_cli_apaga_proteccion_y_descarta_en_una_transaccion(monkeypatch):
    """F2: el CLI guarda la config y descarta las esperas de ambas plataformas
    en una sola transaccion, e informa cuantas descarto."""
    with _db_cola("orbit_m1_cli_atomico") as conn:
        ids = _semilla(
            conn,
            caps={
                "ads_apply_cap_amazon_us_pause": 2,
                "ads_proteccion_venta": "proteccion_venta_v1",
            },
        )
        d = ids["ahora"]
        _siembra_maduros_y_recientes(conn, ids, d)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
        apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=True,
        )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))

        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            rc = cli_productos.corre(_args_proteccion_off())

        assert rc == 0
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", "proteccion_apagada")
        settings = conn.execute(
            "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert "ads_proteccion_venta" not in settings
        assert _used_pause(conn) == 0
        assert _mutaciones(vistos) == []
        assert "1 pausas en espera por venta reciente descartadas" in salida.getvalue()


@_skip_db
def test_arreglos01_i_cli_si_falla_el_descarte_no_guarda_la_config(monkeypatch):
    """F2: si el descarte falla, la config nueva NO queda guardada: el commit es
    uno solo al final de la transaccion config + descarte."""
    with _db_cola("orbit_m1_cli_rollback") as conn:
        ids = _semilla(
            conn,
            caps={
                "ads_apply_cap_amazon_us_pause": 2,
                "ads_proteccion_venta": "proteccion_venta_v1",
            },
        )
        d = ids["ahora"]
        _siembra_maduros_y_recientes(conn, ids, d)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, _vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
        apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=True,
        )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_admin_de(conn))

        def _falla(conn, platform):
            raise RuntimeError("fallo simulado del descarte")

        monkeypatch.setattr(apply_cola, "descarta_esperas_proteccion", _falla)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = cli_productos.corre(_args_proteccion_off())

        assert rc == 1
        ids_config = [f[0] for f in conn.execute("SELECT id FROM config_version ORDER BY id")]
        assert ids_config == [ids["config"]]
        settings = conn.execute(
            "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert "ads_proteccion_venta" in settings
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "released"
        )


@_skip_db
def test_cola_al_vencer_con_ancla_corregida_aplica_y_cobra():
    with _db_cola("orbit_m1_cola_aplica") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        d = ids["ahora"]
        _siembra_maduros_y_recientes(conn, ids, d)
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, _vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
        apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=True,
        )
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "released"
        )

        # El ancla se corrige a cero y el episodio vence once dias despues.
        _metrica(
            conn,
            ids["run"],
            ids["kw"],
            d.date() - dt.timedelta(days=4),
            clicks=1,
            cost=0,
            orders=0,
            observed=d + dt.timedelta(days=1),
        )
        ahora2 = d + dt.timedelta(days=11)
        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=ahora2,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
            proteccion_activa=True,
        )

        assert res.aplicadas == 1, res.descartadas
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0]
            == "applied"
        )
        assert (
            conn.execute(
                "SELECT verify_ok FROM decision_application WHERE decision_id = %s", (dec,)
            ).fetchone()[0]
            is True
        )
        assert _used_pause(conn) == 1


def _siembra_maduros_y_recientes(conn, ids, d) -> None:
    """Ventana madura con 126 clics / 54 USD sin pedidos; D-9..D-1 completo con
    un pedido el D-4 (el disparador de la proteccion)."""
    for fecha in _fechas(d.date() - dt.timedelta(days=28), d.date() - dt.timedelta(days=11)):
        _metrica(conn, ids["run"], ids["kw"], fecha, clicks=7, cost=3, orders=0)
    for fecha in _fechas(d.date() - dt.timedelta(days=9), d.date() - dt.timedelta(days=1)):
        _metrica(conn, ids["run"], ids["kw"], fecha, clicks=1, cost=0, orders=0)
    _metrica(
        conn,
        ids["run"],
        ids["kw"],
        d.date() - dt.timedelta(days=4),
        clicks=1,
        cost=0,
        orders=1,
        observed=d,
    )


def test_cache_proteccion_vencida_no_se_confunde_con_veto():
    """La sonda de Astra: episodio vencido (ancla 10-05, vence 10-15) el 10-16.
    La cache no lo puede tratar como si no existiera: esa conclusion era el
    veto indebido que congelaba las pujas."""
    cache = CacheProteccion(
        plataforma="amazon_us",
        evaluado_el=dt.date(2026, 10, 16),
        cubierta=True,
        _recientes={},
        _episodios={1: EpisodioProteccion(dt.date(2026, 10, 5), dt.date(2026, 10, 15))},
    )

    assert cache.tiene_proteccion(1) is True
    assert cache.tiene_proteccion(2) is False


def _siembra_proteccion_vencida(conn) -> int:
    """Hoja con la ultima fila el 08-14 (ventana de cortes hasta 08-11) y un
    episodio ancla 08-12 que vence 08-22: el 08-22 esta vencido e
    indeterminado (el ancla aun no entra a la ventana madura)."""
    ids = _siembra_r9(conn)
    kw = _entidad(
        conn,
        "amazon_us",
        "keyword",
        "9503",
        parent=ids["ag"],
        match_type="EXACT",
        keyword_text="kw vencida",
    )
    _estado(
        conn,
        kw,
        synced_at=DECIDED_AT - dt.timedelta(hours=4),
        current_bid=Decimal("1.00"),
        bid_currency="USD",
    )
    run = _run(conn)
    for i, fecha in enumerate(_rango(dt.date(2026, 7, 13), dt.date(2026, 8, 14))):
        _metrica_ciclo(
            conn,
            run,
            kw,
            fecha,
            _obs(fecha),
            cost="1.50",
            ad_revenue="0",
            clicks=1 if i < 10 else 0,
            orders=0,
            impressions=10,
        )
    config = conn.execute("SELECT id FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[0]
    dec = _decision_corte(
        conn,
        _ciclo_id(conn),
        config,
        kw,
        "pause",
        decided_at=DECIDED_AT - dt.timedelta(days=3),
    )
    conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo,"
        " estado, vence_el, request_payload) VALUES ('amazon_us', %s, 'pause', %s,"
        " 'live', 'pending_veto', %s, '{}'::jsonb)",
        (kw, dec, DECIDED_AT - dt.timedelta(days=1)),
    )
    conn.execute(
        "UPDATE apply_queue SET estado = 'released', released_at = %s WHERE decision_id = %s",
        (DECIDED_AT - dt.timedelta(hours=2), dec),
    )
    conn.execute(
        "INSERT INTO proteccion_venta_reciente (platform, ad_entity_id, version,"
        " ancla, vence_el, abierta_el, observado_el) VALUES ('amazon_us', %s,"
        " 'proteccion_venta_v1', %s, %s, %s, %s)",
        (
            kw,
            dt.date(2026, 8, 12),
            dt.date(2026, 8, 22),
            DECIDED_AT - dt.timedelta(days=1),
            DECIDED_AT - dt.timedelta(days=2),
        ),
    )
    return kw


@_skip_db
def test_ciclo_proteccion_vencida_no_congela_la_puja():
    """Regresion del hallazgo 1: una fila released con un episodio vencido e
    indeterminado no se confunde con veto; la puja autorizada se evalua y el
    ancla/vencimiento originales no se tocan."""
    with _db_ciclo("orbit_m1_vencida") as (conn, _c):
        kw = _siembra_proteccion_vencida(conn)

        res = _corre(conn)

        assert res.status == "done"
        filas = _decisions_de(conn, res.cycle_id)
        bides = [f for f in filas if f[0] == kw and f[1] == "bid"]
        assert bides, "la puja autorizada se evalua pese a la espera vencida"
        assert bides[0][9]["motivo"] == "gasto_sin_venta"
        assert bides[0][9]["factor"] == "-0.12"
        assert not [f for f in filas if f[0] == kw and f[1] == "pause"]
        assert _episodios(conn, kw) == [(dt.date(2026, 8, 12), dt.date(2026, 8, 22))]


@_skip_db
def test_sombra_sin_target_aplica_la_proteccion_en_el_juicio_hipotetico():
    """F1 (DeepSeek): el juicio hipotetico de una hoja sin target usa los
    mismos insumos que el camino live. Con la regla activa y una venta
    reciente, la pausa por umbral se suprime tambien en la sombra."""
    with _db_ciclo("orbit_m1_sombra_sin_target") as (conn, _c):
        ids = _siembra_maestra(conn, settings={"ads_proteccion_venta": "proteccion_venta_v1"})
        run = _run(conn)
        for fecha in _fechas(dt.date(2026, 8, 20), dt.date(2026, 8, 21)):
            _metrica_ciclo(
                conn,
                run,
                ids["kw_pause"],
                fecha,
                _obs(fecha),
                cost="0.10",
                ad_revenue="0.10",
                clicks=1,
                orders=0,
                impressions=10,
            )
        _metrica_ciclo(
            conn,
            run,
            ids["kw_pause"],
            dt.date(2026, 8, 20),
            _obs(dt.date(2026, 8, 21)),
            cost="0.10",
            ad_revenue="1.00",
            clicks=1,
            orders=1,
            impressions=10,
        )
        cache = lee_cache(conn, "amazon_us", DECIDED_AT)
        evidencia = windows.ventanas_evidencia_ad_group(conn, "amazon_us", DECIDED_AT)
        corte = cortes_mod.umbral_corte(evidencia.get(ids["ag"]), "pause")
        goal = g.Goal(
            scope="platform",
            ad_entity_id=None,
            platform="amazon_us",
            target_acos_pct=Decimal("25"),
            bid_floor=Decimal("0.40"),
            bid_ceiling=Decimal("2.50"),
            bid_currency="USD",
            harvest_campaign_id=None,
            harvest_ad_group_id=None,
            harvest_default_bid=None,
            enabled=True,
            mode="shadow",
        )

        veredicto = ciclo._juicio_hoja(
            conn,
            goal.target_acos_pct,
            platform="amazon_us",
            goal=goal,
            entidad_id=ids["kw_pause"],
            ad_group_id=ids["ag"],
            current_bid=Decimal("1.00"),
            bid_currency="USD",
            decided_at=DECIDED_AT,
            corte_pause=corte,
            pause_economica=False,
            politica=None,
            lecturas=None,
            proteccion_cache=cache,
        )

        assert veredicto.kind is None
        assert veredicto.motivo == subproteccion.MOTIVO_PROTEGIDA
        assert veredicto.proteccion is not None
        assert veredicto.proteccion.accion == "esperar"
        assert veredicto.proteccion.ancla == dt.date(2026, 8, 20)
        assert veredicto.proteccion.vence_el == dt.date(2026, 8, 30)


@_skip_db
def test_ciclo_sin_setting_conserva_la_pausa_historica_y_no_toca_episodios():
    """Reversa de seleccion: sin `ads_proteccion_venta` la pausa por umbral
    procede como antes, el freeze no lleva insumos M1 y el episodio persistido
    no se borra ni se reescribe."""
    with _db_ciclo("orbit_m1_apagada") as (conn, _c):
        ids = _siembra_maestra(conn)
        run = _run(conn)
        for fecha in _fechas(dt.date(2026, 8, 20), dt.date(2026, 8, 21)):
            _metrica_ciclo(
                conn,
                run,
                ids["kw_pause"],
                fecha,
                _obs(fecha),
                cost="0.10",
                ad_revenue="0.10",
                clicks=1,
                orders=0,
                impressions=10,
            )
        _metrica_ciclo(
            conn,
            run,
            ids["kw_pause"],
            dt.date(2026, 8, 20),
            _obs(dt.date(2026, 8, 21)),
            cost="0.10",
            ad_revenue="1.00",
            clicks=1,
            orders=1,
            impressions=10,
        )
        conn.execute(
            "INSERT INTO proteccion_venta_reciente (platform, ad_entity_id, version,"
            " ancla, vence_el, abierta_el, observado_el) VALUES ('amazon_us', %s,"
            " 'proteccion_venta_v1', %s, %s, %s, %s)",
            (
                ids["kw_pause"],
                dt.date(2026, 8, 20),
                dt.date(2026, 8, 30),
                DECIDED_AT,
                DECIDED_AT - dt.timedelta(hours=1),
            ),
        )

        res = _corre(conn)

        assert res.status == "done"
        pausas = _pausa_de(conn, res.cycle_id, ids["kw_pause"])
        assert pausas, "sin el setting la pausa por umbral procede"
        assert pausas[0][1]["proteccion"] is None
        assert _episodios(conn, ids["kw_pause"]) == [(dt.date(2026, 8, 20), dt.date(2026, 8, 30))]


@_skip_db
def test_cola_sin_politica_no_espera_y_aplica_la_pausa_historica():
    """La cola respeta la misma seleccion: con la politica apagada revalida
    como antes y aplica la pausa; el episodio no se borra."""
    with _db_cola("orbit_m1_cola_apagada") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
        d = ids["ahora"]
        _siembra_maduros_y_recientes(conn, ids, d)
        conn.execute(
            "INSERT INTO proteccion_venta_reciente (platform, ad_entity_id, version,"
            " ancla, vence_el, abierta_el, observado_el) VALUES ('amazon_us', %s,"
            " 'proteccion_venta_v1', %s, %s, %s, %s)",
            (
                ids["kw"],
                d.date() - dt.timedelta(days=4),
                d.date() + dt.timedelta(days=6),
                d,
                d - dt.timedelta(hours=1),
            ),
        )
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, _vistos = _handler_cortes()
        aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])

        res = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=d,
            aplicador=aplicador,
            cycle_id=ids["ciclo_ejec"],
        )

        assert res.aplicadas == 1, res.descartadas
        assert res.espera_proteccion == 0
        assert conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0] == (
            "applied"
        )
        assert _episodios(conn, ids["kw"]) == [
            (d.date() - dt.timedelta(days=4), d.date() + dt.timedelta(days=6))
        ]


@_skip_db
def test_ciclo_con_setting_de_proteccion_desconocido_falla_cerrado():
    with _db_ciclo("orbit_m1_setting_malo") as (conn, _c):
        _siembra_maestra(conn, settings={"ads_proteccion_venta": "otra"})

        with pytest.raises(ValueError, match="ads_proteccion_venta"):
            _corre(conn)

        status = conn.execute(
            "SELECT status FROM optimizer_cycle ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert status == "failed"
