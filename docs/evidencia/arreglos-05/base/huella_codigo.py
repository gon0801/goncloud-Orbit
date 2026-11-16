"""Solo lectura: huella del codigo de app; nunca abre secretos ni conecta la base."""
from pathlib import Path
import hashlib
import json

raiz = Path.cwd()
extensiones = {'.py', '.js', '.css', '.html'}
archivos = sorted(p for p in (raiz / 'app').rglob('*') if p.is_file() and p.suffix in extensiones)
h = hashlib.sha256()
for p in archivos:
    h.update(p.relative_to(raiz).as_posix().encode() + b'\0')
    h.update(hashlib.sha256(p.read_bytes()).digest())
print(json.dumps({'archivos': len(archivos), 'sha256_conjunto': h.hexdigest()}))
