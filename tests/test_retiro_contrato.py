"""Contrato del retiro de product ads (AUTO-03 G.5). Escritura en Amazon y en la base,
con escritor falso: resultado por anuncio, autoria adoptada, repone sin recrear,
autorizacion de decision verificada antes del PUT y reconciliacion que no toca las
intenciones de una decision."""

from __future__ import annotations

import datetime as dt

import pytest
from test_fabrica_migracion import _entidad, _producto
from test_impulso_io import HOY, _con_margen, _db_i3, _impulso_vivo, _observa, _Pausador
from test_retiro_anuncios import _bolsa, _db_i4, _escritor, _fila, _skip_db

from app import impulso_io as io
from app import retiro_anuncios as ra

DUENO = ra.AutorizacionHumana("SACAR 1 ANUNCIOS", "dueno")


def _plan(conn, lid):
    return ra.planea_retiro(
        conn, "amazon_mx", listing_ids=(lid,), motivo="sin_pedidos", conservar_grupo_id=None
    )


def _filas(*ads, estado="ENABLED"):
    filas = {}
    for i, ad in enumerate(ads):
        filas.update(_fila(ad, estado, ag=f"ag-{i}", camp=f"c-{i}"))
    return filas


@_skip_db
def test_lote_de_5_con_4_confirmados_devuelve_4_de_5_por_anuncio():
    with _db_i4("orbit_g5_lote") as conn:
        _pid, lid = _producto(conn)
        ads = [f"ad-{i}" for i in range(5)]
        for i, ad in enumerate(ads):
            _bolsa(conn, lid, ad, ag=f"ag-{i}", camp=f"c-{i}")
        escritor, puts = _escritor(_filas(*ads), rechaza_put=frozenset({"ad-3"}))
        res = ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
        assert [(r.ad_id_externo, r.resultado) for r in res] == [
            ("ad-0", "retirado"),
            ("ad-1", "retirado"),
            ("ad-2", "retirado"),
            ("ad-3", "rechazado"),
            ("ad-4", "retirado"),
        ]
        assert (sum(r.fuera for r in res), len(res)) == (4, 5)
        assert "intencion" in res[3].detalle
        assert conn.execute(
            "SELECT ad_id_externo, autoria, actor FROM anuncio_retiro ORDER BY ad_id_externo"
        ).fetchall() == [(ad, "confirmada", "dueno") for ad in ads if ad != "ad-3"]
        assert {r.retiro_id for r in res if r.fuera} == {
            f[0] for f in conn.execute("SELECT id FROM anuncio_retiro").fetchall()
        }
        assert ra.no_resueltos(res).startswith("retiro no confirmado (4/5 retirados)")


@_skip_db
def test_retomar_tras_put_incierto_sella_adoptada_y_put_propio_confirmada():
    with _db_i4("orbit_g5_adopta") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-aplico", ag="ag-a", camp="c-a")
        _bolsa(conn, lid, "ad-no-aplico", ag="ag-b", camp="c-b")
        filas = {**_fila("ad-aplico", "ENABLED"), **_fila("ad-no-aplico", "ENABLED")}
        # LIST 1 es la previa global; desde el 3 todo LIST cae: el PUT de
        # ad-aplico sale y su readback no confirma; ad-no-aplico ya no
        # alcanza a mandar el suyo (su previa cae).
        escritor, puts = _escritor(filas, lists_que_fallan=frozenset(range(3, 40)))
        primero = ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
        assert [r.resultado for r in primero] == ["pendiente", "pendiente"]
        assert puts == [("ad-aplico", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)
        # Retoma: uno ya esta PAUSED (el PUT incierto aplico), el otro sigue ENABLED.
        escritor2, puts2 = _escritor(
            {**_fila("ad-aplico", "PAUSED"), **_fila("ad-no-aplico", "ENABLED")}
        )
        segundo = ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor2)
        assert [(r.ad_id_externo, r.resultado) for r in segundo] == [
            ("ad-aplico", "adoptado"),
            ("ad-no-aplico", "retirado"),
        ]
        assert puts2 == [("ad-no-aplico", "PAUSED")]
        assert conn.execute(
            "SELECT r.ad_id_externo, r.autoria, i.detalle FROM anuncio_retiro r"
            " JOIN anuncio_retiro_intencion i ON i.retiro_id = r.id ORDER BY r.ad_id_externo"
        ).fetchall() == [
            ("ad-aplico", "adoptada", "adoptado: ya estaba PAUSED al confirmar"),
            ("ad-no-aplico", "confirmada", "sello del readback"),
        ]


@_skip_db
def test_repone_de_un_archived_no_hace_post(monkeypatch):
    with _db_i4("orbit_g5_archived") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        escritor, _ = _escritor(_fila("ad-1", "ENABLED"))
        ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        archivado, _ = _escritor(_fila("ad-1", "ARCHIVED"))

        def _post_prohibido(*a, **k):
            raise AssertionError("repone mando POST /sp/productAds")

        monkeypatch.setattr(ra.archivar, "reponer_anuncios", _post_prohibido)
        (res,) = ra.repone(
            conn,
            retiro_ids=(rid,),
            autorizacion=ra.AutorizacionHumana("REGRESAR 1 ANUNCIOS", "dueno"),
            escritor=archivado,
        )
        assert (res.resultado, res.ad_id_nuevo) == ("requiere_recreacion", None)
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)


@_skip_db
def test_repone_de_un_adoptado_no_hace_put_sin_incluye_adoptados():
    with _db_i4("orbit_g5_repone_adoptado") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        escritor, _ = _escritor(_fila("ad-1", "ENABLED"), lists_que_fallan=frozenset(range(3, 40)))
        ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
        pausado, _ = _escritor(_fila("ad-1", "PAUSED"))
        (adoptado,) = ra.ejecuta_retiro(
            conn, _plan(conn, lid), autorizacion=DUENO, escritor=pausado
        )
        assert adoptado.resultado == "adoptado"
        regreso = ra.AutorizacionHumana("REGRESAR 1 ANUNCIOS", "dueno")
        vista, puts = _escritor(_fila("ad-1", "PAUSED"))
        (res,) = ra.repone(
            conn, retiro_ids=(adoptado.retiro_id,), autorizacion=regreso, escritor=vista
        )
        assert res.resultado == "adoptado_sin_reposicion"
        assert puts == []
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)
        (res,) = ra.repone(
            conn,
            retiro_ids=(adoptado.retiro_id,),
            autorizacion=regreso,
            incluye_adoptados=True,
            escritor=vista,
        )
        assert (res.resultado, res.estado_nuevo) == ("repuesto", "ENABLED")
        assert puts == [("ad-1", "ENABLED")]


def _decision_de_pausa(conn, product_ad, *, estado="applying", vence="-1 hour", cuota=True):
    """Decision pause sobre el product_ad, su fila de cola y su apply_attempt."""
    config = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('g5', '{}'::jsonb) RETURNING id"
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_mx') RETURNING id"
    ).fetchone()[0]
    dec = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " data_observed_at, window_start, window_end, inputs)"
        " VALUES (%s, %s, 'pause', %s, now() - interval '40 days', CURRENT_DATE - 60,"
        " CURRENT_DATE - 30, '{}'::jsonb) RETURNING id",
        (ciclo, product_ad, config),
    ).fetchone()[0]
    q = conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo, estado,"
        " vence_el, request_payload) VALUES ('amazon_mx', %s, 'pause', %s, 'live',"
        " 'pending_veto', now() + %s::interval, '{}'::jsonb) RETURNING id",
        (product_ad, dec, vence),
    ).fetchone()[0]
    if estado in ("released", "applying"):
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = now() WHERE id = %s", (q,)
        )
    if estado == "applying":
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = now() WHERE id = %s", (q,)
        )
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
        " VALUES (%s, 1, 'normal', '{}'::jsonb, %s)",
        (dec, cuota),
    )
    conn.commit()
    return dec, q


@_skip_db
def test_autorizacion_de_decision_falsa_no_manda_ningun_put():
    with _db_i4("orbit_g5_decision") as conn:
        _pid, lid = _producto(conn)
        _c, _a, pa = _bolsa(conn, lid, "ad-1")
        _c, _a, otro = _bolsa(conn, lid, "ad-otro", ag="ag-o", camp="c-o")
        dec, q = _decision_de_pausa(conn, pa)
        dec_otro, q_otro = _decision_de_pausa(conn, otro)
        sin_cuota_pa = _entidad(conn, "amazon_mx", "product_ad", "ad-sc", listing_id=lid)
        dec_sc, q_sc = _decision_de_pausa(conn, sin_cuota_pa, cuota=False)
        _c, _a, en_veto = _bolsa(conn, lid, "ad-veto", ag="ag-v", camp="c-v")
        dec_v, q_v = _decision_de_pausa(conn, en_veto, estado="pending_veto", vence="+40 hours")
        plan = ra.plan_de_decision(conn, dec)
        assert [a.ad_id_externo for a in plan.anuncios] == ["ad-1"]
        assert plan.motivo == "producto_sin_ventas"
        escritor, puts = _escritor({**_fila("ad-1", "ENABLED"), **_fila("ad-veto", "ENABLED")})
        falsas = [
            ra.AutorizacionDeDecision(dec + 999, q),  # decision inexistente
            ra.AutorizacionDeDecision(dec, q_otro),  # fila de cola de otra decision
            ra.AutorizacionDeDecision(dec_otro, q_otro),  # decision de otro product_ad
            ra.AutorizacionDeDecision(dec_sc, q_sc),  # sin cuota cobrada (y otro anuncio)
        ]
        for autorizacion in falsas:
            with pytest.raises(ra.AutorizacionInvalida):
                ra.ejecuta_retiro(conn, plan, autorizacion=autorizacion, escritor=escritor)
        with pytest.raises(ra.AutorizacionInvalida):  # veto sin vencer, cola sin reclamar
            ra.ejecuta_retiro(
                conn,
                ra.plan_de_decision(conn, dec_v),
                autorizacion=ra.AutorizacionDeDecision(dec_v, q_v),
                escritor=escritor,
            )
        with pytest.raises(ra.AutorizacionInvalida):  # el motivo nuevo no admite humano
            ra.ejecuta_retiro(conn, plan, autorizacion=DUENO, escritor=escritor)
        with pytest.raises(ra.AutorizacionInvalida):  # una decision no ampara un plan humano
            ra.ejecuta_retiro(
                conn, _plan(conn, lid), autorizacion=ra.AutorizacionDeDecision(dec, q)
            )
        assert puts == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro_intencion").fetchone() == (0,)
        # La verdadera si retira, con la decision en la intencion y la cola de actor.
        (res,) = ra.ejecuta_retiro(
            conn, plan, autorizacion=ra.AutorizacionDeDecision(dec, q), escritor=escritor
        )
        assert res.resultado == "retirado"
        assert puts == [("ad-1", "PAUSED")]
        assert conn.execute(
            "SELECT i.decision_id, r.motivo, r.actor, r.autoria FROM anuncio_retiro_intencion i"
            " JOIN anuncio_retiro r ON r.id = i.retiro_id"
        ).fetchone() == (dec, "producto_sin_ventas", f"apply_queue:{q}", "confirmada")


@_skip_db
def test_reconcilia_no_toca_la_intencion_de_una_decision_aunque_lea_enabled():
    with _db_i4("orbit_g5_reconcilia") as conn:
        _pid, lid = _producto(conn)
        _c, _a, pa = _bolsa(conn, lid, "ad-dec")
        dec, q = _decision_de_pausa(conn, pa)
        incierto, _ = _escritor(
            _fila("ad-dec", "ENABLED"), puts_timeout_sin_aplicar=frozenset({"ad-dec"})
        )
        (res,) = ra.ejecuta_retiro(
            conn,
            ra.plan_de_decision(conn, dec),
            autorizacion=ra.AutorizacionDeDecision(dec, q),
            escritor=incierto,
        )
        assert res.resultado == "pendiente"
        # Control: una intencion humana pendiente al lado si se resuelve.
        _bolsa(conn, lid, "ad-humano", ag="ag-h", camp="c-h")
        humano, _ = _escritor(
            _fila("ad-humano", "ENABLED"), puts_timeout_sin_aplicar=frozenset({"ad-humano"})
        )
        ra.ejecuta_retiro(
            conn,
            ra.PlanRetiro(
                "amazon_mx",
                "sin_pedidos",
                tuple(a for a in _plan(conn, lid).anuncios if a.ad_id_externo == "ad-humano"),
                "h",
            ),
            autorizacion=DUENO,
            escritor=humano,
        )
        vista, puts = _escritor(
            {**_fila("ad-dec", "ENABLED"), **_fila("ad-humano", "ENABLED", ag="ag-h", camp="c-h")}
        )
        resueltas = ra.reconcilia_intenciones(conn, escritor=vista)
        (iid_dec,) = conn.execute(
            "SELECT id FROM anuncio_retiro_intencion WHERE decision_id = %s", (dec,)
        ).fetchone()
        assert iid_dec not in {iid for iid, _ in resueltas}
        assert len(resueltas) == 1
        assert puts == [("ad-humano", "PAUSED")]
        assert conn.execute(
            "SELECT cerrada_el IS NULL, retiro_id FROM anuncio_retiro_intencion WHERE id = %s",
            (iid_dec,),
        ).fetchone() == (True, None)


@_skip_db
def test_el_vigia_sigue_retirando_y_reintenta_lo_no_confirmado():
    with _db_i3("orbit_g5_vigia") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid)
        _bolsa(conn, lid, "ad-viejo")
        _bolsa(conn, lid, "ad-terco", ag="ag-t", camp="c-t")
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1))
        filas = {**_fila("ad-viejo", "ENABLED"), **_fila("ad-terco", "ENABLED", ag="ag-t")}
        escritor, puts = _escritor(filas, rechaza_put=frozenset({"ad-terco"}))
        uno = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor)
        assert uno == []  # el impulso fallo: sin lectura del dia, se reintenta
        assert conn.execute("SELECT count(*) FROM impulso_lectura").fetchone() == (0,)
        assert conn.execute(
            "SELECT ad_id_externo, actor, autoria FROM anuncio_retiro"
        ).fetchall() == [("ad-viejo", "impulso-vigia", "confirmada")]
        escritor2, puts2 = _escritor(
            {**_fila("ad-viejo", "PAUSED"), **_fila("ad-terco", "ENABLED", ag="ag-t")}
        )
        dos = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor2)
        assert dos[0].hecho == "retiro_de_bolsas"
        assert puts2 == [("ad-terco", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (2,)


@_skip_db
def test_un_humano_no_retoma_la_intencion_de_una_decision():
    with _db_i4("orbit_g5_duenno") as conn:
        _pid, lid = _producto(conn)
        _c, _a, pa = _bolsa(conn, lid, "ad-dec")
        dec, q = _decision_de_pausa(conn, pa)
        incierto, _ = _escritor(
            _fila("ad-dec", "ENABLED"), puts_timeout_sin_aplicar=frozenset({"ad-dec"})
        )
        (res,) = ra.ejecuta_retiro(
            conn,
            ra.plan_de_decision(conn, dec),
            autorizacion=ra.AutorizacionDeDecision(dec, q),
            escritor=incierto,
        )
        assert res.resultado == "pendiente"
        humano, puts = _escritor(_fila("ad-dec", "ENABLED"))
        (res,) = ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=humano)
        assert res.resultado == "pendiente"
        assert "otra autorizacion" in res.detalle
        assert puts == []
        assert conn.execute(
            "SELECT decision_id, cerrada_el IS NULL FROM anuncio_retiro_intencion"
        ).fetchall() == [(dec, True)]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)


@_skip_db
def test_una_decision_no_tiene_segundo_put():
    with _db_i4("orbit_g5_segundo") as conn:
        _pid, lid = _producto(conn)
        _c, _a, pa = _bolsa(conn, lid, "ad-dec")
        dec, q = _decision_de_pausa(conn, pa)
        plan = ra.plan_de_decision(conn, dec)
        rechaza, _ = _escritor(_fila("ad-dec", "ENABLED"), rechaza_put=frozenset({"ad-dec"}))
        (res,) = ra.ejecuta_retiro(
            conn, plan, autorizacion=ra.AutorizacionDeDecision(dec, q), escritor=rechaza
        )
        assert res.resultado == "rechazado"
        otra, puts = _escritor(_fila("ad-dec", "ENABLED"))
        with pytest.raises(ra.AutorizacionInvalida, match="segundo PUT"):
            ra.ejecuta_retiro(
                conn, plan, autorizacion=ra.AutorizacionDeDecision(dec, q), escritor=otra
            )
        assert puts == []


@_skip_db
def test_put_incierto_que_queda_paused_en_la_misma_llamada_es_adoptado():
    with _db_i4("orbit_g5_incierto_paused") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        escritor, puts = _escritor(
            _fila("ad-1", "ENABLED"), puts_timeout_que_aplican=frozenset({"ad-1"})
        )
        (res,) = ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
        assert puts == [("ad-1", "PAUSED")]
        assert res.resultado == "adoptado"
        assert "incierto" in res.detalle
        assert conn.execute("SELECT autoria FROM anuncio_retiro").fetchone() == ("adoptada",)


@_skip_db
def test_recrea_un_anuncio_que_revienta_no_tumba_a_los_demas(monkeypatch):
    with _db_i4("orbit_g5_recrea") as conn:
        _pid, lid = _producto(conn)
        filas = {}
        for i in range(2):
            _bolsa(conn, lid, f"ad-{i}", ag=f"ag-{i}", camp=f"c-{i}")
            filas.update(_fila(f"ad-{i}", "ENABLED", ag=f"ag-{i}", camp=f"c-{i}"))
        escritor, _ = _escritor(filas)
        ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
        rids = tuple(f[0] for f in conn.execute("SELECT id FROM anuncio_retiro ORDER BY id"))
        archivados, _ = _escritor(_filas("ad-0", "ad-1", estado="ARCHIVED"))
        real = ra.archivar.reponer_anuncios
        llamadas = []

        def _primero_revienta(esc, lineas, **kw):
            llamadas.append(lineas)
            if len(llamadas) == 1:
                raise ra.AdsApiError("readback caido tras el POST")
            return real(esc, lineas, **kw)

        monkeypatch.setattr(ra.archivar, "reponer_anuncios", _primero_revienta)
        res = ra.recrea(
            conn,
            retiro_ids=rids,
            autorizacion=ra.AutorizacionHumana("RECREAR 2 ANUNCIOS", "dueno"),
            escritor=archivados,
        )
        assert [r.resultado for r in res] == ["fallo", "recreado"]
        assert "revisa Amazon" in res[0].detalle
        assert conn.execute("SELECT retiro_id FROM anuncio_reposicion").fetchall() == [(rids[1],)]


@_skip_db
def test_muerte_entre_sello_y_cierre_de_un_adoptado_conserva_la_marca(monkeypatch):
    with _db_i4("orbit_g5_muerte") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        incierto, _ = _escritor(_fila("ad-1", "ENABLED"), lists_que_fallan=frozenset(range(3, 40)))
        ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=incierto)
        real = ra._cierra_intencion

        def _muere_al_cerrar(conn_, iid, detalle, retiro_id):
            if retiro_id is not None:
                raise RuntimeError("muerte entre sello y cierre (simulada)")
            return real(conn_, iid, detalle, retiro_id)

        monkeypatch.setattr(ra, "_cierra_intencion", _muere_al_cerrar)
        pausado, _ = _escritor(_fila("ad-1", "PAUSED"))
        with pytest.raises(RuntimeError, match="muerte"):
            ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=pausado)
        monkeypatch.setattr(ra, "_cierra_intencion", real)
        (res,) = ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=pausado)
        assert res.resultado == "ya_retirado"
        assert conn.execute(
            "SELECT r.autoria, i.detalle LIKE 'adoptado:%' FROM anuncio_retiro r"
            " JOIN anuncio_retiro_intencion i ON i.retiro_id = r.id"
        ).fetchone() == ("adoptada", True)


@_skip_db
def test_un_error_de_sql_no_deja_tomada_la_llave_del_anuncio(monkeypatch):
    import psycopg
    from test_fabrica_migracion import _dsn_propia

    with _db_i4("orbit_g5_llave") as conn:
        _pid, lid = _producto(conn)
        _c, _a, pa = _bolsa(conn, lid, "ad-1")
        plan = _plan(conn, lid)
        escritor, puts = _escritor(_fila("ad-1", "ENABLED"))

        def _sql_roto(conn_, *a, **k):
            conn_.execute("SELECT 1 / 0")

        monkeypatch.setattr(ra, "_abre_intencion", _sql_roto)
        with psycopg.connect(_dsn_propia(conn), dbname=conn.info.dbname) as transaccional:
            with pytest.raises(psycopg.errors.DivisionByZero):
                ra.ejecuta_retiro(transaccional, plan, autorizacion=DUENO, escritor=escritor)
            (libre,) = conn.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s))", (f"retiro-ad:{pa}",)
            ).fetchone()
            assert libre is True
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"retiro-ad:{pa}",))
        assert puts == []


@_skip_db
def test_recrear_otra_vez_tras_un_post_sin_confirmar_no_duplica_el_anuncio():
    with _db_i4("orbit_g5_recrea_dos") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        escritor, _ = _escritor(_fila("ad-1", "ENABLED"))
        ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        recrear = ra.AutorizacionHumana("RECREAR 1 ANUNCIOS", "dueno")
        # El POST crea ad-nuevo-1 pero su readback nunca lo ve: sin confirmar.
        archivado, _ = _escritor(_fila("ad-1", "ARCHIVED"), ocultos={"ad-nuevo-1": 10})
        (primero,) = ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert primero.resultado == "fallo"
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)
        (segundo,) = ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert (segundo.resultado, segundo.ad_id_nuevo) == ("recreado", "ad-nuevo-1")
        assert ra.archivar.estados_actuales(archivado, ["ad-nuevo-2"]) == {}
        assert conn.execute(
            "SELECT ad_id_nuevo, estado_nuevo FROM anuncio_reposicion WHERE retiro_id = %s", (rid,)
        ).fetchone() == ("ad-nuevo-1", "ENABLED")


@_skip_db
def test_recrear_sin_lectura_confiable_del_ad_group_no_crea_nada(monkeypatch):
    with _db_i4("orbit_g5_recrea_ambiguo") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        escritor, _ = _escritor(_fila("ad-1", "ENABLED"))
        ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
        (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
        # La API ignora el filtro y devuelve la pagina entera: otro ad group en la lista.
        archivado, _ = _escritor(
            {**_fila("ad-1", "ARCHIVED"), **_fila("ad-ajeno", "ENABLED", ag="ag-ajeno")}
        )
        real = archivado.list_sellado

        def _sin_filtro(path, cuerpo):
            if "adGroupIdFilter" in cuerpo:
                return real(path, {"adIdFilter": {"include": ["ad-1", "ad-ajeno"]}})
            return real(path, cuerpo)

        monkeypatch.setattr(archivado, "list_sellado", _sin_filtro)
        monkeypatch.setattr(
            ra.archivar,
            "reponer_anuncios",
            lambda *a, **k: pytest.fail("recrea mando POST sin lectura confiable"),
        )
        (res,) = ra.recrea(
            conn,
            retiro_ids=(rid,),
            autorizacion=ra.AutorizacionHumana("RECREAR 1 ANUNCIOS", "dueno"),
            escritor=archivado,
        )
        assert res.resultado == "fallo"
        assert "no se recrea" in res.detalle
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)


def _retiro_archivado(conn, lid):
    escritor, _ = _escritor(_fila("ad-1", "ENABLED"))
    ra.ejecuta_retiro(conn, _plan(conn, lid), autorizacion=DUENO, escritor=escritor)
    (rid,) = conn.execute("SELECT id FROM anuncio_retiro").fetchone()
    return rid


@_skip_db
def test_recrear_con_el_list_del_grupo_atrasado_no_repite_el_post(monkeypatch):
    """h_eecbcc15: el anuncio creado por un POST incierto tampoco sale en el list del
    ad group (pagina valida, sin nextToken). El reintento no manda otro POST mientras
    el intento siga abierto; cuando Amazon lo muestra, sella ese mismo anuncio."""
    import httpx

    with _db_i4("orbit_g5_recrea_atrasado") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        rid = _retiro_archivado(conn, lid)
        archivado, _ = _escritor(_fila("ad-1", "ARCHIVED"), ocultos={"ad-nuevo-1": 10})
        original = archivado.list_sellado
        atrasado = {"si": True}

        def _lee(path, cuerpo):
            resp = original(path, cuerpo)
            if "adGroupIdFilter" in cuerpo and atrasado["si"]:
                filas = [f for f in resp.json()["productAds"] if f["adId"] != "ad-nuevo-1"]
                return httpx.Response(200, json={"productAds": filas})
            return resp

        monkeypatch.setattr(archivado, "list_sellado", _lee)
        recrear = ra.AutorizacionHumana("RECREAR 1 ANUNCIOS", "dueno")
        (primero,) = ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert primero.resultado == "fallo"
        assert conn.execute(
            "SELECT ad_id_nuevo, cerrada_el IS NULL FROM anuncio_recreacion_intento"
        ).fetchall() == [("ad-nuevo-1", True)]
        (segundo,) = ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert segundo.resultado == "fallo"
        assert "sin resolver" in segundo.detalle
        assert ra.archivar.estados_actuales(archivado, ["ad-nuevo-2"]) == {}
        atrasado["si"] = False
        (tercero,) = ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert (tercero.resultado, tercero.ad_id_nuevo) == ("recreado", "ad-nuevo-1")
        assert ra.archivar.estados_actuales(archivado, ["ad-nuevo-2"]) == {}
        assert conn.execute("SELECT ad_id_nuevo FROM anuncio_reposicion").fetchall() == [
            ("ad-nuevo-1",)
        ]
        assert conn.execute(
            "SELECT cerrada_el IS NOT NULL, detalle LIKE 'sellado ad-nuevo-1%' FROM"
            " anuncio_recreacion_intento"
        ).fetchall() == [(True, True)]


@_skip_db
def test_un_intento_viejo_con_anuncio_creado_e_invisible_no_crea_otro(monkeypatch):
    """h_eecbcc15: la edad del intento no prueba nada. El POST de hace dos horas creo
    ad-nuevo-1 (el ack trajo su adId) y Amazon aun no lo muestra ni por id ni en el ad
    group: recrea no manda otro POST y el intento sigue abierto. Cuando Amazon lo muestra,
    recrea sella ese mismo anuncio y cierra el intento en la misma transaccion."""
    import httpx

    with _db_i4("orbit_g5_recrea_viejo") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        rid = _retiro_archivado(conn, lid)
        archivado, _ = _escritor(_fila("ad-1", "ARCHIVED"), ocultos={"ad-nuevo-1": 100})
        original = archivado.list_sellado
        atrasado = {"si": True}

        def _lee(path, cuerpo):
            resp = original(path, cuerpo)
            if "adGroupIdFilter" in cuerpo and atrasado["si"]:
                filas = [f for f in resp.json()["productAds"] if f["adId"] != "ad-nuevo-1"]
                return httpx.Response(200, json={"productAds": filas})
            return resp

        monkeypatch.setattr(archivado, "list_sellado", _lee)
        (linea,) = conn.execute("SELECT reversa FROM anuncio_retiro").fetchone()
        (creado,) = ra.archivar.reponer_anuncios(archivado, [linea], ejecutar=True)
        assert (creado.resultado, creado.ad_id) == ("sin_confirmar", "ad-nuevo-1")
        conn.execute(
            "INSERT INTO anuncio_recreacion_intento (retiro_id, actor, creada_el, ad_id_nuevo)"
            " VALUES (%s, 'review', now() - interval '2 hours', 'ad-nuevo-1')",
            (rid,),
        )
        recrear = ra.AutorizacionHumana("RECREAR 1 ANUNCIOS", "dueno")
        (res,) = ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert res.resultado == "fallo"
        assert "sin resolver" in res.detalle and "ad-nuevo-1" in res.detalle
        assert ra.archivar.estados_actuales(archivado, ["ad-nuevo-2"]) == {}
        assert conn.execute(
            "SELECT cerrada_el IS NULL FROM anuncio_recreacion_intento"
        ).fetchall() == [(True,)]
        atrasado["si"] = False
        (res,) = ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert (res.resultado, res.ad_id_nuevo) == ("recreado", "ad-nuevo-1")
        assert conn.execute("SELECT ad_id_nuevo FROM anuncio_reposicion").fetchall() == [
            ("ad-nuevo-1",)
        ]
        assert conn.execute("SELECT detalle FROM anuncio_recreacion_intento").fetchall() == [
            ("sellado ad-nuevo-1",)
        ]


@_skip_db
def test_si_falla_la_reposicion_el_intento_queda_abierto_y_el_reintento_sella_el_mismo(
    monkeypatch,
):
    """h_c6ea18e1: el cierre del intento y la reposicion son una sola transaccion. Si la
    reposicion falla despues de un POST confirmado (trigger que revienta el INSERT), el
    intento queda abierto con el adId del ack; el reintento, con el list del grupo
    atrasado, lo encuentra por su id y sella ad-nuevo-1 sin crear otro."""
    import httpx
    import psycopg

    with _db_i4("orbit_g5_recrea_atomico") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        rid = _retiro_archivado(conn, lid)
        archivado, _ = _escritor(_fila("ad-1", "ARCHIVED"))
        conn.execute(
            "CREATE FUNCTION g5_revienta() RETURNS trigger LANGUAGE plpgsql AS"
            " $$ BEGIN RAISE EXCEPTION 'muerte antes de la reposicion (simulada)'; END $$"
        )
        conn.execute(
            "CREATE TRIGGER g5_revienta BEFORE INSERT ON anuncio_reposicion"
            " FOR EACH ROW EXECUTE FUNCTION g5_revienta()"
        )
        recrear = ra.AutorizacionHumana("RECREAR 1 ANUNCIOS", "dueno")
        with pytest.raises(psycopg.errors.RaiseException):
            ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert conn.execute(
            "SELECT ad_id_nuevo, cerrada_el IS NULL FROM anuncio_recreacion_intento"
        ).fetchall() == [("ad-nuevo-1", True)]
        assert conn.execute("SELECT count(*) FROM anuncio_reposicion").fetchone() == (0,)
        conn.execute("DROP TRIGGER g5_revienta ON anuncio_reposicion")
        original = archivado.list_sellado

        def _grupo_atrasado(path, cuerpo):
            resp = original(path, cuerpo)
            if "adGroupIdFilter" in cuerpo:
                filas = [f for f in resp.json()["productAds"] if f["adId"] != "ad-nuevo-1"]
                return httpx.Response(200, json={"productAds": filas})
            return resp

        monkeypatch.setattr(archivado, "list_sellado", _grupo_atrasado)
        (res,) = ra.recrea(conn, retiro_ids=(rid,), autorizacion=recrear, escritor=archivado)
        assert (res.resultado, res.ad_id_nuevo) == ("recreado", "ad-nuevo-1")
        assert ra.archivar.estados_actuales(archivado, ["ad-nuevo-2"]) == {}
        assert conn.execute(
            "SELECT ad_id_nuevo, detalle FROM anuncio_recreacion_intento"
        ).fetchall() == [("ad-nuevo-1", "sellado ad-nuevo-1")]
        assert conn.execute("SELECT ad_id_nuevo FROM anuncio_reposicion").fetchall() == [
            ("ad-nuevo-1",)
        ]


@_skip_db
def test_el_intento_de_recreacion_solo_gana_su_adid_y_se_cierra_una_vez():
    import psycopg

    with _db_i4("orbit_g5_recrea_trigger") as conn:
        _pid, lid = _producto(conn)
        _bolsa(conn, lid, "ad-1")
        rid = _retiro_archivado(conn, lid)
        (iid,) = conn.execute(
            "INSERT INTO anuncio_recreacion_intento (retiro_id, actor, creada_el)"
            " VALUES (%s, 'dueno', now()) RETURNING id",
            (rid,),
        ).fetchone()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO anuncio_recreacion_intento (retiro_id, actor, creada_el)"
                " VALUES (%s, 'otro', now())",
                (rid,),
            )
        conn.execute(
            "UPDATE anuncio_recreacion_intento SET ad_id_nuevo = 'x' WHERE id = %s", (iid,)
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE anuncio_recreacion_intento SET ad_id_nuevo = 'y' WHERE id = %s", (iid,)
            )
        conn.execute(
            "UPDATE anuncio_recreacion_intento SET cerrada_el = now(), detalle = 'd' WHERE id = %s",
            (iid,),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE anuncio_recreacion_intento SET detalle = 'otro' WHERE id = %s", (iid,)
            )
