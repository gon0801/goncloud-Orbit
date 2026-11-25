"""Caso adverso del formulario de Ajustes (ARREGLOS 05 I).

Abre /settings en Edge/Chromium real (CDP, el mismo navegador de orbit-verify),
teclea `1,000` en la cuota de bids y comprueba que la pantalla muestra el error
visible, que NO hay POST y que la base no gana ninguna config_version. Despues
teclea una cuota valida y confirma que el formulario si intenta el POST: la
validacion no bloquea lo que el backend puede guardar.

Uso (dentro del venv que tiene psycopg):
  python docs/evidencia/arreglos-05/I/adverso.py \
    --base http://127.0.0.1:18010 --db orbit_verify_i_cuotas_win
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg

NAV = os.environ.get("ORBIT_CHROMIUM", r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
DSN = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit@127.0.0.1:55449/postgres")


class Cdp:
    def __init__(self, navegador: str, url: str, perfil: Path, ancho: int = 1280, alto: int = 900):
        self.perfil = perfil
        self.proc = subprocess.Popen(
            [
                navegador,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--remote-debugging-port=0",
                f"--user-data-dir={perfil}",
                f"--window-size={ancho},{alto}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.sock = None
        self.pendiente = b""
        self.ident = 0
        self._conectar(url)

    def _conectar(self, url: str) -> None:
        marca = self.perfil / "DevToolsActivePort"
        fin = time.time() + 15
        while not marca.is_file():
            if self.proc.poll() is not None or time.time() > fin:
                raise SystemExit("devtools no arranco")
            time.sleep(0.1)
        puerto = int(marca.read_text(encoding="utf-8").splitlines()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/json/list", timeout=5) as resp:
            paginas = json.loads(resp.read().decode())
        pagina = next(p for p in paginas if p.get("type") == "page")
        ws = urllib.parse.urlparse(pagina["webSocketDebuggerUrl"])
        self.sock = socket.create_connection((ws.hostname, ws.port), timeout=10)
        llave = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {ws.path} HTTP/1.1\r\nHost: {ws.hostname}:{ws.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {llave}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        saludo = b""
        while b"\r\n\r\n" not in saludo:
            saludo += self._recv()
        self.pendiente = saludo.split(b"\r\n\r\n", 1)[1]
        self.llamar("Page.enable")
        self.llamar("Runtime.enable")
        self.llamar("Page.navigate", {"url": url})
        time.sleep(1.5)

    def _recv(self) -> bytes:
        datos = self.sock.recv(4096)
        if not datos:
            raise EOFError("websocket cerrado")
        return datos

    def _enviar(self, texto: str) -> None:
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
        self.sock.sendall(bytes(cabecera) + bytes(b ^ mascara[i % 4] for i, b in enumerate(datos)))

    def _leer(self) -> tuple[int, bytes, bytes]:
        buf = self.pendiente
        while len(buf) < 2:
            buf += self._recv()
        largo = buf[1] & 0x7F
        i = 2
        if largo == 126:
            while len(buf) < 4:
                buf += self._recv()
            largo = struct.unpack("!H", buf[2:4])[0]
            i = 4
        elif largo == 127:
            while len(buf) < 10:
                buf += self._recv()
            largo = struct.unpack("!Q", buf[2:10])[0]
            i = 10
        if buf[1] & 0x80:
            i += 4
        while len(buf) < i + largo:
            buf += self._recv()
        return buf[0] & 0x0F, buf[i : i + largo], buf[i + largo :]

    def llamar(self, metodo: str, params: dict | None = None) -> dict:
        self.ident += 1
        mensaje = {"id": self.ident, "method": metodo}
        if params:
            mensaje["params"] = params
        self._enviar(json.dumps(mensaje))
        while True:
            opcode, payload, sobrante = self._leer()
            self.pendiente = sobrante
            if opcode != 1:
                continue
            data = json.loads(payload.decode())
            if data.get("id") == self.ident:
                return data

    def evaluar(self, expresion: str):
        salida = self.llamar(
            "Runtime.evaluate", {"expression": expresion, "returnByValue": True, "awaitPromise": True}
        )
        return salida["result"]["result"].get("value")

    def teclear(self, selector: str, texto: str) -> None:
        self.evaluar(f"(() => {{ const e=document.querySelector({json.dumps(selector)}); e.focus(); e.select && e.select(); }})()")
        self.llamar("Input.insertText", {"text": texto})
        time.sleep(0.2)

    def cerrar(self) -> None:
        if self.sock is not None:
            self.sock.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def cuentas(db: str) -> int:
    with psycopg.connect(DSN, dbname=db, autocommit=True) as conn:
        return conn.execute("SELECT count(*) FROM config_version").fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18010")
    ap.add_argument("--db", required=True)
    ap.add_argument("--salida", default="")
    args = ap.parse_args()

    perfil = Path(os.environ.get("TEMP", "/tmp")) / f"orbit-i-adverso-{os.getpid()}"
    shutil.rmtree(perfil, ignore_errors=True)
    perfil.mkdir(parents=True, exist_ok=True)
    navegador = Cdp(NAV, args.base + "/settings", perfil)
    checks: dict[str, object] = {}
    try:
        forma = 'form[data-settings-config="amazon_us"]'
        checks["pagina_settings"] = bool(
            navegador.evaluar("!!document.querySelector('body[data-pantalla=\"settings\"]')")
        )
        checks["cap_es_texto"] = navegador.evaluar(
            f"document.querySelector({json.dumps(forma + ' input[name=cap_bid]')}).type"
        ) == "text"

        navegador.evaluar(
            f"""(() => {{
              const forma = document.querySelector({json.dumps(forma)});
              window.__posts = [];
              const original = window.fetch;
              window.fetch = function (url, opciones) {{
                window.__posts.push({{url: String(url), method: (opciones && opciones.method) || 'GET'}});
                return original.apply(this, arguments);
              }};
              window.__marca = 'sin-recarga';
              forma.querySelector('input[name=settings-token]') ||
                document.getElementById('settings-token');
              return true;
            }})()"""
        )
        antes = cuentas(args.db)

        navegador.teclear(f'{forma} input[name="target_manual_pct"]', "21")
        navegador.teclear(f'{forma} input[name="cap_bid"]', "1,000")
        checks["cap_conserva_texto"] = (
            navegador.evaluar(f"document.querySelector({json.dumps(forma + ' input[name=cap_bid]')}).value")
            == "1,000"
        )
        navegador.evaluar(
            f"document.querySelector({json.dumps(forma + ' button[type=submit]')}).click()"
        )
        time.sleep(1.0)

        estado = navegador.evaluar(
            f"document.querySelector({json.dumps(forma + ' [data-estado]')}).textContent"
        )
        posts = navegador.evaluar("window.__posts") or []
        marca = navegador.evaluar("window.__marca")
        despues = cuentas(args.db)
        checks["error_visible"] = (
            isinstance(estado, str)
            and estado.startswith("Error:")
            and "cuota de bid" in estado
            and "1,000" in estado
        )
        checks["cero_post"] = posts == []
        checks["sin_version_nueva"] = despues == antes
        checks["sin_recarga"] = marca == "sin-recarga"
        checks["texto_error"] = estado

        # Control: una cuota valida no la bloquea el navegador; el POST sale
        # (la instancia de verificacion no tiene token ni DSN admin, asi que el
        # servidor lo rechaza: lo que se comprueba es que la UI lo deja salir).
        navegador.evaluar("window.__posts = []; window.__marca = 'sin-recarga';")
        navegador.teclear(f'{forma} input[name="cap_bid"]', "7")
        navegador.evaluar(
            f"document.querySelector({json.dumps(forma + ' button[type=submit]')}).click()"
        )
        time.sleep(1.0)
        posts_ok = navegador.evaluar("window.__posts") or []
        estado_ok = navegador.evaluar(
            f"document.querySelector({json.dumps(forma + ' [data-estado]')}).textContent"
        ) or ""
        checks["cuota_valida_intenta_post"] = len(posts_ok) >= 1
        checks["cuota_valida_sin_error_del_navegador"] = "cuota de" not in estado_ok
        checks["estado_cuota_valida"] = estado_ok
    finally:
        navegador.cerrar()
        shutil.rmtree(perfil, ignore_errors=True)

    ok = all(
        v is True
        for k, v in checks.items()
        if k not in ("texto_error", "estado_cuota_valida")
    )
    salida = {"ok": ok, "base": args.base, "db": args.db, "checks": checks}
    texto = json.dumps(salida, indent=2, ensure_ascii=False)
    print(texto)
    if args.salida:
        Path(args.salida).write_text(texto + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
