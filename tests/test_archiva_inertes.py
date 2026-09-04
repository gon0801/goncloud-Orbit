"""Tests de tools/archiva_inertes.py (BIDS 01, tarea 1.4, D5).

Archivo MANUAL de keywords inertes con reversa: plan desde
v_entidad_inerte (solo kind='keyword'; product_target excluidos con
conteo), dry-run por defecto, mutacion con --acepto-mutacion-real +
--esperado N + --go, ledger keyword_archivo_manual ANTES del HTTP y
--reponer que recrea con el matchType del ledger.

PUROS: sin red, sin base, sin psycopg - la base es una conn falsa que
sirve filas enlatadas y graba cada (sql, params); el LIST vivo es un
AdsClient falso con colas por keyword; el POST de mutacion y el token
LWA viajan por httpx.MockTransport (el "sin HTTP" = transporte sin
requests grabados). Patron de tests/test_reactiva_campanas.py.

Reglas que pinean: cada monto del ledger con su moneda (regla 4),
ningun NULL convertido en 0 (regla 3), ledger ANTES del HTTP (regla 7:
la intencion es durable antes de mutar Amazon).
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import re
import socket
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import archiva_inertes as ar  # noqa: E402

from app.ads.config import AdsCredentials  # noqa: E402
from app.ads.structure import (  # noqa: E402
    EstructuraAds,
    EstructuraPerfil,
    PerfilAds,
    sync_structure,
)

LOTE = "inertes-2026-09-05"
GO = "go lote de prueba"

# ---------------------------------------------------------------------------
# Fakes de base: plan enlatado + ledger grabado.
# ---------------------------------------------------------------------------

# Fila del plan en el ORDEN que el modulo mapea por indice:
# id, platform, kind, texto, match, external, ad_group_id, ad_group_ext,
# campaign_id, campaign_ext, bid, moneda, clasificacion, dias.
_FILA_KW_MX = (
    11,
    "amazon_mx",
    "keyword",
    "arras de plata",
    "EXACT",
    "kw-ext-11",
    5,
    "ag-ext-5",
    2,
    "camp-ext-2",
    Decimal("12.5000"),
    "MXN",
    "peso_muerto",
    45,
    dt.datetime(2026, 1, 5, 12, 0),
)
_FILA_KW_MX_NULA = (
    12,
    "amazon_mx",
    "keyword",
    "arras de oro",
    "PHRASE",
    "kw-ext-12",
    5,
    "ag-ext-5",
    2,
    "camp-ext-2",
    None,
    None,
    "peso_muerto",
    None,  # sin bid cacheado, nunca impresiono
    dt.datetime(2026, 1, 5, 12, 0),
)
_FILA_KW_US = (
    21,
    "amazon_us",
    "keyword",
    "silver arras",
    "BROAD",
    "kw-ext-21",
    7,
    "ag-ext-7",
    3,
    "camp-ext-3",
    Decimal("0.7500"),
    "USD",
    "gasto_sin_ventas",
    31,
    dt.datetime(2026, 1, 5, 12, 0),
)

# Fila de reposicion (SELECT del ledger): id_fila, ad_entity_id, platform,
# campaign_ext, ad_group_ext, kw_ext, texto, match, bid, moneda, clasif, dias.
_FILA_REPONER = (
    100,
    11,
    "amazon_mx",
    "camp-ext-2",
    "ag-ext-5",
    "kw-ext-11",
    "arras de plata",
    "PHRASE",
    Decimal("12.5000"),
    "MXN",
    "peso_muerto",
    45,
)


class _CursorFalso:
    def __init__(self, filas):
        self._filas = list(filas)

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return list(self._filas)


class _ConnFalsa:
    """Conexion enlatada: sirve el plan / el conteo de excluidos / las filas
    a reponer y GRABA inserts/updates del ledger + commits."""

    def __init__(self, plan=(), excluidos=0, reponer=(), pendientes=(), jovenes=0):
        self._plan = list(plan)
        self._excluidos = excluidos
        self._reponer = list(reponer)
        self._pendientes = list(pendientes)
        self._jovenes = jovenes
        self.queries = []
        self.inserts = []  # params de cada INSERT al ledger
        self.updates = []  # (sql, params) de cada UPDATE al ledger
        self.commits = 0
        self._seq = 1000

    def execute(self, sql, params=None):
        plano = " ".join(str(sql).split())
        self.queries.append((plano, params))
        bajo = plano.lower()
        if "count(" in bajo and "v_entidad_inerte" in bajo:
            if "first_seen_at" in bajo:
                return _CursorFalso([(self._jovenes,)])
            return _CursorFalso([(self._excluidos,)])
        if "v_entidad_inerte" in bajo:
            return _CursorFalso(self._plan)
        if bajo.startswith("insert into keyword_archivo_manual"):
            self.inserts.append(params)
            self._seq += 1
            return _CursorFalso([(self._seq,)])
        if bajo.startswith("update keyword_archivo_manual"):
            self.updates.append((plano, params))
            return _CursorFalso([])
        if "estado in" in bajo and "from keyword_archivo_manual" in bajo:
            return _CursorFalso(self._pendientes)
        if "from keyword_archivo_manual" in bajo:
            return _CursorFalso(self._reponer)
        raise AssertionError(f"SQL inesperado: {plano[:120]}")

    def commit(self):
        self.commits += 1


# ---------------------------------------------------------------------------
# Fakes de Amazon: LIST vivo + mutaciones por MockTransport.
# ---------------------------------------------------------------------------


class _Respuesta:
    def __init__(self, status_code, datos, texto=""):
        self.status_code = status_code
        self._datos = datos
        self.text = texto

    def json(self):
        return self._datos


class _ClienteFalso:
    """AdsClient canned: /sp/keywords/list responde por keyword con la cola
    de estados/objetos que le toca (pre-lectura y luego readback)."""

    def __init__(self, por_keyword):
        # por_keyword: external -> lista de objetos (se consume en orden).
        self._colas = {k: list(v) for k, v in por_keyword.items()}
        self.llamadas = []

    def list_objects(self, path, body, *, profile_id=None):
        self.llamadas.append({"path": path, "body": dict(body), "profile_id": profile_id})
        assert path == "/sp/keywords/list", f"path inesperado: {path}"
        kw_id = body["keywordIdFilter"]["include"][0]
        cola = self._colas.get(kw_id, [])
        assert cola, f"LIST inesperado para {kw_id}"
        siguiente = cola.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente  # el cliente real lanza AdsApiError en >=400
        return _Respuesta(200, {"keywords": [siguiente]}, texto='{"state": "ok"}')

    def get(self, *a, **kw):
        raise AssertionError("GET inesperado en el camino del archivo")


def _huella_de(*filas):
    """Huella del conjunto con la formula del modulo (D-2.4.1), calculada
    desde las tuplas del plan: platform = f[1], external = f[5]. Pin: si
    el modulo cambia la formula sin querer, estos tests caen."""
    ids = sorted(f"{f[1]}:{f[5]}" for f in filas)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def _obj_kw(kw_id, estado, texto="t", match="EXACT", ad_group="ag"):
    return {
        "keywordId": kw_id,
        "state": estado,
        "keywordText": texto,
        "matchType": match,
        "adGroupId": ad_group,
    }


class _RedFalsa:
    """httpx.MockTransport canned: token LWA + colas de deletes/creates."""

    def __init__(self, deletes=(), creates=()):
        self.deletes = list(deletes)  # cada uno: (status, body_json)
        self.creates = list(creates)
        self.pedidos = []

    def __call__(self, request):
        self.pedidos.append(request)
        url = str(request.url)
        if "api.amazon.com/auth/o2/token" in url:
            return httpx.Response(200, json={"access_token": "token-falso"})
        if url.endswith("/sp/keywords/delete"):
            assert self.deletes, "DELETE inesperado (el lote debio detenerse)"
            status, cuerpo = self.deletes.pop(0)
            return httpx.Response(status, json=cuerpo)
        if url.endswith("/sp/keywords"):
            assert self.creates, "CREATE inesperado"
            status, cuerpo = self.creates.pop(0)
            return httpx.Response(status, json=cuerpo)
        raise AssertionError(f"POST inesperado: {url}")


_CREDS_FALSAS = AdsCredentials(
    client_id="fake-client-id",
    client_secret="fake-client-secret",
    refresh_token="fake-refresh-token",
)


def _perfil(platform, profile_id=101):
    moneda = "MXN" if platform == "amazon_mx" else "USD"
    pais = "MX" if platform == "amazon_mx" else "US"
    return PerfilAds(
        profile_id=profile_id,
        country=pais,
        currency_code=moneda,
        account_type="seller",
        valid_payment_method=True,
        account_name="cuenta de prueba",
        aceptado=True,
        platform=platform,
        moneda=moneda,
        motivo=None,
    )


def _fakea_frontera(monkeypatch, conn_read, conn_admin, cliente, red):
    """Frontera del main: credenciales, DSNs, AdsClient, perfiles y el
    httpx del modulo (su Client sale con el MockTransport dado)."""
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(lambda cls: _CREDS_FALSAS))
    monkeypatch.setenv("ORBIT_DSN_READ", "dsn-read-falso")
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "dsn-admin-falso")

    def _connect(dsn):
        if dsn == "dsn-read-falso":
            return conn_read
        if dsn == "dsn-admin-falso":
            return conn_admin
        raise AssertionError(f"DSN inesperado: {dsn}")

    monkeypatch.setattr(ar, "connect", _connect)
    monkeypatch.setattr(ar, "AdsClient", lambda cred: cliente)
    monkeypatch.setattr(
        ar,
        "evaluar_perfiles",
        lambda cliente_lectura: [_perfil("amazon_mx"), _perfil("amazon_us", 102)],
    )
    transporte = httpx.MockTransport(red)
    monkeypatch.setattr(
        ar,
        "httpx",
        SimpleNamespace(
            Client=lambda **kw: httpx.Client(transport=transporte),
            Timeout=lambda **kw: None,
        ),
    )
    return transporte


def _eventos(capsys):
    return [
        json.loads(linea) for linea in capsys.readouterr().out.splitlines() if linea.startswith("{")
    ]


_ACK_DELETE_OK = (200, {"keywords": {"success": [{"keywordId": "x"}]}})
_ACK_CREATE_OK = (207, {"keywords": {"success": [{"keyword": {"keywordId": "kw-nuevo-1"}}]}})


# ---------------------------------------------------------------------------
# 1-2. Plan puro: filtros SQL y excluidos.
# ---------------------------------------------------------------------------


def test_plan_sql_filtra_solo_keywords_y_cuenta_excluidos():
    """El plan sale de v_entidad_inerte con kind='keyword' fijo y los
    product_target se cuentan como excluidos (residual 1: solo se
    reportan, jamas se archivan)."""
    conn = _ConnFalsa(plan=[_FILA_KW_MX], excluidos=3, jovenes=2)
    plan, excluidos, jovenes = ar._plan_inertes(
        conn,
        plataforma="amazon_mx",
        clasificacion="peso_muerto",
        min_dias=30,
        limite=None,
    )
    assert len(plan) == 1
    item = plan[0]
    assert item["external_id"] == "kw-ext-11"
    assert item["match"] == "EXACT"
    assert item["bid"] == Decimal("12.5000") and item["bid_currency"] == "MXN"
    assert item["dias"] == 45
    assert item["campaign_external"] == "camp-ext-2"
    assert item["primera_vista"] == dt.datetime(2026, 1, 5, 12, 0)
    assert excluidos == 3, "los 3 product_target se declaran, no se tocan"
    assert jovenes == 2, "las jovenes se cuentan aparte, no entran al plan"
    sql_plan = conn.queries[0][0].lower()
    assert "v_entidad_inerte" in sql_plan
    assert "kind = 'keyword'" in sql_plan, "el filtro de kind vive en SQL"
    assert conn.queries[0][1][1] == "amazon_mx"
    assert conn.queries[0][1][2] == "peso_muerto"
    sql_excl = conn.queries[1][0].lower()
    assert "product_target" in sql_excl


def test_plan_sql_min_dias_deja_pasar_null_y_limite_agrega_limit():
    """dias NULL = nunca impresiono en 90d (el caso mas muerto): pasa el
    filtro como infinito. Con --limite el SQL trae LIMIT."""
    conn = _ConnFalsa(plan=[_FILA_KW_MX_NULA])
    plan, _, _ = ar._plan_inertes(
        conn,
        plataforma=None,
        clasificacion="peso_muerto",
        min_dias=30,
        limite=10,
    )
    assert len(plan) == 1
    assert plan[0]["dias"] is None, "regla 3: el NULL viaja, no se vuelve 0"
    assert plan[0]["bid"] is None and plan[0]["bid_currency"] is None
    sql = conn.queries[0][0].lower()
    assert "dias_sin_impresiones is null or" in sql
    assert "limit" in sql


# ---------------------------------------------------------------------------
# 3-4. Estaticos: migracion 0014 y candado de imports (corren en local).
# ---------------------------------------------------------------------------


def test_migracion_0014_crea_el_ledger_con_grants_y_estados():
    """La migracion existe, parsea (pglast) y trae el DDL del plan: CHECK
    de estados, COMMENT y GRANTs (SELECT a lectura, INSERT/UPDATE a
    app_admin)."""
    import pglast

    texto = (
        Path(ar.__file__).resolve().parent.parent / "migrations" / "0014_keyword_archivo_manual.sql"
    ).read_text(encoding="utf-8")
    pglast.parse_sql(texto)  # revienta si el SQL no parsea
    assert "CREATE TABLE keyword_archivo_manual" in texto
    for estado in ("planeado", "applied", "failed", "repuesto"):
        assert estado in texto
    assert "COMMENT ON TABLE keyword_archivo_manual" in texto
    for rol in ("app_read", "app_ingest", "app_decide", "app_admin"):
        assert rol in texto
    assert "GRANT INSERT" in texto and "app_admin" in texto
    assert "archivo_bid_con_moneda" in texto, (
        "regla 4 por schema (precedente estado_bid_con_moneda): bid y moneda NULL parejos"
    )
    assert "archivo_match_cerrado" in texto, "match con dominio cerrado"
    assert re.search(r"\bbid_currency\s+currency\b", texto), (
        "regla 4 por schema (PR #134): la moneda es enum currency, no TEXT"
    )
    assert "archivo_evidencia_applied" in texto, "applied exige ack+readback"
    assert "archivo_evidencia_repuesto" in texto, "repuesto exige sello completo"


def test_moneda_del_tool_iguala_la_de_la_capa_http():
    """MONEDA_POR_PLATAFORMA no puede divergir del mapa canonico en
    silencio (hallazgo de GLM): lo pinea este test. Import solo de
    tests (el candado de test_architecture cubre app/ y tools/)."""
    from app.ads.write import PLATAFORMA_MONEDA

    assert dict(PLATAFORMA_MONEDA) == ar.MONEDA_POR_PLATAFORMA


def test_modulo_no_importa_write_ni_apply():
    """Candado local del test_architecture: el tool usa HTTP propio con
    el sello v3, jamas el write client (un segundo dueno de la
    mutacion)."""
    fuente = Path(ar.__file__).read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    imports = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            imports.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            imports.add(nodo.module)
    assert not any(i.startswith("app.ads.write") for i in imports)
    assert not any(i.startswith("app.apply") for i in imports)
    assert "app.ads.write" not in fuente and "app.apply" not in fuente


# ---------------------------------------------------------------------------
# 5-7. Dry-run y guards anti-typo (cero HTTP).
# ---------------------------------------------------------------------------


def test_main_dry_run_imprime_el_plan_sin_http(monkeypatch, capsys):
    """Sin --acepto-mutacion-real: tabla del plan + evento dry_run y el
    transporte no graba NI UN request (ni token LWA)."""
    red = _RedFalsa()
    conn_read = _ConnFalsa(plan=[_FILA_KW_MX], excluidos=1)
    _fakea_frontera(
        monkeypatch,
        conn_read,
        _ConnFalsa(),
        _ClienteFalso({}),
        red,
    )
    monkeypatch.setattr(sys, "argv", ["archiva_inertes.py"])
    assert ar.main() == 0
    assert red.pedidos == [], "dry-run no abre HTTP"
    assert conn_read.commits >= 1, "la txn de lectura se cierra (PR #134)"
    fuera = capsys.readouterr().out  # una sola lectura: _eventos consume
    eventos = [json.loads(linea) for linea in fuera.splitlines() if linea.startswith("{")]
    secos = [e for e in eventos if e["evento"] == "dry_run"]
    assert len(secos) == 1
    assert secos[0]["candidatas"] == 1 and secos[0]["excluidas_targets"] == 1
    assert "kw-ext-11" in fuera, "la tabla del plan se imprime"


def test_main_esperado_distinto_aborta_sin_http(monkeypatch):
    """--esperado que no iguala el plan aborta ANTES de abrir HTTP: el
    transporte no graba ni el token."""
    red = _RedFalsa()
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX]),
        _ConnFalsa(),
        _ClienteFalso({}),
        red,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["archiva_inertes.py", "--acepto-mutacion-real", "--esperado", "5", "--go", GO],
    )
    with pytest.raises(ar.Abortar, match="esperado"):
        ar.main()
    assert red.pedidos == []


def test_main_mutacion_sin_go_aborta(monkeypatch):
    """Sin el literal del dueno no hay mutacion (regla 7: cada lote exige
    su go)."""
    red = _RedFalsa()
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX]),
        _ConnFalsa(),
        _ClienteFalso({}),
        red,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["archiva_inertes.py", "--acepto-mutacion-real", "--esperado", "1"],
    )
    with pytest.raises(ar.Abortar, match="[Gg]o"):
        ar.main()
    assert red.pedidos == []


# ---------------------------------------------------------------------------
# 8-11. Mutacion: skip vivo, ledger antes del HTTP, applied/failed.
# ---------------------------------------------------------------------------


def test_main_keyword_no_enabled_se_salta_con_nota_y_sigue(monkeypatch, capsys):
    """LIST previo != ENABLED (ya pausada por otro): se declara el skip y
    la siguiente keyword SI se archiva (no se pisa decision ajena)."""
    red = _RedFalsa(deletes=[_ACK_DELETE_OK])
    cliente = _ClienteFalso(
        {
            "kw-ext-11": [_obj_kw("kw-ext-11", "PAUSED")],
            "kw-ext-21": [_obj_kw("kw-ext-21", "ENABLED"), _obj_kw("kw-ext-21", "ARCHIVED")],
        }
    )
    conn_admin = _ConnFalsa()
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX, _FILA_KW_US]),
        conn_admin,
        cliente,
        red,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "archiva_inertes.py",
            "--acepto-mutacion-real",
            "--esperado",
            "2",
            "--go",
            GO,
            "--huella",
            _huella_de(_FILA_KW_MX, _FILA_KW_US),
        ],
    )
    assert ar.main() == 0
    eventos = _eventos(capsys)
    skips = [e for e in eventos if e["evento"] == "skip_no_enabled"]
    assert len(skips) == 1 and skips[0]["external_id"] == "kw-ext-11"
    assert len(conn_admin.inserts) == 1, "la saltada no deja fila planeado"
    fin = [e for e in eventos if e["evento"] == "reconciliacion_final"]
    assert len(fin) == 1
    assert fin[0]["archivadas"] == 1 and fin[0]["saltadas"] == 1


def test_main_ledger_antes_del_http_post_falla_deja_failed(monkeypatch, capsys):
    """El POST 500 no borra la intencion: la fila 'planeado' existe (con
    commit) AUNQUE el HTTP falle, y queda 'failed' con el lote detenido."""
    red = _RedFalsa(deletes=[(500, {"error": "boom"})])
    cliente = _ClienteFalso(
        {
            "kw-ext-11": [_obj_kw("kw-ext-11", "ENABLED")],
        }
    )
    conn_admin = _ConnFalsa()
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX]),
        conn_admin,
        cliente,
        red,
    )
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
            _huella_de(_FILA_KW_MX),
        ],
    )
    with pytest.raises(ar.Abortar):
        ar.main()
    assert len(conn_admin.inserts) == 1, "planeado ANTES del POST"
    assert conn_admin.commits >= 1, "la intencion se commitea antes del HTTP"
    sqls = [u[0] for u in conn_admin.updates]
    assert sqls and "'failed'" in sqls[0], "el fallo queda sellado"


def test_main_readback_archived_aplica_y_reconcilia(monkeypatch, capsys):
    """Camino feliz: LIST previo ENABLED -> planeado -> DELETE (id STRING,
    vendor v3) -> readback ARCHIVED -> applied + reconciliacion."""
    red = _RedFalsa(deletes=[_ACK_DELETE_OK])
    cliente = _ClienteFalso(
        {
            "kw-ext-11": [_obj_kw("kw-ext-11", "ENABLED"), _obj_kw("kw-ext-11", "ARCHIVED")],
        }
    )
    conn_admin = _ConnFalsa()
    transporte = _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX]),
        conn_admin,
        cliente,
        red,
    )
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
            _huella_de(_FILA_KW_MX),
        ],
    )
    assert ar.main() == 0
    assert len(conn_admin.inserts) == 1
    fila = conn_admin.inserts[0]
    assert fila[1] == 11 and fila[5] == "kw-ext-11", "identidad completa"
    assert fila[8] == Decimal("12.5000") and fila[9] == "MXN", "regla 4"
    assert fila[12] == GO, "el literal del dueno va al ledger"
    sql_ins = conn_admin.queries[0][0]
    assert "'planeado'" in sql_ins, "la intencion nace planeado"
    deletes = [p for p in red.pedidos if p.url.path.endswith("/delete")]
    assert len(deletes) == 1
    cuerpo = json.loads(deletes[0].content.decode())
    assert cuerpo == {"keywordIdFilter": {"include": ["kw-ext-11"]}}
    assert isinstance(cuerpo["keywordIdFilter"]["include"][0], str)
    vendor = "application/vnd.spkeyword.v3+json"
    assert deletes[0].headers["Content-Type"] == vendor
    assert deletes[0].headers["Accept"] == vendor
    sqls = [u[0] for u in conn_admin.updates]
    assert len(sqls) == 1 and "'applied'" in sqls[0]
    params = [p for u in conn_admin.updates for p in u[1]]
    assert "ARCHIVED" in params, "el readback viaja al ledger"
    eventos = _eventos(capsys)
    fin = [e for e in eventos if e["evento"] == "reconciliacion_final"]
    assert len(fin) == 1 and fin[0]["ok"] is True
    assert transporte is not None  # el cliente HTTP salio del transporte dado


def test_main_readback_distinto_falla_y_detiene_el_lote(monkeypatch, capsys):
    """Readback != ARCHIVED (sigue ENABLED): la fila queda 'failed' y la
    2a keyword NO recibe HTTP (el lote se detiene, no se sigue a ciegas)."""
    red = _RedFalsa(deletes=[_ACK_DELETE_OK])
    cliente = _ClienteFalso(
        {
            "kw-ext-11": [_obj_kw("kw-ext-11", "ENABLED"), _obj_kw("kw-ext-11", "ENABLED")],
            "kw-ext-21": [_obj_kw("kw-ext-21", "ENABLED")],
        }
    )
    conn_admin = _ConnFalsa()
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX, _FILA_KW_US]),
        conn_admin,
        cliente,
        red,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "archiva_inertes.py",
            "--acepto-mutacion-real",
            "--esperado",
            "2",
            "--go",
            GO,
            "--huella",
            _huella_de(_FILA_KW_MX, _FILA_KW_US),
        ],
    )
    with pytest.raises(ar.Abortar, match="ARCHIVED"):
        ar.main()
    deletes = [p for p in red.pedidos if p.url.path.endswith("/delete")]
    assert len(deletes) == 1, "la 2a keyword no recibe HTTP"
    assert len(conn_admin.inserts) == 1, "la 2a ni deja fila planeado"
    sqls = [u[0] for u in conn_admin.updates]
    assert len(sqls) == 1 and "'failed'" in sqls[0]


def test_main_nulos_viajan_sin_convertirse_en_cero(monkeypatch, capsys):
    """Regla 3: bid/dias NULL del plan llegan NULL al ledger (jamas 0) y
    la keyword se archiva igual (el DELETE no necesita bid)."""
    red = _RedFalsa(deletes=[_ACK_DELETE_OK])
    cliente = _ClienteFalso(
        {
            "kw-ext-12": [_obj_kw("kw-ext-12", "ENABLED"), _obj_kw("kw-ext-12", "ARCHIVED")],
        }
    )
    conn_admin = _ConnFalsa()
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX_NULA]),
        conn_admin,
        cliente,
        red,
    )
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
            _huella_de(_FILA_KW_MX_NULA),
        ],
    )
    assert ar.main() == 0
    fila = conn_admin.inserts[0]
    assert fila[8] is None and fila[9] is None, "bid+moneda NULL parejo"
    assert fila[11] is None, "dias NULL, no 0"


# ---------------------------------------------------------------------------
# 12-13. Reversa --reponer.
# ---------------------------------------------------------------------------


def test_reponer_recrea_con_el_match_del_ledger_y_sella_repuesto(
    monkeypatch,
    capsys,
):
    """--reponer crea con {adGroupId, campaignId, keywordText, matchType
    del ledger (PHRASE, no el EXACT fijo del harvest), state ENABLED,
    bid} y sella repuesto_* con el external nuevo del ack."""
    red = _RedFalsa(creates=[_ACK_CREATE_OK])
    cliente = _ClienteFalso(
        {
            "kw-nuevo-1": [
                _obj_kw("kw-nuevo-1", "ENABLED", "arras de plata", "PHRASE", "ag-ext-5")
            ],
        }
    )
    conn_admin = _ConnFalsa(reponer=[_FILA_REPONER])
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(),
        conn_admin,
        cliente,
        red,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["archiva_inertes.py", "--reponer", LOTE, "--acepto-mutacion-real"],
    )
    assert ar.main() == 0
    creates = [p for p in red.pedidos if p.url.path == "/sp/keywords"]
    assert len(creates) == 1
    cuerpo = json.loads(creates[0].content.decode())
    # Objeto envuelto en {"keywords": [...]}: sello del probe 2.5
    # (objeto desnudo = 400). Hallazgo ALTA de grok en cross-review.
    assert cuerpo == {
        "keywords": [
            {
                "adGroupId": "ag-ext-5",
                "campaignId": "camp-ext-2",
                "keywordText": "arras de plata",
                "matchType": "PHRASE",
                "state": "ENABLED",
                "bid": 12.5,
            }
        ]
    }
    interno = cuerpo["keywords"][0]
    assert isinstance(interno["bid"], float), "el wire lleva numero (sello)"
    ups = [u for u in conn_admin.updates if "'repuesto'" in u[0]]
    assert len(ups) == 1
    assert "kw-nuevo-1" in str(ups[0][1])
    eventos = _eventos(capsys)
    fin = [e for e in eventos if e["evento"] == "reconciliacion_final"]
    assert len(fin) == 1 and fin[0]["repuestas"] == 1


def test_reponer_sin_mutacion_es_dry_run(monkeypatch, capsys):
    """--reponer sin --acepto-mutacion-real solo lista (cero HTTP)."""
    red = _RedFalsa()
    conn_admin = _ConnFalsa(reponer=[_FILA_REPONER])
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(),
        conn_admin,
        _ClienteFalso({}),
        red,
    )
    monkeypatch.setattr(sys, "argv", ["archiva_inertes.py", "--reponer", LOTE])
    assert ar.main() == 0
    assert red.pedidos == []
    assert conn_admin.commits >= 1, "la txn de lectura se cierra (PR #134)"
    assert "kw-ext-11" in capsys.readouterr().out


def test_reponer_bid_null_aborta_fail_closed(monkeypatch, capsys):
    """Sin bid en el ledger no se inventa uno: la fila queda 'applied',
    se declara y no sale HTTP."""
    fila_sin_bid = (
        101,
        12,
        "amazon_mx",
        "camp-ext-2",
        "ag-ext-5",
        "kw-ext-12",
        "arras de oro",
        "PHRASE",
        None,
        None,
        "peso_muerto",
        None,
    )
    red = _RedFalsa()
    conn_admin = _ConnFalsa(reponer=[fila_sin_bid])
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(),
        conn_admin,
        _ClienteFalso({}),
        red,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["archiva_inertes.py", "--reponer", LOTE, "--acepto-mutacion-real"],
    )
    with pytest.raises(ar.Abortar, match="[Bb]id"):
        ar.main()
    assert red.pedidos == [], "sin bid no hay create"
    assert conn_admin.updates == [], "la fila sigue 'applied', sin sello falso"


# ---------------------------------------------------------------------------
# 14-16. Adjudicacion grok (cross-review, 1 ALTA + 2 menores).
# ---------------------------------------------------------------------------


def test_main_go_vacio_aborta(monkeypatch):
    """--go "" no autoriza: el literal del dueno no puede ser vacio."""
    red = _RedFalsa()
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX]),
        _ConnFalsa(),
        _ClienteFalso({}),
        red,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["archiva_inertes.py", "--acepto-mutacion-real", "--esperado", "1", "--go", ""],
    )
    with pytest.raises(ar.Abortar, match="[Gg]o"):
        ar.main()
    assert red.pedidos == []


def test_main_readback_que_lanza_sella_failed_y_detiene(monkeypatch, capsys):
    """El LIST real lanza en >=400 (no retorna status): si el readback
    post-DELETE lanza, la fila queda 'failed' (no 'planeado' colgado) y
    el lote se detiene."""
    from app.ads.client import AdsApiError

    red = _RedFalsa(deletes=[_ACK_DELETE_OK])
    cliente = _ClienteFalso(
        {
            "kw-ext-11": [
                _obj_kw("kw-ext-11", "ENABLED"),
                AdsApiError("status=500: POST /sp/keywords/list"),
            ],
        }
    )
    conn_admin = _ConnFalsa()
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX]),
        conn_admin,
        cliente,
        red,
    )
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
            _huella_de(_FILA_KW_MX),
        ],
    )
    with pytest.raises(ar.Abortar):
        ar.main()
    sqls = [u[0] for u in conn_admin.updates]
    assert len(sqls) == 1 and "'failed'" in sqls[0]


def test_main_linea_de_mutacion_lleva_el_ack(monkeypatch, capsys):
    """La linea JSON de archivo trae request-identidad + ack + readback
    (el operador concilia sin abrir el ledger)."""
    red = _RedFalsa(deletes=[_ACK_DELETE_OK])
    cliente = _ClienteFalso(
        {
            "kw-ext-11": [_obj_kw("kw-ext-11", "ENABLED"), _obj_kw("kw-ext-11", "ARCHIVED")],
        }
    )
    _fakea_frontera(
        monkeypatch,
        _ConnFalsa(plan=[_FILA_KW_MX]),
        _ConnFalsa(),
        cliente,
        red,
    )
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
            _huella_de(_FILA_KW_MX),
        ],
    )
    assert ar.main() == 0
    fuera = capsys.readouterr().out
    eventos = [json.loads(linea) for linea in fuera.splitlines() if linea.startswith("{")]
    muts = [e for e in eventos if e["evento"] == "archivo"]
    assert len(muts) == 1 and muts[0]["ok"] is True
    assert "ack" in muts[0] and "success" in str(muts[0]["ack"])


# ---------------------------------------------------------------------------
# 17. Migracion 0014 contra Postgres real (PR #134): cada estado exige su
# evidencia por schema, la moneda es enum y el dinero va parejo.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
SQL14 = (ROOT / "migrations" / "0014_keyword_archivo_manual.sql").read_text(encoding="utf-8")
SQL17 = (ROOT / "migrations" / "0017_first_seen_at.sql").read_text(encoding="utf-8")


@contextmanager
def _db_ledger(prefijo):
    """DB temporal con el esquema + la migracion 0014 (patron _db_inerte)."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL14)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _kw_ledger(conn):
    """Keyword minima para referenciar desde el ledger."""
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, match_type, keyword_text)"
        " VALUES ('amazon_mx', 'keyword', 'kw-ledger-1', 'EXACT', 'arras de plata')"
        " RETURNING id"
    ).fetchone()[0]


_BASE_FILA = {
    "lote": "inertes-2026-09-05",
    "platform": "amazon_mx",
    "campaign_external": "camp-ext-2",
    "ad_group_external": "ag-ext-5",
    "keyword_external": "kw-ledger-1",
    "keyword_text": "arras de plata",
    "match_type": "EXACT",
    "clasificacion": "peso_muerto",
    "go_literal": "go de prueba",
    "estado": "planeado",
}


def _inserta(conn, ad_entity_id, **cambios):
    cols = dict(_BASE_FILA, ad_entity_id=ad_entity_id, **cambios)
    nombres = ", ".join(cols)
    marcas = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"INSERT INTO keyword_archivo_manual ({nombres}) VALUES ({marcas}) RETURNING id",
        tuple(cols.values()),
    ).fetchone()[0]


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ledger_0014_exige_evidencia_por_estado_moneda_y_parejo():
    """PR #134: planeado admite NULLs; applied sin ack/readback revienta;
    repuesto sin sello completo revienta; bid sin moneda revienta; moneda
    fuera del enum revienta; match fuera de dominio revienta."""
    with _db_ledger("ledger14") as conn:
        kw = _kw_ledger(conn)

        # Validas: planeado con NULLs, applied y repuesto completos.
        _inserta(conn, kw)
        _inserta(
            conn,
            kw,
            estado="applied",
            bid=Decimal("12.5000"),
            bid_currency="MXN",
            dias_sin_impresiones=45,
            ack='{"status": 200}',
            readback_estado="ARCHIVED",
        )
        _inserta(
            conn,
            kw,
            estado="repuesto",
            bid=Decimal("12.5000"),
            bid_currency="MXN",
            ack='{"status": 200}',
            readback_estado="ARCHIVED",
            repuesto_at="2026-09-05T00:00:00+00:00",
            repuesto_external="kw-nuevo-1",
            repuesto_ack='{"status": 207}',
        )

        # Invalidas: cada una revienta por su CHECK o su tipo.
        with pytest.raises(psycopg.errors.CheckViolation):
            _inserta(conn, kw, estado="applied", readback_estado="ARCHIVED")
        with pytest.raises(psycopg.errors.CheckViolation):
            _inserta(conn, kw, estado="applied", ack='{"status": 200}')
        with pytest.raises(psycopg.errors.CheckViolation):
            _inserta(
                conn,
                kw,
                estado="repuesto",
                ack='{"status": 200}',
                readback_estado="ARCHIVED",
                repuesto_external="kw-nuevo-1",
                repuesto_ack='{"status": 207}',
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            _inserta(conn, kw, bid=Decimal("12.5000"))
        with pytest.raises(psycopg.errors.CheckViolation):
            _inserta(conn, kw, match_type="INVALID")
        with pytest.raises(psycopg.Error):
            _inserta(conn, kw, bid=Decimal("12.5000"), bid_currency="XXX")

        assert conn.execute("SELECT count(*) FROM keyword_archivo_manual").fetchone()[0] == 3


# ---------------------------------------------------------------------------
# Revision del lead 2026-09-04: el SQL contra Postgres DE VERDAD
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_sql_del_plan_corre_contra_postgres_de_verdad():
    """El resto de este archivo usa `_ConnFalsa`: valida el plumbing de Python
    y JAMAS el SQL. Por eso nadie vio que `(%s IS NULL OR ...)` sin cast
    revienta con `IndeterminateDatatype: could not determine data type of
    parameter $1` — la herramienta no habia corrido nunca contra una base
    real. Este test ejecuta las DOS consultas con parametro presente Y con
    parametro NULL. Rojo contra el codigo previo: IndeterminateDatatype."""
    import psycopg

    raiz = Path(__file__).resolve().parents[1]
    base = SQL
    inerte = (raiz / "migrations" / "0013_entidad_inerte.sql").read_text(encoding="utf-8")
    nombre = "orbit_archiva_sql"
    admin = psycopg.connect(_test_dsn(), autocommit=True)
    admin.execute(f'DROP DATABASE IF EXISTS "{nombre}"')
    admin.execute(f'CREATE DATABASE "{nombre}"')
    admin.close()
    conn = psycopg.connect(_test_dsn().rsplit("/", 1)[0] + f"/{nombre}")
    try:
        conn.execute(base)
        conn.execute(inerte)
        conn.execute(SQL17)
        conn.commit()
        for plataforma in ("amazon_mx", None):
            filas = conn.execute(
                ar._SQL_PLAN, (plataforma, plataforma, "peso_muerto", 30, 30)
            ).fetchall()
            assert filas == [], "base vacia: la consulta CORRE y no devuelve nada"
            excl = conn.execute(
                ar._SQL_EXCLUIDOS, (plataforma, plataforma, "peso_muerto", 30)
            ).fetchone()[0]
            assert excl == 0
            jovenes = conn.execute(
                ar._SQL_EXCLUIDOS_JOVENES, (plataforma, plataforma, "peso_muerto", 30, 30)
            ).fetchone()[0]
            assert jovenes == 0
    finally:
        conn.close()
        admin = psycopg.connect(_test_dsn(), autocommit=True)
        admin.execute(f'DROP DATABASE IF EXISTS "{nombre}"')
        admin.close()


# ---------------------------------------------------------------------------
# BIDS 01 2.4: autorizacion por identidad, no por conteo (H4).
# ---------------------------------------------------------------------------


def test_24_mismo_n_distinto_conjunto_aborta_sin_http(monkeypatch, capsys):
    """H4: salen 3 y entran 3 con el mismo N — el go aborta aunque el
    conteo coincida, sin haber tocado nada (cero HTTP, cero inserts).

    Rojo contra el codigo previo (solo --esperado N): el lote pasaba y
    archivaba OTRAS keywords."""
    red = _RedFalsa()
    cliente = _ClienteFalso({})
    conn_admin = _ConnFalsa()
    _fakea_frontera(monkeypatch, _ConnFalsa(plan=[_FILA_KW_MX]), conn_admin, cliente, red)
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
            _huella_de(_FILA_KW_US),  # mismo N=1, OTRO conjunto
        ],
    )
    with pytest.raises(ar.Abortar, match="huella"):
        ar.main()
    assert red.pedidos == [], "la verificacion es antes de cualquier HTTP"
    assert conn_admin.inserts == [], "ni la intencion planeado se escribe"
    assert cliente.llamadas == [], "ni el LIST previo sale"


def test_24_sin_huella_aborta(monkeypatch):
    """Modo real sin --huella: aborta (la autorizacion por conjunto no
    es opcional)."""
    _fakea_frontera(
        monkeypatch, _ConnFalsa(plan=[_FILA_KW_MX]), _ConnFalsa(), _ClienteFalso({}), _RedFalsa()
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["archiva_inertes.py", "--acepto-mutacion-real", "--esperado", "1", "--go", GO],
    )
    with pytest.raises(ar.Abortar, match="huella"):
        ar.main()


def test_24_dry_run_publica_la_huella(monkeypatch):
    """El ensayo imprime y loguea la huella que el go debe pegar."""
    import io
    from contextlib import redirect_stdout

    _fakea_frontera(
        monkeypatch, _ConnFalsa(plan=[_FILA_KW_MX]), _ConnFalsa(), _ClienteFalso({}), _RedFalsa()
    )
    monkeypatch.setattr(sys, "argv", ["archiva_inertes.py"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert ar.main() == 0
    fuera = buf.getvalue()
    esperada = _huella_de(_FILA_KW_MX)
    assert f"huella del conjunto: {esperada}" in fuera
    eventos = [json.loads(linea) for linea in fuera.splitlines() if linea.startswith("{")]
    secos = [e for e in eventos if e["evento"] == "dry_run"]
    assert len(secos) == 1 and secos[0]["huella"] == esperada


def test_24_huella_ordena_y_cubre_plataforma():
    """Invariante al orden del plan; MX y US con el mismo external_id
    dan huellas distintas (el id solo es unico con su plataforma)."""
    a = {"platform": "amazon_mx", "external_id": "kw-1"}
    b = {"platform": "amazon_us", "external_id": "kw-2"}
    assert ar._huella_conjunto([a, b]) == ar._huella_conjunto([b, a])
    assert len(ar._huella_conjunto([a])) == 64, "sha256 completa, sin truncar"
    misma_mx = {"platform": "amazon_mx", "external_id": "kw-9"}
    misma_us = {"platform": "amazon_us", "external_id": "kw-9"}
    assert ar._huella_conjunto([misma_mx]) != ar._huella_conjunto([misma_us])


# BIDS 01 2.3: reconciliacion del ledger (H3).
# ---------------------------------------------------------------------------

# Fila pendiente (SELECT id, lote, platform, keyword_external).
_PENDIENTE_FAILED = (7, LOTE, "amazon_mx", "kw-ext-12")

_FILA_REPONER_2 = (
    101,
    12,
    "amazon_mx",
    "camp-ext-2",
    "ag-ext-5",
    "kw-ext-12",
    "collar rojo",
    "EXACT",
    Decimal("9.0000"),
    "MXN",
    "peso_muerto",
    45,
)

_ACK_CREATE_OK_2 = (207, {"keywords": {"success": [{"keyword": {"keywordId": "kw-nuevo-2"}}]}})


def test_23_reconciliar_recupera_failed_archived():
    """Fila `failed` cuya keyword esta ARCHIVED en Amazon -> `applied`.

    Rojo contra el codigo previo (sin _reconciliar): la fila quedaba
    fuera de la reversa para siempre (H3: archivada en Amazon e
    invisible para --reponer)."""
    conn = _ConnFalsa(pendientes=[_PENDIENTE_FAILED])
    cliente = _ClienteFalso({"kw-ext-12": [_obj_kw("kw-ext-12", "ARCHIVED")]})
    resumen = ar._reconciliar(conn, cliente, {"amazon_mx": 101}, None)
    assert resumen == {"pendientes": 1, "recuperadas": 1, "vivas": 0, "sin_verificar": 0}
    assert len(conn.updates) == 1
    plano, params = conn.updates[0]
    assert "'applied'" in plano
    assert params[1] == "ARCHIVED" and params[2] == 7
    ack = json.loads(params[0])
    assert ack["fuente"] == "reconciliar", "evidencia honesta, no el ack del DELETE"
    assert ack["keyword_external"] == "kw-ext-12"


def test_23_reconciliar_viva_queda_intacta():
    """LIST ENABLED = el DELETE no se aplico: sin UPDATE, contada aparte."""
    conn = _ConnFalsa(pendientes=[_PENDIENTE_FAILED])
    cliente = _ClienteFalso({"kw-ext-12": [_obj_kw("kw-ext-12", "ENABLED")]})
    resumen = ar._reconciliar(conn, cliente, {"amazon_mx": 101}, LOTE)
    assert resumen["vivas"] == 1 and resumen["recuperadas"] == 0
    assert conn.updates == []
    assert conn.queries[0][1] == (LOTE, LOTE), "el filtro de lote viaja"


def test_23_reconciliar_list_caido_aborta_sin_tocar():
    """LIST caido: la fila queda intacta y el comando aborta fail-closed."""
    conn = _ConnFalsa(pendientes=[_PENDIENTE_FAILED])
    cliente = _ClienteFalso({"kw-ext-12": [RuntimeError("red caida")]})
    with pytest.raises(ar.Abortar):
        ar._reconciliar(conn, cliente, {"amazon_mx": 101}, None)
    assert conn.updates == [], "a ciegas no se promueve nada"


def test_23_reconciliar_sin_perfil_aborta_sin_tocar():
    """Sin perfil aceptado no hay LIST posible: intacta + aborto."""
    conn = _ConnFalsa(pendientes=[_PENDIENTE_FAILED])
    cliente = _ClienteFalso({})
    with pytest.raises(ar.Abortar):
        ar._reconciliar(conn, cliente, {}, None)
    assert conn.updates == []
    assert cliente.llamadas == [], "sin perfil ni se pregunta"


def test_23_reponer_incluye_recuperadas_del_lote(monkeypatch, capsys):
    """--reponer con mutacion reconcilia su lote ANTES de leer `applied`:
    la failed recuperada se crea junto a la applied (2 CREATEs)."""
    red = _RedFalsa(creates=[_ACK_CREATE_OK, _ACK_CREATE_OK_2])
    cliente = _ClienteFalso(
        {
            "kw-ext-12": [_obj_kw("kw-ext-12", "ARCHIVED")],
            "kw-nuevo-1": [
                _obj_kw("kw-nuevo-1", "ENABLED", "arras de plata", "PHRASE", "ag-ext-5")
            ],
            "kw-nuevo-2": [_obj_kw("kw-nuevo-2", "ENABLED", "collar rojo", "EXACT", "ag-ext-5")],
        }
    )
    conn_admin = _ConnFalsa(
        reponer=[_FILA_REPONER, _FILA_REPONER_2], pendientes=[_PENDIENTE_FAILED]
    )
    _fakea_frontera(monkeypatch, _ConnFalsa(), conn_admin, cliente, red)
    monkeypatch.setattr(
        sys,
        "argv",
        ["archiva_inertes.py", "--reponer", LOTE, "--acepto-mutacion-real"],
    )
    assert ar.main() == 0
    promo = [u for u in conn_admin.updates if "'applied'" in u[0]]
    assert len(promo) == 1, "la failed se promovio antes de leer applied"
    creates = [p for p in red.pedidos if p.url.path == "/sp/keywords"]
    assert len(creates) == 2, "applied + recuperada se crean"
    eventos = _eventos(capsys)
    fin = [e for e in eventos if e["evento"] == "reconciliacion_final"]
    assert len(fin) == 1 and fin[0]["repuestas"] == 2


def test_23_reconciliar_cmd_solo_lee_amazon(monkeypatch, capsys):
    """--reconciliar standalone no hace DELETE ni CREATE (cero POSTs mutantes)."""
    red = _RedFalsa()
    cliente = _ClienteFalso({"kw-ext-12": [_obj_kw("kw-ext-12", "ARCHIVED")]})
    conn_admin = _ConnFalsa(pendientes=[_PENDIENTE_FAILED])
    _fakea_frontera(monkeypatch, _ConnFalsa(), conn_admin, cliente, red)
    monkeypatch.setattr(sys, "argv", ["archiva_inertes.py", "--reconciliar", "--lote", LOTE])
    assert ar.main() == 0
    assert red.pedidos == [], "sin token ni mutaciones: solo LISTs"
    assert len([u for u in conn_admin.updates if "'applied'" in u[0]]) == 1
    eventos = _eventos(capsys)
    rec = [e for e in eventos if e["evento"] == "reconciliacion"]
    assert len(rec) == 1 and rec[0]["recuperadas"] == 1


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_23_promocion_corre_en_postgres_de_verdad():
    """El UPDATE de promocion + el SELECT de pendientes contra Postgres
    real: el CHECK de evidencia muerde sin ack/readback, y la fila
    promovida la trae _SQL_REPONER (es reponible)."""
    import psycopg

    raiz = Path(__file__).resolve().parents[1]
    base = SQL
    inerte = (raiz / "migrations" / "0013_entidad_inerte.sql").read_text(encoding="utf-8")
    ledger = (raiz / "migrations" / "0014_keyword_archivo_manual.sql").read_text(encoding="utf-8")
    nombre = "orbit_reconcilia_sql"
    admin = psycopg.connect(_test_dsn(), autocommit=True)
    admin.execute(f'DROP DATABASE IF EXISTS "{nombre}"')
    admin.execute(f'CREATE DATABASE "{nombre}"')
    admin.close()
    conn = psycopg.connect(_test_dsn().rsplit("/", 1)[0] + f"/{nombre}")
    try:
        conn.execute(base)
        conn.execute(inerte)
        conn.execute(ledger)
        ent = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id,"
            " match_type, keyword_text)"
            " VALUES ('amazon_mx', 'campaign', 'CMP23', NULL, NULL, NULL)"
            " RETURNING id"
        ).fetchone()[0]
        kw = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id,"
            " match_type, keyword_text)"
            " VALUES ('amazon_mx', 'keyword', 'KW23', %s, 'EXACT', 'collar')"
            " RETURNING id",
            (ent,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO keyword_archivo_manual"
            " (lote, ad_entity_id, platform, campaign_external, ad_group_external,"
            "  keyword_external, keyword_text, match_type, clasificacion, go_literal, estado)"
            " VALUES ('lote23', %s, 'amazon_mx', 'CMP23', 'AG23', 'KW23',"
            "  'collar', 'EXACT', 'peso_muerto', 'go', 'failed')"
            " RETURNING id",
            (kw,),
        ).fetchone()[0]
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE keyword_archivo_manual SET estado = 'applied'"
                " WHERE keyword_external = 'KW23'"
            )
        conn.rollback()
        fila = conn.execute(ar._SQL_PENDIENTES, ("lote23", "lote23")).fetchone()
        assert fila[0] is not None and fila[3] == "KW23"
        assert conn.execute(ar._SQL_PENDIENTES, ("otro", "otro")).fetchall() == []
        conn.execute(
            ar._SQL_PROMUEVE_APPLIED,
            (json.dumps({"fuente": "reconciliar"}), "ARCHIVED", fila[0]),
        )
        conn.commit()
        trae = conn.execute(ar._SQL_REPONER, ("lote23",)).fetchall()
        assert len(trae) == 1 and trae[0][5] == "KW23", "recuperada = reponible"
    finally:
        conn.close()
        admin = psycopg.connect(_test_dsn(), autocommit=True)
        admin.execute(f'DROP DATABASE IF EXISTS "{nombre}"')


# 18. BIDS 01 2.1 contra Postgres real: la edad sella el plan.
# ---------------------------------------------------------------------------

SQL13 = (ROOT / "migrations" / "0013_entidad_inerte.sql").read_text(encoding="utf-8")


@contextmanager
def _db_21(prefijo):
    """DB temporal con esquema + vista inerte + ledger + first_seen_at."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL13)
        conn.execute(SQL14)
        conn.execute(SQL17)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _siembra_campana_21(conn):
    """Jerarquia amazon_mx campana > ad group + watermark viejo de metricas.

    El watermark de la plataforma sale de v_metric_latest: una sola fila de
    hace 100 dias en la campana basta. Las keywords NO llevan metricas:
    quedan peso_muerto con dias NULL (pasa como infinito): una vieja de
    40d y una joven de 5d. Devuelve el id de la campana."""
    hoy = dt.datetime.now(dt.UTC).date()
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name, first_seen_at)"
        " VALUES ('amazon_mx', 'campaign', 'CMP21', 'Camp 21', %s) RETURNING id",
        (hoy - dt.timedelta(days=40),),
    ).fetchone()[0]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, name, first_seen_at)"
        " VALUES ('amazon_mx', 'ad_group', 'AG21', %s, 'AG 21', %s) RETURNING id",
        (camp, hoy - dt.timedelta(days=40)),
    ).fetchone()[0]
    for ext, texto, hace in (("KWVIEJA", "vieja", 40), ("KWJOVEN", "joven", 5)):
        kw = conn.execute(
            "INSERT INTO ad_entity"
            " (platform, kind, external_id, parent_id, match_type, keyword_text, first_seen_at)"
            " VALUES ('amazon_mx', 'keyword', %s, %s, 'EXACT', %s, %s) RETURNING id",
            (ext, ag, texto, hoy - dt.timedelta(days=hace)),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (kw,),
        )
    for eid in (camp, ag):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (eid,),
        )
    run = conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('seed-21') RETURNING id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ads_metric_observation"
        " (ad_entity_id, metric_date, observed_at, metric_currency,"
        "  impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', 7, 1, 0, %s)",
        (camp, hoy - dt.timedelta(days=100), run),
    )
    return camp


def _textos(plan):
    return {p["texto"] for p in plan}


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_21_plan_excluye_joven_y_la_reporta():
    """La joven pasa todo menos la edad: fuera del plan, contada aparte.

    Rojo contra el codigo previo (sin filtro first_seen_at): la joven
    aparecia en el plan y _SQL_EXCLUIDOS_JOVENES no existia."""
    with _db_21("orbit21plan") as conn:
        _siembra_campana_21(conn)
        plan, _exc, jovenes = ar._plan_inertes(conn, "amazon_mx", "peso_muerto", 30, None, 30)
        assert _textos(plan) == {"vieja"}, f"el plan solo trae la vieja: {_textos(plan)}"
        assert jovenes == 1, "la joven se reporta, no se esconde"


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_21_puerta_de_edad_obedece_el_parametro():
    """--min-antiguedad-dias chico (3) deja entrar a la joven de 5 dias."""
    with _db_21("orbit21puerta") as conn:
        _siembra_campana_21(conn)
        plan, _exc, jovenes = ar._plan_inertes(conn, "amazon_mx", "peso_muerto", 30, None, 3)
        assert _textos(plan) == {"vieja", "joven"}
        assert jovenes == 0


def _estructura_21_minima():
    perfil = PerfilAds(
        profile_id=202,
        country="MX",
        currency_code="MXN",
        account_type="seller",
        valid_payment_method=True,
        account_name="Cuenta Test",
        aceptado=True,
        platform="amazon_mx",
        moneda="MXN",
    )
    return EstructuraAds(
        perfiles=[perfil],
        estructuras=[
            EstructuraPerfil(
                perfil=perfil,
                campanas=[
                    {
                        "campaignId": "CMP21S",
                        "name": "Camp sync",
                        "targetingType": "MANUAL",
                        "state": "ENABLED",
                        "budget": {"budget": 10.0, "budgetType": "DAILY"},
                    }
                ],
                ad_groups=[
                    {
                        "adGroupId": "AG21S",
                        "name": "AG sync",
                        "campaignId": "CMP21S",
                        "state": "ENABLED",
                        "defaultBid": 0.75,
                    }
                ],
                keywords=[
                    {
                        "keywordId": "KW21S",
                        "adGroupId": "AG21S",
                        "campaignId": "CMP21S",
                        "keywordText": "sincronizada",
                        "matchType": "EXACT",
                        "state": "ENABLED",
                        "bid": 0.5,
                    }
                ],
                targets=[],
                product_ads=[],
            )
        ],
    )


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_21_doble_sync_preserva_first_seen():
    """El re-sync NO rejuvenece: first_seen_at queda con la fecha vieja.

    Rojo contra el codigo previo (upsert sin first_seen_at o con
    now() tambien en UPDATE): el segundo sync movia la marca a hoy."""
    with _db_21("orbit21sync") as conn:
        assert sync_structure(conn, _estructura_21_minima()).ok is True
        vieja = dt.datetime.now(dt.UTC) - dt.timedelta(days=40)
        conn.execute(
            "UPDATE ad_entity SET first_seen_at = %s"
            " WHERE platform = 'amazon_mx' AND kind = 'keyword'",
            (vieja,),
        )
        assert sync_structure(conn, _estructura_21_minima()).ok is True
        marca = conn.execute(
            "SELECT first_seen_at FROM ad_entity WHERE platform = 'amazon_mx' AND kind = 'keyword'"
        ).fetchone()[0]
        assert marca == vieja, f"el re-sync debe preservar, no rejuvenecer: {marca}"


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres local")
def test_21_migracion_rellena_sin_nulos_y_fija_default():
    """Backfill = piso de migracion: filas viejas quedan selladas NOT NULL
    y la fila nueva trae DEFAULT now() sin que nadie lo pida."""
    dsn = _test_dsn()
    db = f"orbit21back_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_mx', 'campaign', 'PREVIA')"
        )
        conn.execute(SQL17)
        nulos = conn.execute(
            "SELECT count(*) FROM ad_entity WHERE first_seen_at IS NULL"
        ).fetchone()[0]
        assert nulos == 0, "backfill: ninguna fila vieja queda sin marca"
        notnull = conn.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name = 'ad_entity' AND column_name = 'first_seen_at'"
        ).fetchone()[0]
        assert notnull == "NO"
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_mx', 'campaign', 'NUEVA')"
        )
        marca = conn.execute(
            "SELECT first_seen_at FROM ad_entity WHERE external_id = 'NUEVA'"
        ).fetchone()[0]
        assert marca.date() == dt.datetime.now(dt.UTC).date(), f"default now(): {marca}"
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
