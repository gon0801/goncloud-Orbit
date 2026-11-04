import json
import urllib.request

d = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/dashboard/cortes", timeout=20))
b = d["bloqueos_jev"]
print("interruptor", b["interruptor"])
for p in b["plataformas"]:
    print(p["plataforma"], "pausada", p["pausada"], "propuestas", len(p["propuestas"]))
