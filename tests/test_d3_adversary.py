"""Regresiones del encendido D.3 con clientes y base simulados."""

from __future__ import annotations

import datetime as dt
from contextlib import ExitStack, nullcontext, suppress
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from app import apply, apply_cola, apply_harvest, apply_harvest_reconciliacion
from app.optimizer import harvest_destino, hygiene, windows
from tools import reversa_harvest


class MutacionDetectada(Exception):
    """El cliente simulado recibio una escritura a Amazon."""


def _escenario_harvest(caso: str):
    conn = Mock()
    conn.transaction.side_effect = lambda: nullcontext()
    conn.execute.return_value.fetchone.return_value = (1,)
    if caso == "apagado":
        conn.execute.return_value.fetchall.return_value = [
            (1, "harvest", 10, "arras", 99, {}, "pending_veto")
        ]
    else:
        conn.execute.return_value.fetchall.return_value = [
            (1, 99, "arras", 10, "pending", {}, "amazon_mx")
        ]
    cliente = Mock()
    cliente.crear_negative_exacto.side_effect = MutacionDetectada()
    aplicador = SimpleNamespace(
        _cliente=lambda: cliente,
        _profile_id=123,
        cycle_id_ejecutor=7,
        modo_efectivo=Mock(return_value="shadow"),
    )
    contexto = apply_harvest._Contexto(
        "amazon_mx",
        "origen-ag",
        "origen-c",
        "destino-ag",
        "destino-c",
        Decimal("11.62"),
        "MXN",
        Decimal("1"),
        Decimal("20"),
        "grupo",
        1,
    )
    return conn, cliente, aplicador, contexto


def test_apagar_goal_detiene_harvest_encolado_en_live():
    """Un goal pasado a shadow impide el POST de una cola nacida live."""
    conn, cliente, aplicador, contexto = _escenario_harvest("apagado")
    with ExitStack() as stack:
        for modulo, nombre, valor in (
            (apply, "gate_ancestros", None),
            (apply, "consume_quota_y_sello", (True, False)),
            (apply, "_ledger", 1),
            (apply_harvest, "_contexto", contexto),
            (apply_harvest, "_lista_todos", []),
            (apply_harvest, "revalida_harvest", None),
            (
                apply_harvest,
                "_nace_job",
                apply_harvest._Job(1, 99, "arras", 10, "pending", {}, "amazon_mx"),
            ),
            (apply_cola, "_modo_efectivo_corte", "shadow"),
        ):
            stack.enter_context(patch.object(modulo, nombre, return_value=valor))
        with suppress(MutacionDetectada):
            apply_cola.libera_vencidos(
                conn, "amazon_mx", ahora=dt.datetime.now(dt.UTC), aplicador=aplicador
            )
    assert cliente.crear_negative_exacto.call_count == 0


@pytest.mark.parametrize("ya_aplicado", [False, True])
def test_apagar_goal_confirma_negative_aplicado_sin_reintentar_post(ya_aplicado):
    """La reconciliacion lee Amazon, pero no crea un negative en shadow."""
    conn = Mock()
    conn.transaction.side_effect = lambda: nullcontext()

    def execute(sql, params=()):
        filas = {
            apply_harvest_reconciliacion._SQL_NEGATIVAS_APLICANDO: [(1, 10, "arras", 99)],
            apply_harvest._SQL_EXTERNALES: [("origen", "campana")],
        }[sql]
        return SimpleNamespace(
            fetchall=lambda: filas,
            fetchone=lambda: filas[0] if filas else None,
        )

    conn.execute.side_effect = execute
    cliente = Mock()
    aplicador = SimpleNamespace(_cliente=lambda: cliente, _profile_id="perfil", cycle_id_ejecutor=7)
    negativo = {
        "keywordId": "n-1",
        "adGroupId": "origen",
        "keywordText": "arras",
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }
    with (
        patch.object(apply, "gate_ancestros", return_value=None),
        patch.object(apply_cola, "_modo_efectivo_corte", return_value="shadow"),
        patch.object(apply_harvest, "_lista_todos", return_value=[negativo] if ya_aplicado else []),
        patch.object(apply_harvest, "_sella_pendientes"),
        patch.object(apply_harvest, "_termina_cola"),
        patch.object(apply, "_confirma_resumen"),
        patch.object(apply_harvest_reconciliacion, "_aprende_negative", return_value=(None, None)),
        patch.object(apply_harvest_reconciliacion.biblioteca, "avisa_si_fallo"),
    ):
        resultado = apply_harvest_reconciliacion._reconcilia_negativas(conn, aplicador, "amazon_mx")
    assert resultado == ((1 if ya_aplicado else 0), 0)
    cliente.crear_negative_exacto.assert_not_called()


def test_apagar_goal_cierra_higiene_sin_crear_hermanas():
    """El evento ya aplicado queda done y reversible al apagar el goal."""
    rol = apply_harvest.ROLES_DISCOVERY[0]
    externos = {
        "keyword_id": "k-1",
        "keyword_creada": True,
        "negative_id": "n-1",
        "negative_creada": True,
        "hermanas_objetivo": {rol: {"ad_group_id": "ag-hermana"}},
        "hermanas": {},
        "hermanas_ciclos": 0,
    }
    conn = Mock()
    conn.transaction.side_effect = lambda: nullcontext()
    conn.execute.return_value.fetchone.return_value = ("applied",)
    conn.execute.return_value.fetchall.return_value = [
        (1, 99, "arras", 10, "hermanas_negadas", externos, "amazon_mx")
    ]
    aplicador = SimpleNamespace(_cliente=Mock(side_effect=MutacionDetectada()))
    with (
        patch.object(apply_harvest_reconciliacion, "_cola_de", return_value=(1, "applied")),
        patch.object(apply_cola, "_modo_efectivo_corte", return_value="shadow"),
        patch.object(apply_harvest, "_continua_job", side_effect=MutacionDetectada()),
        patch.object(apply_harvest_reconciliacion, "_reconcilia_negativas", return_value=(0, 0)),
        patch.object(apply_harvest_reconciliacion, "_reconcilia_pauses", return_value=(0, 0)),
        patch.object(apply_harvest_reconciliacion, "_reconcilia_harvest_huerfanas", return_value=0),
        patch.object(apply_harvest.notifica, "notifica_harvest_hermanas", return_value=True),
    ):
        resumen = apply_harvest_reconciliacion.reconcilia_harvest(conn, aplicador, "amazon_mx")
    assert resumen.jobs_done == 1
    assert resumen.alertas[0].motivo == apply_harvest.MOTIVO_HERMANAS_PENDIENTES
    aplicador._cliente.assert_not_called()


def test_job_que_espera_quota_revalida_antes_de_aplicar():
    """Un job pending no consume quota ni hace POST si dejo de calificar."""
    conn, cliente, aplicador, contexto = _escenario_harvest("quota")
    with ExitStack() as stack:
        for modulo, nombre, valor in (
            (apply, "gate_ancestros", None),
            (apply, "consume_quota_y_sello", (True, False)),
            (apply, "_ledger", 1),
            (apply_harvest, "_contexto", contexto),
            (apply_harvest, "_lista_todos", []),
            (apply_harvest_reconciliacion, "_cola_de", (1, "released")),
            (apply_harvest_reconciliacion, "revalida_harvest", "ya_no_califica"),
            (apply_cola, "_modo_efectivo_corte", "live"),
            (
                apply_cola,
                "fila_cola",
                apply_cola.FilaCola(1, "harvest", 10, "arras", 99, {}, "released"),
            ),
            (apply_harvest_reconciliacion, "_reconcilia_negativas", (0, 0)),
            (apply_harvest_reconciliacion, "_reconcilia_pauses", (0, 0)),
            (apply_harvest_reconciliacion, "_reconcilia_harvest_huerfanas", 0),
        ):
            stack.enter_context(patch.object(modulo, nombre, return_value=valor))
        with suppress(MutacionDetectada):
            apply_harvest_reconciliacion.reconcilia_harvest(conn, aplicador, "amazon_mx")
    assert cliente.crear_negative_exacto.call_count == 0


@pytest.mark.parametrize("target_congelado", ["21.08", "100"])
def test_revalidacion_harvest_respeta_target_del_goal(target_congelado):
    """ACoS 30 excede el target 21.08 aunque sea menor que el tope 35."""
    ahora = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    fechas = tuple(dt.date(2026, 9, 9) + dt.timedelta(days=i) for i in range(7))
    termino = windows.AgregadoTermino(
        101,
        "arras boda",
        "MXN",
        Decimal("300"),
        Decimal("1000"),
        30,
        2,
        7,
        False,
        ahora,
    )
    ventana = windows.TerminosCortes(101, fechas[0], fechas[-1], fechas, (termino,))
    goal = (
        "campaign",
        100,
        None,
        Decimal("21.08"),
        Decimal("1"),
        Decimal("45"),
        "MXN",
        None,
        None,
        Decimal("11.62"),
        True,
        "live",
    )
    filas = {
        apply_harvest._SQL_DECISION: [(None, None, {"target_acos_pct_usado": target_congelado})],
        apply_harvest._SQL_PADRE: [(100,)],
        apply._SQL_GOALS_ENTIDAD: [goal],
        harvest_destino._SQL_GRUPO: [(1, "category_phrase", "200", "201")],
        harvest_destino._SQL_EXCEPCION: [],
        harvest_destino._SQL_GOALS: [goal],
    }

    class Memoria:
        def transaction(self):
            return nullcontext()

        def execute(self, sql, params=()):
            valores = filas[sql]

            class Filas:
                def __iter__(self):
                    return iter(valores)

                def fetchall(self):
                    return valores

                def fetchone(self):
                    return valores[0] if valores else None

            return Filas()

    with (
        patch.object(windows, "ventanas_evidencia_ad_group", return_value={}),
        patch.object(windows, "terminos_cortes", return_value=ventana),
        patch.object(hygiene, "keywords_campana_destino", return_value=frozenset()),
    ):
        motivo = apply_harvest_reconciliacion.revalida_harvest(
            Memoria(),
            "amazon_mx",
            apply_cola.FilaCola(1, "harvest", 101, "arras boda", 2, {}, "released"),
            ahora,
        )
    assert motivo == "ya_no_califica"


@pytest.mark.parametrize("intentos_previos", [0, 1])
def test_reversa_no_borra_objetos_adoptados_o_de_procedencia_incierta(intentos_previos):
    """Una lectura LIST no demuestra que este job creo los objetos."""
    job = apply_harvest._Job(2, 22, "kit arras", 202, "pending", {}, "amazon_mx")
    contexto = apply_harvest._Contexto(
        plataforma="amazon_mx",
        grupo_ext="origin-b",
        campana_ext="campaign-b",
        destino_grupo="exact",
        destino_campana="campaign-exact",
        default_bid=Decimal("11.62"),
        moneda="MXN",
        floor=Decimal("1"),
        ceiling=Decimal("20"),
        resuelto_por="grupo",
        grupo_id=1,
    )
    conn = MagicMock()
    conn.execute.return_value.rowcount = intentos_previos
    conn.execute.return_value.fetchone.return_value = (bool(intentos_previos),)
    cliente = MagicMock()

    def lista(path, body, profile_id):
        negativa = path == "/sp/negativeKeywords/list"
        item = {
            "keywordId": "negative-del-primero" if negativa else "keyword-del-primero",
            "adGroupId": "origin-b" if negativa else "exact",
            "keywordText": "kit arras",
            "state": "ENABLED",
            "matchType": "NEGATIVE_EXACT" if negativa else "EXACT",
        }
        return httpx.Response(200, json={"negativeKeywords" if negativa else "keywords": [item]})

    cliente.list_objects.side_effect = lista
    aplicador = SimpleNamespace(_profile_id="profile", _cliente=lambda: cliente)
    assert apply_harvest._paso_negative(conn, aplicador, job, contexto, None)[0] == "avanza"
    assert apply_harvest._paso_keyword(conn, aplicador, job, contexto, None)[0] == "avanza"
    job.fase = "done"
    filas = {
        apply_harvest._SQL_JOB_POR_ID: [
            (2, 22, job.search_term, 202, "done", job.external_ids, "amazon_mx")
        ],
        apply_harvest._SQL_VERIFY_OK: [(True,)],
        apply_harvest._SQL_ULTIMA_COLA: [("applied",)],
        apply_harvest._SQL_DECISION: [
            (None, None, {"goal": {"harvest": {"ad_group_id": "exact"}}})
        ],
        apply_harvest._SQL_EXTERNALES: [("origin-b", "campaign-b")],
        apply_harvest._SQL_ACKS_HERMANA: [],
    }

    def execute(sql, params=()):
        valores = filas[sql]
        return SimpleNamespace(
            fetchone=lambda: valores[0] if valores else None,
            fetchall=lambda: valores,
        )

    conn.execute.side_effect = execute
    if intentos_previos:
        assert job.external_ids["negative_creada"] == "incierta"
        assert job.external_ids["keyword_creada"] == "incierta"
        with pytest.raises(ValueError, match="procedencia"):
            apply_harvest.plan_reversa_harvest(conn, 2)
    else:
        pasos = apply_harvest.plan_reversa_harvest(conn, 2)[3]
        assert pasos == []


def test_reversa_automatica_no_borra_keyword_sin_post_propio():
    """Un ACK sin id del negativo no autoriza borrar una exacta ajena."""
    conn = Mock()
    conn.transaction.side_effect = lambda: nullcontext()
    conn.execute.return_value.fetchone.return_value = (False,)
    contexto = apply_harvest._Contexto(
        "amazon_mx",
        "origen",
        "campana",
        "destino",
        "campana-exacta",
        Decimal("11.62"),
        "MXN",
        Decimal("1"),
        Decimal("20"),
        "grupo",
        1,
    )
    job = apply_harvest._Job(1, 99, "arras", 10, "pending", {}, "amazon_mx")
    cliente = Mock()
    cliente.crear_negative_exacto.return_value = httpx.Response(200, json={})
    ajena = {
        "keywordId": "keyword-ajena",
        "adGroupId": "destino",
        "keywordText": "arras",
        "matchType": "EXACT",
        "state": "ENABLED",
    }

    def lista(_cliente, path, _profile):
        return [ajena] if path == "/sp/keywords/list" else []

    aplicador = SimpleNamespace(_cliente=lambda: cliente, _profile_id="perfil")
    with (
        patch.object(apply_harvest, "_lista_todos", side_effect=lista),
        patch.object(apply, "_ledger", return_value=1),
        patch.object(apply_harvest, "_reversa_delete", return_value=True) as borrado,
        patch.object(apply_harvest, "_falla_job", return_value=("failed", None)),
    ):
        apply_harvest._paso_negative(conn, aplicador, job, contexto, None)
    assert cliente.crear_keyword_exacta.call_count == 0
    borrado.assert_not_called()


def test_reversa_rechaza_go_de_solo_espacios():
    """La ceremonia no abre el cliente Amazon con un literal vacio."""
    paso = apply_harvest.PasoReversa("keyword", None, "exact", "k-1")
    huella = reversa_harvest._huella_pasos([paso])
    with (
        patch.object(reversa_harvest, "_dsn_decide", return_value="fake"),
        patch.object(reversa_harvest, "connect", return_value=Mock()),
        patch.object(
            reversa_harvest,
            "plan_reversa_harvest",
            return_value=("amazon_mx", "arras", 22, [paso]),
        ),
        patch.object(reversa_harvest, "_pendientes", return_value=[paso]),
        patch.object(reversa_harvest.apply, "_cliente_reversa") as cliente,
        patch.object(reversa_harvest, "ejecuta_reversa_harvest", return_value=(True, "ok")),
        pytest.raises(reversa_harvest.Abortar, match="--go"),
    ):
        reversa_harvest.main(
            [
                "--job",
                "2",
                "--acepto-mutacion-real",
                "--esperado",
                "1",
                "--go",
                "   ",
                "--huella",
                huella,
            ]
        )
    cliente.assert_not_called()
