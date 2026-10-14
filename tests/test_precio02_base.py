"""Migraciones 0053 y 0054 de REPRICING 02, corte 0.a.

La 0054 convive con el codigo que ya corre: el INSERT de decision que hace
hoy `corrida.persistir_decision` y el INSERT de escenario `disponible` que
hace hoy `estimacion_repository.persistir_escenario` entran sin ninguna
columna nueva. Ademas ejercita los rechazos nuevos del esquema y la siembra
de config.
"""

from __future__ import annotations

import itertools
import os
import socket
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente

RAIZ = Path(__file__).resolve().parents[1]

# La 0054 necesita la 0047 por la FK a `familia`; la 0053 va sola en su
# propia corrida de psql (ADD VALUE no se puede usar en la misma transaccion).
ORDEN02 = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db(*, sin_0054: bool = False):
    from psycopg import sql as pgsql

    orden = list(ORDEN02[:-1]) if sin_0054 else list(ORDEN02)
    dsn = _dsn_base()
    db = f"precio02_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in orden:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _producto_listing(conn, *, sku="SKU-R02", ext="B0R02000001") -> tuple[int, int]:
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto r02') RETURNING id", (sku,)
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_mx', %s, %s) RETURNING id",
        (pid, ext, sku),
    ).fetchone()[0]
    return pid, lid


def _config_vieja(conn) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        (
            "previa",
            Json(
                {
                    "precio_caida_ventas_pct": "0.40",
                    "precio_cap_amazon_mx": 5,
                    "ads_apply_cap_amazon_mx": 20,
                }
            ),
        ),
    ).fetchone()[0]


def _hoy(conn) -> object:
    return conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]


def _cadena_escenario(conn, lid: int, pid: int, *, evento="esc-r02") -> int:
    """Oferta + fee + costo + politica + run + escenario disponible."""
    run, cerrado_en = conn.execute(
        "INSERT INTO ingest_run (source, finished_at, rows_written, ok)"
        " VALUES ('accounting_sku_costs', now(), 1, true) RETURNING id, finished_at"
    ).fetchone()
    oferta = conn.execute(
        "INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku, asin,"
        " canal, price_amount, price_currency, fetched_at, observed_at, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, 'amazon_mx', 'SKU-R02', 'B0R02000001', 'fba', 100, 'MXN', now(), now(),"
        " %s, %s, 'ctx-r02') RETURNING id",
        (lid, evento + "-oferta", Json({})),
    ).fetchone()[0]
    fee = conn.execute(
        "INSERT INTO estimacion_fee_observation (oferta_observation_id, listing_id, platform,"
        " seller_sku, asin, canal, quoted_price_amount, quoted_price_currency, total_fees,"
        " fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, %s, 'amazon_mx', 'SKU-R02', 'B0R02000001', 'fba', 100, 'MXN', 12,"
        " now(), now(), now(), 'success', %s, %s, 'ctx-r02') RETURNING id",
        (oferta, lid, evento + "-fee", Json({})),
    ).fetchone()[0]
    costo = conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id) VALUES (%s, 20, 'MXN', false, '2026-01-01', %s)"
        " RETURNING id",
        (pid, run),
    ).fetchone()[0]
    politica = conn.execute(
        "INSERT INTO estimacion_politica_version (label, universo, formula_version, settings,"
        " valid_from) VALUES ('r02', 'amazon_mx/fba', 'v1', %s, '2026-01-01') RETURNING id",
        (Json({"iva_divisor": 1.16, "isr_tasa": 0.025, "logistica": 0}),),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO estimacion_escenario"
        " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
        " politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
        " sku_cost_id, costo_validation_run_id, costo_validated_at,"
        " fx_rate_date, fx_base, fx_quote, fx_rate, fx_source,"
        " moneda, contribucion, contribucion_pct, estado, motivos, componentes,"
        " exclusiones, canonical_input, context_fingerprint, source_event_id)"
        " VALUES (%s, 'amazon_mx', 'SKU-R02', 'B0R02000001', 'fba',"
        " (now() AT TIME ZONE 'UTC')::date, now(), %s, 'v1', %s, %s, %s, %s, %s,"
        " NULL, NULL, NULL, NULL, NULL, 'MXN', 55, 0.55, 'disponible', %s, %s, %s, %s,"
        " 'ctx-r02', %s)"
        " RETURNING id",
        (
            lid,
            politica,
            oferta,
            fee,
            costo,
            run,
            cerrado_en,
            Json([]),
            Json([]),
            Json([]),
            Json({}),
            evento,
        ),
    ).fetchone()[0]


def _goal(conn, lid: int, *, mode="live", desde=None, hasta=None, pct="0.30") -> int:
    base = desde if desde is not None else _hoy(conn) - timedelta(days=30)
    return conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode, valid_from,"
        " valid_to, creado_por, go_literal)"
        " VALUES (%s, 'amazon_mx', %s, %s, %s, %s, 't',"
        " CASE WHEN %s = 'live' THEN 'go-tdd' END) RETURNING id",
        (lid, Decimal(pct), mode, base, hasta, mode),
    ).fetchone()[0]


def _insert_decision_de_hoy(conn, lid: int, *, mode="live", escenario=None, goal_id=None) -> int:
    """El INSERT con las columnas EXACTAS de `corrida.persistir_decision`."""
    columnas_nuevas = ", goal_id" if goal_id is not None else ""
    valores_nuevos = ", %s" if goal_id is not None else ""
    params: tuple = (lid, escenario, mode)
    if goal_id is not None:
        params = (lid, escenario, mode, goal_id)
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, product_id,"
        " canal, m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, escenario_id,"
        " fee_observation_id, cotizacion_id, u15, u60, n15, n60, racha_senal, perdiendo,"
        f" buy_box_is_own, mode, prioridad, config_version_id{columnas_nuevas})"
        f" VALUES (%s, 'amazon_mx', 'subir', NULL, NULL, 'fba', 0.25, 0.30, 100, 'MXN',"
        " 110, 'MXN', 110, 'MXN', 10, 'MXN', 20, 'MXN', 12, 'MXN', 5, 'MXN', 8, 'MXN',"
        f" %s, NULL, NULL, 1, 2, 3, 4, 0, false, NULL, %s, NULL, NULL{valores_nuevos})"
        " RETURNING id",
        params,
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# convivencia con el codigo de hoy
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_la_0054_deja_entrar_el_insert_de_decision_que_hace_hoy_el_codigo():
    with _db() as conn:
        _config_vieja(conn)
        _pid, lid = _producto_listing(conn)
        _goal(conn, lid, mode="live")
        did = _insert_decision_de_hoy(conn, lid, mode="live")
        fila = conn.execute(
            "SELECT corrida_id, goal_id, l_origen, senal_evidencia, verificacion"
            " FROM precio_decision WHERE id = %s",
            (did,),
        ).fetchone()
        assert fila == (None, None, None, None, None)


@_skip_sin_pg
def test_la_0054_deja_entrar_el_insert_de_escenario_disponible_que_hace_hoy_el_codigo():
    with _db() as conn:
        pid, lid = _producto_listing(conn)
        eid = _cadena_escenario(conn, lid, pid, evento="esc-convive")
        fila = conn.execute(
            "SELECT envio_muestra_id, logistica_origen, fee_variable, fee_fijo, fee_cotizable,"
            " miembro_product_id FROM estimacion_escenario WHERE id = %s",
            (eid,),
        ).fetchone()
        assert fila == (None, None, None, None, None, None)


# ---------------------------------------------------------------------------
# rechazos nuevos
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_rechaza_muestra_familia_sin_donantes():
    with _db() as conn:
        pid, _lid = _producto_listing(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO precio_envio_muestra (product_id, platform, canal,"
                " ventana_desde, ventana_hasta, envios, valor, valor_currency, origen,"
                " familia_id, donantes, regla_version)"
                " VALUES (%s, 'amazon_mx', 'fbm', '2026-04-01', '2026-09-30', 3, 90, 'MXN',"
                " 'familia', NULL, NULL, 'v1')",
                (pid,),
            )


@_skip_sin_pg
def test_rechaza_decision_con_goal_id_ya_cerrado():
    with _db() as conn:
        _config_vieja(conn)
        _pid, lid = _producto_listing(conn)
        cerrado = _goal(conn, lid, mode="live", hasta=_hoy(conn))
        with pytest.raises(psycopg.errors.CheckViolation, match="goal_reemplazado"):
            _insert_decision_de_hoy(conn, lid, mode="live", goal_id=cerrado)


@_skip_sin_pg
def test_rechaza_liberacion_para_corrida_que_no_retuvo():
    with _db() as conn:
        _config_vieja(conn)
        _pid, lid = _producto_listing(conn)
        _goal(conn, lid, mode="live")
        cfg = conn.execute("SELECT max(id) FROM config_version").fetchone()[0]
        corrida = conn.execute(
            "INSERT INTO precio_corrida (platform, owner, config_version_id, modo_global,"
            " estado, decisiones, movidas, virtuales, retenidas, errores,"
            " saltadas_por_edicion, cerrada_at)"
            " VALUES ('amazon_mx', 't', %s, 'shadow', 'cerrada', 0, 0, 0, 0, 0, 0, now())"
            " RETURNING id",
            (cfg,),
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO precio_compuerta_liberacion (corrida_id, liberado_por, nota)"
                " VALUES (%s, 'dueno', 'prueba')",
                (corrida,),
            )


@_skip_sin_pg
def test_rechaza_update_de_origen_sobre_goal_ya_sembrado():
    with _db() as conn:
        _pid, lid = _producto_listing(conn)
        gid = _goal(conn, lid, mode="shadow")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE precio_goal SET origen = 'manual' WHERE id = %s", (gid,))


@_skip_sin_pg
def test_sigue_rechazando_live_bajo_goal_shadow_y_acepta_shadow_bajo_goal_live():
    with _db() as conn:
        _config_vieja(conn)
        _pid, lid = _producto_listing(conn)
        _goal(conn, lid, mode="shadow")
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_decision_de_hoy(conn, lid, mode="live")
    with _db() as conn:
        _config_vieja(conn)
        _pid, lid = _producto_listing(conn)
        _goal(conn, lid, mode="live")
        assert _insert_decision_de_hoy(conn, lid, mode="shadow") > 0
    # R02-r2 B1: desde 0.b toda decision guarda goal_id, asi que el codigo
    # nuevo pasa por la rama `NEW.goal_id IS NOT NULL` del trigger; los
    # mismos dos casos CON goal_id matan el mutante de la linea 627.
    with _db() as conn:
        _config_vieja(conn)
        _pid, lid = _producto_listing(conn)
        gid = _goal(conn, lid, mode="shadow")
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_decision_de_hoy(conn, lid, mode="live", goal_id=gid)
    with _db() as conn:
        _config_vieja(conn)
        _pid, lid = _producto_listing(conn)
        gid = _goal(conn, lid, mode="live")
        assert _insert_decision_de_hoy(conn, lid, mode="shadow", goal_id=gid) > 0


@_skip_sin_pg
def test_acepta_goal_reeditado_el_mismo_dia():
    with _db() as conn:
        _pid, lid = _producto_listing(conn)
        hoy = _hoy(conn)
        primero = _goal(conn, lid, mode="shadow", desde=hoy)
        conn.execute("UPDATE precio_goal SET valid_to = %s WHERE id = %s", (hoy, primero))
        segundo = conn.execute(
            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode, valid_from,"
            " creado_por) VALUES (%s, 'amazon_mx', 0.40, 'shadow', %s, 't') RETURNING id",
            (lid, hoy),
        ).fetchone()[0]
        assert segundo > primero
        vigentes = conn.execute(
            "SELECT count(*) FROM precio_goal WHERE listing_id = %s AND valid_to IS NULL",
            (lid,),
        ).fetchone()[0]
        assert vigentes == 1


# ---------------------------------------------------------------------------
# config sembrada por la migracion
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_la_0054_siembra_config_con_modo_global_shadow_y_conserva_lo_previo():
    with _db(sin_0054=True) as conn:
        previa = _config_vieja(conn)
        conn.execute((RAIZ / "migrations" / ORDEN02[-1]).read_text(encoding="utf-8"))
        id_final, modo, cap_precio, cap_ads, fusible = conn.execute(
            "SELECT id, settings ->> 'precio_modo_global', settings ->> 'precio_cap_amazon_mx',"
            " settings ->> 'ads_apply_cap_amazon_mx', settings ->> 'precio_fusible_frac'"
            " FROM config_version ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert id_final > previa
        assert modo == "shadow"
        assert cap_precio == "5"
        assert cap_ads == "20"
        assert Decimal(fusible) == Decimal("0.5")
