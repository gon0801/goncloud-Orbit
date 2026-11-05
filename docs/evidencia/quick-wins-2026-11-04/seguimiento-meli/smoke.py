"""Smoke RO real del worker, sin instalar ni alterar reloj o fuentes."""

import json
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[3]
REMOTO = r"""
import base64,json,os,sqlite3,subprocess,sys,tempfile
from contextlib import closing
from pathlib import Path
os.umask(0o077)
entrada=json.load(sys.stdin)
comando=['docker','inspect','orbit-app-1','--format','{{json .Mounts}}']
mounts=json.loads(subprocess.check_output(comando,text=True))
assert not any('accounting' in m['Source'] or 'accounting' in m['Destination'] for m in mounts)
with tempfile.TemporaryDirectory(prefix='orbit-p01-smoke-ro-') as temporal:
 p=Path(temporal)/'accounting.db'
 uri='file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro'
 with (closing(sqlite3.connect(uri,uri=True,timeout=15)) as origen,
       closing(sqlite3.connect(p)) as destino):
  origen.backup(destino)
 p.chmod(0o600)
 logs={}
 for clave,nombre in [('costos','costos.log'),('meli','meli_ads.log')]:
  f=Path('/mnt/data/appdata/orbit/logs')/nombre
  logs[clave]='\n'.join(f.read_text().splitlines()[-100:]) if f.exists() else ''
 datos={'snapshot':base64.b64encode(p.read_bytes()).decode(),'conciliador':entrada['conciliador'],'logs':logs}
 r=subprocess.run(['docker','exec','-i','orbit-app-1','python','-c',entrada['worker']],input=json.dumps(datos),capture_output=True,text=True,timeout=180)
 try:
  resultado=json.loads(r.stdout)
 except ValueError:
  resultado={'estado':'FALLA','motivo':'worker sin JSON; stderr no publicado'}
 resultado['smoke']={'worker_exit':r.returncode,'montajes':mounts,
  'snapshot_mode':oct(p.stat().st_mode & 0o777),'instalado':False}
 print(json.dumps(resultado))
"""
entrada = {
    "worker": (BASE / "worker.py").read_text(),
    "conciliador": (
        REPO / "docs/evidencia/resumen-ads-multicanal/01-ingesta/"
        "30-revision-10266d91/scripts/conciliar.py"
    ).read_text(),
}
resultado = subprocess.run(
    ["ssh", "goncloud", "python3 -c " + "'" + REMOTO.replace("'", "'\"'\"'") + "'"],
    input=json.dumps(entrada),
    capture_output=True,
    text=True,
    timeout=210,
    check=True,
)
datos = json.loads(resultado.stdout)
(BASE / "smoke-resultado.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        {
            "estado": datos["estado"],
            "diferencias": datos.get("diferencias"),
            "sesion": datos.get("sesion"),
            "smoke": datos["smoke"],
        },
        ensure_ascii=False,
    )
)
