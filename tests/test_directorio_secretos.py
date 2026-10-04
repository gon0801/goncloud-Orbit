"""R11 (Jev Ads): `ORBIT_SECRETS_DIR` definida pero VACIA no puede caer al
directorio actual. `os.environ.get(var, default)` devuelve "" y `Path("")`
es el cwd: cualquier archivo de secretos plantado ahi se leia. Todos los
cargadores resuelven el directorio con `app.ads.config.directorio_secretos()`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.ads import config

RAIZ = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("variable", [None, ""], ids=["ausente", "vacia"])
def test_sin_variable_o_vacia_es_la_ruta_canonica(monkeypatch, variable):
    if variable is None:
        monkeypatch.delenv("ORBIT_SECRETS_DIR", raising=False)
    else:
        monkeypatch.setenv("ORBIT_SECRETS_DIR", variable)
    monkeypatch.setattr(config, "DEFAULT_SECRETS_DIR", "/ruta/canonica")
    assert config.directorio_secretos() == Path("/ruta/canonica")


def test_con_variable_es_la_variable(monkeypatch):
    monkeypatch.setenv("ORBIT_SECRETS_DIR", "/otra/ruta")
    assert config.directorio_secretos() == Path("/otra/ruta")


def test_ningun_cargador_lee_la_variable_con_default_propio():
    """El patron que cae al cwd con la variable vacia no vuelve a aparecer.
    `api_write` lee la variable a mano a proposito: vacia -> 503 fail-closed."""
    patron = re.compile(
        r"""os\.environ\.get\(\s*["']ORBIT_SECRETS_DIR["']\s*,"""
        r"""|os\.getenv\(\s*["']ORBIT_SECRETS_DIR["']"""
        r"""|os\.environ\[\s*["']ORBIT_SECRETS_DIR["']\s*\]"""
    )
    ofensores = [
        str(ruta.relative_to(RAIZ))
        for ruta in sorted((RAIZ / "app").rglob("*.py"))
        if patron.search(ruta.read_text(encoding="utf-8"))
    ]
    assert ofensores == []
