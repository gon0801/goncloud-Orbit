"""Tests de escritura y reversa de precios (REPRICING 01, A.3).

`tests/test_precio_write.py` corre contra PostgreSQL real con las
migraciones hasta 0039 (+ 0032 de observaciones) y opera bajo
`SET ROLE app_decide`: un `skipped` aqui significa que el carril no
termino (cero skips). Todo el HTTP va por `httpx.MockTransport`: cero
red, cero Amazon. Rojo primero por caso (`.saikit/scratch/C/tdd.md`).
"""

from __future__ import annotations

import json
import logging
import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.redaction import register_secret
from app.spapi.client import MERCADOS, VENDEDORES_PROPIOS, SpapiClient
from app.spapi.precio_write import (
    CambioNoReversible,
    DecisionSinAccion,
    FormaParcheSinSellar,
    PrecioVivoAusente,
    PublicacionSinSku,
    cambiar_precio,
    cerrar_por_observacion,
    construir_escritor,
    leer_precio_vivo,
    revertir,
)

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
ORDEN39C = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0032_spapi_pricing.sql",
    "0039_precio.sql",
)

AHORA = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)
ANTES = datetime(2026, 9, 17, 13, 0, 0, tzinfo=UTC)
CRED = {"lwa_app_id": "id-c", "lwa_client_secret": "sec-c", "refresh_token": "r-c"}
PROPIO = VENDEDORES_PROPIOS[MERCADOS["amazon_mx"]]
ASIN = "B0TESTC001"
SKU = "SKU-C1"
SECRETO = "SECRETO-CARRIL-C-XYZ"


def _dsn():
    return _test_dsn()


def _dsn_db(conn):
    """DSN del tool hacia la DB temporal de este test."""
    from urllib.parse import urlsplit, urlunsplit

    partes = urlsplit(_dsn())
    return urlunsplit((partes.scheme, partes.netloc, "/" + conn.info.dbname, "", ""))


@contextmanager
def db_39c():
    """DB temporal con ORDEN39C; yields conn autocommit (patron `db_39`)."""
    from psycopg import sql as pgsql

    dsn = _dsn()
    db = f"precio_write_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN39C:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@contextmanager
def rol(conn, rolname="app_decide"):
    conn.execute(f"SET ROLE {rolname}")
    try:
        yield conn
    finally:
        conn.execute("RESET ROLE")


def _producto(conn, sku="SKU-C-PROD") -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Prod C') RETURNING id", (sku,)
    ).fetchone()[0]


def _listing(conn, producto, *, platform="amazon_mx", asin=ASIN, sku=SKU) -> int:
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (producto, platform, asin, sku),
    ).fetchone()[0]


def _goal_live(conn, listing, *, platform="amazon_mx", mode="live") -> int:
    go = "GO-TDD" if mode == "live" else None
    return conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
        " valid_from, creado_por, go_literal)"
        " VALUES (%s, %s, 0.30, %s, '2026-09-01', 'tdd', %s) RETURNING id",
        (listing, platform, mode, go),
    ).fetchone()[0]


def _decision(
    conn,
    listing,
    *,
    platform="amazon_mx",
    resultado="subir",
    aplicado=Decimal("110.00"),
    mode="live",
) -> int:
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency)"
        " VALUES (%s, %s, %s, 'tdd', %s, 0.30, 0.25,"
        " 100.00, 'MXN', 110.00, 'MXN', %s, 'MXN',"
        " 10.00, 'MXN', 20.00, 'MXN', 12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN')"
        " RETURNING id",
        (listing, platform, resultado, mode, aplicado),
    ).fetchone()[0]


def _cambio_enviado(
    conn, decision, listing, *, antes="100.00", despues="110.00", aplicado=True
) -> int:
    cid = conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at)"
        " VALUES (%s, %s, 'amazon_mx', %s, 'MXN', %s, 'MXN', %s, 'pendiente', %s)"
        " RETURNING id",
        (decision, listing, antes, despues, aplicado, ANTES),
    ).fetchone()[0]
    with rol(conn):
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado',"
            ' ack = \'{"http_status": 202, "cuerpo": "tdd"}\'::jsonb WHERE id = %s',
            (cid,),
        )
    return cid


def _observacion(conn, *, asin=ASIN, fecha="2026-09-18", precio="100.00", moneda="MXN"):
    conn.execute(
        "INSERT INTO spapi_price_observation (asin, platform, metric_date, observed_at,"
        " own_listing_price, own_listing_currency)"
        " VALUES (%s, 'amazon_mx', %s, %s, %s, %s)",
        (asin, fecha, f"{fecha}T12:00:00+00", precio, moneda),
    )


def _ofertas_body(precio, moneda="MXN", seller=PROPIO, asin=ASIN):
    return {
        "payload": {
            "ASIN": asin,
            "status": "Success",
            "Offers": [
                {
                    "SellerId": seller,
                    "IsBuyBoxWinner": True,
                    "IsFulfilledByAmazon": True,
                    "ListingPrice": {"Amount": precio, "CurrencyCode": moneda},
                }
            ],
        }
    }


def _competitivo_body():
    return {"payload": []}


class _RedFalsa:
    """LWA + GETs de pricing + PATCH scripteados; cuenta llamadas."""

    def __init__(
        self, *, gets_ofertas=(), gets_competitivos=(), patchs=(), exige_fila=None, lwa=()
    ):
        self.gets_ofertas = list(gets_ofertas)
        self.gets_competitivos = list(gets_competitivos)
        self.patchs = list(patchs)
        self.exige_fila = exige_fila
        self.lwa = list(lwa)
        self.n_patch = 0
        self.n_get = 0
        self.tokens = 0
        self.pedidos_patch = []

    def _handler(self, request):
        path = request.url.path
        if path == "/auth/o2/token":
            self.tokens += 1
            if self.lwa:
                status, body = self.lwa.pop(0)
                return httpx.Response(status, json=body)
            return httpx.Response(
                200, json={"access_token": f"tok-{self.tokens}", "expires_in": 3600}
            )
        if request.method == "GET" and "competitivePrice" in path:
            self.n_get += 1
            status, body = self.gets_competitivos.pop(0)
            if isinstance(body, bytes):
                return httpx.Response(status, content=body)
            return httpx.Response(status, json=body)
        if request.method == "GET" and "offers" in path:
            self.n_get += 1
            status, body = self.gets_ofertas.pop(0)
            if isinstance(body, bytes):
                return httpx.Response(status, content=body)
            return httpx.Response(status, json=body)
        if request.method == "PATCH":
            self.n_patch += 1
            self.pedidos_patch.append(request)
            if self.patchs and self.patchs[0][0] == "raise":
                raise self.patchs.pop(0)[1]
            if self.exige_fila is not None and not self.exige_fila():
                return httpx.Response(500, json={"status": "ERROR"})
            status, body = self.patchs.pop(0)
            return httpx.Response(status, json=body)
        raise AssertionError(f"llamada inesperada: {request.method} {path}")

    @property
    def transport(self):
        return httpx.MockTransport(self._handler)


def _clientes(red):
    lector = SpapiClient(credentials=dict(CRED), transport=red.transport, sleep=lambda s: None)
    return lector, construir_escritor(
        lector, "amazon_mx", transport=red.transport, sleep=lambda s: None
    )


def _cuerpo_falso(*, platform, sku, precio, moneda):
    return {"falso": True, "sku": sku, "precio": str(precio), "moneda": moneda}


def _cambio_cerrado(
    conn, decision, listing, *, antes="100.00", despues="110.00", aplicado=True
) -> int:
    """Original cerrado (`confirmado`): la reversa solo nace sin abierto
    (indice `precio_cambio_abierto_unico`)."""
    cid = _cambio_enviado(conn, decision, listing, antes=antes, despues=despues, aplicado=aplicado)
    with rol(conn):
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (cid,),
        )
    return cid


def _semilla_reversion(conn):
    """listing + goal + decision + cambio cerrado; devuelve (lid, cid)."""
    prod = _producto(conn)
    lid = _listing(conn, prod)
    _goal_live(conn, lid)
    dec = _decision(conn, lid)
    return lid, _cambio_cerrado(conn, dec, lid)


# ------------------------------------------------------------ leer vivo


def test_leer_precio_vivo_lee_ofertas_no_listings():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    lector, _ = _clientes(red)
    vivo = leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert (vivo.precio, vivo.moneda) == (Decimal("110.0000"), "MXN")
    assert red.n_get == 1 and red.n_patch == 0  # r5-L2: con propia no se pide el competitivo


def test_leer_precio_vivo_sin_oferta_propia_es_ausente():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0, seller="OTRO"))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    lector, _ = _clientes(red)
    with pytest.raises(PrecioVivoAusente):
        leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)


def test_leer_precio_vivo_get_roto_es_ausente():
    red = _RedFalsa(gets_ofertas=[(503, {})], gets_competitivos=[(200, _competitivo_body())])
    lector, _ = _clientes(red)
    with pytest.raises(PrecioVivoAusente):
        leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)


# --------------------------------------------------------------- revertir


def test_revertir_flujo_enviado_con_readback_ok():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "rev-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado" and res.motivo is None
        fila = conn.execute(
            "SELECT es_reversa, reversa_de, decision_id, estado, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, ack,"
            " readback_precio, readback_precio_currency, readback_estado"
            " FROM precio_cambio WHERE id = %s",
            (res.id_reversa,),
        ).fetchone()
        assert fila[:4] == (True, cid, None, "enviado")
        assert (fila[4], fila[5]) == (Decimal("110.00"), "MXN")
        assert (fila[6], fila[7]) == (Decimal("100.00"), "MXN")
        assert fila[8]["cuerpo"] and "rev-1" in fila[8]["cuerpo"]
        assert (fila[9], fila[10], fila[11]) == (Decimal("100.00"), "MXN", "ok")


def test_r1_a4_revertir_original_enviado_salta():
    """r1-A4: original todavia abierto → saltado original_abierto."""
    red = _RedFalsa()
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        cid = _cambio_enviado(conn, dec, lid)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo == "original_abierto"
        assert res.id_reversa is None
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1
        assert red.n_patch == 0 and red.n_get == 0


def test_r1_a1_cambiar_patch_lleva_precio_destino_en_el_cable():
    """r1-A1: decision 100 -> 110: el cable lleva 110.00 MXN, no el vivo."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        cable = json.loads(red.pedidos_patch[0].content)
        fila = conn.execute(
            "SELECT precio_despues, precio_despues_currency FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert (Decimal(cable["precio"]), cable["moneda"]) == (fila[0], fila[1])
        assert (cable["precio"], cable["moneda"]) == ("110.0000", "MXN")


def test_r1_a1_revertir_patch_lleva_precio_destino_en_el_cable():
    """r1-A1: reversa del 100 -> 110: el cable lleva 100.00 MXN."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0)), (200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "rev-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        cable = json.loads(red.pedidos_patch[0].content)
        fila = conn.execute(
            "SELECT precio_despues, precio_despues_currency FROM precio_cambio WHERE id = %s",
            (res.id_reversa,),
        ).fetchone()
        assert (Decimal(cable["precio"]), cable["moneda"]) == (fila[0], fila[1])
        assert (cable["precio"], cable["moneda"]) == ("100.0000", "MXN")


def test_revertir_salta_si_el_vivo_ya_no_coincide():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        antes = conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0]
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and "distinto" in (res.motivo or "")
        assert res.id_reversa is None
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == antes
        assert red.n_patch == 0


def test_revertir_salta_sin_precio_vivo():
    red = _RedFalsa(gets_ofertas=[(404, {})], gets_competitivos=[(200, _competitivo_body())])
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.id_reversa is None
        assert red.n_patch == 0


@pytest.mark.parametrize("caso", ["inexistente", "reversa", "sin_enviado"])
def test_revertir_mal_uso_revienta(caso):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        if caso == "inexistente":
            cid = 999999
        elif caso == "reversa":
            original = _cambio_cerrado(conn, dec, lid)
            cid = conn.execute(
                "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at, es_reversa, reversa_de)"
                " VALUES (%s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
                " true, 'pendiente', %s, true, %s) RETURNING id",
                (lid, ANTES, original),
            ).fetchone()[0]
        else:
            cid = conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado)"
                " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN', true, 'pendiente')"
                " RETURNING id",
                (dec, lid),
            ).fetchone()[0]
            with rol(conn):
                conn.execute(
                    "UPDATE precio_cambio SET estado = 'error', error_code = 'tdd' WHERE id = %s",
                    (cid,),
                )
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(CambioNoReversible):
            revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )


def test_revertir_arma_cuerpo_antes_de_insertar():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        antes = conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0]
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(FormaParcheSinSellar, match="pendiente_sonda"):
            revertir(conn, cid, lector=lector, escritor=escritor, ahora=AHORA)
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == antes
        assert red.n_patch == 0


def test_revertir_patch_500_sella_error_con_codigo():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0)), (200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(500, {"status": "ERROR"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code, readback_precio FROM precio_cambio WHERE id = %s",
            (res.id_reversa,),
        ).fetchone()
        assert fila[0] == "error"
        assert fila[1].startswith("PATCH /listings/2021-08-01/items/") and fila[1].endswith(" 500")
        assert SKU not in fila[1]
        # Aunque el readback posterior vea precio, el estado no se mueve.
        assert fila[2] == Decimal("110.00")


def test_revertir_readback_429_agotado_es_fallido_sin_patch_extra():
    red = _RedFalsa(
        gets_ofertas=[
            (200, _ofertas_body(110.0)),
            (429, {"status": "Error"}),
            (429, {"status": "Error"}),
        ],
        gets_competitivos=[(200, _competitivo_body())] * 3,
        patchs=[(202, {"submissionId": "rev-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        fila = conn.execute(
            "SELECT readback_precio, readback_estado FROM precio_cambio WHERE id = %s",
            (res.id_reversa,),
        ).fetchone()
        assert fila == (None, "fallido")
        assert red.n_patch == 1


def test_revertir_patch_exige_fila_pendiente_primero():
    """Orden S5: el PATCH solo procede si la fila ya commiteo (probe en otra conexion)."""
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)

        def _pendiente_durable():
            otra = psycopg.connect(_dsn_db(conn), autocommit=True)
            try:
                return (
                    otra.execute(
                        "SELECT count(*) FROM precio_cambio"
                        " WHERE es_reversa AND estado = 'pendiente'"
                    ).fetchone()[0]
                    > 0
                )
            finally:
                otra.close()

        red = _RedFalsa(
            gets_ofertas=[(200, _ofertas_body(110.0)), (200, _ofertas_body(100.0))],
            gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
            patchs=[(202, {"submissionId": "rev-1", "status": "ACCEPTED"})],
            exige_fila=_pendiente_durable,
        )
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        assert red.n_patch == 1


def test_revertir_nunca_loguea_el_cuerpo_y_sanea_el_ack(monkeypatch, caplog):
    import app.redaction as redaction

    monkeypatch.setattr(redaction, "_secrets", list(redaction._secrets))
    register_secret(SECRETO)
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "rev-9", "status": "ACCEPTED", "eco": SECRETO})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn), caplog.at_level(logging.INFO, logger="app.spapi.precio_write"):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        mandado = red.pedidos_patch[0].content.decode("utf-8")
        assert SECRETO not in caplog.text
        assert mandado not in caplog.text
        ack = conn.execute(
            "SELECT ack FROM precio_cambio WHERE id = %s", (res.id_reversa,)
        ).fetchone()[0]
        assert SECRETO not in json.dumps(ack) and "rev-9" in json.dumps(ack)


# ------------------------------------------------ cambiar_precio


def _semilla_cambio(conn, *, asin=ASIN, sku=SKU, desde="100.00", hasta="110.00", mode="live"):
    """listing + goal + decision subir; devuelve (lid, dec)."""
    prod = _producto(conn, sku=f"PR-{sku}")
    lid = _listing(conn, prod, asin=asin, sku=sku)
    _goal_live(conn, lid, mode=mode)
    return lid, _decision(conn, lid, aplicado=Decimal(hasta), mode=mode)


def test_cambiar_ack_aceptado_y_get_viejo_da_enviado():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado" and res.motivo is None
        fila = conn.execute(
            "SELECT estado, precio_antes, precio_despues, ack,"
            " readback_precio, readback_estado FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "enviado"
        assert (fila[1], fila[2]) == (Decimal("100.00"), Decimal("110.00"))
        assert "c-1" in fila[3]["cuerpo"]
        # El GET viejo no mueve el estado: readback solo informa.
        assert (fila[4], fila[5]) == (Decimal("100.00"), "ok")


def test_cambiar_ack_error_y_get_nuevo_da_error():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(500, {"status": "ERROR"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code, readback_precio, readback_estado"
            " FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error" and fila[1].endswith(" 500") and SKU not in fila[1]
        # Aunque el GET nuevo muestre el precio, el estado no se mueve.
        assert (fila[2], fila[3]) == (Decimal("110.00"), "ok")


def test_cambiar_ack_ok_y_get_distinto_da_enviado_con_readback_ok():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(105.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-2", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        fila = conn.execute(
            "SELECT readback_precio, readback_estado FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila == (Decimal("105.00"), "ok")


def test_r1_a2_401_y_lwa_400_sella_lwa_sin_huerfanas():
    """r1-A2: el refresh que LWA rechaza sella `error ... lwa`, sin pendiente."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(401, {})],
        lwa=[
            (200, {"access_token": "tok-vivo", "expires_in": 3600}),
            (400, {"error": "invalid_grant"}),
        ],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error"
        assert fila[1] == f"PATCH /listings/2021-08-01/items/{PROPIO} lwa"
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_cambio WHERE estado = 'pendiente'"
            ).fetchone()[0]
            == 0
        )


def test_r1_a2_error_de_red_sella_sin_relanzar():
    """r1-A2: la red caída sella `error ... red` y no relanza."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[("raise", httpx.ConnectError("caido"))],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code, readback_estado FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error"
        assert fila[1] == f"PATCH /listings/2021-08-01/items/{PROPIO} red"
        assert fila[2] == "ok"


def test_r1_a2_excepcion_rara_sella_y_relanza():
    """r1-A2: un bug sella `error ... excepcion:<Tipo>` y relanza."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[("raise", RuntimeError("bug-falso"))],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(RuntimeError, match="bug-falso"):
            cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert fila[0] == "error"
        assert fila[1] == f"PATCH /listings/2021-08-01/items/{PROPIO} excepcion:RuntimeError"


def test_r1_a2_sku_nulo_no_inserta_ni_sale_a_red():
    """r1-A2: listing sin SKU se valida antes del INSERT."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod, sku=None)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(PublicacionSinSku, match="listing_sin_sku"):
            cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0 and red.n_get == 0


def test_r1_a2_sku_vacio_en_revertir_no_inserta_ni_sale_a_red():
    """r1-A2: SKU vacío en la reversa se valida antes del INSERT."""
    red = _RedFalsa()
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod, sku="")
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        cid = _cambio_cerrado(conn, dec, lid)
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(PublicacionSinSku, match="listing_sin_sku"):
            revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1
        assert red.n_patch == 0 and red.n_get == 0


@pytest.mark.parametrize(
    "ack",
    [{"submissionId": "c-9"}, {"submissionId": "c-9", "status": "ERROR"}],
    ids=["sin-status", "status-distinto"],
)
def test_cambiar_200_sin_accepted_es_error(ack):
    """r1-A5: sin `status == "ACCEPTED"` explicito no hay envio."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(200, ack)],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error" and fila[1].endswith(" 200")


def test_r1_a6_readback_con_lwa_caido_es_fallido_sin_tumbar():
    """r1-A6: LWA caído en el readback → fallido, el enviado no se mueve."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (401, {})],
        gets_competitivos=[(200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-1", "status": "ACCEPTED"})],
        lwa=[
            (200, {"access_token": "tok-vivo", "expires_in": 3600}),
            (400, {"error": "invalid_grant"}),
        ],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        fila = conn.execute(
            "SELECT estado, readback_estado FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila == ("enviado", "fallido")


def test_cambiar_429_agotado_readback_fallido_sin_escritura_extra():
    red = _RedFalsa(
        gets_ofertas=[
            (200, _ofertas_body(100.0)),
            (429, {"status": "Error"}),
            (429, {"status": "Error"}),
        ],
        gets_competitivos=[(200, _competitivo_body())] * 3,
        patchs=[(202, {"submissionId": "c-3", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        fila = conn.execute(
            "SELECT readback_precio, readback_estado FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila == (None, "fallido")
        assert red.n_patch == 1


def test_r1_a3_cambiar_vivo_distinto_de_p_actual_salta():
    """r1-A3: el vivo ya no es el de la decision → sin fila y sin PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and "precio_vivo_distinto" in (res.motivo or "")
        assert res.id_cambio is None
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0


def test_r1_a3_cambiar_vivo_otra_moneda_salta():
    """r1-A3: mismo importe, otra moneda → saltado."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0, moneda="USD"))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.id_cambio is None
        assert red.n_patch == 0


def test_r1_a3_cambiar_vivo_igual_a_p_actual_procede():
    """r1-A3, otro lado: vivo == p_actual → el cambio sí se escribe."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado" and res.id_cambio is not None


def test_cambiar_sin_vivo_es_error_sin_fila():
    red = _RedFalsa(gets_ofertas=[(404, {})], gets_competitivos=[(200, _competitivo_body())])
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error" and res.id_cambio is None
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0


def test_cambiar_decision_que_no_mueve_precio_revienta():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid, resultado="mantener")
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(DecisionSinAccion):
            cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0


def test_cambiar_forma_sin_sellar_no_deja_fila_ni_red():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(FormaParcheSinSellar, match="pendiente_sonda"):
            cambiar_precio(conn, dec, lector=lector, escritor=escritor, ahora=AHORA)
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0


# ----------------------------------------------- cerrar_por_observacion


def _enviado_abierto(conn, dec, lid, *, enviado=ANTES):
    return conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at)"
        " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN', true, 'pendiente', %s)"
        " RETURNING id",
        (dec, lid, enviado),
    ).fetchone()[0]


def _sellar_enviado_sql(conn, cid):
    with rol(conn):
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado',"
            " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
            (cid,),
        )


HOY_CIERRE = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC).date()


def _semilla_cierre(conn, *, asin, sku, enviado=ANTES):
    lid, dec = _semilla_cambio(conn, asin=asin, sku=sku)
    cid = _enviado_abierto(conn, dec, lid, enviado=enviado)
    _sellar_enviado_sql(conn, cid)
    return lid, cid


def test_cerrar_observacion_igual_confirma_distinta_no():
    with db_39c() as conn:
        _, c_igual = _semilla_cierre(conn, asin="B0C1", sku="SKU-C1")
        _, c_dist = _semilla_cierre(conn, asin="B0C2", sku="SKU-C2")
        _, c_moneda = _semilla_cierre(conn, asin="B0C3", sku="SKU-C3")
        _, c_sin = _semilla_cierre(conn, asin="B0C4", sku="SKU-C4")
        _, c_hoy = _semilla_cierre(conn, asin="B0C5", sku="SKU-C5", enviado=AHORA)
        _observacion(conn, asin="B0C1", fecha="2026-09-18", precio="110.00")
        _observacion(conn, asin="B0C2", fecha="2026-09-18", precio="111.00")
        _observacion(conn, asin="B0C3", fecha="2026-09-18", precio="110.00", moneda="USD")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, HOY_CIERRE)
        assert cuenta == {"confirmado": 1, "no_confirmado": 2, "intactos": 1}
        estados = dict(
            conn.execute(
                "SELECT id, estado FROM precio_cambio WHERE id = ANY(%s)",
                ([c_igual, c_dist, c_moneda, c_sin, c_hoy],),
            ).fetchall()
        )
        assert estados[c_igual] == "confirmado"
        assert estados[c_dist] == "no_confirmado"
        assert estados[c_moneda] == "no_confirmado"
        assert estados[c_sin] == "enviado"
        assert estados[c_hoy] == "enviado"
        por = dict(
            conn.execute(
                "SELECT id, confirmado_por FROM precio_cambio WHERE id = ANY(%s)",
                ([c_igual, c_dist, c_moneda],),
            ).fetchall()
        )
        assert set(por.values()) == {"observacion"}


def test_cerrar_reversa_se_cierra_por_observacion():
    with db_39c() as conn:
        lid, dec = _semilla_cambio(conn, asin="B0C6", sku="SKU-C6")
        cid = _enviado_abierto(conn, dec, lid)
        _sellar_enviado_sql(conn, cid)
        with rol(conn):
            conn.execute(
                "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
                " WHERE id = %s",
                (cid,),
            )
            rev = conn.execute(
                "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at, es_reversa, reversa_de)"
                " VALUES (%s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN', true,"
                " 'pendiente', %s, true, %s) RETURNING id",
                (lid, ANTES, cid),
            ).fetchone()[0]
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado',"
                " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
                (rev,),
            )
        _observacion(conn, asin="B0C6", fecha="2026-09-18", precio="100.00")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, HOY_CIERRE)
        assert cuenta == {"confirmado": 1, "no_confirmado": 0, "intactos": 0}
        assert (
            conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (rev,)).fetchone()[0]
            == "confirmado"
        )


# -------------------------------------------------- tools/precio_reversa


def test_tool_dry_run_imprime_plan_y_huella_sin_tocar_nada(monkeypatch, capsys):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        # El tool abre su propia conexion (ORBIT_DSN_DECIDE), no el rol del test.
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        out = capsys.readouterr().out
        assert rc == 0
        assert f"[revertir] cambio={cid} {SKU} amazon_mx" in out
        assert "huella: " in out
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_tool_dry_run_original_abierto_salta_y_no_entra_como_revertir(monkeypatch, capsys):
    """r1-A4: el plan muestra el abierto como saltado con su mensaje."""
    red = _RedFalsa()
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        cid = _cambio_enviado(conn, dec, lid)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        out = capsys.readouterr().out
        assert rc == 0
        assert (
            f"[saltar] cambio={cid} {SKU} amazon_mx"
            " motivo=original_abierto (cierra con la observación del día siguiente)" in out
        )
        assert "[revertir]" not in out
        assert red.n_patch == 0 and red.n_get == 0


def test_r1_a7_go_un_escritor_por_operacion(monkeypatch, capsys):
    """r1-A7b: lote de dos reversibles → dos escritores (uno por operación)."""
    asin2 = "B0TESTC002"
    b110 = lambda a: (200, _ofertas_body(110.0, asin=a))  # noqa: E731
    red = _RedFalsa(
        # dry-run (A, B); go-plan (A, B); rev1 pre+rb (A, A); rev2 pre+rb (B, B).
        gets_ofertas=[b110(a) for a in (ASIN, asin2, ASIN, asin2, ASIN, ASIN, asin2, asin2)],
        gets_competitivos=[(200, _competitivo_body())] * 8,
        patchs=[(202, {"submissionId": "r1", "status": "ACCEPTED"})] * 2,
    )
    with db_39c() as conn:
        _, cid1 = _semilla_reversion(conn)
        lid2, dec2 = _semilla_cambio(conn, asin=asin2, sku="SKU-C7")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        construidos = []
        real_construir = pw.construir_escritor

        def _cuenta(lector, platform, **kw):
            w = real_construir(lector, platform, **kw)
            construidos.append(w)
            return w

        monkeypatch.setattr(pw, "construir_escritor", _cuenta)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        huella = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("huella: ")][
            0
        ].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        assert len(construidos) == 2 and construidos[0] is not construidos[1]


def test_r1_a7_plan_imprime_la_reversa_al_reves(monkeypatch, capsys):
    """r1-A7c: el plan imprime lo que va a pasar (110.00 MXN -> 100.00 MXN)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        out = capsys.readouterr().out
        assert rc == 0
        assert f"[revertir] cambio={cid} {SKU} amazon_mx 110.00 MXN -> 100.00 MXN" in out


def test_r1_a7_ahora_no_utc_se_normaliza():
    """r1-A7d: `ahora` +05:00 → sello y fecha en UTC."""
    from datetime import timedelta, timezone

    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        _observacion(conn, fecha="2026-09-17", precio="77.00")
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=datetime(2026, 9, 18, 1, 0, tzinfo=timezone(timedelta(hours=5))),
            )
        assert res.estado == "enviado"
        fila = conn.execute(
            "SELECT enviado_at, precio_observado_antes FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0].utcoffset().total_seconds() == 0
        assert fila[1] == Decimal("77.00")


def test_tool_dry_run_salta_sin_abortar(monkeypatch, capsys):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        out = capsys.readouterr().out
        assert rc == 0
        assert "[saltar]" in out and "precio_vivo_distinto" in out
        assert red.n_patch == 0


def test_tool_dry_run_no_construye_escritor(monkeypatch, capsys):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        def _explota(*a, **k):
            raise AssertionError("el dry-run no construye el escritor")

        monkeypatch.setattr(pw, "construir_escritor", _explota)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        assert rc == 0


def test_r4_g8_go_sin_go_dice_que_falta_go(monkeypatch):
    """r4-G8: --acepto sin --go dice que falta --go (no un Abortar generico)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="falta --go"):
            tool.main(
                ["--cambio-id", str(cid), "--acepto-mutacion-real"],
                transport=red.transport,
                credentials=dict(CRED),
            )


def test_r4_g8_go_sin_huella_dice_que_falta_huella(monkeypatch):
    """r4-G8: --acepto --go sin --huella dice que falta --huella."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="falta --huella"):
            tool.main(
                ["--cambio-id", str(cid), "--acepto-mutacion-real", "--go", "si"],
                transport=red.transport,
                credentials=dict(CRED),
            )


def test_tool_go_huella_mala_aborta_sin_tocar(monkeypatch):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))] * 2,
        gets_competitivos=[(200, _competitivo_body())] * 2,
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        with pytest.raises(tool.Abortar, match="huella"):
            monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
            tool.main(
                [
                    "--cambio-id",
                    str(cid),
                    "--acepto-mutacion-real",
                    "--huella",
                    "0" * 16,
                    "--go",
                    "si",
                ],
                transport=red.transport,
                credentials=dict(CRED),
            )
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_tool_go_huella_buena_choca_con_forma_sin_sellar(monkeypatch, capsys):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))] * 4,
        gets_competitivos=[(200, _competitivo_body())] * 4,
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        assert seco == 0
        lineas = capsys.readouterr().out.splitlines()
        huella = [ln for ln in lineas if ln.startswith("huella: ")][0].split(": ")[1]
        with pytest.raises(tool.Abortar, match="sin sellar"):
            tool.main(
                [
                    "--cambio-id",
                    str(cid),
                    "--acepto-mutacion-real",
                    "--huella",
                    huella,
                    "--go",
                    "si",
                ],
                transport=red.transport,
                credentials=dict(CRED),
            )
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_tool_cambio_inexistente_aborta(monkeypatch):
    red = _RedFalsa()
    with db_39c() as conn:
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="inexistente"):
            tool.main(["--cambio-id", "424242"], transport=red.transport, credentials=dict(CRED))


def test_r1_a8_docstring_ventana_commit_patch():
    """r1-A8: cambiar y revertir documentan la ventana COMMIT-PATCH.

    Si el proceso muere entre el INSERT (COMMIT) y el PATCH, queda una
    fila pendiente huerfana que el indice de abierto unico vuelve
    visible (todo reintento la ve abierta y salta); el docstring dice
    como detectarla.
    """
    from app.spapi import precio_write as pw

    for fn in (pw.cambiar_precio, pw.revertir):
        doc = (fn.__doc__ or "").lower()
        assert "huerfana" in doc
        assert "commit" in doc and "patch" in doc
        assert "pendiente" in doc


def test_r1_m_p5_shadow_no_mueve_precio():
    """r1-M P5: `subir` en shadow -> DecisionSinAccion, cero PATCH, cero filas."""
    red = _RedFalsa()
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn, mode="shadow")
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(DecisionSinAccion):
            cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert red.n_patch == 0 and red.n_get == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0


def test_r1_m_p7_accepted_sin_submission_id_es_error():
    """r1-M P7: 202 ACCEPTED sin submissionId no es aceptacion -> error."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error"


def test_r1_m_p8_4xx_con_accepted_es_error():
    """r1-M P8: 400 con submissionId y ACCEPTED no es aceptacion -> error."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(400, {"submissionId": "c-4xx", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error" and fila[1].endswith(" 400")


def test_r1_m_p11_error_code_sin_sku_valor_exacto():
    """r1-M P11: el error_code es `PATCH <ruta sin SKU> <status>`, exacto."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(500, {"status": "ERROR"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT error_code FROM precio_cambio WHERE id = %s", (res.id_cambio,)
        ).fetchone()[0]
        assert fila == f"PATCH /listings/2021-08-01/items/{escritor.seller_id} 500"
        assert SKU not in fila


def test_r1_m_p13_virtual_no_se_revierte():
    """r1-M P13: cambio virtual (aplicado=false) -> CambioNoReversible, cero PATCH."""
    red = _RedFalsa()
    with db_39c() as conn:
        lid, dec = _semilla_cambio(conn, mode="shadow")
        # El virtual nace cerrado (trigger): confirmado/virtual, sin pasar por pendiente.
        cid = conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at, confirmado_por)"
            " VALUES (%s, %s, 'amazon_mx', '100.00', 'MXN', '110.00', 'MXN',"
            " false, 'confirmado', %s, 'virtual') RETURNING id",
            (dec, lid, ANTES),
        ).fetchone()[0]
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(CambioNoReversible):
            revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert red.n_patch == 0 and red.n_get == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r1_m_p15_otra_moneda_salta():
    """r1-M P15: mismo importe en otra moneda no coincide -> saltado, cero PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0, moneda="USD"))],
        gets_competitivos=[(200, _competitivo_body())],
        patchs=[(202, {"submissionId": "x", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo.startswith("precio_vivo_distinto")
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r1_m_p17_observacion_mismo_dia_no_cierra():
    """r1-M P17: la observacion del mismo dia del envio no cierra (maduracion)."""
    with db_39c() as conn:
        _, cid = _semilla_cierre(conn, asin="B0TESTC17A", sku="SKU-C17")
        _observacion(conn, asin="B0TESTC17A", fecha="2026-09-17", precio="110.00")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, HOY_CIERRE)
        assert cuenta == {"confirmado": 0, "no_confirmado": 0, "intactos": 1}
        estado = conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[
            0
        ]
        assert estado == "enviado"


def test_r1_m_p19_manda_la_mas_reciente():
    """r1-M P19: dos observaciones posteriores distintas -> manda la mas reciente."""
    from datetime import date

    with db_39c() as conn:
        _, cid = _semilla_cierre(conn, asin="B0TESTC19A", sku="SKU-C19")
        _observacion(conn, asin="B0TESTC19A", fecha="2026-09-18", precio="111.00")
        _observacion(conn, asin="B0TESTC19A", fecha="2026-09-19", precio="110.00")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, date(2026, 9, 19))
        assert cuenta == {"confirmado": 1, "no_confirmado": 0, "intactos": 0}
        estado = conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[
            0
        ]
        assert estado == "confirmado"


def test_r1_m_t1_go_sin_huella_aborta(monkeypatch, capsys):
    """r1-M T1: go con --acepto-mutacion-real pero sin --huella -> aborta, cero PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="huella"):
            tool.main(
                ["--cambio-id", str(cid), "--acepto-mutacion-real", "--go", "si"],
                transport=red.transport,
                credentials=dict(CRED),
            )
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r1_m_t4_saltado_no_aborta_el_lote(monkeypatch, capsys):
    """r1-M T4: lote de dos, el primero saltado, el segundo si se revierte."""
    asin2 = "B0TESTC002"
    a100 = (200, _ofertas_body(100.0))
    b110 = (200, _ofertas_body(110.0, asin=asin2))
    b100 = (200, _ofertas_body(100.0, asin=asin2))
    red = _RedFalsa(
        # dry (saltado, reversible); go igual; rev2 pre + readback.
        gets_ofertas=[a100, b110, a100, b110, b110, b100],
        gets_competitivos=[(200, _competitivo_body())] * 6,
        patchs=[(202, {"submissionId": "t4", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid1 = _semilla_reversion(conn)
        lid2, dec2 = _semilla_cambio(conn, asin=asin2, sku="SKU-T4")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        huella = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("huella: ")][
            0
        ].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "precio_vivo_distinto" in out
        assert "[hecho] cambio=" in out and f"cambio={cid2}" in out
        assert red.n_patch == 1
        reversas = conn.execute(
            "SELECT count(*) FROM precio_cambio WHERE es_reversa AND reversa_de = %s",
            (cid2,),
        ).fetchone()[0]
        assert reversas == 1
        huerfanas = conn.execute(
            "SELECT count(*) FROM precio_cambio WHERE es_reversa AND reversa_de = %s",
            (cid1,),
        ).fetchone()[0]
        assert huerfanas == 0


def _red_cuenta_pendientes(dsn_db, pares=8, precio=110.0):
    """Red falsa cuyo PATCH cuenta desde OTRA conexion las `pendiente` confirmadas.

    Si no ve exactamente 1, contesta 500 (simula a Amazon rechazando lo
    que Orbit no tiene durable). Devuelve (transport, contadores).
    """
    import httpx

    visto = {"n_patch": 0, "n_get": 0, "vistos": []}
    ofertas = [(200, _ofertas_body(precio))] * pares
    competitivos = [(200, _competitivo_body())] * pares

    def _handler(request):
        path = request.url.path
        if path == "/auth/o2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if request.method == "GET" and "offers" in path:
            visto["n_get"] += 1
            status, body = ofertas.pop(0)
            return httpx.Response(status, json=body)
        if request.method == "GET" and "competitivePrice" in path:
            status, body = competitivos.pop(0)
            return httpx.Response(status, json=body)
        if request.method == "PATCH":
            visto["n_patch"] += 1
            otra = psycopg.connect(dsn_db, autocommit=True)
            try:
                n = otra.execute(
                    "SELECT count(*) FROM precio_cambio WHERE estado = 'pendiente'"
                ).fetchone()[0]
            finally:
                otra.close()
            visto["vistos"].append(n)
            if n != 1:
                return httpx.Response(500, json={"status": "ERROR"})
            return httpx.Response(202, json={"submissionId": "s-durable", "status": "ACCEPTED"})
        raise AssertionError(f"llamada inesperada: {request.method} {path}")

    return httpx.MockTransport(_handler), visto


def test_r2_b1_go_con_pendiente_durable(monkeypatch, capsys):
    """r2-B1(a): al momento del PATCH la reversa pendiente ya es durable (otra conexion la ve)."""
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        transport, visto = _red_cuenta_pendientes(_dsn_db(conn))
        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(["--cambio-id", str(cid)], transport=transport, credentials=dict(CRED))
        assert seco == 0
        huella = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("huella: ")][
            0
        ].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        assert visto["n_patch"] == 1 and visto["vistos"] == [1]
        estado = conn.execute(
            "SELECT estado FROM precio_cambio WHERE es_reversa AND reversa_de = %s", (cid,)
        ).fetchone()[0]
        assert estado == "enviado"


def test_r2_b1_cambiar_con_pendiente_durable():
    """r2-B1(b): lo mismo llamando a `cambiar_precio` directo."""
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        from app.spapi.client import SpapiClient
        from app.spapi.write_client import SpapiWriteClient

        transport, visto = _red_cuenta_pendientes(_dsn_db(conn), precio=100.0)
        lector = SpapiClient(credentials=dict(CRED), transport=transport, sleep=lambda s: None)
        escritor = SpapiWriteClient(
            platform="amazon_mx",
            modo_confirmado="live",
            lector=lector,
            transport=transport,
            sleep=lambda s: None,
        )
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        assert visto["n_patch"] == 1 and visto["vistos"] == [1]


def test_r2_b1_sin_autocommit_es_mal_uso():
    """r2-B1(c): sin autocommit -> ValueError antes de nada, cero filas, cero red."""
    red = _RedFalsa()
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        cruda = psycopg.connect(_dsn_db(conn))
        try:
            assert cruda.autocommit is False
            with pytest.raises(ValueError, match="autocommit"):
                cambiar_precio(
                    cruda,
                    dec,
                    lector=lector,
                    escritor=escritor,
                    construir_cuerpo=_cuerpo_falso,
                    ahora=AHORA,
                )
        finally:
            cruda.close()
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0 and red.n_get == 0


def test_r2_b2_p9_invalid_es_error():
    """r2-B2 P9: 202 con submissionId y status INVALID (rechazo real) -> error."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-inv", "status": "INVALID"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error" and fila[1].endswith(" 202")


def test_r2_b2_p20_confirmado_por_exacta_en_ambos():
    """r2-B2 P20: el cierre deja confirmado_por='observacion' en confirmado Y no_confirmado."""
    with db_39c() as conn:
        _, c_igual = _semilla_cierre(conn, asin="B0TESTD20A", sku="SKU-D20")
        _, c_dist = _semilla_cierre(conn, asin="B0TESTD20B", sku="SKU-D21")
        _observacion(conn, asin="B0TESTD20A", fecha="2026-09-18", precio="110.00")
        _observacion(conn, asin="B0TESTD20B", fecha="2026-09-18", precio="111.00")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, HOY_CIERRE)
        assert cuenta == {"confirmado": 1, "no_confirmado": 1, "intactos": 0}
        filas = dict(
            conn.execute(
                "SELECT id, confirmado_por FROM precio_cambio WHERE id = ANY(%s)",
                ([c_igual, c_dist],),
            ).fetchall()
        )
        assert filas[c_igual] == "observacion"
        assert filas[c_dist] == "observacion"


def test_r2_b2_p22_cuerpo_real_no_se_loguea(caplog):
    """r2-B2 P22: lo que de verdad salio por el cable no aparece en logs, en cambiar y revertir."""
    asin2 = "B0TESTC002"
    red = _RedFalsa(
        gets_ofertas=[
            (200, _ofertas_body(100.0)),
            (200, _ofertas_body(100.0)),
            (200, _ofertas_body(110.0, asin=asin2)),
            (200, _ofertas_body(110.0, asin=asin2)),
        ],
        gets_competitivos=[(200, _competitivo_body())] * 4,
        patchs=[(202, {"submissionId": "l1", "status": "ACCEPTED"})] * 2,
    )
    cuerpo = lambda **kw: {"marcador": "ZZ9X8", "precio": str(kw["precio"])}  # noqa: E731
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn, sku="SKU-P22")
        prod = _producto(conn, sku="PR-SKU-R22")
        lid = _listing(conn, prod, asin=asin2, sku="SKU-R22")
        _goal_live(conn, lid)
        de2 = _decision(conn, lid)
        cid = _cambio_cerrado(conn, de2, lid)
        lector, escritor = _clientes(red)
        with rol(conn), caplog.at_level(logging.INFO, logger="app.spapi.precio_write"):
            cambiar_precio(
                conn, dec, lector=lector, escritor=escritor, construir_cuerpo=cuerpo, ahora=AHORA
            )
            revertir(
                conn, cid, lector=lector, escritor=escritor, construir_cuerpo=cuerpo, ahora=AHORA
            )
        mandados = [p.content.decode("utf-8") for p in red.pedidos_patch]
        assert len(mandados) == 2 and all("ZZ9X8" in m for m in mandados)
        assert "ZZ9X8" not in caplog.text


def test_r3_k1_otro_abierto_mismo_listing_salta():
    """r3-K1: otro abierto del par -> saltado, sin fila ni PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        lid, dec_vieja = _semilla_cambio(conn)
        cid = _cambio_cerrado(conn, dec_vieja, lid)
        _cambio_enviado(conn, dec_vieja, lid)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo == "listing_con_cambio_abierto"
        assert red.n_patch == 0 and red.n_get == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 2


def test_r3_k1_tool_afectado_salta_otro_revierte(monkeypatch, capsys):
    """r3-K1 tool: lote de dos, el del listing con abierto salta, el otro se revierte."""
    asin2 = "B0TESTC002"
    b110 = (200, _ofertas_body(110.0, asin=asin2))
    b100 = (200, _ofertas_body(100.0, asin=asin2))
    red = _RedFalsa(
        # dry (cid2) + go (cid2) + rev2 pre + rb; cid1 salta sin leer.
        gets_ofertas=[b110, b110, b110, b100],
        gets_competitivos=[(200, _competitivo_body())] * 4,
        patchs=[(202, {"submissionId": "k1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        lid1, dec1 = _semilla_cambio(conn, sku="SKU-K1")
        cid1 = _cambio_cerrado(conn, dec1, lid1)
        _cambio_enviado(conn, dec1, lid1)
        lid2, dec2 = _semilla_cambio(conn, asin=asin2, sku="SKU-K2")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        out_seco = capsys.readouterr().out
        assert "listing_con_cambio_abierto" in out_seco
        huella = [ln for ln in out_seco.splitlines() if ln.startswith("huella: ")][0].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "listing_con_cambio_abierto" in out
        assert f"[hecho] cambio={cid2}" in out
        assert red.n_patch == 1
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 4


def test_r3_k2_lwa_caido_en_prelectura_es_sin_precio_vivo():
    """r3-K2: SpapiError del lector en el pre-read -> saltado sin_precio_vivo, sin fila ni PATCH."""
    red = _RedFalsa(lwa=[(400, {})])
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo == "sin_precio_vivo"
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r3_k3_estado_fuera_de_vocabulario_es_mal_uso():
    """r3-K3: estado fuera de enviado/error/saltado -> ValueError en ambas."""
    from app.spapi.precio_write import ResultadoCambio, ResultadoReversion

    with pytest.raises(ValueError, match="estado"):
        ResultadoCambio(id_cambio=None, estado="recibido", motivo=None)
    with pytest.raises(ValueError, match="estado"):
        ResultadoReversion(id_reversa=None, estado="recibido", motivo=None)


def test_r3_k6_error_de_programacion_sube_no_es_sin_precio_vivo(monkeypatch, capsys):
    """r3-K6: un bug en la lectura sube al dueno, no se disfraza de sin_precio_vivo."""
    red = _RedFalsa()
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        def _roto(*a, **kw):
            raise RuntimeError("bug-simulado")

        monkeypatch.setattr(pw, "leer_precio_vivo", _roto)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(RuntimeError, match="bug-simulado"):
            tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))


def test_r3_k7_reversa_en_error_devuelve_1(monkeypatch, capsys):
    """r3-K7: si una reversa del lote termina error, el go devuelve 1."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))] * 4,
        gets_competitivos=[(200, _competitivo_body())] * 4,
        patchs=[(500, {"status": "ERROR"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        assert seco == 0
        huella = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("huella: ")][
            0
        ].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert f"[hecho] cambio={cid} estado=error" in out


def test_r3_k8_revertir_sin_autocommit_es_mal_uso():
    """r3-K8: revertir sin autocommit -> ValueError antes de nada, cero filas, cero red."""
    red = _RedFalsa()
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        cruda = psycopg.connect(_dsn_db(conn))
        try:
            assert cruda.autocommit is False
            with pytest.raises(ValueError, match="autocommit"):
                revertir(
                    cruda,
                    cid,
                    lector=lector,
                    escritor=escritor,
                    construir_cuerpo=_cuerpo_falso,
                    ahora=AHORA,
                )
        finally:
            cruda.close()
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1
        assert red.n_patch == 0 and red.n_get == 0


def test_r3_k8_cerrar_sin_autocommit_es_mal_uso():
    """r3-K8: cerrar sin autocommit -> ValueError antes de nada, sin tocar filas."""
    with db_39c() as conn:
        _, cid = _semilla_cierre(conn, asin="B0TESTK08A", sku="SKU-K08")
        _observacion(conn, asin="B0TESTK08A", fecha="2026-09-18", precio="110.00")
        cruda = psycopg.connect(_dsn_db(conn))
        try:
            assert cruda.autocommit is False
            with pytest.raises(ValueError, match="cada cierre confirma"):
                cerrar_por_observacion(cruda, HOY_CIERRE)
        finally:
            cruda.close()
        estado = conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[
            0
        ]
        assert estado == "enviado"


def test_r4_g2_cambiar_con_abierto_del_par_salta():
    """r4-G2: abierto del par -> cambiar salta, sin fila ni PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        lid, dec = _semilla_cambio(conn)
        _cambio_enviado(conn, dec, lid)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo == "listing_con_cambio_abierto"
        assert red.n_patch == 0 and red.n_get == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r4_g3_plan_sin_sku_salta(monkeypatch, capsys):
    """r4-G3: el plan muestra [saltar] sin_sku sin leer ni tocar red de escritura."""
    red = _RedFalsa()
    with db_39c() as conn:
        prod1 = _producto(conn, sku="PR-SKU-G3")
        lid1 = _listing(conn, prod1, sku="")
        _goal_live(conn, lid1)
        dec1 = _decision(conn, lid1)
        cid1 = _cambio_cerrado(conn, dec1, lid1)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid1)], transport=red.transport, credentials=dict(CRED))
        assert rc == 0
        out = capsys.readouterr().out
        assert f"[saltar] cambio={cid1}" in out and "sin_sku" in out
        assert red.n_patch == 0 and red.n_get == 0


def test_r4_g3_lote_sigue_tras_previsible_y_devuelve_1(monkeypatch, capsys):
    """r4-G3: primero sin SKU valido -> [error] en el go, el segundo se revierte, rc 1."""
    asin1 = "B0TESTC003"
    asin2 = "B0TESTC002"
    a110 = (200, _ofertas_body(110.0, asin=asin1))
    b110 = (200, _ofertas_body(110.0, asin=asin2))
    b100 = (200, _ofertas_body(100.0, asin=asin2))
    red = _RedFalsa(
        # dry (cid1, cid2) + go (cid1, cid2) + rev2 pre + rb; cid1 revienta en el go.
        gets_ofertas=[a110, b110, a110, b110, b110, b100],
        gets_competitivos=[(200, _competitivo_body())] * 6,
        patchs=[(202, {"submissionId": "g3", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        prod1 = _producto(conn, sku="PR-SKU-G31")
        lid1 = _listing(conn, prod1, asin=asin1, sku="A/B")
        _goal_live(conn, lid1)
        dec1 = _decision(conn, lid1)
        cid1 = _cambio_cerrado(conn, dec1, lid1)
        lid2, dec2 = _semilla_cambio(conn, asin=asin2, sku="SKU-G32")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        out_seco = capsys.readouterr().out
        assert out_seco.count("[revertir]") == 2
        huella = [ln for ln in out_seco.splitlines() if ln.startswith("huella: ")][0].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert f"[error] cambio={cid1}" in out
        assert f"[hecho] cambio={cid2}" in out
        assert red.n_patch == 1


def test_r4_g4_competitivo_caido_no_bloquea_con_oferta_propia():
    """r4-G4: ofertas con propia + competitivo 5xx -> vivo resuelto."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))],
        gets_competitivos=[(500, {})],
    )
    lector, _ = _clientes(red)
    vivo = leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert (vivo.precio, vivo.moneda) == (Decimal("100.0000"), "MXN")


def test_r4_g4_sin_propia_sigue_ausente_aunque_caiga_competitivo():
    """r4-G4: sin oferta propia el competitivo caido no inventa precio."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0, seller="OTRO"))],
        gets_competitivos=[(500, {})],
    )
    lector, _ = _clientes(red)
    with pytest.raises(PrecioVivoAusente):
        leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)


def test_r4_g6_solo_saltados_devuelve_0(monkeypatch, capsys):
    """r4-G6: lote con solo saltados -> rc 0 (los saltados no son error)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))] * 4,
        gets_competitivos=[(200, _competitivo_body())] * 4,
    )
    with db_39c() as conn:
        _, cid1 = _semilla_reversion(conn)
        prod2 = _producto(conn, sku="PR-SKU-G6")
        lid2 = _listing(conn, prod2, asin="B0TESTC006", sku="SKU-G6")
        _goal_live(conn, lid2)
        dec2 = _decision(conn, lid2)
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        out_seco = capsys.readouterr().out
        assert "precio_vivo_distinto" in out_seco
        huella = [ln for ln in out_seco.splitlines() if ln.startswith("huella: ")][0].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        assert red.n_patch == 0


# ---------------------------------------------------------- r5-L1 platform del escritor


def _escritor_otra_platform(red, lector):
    """Escritor de amazon_us contra filas de amazon_mx (r5-L1: mal uso)."""
    return construir_escritor(lector, "amazon_us", transport=red.transport, sleep=lambda s: None)


def test_r5_l1_cambiar_escritor_otra_platform_revienta_sin_fila_ni_patch():
    """r5-L1: cambiar con escritor amazon_us contra decision amazon_mx es ValueError."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))] * 2,
        gets_competitivos=[(200, _competitivo_body())] * 2,
        patchs=[(202, {"submissionId": "r5-l1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        lector = SpapiClient(credentials=dict(CRED), transport=red.transport, sleep=lambda s: None)
        with rol(conn), pytest.raises(ValueError, match="plataforma"):
            cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=_escritor_otra_platform(red, lector),
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert red.n_patch == 0
        assert conn.execute("SELECT id FROM precio_cambio").fetchall() == []


def test_r5_l1_revertir_escritor_otra_platform_revienta_sin_fila_ni_patch():
    """r5-L1: revertir con escritor amazon_us contra cambio amazon_mx es ValueError."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))] * 2,
        gets_competitivos=[(200, _competitivo_body())] * 2,
        patchs=[(202, {"submissionId": "r5-l1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector = SpapiClient(credentials=dict(CRED), transport=red.transport, sleep=lambda s: None)
        with rol(conn), pytest.raises(ValueError, match="plataforma"):
            revertir(
                conn,
                cid,
                lector=lector,
                escritor=_escritor_otra_platform(red, lector),
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert red.n_patch == 0
        assert len(conn.execute("SELECT id FROM precio_cambio").fetchall()) == 1


# ---------------------------------------------------------- r5-L2 competitivo perezoso


def test_r5_l2_con_propia_un_solo_get():
    """r5-L2: con oferta propia el competitivo ni se pide (cuota Pricing 0.5/s)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    lector, _ = _clientes(red)
    vivo = leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert (vivo.precio, vivo.moneda) == (Decimal("110.0000"), "MXN")
    assert red.n_get == 1 and red.n_patch == 0


def test_r5_l2_sin_propia_pide_competitivo_dos_gets():
    """r5-L2: sin oferta propia en ofertas se pide el competitivo (dos GETs)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0, seller="OTRO"))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    lector, _ = _clientes(red)
    with pytest.raises(PrecioVivoAusente):
        leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert red.n_get == 2


def test_r5_l2_competitivo_no_json_con_propia_resuelve():
    """r5-L2: competitivo 200 no-JSON con oferta propia resuelve sin pedirlo."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, b"esto no es json")],
    )
    lector, _ = _clientes(red)
    vivo = leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert (vivo.precio, vivo.moneda) == (Decimal("110.0000"), "MXN")
    assert red.n_get == 1


def test_r5_l2_competitivo_no_json_sin_propia_es_ausente():
    """r5-L2: competitivo 200 no-JSON sin propia es respaldo vacio -> ausente."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0, seller="OTRO"))],
        gets_competitivos=[(200, b"esto no es json")],
    )
    lector, _ = _clientes(red)
    with pytest.raises(PrecioVivoAusente):
        leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert red.n_get == 2


# ---------------------------------------------------------- r5-L3 go sin acepto


def test_r5_l3_go_con_huella_sin_acepto_aborta(monkeypatch):
    """r5-L3: --go --huella sin --acepto-mutacion-real aborta, no dry-run silencioso."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="acepto-mutacion-real"):
            tool.main(
                ["--cambio-id", str(cid), "--go", "si", "--huella", "0" * 16],
                transport=red.transport,
                credentials=dict(CRED),
            )
        assert red.n_patch == 0
        assert len(conn.execute("SELECT id FROM precio_cambio").fetchall()) == 1


# ---------------------------------------------------------- r5-L4 carrera con abierto


def _cero_abiertos(monkeypatch):
    """El conteo de abiertos del par devuelve 0: la fila abierta entra
    entre el chequeo y el INSERT (carrera r5-L4)."""
    real = psycopg.Connection.execute

    def _sin_abiertos(self, query, params=None):
        if isinstance(query, str) and "count(*)" in query:

            class _Cero:
                def fetchone(self):
                    return (0,)

            return _Cero()
        return real(self, query) if params is None else real(self, query, params)

    monkeypatch.setattr(psycopg.Connection, "execute", _sin_abiertos)


def test_r5_l4_cambiar_carrera_abierto_entre_chequeo_e_insert_salta(monkeypatch):
    """r5-L4: UniqueViolation en el INSERT de cambiar -> saltado, no excepcion cruda."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body())],
        patchs=[(202, {"submissionId": "r5-l4", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado)"
            " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN', true, 'pendiente')",
            (dec, lid),
        )
        _cero_abiertos(monkeypatch)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert (res.estado, res.motivo) == ("saltado", "listing_con_cambio_abierto")
        assert res.id_cambio is None
        assert red.n_patch == 0
        assert len(conn.execute("SELECT id FROM precio_cambio").fetchall()) == 1


def test_r5_l4_revertir_carrera_abierto_entre_chequeo_e_insert_salta(monkeypatch):
    """r5-L4: UniqueViolation en el INSERT de revertir -> saltado, no excepcion cruda."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
        patchs=[(202, {"submissionId": "r5-l4", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        lid, cid = _semilla_reversion(conn)
        conn.execute(
            "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, es_reversa, reversa_de)"
            " VALUES (%s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
            " true, 'pendiente', true, %s)",
            (lid, cid),
        )
        _cero_abiertos(monkeypatch)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert (res.estado, res.motivo) == ("saltado", "listing_con_cambio_abierto")
        assert res.id_reversa is None
        assert red.n_patch == 0
        assert len(conn.execute("SELECT id FROM precio_cambio").fetchall()) == 2
