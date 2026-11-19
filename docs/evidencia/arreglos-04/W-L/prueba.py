"""Prueba real del bloque W-L (ARREGLOS 04, fabrica.md F3), para en_prod.sh.

Funciones puras del nucleo de fabrica: sin base, sin red, sin escritura en disco.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

from app.fabrica_plan import (
    ROLES_CON_NEGATIVO_CRUZADO,
    ROLES_IMPULSO,
    ROLES_ORDEN_CREACION,
    ObjetivoPlanV2,
    ParametrosRol,
    PlanGrupoV2,
    PublicacionGrupoV2,
    Semillas,
    huella_plan_v2,
    negativos_cruzados,
    pasos_del_rol,
    target_del_grupo,
)

assert ROLES_CON_NEGATIVO_CRUZADO == ("category_phrase", "category_broad", "auto_discovery"), (
    ROLES_CON_NEGATIVO_CRUZADO
)
assert "product_targeting" not in ROLES_CON_NEGATIVO_CRUZADO

semillas_probe = Semillas(("arras",), (), (), ("arras",))
assert negativos_cruzados(semillas_probe, ROLES_ORDEN_CREACION) == ("arras",)
assert negativos_cruzados(semillas_probe, ROLES_IMPULSO) == ()

res_target = target_del_grupo([Decimal("38.20")], Decimal("0.5"))
plan = PlanGrupoV2(
    platform="amazon_mx",
    tipo_producto="collar_perro",
    nombre_base="Collar reflectante",
    fecha=dt.date(2026, 9, 5),
    moneda="MXN",
    modo="shadow",
    publicaciones=(
        PublicacionGrupoV2(
            listing_id=11,
            product_id=1,
            asin="B0AAAAAAAA",
            seller_sku="SS-1",
            platform="amazon_mx",
            margen_neto_pct=Decimal("38.20"),
        ),
    ),
    parametros={
        rol: ParametrosRol(rol=rol, budget=Decimal("120"), bid=Decimal("5.00"))
        for rol in ROLES_ORDEN_CREACION
    },
    objetivo=ObjetivoPlanV2(
        "margen_medido",
        res_target.aplicado,
        res_target.procedencia,
        res_target.fraccion,
        res_target.derivado,
    ),
    semillas=Semillas(
        keywords=("arras", "cofre"),
        asins=("B0BBBBBBBB",),
        negativos=("arras", "plata"),
        exact=("arras",),
    ),
)
assert huella_plan_v2(plan) == "c391fb86bf8e2d99b6ab88b8b1d62c636117dac9c85ac7b32395439bcb970007"

neg_arras = {"keywordText": "arras", "matchType": "NEGATIVE_EXACT", "state": "ENABLED"}
neg_plata = {"keywordText": "plata", "matchType": "NEGATIVE_EXACT", "state": "ENABLED"}

for rol in ("category_phrase", "category_broad"):
    negs = [p.payload for p in pasos_del_rol(plan, rol) if p.recurso == "negative_keyword"]
    assert negs == [neg_arras], (rol, negs)

assert [
    p.payload for p in pasos_del_rol(plan, "auto_discovery") if p.recurso == "negative_keyword"
] == [
    neg_arras,
    neg_plata,
]
assert [
    p for p in pasos_del_rol(plan, "product_targeting") if p.recurso == "negative_keyword"
] == []
assert [p for p in pasos_del_rol(plan, "category_exact") if p.recurso == "negative_keyword"] == []

plan_impulso = replace(
    plan,
    roles=ROLES_IMPULSO,
    parametros={
        rol: ParametrosRol(rol=rol, budget=Decimal("120"), bid=Decimal("5.00"))
        for rol in ROLES_IMPULSO
    },
    semillas=Semillas((), (), (), ("arras",)),
)
assert [
    p for p in pasos_del_rol(plan_impulso, "auto_discovery") if p.recurso == "negative_keyword"
] == []

print("W-L OK")
