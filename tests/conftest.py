"""Fixtures compartidas de la suite.

ORBIT 04 3.3 (`app.notifica`): el canal Telegram JAMAS sale a la red real
desde los tests (prohibicion de la task: TODO mockeado). El autouse apunta
`ORBIT_SECRETS_DIR` a un dir VACIO (canal deshabilitado) y borra el cache de
config del canal antes y despues de CADA test; los tests que necesitan el
canal lo configuran con monkeypatch (se restaura solo) y `notifica._reset()`.

R21 de JEV ADS 01 (`app.redaction`): el registro de secretos es global al
proceso. Cada test termina con el registro que encontro, para que el token
corto de un fixture no deforme los textos de las pruebas siguientes.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _canal_telegram_deshabilitado(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path_factory.mktemp("secrets_vacios")))
    monkeypatch.delenv("ORBIT_DSN_READ", raising=False)
    from app import notifica

    notifica._reset()
    yield
    notifica._reset()


@pytest.fixture(autouse=True)
def _registro_de_secretos_aislado():
    from app import redaction

    with redaction._lock:
        antes = list(redaction._secrets)
    yield
    with redaction._lock:
        redaction._secrets[:] = antes


def parte_de(nodeid: str, total: int) -> int:
    """Parte 1..total de una prueba. Cada prueba cae en una sola parte."""
    import zlib

    return zlib.crc32(nodeid.encode("utf-8")) % total + 1


def pytest_collection_modifyitems(config, items):
    # ORBIT_PARTE=i/N reparte la bateria entre N jobs de CI en paralelo: la
    # union de las N partes es la bateria entera (tests/test_partes.py).
    valor = __import__("os").environ.get("ORBIT_PARTE", "")
    if not valor:
        return
    i, n = (int(x) for x in valor.split("/"))
    if not 1 <= i <= n:
        raise pytest.UsageError(f"ORBIT_PARTE fuera de rango: {valor}")
    fuera = [item for item in items if parte_de(item.nodeid, n) != i]
    if fuera:
        config.hook.pytest_deselected(items=fuera)
        items[:] = [item for item in items if parte_de(item.nodeid, n) == i]
