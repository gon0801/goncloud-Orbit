"""TEMPORADA 01, bloque A: calendario y parametros en Ajustes.

Dos pruebas nombradas por la tabla del diseno + una de cableado:

- `test_temporada01_a_fechas_del_calendario` (dinero): los casos de
  «Temporada baja, salida y alta -> Calendario» del diseno (1 ene baja;
  regreso 8 feb 2027 y 9 feb 2028; ago-sep-oct con 42 dias -> 20 ago;
  dic-feb un solo bloque; fin_anticipado).
- `test_temporada01_a_post_rechaza_booleanos` (escribe en produccion):
  true/false en un campo de numero dan 422 con el mensaje exacto y la
  config no cambia; "25" y 25 se aceptan.
- `test_temporada01_a_campana_topada_una_definicion` (dinero):
  ads_campana_topada guardado lo leen pantalla_dinero y avisos_campana
  (una sola definicion: temporada.campana_topada_desde_settings).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json
from test_api_write import TOKEN, _db_con_rol_admin, _secrets_token
from test_pantalla_dinero import (
    HASTA_2A,
    _db_dinero,
    _siembra_campana,
    _siembra_config,
    _siembra_gasto_campana,
)
from test_schema import _postgres_obligatorio_ausente

from app.main import app
from app.optimizer import temporada

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

MESES_DEFECTO = list(temporada.MESES_POR_DEFECTO)


def _crudo(meses=MESES_DEFECTO, prep=21, reduccion="30", fin=None, mult="2", pausadas=None):
    """El objeto ads_temporada tal como viaja y se guarda."""
    return {
        "version": temporada.VERSION_TEMPORADA,
        "meses": list(meses),
        "reduccion_baja_pct": reduccion,
        "dias_preparacion": prep,
        "mult_cortes_baja": mult,
        "aumento_presupuesto_alta_pct": "25",
        "fin_anticipado": fin,
        "mantener_pausadas": [] if pausadas is None else list(pausadas),
    }


def test_temporada01_a_fechas_del_calendario():
    """Los casos del calendario del diseno, sobre fechas reales."""
    config = temporada.valida(_crudo())

    # 1 de enero: baja, el bloque abre ese dia.
    est = temporada.resuelve(config, dt.date(2027, 1, 1))
    assert est.fase == "baja"
    assert est.inicio_bloque == dt.date(2027, 1, 1)
    assert est.reduccion_baja == Decimal("0.30")

    # Regreso el 8 feb 2027 (preparacion de marzo, 21 dias antes del 1 mar).
    assert temporada.resuelve(config, dt.date(2027, 2, 7)).fase == "baja"
    regreso_2027 = temporada.resuelve(config, dt.date(2027, 2, 8))
    assert regreso_2027.fase == "normal"
    assert regreso_2027.previa_alta is True

    # Ano bisiesto: el regreso de 2028 cae el 9 feb.
    assert temporada.resuelve(config, dt.date(2028, 2, 8)).fase == "baja"
    regreso_2028 = temporada.resuelve(config, dt.date(2028, 2, 9))
    assert regreso_2028.fase == "normal"
    assert regreso_2028.previa_alta is True

    # Marzo y abril son alta; noviembre tambien.
    assert temporada.resuelve(config, dt.date(2027, 3, 1)).fase == "alta"
    assert temporada.resuelve(config, dt.date(2027, 11, 15)).fase == "alta"
    # La preparacion de octubre empieza el 10 sep y no mueve bids (no viene de baja).
    previa_oct = temporada.resuelve(config, dt.date(2027, 9, 15))
    assert previa_oct.fase == "normal"
    assert previa_oct.previa_alta is True

    # ago-sep-oct con 42 dias de preparacion: el regreso es el 20 de agosto,
    # aunque haya un mes normal en medio.
    meses_ago = ["normal"] * 12
    meses_ago[7] = "baja"  # agosto
    meses_ago[8] = "normal"  # septiembre
    meses_ago[9] = "alta"  # octubre
    config_ago = temporada.valida(_crudo(meses=meses_ago, prep=42))
    assert temporada.resuelve(config_ago, dt.date(2027, 8, 19)).fase == "baja"
    regreso_ago = temporada.resuelve(config_ago, dt.date(2027, 8, 20))
    assert regreso_ago.fase == "normal"
    assert regreso_ago.previa_alta is True
    # La fase normal-preparacion abre el dia del regreso.
    assert regreso_ago.inicio_bloque == dt.date(2027, 8, 20)
    assert temporada.resuelve(config_ago, dt.date(2027, 8, 1)).fase == "baja"
    assert temporada.resuelve(config_ago, dt.date(2027, 10, 5)).fase == "alta"

    # dic-feb es un solo bloque: enero no abre otro episodio.
    meses_dic = ["baja", "baja", "alta", "alta"] + ["normal"] * 7 + ["baja"]
    config_dic = temporada.valida(_crudo(meses=meses_dic))
    ene = temporada.resuelve(config_dic, dt.date(2027, 1, 10))
    assert ene.fase == "baja"
    assert ene.inicio_bloque == dt.date(2026, 12, 1)
    # El bloque entero cierra con la preparacion de marzo: 8 feb 2027.
    assert temporada.resuelve(config_dic, dt.date(2027, 2, 8)).fase == "normal"
    assert temporada.resuelve(config_dic, dt.date(2026, 12, 15)).fase == "baja"

    # fin_anticipado: terminar la baja el 15 de enero.
    config_fin = temporada.valida(_crudo(fin="2027-01-15"))
    assert temporada.resuelve(config_fin, dt.date(2027, 1, 14)).fase == "baja"
    fin = temporada.resuelve(config_fin, dt.date(2027, 1, 15))
    assert fin.fase == "normal"
    # Aun no es preparacion del bloque alto: esa empieza el 8 feb.
    assert fin.previa_alta is False
    # La alta de marzo sigue tal cual.
    assert temporada.resuelve(config_fin, dt.date(2027, 3, 1)).fase == "alta"
    # El resumen anuncia el regreso en el dia elegido y el aviso de la baja
    # que hoy empieza cuando la edicion cambia la fase de hoy.
    resumen = temporada.resumen(
        config_fin,
        dt.date(2027, 1, 15),
        antes=config,
        metas_hoy={"amazon_mx": Decimal("20.08")},
        meta_alta={"amazon_mx": Decimal("25"), "amazon_us": Decimal("25")},
    )
    tipos_fechas = [(e.tipo, e.fecha) for e in resumen.proximos]
    assert ("restaura", dt.date(2027, 1, 15)) in tipos_fechas
    assert ("entra_baja", dt.date(2027, 5, 1)) in tipos_fechas
    assert resumen.aviso_cambio is not None
    assert "temporada baja" in resumen.aviso_cambio

    # Calendario todo-alta (valida lo acepta): es continuo y no anuncia
    # transiciones (hallazgo de la revision: sale_alta mentiria porque no
    # existe un mes normal al que volver).
    config_alta = temporada.valida(_crudo(meses=["alta"] * 12))
    assert temporada.resuelve(config_alta, dt.date(2027, 6, 15)).fase == "alta"
    assert temporada.resuelve(config_alta, dt.date(2027, 12, 20)).fase == "alta"
    assert temporada.eventos(config_alta, dt.date(2027, 1, 1), dt.date(2027, 12, 31)) == ()

    # Una baja comida por la preparacion se rechaza: con febrero en baja,
    # marzo en alta y 42 dias de preparacion, la preparacion empieza el 18 de
    # enero y se come TODO febrero (hallazgo de revision).
    meses_comida = ["normal", "baja", "alta"] + ["normal"] * 9
    with pytest.raises(ValueError) as error:
        temporada.valida(_crudo(meses=meses_comida, prep=42))
    assert "la deja en 0" in str(error.value)

    # El fin anticipado de enero vive en el bloque que abrio en diciembre
    # (hallazgo de revision: el bloque cruza de un ano al otro).
    config_cruce = temporada.valida(_crudo(meses=meses_dic, fin="2027-01-10"))
    assert config_cruce.fin_anticipado == dt.date(2027, 1, 10)
    assert temporada.resuelve(config_cruce, dt.date(2027, 1, 9)).fase == "baja"
    # El dia del fin ya es normal: la baja termina esa fecha.
    assert temporada.resuelve(config_cruce, dt.date(2027, 1, 10)).fase == "normal"

    # Un fin que queda fuera del calendario editado se descarta al validar
    # (hallazgo de revision: no gobierna nada y no debe bloquear el guardado;
    # el boton «Terminar la baja hoy» lo vuelve a escribir cuando haga falta).
    meses_sin_enero = list(MESES_DEFECTO)
    meses_sin_enero[0] = "normal"
    config_viejo = temporada.valida(_crudo(meses=meses_sin_enero, fin="2027-01-15"))
    assert config_viejo.fin_anticipado is None


@_skip_db
def test_temporada01_a_post_rechaza_booleanos(tmp_path, monkeypatch):
    """true/false en campo de numero dan 422 con el mensaje exacto y la
    config no cambia; "25" y 25 se aceptan (fila nueva)."""
    with _db_con_rol_admin("orbit_t01a_post") as (conn, dsn_admin, dsn_lectura):
        id_viejo = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json({"ads_optimizer_mode": "shadow"})),
        ).fetchone()[0]
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
        cliente = TestClient(app)

        def post_temporada(temporada_crudo, campana_topada=None, base=id_viejo):
            cuerpo = {"base_config_version_id": base, "temporada": temporada_crudo}
            if campana_topada is not None:
                cuerpo["campana_topada"] = campana_topada
            return cliente.post(
                "/api/ads-optimizer/settings/amazon_mx",
                json=cuerpo,
                headers={"x-orbit-token": TOKEN},
            )

        # true en un campo de numero: 422 con el mensaje exacto y cero filas nuevas.
        resp = post_temporada(_crudo(reduccion=True))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "Escribe un número, no sí o no."
        # false en el criterio de campana topada: el mismo mensaje.
        resp = post_temporada(_crudo(), campana_topada={"pct": False, "dias": 5})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "Escribe un número, no sí o no."
        # Coma decimal: mensaje exacto de la tabla.
        resp = post_temporada(_crudo(reduccion="30,5"))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "Escribe solo el número, por ejemplo 30."
        assert conn.execute("SELECT count(*) FROM config_version").fetchone()[0] == 1

        # "25" y 25 se aceptan (dos guardados, fila nueva en cada uno).
        resp = post_temporada(_crudo(reduccion="30"))
        assert resp.status_code == 200, resp.text
        id_1 = resp.json()["config_version_id"]
        fila = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s", (id_1,)
        ).fetchone()[0]
        assert fila["ads_temporada"]["reduccion_baja_pct"] == "30"
        assert fila["ads_temporada"]["dias_preparacion"] == 21
        assert fila["ads_temporada"]["meses"] == MESES_DEFECTO
        # Sin edicion del criterio, la clave no viaja (ausente = default del lector).
        assert "ads_campana_topada" not in fila

        resp = post_temporada(
            _crudo(reduccion=25), campana_topada={"pct": "90", "dias": 5}, base=id_1
        )
        assert resp.status_code == 200, resp.text
        id_2 = resp.json()["config_version_id"]
        fila = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s", (id_2,)
        ).fetchone()[0]
        assert fila["ads_temporada"]["reduccion_baja_pct"] == "25"
        assert fila["ads_campana_topada"] == {"pct": "90", "dias": 5}
        # La respuesta trae el resumen de la temporada (vista de «Así queda tu año»).
        assert resp.json()["resumen_temporada"]["fase_hoy"] in ("baja", "normal", "alta")

        # Modos y metas como objetos (bloqueo de Kimi 2): lo que no es objeto
        # se rechaza con 422 en texto, jamas 500.
        for cuerpo_malo, mensaje in (
            (
                {"temporada_modo": []},
                "El modo de temporada llegó mal: recarga la página y vuelve a guardar.",
            ),
            (
                {"meta_alta": []},
                "La meta de temporada alta llegó mal: recarga la página y vuelve a guardar.",
            ),
        ):
            resp = cliente.post(
                "/api/ads-optimizer/settings/amazon_mx",
                json={"base_config_version_id": id_2, **cuerpo_malo},
                headers={"x-orbit-token": TOKEN},
            )
            assert resp.status_code == 422, resp.text
            assert resp.json()["detail"] == mensaje
        assert conn.execute("SELECT count(*) FROM config_version").fetchone()[0] == 3

        # mantener_pausadas con ids reales y la memoria de la subida rapida
        # se aceptan sin 500 (bloqueo de Kimi 1: _entero con firma rota).
        resp = cliente.post(
            "/api/ads-optimizer/settings/amazon_mx",
            json={
                "base_config_version_id": id_2,
                "temporada": _crudo(reduccion=25, pausadas=[1]),
                "recuperacion_memoria_dias": 365,
            },
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 200, resp.text
        id_3 = resp.json()["config_version_id"]
        fila = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s", (id_3,)
        ).fetchone()[0]
        assert fila["ads_temporada"]["mantener_pausadas"] == [1]
        assert fila["ads_bid_recuperacion_memoria_dias_amazon_mx"] == 365
        # El POST sin cambios sobre config YA guardada sigue en «Nada que
        # cambiar» a nivel servidor: EdicionVacia.
        resp = cliente.post(
            "/api/ads-optimizer/settings/amazon_mx",
            json={"base_config_version_id": id_3, "temporada": _crudo(reduccion=25, pausadas=[1])},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422, resp.text

        # La vista previa sigue viva con la config guardada SIN fin anticipado
        # (hallazgo de revision: un None no se formatea, jamas 500).
        meses = ",".join(MESES_DEFECTO)
        resp = cliente.get(
            "/api/ads-optimizer/temporada/vista-previa",
            params={
                "meses": meses,
                "reduccion_baja_pct": "30",
                "dias_preparacion": "21",
                "mult_cortes_baja": "2",
                "aumento_presupuesto_alta_pct": "25",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resumen"]["fase_hoy"] in ("baja", "normal", "alta")


@_skip_db
def test_temporada01_a_campana_topada_una_definicion():
    """ads_campana_topada guardado lo leen dinero y avisos con el mismo lector."""
    from app.avisos_campana import avisos_del_dia
    from app.pantalla_dinero import FilaCampana, campana_topada_vigente, lee_campanas

    with _db_dinero("orbit_t01a_topada") as conn:
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s)",
            ("topada", Json({"ads_campana_topada": {"pct": "80", "dias": 3}})),
        )
        topada = campana_topada_vigente(conn)
        assert topada.pct == Decimal("0.80")
        assert topada.dias == 3

        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        obs = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        c1 = _siembra_campana(conn, "amazon_mx", "c1", "Topada 80")
        _siembra_config(conn, c1, dt.datetime(2026, 9, 27, tzinfo=dt.UTC), Decimal("100"), "MANUAL")
        for i in range(7):
            dia = HASTA_2A - dt.timedelta(days=6 - i)
            # 85 de 100: al tope con 80 %, debajo del 90 % de hoy.
            _siembra_gasto_campana(
                conn, c1, dia, Decimal("85") if i < 3 else Decimal("0"), obs, run
            )
        filas = lee_campanas(
            conn, plataforma="amazon_mx", desde=HASTA_2A - dt.timedelta(days=29), hasta=HASTA_2A
        )
        assert filas[0].dias_al_tope_7d == 3

        # La pantalla de dinero completa avisa con el criterio guardado
        # (hallazgo de revision: _con_avisos usaba 90 % en 5 por detras).
        from app.pantalla_dinero import lee_dinero

        pantalla = lee_dinero(conn, plataforma="amazon_mx", dias=30, hasta=HASTA_2A)
        assert pantalla.por_campana[0].avisos == (
            "Topada 80: se queda sin presupuesto. Usó 80 % o más en 3 de los últimos 7 días.",
        )

        fila = FilaCampana(
            campana_id=c1,
            nombre="Topada 80",
            presupuesto_diario=Decimal("100"),
            gasto_medio_diario=Decimal("36.43"),
            uso_presupuesto_pct=Decimal("36.43"),
            estrategia="fija",
            ajustes_ubicacion=(),
            gasto_fuera_de_amazon=None,
            dias_al_tope_7d=3,
        )
        avisos = avisos_del_dia(
            (),
            (fila,),
            plataforma="amazon_mx",
            gasto_para_concluir=Decimal("350"),
            topada=topada,
        )
        assert [a.clase for a in avisos] == ["campana_sin_presupuesto"]
        assert avisos[0].frase == (
            "Topada 80: se queda sin presupuesto. Usó 80 % o más en 3 de los últimos 7 días."
        )


@_skip_db
def test_temporada01_a_estado_de_baja_con_mantener_pausada(tmp_path, monkeypatch):
    """El bloque de estado de la baja (bloqueo de Kimi 4): conteos desde
    decisiones reales de temporada, keywords pausadas a reactivar y el
    boton «Mantener pausada» que las guarda en mantener_pausadas."""
    from test_pantalla_dinero import _siembra_entidad

    hoy = dt.datetime.now(dt.UTC)
    ack = Json({"request_id": "t"})
    meses_baja_hoy = ["normal"] * 10 + ["baja", "normal"]  # noviembre en baja
    blob = temporada.como_guardada(temporada.valida(_crudo(meses=meses_baja_hoy)))

    with _db_con_rol_admin("orbit_t01a_estado") as (conn, dsn_admin, dsn_lectura):
        config_id = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            (
                "estado",
                Json(
                    {
                        "ads_optimizer_mode": "shadow",
                        "ads_temporada": blob,
                        "ads_temporada_amazon_mx": "live",
                    }
                ),
            ),
        ).fetchone()[0]

        def _ciclo(mode):
            return conn.execute(
                "INSERT INTO optimizer_cycle (motor, mode, platform, status,"
                " finished_at, decisions_count) VALUES"
                " ('ads_optimizer', %s, 'amazon_mx'::platform, 'done', %s, 0) RETURNING id",
                (mode, hoy),
            ).fetchone()[0]

        ciclo = _ciclo("live")
        ciclo_2 = _ciclo("live")
        ciclo_sombra = _ciclo("shadow")
        campana = _siembra_entidad(conn, "amazon_mx", "campaign", "c-estado")
        grupo = _siembra_entidad(conn, "amazon_mx", "ad_group", "g-estado", parent=campana)
        kw_bid = _siembra_entidad(
            conn,
            "amazon_mx",
            "keyword",
            "k-estado-1",
            parent=grupo,
            match="EXACT",
            texto="bid propio",
        )
        kw_grupo = _siembra_entidad(
            conn,
            "amazon_mx",
            "keyword",
            "k-estado-2",
            parent=grupo,
            match="EXACT",
            texto="bid heredado",
        )
        kw_pausada = _siembra_entidad(
            conn,
            "amazon_mx",
            "keyword",
            "k-estado-3",
            parent=grupo,
            match="EXACT",
            texto="pausada en la baja",
        )
        kw_sin = _siembra_entidad(
            conn,
            "amazon_mx",
            "keyword",
            "k-estado-4",
            parent=grupo,
            match="EXACT",
            texto="sin decision",
        )
        kw_david = _siembra_entidad(
            conn,
            "amazon_mx",
            "keyword",
            "k-estado-5",
            parent=grupo,
            match="EXACT",
            texto="repausada por David",
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, current_bid, bid_currency,"
            " synced_at) VALUES (%s, 'ENABLED', 5.00, 'MXN', now()),"
            " (%s, 'ENABLED', NULL, NULL, now()), (%s, 'PAUSED', NULL, NULL, now()),"
            " (%s, 'ENABLED', NULL, NULL, now()), (%s, 'PAUSED', NULL, NULL, now())",
            (kw_bid, kw_grupo, kw_pausada, kw_sin, kw_david),
        )

        def _decision(ciclo_d, kind, entidad, motivo, *, fase_baja=False, confirmada=False):
            inputs = {"motor": "ads_optimizer", "motivo": motivo}
            if fase_baja:
                inputs["caso"] = {"temporada": {"frontera": {"fase_hoja": "baja"}}}
            # Regla 6 (trigger decision_madurez_corte): una pausa exige
            # ventana cerrada al menos 10 dias antes de la decision.
            fin_ventana = hoy - dt.timedelta(days=11 if kind == "pause" else 1)
            # Las pausas no llevan dinero (CHECK decision_moneda_solo_en_
            # kinds_con_dinero): solo los bids viajan con old/new + moneda.
            dinero = (
                (Decimal("8.00"), Decimal("5.60"), "MXN") if kind == "bid" else (None, None, None)
            )
            id_d = conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
                " config_version_id, data_observed_at, window_start, window_end,"
                " old_value, new_value, value_currency, inputs)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    ciclo_d,
                    entidad,
                    kind,
                    hoy - dt.timedelta(days=1),
                    config_id,
                    fin_ventana - dt.timedelta(days=1),
                    fin_ventana - dt.timedelta(days=19),
                    fin_ventana,
                    dinero[0],
                    dinero[1],
                    dinero[2],
                    Json(inputs),
                ),
            ).fetchone()[0]
            if confirmada:
                conn.execute(
                    "INSERT INTO decision_application (decision_id, confirmed_at, verify_ok,"
                    " platform_ack) VALUES (%s, %s, TRUE, %s)",
                    (id_d, hoy, ack),
                )
            return id_d

        _decision(ciclo, "bid", kw_bid, "entra_temporada_baja", confirmada=True)
        # Decisiones repetidas de la MISMA keyword en otro ciclo: cuentan
        # una sola vez (hallazgo de la revision: confirmados resta unidades
        # distintas).
        _decision(ciclo_2, "bid", kw_bid, "ajusta_temporada_baja", confirmada=True)
        _decision(ciclo, "bid", kw_grupo, "entra_temporada_baja")
        # Una decision de practica (ciclo shadow) jamas se confirma y no
        # cuenta como pendiente de Amazon (hallazgo de la revision).
        _decision(ciclo_sombra, "bid", kw_grupo, "entra_temporada_baja")
        d_pausa = _decision(ciclo, "pause", kw_pausada, "pausa", fase_baja=True, confirmada=True)
        # La pausa llega a Amazon via cola live y se aplica (nace
        # pending_veto por trigger; el UPDATE simula la aplicacion): ese es
        # el ancla de vigencia de la lista (hallazgo de la revision).
        fila_cola = conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id,"
            " modo, estado, vence_el, request_payload) VALUES ('amazon_mx'::platform,"
            " %s, 'pause', %s, 'live', 'pending_veto', %s, %s) RETURNING id",
            (kw_pausada, d_pausa, hoy, Json({})),
        ).fetchone()[0]
        # Maquina de estados: pending_veto -> released -> applying -> applied.
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = %s WHERE id = %s",
            (hoy, fila_cola),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = %s WHERE id = %s",
            (hoy, fila_cola),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applied', applied_at = %s WHERE id = %s",
            (hoy, fila_cola),
        )
        # Contraejemplo de vigencia (hallazgo de la revision): Orbit pauso y
        # confirmo, David la reactivo (detectada como manual despues de la
        # aplicacion) y la volvio a pausar a mano en Amazon. La pausa vieja
        # ya es del dueno: NO vuelve a atribuirse a Orbit.
        d_david = _decision(ciclo, "pause", kw_david, "pausa", fase_baja=True, confirmada=True)
        fila_david = conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id,"
            " modo, estado, vence_el, request_payload) VALUES ('amazon_mx'::platform,"
            " %s, 'pause', %s, 'live', 'pending_veto', %s, %s) RETURNING id",
            (kw_david, d_david, hoy, Json({})),
        ).fetchone()[0]
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=2), fila_david),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=2), fila_david),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applied', applied_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=1), fila_david),
        )
        conn.execute(
            "INSERT INTO reactivacion_manual (ad_entity_id, detectada_en) VALUES (%s, %s)",
            (kw_david, hoy),
        )
        # Contraejemplo del ancla (hallazgo de la revision): DESPUES de la
        # pausa de temporada, la keyword tiene otra pausa aplicada SIN marca
        # de temporada; la ultima aplicada de la keyword manda, igual que
        # _SQL_PAUSAS_DE_BAJA del diseno.
        kw_tardia = _siembra_entidad(
            conn,
            "amazon_mx",
            "keyword",
            "k-estado-6",
            parent=grupo,
            match="EXACT",
            texto="pausa posterior sin marca",
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'PAUSED', now())",
            (kw_tardia,),
        )
        d_tardia = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
            " config_version_id, data_observed_at, window_start, window_end,"
            " value_currency, inputs)"
            " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, NULL, %s)"
            " RETURNING id",
            (
                ciclo_2,
                kw_tardia,
                hoy,
                config_id,
                hoy - dt.timedelta(days=2),
                hoy - dt.timedelta(days=30),
                hoy - dt.timedelta(days=11),
                Json({"motor": "ads_optimizer", "motivo": "pausa"}),
            ),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO decision_application (decision_id, confirmed_at, verify_ok,"
            " platform_ack) VALUES (%s, %s, TRUE, %s)",
            (d_tardia, hoy, ack),
        )
        fila_tardia = conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id,"
            " modo, estado, vence_el, request_payload) VALUES ('amazon_mx'::platform,"
            " %s, 'pause', %s, 'live', 'pending_veto', %s, %s) RETURNING id",
            (kw_tardia, d_tardia, hoy, Json({})),
        ).fetchone()[0]
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = %s WHERE id = %s",
            (hoy - dt.timedelta(minutes=50), fila_tardia),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = %s WHERE id = %s",
            (hoy - dt.timedelta(minutes=50), fila_tardia),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applied', applied_at = %s WHERE id = %s",
            (hoy - dt.timedelta(minutes=50), fila_tardia),
        )
        # Y ANTES de esa sin marca, la pausa de temporada con su cola aplicada
        # (es la que la consulta vieja sacaba de la lista).
        d_t1 = _decision(ciclo, "pause", kw_tardia, "pausa", fase_baja=True, confirmada=True)
        fila_t1 = conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id,"
            " modo, estado, vence_el, request_payload) VALUES ('amazon_mx'::platform,"
            " %s, 'pause', %s, 'live', 'pending_veto', %s, %s) RETURNING id",
            (kw_tardia, d_t1, hoy, Json({})),
        ).fetchone()[0]
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=3), fila_t1),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=3), fila_t1),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applied', applied_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=3), fila_t1),
        )
        # Contraejemplo de agotamiento (hallazgo de la revision): tres
        # reversas fallidas descartan el camino de reactivacion, igual que
        # en el diseno (bool_or ok OR count >= 3).
        kw_agotada = _siembra_entidad(
            conn,
            "amazon_mx",
            "keyword",
            "k-estado-7",
            parent=grupo,
            match="EXACT",
            texto="reversas agotadas",
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'PAUSED', now())",
            (kw_agotada,),
        )
        d_agotada = _decision(ciclo, "pause", kw_agotada, "pausa", fase_baja=True, confirmada=True)
        fila_agotada = conn.execute(
            "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id,"
            " modo, estado, vence_el, request_payload) VALUES ('amazon_mx'::platform,"
            " %s, 'pause', %s, 'live', 'pending_veto', %s, %s) RETURNING id",
            (kw_agotada, d_agotada, hoy, Json({})),
        ).fetchone()[0]
        conn.execute(
            "UPDATE apply_queue SET estado = 'released', released_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=2), fila_agotada),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applying', applying_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=2), fila_agotada),
        )
        conn.execute(
            "UPDATE apply_queue SET estado = 'applied', applied_at = %s WHERE id = %s",
            (hoy - dt.timedelta(hours=2), fila_agotada),
        )
        for _i in range(1, 4):
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada, resultado, finished_at)"
                " VALUES (%s, %s, 'reversa', '{}'::jsonb, false, 'fallo', now())",
                (d_agotada, _i),
            )
        # Lo mas reciente manda: la keyword con entrada confirmada pero un
        # ajuste todavia pendiente aparece en PENDIENTES (hallazgo de la
        # revision: bool_or miraba cualquier confirmacion historica).
        ciclo_3 = _ciclo("live")
        _decision(ciclo_3, "bid", kw_bid, "ajusta_temporada_baja")
        # La keyword pausada tambien recibio su entrada confirmada (hallazgo
        # de la revision: cuenta en confirmados aunque hoy este PAUSED, sin
        # restarse a otra keyword en sin_tocar).
        _decision(ciclo_2, "bid", kw_pausada, "entra_temporada_baja", confirmada=True)

        monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        cliente = TestClient(app)

        def _estado_mx():
            datos = cliente.get("/api/dashboard/settings").json()
            return {p["plataforma"]: p["temporada_estado"] for p in datos["plataformas"]}

        estados = _estado_mx()
        # US no tiene modo: sin estado operativo ni acciones (apagada).
        assert estados["amazon_us"] is None
        estado = estados["amazon_mx"]
        assert estado["fase"] == "baja"
        # kw_bid entra confirmada y ajusta pendiente: su ULTIMA decision es
        # la pendiente, asi que cuenta en pendientes (no en confirmados).
        # kw_pausada recibio su entrada confirmada y hoy esta PAUSED: cuenta
        # en confirmados sin restarse a otra keyword en sin_tocar.
        assert estado["confirmados"] == 1
        assert estado["pendientes"] == 2
        # sin_tocar se cuenta DIRECTO sobre las keywords habilitadas sin
        # decision de temporada: solo kw_sin (resta de conjuntos homogeneos),
        # y la razon de grupo tambien: kw_sin es la unica sin tocar y usa el
        # bid de su grupo (kw_grupo ya tiene decision y no entra en la
        # explicacion).
        assert estado["sin_tocar"] == 1
        assert estado["frase_sin_tocar"] == (
            "Sin tocar: 1. 1 usan el bid de su grupo"
            " (Orbit no los toca hasta que tengan bid propio)."
        )
        assert [p["ad_entity_id"] for p in estado["pausadas"]] == [kw_pausada]
        # Las dos contraejemplos NO aparecen: la pausa tardia sin marca de
        # temporada es la ultima aplicada de su keyword (el ancla de
        # aplicacion manda) y las reversas agotadas descartan la suya.
        assert kw_tardia not in [p["ad_entity_id"] for p in estado["pausadas"]]
        assert kw_agotada not in [p["ad_entity_id"] for p in estado["pausadas"]]
        # El texto visible de la lista es el TEXTO de la keyword.
        assert estado["pausadas"][0]["nombre"] == "pausada en la baja"

        resp = cliente.post(
            "/api/ads-optimizer/temporada/mantener-pausada",
            json={"ad_entity_id": kw_pausada, "base_config_version_id": config_id},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["mantener_pausadas"] == [kw_pausada]
        id_mantener = resp.json()["config_version_id"]
        blob_guardado = conn.execute(
            "SELECT settings->'ads_temporada'->'mantener_pausadas' FROM config_version"
            " WHERE id = %s",
            (id_mantener,),
        ).fetchone()[0]
        assert blob_guardado == [kw_pausada]

        # La lista del estado ya no la ofrece (esta en Mantener pausada) y
        # repetir el boton contesta 409 para que David recargue.
        assert _estado_mx()["amazon_mx"]["pausadas"] == []
        resp = cliente.post(
            "/api/ads-optimizer/temporada/mantener-pausada",
            json={"ad_entity_id": kw_pausada, "base_config_version_id": id_mantener},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 409


@_skip_db
def test_terminar_baja_serializa_con_advisory_lock_config_version(tmp_path, monkeypatch):
    """F3/F6 (Ola F1): `terminar_baja_temporada` toma el advisory lock de
    `config_version` ANTES de leer la config vigente. Si otra transaccion
    sostiene el candado y termina la baja antes de commitear, el endpoint lee
    el estado ya actualizado (fuera de baja -> 422) en lugar de leer el viejo
    y llegar hasta guarda_config (409)."""
    import threading
    import time

    import psycopg

    hoy = dt.datetime.now(dt.UTC).date()
    meses_baja = ["normal"] * 12
    meses_baja[hoy.month - 1] = "baja"
    meses_normal = ["normal"] * 12
    with _db_con_rol_admin("orbit_temp_lock") as (conn, dsn_admin, _dsn_lectura):
        config_id = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('base', %s) RETURNING id",
            (
                Json(
                    {
                        "ads_temporada": _crudo(meses=meses_baja),
                        "ads_temporada_modo_amazon_mx": "live",
                    }
                ),
            ),
        ).fetchone()[0]
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        cliente = TestClient(app)

        resultado: dict = {}

        def _corre_endpoint():
            resp = cliente.post(
                "/api/ads-optimizer/temporada/terminar-baja",
                json={"base_config_version_id": config_id},
                headers={"x-orbit-token": TOKEN},
            )
            resultado["status"] = resp.status_code

        with psycopg.connect(dsn_admin, autocommit=False) as bloqueadora:
            bloqueadora.execute("SELECT pg_advisory_xact_lock(hashtext('config_version'))")
            hilo = threading.Thread(target=_corre_endpoint, daemon=True)
            hilo.start()
            # Mientras la transaccion bloqueadora sostiene el candado, el endpoint
            # espera en lugar de leer config_version antes del lock.
            time.sleep(0.15)
            assert hilo.is_alive(), "terminar-baja debio esperar el advisory lock"
            bloqueadora.execute(
                "INSERT INTO config_version (label, settings) VALUES ('fin_baja', %s)",
                (
                    Json(
                        {
                            "ads_temporada": _crudo(meses=meses_normal),
                            "ads_temporada_modo_amazon_mx": "live",
                        }
                    ),
                ),
            )
            bloqueadora.commit()

        hilo.join(timeout=5)
        assert not hilo.is_alive()
        # Con el lock previo a la lectura lee 'fin_baja' (fuera de baja -> 422);
        # sin ese lock habria leido 'base' y chocado en guarda_config con 409.
        assert resultado.get("status") == 422


def test_eventos_no_emite_prepara_alta_durante_bloque_alta_anterior():
    """Hallazgo #14: si dos bloques alta se separan por un hueco menor a
    dias_preparacion (febrero normal de 28 dias con prep=30), prepara_alta
    nunca cae en un dia con fase=='alta' y se emite el primer dia real con
    previa_alta=True."""
    meses = ["alta", "normal", "alta"] + ["normal"] * 9
    cfg = temporada.valida(_crudo(meses=meses, prep=30))
    evs = temporada.eventos(cfg, dt.date(2027, 1, 1), dt.date(2027, 12, 31))
    preps = [ev for ev in evs if ev.tipo == "prepara_alta"]

    assert preps
    for ev in preps:
        est = temporada.resuelve(cfg, ev.fecha)
        assert est.fase != "alta", f"prepara_alta cayo dentro de alta en {ev.fecha}"
        assert est.previa_alta is True
    # 2027-01-30 y 2027-01-31 siguen en enero alta; el primer dia real con
    # previa_alta=True para marzo alta es 2027-02-01.
    assert [ev.fecha for ev in preps] == [dt.date(2027, 2, 1), dt.date(2027, 12, 2)]


def test_identificadores_rechaza_id_fuera_de_bigint():
    """Hallazgo #15: mantener_pausadas exige 1 <= id <= 2**63 - 1 para no
    reventar consultas SQL con NumericValueOutOfRange."""
    bigint_max = 2**63 - 1
    cfg = temporada.valida(_crudo(pausadas=[1, bigint_max]))
    assert cfg.mantener_pausadas == frozenset({1, bigint_max})

    for fuera in (0, -1, 2**63, 10**30):
        with pytest.raises(ValueError):
            temporada.valida(_crudo(pausadas=[fuera]))
