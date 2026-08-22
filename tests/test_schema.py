"""Tests del esquema `migrations/0001_initial.sql`.

(a) ESTÁTICOS: parsean la migración con pglast y afirman los invariantes
    sellados del diseño sobre el AST (no sobre texto). Corren siempre.
(b) INTEGRACIÓN: aplican la migración en un Postgres temporal y prueban
    rechazos reales. Skip automático si no hay servidor en localhost:5432.

Regla 8 del diseño: los invariantes se prueban contra la forma real del dato;
aquí la "forma real" es el AST que PostgreSQL ejecutaría.
"""

import os
import socket
from pathlib import Path

import pglast
import pytest
from pglast import ast, enums

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations" / "0001_initial.sql").read_text(encoding="utf-8")
STMTS = tuple(pglast.parse_sql(SQL))

APPEND_ONLY = {
    "ads_metric_observation",
    "search_term_observation",
    "ledger_event",
    "config_version",
    "decision",
    "fx_rate",
    "external_reconciliation",
}

TIPOS_FLOAT = {"float4", "float8"}  # real / double precision / float


def _stmts(cls):
    return [s.stmt for s in STMTS if isinstance(s.stmt, cls)]


TABLES = {t.relation.relname: t for t in _stmts(ast.CreateStmt)}
TRIGGERS = _stmts(ast.CreateTrigStmt)
INDEXES = _stmts(ast.IndexStmt)
FUNCTIONS = {f.funcname[-1].sval: f for f in _stmts(ast.CreateFunctionStmt)}


def _cols(tabla):
    return {e.colname: e for e in TABLES[tabla].tableElts if isinstance(e, ast.ColumnDef)}


def _type_name(coldef):
    return ".".join(n.sval for n in coldef.typeName.names)


def _contypes(coldef):
    return {c.contype for c in (coldef.constraints or ())}


def _check_constraints(tabla):
    return {
        e.conname: e
        for e in TABLES[tabla].tableElts
        if isinstance(e, ast.Constraint)
        and e.contype == enums.ConstrType.CONSTR_CHECK
        and e.conname
    }


def _body_plpgsql(nombre_funcion):
    """Cuerpo de una función PL/pgSQL, extraído del AST de CREATE FUNCTION."""
    func = FUNCTIONS[nombre_funcion]
    for opt in func.options:
        if opt.defname == "as":
            return "".join(parte.sval for parte in opt.arg)
    raise AssertionError(f"{nombre_funcion}: sin cuerpo 'as' en el AST")


# ---------------------------------------------------------------------------
# (a) ESTÁTICOS — sobre el AST de la migración
# ---------------------------------------------------------------------------


def test_migracion_parsea():
    assert len(STMTS) > 100  # sanity: el archivo completo parseó


def test_las_siete_append_only_tienen_candado():
    # Constantes de trigger.h: BEFORE=2; INSERT=4, DELETE=8, UPDATE=16,
    # TRUNCATE=32. El candado exige DOS capas por tabla: FOR EACH ROW contra
    # UPDATE/DELETE y FOR EACH STATEMENT contra TRUNCATE — los triggers de
    # fila NO se disparan con TRUNCATE, y sin la segunda capa un TRUNCATE
    # borraría la historia entera sin pasar por prohibir_mutacion
    # (hallazgo CodeRabbit, PR #1).
    capa_fila = set()
    capa_truncate = set()
    for t in TRIGGERS:
        if not (t.funcname and t.funcname[-1].sval == "prohibir_mutacion"):
            continue
        assert t.timing == 2, f"{t.relation.relname}: prohibir_mutacion debe ser BEFORE"
        if t.row:
            assert t.events & 8 and t.events & 16, (
                f"{t.relation.relname}: el candado de fila debe cubrir UPDATE y DELETE"
            )
            capa_fila.add(t.relation.relname)
        else:
            assert t.events & 32, (
                f"{t.relation.relname}: el candado de sentencia debe cubrir TRUNCATE"
            )
            capa_truncate.add(t.relation.relname)
    assert capa_fila >= APPEND_ONLY, (
        f"tablas append-only sin candado de fila (UPDATE/DELETE): {APPEND_ONLY - capa_fila}"
    )
    assert capa_truncate >= APPEND_ONLY, (
        f"tablas append-only sin candado de sentencia (TRUNCATE): {APPEND_ONLY - capa_truncate}"
    )


def test_nuevos_candados_de_rango_positivo():
    # Ronda CodeRabbit (PR #1): bids y target ACoS acotados por abajo, y la
    # conciliación con período coherente (append-only: un período invertido
    # no se podría corregir con UPDATE).
    goals = _check_constraints("ads_optimizer_goal")
    assert "goal_bids_positivos" in goals
    assert "goal_harvest_bid_positivo" in goals
    assert "goal_target_acos_positivo" in goals
    bids = repr(goals["goal_bids_positivos"].raw_expr)
    assert "bid_floor" in bids and "bid_ceiling" in bids
    reconciliacion = _check_constraints("external_reconciliation")
    assert "reconciliacion_periodo_coherente" in reconciliacion
    periodo = repr(reconciliacion["reconciliacion_periodo_coherente"].raw_expr)
    assert "period_start" in periodo and "period_end" in periodo


def test_ninguna_columna_usa_float():
    for tabla, cols in ((n, _cols(n)) for n in TABLES):
        for col in cols.values():
            tipo = _type_name(col).split(".")[-1]
            assert tipo not in TIPOS_FLOAT, f"{tabla}.{col.colname} usa {tipo}"


def test_toda_tabla_con_dinero_tiene_moneda():
    for tabla in TABLES:
        tipos = {_type_name(c) for c in _cols(tabla).values()}
        if "money_amount" in tipos:
            assert "currency" in tipos, f"{tabla} tiene money_amount sin currency"


def test_decision_tiene_ventana_not_null():
    cols = _cols("decision")
    for nombre in ("window_start", "window_end"):
        assert nombre in cols, f"decision.{nombre} no existe"
        assert enums.ConstrType.CONSTR_NOTNULL in _contypes(cols[nombre])


def test_trigger_madurez_cubre_los_tres_cortes():
    candidatos = [
        t
        for t in TRIGGERS
        if t.relation.relname == "decision"
        and t.funcname
        and t.funcname[-1].sval == "decision_madurez_corte"
    ]
    assert candidatos, "falta el trigger decision_madurez_corte en decision"
    # BEFORE INSERT FOR EACH ROW (constantes de parsenodes.h: BEFORE=2, INSERT=4).
    assert any(t.timing == 2 and t.events & 4 and t.row for t in candidatos)
    # Los kinds viven en el cuerpo PL/pgSQL: el AST de CREATE FUNCTION lo trae
    # como constante de texto (pglast no parsea cuerpos plpgsql completos), así
    # que la lista de kinds se afirma sobre ESE cuerpo extraído del AST.
    cuerpo = " ".join(_body_plpgsql("decision_madurez_corte").split())
    assert "NEW.kind IN ('pause', 'negative', 'harvest')" in cuerpo, (
        "el trigger de madurez no cubre exactamente pause/negative/harvest"
    )


def test_indices_de_cooldown_en_decision():
    columnas = {
        tuple(p.name for p in i.indexParams) for i in INDEXES if i.relation.relname == "decision"
    }
    assert ("ad_entity_id", "decided_at") in columnas  # cooldown 7d por entidad
    assert ("cycle_id",) in columnas  # reconstrucción del envelope
    # "una decisión por entidad (o entidad+término) por ciclo": dos únicos
    # parciales (con WHERE), uno por familia de kinds.
    unicos_parciales = [
        i
        for i in INDEXES
        if i.relation.relname == "decision" and i.unique and i.whereClause is not None
    ]
    assert len(unicos_parciales) == 2


def test_is_asin_like_obligatorio_sin_default():
    col = _cols("search_term_observation")["is_asin_like"]
    tipos = _contypes(col)
    assert enums.ConstrType.CONSTR_NOTNULL in tipos
    assert enums.ConstrType.CONSTR_DEFAULT not in tipos, (
        "is_asin_like con default = 'no lo revisé' disfrazado de 'no es ASIN'"
    )


def test_checks_de_signo_y_cantidad_en_ledger():
    checks = _check_constraints("ledger_event")
    # Afirmar la FORMA de la expresión, no solo el nombre: un CHECK con
    # expresión NULL pasa (no es FALSE), así que la obligación de quantity
    # solo existe si el NULL está prohibido explícitamente (NullTest).
    signos = repr(checks["ledger_convencion_signos"].raw_expr)
    assert "amount" in signos and "kind" in signos
    cantidad = repr(checks["ledger_venta_con_cantidad"].raw_expr)
    assert "NullTest" in cantidad and "quantity" in cantidad, (
        "ledger_venta_con_cantidad sin NullTest: quantity NULL pasaría el CHECK"
    )


def test_sello_moneda_en_ambas_tablas_de_metricas():
    selladas = {
        t.relation.relname
        for t in TRIGGERS
        if t.funcname and t.funcname[-1].sval == "metric_moneda_de_plataforma"
    }
    assert {"ads_metric_observation", "search_term_observation"} <= selladas


def test_sello_moneda_cruza_contra_entidad_en_search_terms():
    # D1 (ronda cross-review grok): en search_term_observation la plataforma
    # viene desnormalizada en la fila y la PK la incluye — el trigger debe
    # cruzarla contra ad_entity y RECHAZAR la fila que declara otra
    # plataforma. Sin el cruce, el mismo hecho cabría dos veces con
    # plataformas distintas y el sello sellaría la equivocada. Una
    # reescritura lo revirtió en silencio; este test lo amarra al AST.
    cuerpo = " ".join(_body_plpgsql("metric_moneda_de_plataforma").split())
    assert "FROM ad_entity" in cuerpo
    assert "la fila declara plataforma" in cuerpo, (
        "falta el rechazo de la fila desnormalizada (regresión D1)"
    )


def _grants_update_sobre(tabla):
    """AccessPriv de UPDATE sobre `tabla` en la migración (para el AST real)."""
    resultado = []
    for s in STMTS:
        st = s.stmt
        if not isinstance(st, ast.GrantStmt):
            continue
        if st.targtype != enums.GrantTargetType.ACL_TARGET_OBJECT:
            continue
        if not any(isinstance(o, ast.RangeVar) and o.relname == tabla for o in st.objects):
            continue
        resultado.extend(p for p in st.privileges if p.priv_name == "update")
    return resultado


def test_ad_entity_update_solo_por_columnas():
    # D7 (ronda cross-review grok): la identidad de la entidad (platform,
    # kind, external_id, parent_id, match_type, keyword_text) es inmutable
    # por permisos — mutarla tras insertar hechos rompería el sello de
    # moneda a posteriori y dejaría goals apuntando a kinds que ya no son
    # campaign. Un GRANT UPDATE genérico sobre ad_entity lo rompería en
    # silencio.
    updates = _grants_update_sobre("ad_entity")
    assert updates, "falta el GRANT UPDATE sobre ad_entity"
    genericos = [p for p in updates if not p.cols]
    assert not genericos, "existe GRANT UPDATE genérico sobre ad_entity"
    permitidas = {c.sval for p in updates for c in (p.cols or ())}
    assert permitidas == {"name", "listing_id"}, (
        f"ad_entity admite UPDATE en columnas no declaradas: {permitidas}"
    )


def test_dedupe_ledger_cubre_todo_el_espacio():
    # D8 (ronda cross-review grok): entre las tres claves de dedupe queda
    # cubierto TODO el espacio (source_event_id / order_id / ninguno). Una
    # fila con order_id pero sin source_event_id (re-sync de fees) no caía en
    # ninguna de las dos primeras y se duplicaba en silencio.
    def nulltests(expr):
        if isinstance(expr, ast.NullTest):
            yield expr.arg.fields[-1].sval, expr.nulltesttype
        elif isinstance(expr, ast.BoolExpr):
            for arg in expr.args:
                yield from nulltests(arg)

    claves = {}
    for i in INDEXES:
        if i.relation.relname != "ledger_event" or not i.unique:
            continue
        if i.whereClause is None:
            continue
        r = dict(nulltests(i.whereClause))
        claves.setdefault((r.get("source_event_id"), r.get("order_id")), i.idxname)
    cubierto = {
        (enums.NullTestType.IS_NOT_NULL, None): "ledger_dedupe_source",
        (enums.NullTestType.IS_NULL, enums.NullTestType.IS_NULL): "ledger_dedupe_sin_orden",
        (enums.NullTestType.IS_NULL, enums.NullTestType.IS_NOT_NULL): "ledger_dedupe_con_orden",
    }
    for combinacion, nombre in cubierto.items():
        assert claves.get(combinacion) == nombre, (
            f"falta el índice de dedupe {nombre} en ledger_event ({combinacion})"
        )


def test_no_negativos_incluye_revenue_same_sku():
    # D10 (ronda cross-review grok): revenue_same_sku es venta atribuida por
    # Amazon (halo) y un negativo ahí es tan bug de ingesta como un costo
    # negativo.
    checks = _check_constraints("ads_metric_observation")
    assert "revenue_same_sku" in repr(checks["metric_no_negativos"].raw_expr), (
        "metric_no_negativos sin revenue_same_sku"
    )


def test_quota_no_excedida_es_backstop():
    # D2 (ronda cross-review grok): used <= cap es el backstop del consumo
    # atómico (la defensa de carrera es WHERE used < cap en la app); si el
    # CHECK desaparece, el tope descansa solo en la disciplina de la app.
    checks = _check_constraints("apply_quota_state")
    assert "quota_no_excedida" in checks, "falta quota_no_excedida en apply_quota_state"
    expr = repr(checks["quota_no_excedida"].raw_expr)
    assert "used" in expr and "cap" in expr


def test_v_tacos_convierte_por_fila_a_mxn():
    # D3 (ronda cross-review grok): la vista convierte CADA lado por fila a la
    # canónica MXN con fx_resolve y el JOIN es por (platform, mes) sobre
    # montos ya en MXN. El viejo suponía UNA moneda de venta por
    # plataforma+mes (columnas moneda_gasto/moneda_venta): si reaparecen, el
    # supuesto volvió y el gasto se repetiría por fila ante dos monedas.
    tacos = [v for v in _stmts(ast.ViewStmt) if v.view.relname == "v_tacos"]
    assert tacos, "falta la vista v_tacos"
    cuerpo = repr(tacos[0].query)
    assert "fx_resolve" in cuerpo, "v_tacos sin conversión por fila"
    assert "MXN" in cuerpo, "v_tacos sin moneda canónica MXN"
    assert "moneda_gasto" not in cuerpo and "moneda_venta" not in cuerpo, (
        "v_tacos: el supuesto de moneda única volvió (moneda_gasto/moneda_venta)"
    )


def test_v_tacos_fail_loud_con_huecos_parciales_de_fx():
    # Ronda CodeRabbit (PR #1): un hueco PARCIAL de FX (algunas filas sin tasa
    # utilizable) antes publicaba un tacos_pct corto sin señal — SUM ignora
    # los NULL. Ahora la vista cuenta las filas sin convertir por lado y
    # tacos_pct sale NULL con una sola.
    tacos = [v for v in _stmts(ast.ViewStmt) if v.view.relname == "v_tacos"]
    assert tacos, "falta la vista v_tacos"
    cuerpo = repr(tacos[0].query)
    assert "gasto_sin_tasa" in cuerpo and "ventas_sin_tasa" in cuerpo, (
        "v_tacos sin contadores de filas no convertibles por lado"
    )


# ---------------------------------------------------------------------------
# (b) INTEGRACIÓN — skip automático sin Postgres local
# ---------------------------------------------------------------------------


def _test_dsn():
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _hay_postgres_local():
    """¿Hay un Postgres UTILIZABLE en el DSN de prueba?

    Un probe TCP no basta: un túnel SSH muerto (o cualquier proceso colgado
    del puerto) acepta la conexión y luego cierra sin hablar protocolo
    Postgres — el test corría contra ese agujero y fallaba con
    OperationalError tras ~2 min de timeout en vez de skipear (caso real:
    túnel SSH caído en 127.0.0.1:5432 bloqueando el pre-push, ronda
    cross-review claude). Hay que hablar Postgres de verdad, con tope corto.
    """
    psycopg = pytest.importorskip("psycopg")
    try:
        with psycopg.connect(_test_dsn(), connect_timeout=2):
            return True
    except psycopg.Error:
        return False


def test_probe_rechaza_listener_muerto(monkeypatch):
    """Un listener que acepta TCP y cierra sin hablar Postgres NO es "hay
    Postgres": es el túnel SSH muerto que tumbó el pre-push. Con el probe TCP
    viejo este test daría True (regresión)."""
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _aceptar_y_cerrar():
        try:
            conn, _ = srv.accept()
            conn.close()
        except OSError:
            pass

    threading.Thread(target=_aceptar_y_cerrar, daemon=True).start()
    monkeypatch.setenv("ORBIT_TEST_DSN", f"postgresql://u:p@127.0.0.1:{port}/postgres")
    try:
        assert _hay_postgres_local() is False
    finally:
        srv.close()


@pytest.mark.skipif(
    not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_migracion_rechaza_en_vivo():
    """Aplica la migración en una base temporal y prueba 6 rechazos reales.

    El DSN viene de ORBIT_TEST_DSN (default: el docker-compose.yml del repo,
    POSTGRES_USER=orbit). Sin él, psycopg usaría el usuario del SO y el test
    fallaría por autenticación contra el compose declarado (D12, ronda
    cross-review grok).
    """
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    dsn = _test_dsn()
    db = f"orbit_schema_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    # Rol centinela: prueba que la migración NO toca roles pre-existentes del
    # cluster (hallazgo CodeRabbit PR #1: antes el test DROPEABA los roles
    # app_* — de cluster, no de base — y no los restauraba; contra un cluster
    # compartido eso destruye roles ajenos). La migración ahora crea los roles
    # solo si faltan (guard pg_roles), así que no hay que borrar nada. El
    # centinela lo crea y lo destruye este mismo test.
    centinela = f"orbit_sentinel_{os.getpid()}"
    try:
        # sql.Identifier: el nombre lleva el hostname; componer el DDL a mano
        # con "{db}" se rompe con un hostname con comillas (hallazgo SAST).
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
        admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(centinela)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        try:
            conn.execute("SET TIME ZONE 'UTC'")
            conn.execute(SQL)  # la migración entera, con su BEGIN/COMMIT

            # Los roles del esquema existen y el centinela (ajeno a la
            # migración) sobrevivió intacto: roles de cluster intactos.
            esperados = {centinela, "app_ingest", "app_decide", "app_read", "app_admin"}
            roles = {
                r[0]
                for r in conn.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                    (list(esperados),),
                )
            }
            assert esperados <= roles

            run_id = conn.execute(
                "INSERT INTO ingest_run (source) VALUES ('test') RETURNING id"
            ).fetchone()[0]
            entidad_us = conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id)"
                " VALUES ('amazon_us', 'campaign', 'T1') RETURNING id"
            ).fetchone()[0]

            # 1. Métrica en moneda que no corresponde a la plataforma.
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                    " observed_at, metric_currency, ingest_run_id) VALUES"
                    f" ({entidad_us}, '2026-08-01', now(), 'MXN', {run_id})"
                )

            # 2. Pausa con ventana inmadura (window_end = hoy).
            config_id = conn.execute(
                "INSERT INTO config_version (settings) VALUES ('{}') RETURNING id"
            ).fetchone()[0]
            ciclo_id = conn.execute(
                "INSERT INTO optimizer_cycle (mode, platform)"
                " VALUES ('shadow', 'amazon_us') RETURNING id"
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO decision (cycle_id, ad_entity_id, kind,"
                    " config_version_id, data_observed_at, window_start, window_end,"
                    " inputs) VALUES"
                    f" ({ciclo_id}, {entidad_us}, 'pause', {config_id},"
                    " now(), CURRENT_DATE - 30, CURRENT_DATE, '{}')"
                )

            # 3. Re-ingestar el mismo reporte duplica la observación.
            conn.execute(
                "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                " observed_at, metric_currency, source_report_id, ingest_run_id)"
                f" VALUES ({entidad_us}, '2026-08-01', now(), 'USD', 'R1', {run_id})"
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                    " observed_at, metric_currency, source_report_id, ingest_run_id)"
                    f" VALUES ({entidad_us}, '2026-08-01', now() + interval '1h',"
                    f" 'USD', 'R1', {run_id})"
                )
            # 4. Desglose fiscal negativo (D13 cubre los 4 campos; aquí se
            # prueba item_tax en vivo).
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO ledger_event (platform, kind, event_date,"
                    " amount, amount_currency, item_tax, ingest_run_id) VALUES"
                    f" ('amazon_us', 'sale', '2026-08-01', 100, 'USD', -1, {run_id})"
                )

            # 5. Un harvest_job debe NACER en fase pending; las demás fases
            # solo se alcanzan por UPDATE. La decisión madura (window_end hace
            # 15 días) además es el control positivo del corte de madurez.
            decision_ok = conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind,"
                " config_version_id, data_observed_at, window_start, window_end,"
                " inputs) VALUES"
                f" ({ciclo_id}, {entidad_us}, 'pause', {config_id},"
                " now(), CURRENT_DATE - 45, CURRENT_DATE - 15, '{}') RETURNING id"
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO harvest_job (decision_id, search_term, platform,"
                    " ad_entity_id, fase) VALUES"
                    f" ({decision_ok}, 'term', 'amazon_us', {entidad_us}, 'done')"
                )

            # 6. TRUNCATE no esquiva el candado append-only: los triggers de
            # fila no se disparan con TRUNCATE; hace falta el de sentencia.
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute("TRUNCATE fx_rate")
        finally:
            # D14 (ronda cross-review grok): sin el guard, un fallo del
            # segundo connect dejaría `conn` sin asignar y el finally lanzaría
            # NameError tapando el error real de conexión.
            if conn is not None:
                conn.close()
    finally:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(centinela)))
        admin.close()
