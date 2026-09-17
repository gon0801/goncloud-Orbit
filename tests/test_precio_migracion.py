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


def _decision(
    conn, listing: int, *, platform="amazon_mx", resultado="mantener", mode="shadow"
) -> int:
    return conn.execute(
        f"INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
        f" {_MONEDAS_DECISION})"
        " VALUES (%s, %s, %s, 'tdd', %s,"
        " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN') RETURNING id",
        (listing, platform, resultado, mode),
    ).fetchone()[0]


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


def _cotizacion(conn, decision: int, oferta: int, *, estado="success", codigo=None) -> int:
    return conn.execute(
        "INSERT INTO precio_cotizacion (decision_id, oferta_observation_id, quoted_price,"
        " quoted_price_currency, total_fees, total_fees_currency, fee_details,"
        " fees_estimated_at, estado, error_code, source_event_id)"
        " VALUES (%s, %s, 110.00, 'MXN', 12.00, 'MXN', '[]'::jsonb, now(), %s, %s, %s)"
        " RETURNING id",
        (decision, oferta, estado, codigo, f"cotiz-{decision}-{oferta}"),
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
        did = _decision(conn, lid)
        ofid = _oferta(conn, lid)
        cid = _cotizacion(conn, did, ofid)
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
        did = _decision(conn, lid)
        ofid = _oferta(conn, lid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _cotizacion(conn, did, ofid, estado="error")
        assert _cotizacion(conn, did, ofid, estado="error", codigo="GET /fees 500")


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
        conn.execute("UPDATE precio_cambio SET estado = 'enviado' WHERE id = %s", (cid,))
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
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        # El virtual nace cerrado: no ocupa el índice de abierto.
        _cambio_virtual(conn, did, lid)
        _cambio(conn, did, lid)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _cambio(conn, did, lid)
        # Tras cerrar el abierto, el siguiente sí entra.
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),"
            ' ack = \'{"submissionId": "x"}\'::jsonb WHERE aplicado'
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE aplicado"
        )
        assert _cambio(conn, did, lid, estado="pendiente")


@_skip_db
def test_cambio_virtual_nace_cerrado():
    """El virtual nace `confirmado`/`virtual` con `enviado_at` y sin
    ack/readback; sin `enviado_at` o con `ack` revienta.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_virtual") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
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
                " THEN 'PATCH 500' END WHERE id = %s",
                (destino, destino, cid),
            )
            assert (
                conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[0]
                == destino
            )
        lid = _listing(conn, prod, ext="ASIN-c", sku="SKU-c")
        cid = _cambio(conn, _decision(conn, lid), lid)
        conn.execute("UPDATE precio_cambio SET estado = 'enviado' WHERE id = %s", (cid,))
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
        conn.execute("UPDATE precio_cambio SET estado = 'enviado' WHERE id = %s", (c2,))
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
            "UPDATE precio_cambio SET estado = 'enviado', ack = '{\"a\": 1}'::jsonb WHERE id = %s",
            (cid,),
        )
        with pytest.raises(psycopg.errors.RestrictViolation, match="UNA vez"):
            conn.execute("UPDATE precio_cambio SET ack = '{\"a\": 2}'::jsonb WHERE id = %s", (cid,))
        with pytest.raises(psycopg.errors.RestrictViolation, match="UNA vez"):
            conn.execute("UPDATE precio_cambio SET ack = NULL WHERE id = %s", (cid,))


@_skip_db
def test_cambio_reversa_sin_decision():
    """La reversa no tiene decisión propia: `decision_id` NULL con
    `es_reversa` y `reversa_de`; los cruces revientan.
    Rojo pre-0039: la tabla no existe."""
    with db_39("orbit_pcb_rev") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        did = _decision(conn, lid)
        orig = _cambio(conn, did, lid)
        conn.execute("UPDATE precio_cambio SET estado = 'enviado' WHERE id = %s", (orig,))
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
        with pytest.raises(psycopg.errors.CheckViolation):
            _cambio(conn, did, lid, es_reversa=True)
        with pytest.raises(psycopg.errors.CheckViolation):
            _cambio(conn, None, lid, es_reversa=False)
        with pytest.raises(psycopg.errors.CheckViolation):
            _cambio(conn, did, lid, reversa_de=orig)


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
            f"INSERT INTO precio_decision (listing_id, platform, resultado, mode,"
            f" {_MONEDAS_DECISION}) VALUES ({lid}, 'amazon_mx', 'mantener', 'shadow',"
            " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN')",
            "INSERT INTO precio_cotizacion (decision_id, oferta_observation_id, quoted_price,"
            " quoted_price_currency, fees_estimated_at, estado, source_event_id)"
            f" VALUES ({did}, {ofid}, 110.00, 'MXN', now(), 'success', 'x')",
            "INSERT INTO precio_envio_muestra (product_id, platform, ventana_desde,"
            f" ventana_hasta, envios, valor, valor_currency) VALUES ({prod}, 'amazon_mx',"
            " '2026-06-01', '2026-08-30', 8, 95.00, 'MXN')",
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado, estado)"
            f" VALUES ({did}, {lid}, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN', true, 'pendiente')",
        )
        try:
            conn.execute("SET ROLE app_read")
            for sentencia in validos:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sentencia)
            assert conn.execute("SELECT count(*) FROM precio_goal").fetchone()[0] == 0
        finally:
            conn.execute("RESET ROLE")


@_skip_db
def test_app_decide_inserta_y_sella_pero_no_goal():
    """El motor inserta decisión/cotización/muestra/cambio y sella el
    cambio; en `precio_goal` no inserta. Rojo pre-0039: no existen."""
    with db_39("orbit_pg_decide") as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        try:
            conn.execute("SET ROLE app_decide")
            did = conn.execute(
                f"INSERT INTO precio_decision (listing_id, platform, resultado, mode,"
                f" {_MONEDAS_DECISION})"
                " VALUES (%s, 'amazon_mx', 'mantener', 'shadow',"
                " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN') RETURNING id",
                (lid,),
            ).fetchone()[0]
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado' WHERE id = %s",
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
                _decision(conn, lid)
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
