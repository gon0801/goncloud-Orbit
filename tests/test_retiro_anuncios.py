"""Retiro de product ads de bolsas viejas y su reposicion (BIDS 02, I.4). Con base."""

from __future__ import annotations

import json

import httpx
import pytest
from test_fabrica_migracion import (
    _entidad,
    _grupo,
    _lote,
    _producto,
    db_fabrica,
)
from test_migracion_0065 import SQL64, SQL65
from test_migracion_0066 import SQL66
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
):
    """Escritor sobre transporte falso: list con filas completas, PUT que
    cambia el estado en vivo, create que devuelve un adId nuevo.
    lists_que_fallan: numeros de LIST (1-based) que responden 500.
    stale_lists: numeros de LIST que ven el estado previo al ultimo PUT.
    rechaza_put: adIds cuyo PUT responde 400.
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
def test_ejecuta_relectura_caida_conserva_fila():
    """R09 B5 (f/A): el PUT pasa y la relectura da 500 (con reintentos):
    el anuncio queda pausado CON fila (reponible) y el error lo nombra."""
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
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)


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
def test_ejecuta_relectura_atrasada_conserva_fila():
    """R09 B5 (C): relectura stale (ve ENABLED tras el PUT): sin_confirmar
    conserva la fila y el error la nombra."""
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
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)


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
