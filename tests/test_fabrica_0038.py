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

import contextlib
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

# Texto canónico del statement del motor (brief A.2 (f)): cuatro
# parámetros posicionales en este orden — tipo_producto, platform, texto,
# origen. Aquí viajan como `%s` de psycopg (ejecutables); el brief los
# escribe `$1..$4`. A.4 tendrá que igualar esta misma constante desde
# `app/`: lo invariante son columnas, ON CONFLICT, SET y RETURNING, que el
# test de fragmentos cruza contra el DO de 0038.
SQL_BIBLIOTECA_KEYWORD = (
    "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)"
    " VALUES (%s, %s, %s, %s)"
    " ON CONFLICT (tipo_producto, platform, texto) DO UPDATE SET updated_at = now()"
    " RETURNING id"
)
SQL_BIBLIOTECA_NEGATIVE = (
    "INSERT INTO negative_biblioteca (tipo_producto, platform, texto, origen)"
    " VALUES (%s, %s, %s, %s)"
    " ON CONFLICT (tipo_producto, platform, texto) DO NOTHING"
)

# Valores de prueba del candado: válidos contra los CHECKs de 0018 e
# imposibles en producción (prefijo zz_).
BIB_TIPO = "zz_candado_0038"
BIB_TEXTO = "zz candado 0038 termino"
BIB_ORIGEN = "migracion_0038"


def test_0038_canon_biblioteca_en_do():
    """El texto canónico vive en el DO de 0038: los fragmentos invariantes
    (columnas, ON CONFLICT, SET, RETURNING, DO NOTHING) aparecen en su
    cuerpo parseado. Si A.4 escribe otro statement, este test lo dice."""
    import pglast
    from pglast import ast as pgast

    cuerpo = next(
        s.stmt.args[0].arg.sval for s in pglast.parse_sql(SQL38) if isinstance(s.stmt, pgast.DoStmt)
    )
    plano = " ".join(cuerpo.split())
    for fragmento in (
        "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)",
        "ON CONFLICT (tipo_producto, platform, texto) DO UPDATE SET updated_at = now()",
        "RETURNING id",
        "INSERT INTO negative_biblioteca (tipo_producto, platform, texto, origen)",
        "ON CONFLICT (tipo_producto, platform, texto) DO NOTHING",
    ):
        assert fragmento in plano, f"el DO de 0038 no trae: {fragmento}"


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


def _semilla_grupo_minimo(conn, *, platform="amazon_us", con_segundo_grupo=False) -> dict:
    """Lote + grupo + campaña/ad group en `campana_grupo_rol` (para el
    trigger bid-solo: estado 3 exige membresía). Con `con_segundo_grupo`:
    otro lote + grupo vacío (destino del re-apuntado por `grupo_id`)."""
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
    salida = {"grupo": grupo, "camp": camp, "ag": ag}
    if con_segundo_grupo:
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES ('lote-0038-b', %s, 'collar_perro',"
            " 'Base B', 'go', 'h', '{}'::jsonb, 'live', 'applied')",
            (platform,),
        )
        salida["grupo2"] = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
            " VALUES (%s, 'collar_perro', 'Base B', 'lote-0038-b', 20, 20, 0.5, 't', 'go')"
            " RETURNING id",
            (platform,),
        ).fetchone()[0]
    return salida


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


@contextmanager
def _dos_conexiones_38(prefijo: str):
    """DB con ORDEN38 + grupo mínimo, y DOS conexiones sin autocommit para
    el test de concurrencia (DoD 11): cada hilo su conexión."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    setup = None
    conn_a = conn_b = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        setup = psycopg.connect(dsn, dbname=db, autocommit=True)
        setup.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN38:
            setup.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        gpo = _semilla_grupo_minimo(setup)
        conn_a = psycopg.connect(dsn, dbname=db, autocommit=False)
        conn_b = psycopg.connect(dsn, dbname=db, autocommit=False)
        yield conn_a, conn_b, gpo
        for c in (conn_a, conn_b):
            with contextlib.suppress(psycopg.Error):
                c.rollback()
    finally:
        for c in (conn_a, conn_b):
            if c is not None:
                c.close()
        if setup is not None:
            setup.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


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
    re-apunta rechazado (por `ad_entity_id` y por `grupo_id`); goal en
    estado 2: DELETE permitido. Rojo pre-0038: el DELETE pasa (no hay
    trigger). Ronda del lead PR #261: sin el caso `grupo_id`, quitar esa
    cláusula del trigger no mataba nada."""
    with db_38("orbit_38_sim") as conn:
        gpo = _semilla_grupo_minimo(conn, con_segundo_grupo=True)
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
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE campana_grupo_rol SET grupo_id = %s"
                " WHERE grupo_id = %s AND rol = 'category_phrase'",
                (gpo["grupo2"], gpo["grupo"]),
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
# DoD 11 — el lock por campaña serializa estado 3 vs membresía
# ---------------------------------------------------------------------------


def _goal_estado3(conn, camp: int) -> None:
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct,"
        " bid_floor, bid_ceiling, bid_currency, harvest_default_bid, enabled, mode)"
        " VALUES ('campaign', %s, 20, 0.10, 2.50, 'USD', 1.00, true, 'live')",
        (camp,),
    )


@_skip_db
def test_0038_estado3_y_membresia_se_serializan():
    """DoD 11 (hallazgo CodeRabbit PR #260): A inserta el estado 3 sin
    confirmar y B intenta el DELETE de la membresía → se bloquea; A
    confirma → B falla. Y al revés: B borra sin confirmar y A intenta el
    estado 3 → se bloquea; B confirma → A falla. Nunca queda un estado 3
    sin membresía. Sin el lock, el segundo pasaría con el snapshot viejo."""
    import threading

    with _dos_conexiones_38("orbit_38_lock") as (a, b, gpo):
        # Ida: estado 3 sin confirmar bloquea al DELETE.
        _goal_estado3(a, gpo["camp"])
        errores: list = []
        listo = threading.Event()

        def _borra():
            try:
                b.execute(
                    "DELETE FROM campana_grupo_rol WHERE grupo_id = %s AND rol = 'category_phrase'",
                    (gpo["grupo"],),
                )
                b.commit()
            except Exception as exc:  # noqa: BLE001 — el assert decide abajo
                errores.append(exc)
            finally:
                listo.set()

        hilo = threading.Thread(target=_borra)
        hilo.start()
        assert listo.wait(10) is False, "el DELETE debió bloquearse tras el INSERT"
        a.commit()
        assert listo.wait(10), "al confirmar A, B debe despertar"
        hilo.join(10)
        assert len(errores) == 1 and isinstance(errores[0], psycopg.errors.CheckViolation), (
            f"B debió fallar con check_violation, no {errores!r}"
        )
        b.rollback()
        assert (
            a.execute(
                "SELECT count(*) FROM campana_grupo_rol WHERE grupo_id = %s", (gpo["grupo"],)
            ).fetchone()[0]
            == 1
        )

        # Vuelta: sin goal previo, el DELETE sin confirmar bloquea al
        # estado 3 (se borra el de la ida: si no, el UNIQUE lo rechazaría
        # antes que el trigger y el DELETE previo ni arrancaría).
        a.execute("DELETE FROM ads_optimizer_goal WHERE ad_entity_id = %s", (gpo["camp"],))
        a.commit()
        b.execute(
            "DELETE FROM campana_grupo_rol WHERE grupo_id = %s AND rol = 'category_phrase'",
            (gpo["grupo"],),
        )
        errores2: list = []
        listo2 = threading.Event()

        def _inserta():
            try:
                _goal_estado3(a, gpo["camp"])
                a.commit()
            except Exception as exc:  # noqa: BLE001 — el assert decide abajo
                errores2.append(exc)
            finally:
                listo2.set()

        hilo2 = threading.Thread(target=_inserta)
        hilo2.start()
        assert listo2.wait(10) is False, "el INSERT debió bloquearse tras el DELETE"
        b.commit()
        assert listo2.wait(10), "al confirmar B, A debe despertar"
        hilo2.join(10)
        assert len(errores2) == 1 and isinstance(errores2[0], psycopg.errors.CheckViolation), (
            f"A debió fallar con check_violation, no {errores2!r}"
        )
        a.rollback()
        assert (
            b.execute(
                "SELECT count(*) FROM ads_optimizer_goal WHERE ad_entity_id = %s",
                (gpo["camp"],),
            ).fetchone()[0]
            == 0
        ), "la vuelta no dejó goal: el estado 3 sin membresía nunca existe"


@_skip_db
def test_0038_app_decide_bibliotecas_statement_literal():
    """Bajo SET ROLE app_decide los dos statements canónicos insertan y
    actualizan de verdad (fila leída, id estable, updated_at movido,
    `origen` no pisado); los negativos truenan (patrón
    test_apply_schema.py:709-745, no catálogo). Rojo pre-0038:
    InsufficientPrivilege en el primer INSERT."""
    with db_38("orbit_38_rol") as conn:
        conn.execute("SET ROLE app_decide")
        try:
            primero = conn.execute(
                SQL_BIBLIOTECA_KEYWORD, (BIB_TIPO, "amazon_mx", BIB_TEXTO, BIB_ORIGEN)
            ).fetchone()
            assert (
                conn.execute(
                    "SELECT origen FROM keyword_biblioteca WHERE id = %s", (primero[0],)
                ).fetchone()[0]
                == BIB_ORIGEN
            )
            conn.execute(
                "UPDATE keyword_biblioteca SET updated_at = now() - interval '1 hour'"
                " WHERE id = %s",
                (primero[0],),
            )
            viejo = conn.execute(
                "SELECT updated_at FROM keyword_biblioteca WHERE id = %s", (primero[0],)
            ).fetchone()[0]
            # Conflicto con OTRO origen: solo updated_at se mueve, el origen
            # primero queda (diseño, Biblioteca: "origen no se pisa"). El
            # canónico devuelve solo `id`: el updated_at se lee aparte.
            segundo = conn.execute(
                SQL_BIBLIOTECA_KEYWORD, (BIB_TIPO, "amazon_mx", BIB_TEXTO, "otro_origen")
            ).fetchone()
            fila = conn.execute(
                "SELECT origen, updated_at FROM keyword_biblioteca WHERE id = %s",
                (primero[0],),
            ).fetchone()
            assert segundo[0] == primero[0], "el upsert no duplicó: id estable"
            assert fila[0] == BIB_ORIGEN, "el conflicto no pisa el origen"
            assert fila[1] > viejo, "updated_at se movió"
            conn.execute(SQL_BIBLIOTECA_NEGATIVE, (BIB_TIPO, "amazon_mx", BIB_TEXTO, BIB_ORIGEN))
            conn.execute(SQL_BIBLIOTECA_NEGATIVE, (BIB_TIPO, "amazon_mx", BIB_TEXTO, BIB_ORIGEN))
            fila_neg = conn.execute(
                "SELECT id, origen FROM negative_biblioteca"
                " WHERE tipo_producto = %s AND texto = %s",
                (BIB_TIPO, BIB_TEXTO),
            ).fetchone()
            assert fila_neg is not None and fila_neg[1] == BIB_ORIGEN
            assert (
                conn.execute(
                    "SELECT count(*) FROM negative_biblioteca"
                    " WHERE tipo_producto = %s AND texto = %s",
                    (BIB_TIPO, BIB_TEXTO),
                ).fetchone()[0]
                == 1
            )
            for sql, params in (
                ("DELETE FROM keyword_biblioteca WHERE tipo_producto = %s", (BIB_TIPO,)),
                (
                    "UPDATE keyword_biblioteca SET origen = 'x' WHERE tipo_producto = %s",
                    (BIB_TIPO,),
                ),
                (
                    "UPDATE keyword_biblioteca SET texto = 'x' WHERE tipo_producto = %s",
                    (BIB_TIPO,),
                ),
                (
                    "UPDATE keyword_biblioteca SET first_seen_at = now() WHERE tipo_producto = %s",
                    (BIB_TIPO,),
                ),
                (
                    "UPDATE keyword_biblioteca SET cost = 1.00 WHERE tipo_producto = %s",
                    (BIB_TIPO,),
                ),
                ("DELETE FROM negative_biblioteca WHERE tipo_producto = %s", (BIB_TIPO,)),
                (
                    "UPDATE negative_biblioteca SET origen = 'x' WHERE tipo_producto = %s",
                    (BIB_TIPO,),
                ),
                ("UPDATE harvest_excepcion SET go_literal = 'x' WHERE false", ()),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sql, params)
        finally:
            conn.execute("RESET ROLE")
        conn.execute("DELETE FROM keyword_biblioteca WHERE tipo_producto = %s", (BIB_TIPO,))
        conn.execute("DELETE FROM negative_biblioteca WHERE tipo_producto = %s", (BIB_TIPO,))


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
