# Pruebas nuevas del bloque A, demostradas fallando contra el SHA previo (regla 9)
SHA previo: 43d4e08d132b5234870138b4c76907039bbff473 (master con el bloque I)
Comando: PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_a_
Nota de esta PC (Windows): tests/test_corrige_descripciones_pez.py no colecciona por fcntl (solo Unix); fallo preexistente ajeno al bloque. En Linux/CI el comando corre sin --ignore.

```
    from app.optimizer import temporada
E   ImportError: cannot import name 'temporada' from 'app.optimizer' (C:\Users\ehven\.ao\data\worktrees\orbit-temporada-01\orbit-temporada-01-3\app\optimizer\__init__.py)
============================== warnings summary ===============================
.venv\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\ehven\.ao\data\worktrees\orbit-temporada-01\orbit-temporada-01-3\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
ERROR tests/test_temporada01_a.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
5979 deselected, 1 warning, 1 error in 3.02s
```
