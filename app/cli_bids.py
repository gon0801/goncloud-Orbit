"""Comandos BIDS (BIDS 02, V.4). Registry para no engordar `app/cli.py`.

`avisos-campana` corre los avisos diarios de campana: lee con
ORBIT_DSN_READ, envia por Telegram (via `notifica.avisar_campanas`) y
marca lo enviado con ORBIT_DSN_INGEST. Sale 0 aunque Telegram falle
(lo fallido sale en la siguiente corrida); 2 sin DSN o con args extra.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

_SQL_YA_SALIO = """
SELECT 1 FROM ingest_run
 WHERE source = %s AND platform = %s AND ok AND started_at >= %s AND started_at < %s
"""

_SQL_MARCA = """
INSERT INTO ingest_run (source, platform, ok, started_at, finished_at)
VALUES (%s, %s, true, now(), now())
"""


def _ya_salio(conn, plataforma: str, clase: str, hoy: dt.date) -> bool:
    inicio = dt.datetime.combine(hoy, dt.time(), tzinfo=dt.UTC)
    fin = inicio + dt.timedelta(days=1)
    return (
        conn.execute(_SQL_YA_SALIO, (f"avisos_campana:{clase}", plataforma, inicio, fin)).fetchone()
        is not None
    )


def _parsea_forzar(argv: list[str], comando: str) -> tuple[bool, int | None]:
    """--forzar corre el comando a mano sin las guardas de metricas de ayer;
    cualquier otro token es error de uso (exit 2)."""
    if argv == ["--forzar"]:
        return True, None
    if argv:
        print(f"argumentos desconocidos para '{comando}': {argv}", file=sys.stderr)
        return False, 2
    return False, None


def main_avisos_campana(argv: list[str]) -> int:
    from app import notifica
    from app.avisos_campana import CLASES
    from app.db import connect
    from app.optimizer.bid import PLATAFORMAS_MONEDA

    forzar, error = _parsea_forzar(argv, "avisos-campana")
    if error is not None:
        return error
    dsn_read = os.environ.get("ORBIT_DSN_READ")
    if not dsn_read:
        print("avisos-campana: falta ORBIT_DSN_READ", file=sys.stderr)
        return 2
    dsn_ingest = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn_ingest:
        print("avisos-campana: falta ORBIT_DSN_INGEST", file=sys.stderr)
        return 2
    hoy = dt.datetime.now(dt.UTC).date()
    conn_read = connect(dsn_read)
    try:
        # Guarda de ingesta-01 A.3: este comando lee ads_placement_observation
        # (via pantalla_dinero.lee_ubicaciones) y v_metric_latest: sin las
        # corridas ok de hoy con D-1 no abre nada, no envia nada y no marca.
        from app import metricas_listas

        if not metricas_listas.guarda(
            conn_read, PLATAFORMAS_MONEDA, forzar=forzar, exigir_placements=True
        ):
            print(metricas_listas.TEXTO_ESPERA)
            return 0
        pendientes = {
            (plataforma, clase)
            for plataforma in PLATAFORMAS_MONEDA
            for clase in CLASES
            if not _ya_salio(conn_read, plataforma, clase, hoy)
        }
        if not pendientes:
            return 0
        salieron = notifica.avisar_campanas(conn_read, hoy, solo=pendientes)
    finally:
        conn_read.close()
    conn_mark = connect(dsn_ingest)
    try:
        with conn_mark.transaction():
            for plataforma, clase in sorted(salieron):
                if salieron[(plataforma, clase)]:
                    conn_mark.execute(_SQL_MARCA, (f"avisos_campana:{clase}", plataforma))
    finally:
        conn_mark.close()
    return 0


def main_impulso_vigia(argv: list[str]) -> int:
    """Vigia diario del impulso (BIDS 02 I.3): lee, guarda y actua. Sin
    impulsos vivos sale 0 sin tocar la red. 2 sin DSN o con args extra.
    R09-F: con impulsos vivos y ninguno atendido (todos fallaron o sin
    ingesta) avisa y sale 1, para que el cron no calle un dia perdido."""
    from app import impulso_io
    from app.db import connect

    forzar, error = _parsea_forzar(argv, "impulso-vigia")
    if error is not None:
        return error
    dsn_admin = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn_admin:
        print("impulso-vigia: falta ORBIT_DSN_ADMIN", file=sys.stderr)
        return 2
    conn = connect(dsn_admin)
    try:
        from app import metricas_listas

        filas_vivos = conn.execute(impulso_io._SQL_VIVOS).fetchall()
        vivos = len(filas_vivos)
        plataformas = sorted({fila[1] for fila in filas_vivos})
        conn.commit()
        # Guarda de ingesta-01 A.3: el vigia decide con v_metric_latest; sin
        # las metricas de ayer de TODAS las plataformas con impulso vivo no
        # abre nada (cero impulso_lectura, cero fabrica_lote, cero goals).
        if not metricas_listas.guarda(conn, plataformas, forzar=forzar):
            print(metricas_listas.TEXTO_ESPERA)
            return 0
        hoy = dt.datetime.now(dt.UTC).date()
        acciones = impulso_io.vigila(conn, impulso_io.PausadorFabrica(conn), hoy=hoy)
    finally:
        conn.close()
    for accion in acciones:
        print(
            f"impulso {accion.impulso_id}: {accion.veredicto.estado} -> {accion.hecho}",
            flush=True,
        )
    if vivos > 0 and not any(accion.atendido for accion in acciones):
        print(
            f"impulso-vigia: {vivos} impulso(s) vivo(s) y ninguno atendido;"
            " el dia no se proceso (fallos o sin ingesta)",
            file=sys.stderr,
        )
        return 1
    return 0


COMANDOS = {"avisos-campana": main_avisos_campana, "impulso-vigia": main_impulso_vigia}


def registra(sub) -> None:
    """Agrega los subparsers BIDS al CLI principal."""
    p_avisos = sub.add_parser(
        "avisos-campana", help="avisos diarios de campana por Telegram (BIDS 02 V.4)"
    )
    p_avisos.add_argument(
        "--forzar", action="store_true", help="corre sin las guardas de métricas de ayer"
    )
    p_vigia = sub.add_parser("impulso-vigia", help="vigia diario del impulso (BIDS 02 I.3)")
    p_vigia.add_argument(
        "--forzar", action="store_true", help="corre sin las guardas de métricas de ayer"
    )


def despacha(comando: str, rest: list[str]) -> int:
    """Corre el comando BIDS con sus args."""
    return COMANDOS[comando](rest)
