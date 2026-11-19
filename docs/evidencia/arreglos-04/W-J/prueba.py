"""Prueba real del bloque W-J (ARREGLOS 04, fabrica.md F4), para en_prod.sh.

Funciones puras e inspeccion de modulo con conexion y transporte falsos:
sin base, sin red, sin escritura en disco.
"""

from __future__ import annotations

import io
import json
from contextlib import ExitStack, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from tools import fabrica_campanas as fc

PASOS_INTERRUMPIDOS = (
    ("category_exact", "c-1", None, 1, "applied"),
    ("category_phrase", "c-phrase", None, 2, "planeado"),
)


class _Cursor:
    def __init__(self, filas):
        self._filas = list(filas)

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return list(self._filas)


class _ConnLote:
    def __init__(self, pasos=PASOS_INTERRUMPIDOS, *, estado_lote: str = "planeado"):
        self.pasos = list(pasos)
        self.estado_lote = estado_lote
        self._seq = len(self.pasos)
        self.consultas_campanas: list[str] = []

    def execute(self, sql: str, params=None):
        plano = " ".join(str(sql).split())
        bajo = plano.lower()
        if bajo.startswith("insert into fabrica_lote_paso"):
            self._seq += 1
            return _Cursor([(self._seq,)])
        if bajo.startswith("update fabrica_lote "):
            if "set estado =" in bajo and params:
                self.estado_lote = params[0]
            return _Cursor([])
        if bajo.startswith(("insert", "update", "delete")):
            return _Cursor([])
        if bajo.startswith("select estado from fabrica_lote"):
            return _Cursor([(self.estado_lote,)])
        if "from fabrica_lote" in bajo and "platform" in bajo:
            return _Cursor([("amazon_mx",)])
        if "max(orden)" in bajo:
            return _Cursor([(self._seq,)])
        if "from fabrica_lote_paso" in bajo and "recurso = 'campaign'" in bajo:
            self.consultas_campanas.append(sql)
            filtra_estado = "estado in" in bajo
            visibles = [
                (rol, ext, goal_id, orden)
                for rol, ext, goal_id, orden, estado in self.pasos
                if ext is not None and (not filtra_estado or estado in ("applied", "failed"))
            ]
            if "order by t.orden" in bajo:
                visibles.sort(key=lambda f: f[3])
            else:
                visibles.sort(key=lambda f: f[0])
            if "left join campana_grupo" in bajo:
                return _Cursor([(rol, ext, goal_id) for rol, ext, goal_id, _ in visibles])
            return _Cursor([(rol, ext) for rol, ext, _, _ in visibles])
        raise AssertionError(f"SQL inesperado: {plano}")

    def commit(self):
        pass

    def close(self):
        pass


class _ClienteLectura:
    def __init__(self, transporte):
        self._http = httpx.Client(transport=transporte)

    def list_objects(self, path, body, *, profile_id):
        return self._http.post(f"{fc.API}{path}", json=body)


def _transporte_estados(estados: dict[str, str], puts: list[tuple[str, str]]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path == "/sp/campaigns":
            cuerpo = json.loads(request.content)["campaigns"][0]
            cid, nuevo = cuerpo["campaignId"], cuerpo["state"]
            puts.append((cid, nuevo))
            estados[cid] = nuevo
            return httpx.Response(
                207, json={"campaigns": {"success": [{"index": 0, "campaignId": cid}], "error": []}}
            )
        cid = json.loads(request.content)["campaignIdFilter"]["include"][0]
        return httpx.Response(
            200, json={"campaigns": [{"campaignId": cid, "state": estados[cid], "name": "n"}]}
        )

    return httpx.MockTransport(handler)


def _parches_desarmar(stack: ExitStack, conn: _ConnLote, transporte: httpx.MockTransport) -> None:
    cred_falsa = SimpleNamespace(client_id="c")
    stack.enter_context(patch.object(fc, "_dsn_admin", return_value="dsn-falsa"))
    stack.enter_context(patch.object(fc, "connect", return_value=conn))
    stack.enter_context(
        patch.object(fc.AdsCredentials, "from_secrets_dir", return_value=cred_falsa)
    )
    stack.enter_context(patch.object(fc, "AdsClient", lambda _c: _ClienteLectura(transporte)))
    stack.enter_context(patch.object(fc, "_perfiles", return_value={"amazon_mx": 101}))
    stack.enter_context(patch.object(fc, "_token_lwa", return_value="t"))
    stack.enter_context(
        patch.object(
            fc,
            "httpx",
            SimpleNamespace(
                Client=lambda **_kw: httpx.Client(transport=transporte),
                Timeout=lambda **_kw: None,
            ),
        )
    )
    stack.enter_context(patch.object(fc.time, "sleep", lambda _s: None))


estados_desarme = {"c-1": "ENABLED", "c-phrase": "ENABLED"}
puts_desarme: list[tuple[str, str]] = []
conn_desarme = _ConnLote()
with ExitStack() as stack, redirect_stdout(io.StringIO()):
    _parches_desarmar(stack, conn_desarme, _transporte_estados(estados_desarme, puts_desarme))
    rc = fc._desarmar(SimpleNamespace(desarmar="LX1", acepto_mutacion_real=True, go="DESARMAR LX1"))
assert rc == 0
assert conn_desarme.estado_lote == "desarmado"
pausadas = [cid for cid, estado in puts_desarme if estado == "PAUSED"]
assert pausadas == ["c-1", "c-phrase"], (
    f"solo se pauso {pausadas}; c-phrase (id conocido) sigue ENABLED gastando"
)

assert "estado in" not in fc._SQL_CAMPANAS_DEL_LOTE.lower(), fc._SQL_CAMPANAS_DEL_LOTE
for ausente in ("_SQL_CAMPANAS_DEL_LOTE_PARA_PAUSA", "_SQL_CAMPANAS_CON_EXTERNAL"):
    assert not hasattr(fc, ausente), f"tools.fabrica_campanas conserva {ausente}"
assert hasattr(fc, "_SQL_EXTERNALS_CAMPANA_DEL_LOTE")

conn_vacia = _ConnLote(pasos=())
with ExitStack() as stack:
    _parches_desarmar(stack, conn_vacia, _transporte_estados({}, []))
    try:
        fc._desarmar(SimpleNamespace(desarmar="LV0", acepto_mutacion_real=True, go="DESARMAR LV0"))
    except fc.Abortar as exc:
        assert "LV0" in str(exc), str(exc)
        assert "applied, o failed" not in str(exc), str(exc)
    else:
        raise AssertionError("desarmar sin filas no aborto")

estados_ciclo = {"c-1": "ENABLED", "c-phrase": "ENABLED"}
puts_ciclo: list[tuple[str, str]] = []
transporte_ciclo = _transporte_estados(estados_ciclo, puts_ciclo)
conn_ciclo = _ConnLote(estado_lote="applied")
ctx = fc._Ctx(
    httpx.Client(transport=transporte_ciclo),
    "t",
    SimpleNamespace(client_id="c"),
    _ClienteLectura(transporte_ciclo),
    101,
    conn_ciclo,
    "LP2",
)
with patch.object(fc.time, "sleep", lambda _s: None):
    fc.pausar_lote(ctx, "LP2", "dueno")
    assert puts_ciclo == [("c-1", "PAUSED"), ("c-phrase", "PAUSED")], puts_ciclo
    puts_ciclo.clear()
    fc.reactiva_lote(ctx, "LP2", "dueno")
    assert puts_ciclo == [("c-1", "ENABLED"), ("c-phrase", "ENABLED")], puts_ciclo
assert conn_ciclo.consultas_campanas == [fc._SQL_CAMPANAS_DEL_LOTE, fc._SQL_CAMPANAS_DEL_LOTE]

print("W-J OK")
