#!/usr/bin/env python3
from __future__ import annotations

import argparse
import struct
from collections import deque
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
FUENTE_DEFAULT = DIR_FAVICON / "favicon-512.png"
MASTER_RGBA = DIR_FAVICON / "orbit-icon-source.png"

UMBRAL_BLANCO = 248


def _pil_image():
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit(
            "Pillow no esta instalado. Solo sesion, no pyproject: python -m pip install pillow"
        ) from exc
    return Image


def es_lienzo(r: int, g: int, b: int, a: int) -> bool:
    if a == 0:
        return True
    if r >= UMBRAL_BLANCO and g >= UMBRAL_BLANCO and b >= UMBRAL_BLANCO:
        return True
    return a < 80 and min(r, g, b) >= 200


def suavizar_halo(pix, ancho: int, alto: int) -> None:
    vecinos = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    for _ in range(2):
        candidatos: list[tuple[int, int]] = []
        for y in range(alto):
            for x in range(ancho):
                if pix[x, y][3] == 0:
                    continue
                if any(
                    0 <= x + dx < ancho and 0 <= y + dy < alto and pix[x + dx, y + dy][3] == 0
                    for dx, dy in vecinos
                ):
                    candidatos.append((x, y))
        for x, y in candidatos:
            r, g, b, _a = pix[x, y]
            alfa = max(255 - r, 255 - g, 255 - b)
            if alfa == 0:
                pix[x, y] = (0, 0, 0, 0)
                continue
            inv = 255 - alfa
            sr = max(0, min(255, round((r - inv) * 255 / alfa)))
            sg = max(0, min(255, round((g - inv) * 255 / alfa)))
            sb = max(0, min(255, round((b - inv) * 255 / alfa)))
            pix[x, y] = (sr, sg, sb, alfa)


def perforar_esquinas(im, *, forzar: bool = False):
    im = im.convert("RGBA")
    ancho, alto = im.size
    pix = im.load()
    esquinas = ((0, 0), (ancho - 1, 0), (0, alto - 1), (ancho - 1, alto - 1))
    if not forzar and all(pix[x, y][3] == 0 for x, y in esquinas):
        return im
    visitado = [[False] * ancho for _ in range(alto)]
    cola: deque[tuple[int, int]] = deque()
    for x, y in esquinas:
        visitado[y][x] = True
        cola.append((x, y))
    while cola:
        x, y = cola.popleft()
        r, g, b, a = pix[x, y]
        if not es_lienzo(r, g, b, a):
            continue
        pix[x, y] = (0, 0, 0, 0)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < ancho and 0 <= ny < alto and not visitado[ny][nx]:
                    visitado[ny][nx] = True
                    cola.append((nx, ny))
    suavizar_halo(pix, ancho, alto)
    return im


def escalar(master, px: int):
    if master.size == (px, px):
        return master.copy()
    Image = _pil_image()
    return master.resize((px, px), Image.Resampling.LANCZOS)


def escribir_ico(frames: list[tuple[int, bytes]], ruta: Path) -> None:
    count = len(frames)
    offset = 6 + 16 * count
    entradas = []
    blobs = []
    for lado, png in frames:
        wb = 0 if lado >= 256 else lado
        entradas.append(struct.pack("<BBBBHHII", wb, wb, 0, 0, 1, 32, len(png), offset))
        blobs.append(png)
        offset += len(png)
    ruta.write_bytes(struct.pack("<HHH", 0, 1, count) + b"".join(entradas) + b"".join(blobs))


def generar(fuente: Path, destino: Path) -> None:
    Image = _pil_image()
    destino.mkdir(parents=True, exist_ok=True)
    master = perforar_esquinas(Image.open(fuente))
    master.save(destino / MASTER_RGBA.name)
    for nombre, px in TAMANOS:
        foto = escalar(master, px)
        if foto.size != master.size:
            foto = perforar_esquinas(foto, forzar=True)
        foto.save(destino / nombre)
    ico_frames: list[tuple[int, bytes]] = []
    for px in ICO_TAMANOS:
        ico_frames.append((px, (destino / f"favicon-{px}.png").read_bytes()))
    escribir_ico(ico_frames, destino / "favicon.ico")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera favicons RGBA con esquinas transparentes.")
    parser.add_argument("fuente", nargs="?", default=str(FUENTE_DEFAULT))
    parser.add_argument("--destino", default=str(DIR_FAVICON))
    args = parser.parse_args(argv)
    generar(Path(args.fuente), Path(args.destino))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
