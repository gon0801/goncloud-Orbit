"""Firmas reales, tomadas del AST, de los simbolos que ARREGLOS 05 DISENO nombra.

Uso desde la raiz del checkout:
    python docs/evidencia/arreglos-05/BASE/firmas.py           # arbol de trabajo
    python docs/evidencia/arreglos-05/BASE/firmas.py <sha>     # lee desde git, sin checkout

Imprime Markdown. Cada simbol lleva archivo, linea y firma completa en ese estado.
Una firma que cambia entre dos SHAs es el delta que BASE/diferencias.md debe registrar.
"""

import ast
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[4]
MODULO_TEMPORADA = "app/temporada_io.py"

# (archivo, simbolo, bloques de ARREGLOS 05 que lo consumen). "Clase.metodo" busca
# el metodo dentro de la clase; "*" lista la API publica del modulo.
SIMBOLOS = (
    ("app/optimizer/caso.py", "OrigenCambio", "A, B, H"),
    ("app/optimizer/caso.py", "HistoriaBid", "A, B, F"),
    ("app/optimizer/recuperacion.py", "cadena_bid", "A, B"),
    ("app/apply.py", "Aplicador.aplica_bids", "A, B, H"),
    ("app/apply_store.py", "_confirma_resumen", "A, B"),
    ("app/apply_cuota.py", "registra_sin_aplicar", "A, H"),
    ("app/apply_cola_ejecuta.py", "aplica_reactivaciones", "A, B, E"),
    ("app/config_write.py", "proxima_config", "I, J"),
    ("app/cycle_serializacion.py", "_sello_bitemporal", "G"),
)


def fuente(ruta: str, sha: str | None) -> str:
    if sha is None:
        return (RAIZ / ruta).read_text(encoding="utf-8")
    return subprocess.check_output(
        ["git", "-C", str(RAIZ), "show", f"{sha}:{ruta}"], text=True, encoding="utf-8"
    )


def primera_frase(doc: str | None) -> str:
    if not doc:
        return ""
    for linea in doc.strip().splitlines():
        if linea.strip():
            return linea.strip()
    return ""


def firma_funcion(nodo: ast.FunctionDef) -> str:
    piezas: list[str] = []
    args = nodo.args
    posicionales = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(posicionales) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(posicionales, defaults, strict=True):
        piezas.append(_parametro(arg, default))
    if args.vararg:
        piezas.append("*" + _nombre_tipado(args.vararg))
    elif args.kwonlyargs:
        piezas.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        piezas.append(_parametro(arg, default))
    if args.kwarg:
        piezas.append("**" + _nombre_tipado(args.kwarg))
    retorno = f" -> {ast.unparse(nodo.returns)}" if nodo.returns else ""
    return f"def {nodo.name}({', '.join(piezas)}){retorno}"


def _parametro(arg: ast.arg, default: ast.expr | None) -> str:
    texto = _nombre_tipado(arg)
    return f"{texto}={ast.unparse(default)}" if default is not None else texto


def _nombre_tipado(arg: ast.arg) -> str:
    return f"{arg.arg}: {ast.unparse(arg.annotation)}" if arg.annotation else arg.arg


def es_dataclass(nodo: ast.ClassDef) -> bool:
    for decorador in nodo.decorator_list:
        texto = ast.unparse(decorador)
        if texto == "dataclass" or texto.startswith("dataclass("):
            return True
    return False


def firma_clase(nodo: ast.ClassDef) -> list[str]:
    if not es_dataclass(nodo):
        return [f"class {nodo.name}:"] + [
            f"    {firma_funcion(m)}  # linea {m.lineno}"
            for m in nodo.body
            if isinstance(m, ast.FunctionDef)
        ]
    lineas = [f"@{', @'.join(ast.unparse(d) for d in nodo.decorator_list)}", f"class {nodo.name}:"]
    for sentencia in nodo.body:
        if isinstance(sentencia, ast.AnnAssign) and isinstance(sentencia.target, ast.Name):
            valor = ""
            if sentencia.value is not None:
                valor = f" = {ast.unparse(sentencia.value)}"
            lineas.append(f"    {sentencia.target.id}: {ast.unparse(sentencia.annotation)}{valor}")
    return lineas


def buscar(arbol: ast.Module, simbolo: str):
    if "." in simbolo:
        clase, metodo = simbolo.split(".", 1)
        for nodo in arbol.body:
            if isinstance(nodo, ast.ClassDef) and nodo.name == clase:
                for miembro in nodo.body:
                    if isinstance(miembro, ast.FunctionDef) and miembro.name == metodo:
                        return miembro
        return None
    for nodo in arbol.body:
        if isinstance(nodo, ast.ClassDef | ast.FunctionDef) and nodo.name == simbolo:
            return nodo
        if isinstance(nodo, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == simbolo for t in nodo.targets
        ):
            return nodo
    return None


def render_simbolo(ruta: str, simbolo: str, bloques: str, sha: str | None) -> str:
    arbol = ast.parse(fuente(ruta, sha))
    nodo = buscar(arbol, simbolo)
    if nodo is None:
        return f"### `{simbolo}`\n\nNO EXISTE en `{ruta}`.\n"
    titulo = f"### `{simbolo}`\n\n- Archivo: `{ruta}`, lineas {nodo.lineno}-{nodo.end_lineno}."
    titulo += f"\n- Lo consumen los bloques: {bloques}."
    if isinstance(nodo, ast.Assign):
        cuerpo = [f"{simbolo} = {ast.unparse(nodo.value)}"]
    elif isinstance(nodo, ast.ClassDef):
        cuerpo = firma_clase(nodo)
    else:
        cuerpo = [firma_funcion(nodo)]
    return titulo + "\n\n```python\n" + "\n".join(cuerpo) + "\n```\n"


def render_modulo_temporada(sha: str | None) -> str:
    arbol = ast.parse(fuente(MODULO_TEMPORADA, sha))
    lineas = [
        f"### API publica de `{MODULO_TEMPORADA}`\n",
        "| Linea | Simbolo | Firma |",
        "|---|---|---|",
    ]
    for nodo in arbol.body:
        if isinstance(nodo, ast.FunctionDef) and not nodo.name.startswith("_"):
            firma = firma_funcion(nodo).replace("|", "\\|")
        elif isinstance(nodo, ast.ClassDef) and not nodo.name.startswith("_"):
            firma = f"class {nodo.name}" + (
                f"({', '.join(ast.unparse(b) for b in nodo.bases)})" if nodo.bases else ""
            )
        else:
            continue
        lineas.append(f"| {nodo.lineno} | `{nodo.name}` | `{firma}` |")
    constantes = [
        f"`{t.id}` = `{ast.unparse(nodo.value)}`"
        for nodo in arbol.body
        if isinstance(nodo, ast.Assign)
        for t in nodo.targets
        if isinstance(t, ast.Name) and t.id.isupper()
    ]
    return "\n".join(lineas) + "\n\nConstantes publicas: " + ", ".join(constantes) + ".\n"


def main() -> None:
    sha = sys.argv[1] if len(sys.argv) > 1 else None
    estado = (
        sha
        or subprocess.check_output(["git", "-C", str(RAIZ), "rev-parse", "HEAD"], text=True).strip()
    )
    print(f"Firmas tomadas del AST en `{estado}`" + ("" if sha else " (arbol de trabajo).") + "\n")
    print(render_modulo_temporada(sha))
    for ruta, simbolo, bloques in SIMBOLOS:
        print(render_simbolo(ruta, simbolo, bloques, sha))


if __name__ == "__main__":
    main()
