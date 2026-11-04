"""Candados de arquitectura de la letra I (BIDS 02, seccion 5).

impulso.py es puro: no importa nada de PROHIBIDOS_MOTOR.
retiro_anuncios.py escribe por app/ads/archivar.py (regla 15), nunca
por app.ads.write directo.
"""

from __future__ import annotations

from pathlib import Path

from test_architecture import PROHIBIDOS_MOTOR, _imports_runtime, _violaciones

RAIZ = Path(__file__).resolve().parents[1]
IMPULSO = RAIZ / "app" / "impulso.py"
RETIRO = RAIZ / "app" / "retiro_anuncios.py"


def test_impulso_es_puro():
    """I.2: app/impulso.py no importa IO (misma frontera que el motor)."""
    assert IMPULSO.exists(), "sin app/impulso.py el candado pasaria en falso"
    fugas = _violaciones(_imports_runtime(IMPULSO), PROHIBIDOS_MOTOR)
    assert not fugas, f"app/impulso.py debe ser puro: {fugas}"


def test_detector_caza_import_fuga_en_impulso(tmp_path):
    """El detector muerde: un import de app.ads.write aparece listado."""
    fuga = tmp_path / "impulso.py"
    fuga.write_text("from app.ads.write import AdsWriteClient\n", encoding="utf-8")
    assert "app.ads.write" in _violaciones(_imports_runtime(fuga), PROHIBIDOS_MOTOR)


def test_retiro_no_importa_write_directo():
    """I.4: app/retiro_anuncios.py escribe por archivar, nunca por
    app.ads.write directo (regla 15)."""
    assert RETIRO.exists(), "sin app/retiro_anuncios.py el candado pasaria en falso"
    fugas = _violaciones(_imports_runtime(RETIRO), ("app.ads.write",))
    assert not fugas, f"app/retiro_anuncios.py no toca app.ads.write: {fugas}"


def test_detector_caza_write_en_retiro(tmp_path):
    """El detector muerde: un import de app.ads.write aparece listado."""
    fuga = tmp_path / "retiro_anuncios.py"
    fuga.write_text("from app.ads.write import AdsWriteClient\n", encoding="utf-8")
    assert "app.ads.write" in _violaciones(_imports_runtime(fuga), ("app.ads.write",))
