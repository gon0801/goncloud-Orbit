"""Comprobacion RO dentro de orbit-app-1; entrada y salida JSON por stdin/stdout."""

import base64
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import types
from pathlib import Path

from app.db import connect
from app.meli.ads_ingest import _huella_de_marca, _huella_snapshot, _marca_de_as_of
from app.meli.ads_source import leer_origen
from app.redaction import redact_dsn, scrub


def main():
    redact_dsn(os.environ["ORBIT_DSN_READ"])
    datos = json.load(sys.stdin)
    modulo = types.ModuleType("conciliador")
    exec(compile(datos["conciliador"], "conciliar.py", "exec"), modulo.__dict__)
    with tempfile.TemporaryDirectory(prefix="orbit-p01-ro-") as temporal:
        snapshot = Path(temporal) / "accounting.db"
        snapshot.write_bytes(base64.b64decode(datos["snapshot"]))
        snapshot.chmod(0o600)
        fuente = leer_origen(snapshot)
        origen, dias, errores = modulo.carga_origen(str(snapshot))
        with connect(
            os.environ["ORBIT_DSN_READ"],
            connect_timeout=15,
            options="-c statement_timeout=15000 -c default_transaction_read_only=on",
        ) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            sesion = conn.execute(
                "SELECT current_user,current_setting('transaction_read_only')"
            ).fetchone()
            orbit, errores_orbit = modulo.carga_orbit(conn)
            runs = conn.execute(
                "SELECT id,started_at,finished_at,ok,skip_reason,rows_written,rows_skipped "
                "FROM ingest_run WHERE source='accounting_meli_ads_campaigns' "
                "ORDER BY started_at"
            ).fetchall()
        desde, hasta = dt.date(2026, 4, 16), dt.date(2026, 11, 4)
        with contextlib.redirect_stdout(io.StringIO()) as salida:
            diferencias = (
                errores + errores_orbit + modulo.compara(origen, dias, orbit, desde, hasta)
            )
        huella = _huella_snapshot(fuente.filas)
        cron = dt.datetime(2026, 11, 5, 8, 15, tzinfo=dt.UTC)
        nuevas = [r for r in runs if r[1] >= cron]
        if not nuevas or any(r[3] is not True or r[2] is None for r in nuevas):
            diferencias.append("Falta corrida postcron exitosa o existe una fallida/incompleta")
        aceptadas = [
            r
            for r in nuevas
            if r[3] is True
            and _huella_de_marca(r[4]) == huella
            and fuente.as_of is not None
            and _marca_de_as_of(r[4]) == int(fuente.as_of.timestamp())
        ]
        if not aceptadas:
            diferencias.append(
                "Snapshot actual no coincide en huella Y as-of con aceptacion postcron"
            )
        if fuente.rechazos or not fuente.filas:
            diferencias.append("Fuente vacia o con rechazos")
        if sesion != ("orbit_read", "on"):
            diferencias.append("Sesion distinta de orbit_read/read-only")
        logs = {k: scrub(v) for k, v in datos["logs"].items()}
        if "2026-11-05 meli-ads rc=0 " not in logs["costos"] or not logs["meli"].strip():
            diferencias.append("Falta rc=0 postcron en costos.log o meli_ads.log vacio")
        print(
            json.dumps(
                {
                    "estado": "FALLA" if diferencias else "OK",
                    "capturado_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "sesion": sesion,
                    "huella": huella,
                    "as_of": fuente.as_of,
                    "runs": runs,
                    "diferencias": diferencias,
                    "conciliacion": salida.getvalue(),
                    "logs": logs,
                },
                default=str,
            )
        )


try:
    main()
except Exception as exc:
    print(json.dumps({"estado": "FALLA", "error": scrub(str(exc))}))
    raise SystemExit(2) from None
