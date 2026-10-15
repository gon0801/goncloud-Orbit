"""Candados de arquitectura del carril F de REPRICING 02.

0.a crea el archivo para que el patron del paso de guardas del job `rapido`
tenga donde encontrar; el carril escribe aqui sus candados (los de 0.a y 0.b
viven en `tests/test_architecture.py`).

F.1: el atribuidor de Amazon solo lee. La escritura a
`ledger_event_atribucion` es de `app/ledger.py` (un escritor por tabla); si
el atribuidor trajera un verbo de escritura, una venta indecidible podria
quedar fija como fila `indeterminado` y la senal saldria `sin_dato` 60 dias.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ATRIBUCION = RAIZ / "app" / "ledger_atribucion.py"

_ESCRITURA = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|COPY)\b")


def _fugas_escritura(fuente: str) -> list[str]:
    """Verbos de escritura en literales de cadena (los comentarios no cuentan)."""
    fugas = []
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            fugas.extend(
                f"linea {nodo.lineno}: {verbo}" for verbo in _ESCRITURA.findall(nodo.value.upper())
            )
    return fugas


def test_archivo_del_carril():
    """El carril existe y este es su archivo de candados."""
    assert Path(__file__).name == "test_arq_precio_f.py"


def test_atribuidor_amazon_no_escribe():
    """F.1: `app/ledger_atribucion.py` no trae verbos de escritura."""
    assert _fugas_escritura(ATRIBUCION.read_text(encoding="utf-8")) == []


def test_candado_de_escritura_detecta_fuga_sembrada():
    """El candado si agarraria un INSERT sembrado en el atribuidor."""
    assert _fugas_escritura('db.execute("INSERT INTO t VALUES (1)")') != []
