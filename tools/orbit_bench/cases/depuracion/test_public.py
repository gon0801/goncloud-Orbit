import json
import subprocess
import sys


def ejecutar(entrada: dict) -> dict:
    process = subprocess.run(
        [sys.executable, "solution.py"],
        input=json.dumps(entrada),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(process.stdout)


actual = {
    "mode": "current",
    "platform": "amazon_us",
    "clicks": 100,
    "orders": 0,
    "cost": "40.0000",
    "currency": "USD",
}
assert ejecutar(actual) == {"action": "pause", "reason": "corte"}

historica = {
    "mode": "replay",
    "platform": "amazon_us",
    "clicks": 25,
    "orders": 0,
    "cost": "12.0000",
    "currency": "USD",
    "frozen_policy": {"min_clicks": 25, "min_cost": "12.0000", "currency": "USD"},
}
assert ejecutar(historica) == {"action": "pause", "reason": "corte"}
print("ok")
