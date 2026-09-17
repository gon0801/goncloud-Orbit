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
    return os.environ["ORBIT_TEST_DSN"]


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


def _goal_live(conn, listing, *, platform="amazon_mx") -> int:
    return conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
        " valid_from, creado_por, go_literal)"
        " VALUES (%s, %s, 0.30, 'live', '2026-09-01', 'tdd', 'GO-TDD') RETURNING id",
        (listing, platform),
    ).fetchone()[0]


def _decision(
    conn, listing, *, platform="amazon_mx", resultado="subir", aplicado=Decimal("110.00")
) -> int:
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency)"
        " VALUES (%s, %s, %s, 'tdd', 'live', 0.30, 0.25,"
        " 100.00, 'MXN', 110.00, 'MXN', %s, 'MXN',"
        " 10.00, 'MXN', 20.00, 'MXN', 12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN')"
        " RETURNING id",
        (listing, platform, resultado, aplicado),
    ).fetchone()[0]


def _cambio_enviado(conn, decision, listing, *, antes="100.00", despues="110.00") -> int:
    cid = conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at)"
        " VALUES (%s, %s, 'amazon_mx', %s, 'MXN', %s, 'MXN', true, 'pendiente', %s)"
        " RETURNING id",
        (decision, listing, antes, despues, ANTES),
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


def _ofertas_body(precio, moneda="MXN", seller=PROPIO):
    return {
        "payload": {
            "ASIN": ASIN,
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
            return httpx.Response(status, json=body)
        if request.method == "GET" and "offers" in path:
            self.n_get += 1
            status, body = self.gets_ofertas.pop(0)
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


def _cambio_cerrado(conn, decision, listing, *, antes="100.00", despues="110.00") -> int:
    """Original cerrado (`confirmado`): la reversa solo nace sin abierto
    (indice `precio_cambio_abierto_unico`)."""
    cid = _cambio_enviado(conn, decision, listing, antes=antes, despues=despues)
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
    assert red.n_get == 2 and red.n_patch == 0


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
    """Orden S5: el PATCH solo procede si la fila ya commiteo."""
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        red = _RedFalsa(
            gets_ofertas=[(200, _ofertas_body(110.0)), (200, _ofertas_body(100.0))],
            gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
            patchs=[(202, {"submissionId": "rev-1", "status": "ACCEPTED"})],
            exige_fila=lambda: (
                conn.execute(
                    "SELECT count(*) FROM precio_cambio WHERE es_reversa AND estado = 'pendiente'"
                ).fetchone()[0]
                > 0
            ),
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


def test_revertir_nunca_loguea_el_cuerpo_y_sanea_el_ack(caplog):
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
        assert "110.00" not in caplog.text and SECRETO not in caplog.text
        ack = conn.execute(
            "SELECT ack FROM precio_cambio WHERE id = %s", (res.id_reversa,)
        ).fetchone()[0]
        assert SECRETO not in json.dumps(ack) and "rev-9" in json.dumps(ack)


# ------------------------------------------------ cambiar_precio


def _semilla_cambio(conn, *, asin=ASIN, sku=SKU, desde="100.00", hasta="110.00"):
    """listing + goal + decision subir live; devuelve (lid, dec)."""
    prod = _producto(conn, sku=f"PR-{sku}")
    lid = _listing(conn, prod, asin=asin, sku=sku)
    _goal_live(conn, lid)
    return lid, _decision(conn, lid, aplicado=Decimal(hasta))


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


def test_cambiar_200_sin_accepted_es_error():
    """El 2xx con estado explicito distinto de ACCEPTED no es envio."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(200, {"submissionId": "c-9", "status": "ERROR"})],
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
        lid, original = _semilla_cambio(conn, asin="B0C6", sku="SKU-C6")
        _sellar_enviado_sql(conn, _enviado_abierto(conn, original, lid))
        with rol(conn):
            conn.execute(
                "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
                " WHERE id = %s",
                (original,),
            )
            rev = conn.execute(
                "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at, es_reversa, reversa_de)"
                " VALUES (%s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN', true,"
                " 'pendiente', %s, true, %s) RETURNING id",
                (lid, ANTES, original),
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


def test_tool_go_sin_banderas_aborta(monkeypatch):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar):
            tool.main(
                ["--cambio-id", "1", "--acepto-mutacion-real"],
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
