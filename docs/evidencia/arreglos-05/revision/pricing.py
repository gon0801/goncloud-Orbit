"""Runner portable de aceptacion pricing. No edita producto ni usa APIs reales.

Desde /repo: ORBIT_REVIEW_SHA=<sha> ORBIT_TEST_DSN=<PG desechable> python -B
/evidence/pricing.py. Solo crea/elimina thermo_pricing_current; no paralelizar
este archivo consigo mismo. Las pruebas afirman comportamiento CORRECTO.
"""
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(os.environ.get('ORBIT_REVIEW_REPO', os.getcwd())).resolve()
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

if __name__ == '__main__':
    import pytest
    from app.precio import corrida
    from app.meli import precio_mercado

    print('SHA declarado:', os.environ.get('ORBIT_REVIEW_SHA', 'no declarado'), flush=True)
    print('Python:', sys.version, flush=True)
    print('Repo:', ROOT, flush=True)
    print('Import corrida:', corrida.__file__, flush=True)
    print('Import MercadoMeli:', precio_mercado.__file__, flush=True)
    raise SystemExit(pytest.main([
        '-v', '-s', '-p', 'no:cacheprovider', '--tb=short',
        str(Path(__file__).with_name('test_accept_pricing.py')), *sys.argv[1:]
    ]))
