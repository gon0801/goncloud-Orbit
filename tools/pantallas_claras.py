"""Detector de lo que confunde en cada pantalla de Orbit (CLARIDAD 01, A.1).

Lee las rutas de los `href` del menu de `app/templates/base.html`, hace GET
a cada una contra `--base` y cuenta en el texto visible (con `<option>`,
`title`, `aria-label` y `placeholder`; nunca `<script>` ni `<style>`) los
codigos internos en snake_case, los decimales de 5 digitos o mas, las horas
con microsegundos, los montos sin separador de miles, los enteros largos sin
separador y las horas en UTC. Una ruta que no responde 200 dentro del tope
cuenta como `ruta_falla`. El texto dentro de un elemento con
`data-dato-libre` (nombres de campana, keywords, busquedas) no se cuenta.

Con `--navegador` lee la pantalla despues de correr JavaScript, con
`agent-browser`. Solo usa la biblioteca estandar y solo hace GET.

    python3 tools/pantallas_claras.py --base http://10.13.13.1:8010 --solo-cuenta
    python3 tools/pantallas_claras.py --base ... --rutas /,/cortes --tope 0
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

BASE_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "base.html"
TOPE_RUTA_S = 300
EJEMPLOS_POR_TIPO = 5
RUTA_FALLA = "ruta_falla"

# El orden importa: cada coincidencia se tacha del texto antes de buscar el
# patron siguiente, asi una cifra cuenta una sola vez y en su tipo mas fuerte.
PATRONES: dict[str, re.Pattern[str]] = {
    "microsegundos": re.compile(r"\d{2}:\d{2}:\d{2}\.\d+"),
    "utc": re.compile(r"\bUTC\b|\+00:00"),
    "decimales_largos": re.compile(r"(?<![\d.:])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{5,}"),
    "monto_sin_miles": re.compile(r"(?<![\d.,:])\d{4,}\.\d+"),
    "entero_sin_miles": re.compile(r"(?<![#\w.,:])\d{5,}(?!\d)"),
    "codigo_interno": re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"),
}

_OCULTOS = {"script", "style", "template"}
_VACIOS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}  # fmt: skip
_ATRIBUTOS = ("title", "aria-label", "placeholder")

# agent-browser deja de responder mientras un documento tarda en llegar (Inertes
# tarda 235 s), asi que el HTML se pide por HTTP con su tope y se escribe en una
# pagina del mismo origen, donde los scripts corren y piden sus APIs igual.
# document.open conserva el window, asi que el contador de fetch sigue vivo y
# dice cuando terminaron las lecturas que pintan la pantalla. Un fetch cuenta
# como pendiente hasta que llega todo su cuerpo, no solo los headers.
_JS_ESCRIBIR = """
window.__pendientes = 0;
const original = window.fetch;
const listo = () => window.__pendientes--;
window.fetch = (...args) => {
  window.__pendientes++;
  return original.apply(window, args).then(
    r => { r.clone().arrayBuffer().then(listo, listo); return r; },
    e => { listo(); throw e; });
};
history.replaceState(null, '', %s);
document.open(); document.write(%s); document.close(); true
"""

_JS_ESTADO = (
    "document.readyState === 'complete' && window.__pendientes === 0 "
    "? document.documentElement.outerHTML.length : -1"
)

# innerText deja fuera lo que esta oculto (un <details> cerrado, una pestana
# sin elegir), que la persona puede abrir; por eso tambien va el DOM ya pintado
# para leerlo con el mismo parser que el modo HTTP.
_JS_VISIBLE = """
(() => {
  const libres = [...document.querySelectorAll('[data-dato-libre]')];
  const antes = libres.map(e => e.style.getPropertyValue('display'));
  libres.forEach(e => e.style.setProperty('display', 'none', 'important'));
  const texto = document.body.innerText;
  libres.forEach((e, i) => antes[i] ? e.style.setProperty('display', antes[i])
                                    : e.style.removeProperty('display'));
  const attrs = [...document.querySelectorAll('[title],[aria-label],[placeholder]')]
    .filter(e => !e.closest('[data-dato-libre],script,style,template'))
    .flatMap(e => ['title', 'aria-label', 'placeholder']
      .map(a => e.getAttribute(a)).filter(Boolean));
  return {texto: [texto, ...attrs].join('\\n'), html: document.documentElement.outerHTML};
})()
"""


class _TextoVisible(HTMLParser):
    """Junta el texto que la persona ve, sin scripts ni datos libres."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pila: list[tuple[str, bool]] = []
        self.partes: list[str] = []

    def _excluido(self) -> bool:
        return any(t in _OCULTOS or libre for t, libre in self.pila)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        libre = "data-dato-libre" in a
        if not libre and not self._excluido():
            self.partes += [a[k] for k in _ATRIBUTOS if a.get(k)]
        if tag not in _VACIOS:
            self.pila.append((tag, libre))

    def handle_startendtag(self, tag, attrs):
        a = dict(attrs)
        if "data-dato-libre" not in a and not self._excluido():
            self.partes += [a[k] for k in _ATRIBUTOS if a.get(k)]

    def handle_endtag(self, tag):
        for i in range(len(self.pila) - 1, -1, -1):
            if self.pila[i][0] == tag:
                del self.pila[i:]
                return

    def handle_data(self, data):
        if not self._excluido():
            self.partes.append(data)


def texto_visible(html: str) -> str:
    p = _TextoVisible()
    p.feed(html)
    p.close()
    return "\n".join(p.partes)


def hallazgos(texto: str) -> dict[str, list[str]]:
    """Coincidencias distintas por tipo, en el orden en que aparecen."""
    out: dict[str, list[str]] = {}
    for tipo, patron in PATRONES.items():
        vistos: dict[str, None] = {}
        for m in patron.finditer(texto):
            vistos[m.group(0)] = None
        texto = patron.sub(lambda m: " " * len(m.group(0)), texto)
        if vistos:
            out[tipo] = list(vistos)
    return out


def rutas_del_menu(base_html: Path = BASE_HTML) -> list[str]:
    """Hrefs internos del `<nav id="nav-secciones">`, en orden y sin repetir."""
    fuente = base_html.read_text(encoding="utf-8")
    m = re.search(r'<nav id="nav-secciones".*?</nav>', fuente, re.S)
    if not m:
        raise SystemExit(f"no encontre el menu nav-secciones en {base_html}")
    rutas = re.findall(r"""(?:href="|\(\s*'[\w-]+',\s*')(/[^"'{}\s]*)""", m.group(0))
    return list(dict.fromkeys(rutas))


def _pedir(base: str, ruta: str) -> tuple[str | None, str | None]:
    """HTML de la ruta, o el motivo por el que cuenta como `ruta_falla`.

    Usa http.client y no urllib para no seguir redirecciones: un 302 es una
    falla de la ruta pedida. El timeout del socket solo mide silencios, asi que
    un reloj cierra el socket al vencer el tope aunque sigan llegando bytes, en
    las cabeceras o en el cuerpo. Una respuesta mas corta que su Content-Length
    tambien es falla.
    """
    limite = time.monotonic() + TOPE_RUTA_S
    url = urllib.parse.urlsplit(base + ruta)
    clase = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conexion = clase(url.netloc, timeout=TOPE_RUTA_S)
    vencido = threading.Event()
    reloj = None
    try:
        conexion.connect()
        sock = conexion.sock

        def cortar() -> None:
            vencido.set()
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

        reloj = threading.Timer(max(0.0, limite - time.monotonic()), cortar)
        reloj.start()
        conexion.request("GET", url.path + (f"?{url.query}" if url.query else ""))
        r = conexion.getresponse()
        if r.status != 200:
            return None, f"HTTP {r.status}"
        cuerpo = r.read()
    except (http.client.HTTPException, OSError) as e:
        if not vencido.is_set():
            return None, f"sin respuesta: {e!r}"
    finally:
        if reloj:
            reloj.cancel()
        conexion.close()
    if vencido.is_set():
        return None, f"no termino en el tope de {TOPE_RUTA_S} s"
    return cuerpo.decode("utf-8", "replace"), None


class _Navegador:
    """Una sesion propia de agent-browser para leer la pantalla ya pintada."""

    def __init__(self) -> None:
        self.sesion = f"pantallas-claras-{os.getpid()}"

    def _ab(self, *args: str, entrada: str | None = None) -> str:
        r = subprocess.run(
            ["agent-browser", "--session", self.sesion, *args],
            input=entrada,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if r.returncode:
            raise RuntimeError(f"agent-browser {args[0]}: {(r.stderr or r.stdout).strip()}")
        return r.stdout

    def _eval(self, js: str):
        crudo = json.loads(self._ab("--json", "eval", "--stdin", entrada=js))
        if not crudo.get("success", True):
            raise RuntimeError(f"agent-browser eval: {crudo.get('error')}")
        return crudo["data"]["result"]

    def leer(self, base: str, ruta: str, html: str) -> tuple[str | None, str | None]:
        limite = time.monotonic() + TOPE_RUTA_S
        try:
            self._ab("open", base + "/health")
            self._eval(_JS_ESCRIBIR % (json.dumps(ruta), json.dumps(html)))
            # Quieta: sin fetch pendientes y el DOM igual que un segundo antes.
            antes, ahora = -1, self._eval(_JS_ESTADO)
            while ahora < 0 or ahora != antes:
                if time.monotonic() > limite:
                    return None, f"no termino de pintar en {TOPE_RUTA_S} s"
                time.sleep(1)
                antes, ahora = ahora, self._eval(_JS_ESTADO)
            leido = self._eval(_JS_VISIBLE)
        except (RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as e:
            return None, f"navegador: {e}"
        return leido["texto"] + "\n" + texto_visible(leido["html"]), None

    def cerrar(self) -> None:
        subprocess.run(
            ["agent-browser", "--session", self.sesion, "close"],
            capture_output=True,
        )


def medir(base: str, rutas: list[str], navegador: bool) -> dict:
    base = base.rstrip("/")
    with ThreadPoolExecutor(max_workers=4) as pool:
        pedidos = list(pool.map(lambda r: _pedir(base, r), rutas))
    if navegador:
        nav = _Navegador()
        try:
            lecturas = [
                nav.leer(base, r, html) if html else (None, falla)
                for r, (html, falla) in zip(rutas, pedidos, strict=True)
            ]
        finally:
            nav.cerrar()
    else:
        lecturas = [(html and texto_visible(html), falla) for html, falla in pedidos]

    por_ruta: dict[str, dict[str, int]] = {}
    ejemplos: dict[str, list[str]] = {}
    for ruta, (texto, falla) in zip(rutas, lecturas, strict=True):
        if falla:
            por_ruta[ruta] = {RUTA_FALLA: 1}
            ejemplos[ruta] = [falla]
            continue
        h = hallazgos(texto or "")
        por_ruta[ruta] = {tipo: len(v) for tipo, v in h.items()}
        ejemplos[ruta] = [x for v in h.values() for x in v[:EJEMPLOS_POR_TIPO]]
    return {
        "base": base,
        "fecha": datetime.now(UTC).isoformat(timespec="seconds"),
        "navegador": navegador,
        "por_ruta": por_ruta,
        "ejemplos": ejemplos,
    }


def total(informe: dict) -> int:
    return sum(sum(c.values()) for c in informe["por_ruta"].values())


def imprimir(informe: dict, con_ejemplos: bool) -> None:
    for ruta, cuenta in informe["por_ruta"].items():
        detalle = ", ".join(f"{t}={n}" for t, n in cuenta.items()) or "limpia"
        print(f"{ruta}: {sum(cuenta.values())} ({detalle})")
        if con_ejemplos and informe["ejemplos"].get(ruta):
            print("    " + " | ".join(informe["ejemplos"][ruta]))
    print(f"total: {total(informe)} hallazgos en {len(informe['por_ruta'])} rutas")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Cuenta por pantalla de Orbit los codigos internos, decimales de mas, "
        "horas UTC y cifras sin miles."
    )
    ap.add_argument(
        "--base", required=True, help="raiz del dashboard, p. ej. http://10.13.13.1:8010"
    )
    ap.add_argument("--rutas", help="lista separada por comas; por omision, el menu de base.html")
    ap.add_argument("--json", metavar="ARCHIVO", help="guarda la cuenta por ruta y los ejemplos")
    ap.add_argument(
        "--navegador", action="store_true", help="lee la pantalla ya pintada por JavaScript"
    )
    ap.add_argument("--solo-cuenta", action="store_true", help="sin ejemplos; sale 0 siempre")
    ap.add_argument("--tope", type=int, help="sale 1 si los hallazgos pasan de N")
    args = ap.parse_args(argv)

    rutas = rutas_del_menu()
    if args.rutas:
        rutas = [r.strip() for r in args.rutas.split(",") if r.strip()]
    informe = medir(args.base, rutas, args.navegador)
    imprimir(informe, con_ejemplos=not args.solo_cuenta)
    if args.json:
        Path(args.json).write_text(
            json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.solo_cuenta:
        return 0
    if args.tope is not None and total(informe) > args.tope:
        print(f"pasa el tope: {total(informe)} > {args.tope}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
