"""Revision JEV: una corrida que no puede juzgar nada cierra como exito.

Falla: sin llave de TypeSafe (o con el proveedor caido) `jev-senales` sale con
0, no manda aviso, y el vigia de las 11:00 tampoco avisa porque solo mira que
haya `inicio` reciente. En produccion paso el 2026-10-10 y 2026-10-11 (cuatro
corridas `sin_api_key`). Estas pruebas FALLAN por la asercion que describe la
falla: el dueno deberia enterarse.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_jev_senales import (  # noqa: E402
    AHORA,
    ON,
    _acta,
    _ads_de,
    _config,
    _dsn_s4,
    _escritor,
    _grupo_con_roster,
    _Pedido,
    _run,
    _skip_db,
    _ventana,
    db_s4,
)


def _mundo(conn):
    from datetime import timedelta

    from app.ads.structure_plan import huella_anuncios

    _config(conn, ON)
    run = _run(conn, finished_at=AHORA - timedelta(hours=1))
    grupo, _, _, _ = _grupo_con_roster(conn, n=5)
    vivos = sorted(externo for _, externo, _ in _ads_de(conn, grupo))
    _acta(conn, run, "amazon_mx", 1, 5, ((grupo, 5, huella_anuncios(vivos), 0),))
    _ventana(conn, run, "amazon_mx", grupo, "collar plata")


@_skip_db
def test_sin_llave_el_job_sale_0_y_no_avisa(monkeypatch, tmp_path):
    from app import jev_senales, notifica

    avisos: list[str] = []
    monkeypatch.setattr(notifica, "envia_aviso", lambda texto: avisos.append(texto) or True)
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))  # sin typesafe.json
    with db_s4() as conn:
        _mundo(conn)
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_s4())
        monkeypatch.setenv("ORBIT_DSN_JEV", _dsn_s4())
        salida = jev_senales.main(["--aplicar", "--forzar"])
        cierre = conn.execute(
            "SELECT cierre FROM jev_corrida WHERE evento = 'fin'"
        ).fetchone()[0]
    assert cierre == "sin_api_key"
    # Lo que deberia pasar: salida distinta de 0 (cron-avisa avisa) o un aviso.
    assert salida != 0 or avisos, (
        f"corrida '{cierre}' sin juzgar nada: salida={salida}, avisos={avisos}"
    )


@_skip_db
def test_vigia_calla_si_jev_corre_sin_juzgar():
    from app import jev_senales, jev_vigia

    with db_s4() as conn:
        _mundo(conn)
        with _escritor() as escritor:
            cierre = jev_senales.correr(
                conn,
                escritor,
                ahora=AHORA,
                aplicar=True,
                pedir=_Pedido(falla_en={1, 2, 3, 4, 5}),
                api_key="k",
            )
        assert cierre.motivo == "proveedor_caido"
        aviso = jev_vigia.revisa(conn)
    assert aviso is not None, "proveedor caido y el vigia no avisa"
