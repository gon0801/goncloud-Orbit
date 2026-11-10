"""Sonda C: el episodio congelado en inputs.caso reproduce la decision (replay)."""
import json
import sys

sys.path.insert(0, "/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt")
sys.argv = ["x"]
import runpy  # noqa: E402

m = runpy.run_path("C_m6_politica.py", run_name="sonda")  # reutiliza constructores
from app.optimizer.caso import CasoHoja  # noqa: E402
from app.optimizer.politica import decide  # noqa: E402
from app.optimizer.replay import reproduce  # noqa: E402

D = m["D"]
casos = {
    "subida frenada": m["caso"](m["ruido"], cambio=m["SUBIDA"], efecto=m["EFECTO_SUBIDA"]),
    "sin episodio": m["caso"](None, cambio=m["SUBIDA"], efecto=m["EFECTO_SUBIDA"]),
    "episodio margen 8 con contribucion de 28 digitos": m["caso"](
        m["epi"](m["ven"](m["PRE"], "3.3333", "71.1111", 1, 4), m["ven"](m["POST"], "7.7777", "93.3333", 2, 5), "7.123456789"),
        cambio=m["SUBIDA"], efecto=m["EFECTO_SUBIDA"]),
}
print("\n== Replay ==")
for nombre, caso in casos.items():
    congelado = json.loads(json.dumps(caso.como_json()))
    vuelta = CasoHoja.desde_json(congelado)
    inputs = {"politica": "niveles_v3", "caso": congelado}
    print(f"  {nombre}: igual={vuelta == caso} decide={decide(caso)} replay={reproduce(inputs)}")
