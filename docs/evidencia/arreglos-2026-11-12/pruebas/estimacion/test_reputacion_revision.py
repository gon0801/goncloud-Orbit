"""Reproducciones de la revision de Reputacion (solo lectura del codigo).

Cada prueba FALLA por la asercion que describe la falla.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_reputacion import _skip_db, db_reputacion  # noqa: E402

from app.api_reputacion import carga_resumen  # noqa: E402
from app.reputacion_alertas import carga_reputacion_digest, evalua_y_persiste  # noqa: E402

HOY = dt.date(2026, 11, 12)
FETCH = dt.datetime(2026, 11, 12, 9, 30, tzinfo=dt.UTC)


def _seller(conn, metric_date, level_id):
    conn.execute(
        "INSERT INTO seller_reputation_snapshot"
        " (platform, metric_date, level_id, disputas_total, fetched_at, observed_at)"
        " VALUES ('meli', %s, %s, 0, %s, %s)",
        (
            metric_date,
            level_id,
            FETCH,
            dt.datetime.combine(metric_date, dt.time(9, 30), tzinfo=dt.UTC),
        ),
    )


def _pregunta(conn, item, qid, estado, observed_at):
    conn.execute(
        "INSERT INTO meli_question"
        " (platform, external_id, question_external_id, estado, texto,"
        "  asked_at, fetched_at, observed_at)"
        " VALUES ('meli', %s, %s, %s, 'hola', %s, %s, %s)",
        (item, qid, estado, observed_at - dt.timedelta(days=1), observed_at, observed_at),
    )


@_skip_db
def test_caida_de_nivel_de_cuenta_se_pierde_si_el_job_de_alertas_salta_un_dia():
    """La cuenta MeLi baja de verde a amarillo el dia D-1; el job de alertas
    de ese dia no corre (o la ingesta corrio dos veces). El dia D la cuenta
    SIGUE en amarillo y nunca se aviso: salud_cuenta compara solo las dos
    ultimas fotos (amarillo vs amarillo) y no abre nada."""
    with db_reputacion("orbit_revrep1") as (conn, _dsn):
        _seller(conn, HOY - dt.timedelta(days=2), "5_green")
        _seller(conn, HOY - dt.timedelta(days=1), "3_yellow")
        _seller(conn, HOY, "3_yellow")
        resultado = evalua_y_persiste(conn, HOY)
        tipos = {a["tipo"] for a in resultado["alertas"]}
        assert "salud_cuenta" in tipos, (
            "la cuenta cayo a 3_yellow y nunca hubo alerta: " f"{resultado}"
        )


@_skip_db
def test_pregunta_que_desaparece_de_meli_queda_pendiente_para_siempre():
    """Forma real en produccion: 3 preguntas UNANSWERED del item
    MLM2736115339 se vieron por ultima vez el 2026-10-27 (el item se sigue
    leyendo a diario). La pantalla las cuenta como pendientes 16 dias
    despues porque la ultima fila que existe dice UNANSWERED."""
    with db_reputacion("orbit_revrep2") as (conn, _dsn):
        dia1 = dt.datetime(2026, 10, 27, 9, 30, tzinfo=dt.UTC)
        hoy = dt.datetime(2026, 11, 12, 9, 30, tzinfo=dt.UTC)
        # Dia 1: MeLi devuelve dos preguntas sin responder.
        _pregunta(conn, "MLM1", "Q_SIGUE", "UNANSWERED", dia1)
        _pregunta(conn, "MLM1", "Q_BORRADA", "UNANSWERED", dia1)
        # Hoy: MeLi solo devuelve Q_SIGUE (Q_BORRADA ya no existe alla).
        _pregunta(conn, "MLM1", "Q_SIGUE", "UNANSWERED", hoy)
        resumen = carga_resumen(conn, hoy.date())
        ids = [p["question_external_id"] for p in resumen["preguntas_pendientes"]]
        assert ids == ["Q_SIGUE"], f"pendientes fantasma en pantalla: {ids}"


@_skip_db
def test_alerta_nunca_llega_a_telegram_si_el_ciclo_no_corre_en_24h():
    """La alerta de reputacion no tiene envio propio: solo viaja dentro del
    digest del ciclo de Amazon Ads y solo si se creo en las ultimas 24 h.
    Si ese ciclo no corre al dia siguiente (falla, deploy, flag apagada),
    la alerta abierta y nunca enviada ya no sale jamas."""
    with db_reputacion("orbit_revrep3") as (conn, _dsn):
        creada = dt.datetime.now(dt.UTC) - dt.timedelta(hours=46)
        conn.execute(
            "INSERT INTO reputation_alert"
            " (platform, external_id, tipo, severidad, mensaje, created_at)"
            " VALUES ('meli', 'MLM1', 'resena_1', 'critica',"
            " '1 estrella nueva: review 9 en MLM1', %s)",
            (creada,),
        )
        novedades = carga_reputacion_digest(conn=conn)
        assert novedades and novedades[0]["tipo"] == "resena_1", (
            f"alerta critica abierta y nunca enviada no sale: {novedades}"
        )


class _ClienteFalso:
    """Imita ClienteMeli.get: devuelve (dict, fetched_at) por ruta."""

    def __init__(self, respuestas: dict) -> None:
        self._r = respuestas

    def get(self, path, params=None):
        return self._r[path], FETCH


def test_respuesta_de_disputas_sin_la_clave_data_se_guarda_como_cero_disputas():
    """Regla 3: si MeLi responde 200 sin la lista `data` (cambio de forma,
    respuesta parcial), Orbit escribe disputas_total=0 como si fuera un dato.
    Con el historico real (24 disputas acumuladas) ese 0 rompe la resta de
    reclamos_suben: o la apaga en silencio o la dispara en falso al volver."""
    from app.reputacion import fetch_seller

    cliente = _ClienteFalso(
        {
            "/users/me": {"id": 1},
            "/users/1": {"seller_reputation": {"level_id": "5_green", "transactions": {}}},
            "/post-purchase/v1/claims/search": {"paging": {"total": 24}},
        }
    )
    plan, _skips = fetch_seller(cliente)
    assert plan.disputas_total is None, (
        f"sin lista de disputas se inventa un conteo: disputas_total={plan.disputas_total}"
    )
