"""Vista previa, lanzamiento y vigia del impulso (BIDS 02, I.3). Con base."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from test_fabrica_migracion import _entidad, _ledger_producto, _producto, db_fabrica
from test_migracion_0064 import SQL64
from test_migracion_0065 import SQL65
from test_migracion_0066 import SQL66
from test_migracion_0068 import SQL68
from test_migracion_0069 import SQL69
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
            conn.execute(SQL68)
            conn.execute(SQL69)
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


def _ejecutor_falso(conn, *, falla_en=(), platform="amazon_mx", moneda="MXN"):
    """Ejecutor de fabrica falso: siembra lote+grupo+roles+goals reales.

    Como el real (_mutar), escribe por SU PROPIA conexion, no por la
    del llamador: asi no rompe la transaccion de lanza."""

    import psycopg

    dsn = conn.info.dsn
    dbname = conn.info.dbname

    def ejecuta(plan_grupo, huella, lote):
        if lote in falla_en:
            raise RuntimeError("amazon caido")
        propia = psycopg.connect(dsn, dbname=dbname, autocommit=True)
        try:
            propia.execute(
                "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base,"
                " go_literal, huella, plan, modo_goal, estado) VALUES (%s, %s,"
                " 'aros', 'Base', 'go', %s, '{}'::jsonb, 'shadow', 'applied')",
                (lote, platform, huella),
            )
            grupo = propia.execute(
                "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
                " target_acos_pct, target_derivado_pct, fraccion, target_procedencia,"
                " go_literal, tipo) VALUES (%s, 'aros', 'Base', %s, 19.10,"
                " 19.1040, 0.5, 'm', 'go', 'impulso') RETURNING id",
                (platform, lote),
            ).fetchone()[0]
            for rol in ("category_exact", "auto_discovery"):
                camp = propia.execute(
                    "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s,"
                    " 'campaign', %s) RETURNING id",
                    (platform, f"{lote}-{rol}"),
                ).fetchone()[0]
                ag = propia.execute(
                    "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
                    " VALUES (%s, 'ad_group', %s, %s) RETURNING id",
                    (platform, f"{lote}-{rol}-ag", camp),
                ).fetchone()[0]
                propia.execute(
                    "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id,"
                    " ad_group_ad_entity_id) VALUES (%s, %s, %s, %s)",
                    (grupo, rol, camp, ag),
                )
                propia.execute(
                    "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct,"
                    " bid_floor, bid_ceiling, bid_currency, enabled, mode) VALUES"
                    " ('campaign', %s, NULL, 0.10, 2.50, %s, true, 'shadow')",
                    (camp, moneda),
                )
        finally:
            propia.close()
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
def test_lanza_concurrente_mismo_listing_un_solo_vivo():
    """R09-r2 GLM47-2: dos lanzar al mismo listing en dos conexiones
    autocommit (como prod): uno crea, el otro relee por el candado
    (idempotente); jamas dos vivos ni UniqueViolation cruda."""
    import threading

    import psycopg

    with _db_i3("orbit_i3_race") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        barrera = threading.Barrier(2)
        resultados: dict = {}

        def _corre(n, c):
            try:
                barrera.wait(timeout=30)
                r = io.lanza(
                    c,
                    solicitud,
                    huella=vista.huella,
                    confirmacion=vista.confirmacion_esperada,
                    actor="dueno",
                    ejecutor=_ejecutor_falso(c),
                    sugeridor=_sugeridor_fijo(),
                )
                resultados[n] = ("ok", [x.impulso_id for x in r])
            except Exception as exc:  # noqa: BLE001 - el test clasifica
                resultados[n] = ("crash", type(exc).__name__)

        conn2 = psycopg.connect(conn.info.dsn, dbname=conn.info.dbname, autocommit=True)
        try:
            hilos = [
                threading.Thread(target=_corre, args=(1, conn)),
                threading.Thread(target=_corre, args=(2, conn2)),
            ]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join(timeout=60)
                assert not h.is_alive()
        finally:
            conn2.close()
        assert resultados[1][0] == "ok", resultados
        assert resultados[2][0] == "ok", resultados
        assert resultados[1][1] == resultados[2][1]
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (1,)


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
        self.reactivados = []

    def pausa(self, lote):
        self.pausados.append(lote)

    def reanuda(self, lote):
        self.reanudados.append(lote)

    def reactiva(self, lote):
        self.reactivados.append(lote)


def _impulso_vivo(
    conn,
    *,
    pid=None,
    lid=None,
    lote="impulso-vivo-1",
    lanzado=None,
    tope="350",
    diario="25",
    platform="amazon_mx",
    moneda="MXN",
):
    """Siembra producto+grupo+impulso vivo con 2 campanas; devuelve ids."""
    if pid is None:
        pid, lid = _con_margen(conn)
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES (%s, %s, 'aros', 'Base', 'go',"
        " 'h', '{}'::jsonb, 'shadow', 'applied')",
        (lote, platform),
    )
    grupo = conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
        " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal,"
        " tipo) VALUES (%s, 'aros', 'Base', %s, 19.10, 19.1040, 0.5, 'm', 'go',"
        " 'impulso') RETURNING id",
        (platform, lote),
    ).fetchone()[0]
    camps = []
    for rol in ("category_exact", "auto_discovery"):
        camp = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s,"
            " 'campaign', %s) RETURNING id",
            (platform, f"{lote}-{rol}"),
        ).fetchone()[0]
        ag = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES (%s, 'ad_group', %s, %s) RETURNING id",
            (platform, f"{lote}-{rol}-ag", camp),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id,"
            " ad_group_ad_entity_id) VALUES (%s, %s, %s, %s)",
            (grupo, rol, camp, ag),
        )
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct,"
            " bid_floor, bid_ceiling, bid_currency, enabled, mode) VALUES ('campaign',"
            " %s, NULL, 0.10, 2.50, %s, true, 'shadow')",
            (camp, moneda),
        )
        camps.append(camp)
    imp = conn.execute(
        "INSERT INTO impulso (platform, listing_id, product_id, grupo_id, lote, subfamilia,"
        " tope_monto, tope_moneda, presupuesto_diario, plazo_dias, lanzado_el, actor,"
        " go_literal) VALUES (%s, %s, %s, %s, %s, 'aros', %s, %s, %s, 14,"
        " %s, 'dueno', 'go') RETURNING id",
        (
            platform,
            lid,
            pid,
            grupo,
            lote,
            tope,
            moneda,
            diario,
            lanzado or HOY - dt.timedelta(days=6),
        ),
    ).fetchone()[0]
    conn.commit()
    return {"impulso": imp, "grupo": grupo, "lote": lote, "camps": camps}


def _observa(conn, camp, dia, *, impr=100, clics=5, gasto="10", pedidos=0, moneda="MXN"):
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s)",
        (camp, dia, moneda, gasto, impr, clics, pedidos, run),
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
def test_vigia_retira_cada_plataforma_con_su_escritor(monkeypatch):
    """R09-r2 GLM53-1: con impulsos MX y US que retiran el mismo dia,
    el vigia prepara un escritor por plataforma (antes reusaba el de
    MX para US y el retiro US fallaba)."""
    from app.ads import archivar

    with _db_i3("orbit_i3_2plat") as conn:
        pid_mx, lid_mx = _producto(conn, sku="SS-2P-MX", platform="amazon_mx")
        pid_us, lid_us = _producto(
            conn, sku="SS-2P-US", asin="B0USSS0001", platform="amazon_us", seller_sku="SS-2P-US"
        )
        mx = _impulso_vivo(conn, pid=pid_mx, lid=lid_mx, lote="impulso-mx-1")
        us = _impulso_vivo(
            conn,
            pid=pid_us,
            lid=lid_us,
            lote="impulso-us-1",
            platform="amazon_us",
            moneda="USD",
        )
        _bolsa(conn, lid_mx, "ad-mx")
        _bolsa(conn, lid_us, "ad-us", camp="c-us", ag="ag-us", platform="amazon_us")
        _observa(conn, mx["camps"][0], HOY - dt.timedelta(days=1))
        _observa(conn, us["camps"][0], HOY - dt.timedelta(days=1), moneda="USD")
        escritor_mx, puts_mx = _escritor(_fila("ad-mx", "ENABLED"))
        escritor_us, puts_us = _escritor(_fila("ad-us", "ENABLED"))
        llamadas: list[str] = []

        def _preparar(platform):
            llamadas.append(platform)
            return {"amazon_mx": escritor_mx, "amazon_us": escritor_us}[platform]

        monkeypatch.setattr(archivar, "preparar_escritor", _preparar)
        acciones = io.vigila(conn, _Pausador(), hoy=HOY)
        assert llamadas == ["amazon_mx", "amazon_us"]
        assert {a.hecho for a in acciones} == {"retiro_de_bolsas"}
        assert puts_mx == [("ad-mx", "PAUSED")]
        assert puts_us == [("ad-us", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (2,)


@_skip_db
def test_vigia_gradua_y_retira_el_mismo_dia():
    """R09-B: si vende el primer dia con impresiones, el vigia gradua
    (goals live) Y retira de las bolsas viejas; el ganador no compite
    consigo mismo."""
    with _db_i3("orbit_i4_rb") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid)
        _bolsa(conn, lid, "ad-viejo")
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        escritor, puts = _escritor(_fila("ad-viejo", "ENABLED"))
        acciones = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor)
        assert acciones[0].hecho == "graduado"
        assert acciones[0].veredicto.final is True
        assert conn.execute(
            "SELECT DISTINCT mode FROM ads_optimizer_goal WHERE ad_entity_id = ANY(%s)",
            (ids["camps"],),
        ).fetchall() == [("live",)]
        assert puts == [("ad-viejo", "PAUSED")]
        assert conn.execute("SELECT motivo, impulso_id FROM anuncio_retiro").fetchone() == (
            "impulso",
            ids["impulso"],
        )


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
def test_vigia_descarta_el_dia_en_curso():
    """R09-r2 B-4 (regla 6): con ingesta solo de hoy, el vigia no la
    usa: guarda ceros en curso, sin leer parciales."""
    with _db_i3("orbit_i3_hoy") as conn:
        ids = _impulso_vivo(conn)
        _observa(conn, ids["camps"][0], HOY, impr=500, clics=40, gasto="300", pedidos=0)
        acciones = io.vigila(conn, _Pausador(), hoy=HOY)
        assert acciones[0].hecho == "nada"
        assert acciones[0].lectura.impresiones == 0
        assert conn.execute(
            "SELECT impresiones, gasto, corte FROM impulso_lectura WHERE impulso_id = %s",
            (ids["impulso"],),
        ).fetchone() == (0, Decimal("0"), HOY - dt.timedelta(days=1))


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
def test_vigia_un_fallo_no_detiene_al_vecino(capsys):
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
        assert f"impulso {malo['impulso']} fallo" in capsys.readouterr().err


@_skip_db
@_skip_db
def test_lanza_reintento_dia_siguiente_mismo_plan_reutiliza_y_reactiva(monkeypatch):
    """R09-r4 B1 (H2-coincide): huerfano applied de AYER con el MISMO
    plan (presupuesto y bids) se reutiliza HOY; si quedo pausado, se
    reactiva antes de sellar (un impulso vivo gasta). Ruta real."""
    import datetime as _dt
    from decimal import Decimal

    from app import impulso as im
    from app.api_fabrica import _ejecutor_lanzar

    with _db_i3("orbit_i3_r4h2c") as conn:
        _, lid = _con_margen(conn)
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        sub = {lid: "aros"}
        ayer = HOY - _dt.timedelta(days=1)
        sol = io.SolicitudImpulso(
            plataforma="amazon_mx",
            listing_ids=(lid,),
            subfamilia_por_listing=sub,
            tope_monto=Decimal("350"),
        )
        vista_ayer = io.previsualiza(conn, sol, sugeridor=_sugeridor_fijo(), hoy=ayer)
        plan_ayer = vista_ayer.planes[0]
        viejo = io.lote_de_plan(plan_ayer)
        hecho = _ejecutor_lanzar(conn, vista_ayer.confirmacion_esperada)(
            im.grupo_v2_de_plan(plan_ayer), plan_ayer.huella(), viejo
        )
        assert hecho.estado_lote == "applied"
        # Quedo pausado (crash entre activar y sellar): HOY el mismo
        # plan lo reutiliza y lo reactiva.
        for ext, payload in amazon.objetos.items():
            if ext.startswith("campaigns-"):
                payload["state"] = "PAUSED"
        vista_hoy = io.previsualiza(conn, sol, sugeridor=_sugeridor_fijo(), hoy=HOY)
        assert vista_hoy.huella != vista_ayer.huella
        assert io.lote_de_plan(vista_hoy.planes[0]) != viejo
        lanzados = io.lanza(
            conn,
            sol,
            huella=vista_hoy.huella,
            confirmacion=vista_hoy.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_lanzar(conn, vista_hoy.confirmacion_esperada),
            sugeridor=_sugeridor_fijo(),
            hoy=HOY,
        )
        assert lanzados[0].lote == viejo
        assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone() == (1,)
        estados = {
            payload.get("state")
            for ext, payload in amazon.objetos.items()
            if ext.startswith("campaigns-")
        }
        assert estados == {"ENABLED"}
        assert conn.execute(
            "SELECT tope_monto FROM impulso WHERE lote = %s", (viejo,)
        ).fetchone() == (Decimal("350"),)


@_skip_db
def test_r09r4_i04_planeado_con_externals_bloquea_502():
    """R09-r4 B6 (I04): lote planeado CON algo creado (otra huella) no
    se archiva ni se salta: 502 reconcilia a mano, sin ejecutar."""
    with _db_i3("orbit_i3_r4i04") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        viejo = f"impulso-{lid}-eeeeeeee"
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
            " 'eeee', '{}'::jsonb, 'shadow', 'planeado')",
            (viejo,),
        )
        conn.execute(
            "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
            " external_id, ack, readback_estado, estado) VALUES (%s, 1,"
            " 'category_exact'::campana_rol, 'campaign', '{}'::jsonb, 'campaigns-9',"
            " '{}'::jsonb, 'PAUSED', 'applied')",
            (viejo,),
        )
        conn.commit()
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        assert io.lote_de_plan(vista.planes[0]) != viejo
        llamadas = []

        def _cuenta(plan_grupo, huella, lote_llamado):
            llamadas.append(lote_llamado)
            raise AssertionError("la fabrica no debio ejecutarse")

        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                solicitud,
                huella=vista.huella,
                confirmacion=vista.confirmacion_esperada,
                actor="dueno",
                ejecutor=_cuenta,
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 502
        assert "reconcilia a mano" in str(exc.value.fallos)
        assert llamadas == []


@_skip_db
def test_r09r4_i07_failed_con_externals_no_se_descarta_solo():
    """R09-r4 B6 (I07): failed CON externals (otra huella) no se pausa
    y desarma solo: 502 reconcilia a mano (boton), sin ejecutar."""
    with _db_i3("orbit_i3_r4i07") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        viejo = f"impulso-{lid}-ffffffff"
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
            " 'ffff', '{}'::jsonb, 'shadow', 'failed')",
            (viejo,),
        )
        conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia,"
            " go_literal, tipo) VALUES ('amazon_mx', 'aros', 'Base', %s, 19.10,"
            " 19.1040, 0.5, 'm', 'go', 'impulso')",
            (viejo,),
        )
        conn.execute(
            "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
            " external_id, ack, readback_estado, estado) VALUES (%s, 1,"
            " 'category_exact'::campana_rol, 'campaign', '{}'::jsonb, 'campaigns-9',"
            " '{}'::jsonb, 'ENABLED', 'applied')",
            (viejo,),
        )
        conn.commit()
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        assert io.lote_de_plan(vista.planes[0]) != viejo
        llamadas = []

        def _cuenta(plan_grupo, huella, lote_llamado):
            llamadas.append(lote_llamado)
            raise AssertionError("la fabrica no debio ejecutarse")

        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                solicitud,
                huella=vista.huella,
                confirmacion=vista.confirmacion_esperada,
                actor="dueno",
                ejecutor=_cuenta,
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 502
        assert "reconcilia a mano" in str(exc.value.fallos)
        assert llamadas == []
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (viejo,)
        ).fetchone() == ("failed",)


@_skip_db
def test_r09r4_i08_applied_sin_grupo_no_se_sella():
    """R09-r4 B6 (I08): applied SIN grupo (crash entre lote y grupo):
    502 reconcilia a mano, no sella impulso sin grupo."""
    with _db_i3("orbit_i3_r4i08") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        viejo = f"impulso-{lid}-99999999"
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
            " '9999', '{}'::jsonb, 'shadow', 'applied')",
            (viejo,),
        )
        conn.commit()
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        assert io.lote_de_plan(vista.planes[0]) != viejo
        llamadas = []

        def _cuenta(plan_grupo, huella, lote_llamado):
            llamadas.append(lote_llamado)
            raise AssertionError("la fabrica no debio ejecutarse")

        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                solicitud,
                huella=vista.huella,
                confirmacion=vista.confirmacion_esperada,
                actor="dueno",
                ejecutor=_cuenta,
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 502
        assert "reconcilia a mano" in str(exc.value.fallos)
        assert llamadas == []
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (0,)


@_skip_db
def test_r09r4_i06_impulso_cerrado_no_se_reutiliza(monkeypatch):
    """R09-r4 B6 (I06): lote que YA tuvo impulso (cerrado con lectura
    final) no cuenta como huerfano NI aunque coincida el plan:
    relanzar crea un par nuevo, no reutiliza el cerrado. Ruta real."""
    import datetime as _dt
    from decimal import Decimal

    from app.api_fabrica import _ejecutor_lanzar

    with _db_i3("orbit_i3_r4i06") as conn:
        _, lid = _con_margen(conn)
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        sub = {lid: "aros"}
        ayer = HOY - _dt.timedelta(days=1)
        sol = io.SolicitudImpulso(
            plataforma="amazon_mx",
            listing_ids=(lid,),
            subfamilia_por_listing=sub,
            tope_monto=Decimal("350"),
        )
        vista = io.previsualiza(conn, sol, sugeridor=_sugeridor_fijo(), hoy=ayer)
        lanzados = io.lanza(
            conn,
            sol,
            huella=vista.huella,
            confirmacion=vista.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_lanzar(conn, vista.confirmacion_esperada),
            sugeridor=_sugeridor_fijo(),
            hoy=ayer,
        )
        viejo = lanzados[0].lote
        conn.execute(
            "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
            " impresiones, clics, gasto, pedidos, veredicto, es_final, hecho)"
            " VALUES (%s, %s, now(), 1, 100, 5, 10, 1, 'vende', true, 'graduado')",
            (lanzados[0].impulso_id, ayer),
        )
        conn.commit()
        vista2 = io.previsualiza(conn, sol, sugeridor=_sugeridor_fijo(), hoy=HOY)
        lanz2 = io.lanza(
            conn,
            sol,
            huella=vista2.huella,
            confirmacion=vista2.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_lanzar(conn, vista2.confirmacion_esperada),
            sugeridor=_sugeridor_fijo(),
            hoy=HOY,
        )
        assert lanz2[0].lote == io.lote_de_plan(vista2.planes[0])
        assert lanz2[0].lote != viejo
        assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone() == (2,)
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (2,)


@_skip_db
def test_r09r4_i02_like_no_confunde_listing_3_con_31():
    """R09-r4 B6 (I02, respaldo del NO-APLICA): el patron LIKE de
    pendientes lleva guion: un lote de impulso-31-* no aparece como
    pendiente de impulso-3 (ni bloquea ni se reutiliza)."""
    with _db_i3("orbit_i3_r4i02") as conn:
        _con_margen(conn)
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES ('impulso-31-aaaaaaaa', 'amazon_mx',"
            " 'aros', 'Base', 'go', 'aaaa', '{}'::jsonb, 'shadow', 'failed')"
        )
        conn.commit()
        n = conn.execute(
            "SELECT count(*) FROM fabrica_lote f WHERE f.lote LIKE %s", ("impulso-3-%",)
        ).fetchone()[0]
        assert n == 0
        n = conn.execute(
            "SELECT count(*) FROM fabrica_lote f WHERE f.lote LIKE %s", ("impulso-3%",)
        ).fetchone()[0]
        assert n == 1


@_skip_db
def test_r09r4_i09_dos_huerfanos_reutiliza_el_mas_nuevo(monkeypatch):
    """R09-r4 B6 (I09): dos huerfanos applied del MISMO plan: se
    reutiliza el mas nuevo (pendientes[0], created_at DESC), no el
    mas viejo. Ruta real: dos ejecuciones sin sellar + lanza."""
    import datetime as _dt
    from decimal import Decimal

    from app import impulso as im
    from app.api_fabrica import _ejecutor_lanzar

    with _db_i3("orbit_i3_r4i09") as conn:
        _, lid = _con_margen(conn)
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        sub = {lid: "aros"}
        sol = io.SolicitudImpulso(
            plataforma="amazon_mx",
            listing_ids=(lid,),
            subfamilia_por_listing=sub,
            tope_monto=Decimal("350"),
        )
        lotes = []
        for hace in (2, 1):
            dia = HOY - _dt.timedelta(days=hace)
            vista = io.previsualiza(conn, sol, sugeridor=_sugeridor_fijo(), hoy=dia)
            plan = vista.planes[0]
            lote = io.lote_de_plan(plan)
            hecho = _ejecutor_lanzar(conn, vista.confirmacion_esperada)(
                im.grupo_v2_de_plan(plan), plan.huella(), lote
            )
            assert hecho.estado_lote == "applied"
            lotes.append(lote)
        assert lotes[0] != lotes[1]
        vista_hoy = io.previsualiza(conn, sol, sugeridor=_sugeridor_fijo(), hoy=HOY)
        lanzados = io.lanza(
            conn,
            sol,
            huella=vista_hoy.huella,
            confirmacion=vista_hoy.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_lanzar(conn, vista_hoy.confirmacion_esperada),
            sugeridor=_sugeridor_fijo(),
            hoy=HOY,
        )
        assert lanzados[0].lote == lotes[1]
        assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone() == (2,)


@_skip_db
def test_lanza_failed_vacio_otra_huella_archiva_y_relanza(monkeypatch):
    """R09-r4 B1 (H6-pendientes): lote failed sin nada creado, de otra
    huella, no bloquea: se archiva (-descartado, desarmado) y el
    reintento crea el par nuevo. Ruta real."""
    from decimal import Decimal

    from app.api_fabrica import _ejecutor_lanzar

    with _db_i3("orbit_i3_r4h6p") as conn:
        _, lid = _con_margen(conn)
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        viejo = f"impulso-{lid}-00000000"
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
            " '0000', '{}'::jsonb, 'shadow', 'failed')",
            (viejo,),
        )
        conn.commit()
        sol = io.SolicitudImpulso(
            plataforma="amazon_mx",
            listing_ids=(lid,),
            subfamilia_por_listing={lid: "aros"},
            tope_monto=Decimal("350"),
        )
        vista = io.previsualiza(conn, sol, sugeridor=_sugeridor_fijo())
        assert io.lote_de_plan(vista.planes[0]) != viejo
        lanzados = io.lanza(
            conn,
            sol,
            huella=vista.huella,
            confirmacion=vista.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_lanzar(conn, vista.confirmacion_esperada),
            sugeridor=_sugeridor_fijo(),
        )
        assert lanzados[0].lote == io.lote_de_plan(vista.planes[0])
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (f"{viejo}-descartado",)
        ).fetchone() == ("desarmado",)
        assert conn.execute(
            "SELECT count(*) FROM impulso WHERE lote = %s", (lanzados[0].lote,)
        ).fetchone() == (1,)


@_skip_db
def test_vigia_atraso_cuenta_dias_con_dato_no_calendario():
    """R09 B3 (E): ingesta 3 dias atrasada: el dia 14 de calendario con
    11 dias con dato sigue en_curso y guarda 11, no 14."""
    with _db_i3("orbit_i3_r3c") as conn:
        d0 = HOY - dt.timedelta(days=14)
        ids = _impulso_vivo(conn, lanzado=d0, lote="impulso-r3c-1")
        for k in range(11):  # dias 0..10 con dato; 11..13 sin ingesta
            dia = d0 + dt.timedelta(days=k)
            for camp in ids["camps"]:
                _observa(conn, camp, dia, impr=90, clics=1, gasto="3")
        acciones = io.vigila(conn, _Pausador(), hoy=HOY)
        assert acciones[0].veredicto.estado == "en_curso"
        assert acciones[0].veredicto.final is False
        assert conn.execute(
            "SELECT corte, dias_observados, es_final FROM impulso_lectura WHERE impulso_id = %s",
            (ids["impulso"],),
        ).fetchone() == (d0 + dt.timedelta(days=10), 11, False)


@_skip_db
def test_vigia_d3_keywords_al_dia_no_cuentan_dias():
    """R09-r4 B2 (D3): reporte de campanas 3 dias atrasado y keywords
    al dia: los dias se cuentan con las campanas (11, en_curso), no
    con cualquier metrica (14, final)."""
    with _db_i3("orbit_i3_r4d3") as conn:
        d0 = HOY - dt.timedelta(days=14)
        ids = _impulso_vivo(conn, lanzado=d0, lote="impulso-r4d3-1")
        kid = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, name,"
            " match_type, keyword_text) VALUES ('amazon_mx', 'keyword', 'kw-d3',"
            " %s, 'kw d3', 'EXACT', 'kw d3') RETURNING id",
            (ids["camps"][0],),
        ).fetchone()[0]
        for k in range(14):  # keywords al dia: 0..13
            _observa(conn, kid, d0 + dt.timedelta(days=k), impr=10, clics=1, gasto="1")
        for k in range(11):  # campanas atrasadas: 0..10
            dia = d0 + dt.timedelta(days=k)
            for camp in ids["camps"]:
                _observa(conn, camp, dia, impr=90, clics=1, gasto="3")
        # I16: ingesta de OTRA plataforma no cuenta dias de MX.
        us = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, name)"
            " VALUES ('amazon_us', 'campaign', 'c-us-1', 'us') RETURNING id"
        ).fetchone()[0]
        _observa(conn, us, HOY - dt.timedelta(days=1), impr=50, clics=2, gasto="5", moneda="USD")
        # I18: dato del dia en curso (HOY): el corte se capa a ayer y los
        # dias no lo incluyen.
        for camp in ids["camps"]:
            _observa(conn, camp, HOY, impr=90, clics=1, gasto="3")
        acciones = io.vigila(conn, _Pausador(), hoy=HOY)
        assert acciones[0].veredicto.estado == "en_curso"
        assert acciones[0].veredicto.final is False
        assert conn.execute(
            "SELECT corte, dias_observados, es_final FROM impulso_lectura WHERE impulso_id = %s",
            (ids["impulso"],),
        ).fetchone() == (HOY - dt.timedelta(days=1), 11, False)


@_skip_db
def test_vigia_graduar_reactiva_lote_pausado():
    """R09 B4 (D): pedido con lote pausado: gradua (goals live) y reactiva
    las campanas para casar con Amazon."""
    with _db_i3("orbit_i3_r3d") as conn:
        ids = _impulso_vivo(conn, lote="impulso-r3d-1")
        conn.execute("UPDATE fabrica_lote SET estado = 'pausado' WHERE lote = %s", (ids["lote"],))
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        pausador = _Pausador()
        acciones = io.vigila(conn, pausador, hoy=HOY)
        assert acciones[0].hecho == "graduado"
        assert pausador.reactivados == [ids["lote"]]
        assert conn.execute(
            "SELECT mode FROM ads_optimizer_goal WHERE ad_entity_id = %s", (ids["camps"][0],)
        ).fetchone() == ("live",)


@_skip_db
def test_r09r4_g0_pausa_registra_actor(monkeypatch):
    """R09-r4 B3 (G0): pausar registra QUIEN pauso (dueno o vigia) en
    fabrica_lote.pausado_por. Pausador real, Amazon falso."""
    with _db_i3("orbit_i3_r4g0") as conn:
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        for lote in ("impulso-9-g0d", "impulso-9-g0v"):
            conn.execute(
                "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base,"
                " go_literal, huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx',"
                " 'aros', 'Base', 'go', 'h', '{}'::jsonb, 'shadow', 'applied')",
                (lote,),
            )
            ext = f"campaigns-{lote[-1:]}"
            amazon.objetos[ext] = {"campaignId": ext, "state": "ENABLED"}
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " external_id, ack, readback_estado, estado) VALUES (%s, 1,"
                " 'category_exact'::campana_rol, 'campaign', '{}'::jsonb, %s,"
                " '{}'::jsonb, 'ENABLED', 'applied')",
                (lote, ext),
            )
        conn.commit()
        io.PausadorFabrica(conn, actor="dueno").pausa("impulso-9-g0d")
        io.PausadorFabrica(conn).pausa("impulso-9-g0v")
        assert conn.execute(
            "SELECT lote, estado, pausado_por FROM fabrica_lote WHERE lote LIKE 'impulso-9-g0%'"
            " ORDER BY lote"
        ).fetchall() == [
            ("impulso-9-g0d", "pausado", "dueno"),
            ("impulso-9-g0v", "pausado", "impulso-vigia"),
        ]


@_skip_db
def test_r09r4_g1_dueno_pausa_gradua_sin_reencender(monkeypatch):
    """R09-r4 B3 (G1): pauso el dueno, llega un pedido: gradua (goals
    live, hecho graduado_sin_reencender) SIN mandar PUTs a Amazon;
    las campanas siguen PAUSED. Pausador real, Amazon falso."""
    with _db_i3("orbit_i3_r4g1") as conn:
        ids = _impulso_vivo(conn, lote="impulso-r4g1-1")
        conn.execute(
            "UPDATE fabrica_lote SET estado = 'pausado', pausado_por = 'dueno' WHERE lote = %s",
            (ids["lote"],),
        )
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        for i in range(2):
            ext = f"campaigns-{i + 1}"
            amazon.objetos[ext] = {"campaignId": ext, "state": "PAUSED"}
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " external_id, ack, readback_estado, estado) VALUES (%s, %s,"
                " %s::campana_rol, 'campaign', '{}'::jsonb, %s, '{}'::jsonb,"
                " 'PAUSED', 'applied')",
                (
                    ids["lote"],
                    10 + i,
                    ("category_exact", "auto_discovery")[i],
                    ext,
                ),
            )
        conn.commit()
        n_put_antes = len(
            [r for r in amazon.pedidos if r.method == "PUT"] if hasattr(amazon, "pedidos") else []
        )
        acciones = io.vigila(conn, io.PausadorFabrica(conn), hoy=HOY)
        assert acciones[0].hecho == "graduado_sin_reencender"
        assert acciones[0].veredicto.final is True
        assert [r for r in amazon.pedidos if r.method == "PUT"][n_put_antes:] == []
        assert {p["state"] for p in amazon.objetos.values()} == {"PAUSED"}
        assert conn.execute(
            "SELECT mode FROM ads_optimizer_goal WHERE ad_entity_id = %s", (ids["camps"][0],)
        ).fetchone() == ("live",)
        assert (
            conn.execute(
                "SELECT estado, detalle FROM fabrica_lote WHERE lote = %s", (ids["lote"],)
            ).fetchone()[0]
            == "pausado"
        )


@_skip_db
def test_r09r4_i21_applied_a_medias_tambien_reactiva(monkeypatch):
    """R09-r4 B6 (I21, caso A): lote applied (no 'pausado') con las
    campanas PAUSED en Amazon + pedido: gradua Y reactiva (PUTs).
    Pausador real, Amazon falso."""
    with _db_i3("orbit_i3_r4i21") as conn:
        ids = _impulso_vivo(conn, lote="impulso-r4i21-1")
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        for i in range(2):
            ext = f"campaigns-{i + 1}"
            amazon.objetos[ext] = {"campaignId": ext, "state": "PAUSED"}
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " external_id, ack, readback_estado, estado) VALUES (%s, %s,"
                " %s::campana_rol, 'campaign', '{}'::jsonb, %s, '{}'::jsonb,"
                " 'PAUSED', 'applied')",
                (ids["lote"], 10 + i, ("category_exact", "auto_discovery")[i], ext),
            )
        conn.commit()
        acciones = io.vigila(conn, io.PausadorFabrica(conn), hoy=HOY)
        assert acciones[0].hecho == "graduado"
        assert [r for r in amazon.pedidos if r.method == "PUT"] != []
        assert {p["state"] for p in amazon.objetos.values()} == {"ENABLED"}


@_skip_db
def test_r09r4_i11_lanza_actor_nul_422():
    """R09-r4 B6 (I11/A03): actor con NUL se frena con 422 antes de
    tocar Amazon (lanza y el validador compartido del borde)."""
    import pytest

    with _db_i3("orbit_i3_r4nul") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                solicitud,
                huella=vista.huella,
                confirmacion=vista.confirmacion_esperada,
                actor="due\x00no",
                ejecutor=lambda *a: (_ for _ in ()).throw(AssertionError("no debe ejecutar")),
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 422


@_skip_db
def test_r09r4_n17_reactiva_verifica_readback(monkeypatch):
    """R09-r4 B6 (N17): PUT ENABLED sordo al reactivar (ack ok, no
    aplica): el readback lo detecta, no gradua (aislado, lote sigue
    pausado, goals shadow)."""
    with _db_i3("orbit_i3_r4n17") as conn:
        ids = _impulso_vivo(conn, lote="impulso-r4n17-1")
        conn.execute(
            "UPDATE fabrica_lote SET estado = 'pausado', pausado_por = 'impulso-vigia'"
            " WHERE lote = %s",
            (ids["lote"],),
        )
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        amazon = _amazon_estado()
        amazon.put_falla = {1: "sordo", 2: "sordo"}
        _frontera_impulso(monkeypatch, conn, amazon)
        for i in range(2):
            ext = f"campaigns-{i + 1}"
            amazon.objetos[ext] = {"campaignId": ext, "state": "PAUSED"}
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " external_id, ack, readback_estado, estado) VALUES (%s, %s,"
                " %s::campana_rol, 'campaign', '{}'::jsonb, %s, '{}'::jsonb,"
                " 'PAUSED', 'applied')",
                (ids["lote"], 10 + i, ("category_exact", "auto_discovery")[i], ext),
            )
        conn.commit()
        acciones = io.vigila(conn, io.PausadorFabrica(conn), hoy=HOY)
        assert acciones == []
        assert conn.execute(
            "SELECT mode FROM ads_optimizer_goal WHERE ad_entity_id = %s", (ids["camps"][0],)
        ).fetchone() == ("shadow",)
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (ids["lote"],)
        ).fetchone() == ("pausado",)


@_skip_db
def test_r09r4_n20_reactiva_no_ignora_ack(monkeypatch):
    """R09-r4 B6 (N20): PUT ENABLED que aplica pero responde 400 al
    reactivar: se aborta por el ack aunque el readback diga ENABLED;
    no gradua."""
    with _db_i3("orbit_i3_r4n20") as conn:
        ids = _impulso_vivo(conn, lote="impulso-r4n20-1")
        conn.execute(
            "UPDATE fabrica_lote SET estado = 'pausado', pausado_por = 'impulso-vigia'"
            " WHERE lote = %s",
            (ids["lote"],),
        )
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        amazon = _amazon_estado()
        amazon.put_falla = {1: "miente"}
        _frontera_impulso(monkeypatch, conn, amazon)
        for i in range(2):
            ext = f"campaigns-{i + 1}"
            amazon.objetos[ext] = {"campaignId": ext, "state": "PAUSED"}
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " external_id, ack, readback_estado, estado) VALUES (%s, %s,"
                " %s::campana_rol, 'campaign', '{}'::jsonb, %s, '{}'::jsonb,"
                " 'PAUSED', 'applied')",
                (ids["lote"], 10 + i, ("category_exact", "auto_discovery")[i], ext),
            )
        conn.commit()
        acciones = io.vigila(conn, io.PausadorFabrica(conn), hoy=HOY)
        assert acciones == []
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (ids["lote"],)
        ).fetchone() == ("pausado",)


@_skip_db
def test_r09r4_g2_reactiva_a_medias_no_gradua(monkeypatch):
    """R09-r4 B3 (G2): pauso el vigia, llega un pedido pero Amazon
    rechaza el segundo PUT ENABLED: no gradua (goals siguen shadow,
    sin lectura final, lote pausado); cuando Amazon acepta, gradua."""
    with _db_i3("orbit_i3_r4g2") as conn:
        ids = _impulso_vivo(conn, lote="impulso-r4g2-1")
        conn.execute(
            "UPDATE fabrica_lote SET estado = 'pausado', pausado_por = 'impulso-vigia'"
            " WHERE lote = %s",
            (ids["lote"],),
        )
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        amazon = _amazon_estado()
        amazon.put_falla = {2: True}
        _frontera_impulso(monkeypatch, conn, amazon)
        for i in range(2):
            ext = f"campaigns-{i + 1}"
            amazon.objetos[ext] = {"campaignId": ext, "state": "PAUSED"}
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " external_id, ack, readback_estado, estado) VALUES (%s, %s,"
                " %s::campana_rol, 'campaign', '{}'::jsonb, %s, '{}'::jsonb,"
                " 'PAUSED', 'applied')",
                (ids["lote"], 10 + i, ("category_exact", "auto_discovery")[i], ext),
            )
        conn.commit()
        acciones = io.vigila(conn, io.PausadorFabrica(conn), hoy=HOY)
        assert acciones == []
        assert conn.execute(
            "SELECT mode FROM ads_optimizer_goal WHERE ad_entity_id = %s", (ids["camps"][0],)
        ).fetchone() == ("shadow",)
        assert conn.execute(
            "SELECT count(*) FROM impulso_lectura WHERE impulso_id = %s AND es_final",
            (ids["impulso"],),
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (ids["lote"],)
        ).fetchone() == ("pausado",)
        amazon.put_falla = {}
        acciones = io.vigila(conn, io.PausadorFabrica(conn), hoy=HOY)
        assert acciones[0].hecho == "graduado"
        assert amazon.objetos["campaigns-2"]["state"] == "ENABLED"


@_skip_db
def test_vigia_graduar_lote_desarmado_no_revive():
    """R09 B4: pedido con lote desarmado: gradua goals pero no reactiva
    (muerto es muerto)."""
    with _db_i3("orbit_i3_r3d2") as conn:
        ids = _impulso_vivo(conn, lote="impulso-r3d2-1")
        conn.execute("UPDATE fabrica_lote SET estado = 'desarmado' WHERE lote = %s", (ids["lote"],))
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1), pedidos=1)
        pausador = _Pausador()
        acciones = io.vigila(conn, pausador, hoy=HOY)
        assert acciones[0].hecho == "graduado"
        assert pausador.reactivados == []


def _con_margen_us(conn, *, sku="SS-US", asin="B0US000001", seller_sku="SS-US"):
    pid, lid = _producto(conn, sku=sku, asin=asin, platform="amazon_us", seller_sku=seller_sku)
    _ledger_producto(conn, pid, hoy=HOY - dt.timedelta(days=1), platform="amazon_us")
    from psycopg.types.json import Json

    conn.execute(
        "INSERT INTO config_version(label,settings) VALUES ('fabrica',%s)",
        (
            Json(
                {
                    "ads_target_fraccion_margen_amazon_mx": "0.5",
                    "ads_target_fraccion_margen_amazon_us": "0.5",
                }
            ),
        ),
    )
    conn.commit()
    return pid, lid


@_skip_db
def test_r09r3_preview_us_moneda_y_tope_por_plataforma():
    """R09 B12 (L10/L11/L12): en US el plan sale en USD con tope 36, no 350 MXN."""
    with _db_i3("orbit_i3_r3us") as conn:
        _, lid = _con_margen_us(conn)
        solicitud = io.SolicitudImpulso(
            plataforma="amazon_us",
            listing_ids=(lid,),
            subfamilia_por_listing={lid: "aros"},
        )
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        assert vista.moneda == "USD"
        assert vista.tope_total == Decimal("36")
        assert vista.planes[0].tope.moneda == "USD"
        assert vista.planes[0].tope.monto == Decimal("36")


@_skip_db
def test_r09r3_lanza_us_guarda_moneda_del_plan():
    """R09 B12 (L09): lanzar en US guarda USD como moneda del tope, no MXN fijo."""
    with _db_i3("orbit_i3_r3us2") as conn:
        _, lid = _con_margen_us(conn)
        solicitud = io.SolicitudImpulso(
            plataforma="amazon_us",
            listing_ids=(lid,),
            subfamilia_por_listing={lid: "aros"},
        )
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        lanzados = io.lanza(
            conn,
            solicitud,
            huella=vista.huella,
            confirmacion=vista.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_falso(conn, platform="amazon_us", moneda="USD"),
            sugeridor=_sugeridor_fijo(),
        )
        assert len(lanzados) == 1
        assert conn.execute(
            "SELECT tope_moneda, tope_monto FROM impulso WHERE id = %s",
            (lanzados[0].impulso_id,),
        ).fetchone() == ("USD", Decimal("36"))


@_skip_db
def test_r09r3_lanza_lote_no_applied_no_sella_impulso():
    """R09 B12 (L06): si la fabrica deja el lote fuera de applied, lanza da
    502 y no sella impulso."""
    with _db_i3("orbit_i3_r3l06") as conn:
        _, lid = _con_margen(conn)
        solicitud = _solicitud(conn, [lid])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        base = _ejecutor_falso(conn)

        def _casi(plan_grupo, huella, lote):
            hecho = base(plan_grupo, huella, lote)
            return io.EjecucionFabrica(
                lote=hecho.lote, grupo_id=hecho.grupo_id, estado_lote="failed"
            )

        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                solicitud,
                huella=vista.huella,
                confirmacion=vista.confirmacion_esperada,
                actor="dueno",
                ejecutor=_casi,
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 502
        assert conn.execute("SELECT count(*) FROM impulso").fetchone() == (0,)


@_skip_db
def test_r09r4_lanza_failed_con_grupo_sin_externals_archiva_y_reusa_nombre(monkeypatch):
    """R09-r4 B1 (H6): failed con grupo pero sin nada en Amazon: se
    archiva (el grupo se mueve al -descartado) y el reintento reusa
    el nombre canonico. Ruta real."""
    from app.api_fabrica import _ejecutor_lanzar

    with _db_i3("orbit_i3_r4h6m") as conn:
        _, lid = _con_margen(conn)
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        solicitud = _solicitud(conn, [lid])
        vista = io.previsualiza(conn, solicitud, sugeridor=_sugeridor_fijo())
        lote = io.lote_de_plan(vista.planes[0])
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
            " 'bbbb', '{}'::jsonb, 'shadow', 'failed')",
            (lote,),
        )
        conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia,"
            " go_literal, tipo) VALUES ('amazon_mx', 'aros', 'Base', %s, 19.10,"
            " 19.1040, 0.5, 'm', 'go', 'impulso')",
            (lote,),
        )
        conn.commit()
        lanzados = io.lanza(
            conn,
            solicitud,
            huella=vista.huella,
            confirmacion=vista.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_lanzar(conn, vista.confirmacion_esperada),
            sugeridor=_sugeridor_fijo(),
        )
        assert lanzados[0].lote == lote
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (lote,)
        ).fetchone() == ("applied",)
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (f"{lote}-descartado",)
        ).fetchone() == ("desarmado",)
        assert conn.execute(
            "SELECT lote FROM campana_grupo WHERE lote = %s", (f"{lote}-descartado",)
        ).fetchone() == (f"{lote}-descartado",)
        assert conn.execute("SELECT count(*) FROM impulso WHERE lote = %s", (lote,)).fetchone() == (
            1,
        )


class _AmazonPausa:
    """Amazon falso para el pausador: registra PUTs y contesta readback."""

    def __init__(self, *, readback="PAUSED", put_status=200):
        self.readback = readback
        self.put_status = put_status
        self.puts = []

    def put(self, request):
        import json as _json

        import httpx

        cuerpo = _json.loads(request.content.decode("utf-8"))
        self.puts.append(cuerpo["campaigns"][0])
        return httpx.Response(self.put_status, json={})

    def list_objects(self, path, body, *, profile_id):
        from types import SimpleNamespace

        exts = body["campaignIdFilter"]["include"]
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"campaigns": [{"campaignId": e, "state": self.readback} for e in exts]},
        )


def _pausador_real(conn, amazon):
    """PausadorFabrica real con red falsa: LWA+PUT por MockTransport, LIST
    por cliente falso. Solo credenciales y red; la logica intacta."""
    from types import SimpleNamespace

    import httpx

    def _handler(request):
        if request.url.path == "/auth/o2/token":
            return httpx.Response(200, json={"access_token": "tok"})
        if request.method == "PUT" and request.url.path == "/sp/campaigns":
            return amazon.put(request)
        return httpx.Response(404, json={})

    http = httpx.Client(transport=httpx.MockTransport(_handler))
    cred = SimpleNamespace(client_id="cid", client_secret="sec", refresh_token="rt")
    pausador = io.PausadorFabrica(conn)
    pausador._armado = (cred, amazon, http, {"amazon_mx": 101})
    return pausador


def _lote_pausable(conn, lote):
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'aros', 'Base', 'go',"
        " 'h', '{}'::jsonb, 'shadow', 'applied')",
        (lote,),
    )
    for orden, (rol, ext) in enumerate(
        (("category_exact", "c-1"), ("auto_discovery", "c-2")), start=1
    ):
        conn.execute(
            "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
            " external_id, ack, readback_estado, estado) VALUES (%s, %s, %s::campana_rol,"
            " 'campaign', '{}'::jsonb, %s, '{}'::jsonb, 'ENABLED', 'applied')",
            (lote, orden, rol, ext),
        )
    conn.commit()


@_skip_db
def test_r09r3_v17_pausador_real_pausa_en_amazon():
    """R09 B12 (V17): PausadorFabrica.pausa manda PUT PAUSED por campana,
    con fila en el ledger antes, readback y lote pausado."""
    with _db_i3("orbit_i3_r3v17") as conn:
        lote = "impulso-v17-1"
        _lote_pausable(conn, lote)
        amazon = _AmazonPausa()
        io.PausadorFabrica(conn)  # sin nada que pausar no toca la red
        _pausador_real(conn, amazon).pausa(lote)
        assert amazon.puts == [
            {"campaignId": "c-2", "state": "PAUSED"},
            {"campaignId": "c-1", "state": "PAUSED"},
        ]
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (lote,)
        ).fetchone() == ("pausado",)
        assert conn.execute(
            "SELECT count(*) FROM fabrica_lote_paso WHERE lote = %s"
            " AND request_payload->>'state' = 'PAUSED' AND estado = 'applied'",
            (lote,),
        ).fetchone() == (2,)


@_skip_db
def test_r09r3_t06_pausa_exige_readback():
    """R09 B12 (T06): si el readback no muestra PAUSED, la pausa aborta."""
    import tools.fabrica_campanas as fc

    with _db_i3("orbit_i3_r3t06") as conn:
        lote = "impulso-t06-1"
        _lote_pausable(conn, lote)
        amazon = _AmazonPausa(readback="ENABLED")
        with pytest.raises(fc.Abortar, match="readback"):
            _pausador_real(conn, amazon).pausa(lote)


@_skip_db
def test_r09r3_t07_put_rechazado_detiene_pausa():
    """R09 B12 (T07): si Amazon rechaza el PUT, la pausa aborta."""
    import tools.fabrica_campanas as fc

    with _db_i3("orbit_i3_r3t07") as conn:
        lote = "impulso-t07-1"
        _lote_pausable(conn, lote)
        amazon = _AmazonPausa(put_status=400)
        with pytest.raises(fc.Abortar, match="rechazado"):
            _pausador_real(conn, amazon).pausa(lote)


@_skip_db
def test_r09r3_v13_vigia_pausa_a_dos_diarios_del_tope():
    """R09 B12 (V13): por la ruta del vigia, gasto entre monto-2*diario y
    monto-diario pausa (con medio margen seguiria)."""
    with _db_i3("orbit_i3_r3v13") as conn:
        d0 = HOY - dt.timedelta(days=6)
        ids = _impulso_vivo(conn, lanzado=d0, lote="impulso-v13-1")
        for k in range(5):  # 5 dias con dato; gasto total 312.50
            dia = d0 + dt.timedelta(days=k)
            for camp in ids["camps"]:
                _observa(conn, camp, dia, gasto="31.25")
        pausador = _Pausador()
        acciones = io.vigila(conn, pausador, hoy=HOY)
        assert acciones[0].veredicto.estado == "clics_sin_ventas"
        assert acciones[0].hecho == "pausado"
        assert pausador.pausados == ["impulso-v13-1"]


@_skip_db
def test_r09r3_v04_vigia_no_retira_del_propio_grupo(monkeypatch):
    """R09 B12 (V04): el vigia saca de las bolsas viejas pero conserva los
    anuncios del propio grupo del impulso (pasa su grupo al plan)."""
    from app import retiro_anuncios

    real = retiro_anuncios.planea_retiro
    vistos = []

    def _espia(conn, plataforma, **kw):
        vistos.append(kw.get("conservar_grupo_id"))
        return real(conn, plataforma, **kw)

    monkeypatch.setattr(retiro_anuncios, "planea_retiro", _espia)
    with _db_i3("orbit_i3_r3v04") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lote="impulso-v04-1")
        ag_propio = conn.execute(
            "SELECT ad_group_ad_entity_id FROM campana_grupo_rol WHERE grupo_id = %s",
            (ids["grupo"],),
        ).fetchone()[0]
        _bolsa(conn, lid, "ad-viejo")
        propio = _entidad(conn, "amazon_mx", "product_ad", "ad-propio", ag_propio, lid)
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (propio,),
        )
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1))
        escritor, puts = _escritor(
            {**_fila("ad-viejo", "ENABLED"), **_fila("ad-propio", "ENABLED")}
        )
        uno = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor)
        assert uno[0].hecho == "retiro_de_bolsas"
        assert vistos == [ids["grupo"]]
        assert puts == [("ad-viejo", "PAUSED")]
        assert conn.execute("SELECT ad_id_externo FROM anuncio_retiro").fetchone() == ("ad-viejo",)


@_skip_db
def test_r09r3_v05_vigia_descuenta_retiro_abierto():
    """R09 B12 (V05): si el unico anuncio de la bolsa ya tiene un retiro
    abierto, el vigia no ejecuta otro (hecho nada)."""
    with _db_i3("orbit_i3_r3v05") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lote="impulso-v05-1")
        _bolsa(conn, lid, "ad-viejo")
        ent = conn.execute(
            "SELECT id FROM ad_entity WHERE kind = 'product_ad' AND external_id = 'ad-viejo'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
            " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
            " reversa, retirado_el, actor) VALUES ('amazon_mx', 'sin_pedidos',"
            " 'pausa', 'retiro-v05', NULL, %s, 'ad-viejo', 'ag-vieja', 'c-vieja', 'SS-1',"
            " 'B0AAAAAAAA', 'state=ENABLED', now(), 'dueno')",
            (ent,),
        )
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1))
        escritor, puts = _escritor(_fila("ad-viejo", "ENABLED"))
        uno = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor)
        assert uno[0].hecho == "nada"
        assert puts == []
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)


@_skip_db
def test_r09r4_v_vigia_retoma_intencion_pendiente():
    """R09-r4 B4 (Y-01 V, tabla propia): una intencion pendiente no
    descuenta: el vigia la incluye, manda el PUT y sella el retiro."""
    with _db_i3("orbit_i3_r4v") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lote="impulso-r4v")
        _bolsa(conn, lid, "ad-viejo")
        ent = conn.execute(
            "SELECT id FROM ad_entity WHERE kind = 'product_ad' AND external_id = 'ad-viejo'"
        ).fetchone()[0]
        iid = conn.execute(
            "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro,"
            " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
            " reversa, actor, creada_el) VALUES ('amazon_mx', 'sin_pedidos', 'pausa',"
            " 'retiro-v', %s, 'ad-viejo', 'ag-vieja', 'c-vieja', 'SS-1',"
            " 'B0AAAAAAAA', 'state=ENABLED', 'dueno', now()) RETURNING id",
            (ent,),
        ).fetchone()[0]
        conn.commit()
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1))
        escritor, puts = _escritor(_fila("ad-viejo", "ENABLED"))
        uno = io.vigila(conn, _Pausador(), hoy=HOY, escritor_retiro=escritor)
        assert uno[0].hecho == "retiro_de_bolsas"
        assert puts == [("ad-viejo", "PAUSED")]
        assert conn.execute("SELECT count(*) FROM anuncio_retiro").fetchone() == (1,)
        assert (
            conn.execute(
                "SELECT retiro_id IS NOT NULL FROM anuncio_retiro_intencion WHERE id = %s",
                (iid,),
            ).fetchone()[0]
            is True
        )


class _AmazonImpulsoReal:
    """Placeholder: se define abajo con la frontera (ver _frontera_impulso)."""


def _frontera_impulso(monkeypatch, conn, amazon):
    """Frontera mock para lanzar de verdad: credenciales, perfiles, HTTP
    falso, sync minimo a ad_entity. Como el R5 de test_api_fabrica."""
    from types import SimpleNamespace

    import httpx
    import psycopg
    from test_fabrica_campanas import _CREDS, _ClienteLectura, _perfil

    import tools.fabrica_campanas as fc
    from app.ads.config import AdsCredentials

    transporte = httpx.MockTransport(amazon)
    monkeypatch.setattr(
        AdsCredentials, "from_secrets_dir", classmethod(lambda cls, *a, **k: _CREDS)
    )
    monkeypatch.setattr(fc, "AdsClient", lambda cred: _ClienteLectura(transporte))
    monkeypatch.setattr(fc, "evaluar_perfiles", lambda c: [_perfil()])
    monkeypatch.setattr(
        fc,
        "httpx",
        SimpleNamespace(
            Client=lambda **kw: httpx.Client(transport=transporte),
            Timeout=lambda **kw: None,
        ),
    )
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    dsn = conn.info.dsn
    dbname = conn.info.dbname
    monkeypatch.setattr(fc, "connect", lambda d: psycopg.connect(dsn, dbname=dbname))
    for var in ("ORBIT_DSN_READ", "ORBIT_DSN_ADMIN", "ORBIT_DSN_INGEST"):
        monkeypatch.setenv(var, dsn)

    def _sync(cliente_lectura):
        propia = psycopg.connect(dsn, dbname=dbname, autocommit=True)
        try:
            camps = {}
            for ext in amazon.objetos:
                base, num = ext.rsplit("-", 1)
                if base == "campaigns":
                    ya = propia.execute(
                        "INSERT INTO ad_entity (platform, kind, external_id) VALUES"
                        " ('amazon_mx', 'campaign', %s) ON CONFLICT DO NOTHING RETURNING id",
                        (ext,),
                    ).fetchone()
                    if ya is None:
                        ya = propia.execute(
                            "SELECT id FROM ad_entity WHERE platform = 'amazon_mx'"
                            " AND kind = 'campaign' AND external_id = %s",
                            (ext,),
                        ).fetchone()
                    camps[num] = ya[0]
            for ext in amazon.objetos:
                base, num = ext.rsplit("-", 1)
                if base == "adGroups":
                    propia.execute(
                        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
                        " VALUES ('amazon_mx', 'ad_group', %s, %s) ON CONFLICT DO NOTHING",
                        (ext, camps[num]),
                    )
        finally:
            propia.close()

    monkeypatch.setattr(fc, "_sync", _sync)


def _amazon_estado():
    """Amazon falso con estado: crea con el state pedido, PUT actualiza,
    LIST lee lo vivo."""
    import json as _json

    import httpx
    from test_fabrica_campanas import _Amazon

    import app.fabrica_plan as fp

    class _ConEstado(_Amazon):
        # put_falla: n_put -> True (400) | "sordo" (207 sin aplicar) |
        # "miente" (aplica pero 400). G2/N17/N20.
        put_falla: dict = {}

        def __call__(self, request):
            if request.method == "PUT" and str(request.url).endswith("/sp/campaigns"):
                self.pedidos.append(request)
                cuerpo = _json.loads(request.content)
                self.n_put = getattr(self, "n_put", 0) + 1
                modo = self.put_falla.get(self.n_put)
                if modo == "sordo":
                    ok = [
                        {"index": i, "campaignId": str(c["campaignId"])}
                        for i, c in enumerate(cuerpo["campaigns"])
                    ]
                    return httpx.Response(207, json={"campaigns": {"success": ok, "error": []}})
                if modo == "miente":
                    for c in cuerpo["campaigns"]:
                        self.objetos[str(c["campaignId"])]["state"] = str(c["state"])
                    return httpx.Response(400, json={"code": "400", "details": "rechazado"})
                if modo:
                    return httpx.Response(400, json={"code": "400", "details": "rechazado"})
                ok = []
                for i, c in enumerate(cuerpo["campaigns"]):
                    ext = str(c["campaignId"])
                    self.objetos[ext]["state"] = str(c["state"])
                    ok.append({"index": i, "campaignId": ext})
                return httpx.Response(207, json={"campaigns": {"success": ok, "error": []}})
            return super().__call__(request)

        def _list(self, path, body):
            filtro = fp.FILTRO_ID_POR_LIST[path]
            ext = body[filtro]["include"][0]
            contenedor = fp.CONTENEDOR_POR_LIST[path]
            clave = fp.CLAVE_ID_POR_PATH[path.removesuffix("/list")]
            estado = self.objetos.get(ext, {}).get("state", "ENABLED")
            return httpx.Response(
                200,
                json={contenedor: [{**self.objetos.get(ext, {}), clave: ext, "state": estado}]},
            )

    return _ConEstado()


@_skip_db
@_skip_db
def test_r09r4_l2_vigia_pausa_huerfana_enabled_de_dia_anterior(monkeypatch):
    """R09-r4 B1 (L2): lote de impulso de AYER con campanas ENABLED en
    Amazon y sin impulso: el vigia las pausa (PUT + readback). Un lote
    de HOY no se toca (puede ser un lanzamiento en curso)."""
    with _db_i3("orbit_i3_r4l2v") as conn:
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        viejo, nuevo = "impulso-9-viejo000", "impulso-9-hoy00000"
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado, created_at) VALUES (%s, 'amazon_mx', 'aros',"
            " 'Base', 'go', 'v', '{}'::jsonb, 'shadow', 'failed', '2026-10-08 12:00+00')",
            (viejo,),
        )
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado, created_at) VALUES (%s, 'amazon_mx', 'aros',"
            " 'Base', 'go', 'n', '{}'::jsonb, 'shadow', 'applied', '2026-10-09 12:00+00')",
            (nuevo,),
        )
        semillas = (
            (viejo, "category_exact", "campaigns-1"),
            (viejo, "auto_discovery", "campaigns-2"),
            (nuevo, "category_exact", "campaigns-3"),
        )
        for i, (lote, rol, ext) in enumerate(semillas, start=1):
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " external_id, ack, readback_estado, estado) VALUES (%s, %s, %s::campana_rol,"
                " 'campaign', '{}'::jsonb, %s, '{}'::jsonb, 'ENABLED', 'applied')",
                (lote, i, rol, ext),
            )
            amazon.objetos[ext] = {"campaignId": ext, "state": "ENABLED"}
        conn.commit()
        pausados = io.reconcilia_huerfanos(conn, io.PausadorFabrica(conn), HOY)
        assert pausados == [viejo]
        assert amazon.objetos["campaigns-1"]["state"] == "PAUSED"
        assert amazon.objetos["campaigns-2"]["state"] == "PAUSED"
        assert amazon.objetos["campaigns-3"]["state"] == "ENABLED"


@_skip_db
def test_r09r4_a01_a02_lanza_ejecutor_revierte_y_nace_pausado(monkeypatch):
    """R09-r4 B6 (A01/A02): el ejecutor de la ruta crea PAUSED y con
    rollback: si Amazon rechaza la activacion, revierte (PUTs PAUSED),
    lote failed, 502 que lo dice. Ruta real."""
    from decimal import Decimal

    import pytest

    from app.api_fabrica import _ejecutor_lanzar

    with _db_i3("orbit_i3_r4a01") as conn:
        _, lid = _con_margen(conn)
        amazon = _amazon_estado()
        # Falla el PUT#2 (c1 ya quedo ENABLED): sin rollback c1
        # quedaria huerfana ENABLED.
        amazon.put_falla = {2: True}
        _frontera_impulso(monkeypatch, conn, amazon)
        sub = {lid: "aros"}
        sol = io.SolicitudImpulso(
            plataforma="amazon_mx",
            listing_ids=(lid,),
            subfamilia_por_listing=sub,
            tope_monto=Decimal("350"),
        )
        vista = io.previsualiza(conn, sol, sugeridor=_sugeridor_fijo())
        with pytest.raises(io.ErrorImpulso) as exc:
            io.lanza(
                conn,
                sol,
                huella=vista.huella,
                confirmacion=vista.confirmacion_esperada,
                actor="dueno",
                ejecutor=_ejecutor_lanzar(conn, vista.confirmacion_esperada),
                sugeridor=_sugeridor_fijo(),
            )
        assert exc.value.status == 502
        # A02: las campanas nacieron PAUSED (el POST trae state PAUSED).
        posts = [r for r in amazon.pedidos if r.method == "POST" and r.url.path == "/sp/campaigns"]
        assert len(posts) == 2
        import json as _json

        for r in posts:
            for _env, filas in _json.loads(r.content).items():
                for f in filas:
                    if "state" in f:
                        assert f["state"] == "PAUSED"
        # A01: rollback mando PAUSED y nada queda ENABLED.
        puts = [r for r in amazon.pedidos if r.method == "PUT"]
        assert puts != []
        assert all(
            p.get("state") == "PAUSED"
            for x, p in amazon.objetos.items()
            if x.startswith("campaigns-")
        )
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s",
            (io.lote_de_plan(vista.planes[0]),),
        ).fetchone() == ("failed",)


def test_r09r4_h2_huerfano_otro_plan_se_pausa_y_no_se_reusa(monkeypatch):
    """R09-r4 B1 (X-H2): un huerfano applied de OTRO plan (tope 350)
    no se reutiliza con el plan confirmado (tope 350): se pausa, se
    desarma y se crea el par nuevo con el presupuesto confirmado."""
    from decimal import Decimal

    from app import impulso as im
    from app.api_fabrica import _ejecutor_lanzar

    with _db_i3("orbit_i3_r4h2") as conn:
        _, lid = _con_margen(conn)
        amazon = _amazon_estado()
        _frontera_impulso(monkeypatch, conn, amazon)
        sub = {lid: "aros"}
        sol1 = io.SolicitudImpulso(
            plataforma="amazon_mx",
            listing_ids=(lid,),
            subfamilia_por_listing=sub,
            tope_monto=Decimal("350"),
        )
        vista1 = io.previsualiza(conn, sol1, sugeridor=_sugeridor_fijo())
        plan1 = vista1.planes[0]
        # Dia 1: la fabrica crea el par pero la fila de impulso nunca se
        # sella (crash tras _mutar): huerfano applied sin impulso.
        hecho = _ejecutor_lanzar(conn, vista1.confirmacion_esperada)(
            im.grupo_v2_de_plan(plan1), plan1.huella(), io.lote_de_plan(plan1)
        )
        assert hecho.estado_lote == "applied"
        viejo = io.lote_de_plan(plan1)
        sol2 = io.SolicitudImpulso(
            plataforma="amazon_mx",
            listing_ids=(lid,),
            subfamilia_por_listing=sub,
            tope_monto=Decimal("140"),
        )
        vista2 = io.previsualiza(conn, sol2, sugeridor=_sugeridor_fijo())
        assert vista2.huella != vista1.huella
        lanz2 = io.lanza(
            conn,
            sol2,
            huella=vista2.huella,
            confirmacion=vista2.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_lanzar(conn, vista2.confirmacion_esperada),
            sugeridor=_sugeridor_fijo(),
        )
        assert lanz2[0].lote != viejo
        assert conn.execute(
            "SELECT estado FROM fabrica_lote WHERE lote = %s", (viejo,)
        ).fetchone() == ("desarmado",)
        assert conn.execute(
            "SELECT tope_monto FROM impulso WHERE lote = %s", (lanz2[0].lote,)
        ).fetchone() == (Decimal("140"),)
        viejo_exts = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT external_id FROM fabrica_lote_paso WHERE lote = %s"
                " AND recurso = 'campaign' AND external_id IS NOT NULL",
                (viejo,),
            ).fetchall()
        }
        estados = {
            ext: p.get("state") for ext, p in amazon.objetos.items() if ext.startswith("campaigns-")
        }
        assert {estados[x] for x in viejo_exts} == {"PAUSED"}
        assert sum(1 for e in estados.values() if e == "ENABLED") == 2


@_skip_db
def test_r09r4_i02_like_funcional_31_no_toca_al_3():
    """R09-r4 B6 (I02 funcional): con un lote failed impulso-31-* sin nada
    creado, lanzar el listing 3 crea su par aparte y deja el de 31
    intacto (H6 no lo archiva, no se reutiliza, no bloquea). Mata la
    reescritura del mutante I02 en cualquiera de los 3 sitios LIKE."""
    with _db_i3("orbit_i3_r4i02b") as conn:
        lids = []
        for i in range(12):
            _pid, lid = _con_margen(
                conn,
                sku=f"SS-P{i}",
                asin=f"B0{i:08d}",
                seller_sku=f"SS-P{i}",
                desfase=i * 7,
            )
            lids.append(lid)
        uno, doce = lids[0], lids[11]
        assert str(doce).startswith(str(uno)) and uno != doce
        lote_doce = f"impulso-{doce}-bbbbbbbb"
        lote_uno_vacio = f"impulso-{uno}-deadbeef"
        for lote in (lote_doce, lote_uno_vacio):
            conn.execute(
                "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
                " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx',"
                " 'aros', 'Base', 'go', 'bbbb', '{}'::jsonb, 'shadow', 'failed')",
                (lote,),
            )
        conn.commit()
        vista = io.previsualiza(conn, _solicitud(conn, [uno]), sugeridor=_sugeridor_fijo())
        lanzados = io.lanza(
            conn,
            _solicitud(conn, [uno]),
            huella=vista.huella,
            confirmacion=vista.confirmacion_esperada,
            actor="dueno",
            ejecutor=_ejecutor_falso(conn),
            sugeridor=_sugeridor_fijo(),
        )
        assert len(lanzados) == 1
        assert lanzados[0].lote.startswith(f"impulso-{uno}-")
        assert (
            conn.execute(
                "SELECT estado FROM fabrica_lote WHERE lote = %s", (lote_doce,)
            ).fetchone()[0]
            == "failed"
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM fabrica_lote WHERE lote LIKE %s",
                (lote_doce + "%descartado%",),
            ).fetchone()[0]
            == 0
        )
