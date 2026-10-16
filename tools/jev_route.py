#!/usr/bin/env python3
"""CLI del router Jev: un archivo JSON, una llamada (o ninguna).

Uso:
  python tools/jev_route.py --input tools/jev_examples/01-chief.json
      # dry-run: sin key, sin llamadas, imprime via dry-run
  TYPESAFE_API_KEY="$TYPESAFE_API_KEY" JEV_ROUTER_ENABLED=1 \
      python tools/jev_route.py --input tools/jev_examples/08-triple.json --live

El JSON trae {"casos": [{id, tipo, pregunta, opciones?|niveles?, estado?, umbral?}]}.
Jev solo decide; el llamador ejecuta y pide aprobacion para publicar o gastar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.jev_router import decidir_lote


def main() -> int:
    ap = argparse.ArgumentParser(description="Router Jev por archivo JSON")
    ap.add_argument("--input", required=True, help="JSON con {casos: [...]}")
    ap.add_argument(
        "--live",
        action="store_true",
        help="llamada real (exige JEV_ROUTER_ENABLED=1 y TYPESAFE_API_KEY)",
    )
    args = ap.parse_args()

    datos = json.loads(Path(args.input).read_text(encoding="utf-8"))
    casos = datos.get("casos", [])
    if not args.live:
        os.environ.pop("TYPESAFE_API_KEY", None)
    print(json.dumps(decidir_lote(casos, log=None), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
