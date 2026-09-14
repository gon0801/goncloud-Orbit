"""Reversa manual del harvest con hermanas (FABRICA 02, A.3, bloque 5).

Logica (`plan_reversa_harvest` / `ejecuta_reversa_harvest` de
`app.apply_harvest`) con MockTransport + CLI (`tools/reversa_harvest.py`:
orden, candados y reanudacion). Reutiliza `db_f2`, `_semilla_grupo` y los
helpers de siembra de `test_fabrica_f2_hermanas` (no duplicados).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from test_fabrica_f2 import db_f2
from test_fabrica_f2_hermanas import (
    ROLES_DISCOVERY,
    TERMINO_F2,
    _decision_harvest_grupo,
    _encola_fila,
    _flujo_mismatch_al_tope,
    _grupo_listo,
    _job_en_hermanas,
    _libera_cola,
)
from test_schema import _postgres_obligatorio_ausente

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _handler_reversa(vivos, *, no_archiva=(), fallo_delete=(), muta_identidad=()):
    """MockTransport minimo de reversa: LIST con los vivos (menos lo
    archivado) y deletes que archivan salvo `no_archiva` (sigue vivo) o
    `fallo_delete` (400). `muta_identidad` deja al objeto ENABLED pero con
    otro termino (el objeto muto bajo los pies: el readback por ID debe
    seguir viendolo vivo)."""
    store = [dict(x) for x in vivos]
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "fake-access-rev", "expires_in": 3600})
        vistos.append(request)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/sp/negativeKeywords/list":
            dentro = set((body.get("adGroupIdFilter") or {}).get("include", []))
            filtrados = (
                [x for x in store if str(x.get("adGroupId")) in dentro] if dentro else list(store)
            )
            return httpx.Response(
                200, json={"negativeKeywords": filtrados, "totalResults": len(filtrados)}
            )
        if path == "/sp/keywords/list":
            kws = [x for x in store if x.get("_clase") == "keyword"]
            return httpx.Response(200, json={"keywords": kws, "totalResults": len(kws)})
        if path == "/sp/keywords/delete":
            kid = body["keywordIdFilter"]["include"][0]
            if kid in fallo_delete:
                return httpx.Response(400, json={"code": "400"})
            for item in store:
                if str(item.get("keywordId")) == str(kid):
                    if kid in muta_identidad:
                        item["keywordText"] = "OTRO TERMINO POST-DELETE"
                    elif kid not in no_archiva:
                        item["state"] = "ARCHIVED"
            return httpx.Response(
                200, json={"keywords": {"error": [], "success": [{"keywordId": kid}]}}
            )
        if path == "/sp/negativeKeywords/delete":
            kid = body["negativeKeywordIdFilter"]["include"][0]
            if kid in fallo_delete:
                return httpx.Response(400, json={"code": "400"})
            for item in store:
                if str(item.get("keywordId")) == str(kid):
                    if kid in muta_identidad:
                        item["keywordText"] = "OTRO TERMINO POST-DELETE"
                    elif kid not in no_archiva:
                        item["state"] = "ARCHIVED"
            return httpx.Response(
                200,
                json={"negativeKeywords": {"error": [], "success": [{"negativeKeywordId": kid}]}},
            )
        raise AssertionError(f"request inesperado: {request.method} {path}")

    return handler, vistos


def _cliente_reversa(handler):
    from app.ads.config import AdsCredentials
    from app.ads.write import AdsWriteClient

    return AdsWriteClient(
        AdsCredentials(
            client_id="fake-id-rev-1",
            client_secret="fake-secret-rev-1",
            refresh_token="fake-refresh-rev-1",
        ),
        platform="amazon_us",
        profile_id=404040,
        modo_confirmado="live",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _job_done_mixto(conn, setup, *, term=TERMINO_F2):
    """Job done con mezcla propia/adoptada: h1 y h3 propias, h2 adoptada.
    Camina las fases por UPDATE (trigger) con external_ids finales."""
    from psycopg.types.json import Json

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
    _libera_cola(conn, qid)
    jid = _job_en_hermanas(conn, setup, dec, qid)
    roles = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
    ext = {
        "keyword_id": "k-9",
        "negative_id": "n-0",
        "hermanas_objetivo": {
            r: {
                "campaign_id": setup["roles"][r]["camp_ext"],
                "ad_group_id": setup["roles"][r]["ag_ext"],
            }
            for r in roles
        },
        "hermanas": {
            roles[0]: {"negative_id": "n-1", "creada": True},
            roles[1]: {"negative_id": "n-2", "creada": False},
            roles[2]: {"negative_id": "n-3", "creada": True},
        },
        "hermanas_ciclos": 1,
        "hermanas_pendientes": {},
    }
    conn.execute(
        "UPDATE harvest_job SET fase = 'done', external_ids = %s WHERE id = %s", (Json(ext), jid)
    )
    return jid, dec, roles


def _vivos_reversa(setup, roles):
    vivos = [
        {
            "_clase": "keyword",
            "adGroupId": setup["roles"]["category_exact"]["ag_ext"],
            "campaignId": setup["roles"]["category_exact"]["camp_ext"],
            "keywordId": "k-9",
            "keywordText": TERMINO_F2,
            "matchType": "EXACT",
            "state": "ENABLED",
        }
    ]
    for i, rol in enumerate(roles):
        vivos.append(
            {
                "adGroupId": setup["roles"][rol]["ag_ext"],
                "campaignId": setup["roles"][rol]["camp_ext"],
                "keywordId": f"n-{i + 1}",
                "keywordText": TERMINO_F2,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
        )
    vivos.append(
        {
            "adGroupId": setup["origen"]["ag_ext"],
            "campaignId": setup["origen"]["camp_ext"],
            "keywordId": "n-0",
            "keywordText": TERMINO_F2,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
    )
    return vivos


# ---------------------------------------------------------------------------
# Plan: orden canonico y mezcla propia/adoptada
# ---------------------------------------------------------------------------


@_skip_db
def test_plan_orden_canonical_excluye_adoptadas():
    """El plan es [keyword, hermanas propias en orden ROLES_DISCOVERY,
    origen]; la adoptada no entra. Regla 9: borrar adoptadas destruiria
    negativos ajenos."""
    from app.apply_harvest import plan_reversa_harvest

    with db_f2("orbit_rev_plan") as conn:
        setup = _grupo_listo(conn)
        jid, dec, roles = _job_done_mixto(conn, setup)
        platform, term, dec2, pasos = plan_reversa_harvest(conn, jid)
        assert (platform, term, dec2) == ("amazon_us", TERMINO_F2, dec)
        assert [(p.clase, p.rol, p.objeto_id) for p in pasos] == [
            ("keyword", None, "k-9"),
            ("negative", roles[0], "n-1"),
            ("negative", roles[2], "n-3"),
            ("negative", None, "n-0"),
        ], "adoptada fuera, propias en orden canonico, origen ultimo"


@_skip_db
def test_plan_precondiciones_fallan_cerrado():
    """Job no done, resumen sin verify_ok, cola no applied o sin ids ->
    ValueError (el tool lo vuelve Abortar). Regla 9: sin precondiciones,
    la reversa correria sobre un harvest a medio aplicar."""

    from app.apply_harvest import plan_reversa_harvest

    with db_f2("orbit_rev_pre") as conn:
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
        jid = _job_en_hermanas(conn, setup, dec, qid)
        with pytest.raises(ValueError, match="solo acepta done"):
            plan_reversa_harvest(conn, jid)
        # Sin verify_ok: lo borro y sigue fallando por otra precondicion.
        conn.execute("DELETE FROM decision_application WHERE decision_id = %s", (dec,))
        conn.execute("UPDATE harvest_job SET fase = 'done' WHERE id = %s", (jid,))
        with pytest.raises(ValueError, match="sin verify_ok"):
            plan_reversa_harvest(conn, jid)


# ---------------------------------------------------------------------------
# Ejecucion: orden, readback, stop y reanudacion
# ---------------------------------------------------------------------------


@_skip_db
def test_ejecuta_orden_filas_y_readback():
    """Ejecucion completa: deletes en orden [kw, h1, h3, origen], una fila
    tipo='reversa' exenta por borrado y readback entre deletes. Regla 9:
    orden invertido dejaria el termino compitiendo con la keyword muerta."""
    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_run") as conn:
        setup = _grupo_listo(conn)
        jid, dec, roles = _job_done_mixto(conn, setup)
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, jid)
        handler, vistos = _handler_reversa(_vivos_reversa(setup, roles))
        ok, detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(handler), dec, term, pasos)
        assert (ok, detalle) == (True, "reversa: ok")
        deletes = [r for r in vistos if r.url.path.endswith("/delete")]
        assert [r.url.path for r in deletes] == [
            "/sp/keywords/delete",
            "/sp/negativeKeywords/delete",
            "/sp/negativeKeywords/delete",
            "/sp/negativeKeywords/delete",
        ]
        cuerpos = [json.loads(r.content) for r in deletes]
        assert cuerpos[0] == {"keywordIdFilter": {"include": ["k-9"]}}
        assert [c["negativeKeywordIdFilter"]["include"] for c in cuerpos[1:]] == [
            ["n-1"],
            ["n-3"],
            ["n-0"],
        ]
        filas = conn.execute(
            "SELECT tipo, quota_cobrada, resultado FROM apply_attempt"
            " WHERE decision_id = %s AND tipo = 'reversa' ORDER BY id",
            (dec,),
        ).fetchall()
        assert filas == [("reversa", False, "ok")] * 4
        # Readback entre deletes: un LIST por clase tras cada delete.
        listas = [r for r in vistos if r.url.path.endswith("/list")]
        assert len(listas) == 4, "un readback por delete antes de seguir"


@_skip_db
def test_readback_vivo_detiene_antes_de_seguir():
    """Si tras el delete la hermana sigue viva (no archivo), stop: lo
    siguiente no se toca. Regla 9: sin el readback, la reversa seguiria
    borrando a ciegas."""
    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_vivo") as conn:
        setup = _grupo_listo(conn)
        jid, dec, roles = _job_done_mixto(conn, setup)
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, jid)
        handler, vistos = _handler_reversa(_vivos_reversa(setup, roles), no_archiva=("n-1",))
        ok, detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(handler), dec, term, pasos)
        assert ok is False and "sigue vivo" in detalle
        deletes = [r for r in vistos if r.url.path.endswith("/delete")]
        assert len(deletes) == 2, "keyword + h1; h3 y origen intactos"
        # F1 (r1): la fila de h1 NO quedo ok (el objeto sigue vivo): la
        # reanudacion no la salta y el reintento si la borra.
        fila = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'"
            " AND request_payload->'negativeKeywordIdFilter'->'include' @> '[\"n-1\"]'::jsonb",
            (dec,),
        ).fetchone()
        assert fila[0] == "fallo:sigue_vivo"
        handler2, _v2b = _handler_reversa(_vivos_reversa(setup, roles))
        ok2, _d2 = ejecuta_reversa_harvest(conn, _cliente_reversa(handler2), dec, term, pasos)
        assert ok2 is True, "reintentada, la reversa completa"


@_skip_db
def test_keyword_viva_con_identidad_discordante_detiene_reversa():
    """Tras el delete la keyword sigue ENABLED pero con OTRO termino: el
    readback la ve viva POR ID (sin filtrar por identidad) -> sella
    fallo:sigue_vivo y stop; jamas ok. Regla 9 (r4): con el readback
    filtrado por identidad, la id mutada desaparecia de la lista, se
    sellaba ok y la reversa seguia borrando a ciegas."""
    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_kwdisc") as conn:
        setup = _grupo_listo(conn)
        jid, dec, roles = _job_done_mixto(conn, setup)
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, jid)
        handler, vistos = _handler_reversa(_vivos_reversa(setup, roles), muta_identidad=("k-9",))
        ok, detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(handler), dec, term, pasos)
        assert ok is False and "sigue vivo" in detalle, detalle
        deletes = [r for r in vistos if r.url.path.endswith("/delete")]
        assert [r.url.path for r in deletes] == ["/sp/keywords/delete"], (
            "solo el delete de la keyword salio; hermanas y origen intactos"
        )
        fila = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'"
            " AND request_payload->'keywordIdFilter'->'include' @> '[\"k-9\"]'::jsonb",
            (dec,),
        ).fetchone()
        assert fila[0] == "fallo:sigue_vivo"


@_skip_db
def test_negative_viva_con_identidad_discordante_detiene_reversa():
    """Mismo bloqueante en negative: la hermana h1 queda ENABLED con otro
    termino tras su delete. El readback por ID la sigue viendo viva ->
    fallo:sigue_vivo y stop antes de h3 y origen. Regla 9 (r4)."""
    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_negdisc") as conn:
        setup = _grupo_listo(conn)
        jid, dec, roles = _job_done_mixto(conn, setup)
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, jid)
        handler, vistos = _handler_reversa(_vivos_reversa(setup, roles), muta_identidad=("n-1",))
        ok, detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(handler), dec, term, pasos)
        assert ok is False and "sigue vivo" in detalle, detalle
        deletes = [r for r in vistos if r.url.path.endswith("/delete")]
        assert len(deletes) == 2, "keyword + h1; h3 y origen intactos"
        fila = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'"
            " AND request_payload->'negativeKeywordIdFilter'->'include' @> '[\"n-1\"]'::jsonb",
            (dec,),
        ).fetchone()
        assert fila[0] == "fallo:sigue_vivo"


@_skip_db
def test_fallo_en_hermana_detiene_y_origen_intacto():
    """Delete rechazado (400) en h1: stop inmediato, origen sin tocar.
    Regla 9: sin stop, la reversa dejaria el termino compitiendo en
    origen con la keyword muerta."""
    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_stop") as conn:
        setup = _grupo_listo(conn)
        jid, dec, roles = _job_done_mixto(conn, setup)
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, jid)
        handler, vistos = _handler_reversa(_vivos_reversa(setup, roles), fallo_delete=("n-1",))
        ok, _detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(handler), dec, term, pasos)
        assert ok is False
        cuerpos = [json.loads(r.content) for r in vistos if r.url.path.endswith("/delete")]
        assert cuerpos[0] == {"keywordIdFilter": {"include": ["k-9"]}}
        assert len(cuerpos) == 2, "keyword + h1 fallida; nada mas"
        assert all("n-0" not in json.dumps(c) for c in cuerpos), "origen intacto"


@_skip_db
def test_crash_despues_de_keyword_reanuda_sin_repetir():
    """Crash tras la keyword (h1 falla, stop): al reejecutar salta la
    keyword confirmada y completa el resto; la keyword tiene UNA sola fila
    reversa. Regla 9: sin salto por ledger, el reintento repetiria deletes
    confirmados."""
    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_resume") as conn:
        setup = _grupo_listo(conn)
        jid, dec, roles = _job_done_mixto(conn, setup)
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, jid)
        handler1, _v1 = _handler_reversa(_vivos_reversa(setup, roles), fallo_delete=("n-1",))
        ok, _d = ejecuta_reversa_harvest(conn, _cliente_reversa(handler1), dec, term, pasos)
        assert ok is False
        # El almacen real conserva lo archivado: la reanudacion lo ve.
        # k-9 ya cayo (no vuelve al LIST); n-1 sigue vivo (su delete fallo).
        vivos2 = [x for x in _vivos_reversa(setup, roles) if x["keywordId"] != "k-9"]
        handler2, vistos2 = _handler_reversa(vivos2)
        ok2, detalle2 = ejecuta_reversa_harvest(conn, _cliente_reversa(handler2), dec, term, pasos)
        assert (ok2, detalle2) == (True, "reversa: ok")
        kw_deletes = [
            r for r in vistos2 if r.url.path.endswith("/delete") and "k-9" in r.content.decode()
        ]
        assert kw_deletes == [], "keyword confirmada: se salta, no se repite"
        n_kw = conn.execute(
            "SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'"
            " AND request_payload->'keywordIdFilter'->'include' @> '[\"k-9\"]'::jsonb",
            (dec,),
        ).fetchone()[0]
        assert n_kw == 1


@_skip_db
def test_reversa_confirmada_distingue_clase():
    """`_reversa_confirmada` cruza por clase (r4): una keyword y una
    negativa que comparten id no se confirman entre si. Regla 9: con el OR
    por id, confirmar la keyword saltaria el delete pendiente de la
    negativa con la misma id."""
    from psycopg.types.json import Json as _Json

    from app.apply_harvest import _reversa_confirmada

    with db_f2("orbit_rev_clase") as conn:
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
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, ack, resultado, finished_at) VALUES (%s, 1, 'reversa',"
            " %s, false, '{}'::jsonb, 'ok', now())",
            (dec, _Json({"keywordIdFilter": {"include": ["X-1"]}})),
        )
        assert _reversa_confirmada(conn, dec, "keyword", "X-1") is True
        assert _reversa_confirmada(conn, dec, "negative", "X-1") is False, (
            "la negativa X-1 no esta confirmada aunque la keyword X-1 si"
        )


@_skip_db
def test_provisional_discordante_detiene_toda_la_reversa():
    """ID provisional viva con OTRA identidad (termino distinto): stop de
    TODA la reversa antes de tocarla, origen intacto. Regla 9 (r4): sin
    distinguirla de 'ausente', se omitiria y se seguiria hasta borrar el
    origen con el objeto mutado bajo los pies."""
    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_discord") as conn:
        setup = _grupo_listo(conn)
        dec, hermanas, ags, ids = _flujo_mismatch_al_tope(conn, setup)
        job_id = conn.execute(
            "SELECT id FROM harvest_job WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, job_id)
        vivos = [
            {
                "_clase": "keyword",
                "adGroupId": setup["roles"]["category_exact"]["ag_ext"],
                "campaignId": setup["roles"]["category_exact"]["camp_ext"],
                "keywordId": "k-1",
                "keywordText": TERMINO_F2,
                "matchType": "EXACT",
                "state": "ENABLED",
            },
            {
                "adGroupId": ags[hermanas[0]],
                "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                "keywordId": ids["ack0"],
                "keywordText": "OTRO TERMINO",
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            },
        ]
        handler, vistos = _handler_reversa(vivos)
        ok, detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(handler), dec, term, pasos)
        assert ok is False and "discordante" in detalle, detalle
        deletes = [r for r in vistos if r.url.path.endswith("/delete")]
        assert len(deletes) == 1, "solo la keyword salio; la provisional mutada detiene"
        assert "n-0" not in json.dumps([json.loads(r.content) for r in deletes]), "origen intacto"


@_skip_db
def test_readback_keyword_truncado_detiene_reversa():
    """Keywords LIST con `nextToken` vivo al tope durante la reversa: el
    delete ya salio pero sin readback concluyente no se confirma ni se
    sigue; la fila queda abierta para reintentar. Regla 9 (r1/F2): con
    lectura truncada, concluir ausencia es borrar a ciegas."""
    import httpx as _httpx4

    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_kwtrunc") as conn:
        setup = _grupo_listo(conn)
        jid, dec, roles = _job_done_mixto(conn, setup)
        vistos: list = []

        def _kw_trunco(request):
            if request.url.host == "api.amazon.com":
                return _httpx4.Response(
                    200, json={"access_token": "fake-access-kwtr", "expires_in": 3600}
                )
            vistos.append(request)
            if request.url.path == "/sp/keywords/delete":
                return _httpx4.Response(
                    200, json={"keywords": {"error": [], "success": [{"keywordId": "k-9"}]}}
                )
            if request.url.path == "/sp/keywords/list":
                return _httpx4.Response(
                    200, json={"keywords": [], "totalResults": 99, "nextToken": "t1"}
                )
            if request.url.path == "/sp/negativeKeywords/list":
                return _httpx4.Response(200, json={"negativeKeywords": [], "totalResults": 0})
            raise AssertionError(f"request inesperado: {request.method} {request.url.path}")

        _platform, term, _dec, pasos = plan_reversa_harvest(conn, jid)
        ok, _detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(_kw_trunco), dec, term, pasos)
        assert ok is False
        deletes = [r for r in vistos if r.url.path.endswith("/delete")]
        assert len(deletes) == 1, "el delete salio pero sin confirmacion no se sigue"
        fila = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'",
            (dec,),
        ).fetchone()
        assert fila[0] is None, "sin readback concluyente la fila no se sella ok"


@_skip_db
def test_reversa_resuelve_ack_potencial_y_no_borra_adoptada():
    """AC-7: job done con n-ack-h0 no resuelta + n-otro-h0 adoptada. La
    reversa borra n-ack-h0 (viva y coincidente, con ledger/readback) y
    JAMAS toca n-otro-h0 (adoptada). Si el pre-readback es unknown, se
    detiene antes del siguiente delete. Regla 9 (r3): sin resolver el ACK,
    lo propio no probado quedaria sin reversa; tocando la adoptada se
    destruiria lo ajeno."""
    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_prov") as conn:
        setup = _grupo_listo(conn)
        dec, hermanas, ags, ids = _flujo_mismatch_al_tope(conn, setup)
        job_id = conn.execute(
            "SELECT id FROM harvest_job WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, job_id)
        prov = [(p.rol, p.objeto_id) for p in pasos if p.provisional]
        assert prov == [(hermanas[0], ids["ack0"])], [(p.rol, p.objeto_id) for p in pasos]
        id_h1 = f"n-ack-{ags[hermanas[1]]}"
        vivos = [
            {
                "_clase": "keyword",
                "adGroupId": setup["roles"]["category_exact"]["ag_ext"],
                "campaignId": setup["roles"]["category_exact"]["camp_ext"],
                "keywordId": "k-1",
                "keywordText": TERMINO_F2,
                "matchType": "EXACT",
                "state": "ENABLED",
            },
            {
                "adGroupId": ags[hermanas[0]],
                "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                "keywordId": ids["ack0"],
                "keywordText": TERMINO_F2,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            },
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
                "keywordId": id_h1,
                "keywordText": TERMINO_F2,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            },
            {
                "adGroupId": setup["origen"]["ag_ext"],
                "campaignId": setup["origen"]["camp_ext"],
                "keywordId": "n-0",
                "keywordText": TERMINO_F2,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            },
        ]
        handler, vistos = _handler_reversa(vivos)
        ok, detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(handler), dec, term, pasos)
        assert (ok, detalle) == (True, "reversa: ok")
        borrados = [json.loads(r.content) for r in vistos if r.url.path.endswith("/delete")]
        ids_borrados = [
            (b.get("keywordIdFilter") or b.get("negativeKeywordIdFilter"))["include"][0]
            for b in borrados
        ]
        assert ids_borrados == ["k-1", ids["ack0"], id_h1, "n-0"], (
            "kw, provisional viva y propia en orden canonico por rol, origen ultimo;"
            " la adoptada jamas"
        )
        assert ids["otro"] not in ids_borrados, "la adoptada jamas se toca"


# ---------------------------------------------------------------------------
# CLI: candados y dry-run
# ---------------------------------------------------------------------------


def _carga_tool():
    ruta = Path(__file__).resolve().parent.parent / "tools" / "reversa_harvest.py"
    spec = importlib.util.spec_from_file_location("reversa_harvest", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tool_no_importa_ni_construye_cliente_escritura():
    """El tool llega al cliente solo via `apply._cliente_reversa`: sin
    import de `app.ads.write`, sin `AdsWriteClient` y sin DSN admin.
    Regla 9: un segundo dueno del cliente mutador seria una segunda
    superficie de escritura a Amazon."""
    import ast as _ast

    fuente = (Path(__file__).resolve().parent.parent / "tools" / "reversa_harvest.py").read_text(
        encoding="utf-8"
    )
    arbol = _ast.parse(fuente)
    importados: set[str] = set()
    nombres: set[str] = set()
    for nodo in _ast.walk(arbol):
        if isinstance(nodo, _ast.Import):
            importados.update(a.name for a in nodo.names)
        elif isinstance(nodo, _ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)
            nombres.update(a.name for a in nodo.names)
        elif isinstance(nodo, _ast.Name):
            nombres.add(nodo.id)
        elif isinstance(nodo, _ast.Attribute):
            nombres.add(nodo.attr)
    assert "app.ads.write" not in importados
    # "AdsWriteClient" puede mencionarse en docstrings/comentarios, pero
    # jamas como codigo (import, nombre o atributo).
    assert "AdsWriteClient" not in nombres, "el tool referencia al cliente de escritura"
    assert "ORBIT_DSN_ADMIN" not in fuente
    assert "_cliente_reversa" in fuente and "ORBIT_DSN_DECIDE" in fuente


def test_cli_sin_job_falla():
    mod = _carga_tool()
    with pytest.raises(SystemExit) as exc:
        mod.main([])
    assert exc.value.code == 2, "argparse exige --job"


@_skip_db
def test_cli_dry_run_imprime_huella_sin_http(monkeypatch, capsys):
    mod = _carga_tool()
    with db_f2("orbit_rev_cli") as conn:
        setup = _grupo_listo(conn)
        jid, _dec, _roles = _job_done_mixto(conn, setup)
        monkeypatch.setenv(
            "ORBIT_DSN_DECIDE", f"postgresql://orbit:orbit@localhost:5432/{conn.info.dbname}"
        )
        rc = mod.main(["--job", str(jid)])
        assert rc == 0
        salida = capsys.readouterr().out
        assert "huella:" in salida and "dry-run" in salida
        assert "[pendiente] keyword" in salida and "[pendiente] negative" in salida


@_skip_db
def test_cli_mutacion_exige_ceremonia_completa(monkeypatch, capsys):
    mod = _carga_tool()
    with db_f2("orbit_rev_cer") as conn:
        setup = _grupo_listo(conn)
        jid, _dec, _roles = _job_done_mixto(conn, setup)
        monkeypatch.setenv(
            "ORBIT_DSN_DECIDE", f"postgresql://orbit:orbit@localhost:5432/{conn.info.dbname}"
        )
        with pytest.raises(Exception, match="--esperado"):
            mod.main(["--job", str(jid), "--acepto-mutacion-real", "--go", "x"])
        with pytest.raises(Exception, match="--go"):
            mod.main(["--job", str(jid), "--acepto-mutacion-real", "--esperado", "4"])
        with pytest.raises(Exception, match="--huella"):
            mod.main(["--job", str(jid), "--acepto-mutacion-real", "--esperado", "4", "--go", "x"])
        capsys.readouterr()
        rc = mod.main(["--job", str(jid)])
        assert rc == 0
        salida = capsys.readouterr().out
        linea = [ln for ln in salida.splitlines() if ln.startswith("pendientes:")][0]
        huella = linea.split("huella: ")[1]
        with pytest.raises(Exception, match="huella"):
            mod.main(
                [
                    "--job",
                    str(jid),
                    "--acepto-mutacion-real",
                    "--esperado",
                    "4",
                    "--go",
                    "x",
                    "--huella",
                    "muerta",
                ]
            )
        with pytest.raises(Exception, match="--go"):
            mod.main(
                [
                    "--job",
                    str(jid),
                    "--acepto-mutacion-real",
                    "--esperado",
                    "4",
                    "--go",
                    "",
                    "--huella",
                    huella,
                ]
            )


@_skip_db
def test_cli_readback_ambiguo_aborta_limpio(monkeypatch, capsys):
    """LIST ambiguo a mitad de reversa (r4): Abortar limpio con mensaje de
    reanudacion, no traceback. El delete ya enviado queda con fila abierta
    (reanudable con el mismo plan). Regla 9: sin el catch, el 5xx/red del
    readback moria con traceback a mitad de reversa."""
    from types import SimpleNamespace as _NS

    from app import apply as _apply_mod
    from app.ads.client import AdsApiError as _AdsApiError3

    mod = _carga_tool()
    with db_f2("orbit_rev_abort") as conn:
        setup = _grupo_listo(conn)
        dec, _hermanas, _ags, _ids = _flujo_mismatch_al_tope(conn, setup)
        job_id = conn.execute(
            "SELECT id FROM harvest_job WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        monkeypatch.setenv(
            "ORBIT_DSN_DECIDE", f"postgresql://orbit:orbit@localhost:5432/{conn.info.dbname}"
        )
        capsys.readouterr()
        assert mod.main(["--job", str(job_id)]) == 0
        salida = capsys.readouterr().out
        linea = [ln for ln in salida.splitlines() if ln.startswith("pendientes:")][0]
        n_pend = linea.split()[1]
        huella = linea.split("huella: ")[1]

        def _borrar_kw(_kid):
            return httpx.Response(
                200, json={"keywords": {"error": [], "success": [{"keywordId": "k-1"}]}}
            )

        def _list_roto(_path, _body):
            raise _AdsApiError3("status=503 sin retry: POST https://fake/list")

        stub = _NS(borrar_keyword=_borrar_kw, borrar_negative=_borrar_kw, list_sellado=_list_roto)
        monkeypatch.setattr(_apply_mod, "_cliente_reversa", lambda platform, transport=None: stub)
        with pytest.raises(mod.Abortar, match="ambigua.*reanudar"):
            mod.main(
                [
                    "--job",
                    str(job_id),
                    "--acepto-mutacion-real",
                    "--esperado",
                    n_pend,
                    "--go",
                    "go-dueno",
                    "--huella",
                    huella,
                ]
            )
        fila = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'"
            " AND request_payload->'keywordIdFilter'->'include' @> '[\"k-1\"]'::jsonb",
            (dec,),
        ).fetchone()
        assert fila[0] is None, "el delete enviado queda abierto y reanudable"


@_skip_db
def test_cli_cliente_sin_credenciales_aborta_limpio(monkeypatch, capsys):
    """Falta de credenciales (AdsConfigError) o fallo LWA (AdsAuthError, un
    AdsClientError) al construir el cliente: Abortar limpio, no traceback.
    Regla 9 (r4): el catch viejo solo cubria AdsApiError/SinPerfilReversa."""
    from app import apply as _apply_mod2
    from app.ads.client import AdsAuthError as _AdsAuthError
    from app.ads.config import AdsConfigError as _AdsConfigError

    mod = _carga_tool()
    with db_f2("orbit_rev_creds") as conn:
        setup = _grupo_listo(conn)
        jid, _dec, _roles = _job_done_mixto(conn, setup)
        monkeypatch.setenv(
            "ORBIT_DSN_DECIDE", f"postgresql://orbit:orbit@localhost:5432/{conn.info.dbname}"
        )
        capsys.readouterr()
        assert mod.main(["--job", str(jid)]) == 0
        salida = capsys.readouterr().out
        linea = [ln for ln in salida.splitlines() if ln.startswith("pendientes:")][0]
        n_pend = linea.split()[1]
        huella = linea.split("huella: ")[1]
        argv_real = [
            "--job",
            str(jid),
            "--acepto-mutacion-real",
            "--esperado",
            n_pend,
            "--go",
            "go-dueno",
            "--huella",
            huella,
        ]
        for exc in (
            _AdsConfigError("falta client_id"),
            _AdsAuthError("token LWA rechazado"),
        ):

            def _rompe(platform, transport=None, _exc=exc):
                raise _exc

            monkeypatch.setattr(_apply_mod2, "_cliente_reversa", _rompe)
            with pytest.raises(mod.Abortar, match="sin cliente de reversa"):
                mod.main(argv_real)


def test_cli_fallo_conexion_aborta_limpio(monkeypatch):
    """OrbitDbError al abrir Postgres se convierte en Abortar para que el
    entrypoint imprima ABORTAR y salga 2, sin traceback. Regla 9: sin la
    captura, main propaga OrbitDbError fuera del contrato del CLI."""
    from app.db import OrbitDbError

    mod = _carga_tool()
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://orbit:secreto@localhost/orbit")

    def _sin_db(_dsn):
        raise OrbitDbError("no se pudo conectar a la base de datos: postgresql://***@localhost")

    monkeypatch.setattr(mod, "connect", _sin_db)
    with pytest.raises(mod.Abortar, match="no se pudo conectar") as excinfo:
        mod.main(["--job", "1"])
    assert excinfo.value.__suppress_context__ is True


@_skip_db
def test_list_incompleto_en_provisional_cero_delete():
    """Keyword ya confirmada; el LIST de la provisional llega sin
    keywordId: unknown, cero DELETE, no se concluye que el ID desaparecio.
    Regla 9: la pagina malformada se leia como vacia-de-ese-id y se
    omitia o se sellaba ok."""
    from psycopg.types.json import Json as _Json

    from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest

    with db_f2("orbit_rev_nokid") as conn:
        setup = _grupo_listo(conn)
        dec, hermanas, ags, ids = _flujo_mismatch_al_tope(conn, setup)
        job_id = conn.execute(
            "SELECT id FROM harvest_job WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
            " quota_cobrada, ack, resultado, finished_at) VALUES (%s, 1, 'reversa',"
            " %s, false, '{}'::jsonb, 'ok', now())",
            (dec, _Json({"keywordIdFilter": {"include": ["k-1"]}})),
        )
        _platform, term, _dec, pasos = plan_reversa_harvest(conn, job_id)
        vivos = [
            {
                "adGroupId": ags[hermanas[0]],
                "campaignId": setup["roles"][hermanas[0]]["camp_ext"],
                "keywordText": TERMINO_F2,
                "matchType": "NEGATIVE_EXACT",
                "state": "ENABLED",
            }
        ]
        handler, vistos = _handler_reversa(vivos)
        ok, detalle = ejecuta_reversa_harvest(conn, _cliente_reversa(handler), dec, term, pasos)
        assert ok is False and "no concluyente" in detalle, detalle
        deletes = [r for r in vistos if r.url.path.endswith("/delete")]
        assert deletes == [], "LIST incompleto: cero DELETE"


@_skip_db
def test_cli_auth_durante_delete_aborta_limpio(monkeypatch, capsys):
    """AdsAuthError en el primer DELETE (cliente ya construido): Abortar
    limpio, no traceback. Regla 9 (r4): el segundo catch solo cubria
    AdsApiError; AdsAuthError (hermano, no hijo) escapaba."""
    from types import SimpleNamespace as _NS

    from app import apply as _apply_mod3
    from app.ads.client import AdsAuthError as _AdsAuthError2

    mod = _carga_tool()
    with db_f2("orbit_rev_authdel") as conn:
        setup = _grupo_listo(conn)
        jid, dec, _roles = _job_done_mixto(conn, setup)
        monkeypatch.setenv(
            "ORBIT_DSN_DECIDE", f"postgresql://orbit:orbit@localhost:5432/{conn.info.dbname}"
        )
        capsys.readouterr()
        assert mod.main(["--job", str(jid)]) == 0
        salida = capsys.readouterr().out
        linea = [ln for ln in salida.splitlines() if ln.startswith("pendientes:")][0]
        n_pend = linea.split()[1]
        huella = linea.split("huella: ")[1]

        def _auth_en_delete(_kid):
            raise _AdsAuthError2("token LWA rechazado a mitad de reversa")

        stub = _NS(
            borrar_keyword=_auth_en_delete,
            borrar_negative=_auth_en_delete,
            list_sellado=lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("no deberia listar si el delete ya fallo")
            ),
        )
        monkeypatch.setattr(_apply_mod3, "_cliente_reversa", lambda platform, transport=None: stub)
        with pytest.raises(mod.Abortar, match="ambigua.*reanudar"):
            mod.main(
                [
                    "--job",
                    str(jid),
                    "--acepto-mutacion-real",
                    "--esperado",
                    n_pend,
                    "--go",
                    "go-dueno",
                    "--huella",
                    huella,
                ]
            )
        fila = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'reversa'",
            (dec,),
        ).fetchone()
        assert fila is None or fila[0] is None, "el delete no confirmado queda abierto"
