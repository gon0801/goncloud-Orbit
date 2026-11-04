"""Candados de arquitectura del carril M de REPRICING 02.

0.a crea el archivo para que el patron del paso de guardas del job `rapido`
tenga donde encontrar; el carril escribe aqui sus candados (los de 0.a y 0.b
viven en `tests/test_architecture.py`).

M.4: el unico PUT de `app/` vive en `app/meli/write_client.py`
(`tools/` trae puts legacy de ads fuera del carril; caza tambien
`request("PUT")` y `getattr`); un solo importador del cliente
(`app/cli.py`; caza el alias `from`); `ClienteMeli` con inventario
cerrado de metodos (sin vacuo); `sonda` (cualquier valor no-`False`)
solo en `tools/precio_sonda.py`; `app/meli/` no importa `app.spapi.*`.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
APP = RAIZ / "app"
TOOLS = RAIZ / "tools"
REPUTACION = APP / "reputacion_clientes.py"


def test_archivo_del_carril():
    """El carril existe y este es su archivo de candados."""
    assert Path(__file__).name == "test_arq_precio_m.py"


def _es_put(nodo: ast.Call) -> bool:
    """`.put(` directo o sus evasiones (`request("PUT")`, `getattr`)."""
    func = nodo.func
    if isinstance(func, ast.Attribute) and func.attr == "put":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "request":
        for arg in nodo.args[:1]:
            if isinstance(arg, ast.Constant) and arg.value == "PUT":
                return True
        for k in nodo.keywords:
            if k.arg == "method" and isinstance(k.value, ast.Constant) and k.value.value == "PUT":
                return True
    if isinstance(func, ast.Name) and func.id == "getattr":
        for arg in nodo.args[1:2]:
            if isinstance(arg, ast.Constant) and arg.value == "put":
                return True
    return False


def _puts_por_archivo(raiz: Path) -> dict[str, list[int]]:
    """PUTs por archivo bajo `raiz` (AST, relativo a `raiz`)."""
    hallados: dict[str, list[int]] = {}
    for ruta in sorted(raiz.rglob("*.py")):
        lineas = [
            nodo.lineno
            for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8")))
            if isinstance(nodo, ast.Call) and _es_put(nodo)
        ]
        if lineas:
            hallados[ruta.relative_to(raiz).as_posix()] = lineas
    return hallados


def test_put_solo_en_write_client():
    """M.4: `MeliWriteClient` es el unico PUT de app/ (tools/ trae
    los puts legacy de ads: fabrica/reactiva, fuera del carril)."""
    assert list(_puts_por_archivo(APP)) == ["meli/write_client.py"]


def test_candado_put_caza_fuga_sembrada(tmp_path):
    """El candado si agarraria un PUT sembrado fuera del escritor."""
    (tmp_path / "fuga.py").write_text("c.put('/x')\n", encoding="utf-8")
    (tmp_path / "fuga2.py").write_text('c.request("PUT", "/x")\n', encoding="utf-8")
    (tmp_path / "fuga3.py").write_text('getattr(c, "put")("/x")\n', encoding="utf-8")
    (tmp_path / "ok.py").write_text("# c.put('/x') en comentario\n", encoding="utf-8")
    assert list(_puts_por_archivo(tmp_path)) == ["fuga.py", "fuga2.py", "fuga3.py"]


def _importadores_de(ruta_obj: str, raiz: Path) -> set[str]:
    """Modulos bajo `raiz` que importan `ruta_obj` (caza el alias
    `from app.meli import write_client`, como el precedente 0.a)."""
    hallados = set()
    for ruta in sorted(raiz.rglob("*.py")):
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                nombres = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom):
                base = nodo.module or ""
                nombres = [base] + [f"{base}.{a.name}" for a in nodo.names]
            else:
                continue
            if any(n == ruta_obj or n.startswith(ruta_obj + ".") for n in nombres):
                hallados.add(ruta.relative_to(raiz).as_posix())
    return hallados


def test_un_solo_importador_del_escritor():
    """M.4: solo `app/cli.py` importa el escritor (el adaptador lo inyecta;
    ningun tool lo compone por su cuenta)."""
    assert _importadores_de("app.meli.write_client", APP) == {"cli.py"}
    assert _importadores_de("app.meli.write_client", TOOLS) == set()


def test_candado_importador_caza_fuga_sembrada(tmp_path):
    """El candado si agarraria un segundo importador del escritor."""
    (tmp_path / "cli.py").write_text(
        "from app.meli.write_client import MeliWriteClient\n", encoding="utf-8"
    )
    (tmp_path / "fuga.py").write_text("import app.meli.write_client\n", encoding="utf-8")
    (tmp_path / "fuga2.py").write_text("from app.meli import write_client\n", encoding="utf-8")
    assert _importadores_de("app.meli.write_client", tmp_path) == {
        "cli.py",
        "fuga.py",
        "fuga2.py",
    }


_METODOS_CLIENTE_MELI = frozenset({"__init__", "close", "_request", "get", "items_seller"})


def _metodos_cliente_meli(path: Path) -> tuple[bool, set[str]]:
    """`(encontrada, metodos)` de `class ClienteMeli` (inventario cerrado:
    un metodo escritor renombrado tambien es un metodo nuevo)."""
    arbol = ast.parse(path.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ClassDef) and nodo.name == "ClienteMeli":
            return True, {
                item.name
                for item in nodo.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return False, set()


def test_cliente_meli_solo_expone_get():
    """M.4/pase del bridge: `ClienteMeli` no gana metodos (el 401 aborta
    con `pase del bridge vencido`; NO hay `_refresh` ni refresh alguno en
    Orbit; si la clase desaparece, el candado falla en vez de pasar
    vacuo)."""
    encontrada, metodos = _metodos_cliente_meli(REPUTACION)
    assert encontrada, "class ClienteMeli no encontrada"
    assert metodos <= _METODOS_CLIENTE_MELI, f"metodos nuevos: {metodos}"


def test_candado_cliente_meli_caza_metodo_sembrado(tmp_path):
    """El candado si agarraria un metodo sembrado en `ClienteMeli`."""
    (tmp_path / "rep.py").write_text(
        "class ClienteMeli:\n    def get(self): ...\n    def put(self): ...\n",
        encoding="utf-8",
    )
    encontrada, metodos = _metodos_cliente_meli(tmp_path / "rep.py")
    assert encontrada
    assert metodos - _METODOS_CLIENTE_MELI == {"put"}
    (tmp_path / "vacia.py").write_text("X = 1\n", encoding="utf-8")
    assert _metodos_cliente_meli(tmp_path / "vacia.py") == (False, set())


def _pasa_sonda(nodo: ast.Call) -> bool:
    """Llamada que ORIGINA `sonda` (caza `=True`, `=1`, variable y
    `**{"sonda": ...}`). No cuenta el reenvio puro (`sonda=sonda`, solo
    el adaptador) ni `sonda=False` explicito."""
    for k in nodo.keywords:
        if k.arg == "sonda":
            if isinstance(k.value, ast.Constant) and k.value.value is False:
                continue
            if isinstance(k.value, ast.Name) and k.value.id == "sonda":
                continue
            return True
        if k.arg is None and isinstance(k.value, ast.Dict):
            for clave in k.value.keys:
                if isinstance(clave, ast.Constant) and clave.value == "sonda":
                    return True
    return False


def _sonda_por_archivo(raiz: Path) -> dict[str, list[int]]:
    """Llamadas que originan `sonda` bajo `raiz` (los `def` no cuentan)."""
    hallados: dict[str, list[int]] = {}
    for ruta in sorted(raiz.rglob("*.py")):
        lineas = [
            nodo.lineno
            for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8")))
            if isinstance(nodo, ast.Call) and _pasa_sonda(nodo)
        ]
        if lineas:
            hallados[ruta.relative_to(raiz).as_posix()] = lineas
    return hallados


def _reenvia_sonda(raiz: Path) -> dict[str, list[int]]:
    """Llamadas con reenvio puro (`sonda=sonda`) bajo `raiz`."""
    hallados: dict[str, list[int]] = {}
    for ruta in sorted(raiz.rglob("*.py")):
        lineas = [
            nodo.lineno
            for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8")))
            if isinstance(nodo, ast.Call)
            and any(
                k.arg == "sonda" and isinstance(k.value, ast.Name) and k.value.id == "sonda"
                for k in nodo.keywords
            )
        ]
        if lineas:
            hallados[ruta.relative_to(raiz).as_posix()] = lineas
    return hallados


def test_sonda_true_solo_en_precio_sonda():
    """M.4: solo `tools/precio_sonda.py` ORIGINA `sonda` (la corrida
    nunca); el unico reenvio puro es el adaptador MeLi."""
    assert _sonda_por_archivo(APP) == {}
    assert list(_sonda_por_archivo(TOOLS)) == ["precio_sonda.py"]
    assert list(_reenvia_sonda(APP)) == ["meli/precio_mercado.py"]
    assert _reenvia_sonda(TOOLS) == {}


def test_candado_sonda_caza_fuga_sembrada(tmp_path):
    """El candado si agarraria un `sonda` sembrado en la corrida."""
    (tmp_path / "corrida.py").write_text("m.escribir(u, p, sonda=True)\n", encoding="utf-8")
    (tmp_path / "corrida2.py").write_text("m.escribir(u, p, sonda=flag)\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("m.escribir(u, p, sonda=False)\n", encoding="utf-8")
    assert list(_sonda_por_archivo(tmp_path)) == ["corrida.py", "corrida2.py"]
    (tmp_path / "corrida3.py").write_text(
        "def f(sonda):\n    m.escribir(u, p, sonda=sonda)\n", encoding="utf-8"
    )
    assert list(_reenvia_sonda(tmp_path)) == ["corrida3.py"]


def _imports_spapi_en(raiz: Path) -> dict[str, list[str]]:
    """Imports de `app.spapi.*` bajo `raiz` (los adaptadores son
    hermanos: lo compartido vive en `app/precio/`, no en el vecino)."""
    hallados: dict[str, list[str]] = {}
    for ruta in sorted(raiz.rglob("*.py")):
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        fugas = []
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                fugas.extend(a.name for a in nodo.names if a.name.startswith("app.spapi"))
            elif isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith("app.spapi"):
                fugas.append(nodo.module or "")
        if fugas:
            hallados[ruta.relative_to(raiz).as_posix()] = sorted(set(fugas))
    return hallados


def test_meli_no_importa_spapi():
    """M.4: `app/meli/` jamas importa `app.spapi.*` (lo compartido vive
    en `app/precio/`; los adaptadores no se conocen entre si)."""
    assert _imports_spapi_en(APP / "meli") == {}


def test_candado_meli_spapi_caza_fuga_sembrada(tmp_path):
    """El candado si agarraria un `import app.spapi` sembrado en meli."""
    (tmp_path / "fuga.py").write_text("import app.spapi.client\n", encoding="utf-8")
    assert list(_imports_spapi_en(tmp_path)) == ["fuga.py"]
