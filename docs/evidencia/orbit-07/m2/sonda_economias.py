"""ORBIT 07 M2: sonda reproducible de la economia efectiva del producto.

Crea una base PostgreSQL desechable, corre dos ciclos (margen 8 %, fraccion
0.5: meta 4 %) y revalida una pausa economica con la evaluacion del ciclo
ejecutor. Despues vuelve invalido el margen del producto y comprueba que la
revalidacion resuelve el respaldo completo en vez de aplicar el 4 % congelado.

No toca produccion ni Amazon. Uso:

    $env:ORBIT_TEST_DSN='postgresql://.../postgres'
    $env:PYTHONPATH='.;tests'
    uv run --frozen python docs/evidencia/orbit-07/m2/sonda_economias.py
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json

from test_cycle import _config_version, _corre, _run
from test_cycle_target_ciclo import _mundo_familia
from test_productos_io import _db as _db_productos
from test_productos_meta_ciclo import SQL76

from app import apply, apply_cola, productos_io
from app.optimizer import productos as p

REFERENCIA = "ORBIT 07 M2 sobre c4121c5 (M1) / f86ed7dc (AUTO-03)"


@contextlib.contextmanager
def _db(prefijo: str):
    with _db_productos(prefijo) as conn:
        conn.execute(SQL76)
        yield conn


def _settings(conn) -> dict:
    return conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[
        0
    ]


def _mundo(conn) -> dict:
    ids = _mundo_familia(conn, margen_familia=8)
    ids["ag"] = conn.execute(
        "SELECT parent_id FROM ad_entity WHERE id = %s", (ids["kw"],)
    ).fetchone()[0]
    _config_version(
        conn,
        {
            **_settings(conn),
            "ads_producto_meta_amazon_us": "live",
            "ads_producto_economia_amazon_us": "producto_directo_v1",
            "ads_pause_economica": True,
        },
    )
    return ids


def _metrica_economica(conn, run_id, kw) -> None:
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, 'USD', 5000, 10000, 1000, 120, 1, %s)",
        (kw, dt.date(2026, 8, 9), dt.datetime(2026, 8, 21, tzinfo=dt.UTC), run_id),
    )


def _pausa(conn, cycle_id: int, entidad: int) -> dict | None:
    fila = conn.execute(
        "SELECT id, inputs FROM decision WHERE cycle_id = %s AND ad_entity_id = %s"
        " AND kind = 'pause'",
        (cycle_id, entidad),
    ).fetchone()
    if fila is None:
        return None
    inputs = fila[1]
    return {
        "decision_id": fila[0],
        "inputs": inputs,
        "target_procedencia": inputs.get("target_procedencia"),
        "target_acos_pct_usado": inputs.get("target_acos_pct_usado"),
        "target_snapshot": inputs.get("target_snapshot"),
    }


def _evaluaciones(conn, cycle_id: int) -> list[dict]:
    filas = conn.execute(
        "SELECT product_id, politica, meta_pct, huella_alcance, moneda,"
        " ventana_desde, ventana_hasta, entrada, resultado"
        " FROM evaluacion_producto_ciclo WHERE cycle_id = %s ORDER BY product_id",
        (cycle_id,),
    ).fetchall()
    salida = []
    for fila in filas:
        ev = productos_io.evaluacion_de_fila(fila[7], fila[8])
        margen = ev.entrada.margen
        salida.append(
            {
                "product_id": fila[0],
                "politica": fila[1],
                "meta_pct": str(fila[2]) if fila[2] is not None else None,
                "equilibrio_pct": str(margen.margen_neto_pct)
                if getattr(margen, "margen_neto_pct", None) is not None
                else None,
                "motivos": list(ev.motivos),
                "huella_alcance": fila[3],
                "moneda": fila[4],
                "ventana_desde": fila[5].isoformat() if fila[5] else None,
                "ventana_hasta": fila[6].isoformat() if fila[6] else None,
            }
        )
    return salida


def _hojas(conn, cycle_id: int) -> list[dict]:
    return [
        {
            "hoja_id": fila[0],
            "target": str(fila[1]),
            "procedencia": fila[2],
        }
        for fila in conn.execute(
            "SELECT ad_entity_id, target_acos_pct, procedencia FROM target_acos_ciclo"
            " WHERE cycle_id = %s ORDER BY ad_entity_id",
            (cycle_id,),
        )
    ]


def _aplicador(cycle_id: int):
    return type(
        "Aplicador",
        (),
        {"_cliente": lambda self: object(), "cycle_id_ejecutor": cycle_id},
    )()


def _ventas_sin_costo(conn, producto: int, *, dias: int = 40) -> None:
    run = _run(conn)
    inicio = dt.date(2026, 3, 1)
    for i in range(dias):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_us', 'sale', %s, %s, %s, 1, 100, 'USD', %s)",
            (inicio + dt.timedelta(days=i), f"sc-{producto}-{i}", producto, run),
        )
    conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok)"
        " VALUES ('accounting_ledger_events', now(), true)"
    )


def _revalida(conn, cycle_id: int, decision_id: int, kw: int) -> dict:
    apply._estado_de_readback = lambda *_: "ENABLED"
    evidencias: list[dict] = []
    resultado = apply_cola._revalida_pause(
        conn,
        _aplicador(cycle_id),
        "amazon_us",
        apply_cola.FilaCola(1, "pause", kw, None, decision_id, {}, "released"),
        dt.datetime.now(dt.UTC),
        evidencias,
    )
    return {"resultado": resultado, "evidencias": evidencias}


def main() -> dict:
    with _db("orbit_m2_sonda") as conn:
        ids = _mundo(conn)
        _metrica_economica(conn, _run(conn), ids["kw"])

        r1 = _corre(conn)
        assert r1.status == "done", r1.notes
        pausa = _pausa(conn, r1.cycle_id, ids["kw"])
        assert pausa is not None, r1.notes
        ciclo1 = {
            "cycle_id": r1.cycle_id,
            "evaluaciones": _evaluaciones(conn, r1.cycle_id),
            "hojas": _hojas(conn, r1.cycle_id),
            "pausa": {
                "decision_id": pausa["decision_id"],
                "target_procedencia": pausa["target_procedencia"],
                "target_acos_pct_usado": pausa["target_acos_pct_usado"],
                "snapshot": pausa["target_snapshot"],
                "caso_target_acos_pct": (
                    (pausa["inputs"].get("caso") or {}).get("economia") or {}
                ).get("target_acos_pct"),
                "caso_equilibrio_acos_pct": (
                    ((pausa["inputs"].get("caso") or {}).get("economia") or {}).get("plataforma")
                    or {}
                ).get("equilibrio_acos_pct"),
            },
        }

        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        sin_cambio = _revalida(conn, r2.cycle_id, pausa["decision_id"], ids["kw"])
        ciclo2 = {
            "cycle_id": r2.cycle_id,
            "evaluaciones": _evaluaciones(conn, r2.cycle_id),
            "hojas": _hojas(conn, r2.cycle_id),
        }

        producto = conn.execute("SELECT id FROM product WHERE odoo_sku = 'SKU-FAM'").fetchone()[0]
        _ventas_sin_costo(conn, producto)
        r3 = _corre(conn)
        assert r3.status == "done", r3.notes
        sin_elegibilidad = _revalida(conn, r3.cycle_id, pausa["decision_id"], ids["kw"])
        ciclo3 = {
            "cycle_id": r3.cycle_id,
            "evaluaciones": _evaluaciones(conn, r3.cycle_id),
            "hojas": _hojas(conn, r3.cycle_id),
            "margen_producto": conn.execute(
                "SELECT margen_neto_pct FROM v_margen_producto WHERE product_id = %s",
                (producto,),
            ).fetchone()[0],
        }

        return {
            "version": p.POLITICA_ECONOMIA,
            "referencia": REFERENCIA,
            "sonda": "docs/evidencia/orbit-07/m2/sonda_economias.py",
            "caso": {
                "margen_producto_pct": "8",
                "fraccion": "0.5",
                "meta_individual_pct": "4",
                "gasto": "5000 USD",
                "ventas": "10000 USD",
                "pedidos": 1,
                "clics": 120,
            },
            "ciclo_1": ciclo1,
            "ciclo_2": ciclo2,
            "ciclo_3": ciclo3,
            "revalidacion_sin_cambio": sin_cambio,
            "revalidacion_sin_elegibilidad": sin_elegibilidad,
        }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, ensure_ascii=False, default=str))
