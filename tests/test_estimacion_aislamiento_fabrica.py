"""AC10: la estimacion no contamina caminos comerciales de fabrica.

Cambiar solo el sobre S5 (contribucion/estado) con la misma solicitud y la
misma economia observada deja plan, huella, payloads, elegibilidad y
recuperacion identicos. GET catalogo/evaluacion siguen exponiendo estimacion.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from psycopg.types.json import Json
from test_api_fabrica import (
    HEADERS,
    _crear,
    _escenario_leido,
    _listing_mx,
    _motor_simulado,
    _preview,
    _solicitud_v2,
    escenario,
)
from test_architecture import APP, RAIZ, _imports_runtime, _violaciones
from test_schema import _postgres_obligatorio_ausente

from app import fabrica_plan as fp
from app.estimacion_proyeccion import proyeccion_s5

assert escenario is not None

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

PROHIBIDOS_ESTIMACION = (
    "app.estimacion_proyeccion",
    "app.estimacion_repository",
    "app.estimacion_venta",
)
TOKENS_FUENTE = (
    "estimacion_proyeccion",
    "estimacion_repository",
    "estimacion_venta",
)
CLAVES_INCIDENTALES = frozenset({"created_at", "finished_at", "observed_at"})


def _caminos_comerciales() -> list[Path]:
    rutas = [RAIZ / "app" / "ads" / "write.py", RAIZ / "app" / "fabrica_plan.py"]
    rutas.extend(sorted(p for p in (APP / "optimizer").rglob("*.py") if p.is_file()))
    return rutas


def _sobre_disponible() -> dict:
    return proyeccion_s5(_escenario_leido(1))


def _sobre_incompleta() -> dict:
    return proyeccion_s5(
        _escenario_leido(
            1,
            id=100,
            estado="incompleta",
            motivos=("precio_ausente",),
            contribucion=None,
            contribucion_pct=None,
            moneda=None,
            canonical_input={"resultado": {"estado": "incompleta"}},
        )
    )


def _fijar_sobre(monkeypatch, sobre: dict) -> None:
    payload = copy.deepcopy(sobre)

    def fake(_conn, listing_ids, *, as_of):
        return {lid: copy.deepcopy(payload) for lid in listing_ids}

    monkeypatch.setattr("app.fabrica_web.adjuntar_estimaciones", fake)


def _tiene_clave(obj, clave: str) -> bool:
    if isinstance(obj, dict):
        if clave in obj:
            return True
        return any(_tiene_clave(valor, clave) for valor in obj.values())
    if isinstance(obj, list):
        return any(_tiene_clave(valor, clave) for valor in obj)
    return False


def _sin_incidental(obj):
    if isinstance(obj, dict):
        return {k: _sin_incidental(v) for k, v in obj.items() if k not in CLAVES_INCIDENTALES}
    if isinstance(obj, list):
        return [_sin_incidental(v) for v in obj]
    return obj


def _payloads_amazon(vista: dict) -> list[tuple]:
    plan_json = vista["plan"]
    plan = (
        fp.plan_v2_desde_json(plan_json)
        if plan_json.get("schema_version") == 2
        else fp.plan_desde_json(plan_json)
    )
    return [
        (paso.rol, paso.recurso, paso.path, paso.payload)
        for rol in fp.ROLES_ORDEN_CREACION
        for paso in fp.pasos_del_rol(plan, rol)
    ]


def _pub_catalogo(data: dict, listing_id: int) -> dict:
    return next(
        pub
        for prod in data["productos"]
        for pub in prod["publicaciones"]
        if pub["id"] == listing_id
    )


def test_sobres_discriminantes_difieren():
    a = _sobre_disponible()
    b = _sobre_incompleta()
    assert a["estado"] == "disponible"
    assert a["contribucion"] == "42.5000"
    assert a["contribucion_pct"] == "36.6379"
    assert b["estado"] == "incompleta"
    assert b["contribucion"] is None
    assert b["contribucion_pct"] is None
    assert a["estado"] != b["estado"]
    assert a["contribucion"] != b["contribucion"]


def test_optimizer_write_y_plan_no_importan_estimacion():
    fugas = []
    for path in _caminos_comerciales():
        rel = path.relative_to(RAIZ).as_posix()
        viol = _violaciones(_imports_runtime(path), PROHIBIDOS_ESTIMACION)
        if viol:
            fugas.append(f"{rel}: {viol}")
        texto = path.read_text(encoding="utf-8")
        for token in TOKENS_FUENTE:
            if token in texto:
                fugas.append(f"{rel} menciona {token}")
    assert fugas == [], fugas


@_skip_db
def test_mismo_plan_huella_y_payload_con_estimacion_distinta(escenario, monkeypatch):
    cliente, conn, solicitud, _, ids = escenario
    lid = _listing_mx(conn, ids[0])
    a = _sobre_disponible()
    b = _sobre_incompleta()

    _fijar_sobre(monkeypatch, a)
    cat_a = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx")
    eva_a = cliente.get("/api/fabrica/evaluacion?plataforma=amazon_mx")
    plan_a = _preview(cliente, solicitud)
    assert cat_a.status_code == eva_a.status_code == 200

    _fijar_sobre(monkeypatch, b)
    cat_b = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx")
    eva_b = cliente.get("/api/fabrica/evaluacion?plataforma=amazon_mx")
    plan_b = _preview(cliente, solicitud)
    assert cat_b.status_code == eva_b.status_code == 200

    pub_a = _pub_catalogo(cat_a.json(), lid)
    pub_b = _pub_catalogo(cat_b.json(), lid)
    eva_pub_a = next(p for p in eva_a.json()["publicaciones"] if p["listing_id"] == lid)
    eva_pub_b = next(p for p in eva_b.json()["publicaciones"] if p["listing_id"] == lid)

    assert pub_a["estimacion"]["estado"] == "disponible"
    assert pub_b["estimacion"]["estado"] == "incompleta"
    assert pub_a["estimacion"]["contribucion"] != pub_b["estimacion"]["contribucion"]
    assert eva_pub_a["estimacion"]["estado"] == "disponible"
    assert eva_pub_b["estimacion"]["estado"] == "incompleta"

    assert pub_a["elegible"] is True and pub_a["elegible"] == pub_b["elegible"]
    assert eva_pub_a["seleccionable"] is True
    assert eva_pub_a["seleccionable"] == eva_pub_b["seleccionable"]

    observado = "40.00000000000000000"
    assert pub_a["margen_neto_pct"] == observado
    assert pub_b["margen_neto_pct"] == observado
    assert eva_pub_a["economia"]["margen_neto_pct"] == observado
    assert eva_pub_b["economia"]["margen_neto_pct"] == observado
    assert eva_pub_a["economia"]["margen_neto_pct"] != a["contribucion_pct"]

    cuerpo_a = _sin_incidental(plan_a)
    cuerpo_b = _sin_incidental(plan_b)
    assert cuerpo_a == cuerpo_b
    assert plan_a["huella"] == plan_b["huella"]
    assert plan_a["plan"]["target_acos_pct"] == plan_b["plan"]["target_acos_pct"]
    assert plan_a["plan"]["parametros"] == plan_b["plan"]["parametros"]
    assert not _tiene_clave(plan_a, "estimacion")
    assert not _tiene_clave(plan_b, "estimacion")
    assert _payloads_amazon(plan_a) == _payloads_amazon(plan_b)


@_skip_db
def test_plan_v2_idempotente_ante_cambio_solo_de_estimacion(escenario, monkeypatch):
    cliente, conn, solicitud, _, ids = escenario
    listing = _listing_mx(conn, ids[0])
    v2 = _solicitud_v2(solicitud, [listing])

    _fijar_sobre(monkeypatch, _sobre_disponible())
    plan_a = _preview(cliente, v2)
    _fijar_sobre(monkeypatch, _sobre_incompleta())
    plan_b = _preview(cliente, v2)

    assert plan_a["plan"]["schema_version"] == 2
    assert _sin_incidental(plan_a) == _sin_incidental(plan_b)
    assert plan_a["huella"] == plan_b["huella"]
    assert plan_a["plan"]["objetivo"] == plan_b["plan"]["objetivo"]
    assert [p["margen_neto_pct"] for p in plan_a["plan"]["publicaciones"]] == [
        p["margen_neto_pct"] for p in plan_b["plan"]["publicaciones"]
    ]
    assert not _tiene_clave(plan_a, "estimacion")
    assert _payloads_amazon(plan_a) == _payloads_amazon(plan_b)


@_skip_db
def test_recuperacion_v1_no_cambia_si_cambia_estimacion(escenario, monkeypatch):
    cliente, conn, solicitud, fw, _ = escenario
    _fijar_sobre(monkeypatch, _sobre_disponible())
    vista = _preview(cliente, solicitud)
    _motor_simulado(monkeypatch, fw, conn)
    creado = _crear(cliente, solicitud, vista["huella"])
    assert creado.status_code == 200, creado.text

    lote_a = cliente.get("/api/fabrica/lotes/" + vista["lote"])
    listado_a = cliente.get("/api/fabrica/lotes?plataforma=amazon_mx")
    assert lote_a.status_code == 200

    _fijar_sobre(monkeypatch, _sobre_incompleta())
    lote_b = cliente.get("/api/fabrica/lotes/" + vista["lote"])
    listado_b = cliente.get("/api/fabrica/lotes?plataforma=amazon_mx")
    plan_otra_vez = _preview(cliente, solicitud)

    assert lote_a.json() == lote_b.json()
    assert listado_a.json() == listado_b.json()
    assert plan_otra_vez["huella"] == vista["huella"]
    assert not _tiene_clave(lote_a.json(), "estimacion")
    assert not _tiene_clave(creado.json(), "estimacion")


@_skip_db
def test_recuperacion_v2_y_acciones_ignoran_estimacion(escenario, monkeypatch):
    cliente, conn, solicitud, fw, ids = escenario
    listing = _listing_mx(conn, ids[0])
    conn.execute(
        "INSERT INTO config_version(label, settings) VALUES ('v2', %s)",
        (Json({"ads_target_fraccion_margen_amazon_mx": "0.5", "fabrica.creacion": "v2"}),),
    )
    v2 = _solicitud_v2(solicitud, [listing])
    _fijar_sobre(monkeypatch, _sobre_disponible())
    vista = _preview(cliente, v2)
    _motor_simulado(monkeypatch, fw, conn)
    creado = _crear(cliente, v2, vista["huella"])
    assert creado.status_code == 200, creado.text
    detalle_a = cliente.get("/api/fabrica/lotes/" + vista["lote"]).json()

    ejecutadas = []

    def reconciliar(args):
        ejecutadas.append(("reconciliar", args.lote, args.plataforma))
        return 0

    monkeypatch.setattr(fw.fc, "_reconciliar_cmd", reconciliar)
    _fijar_sobre(monkeypatch, _sobre_incompleta())
    detalle_b = cliente.get("/api/fabrica/lotes/" + vista["lote"]).json()
    rec = cliente.post(
        f"/api/fabrica/lotes/{vista['lote']}/reconciliar",
        headers=HEADERS,
        json={"confirmacion": "RECONCILIAR GRUPO"},
    )
    plan_b = _preview(cliente, v2)

    assert detalle_a == detalle_b
    assert rec.status_code == 200, rec.text
    assert plan_b["huella"] == vista["huella"]
    assert ejecutadas == [("reconciliar", vista["lote"], "amazon_mx")]
    assert not _tiene_clave(creado.json(), "estimacion")
    assert not _tiene_clave(detalle_a, "estimacion")
    assert not _tiene_clave(rec.json(), "estimacion")
