"""ARREGLOS 05 D: toda mutacion Amazon Ads pasa por el cliente protegido.

Regresiones del bloque D de `plans/arreglos-05.md` (seccion D-E de
[DISENO](../../docs/evidencia/arreglos-05/DISENO.md), solo la parte D). El
servicio externo se simula con `httpx.MockTransport`; el resto (SQL, plan,
ledger, protocolo de reposicion) corre real, con Postgres desechable cuando el
camino lo necesita.

Lo que se comprueba, por camino:

- la fabrica (API/impulso/vigia) crea y cambia estado con el cliente
  protegido: perfil sellado, (metodo, ruta) dentro de la allowlist y cuerpo
  identico al payload que su ledger guardo antes del HTTP;
- el archivo manual y la reposicion/reversa (bloque C) usan el mismo cliente y
  su protocolo no cambia: 207 con error por item no sella ok, 401/403 llega
  como rechazo definitivo al ledger del intento;
- `preparar_escritor` recibe el modo real y no fabrica `live`;
- no queda transporte mutante propio en los modulos que escriben.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal

import httpx
import pytest
from test_arreglos05_c_reposicion import (
    _Amazon as _AmazonReposicion,
)
from test_arreglos05_c_reposicion import (
    _corre_cli,
    _db,
    _marca_repuesto,
    _semilla,
    _vivo,
)
from test_fabrica_campanas import (
    ARGS_BASE,
    _args_go,
    _conn_plan,
    _Cursor,
    _frontera_mutacion,
    _huella_de,
    _pasos,
    _sellos,
    fc,
)
from test_fabrica_campanas import (
    _Amazon as _AmazonFabrica,
)
from test_fabrica_campanas import (
    _ConnFalsa as _ConnFabrica,
)
from test_schema import _postgres_obligatorio_ausente

from app import fabrica_lotes as fl
from app import fabrica_motor as fm
from app import fabrica_plan as fp
from app.ads import archivar, archivar_inertes
from app.ads.client import MutationNotAllowedError
from app.ads.config import AdsCredentials
from app.ads.write import (
    MUTATION_CONTAINERS,
    MUTATION_REQUEST_TYPES,
    AdsApiErrorMutacion,
    AdsWriteClient,
)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

CRC = AdsCredentials(client_id="fake-c", client_secret="fake-c", refresh_token="fake-c")
VENDOR_KEYWORDS = "application/vnd.spkeyword.v3+json"


def _escritor(handler, *, plataforma: str = "amazon_mx", perfil: int = 101) -> AdsWriteClient:
    return AdsWriteClient(
        CRC,
        platform=plataforma,
        profile_id=perfil,
        modo_confirmado="live",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )


def _solo_cliente_con_transporte(monkeypatch):
    """Cualquier `httpx.Client` sin transporte inyectado seria un bypass."""
    real = httpx.Client

    def _cliente(*args, **kwargs):
        assert kwargs.get("transport") is not None, (
            "httpx.Client sin transporte inyectado: una mutacion salio por fuera "
            "del cliente protegido"
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _cliente)


class _Lector:
    """Lector de readback con el mismo transporte simulado."""

    def __init__(self, handler) -> None:
        self._c = httpx.Client(transport=httpx.MockTransport(handler))

    def list_objects(self, path, body, *, profile_id):
        return self._c.post(f"https://advertising-api.amazon.com{path}", json=body)


# ---------------------------------------------------------------------------
# D.2: operaciones especificas del cliente protegido
# ---------------------------------------------------------------------------


def test_arreglos05_d_crear_campana_sella_ruta_vendor_y_scope():
    """POST /sp/campaigns con el cuerpo sellado y el scope de la instancia."""
    pedidos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        pedidos.append(request)
        return httpx.Response(207, json={"campaigns": {"success": [{"campaignId": "c-1"}]}})

    _escritor(handler).crear_campana(
        nombre="t | base | exact | 2026-11-24",
        targeting_type="MANUAL",
        estado="ENABLED",
        presupuesto=Decimal("150"),
        moneda="MXN",
        fecha_inicio=dt.date(2026, 11, 24),
    )
    (pedido,) = pedidos
    assert pedido.method == "POST" and pedido.url.path == "/sp/campaigns"
    assert pedido.headers["Content-Type"] == "application/vnd.spcampaign.v3+json"
    assert pedido.headers["Accept"] == "application/vnd.spcampaign.v3+json"
    assert pedido.headers["Amazon-Advertising-API-Scope"] == "101"
    assert json.loads(pedido.content) == {
        "campaigns": [
            {
                "name": "t | base | exact | 2026-11-24",
                "targetingType": "MANUAL",
                "state": "ENABLED",
                "budget": {"budgetType": "DAILY", "budget": 150.0},
                "startDate": "2026-11-24",
                "dynamicBidding": {"strategy": "LEGACY_FOR_SALES"},
            }
        ]
    }


def test_arreglos05_d_crear_ad_group_y_target_asin():
    """ad group con defaultBid y target ASIN_SAME_AS: mismas rutas selladas."""
    pedidos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        pedidos.append(request)
        path = request.url.path
        return httpx.Response(207, json={MUTATION_CONTAINERS[path]: {"success": [{}]}})

    escritor = _escritor(handler)
    escritor.crear_ad_group(
        "21", nombre="ag", estado="ENABLED", default_bid=Decimal("2.50"), moneda="MXN"
    )
    escritor.crear_target_asin("31", "21", "B0ABCDEFGH", Decimal("1.10"), "MXN")
    ad_group, target = pedidos
    assert ad_group.url.path == "/sp/adGroups"
    assert ad_group.headers["Content-Type"] == "application/vnd.spadgroup.v3+json"
    assert json.loads(ad_group.content) == {
        "adGroups": [{"campaignId": "21", "name": "ag", "state": "ENABLED", "defaultBid": 2.5}]
    }
    assert target.url.path == "/sp/targets"
    assert json.loads(target.content) == {
        "targetingClauses": [
            {
                "adGroupId": "31",
                "campaignId": "21",
                "expressionType": "MANUAL",
                "expression": [{"type": "ASIN_SAME_AS", "value": "B0ABCDEFGH"}],
                "state": "ENABLED",
                "bid": 1.1,
            }
        ]
    }


def test_arreglos05_d_crear_keyword_respeta_match_y_estado_de_reposicion():
    """El mismo camino sirve al harvest (EXACT/ENABLED) y a la reposicion (PAUSED)."""
    pedidos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        pedidos.append(request)
        return httpx.Response(207, json={"keywords": {"success": [{"keywordId": "1"}]}})

    escritor = _escritor(handler)
    escritor.crear_keyword(
        "31", "21", "arras de plata", Decimal("12.5"), "MXN", match_type="PHRASE", estado="PAUSED"
    )
    escritor.crear_keyword("31", "21", "exacta", Decimal("1"), "MXN")
    reposicion, harvest = pedidos
    assert json.loads(reposicion.content) == {
        "keywords": [
            {
                "adGroupId": "31",
                "campaignId": "21",
                "keywordText": "arras de plata",
                "matchType": "PHRASE",
                "state": "PAUSED",
                "bid": 12.5,
            }
        ]
    }
    exacta = json.loads(harvest.content)["keywords"][0]
    assert exacta["matchType"] == "EXACT" and exacta["state"] == "ENABLED"


def test_arreglos05_d_crear_keyword_rechaza_match_y_estado_fuera_del_wire_sin_http():
    """Un valor fuera del enum revienta ANTES del HTTP (ni siquiera el token)."""
    emitidos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        emitidos.append(request)
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})

    escritor = _escritor(handler)
    with pytest.raises(ValueError):
        escritor.crear_keyword("31", "21", "x", Decimal("1"), "MXN", match_type="NEGATIVE_EXACT")
    with pytest.raises(ValueError):
        escritor.crear_keyword("31", "21", "x", Decimal("1"), "MXN", estado="ARCHIVED")
    with pytest.raises(ValueError):
        escritor.crear_target_asin("31", "21", " ", Decimal("1"), "MXN")
    assert emitidos == []


def test_arreglos05_d_cambiar_estado_campana_solo_activa_o_pausa():
    """PUT /sp/campaigns de estado: ENABLED/PAUSED; ARCHIVED no se escribe."""
    pedidos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        pedidos.append(request)
        return httpx.Response(207, json={"campaigns": {"success": [{}], "error": []}})

    escritor = _escritor(handler)
    escritor.cambiar_estado_campana("c-1", "PAUSED")
    escritor.cambiar_estado_campana("c-1", "ENABLED")
    with pytest.raises(ValueError):
        escritor.cambiar_estado_campana("c-1", "ARCHIVED")
    assert [(p.method, p.url.path) for p in pedidos] == [("PUT", "/sp/campaigns")] * 2
    assert [json.loads(p.content)["campaigns"][0]["state"] for p in pedidos] == [
        "PAUSED",
        "ENABLED",
    ]


def test_arreglos05_d_vendor_de_la_fabrica_y_del_cliente_es_el_mismo():
    """El mapa del plan y el del cliente no pueden divergir (regla 2)."""
    for path, vendor in fp.VENDOR_POR_PATH.items():
        assert MUTATION_REQUEST_TYPES[("POST", path)] == vendor


def test_arreglos05_d_allowlist_suma_los_creates_y_conserva_default_deny():
    """Las tres rutas nuevas entran con su vendor; nada mas se abre."""
    for path, contenedor, vendor in (
        ("/sp/campaigns", "campaigns", "application/vnd.spcampaign.v3+json"),
        ("/sp/adGroups", "adGroups", "application/vnd.spadgroup.v3+json"),
        ("/sp/targets", "targetingClauses", "application/vnd.sptargetingclause.v3+json"),
    ):
        assert MUTATION_REQUEST_TYPES[("POST", path)] == vendor
        assert MUTATION_CONTAINERS[path] == contenedor
    assert ("DELETE", "/sp/campaigns") not in MUTATION_REQUEST_TYPES
    assert ("POST", "/sp/adGroups/delete") not in MUTATION_REQUEST_TYPES
    assert ("PUT", "/sp/adGroups") not in MUTATION_REQUEST_TYPES


def test_arreglos05_d_ack_de_y_ack_de_error_conservan_el_207_y_lo_incierto():
    """Un solo ack canonico: cuerpo tal cual y rechazo/incierto distinguibles."""
    from app.ads.sp_wire import ack_de, ack_de_error

    ok = ack_de(httpx.Response(207, json={"keywords": {"success": [{"keywordId": "9"}]}}))
    assert ok["status"] == 207
    assert ok["cuerpo"] == {"keywords": {"success": [{"keywordId": "9"}]}}
    nulo = ack_de(httpx.Response(207, content=b"null"))
    assert nulo["status"] == 207 and nulo["cuerpo"] is None
    rechazo = ack_de_error(
        AdsApiErrorMutacion(
            "400", cuerpo='{"code":"400"}', status=400, method="POST", path="/sp/keywords"
        )
    )
    assert rechazo["status"] == 400 and rechazo["texto"] == '{"code":"400"}'
    incierto = ack_de_error(
        AdsApiErrorMutacion("", cuerpo="", status=None, method="POST", path="/sp/keywords")
    )
    assert incierto["status"] == "excepcion"


def test_arreglos05_d_preparar_escritor_exige_el_modo_autorizado(monkeypatch):
    """Sin argumento no compila; `shadow` revienta antes de construir nada."""
    from app.ads.structure_api import PerfilAds

    perfil = PerfilAds(
        profile_id=101,
        country="MX",
        currency_code="MXN",
        account_type="seller",
        valid_payment_method=True,
        account_name="t",
        aceptado=True,
        platform="amazon_mx",
        moneda="MXN",
        motivo=None,
    )
    monkeypatch.setattr(archivar, "evaluar_perfiles", lambda _c: [perfil])
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(lambda cls, *a, **k: CRC))
    with pytest.raises(TypeError):
        archivar.preparar_escritor("amazon_mx")  # type: ignore[call-arg]
    with pytest.raises(MutationNotAllowedError):
        archivar.preparar_escritor("amazon_mx", modo_confirmado="shadow")
    escritor = archivar.preparar_escritor("amazon_mx", modo_confirmado="live")
    assert escritor._profile_id == "101"


# ---------------------------------------------------------------------------
# D.2/D.3: la fabrica (y sus consumidores) mutan por el cliente protegido
# ---------------------------------------------------------------------------


def _huella(monkeypatch, capsys) -> str:
    _huella_de(monkeypatch)
    return [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(
        ": "
    )[1]


def test_arreglos05_d_fabrica_crea_por_el_cliente_y_cuerpo_igual_al_ledger(monkeypatch, capsys):
    """Cada POST sale con el scope sellado y el mismo objeto que el ledger guardo."""
    _solo_cliente_con_transporte(monkeypatch)
    huella = _huella(monkeypatch, capsys)
    conn_admin = _ConnFabrica()
    amazon = _AmazonFabrica()
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    assert fc.main() == 0

    creados = [p for p in amazon.pedidos if (p.method, p.url.path) in MUTATION_REQUEST_TYPES]
    assert creados, "no hubo mutaciones"
    for pedido in creados:
        par = (pedido.method, pedido.url.path)
        assert par in MUTATION_REQUEST_TYPES, par
        assert pedido.headers["Amazon-Advertising-API-Scope"] == "101"
        assert pedido.headers["Content-Type"] == MUTATION_REQUEST_TYPES[par]

    payloads = [json.loads(p[4]) for p in _pasos(conn_admin)]
    enviados = [
        json.loads(pedido.content)[fp.ENVOLTURA_POR_PATH[pedido.url.path]][0] for pedido in creados
    ]
    assert enviados == payloads, "el cuerpo enviado no es el payload del ledger"


def test_arreglos05_d_fabrica_207_con_error_por_item_no_sella_applied(monkeypatch, capsys):
    """Un error por item deja el paso failed y detiene el lote."""
    huella = _huella(monkeypatch, capsys)
    conn_admin = _ConnFabrica()

    class _AmazonConError(_AmazonFabrica):
        def __call__(self, request):
            if request.url.path == "/sp/keywords" and request.method == "POST":
                self.pedidos.append(request)
                return httpx.Response(
                    207,
                    json={
                        "keywords": {
                            "success": [],
                            "error": [{"index": 0, "code": "4XX", "description": "rechazo"}],
                        }
                    },
                )
            return super().__call__(request)

    amazon = _AmazonConError()
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(Exception, match="rechazado"):
        fc.main()
    fallados = _sellos(conn_admin, "failed")
    aplicados = _sellos(conn_admin, "applied")
    assert len(fallados) == 1
    assert len(aplicados) == len(_pasos(conn_admin)) - len(fallados)


def test_arreglos05_d_fabrica_caida_tras_el_http_deja_el_paso_incierto(monkeypatch, capsys):
    """Un 5xx no reintenta y el paso queda failed con su motivo, no applied."""
    huella = _huella(monkeypatch, capsys)
    conn_admin = _ConnFabrica()

    class _AmazonCaida(_AmazonFabrica):
        def __call__(self, request):
            if request.url.path == "/sp/adGroups":
                self.pedidos.append(request)
                return httpx.Response(503, json={"code": "503"})
            return super().__call__(request)

    amazon = _AmazonCaida()
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(Exception, match="INCERTO"):
        fc.main()
    assert len(amazon.posts("/sp/adGroups")) == 1, "un 5xx no se reintenta"
    assert len(_sellos(conn_admin, "failed")) == 1
    assert len(_sellos(conn_admin, "applied")) == 1  # solo la campana previa


def test_arreglos05_d_fabrica_dry_run_no_construye_escritor(monkeypatch, capsys):
    """Sin ceremonia no hay cliente de escritura ni se toca Amazon."""
    _huella_de(monkeypatch)
    capsys.readouterr()

    def _no_debe(*_a, **_k):
        raise AssertionError("el dry-run construyo un AdsWriteClient")

    monkeypatch.setattr(fm, "AdsWriteClient", _no_debe)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    assert fc.main() == 0


def test_arreglos05_d_fabrica_reactiva_por_el_cliente_protegido():
    """La reactivacion de campanas (graduacion de la fabrica) usa el mismo PUT."""
    pedidos: list[httpx.Request] = []
    estados = {"c-1": "PAUSED"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        pedidos.append(request)
        if request.url.path == "/sp/campaigns/list":
            return httpx.Response(
                200, json={"campaigns": [{"campaignId": "c-1", "state": estados["c-1"]}]}
            )
        assert request.method == "PUT" and request.url.path == "/sp/campaigns"
        estados["c-1"] = json.loads(request.content)["campaigns"][0]["state"]
        return httpx.Response(207, json={"campaigns": {"success": [{}], "error": []}})

    class _ConnPaquetes(_ConnFabrica):
        def execute(self, sql, params=None):
            plano = " ".join(str(sql).split()).lower()
            if plano.startswith("select estado from fabrica_lote"):
                return _Cursor([("pausado",)])
            return super().execute(sql, params)

    conn = _ConnPaquetes(grupo=[("exact", "c-1", None)])
    ctx = fm._Ctx(_escritor(handler), _Lector(handler), 101, conn, "L1")
    fl.reactiva_lote(ctx, "L1", "dueno")
    puts = [p for p in pedidos if p.method == "PUT"]
    assert len(puts) == 1 and puts[0].headers["Amazon-Advertising-API-Scope"] == "101"
    assert len(_sellos(conn, "applied")) == 1


class _ConnPausaHuerfanas:
    """Conexion enlatada para `pausa_huerfanas`: filas del lote + escrituras."""

    def __init__(self, filas):
        self.filas = list(filas)
        self.escrituras = []
        self.commits = 0

    def execute(self, sql, params=None):
        plano = " ".join(str(sql).split())
        bajo = plano.lower()
        if bajo.startswith("select platform::text from fabrica_lote"):
            return _Cursor([("amazon_mx",)])
        if bajo.startswith("select coalesce(max(orden)"):
            return _Cursor([(0,)])
        if bajo.startswith("select distinct p.rol"):
            return _Cursor(self.filas)
        if bajo.startswith(("insert", "update", "delete")):
            self.escrituras.append((plano, params))
            if "insert into fabrica_lote_paso" in bajo:
                return _Cursor([(len(self.escrituras),)])
            return _Cursor([])
        raise AssertionError(f"SQL inesperado: {plano[:120]}")

    def commit(self):
        self.commits += 1


def test_arreglos05_d_fabrica_pausa_huerfanas_sigue_tras_un_503():
    """Un 5xx en una campana no frena las demas pausas del lote.

    El paso de la que fallo queda `failed` CON su external_id y su ACK incierto
    (la conciliacion lo puede leer); las siguientes se intentan igual y el
    residuo se informa al final, como antes del cliente protegido."""
    from app import impulso_preview as io

    estados = {"c-1": "ENABLED", "c-2": "ENABLED"}
    pedidos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        pedidos.append(request)
        if request.url.path.endswith("/list"):
            pedido = json.loads(request.content)["campaignIdFilter"]["include"][0]
            return httpx.Response(
                200, json={"campaigns": [{"campaignId": pedido, "state": estados[pedido]}]}
            )
        assert request.method == "PUT" and request.url.path == "/sp/campaigns"
        campana = json.loads(request.content)["campaigns"][0]
        if campana["campaignId"] == "c-1":
            return httpx.Response(503, json={"code": "503"})
        estados[campana["campaignId"]] = campana["state"]
        return httpx.Response(207, json={"campaigns": {"success": [{}], "error": []}})

    conn = _ConnPausaHuerfanas([("exact", "c-1"), ("auto", "c-2")])
    pausador = io.PausadorFabrica(conn)
    pausador._armado = (CRC, _Lector(handler), {"amazon_mx": 101})
    pausador._ctxs["amazon_mx"] = fm._Ctx(_escritor(handler), _Lector(handler), 101, conn, "L1")
    with pytest.raises(Exception, match="c-1"):
        pausador.pausa_huerfanas("L1")

    puts = [
        json.loads(p.content)["campaigns"][0]["campaignId"] for p in pedidos if p.method == "PUT"
    ]
    assert puts == ["c-1", "c-2"], "la segunda campana tambien se intenta"
    sellos = [e for e in conn.escrituras if "fabrica_lote_paso" in e[0].lower()]
    fallados = [e for e in sellos if "'failed'" in e[0]]
    aplicados = [e for e in sellos if "'applied'" in e[0]]
    assert len(fallados) == 1 and len(aplicados) == 1
    (external, ack, _readback, _paso) = fallados[0][1]
    assert external == "c-1", "el paso fallido conserva su external_id"
    assert json.loads(ack)["status"] == 503, "el ACK incierto queda en el ledger"


def test_arreglos05_d_fabrica_pausa_huerfanas_sigue_sin_token():
    """Un fallo de LWA en la primera pausa no deja el paso a medias ni frena el resto.

    `AdsAuthError` no es `AdsApiError`: sin este camino escapaba despues del
    INSERT del paso, dejandolo `planeado` sin ID y saltandose las demas
    campanas. Ahora el paso se sella `failed` con su `external_id` y el motivo,
    y la siguiente campana se intenta con el token que ya funciona."""
    from app import impulso_preview as io

    estados = {"c-1": "ENABLED", "c-2": "ENABLED"}
    puts: list[str] = []
    intentos_lwa: list[int] = []

    def handler_lector(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "lector", "expires_in": 3600})
        assert request.url.path.endswith("/list")
        pedido = json.loads(request.content)["campaignIdFilter"]["include"][0]
        return httpx.Response(
            200, json={"campaigns": [{"campaignId": pedido, "state": estados[pedido]}]}
        )

    def handler_escritor(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            intentos_lwa.append(1)
            # El refresh es idempotente: el cliente agota sus reintentos y
            # recien entonces lanza AdsAuthError. La segunda campana ya
            # encuentra el token.
            if len(intentos_lwa) <= 4:
                return httpx.Response(503, json={"error": "temporal"})
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.method == "PUT" and request.url.path == "/sp/campaigns"
        campana = json.loads(request.content)["campaigns"][0]
        puts.append(campana["campaignId"])
        estados[campana["campaignId"]] = campana["state"]
        return httpx.Response(207, json={"campaigns": {"success": [{}], "error": []}})

    conn = _ConnPausaHuerfanas([("exact", "c-1"), ("auto", "c-2")])
    pausador = io.PausadorFabrica(conn)
    pausador._armado = (CRC, _Lector(handler_lector), {"amazon_mx": 101})
    pausador._ctxs["amazon_mx"] = fm._Ctx(
        _escritor(handler_escritor), _Lector(handler_lector), 101, conn, "L1"
    )
    with pytest.raises(Exception, match="c-1"):
        pausador.pausa_huerfanas("L1")

    assert puts == ["c-2"], "la campana con token valido si se pausa"
    assert len(intentos_lwa) == 5, (
        "4 intentos del refresh que agota sus reintentos + 1 del token que ya funciona"
    )
    sellos = [e for e in conn.escrituras if "fabrica_lote_paso" in e[0].lower()]
    fallados = [e for e in sellos if "'failed'" in e[0]]
    aplicados = [e for e in sellos if "'applied'" in e[0]]
    assert len(fallados) == 1 and len(aplicados) == 1
    (external, ack, _readback, _paso) = fallados[0][1]
    assert external == "c-1", "el paso fallido conserva su external_id"
    assert json.loads(ack)["status"] == fm.ACK_SIN_ENVIO, "nada salio: no es incierto"
    assert "LWA" in json.loads(ack)["texto"], "el motivo queda en el ledger"


def test_arreglos05_d_fabrica_lwa_rechazado_no_registra_lote(monkeypatch, capsys):
    """Si LWA rechaza el preflight, no queda un lote que bloquee el reintento."""
    huella = _huella(monkeypatch, capsys)
    conn_admin = _ConnFabrica()

    class _AmazonSinToken(_AmazonFabrica):
        def __call__(self, request):
            if "auth/o2/token" in str(request.url):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return super().__call__(request)

    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, _AmazonSinToken())
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(Exception, match="LWA"):
        fc.main()
    lotes = [
        e for e in conn_admin.escrituras if e[0].lower().startswith("insert into fabrica_lote ")
    ]
    assert lotes == [], "sin token no se registra el lote: la misma huella se puede repetir"

    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, _AmazonFabrica())
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    assert fc.main() == 0, "la misma orden se repite cuando LWA vuelve"


def test_arreglos05_d_sin_transporte_mutante_propio():
    """Los modulos que escriben ya no tienen POST/PUT/httpx propios."""
    for modulo in (fm, archivar_inertes):
        assert not hasattr(modulo, "_token_lwa"), modulo.__name__
        assert not hasattr(modulo, "_post"), modulo.__name__


# ---------------------------------------------------------------------------
# D.2/D.3: archivo manual y reposicion (bloque C) por el cliente protegido
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_d_inertes_archiva_por_el_cliente_y_sella_applied(monkeypatch):
    """El DELETE del archivo manual sale con el scope sellado y su ack."""
    from test_archiva_inertes import (
        _ACK_DELETE_OK,
        _FILA_KW_MX,
        GO,
        _ClienteFalso,
        _fakea_frontera,
        _obj_kw,
        _RedFalsa,
    )
    from test_archiva_inertes import (
        _ConnFalsa as _ConnInertes,
    )
    from test_archiva_inertes import (
        _huella_de as _huella_inertes,
    )

    red = _RedFalsa(deletes=[_ACK_DELETE_OK])
    cliente = _ClienteFalso(
        {"kw-ext-11": [_obj_kw("kw-ext-11", "ENABLED"), _obj_kw("kw-ext-11", "ARCHIVED")]}
    )
    conn_admin = _ConnInertes()
    _fakea_frontera(monkeypatch, _ConnInertes(plan=[_FILA_KW_MX]), conn_admin, cliente, red)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "archiva_inertes.py",
            "--acepto-mutacion-real",
            "--esperado",
            "1",
            "--go",
            GO,
            "--huella",
            _huella_inertes(_FILA_KW_MX),
        ],
    )
    assert archivar_inertes.main() == 0
    (delete,) = [p for p in red.pedidos if p.url.path.endswith("/delete")]
    assert delete.headers["Amazon-Advertising-API-Scope"] == "101"
    assert delete.headers["Content-Type"] == VENDOR_KEYWORDS
    assert delete.url.path == "/sp/keywords/delete"
    assert len(conn_admin.inserts) == 1
    assert len(conn_admin.updates) == 1 and "'applied'" in conn_admin.updates[0][0]


@_skip_db
def test_arreglos05_d_inertes_207_con_error_por_item_no_marca_applied(monkeypatch):
    """Un DELETE con 207 y error por item deja la fila failed."""
    from test_archiva_inertes import (
        _FILA_KW_MX,
        GO,
        _ClienteFalso,
        _fakea_frontera,
        _obj_kw,
        _RedFalsa,
    )
    from test_archiva_inertes import (
        _ConnFalsa as _ConnInertes,
    )
    from test_archiva_inertes import (
        _huella_de as _huella_inertes,
    )

    red = _RedFalsa(deletes=[(207, {"keywords": {"success": [], "error": [{"code": "4XX"}]}})])
    cliente = _ClienteFalso({"kw-ext-11": [_obj_kw("kw-ext-11", "ENABLED")]})
    conn_admin = _ConnInertes()
    _fakea_frontera(monkeypatch, _ConnInertes(plan=[_FILA_KW_MX]), conn_admin, cliente, red)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "archiva_inertes.py",
            "--acepto-mutacion-real",
            "--esperado",
            "1",
            "--go",
            GO,
            "--huella",
            _huella_inertes(_FILA_KW_MX),
        ],
    )
    with pytest.raises(archivar_inertes.Abortar):
        archivar_inertes.main()
    assert len(conn_admin.inserts) == 1
    assert len(conn_admin.updates) == 1 and "'failed'" in conn_admin.updates[0][0]


def _fuerza_transporte(monkeypatch, amazon):
    original = httpx.Client.__init__

    def init(client, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(amazon)
        original(client, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", init)
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", lambda: CRC)


@_skip_db
def test_arreglos05_d_inertes_reponer_envia_por_el_cliente_y_sella_repuesto(monkeypatch):
    """La reposicion (C) usa el cliente: un POST, cuerpo exacto y scope sellado."""
    with _db("orbit_d_reponer") as (conn, _extra):
        fila, _externo, _texto = _semilla(conn)
        amazon = _AmazonReposicion()
        _fuerza_transporte(monkeypatch, amazon)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", conn.info.dsn)
        salida = _corre_cli(
            ["archiva_inertes.py", "--reponer", "lote-c", "--acepto-mutacion-real", "--go", "go"]
        )
        assert salida == "ok", salida
        (cuerpo,) = amazon.posts
        assert cuerpo == {
            "keywords": [
                {
                    "adGroupId": "ag-1",
                    "campaignId": "camp-1",
                    "keywordText": "termino propio",
                    "matchType": "EXACT",
                    "state": "PAUSED",
                    "bid": 1.0,
                }
            ]
        }
        estado, nuevo = conn.execute(
            "SELECT estado, repuesto_external FROM keyword_archivo_manual WHERE id = %s", (fila,)
        ).fetchone()
        assert (estado, nuevo) == ("repuesto", "k-1")


@_skip_db
def test_arreglos05_d_inertes_401_del_cliente_llega_como_rechazo_al_intento(monkeypatch):
    """El 401 del cliente protegido se conserva en el ACK y aborta la fase."""
    with _db("orbit_d_rechazo") as (conn, _extra):
        fila, nuevo, _texto = _semilla(conn)
        _marca_repuesto(conn, fila, nuevo)
        amazon = _AmazonReposicion([_vivo(nuevo)])
        amazon.falla_delete = 401
        _fuerza_transporte(monkeypatch, amazon)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", conn.info.dsn)
        salida = _corre_cli(
            [
                "archiva_inertes.py",
                "--revertir-reposicion",
                "lote-c",
                "--acepto-mutacion-real",
                "--go",
                "go",
            ]
        )
        assert salida.startswith("abortar"), salida
        (intento,) = conn.execute(
            "SELECT ack FROM keyword_reposicion_intento WHERE archivo_id = %s"
            " AND finished_at IS NULL",
            (fila,),
        ).fetchall()
        assert int(intento[0]["status"]) == 401


@_skip_db
def test_arreglos05_d_reposicion_lwa_rechazado_detiene_la_fase():
    """Sin token no sale ningun POST: el intento se sella no_aplicado y la fase
    se detiene antes de abrir el intento de la fila siguiente."""
    from test_arreglos05_c_reposicion import PERFILES

    from app import apply_estados as est

    with _db("orbit_d_sin_token") as (conn, _extra):
        fila1, _ext1, _t1 = _semilla(conn, texto="termino uno", externo="vieja-1")
        fila2, _ext2, _t2 = _semilla(conn, texto="termino dos", externo="vieja-2")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(400, json={"error": "invalid_grant"})
            raise AssertionError(f"no debe salir HTTP de mutacion: {request.url}")

        escritor = AdsWriteClient(
            CRC,
            platform="amazon_us",
            profile_id=PERFILES["amazon_us"],
            modo_confirmado="live",
            transport=httpx.MockTransport(handler),
            sleep=lambda _s: None,
        )
        enviar = archivar_inertes._enviar_keyword({"amazon_us": escritor})
        with pytest.raises(archivar.AbortoFase):
            archivar.reponer_keyword(
                conn,
                lote="lote-c",
                cliente=object(),
                perfiles=PERFILES,
                enviar=enviar,
                owner="cli-a",
                ejecutar=True,
                dormir=lambda _s: None,
            )
        intentos = conn.execute(
            "SELECT archivo_id, resultado FROM keyword_reposicion_intento ORDER BY archivo_id"
        ).fetchall()
        assert [i[0] for i in intentos] == [fila1], "la segunda fila ni abre intento"
        assert intentos[0][1] == est.RESULTADO_NO_APLICADO
