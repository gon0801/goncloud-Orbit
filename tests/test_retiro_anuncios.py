"""Retiro de product ads de bolsas viejas y su reposicion (BIDS 02, I.4). Con base."""

from __future__ import annotations

import json

import httpx
import pytest
from test_fabrica_migracion import (
    _dsn_propia,
    _entidad,
    _grupo,
    _lote,
    _producto,
    db_fabrica,
)
from test_migracion_0065 import SQL64, SQL65
from test_migracion_0066 import SQL66
from test_migracion_0068 import SQL68
from test_migracion_0069 import SQL69
from test_schema import _postgres_obligatorio_ausente

from app import retiro_anuncios as ra
from tests.test_ads_write import _token_response, make_write_client

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _db_i4(prefijo):
    class _Db:
        def __enter__(self):
            self._ctx = db_fabrica(prefijo)
            conn = self._ctx.__enter__()
            conn.execute(SQL64)
            conn.execute(SQL65)
            conn.execute(SQL66)
            conn.execute(SQL68)
            conn.execute(SQL69)
            return conn

        def __exit__(self, *exc):
            return self._ctx.__exit__(*exc)

    return _Db()


def _bolsa(
    conn, lid, external, *, camp="c-vieja", ag="ag-vieja", estado="ENABLED", platform="amazon_mx"
):
    """Campana + ad group + product ad con listing y estado; devuelve
    (camp_id, ag_id, pa_id)."""
    camp_id = _entidad(conn, platform, "campaign", camp)
    ag_id = _entidad(conn, platform, "ad_group", ag, parent=camp_id)
    pa_id = _entidad(conn, platform, "product_ad", external, parent=ag_id, listing_id=lid)
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, now())",
        (pa_id, estado),
    )
    return camp_id, ag_id, pa_id


def _escritor(
    filas: dict[str, dict],
    *,
    lists_que_fallan=frozenset(),
    stale_lists=frozenset(),
    rechaza_put=frozenset(),
    ocultos=None,
    puts_500_que_aplican=frozenset(),
    puts_207_con_error=frozenset(),
    puts_red_caida=frozenset(),
    puts_timeout_que_aplican=frozenset(),
    puts_timeout_sin_aplicar=frozenset(),
):
    """Escritor sobre transporte falso: list con filas completas, PUT que
    cambia el estado en vivo, create que devuelve un adId nuevo.
    lists_que_fallan: numeros de LIST (1-based) que responden 500.
    stale_lists: numeros de LIST que ven el estado previo al ultimo PUT.
    rechaza_put: adIds cuyo PUT responde 400.
    puts_500_que_aplican: adIds cuyo PUT aplica pero responde 500.
    puts_207_con_error: adIds cuyo PUT responde 207 con error por item.
    puts_red_caida: adIds cuyo PUT corta la conexion sin aplicar.
    puts_timeout_que_aplican: adIds cuyo PUT aplica pero expira.
    puts_timeout_sin_aplicar: adIds cuyo PUT expira sin aplicar.
    ocultos: {adId: n} LISTs que aun no traen ese ad (eventual)."""
    puts: list[tuple[str, str]] = []
    vivos = {k: dict(v) for k, v in filas.items()}
    foto = {k: dict(v) for k, v in filas.items()}
    nuevo = {"n": 0}
    n_list = {"n": 0}
    oculta = dict(ocultos or {})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        if request.method == "PUT" and request.url.path == "/sp/productAds":
            for entrada in cuerpo.get("productAds", []):
                if str(entrada["adId"]) in rechaza_put:
                    return httpx.Response(400, json={"code": "400", "details": "rechazado"})
                if str(entrada["adId"]) in puts_500_que_aplican:
                    foto[str(entrada["adId"])] = dict(vivos[str(entrada["adId"])])
                    vivos[str(entrada["adId"])]["state"] = str(entrada["state"])
                    puts.append((str(entrada["adId"]), str(entrada["state"])))
                    return httpx.Response(500, text="Internal error")
                if str(entrada["adId"]) in puts_207_con_error:
                    return httpx.Response(
                        207,
                        json={
                            "productAds": {
                                "error": [{"code": "400", "adId": str(entrada["adId"])}],
                                "success": [],
                            }
                        },
                    )
                if str(entrada["adId"]) in puts_red_caida:
                    raise httpx.ConnectError("conexion cortada antes de Amazon")
                if str(entrada["adId"]) in puts_timeout_que_aplican:
                    foto[str(entrada["adId"])] = dict(vivos[str(entrada["adId"])])
                    vivos[str(entrada["adId"])]["state"] = str(entrada["state"])
                    puts.append((str(entrada["adId"]), str(entrada["state"])))
                    raise httpx.TimeoutException("respuesta perdida tras aplicar")
                if str(entrada["adId"]) in puts_timeout_sin_aplicar:
                    raise httpx.TimeoutException("expiro sin aplicar")
                puts.append((str(entrada["adId"]), str(entrada["state"])))
                foto[str(entrada["adId"])] = dict(vivos[str(entrada["adId"])])
                vivos[str(entrada["adId"])]["state"] = str(entrada["state"])
            return httpx.Response(207, json={"productAds": {"error": [], "success": []}})
        if request.method == "POST" and request.url.path == "/sp/productAds":
            nuevo["n"] += 1
            ad_id = f"ad-nuevo-{nuevo['n']}"
            vivos[ad_id] = {
                "adId": ad_id,
                "state": cuerpo["productAds"][0]["state"],
                "adGroupId": cuerpo["productAds"][0]["adGroupId"],
                "campaignId": cuerpo["productAds"][0]["campaignId"],
                "sku": cuerpo["productAds"][0]["sku"],
            }
            return httpx.Response(
                207, json={"productAds": {"error": [], "success": [{"adId": ad_id}]}}
            )
        n_list["n"] += 1
        if n_list["n"] in lists_que_fallan:
            return httpx.Response(500, text="Internal error")
        fuente = foto if n_list["n"] in stale_lists else vivos
        pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
        visibles = []
        for p in pedidos:
            if oculta.get(p, 0) > 0:
                oculta[p] -= 1
                continue
            if p in fuente:
                visibles.append(fuente[p])
        return httpx.Response(200, json={"productAds": visibles})

    return make_write_client(handler), puts


def _fila(ad_id, estado, ag="ag-vieja", camp="c-vieja", sku="SS-1"):
    return {
        ad_id: {
            "adId": ad_id,
            "state": estado,
            "adGroupId": ag,
            "campaignId": camp,
            "sku": sku,
            "asin": "B0AAAAAAAA",
        }
    }


@_skip_db
def test_planea_lista_enabled_sin_escribir():
    """I.4: planea lista los ENABLED con sus datos, no escribe nada, y la
    huella cambia si cambia un anuncio."""
    with _db_i4("orbit_i4_pl") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        _bolsa(conn, lid, "ad-2", ag="ag-otra", camp="c-otra")
        _bolsa(conn, lid, "ad-3", ag="ag-p", camp="c-p", estado="PAUSED")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-1", "ad-2"]
        assert plan.anuncios[0].ad_group_nombre is not None
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)
        _bolsa(conn, lid, "ad-4", ag="ag-x", camp="c-x")
        otro = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert otro.huella != plan.huella


@_skip_db
def test_r09r4_planea_incluye_intencion_pendiente_aunque_base_diga_paused():
    """R09-r4 B4 (f): el sync puede mentir (PAUSED con Amazon ENABLED);
    una intencion pendiente entra al plan igual para que el
    reintento la retome. Cerrada no entra."""
    with _db_i4("orbit_i4_r4f") as conn:
        _pid, lid = _producto(conn)
        _c, _a, pa_pend = _bolsa(conn, lid, "ad-pend", camp="c-f1", ag="ag-f1", estado="PAUSED")
        _c, _a, pa_cerr = _bolsa(conn, lid, "ad-cerr", camp="c-f2", ag="ag-f2", estado="PAUSED")
        _c, _a, _p = _bolsa(conn, lid, "ad-paus", camp="c-f3", ag="ag-f3", estado="PAUSED")

        def _intencion(pa_id, ad):
            return conn.execute(
                "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro,"
                " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku,"
                " asin, reversa, actor, creada_el) VALUES ('amazon_mx', 'sin_pedidos',"
                " 'pausa', 'retiro-f', %s, %s, 'ag', 'c', 'SS-1', 'B0AAAAAAAA',"
                " 'reversa', 'dueno', now()) RETURNING id",
                (pa_id, ad),
            ).fetchone()[0]

        _intencion(pa_pend, "ad-pend")
        iid = _intencion(pa_cerr, "ad-cerr")
        conn.execute(
            "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = 'x' WHERE id = %s",
            (iid,),
        )
        conn.commit()
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-pend"]


@_skip_db
def test_r09r4_f_previa_caida_retoma_tras_sync_mentiroso():
    """R09-r4 B4 (f): previa caida -> intencion pendiente sin fila de
    retiro; el sync escribe PAUSED (Amazon sigue ENABLED); el plan
    nuevo la incluye igual y el reintento manda el PUT y sella."""
    import pytest

    with _db_i4("orbit_i4_r4fi") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-f1", camp="c-fi1", ag="ag-fi1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-f1"]
        # La previa de ejecuta (list 1) pasa; cae la de pausar (2+).
        escritor, puts = _escritor(
            _fila("ad-f1", "ENABLED"), lists_que_fallan=frozenset({2, 3, 4, 5})
        )
        with pytest.raises(RuntimeError, match="sin_confirmar"):
            ra.ejecuta_retiro(conn, plan, go="SACAR 1", escritor=escritor, actor="dueno")
        assert puts == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone() == (1,)
        conn.execute(
            "UPDATE ad_entity_state s SET status = 'PAUSED' FROM ad_entity e"
            " WHERE e.id = s.ad_entity_id AND e.external_id = 'ad-f1'"
        )
        conn.commit()
        plan2 = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan2.anuncios] == ["ad-f1"]
        escritor2, puts2 = _escritor(_fila("ad-f1", "ENABLED"))
        n = ra.ejecuta_retiro(conn, plan2, go="SACAR 1", escritor=escritor2, actor="dueno")
        assert n == 1
        assert puts2 == [("ad-f1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone() == (0,)


@_skip_db
def test_r09r4_ra02_plan_protege_impulso_vivo_con_lectura_en_curso():
    """R09-r4 B6 (RA02): ad group de un impulso vivo que YA tiene
    lectura (en curso, no final): el plan lo excluye (protegido)."""
    from test_impulso_io import _impulso_vivo

    with _db_i4("orbit_i4_r4ra02") as conn:
        _pid, lid = _producto(conn)
        ids = _impulso_vivo(conn, pid=_pid, lid=lid, lote="impulso-ra02-1")
        ag = conn.execute(
            "SELECT ad_group_ad_entity_id FROM campana_grupo_rol WHERE grupo_id = %s LIMIT 1",
            (ids["grupo"],),
        ).fetchone()[0]
        pa = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
            " VALUES ('amazon_mx', 'product_ad', 'ad-ra02', %s, %s) RETURNING id",
            (ag, lid),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (pa,),
        )
        conn.execute(
            "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
            " impresiones, clics, gasto, gasto_moneda, pedidos, veredicto, es_final, hecho)"
            " VALUES (%s, CURRENT_DATE, now(), 3, 100, 5, 10, 'MXN', 0, 'en_curso', false, 'nada')",
            (ids["impulso"],),
        )
        conn.commit()
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan.anuncios] == []


@_skip_db
def test_r09r4_ra04_impulso_sin_impresiones_no_retira():
    """R09-r4 B6 (RA04): motivo 'impulso' exige al menos una lectura
    con impresiones > 0; 0 impresiones (o NULL) no basta."""
    from test_impulso_io import _impulso_vivo

    with _db_i4("orbit_i4_r4ra04") as conn:
        _pid, lid = _producto(conn)
        ids = _impulso_vivo(conn, pid=_pid, lid=lid, lote="impulso-ra04-1")
        assert ra.impulso_tuvo_impresiones(conn, ids["impulso"]) is False
        conn.execute(
            "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
            " impresiones, clics, gasto, gasto_moneda, pedidos, veredicto, es_final, hecho)"
            " VALUES (%s, CURRENT_DATE, now(), 3, 0, 0, 0, 'MXN', 0, 'en_curso', false, 'nada')",
            (ids["impulso"],),
        )
        conn.commit()
        assert ra.impulso_tuvo_impresiones(conn, ids["impulso"]) is False
        conn.execute(
            "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
            " impresiones, clics, gasto, gasto_moneda, pedidos, veredicto, es_final, hecho)"
            " VALUES (%s, CURRENT_DATE + 1, now(), 4, 7, 1, 1, 'MXN', 0,"
            " 'en_curso', false, 'nada')",
            (ids["impulso"],),
        )
        conn.commit()
        assert ra.impulso_tuvo_impresiones(conn, ids["impulso"]) is True


@_skip_db
def test_r09r4_ra07_grupo_sin_anuncios_no_bloquea_lote():
    """R09-r4 B6 (RA07): grupo a conservar aun sin anuncios en
    ad_entity (dia de creacion): se acepta como propio (no 422)."""
    from test_impulso_io import _impulso_vivo

    with _db_i4("orbit_i4_r4ra07") as conn:
        _pid, lid = _producto(conn)
        ids = _impulso_vivo(conn, pid=_pid, lid=lid, lote="impulso-ra07-1")
        assert ra.grupo_alcanza_listings(conn, ids["grupo"], [lid]) is True


@_skip_db
def test_r09r4_ra28b_retoma_desaparecido_cierra_sin_fallar():
    """R09-r4 B6 (RA28, segundo sitio), R09-r5 B3 (iii): reintento cuya
    previa ya no trae el anuncio: sin PUT, cierra la intencion con
    detalle (inexistente: nada que pausar) y no falla: el reintento
    converge en vez de responder 502 para siempre."""
    import pytest

    with _db_i4("orbit_i4_r4ra28b") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-28b")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(
            _fila("ad-28b", "ENABLED"), lists_que_fallan=frozenset({2, 3, 4, 5})
        )
        with pytest.raises(RuntimeError, match="sin_confirmar"):
            ra.ejecuta_retiro(conn, plan, go="SACAR 1", escritor=escritor, actor="dueno")
        (iid,) = conn.execute("SELECT id FROM anuncio_retiro_intencion").fetchall()
        plan2 = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan2.anuncios] == ["ad-28b"]
        escritor2, puts2 = _escritor({})
        assert ra.ejecuta_retiro(conn, plan2, go="SACAR 1", escritor=escritor2, actor="dueno") == 0
        assert puts2 == []
        assert conn.execute(
            "SELECT cerrada_el IS NOT NULL, retiro_id IS NULL FROM anuncio_retiro_intencion"
            " WHERE id = %s",
            (iid[0],),
        ).fetchone() == (True, True)
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)


@_skip_db
def test_r09r4_ra28_anuncio_inexistente_se_nombra():
    """R09-r4 B6 (RA28): anuncio que no existe en Amazon (previa sin
    fila): se nombra como no_existe, no revienta mas abajo."""
    import pytest

    with _db_i4("orbit_i4_r4ra28") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-fantasma")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-fantasma"]
        escritor, puts = _escritor({})
        with pytest.raises(RuntimeError, match="no_existe"):
            ra.ejecuta_retiro(conn, plan, go="SACAR 1", escritor=escritor, actor="dueno")
        assert puts == []


@_skip_db
def test_planea_respeta_grupo_y_salta_sin_listing():
    """I.4: con conservar_grupo_id no lista los anuncios de sus ad groups;
    los anuncios sin listing quedan fuera."""
    with _db_i4("orbit_i4_gr") as conn:
        _pid, lid = _producto(conn)
        _camp_c, ag_c, _pa_c = _bolsa(conn, lid, "ad-grupo", camp="c-g", ag="ag-g")
        _bolsa(conn, lid, "ad-fuera", camp="c-f", ag="ag-f")
        camp_s = _entidad(conn, "amazon_mx", "campaign", "c-s")
        ag_s = _entidad(conn, "amazon_mx", "ad_group", "ag-s", parent=camp_s)
        pa_s = _entidad(conn, "amazon_mx", "product_ad", "ad-sin", parent=ag_s, listing_id=None)
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (pa_s,),
        )
        lote = _lote(conn, "fabrica-i4-g")
        grupo = _grupo(conn, lote)
        conn.execute(
            "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)"
            " VALUES (%s, 'category_exact', %s, %s)",
            (grupo, _camp_c, ag_c),
        )
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=grupo
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-fuera"]


@_skip_db
def test_plan_con_lote_nuevo_excluye_sus_ad_groups():
    """E.1 mutante: con el lote de un grupo nuevo, el plan no trae
    ningun anuncio de sus ad groups (quitar el filtro lo rompe)."""
    with _db_i4("orbit_e1_lote") as conn:
        _pid, lid = _producto(conn)
        _camp_c, ag_c, _pa_c = _bolsa(conn, lid, "ad-nuevo", camp="c-n", ag="ag-n")
        _bolsa(conn, lid, "ad-viejo", camp="c-v", ag="ag-v")
        lote = _lote(conn, "fabrica-e1-nuevo")
        grupo = _grupo(conn, lote)
        conn.execute(
            "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)"
            " VALUES (%s, 'category_exact', %s, %s)",
            (grupo, _camp_c, ag_c),
        )
        conservar = ra.grupo_de_lote(conn, lote)
        assert conservar == grupo
        assert ra.grupo_de_lote(conn, "lote-que-no-existe") is None
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="subfamilia", conservar_grupo_id=conservar
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-viejo"]


@_skip_db
def test_ejecuta_pausa_y_segunda_vez_no_repite():
    """I.4: ejecuta pausa (modo 'pausa'), guarda una fila por anuncio y la
    segunda llamada no manda ni una mutacion."""
    with _db_i4("orbit_i4_ej") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        _bolsa(conn, lid, "ad-2", ag="ag-otra", camp="c-otra")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor({**_fila("ad-1", "ENABLED"), **_fila("ad-2", "ENABLED")})
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 2
        assert puts == [("ad-1", "PAUSED"), ("ad-2", "PAUSED")]
        assert conn.execute(
            "SELECT count(*), count(DISTINCT modo) FROM anuncio_retiro"
        ).fetchone() == (2, 1)
        assert conn.execute("SELECT DISTINCT modo FROM anuncio_retiro").fetchone() == ("pausa",)
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 0
        assert len(puts) == 2


@_skip_db
def test_repone_reactiva_y_segunda_no_repite():
    """I.4: repone reactiva el MISMO adId, guarda la reposicion y la
    segunda llamada no repone otra vez."""
    with _db_i4("orbit_i4_rep") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor) == 1
        assert puts == [("ad-1", "PAUSED"), ("ad-1", "ENABLED")]
        assert conn.execute(
            "SELECT ad_id_nuevo, estado_nuevo FROM anuncio_reposicion WHERE retiro_id = %s",
            (ret[0],),
        ).fetchone() == ("ad-1", "ENABLED")
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor) == 0
        assert len(puts) == 2


@_skip_db
def test_parejas_ignora_archived():
    """I.4: una campana cuyo unico product ad de otra familia esta
    ARCHIVED conserva su familia (el SQL solo cuenta ENABLED/PAUSED)."""
    from app import cycle as ciclo
    from app.optimizer.goals import familia_efectiva_por_campana

    with _db_i4("orbit_i4_pf") as conn:
        pid_a, lid_a = _producto(conn)
        pid_b, lid_b = _producto(conn, sku="SKU-B", asin="B0BBBBBBBB", seller_sku="SS-B")
        fam_a = conn.execute(
            "INSERT INTO familia (platform, nombre, slug) VALUES ('amazon_mx', 'Fam A', 'fam_a')"
            " RETURNING id"
        ).fetchone()[0]
        fam_b = conn.execute(
            "INSERT INTO familia (platform, nombre, slug) VALUES ('amazon_mx', 'Fam B', 'fam_b')"
            " RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO producto_familia (product_id, platform, familia_id)"
            " VALUES (%s, 'amazon_mx', %s), (%s, 'amazon_mx', %s)",
            (pid_a, fam_a, pid_b, fam_b),
        )
        camp = _entidad(conn, "amazon_mx", "campaign", "c-mezcla")
        ag = _entidad(conn, "amazon_mx", "ad_group", "ag-mezcla", parent=camp)
        pa_a = _entidad(conn, "amazon_mx", "product_ad", "ad-a", parent=ag, listing_id=lid_a)
        pa_b = _entidad(conn, "amazon_mx", "product_ad", "ad-b", parent=ag, listing_id=lid_b)
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now()), (%s, 'ARCHIVED', now())",
            (pa_a, pa_b),
        )
        pares = [
            (f[0], f[1])
            for f in conn.execute(ciclo._SQL_PAREJAS_CAMPANA_FAMILIA, ("amazon_mx",)).fetchall()
        ]
        assert pares == [(camp, fam_a)]
        assert familia_efectiva_por_campana(pares) == {camp: fam_a}


@_skip_db
def test_ejecuta_un_fallo_no_bloquea_el_registro_de_los_confirmados():
    """R09-B: con [a, b-archived, c], ejecuta registra a y c y al final
    falla nombrando a b (antes abortaba y c quedaba pausado en Amazon
    sin fila ni reversa)."""
    with _db_i4("orbit_i4_parcial") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        _bolsa(conn, lid, "ad-2", ag="ag-otra", camp="c-otra")
        _bolsa(conn, lid, "ad-3", ag="ag-x", camp="c-x")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-1", "ad-2", "ad-3"]
        escritor, puts = _escritor(
            {
                **_fila("ad-1", "ENABLED"),
                **_fila("ad-2", "ARCHIVED"),
                **_fila("ad-3", "ENABLED"),
            }
        )
        with pytest.raises(RuntimeError, match="ad-2"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == [("ad-1", "PAUSED"), ("ad-3", "PAUSED")]
        assert conn.execute(
            "SELECT ad_id_externo FROM anuncio_retiro ORDER BY ad_id_externo"
        ).fetchall() == [("ad-1",), ("ad-3",)]


@_skip_db
def test_ejecuta_ya_pausado_se_registra_sin_fallar():
    """R09-r2 B-2: crash entre PUT e INSERT: en la re-corrida el anuncio
    ya esta PAUSED (ya_estaba) y se registra como retirado sin PUT ni
    RuntimeError (antes bloqueaba el retiro para siempre)."""
    with _db_i4("orbit_i4_yaestaba") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        _bolsa(conn, lid, "ad-2", ag="ag-otra", camp="c-otra")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor({**_fila("ad-1", "ENABLED"), **_fila("ad-2", "PAUSED")})
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 2
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute(
            "SELECT ad_id_externo FROM anuncio_retiro ORDER BY ad_id_externo"
        ).fetchall() == [("ad-1",), ("ad-2",)]


@_skip_db
def test_repone_archived_recrea_con_la_linea():
    """I.4: si el anuncio se archivo por fuera, reponer lo recrea en su
    ad group con la linea guardada."""
    with _db_i4("orbit_i4_arc") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        escritor2, _puts2 = _escritor(_fila("ad-1", "ARCHIVED"))
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor2) == 1
        from app.ads import archivar

        assert archivar.estados_actuales(escritor2, ["ad-nuevo-1"]) == {"ad-nuevo-1": "ENABLED"}
        assert conn.execute(
            "SELECT ad_id_nuevo, estado_nuevo FROM anuncio_reposicion WHERE retiro_id = %s",
            (ret[0],),
        ).fetchone() == ("ad-nuevo-1", "ENABLED")


@_skip_db
def test_ejecuta_relectura_caida_conserva_intencion():
    """R09 B5 (f/A) + R09-r4 (tabla propia): el PUT pasa y la relectura
    da 500 (con reintentos): sin fila de retiro, con intencion
    pendiente (retomable) y el error la nombra."""
    with _db_i4("orbit_i4_r5a") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(
            _fila("ad-1", "ENABLED"),
            lists_que_fallan=set(range(3, 12)),  # relectura + reintentos
        )
        with pytest.raises(RuntimeError, match="ad-1"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone() == (1,)


@_skip_db
def test_ejecuta_sin_sku_falla_antes_del_put():
    """R09 B5 (h/B): anuncio sin sku (listing con seller_sku NULL): ValueError
    antes del primer PUT, sin filas."""
    with _db_i4("orbit_i4_r5b") as conn:
        _pid, lid = _producto(conn)
        conn.execute("UPDATE listing SET seller_sku = NULL WHERE id = %s", (lid,))
        conn.commit()
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert plan.anuncios[0].sku is None
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))
        with pytest.raises(ValueError, match="sin sku"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)


@_skip_db
def test_ejecuta_relectura_atrasada_conserva_intencion():
    """R09 B5 (C) + R09-r4 (tabla propia): relectura stale (ve ENABLED
    tras el PUT): sin_confirmar conserva la intencion pendiente (sin
    fila de retiro) y el error la nombra."""
    with _db_i4("orbit_i4_r5c") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), stale_lists={3})
        with pytest.raises(RuntimeError, match="sin_confirmar"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone() == (1,)


@_skip_db
def test_repone_previo_pausado_no_reactiva():
    """R09 B6 (ad-2): pausado por el dueno antes del retiro: al reponer no
    se reactiva, se cierra con PAUSED."""
    with _db_i4("orbit_i4_r6a") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 1
        assert puts == []
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor) == 1
        assert puts == []
        assert conn.execute(
            "SELECT ad_id_nuevo, estado_nuevo FROM anuncio_reposicion WHERE retiro_id = %s",
            (ret[0],),
        ).fetchone() == ("ad-1", "PAUSED")


@_skip_db
def test_repone_previo_pausado_actual_enabled_lo_vuelve_a_pausar():
    """R09 B6: previo PAUSED pero prendido por fuera: reponer lo devuelve
    a PAUSED."""
    with _db_i4("orbit_i4_r6b") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "PAUSED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        escritor.cambiar_estado_product_ad("ad-1", "ENABLED")
        puts.clear()
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor) == 1
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute(
            "SELECT estado_nuevo FROM anuncio_reposicion WHERE retiro_id = %s",
            (ret[0],),
        ).fetchone() == ("PAUSED",)


@_skip_db
def test_repone_previo_pausado_archivado_recrea_pausado():
    """R09 B6 (ad-4): previo PAUSED y archivado por fuera: se recrea PAUSED."""
    with _db_i4("orbit_i4_r6c") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-1", "PAUSED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        escritor2, _puts2 = _escritor(_fila("ad-1", "ARCHIVED"))
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor2) == 1
        from app.ads import archivar

        assert archivar.estados_actuales(escritor2, ["ad-nuevo-1"]) == {"ad-nuevo-1": "PAUSED"}
        assert conn.execute(
            "SELECT ad_id_nuevo, estado_nuevo FROM anuncio_reposicion WHERE retiro_id = %s",
            (ret[0],),
        ).fetchone() == ("ad-nuevo-1", "PAUSED")


def _otra_conn(conn):
    import psycopg
    from test_schema import _test_dsn

    return psycopg.connect(_test_dsn(), dbname=conn.info.dbname, autocommit=True)


@_skip_db
def test_r09r4_ra22_intencion_visible_durante_put():
    """R09-r4 B6 (RA22): la intencion se confirma (commit) ANTES del
    PUT: otra conexion la ve mientras el PUT esta en vuelo (si el
    proceso muere ahi, el reintento retoma). Aun no hay fila de
    retiro (solo se escribe tras el readback)."""
    import threading

    import httpx
    from test_ads_write import make_write_client

    with _db_i4("orbit_i4_r4ra22") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-22")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        en_put = threading.Event()
        suelta = threading.Event()
        vivos = {"ad-22": {"adId": "ad-22", "state": "ENABLED"}}

        def handler(request):
            if request.url.host == "api.amazon.com":
                return _token_response()
            cuerpo = json.loads(request.content) if request.content else {}
            if request.method == "PUT":
                en_put.set()
                assert suelta.wait(timeout=30)
                for entrada in cuerpo.get("productAds", []):
                    vivos[str(entrada["adId"])]["state"] = str(entrada["state"])
                return httpx.Response(207, json={"productAds": {"error": [], "success": []}})
            pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
            return httpx.Response(
                200, json={"productAds": [vivos[p] for p in pedidos if p in vivos]}
            )

        escritor = make_write_client(handler, sleep=lambda s: None)
        err: list = []

        def _corre():
            import psycopg
            from test_schema import _test_dsn

            try:
                # Sin autocommit (como el vigia): el commit explicito
                # antes del PUT es lo que hace visible la fila.
                otra = psycopg.connect(_test_dsn(), dbname=conn.info.dbname)
                try:
                    ra.ejecuta_retiro(otra, plan, go="go dueno", escritor=escritor)
                finally:
                    otra.close()
            except Exception as exc:  # noqa: BLE001
                err.append(exc)
            finally:
                en_put.set()

        hilo = threading.Thread(target=_corre)
        hilo.start()
        assert en_put.wait(timeout=30)
        try:
            assert conn.execute(
                "SELECT count(*) FROM anuncio_retiro_intencion"
                " WHERE ad_id_externo = 'ad-22' AND cerrada_el IS NULL"
            ).fetchone() == (1,)
            assert conn.execute(
                "SELECT count(*) FROM anuncio_retiro WHERE ad_id_externo = 'ad-22'"
            ).fetchone() == (0,)
        finally:
            suelta.set()
            hilo.join(timeout=30)
        assert not err


@_skip_db
def test_ejecuta_simultaneo_un_retiro_un_put():
    """R09 B9 (i5): dos ejecutar a la vez sobre el mismo anuncio: una fila,
    un PUT, sin excepcion (1 y 0)."""
    from concurrent.futures import ThreadPoolExecutor

    with _db_i4("orbit_i4_r9a") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def uno(_):
            otra = _otra_conn(conn)
            try:
                return ra.ejecuta_retiro(otra, plan, go="go dueno", escritor=escritor)
            finally:
                otra.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(uno, range(2))) == [0, 1]
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)


@_skip_db
def test_r09r4_ra15_candado_se_suelta_al_terminar():
    """R09-r4 B6 (RA15): ejecutar suelta sus advisory locks (cero
    retenidos en pg_locks al terminar)."""
    with _db_i4("orbit_i4_r4ra15") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-15")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-15", "ENABLED"))
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 1
        assert conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
        ).fetchone() == (0,)


@_skip_db
def test_r09r4_ra30_reponer_sin_escritor_es_502(monkeypatch):
    """R09-r4 B6 (RA30): preparar_escritor caido (ValueError) al
    reponer: RuntimeError (502), no ValueError (422)."""
    import pytest

    from app.ads import archivar

    with _db_i4("orbit_i4_r4ra30") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-30")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-30", "ENABLED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()

        def _caido(platform):
            raise ValueError("sin perfil unico")

        monkeypatch.setattr(archivar, "preparar_escritor", _caido)
        with pytest.raises(RuntimeError, match="Amazon no disponible para reponer"):
            ra.repone(conn, retiro_ids=(ret[0],), go="go dueno")


@_skip_db
def test_r09r4_ra42_reponer_pausa_no_confirmada_no_cierra():
    """R09-r4 B6 (RA42): reponer previo-pausado con PUT rechazado: no
    marca repuesto (levanta, sin reposicion)."""
    import pytest

    with _db_i4("orbit_i4_r4ra42") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-42")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        # Previo PAUSED (ya_estaba, sella); luego el dueno lo enciende.
        escritor, _puts = _escritor(_fila("ad-42", "PAUSED"))
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 1
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        escritor2, _puts2 = _escritor(_fila("ad-42", "ENABLED"), rechaza_put=frozenset({"ad-42"}))
        with pytest.raises(RuntimeError, match="no confirmada"):
            ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor2)
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)


@_skip_db
def test_r09r4_ra44_recreado_sin_confirmar_no_cierra():
    """R09-r4 B6 (RA44): reponer archivado cuyo recreado nunca se ve
    en el list: no marca repuesto (levanta, sin reposicion)."""
    import pytest

    with _db_i4("orbit_i4_r4ra44") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-44")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-44", "ENABLED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        escritor2, _puts2 = _escritor(
            _fila("ad-44", "ARCHIVED"), ocultos={"ad-nuevo-1": 9, "ad-nuevo-2": 9}
        )
        with pytest.raises(RuntimeError, match="no confirmada"):
            ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor2)
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)


@_skip_db
def test_r09r4_ra46_repone_graba_estado_real():
    """R09-r4 B6 (RA46): reponer anuncio que ya esta como estaba
    (ENABLED): graba ENABLED, no PAUSED."""
    with _db_i4("orbit_i4_r4ra46") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-46")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-46", "ENABLED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        escritor2, puts2 = _escritor(_fila("ad-46", "ENABLED"))
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor2) == 1
        assert puts2 == []
        assert conn.execute(
            "SELECT estado_nuevo FROM anuncio_reposicion WHERE retiro_id = %s", (ret[0],)
        ).fetchone() == ("ENABLED",)


@_skip_db
def test_repone_simultaneo_una_reposicion():
    """R09 B9 (i7/04-b): dos reponer a la vez: una reposicion, un PUT, sin
    excepcion (1 y 0)."""
    from concurrent.futures import ThreadPoolExecutor

    with _db_i4("orbit_i4_r9b") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        puts.clear()

        def uno(_):
            otra = _otra_conn(conn)
            try:
                return ra.repone(otra, retiro_ids=(ret[0],), go="go dueno", escritor=escritor)
            finally:
                otra.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(uno, range(2))) == [0, 1]
        assert puts == [("ad-1", "ENABLED")]
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (1,)


@_skip_db
def test_repone_parcial_sigue_y_nombra():
    """R09 B9 (g): Amazon rechaza reactivar 1 de 3: los buenos se reponen,
    el error nombra al malo."""
    with _db_i4("orbit_i4_r9c") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        _bolsa(conn, lid, "ad-2", ag="ag-2", camp="c-2")
        _bolsa(conn, lid, "ad-3", ag="ag-3", camp="c-3")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(
            {**_fila("ad-1", "ENABLED"), **_fila("ad-2", "ENABLED"), **_fila("ad-3", "ENABLED")},
        )
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 3
        rids = tuple(
            r[0] for r in conn.execute("SELECT id FROM anuncio_retiro ORDER BY id").fetchall()
        )
        escritor2, _puts2 = _escritor(
            {**_fila("ad-1", "PAUSED"), **_fila("ad-2", "PAUSED"), **_fila("ad-3", "PAUSED")},
            rechaza_put={"ad-2"},
        )
        with pytest.raises(RuntimeError, match="ad-2"):
            ra.repone(conn, retiro_ids=rids, go="go dueno", escritor=escritor2)
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (2,)


@_skip_db
def test_repone_create_invisible_reintenta_un_solo_create():
    """R09 B9 (04-a): el nuevo tarda un LIST en verse: se reintenta la
    relectura (no el create): un anuncio nuevo, repuesto."""
    with _db_i4("orbit_i4_r9d") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        escritor2, _puts2 = _escritor(_fila("ad-1", "ARCHIVED"), ocultos={"ad-nuevo-1": 1})
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor2) == 1
        from app.ads import archivar

        assert archivar.estados_actuales(escritor2, ["ad-nuevo-1", "ad-nuevo-2"]) == {
            "ad-nuevo-1": "ENABLED"
        }


@_skip_db
def test_repone_reversa_legacy_reactiva():
    """R09 B9 (i7): reversa '{}' sembrada a mano (sin state): previo
    desconocido, se reactiva sin reventar."""
    with _db_i4("orbit_i4_r9e") as conn:
        _pid, lid = _producto(conn)
        _, _, pa = _bolsa(conn, lid, "ad-1")
        rid = conn.execute(
            "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, ad_entity_id,"
            " ad_id_externo, ad_group_externo, campana_externa, sku, asin, reversa,"
            " retirado_el, actor) VALUES ('amazon_mx', 'sin_pedidos', 'pausa',"
            " 'lot', %s, 'ad-1', 'ag-vieja', 'c-vieja', 'sku', 'asin', '{}', now(), 'dueno')"
            " RETURNING id",
            (pa,),
        ).fetchone()[0]
        conn.commit()
        escritor, puts = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.repone(conn, retiro_ids=(rid,), go="go dueno", escritor=escritor) == 1
        assert puts == [("ad-1", "ENABLED")]


@_skip_db
def test_r09r3_p02_plan_no_cruza_plataforma():
    """R09 B12 (P02): el plan de MX no lista anuncios de US."""
    with _db_i4("orbit_i4_r3p02") as conn:
        _p1, lid_mx = _producto(conn, sku="SS-P02-MX", seller_sku="SS-P02-MX")
        _p2, lid_us = _producto(
            conn,
            sku="SS-P02-US",
            asin="B0P02US0001",
            platform="amazon_us",
            seller_sku="SS-P02-US",
        )
        _bolsa(conn, lid_mx, "ad-mx")
        _bolsa(conn, lid_us, "ad-us", camp="c-us", ag="ag-us", platform="amazon_us")
        plan = ra.planea_retiro(
            conn,
            "amazon_mx",
            listing_ids=(lid_mx, lid_us),
            motivo="sin_pedidos",
            conservar_grupo_id=None,
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-mx"]


@_skip_db
def test_r09r3_p03_plan_solo_sus_productos():
    """R09 B12 (P03): el plan no lista anuncios de otro producto."""
    with _db_i4("orbit_i4_r3p03") as conn:
        _p1, lid1 = _producto(conn, sku="SS-P03-1", seller_sku="SS-P03-1")
        _p2, lid2 = _producto(conn, sku="SS-P03-2", asin="B0P030000002", seller_sku="SS-P03-2")
        _bolsa(conn, lid1, "ad-1")
        _bolsa(conn, lid2, "ad-2", camp="c-2", ag="ag-2")
        plan = ra.planea_retiro(
            conn,
            "amazon_mx",
            listing_ids=(lid1,),
            motivo="sin_pedidos",
            conservar_grupo_id=None,
        )
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-1"]


@_skip_db
def test_r09r3_e01_repuesto_se_puede_retirar_de_nuevo():
    """R09 B12 (E01): un anuncio ya repuesto se puede retirar otra vez
    (el retiro cerrado no bloquea)."""
    with _db_i4("orbit_i4_r3e01") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 1
        (ret,) = conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        assert ra.repone(conn, retiro_ids=(ret[0],), go="go dueno", escritor=escritor) == 1
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 1
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (2,)


@_skip_db
def test_r09r4_r04r_recreado_sin_confirmar_no_marca_repuesto():
    """R09-r4 B6 (R04r): reponer un ARCHIVADO cuyo anuncio nuevo nunca se
    ve en la relectura no registra reposicion."""
    with _db_i4("orbit_i4_r4r04") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 1
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        ciego, _puts2 = _escritor(_fila("ad-1", "ARCHIVED"), ocultos={"ad-nuevo-1": 10})
        with pytest.raises(RuntimeError, match="reposicion parcial"):
            ra.repone(conn, retiro_ids=(rid,), go="go dueno", escritor=ciego)
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)


@_skip_db
def test_r09r4_c_retiro_y_regreso_a_la_vez_se_excluyen():
    """R09-r4 B4 (Y-05 c, tabla propia): con un retiro sellado abierto,
    regresar (PUT frenado) y retirar a la vez usan la misma llave
    retiro-ad: se serializan: el regreso cierra y el retiro posterior
    abre un retiro NUEVO (no deja PAUSED sin regreso registrado). Sin
    la llave, el retiro se cuela viendo el retiro aun abierto y salta."""
    import threading
    import time

    import psycopg

    with _db_i4("orbit_i4_r4c") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )

        import app.ads.archivar as archivar_real

        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, plan, go="go cero", escritor=escritor) == 1
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        llego, sigue = threading.Event(), threading.Event()
        real_reactivar = archivar_real.reactivar_anuncios

        def _freno(esc, ad_ids, **kw):
            if not llego.is_set():
                llego.set()
                assert sigue.wait(20)
            return real_reactivar(esc, ad_ids, **kw)

        archivar_real.reactivar_anuncios = _freno
        res = {}
        try:
            info = conn.info
            dsn_hilos = _dsn_propia(conn)

            def _regresa():
                with psycopg.connect(dsn_hilos, dbname=info.dbname, autocommit=True) as c2:
                    try:
                        res["regreso"] = ra.repone(
                            c2, retiro_ids=(rid,), go="go uno", escritor=escritor, actor="uno"
                        )
                    except Exception as exc:  # noqa: BLE001
                        res["regreso"] = exc

            def _retira():
                with psycopg.connect(dsn_hilos, dbname=info.dbname, autocommit=True) as c3:
                    try:
                        res["retiro"] = ra.ejecuta_retiro(
                            c3, plan, go="go dos", escritor=escritor, actor="dos"
                        )
                    except Exception as exc:  # noqa: BLE001
                        res["retiro"] = exc

            t1 = threading.Thread(target=_regresa)
            t1.start()
            assert llego.wait(20)
            t2 = threading.Thread(target=_retira)
            t2.start()
            time.sleep(1.0)
            sigue.set()
            t1.join(30)
            t2.join(30)
        finally:
            archivar_real.reactivar_anuncios = real_reactivar
        assert res["regreso"] == 1
        assert res["retiro"] == 1
        estados = archivar_real.estados_actuales(escritor, ["ad-1"])
        assert estados == {"ad-1": "PAUSED"}
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (2,)
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (1,)


@_skip_db
def test_r09r4_b4b_rechazo_no_deja_fila_y_reintento_sella():
    """R09-r4 B4 (Y-01 B/D, tabla propia): PUT rechazado limpio NO deja
    fila en anuncio_retiro (la intencion se cierra en su tabla); el
    reintento abre intencion nueva, manda el PUT y sella."""
    with _db_i4("orbit_i4_r4b4b") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), rechaza_put={"ad-1"})
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM anuncio_retiro_intencion").fetchone() == (1,)
        escritor2, puts2 = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor2) == 1
        assert puts2 == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM anuncio_retiro_intencion").fetchone() == (2,)


@_skip_db
def test_r09r4_b4d_muerte_no_deja_fila_y_reintento_retoma():
    """R09-r4 B4 (Y-01 D, tabla propia): muerte entre intencion y PUT
    deja intencion pendiente pero NINGUNA fila en anuncio_retiro; el
    reintento retoma LA MISMA intencion, manda el PUT y sella."""
    import app.ads.archivar as archivar_real

    with _db_i4("orbit_i4_r4b4d") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))
        real_pausar = archivar_real.pausar_anuncios

        class Muerte(BaseException):
            pass

        def _muere(*args, **kwargs):
            raise Muerte("kill -9 antes del PUT")

        archivar_real.pausar_anuncios = _muere
        try:
            with pytest.raises(Muerte):
                ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        finally:
            archivar_real.pausar_anuncios = real_pausar
        assert puts == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        (iid,) = conn.execute(
            "SELECT id FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone()
        plan2 = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan2.anuncios] == ["ad-1"]
        escritor2, puts2 = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, plan2, go="go dueno", escritor=escritor2) == 1
        assert puts2 == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        assert conn.execute(
            "SELECT retiro_id IS NOT NULL FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
        ).fetchone() == (True,)


@_skip_db
def test_r09r4_b4e_put_ok_sin_readback_reintento_sella():
    """R09-r4 B4 (Y-01 B/C/E, tabla propia): PUT ok pero readback
    imposible (caido): sin fila de retiro, con intencion pendiente
    (transitorio correcto: sellar sin readback violaria el criterio);
    el reintento confirma (ya_estaba) y sella."""
    with _db_i4("orbit_i4_r4b4e") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), lists_que_fallan=set(range(3, 12)))
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        (iid,) = conn.execute(
            "SELECT id FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone()
        plan2 = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan2.anuncios] == ["ad-1"]
        escritor2, puts2 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, plan2, go="go dueno", escritor=escritor2) == 1
        assert puts2 == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        assert (
            conn.execute(
                "SELECT retiro_id IS NOT NULL FROM anuncio_retiro_intencion WHERE id = %s",
                (iid,),
            ).fetchone()[0]
            is True
        )


@_skip_db
def test_r09r4_ra21_archived_ni_abre_intencion():
    """R09-r4 B6 (RA21): anuncio ARCHIVED en la previa: se nombra sin
    abrir intencion (fail fast, 0 escrituras) y sin PUT."""
    with _db_i4("orbit_i4_r4ra21") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-21")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-21", "ARCHIVED"))
        with pytest.raises(RuntimeError, match="ARCHIVED"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro_intencion").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)


@_skip_db
def test_r09r5_r01_reintento_sella_con_reversa_de_la_intencion():
    """R09-r5 B3 (r01, caso E): PUT aplicado pero incierto; el
    reintento lee PAUSED y sella. El retiro lleva la reversa
    guardada en la intencion (state=ENABLED, la foto previa al
    primer PUT), no la de la lectura del reintento; 'Regresar'
    manda PUT ENABLED y el anuncio vuelve."""
    with _db_i4("orbit_i4_r5r01") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), lists_que_fallan=set(range(3, 12)))
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        (rev_intencion,) = conn.execute(
            "SELECT reversa FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone()
        assert "state=ENABLED" in rev_intencion
        plan2 = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor2, puts2 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, plan2, go="go dueno", escritor=escritor2) == 1
        (rev_retiro,) = conn.execute("SELECT reversa FROM anuncio_retiro").fetchone()
        assert rev_retiro == rev_intencion
        assert "state=ENABLED" in rev_retiro
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        escritor3, puts3 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.repone(conn, retiro_ids=(rid,), go="go dueno", escritor=escritor3) == 1
        assert puts3 == [("ad-1", "ENABLED")]


@_skip_db
def test_r09r5_3b_put_500_que_aplica_deja_intencion_pendiente():
    """R09-r5 B3 (3b, VT/VQ): el PUT aplica pero responde 500 y el
    readback va una lectura atrasado (ve ENABLED): NO es rechazo,
    es incierto: sin fila de retiro y con intencion pendiente (la
    reconciliacion la sella al leer PAUSED)."""
    with _db_i4("orbit_i4_r53b") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(
            _fila("ad-1", "ENABLED"), puts_500_que_aplican={"ad-1"}, stale_lists={3}
        )
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        fila = conn.execute(
            "SELECT cerrada_el IS NULL, retiro_id FROM anuncio_retiro_intencion"
        ).fetchone()
        assert fila == (True, None)


@_skip_db
def test_r09r5_nq_reintento_cierra_intencion_con_retiro_ya_sellado(monkeypatch):
    """R09-r5 B3 (10 n/q, 11 E): muerte entre el sello y el cierre:
    retiro sellado + intencion pendiente. El reintento NO manda PUT
    ni duplica la fila: cierra la intencion apuntando al retiro."""
    with _db_i4("orbit_i4_r5nq") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        class Muerte(BaseException):
            pass

        def _muere(*args, **kwargs):
            raise Muerte("kill -9 entre sello y cierre")

        monkeypatch.setattr(ra, "_cierra_intencion", _muere)
        with pytest.raises(Muerte):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        assert conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone() == (1,)
        plan2 = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        escritor2, puts2 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, plan2, go="go dueno", escritor=escritor2) == 0
        assert puts2 == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        fila = conn.execute(
            "SELECT cerrada_el IS NOT NULL, retiro_id FROM anuncio_retiro_intencion"
        ).fetchone()
        assert fila == (True, rid)


@_skip_db
def test_r09r5_d3_archivado_por_fuera_cierra_intencion_sin_fallar():
    """R09-r5 B3 (11 D3/B, 3c): intencion pendiente de un anuncio que
    se archiva por fuera: el reintento cierra la intencion con
    detalle y NO falla (nunca 502 cada vez; el vigia no se atora)."""
    with _db_i4("orbit_i4_r5d3") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        # Muerte antes del PUT: intencion pendiente, Amazon intacto.
        import app.ads.archivar as archivar_real

        real_pausar = archivar_real.pausar_anuncios

        class Muerte(BaseException):
            pass

        def _muere(*args, **kwargs):
            raise Muerte("kill -9 antes del PUT")

        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        archivar_real.pausar_anuncios = _muere
        try:
            with pytest.raises(Muerte):
                ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        finally:
            archivar_real.pausar_anuncios = real_pausar
        # El anuncio se archiva por fuera; el reintento converge.
        plan2 = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        assert [a.ad_id_externo for a in plan2.anuncios] == ["ad-1"]
        escritor2, puts2 = _escritor(_fila("ad-1", "ARCHIVED"))
        assert ra.ejecuta_retiro(conn, plan2, go="go dueno", escritor=escritor2) == 0
        assert puts2 == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        fila = conn.execute(
            "SELECT cerrada_el IS NOT NULL, retiro_id IS NULL, detalle"
            " FROM anuncio_retiro_intencion"
        ).fetchone()
        assert fila[0] and fila[1] and "ARCHIVED" in fila[2]


def _intencion_pendiente(conn, lid, external, *, motivo="sin_pedidos", impulso_id=None):
    """Siembra ad + intencion pendiente con reversa ENABLED; devuelve iid."""
    _bolsa(conn, lid, external)
    (ent,) = conn.execute(
        "SELECT id FROM ad_entity WHERE kind = 'product_ad' AND external_id = %s",
        (external,),
    ).fetchone()
    (iid,) = conn.execute(
        "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, actor, creada_el) VALUES ('amazon_mx', %s, 'pausa', 'retiro-r5', %s,"
        " %s, %s, 'ag-vieja', 'c-vieja', 'SS-1', 'B0AAAAAAAA',"
        " 'adGroupId=ag-vieja campaignId=c-vieja sku=SS-1 state=ENABLED asin=B0AAAAAAAA',"
        " 'dueno', now()) RETURNING id",
        (motivo, impulso_id, ent, external),
    ).fetchone()
    conn.commit()
    return iid


@_skip_db
def test_r09r5_reconcilia_paused_sella():
    """R09-r5 B3 (11 A): pendiente + PAUSED en Amazon (nadie
    reintenta): la reconciliacion sella el retiro con la reversa de
    la intencion y la cierra, sin PUT."""
    with _db_i4("orbit_i4_r5ra") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        escritor, puts = _escritor(_fila("ad-1", "PAUSED"))
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert puts == []
        assert res == [(iid, "sellado")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        (rev,) = conn.execute("SELECT reversa FROM anuncio_retiro").fetchone()
        assert "state=ENABLED" in rev
        assert conn.execute(
            "SELECT cerrada_el IS NOT NULL FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
        ).fetchone() == (True,)


@_skip_db
def test_r09r5_reconcilia_enabled_reintenta():
    """R09-r5 B3 (iii): pendiente + ENABLED en Amazon: la
    reconciliacion reintenta el PUT y sella."""
    with _db_i4("orbit_i4_r5rb") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        assert res == [(iid, "sellado")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)


@_skip_db
def test_r09r5_reconcilia_archived_cierra():
    """R09-r5 B3 (iii): pendiente + ARCHIVED en Amazon: cierra sin
    retiro, con detalle, sin PUT."""
    with _db_i4("orbit_i4_r5rc") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        escritor, puts = _escritor(_fila("ad-1", "ARCHIVED"))
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert puts == []
        assert res == [(iid, "cerrado")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        (detalle,) = conn.execute(
            "SELECT detalle FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
        ).fetchone()
        assert "ARCHIVED" in detalle


@_skip_db
def test_r09r5_reconcilia_inexistente_cierra():
    """R09-r5 B3 (iii): pendiente + ad inexistente en Amazon: cierra
    sin retiro, con detalle, sin PUT."""
    with _db_i4("orbit_i4_r5rd") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        escritor, puts = _escritor({})
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert puts == []
        assert res == [(iid, "cerrado")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)


@_skip_db
def test_r09r5_reconcilia_impulso_terminado():
    """R09-r5 B3 (16 VF): pendiente de motivo impulso con el impulso
    ya terminado: la reconciliacion la resuelve igual (PAUSED sella)."""
    with _db_i4("orbit_i4_r5re") as conn:
        _pid, lid = _producto(conn)
        lote = _lote(conn, "impulso-9-terminado")
        grupo = _grupo(conn, lote)
        (imp,) = conn.execute(
            "INSERT INTO impulso (platform, listing_id, product_id, grupo_id, lote,"
            " subfamilia, tope_monto, tope_moneda, presupuesto_diario, plazo_dias,"
            " lanzado_el, actor, go_literal) VALUES ('amazon_mx', %s, %s, %s, %s,"
            " 'aros', 350, 'MXN', 25, 30, now(), 'dueno', 'go') RETURNING id",
            (lid, _pid, grupo, lote),
        ).fetchone()
        conn.execute(
            "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
            " impresiones, clics, gasto, gasto_moneda, pedidos, veredicto, es_final, hecho)"
            " VALUES (%s, now()::date - 1, now(), 5, 100, 5, 50, 'MXN', 1, 'vende', TRUE,"
            " 'graduado')",
            (imp,),
        )
        conn.commit()
        iid = _intencion_pendiente(conn, lid, "ad-1", motivo="impulso", impulso_id=imp)
        escritor, puts = _escritor(_fila("ad-1", "PAUSED"))
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert res == [(iid, "sellado")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)


@_skip_db
def test_r09r5_reconcilia_list_caido_no_tumba():
    """R09-r5 B3 (iii): LIST caido: la pendiente sigue pendiente y la
    reconciliacion no levanta (manana sera)."""
    with _db_i4("orbit_i4_r5rf") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        _bolsa(conn, lid, "ad-2", camp="c-vieja-2", ag="ag-vieja-2")
        (ent2,) = conn.execute(
            "SELECT id FROM ad_entity WHERE kind = 'product_ad' AND external_id = 'ad-2'"
        ).fetchone()
        (iid2,) = conn.execute(
            "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro,"
            " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
            " reversa, actor, creada_el) VALUES ('amazon_mx', 'sin_pedidos', 'pausa',"
            " 'retiro-r5', %s, 'ad-2', 'ag-vieja-2', 'c-vieja-2', 'SS-1', 'B0AAAAAAAA',"
            " 'adGroupId=ag-vieja-2 campaignId=c-vieja-2 sku=SS-1 state=ENABLED"
            " asin=B0AAAAAAAA', 'dueno', now()) RETURNING id",
            (ent2,),
        ).fetchone()
        conn.commit()
        escritor, puts = _escritor(
            {**_fila("ad-1", "PAUSED"), **_fila("ad-2", "PAUSED")},
            lists_que_fallan=set(range(1, 40)),
        )
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert res == []
        assert puts == []
        assert conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone() == (2,)
        assert {iid, iid2} == {
            r[0]
            for r in conn.execute(
                "SELECT id FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
            ).fetchall()
        }


def _retiro_listo(conn):
    """Producto + bolsa + plan sin_pedidos; devuelve (lid, plan)."""
    _pid, lid = _producto(conn)
    _bolsa(conn, lid, "ad-1")
    plan = ra.planea_retiro(
        conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
    )
    return lid, plan


def _cuenta(conn):
    """(retiros, intenciones pendientes, intenciones cerradas)."""
    return (
        conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone()[0],
        conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone()[0],
        conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NOT NULL"
        ).fetchone()[0],
    )


class _Muerte10(BaseException):
    pass


def _plan2(conn, lid):
    return ra.planea_retiro(
        conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
    )


@_skip_db
def test_r09r5_10b_put_207_con_error_cierra_y_reintento_sella():
    """R09-r5 B3 (10 b): PUT 207 con error por item = rechazo limpio:
    cierra sin fila; el reintento (Amazon ya acepta) sella."""
    with _db_i4("orbit_i4_r510b") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), puts_207_con_error={"ad-1"})
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert _cuenta(conn) == (0, 0, 1)
        escritor2, puts2 = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert puts2 == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (1, 0, 2)


@_skip_db
def test_r09r5_10c_red_caida_sin_aplicar_pendiente_y_reintento_sella():
    """R09-r5 B3 (10 c): la conexion falla y el PUT no llega: incierto
    (pendiente, sin fila); el reintento manda el PUT y sella."""
    with _db_i4("orbit_i4_r510c") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), puts_red_caida={"ad-1"})
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == []
        assert _cuenta(conn) == (0, 1, 0)
        escritor2, puts2 = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert puts2 == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (1, 0, 1)


@_skip_db
def test_r09r5_10d_timeout_que_aplica_pendiente_y_reintento_sella():
    """R09-r5 B3 (10 d): PUT aplica pero expira, readback al dia: el
    readback manda (sella en el primer intento, con nota de envio
    incierto) y Regresar vuelve a ENABLED."""
    with _db_i4("orbit_i4_r510d") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), puts_timeout_que_aplican={"ad-1"})
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 1
        assert puts == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (1, 0, 1)
        (rev,) = conn.execute("SELECT reversa FROM anuncio_retiro").fetchone()
        assert "state=ENABLED" in rev
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        escritor3, puts3 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.repone(conn, retiro_ids=(rid,), go="go dueno", escritor=escritor3) == 1
        assert puts3 == [("ad-1", "ENABLED")]


@_skip_db
def test_r09r5_10e_500_que_aplica_pendiente_y_reintento_sella():
    """R09-r5 B3 (10 e): PUT aplica pero responde 500, readback al dia:
    el readback manda (sella en el primer intento, con nota de envio
    incierto)."""
    with _db_i4("orbit_i4_r510e") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), puts_500_que_aplican={"ad-1"})
        assert ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor) == 1
        assert puts == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (1, 0, 1)


@_skip_db
def test_r09r5_10f_timeout_que_aplica_con_stale_queda_pendiente():
    """R09-r5 B3 (10 f): PUT aplica pero expira y el readback va una
    lectura atrasado: incierto (pendiente, sin fila), nunca rechazo."""
    with _db_i4("orbit_i4_r510f") as conn:
        _lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(
            _fila("ad-1", "ENABLED"), puts_timeout_que_aplican={"ad-1"}, stale_lists={3}
        )
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (0, 1, 0)


@_skip_db
def test_r09r5_10g_expira_aplica_tarde_y_reintento_sella():
    """R09-r5 B3 (10 g): el PUT expira sin aplicar y Amazon lo aplica
    DESPUES del readback: pendiente; el reintento (ya PAUSED) sella
    con la reversa de la intencion."""
    with _db_i4("orbit_i4_r510g") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), puts_timeout_sin_aplicar={"ad-1"})
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == []
        assert _cuenta(conn) == (0, 1, 0)
        # Amazon aplico tarde el PUT expirado: el reintento lo confirma.
        escritor2, puts2 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert puts2 == []
        (rev,) = conn.execute("SELECT reversa FROM anuncio_retiro").fetchone()
        assert "state=ENABLED" in rev
        assert _cuenta(conn) == (1, 0, 1)


@_skip_db
def test_r09r5_10p_previa_interna_caida_put_nunca_sale():
    """R09-r5 B3 (10 p): cae la previa interna de pausar (el PUT nunca
    sale): pendiente, cero PUT; el reintento manda el PUT y sella."""
    with _db_i4("orbit_i4_r510p") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), lists_que_fallan=set(range(2, 20)))
        with pytest.raises(RuntimeError, match="retiro no confirmado"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert puts == []
        assert _cuenta(conn) == (0, 1, 0)
        escritor2, puts2 = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert puts2 == [("ad-1", "PAUSED")]


@_skip_db
def test_r09r5_10l_muerte_entre_put_y_readback_reintento_sella(monkeypatch):
    """R09-r5 B3 (10 l): muerte entre el PUT y el readback: pendiente
    con Amazon PAUSED; el reintento confirma y sella."""
    import app.ads.archivar as archivar_real

    with _db_i4("orbit_i4_r510l") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def _muere(*args, **kwargs):
            raise _Muerte10("kill -9 entre PUT y readback")

        monkeypatch.setattr(archivar_real, "estados_actuales", _muere)
        with pytest.raises(_Muerte10):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        assert puts == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (0, 1, 0)
        escritor2, puts2 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert _cuenta(conn) == (1, 0, 1)


@_skip_db
def test_r09r5_10m_muerte_entre_readback_y_sello_reintento_sella(monkeypatch):
    """R09-r5 B3 (10 m): muerte entre el readback y el INSERT: pendiente
    con Amazon PAUSED; el reintento confirma y sella."""
    with _db_i4("orbit_i4_r510m") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def _muere(*args, **kwargs):
            raise _Muerte10("kill -9 entre readback y sello")

        monkeypatch.setattr(ra, "_sella_retiro", _muere)
        with pytest.raises(_Muerte10):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        assert puts == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (0, 1, 0)
        escritor2, _puts2 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert _cuenta(conn) == (1, 0, 1)


@_skip_db
def test_r09r5_10r_base_falla_en_intencion_sin_put_y_reintento_sella(monkeypatch):
    """R09-r5 B3 (10 r): la base rechaza el INSERT de la intencion: sin
    PUT, sin intencion, sin fila; el reintento abre y sella."""
    with _db_i4("orbit_i4_r510r") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def _falla(*args, **kwargs):
            raise RuntimeError("la base rechazo el INSERT de la intencion (simulado)")

        monkeypatch.setattr(ra, "_abre_intencion", _falla)
        with pytest.raises(RuntimeError, match="INSERT de la intencion"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        assert puts == []
        assert _cuenta(conn) == (0, 0, 0)
        escritor2, puts2 = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert puts2 == [("ad-1", "PAUSED")]


@_skip_db
def test_r09r5_10s_muerte_antes_de_intencion_reintento_sella(monkeypatch):
    """R09-r5 B3 (10 s): muerte entre la previa y la intencion: sin
    PUT, sin intencion, sin fila; el reintento abre y sella."""
    with _db_i4("orbit_i4_r510s") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def _muere(*args, **kwargs):
            raise _Muerte10("kill -9 antes de la intencion")

        monkeypatch.setattr(ra, "_abre_intencion", _muere)
        with pytest.raises(_Muerte10):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        assert puts == []
        assert _cuenta(conn) == (0, 0, 0)
        escritor2, puts2 = _escritor(_fila("ad-1", "ENABLED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert puts2 == [("ad-1", "PAUSED")]


@_skip_db
def test_r09r5_10o_base_falla_en_sello_reintento_sella(monkeypatch):
    """R09-r5 B3 (10 o): la base rechaza el INSERT del retiro: pendiente
    con Amazon PAUSED; el reintento confirma y sella."""
    with _db_i4("orbit_i4_r510o") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def _falla(*args, **kwargs):
            raise RuntimeError("la base rechazo el INSERT del retiro (simulado)")

        monkeypatch.setattr(ra, "_sella_retiro", _falla)
        with pytest.raises(RuntimeError, match="INSERT del retiro"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        assert puts == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (0, 1, 0)
        escritor2, _puts2 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 1
        assert _cuenta(conn) == (1, 0, 1)


@_skip_db
def test_r09r5_10q_base_falla_en_cierre_reintento_cierra(monkeypatch):
    """R09-r5 B3 (10 q): la base rechaza el cierre (excepcion, no
    muerte): retiro sellado + pendiente; el reintento cierra sin PUT
    ni fila nueva."""
    with _db_i4("orbit_i4_r510q") as conn:
        lid, plan = _retiro_listo(conn)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def _falla(*args, **kwargs):
            raise RuntimeError("la base rechazo el cierre de la intencion (simulado)")

        monkeypatch.setattr(ra, "_cierra_intencion", _falla)
        with pytest.raises(RuntimeError, match="cierre de la intencion"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        assert puts == [("ad-1", "PAUSED")]
        assert _cuenta(conn) == (1, 1, 0)
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        escritor2, puts2 = _escritor(_fila("ad-1", "PAUSED"))
        assert ra.ejecuta_retiro(conn, _plan2(conn, lid), go="go dueno", escritor=escritor2) == 0
        assert puts2 == []
        assert _cuenta(conn) == (1, 0, 1)
        assert conn.execute("SELECT retiro_id FROM anuncio_retiro_intencion").fetchone() == (rid,)


@_skip_db
def test_r09r5_r05_sello_confirma_antes_de_cerrar(monkeypatch):
    """R09-r5 B6 (R05): muerte entre el sello y el cierre, conexion
    SIN autocommit (como el cron): el retiro persiste (su commit es
    propio) y la intencion queda pendiente."""
    with _db_i4("orbit_i4_r5r05") as conn:
        conn.autocommit = False
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        conn.commit()
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def _muere(*args, **kwargs):
            raise _Muerte10("kill -9 entre sello y cierre")

        monkeypatch.setattr(ra, "_cierra_intencion", _muere)
        with pytest.raises(_Muerte10):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        # La muerte suelta lo no confirmado: solo persiste lo que
        # trajo su propio commit.
        conn.rollback()
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL"
        ).fetchone() == (1,)


@_skip_db
def test_r09r5_r06_cierre_confirma(monkeypatch):
    """R09-r5 B6 (R06): muerte tras el cierre (antes del commit del
    llamador), conexion SIN autocommit: el cierre persiste (su
    commit es propio)."""
    with _db_i4("orbit_i4_r5r06") as conn:
        conn.autocommit = False
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        plan = ra.planea_retiro(
            conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
        )
        conn.commit()
        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        real_cierra = ra._cierra_intencion

        def _muere(*args, **kwargs):
            real_cierra(*args, **kwargs)
            raise _Muerte10("kill -9 tras el cierre")

        monkeypatch.setattr(ra, "_cierra_intencion", _muere)
        with pytest.raises(_Muerte10):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        monkeypatch.undo()
        # La muerte suelta lo no confirmado: solo persiste lo que
        # trajo su propio commit.
        conn.rollback()
        assert conn.execute(
            "SELECT cerrada_el IS NOT NULL FROM anuncio_retiro_intencion"
        ).fetchone() == (True,)


@_skip_db
def test_r09r5_reconcilia_rechazo_cierra():
    """R09-r5 B3 (iii, decision 1): pendiente + ENABLED + PUT
    rechazado (400): la reconciliacion cierra sin retiro."""
    with _db_i4("orbit_i4_r5rg") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"), rechaza_put={"ad-1"})
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert puts == []  # el 400 no aplica nada
        assert res == [(iid, "cerrado")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        (detalle,) = conn.execute(
            "SELECT detalle FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
        ).fetchone()
        assert "fallo" in detalle


@_skip_db
def test_r09r5_reconcilia_incierto_sigue_pendiente():
    """R09-r5 B6 (iii): pendiente + ENABLED + PUT incierto (500 que
    aplica + readback atrasado): sigue pendiente, sin fila."""
    with _db_i4("orbit_i4_r5rh") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        escritor, puts = _escritor(
            _fila("ad-1", "ENABLED"), puts_500_que_aplican={"ad-1"}, stale_lists={3}
        )
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        assert res == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        assert conn.execute(
            "SELECT cerrada_el IS NULL FROM anuncio_retiro_intencion WHERE id = %s",
            (iid,),
        ).fetchone() == (True,)


def _pausa_con_resultado(resultado):
    """Sustituto de archivar.pausar_anuncios: el anuncio cambio entre
    la lectura del llamador (ENABLED) y la previa interna de pausar,
    que ya lo ve pausado por fuera o ya no lo encuentra."""
    from app.ads import archivar

    def _fake(escritor, ad_ids, ejecutar=True):
        return [archivar.ResultadoAnuncio(ad_ids[0], "ENABLED", resultado)]

    return _fake


@_skip_db
def test_r09r6_r12b_reconcilia_ya_estaba_sella(monkeypatch):
    """R09-r6 (R12b): en la reconciliacion, si el anuncio se pauso por
    fuera entre la lectura y el PUT (ya_estaba), sella el retiro y
    cierra la intencion con su fila; no queda pendiente."""
    from app.ads import archivar

    with _db_i4("orbit_i4_r6r12b") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        monkeypatch.setattr(archivar, "pausar_anuncios", _pausa_con_resultado("ya_estaba"))
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert res == [(iid, "sellado")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        assert conn.execute(
            "SELECT cerrada_el IS NOT NULL, retiro_id IS NOT NULL"
            " FROM anuncio_retiro_intencion WHERE id = %s",
            (iid,),
        ).fetchone() == (True, True)


@_skip_db
def test_r09r6_r15a_ejecuta_no_existe_cierra_intencion(monkeypatch):
    """R09-r6 (R15a): al ejecutar, si el anuncio desaparece entre la
    lectura previa y la previa de pausar (no_existe), la intencion
    se cierra sin fila de retiro (no pendiente) y el fallo se nombra."""
    from app.ads import archivar

    with _db_i4("orbit_i4_r6r15a") as conn:
        _lid, plan = _retiro_listo(conn)
        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        monkeypatch.setattr(archivar, "pausar_anuncios", _pausa_con_resultado("no_existe"))
        with pytest.raises(RuntimeError, match="cerrada"):
            ra.ejecuta_retiro(conn, plan, go="go dueno", escritor=escritor)
        assert _cuenta(conn) == (0, 0, 1)


@_skip_db
def test_r09r6_r15b_reconcilia_no_existe_cierra(monkeypatch):
    """R09-r6 (R15b): en la reconciliacion, si el anuncio desaparece
    entre la lectura y la previa de pausar (no_existe), la intencion
    se cierra sin fila de retiro; no sigue pendiente."""
    from app.ads import archivar

    with _db_i4("orbit_i4_r6r15b") as conn:
        _pid, lid = _producto(conn)
        iid = _intencion_pendiente(conn, lid, "ad-1")
        escritor, _puts = _escritor(_fila("ad-1", "ENABLED"))
        monkeypatch.setattr(archivar, "pausar_anuncios", _pausa_con_resultado("no_existe"))
        res = ra.reconcilia_intenciones(conn, escritor=escritor)
        assert res == [(iid, "cerrado")]
        assert _cuenta(conn) == (0, 0, 1)
