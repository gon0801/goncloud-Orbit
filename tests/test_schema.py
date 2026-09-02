"""Tests del esquema `migrations/0001_initial.sql`.

(a) ESTÁTICOS: parsean la migración con pglast y afirman los invariantes
    sellados del diseño sobre el AST (no sobre texto). Corren siempre.
(b) INTEGRACIÓN: aplican la migración en un Postgres temporal y prueban
    rechazos reales. Skip automático si no hay servidor en localhost:5432.

Regla 8 del diseño: los invariantes se prueban contra la forma real del dato;
aquí la "forma real" es el AST que PostgreSQL ejecutaría.
"""

import os
import re
import socket
from pathlib import Path

import pglast
import pytest
from pglast import ast, enums

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations" / "0001_initial.sql").read_text(encoding="utf-8")
STMTS = tuple(pglast.parse_sql(SQL))

# 0002 (ORBIT 04, task 1.2 — sellado 24 del header: test_schema parsea TAMBIEN
# 0002 para que los invariantes cubran las tablas nuevas).
SQL2 = (ROOT / "migrations" / "0002_apply.sql").read_text(encoding="utf-8")
STMTS2 = tuple(pglast.parse_sql(SQL2))

# 0003 (ORBIT 05 preflight 1.2 — mismo criterio: los invariantes del sello
# "sin DEFAULT en piso/techo" se afirman sobre el AST de la migracion).
SQL3 = (ROOT / "migrations" / "0003_goal_bounds_explicit.sql").read_text(encoding="utf-8")
STMTS3 = tuple(pglast.parse_sql(SQL3))

# 0004 (ORBIT 06 0.4): solo ADD VALUE 'product_ad'. El valor nuevo no se
# puede usar en la misma transaccion que lo agrega.
SQL4 = (ROOT / "migrations" / "0004_ad_entity_kind_product_ad.sql").read_text(encoding="utf-8")
STMTS4 = tuple(pglast.parse_sql(SQL4))

# 0005 (ORBIT 06 0.7 — hallazgo de qwen en la review 3.3): v_tacos sumaba el
# gasto por fila kind='campaign' Y por sus hijas kind='keyword'/
# 'product_target' -- ads_metric_observation duplica el costo entre ambos
# grados. Filtro de grano único en el CTE `gasto`.
SQL5 = (ROOT / "migrations" / "0005_v_tacos_grano_unico.sql").read_text(encoding="utf-8")
STMTS5 = tuple(pglast.parse_sql(SQL5))

# 0006 (ORBIT 06 1.2): contribucion por entidad + residual campaign +
# cobertura por motivo + desfase ads metricas vs ledger (D6).
SQL6 = (ROOT / "migrations" / "0006_contribucion_entidad.sql").read_text(encoding="utf-8")
STMTS6 = tuple(pglast.parse_sql(SQL6))

# 0007 (ORBIT 06 1.2 perf): misma vista, cadena de CTEs rearmada — fx_resolve
# por DIA (era LATERAL por fila: 2.2M llamadas en prod) y sku_cost as-of por
# (producto, dia) (era sonda de rango por fila). Semantica identica (diff
# fila-por-fila en prod antes de aplicar).
SQL7 = (ROOT / "migrations" / "0007_contribucion_perf.sql").read_text(encoding="utf-8")
STMTS7 = tuple(pglast.parse_sql(SQL7))

# 0008 (ORBIT 06 1.5): precio multilisting US — MIN marcado (enmienda D1.bis,
# sello del dueno 2026-09-01). Agrega UNA columna al final de
# v_contribucion_entidad: precio_min_multilisting.
SQL8 = (ROOT / "migrations" / "0008_precio_multilisting_us.sql").read_text(encoding="utf-8")
STMTS8 = tuple(pglast.parse_sql(SQL8))

# 0009 (ORBIT 06 1.5b): escala de dinero en la frontera de la vista — ROUND
# a 4 decimales en cogs/contrib computados (bug prod: colas de ~40 digitos).
SQL9 = (ROOT / "migrations" / "0009_contribucion_redondeo.sql").read_text(encoding="utf-8")
STMTS9 = tuple(pglast.parse_sql(SQL9))

# 0010 (ORBIT 06 1.5 cross-review): la marca precio_min_multilisting exige
# PESO (el MIN entro al ratio; presencia sin ventas no marca) y contrib se
# computa del cogs YA redondeado (columnas publicadas reconcilian al 4o
# decimal). Hallazgos media de claude/codex/grok 2026-09-01.
SQL10 = (ROOT / "migrations" / "0010_contribucion_cross_review.sql").read_text(encoding="utf-8")
STMTS10 = tuple(pglast.parse_sql(SQL10))

# 0012 (residual de la 0005, cross-review codex 2026-08-31): el residuo del
# grano —gasto_campaign_sin_contraparte— deja de ser columna informativa y
# ANULA tacos_pct por encima del 1.00 % de gasto_ads, en valor absoluto.
# Agrega residuo_pct al final. 0011 no entra aqui: es una migracion de DATOS.
SQL12 = (ROOT / "migrations" / "0012_tacos_residuo_fail_loud.sql").read_text(encoding="utf-8")
STMTS12 = tuple(pglast.parse_sql(SQL12))

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

# Tablas que guardan dinero en NUMERIC crudo, fuera del dominio money_amount:
# el detector por tipo no las ve y se saltarian el candado de la regla 4.
DINERO_EN_NUMERIC = {"decision"}

# Kinds de decision cuyo old_value/new_value ES un importe.
DECISION_KINDS_CON_DINERO = ("bid", "budget", "harvest")


def _stmts(cls):
    return [s.stmt for s in STMTS if isinstance(s.stmt, cls)]


def _v_tacos_viva():
    """La definicion VIVA de v_tacos: la ULTIMA ViewStmt a traves de todas
    las migraciones (0005/0006/0012 la reemplazan con CREATE OR REPLACE). Los
    invariantes sellados se afirman contra ESTA — afirmarlos contra la de
    0001 tras aplicar 0005 es vigilar SQL muerto (hallazgo del adversario
    en la review de la 0005: una regresion dentro de 0005 que conservara
    las columnas viajaba en verde)."""
    vistas = [
        s.stmt
        for lote in (STMTS, STMTS2, STMTS3, STMTS4, STMTS5, STMTS6, STMTS12)
        for s in lote
        if isinstance(s.stmt, ast.ViewStmt) and s.stmt.view.relname == "v_tacos"
    ]
    assert vistas, "falta la vista v_tacos en las migraciones"
    return vistas[-1]


def _tacos_pct_viva():
    """El subarbol del target `tacos_pct` de la definicion viva de v_tacos.

    Los fail-loud se afirman DENTRO de este subarbol y no por presencia en
    el cuerpo entero: cada contador aparece ademas en su COUNT y su
    COALESCE, asi que "esta en alguna parte" deja pasar la mutacion exacta
    del adversario de la 0005 (borrar SOLO la rama del CASE conservando las
    columnas) — comprobado midiendo: la presencia global sobrevive, el
    subarbol de tacos_pct no."""
    query = _v_tacos_viva().query
    targets = [r for r in query.targetList if getattr(r, "name", None) == "tacos_pct"]
    assert targets, "v_tacos vivo sin columna tacos_pct"
    return repr(targets[0])


def _stmts2(cls):
    return [s.stmt for s in STMTS2 if isinstance(s.stmt, cls)]


def _stmts3(cls):
    return [s.stmt for s in STMTS3 if isinstance(s.stmt, cls)]


def _stmts4(cls):
    return [s.stmt for s in STMTS4 if isinstance(s.stmt, cls)]


TABLES = {t.relation.relname: t for t in _stmts(ast.CreateStmt)}
TRIGGERS = _stmts(ast.CreateTrigStmt)
INDEXES = _stmts(ast.IndexStmt)
FUNCTIONS = {f.funcname[-1].sval: f for f in _stmts(ast.CreateFunctionStmt)}

TABLES2 = {t.relation.relname: t for t in _stmts2(ast.CreateStmt)}
TRIGGERS2 = _stmts2(ast.CreateTrigStmt)
INDEXES2 = _stmts2(ast.IndexStmt)
FUNCTIONS2 = {f.funcname[-1].sval: f for f in _stmts2(ast.CreateFunctionStmt)}

# Union para los invariantes TRANSVERSALES (regla del repo que vale igual para
# toda migracion): toda FK con indice de apoyo, nada de float para dinero,
# ningun CHECK dependiente de la TimeZone de sesion.
TABLAS_TOTALES = {**TABLES, **TABLES2}
INDEXES_TOTALES = (*INDEXES, *INDEXES2)


def _cols(tabla):
    return {e.colname: e for e in TABLES[tabla].tableElts if isinstance(e, ast.ColumnDef)}


def _cols_de(tables, tabla):
    return {e.colname: e for e in tables[tabla].tableElts if isinstance(e, ast.ColumnDef)}


def _cols2(tabla):
    return _cols_de(TABLES2, tabla)


def _type_name(coldef):
    return ".".join(n.sval for n in coldef.typeName.names)


def _contypes(coldef):
    return {c.contype for c in (coldef.constraints or ())}


def _check_constraints(tabla):
    return _checks_de(TABLES, tabla)


def _checks_de(tables, tabla):
    return {
        e.conname: e
        for e in tables[tabla].tableElts
        if isinstance(e, ast.Constraint)
        and e.contype == enums.ConstrType.CONSTR_CHECK
        and e.conname
    }


def _body_plpgsql(nombre_funcion):
    """Cuerpo de una función PL/pgSQL, extraído del AST de CREATE FUNCTION."""
    return _body_de(FUNCTIONS, nombre_funcion)


def _body_de(funciones, nombre_funcion):
    for opt in funciones[nombre_funcion].options:
        if opt.defname == "as":
            return "".join(parte.sval for parte in opt.arg)
    raise AssertionError(f"{nombre_funcion}: sin cuerpo 'as' en el AST")


def _pares_de_transiciones(cuerpo):
    """Extrae los pares ('desde', 'hasta') de una tabla de transiciones
    escrita como (OLD.campo, NEW.campo) IN ( ('a','b'), ... ) THEN en el
    cuerpo plpgsql del trigger. ANCLADA al patrón de la tabla y NO-GREEDY
    hasta el primer ') THEN' (el cierre de la tabla): la versión naïve
    (cualquier par de strings consecutivos) confundió el NOT IN
    ('vetoed','discarded') del perímetro shadow con una transición, y el
    greedy tragaría la tabla entera hasta el ') THEN' del propio guard
    (hallazgo del rojo del guard, reviewer r1 de 1.2)."""
    tabla = re.search(r"\(OLD\.(\w+),\s*NEW\.\1\)\s*IN\s*\((.*?)\)\s*THEN", cuerpo, re.DOTALL)
    assert tabla, "no se encontró la tabla (OLD.campo, NEW.campo) IN (...) del trigger"
    return set(re.findall(r"\('([a-z_]+)',\s*'([a-z_]+)'\)", tabla.group(2)))


def _grants_sobre_2(tabla, priv):
    """{rol: set(columnas)} de los GRANT <priv> [ (cols) ] sobre `tabla` en 0002."""
    resultado: dict[str, set[str]] = {}
    for s in STMTS2:
        st = s.stmt
        if not isinstance(st, ast.GrantStmt):
            continue
        if st.targtype != enums.GrantTargetType.ACL_TARGET_OBJECT:
            continue
        if not any(isinstance(o, ast.RangeVar) and o.relname == tabla for o in st.objects):
            continue
        for p in st.privileges:
            if p.priv_name != priv:
                continue
            for rol in st.grantees:
                resultado.setdefault(rol.rolename, set()).update(c.sval for c in (p.cols or ()))
    return resultado


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
    for tabla, cols in ((n, _cols_de(TABLAS_TOTALES, n)) for n in TABLAS_TOTALES):
        for col in cols.values():
            tipo = _type_name(col).split(".")[-1]
            assert tipo not in TIPOS_FLOAT, f"{tabla}.{col.colname} usa {tipo}"


def test_toda_tabla_con_dinero_tiene_moneda():
    # OJO: detectar el dinero SOLO por el dominio money_amount deja fuera a
    # `decision`, que guarda el bid/budget viejo y nuevo en NUMERIC crudo — la
    # tabla se saltaba esta prueba entera. Y "existe una columna currency" no
    # es el invariante de la regla 4: el invariante es que un importe SIN
    # moneda sea imposible.
    for tabla in TABLAS_TOTALES:
        cols = _cols_de(TABLAS_TOTALES, tabla)
        tipos = {_type_name(c) for c in cols.values()}
        if "money_amount" not in tipos and tabla not in DINERO_EN_NUMERIC:
            continue
        assert "currency" in tipos, f"{tabla} guarda dinero sin columna currency"
        checks = _check_constraints(tabla)
        for nombre, col in cols.items():
            if _type_name(col) != "currency":
                continue
            if enums.ConstrType.CONSTR_NOTNULL in _contypes(col):
                continue
            atada = any(nombre in repr(c.raw_expr) for c in checks.values())
            assert atada, (
                f"{tabla}.{nombre} es nullable y ningun CHECK la ata al importe: "
                "un monto sin moneda entraria en silencio (regla 4)"
            )


def test_decision_exige_moneda_en_todo_kind_con_dinero():
    # `harvest` mueve dinero: new_value es el bid inicial de la keyword
    # harvesteada (goal.harvest_default_bid). Como old_value/new_value son
    # NUMERIC crudo y no money_amount, ningun otro candado del esquema lo
    # cubria: una decision harvest podia registrar el importe SIN moneda y un
    # SUM() sobre decision volvia a mezclar MXN con USD (regla 4).
    checks = _check_constraints("decision")
    expr = repr(checks["decision_valor_con_moneda"].raw_expr)
    for kind in DECISION_KINDS_CON_DINERO:
        assert f"'{kind}'" in expr, f"decision_valor_con_moneda no exige moneda para kind={kind}"
    # Y la inversa: moneda suelta en un kind sin importe es dato inventado.
    assert "decision_moneda_solo_en_kinds_con_dinero" in checks, (
        "falta el CHECK inverso: value_currency en un kind sin dinero"
    )


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
    cuerpo = repr(_v_tacos_viva().query)
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
    pct = _tacos_pct_viva()
    assert "gasto_sin_tasa" in pct and "ventas_sin_tasa" in pct, (
        "v_tacos: los contadores de filas sin tasa ya no participan del CASE "
        "de tacos_pct (el fail-loud de FX parcial se perdio)"
    )


# ---------------------------------------------------------------------------
# ESTATICOS de 0012 — el residuo del grano anula tacos_pct
# ---------------------------------------------------------------------------


def test_0012_residuo_participa_del_case_de_tacos_pct():
    """El fail-loud nuevo vive DENTRO del CASE de tacos_pct, no al lado.

    Mismo criterio que los otros tres contadores: se afirma sobre el subarbol
    del target, no por presencia en el cuerpo — `gasto_campaign_sin_contraparte`
    aparece ademas como columna propia, asi que "esta en alguna parte" dejaria
    pasar la mutacion exacta de borrar SOLO la rama del CASE.
    """
    pct = _tacos_pct_viva()
    assert "gasto_campaign_sin_contraparte" in pct, (
        "v_tacos: el residuo del grano ya no anula tacos_pct — un keywordType "
        "fuera de la allowlist volveria a publicar un TACoS corto en silencio"
    )


def test_0012_umbral_es_el_medido():
    """El umbral es 1.00 %, medido en prod (peor mes real: 0.0773 %).

    Un test que solo mirara "hay un ABS" pasaria con cualquier numero; el
    umbral ES la decision, asi que se fija.
    """
    pct = _tacos_pct_viva()
    assert "1.00" in pct, f"el umbral del residuo no es 1.00 en el CASE: {pct[:400]}"


def test_0012_residuo_se_juzga_en_valor_absoluto():
    """Hijas por encima de su campana rompe el supuesto de grano igual que al
    reves: la comparacion va en valor absoluto, no solo por arriba."""
    pct = _tacos_pct_viva()
    assert "ABS" in pct.upper(), (
        "v_tacos: el residuo se compara con signo — un doble conteo por el "
        "lado de las hijas pasaria en verde"
    )


def test_0012_expone_residuo_pct_al_final():
    """La senal es visible aunque tacos_pct sobreviva, y entra AL FINAL: los
    consumidores por posicion de las columnas viejas no se mueven."""
    query = _v_tacos_viva().query
    nombres = [getattr(r, "name", None) for r in query.targetList]
    assert nombres[-1] == "residuo_pct", f"orden de columnas de v_tacos: {nombres}"
    assert nombres[:8] == [
        "platform",
        "mes",
        "gasto_ads",
        "venta_total",
        "filas_gasto_sin_tasa",
        "filas_venta_sin_tasa",
        "filas_gasto_sin_costo",
        "tacos_pct",
    ], f"0012 movio columnas existentes de v_tacos: {nombres}"


def _lideres_de_indice_no_parcial(indexes=INDEXES_TOTALES, tables=TABLAS_TOTALES):
    """{(tabla, primera_columna)} de todo indice NO parcial de la migracion.

    Cuenta los indices implicitos de PRIMARY KEY / UNIQUE / EXCLUDE: son
    indices reales y sirven igual. Los PARCIALES no cuentan: la verificacion
    de integridad de una FK consulta la clave sin filtro extra y no puede
    apoyarse en un indice con WHERE.
    """
    lideres = set()
    for i in indexes:
        if i.whereClause is not None:
            continue
        primera = i.indexParams[0]
        if primera.name:
            lideres.add((i.relation.relname, primera.name))
    llaves = {enums.ConstrType.CONSTR_PRIMARY, enums.ConstrType.CONSTR_UNIQUE}
    for tabla, t in tables.items():
        for e in t.tableElts:
            if isinstance(e, ast.ColumnDef):
                if _contypes(e) & llaves:
                    lideres.add((tabla, e.colname))
            elif isinstance(e, ast.Constraint):
                if e.contype in llaves:
                    lideres.add((tabla, e.keys[0].sval))
                elif e.contype == enums.ConstrType.CONSTR_EXCLUSION:
                    primera = e.exclusions[0][0]
                    if primera.name:
                        lideres.add((tabla, primera.name))
    return lideres


def _claves_foraneas(tables=TABLES):
    for tabla, t in tables.items():
        for e in t.tableElts:
            if isinstance(e, ast.ColumnDef):
                for c in e.constraints or ():
                    if c.contype == enums.ConstrType.CONSTR_FOREIGN:
                        yield tabla, e.colname
            elif isinstance(e, ast.Constraint) and e.contype == enums.ConstrType.CONSTR_FOREIGN:
                yield tabla, e.fk_attrs[0].sval


def _fks_de_add_column(stmts):
    """FKs agregadas por ALTER TABLE ... ADD COLUMN (0002:
    decision_application.applied_cycle_id). El invariante de indice de apoyo
    tambien vale para las columnas que llegan por ALTER."""
    for s in stmts:
        st = s.stmt
        if not isinstance(st, ast.AlterTableStmt):
            continue
        for cmd in st.cmds:
            if not isinstance(cmd.def_, ast.ColumnDef):
                continue
            for c in cmd.def_.constraints or ():
                if c.contype == enums.ConstrType.CONSTR_FOREIGN:
                    yield st.relation.relname, cmd.def_.colname


def test_toda_fk_tiene_indice_de_apoyo():
    # PostgreSQL NO crea indice por un REFERENCES. Sin el, cada verificacion
    # de integridad al tocar la tabla padre barre la hija entera, y los JOIN
    # que el esquema mismo declara (v_margen_plataforma cruza ledger_event por
    # product_id; v_tacos cruza las metricas por ad_entity_id) salen a
    # secuencial. Vale para 0001 Y 0002 (sellado 24: test_schema parsea 0002).
    fks = (
        set(_claves_foraneas(TABLES))
        | set(_claves_foraneas(TABLES2))
        | set(_fks_de_add_column(STMTS2))
    )
    sin_indice = sorted(fk for fk in fks if fk not in _lideres_de_indice_no_parcial())
    assert not sin_indice, f"FKs sin indice de apoyo: {sin_indice}"


def test_sku_cost_no_se_puede_reescribir_ni_borrar():
    # El COMMENT de sku_cost promete que el importe y valid_from de una fila
    # publicada jamas se reescriben, pero eso descansaba SOLO en el GRANT por
    # columna — exactamente lo que la seccion 16 declara insuficiente ("el
    # candado no debe depender de la AUSENCIA de un GRANT"). Y nada impedia el
    # DELETE: borrar una vigencia publicada reescribe el historico de margenes
    # hacia atras, que es el bug que esta tabla existe para matar.
    fila = [t for t in TRIGGERS if t.relation.relname == "sku_cost" and t.row and t.timing == 2]
    assert fila, "sku_cost sin candado de fila (BEFORE UPDATE OR DELETE)"
    eventos = 0
    for t in fila:
        eventos |= t.events
    assert eventos & 8 and eventos & 16, "el candado de sku_cost debe cubrir UPDATE y DELETE"
    sentencia = [
        t for t in TRIGGERS if t.relation.relname == "sku_cost" and not t.row and t.events & 32
    ]
    assert sentencia, "sku_cost sin candado de sentencia contra TRUNCATE"


def test_sku_cost_cierre_de_vigencia_es_una_sola_transicion():
    # Hallazgo CodeRabbit (PR #2) sobre la propia corrección de este PR: el
    # candado dejaba `valid_to` libre y sólo protegía las demás columnas. Un
    # DATE->DATE (mover el corte) o un DATE->NULL (reabrir la vigencia)
    # reescribe el período histórico en el que ese costo aplicó, y el EXCLUDE
    # NO lo atrapa: encoger un rango nunca genera solapamiento, y extenderlo
    # tampoco si esa fila no tiene sucesora. El caso mudo y peligroso es
    # extender la última vigencia de un producto: los márgenes de días que ese
    # costo nunca cubrió cambian, con cobertura 100% y sin señal.
    cuerpo = " ".join(_body_plpgsql("sku_cost_solo_cierra_vigencia").split())
    assert (
        "IF NEW.valid_to IS DISTINCT FROM OLD.valid_to "
        "AND (OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL) THEN"
    ) in cuerpo, "valid_to admite algo más que la transición única NULL -> fecha"


def test_includes_tax_obligatorio_sin_default():
    # Mismo candado que is_asin_like y con la misma factura detras: la
    # pregunta "el costo lleva IVA?" quedo sin responder un anio y valia 8
    # puntos de margen en MX. Un default la contestaria sola.
    col = _cols("sku_cost")["includes_tax"]
    tipos = _contypes(col)
    assert enums.ConstrType.CONSTR_NOTNULL in tipos
    assert enums.ConstrType.CONSTR_DEFAULT not in tipos, (
        "includes_tax con default = la pregunta fiscal contestada por omision"
    )


def test_ningun_check_depende_de_la_timezone_de_sesion():
    # La cabecera del SQL y docs/DATABASE.md lo declaran: PostgreSQL ACEPTA
    # expresiones STABLE en un CHECK y las evalua segun la TimeZone de cada
    # sesion, asi que el mismo CHECK pasaria en una sesion y fallaria en otra.
    # Los invariantes fecha-vs-timestamp viven en TRIGGERS con UTC fijado.
    # Este test impide que alguien "simplifique" un trigger a CHECK.
    prohibido = ("now", "current_date", "current_timestamp", "localtimestamp", "timezone")
    for tabla in TABLAS_TOTALES:
        for nombre, c in _checks_de(TABLAS_TOTALES, tabla).items():
            expr = repr(c.raw_expr).lower()
            for palabra in prohibido:
                assert f"'{palabra}'" not in expr, (
                    f"{tabla}.{nombre} usa {palabra}: el CHECK se evaluaria "
                    "segun la TimeZone de la sesion"
                )


def test_v_tacos_fail_loud_con_costo_nulo():
    # El mismo agujero que el hueco parcial de FX, por el otro lado: SUM
    # ignora los NULL, asi que una fila de metrica con cost NULL bajaba
    # gasto_ads sin dejar senal y tacos_pct salia CORTO — el sesgo optimista
    # de siempre ("todo se ve rentable"), justo el que la vista existe para
    # matar. El lado de la venta no lo necesita: ledger_event.amount es NOT
    # NULL.
    assert "gasto_sin_costo" in _tacos_pct_viva(), (
        "v_tacos: gasto_sin_costo ya no participa del CASE de tacos_pct "
        "(el fail-loud de cost NULL se perdio)"
    )


def test_v_tacos_vivo_filtra_al_grano_keyword_y_target():
    # 0005 (doble conteo): la definicion VIVA debe filtrar el CTE gasto al
    # grano keyword + product_target — las DOS mitades, y campaign fuera del
    # gasto_ads. 0006 agrega un CTE gasto_campaign APARTADO para el residual;
    # 'campaign' puede aparecer ahi, pero gasto_ads sigue sin sumarlo.
    cuerpo = repr(_v_tacos_viva().query)
    assert "keyword" in cuerpo and "product_target" in cuerpo, (
        "v_tacos vivo sin el filtro de grano (keyword + product_target): "
        "el doble conteo campaign+hijas volvio (migracion 0005/0006)"
    )
    assert "gasto_campaign_sin_contraparte" in cuerpo, (
        "v_tacos vivo sin gasto_campaign_sin_contraparte (migracion 0006 D6)"
    )
    # Y el ENSANCHAMIENTO sigue clavado en ESTATICO (review del lead sobre la
    # 1.2): con la 0006, 'campaign' aparece legitimo en el CTE del residual,
    # asi que el candado global de la 0005 ya no aplicaba — pero soltar la
    # discriminacion local dejaba pasar sumar 'campaign' al IN del CTE gasto
    # (la integracion lo caza solo en CI). Se afirma sobre el SUBARBOL del
    # CTE gasto, patron _tacos_pct_viva.
    ctes = {c.ctename: c for c in _v_tacos_viva().query.withClause.ctes}
    assert "gasto" in ctes, "v_tacos vivo sin CTE gasto"
    assert "campaign" not in repr(ctes["gasto"]), (
        "el CTE gasto de v_tacos incluye 'campaign': el grano se ensancho y "
        "el gasto vuelve a contarse doble (0005/0006)"
    )


def test_0006_parsea():
    assert len(STMTS6) >= 4, "0006 debe definir v_tacos + 3 vistas nuevas"


def test_0006_vistas_contribucion_presentes():
    nombres = {s.stmt.view.relname for s in STMTS6 if isinstance(s.stmt, ast.ViewStmt)}
    assert "v_tacos" in nombres
    assert "v_contribucion_entidad" in nombres
    assert "v_contribucion_cobertura" in nombres
    assert "v_desfase_gasto_ads" in nombres


def test_allowlist_kinds_en_tres_sitios():
    # D6: la allowlist keyword|product_target vive en 3 copias; si una deriva,
    # el grano del motor, de TACoS y de contribucion se desincronizan.
    cob_src = (ROOT / "app" / "cobertura.py").read_text(encoding="utf-8")
    sitios = {
        "v_tacos": repr(_v_tacos_viva().query),
        "cobertura": cob_src,
        # La definicion VIVA es la de 0010 (CREATE OR REPLACE); afirmar contra
        # SQL6/SQL7/SQL8/SQL9 seria vigilar SQL muerto (misma leccion que
        # _v_tacos_viva).
        "v_contribucion_entidad": SQL10,
    }
    for nombre, cuerpo in sitios.items():
        assert "'keyword'" in cuerpo and "'product_target'" in cuerpo, (
            f"allowlist kinds ausente en {nombre}"
        )
    # gasto_ads no debe ensancharse a campaign (el residual vive aparte).
    assert "kind IN ('keyword', 'product_target', 'campaign')" not in SQL6
    assert "kind IN ('campaign', 'keyword', 'product_target')" not in SQL6


def test_0006_fx_trampa_divide_usd_mxn():
    # Sello del lead: convertir MXN→USD es DIVIDIR por fx_resolve(,'USD','MXN'),
    # NUNCA llamar el par invertido (devuelve cero filas).
    compacto = " ".join(SQL6.split())
    assert "fx_resolve" in compacto
    assert "/ fx.rate" in compacto or "/fx.rate" in compacto.replace(" ", "")
    assert "fx_resolve(d.metric_date, 'MXN'" not in compacto, (
        "0006 llama fx_resolve con base MXN (par invertido; cero filas)"
    )
    assert "fx_resolve(m.metric_date, 'MXN'" not in compacto
    # La direccion sellada aparece (USD, MXN).
    assert "fx_resolve(d.metric_date, 'USD'::currency, 'MXN'::currency)" in compacto or (
        "fx_resolve(d.metric_date, 'USD'" in compacto and "'MXN'" in compacto
    )


def test_0006_precio_us_exige_usd_y_dedup():
    compacto = " ".join(SQL6.split())
    assert "price_currency = 'USD'::currency" in compacto
    assert "COUNT(DISTINCT v.listing_price) = 1" in compacto


def test_0006_ventas_peso_moneda_homogenea():
    compacto = " ".join(SQL6.split())
    assert "COUNT(DISTINCT vl.amount_currency) = 1" in compacto
    assert "catalogo_vivo" in compacto


def test_0006_catalogo_dia_sin_rejoin_vivos():
    # catalogo_dia debe usar listing_id de costo_dia, no re-join vivos (fan-out).
    idx = SQL6.find("catalogo_dia AS (")
    assert idx >= 0
    bloque = SQL6[idx : idx + 1200]
    assert "cd.listing_id" in bloque
    assert "JOIN vivos" not in bloque


# ---------------------------------------------------------------------------
# ESTATICOS de 0007_contribucion_perf — ORBIT 06, perf de la 1.2 en prod
# (~100s por consulta medidos en vivo; fx_resolve corria en LATERAL por fila)
# ---------------------------------------------------------------------------


def _vista_de(stmts, nombre):
    for s in stmts:
        if isinstance(s.stmt, ast.ViewStmt) and s.stmt.view.relname == nombre:
            return s.stmt
    raise AssertionError(f"falta CREATE OR REPLACE VIEW {nombre}")


def _columnas_vista(viewstmt):
    # Alias si hay; si la columna es una referencia desnuda (c.col), su nombre
    # real es el del ColumnRef (0009 nombra con AS las columnas que 0008 dejaba
    # desnudas: la interfaz no cambio, el alias solo se hizo explicito).
    cols = []
    for c in viewstmt.query.targetList:
        if c.name:
            cols.append(c.name)
        elif isinstance(c.val, ast.ColumnRef):
            cols.append(c.val.fields[-1].sval)
        else:
            cols.append(None)
    return cols


def test_0007_parsea_y_reemplaza_ambas_vistas():
    assert _vista_de(STMTS7, "v_contribucion_entidad") is not None
    assert _vista_de(STMTS7, "v_contribucion_cobertura") is not None
    # 0007 NO toca v_tacos ni v_desfase_gasto_ads (quedan las de 0006).
    nombres = {s.stmt.view.relname for s in STMTS7 if isinstance(s.stmt, ast.ViewStmt)}
    assert nombres == {"v_contribucion_entidad", "v_contribucion_cobertura"}


def test_0007_misma_interfaz_que_0006():
    # Semantica sellada: las columnas publicadas NO cambian (ni nombre ni orden).
    for vista in ("v_contribucion_entidad", "v_contribucion_cobertura"):
        assert _columnas_vista(_vista_de(STMTS7, vista)) == _columnas_vista(
            _vista_de(STMTS6, vista)
        ), f"0007 cambio la interfaz de {vista}"


def test_0007_fx_por_dia_no_por_fila():
    # El fix: fx_resolve se resuelve UNA VEZ por metric_date (CTE fx_dia),
    # nunca en LATERAL por fila entidad-dia x producto (2.2M llamadas en prod).
    # Se afirma la llamada real ("LATERAL fx_resolve("), no el texto de los
    # COMMENT ON VIEW (que mencionan la funcion y no se ejecutan).
    assert "fx_dia" in SQL7
    llamadas = [m.start() for m in re.finditer(r"LATERAL fx_resolve\(", SQL7)]
    assert len(llamadas) == 2, (
        "fx_resolve debe llamarse 2 veces (una por vista, en su fx_dia); "
        "si reaparece por fila, la vista vuelve a ~100s"
    )
    for pos in llamadas:
        ctx = SQL7[max(0, pos - 400) : pos]
        assert "fx_dia" in ctx, "fx_resolve fuera del CTE fx_dia (por fila otra vez)"
    # Y ningun otro patron de llamada por fila (LEFT JOIN LATERAL de 0006).
    assert "LEFT JOIN LATERAL" not in SQL7


def test_0007_trampa_par_invertido_y_direccion_sellada():
    # Mismo sello que 0006: USD/MXN con division; NUNCA el par invertido.
    compacto = " ".join(SQL7.split())
    assert "fx_resolve(d.metric_date, 'USD'::currency, 'MXN'::currency)" in compacto
    assert "fx_resolve(d.metric_date, 'MXN'" not in compacto
    assert "fx_resolve(m.metric_date, 'MXN'" not in compacto
    assert "/ fxd.rate" in compacto, "US: cost MXN / fx — la division sellada"


def test_0007_costo_asof_por_producto_dia():
    # sku_cost as-of se resuelve por (producto, dia) distinto (CTE
    # costo_producto_dia), no como sonda de rango por fila del millon.
    assert "costo_producto_dia" in SQL7
    idx = SQL7.find("costo_producto_dia AS (")
    assert idx >= 0
    bloque = SQL7[idx : idx + 1500]
    assert "SELECT DISTINCT" in bloque and "JOIN sku_cost" in bloque


# ---------------------------------------------------------------------------
# ESTATICOS de 0008_precio_multilisting_us — ORBIT 06 1.5 (enmienda D1.bis,
# sello del dueno 2026-09-01: precio MENOR, MARCADO)
# ---------------------------------------------------------------------------


def test_0008_parsea_y_reemplaza_solo_entidad():
    assert _vista_de(STMTS8, "v_contribucion_entidad") is not None
    # 0008 NO toca v_contribucion_cobertura (su logica no cambia: las
    # entidades recien publicadas simplemente salen del balde).
    nombres = {s.stmt.view.relname for s in STMTS8 if isinstance(s.stmt, ast.ViewStmt)}
    assert nombres == {"v_contribucion_entidad"}


def test_0008_interfaz_0007_mas_columna_marca():
    # La marca es UNA columna nueva AL FINAL (lo unico que CREATE OR REPLACE
    # permite agregar); el resto de la interfaz queda intacta.
    cols7 = _columnas_vista(_vista_de(STMTS7, "v_contribucion_entidad"))
    cols8 = _columnas_vista(_vista_de(STMTS8, "v_contribucion_entidad"))
    assert cols8 == cols7 + ["precio_min_multilisting"], (
        "0008 solo puede AGREGAR precio_min_multilisting al final de la interfaz"
    )


def test_0008_precio_us_min_y_marca_multilisting():
    compacto = " ".join(SQL8.split())
    assert "MIN(v.listing_price)" in compacto, "US: el precio es el MENOR (sello)"
    # La marca: productos con 2+ precios distintos entre sus vivos.
    assert "COUNT(DISTINCT v.listing_price) > 1" in compacto
    assert "producto_multilisting" in SQL8 and "grupo_multilisting" in SQL8
    # El candado viejo (precio unico o ausente) NO debe sobrevivir en la viva.
    assert "COUNT(DISTINCT v.listing_price) = 1" not in compacto


def test_0008_direccion_fx_y_grano_sellados_intactos():
    # Mismo sello que 0006/0007: USD/MXN con division; NUNCA el par invertido.
    compacto = " ".join(SQL8.split())
    assert "fx_resolve(d.metric_date, 'USD'::currency, 'MXN'::currency)" in compacto
    assert "fx_resolve(d.metric_date, 'MXN'" not in compacto
    assert "/ fxd.rate" in compacto
    assert "'keyword'" in SQL8 and "'product_target'" in SQL8


# ---------------------------------------------------------------------------
# ESTATICOS de 0009_contribucion_redondeo — escala de dinero en la frontera
# (bug prod 2026-09-01: cogs/contrib con colas de ~40 decimales)
# ---------------------------------------------------------------------------


def test_0009_parsea_misma_interfaz_que_0008():
    assert _vista_de(STMTS9, "v_contribucion_entidad") is not None
    # Solo cambian los VALORES (redondeo); la interfaz es identica a 0008.
    nombres = {s.stmt.view.relname for s in STMTS9 if isinstance(s.stmt, ast.ViewStmt)}
    assert nombres == {"v_contribucion_entidad"}
    assert _columnas_vista(_vista_de(STMTS9, "v_contribucion_entidad")) == _columnas_vista(
        _vista_de(STMTS8, "v_contribucion_entidad")
    )


def test_0009_round_en_las_4_columnas_computadas():
    # Regla 4 en la frontera: las columnas de dinero COMPUTADAS salen con la
    # escala del schema. Se afirma el ROUND del SELECT final (texto compacto),
    # no el de los COMMENT (que no se ejecutan).
    compacto = " ".join(SQL9.split())
    assert "ROUND(c.cogs_sin_halo, 4) AS cogs_sin_halo" in compacto
    assert "ROUND(c.cogs_con_halo, 4) AS cogs_con_halo" in compacto
    assert (
        "ROUND(s.revenue_same_sku_sum - s.cost_sum - c.cogs_sin_halo, 4)"
        " AS contrib_sin_halo" in compacto
    )
    assert (
        "ROUND(s.ad_revenue_sum - s.cost_sum - c.cogs_con_halo, 4) AS contrib_con_halo" in compacto
    )


# ---------------------------------------------------------------------------
# ESTATICOS de 0010_contribucion_cross_review — cross-review 1.5 (claude/
# codex/grok 2026-09-01): marca con peso exigido + reconciliacion al 4o
# decimal. Rojos de integracion demostrados en el commit 17e883a.
# ---------------------------------------------------------------------------


def test_0010_parsea_misma_interfaz_que_0009():
    assert _vista_de(STMTS10, "v_contribucion_entidad") is not None
    # Solo cambian VALORES y el disparador de la marca; la interfaz (y la
    # cobertura, que 0010 no toca) quedan identicas a 0009.
    nombres = {s.stmt.view.relname for s in STMTS10 if isinstance(s.stmt, ast.ViewStmt)}
    assert nombres == {"v_contribucion_entidad"}
    assert _columnas_vista(_vista_de(STMTS10, "v_contribucion_entidad")) == _columnas_vista(
        _vista_de(STMTS9, "v_contribucion_entidad")
    )


def test_0010_marca_exige_peso():
    # Hallazgo claude/grok: la marca solo puede prender si el producto
    # multilisting PARTICIPO del ratio (tiene w_i). grupo_multilisting se
    # define sobre `pesos`, no sobre `vivos` (presencia sin ventas mentia).
    idx = SQL10.find("grupo_multilisting AS (")
    assert idx >= 0
    bloque = SQL10[idx : idx + 400]
    assert "FROM pesos" in bloque, "grupo_multilisting debe leer pesos, no vivos"
    assert "FROM vivos" not in bloque
    # Y se define DESPUES de pesos en la cadena (depende de el).
    assert SQL10.find("pesos AS (") < idx


def test_0010_contrib_desde_cogs_redondeado():
    # Hallazgo claude: las columnas publicadas reconcilian — contrib sale del
    # cogs YA redondeado (ROUND externo = identidad), no del crudo.
    compacto = " ".join(SQL10.split())
    assert "ROUND(s.revenue_same_sku_sum - s.cost_sum - ROUND(c.cogs_sin_halo, 4), 4)" in compacto
    assert "ROUND(s.ad_revenue_sum - s.cost_sum - ROUND(c.cogs_con_halo, 4), 4)" in compacto
    # El patron viejo (contrib del cogs crudo) NO debe sobrevivir en la viva.
    assert "s.cost_sum - c.cogs_sin_halo" not in compacto
    assert "s.cost_sum - c.cogs_con_halo" not in compacto


# ---------------------------------------------------------------------------
# (a2) ESTÁTICOS de 0002_apply — ORBIT 04, task 1.2 (docs/APPLY.md es la spec)
# ---------------------------------------------------------------------------

# Tabla de transiciones EXACTA del brief §1.2 (sellado 4). El set EXACTO
# asegura lo tres candados a la vez: no sobra transición (no existe
# applying -> discarded), no falta (released sigue vetable), y los terminales
# (vetoed/applied/failed/discarded) no aparecen como ORIGEN.
BRIEF_TRANSICIONES_APLICAR = {
    ("pending_veto", "vetoed"),
    ("pending_veto", "released"),
    ("pending_veto", "discarded"),
    ("released", "vetoed"),
    ("released", "applying"),
    ("released", "discarded"),
    ("applying", "applied"),
    ("applying", "failed"),
}

# Progresión sellada de harvest_job (sellado 13): la cadena sin saltos ni
# retrocesos, con failed alcanzable desde CUALQUIER fase en vuelo (la matriz
# §6.1 cierra failed desde negative_created Y exact_created; done solo se
# alcanza desde exact_created).
PROGRESION_HARVEST = {
    ("pending", "negative_created"),
    ("negative_created", "exact_created"),
    ("exact_created", "done"),
    ("pending", "failed"),
    ("negative_created", "failed"),
    ("exact_created", "failed"),
}


def test_0002_parsea():
    assert len(STMTS2) > 25  # sanity: la migración completa parseó


def test_0002_apply_queue_nace_pending_veto():
    # Sellado 4 / brief §1.5: un INSERT directo en `released` saltaría la
    # ventana de veto — la fila nace SIEMPRE pending_veto por trigger
    # (patrón harvest_job de 0001).
    candidatos = [
        t
        for t in TRIGGERS2
        if t.relation.relname == "apply_queue"
        and t.funcname
        and t.funcname[-1].sval == "apply_queue_nace_pending_veto"
    ]
    assert candidatos, "falta el trigger de INSERT que exige nacer pending_veto"
    # BEFORE INSERT FOR EACH ROW (constantes de parsenodes.h: BEFORE=2, INSERT=4).
    assert any(t.timing == 2 and t.events & 4 and t.row for t in candidatos)
    cuerpo = " ".join(_body_de(FUNCTIONS2, "apply_queue_nace_pending_veto").split())
    assert "NEW.estado <> 'pending_veto'" in cuerpo


def test_0002_transiciones_exactas_del_brief_y_veto_exige_admin():
    # Sellado 4: la máquina de estados vive en el trigger, no en la app. El
    # set de pares extraído del cuerpo debe ser EXACTAMENTE el del brief.
    candidatos = [
        t
        for t in TRIGGERS2
        if t.relation.relname == "apply_queue"
        and t.funcname
        and t.funcname[-1].sval == "apply_queue_sella_transiciones"
    ]
    assert candidatos, "falta el trigger de UPDATE con la tabla de transiciones"
    assert any(t.timing == 2 and t.events & 16 and t.row for t in candidatos)
    cuerpo = " ".join(_body_de(FUNCTIONS2, "apply_queue_sella_transiciones").split())
    assert _pares_de_transiciones(cuerpo) == BRIEF_TRANSICIONES_APLICAR, (
        "la tabla de transiciones del trigger NO coincide con el brief §1.2"
    )
    # Sellados 4/18 (r2 grok 7): la transición a vetoed exige admin POR
    # SCHEMA — el rol del motor NO veta ni siquiera con el UPDATE del claim.
    assert "pg_has_role(current_user, 'app_admin', 'MEMBER')" in cuerpo
    # Todo UPDATE de la cola ES una transición: no existen updates in-place.
    assert "NEW.estado = OLD.estado" in cuerpo


def test_0002_fila_shadow_jamas_sale_del_permetro_veto_descartar():
    # Sellado 6 (hallazgo reviewer r1 de 1.2): "una fila shadow JAMAS
    # transiciona a released" — y por construccion tampoco a applying/applied/
    # failed (de una fila shadow jamas sale HTTP). Candado de SCHEMA, no de
    # disciplina de la app: de una fila modo='shadow' solo se llega a vetoed
    # (practica del veto) o discarded (flip de ORBIT 05).
    cuerpo = " ".join(_body_de(FUNCTIONS2, "apply_queue_sella_transiciones").split())
    assert "OLD.modo = 'shadow'" in cuerpo, (
        "el trigger de transiciones no condiciona por modo: una fila shadow "
        "podria liberarse por SQL directo"
    )
    assert "NOT IN ('vetoed', 'discarded')" in cuerpo, (
        "el perimetro de una fila shadow es vetoed|discarded, nada mas"
    )


def test_0002_clave_de_efecto_unico_parcial_nulls_not_distinct():
    # Sellado 4 / brief §1.5: a lo sumo un en-vuelo por CLAVE DE EFECTO. El
    # NULLS NOT DISTINCT es OBLIGATORIO porque search_term es NULL en los
    # pause: sin él, dos pauses de la misma entidad no chocarían.
    unicos = [i for i in INDEXES2 if i.relation.relname == "apply_queue" and i.unique]
    parciales = [i for i in unicos if i.whereClause is not None]
    assert len(parciales) == 1, "debe existir exactamente un único parcial en apply_queue"
    idx = parciales[0]
    assert idx.nulls_not_distinct, "sin NULLS NOT DISTINCT los pause (search_term NULL) no chocan"
    assert tuple(p.name for p in idx.indexParams) == (
        "platform",
        "ad_entity_id",
        "familia",
        "search_term",
    ), "la clave del único parcial no es la clave de efecto sellada"
    # Parcial sobre NO terminales: los terminales liberan la clave.
    where = repr(idx.whereClause)
    for terminal in ("applied", "failed", "vetoed", "discarded"):
        assert f"'{terminal}'" in where, f"el parcial no excluye el terminal {terminal}"


def test_0002_familia_de_efecto_derivada_y_clave_coherente():
    # Sellado 4: familia = entity_cut (pause) / term_cut (negative Y harvest).
    # Con kind en la clave, un veto de negative se eludía proponiendo harvest
    # del MISMO término: la familia de efecto es lo que choca. Es GENERATED
    # (regla 2: un número, una fuente) — deriva del kind, no se elige.
    cols = _cols2("apply_queue")
    gen = [
        c
        for c in (cols["familia"].constraints or ())
        if c.contype == enums.ConstrType.CONSTR_GENERATED
    ]
    assert gen, "familia debe ser columna GENERATED (deriva del kind, no se elige)"
    expr = repr(gen[0].raw_expr)
    for token in ("pause", "negative", "harvest", "entity_cut", "term_cut"):
        assert token in expr, f"la expresión de familia no menciona {token}"
    checks = _checks_de(TABLES2, "apply_queue")
    # Perímetro sellado 1: SOLO cortes en la cola; los bids aplican en su ciclo.
    solo_cortes = repr(checks["apply_queue_solo_cortes"].raw_expr)
    for kind in ("pause", "negative", "harvest"):
        assert f"'{kind}'" in solo_cortes
    assert "'bid'" not in solo_cortes, "un bid en la cola rompe el perímetro híbrido"
    # Coherencia clave↔familia: entity_cut lleva search_term NULL, term_cut
    # lo exige NOT NULL (brief §1.3).
    coherente = repr(checks["apply_queue_clave_coherente"].raw_expr)
    assert "search_term" in coherente
    assert "entity_cut" in coherente and "term_cut" in coherente


def test_0002_apply_attempt_excepcion_declarada_sello_una_vez():
    # Sellado 10 / brief §4.1: el "append-only" estricto de prohibir_mutacion
    # bloquearía el sello del ack. La excepción es DELIBERADA y con candado
    # propio ACOTADO por columnas (patrón sku_cost_solo_cierra_vigencia):
    # SOLO ack/resultado/finished_at pasan de NULL a valor UNA vez; el resto
    # de la fila y el DELETE revientan.
    fila = [
        t for t in TRIGGERS2 if t.relation.relname == "apply_attempt" and t.row and t.timing == 2
    ]
    assert fila, "apply_attempt sin candado de fila (BEFORE UPDATE OR DELETE)"
    eventos = 0
    for t in fila:
        eventos |= t.events
    assert eventos & 8 and eventos & 16, "el candado de apply_attempt cubre UPDATE y DELETE"
    nombres = {t.funcname[-1].sval for t in fila if t.funcname}
    assert nombres == {"apply_attempt_solo_sella_resultado"}, (
        "apply_attempt NO lleva prohibir_mutacion de fila: la excepción del "
        "sello es deliberada y su trigger es el acotado"
    )
    cuerpo = " ".join(_body_de(FUNCTIONS2, "apply_attempt_solo_sella_resultado").split())
    assert "TG_OP = 'DELETE'" in cuerpo
    for col in ("ack", "resultado", "finished_at"):
        assert f"NEW.{col} IS DISTINCT FROM OLD.{col}" in cuerpo
        assert f"OLD.{col} IS NOT NULL" in cuerpo, (
            f"falta el candado de re-sello: {col} ya sellada debe revienta"
        )
    assert "ROW(NEW.id" in cuerpo and "NEW.quota_cobrada" in cuerpo, (
        "falta la inmutabilidad del resto de la fila (patrón ROW IS DISTINCT FROM)"
    )
    # Los triggers de fila no se disparan con TRUNCATE: capa de sentencia.
    sentencia = [
        t
        for t in TRIGGERS2
        if t.relation.relname == "apply_attempt" and not t.row and t.events & 32
    ]
    assert sentencia, "apply_attempt sin candado de sentencia contra TRUNCATE"
    # decision_id NULL SOLO para probes (brief §4.1).
    checks = _checks_de(TABLES2, "apply_attempt")
    expr = repr(checks["attempt_probe_sin_decision"].raw_expr)
    assert "'probe'" in expr and "decision_id" in expr


def test_0002_reactivacion_manual_es_hecho_puro():
    # Sellado 17: la gracia de 7d corre DESDE detectada_en — pisar esa fecha
    # movería la gracia. Es un hecho puro: prohibir_mutacion completo (fila +
    # TRUNCATE), sin excepciones.
    cols = _cols2("reactivacion_manual")
    assert enums.ConstrType.CONSTR_PRIMARY in _contypes(cols["ad_entity_id"])
    assert enums.ConstrType.CONSTR_NOTNULL in _contypes(cols["detectada_en"])
    fila = {
        t.funcname[-1].sval
        for t in TRIGGERS2
        if t.relation.relname == "reactivacion_manual" and t.row and t.funcname
    }
    trunc = [
        t
        for t in TRIGGERS2
        if t.relation.relname == "reactivacion_manual" and not t.row and t.events & 32
    ]
    assert fila == {"prohibir_mutacion"} and trunc, "reactivacion_manual debe ser append-only"
    # Re-confirmación de la excepción de 0001 (trato par al de apply_attempt):
    # decision_application sigue siendo el RESUMEN mutable por columna —
    # jamás tabla append-only (su UPDATE sella el readback).
    assert "decision_application" not in APPEND_ONLY
    con_prohibir = {
        t.relation.relname
        for t in TRIGGERS
        if t.funcname and t.funcname[-1].sval == "prohibir_mutacion" and t.row
    }
    assert "decision_application" not in con_prohibir


def test_0002_mapeo_quota_contiene_las_ocho_claves():
    # Sellado 7 / brief §5.2: DOS vocabularios (config vs quota) sin mapeo era
    # el hueco (r2 grok 13). El mapeo es EXPLÍCITO y CERRADO: las 8 claves
    # ads_apply_cap_* mapeadas a los 8 motores ads_optimizer:* — un motor
    # fuera de mapa no resuelve clave y el INSERT revienta (fail-closed).
    cuerpo = " ".join(_body_de(FUNCTIONS2, "apply_cap_de_config").split())
    for plat in ("amazon_us", "amazon_mx"):
        for kind in ("bid", "pause", "negative", "harvest"):
            assert f"ads_apply_cap_{plat}_{kind}" in cuerpo, (
                f"falta la clave de config ads_apply_cap_{plat}_{kind} en el mapeo"
            )
            assert f"ads_optimizer:{plat}:{kind}" in cuerpo, (
                f"falta el motor ads_optimizer:{plat}:{kind} en el mapeo"
            )
    assert "config_version" in cuerpo, "el mapeo debe resolver la config vigente"


def test_0002_quota_fail_closed_dia_utc_y_used_creciente():
    # Sellado 8: fila del día SOLO desde config vigente (fail-closed: sin
    # clave no nace fila → cero applies), quota_date = día UTC de la BASE
    # (r2 codex: DATE sin zona + sesiones con TZ distinta duplicaban el cap)
    # y used jamás decrece (en 0001 esto no era enforceable).
    cuerpo_ins = " ".join(_body_de(FUNCTIONS2, "apply_quota_fila_desde_config").split())
    assert "(now() AT TIME ZONE 'UTC')::date" in cuerpo_ins, (
        "quota_date debe validarse contra el día UTC de la base, no CURRENT_DATE"
    )
    assert "apply_cap_de_config" in cuerpo_ins, "el INSERT de quota debe resolver el cap del mapeo"
    cuerpo_upd = " ".join(_body_de(FUNCTIONS2, "apply_quota_used_creciente").split())
    assert "NEW.used < OLD.used" in cuerpo_upd, "falta el candado used creciente"
    for col in ("cap", "quota_date", "motor"):
        assert f"NEW.{col} <> OLD.{col}" in cuerpo_upd, (
            f"falta la inmutabilidad de {col} por UPDATE (la PK no lo sella sola)"
        )


def test_0002_harvest_job_sella_progresion():
    # Sellado 13: las transiciones de harvest_job las sella 0002 por trigger
    # de UPDATE — SIN tocar el trigger de INSERT de 0001 (que sigue siendo el
    # único ahí: nace pending + coherencia con la decisión).
    sobre_0001 = {
        t.funcname[-1].sval for t in TRIGGERS if t.relation.relname == "harvest_job" and t.funcname
    }
    assert sobre_0001 == {"harvest_job_decision_coherente"}, (
        "0002 no debe tocar los triggers de INSERT de harvest_job"
    )
    candidatos = [
        t
        for t in TRIGGERS2
        if t.relation.relname == "harvest_job"
        and t.funcname
        and t.funcname[-1].sval == "harvest_job_sella_fases"
    ]
    assert candidatos, "falta el trigger de UPDATE que sella las fases"
    assert any(t.timing == 2 and t.events & 16 and t.row for t in candidatos)
    cuerpo = " ".join(_body_de(FUNCTIONS2, "harvest_job_sella_fases").split())
    assert _pares_de_transiciones(cuerpo) == PROGRESION_HARVEST, (
        "la progresión de harvest_job no es la cadena sellada (sin saltos ni retrocesos)"
    )


def test_0002_applied_cycle_id_en_decision_application():
    # Sellado 21: cooldown por ciclo EJECUTOR — decision_application gana
    # applied_cycle_id (NULL hasta confirmar; el sellado AL CONFIRMAR es
    # disciplina de la app, declarada en el COMMENT de la columna).
    alteradas = [
        st
        for st in (s.stmt for s in STMTS2)
        if isinstance(st, ast.AlterTableStmt) and st.relation.relname == "decision_application"
    ]
    assert alteradas, "falta el ALTER de decision_application"
    columnas = [
        cmd.def_.colname
        for st in alteradas
        for cmd in st.cmds
        if isinstance(cmd.def_, ast.ColumnDef)
    ]
    assert columnas == ["applied_cycle_id"], "el único ALTER debe agregar applied_cycle_id"
    assert any(
        i.relation.relname == "decision_application"
        and [p.name for p in i.indexParams] == ["applied_cycle_id"]
        for i in INDEXES2
    ), "applied_cycle_id es FK y necesita índice de apoyo"
    assert _grants_sobre_2("decision_application", "update").get("app_decide") == {
        "applied_cycle_id"
    }, "el GRANT de columnas existente debe ganar SOLO applied_cycle_id"


def test_0002_cache_ad_entity_state_update_acotado():
    # Sellado 16: el apply actualiza el cache CON el readback (lo LEÍDO, no lo
    # pedido) — sin este UPDATE acotado el ciclo siguiente calcularía +15%
    # sobre el bid viejo (regla 2: la fuente es Amazon y el readback ES de
    # Amazon).
    assert _grants_sobre_2("ad_entity_state", "update").get("app_decide") == {
        "current_bid",
        "status",
        "synced_at",
    }


def test_0002_update_de_apply_repartido_por_columnas():
    # La separación sellada de la cola entre el ROL MOTOR y el ADMIN, hecha
    # cumplir por GRANTs positivos POR COLUMNA (nadie tiene UPDATE genérico:
    # el candado no descansa en la ausencia de un GRANT, pero tampoco deja
    # uno abierto):
    #   - app_decide (motor): avanza la máquina y sella sus timestamps +
    #     descarta con motivo. JAMAS vence_el/vetoed_at/vetoed_by.
    #   - app_admin (veto + flip de cutover): transición a vetoed con su
    #     bloqueo editable y el discard masivo de shadow.
    por_rol = _grants_sobre_2("apply_queue", "update")
    assert por_rol.get("app_decide") == {
        "estado",
        "released_at",
        "applying_at",
        "applied_at",
        "failed_at",
        "discarded_at",
        "discard_motivo",
    }
    assert por_rol.get("app_admin") == {
        "estado",
        "vence_el",
        "vetoed_at",
        "vetoed_by",
        "discarded_at",
        "discard_motivo",
    }
    for rol, cols in por_rol.items():
        assert cols, f"UPDATE genérico (sin columnas) sobre apply_queue para {rol}"
    assert set(por_rol) == {"app_decide", "app_admin"}, "nadie más muta la cola"
    # El ledger: el motor SOLO sella el resultado (el intento nace pre-HTTP).
    assert _grants_sobre_2("apply_attempt", "update").get("app_decide") == {
        "ack",
        "resultado",
        "finished_at",
    }
    assert set(_grants_sobre_2("apply_attempt", "update")) == {"app_decide"}
    # La detección de reactivación la escribe SOLO el aplicador (sellado 17).
    assert not _grants_sobre_2("reactivacion_manual", "update")
    assert set(_grants_sobre_2("reactivacion_manual", "insert")) == {"app_decide"}
    assert set(_grants_sobre_2("apply_queue", "insert")) == {"app_decide"}
    assert set(_grants_sobre_2("apply_attempt", "insert")) == {"app_decide"}


def test_0002_secuencias_y_select_explicitos():
    # Sellado 24: GRANTs positivos completos — USAGE de las secuencias
    # IDENTITY nuevas para los tres escritores (patrón línea 1517 de 0001) y
    # SELECT explícito a app_read sobre las tablas nuevas (patrón 1459; el
    # DEFAULT PRIVILEGES de 0001 también aplicaría, pero el candado no
    # descansa en que el owner de la migración sea el mismo).
    usage = [
        st
        for st in (s.stmt for s in STMTS2)
        if isinstance(st, ast.GrantStmt) and any(p.priv_name == "usage" for p in st.privileges)
    ]
    assert usage, "falta el GRANT USAGE de secuencias en 0002"
    roles_usage = {g.rolename for st in usage for g in st.grantees}
    assert {"app_ingest", "app_decide", "app_admin"} <= roles_usage
    select = [
        st
        for st in (s.stmt for s in STMTS2)
        if isinstance(st, ast.GrantStmt)
        and any(p.priv_name == "select" for p in st.privileges)
        and any(g.rolename == "app_read" for g in st.grantees)
    ]
    tablas_read = {o.relname for st in select for o in st.objects if isinstance(o, ast.RangeVar)}
    assert {"apply_queue", "apply_attempt", "reactivacion_manual"} <= tablas_read


# ---------------------------------------------------------------------------
# (a3) ESTÁTICOS de 0003_goal_bounds_explicit — ORBIT 05 preflight 1.2
# ---------------------------------------------------------------------------


def test_0003_quita_los_defaults_de_piso_y_techo():
    # Sellado 2 del plan ORBIT 05 preflight (spot-check 4.4 + decision del
    # dueno 2026-08-28): la DB ya NO nace goals en 0.10/2.50 — numeros
    # pensados en USD que el goal MXN heredó y con los que el techo habria
    # aplastado bids vivos. El ALTER debe ser EXACTAMENTE DROP DEFAULT
    # (AT_ColumnDefault con def_ None: un SET DEFAULT aqui traria el numero
    # inventado de vuelta) sobre esas DOS columnas, y NADA mas.
    altera = [
        st for st in _stmts3(ast.AlterTableStmt) if st.relation.relname == "ads_optimizer_goal"
    ]
    assert len(altera) == 2, "0003 debe alterar ads_optimizer_goal exactamente dos veces"
    columnas = []
    for st in altera:
        assert len(st.cmds) == 1
        cmd = st.cmds[0]
        assert cmd.subtype == enums.AlterTableType.AT_ColumnDefault
        assert cmd.def_ is None, "debe ser DROP DEFAULT, no SET DEFAULT (el numero inventado)"
        columnas.append(cmd.name)
    assert sorted(columnas) == ["bid_ceiling", "bid_floor"]
    # La migracion NO toca nada mas: sin CREATE/GRANT/INSERT en 0003.
    assert not [
        s for s in STMTS3 if isinstance(s.stmt, (ast.CreateStmt, ast.GrantStmt, ast.InsertStmt))
    ], "0003 es SOLO los dos DROP DEFAULT (nada de datos ni grants)"


# ---------------------------------------------------------------------------
# (a4) ESTATICOS de 0004_ad_entity_kind_product_ad — ORBIT 06 0.4
# ---------------------------------------------------------------------------


def test_0004_agrega_solo_product_ad_al_enum():
    """0004 es SOLO ALTER TYPE ... ADD VALUE 'product_ad'. Sin CREATE, GRANT,
    INSERT ni DROP: la migracion no toca datos ni permisos."""
    altera = _stmts4(ast.AlterEnumStmt)
    assert len(altera) == 1, "0004 debe alterar exactamente un enum"
    assert altera[0].typeName[-1].sval == "ad_entity_kind"
    assert altera[0].newVal == "product_ad"
    assert not altera[0].skipIfNewValExists, "0004 no es re-runnable (sin IF NOT EXISTS)"
    assert not [
        s
        for s in STMTS4
        if isinstance(s.stmt, (ast.CreateStmt, ast.GrantStmt, ast.InsertStmt, ast.DropStmt))
    ], "0004 es SOLO el ADD VALUE (nada de datos, grants ni drops)"


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
    if not _psycopg_disponible():
        return False
    import psycopg

    try:
        with psycopg.connect(_test_dsn(), connect_timeout=2):
            return True
    except psycopg.Error:
        return False


def _psycopg_disponible() -> bool:
    """Driver psycopg importable (hallazgo CodeRabbit PR #12, ronda ready):
    sin driver, el importorskip de los tests se comeria la cobertura en
    silencio AUN con ORBIT_TEST_DSN definido."""
    import importlib

    try:
        importlib.import_module("psycopg")
    except ImportError:
        # find_spec puede devolver spec aunque el import truene
        # (CodeRabbit): el criterio es el import REAL
        return False
    return True


def _postgres_obligatorio_ausente() -> bool:
    """Condicion de skip FAIL-CLOSED para tests de integracion (hallazgo
    CodeRabbit PR #12): sin DSN explicito y sin Postgres local se skipea,
    PERO en CI (env CI definida; GitHub Actions siempre la pone) Postgres
    debe existir -- su ausencia ahi es RuntimeError ruidoso en la coleccion,
    jamas un skip silencioso que se lea como cobertura. El DRIVER psycopg
    cuenta como parte de "Postgres utilizable": sin el nada corre, y el
    importorskip de los tests NO es señal (skipea, no truena).
    """
    if not _psycopg_disponible():
        if "CI" in os.environ:
            raise RuntimeError(
                "CI definida pero sin driver psycopg: los tests de "
                "integracion desaparecerian en silencio (fail-closed)"
            )
        return True
    if os.environ.get("ORBIT_TEST_DSN"):
        return False
    if _hay_postgres_local():
        return False
    if "CI" in os.environ:  # presencia, no truthiness: CI="" tambien es CI
        raise RuntimeError(
            "CI definida pero sin Postgres utilizable: los tests de "
            "integracion desaparecerian en silencio (fail-closed)"
        )
    return True


def test_postgres_obligatorio_ausente_fail_closed(monkeypatch):
    """El guard NO puede fallar abierto en CI: sin DSN y sin Postgres, con
    CI definida revienta (jamas skip silencioso); sin CI, si skipea."""
    import test_schema as ts

    monkeypatch.delenv("ORBIT_TEST_DSN", raising=False)
    monkeypatch.setattr(ts, "_hay_postgres_local", lambda: False)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(RuntimeError, match="fail-closed"):
        ts._postgres_obligatorio_ausente()
    # CI="" TAMBIEN es CI (GitHub podria setearla vacia): presencia manda
    monkeypatch.setenv("CI", "")
    with pytest.raises(RuntimeError, match="fail-closed"):
        ts._postgres_obligatorio_ausente()
    monkeypatch.delenv("CI", raising=False)
    assert ts._postgres_obligatorio_ausente() is True


def test_postgres_obligatorio_driver_ausente_fail_closed(monkeypatch):
    """Hallazgo CodeRabbit (ronda ready): con el driver psycopg AUSENTE el
    guard debe tratar Postgres como no utilizable -- RuntimeError en CI
    (jamas skip silencioso) y skip local, TANTO con ORBIT_TEST_DSN definido
    como sin el (el importorskip de los tests skipea, no truena: no es
    señal)."""
    import test_schema as ts

    monkeypatch.setattr(ts, "_psycopg_disponible", lambda: False)
    monkeypatch.setattr(ts, "_hay_postgres_local", lambda: True)  # da igual: el driver manda
    for dsn in (None, "postgresql://x/y"):
        monkeypatch.delenv("ORBIT_TEST_DSN", raising=False)
        if dsn:
            monkeypatch.setenv("ORBIT_TEST_DSN", dsn)
        monkeypatch.setenv("CI", "true")
        with pytest.raises(RuntimeError, match="driver psycopg"):
            ts._postgres_obligatorio_ausente()
        monkeypatch.delenv("CI", raising=False)
        assert ts._postgres_obligatorio_ausente() is True


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
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_migracion_rechaza_en_vivo():
    """Aplica la migración en una base temporal y prueba 11 rechazos reales.

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

            # 7 y 8. sku_cost: cerrar la vigencia es el ÚNICO cambio legítimo.
            # Reescribir el importe de una fila publicada o borrarla reescribe
            # el histórico de márgenes hacia atrás — y hasta ahora eso sólo lo
            # frenaba la AUSENCIA de un GRANT, que es justo lo que la sección
            # 16 del SQL declara insuficiente. Aquí se prueba con superusuario
            # (todos los permisos): el candado tiene que aguantar igual.
            prod_id = conn.execute(
                "INSERT INTO product (odoo_sku, name) VALUES ('SKU-1', 'p') RETURNING id"
            ).fetchone()[0]
            costo_id = conn.execute(
                "INSERT INTO sku_cost (product_id, cost_amount, cost_currency,"
                " includes_tax, valid_from) VALUES"
                f" ({prod_id}, 10, 'MXN', true, '2026-01-01') RETURNING id"
            ).fetchone()[0]
            # Control positivo: cerrar la vigencia SÍ se permite, UNA vez.
            conn.execute(f"UPDATE sku_cost SET valid_to = '2026-06-01' WHERE id = {costo_id}")
            # Mover el corte de una vigencia ya cerrada reescribe el período
            # histórico en que ese costo aplicó — y el EXCLUDE no lo ve
            # (encoger no solapa; extender tampoco, sin fila sucesora).
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute(f"UPDATE sku_cost SET valid_to = '2026-07-01' WHERE id = {costo_id}")
            # Reabrirla es lo mismo por el otro lado: el costo vuelve a
            # aplicar hasta el infinito.
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute(f"UPDATE sku_cost SET valid_to = NULL WHERE id = {costo_id}")
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute(f"UPDATE sku_cost SET cost_amount = 99 WHERE id = {costo_id}")
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute(f"DELETE FROM sku_cost WHERE id = {costo_id}")

            # 9. Una decisión harvest lleva dinero (new_value = bid inicial de
            # la keyword harvesteada) y por lo tanto exige moneda: como
            # old_value/new_value son NUMERIC crudo, sin este CHECK el importe
            # entraba sin moneda y un SUM() sobre decision volvía a mezclar
            # MXN con USD (regla 4).
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO decision (cycle_id, ad_entity_id, kind,"
                    " config_version_id, data_observed_at, window_start,"
                    " window_end, search_term, new_value, inputs) VALUES"
                    f" ({ciclo_id}, {entidad_us}, 'harvest', {config_id}, now(),"
                    " CURRENT_DATE - 45, CURRENT_DATE - 15, 'zapato', 1.25, '{}')"
                )
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


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_0003_goal_sin_piso_techo_revierte_y_con_bounds_pasa():
    """(e) Sello 0003 en vivo (ORBIT 05 preflight 1.2, sellado 2 del plan):
    tras 0001+0002+0003, un INSERT de goal que omita bid_floor/bid_ceiling
    REVIENTA con NotNullViolation — la DB ya no trae el DEFAULT 0.10/2.50
    (pensado en USD: el goal 4 MXN nacio con el techo que aplastaba 144/233
    keywords y 44/51 targets MX con bid > 2.50 MXN, spot-check 4.4) — y el
    INSERT con bounds explicitos PASA (control positivo, MXN 1.00/45.00).
    ROJO contra 0001+0002: el DEFAULT llenaba los omitidos y el insert
    prosperaba naciendo en USD."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_0003_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # 0001: esquema sellado
        conn.execute(SQL2)  # 0002: apply
        conn.execute(SQL3)  # 0003: sin DEFAULT en piso/techo
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "INSERT INTO ads_optimizer_goal (scope, platform, bid_currency, enabled)"
                " VALUES ('platform', 'amazon_mx', 'MXN', true)"
            )
        # Control positivo: bounds explicitos PASAN (los del sello MXN).
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, platform, bid_currency, bid_floor,"
            " bid_ceiling, enabled, mode)"
            " VALUES ('platform', 'amazon_mx', 'MXN', 1.00, 45.00, true, 'shadow')"
        )
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_0004_permite_insert_kind_product_ad():
    """Tras 0001 el enum rechaza product_ad; tras 0004 (otra transaccion) el
    INSERT pasa. El ADD VALUE y el primer uso no pueden compartir TX."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_0004_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id)"
                " VALUES ('amazon_us', 'product_ad', 'ad-1')"
            )
        conn.execute(SQL4)
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_us', 'product_ad', 'ad-1')"
        )
        kind = conn.execute(
            "SELECT kind::text FROM ad_entity WHERE external_id = 'ad-1'"
        ).fetchone()[0]
        assert kind == "product_ad"
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
