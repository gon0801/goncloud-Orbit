"""Invariante de los favicons: PNG RGBA con esquinas transparentes.

El pack RGB (color type 2, esquinas 255,255,255 opacas) dejaba orejas
blancas en el chrome del navegador. Este test lee IHDR/IDAT con stdlib
y habria fallado contra ese pack.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_FAVICON = RAIZ / "app" / "static" / "favicon"

TAMANOS = (
    ("favicon-16.png", 16),
    ("favicon-32.png", 32),
    ("favicon-48.png", 48),
    ("favicon-64.png", 64),
    ("favicon-180.png", 180),
    ("favicon-192.png", 192),
    ("favicon-512.png", 512),
)
ICO_TAMANOS = (16, 32, 48)

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_CANALES = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _reconstruir_filas(bruto: bytes, ancho: int, alto: int, bpp: int) -> list[bytes]:
    stride = ancho * bpp
    filas: list[bytes] = []
    prev = bytearray(stride)
    i = 0
    for _ in range(alto):
        filtro = bruto[i]
        scan = bruto[i + 1 : i + 1 + stride]
        i += 1 + stride
        recon = bytearray(stride)
        for x, val in enumerate(scan):
            a = recon[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if filtro == 0:
                recon[x] = val
            elif filtro == 1:
                recon[x] = (val + a) & 255
            elif filtro == 2:
                recon[x] = (val + b) & 255
            elif filtro == 3:
                recon[x] = (val + (a + b) // 2) & 255
            elif filtro == 4:
                recon[x] = (val + _paeth(a, b, c)) & 255
            else:
                raise AssertionError(f"filtro PNG desconocido: {filtro}")
        filas.append(bytes(recon))
        prev = recon
    return filas


def _leer_png(data: bytes) -> dict:
    assert data[:8] == _PNG_SIG, "firma PNG invalida"
    pos = 8
    ancho = alto = profundidad = color = entrelazado = None
    idat = b""
    while pos + 8 <= len(data):
        largo = struct.unpack(">I", data[pos : pos + 4])[0]
        tipo = data[pos + 4 : pos + 8]
        cuerpo = data[pos + 8 : pos + 8 + largo]
        if tipo == b"IHDR":
            ancho, alto, profundidad, color, _comp, _filt, entrelazado = struct.unpack(
                ">IIBBBBB", cuerpo
            )
        elif tipo == b"IDAT":
            idat += cuerpo
        elif tipo == b"IEND":
            break
        pos += 12 + largo
    assert None not in (ancho, alto, profundidad, color, entrelazado)
    assert profundidad == 8, f"bit depth {profundidad} (solo 8)"
    assert entrelazado == 0, "PNG entrelazado no soportado"
    assert color in _CANALES, f"color type {color} no soportado"
    bpp = _CANALES[color]
    filas = _reconstruir_filas(zlib.decompress(idat), ancho, alto, bpp)
    return {
        "ancho": ancho,
        "alto": alto,
        "color": color,
        "bpp": bpp,
        "filas": filas,
    }


def _pixel(png: dict, x: int, y: int) -> tuple[int, int, int, int]:
    bpp = png["bpp"]
    crudo = png["filas"][y][x * bpp : (x + 1) * bpp]
    if png["color"] == 6:
        return (crudo[0], crudo[1], crudo[2], crudo[3])
    if png["color"] == 2:
        return (crudo[0], crudo[1], crudo[2], 255)
    raise AssertionError(f"color type {png['color']} sin RGBA")


def _esquinas(png: dict) -> list[tuple[tuple[int, int], tuple[int, int, int, int]]]:
    w, h = png["ancho"], png["alto"]
    coords = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
    return [(xy, _pixel(png, *xy)) for xy in coords]


def _fallos_png(ruta: Path, px_esperado: int | None) -> list[str]:
    png = _leer_png(ruta.read_bytes())
    fallos: list[str] = []
    if png["color"] != 6:
        fallos.append(f"{ruta.name}: color type {png['color']} (esperado 6 RGBA)")
    if px_esperado is not None and (png["ancho"], png["alto"]) != (px_esperado, px_esperado):
        fallos.append(
            f"{ruta.name}: {png['ancho']}x{png['alto']} (esperado {px_esperado}x{px_esperado})"
        )
    for (x, y), pix in _esquinas(png):
        r, g, b, a = pix
        if a != 0:
            fallos.append(f"{ruta.name}: esquina ({x},{y}) alpha={a} (esperado 0)")
        if (r, g, b) == (255, 255, 255) and a == 255:
            fallos.append(f"{ruta.name}: esquina ({x},{y}) es blanco opaco")
    return fallos


def _leer_dib(blob: bytes, w_dir: int, h_dir: int) -> dict:
    """DIB clasico de ICO (XOR + mascara AND)."""
    cab = struct.unpack_from("<IiiHHIIiiII", blob, 0)
    bitcount = cab[4]
    alto_dib = abs(cab[2])
    alto = alto_dib // 2 if alto_dib == h_dir * 2 else h_dir
    ancho = cab[1] if cab[1] else w_dir
    off = cab[0]
    row_xor = ((ancho * bitcount + 31) // 32) * 4
    filas: list[bytes] = []
    for y in range(alto):
        # Filas DIB van de abajo hacia arriba.
        src = blob[off + (alto - 1 - y) * row_xor : off + (alto - y) * row_xor]
        if bitcount == 32:
            # BGRA
            fila = bytearray()
            for x in range(ancho):
                b, g, r, a = src[x * 4 : x * 4 + 4]
                fila.extend((r, g, b, a))
            filas.append(bytes(fila))
        elif bitcount == 24:
            fila = bytearray()
            for x in range(ancho):
                b, g, r = src[x * 3 : x * 3 + 3]
                fila.extend((r, g, b, 255))
            filas.append(bytes(fila))
        else:
            raise AssertionError(f"ICO DIB bitcount {bitcount} no soportado")
    and_off = off + row_xor * alto
    row_and = ((ancho + 31) // 32) * 4
    if and_off + row_and * alto <= len(blob) and bitcount != 32:
        for y in range(alto):
            src = blob[and_off + (alto - 1 - y) * row_and : and_off + (alto - y) * row_and]
            fila = bytearray(filas[y])
            for x in range(ancho):
                bit = (src[x // 8] >> (7 - (x % 8))) & 1
                if bit:
                    fila[x * 4 + 3] = 0
            filas[y] = bytes(fila)
    return {
        "ancho": ancho,
        "alto": alto,
        "color": 6,
        "bpp": 4,
        "filas": filas,
        "via": "dib",
    }


def _leer_ico(ruta: Path) -> list[dict]:
    data = ruta.read_bytes()
    _reserved, tipo, count = struct.unpack_from("<HHH", data, 0)
    assert tipo == 1, f"{ruta.name}: tipo ICO {tipo}"
    frames: list[dict] = []
    for i in range(count):
        w, h, _ncolors, _res, _planes, _bits, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + i * 16
        )
        w = 256 if w == 0 else w
        h = 256 if h == 0 else h
        blob = data[offset : offset + size]
        if blob[:8] == _PNG_SIG:
            png = _leer_png(blob)
            png["via"] = "png"
            frames.append(png)
        else:
            frames.append(_leer_dib(blob, w, h))
    return frames


def test_favicons_png_rgba_esquinas_transparentes():
    fallos: list[str] = []
    vistos: set[str] = set()
    for nombre, px in TAMANOS:
        ruta = DIR_FAVICON / nombre
        if not ruta.is_file():
            fallos.append(f"{nombre}: no existe")
            continue
        vistos.add(nombre)
        fallos.extend(_fallos_png(ruta, px))
    for ruta in sorted(DIR_FAVICON.glob("*.png")):
        if ruta.name in vistos:
            continue
        fallos.extend(_fallos_png(ruta, None))
    assert not fallos, "favicons PNG invalidos:\n" + "\n".join(fallos)


def test_favicon_ico_frames_con_transparencia():
    ruta = DIR_FAVICON / "favicon.ico"
    assert ruta.is_file(), "falta favicon.ico"
    frames = _leer_ico(ruta)
    fallos: list[str] = []
    if len(frames) < 3:
        fallos.append(f"favicon.ico tiene {len(frames)} frame(s) (esperado >=3: {ICO_TAMANOS})")
    tamanos = {(fr["ancho"], fr["alto"]) for fr in frames}
    for px in ICO_TAMANOS:
        if (px, px) not in tamanos:
            fallos.append(f"favicon.ico sin frame {px}x{px}")
    for i, fr in enumerate(frames):
        etiqueta = f"ico[{i}] {fr['ancho']}x{fr['alto']} via={fr.get('via', '?')}"
        if fr["color"] != 6:
            fallos.append(f"{etiqueta}: color type {fr['color']} (esperado 6 RGBA)")
        for (x, y), pix in _esquinas(fr):
            r, g, b, a = pix
            if a != 0:
                fallos.append(f"{etiqueta}: esquina ({x},{y}) alpha={a}")
            if (r, g, b) == (255, 255, 255) and a == 255:
                fallos.append(f"{etiqueta}: esquina ({x},{y}) es blanco opaco")
    assert not fallos, "favicon.ico invalido:\n" + "\n".join(fallos)
