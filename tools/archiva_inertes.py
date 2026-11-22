#!/usr/bin/env python3
"""CLI delgado de archivo MANUAL de keywords inertes con reversa (BIDS 01, D5).

El motor vive en `app/ads/archivar_inertes.py`. Este punto de entrada se
mantiene compatible con ejecucion por stdin dentro del contenedor app:

    docker exec -i orbit-app-1 python - < tools/archiva_inertes.py
    docker exec -i orbit-app-1 python - --acepto-mutacion-real \\
      --esperado 12 --huella <sha> --go "<literal>" < tools/archiva_inertes.py
    docker exec -i orbit-app-1 python - --reponer inertes-2026-09-05 \\
      --acepto-mutacion-real --go "<literal>" < tools/archiva_inertes.py
"""

from __future__ import annotations

import sys

from app.ads.archivar_inertes import Abortar, _log, main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        _log("ABORTAR_FAIL_CLOSED", motivo=str(exc))
        sys.exit(2)
