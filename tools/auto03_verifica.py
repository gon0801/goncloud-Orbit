"""CLI delgado de verificacion de decisiones por producto (AUTO-03).

El motor vive en `app/productos_verifica.py`; este punto de entrada se mantiene
para `Dockerfile` y `tools/despliegue/desplegar.sh` (`python -m tools.auto03_verifica`).
"""

from __future__ import annotations

import sys

from app.productos_verifica import (  # noqa: F401 - re-export estable del CLI
    CERO,
    PLAZO_CONCILIA_S,
    TOLERANCIA_DINERO,
    Comparacion,
    SinReporte,
    Totales,
    amazon_por_fecha,
    campos_distintos,
    comparaciones,
    concilia,
    descarga_reportes,
    diferencias,
    diferencias_pausas,
    evalua,
    main,
    replay,
    simula,
    tramo_halo_maduro,
)

if __name__ == "__main__":
    sys.exit(main())
