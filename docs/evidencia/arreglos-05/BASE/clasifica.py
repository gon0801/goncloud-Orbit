"""Clasifica los 31 casos base de ARREGLOS 05 comparando dos corridas del runner.

Uso desde la raiz del checkout:
    python docs/evidencia/arreglos-05/BASE/clasifica.py \
        docs/evidencia/arreglos-05/revision/acceptance.xml \
        docs/evidencia/arreglos-05/BASE/resultados.xml

El primer XML es la corrida original de la revision (ff03ed61); el segundo, la
corrida a clasificar. Imprime Markdown. Un fallo es de producto solo si repite
el de la revision original (misma excepcion y misma sentencia que fallo).
Una excepcion de carga o de archivo, o un rojo distinto del original, no lo es.
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# prefijo del test -> (hallazgo, dueno vigente segun COBERTURA.md, cerrado antes de 05)
DUENOS = {
    "01": ("control 01", "01K", True),
    "02": ("TN02", "04 B-C", True),
    "03": ("TN03", "05 B", False),
    "04": ("TN04", "04 W-P", True),
    "05": ("TN05", "03 D-G", True),
    "06": ("TN06", "03 D-M", True),
    "07": ("TN07", "03 P-G", True),
    "08": ("control 08", "01R", True),
    "09": ("control 09", "01 (de895d18)", True),
    "10": ("TN10", "05 B", False),
    "11": ("TN11", "05 C", False),
    "12": ("TN12", "05 F", False),
    "13": ("TN13", "05 G", False),
    "14": ("TN14", "03 D-M", True),
    "15": ("TN15", "04 W-E", True),
    "16": ("TN16", "04 W-D", True),
    "S1": ("TNS1", "03 D-N", True),
    "D2": ("control D2", "01E", True),
    "arreglos02": ("control ARREGLOS 02", "02 D", True),
    "control": ("control", "revision", True),
}


def leer(ruta: Path) -> dict[str, dict]:
    casos = {}
    for tc in ET.parse(ruta).getroot().iter("testcase"):
        nombre = tc.get("name", "")
        grupo = tc.get("classname", "").split(".")[-1].removeprefix("test_accept_")
        estado, tipo, texto = "pasa", "", ""
        for hijo in tc:
            if hijo.tag in {"failure", "error", "skipped"}:
                estado = {"failure": "falla", "error": "error", "skipped": "omite"}[hijo.tag]
                texto = hijo.text or ""
                # Este junit deja `type` vacio: la excepcion viaja en message y en la linea E.
                m = re.match(r"(\w+):", hijo.get("message") or "") or re.search(
                    r"^E\s+(\w+):", texto, re.M
                )
                tipo = m.group(1) if m else (hijo.get("type") or "")
        casos[nombre] = {"grupo": grupo, "estado": estado, "tipo": tipo, "texto": texto}
    return casos


def asercion(texto: str) -> str | None:
    """La sentencia que fallo (la primera linea `>` del reporte de pytest)."""
    m = re.search(r"^>\s+(.+)$", texto, re.M)
    return m.group(1).strip() if m else None


# Una prueba que ni carga, o que no encuentra un archivo, nunca es un fallo de producto.
SIEMPRE_ENTORNO = {
    "ModuleNotFoundError",
    "ImportError",
    "FileNotFoundError",
    "PermissionError",
    "OSError",
}


def dueno(nombre: str) -> tuple[str, str, bool]:
    resto = nombre.removeprefix("test_")
    for prefijo in ("arreglos02", "control"):
        if resto.startswith(prefijo):
            return DUENOS[prefijo]
    return DUENOS.get(resto.split("_", 1)[0], ("?", "?", False))


def clase_de(actual: dict, original: dict | None) -> str:
    if actual["estado"] == "pasa":
        return "verde"
    if actual["estado"] == "omite":
        return "omision"
    if actual["tipo"] in SIEMPRE_ENTORNO:
        return "fixture/entorno"
    reproduce = (
        original is not None
        and original["estado"] == "falla"
        and actual["tipo"] == original["tipo"]
        and asercion(actual["texto"]) == asercion(original["texto"])
    )
    # Producto solo si repite el fallo de la revision original (misma excepcion y
    # sentencia). Alli TN07 corriente es un CheckViolation del esquema, no un assert.
    if reproduce:
        return "producto"
    return "revisar" if actual["tipo"] == "AssertionError" else "fixture/entorno"


def clasificar(original: dict, actual: dict) -> list[tuple]:
    filas = []
    for nombre, act in actual.items():
        hallazgo, dueno_vigente, cerrado = dueno(nombre)
        orig = original.get(nombre)
        esperado = "verde" if cerrado else "producto"
        filas.append(
            (
                nombre,
                act["grupo"],
                hallazgo,
                dueno_vigente,
                orig["estado"] if orig else "nuevo",
                act["estado"],
                clase_de(act, orig),
                esperado,
            )
        )
    return filas


def main() -> None:
    original, actual = (leer(Path(p)) for p in sys.argv[1:3])
    filas = clasificar(original, actual)
    print("| Caso | Grupo | Hallazgo | Dueno vigente | En ff03ed61 | Ahora | Clase | Exigido |")
    print("|---|---|---|---|---|---|---|---|")
    for f in sorted(filas, key=lambda f: (f[2], f[0])):
        print("| " + " | ".join(f) + " |")
    totales: dict[str, int] = {}
    for f in filas:
        totales[f[6]] = totales.get(f[6], 0) + 1
    sin_cerrar = sum(1 for f in filas if f[7] == "producto")
    fuera = [f[0] for f in filas if f[6] != f[7]]
    print()
    print(f"- Casos recogidos: {len(filas)}.")
    print("- Por clase: " + ", ".join(f"{k} {v}" for k, v in sorted(totales.items())) + ".")
    print(
        f"- Cerrados antes de 05 y controles (deben estar verdes): {len(filas) - sin_cerrar};"
        f" de 05 (hoy rojos de producto): {sin_cerrar}."
    )
    print(
        f"- Casos fuera de lo exigido: {len(fuera)}" + (f": {', '.join(fuera)}." if fuera else ".")
    )
    sys.exit(1 if fuera else 0)


if __name__ == "__main__":
    main()
