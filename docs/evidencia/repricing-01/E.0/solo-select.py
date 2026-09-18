"""Candado estructural de correr.sh: cada sentencia de un archivo de consultas
empieza con `select` o `with` (ORBIT · fase 10 · repricing-01 · E.0a).

Quita los comentarios `-- ...` hasta fin de linea (los de bloque se
rechazan, ver abajo) respetando los strings entre comillas simples (con
`''` como comilla escapada) y los identificadores entre comillas dobles,
parte por `;` fuera de strings y comentarios, y exige que la primera palabra
de cada sentencia no vacia sea `select` o `with`. Asi un `commit;`, `end;`,
`END WORK;`, `END/*x*/;`, `do $$ ... $$`, `set ...`, `revoke`, `analyze` o
`call` en un archivo de consultas no llegan a produccion, y un
`case ... end` en su propia linea no es un falso positivo.

Falla cerrado ante lo que no sabe partir (grok, cierre r2 y r3): **todo
`$` fuera de un string se rechaza**, lo que cubre el dollar-quoting de
PostgreSQL con cualquier tag (`$$`, `$q$`, `$é$`: dentro de el un `;` o una
comilla cambiarian donde termina cada sentencia) y los parametros
posicionales; un string o un comentario de bloque sin cerrar al final del
archivo tambien se rechaza, y **ningun comentario de bloque `/* */`
se admite**: PostgreSQL los anida y lee `/*/` como apertura, asi que el
partidor no podria cortar igual (revisor de la Fase 10, D1); las
consultas usan solo comentarios `--`. Los strings con escapes de diagonal (`E'\''`,
`U&'...'`) no llegan aqui: `correr.sh` rechaza antes toda diagonal
invertida, asi que las comillas solo se escapan como `''`.

Uso: python3 solo-select.py <archivo.sql> [...]. Imprime cada sentencia
rechazada con su archivo y sale 1 si hay alguna; sale 0 si todas son
`select`/`with`. Un archivo ilegible tambien sale 1 (falla cerrado).
"""

from __future__ import annotations

import sys

PERMITIDAS = ("select", "with")


class NoSeParte(ValueError):
    """El texto trae algo que este candado no sabe partir: falla cerrado."""


def sentencias(texto: str) -> list[str]:
    """Sentencias sin comentarios, partidas por `;` fuera de strings.

    Levanta `NoSeParte` ante dollar-quoting fuera de un string, o ante un
    string o comentario de bloque sin cerrar.
    """
    salida: list[str] = []
    actual: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c == "'" or c == '"':
            fin = c
            actual.append(c)
            i += 1
            cerrado = False
            while i < n:
                actual.append(texto[i])
                if texto[i] == fin:
                    if i + 1 < n and texto[i + 1] == fin:
                        actual.append(texto[i + 1])
                        i += 2
                        continue
                    i += 1
                    cerrado = True
                    break
                i += 1
            if not cerrado:
                raise NoSeParte("string sin cerrar al final del archivo")
            continue
        if c == "$":
            raise NoSeParte("un $ fuera de un string (dollar-quoting o parametro)")
        if texto.startswith("--", i):
            salto = texto.find("\n", i)
            i = n if salto == -1 else salto
            actual.append(" ")
            continue
        if texto.startswith("/*", i):
            # Ningun comentario de bloque se admite (revisor de la Fase 10,
            # D1): PostgreSQL los anida y su lexer lee `/*/` como apertura,
            # asi que imitarlo variante por variante no cierra la clase.
            # Las consultas usan solo comentarios `--`.
            raise NoSeParte("comentario de bloque /* */: no se admite")
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
        try:
            partes = sentencias(texto)
        except NoSeParte as exc:
            print(f"{ruta}: sentencia que no es select/with: no se puede partir ({exc})")
            rechazos += 1
            continue
        for sentencia in partes:
            if primera_palabra(sentencia) not in PERMITIDAS:
                resumen = " ".join(sentencia.split())[:80]
                print(f"{ruta}: sentencia que no es select/with: {resumen}")
                rechazos += 1
    return 1 if rechazos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
