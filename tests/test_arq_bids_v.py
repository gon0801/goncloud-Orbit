"""Guardas de arquitectura de la letra V (BIDS 02 V.1; las demas tareas V agregan aqui).

Ningun payload de Amazon sale de `config_de_payload`: en `app/`, solo
`app/ads/campana_config.py` nombra `placementBidding` y `offAmazonSettings`.

`app/avisos_campana.py` (V.4) es puro: no importa `psycopg`, `httpx` ni
`app.notifica` (sin DB ni red).

`app/campana_ajustes.py` (V.3) es puro: no importa `psycopg`, `httpx` ni
`app.ads.write` (sin DB ni red; la escritura vive en `app/apply.py`).
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


PURO = APP / "avisos_campana.py"
IMPUROS = ("psycopg", "httpx", "app.notifica")


def test_avisos_campana_es_puro():
    assert PURO.exists()
    assert _archivos_que_nombran(PURO.parent, IMPUROS) & {PURO} == set()


def test_detector_puro_caza_import_sembrado(tmp_path):
    bueno = tmp_path / "app" / "avisos_campana.py"
    bueno.parent.mkdir(parents=True)
    bueno.write_text("from app.optimizer.bid import PLATAFORMAS_MONEDA\n", encoding="utf-8")
    assert _archivos_que_nombran(tmp_path / "app", IMPUROS) == set()
    fuga = tmp_path / "app" / "avisos_otro.py"
    fuga.write_text("import httpx\n", encoding="utf-8")
    assert _archivos_que_nombran(tmp_path / "app", IMPUROS) == {fuga}


PURO_AJUSTES = APP / "campana_ajustes.py"
IMPUROS_AJUSTES = ("psycopg", "httpx", "app.ads.write")


def test_campana_ajustes_es_puro():
    assert PURO_AJUSTES.exists()
    assert _archivos_que_nombran(PURO_AJUSTES.parent, IMPUROS_AJUSTES) & {PURO_AJUSTES} == set()


def test_detector_ajustes_caza_import_sembrado(tmp_path):
    bueno = tmp_path / "app" / "campana_ajustes.py"
    bueno.parent.mkdir(parents=True)
    bueno.write_text("from app.ads.campana_config import ConfigCampana\n", encoding="utf-8")
    assert _archivos_que_nombran(tmp_path / "app", IMPUROS_AJUSTES) == set()
    fuga = tmp_path / "app" / "campana_ajustes_otro.py"
    fuga.write_text("import httpx\n", encoding="utf-8")
    assert _archivos_que_nombran(tmp_path / "app", IMPUROS_AJUSTES) == {fuga}
