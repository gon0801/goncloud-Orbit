"""Revalidacion UI externa: ejecutar desde el checkout con ORBIT_UI_DSN local.

Solo crea/elimina thermo_ui_current. Nunca ejecuta POST mutador de la app.
El navegador usa rutas/HTML/assets reales y una frontera HTTP de fallo controlado.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

sys.dont_write_bytecode = True
ROOT = Path(os.environ.get("ORBIT_REVIEW_REPO", os.getcwd())).resolve()
EVIDENCE = Path(__file__).resolve().parent
if not (ROOT / "app/main.py").is_file():
    raise RuntimeError("Ejecutar desde el checkout o definir ORBIT_REVIEW_REPO")
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
DB = "thermo_ui_current"


def metadata():
    from app import main, pantalla_ruido, ui_metricas
    sha = os.environ.get("ORBIT_REVIEW_SHA")
    if not sha:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = {m.__name__: str(Path(m.__file__).resolve()) for m in (main, pantalla_ruido, ui_metricas)}
    for path in paths.values():
        if not Path(path).is_relative_to(ROOT):
            raise RuntimeError("Import ajeno al checkout: " + path)
    return {"sha_reportado": sha, "repo": str(ROOT), "python": sys.version, "imports": paths}


@contextlib.contextmanager
def scenario():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.types.json import Json
    from fastapi.testclient import TestClient
    from app.api import _conexion_lectura
    from app.main import app
    from app.optimizer.caso import POLITICA_BID
    from test_pantalla_ruido import _triple, _ingest, _metrica_pg, _config
    from test_fabrica_migracion import _producto
    from app.ads.reports import _planea_filas, KEYWORDS_CFG, CAMPANAS_CFG

    dsn = os.environ.get("ORBIT_UI_DSN")
    if not dsn:
        raise RuntimeError("Falta ORBIT_UI_DSN; no se consulta configuracion ni secretos")
    opts = conninfo_to_dict(dsn)
    if opts.get("host") not in {"127.0.0.1", "localhost", "orbit-review-current-pg"}:
        raise RuntimeError("Solo PG local desechable autorizado")
    admin = psycopg.connect(dsn, dbname="postgres", autocommit=True)
    # No DROP previo: una colision con otra ejecucion debe fallar.
    admin.execute("CREATE DATABASE thermo_ui_current")
    conn = None
    try:
        conn = psycopg.connect(dsn, dbname=DB, autocommit=True)
        for f in sorted((ROOT / "migrations").glob("*.sql")):
            if f.name.startswith("0011_") or "_reversa_" in f.name:
                continue
            conn.execute(f.read_text())
        fecha = dt.date.today() - dt.timedelta(days=40)
        encendido = dt.datetime.combine(fecha - dt.timedelta(days=5), dt.time(12), dt.UTC)
        _config(conn, {"ads_bid_politica_amazon_mx": POLITICA_BID,
                       "resumen_acos_objetivo": {"amazon_mx": "30"}}, created_at=encendido)
        ingest = _ingest(conn)
        productos = []
        for i, cost in enumerate(("10", None), 1):
            c, g, h = _triple(conn, tag=f"ui-{i}")
            # Parser real: las mismas observaciones parciales son aceptadas para
            # campaign y keyword. PG comprueba el resto de los invariantes.
            for cfg, ent in ((KEYWORDS_CFG, h), (CAMPANAS_CFG, c)):
                filas, skips = _planea_filas(cfg, [{cfg["id_field"]: str(ent),
                    "date": fecha.isoformat(), "cost": cost, "sales30d": "100",
                    "clicks": 3, "purchases30d": 1}], ultimo_cerrado=dt.date.today(),
                    fecha_ini=fecha, fecha_fin=fecha)
                assert len(filas) == 1 and not skips, (filas, skips)
                _metrica_pg(conn, ingest, ent, fecha, costo=filas[0].cost,
                            venta=filas[0].ad_revenue, pedidos=1)
            pid, lid = _producto(conn, sku=f"UI-{i}", asin=f"B0UI00000{i}", seller_sku=f"UI-{i}")
            pa = conn.execute("INSERT INTO ad_entity(platform,kind,external_id,parent_id,listing_id) "
                              "VALUES ('amazon_mx','product_ad',%s,%s,%s) RETURNING id",
                              (f"pa-ui-{i}", g, lid)).fetchone()[0]
            conn.execute("INSERT INTO ad_entity_state(ad_entity_id,status,synced_at) "
                         "VALUES (%s,'ENABLED',now())", (pa,))
            productos.append({"product_id": pid, "listing_id": lid})
        # ARREGLOS 04 W-M: sin una corrida ok del libro de ventas, el plan de
        # retiro no trae anuncios y el caso TN04 de retiro no tendria previa.
        conn.execute(
            "INSERT INTO ingest_run (source, finished_at, ok)"
            " VALUES ('accounting_ledger_events', now(), true)"
        )
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        with TestClient(app) as client:
            yield {"conn": conn, "client": client, "fecha": fecha.isoformat(), "productos": productos}
    finally:
        app.dependency_overrides.pop(_conexion_lectura, None)
        if conn:
            conn.close()
        admin.execute("DROP DATABASE thermo_ui_current WITH (FORCE)")
        admin.close()


def metricas(s):
    client = s["client"]
    ruido = client.get("/api/dashboard/ruido?plataforma=amazon_mx")
    params = {"canal": "amazon_mx", "desde": s["fecha"], "hasta": s["fecha"]}
    resumen = client.get("/api/dashboard/resumen", params=params)
    pagina = client.get("/", params=params)
    assert (ruido.status_code, resumen.status_code, pagina.status_code) == (200, 200, 200)
    from app.ui_metricas import vista_resumen
    datos = resumen.json()
    return {"ruido": ruido.json()["antes_y_despues"],
            "resumen": datos["canales"]["amazon_mx"]["totals"],
            "frase": vista_resumen(datos)["tarjetas"]["amazon_mx"]["frase"],
            "html_con_afirmacion_sin_ventas": "Sin ventas atribuidas frente a la meta" in pagina.text,
            "headers": {k: pagina.headers[k] for k in ("content-security-policy", "cache-control")}}


def browser(s, modo):
    """Chromium real, handlers reales, GET pagina real; POST escritura capturado.

    retiro/plan responde la API real contra PG. impulso/plan usa un payload
    de frontera: no certifica elegibilidad economica ni ejecucion del impulso.
    """
    client = s["client"]
    writes, plans = [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body, typ="application/json", headers=None):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if urlsplit(self.path).path == "/ui.js":
                return self.reply(200, (EVIDENCE / "ui.js").read_bytes(), "application/javascript")
            if self.path.startswith("/productos-sin-clics"):
                r = client.get("/productos-sin-clics?plataforma=amazon_mx")
                body = r.content.replace(b"</body>", b'<script src="/ui.js"></script></body>')
                return self.reply(r.status_code, body, "text/html", {
                    "Content-Security-Policy": r.headers["content-security-policy"]})
            if self.path.startswith("/static/"):
                r = client.get(self.path)
                return self.reply(r.status_code, r.content, r.headers.get("content-type", "text/plain"))
            # Cabecera y polling decorativos: sin IO externo.
            return self.reply(200, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/plan"):
                plans.append({"url": self.path, "body": body})
                if len(plans) > 1:
                    return self.reply(502, {"detail": "Fallo controlado de la nueva consulta"})
                if self.path == "/api/fabrica/retiro/plan":
                    r = client.post(self.path, json=body)
                    return self.reply(r.status_code, r.content)
                lid = body["listing_ids"][0]
                return self.reply(200, {"huella": "a" * 64,
                    "confirmacion_esperada": "LANZAR IMPULSO DE 1 PRODUCTOS", "no_lanzables": [],
                    "planes": [{"listing_id": lid, "seller_sku": "UI-1", "subfamilia": "ui",
                        "tope": {"monto": "100", "moneda": "MXN", "presupuesto_diario": "5"},
                        "keywords_exact": [], "negatives": [], "retiros": [], "bid_por_rol": {}}]})
            writes.append({"url": self.path, "body": body})
            return self.reply(503, {"detail": "Escritura capturada: no ejecutada"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        chrome = os.environ.get("ORBIT_CHROMIUM") or shutil.which("chromium")
        if not chrome:
            raise RuntimeError("Falta chromium: no sustituir por un DOM falso")
        url = f"http://127.0.0.1:{server.server_port}/productos-sin-clics?ui_modo={modo}"
        proc = subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--disable-background-networking", "--no-first-run",
            "--proxy-server=http://127.0.0.1:9", "--disable-extensions",
            "--virtual-time-budget=10000", "--dump-dom", url], capture_output=True, text=True, timeout=40)
        match = re.search(r'<pre id="ui-result">(.*?)</pre>', proc.stdout, re.S)
        if not match:
            raise RuntimeError("No termino el navegador: " + proc.stderr[-1000:] + proc.stdout[-1000:])
        dom = json.loads(html.unescape(match.group(1)))
        if "error" in dom:
            raise RuntimeError(dom["error"])
        return {"modo": modo, "dom": dom, "planes": plans, "escrituras_capturadas": writes}
    finally:
        server.shutdown()
        server.server_close()


def main():
    print(json.dumps(metadata(), ensure_ascii=False), flush=True)
    with scenario() as s:
        print("METRICAS " + json.dumps(metricas(s), ensure_ascii=False), flush=True)
        for modo in ("retiro", "impulso"):
            print("BROWSER " + json.dumps(browser(s, modo), ensure_ascii=False), flush=True)
    print("Solo thermo_ui_current creada y eliminada; ningun POST mutador de la app ejecutado.")


if __name__ == "__main__":
    main()
