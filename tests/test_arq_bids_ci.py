"""El paso de guardas del job `rapido` corre `tests/test_arq_bids_*.py` (BIDS 02 0.b).

Sin base: lee el workflow como tests/test_precommit_hooks.py:104. Este archivo
existe para que el patron del paso de guardas siempre case con algo: un patron
que no encuentra ningun archivo hace fallar a pytest con codigo 4.
"""

from __future__ import annotations

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]


def test_paso_guardas_nombra_test_arq_bids():
    """El comando pytest del paso de guardas nombra `tests/test_arq_bids_*.py`."""
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    pasos = workflow["jobs"]["rapido"]["steps"]
    guardas = next(paso for paso in pasos if paso.get("name", "").startswith("Guardas"))
    assert "tests/test_arq_bids_*.py" in guardas["run"], (
        "el paso de guardas debe correr tests/test_arq_bids_*.py "
        f"(comando actual: {guardas['run']!r})"
    )
