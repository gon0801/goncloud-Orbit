import inspect
from app.optimizer import caso, replay
from test_optimizer_caso import fabrica_caso

print("Firma reproduce:", inspect.signature(replay.reproduce))
base_json = fabrica_caso().como_json()
try:
    caso.CasoHoja.desde_json({**base_json, "campo_nuevo": None})
except ValueError as exc:
    print("Fallo con clave nueva en CasoHoja.desde_json:", exc)
