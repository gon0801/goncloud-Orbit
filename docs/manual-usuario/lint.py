"""Revisa el texto de paginas/*.html contra las reglas de CONTRATO.md.

Uso: python3 lint.py
Imprime archivo, hoja y la frase marcada. Los códigos internos (palabra_con_guion_bajo)
salen como AVISO: se permiten solo si la pantalla los muestra así y el texto los explica.
"""
import html
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent

PROHIBIDO = {
    "jerga": r"\b(API|JSON|endpoint|cron|SQL|flag|backend|frontend|query|script|deploy|commit|token de API|header|POST|GET)\b",
    "palabra vacía": r"\b(crucial|robust[oa]|potente|fundamental|asegura|garantiza que|cabe destacar|es importante)\b",
    "guion largo": r"[a-záéíóúñ,] — [a-záéíóúñ]",
}
AVISO = {
    "código interno": r"\b[a-z]+_[a-z_]+\b",
    "dos puntos a mitad": r"[a-záéíóúñ]: [a-záéíóúñ]",
}


def texto_de(fragmento):
    sin_tags = re.sub(r"<[^>]+>", " ", fragmento)
    return re.sub(r"\s+", " ", html.unescape(sin_tags)).strip()


def main():
    total = 0
    for archivo in sorted((RAIZ / "paginas").glob("*.html")):
        fuente = archivo.read_text(encoding="utf-8")
        for m in re.finditer(r"p[áa]g(?:ina|s?\.)\s*\d+", fuente):
            print(f"ERROR {archivo.name} [número de página escrito a mano, usa {{{{pag:<id>}}}}] {m.group(0)}")
            total += 1
        for n, seccion in enumerate(re.findall(r"<section\b.*?</section>", fuente, re.S), 1):
            texto = texto_de(seccion)
            for nivel, reglas in (("ERROR", PROHIBIDO), ("AVISO", AVISO)):
                for nombre, patron in reglas.items():
                    for m in re.finditer(patron, texto):
                        ini, fin = max(0, m.start() - 50), min(len(texto), m.end() + 50)
                        print(f"{nivel} {archivo.name} hoja {n} [{nombre}] …{texto[ini:fin]}…")
                        total += nivel == "ERROR"
    print(f"{total} errores")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
