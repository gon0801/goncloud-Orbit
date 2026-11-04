"""Completa este programa sin cambiar el contrato de stdin/stdout."""

import json
import sys


def decidir(data: dict) -> dict:
    del data
    return {"action": "none", "reason": "sin_implementar"}


json.dump(decidir(json.load(sys.stdin)), sys.stdout)
