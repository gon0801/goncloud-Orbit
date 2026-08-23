"""Candados de arquitectura (plan guardrails-01, tasks 1.2 y 1.3).

La muerte del sistema viejo fue el monolito (62 modulos / 206 flags / 147
jobs para 3 decisiones — Traspaso 2). Estos tests convierten las defensas en
invariantes ejecutables:

1. FRONTERAS DE IMPORTS: el motor (`app/optimizer/`) es PURO — jamas importa
   IO (`httpx`, `psycopg`, `app.ads`, `app.db`) en runtime. Unica excepcion
   declarada: `windows.py`, la puerta de datos (puede `psycopg`/`app.db`;
   jamas `httpx`/`app.ads`). Imports bajo `if TYPE_CHECKING:` se permiten:
   son anotaciones, no acoplamiento de runtime.
2. PRESUPUESTO DE TAMANO: ningun modulo de `app/` pasa de 900 lineas salvo
   entrada en la allowlist CON razon escrita. Crecer la allowlist exige
   editar este archivo = decision visible en diff y review, jamas deriva
   silenciosa. La allowlist es auto-limpiante: si un modulo listado baja del
   umbral, el test exige sacarlo.

Regla anti-Goodhart (sellada en el plan): cuando un candado dispare, las
salidas validas son simplificar de verdad o allowlist/noqa con razon escrita
que pasa por review — PROHIBIDO partir un modulo coherente en pedazos
incoherentes solo para esquivar el numero.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / "app"
OPTIMIZER = APP / "optimizer"

# El motor no habla con el mundo: ni red, ni base, ni la capa de ingesta.
PROHIBIDOS_MOTOR = ("httpx", "psycopg", "app.ads", "app.db")
# La puerta de datos (windows.py) si lee la base; la API de Amazon jamas.
PROHIBIDOS_PUERTA = ("httpx", "app.ads")

MAX_LINEAS_MODULO = 900
# path relativo (posix) -> razon escrita. Sacar una entrada exige que el
# modulo haya bajado del umbral; agregarla exige razon y review.
ALLOWLIST_TAMANO = {
    "app/ads/reports.py": (
        "pipeline compartido de reporting v3 (metricas + search terms + "
        "fusion de grano); candidato DECLARADO a partirse en "
        "report_pipeline/metrics/terms la proxima vez que se toque en grande"
    ),
    "app/cycle.py": (
        "ORBIT 03 task 3.1: orquestador del ciclo, ubicacion SELLADA por el "
        "plan (importa psycopg, fuera del motor puro) y API publica sellada "
        "(corre_ciclo + reproduce para el spot-check 4.4). Sus piezas (SQL "
        "sellada de claim/envelope/rastro, 3 fases de transaccion, "
        "serializacion congelada de inputs y replay) no tienen frontera "
        "coherente para partirse sin romper el sellado; partir por partir "
        "esta prohibido por la regla anti-Goodhart. Candidato DECLARADO "
        "(hallazgo reviewer 3.1): el codec de inputs congelados "
        "(serializacion + replay, par freeze<->replay de ~300 lineas) si "
        "3.3/PR2 hacen crecer el modulo"
    ),
}


def _imports_runtime(path: Path) -> set[str]:
    """Imports de un modulo EXCLUYENDO los bloques `if TYPE_CHECKING:`.

    Un import solo-para-tipos no acopla runtime: la frontera que este candado
    protege es la de EJECUCION (IO real), no la de anotaciones.
    """
    arbol = ast.parse(path.read_text(encoding="utf-8"))

    def _es_type_checking(nodo: ast.stmt) -> bool:
        if not isinstance(nodo, ast.If):
            return False
        test = nodo.test
        return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )

    encontrados: set[str] = set()

    def _visitar(nodos: list) -> None:
        # Imports dentro de funciones/clases tambien cuentan (IO diferido
        # sigue siendo IO); solo los subarboles TYPE_CHECKING quedan fuera.
        for nodo in nodos:
            if _es_type_checking(nodo):
                continue
            if isinstance(nodo, ast.Import):
                encontrados.update(alias.name for alias in nodo.names)
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.module and nodo.level == 0:
                    encontrados.add(nodo.module)
            else:
                _visitar(list(ast.iter_child_nodes(nodo)))

    _visitar(arbol.body)
    return encontrados


def _violaciones(imports: set[str], prohibidos: tuple[str, ...]) -> list[str]:
    return sorted(i for i in imports if any(i == p or i.startswith(p + ".") for p in prohibidos))


def test_motor_puro_sin_io():
    """Ningun modulo del motor (salvo windows.py) importa IO en runtime."""
    modulos = [p for p in OPTIMIZER.glob("*.py") if p.name not in ("windows.py", "__init__.py")]
    assert modulos, "no se encontro el motor: ¿se movio app/optimizer/?"
    fugas = {
        p.name: v for p in modulos if (v := _violaciones(_imports_runtime(p), PROHIBIDOS_MOTOR))
    }
    assert not fugas, (
        f"el motor debe ser PURO (regla 1 de la autopsia); imports de IO encontrados: {fugas}"
    )


def test_puerta_de_datos_sin_api_de_amazon():
    """windows.py puede leer la base (es la puerta), pero JAMAS la API/ingesta."""
    fugas = _violaciones(_imports_runtime(OPTIMIZER / "windows.py"), PROHIBIDOS_PUERTA)
    assert not fugas, f"la puerta de datos no habla con Amazon ni la ingesta: {fugas}"


def test_presupuesto_de_tamano_por_modulo():
    """Ningun .py de app/ pasa de 900 lineas salvo allowlist con razon."""
    excedidos = {}
    for p in APP.rglob("*.py"):
        rel = p.relative_to(RAIZ).as_posix()
        lineas = len(p.read_text(encoding="utf-8").splitlines())
        if lineas > MAX_LINEAS_MODULO and rel not in ALLOWLIST_TAMANO:
            excedidos[rel] = lineas
    assert not excedidos, (
        f"modulos sobre el presupuesto de {MAX_LINEAS_MODULO} lineas sin "
        f"entrada en la allowlist (agregar entrada CON razon o partir el "
        f"modulo — jamas partir por partir): {excedidos}"
    )


def test_allowlist_de_tamano_auto_limpiante():
    """Cada entrada de la allowlist debe (a) existir y (b) seguir excedida:
    si un modulo bajo del umbral, su entrada sobra y hay que sacarla."""
    for rel, razon in ALLOWLIST_TAMANO.items():
        p = RAIZ / rel
        assert p.is_file(), f"allowlist apunta a un modulo inexistente: {rel}"
        assert razon.strip(), f"entrada de allowlist sin razon escrita: {rel}"
        lineas = len(p.read_text(encoding="utf-8").splitlines())
        assert lineas > MAX_LINEAS_MODULO, (
            f"{rel} tiene {lineas} lineas (<= {MAX_LINEAS_MODULO}): ya no "
            f"necesita allowlist — sacar la entrada"
        )
