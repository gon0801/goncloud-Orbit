"""Sonda pura B-B para comprobar que solo un negative ENABLED bloquea.

Corre sin base ni red contra `_negative_que_bloquea` con los items de la
seccion F4 de `docs/evidencia/arreglos-04/DISENO-bloqueos.md`.
"""

from __future__ import annotations

from app.bloqueo_dueno_efecto import _negative_que_bloquea


def main() -> None:
    k1 = {
        "keywordId": "k1",
        "adGroupId": "9",
        "keywordText": "baño",
        "matchType": "NEGATIVE_EXACT",
        "state": "PAUSED",
    }
    k2 = {
        "keywordId": "k2",
        "adGroupId": "9",
        "keywordText": "baño",
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }
    assert _negative_que_bloquea([k1], "9", "baño") is None
    assert _negative_que_bloquea([k1, k2], "9", "baño") == k2
    print("B-B OK")


if __name__ == "__main__":
    main()
