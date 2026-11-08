import datetime as dt
from decimal import Decimal
from app import cycle
from app.optimizer.goals import Goal

class SinBase:
    def execute(self, *args, **kwargs):
        raise AssertionError("La sonda no debe consultar ninguna base")

class LecturasEspia:
    llamadas = 0
    def caso(self, *args, **kwargs):
        self.llamadas += 1
        raise AssertionError("La hoja bloqueada no llega a armar el caso")

goal = Goal("platform", None, "amazon_mx", Decimal("20"), Decimal("1"),
            Decimal("50"), "MXN", None, None, None, True, "live")
contadores = cycle._Contadores()
lecturas = LecturasEspia()
pendientes = []
cycle._procesa_decisora(
    SinBase(), fila=(1, 2, 3, Decimal("10"), "MXN", "ENABLED", None, "ENABLED", "ENABLED"),
    platform="amazon_mx", setting_target=Decimal("20"), goals=(goal, {}),
    modo="live", decided_at=dt.datetime(2026, 11, 6, tzinfo=dt.UTC),
    contadores=contadores, pendientes=pendientes, tick=lambda: None,
    evidencia_ad_groups={}, corte_pause_por_grupo={},
    bloqueadas={(1, "entity_cut", None)}, inertes=set(),
    margen_plataforma=None, snapshot_margen={}, familias_ciclo=None,
    pause_economica=True, politica="niveles_v3", lecturas=lecturas,
)
print("skips=", dict(contadores.skips_entidad))
print("casos_evaluados=", lecturas.llamadas)
print("decisiones_pendientes=", len(pendientes))
