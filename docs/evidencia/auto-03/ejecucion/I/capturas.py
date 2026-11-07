"""Capturas de AUTO-03 I.8: encender la pausa por producto desde la pantalla.

No es codigo de producto. Corre sobre una instancia de `orbit-verify launch`
(base desechable con la semilla de productos) y deja en `--salida` las
capturas `pantalla-*.png` y `pantalla-capturas.json`:

    h=.cursor/skills/verify-orbit/helpers/orbit-verify
    ORBIT_VERIFY_RUN_ID=cap $h launch
    .venv/bin/python docs/evidencia/auto-03/ejecucion/I/capturas.py --run-id cap \
        --salida docs/evidencia/auto-03/ejecucion/I
    $h cleanup --run-id cap

La instancia de `launch` no tiene token ni DSN admin, asi que este script
levanta un segundo uvicorn sobre la MISMA base desechable con un token propio
(en un directorio temporal) y `ORBIT_DSN_ADMIN` = el DSN de esa base, y hace
clic de verdad con Chrome headless (CDP): guarda los limites con "Guardar
límites", abre "Encender la pausa", cancela, confirma, y vuelve a practica.
Antes deja la pausa en practica con `productos_modo.cambia_pausa`, como esta
en produccion. Nunca toca otra base: el DSN sale del state del run.
"""

from __future__ import annotations

import argparse
import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[5]
HELPER = RAIZ / ".cursor" / "skills" / "verify-orbit" / "helpers" / "orbit-verify"
TOKEN = "token-de-capturas-i8"
MX = "amazon_mx"


def _helper():
    """El helper de verify-orbit (cliente websocket y navegador)."""
    cargador = importlib.machinery.SourceFileLoader("orbit_verify", str(HELPER))
    spec = importlib.util.spec_from_loader("orbit_verify", cargador)
    modulo = importlib.util.module_from_spec(spec)
    cargador.exec_module(modulo)
    return modulo


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _espera_http(url: str, segundos: float = 15) -> None:
    fin = time.time() + segundos
    while time.time() < fin:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.25)
    raise SystemExit(f"capturas: {url} no respondio")


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Navegador:
    """Chrome headless con un cliente CDP minimo (el del helper)."""

    def __init__(self, ov) -> None:
        self.ov = ov
        self.perfil = Path(tempfile.mkdtemp(prefix="orbit-capturas-"))
        self.proc = subprocess.Popen(
            [
                ov._navegador(),
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--hide-scrollbars",
                "--remote-debugging-port=0",
                f"--user-data-dir={self.perfil}",
                "--window-size=1280,900",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        marca = self.perfil / "DevToolsActivePort"
        fin = time.time() + 20
        while not marca.is_file():
            if self.proc.poll() is not None or time.time() > fin:
                raise SystemExit("capturas: el navegador no abrio DevTools")
            time.sleep(0.1)
        puerto = int(marca.read_text(encoding="utf-8").splitlines()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/json/list", timeout=5) as resp:
            pagina = next(p for p in json.loads(resp.read()) if p.get("type") == "page")
        ws = urllib.parse.urlparse(pagina["webSocketDebuggerUrl"])
        self.sock = socket.create_connection((ws.hostname, ws.port), timeout=20)
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
            saludo += ov._ws_recv(self.sock)
        self.pendiente = saludo.split(b"\r\n\r\n", 1)[1]
        self.ident = 0
        self.llamar("Page.enable")
        self.llamar("Runtime.enable")

    def llamar(self, metodo: str, params: dict | None = None) -> dict:
        self.ident += 1
        mensaje = {"id": self.ident, "method": metodo, **({"params": params} if params else {})}
        self.ov._ws_enviar(self.sock, json.dumps(mensaje))
        while True:
            opcode, payload, self.pendiente = self.ov._ws_leer(self.sock, self.pendiente)
            if opcode == 8:
                raise EOFError("websocket cerrado por el navegador")
            if opcode != 1:
                continue
            data = json.loads(payload.decode())
            if data.get("id") == self.ident:
                if "error" in data:
                    raise RuntimeError(f"{metodo}: {data['error']}")
                return data

    def js(self, expresion: str):
        r = self.llamar(
            "Runtime.evaluate",
            {"expression": expresion, "returnByValue": True, "awaitPromise": True},
        )["result"]
        if "exceptionDetails" in r:
            raise RuntimeError(f"js fallo: {r['exceptionDetails']}")
        return r["result"].get("value")

    def espera(self, expresion: str, segundos: float = 15):
        fin = time.time() + segundos
        ultimo = None
        while time.time() < fin:
            try:
                valor = self.js(expresion)
                if valor:
                    return valor
            except (RuntimeError, KeyError) as exc:
                ultimo = exc
            time.sleep(0.2)
        raise SystemExit(f"capturas: no se cumplio {expresion!r} ({ultimo})")

    def abre(self, url: str) -> None:
        self.llamar("Page.navigate", {"url": url})
        self.espera("document.readyState === 'complete' && !!document.getElementById('pausa-vivo')")

    def tecla(self, key: str, code: str, vk: int, texto: str | None = None) -> None:
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
        self.llamar(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", **base, **({"text": texto} if texto else {})},
        )
        self.llamar("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    def tema(self, tema: str) -> str:
        return self.js(
            f"localStorage.setItem('orbit-tema', '{tema}');"
            f" document.documentElement.dataset.tema = '{tema}';"
            " document.documentElement.dataset.tema"
        )

    def ancho(self, ancho: int | None) -> dict:
        if ancho is None:
            self.llamar("Emulation.clearDeviceMetricsOverride")
        else:
            self.llamar(
                "Emulation.setDeviceMetricsOverride",
                {"width": ancho, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
        time.sleep(0.4)
        return self.js(
            "({ancho: window.innerWidth,"
            " bodyScroll: document.body.scrollWidth > document.body.clientWidth + 1})"
        )

    def captura(self, selector: str, png: Path) -> None:
        caja = self.js(
            f"(() => {{ const el = document.querySelector('{selector}');"
            " el.scrollIntoView({block: 'start'});"
            " const r = el.getBoundingClientRect();"
            " return {x: r.left + window.scrollX, y: r.top + window.scrollY,"
            " w: r.width, h: r.height}; })()"
        )
        margen = 8
        clip = {
            "x": max(caja["x"] - margen, 0),
            "y": max(caja["y"] - margen, 0),
            "width": caja["w"] + 2 * margen,
            "height": caja["h"] + 2 * margen,
            "scale": 1,
        }
        toma = self.llamar(
            "Page.captureScreenshot",
            {"format": "png", "clip": clip, "captureBeyondViewport": True},
        )
        png.write_bytes(base64.b64decode(toma["result"]["data"]))

    def cierra(self) -> None:
        try:
            self.sock.close()
        finally:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            shutil.rmtree(self.perfil, ignore_errors=True)


def _foco_con_tab(nav: Navegador, url: str, objetivo: str, maximo: int = 250) -> dict:
    """Pagina recien cargada y Tab desde el inicio hasta `objetivo`; cuenta las teclas."""
    nav.abre(url)
    for n in range(1, maximo + 1):
        nav.tecla("Tab", "Tab", 9)
        if nav.js(f"document.activeElement && document.activeElement.id === '{objetivo}'"):
            datos = nav.js(
                "(() => { const a = document.activeElement; const e = getComputedStyle(a);"
                " return {id: a.id, texto: a.textContent.trim(),"
                " focoVisible: a.matches(':focus-visible'),"
                " outline: e.outlineStyle + ' ' + e.outlineWidth + ' ' + e.outlineColor}; })()"
            )
            return {"tabs": n, **datos}
    raise SystemExit(f"capturas: Tab no llego a #{objetivo}")


def _cambia_a_practica(state: dict) -> int:
    """La pausa en practica, como en produccion (productos-modo --pausa shadow)."""
    import psycopg

    sys.path.insert(0, str(RAIZ))
    from app import productos_modo

    base = state["seed"]["productos"]["config_version_id"]
    with psycopg.connect(state["dsn"]) as conn:
        r = productos_modo.cambia_pausa(
            conn, MX, "shadow", base, datetime.now(UTC), actor="capturas-i8"
        )
    return r["config_version_id"]


TARJETA = "#productos-limites"


def _png(salida: Path, nombre: str) -> Path:
    return salida / f"pantalla-{nombre}.png"


def _recorrido_practica(nav: Navegador, base: str, url: str, salida: Path, registro: dict) -> None:
    """Sin limites (deshabilitado), guardar limites, foco, confirmacion y cancelar."""
    tarjeta = TARJETA

    def png(nombre: str) -> Path:
        return _png(salida, nombre)

    # 1. Sin limites guardados: "Encender la pausa" deshabilitado.
    nav.abre(url)
    nav.tema("dia")
    registro["deshabilitado"] = nav.js(
        "(() => { const b = document.getElementById('pausa-encender');"
        " return {disabled: b.disabled, texto: b.textContent.trim(),"
        " describe: document.getElementById(b.getAttribute('aria-describedby')).textContent,"
        " estado: document.querySelector('[data-modo-vigente]').textContent}; })()"
    )
    nav.captura(tarjeta, png("01-escritorio-claro-deshabilitado"))
    registro["tema_noche"] = nav.tema("noche")
    nav.captura(tarjeta, png("02-escritorio-oscuro-deshabilitado"))
    nav.tema("dia")
    registro["ancho_390_deshabilitado"] = nav.ancho(390)
    nav.captura(tarjeta, png("03-390-claro-deshabilitado"))
    nav.ancho(None)

    # 2. Guardar los limites por la ruta real (token y POST /settings).
    nav.js(
        f"document.getElementById('limites-token').value = '{TOKEN}';"
        " document.querySelector('#limites-guardar button[type=submit]').click(); true"
    )
    nav.espera(
        "document.readyState === 'complete' && !!document.getElementById('pausa-encender')"
        " && !document.getElementById('pausa-encender').disabled"
    )
    antes = _get_json(f"{base}/api/dashboard/productos/pausa?platform={MX}")
    registro["con_limites"] = antes

    # 3. Teclado: Tab hasta el boton y Enter abre la confirmacion.
    registro["foco_encender"] = _foco_con_tab(nav, url, "pausa-encender")
    nav.captura("#pausa-vivo", png("04-teclado-foco-encender-claro"))
    nav.tecla("Enter", "Enter", 13, "\r")
    nav.espera("!document.getElementById('pausa-confirmar-form').hidden")
    registro["confirmacion"] = nav.js(
        "({titulo: document.querySelector('#pausa-confirmar-titulo').textContent.trim(),"
        " resumen: document.querySelector('[data-resumen-encender]').textContent,"
        " cobertura: (document.querySelector('#pausa-confirmar [data-sin-cobertura]')"
        " || {}).textContent || null,"
        " foco: document.activeElement.id})"
    )
    nav.captura(tarjeta, png("05-escritorio-claro-confirmacion"))
    nav.tema("noche")
    nav.captura(tarjeta, png("06-escritorio-oscuro-confirmacion"))
    registro["ancho_390_confirmacion"] = nav.ancho(390)
    nav.captura("#pausa-vivo", png("07-390-oscuro-confirmacion"))
    nav.tema("dia")
    nav.captura("#pausa-vivo", png("08-390-claro-confirmacion"))
    nav.ancho(None)

    # 4. Cancelar no escribe.
    nav.js("document.getElementById('pausa-cancelar').click(); true")
    nav.espera("document.getElementById('pausa-confirmar').hidden")
    despues = _get_json(f"{base}/api/dashboard/productos/pausa?platform={MX}")
    registro["cancelar"] = {
        "estado": nav.js("document.getElementById('pausa-vivo-estado').textContent"),
        "foco": nav.js("document.activeElement.id"),
        "config_antes": antes["config_version_id"],
        "config_despues": despues["config_version_id"],
        "modo_despues": despues["modo"],
        "no_escribio": despues == antes,
    }
    nav.captura("#pausa-vivo", png("09-escritorio-claro-cancelado"))


def _recorrido_vivo(nav: Navegador, base: str, salida: Path, registro: dict) -> None:
    """Encender de verdad, en vivo en los dos temas y a 390 px, y volver a practica."""
    tarjeta = TARJETA

    def png(nombre: str) -> Path:
        return _png(salida, nombre)

    # 5. Encender de verdad: simulacion, token y Confirmar.
    nav.js("document.getElementById('pausa-encender').click(); true")
    nav.espera("!document.getElementById('pausa-confirmar-form').hidden")
    nav.llamar("Input.insertText", {"text": TOKEN})
    nav.js("document.querySelector('#pausa-confirmar-form button[type=submit]').click(); true")
    nav.espera("document.readyState === 'complete' && !!document.getElementById('pausa-volver')")
    vivo = _get_json(f"{base}/api/dashboard/productos/pausa?platform={MX}")
    registro["en_vivo"] = {
        "modo": vivo["modo"],
        "config_version_id": vivo["config_version_id"],
        "estado": nav.js("document.querySelector('[data-modo-vigente]').textContent"),
        "boton": nav.js("document.getElementById('pausa-volver').textContent"),
    }
    nav.captura(tarjeta, png("10-escritorio-claro-en-vivo"))
    nav.tema("noche")
    nav.captura(tarjeta, png("11-escritorio-oscuro-en-vivo"))
    registro["ancho_390_en_vivo"] = nav.ancho(390)
    nav.captura("#pausa-vivo", png("12-390-oscuro-en-vivo"))
    nav.tema("dia")
    nav.captura("#pausa-vivo", png("13-390-claro-en-vivo"))
    nav.ancho(None)

    # 6. Volver a practica: foco de teclado, confirmacion y Confirmar.
    registro["foco_volver"] = _foco_con_tab(
        nav, f"{base}/productos-sin-clics?plataforma={MX}", "pausa-volver"
    )
    nav.tecla("Enter", "Enter", 13, "\r")
    nav.espera("!document.getElementById('pausa-confirmar-form').hidden")
    registro["volver_confirmacion"] = nav.js(
        "({titulo: document.querySelector('#pausa-confirmar-titulo').textContent.trim(),"
        " foco: document.activeElement.id})"
    )
    nav.captura("#pausa-vivo", png("14-escritorio-claro-volver-confirmacion"))
    nav.llamar("Input.insertText", {"text": TOKEN})
    nav.js("document.querySelector('#pausa-confirmar-form button[type=submit]').click(); true")
    nav.espera("document.readyState === 'complete' && !!document.getElementById('pausa-encender')")
    practica = _get_json(f"{base}/api/dashboard/productos/pausa?platform={MX}")
    registro["de_vuelta"] = {
        "modo": practica["modo"],
        "config_version_id": practica["config_version_id"],
        "estado": nav.js("document.querySelector('[data-modo-vigente]').textContent"),
    }
    nav.captura("#pausa-vivo", png("15-escritorio-claro-de-vuelta-en-practica"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--salida", required=True, type=Path)
    args = parser.parse_args()
    state = json.loads(
        Path(f"/tmp/orbit-verify/{args.run_id}/state.json").read_text(encoding="utf-8")
    )
    if "orbit_verify_" not in state["db"]:
        raise SystemExit(f"capturas: {state['db']} no es una base desechable de verify-orbit")
    salida: Path = args.salida
    salida.mkdir(parents=True, exist_ok=True)
    ov = _helper()
    registro: dict = {"run_id": args.run_id}
    registro["config_practica"] = _cambia_a_practica(state)

    secretos = Path(tempfile.mkdtemp(prefix="orbit-capturas-secretos-"))
    (secretos / "api_write_token").write_text(TOKEN, encoding="utf-8")
    puerto = _puerto_libre()
    env = {
        **os.environ,
        "ORBIT_DSN_READ": state["dsn"],
        "ORBIT_DSN_ADMIN": state["dsn"],
        "ORBIT_SECRETS_DIR": str(secretos),
    }
    for clave in ("ORBIT_PG_HOST", "ORBIT_DSN_DECIDE", "ORBIT_DSN_INGEST"):
        env.pop(clave, None)
    uvicorn = subprocess.Popen(
        [
            str(RAIZ / ".venv" / "bin" / "python"),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(puerto),
        ],
        cwd=str(RAIZ),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{puerto}"
    url = f"{base}/productos-sin-clics?plataforma={MX}"
    nav = None
    try:
        _espera_http(f"{base}/health")
        nav = Navegador(ov)
        _recorrido_practica(nav, base, url, salida, registro)
        _recorrido_vivo(nav, base, salida, registro)
    finally:
        if nav is not None:
            nav.cierra()
        uvicorn.terminate()
        try:
            uvicorn.wait(timeout=5)
        except subprocess.TimeoutExpired:
            uvicorn.kill()
        shutil.rmtree(secretos, ignore_errors=True)
    registro["capturas"] = sorted(p.name for p in salida.glob("pantalla-*.png"))
    (salida / "pantalla-capturas.json").write_bytes(
        (json.dumps(registro, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )
    print(json.dumps(registro, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
