"""Ejecutar UNA vez en host goncloud. No escribe fuentes ni configura tareas."""

import base64
import datetime as dt
import json
import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DESTINO = Path("/mnt/data/appdata/orbit/seguimientos/p01-20261105")
CONCILIADOR = RAIZ / "conciliar.py"


def ejecutar():
    os.umask(0o077)
    ahora = dt.datetime.now(dt.UTC)
    if ahora < dt.datetime(2026, 11, 5, 8, 15, tzinfo=dt.UTC):
        print(
            json.dumps(
                {
                    "estado": "PENDIENTE",
                    "capturado_utc": ahora.isoformat(),
                    "motivo": "Cron de 2026-11-05 08:15 UTC todavia futuro",
                }
            )
        )
        return 3
    DESTINO.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Sin bind de accounting: el host toma snapshot RO; el contenedor recibe copia por stdin.
    mounts = json.loads(
        subprocess.check_output(
            ["docker", "inspect", "orbit-app-1", "--format", "{{json .Mounts}}"], text=True
        )
    )
    if any("accounting" in m["Source"] or "accounting" in m["Destination"] for m in mounts):
        raise RuntimeError("Montaje inesperado de accounting en contenedor")
    with tempfile.TemporaryDirectory(prefix="orbit-p01-ro-") as temporal:
        snapshot = Path(temporal) / "accounting.db"
        with (
            closing(
                sqlite3.connect(
                    "file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro",
                    uri=True,
                    timeout=15,
                )
            ) as origen,
            closing(sqlite3.connect(snapshot)) as destino,
        ):
            origen.backup(destino)
        snapshot.chmod(0o600)
        logs = {}
        for clave, nombre in [("costos", "costos.log"), ("meli", "meli_ads.log")]:
            archivo = Path("/mnt/data/appdata/orbit/logs") / nombre
            logs[clave] = (
                "\n".join(archivo.read_text().splitlines()[-100:]) if archivo.exists() else ""
            )
        entrada = {
            "snapshot": base64.b64encode(snapshot.read_bytes()).decode(),
            "conciliador": CONCILIADOR.read_text(),
            "logs": logs,
        }
        resultado = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "orbit-app-1",
                "python",
                "-c",
                (RAIZ / "worker.py").read_text(),
            ],
            input=json.dumps(entrada),
            capture_output=True,
            text=True,
            timeout=180,
        )
        # No persistir stderr ni excepciones crudas del runtime.
        try:
            evidencia = json.loads(resultado.stdout)
        except ValueError:
            evidencia = {
                "estado": "FALLA",
                "motivo": "Worker sin resultado JSON; revisar runtime sin publicar stderr",
            }
        salida = DESTINO / ("resultado-" + ahora.strftime("%Y%m%dT%H%M%SZ") + ".json")
        with salida.open("x") as archivo:
            json.dump(evidencia, archivo, ensure_ascii=False, indent=2)
        print(json.dumps({"estado": evidencia["estado"], "archivo": str(salida)}))
        return 0 if evidencia["estado"] == "OK" and resultado.returncode == 0 else 1


def main():
    try:
        return ejecutar()
    except Exception:
        evidencia = {"estado": "FALLA", "motivo": "Fallo de comprobacion; error crudo omitido"}
        try:
            os.umask(0o077)
            DESTINO.mkdir(parents=True, exist_ok=True, mode=0o700)
            ruta = DESTINO / (
                "fallo-" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
            )
            with ruta.open("x") as archivo:
                json.dump(evidencia, archivo)
            evidencia["archivo"] = str(ruta)
        except OSError:
            evidencia["motivo"] += "; no se pudo guardar evidencia"
        print(json.dumps(evidencia))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
