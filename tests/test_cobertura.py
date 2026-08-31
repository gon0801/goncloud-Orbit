"""Candado de cobertura (ORBIT 06 0.7): que fraccion del GASTO real puede
costearse de punta a punta (anuncio → listing → costo → FX).

Lo que estos tests protegen, en orden de gravedad:

1. La ponderacion es por GASTO, no por conteo de entidades (la fila del plan
   lo exige: un grupo con 1,000 MXN pesa 1,000, no 1).
2. Un ad group cuyos product ads estan TODOS archivados clasifica
   `sin_anuncios_vivos` — NO desaparece de la medicion. Este es el bug que
   la medicion manual del lead tuvo (filtro de estado en WHERE en vez del
   JOIN): el grupo se esfumaba y su gasto quedaba `grupo_desconocido`.
3. El desglose distingue multi-ASIN de cubierto-unico (la atribucion
   por-producto de los multi es decision de la vista 1.1, no de este gate).
4. Nunca un SUM mezclando monedas (regla 4): el gasto sale por
   (plataforma, moneda).
5. Un dia de gasto USD sin tasa utilizable cuenta como gasto_sin_fx.
"""

from __future__ import annotations

import os
import socket
from datetime import date, timedelta

import pytest
from test_schema import SQL, SQL4, _hay_postgres_local, _test_dsn

from app.cobertura import (
    ESTADOS,
    SQL_COBERTURA,
    main,
    medir_cobertura,
    resumen_por_plataforma,
)


def test_estados_vocabulario_cerrado():
    assert ESTADOS == (
        "cubierto_unico",
        "cubierto_multi_asin",
        "sin_costo",
        "sin_listing",
        "sin_anuncios_vivos",
        "grupo_desconocido",
    )


def test_sql_parsea_como_postgres():
    """Guarda barata que SIEMPRE corre (mismo patron que structure)."""
    import pglast

    assert pglast.parse_sql(SQL_COBERTURA.replace("%(dias)s", "NULL"))


def test_resumen_pondera_por_gasto_no_por_conteo():
    """Un grupo cubierto de 1,000 y nueve sin listing de 1 c/u = 99% cubierto
    por gasto aunque sea 10% por conteo."""
    from decimal import Decimal

    filas = [
        ("amazon_mx", "MXN", "cubierto_multi_asin", 1, Decimal("1000"), Decimal("0")),
        ("amazon_mx", "MXN", "sin_listing", 9, Decimal("9"), Decimal("0")),
    ]
    resumen = resumen_por_plataforma(filas)
    fila = resumen[("amazon_mx", "MXN")]
    assert fila["gasto_total"] == Decimal("1009")
    assert fila["gasto_cubierto"] == Decimal("1000")
    assert round(fila["pct"], 1) == 99.1
    # multi-ASIN NO cuenta en la estricta: los dos numeros divergen a la vista
    assert fila["pct_estricta"] == 0.0


def test_resumen_no_mezcla_monedas():
    from decimal import Decimal

    filas = [
        ("amazon_mx", "MXN", "cubierto_unico", 1, Decimal("100"), Decimal("0")),
        ("amazon_us", "USD", "cubierto_unico", 1, Decimal("100"), Decimal("0")),
    ]
    resumen = resumen_por_plataforma(filas)
    assert ("amazon_mx", "MXN") in resumen and ("amazon_us", "USD") in resumen
    assert all(v["gasto_total"] == Decimal("100") for v in resumen.values())


def test_gasto_sin_fx_no_cuenta_como_cubierto():
    """qwen r2 [media]: el invariante estaba DECLARADO (proteccion 5 del
    docstring) pero ningun test lo ejercitaba — `+= gasto` a secas dejaba
    la suite verde. Aqui un grupo cubierto con TODO su gasto sin tasa no
    aporta nada al cubierto, y uno parcial aporta solo la parte con tasa."""
    from decimal import Decimal

    filas = [
        ("amazon_us", "USD", "cubierto_multi_asin", 1, Decimal("100"), Decimal("100")),
        ("amazon_us", "USD", "cubierto_unico", 1, Decimal("50"), Decimal("20")),
    ]
    resumen = resumen_por_plataforma(filas)
    fila = resumen[("amazon_us", "USD")]
    assert fila["gasto_total"] == Decimal("150")
    assert fila["gasto_cubierto"] == Decimal("30"), "solo los 50-20 con tasa"
    assert fila["gasto_cubierto_unico"] == Decimal("30")
    assert fila["gasto_sin_fx"] == Decimal("120")
    assert fila["pct"] == 20.0


def test_ventana_invalida_es_error_de_uso_sin_conectar(monkeypatch, capsys):
    """qwen r2 (baja): --ventana-dias 0 salia exit 1 tras abrir conexion;
    es un error de OPERADOR: exit 2 y jamas se conecta."""

    def _no_conectar(_dsn):
        raise AssertionError("no debe conectarse con ventana invalida")

    monkeypatch.setenv("ORBIT_DSN_READ", "postgresql://x@127.0.0.1/x")
    monkeypatch.setattr("app.cobertura.connect", _no_conectar)
    assert main(["--ventana-dias", "0"]) == 2
    assert "ventana-dias" in capsys.readouterr().err


def test_main_sin_dsn_falla_cerrado(monkeypatch, capsys):
    """Rol de LECTURA (minimo privilegio, hallazgo codex): jamas el de
    decision para un reporte."""
    monkeypatch.delenv("ORBIT_DSN_READ", raising=False)
    assert main(["--ventana-dias", "90"]) == 2
    assert "ORBIT_DSN_READ" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Integracion (patron test_schema: skip local sin Postgres, corre en CI)
# ---------------------------------------------------------------------------

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_cobertura_clasifica_y_pondera_en_vivo():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_cob_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL4)

        run_id = conn.execute(
            "INSERT INTO ingest_run (source) VALUES ('test') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "UPDATE ingest_run SET finished_at = now(), ok = true WHERE id = %s", (run_id,)
        )

        def entidad(kind, ext, parent=None, listing=None):
            # CHECK ad_entity_keyword_coherente: keyword exige match_type y
            # keyword_text; el resto los exige NULL (esquema real, regla 8).
            match, texto = ("EXACT", ext) if kind == "keyword" else (None, None)
            eid = conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id, parent_id,"
                " listing_id, match_type, keyword_text)"
                " VALUES ('amazon_mx', %s, %s, %s, %s, %s, %s) RETURNING id",
                (kind, ext, parent, listing, match, texto),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES (%s, 'ENABLED', now())",
                (eid,),
            )
            return eid

        def producto(sku, con_costo=True):
            pid = conn.execute(
                "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
                (sku, sku),
            ).fetchone()[0]
            if con_costo:
                conn.execute(
                    "INSERT INTO sku_cost (product_id, cost_amount, cost_currency,"
                    " includes_tax, valid_from, ingest_run_id)"
                    " VALUES (%s, 10, 'MXN', false, '2026-01-01', %s)",
                    (pid, run_id),
                )
            return pid

        def listing(pid, asin):
            # listing NO lleva ingest_run_id (regla 8: esquema real abierto
            # tras el fallo de CI del primer intento de este seed).
            return conn.execute(
                "INSERT INTO listing (product_id, platform, external_id)"
                " VALUES (%s, 'amazon_mx', %s) RETURNING id",
                (pid, asin),
            ).fetchone()[0]

        def metrica(entity_id, costo):
            dia = (date.today() - timedelta(days=20)).isoformat()
            conn.execute(
                "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                " observed_at, metric_currency, cost, ingest_run_id)"
                " VALUES (%s, %s, now(), 'MXN', %s, %s)",
                (entity_id, dia, costo, run_id),
            )

        cam = entidad("campaign", "C1")

        # grupo A: 1 product ad con listing+costo, 1 producto -> cubierto_unico
        p1 = producto("SKU-A")
        l1 = listing(p1, "B0AAA")
        ag_a = entidad("ad_group", "A", cam)
        entidad("product_ad", "A1", ag_a, l1)
        kw_a = entidad("keyword", "KA", ag_a)
        metrica(kw_a, 100)

        # grupo B: dos productos distintos -> cubierto_multi_asin
        p2 = producto("SKU-B")
        l2 = listing(p2, "B0BBB")
        ag_b = entidad("ad_group", "B", cam)
        entidad("product_ad", "B1", ag_b, l1)
        entidad("product_ad", "B2", ag_b, l2)
        kw_b = entidad("keyword", "KB", ag_b)
        metrica(kw_b, 300)

        # grupo C: un ad sin listing -> sin_listing
        ag_c = entidad("ad_group", "C", cam)
        entidad("product_ad", "C1", ag_c, None)
        kw_c = entidad("keyword", "KC", ag_c)
        metrica(kw_c, 40)

        # grupo D: listing cuyo producto NO tiene costo vigente -> sin_costo
        p3 = producto("SKU-D", con_costo=False)
        l3 = listing(p3, "B0DDD")
        ag_d = entidad("ad_group", "D", cam)
        entidad("product_ad", "D1", ag_d, l3)
        kw_d = entidad("keyword", "KD", ag_d)
        metrica(kw_d, 25)

        # grupo E: su UNICO product ad esta ARCHIVED -> sin_anuncios_vivos
        # (el bug de la medicion manual: en WHERE, este grupo DESAPARECIA)
        ag_e = entidad("ad_group", "E", cam)
        pa_e = entidad("product_ad", "E1", ag_e, l1)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'ARCHIVED' WHERE ad_entity_id = %s",
            (pa_e,),
        )
        kw_e = entidad("keyword", "KE", ag_e)
        metrica(kw_e, 60)

        # grupo F (amazon_us): cadena RESUELTA pero gasto USD sin NINGUNA
        # tasa en fx_rate -> fx_ok falso, cubierto NO lo cuenta (qwen r2).
        def entidad_us(kind, ext, parent=None, listing=None):
            match, texto = ("EXACT", ext) if kind == "keyword" else (None, None)
            eid = conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id, parent_id,"
                " listing_id, match_type, keyword_text)"
                " VALUES ('amazon_us', %s, %s, %s, %s, %s, %s) RETURNING id",
                (kind, ext, parent, listing, match, texto),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES (%s, 'ENABLED', now())",
                (eid,),
            )
            return eid

        lus = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id)"
            " VALUES (%s, 'amazon_us', 'B0FFF') RETURNING id",
            (p1,),
        ).fetchone()[0]
        cam_us = entidad_us("campaign", "CU")
        ag_f = entidad_us("ad_group", "F", cam_us)
        entidad_us("product_ad", "F1", ag_f, lus)
        kw_f = entidad_us("keyword", "KF", ag_f)
        conn.execute(
            "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
            " observed_at, metric_currency, cost, ingest_run_id)"
            " VALUES (%s, %s, now(), 'USD', 80, %s)",
            (kw_f, (date.today() - timedelta(days=20)).isoformat(), run_id),
        )

        filas = medir_cobertura(conn, dias=30)
        por_estado = {f[2]: f for f in filas if f[0] == "amazon_mx"}
        fila_us = [f for f in filas if f[0] == "amazon_us"]
        assert len(fila_us) == 1
        assert fila_us[0][2] == "cubierto_unico"
        assert fila_us[0][5] == 80, "TODO el gasto USD quedo sin tasa (fx_rate vacia)"
        resumen_us = resumen_por_plataforma(fila_us)[("amazon_us", "USD")]
        assert resumen_us["gasto_cubierto"] == 0, (
            "cadena resuelta pero SIN tasa: no cuenta como cubierto"
        )
        assert resumen_us["gasto_sin_fx"] == 80

        assert por_estado["cubierto_unico"][4] == 100
        assert por_estado["cubierto_multi_asin"][4] == 300
        assert por_estado["sin_listing"][4] == 40
        assert por_estado["sin_costo"][4] == 25
        assert por_estado["sin_anuncios_vivos"][4] == 60, (
            "el grupo con todos sus ads archivados debe CLASIFICAR, no desaparecer"
        )
        assert "grupo_desconocido" not in por_estado

        resumen = resumen_por_plataforma(filas)
        fila = resumen[("amazon_mx", "MXN")]
        assert fila["gasto_total"] == 525
        assert fila["gasto_cubierto"] == 400
        assert round(fila["pct"], 1) == 76.2
        assert round(fila["pct_estricta"], 1) == 19.0  # solo el grupo A (100)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
