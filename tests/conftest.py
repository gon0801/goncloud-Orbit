"""Fixtures compartidas de la suite.

ORBIT 04 3.3 (`app.notifica`): el canal Telegram JAMAS sale a la red real
desde los tests (prohibicion de la task: TODO mockeado). El autouse apunta
`ORBIT_SECRETS_DIR` a un dir VACIO (canal deshabilitado) y borra el cache de
config del canal antes y despues de CADA test; los tests que necesitan el
canal lo configuran con monkeypatch (se restaura solo) y `notifica._reset()`.
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
