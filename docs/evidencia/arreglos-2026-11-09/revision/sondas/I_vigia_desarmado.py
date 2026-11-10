"""Sonda I-2: impulso cuyo lote quedo 'desarmado' (boton PAUSAR GRUPO de la
fabrica). El vigia llega a 'pausar' y revienta todos los dias: nunca guarda
lectura final, el impulso sigue 'vivo' para siempre."""
import datetime as dt
from decimal import Decimal
import tools.fabrica_campanas as fc
from app import impulso_io as io

HOY = dt.date(2026, 11, 9)
LANZADO = dt.datetime(2026, 10, 20, tzinfo=dt.UTC)

class R:
    def __init__(self, filas): self.filas = filas
    def fetchone(self): return self.filas[0] if self.filas else None
    def fetchall(self): return self.filas

class Conn:
    def __init__(self): self.escrito = []
    def commit(self): pass
    def rollback(self): pass
    def execute(self, sql, p=None):
        s = " ".join(sql.split())
        if "max(v.metric_date)" in s: return R([(HOY - dt.timedelta(days=1),)])
        if "count(DISTINCT v.metric_date)" in s: return R([(19,)])
        if "FROM campana_grupo_rol r JOIN impulso" in s: return R([(11,), (12,)])
        if "v.metric_date, v.impressions" in s:  # sin gasto: campanas pausadas
            return R([(HOY - dt.timedelta(days=d), 10, 0, Decimal("0"), 0) for d in range(1, 20)])
        if "SELECT hecho FROM impulso_lectura" in s: return R([])
        if "SELECT estado, pausado_por FROM fabrica_lote" in s: return R([("desarmado", None)])
        if "fabrica_lote_paso WHERE lote" in s and "recurso = 'campaign'" in s: return R([(1,)])
        if s.startswith("INSERT INTO impulso_lectura"): self.escrito.append(p); return R([])
        raise AssertionError("SQL no previsto: " + s[:90])

conn = Conn()

class CtxFalso: pass
def pausar_lote_real_sin_red(ctx, lote, actor):
    # la primera linea real de _cambia_estado_lote con el estado 'desarmado'
    raise fc.Abortar(f"lote {lote} desarmado: nunca revive")

pausador = io.PausadorFabrica(conn)
pausador._ctx_para = lambda lote: CtxFalso()
fc.pausar_lote = pausar_lote_real_sin_red

fila = (7, "amazon_mx", "impulso-5-abcd1234", Decimal("1000"), "MXN", Decimal("71.43"), 14,
        LANZADO, 3, 5, "LANZAR IMPULSO DE 1 PRODUCTO")
for dia in range(3):
    try:
        io._vigila_uno(conn, pausador, fila, hoy=HOY + dt.timedelta(days=dia), escritor_retiro=None)
        print("dia", dia, "atendido")
    except Exception as exc:
        print("dia", dia, "FALLA:", type(exc).__name__, exc.status if hasattr(exc, "status") else "", exc)
print("lecturas guardadas:", len(conn.escrito))
