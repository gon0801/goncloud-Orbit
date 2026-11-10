"""Orbit (app/static/js/formato.js) para los arneses de Node de las pantallas.

En el navegador, base.html emite el registro y carga formato.js antes que los
demas scripts. Los arneses corren un solo archivo sin base.html: `con_formato`
antepone al guion la carga de formato.js con el registro real
(`registro_navegador`), asi los textos que el arnes compara son los mismos que
ve la persona.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.etiquetas import registro_navegador

FORMATO_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "formato.js"


def con_formato(guion: str) -> str:
    registro = json.dumps(json.dumps(registro_navegador(), ensure_ascii=False))
    ruta = json.dumps(str(FORMATO_JS))
    preludio = (
        f'global.Orbit = new Function("document", require("node:fs").readFileSync({ruta}, "utf8")'
        f' + "\\nreturn Orbit;")({{getElementById: () => ({{textContent: {registro}}})}});\n'
    )
    return preludio + guion


_REGISTRO = re.compile(r'<script type="application/json" id="orbit-etiquetas">.*?</script>', re.S)


def sin_registro(html: str) -> str:
    """El HTML sin el registro inerte de base.html: sus claves son los codigos
    internos que la pantalla ya no muestra."""
    return _REGISTRO.sub("", html)
