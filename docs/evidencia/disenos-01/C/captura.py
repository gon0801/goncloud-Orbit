#!/usr/bin/env python3
"""Capturas de /contribucion a 1440 y 390 px (DISENOS 01, bloque C).

Sirve el HTML guardado con el CSS real y usa Chrome/Edge headless con el
viewport emulado por CDP (390 px reales, no el minimo de la ventana) para dejar
`despues/contribucion-1440.png` y `despues/contribucion-390.png`.

Uso:
    python docs/evidencia/disenos-01/C/captura.py
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[4]
AQUI = Path(__file__).resolve().parent
HTML_DEFAULT = AQUI / "despues" / "contribucion.html"
SALIDA_DEFAULT = AQUI / "despues"
CAPTURAS = ((1440, 3600, "contribucion-1440.png"), (390, 5200, "contribucion-390.png"))


def _navegador() -> str | None:
    elegido = os.environ.get("ORBIT_VERIFY_CHROME", "").strip()
    if elegido and Path(elegido).is_file():
        return elegido
    for nombre in ("google-chrome", "google-chrome-stable", "chromium", "msedge", "chrome"):
        encontrado = shutil.which(nombre)
        if encontrado:
            return encontrado
    for candidato in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ):
        if Path(candidato).is_file():
            return candidato
    return None


def _ws_modulo():
    ruta = AQUI / "mide_390.py"
    spec = importlib.util.spec_from_file_location("orbit_mide_390", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class _Handler(BaseHTTPRequestHandler):
    html: bytes = b""

    def do_GET(self) -> None:  # noqa: N802 - firma de BaseHTTPRequestHandler
        ruta = urllib.parse.urlparse(self.path).path
        if ruta in ("/", "/contribucion"):
            self._responder(200, "text/html; charset=utf-8", self.html)
            return
        if ruta.startswith("/static/"):
            archivo = RAIZ / "app" / ruta.lstrip("/")
            if archivo.is_file():
                tipo = "text/css" if archivo.suffix == ".css" else "application/octet-stream"
                self._responder(200, tipo, archivo.read_bytes())
                return
        self._responder(404, "text/plain; charset=utf-8", b"no encontrado")

    def _responder(self, codigo: int, tipo: str, cuerpo: bytes) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_args) -> None:
        pass


def _capturar(navegador: str, url: str, salida: Path) -> None:
    ws_mod = _ws_modulo()
    perfil = Path(tempfile.mkdtemp(prefix="orbit-captura-"))
    proc = subprocess.Popen(
        [
            navegador,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--remote-debugging-port=0",
            f"--user-data-dir={perfil}",
            "--window-size=1280,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        marca = perfil / "DevToolsActivePort"
        for _ in range(150):
            if marca.is_file():
                break
            if proc.poll() is not None:
                raise SystemExit("el navegador no arranco")
            time.sleep(0.1)
        puerto = int(marca.read_text(encoding="utf-8").splitlines()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/json/list", timeout=5) as r:
            paginas = json.loads(r.read().decode())
        pagina = next(p for p in paginas if p.get("type") == "page")
        ws = urllib.parse.urlparse(pagina["webSocketDebuggerUrl"])
        sock = socket.create_connection((ws.hostname, ws.port), timeout=10)
        llave = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            (
                f"GET {ws.path} HTTP/1.1\r\n"
                f"Host: {ws.hostname}:{ws.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {llave}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        saludo = b""
        while b"\r\n\r\n" not in saludo:
            saludo += ws_mod._ws_recv(sock)
        pendiente = saludo.split(b"\r\n\r\n", 1)[1]
        ident = 0

        def llamar(metodo: str, params: dict | None = None) -> dict:
            nonlocal ident, pendiente
            ident += 1
            mensaje = {"id": ident, "method": metodo}
            if params:
                mensaje["params"] = params
            ws_mod._ws_enviar(sock, json.dumps(mensaje))
            while True:
                opcode, payload, pendiente = ws_mod._ws_leer(sock, pendiente)
                if opcode != 1:
                    continue
                data = json.loads(payload.decode())
                if data.get("id") == ident:
                    return data

        llamar("Page.enable")
        llamar("Runtime.enable")
        for ancho, alto, nombre in CAPTURAS:
            llamar(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": ancho,
                    "height": alto,
                    "deviceScaleFactor": 1,
                    "mobile": ancho < 800,
                },
            )
            llamar("Page.navigate", {"url": url})
            time.sleep(2.0)
            toma = llamar("Page.captureScreenshot", {"format": "png"})
            png = salida / nombre
            png.write_bytes(base64.b64decode(toma["result"]["data"]))
            if not png.is_file() or png.stat().st_size == 0:
                raise SystemExit(f"captura vacia a {ancho} px")
        sock.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(perfil, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", default=str(HTML_DEFAULT))
    parser.add_argument("--salida", default=str(SALIDA_DEFAULT))
    args = parser.parse_args(argv)
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")

    navegador = _navegador()
    if navegador is None:
        print("sin navegador: define ORBIT_VERIFY_CHROME o instala Chrome/Edge")
        return 2

    _Handler.html = Path(args.html).read_bytes()
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{servidor.server_address[1]}"
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    try:
        _capturar(navegador, f"{base}/contribucion", salida)
    finally:
        servidor.shutdown()
        servidor.server_close()
    print(f"capturas en {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
