import collections
import os

from app.bloqueo_dueno import lee_propuestas
from app.db import connect

with connect(os.environ["ORBIT_DSN_READ"]) as conn:
    conn.execute("SET TRANSACTION READ ONLY")
    print("transaction_read_only", conn.execute("SHOW transaction_read_only").fetchone()[0])
    c = collections.Counter(p.plataforma for p in lee_propuestas(conn))
    for k in sorted(c):
        print(k, c[k])
