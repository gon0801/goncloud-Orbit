"""Prueba real del bloque W-F (ARREGLOS 04), para en_prod.sh.

Funciones puras sobre el manejador HTTP registrado, sin base, sin red ni escritura.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from app.main import app

manejador = app.exception_handlers[RequestValidationError]
request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
error = RequestValidationError(
    [
        {
            "type": "decimal_parsing",
            "loc": ("body", "target_manual_pct"),
            "msg": "x",
            "input": "20%",
        }
    ]
)

respuesta = asyncio.run(manejador(request, error))
assert respuesta.status_code == 422, respuesta.status_code

detail = json.loads(respuesta.body)["detail"]
assert isinstance(detail, str), f"detail no es str: {detail!r}"
assert "Meta manual de ACoS" in detail, detail
assert "20%" not in detail, detail

print("W-F OK")
