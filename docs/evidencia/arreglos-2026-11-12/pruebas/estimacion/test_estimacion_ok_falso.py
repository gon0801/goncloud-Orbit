"""Revision ESTIMACION: la actualizacion de estimaciones dice "ok" aunque no
haya podido cotizar NADA.

Falla: si SP-API Product Fees rechaza todas las cotizaciones (403, cuota,
credencial vencida) o el token de MeLi no carga, `ejecutar_ingesta` sella
`ingest_run.ok = true` y `main` sale 0. `cron-avisa` solo avisa con salida
distinta de 0, asi que nadie se entera; el motor de precios deja de evaluar
esas publicaciones y el unico aviso llega a los 3 dias
(`precio_aviso_dias_sin_evaluar`). Estas pruebas FALLAN por la asercion que
describe la falla.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from test_estimacion_fees import (  # noqa: E402
    FETCH,
    NOW,
    _skip_db,
    _snapshot_bridge,
    _token_response,
    db_fees,
    make_client,
)

from app.estimacion_ingest import ejecutar_ingesta  # noqa: E402


def _dos_listings(conn):
    for i in (1, 2):
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
            (f"P{i}", f"P{i}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', %s, %s)",
            (pid, f"B0EST0{i}", f"SS-MX-{i}"),
        )
    conn.commit()


@_skip_db
def test_todas_las_cotizaciones_fallan_y_la_corrida_queda_ok(tmp_path):
    snap = _snapshot_bridge(
        tmp_path / "bridge.db",
        [
            ("SS-MX-1", "B0EST01", "A1AM78C64UM0Y8", "amazon_mx", 116.0, "AMAZON_NA",
             FETCH.isoformat()),
            ("SS-MX-2", "B0EST02", "A1AM78C64UM0Y8", "amazon_mx", 120.0, "AMAZON_NA",
             FETCH.isoformat()),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        return httpx.Response(403, json={"errors": []})  # SP-API rechaza TODO

    with db_fees() as conn:
        _dos_listings(conn)
        r = ejecutar_ingesta(conn, ruta_sqlite=snap, now_utc=NOW, client=make_client(handler))
        conn.commit()
        disponibles = conn.execute(
            "SELECT count(*) FROM estimacion_escenario WHERE estado = 'disponible'"
        ).fetchone()[0]

    assert r.fees_error == 2 and r.fees_nuevos == 2  # las dos cotizaciones fallaron
    assert disponibles == 0  # ninguna publicacion quedo evaluable
    # Lo que deberia pasar: 0 de 2 cotizadas no es una corrida sana.
    assert r.ok is False, "100% de cotizaciones en error y la corrida se sella ok=true"
