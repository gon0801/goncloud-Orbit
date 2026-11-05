"""Conciliacion RO reproducible; secretos solo en memoria, temporales propios."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

RAIZ = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RAIZ))
from app.db import connect  # noqa: E402 - arranque desde la ruta de evidencia
from app.meli.ads_ingest import _huella_snapshot  # noqa: E402
from app.meli.ads_source import leer_origen  # noqa: E402
from app.redaction import redact_dsn, scrub  # noqa: E402

SALIDA = Path(__file__).resolve().parent
CONCILIADOR = (
    RAIZ
    / "docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-10266d91/scripts/conciliar.py"
)
REMOTO = """import sqlite3,tempfile,os
fd,p=tempfile.mkstemp(prefix="orbit-qw-conciliacion-",suffix=".db");os.close(fd)
try:
 s=sqlite3.connect("file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro",uri=True)
 d=sqlite3.connect(p);s.backup(d);d.close();s.close();print(p)
except BaseException:
 os.unlink(p);raise
"""


def ssh(*args, **kwargs):
    return subprocess.run(
        ["ssh", "goncloud", *args], check=True, capture_output=True, text=True, **kwargs
    ).stdout


def main():
    remoto = None
    tunel = None
    try:
        remoto = ssh("python3 -", input=REMOTO).strip()
        if not remoto.startswith("/tmp/orbit-qw-conciliacion-") or not remoto.endswith(".db"):
            raise ValueError("ruta temporal remota inesperada")
        with tempfile.TemporaryDirectory(prefix="orbit-qw-conciliacion-") as temporal:
            snapshot = Path(temporal) / "accounting.db"
            subprocess.run(["scp", "-q", f"goncloud:{remoto}", str(snapshot)], check=True)
            snapshot.chmod(0o600)
            with socket.socket() as reserva:
                reserva.bind(("127.0.0.1", 0))
                puerto = reserva.getsockname()[1]
            tunel = subprocess.Popen(
                [
                    "ssh",
                    "-N",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-L",
                    f"{puerto}:127.0.0.1:5432",
                    "goncloud",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(50):
                try:
                    with socket.create_connection(("127.0.0.1", puerto), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            dsn = ssh("docker exec orbit-app-1 printenv ORBIT_DSN_READ").strip()
            redact_dsn(dsn)
            partes = urlsplit(dsn)
            auth = partes.netloc.rsplit("@", 1)[0]
            dsn = urlunsplit(
                (
                    partes.scheme,
                    f"{auth}@127.0.0.1:{puerto}",
                    partes.path,
                    partes.query,
                    partes.fragment,
                )
            )
            redact_dsn(dsn)
            spec = importlib.util.spec_from_file_location("conciliador_vigente", CONCILIADOR)
            modulo = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modulo)
            desde = dt.date(2026, 4, 16)
            hasta = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
            origen, dias, errores = modulo.carga_origen(str(snapshot))
            fuente = leer_origen(snapshot)
            with connect(dsn, connect_timeout=15, options="-c statement_timeout=15000") as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                sesion = conn.execute(
                    "SELECT current_user, current_setting('transaction_read_only'), now()"
                ).fetchone()
                orbit, errores_orbit = modulo.carga_orbit(conn)
                runs = conn.execute(
                    "SELECT id, source, started_at, finished_at, rows_written, "
                    "rows_skipped, skip_reason, ok "
                    "FROM ingest_run WHERE source='accounting_meli_ads_campaigns' "
                    "ORDER BY started_at"
                ).fetchall()
                versiones = conn.execute(
                    "SELECT e.external_id, l.metric_date, l.source_report_id, l.orders "
                    "FROM v_metric_latest l JOIN ad_entity e ON e.id=l.ad_entity_id "
                    "WHERE e.platform='meli' AND e.kind='campaign' "
                    "ORDER BY l.metric_date,e.external_id"
                ).fetchall()
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                diferencias = (
                    errores + errores_orbit + modulo.compara(origen, dias, orbit, desde, hasta)
                )
                print(f"fecha de conciliacion: {dt.datetime.now(dt.UTC).isoformat()}")
                print(f"ventana UTC: {desde}..{hasta}")
                for error in diferencias:
                    print(error)
                print("CONCILIACION FALLA" if diferencias else "CONCILIACION OK")
            (SALIDA / "resultado.txt").write_text(salida.getvalue())

            def serializa(valor):
                return str(valor)

            detalle = {
                "capturado_utc": dt.datetime.now(dt.UTC).isoformat(),
                "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                "huella_canonica_fuente": _huella_snapshot(fuente.filas),
                "as_of_fuente": fuente.as_of,
                "rechazos_fuente": dict(fuente.rechazos),
                "ventana_utc": [desde, hasta],
                "sesion_orbit": sesion,
                "ingest_runs": runs,
                "versiones_orbit": versiones,
                "filas_origen": [[*k, *v] for k, v in sorted(origen.items())],
                "filas_orbit": [[*k, *v] for k, v in sorted(orbit.items())],
                "control_diario_fuente": sorted(dias.items()),
                "dias_orbit": sorted(modulo._agrega_dias(orbit).items()),
                "diferencias": diferencias,
            }
            (SALIDA / "datos.json").write_text(
                json.dumps(detalle, default=serializa, ensure_ascii=False, indent=2) + "\n"
            )
            print(salida.getvalue())
            print("Huella canonica fuente:", detalle["huella_canonica_fuente"])
            print("as_of fuente:", fuente.as_of)
            print("Corridas:", json.dumps(runs, default=str))
            return int(bool(diferencias))
    except Exception as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 2
    finally:
        try:
            if tunel is not None:
                tunel.terminate()
                try:
                    tunel.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    tunel.kill()
                    tunel.wait(timeout=10)
        finally:
            if (
                remoto is not None
                and remoto.startswith("/tmp/orbit-qw-conciliacion-")
                and remoto.endswith(".db")
            ):
                ssh(
                    "python3 -",
                    input=(
                        f"import os\np={remoto!r}\n"
                        'for x in (p,p+"-wal",p+"-shm"):\n'
                        " try: os.unlink(x)\n except FileNotFoundError: pass\n"
                    ),
                )


if __name__ == "__main__":
    raise SystemExit(main())
