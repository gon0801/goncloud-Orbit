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
    FormaParcheSinSellar,
    PrecioVivoAusente,
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

    def __init__(self, *, gets_ofertas=(), gets_competitivos=(), patchs=(), exige_fila=None):
        self.gets_ofertas = list(gets_ofertas)
        self.gets_competitivos = list(gets_competitivos)
        self.patchs = list(patchs)
        self.exige_fila = exige_fila
        self.n_patch = 0
        self.n_get = 0
        self.tokens = 0

    def _handler(self, request):
        path = request.url.path
        if path == "/auth/o2/token":
            self.tokens += 1
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
