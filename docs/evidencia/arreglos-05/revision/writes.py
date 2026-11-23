"""Revalidacion de escrituras: PostgreSQL aislado y HTTP por MockTransport.

Uso desde el checkout: ORBIT_REVIEW_ROOT=/repo ORBIT_REVIEW_DSN=... python -B writes.py
Solo crea/elimina thermo_writes_current. No fija un SHA esperado: informa el observado.
Las semillas se reutilizan de tests del checkout, no de una revision anterior.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True
import os
import json
import datetime as dt
import subprocess
from pathlib import Path
from contextlib import contextmanager, ExitStack, redirect_stdout
from io import StringIO
from unittest.mock import patch

ROOT = Path(os.environ.get("ORBIT_REVIEW_ROOT", os.getcwd())).resolve()
if not (ROOT / "app/apply.py").is_file():
    raise RuntimeError("Ejecutar desde checkout o definir ORBIT_REVIEW_ROOT")
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
DSN = os.environ["ORBIT_REVIEW_DSN"]
os.environ["ORBIT_TEST_DSN"] = DSN

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Json
import httpx
from fastapi import HTTPException
import test_apply as ta
import test_apply_harvest as th
import test_fabrica_f2_hermanas as tf
from app import apply as a, apply_cola as ac, apply_harvest as h, api_write, cycle
from app.optimizer.goals import ModoEfectivo
from app.ads.config import AdsCredentials
from app.ads.client import AdsApiError
from tools import archiva_inertes as ar, reversa_harvest as rh
# El SQL del ledger de archivo vive en el motor; tools/archiva_inertes solo reenvia el CLI.
from app.ads.archivar_inertes import _SQL_INSERT_PLANEADO, _SQL_SELLA_APPLIED

DBNAME = "thermo_writes_current"
SAFE_HOSTS = {"127.0.0.1", "localhost", "orbit-review-current-pg"}
if conninfo_to_dict(DSN).get("host") not in SAFE_HOSTS:
    raise RuntimeError("Solo se admite PG de laboratorio explicito")
DBDSN = make_conninfo(DSN, dbname=DBNAME)
assert Path(a.__file__).resolve().is_relative_to(ROOT)
CRED = AdsCredentials(client_id="fake-writes-id", client_secret="fake-writes-secret",
                      refresh_token="fake-writes-refresh")


def metadata():
    sha = os.environ.get("ORBIT_REVIEW_SHA")
    if not sha:
        sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    return {"sha": sha, "python": sys.version, "root": str(ROOT),
            "apply_import": a.__file__, "harvest_import": h.__file__,
            "tools_import": ar.__file__, "database": DBNAME}


@contextmanager
def database():
    admin = psycopg.connect(DSN, dbname="postgres", autocommit=True)
    conn = None
    created = False
    try:
        # Si ya existe, fallar sin tocarla: puede pertenecer a otra ejecucion.
        admin.execute("CREATE DATABASE thermo_writes_current")
        created = True
        conn = psycopg.connect(DBDSN, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            if path.name.startswith("0011_") or "_reversa_" in path.name:
                continue
            conn.execute(path.read_text())
        yield conn
    finally:
        if conn:
            conn.close()
        if created:
            admin.execute("DROP DATABASE thermo_writes_current WITH (FORCE)")
        admin.close()


class Amazon:
    """Doble solo del servicio externo: cliente, parseo, SQL y logica son reales."""
    def __init__(self):
        self.requests = []
        self.negatives = []
        self.keywords = []
        self.bid = 1.0
        self.timeout_bid = False
        self.timeout_negative = False
        self.shape_negative = "ok"
        self.replace_keyword = False
        self.replaced = False
        self.fail_keyword_readback = False
        self.seq = 0

    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content and path != "/auth/o2/token" else {}
        self.requests.append((request.method, path, body))
        if path == "/auth/o2/token":
            return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600})
        if path == "/v2/profiles":
            return httpx.Response(200, json=[{
                "profileId": 404040, "countryCode": "US", "currencyCode": "USD",
                "accountInfo": {"type": "seller", "name": "Prueba", "validPaymentMethod": True},
            }])
        if "bidRecommendations" in path:
            return httpx.Response(403, json={"code": "403"})
        if request.method == "PUT" and path == "/sp/keywords":
            if self.timeout_bid:
                raise httpx.ReadTimeout("respuesta perdida", request=request)
            self.bid = body["keywords"][0]["bid"]
            return httpx.Response(207, json={"keywords": {"success": [{"keywordId": "7201"}], "error": []}})
        if path in ("/sp/keywords/list", "/sp/negativeKeywords/list"):
            negative = path == "/sp/negativeKeywords/list"
            key = "negativeKeywords" if negative else "keywords"
            store = self.negatives if negative else self.keywords
            if negative and self.shape_negative == "missing":
                return httpx.Response(200, json={})
            if negative and self.shape_negative == "truncated":
                return httpx.Response(200, json={key: [], "nextToken": "otra-pagina"})
            if not negative and self.fail_keyword_readback:
                return httpx.Response(503, json={"message": "sin lectura"})
            if not negative and self.replace_keyword and store and not self.replaced:
                own = store[0]
                own["state"] = "ARCHIVED"
                store.append({**own, "keywordId": "ajena", "state": "ENABLED"})
                self.replaced = True
            rows = list(store)
            group_filter = (body.get("adGroupIdFilter") or {}).get("include")
            id_filter = (body.get("keywordIdFilter") or {}).get("include")
            if group_filter:
                rows = [x for x in rows if str(x["adGroupId"]) in group_filter]
            if id_filter:
                rows = [x for x in rows if str(x["keywordId"]) in id_filter]
            if not negative and not self.keywords:
                rows = [{"keywordId": "7201", "keywordText": "kw 7201", "adGroupId": "7101",
                         "matchType": "EXACT", "state": "ENABLED", "bid": self.bid}]
            return httpx.Response(200, json={key: rows, "totalResults": len(rows)})
        if path in ("/sp/negativeKeywords", "/sp/keywords") and request.method == "POST":
            negative = path == "/sp/negativeKeywords"
            key = "negativeKeywords" if negative else "keywords"
            obj = body[key][0]
            self.seq += 1
            kid = ("n-" if negative else "k-") + str(self.seq)
            store = self.negatives if negative else self.keywords
            store.append({**obj, "keywordId": kid})
            if negative and self.timeout_negative:
                self.timeout_negative = False
                raise httpx.ReadTimeout("aceptada en Amazon; respuesta perdida", request=request)
            field = "negativeKeywordId" if negative else "keywordId"
            return httpx.Response(207, json={key: {"success": [{"index": 0, field: kid}], "error": []}})
        if path in ("/sp/negativeKeywords/delete", "/sp/keywords/delete"):
            negative = path.startswith("/sp/negative")
            key = "negativeKeywords" if negative else "keywords"
            field = "negativeKeywordId" if negative else "keywordId"
            ids = body[field + "Filter"]["include"]
            for obj in self.negatives if negative else self.keywords:
                if obj["keywordId"] in ids:
                    obj["state"] = "ARCHIVED"
            return httpx.Response(207, json={key: {"success": [{field: ids[0]}], "error": []}})
        raise AssertionError(f"HTTP inesperado: {request.method} {path}")

    def posts(self, path):
        return [body for method, p, body in self.requests if method == "POST" and p == path]


@contextmanager
def external_io(server):
    """Solo sustituye transporte y credenciales; no sustituye funciones de negocio."""
    original_init = httpx.Client.__init__
    def init(client, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(server)
        original_init(client, *args, **kwargs)
    def blocked(*args, **kwargs):
        raise AssertionError("HTTP real prohibido")
    with ExitStack() as stack:
        stack.enter_context(patch.object(httpx.Client, "__init__", init))
        stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", blocked))
        stack.enter_context(patch.object(AdsCredentials, "from_secrets_dir", return_value=CRED))
        stack.enter_context(patch.dict(os.environ, {"ORBIT_DSN_ADMIN": DBDSN, "ORBIT_DSN_DECIDE": DBDSN}))
        yield


def applicator(conn, server, cycle_id, **kwargs):
    return a.Aplicador(conn, platform="amazon_us", profile_id=404040,
                       credentials=CRED, cycle_id_ejecutor=cycle_id,
                       owner="writes:review", job_key="ads_optimizer:amazon_us",
                       transport=httpx.MockTransport(server), sleep=lambda _: None, **kwargs)


def scenario01(mode="shadow", enabled=True):
    with database() as conn:
        server = Amazon()
        with external_io(server):
            ids = ta._semilla(conn, mode_ciclo_dec="live")
            dec = ta._decision_bid(conn, ids["ciclo_dec"], ids["config"], ids["kw"])
            ap = applicator(conn, server, ids["ciclo_dec"])
            server.timeout_bid = True
            try:
                ap.aplica_bids(a.bids_del_ciclo(conn, ids["ciclo_dec"]), escalera_global="live")
            except AdsApiError:
                pass
            assert conn.execute("SELECT count(*) FROM apply_attempt WHERE decision_id=%s AND finished_at IS NULL", (dec,)).fetchone()[0] == 1
            # Opt-out de campana: mismo estado permitido por goals_write.
            conn.execute("INSERT INTO ads_optimizer_goal (scope,ad_entity_id,bid_floor,bid_ceiling,bid_currency,enabled,mode) VALUES ('campaign',%s,0.1,2.5,'USD',%s,%s)", (ids["camp"], enabled, mode))
            before = len(server.requests)
            server.timeout_bid = False
            cycle._toma_claim(conn, "ads_optimizer:amazon_us", "writes:review")
            guard = cycle._guard_apply(conn, "ads_optimizer:amazon_us", "writes:review", lambda: None)
            def factory(c, **kwargs):
                return applicator(c, server, kwargs["cycle_id_ejecutor"], tick=kwargs["tick"], guard_http=kwargs["tick"])
            result = cycle._fase_apply(conn, cycle_id=ids["ciclo_ejec"], modo=ModoEfectivo("live", None), platform="amazon_us", decided_at=dt.datetime.now(dt.UTC), job_key="ads_optimizer:amazon_us", owner="writes:review", guard=guard, aplicador_factory=factory)
            assert not result.fallo, result.notas
            return {"mode": mode, "enabled": enabled, "puts_recovery": sum(m == "PUT" for m, p, b in server.requests[before:]), "ledger": conn.execute("SELECT resultado FROM apply_attempt WHERE decision_id=%s ORDER BY seq", (dec,)).fetchall(), "phase": result.notas}


def negative_pending(conn, server):
    ids = th._semilla(conn)
    term = "termino malo"
    now = dt.datetime.now(dt.UTC)
    for offset in range(12, 42):
        th._termino_obs(conn, ids["run"], ids["ag"], term, now.date()-dt.timedelta(days=offset), clicks=100, cost="100", orders=0, ad_revenue="0")
    dec = th._decision_negative(conn, ids["ciclo_dec"], ids["config"], ids["ag"], term=term)
    ap = applicator(conn, server, ids["ciclo_ejec"])
    ac.encola_cortes(conn, ap, ids["ciclo_dec"], modo_envelope="live", ahora=now-dt.timedelta(days=3))
    qid = conn.execute("SELECT id FROM apply_queue WHERE decision_id=%s", (dec,)).fetchone()[0]
    server.timeout_negative = True
    try:
        ac.libera_vencidos(conn, "amazon_us", ahora=now, aplicador=ap)
    except AdsApiError:
        pass
    state = conn.execute("SELECT estado FROM apply_queue WHERE id=%s", (qid,)).fetchone()[0]
    assert state == "applying", state
    assert len(server.negatives) == 1
    assert conn.execute("SELECT count(*) FROM apply_attempt WHERE decision_id=%s AND finished_at IS NULL", (dec,)).fetchone()[0] == 1
    return ids, dec, qid, ap


def scenario02(shape="missing"):
    with database() as conn:
        server = Amazon()
        with external_io(server):
            ids, dec, qid, ap = negative_pending(conn, server)
            before = len(server.posts("/sp/negativeKeywords"))
            server.shape_negative = shape
            lectura_ambigua = None
            try:
                h.reconcilia_harvest(conn, ap, "amazon_us")
            except AdsApiError as exc:
                # Tambien es valido propagar la lectura ambigua sin mutar.
                lectura_ambigua = type(exc).__name__
            return {"shape": shape, "reposts": len(server.posts("/sp/negativeKeywords"))-before,
                    "queue": conn.execute("SELECT estado FROM apply_queue WHERE id=%s", (qid,)).fetchone()[0],
                    "lectura_ambigua": lectura_ambigua,
                    "confirmada": conn.execute(
                        "SELECT EXISTS (SELECT 1 FROM decision_application"
                        " WHERE decision_id=%s AND verify_ok)", (dec,)
                    ).fetchone()[0]}


def scenario10():
    with database() as conn:
        server = Amazon()
        with external_io(server):
            ids, dec, qid, ap = negative_pending(conn, server)
            h.reconcilia_harvest(conn, ap, "amazon_us")
            assert conn.execute("SELECT estado FROM apply_queue WHERE id=%s", (qid,)).fetchone()[0] == "applied"
            attempts = conn.execute("SELECT resultado,ack FROM apply_attempt WHERE decision_id=%s", (dec,)).fetchall()
            summary = conn.execute("SELECT platform_ack FROM decision_application WHERE decision_id=%s", (dec,)).fetchone()[0]
            try:
                result = api_write.reversa_negative(_token="auth-simulada", conn=conn, cuerpo=api_write.CuerpoReversaNegative(queue_id=qid))
                status = 200
            except HTTPException as exc:
                status, result = exc.status_code, str(exc.detail)
            return {"status": status, "result": result, "ledger": attempts, "summary": summary,
                    "deletes": server.posts("/sp/negativeKeywords/delete")}


def scenario03():
    with database() as conn:
        server = Amazon()
        with external_io(server):
            setup = tf._grupo_listo(conn)
            exact = setup["roles"]["category_exact"]
            dec = tf._decision_harvest_grupo(conn, setup["ciclo_dec"], setup["config"], setup["origen"]["ag"], grupo_id=setup["grupo_id"], exacta_camp_ext=exact["camp_ext"], exacta_ag_ext=exact["ag_ext"])
            ap = applicator(conn, server, setup["ciclo_ejec"])
            ac.encola_cortes(conn, ap, setup["ciclo_dec"], modo_envelope="live", ahora=dt.datetime.now(dt.UTC)-dt.timedelta(days=3))
            server.replace_keyword = True
            ac.libera_vencidos(conn, "amazon_us", ahora=dt.datetime.now(dt.UTC), aplicador=ap)
            row = conn.execute("SELECT id,fase,external_ids FROM harvest_job WHERE decision_id=%s", (dec,)).fetchone()
            assert row, "El flujo no alcanzo harvest"
            jid, fase, ext = row
            ack = conn.execute("SELECT ack FROM apply_attempt WHERE decision_id=%s AND request_payload->>'matchType'='EXACT'", (dec,)).fetchall()
            deletes = []
            if fase == "done":
                platform, term, decision_id, steps = h.plan_reversa_harvest(conn, jid)
                pending = rh._pendientes(conn, decision_id, steps)
                # CLI real: ceremonia y readback reales, transporte externo falso.
                with redirect_stdout(StringIO()):
                    rh.main(["--job", str(jid), "--acepto-mutacion-real", "--esperado", str(len(pending)), "--huella", rh._huella_pasos(pending), "--go", "prueba aislada"])
                deletes = server.posts("/sp/keywords/delete")
            return {"fase": fase, "external_ids": ext, "ack_keyword": ack,
                    "deletes_keyword": deletes, "keywords_remote": server.keywords}


def scenario11():
    with database() as conn:
        server = Amazon()
        with external_io(server):
            ids = ta._semilla(conn)
            # Estado aplicado legal: una keyword archivada con ack y readback.
            fid = conn.execute(_SQL_INSERT_PLANEADO, ("writes-lote", ids["kw"], "amazon_us", "7001", "7101", "7201", "kw 7201", "EXACT", 1, "USD", "peso_muerto", 90, "archivo autorizado")).fetchone()[0]
            conn.execute(_SQL_SELLA_APPLIED, (json.dumps({"keywords": {"success": [{"keywordId": "7201"}]}}), "ARCHIVED", fid))
            server.fail_keyword_readback = True
            outcomes = []
            for _ in range(2):
                with patch.object(sys, "argv", ["archiva_inertes.py", "--reponer", "writes-lote", "--acepto-mutacion-real", "--go", "prueba aislada"]), redirect_stdout(StringIO()):
                    try:
                        ar.main()
                        outcomes.append("ok")
                    except ar.Abortar as exc:
                        outcomes.append(str(exc))
            state = conn.execute("SELECT estado,repuesto_external,repuesto_ack FROM keyword_archivo_manual WHERE id=%s", (fid,)).fetchone()
            return {"posts": len(server.posts("/sp/keywords")), "ledger": state, "outcomes": outcomes}


def main():
    print(json.dumps({"metadata": metadata()}, ensure_ascii=False))
    cases = [("01", lambda: scenario01()), ("02", scenario02), ("03", scenario03), ("10", scenario10), ("11", scenario11)]
    for id_, run in cases:
        print(json.dumps({"id": id_, "observado": run()}, default=str, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
