"""Sonda J5 (solo lectura): el bloque instalador de docs/DEPLOY.md, corrido sobre las
lineas de cron vigentes con cron-avisa (evidencia del 4 y 9-nov), las borra y deja la
version sin cron-avisa. Reproduce el filtro `grep -v` del bloque con Python, sin tocar cron.

Uso: python J5_instalador_quita_cron_avisa.py <copia>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WT = Path(sys.argv[1])
deploy = (WT / "docs/DEPLOY.md").read_text(encoding="utf-8")
inst = deploy.split("ORBIT_BLOCK=$(cat <<'CRON'")[1]
bloque = inst.split("\nCRON\n")[0].strip("\n").splitlines()
filtro_linea = next(l for l in inst.splitlines() if l.startswith("{ crontab -u gon -l"))
patrones = re.findall(r'grep -v "([^"]+)"', filtro_linea)
print("patrones grep -v del instalador:", patrones)

vigente = [
    l
    for l in (WT / "docs/evidencia/estado-planes-2026-11-04/cron-vigente.txt").read_text().splitlines()
    if "cron-avisa" in l or l.startswith("#")
]
vigente.append(
    "10 13 * * * /usr/local/bin/cron-avisa --nombre \"Corrida de precios de Amazon\" --cada 24 -- "
    "/bin/bash -c '/usr/bin/flock -n -E 75 /tmp/precio-spapi.lock docker exec orbit-app-1 python -m "
    "app.cli precio --platform amazon_mx --platform amazon_us' >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1"
)
resultado = [l for l in vigente if not any(p in l for p in patrones)] + bloque
print("\nANTES (lineas con cron-avisa de la evidencia):")
for l in vigente:
    print("  ", l[:150])
print("\nDESPUES de correr el instalador, lineas de ads-salud:")
for l in resultado:
    if "ads-salud" in l:
        print("  ", l[:150], "| cron-avisa:", "cron-avisa" in l)
