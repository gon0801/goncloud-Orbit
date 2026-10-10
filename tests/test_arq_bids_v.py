"""Guardas de arquitectura de la letra V (BIDS 02 V.1; las demas tareas V agregan aqui).

Ningun payload de Amazon sale de `config_de_payload`: en `app/`, solo
`app/ads/campana_config.py` nombra `placementBidding` y `offAmazonSettings`.
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
APP = RAIZ / "app"
DUENO = APP / "ads" / "campana_config.py"
LLAVES = ("placementBidding", "offAmazonSettings")


def _archivos_que_nombran(raiz: Path, llaves: tuple[str, ...]) -> set[Path]:
    """Pythons bajo raiz cuyo texto nombra alguna llave."""
    return {
        ruta
        for ruta in sorted(raiz.rglob("*.py"))
        if any(llave in ruta.read_text(encoding="utf-8") for llave in llaves)
    }


def test_solo_campana_config_nombra_llaves_de_payload():
    assert DUENO.exists()
    assert _archivos_que_nombran(APP, LLAVES) == {DUENO}


def test_detector_caza_fuga_sembrada(tmp_path):
    bueno = tmp_path / "app" / "ads" / "campana_config.py"
    bueno.parent.mkdir(parents=True)
    bueno.write_text('x = item.get("placementBidding")\n', encoding="utf-8")
    assert _archivos_que_nombran(tmp_path / "app", LLAVES) == {bueno}
    fuga = tmp_path / "app" / "otro.py"
    fuga.write_text('y = item.get("offAmazonSettings")\n', encoding="utf-8")
    assert _archivos_que_nombran(tmp_path / "app", LLAVES) == {bueno, fuga}
