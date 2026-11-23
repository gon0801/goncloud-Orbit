"""TEMPORADA 01, bloque G: propuestas de presupuesto que aprueba el dueno.

`test_temporada01_g_propuestas_absolutas` (dinero) cubre los momentos del
diseno: baja 300 -> 210, fin de la baja 210 -> 300, alta 300 -> 375 una sola
vez, 250 a mano que no produce fin de baja y el tope humano de X.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from test_aplica_ajuste import _skip_db
from test_temporada01_b import MESES_PRUEBA

from app.optimizer import temporada
from app.optimizer.temporada_presupuesto import propuesta_presupuesto

CONFIG = {
    "ads_temporada": {
        "version": temporada.VERSION_TEMPORADA,
        "meses": list(MESES_PRUEBA),
        "reduccion_baja_pct": "30",
        "dias_preparacion": 21,
        "mult_cortes_baja": "2",
        "aumento_presupuesto_alta_pct": "25",
        "fin_anticipado": None,
        "mantener_pausadas": [],
    }
}
TOPADA = temporada.CampanaTopada(pct=Decimal("0.90"), dias=5)
TECHO_MXN = Decimal("10708")
CUENTA = dict(
    estacion=None,
    config=None,
    episodio=None,
    historial=(),
    hoy=dt.date(2027, 1, 10),
    inicio_bloque_alto=None,
    dias_topados_7d=None,
    topada=TOPADA,
    techo_humano=TECHO_MXN,
    moneda="MXN",
)


def _config():
    return temporada.desde_settings(CONFIG)


def _estacion(dia: dt.date):
    return temporada.resuelve(_config(), dia)


def _episodio(*, abierto: dt.date, cerrado: dt.date | None, id_: int = 1):
    return SimpleNamespace(id=id_, abierto_el=abierto, cerrado_el=cerrado)


def _propuesta(**ajas):
    datos = {
        **CUENTA,
        "config": _config(),
        "estacion": _estacion(ajas.get("hoy", CUENTA["hoy"])),
    }
    datos.update(ajas)
    datos["estacion"] = _estacion(datos["hoy"])
    return propuesta_presupuesto(**datos)


def test_temporada01_g_propuestas_absolutas():
    """Baja, fin, alta, mano del dueno y tope humano con montos absolutos."""
    baja = _propuesta(
        hoy=dt.date(2027, 1, 10),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=None),
        historial=((dt.date(2026, 12, 1), Decimal("300")),),
    )
    assert baja is not None
    assert (baja.actual, baja.propuesto, baja.motivo) == (
        Decimal("300"),
        Decimal("210.00"),
        "baja",
    )
    assert "bajar de 300.00 a 210.00 MXN al día" in baja.frase

    # Un cambio de presupuesto desde la apertura apaga la baja.
    assert (
        _propuesta(
            hoy=dt.date(2027, 1, 10),
            episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=None),
            historial=(
                (dt.date(2026, 12, 1), Decimal("300")),
                (dt.date(2027, 1, 5), Decimal("210")),
            ),
        )
        is None
    )

    fin = _propuesta(
        hoy=dt.date(2027, 2, 5),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
        historial=(
            (dt.date(2026, 12, 1), Decimal("300")),
            (dt.date(2027, 1, 5), Decimal("210")),
        ),
    )
    assert fin is not None
    assert (fin.propuesto, fin.motivo) == (Decimal("300"), "fin_de_baja")
    assert "subir de 210.00 a 300.00 MXN al día" in fin.frase

    # 250 a mano: no es la propuesta de baja, no hay fin de la baja.
    assert (
        _propuesta(
            hoy=dt.date(2027, 2, 5),
            episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
            historial=(
                (dt.date(2026, 12, 1), Decimal("300")),
                (dt.date(2027, 1, 5), Decimal("250")),
            ),
        )
        is None
    )

    alta = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=((dt.date(2027, 2, 5), Decimal("300")),),
        inicio_bloque_alto=dt.date(2027, 3, 1),
        dias_topados_7d=5,
    )
    assert alta is not None
    assert (alta.propuesto, alta.motivo) == (Decimal("375.00"), "alta_sin_presupuesto")
    assert "se queda sin presupuesto; subir de 300.00 a 375.00 MXN al día" in alta.frase

    # Ya hubo un aumento desde la preparacion: no se repite.
    assert (
        _propuesta(
            hoy=dt.date(2027, 3, 10),
            episodio=None,
            historial=(
                (dt.date(2027, 2, 5), Decimal("300")),
                (dt.date(2027, 3, 5), Decimal("375")),
            ),
            inicio_bloque_alto=dt.date(2027, 3, 1),
            dias_topados_7d=5,
        )
        is None
    )

    # Tope humano de X: 10,000 x 1.25 = 12,500 se queda en 10,708.
    tope = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=((dt.date(2027, 2, 5), Decimal("10000")),),
        inicio_bloque_alto=dt.date(2027, 3, 1),
        dias_topados_7d=5,
    )
    assert tope is not None
    assert tope.propuesto == TECHO_MXN
    assert "topado por el techo humano" in tope.frase

    # Dos cambios el MISMO dia: manda el ultimo aplicado (orden del SQL).
    mismo_dia = _propuesta(
        hoy=dt.date(2027, 1, 10),
        episodio=_episodio(abierto=dt.date(2027, 1, 6), cerrado=None),
        historial=(
            (dt.date(2026, 12, 1), Decimal("300")),
            (dt.date(2027, 1, 5), Decimal("250")),
            (dt.date(2027, 1, 5), Decimal("220")),
        ),
    )
    assert mismo_dia is not None and mismo_dia.actual == Decimal("220")

    # Una observacion sin presupuesto no rompe la revision de aumentos.
    sin_monto = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=(
            (dt.date(2027, 2, 5), Decimal("300")),
            (dt.date(2027, 2, 20), None),
            (dt.date(2027, 2, 25), Decimal("300")),
        ),
        inicio_bloque_alto=dt.date(2027, 3, 1),
        dias_topados_7d=5,
    )
    assert sin_monto is not None and sin_monto.propuesto == Decimal("375.00")

    # La frontera estable es la preparacion: un aumento aprobado alli no se
    # repite al entrar en alta.
    prep = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=(
            (dt.date(2027, 2, 1), Decimal("300")),
            (dt.date(2027, 2, 15), Decimal("375")),
        ),
        inicio_bloque_alto=dt.date(2027, 2, 8),
        dias_topados_7d=5,
    )
    assert prep is None


def test_temporada01_ola1_propuesta_ignora_observaciones_sin_cambio_y_frontera():
    """Ola 1B (regla 9): observaciones de placement/estrategia que repiten el
    mismo presupuesto no cuentan como cambio; una observacion inicial el mismo
    dia de apertura es la base (no un cambio posterior); en la primera alta sin
    episodio previo el piso es el presupuesto al inicio del bloque, no 0."""
    # 1) Cambio de placement durante baja (300 -> 300): sigue proponiendo baja.
    baja_placement = _propuesta(
        hoy=dt.date(2027, 1, 10),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=None),
        historial=(
            (dt.date(2026, 12, 1), Decimal("300")),
            (dt.date(2027, 1, 5), Decimal("300")),
        ),
    )
    assert baja_placement is not None
    assert (baja_placement.propuesto, baja_placement.motivo) == (Decimal("210.00"), "baja")

    # 2) Cambio de placement tras bajar a 210 (300 -> 210 -> 210): propone fin de baja.
    fin_placement = _propuesta(
        hoy=dt.date(2027, 2, 5),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
        historial=(
            (dt.date(2026, 12, 1), Decimal("300")),
            (dt.date(2027, 1, 5), Decimal("210")),
            (dt.date(2027, 1, 12), Decimal("210")),
        ),
    )
    assert fin_placement is not None
    assert (fin_placement.propuesto, fin_placement.motivo) == (Decimal("300"), "fin_de_baja")

    # 3) Primera observacion el MISMO dia de apertura del episodio: es la base.
    baja_mismo_dia = _propuesta(
        hoy=dt.date(2027, 1, 10),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=None),
        historial=((dt.date(2027, 1, 1), Decimal("300")),),
    )
    assert baja_mismo_dia is not None
    assert (baja_mismo_dia.propuesto, baja_mismo_dia.motivo) == (Decimal("210.00"), "baja")

    # 4) Primera alta sin episodio previo + cambio de placement (300 -> 300).
    alta_placement = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=(
            (dt.date(2027, 2, 5), Decimal("300")),
            (dt.date(2027, 3, 2), Decimal("300")),
        ),
        inicio_bloque_alto=dt.date(2027, 3, 1),
        dias_topados_7d=5,
    )
    assert alta_placement is not None
    assert (alta_placement.propuesto, alta_placement.motivo) == (
        Decimal("375.00"),
        "alta_sin_presupuesto",
    )

    # 5) Primera alta sin episodio previo + primera observacion el inicio del bloque.
    alta_inicio = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=((dt.date(2027, 3, 1), Decimal("300")),),
        inicio_bloque_alto=dt.date(2027, 3, 1),
        dias_topados_7d=5,
    )
    assert alta_inicio is not None
    assert (alta_inicio.propuesto, alta_inicio.motivo) == (
        Decimal("375.00"),
        "alta_sin_presupuesto",
    )

    # 6) Primera alta sin episodio previo + el dueno bajo el presupuesto (no subio).
    alta_bajada = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=None,
        historial=(
            (dt.date(2027, 2, 5), Decimal("300")),
            (dt.date(2027, 3, 2), Decimal("250")),
        ),
        inicio_bloque_alto=dt.date(2027, 3, 1),
        dias_topados_7d=5,
    )
    assert alta_bajada is not None
    assert (alta_bajada.propuesto, alta_bajada.motivo) == (
        Decimal("312.50"),
        "alta_sin_presupuesto",
    )


@_skip_db
def test_temporada01_g_presupuesto_misma_campana_en_orden(monkeypatch):
    """A aprueba 375 antes que B ordene 270; B termina primero. Al aplicar A,
    el candado por campana y campo ve la orden posterior aceptada: sin PUT,
    409 y el final es 270."""
    import json

    import httpx
    import pytest as _pytest
    from test_aplica_ajuste import (
        EXTERNA,
        _creds_fake,
        _db,
        _handler_campanas,
        _plan,
        _siembra,
    )

    from app import apply, apply_ajustes
    from app.ads.campana_config import config_vigente
    from app.campana_ajustes import CambiarPresupuesto

    with _db("orbit_t01_g_orden") as conn:
        camp, cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)

        # A quedo insertada (intencion) antes que B.
        plan_a = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("375")))
        a_id = conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, regresa_a)"
            " VALUES (%s, 'amazon_mx', 'presupuesto', %s, %s, %s, 'dueno',"
            " 'APLICAR AJUSTE', NULL) RETURNING id",
            (camp, cfg, json.dumps(plan_a.despues.como_json()), plan_a.huella()),
        ).fetchone()[0]
        conn.commit()

        # B ordena 270 DESPUES y se aplica primero.
        plan_b = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("270")))
        apply_ajustes.aplica_ajuste_campana(
            conn,
            plan_b,
            huella=plan_b.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        assert config_vigente(conn, camp).presupuesto_diario == Decimal("270")

        cliente = apply._cliente_ajuste("amazon_mx", transport=httpx.MockTransport(handler))
        with _pytest.raises(apply_ajustes.AjusteEnCurso, match="Ya mandaste otra orden"):
            apply_ajustes._aplica_y_confirma(
                conn,
                cliente,
                "amazon_mx",
                "presupuesto",
                plan_a.despues,
                plan_a.ajuste,
                campana_id=camp,
                ajuste_id=a_id,
                previa=plan_a.antes,
            )

        assert len([r for r in vistos if r.method == "PUT"]) == 1, "A no envia"
        assert config_vigente(conn, camp).presupuesto_diario == Decimal("270")
        assert (
            conn.execute(
                "SELECT confirmado_el FROM campana_ajuste WHERE id = %s", (a_id,)
            ).fetchone()[0]
            is None
        ), "A queda sin confirmar"


@_skip_db
def test_temporada01_g_reaprobacion_tras_regreso_no_hereda_orden_vieja(monkeypatch):
    """Tras un regreso a la configuracion inicial, la reaprobacion del mismo
    plan no reusa la orden pendiente superada: crea un acto nuevo y envia."""
    import datetime as dt
    import json

    import httpx
    from test_aplica_ajuste import (
        EXTERNA,
        _creds_fake,
        _db,
        _handler_campanas,
        _plan,
        _siembra,
        _vigente_config,
    )

    from app import apply_ajustes
    from app.ads.campana_config import config_vigente, guarda_config
    from app.campana_ajustes import CambiarPresupuesto

    with _db("orbit_t01_g_reapr") as conn:
        camp, cfg = _siembra(conn)
        remoto = {EXTERNA: {"budget": {"budget": 100.0, "budgetType": "DAILY"}}}
        handler, vistos = _handler_campanas(remoto)
        _creds_fake(monkeypatch)
        plan_a = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("375")))
        a_id = conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, regresa_a)"
            " VALUES (%s, 'amazon_mx', 'presupuesto', %s, %s, %s, 'dueno',"
            " 'APLICAR AJUSTE', NULL) RETURNING id",
            (camp, cfg, json.dumps(plan_a.despues.como_json()), plan_a.huella()),
        ).fetchone()[0]
        conn.commit()
        plan_b = _plan(conn, camp, CambiarPresupuesto(presupuesto_diario=Decimal("270")))
        apply_ajustes.aplica_ajuste_campana(
            conn,
            plan_b,
            huella=plan_b.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )
        # El dueno regresa a la configuracion inicial (100).
        guarda_config(conn, "amazon_mx", [_vigente_config()], dt.datetime.now(dt.UTC))
        conn.commit()
        assert config_vigente(conn, camp).presupuesto_diario == Decimal("100")

        hecho = apply_ajustes.aplica_ajuste_campana(
            conn,
            plan_a,
            huella=plan_a.huella(),
            confirmacion="APLICAR AJUSTE",
            actor="dueno",
            transport=httpx.MockTransport(handler),
        )

        assert hecho.confirmada is True
        assert hecho.ajuste_id != a_id, "no hereda la orden vieja superada"
        assert config_vigente(conn, camp).presupuesto_diario == Decimal("375")
        assert len([r for r in vistos if r.method == "PUT"]) == 2

        # Reenviar el POST viejo (huella base) no sella la orden superada:
        # devuelve el acto con sufijo que si confirmo el destino.
        rehecho = apply_ajustes.reconcilia_ajuste_campana(
            conn,
            camp,
            "presupuesto",
            plan_a.ajuste,
            huella=plan_a.huella(),
            actor="dueno",
        )
        assert rehecho.ajuste_id == hecho.ajuste_id
        assert (
            conn.execute(
                "SELECT confirmado_el FROM campana_ajuste WHERE id = %s", (a_id,)
            ).fetchone()[0]
            is None
        ), "la orden base superada no se sella"
        confirmadas = conn.execute(
            "SELECT count(*) FROM campana_ajuste WHERE huella LIKE %s"
            " AND confirmado_el IS NOT NULL",
            (plan_a.huella() + "#%",),
        ).fetchone()[0]
        assert confirmadas == 1, "un solo acto confirmado por el cambio"


@_skip_db
def test_temporada01_g_reversion_de_otra_ubicacion_no_supera():
    """Una reversion de resto no invalida una orden de top: la reversion
    guarda la config completa vieja, pero solo restaura SU ubicacion."""
    import datetime as dt
    import json
    from dataclasses import replace

    from test_aplica_ajuste import EXTERNA, _db, _siembra

    from app import apply_ajustes
    from app.ads.campana_config import ConfigCampana, guarda_config
    from app.campana_ajustes import CambiarAjusteUbicacion

    with _db("orbit_t01_g_rev") as conn:
        camp, _cfg = _siembra(conn)
        t0 = dt.datetime.now(dt.UTC)

        def _obs(config: ConfigCampana, segundos: int) -> int:
            guarda_config(conn, "amazon_mx", [config], t0 + dt.timedelta(seconds=segundos))
            return conn.execute(
                "SELECT id FROM ads_campana_config_observation WHERE ad_entity_id = %s"
                " ORDER BY id DESC LIMIT 1",
                (camp,),
            ).fetchone()[0]

        cfg0 = ConfigCampana(
            campana_externa=EXTERNA,
            presupuesto_diario=Decimal("100"),
            moneda="MXN",
            estrategia_puja="LEGACY_FOR_SALES",
            ajuste_top_pct=0,
            ajuste_resto_pct=0,
            ajuste_producto_pct=40,
            fuera_de_amazon=None,
        )
        obs1 = _obs(cfg0, 0)
        cfg2 = replace(cfg0, ajuste_resto_pct=20)
        obs2 = _obs(cfg2, 1)
        cfg3 = replace(cfg2, ajuste_top_pct=30)
        obs3 = _obs(cfg3, 2)

        def _fila(
            antes: int,
            despues: ConfigCampana,
            huella: str,
            *,
            pagada=None,
            confirmada=False,
        ) -> int:
            return conn.execute(
                "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
                " despues, huella, actor, go_literal, regresa_a, confirmado_el)"
                " VALUES (%s, 'amazon_mx', 'ajuste_ubicacion', %s, %s, %s, 'dueno',"
                " 'APLICAR AJUSTE', %s, %s) RETURNING id",
                (
                    camp,
                    antes,
                    json.dumps(despues.como_json()),
                    huella,
                    pagada,
                    t0 if confirmada else None,
                ),
            ).fetchone()[0]

        padre = _fila(obs1, cfg2, "rev-padre", confirmada=True)  # resto 0 -> 20
        orden = _fila(obs2, cfg3, "rev-orden")  # top 0 -> 30, pendiente
        _fila(obs3, cfg0, "rev-padre-reg", pagada=padre, confirmada=True)  # regresa resto

        assert (
            apply_ajustes._superada(
                conn,
                orden,
                campana_id=camp,
                clase="ajuste_ubicacion",
                ajuste=CambiarAjusteUbicacion(ubicacion="arriba_de_busqueda", porcentaje=30),
            )
            is False
        ), "la reversion de resto no toca el top"


@_skip_db
def test_temporada01_ola3_aplica_y_confirma_libera_lock_al_fallar():
    """Ola 3B: si _aplica_y_confirma falla despues de tomar el advisory lock,
    hace rollback y libera el candado de inmediato para otra sesion."""
    import psycopg
    import pytest
    from psycopg import pq
    from test_aplica_ajuste import EXTERNA, _db, _siembra

    from app import apply_ajustes
    from app.ads.campana_config import config_vigente
    from app.campana_ajustes import CambiarPresupuesto

    with _db("orbit_t01_ola3_lock") as conn:
        camp, obs_id = _siembra(conn)
        cfg0 = config_vigente(conn, camp)
        assert cfg0 is not None
        dsn = conn.info.dsn
        if conn.info.password:
            from psycopg.conninfo import make_conninfo

            dsn = make_conninfo(dsn, password=conn.info.password)
        conn_tx = psycopg.connect(dsn, autocommit=False)
        conn_otra = psycopg.connect(dsn, autocommit=True)
        try:
            ajuste = CambiarPresupuesto(presupuesto_diario=Decimal("150"))
            destino = cfg0
            orden = conn.execute(
                "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
                " despues, huella, actor, go_literal)"
                " VALUES (%s, 'amazon_mx', 'presupuesto', %s, '{}'::jsonb, 'h-lock-err',"
                " 'dueno', 'APLICAR AJUSTE') RETURNING id",
                (camp, obs_id),
            ).fetchone()[0]
            conn.commit()

            class _ClienteFalla:
                def ajustar_campana(self, *_a, **_k):
                    raise RuntimeError("error simulado en PUT")

            with pytest.raises(RuntimeError, match="error simulado"):
                apply_ajustes._aplica_y_confirma(
                    conn_tx,
                    _ClienteFalla(),
                    "amazon_mx",
                    "presupuesto",
                    destino,
                    ajuste,
                    campana_id=camp,
                    ajuste_id=orden,
                    previa=cfg0,
                )
            assert conn_tx.info.transaction_status == pq.TransactionStatus.IDLE
            llave = f"campana:{camp}:presupuesto_diario"
            libre = conn_otra.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext(%s))", (llave,)
            ).fetchone()[0]
            assert libre is True, f"el lock {llave} quedo retenido tras la excepcion"
            assert cfg0.campana_externa == EXTERNA
        finally:
            conn_tx.close()
            conn_otra.close()


def test_temporada01_propuesta_alta_no_repite_aumento_tras_bajada_manual():
    """Sin baja previa, bajar de 300 a 200 y aprobar +25 % (250) ya cuenta como
    aumento del bloque alto: no propone un segundo aumento (312.50)."""
    assert (
        _propuesta(
            hoy=dt.date(2027, 3, 15),
            episodio=None,
            historial=(
                (dt.date(2027, 2, 1), Decimal("300")),
                (dt.date(2027, 3, 5), Decimal("200")),
                (dt.date(2027, 3, 10), Decimal("250")),
            ),
            inicio_bloque_alto=dt.date(2027, 3, 1),
            dias_topados_7d=5,
        )
        is None
    )


def test_propuesta_presupuesto_no_repite_subida_bajo_antes_ni_suprime_recorte_en_previa():
    """Hallazgo #3: con episodio de baja cerrado, subir 140 -> 175 (< antes=200)
    ya cuenta como aumento del bloque alto (no propone 218.75), restaurar
    exactamente 140 -> 200 no bloquea el +25 % (propone 250.00), y bajar
    400 -> 350 en previa con antes=300 no suprime la propuesta (437.50)."""
    # 1) Baja cerrada 200 -> 140, luego aplico +25 % a 175 en previa: no repite.
    assert (
        _propuesta(
            hoy=dt.date(2027, 3, 10),
            episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
            historial=(
                (dt.date(2026, 12, 1), Decimal("200")),
                (dt.date(2027, 1, 5), Decimal("140")),
                (dt.date(2027, 2, 10), Decimal("175")),
            ),
            inicio_bloque_alto=dt.date(2027, 2, 8),
            dias_topados_7d=5,
        )
        is None
    )

    # 2) Baja cerrada 200 -> 140, restauracion exacta 140 -> 200 en previa: propone 250.00.
    restaurada = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
        historial=(
            (dt.date(2026, 12, 1), Decimal("200")),
            (dt.date(2027, 1, 5), Decimal("140")),
            (dt.date(2027, 2, 10), Decimal("200")),
        ),
        inicio_bloque_alto=dt.date(2027, 2, 8),
        dias_topados_7d=5,
    )
    assert restaurada is not None
    assert (restaurada.propuesto, restaurada.motivo) == (
        Decimal("250.00"),
        "alta_sin_presupuesto",
    )

    # 3) Baja cerrada antes=300, subio a 400 antes de previa y bajo a 350 en previa: propone 437.50.
    recortada = _propuesta(
        hoy=dt.date(2027, 3, 10),
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
        historial=(
            (dt.date(2026, 12, 1), Decimal("300")),
            (dt.date(2027, 1, 5), Decimal("210")),
            (dt.date(2027, 2, 2), Decimal("400")),
            (dt.date(2027, 2, 10), Decimal("350")),
        ),
        inicio_bloque_alto=dt.date(2027, 2, 8),
        dias_topados_7d=5,
    )
    assert recortada is not None
    assert (recortada.propuesto, recortada.motivo) == (
        Decimal("437.50"),
        "alta_sin_presupuesto",
    )


def test_propuesta_alta_conserva_propuesta_tras_cambiar_porcentaje_baja():
    """Review PR #738 (Devin): restaurar 210 -> 300 tras una baja de 30 % y luego
    editar reduccion_baja_pct a 20 % en config vigente no debe reclasificar la
    restauracion como subida de alta ni ocultar la propuesta de 375.00. En
    cambio, si la campana nunca bajo en la baja (300 -> 240 -> 300 en previa),
    la subida 240 -> 300 si cuenta como aumento aplicado."""
    cfg_20 = temporada.desde_settings(
        {
            "ads_temporada": {
                **CONFIG["ads_temporada"],
                "reduccion_baja_pct": "20",
            }
        }
    )
    assert cfg_20 is not None
    # Baja cerrada con 30 % (300 -> 210), restauracion 210 -> 300 en previa,
    # y luego el dueno edita la reduccion de baja a 20 %: sigue proponiendo 375.00.
    prop = propuesta_presupuesto(
        estacion=temporada.resuelve(cfg_20, dt.date(2027, 3, 10)),
        config=cfg_20,
        episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
        historial=(
            (dt.date(2026, 12, 1), Decimal("300")),
            (dt.date(2027, 1, 5), Decimal("210")),
            (dt.date(2027, 2, 10), Decimal("300")),
        ),
        hoy=dt.date(2027, 3, 10),
        inicio_bloque_alto=dt.date(2027, 2, 8),
        dias_topados_7d=5,
        topada=TOPADA,
        techo_humano=TECHO_MXN,
        moneda="MXN",
    )
    assert prop is not None
    assert (prop.propuesto, prop.motivo) == (Decimal("375.00"), "alta_sin_presupuesto")

    # Control: si nunca bajo durante el episodio (base_alto == antes == 300),
    # bajar 300 -> 240 y subir 240 -> 300 en previa SI bloquea otra subida.
    assert (
        propuesta_presupuesto(
            estacion=temporada.resuelve(cfg_20, dt.date(2027, 3, 10)),
            config=cfg_20,
            episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
            historial=(
                (dt.date(2026, 12, 1), Decimal("300")),
                (dt.date(2027, 2, 10), Decimal("240")),
                (dt.date(2027, 2, 15), Decimal("300")),
            ),
            hoy=dt.date(2027, 3, 10),
            inicio_bloque_alto=dt.date(2027, 2, 8),
            dias_topados_7d=5,
            topada=TOPADA,
            techo_humano=TECHO_MXN,
            moneda="MXN",
        )
        is None
    )

    # Control 2: tras restaurar 210 -> 300, bajar a 240 y subir de nuevo a 300
    # ya cuenta como subida real del bloque alto (no se trata como segunda restauracion).
    assert (
        propuesta_presupuesto(
            estacion=temporada.resuelve(cfg_20, dt.date(2027, 3, 10)),
            config=cfg_20,
            episodio=_episodio(abierto=dt.date(2027, 1, 1), cerrado=dt.date(2027, 2, 1)),
            historial=(
                (dt.date(2026, 12, 1), Decimal("300")),
                (dt.date(2027, 1, 5), Decimal("210")),
                (dt.date(2027, 2, 10), Decimal("300")),
                (dt.date(2027, 2, 12), Decimal("240")),
                (dt.date(2027, 2, 15), Decimal("300")),
            ),
            hoy=dt.date(2027, 3, 10),
            inicio_bloque_alto=dt.date(2027, 2, 8),
            dias_topados_7d=5,
            topada=TOPADA,
            techo_humano=TECHO_MXN,
            moneda="MXN",
        )
        is None
    )


def test_cuenta_dias_topados_omite_dia_sin_fila_y_presupuesto_cero():
    """Hallazgo #2 y Review Focus #1: cuenta_dias_topados no inventa Decimal(0)
    en dias sin fila ni cuenta presupuesto_diario == 0 como topado."""
    from app.optimizer.temporada_presupuesto import cuenta_dias_topados

    dias = tuple(dt.date(2027, 1, 1) + dt.timedelta(days=i) for i in range(7))
    hist_cero = ((dt.date(2026, 12, 1), Decimal("0")),)
    assert cuenta_dias_topados(dias, {}, hist_cero, TOPADA) == 0
    assert cuenta_dias_topados(dias, {dias[0]: Decimal("0")}, hist_cero, TOPADA) == 0

    hist_pos = ((dt.date(2026, 12, 1), Decimal("100")),)
    assert cuenta_dias_topados(dias, {}, hist_pos, TOPADA) == 0
    assert cuenta_dias_topados(dias, {dias[0]: Decimal("95")}, hist_pos, TOPADA) == 1
