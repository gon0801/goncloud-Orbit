"""Mutantes de dos ediciones de A.6 (S3, R1C2): siembra, corre el test y restaura.

Uso, desde la raiz del repo con la base local de pruebas arriba:
    python3 docs/evidencia/repricing-01/R.1/remutacion/muta_dos_A6.py
Mismo metodo que `muta.py`: cambio exacto, base real, cache de bytecode nueva,
restauracion de los bytes originales y `git status` limpio del archivo.
"""

import os
import subprocess
import tempfile
from pathlib import Path

RAIZ = Path.cwd()
ARCHIVO = RAIZ / "app/cli.py"
DSN = "postgresql://orbit:orbit@localhost:5432/postgres"
IMPORT_TARDIO = "        from app.notifica import avisar_precio\n"
CABECERA = "from app.redaction import scrub\n"
INICIO_PRECIO = "    from app.precio import corrida as corrida_precios\n"


def main() -> None:
    original = ARCHIVO.read_bytes()
    texto = original.decode()
    assert texto.count(IMPORT_TARDIO) == 1
    sin_import = texto.replace(IMPORT_TARDIO, "")
    casos = {
        # S3: el import sube a la cabecera del modulo.
        "S3": sin_import.replace(
            CABECERA, "from app.notifica import avisar_precio\n" + CABECERA, 1
        ),
        # R1C2: el import sube al inicio de `_precio`, antes de `--reporte`.
        "R1C2": sin_import.replace(
            INICIO_PRECIO, INICIO_PRECIO + "    from app.notifica import avisar_precio\n", 1
        ),
    }
    for caso, mutado in casos.items():
        assert mutado != texto
        ARCHIVO.write_text(mutado, encoding="utf-8")
        try:
            env = dict(os.environ, ORBIT_TEST_DSN=DSN, PYTHONPYCACHEPREFIX=tempfile.mkdtemp())
            proc = subprocess.run(
                [str(RAIZ / ".venv/bin/python"), "-m", "pytest", "tests/test_precio_pantalla.py"]
                + ["-q", "-p", "no:cacheprovider", "-x"],
                env=env,
                capture_output=True,
                text=True,
            )
        finally:
            ARCHIVO.write_bytes(original)
        ultima = [linea for linea in proc.stdout.splitlines() if linea.strip()][-1]
        veredicto = {1: "MUERTO", 0: "SOBREVIVE"}.get(proc.returncode, "INVALIDO")
        print(caso, veredicto, ultima)
    estado = subprocess.run(
        ["git", "status", "--porcelain", "app/cli.py"], capture_output=True, text=True
    ).stdout
    print("limpio" if not estado else "SUCIO")


if __name__ == "__main__":
    main()
