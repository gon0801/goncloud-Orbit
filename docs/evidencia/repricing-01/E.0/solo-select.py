"""Candado estructural de correr.sh: cada sentencia de un archivo de consultas
empieza con `select` o `with` (ORBIT · fase 10 · repricing-01 · E.0a).

Quita los comentarios (`-- ...` hasta fin de linea y `/* ... */`, aunque
abarquen varias lineas) respetando los strings entre comillas simples (con
`''` como comilla escapada) y los identificadores entre comillas dobles,
parte por `;` fuera de strings y comentarios, y exige que la primera palabra
de cada sentencia no vacia sea `select` o `with`. Asi un `commit;`, `end;`,
`END WORK;`, `END/*x*/;`, `do $$ ... $$`, `set ...`, `revoke`, `analyze` o
`call` en un archivo de consultas no llegan a produccion, y un
`case ... end` en su propia linea no es un falso positivo.

Uso: python3 solo-select.py <archivo.sql> [...]. Imprime cada sentencia
rechazada con su archivo y sale 1 si hay alguna; sale 0 si todas son
`select`/`with`. Un archivo ilegible tambien sale 1 (falla cerrado).
"""

from __future__ import annotations

import sys

PERMITIDAS = ("select", "with")


def sentencias(texto: str) -> list[str]:
    """Sentencias sin comentarios, partidas por `;` fuera de strings."""
    salida: list[str] = []
    actual: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c == "'" or c == '"':
            fin = c
            actual.append(c)
            i += 1
            while i < n:
                actual.append(texto[i])
                if texto[i] == fin:
                    if i + 1 < n and texto[i + 1] == fin:
                        actual.append(texto[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if texto.startswith("--", i):
            salto = texto.find("\n", i)
            i = n if salto == -1 else salto
            actual.append(" ")
            continue
        if texto.startswith("/*", i):
            cierre = texto.find("*/", i + 2)
            i = n if cierre == -1 else cierre + 2
            actual.append(" ")
            continue
        if c == ";":
            salida.append("".join(actual))
            actual = []
            i += 1
            continue
        actual.append(c)
        i += 1
    salida.append("".join(actual))
    return [s.strip() for s in salida if s.strip()]


def primera_palabra(sentencia: str) -> str:
    palabra = []
    for c in sentencia:
        if c.isalpha() or c == "_":
            palabra.append(c)
        else:
            break
    return "".join(palabra).lower()


def main(argv: list[str]) -> int:
    rechazos = 0
    for ruta in argv:
        try:
            with open(ruta, encoding="utf-8") as f:
                texto = f.read()
        except OSError as exc:
            print(f"{ruta}: ilegible ({exc})")
            rechazos += 1
            continue
        for sentencia in sentencias(texto):
            if primera_palabra(sentencia) not in PERMITIDAS:
                resumen = " ".join(sentencia.split())[:80]
                print(f"{ruta}: sentencia que no es select/with: {resumen}")
                rechazos += 1
    return 1 if rechazos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
