"""B.3: aprobar, rechazar, quitar y reconciliar el bloqueo del dueno.

Postgres real y MockTransport. La prueba 4 usa dos conexiones. Una lista
truncada o ambigua no hace POST. El duplicado del motor queda fijado con
la respuesta simulada.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
from psycopg.types.json import Json
from test_architecture import PERMITIDOS_IMPORTAR_ADS_WRITE, _imports_runtime
from test_bloqueo_dueno import (
    _cola,
    _db,
    _ficha_real,
    _lote,
    _roster,
    _senal_ajena,
    _skip_db,
)

import app.bloqueo_dueno as lectura
import app.bloqueo_dueno_efecto as efecto
from app.ads.client import AdsAuthError
from app.ads.config import AdsCredentials
from app.ads.write import AdsWriteClient
from app.apply import Aplicador
from app.apply_cola import _ejecuta_negative, fila_cola
from app.redaction import REDACTED, register_secret

RAIZ = Path(__file__).resolve().parents[1]
FICHA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SHA = "c" * 64
PERFIL_MX = 111
PERFIL_US = 222


def _parche(monkeypatch):
    cred = AdsCredentials("cid", "csecret", "rtok")
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(lambda cls, *a, **k: cred))


def _perfiles():
    def uno(profile_id, pais, moneda):
        return {
            "profileId": profile_id,
            "countryCode": pais,
            "currencyCode": moneda,
            "accountInfo": {"type": "seller", "name": pais},
        }

    return [
        _perfiles_item(uno, PERFIL_MX, "MX", "MXN"),
        _perfiles_item(uno, PERFIL_US, "US", "USD"),
    ]


def _perfiles_item(uno, profile_id, pais, moneda):
    return uno(profile_id, pais, moneda)


def _interruptor(conn, valor=True):
    conn.execute(
        "INSERT INTO config_version (settings) VALUES (%s::jsonb)",
        (json.dumps({"jev.bloqueo": valor}),),
    )


def _grupo(conn, plataforma: str) -> tuple[int, str, str]:
    campana = f"c-{uuid.uuid4()}"
    padre = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES (%s, 'campaign', %s) RETURNING id",
        (plataforma, campana),
    ).fetchone()[0]
    grupo_ext = f"ag-{uuid.uuid4()}"
    grupo = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES (%s, 'ad_group', %s, %s) RETURNING id",
        (plataforma, grupo_ext, padre),
    ).fetchone()[0]
    return grupo, grupo_ext, campana


def _prepara(conn, plataforma="amazon_mx", termino="baño", sha=SHA):
    lote = _lote(conn)
    grupo, grupo_ext, campana = _grupo(conn, plataforma)
    ficha = str(uuid.uuid4())
    producto = _ficha_real(conn, plataforma, ficha, "aro")
    roster = uuid.uuid4().hex + uuid.uuid4().hex[:32]
    miembros = {
        "censo": [[["ad-1"], producto, [1], ["ENABLED"]]],
        "fichas": [ficha],
    }
    _roster(conn, grupo, plataforma, miembros, roster)
    senal = _senal_ajena(
        conn,
        grupo,
        termino,
        sha,
        roster,
        plataforma=plataforma,
        gasto=Decimal("13.6200"),
        lote=lote,
        miembros=1,
    )
    _interruptor(conn)
    return senal, grupo, grupo_ext, campana, termino, roster


def _transporte(
    grupo_ext,
    termino,
    *,
    modo="ok",
    explota=False,
    post_status=None,
    readback=None,
    detalle_post="transporte",
    secreto=None,
):
    store: list[dict] = []
    vistos: list[httpx.Request] = []
    seq = iter(range(1, 100))
    pagina = {"n": 0}
    listas = {"n": 0}
    post_visto = {"ok": False}

    def _aparece() -> None:
        store.append(
            {
                "keywordId": "kid-rb",
                "adGroupId": grupo_ext,
                "keywordText": termino,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
        )

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        if request.url.host == "api.amazon.com":
            if readback == "auth" and post_visto["ok"]:
                return httpx.Response(401, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        path = request.url.path
        if path == "/v2/profiles":
            return httpx.Response(200, json=_perfiles())
        if path == "/sp/negativeKeywords/list":
            if modo == "ambiguo":
                return httpx.Response(200, json={"negativeKeywords": "no"})
            if modo == "truncado":
                pagina["n"] += 1
                return httpx.Response(
                    200, json={"negativeKeywords": [], "nextToken": f"t{pagina['n']}"}
                )
            n = listas["n"]
            listas["n"] += 1
            if readback == "ambiguo" and n >= 1:
                return httpx.Response(200, json={"negativeKeywords": "no"})
            if readback == "auth" and n >= 1:
                return httpx.Response(401, json={"message": "unauthorized"})
            if readback == "vacio" and n >= 1:
                return httpx.Response(200, json={"negativeKeywords": []})
            return httpx.Response(200, json={"negativeKeywords": list(store)})
        if path == "/sp/negativeKeywords" and request.method == "POST":
            post_visto["ok"] = True
            if explota or post_status is not None:
                if readback == "enabled":
                    _aparece()
                if explota:
                    raise httpx.ConnectError(detalle_post)
                cuerpo = {"message": detalle_post}
                if secreto:
                    cuerpo["eco"] = secreto
                return httpx.Response(post_status, json=cuerpo)
            if readback in ("vacio", "ambiguo"):
                return httpx.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "error": [],
                            "success": [{"negativeKeywordId": "kid-fantasma"}],
                        }
                    },
                )
            body = json.loads(request.content)
            obj = body["negativeKeywords"][0]
            kid = f"kid-{next(seq)}"
            store.append(
                {
                    "keywordId": kid,
                    "adGroupId": obj["adGroupId"],
                    "keywordText": obj["keywordText"],
                    "matchType": "NEGATIVE_EXACT",
                    "state": "ENABLED",
                }
            )
            return httpx.Response(
                207,
                json={"negativeKeywords": {"error": [], "success": [{"negativeKeywordId": kid}]}},
            )
        if path == "/sp/negativeKeywords/delete":
            body = json.loads(request.content)
            kid = body["negativeKeywordIdFilter"]["include"][0]
            for item in store:
                if item["keywordId"] == str(kid):
                    item["state"] = "ARCHIVED"
            return httpx.Response(207, json={"negativeKeywords": {"success": [], "error": []}})
        return httpx.Response(400, json={"message": path})

    return httpx.MockTransport(handler), vistos, store


def _posts(vistos):
    return [r for r in vistos if r.method == "POST" and r.url.path == "/sp/negativeKeywords"]


def _pasos(conn, vid):
    return conn.execute(
        """
        SELECT paso, motivo, negative_keyword_id
          FROM bloqueo_efecto
         WHERE veredicto_id = %s
         ORDER BY created_at, id
        """,
        (vid,),
    ).fetchall()


def _dsn_de(conn) -> str:
    partes = urlsplit(os.environ["ORBIT_TEST_DSN"])
    return urlunsplit((partes.scheme, partes.netloc, "/" + conn.info.dbname, "", ""))


@_skip_db
def test_senal_cambio_anula_sin_http(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, grupo, grupo_ext, _campana, termino, roster = _prepara(conn)
        _senal_ajena(
            conn,
            grupo,
            termino,
            SHA,
            roster,
            plataforma="amazon_mx",
            gasto=Decimal("1"),
            lote=_lote(conn),
            miembros=1,
        )
        transport, vistos, _store = _transporte(grupo_ext, termino)
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.status == 200
        assert resultado.paso == "anulado"
        assert resultado.motivo == "senal_cambio"
        assert _pasos(conn, resultado.veredicto_id) == [("anulado", "senal_cambio", None)]
        assert vistos == []


@_skip_db
def test_interruptor_apagado_no_escribe(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo_id, grupo_ext, _campana, termino, _roster = _prepara(conn)
        _interruptor(conn, False)
        transport, vistos, _store = _transporte(grupo_ext, termino)
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado == efecto.Efecto(409, motivo="interruptor")
        assert conn.execute("SELECT count(*) FROM bloqueo_veredicto").fetchone()[0] == 0
        assert vistos == []
        texto = efecto.rechazar(conn, senal)
        assert texto.status == 409
        assert conn.execute("SELECT count(*) FROM bloqueo_veredicto").fetchone()[0] == 0


@_skip_db
def test_ledger_antes_del_http_y_reconciliacion(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(grupo_ext, termino, explota=True)
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.status == 200
        assert resultado.paso == "intencion"
        assert resultado.motivo == "por_confirmar"
        assert _pasos(conn, resultado.veredicto_id) == [("intencion", None, None)]
        assert len(_posts(vistos)) == 1

        vid = uuid.uuid4()
        conn.execute(
            "INSERT INTO bloqueo_veredicto (id, plataforma, ad_group_id, termino,"
            " termino_sha256, senal_id, huella_catalogo, tipo)"
            " VALUES (%s, 'amazon_mx', %s, %s, %s, %s, %s, 'aprobado')",
            (vid, grupo, termino, SHA, senal, "h" * 64),
        )
        conn.execute(
            "INSERT INTO bloqueo_efecto (id, veredicto_id, paso) VALUES (%s, %s, 'intencion')",
            (uuid.uuid4(), vid),
        )
        store = [
            {
                "keywordId": "kid-ya",
                "adGroupId": grupo_ext,
                "keywordText": termino,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
        ]
        transport_ok, _vistos, caja = _transporte(grupo_ext, termino)
        caja.extend(store)
        adoptado = efecto.reconciliar(
            conn, ad_group_id=grupo, termino_sha256=SHA, transport=transport_ok
        )
        assert adoptado.paso == "aplicado"
        assert _pasos(conn, vid)[-1] == ("aplicado", None, "kid-ya")
        otra = efecto.reconciliar(
            conn, ad_group_id=grupo, termino_sha256=SHA, transport=transport_ok
        )
        assert otra.motivo == "nada"
        assert _pasos(conn, vid)[-1] == ("aplicado", None, "kid-ya")

        vacio = uuid.uuid4()
        conn.execute(
            "INSERT INTO bloqueo_veredicto (id, plataforma, ad_group_id, termino,"
            " termino_sha256, senal_id, huella_catalogo, tipo)"
            " VALUES (%s, 'amazon_mx', %s, 'otra', %s, %s, %s, 'aprobado')",
            (vacio, grupo, "d" * 64, senal, "h" * 64),
        )
        conn.execute(
            "INSERT INTO bloqueo_efecto (id, veredicto_id, paso) VALUES (%s, %s, 'intencion')",
            (uuid.uuid4(), vacio),
        )
        limpio, _v, _c = _transporte(grupo_ext, "otra")
        sin = efecto.reconciliar(conn, ad_group_id=grupo, termino_sha256="d" * 64, transport=limpio)
        assert sin.paso == "fallido"
        assert sin.motivo == "sin_resultado"


@_skip_db
def test_dos_conexiones_un_solo_post(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo_id, grupo_ext, _campana, termino, _roster = _prepara(conn)
        paso = threading.Barrier(2)
        listo = threading.Event()
        vistos = []
        store = []

        def handler(request: httpx.Request) -> httpx.Response:
            if not listo.is_set():
                listo.set()
                paso.wait(timeout=20)
            return _responde(request, vistos, store, grupo_ext)

        transport = httpx.MockTransport(handler)
        otro = psycopg.connect(_dsn_de(conn), autocommit=True)
        transport2, vistos2, _store2 = _transporte(grupo_ext, termino)
        errores: list[BaseException] = []
        salida: dict = {}

        def primero():
            try:
                salida["uno"] = efecto.aprobar(conn, senal, transport=transport)
            except BaseException as exc:
                errores.append(exc)

        hilo = threading.Thread(target=primero)
        hilo.start()
        assert listo.wait(timeout=20)
        segundo = efecto.aprobar(otro, senal, transport=transport2)
        paso.wait(timeout=20)
        hilo.join(timeout=20)
        otro.close()
        assert errores == []
        assert segundo.status == 409
        assert segundo.motivo == "candado"
        assert vistos2 == []
        assert salida["uno"].paso == "aplicado"
        assert len(_posts(vistos)) == 1
        assert [p[0] for p in _pasos(conn, salida["uno"].veredicto_id)] == ["intencion", "aplicado"]


def _responde(request, vistos, store, grupo_ext):
    vistos.append(request)
    if request.url.host == "api.amazon.com":
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    if request.url.path == "/v2/profiles":
        return httpx.Response(200, json=_perfiles())
    if request.url.path == "/sp/negativeKeywords/list":
        return httpx.Response(200, json={"negativeKeywords": list(store)})
    if request.url.path == "/sp/negativeKeywords" and request.method == "POST":
        body = json.loads(request.content)
        obj = body["negativeKeywords"][0]
        kid = "kid-1"
        store.append(
            {
                "keywordId": kid,
                "adGroupId": obj["adGroupId"],
                "keywordText": obj["keywordText"],
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
        )
        return httpx.Response(
            207,
            json={"negativeKeywords": {"error": [], "success": [{"negativeKeywordId": kid}]}},
        )
    return httpx.Response(400, json={"message": request.url.path, "grupo": grupo_ext})


@_skip_db
def test_lista_truncada_o_ambigua_no_postea(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        for modo, motivo in (("ambiguo", "lista_ambiguo"), ("truncado", "lista_truncado")):
            senal, _g, grupo_ext, _c, termino, _roster = _prepara(conn, sha=modo * 32)
            transport, vistos, _store = _transporte(grupo_ext, termino, modo=modo)
            resultado = efecto.aprobar(conn, senal, transport=transport)
            assert resultado.paso == "fallido"
            assert resultado.motivo == motivo
            assert _posts(vistos) == []


@_skip_db
def test_quitar_usa_el_id_del_ledger_y_oculta_la_huella(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(grupo_ext, termino)
        hecho = efecto.aprobar(conn, senal, transport=transport)
        assert hecho.paso == "aplicado"
        assert "baño" in _posts(vistos)[0].content.decode()
        kid = _pasos(conn, hecho.veredicto_id)[-1][2]
        assert kid == "kid-1"
        quitado = efecto.quitar(
            conn,
            hecho.veredicto_id,
            transport=transport,
            negative_keyword_id_body="otro-id",
        )
        assert quitado.paso == "revertido"
        borrados = [
            json.loads(r.content)["negativeKeywordIdFilter"]["include"]
            for r in vistos
            if r.url.path == "/sp/negativeKeywords/delete"
        ]
        assert borrados == [[kid]]
        assert [p.termino for p in lectura.lee_propuestas(conn)] == []
        assert grupo > 0 and termino == "baño"


@_skip_db
def test_aprobar_con_clave_de_motor_anula_sin_http(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        _cola(conn, grupo, "amazon_mx", termino, estado="pending_veto")
        _interruptor(conn)
        transport, vistos, _store = _transporte(grupo_ext, termino)
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.paso == "anulado"
        assert resultado.motivo == "motor"
        assert vistos == []


@_skip_db
def test_aprobacion_us_usa_el_perfil_de_us(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _g, grupo_ext, campana, termino, _roster = _prepara(
            conn, plataforma="amazon_us", termino="ring", sha="d" * 64
        )
        transport, vistos, _store = _transporte(grupo_ext, termino)
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.paso == "aplicado"
        posts = _posts(vistos)
        assert len(posts) == 1
        assert posts[0].headers["Amazon-Advertising-API-Scope"] == str(PERFIL_US)
        cuerpo = json.loads(posts[0].content)
        assert cuerpo["negativeKeywords"][0]["campaignId"] == campana
        assert cuerpo["negativeKeywords"][0]["keywordText"] == "ring"


def test_allowlist_y_el_motor_no_importa_el_efecto():
    ruta = "app/bloqueo_dueno_efecto.py"
    assert ruta in PERMITIDOS_IMPORTAR_ADS_WRITE
    assert "quota negative" in PERMITIDOS_IMPORTAR_ADS_WRITE[ruta]
    assert "app.ads.write" in _imports_runtime(RAIZ / ruta)
    assert "app.ads.write" not in _imports_runtime(RAIZ / "app/bloqueo_dueno.py")
    prohibidos = ("app.bloqueo_dueno", "app.bloqueo_dueno_efecto")
    for rel in ("app/cycle.py", "app/apply_cola.py", "app/optimizer/__init__.py"):
        imports = _imports_runtime(RAIZ / rel)
        assert not any(nombre in imports for nombre in prohibidos), rel
    arbol = ast.parse((RAIZ / "app/cli.py").read_text(encoding="utf-8"))
    for nodo in arbol.body:
        if isinstance(nodo, ast.ImportFrom) and nodo.module and "bloqueo_dueno" in nodo.module:
            raise AssertionError(nodo.module)


def test_cli_sin_dsn_admin_sale_2(monkeypatch):
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    from app.cli import main as cli_main

    assert efecto.main([]) == 2
    assert efecto.main(["--sobra"]) == 2
    assert cli_main(["bloqueo-reconcilia"]) == 2
    assert cli_main(["bloqueo-reconcilia", "--sobra"]) == 2


def test_cli_reconcilia_aplica_orbit_pg_host(monkeypatch):
    # En el contenedor el DSN del .env apunta a 127.0.0.1; ORBIT_PG_HOST=db
    # lo reescribe (app.db.connect). Sin eso el comando falla en prod.
    import app.db as db

    vistos = []

    def falso(dsn, **kw):
        vistos.append(dsn)
        raise psycopg.OperationalError("sin red en la prueba")

    monkeypatch.setattr(db.psycopg, "connect", falso)
    monkeypatch.setenv("ORBIT_PG_HOST", "db")
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://u:p@127.0.0.1:5432/orbit")
    assert efecto.main([]) == 1
    assert vistos == ["postgresql://u:p@db:5432/orbit"]


@_skip_db
def test_cli_reconcilia_sin_huerfanos_sale_0(monkeypatch):
    with _db() as conn:
        monkeypatch.setenv("ORBIT_DSN_ADMIN", _dsn_de(conn))
        assert efecto.main([]) == 0


@_skip_db
def test_duplicado_del_motor_sella_ack_sin_id(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        _grupo_id, grupo_ext, campana = _grupo(conn, "amazon_mx")
        _cola(conn, _grupo_id, "amazon_mx", "baño")
        fila_id = conn.execute("SELECT id FROM apply_queue").fetchone()[0]
        payload = {
            "adGroupId": grupo_ext,
            "campaignId": campana,
            "keywordText": "baño",
        }
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', request_payload = %s WHERE id = %s",
            (Json(payload), fila_id),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying' WHERE id = %s",
            (fila_id,),
        )
        vistos = []

        def handler(request: httpx.Request) -> httpx.Response:
            vistos.append(request)
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/sp/negativeKeywords" and request.method == "POST":
                return httpx.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "error": [
                                {"index": 0, "code": "DUPLICATE", "negativeKeywordId": "kid-dup"}
                            ],
                            "success": [],
                        }
                    },
                )
            return httpx.Response(400, json={"message": request.url.path})

        aplicador = Aplicador(
            conn,
            platform="amazon_mx",
            cycle_id_ejecutor=conn.execute("SELECT id FROM optimizer_cycle").fetchone()[0],
            owner="test",
            job_key="test",
            transport=httpx.MockTransport(handler),
            sleep=lambda _seconds: None,
            credentials=AdsCredentials("cid", "csecret", "rtok"),
            profile_id=PERFIL_MX,
        )
        fila = fila_cola(conn, fila_id)
        assert _ejecuta_negative(conn, aplicador, fila) == "failed"
        estado = conn.execute(
            "SELECT estado FROM apply_queue WHERE id = %s", (fila_id,)
        ).fetchone()[0]
        resultado = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s",
            (fila.decision_id,),
        ).fetchone()[0]
        assert estado == "failed"
        assert resultado == "fallo:ack_sin_id"


@_skip_db
def test_rechazar_escribe_sin_http_y_409_si_la_senal_cambio(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, grupo, grupo_ext, _c, termino, roster = _prepara(conn)
        transport, vistos, _store = _transporte(grupo_ext, termino)
        hecho = efecto.rechazar(conn, senal)
        assert hecho.paso == "rechazado"
        assert vistos == []
        tipo = conn.execute(
            "SELECT tipo, termino_sha256 FROM bloqueo_veredicto WHERE id = %s",
            (hecho.veredicto_id,),
        ).fetchone()
        assert tipo == ("rechazado", SHA)
        _senal_ajena(
            conn,
            grupo,
            termino,
            SHA,
            roster,
            plataforma="amazon_mx",
            gasto=Decimal("1"),
            lote=_lote(conn),
            miembros=1,
        )
        cambio = efecto.rechazar(conn, senal)
        assert cambio.status == 409
        assert (
            conn.execute(
                "SELECT count(*) FROM bloqueo_veredicto WHERE tipo = 'rechazado'"
            ).fetchone()[0]
            == 1
        )


@_skip_db
def test_pausa_de_la_plataforma_responde_409(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _g, grupo_ext, _c, termino, _roster = _prepara(conn)
        for n in range(20):
            tipo = "aprobado" if n < 13 else "rechazado"
            conn.execute(
                "INSERT INTO bloqueo_veredicto (id, plataforma, ad_group_id, termino,"
                " termino_sha256, senal_id, huella_catalogo, tipo, created_at)"
                " VALUES (%s, 'amazon_mx', %s, %s, %s, %s, %s, %s, %s)",
                (
                    uuid.uuid4(),
                    _g,
                    f"t{n}",
                    f"{n:064x}",
                    senal,
                    "h" * 64,
                    tipo,
                    datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=n),
                ),
            )
        transport, vistos, _store = _transporte(grupo_ext, termino)
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.status == 409
        assert resultado.motivo == "pausa"
        assert vistos == []


@_skip_db
def test_intencion_es_visible_desde_otra_conexion_antes_del_post(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _g, grupo_ext, _c, termino, _roster = _prepara(conn)
        lector = psycopg.connect(_dsn_de(conn), autocommit=True)
        escritora = psycopg.connect(_dsn_de(conn), autocommit=False)
        vistos = []
        store = []
        pasos_en_el_post: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path == "/sp/negativeKeywords":
                pasos_en_el_post.append(
                    [r[0] for r in lector.execute("SELECT paso FROM bloqueo_efecto").fetchall()]
                )
            return _responde(request, vistos, store, grupo_ext)

        try:
            hecho = efecto.aprobar(escritora, senal, transport=httpx.MockTransport(handler))
        finally:
            escritora.close()
            lector.close()
        assert hecho.paso == "aplicado"
        assert pasos_en_el_post == [["intencion"]]


@_skip_db
def test_rechazar_dos_veces_es_un_solo_veredicto(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _g, _ge, _c, _t, _roster = _prepara(conn)
        uno = efecto.rechazar(conn, senal)
        dos = efecto.rechazar(conn, senal)
        assert uno.paso == "rechazado"
        assert (dos.status, dos.paso, dos.motivo) == (200, "rechazado", "ya_rechazado")
        assert dos.veredicto_id == uno.veredicto_id
        assert (
            conn.execute(
                "SELECT count(*) FROM bloqueo_veredicto WHERE tipo = 'rechazado'"
            ).fetchone()[0]
            == 1
        )


@_skip_db
def test_pausa_no_cuenta_anulados_ni_fallidos_de_amazon():
    with _db() as conn:
        senal, grupo, _ge, _c, _t, _roster = _prepara(conn)
        for n in range(20):
            vid = uuid.uuid4()
            conn.execute(
                "INSERT INTO bloqueo_veredicto (id, plataforma, ad_group_id, termino,"
                " termino_sha256, senal_id, huella_catalogo, tipo, created_at)"
                " VALUES (%s, 'amazon_mx', %s, %s, %s, %s, %s, %s, %s)",
                (
                    vid,
                    grupo,
                    f"t{n}",
                    f"{n:064x}",
                    senal,
                    "h" * 64,
                    "aprobado" if n < 13 else "rechazado",
                    datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=n),
                ),
            )
            if n < 3:
                pasos = [("anulado", "senal_cambio")]
            elif n < 6:
                pasos = [("intencion", None), ("fallido", "http_500")]
            else:
                pasos = []
            for paso, motivo in pasos:
                conn.execute(
                    "INSERT INTO bloqueo_efecto (id, veredicto_id, paso, motivo)"
                    " VALUES (%s, %s, %s, %s)",
                    (uuid.uuid4(), vid, paso, motivo),
                )
        assert lectura.lee_pausas(conn)["amazon_mx"] == lectura.Pausa(False, 7, 14)


SECRETO_R1 = "sekreto-r1-9f3a"
MARCA_R1 = "marca-cuerpo-r1"


def _log_tiene(caplog, texto: str) -> bool:
    return any(texto in rec.getMessage() for rec in caplog.records)


@_skip_db
def test_r1_a_transporte_y_list_enabled_queda_aplicado(monkeypatch, caplog):
    _parche(monkeypatch)
    register_secret(SECRETO_R1)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(
            grupo_ext,
            termino,
            explota=True,
            readback="enabled",
            detalle_post=f"{MARCA_R1} {SECRETO_R1}",
        )
        with caplog.at_level(logging.WARNING, logger="app.bloqueo_dueno_efecto"):
            resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.status == 200
        assert resultado.paso == "aplicado"
        assert resultado.motivo is None
        assert _pasos(conn, resultado.veredicto_id) == [
            ("intencion", None, None),
            ("aplicado", None, "kid-rb"),
        ]
        assert len(_posts(vistos)) == 1
        assert _log_tiene(caplog, MARCA_R1)
        assert _log_tiene(caplog, str(resultado.veredicto_id))
        assert not _log_tiene(caplog, SECRETO_R1)
        assert _log_tiene(caplog, REDACTED)


@_skip_db
def test_r1_b_5xx_y_list_enabled_queda_aplicado(monkeypatch, caplog):
    _parche(monkeypatch)
    register_secret(SECRETO_R1)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(
            grupo_ext,
            termino,
            post_status=503,
            readback="enabled",
            detalle_post=MARCA_R1,
            secreto=SECRETO_R1,
        )
        with caplog.at_level(logging.WARNING, logger="app.bloqueo_dueno_efecto"):
            resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.status == 200
        assert resultado.paso == "aplicado"
        assert _pasos(conn, resultado.veredicto_id) == [
            ("intencion", None, None),
            ("aplicado", None, "kid-rb"),
        ]
        assert len(_posts(vistos)) == 1
        assert _log_tiene(caplog, "503")
        assert _log_tiene(caplog, MARCA_R1)
        assert not _log_tiene(caplog, SECRETO_R1)
        assert _log_tiene(caplog, REDACTED)


@_skip_db
def test_r1_c_4xx_sella_fallido_http(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(
            grupo_ext,
            termino,
            post_status=400,
            readback="enabled",
            detalle_post="rechazo-cliente",
        )
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado == efecto.Efecto(
            200, veredicto_id=resultado.veredicto_id, paso="fallido", motivo="http_400"
        )
        assert _pasos(conn, resultado.veredicto_id) == [
            ("intencion", None, None),
            ("fallido", "http_400", None),
        ]
        assert len(_posts(vistos)) == 1


@_skip_db
def test_r1_auth_antes_del_post_sella_fallido(monkeypatch):
    """Un rechazo de token no es un POST incierto: no se envio nada."""
    _parche(monkeypatch)

    def _nunca_enviado(self, ad_group_id, campaign_id, keyword_text):
        raise AdsAuthError("refresh de LWA no produjo access_token")

    monkeypatch.setattr(AdsWriteClient, "crear_negative_exacto", _nunca_enviado)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(grupo_ext, termino, readback="enabled")
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.paso == "fallido"
        assert resultado.motivo != "por_confirmar"
        assert _pasos(conn, resultado.veredicto_id)[-1][0] == "fallido"
        assert _posts(vistos) == []


@_skip_db
@_skip_db
def test_r1_auth_en_readback_deja_la_intencion_abierta(monkeypatch):
    """Un 401 con el refresh caido, despues del POST, no sella fallido."""
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(
            grupo_ext, termino, post_status=503, readback="auth"
        )
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert (resultado.status, resultado.paso, resultado.motivo) == (
            200,
            "intencion",
            "por_confirmar",
        )
        assert _pasos(conn, resultado.veredicto_id) == [("intencion", None, None)]
        assert len(_posts(vistos)) == 1


@_skip_db
def test_r1_429_agotado_sella_fallido_sin_readback(monkeypatch):
    """429 agotado es rechazo sin procesar: fallido, aunque el LIST vea ENABLED."""
    _parche(monkeypatch)
    original = AdsWriteClient.__init__

    def _init(self, *args, **kwargs):
        kwargs["sleep"] = lambda _s: None
        original(self, *args, **kwargs)

    monkeypatch.setattr(AdsWriteClient, "__init__", _init)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(
            grupo_ext,
            termino,
            post_status=429,
            readback="enabled",
            detalle_post="throttled",
        )
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado == efecto.Efecto(
            200, veredicto_id=resultado.veredicto_id, paso="fallido", motivo="http_429"
        )
        assert _pasos(conn, resultado.veredicto_id) == [
            ("intencion", None, None),
            ("fallido", "http_429", None),
        ]
        assert len(_posts(vistos)) == 4


@_skip_db
def test_r1_d_5xx_sin_list_queda_intencion_y_reconcilia(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, _vistos, _store = _transporte(
            grupo_ext, termino, post_status=500, readback="vacio", detalle_post=MARCA_R1
        )
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert (resultado.status, resultado.paso, resultado.motivo) == (
            200,
            "intencion",
            "por_confirmar",
        )
        assert _pasos(conn, resultado.veredicto_id) == [("intencion", None, None)]

        transport_ok, _v, caja = _transporte(grupo_ext, termino)
        caja.append(
            {
                "keywordId": "kid-luego",
                "adGroupId": grupo_ext,
                "keywordText": termino,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
        )
        cerrado = efecto.reconciliar(
            conn, ad_group_id=grupo, termino_sha256=SHA, transport=transport_ok
        )
        assert cerrado.paso == "aplicado"
        assert _pasos(conn, resultado.veredicto_id)[-1] == ("aplicado", None, "kid-luego")


@_skip_db
def test_r1_d_lista_no_ok_deja_la_intencion_abierta(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, _vistos, _store = _transporte(
            grupo_ext, termino, post_status=502, readback="ambiguo"
        )
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert (resultado.status, resultado.paso, resultado.motivo) == (
            200,
            "intencion",
            "por_confirmar",
        )
        assert _pasos(conn, resultado.veredicto_id) == [("intencion", None, None)]


@_skip_db
def test_r1_e_post_ok_sin_readback_queda_intencion(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(grupo_ext, termino, readback="vacio")
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert (resultado.status, resultado.paso, resultado.motivo) == (
            200,
            "intencion",
            "por_confirmar",
        )
        assert resultado.motivo != "readback"
        assert _pasos(conn, resultado.veredicto_id) == [("intencion", None, None)]
        assert len(_posts(vistos)) == 1


@_skip_db
def test_r1_e_post_ok_lista_ambigua_queda_intencion(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(grupo_ext, termino, readback="ambiguo")
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert (resultado.status, resultado.paso, resultado.motivo) == (
            200,
            "intencion",
            "por_confirmar",
        )
        assert _pasos(conn, resultado.veredicto_id) == [("intencion", None, None)]
        assert len(_posts(vistos)) == 1
