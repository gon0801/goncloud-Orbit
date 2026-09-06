from __future__ import annotations

import re
from pathlib import Path

from app.api_dashboard import MOTIVOS_ES_DECISIONES, MOTIVOS_ES_SALUD

_TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
_CAMPANA_ASCII = re.compile(r"\bcampanas?\b", re.IGNORECASE)
_JINJA_COMENTARIO = re.compile(r"\{#.*?#\}", re.DOTALL)
_JINJA_TAG = re.compile(r"\{%.*?%\}", re.DOTALL)
_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")


def _texto_visible(fuente: str) -> str:
    texto = _JINJA_COMENTARIO.sub(" ", fuente)
    texto = _JINJA_TAG.sub(" ", texto)
    texto = _JINJA_EXPR.sub(" ", texto)
    return _HTML_TAG.sub(" ", texto)


def test_templates_sin_campana_ascii_en_copia_visible():
    restos: list[str] = []
    for path in sorted(_TEMPLATES.glob("*.html")):
        visible = _texto_visible(path.read_text(encoding="utf-8"))
        for m in _CAMPANA_ASCII.finditer(visible):
            restos.append(f"{path.name}:{m.group(0)}")
    assert restos == []


def test_motivos_es_sin_campana_ascii():
    restos: list[str] = []
    for nombre, tabla in (
        ("MOTIVOS_ES_DECISIONES", MOTIVOS_ES_DECISIONES),
        ("MOTIVOS_ES_SALUD", MOTIVOS_ES_SALUD),
    ):
        for valor in tabla.values():
            hallado = _CAMPANA_ASCII.search(valor)
            if hallado is not None:
                restos.append(f"{nombre}:{hallado.group(0)}")
    assert restos == []
