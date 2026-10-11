"""Vista previa, lanzamiento y vigia del impulso (BIDS 02, I.3). Con base."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from test_fabrica_migracion import _entidad, _ledger_producto, _producto, db_fabrica
from test_migracion_0064 import SQL64
from test_migracion_0065 import SQL65
from test_migracion_0066 import SQL66
from test_retiro_anuncios import _bolsa, _escritor, _fila
from test_schema import _postgres_obligatorio_ausente

from app import impulso_io as io

HOY = dt.date(2026, 10, 9)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _db_i3(prefijo):
    """db_fabrica (ORDEN) mas 0064 y 0065, con fraccion vigente."""

    class _Db:
        def __enter__(self):
            from psycopg.types.json import Json

            self._ctx = db_fabrica(prefijo)
            conn = self._ctx.__enter__()
            conn.execute(SQL64)
            conn.execute(SQL65)
            conn.execute(SQL66)
            conn.execute(
                "INSERT INTO config_version(label,settings) VALUES ('fabrica',%s)",
                (Json({"ads_target_fraccion_margen_amazon_mx": "0.5"}),),
            )
            conn.commit()
            return conn

        def __exit__(self, *exc):
            return self._ctx.__exit__(*exc)

    return _Db()


def _con_margen(conn, *, sku="SS-1", asin="B0AAAAAAAA", seller_sku="SS-1", desfase=0):
    pid, lid = _producto(conn, sku=sku, asin=asin, seller_sku=seller_sku)
    _ledger_producto(conn, pid, hoy=HOY - dt.timedelta(days=1), fee_desfase=desfase)
    return pid, lid


def _solicitud(conn, lids, *, subfamilias=None):
    return io.SolicitudImpulso(
        plataforma="amazon_mx",
        listing_ids=tuple(lids),
        subfamilia_por_listing={lid: (subfamilias or {}).get(lid, "aros") for lid in lids},
    )


def _sugeridor_fijo(**por_rol):
    llamadas = []

    def sugiere(asins, expresiones_por_rol):
        llamadas.append((asins, sorted(expresiones_por_rol)))
        return {
            rol: (por_rol.get(rol) if expresiones_por_rol.get(rol) else None)
            for rol in expresiones_por_rol
        }

    sugiere.llamadas = llamadas
    return sugiere


@_skip_db
def test_vista_previa_dos_productos_huella_y_totales_sin_escribir():
    """I.3: previsualiza planea dos, huella estable, confirmacion exacta,
    totales, y no escribe nada."""
    with _db_i3("orbit_i3_prev") as conn:
        _, lid1 = _con_margen(conn)
        _, lid2 = _con_margen(conn, sku="SS-2", asin="B0AAAAAAAB", seller_sku="SS-2", desfase=20)
        vista = io.previsualiza(conn, _solicitud(conn, [lid1, lid2]), sugeridor=_sugeridor_fijo())
        assert len(vista.planes) == 2
        assert vista.confirmacion_esperada == "LANZAR IMPULSO DE 2 PRODUCTOS"
        assert vista.presupuesto_diario_total == Decimal("50")
        assert vista.tope_total == Decimal("700")
        assert vista.moneda == "MXN"
        rev = io.previsualiza(conn, _solicitud(conn, [lid2, lid1]), sugeridor=_sugeridor_fijo())
        assert rev.huella == vista.huella
        assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (0,)


@_skip_db
def test_vista_previa_sin_margen_no_bloquea_al_vecino():
    """I.3: sin margen positivo no hay objetivo medido: el producto queda
    como no lanzable y no bloquea la vista de los demas."""
    with _db_i3("orbit_i3_nomargen") as conn:
        _, lid_sin = _producto(conn, asin="B0AAAAAAAA")
        _, lid_con = _con_margen(conn, sku="SS-9", asin="B0AAAAAAAZ", seller_sku="SS-9")
        vista = io.previsualiza(
            conn, _solicitud(conn, [lid_sin, lid_con]), sugeridor=_sugeridor_fijo()
        )
        assert len(vista.planes) == 1
        assert [(n.listing_id, n.motivo) for n in vista.no_lanzables] != []
        assert vista.no_lanzables[0].listing_id == lid_sin
        assert vista.confirmacion_esperada == "LANZAR IMPULSO DE 1 PRODUCTO"
        solo_sin = io.previsualiza(conn, _solicitud(conn, [lid_sin]), sugeridor=_sugeridor_fijo())
        assert solo_sin.planes == ()
        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                _solicitud(conn, [lid_sin]),
                huella=solo_sin.huella,
                confirmacion=solo_sin.confirmacion_esperada,
                actor="dueno",
                ejecutor=_ejecutor_falso(conn),
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 422


@_skip_db
def test_vista_previa_sin_terminos_deja_bid_none():
    """I.3: sin terminos, el rol exact queda con bid None."""
    with _db_i3("orbit_i3_noterm") as conn:
        _, lid = _con_margen(conn)
        vista = io.previsualiza(
            conn,
            _solicitud(conn, [lid]),
            sugeridor=_sugeridor_fijo(
                category_exact=Decimal("6.00"), auto_discovery=Decimal("7.00")
            ),
        )
        assert vista.planes[0].bid_por_rol["category_exact"] is None
        assert vista.planes[0].bid_por_rol["auto_discovery"] == Decimal("7.00")


@_skip_db
def test_vista_previa_trae_retiros_de_bolsas():
    """I.4: la vista previa lista los anuncios ENABLED del producto en
    bolsas viejas, para que el dueno vea que saldran."""
    with _db_i3("orbit_i4_prev") as conn:
        _pid, lid = _con_margen(conn)
        _bolsa(conn, lid, "ad-viejo")
        vista = io.previsualiza(conn, _solicitud(conn, [lid]), sugeridor=_sugeridor_fijo())
        assert [r.ad_id_externo for r in vista.planes[0].retiros] == ["ad-viejo"]
        assert vista.planes[0].retiros[0].campana_externa == "c-vieja"


@_skip_db
def test_sugeridor_real_una_llamada_por_producto():
    """I.3: el sugeridor de Amazon hace una sola llamada por producto."""
    with _db_i3("orbit_i3_1call") as conn:
        _, lid = _con_margen(conn)

        class _Cliente:
            llamadas = 0

            def recommend_bids(self, payload, *, profile_id):
                type(self).llamadas += 1

                class _R:
                    def json(self):
                        return {"bidRecommendations": []}

                return _R()

        io.previsualiza(
            conn, _solicitud(conn, [lid]), sugeridor=io.sugeridor_amazon(_Cliente(), "p1", "MXN")
        )
        assert _Cliente.llamadas == 1


def _ejecutor_falso(conn, *, falla_en=()):
    """Ejecutor de fabrica falso: siembra lote+grupo+roles+goals reales."""

    def ejecuta(plan_grupo, huella, lote):
        if lote in falla_en:
            raise RuntimeError("amazon caido")
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
            " %s, '{}'::jsonb, 'shadow', 'applied')",
            (lote, huella),
        )
        grupo = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal,"
            " tipo) VALUES ('amazon_mx', 'aros', 'Base', %s, 19.10, 19.1040, 0.5, 'm', 'go',"
            " 'impulso') RETURNING id",
            (lote,),
        ).fetchone()[0]
        for rol in ("category_exact", "auto_discovery"):
            camp = conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
                " 'campaign', %s) RETURNING id",
                (f"{lote}-{rol}",),
            ).fetchone()[0]
            ag = conn.execute(
                "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
                " VALUES ('amazon_mx', 'ad_group', %s, %s) RETURNING id",
                (f"{lote}-{rol}-ag", camp),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id,"
                " ad_group_ad_entity_id) VALUES (%s, %s, %s, %s)",
                (grupo, rol, camp, ag),
            )
            conn.execute(
                "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct,"
                " bid_floor, bid_ceiling, bid_currency, enabled, mode) VALUES ('campaign',"
                " %s, NULL, 0.10, 2.50, 'MXN', true, 'shadow')",
                (camp,),
            )
        conn.commit()
        return io.EjecucionFabrica(lote=lote, grupo_id=grupo, estado_lote="applied")

    return ejecuta


@_skip_db
def test_lanza_dos_crea_impulsos_y_es_idempotente():
    """I.3: lanza crea un impulso por producto con goals shadow; repetir
    con la misma huella no duplica."""
    with _db_i3("orbit_i3_lanza") as conn:
        _, lid1 = _con_margen(conn)
        _, lid2 = _con_margen(conn, sku="SS-2", asin="B0AAAAAAAB", seller_sku="SS-2", desfase=20)
        solicitud = _solicitud(conn, [lid1, lid2])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        lanzados = io.lanza(
            conn,
            solicitud,
            huella=vista.huella,
            confirmacion="LANZAR IMPULSO DE 2 PRODUCTOS",
            actor="dueno",
            ejecutor=_ejecutor_falso(conn),
            sugeridor=_sugeridor_fijo(),
        )
        assert len(lanzados) == 2
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (2,)
        assert conn.execute(
            "SELECT count(*) FROM ads_optimizer_goal WHERE mode = 'shadow'"
        ).fetchone() == (4,)
        otra = io.lanza(
            conn,
            solicitud,
            huella=vista.huella,
            confirmacion="LANZAR IMPULSO DE 2 PRODUCTOS",
            actor="dueno",
            ejecutor=_ejecutor_falso(conn),
            sugeridor=_sugeridor_fijo(),
        )
        assert [x.impulso_id for x in otra] == [x.impulso_id for x in lanzados]
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (2,)


@_skip_db
def test_lanza_huella_distinta_da_409():
    """I.3: si el plan cambio desde la vista previa, 409 sin escribir."""
    with _db_i3("orbit_i3_409") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                solicitud,
                huella="0" * 64,
                confirmacion=vista.confirmacion_esperada,
                actor="dueno",
                ejecutor=_ejecutor_falso(conn),
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 409
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (0,)


@_skip_db
def test_lanza_un_fallo_no_detiene_al_otro():
    """I.3: si un producto falla, el otro sale y el fallo se reporta."""
    with _db_i3("orbit_i3_falla1") as conn:
        _, lid1 = _con_margen(conn)
        _, lid2 = _con_margen(conn, sku="SS-2", asin="B0AAAAAAAB", seller_sku="SS-2", desfase=20)
        solicitud = _solicitud(conn, [lid1, lid2])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                solicitud,
                huella=vista.huella,
                confirmacion=vista.confirmacion_esperada,
                actor="dueno",
                ejecutor=_ejecutor_falso(conn, falla_en={io.lote_de_plan(vista.planes[1])}),
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 502
        assert len(exc.value.lanzados) == 1
        assert list(exc.value.fallos) == [lid2]
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (1,)


@_skip_db
def test_lanza_rechaza_vivo_y_acepta_ids_vacios_con_422():
    """I.3: relanzar un vivo con plan distinto da 409; listing_ids vacio
    da 422."""
    with _db_i3("orbit_i3_vivo") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        io.lanza(
            conn,
            solicitud,
            huella=vista.huella,
            confirmacion=vista.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_falso(conn),
            sugeridor=_sugeridor_fijo(),
        )
        otra = _solicitud(conn, [lid], subfamilias={lid: "collares"})
        vista2 = io.previsualiza(conn, otra, sugeridor=_sugeridor_fijo())
        assert vista2.huella != vista.huella
        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                otra,
                huella=vista2.huella,
                confirmacion=vista2.confirmacion_esperada,
                actor="dueno",
                ejecutor=_ejecutor_falso(conn),
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 409
        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                _solicitud(conn, []),
                huella="x",
                confirmacion="x",
                actor="dueno",
                ejecutor=_ejecutor_falso(conn),
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 422


@_skip_db
def test_lanza_lote_huerfano_sella_sin_llamar_a_la_fabrica():
    """N3: crash entre _mutar e INSERT: el lote applied sin impulso se
    acepta, se sella el impulso y la fabrica no se toca."""
    with _db_i3("orbit_i3_n3") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        lote = io.lote_de_plan(vista.planes[0])
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
            " %s, '{}'::jsonb, 'shadow', 'applied')",
            (lote, vista.planes[0].huella()),
        )
        grupo = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal,"
            " tipo) VALUES ('amazon_mx', 'aros', 'Base', %s, 19.10, 19.1040, 0.5, 'm', 'go',"
            " 'impulso') RETURNING id",
            (lote,),
        ).fetchone()[0]
        conn.commit()

        def _truena_si_llaman(plan_grupo, huella, lote_llamado):
            raise AssertionError("la fabrica no debio ejecutarse")

        lanzados = io.lanza(
            conn,
            solicitud,
            huella=vista.huella,
            confirmacion=vista.confirmacion_esperada,
            actor="dueno",
            ejecutor=_truena_si_llaman,
            sugeridor=_sugeridor_fijo(),
        )
        assert len(lanzados) == 1
        assert lanzados[0].estado_lote == "applied"
        assert conn.execute(
            "SELECT grupo_id, actor FROM impulso WHERE lote = %s", (lote,)
        ).fetchone() == (grupo, "dueno")


@_skip_db
def test_lanza_409_por_vivo_adjunta_lo_lanzado():
    """N4: si el segundo producto choca con un vivo, el 409 trae en
    lanzados al primero que si salio."""
    with _db_i3("orbit_i3_n4") as conn:
        _, lid1 = _con_margen(conn)
        _, lid2 = _con_margen(conn, sku="SS-2", asin="B0AAAAAAAB", seller_sku="SS-2", desfase=20)
        solo2 = _solicitud(conn, [lid2])
        vista2 = io.previsualiza(conn, solo2, sugeridor=_sugeridor_fijo())
        io.lanza(
            conn,
            solo2,
            huella=vista2.huella,
            confirmacion=vista2.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_falso(conn),
            sugeridor=_sugeridor_fijo(),
        )
        solicitud = _solicitud(conn, [lid1, lid2], subfamilias={lid2: "collares"})
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                solicitud,
                huella=vista.huella,
                confirmacion=vista.confirmacion_esperada,
                actor="dueno",
                ejecutor=_ejecutor_falso(conn),
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 409
        assert len(exc.value.lanzados) == 1
        assert exc.value.lanzados[0].lote == io.lote_de_plan(vista.planes[0])
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (2,)


class _Pausador:
    def __init__(self):
        self.pausados = []
        self.reanudados = []

    def pausa(self, lote):
        self.pausados.append(lote)

    def reanuda(self, lote):
        self.reanudados.append(lote)


def _impulso_vivo(
    conn, *, pid=None, lid=None, lote="impulso-vivo-1", lanzado=None, tope="350", diario="25"
):
    """Siembra producto+grupo+impulso vivo con 2 campanas; devuelve ids."""
    if pid is None:
        pid, lid = _con_margen(conn)
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
        " 'h', '{}'::jsonb, 'shadow', 'applied')",
        (lote,),
    )
    grupo = conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
        " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal,"
        " tipo) VALUES ('amazon_mx', 'aros', 'Base', %s, 19.10, 19.1040, 0.5, 'm', 'go',"
        " 'impulso') RETURNING id",
        (lote,),
    ).fetchone()[0]
    camps = []
    for rol in ("category_exact", "auto_discovery"):
        camp = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
            " 'campaign', %s) RETURNING id",
            (f"{lote}-{rol}",),
        ).fetchone()[0]
        ag = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES ('amazon_mx', 'ad_group', %s, %s) RETURNING id",
            (f"{lote}-{rol}-ag", camp),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id,"
            " ad_group_ad_entity_id) VALUES (%s, %s, %s, %s)",
            (grupo, rol, camp, ag),
        )
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct,"
            " bid_floor, bid_ceiling, bid_currency, enabled, mode) VALUES ('campaign',"
            " %s, NULL, 0.10, 2.50, 'MXN', true, 'shadow')",
            (camp,),
        )
        camps.append(camp)
    imp = conn.execute(
        "INSERT INTO impulso (platform, listing_id, product_id, grupo_id, lote, subfamilia,"
        " tope_monto, tope_moneda, presupuesto_diario, plazo_dias, lanzado_el, actor,"
        " go_literal) VALUES ('amazon_mx', %s, %s, %s, %s, 'aros', %s, 'MXN', %s, 14,"
        " %s, 'dueno', 'go') RETURNING id",
        (lid, pid, grupo, lote, tope, diario, lanzado or HOY - dt.timedelta(days=6)),
    ).fetchone()[0]
    conn.commit()
    return {"impulso": imp, "grupo": grupo, "lote": lote, "camps": camps}


def _observa(conn, camp, dia, *, impr=100, clics=5, gasto="10", pedidos=0):
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', %s, %s, %s, %s, %s)",
        (camp, dia, gasto, impr, clics, pedidos, run),
    )
    conn.commit()


@_skip_db
def test_vigia_en_curso_guarda_y_no_toca():
    """I.3: con gasto bajo, el vigia guarda la lectura y no hace nada."""
    with _db_i3("orbit_i3_v1") as conn:
        ids = _impulso_vivo(conn)
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1))
        _observa(conn, ids["camps"][1], HOY - dt.timedelta(days=1))
        pausador = _Pausador()
        acciones = io.vigila(conn, pausador, hoy=HOY)
        assert len(acciones) == 1
        assert acciones[0].hecho == "nada"
        assert acciones[0].veredicto.estado == "en_curso"
        assert pausador.pausados == []
        fila = conn.execute(
            "SELECT veredicto, es_final, hecho, gasto FROM impulso_lectura WHERE impulso_id = %s",
            (ids["impulso"],),
        ).fetchone()
        assert fila == ("en_curso", False, "nada", Decimal("20"))
        assert conn.execute(
            "SELECT mode FROM ads_optimizer_goal WHERE ad_entity_id = %s", (ids["camps"][0],)
        ).fetchone() == ("shadow",)


@_skip_db
def test_vigia_agotado_pausa_y_sella():
    """I.3: con el tope agotado, el vigia pausa el lote y guarda final."""
    with _db_i3("orbit_i3_v2") as conn:
        ids = _impulso_vivo(conn)
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), gasto="200", clics=30)
        _observa(conn, ids["camps"][1], HOY - dt.timedelta(days=1), gasto="150", clics=30)
        pausador = _Pausador()
        acciones = io.vigila(conn, pausador, hoy=HOY)
        assert acciones[0].hecho == "pausado"
        assert pausador.pausados == [ids["lote"]]
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (ids["lote"],)
        ).fetchone() == ("pausado",)
        assert conn.execute(
            "SELECT veredicto, es_final FROM impulso_lectura WHERE impulso_id = %s",
            (ids["impulso"],),
        ).fetchone() == ("clics_sin_ventas", True)


@_skip_db
def test_vigia_vende_gradua():
    """I.3: con un pedido, el vigia gradua: goals a live."""
    with _db_i3("orbit_i3_v3") as conn:
        ids = _impulso_vivo(conn)
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        pausador = _Pausador()
        acciones = io.vigila(conn, pausador, hoy=HOY)
        assert acciones[0].hecho == "graduado"
        assert pausador.pausados == []
        assert conn.execute(
            "SELECT DISTINCT mode FROM ads_optimizer_goal WHERE ad_entity_id = ANY(%s)",
            (ids["camps"],),
        ).fetchall() == [("live",)]


@_skip_db
def test_vigia_dos_corridas_no_duplica_y_sin_datos_no_guarda():
    """I.3: la misma corrida dos veces guarda una fila; sin observaciones
    no guarda nada."""
    with _db_i3("orbit_i3_v4") as conn:
        ids = _impulso_vivo(conn)
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1))
        pausador = _Pausador()
        io.vigila(conn, pausador, hoy=HOY)
        io.vigila(conn, pausador, hoy=HOY)
        assert conn.execute(
            "SELECT count(*) FROM impulso_lectura WHERE impulso_id = %s", (ids["impulso"],)
        ).fetchone() == (1,)


@_skip_db
def test_vigia_segunda_corrida_no_repite_la_accion():
    """I.3: con veredicto pausar, la segunda corrida del mismo dia no
    vuelve a llamar al pausador."""
    with _db_i3("orbit_i3_v6") as conn:
        ids = _impulso_vivo(conn)
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), gasto="200", clics=30)
        _observa(conn, ids["camps"][1], HOY - dt.timedelta(days=1), gasto="150", clics=30)
        pausador = _Pausador()
        uno = io.vigila(conn, pausador, hoy=HOY)
        dos = io.vigila(conn, pausador, hoy=HOY)
        assert uno[0].hecho == "pausado"
        assert dos == []
        assert pausador.pausados == [ids["lote"]]
        assert conn.execute(
            "SELECT count(*) FROM impulso_lectura WHERE impulso_id = %s", (ids["impulso"],)
        ).fetchone() == (1,)


@_skip_db
def test_vigia_no_retira_sin_impresiones():
    """I.4 mutante: el retiro no saca un anuncio antes de las
    impresiones (0 o None)."""
    with _db_i3("orbit_i4_r0") as conn:
        pid, lid = _con_margen(conn)
        _impulso_vivo(conn, pid=pid, lid=lid)
        _bolsa(conn, lid, "ad-viejo")
        ajena = _entidad(conn, "amazon_mx", "campaign", "ajena-r0")
        conn.commit()
        _observa(conn, ajena, HOY - dt.timedelta(days=1))
        escritor, puts = _escritor(_fila("ad-viejo", "ENABLED"))
        acciones = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor)
        assert acciones[0].hecho == "nada"
        assert acciones[0].lectura.impresiones == 0
        assert puts == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (0,)


@_skip_db
def test_vigia_retira_una_vez_con_impresiones():
    """I.4: el primer dia con impresiones, el vigia saca el producto de
    sus bolsas viejas y guarda `retiro_de_bolsas`; no repite."""
    with _db_i3("orbit_i4_r1") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid)
        _bolsa(conn, lid, "ad-viejo")
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1))
        escritor, puts = _escritor(_fila("ad-viejo", "ENABLED"))
        uno = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor)
        assert uno[0].hecho == "retiro_de_bolsas"
        assert puts == [("ad-viejo", "PAUSED")]
        assert conn.execute("SELECT motivo, modo, impulso_id FROM anuncio_retiro").fetchone() == (
            "impulso",
            "pausa",
            ids["impulso"],
        )
        assert conn.execute("SELECT actor FROM anuncio_retiro").fetchone() == ("impulso-vigia",)
        dos = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor)
        assert dos[0].hecho == "retiro_de_bolsas"
        assert len(puts) == 1
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)


@_skip_db
def test_vigia_sin_observaciones_no_guarda():
    """I.3: sin filas de ingesta, el vigia no inventa lectura."""
    with _db_i3("orbit_i3_v5") as conn:
        ids = _impulso_vivo(conn)
        acciones = io.vigila(conn, _Pausador(), hoy=HOY)
        assert acciones[0].hecho == "nada"
        assert conn.execute(
            "SELECT count(*) FROM impulso_lectura WHERE impulso_id = %s", (ids["impulso"],)
        ).fetchone() == (0,)


@_skip_db
def test_vigia_dia_sin_fila_cuenta_cero():
    """I.3 mutante M-dia-sin-fila-cero: con ingesta en la plataforma pero
    sin filas de sus campanas, el vigia guarda ceros (no se queda sin
    leer)."""
    with _db_i3("orbit_i3_m1") as conn:
        ids = _impulso_vivo(conn)
        ajena = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id) VALUES ('amazon_mx',"
            " 'campaign', 'ajena-1') RETURNING id"
        ).fetchone()[0]
        conn.commit()
        _observa(conn, ajena, HOY - dt.timedelta(days=1))
        acciones = io.vigila(conn, _Pausador(), hoy=HOY)
        assert acciones[0].hecho == "nada"
        assert acciones[0].lectura.gasto == 0
        assert conn.execute(
            "SELECT gasto, clics FROM impulso_lectura WHERE impulso_id = %s", (ids["impulso"],)
        ).fetchone() == (Decimal("0"), 0)


@_skip_db
def test_vigia_metrica_null_no_veredicto():
    """I.3 mutante M-metricas-NULL: una metrica NULL llega como None y
    deja el impulso en curso (nunca un cero inventado que pause)."""
    with _db_i3("orbit_i3_m2") as conn:
        ids = _impulso_vivo(conn)
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        conn.execute(
            "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
            " metric_currency, cost, impressions, clicks, orders, ingest_run_id)"
            " VALUES (%s, %s, now(), 'MXN', 400, 5000, NULL, 0, %s)",
            (ids["camps"][0], HOY - dt.timedelta(days=1), run),
        )
        conn.commit()
        acciones = io.vigila(conn, _Pausador(), hoy=HOY)
        assert acciones[0].lectura.clics is None
        assert acciones[0].veredicto.estado == "en_curso"
        assert acciones[0].hecho == "nada"


@_skip_db
def test_vigia_usa_el_tope_guardado():
    """I.3 mutante M-veredicto-presupuesto: el veredicto usa el tope de
    la fila del impulso, no el default de settings."""
    with _db_i3("orbit_i3_m3") as conn:
        ids = _impulso_vivo(conn, tope="20", diario="2")
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), gasto="25", clics=30)
        acciones = io.vigila(conn, _Pausador(), hoy=HOY)
        assert acciones[0].hecho == "pausado"


@_skip_db
def test_vigia_crash_entre_pausar_y_guardar_no_repite_la_pausa():
    """B1: si el lote ya quedo pausado (murio antes de guardar la
    lectura), la re-corrida no llama al pausador y sella 'pausado'."""
    with _db_i3("orbit_i3_b1") as conn:
        ids = _impulso_vivo(conn, lote="impulso-crash-1")
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), gasto="200", clics=30)
        _observa(conn, ids["camps"][1], HOY - dt.timedelta(days=1), gasto="150", clics=30)
        conn.execute("UPDATE fabrica_lote SET estado = 'pausado' WHERE lote = %s", (ids["lote"],))
        conn.commit()
        pausador = _Pausador()
        acciones = io.vigila(conn, pausador, hoy=HOY)
        assert acciones[0].hecho == "pausado"
        assert pausador.pausados == []
        assert conn.execute(
            "SELECT hecho FROM impulso_lectura WHERE impulso_id = %s", (ids["impulso"],)
        ).fetchone() == ("pausado",)


@_skip_db
def test_vigia_un_fallo_no_detiene_al_vecino():
    """N5: si un impulso truena, el vecino se atiende igual y el
    fallido queda sin lectura para reintentarse manana."""
    with _db_i3("orbit_i3_n5") as conn:
        pid_malo, lid_malo = _con_margen(conn, sku="SS-N5M", asin="B0NNNNN5M1", seller_sku="SS-N5M")
        pid_bueno, lid_bueno = _con_margen(
            conn, sku="SS-N5B", asin="B0NNNNN5B1", seller_sku="SS-N5B", desfase=7
        )
        malo = _impulso_vivo(
            conn, pid=pid_malo, lid=lid_malo, lote="impulso-malo-1", tope="1", diario="1"
        )
        bueno = _impulso_vivo(conn, pid=pid_bueno, lid=lid_bueno, lote="impulso-bueno-1")
        _observa(conn, bueno["camps"][0], HOY - dt.timedelta(days=1))

        class _Truena(_Pausador):
            def pausa(self, lote):
                if lote == "impulso-malo-1":
                    raise RuntimeError("amazon caido")
                super().pausa(lote)

        _observa(conn, malo["camps"][0], HOY - dt.timedelta(days=1), gasto="50", clics=30)
        pausador = _Truena()
        acciones = io.vigila(conn, pausador, hoy=HOY)
        assert {a.impulso_id for a in acciones} == {bueno["impulso"]}
        assert conn.execute(
            "SELECT count(*) FROM impulso_lectura WHERE impulso_id = %s", (malo["impulso"],)
        ).fetchone() == (0,)
        assert (
            conn.execute(
                "SELECT hecho FROM impulso_lectura WHERE impulso_id = %s", (bueno["impulso"],)
            ).fetchone()
            is not None
        )
