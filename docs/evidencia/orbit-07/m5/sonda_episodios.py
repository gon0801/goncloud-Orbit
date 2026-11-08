"""ORBIT 07 M5/M6: sonda reproducible del episodio economico y su decision.

Crea una base PostgreSQL 16 desechable con TODAS las migraciones de
produccion, siembra un cambio de bid confirmado con ventanas economicas
maduras y lee el caso por el camino real (`lee_plataforma`). Despues inserta
un cambio de configuracion dentro de la ventana para mostrar el caso no
comparable. Finalmente evalua la regla M6 sobre un caso sintetico con el
episodio maduro, sin episodio y con un episodio incomparable.

No toca produccion ni Amazon. Uso:

    $env:ORBIT_TEST_DSN='postgresql://.../postgres'
    $env:PYTHONPATH='.;tests'
    uv run --frozen python docs/evidencia/orbit-07/m5/sonda_episodios.py
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from test_orbit07_m5m6 import (
    _cambio_pasado,
    _caso,
    _caso_desde_lecturas,
    _db_completa,
    _efecto_corte,
    _lecturas,
    _mundo_m5,
    _propia_sin_pedidos,
)

from app.optimizer import economia_cambio as regla
from app.optimizer.politica import decide

SALIDA = Path(__file__).with_name("episodios.json")
REFERENCIA = "ORBIT 07 M5/M6 sobre 4e0e78d (M2) / f86ed7dc (AUTO-03)"


def _episodio_json(episodio) -> dict | None:
    if episodio is None:
        return None
    datos = asdict(episodio)
    return json.loads(json.dumps(datos, default=str))


def _decision(caso) -> dict:
    veredicto = decide(caso)
    return {
        "tipo": type(veredicto).__name__,
        "motivo": veredicto.motivo,
        "bid_nuevo": str(getattr(veredicto, "bid_nuevo", "")) or None,
    }


def main() -> dict:
    with _db_completa("orbit_m5m6_sonda") as (conn, _c):
        ids = _mundo_m5(conn)
        util = _caso_desde_lecturas(conn, _lecturas(conn), ids).trayectoria.episodio
        assert util is not None
        evento = dt.datetime.combine(
            util.fecha_cambio + dt.timedelta(days=3), dt.time(12), tzinfo=dt.UTC
        )
        conn.execute(
            "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
            " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
            " ajuste_resto_pct, ajuste_producto_pct, fuera_de_amazon)"
            " VALUES (%s, %s, 100, 'USD', 'AUTO', 0, 0, 0, NULL)",
            (ids["camp"], evento),
        )
        no_comparable = _caso_desde_lecturas(conn, _lecturas(conn), ids).trayectoria.episodio
        assert no_comparable is not None
        cambio = _cambio_pasado()
        base = _caso(
            propia=_propia_sin_pedidos(),
            bid="0.85",
            cambios=(cambio,),
            efecto=_efecto_corte(),
            episodio=None,
        )
        con_util = replace(
            base,
            trayectoria=replace(base.trayectoria, episodio=util),
        )
        con_incomparable = replace(
            base,
            trayectoria=replace(base.trayectoria, episodio=no_comparable),
        )
        juicio_util = regla.juicio_repeticion(
            util, target_acos_pct=Decimal("25"), factor=Decimal("-0.12")
        )
        return {
            "version": util.version,
            "referencia": REFERENCIA,
            "sonda": "docs/evidencia/orbit-07/m5/sonda_episodios.py",
            "cambio": {
                "fecha": cambio.fecha.isoformat(),
                "bid_antes": str(cambio.bid_antes),
                "bid_despues": str(cambio.bid_despues),
                "ventanas": "[x-7, x-1] y [x+1, x+7], sin el dia del cambio",
                "madurez": "D+17 (siete fechas posteriores completas con diez dias de madurez)",
            },
            "episodio_util": _episodio_json(util),
            "episodio_no_comparable": _episodio_json(no_comparable),
            "juicio_con_episodio_util": {
                "veredicto": juicio_util.veredicto,
                "motivo": juicio_util.motivo,
            },
            "decision_sin_episodio": _decision(base),
            "decision_con_episodio_util": _decision(con_util),
            "decision_con_episodio_no_comparable": _decision(con_incomparable),
            "cobertura": {
                "cambios_leidos": 1,
                "episodios_utiles": int(util.estado == "maduro" and util.aplicable),
                "episodios_no_comparables": int(no_comparable.estado == "incomparable"),
                "contribucion_requiere_margen": True,
            },
        }


if __name__ == "__main__":
    datos = main()
    SALIDA.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(datos, indent=2, ensure_ascii=False))
