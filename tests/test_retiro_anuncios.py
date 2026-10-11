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


def _bolsa(conn, lid, external, *, camp="c-vieja", ag="ag-vieja", estado="ENABLED"):
    """Campana + ad group + product ad con listing y estado; devuelve
    (camp_id, ag_id, pa_id)."""
    camp_id = _entidad(conn, "amazon_mx", "campaign", camp)
    ag_id = _entidad(conn, "amazon_mx", "ad_group", ag, parent=camp_id)
    pa_id = _entidad(conn, "amazon_mx", "product_ad", external, parent=ag_id, listing_id=lid)
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, now())",
        (pa_id, estado),
    )
    return camp_id, ag_id, pa_id


def _escritor(filas: dict[str, dict]):
    """Escritor sobre transporte falso: list con filas completas, PUT que
    cambia el estado en vivo, create que devuelve un adId nuevo."""
    puts: list[tuple[str, str]] = []
    vivos = {k: dict(v) for k, v in filas.items()}
    nuevo = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token_response()
        cuerpo = json.loads(request.content) if request.content else {}
        if request.method == "PUT" and request.url.path == "/sp/productAds":
            for entrada in cuerpo.get("productAds", []):
                puts.append((str(entrada["adId"]), str(entrada["state"])))
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
        pedidos = cuerpo.get("adIdFilter", {}).get("include", [])
        return httpx.Response(200, json={"productAds": [vivos[p] for p in pedidos if p in vivos]})

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
        assert conn.execute(
            "SELECT ad_id_nuevo, estado_nuevo FROM anuncio_reposicion WHERE retiro_id = %s",
            (ret[0],),
        ).fetchone() == ("ad-nuevo-1", "PAUSED")
