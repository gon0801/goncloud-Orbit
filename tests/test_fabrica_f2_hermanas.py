"""Fase `hermanas_negadas` (FABRICA 02, A.3): comportamiento del job de grupo.

Reutiliza `db_f2`, `_semilla_grupo` y `_handler_harvest` (A.0; no duplicados)
mas los helpers de siembra de `test_apply_harvest` (`_semilla`,
`_termino_calificado`, `_estado`, `_encola_fila`, `_aplicador`).

Orden TDD del brief, bloque 1 (sello, roster y compatibilidad):
- sello durable visible desde segunda conexion antes del primer POST;
- `applied_count`/`confirmed_at` estables tras reintentos de higiene;
- tres hermanas por cada uno de los cuatro roles discovery de origen;
- excepcion/terna cierra `exact_created -> done` sin fase nueva.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_apply_harvest import (
    TERMINO,
    _aplicador,
    _decision_harvest,
    _encola_fila,
    _estado,
    _handler_harvest,
    _semilla,
    _termino_calificado,
)
from test_fabrica_f2 import _semilla_grupo, db_f2
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.apply_cola import libera_vencidos

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

TERMINO_F2 = "termino hermana f2"

ROLES_DISCOVERY = (
    "auto_discovery",
    "category_phrase",
    "category_broad",
    "product_targeting",
)


def _decision_harvest_grupo(
    conn,
    ciclo: int,
    config_id: int,
    ag_origen: int,
    *,
    grupo_id: int,
    exacta_camp_ext: str,
    exacta_ag_ext: str,
    term: str = TERMINO_F2,
    bid: str = "1.00",
) -> int:
    """Decision kind='harvest' con el destino de grupo congelado
    (`inputs.goal.harvest` con `resuelto_por = grupo`, como lo deja
    `_goal_json` en A.1). Espejo de `_decision_harvest` + congelado."""
    dec = dt.datetime.now(dt.UTC) - dt.timedelta(days=3)
    inputs = {
        "motor": "hygiene",
        "platform": "amazon_us",
        "modo": "live",
        "motivo": "harvest_umbral",
        "goal": {
            "scope": "campaign",
            "bid_floor": "0.10",
            "bid_ceiling": "2.50",
            "harvest": {
                "campaign_id": exacta_camp_ext,
                "ad_group_id": exacta_ag_ext,
                "default_bid": bid,
                "moneda": "USD",
                "resuelto_por": "grupo",
                "grupo_id": grupo_id,
                "motivo": None,
            },
        },
    }
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'harvest', %s, %s, %s - interval '1 day', %s - 60, %s - 30,"
        " %s, 1.00, 'USD', %s) RETURNING id",
        (ciclo, ag_origen, dec, config_id, dec, dec.date(), dec.date(), term, Json(inputs)),
    ).fetchone()[0]


def _grupo_listo(conn, *, origen_rol: str = "category_phrase", term: str = TERMINO_F2) -> dict:
    """`_semilla` (config/ciclos) + `_semilla_grupo` + states ENABLED en las
    10 entidades + observaciones que califican el termino en el origen."""
    ids = _semilla(conn)
    grupo = _semilla_grupo(conn)
    for par in grupo["roles"].values():
        _estado(conn, par["camp"])
        _estado(conn, par["ag"])
    origen = grupo["roles"][origen_rol]
    _termino_calificado(conn, origen["ag"], term=term)
    return {**ids, **grupo, "origen": origen, "origen_rol": origen_rol}


def _corre_harvest_grupo(conn, setup: dict, *, term: str = TERMINO_F2, handler_kw=None):
    """Encola el harvest de grupo del setup y lo libera (el camino real:
    revalida -> aplica -> sello -> hermanas). Devuelve decision/cola/handler."""
    exacta = setup["roles"]["category_exact"]
    dec = _decision_harvest_grupo(
        conn,
        setup["ciclo_dec"],
        setup["config"],
        setup["origen"]["ag"],
        grupo_id=setup["grupo_id"],
        exacta_camp_ext=exacta["camp_ext"],
        exacta_ag_ext=exacta["ag_ext"],
        term=term,
    )
    qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=term)
    handler, vistos = _handler_harvest(**(handler_kw or {}))
    res = libera_vencidos(
        conn,
        "amazon_us",
        ahora=dt.datetime.now(dt.UTC),
        aplicador=_aplicador(conn, handler, setup["ciclo_ejec"]),
    )
    return {"dec": dec, "qid": qid, "handler": handler, "vistos": vistos, "res": res}


def _job_de(conn, dec: int) -> dict:
    fila = conn.execute(
        "SELECT fase, external_ids FROM harvest_job WHERE decision_id = %s", (dec,)
    ).fetchone()
    return {"fase": fila[0], "ext": dict(fila[1] or {})}


# ---------------------------------------------------------------------------
# Bloque 1: sello durable antes del primer POST de hermana
# ---------------------------------------------------------------------------


@_skip_db
def test_sello_durable_visible_desde_segunda_conexion_antes_del_primer_post(monkeypatch):
    """En el primer POST de hermana, otra conexion YA ve: keyword confirmada
    (`verify_ok`), cola `applied`, fase `hermanas_negadas` y roster
    congelado. Regla 9: sin el sello previo al POST, la segunda conexion
    veria el job todavia en `exact_created` y sin roster."""
    with db_f2("orbit_hna_sello") as conn:
        setup = _grupo_listo(conn)
        hermanas_ag = {
            setup["roles"][r]["ag_ext"] for r in ROLES_DISCOVERY if r != setup["origen_rol"]
        }
        visto: dict = {}

        from app.ads.write import AdsWriteClient

        original = AdsWriteClient.crear_negative_exacto

        def _espia(self, ad_group_id, campaign_id, keyword_text):
            if str(ad_group_id) in hermanas_ag and "sello" not in visto:
                otra = psycopg.connect(_test_dsn(), dbname=conn.info.dbname, autocommit=True)
                try:
                    ver = otra.execute(
                        "SELECT verify_ok FROM decision_application WHERE decision_id = %s",
                        (visto["dec"],),
                    ).fetchone()
                    cola = otra.execute(
                        "SELECT estado FROM apply_queue WHERE id = %s", (visto["qid"],)
                    ).fetchone()
                    job = otra.execute(
                        "SELECT fase, external_ids FROM harvest_job WHERE decision_id = %s",
                        (visto["dec"],),
                    ).fetchone()
                    visto["sello"] = {"verify": ver, "cola": cola, "job": job}
                finally:
                    otra.close()
            return original(self, ad_group_id, campaign_id, keyword_text)

        monkeypatch.setattr(AdsWriteClient, "crear_negative_exacto", _espia)
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        visto["dec"], visto["qid"] = dec, qid
        handler, _vistos = _handler_harvest()
        libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, setup["ciclo_ejec"]),
        )

        sello = visto.get("sello")
        assert sello is not None, "el flujo de grupo debe POSTear a hermanas (hay roster)"
        assert sello["verify"] == (True,), "decision confirmada antes del primer POST"
        assert sello["cola"] == ("applied",), "cola applied antes del primer POST"
        assert sello["job"][0] == "hermanas_negadas", "fase avanzada antes del primer POST"
        ext = dict(sello["job"][1] or {})
        esperados = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        assert sorted(ext.get("hermanas_objetivo", {})) == sorted(esperados)
        for rol in esperados:
            objetivo = ext["hermanas_objetivo"][rol]
            assert objetivo == {
                "campaign_id": setup["roles"][rol]["camp_ext"],
                "ad_group_id": setup["roles"][rol]["ag_ext"],
            }, rol


@_skip_db
def test_sello_no_se_repite_en_reintentos_de_higiene():
    """Tras el flujo completo (una hermana falla, se reintenta y cierra),
    `applied_count` es 1 y `confirmed_at` no se movio: la higiene posterior
    jamas re-sella el evento de valor. Regla 9: sin el sello unico, el
    reintento sumaria otro applied."""
    with db_f2("orbit_hna_sello1") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}}
        )
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "hermanas_negadas", job
        antes = conn.execute(
            "SELECT applied_cycle_id, confirmed_at FROM decision_application"
            " WHERE decision_id = %s",
            (corrido["dec"],),
        ).fetchone()
        cuenta_antes = conn.execute(
            "SELECT applied_count FROM optimizer_cycle WHERE id = %s", (setup["ciclo_ejec"],)
        ).fetchone()[0]

        from app.apply_harvest import reconcilia_harvest

        for _ in range(2):
            handler2, _vistos2 = _handler_harvest()
            reconcilia_harvest(conn, _aplicador(conn, handler2, setup["ciclo_ejec"]), "amazon_us")

        despues = conn.execute(
            "SELECT applied_cycle_id, confirmed_at FROM decision_application"
            " WHERE decision_id = %s",
            (corrido["dec"],),
        ).fetchone()
        cuenta_despues = conn.execute(
            "SELECT applied_count FROM optimizer_cycle WHERE id = %s", (setup["ciclo_ejec"],)
        ).fetchone()[0]
        assert despues == antes, "el resumen no se toca en la higiene"
        assert cuenta_despues == cuenta_antes == 1, "applied_count aumenta exactamente una vez"


@_skip_db
@pytest.mark.parametrize("origen_rol", list(ROLES_DISCOVERY))
def test_roster_tres_hermanas_por_cada_rol_origen(origen_rol):
    """Cada uno de los cuatro roles discovery como origen deja exactamente
    las otras tres identidades objetivo, en orden canonico, derivadas del
    grupo (jamas por nombre). Regla 9: un roster vivo o por nombre no
    coincidiria con los externos del grupo."""
    with db_f2(f"orbit_hna_ros_{origen_rol[:4]}") as conn:
        setup = _grupo_listo(conn, origen_rol=origen_rol)
        corrido = _corre_harvest_grupo(conn, setup)
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "done", job
        esperados = [r for r in ROLES_DISCOVERY if r != origen_rol]
        # jsonb NO preserva orden de claves (las reordena por longitud): el
        # roster guardado es un MAPA y el orden canonico lo impone el lector
        # via ROLES_DISCOVERY (la reversa lo usa). Aqui: contenido exacto.
        assert sorted(job["ext"].get("hermanas_objetivo", {})) == sorted(esperados), (
            "roster canonico menos exacta y menos origen"
        )
        for rol in esperados:
            assert job["ext"]["hermanas_objetivo"][rol] == {
                "campaign_id": setup["roles"][rol]["camp_ext"],
                "ad_group_id": setup["roles"][rol]["ag_ext"],
            }, rol
        assert sorted(job["ext"].get("hermanas", {})) == sorted(esperados)
        for rol in esperados:
            assert job["ext"]["hermanas"][rol]["creada"] is True
            assert job["ext"]["hermanas"][rol]["negative_id"]


@_skip_db
def test_terna_sin_grupo_cierra_como_hoy():
    """Harvest sin grupo (terna de plataforma) cierra `exact_created ->
    done` como hoy: sin consulta de grupo, sin ledger de hermana y sin
    alerta de hermanas. Regla 9: si la fase nueva atrapara este camino, el
    job quedaria en `hermanas_negadas` y habria POSTs de mas."""
    with db_f2("orbit_hna_terna") as conn:
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        qid = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _termino_calificado(conn, ids["ag"], term=TERMINO)
        handler, vistos = _handler_harvest()
        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )
        assert res.aplicadas == 1
        job = _job_de(conn, dec)
        assert job["fase"] == "done", job
        assert "hermanas_objetivo" not in job["ext"], "sin roster sin grupo"
        assert "hermanas" not in job["ext"], "sin hermanas sin grupo"
        tipos = conn.execute(
            "SELECT DISTINCT tipo FROM apply_attempt WHERE decision_id = %s", (dec,)
        ).fetchall()
        assert {t[0] for t in tipos} == {"normal"}, tipos
        muts = [r for r in vistos if r.method == "POST" and not r.url.path.endswith("/list")]
        assert [r.url.path for r in muts] == [
            "/sp/negativeKeywords",
            "/sp/keywords",
        ], "exactamente los 2 HTTPs historicos"
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert cola == "applied"


# ---------------------------------------------------------------------------
# Bloque 2: LIST filtrado, identidad y truncacion fail-closed
# ---------------------------------------------------------------------------


def _neg_hermana(ag_ext, camp_ext, kid, *, match="NEGATIVE_EXACT", state="ENABLED"):
    return {
        "adGroupId": ag_ext,
        "campaignId": camp_ext,
        "keywordId": kid,
        "keywordText": TERMINO_F2,
        "matchType": match,
        "state": state,
    }


@_skip_db
def test_previo_truncado_cero_post_y_pendientes():
    """LIST previo con `nextToken` vivo al tope -> fail-closed: cero POST de
    hermanas y pendientes `list_truncado`, el job sigue en la fase con
    ciclos nutrido. Regla 9: sin el corte, el flujo POSTearia a ciegas
    contra un LIST incompleto (un negativo existente mas alla del tope se
    duplicaria)."""
    with db_f2("orbit_hna_trunc") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        sembrados = []
        for i, rol in enumerate(hermanas):
            for j in range(7):
                sembrados.append(
                    _neg_hermana(
                        setup["roles"][rol]["ag_ext"],
                        setup["roles"][rol]["camp_ext"],
                        f"n-t-{i}-{j}",
                    )
                )
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"negatives": sembrados, "page_size": 1}
        )
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "hermanas_negadas", job
        muts = [
            r
            for r in corrido["vistos"]
            if r.method == "POST" and r.url.path == "/sp/negativeKeywords"
        ]
        hermanas_muts = [
            r
            for r in muts
            if json.loads(r.content)["negativeKeywords"][0]["adGroupId"]
            != setup["origen"]["ag_ext"]
        ]
        assert hermanas_muts == [], "truncado: cero POST de hermanas"
        for rol in hermanas:
            assert job["ext"]["hermanas"][rol] == {"motivo": "list_truncado"}, rol
        assert job["ext"]["hermanas_ciclos"] == 1, "el precheck truncado nutre ciclos"


@_skip_db
def test_list_fila_sin_keyword_id_cero_post_nunca_none():
    """Fila LIST con identidad (adGroup/texto/match/state) pero sin
    keywordId: unknown. Cero POST de hermanas y nunca persiste
    negative_id=None (la clave existiria y la hermana dejaria de ser
    pendiente). Regla 9: `_valida_pagina_list` solo pedia dict y
    `_paso_hermanas` adoptaba halladas[0].get('keywordId')."""
    with db_f2("orbit_hna_nokid") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        h1 = hermanas[0]
        incompleta = {
            "adGroupId": setup["roles"][h1]["ag_ext"],
            "campaignId": setup["roles"][h1]["camp_ext"],
            "keywordText": TERMINO_F2,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
        corrido = _corre_harvest_grupo(conn, setup, handler_kw={"negatives": [incompleta]})
        job = _job_de(conn, corrido["dec"])
        muts = [
            r
            for r in corrido["vistos"]
            if r.method == "POST" and r.url.path == "/sp/negativeKeywords"
        ]
        hermanas_muts = [
            r
            for r in muts
            if json.loads(r.content)["negativeKeywords"][0]["adGroupId"]
            != setup["origen"]["ag_ext"]
        ]
        assert hermanas_muts == [], "LIST incompleto: cero POST de hermanas"
        for rol in hermanas:
            reg = job["ext"]["hermanas"][rol]
            assert "negative_id" not in reg, (rol, reg)
            assert None not in reg.values(), (rol, reg)
            assert reg == {"motivo": "list_ambiguo"}, (rol, reg)


@_skip_db
def test_identidad_adopta_enabled_exact_e_ignora_archived_y_phrase():
    """Previo con ENABLED+NEGATIVE_EXACT en h1 (adoptada, cero POST),
    ARCHIVED en h2 (ignorado: POST) y NEGATIVE_PHRASE en h3 (nunca se
    adopta: POST). Regla 9: adoptar ARCHIVED duplicaria un negativo
    archivado; adoptar PHRASE bloquearia con otro match."""
    with db_f2("orbit_hna_ident") as conn:
        setup = _grupo_listo(conn)
        h1, h2, h3 = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        p1, p2, p3 = (setup["roles"][r] for r in (h1, h2, h3))
        sembrados = [
            _neg_hermana(p1["ag_ext"], p1["camp_ext"], "n-viva"),
            _neg_hermana(p2["ag_ext"], p2["camp_ext"], "n-vieja", state="ARCHIVED"),
            _neg_hermana(p3["ag_ext"], p3["camp_ext"], "n-frase", match="NEGATIVE_PHRASE"),
        ]
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
        _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        handler, vistos = _handler_harvest(negatives=sembrados)
        libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, setup["ciclo_ejec"]),
        )
        job = _job_de(conn, dec)
        assert job["fase"] == "done", job
        assert job["ext"]["hermanas"][h1] == {"negative_id": "n-viva", "creada": False}
        assert job["ext"]["hermanas"][h2]["creada"] is True
        assert job["ext"]["hermanas"][h2]["negative_id"] != "n-vieja"
        assert job["ext"]["hermanas"][h3]["creada"] is True
        assert job["ext"]["hermanas"][h3]["negative_id"] != "n-frase"
        posts = [r for r in vistos if r.method == "POST" and r.url.path == "/sp/negativeKeywords"]
        posts_hermanas = [
            r
            for r in posts
            if json.loads(r.content)["negativeKeywords"][0]["adGroupId"]
            != setup["origen"]["ag_ext"]
        ]
        assert len(posts_hermanas) == 2, "adoptada sin POST; ARCHIVED y PHRASE con POST"


@_skip_db
def test_dos_barridos_batched_con_filtro_en_cada_pagina():
    """Un ciclo usa como maximo dos barridos logicos (previo + posterior),
    ambos con `adGroupIdFilter` en cada pagina: prohibido un LIST por
    hermana. Regla 9: sin batch, el flujo emitiria un LIST por hermana."""
    import json as _json

    with db_f2("orbit_hna_batch") as conn:
        setup = _grupo_listo(conn)
        corrido = _corre_harvest_grupo(conn, setup)
        listas = [r for r in corrido["vistos"] if r.url.path == "/sp/negativeKeywords/list"]
        # Origen (pre-POST sin filtro, contrato historico) + previo + posterior.
        hermanas_listas = [
            r for r in listas if "adGroupIdFilter" in (r.content.decode() if r.content else "")
        ]
        assert 1 <= len(hermanas_listas) <= 2, [r.url.path for r in listas]
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        esperados = {setup["roles"][r]["ag_ext"] for r in hermanas}
        for req in hermanas_listas:
            cuerpo = _json.loads(req.content)
            assert set(cuerpo["adGroupIdFilter"]["include"]) <= esperados, cuerpo
        previo = _json.loads(hermanas_listas[0].content)
        assert set(previo["adGroupIdFilter"]["include"]) == esperados, (
            "el previo cubre todas las pendientes de una vez"
        )


def _cliente_lista(handler):
    """Write client falso solo-lectura para probar `_lista_filtrada`."""
    from app.ads.config import AdsCredentials
    from app.ads.write import AdsWriteClient

    return AdsWriteClient(
        AdsCredentials(
            client_id="fake-id-hna-1",
            client_secret="fake-secret-hna-1",
            refresh_token="fake-refresh-hna-1",
        ),
        platform="amazon_us",
        profile_id=404040,
        modo_confirmado="live",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def test_lista_filtrada_filtro_en_cada_pagina_y_senales_de_ambiguedad():
    """`_lista_filtrada` preserva el filtro en cada pagina; token repetido o
    item fuera del filtro -> "ambiguo"; `nextToken` vivo al tope ->
    "truncado". Regla 9: sin cada guarda, el barrido afirmaria completitud
    que no tiene."""
    import json as _json

    import httpx as _httpx

    from app.apply_harvest import _lista_filtrada

    cuerpos: list[dict] = []

    def _handler(request):
        if request.url.host == "api.amazon.com":
            return _httpx.Response(200, json={"access_token": "fake-access-hna", "expires_in": 1})
        cuerpos.append(_json.loads(request.content) if request.content else {})
        pagina = len(cuerpos)
        if pagina == 1:
            return _httpx.Response(
                200,
                json={
                    "negativeKeywords": [
                        {
                            "adGroupId": "6201",
                            "keywordId": "n-1",
                            "keywordText": "t",
                            "matchType": "NEGATIVE_EXACT",
                            "state": "ENABLED",
                        }
                    ],
                    "totalResults": 2,
                    "nextToken": "1",
                },
            )
        return _httpx.Response(
            200,
            json={
                "negativeKeywords": [
                    {
                        "adGroupId": "6202",
                        "keywordId": "n-2",
                        "keywordText": "t",
                        "matchType": "NEGATIVE_EXACT",
                        "state": "ENABLED",
                    }
                ],
                "totalResults": 2,
            },
        )

    items, estado = _lista_filtrada(_cliente_lista(_handler), ["6201", "6202"])
    assert estado == "ok"
    assert [x["keywordId"] for x in items] == ["n-1", "n-2"]
    assert len(cuerpos) == 2
    assert all(
        set(c.get("adGroupIdFilter", {}).get("include", [])) == {"6201", "6202"} for c in cuerpos
    ), "filtro en cada pagina"

    def _handler_repite(request):
        if request.url.host == "api.amazon.com":
            return _httpx.Response(200, json={"access_token": "fake-access-hna", "expires_in": 1})
        return _httpx.Response(
            200, json={"negativeKeywords": [], "totalResults": 9, "nextToken": "mismo"}
        )

    _items, estado = _lista_filtrada(_cliente_lista(_handler_repite), ["6201"])
    assert estado == "ambiguo", "token que no avanza"

    def _handler_filtro_ignorado(request):
        if request.url.host == "api.amazon.com":
            return _httpx.Response(200, json={"access_token": "fake-access-hna", "expires_in": 1})
        return _httpx.Response(
            200,
            json={
                "negativeKeywords": [
                    {
                        "adGroupId": "9999",
                        "keywordId": "n-x",
                        "keywordText": "t",
                        "matchType": "NEGATIVE_EXACT",
                        "state": "ENABLED",
                    }
                ],
                "totalResults": 1,
            },
        )

    _items, estado = _lista_filtrada(_cliente_lista(_handler_filtro_ignorado), ["6201"])
    assert estado == "ambiguo", "filtro ignorado por el servidor"


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {"negativeKeywords": None}, {"negativeKeywords": "nada"}, {"otra_clave": []}],
)
def test_lista_filtrada_200_malformado_es_unknown(cuerpo):
    """Un 200 sin contenedor util ({}, null, no-lista, otra clave) es
    unknown (`ambiguo`), jamas ([], "ok"): tratarlo como ausencia
    habilitaria POST contra un LIST que no dijo nada. Regla 9: sin la
    guarda, el barrido afirmaria completitud sobre un body vacio."""
    import httpx as _httpx3

    from app.apply_harvest import _lista_filtrada

    def _handler_malo(request):
        if request.url.host == "api.amazon.com":
            return _httpx3.Response(200, json={"access_token": "fake-access-malo", "expires_in": 1})
        return _httpx3.Response(200, json=cuerpo)

    items, estado = _lista_filtrada(_cliente_lista(_handler_malo), ["6201"])
    assert (items, estado) == ([], "ambiguo")


def _body_con(
    elementos,
    *,
    token=None,
    contenedor="negativeKeywords",
    path="/sp/negativeKeywords/list",
):
    """Body de LIST con elementos y token dados (los tests R3 inyectan
    formas malformadas)."""
    body: dict = {contenedor: elementos, "totalResults": len(elementos)}
    if token is not None:
        body["nextToken"] = token
    return body


@pytest.mark.parametrize(
    "elementos",
    [[None], ["texto"], [42]],
)
def test_lista_filtrada_item_o_token_malformado_es_unknown(elementos):
    """Elemento no-dict (null, string, numero) en el contenedor es unknown
    (`ambiguo`), jamas presencia filtrada: un item ilegible no confirma ni
    niega nada (AC-1 exige elementos dict). Los dicts incompletos tambien
    invalidan la pagina (identidad y estado no se infieren). Regla 9
    (r3, AC-1): sin la guarda, el barrido filtraba sobre contenido
    parcialmente malformado y declaraba ausencia."""
    import httpx as _httpx7

    from app.apply_harvest import _lista_filtrada

    def _handler_items(request):
        if request.url.host == "api.amazon.com":
            return _httpx7.Response(
                200, json={"access_token": "fake-access-items", "expires_in": 1}
            )
        return _httpx7.Response(200, json=_body_con(elementos))

    items, estado = _lista_filtrada(_cliente_lista(_handler_items), ["6201"])
    assert (items, estado) == ([], "ambiguo")


@pytest.mark.parametrize("token_malo", [[], {}, 42, ""])
def test_lista_filtrada_token_malformado_es_unknown(token_malo):
    """`nextToken` no-string-no-vacio (lista, dict, numero, vacio) es
    unknown: no es pagina final ni token reenviable. Regla 9 (r3, AC-1)."""
    import httpx as _httpx8

    from app.apply_harvest import _lista_filtrada

    def _handler_token(request):
        if request.url.host == "api.amazon.com":
            return _httpx8.Response(200, json={"access_token": "fake-access-tok", "expires_in": 1})
        return _httpx8.Response(200, json=_body_con([], token=token_malo))

    items, estado = _lista_filtrada(_cliente_lista(_handler_token), ["6201"])
    assert (items, estado) == ([], "ambiguo")


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"keywords": [None], "totalResults": 1},
        {"keywords": ["x"], "totalResults": 1},
        {"keywords": [], "totalResults": 0, "nextToken": []},
        {"keywords": [], "totalResults": 0, "nextToken": {}},
        {"keywords": [], "totalResults": 0, "nextToken": ""},
    ],
)
def test_lista_completa_item_o_token_malformado_es_incompleta(cuerpo):
    """El lector completo es igual de estricto: elemento no-dict o token
    invalido -> (items, False). Regla 9 (r3, AC-1): la reversa jamas
    concluye ausencia sobre lectura malformada."""
    import httpx as _httpx9

    from app.apply_harvest import _lista_completa

    def _handler_kw_malo(request):
        if request.url.host == "api.amazon.com":
            return _httpx9.Response(
                200, json={"access_token": "fake-access-kwmalo", "expires_in": 1}
            )
        return _httpx9.Response(200, json=cuerpo)

    items, completa = _lista_completa(_cliente_lista(_handler_kw_malo), "/sp/keywords/list")
    assert completa is False


_FALTANTE = object()
_CAMPOS_LIST = ("keywordId", "adGroupId", "keywordText", "matchType", "state")


def _elemento_list_ok(*, keyword=False) -> dict:
    """Fila LIST completa (identidad + estado) para ambos lectores."""
    return {
        "adGroupId": "6201",
        "keywordId": "k-1" if keyword else "n-1",
        "keywordText": "t",
        "matchType": "EXACT" if keyword else "NEGATIVE_EXACT",
        "state": "ENABLED",
    }


def _elemento_list_roto(campo: str, valor, *, keyword=False) -> dict:
    item = _elemento_list_ok(keyword=keyword)
    if valor is _FALTANTE:
        item.pop(campo)
    else:
        item[campo] = valor
    return item


@pytest.mark.parametrize("campo", _CAMPOS_LIST)
@pytest.mark.parametrize("valor", [_FALTANTE, None, "", []])
def test_lista_filtrada_elemento_incompleto_es_unknown(campo, valor):
    """Faltante/null/vacio/lista en keywordId, adGroupId, keywordText,
    matchType o state convierte TODA la pagina en unknown. Regla 9: el
    barrido viejo aceptaba el dict y `_coincidencias` adoptaba
    negative_id=None."""
    import httpx as _httpx_inc

    from app.apply_harvest import _lista_filtrada

    def _handler(request):
        if request.url.host == "api.amazon.com":
            return _httpx_inc.Response(
                200, json={"access_token": "fake-access-inc", "expires_in": 1}
            )
        return _httpx_inc.Response(200, json=_body_con([_elemento_list_roto(campo, valor)]))

    items, estado = _lista_filtrada(_cliente_lista(_handler), ["6201"])
    assert (items, estado) == ([], "ambiguo")


@pytest.mark.parametrize("campo", _CAMPOS_LIST)
@pytest.mark.parametrize("valor", [_FALTANTE, None, "", []])
def test_lista_completa_elemento_incompleto_es_incompleta(campo, valor):
    """El lector completo es igual de estricto: un solo campo incompleto
    -> (items, False). Regla 9: en reversa, la misma pagina confirmaba
    que un ID desaparecio."""
    import httpx as _httpx_inc2

    from app.apply_harvest import _lista_completa

    def _handler(request):
        if request.url.host == "api.amazon.com":
            return _httpx_inc2.Response(
                200, json={"access_token": "fake-access-inc2", "expires_in": 1}
            )
        return _httpx_inc2.Response(
            200,
            json=_body_con(
                [_elemento_list_roto(campo, valor, keyword=True)],
                contenedor="keywords",
            ),
        )

    items, completa = _lista_completa(_cliente_lista(_handler), "/sp/keywords/list")
    assert (items, completa) == ([], False)


@pytest.mark.parametrize("campo,valor", [("keywordId", True), ("adGroupId", False)])
def test_lista_filtrada_id_booleano_es_unknown(campo, valor):
    """IDs booleanos (bool es int en Python) no son identidad. Regla 9."""
    import httpx as _httpx_bool

    from app.apply_harvest import _lista_filtrada

    def _handler(request):
        if request.url.host == "api.amazon.com":
            return _httpx_bool.Response(
                200, json={"access_token": "fake-access-bool", "expires_in": 1}
            )
        return _httpx_bool.Response(200, json=_body_con([_elemento_list_roto(campo, valor)]))

    items, estado = _lista_filtrada(_cliente_lista(_handler), ["6201"])
    assert (items, estado) == ([], "ambiguo")


def test_lista_filtrada_ids_enteros_siguen_ok():
    """keywordId/adGroupId enteros no-bool siguen siendo identidad valida."""
    import httpx as _httpx_int

    from app.apply_harvest import _lista_filtrada

    item = _elemento_list_ok()
    item["keywordId"] = 101
    item["adGroupId"] = 6201

    def _handler(request):
        if request.url.host == "api.amazon.com":
            return _httpx_int.Response(
                200, json={"access_token": "fake-access-int", "expires_in": 1}
            )
        return _httpx_int.Response(200, json=_body_con([item]))

    items, estado = _lista_filtrada(_cliente_lista(_handler), ["6201"])
    assert estado == "ok"
    assert items[0]["keywordId"] == 101


def test_lista_filtrada_un_incompleto_tira_la_pagina_entera():
    """Una fila valida + una sin keywordId -> unknown (no se adopta la
    valida ni se declara ausencia)."""
    import httpx as _httpx_mix

    from app.apply_harvest import _lista_filtrada

    def _handler(request):
        if request.url.host == "api.amazon.com":
            return _httpx_mix.Response(
                200, json={"access_token": "fake-access-mixinc", "expires_in": 1}
            )
        return _httpx_mix.Response(
            200,
            json=_body_con([_elemento_list_ok(), _elemento_list_roto("keywordId", _FALTANTE)]),
        )

    items, estado = _lista_filtrada(_cliente_lista(_handler), ["6201"])
    assert (items, estado) == ([], "ambiguo")


def test_lectores_nuevos_usen_list_sellado_sin_profile_del_caller():
    """AC-3: `_lista_filtrada` y `_lista_completa` leen por la puerta
    sellada (scope de la instancia) y ninguna firma acepta `profile_id`
    del caller. Regla 9: llamar `list_objects` directo con profile a mano
    reabre el hueco del readback con scope ajeno (hallazgo r1 del brief
    S13)."""
    import inspect as _inspect

    import app.apply_harvest as _ah

    for nombre in ("_lista_filtrada", "_lista_completa"):
        fn = getattr(_ah, nombre)
        assert "profile_id" not in _inspect.signature(fn).parameters, nombre
        fuente = _inspect.getsource(fn)
        assert "list_sellado" in fuente, nombre
        assert "list_objects" not in fuente, nombre


# ---------------------------------------------------------------------------
# Bloque 3: ledger de hermana y recuperacion de crash con procedencia
# ---------------------------------------------------------------------------


def _libera_cola(conn, qid: int) -> None:
    """Pone una fila en released (el hook reclama applying el mismo)."""
    conn.execute(
        "UPDATE apply_queue SET estado = 'released', released_at = now() WHERE id = %s", (qid,)
    )


def _job_en_hermanas(
    conn, setup: dict, dec: int, qid: int, *, term: str = TERMINO_F2, cola_final: str = "applied"
):
    """Siembra un job en `hermanas_negadas` con roster, resumen confirmado y
    cola applied (camina las fases por UPDATE, como exige el trigger). Base
    para reanudacion, caps y cierre sin pasar por el flujo completo."""
    jid = conn.execute(
        "INSERT INTO harvest_job (decision_id, search_term, platform, ad_entity_id, fase)"
        " VALUES (%s, %s, 'amazon_us', %s, 'pending') RETURNING id",
        (dec, term, setup["origen"]["ag"]),
    ).fetchone()[0]
    conn.execute("UPDATE harvest_job SET fase = 'negative_created' WHERE id = %s", (jid,))
    conn.execute(
        "UPDATE harvest_job SET fase = 'exact_created', external_ids = %s WHERE id = %s",
        (Json({"keyword_id": "k-1"}), jid),
    )
    roster = {
        r: {
            "campaign_id": setup["roles"][r]["camp_ext"],
            "ad_group_id": setup["roles"][r]["ag_ext"],
        }
        for r in ROLES_DISCOVERY
        if r != setup["origen_rol"]
    }
    conn.execute(
        "UPDATE harvest_job SET fase = 'hermanas_negadas', external_ids = %s WHERE id = %s",
        (
            Json(
                {
                    "keyword_id": "k-1",
                    "negative_id": "n-0",
                    "hermanas_objetivo": roster,
                    "hermanas": {},
                    "hermanas_ciclos": 0,
                }
            ),
            jid,
        ),
    )
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
        " ack, resultado, finished_at) VALUES (%s, 1, 'normal', '{}'::jsonb, true,"
        " '{}'::jsonb, 'ok', now()), (%s, 2, 'normal', '{}'::jsonb, false,"
        " '{}'::jsonb, 'ok', now())",
        (dec, dec),
    )
    conn.execute(
        "INSERT INTO decision_application (decision_id, confirmed_at, platform_ack,"
        " verify_ok, applied_cycle_id) VALUES (%s, now(), '{}'::jsonb, true, %s)",
        (dec, setup["ciclo_ejec"]),
    )
    if cola_final in ("applying", "applied"):
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = now() WHERE id = %s",
            (qid,),
        )
    if cola_final == "applied":
        conn.execute(
            "UPDATE apply_queue SET estado = 'applied', applied_at = now() WHERE id = %s", (qid,)
        )
    # "released": no se toca (ya quedo released por quien siembra la fila).
    conn.execute(
        "UPDATE apply_quota_state SET used = 1 WHERE motor = %s",
        ("ads_optimizer:amazon_us:harvest",),
    )
    return jid


@_skip_db
def test_crash_sin_ack_nunca_marca_propio(monkeypatch):
    """Respuesta perdida sin ack (crash entre POST y sello): aunque el
    siguiente previo encuentre la identidad, sin id de ack duradera que la
    pruebe queda pendiente `red_ambigua` con la fila abierta — jamas
    `creada = true`. Al tope cierra done con la pendiente (fuga visible,
    nunca borrado ajeno). Regla 9 (r2): sin esta guarda, el cruce
    marcaría propia una id que Orbit pudo no crear y la reversa la
    borraría."""
    from app.ads.client import AdsApiError as _AdsApiError

    with db_f2("orbit_hna_crash") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]

        from app.ads.write import AdsWriteClient

        original = AdsWriteClient.crear_negative_exacto
        hermanas_ag = {setup["roles"][r]["ag_ext"] for r in hermanas}
        llamadas: list[str] = []

        def _corta_despues_del_post(self, ad_group_id, campaign_id, keyword_text):
            # Call-through: el POST LLEGA al almacen; la respuesta se pierde
            # SOLO en la primera hermana (el origen va primero y no se toca).
            resp = original(self, ad_group_id, campaign_id, keyword_text)
            if str(ad_group_id) in hermanas_ag and not llamadas:
                llamadas.append(str(ad_group_id))
                raise _AdsApiError("respuesta perdida tras el POST")
            return resp

        monkeypatch.setattr(AdsWriteClient, "crear_negative_exacto", _corta_despues_del_post)
        corrido = _corre_harvest_grupo(conn, setup)
        dec = corrido["dec"]
        ag_crash = llamadas[0]
        rol_crash = next(r for r in hermanas if setup["roles"][r]["ag_ext"] == ag_crash)
        fila_abierta = conn.execute(
            "SELECT id, resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'hermana'"
            " AND request_payload->>'adGroupId' = %s ORDER BY id DESC LIMIT 1",
            (dec, ag_crash),
        ).fetchone()
        assert fila_abierta[1] is None, "la fila quedo abierta (sin sello)"
        job = _job_de(conn, dec)
        assert "negative_id" not in job["ext"]["hermanas"].get(rol_crash, {})

        # El ciclo 2 corre contra un almacen que SI tiene lo que el ciclo 1
        # creo (el POST llego): se siembra la identidad viva del crash.
        monkeypatch.undo()
        from app.apply_harvest import reconcilia_harvest

        crash_vivo = {
            "adGroupId": ag_crash,
            "campaignId": setup["roles"][rol_crash]["camp_ext"],
            "keywordId": "n-del-crash",
            "keywordText": TERMINO_F2,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
        handler2, vistos2 = _handler_harvest(negatives=[crash_vivo])
        resumen = reconcilia_harvest(
            conn, _aplicador(conn, handler2, setup["ciclo_ejec"]), "amazon_us"
        )

        assert resumen.jobs_done == 0, "sin prueba no hay cierre"
        job2 = _job_de(conn, dec)
        assert job2["fase"] == "hermanas_negadas", job2
        assert job2["ext"]["hermanas"][rol_crash] == {"motivo": "red_ambigua"}, job2["ext"]
        sello = conn.execute(
            "SELECT resultado, finished_at IS NOT NULL FROM apply_attempt WHERE id = %s",
            (fila_abierta[0],),
        ).fetchone()
        assert sello[0] is None and not sello[1], "la fila sigue abierta"
        # Cero POST nuevos a la hermana del crash: el previo la encontro.
        posts_crash = [
            r
            for r in vistos2
            if r.method == "POST"
            and r.url.path == "/sp/negativeKeywords"
            and json.loads(r.content)["negativeKeywords"][0]["adGroupId"] == ag_crash
        ]
        assert posts_crash == []
        # Al tercer ciclo cierra done con la pendiente declarada.
        handler3, _v3 = _handler_harvest(negatives=[crash_vivo])
        resumen3 = reconcilia_harvest(
            conn, _aplicador(conn, handler3, setup["ciclo_ejec"]), "amazon_us"
        )
        assert resumen3.jobs_done == 1
        job3 = _job_de(conn, dec)
        assert job3["fase"] == "done", job3
        assert job3["ext"]["hermanas_pendientes"] == {rol_crash: "red_ambigua"}, job3["ext"]
        assert [a.motivo for a in resumen3.alertas] == ["hermanas_pendientes"]
        # AC-8: la reversa no borra por inferencia — el plan no referencia
        # la ag del crash (sin id probada no hay paso que la toque).
        from app.apply_harvest import plan_reversa_harvest as _plan_rev

        _jid = conn.execute("SELECT id FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()[
            0
        ]
        _p, _t, _d, _pasos = _plan_rev(conn, _jid)
        assert all(p.ad_group_ext != ag_crash for p in _pasos), [
            (p.rol, p.objeto_id) for p in _pasos
        ]


@_skip_db
def test_recupera_dos_abiertas_sin_atribuir_ajena_intacta(monkeypatch):
    """Dos hermanas con respuesta perdida (doble crash, sin ack): quedan
    pendientes sin atribuir y la tercera (sellada ok en su ciclo) no se
    re-sella. Regla 9 (r2): atribuir a ciegas marcaria propias ids sin
    procedencia; un `_sella_pendientes` marcaria intentos ajenos."""
    from app.ads.client import AdsApiError as _AdsApiError2

    with db_f2("orbit_hna_crash2") as conn:
        setup = _grupo_listo(conn)
        roles = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]

        from app.ads.write import AdsWriteClient

        original = AdsWriteClient.crear_negative_exacto
        hermanas_ag = {setup["roles"][r]["ag_ext"] for r in roles}
        llamadas: list[str] = []

        def _corta_dos(self, ad_group_id, campaign_id, keyword_text):
            resp = original(self, ad_group_id, campaign_id, keyword_text)
            if str(ad_group_id) in hermanas_ag and len(llamadas) < 2:
                llamadas.append(str(ad_group_id))
                raise _AdsApiError2("respuesta perdida tras el POST")
            return resp

        monkeypatch.setattr(AdsWriteClient, "crear_negative_exacto", _corta_dos)
        corrido = _corre_harvest_grupo(conn, setup)
        dec = corrido["dec"]
        ags_crash = list(llamadas)
        assert len(ags_crash) == 2
        roles_crash = [r for r in roles if setup["roles"][r]["ag_ext"] in ags_crash]
        rol_sano = next(r for r in roles if r not in roles_crash)
        ag_sana = setup["roles"][rol_sano]["ag_ext"]
        sello_sano_antes = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'hermana'"
            " AND request_payload->>'adGroupId' = %s",
            (dec, ag_sana),
        ).fetchone()[0]
        assert sello_sano_antes == "ok"

        # Ciclo 2 contra un almacen con lo que el ciclo 1 creo: sin ack
        # duradero que pruebe, no se atribuye.
        monkeypatch.undo()
        from app.apply_harvest import reconcilia_harvest

        vivos = [
            {
                "adGroupId": setup["roles"][r]["ag_ext"],
                "campaignId": setup["roles"][r]["camp_ext"],
                "keywordId": f"n-viva-{r}",
                "keywordText": TERMINO_F2,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
            for r in roles_crash
        ]
        handler2, _v2 = _handler_harvest(negatives=vivos)
        resumen = reconcilia_harvest(
            conn, _aplicador(conn, handler2, setup["ciclo_ejec"]), "amazon_us"
        )
        assert resumen.jobs_done == 0, "sin prueba no hay cierre"
        job2 = _job_de(conn, dec)
        assert job2["fase"] == "hermanas_negadas", job2
        for rol in roles_crash:
            assert job2["ext"]["hermanas"][rol] == {"motivo": "red_ambigua"}, (rol, job2["ext"])
            abierta = conn.execute(
                "SELECT finished_at IS NULL FROM apply_attempt WHERE decision_id = %s"
                " AND tipo = 'hermana' AND request_payload->>'adGroupId' = %s",
                (dec, setup["roles"][rol]["ag_ext"]),
            ).fetchone()[0]
            assert abierta is True, (rol, "la fila sigue abierta")
        sello_sano_despues = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'hermana'"
            " AND request_payload->>'adGroupId' = %s",
            (dec, ag_sana),
        ).fetchone()[0]
        assert sello_sano_despues == sello_sano_antes == "ok", "la ajena no se toca"


@_skip_db
def test_cap_tres_intentos_por_adgroup():
    """Con tres filas de hermana para un ad group (tope por identidad), el
    ciclo siguiente no POSTea ahi: pendiente `tope_intentos` sin fila nueva,
    mientras las demas resuelven. Regla 9: sin el cap por identidad, el
    reintento emitiria un 4o POST al mismo ad group."""
    with db_f2("orbit_hna_cap") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        _job_en_hermanas(conn, setup, dec, qid)
        roles = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_tope = setup["roles"][roles[0]]["ag_ext"]
        for seq in (3, 4, 5):
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada, ack, resultado, finished_at) VALUES (%s, %s, 'hermana',"
                " %s, false, '{}'::jsonb, 'fallo http 400', now())",
                (
                    dec,
                    seq,
                    Json(
                        {
                            "adGroupId": ag_tope,
                            "campaignId": setup["roles"][roles[0]]["camp_ext"],
                            "keywordText": TERMINO_F2,
                            "matchType": "NEGATIVE_EXACT",
                            "state": "ENABLED",
                        }
                    ),
                ),
            )

        from app.apply_harvest import reconcilia_harvest

        handler, vistos = _handler_harvest()
        reconcilia_harvest(conn, _aplicador(conn, handler, setup["ciclo_ejec"]), "amazon_us")

        job = _job_de(conn, dec)
        assert job["ext"]["hermanas"][roles[0]] == {"motivo": "tope_intentos"}
        n = conn.execute(
            "SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'hermana'"
            " AND request_payload->>'adGroupId' = %s",
            (dec, ag_tope),
        ).fetchone()[0]
        assert n == 3, "sin 4a fila: el tope muerde antes del POST"
        posts_tope = [
            r
            for r in vistos
            if r.method == "POST"
            and r.url.path == "/sp/negativeKeywords"
            and json.loads(r.content)["negativeKeywords"][0]["adGroupId"] == ag_tope
        ]
        assert posts_tope == []
        assert job["fase"] == "hermanas_negadas", "las demas siguen; el job no cierra por el tope"


@_skip_db
def test_seq_global_monotonica_en_flujo_de_hermanas():
    """Flujo completo de grupo: seq 1..5 sin repetir y tipos
    [normal, normal, hermana x3] (una unidad de quota). Fija el DoD-5 a
    nivel de flujo."""
    with db_f2("orbit_hna_seq") as conn:
        setup = _grupo_listo(conn)
        corrido = _corre_harvest_grupo(conn, setup)
        filas = conn.execute(
            "SELECT seq, tipo, quota_cobrada FROM apply_attempt WHERE decision_id = %s ORDER BY id",
            (corrido["dec"],),
        ).fetchall()
        assert [(f[0], f[1]) for f in filas] == [
            (1, "normal"),
            (2, "normal"),
            (3, "hermana"),
            (4, "hermana"),
            (5, "hermana"),
        ], filas
        assert [f[2] for f in filas] == [True, False, False, False, False]
        quota = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:harvest",),
        ).fetchone()[0]
        assert quota == 1, "una unidad por harvest aunque sean 5 HTTPs"


# ---------------------------------------------------------------------------
# Bloque 4: reintentos con motivos, cierre al tope y alerta veraz
# ---------------------------------------------------------------------------


def _reconcilia(conn, setup, handler):
    from app.apply_harvest import reconcilia_harvest

    return reconcilia_harvest(conn, _aplicador(conn, handler, setup["ciclo_ejec"]), "amazon_us")


def _posts_hermanas(vistos, ag_ext=None):
    posts = [r for r in vistos if r.method == "POST" and r.url.path == "/sp/negativeKeywords"]
    if ag_ext is None:
        return posts
    return [r for r in posts if json.loads(r.content)["negativeKeywords"][0]["adGroupId"] == ag_ext]


@_skip_db
def test_fallo_400_deja_pendiente_y_reintenta_sin_quota():
    """400 en una hermana: pendiente `http_400`, el job sigue, el ciclo
    siguiente la reintenta sin cobrar quota y resuelve. Regla 9: sin
    pendiente con motivo, el fallo tumbaria el harvest o se perderia."""
    with db_f2("orbit_hna_400") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}}
        )
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "hermanas_negadas", job
        assert job["ext"]["hermanas"][hermanas[0]] == {"motivo": "http_400"}
        assert job["ext"]["hermanas_ciclos"] == 1

        handler2, _v2 = _handler_harvest()
        resumen = _reconcilia(conn, setup, handler2)
        assert resumen.jobs_done == 1
        job2 = _job_de(conn, corrido["dec"])
        assert job2["fase"] == "done", job2
        assert job2["ext"]["hermanas"][hermanas[0]]["creada"] is True
        assert job2["ext"]["hermanas_pendientes"] == {}, "cierre limpio con clave vacia"
        quota = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:harvest",),
        ).fetchone()[0]
        assert quota == 1, "reintento sin recobro"


@_skip_db
def test_pt_rechazada_queda_pendiente_con_motivo_propio():
    """PT rechazada en vivo (400): pendiente `pt_no_acepta_negative_keyword`
    (no `http_400`), el job sigue y la reconciliacion la reintenta con la
    misma semantica que cualquier hermana. Regla 9: un skip aparte
    esconderia el reintento."""
    with db_f2("orbit_hna_pt") as conn:
        setup = _grupo_listo(conn, origen_rol="category_phrase")
        assert "product_targeting" in [r for r in ROLES_DISCOVERY if r != "category_phrase"]
        ag_pt = setup["roles"]["product_targeting"]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_pt: 400}}
        )
        job = _job_de(conn, corrido["dec"])
        assert job["ext"]["hermanas"]["product_targeting"] == {
            "motivo": "pt_no_acepta_negative_keyword"
        }
        assert job["fase"] == "hermanas_negadas"

        handler2, _v2 = _handler_harvest()
        resumen = _reconcilia(conn, setup, handler2)
        assert resumen.jobs_done == 1
        job2 = _job_de(conn, corrido["dec"])
        assert job2["ext"]["hermanas"]["product_targeting"]["creada"] is True


@_skip_db
def test_5xx_y_ack_sin_id_quedan_pendientes_con_motivo():
    """503 en una hermana -> `http_5xx`; ack 207 sin id -> `ack_sin_id`.
    Ambos pendientes sin tumbar el job. Regla 9: sin motivo distinto, un
    5xx ambiguo se trataria como rechazo definitivo o como exito."""
    with db_f2("orbit_hna_5xx") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 503}}
        )
        job = _job_de(conn, corrido["dec"])
        assert job["ext"]["hermanas"][hermanas[0]] == {"motivo": "http_5xx"}, job["ext"]


@_skip_db
def test_ack_sin_id_queda_pendiente():
    """Ack 207 sin id en hermanas -> `ack_sin_id` (fail-closed: sin id no
    hay evidencia del corte). Regla 9: sin el motivo, el ack vacio se
    trataria como exito o como ambiguo sin rastro."""
    with db_f2("orbit_hna_ackid") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        _job_en_hermanas(conn, setup, dec, qid)
        handler, _vistos = _handler_harvest(ack_negative_sin_id=True)
        _reconcilia(conn, setup, handler)
        job = _job_de(conn, dec)
        for rol in [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]:
            assert job["ext"]["hermanas"][rol] == {"motivo": "ack_sin_id"}, (rol, job["ext"])


@_skip_db
def test_merge_conserva_exitos_previos():
    """Ciclo 1 resuelve h1 y h3, falla h2; ciclo 2 resuelve h2: los ids de
    h1/h3 no cambian (merge superficial con el dict completo, sin
    reemplazos). Regla 9: un reemplazo del dict perderia los exitos."""
    with db_f2("orbit_hna_merge") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[1]]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}}
        )
        antes = _job_de(conn, corrido["dec"])["ext"]["hermanas"]
        assert antes[hermanas[0]]["creada"] is True and antes[hermanas[2]]["creada"] is True

        handler2, _v2 = _handler_harvest()
        _reconcilia(conn, setup, handler2)
        despues = _job_de(conn, corrido["dec"])["ext"]["hermanas"]
        assert despues[hermanas[0]] == antes[hermanas[0]]
        assert despues[hermanas[2]] == antes[hermanas[2]]
        assert despues[hermanas[1]]["creada"] is True


@_skip_db
def test_gate_origen_pausado_no_falla_el_harvest():
    """Origen pausado durante la higiene: hermanas pendientes con
    `ancestro_no_enabled`, cero POST, el job sigue; al tope cierra `done`
    con alerta (jamas failed, jamas reversa de la keyword). Regla 9: sin la
    exencion post-sello, el gate fallaria un harvest ya aplicado."""
    with db_f2("orbit_hna_gate") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}}
        )
        assert _job_de(conn, corrido["dec"])["fase"] == "hermanas_negadas"
        # El dueno pausa el origen a mitad de la higiene.
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id IN (%s, %s)",
            (setup["origen"]["ag"], setup["origen"]["camp"]),
        )

        handler2, vistos2 = _handler_harvest()
        resumen = _reconcilia(conn, setup, handler2)
        assert resumen.jobs_done == 0 and resumen.jobs_failed == 0
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "hermanas_negadas", job
        # La pendiente toma el motivo del gate; las ya resueltas no se tocan.
        assert job["ext"]["hermanas"][hermanas[0]] == {"motivo": "ancestro_no_enabled"}
        for rol in hermanas[1:]:
            assert job["ext"]["hermanas"][rol]["creada"] is True, (rol, job["ext"])
        assert _posts_hermanas(vistos2) == [], "gate: cero POST"
        cola = conn.execute(
            "SELECT estado FROM apply_queue WHERE id = %s", (corrido["qid"],)
        ).fetchone()[0]
        assert cola == "applied", "la cola no se toca post-sello"
        ver = conn.execute(
            "SELECT verify_ok FROM decision_application WHERE decision_id = %s",
            (corrido["dec"],),
        ).fetchone()[0]
        assert ver is True, "el resumen no se toca post-sello"


@_skip_db
def test_ciclo_3_cierra_done_con_pendientes_y_alerta_veraz(monkeypatch):
    """400 persistente en una hermana: al tercer ciclo el job cierra `done`
    con `hermanas_pendientes` y una AlertaHarvest que NO dice "failed" (el
    evento de valor quedo aplicado). Regla 9: sin el tope, la higiene seria
    eterna; sin la alerta, la pendiente seria invisible."""
    from app import notifica as _notifica

    with db_f2("orbit_hna_topec") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        enviados: list[str] = []
        monkeypatch.setattr(_notifica, "canal_activo", lambda: True)
        monkeypatch.setattr(
            _notifica, "_envia_texto", lambda texto, transport=None: enviados.append(texto) or True
        )
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}}
        )
        ultimo = None
        for _ in range(2):
            handler, _v = _handler_harvest(fallo_post_negative_por_adgroup={ag_falla: 400})
            ultimo = _reconcilia(conn, setup, handler)
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "done", job
        assert job["ext"]["hermanas_ciclos"] == 3, job["ext"]
        assert job["ext"]["hermanas_pendientes"] == {hermanas[0]: "http_400"}, job["ext"]
        # F6 (r1): la reconciliacion recoge la alerta aunque estado == done.
        assert ultimo is not None
        assert [a.motivo for a in ultimo.alertas] == ["hermanas_pendientes"], (
            "la alerta de hermanas pendientes no se descarta en done"
        )
        assert len(enviados) == 1, "una alerta al cerrar con pendientes"
        texto = enviados[0]
        assert "failed" not in texto.lower(), texto
        assert TERMINO_F2 in texto and ag_falla not in texto, texto
        assert "http_400" in texto, texto
        # Keyword intacta: cero deletes en todo el flujo.
        todos = corrido["vistos"]
        assert [r for r in todos if r.url.path.endswith("/delete")] == []
        ver = conn.execute(
            "SELECT verify_ok FROM decision_application WHERE decision_id = %s",
            (corrido["dec"],),
        ).fetchone()[0]
        assert ver is True


@_skip_db
def test_posterior_truncado_en_ciclo_3_cierra_por_tope():
    """Posterior truncado en el ciclo 3: cierra done por tope con pendientes
    y alerta (no existe cuarto ciclo). Regla 9: sin pasar por el cierre, el
    job quedaria en `hermanas_negadas` para siempre esperando un ciclo 4."""
    import httpx as _httpx2

    with db_f2("orbit_hna_postrunc") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}}
        )
        dec = corrido["dec"]
        handler2, _v2 = _handler_harvest(fallo_post_negative_por_adgroup={ag_falla: 400})
        _reconcilia(conn, setup, handler2)
        llamadas = {"list": 0}

        def _posterior_trunco(request):
            if request.url.host == "api.amazon.com":
                return _httpx2.Response(
                    200, json={"access_token": "fake-access-post", "expires_in": 3600}
                )
            if request.url.path == "/sp/negativeKeywords/list":
                llamadas["list"] += 1
                if llamadas["list"] == 1:
                    return _httpx2.Response(200, json={"negativeKeywords": [], "totalResults": 0})
                # Token siempre nuevo: 20 paginas sin agotar -> truncado.
                return _httpx2.Response(
                    200,
                    json={
                        "negativeKeywords": [],
                        "totalResults": 99,
                        "nextToken": f"t{llamadas['list']}",
                    },
                )
            if request.url.path == "/sp/negativeKeywords":
                return _httpx2.Response(
                    207,
                    json={
                        "negativeKeywords": {
                            "error": [],
                            "success": [{"index": 0, "negativeKeywordId": "n-post"}],
                        }
                    },
                )
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        ultimo = _reconcilia(conn, setup, _posterior_trunco)
        job = _job_de(conn, dec)
        assert job["fase"] == "done", job
        assert job["ext"]["hermanas_ciclos"] == 3, job["ext"]
        assert job["ext"]["hermanas_pendientes"] == {hermanas[0]: "list_truncado"}, job["ext"]
        assert [a.motivo for a in ultimo.alertas] == ["hermanas_pendientes"]


@_skip_db
def test_membresia_cambiada_no_falla_harvest_confirmado():
    """Reanudacion en `hermanas_negadas` con la membresia viva cambiada (el
    origen ya no esta en el grupo): el job retoma con el roster CONGELADO
    hasta done, sin re-validar membresia ni entrar al camino failed.
    Regla 9: re-validando, un cambio de grupo fallaria un harvest ya
    aplicado e ignoraria el roster sellado."""
    with db_f2("orbit_hna_membresia") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        _job_en_hermanas(conn, setup, dec, qid)
        # El grupo cambia despues del sello: el origen sale del grupo.
        conn.execute(
            "DELETE FROM campana_grupo_rol WHERE ad_entity_id = %s", (setup["origen"]["camp"],)
        )
        handler, _v = _handler_harvest()
        resumen = _reconcilia(conn, setup, handler)
        assert resumen.jobs_done == 1 and resumen.jobs_failed == 0, resumen
        job = _job_de(conn, dec)
        assert job["fase"] == "done", job
        assert len(job["ext"]["hermanas"]) == 3, "roster congelado, no membresia viva"


@_skip_db
def test_dup_tras_repost_sigue_provisional_reversible():
    """Si un re-POST duplica (X del ciclo 1 aparece tarde junto a Z del
    ciclo 2, que se confirma), X sigue visible como provisional en el plan
    de reversa: nada aceptado por Amazon queda fuera de reversa. Regla 9
    (r4/grok-1): sin la inclusion, el duplicado seria inalcanzable para la
    reversa. El re-POST ante ausencia se conserva a proposito (progresar
    exige POSTear; el tope por identidad y los ciclos lo acotan)."""
    import httpx as _httpx12

    from app.apply_harvest import plan_reversa_harvest

    with db_f2("orbit_hna_duprov") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        _job_en_hermanas(conn, setup, dec, qid)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag0 = setup["roles"][hermanas[0]]["ag_ext"]
        llamadas = {"list": 0, "post": 0}

        def _dup(request):
            if request.url.host == "api.amazon.com":
                return _httpx12.Response(
                    200, json={"access_token": "fake-access-dup", "expires_in": 3600}
                )
            if request.url.path == "/sp/negativeKeywords/list":
                llamadas["list"] += 1
                if llamadas["list"] in (1, 3):
                    return _httpx12.Response(200, json={"negativeKeywords": [], "totalResults": 0})
                if llamadas["list"] == 2:
                    return _httpx12.Response(200, json={"negativeKeywords": [], "totalResults": 0})
                return _httpx12.Response(
                    200,
                    json={
                        "negativeKeywords": [
                            {
                                "adGroupId": ag0,
                                "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                                "keywordId": "n-dup-x",
                                "keywordText": TERMINO_F2,
                                "matchType": "NEGATIVE_EXACT",
                                "state": "ENABLED",
                            },
                            {
                                "adGroupId": ag0,
                                "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                                "keywordId": "n-dup-z",
                                "keywordText": TERMINO_F2,
                                "matchType": "NEGATIVE_EXACT",
                                "state": "ENABLED",
                            },
                        ],
                        "totalResults": 2,
                    },
                )
            if request.url.path == "/sp/negativeKeywords":
                llamadas["post"] += 1
                kid = "n-dup-x" if llamadas["post"] == 1 else "n-dup-z"
                return _httpx12.Response(
                    200,
                    json={
                        "negativeKeywords": {
                            "error": [],
                            "success": [{"index": 0, "negativeKeywordId": kid}],
                        }
                    },
                )
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        _reconcilia(conn, setup, _dup)
        _reconcilia(conn, setup, _dup)
        _reconcilia(conn, setup, _dup)
        job = _job_de(conn, dec)
        # Z confirmada (propia); X sellada con ack sin prueba.
        assert job["ext"]["hermanas"][hermanas[0]] == {
            "negative_id": "n-dup-z",
            "creada": True,
        }, job["ext"]
        jid = conn.execute("SELECT id FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()[
            0
        ]
        _p, _t, _d, pasos = plan_reversa_harvest(conn, jid)
        prov = [(p.rol, p.objeto_id) for p in pasos if p.provisional]
        assert prov == [(hermanas[0], "n-dup-x")], [(p.rol, p.objeto_id) for p in pasos]


@_skip_db
def test_quota_no_se_recobra_en_reintentos_de_higiene():
    """Tres ciclos con una hermana fallando: `used` sigue en 1 (la unidad
    la cobro el apply; la higiene y la reconciliacion jamas recobran)."""
    with db_f2("orbit_hna_quota") as conn:
        setup = _grupo_listo(conn)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        _corre_harvest_grupo(
            conn, setup, handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}}
        )
        for _ in range(2):
            handler, _v = _handler_harvest(fallo_post_negative_por_adgroup={ag_falla: 400})
            _reconcilia(conn, setup, handler)
        used = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = %s",
            ("ads_optimizer:amazon_us:harvest",),
        ).fetchone()[0]
        assert used == 1


@_skip_db
def test_reanudacion_retoma_indice_y_huerfanas_no_cierra():
    """Job sembrado en `hermanas_negadas`: la reconciliacion lo retoma hasta
    `done`; un segundo job del mismo trio choca con el indice parcial; y el
    barrido de huerfanas no cierra una fila applying con job en vuelo.
    Regla 9: sin la fase en las listas, el job quedaria zombi o duplicado."""
    import psycopg as _psycopg

    from app.apply_harvest_reconciliacion import _reconcilia_harvest_huerfanas

    with db_f2("orbit_hna_reanuda") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        # Cola applying con job en vuelo: huerfanas no la toca.
        _job_en_hermanas(conn, setup, dec, qid, cola_final="applying")
        assert _reconcilia_harvest_huerfanas(conn, "amazon_us") == 0
        estado = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert estado == "applying"
        # Segundo job del mismo trio choca con el indice parcial.
        with (
            pytest.raises(_psycopg.errors.UniqueViolation),
            conn.transaction(),
        ):
            conn.execute(
                "INSERT INTO harvest_job (decision_id, search_term, platform, ad_entity_id,"
                " fase) VALUES (%s, %s, 'amazon_us', %s, 'pending')",
                (dec, TERMINO_F2, setup["origen"]["ag"]),
            )
        # Y la reconciliacion lo retoma hasta done con cola applied.
        handler, _v = _handler_harvest()
        resumen = _reconcilia(conn, setup, handler)
        assert resumen.jobs_done == 1
        assert _job_de(conn, dec)["fase"] == "done"
        estado = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert estado == "applied"


@_skip_db
def test_grupo_en_shadow_cero_jobs():
    """Harvest de grupo en shadow: la fila jamas se libera y ningun job
    nace (sellado 6). Regla 9: sin el perimetro shadow, el grupo cosecharia
    en modo practica."""
    with db_f2("orbit_hna_shadow") as conn:
        ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_harvest": 2})
        grupo = _semilla_grupo(conn, mode="shadow")
        for par in grupo["roles"].values():
            _estado(conn, par["camp"])
            _estado(conn, par["ag"])
        origen = grupo["roles"]["category_phrase"]
        _termino_calificado(conn, origen["ag"], term=TERMINO_F2)
        exacta = grupo["roles"]["category_exact"]
        dec = _decision_harvest_grupo(
            conn,
            ids["ciclo_dec"],
            ids["config"],
            origen["ag"],
            grupo_id=grupo["grupo_id"],
            exacta_camp_ext=exacta["camp_ext"],
            exacta_ag_ext=exacta["ag_ext"],
        )
        _encola_fila(conn, dec, origen["ag"], term=TERMINO_F2, modo="shadow")
        handler, vistos = _handler_harvest()
        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )
        assert res.liberadas == 0
        total = conn.execute("SELECT count(*) FROM harvest_job").fetchone()[0]
        assert total == 0, "shadow jamas crea jobs, ni de grupo"
        assert vistos == []


# ---------------------------------------------------------------------------
# R1/F7: procedencia por ID (el ack solo vale si aparece en el readback)
# ---------------------------------------------------------------------------


@_skip_db
def test_posterior_con_otro_id_no_confirma_propio():
    """POST con ack n-ack pero readback con OTRA id (n-otro): no se registra
    propia (el ack no probo esa id en Amazon); la fila se sella CON el ack
    (la id queda durable) y n-otro se ADOPTA (el termino queda bloqueado
    igual, y adoptada jamas se borra). Regla 9 (r2): sin el cruce por id,
    el ack se marcaria propio sin prueba y la reversa podria borrar lo
    ajeno."""
    import httpx as _httpx5

    with db_f2("orbit_hna_ackid") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        _job_en_hermanas(conn, setup, dec, qid)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ags = {r: setup["roles"][r]["ag_ext"] for r in hermanas}
        llamadas = {"list": 0}

        def _ack_distinto(request):
            if request.url.host == "api.amazon.com":
                return _httpx5.Response(
                    200, json={"access_token": "fake-access-ackid", "expires_in": 3600}
                )
            if request.url.path == "/sp/negativeKeywords/list":
                llamadas["list"] += 1
                if llamadas["list"] == 1:
                    return _httpx5.Response(200, json={"negativeKeywords": [], "totalResults": 0})
                otro = {
                    "adGroupId": ags[hermanas[0]],
                    "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                    "keywordId": "n-otro",
                    "keywordText": TERMINO_F2,
                    "matchType": "NEGATIVE_EXACT",
                    "state": "ENABLED",
                }
                return _httpx5.Response(200, json={"negativeKeywords": [otro], "totalResults": 1})
            if request.url.path == "/sp/negativeKeywords":
                return _httpx5.Response(
                    200,
                    json={
                        "negativeKeywords": {
                            "error": [],
                            "success": [{"index": 0, "negativeKeywordId": "n-ack"}],
                        }
                    },
                )
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        _reconcilia(conn, setup, _ack_distinto)
        job = _job_de(conn, dec)
        # n-otro adoptada (no borrable); n-ack durable en el ledger.
        assert job["ext"]["hermanas"][hermanas[0]] == {
            "negative_id": "n-otro",
            "creada": False,
        }, job["ext"]
        fila = conn.execute(
            "SELECT ack, resultado, finished_at IS NOT NULL FROM apply_attempt"
            " WHERE decision_id = %s AND tipo = 'hermana'"
            " AND request_payload->>'adGroupId' = %s",
            (dec, ags[hermanas[0]]),
        ).fetchone()
        assert fila[1] == "fallo:ack_sin_prueba" and fila[2], fila
        assert "n-ack" in json.dumps(fila[0]), "la id del ack queda durable"


@_skip_db
def test_ack_tardio_prueba_propiedad():
    """Ack n-ack sellado sin prueba en el ciclo 1 (posterior vacio); en el
    ciclo 2 n-ack APARECE en el readback: la id duradera del ledger la
    prueba y se registra propia. Regla 9 (r2): sin conservar el ack, la
    prueba tardia seria imposible y lo propio quedaria adoptado."""
    import httpx as _httpx6

    with db_f2("orbit_hna_acktarde") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        _job_en_hermanas(conn, setup, dec, qid)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ags = {r: setup["roles"][r]["ag_ext"] for r in hermanas}
        llamadas = {"list": 0}

        def _tardo(request):
            if request.url.host == "api.amazon.com":
                return _httpx6.Response(
                    200, json={"access_token": "fake-access-acktarde", "expires_in": 3600}
                )
            if request.url.path == "/sp/negativeKeywords/list":
                llamadas["list"] += 1
                if llamadas["list"] <= 2:
                    return _httpx6.Response(200, json={"negativeKeywords": [], "totalResults": 0})
                tarde = {
                    "adGroupId": ags[hermanas[0]],
                    "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                    "keywordId": "n-ack",
                    "keywordText": TERMINO_F2,
                    "matchType": "NEGATIVE_EXACT",
                    "state": "ENABLED",
                }
                return _httpx6.Response(200, json={"negativeKeywords": [tarde], "totalResults": 1})
            if request.url.path == "/sp/negativeKeywords":
                return _httpx6.Response(
                    200,
                    json={
                        "negativeKeywords": {
                            "error": [],
                            "success": [{"index": 0, "negativeKeywordId": "n-ack"}],
                        }
                    },
                )
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        _reconcilia(conn, setup, _tardo)
        job = _job_de(conn, dec)
        # Ciclo 1: previo vacio -> POST ack n-ack -> posterior vacio ->
        # pendiente con la fila sellada CON el ack.
        assert job["ext"]["hermanas"][hermanas[0]] == {"motivo": "red_ambigua"}, job["ext"]
        # Ciclo 2: n-ack aparece -> la id duradera la prueba -> propia.
        _reconcilia(conn, setup, _tardo)
        job2 = _job_de(conn, dec)
        assert job2["ext"]["hermanas"][hermanas[0]] == {
            "negative_id": "n-ack",
            "creada": True,
        }, job2["ext"]


@_skip_db
def test_previo_dos_abiertas_no_atribuye():
    """Dos intentos abiertos y un hallazgo: no se puede atribuir a uno;
    pendiente `red_ambigua` con ambas filas abiertas. Regla 9: atribuir a
    ciegas marcaria propia una id sin procedencia."""
    with db_f2("orbit_hna_2ab") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        _job_en_hermanas(conn, setup, dec, qid)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_doble = setup["roles"][hermanas[0]]["ag_ext"]
        for seq in (3, 4):
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada) VALUES (%s, %s, 'hermana', %s, false)",
                (
                    dec,
                    seq,
                    Json(
                        {
                            "adGroupId": ag_doble,
                            "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                            "keywordText": TERMINO_F2,
                            "matchType": "NEGATIVE_EXACT",
                            "state": "ENABLED",
                        }
                    ),
                ),
            )
        vivo = {
            "adGroupId": ag_doble,
            "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
            "keywordId": "n-dudoso",
            "keywordText": TERMINO_F2,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
        handler, _v = _handler_harvest(negatives=[vivo])
        _reconcilia(conn, setup, handler)
        job = _job_de(conn, dec)
        assert job["ext"]["hermanas"][hermanas[0]] == {"motivo": "red_ambigua"}, job["ext"]
        abiertas = conn.execute(
            "SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'hermana'"
            " AND finished_at IS NULL",
            (dec,),
        ).fetchone()[0]
        assert abiertas == 2, "ninguna se sella sin atribucion"


# ---------------------------------------------------------------------------
# R3: ACK durable, prueba tardia y provisionales en reversa (AC-4/5/6)
# ---------------------------------------------------------------------------


def _flujo_mismatch_al_tope(conn, setup):
    """Cierra un job done con h0 adoptada (n-otro-h0) + ack n-ack-h0 durable
    en ledger, h1 propia y h2 pendiente al tope con alerta. Devuelve
    (dec, roles, ids) para AC-6/AC-7. Ciclo 1 con handler mixto; ciclos 2-3
    con 400 persistente en h2."""
    import httpx as _httpx11

    hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
    ags = {r: setup["roles"][r]["ag_ext"] for r in hermanas}
    ids = {
        "otro": f"n-otro-{ags[hermanas[0]]}",
        "ack0": f"n-ack-{ags[hermanas[0]]}",
        "ack1": f"n-ack-{ags[hermanas[1]]}",
    }
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
    qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
    _libera_cola(conn, qid)
    _job_en_hermanas(conn, setup, dec, qid)
    llamadas = {"list": 0}

    def _mix(request):
        if request.url.host == "api.amazon.com":
            return _httpx11.Response(
                200, json={"access_token": "fake-access-mix", "expires_in": 3600}
            )
        if request.url.path == "/sp/negativeKeywords/list":
            llamadas["list"] += 1
            if llamadas["list"] == 1:
                return _httpx11.Response(200, json={"negativeKeywords": [], "totalResults": 0})
            items = [
                {
                    "adGroupId": ags[hermanas[0]],
                    "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                    "keywordId": ids["otro"],
                    "keywordText": TERMINO_F2,
                    "matchType": "NEGATIVE_EXACT",
                    "state": "ENABLED",
                },
                {
                    "adGroupId": ags[hermanas[1]],
                    "campaignId": setup["roles"][hermanas[1]]["camp_ext"],
                    "keywordId": ids["ack1"],
                    "keywordText": TERMINO_F2,
                    "matchType": "NEGATIVE_EXACT",
                    "state": "ENABLED",
                },
            ]
            return _httpx11.Response(200, json={"negativeKeywords": items, "totalResults": 2})
        if request.url.path == "/sp/negativeKeywords":
            ag = json.loads(request.content)["negativeKeywords"][0]["adGroupId"]
            if ag == ags[hermanas[2]]:
                return _httpx11.Response(400, json={"code": "400"})
            return _httpx11.Response(
                200,
                json={
                    "negativeKeywords": {
                        "error": [],
                        "success": [{"index": 0, "negativeKeywordId": f"n-ack-{ag}"}],
                    }
                },
            )
        raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

    _reconcilia(conn, setup, _mix)
    for _ in range(2):
        handler, _v = _handler_harvest(fallo_post_negative_por_adgroup={ags[hermanas[2]]: 400})
        _reconcilia(conn, setup, handler)
    return dec, hermanas, ags, ids


@_skip_db
def test_ack_distinto_no_desaparece_al_cerrar():
    """AC-6: ack n-ack + n-otro viva -> n-otro adoptada (nunca propia) y
    n-ack durable en ledger; al cerrar al tope, n-ack sigue en el estado
    operativo (pendientes + plan de reversa la incluye como provisional).
    Regla 9 (r3): sin conservar el ack, lo propio no probado desapareceria
    del estado al cerrar."""
    from app.apply_harvest import plan_reversa_harvest

    with db_f2("orbit_hna_ac6") as conn:
        setup = _grupo_listo(conn)
        dec, hermanas, ags, ids = _flujo_mismatch_al_tope(conn, setup)
        job = _job_de(conn, dec)
        assert job["fase"] == "done", job
        # n-otro adoptada, nunca propia.
        assert job["ext"]["hermanas"][hermanas[0]] == {
            "negative_id": ids["otro"],
            "creada": False,
        }, job["ext"]
        assert job["ext"]["hermanas"][hermanas[1]]["creada"] is True
        motivo_h2 = (
            "pt_no_acepta_negative_keyword" if hermanas[2] == "product_targeting" else "http_400"
        )
        assert job["ext"]["hermanas_pendientes"] == {hermanas[2]: motivo_h2}, job["ext"]
        # n-ack durable en ledger aunque el job cerro.
        fila = conn.execute(
            "SELECT ack, resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'hermana'"
            " AND request_payload->>'adGroupId' = %s",
            (dec, ags[hermanas[0]]),
        ).fetchone()
        assert fila[1] == "fallo:ack_sin_prueba", fila
        assert ids["ack0"] in json.dumps(fila[0]), fila
        # Y visible en el plan de reversa como provisional (no desaparece).
        _platform, _term, _dec, pasos = plan_reversa_harvest(conn, job_id=_job_id_de(conn, dec))
        prov = [(p.rol, p.objeto_id) for p in pasos if p.provisional]
        assert prov == [(hermanas[0], ids["ack0"])], [
            (p.rol, p.objeto_id, p.provisional) for p in pasos
        ]
        assert all(p.objeto_id != ids["otro"] for p in pasos), "la adoptada no entra"


def _job_id_de(conn, dec: int) -> int:
    return conn.execute("SELECT id FROM harvest_job WHERE decision_id = %s", (dec,)).fetchone()[0]


@_skip_db
def test_ack_durable_con_posterior_unknown_y_prueba_tardia():
    """AC-4/AC-5: POST aceptado + posterior truncado -> la fila queda
    ABIERTA pero CON el ack durable (la id se observa en ledger antes de
    otro POST); cuando la id aparece en un previo, se registra propia SIN
    nuevo POST. Regla 9 (r3): sin el ack durable, la prueba tardia seria
    imposible y lo propio quedaria pendiente para siempre."""
    import httpx as _httpx10

    with db_f2("orbit_hna_ackdur") as conn:
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
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_F2)
        _libera_cola(conn, qid)
        _job_en_hermanas(conn, setup, dec, qid)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ags = {r: setup["roles"][r]["ag_ext"] for r in hermanas}
        llamadas = {"list": 0}

        def _trunco_tras_post(request):
            if request.url.host == "api.amazon.com":
                return _httpx10.Response(
                    200, json={"access_token": "fake-access-ackdur", "expires_in": 3600}
                )
            if request.url.path == "/sp/negativeKeywords/list":
                llamadas["list"] += 1
                if llamadas["list"] == 1:
                    return _httpx10.Response(200, json={"negativeKeywords": [], "totalResults": 0})
                return _httpx10.Response(
                    200,
                    json={
                        "negativeKeywords": [],
                        "totalResults": 99,
                        "nextToken": f"u{llamadas['list']}",
                    },
                )
            if request.url.path == "/sp/negativeKeywords":
                ag = json.loads(request.content)["negativeKeywords"][0]["adGroupId"]
                kid = f"n-ack-{ag}"
                return _httpx10.Response(
                    200,
                    json={
                        "negativeKeywords": {
                            "error": [],
                            "success": [{"index": 0, "negativeKeywordId": kid}],
                        }
                    },
                )
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        _reconcilia(conn, setup, _trunco_tras_post)
        job = _job_de(conn, dec)
        assert job["fase"] == "hermanas_negadas", job
        for rol in hermanas:
            assert job["ext"]["hermanas"][rol] == {"motivo": "list_truncado"}, (rol, job["ext"])
            fila = conn.execute(
                "SELECT ack, resultado, finished_at IS NULL FROM apply_attempt"
                " WHERE decision_id = %s AND tipo = 'hermana'"
                " AND request_payload->>'adGroupId' = %s",
                (dec, ags[rol]),
            ).fetchone()
            assert fila[1] is None and fila[2], (rol, "abierta pero con ack")
            assert f"n-ack-{ags[rol]}" in json.dumps(fila[0]), (rol, "ACK durable pre-posterior")

        # Ciclo 2: aparece la id del primer POST -> propia sin nuevo POST.
        ag0 = ags[hermanas[0]]
        vivo0 = {
            "adGroupId": ag0,
            "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
            "keywordId": f"n-ack-{ag0}",
            "keywordText": TERMINO_F2,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
        handler2, vistos2 = _handler_harvest(negatives=[vivo0])
        _reconcilia(conn, setup, handler2)
        job2 = _job_de(conn, dec)
        assert job2["ext"]["hermanas"][hermanas[0]] == {
            "negative_id": f"n-ack-{ag0}",
            "creada": True,
        }, job2["ext"]
        assert _posts_hermanas(vistos2, ag0) == [], "prueba tardia sin POST"
        sello = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'hermana'"
            " AND request_payload->>'adGroupId' = %s AND finished_at IS NOT NULL",
            (dec, ag0),
        ).fetchone()
        assert sello is not None and sello[0] == "ok", "la prueba sella la abierta"
