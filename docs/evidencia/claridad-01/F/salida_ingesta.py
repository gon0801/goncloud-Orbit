"""CLARIDAD 01 F: la ingesta de estimacion desplegada sale 1 y nombra la plataforma cuando la copia
del bridge trae una oferta de Amazon de mas de 6 h, y sale 0 cuando todas estan frescas.

Corre el camino real completo: `app.estimacion_ingest.main` -> `ejecutar_ingesta` ->
`leer_ofertas_bridge` sobre una SQLite temporal con `amazon_listing_prices` ->
`plataformas_sin_oferta_fresca` -> la linea y la salida de `main`. Solo se sustituyen las fronteras
externas: la conexion a Postgres (una falsa que entrega dos listings, uno de MX y uno de US, y
rechaza cualquier otra sentencia, asi cada publicacion cae en su skip aislado y nada se escribe),
el cliente de Product Fees y las credenciales de MeLi. No lee ni escribe la base, el bridge ni
Amazon. Las horas son relativas al reloj real: MX hace 3 min, US vieja hace 6 h 33 min (lo que
vio la corrida de las 13:10 del 9-nov) y US fresca hace 2 min.
Se pasa por stdin: `docker exec -i orbit-app-1 python - < salida_ingesta.py`. Sale 0 solo si las
dos corridas se distinguen.
"""

import contextlib
import io
import os
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import app.estimacion_ingest as ei

LISTINGS = [(1, "amazon_mx", "B0MX", "SS-MX"), (2, "amazon_us", "B0US", "SS-US")]


class _Cursor:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None


class _ConexionSinBase:
    """Entrega los listings y el id de corrida; cualquier otra sentencia se rechaza."""

    def __init__(self):
        self.rechazadas = 0

    def transaction(self):
        return contextlib.nullcontext()

    def execute(self, sql, _params=None):
        if sql.startswith("SELECT id, platform, external_id, seller_sku"):
            return _Cursor(LISTINGS)
        if sql == ei._SQL_ABRIR_RUN:
            return _Cursor([(0,)])
        if sql == ei._SQL_SELLAR_RUN:
            return _Cursor([])
        self.rechazadas += 1
        raise RuntimeError("sonda: sin escritura")

    def close(self):
        pass


class _SinFees:
    def __getattr__(self, nombre):
        raise RuntimeError(f"sonda: sin Product Fees ({nombre})")


def _sin_meli():
    raise OSError("sin credenciales en la prueba")


def _copia(ruta: Path, edad_us: timedelta) -> Path:
    ahora = datetime.now(UTC)
    con = sqlite3.connect(ruta)
    con.execute(
        "CREATE TABLE amazon_listing_prices (seller_sku TEXT, asin TEXT, marketplace_id TEXT,"
        " marketplace_name TEXT, price REAL, fulfillment_channel TEXT, fetched_at TEXT)"
    )
    con.executemany(
        "INSERT INTO amazon_listing_prices VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("SS-MX", "B0MX", "A1AM78C64UM0Y8", "amazon_mx", 116.0, "DEFAULT",
             (ahora - timedelta(minutes=3)).isoformat()),
            ("SS-US", "B0US", "ATVPDKIKX0DER", "amazon_us", 19.99, "DEFAULT",
             (ahora - edad_us).isoformat()),
        ],
    )  # fmt: skip
    con.commit()
    con.close()
    return ruta


def corre(ruta: Path):
    conexion = _ConexionSinBase()
    ei.connect = lambda _dsn: conexion
    ei.ProductFeesClient = _SinFees
    ei.MeliCredentials = type("SinMeli", (), {"from_secrets_dir": staticmethod(_sin_meli)})
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
        rc = ei.main(["--sqlite", str(ruta)])
    return rc, salida.getvalue(), conexion.rechazadas


os.environ["ORBIT_DSN_INGEST"] = "sin-uso"
with tempfile.TemporaryDirectory(prefix="claridad-f-") as tmp:
    rc_vieja, texto_vieja, rech_vieja = corre(
        _copia(Path(tmp) / "vieja.db", timedelta(hours=6, minutes=33))
    )
    rc_fresca, texto_fresca, rech_fresca = corre(
        _copia(Path(tmp) / "fresca.db", timedelta(minutes=2))
    )
print(f"copia con US de hace 6 h 33 min: rc={rc_vieja} (sentencias rechazadas: {rech_vieja})")
print(f"copia con US de hace 2 min: rc={rc_fresca} (sentencias rechazadas: {rech_fresca})")
ok = (
    rc_vieja == 1
    and "sin oferta fresca: amazon_us (la mas nueva del bridge: " in texto_vieja
    and "sin oferta fresca: amazon_mx" not in texto_vieja
    and rc_fresca == 0
    and "sin oferta fresca" not in texto_fresca
)
if not ok:
    print(texto_vieja + texto_fresca)
sys.exit(0 if ok else 1)
