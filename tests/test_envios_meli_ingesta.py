"""ENVIOS 01: ingesta solo-GET de Mercado Libre contra base real y MeLi simulado."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from helpers.pg import db_desde_template
from psycopg.rows import dict_row
from test_envios_estado import envio_de_prueba
from test_fabrica_f2 import _skip_db
from test_schema import _test_dsn

from app import cli
from app.envios import meli_ingesta
from app.envios.meli import creada_el, envio_desde_meli, envio_ref_de_orden
from app.envios.modelo import Articulo, PaqueteObservado, huella
from app.envios.store import guardar
from app.meli.client import ClienteMeli, MeliCredentials, ReputacionError

AHORA = datetime(2026, 11, 25, 2, 25, tzinfo=UTC)
HACE_35_DIAS = datetime(2026, 10, 21, 2, 25, tzinfo=UTC)
RECIENTE = "2026-11-20T10:00:00.000-06:00"
VIEJA = "2026-10-21T10:00:00.000-06:00"

_DIRECCION = {"address_line": "CENTINELA_CALLE", "zip_code": "53240"}
_PII = {
    "buyer": {"nickname": "CENTINELA_COMPRADOR"},
    "receiver_address": _DIRECCION,
    "sender_address": _DIRECCION,
    "receiver_id": "CENTINELA_RECEPTOR",
    "sender_id": "CENTINELA_REMITENTE",
    "destination": _DIRECCION,
    "origin": _DIRECCION,
    "comments": "CENTINELA_COMENTARIO",
}


def _orden(orden_id: int, envio_id: int | None, *, creada=RECIENTE, pack=None, **mas) -> dict:
    return {
        "id": orden_id,
        "pack_id": pack,
        "date_created": creada,
        "shipping": {"id": envio_id},
        "order_items": [
            {
                "quantity": 1,
                "item": {"id": f"MLM{orden_id}", "seller_sku": f"SKU-{orden_id}", "title": "Pieza"},
            }
        ],
        **_PII,
        **mas,
    }


def _envio(envio_id: int, status: str | None = "shipped", **mas) -> dict:
    return {
        "id": envio_id,
        "logistic_type": "xd_drop_off",
        "status": status,
        "substatus": None,
        "tracking_number": f"GUIA-{envio_id}",
        "tracking_method": "Agencias Mercado Libre",
        "last_updated": "2026-11-24T11:00:00.123-04:00",
        "status_history": {"date_shipped": "2026-11-21T09:00:00.456-06:00"},
        "shipping_option": {
            "estimated_delivery_time": {
                "date": "2026-11-27T00:00:00.000-06:00",
                "offset": {"date": "2026-11-30T00:00:00.000-06:00"},
            },
            "estimated_handling_limit": None,
        },
        **_PII,
        **mas,
    }


def _responde(status: int, cuerpo: dict) -> httpx.Response:
    # Con escapes ASCII, como puede mandarlo la API: admite sustitutos sueltos.
    return httpx.Response(status, content=json.dumps(cuerpo).encode("ascii"))


class MeliFalso:
    """api.mercadolibre.com en memoria; un envio puede ser un cuerpo o un status HTTP."""

    def __init__(self, paginas: list[list] | None = None, envios: dict | None = None) -> None:
        self.paginas = paginas or []
        self.envios = envios or {}
        self.busquedas: list[dict] = []
        self.leidos: list[str] = []
        self.metodos: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.metodos.add(request.method)
        ruta = request.url.path
        if ruta == "/users/me":
            return httpx.Response(200, json={"id": 111, "nickname": "CENTINELA_VENDEDOR"})
        if ruta == "/orders/search":
            params = dict(request.url.params)
            self.busquedas.append(params)
            todas = [orden for pagina in self.paginas for orden in pagina]
            inicio = 0
            lote: list = []
            for pagina in self.paginas:
                if inicio == int(params["offset"]):
                    lote = pagina
                    break
                inicio += len(pagina)
            paging = {"total": len(todas), "offset": int(params["offset"]), "limit": 50}
            return _responde(200, {"paging": paging, "results": lote})
        assert ruta.startswith("/shipments/"), ruta
        ref = ruta.removeprefix("/shipments/")
        self.leidos.append(ref)
        cuerpo = self.envios[ref]
        if isinstance(cuerpo, int):
            return httpx.Response(cuerpo, json={"message": "CENTINELA_ERROR", **_PII})
        return httpx.Response(200, json=cuerpo)


@pytest.fixture
def base():
    """`(conn de inspeccion, dsn)` sobre una base migrada y aislada."""
    with db_desde_template("orbit_envios_meli", base=_test_dsn(), autocommit=True) as par:
        yield par


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    monkeypatch.setattr("app.meli.client.time.sleep", lambda _segundos: None)


def _correr(dsn: str, falso: MeliFalso, *, ahora=AHORA, **mas):
    """Corre como en produccion: rol de ingesta, sin autocommit y con otra zona de sesion."""
    cliente = ClienteMeli(MeliCredentials("tk-prueba"), transport=httpx.MockTransport(falso))
    conn = psycopg.connect(dsn)
    try:
        conn.execute("SET ROLE app_ingest")
        conn.execute("SET TIME ZONE 'America/Mexico_City'")
        conn.commit()
        return meli_ingesta.ejecutar_ingesta(conn, cliente, ahora=ahora, **mas)
    finally:
        conn.close()
        cliente.close()


def _filas(conn, tabla: str = "v_envio_actual", orden: str = "envio_ref") -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(f"SELECT * FROM {tabla} ORDER BY {orden}").fetchall()


def _corridas(conn) -> list[dict]:
    return _filas(conn, "ingest_run", "id")


def _total(conn) -> int:
    return conn.execute("SELECT count(*) FROM envio_observation").fetchone()[0]


def _sembrar(conn, envios) -> None:
    run_id = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('prueba_envios') RETURNING id"
    ).fetchone()[0]
    guardar(conn, envios, observed_at=AHORA - timedelta(hours=4), run_id=run_id)


def _previo(envio_ref: str, estado: str | None, **mas):
    campos = {
        "platform": "meli",
        "envio_ref": envio_ref,
        "pedidos": (f"1{envio_ref}",),
        "logistica_fuente": "xd_drop_off",
        "estado_fuente": estado,
    }
    return envio_de_prueba(**(campos | mas))


def _sin_centinelas(conn, *textos: str) -> None:
    volcado = [
        json.dumps(_filas(conn, tabla, "id"), default=str)
        for tabla in ("envio_observation", "envio_paquete_observation", "ingest_run")
    ]
    for texto in (*volcado, *textos):
        assert "CENTINELA" not in texto
        assert "53240" not in texto


def _tokens(tmp_path, monkeypatch) -> None:
    (tmp_path / "meli_tokens.json").write_text(
        json.dumps({"access_token": "tk-prueba"}), encoding="utf-8"
    )
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        (44000000001, "44000000001"),
        (" 44000000001 ", "44000000001"),
        (None, None),
        (True, None),
        (-1, None),
        (4.4, None),
        ("", None),
        ("123/../users", None),
        ({"id": 1}, None),
    ],
)
def test_envio_ref_de_orden_solo_ids_numericos(crudo, esperado):
    assert envio_ref_de_orden({"shipping": {"id": crudo}}) == esperado


def test_creada_el_exige_zona_y_fecha_legible():
    assert creada_el({"date_created": RECIENTE}) == datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
    for ilegible in (None, 1763740800, "ayer", "2026-11-20T10:00:00", "2026-11-20"):
        assert creada_el({"date_created": ilegible}) is None
    # Legible como texto pero fuera de rango al pasar a UTC.
    assert creada_el({"date_created": "0001-01-01T00:00:00+14:00"}) is None


@_skip_db
def test_corta_paginacion_al_pasar_30_dias_y_agrupa_packs(base):
    conn, dsn = base
    falso = MeliFalso(
        paginas=[
            [
                _orden(1001, 501, pack=9001, creada="2026-11-24T10:00:05.000-06:00"),
                _orden(1002, 501, pack=9001, creada="2026-11-24T10:00:00.000-06:00"),
                _orden(1003, 502),
            ],
            [
                _orden(1004, 503, creada="2026-11-10T10:00:00.000-06:00"),
                _orden(1005, 504, creada=VIEJA),
                _orden(1007, 503, creada=VIEJA),
            ],
            [_orden(1006, 505, creada=VIEJA)],
        ],
        envios={"501": _envio(501), "502": _envio(502), "503": _envio(503)},
    )

    resultado = _correr(dsn, falso)

    assert [(b["seller"], b["sort"], b["limit"], b["offset"]) for b in falso.busquedas] == [
        ("111", "date_desc", "50", "0"),
        ("111", "date_desc", "50", "3"),
    ]
    assert falso.leidos == ["501", "502", "503"]
    assert falso.metodos == {"GET"}
    assert (resultado.ok, resultado.escritos, resultado.omitidos, resultado.aviso) == (
        True,
        3,
        0,
        None,
    )
    filas = _filas(conn)
    assert [(f["envio_ref"], f["pedidos"], f["pack_ref"]) for f in filas] == [
        ("501", ["1001", "1002"], "9001"),
        ("502", ["1003"], None),
        ("503", ["1004", "1007"], None),
    ]
    assert filas[0]["platform"] == "meli"
    assert filas[0]["pedido_creado_el"] == datetime(2026, 11, 24, 16, 0, tzinfo=UTC)
    assert [a["pedido_ref"] for a in filas[0]["articulos"]] == ["1001", "1002"]
    (corrida,) = _corridas(conn)
    assert (corrida["source"], corrida["platform"], corrida["ok"]) == ("envios_meli", "meli", True)
    assert (corrida["rows_written"], corrida["rows_skipped"], corrida["skip_reason"]) == (
        3,
        0,
        None,
    )
    assert corrida["finished_at"] is not None
    assert {f["ingest_run_id"] for f in filas} == {corrida["id"]}
    _sin_centinelas(conn)


@_skip_db
def test_no_relee_cerrados_y_relee_abiertos_mayores_de_30_dias(base):
    conn, dsn = base
    articulos = (Articulo("1603", "SKU-VIEJO", "Arras de boda", 3, "MLM603"),)
    _sembrar(
        conn,
        [
            _previo("601", "delivered"),
            _previo("602", "cancelled"),
            _previo(
                "603",
                "shipped",
                pedidos=("1603", "2603"),
                pack_ref="9603",
                pedido_creado_el=HACE_35_DIAS,
                articulos=articulos,
                paquetes=(PaqueteObservado("603", None, None, "GUIA-603", None, None, None),),
            ),
        ],
    )
    entregado = _envio(
        603, "delivered", status_history={"date_delivered": "2026-11-24T12:00:00.000-06:00"}
    )
    falso = MeliFalso(paginas=[[_orden(1601, 601)]], envios={"603": entregado})

    resultado = _correr(dsn, falso)

    assert falso.leidos == ["603"]
    assert falso.metodos == {"GET"}
    assert (resultado.ok, resultado.escritos, resultado.sin_cambio) == (True, 1, 0)
    fila = _filas(conn)[2]
    assert fila["envio_ref"] == "603"
    assert fila["estado_fuente"] == "delivered"
    assert fila["entregado_el"] == datetime(2026, 11, 24, 18, 0, tzinfo=UTC)
    assert fila["pedidos"] == ["1603", "2603"]
    assert fila["pack_ref"] == "9603"
    assert fila["pedido_creado_el"] == HACE_35_DIAS
    assert fila["articulos"] == [
        {
            "pedido_ref": "1603",
            "sku": "SKU-VIEJO",
            "titulo": "Arras de boda",
            "cantidad": 3,
            "producto_ref": "MLM603",
        }
    ]
    _sin_centinelas(conn)


@_skip_db
def test_devuelto_es_terminal_y_sin_devolucion_sigue_abierto(base):
    conn, dsn = base
    _sembrar(
        conn,
        [
            _previo("701", "not_delivered", devuelto_el=datetime(2026, 11, 22, tzinfo=UTC)),
            _previo("702", "not_delivered"),
            _previo("703", None),
            _previo("704", "estado_nuevo"),
        ],
    )
    devuelto = _envio(
        702, "not_delivered", status_history={"date_returned": "2026-11-24T08:00:00.000-06:00"}
    )
    paginas = [[_orden(1701, 701), _orden(1702, 702)]]
    primera = MeliFalso(
        paginas, {"702": devuelto, "703": _envio(703, None), "704": _envio(704, "estado_nuevo")}
    )

    _correr(dsn, primera)

    assert primera.leidos == ["702", "703", "704"]
    assert _filas(conn)[1]["devuelto_el"] == datetime(2026, 11, 24, 14, 0, tzinfo=UTC)

    segunda = MeliFalso(paginas, {"703": _envio(703, None), "704": _envio(704, "estado_nuevo")})
    resultado = _correr(dsn, segunda, ahora=AHORA + timedelta(hours=4))

    assert segunda.leidos == ["703", "704"]
    assert (resultado.ok, resultado.escritos, resultado.sin_cambio) == (True, 0, 2)


@_skip_db
def test_tope_relectura_90_dias_y_fallo_viejo_no_baja_ok(base):
    conn, dsn = base
    _sembrar(
        conn,
        [
            # Mas de 90 dias: deja de releerse por completo.
            _previo("851", "shipped", pedido_creado_el=AHORA - timedelta(days=91)),
            # En el borde de 90 dias: aun se relee.
            _previo("852", "shipped", pedido_creado_el=AHORA - timedelta(days=90)),
            # Abierto de 45 dias (>30d): si falla su GET, se anota sin bajar ok.
            _previo("853", "shipped", pedido_creado_el=AHORA - timedelta(days=45)),
        ],
    )
    falso = MeliFalso(
        paginas=[[_orden(1854, 854)]],
        envios={
            "852": _envio(852, "delivered"),
            "853": 500,
            "854": _envio(854, "shipped"),
        },
    )

    resultado = _correr(dsn, falso)

    assert "851" not in falso.leidos
    assert falso.leidos == ["852", "853", "853", "853", "854"]
    assert (resultado.ok, resultado.escritos, resultado.omitidos) == (True, 2, 1)
    assert resultado.skip_reason == "1x envio_viejo_no_leido"
    (corrida,) = _corridas(conn)[-1:]
    assert (corrida["ok"], corrida["rows_written"], corrida["rows_skipped"]) == (True, 2, 1)
    assert corrida["skip_reason"] == "1x envio_viejo_no_leido"

    # Un 401 en un envio viejo (>30d) sigue abortando la corrida completa.
    vencido = MeliFalso(paginas=[], envios={"853": 401})
    with pytest.raises(meli_ingesta.PaseVencidoError):
        _correr(dsn, vencido, ahora=AHORA + timedelta(hours=4))
    assert _corridas(conn)[-1]["ok"] is False

    # Un abierto reciente (<=30d) que falla si baja ok aunque no salga en busqueda.
    _sembrar(conn, [_previo("855", "shipped", pedido_creado_el=AHORA - timedelta(days=20))])
    fallo_reciente = MeliFalso(paginas=[], envios={"853": 500, "854": _envio(854), "855": 500})
    res_reciente = _correr(dsn, fallo_reciente, ahora=AHORA + timedelta(hours=8))
    assert res_reciente.ok is False
    assert res_reciente.skip_reason == (
        "1x envio_no_leido, 1x envio_sin_cambio, 1x envio_viejo_no_leido"
    )


@_skip_db
def test_releer_abierto_sin_cambios_no_inserta_fila(base):
    conn, dsn = base
    ordenes = [_orden(1802, 801, pack=9801), _orden(1801, 801, pack=9801)]
    envios = {"801": _envio(801)}
    esperado = envio_desde_meli(ordenes, envios["801"])
    _correr(dsn, MeliFalso([ordenes], envios))

    with psycopg.connect(dsn) as lectura:
        lectura.execute("SET TIME ZONE 'Asia/Kolkata'")
        reconstruido = meli_ingesta.envios_abiertos(lectura)
    assert reconstruido == {"801": esperado}
    assert huella(reconstruido["801"]) == _filas(conn)[0]["huella"]

    # Sigue en la busqueda (con otro orden) y despues ya salio de ella.
    en_ventana = MeliFalso([list(reversed(ordenes))], envios)
    fuera_de_ventana = MeliFalso([], envios)
    for horas, falso in ((4, en_ventana), (8, fuera_de_ventana)):
        resultado = _correr(dsn, falso, ahora=AHORA + timedelta(hours=horas))
        assert falso.leidos == ["801"]
        assert (resultado.ok, resultado.escritos, resultado.sin_cambio) == (True, 0, 1)
    assert _total(conn) == 1
    assert [(c["rows_written"], c["rows_skipped"], c["skip_reason"]) for c in _corridas(conn)] == [
        (1, 0, None),
        (0, 1, "1x envio_sin_cambio"),
        (0, 1, "1x envio_sin_cambio"),
    ]


@_skip_db
def test_fallo_parcial_shipment_conserva_escritos_y_sella_ok_false(
    base, tmp_path, monkeypatch, caplog, capsys
):
    conn, dsn = base
    _tokens(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)
    caplog.set_level(logging.DEBUG)
    # main() lee el reloj real: las ordenes van sin fecha para no depender de el.
    paginas = [[_orden(1901 + i, 901 + i, creada=None) for i in range(3)]]
    falso = MeliFalso(paginas, {"901": _envio(901), "902": 500, "903": _envio(903)})

    assert meli_ingesta.main([], transport=httpx.MockTransport(falso)) == 1

    assert falso.leidos == ["901", "902", "902", "902", "903"]
    assert falso.metodos == {"GET"}
    assert [f["envio_ref"] for f in _filas(conn)] == ["901", "903"]
    (corrida,) = _corridas(conn)
    assert (corrida["ok"], corrida["rows_written"], corrida["rows_skipped"]) == (False, 2, 1)
    assert corrida["skip_reason"] == "1x envio_no_leido"
    assert corrida["finished_at"] is not None

    vencido = MeliFalso(paginas, {"901": 401, "902": _envio(902), "903": _envio(903)})
    assert meli_ingesta.main([], transport=httpx.MockTransport(vencido)) == 1

    # El 401 aborta: ni reintento ni los envios que seguian.
    assert vencido.leidos == ["901"]
    assert _total(conn) == 2
    abortada = _corridas(conn)[1]
    assert (abortada["ok"], abortada["rows_written"]) == (False, 0)
    assert "401" in abortada["skip_reason"]
    assert "pase del bridge vencido" in abortada["skip_reason"]
    salida = capsys.readouterr()
    assert "envio_no_leido" in salida.err
    assert "902" in caplog.text
    _sin_centinelas(conn, caplog.text, salida.out, salida.err)


@_skip_db
def test_limite_max_paginas_marca_aviso_y_ok_false(base, tmp_path, monkeypatch):
    conn, dsn = base
    paginas = [[_orden(1001, 501)], [_orden(1002, 502)]]
    envios = {"501": _envio(501), "502": _envio(502)}
    falso = MeliFalso(paginas, envios)

    resultado = _correr(dsn, falso, max_paginas=1)

    assert (resultado.ok, resultado.aviso, resultado.escritos) == (False, "limite_max_paginas", 1)
    assert len(falso.busquedas) == 1
    (corrida,) = _corridas(conn)
    assert (corrida["ok"], corrida["rows_written"]) == (False, 1)
    assert corrida["skip_reason"] == "paginacion_incompleta:limite_max_paginas"

    _tokens(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)
    # main() lee el reloj real: las ordenes van sin fecha para no depender de el.
    sin_fecha = [[_orden(1001, 501, creada=None)], [_orden(1002, 502, creada=None)]]
    transporte = httpx.MockTransport(MeliFalso(sin_fecha, envios))
    assert meli_ingesta.main(["--max-paginas", "1"], transport=transporte) == 1


@_skip_db
def test_pagina_vacia_antes_del_total_no_es_exito(base):
    conn, dsn = base

    def corta(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(200, json={"id": 111})
        return httpx.Response(200, json={"paging": {"total": 7}, "results": []})

    resultado = _correr(dsn, corta)

    assert (resultado.ok, resultado.aviso) == (False, "pagina_vacia_antes_del_total")


@_skip_db
def test_busqueda_sin_lista_o_users_me_sin_id_sellan_fallo(base):
    conn, dsn = base
    for respuestas, mensaje in (
        ({"/users/me": {}}, "sin id"),
        ({"/users/me": {"id": 111}, "/orders/search": {"paging": {}}}, "sin lista"),
    ):
        with pytest.raises(ReputacionError, match=mensaje):
            _correr(
                dsn, lambda r, cuerpos=respuestas: httpx.Response(200, json=cuerpos[r.url.path])
            )
    assert [(c["ok"], c["rows_written"]) for c in _corridas(conn)] == [(False, 0), (False, 0)]
    assert all(c["finished_at"] is not None for c in _corridas(conn))


@_skip_db
def test_ilegibles_se_cuentan_sin_tumbar_a_los_demas(base, caplog):
    conn, dsn = base
    caplog.set_level(logging.DEBUG)
    raro = _orden(1904, 904, creada="CENTINELA_FECHA", pack="CENTINELA_PACK")
    raro["order_items"] = [
        {"quantity": -2, "item": {"id": "MLM\u0000904", "title": "Pie\ud800za", "seller_sku": " "}}
    ]
    paginas = [
        [
            "no-es-orden",
            _orden(1900, None),
            _orden(None, 905),
            _orden(1902, 902),
            _orden(1903, 903),
            raro,
            # Repetida entre paginas porque entro una venta durante el recorrido.
            _orden(1901, 901),
        ],
        [_orden(1901, 901), _orden(1800, None, creada=VIEJA)],
    ]
    sin_numero = _envio(904, status_history="no-es-objeto", tracking_number=None)
    falso = MeliFalso(
        paginas,
        {
            "901": _envio(901),
            "902": {"status": "shipped", **_PII},
            "903": _envio(999),
            "904": sin_numero,
        },
    )

    resultado = _correr(dsn, falso)

    assert falso.leidos == ["901", "902", "903", "904"]
    assert (resultado.ok, resultado.escritos, resultado.omitidos) == (True, 2, 5)
    assert resultado.skip_reason == "2x envio_ilegible, 2x orden_ilegible, 1x orden_sin_envio"
    filas = _filas(conn)
    assert [(f["envio_ref"], f["pedidos"]) for f in filas] == [
        ("901", ["1901"]),
        ("904", ["1904"]),
    ]
    assert len(filas[0]["articulos"]) == 1
    assert filas[1]["pedido_creado_el"] is None
    assert filas[1]["pack_ref"] is None
    assert filas[1]["articulos"] == [
        {
            "pedido_ref": "1904",
            "sku": None,
            "titulo": "Pie?za",
            "cantidad": None,
            "producto_ref": "MLM904",
        }
    ]
    paquete = _filas(conn, "envio_paquete_observation", "paquete_ref")[1]
    assert (paquete["paquete_ref"], paquete["guia"], paquete["despachado_el"]) == (
        "904",
        None,
        None,
    )
    _sin_centinelas(conn, caplog.text)


@_skip_db
def test_error_inesperado_sella_solo_la_clase(base, monkeypatch, tmp_path, capsys):
    conn, dsn = base
    _tokens(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)

    def revienta(*_args, **_kwargs):
        raise RuntimeError("CENTINELA_DETALLE")

    monkeypatch.setattr("app.envios.meli_ingesta.envios_store.guardar", revienta)
    falso = MeliFalso([[_orden(1001, 501, creada=None)]], {"501": _envio(501)})

    assert meli_ingesta.main([], transport=httpx.MockTransport(falso)) == 1

    (corrida,) = _corridas(conn)
    assert (corrida["ok"], corrida["rows_written"]) == (False, 0)
    assert corrida["skip_reason"] == "fallo la corrida: RuntimeError"
    salida = capsys.readouterr()
    assert "RuntimeError" in salida.err
    _sin_centinelas(conn, salida.out, salida.err)


@_skip_db
def test_main_sin_llave_deja_corrida_fallida(base, monkeypatch, capsys):
    conn, dsn = base
    # El autouse de conftest deja ORBIT_SECRETS_DIR en un directorio vacio.
    monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)

    assert meli_ingesta.main([]) == 1

    (corrida,) = _corridas(conn)
    assert (corrida["source"], corrida["platform"], corrida["ok"]) == ("envios_meli", "meli", False)
    assert corrida["skip_reason"] == "fallo antes de empezar: ReputacionError"
    assert corrida["finished_at"] is not None
    assert "meli_tokens.json" in capsys.readouterr().err


@_skip_db
def test_main_exito_sale_cero(base, tmp_path, monkeypatch, capsys):
    conn, dsn = base
    _tokens(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)
    falso = MeliFalso([[_orden(1001, 501, creada=None)]], {"501": _envio(501)})

    assert meli_ingesta.main([], transport=httpx.MockTransport(falso)) == 0

    assert _total(conn) == 1
    assert "escritos=1 sin_cambio=0" in capsys.readouterr().out


def test_main_sin_dsn_sale_dos(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert meli_ingesta.main([]) == 2
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_cli_despacha_ingest_envios_meli(monkeypatch):
    recibidos: list = []
    monkeypatch.setattr(meli_ingesta, "main", lambda argv: recibidos.append(argv) or 7)

    assert cli.main(["ingest", "envios_meli", "--max-paginas", "3"]) == 7
    assert recibidos == [["--max-paginas", "3"]]
