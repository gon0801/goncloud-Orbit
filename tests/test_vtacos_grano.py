"""v_tacos grano único (ORBIT 06 0.7 — hallazgo de qwen en la review 3.3).

BUG CONFIRMADO EN VIVO (2026-08-31): `ads_metric_observation` guarda el gasto
DOS VECES por campaña con hijos -- una fila kind='campaign' Y otra(s)
kind='keyword'/'product_target' con el MISMO costo. `v_tacos` (0001) sumaba
`v_metric_mature` sin filtrar `kind` -> gasto_ads inflado ~2x.

Este test demuestra el rojo DENTRO de sí mismo: aplica 0001-0004 (sin 0005) y
mide que gasto_ads es la suma doble (keyword + campaign); luego aplica 0005
sobre la MISMA base y mide que gasto_ads es SOLO el costo de la keyword.
"""

from __future__ import annotations

import os
import socket
from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_schema import SQL, SQL2, SQL3, SQL4, SQL5, _hay_postgres_local, _test_dsn

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_v_tacos_deja_de_duplicar_gasto_campaign_keyword():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_tacos_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL2)
        conn.execute(SQL3)
        conn.execute(SQL4)

        run_id = conn.execute(
            "INSERT INTO ingest_run (source) VALUES ('test') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "UPDATE ingest_run SET finished_at = now(), ok = true WHERE id = %s", (run_id,)
        )

        def entidad(kind, ext, parent=None):
            match, texto = ("EXACT", ext) if kind == "keyword" else (None, None)
            eid = conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id, parent_id,"
                " match_type, keyword_text)"
                " VALUES ('amazon_mx', %s, %s, %s, %s, %s) RETURNING id",
                (kind, ext, parent, match, texto),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES (%s, 'ENABLED', now())",
                (eid,),
            )
            return eid

        cam = entidad("campaign", "C-TACOS-1")
        ag = entidad("ad_group", "AG-TACOS-1", cam)
        kw = entidad("keyword", "KW-TACOS-1", ag)

        # Maduro: D-20 (dentro de la ventana D-15 de v_metric_mature), mismo
        # mes para campaign y keyword.
        dia = (date.today() - timedelta(days=20)).isoformat()

        costo_campana = Decimal("500.00")
        costo_keyword = Decimal("500.00")

        def metrica(entity_id, costo):
            conn.execute(
                "INSERT INTO ads_metric_observation (ad_entity_id, metric_date,"
                " observed_at, metric_currency, cost, ingest_run_id)"
                " VALUES (%s, %s, now(), 'MXN', %s, %s)",
                (entity_id, dia, costo, run_id),
            )

        metrica(cam, costo_campana)
        metrica(kw, costo_keyword)

        # date_trunc del mes de `dia`, no de hoy.
        mes_dia = date.fromisoformat(dia).replace(day=1)

        def gasto_ads_mx():
            fila = conn.execute(
                "SELECT gasto_ads FROM v_tacos WHERE platform = 'amazon_mx' AND mes = %s",
                (mes_dia,),
            ).fetchone()
            assert fila is not None, "v_tacos no devolvió fila para amazon_mx en el mes sembrado"
            return fila[0]

        # ROJO: con 0001-0004 (sin 0005), v_tacos suma campaign Y keyword ->
        # doble conteo del mismo gasto.
        gasto_doble = gasto_ads_mx()
        assert gasto_doble == costo_campana + costo_keyword, (
            "el bug no se reprodujo: se esperaba que v_tacos SIN 0005 sumara "
            "el costo de campaign y de keyword (doble conteo)"
        )

        # VERDE: aplicar 0005 sobre la MISMA base (CREATE OR REPLACE VIEW).
        conn.execute(SQL5)

        gasto_unico = gasto_ads_mx()
        assert gasto_unico == costo_keyword, (
            "v_tacos con 0005 debe sumar SOLO kind IN ('keyword', "
            "'product_target'); el gasto de la fila 'campaign' no debe entrar"
        )
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
