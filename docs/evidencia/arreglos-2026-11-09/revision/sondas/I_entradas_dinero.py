"""Sonda I-3: entradas raras en las rutas que escriben dinero."""
from decimal import Decimal
from pydantic import ValidationError
from app.api_write import CuerpoAplicaAjuste, CuerpoSettings, CuerpoGoal
from app.api_fabrica import SolicitudImpulsoPlan
from app.campana_ajustes import planea_ajuste, CambiarPresupuesto, CambiarAjusteUbicacion
from app.ads.campana_config import ConfigCampana

def prueba(nombre, fn):
    try:
        r = fn()
        print(f"ACEPTA  {nombre}: {r}")
    except ValidationError as e:
        print(f"RECHAZA {nombre}: {e.errors()[0]['type']}")
    except Exception as e:
        print(f"RECHAZA {nombre}: {type(e).__name__}: {e}")

base = dict(campana_id=1, clase="presupuesto", huella="x", confirmacion="APLICAR AJUSTE", actor="d")
for v in ["NaN", "Infinity", "-1", "0", "1e25", "1e40"]:
    prueba(f"ajuste presupuesto={v}", lambda v=v: CuerpoAplicaAjuste(**base, presupuesto=v).presupuesto)
prueba("ajuste porcentaje=True", lambda: CuerpoAplicaAjuste(**{**base, "clase": "ajuste_ubicacion"}, ubicacion="arriba_de_busqueda", porcentaje=True).porcentaje)
vig = ConfigCampana("123", Decimal("100.00"), "MXN", "LEGACY_FOR_SALES", 0, 0, 0, None)
for v in ["1e25", "1e27"]:
    prueba(f"planea presupuesto={v}", lambda v=v: planea_ajuste(vig, CambiarPresupuesto(Decimal(v)), campana_id=1, plataforma="amazon_mx").frase)
prueba("planea ubicacion 900", lambda: planea_ajuste(vig, CambiarAjusteUbicacion("arriba_de_busqueda", 900), campana_id=1, plataforma="amazon_mx").frase)
prueba("planea ubicacion 901", lambda: planea_ajuste(vig, CambiarAjusteUbicacion("arriba_de_busqueda", 901), campana_id=1, plataforma="amazon_mx").frase)
prueba("settings caps bid=True", lambda: CuerpoSettings(base_config_version_id=1, caps={"bid": True}).caps)
prueba("settings target=Infinity", lambda: CuerpoSettings(base_config_version_id=1, target_manual_pct="Infinity").target_manual_pct)
prueba("settings target=100000", lambda: CuerpoSettings(base_config_version_id=1, target_manual_pct="100000").target_manual_pct)
prueba("goal ceiling=4500", lambda: CuerpoGoal(bid_ceiling="4500").bid_ceiling)
prueba("impulso tope=1e3", lambda: SolicitudImpulsoPlan(plataforma="amazon_mx", listing_ids=[1], subfamilia_por_listing={"1": "x"}, tope_monto="1e3").tope_monto)
prueba("impulso tope=9999999999", lambda: SolicitudImpulsoPlan(plataforma="amazon_mx", listing_ids=[1], subfamilia_por_listing={"1": "x"}, tope_monto="9999999999").tope_monto)
