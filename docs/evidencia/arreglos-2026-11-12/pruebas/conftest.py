"""Deja correr estas pruebas de reproduccion desde la carpeta de evidencia.

Importan bancos de `tests/` por nombre de modulo y necesitan las fixtures
automaticas de la suite (canal de Telegram apagado y registro de secretos
aislado), que viven en `tests/conftest.py` y no alcanzan a esta carpeta.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RAIZ / "tests"))

_spec = importlib.util.spec_from_file_location("conftest_de_la_suite", RAIZ / "tests" / "conftest.py")
assert _spec is not None and _spec.loader is not None
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)

_canal_telegram_deshabilitado = _suite._canal_telegram_deshabilitado
_registro_de_secretos_aislado = _suite._registro_de_secretos_aislado
# Los bancos de la suite hacen `from conftest import ahora_db`; aqui `conftest` es este archivo.
ahora_db = _suite.ahora_db
parte_de = _suite.parte_de
