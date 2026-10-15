#!/usr/bin/env python3
"""Comprueba F.3 en local: una oferta FBM de MX y una de US producen
escenario disponible con origen y fees partidos."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "tests"))

from test_precio02_escenario_fbm import (  # noqa: E402
    NOW,
    VALORACION,
    _db,
    _fx,
    _sembrar_fbm_mx,
    _sembrar_fbm_us,
)

from app.estimacion_repository import sembrar_escenario_desde_refs  # noqa: E402

with _db() as conn:
    _sembrar_fbm_mx(conn)
    _fx(conn, dia=VALORACION)
    _sembrar_fbm_us(conn)
    mx = sembrar_escenario_desde_refs(
        conn,
        listing_id=conn.execute("SELECT id FROM listing WHERE seller_sku = 'SS-F3-FBM'").fetchone()[
            0
        ],
        oferta_observation_id=conn.execute(
            "SELECT id FROM estimacion_oferta_observation WHERE source_event_id = 'evt-oferta-fbm'"
        ).fetchone()[0],
        fee_observation_id=conn.execute(
            "SELECT id FROM estimacion_fee_observation WHERE source_event_id = 'evt-fee-fbm'"
        ).fetchone()[0],
        valoracion_date=VALORACION,
        observed_at=NOW,
    )
    us = sembrar_escenario_desde_refs(
        conn,
        listing_id=conn.execute("SELECT id FROM listing WHERE seller_sku = 'SS-F3-US'").fetchone()[
            0
        ],
        oferta_observation_id=conn.execute(
            "SELECT id FROM estimacion_oferta_observation WHERE source_event_id = 'evt-oferta-us'"
        ).fetchone()[0],
        fee_observation_id=conn.execute(
            "SELECT id FROM estimacion_fee_observation WHERE source_event_id = 'evt-fee-us'"
        ).fetchone()[0],
        valoracion_date=VALORACION,
        observed_at=NOW,
    )
    for nombre, esc in (("MX FBM", mx), ("US FBM", us)):
        print(
            nombre,
            {
                "estado": esc.estado,
                "origen": esc.logistica_origen,
                "variable": str(esc.fee_variable),
                "fijo": str(esc.fee_fijo),
                "cotizable": esc.fee_cotizable,
            },
        )
    assert mx.estado == "disponible", mx.estado
    assert us.estado == "disponible", us.estado
    print("OK: MX FBM y US FBM disponibles")
