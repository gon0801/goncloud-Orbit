# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Verifica que cada llamada de los bloques ```python de design.md exista en bosquejo.py con la misma firma:
nombre definido, palabras clave aceptadas y no mas argumentos posicionales de los que la firma admite.
Los nombres que ya existen en el repo (y no son parte del bosquejo) van en YA_EXISTE, cada uno con su archivo."""

import ast
import re
import sys

YA_EXISTE = {
    "confianza_recorte_desde_settings": "app/optimizer/goals.py:850",
    "confianza_subida_desde_settings": "app/optimizer/goals.py:858",
    "isinstance": "builtin",
    "str": "builtin",
    "date": "datetime.date",
    "append": "list.append",
    "post": "fastapi router",
    "get": "fastapi router",
    "Depends": "fastapi",
    "HTTPException": "fastapi",
}
raiz = sys.argv[1] if len(sys.argv) > 1 else ".."
arbol = ast.parse(open(f"{raiz}/bosquejo.py").read())
firmas = {}
for nodo in ast.walk(arbol):
    if isinstance(nodo, (ast.FunctionDef,)):
        a = nodo.args
        pos = [x.arg for x in a.posonlyargs + a.args if x.arg not in ("self", "cls")]
        firmas.setdefault(nodo.name, []).append((pos, [x.arg for x in a.kwonlyargs]))
    elif isinstance(nodo, ast.ClassDef):
        campos = [
            s.target.id
            for s in nodo.body
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
        ]
        firmas.setdefault(nodo.name, []).append((campos, []))
bloques = re.findall(
    r"```python\n(.*?)```",
    open(f"{raiz}/../../../superpowers/specs/2026-10-09-bids-02-design.md").read(),
    re.S,
)
fallas = vistas = 0
for i, b in enumerate(bloques, 1):
    for nodo in ast.walk(ast.parse(b)):
        if not isinstance(nodo, ast.Call):
            continue
        f = nodo.func
        nombre = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if nombre is None:
            continue
        vistas += 1
        claves = [k.arg for k in nodo.keywords if k.arg]
        if nombre in firmas:
            ok = any(
                set(claves) <= set(pos) | set(kw) and len(nodo.args) <= len(pos)
                for pos, kw in firmas[nombre]
            )
            estado = "ok" if ok else "FIRMA DISTINTA"
        elif nombre in YA_EXISTE:
            estado = f"ya existe ({YA_EXISTE[nombre]})"
        else:
            estado = "NO EXISTE"
        if estado in ("FIRMA DISTINTA", "NO EXISTE"):
            fallas += 1
        print(f"bloque {i}: {nombre}({len(nodo.args)} posicionales, {claves}) -> {estado}")
print(f"\n{vistas} llamadas en {len(bloques)} bloques; fallas: {fallas}")
sys.exit(1 if fallas else 0)
