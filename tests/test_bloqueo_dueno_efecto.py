"""B.3: aprobar, rechazar, quitar y reconciliar el bloqueo del dueno.

Postgres real y MockTransport. La prueba 4 usa dos conexiones. Una lista
truncada o ambigua no hace POST. El duplicado del motor queda fijado con
la respuesta simulada.
"""

from __future__ import annotations

import ast
import json
import logging
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
from test_schema import _test_dsn

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
            if readback == "sin_id" and n >= 1:
                return httpx.Response(
                    200,
                    json={
                        "negativeKeywords": [
                            {
                                "adGroupId": grupo_ext,
                                "keywordText": termino,
                                "matchType": "NEGATIVE_EXACT",
                                "state": "ENABLED",
                            }
                        ]
                    },
                )
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
            if readback in ("vacio", "ambiguo", "sin_id"):
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
    partes = urlsplit(_test_dsn())
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
    modulos_ciclo = sorted(
        p.relative_to(RAIZ).as_posix() for p in (RAIZ / "app").rglob("cycle*.py")
    )
    assert {
        "app/cycle.py",
        "app/cycle_apply.py",
        "app/cycle_grupos.py",
        "app/cycle_recorrido.py",
        "app/cycle_temporada.py",
    } <= set(modulos_ciclo)
    for rel in (*modulos_ciclo, "app/apply_cola.py", "app/optimizer/__init__.py"):
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


@_skip_db
def test_r2_pantalla_dice_por_confirmar_al_recargar(monkeypatch):
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, _vistos, _store = _transporte(
            grupo_ext, termino, post_status=503, readback="vacio"
        )
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert resultado.paso == "intencion"
        filas = [
            f
            for f in lectura.lee_pantalla(conn)["bloqueos"]
            if f["veredicto_id"] == str(resultado.veredicto_id)
        ]
        assert [(f["paso"], f["motivo"], f["quitar"]) for f in filas] == [
            ("intencion", "por_confirmar", False)
        ]
        from app import ui

        html = ui.templates.env.get_template("cortes.html").render(
            pantalla="cortes",
            items=[],
            bloqueos_disponibles=True,
            bloqueos_jev=lectura.lee_pantalla(conn),
        )
        assert 'Por aplicar <span class="detalle-celda">por confirmar en Amazon</span>' in html


@_skip_db
def test_r2_readback_sin_keyword_id_no_sella_aplicado(monkeypatch):
    """Un ENABLED sin keywordId vuelve la pagina ambigua: la intencion sigue abierta.

    Fija la razon por la que `_readback` no necesita guarda propia de keywordId.
    """
    _parche(monkeypatch)
    with _db() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, _store = _transporte(grupo_ext, termino, readback="sin_id")
        resultado = efecto.aprobar(conn, senal, transport=transport)
        assert (resultado.status, resultado.paso, resultado.motivo) == (
            200,
            "intencion",
            "por_confirmar",
        )
        assert _pasos(conn, resultado.veredicto_id) == [("intencion", None, None)]
        assert len(_posts(vistos)) == 1


class _MemRes:
    def __init__(self, filas):
        self._filas = list(filas)

    def __iter__(self):
        return iter(self._filas)

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return list(self._filas)


class _MemBloqueo:
    """Conexion en memoria con los mismos invariantes de 0074 para correr sin PG local."""

    autocommit = False

    def __init__(self):
        self.seq = 0
        self.ahora = datetime(2026, 11, 12, 12, tzinfo=UTC)
        self.candados: set[str] = set()
        self.configs: list[dict] = []
        self.entidades: dict[int, dict] = {}
        self.fichas: dict[str, tuple] = {}
        self.rosters: dict[str, dict] = {}
        self.senales: list[dict] = []
        self.veredictos: list[dict] = []
        self.efectos: list[dict] = []

    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def transaction(self):
        from contextlib import nullcontext

        return nullcontext()

    def commit(self):
        pass

    def rollback(self):
        pass

    def _ultimo_efecto(self, vid):
        propios = [e for e in self.efectos if e["veredicto_id"] == vid]
        return propios[-1] if propios else None

    def execute(self, sql: str, params=()):
        q = " ".join(str(sql).split())
        if q == "SELECT now()":
            return _MemRes([(self.ahora,)])
        if q.startswith("SELECT pg_try_advisory_lock"):
            llave = str(params[0])
            if llave in self.candados:
                return _MemRes([(False,)])
            self.candados.add(llave)
            return _MemRes([(True,)])
        if q.startswith("SELECT pg_advisory_unlock"):
            self.candados.discard(str(params[0]))
            return _MemRes([(True,)])
        if q.startswith("INSERT INTO jev_revision"):
            return _MemRes([])
        if q.startswith(("INSERT INTO product ", "INSERT INTO listing ")):
            return _MemRes([(self._next(),)])
        if q.startswith("INSERT INTO jev_ficha_version"):
            fid, _prod, _plat, _l, hechos, desc, *_ = params
            datos = json.loads(hechos) if isinstance(hechos, str) else hechos
            self.fichas[str(fid)] = (str(fid), datos, desc)
            return _MemRes([])
        if "FROM jev_ficha_version WHERE id = ANY" in q:
            return _MemRes([self.fichas[str(i)] for i in params[0] if str(i) in self.fichas])
        if q.startswith("INSERT INTO ad_entity") and "'campaign'" in q:
            nid = self._next()
            self.entidades[nid] = {
                "platform": params[0],
                "kind": "campaign",
                "external_id": params[1],
                "parent_id": None,
            }
            return _MemRes([(nid,)])
        if q.startswith("INSERT INTO ad_entity") and "'ad_group'" in q:
            nid = self._next()
            self.entidades[nid] = {
                "platform": params[0],
                "kind": "ad_group",
                "external_id": params[1],
                "parent_id": params[2],
            }
            return _MemRes([(nid,)])
        if "FROM ad_entity grp JOIN ad_entity cam" in q:
            grp = self.entidades.get(params[0])
            cam = self.entidades.get(grp["parent_id"]) if grp else None
            if grp and cam and cam["kind"] == "campaign":
                return _MemRes([(grp["external_id"], cam["external_id"])])
            return _MemRes([])
        if q.startswith("INSERT INTO jev_roster"):
            sha, _plat, _grp, miembros, _fids = params
            self.rosters[str(sha)] = json.loads(miembros) if isinstance(miembros, str) else miembros
            return _MemRes([])
        if q.startswith("SELECT miembros FROM jev_roster"):
            sha = str(params[0])
            return _MemRes([(self.rosters[sha],)] if sha in self.rosters else [])
        if q.startswith("INSERT INTO jev_senal"):
            self.senales.append(
                {
                    "id": params[0],
                    "plataforma": params[2],
                    "ad_group_id": params[3],
                    "termino": params[4],
                    "termino_sha256": params[5],
                    "roster_sha256": params[7],
                    "lectura": params[14],
                    "valida_hasta": params[15],
                    "seq": self._next(),
                }
            )
            return _MemRes([])
        if "FROM jev_senal WHERE id = %s" in q:
            sid = params[0]
            for s in self.senales:
                if s["id"] == sid:
                    return _MemRes(
                        [
                            (
                                s["id"],
                                s["plataforma"],
                                s["ad_group_id"],
                                s["termino"],
                                s["termino_sha256"],
                                s["roster_sha256"],
                            )
                        ]
                    )
            return _MemRes([])
        if "FROM jev_senal_vigente v JOIN jev_senal s" in q:
            grp, sha = params
            cand = [
                s for s in self.senales if s["ad_group_id"] == grp and s["termino_sha256"] == sha
            ]
            if not cand:
                return _MemRes([])
            ult = max(cand, key=lambda s: s["seq"])
            return _MemRes([(ult["id"], ult["valida_hasta"] > self.ahora, ult["lectura"])])
        if q.startswith("INSERT INTO config_version"):
            raw = params[0] if params else "{}"
            self.configs.append(json.loads(raw) if isinstance(raw, str) else dict(raw))
            return _MemRes([(len(self.configs),)])
        if q.startswith("SELECT settings FROM config_version"):
            return _MemRes([(self.configs[-1],)] if self.configs else [])
        if "FROM apply_queue q" in q:
            return _MemRes([])
        if q.startswith("INSERT INTO bloqueo_veredicto"):
            if len(params) == 2:
                vid, plat = params
                fila = {
                    "id": vid,
                    "plataforma": plat,
                    "ad_group_id": None,
                    "termino": None,
                    "termino_sha256": None,
                    "senal_id": None,
                    "huella_catalogo": None,
                    "origen_id": None,
                    "tipo": "reanudacion",
                    "created_at": self.ahora + timedelta(microseconds=self._next()),
                }
            else:
                vid, plat, grp, term, sha, sid, huella, orig, tipo = params
                assert tipo in ("aprobado", "rechazado", "quitado", "reanudacion")
                if tipo == "quitado":
                    assert orig is not None
                    assert any(v["id"] == orig and v["tipo"] == "aprobado" for v in self.veredictos)
                elif tipo in ("aprobado", "rechazado"):
                    assert grp is not None and term is not None
                    assert sid is not None and huella is not None
                fila = {
                    "id": vid,
                    "plataforma": plat,
                    "ad_group_id": grp,
                    "termino": term,
                    "termino_sha256": sha,
                    "senal_id": sid,
                    "huella_catalogo": huella,
                    "origen_id": orig,
                    "tipo": tipo,
                    "created_at": self.ahora + timedelta(microseconds=self._next()),
                }
            self.veredictos.append(fila)
            return _MemRes([])
        if q.startswith("INSERT INTO bloqueo_efecto"):
            eid, vid, paso, kid, status, resp, motivo = params
            assert paso in (
                "intencion",
                "aplicado",
                "fallido",
                "anulado",
                "reversa_intencion",
                "revertido",
                "reversa_fallida",
            )
            assert any(v["id"] == vid and v["tipo"] == "aprobado" for v in self.veredictos)
            if paso in ("aplicado", "reversa_intencion", "revertido"):
                assert kid is not None
            if paso in ("fallido", "anulado", "reversa_fallida"):
                assert motivo is not None
            self.efectos.append(
                {
                    "id": eid,
                    "veredicto_id": vid,
                    "paso": paso,
                    "negative_keyword_id": kid,
                    "http_status": status,
                    "respuesta": resp,
                    "motivo": motivo,
                    "seq": self._next(),
                }
            )
            return _MemRes([])
        if "FROM bloqueo_veredicto v WHERE NOT" in q:
            malos = {e["veredicto_id"] for e in self.efectos if e["paso"] in ("anulado", "fallido")}
            filas = [
                (v["id"], v["plataforma"], v["tipo"], v["created_at"], v["origen_id"])
                for v in self.veredictos
                if not (v["tipo"] == "aprobado" and v["id"] in malos)
            ]
            return _MemRes(filas)
        if "FROM bloqueo_veredicto WHERE tipo = 'rechazado' AND senal_id = %s" in q:
            filas = [
                (v["id"],)
                for v in self.veredictos
                if v["tipo"] == "rechazado" and v["senal_id"] == params[0]
            ]
            return _MemRes(filas)
        if "FROM bloqueo_veredicto WHERE id = %s AND tipo = 'aprobado'" in q:
            for v in self.veredictos:
                if v["id"] == params[0] and v["tipo"] == "aprobado":
                    return _MemRes(
                        [
                            (
                                v["id"],
                                v["plataforma"],
                                v["ad_group_id"],
                                v["termino"],
                                v["termino_sha256"],
                                v["huella_catalogo"],
                                v["senal_id"],
                            )
                        ]
                    )
            return _MemRes([])
        if "SELECT 1 FROM bloqueo_veredicto v JOIN LATERAL" in q:
            plat, grp, sha, vivos = params
            for v in self.veredictos:
                if (
                    v["tipo"] == "aprobado"
                    and v["plataforma"] == plat
                    and v["ad_group_id"] == grp
                    and v["termino_sha256"] == sha
                ):
                    ult = self._ultimo_efecto(v["id"])
                    if ult and ult["paso"] in vivos:
                        return _MemRes([(1,)])
            return _MemRes([])
        if "SELECT v.id, v.senal_id, ult.paso, ult.negative_keyword_id" in q:
            plat, grp, sha, origen_id, pasos = params
            filas = []
            for v in self.veredictos:
                if (
                    v["tipo"] == "aprobado"
                    and v["plataforma"] == plat
                    and v["ad_group_id"] == grp
                    and v["termino_sha256"] == sha
                    and v["id"] != origen_id
                ):
                    ult = self._ultimo_efecto(v["id"])
                    if ult and ult["paso"] in pasos:
                        filas.append(
                            (v["id"], v["senal_id"], ult["paso"], ult["negative_keyword_id"])
                        )
            return _MemRes(filas)
        if "SELECT v.id, ult.paso, ult.negative_keyword_id, v.plataforma::text, v.termino" in q:
            grp, sha, pasos = params
            filas = []
            for v in self.veredictos:
                if (
                    v["tipo"] == "aprobado"
                    and v["ad_group_id"] == grp
                    and v["termino_sha256"] == sha
                ):
                    ult = self._ultimo_efecto(v["id"])
                    if ult and ult["paso"] in pasos:
                        filas.append(
                            (
                                v["id"],
                                ult["paso"],
                                ult["negative_keyword_id"],
                                v["plataforma"],
                                v["termino"],
                            )
                        )
            return _MemRes(filas)
        if "SELECT DISTINCT v.ad_group_id, v.termino_sha256 FROM bloqueo_veredicto v" in q:
            pasos = params[0]
            vistos: list[tuple[int, str]] = []
            for v in self.veredictos:
                if (
                    v["tipo"] == "aprobado"
                    and v["ad_group_id"] is not None
                    and v["termino_sha256"] is not None
                ):
                    ult = self._ultimo_efecto(v["id"])
                    par = (v["ad_group_id"], v["termino_sha256"])
                    if ult and ult["paso"] in pasos and par not in vistos:
                        vistos.append(par)
            return _MemRes(vistos)
        if q.startswith("SELECT paso, negative_keyword_id FROM bloqueo_efecto"):
            ult = self._ultimo_efecto(params[0])
            return _MemRes([(ult["paso"], ult["negative_keyword_id"])] if ult else [])
        if q.startswith("SELECT negative_keyword_id FROM bloqueo_efecto"):
            con_id = [
                e
                for e in self.efectos
                if e["veredicto_id"] == params[0] and e["negative_keyword_id"] is not None
            ]
            return _MemRes([(con_id[-1]["negative_keyword_id"],)] if con_id else [])
        if q.startswith("SELECT paso, motivo, negative_keyword_id FROM bloqueo_efecto"):
            filas = [
                (e["paso"], e["motivo"], e["negative_keyword_id"])
                for e in self.efectos
                if e["veredicto_id"] == params[0]
            ]
            return _MemRes(filas)
        raise AssertionError(f"SQL no soportado en _MemBloqueo: {q}")


def _db_o_mem():
    import os
    from contextlib import contextmanager

    from test_schema import _hay_postgres_local

    @contextmanager
    def _ctx():
        if "CI" in os.environ or os.environ.get("ORBIT_TEST_DSN") or _hay_postgres_local():
            with _db() as conn:
                yield conn
        else:
            yield _MemBloqueo()

    return _ctx()


def test_arreglos04_bb_negative_pausado_no_cuenta_como_bloqueo(monkeypatch):
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal, _grupo_id, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, vistos, store = _transporte(grupo_ext, termino)
        store.append(
            {
                "keywordId": "kid-pausado",
                "adGroupId": grupo_ext,
                "keywordText": termino,
                "matchType": "NEGATIVE_EXACT",
                "state": "PAUSED",
            }
        )
        resultado = efecto.aprobar(conn, senal, transport=transport)
        posts = _posts(vistos)
        pasos = _pasos(conn, resultado.veredicto_id)
        assert not (resultado.paso == "aplicado" and posts == []), (
            f"sello {pasos} sin POST con el negative en PAUSED: el termino sigue gastando"
        )
        assert len(posts) == 1
        assert resultado.paso == "aplicado"
        assert pasos[-1] == ("aplicado", None, "kid-1")


def test_arreglos04_bb_rechazo_por_item_del_207_sella_fallido(monkeypatch):
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal, _grupo_id, grupo_ext, _campana, termino, _roster = _prepara(conn)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=_perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                return httpx.Response(200, json={"negativeKeywords": []})
            if request.url.path == "/sp/negativeKeywords":
                return httpx.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "success": [],
                            "error": [
                                {
                                    "index": 0,
                                    "errors": [{"errorType": "invalidArgument", "errorValue": {}}],
                                }
                            ],
                        }
                    },
                )
            return httpx.Response(400, json={"message": request.url.path})

        resultado = efecto.aprobar(conn, senal, transport=httpx.MockTransport(handler))
        pasos = _pasos(conn, resultado.veredicto_id)
        assert resultado.paso == "fallido", (
            f"207 con error por item quedo {resultado.paso}/{resultado.motivo}; pasos={pasos}"
        )
        assert resultado.motivo == "rechazo_amazon"
        assert pasos == [("intencion", None, None), ("fallido", "rechazo_amazon", None)]


def test_arreglos04_bb_quitar_con_5xx_relee_y_cierra(monkeypatch):
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal, grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, _vistos, store = _transporte(grupo_ext, termino)
        hecho = efecto.aprobar(conn, senal, transport=transport)
        assert hecho.paso == "aplicado"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=_perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                return httpx.Response(200, json={"negativeKeywords": list(store)})
            if request.url.path == "/sp/negativeKeywords/delete":
                kid = json.loads(request.content)["negativeKeywordIdFilter"]["include"][0]
                for item in store:
                    if item["keywordId"] == str(kid):
                        item["state"] = "ARCHIVED"
                return httpx.Response(503, json={"message": "gateway"})
            return httpx.Response(400, json={"message": request.url.path})

        quitado = efecto.quitar(conn, hecho.veredicto_id, transport=httpx.MockTransport(handler))
        efecto.reconcilia_todas(conn, transport=httpx.MockTransport(handler))
        ultimo = _pasos(conn, hecho.veredicto_id)[-1]
        assert [i["state"] for i in store] == ["ARCHIVED"]
        assert ultimo[0] == "revertido", (
            f"quitar={quitado.paso}/{quitado.motivo}; ultimo paso {ultimo} con el negative"
            " ya ARCHIVED en Amazon; la reconciliacion no lo revisa"
        )
        assert grupo > 0


def test_arreglos04_bb_reconciliacion_cierra_un_quitar_dudoso(monkeypatch):
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal, _grupo, grupo_ext, _campana, termino, _roster = _prepara(conn)
        transport, _vistos, store = _transporte(grupo_ext, termino)
        hecho = efecto.aprobar(conn, senal, transport=transport)
        assert hecho.paso == "aplicado"

        def handler_caido(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=_perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                return httpx.Response(503, json={"message": "list down"})
            if request.url.path == "/sp/negativeKeywords/delete":
                kid = json.loads(request.content)["negativeKeywordIdFilter"]["include"][0]
                for item in store:
                    if item["keywordId"] == str(kid):
                        item["state"] = "ARCHIVED"
                return httpx.Response(503, json={"message": "delete 503"})
            return httpx.Response(400, json={"message": request.url.path})

        quitado = efecto.quitar(
            conn, hecho.veredicto_id, transport=httpx.MockTransport(handler_caido)
        )
        assert (quitado.paso, quitado.motivo) == ("reversa_intencion", "por_confirmar")
        assert _pasos(conn, hecho.veredicto_id)[-1][0] == "reversa_intencion"

        efecto.reconcilia_todas(conn, transport=transport)
        assert _pasos(conn, hecho.veredicto_id)[-1] == ("revertido", None, "kid-1")


def test_arreglos04_bb_quitar_rechazo_207_relee_archivado_vivo_y_lista_caida(monkeypatch):
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal1, _g1, ge1, _c1, t1, _r1 = _prepara(conn, termino="uno", sha="1" * 64)
        tp1, _v1, st1 = _transporte(ge1, t1)
        h1 = efecto.aprobar(conn, senal1, transport=tp1)
        st1[0]["state"] = "ARCHIVED"

        def h_del_err(store, *, list_status=200):
            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.host == "api.amazon.com":
                    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
                if request.url.path == "/v2/profiles":
                    return httpx.Response(200, json=_perfiles())
                if request.url.path == "/sp/negativeKeywords/list":
                    if list_status != 200:
                        return httpx.Response(list_status, json={"message": "down"})
                    return httpx.Response(200, json={"negativeKeywords": list(store)})
                if request.url.path == "/sp/negativeKeywords/delete":
                    return httpx.Response(
                        207,
                        json={
                            "negativeKeywords": {
                                "success": [],
                                "error": [{"index": 0, "code": "NOT_FOUND"}],
                            }
                        },
                    )
                return httpx.Response(400, json={"message": request.url.path})

            return httpx.MockTransport(handler)

        q1 = efecto.quitar(conn, h1.veredicto_id, transport=h_del_err(st1))
        assert q1.paso == "revertido"
        assert _pasos(conn, h1.veredicto_id)[-1] == ("revertido", None, "kid-1")

        senal2, _g2, ge2, _c2, t2, _r2 = _prepara(conn, termino="dos", sha="2" * 64)
        tp2, _v2, st2 = _transporte(ge2, t2)
        h2 = efecto.aprobar(conn, senal2, transport=tp2)
        q2 = efecto.quitar(conn, h2.veredicto_id, transport=h_del_err(st2))
        assert (q2.paso, q2.motivo) == ("reversa_fallida", "rechazo_amazon")
        assert _pasos(conn, h2.veredicto_id)[-1] == ("reversa_fallida", "rechazo_amazon", None)

        senal3, _g3, ge3, _c3, t3, _r3 = _prepara(conn, termino="tres", sha="3" * 64)
        tp3, _v3, st3 = _transporte(ge3, t3)
        h3 = efecto.aprobar(conn, senal3, transport=tp3)
        q3 = efecto.quitar(conn, h3.veredicto_id, transport=h_del_err(st3, list_status=503))
        assert (q3.paso, q3.motivo) == ("reversa_intencion", "por_confirmar")
        assert _pasos(conn, h3.veredicto_id)[-1][0] == "reversa_intencion"


def test_arreglos04_bb_reparacion_precondiciones_quitar_y_aprobar(monkeypatch):
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal_vieja, grupo, grupo_ext, _campana, termino, roster = _prepara(conn)
        tp, vistos, store = _transporte(grupo_ext, termino)
        origen = efecto.aprobar(conn, senal_vieja, transport=tp)
        assert origen.paso == "aplicado"
        kid_origen = _pasos(conn, origen.veredicto_id)[-1][2]

        senal_vigente = _senal_ajena(
            conn,
            grupo,
            termino,
            SHA,
            roster,
            plataforma="amazon_mx",
            gasto=Decimal("9.5000"),
            lote=_lote(conn),
            miembros=1,
        )

        store[0]["state"] = "ENABLED"
        vistos.clear()
        pre_paused = efecto.PrecondicionQuitar(
            plataforma="amazon_mx",
            ad_group_id=grupo,
            termino=termino,
            negative_keyword_id=kid_origen,
            estado_observado="PAUSED",
            senal_id=senal_vigente,
        )
        cambio = efecto.quitar(conn, origen.veredicto_id, transport=tp, precondicion=pre_paused)
        assert cambio == efecto.Efecto(409, motivo="estado_cambio"), "ENABLED actual frena quitar"
        assert [r for r in vistos if r.url.path == "/sp/negativeKeywords/delete"] == []
        assert _pasos(conn, origen.veredicto_id)[-1] == ("aplicado", None, kid_origen)

        store[0]["state"] = "PAUSED"
        store.append(
            {
                "keywordId": "kid-ajeno",
                "adGroupId": grupo_ext,
                "keywordText": termino,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
        )
        vistos.clear()
        con_otro = efecto.quitar(conn, origen.veredicto_id, transport=tp, precondicion=pre_paused)
        assert con_otro == efecto.Efecto(409, motivo="otro_enabled"), "otro ENABLED frena quitar"
        assert [r for r in vistos if r.url.path == "/sp/negativeKeywords/delete"] == []
        store.pop()

        pre_ap = efecto.PrecondicionAprobar(
            origen_id=origen.veredicto_id,
            plataforma="amazon_mx",
            ad_group_id=grupo,
            termino=termino,
        )
        vistos.clear()
        antes = efecto.aprobar(conn, senal_vigente, transport=tp, precondicion=pre_ap)
        assert antes == efecto.Efecto(409, motivo="origen_no_revertido")
        assert _posts(vistos) == []

        vistos.clear()
        quitado = efecto.quitar(conn, origen.veredicto_id, transport=tp, precondicion=pre_paused)
        assert quitado == efecto.Efecto(200, veredicto_id=origen.veredicto_id, paso="revertido")
        assert len([r for r in vistos if r.url.path == "/sp/negativeKeywords/delete"]) == 1
        assert efecto.lee_pausas(conn)["amazon_mx"].aprobados == 1, (
            "reparar no cuenta como rechazo del dueno en pausa_de"
        )
        vistos.clear()
        repetido = efecto.quitar(conn, origen.veredicto_id, transport=tp, precondicion=pre_paused)
        assert repetido == efecto.Efecto(
            200, veredicto_id=origen.veredicto_id, paso="revertido", motivo="ya_revertido"
        )
        assert [r for r in vistos if r.url.path == "/sp/negativeKeywords/delete"] == []

        store.append(
            {
                "keywordId": "kid-ajeno",
                "adGroupId": grupo_ext,
                "keywordText": termino,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
        )
        vistos.clear()
        no_adopta = efecto.aprobar(conn, senal_vigente, transport=tp, precondicion=pre_ap)
        assert no_adopta == efecto.Efecto(409, motivo="otro_enabled")
        assert _posts(vistos) == []
        store.pop()

        fase = {"list_post": "503"}

        def h_reemplazo(request: httpx.Request) -> httpx.Response:
            vistos.append(request)
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=_perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                if fase["list_post"] == "caido":
                    return httpx.Response(503, json={"message": "list down"})
                return httpx.Response(200, json={"negativeKeywords": list(store)})
            if request.url.path == "/sp/negativeKeywords" and request.method == "POST":
                store.append(
                    {
                        "keywordId": "kid-reemplazo",
                        "adGroupId": grupo_ext,
                        "keywordText": termino,
                        "matchType": "NEGATIVE_EXACT",
                        "state": "ENABLED",
                    }
                )
                fase["list_post"] = "caido"
                return httpx.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "success": [{"negativeKeywordId": "kid-reemplazo"}],
                            "error": [],
                        }
                    },
                )
            return httpx.Response(400, json={"message": request.url.path})

        tp_incierto = httpx.MockTransport(h_reemplazo)
        vistos.clear()
        dudoso = efecto.aprobar(conn, senal_vigente, transport=tp_incierto, precondicion=pre_ap)
        assert (dudoso.status, dudoso.paso, dudoso.motivo) == (200, "intencion", "por_confirmar")
        assert len(_posts(vistos)) == 1

        vistos.clear()
        pendiente = efecto.aprobar(conn, senal_vigente, transport=tp_incierto, precondicion=pre_ap)
        assert pendiente == efecto.Efecto(409, motivo="reconciliacion_pendiente")
        assert _posts(vistos) == []

        fase["list_post"] = "ok"
        vistos.clear()
        cerrado = efecto.aprobar(conn, senal_vigente, transport=tp_incierto, precondicion=pre_ap)
        assert cerrado == efecto.Efecto(
            200, veredicto_id=dudoso.veredicto_id, paso="aplicado", motivo="ya_aplicado"
        )
        assert _posts(vistos) == []
        assert _pasos(conn, dudoso.veredicto_id)[-1] == ("aplicado", None, "kid-reemplazo")

        s_aus, g_aus, ge_aus, _c_aus, t_aus, _r_aus = _prepara(
            conn, termino="ausente", sha="4" * 64
        )
        tp_aus, vistos_aus, st_aus = _transporte(ge_aus, t_aus)
        orig_aus = efecto.aprobar(conn, s_aus, transport=tp_aus)
        kid_aus = _pasos(conn, orig_aus.veredicto_id)[-1][2]
        st_aus.clear()
        vistos_aus.clear()
        q_aus = efecto.quitar(
            conn,
            orig_aus.veredicto_id,
            transport=tp_aus,
            precondicion=efecto.PrecondicionQuitar(
                plataforma="amazon_mx",
                ad_group_id=g_aus,
                termino=t_aus,
                negative_keyword_id=kid_aus,
                estado_observado="AUSENTE",
                senal_id=s_aus,
            ),
        )
        assert q_aus == efecto.Efecto(200, veredicto_id=orig_aus.veredicto_id, paso="revertido")
        assert [r for r in vistos_aus if r.url.path == "/sp/negativeKeywords/delete"] == []


def test_arreglos04_bb_reconciliacion_no_adopta_un_enabled_ajeno(monkeypatch):
    """El ack confirma A y la lista solo trae B: la conciliacion no adopta B."""
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal_vieja, grupo, grupo_ext, _campana, termino, roster = _prepara(conn)
        tp, _vistos, _store = _transporte(grupo_ext, termino)
        origen = efecto.aprobar(conn, senal_vieja, transport=tp)
        assert origen.paso == "aplicado"
        assert efecto.quitar(conn, origen.veredicto_id, transport=tp).paso == "revertido"

        senal_vigente = _senal_ajena(
            conn,
            grupo,
            termino,
            SHA,
            roster,
            plataforma="amazon_mx",
            gasto=Decimal("9.5000"),
            lote=_lote(conn),
            miembros=1,
        )
        pre_ap = efecto.PrecondicionAprobar(
            origen_id=origen.veredicto_id,
            plataforma="amazon_mx",
            ad_group_id=grupo,
            termino=termino,
        )
        fase = {"listas": 0, "listar_a": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=_perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                fase["listas"] += 1
                if fase["listas"] == 1:
                    return httpx.Response(200, json={"negativeKeywords": []})
                items = [
                    {
                        "keywordId": "kid-ajeno",
                        "adGroupId": grupo_ext,
                        "keywordText": termino,
                        "matchType": "NEGATIVE_EXACT",
                        "state": "ENABLED",
                    }
                ]
                if fase["listar_a"]:
                    items.insert(
                        0,
                        {
                            "keywordId": "kid-reemplazo",
                            "adGroupId": grupo_ext,
                            "keywordText": termino,
                            "matchType": "NEGATIVE_EXACT",
                            "state": "ENABLED",
                        },
                    )
                return httpx.Response(200, json={"negativeKeywords": items})
            if request.url.path == "/sp/negativeKeywords" and request.method == "POST":
                return httpx.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "success": [{"negativeKeywordId": "kid-reemplazo"}],
                            "error": [],
                        }
                    },
                )
            return httpx.Response(400, json={"message": request.url.path})

        tp_ab = httpx.MockTransport(handler)
        dudoso = efecto.aprobar(conn, senal_vigente, transport=tp_ab, precondicion=pre_ap)
        assert (dudoso.status, dudoso.paso, dudoso.motivo) == (200, "intencion", "por_confirmar")
        assert _pasos(conn, dudoso.veredicto_id)[-1] == (
            "intencion",
            "reparacion",
            "kid-reemplazo",
        )

        efecto.reconcilia_todas(conn, transport=tp_ab)
        pasos = _pasos(conn, dudoso.veredicto_id)
        assert pasos[-1][0] == "intencion", (
            f"la conciliacion adopto el ENABLED ajeno kid-ajeno: {pasos}"
        )
        assert not any(p[2] == "kid-ajeno" for p in pasos)

        fase["listar_a"] = True
        efecto.reconcilia_todas(conn, transport=tp_ab)
        assert _pasos(conn, dudoso.veredicto_id)[-1] == (
            "aplicado",
            None,
            "kid-reemplazo",
        )


def test_arreglos04_bb_reconciliacion_no_atribuye_sin_ack(monkeypatch):
    """Sin ID en el acuse, un ENABLED posterior no se adopta como reemplazo."""
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal_vieja, grupo, grupo_ext, _campana, termino, roster = _prepara(
            conn, termino="sin-ack", sha="5" * 64
        )
        tp, _vistos, _store = _transporte(grupo_ext, termino)
        origen = efecto.aprobar(conn, senal_vieja, transport=tp)
        assert origen.paso == "aplicado"
        assert efecto.quitar(conn, origen.veredicto_id, transport=tp).paso == "revertido"

        senal_vigente = _senal_ajena(
            conn,
            grupo,
            termino,
            "5" * 64,
            roster,
            plataforma="amazon_mx",
            gasto=Decimal("9.5000"),
            lote=_lote(conn),
            miembros=1,
        )
        pre_ap = efecto.PrecondicionAprobar(
            origen_id=origen.veredicto_id,
            plataforma="amazon_mx",
            ad_group_id=grupo,
            termino=termino,
        )
        fase = {"listas": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=_perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                fase["listas"] += 1
                if fase["listas"] == 1:
                    return httpx.Response(200, json={"negativeKeywords": []})
                return httpx.Response(
                    200,
                    json={
                        "negativeKeywords": [
                            {
                                "keywordId": "kid-ajeno",
                                "adGroupId": grupo_ext,
                                "keywordText": termino,
                                "matchType": "NEGATIVE_EXACT",
                                "state": "ENABLED",
                            }
                        ]
                    },
                )
            if request.url.path == "/sp/negativeKeywords" and request.method == "POST":
                return httpx.Response(503, json={"message": "post 503"})
            return httpx.Response(400, json={"message": request.url.path})

        tp_sin = httpx.MockTransport(handler)
        dudoso = efecto.aprobar(conn, senal_vigente, transport=tp_sin, precondicion=pre_ap)
        assert (dudoso.status, dudoso.paso, dudoso.motivo) == (200, "intencion", "por_confirmar")
        assert _pasos(conn, dudoso.veredicto_id)[-1] == ("intencion", "reparacion", None)

        efecto.reconcilia_todas(conn, transport=tp_sin)
        pasos = _pasos(conn, dudoso.veredicto_id)
        assert pasos[-1][0] == "intencion", (
            f"sin ID de acuse la conciliacion adopto kid-ajeno: {pasos}"
        )
        assert not any(p[0] == "aplicado" for p in pasos)


def test_arreglos04_bb_reparacion_nace_marcada_antes_del_acuse(monkeypatch):
    """Si el proceso muere antes del acuse, la primera intencion ya es de reparacion."""
    _parche(monkeypatch)
    with _db_o_mem() as conn:
        senal_vieja, grupo, grupo_ext, _campana, termino, roster = _prepara(
            conn, termino="nace-marcada", sha="6" * 64
        )
        tp, _vistos, _store = _transporte(grupo_ext, termino)
        origen = efecto.aprobar(conn, senal_vieja, transport=tp)
        assert origen.paso == "aplicado"
        assert efecto.quitar(conn, origen.veredicto_id, transport=tp).paso == "revertido"

        senal_vigente = _senal_ajena(
            conn,
            grupo,
            termino,
            "6" * 64,
            roster,
            plataforma="amazon_mx",
            gasto=Decimal("9.5000"),
            lote=_lote(conn),
            miembros=1,
        )
        pre_ap = efecto.PrecondicionAprobar(
            origen_id=origen.veredicto_id,
            plataforma="amazon_mx",
            ad_group_id=grupo,
            termino=termino,
        )
        fase = {"listas": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=_perfiles())
            if request.url.path == "/sp/negativeKeywords/list":
                fase["listas"] += 1
                if fase["listas"] == 1:
                    return httpx.Response(200, json={"negativeKeywords": []})
                return httpx.Response(
                    200,
                    json={
                        "negativeKeywords": [
                            {
                                "keywordId": "kid-ajeno",
                                "adGroupId": grupo_ext,
                                "keywordText": termino,
                                "matchType": "NEGATIVE_EXACT",
                                "state": "ENABLED",
                            }
                        ]
                    },
                )
            return httpx.Response(400, json={"message": request.url.path})

        tp_caida = httpx.MockTransport(handler)

        def muere(*_args, **_kwargs):
            raise KeyboardInterrupt

        original = efecto._postea_y_relee
        monkeypatch.setattr(efecto, "_postea_y_relee", muere)
        try:
            efecto.aprobar(conn, senal_vigente, transport=tp_caida, precondicion=pre_ap)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("la caida simulada no interrumpio la reparacion")
        monkeypatch.setattr(efecto, "_postea_y_relee", original)

        vid = conn.execute(
            "SELECT id FROM bloqueo_veredicto WHERE tipo = 'aprobado' AND id <> %s",
            (origen.veredicto_id,),
        ).fetchone()[0]
        assert _pasos(conn, vid) == [("intencion", "reparacion", None)], (
            "la intencion debe nacer marcada como reparacion"
        )

        efecto.reconcilia_todas(conn, transport=tp_caida)
        pasos = _pasos(conn, vid)
        assert pasos[-1][0] == "intencion", (
            f"la recuperacion adopto el ENABLED ajeno kid-ajeno: {pasos}"
        )
        assert not any(p[0] == "aplicado" for p in pasos)
