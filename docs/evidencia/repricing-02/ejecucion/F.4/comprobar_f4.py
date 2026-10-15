#!/usr/bin/env python3
"""Comprueba F.4 en local: historia de una unidad FBM y sonda dry-run.

La sonda corre su `main` real con mercado falso (la red la pone
`precio_mercados.abrir` en produccion): el dry-run imprime el plan y no
escribe, el go mueve un centavo y revierte.
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))

import test_precio_sonda as sonda_fx  # noqa: E402
from test_precio02_mercado import (  # noqa: E402
    _ads,
    _atribuir,
    _db02,
    _estado_obs,
    _hoy,
    _inventario,
    _listing,
    _producto,
    _run_ledger,
    _unidad_f4,
    _venta,
)

from app.spapi.precio_mercado import historia_unidad  # noqa: E402
from tools.precio_sonda import main as sonda_main  # noqa: E402

with _db02() as conn:
    pid = _producto(conn, sku="ODOO-F4-COMP")
    lid = _listing(conn, pid, ext="B0F4COMP001", sku="SS-F4-COMP")
    hoy = _hoy(conn)
    d1 = hoy - timedelta(days=2)
    d2 = hoy - timedelta(days=1)
    _estado_obs(
        conn, sku="SS-F4-COMP", observed=datetime(d1.year, d1.month, d1.day, 12, tzinfo=UTC)
    )
    _estado_obs(
        conn,
        sku="SS-F4-COMP",
        status="DISCONTINUED",
        observed=datetime(d2.year, d2.month, d2.day, 12, tzinfo=UTC),
    )
    _inventario(conn, sku="SS-F4-COMP", dia=d1, qty=0)
    run = _run_ledger(conn)
    _atribuir(
        conn,
        evento_id=_venta(conn, pid=pid, dia=d1, qty=3, evento="evt-f4-comp", run=run),
        lid=lid,
        run=run,
    )
    _ads(conn, asin="B0F4COMP001", sku="SS-F4-COMP", dia=d1, impresiones=200, clics=9)
    historia = historia_unidad(
        conn,
        unidad=_unidad_f4(pid, lid, sku="SS-F4-COMP", ext="B0F4COMP001"),
        canal="fbm",
        hoy=hoy,
    )
    for d in historia.dias:
        print(
            f"dia={d.dia} unidades={d.unidades} disponibilidad={d.disponibilidad}"
            f" exposiciones={d.exposiciones} clics={d.clics}"
        )
    print(f"primera_venta={historia.primera_venta} sin_atribuir={historia.sin_atribuir}")
    assert [d.disponibilidad for d in historia.dias] == ["disponible", "no_disponible"]

with sonda_fx._db_sonda() as (conn2, dsn2):
    lid2 = sonda_fx._listing(conn2)
    os.environ["ORBIT_DSN_DECIDE"] = dsn2
    mercado = sonda_fx._MercadoSondaFalso()
    rc = sonda_main(["--platform", "amazon_mx", "--listing-id", str(lid2)], mercado=mercado)
    print(f"dry-run rc={rc} escrituras={mercado.escrituras}")
    assert rc == 0 and mercado.escrituras == []
    rc = sonda_main(
        [
            "--platform",
            "amazon_mx",
            "--listing-id",
            str(lid2),
            "--acepto-mutacion-real",
            "--go",
            "comp-f4",
        ],
        mercado=mercado,
    )
    print(f"go rc={rc} escrituras={[(str(v), m) for v, m, _s in mercado.escrituras]}")
    assert rc == 0
    assert [v for v, _m, _s in mercado.escrituras] == [Decimal("116.01"), Decimal("116.00")]
print("OK: historia FBM y sonda (dry-run + go con reversa)")
