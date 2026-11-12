#!/usr/bin/env python3
"""Mide que /contribucion no desborde a 390 px (DISENOS 01, bloque C).

Sirve el HTML guardado en `despues/contribucion.html` (o una base viva) y abre un
navegador headless con el viewport emulado de 390 px. Falla si el ancho de la
pagina supera 390, si el body tiene scroll horizontal o si la tabla no scrollea
dentro de su caja.

Uso:
    python docs/evidencia/disenos-01/C/mide_390.py
    python docs/evidencia/disenos-01/C/mide_390.py --base http://127.0.0.1:18098
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import shutil
import socket
import struct
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
ANCHO = 390
ALTO = 2400


def _navegador() -> str | None:
    elegido = os.environ.get("ORBIT_VERIFY_CHROME", "").strip()
    if elegido and Path(elegido).is_file():
        return elegido
    for nombre in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "chrome",
    ):
        encontrado = shutil.which(nombre)
        if encontrado:
            return encontrado
    candidatos = (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    )
    for candidato in candidatos:
        if Path(candidato).is_file():
            return candidato
    return None


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


def _ws_enviar(sock: socket.socket, texto: str) -> None:
    datos = texto.encode()
    mascara = os.urandom(4)
    n = len(datos)
    cabecera = bytearray([0x81])
    if n < 126:
        cabecera.append(0x80 | n)
    elif n < 65536:
        cabecera.append(0x80 | 126)
        cabecera += struct.pack("!H", n)
    else:
        cabecera.append(0x80 | 127)
        cabecera += struct.pack("!Q", n)
    cabecera += mascara
    sock.sendall(bytes(cabecera) + bytes(b ^ mascara[i % 4] for i, b in enumerate(datos)))


def _ws_recv(sock: socket.socket) -> bytes:
    datos = sock.recv(4096)
    if not datos:
        raise EOFError("websocket cerrado por el navegador")
    return datos


def _ws_leer(sock: socket.socket, pendiente: bytes) -> tuple[int, bytes, bytes]:
    buf = pendiente
    while len(buf) < 2:
        buf += _ws_recv(sock)
    largo = buf[1] & 0x7F
    i = 2
    if largo == 126:
        while len(buf) < 4:
            buf += _ws_recv(sock)
        largo = struct.unpack("!H", buf[2:4])[0]
        i = 4
    elif largo == 127:
        while len(buf) < 10:
            buf += _ws_recv(sock)
        largo = struct.unpack("!Q", buf[2:10])[0]
        i = 10
    if buf[1] & 0x80:
        i += 4
    while len(buf) < i + largo:
        buf += _ws_recv(sock)
    return buf[0] & 0x0F, buf[i : i + largo], buf[i + largo :]


def _medir(base: str, navegador: str) -> dict:
    perfil = Path(tempfile.mkdtemp(prefix="orbit-390-"))
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
            saludo += _ws_recv(sock)
        pendiente = saludo.split(b"\r\n\r\n", 1)[1]
        ident = 0

        def llamar(metodo: str, params: dict | None = None) -> dict:
            nonlocal ident, pendiente
            ident += 1
            mensaje = {"id": ident, "method": metodo}
            if params:
                mensaje["params"] = params
            _ws_enviar(sock, json.dumps(mensaje))
            while True:
                opcode, payload, pendiente = _ws_leer(sock, pendiente)
                if opcode != 1:
                    continue
                data = json.loads(payload.decode())
                if data.get("id") == ident:
                    return data

        llamar("Page.enable")
        llamar("Runtime.enable")
        llamar(
            "Emulation.setDeviceMetricsOverride",
            {"width": ANCHO, "height": ALTO, "deviceScaleFactor": 1, "mobile": True},
        )
        llamar("Page.navigate", {"url": f"{base.rstrip('/')}/contribucion"})
        time.sleep(2.0)
        expresion = """(() => {
          const tabla = document.querySelector('.tabla-scroll');
          return {
            ancho: window.innerWidth,
            scrollWidth: document.documentElement.scrollWidth,
            bodyScrollWidth: document.body.scrollWidth,
            bodyClientWidth: document.body.clientWidth,
            bodyScroll: document.body.scrollWidth > document.body.clientWidth + 1,
            tablaScroll: tabla ? tabla.scrollWidth > tabla.clientWidth + 1 : null
          };
        })()"""
        resultado = llamar(
            "Runtime.evaluate",
            {"expression": expresion, "returnByValue": True},
        )
        sock.close()
        return resultado["result"]["result"]["value"]
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
    parser.add_argument("--html", default=str(HTML_DEFAULT), help="HTML guardado para servir")
    parser.add_argument("--base", help="instancia viva; ignora --html")
    args = parser.parse_args(argv)
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")

    navegador = _navegador()
    if navegador is None:
        print("sin navegador: define ORBIT_VERIFY_CHROME o instala Chrome/Edge")
        return 2

    servidor = None
    try:
        if args.base:
            base = args.base
        else:
            html = Path(args.html).read_bytes()
            _Handler.html = html
            servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            threading.Thread(target=servidor.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{servidor.server_address[1]}"
        medidas = _medir(base, navegador)
    finally:
        if servidor is not None:
            servidor.shutdown()
            servidor.server_close()

    print(json.dumps(medidas, ensure_ascii=False))
    ok = (
        medidas.get("ancho") == ANCHO
        and medidas.get("scrollWidth", 0) <= ANCHO
        and medidas.get("bodyScroll") is False
        and medidas.get("tablaScroll") is True
    )
    if not ok:
        print("DESBORDE a 390 px o la tabla no scrollea dentro de su caja")
        return 1
    print("sin desborde a 390 px; la tabla scrollea dentro de su caja")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
