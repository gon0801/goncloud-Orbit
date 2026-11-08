import datetime as dt
from decimal import Decimal

from app.optimizer.bid import (
    EXCESO_MINIMO,
    PAUSE_COST_MIN,
    POLITICA_PAUSE_ECONOMICA,
    _decide_pause,
    exceso_economico,
)
from app.optimizer.goals import (
    MARGEN_BANDA_MAX,
    MARGEN_BANDA_MIN,
    MARGEN_PASO_MAX_BAJADA,
    COOLDOWN,
    MedicionMargen,
    PELDANOS_CASCADA,
    peldano_target_acos,
    resuelve_target_margen,
)
from app.optimizer.politica import DIAS_EFECTO, RAZON_CRECE, RAZON_RETIENE, decide
from app.optimizer.windows import DIAS_FRESCURA_BIDS, DIAS_MADUREZ_CORTES, fin_ventana_cortes
from app.lecturas_caso import DIAS_INMADUROS_HASTA
from test_optimizer_caso import InsumosPausa, PrecioVentana, cambio, cortes, efecto, fabrica_caso, nivel, tramo, trayectoria
from test_optimizer_politica import _cuenta_llena

print("constantes", DIAS_EFECTO, COOLDOWN, DIAS_MADUREZ_CORTES, DIAS_FRESCURA_BIDS)
print("banda", MARGEN_BANDA_MIN, MARGEN_BANDA_MAX, "paso_bajada", MARGEN_PASO_MAX_BAJADA)
print("peldanos", PELDANOS_CASCADA)

print("\n=== apendice ===")
for clics in (99, 120):
    caso = fabrica_caso(
        propia=nivel(tramo(clics=clics, pedidos=0, venta="0", gasto="500")),
        pedidos_inmaduros=1,
        pausa=InsumosPausa(cortes(clicks=clics, cost="500"), 100, PAUSE_COST_MIN["amazon_mx"], None, None),
        observado_al=dt.datetime(2026, 10, 9, 8, 40, tzinfo=dt.UTC),
    )
    print(clics, decide(caso))

print("\n=== economica queda detras del umbral ===")
for cost in ("500", "1500"):
    agregado = cortes(clicks=120, cost=cost, revenue="0", orders=0)
    exceso = exceso_economico(agregado, Decimal("20.72"), "MXN")
    pausa, _ = _decide_pause(agregado, "MXN", 100, PAUSE_COST_MIN["amazon_mx"], Decimal("20.72"), POLITICA_PAUSE_ECONOMICA)
    print("cost", cost, "exceso", exceso, "minimo", EXCESO_MINIMO["MXN"], "motivo_devuelto", pausa.motivo)

print("\n=== meta 8 x 0.5 ===")
hoy = dt.date(2026, 9, 4)
med = MedicionMargen(
    margen_neto_pct=Decimal("8"),
    cobertura=Decimal("1"),
    dias_con_venta=90,
    venta_cubierta=Decimal("4000"),
    ledger_fresco_at=dt.datetime(2026, 9, 4, 12, 0),
    moneda="MXN",
    ventana_desde=dt.date(2026, 5, 1),
    ventana_hasta=dt.date(2026, 8, 20),
)
print("min(fraccion*margen, BANDA_MAX)", min(Decimal("0.5") * Decimal("8"), MARGEN_BANDA_MAX))
for ultimo in (None, Decimal("25"), Decimal("10")):
    res = resuelve_target_margen(med, Decimal("0.5"), hoy, ultimo, Decimal("20"))
    print("ultimo", ultimo, "aplicado", res.aplicado, "derivado", res.derivado, "motivo", res.motivo)

print("\n=== cascada ===")
print(
    "peldano",
    peldano_target_acos(None, None, Decimal("22"), Decimal("30"), Decimal("15")),
)

print("\n=== reloj repeticion contra ventana madura ===")
cambio_fecha = dt.date(2026, 10, 1)
confirmado = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.UTC)
uno = dt.timedelta(days=1)
dias_ingesta = {cambio_fecha + dt.timedelta(days=i) for i in range(0, 40)}
post_fin = cambio_fecha + dt.timedelta(days=7)
for delta in (7, 8, 16, 17):
    decision = cambio_fecha + dt.timedelta(days=delta)
    ayer = decision - dt.timedelta(days=DIAS_INMADUROS_HASTA)
    dias_post = sum(
        1 for n in range((ayer - cambio_fecha).days) if cambio_fecha + uno * (n + 1) in dias_ingesta
    )
    ahora = confirmado + dt.timedelta(days=delta)
    window_end = fin_ventana_cortes(ayer, ahora)
    print(
        "delta", delta,
        "decision", decision.isoformat(),
        "dias_post", dias_post,
        "bloquea_efecto", dias_post < DIAS_EFECTO,
        "cooldown", confirmado > (ahora - COOLDOWN),
        "window_end", window_end.isoformat(),
        "fin_post", post_fin.isoformat(),
        "post_maduro", window_end >= post_fin,
    )

print("\n=== veredictos vigentes ===")
propia = nivel(tramo(clics=50, pedidos=5, venta="5000", gasto="200", impresiones=2000))
caso_sube = fabrica_caso(
    propia=propia,
    grupo=propia,
    cuenta=_cuenta_llena(),
    precio=PrecioVentana(Decimal("100"), 50),
    trayectoria=trayectoria(
        [cambio(cambio_fecha, "8.00", "10.00", "motor")],
        efecto(dias_post=7, pre_impr=7000, post_impr=7700, clics_post=25, vendia=True),
    ),
)
ef = caso_sube.trayectoria.efecto
print("razon_trafico", ef.razon_trafico(), "crece_si", RAZON_CRECE, "veredicto", decide(caso_sube))
caso_recorte = fabrica_caso(
    propia=nivel(tramo(gasto="400")),
    trayectoria=trayectoria(
        [cambio(cambio_fecha, "12.00", "10.00", "motor")],
        efecto(dias_post=7, pre_impr=7000, post_impr=3500, clics_post=25, vendia=True),
    ),
)
print(
    "razon_recorte",
    caso_recorte.trayectoria.efecto.razon_trafico(),
    "retiene_si_menor",
    RAZON_RETIENE,
    "veredicto",
    decide(caso_recorte),
)
