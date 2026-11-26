"""ENVIOS 01 §4: lector unico, API `/api/envios`, etiquetas y pantalla `/envios`."""

from __future__ import annotations

import html as html_lib
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from formato_js import sin_registro
from helpers.pg import db_desde_template
from test_envios_estado import envio_de_prueba
from test_fabrica_f2 import _skip_db
from test_ola_a2_frontend import JS_DIR, _node
from test_schema import _test_dsn

from app import api_envios, etiquetas
from app.api import _conexion_lectura
from app.envios.estado import Estado, Motivo, Tipo
from app.envios.lectura import cuenta_atencion, envios_actuales, ultimas_lecturas
from app.envios.modelo import Articulo, EnvioObservado, PaqueteObservado
from app.envios.store import guardar
from app.main import app

AHORA = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)
D = timedelta(days=1)

MX_DEVOLUCION = "701-1111111-1111111"
MX_FBA = "701-2222222-2222222"
US_DHL = "111-3333333-3333333"
US_SIN_DESPACHAR = "111-4444444-4444444"
ML_RECOGER = "44000000001"
ML_ENTREGADO = "44000000002"
ML_ATRASADO = "44000000003"

# Atencion primero y, dentro de cada grupo, el plazo mas proximo; sin plazo al final.
# Enviado sin confirmacion y esperando recoleccion no tienen plazo: quedan junto
# al cerrado, desempatados por plataforma y referencia (misma fecha de pedido).
ORDEN_TODOS = [
    US_SIN_DESPACHAR,
    MX_DEVOLUCION,
    ML_ATRASADO,
    MX_FBA,
    US_DHL,
    ML_RECOGER,
    ML_ENTREGADO,
]


def _corrida(conn, source: str, platform: str, hace: timedelta, ok: bool | None = True) -> int:
    return conn.execute(
        "INSERT INTO ingest_run (source, platform, started_at, ok)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (source, platform, AHORA - hace, ok),
    ).fetchone()[0]


def _paquete(ref: str, **cambios) -> PaqueteObservado:
    base = dict.fromkeys(
        ("transportista", "servicio", "guia", "despachado_el", "estado_fuente", "detalle_fuente")
    )
    return PaqueteObservado(paquete_ref=ref, **(base | cambios))


def _envio(ref: str, **cambios) -> EnvioObservado:
    cambios.setdefault("pedidos", (ref,))
    cambios.setdefault("pedido_creado_el", AHORA - 5 * D)
    return envio_de_prueba(envio_ref=ref, **cambios)


def _meli(ref: str, pedido: str, status: str, substatus: str | None = None, **cambios):
    return _envio(
        ref,
        platform="meli",
        pedidos=(pedido,),
        logistica_fuente="xd_drop_off",
        estado_fuente=status,
        subestado_fuente=substatus,
        **cambios,
    )


def _articulo(pedido: str, sku: str | None, titulo: str | None, cantidad: int | None = 1):
    return Articulo(pedido_ref=pedido, sku=sku, titulo=titulo, cantidad=cantidad, producto_ref=None)


def _siembra(conn) -> None:
    """Corridas: MX justo en el limite de 8 h, US atrasada, MeLi sin lectura correcta."""
    run = _corrida(conn, "spapi_orders", "amazon_mx", 8 * H)
    _corrida(conn, "spapi_orders", "amazon_mx", 1 * H, ok=False)
    _corrida(conn, "spapi_orders", "amazon_us", 9 * H)
    _corrida(conn, "spapi_orders", "amazon_us", 30 * H)
    _corrida(conn, "spapi_inventory", "amazon_us", 1 * H)
    _corrida(conn, "envios_meli", "meli", 1 * H, ok=False)
    _corrida(conn, "envios_meli", "meli", 2 * H, ok=None)

    antes = _envio(
        MX_DEVOLUCION,
        estado_fuente="SHIPPED",
        paquetes=(_paquete("P-VIEJO", transportista="AMZMX", guia="GUIA-VIEJA"),),
    )
    guardar(conn, [antes], observed_at=AHORA - 3 * D, run_id=run)
    envios = [
        _meli(
            ML_ENTREGADO,
            "2000000002",
            "delivered",
            entrega_prometida_el=AHORA - 10 * D,
            entregado_el=AHORA - 9 * D,
        ),
        _envio(
            MX_FBA,
            logistica_fuente="AMAZON",
            estado_fuente="SHIPPED",
            entrega_prometida_el=AHORA + 3 * D,
            articulos=(_articulo(MX_FBA, "SKU-FBA", "Lampara"),),
        ),
        _meli(
            ML_RECOGER,
            "2000000001",
            "shipped",
            "waiting_for_withdrawal",
            pack_ref="2000009999",
            entrega_prometida_el=AHORA - 4 * D,
            entrega_limite_el=AHORA + 1 * D,
            paquetes=(
                _paquete(ML_RECOGER, transportista="Agencias Mercado Libre", guia="GUIA-ML-1"),
            ),
        ),
        _envio(
            US_DHL,
            platform="amazon_us",
            estado_fuente="SHIPPED",
            entrega_prometida_el=AHORA - 1 * D,
            articulos=(_articulo(US_DHL, "SKU-US", "<script>alert(1)</script>", 2),),
            paquetes=(
                _paquete(
                    "PKG-1",
                    transportista="DHL",
                    guia="GUIA-US-1",
                    despachado_el=AHORA - 4 * D,
                    estado_fuente="SHIPPED",
                ),
                _paquete("PKG-2", transportista="DHL", guia="GUIA-US-2", estado_fuente="SHIPPED"),
            ),
        ),
        _meli(
            ML_ATRASADO,
            "2000000003",
            "ready_to_ship",
            "delayed",
            entrega_prometida_el=AHORA - 3 * D,
            articulos=(_articulo("2000000003", None, None, None),),
        ),
        _envio(
            MX_DEVOLUCION,
            estado_fuente="SHIPPED",
            entrega_prometida_el=AHORA - 2 * D,
            paquetes=(
                _paquete(
                    "P-1",
                    transportista="AMZMX",
                    guia="GUIA-MX-1",
                    estado_fuente="UNDELIVERABLE",
                    detalle_fuente="RETURNING_TO_SELLER",
                ),
            ),
        ),
        _envio(
            US_SIN_DESPACHAR,
            platform="amazon_us",
            estado_fuente="UNSHIPPED",
            despachar_antes_de=AHORA - 3 * D,
            entrega_prometida_el=AHORA + 5 * D,
        ),
    ]
    guardar(conn, envios, observed_at=AHORA - 20 * H, run_id=run)


@contextmanager
def _base(monkeypatch) -> Iterator[tuple]:
    monkeypatch.setattr(api_envios, "ahora_utc", lambda: AHORA)
    with db_desde_template("orbit_envios_api", base=_test_dsn(), autocommit=True) as (conn, _):
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            yield conn, TestClient(app)
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)


def _refs(cliente: TestClient, consulta: str) -> list[str]:
    resp = cliente.get(f"/api/envios?{consulta}")
    assert resp.status_code == 200, resp.text
    return [e["envio_ref"] for e in resp.json()["envios"]]


def _fecha(texto: str) -> datetime:
    return datetime.fromisoformat(texto)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@_skip_db
def test_api_envios_contrato_orden_filtros_y_paginacion(monkeypatch):
    with _base(monkeypatch) as (conn, cliente):
        _siembra(conn)
        conn.execute("SET ROLE app_read")
        resp = cliente.get("/api/envios?vista=todos")
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert _fecha(cuerpo["generado_el"]) == AHORA
        assert cuerpo["ventana_dias"] == 30
        lecturas = cuerpo["lecturas"]
        assert _fecha(lecturas["amazon_mx"]["ultima_ok"]) == AHORA - 8 * H
        assert lecturas["amazon_mx"]["atrasada"] is False
        assert _fecha(lecturas["amazon_us"]["ultima_ok"]) == AHORA - 9 * H
        assert lecturas["amazon_us"]["atrasada"] is True
        assert lecturas["meli"] == {"ultima_ok": None, "atrasada": True}
        assert cuerpo["resumen"] == {"atencion": 3, "en_curso": 6, "cerrados": 1, "todos": 7}
        assert (cuerpo["total"], cuerpo["page"], cuerpo["paginas"]) == (7, 1, 1)
        assert [e["envio_ref"] for e in cuerpo["envios"]] == ORDEN_TODOS

        por_ref = {e["envio_ref"]: e for e in cuerpo["envios"]}
        assert {ref: (e["estado"], e["tipo"], e["motivo"]) for ref, e in por_ref.items()} == {
            MX_DEVOLUCION: ("en_devolucion", "amazon_fbm", "problema_entrega"),
            MX_FBA: ("enviado_sin_confirmacion", "amazon_fba", None),
            US_DHL: ("enviado_sin_confirmacion", "amazon_fbm", None),
            US_SIN_DESPACHAR: ("por_despachar", "amazon_fbm", "despacho_vencido"),
            ML_RECOGER: ("esperando_recoleccion", "meli_envios", None),
            ML_ENTREGADO: ("entregado", "meli_envios", None),
            ML_ATRASADO: ("por_despachar", "meli_envios", "despacho_vencido"),
        }
        assert [p["guia"] for p in por_ref[US_DHL]["paquetes"]] == ["GUIA-US-1", "GUIA-US-2"]
        assert [p["guia"] for p in por_ref[MX_DEVOLUCION]["paquetes"]] == ["GUIA-MX-1"]
        assert por_ref[ML_ATRASADO]["articulos"] == [
            {
                "pedido_ref": "2000000003",
                "sku": None,
                "titulo": None,
                "cantidad": None,
                "producto_ref": None,
            }
        ]
        assert por_ref[ML_ATRASADO]["despachar_antes_de"] is None
        assert por_ref[ML_RECOGER]["pack_ref"] == "2000009999"

        assert _refs(cliente, "") == ORDEN_TODOS[:6]
        assert _refs(cliente, "vista=en_curso") == ORDEN_TODOS[:6]
        assert _refs(cliente, "vista=atencion") == ORDEN_TODOS[:3]
        assert _refs(cliente, "vista=cerrados") == [ML_ENTREGADO]
        assert _refs(cliente, "vista=todos&plataforma=meli") == [
            ML_ATRASADO,
            ML_RECOGER,
            ML_ENTREGADO,
        ]
        assert _refs(cliente, "tipo=amazon_fba") == [MX_FBA]
        assert _refs(cliente, "estado=en_devolucion") == [MX_DEVOLUCION]
        assert _refs(cliente, "q=1111111-11") == [MX_DEVOLUCION]
        assert _refs(cliente, "q=2000000003") == [ML_ATRASADO]
        assert _refs(cliente, "q=guia-us-2") == [US_DHL]
        assert _refs(cliente, "q=sku-fba") == [MX_FBA]
        assert _refs(cliente, "q=GUIA-VIEJA&vista=todos") == []
        assert _refs(cliente, "q=2000000002") == []
        assert _refs(cliente, "q=2000000002&vista=todos") == [ML_ENTREGADO]
        assert _refs(cliente, "plataforma=amazon_us&estado=por_despachar") == [US_SIN_DESPACHAR]

        filtrado = cliente.get("/api/envios?plataforma=amazon_us").json()
        assert filtrado["total"] == 2
        assert filtrado["resumen"] == cuerpo["resumen"]

        contador = cliente.get("/api/envios/contador")
        assert contador.status_code == 200
        assert contador.json() == {"atencion": 3}
        assert cuenta_atencion(conn, ahora=AHORA) == 3


@_skip_db
def test_api_envios_pagina_de_50(monkeypatch):
    with _base(monkeypatch) as (conn, cliente):
        run = _corrida(conn, "envios_meli", "meli", 1 * H)
        envios = [
            _meli(f"5500{n:04d}", f"3000{n:04d}", "shipped", entrega_limite_el=AHORA + n * H)
            for n in range(1, 52)
        ]
        guardar(conn, envios, observed_at=AHORA - H, run_id=run)
        primera = cliente.get("/api/envios").json()
        assert (primera["total"], primera["page"], primera["paginas"]) == (51, 1, 2)
        assert len(primera["envios"]) == 50
        assert _refs(cliente, "page=2") == ["55000051"]
        assert _refs(cliente, "page=3") == []

        html = cliente.get("/envios?plataforma=meli").text
        assert 'href="/envios?vista=en_curso&amp;plataforma=meli&amp;page=2"' in html
        assert "página 1 de 2" in html
        segunda = cliente.get("/envios?plataforma=meli&page=2").text
        assert 'href="/envios?vista=en_curso&amp;plataforma=meli"' in segunda
        assert re.findall(r'data-envio="(\d+)"', segunda) == ["55000051"]


@_skip_db
def test_por_despachar_no_queda_bajo_los_enviados_sin_confirmacion(monkeypatch):
    # Poblacion de la sonda: los enviados sin confirmacion nunca cierran y su
    # fecha de entrega ya paso.
    with _base(monkeypatch) as (conn, cliente):
        run = _corrida(conn, "spapi_orders", "amazon_us", 1 * H)
        enviados_us = [
            _envio(
                f"111-{n:07d}-0000000",
                platform="amazon_us",
                estado_fuente="SHIPPED",
                entrega_prometida_el=AHORA - 10 * D,
                paquetes=(_paquete("1", transportista="DHL", estado_fuente="SHIPPED"),),
            )
            for n in range(40)
        ]
        enviados_fba = [
            _envio(
                f"701-{n:07d}-0000000",
                logistica_fuente="AMAZON",
                estado_fuente="SHIPPED",
                entrega_prometida_el=AHORA - 8 * D,
            )
            for n in range(14)
        ]
        por_despachar = [
            _envio(
                f"111-9999999-000000{n}",
                platform="amazon_us",
                estado_fuente="UNSHIPPED",
                despachar_antes_de=AHORA + n * H,
                entrega_prometida_el=AHORA + 5 * D,
            )
            for n in (1, 2, 3)
        ]
        envios = enviados_us + enviados_fba + por_despachar
        guardar(conn, envios, observed_at=AHORA - H, run_id=run)

        primera = cliente.get("/api/envios").json()
        assert (primera["total"], primera["paginas"]) == (57, 2)
        assert primera["resumen"]["atencion"] == 0
        assert [e["envio_ref"] for e in primera["envios"][:3]] == [
            "111-9999999-0000001",
            "111-9999999-0000002",
            "111-9999999-0000003",
        ]


@pytest.mark.parametrize(
    "consulta",
    ["vista=otro", "plataforma=ebay", "tipo=x", "estado=x", "page=0"],
)
def test_api_envios_vocabulario_invalido_422(consulta):
    app.dependency_overrides[_conexion_lectura] = lambda: None
    try:
        cliente = TestClient(app)
        assert cliente.get(f"/api/envios?{consulta}").status_code == 422
        assert cliente.get(f"/envios?{consulta}").status_code == 422
    finally:
        app.dependency_overrides.pop(_conexion_lectura, None)


# ---------------------------------------------------------------------------
# Lector
# ---------------------------------------------------------------------------


@_skip_db
def test_lectura_atrasada_congela_juzgado_al(monkeypatch):
    with _base(monkeypatch) as (conn, cliente):
        run = _corrida(conn, "spapi_orders", "amazon_mx", 12 * H)
        pendiente = _envio(
            MX_DEVOLUCION, estado_fuente="UNSHIPPED", despachar_antes_de=AHORA - 6 * H
        )
        sin_lectura = _meli(ML_ATRASADO, "2000000003", "handling", despachar_antes_de=AHORA - 6 * H)
        guardar(conn, [pendiente, sin_lectura], observed_at=AHORA - 12 * H, run_id=run)

        por_ref = {v.envio.envio_ref: v for v in envios_actuales(conn, ahora=AHORA)}
        assert por_ref[MX_DEVOLUCION].juzgado_al == AHORA - 12 * H
        assert por_ref[MX_DEVOLUCION].clasificacion.motivo is None
        # Sin ninguna lectura correcta se juzga al instante en que se observo.
        assert por_ref[ML_ATRASADO].juzgado_al == AHORA - 12 * H
        assert por_ref[ML_ATRASADO].clasificacion.motivo is None
        lecturas = cliente.get("/api/envios").json()["lecturas"]
        assert lecturas["amazon_mx"]["atrasada"] is True

        _corrida(conn, "spapi_orders", "amazon_mx", 1 * H)
        por_ref = {v.envio.envio_ref: v for v in envios_actuales(conn, ahora=AHORA)}
        assert por_ref[MX_DEVOLUCION].juzgado_al == AHORA - 1 * H
        assert por_ref[MX_DEVOLUCION].clasificacion.motivo == "despacho_vencido"

        # Una corrida fechada despues de `ahora` no adelanta el juicio.
        _corrida(conn, "spapi_orders", "amazon_mx", -3 * H)
        por_ref = {v.envio.envio_ref: v for v in envios_actuales(conn, ahora=AHORA)}
        assert por_ref[MX_DEVOLUCION].juzgado_al == AHORA


@_skip_db
def test_ultimas_lecturas_sin_corridas(monkeypatch):
    with _base(monkeypatch) as (conn, cliente):
        assert ultimas_lecturas(conn) == {"amazon_mx": None, "amazon_us": None, "meli": None}
        cuerpo = cliente.get("/api/envios").json()
        assert cuerpo["resumen"] == {"atencion": 0, "en_curso": 0, "cerrados": 0, "todos": 0}
        assert (cuerpo["total"], cuerpo["paginas"], cuerpo["envios"]) == (0, 1, [])
        html = cliente.get("/envios").text
        assert "Sin envíos con esta vista y estos filtros." in html


@_skip_db
def test_ventana_de_30_dias_por_fecha_del_pedido(monkeypatch):
    with _base(monkeypatch) as (conn, _cliente):
        run = _corrida(conn, "envios_meli", "meli", 1 * H)
        envios = [
            _meli("1", "11", "shipped", pedido_creado_el=AHORA - 30 * D),
            _meli("2", "12", "shipped", pedido_creado_el=AHORA - 30 * D - H),
            _meli("3", "13", "shipped", pedido_creado_el=None),
        ]
        guardar(conn, envios, observed_at=AHORA - H, run_id=run)
        viejo = _meli("4", "14", "shipped", pedido_creado_el=None)
        guardar(conn, [viejo], observed_at=AHORA - 31 * D, run_id=run)
        vistos = [v.envio.envio_ref for v in envios_actuales(conn, ahora=AHORA)]
        assert sorted(vistos) == ["1", "3"]


# ---------------------------------------------------------------------------
# Etiquetas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dominio", "vocabulario"),
    [("estado_envio", Estado), ("tipo_envio", Tipo), ("motivo_envio", Motivo)],
)
def test_etiquetas_cubren_el_vocabulario_de_envios(dominio, vocabulario):
    assert set(etiquetas.ETIQUETAS[dominio]) == set(get_args(vocabulario))


def test_etiquetas_de_envios_en_espanol():
    assert etiquetas.etiqueta("esperando_recoleccion", "estado_envio") == (
        "Esperando recolección en agencia"
    )
    assert etiquetas.etiqueta("cerrado_sin_confirmar", "estado_envio") == (
        "Cerrado por vencimiento, sin confirmar entrega"
    )
    assert etiquetas.FUENTE_INGESTA["envios_meli"] == "Envíos de Mercado Libre"


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------


def _texto_visible(html: str) -> str:
    sin_etiquetas = re.sub(r"<[^>]+>", " ", sin_registro(html))
    return " ".join(html_lib.unescape(sin_etiquetas).split())


def _bloque(html: str, ident: str) -> str:
    hallado = re.search(rf'<(\w+)[^>]*id="{ident}"[^>]*>(.*?)</\1>', html, re.S)
    assert hallado, f"falta #{ident}"
    return _texto_visible(hallado[2])


def _tarjeta(html: str, ref: str) -> str:
    hallado = re.search(rf'<article[^>]*data-envio="{ref}"[^>]*>(.*?)</article>', html, re.S)
    assert hallado, f"falta la tarjeta de {ref}"
    return _texto_visible(hallado[1])


@_skip_db
def test_pantalla_envios_html_y_etiquetas(monkeypatch):
    with _base(monkeypatch) as (conn, cliente):
        _siembra(conn)
        conn.execute("SET ROLE app_read")
        resp = cliente.get("/envios?vista=todos&plataforma=&tipo=&estado=&q=")
        assert resp.status_code == 200, resp.text
        html = resp.text
        assert 'data-pantalla="envios"' in html
        assert re.findall(r'data-envio="([^"]+)"', html) == ORDEN_TODOS

        texto = _texto_visible(html)
        for visible in (
            "En devolución",
            "Esperando recolección en agencia",
            "Mercado Envíos",
            "Envías tú (Amazon FBM)",
            "Envía Amazon (FBA)",
            "Despacho atrasado",
            "Requieren atención (3)",
            "En curso (6)",
            "Cerrados (1)",
            "Todos (7)",
            "Pedidos de los últimos 30 días",
        ):
            assert visible in texto, visible
        for codigo in (
            "xd_drop_off",
            "en_devolucion",
            "waiting_for_withdrawal",
            "ready_to_ship",
            "RETURNING_TO_SELLER",
            "UNDELIVERABLE",
            "amazon_fbm",
            "amazon_us",
            "problema_entrega",
            "despacho_vencido",
            "esperando_recoleccion",
        ):
            assert codigo not in texto, codigo

        atraso = _bloque(html, "envios-atraso")
        assert "Amazon US" in atraso and "Mercado Libre" in atraso
        assert "Amazon MX" not in atraso
        # Horas del centro de Mexico (UTC-6).
        lecturas = _bloque(html, "envios-lecturas")
        assert "Amazon MX: 24 nov, 22:00" in lecturas
        assert "Amazon US: 24 nov, 21:00 Atrasada" in lecturas
        assert "Mercado Libre: sin lectura correcta Atrasada" in lecturas

        dos = _tarjeta(html, US_DHL)
        assert "Paquete 1 de 2" in dos and "GUIA-US-1" in dos
        assert "Paquete 2 de 2" in dos and "GUIA-US-2" in dos
        assert "2 × <script>alert(1)</script>" in dos
        assert "<script>alert(1)</script>" not in html
        sin_datos = _tarjeta(html, ML_ATRASADO)
        assert "— × — · SKU —" in sin_datos
        assert "Despachar antes de: —" in sin_datos
        assert "Sin paquetes reportados" in sin_datos
        recoger = _tarjeta(html, ML_RECOGER)
        assert "Entrega límite: 26 nov, 06:00" in recoger
        assert "Venta en carrito 2000009999" in recoger
        assert "Entregado: 16 nov, 06:00" in _tarjeta(html, ML_ENTREGADO)

        assert 'href="/envios"' in html
        assert 'id="envios-contador"' in html
        assert 'class="tab tab-activo" href="/envios" aria-current="page"' in html
        assert 'data-rol="envios"' in html
        assert 'class="tab tab-activo" href="/reputacion"' not in html
        assert "Amazon MX · Amazon US · Mercado Libre" in _texto_visible(
            re.search(r'<span class="alcance">.*?</span>', html, re.S)[0]
        )
        assert not re.findall(r"\sstyle=|\son\w+=|<style", html)
        scripts = re.findall(r"<script[^>]*>", html)
        assert all("src=" in s or 'type="application/json"' in s for s in scripts)
        assert resp.headers["content-security-policy"] == "default-src 'self'"

        defecto = cliente.get("/envios").text
        assert re.findall(r'data-envio="([^"]+)"', defecto) == ORDEN_TODOS[:6]
        buscado = cliente.get("/envios?q=2000000002").text
        assert 'href="/envios?vista=todos&amp;q=2000000002"' in buscado


@_skip_db
def test_pantalla_sin_atraso_no_pinta_aviso(monkeypatch):
    with _base(monkeypatch) as (conn, cliente):
        for source, platform in (
            ("spapi_orders", "amazon_mx"),
            ("spapi_orders", "amazon_us"),
            ("envios_meli", "meli"),
        ):
            _corrida(conn, source, platform, 1 * H)
        html = cliente.get("/envios").text
        assert 'id="envios-atraso"' not in html
        assert "Mercado Libre: 25 nov, 05:00" in _bloque(html, "envios-lecturas")


# ---------------------------------------------------------------------------
# Cierre por vencimiento sin confirmacion
# ---------------------------------------------------------------------------


def _siembra_vencimiento(conn, ahora: datetime) -> None:
    """Dos vencidos a 90 dias (uno solo en la ultima observacion), uno a 30 y
    los que nunca cuentan: terminales, sin fecha y Amazon."""
    run = _corrida(conn, "envios_meli", "meli", 1 * H)
    hace = {dias: ahora - dias * D for dias in (31, 91, 200)}
    viejo = _meli("100", "1100", "shipped", pedido_creado_el=hace[200])
    cerrado_despues = _meli("200", "1200", "shipped", pedido_creado_el=hace[200])
    guardar(conn, [viejo, cerrado_despues], observed_at=ahora - 100 * D, run_id=run)
    envios = [
        _meli("200", "1200", "delivered", pedido_creado_el=hace[200]),
        _meli("300", "1300", "not_delivered", pedido_creado_el=hace[91]),
        _meli("400", "1400", "ready_to_ship", "delayed", pedido_creado_el=hace[31]),
        _meli("500", "1500", "cancelled", pedido_creado_el=hace[200]),
        _meli(
            "600",
            "1600",
            "not_delivered",
            pedido_creado_el=hace[200],
            devuelto_el=ahora - 150 * D,
        ),
        _meli("700", "1700", "shipped", pedido_creado_el=None),
        _envio("701-0000007", estado_fuente="UNSHIPPED", pedido_creado_el=hace[200]),
    ]
    guardar(conn, envios, observed_at=ahora - H, run_id=run)


def _fija_dias(conn, valor: str) -> None:
    conn.execute(
        "INSERT INTO config_version (label, settings)"
        " SELECT 'prueba envios', settings || jsonb_build_object('envios_meli_dias_cierre',"
        " %s::text) FROM config_version ORDER BY id DESC LIMIT 1",
        (valor,),
    )


@_skip_db
def test_contador_de_vencidos_sobre_toda_la_historia_en_api_y_pantalla(monkeypatch):
    with _base(monkeypatch) as (conn, cliente):
        _siembra_vencimiento(conn, AHORA)
        conn.execute("SET ROLE app_read")

        cuerpo = cliente.get("/api/envios?vista=todos").json()
        assert cuerpo["vencimiento"] == {"dias_cierre": 90, "cerrados": 2}
        # Vencidos hace mas de 30 dias: cuentan aunque la lista ya no los muestre.
        assert sorted(e["envio_ref"] for e in cuerpo["envios"]) == ["700"]
        assert _bloque(cliente.get("/envios").text, "envios-vencimiento") == (
            "Mercado Libre, en toda la historia: 2 en Cerrado por vencimiento, sin confirmar"
            " entrega (pasaron más de 90 días desde la compra sin un estado final)."
        )

        conn.execute("RESET ROLE")
        _fija_dias(conn, "30")
        conn.execute("SET ROLE app_read")
        assert cliente.get("/api/envios").json()["vencimiento"] == {
            "dias_cierre": 30,
            "cerrados": 3,
        }
        assert "más de 30 días" in _bloque(cliente.get("/envios").text, "envios-vencimiento")
        opciones = cliente.get("/envios").text
        assert (
            '<option value="cerrado_sin_confirmar">'
            "Cerrado por vencimiento, sin confirmar entrega</option>"
        ) in opciones


@_skip_db
@pytest.mark.parametrize("ruta", ["/api/envios", "/envios", "/api/envios/contador"])
def test_dias_de_cierre_invalidos_son_un_error_visible(monkeypatch, ruta):
    with _base(monkeypatch) as (conn, cliente):
        _fija_dias(conn, "noventa")
        conn.execute("SET ROLE app_read")
        resp = cliente.get(ruta)
        assert resp.status_code == 503
        detalle = resp.json()["detail"]
        assert "envios_meli_dias_cierre" in detalle and "'noventa'" in detalle
        assert "envios-ventana --dias" in detalle


def _consulta_documentada(marca: str = "cerrados_por_vencimiento") -> str:
    texto = (Path(__file__).resolve().parents[1] / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    bloques = [b for b in re.findall(r"```sql\n(.*?)```", texto, re.S) if marca in b]
    assert len(bloques) == 1
    return bloques[0]


@_skip_db
def test_la_consulta_documentada_cuenta_lo_mismo_que_el_contador(monkeypatch):
    # La consulta usa now(): aqui todo se siembra contra el reloj real.
    ahora = datetime.now(UTC)
    consulta = _consulta_documentada()
    with _base(monkeypatch) as (conn, cliente):
        monkeypatch.setattr(api_envios, "ahora_utc", lambda: ahora)
        _siembra_vencimiento(conn, ahora)
        for dias, esperados in (("90", 2), ("30", 3), ("365", 0)):
            _fija_dias(conn, dias)
            conn.execute("SET ROLE app_read")
            assert conn.execute(consulta).fetchone() == (int(dias), esperados)
            assert cliente.get("/api/envios").json()["vencimiento"] == {
                "dias_cierre": int(dias),
                "cerrados": esperados,
            }
            conn.execute("RESET ROLE")
        # Sin la clave en la config vigente, ambos usan el valor por omision.
        conn.execute(
            "INSERT INTO config_version (label, settings)"
            " SELECT 'sin clave', settings - 'envios_meli_dias_cierre'"
            " FROM config_version ORDER BY id DESC LIMIT 1"
        )
        assert conn.execute(consulta).fetchone() == (90, 2)
        assert cliente.get("/api/envios").json()["vencimiento"]["cerrados"] == 2


@_skip_db
def test_la_consulta_documentada_lista_las_referencias_que_cuenta_el_contador(monkeypatch):
    # La consulta usa now(): aqui todo se siembra contra el reloj real.
    ahora = datetime.now(UTC)
    consulta = _consulta_documentada("e.subestado_fuente")
    fila_100 = ("100", ["1100"], "shipped", None, ahora - 200 * D)
    fila_300 = ("300", ["1300"], "not_delivered", None, ahora - 91 * D)
    fila_400 = ("400", ["1400"], "ready_to_ship", "delayed", ahora - 31 * D)
    with _base(monkeypatch) as (conn, cliente):
        monkeypatch.setattr(api_envios, "ahora_utc", lambda: ahora)
        _siembra_vencimiento(conn, ahora)
        for dias, filas in (
            ("90", [fila_100, fila_300]),
            ("30", [fila_100, fila_300, fila_400]),
            ("365", []),
        ):
            _fija_dias(conn, dias)
            conn.execute("SET ROLE app_read")
            cursor = conn.execute(consulta)
            assert [columna.name for columna in cursor.description] == [
                "envio_ref",
                "pedidos",
                "estado_fuente",
                "subestado_fuente",
                "pedido_creado_el",
            ]
            assert cursor.fetchall() == filas
            contador = cliente.get("/api/envios").json()["vencimiento"]["cerrados"]
            assert contador == len(filas)
            conn.execute("RESET ROLE")
        # Sin la clave en la config vigente, el listado usa el valor por omision.
        conn.execute(
            "INSERT INTO config_version (label, settings)"
            " SELECT 'sin clave', settings - 'envios_meli_dias_cierre'"
            " FROM config_version ORDER BY id DESC LIMIT 1"
        )
        assert conn.execute(consulta).fetchall() == [fila_100, fila_300]
        assert cliente.get("/api/envios").json()["vencimiento"]["cerrados"] == 2


@_skip_db
def test_pantalla_una_fecha_ausente_se_ve_como_raya(monkeypatch):
    with _base(monkeypatch) as (conn, cliente):
        run = _corrida(conn, "envios_meli", "meli", 1 * H)
        sin_fechas = _meli("800", "1800", "ready_to_ship", "in_hub", paquetes=(_paquete("800"),))
        guardar(conn, [sin_fechas], observed_at=AHORA - H, run_id=run)
        tarjeta = _tarjeta(cliente.get("/envios").text, "800")
        assert "En camino" in tarjeta
        assert "Comprado: 20 nov, 06:00" in tarjeta
        assert "Despachar antes de: —" in tarjeta
        assert "Entrega prometida: —" in tarjeta
        assert "Despachado: —" in tarjeta
        # La fecha de compra no rellena ninguna otra.
        assert tarjeta.count("20 nov") == 1


# ---------------------------------------------------------------------------
# Contador del menu
# ---------------------------------------------------------------------------


def test_shell_pinta_el_contador_de_envios_solo_si_hay_atencion():
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
async function corre(respuesta) {
  const nodos = {};
  const nodo = () => ({ hidden: true, textContent: "", attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; }, replaceChildren() {}, append() {} });
  ["propuestas-contador", "envios-contador", "ciclo-resumen", "watermark-resumen"]
    .forEach(id => { nodos[id] = nodo(); });
  const movil = nodo();
  let listo;
  global.document = { addEventListener(_, f) { listo = f; },
    getElementById(id) { return nodos[id] || null; },
    querySelectorAll(sel) { return sel === '[data-rol="envios"]' ? [movil] : []; } };
  global.Orbit = { async getJson(url) {
    if (url === "/api/envios/contador") return respuesta;
    return { ok: true, jsonOk: true, data: {} };
  } };
  vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
  listo();
  await new Promise(r => setImmediate(r));
  return { sidebar: nodos["envios-contador"], movil };
}
(async () => {
  const tres = await corre({ ok: true, jsonOk: true, data: { atencion: 3 } });
  for (const n of [tres.sidebar, tres.movil]) {
    assert.equal(n.hidden, false);
    assert.equal(n.textContent, "3");
    assert.equal(n.attrs["aria-label"], "Envíos que requieren atención: 3");
  }
  const cero = await corre({ ok: true, jsonOk: true, data: { atencion: 0 } });
  assert.equal(cero.sidebar.hidden, true);
  assert.equal(cero.movil.hidden, true);
  const error = await corre({ ok: false, jsonOk: false });
  assert.equal(error.sidebar.hidden, true);
  assert.equal(error.movil.hidden, true);
})().catch(e => { console.error(e); process.exit(1); });
"""
    r = subprocess.run(
        [_node(), "-e", guion, str(JS_DIR / "shell.js")],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
