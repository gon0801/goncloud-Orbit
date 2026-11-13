"""Reproducciones de la revision de precios (solo lectura del codigo).

Cada prueba FALLA por la asercion que describe la falla. Reutiliza los
bancos de `tests/test_precio_corrida.py` (base temporal, red falsa).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_precio_corrida as banco  # noqa: E402
from test_precio_corrida import (  # noqa: E402
    _andamio_hoy,
    _cadena,
    _corre,
    _db,
    _price_obs,
    _RedFalsa,
    _skip_sin_pg,
)


def _cambio_error_con_readback_viejo(conn, dec, lid, *, antes, despues, dia):
    """Cambio real `error` (la red se corto tras el PATCH) con relectura
    inmediata = precio viejo, que es lo que Amazon devuelve SIEMPRE en
    produccion (37 de 37 relecturas = precio_antes)."""
    conn.execute("ALTER TABLE precio_cambio DISABLE TRIGGER precio_cambio_coherente")
    try:
        cid = conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at)"
            " VALUES (%s, %s, 'amazon_mx', %s, 'MXN', %s, 'MXN', true, 'pendiente', %s)"
            " RETURNING id",
            (dec, lid, Decimal(antes), Decimal(despues), dia),
        ).fetchone()[0]
        conn.execute(
            "UPDATE precio_cambio SET estado = 'error',"
            " error_code = 'PATCH /listings/2021-08-01/items/X red' WHERE id = %s",
            (cid,),
        )
        conn.execute(
            "UPDATE precio_cambio SET readback_precio = %s, readback_precio_currency = 'MXN',"
            " readback_estado = 'ok', readback_at = %s WHERE id = %s",
            (Decimal(antes), dia, cid),
        )
    finally:
        conn.execute("ALTER TABLE precio_cambio ENABLE TRIGGER precio_cambio_coherente")
    return cid


@_skip_sin_pg
def test_f1_patch_que_si_entro_no_frena_7_dias_y_se_vuelve_a_mover():
    """Hace 2 dias el PATCH 105.46 -> 116.00 se corto por red DESPUES de que
    Amazon lo recibio. La relectura inmediata vio el precio viejo (Amazon
    tarda), asi que el cambio quedo `error` + `sin_efecto`. Hoy la
    observacion de Amazon dice 116.00 (el cambio SI entro). Debe respetar
    los 7 dias entre cambios; en cambio sube otra vez y manda otro PATCH."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn, precio="116")  # hoy Amazon muestra el precio nuevo
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-REV-F1", ext="B0A5REVF01")
        _cambio_error_con_readback_viejo(
            conn,
            dec_b,
            datos["listing"],
            antes="105.46",
            despues="116.00",
            dia=datetime.now(UTC) - timedelta(days=2),
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        _corre(conn, red)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert (fila, red.n_patch) == (("mantener", "cooldown"), 0), (
            f"el precio ya se movio hace 2 dias y el motor decidio {fila} con {red.n_patch} PATCH"
        )


@_skip_sin_pg
def test_f1b_cambio_que_si_entro_queda_como_error_y_la_reversa_lo_salta():
    """Mismo caso: el precio vivo es el nuevo (116.00), pero el registro
    sigue en `error` para siempre (el cierre por observacion solo mira
    `enviado`) y el plan de reversa lo marca `sin_efecto`: el dueno no lo
    veria como revertible aunque el precio si cambio."""
    from app.precio.cambios import SeleccionPorCambios, cerrar_por_observacion, planear_reversa
    from app.spapi.precio_mercado import MercadoAmazon

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-REV-F1B", ext="B0A5REVF1B")
        ayer = datetime.now(UTC) - timedelta(days=2)
        cid = _cambio_error_con_readback_viejo(
            conn, dec_b, datos["listing"], antes="105.46", despues="116.00", dia=ayer
        )
        _price_obs(conn, precio="116")
        vitrina = MercadoAmazon("amazon_mx")
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        cerrar_por_observacion(conn, vitrina, hoy)
        estado = conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()
        plan = planear_reversa(conn, vitrina, SeleccionPorCambios(ids=(cid,)))
        assert (estado[0], plan.filas[0].estado_plan) == ("confirmado", "revertible"), (
            f"Amazon muestra 116.00 (= precio_despues) y el registro dice {estado[0]}"
            f" / plan {plan.filas[0].estado_plan}"
        )


@_skip_sin_pg
def test_f2_corrida_que_se_cae_no_avisa_y_tumba_a_todas(monkeypatch):
    """Un error de base de UNA publicacion (CheckViolation de un trigger,
    como el del 22 al 25-oct en MeLi) tumba la corrida entera: ninguna otra
    publicacion queda decidida, la fila de corrida queda `abierta` y el
    gancho de aviso (Telegram) nunca se llama. Cada repaso repite lo mismo
    (la publicacion rota se vuelve a decidir), asi que el dia entero queda
    sin precios para TODA la plataforma."""
    import app.precio.corrida as corrida_mod

    avisos: list = []
    original = corrida_mod.persistir_decision

    with _db() as (conn, _dsn):
        # La rota tiene el listing_id menor: se decide primero (orden de la corrida).
        rota = _cadena(conn, sku="SKU-REV-ROTA", ext="B0A5REVROT", mode="live")
        sana = _cadena(conn, sku="SKU-REV-SANA", ext="B0A5REVSAN", mode="live")
        _price_obs(conn, asin="B0A5REVSAN")
        _price_obs(conn, asin="B0A5REVROT")

        def persistir(conn_, decision, *, listing_id, **kw):
            if listing_id == rota["listing"]:
                raise psycopg.errors.CheckViolation("precio_decision: dato de UNA publicacion")
            return original(conn_, decision, listing_id=listing_id, **kw)

        monkeypatch.setattr(corrida_mod, "persistir_decision", persistir)
        red = _RedFalsa(skus={sana["evento"]: sana["sku"], rota["evento"]: rota["sku"]})
        try:
            _corre(conn, red, avisar=lambda *a: avisos.append(a))
        except psycopg.Error:
            pass
        decididas = conn.execute(
            "SELECT count(*) FROM precio_decision WHERE listing_id = %s", (sana["listing"],)
        ).fetchone()[0]
        estado = conn.execute("SELECT estado FROM precio_corrida").fetchone()[0]
        assert (decididas, red.n_patch, estado, len(avisos)) == (1, 1, "cerrada", 1), (
            f"publicacion sana decidida={decididas} patch={red.n_patch}, corrida={estado},"
            f" avisos={len(avisos)}"
        )


def _motor():
    from app.precio.config import leer_config_motor

    return leer_config_motor(banco.SETTINGS)


def test_f3_fusible_ciego_al_pasar_de_sombra_a_live():
    """20 de 20 publicaciones de un universo pasan de sombra a live el mismo
    dia. En sombra cada una ya 'subio' (cambio virtual, el precio real NO se
    movio). El dia del encendido las 20 suben de verdad a la vez, pero el
    fusible las cuenta como 'segundo escalon' (no nuevas) y no retiene."""
    from app.precio.compuerta import Anterior, Intencion, Retenida, es_nueva, evaluar

    goal_id = 7
    intenciones = [
        Intencion(
            decision_id=i,
            listing_id=i,
            canal="fba",
            # la decision previa (de ayer, en SOMBRA) fue subir con el mismo goal
            nueva=es_nueva(Anterior("subir", None, goal_id), goal_id=goal_id),
            liberada=False,
        )
        for i in range(20)
    ]
    veredicto = evaluar(intenciones, medidas=20, derivas=[], config=_motor())
    assert isinstance(veredicto, Retenida), (
        f"20 de 20 precios reales se mueven el primer dia en live y el fusible dice {veredicto}"
    )


def test_f4_mas_de_50_precios_en_un_dia_sin_confirmacion():
    """CONTEXTO.md: 'cambios masivos de precio (>50 productos) ... requieren
    confirmacion'. Con las medidas reales de produccion (MX FBA 125 y MX FBM
    59 publicaciones medidas), 62 + 29 = 91 precios nuevos se mueven el mismo
    dia sin que el fusible retenga nada (y `precio_cap_*` = 1,000,000 ni
    siquiera se lee)."""
    from app.precio.compuerta import Intencion, Retenida, evaluar

    def universo(canal, medidas, nuevas):
        intenciones = [
            Intencion(decision_id=i, listing_id=i, canal=canal, nueva=True, liberada=False)
            for i in range(nuevas)
        ]
        return evaluar(intenciones, medidas=medidas, derivas=[], config=_motor())

    fba = universo("fba", 125, 62)
    fbm = universo("fbm", 59, 29)
    retenidos = [v for v in (fba, fbm) if isinstance(v, Retenida)]
    assert retenidos, f"91 precios de amazon_mx se mueven hoy sin confirmacion: {fba} / {fbm}"
