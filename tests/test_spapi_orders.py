"""Tests SP-API 01 A.2 — ingesta Orders 2026-01-01.

(a) UNITARIOS: parseo de resumenes (acta 0.1), ventana, paginacion con las
dos guardas TRASPASO-1. Cero red real (httpx.MockTransport), cero DB.
(b) INTEGRACION: migracion 0030 (unicidad, trigger, grants) e ingesta
punta a punta idempotente en Postgres de test (skipea sin ORBIT_TEST_DSN).
"""

from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.spapi import orders
from app.spapi.client import SpapiClient

ROOT = Path(__file__).resolve().parents[1]
ORDEN = ("0001_initial.sql", "0030_spapi_orders.sql", "0031_spapi_orders_bitemporal.sql")

AHORA = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
CRED = {
    "lwa_app_id": "id-orders",
    "lwa_client_secret": "secreto-orders",
    "refresh_token": "refresh-orders",
}

_TOKEN_LWA = "tk-spapi-orders-fixture-lwa"

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def _resumen(order_id="MX-1", **mas):
    base = {
        "orderId": order_id,
        "createdTime": "2026-09-08T10:00:00Z",
        "lastUpdatedTime": "2026-09-08T11:00:00Z",
        "salesChannel": "Amazon.com.mx",
    }
    base.update(mas)
    return base


def test_parsea_resumen_completo_2026():
    orden = orders.parsear_orden(
        _resumen(
            orderStatus="Shipped",
            fulfillmentChannel="AFN",
            orderTotal={"Amount": 599.0, "CurrencyCode": "MXN"},
            orderItems=[{"x": 1}, {"x": 2}],
        )
    )
    assert orden.amazon_order_id == "MX-1"
    assert orden.purchase_date == datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
    assert orden.last_updated_time == datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC)
    assert orden.order_status == "Shipped"
    assert orden.fulfillment_channel == "AFN"
    assert orden.sales_channel == "Amazon.com.mx"
    assert orden.total_amount == Decimal("599.0000")
    assert orden.total_currency == "MXN"
    assert orden.number_of_items == 2


def test_parsea_minimo_con_nulos():
    orden = orders.parsear_orden(
        {
            "orderId": "MX-2",
            "createdTime": "2026-09-08T10:00:00Z",
            "lastUpdatedTime": "2026-09-08T11:00:00Z",
        }
    )
    assert orden.order_status is None
    assert orden.fulfillment_channel is None
    assert orden.sales_channel is None
    assert orden.total_amount is None
    assert orden.total_currency is None
    assert orden.number_of_items is None


def test_parsea_secciones_fulfillment_y_proceeds():
    # Claves reales del acta A.2b/sonda.md.
    orden = orders.parsear_orden(
        {
            "orderId": "MX-20",
            "createdTime": "2026-09-08T10:00:00Z",
            "lastUpdatedTime": "2026-09-08T11:00:00Z",
            "salesChannel": "Amazon.com.mx",
            "fulfillment": {
                "deliverByWindow": {"start": "2026-09-09T00:00:00Z"},
                "fulfilledBy": "AFN",
                "fulfillmentServiceLevel": "Standard",
                "fulfillmentStatus": "Shipped",
                "shipByWindow": {},
            },
            "proceeds": {
                "breakdowns": [{"tipo": "Principal"}],
                "grandTotal": {"amount": 749.0, "currencyCode": "MXN"},
            },
            "programs": [],
        }
    )
    # Revision PR #240: dos estados, dos columnas; la seccion ya no pisa
    # el ciclo.
    assert orden.order_status is None
    assert orden.fulfillment_status == "Shipped"
    assert orden.fulfillment_channel == "AFN"
    assert orden.total_amount == Decimal("749.0000")
    assert orden.total_currency == "MXN"


def test_estados_de_ciclo_y_envio_no_se_mezclan():
    # Revision PR #240: order_status SOLO del ciclo (clave plana) y
    # fulfillment_status SOLO del envio (seccion), aunque vengan los dos.
    orden = orders.parsear_orden(
        _resumen(
            orderStatus="Plano",
            fulfillment={"fulfillmentStatus": "Seccion", "fulfilledBy": "MFN"},
            orderTotal={"Amount": 1.0, "CurrencyCode": "MXN"},
            proceeds={"grandTotal": {"amount": 2.0, "currencyCode": "MXN"}},
        )
    )
    assert orden.order_status == "Plano"
    assert orden.fulfillment_status == "Seccion"
    assert orden.fulfillment_channel == "MFN"
    assert orden.total_amount == Decimal("2.0000")


def test_recipient_y_buyer_se_ignoran_aunque_vengan():
    from app.spapi.client import sanear

    crudo = _resumen(
        recipient={"name": "Nadie Debeverse"},
        buyer={"buyerEmail": "nadie@example.com"},
        ShippingAddress={"AddressLine1": "Calle 123"},
    )
    limpio = sanear(crudo, "orders")
    # Se afirma sobre el objeto saneado (valores fuera), no sobre campos
    # que pasan con o sin filtro.
    texto_limpio = json.dumps(limpio, sort_keys=True)
    assert "Nadie Debeverse" not in texto_limpio
    assert "nadie@example.com" not in texto_limpio
    assert "Calle 123" not in texto_limpio
    assert "recipient" not in limpio
    orden = orders.parsear_orden(crudo)
    assert orden.amazon_order_id == "MX-1"


def test_parsea_alias_v0():
    orden = orders.parsear_orden(
        {
            "AmazonOrderId": "MX-3",
            "PurchaseDate": "2026-09-08T10:00:00Z",
            "LastUpdateDate": "2026-09-08T11:00:00Z",
            "OrderStatus": "Pending",
            "FulfillmentChannel": "MFN",
            "SalesChannel": "Amazon.com",
            "OrderTotal": {"Amount": "10.50", "CurrencyCode": "USD"},
        }
    )
    assert orden.amazon_order_id == "MX-3"
    assert orden.total_amount == Decimal("10.5000")
    assert orden.total_currency == "USD"


def test_omite_sin_identidad_o_tiempo():
    for cuerpo in (
        {"lastUpdatedTime": "2026-09-08T11:00:00Z"},
        {"orderId": "MX-x"},
        {"orderId": "MX-x", "lastUpdatedTime": "no-es-fecha"},
        "no-es-dict",
    ):
        with pytest.raises(orders.OrdenOmitida):
            orders.parsear_orden(cuerpo)


def test_omite_tiempo_incoherente():
    with pytest.raises(orders.OrdenOmitida, match="tiempo_incoherente"):
        orders.parsear_orden(
            _resumen(
                createdTime="2026-09-08T12:00:00Z",
                lastUpdatedTime="2026-09-08T11:00:00Z",
            )
        )


def test_total_incoherente_da_nulos_sin_omitir():
    for total in (
        {"Amount": 5.0},
        {"Amount": 5.0, "CurrencyCode": "EUR"},
        {"Amount": "basura", "CurrencyCode": "MXN"},
        "no-es-dict",
    ):
        orden = orders.parsear_orden(_resumen(orderTotal=total))
        assert (orden.total_amount, orden.total_currency) == (None, None)


def test_items_varias_formas():
    assert orders.parsear_orden(_resumen(orderItems=3)).number_of_items == 3
    assert orders.parsear_orden(_resumen(orderItems={"count": 4})).number_of_items == 4
    assert orders.parsear_orden(_resumen(orderItems=-1)).number_of_items is None
    assert orders.parsear_orden(_resumen()).number_of_items is None


def test_ventana_desde_ignora_maximo():
    params = orders.parametros_ventana(
        marketplace_id="A1AM78C64UM0Y8",
        ultimo_observado=datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC),
        ahora=AHORA,
        desde=datetime(2026, 8, 10).date(),
    )
    assert params["lastUpdatedAfter"] == "2026-08-10T00:00:00Z"
    assert "createdAfter" not in params


def test_ventana_primera_y_segunda_corrida():
    mid = "A1AM78C64UM0Y8"
    primera = orders.parametros_ventana(marketplace_id=mid, ultimo_observado=None, ahora=AHORA)
    assert primera["createdAfter"] == "2026-08-10T12:00:00Z"
    assert "lastUpdatedAfter" not in primera
    segunda = orders.parametros_ventana(
        marketplace_id=mid,
        ultimo_observado=datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC),
        ahora=AHORA,
    )
    assert segunda["lastUpdatedAfter"] == "2026-09-07T11:00:00Z"
    assert "createdAfter" not in segunda


def test_pide_secciones_en_cada_peticion():
    llamadas: list = []
    cliente = _cliente([{"cuerpo": {"payload": {"orders": []}}}], llamadas=llamadas)
    params = orders.parametros_ventana(marketplace_id="X", ultimo_observado=None, ahora=AHORA)
    orders.recorrer_ordenes(cliente, params, max_paginas=2)
    assert llamadas, "sin HTTP no hay nada que afirmar"
    for request in llamadas:
        assert request.url.params["includedData"] == "FULFILLMENT,PROCEEDS"


def test_jamas_pide_buyer_ni_recipient():
    llamadas: list = []
    paginas = [
        _pagina([_resumen("A-1")], token="T9"),
        {
            "token_entrada": "T9",
            "cuerpo": {"payload": {"orders": [_resumen("A-2")]}},
        },
    ]
    cliente = _cliente(paginas, llamadas=llamadas)
    orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"}, max_paginas=3)
    assert len(llamadas) == 2
    for request in llamadas:
        for valor in request.url.params.values():
            assert "BUYER" not in valor
            assert "RECIPIENT" not in valor


def _cliente(paginas, *, llamadas=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        if llamadas is not None:
            llamadas.append(request)
        token = request.url.params.get("paginationToken")
        if token is None:
            cuerpo = paginas[0]
        else:
            cuerpo = next((p for p in paginas if p.get("token_entrada") == token), None)
            if cuerpo is None:
                return httpx.Response(200, json={"payload": {"orders": []}})
        return httpx.Response(200, json=cuerpo["cuerpo"])

    return SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )


def _pagina(ordenes, token=None):
    cuerpo: dict = {"payload": {"orders": ordenes}}
    if token is not None:
        cuerpo["payload"]["pagination"] = {"nextToken": token}
    return {"cuerpo": cuerpo}


def test_recorre_varias_paginas():
    paginas = [
        _pagina([_resumen("A-1")], token="T1"),
        {"token_entrada": "T1", "cuerpo": {"payload": {"orders": [_resumen("A-2")]}}},
    ]
    cliente = _cliente(paginas)
    crudas, info = orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"}, max_paginas=5)
    assert [o["orderId"] for o in crudas] == ["A-1", "A-2"]
    assert info["paginas"] == 2
    assert info["aviso_paginacion"] is None


def test_token_repetido_para_y_marca():
    paginas = [
        _pagina([_resumen("A-1")], token="T"),
        {
            "token_entrada": "T",
            "cuerpo": {"payload": {"orders": [_resumen("A-2")], "pagination": {"nextToken": "T"}}},
        },
    ]
    cliente = _cliente(paginas)
    crudas, info = orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"}, max_paginas=5)
    assert info["aviso_paginacion"] == "next_token_repetido"
    assert info["paginas"] == 2


def test_pagina_vacia_con_token_para_y_marca():
    paginas = [
        _pagina([_resumen("A-1")], token="T2"),
        {
            "token_entrada": "T2",
            "cuerpo": {"payload": {"orders": [], "pagination": {"nextToken": "T3"}}},
        },
    ]
    cliente = _cliente(paginas)
    crudas, info = orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"}, max_paginas=5)
    assert info["aviso_paginacion"] == "pagina_vacia_con_token"
    assert len(crudas) == 1


def test_tope_de_paginas_para_y_marca():
    paginas = [
        _pagina([_resumen("A-1")], token="T9"),
        {
            "token_entrada": "T9",
            "cuerpo": {
                "payload": {"orders": [_resumen("A-2")], "pagination": {"nextToken": "T10"}}
            },
        },
    ]
    cliente = _cliente(paginas)
    crudas, info = orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"}, max_paginas=1)
    assert info["aviso_paginacion"] == "limite_max_paginas"
    assert info["paginas"] == 1
    assert [o["orderId"] for o in crudas] == ["A-1"]


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"payload": {"pagination": {"nextToken": "X"}}},
        {"payload": [1, 2]},
        {"payload": {"orders": {"no": "lista"}}},
        [{"orderId": "suelta"}],
    ],
)
def test_contrato_inesperado_es_fatal(cuerpo):
    cliente = _cliente([{"cuerpo": cuerpo}])
    with pytest.raises(orders.IngestaOrdersError, match="contrato inesperado"):
        orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"})


def test_lista_vacia_con_clave_es_valida():
    cliente = _cliente([{"cuerpo": {"payload": {"orders": []}}}])
    crudas, info = orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"})
    assert crudas == []
    assert info["paginas"] == 1
    assert info["aviso_paginacion"] is None


def test_limitador_20_paginas_y_la_21_espera():
    dormidas: list = []
    reloj = {"v": 5000.0}
    paginas = []
    for i in range(21):
        cuerpo: dict = {"payload": {"orders": [{"orderId": f"B-{i}"}]}}
        if i < 20:
            cuerpo["payload"]["pagination"] = {"nextToken": f"Q{i}"}
        entrada: dict = {"cuerpo": cuerpo}
        if i > 0:
            entrada["token_entrada"] = f"Q{i - 1}"
        paginas.append(entrada)

    llamadas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        llamadas.append(request)
        token = request.url.params.get("paginationToken")
        if token is None:
            cuerpo = paginas[0]
        else:
            cuerpo = next((p for p in paginas if p.get("token_entrada") == token), None)
            assert cuerpo is not None
        return httpx.Response(200, json=cuerpo["cuerpo"])

    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=dormidas.append,
        clock=lambda: reloj["v"],
    )
    crudas, info = orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"}, max_paginas=25)
    assert len(crudas) == 21
    assert len([r for r in llamadas if r.url.host != "api.amazon.com"]) == 21
    assert len(dormidas) == 1
    assert dormidas[0] == pytest.approx(1 / 0.0056, abs=0.01)


def test_status_no_200_es_fatal():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        return httpx.Response(500, json={})

    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with pytest.raises(orders.IngestaOrdersError, match="status=500"):
        orders.recorrer_ordenes(cliente, {"marketplaceIds": "X"})


# ---------------------------------------------------------------------------
# (b) INTEGRACION
# ---------------------------------------------------------------------------


@contextmanager
def db_orders(prefijo: str = "orbit_spapi_a2"):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=False)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        conn.commit()
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@_skip_db
def test_migracion_unicidad_trigger_y_grants():
    import psycopg

    with db_orders() as conn:
        fila = (
            "MX-1",
            "amazon_mx",
            "A1AM78C64UM0Y8",
            datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC),
            "Pending",
            "Shipped",
            "AFN",
            "Amazon.com.mx",
            Decimal("599.0000"),
            "MXN",
            2,
            "2026-01-01",
            datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
            None,
        )
        conn.execute(
            "INSERT INTO spapi_order_observation"
            " (amazon_order_id, platform, marketplace_id, purchase_date,"
            " last_updated_time, order_status, fulfillment_status,"
            " fulfillment_channel, sales_channel,"
            " order_total_amount, order_total_currency, number_of_items,"
            " api_version, observed_at, ingest_run_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            fila,
        )
        # Revision #7: la primera fila se commitea ANTES del duplicado;
        # sin ese commit, el rollback de abajo la borraba y el insert en
        # otra plataforma entraba contra tabla vacia (no demostraba que
        # platform sea parte de la clave).
        conn.commit()
        # Unicidad (plataforma, orden, actualizacion): el duplicado truena.
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO spapi_order_observation"
                " (amazon_order_id, platform, marketplace_id,"
                " last_updated_time, observed_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    "MX-1",
                    "amazon_mx",
                    "A1AM78C64UM0Y8",
                    datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC),
                    datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
                ),
            )
        conn.rollback()
        # Misma orden y tiempo en otra plataforma: fila distinta, si entra
        # (contra la fila commiteada: esto si prueba la clave).
        conn.execute(
            "INSERT INTO spapi_order_observation"
            " (amazon_order_id, platform, marketplace_id,"
            " last_updated_time, observed_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (
                "MX-1",
                "amazon_us",
                "ATVPDKIKX0DER",
                datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC),
                datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
            ),
        )
        conn.commit()
        # Trigger: compra posterior a la actualizacion truena.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO spapi_order_observation"
                " (amazon_order_id, platform, marketplace_id, purchase_date,"
                " last_updated_time, observed_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    "MX-2",
                    "amazon_mx",
                    "A1AM78C64UM0Y8",
                    datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC),
                    datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC),
                    datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
                ),
            )
        conn.rollback()
        # Append-only real: UPDATE y DELETE truenan aunque el rol pudiera
        # (prohibir_mutacion levanta restrict_violation, patron 0001).
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(
                "UPDATE spapi_order_observation SET sales_channel = 'x'"
                " WHERE amazon_order_id = 'MX-1'"
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM spapi_order_observation WHERE amazon_order_id = 'MX-1'")
        conn.rollback()
        # Grants: la tabla es legible y la secuencia usable.
        assert conn.execute(
            "SELECT has_table_privilege('app_read', 'spapi_order_observation', 'SELECT')"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_order_observation', 'INSERT')"
        ).fetchone()[0]
        # Permisos negativos: lectura no escribe, ingesta no muta historia.
        assert not conn.execute(
            "SELECT has_table_privilege('app_read', 'spapi_order_observation', 'INSERT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_decide', 'spapi_order_observation', 'INSERT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_order_observation', 'UPDATE')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_order_observation', 'DELETE')"
        ).fetchone()[0]


@_skip_db
def test_reversa_0031():
    import psycopg

    reversa = (ROOT / "migrations" / "0031_reversa_spapi_orders_bitemporal.sql").read_text(
        encoding="utf-8"
    )
    t_upd = datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC)
    with db_orders() as conn:
        # Sin re-observaciones: aplica limpio, vista muerta, tripleta de vuelta
        # (cualquier observed repetido truena).
        conn.execute(reversa)
        assert (
            conn.execute(
                "SELECT count(*) FROM pg_views WHERE viewname = 'v_spapi_order_ultima'"
            ).fetchone()[0]
            == 0
        )
        semilla = ("MX-1", "amazon_mx", "A1AM78C64UM0Y8", t_upd)
        conn.execute(
            "INSERT INTO spapi_order_observation"
            " (amazon_order_id, platform, marketplace_id,"
            " last_updated_time, observed_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            semilla + (datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),),
        )
        conn.commit()
        for obs in (
            datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 9, 13, 0, 0, tzinfo=UTC),
        ):
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO spapi_order_observation"
                    " (amazon_order_id, platform, marketplace_id,"
                    " last_updated_time, observed_at)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    semilla + (obs,),
                )
            conn.rollback()
    with db_orders() as conn:
        # Con re-observaciones: la guarda aborta sin escribir nada.
        for obs in (
            datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 9, 13, 0, 0, tzinfo=UTC),
        ):
            conn.execute(
                "INSERT INTO spapi_order_observation"
                " (amazon_order_id, platform, marketplace_id,"
                " last_updated_time, observed_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                ("MX-1", "amazon_mx", "A1AM78C64UM0Y8", t_upd, obs),
            )
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="reversa 0031"):
            conn.execute(reversa)
        conn.rollback()


@_skip_db
def test_reobservacion_bitemporal_y_vista():
    import psycopg

    t_upd = datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 9, 9, 13, 0, 0, tzinfo=UTC)
    with db_orders() as conn:
        for obs, estado in ((t1, None), (t2, "Shipped")):
            conn.execute(
                "INSERT INTO spapi_order_observation"
                " (amazon_order_id, platform, marketplace_id,"
                " last_updated_time, order_status, observed_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                ("MX-9", "amazon_mx", "A1AM78C64UM0Y8", t_upd, estado, obs),
            )
        conn.commit()
        # Misma cuádruple completa: sí truena.
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO spapi_order_observation"
                " (amazon_order_id, platform, marketplace_id,"
                " last_updated_time, observed_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                ("MX-9", "amazon_mx", "A1AM78C64UM0Y8", t_upd, t2),
            )
        conn.rollback()
        fila = conn.execute(
            "SELECT order_status, observed_at FROM v_spapi_order_ultima"
            " WHERE platform = 'amazon_mx' AND amazon_order_id = 'MX-9'"
        ).fetchall()
        assert len(fila) == 1
        assert fila[0][0] == "Shipped"
        assert fila[0][1] == t2
        assert conn.execute(
            "SELECT has_table_privilege('app_read', 'v_spapi_order_ultima', 'SELECT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_decide', 'v_spapi_order_ultima', 'INSERT')"
        ).fetchone()[0]


def _handler_fixture(paginas, llamadas):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        llamadas.append(request)
        token = request.url.params.get("paginationToken")
        if token is None:
            cuerpo = paginas[0]
        else:
            cuerpo = next(
                (p for p in paginas if p.get("token_entrada") == token),
                {"cuerpo": {"payload": {"orders": []}}},
            )
        return httpx.Response(200, json=cuerpo["cuerpo"])

    return handler


def _fixture_dos_paginas():
    return [
        {
            "cuerpo": {
                "payload": {
                    "orders": [
                        _resumen(
                            "MX-10",
                            createdTime="2026-09-07T10:00:00Z",
                            lastUpdatedTime="2026-09-07T11:00:00Z",
                            orderStatus="Shipped",
                            orderTotal={"Amount": 100.0, "CurrencyCode": "MXN"},
                            orderItems=[{}, {}],
                        ),
                        {
                            "orderId": "MX-11",
                            "createdTime": "2026-09-07T12:00:00Z",
                            "lastUpdatedTime": "2026-09-07T13:00:00Z",
                            "salesChannel": "Amazon.com.mx",
                        },
                        {"orderId": "MX-sin-tiempo"},
                    ],
                    "pagination": {"nextToken": "P2"},
                }
            }
        },
        {
            "token_entrada": "P2",
            "cuerpo": {
                "payload": {
                    "orders": [
                        _resumen(
                            "MX-12",
                            createdTime="2026-09-08T10:00:00Z",
                            lastUpdatedTime="2026-09-08T11:00:00Z",
                        )
                    ]
                }
            },
        },
    ]


@_skip_db
def test_ingesta_punta_a_punta_idempotente_y_ventana():
    llamadas: list = []
    paginas = _fixture_dos_paginas()
    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(_handler_fixture(paginas, llamadas)),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_orders() as conn:
        primera = orders.ejecutar_ingesta(
            conn, cliente, platform="amazon_mx", ahora=AHORA, max_paginas=5
        )
        assert primera.ok
        assert primera.escritas == 3
        assert primera.paginas == 2
        assert primera.aviso_paginacion is None
        # Primera corrida usa createdAfter de 30 dias.
        assert llamadas[0].url.params.get("createdAfter") == "2026-08-10T12:00:00Z"
        assert "lastUpdatedAfter" not in llamadas[0].url.params
        run = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason FROM ingest_run WHERE id = %s",
            (primera.run_id,),
        ).fetchone()
        assert run[0] is True
        assert run[1] == 3
        assert run[2] == 1
        assert run[3] == "1x sin_identidad"
        assert conn.execute("SELECT count(*) FROM spapi_order_observation").fetchone()[0] == 3
        total = conn.execute(
            "SELECT order_total_amount, order_total_currency FROM spapi_order_observation"
            " WHERE amazon_order_id = 'MX-10'"
        ).fetchone()
        assert (total[0], total[1]) == (Decimal("100.0000"), "MXN")

        # Re-corrida con el mismo fixture: idempotente, duplicadas al skip.
        llamadas.clear()
        segunda = orders.ejecutar_ingesta(
            conn, cliente, platform="amazon_mx", ahora=AHORA, max_paginas=5
        )
        assert segunda.escritas == 0
        assert conn.execute("SELECT count(*) FROM spapi_order_observation").fetchone()[0] == 3
        run2 = conn.execute(
            "SELECT rows_written, rows_skipped, skip_reason FROM ingest_run WHERE id = %s",
            (segunda.run_id,),
        ).fetchone()
        assert run2[0] == 0
        assert "duplicada" in (run2[2] or "")
        # Segunda corrida usa lastUpdatedAfter = max - 1 dia de solape.
        params2 = llamadas[0].url.params
        assert params2.get("lastUpdatedAfter") == "2026-09-07T11:00:00Z"
        assert "createdAfter" not in params2


@_skip_db
def test_dos_corridas_reobservan_y_vista_muestra_ultima():
    pelada = {
        "orderId": "MX-30",
        "createdTime": "2026-09-07T10:00:00Z",
        "lastUpdatedTime": "2026-09-07T11:00:00Z",
        "salesChannel": "Amazon.com.mx",
    }
    completa = dict(
        pelada,
        fulfillment={"fulfilledBy": "AFN", "fulfillmentStatus": "Shipped"},
        proceeds={"grandTotal": {"amount": 250.0, "currencyCode": "MXN"}},
    )

    def handler_con(paginas):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
            return httpx.Response(200, json=paginas)

        return handler

    with db_orders() as conn:
        for ahora, cuerpo in (
            (AHORA, {"payload": {"orders": [pelada]}}),
            (
                AHORA + timedelta(hours=1),
                {"payload": {"orders": [completa]}},
            ),
        ):
            cliente = SpapiClient(
                credentials=CRED,
                transport=httpx.MockTransport(handler_con(cuerpo)),
                sleep=lambda _s: None,
                clock=lambda: 1000.0,
            )
            resultado = orders.ejecutar_ingesta(
                conn, cliente, platform="amazon_mx", ahora=ahora, max_paginas=5
            )
            assert resultado.ok
            assert resultado.escritas == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM spapi_order_observation WHERE amazon_order_id = 'MX-30'"
            ).fetchone()[0]
            == 2
        )
        vista = conn.execute(
            "SELECT order_status, fulfillment_status, fulfillment_channel,"
            " order_total_amount, order_total_currency"
            " FROM v_spapi_order_ultima WHERE amazon_order_id = 'MX-30'"
        ).fetchone()
        # La segunda corrida trae seccion sin ciclo: el envio queda en su
        # columna y el ciclo en NULL (nunca mezclados).
        assert vista == (None, "Shipped", "AFN", Decimal("250.0000"), "MXN")


@_skip_db
def test_desde_fuerza_backfill_sobre_maximo():
    llamadas: list = []
    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(
            _handler_fixture([{"cuerpo": {"payload": {"orders": []}}}], llamadas)
        ),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_orders() as conn:
        conn.execute(
            "INSERT INTO spapi_order_observation"
            " (amazon_order_id, platform, marketplace_id,"
            " last_updated_time, observed_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (
                "MX-VIEJA",
                "amazon_mx",
                "A1AM78C64UM0Y8",
                datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC),
                AHORA,
            ),
        )
        conn.commit()
        orders.ejecutar_ingesta(
            conn,
            cliente,
            platform="amazon_mx",
            ahora=AHORA,
            desde=datetime(2026, 8, 10).date(),
        )
        assert llamadas[0].url.params["lastUpdatedAfter"] == "2026-08-10T00:00:00Z"


def test_desde_invalido_es_error_de_uso():
    with pytest.raises(SystemExit):
        orders.main(["--platform", "amazon_mx", "--desde", "no-es-fecha"])


@_skip_db
def test_paginacion_incompleta_sella_ok_false_con_lo_escrito():
    # Revision PR #240: el aviso no es exito; las filas traidas se
    # conservan pero el run marca el hueco para reparar con --desde.
    llamadas: list = []
    paginas = [
        {
            "cuerpo": {
                "payload": {
                    "orders": [_resumen("MX-40")],
                    "pagination": {"nextToken": "Q"},
                }
            }
        },
        {
            "token_entrada": "Q",
            "cuerpo": {
                "payload": {
                    "orders": [_resumen("MX-41")],
                    "pagination": {"nextToken": "Q"},
                }
            },
        },
    ]
    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(_handler_fixture(paginas, llamadas)),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_orders() as conn:
        resultado = orders.ejecutar_ingesta(
            conn, cliente, platform="amazon_mx", ahora=AHORA, max_paginas=5
        )
        assert resultado.ok is False
        assert resultado.escritas == 2
        assert resultado.aviso_paginacion == "next_token_repetido"
        assert conn.execute("SELECT count(*) FROM spapi_order_observation").fetchone()[0] == 2
        run = conn.execute(
            "SELECT ok, rows_written, skip_reason FROM ingest_run WHERE id = %s",
            (resultado.run_id,),
        ).fetchone()
        assert run[0] is False
        assert run[1] == 2
        assert run[2] == "paginacion_incompleta:next_token_repetido"


@_skip_db
def test_fallo_http_sella_ok_false():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        return httpx.Response(500, json={})

    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_orders() as conn:
        with pytest.raises(orders.IngestaOrdersError):
            orders.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        run = conn.execute(
            "SELECT ok, rows_written FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run[0] is False
        assert run[1] == 0


def test_cli_sin_dsn_falla_cerrado(monkeypatch):
    from app import cli

    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert cli.main(["ingest", "spapi_orders", "--platform", "amazon_mx"]) == 2


def test_cli_registra_pipeline():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "ingest", "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert "spapi_orders" in proc.stdout
