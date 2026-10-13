#!/usr/bin/env bash
# B1 (R14-r3): el paso 8 da FALLA con la moneda cruzada. Adaptado del
# repro del revisor a la lectura por HTTP. Sirve un JSON con la forma
# real de /api/dashboard/busquedas-sin-venta y la moneda cruzada.
set -uo pipefail
PUERTO=${1:-18099}
python3 - "$PUERTO" << 'PY' &
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/dashboard/busquedas-sin-venta"):
            moneda = "USD" if "amazon_mx" in self.path else "MXN"
            plat = "amazon_mx" if "amazon_mx" in self.path else "amazon_us"
            cuerpo = json.dumps({"plataforma": plat, "moneda": moneda, "asins": [], "palabras": []}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
SRV=$!
sleep 1
D=docs/evidencia/bids-02/ejecucion/D.6
ORBIT_SIMULACION=1 ORBIT_SIM_DSN=postgresql://orbit:orbit@127.0.0.1:5433/no_existe_r14 \
  ORBIT_SIM_API=http://127.0.0.1:$PUERTO bash $D/checklist.sh forzada 2>/dev/null | sed -n '/== 8)/,/== RESULTADO/p'
COD=${PIPESTATUS[0]}
kill $SRV
echo "exit=$COD"
