from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

from gen_favicons import DIR_FAVICON, ICO_TAMANOS, TAMANOS  # noqa: E402

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
    fallos.extend(_orejas_blancas_en_borde(ruta.name, png))
    return fallos


def _orejas_blancas_en_borde(nombre: str, png: dict) -> list[str]:
    w, h = png["ancho"], png["alto"]
    vecinos = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    fallos: list[str] = []
    for y in range(h):
        for x in range(w):
            r, g, b, a = _pixel(png, x, y)
            if a != 255 or r < 248 or g < 248 or b < 248:
                continue
            if any(
                0 <= x + dx < w and 0 <= y + dy < h and _pixel(png, x + dx, y + dy)[3] == 0
                for dx, dy in vecinos
            ):
                fallos.append(f"{nombre}: oreja blanca en ({x},{y}) rgba=({r},{g},{b},{a})")
    return fallos


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
        assert blob[:8] == _PNG_SIG, f"{ruta.name}: frame {w}x{h} no es PNG"
        png = _leer_png(blob)
        png["via"] = "png"
        frames.append(png)
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
        fallos.extend(_orejas_blancas_en_borde(etiqueta, fr))
    assert not fallos, "favicon.ico invalido:\n" + "\n".join(fallos)
