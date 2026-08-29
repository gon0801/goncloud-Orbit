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
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / "app"
OPTIMIZER = APP / "optimizer"

# El motor no habla con el mundo: ni red, ni base, ni la capa de ingesta.
# El marker de import relativo nivel >= 2 va en AMBAS listas: desde
# app/optimizer, ".." alcanza app y un alias ads/db escaparia del candado.
PROHIBIDOS_MOTOR = ("httpx", "psycopg", "app.ads", "app.db", "<import-relativo-nivel-2>")
# La puerta de datos (windows.py) si lee la base; la API de Amazon jamas.
PROHIBIDOS_PUERTA = ("httpx", "app.ads", "<import-relativo-nivel-2>")

MAX_LINEAS_MODULO = 900
# path relativo (posix) -> razon escrita. Sacar una entrada exige que el
# modulo haya bajado del umbral; agregarla exige razon y review.
ALLOWLIST_TAMANO = {
    "app/ads/structure.py": (
        "ORBIT 05 preflight 1.3: al modulo del sync de estructura se le sumo "
        "PATH_NEGATIVE_KEYWORDS (evidencia regla 8, 2026-08-25) y la "
        "paginacion promovida a API publica (listar_todo) para el snapshot "
        "read-only de listas (tools/snapshot_listas.py); el modulo ya vivia "
        "al tope del presupuesto (900). Candidato DECLARADO a partirse la "
        "proxima vez que se toque en grande: IO de API (evaluar_perfiles + "
        "listar_todo + fetch_structure) de IO de DB (SQL sellada + "
        "_plan_items + sync_structure) — la frontera ya esta marcada en el "
        "propio modulo; partir por partir esta prohibido por la regla "
        "anti-Goodhart"
    ),
    "app/ads/reports.py": (
        "pipeline compartido de reporting v3 (metricas + search terms + "
        "fusion de grano); candidato DECLARADO a partirse en "
        "report_pipeline/metrics/terms la proxima vez que se toque en grande"
    ),
    "app/cycle.py": (
        "ORBIT 03 task 3.1: orquestador del ciclo, ubicacion SELLADA por el "
        "plan (importa psycopg, fuera del motor puro) y API publica sellada "
        "(corre_ciclo + reproduce para el spot-check 4.4). ORBIT 04 2.4 "
        "agrego la fase de apply dentro del lock (TX4 + aplicador + guard de "
        "ownership). Sus piezas (SQL sellada de claim/envelope/rastro, fases "
        "de transaccion, serializacion congelada de inputs, replay y la fase "
        "de apply) no tienen frontera coherente para partirse sin romper el "
        "sellado; partir por partir esta prohibido por la regla "
        "anti-Goodhart. Candidato DECLARADO (hallazgo reviewer 3.1): el codec "
        "de inputs congelados (serializacion + replay, par freeze<->replay de "
        "~300 lineas)"
    ),
    "app/apply_harvest.py": (
        "ORBIT 04 2.3: ejecucion del corte harvest (cadena de fases del job, "
        "bid sugerido, reversas). La review adversaria de phase 2 le SUMO la "
        "reconciliacion de inicio de ciclo que le faltaba (pausas applying "
        "huerfanas ADV-03 + re-validacion del harvest ADV-05) y con eso paso "
        "el umbral. Candidato DECLARADO a partirse la proxima vez que se "
        "toque en grande: reconciliacion (reconcilia_harvest/_reconcilia_* + "
        "revalida_harvest) vs ejecucion de jobs (la cadena _paso_*); partir "
        "por partir esta prohibido por la regla anti-Goodhart"
    ),
    "app/apply.py": (
        "ORBIT 04 2.1: nucleo del aplicador (quota, ledger, secuencia sellada "
        "de mutaciones, reversas). La review adversaria de phase 2 le SUMO "
        "reconcilia_bids (ADV-04: el ledger de bids sin sello no tenia "
        "caller) y con eso paso el umbral (936). Mismo candidato DECLARADO "
        "que apply_harvest: partir ejecucion (Aplicador + _ejecuta_mutacion) "
        "de reconciliacion de ledger (reconcilia_bids) la proxima vez que se "
        "tome en grande; partir por partir esta prohibido por la regla "
        "anti-Goodhart"
    ),
    "app/apply_cola.py": (
        "ORBIT 04 2.2: cola de cortes (encolado, re-validacion PRE-claim, "
        "liberacion FIFO y reversas). La cross-review del dueno (codex+grok"
        "+qwen, ORBIT 04 P2) le sumo el barrido resiliente (GK3: AdsApiError "
        "por fila en la re-validacion), el verify honesto del negative "
        "(CX4/GK6) y el cruce de id del readback de estado (CX6/GK8) y con "
        "eso paso el umbral (912). Mismo candidato DECLARADO que la familia "
        "apply: partir la ejecucion sellada por fila (_revalida*/_ejecuta_*) "
        "de la maquina de encolado/liberacion la proxima vez que se toque en "
        "grande; partir por partir esta prohibido por la regla anti-Goodhart"
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
                    # "from app import ads" importa app.ads: registrar el
                    # modulo EFECTIVO de cada alias, no solo el contenedor
                    # (hallazgo CodeRabbit: sin esto el import pasaba el
                    # candado porque solo se registraba "app").
                    encontrados.update(f"{nodo.module}.{alias.name}" for alias in nodo.names)
                elif nodo.level >= 2:
                    # ".." desde app/optimizer/<modulo> alcanza app: un alias
                    # ads/db escaparia por la puerta relativa. El detector no
                    # sabe la profundidad del paquete, asi que nivel >= 2 se
                    # marca ENTERO: el motor usa imports absolutos.
                    encontrados.add("<import-relativo-nivel-2>")
            else:
                _visitar(list(ast.iter_child_nodes(nodo)))

    _visitar(arbol.body)
    return encontrados


def _violaciones(imports: set[str], prohibidos: tuple[str, ...]) -> list[str]:
    return sorted(i for i in imports if any(i == p or i.startswith(p + ".") for p in prohibidos))


def test_motor_puro_sin_io():
    """Ningun modulo del motor (salvo windows.py) importa IO en runtime.
    rglob: un subpaquete app/optimizer/<sub>/x.py con IO tambien es una fuga
    (hallazgo CodeRabbit: glob solo miraba el nivel raiz)."""
    modulos = [
        p
        for p in OPTIMIZER.rglob("*.py")
        if p.relative_to(OPTIMIZER).as_posix() not in ("windows.py", "__init__.py")
    ]
    assert modulos, "no se encontro el motor: ¿se movio app/optimizer/?"
    fugas = {
        p.relative_to(OPTIMIZER).as_posix(): v
        for p in modulos
        if (v := _violaciones(_imports_runtime(p), PROHIBIDOS_MOTOR))
    }
    assert not fugas, (
        f"el motor debe ser PURO (regla 1 de la autopsia); imports de IO encontrados: {fugas}"
    )


def test_detector_caza_import_desde_contenedor_y_relativos(tmp_path):
    """Regresion del hallazgo CodeRabbit: "from app import ads" solo
    registraba "app" (el contenedor) y pasaba el candado; el import
    relativo de nivel >= 2 (que desde app/optimizer alcanza app) tampoco
    tenia marca. Ambos deben quedar registrados."""
    fuga = tmp_path / "fuga.py"
    fuga.write_text(
        "from app import ads\nfrom .. import db\nimport httpx\n",
        encoding="utf-8",
    )
    imp = _imports_runtime(fuga)
    assert "app.ads" in imp, "from app import ads debe registrar app.ads"
    assert "<import-relativo-nivel-2>" in imp, "from .. import db debe quedar marcado"
    assert "httpx" in imp
    viol = _violaciones(imp, PROHIBIDOS_MOTOR)
    assert "app.ads" in viol and "<import-relativo-nivel-2>" in viol and "httpx" in viol


def test_detector_type_checking_excluido_y_from_normal(tmp_path):
    """La cara complementaria: lo legitimo no se marca. TYPE_CHECKING sigue
    excluido (anotacion, no runtime) y un "from app.optimizer import bid"
    registra el contenedor y el modulo efectivo SIN disparar el candado."""
    sano = tmp_path / "sano.py"
    sano.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import psycopg\n"
        "from app.optimizer import bid\n",
        encoding="utf-8",
    )
    imp = _imports_runtime(sano)
    assert "psycopg" not in imp
    assert "app.optimizer" in imp
    assert "app.optimizer.bid" in imp
    assert _violaciones(imp, PROHIBIDOS_MOTOR) == []


def test_frontera_recorre_subpaquetes(tmp_path, monkeypatch):
    """Regresion del hallazgo CodeRabbit: un subpaquete anidado con IO debe
    DISPARAR el candado (antes glob("*.py") no lo veia)."""
    import pytest

    (tmp_path / "windows.py").write_text("", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "fuga.py").write_text("import httpx\n", encoding="utf-8")
    monkeypatch.setattr("test_architecture.OPTIMIZER", tmp_path)
    with pytest.raises(AssertionError, match="sub/fuga.py"):
        test_motor_puro_sin_io()


def test_puerta_de_datos_sin_api_de_amazon():
    """windows.py puede leer la base (es la puerta), pero JAMAS la API/ingesta."""
    fugas = _violaciones(_imports_runtime(OPTIMIZER / "windows.py"), PROHIBIDOS_PUERTA)
    assert not fugas, f"la puerta de datos no habla con Amazon ni la ingesta: {fugas}"


# ORBIT 04 decision 9 (r2 codex 5): quien puede importar el cliente de
# ESCRITURA de Amazon Ads. La allowlist nombra A FUTURO — app/apply.py y
# tools/smoke_apply.py existen recien en las fases 2.x/2.5 del plan — asi
# que hoy el conjunto de importers legitimos de app/ y tools/ es vacio; los
# tests propios quedan fuera por diseno (este candado recorre app/ y
# tools/, no tests/). Crecer la allowlist exige editar este archivo =
# decision visible en diff y review, jamas derivacion silenciosa (mismo
# trato que ALLOWLIST_TAMANO).
PERMITIDOS_IMPORTAR_ADS_WRITE = {
    "app/apply.py": (
        "aplicador del modulo APPLY (fase 2.x): el dueno legitimo del "
        "cliente de escritura, que re-resuelve la escalera POR DECISION "
        "antes de construirlo"
    ),
    "tools/smoke_apply.py": (
        "smoke E2E autorizado del probe 2.5 (sellado 23): corre con "
        "ORBIT_DSN_DECIDE y sus filas de ledger nacen tipo probe"
    ),
}


def test_imports_del_cliente_de_escritura_acotados():
    """Nadie fuera de {app/apply.py, tools/smoke_apply.py} importa
    `app.ads.write` en runtime. El write client es la unica superficie que
    escribe en Amazon: colgarlo de otro modulo (una API, un job suelto)
    seria un segundo dueno de la mutacion. Imports bajo TYPE_CHECKING no
    cuentan: anotaciones, no construccion."""
    importadores: set[str] = set()
    for raiz in (APP, RAIZ / "tools"):
        for p in raiz.rglob("*.py"):
            if "app.ads.write" in _imports_runtime(p):
                importadores.add(p.relative_to(RAIZ).as_posix())

    ilegales = importadores - set(PERMITIDOS_IMPORTAR_ADS_WRITE)
    assert not ilegales, (
        f"modulos que importan app.ads.write sin estar en la allowlist "
        f"(decision 9 sellada; sumar entrada SOLO con decision del dueno): {sorted(ilegales)}"
    )
    for rel, razon in PERMITIDOS_IMPORTAR_ADS_WRITE.items():
        assert razon.strip(), f"entrada de allowlist sin razon escrita: {rel}"


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


# ORBIT 04 decision 26 (sellada): la escritura de goals tiene UN SOLO dueno —
# app/goals_write.edita_goal. El CLI y el router de escritura DESPACHAN a esa
# funcion (regla 1, una decision un camino); una segunda copia del SQL de
# ads_optimizer_goal en cualquier superficie seria una segunda fuente de
# verdad sobre como se edita un goal. Patrones SQL (no menciones de
# docstring): lo que se prohibe es CONSULTAR/MUTAR la tabla desde otro lado.
# COMPILADOS con re.IGNORECASE y \s+ entre palabras (hallazgo #5 review 3.2):
# "uPdAtE\n\tads_optimizer_goal" (case/whitespace evadido) tambien detecta;
# una frase benigna sin verbo SQL delante ("...escritura de
# ads_optimizer_goal") no dispara.
_SQL_UPDATE_GOAL = r"UPDATE\s+ads_optimizer_goal"
_PATRONES_SQL_GOALS = tuple(
    re.compile(patron, re.IGNORECASE)
    for patron in (
        r"FROM\s+ads_optimizer_goal",
        _SQL_UPDATE_GOAL,
        r"INSERT\s+INTO\s+ads_optimizer_goal",
        r"DELETE\s+FROM\s+ads_optimizer_goal",
    )
)
# El candado del escritor unico usa SOLO el UPDATE (SELECT si puede leer):
# mismo patron compilado, no una segunda copia del texto.
_PATRON_UPDATE_GOAL = re.compile(_SQL_UPDATE_GOAL, re.IGNORECASE)
MODULOS_DESPACHAN_GOALS = ("app/cli.py", "app/api_write.py")


def test_escritura_de_goals_vive_solo_en_goals_write():
    """Candado de camino unico de goals (3.2): cli.py y api_write.py (a) NO
    contienen SQL contra ads_optimizer_goal y (b) importan app.goals_write en
    runtime; y NINGUN modulo de app/ fuera de goals_write.py escribe
    `UPDATE ads_optimizer_goal` (las lecturas de cycle/api_dashboard/apply si
    pueden: SELECT no es escritura)."""
    for rel in MODULOS_DESPACHAN_GOALS:
        fuente = (RAIZ / rel).read_text(encoding="utf-8")
        sql_encontrado = [p.pattern for p in _PATRONES_SQL_GOALS if p.search(fuente)]
        assert not sql_encontrado, (
            f"{rel} contiene SQL contra ads_optimizer_goal ({sql_encontrado}): "
            "la escritura vive SOLO en app/goals_write.py (decision 26; "
            "despachar, no duplicar)"
        )
        assert "app.goals_write" in _imports_runtime(RAIZ / rel), (
            f"{rel} debe importar app.goals_write en runtime (camino unico de la edicion de goals)"
        )

    escritores = [
        p.relative_to(RAIZ).as_posix()
        for p in APP.rglob("*.py")
        if _PATRON_UPDATE_GOAL.search(p.read_text(encoding="utf-8"))
    ]
    assert escritores == ["app/goals_write.py"], (
        f"UPDATE de ads_optimizer_goal fuera de app/goals_write.py (decision "
        f"26, un solo dueno): {escritores}"
    )


def test_patrones_sql_goals_resisten_case_y_whitespace():
    """#5 (hallazgo review 3.2): el candado escaneaba cadenas LITERALES —
    "uPdAtE\\n\\tads_optimizer_goal" lo evadia con case/whitespace. Los
    patrones van compilados (IGNORECASE, \\s+): la evasion DETECTA y una frase
    benigna sin verbo SQL delante no dispara falso positivo. Limitacion
    declarada: tools/ queda fuera del alcance del candado (no se amplia aqui)."""
    assert _PATRON_UPDATE_GOAL.search("uPdAtE\n\tads_optimizer_goal")
    assert any(p.search("fRoM   ads_optimizer_goal") for p in _PATRONES_SQL_GOALS)
    benigno = "el UNICO camino de escritura de ads_optimizer_goal (decision 26)"
    assert not any(p.search(benigno) for p in _PATRONES_SQL_GOALS)


# ---------------------------------------------------------------------------
# ORBIT 05 preflight 1.3 (decision sellada 3): el snapshot de listas del
# backup pre-cutover es un TOOL del repo con test, no codigo inline. Allowlist
# POSITIVA de los imports de runtime de tools/snapshot_listas.py: stdlib + el
# cliente de LECTURA (app.ads.client), credenciales, estructura y redaccion.
# Ampliarla exige editar este archivo a proposito (mismo trato que
# ALLOWLIST_TAMANO: decision visible en diff y review, jamas deriva
# silenciosa). El tool JAMAS entra a PERMITIDOS_IMPORTAR_ADS_WRITE: no tiene
# porque importar write y el candado
# test_imports_del_cliente_de_escritura_acotados ya escanea tools/ entero.
# Sincronizada con los imports del tool (incluye los "modulo.alias" que
# _imports_runtime registra para cada from-import).
# ---------------------------------------------------------------------------
ALLOWLIST_IMPORTS_SNAPSHOT_LISTAS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "argparse",
        "datetime",
        "json",
        "os",
        "sys",
        "pathlib",
        "pathlib.Path",
        "typing",
        "typing.TYPE_CHECKING",
        "app.ads.client",
        "app.ads.client.AdsClient",
        "app.ads.config",
        "app.ads.config.AdsCredentials",
        "app.ads.structure",
        "app.ads.structure.PATH_KEYWORDS",
        "app.ads.structure.PATH_NEGATIVE_KEYWORDS",
        "app.ads.structure.PATH_TARGETS",
        "app.ads.structure.listar_todo",
        "app.ads.structure.perfiles_aceptados",
        "app.redaction",
        "app.redaction.scrub",
    }
)


def test_snapshot_listas_solo_importa_lectura():
    """El snapshot de listas es SOLO lectura: sus imports de runtime deben ser
    subconjunto de la allowlist positiva. Un import de mas es una decision de
    arquitectura: se suma EDITANDO este archivo (visible en diff y review)."""
    extras = (
        _imports_runtime(RAIZ / "tools" / "snapshot_listas.py") - ALLOWLIST_IMPORTS_SNAPSHOT_LISTAS
    )
    assert not extras, (
        f"tools/snapshot_listas.py importa por fuera de la allowlist de "
        f"lectura: {sorted(extras)} — ampliar ALLOWLIST_IMPORTS_SNAPSHOT_LISTAS "
        "exige editar tests/test_architecture.py a proposito"
    )
    assert "tools/snapshot_listas.py" not in PERMITIDOS_IMPORTAR_ADS_WRITE, (
        "el snapshot jamas debe habilitarse para importar app.ads.write"
    )


def test_allowlist_snapshot_caza_import_de_escritura(tmp_path):
    """Regla 9: si manana el tool importara app.ads.write, la allowlist
    (subconjunto) lo detecta: la copia del tool con la linea agregada REBENTA
    con el import de mas identificado (el detector muerde)."""
    fuente = (RAIZ / "tools" / "snapshot_listas.py").read_text(encoding="utf-8")
    fuga = tmp_path / "snapshot_listas_fuga.py"
    fuga.write_text(fuente + "from app.ads.write import AdsWriteClient\n", encoding="utf-8")
    imp = _imports_runtime(fuga)
    assert "app.ads.write" in _violaciones(imp, ("app.ads.write",))
    extras = imp - ALLOWLIST_IMPORTS_SNAPSHOT_LISTAS
    assert "app.ads.write" in extras and "app.ads.write.AdsWriteClient" in extras
