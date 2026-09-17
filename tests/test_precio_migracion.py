"""Tests de la migración `migrations/0039_precio.sql` (REPRICING 01, A.0):
motor de precios por goal de margen — enum `precio_mode`; `precio_goal`
(vigencia patrón `sku_cost`, banda de goal, `live ⇔ go_literal`, un vigente);
`precio_decision` (una por día, `decision_date` por trigger UTC, moneda
obligatoria); `precio_cotizacion`; `precio_envio_muestra`; `precio_cambio`
(transiciones por trigger, índice único parcial de cambio abierto, nacimiento
virtual cerrado, reversa sin decisión); GRANTs por columna;
`apply_cap_de_config` ampliado con los tres motores de precio; bloque DO bajo
`SET ROLE` que revierte lo que inserta.

`ORDEN39` es el subconjunto mínimo que toca 0039 (0001 + 0002 + 0028):
ninguna migración entre 0003 y 0038 menciona `listing`, `apply_quota_state`,
`apply_cap_de_config` ni las tablas de estimación (verificado por grep al
escribir el módulo), así que el subconjunto equivale al esquema real para
estos objetos. Todos los tests corren con `ORBIT_TEST_DSN` apuntado
(0 skipped); sin Postgres skipean en verde fuera de CI (patrón
`test_apply_schema`).
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
ORDEN39 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def db_39(prefijo: str):
    """DB temporal con ORDEN39; yields conn autocommit (patrón `db_38`)."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN39:
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


def _producto(conn, sku="SKU-PRECIO-1") -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto precio') RETURNING id",
        (sku,),
    ).fetchone()[0]


def _listing(conn, producto: int, *, platform="amazon_mx", ext="ASIN1", sku="SKU-P1") -> int:
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (producto, platform, ext, sku),
    ).fetchone()[0]


def _goal(
    conn,
    listing: int,
    *,
    platform="amazon_mx",
    pct=Decimal("0.30"),
    mode="shadow",
    go=None,
    valid_from="2026-09-01",
) -> int:
    return conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
        " valid_from, creado_por, go_literal)"
        " VALUES (%s, %s, %s, %s, %s, 'tdd', %s) RETURNING id",
        (listing, platform, pct, mode, valid_from, go),
    ).fetchone()[0]


_MONEDAS_DECISION = (
    "p_actual_currency, p_objetivo_currency, p_aplicado_currency,"
    " i_currency, c_currency, f_currency, l_currency, r_currency"
)


def _ensure_goal(conn, listing: int, *, platform="amazon_mx", mode="live") -> None:
    """Siembra el goal vigente que la decisión necesita (r4 punto 3): solo
    cuando no hay NINGÚN vigente hoy —hay un solo vigente por
    (listing, platform), sin importar el modo, así que si existe de otro modo
    no se toca nada y la decisión falla en voz alta (dato mal puesto)."""
    hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    existe = conn.execute(
        "SELECT 1 FROM precio_goal WHERE listing_id = %s AND platform = %s"
        " AND valid_from <= %s AND (valid_to IS NULL OR %s < valid_to)",
        (listing, platform, hoy, hoy),
    ).fetchone()
    if not existe:
        # `live` exige go literal (CHECK): se siembra con go; `shadow`, sin.
        go = "go tdd" if mode == "live" else None
        conn.execute(
            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
            " valid_from, creado_por, go_literal)"
            " VALUES (%s, %s, 0.30, %s, '2026-01-01', 'tdd', %s)",
            (listing, platform, mode, go),
        )


def _decision_sin_siembra(
    conn,
    listing: int,
    *,
    platform="amazon_mx",
    resultado="subir",
    mode="live",
    motivo="tdd",
) -> int:
    return conn.execute(
        f"INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
        f" {_MONEDAS_DECISION})"
        " VALUES (%s, %s, %s, %s, %s,"
        " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN') RETURNING id",
        (listing, platform, resultado, motivo, mode),
    ).fetchone()[0]


def _decision(
    conn,
    listing: int,
    *,
    platform="amazon_mx",
    resultado="subir",
    mode="live",
    motivo="tdd",
) -> int:
    _ensure_goal(conn, listing, platform=platform, mode=mode)
    return _decision_sin_siembra(
        conn, listing, platform=platform, resultado=resultado, mode=mode, motivo=motivo
    )


def _oferta(
    conn, listing: int, *, platform="amazon_mx", sku="SKU-P1", asin="ASIN1", tag="tdd"
) -> int:
    return conn.execute(
        "INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku, asin,"
        " canal, price_amount, price_currency, fetched_at, observed_at,"
        " source_event_id, canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, 'fba', 100.00, 'MXN',"
        " now() - interval '2 hours', now(), %s, '{}'::jsonb, 'huella') RETURNING id",
        (listing, platform, sku, asin, f"oferta-{tag}"),
    ).fetchone()[0]


def _cotizacion(
    conn,
    listing: int,
    oferta: int,
    *,
    platform="amazon_mx",
    intento=1,
    estado="success",
    codigo=None,
    tag="tdd",
) -> int:
    """Cotización con identidad propia (r3 punto 1): listing + intento + fecha
    del servidor. En `error`, sin fees ni fecha estimada (punto 2)."""
    if estado == "success":
        total, moneda_total, estimada = "12.00", "'MXN'", "now()"
    else:
        total, moneda_total, estimada = "NULL", "NULL", "NULL"
    return conn.execute(
        "INSERT INTO precio_cotizacion (listing_id, platform, intento, oferta_observation_id,"
        " quoted_price, quoted_price_currency, total_fees, total_fees_currency, fee_details,"
        f" fees_estimated_at, estado, error_code, source_event_id)"
        " VALUES (%s, %s, %s, %s, 110.00, 'MXN', "
        f"{total}, {moneda_total}, '[]'::jsonb, {estimada}, %s, %s, %s)"
        " RETURNING id",
        (listing, platform, intento, oferta, estado, codigo, f"cotiz-{tag}-{intento}"),
    ).fetchone()[0]


def _escenario(
    conn, listing: int, *, platform="amazon_mx", sku="SKU-P1", asin="ASIN1", tag="tdd"
) -> int:
    return conn.execute(
        "INSERT INTO estimacion_escenario (listing_id, platform, seller_sku, asin, canal,"
        " valoracion_date, observed_at, formula_version, estado, canonical_input,"
        " context_fingerprint, source_event_id)"
        " VALUES (%s, %s, %s, %s, 'fba', '2026-09-01', now(), 'v1', 'incompleta',"
        " '{}'::jsonb, 'huella', %s) RETURNING id",
        (listing, platform, sku, asin, f"esc-{tag}"),
    ).fetchone()[0]


def _fee(
    conn, listing: int, oferta: int, *, platform="amazon_mx", sku="SKU-P1", asin="ASIN1", tag="tdd"
) -> int:
    return conn.execute(
        "INSERT INTO estimacion_fee_observation (oferta_observation_id, listing_id, platform,"
        " seller_sku, asin, canal, quoted_price_amount, quoted_price_currency, total_fees,"
        " fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, %s, 'fba', 100.00, 'MXN', 12.00,"
        " now(), now() - interval '2 hours', now(), 'success', %s,"
        " '{}'::jsonb, 'huella') RETURNING id",
        (oferta, listing, platform, sku, asin, f"fee-{tag}"),
    ).fetchone()[0]


def _muestra(conn, producto: int, *, platform="amazon_mx") -> int:
    return conn.execute(
        "INSERT INTO precio_envio_muestra (product_id, platform, ventana_desde, ventana_hasta,"
        " envios, percentil, valor, valor_currency, mediana, mediana_currency)"
        " VALUES (%s, %s, '2026-06-01', '2026-08-30', 8, 'mediana', 95.00, 'MXN', 95.00, 'MXN')"
        " RETURNING id",
        (producto, platform),
    ).fetchone()[0]


def _cambio(
    conn,
    decision,
    listing: int,
    *,
    platform="amazon_mx",
    aplicado=True,
    estado="pendiente",
    **extra,
) -> int:
    cols = (
        "decision_id, listing_id, platform, precio_antes, precio_antes_currency,"
        " precio_despues, precio_despues_currency, aplicado, estado"
    )
    vals = [
        decision,
        listing,
        platform,
        Decimal("100.00"),
        "MXN",
        Decimal("110.00"),
        "MXN",
        aplicado,
        estado,
    ]
    for columna, valor in extra.items():
        cols += f", {columna}"
        vals.append(valor)
    marca = ", ".join(["%s"] * len(vals))
    return conn.execute(
        f"INSERT INTO precio_cambio ({cols}) VALUES ({marca}) RETURNING id",
        vals,
    ).fetchone()[0]


def _cambio_virtual(conn, decision: int, listing: int, *, platform="amazon_mx") -> int:
    """Virtual con su decisión shadow (S4 #13 la registra): solo la reversa
    está exenta de `decision_id`."""
    return conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado, estado,"
        " enviado_at, confirmado_por)"
        " VALUES (%s, %s, %s, 100.00, 'MXN', 110.00, 'MXN', false,"
        " 'confirmado', now(), 'virtual') RETURNING id",
        (decision, listing, platform),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# precio_goal — vigencia, banda, ceremonia live
# ---------------------------------------------------------------------------


@_skip_db
def test_goal_rechaza_update_de_margen():
    """De una fila publicada solo se cierra la vigencia: tocar
    `margen_goal_pct` revienta. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pg_goal_upd") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        gid = _goal(conn, lid)
        with pytest.raises(psycopg.errors.RestrictViolation, match="cerrar la vigencia"):
            conn.execute("UPDATE precio_goal SET margen_goal_pct = 0.40 WHERE id = %s", (gid,))


@_skip_db
def test_goal_rechaza_segundo_vigente():
    """Un solo vigente por (listing, platform): el segundo con `valid_to`
    NULL choca en el índice parcial. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pg_goal_vig") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal(conn, lid)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _goal(conn, lid, valid_from="2026-09-02")
        # Cerrado el primero, el segundo sí entra.
        conn.execute("UPDATE precio_goal SET valid_to = '2026-09-02' WHERE valid_to IS NULL")
        gid2 = _goal(conn, lid, valid_from="2026-09-02")
        assert gid2


@_skip_db
def test_goal_live_sin_go_rechazado():
    """`live` sin `go_literal` revienta; `shadow` sin go pasa y `live` con
    go pasa. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pg_goal_go") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        with pytest.raises(psycopg.errors.CheckViolation):
            _goal(conn, lid, mode="live")
        with pytest.raises(psycopg.errors.CheckViolation):
            _goal(conn, lid, mode="live", go="   ")
        _goal(conn, lid, mode="live", go="go dueno 2026-09-17")
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        _goal(conn, lid2)


@_skip_db
def test_goal_banda():
    """Goal fuera de 0.10–0.60 revienta en la base; los bordes pasan.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pg_goal_banda") as conn:
        prod = _producto(conn)
        for n, pct in (("a", "0.09"), ("b", "0.61")):
            lid = _listing(conn, prod, ext=f"ASIN-{n}", sku=f"SKU-{n}")
            with pytest.raises(psycopg.errors.CheckViolation):
                _goal(conn, lid, pct=Decimal(pct))
        for n, pct in (("c", "0.10"), ("d", "0.60")):
            lid = _listing(conn, prod, ext=f"ASIN-{n}", sku=f"SKU-{n}")
            assert _goal(conn, lid, pct=Decimal(pct))


@_skip_db
def test_goal_valid_to_una_sola_vez():
    """Cerrar pasa una vez; mover el corte o reabrir revienta.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pg_goal_cierre") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        gid = _goal(conn, lid)
        conn.execute("UPDATE precio_goal SET valid_to = '2026-09-10' WHERE id = %s", (gid,))
        with pytest.raises(psycopg.errors.RestrictViolation, match="una vez"):
            conn.execute("UPDATE precio_goal SET valid_to = '2026-09-11' WHERE id = %s", (gid,))
        with pytest.raises(psycopg.errors.RestrictViolation, match="una vez"):
            conn.execute("UPDATE precio_goal SET valid_to = NULL WHERE id = %s", (gid,))


@_skip_db
def test_goal_solo_cierra_vigencia_y_no_borra():
    """UPDATE de otra columna y DELETE revientan aunque el rol tenga
    permiso. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pg_goal_solo") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        gid = _goal(conn, lid)
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE precio_goal SET creado_por = 'otro' WHERE id = %s", (gid,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE precio_goal SET valid_from = '2026-09-05' WHERE id = %s", (gid,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM precio_goal WHERE id = %s", (gid,))


@_skip_db
def test_goal_solape_rechazado():
    """Dos goals vigentes el mismo día revientan: A `[09-01, 09-20)` cerrado
    + B desde `09-10` choca en el EXCLUDE (el parcial solo cubre abiertas y el
    UNIQUE solo el `valid_from`). Rojo r2: sin el EXCLUDE el solape pasa."""
    with db_39("orbit_r2_solape") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal(conn, lid, valid_from="2026-09-01")
        conn.execute("UPDATE precio_goal SET valid_to = '2026-09-20' WHERE valid_to IS NULL")
        with pytest.raises(psycopg.errors.ExclusionViolation, match="precio_goal_sin_solape"):
            _goal(conn, lid, valid_from="2026-09-10")


@_skip_db
def test_goal_contiguo_y_anulado_no_solapan():
    """Semiabierto `[from, to)`: A cerrado en `09-10` + B desde `09-10` pasa;
    A anulado (`valid_to = valid_from`, rango vacío) + B «dentro» pasa.
    Verde antes y después: el EXCLUDE no puede romperlos."""
    with db_39("orbit_r2_contiguo") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal(conn, lid, valid_from="2026-09-01")
        conn.execute("UPDATE precio_goal SET valid_to = '2026-09-10' WHERE valid_to IS NULL")
        assert _goal(conn, lid, valid_from="2026-09-10")
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        _goal(conn, lid2, valid_from="2026-09-01")
        conn.execute("UPDATE precio_goal SET valid_to = valid_from WHERE listing_id = %s", (lid2,))
        assert _goal(conn, lid2, valid_from="2026-09-05")


@_skip_db
def test_goal_solape_otro_listing_pasa():
    """El mismo solapamiento en otro listing u otra plataforma pasa: el
    EXCLUDE es por `(listing_id, platform)`. Verde antes y después."""
    with db_39("orbit_r2_otro") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal(conn, lid, valid_from="2026-09-01")
        conn.execute("UPDATE precio_goal SET valid_to = '2026-09-20' WHERE valid_to IS NULL")
        lid_us = _listing(conn, prod, platform="amazon_us", ext="ASIN-US", sku="SKU-US")
        assert _goal(conn, lid_us, platform="amazon_us", valid_from="2026-09-10")
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        assert _goal(conn, lid2, valid_from="2026-09-10")


@_skip_db
def test_cambio_precios_positivos():
    """El ledger no admite precio 0 o negativo; observado/readback solo
    cuando no son NULL. Rojo r2: sin el CHECK el 0 pasa."""
    with db_39("orbit_r2_posit") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_cambio_precios_positivos"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado)"
                " VALUES (%s, %s, 'amazon_mx', 0, 'MXN', 110.00, 'MXN', true, 'pendiente')",
                (did, lid),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_cambio_precios_positivos"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado)"
                " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', -1, 'MXN', true, 'pendiente')",
                (did, lid),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_cambio_precios_positivos"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_observado_antes,"
                " precio_observado_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado)"
                " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', -5, 'MXN',"
                " 110.00, 'MXN', true, 'pendiente')",
                (did, lid),
            )
        # El readback al nacer lo prohíbe el punto 7; aquí va por UPDATE.
        lid_rb = _listing(conn, prod, ext="ASIN-RB", sku="SKU-RB")
        cid_rb = _cambio(conn, _decision(conn, lid_rb), lid_rb)
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_cambio_precios_positivos"):
            conn.execute(
                "UPDATE precio_cambio SET readback_precio = 0,"
                " readback_precio_currency = 'MXN' WHERE id = %s",
                (cid_rb,),
            )


@_skip_db
def test_goal_truncate_rechazado():
    """TRUNCATE no pasa por el trigger de fila: lo cubre la capa de
    sentencia. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pg_goal_trunc") as conn:
        prod = _producto(conn)
        _goal(conn, _listing(conn, prod))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE precio_goal")


# ---------------------------------------------------------------------------
# precio_decision — una por día, fecha del servidor, moneda obligatoria
# ---------------------------------------------------------------------------


@_skip_db
def test_decision_fecha_del_cliente_ignorada():
    """`decision_date` lo fija el trigger al día UTC de la base, no al
    valor que mande el cliente. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pd_fecha") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _ensure_goal(conn, lid, mode="shadow")
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        fila = conn.execute(
            f"INSERT INTO precio_decision (listing_id, platform, decision_date, resultado,"
            f" motivo, mode, {_MONEDAS_DECISION})"
            " VALUES (%s, 'amazon_mx', '2000-01-01', 'mantener', 'tdd', 'shadow',"
            " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN') RETURNING decision_date",
            (lid,),
        ).fetchone()[0]
        assert fila == hoy


@_skip_db
def test_decision_unica_por_dia():
    """Segunda decisión del mismo listing el mismo día choca.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pd_unica") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _decision(conn, lid)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _decision(conn, lid, resultado="subir")


@_skip_db
def test_decision_append_only():
    """UPDATE/DELETE/TRUNCATE en `precio_decision` revientan.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pd_append") as conn:
        prod = _producto(conn)
        did = _decision(conn, _listing(conn, prod))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE precio_decision SET motivo = 'otro' WHERE id = %s", (did,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM precio_decision WHERE id = %s", (did,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE precio_decision CASCADE")


@_skip_db
def test_decision_moneda_not_null():
    """Dinero sin moneda no entra: las columnas de moneda son NOT NULL.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pd_moneda") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _ensure_goal(conn, lid, mode="shadow")
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "INSERT INTO precio_decision (listing_id, platform, resultado, mode,"
                " p_actual_currency, p_objetivo_currency, p_aplicado_currency,"
                " i_currency, c_currency, f_currency, l_currency)"
                " VALUES (%s, 'amazon_mx', 'mantener', 'shadow',"
                " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN')",
                (lid,),
            )


# ---------------------------------------------------------------------------
# precio_cotizacion y precio_envio_muestra — append-only
# ---------------------------------------------------------------------------


@_skip_db
def test_cotizacion_append_only():
    """UPDATE/DELETE/TRUNCATE en `precio_cotizacion` revientan.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pc_append") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        ofid = _oferta(conn, lid)
        cid = _cotizacion(conn, lid, ofid)
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE precio_cotizacion SET total_fees = 1.00 WHERE id = %s", (cid,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM precio_cotizacion WHERE id = %s", (cid,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE precio_cotizacion CASCADE")


@_skip_db
def test_cotizacion_error_exige_codigo():
    """`estado = 'error'` sin `error_code` revienta; con código pasa.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pc_error") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        ofid = _oferta(conn, lid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _cotizacion(conn, lid, ofid, estado="error")
        assert _cotizacion(conn, lid, ofid, estado="error", codigo="GET /fees 500")


@_skip_db
def test_cotizacion_success_exige_fees_y_fecha():
    """`success` sin `total_fees` o sin `fees_estimated_at` revienta; `error`
    con fees revienta (candado de 0028 copiado entero, punto 2 de la r3).
    Rojo r3: hoy entran."""
    with db_39("orbit_r3_cotiz") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        ofid = _oferta(conn, lid)
        with pytest.raises(
            psycopg.errors.CheckViolation, match="precio_cotizacion_success_exige_total"
        ):
            conn.execute(
                "INSERT INTO precio_cotizacion (listing_id, platform, intento,"
                " oferta_observation_id, quoted_price, quoted_price_currency,"
                " fees_estimated_at, estado, source_event_id)"
                " VALUES (%s, 'amazon_mx', 1, %s, 110.00, 'MXN', now(), 'success', 'r3-a')",
                (lid, ofid),
            )
        with pytest.raises(
            psycopg.errors.CheckViolation, match="precio_cotizacion_success_exige_total"
        ):
            conn.execute(
                "INSERT INTO precio_cotizacion (listing_id, platform, intento,"
                " oferta_observation_id, quoted_price, quoted_price_currency,"
                " total_fees, total_fees_currency, estado, error_code, source_event_id)"
                " VALUES (%s, 'amazon_mx', 1, %s, 110.00, 'MXN', 12.00, 'MXN',"
                " 'error', 'GET /fees 500', 'r3-c')",
                (lid, ofid),
            )
        with pytest.raises(
            psycopg.errors.CheckViolation, match="precio_cotizacion_success_exige_estimada"
        ):
            conn.execute(
                "INSERT INTO precio_cotizacion (listing_id, platform, intento,"
                " oferta_observation_id, quoted_price, quoted_price_currency,"
                " total_fees, total_fees_currency, estado, source_event_id)"
                " VALUES (%s, 'amazon_mx', 1, %s, 110.00, 'MXN', 12.00, 'MXN',"
                " 'success', 'r3-b')",
                (lid, ofid),
            )


@_skip_db
def test_muestra_append_only():
    """UPDATE/DELETE/TRUNCATE en `precio_envio_muestra` revientan.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pm_append") as conn:
        prod = _producto(conn)
        mid = _muestra(conn, prod)
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE precio_envio_muestra SET envios = 9 WHERE id = %s", (mid,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM precio_envio_muestra WHERE id = %s", (mid,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE precio_envio_muestra CASCADE")


# ---------------------------------------------------------------------------
# precio_cambio — transiciones, sello, abierto único, virtual, reversa
# ---------------------------------------------------------------------------


@_skip_db
def test_cambio_confirmado_a_pendiente_rechazado():
    """Retroceder `confirmado → pendiente` revienta.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_retro") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        cid = _cambio(conn, did, lid)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (cid,),
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (cid,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="progresi"):
            conn.execute("UPDATE precio_cambio SET estado = 'pendiente' WHERE id = %s", (cid,))


@_skip_db
def test_cambio_precios_y_origen_inmutables():
    """`precio_despues`, `precio_antes`, `aplicado` y `decision_id` no se
    reescriben: solo se sellan estado y columnas de sello.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_inmut") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        cid = _cambio(conn, did, lid)
        with pytest.raises(psycopg.errors.RestrictViolation, match="SOLO se sellan"):
            conn.execute("UPDATE precio_cambio SET precio_despues = 120.00 WHERE id = %s", (cid,))
        with pytest.raises(psycopg.errors.RestrictViolation, match="SOLO se sellan"):
            conn.execute("UPDATE precio_cambio SET aplicado = false WHERE id = %s", (cid,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM precio_cambio WHERE id = %s", (cid,))


@_skip_db
def test_cambio_segundo_abierto_rechazado():
    """Un solo cambio abierto por (listing, platform): el segundo
    `pendiente` choca; tras cerrar, el siguiente sí entra.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_abierto") as conn:
        prod = _producto(conn)
        # El virtual nace cerrado bajo decisión shadow y no ocupa el índice de
        # abierto (punto 4 de la r3: cada cambio cuelga de una decisión de su
        # modo; una publicación no puede ser shadow y live el mismo día por el
        # UNIQUE de decisión diaria).
        lid = _listing(conn, prod)
        _cambio_virtual(conn, _decision(conn, lid, mode="shadow"), lid)
        lid_b = _listing(conn, prod, ext="ASIN-B", sku="SKU-PB")
        did_b = _decision(conn, lid_b, mode="live")
        _cambio(conn, did_b, lid_b)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _cambio(conn, did_b, lid_b)
        # Tras cerrar el abierto, el siguiente sí entra.
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            ' ack = \'{"submissionId": "x"}\'::jsonb WHERE aplicado'
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE aplicado"
        )
        assert _cambio(conn, did_b, lid_b, estado="pendiente")


@_skip_db
def test_cambio_virtual_nace_cerrado():
    """El virtual nace `confirmado`/`virtual` con `enviado_at` y sin
    ack/readback; sin `enviado_at` o con `ack` revienta.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_virtual") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid, mode="shadow")
        assert _cambio_virtual(conn, did, lid)
        with pytest.raises(psycopg.errors.CheckViolation, match="virtual"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, confirmado_por)"
                " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN',"
                " false, 'confirmado', 'virtual')",
                (did, lid),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="virtual"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at, confirmado_por, ack)"
                " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN',"
                " false, 'confirmado', now(), 'virtual', '{}'::jsonb)",
                (did, lid),
            )


@_skip_db
def test_cambio_real_solo_nace_pendiente():
    """Con `aplicado = true` solo se nace `pendiente`.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_nace") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        with pytest.raises(psycopg.errors.CheckViolation, match="nace"):
            _cambio(conn, did, lid, estado="enviado")


@_skip_db
def test_cambio_transiciones_legales():
    """`pendiente → enviado | error` y `enviado → confirmado | no_confirmado`
    avanzan. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_legal") as conn:
        prod = _producto(conn)
        for n, destino in (("a", "enviado"), ("b", "error")):
            lid = _listing(conn, prod, ext=f"ASIN-{n}", sku=f"SKU-{n}")
            cid = _cambio(conn, _decision(conn, lid), lid)
            conn.execute(
                "UPDATE precio_cambio SET estado = %s, error_code = CASE WHEN %s = 'error'"
                " THEN 'PATCH 500' END, enviado_at = now(),"
                " ack = CASE WHEN %s = 'enviado' THEN '{\"s\": 1}'::jsonb END WHERE id = %s",
                (destino, destino, destino, cid),
            )
            assert (
                conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[0]
                == destino
            )
        lid = _listing(conn, prod, ext="ASIN-c", sku="SKU-c")
        cid = _cambio(conn, _decision(conn, lid), lid)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (cid,),
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'no_confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (cid,),
        )
        assert (
            conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[0]
            == "no_confirmado"
        )


@_skip_db
def test_cambio_transiciones_ilegales():
    """Saltos (`pendiente → confirmado`), laterales y salidas de
    terminales revientan. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_ilegal") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        c1 = _cambio(conn, _decision(conn, lid), lid)
        with pytest.raises(psycopg.errors.CheckViolation, match="progresi"):
            conn.execute("UPDATE precio_cambio SET estado = 'confirmado' WHERE id = %s", (c1,))
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        c2 = _cambio(conn, _decision(conn, lid2), lid2)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (c2,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="progresi"):
            conn.execute("UPDATE precio_cambio SET estado = 'error' WHERE id = %s", (c2,))
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (c2,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="progresi"):
            conn.execute("UPDATE precio_cambio SET estado = 'enviado' WHERE id = %s", (c2,))


@_skip_db
def test_cambio_sello_una_vez():
    """`ack` se sella una vez: re-sellar o des-sellar revienta.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_sello") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        cid = _cambio(conn, _decision(conn, lid), lid)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"a\": 1}'::jsonb WHERE id = %s",
            (cid,),
        )
        with pytest.raises(psycopg.errors.RestrictViolation, match="UNA vez"):
            conn.execute("UPDATE precio_cambio SET ack = '{\"a\": 2}'::jsonb WHERE id = %s", (cid,))
        with pytest.raises(psycopg.errors.RestrictViolation, match="UNA vez"):
            conn.execute("UPDATE precio_cambio SET ack = NULL WHERE id = %s", (cid,))


@_skip_db
def test_cambio_reversa_sin_decision():
    """La reversa no tiene decisión propia: `decision_id` NULL con
    `es_reversa` y `reversa_de`; cada cruce revienta con el NOMBRE de su
    constraint (no con el índice de abierto).
    Rojo pre-0039: la tabla no existe. Rojo r1: sin los CHECKs los cruces
    pasan."""
    with db_39("orbit_pcb_rev") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        orig = _cambio(conn, did, lid)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (orig,),
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (orig,),
        )
        rev = conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, es_reversa, reversa_de)"
            " VALUES (NULL, %s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
            " true, 'pendiente', true, %s) RETURNING id",
            (lid, orig),
        ).fetchone()[0]
        assert rev
        # Cada cruce nombra SU constraint: para que lo diga el CHECK y no el
        # trigger de coherencia (r3-3, que dispara primero), la reversa
        # referenciada es del mismo listing —con su propio original cerrado—.
        for n, (es_rev, resto) in (
            ("b", (True, "precio_cambio_decision_salvo_reversa")),
            ("c", (False, "precio_cambio_reversa_binaria")),
        ):
            lidx = _listing(conn, prod, ext=f"ASIN-{n}", sku=f"SKU-{n}")
            didx = _decision(conn, lidx)
            origx = _cambio(conn, didx, lidx)
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
                " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
                (origx,),
            )
            conn.execute(
                "UPDATE precio_cambio SET estado = 'confirmado',"
                " confirmado_por = 'observacion' WHERE id = %s",
                (origx,),
            )
            if es_rev:
                with pytest.raises(psycopg.errors.CheckViolation, match=resto):
                    conn.execute(
                        "INSERT INTO precio_cambio (decision_id, listing_id, platform,"
                        " precio_antes, precio_antes_currency, precio_despues,"
                        " precio_despues_currency, aplicado, estado, es_reversa, reversa_de)"
                        " VALUES (%s, %s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
                        " true, 'pendiente', true, %s)",
                        (didx, lidx, origx),
                    )
            else:
                with pytest.raises(psycopg.errors.CheckViolation, match=resto):
                    _cambio(conn, didx, lidx, reversa_de=origx)


@_skip_db
def test_cambio_truncate_rechazado():
    """TRUNCATE en `precio_cambio` revienta. Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_trunc") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _cambio(conn, _decision(conn, lid), lid)
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE precio_cambio")


# ---------------------------------------------------------------------------
# Cuota propia — apply_cap_de_config ampliado
# ---------------------------------------------------------------------------

_CAP_PRECIO = {
    "precio_cap_amazon_mx": 5,
    "precio_cap_amazon_us": 5,
    "precio_cap_meli": 5,
}
_CAP_ADS_MX_BID = 10


def _config(conn, settings: dict) -> int:
    from psycopg.types.json import Json

    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-0039', %s) RETURNING id",
        (Json(settings),),
    ).fetchone()[0]


@_skip_db
def test_quota_precio_nace_de_config():
    """`INSERT apply_quota_state (motor='precio:amazon_mx')` nace con el
    `cap` de la config. Rojo pre-0039: el motor no existe en el mapeo."""
    with db_39("orbit_pq_nace") as conn:
        _config(conn, _CAP_PRECIO)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        for motor, clave in (
            ("precio:amazon_mx", "precio_cap_amazon_mx"),
            ("precio:amazon_us", "precio_cap_amazon_us"),
            ("precio:meli", "precio_cap_meli"),
        ):
            cap = conn.execute(
                "INSERT INTO apply_quota_state (motor, quota_date, cap)"
                " VALUES (%s, %s, %s) RETURNING cap",
                (motor, hoy, _CAP_PRECIO[clave]),
            ).fetchone()[0]
            assert cap == _CAP_PRECIO[clave]


@_skip_db
def test_cap_resuelve_los_once_motores():
    """`apply_cap_de_config(motor)` devuelve lo suyo para los once motores
    con una config donde cada clave vale distinto (los ocho de Ads intactos +
    los tres de precio). Rojo r1: con el `in` viejo un mapeo adulterado pasaba."""
    with db_39("orbit_r1_once") as conn:
        claves = {
            "ads_apply_cap_amazon_us_bid": 11,
            "ads_apply_cap_amazon_us_pause": 12,
            "ads_apply_cap_amazon_us_negative": 13,
            "ads_apply_cap_amazon_us_harvest": 14,
            "ads_apply_cap_amazon_mx_bid": 15,
            "ads_apply_cap_amazon_mx_pause": 16,
            "ads_apply_cap_amazon_mx_negative": 17,
            "ads_apply_cap_amazon_mx_harvest": 18,
            "precio_cap_amazon_mx": 21,
            "precio_cap_amazon_us": 22,
            "precio_cap_meli": 23,
        }
        _config(conn, claves)
        motores = {
            "ads_optimizer:amazon_us:bid": 11,
            "ads_optimizer:amazon_us:pause": 12,
            "ads_optimizer:amazon_us:negative": 13,
            "ads_optimizer:amazon_us:harvest": 14,
            "ads_optimizer:amazon_mx:bid": 15,
            "ads_optimizer:amazon_mx:pause": 16,
            "ads_optimizer:amazon_mx:negative": 17,
            "ads_optimizer:amazon_mx:harvest": 18,
            "precio:amazon_mx": 21,
            "precio:amazon_us": 22,
            "precio:meli": 23,
        }
        for motor, esperado in motores.items():
            assert (
                conn.execute("SELECT apply_cap_de_config(%s)", (motor,)).fetchone()[0] == esperado
            ), motor


@_skip_db
def test_quota_precio_sin_clave_revienta():
    """Sin clave `precio_cap_*` en la config vigente no nace fila
    (fail-closed); con cap distinto tampoco. Rojo pre-0039: el motor no
    existe en el mapeo."""
    with db_39("orbit_pq_fail") as conn:
        _config(conn, {"otra_clave": 1})
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO apply_quota_state (motor, quota_date, cap)"
                " VALUES ('precio:amazon_mx', %s, 5)",
                (hoy,),
            )
        _config(conn, _CAP_PRECIO)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO apply_quota_state (motor, quota_date, cap)"
                " VALUES ('precio:amazon_mx', %s, 99)",
                (hoy,),
            )


@_skip_db
def test_quota_ads_intacta():
    """Los ocho mapeos de Ads siguen resolviendo tras el `CREATE OR REPLACE`.
    Rojo pre-0039: solo si 0039 rompiera el mapeo (no aplica); este test
    blinda la regresión."""
    with db_39("orbit_pq_ads") as conn:
        _config(conn, {**_CAP_PRECIO, "ads_apply_cap_amazon_mx_bid": _CAP_ADS_MX_BID})
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        cap = conn.execute(
            "INSERT INTO apply_quota_state (motor, quota_date, cap)"
            " VALUES ('ads_optimizer:amazon_mx:bid', %s, %s) RETURNING cap",
            (hoy, _CAP_ADS_MX_BID),
        ).fetchone()[0]
        assert cap == _CAP_ADS_MX_BID


# ---------------------------------------------------------------------------
# GRANTs por columna con el rol real + la migración no deja filas
# ---------------------------------------------------------------------------


@_skip_db
def test_app_read_no_inserta():
    """`app_read` solo lee las cinco tablas: INSERTs válidos bajo el rol
    mueren por permiso, y el SELECT pasa. Rojo pre-0039: no existen."""
    with db_39("orbit_pg_read") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        ofid = _oferta(conn, lid)
        validos = (
            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
            f" valid_from, creado_por) VALUES ({lid}, 'amazon_mx', 0.30, 'shadow',"
            " '2026-09-01', 'tdd')",
            f"INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
            f" {_MONEDAS_DECISION}) VALUES ({lid}, 'amazon_mx', 'mantener', 'candado',"
            " 'shadow', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN')",
            "INSERT INTO precio_cotizacion (listing_id, platform, intento, oferta_observation_id,"
            " quoted_price, quoted_price_currency, total_fees, total_fees_currency,"
            " fees_estimated_at, estado, source_event_id)"
            f" VALUES ({lid}, 'amazon_mx', 1, {ofid}, 110.00, 'MXN', 12.00, 'MXN',"
            " now(), 'success', 'x')",
            "INSERT INTO precio_envio_muestra (product_id, platform, ventana_desde,"
            f" ventana_hasta, envios, valor, valor_currency) VALUES ({prod}, 'amazon_mx',"
            " '2026-06-01', '2026-08-30', 8, 95.00, 'MXN')",
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado, estado)"
            f" VALUES ({did}, {lid}, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN', true, 'pendiente')",
        )
        antes = conn.execute("SELECT count(*) FROM precio_cotizacion").fetchone()[0]
        try:
            conn.execute("SET ROLE app_read")
            for sentencia in validos:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sentencia)
            assert conn.execute("SELECT count(*) FROM precio_cotizacion").fetchone()[0] == antes
        finally:
            conn.execute("RESET ROLE")


@_skip_db
def test_app_decide_inserta_y_sella_pero_no_goal():
    """El motor inserta decisión/cotización/muestra/cambio y sella el
    cambio; en `precio_goal` no inserta. Rojo pre-0039: no existen. Rojo r2:
    sin el `GRANT INSERT` la cotización o la muestra revienta por permiso."""
    with db_39("orbit_pg_decide") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        ofid = _oferta(conn, lid)
        _ensure_goal(conn, lid, mode="live")
        try:
            conn.execute("SET ROLE app_decide")
            did = conn.execute(
                f"INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
                f" {_MONEDAS_DECISION})"
                " VALUES (%s, 'amazon_mx', 'subir', 'candado', 'live',"
                " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN') RETURNING id",
                (lid,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO precio_cotizacion (listing_id, platform, intento,"
                " oferta_observation_id, quoted_price, quoted_price_currency, total_fees,"
                " total_fees_currency, fees_estimated_at, estado, source_event_id)"
                " VALUES (%s, 'amazon_mx', 1, %s, 110.00, 'MXN', 12.00, 'MXN',"
                " now(), 'success', 'r2-cotiz')",
                (lid, ofid),
            )
            conn.execute(
                "INSERT INTO precio_envio_muestra (product_id, platform, ventana_desde,"
                " ventana_hasta, envios, valor, valor_currency)"
                " VALUES (%s, 'amazon_mx', '2026-06-01', '2026-08-30', 8, 95.00, 'MXN')",
                (prod,),
            )
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
                " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
                (_cambio(conn, did, lid),),
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
                    " valid_from, creado_por) VALUES (%s, 'amazon_mx', 0.30, 'shadow',"
                    " '2026-09-01', 'tdd')",
                    (lid,),
                )
        finally:
            conn.execute("RESET ROLE")


@_skip_db
def test_app_admin_escribe_goal_y_no_decision():
    """Solo `app_admin` inserta goals y cierra `valid_to`; en decisión no
    inserta. Rojo pre-0039: no existen."""
    with db_39("orbit_pg_admin") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        # La negativa de decisión va en otro listing: el goal del admin se
        # cierra y ya no ampararía nada (r4 punto 3).
        lid_neg = _listing(conn, prod, ext="ASIN-N", sku="SKU-N")
        try:
            conn.execute("SET ROLE app_admin")
            gid = conn.execute(
                "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
                " valid_from, creado_por)"
                " VALUES (%s, 'amazon_mx', 0.30, 'shadow', '2026-09-01', 'tdd') RETURNING id",
                (lid,),
            ).fetchone()[0]
            conn.execute("UPDATE precio_goal SET valid_to = '2026-09-02' WHERE id = %s", (gid,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE precio_goal SET mode = 'live', go_literal = 'x' WHERE id = %s",
                    (gid,),
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _decision(conn, lid_neg)
        finally:
            conn.execute("RESET ROLE")


@_skip_db
def test_migracion_no_deja_filas():
    """El bloque DO de 0039 revierte lo que inserta: tras aplicar ORDEN39
    las cinco tablas están vacías. Rojo pre-0039: no existen."""
    with db_39("orbit_p_sin_filas") as conn:
        for tabla in (
            "precio_goal",
            "precio_decision",
            "precio_cotizacion",
            "precio_envio_muestra",
            "precio_cambio",
        ):
            assert conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] == 0


@_skip_db
def test_fk_compuesta_atrapa_plataforma_cruzada():
    """La FK es compuesta `(listing_id, platform)`: un listing que SÍ existe
    en `amazon_mx` insertado con `platform = 'amazon_us'` revienta (un
    `listing_id` inexistente no distinguiría una FK simple). En `precio_goal`
    (sin triggers) y en decisión sin insumos lo dice la FK; en el cambio lo
    dice primero el trigger de coherencia —y el catálogo prueba las tres
    definiciones. Rojo r1: con FK simple a `listing(id)` el mismatch pasa."""
    with db_39("orbit_r1_fk") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod, platform="amazon_mx", ext="ASIN1", sku="SKU-P1")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _goal(conn, lid, platform="amazon_us")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _decision(conn, lid, platform="amazon_us")
        did = _decision(conn, lid, mode="live")
        with pytest.raises(psycopg.errors.CheckViolation, match="decision .* es del listing"):
            _cambio(conn, did, lid, platform="amazon_us")
        for tabla, columna in (
            ("precio_goal", "precio_goal_listing_fk"),
            ("precio_decision", "precio_decision_listing_fk"),
            ("precio_cambio", "precio_cambio_listing_fk"),
        ):
            definicion = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
                (columna,),
            ).fetchone()[0]
            assert "FOREIGN KEY (listing_id, platform) REFERENCES listing" in definicion, (
                f"{tabla}: {definicion}"
            )


@_skip_db
def test_triggers_truncate_en_catalogo():
    """Cada una de las cinco tablas tiene su trigger `BEFORE TRUNCATE` de
    sentencia sobre `prohibir_mutacion` (determinista: no depende de qué otro
    trigger dispare un CASCADE). Rojo r1: sin el trigger el catálogo no trae
    la fila."""
    with db_39("orbit_r1_truncat") as conn:
        filas = {
            r[0]
            for r in conn.execute(
                "SELECT tgrelid::regclass::text FROM pg_trigger"
                " WHERE NOT tgisinternal AND tgenabled <> 'D'"
                " AND ((tgtype & 32) <> 0) AND ((tgtype & 2) <> 0)"
                " AND tgfoid = 'prohibir_mutacion()'::regprocedure"
            ).fetchall()
        }
        for tabla in (
            "precio_goal",
            "precio_decision",
            "precio_cotizacion",
            "precio_envio_muestra",
            "precio_cambio",
        ):
            assert tabla in filas, f"{tabla} sin trigger BEFORE TRUNCATE de sentencia"


@_skip_db
def test_decision_resultado_vocabulario():
    """S4 fija seis resultados: otro valor revienta con nombre de constraint.
    Rojo r1: sin el CHECK el INSERT pasa."""
    with db_39("orbit_r1_vocab") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_decision_resultado_valido"):
            _decision(conn, lid, resultado="invento")


@_skip_db
def test_decision_no_evaluado_exige_motivo():
    """Decisión 11 («ningún silencio»): fuera de subir/bajar el motivo es
    obligatorio y no en blanco; subir/bajar sin motivo sí pasan.
    Rojo r1: sin el CHECK el silencio pasa."""
    with db_39("orbit_r1_motivo") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        with pytest.raises(
            psycopg.errors.CheckViolation, match="precio_decision_motivo_no_silencio"
        ):
            _decision(conn, lid, resultado="no_evaluado", motivo=None)
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        with pytest.raises(
            psycopg.errors.CheckViolation, match="precio_decision_motivo_no_silencio"
        ):
            _decision(conn, lid2, resultado="frenado", motivo="   ")
        lid3 = _listing(conn, prod, ext="ASIN3", sku="SKU-P3")
        assert _decision(conn, lid3, resultado="subir", motivo=None)


@_skip_db
def test_cambio_error_exige_codigo():
    """`pendiente → error` sin `error_code` revienta con nombre de constraint.
    Rojo r1: sin el CHECK el avance pasa."""
    with db_39("orbit_r1_errcode") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        cid = _cambio(conn, _decision(conn, lid), lid)
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_cambio_error_exige_codigo"):
            conn.execute("UPDATE precio_cambio SET estado = 'error' WHERE id = %s", (cid,))


@_skip_db
def test_cambio_confirmado_exige_confirmado_por():
    """Cerrar a `confirmado` sin `confirmado_por` revienta.
    Rojo r1: sin el CHECK el avance pasa."""
    with db_39("orbit_r1_confpor") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        cid = _cambio(conn, _decision(conn, lid), lid)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (cid,),
        )
        with pytest.raises(
            psycopg.errors.CheckViolation, match="precio_cambio_cierre_exige_origen"
        ):
            conn.execute("UPDATE precio_cambio SET estado = 'confirmado' WHERE id = %s", (cid,))


@_skip_db
def test_cambio_enviado_exige_ack():
    """`pendiente → enviado` sin `ack` ni `enviado_at` revienta.
    Rojo r1: sin el CHECK el avance pasa."""
    with db_39("orbit_r1_ack") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        cid = _cambio(conn, _decision(conn, lid), lid)
        with pytest.raises(psycopg.errors.CheckViolation, match="precio_cambio_envio_exige_ack"):
            conn.execute("UPDATE precio_cambio SET estado = 'enviado' WHERE id = %s", (cid,))


@_skip_db
def test_cambio_nace_sin_sellos():
    """Un cambio real nace `pendiente` y sin sellos puestos (si no, el «sello
    una sola vez» bloquearía el sello legítimo); `enviado_at` sí puede nacer
    puesto (A.4). Rojo r1: sin la regla el nacimiento con `ack` pasa."""
    with db_39("orbit_r1_nacesello") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        with pytest.raises(psycopg.errors.CheckViolation, match="nace sin sellos"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, ack)"
                " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN',"
                " true, 'pendiente', '{}'::jsonb)",
                (did, lid),
            )
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        did2 = _decision(conn, lid2)
        assert conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, enviado_at)"
            " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN',"
            " true, 'pendiente', now()) RETURNING id",
            (did2, lid2),
        ).fetchone()[0]


@_skip_db
def test_goal_anulado_mismo_dia():
    """`valid_to = valid_from` es intervalo vacío: el goal queda anulado,
    nunca vigente (apaga el error de dedo el mismo día).
    Rojo r1: con `>` el cierre mismo-día revienta."""
    with db_39("orbit_r1_anulado") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        gid = _goal(conn, lid)
        conn.execute("UPDATE precio_goal SET valid_to = valid_from WHERE id = %s", (gid,))
        assert (
            conn.execute("SELECT valid_to FROM precio_goal WHERE id = %s", (gid,)).fetchone()[0]
            == conn.execute("SELECT valid_from FROM precio_goal WHERE id = %s", (gid,)).fetchone()[
                0
            ]
        )


@_skip_db
def test_cotiza_antes_de_decidir_puntero_lleno():
    """Flujo S4 #3 con la forma nueva (r3 punto 1), bajo `SET ROLE app_decide`:
    cotización 1, cotización 2, decisión con `cotizacion_id` = la 2 → las tres
    entran y el puntero queda lleno; el tercer intento del día se rechaza.
    Rojo r3: sin `decision_id` la cotización no nacía."""
    with db_39("orbit_r3_e2e") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        ofid = _oferta(conn, lid)
        _ensure_goal(conn, lid, mode="live")
        try:
            conn.execute("SET ROLE app_decide")
            c1 = conn.execute(
                "INSERT INTO precio_cotizacion (listing_id, platform, intento,"
                " oferta_observation_id, quoted_price, quoted_price_currency,"
                " total_fees, total_fees_currency, fees_estimated_at, estado, source_event_id)"
                " VALUES (%s, 'amazon_mx', 1, %s, 110.00, 'MXN', 12.00, 'MXN',"
                " now(), 'success', 'r3-e2e-1') RETURNING id",
                (lid, ofid),
            ).fetchone()[0]
            c2 = conn.execute(
                "INSERT INTO precio_cotizacion (listing_id, platform, intento,"
                " oferta_observation_id, quoted_price, quoted_price_currency,"
                " total_fees, total_fees_currency, fees_estimated_at, estado, source_event_id)"
                " VALUES (%s, 'amazon_mx', 2, %s, 108.00, 'MXN', 11.00, 'MXN',"
                " now(), 'success', 'r3-e2e-2') RETURNING id, cotizacion_date",
                (lid, ofid),
            ).fetchone()
            assert c2[1] == conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
            did = conn.execute(
                f"INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
                f" cotizacion_id, {_MONEDAS_DECISION})"
                " VALUES (%s, 'amazon_mx', 'bajar', 'e2e', 'live', %s,"
                " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN') RETURNING id",
                (lid, c2[0]),
            ).fetchone()[0]
            assert (
                conn.execute(
                    "SELECT cotizacion_id FROM precio_decision WHERE id = %s", (did,)
                ).fetchone()[0]
                == c2[0]
            )
            assert c1 != c2[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO precio_cotizacion (listing_id, platform, intento,"
                    " oferta_observation_id, quoted_price, quoted_price_currency,"
                    " total_fees, total_fees_currency, fees_estimated_at, estado,"
                    " source_event_id)"
                    " VALUES (%s, 'amazon_mx', 3, %s, 107.00, 'MXN', 10.00, 'MXN',"
                    " now(), 'success', 'r3-e2e-3')",
                    (lid, ofid),
                )
        finally:
            conn.execute("RESET ROLE")


@_skip_db
def test_coherencia_cotizacion_oferta():
    """La oferta cotizada es del mismo `(listing_id, platform)`.
    Rojo r3: hoy entra cruzada."""
    with db_39("orbit_r3_coh_cot") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        ofid = _oferta(conn, lid)
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        with pytest.raises(psycopg.errors.CheckViolation, match="oferta .* es del listing"):
            conn.execute(
                "INSERT INTO precio_cotizacion (listing_id, platform, intento,"
                " oferta_observation_id, quoted_price, quoted_price_currency,"
                " total_fees, total_fees_currency, fees_estimated_at, estado, source_event_id)"
                " VALUES (%s, 'amazon_mx', 1, %s, 110.00, 'MXN', 12.00, 'MXN',"
                " now(), 'success', 'r3-x')",
                (lid2, ofid),
            )


@_skip_db
def test_coherencia_decision_insumos():
    """Escenario, fee y cotización de la decisión son del mismo
    `(listing_id, platform)`; la muestra, del mismo `(product_id, platform)`;
    y `product_id`, el del listing. Un test por cruce, dos listings.
    Rojo r3: hoy entran cruzados."""
    with db_39("orbit_r3_coh_dec") as conn:
        prod = _producto(conn)
        prod_b = _producto(conn, sku="SKU-PRECIO-B")
        lid = _listing(conn, prod)
        ofid = _oferta(conn, lid)
        esc = _escenario(conn, lid)
        fee = _fee(conn, lid, ofid)
        cot = _cotizacion(conn, lid, ofid)
        mues = _muestra(conn, prod)
        lid2 = _listing(conn, prod_b, ext="ASIN2", sku="SKU-P2")

        def _cruzada(columna):
            return (
                "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
                f" {_MONEDAS_DECISION}, {columna})"
                " VALUES (%s, 'amazon_mx', 'mantener', 'x', 'live',"
                " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', %s)"
            )

        with pytest.raises(psycopg.errors.CheckViolation, match="escenario .*listing"):
            conn.execute(_cruzada("escenario_id"), (lid2, esc))
        with pytest.raises(psycopg.errors.CheckViolation, match="fee .*listing"):
            conn.execute(_cruzada("fee_observation_id"), (lid2, fee))
        with pytest.raises(psycopg.errors.CheckViolation, match="cotizacion .*listing"):
            conn.execute(_cruzada("cotizacion_id"), (lid2, cot))
        with pytest.raises(psycopg.errors.CheckViolation, match="muestra .*product"):
            conn.execute(_cruzada("envio_muestra_id"), (lid2, mues))
        with pytest.raises(psycopg.errors.CheckViolation, match="no es el producto"):
            conn.execute(_cruzada("product_id"), (lid, prod_b))


@_skip_db
def test_coherencia_cambio_origen():
    """El cambio cuelga de una decisión del mismo `(listing_id, platform)` y la
    reversa de un cambio real no-reversa del mismo par. Rojo r3: hoy entran."""
    with db_39("orbit_r3_coh_cambio") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid, mode="live")
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        did2 = _decision(conn, lid2, mode="live")
        with pytest.raises(psycopg.errors.CheckViolation, match="decision .*listing"):
            _cambio(conn, did2, lid)
        orig = _cambio(conn, did, lid)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (orig,),
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (orig,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="reversa .*listing"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, es_reversa, reversa_de)"
                " VALUES (NULL, %s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
                " true, 'pendiente', true, %s)",
                (lid2, orig),
            )


@_skip_db
def test_cambio_real_exige_decision_live():
    """S4 #13: cambio real solo bajo decisión `live`; virtual solo bajo
    `shadow`; reversa (sin decisión) siempre `aplicado = true`.
    Rojo r3: hoy el modo no se mira."""
    with db_39("orbit_r3_modo") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did_shadow = _decision(conn, lid, mode="shadow")
        with pytest.raises(psycopg.errors.CheckViolation, match="mode"):
            _cambio(conn, did_shadow, lid)
        lid2 = _listing(conn, prod, ext="ASIN2", sku="SKU-P2")
        did_live = _decision(conn, lid2, mode="live")
        with pytest.raises(psycopg.errors.CheckViolation, match="mode"):
            _cambio_virtual(conn, did_live, lid2)
        assert _cambio(conn, did_live, lid2)
        lid3 = _listing(conn, prod, ext="ASIN3", sku="SKU-P3")
        assert _cambio_virtual(conn, _decision(conn, lid3, mode="shadow"), lid3)


@_skip_db
def test_created_at_inmutable_por_trigger():
    """`created_at` está en el ROW inmutable de goal y cambio: ni con GRANT
    amplio se reescribe, lo sostiene el trigger. Rojo r3: hoy pasa."""
    with db_39("orbit_r3_created") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        gid = _goal(conn, lid, mode="live", go="go tdd")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE precio_goal SET created_at = now() WHERE id = %s", (gid,))
        cid = _cambio(conn, _decision(conn, lid, mode="live"), lid)
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE precio_cambio SET created_at = now() WHERE id = %s", (cid,))


@_skip_db
def test_virtual_no_recibe_sellos():
    """Nace `confirmado` sin sellos y así se queda: cualquier UPDATE sobre un
    virtual se rechaza. Rojo r3: hoy el sello NULL → valor pasa."""
    with db_39("orbit_r3_virtual") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        vc = _cambio_virtual(conn, _decision(conn, lid, mode="shadow"), lid)
        with pytest.raises(psycopg.errors.RestrictViolation, match="virtual"):
            conn.execute("UPDATE precio_cambio SET ack = '{\"s\": 1}'::jsonb WHERE id = %s", (vc,))
        with pytest.raises(psycopg.errors.RestrictViolation, match="virtual"):
            conn.execute("UPDATE precio_cambio SET estado = 'confirmado' WHERE id = %s", (vc,))


@_skip_db
def test_reversa_solo_de_cambio_real():
    """La reversa apunta a un cambio real no-reversa: ni a un virtual
    (`aplicado = false`) ni a otra reversa —cada caso con el mensaje de ESA
    rama—. Y el positivo al lado: reversa de un real confirmado entra.
    Rojo r3b: sin la rama los cruces pasan."""
    with db_39("orbit_r3b_rev") as conn:
        prod = _producto(conn)

        def _cerrado(lid, did):
            orig = _cambio(conn, did, lid)
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
                " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
                (orig,),
            )
            conn.execute(
                "UPDATE precio_cambio SET estado = 'confirmado',"
                " confirmado_por = 'observacion' WHERE id = %s",
                (orig,),
            )
            return orig

        # (a) reversa_de = virtual del mismo listing (shadow + virtual).
        lid_v = _listing(conn, prod)
        virt = _cambio_virtual(conn, _decision(conn, lid_v, mode="shadow"), lid_v)
        with pytest.raises(psycopg.errors.CheckViolation, match="no es un cambio real no-reversa"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, es_reversa, reversa_de)"
                " VALUES (NULL, %s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
                " true, 'pendiente', true, %s)",
                (lid_v, virt),
            )
        # (b) reversa_de = otra reversa del mismo listing.
        lid_r = _listing(conn, prod, ext="ASIN-R", sku="SKU-R")
        orig_r = _cerrado(lid_r, _decision(conn, lid_r, mode="live"))
        rev1 = conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, es_reversa, reversa_de)"
            " VALUES (NULL, %s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
            " true, 'pendiente', true, %s) RETURNING id",
            (lid_r, orig_r),
        ).fetchone()[0]
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (rev1,),
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado',"
            " confirmado_por = 'observacion' WHERE id = %s",
            (rev1,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="no es un cambio real no-reversa"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, es_reversa, reversa_de)"
                " VALUES (NULL, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN',"
                " true, 'pendiente', true, %s)",
                (lid_r, rev1),
            )
        # Positivo: reversa de un real confirmado entra.
        lid_p = _listing(conn, prod, ext="ASIN-P", sku="SKU-P")
        orig_p = _cerrado(lid_p, _decision(conn, lid_p, mode="live"))
        assert conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, es_reversa, reversa_de)"
            " VALUES (NULL, %s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
            " true, 'pendiente', true, %s) RETURNING id",
            (lid_p, orig_p),
        ).fetchone()[0]


@_skip_db
def test_reversa_nace_aplicada():
    """Sin decisión (reversa) solo `aplicado = true`: con `false` la rechaza
    la rama ELSIF del nacimiento. Rojo r3b: sin la rama cae en otro error."""
    with db_39("orbit_r3b_aplic") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        orig = _cambio(conn, _decision(conn, lid, mode="live"), lid)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (orig,),
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado',"
            " confirmado_por = 'observacion' WHERE id = %s",
            (orig,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="solo aplicado = true"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, es_reversa, reversa_de)"
                " VALUES (NULL, %s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
                " false, 'pendiente', true, %s)",
                (lid, orig),
            )


@_skip_db
def test_virtual_exige_confirmado_por_virtual():
    """La rama virtual del nacimiento compara NULL-safe: con
    `confirmado_por` NULL dispara el trigger (hoy lo tapa el CHECK de
    cierre). Rojo r4: sale el mensaje del CHECK, no el del trigger."""
    with db_39("orbit_r4_nulseg") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid, mode="shadow")
        with pytest.raises(psycopg.errors.CheckViolation, match="nace cerrado"):
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at)"
                " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN',"
                " false, 'confirmado', now())",
                (did, lid),
            )


@_skip_db
def test_confirmado_por_vocabulario_cerrado():
    """`confirmado_por` solo `observacion` o `virtual` (S6: solo cierra la
    observación; el virtual nace cerrado). Rojo r4: 'jefe' entra."""
    with db_39("orbit_r4_origen") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        cid = _cambio(conn, _decision(conn, lid, mode="live"), lid)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            " ack = '{\"s\": 1}'::jsonb WHERE id = %s",
            (cid,),
        )
        with pytest.raises(
            psycopg.errors.CheckViolation, match="precio_cambio_confirmado_por_valido"
        ):
            conn.execute(
                "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'jefe'"
                " WHERE id = %s",
                (cid,),
            )


@_skip_db
def test_error_code_solo_en_error():
    """`error_code` fuera de `error` revienta. Rojo r4: hoy entra."""
    with db_39("orbit_r4_errfuera") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        cid = _cambio(conn, _decision(conn, lid, mode="live"), lid)
        with pytest.raises(
            psycopg.errors.CheckViolation, match="precio_cambio_error_code_solo_error"
        ):
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
                " ack = '{\"s\": 1}'::jsonb, error_code = 'X' WHERE id = %s",
                (cid,),
            )


@_skip_db
def test_decision_exige_goal_vigente():
    """«Sin fila vigente no hay decisión» (S3) en la base: sin goal, con goal
    cerrado ayer, con goal anulado hoy o con modo distinto → rechazada; goal
    `live` + decisión `live` → entra. Rojo r4: hoy entran todas."""
    with db_39("orbit_r4_goalvig") as conn:
        prod = _producto(conn)
        # Sin goal.
        lid0 = _listing(conn, prod, ext="ASIN-0", sku="SKU-0")
        with pytest.raises(psycopg.errors.CheckViolation, match="sin goal vigente"):
            _decision_sin_siembra(conn, lid0, mode="live")
        # Goal cerrado ayer.
        lid1 = _listing(conn, prod, ext="ASIN-1", sku="SKU-1")
        _goal(conn, lid1, mode="live", go="go tdd", valid_from="2026-09-01")
        conn.execute(
            "UPDATE precio_goal SET valid_to = (now() AT TIME ZONE 'UTC')::date - 1"
            " WHERE listing_id = %s",
            (lid1,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="sin goal vigente"):
            _decision_sin_siembra(conn, lid1, mode="live")
        # Goal anulado hoy (valid_to = valid_from).
        lid2 = _listing(conn, prod, ext="ASIN-2", sku="SKU-2")
        _goal(conn, lid2, mode="live", go="go tdd", valid_from="2026-09-01")
        conn.execute("UPDATE precio_goal SET valid_to = valid_from WHERE listing_id = %s", (lid2,))
        with pytest.raises(psycopg.errors.CheckViolation, match="sin goal vigente"):
            _decision_sin_siembra(conn, lid2, mode="live")
        # Goal shadow + decisión live.
        lid3 = _listing(conn, prod, ext="ASIN-3", sku="SKU-P3")
        _goal(conn, lid3, mode="shadow", valid_from="2026-09-01")
        with pytest.raises(psycopg.errors.CheckViolation, match="sin goal vigente"):
            _decision_sin_siembra(conn, lid3, mode="live")
        # Goal live + decisión live → entra.
        lid4 = _listing(conn, prod, ext="ASIN-4", sku="SKU-4")
        _goal(conn, lid4, mode="live", go="go tdd", valid_from="2026-09-01")
        assert _decision_sin_siembra(conn, lid4, mode="live")
        # Goal que todavía no arranca (r4b): live con `valid_from` = mañana y
        # `valid_to` NULL + decisión live hoy → rechazada. Sin `_ensure_goal`
        # (sembraría uno vigente y taparía el caso).
        lid5 = _listing(conn, prod, ext="ASIN-5", sku="SKU-5")
        manana = conn.execute("SELECT (((now() AT TIME ZONE 'UTC')::date + 1))::text").fetchone()[0]
        _goal(conn, lid5, mode="live", go="go tdd", valid_from=manana)
        with pytest.raises(psycopg.errors.CheckViolation, match="sin goal vigente"):
            _decision_sin_siembra(conn, lid5, mode="live")


@_skip_db
def test_cambio_exige_decision_que_mueve_precio():
    """El cambio cuelga de una decisión con `resultado IN ('subir','bajar')`.
    Rojo r4: colgado de `mantener` entra."""
    with db_39("orbit_r4_mueve") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal(conn, lid, mode="live", go="go tdd", valid_from="2026-01-01")
        with pytest.raises(psycopg.errors.CheckViolation, match="no mueve precio"):
            _cambio(conn, _decision(conn, lid, mode="live", resultado="mantener"), lid)


@_skip_db
def test_listing_gana_unique_id_platform():
    """0039 declara `UNIQUE (id, platform)` en `listing` (lo único que toca
    de una tabla existente): la FK compuesta de `precio_goal` lo exige.
    Rojo pre-0039: el constraint no existe."""
    with db_39("orbit_p_listing_uq") as conn:
        fila = conn.execute(
            "SELECT conname FROM pg_constraint"
            " WHERE conrelid = 'public.listing'::regclass AND conname = 'listing_id_platform_key'"
        ).fetchone()
        assert fila is not None
