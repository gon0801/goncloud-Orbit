"""Sonda C (M5): construccion del episodio por LecturasPlataforma.caso con una
conexion falsa (sin base). Muestra:

1. Persistencia: el mismo episodio sigue maduro y aplicable de D=x+17 a
   D=x+90 si no hay otro evento; la subida queda frenada todo ese tiempo.
2. Corte de 90 dias: un cambio de puja en [x-7, x-1] desaparece de `cambios`
   cuando cae antes de D-90 (filtro de _SQL_CAMBIOS) y el episodio pasa de
   incomparable a maduro.
3. Ancla: con un ajuste de campana posterior, las ventanas se miden alrededor
   del ajuste (cambios[-1]) pero el episodio dice fecha_cambio = cambio real.
"""

import datetime as dt
import sys
from decimal import Decimal as D

sys.path.insert(
    0,
    "/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt",
)

from app.lecturas_caso import LecturasPlataforma  # noqa: E402
from app.optimizer.caso import (  # noqa: E402
    BidVigente,
    CambioBid,
    EconomiaPlataforma,
    EvidenciaNivel,
    InsumosPausa,
    PrecioVentana,
    Tramo,
)
from app.optimizer.politica import decide  # noqa: E402

X = dt.date(2026, 8, 1)


class _Fila:
    def __init__(self, fila):
        self._fila = fila

    def fetchone(self):
        return self._fila


class ConexionFalsa:
    """Devuelve una fila fija del efecto: pre7/post con trafico que crece,
    40 clics al bid nuevo; ventana economica pre con 1 venta de 30 y post con
    3 clics y 0 ventas."""

    def __init__(self):
        self.params = None

    def execute(self, sql, params=()):
        self.params = params
        efecto = (7, 700, 20, 7, 840, 30, 40, D("20"), 30, 20, D("17"), 30, 2)
        econ_pre = (7, D("4"), D("30"), D("30"), 1, 4)
        econ_post = (7, D("5"), D("0"), D("0"), 0, 3)
        return _Fila(efecto + econ_pre + econ_post)


def vendedora():
    return EvidenciaNivel(
        reciente=Tramo(clics=40, pedidos=2, venta=D("200"), gasto=D("20"), impresiones=1000),
        antiguo=Tramo(clics=0, pedidos=0, venta=D("0"), gasto=D("0"), impresiones=0),
    )


def lecturas(d: dt.date, cambios):
    dias = frozenset(d - dt.timedelta(days=n) for n in range(1, 181))
    return LecturasPlataforma(
        plataforma="amazon_us",
        decidido_el=dt.datetime.combine(d, dt.time(12), tzinfo=dt.UTC),
        economia=EconomiaPlataforma(
            moneda="USD",
            equilibrio_acos_pct=None,
            gasto_para_concluir=D("36"),
            confianza_recorte=D("0.80"),
            confianza_subida=D("0.70"),
        ),
        _por_hoja={1: vendedora()},
        _por_grupo={2: vendedora()},
        _cuenta=vendedora(),
        _cambios={1: cambios},
        _precio={1: PrecioVentana(gasto=D("5"), clics=10)},
        _inmaduros={1: 0},
        _campanas={1: 3},
        _eventos_campana={},
        _eventos_grupo={},
        _con_economia=True,
        _dias_con_ingesta=dias,
        _d=d,
        _visto_el=None,
        _observado_al=None,
    )


PAUSA = InsumosPausa(
    cortes=None, umbral_clics=100, gasto_minimo=D("40"), expected_clicks=None, politica_economica=None
)


def corre(d, cambios):
    lect = lecturas(d, cambios)
    caso = lect.caso(
        ConexionFalsa(),
        hoja_id=1,
        ad_group_id=2,
        bid=BidVigente(valor=D("1.00"), moneda="USD", piso=D("0.20"), techo=D("3.00")),
        target_acos_pct=D("25"),
        pausa=PAUSA,
    )
    return caso, decide(caso)


def visible(d, cambios):
    """Filtro de _SQL_CAMBIOS: confirmado_el >= D-90 (medianoche UTC)."""
    desde = d - dt.timedelta(days=90)
    return tuple(c for c in cambios if c.fecha >= desde)


SUBIDA = CambioBid(fecha=X, bid_antes=D("0.87"), bid_despues=D("1.00"), origen="motor")

print("== 1. Persistencia del freno de subida (sin otros eventos) ==")
for n in (16, 17, 30, 60, 90, 91):
    d = X + dt.timedelta(days=n)
    cambios = visible(d, (SUBIDA,))
    if not cambios:
        lect = lecturas(d, ())
        caso = lect.caso(
            ConexionFalsa(),
            hoja_id=1,
            ad_group_id=2,
            bid=BidVigente(valor=D("1.00"), moneda="USD", piso=D("0.20"), techo=D("3.00")),
            target_acos_pct=D("25"),
            pausa=PAUSA,
        )
        print(f"  D=x+{n:3d}: sin cambios en 90 dias -> {decide(caso)}")
        continue
    caso, v = corre(d, cambios)
    e = caso.trayectoria.episodio
    print(f"  D=x+{n:3d}: episodio estado={e.estado} aplicable={e.aplicable} -> {v}")

print()
print("== 2. Cambio previo dentro de [x-7, x-1] que sale de la ventana de 90 dias ==")
PREVIO = CambioBid(fecha=X - dt.timedelta(days=3), bid_antes=D("0.76"), bid_despues=D("0.87"), origen="motor")
for n in (60, 86, 87, 88):
    d = X + dt.timedelta(days=n)
    cambios = visible(d, (PREVIO, SUBIDA))
    caso, v = corre(d, cambios)
    e = caso.trayectoria.episodio
    print(
        f"  D=x+{n}: cambios visibles={[c.fecha.isoformat() for c in cambios]} "
        f"estado={e.estado} motivo={e.motivo} pre={e.pre.desde}..{e.pre.hasta} -> {v}"
    )

print()
print("== 3. Ajuste de campana posterior al cambio real ==")
AJUSTE = CambioBid(fecha=X + dt.timedelta(days=5), bid_antes=D("1.00"), bid_despues=D("1.00"), origen="ajuste_de_campana")
caso, v = corre(X + dt.timedelta(days=40), (SUBIDA, AJUSTE))
e = caso.trayectoria.episodio
print(f"  fecha_cambio={e.fecha_cambio} pre={e.pre.desde}..{e.pre.hasta} post={e.post.desde}..{e.post.hasta}")
print(f"  estado={e.estado} aplicable={e.aplicable} motivo_aplicabilidad={e.motivo_aplicabilidad} -> {v}")
