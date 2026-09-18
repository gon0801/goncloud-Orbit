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

import pytest

RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / "app"
TOOLS = RAIZ / "tools"
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
    "app/ads/reports.py": (
        "pipeline compartido de reporting v3 (metricas + search terms + "
        "fusion de grano); candidato DECLARADO a partirse en "
        "report_pipeline/metrics/terms la proxima vez que se toque en grande"
    ),
    "app/cycle.py": (
        "ORBIT 03 task 3.1: orquestador del ciclo, ubicacion SELLADA por el "
        "plan (importa psycopg, fuera del motor puro) y API publica sellada "
        "(corre_ciclo + reexport de reproduce para el spot-check 4.4). "
        "ORBIT 04 2.4 "
        "agrego la fase de apply dentro del lock (TX4 + aplicador + guard de "
        "ownership). Sus piezas (SQL sellada de claim/envelope/rastro, fases "
        "de transaccion, serializacion congelada de inputs y la fase "
        "de apply) no tienen frontera coherente para partirse sin romper el "
        "sellado; partir por partir esta prohibido por la regla "
        "anti-Goodhart. El replay puro ya vive en app/optimizer/replay.py"
    ),
    "app/apply_harvest.py": (
        "ORBIT 04 2.3 + FABRICA 02 A.3a: maquina de ejecucion del corte harvest "
        "sellada (cadena de fases del job, bid sugerido, reversas, delegados "
        "compatibles). La reconciliacion YA fue extraida a "
        "app/apply_harvest_reconciliacion.py (revalida_harvest, "
        "reconcilia_harvest y sus barridos/SQL exclusivos, sin duplicar "
        "compartidos); lo que queda aqui es ejecucion + superficie compatible "
        "y no tiene frontera coherente para partirse mas (partir por partir "
        "esta prohibido por la regla anti-Goodhart)"
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
    "app/api_dashboard.py": (
        "Feed de Decisiones/Cortes/Inertes (SQL + _fila_* + filtros). "
        "El wiring de Entidad (kind/keyword_text + JOINs de campana) "
        "lo empujo 25 lineas sobre 900. Candidato a partir cortes/"
        "inertes del feed de decisiones; no se parte por partir."
    ),
    "app/precio/corrida.py": (
        "REPRICING 01 A.5: orquestador sellado de la corrida diaria por "
        "plataforma (claim, huerfanas, cierre por observacion, decision, "
        "reparto de cupo, aplicacion live/virtual y frenos en un solo "
        "camino dueno). Partirlo crearia un segundo dueno del camino; "
        "las piezas con frontera propia ya viven fuera (cuota, reglas, "
        "fuentes, precio_write). No se parte por partir."
    ),
}


def _es_bloque_type_checking(nodo: ast.stmt) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` (solo tipos, no runtime)."""
    if not isinstance(nodo, ast.If):
        return False
    test = nodo.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _nodos_runtime(arbol: ast.AST):
    """Nodos del arbol salvo subarboles `if TYPE_CHECKING:` (r6-C4)."""
    if _es_bloque_type_checking(arbol):
        return
    yield arbol
    for hijo in ast.iter_child_nodes(arbol):
        yield from _nodos_runtime(hijo)


def _imports_runtime(path: Path) -> set[str]:
    """Imports de un modulo EXCLUYENDO los bloques `if TYPE_CHECKING:`.

    Un import solo-para-tipos no acopla runtime: la frontera que este candado
    protege es la de EJECUCION (IO real), no la de anotaciones.
    """
    arbol = ast.parse(path.read_text(encoding="utf-8"))

    encontrados: set[str] = set()

    def _visitar(nodos: list) -> None:
        # Imports dentro de funciones/clases tambien cuentan (IO diferido
        # sigue siendo IO); solo los subarboles TYPE_CHECKING quedan fuera.
        for nodo in nodos:
            if _es_bloque_type_checking(nodo):
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


def test_structure_plan_sin_io_en_runtime():
    """structure_plan es planificacion pura: sin httpx/psycopg/cliente Ads.

    ESTRUCTURA 01 (cross-review): un import runtime de structure_api
    arrastraba AdsClient -> httpx. EstructuraAds solo es anotacion.
    """
    plan = APP / "ads" / "structure_plan.py"
    imp = _imports_runtime(plan)
    prohibidos = ("httpx", "psycopg", "app.ads.client", "app.ads.structure_api", "app.db")
    fugas = sorted(p for p in prohibidos if p in imp)
    assert not fugas, f"structure_plan arrastra IO en runtime: {fugas} (imports={sorted(imp)})"


# ORBIT 04 decision 9 (r2 codex 5): quien puede importar el cliente de
# ESCRITURA de Amazon Ads. Tres importadores reales: apply (aplicador),
# smoke_apply (probe 2.5) y archivar (limpieza operada). Crecer la
# allowlist exige editar este archivo = decision visible en diff y review,
# jamas derivacion silenciosa (mismo trato que ALLOWLIST_TAMANO).
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
    "app/ads/archivar.py": (
        "limpieza OPERADA de product ads muertos (ORBIT 06, decision del "
        "dueno 2026-08-30): no es un segundo dueno de la DECISION — no lee "
        "decision ni apply_queue y solo corre cuando un humano tipea "
        "--confirmar live con una lista explicita de adIds. El CLI queda "
        "fuera de esta allowlist a proposito: delega aqui para que el "
        "importador de app.ads.write siga siendo UNO por camino"
    ),
}


def test_imports_del_cliente_de_escritura_acotados():
    """Nadie fuera de la allowlist de arriba importa
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
# ads_optimizer_goal") no dispara. Ronda PR #258 (hallazgo CodeRabbit): el
# identificador acepta esquema opcional (`public.ads_optimizer_goal`) y
# comillas dobles (`"ads_optimizer_goal"`, `"public"."ads_optimizer_goal"`):
# sin eso, calificar o entrecomillar evadia el candado en silencio.
_IDENT_GOAL = r'(?:"?\w+"?\.)?"?ads_optimizer_goal"?'
_SQL_UPDATE_GOAL = rf"UPDATE\s+{_IDENT_GOAL}"
_SQL_INSERT_GOAL = rf"INSERT\s+INTO\s+{_IDENT_GOAL}"
_SQL_FROM_GOAL = rf"FROM\s+{_IDENT_GOAL}"
_SQL_DELETE_GOAL = rf"DELETE\s+FROM\s+{_IDENT_GOAL}"
_PATRONES_SQL_GOALS = tuple(
    re.compile(patron, re.IGNORECASE)
    for patron in (
        _SQL_FROM_GOAL,
        _SQL_UPDATE_GOAL,
        _SQL_INSERT_GOAL,
        _SQL_DELETE_GOAL,
    )
)
# El candado del escritor unico usa SOLO el UPDATE (SELECT si puede leer):
# mismo patron compilado, no una segunda copia del texto.
_PATRON_UPDATE_GOAL = re.compile(_SQL_UPDATE_GOAL, re.IGNORECASE)
_PATRON_INSERT_GOAL = re.compile(_SQL_INSERT_GOAL, re.IGNORECASE)
MODULOS_DESPACHAN_GOALS = ("app/cli.py", "app/api_write.py")


def _escritores_crudos_goals(raiz: Path) -> list[str]:
    """FABRICA 02 (A.5, DoD de la fila): escritores crudos de
    `ads_optimizer_goal` bajo `<raiz>/tools/` (UPDATE o INSERT del
    patron compilado): el escaneo que
    `test_escritura_de_goals_vive_solo_en_goals_write` usa sobre el repo
    real, extraido para poder probarlo con fuga sembrada
    (`test_candado_tools_caza_update_crudo_de_goals`)."""
    return sorted(
        p.relative_to(raiz).as_posix()
        for p in (raiz / "tools").rglob("*.py")
        if _PATRON_UPDATE_GOAL.search(p.read_text(encoding="utf-8"))
        or _PATRON_INSERT_GOAL.search(p.read_text(encoding="utf-8"))
    )


def test_escritura_de_goals_vive_solo_en_goals_write():
    """Candado de camino unico de goals (3.2): cli.py y api_write.py (a) NO
    contienen SQL contra ads_optimizer_goal y (b) importan app.goals_write en
    runtime; y NINGUN modulo de app/ fuera de goals_write.py escribe
    `UPDATE` o `INSERT` de ads_optimizer_goal (las lecturas de
    cycle/api_dashboard/apply si pueden: SELECT no es escritura). FABRICA 02
    (A.1): el candado cubre tambien tools/ (la herramienta de A.5 despacha a
    goals_write, jamas escribe crudo)."""
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

    escritores_insert = [
        p.relative_to(RAIZ).as_posix()
        for p in APP.rglob("*.py")
        if _PATRON_INSERT_GOAL.search(p.read_text(encoding="utf-8"))
    ]
    assert escritores_insert == ["app/goals_write.py"], (
        f"INSERT de ads_optimizer_goal fuera de app/goals_write.py (crea_goal, "
        f"FABRICA 01): {escritores_insert}"
    )

    escritores_tools = _escritores_crudos_goals(RAIZ)
    assert escritores_tools == [], (
        f"escritura cruda de ads_optimizer_goal en tools/ (FABRICA 02 A.1: "
        f"los tools despachan a app.goals_write): {escritores_tools}"
    )


def test_candado_tools_caza_update_crudo_de_goals(tmp_path):
    """Regla 9 (FABRICA 02, A.5): el candado de escritor unico en `tools/`
    DETECTA: la copia del tool con un `UPDATE ads_optimizer_goal` crudo
    sembrado aparece listada por el helper (el detector muerde). La fuga
    se siembra en los dos tools que despachan goals."""
    tools = tmp_path / "tools"
    tools.mkdir()
    fuga = '\n# fuga sembrada (regla 9):\n_FUGA = "UPDATE ads_optimizer_goal SET x = 1"\n'
    for nombre in ("harvest_excepcion.py", "goals_modo_grupo.py"):
        (tools / nombre).write_text(
            (RAIZ / "tools" / nombre).read_text(encoding="utf-8") + fuga,
            encoding="utf-8",
        )
    assert _escritores_crudos_goals(tmp_path) == [
        "tools/goals_modo_grupo.py",
        "tools/harvest_excepcion.py",
    ]


def test_patrones_sql_goals_resisten_case_y_whitespace():
    """#5 (hallazgo review 3.2): el candado escaneaba cadenas LITERALES —
    "uPdAtE\\n\\tads_optimizer_goal" lo evadia con case/whitespace. Los
    patrones van compilados (IGNORECASE, \\s+): la evasion DETECTA y una frase
    benigna sin verbo SQL delante no dispara falso positivo. FABRICA 02
    (A.1): el alcance cubre tools/ (ver el test de escritor unico). Ronda
    PR #258 (hallazgo CodeRabbit): el identificador con esquema
    (`public.ads_optimizer_goal`) o entre comillas (`"ads_optimizer_goal"`)
    tambien DETECTA; las menciones benignas con esquema o comillas pero SIN
    verbo SQL no disparan."""
    assert _PATRON_UPDATE_GOAL.search("uPdAtE\n\tads_optimizer_goal")
    assert any(p.search("fRoM   ads_optimizer_goal") for p in _PATRONES_SQL_GOALS)
    assert _PATRON_UPDATE_GOAL.search("UPDATE public.ads_optimizer_goal SET x = 1")
    assert _PATRON_INSERT_GOAL.search('INSERT INTO "ads_optimizer_goal" (a)')
    assert _PATRON_UPDATE_GOAL.search('UPDATE "public"."ads_optimizer_goal" SET x = 1')
    assert any(p.search("DELETE FROM public.ads_optimizer_goal") for p in _PATRONES_SQL_GOALS)
    benigno = "el UNICO camino de escritura de ads_optimizer_goal (decision 26)"
    assert not any(p.search(benigno) for p in _PATRONES_SQL_GOALS)
    for variante in (
        "el UNICO camino de escritura de public.ads_optimizer_goal (decision 26)",
        'el UNICO camino de escritura de "ads_optimizer_goal" (decision 26)',
    ):
        assert not any(p.search(variante) for p in _PATRONES_SQL_GOALS), variante
    assert TOOLS.is_dir(), "el candado de escritor unico escanea tools/"


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
        # contextlib / stat / tempfile: endurecimiento de la escritura
        # (hallazgos Greptile + CodeRabbit PR #48) — temporal EXCLUSIVO con
        # mkstemp (nada de nombre predecible), modo 700 impuesto al out_dir
        # preexistente y limpieza del temporal si algo revienta.
        "contextlib",
        "datetime",
        "json",
        "os",
        "stat",
        "sys",
        "tempfile",
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
    arquitectura: se suma EDITANDO este archivo (visible en diff y review).

    Residual DECLARADO (hallazgo reviewer 1.3): la allowlist nombra
    app.ads.structure ENTERO, asi que un caller hipotetico podria llegar a
    sync_structure por atributo sin disparar — granularidad aceptada: el
    modelo de amenaza es deriva accidental, no malicia, y reusar structure es
    el diseno (reusar, no reescribir)."""
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
    # Cierre barato del hueco AST (hallazgo reviewer 1.3): __import__("...")
    # y importlib.import_module no producen nodos de import y la allowlist no
    # los ve. Un tool read-only no tiene razon legitima de import dinamico:
    # escaneo de texto; ampliarlo exige editar este archivo a proposito.
    fuente = (RAIZ / "tools" / "snapshot_listas.py").read_text(encoding="utf-8")
    for patron in ("__import__(", "import_module("):
        assert patron not in fuente, (
            f"tools/snapshot_listas.py usa import dinamico ({patron!r}): el "
            "candado de allowlist no lo ve — justificarlo y editar "
            "tests/test_architecture.py a proposito"
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


# FABRICA 01 (plans/fabrica-01.md tarea 10): allowlist POSITIVA de los imports
# de runtime de tools/fabrica_campanas.py (mismo trato que snapshot_listas).
# El tool MUTA Amazon con HTTP propio: jamas app.ads.write (candado
# test_imports_del_cliente_de_escritura_acotados) y sus escrituras internas
# van SOLO por los caminos unicos: app.goals_write (goals) y
# app.ads.structure.sync_structure (ad_entity). Ampliarla = editar este
# archivo a proposito. D-GLM-7-10-7: lista = imports REALES post 7-9.
ALLOWLIST_IMPORTS_FABRICA_CAMPANAS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "argparse",
        "contextlib",
        "dataclasses",
        "dataclasses.dataclass",
        "dataclasses.field",
        "datetime",
        "decimal",
        "decimal.Decimal",
        "decimal.InvalidOperation",
        "decimal.ROUND_HALF_EVEN",
        "json",
        "logging",
        "os",
        "sys",
        "time",
        "typing",
        "typing.Any",
        "httpx",
        "psycopg",
        "psycopg.rows",
        "psycopg.rows.tuple_row",
        "app",
        "app.fabrica_plan",
        "app.goals_write",
        "app.ads.client",
        "app.ads.client.DEFAULT_BASE_URL",
        "app.ads.client.AdsClient",
        "app.ads.config",
        "app.ads.config.AdsCredentials",
        "app.ads.structure",
        "app.ads.structure.evaluar_perfiles",
        "app.ads.structure.fetch_structure",
        "app.ads.structure.sync_structure",
        "app.db",
        "app.db.connect",
        "app.optimizer.goals",
        "app.optimizer.goals.fraccion_desde_settings",
        "app.redaction",
        "app.redaction.install_scrub_filter",
        "app.redaction.register_secret",
        "app.redaction.scrub",
    }
)


def test_fabrica_campanas_solo_importa_lo_declarado():
    extras = (
        _imports_runtime(RAIZ / "tools" / "fabrica_campanas.py")
        - ALLOWLIST_IMPORTS_FABRICA_CAMPANAS
    )
    assert not extras, (
        f"tools/fabrica_campanas.py importa por fuera de su allowlist: {sorted(extras)} — "
        "ampliar ALLOWLIST_IMPORTS_FABRICA_CAMPANAS exige editar tests/test_architecture.py"
    )
    assert "tools/fabrica_campanas.py" not in PERMITIDOS_IMPORTAR_ADS_WRITE
    fuente = (RAIZ / "tools" / "fabrica_campanas.py").read_text(encoding="utf-8")
    for patron in ("__import__(", "import_module(", "app.apply"):
        assert patron not in fuente, f"tools/fabrica_campanas.py usa {patron!r}"


def test_fabrica_guard_main_es_lo_ultimo_del_archivo():
    """FABRICA 01: el tool entra por stdin (`python - < file`) y main()
    despacha funciones definidas mas abajo; el guard __main__ es LO ULTIMO."""
    lineas = [
        linea
        for linea in (RAIZ / "tools" / "fabrica_campanas.py")
        .read_text(encoding="utf-8")
        .splitlines()
        if linea.strip() and not linea.strip().startswith("#")
    ]
    idx = next(
        i for i, linea in enumerate(lineas) if linea.startswith('if __name__ == "__main__":')
    )
    resto = lineas[idx + 1 :]
    assert all(linea.startswith((" ", "\t")) for linea in resto), (
        f"codigo top-level despues del guard __main__ (linea {idx}): {resto}"
    )


def test_allowlist_fabrica_caza_import_de_escritura(tmp_path):
    """Regla 9: la copia del tool con `from app.ads.write import AdsWriteClient`
    queda fuera de la allowlist Y dispara el candado general."""
    fuente = (RAIZ / "tools" / "fabrica_campanas.py").read_text(encoding="utf-8")
    fuga = tmp_path / "fabrica_fuga.py"
    fuga.write_text(fuente + "from app.ads.write import AdsWriteClient\n", encoding="utf-8")
    imp = _imports_runtime(fuga)
    assert "app.ads.write" in _violaciones(imp, ("app.ads.write",))
    assert "app.ads.write" in imp - ALLOWLIST_IMPORTS_FABRICA_CAMPANAS


# FABRICA 02 (A.5): allowlist POSITIVA de los imports de runtime de
# tools/harvest_excepcion.py (mismo trato que fabrica_campanas): el tool es
# solo Postgres + app_admin, cero Amazon, cero apply. Solo stdlib de CLI
# (argparse, datetime, hashlib, os, sys), app.db (connect), app.goals_write
# (el UNICO camino de escritura de goals, que el tool despacha) y
# app.optimizer.harvest_destino (lectura + simulacion del destino).
# Sincronizada con los imports reales (incluye los "modulo.alias" que
# _imports_runtime registra por cada from-import). Ampliarla = editar este
# archivo a proposito.
ALLOWLIST_IMPORTS_HARVEST_EXCEPCION = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "argparse",
        "datetime",
        "hashlib",
        "os",
        "sys",
        "app.db",
        "app.db.OrbitDbError",
        "app.db.connect",
        "app.goals_write",
        "app.goals_write.GoalInexistente",
        "app.goals_write.GoalInvalido",
        "app.goals_write.edita_goal",
        "app.optimizer.harvest_destino",
        "app.optimizer.harvest_destino.DestinoHarvest",
        "app.optimizer.harvest_destino.RESUELTO_EXCEPCION",
        "app.optimizer.harvest_destino.RESUELTO_GRUPO",
        "app.optimizer.harvest_destino.SaltoHarvest",
        "app.optimizer.harvest_destino.resolver_destino",
        "app.optimizer.harvest_destino.simula_excepcion",
    }
)


def test_harvest_excepcion_solo_importa_lo_declarado():
    """A.5: el tool solo importa lo declarado (stdlib CLI + app.db +
    app.goals_write + harvest_destino). Un import de mas (Amazon, apply,
    red) es una decision de arquitectura: se suma EDITANDO este archivo.
    """
    extras = (
        _imports_runtime(RAIZ / "tools" / "harvest_excepcion.py")
        - ALLOWLIST_IMPORTS_HARVEST_EXCEPCION
    )
    assert not extras, (
        f"tools/harvest_excepcion.py importa por fuera de su allowlist: {sorted(extras)} — "
        "ampliar ALLOWLIST_IMPORTS_HARVEST_EXCEPCION exige editar "
        "tests/test_architecture.py"
    )
    assert "tools/harvest_excepcion.py" not in PERMITIDOS_IMPORTAR_ADS_WRITE, (
        "el tool jamas debe habilitarse para importar app.ads.write"
    )
    fuente = (RAIZ / "tools" / "harvest_excepcion.py").read_text(encoding="utf-8")
    for patron in ("__import__(", "import_module(", "app.apply", "httpx"):
        assert patron not in fuente, f"tools/harvest_excepcion.py usa {patron!r}"


def test_allowlist_harvest_excepcion_caza_import_de_escritura(tmp_path):
    """Regla 9: la copia del tool con `from app.ads.write import AdsWriteClient`
    queda fuera de la allowlist Y dispara el candado general."""
    fuente = (RAIZ / "tools" / "harvest_excepcion.py").read_text(encoding="utf-8")
    fuga = tmp_path / "excepcion_fuga.py"
    fuga.write_text(fuente + "from app.ads.write import AdsWriteClient\n", encoding="utf-8")
    imp = _imports_runtime(fuga)
    assert "app.ads.write" in _violaciones(imp, ("app.ads.write",))
    assert "app.ads.write" in imp - ALLOWLIST_IMPORTS_HARVEST_EXCEPCION


# Modo de goals con ceremonia (precondicion de D.3): allowlist POSITIVA de
# los imports de runtime de tools/goals_modo_grupo.py (mismo trato que
# harvest_excepcion). El tool MUTA goals SOLO por app.goals_write.edita_goal
# y lee el meet de app.optimizer.goals (reusar, no reescribir); jamas Amazon,
# apply ni red. Ampliarla = editar este archivo a proposito.
ALLOWLIST_IMPORTS_GOALS_MODO_GRUPO = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "argparse",
        "datetime",
        "hashlib",
        "os",
        "sys",
        "app.db",
        "app.db.OrbitDbError",
        "app.db.connect",
        "app.goals_write",
        "app.goals_write.GoalInexistente",
        "app.goals_write.GoalInvalido",
        "app.goals_write.edita_goal",
        "app.optimizer.goals",
        "app.optimizer.goals.CLAVE_SETTING_MODO",
        "app.optimizer.goals.modo_desde_settings",
        "app.optimizer.goals.modo_efectivo",
    }
)


def test_goals_modo_grupo_solo_importa_lo_declarado():
    """El tool de modo de grupo solo importa lo declarado (stdlib CLI +
    app.db + app.goals_write + app.optimizer.goals). Un import de mas
    (Amazon, apply, red) es una decision de arquitectura: se suma
    EDITANDO este archivo."""
    extras = (
        _imports_runtime(RAIZ / "tools" / "goals_modo_grupo.py")
        - ALLOWLIST_IMPORTS_GOALS_MODO_GRUPO
    )
    assert not extras, (
        f"tools/goals_modo_grupo.py importa por fuera de su allowlist: {sorted(extras)} — "
        "ampliar ALLOWLIST_IMPORTS_GOALS_MODO_GRUPO exige editar "
        "tests/test_architecture.py"
    )
    assert "tools/goals_modo_grupo.py" not in PERMITIDOS_IMPORTAR_ADS_WRITE, (
        "el tool jamas debe habilitarse para importar app.ads.write"
    )
    fuente = (RAIZ / "tools" / "goals_modo_grupo.py").read_text(encoding="utf-8")
    for patron in ("__import__(", "import_module(", "app.apply", "httpx"):
        assert patron not in fuente, f"tools/goals_modo_grupo.py usa {patron!r}"


def test_allowlist_goals_modo_grupo_caza_import_de_escritura(tmp_path):
    """Regla 9: la copia del tool con `from app.ads.write import AdsWriteClient`
    queda fuera de la allowlist Y dispara el candado general."""
    fuente = (RAIZ / "tools" / "goals_modo_grupo.py").read_text(encoding="utf-8")
    fuga = tmp_path / "modo_grupo_fuga.py"
    fuga.write_text(fuente + "from app.ads.write import AdsWriteClient\n", encoding="utf-8")
    imp = _imports_runtime(fuga)
    assert "app.ads.write" in _violaciones(imp, ("app.ads.write",))
    assert "app.ads.write" in imp - ALLOWLIST_IMPORTS_GOALS_MODO_GRUPO


def test_fabrica_plan_es_puro():
    """app/fabrica_plan.py no importa IO (misma frontera que el motor)."""
    fugas = _violaciones(_imports_runtime(RAIZ / "app" / "fabrica_plan.py"), PROHIBIDOS_MOTOR)
    assert not fugas, f"app/fabrica_plan.py debe ser puro: {fugas}"


# ---------------------------------------------------------------------------
# UNA SOLA FUENTE DE MONEDA POR PLATAFORMA (correccion del lead, ORBIT 06 0.2)
# ---------------------------------------------------------------------------


# Las UNICAS definiciones permitidas del mapa plataforma -> moneda, cada una
# con su razon declarada EN EL CODIGO:
#   - app/optimizer/bid.py : PLATAFORMAS_MONEDA, la fuente de la capa de
#     DECISIONES; la importan app/api.py, app/api_dashboard.py y app/listings.py.
#   - app/ads/write.py     : PLATAFORMA_MONEDA, congelada (MappingProxyType) y
#     declarada a proposito como el mapa de la capa HTTP ("capa distinta, misma
#     ley"). DEUDA CONOCIDA: consolidarla con la del motor es candidato de
#     revision, pero NO se toca el cliente de escritura sellado por una
#     constante — se declara y se revisa en su fase.
#   - app/ads/structure_api.py : _PAIS_PLATAFORMA_MONEDA, forma DISTINTA
#     (codigo de pais -> (plataforma, moneda)) y proposito distinto: resolver
#     el perfil de Amazon durante el discovery, no decidir dinero. Se declara
#     porque codifica la misma ley en otra forma. (Hallazgo de la cross-review
#     kimi 2026-08-30: la version anterior de este candado NO la veia —solo
#     miraba claves `amazon_*` con valores string— y el commit afirmaba que
#     solo habia tres definiciones. Eran CUATRO, y el comentario original de
#     app/listings.py que la citaba tenia razon.)
DEFINICIONES_MONEDA_DECLARADAS = frozenset(
    {"app/optimizer/bid.py", "app/ads/write.py", "app/ads/structure_api.py"}
)

_MONEDAS_CONOCIDAS = ("MXN", "USD")


def _es_moneda(nodo: ast.expr) -> bool:
    """El valor codifica una moneda: 'MXN'/'USD' suelto o dentro de una tupla."""
    if isinstance(nodo, ast.Constant):
        return nodo.value in _MONEDAS_CONOCIDAS
    if isinstance(nodo, ast.Tuple):
        return any(isinstance(e, ast.Constant) and e.value in _MONEDAS_CONOCIDAS for e in nodo.elts)
    return False


def _mapas_de_moneda(raiz: Path = RAIZ) -> dict[str, list[int]]:
    """Modulos de app/ que definen un dict literal que ata algo a una moneda.

    Detecta las DOS formas vistas en el repo: `{"amazon_xx": "MXN"}` (mapa de
    la capa de decisiones / HTTP) y `{"MX": ("amazon_mx", "MXN")}` (el de
    discovery de perfiles). El criterio es el VALOR —que sea o contenga una
    moneda— y no la forma de la clave: mirar solo claves `amazon_*` dejaba
    pasar la segunda forma (hallazgo kimi 2026-08-30).

    `raiz` es parametro para que el test de poder discriminante trabaje sobre
    un arbol temporal y NO escriba dentro del repo (mismo hallazgo).
    """
    hallazgos: dict[str, list[int]] = {}
    for py in sorted((raiz / "app").rglob("*.py")):
        arbol = ast.parse(py.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Dict):
                continue
            # El criterio es el VALOR, y se evalua sobre los valores que se
            # pueden leer estaticamente. Las claves NO se filtran (hallazgo 1
            # de qwen, 2026-08-30): exigir que TODAS fueran constantes
            # ACHICABA la deteccion — un mapa con claves mixtas
            # (`{plataforma: "MXN", "amazon_mx": "MXN"}`) o con `**base` se
            # escapaba, que es el punto ciego inverso al que se corrigio.
            # Con `**`, ast pone key=None: esa entrada NO es evidencia y se
            # ignora, pero el resto del dict si se evalua.
            #
            # Lo que NO se ignora es un valor calculado en una entrada normal
            # (hallazgo MEDIA de glm-5.3, 2026-08-30): saltar todo lo no
            # literal hacia marcar `{"mx": "MXN", "tasa": get_tasa()}` como
            # mapa de moneda, un ensanchamiento mas alla del `**` que el
            # commit anterior documentaba, y sin fixture que lo sellara. Por
            # eso el criterio se aplica a TODAS las entradas con clave real:
            # si alguna trae un valor que no se puede leer estaticamente, el
            # dict no califica.
            entradas = [v for k, v in zip(nodo.keys, nodo.values, strict=True) if k is not None]
            if entradas and all(_es_moneda(v) for v in entradas):
                rel = py.relative_to(raiz).as_posix()
                hallazgos.setdefault(rel, []).append(nodo.lineno)
    return hallazgos


def test_una_sola_fuente_de_moneda_por_plataforma():
    """Regla 2 (una sola fuente por numero) aplicada a la moneda.

    La fuente es `PLATAFORMAS_MONEDA` de app/optimizer/bid.py y NADIE mas
    define su propio mapa: se importa. Hoy dos mapas coincidirian, pero un
    tercer marketplace actualizaria uno y no el otro, y en este proyecto ese
    error ya se pago caro (sales_history reportando MXN para amazon_us: 18.66x
    SIEMPRE a favor de "todo es rentabilisimo").

    Historia: la entrega de la 0.2 definia `MONEDA_POR_PLATAFORMA` en
    app/listings.py, identico al del motor. El lead lo unifico al importarlo.
    """
    hallazgos = _mapas_de_moneda()
    extras = set(hallazgos) - DEFINICIONES_MONEDA_DECLARADAS
    assert not extras, (
        f"mapa de moneda por plataforma NO declarado en: {sorted(extras)}. "
        "Importa PLATAFORMAS_MONEDA de app/optimizer/bid.py en vez de escribir "
        "uno propio (el motor es puro y no importa de afuera, pero importar DEL "
        "motor si esta permitido, y es lo que ya hacen api.py y api_dashboard.py). "
        "Si de verdad hace falta otra definicion, se agrega a "
        "DEFINICIONES_MONEDA_DECLARADAS con su razon escrita, como las dos que hay."
    )
    assert hallazgos, "no se encontro NINGUN mapa de moneda: el detector dejo de morder"
    # La allowlist se limpia sola: una entrada que ya no define un mapa es
    # permiso muerto que deja pasar una duplicacion futura sin avisar (mismo
    # criterio que ALLOWLIST_TAMANO).
    obsoletas = DEFINICIONES_MONEDA_DECLARADAS - set(hallazgos)
    assert not obsoletas, (
        f"DEFINICIONES_MONEDA_DECLARADAS tiene entradas que ya no definen un "
        f"mapa de moneda: {sorted(obsoletas)}. Quitalas: un permiso muerto "
        "deja pasar la proxima duplicacion en silencio."
    )


def test_detector_de_moneda_caza_las_dos_formas(tmp_path):
    """Regla 9: el candado MUERDE, y muerde las DOS formas que existen.

    Trabaja sobre un arbol TEMPORAL, jamas sobre `app/` del repo: la version
    anterior escribia un modulo de prueba dentro del arbol fuente y lo borraba
    en un `finally` — si el proceso moria en medio quedaba basura en el repo, y
    con pytest-xdist otros tests que barren `app/` podian ver el fantasma
    (hallazgo kimi 2026-08-30).
    """
    app = tmp_path / "app"
    app.mkdir()
    (app / "plano.py").write_text(
        'MIO = {"amazon_mx": "MXN", "amazon_us": "USD"}\n', encoding="utf-8"
    )
    (app / "por_pais.py").write_text(
        'OTRO = {"MX": ("amazon_mx", "MXN"), "US": ("amazon_us", "USD")}\n', encoding="utf-8"
    )
    (app / "inocente.py").write_text('CAPS = {"bid": 10, "pause": 2}\n', encoding="utf-8")
    # Hallazgo 4 de qwen (2026-08-30): las reglas de descarte del detector no
    # estaban selladas por ningun fixture, y son justo el codigo del cuerpo.
    (app / "claves_mixtas.py").write_text(
        'P = "amazon_mx"\nMIXTO = {P: "MXN", "amazon_us": "USD"}\n', encoding="utf-8"
    )
    (app / "esparcido.py").write_text(
        'BASE = {}\nCON_SPREAD = {**BASE, "amazon_mx": "MXN"}\n', encoding="utf-8"
    )
    (app / "vacio.py").write_text("NADA = {}\n", encoding="utf-8")
    # Hallazgo MEDIA de glm-5.3 (2026-08-30): una moneda literal conviviendo
    # con un valor CALCULADO no es un mapa de moneda, y la version anterior
    # del detector la marcaba porque ignoraba todo lo no literal.
    (app / "con_calculo.py").write_text(
        'def t(): return 1\nMIXTO_VALOR = {"mx": "MXN", "tasa": t()}\n', encoding="utf-8"
    )

    hallazgos = _mapas_de_moneda(tmp_path)

    assert "app/plano.py" in hallazgos, "no caza la forma plataforma -> moneda"
    assert "app/por_pais.py" in hallazgos, (
        "no caza la forma pais -> (plataforma, moneda): es la que se le escapo "
        "a la primera version del candado"
    )
    assert "app/claves_mixtas.py" in hallazgos, (
        "no caza un mapa con claves mixtas: es el punto ciego que introdujo la "
        "correccion anterior al exigir que TODAS las claves fueran constantes"
    )
    assert "app/esparcido.py" in hallazgos, (
        "no caza un mapa con `**base`: el spread no es evidencia, pero el resto "
        "del dict si se evalua"
    )
    assert "app/inocente.py" not in hallazgos, "falso positivo: un dict sin monedas no cuenta"
    assert "app/vacio.py" not in hallazgos, "falso positivo: un dict vacio no es un mapa de moneda"
    assert "app/con_calculo.py" not in hallazgos, (
        "falso positivo: una moneda literal junto a un valor CALCULADO no es un "
        "mapa de moneda; ignorar lo no literal ensanchaba el candado de mas"
    )


def test_biblioteca_sin_apply_ni_escritura_ni_red():
    """FABRICA 02 (A.4): `app/biblioteca.py` es contabilidad derivada — no
    importa modulos de apply (ellos importan AQUI: un import inverso
    cerraria el ciclo y engordaria modulos ya en allowlist de tamano),
    ni `app.ads.write` (la biblioteca no habla con Amazon; el candado
    general ya lo cubre y aqui queda la razon junto a los apply), ni
    `httpx` (cero red). Solo `psycopg`, higiene (normalizacion) y
    notifica (alerta)."""
    prohibidos = (
        "app.apply",
        "app.apply_cola",
        "app.apply_harvest",
        "app.apply_harvest_reconciliacion",
        "app.ads.write",
        "httpx",
    )
    fugas = _violaciones(_imports_runtime(RAIZ / "app" / "biblioteca.py"), prohibidos)
    assert not fugas, f"app/biblioteca.py importa fuera de su frontera: {fugas}"


def test_spapi_vigilante_sin_ads_directo():
    """Vigilante SP-API: misma regla que salud.py — cero import DIRECTO
    de app.ads (AST, ni siquiera diferido en funciones). Solo stdlib CLI
    + app.db (lectura) + app.notifica (aviso) + app.redaction (scrub) +
    app.spapi.salud (constantes)."""
    fugas = _violaciones(_imports_runtime(RAIZ / "app" / "spapi" / "vigilante.py"), ("app.ads",))
    assert not fugas, f"app/spapi/vigilante.py importa app.ads directo: {fugas}"


# REPRICING 01 A.2: el motor de precios es PURO. `app/estimacion_fees.py`
# entero hace I/O (httpx, psycopg), por eso `cotizar_a_precio` vive ahi y
# `app/precio/*` NO lo importa: ni red, ni base, ni Ads/SP-API, ni la capa
# de cotizacion. El reloj tampoco entra: `hoy` y todo dato llegan como
# argumento. Si importa tipos puros de `app.estimacion_venta` (frozen,
# Decimal, sin I/O). Candado en paralelo al de `app/optimizer/*`, sin
# tocar lo existente.
PRECIO = APP / "precio"
PROHIBIDOS_PRECIO = (
    "httpx",
    "psycopg",
    "app.db",
    "app.ads",
    "app.spapi",
    "app.estimacion_fees",
    "time",
    "os",
    "random",
    "secrets",
    "importlib",
    "<import-relativo-nivel-2>",
)


def _usos_reloj(arbol: ast.AST) -> list[str]:
    """Reloj o entorno en el AST, sin importar el dueño: cualquier acceso
    a `.now`, `.utcnow` o `.today` (`dt.now()`, `datetime.datetime.now()`
    y la referencia sin llamada `reloj = dt.now` incluidos) y cualquier
    `os.environ`. Las CLASES datetime/date pueden aparecer (firman los
    argumentos de fecha); USARLAS como reloj, no. `time.time()` cae
    SOLO por el import prohibido de `time` (este candado no lo ve: `time`
    no es `.now`/`.utcnow`/`.today`). `time.*` y `os.*` caen además por
    el import prohibido (`time.monotonic`, `os.getenv` necesitan
    importarse). R4-G9: la referencia sin llamada tambien es reloj; solo
    cazar el Call dejaba escapar el alias. R4b-H2: `ast.walk` visita el
    `Call` Y su `Attribute` interno, asi que la rama del `Call` era
    redundante: una sola tupla en la rama del `Attribute`."""
    hallados: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Attribute):
            dueno = nodo.value
            if isinstance(dueno, ast.Name) and dueno.id == "os" and nodo.attr == "environ":
                hallados.append("os.environ")
            elif nodo.attr in ("now", "utcnow", "today"):
                hallados.append(f".{nodo.attr}")
    return hallados


def _puros_precio(raiz=None):
    base = raiz or PRECIO
    return [
        p
        for p in base.rglob("*.py")
        if p.relative_to(base).as_posix() not in EXCEPCIONES_PURAS_PRECIO
    ]


# REPRICING 01 A.1: `goals_write.py` es el unico escritor de `precio_goal`
# (su candado de imports aplica `PROHIBIDOS_PRECIO` completo salvo
# `psycopg`, lo unico que la excepcion levanta; reloj/entorno e import
# dinamico los cubren `_usos_reloj` y `_usos_import_dinamico`, con una
# fuga sembrada para cada caso; su candado propio vive en
# `tests/test_precio_goals.py`). Excepcion POR NOMBRE: el `rglob` sigue
# cubriendo todo lo demas de `app/precio/`.
# REPRICING 01 A.7: `fuentes.py` es el lector de cobertura (puede `psycopg`,
# `app.precio.*`, `app.estimacion_insumos` solo `mapear_canal`, y stdlib;
# jamas red, reloj, `app.spapi.*` ni escritura: su candado propio esta al
# final del archivo). Excepcion POR NOMBRE, en paralelo a la de A.1.
EXCEPCIONES_PURAS_PRECIO = ("goals_write.py", "fuentes.py", "corrida.py", "cuota.py")


def test_precio_excepcion_por_nombre():
    """A.1 + A.7 + A.5: la excepcion de pureza es una tupla por nombre con
    `goals_write.py`, `fuentes.py`, `corrida.py` y `cuota.py` y nada mas;
    quitar el `rglob` o exceptuar la carpeta la rompe."""
    assert EXCEPCIONES_PURAS_PRECIO == ("goals_write.py", "fuentes.py", "corrida.py", "cuota.py")
    assert (PRECIO / "goals_write.py").is_file()
    assert (PRECIO / "fuentes.py").is_file()
    assert (PRECIO / "corrida.py").is_file()
    assert (PRECIO / "cuota.py").is_file()
    assert (PRECIO / "tipos.py").is_file()


def test_precio_puro_sin_io():
    """Ningun modulo de `app/precio/` importa I/O en runtime (rglob: un
    subpaquete anidado con IO tambien es fuga)."""
    modulos = _puros_precio()
    assert modulos, "no se encontro el motor de precios: ¿se movio app/precio/?"
    fugas = {
        p.relative_to(PRECIO).as_posix(): v
        for p in modulos
        if (v := _violaciones(_imports_runtime(p), PROHIBIDOS_PRECIO))
    }
    assert not fugas, f"app/precio debe ser PURO; imports de IO encontrados: {fugas}"


def _usos_import_dinamico(arbol: ast.AST) -> list[str]:
    """`__import__` por AST: `Name` con `id == "__import__"` o `Attribute`
    con `attr == "__import__"` (`__import__ ("httpx")` con espacio y
    `builtins.__import__("httpx")` incluidos; r6-C3: el barrido de texto
    `"__import__("` no ve el espacio)."""
    hallados: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Name) and nodo.id == "__import__":
            hallados.append("__import__")
        elif isinstance(nodo, ast.Attribute) and nodo.attr == "__import__":
            hallados.append(f".{nodo.attr}")
    return hallados


def test_precio_sin_import_dinamico():
    """Ni `import importlib` ni `__import__("...")` en `app/precio/*`.

    R5-J5: el import dinámico no produce nodos de import y el candado
    de `test_precio_puro_sin_io` no lo ve (`importlib` sí cae por
    `PROHIBIDOS_PRECIO`; `__import__` por este detector AST).
    Precedente: el candado de `snapshot_listas` en este mismo archivo.
    """
    modulos = _puros_precio()
    assert modulos, "no se encontro el motor de precios: ¿se movio app/precio/?"
    fugas = {
        p.relative_to(PRECIO).as_posix(): v
        for p in modulos
        if (v := _usos_import_dinamico(ast.parse(p.read_text(encoding="utf-8"))))
    }
    assert not fugas, f"app/precio usa import dinamico: {fugas}"


def test_precio_frontera_caza_importlib_dinamico(tmp_path, monkeypatch):
    """R5-J5, fuga sembrada: `importlib.import_module("psycopg")` dispara
    el candado de imports."""
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "dyn.py").write_text(
        'import importlib\nx = importlib.import_module("psycopg")\n', encoding="utf-8"
    )
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="dyn.py"):
        test_precio_puro_sin_io()


@pytest.mark.parametrize(
    "cuerpo",
    [
        'x = __import__("httpx")\n',
        'x = __import__ ("httpx")\n',
        'import builtins\nx = builtins.__import__("httpx")\n',
    ],
)
def test_precio_frontera_caza_dunder_import(tmp_path, monkeypatch, cuerpo):
    """R5-J5, fuga sembrada: `__import__("httpx")` dispara el detector
    (r6-C3: también con espacio y por atributo)."""
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "dyn.py").write_text(cuerpo, encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="dyn.py"):
        test_precio_sin_import_dinamico()


def test_precio_sin_reloj_ni_entorno():
    """`hoy` entra como argumento: ni datetime.now, ni date.today, ni
    time.time, ni os.environ en el AST de `app/precio/*`."""
    modulos = _puros_precio()
    assert modulos, "no se encontro el motor de precios: ¿se movio app/precio/?"
    fugas = {
        p.relative_to(PRECIO).as_posix(): v
        for p in modulos
        if (v := _usos_reloj(ast.parse(p.read_text(encoding="utf-8"))))
    }
    assert not fugas, f"app/precio lee reloj o entorno: {fugas}"


def test_precio_frontera_caza_fuga_en_subpaquete(tmp_path, monkeypatch):
    """Fuga sembrada: un `sub/fuga.py` con `import httpx` hace fallar el
    candado con el nombre del archivo."""

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "fuga.py").write_text("import httpx\n", encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="sub/fuga.py"):
        test_precio_puro_sin_io()


def test_precio_init_tambien_se_escanea(tmp_path, monkeypatch):
    """r1-B5: una fuga en `__init__.py` también dispara el candado."""

    (tmp_path / "__init__.py").write_text("import httpx\n", encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="__init__.py"):
        test_precio_puro_sin_io()


def test_precio_frontera_caza_reloj_con_alias(tmp_path, monkeypatch):
    """r1-B5, fuga sembrada de reloj: `from datetime import datetime as dt`
    + `dt.now()` hace fallar el candado con el nombre del archivo."""

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "reloj.py").write_text(
        "from datetime import datetime as dt\nx = dt.now()\n", encoding="utf-8"
    )
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="sub/reloj.py"):
        test_precio_sin_reloj_ni_entorno()


@pytest.mark.parametrize(
    "cuerpo",
    [
        "import os\n",
        "import time\n",
        "import random\n",
        "import secrets\n",
        "from os import getenv\n",
    ],
)
def test_precio_frontera_caza_imports_de_reloj_entorno_azar(tmp_path, monkeypatch, cuerpo):
    """r2-A4: cada import prohibido hace fallar el candado con el nombre
    del archivo (reemplaza al test tautológico que solo miraba la
    constante)."""

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "fuga.py").write_text(cuerpo, encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="sub/fuga.py"):
        test_precio_puro_sin_io()


def test_r4_g9_reloj_caza_acceso_sin_llamada(tmp_path, monkeypatch):
    """r4-G9: `reloj = dt.now` (referencia sin llamar) también dispara."""
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "reloj.py").write_text(
        "from datetime import datetime as dt\nreloj = dt.now\n", encoding="utf-8"
    )
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="sub/reloj.py"):
        test_precio_sin_reloj_ni_entorno()


@pytest.mark.parametrize(
    "cuerpo",
    [
        "from datetime import datetime as dt\nx = dt.now()\n",
        "from datetime import datetime as dt\nx = dt.utcnow()\n",
        "from datetime import date as d\nx = d.today()\n",
    ],
)
def test_precio_frontera_caza_reloj_en_todas_sus_formas(tmp_path, monkeypatch, cuerpo):
    """r2-A4: `now`, `utcnow` y `today` caen sea quien sea el dueño."""

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "reloj.py").write_text(cuerpo, encoding="utf-8")
    monkeypatch.setattr("test_architecture.PRECIO", tmp_path)
    with pytest.raises(AssertionError, match="sub/reloj.py"):
        test_precio_sin_reloj_ni_entorno()


# REPRICING 01 A.3 (carril C): candados del cliente de escritura SP-API.
# Solo se AGREGA al final (el carril B tambien toca este archivo).


def _sin_comentario(linea: str) -> str:
    """Corta el comentario (`#` fuera de cadenas, r6-C3): los comentarios
    no ejecutan y un `PATCH` en ellos no es escritura."""
    comilla = None
    i = 0
    while i < len(linea):
        c = linea[i]
        if comilla is not None:
            if c == "\\":
                i += 2
                continue
            if c == comilla:
                comilla = None
        elif c in ("'", '"'):
            comilla = c
        elif c == "#":
            return linea[:i]
        i += 1
    return linea


def _fugas_patch_crudos(raiz_app, raiz_tools):
    """Archivos que escriben a Listings fuera de `app/spapi/write_client.py`.

    Candado de deriva accidental, no prueba exhaustiva: caza por linea
    (sin comentarios) `httpx.patch` / `.patch(` / `request("PATCH"` (con
    o sin espacio tras el paréntesis) / `method="PATCH"` crudos, o el
    prefijo `/listings/2021-08-01/items` junto a una llamada ejecutable
    `httpx.patch(...)` en la misma linea. Un verbo en variable o
    construido por partes escapa. `precio_write.py` nombra el prefijo
    solo para el `error_code` (sin llamada en esa linea) y por eso no
    dispara.
    """
    import re

    llamada = re.compile(r"httpx\s*\.\s*patch\s*\(")
    fugas = []
    for base in (raiz_app, raiz_tools):
        for p in sorted(base.rglob("*.py")):
            try:
                rel = p.relative_to(RAIZ).as_posix()
            except ValueError:
                rel = p.name  # fuga sembrada bajo tmp_path
            if rel == "app/spapi/write_client.py":
                continue
            try:
                lineas = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                # r6-C2: lo ilegible no es cobertura aparente: cuenta
                # como fuga con su nombre, nunca se salta en silencio.
                fugas.append(f"{rel}:ilegible")
                continue
            for n, linea in enumerate(lineas, start=1):
                codigo = _sin_comentario(linea)
                sin_espacios = codigo.replace(" ", "")
                if (
                    "httpx.patch" in codigo
                    or ".patch(" in codigo
                    or 'request("PATCH"' in sin_espacios
                    or "request('PATCH'" in sin_espacios
                    or 'method="PATCH"' in sin_espacios
                    or "method='PATCH'" in sin_espacios
                    or ("/listings/2021-08-01/items" in codigo and llamada.search(codigo))
                ):
                    fugas.append(f"{rel}:{n}")
    return fugas


def test_precio_write_sin_patch_crudo():
    """A.3: el unico PATCH a Listings sale de `app/spapi/write_client.py`
    (AC9: `httpx.patch` con la ruta de listings fuera de el hace fallar)."""
    fugas = _fugas_patch_crudos(APP, RAIZ / "tools")
    assert not fugas, f"PATCH crudo a Listings fuera del write client: {fugas}"


def test_precio_write_frontera_caza_patch_crudo(tmp_path):
    """Fuga sembrada: `httpx.patch` con la ruta de listings dispara."""
    (tmp_path / "fuga.py").write_text(
        'import httpx\nhttpx.patch("/listings/2021-08-01/items/X/S", json={})\n',
        encoding="utf-8",
    )
    fugas = _fugas_patch_crudos(tmp_path, tmp_path)
    assert any("fuga.py" in f for f in fugas)


def test_precio_write_frontera_caza_prefijo_con_llamada_con_espacios(tmp_path):
    """Fuga sembrada: el prefijo con llamada ejecutable dispara aun con espacios."""
    (tmp_path / "fuga.py").write_text(
        'httpx . patch ("/listings/2021-08-01/items/X/S")\n', encoding="utf-8"
    )
    fugas = _fugas_patch_crudos(tmp_path, tmp_path)
    assert any("fuga.py" in f for f in fugas)


def test_precio_write_frontera_caza_formas_con_espacio(tmp_path):
    """Fuga sembrada: `request( "PATCH"` y `method="PATCH"` disparan."""
    (tmp_path / "fuga.py").write_text(
        'client.request( "PATCH", url)\nclient.request(method="PATCH", url=url)\n',
        encoding="utf-8",
    )
    fugas = _fugas_patch_crudos(tmp_path, tmp_path)
    lineas = {f.split(":")[1] for f in fugas if "fuga.py" in f}
    assert lineas == {"1", "2"}


# Quien puede importar el cliente de ESCRITURA SP-API: solo su modulo de
# escritura. Crecer la allowlist exige editar este archivo = decision
# visible en diff y review (mismo trato que PERMITIDOS_IMPORTAR_ADS_WRITE).
PERMITIDOS_IMPORTAR_SPAPI_WRITE = {
    "app/spapi/precio_write.py": (
        "escritor de precios (A.3): el dueno legitimo del PATCH a Listings;"
        " el tool llega por el, nunca directo"
    ),
}


def _importadores_spapi_write(raiz_app, raiz_tools):
    import ast

    importadores = set()
    for base in (raiz_app, raiz_tools):
        for p in base.rglob("*.py"):
            if "app.spapi.write_client" in _imports_runtime(p):
                importadores.add(p)
                continue
            # Nivel 1: `from .write_client import …` / `from . import
            # write_client`: el importador legitimo es hermano en
            # `app/spapi/` y la forma relativa es la natural de saltarse el
            # candado. Conservador: cualquier nivel 1 a `write_client` en el
            # arbol escaneado se resuelve a `app.spapi.write_client` (es el
            # unico `write_client` del repo).
            try:
                arbol = ast.parse(p.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            # r6-C4: como `_imports_runtime`, los relativos bajo
            # TYPE_CHECKING no corren en runtime y no son fuga.
            for nodo in _nodos_runtime(arbol):
                if not (isinstance(nodo, ast.ImportFrom) and nodo.level == 1):
                    continue
                if (nodo.module or "").split(".")[0] == "write_client" or any(
                    alias.name == "write_client" for alias in nodo.names
                ):
                    importadores.add(p)
                    break
    return importadores


def test_imports_del_spapi_write_client_acotados():
    """Nadie fuera de la allowlist importa `app.spapi.write_client`."""
    importadores = {
        p.relative_to(RAIZ).as_posix() for p in _importadores_spapi_write(APP, RAIZ / "tools")
    }
    ilegales = importadores - set(PERMITIDOS_IMPORTAR_SPAPI_WRITE)
    assert not ilegales, (
        f"modulos que importan app.spapi.write_client sin estar en la allowlist: {sorted(ilegales)}"
    )
    for rel, razon in PERMITIDOS_IMPORTAR_SPAPI_WRITE.items():
        assert razon.strip(), f"entrada de allowlist sin razon escrita: {rel}"


def test_imports_spapi_write_frontera_caza_import_extra(tmp_path):
    """Fuga sembrada: un importador fuera de la allowlist se detecta."""
    fuga = tmp_path / "otro.py"
    fuga.write_text("from app.spapi.write_client import SpapiWriteClient\n", encoding="utf-8")
    importadores = _importadores_spapi_write(tmp_path, tmp_path)
    assert {p.name for p in importadores} == {"otro.py"}
    assert "otro.py" not in PERMITIDOS_IMPORTAR_SPAPI_WRITE


def _importadores_dinamicos_spapi_write(raiz_app, raiz_tools):
    """Módulos que nombran `app.spapi.write_client` junto a un import dinámico.

    Cierre del hueco AST (mismo trato que `snapshot_listas`): `__import__(`
    e `import_module(` no producen nodos de import y el candado de arriba
    no los ve. Hoy nadie en `app/` ni `tools/` usa imports dinámicos.
    """
    dinamicos = set()
    for base in (raiz_app, raiz_tools):
        for p in base.rglob("*.py"):
            try:
                fuente = p.read_text(encoding="utf-8")
            except OSError:
                # r6-C2: lo ilegible cuenta como fuga (ver arriba).
                dinamicos.add(p)
                continue
            if ("app.spapi.write_client" in fuente or ".write_client" in fuente) and (
                "__import__(" in fuente or "import_module(" in fuente
            ):
                dinamicos.add(p)
    return dinamicos


def test_imports_spapi_write_sin_import_dinamico():
    """Nadie llega a `app.spapi.write_client` por import dinámico."""
    dinamicos = {
        p.relative_to(RAIZ).as_posix()
        for p in _importadores_dinamicos_spapi_write(APP, RAIZ / "tools")
    }
    assert not dinamicos, (
        f"modulos que nombran app.spapi.write_client junto a un import dinamico:"
        f" {sorted(dinamicos)}"
    )


def test_imports_spapi_write_frontera_caza_import_dinamico(tmp_path):
    """Fuga sembrada: el import dinámico de write_client se detecta."""
    fuga = tmp_path / "otro.py"
    fuga.write_text('mod = __import__("app.spapi.write_client")\n', encoding="utf-8")
    dinamicos = _importadores_dinamicos_spapi_write(tmp_path, tmp_path)
    assert {p.name for p in dinamicos} == {"otro.py"}


def test_imports_spapi_write_frontera_caza_nivel_1(tmp_path):
    """Fuga sembrada: `from .write_client import …` se resuelve al absoluto."""
    fuga = tmp_path / "hermano.py"
    fuga.write_text("from .write_client import SpapiWriteClient\n", encoding="utf-8")
    importadores = _importadores_spapi_write(tmp_path, tmp_path)
    assert {p.name for p in importadores} == {"hermano.py"}


def test_imports_spapi_write_frontera_caza_dinamico_relativo(tmp_path):
    """Fuga sembrada: `import_module(".write_client", "app.spapi")` se detecta."""
    fuga = tmp_path / "otro.py"
    fuga.write_text(
        'import importlib\nmod = importlib.import_module(".write_client", "app.spapi")\n',
        encoding="utf-8",
    )
    dinamicos = _importadores_dinamicos_spapi_write(tmp_path, tmp_path)
    assert {p.name for p in dinamicos} == {"otro.py"}


ALLOWLIST_IMPORTS_PRECIO_REVERSA = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "app.db",
        "app.db.OrbitDbError",
        "app.db.connect",
        "app.spapi",
        "app.spapi.client",
        "app.spapi.client.CuboTasa",
        "app.spapi.client.SpapiClient",
        "app.spapi.client.SpapiError",
        "app.spapi.client.SpapiNoPermitida",
        "psycopg",
        "psycopg.errors",
        "psycopg.errors.UniqueViolation",
        "app.spapi.precio_write",
        "app.spapi.precio_write.FormaParcheSinSellar",
        "argparse",
        "hashlib",
        "httpx",
        "os",
        "sys",
        "time",
    }
)


def test_tool_precio_reversa_solo_importa_lectura():
    """`tools/precio_reversa.py` es lectura + `precio_write`: sus imports
    son subconjunto de la allowlist y nunca el write client directo (el
    escritor se construye via `precio_write.construir_escritor`)."""
    imp = _imports_runtime(RAIZ / "tools" / "precio_reversa.py")
    extras = imp - ALLOWLIST_IMPORTS_PRECIO_REVERSA
    assert not extras, (
        f"tools/precio_reversa.py importa por fuera de la allowlist: {sorted(extras)} — "
        "ampliar exige editar tests/test_architecture.py a proposito"
    )
    assert "app.spapi.write_client" not in imp


def test_tool_precio_reversa_frontera_caza_write_directo(tmp_path):
    """Fuga sembrada: si el tool importara el write client, la allowlist
    (subconjunto) lo detecta con el import de mas identificado."""
    fuente = (RAIZ / "tools" / "precio_reversa.py").read_text(encoding="utf-8")
    fuga = tmp_path / "precio_reversa_fuga.py"
    fuga.write_text(
        fuente + "from app.spapi.write_client import SpapiWriteClient\n", encoding="utf-8"
    )
    imp = _imports_runtime(fuga)
    assert "app.spapi.write_client" in _violaciones(imp, ("app.spapi.write_client",))
    extras = imp - ALLOWLIST_IMPORTS_PRECIO_REVERSA
    assert "app.spapi.write_client" in extras


# ---------------------------------------------------------- r6-C2 ilegible es fuga


def test_precio_write_frontera_ilegible_cuenta_como_fuga(tmp_path):
    """r6-C2: un .py ilegible no es cobertura aparente: cuenta como fuga."""
    p = tmp_path / "ilegible.py"
    p.write_text("x = 1\n", encoding="utf-8")
    p.chmod(0o000)
    try:
        fugas = _fugas_patch_crudos(tmp_path, tmp_path)
    finally:
        p.chmod(0o644)
    assert any("ilegible.py" in f for f in fugas)


def test_imports_spapi_write_frontera_ilegible_cuenta_como_fuga(tmp_path):
    """r6-C2: un .py ilegible cuenta como fuga tambien en dinamicos."""
    p = tmp_path / "ilegible.py"
    p.write_text("x = 1\n", encoding="utf-8")
    p.chmod(0o000)
    try:
        dinamicos = _importadores_dinamicos_spapi_write(tmp_path, tmp_path)
    finally:
        p.chmod(0o644)
    assert {x.name for x in dinamicos} == {"ilegible.py"}


# ---------------------------------------------------------- r6-C3 comentarios no ejecutan


def test_precio_write_frontera_comentario_con_patch_no_es_fuga(tmp_path):
    """r6-C3: PATCH en comentario (aun junto al prefijo) no es escritura."""
    (tmp_path / "notas.py").write_text(
        'RUTA = "/listings/2021-08-01/items"  # PATCH\n# PATCH "/listings/2021-08-01/items"\n',
        encoding="utf-8",
    )
    assert _fugas_patch_crudos(tmp_path, tmp_path) == []


def test_precio_write_frontera_caza_llamada_ejecutable_con_prefijo(tmp_path):
    """r6-C3: la frontera del prefijo es la llamada ejecutable."""
    (tmp_path / "fuga.py").write_text(
        'import httpx\nhttpx.patch("/listings/2021-08-01/items/X/S", json={})\n',
        encoding="utf-8",
    )
    fugas = _fugas_patch_crudos(tmp_path, tmp_path)
    assert any("fuga.py" in f for f in fugas)


# ---------------------------------------------------------- r6-C4 TYPE_CHECKING en relativos


def test_imports_spapi_write_frontera_type_checking_relativo_no_es_fuga(tmp_path):
    """r6-C4: `from .write_client` bajo TYPE_CHECKING no corre en runtime."""
    (tmp_path / "hermano.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .write_client import SpapiWriteClient\n",
        encoding="utf-8",
    )
    assert _importadores_spapi_write(tmp_path, tmp_path) == set()


# ---------------------------------------------------------------------------
# REPRICING 01 A.1: un solo escritor de `precio_goal`.
#
# `app/precio/goals_write.py` es el unico que escribe `precio_goal`
# (INSERT al sembrar, UPDATE de `valid_to` al cerrar; el trigger ya rechaza
# cualquier otro UPDATE). `tools/precio_goal.py` despacha, jamas SQL crudo
# (las lecturas con SELECT si pueden: SELECT no es escritura). En paralelo
# a `_IDENT_GOAL`/`_SQL_UPDATE_GOAL` de `ads_optimizer_goal`, sin tocarlos.
# ---------------------------------------------------------------------------
_IDENT_PRECIO_GOAL = r'(?:"?\w+"?\.)?"?precio_goal"?'
_SQL_UPDATE_PRECIO_GOAL = rf"UPDATE\s+{_IDENT_PRECIO_GOAL}"
_SQL_INSERT_PRECIO_GOAL = rf"INSERT\s+INTO\s+{_IDENT_PRECIO_GOAL}"
_PATRON_UPDATE_PRECIO_GOAL = re.compile(_SQL_UPDATE_PRECIO_GOAL, re.IGNORECASE)
_PATRON_INSERT_PRECIO_GOAL = re.compile(_SQL_INSERT_PRECIO_GOAL, re.IGNORECASE)


def _escritores_crudos_precio_goal(raiz: Path) -> list[str]:
    """Escritores crudos de `precio_goal` bajo `<raiz>/tools/` (UPDATE o
    INSERT del patron compilado): el escaneo que el candado usa sobre el
    repo real, extraido para probarlo con fuga sembrada."""
    return sorted(
        p.relative_to(raiz).as_posix()
        for p in (raiz / "tools").rglob("*.py")
        if _PATRON_UPDATE_PRECIO_GOAL.search(p.read_text(encoding="utf-8"))
        or _PATRON_INSERT_PRECIO_GOAL.search(p.read_text(encoding="utf-8"))
    )


def _escritores_app_precio_goal() -> list[str]:
    """Modulos de `app/` con UPDATE o INSERT crudo de `precio_goal`."""
    return sorted(
        p.relative_to(RAIZ).as_posix()
        for p in APP.rglob("*.py")
        if _PATRON_UPDATE_PRECIO_GOAL.search(p.read_text(encoding="utf-8"))
        or _PATRON_INSERT_PRECIO_GOAL.search(p.read_text(encoding="utf-8"))
    )


def test_escritura_precio_goal_vive_solo_en_goals_write():
    """Candado de camino unico de goals de precio (A.1): solo
    `app/precio/goals_write.py` trae UPDATE o INSERT de `precio_goal`, y
    NINGUN tool trae SQL crudo de escritura (despachan a `goals_write`)."""
    assert _escritores_app_precio_goal() == ["app/precio/goals_write.py"]
    assert _escritores_crudos_precio_goal(RAIZ) == []


def test_candado_precio_goal_caza_update_crudo_en_tools(tmp_path):
    """A.1, fuga sembrada: la copia del tool con un `UPDATE precio_goal`
    crudo aparece listada por el helper (el detector muerde)."""
    tools = tmp_path / "tools"
    tools.mkdir()
    fuga = '\n# fuga sembrada (A.1):\n_FUGA = "UPDATE precio_goal SET x = 1"\n'
    (tools / "precio_goal.py").write_text(
        (RAIZ / "tools" / "precio_goal.py").read_text(encoding="utf-8") + fuga,
        encoding="utf-8",
    )
    assert _escritores_crudos_precio_goal(tmp_path) == ["tools/precio_goal.py"]


def test_patrones_precio_goal_resisten_case_y_whitespace():
    """A.1: `uPdAtE\\n\\tprecio_goal` DETECTA; esquema o comillas tambien;
    la mencion benigna sin verbo SQL no dispara."""
    assert _PATRON_UPDATE_PRECIO_GOAL.search("uPdAtE\n\tprecio_goal")
    assert _PATRON_UPDATE_PRECIO_GOAL.search("UPDATE public.precio_goal SET x = 1")
    assert _PATRON_INSERT_PRECIO_GOAL.search('INSERT INTO "precio_goal" (a)')
    benigno = "el UNICO camino de escritura de precio_goal (A.1)"
    assert not _PATRON_UPDATE_PRECIO_GOAL.search(benigno)
    assert not _PATRON_INSERT_PRECIO_GOAL.search(benigno)


# ---------------------------------------------------------------------------
# REPRICING 01 A.7: candado propio de `fuentes.py` (lector de cobertura).
#
# Solo `psycopg`, `app.precio.*`, stdlib, y de `app.estimacion_insumos`
# UNICAMENTE `from app.estimacion_insumos import mapear_canal` (ahi vive
# ese simbolo; el modulo entero trae escritura y no entra). Jamas red,
# reloj, entorno, import dinamico (relativo incluido), `app.spapi.*` ni
# escritura (el patron corre SOLO sobre las constantes de texto del
# modulo, recorridas con `ast`: ahi vive el SQL; multilinea, con todos
# los verbos). r1: la excepcion por nombre solo levanta la prohibicion
# de imports de I/O (`psycopg`); el resto lo recupera este candado (G1).
# r2-G5: el candado de imports de `fuentes.py` aplica `PROHIBIDOS_PRECIO`
# completo (`os`, `time`, `importlib`, `random`, `secrets` y el resto de
# la lista), salvo `psycopg`, que es lo unico que la excepcion levanta.
# `datetime` sigue permitido para `date`/`UTC`, pero `date.today()`,
# `datetime.now()` y compania los caza el detector de reloj.
# ---------------------------------------------------------------------------
_PATRON_ESCRITURA_FUENTES = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+|DELETE\s+FROM|TRUNCATE\s+|COPY\s+|DROP\s+"
    r"|ALTER\s+|MERGE\s+|CREATE\s+)",
    re.IGNORECASE,
)


# r2-G5: `PROHIBIDOS_PRECIO` completo salvo `psycopg` (lo unico que la
# excepcion de A.7 levanta); el resto de stdlib y `app.precio.*` siguen
# permitidos. El apareo es exacto o por prefijo punteado (`os.getenv`
# cae por `os`; `app.db.x` por `app.db`).
_PROHIBIDOS_IMPORTS_FUENTES = tuple(e for e in PROHIBIDOS_PRECIO if e != "psycopg")


def _import_prohibido_fuentes(nombre: str) -> bool:
    return any(
        nombre == entrada or nombre.startswith(f"{entrada}.")
        for entrada in _PROHIBIDOS_IMPORTS_FUENTES
        if not entrada.startswith("<")
    )


def _fugas_imports_fuentes(path: Path) -> list[str]:
    import sys as _sys

    def _permitido(nombre: str) -> bool:
        return (
            nombre.split(".")[0] in _sys.stdlib_module_names
            or nombre == "psycopg"
            or nombre.startswith(("psycopg.", "app.precio."))
        )

    arbol = ast.parse(path.read_text(encoding="utf-8"))
    fugas: list[str] = []

    def _visitar(nodos: list) -> None:
        for nodo in nodos:
            if _es_bloque_type_checking(nodo):
                continue
            if isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    if _import_prohibido_fuentes(alias.name) or not _permitido(alias.name):
                        fugas.append(alias.name)
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.level:
                    fugas.append(f"<relativo-nivel-{nodo.level}>")
                elif nodo.module == "app.estimacion_insumos":
                    if sorted(a.name for a in nodo.names) != ["mapear_canal"]:
                        fugas.append(
                            "app.estimacion_insumos:" + ",".join(sorted(a.name for a in nodo.names))
                        )
                elif nodo.module:
                    candidatos = [nodo.module] + [f"{nodo.module}.{a.name}" for a in nodo.names]
                    for candidato in candidatos:
                        if _import_prohibido_fuentes(candidato) or not _permitido(candidato):
                            fugas.append(candidato)
            else:
                _visitar(list(ast.iter_child_nodes(nodo)))

    _visitar(arbol.body)
    return sorted(set(fugas))


def _textos_constantes_fuentes(path: Path) -> list[str]:
    """Solo las constantes de texto del modulo (r2-G7: ahi vive el SQL;
    comentarios e imports quedan fuera del patron de escritura)."""
    arbol = ast.parse(path.read_text(encoding="utf-8"))
    return [
        nodo.value
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
    ]


def _escritura_en_fuentes(path: Path) -> list[str]:
    return [
        m.group(0).strip()[:80]
        for texto in _textos_constantes_fuentes(path)
        for m in _PATRON_ESCRITURA_FUENTES.finditer(texto)
    ]


def test_fuentes_solo_importa_permitido():
    """A.7: `fuentes.py` no trae red, `app.spapi.*` ni nada fuera de la
    lista (el detector muerde: ver fugas sembradas)."""
    assert _fugas_imports_fuentes(PRECIO / "fuentes.py") == []


def test_candado_imports_fuentes_caza_fuga_sembrada(tmp_path):
    """A.7, fuga sembrada: la copia con `import httpx` aparece listada."""
    (tmp_path / "fuentes.py").write_text(
        (PRECIO / "fuentes.py").read_text(encoding="utf-8") + "\nimport httpx  # fuga\n",
        encoding="utf-8",
    )
    assert _fugas_imports_fuentes(tmp_path / "fuentes.py") == ["httpx"]


def test_candado_imports_fuentes_solo_mapear_canal(tmp_path):
    """A.7 r1-G3, fuga sembrada: otro simbolo de `app.estimacion_insumos`
    aparece listado (el modulo entero no entra)."""
    (tmp_path / "fuentes.py").write_text(
        (PRECIO / "fuentes.py").read_text(encoding="utf-8")
        + "\nfrom app.estimacion_insumos import persistir_oferta_observation  # fuga\n",
        encoding="utf-8",
    )
    assert _fugas_imports_fuentes(tmp_path / "fuentes.py") == [
        "app.estimacion_insumos:persistir_oferta_observation"
    ]


def test_candado_imports_fuentes_caza_relativo(tmp_path):
    """A.7 r1-G4, fuga sembrada: un import relativo aparece listado
    (`_imports_runtime` no lo ve)."""
    (tmp_path / "fuentes.py").write_text(
        (PRECIO / "fuentes.py").read_text(encoding="utf-8")
        + "\nfrom .hermano import cosa  # fuga\n",
        encoding="utf-8",
    )
    assert _fugas_imports_fuentes(tmp_path / "fuentes.py") == ["<relativo-nivel-1>"]


def test_fuentes_sin_reloj_ni_entorno_ni_dinamico():
    """A.7 r1-G1: la excepcion por nombre no ampara reloj, entorno ni
    import dinamico en `fuentes.py` (los detectores generales no lo
    cubren: sale exceptuado del `rglob`)."""
    arbol = ast.parse((PRECIO / "fuentes.py").read_text(encoding="utf-8"))
    assert _usos_reloj(arbol) == []
    assert _usos_import_dinamico(arbol) == []


def test_candado_reloj_fuentes_caza_fuga_sembrada(tmp_path):
    """A.7 r1-G1, fuga sembrada: `date.today()` en la copia sale rojo."""
    (tmp_path / "fuentes.py").write_text(
        (PRECIO / "fuentes.py").read_text(encoding="utf-8") + "\nprint(date.today())  # fuga\n",
        encoding="utf-8",
    )
    arbol = ast.parse((tmp_path / "fuentes.py").read_text(encoding="utf-8"))
    assert _usos_reloj(arbol) == [".today"]


def _fuentes_mas(extra: str, tmp_path: Path) -> Path:
    """Copia de `fuentes.py` en `tmp_path` con `extra` al final (fuga)."""
    destino = tmp_path / "fuentes.py"
    destino.write_text(
        (PRECIO / "fuentes.py").read_text(encoding="utf-8") + "\n" + extra + "\n",
        encoding="utf-8",
    )
    return destino


def test_candado_imports_fuentes_caza_import_time(tmp_path):
    """A.7 r2-G5/G6, fuga sembrada: `import time` + `time.time()` sale rojo."""
    copia = _fuentes_mas("import time\n\n_huella = time.time()  # fuga\n", tmp_path)
    assert _fugas_imports_fuentes(copia) != []


def test_candado_imports_fuentes_caza_from_os_getenv(tmp_path):
    """A.7 r2-G5/G6, fuga sembrada: `from os import getenv` sale rojo."""
    copia = _fuentes_mas("from os import getenv\n\n_ajuste = getenv  # fuga\n", tmp_path)
    assert _fugas_imports_fuentes(copia) != []


def test_candado_reloj_fuentes_caza_os_environ(tmp_path):
    """A.7 r2-G6, fuga sembrada: `os.environ[...]` sale rojo (reloj/entorno)."""
    copia = _fuentes_mas('_ajuste = os.environ["X"]  # fuga\n', tmp_path)
    arbol = ast.parse(copia.read_text(encoding="utf-8"))
    assert _usos_reloj(arbol) != []


def test_candado_imports_fuentes_caza_importlib(tmp_path):
    """A.7 r2-G5/G6, fuga sembrada: `import importlib` + uso sale rojo."""
    copia = _fuentes_mas(
        'import importlib\n\n_mod = importlib.import_module("x")  # fuga\n', tmp_path
    )
    assert _fugas_imports_fuentes(copia) != []


def test_candado_dinamico_fuentes_caza_dunder_import(tmp_path):
    """A.7 r2-G6, fuga sembrada: `__import__("httpx")` sale rojo."""
    copia = _fuentes_mas('_mod = __import__("httpx")  # fuga\n', tmp_path)
    arbol = ast.parse(copia.read_text(encoding="utf-8"))
    assert _usos_import_dinamico(arbol) != []


def test_fuentes_solo_select():
    """A.7 r3-CR3: candado lexico complementario de `fuentes.py` (cero
    verbos de escritura en las constantes de texto, multilinea incluidos;
    no ve `CALL`/`DO` ni funciones con efectos dentro de un `SELECT`). La
    garantia de solo lectura es el rol `app_read`: ver
    `test_app_read_no_escribe_tablas_de_fuentes` en
    `tests/test_precio_cobertura.py`."""
    assert _escritura_en_fuentes(PRECIO / "fuentes.py") == []


def test_candado_select_fuentes_caza_fugas_sembradas(tmp_path):
    """A.7 r1-G2, fugas sembradas: `INSERT` partido en dos lineas y
    `TRUNCATE` aparecen listados."""
    # r2-G7: secuencia escapada (el archivo debe parsear; el VALOR
    # decodificado conserva el salto y el `\s+` multilinea discrimina).
    (tmp_path / "fuga_insert.py").write_text(
        'pedido = "INSERT\\nINTO x (a)"  # fuga\n', encoding="utf-8"
    )
    # r2-G7: la fuga es una constante de texto (el patron ya no barre codigo
    # ni comentarios, y el `TRUNCATE` pelado ni siquiera parsea).
    (tmp_path / "fuga_truncate.py").write_text('sql = "TRUNCATE x"  # fuga\n', encoding="utf-8")
    assert _escritura_en_fuentes(tmp_path / "fuga_insert.py") != []
    assert _escritura_en_fuentes(tmp_path / "fuga_truncate.py") != []


def test_candado_escritura_fuentes_ignora_import_deepcopy(tmp_path):
    """A.7 r2-G7: `from copy import deepcopy` no dispara el patron (`COPY\\s+`)."""
    copia = _fuentes_mas(
        "from copy import deepcopy\n\n_clon = deepcopy({})  # no es escritura\n", tmp_path
    )
    assert _escritura_en_fuentes(copia) == []


def test_candado_escritura_fuentes_ignora_comentario(tmp_path):
    """A.7 r2-G7: un comentario con «insert into» no dispara el patron."""
    copia = _fuentes_mas("# nota: esto NO es un insert into real\n", tmp_path)
    assert _escritura_en_fuentes(copia) == []


# ---------------------------------------------------------------------------
# REPRICING 01 A.5: candados propios de `corrida.py` y `cuota.py`.
#
# `corrida.py` puede `psycopg`, `app.precio.*`, `app.spapi.precio_write`,
# `app.estimacion_reader`, `app.estimacion_fees` (cotizar),
# `app.estimacion_venta` (DetalleFee) y `app.estimacion_insumos`
# (OfertaResuelta) mas stdlib. FORBIDDEN del brief: ni `app.apply`,
# `app.cycle`, `app.ads` ni `app.optimizer` (la moneda sin P sale de las
# observaciones, nunca de un mapa en codigo). El cubo entra inyectado
# (`limitador`, lo
# construye quien llama): ni `CuboTasa` ni literales float aqui. Jamas
# red (`httpx`), reloj (`time`, `datetime.now`), entorno (`os`),
# `importlib`, import dinamico, `app.db`, `app.ads`, `app.apply`,
# `app.cycle` ni el resto de `app.spapi.*`/`app.estimacion_*`. Escribe
# SOLO en `precio_decision`, `precio_cotizacion`, `precio_cambio` y
# `ads_optimizer_lock` (el cupo lo escribe `cuota.py`).
# `cuota.py` puede `psycopg` + stdlib y escribe SOLO `apply_quota_state`.
# El cubo lo construye quien llama
# (CLI/tests) y entra inyectado como `limitador`: `corrida.py` no importa
# `CuboTasa` ni trae literales float (el 0.5/s de Pricing vive en el CLI).
# ---------------------------------------------------------------------------

_PERMITIDOS_CORRIDA = (
    "psycopg",
    "app.precio.",
    "app.spapi.precio_write",
    "app.estimacion_reader",
    "app.estimacion_fees",
    "app.estimacion_venta",
    "app.estimacion_insumos",
)

# `app.spapi` y `app.estimacion_fees` estan vetados para el resto de
# `app/precio`, pero la corrida los tiene concedidos arriba de forma
# explicita (precio_write para aplicar, fees para cotizar): se excluyen
# del veto para que la concesion no sea letra muerta. El resto (`httpx`,
# `app.db`, `app.ads`, `time`, `os`, `importlib`, ...) sigue vetado, y lo
# no concedido (p. ej. `app.spapi.client`, el resto de `app.spapi.*`) lo
# frena la lista de permitidos.
_PROHIBIDOS_IMPORTS_CORRIDA = tuple(
    e for e in PROHIBIDOS_PRECIO if e not in ("psycopg", "app.spapi", "app.estimacion_fees")
)

# El cupo se reparte con `app.precio.cuota` (su escritura la candadea
# `_ESCRIBE_CUOTA`): `corrida.py` no trae SQL propio sobre
# `apply_quota_state`, solo delega. Un solo dueno por tabla.
_ESCRIBE_CORRIDA = (
    "precio_decision",
    "precio_cotizacion",
    "precio_cambio",
    "ads_optimizer_lock",
)

_ESCRIBE_CUOTA = ("apply_quota_state",)

_PATRON_TABLA_ESCRITA = re.compile(
    # El `(?!SET\b)` excluye el `DO UPDATE SET` del claim (ahi no hay
    # tabla entre UPDATE y SET; en un UPDATE real si: `UPDATE t SET`).
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?!SET\b)([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _import_prohibido_corrida(nombre: str) -> bool:
    return any(
        nombre == entrada or nombre.startswith(f"{entrada}.")
        for entrada in _PROHIBIDOS_IMPORTS_CORRIDA
        if not entrada.startswith("<")
    )


def _fugas_imports_corrida(path: Path, *, permitidos: tuple) -> list[str]:
    import sys as _sys

    def _permitido(nombre: str) -> bool:
        # K7: borde de punto (`app.spapi.precio_write_extra` no pasa por
        # `app.spapi.precio_write`), como en `_import_prohibido_corrida`.
        return nombre.split(".")[0] in _sys.stdlib_module_names or any(
            nombre == p or nombre.startswith(p.rstrip(".") + ".") for p in permitidos
        )

    arbol = ast.parse(path.read_text(encoding="utf-8"))
    fugas: list[str] = []

    def _visitar(nodos: list) -> None:
        for nodo in nodos:
            if _es_bloque_type_checking(nodo):
                continue
            if isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    if _import_prohibido_corrida(alias.name) or not _permitido(alias.name):
                        fugas.append(alias.name)
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.level:
                    fugas.append(f"<relativo-nivel-{nodo.level}>")
                elif nodo.module:
                    candidatos = [nodo.module] + [f"{nodo.module}.{a.name}" for a in nodo.names]
                    for candidato in candidatos:
                        if _import_prohibido_corrida(candidato) or not _permitido(candidato):
                            fugas.append(candidato)
            else:
                _visitar(list(ast.iter_child_nodes(nodo)))

    _visitar(arbol.body)
    return sorted(set(fugas))


def _tablas_escritas(path: Path) -> list[str]:
    return sorted(
        {
            m.group(1)
            for texto in _textos_constantes_fuentes(path)
            for m in _PATRON_TABLA_ESCRITA.finditer(texto)
        }
    )


def test_corrida_solo_importa_permitido():
    """A.5: `corrida.py` no trae red, reloj, entorno, `app.apply` ni nada
    fuera de la lista (el detector muerde: ver fugas sembradas)."""
    assert _fugas_imports_corrida(PRECIO / "corrida.py", permitidos=_PERMITIDOS_CORRIDA) == []


def test_corrida_borde_de_punto_en_permitidos(tmp_path):
    """K7: `app.spapi.precio_write_extra` no pasa por `app.spapi.precio_write` (borde de punto)."""
    sonda = tmp_path / "corrida.py"
    sonda.write_text("import app.spapi.precio_write_extra\n", encoding="utf-8")
    assert "app.spapi.precio_write_extra" in _fugas_imports_corrida(
        sonda, permitidos=_PERMITIDOS_CORRIDA
    )


def test_corrida_sin_reloj_ni_dinamico():
    """A.5: la excepcion no ampara reloj ni import dinamico en `corrida.py`."""
    arbol = ast.parse((PRECIO / "corrida.py").read_text(encoding="utf-8"))
    assert _usos_reloj(arbol) == []
    assert _usos_import_dinamico(arbol) == []


def test_corrida_solo_escribe_sus_tablas():
    """A.5: `corrida.py` escribe solo sus cinco tablas (ni Ads ni ingest)."""
    assert _tablas_escritas(PRECIO / "corrida.py") == sorted(_ESCRIBE_CORRIDA)


def test_cuota_solo_importa_psycopg_y_stdlib():
    """A.5: `cuota.py` no trae red, reloj ni nada fuera de la lista."""
    assert _fugas_imports_corrida(PRECIO / "cuota.py", permitidos=("psycopg",)) == []


def test_cuota_solo_escribe_quota_state():
    """A.5: `cuota.py` escribe solo `apply_quota_state`."""
    assert _tablas_escritas(PRECIO / "cuota.py") == ["apply_quota_state"]


def _corrida_mas(extra: str, tmp_path: Path) -> Path:
    destino = tmp_path / "corrida.py"
    destino.write_text(
        (PRECIO / "corrida.py").read_text(encoding="utf-8") + "\n" + extra + "\n",
        encoding="utf-8",
    )
    return destino


def test_candado_imports_corrida_caza_app_apply(tmp_path):
    """A.5, fuga sembrada: `import app.apply` en la corrida sale rojo."""
    copia = _corrida_mas("import app.apply  # fuga\n", tmp_path)
    assert _fugas_imports_corrida(copia, permitidos=_PERMITIDOS_CORRIDA) != []


def test_candado_tablas_corrida_caza_tabla_ads(tmp_path):
    """A.5, fuga sembrada: `INSERT INTO ads_optimizer_goal` sale rojo."""
    copia = _corrida_mas(
        'sql = "INSERT INTO ads_optimizer_goal (a) VALUES (1)"  # fuga\n', tmp_path
    )
    assert set(_tablas_escritas(copia)) - set(_ESCRIBE_CORRIDA) != set()


def _cuota_mas(extra: str, tmp_path: Path) -> Path:
    destino = tmp_path / "cuota.py"
    destino.write_text(
        (PRECIO / "cuota.py").read_text(encoding="utf-8") + "\n" + extra + "\n",
        encoding="utf-8",
    )
    return destino


def test_candado_imports_cuota_caza_httpx(tmp_path):
    """A.5, fuga sembrada: `import httpx` en la cuota sale rojo."""
    copia = _cuota_mas("import httpx  # fuga\n", tmp_path)
    assert _fugas_imports_corrida(copia, permitidos=("psycopg",)) != []


def test_candado_tablas_cuota_caza_decision(tmp_path):
    """A.5, fuga sembrada: `INSERT INTO precio_decision` en cuota sale rojo."""
    copia = _cuota_mas('sql = "INSERT INTO precio_decision (a) VALUES (1)"  # fuga\n', tmp_path)
    assert set(_tablas_escritas(copia)) - set(_ESCRIBE_CUOTA) != set()
