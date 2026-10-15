"""REPRICING 02 M.4: MercadoMeli + MeliWriteClient (forma sin sellar).

DoD de la fila M.4: sin `modo_confirmado="live"` el cliente no se
construye; con la forma sin sellar, `escribir` levanta
`EscrituraNoDisponible` sin tocar la red; `ClienteMeli` solo GET (candado
en `test_arq_precio_m.py`); candado del `.put(`. Mas la de la guia: con
la forma sin sellar, solo `sonda=True` llega a la red (una llamada).
El adaptador espeja `MercadoAmazon` con fuentes MeLi (catalogo G27,
cuenta de `leer_cuenta`, historia con visitas, cotizacion por
`CotizadorMeli`, vivo por GET).
"""

from __future__ import annotations

import itertools
import os
import socket
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

from app.precio.puerto import (
    EscrituraNoDisponible,
    Importe,
    Miembro,
    PrecioVivoAusente,
    UnidadPrecio,
)
from app.reputacion_clientes import ClienteMeli, MeliCredentials

RAIZ = Path(__file__).resolve().parents[1]

ORDEN_M4 = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
    "0055_precio02_escenario_exige.sql",
    "0073_precio02_meli_miembro_costo.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
HOY = date(2026, 9, 8)

_CONTADOR = itertools.count()


def _creds(tmp_path) -> MeliCredentials:
    return MeliCredentials(
        access_token="tok-m4",
        refresh_token="ref-m4",
        client_id="cid",
        client_secret="sec",
        ruta=tmp_path / "tok.json",
    )


# ---------------------------------------------------------------------------
# Cliente de escritura (sin DB)
# ---------------------------------------------------------------------------


def test_escritor_exige_modo_live(tmp_path):
    from app.meli.write_client import MeliWriteClient, MutationNotAllowedError

    for modo in ("shadow", "off", "", "LIVE", "live "):
        with pytest.raises(MutationNotAllowedError, match="modo_confirmado"):
            MeliWriteClient(modo_confirmado=modo, creds=_creds(tmp_path))
    cli = MeliWriteClient(modo_confirmado="live", creds=_creds(tmp_path))
    cli.close()


def test_poner_precio_sin_sellar_no_toca_red(tmp_path):
    from app.meli.write_client import FORMA_ESCRITURA_MELI, MeliWriteClient

    assert FORMA_ESCRITURA_MELI == "pendiente_sonda"
    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json={})

    cli = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(EscrituraNoDisponible):
        cli.poner_precio("MLM1", ["v-a"], Decimal("979.11"), "MXN")
    assert llamadas == []
    cli.close()


def test_poner_precio_sonda_llega_a_red_una_vez(tmp_path):
    from app.meli.write_client import MeliWriteClient

    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json={"id": "MLM1", "price": 979.12})

    cli = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    acuse = cli.poner_precio("MLM1", ["v-a", "v-b"], Decimal("979.12"), "MXN", sonda=True)
    assert len(llamadas) == 1
    pet = llamadas[0]
    assert pet.method == "PUT"
    assert pet.url.path == "/items/MLM1"
    assert pet.headers["Authorization"] == "Bearer tok-m4"
    cuerpo = httpx.Response(200, content=pet.content).json()
    assert cuerpo["price"] == 979.12
    assert cuerpo["variations"] == [
        {"id": "v-a", "price": 979.12},
        {"id": "v-b", "price": 979.12},
    ]
    assert b'"price": 979.12' in pet.content
    assert acuse.aceptada is True
    assert acuse.codigo is None
    assert "tok-m4" not in str(acuse.cuerpo)
    cli.close()


def test_poner_precio_sin_variaciones_solo_raiz(tmp_path):
    from app.meli.write_client import MeliWriteClient

    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json={"id": "MLM1", "price": 100})

    cli = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    acuse = cli.poner_precio("MLM1", [], Decimal("100"), "MXN", sonda=True)
    assert acuse.aceptada is True
    cuerpo = httpx.Response(200, content=llamadas[0].content).json()
    assert cuerpo == {"price": 100}
    cli.close()


def test_poner_precio_valida_sin_red(tmp_path):
    from app.meli.write_client import MeliWriteClient

    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json={})

    cli = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="item_id"):
        cli.poner_precio("", ["v-a"], Decimal("10"), "MXN", sonda=True)
    with pytest.raises(ValueError, match="item_id"):
        cli.poner_precio("MLM1/x", ["v-a"], Decimal("10"), "MXN", sonda=True)
    with pytest.raises(ValueError, match="variante"):
        cli.poner_precio("MLM1", [""], Decimal("10"), "MXN", sonda=True)
    with pytest.raises(ValueError, match="variante"):
        cli.poner_precio("MLM1", ["v-a", "v-a"], Decimal("10"), "MXN", sonda=True)
    with pytest.raises(ValueError, match="precio"):
        cli.poner_precio("MLM1", [], Decimal("0"), "MXN", sonda=True)
    with pytest.raises(ValueError, match="precio"):
        cli.poner_precio("MLM1", [], Decimal("10.001"), "MXN", sonda=True)
    with pytest.raises(ValueError, match="moneda"):
        cli.poner_precio("MLM1", [], Decimal("10"), "USD", sonda=True)
    assert llamadas == []
    cli.close()


def test_put_401_refresca_y_reintenta_una_vez(tmp_path):
    from app.meli.write_client import MeliWriteClient

    llamadas = []
    refrescos = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        if len(llamadas) == 1:
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"id": "MLM1", "price": 10})

    def refresco(creds, http):
        refrescos.append(creds)
        creds.access_token = "tok-nuevo"
        creds.refresh_token = "ref-nuevo"

    cli = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        refresco=refresco,
        transport=httpx.MockTransport(handler),
    )
    acuse = cli.poner_precio("MLM1", [], Decimal("10"), "MXN", sonda=True)
    assert acuse.aceptada is True
    assert len(llamadas) == 2
    assert len(refrescos) == 1
    assert llamadas[1].headers["Authorization"] == "Bearer tok-nuevo"
    cli.close()


def test_put_401_doble_es_acuse_error_sin_loop(tmp_path):
    from app.meli.write_client import MeliWriteClient

    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(401, json={"message": "expired"})

    def refresco(creds, http):
        creds.access_token = "tok-nuevo"

    cli = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        refresco=refresco,
        transport=httpx.MockTransport(handler),
    )
    acuse = cli.poner_precio("MLM1", [], Decimal("10"), "MXN", sonda=True)
    assert acuse.aceptada is False
    assert "401" in (acuse.codigo or "")
    assert len(llamadas) == 2
    cli.close()


def test_put_429_reintenta_una_vez_y_5xx_no(tmp_path):
    from app.meli.write_client import MeliWriteClient

    llamadas = []
    pausas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        if len(llamadas) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        return httpx.Response(200, json={"id": "MLM1", "price": 10})

    cli = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        transport=httpx.MockTransport(handler),
        sleep=pausas.append,
    )
    acuse = cli.poner_precio("MLM1", [], Decimal("10"), "MXN", sonda=True)
    assert acuse.aceptada is True
    assert pausas == [2.0]
    assert len(llamadas) == 2
    cli.close()

    llamadas500 = []

    def handler500(request: httpx.Request) -> httpx.Response:
        llamadas500.append(request)
        return httpx.Response(500, json={})

    cli500 = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        transport=httpx.MockTransport(handler500),
    )
    acuse500 = cli500.poner_precio("MLM1", [], Decimal("10"), "MXN", sonda=True)
    assert acuse500.aceptada is False
    assert len(llamadas500) == 1
    cli500.close()


def test_cliente_meli_rechaza_put_sin_red(tmp_path):
    import httpx

    from app.reputacion_clientes import ReputacionError

    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json={})

    cli = ClienteMeli(_creds(tmp_path), transport=httpx.MockTransport(handler), backoff_base=0.0)
    with pytest.raises(ReputacionError, match="solo-GET"):
        cli._request("PUT", "/items/MLM1")
    assert llamadas == []
    cli.close()


def test_refresco_publico_renueva_y_guarda(tmp_path):
    import json

    import httpx

    from app.reputacion_clientes import MeliCredentials, refrescar_tokens_meli

    ruta = tmp_path / "meli_tokens.json"
    ruta.write_text(
        json.dumps(
            {
                "access_token": "viejo",
                "refresh_token": "ref-viejo",
                "client_id": "cid",
                "client_secret": "sec",
            }
        ),
        encoding="utf-8",
    )
    creds = MeliCredentials.from_secrets_dir(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/oauth/token"
        return httpx.Response(200, json={"access_token": "nuevo", "refresh_token": "ref-nuevo"})

    http = httpx.Client(
        base_url="https://api.mercadolibre.com", transport=httpx.MockTransport(handler)
    )
    refrescar_tokens_meli(creds, http)
    assert creds.access_token == "nuevo"
    assert json.loads(ruta.read_text(encoding="utf-8"))["access_token"] == "nuevo"
    assert not (tmp_path / "meli_tokens.tmp").exists()
    http.close()


# ---------------------------------------------------------------------------
# Ayudantes DB (cadena M.3 reutilizada)
# ---------------------------------------------------------------------------


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db():
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"precio02m4_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_M4:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _m3():
    import test_precio02_escenario_meli as m3

    return m3


def _unidad(lid, ancla, miembros, externo="MLM1"):
    return UnidadPrecio(
        listing_id=lid,
        plataforma="meli",
        externo=externo,
        canal=None,
        producto_ancla=ancla,
        miembros=tuple(miembros),
    )


def _metas(lid, *, goal="0.30", mode="shadow"):
    from app.precio.puerto import MetaGoal

    return {
        lid: MetaGoal(goal=Decimal(goal), mode=mode, goal_vigente_desde=HOY - timedelta(days=30))
    }


def _configuracion(conn):
    from test_precio_corrida import SETTINGS, _config

    from app.precio.config import leer_config

    _config(conn)
    return leer_config(SETTINGS)


# ---------------------------------------------------------------------------
# Vitrina: catalogo / observado / historia
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_vitrina_sin_clientes_solo_lee():
    from app.meli.precio_mercado import MercadoMeli

    mercado = MercadoMeli("meli")
    assert mercado.clave == "meli"
    assert mercado.moneda == "MXN"
    assert mercado.grupo_tasa == "meli"
    with pytest.raises(ValueError, match="clave"):
        MercadoMeli("amazon_mx")
    unidad = _unidad(1, 2, [Miembro(2, "SKU-X", "v-x")])
    with pytest.raises(RuntimeError, match="sin clientes"):
        mercado.precio_vivo(unidad)
    with pytest.raises(RuntimeError, match="sin clientes"):
        mercado.cotizar(None, None, Importe(Decimal("1"), "MXN"))
    with pytest.raises(RuntimeError, match="sin clientes"):
        mercado.escribir(unidad, Importe(Decimal("1"), "MXN"))
    mercado.cerrar()


@_skip_sin_pg
def test_catalogo_activas_g27():
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        pb = m3._producto(conn, "SKU-M4-B")
        m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        m3._publicacion(conn, "MLM2", [("SKU-M4-B", pb)], estado="closed")
        activas = MercadoMeli("meli").catalogo(conn, hoy=HOY)
        assert len(activas) == 1
        (activa,) = activas
        assert activa.seller_sku == "SKU-M4-A"
        assert activa.precio is None
        assert activa.dias_sin_reportar == 0
        assert activa.unidad is not None
        assert activa.unidad.externo == "MLM1"
        assert activa.unidad.producto_ancla == pa
        assert activa.unidad.miembros == (Miembro(pa, "SKU-M4-A", "v-SKU-M4-A"),)


@_skip_sin_pg
def test_catalogo_ultima_obs_manda_y_sin_mapear_va_con_none():
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa), ("SKU-M4-Z", None)])
        m3._observacion(conn, "MLM1", observado=NOW - timedelta(days=3))
        activas = MercadoMeli("meli").catalogo(conn, hoy=HOY)
        assert len(activas) == 1
        unidad = activas[0].unidad
        assert unidad is not None
        assert unidad.listing_id == lid
        assert set(unidad.miembros) == {
            Miembro(pa, "SKU-M4-A", "v-SKU-M4-A"),
            Miembro(None, "SKU-M4-Z", "v-SKU-M4-Z"),
        }
        m3._observacion(conn, "MLM1", observado=NOW, estado="closed")
        assert MercadoMeli("meli").catalogo(conn, hoy=HOY) == ()


@_skip_sin_pg
def test_observado_ventana():
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        unidad = _unidad(lid, pa, [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A")])
        mercado = MercadoMeli("meli")
        obs = mercado.observado(conn, unidad, despues_de=HOY - timedelta(days=1), hasta=HOY)
        assert obs is not None
        assert obs.precio == Importe(Decimal("979.1100"), "MXN")
        assert obs.observada_en is not None
        assert (
            mercado.observado(
                conn, unidad, despues_de=HOY - timedelta(days=9), hasta=HOY - timedelta(days=8)
            )
            is None
        )


@_skip_sin_pg
def test_historia_meli_disponibilidad_y_visitas():
    from app.meli.precio_mercado import historia_meli_unidad

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        m3._observacion(conn, "MLM1", observado=NOW - timedelta(days=2), visitas=5)
        m3._observacion(conn, "MLM1", observado=NOW - timedelta(days=3), stock=0)
        m3._observacion(conn, "MLM1", observado=NOW - timedelta(days=4), estado="closed", stock=0)
        unidad = _unidad(lid, pa, [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A")])
        historia = historia_meli_unidad(conn, unidad=unidad, hoy=HOY)
        assert historia.primera_venta is None
        assert historia.sin_atribuir == 0
        assert historia.ledger_cubierto_hasta is None
        dias = {d.dia: d for d in historia.dias}
        assert set(dias) == {
            HOY - timedelta(days=4),
            HOY - timedelta(days=3),
            HOY - timedelta(days=2),
            HOY,
        }
        assert dias[HOY].disponibilidad == "disponible"
        assert dias[HOY].exposiciones is None
        assert dias[HOY].clics is None
        assert dias[HOY].buy_box_propia is None
        assert dias[HOY].unidades == 0
        assert dias[HOY - timedelta(days=2)].exposiciones == 5
        assert dias[HOY - timedelta(days=3)].disponibilidad == "no_disponible"
        assert dias[HOY - timedelta(days=4)].disponibilidad == "no_disponible"


# ---------------------------------------------------------------------------
# Insumos
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_insumos_disponible_trae_cuenta_del_que_manda():
    from app.estimacion_reader import leer_cuenta
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        pb = m3._producto(conn, "SKU-M4-B")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa), ("SKU-M4-B", pb)])
        m3._costo(conn, pa)
        m3._muestra(conn, pa)
        m3._costo(conn, pb, monto="500.0000")
        m3._muestra(conn, pb, valor="20.0000")
        m3._politica(conn)
        m3._siembra(conn, lid)
        config = _configuracion(conn)
        unidad = _unidad(
            lid,
            pa,
            [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A"), Miembro(pb, "SKU-M4-B", "v-SKU-M4-B")],
        )
        lecturas = MercadoMeli("meli").insumos(
            conn, (unidad,), hoy=HOY, ahora=NOW, config=config, metas=_metas(lid)
        )
        lectura = lecturas[lid]
        assert lectura.motivo is None
        assert lectura.entrada is not None
        assert lectura.entrada.canal == "meli"
        assert lectura.entrada.mode == "shadow"
        assert lectura.entrada.pricing is not None
        assert lectura.entrada.pricing.precio == Importe(Decimal("979.1100"), "MXN")
        assert lectura.historia is not None
        cuenta = leer_cuenta(conn, lid, as_of=NOW)
        assert cuenta is not None and not isinstance(cuenta, str)
        assert lectura.entrada.escenario.componentes == cuenta.componentes
        assert lectura.ids is not None
        assert lectura.ids["escenario_id"] == cuenta.refs.escenario_id
        assert lectura.ids["envio_muestra_id"] == cuenta.refs.envio_muestra_id
        assert lectura.cotizable is not None
        assert lectura.fees_escenario != "fee_incompatible"


@_skip_sin_pg
def test_insumos_sin_miembros_da_costo_ausente():
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        config = _configuracion(conn)
        lecturas = MercadoMeli("meli").insumos(
            conn,
            (_unidad(lid, pa, []),),
            hoy=HOY,
            ahora=NOW,
            config=config,
            metas=_metas(lid),
        )
        lectura = lecturas[lid]
        assert lectura.entrada is None
        assert lectura.motivo == "costo_ausente"


@_skip_sin_pg
def test_insumos_variante_sin_mapear():
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa), ("SKU-M4-Z", None)])
        m3._costo(conn, pa)
        m3._muestra(conn, pa)
        m3._politica(conn)
        m3._siembra(conn, lid)
        config = _configuracion(conn)
        unidad = _unidad(
            lid,
            pa,
            [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A"), Miembro(None, "SKU-M4-Z", "v-SKU-M4-Z")],
        )
        lecturas = MercadoMeli("meli").insumos(
            conn, (unidad,), hoy=HOY, ahora=NOW, config=config, metas=_metas(lid)
        )
        lectura = lecturas[lid]
        assert lectura.entrada is None
        assert lectura.motivo == "variante_sin_mapear"
        assert "SKU-M4-Z" in lectura.diagnostico


@_skip_sin_pg
def test_insumos_fallo_interno_no_tumba_lote(monkeypatch):
    import app.meli.precio_mercado as pm
    from app.meli.precio_mercado import MercadoMeli

    def _boom(*args, **kwargs):
        raise RuntimeError("partido roto")

    monkeypatch.setattr(pm, "partir_fees_meli", _boom)
    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        m3._costo(conn, pa)
        m3._muestra(conn, pa)
        m3._politica(conn)
        m3._siembra(conn, lid)
        config = _configuracion(conn)
        unidad = _unidad(lid, pa, [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A")])
        lecturas = MercadoMeli("meli").insumos(
            conn, (unidad,), hoy=HOY, ahora=NOW, config=config, metas=_metas(lid)
        )
        lectura = lecturas[lid]
        assert lectura.entrada is None
        assert lectura.motivo == "escenario_incoherente"
        assert "partido roto" in lectura.diagnostico


@_skip_sin_pg
def test_insumos_error_db_relevanta(monkeypatch):
    import app.meli.precio_mercado as pm
    from app.meli.precio_mercado import MercadoMeli

    def _boom_db(*args, **kwargs):
        raise psycopg.Error("conexion muerta")

    monkeypatch.setattr(pm, "partir_fees_meli", _boom_db)
    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        m3._costo(conn, pa)
        m3._muestra(conn, pa)
        m3._politica(conn)
        m3._siembra(conn, lid)
        config = _configuracion(conn)
        unidad = _unidad(lid, pa, [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A")])
        with pytest.raises(psycopg.Error, match="conexion muerta"):
            MercadoMeli("meli").insumos(
                conn, (unidad,), hoy=HOY, ahora=NOW, config=config, metas=_metas(lid)
            )


@_skip_sin_pg
def test_insumos_sin_escenario_da_precio_ausente():
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        config = _configuracion(conn)
        unidad = _unidad(lid, pa, [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A")])
        lecturas = MercadoMeli("meli").insumos(
            conn, (unidad,), hoy=HOY, ahora=NOW, config=config, metas=_metas(lid)
        )
        lectura = lecturas[lid]
        assert lectura.entrada is None
        assert lectura.motivo == "precio_ausente"


# ---------------------------------------------------------------------------
# Cotizar / vivo / escribir
# ---------------------------------------------------------------------------


def _mercado_con_red(handler_ofertas, handler_precios, tmp_path):
    import httpx

    from app.meli.estimacion import CotizadorMeli
    from app.meli.precio_mercado import MercadoMeli
    from app.meli.write_client import MeliWriteClient

    lector = ClienteMeli(
        _creds(tmp_path), transport=httpx.MockTransport(handler_ofertas), backoff_base=0.0
    )
    fees = CotizadorMeli(
        ClienteMeli(
            _creds(tmp_path),
            transport=httpx.MockTransport(handler_precios),
            backoff_base=0.0,
        )
    )
    escritor = MeliWriteClient(
        modo_confirmado="live",
        creds=_creds(tmp_path),
        transport=httpx.MockTransport(handler_precios),
    )
    return MercadoMeli("meli", lector=lector, fees=fees, escritor=escritor)


@_skip_sin_pg
def test_cotizar_parte_fees_meli(tmp_path):
    from app.estimacion_insumos import persistir_oferta_observation
    from app.meli.estimacion import resolver_oferta_meli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        oid = persistir_oferta_observation(conn, res.oferta, observed_at=NOW).id

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "currency_id": "MXN",
                    "sale_fee_amount": 190.93,
                    "sale_fee_details": {"percentage_fee": 19.5, "fixed_fee": 0},
                    "listing_fee_amount": 0,
                },
            )

        mercado = _mercado_con_red(handler, handler, tmp_path)
        from app.estimacion_insumos import oferta_para_cotizar

        oferta = oferta_para_cotizar(conn, oid)
        cot = mercado.cotizar(conn, oferta, Importe(Decimal("979.11"), "MXN"))
        assert cot.error_code is None
        assert cot.fees is not None
        assert cot.fees.total == Importe(Decimal("190.93"), "MXN")
        assert cot.fees.variable == Importe(Decimal("190.93"), "MXN")
        assert cot.fees.fijo == Importe(Decimal("0"), "MXN")
        mercado.cerrar()


@_skip_sin_pg
def test_cotizar_error_y_moneda_distinta(tmp_path):
    from app.estimacion_insumos import oferta_para_cotizar, persistir_oferta_observation
    from app.meli.estimacion import resolver_oferta_meli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        oid = persistir_oferta_observation(conn, res.oferta, observed_at=NOW).id

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={})

        mercado = _mercado_con_red(handler, handler, tmp_path)
        oferta = oferta_para_cotizar(conn, oid)
        cot = mercado.cotizar(conn, oferta, Importe(Decimal("979.11"), "MXN"))
        assert cot.fees is None
        assert cot.error_code == "fee_error"
        with pytest.raises(ValueError, match="moneda"):
            mercado.cotizar(conn, oferta, Importe(Decimal("979.11"), "USD"))
        mercado.cerrar()

        def handler_raro(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "currency_id": "MXN",
                    "sale_fee_amount": 190.93,
                    "sale_fee_details": {"percentage_fee": 19.5, "fixed_fee": 190.93},
                    "listing_fee_amount": 0,
                },
            )

        mercado2 = _mercado_con_red(handler_raro, handler_raro, tmp_path)
        cot2 = mercado2.cotizar(conn, oferta, Importe(Decimal("979.11"), "MXN"))
        assert cot2.fees is None
        assert cot2.error_code == "fee_incompatible"
        mercado2.cerrar()


@_skip_sin_pg
def test_precio_vivo_ok_y_ausente(tmp_path):
    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        unidad = _unidad(lid, pa, [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A")])

        def handler_ok(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/items/MLM1"
            return httpx.Response(200, json={"id": "MLM1", "price": 979.11, "currency_id": "MXN"})

        mercado = _mercado_con_red(handler_ok, handler_ok, tmp_path)
        assert mercado.precio_vivo(unidad) == Importe(Decimal("979.11"), "MXN")
        mercado.cerrar()

        def handler_404(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "not found"})

        mercado404 = _mercado_con_red(handler_404, handler_404, tmp_path)
        with pytest.raises(PrecioVivoAusente):
            mercado404.precio_vivo(unidad)
        mercado404.cerrar()


@_skip_sin_pg
def test_escribir_sin_sellar_levanta_sin_red(tmp_path):
    m3 = _m3()
    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json={})

    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        unidad = _unidad(lid, pa, [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A")])
        mercado = _mercado_con_red(handler, handler, tmp_path)
        with pytest.raises(EscrituraNoDisponible):
            mercado.escribir(unidad, Importe(Decimal("979.12"), "MXN"))
        assert llamadas == []
        mercado.cerrar()


@_skip_sin_pg
def test_escribir_sonda_pasa_y_valida_destino(tmp_path):
    from app.precio.cambios import PublicacionSinSku

    m3 = _m3()
    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json={"id": "MLM1", "price": 979.12})

    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        unidad = _unidad(lid, pa, [Miembro(pa, "SKU-M4-A", "v-SKU-M4-A")])
        mercado = _mercado_con_red(handler, handler, tmp_path)
        mercado.validar_destino(unidad)
        acuse = mercado.escribir(unidad, Importe(Decimal("979.12"), "MXN"), sonda=True)
        assert acuse.aceptada is True
        assert len(llamadas) == 1
        cuerpo = httpx.Response(200, content=llamadas[0].content).json()
        assert cuerpo["variations"] == [{"id": "v-SKU-M4-A", "price": 979.12}]
        with pytest.raises(PublicacionSinSku):
            mercado.validar_destino(_unidad(lid, pa, [], externo=""))
        mercado.cerrar()


@_skip_sin_pg
def test_cotizacion_guardada_reusa_mismatch_y_no_concilia():
    from types import SimpleNamespace

    from psycopg.types.json import Json

    from app.estimacion_insumos import persistir_oferta_observation
    from app.meli.estimacion import resolver_oferta_meli
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        oid = persistir_oferta_observation(conn, res.oferta, observed_at=NOW).id
        detalles = [
            {"fee_type": "SaleFee", "final_fee": "190.93"},
            {"fee_type": "SaleFeeFixed", "final_fee": "0"},
        ]
        conn.execute(
            "INSERT INTO precio_cotizacion (listing_id, platform, intento, cotizacion_date,"
            " oferta_observation_id, quoted_price, quoted_price_currency, total_fees,"
            " total_fees_currency, fee_details, fees_estimated_at, estado, source_event_id)"
            " VALUES (%s, 'meli', 1, %s, %s, 979.11, 'MXN', 190.93, 'MXN', %s, %s,"
            " 'success', 'ev-m4-ok')",
            (lid, HOY, oid, Json(detalles), NOW),
        )
        conn.execute(
            "INSERT INTO precio_cotizacion (listing_id, platform, intento, cotizacion_date,"
            " oferta_observation_id, quoted_price, quoted_price_currency, total_fees,"
            " total_fees_currency, fee_details, fees_estimated_at, estado, source_event_id)"
            " VALUES (%s, 'meli', 2, %s, %s, 979.11, 'MXN', 190.93, 'MXN', %s, %s,"
            " 'success', 'ev-m4-mala')",
            (
                lid,
                HOY,
                oid,
                Json([{"fee_type": "SaleFee", "final_fee": "100.00"}]),
                NOW,
            ),
        )
        mercado = MercadoMeli("meli")
        pedido = SimpleNamespace(precio=Importe(Decimal("979.11"), "MXN"))
        assert (
            mercado.cotizacion_guardada(
                conn,
                evento="ev-m4-nada",
                listing_id=lid,
                hoy=HOY,
                oferta_id=oid,
                pedido=pedido,
            )
            is None
        )
        cid, cot = mercado.cotizacion_guardada(
            conn, evento="ev-m4-ok", listing_id=lid, hoy=HOY, oferta_id=oid, pedido=pedido
        )
        assert cot.error_code is None
        assert cot.fees is not None
        assert cot.fees.variable == Importe(Decimal("190.93"), "MXN")
        with pytest.raises(ValueError, match="no coincide"):
            mercado.cotizacion_guardada(
                conn,
                evento="ev-m4-ok",
                listing_id=lid,
                hoy=HOY,
                oferta_id=oid,
                pedido=SimpleNamespace(precio=Importe(Decimal("900"), "MXN")),
            )
        _cid2, cot2 = mercado.cotizacion_guardada(
            conn, evento="ev-m4-mala", listing_id=lid, hoy=HOY, oferta_id=oid, pedido=pedido
        )
        assert cot2.fees is None
        assert cot2.error_code == "fee_error:fee_no_concilia"


@_skip_sin_pg
def test_cotizacion_guardada_corrupta_da_value_error():
    from types import SimpleNamespace

    from psycopg.types.json import Json

    from app.estimacion_insumos import persistir_oferta_observation
    from app.meli.estimacion import resolver_oferta_meli
    from app.meli.precio_mercado import MercadoMeli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        oid = persistir_oferta_observation(conn, res.oferta, observed_at=NOW).id
        conn.execute(
            "INSERT INTO precio_cotizacion (listing_id, platform, intento, cotizacion_date,"
            " oferta_observation_id, quoted_price, quoted_price_currency, total_fees,"
            " total_fees_currency, fee_details, fees_estimated_at, estado, source_event_id)"
            " VALUES (%s, 'meli', 1, %s, %s, 979.11, 'MXN', 190.93, 'MXN', %s, %s,"
            " 'success', 'ev-m4-corrupta')",
            (lid, HOY, oid, Json([{"fee_type": "SaleFee"}]), NOW),
        )
        mercado = MercadoMeli("meli")
        pedido = SimpleNamespace(precio=Importe(Decimal("979.11"), "MXN"))
        with pytest.raises(ValueError, match="detalle de fee"):
            mercado.cotizacion_guardada(
                conn,
                evento="ev-m4-corrupta",
                listing_id=lid,
                hoy=HOY,
                oferta_id=oid,
                pedido=pedido,
            )


@_skip_sin_pg
def test_cerrar_no_levanta(tmp_path):
    from app.meli.precio_mercado import MercadoMeli

    class _Malo:
        def close(self):
            raise RuntimeError("boom")

    mercado = MercadoMeli("meli", lector=_Malo(), escritor=_Malo())
    mercado.cerrar()


def test_direccion_sin_movimiento_es_none():
    from app.precio.fuentes import _direccion

    assert _direccion(Decimal("10"), Decimal("10")) is None
    assert _direccion(Decimal("10"), Decimal("11")) == "subir"
    assert _direccion(Decimal("11"), Decimal("10")) == "bajar"


@_skip_sin_pg
def test_oferta_para_cotizar_devuelve_campos():
    from app.estimacion_insumos import oferta_para_cotizar, persistir_oferta_observation
    from app.meli.estimacion import resolver_oferta_meli

    m3 = _m3()
    with _db() as conn:
        pa = m3._producto(conn, "SKU-M4-A")
        lid = m3._publicacion(conn, "MLM1", [("SKU-M4-A", pa)])
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        oid = persistir_oferta_observation(conn, res.oferta, observed_at=NOW).id
        leida = oferta_para_cotizar(conn, oid)
        assert leida.listing_id == lid
        assert leida.platform == "meli"
        assert leida.seller_sku == "SKU-M4-A"
        assert leida.asin == "MLM1"
        assert leida.canal == "meli"
        assert leida.price_amount == Decimal("979.1100")
        assert leida.price_currency == "MXN"
        assert leida.source_event_id == res.oferta.source_event_id
        assert leida.context_fingerprint == res.oferta.context_fingerprint


@_skip_sin_pg
def test_abrir_meli_por_raiz():
    import app.precio_mercados as mercados

    with mercados.abrir("meli") as vitrina:
        assert vitrina.clave == "meli"
    assert mercados.vitrina("meli").clave == "meli"
