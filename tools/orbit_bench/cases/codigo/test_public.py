import json
import subprocess
import sys

entrada = {
    "platform": "amazon_us",
    "clicks": 100,
    "orders": 0,
    "cost": "40.0000",
    "currency": "USD",
    "metric_date": "2026-08-20",
    "decided_at": "2026-08-30",
}
process = subprocess.run(
    [sys.executable, "solution.py"],
    input=json.dumps(entrada),
    capture_output=True,
    text=True,
    check=True,
)
assert json.loads(process.stdout) == {"action": "pause", "reason": "corte"}
print("ok")
