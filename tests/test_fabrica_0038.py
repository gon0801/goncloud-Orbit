"""Tests de la migración `migrations/0038_fabrica_hermanas_biblioteca.sql`
(FABRICA 02, A.2): fase `hermanas_negadas`, tipo `hermana` en el ledger,
trigger bid-solo del goal + simétrico de grupo, y GRANTs de biblioteca.

`ORDEN38` es el subconjunto mínimo que toca 0038 (0001 + 0002 + 0003 +
0018): ninguna migración entre 0004 y 0037 menciona `harvest_job`,
`apply_attempt`, `ads_optimizer_goal`, `campana_grupo_rol` ni las
bibliotecas (verificado por grep al escribir el módulo), así que el
subconjunto equivale al esquema real para estos objetos. Todos los tests
de base corren con `ORBIT_TEST_DSN` apuntado (0 skipped); sin Postgres
skipean en verde fuera de CI (patrón `test_apply_schema`).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
ORDEN38 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0003_goal_bounds_explicit.sql",
    "0018_fabrica_campanas.sql",
    "0038_fabrica_hermanas_biblioteca.sql",
)
SQL38 = (ROOT / "migrations" / "0038_fabrica_hermanas_biblioteca.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

# Fases en vuelo (DoD 3): cruzada contra `pg_index.indpred` del índice real
# en la base de test — las cuatro, ni una más.
FASES_EN_VUELO = frozenset({"pending", "negative_created", "exact_created", "hermanas_negadas"})


@contextmanager
def db_38(prefijo: str):
    """DB temporal con ORDEN38; yields conn autocommit (patrón `db_f2`)."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN38:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# Seeds mínimos
# ---------------------------------------------------------------------------


def _semilla_harvest(conn, *, term="termino a", platform="amazon_us", n="70") -> dict:
    """Config + ciclo + campaña/ad group + decisión harvest madura."""
    config = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-0038', '{}'::jsonb) RETURNING id"
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status) VALUES ('live', %s, 'done')"
        " RETURNING id",
        (platform,),
    ).fetchone()[0]
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
        " RETURNING id",
        (platform, f"{n}01"),
    ).fetchone()[0]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES (%s, 'ad_group', %s, %s) RETURNING id",
        (platform, f"{n}11", camp),
    ).fetchone()[0]
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'harvest', %s, now() - interval '40 days',"
        " CURRENT_DATE - 60, CURRENT_DATE - 30, %s, 1.00, 'USD', '{}'::jsonb) RETURNING id",
        (ciclo, ag, config, term),
    ).fetchone()[0]
    return {"config": config, "ciclo": ciclo, "camp": camp, "ag": ag, "dec": dec}


def _job(conn, decision: int, ag: int, *, term="termino a", platform="amazon_us") -> int:
    return conn.execute(
        "INSERT INTO harvest_job (decision_id, search_term, platform, ad_entity_id, fase)"
        " VALUES (%s, %s, %s::platform, %s, 'pending') RETURNING id",
        (decision, term, platform, ag),
    ).fetchone()[0]


def _semilla_grupo_minimo(conn, *, platform="amazon_us") -> dict:
    """Lote + grupo + campaña/ad group en `campana_grupo_rol` (para el
    trigger bid-solo: estado 3 exige membresía)."""
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES ('lote-0038', %s, 'collar_perro',"
        " 'Base', 'go', 'h', '{}'::jsonb, 'live', 'applied')",
        (platform,),
    )
    grupo = conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
        " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
        " VALUES (%s, 'collar_perro', 'Base', 'lote-0038', 20, 20, 0.5, 't', 'go')"
        " RETURNING id",
        (platform,),
    ).fetchone()[0]
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', '6101')"
        " RETURNING id",
        (platform,),
    ).fetchone()[0]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES (%s, 'ad_group', '6201', %s) RETURNING id",
        (platform, camp),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)"
        " VALUES (%s, 'category_phrase', %s, %s)",
        (grupo, camp, ag),
    )
    return {"grupo": grupo, "camp": camp, "ag": ag}


def _goal(conn, **cols) -> int:
    base = {
        "scope": "campaign",
        "ad_entity_id": None,
        "platform": None,
        "target_acos_pct": Decimal("20"),
        "bid_floor": Decimal("0.10"),
        "bid_ceiling": Decimal("2.50"),
        "bid_currency": "USD",
        "harvest_campaign_id": None,
        "harvest_ad_group_id": None,
        "harvest_default_bid": None,
        "enabled": True,
        "mode": "live",
    }
    base.update(cols)
    return conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, platform, target_acos_pct,"
        " bid_floor, bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s) RETURNING id",
        (
            base["scope"],
            base["ad_entity_id"],
            base["platform"],
            base["target_acos_pct"],
            base["bid_floor"],
            base["bid_ceiling"],
            base["bid_currency"],
            base["harvest_campaign_id"],
            base["harvest_ad_group_id"],
            base["harvest_default_bid"],
            base["enabled"],
            base["mode"],
        ),
    ).fetchone()[0]


def _fases_de_indice(conn) -> frozenset:
    """Fases del predicado del índice real (DoD 3)."""
    pred = conn.execute(
        "SELECT pg_get_expr(indpred, indrelid) FROM pg_index"
        " WHERE indexrelid = 'harvest_job_en_vuelo'::regclass"
    ).fetchone()[0]
    import re

    return frozenset(re.findall(r"'([a-z_]+)'::text", pred))


# ---------------------------------------------------------------------------
# DoD 1 — progresión con `hermanas_negadas` (+ `exact_created → done`)
# ---------------------------------------------------------------------------


@_skip_db
def test_0038_progresion_hermanas_y_done_conservado():
    """Válidas: exact_created → hermanas_negadas → done|failed; la vieja
    exact_created → done sigue viva (jobs viejos). Rojo pre-0038: la fase
    nueva viola el CHECK inline de 0001."""
    with db_38("orbit_38_prog") as conn:
        ids = _semilla_harvest(conn)
        j1 = _job(conn, ids["dec"], ids["ag"])
        conn.execute("UPDATE harvest_job SET fase = 'negative_created' WHERE id = %s", (j1,))
        conn.execute("UPDATE harvest_job SET fase = 'exact_created' WHERE id = %s", (j1,))
        conn.execute("UPDATE harvest_job SET fase = 'hermanas_negadas' WHERE id = %s", (j1,))
        conn.execute("UPDATE harvest_job SET fase = 'done' WHERE id = %s", (j1,))

        ids_b = _semilla_harvest(conn, term="termino b", n="71")
        j2 = _job(conn, ids_b["dec"], ids_b["ag"], term="termino b")
        conn.execute("UPDATE harvest_job SET fase = 'negative_created' WHERE id = %s", (j2,))
        conn.execute("UPDATE harvest_job SET fase = 'exact_created' WHERE id = %s", (j2,))
        conn.execute("UPDATE harvest_job SET fase = 'hermanas_negadas' WHERE id = %s", (j2,))
        conn.execute("UPDATE harvest_job SET fase = 'failed' WHERE id = %s", (j2,))

        # Job viejo: salta la fase nueva sin pasar por ella.
        ids_c = _semilla_harvest(conn, term="termino c", n="72")
        j3 = _job(conn, ids_c["dec"], ids_c["ag"], term="termino c")
        conn.execute("UPDATE harvest_job SET fase = 'negative_created' WHERE id = %s", (j3,))
        conn.execute("UPDATE harvest_job SET fase = 'exact_created' WHERE id = %s", (j3,))
        conn.execute("UPDATE harvest_job SET fase = 'done' WHERE id = %s", (j3,))
        fases = conn.execute("SELECT fase FROM harvest_job ORDER BY id").fetchall()
        assert [f[0] for f in fases] == ["done", "failed", "done"]


@_skip_db
def test_0038_progresion_rechaza_saltos_y_retrocesos():
    """Inválidas (check_violation): pending/negative_created → hermanas,
    hermanas → exact_created, done → hermanas. Rojo pre-0038: el mensaje
    ni existe (la fase es ilegal por CHECK, no por progresión)."""
    with db_38("orbit_38_nope") as conn:
        ids = _semilla_harvest(conn)
        j1 = _job(conn, ids["dec"], ids["ag"])
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE harvest_job SET fase = 'hermanas_negadas' WHERE id = %s", (j1,))
        conn.execute("UPDATE harvest_job SET fase = 'negative_created' WHERE id = %s", (j1,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE harvest_job SET fase = 'hermanas_negadas' WHERE id = %s", (j1,))
        conn.execute("UPDATE harvest_job SET fase = 'exact_created' WHERE id = %s", (j1,))
        conn.execute("UPDATE harvest_job SET fase = 'hermanas_negadas' WHERE id = %s", (j1,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE harvest_job SET fase = 'exact_created' WHERE id = %s", (j1,))
        conn.execute("UPDATE harvest_job SET fase = 'done' WHERE id = %s", (j1,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE harvest_job SET fase = 'hermanas_negadas' WHERE id = %s", (j1,))


# ---------------------------------------------------------------------------
# DoD 2 y 3 — índice parcial con la fase nueva
# ---------------------------------------------------------------------------


@_skip_db
def test_0038_en_vuelo_bloquea_en_hermanas_y_libera_en_done():
    """Job en `hermanas_negadas` bloquea al gemelo (unique); el mismo en
    `done` no bloquea. Rojo pre-0038: el gemelo entra (la fase no está en
    el predicado)."""
    with db_38("orbit_38_idx") as conn:
        ids = _semilla_harvest(conn)
        j1 = _job(conn, ids["dec"], ids["ag"])
        for fase in ("negative_created", "exact_created", "hermanas_negadas"):
            conn.execute("UPDATE harvest_job SET fase = %s WHERE id = %s", (fase, j1))
        with pytest.raises(psycopg.errors.UniqueViolation):
            _job(conn, ids["dec"], ids["ag"])
        conn.execute("UPDATE harvest_job SET fase = 'done' WHERE id = %s", (j1,))
        j2 = _job(conn, ids["dec"], ids["ag"])
        assert j2 != j1


@_skip_db
def test_0038_fases_en_vuelo_iguales_al_indice_real():
    """La constante == `pg_index.indpred` del índice real: las cuatro, ni
    una más (mata "quitar 'hermanas_negadas' del predicado"). Rojo pre-0038:
    el índice trae tres."""
    with db_38("orbit_38_ind") as conn:
        assert _fases_de_indice(conn) == FASES_EN_VUELO


# ---------------------------------------------------------------------------
# DoD 4 — `tipo='hermana'` en el ledger
# ---------------------------------------------------------------------------


@_skip_db
def test_0038_hermana_en_ledger_sin_quota_y_sin_decision_truena():
    """`hermana` con quota_cobrada=false entra; sin decision_id truena
    (`attempt_probe_sin_decision` intacto); el COUNT de `normal` de esa
    decisión no cambia (las hermanas no amplían el presupuesto `normal`).
    Rojo pre-0038: el tipo viola `attempt_tipo_valido`."""
    with db_38("orbit_38_her") as conn:
        ids = _semilla_harvest(conn)
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 1, 'normal', '{}'::jsonb, true)",
            (ids["dec"],),
        )
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 2, 'normal', '{}'::jsonb, false)",
            (ids["dec"],),
        )
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 3, 'hermana', '{}'::jsonb, false)",
            (ids["dec"],),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO apply_attempt (seq, tipo, request_payload, quota_cobrada)"
                " VALUES (4, 'hermana', '{}'::jsonb, false)"
            )
        normales = conn.execute(
            "SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'",
            (ids["dec"],),
        ).fetchone()[0]
        assert normales == 2


# ---------------------------------------------------------------------------
# DoD 5 — trigger del goal (tres estados, errcode check_violation)
# ---------------------------------------------------------------------------


@_skip_db
def test_0038_goal_bid_solo_con_grupo_y_rechazos():
    """Estado 3 aceptado con campaña en grupo; rechazado (check_violation)
    con scope=platform, con campaña fuera de grupo y con parcial distinto
    (solo campaign_id); estados 1 y 2 intactos. Rojo pre-0038: el estado 3
    viola `goal_harvest_completo`."""
    with db_38("orbit_38_goal") as conn:
        gpo = _semilla_grupo_minimo(conn)
        # Estado 3: bid-solo con grupo.
        bid_solo = _goal(
            conn,
            ad_entity_id=gpo["camp"],
            harvest_campaign_id=None,
            harvest_ad_group_id=None,
            harvest_default_bid=Decimal("1.00"),
        )
        fila = conn.execute(
            "SELECT harvest_campaign_id, harvest_ad_group_id, harvest_default_bid"
            " FROM ads_optimizer_goal WHERE id = %s",
            (bid_solo,),
        ).fetchone()
        assert fila == (None, None, Decimal("1.0000"))
        # scope = platform con bid-solo: rechazado.
        with pytest.raises(psycopg.errors.CheckViolation):
            _goal(
                conn,
                scope="platform",
                ad_entity_id=None,
                platform="amazon_us",
                harvest_campaign_id=None,
                harvest_ad_group_id=None,
                harvest_default_bid=Decimal("1.00"),
            )
        # Campaña fuera de grupo con bid-solo: rechazado.
        suelta = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_us', 'campaign', '7002') RETURNING id"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            _goal(
                conn,
                ad_entity_id=suelta,
                harvest_campaign_id=None,
                harvest_ad_group_id=None,
                harvest_default_bid=Decimal("1.00"),
            )
        # Parcial distinto (solo campaign_id): rechazado.
        with pytest.raises(psycopg.errors.CheckViolation):
            _goal(
                conn,
                ad_entity_id=gpo["camp"],
                harvest_campaign_id="8001",
                harvest_ad_group_id=None,
                harvest_default_bid=None,
            )
        # Estados 1 y 2 intactos (en campañas sin goal previo).
        _goal(conn, ad_entity_id=suelta)
        suelta2 = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_us', 'campaign', '7003') RETURNING id"
        ).fetchone()[0]
        _goal(
            conn,
            ad_entity_id=suelta2,
            harvest_campaign_id="8001",
            harvest_ad_group_id="8101",
            harvest_default_bid=Decimal("1.00"),
        )


# ---------------------------------------------------------------------------
# DoD 6 — trigger simétrico en `campana_grupo_rol`
# ---------------------------------------------------------------------------


@_skip_db
def test_0038_grupo_no_suelta_campana_con_goal_bid_solo():
    """Goal en estado 3: DELETE de su fila de rol rechazado y UPDATE que la
    re-apunta rechazado; goal en estado 2: DELETE permitido. Rojo pre-0038:
    el DELETE pasa (no hay trigger)."""
    with db_38("orbit_38_sim") as conn:
        gpo = _semilla_grupo_minimo(conn)
        _goal(
            conn,
            ad_entity_id=gpo["camp"],
            harvest_campaign_id=None,
            harvest_ad_group_id=None,
            harvest_default_bid=Decimal("1.00"),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "DELETE FROM campana_grupo_rol WHERE grupo_id = %s AND rol = 'category_phrase'",
                (gpo["grupo"],),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE campana_grupo_rol SET ad_entity_id = %s"
                " WHERE grupo_id = %s AND rol = 'category_phrase'",
                (gpo["ag"], gpo["grupo"]),
            )
        # UPDATE que no re-apunta (mismo grupo y campaña) sigue legal.
        conn.execute(
            "UPDATE campana_grupo_rol SET ad_group_ad_entity_id = %s"
            " WHERE grupo_id = %s AND rol = 'category_phrase'",
            (gpo["ag"], gpo["grupo"]),
        )
        # Con goal en estado 2 (terna completa), el DELETE es legal.
        conn.execute(
            "UPDATE ads_optimizer_goal SET harvest_campaign_id = '8001',"
            " harvest_ad_group_id = '8101' WHERE ad_entity_id = %s",
            (gpo["camp"],),
        )
        conn.execute(
            "DELETE FROM campana_grupo_rol WHERE grupo_id = %s AND rol = 'category_phrase'",
            (gpo["grupo"],),
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM campana_grupo_rol WHERE grupo_id = %s", (gpo["grupo"],)
            ).fetchone()[0]
            == 0
        )


# ---------------------------------------------------------------------------
# DoD 7 — `app_decide` escribe bibliotecas con el statement literal
# ---------------------------------------------------------------------------


@_skip_db
def test_0038_app_decide_bibliotecas_statement_literal():
    """Bajo SET ROLE app_decide los dos statements literales insertan y
    actualizan de verdad (fila leída, id estable, updated_at movido); los
    negativos truenan (patrón test_apply_schema.py:709-745, no catálogo).
    Rojo pre-0038: InsufficientPrivilege en el primer INSERT."""
    with db_38("orbit_38_rol") as conn:
        conn.execute("SET ROLE app_decide")
        try:
            primero = conn.execute(
                "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)"
                " VALUES ('t_rol', 'amazon_mx', 'texto rol', 'grupo:1/campana:2/harvest:3')"
                " ON CONFLICT (tipo_producto, platform, texto)"
                " DO UPDATE SET updated_at = now() RETURNING id, updated_at"
            ).fetchone()
            conn.execute(
                "UPDATE keyword_biblioteca SET updated_at = now() - interval '1 hour'"
                " WHERE id = %s",
                (primero[0],),
            )
            segundo = conn.execute(
                "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)"
                " VALUES ('t_rol', 'amazon_mx', 'texto rol', 'grupo:1/campana:2/harvest:3')"
                " ON CONFLICT (tipo_producto, platform, texto)"
                " DO UPDATE SET updated_at = now() RETURNING id, updated_at"
            ).fetchone()
            assert segundo[0] == primero[0], "el upsert no duplicó: id estable"
            assert segundo[1] > primero[1] - dt.timedelta(hours=1), "updated_at se movió"
            conn.execute(
                "INSERT INTO negative_biblioteca (tipo_producto, platform, texto, origen)"
                " VALUES ('t_rol', 'amazon_mx', 'texto rol', 'origen:neg')"
                " ON CONFLICT (tipo_producto, platform, texto) DO NOTHING"
            )
            fila = conn.execute(
                "SELECT id, origen FROM negative_biblioteca"
                " WHERE tipo_producto = 't_rol' AND texto = 'texto rol'"
            ).fetchone()
            assert fila is not None and fila[1] == "origen:neg"
            for sql, params in (
                ("DELETE FROM keyword_biblioteca WHERE id = %s", (primero[0],)),
                (
                    "UPDATE keyword_biblioteca SET origen = 'x' WHERE id = %s",
                    (primero[0],),
                ),
                (
                    "UPDATE keyword_biblioteca SET texto = 'x' WHERE id = %s",
                    (primero[0],),
                ),
                (
                    "UPDATE keyword_biblioteca SET first_seen_at = now() WHERE id = %s",
                    (primero[0],),
                ),
                ("DELETE FROM negative_biblioteca WHERE id = %s", (fila[0],)),
                (
                    "UPDATE negative_biblioteca SET origen = 'x' WHERE id = %s",
                    (fila[0],),
                ),
                ("UPDATE harvest_excepcion SET go_literal = 'x' WHERE false", ()),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sql, params)
        finally:
            conn.execute("RESET ROLE")
        conn.execute("DELETE FROM keyword_biblioteca WHERE tipo_producto = 't_rol'")
        conn.execute("DELETE FROM negative_biblioteca WHERE tipo_producto = 't_rol'")


# ---------------------------------------------------------------------------
# DoD 8 — mutante obligatorio: sin USAGE la migración truena en el DO
# ---------------------------------------------------------------------------


@_skip_db
def test_0038_sin_usage_en_secuencia_la_migracion_truena():
    """Mutante obligatorio: 0038 sin el `GRANT USAGE ON SEQUENCE` no puede
    pasar su propio DO (el INSERT del motor necesita nextval). Se aplica el
    texto sin esa línea y tiene que tronar."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_38_mut_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN38[:-1]:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        mutante = SQL38.replace(
            "GRANT USAGE ON SEQUENCE keyword_biblioteca_id_seq, negative_biblioteca_id_seq\n"
            "    TO app_decide;",
            "-- MUTANTE: sin USAGE en secuencias",
        )
        assert "GRANT USAGE" not in mutante
        # El error es el crudo de Postgres (sin "0038": lo lanza el
        # nextval, no un RAISE propio): permission denied for sequence.
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="keyword_biblioteca_id_seq"):
            conn.execute(mutante)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
