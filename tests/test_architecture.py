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
