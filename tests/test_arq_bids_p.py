"""Candados de las pantallas de BIDS 02 (carril P).

Lo crea P.3a (seccion 2) con el candado de P.1: ningun `app/pantalla_*.py`
importa `app.ads.write` ni `app.apply` (las pantallas son puro contrato:
dataclasses + `como_dict()` + lecturas SOLO SELECT). Las demas tareas P
agregan aqui sus candados; la segunda seccion en mergear une los archivos.

Corre sin Postgres y solo con la biblioteca estandar + pytest (paso de
guardas de `quality.yml`).
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
APP = RAIZ / "app"

PROHIBIDOS_PANTALLA = ("app.ads.write", "app.apply")


def _imports_runtime_pantalla(ruta: Path) -> set[str]:
    """Imports runtime de un `app/pantalla_*.py`, por modulo efectivo.

    `from app import apply` registra `app.apply` (no solo el contenedor).
    `TYPE_CHECKING` no es runtime y se excluye. Un import relativo de nivel
    1 (`from . import apply`) vive en `app/` y se resuelve como `app.*`; uno
    de nivel >= 2 sale del paquete y se marca entero.
    """
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    encontrados: set[str] = set()

    def _visitar(nodos: list[ast.stmt]) -> None:
        for nodo in nodos:
            if isinstance(nodo, ast.If):
                prueba = nodo.test
                if (
                    isinstance(prueba, ast.Name)
                    and prueba.id == "TYPE_CHECKING"
                    or isinstance(prueba, ast.Attribute)
                    and prueba.attr == "TYPE_CHECKING"
                ):
                    continue
            if isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    encontrados.add(alias.name)
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.level and nodo.level >= 2:
                    encontrados.add("<import-relativo-nivel-2>")
                else:
                    base = nodo.module or ""
                    if nodo.level == 1:
                        base = f"app.{base}" if base else "app"
                    if base:
                        encontrados.add(base)
                    for alias in nodo.names:
                        if alias.name != "*":
                            encontrados.add(f"{base}.{alias.name}" if base else alias.name)
            else:
                _visitar(list(ast.iter_child_nodes(nodo)))

    _visitar(arbol.body)
    return encontrados


def _violaciones_pantalla(imports: set[str]) -> list[str]:
    return sorted(
        i
        for i in imports
        if any(i == p or i.startswith(p + ".") for p in PROHIBIDOS_PANTALLA)
        or i == "<import-relativo-nivel-2>"
    )


def test_pantallas_no_importan_escritura():
    """Ningun `app/pantalla_*.py` importa `app.ads.write` ni `app.apply`."""
    modulos = sorted(APP.glob("pantalla_*.py"))
    assert modulos, "sin app/pantalla_*.py el candado pasaria en falso"
    fugas = {
        p.name: v for p in modulos if (v := _violaciones_pantalla(_imports_runtime_pantalla(p)))
    }
    assert not fugas, f"una pantalla no escribe a Amazon ni aplica: {fugas}"


def test_detector_caza_fuga_en_pantalla(tmp_path):
    """El detector muerde: fuga sembrada con `app.apply` y relativo nivel 2."""
    fuga = tmp_path / "pantalla_fuga.py"
    fuga.write_text(
        "from app import apply\nfrom .. import db\n",
        encoding="utf-8",
    )
    imp = _imports_runtime_pantalla(fuga)
    assert "app.apply" in imp, "from app import apply debe registrar app.apply"
    assert "<import-relativo-nivel-2>" in imp
    assert _violaciones_pantalla(imp) == ["<import-relativo-nivel-2>", "app.apply"]


def test_detector_excluye_type_checking_y_resuelve_relativo_nivel_1(tmp_path):
    """Lo legitimo no se marca: `TYPE_CHECKING` se excluye y `from . import`
    se resuelve dentro de `app/`."""
    sana = tmp_path / "pantalla_sana.py"
    sana.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app import apply\n"
        "from . import etiqueta_entidad\n"
        "from app.optimizer import bid\n",
        encoding="utf-8",
    )
    imp = _imports_runtime_pantalla(sana)
    assert "app.apply" not in imp
    assert "app.etiqueta_entidad" in imp
    assert "app.optimizer.bid" in imp
    assert _violaciones_pantalla(imp) == []
