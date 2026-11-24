"""ARREGLOS 05 C: reposicion durable de keywords.

Regresiones del bloque C de `plans/arreglos-05.md` (seccion C de DISENO). Cada
prueba corre el motor real sobre una base desechable con TODAS las migraciones
canonicas y la API de Ads simulada con `httpx.MockTransport`: cero HTTP vivo y
secretos siempre falsos.

La reproduccion externa `T writes test_11` (un solo POST tras ACK y fallo de
readback) se cubre aqui a nivel CLI y en su archivo de aceptacion.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
import psycopg
import pytest
from test_arreglos05_a_ciclo import _db
from test_cycle_apply import PERFIL_US
from test_schema import _postgres_obligatorio_ausente

from app import apply_estados as est
from app.ads import archivar, archivar_inertes
from app.ads import reposicion_store as store
from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.apply_harvest_list import ListaIncompleta, _lista_completa

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
PERFILES = {"amazon_us": PERFIL_US["profileId"]}
_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# ---------------------------------------------------------------------------
# Semilla y doble de Amazon
# ---------------------------------------------------------------------------


def _semilla(
    conn,
    *,
    lote: str = "lote-c",
    plataforma: str = "amazon_us",
    moneda: str = "USD",
    bid: str | None = "1.0000",
    match: str = "EXACT",
    texto: str = "termino propio",
    campaign: str = "camp-1",
    ad_group: str = "ag-1",
    externo: str = "vieja-1",
) -> tuple[int, str, str]:
    """Una fila `applied` del ledger madre con identidad completa."""
    kw = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, match_type, keyword_text)"
        " VALUES (%s::platform, 'keyword', %s, %s, %s) RETURNING id",
        (plataforma, externo, match, texto),
    ).fetchone()[0]
    ack = json.dumps({"keywords": {"success": [{"keywordId": externo}], "error": []}})
    fila = conn.execute(
        "INSERT INTO keyword_archivo_manual"
        " (lote, ad_entity_id, platform, campaign_external, ad_group_external,"
        "  keyword_external, keyword_text, match_type, bid, bid_currency,"
        "  clasificacion, dias_sin_impresiones, go_literal, estado, ack,"
        "  readback_estado)"
        " VALUES (%s, %s, %s::platform, %s, %s, %s, %s, %s, %s,"
        "         %s, 'peso_muerto', 90, 'go de prueba', 'applied', %s::jsonb,"
        "         'ARCHIVED')"
        " RETURNING id",
        (
            lote,
            kw,
            plataforma,
            campaign,
            ad_group,
            externo,
            texto,
            match,
            Decimal(bid) if bid is not None else None,
            moneda,
            ack,
        ),
    ).fetchone()[0]
    conn.commit()
    return fila, externo, texto


def _marca_repuesto(conn, fila: int, nuevo: str) -> None:
    conn.execute(
        "UPDATE keyword_archivo_manual SET estado = 'repuesto', repuesto_at = now(),"
        " repuesto_external = %s, repuesto_ack = %s::jsonb WHERE id = %s",
        (nuevo, json.dumps({"keywords": {"success": [{"keywordId": nuevo}]}}), fila),
    )
    conn.commit()


def _intentos(conn, fila: int) -> list[dict]:
    return [
        f[0]
        for f in conn.execute(
            "SELECT to_jsonb(i) FROM keyword_reposicion_intento i"
            " WHERE archivo_id = %s ORDER BY seq",
            (fila,),
        ).fetchall()
    ]


def _vivo(keyword_id: str, texto: str = "termino propio", estado: str = "PAUSED") -> dict:
    return {
        "keywordId": keyword_id,
        "adGroupId": "ag-1",
        "campaignId": "camp-1",
        "keywordText": texto,
        "matchType": "EXACT",
        "state": estado,
        "bid": "1.0",
    }


class _Amazon:
    """API de Ads simulada; solo el servicio externo es doble."""

    def __init__(self, keywords: list[dict] | None = None) -> None:
        self.keywords = [dict(x) for x in (keywords or [])]
        self.posts: list[dict] = []
        self.deletes: list[dict] = []
        self.falla_lectura: int | None = None
        self.falla_post: int | None = None
        self.falla_delete: int | None = None
        self.pierde_post = False
        self.pierde_delete = False
        self.delete_cuerpo_nulo = False
        self.post_cuerpo_nulo = False
        self.list_truncada = False
        self.total_declarado: int | None = None
        self.al_post = None
        self.al_delete = None
        self._seq = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600})
        if request.method == "GET" and request.url.path == "/v2/profiles":
            return httpx.Response(200, json=[PERFIL_US])
        path = request.url.path
        cuerpo = json.loads(request.content) if request.content else {}
        if path == "/sp/keywords/list":
            if self.falla_lectura is not None:
                return httpx.Response(self.falla_lectura, json={"message": "sin lectura"})
            if self.list_truncada:
                return httpx.Response(200, json={"keywords": [], "nextToken": "misma"})
            total = len(self.keywords) if self.total_declarado is None else self.total_declarado
            return httpx.Response(
                200, json={"keywords": list(self.keywords), "totalResults": total}
            )
        if path == "/sp/keywords" and request.method == "POST":
            self.posts.append(cuerpo)
            if self.al_post is not None:
                self.al_post()
            if self.falla_post is not None:
                return httpx.Response(self.falla_post, json={"code": str(self.falla_post)})
            self._seq += 1
            nuevo = f"k-{self._seq}"
            self.keywords.append({**cuerpo["keywords"][0], "keywordId": nuevo})
            if self.post_cuerpo_nulo:
                self.post_cuerpo_nulo = False
                return httpx.Response(
                    207, content=b"null", headers={"content-type": "application/json"}
                )
            if self.pierde_post:
                self.pierde_post = False
                raise httpx.ReadTimeout("creada; respuesta perdida", request=request)
            return httpx.Response(
                207,
                json={"keywords": {"success": [{"keywordId": nuevo}], "error": []}},
            )
        if path == "/sp/keywords/delete" and request.method == "POST":
            self.deletes.append(cuerpo)
            if self.al_delete is not None:
                self.al_delete()
            if self.falla_delete is not None:
                return httpx.Response(self.falla_delete, json={"code": str(self.falla_delete)})
            ids = [str(x) for x in cuerpo["keywordIdFilter"]["include"]]
            for objeto in self.keywords:
                if str(objeto["keywordId"]) in ids:
                    objeto["state"] = "ARCHIVED"
            if self.delete_cuerpo_nulo:
                self.delete_cuerpo_nulo = False
                return httpx.Response(
                    207, content=b"null", headers={"content-type": "application/json"}
                )
            if self.pierde_delete:
                self.pierde_delete = False
                raise httpx.ReadTimeout("archivada; respuesta perdida", request=request)
            return httpx.Response(
                207, json={"keywords": {"success": [{"keywordId": ids[0]}], "error": []}}
            )
        raise AssertionError(f"request inesperado: {request.method} {path}")


def _cliente(amazon: _Amazon):
    return AdsClient(
        AdsCredentials(client_id="fake-c", client_secret="fake-c", refresh_token="fake-c"),
        transport=httpx.MockTransport(amazon),
    )


def _respuesta(amazon: _Amazon, metodo: str, path: str, payload: dict, envolver: str | None = None):
    cuerpo = {envolver: [payload]} if envolver else payload
    req = httpx.Request(metodo, f"https://advertising-api.amazon.com{path}", json=cuerpo)
    resp = amazon(req)
    try:
        datos = resp.json()
    except ValueError:
        datos = {}
    return {"status": resp.status_code, "cuerpo": datos, "texto": resp.text[:400]}


def _enviar(amazon: _Amazon):
    def enviar(plataforma: str, profile: int, payload: dict) -> dict:
        return _respuesta(amazon, "POST", "/sp/keywords", payload, "keywords")

    return enviar


def _eliminar(amazon: _Amazon):
    def eliminar(plataforma: str, profile: int, payload: dict) -> dict:
        return _respuesta(amazon, "POST", "/sp/keywords/delete", payload)

    return eliminar


def _red(monkeypatch, amazon: _Amazon, dsn: str) -> None:
    original = httpx.Client.__init__

    def init(client, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(amazon)
        original(client, *args, **kwargs)

    cred = AdsCredentials(client_id="fake-c", client_secret="fake-c", refresh_token="fake-c")
    monkeypatch.setattr(httpx.Client, "__init__", init)
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", lambda: cred)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)


def _abre_intento(
    conn,
    fila: int,
    *,
    tipo: str = "reponer",
    owner: str = "cli-a",
    ack: dict | None = None,
    external_id: str | None = None,
    lease: dt.timedelta = dt.timedelta(minutes=5),
) -> int:
    intento = conn.execute(
        "INSERT INTO keyword_reposicion_intento"
        " (archivo_id, seq, tipo, owner, lease_hasta, request_payload)"
        " VALUES (%s, coalesce((SELECT max(seq) FROM keyword_reposicion_intento"
        "   WHERE archivo_id = %s), 0) + 1, %s, %s, now() + %s, '{}'::jsonb)"
        " RETURNING id",
        (fila, fila, tipo, owner, lease),
    ).fetchone()[0]
    if ack is not None:
        conn.execute(
            "UPDATE keyword_reposicion_intento SET ack = %s::jsonb, external_id = %s WHERE id = %s",
            (json.dumps(ack), external_id, intento),
        )
    conn.commit()
    return intento


def _corre_cli(argv: list[str]) -> str:
    with patch("sys.argv", argv), patch("sys.stdout", new_callable=StringIO):
        try:
            archivar_inertes.main()
            return "ok"
        except archivar_inertes.Abortar as exc:
            return f"abortar: {exc}"


def _vence_lease(conn, fila: int) -> None:
    conn.execute(
        "UPDATE keyword_reposicion_intento"
        " SET lease_hasta = started_at + interval '1 millisecond'"
        " WHERE archivo_id = %s AND finished_at IS NULL",
        (fila,),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Modelo puro: adaptadores de la reposicion
# ---------------------------------------------------------------------------


class _LectorLista:
    def __init__(self, respuestas: list[dict]) -> None:
        self.respuestas = list(respuestas)

    def list_sellado(self, path: str, body: dict):
        return httpx.Response(200, json=self.respuestas.pop(0))


def test_arreglos05_c_total_solo_se_exige_sin_filtro():
    """El total declarado prueba ausencia solo en la consulta sin filtro.

    Con `adGroupIdFilter` Amazon puede contar filas de otros grupos: se
    conservan el token y la pertenencia, no el total (contrato 04 B-C)."""
    fila = {
        "keywordId": "k-1",
        "adGroupId": "6201",
        "keywordText": "termino",
        "matchType": "EXACT",
        "state": "PAUSED",
    }
    lector = _LectorLista([{"keywords": [fila], "totalResults": 50}])
    with pytest.raises(ListaIncompleta):
        _lista_completa(lector, "/sp/keywords/list")

    lector = _LectorLista([{"keywords": [fila], "totalResults": 50}])
    assert _lista_completa(lector, "/sp/keywords/list", grupos=("6201",)) == [fila]


def test_arreglos05_c_omision_y_duplicado_no_confirman_y_rechazo_no_cierra():
    """El adaptador C no adopta una omision ni un duplicado; un rechazo
    definitivo no es terminal para la operacion del archivo."""
    items = [_vivo("k-1")]
    ok = est.evento_reposicion_desde_lectura(
        items,
        id_propio="k-1",
        ad_group="ag-1",
        texto="termino propio",
        match="EXACT",
        destino="vivo",
        campaign_esperada="camp-1",
        bid_esperado=Decimal("1.0"),
    )
    assert ok == est.EventoCambio.OK_CONFIRMADO
    assert (
        est.evento_reposicion_desde_lectura(
            [],
            id_propio="k-1",
            ad_group="ag-1",
            texto="termino propio",
            match="EXACT",
            destino="vivo",
        )
        == est.EventoCambio.IDENTIDAD_DUDOSA
    )
    assert (
        est.evento_reposicion_desde_lectura(
            items + [_vivo("k-2")],
            id_propio="k-1",
            ad_group="ag-1",
            texto="termino propio",
            match="EXACT",
            destino="vivo",
        )
        == est.EventoCambio.IDENTIDAD_DUDOSA
    )
    assert est.resultado_terminal_reposicion(est.RESULTADO_REPOSICION_RECHAZO) is None
    assert est.resultado_terminal_reposicion(est.RESULTADO_OK) == est.RESULTADO_OK


# ---------------------------------------------------------------------------
# C.2 Reponer: intencion, ACK/ID durable y un solo POST
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_c_reponer_publica_intencion_antes_del_post_y_sella_intento_y_archivo():
    with _db("orbit_c05_ok") as (conn, extra):
        fila, _viejo, _texto = _semilla(conn)
        amazon = _Amazon()
        vistos: dict = {}

        def enviar(plataforma, profile, payload):
            with extra() as otra:
                vistos["abiertos"] = otra.execute(
                    "SELECT count(*) FROM keyword_reposicion_intento"
                    " WHERE archivo_id = %s AND finished_at IS NULL",
                    (fila,),
                ).fetchone()[0]
            return _enviar(amazon)(plataforma, profile, payload)

        resultados = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=enviar,
            owner="cli-1",
            ejecutar=True,
            dormir=lambda _s: None,
        )

        assert vistos["abiertos"] == 1, "la intencion es visible antes del HTTP"
        assert len(amazon.posts) == 1
        assert [r.resultado for r in resultados] == ["repuesto"]
        estado, nuevo, ack = conn.execute(
            "SELECT estado, repuesto_external, repuesto_ack FROM keyword_archivo_manual"
            " WHERE id = %s",
            (fila,),
        ).fetchone()
        assert (estado, nuevo) == ("repuesto", "k-1")
        assert ack is not None
        (intento,) = _intentos(conn, fila)
        assert (intento["resultado"], intento["external_id"]) == ("ok", "k-1")
        assert intento["ack"] is not None and intento["finished_at"] is not None

        # Repetir con la intencion ya confirmada: cero HTTP, solo lee.
        otra_vez = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=_enviar(amazon),
            owner="cli-2",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert otra_vez == []
        assert len(amazon.posts) == 1


@_skip_db
def test_arreglos05_c_ack_y_readback_caido_no_repite_post(monkeypatch):
    """La reproduccion `T writes test_11`: el readback falla tras el ACK; la
    segunda corrida relee por ID y no crea otra keyword. Al volver la lectura,
    el mismo intento se sella sin otro POST."""
    with _db("orbit_c05_readback") as (conn, _extra):
        dsn = conn.info.dsn
        fila, _viejo, _texto = _semilla(conn, lote="writes-lote")
        amazon = _Amazon()
        amazon.falla_lectura = 503
        _red(monkeypatch, amazon, dsn)

        for _ in range(2):
            _corre_cli(
                [
                    "archiva_inertes.py",
                    "--reponer",
                    "writes-lote",
                    "--acepto-mutacion-real",
                    "--go",
                    "prueba aislada",
                ]
            )
        assert len(amazon.posts) == 1, "un solo POST aunque el readback falle"
        (intento,) = _intentos(conn, fila)
        assert intento["resultado"] is None and intento["finished_at"] is None
        assert intento["ack"] is not None and intento["external_id"] == "k-1"

        _vence_lease(conn, fila)
        amazon.falla_lectura = None
        _corre_cli(
            [
                "archiva_inertes.py",
                "--reponer",
                "writes-lote",
                "--acepto-mutacion-real",
                "--go",
                "prueba aislada",
            ]
        )
        assert len(amazon.posts) == 1
        estado, nuevo = conn.execute(
            "SELECT estado, repuesto_external FROM keyword_archivo_manual WHERE id = %s",
            (fila,),
        ).fetchone()
        assert (estado, nuevo) == ("repuesto", "k-1")


@_skip_db
def test_arreglos05_c_claim_revalida_el_sello_bajo_cerrojo():
    """Dos CLI: si A sella antes de la reserva de B, B no reserva ni envia.
    La reserva revalida el estado del archivo y el ultimo `ok` bajo el
    cerrojo transaccional de la fila y la operacion."""
    with _db("orbit_c05_claim") as (conn, extra):
        fila, _viejo, _texto = _semilla(conn)
        lease = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)
        primero = store.abre_intento(
            conn,
            archivo_id=fila,
            tipo="reponer",
            owner="cli-a",
            payload={},
            lease_hasta=lease,
            estado_esperado="applied",
        )
        assert primero is not None
        store.guarda_ack_e_id(conn, primero, {"ok": True}, "k-1")
        with conn.transaction():
            assert store.sella(conn, primero, "ok")
            assert store.sella_archivo_repuesto(conn, fila, "k-1", {"ok": True})
        with extra() as otra:
            assert (
                store.abre_intento(
                    otra,
                    archivo_id=fila,
                    tipo="reponer",
                    owner="cli-b",
                    payload={},
                    lease_hasta=lease,
                    estado_esperado="applied",
                )
                is None
            ), "la fila ya no esta applied: no hay otro CREATE"
        reversa = store.abre_intento(
            conn,
            archivo_id=fila,
            tipo="reversa",
            owner="cli-a",
            payload={},
            lease_hasta=lease,
            estado_esperado="repuesto",
            repuesto_external_esperado="k-1",
        )
        assert reversa is not None
        store.guarda_ack_e_id(conn, reversa, {"ok": True}, "k-1")
        with conn.transaction():
            assert store.sella(conn, reversa, "ok")
        with extra() as otra:
            assert (
                store.abre_intento(
                    otra,
                    archivo_id=fila,
                    tipo="reversa",
                    owner="cli-b",
                    payload={},
                    lease_hasta=lease,
                    estado_esperado="repuesto",
                    repuesto_external_esperado="k-1",
                )
                is None
            ), "la reversa ya quedo ok: no hay otro DELETE"
        assert [i["resultado"] for i in _intentos(conn, fila)] == ["ok", "ok"]


@_skip_db
def test_arreglos05_c_dos_cli_no_crean_dos_objetos():
    """Con un lease vivo, el segundo CLI no envia. Vencido el lease, lo toma y
    concilia por lectura, sin otro POST."""
    with _db("orbit_c05_dos") as (conn, _extra):
        fila, _viejo, _texto = _semilla(conn)
        amazon = _Amazon([_vivo("k-1")])
        _abre_intento(conn, fila, owner="cli-a", ack={"ok": True}, external_id="k-1")

        primero = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=_enviar(amazon),
            owner="cli-b",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert amazon.posts == []
        assert primero and primero[0].resultado == "pendiente"
        assert _intentos(conn, fila)[0]["resultado"] is None

        _vence_lease(conn, fila)
        segundo = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=_enviar(amazon),
            owner="cli-b",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert amazon.posts == [], "el sucesor solo concilia"
        assert any(r.resultado == "repuesto" for r in segundo), segundo
        (intento,) = _intentos(conn, fila)
        assert intento["resultado"] == "ok" and intento["owner"] == "cli-b"


@_skip_db
def test_arreglos05_c_total_declarado_mayor_no_autoriza_ausencia():
    """Una pagina con keywords vacio y totalResults=1 no prueba ausencia: el
    intento sigue abierto y el segundo bucle de --reponer no envia otro POST."""
    with _db("orbit_c05_total") as (conn, _extra):
        fila, _viejo, _texto = _semilla(conn)
        amazon = _Amazon()
        amazon.total_declarado = 1
        _abre_intento(conn, fila, owner="cli-a")
        enviados: list[dict] = []

        def enviar(plataforma, profile, payload):
            enviados.append(payload)
            return _respuesta(amazon, "POST", "/sp/keywords", payload, "keywords")

        resultados = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=enviar,
            owner="cli-a",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert enviados == [], "la ausencia declarada parcial no autoriza POST"
        assert resultados and resultados[0].resultado == "pendiente", resultados
        (intento,) = _intentos(conn, fila)
        assert intento["resultado"] is None and intento["finished_at"] is None


@_skip_db
def test_arreglos05_c_ack_nulo_no_sella_rechazo():
    """Un 207 con cuerpo null no es rechazo: deja el intento abierto y la
    segunda corrida no repite el POST."""
    with _db("orbit_c05_ack_nulo") as (conn, _extra):
        fila, _viejo, _texto = _semilla(conn)
        amazon = _Amazon()
        amazon.post_cuerpo_nulo = True
        primero = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=_enviar(amazon),
            owner="cli-1",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert primero[0].resultado == "pendiente"
        (intento,) = _intentos(conn, fila)
        assert intento["resultado"] is None and intento["ack"]["status"] == 207

        segundo = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=_enviar(amazon),
            owner="cli-2",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert len(amazon.posts) == 1, "un ACK de forma inesperada no autoriza otro POST"
        assert segundo[0].resultado == "pendiente"
        assert _intentos(conn, fila)[0]["resultado"] is None


@_skip_db
@pytest.mark.parametrize("forma", ["omitido", "truncado", "duplicado"])
def test_arreglos05_c_lectura_incompleta_o_duplicada_no_autoriza_post(forma):
    with _db(f"orbit_c05_lectura_{forma}") as (conn, _extra):
        fila, _viejo, _texto = _semilla(conn)
        amazon = _Amazon([_vivo("k-1")])
        if forma == "omitido":
            amazon.keywords = []
        elif forma == "truncado":
            amazon.list_truncada = True
        else:
            amazon.keywords.append(_vivo("k-2"))
        _abre_intento(conn, fila, owner="cli-a", ack={"ok": True}, external_id="k-1")

        resultados = archivar.reconciliar_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            owner="cli-a",
        )
        assert amazon.posts == []
        assert resultados and resultados[0].resultado == "pendiente"
        assert _intentos(conn, fila)[0]["resultado"] is None

        # Solo una lectura completa y unica cierra el intento.
        if forma == "omitido":
            amazon.keywords = [_vivo("k-1")]
        elif forma == "truncado":
            amazon.list_truncada = False
        else:
            amazon.keywords = [_vivo("k-1")]
        cerrado = archivar.reconciliar_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            owner="cli-a",
        )
        assert any(r.resultado == "repuesto" for r in cerrado), cerrado


@_skip_db
def test_arreglos05_c_rechazo_definitivo_permite_otro_intento_registrado():
    with _db("orbit_c05_rechazo") as (conn, _extra):
        fila, _viejo, _texto = _semilla(conn)
        amazon = _Amazon()
        amazon.falla_post = 400
        primero = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=_enviar(amazon),
            owner="cli-1",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert primero[0].resultado == "fallo"
        (intento,) = _intentos(conn, fila)
        assert intento["resultado"] == est.RESULTADO_REPOSICION_RECHAZO

        amazon.falla_post = None
        segundo = archivar.reponer_keyword(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            enviar=_enviar(amazon),
            owner="cli-2",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert len(amazon.posts) == 2
        assert segundo[0].resultado == "repuesto"
        intentos = _intentos(conn, fila)
        assert [i["resultado"] for i in intentos] == [est.RESULTADO_REPOSICION_RECHAZO, "ok"]
        assert [i["seq"] for i in intentos] == [1, 2]


@_skip_db
@pytest.mark.parametrize("status", [401, 403])
def test_arreglos05_c_rechazo_de_identidad_aborta_sin_sellar(status):
    with _db(f"orbit_c05_ident_{status}") as (conn, _extra):
        fila, _viejo, _texto = _semilla(conn)
        amazon = _Amazon()
        amazon.falla_post = status
        with pytest.raises(archivar.AbortoFase):
            archivar.reponer_keyword(
                conn,
                lote="lote-c",
                cliente=_cliente(amazon),
                perfiles=PERFILES,
                enviar=_enviar(amazon),
                owner="cli-1",
                ejecutar=True,
                dormir=lambda _s: None,
            )
        (intento,) = _intentos(conn, fila)
        assert intento["resultado"] is None and intento["finished_at"] is None


# ---------------------------------------------------------------------------
# C.3 Reversa del ID repuesto propio
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_c_403_en_reversa_se_sella_y_permite_reintentar():
    """El 403 del DELETE queda en el ACK durable; al vencer el lease, la
    conciliacion lo sella como rechazo y la misma corrida envia el DELETE
    valido. Un 5xx sigue sin reenviar (prueba vecina)."""
    with _db("orbit_c05_403") as (conn, _extra):
        fila, _viejo, texto = _semilla(conn)
        _marca_repuesto(conn, fila, "k-1")
        amazon = _Amazon([_vivo("k-1", texto)])
        amazon.falla_delete = 403
        with pytest.raises(archivar.AbortoFase):
            archivar.revertir_reposicion(
                conn,
                lote="lote-c",
                cliente=_cliente(amazon),
                perfiles=PERFILES,
                eliminar=_eliminar(amazon),
                owner="cli-1",
                ejecutar=True,
                dormir=lambda _s: None,
            )
        (intento,) = _intentos(conn, fila)
        assert intento["resultado"] is None and intento["finished_at"] is None

        _vence_lease(conn, fila)
        amazon.falla_delete = None
        resultados = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-2",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert [r.resultado for r in resultados] == ["reintentable", "revertido"], resultados
        assert len(amazon.deletes) == 2
        assert [i["resultado"] for i in _intentos(conn, fila)] == [
            est.RESULTADO_REPOSICION_RECHAZO,
            "ok",
        ]
        assert _intentos(conn, fila)[0]["ack"]["status"] == 403, "el rechazo quedo durable"


@_skip_db
def test_arreglos05_c_reversa_con_ack_nulo_confirma_por_el_id_conocido():
    """Un 207 con cuerpo null no bloquea la reversa: el ID propio sale de la
    fila repuesta y la lectura confirma el archivado."""
    with _db("orbit_c05_reversa_ack_nulo") as (conn, _extra):
        fila, _viejo, texto = _semilla(conn)
        _marca_repuesto(conn, fila, "k-1")
        amazon = _Amazon([_vivo("k-1", texto)])
        amazon.delete_cuerpo_nulo = True
        resultados = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-1",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert [r.resultado for r in resultados] == ["revertido"], resultados
        assert len(amazon.deletes) == 1
        (intento,) = _intentos(conn, fila)
        assert intento["resultado"] == "ok"
        assert intento["ack"]["status"] == 207


@_skip_db
def test_arreglos05_c_reversa_archiva_solo_el_id_propio_y_no_repite():
    with _db("orbit_c05_reversa") as (conn, _extra):
        fila, viejo, texto = _semilla(conn)
        _marca_repuesto(conn, fila, "k-1")
        amazon = _Amazon([_vivo("k-1", texto), _vivo("ajena", texto)])
        resultados = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-1",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert [r.resultado for r in resultados] == ["revertido"]
        assert [d["keywordIdFilter"]["include"] for d in amazon.deletes] == [["k-1"]]
        assert [x["state"] for x in amazon.keywords if x["keywordId"] == "ajena"] == ["PAUSED"]
        assert [x["state"] for x in amazon.keywords if x["keywordId"] == "k-1"] == ["ARCHIVED"]
        original = conn.execute(
            "SELECT keyword_external, estado, repuesto_external FROM keyword_archivo_manual"
            " WHERE id = %s",
            (fila,),
        ).fetchone()
        assert original == (viejo, "repuesto", "k-1"), "no devuelve identidad a la original"

        otra = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-2",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert otra[0].resultado == "ya_revertido"
        assert len(amazon.deletes) == 1


@_skip_db
def test_arreglos05_c_reversa_sin_respuesta_no_reenvia_y_se_resuelve_leyendo():
    with _db("orbit_c05_reversa_perdida") as (conn, _extra):
        fila, _viejo, texto = _semilla(conn)
        _marca_repuesto(conn, fila, "k-1")
        amazon = _Amazon([_vivo("k-1", texto)])
        amazon.pierde_delete = True
        primero = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-1",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert primero[0].resultado == "pendiente"
        assert len(amazon.deletes) == 1
        _vence_lease(conn, fila)

        segundo = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-2",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert segundo[0].resultado == "revertido"
        assert len(amazon.deletes) == 1, "la caida no repite el envio"
        (intento,) = _intentos(conn, fila)
        assert intento["tipo"] == "reversa" and intento["resultado"] == "ok"


@_skip_db
def test_arreglos05_c_reversa_incierta_sin_efecto_no_repite_el_delete():
    with _db("orbit_c05_reversa_incierta") as (conn, _extra):
        fila, _viejo, texto = _semilla(conn)
        _marca_repuesto(conn, fila, "k-1")
        amazon = _Amazon([_vivo("k-1", texto)])
        amazon.falla_delete = 500
        primero = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-1",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert primero[0].resultado == "pendiente"
        _vence_lease(conn, fila)
        amazon.falla_delete = None
        segundo = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-2",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert segundo[0].resultado == "pendiente"
        assert len(amazon.deletes) == 1, "un DELETE incierto no autoriza otro"
        _vence_lease(conn, fila)
        # Si Amazon lo archivo por fuera, la lectura lo resuelve sin envio.
        for objeto in amazon.keywords:
            objeto["state"] = "ARCHIVED"
        tercero = archivar.revertir_reposicion(
            conn,
            lote="lote-c",
            cliente=_cliente(amazon),
            perfiles=PERFILES,
            eliminar=_eliminar(amazon),
            owner="cli-3",
            ejecutar=True,
            dormir=lambda _s: None,
        )
        assert tercero[0].resultado == "revertido"
        assert len(amazon.deletes) == 1


# ---------------------------------------------------------------------------
# C.1 Migracion 0097: ida / reversa / ida y roles reales
# ---------------------------------------------------------------------------


def _sonda(ruta: Path) -> str:
    cabecera = next(
        linea
        for linea in ruta.read_text(encoding="utf-8").splitlines()[:10]
        if linea.startswith("-- sonda-aplicada:")
    )
    return cabecera.split("-- sonda-aplicada:", 1)[1].strip().rstrip(";")


@_skip_db
def test_arreglos05_c_migracion_ida_reversa_e_ida_y_roles_reales():
    ida = MIGRACIONES / "0097_arreglos05_reposicion.sql"
    reversa = MIGRACIONES / "0097_reversa_arreglos05_reposicion.sql"
    assert ida.exists() and reversa.exists(), "falta la migracion 0097 o su reversa"
    with _db("orbit_c05_mig") as (conn, _extra):
        assert conn.execute(
            "SELECT to_regclass('public.keyword_reposicion_intento') IS NOT NULL,"
            " (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public'"
            "   AND tablename = 'keyword_reposicion_intento'"
            "   AND indexname = 'keyword_reposicion_intento_uno_abierto')"
        ).fetchone() == (True, 1)
        assert conn.execute(_sonda(ida)).fetchone() == (True,)
        assert conn.execute(
            "SELECT has_table_privilege('app_admin', 'keyword_reposicion_intento', 'INSERT'),"
            " has_column_privilege('app_admin', 'keyword_reposicion_intento', 'resultado',"
            "                      'UPDATE'),"
            " has_column_privilege('app_admin', 'keyword_reposicion_intento',"
            "                      'request_payload', 'UPDATE'),"
            " has_table_privilege('app_read', 'keyword_reposicion_intento', 'INSERT'),"
            " has_table_privilege('app_ingest', 'keyword_reposicion_intento', 'SELECT'),"
            " has_table_privilege('app_decide', 'keyword_reposicion_intento', 'SELECT')"
        ).fetchone() == (True, True, False, False, False, False)

        fila, _viejo, _texto = _semilla(conn)
        intento = _abre_intento(conn, fila, lease=dt.timedelta(minutes=5))
        # Ni el dueno de la tabla reescribe la identidad ni el payload.
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(
                "UPDATE keyword_reposicion_intento SET request_payload = '{\"x\": 1}'::jsonb"
                " WHERE id = %s",
                (intento,),
            )
        # El sello es una vez.
        conn.execute(
            "UPDATE keyword_reposicion_intento SET ack = '{\"ok\": true}'::jsonb,"
            " external_id = 'k-1' WHERE id = %s",
            (intento,),
        )
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(
                "UPDATE keyword_reposicion_intento SET ack = '{\"otro\": true}'::jsonb"
                " WHERE id = %s",
                (intento,),
            )
        # No hay dos intentos abiertos de la misma operacion.
        with pytest.raises(psycopg.errors.UniqueViolation):
            _abre_intento(conn, fila, owner="cli-b")
        # Un intento sellado no se actualiza ni se borra.
        conn.execute(
            "UPDATE keyword_reposicion_intento SET resultado = 'ok', finished_at = now()"
            " WHERE id = %s",
            (intento,),
        )
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(
                "UPDATE keyword_reposicion_intento SET lease_hasta = now() + interval '1 min'"
                " WHERE id = %s",
                (intento,),
            )
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM keyword_reposicion_intento WHERE id = %s", (intento,))
        # La reversa con historial aborta y no borra intentos.
        with pytest.raises(psycopg.errors.RestrictViolation, match="historial"):
            conn.execute(reversa.read_text(encoding="utf-8"))

    with _db("orbit_c05_mig_vacia") as (conn, _extra):
        # El instalador evalua la sonda ANTES de aplicar: sobre una base sin la
        # tabla no debe resolver ningun ::regclass de una relacion ausente.
        conn.execute("DROP TABLE keyword_reposicion_intento")
        assert conn.execute(_sonda(ida)).fetchone() == (False,)
        conn.execute(reversa.read_text(encoding="utf-8"))
        assert conn.execute(
            "SELECT to_regclass('public.keyword_reposicion_intento') IS NULL"
        ).fetchone() == (True,)
        conn.execute(ida.read_text(encoding="utf-8"))
        assert conn.execute(
            "SELECT to_regclass('public.keyword_reposicion_intento') IS NOT NULL"
        ).fetchone() == (True,)
        assert conn.execute(_sonda(ida)).fetchone() == (True,)
