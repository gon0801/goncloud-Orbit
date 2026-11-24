"""Regresiones del bloque E: la practica vencida no bloquea el trabajo real.

Una propuesta de practica (`apply_queue.modo = 'shadow'`) deja de congelar la
clave de efecto cuando vence su ventana de 48 h, conservando su historial. Una
practica vigente sigue deduplicando y un efecto live no terminal o un veto
vigente siguen protegiendo la clave.

Estas pruebas usan la cadena canonica de migraciones (incluye 0098) sobre el
PostgreSQL desechable del laboratorio. La foto de produccion que respalda el
invariante vive en docs/evidencia/arreglos-05/E/previa.txt.
"""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest
from test_apply_cola import (  # noqa: F401 - helpers reutilizados
    _aplicador,
    _decision_corte,
    _encola_fila,
    _handler_cortes,
    _payload_pause,
    _semilla,
    encola_cortes,
)
from test_schema import _postgres_obligatorio_ausente

from app.apply_cola import claves_bloqueadas, skip_por_clave
from app.productos_io import _SQL_CLAVES_EN_VUELO
from tests.helpers.pg import db_desde_template

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _decision(conn, ids: dict, entidad: int, kind: str, **kw) -> int:
    """Decision de corte en su PROPIO ciclo: `decision` es unica por
    (cycle_id, ad_entity_id), y aqui necesitamos varias decisiones de la misma
    entidad para probar la clave de efecto."""
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    return _decision_corte(conn, ciclo, ids["config"], entidad, kind, **kw)


def _fila(conn, fila_id: int):
    """La fila completa de la practica: para comparar su historia sin cambios."""
    return conn.execute(
        "SELECT id, platform, ad_entity_id, familia, search_term, kind, decision_id,"
        " modo, estado, vence_el, encolado_at, request_payload"
        " FROM apply_queue WHERE id = %s",
        (fila_id,),
    ).fetchone()


def _inserta_live(conn, decision_id: int, entidad: int, payload: dict) -> int:
    return conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term, decision_id,"
        " modo, estado, vence_el, request_payload)"
        " VALUES ('amazon_us', %s, 'pause', NULL, %s, 'live', 'pending_veto', %s, %s)"
        " RETURNING id",
        (
            entidad,
            decision_id,
            dt.datetime.now(dt.UTC) + dt.timedelta(hours=48),
            psycopg.types.json.Json(payload),
        ),
    ).fetchone()[0]


@_skip_db
def test_arreglos05_e_shadow_vencida_no_bloquea_el_select():
    """E.1: una shadow vencida deja de bloquear el SELECT de la clave."""
    with db_desde_template("orbit_a05e_vencida_sel", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]

        dec_sh = _decision(conn, ids, ids["kw"], "pause")
        _encola_fila(
            conn,
            dec_sh,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora - dt.timedelta(hours=1),
            payload=_payload_pause("7201"),
        )

        assert (ids["kw"], "entity_cut", None) not in claves_bloqueadas(conn, "amazon_us", ahora), (
            "una practica vencida no puede seguir en claves_bloqueadas"
        )
        assert (
            skip_por_clave(conn, "amazon_us", {(ids["kw"], "entity_cut", None)}, ahora) == set()
        ), "skip_por_clave no debe saltarse una clave con solo shadow vencida"


@_skip_db
def test_arreglos05_e_shadow_vencida_no_bloquea_el_insert_y_conserva_historia():
    """E.1: con una shadow vencida, el INSERT live real entra y la fila shadow
    conserva su historia identica."""
    with db_desde_template("orbit_a05e_vencida_ins", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]

        dec_sh = _decision(conn, ids, ids["kw"], "pause")
        q_sh = _encola_fila(
            conn,
            dec_sh,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora - dt.timedelta(hours=1),
            payload=_payload_pause("7201"),
        )
        antes = _fila(conn, q_sh)

        dec_live = _decision(conn, ids, ids["kw"], "pause")
        q_live = _inserta_live(conn, dec_live, ids["kw"], _payload_pause("7201"))

        assert _fila(conn, q_sh) == antes, "la practica vencida conserva su historia sin cambios"
        assert _fila(conn, q_live)[7] == "live", "la decision real entro como live"
        no_terminales = conn.execute(
            "SELECT count(*) FROM apply_queue WHERE ad_entity_id = %s AND familia = 'entity_cut'"
            " AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')",
            (ids["kw"],),
        ).fetchone()[0]
        assert no_terminales == 2, "conviven la sombra vencida (historia) y la live real"


@_skip_db
def test_arreglos05_e_shadow_vigente_sigue_deduplicando_y_bloqueando():
    """E.2: mientras la practica esta vigente (vence_el > reloj de la base),
    sigue bloqueando una decision nueva hasta que vence."""
    with db_desde_template("orbit_a05e_vigente", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]

        dec_sh = _decision(conn, ids, ids["kw"], "pause")
        q_sh = _encola_fila(
            conn,
            dec_sh,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora + dt.timedelta(hours=2),
            payload=_payload_pause("7201"),
        )
        assert (ids["kw"], "entity_cut", None) in claves_bloqueadas(conn, "amazon_us", ahora), (
            "una practica vigente sigue bloqueando el SELECT"
        )

        dec_live = _decision(conn, ids, ids["kw"], "pause")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _inserta_live(conn, dec_live, ids["kw"], _payload_pause("7201"))

        dec_sh2 = _decision(conn, ids, ids["kw"], "pause")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _encola_fila(
                conn,
                dec_sh2,
                ids["kw"],
                "pause",
                modo="shadow",
                vence=ahora + dt.timedelta(hours=2),
                payload=_payload_pause("7201"),
            )
        assert conn.execute("SELECT count(*) FROM apply_queue").fetchone()[0] == 1, (
            "la practica vigente deduplica: no se crea otra fila"
        )
        assert _fila(conn, q_sh)[8] == "pending_veto"


@_skip_db
def test_arreglos05_e_live_no_terminal_y_veto_vigente_siguen_protegiendo():
    """E.2: un live no terminal bloquea INSERT y SELECT; un veto vigente
    bloquea la decision (SELECT) y un veto vencido no bloquea."""
    with db_desde_template("orbit_a05e_live_veto", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]

        dec_live = _decision(conn, ids, ids["kw"], "pause")
        _inserta_live(conn, dec_live, ids["kw"], _payload_pause("7201"))
        assert (ids["kw"], "entity_cut", None) in claves_bloqueadas(conn, "amazon_us", ahora)

        dec_live2 = _decision(conn, ids, ids["kw"], "pause")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _inserta_live(conn, dec_live2, ids["kw"], _payload_pause("7201"))
        dec_sh = _decision(conn, ids, ids["kw"], "pause")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _encola_fila(
                conn,
                dec_sh,
                ids["kw"],
                "pause",
                modo="shadow",
                vence=ahora + dt.timedelta(hours=2),
                payload=_payload_pause("7201"),
            )

        # Veto vigente y veto vencido sobre la entidad kw2 (clave libre).
        dec_v = _decision(conn, ids, ids["kw2"], "pause")
        q_v = _encola_fila(
            conn,
            dec_v,
            ids["kw2"],
            "pause",
            vence=ahora + dt.timedelta(days=20),
            payload=_payload_pause("7202"),
        )
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                " vetoed_by = 'dueno' WHERE id = %s",
                (q_v,),
            )
        finally:
            conn.execute("RESET ROLE")
        assert (ids["kw2"], "entity_cut", None) in claves_bloqueadas(conn, "amazon_us", ahora), (
            "un veto vigente sigue protegiendo la clave"
        )


@_skip_db
def test_arreglos05_e_dos_workers_misma_clave_como_maximo_un_live_no_terminal():
    """E.2: dos conexiones (dos workers) sobre la misma clave producen como
    maximo una fila live no terminal; cero doble efecto."""
    with db_desde_template("orbit_a05e_workers", autocommit=True) as (conn, dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        dec_a = _decision(conn, ids, ids["kw"], "pause")
        dec_b = _decision(conn, ids, ids["kw"], "pause")

        worker_a = psycopg.connect(dsn, autocommit=True)
        worker_b = psycopg.connect(dsn, autocommit=True)
        try:
            _inserta_live(worker_a, dec_a, ids["kw"], _payload_pause("7201"))
            with pytest.raises(psycopg.errors.UniqueViolation):
                _inserta_live(worker_b, dec_b, ids["kw"], _payload_pause("7201"))
        finally:
            worker_a.close()
            worker_b.close()

        vivas = conn.execute(
            "SELECT count(*) FROM apply_queue WHERE ad_entity_id = %s AND familia = 'entity_cut'"
            " AND modo = 'live' AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')",
            (ids["kw"],),
        ).fetchone()[0]
        assert vivas == 1, "como maximo una fila live no terminal por clave"


def _ciclo_nuevo(conn) -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]


@_skip_db
def test_arreglos05_e_encola_cortes_entra_tras_practica_vencida():
    """E.1 de punta a punta: el motor encola la decision live real aunque exista
    una shadow vencida de la misma clave, sin registrar choque."""
    with db_desde_template("orbit_a05e_encola_venc", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]
        dec_sh = _decision(conn, ids, ids["kw"], "pause")
        _encola_fila(
            conn,
            dec_sh,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora - dt.timedelta(hours=1),
            payload=_payload_pause("7201"),
        )
        ciclo = _ciclo_nuevo(conn)
        _decision_corte(conn, ciclo, ids["config"], ids["kw"], "pause")
        handler, _vistos = _handler_cortes()

        resumen = encola_cortes(
            conn,
            _aplicador(conn, handler, ids["ciclo_ejec"]),
            ciclo,
            modo_envelope="live",
            ahora=ahora,
        )

        assert resumen.encoladas_live == 1 and resumen.choques == [], (
            "una practica vencida no puede impedir encolar la decision real"
        )


@_skip_db
def test_arreglos05_e_encola_cortes_choca_con_practica_vigente_y_lo_registra():
    """E.2 de punta a punta: con una shadow vigente, el motor captura el choque
    del trigger (unique_violation) como choque_clave y no duplica la clave."""
    with db_desde_template("orbit_a05e_encola_vig", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]
        dec_sh = _decision(conn, ids, ids["kw"], "pause")
        _encola_fila(
            conn,
            dec_sh,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora + dt.timedelta(hours=2),
            payload=_payload_pause("7201"),
        )
        ciclo = _ciclo_nuevo(conn)
        dec = _decision_corte(conn, ciclo, ids["config"], ids["kw"], "pause")
        handler, _vistos = _handler_cortes()

        resumen = encola_cortes(
            conn,
            _aplicador(conn, handler, ids["ciclo_ejec"]),
            ciclo,
            modo_envelope="live",
            ahora=ahora,
        )

        assert resumen.encoladas_live == 0 and len(resumen.choques) == 1
        assert str(dec) in resumen.choques[0]
        assert conn.execute("SELECT count(*) FROM apply_queue").fetchone()[0] == 1
        assert conn.execute(
            "SELECT motivo FROM decision_sin_aplicar WHERE decision_id = %s", (dec,)
        ).fetchone() == ("choque_clave",)


@_skip_db
def test_arreglos05_e_espejo_productos_no_bloquea_practica_vencida():
    """El espejo de productos_io aplica la misma vigencia: una shadow vencida
    no bloquea la pausa de producto; una vigente si."""
    with db_desde_template("orbit_a05e_productos", autocommit=True) as (conn, _dsn):
        conn.execute("SET TIME ZONE 'UTC'")
        ids = _semilla(conn)
        ahora = ids["ahora"]
        dec_venc = _decision(conn, ids, ids["kw"], "pause")
        _encola_fila(
            conn,
            dec_venc,
            ids["kw"],
            "pause",
            modo="shadow",
            vence=ahora - dt.timedelta(hours=1),
            payload=_payload_pause("7201"),
        )
        dec_vig = _decision(conn, ids, ids["kw2"], "pause")
        _encola_fila(
            conn,
            dec_vig,
            ids["kw2"],
            "pause",
            modo="shadow",
            vence=ahora + dt.timedelta(hours=2),
            payload=_payload_pause("7202"),
        )
        params = {
            "plataforma": "amazon_us",
            "ahora": ahora,
            "ignora_practicas": False,
            "excluye_cola": None,
        }
        bloqueadas = {fila[0] for fila in conn.execute(_SQL_CLAVES_EN_VUELO, params).fetchall()}
        assert ids["kw"] not in bloqueadas, "una practica vencida no bloquea productos"
        assert ids["kw2"] in bloqueadas, "una practica vigente sigue bloqueando productos"

        params_simulacion = {**params, "ignora_practicas": True}
        assert conn.execute(_SQL_CLAVES_EN_VUELO, params_simulacion).fetchall() == [], (
            "ignora_practicas no debe bloquear ninguna shadow"
        )
