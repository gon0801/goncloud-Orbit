"""ARREGLOS 05 B: reversas sobre identidad y valor comprobados.

Regresiones del bloque B de `plans/arreglos-05.md` (seccion B de DISENO). Cada
prueba corre el aplicador real sobre una base desechable con TODAS las
migraciones canonicas y la API de Ads simulada con `httpx.MockTransport`: cero
HTTP vivo y secretos siempre falsos.

B.1 cosecha: el ID del ACK es inmutable y se guarda antes del readback; la
lectura verifica ese ID y nada ajeno se adopta como propio ni se borra.
B.2 negative conciliado: la ausencia previa se guarda antes del POST y la
reversa manual resuelve la confirmacion por LIST, revalida el ID vivo y
confirma una sola vez.
B.3 reversa de bid: vuelve al valor leido antes del PUT, respeta un cambio
manual posterior y una reversa incierta se concilia antes de otro envio.

Las columnas nuevas del ledger se leen con `to_jsonb`: contra un esquema sin
la 0095 no aparecen y la prueba falla por su asercion, no por un error de
fixture.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from psycopg.types.json import Json
from test_apply import _aplicador as _aplicador_bid
from test_apply import _decision_bid, _write_client
from test_apply import _semilla as _semilla_bid
from test_apply_harvest import (
    ORIGEN_GRUPO,
    _decision_harvest,
    _decision_negative,
    _termino_obs,
)
from test_apply_harvest import (
    _aplicador as _aplicador_harvest,
)
from test_apply_harvest import (
    _semilla as _semilla_harvest,
)
from test_arreglos05_a_ciclo import _db, _Muerte
from test_cycle_apply import PERFIL_US
from test_fabrica_f2_hermanas import (
    ROLES_DISCOVERY,
    TERMINO_F2,
    _decision_harvest_grupo,
    _encola_fila,
    _grupo_listo,
)
from test_reversa_harvest import _job_done_mixto, _vivos_reversa
from test_schema import _postgres_obligatorio_ausente

from app import api_write, apply, apply_cola, apply_cola_ejecuta, apply_harvest
from app.ads.client import AdsApiError
from app.ads.config import AdsCredentials

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
TERMINO = "buen termino"
TERMINO_MALO = "termino malo"
_RECURSO = {"negative": "negativeKeywords", "keyword": "keywords"}
PASO_READBACK = apply_harvest._paso_readback

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# ---------------------------------------------------------------------------
# Doble de la API de Ads con estado: lo que Amazon "tiene" se siembra
# ---------------------------------------------------------------------------


class _Amazon:
    """API de Ads simulada. `negatives` y `keywords` son los objetos vivos con
    su identidad completa; `bids` es la puja por id externo. Cada POST de
    creacion o delete y cada PUT de puja lo actualiza.

    `pierde_respuesta` acepta la escritura en Amazon y pierde la respuesta
    (`negative` o `put_bid`): el caso que la conciliacion existe para
    resolver. `al_list` corre dentro de cada LIST de keywords,
    `al_crear_negative` dentro del POST de un negative y `al_put_bid` justo
    despues de cada PUT de puja. `rechaza_lista` responde ese status a toda
    lectura y `rechaza_put_bid` al PUT de puja; con `delete_negative` en
    `pierde_respuesta` el DELETE del negative se aplica y se pierde la
    respuesta. `list_truncada` sirve un nextToken que nunca se agota."""

    def __init__(self, *, negatives=(), keywords=(), bids=None):
        self.negatives: list[dict] = [dict(x) for x in negatives]
        self.keywords: list[dict] = [dict(x) for x in keywords]
        self.bids: dict[str, str] = {str(k): str(v) for k, v in (bids or {}).items()}
        self.vistos: list[httpx.Request] = []
        self.pierde_respuesta: set[str] = set()
        self.al_list = None
        self.al_crear_negative = None
        self.al_put_bid = None
        self.list_truncada = False
        self.rechaza_lista: int | None = None
        self.rechaza_put_bid: int | None = None
        self._seq = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access-b", "expires_in": 3600})
        if request.method == "GET" and request.url.path == "/v2/profiles":
            return httpx.Response(200, json={"profiles": [PERFIL_US]})
        self.vistos.append(request)
        cuerpo = json.loads(request.content) if request.content else {}
        path = request.url.path
        if path.endswith("/list") and self.rechaza_lista is not None:
            return httpx.Response(self.rechaza_lista, json={"code": "ACCESS_DENIED"})
        if path == "/sp/negativeKeywords/list":
            if self.list_truncada:
                return httpx.Response(200, json={"negativeKeywords": [], "nextToken": "misma"})
            return self._pagina("negativeKeywords", self.negatives, cuerpo)
        if path == "/sp/keywords/list":
            if self.al_list is not None:
                self.al_list()
            filas = list(self.keywords) if self.keywords else self._filas_de_puja()
            return self._pagina("keywords", filas, cuerpo)
        if path == "/sp/negativeKeywords" and request.method == "POST":
            return self._crea(request, "negative", cuerpo["negativeKeywords"][0])
        if path == "/sp/keywords" and request.method == "POST":
            return self._crea(request, "keyword", cuerpo["keywords"][0])
        if path == "/sp/negativeKeywords/delete":
            respuesta = self._borra(self.negatives, cuerpo)
            if "delete_negative" in self.pierde_respuesta:
                raise httpx.ReadTimeout("borrado en Amazon; respuesta perdida", request=request)
            return respuesta
        if path == "/sp/keywords/delete":
            return self._borra(self.keywords, cuerpo)
        if path == "/sp/keywords" and request.method == "PUT":
            return self._put(request, cuerpo["keywords"][0])
        if "bidRecommendations" in path:
            return httpx.Response(403, json={"code": "403"})
        raise AssertionError(f"request inesperado: {request.method} {path}")

    def _filas_de_puja(self) -> list[dict]:
        return [
            {"keywordId": ext, "bid": bid, "state": "ENABLED"} for ext, bid in self.bids.items()
        ]

    def _pagina(self, contenedor: str, filas: list[dict], pedido: dict) -> httpx.Response:
        dentro = {str(x) for x in (pedido.get("adGroupIdFilter") or {}).get("include", [])}
        if dentro:
            filas = [x for x in filas if str(x.get("adGroupId")) in dentro]
        return httpx.Response(200, json={contenedor: filas, "totalResults": len(filas)})

    def _crea(self, request: httpx.Request, clase: str, objeto: dict) -> httpx.Response:
        self._seq += 1
        kid = f"{clase[0]}-{self._seq}"
        store = self.negatives if clase == "negative" else self.keywords
        store.append({**objeto, "keywordId": kid})
        if clase == "negative" and self.al_crear_negative is not None:
            self.al_crear_negative(objeto, kid)
        if clase in self.pierde_respuesta:
            raise httpx.ReadTimeout("aceptada en Amazon; respuesta perdida", request=request)
        contenedor = "negativeKeywords" if clase == "negative" else "keywords"
        campo = "negativeKeywordId" if clase == "negative" else "keywordId"
        return httpx.Response(
            207,
            json={contenedor: {"success": [{"index": 0, campo: kid}], "error": []}},
        )

    def _borra(self, store: list[dict], cuerpo: dict) -> httpx.Response:
        campo = next(iter(cuerpo))
        ids = [str(x) for x in cuerpo[campo]["include"]]
        for objeto in store:
            if str(objeto["keywordId"]) in ids:
                objeto["state"] = "ARCHIVED"
        contenedor = "negativeKeywords" if campo == "negativeKeywordIdFilter" else "keywords"
        id_campo = "negativeKeywordId" if campo == "negativeKeywordIdFilter" else "keywordId"
        return httpx.Response(
            207, json={contenedor: {"success": [{id_campo: ids[0]}], "error": []}}
        )

    def _put(self, request: httpx.Request, objeto: dict) -> httpx.Response:
        if self.rechaza_put_bid is not None:
            return httpx.Response(self.rechaza_put_bid, json={"code": "ACCESS_DENIED"})
        self.bids[str(objeto["keywordId"])] = objeto["bid"]
        if self.al_put_bid is not None:
            self.al_put_bid(str(objeto["keywordId"]))
        if "put_bid" in self.pierde_respuesta:
            raise httpx.ReadTimeout("aplicada en Amazon; respuesta perdida", request=request)
        return httpx.Response(207, json={"keywords": {"success": [objeto], "error": []}})

    def _pedidos(self, metodo: str, path: str) -> list[dict]:
        return [
            json.loads(r.content) for r in self.vistos if r.method == metodo and r.url.path == path
        ]

    def creaciones(self, clase: str) -> list[dict]:
        return self._pedidos("POST", f"/sp/{_RECURSO[clase]}")

    def borrados(self, clase: str) -> list[str]:
        campo = "negativeKeywordIdFilter" if clase == "negative" else "keywordIdFilter"
        return [
            str(x)
            for cuerpo in self._pedidos("POST", f"/sp/{_RECURSO[clase]}/delete")
            for x in cuerpo[campo]["include"]
        ]

    def mutaciones(self) -> list[httpx.Request]:
        """Los HTTP que escriben en Amazon: ni lecturas ni el token LWA."""
        return [r for r in self.vistos if not r.url.path.endswith("/list")]

    def puestas(self) -> list[Decimal]:
        """El bid de cada PUT de puja, como monto."""
        return [
            Decimal(str(c["keywords"][0]["bid"]))
            for c in self._pedidos("PUT", "/sp/keywords")
            if "bid" in c["keywords"][0]
        ]

    def bid(self, ext: str = "7201") -> Decimal:
        return Decimal(str(self.bids[ext]))


def _negativo(term: str, *, grupo=ORIGEN_GRUPO, kid="pre") -> dict:
    return {
        "adGroupId": grupo,
        "campaignId": "7001",
        "keywordId": kid,
        "keywordText": term,
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }


def _hace(dias: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=dias)


def _red_por(monkeypatch, amazon: _Amazon) -> None:
    """Todo `httpx.Client` de la prueba habla con el doble, tambien el que
    construye la frontera manual de la API con su perfil y sus credenciales."""
    original = httpx.Client.__init__

    def init(client, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(amazon)
        original(client, *args, **kwargs)

    credenciales = AdsCredentials(
        client_id="fake-b-id", client_secret="fake-b-secret", refresh_token="fake-b-refresh"
    )
    monkeypatch.setattr(httpx.Client, "__init__", init)
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", lambda: credenciales)


def _ledger(conn, decision: int, tipo: str | None = None) -> list[dict]:
    """Filas del ledger de la decision como JSON: las columnas que no existen
    en el esquema (sin la 0095) simplemente no aparecen."""
    sql = "SELECT to_jsonb(a) FROM apply_attempt a WHERE decision_id = %s"
    params: tuple = (decision,)
    if tipo is not None:
        sql += " AND tipo = %s"
        params = (decision, tipo)
    return [f[0] for f in conn.execute(sql + " ORDER BY seq", params).fetchall()]


def _intento_keyword(conn, decision: int) -> dict:
    filas = [f for f in _ledger(conn, decision) if f["request_payload"].get("matchType") == "EXACT"]
    assert len(filas) == 1, "un solo intento de keyword por job"
    return filas[0]


def _job(conn, decision: int):
    return conn.execute(
        "SELECT id, fase, external_ids FROM harvest_job WHERE decision_id = %s", (decision,)
    ).fetchone()


def _cola(conn, decision: int):
    return conn.execute(
        "SELECT id, estado FROM apply_queue WHERE decision_id = %s ORDER BY id DESC LIMIT 1",
        (decision,),
    ).fetchone()


def _resumen(conn, decision: int):
    return conn.execute(
        "SELECT platform_ack, verify_ok FROM decision_application WHERE decision_id = %s",
        (decision,),
    ).fetchone()


def _aplicar_cosecha(conn, amazon: _Amazon, ids: dict, dec: int) -> None:
    ap = _aplicador_harvest(conn, amazon, ids["ciclo_ejec"])
    apply_cola.encola_cortes(conn, ap, ids["ciclo_dec"], modo_envelope="live", ahora=_hace(3))
    apply_cola.libera_vencidos(conn, "amazon_us", ahora=dt.datetime.now(dt.UTC), aplicador=ap)


# ---------------------------------------------------------------------------
# B.1 Cosecha: ID del ACK inmutable, lectura completa, propio frente a ajeno
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_b_cosecha_no_adopta_keyword_sustituida_ni_la_borra():
    """Entre el POST y la lectura de vuelta alguien archiva la keyword propia y
    deja viva otra con el mismo texto. El job conserva el ID del ACK como
    `keyword_id`, no transfiere la autoria a la ajena, no la borra y retira
    solo lo propio que sigue vivo (el negativo del origen)."""
    with _db("orbit_b05_sustituida") as (conn, _extra):
        ids = _semilla_harvest(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"], TERMINO)
        amazon = _Amazon()

        def sustituye() -> None:
            vivas = [
                x for x in amazon.keywords if x["state"] == "ENABLED" and x["keywordId"] != "ajena"
            ]
            if len(vivas) == 1:
                vivas[0]["state"] = "ARCHIVED"
                amazon.keywords.append({**vivas[0], "keywordId": "ajena", "state": "ENABLED"})

        amazon.al_list = sustituye
        _aplicar_cosecha(conn, amazon, ids, dec)

        propia = _intento_keyword(conn, dec)["ack"]["keywords"]["success"][0]["keywordId"]
        _jid, fase, ext = _job(conn, dec)
        assert ext.get("keyword_id") == propia, "el ID del ACK es inmutable"
        assert propia != "ajena"
        assert amazon.borrados("keyword") == [], (
            "cero DELETE: la propia ya no vive y la ajena no se toca"
        )
        assert [x["state"] for x in amazon.keywords if x["keywordId"] == "ajena"] == ["ENABLED"]
        assert fase == "failed"
        assert amazon.borrados("negative") == [str(ext["negative_id"])], "lo propio vivo se retira"


@_skip_db
def test_arreglos05_b_cosecha_con_identidad_dudosa_queda_pendiente_sin_borrar():
    """La lectura de vuelta no permite concluir quien es la keyword propia:
    dos vivas con su texto, o su ID vivo con otro texto. El job queda
    pendiente en su fase y no borra nada."""
    for nombre in ("dos_vivas", "otro_texto"):
        with _db(f"orbit_b05_dudosa_{nombre}") as (conn, _extra):
            ids = _semilla_harvest(conn)
            dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"], TERMINO)
            amazon = _Amazon()

            def altera(amazon=amazon, nombre=nombre) -> None:
                vivas = [x for x in amazon.keywords if x["state"] == "ENABLED"]
                if len(vivas) != 1:
                    return
                if nombre == "dos_vivas":
                    vivas[0]["state"] = "ARCHIVED"
                    for n in (1, 2):
                        amazon.keywords.append(
                            {**vivas[0], "keywordId": f"ajena-{n}", "state": "ENABLED"}
                        )
                else:
                    vivas[0]["keywordText"] = "otro texto"

            amazon.al_list = altera
            _aplicar_cosecha(conn, amazon, ids, dec)

            propia = _intento_keyword(conn, dec)["ack"]["keywords"]["success"][0]["keywordId"]
            _jid, fase, ext = _job(conn, dec)
            assert (fase, ext.get("keyword_id")) == ("exact_created", propia), nombre
            assert _cola(conn, dec)[1] == "applying", nombre
            assert amazon.borrados("keyword") == [] and amazon.borrados("negative") == [], nombre
            assert _resumen(conn, dec) is None, nombre


@_skip_db
def test_arreglos05_b_cosecha_guarda_el_ack_antes_del_readback_y_el_ciclo_siguiente_lo_sella(
    monkeypatch,
):
    """El proceso muere entre el ACK y la lectura de vuelta. Quedan durables
    el ACK con su ID (ledger abierto, job en exact_created). El ciclo
    siguiente completa leyendo: sin otro POST, sella `ok` y cierra el job."""
    with _db("orbit_b05_ack_durable") as (conn, _extra):
        ids = _semilla_harvest(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"], TERMINO)
        amazon = _Amazon()

        def muere(*_args, **_kwargs):
            raise _Muerte("el proceso murio entre el ACK y el readback")

        monkeypatch.setattr(apply_harvest, "_paso_readback", muere)
        with pytest.raises(_Muerte):
            _aplicar_cosecha(conn, amazon, ids, dec)

        intento = _intento_keyword(conn, dec)
        ack = intento["ack"]
        assert isinstance(ack, dict), "el ACK se guarda antes del readback"
        assert intento["finished_at"] is None, "el intento queda abierto hasta la lectura de vuelta"
        jid, fase, ext = _job(conn, dec)
        assert (fase, ext.get("keyword_id"), ext.get("keyword_creada")) == (
            "exact_created",
            ack["keywords"]["success"][0]["keywordId"],
            True,
        )
        assert _resumen(conn, dec) is None, "sin readback no hay confirmacion"

        monkeypatch.setattr(apply_harvest, "_paso_readback", PASO_READBACK)
        resumen = apply_harvest.reconcilia_harvest(
            conn, _aplicador_harvest(conn, amazon, ids["ciclo_ejec"]), "amazon_us"
        )
        assert resumen.jobs_done == 1
        assert len(amazon.creaciones("keyword")) == 1, "la recuperacion no repite el POST"
        sellada = _intento_keyword(conn, dec)
        assert (sellada["resultado"], sellada["ack"]) == ("ok", ack)
        assert _job(conn, dec)[1] == "done"
        assert _resumen(conn, dec) == ({"fuente": "list", "keywordId": ext["keyword_id"]}, True)


def _harvest_con_hermanas(conn) -> tuple[int, str, list, _Amazon, dict]:
    """Job de cosecha de grupo terminado, con la mezcla de
    `test_reversa_harvest._job_done_mixto`: keyword propia k-9, hermanas
    propias n-1 y n-3, hermana adoptada n-2 y negativo de origen propio n-0.
    El doble de Amazon arranca con todo vivo; la prueba lo altera."""
    setup = _grupo_listo(conn)
    jid, dec, roles = _job_done_mixto(conn, setup)
    _plataforma, term, _decision, pasos = apply_harvest.plan_reversa_harvest(conn, jid)
    vivos = _vivos_reversa(setup, roles)
    amazon = _Amazon(
        keywords=[x for x in vivos if x.get("_clase") == "keyword"],
        negatives=[x for x in vivos if x.get("_clase") != "keyword"],
    )
    return dec, term, pasos, amazon, {"setup": setup, "roles": roles}


@_skip_db
def test_arreglos05_b_reversa_manual_no_borra_keyword_sustituida_y_queda_pendiente():
    """Job terminado con keyword propia. En Amazon la propia esta archivada y
    vive otra con el mismo texto. La reversa manual se detiene en ese paso sin
    enviar nada: ni la ajena ni el negativo del origen (el orden sellado no
    retira el origen mientras la keyword propia no este confirmada)."""
    with _db("orbit_b05_rev_sustituida") as (conn, _extra):
        dec, term, pasos, amazon, _resto = _harvest_con_hermanas(conn)
        propia = amazon.keywords[0]
        propia["state"] = "ARCHIVED"
        amazon.keywords.append({**propia, "keywordId": "ajena", "state": "ENABLED"})

        ok, detalle = apply_harvest.ejecuta_reversa_harvest(
            conn, _write_client(amazon), dec, term, pasos
        )

        assert ok is False and "sustituida" in detalle
        assert amazon.mutaciones() == [], "pendiente: cero DELETE, de la ajena y del origen"
        assert [x["state"] for x in amazon.keywords if x["keywordId"] == "ajena"] == ["ENABLED"]
        ext = _job(conn, dec)[2]
        assert (ext["keyword_id"], ext["keyword_creada"]) == ("k-9", True)
        assert _ledger(conn, dec, "reversa") == [], "pendiente no deja intencion abierta"


@_skip_db
def test_arreglos05_b_reversa_manual_confirma_por_lectura_lo_propio_ya_retirado():
    """La keyword propia ya esta archivada y nadie ocupa su lugar: ese paso se
    confirma leyendo, sin DELETE, y la reversa sigue con los negativos propios
    (la hermana adoptada n-2 no se toca)."""
    with _db("orbit_b05_rev_retirada") as (conn, _extra):
        dec, term, pasos, amazon, _resto = _harvest_con_hermanas(conn)
        amazon.keywords[0]["state"] = "ARCHIVED"

        ok, detalle = apply_harvest.ejecuta_reversa_harvest(
            conn, _write_client(amazon), dec, term, pasos
        )

        assert (ok, detalle) == (True, "reversa: ok")
        assert amazon.borrados("keyword") == [], "lo que ya no vive no lleva DELETE"
        assert amazon.borrados("negative") == ["n-1", "n-3", "n-0"]
        assert [x["state"] for x in amazon.negatives if x["keywordId"] == "n-2"] == ["ENABLED"]
        confirmadas = _ledger(conn, dec, "reversa")
        assert [f["resultado"] for f in confirmadas] == ["ok"] * 4
        assert confirmadas[0]["ack"] == {"conciliado": True, "fuente": "list", "leido": "ausente"}
        assert [f["quota_cobrada"] for f in confirmadas] == [False] * 4

        ok2, detalle2 = apply_harvest.ejecuta_reversa_harvest(
            conn, _write_client(amazon), dec, term, pasos
        )

        assert (ok2, detalle2) == (True, "reversa: ok")
        assert len(amazon.mutaciones()) == 3, (
            "lo confirmado, por lectura o por DELETE, no se repite"
        )


@_skip_db
def test_arreglos05_b_reversa_manual_no_borra_hermana_sustituida():
    """El mismo resolver para las hermanas: la hermana propia n-1 fue
    reemplazada por otra con el mismo texto en su grupo. La reversa retira la
    keyword propia, se detiene en la hermana sin tocar a la ajena y deja el
    resto de lo propio y la adoptada intactos."""
    with _db("orbit_b05_rev_hermana") as (conn, _extra):
        dec, term, pasos, amazon, resto = _harvest_con_hermanas(conn)
        sustituida = next(x for x in amazon.negatives if x["keywordId"] == "n-1")
        sustituida["state"] = "ARCHIVED"
        amazon.negatives.append({**sustituida, "keywordId": "ajena", "state": "ENABLED"})
        assert any(
            paso.objeto_id == "n-1" and paso.ad_group_ext == sustituida["adGroupId"]
            for paso in pasos
        )

        ok, detalle = apply_harvest.ejecuta_reversa_harvest(
            conn, _write_client(amazon), dec, term, pasos
        )

        assert ok is False and "sustituida" in detalle
        assert amazon.borrados("keyword") == ["k-9"]
        assert amazon.borrados("negative") == [], "ni la hermana ajena ni lo que sigue en el orden"
        assert [x["state"] for x in amazon.negatives if x["keywordId"] == "ajena"] == ["ENABLED"]
        assert [f["resultado"] for f in _ledger(conn, dec, "reversa")] == ["ok"]
        assert resto["roles"], "el plan conserva el roster de la prueba original"


# ---------------------------------------------------------------------------
# B.2 Negative conciliado: evidencia previa durable y reversa ejecutable
# ---------------------------------------------------------------------------


def _corte_negative(conn) -> tuple[dict, int]:
    ids = _semilla_harvest(conn)
    for offset in range(12, 42):
        _termino_obs(
            conn,
            ids["run"],
            ids["ag"],
            TERMINO_MALO,
            _hace(offset).date(),
            clicks=100,
            cost="100",
            orders=0,
            ad_revenue="0",
        )
    dec = _decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"], term=TERMINO_MALO)
    return ids, dec


def _encolar_y_soltar(conn, amazon: _Amazon, ids: dict):
    ap = _aplicador_harvest(conn, amazon, ids["ciclo_ejec"])
    apply_cola.encola_cortes(conn, ap, ids["ciclo_dec"], modo_envelope="live", ahora=_hace(3))
    apply_cola.libera_vencidos(conn, "amazon_us", ahora=dt.datetime.now(dt.UTC), aplicador=ap)
    return ap


def _reversa_negative_api(conn, qid: int) -> dict:
    return api_write.reversa_negative(
        _token="simulada", conn=conn, cuerpo=api_write.CuerpoReversaNegative(queue_id=qid)
    )


@_skip_db
def test_arreglos05_b_negative_guarda_la_ausencia_previa_antes_del_post():
    """Antes del POST del corte negative, una lectura completa demuestra que la
    identidad no esta viva y esa evidencia nace con la fila del ledger, visible
    desde otra conexion cuando el HTTP sale."""
    with _db("orbit_b05_ausencia_previa") as (conn, extra):
        ids, dec = _corte_negative(conn)
        amazon = _Amazon()
        vistas = []

        def al_postear(_objeto: dict, _kid: str) -> None:
            with extra() as otra:
                vistas.extend(
                    otra.execute(
                        "SELECT to_jsonb(a) FROM apply_attempt a WHERE decision_id = %s", (dec,)
                    ).fetchall()
                )

        amazon.al_crear_negative = al_postear
        _encolar_y_soltar(conn, amazon, ids)

        assert len(amazon.creaciones("negative")) == 1
        assert len(vistas) == 1, "la intencion es durable y unica cuando el HTTP sale"
        fila = vistas[0][0]
        assert fila["finished_at"] is None
        assert "ausencia_previa" in fila, "la evidencia previa nace con la fila del ledger"
        assert fila["ausencia_previa"] == {
            "lectura": "/sp/negativeKeywords/list",
            "plataforma": "amazon_us",
            "identidad": {
                "adGroupId": ORIGEN_GRUPO,
                "keywordText": TERMINO_MALO,
                "matchType": "NEGATIVE_EXACT",
            },
        }
        assert _cola(conn, dec)[1] == "applied"


@_skip_db
def test_arreglos05_b_lectura_previa_incompleta_no_autoriza_el_post():
    """La lectura previa del negative no llega al fin: cero POST, la fila queda
    en applying y nada nace en el ledger."""
    with _db("orbit_b05_previa_truncada") as (conn, _extra):
        ids, dec = _corte_negative(conn)
        amazon = _Amazon()
        amazon.list_truncada = True
        _encolar_y_soltar(conn, amazon, ids)

        assert amazon.creaciones("negative") == []
        assert _ledger(conn, dec) == []
        assert _cola(conn, dec)[1] == "applying"


@_skip_db
def test_arreglos05_b_negative_preexistente_no_se_envia_ni_se_borra(monkeypatch):
    """El termino ya esta negado en el grupo por mano ajena. Orbit no manda el
    POST, no adopta ese objeto y su reversa no lo alcanza."""
    with _db("orbit_b05_preexistente") as (conn, _extra):
        ids, dec = _corte_negative(conn)
        amazon = _Amazon(negatives=[_negativo(TERMINO_MALO, kid="ajena")])
        _encolar_y_soltar(conn, amazon, ids)

        assert amazon.creaciones("negative") == [], (
            "un objeto preexistente no se adopta ni se reenvia"
        )
        qid, estado = _cola(conn, dec)
        assert estado == "failed" and _resumen(conn, dec) is None
        _red_por(monkeypatch, amazon)
        with pytest.raises(HTTPException) as excepcion:
            _reversa_negative_api(conn, qid)
        assert excepcion.value.status_code == 409
        assert amazon.borrados("negative") == []


@_skip_db
def test_arreglos05_b_negative_reconciliado_tiene_reversa_ejecutable_y_se_confirma_una_vez(
    monkeypatch,
):
    """Amazon acepta el POST y la respuesta se pierde. La conciliacion confirma
    por LIST guardando el ID y su procedencia. La reversa manual resuelve esa
    confirmacion, revalida el ID vivo, registra su intento y lo retira una vez;
    el segundo intento no abre HTTP."""
    with _db("orbit_b05_reconciliado") as (conn, _extra):
        ids, dec = _corte_negative(conn)
        amazon = _Amazon()
        amazon.pierde_respuesta.add("negative")
        ap = _encolar_y_soltar(conn, amazon, ids)
        qid, estado = _cola(conn, dec)
        assert (estado, len(amazon.negatives)) == ("applying", 1)

        apply_harvest.reconcilia_harvest(conn, ap, "amazon_us")

        assert _cola(conn, dec)[1] == "applied"
        intento = _ledger(conn, dec, "normal")[0]
        assert (intento["resultado"], intento["ack"]) == ("ok:reconciliado", None), (
            "no se falsifica un ACK remoto"
        )
        ack, verify = _resumen(conn, dec)
        assert verify is True
        assert "procedencia" in ack, "la confirmacion por LIST declara su procedencia"
        assert (ack["fuente"], ack["keywordId"], ack["procedencia"]) == (
            "list",
            "n-1",
            "efecto_reconciliado",
        )

        _red_por(monkeypatch, amazon)
        assert _reversa_negative_api(conn, qid)["confirmada"] is True
        assert amazon.borrados("negative") == ["n-1"]
        assert [(f["resultado"], f["quota_cobrada"]) for f in _ledger(conn, dec, "reversa")] == [
            ("ok", False)
        ]

        vistos_antes = len(amazon.vistos)
        with pytest.raises(HTTPException) as excepcion:
            _reversa_negative_api(conn, qid)
        assert excepcion.value.status_code == 409
        assert len(amazon.vistos) == vistos_antes, "la segunda reversa confirmada no abre HTTP"
        assert len(_ledger(conn, dec, "reversa")) == 1


@_skip_db
def test_arreglos05_b_sin_evidencia_previa_el_negative_no_se_confirma_por_conciliacion():
    """Un intento abierto que nacio sin evidencia de ausencia no prueba que el
    negative vivo sea efecto de Orbit: queda pendiente, sin confirmar y sin
    reenviar."""
    with _db("orbit_b05_sin_evidencia") as (conn, _extra):
        ids, dec = _corte_negative(conn)
        ap = _aplicador_harvest(conn, _Amazon(), ids["ciclo_ejec"])
        apply_cola.encola_cortes(conn, ap, ids["ciclo_dec"], modo_envelope="live", ahora=_hace(3))
        qid, _estado = _cola(conn, dec)
        payload = apply_cola_ejecuta.fila_cola(conn, qid).request_payload
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = now() WHERE id = %s", (qid,)
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = now() WHERE id = %s", (qid,)
        )
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 1, 'normal', %s, true)",
            (dec, Json(payload)),
        )
        amazon = _Amazon(negatives=[_negativo(TERMINO_MALO, kid="n-1")])

        apply_harvest.reconcilia_harvest(
            conn, _aplicador_harvest(conn, amazon, ids["ciclo_ejec"]), "amazon_us"
        )

        assert _resumen(conn, dec) is None, "sin evidencia previa no hay confirmacion"
        assert _cola(conn, dec)[1] == "applying"
        assert amazon.creaciones("negative") == [] and amazon.borrados("negative") == []
        assert _ledger(conn, dec, "normal")[0]["resultado"] is None


@_skip_db
@_skip_db
def test_arreglos05_b_con_varios_candidatos_el_negative_reconciliado_queda_pendiente():
    """Dos negativos vivos con la misma identidad tras un POST de respuesta
    perdida: no se elige uno y la fila sigue pendiente, sin reenviar."""
    with _db("orbit_b05_varios") as (conn, _extra):
        ids, dec = _corte_negative(conn)
        amazon = _Amazon()
        amazon.pierde_respuesta.add("negative")
        ap = _encolar_y_soltar(conn, amazon, ids)
        amazon.negatives.append(_negativo(TERMINO_MALO, kid="n-9"))

        apply_harvest.reconcilia_harvest(conn, ap, "amazon_us")

        assert _resumen(conn, dec) is None and _cola(conn, dec)[1] == "applying"
        assert len(amazon.creaciones("negative")) == 1, "solo el POST cuya respuesta se perdio"
        assert _ledger(conn, dec, "normal")[0]["resultado"] is None


@_skip_db
@pytest.mark.parametrize(
    ("modo", "confirmada", "borrados"),
    [("id_cambiado", False, []), ("duplicado", True, ["n-1"])],
)
def test_arreglos05_b_reversa_del_negative_respeta_lo_que_no_es_el_id_acreditado(
    monkeypatch, modo, confirmada, borrados
):
    """Tras un apply normal: si el ID propio fue reemplazado por otro con el
    mismo texto, la reversa queda pendiente y no borra nada. Si ademas del
    propio vive un duplicado, se borra solo el propio, que es el unico que el
    ledger acredita; el otro no se toca."""
    with _db(f"orbit_b05_{modo}") as (conn, _extra):
        ids, dec = _corte_negative(conn)
        amazon = _Amazon()
        _encolar_y_soltar(conn, amazon, ids)
        qid, estado = _cola(conn, dec)
        assert estado == "applied"
        if modo == "id_cambiado":
            amazon.negatives[0]["state"] = "ARCHIVED"
        amazon.negatives.append(_negativo(TERMINO_MALO, kid="n-9"))

        _red_por(monkeypatch, amazon)
        resultado = _reversa_negative_api(conn, qid)

        assert resultado["confirmada"] is confirmada
        assert amazon.borrados("negative") == borrados
        assert [x["state"] for x in amazon.negatives if x["keywordId"] == "n-9"] == ["ENABLED"]
        assert [f["resultado"] for f in _ledger(conn, dec, "reversa")] == (
            [] if not confirmada else ["ok"]
        )


# ---------------------------------------------------------------------------
# B.3 Reversa de bid: valor leido antes del PUT, intervencion posterior, crash
# ---------------------------------------------------------------------------


def _puja_aplicada(conn, amazon: _Amazon, *, viejo: str) -> int:
    """Ciclo que aplica UNA puja de 2.00 sobre la hoja 7201 con el doble dado.
    `viejo` es el bid que la cache crea al decidir."""
    ids = _semilla_bid(conn, mode_ciclo_dec="live")
    dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"], old=viejo, new="2.00")
    _aplicador_bid(conn, amazon, ids["ciclo_ejec"]).aplica_bids(
        apply.bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live"
    )
    assert _resumen(conn, dec) is not None and _resumen(conn, dec)[1] is True
    assert amazon.puestas() == [Decimal("2.00")]
    return dec


def _reversa_bid_api(conn, decision: int) -> dict:
    return api_write.reversa_bid(
        _token="simulada", conn=conn, cuerpo=api_write.CuerpoReversaBid(decision_id=decision)
    )


def _pujas_de_reversa(amazon: _Amazon) -> list[Decimal]:
    return amazon.puestas()[1:]


@_skip_db
def test_arreglos05_b_la_puja_guarda_el_bid_leido_antes_del_put():
    """El valor que Amazon tenia justo antes del PUT viaja en la fila del ledger
    del intento, con la precision leida."""
    with _db("orbit_b05_bid_previo") as (conn, _extra):
        amazon = _Amazon(bids={"7201": "1.008"})
        dec = _puja_aplicada(conn, amazon, viejo="1.004")

        leido = _ledger(conn, dec, "normal")[0].get("bid_previo")
        assert leido is not None and Decimal(str(leido)) == Decimal("1.0080"), (
            "el ledger conserva lo leido antes del PUT, no el bid de la cache (1.0040)"
        )


@_skip_db
def test_arreglos05_b_reversa_de_bid_vuelve_al_valor_leido_antes_del_put(monkeypatch):
    """La cache traia 1.004 y Amazon tenia 1.008 antes del PUT (dentro de la
    tolerancia de cadena). La reversa vuelve a lo leido: 1.01, no 1.00."""
    with _db("orbit_b05_rev_bid") as (conn, _extra):
        amazon = _Amazon(bids={"7201": "1.008"})
        dec = _puja_aplicada(conn, amazon, viejo="1.004")
        _red_por(monkeypatch, amazon)

        resultado = _reversa_bid_api(conn, dec)

        assert resultado["confirmada"] is True
        assert _pujas_de_reversa(amazon) == [Decimal("1.01")]
        assert amazon.bid() == Decimal("1.01")
        reversa = _ledger(conn, dec, "reversa")[0]
        assert (reversa["resultado"], reversa["quota_cobrada"]) == ("ok", False)


@_skip_db
def test_arreglos05_b_reversa_de_bid_respeta_un_cambio_manual_posterior(monkeypatch):
    """Tras aplicar 2.00 el dueno pone 3.00 a mano. La reversa no lo pisa."""
    with _db("orbit_b05_rev_manual") as (conn, _extra):
        amazon = _Amazon(bids={"7201": "1.00"})
        dec = _puja_aplicada(conn, amazon, viejo="1.00")
        amazon.bids["7201"] = "3.00"
        _red_por(monkeypatch, amazon)

        resultado = _reversa_bid_api(conn, dec)

        assert resultado["confirmada"] is False
        assert amazon.puestas() == [Decimal("2.00")], "solo la puja original; la reversa no envia"
        assert amazon.bid() == Decimal("3.00")


@_skip_db
def test_arreglos05_b_reversa_de_bid_con_respuesta_perdida_no_duplica_el_put(monkeypatch):
    """El PUT de reversa aplica y la respuesta se pierde. El intento queda
    abierto; el intento siguiente lo resuelve leyendo, sin otro PUT."""
    with _db("orbit_b05_rev_perdida") as (conn, _extra):
        amazon = _Amazon(bids={"7201": "1.00"})
        dec = _puja_aplicada(conn, amazon, viejo="1.00")
        _red_por(monkeypatch, amazon)
        amazon.pierde_respuesta.add("put_bid")

        with pytest.raises(AdsApiError):
            _reversa_bid_api(conn, dec)
        abierta = _ledger(conn, dec, "reversa")[0]
        assert (abierta["resultado"], abierta["finished_at"]) == (None, None)
        assert amazon.bid() == Decimal("1.00")
        amazon.pierde_respuesta.clear()

        resultado = _reversa_bid_api(conn, dec)

        assert resultado["confirmada"] is True
        assert _pujas_de_reversa(amazon) == [Decimal("1.00")], "un solo PUT de reversa"
        sellada = _ledger(conn, dec, "reversa")[0]
        assert (sellada["resultado"], sellada["ack"]["conciliado"]) == ("ok", True)
        assert len(_ledger(conn, dec, "reversa")) == 1


@_skip_db
def test_arreglos05_b_reversa_de_bid_incierta_que_no_aplico_se_lee_y_se_reenvia_una_vez(
    monkeypatch,
):
    """El PUT de reversa se pierde sin aplicar. El intento siguiente lo lee,
    lo cuenta y recien entonces reenvia: dos PUT de reversa en total."""
    with _db("orbit_b05_rev_no_aplico") as (conn, _extra):
        amazon = _Amazon(bids={"7201": "1.00"})
        dec = _puja_aplicada(conn, amazon, viejo="1.00")
        _red_por(monkeypatch, amazon)
        original = _Amazon._put

        def pierde_sin_aplicar(self, request, objeto):
            if "put_bid" in self.pierde_respuesta:
                raise httpx.ReadTimeout("no llego a Amazon", request=request)
            return original(self, request, objeto)

        monkeypatch.setattr(_Amazon, "_put", pierde_sin_aplicar)
        amazon.pierde_respuesta.add("put_bid")
        with pytest.raises(AdsApiError):
            _reversa_bid_api(conn, dec)
        assert amazon.bid() == Decimal("2.00")
        amazon.pierde_respuesta.clear()

        resultado = _reversa_bid_api(conn, dec)

        assert (resultado["confirmada"], amazon.bid()) == (True, Decimal("1.00"))
        assert _pujas_de_reversa(amazon) == [Decimal("1.00"), Decimal("1.00")]
        assert [f["resultado"] for f in _ledger(conn, dec, "reversa")] == ["ok:no_aplicado", "ok"]


@_skip_db
@pytest.mark.parametrize("donde", ["lectura_previa", "put"])
def test_arreglos05_b_rechazo_de_identidad_en_la_reversa_de_bid_es_un_resultado(monkeypatch, donde):
    """Un 403 de Amazon durante la reversa manual de bid, en la lectura previa
    o en el PUT, es un resultado de la frontera (`confirmada: false`), no un
    500. Detras del rechazo no sale otra escritura."""
    with _db(f"orbit_b05_rev_bid_403_{donde}") as (conn, _extra):
        amazon = _Amazon(bids={"7201": "1.00"})
        dec = _puja_aplicada(conn, amazon, viejo="1.00")
        _red_por(monkeypatch, amazon)
        puestas = len(amazon.puestas())
        if donde == "lectura_previa":
            amazon.rechaza_lista = 403
        else:
            amazon.rechaza_put_bid = 403

        resultado = _reversa_bid_api(conn, dec)

        assert resultado["confirmada"] is False
        reversa = _ledger(conn, dec, "reversa")
        if donde == "lectura_previa":
            assert len(amazon.puestas()) == puestas, "sin lectura no sale el PUT"
            assert reversa == [], "sin lectura concluyente no nace intento de reversa"
        else:
            assert len(amazon.puestas()) == puestas + 1, "solo el PUT rechazado: ninguno despues"
            assert len(reversa) == 1 and reversa[0]["resultado"].startswith("fallo http 403")
        assert amazon.bid() == Decimal("2.00"), "la puja aplicada no cambia"


@_skip_db
def test_arreglos05_b_reversa_del_negative_con_delete_perdido_se_resuelve_leyendo(monkeypatch):
    """El DELETE del negative se aplica en Amazon y la respuesta se pierde. El
    intento de reversa queda abierto; el intento siguiente lo resuelve leyendo,
    sin otro DELETE."""
    with _db("orbit_b05_rev_neg_perdida") as (conn, _extra):
        ids, dec = _corte_negative(conn)
        amazon = _Amazon()
        _encolar_y_soltar(conn, amazon, ids)
        qid, estado = _cola(conn, dec)
        assert estado == "applied"
        _red_por(monkeypatch, amazon)
        amazon.pierde_respuesta.add("delete_negative")

        with pytest.raises(AdsApiError):
            _reversa_negative_api(conn, qid)
        abierta = _ledger(conn, dec, "reversa")[0]
        assert abierta["finished_at"] is None, "el intento queda abierto"
        assert [x["state"] for x in amazon.negatives] == ["ARCHIVED"]
        amazon.pierde_respuesta.clear()
        mutaciones_antes = len(amazon.mutaciones())

        assert _reversa_negative_api(conn, qid)["confirmada"] is True

        assert len(amazon.borrados("negative")) == 1, "un solo DELETE en total"
        assert len(amazon.mutaciones()) == mutaciones_antes, "el segundo intento no escribe"
        sellada = _ledger(conn, dec, "reversa")[0]
        assert (sellada["resultado"], sellada["ack"]["conciliado"]) == ("ok", True)
        assert len(_ledger(conn, dec, "reversa")) == 1


@_skip_db
def test_arreglos05_b_cosecha_cuya_keyword_propia_fue_archivada_cierra_sin_borrar_ajenos():
    """La keyword propia aparece ARCHIVED en la lectura de vuelta y nadie ocupa
    su lugar: el intento se sella como archivado, el job cierra fallido y la
    reversa retira solo el negativo propio. Ningun DELETE de keyword."""
    with _db("orbit_b05_archivada") as (conn, _extra):
        ids = _semilla_harvest(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"], TERMINO)
        amazon = _Amazon()

        def archiva() -> None:
            for x in amazon.keywords:
                if x["state"] == "ENABLED":
                    x["state"] = "ARCHIVED"

        amazon.al_list = archiva
        _aplicar_cosecha(conn, amazon, ids, dec)

        _jid, fase, ext = _job(conn, dec)
        assert _intento_keyword(conn, dec)["resultado"] == "fallo:entidad_archived"
        assert fase == "failed"
        assert amazon.borrados("keyword") == []
        assert amazon.borrados("negative") == [str(ext["negative_id"])]


@_skip_db
def test_arreglos05_b_reversa_de_bid_que_diverge_al_volver_sella_la_etiqueta_unificada(monkeypatch):
    """Control (ya pasaba en el padre; aqui conserva el contrato QW1 en el
    camino que sigue siendo alcanzable): si tras el PUT de reversa Amazon
    queda en otro valor, el intento se sella `fallo:divergencia_readback`, la
    misma etiqueta del apply, y la reversa no se da por buena."""
    with _db("orbit_b05_rev_diverge") as (conn, _extra):
        amazon = _Amazon(bids={"7201": "1.00"})
        dec = _puja_aplicada(conn, amazon, viejo="1.00")
        _red_por(monkeypatch, amazon)
        puestas = len(amazon.puestas())

        def amazon_deja_otro(_ext: str) -> None:
            amazon.bids["7201"] = "1.50"

        amazon.al_put_bid = amazon_deja_otro

        resultado = _reversa_bid_api(conn, dec)

        assert resultado["confirmada"] is False
        assert amazon.puestas()[puestas:] == [Decimal("1.00")], "solo salio el PUT de reversa"
        assert [f["resultado"] for f in _ledger(conn, dec, "reversa")] == [
            "fallo:divergencia_readback"
        ]
        assert amazon.bid() == Decimal("1.50")


def _abiertos(conn, decision: int) -> list[dict]:
    """Intentos del job todavia sin sello."""
    return conn.execute(
        "SELECT to_jsonb(a) FROM apply_attempt a"
        " WHERE decision_id = %s AND finished_at IS NULL ORDER BY seq",
        (decision,),
    ).fetchall()


def _sin_abiertos_con_evidencia(conn) -> int:
    """Lo que la reversa de la 0095 revisa antes de seguir."""
    return conn.execute(
        "SELECT count(*) FROM apply_attempt"
        " WHERE finished_at IS NULL AND (bid_previo IS NOT NULL OR ausencia_previa IS NOT NULL)"
    ).fetchone()[0]


def _cosecha_grupo(conn) -> tuple[dict, int]:
    """Cosecha de grupo (con hermanas) lista para correr por la cola."""
    setup = _grupo_listo(conn)
    exacta = setup["roles"]["category_exact"]
    dec = _decision_harvest_grupo(
        conn,
        setup["ciclo_dec"],
        setup["config"],
        setup["origen"]["ag"],
        grupo_id=setup["grupo_id"],
        exacta_camp_ext=exacta["camp_ext"],
        exacta_ag_ext=exacta["ag_ext"],
    )
    return setup, dec


@_skip_db
def test_arreglos05_b_cosecha_con_roster_imposible_sella_la_creacion_verificada():
    """La lectura de vuelta verifica la keyword propia, pero al grupo le falta
    un rol de descubrimiento: el job cierra fallido y compensa. El intento de
    creacion, que ya trae su ACK, se sella como aplicado; la compensacion queda
    en sus propias filas. Ningun intento queda abierto."""
    with _db("orbit_b05_roster_imposible") as (conn, _extra):
        setup, dec = _cosecha_grupo(conn)
        faltante = next(r for r in ROLES_DISCOVERY if r != setup["origen_rol"])
        conn.execute(
            "DELETE FROM campana_grupo_rol WHERE grupo_id = %s AND rol = %s",
            (setup["grupo_id"], faltante),
        )
        amazon = _Amazon()
        ap = _aplicador_harvest(conn, amazon, setup["ciclo_ejec"])
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        apply_cola.libera_vencidos(conn, "amazon_us", ahora=dt.datetime.now(dt.UTC), aplicador=ap)

        assert _job(conn, dec)[1] == "failed"
        assert (
            conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
            == "failed"
        )
        creacion = [
            f[0]
            for f in conn.execute(
                "SELECT to_jsonb(a) FROM apply_attempt a WHERE decision_id = %s"
                " AND tipo = 'normal' ORDER BY seq",
                (dec,),
            ).fetchall()
        ]
        keyword = next(f for f in creacion if f["request_payload"]["matchType"] == "EXACT")
        assert (keyword["resultado"], keyword["finished_at"] is not None) == ("ok", True), (
            "la creacion verificada se sella como aplicada, con su ACK"
        )
        assert (
            keyword["ack"]["keywords"]["success"][0]["keywordId"]
            == _job(conn, dec)[2]["keyword_id"]
        )
        compensacion = [f["resultado"] for f in _ledger(conn, dec, "reversa")]
        assert compensacion == ["ok", "ok"], "la compensacion queda en sus propias filas"
        assert amazon.borrados("keyword") and amazon.borrados("negative")
        assert _abiertos(conn, dec) == [], "ningun intento queda abierto"
        assert _sin_abiertos_con_evidencia(conn) == 0, "la reversa de la 0095 puede seguir"


def _cosecha_con_ack_durable(conn, monkeypatch) -> tuple[dict, int, _Amazon]:
    """Cosecha de plataforma interrumpida entre el ACK y la lectura de vuelta:
    el job queda en exact_created con el intento de creacion abierto y su ACK."""
    ids = _semilla_harvest(conn)
    dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"], TERMINO)
    amazon = _Amazon()

    def muere(*_args, **_kwargs):
        raise _Muerte("el proceso murio entre el ACK y el readback")

    monkeypatch.setattr(apply_harvest, "_paso_readback", muere)
    with pytest.raises(_Muerte):
        _aplicar_cosecha(conn, amazon, ids, dec)
    monkeypatch.setattr(apply_harvest, "_paso_readback", PASO_READBACK)
    assert _job(conn, dec)[1] == "exact_created"
    abiertos = _abiertos(conn, dec)
    assert len(abiertos) == 1 and isinstance(abiertos[0][0]["ack"], dict)
    return ids, dec, amazon


def _creacion_sellada_ok(conn, dec: int) -> None:
    creacion = [f for f in _ledger(conn, dec, "normal") if f["request_payload"].get("bid")]
    assert [(f["resultado"], f["finished_at"] is not None) for f in creacion] == [("ok", True)], (
        "el intento con ACK guardado se sella como aplicado"
    )
    assert _abiertos(conn, dec) == [], "ningun intento queda abierto"
    assert _sin_abiertos_con_evidencia(conn) == 0, "la reversa de la 0095 puede seguir"


@_skip_db
def test_arreglos05_b_cosecha_cerrada_sin_contexto_sella_la_creacion_con_ack(monkeypatch):
    """El proceso muere entre el ACK y la lectura de vuelta y, en la corrida que
    lo retoma, el destino ya no se puede resolver. El job cierra fallido y el
    intento de creacion, que ya trae su ACK, no queda abierto."""
    with _db("orbit_b05_cierra_sin_contexto") as (conn, _extra):
        ids, dec, amazon = _cosecha_con_ack_durable(conn, monkeypatch)
        monkeypatch.setattr(
            apply_harvest,
            "_contexto",
            lambda *_a, **_k: (_ for _ in ()).throw(ValueError("destino_desincronizado")),
        )

        resumen = apply_harvest.reconcilia_harvest(
            conn, _aplicador_harvest(conn, amazon, ids["ciclo_ejec"]), "amazon_us"
        )

        assert resumen.jobs_failed == 1
        assert _job(conn, dec)[1] == "failed"
        _creacion_sellada_ok(conn, dec)


@_skip_db
def test_arreglos05_b_cosecha_cerrada_por_ancestro_sella_la_creacion_con_ack(monkeypatch):
    """El proceso muere entre el ACK y la lectura de vuelta y, en la corrida que
    lo retoma, la campana de origen ya no esta habilitada. El job cierra fallido
    y el intento de creacion se sella como aplicado: Amazon lo acepto, aunque el
    trabajo no prospero. No se le cuelga el motivo del cierre del job."""
    with _db("orbit_b05_cierra_por_ancestro") as (conn, _extra):
        ids, dec, amazon = _cosecha_con_ack_durable(conn, monkeypatch)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s",
            (ids["camp"],),
        )

        resumen = apply_harvest.reconcilia_harvest(
            conn, _aplicador_harvest(conn, amazon, ids["ciclo_ejec"]), "amazon_us"
        )

        assert resumen.jobs_failed == 1
        assert _job(conn, dec)[1] == "failed"
        _creacion_sellada_ok(conn, dec)


# ---------------------------------------------------------------------------
# Migracion 0095: evidencia previa del ledger, ida / reversa / ida
# ---------------------------------------------------------------------------


def _sonda_de(ruta: Path) -> str:
    cabecera = next(
        linea
        for linea in ruta.read_text(encoding="utf-8").splitlines()[:10]
        if linea.startswith("-- sonda-aplicada:")
    )
    return cabecera.split("-- sonda-aplicada:", 1)[1].strip().rstrip(";")


def _columnas_previas(conn) -> set[str]:
    return {
        f[0]
        for f in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = 'apply_attempt'"
            " AND column_name IN ('bid_previo', 'ausencia_previa')"
        ).fetchall()
    }


def _insertar_con_evidencia(conn, decision: int, seq: int, bid_previo, ausencia_previa) -> int:
    return conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
        " bid_previo, ausencia_previa) VALUES (%s, %s, 'normal', '{}'::jsonb, true, %s, %s)"
        " RETURNING id",
        (decision, seq, bid_previo, Json(ausencia_previa) if ausencia_previa is not None else None),
    ).fetchone()[0]


@_skip_db
def test_arreglos05_b_migracion_evidencia_previa_ida_reversa_e_ida():
    """0095 con el rol real del motor: la evidencia previa nace con la fila del
    ledger y no se actualiza, ni por quien ya podia sellar ni por el dueno de
    la tabla; a lo sumo una de las dos; la reversa no abandona un intento
    abierto con evidencia y deja la migracion aplicable de nuevo."""
    ida = MIGRACIONES / "0095_arreglos05_evidencia_previa.sql"
    reversa = MIGRACIONES / "0095_reversa_arreglos05_evidencia_previa.sql"
    assert ida.exists() and reversa.exists(), "falta la migracion 0095 o su reversa"
    with _db("orbit_b05_mig") as (conn, _extra):
        assert _columnas_previas(conn) == {"bid_previo", "ausencia_previa"}
        assert conn.execute(_sonda_de(ida)).fetchone() == (True,), "la sonda da true aplicada"
        assert conn.execute(
            "SELECT has_column_privilege('app_decide', 'public.apply_attempt',"
            " 'bid_previo', 'UPDATE'),"
            " has_column_privilege('app_decide', 'public.apply_attempt',"
            " 'ausencia_previa', 'UPDATE'),"
            " has_column_privilege('app_decide', 'public.apply_attempt', 'ack', 'UPDATE'),"
            " has_table_privilege('app_decide', 'public.apply_attempt', 'UPDATE')"
        ).fetchone() == (False, False, True, False), (
            "la evidencia previa no se actualiza; el sello por columna no cambia"
        )
        ids = _semilla_bid(conn)
        dec = _decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
        conn.execute("SET ROLE app_decide")
        try:
            con_bid = _insertar_con_evidencia(conn, dec, 1, Decimal("1.0080"), None)
            conn.execute(
                "UPDATE apply_attempt SET ack = '{\"ok\": true}'::jsonb, resultado = 'ok',"
                " finished_at = now() WHERE id = %s",
                (con_bid,),
            )
            for sentencia in (
                "UPDATE apply_attempt SET bid_previo = 2.0000 WHERE id = %s",
                "UPDATE apply_attempt SET ausencia_previa = '{}'::jsonb WHERE id = %s",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sentencia, (con_bid,))
            with pytest.raises(psycopg.errors.CheckViolation):
                _insertar_con_evidencia(conn, dec, 2, Decimal("1.0000"), {"ausente": True})
        finally:
            conn.execute("RESET ROLE")
        # El dueno de la tabla tampoco reescribe la evidencia: la sella el
        # disparador, no el permiso.
        with pytest.raises(psycopg.errors.RestrictViolation, match="0095"):
            conn.execute(
                "UPDATE apply_attempt SET ausencia_previa = '{}'::jsonb WHERE id = %s", (con_bid,)
            )

        # Reversa con un intento ABIERTO que ya lleva evidencia: aborta.
        abierto = _insertar_con_evidencia(conn, dec, 3, Decimal("1.0000"), None)
        with pytest.raises(psycopg.errors.RaiseException, match="abierto"):
            conn.execute(reversa.read_text(encoding="utf-8"))
        conn.execute(
            "UPDATE apply_attempt SET resultado = 'ok', finished_at = now() WHERE id = %s",
            (abierto,),
        )
        conn.execute(reversa.read_text(encoding="utf-8"))
        assert _columnas_previas(conn) == set()
        # El disparador vuelve al texto anterior: no nombra las columnas.
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 4, 'probe', '{}'::jsonb, false)",
            (dec,),
        )
        conn.execute(ida.read_text(encoding="utf-8"))
        assert _columnas_previas(conn) == {"bid_previo", "ausencia_previa"}
        assert conn.execute(_sonda_de(ida)).fetchone() == (True,)
