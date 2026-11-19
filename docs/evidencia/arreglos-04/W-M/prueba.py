"""Prueba real del bloque W-M (fabrica.md F1 y F5), para en_prod.sh.

Funciones puras con conexion falsa: sin base, sin red, sin escritura.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import inspect

from app import pantalla_productos, retiro_anuncios

HOY = dt.date(2026, 11, 12)
ANUNCIOS_DB = {
    10: ("ad-vende", "ag-1", "Grupo 1", "c-1", "SV-1", "B0WM000001"),
    20: ("ad-ads", "ag-2", "Grupo 2", "c-2", "SV-2", "B0WM000002"),
    30: ("ad-huerf", "ag-3", "Grupo 3", "c-3", "SV-3", "B0WM000003"),
    40: ("ad-cero", "ag-4", "Grupo 4", "c-4", "SV-4", "B0WM000004"),
}


class _Resultado:
    def __init__(self, filas):
        self._filas = list(filas)

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return list(self._filas)


class _ConnFalsa:
    def __init__(self, *, libro: dt.datetime | None, vendio: dict[int, bool]):
        self.libro = libro
        self.vendio = dict(vendio)

    def execute(self, sql: str, params=()):
        texto = " ".join(sql.split())
        if "FROM ingest_run" in texto and "accounting_ledger_events" in texto:
            return _Resultado([(self.libro,)])
        if "FROM listing li LEFT JOIN ventas v USING (product_id)" in texto:
            pedidos = set(params[-1])
            return _Resultado(
                [(lid, si) for lid, si in sorted(self.vendio.items()) if lid in pedidos]
            )
        if "FROM ad_entity pa" in texto and "pa.listing_id = ANY" in texto:
            pedidos = set(params[1])
            filas = [(lid, *ANUNCIOS_DB[lid]) for lid in sorted(pedidos) if lid in ANUNCIOS_DB]
            return _Resultado(filas)
        raise AssertionError(f"SQL inesperado: {texto}")


parametros_planea = inspect.signature(retiro_anuncios.planea_retiro).parameters
kw_hoy = {"hoy": HOY} if "hoy" in parametros_planea else {}

libro_ok = dt.datetime.combine(HOY, dt.time(8, 15), tzinfo=dt.UTC)
conn_ok = _ConnFalsa(libro=libro_ok, vendio={10: True, 20: True, 40: False})
plan_ok = retiro_anuncios.planea_retiro(
    conn_ok,
    "amazon_mx",
    listing_ids=(10, 20, 30, 40),
    motivo="sin_pedidos",
    conservar_grupo_id=None,
    **kw_hoy,
)
obtenidos = [a.ad_id_externo for a in plan_ok.anuncios]
assert obtenidos == ["ad-cero"], f"anuncios en el plan: {obtenidos} != ['ad-cero']"

assert "hoy" in parametros_planea, "planea_retiro no acepta el parametro hoy"
assert getattr(retiro_anuncios, "DIAS_LIBRO_AL_DIA", None) == 2, (
    "retiro_anuncios.DIAS_LIBRO_AL_DIA debe ser 2"
)
assert "li.seller_sku = u.advertised_sku" in pantalla_productos._SQL_CANDIDATOS, (
    "_SQL_CANDIDATOS aun une clics solo por ASIN sin filtrar seller_sku"
)

campos_plan = {f.name for f in dataclasses.fields(retiro_anuncios.PlanRetiro)}
for campo in ("vendieron", "ventas_al_dia", "ventas_al_dia_hasta"):
    assert campo in campos_plan, f"PlanRetiro no tiene el campo {campo}"

assert plan_ok.vendieron == (10, 20), plan_ok.vendieron
assert plan_ok.ventas_al_dia is True
assert plan_ok.ventas_al_dia_hasta is None
como_dict = plan_ok.como_dict()
assert como_dict["vendieron"] == [10, 20]
assert como_dict["ventas_al_dia"] is True
assert como_dict["ventas_al_dia_hasta"] is None

libro_viejo = dt.datetime.combine(HOY - dt.timedelta(days=3), dt.time(8, 15), tzinfo=dt.UTC)
conn_vieja = _ConnFalsa(libro=libro_viejo, vendio={40: False})
plan_viejo = retiro_anuncios.planea_retiro(
    conn_vieja,
    "amazon_mx",
    listing_ids=(40,),
    motivo="sin_pedidos",
    conservar_grupo_id=None,
    hoy=HOY,
)
assert plan_viejo.anuncios == (), f"saco anuncios con libro viejo: {plan_viejo.anuncios}"
assert plan_viejo.vendieron == ()
assert plan_viejo.ventas_al_dia is False
assert plan_viejo.ventas_al_dia_hasta == libro_viejo
assert plan_viejo.como_dict()["ventas_al_dia"] is False
assert plan_viejo.como_dict()["ventas_al_dia_hasta"] == libro_viejo.isoformat()

hoy_cruce = dt.date(2026, 11, 19)
libro_cruce_mx = dt.datetime(2026, 11, 17, 4, 0, tzinfo=dt.UTC)
plan_cruce = retiro_anuncios.planea_retiro(
    _ConnFalsa(libro=libro_cruce_mx, vendio={40: False}),
    "amazon_mx",
    listing_ids=(40,),
    motivo="sin_pedidos",
    conservar_grupo_id=None,
    hoy=hoy_cruce,
)
assert plan_cruce.anuncios == (), (
    f"saco anuncios con carga del 16-nov 22:00 CDMX: {plan_cruce.anuncios}"
)
assert plan_cruce.ventas_al_dia is False
assert plan_cruce.ventas_al_dia_hasta == libro_cruce_mx

libro_borde_mx = dt.datetime(2026, 11, 17, 6, 0, tzinfo=dt.UTC)
plan_borde = retiro_anuncios.planea_retiro(
    _ConnFalsa(libro=libro_borde_mx, vendio={40: False}),
    "amazon_mx",
    listing_ids=(40,),
    motivo="sin_pedidos",
    conservar_grupo_id=None,
    hoy=hoy_cruce,
)
assert [a.ad_id_externo for a in plan_borde.anuncios] == ["ad-cero"]
assert plan_borde.ventas_al_dia is True

print("W-M OK")
