"""ARREGLOS 05 F: la meta base avanza sin confundirse con temporada.

Hallazgo 12: con el ledger vencido la meta se congelaba porque el ancla se
reconocia por `notes.target.procedencia == 'margen_plataforma'`; el ciclo
rancio deja la procedencia en NULL y nunca puede ser el ancla del siguiente.
El contrato (DISENO F-G parte F) pide que la base salga del ultimo ciclo live
con decision comprometida, separada del overlay estacional en un snapshot
versionado, sin reconstruir nada por resta y con replay fiel a su version.

Estas regresiones ejercitan el ciclo completo sobre PostgreSQL 16 desechable:
lecturas, TX2, TX3, apply (sin Amazon: goals y temporada en shadow) y replay.
"""

import datetime as dt
import json
from decimal import Decimal

import pytest
from test_cycle import (
    DECIDED_AT,
    _config_version,
    _db_temporal,
    _decisions_de,
    _siembra_margen,
)
from test_schema import _postgres_obligatorio_ausente

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_MESES_ALTA_AGOSTO = [
    "baja",
    "baja",
    "alta",
    "alta",
    "baja",
    "baja",
    "normal",
    "alta",
    "normal",
    "alta",
    "alta",
    "normal",
]
_MESES_NORMAL_AGOSTO = [
    "baja",
    "baja",
    "alta",
    "alta",
    "baja",
    "baja",
    "normal",
    "normal",
    "normal",
    "alta",
    "alta",
    "normal",
]


def _config(conn, **cambios) -> int:
    settings = {
        "ads_optimizer_mode": "live",
        "ads_target_acos_pct_amazon_us": "29.5",
        "ads_target_fraccion_margen_amazon_us": "0.5",
        "ads_bid_politica_amazon_us": "niveles_v3",
    }
    settings.update(cambios)
    return _config_version(conn, settings)


def _temporada(conn, *, meses=None, modo="shadow") -> None:
    crudo = {
        "version": "temporada_v1",
        "meses": list(meses if meses is not None else _MESES_NORMAL_AGOSTO),
        "reduccion_baja_pct": "30",
        "dias_preparacion": 21,
        "mult_cortes_baja": "2",
        "aumento_presupuesto_alta_pct": "25",
        "fin_anticipado": None,
        "mantener_pausadas": [],
    }
    _config(
        conn,
        ads_temporada=crudo,
        ads_temporada_amazon_us=modo,
        ads_temporada_amazon_mx=modo,
    )


def _mundo(conn, *, setting="29.5") -> dict:
    """Mundo del peldano margen con el envelope en live y las decisiones en
    shadow (el mismo patron del test externo `optimizer test_12`): el ciclo
    real corre entero y no envia nada a Amazon."""
    ids = _siembra_margen(conn)
    conn.execute("UPDATE ads_optimizer_goal SET mode = 'shadow'")
    _config(conn, ads_target_acos_pct_amazon_us=str(setting))
    return ids


def _envejece_ledger(conn) -> None:
    """Ledger con 10 dias sin sincronizar: la vista publica ledger_rancio."""
    conn.execute(
        "UPDATE ingest_run SET started_at = now() - interval '10 days',"
        " finished_at = now() - interval '10 days'"
        " WHERE source = 'accounting_ledger_events'"
    )


def _target_de(conn, cycle_id: int) -> dict:
    notas = conn.execute("SELECT notes FROM optimizer_cycle WHERE id = %s", (cycle_id,)).fetchone()[
        0
    ]
    return json.loads(notas)["target"]


def _ciclo_previo(conn, notes: dict, *, mode="live") -> int:
    """Ciclo historico con sello de TX3, escrito como lo dejaria el binario
    anterior: el sello no nace en el INSERT (trigger de 0094)."""
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform, status, started_at,"
        " finished_at, decisions_count, notes)"
        " VALUES ('ads_optimizer', %s, 'amazon_us', 'done', %s, %s, 0, %s)"
        " RETURNING id",
        (
            mode,
            DECIDED_AT - dt.timedelta(hours=2),
            DECIDED_AT - dt.timedelta(hours=1),
            json.dumps(notes),
        ),
    ).fetchone()[0]
    conn.execute(
        "UPDATE optimizer_cycle SET decision_committed_at = %s WHERE id = %s",
        (DECIDED_AT - dt.timedelta(hours=1), ciclo_id),
    )
    return ciclo_id


def _sin_perfil(*_args, **_kwargs):
    from app.cycle_apply import SinPerfilAplicar

    raise SinPerfilAplicar("sin perfil de Amazon en el laboratorio")


def _aplicador_de_prueba(conn, **kwargs):
    """Aplicador real sin credenciales (el mismo que usa `optimizer test_12`):
    con los goals en shadow ninguna decision sale a Amazon."""
    from app import cycle as ciclo

    return ciclo.Aplicador(conn, **kwargs)


def _corre_f(conn, *, decided_at: dt.datetime = DECIDED_AT):
    from app import cycle as ciclo

    return ciclo.corre_ciclo(
        conn,
        platform="amazon_us",
        owner="f05:test",
        decided_at=decided_at,
        heartbeat_cada=1,
        aplicador_factory=_aplicador_de_prueba,
    )


# ---------------------------------------------------------------------------
# F.1: la base avanza con ledger rancio
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_f_base_avanza_con_ledger_rancio():
    """29 -> 28.5 -> 28: con el ledger vencido cada ciclo camina 0.5 hacia el
    setting desde la base del ciclo anterior, no desde el ultimo snapshot con
    procedencia 'margen_plataforma'. Antes del arreglo la secuencia era
    29 -> 28.5 -> 28.5 (meta congelada)."""
    with _db_temporal("orbit_f05_rancio") as (conn, _extra):
        _mundo(conn, setting="29.5")
        primero = _corre_f(conn)
        assert primero.status == "done", primero.notes
        inicial = _target_de(conn, primero.cycle_id)
        assert Decimal(inicial["target_aplicado"]) == Decimal("29")
        assert inicial["procedencia"] == "margen_plataforma"

        _envejece_ledger(conn)
        _config(conn, ads_target_acos_pct_amazon_us="28")
        vistos = []
        for _ in range(2):
            res = _corre_f(conn)
            assert res.status == "done", res.notes
            target = _target_de(conn, res.cycle_id)
            assert target["motivo_abstencion"] == "ledger_rancio"
            assert target["convergiendo_a_setting"] is True
            assert target["procedencia"] is None
            vistos.append((res.cycle_id, target))

        aplicados = [Decimal(cycle_target["target_aplicado"]) for _, cycle_target in vistos]
        assert aplicados == [Decimal("28.5"), Decimal("28.0")], (
            "la base camina 0.5 por ciclo; no se congela en el ultimo snapshot sano"
        )
        assert inicial["version"] == "target_v2"
        assert inicial["base"]["valor"] == "29.0"
        assert inicial["base"]["origen"] == "setting"
        assert inicial["overlay"]["modo"] is None, "temporada no configurada: ausencia"
        assert vistos[0][1]["base"]["valor"] == "28.5"
        assert vistos[0][1]["base"]["origen"] == f"ciclo:{primero.cycle_id}"
        assert vistos[1][1]["base"]["valor"] == "28"
        assert Decimal(vistos[1][1]["base"]["valor"]) == Decimal("28.0")
        assert vistos[1][1]["base"]["origen"] == f"ciclo:{vistos[0][0]}"

        # La hoja recibe la misma base que el peldano congelo en notes.target.
        for (cycle_id, _target), esperado in zip(vistos, aplicados, strict=True):
            hojas = conn.execute(
                "SELECT target_acos_pct FROM target_acos_ciclo WHERE cycle_id = %s",
                (cycle_id,),
            ).fetchall()
            assert [Decimal(fila[0]) for fila in hojas] == [esperado]


# ---------------------------------------------------------------------------
# F.1: el overlay de alta 25 no se vuelve la siguiente base
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_f_overlay_alta_no_se_vuelve_base():
    """En temporada alta la meta del juicio es max(base, 25), pero la base
    sigue siendo la convergente: el snapshot guarda las dos por separado y la
    siguiente base sale de `base`, nunca del overlay."""
    with _db_temporal("orbit_f05_alta") as (conn, _extra):
        _mundo(conn, setting="29.5")
        _temporada(conn, meses=_MESES_ALTA_AGOSTO, modo="live")
        primero = _corre_f(conn)
        assert primero.status == "done", primero.notes
        inicial = _target_de(conn, primero.cycle_id)
        assert Decimal(inicial["target_aplicado"]) == Decimal("29")

        _envejece_ledger(conn)
        _config(
            conn,
            ads_target_acos_pct_amazon_us="28",
            ads_temporada={
                "version": "temporada_v1",
                "meses": list(_MESES_ALTA_AGOSTO),
                "reduccion_baja_pct": "30",
                "dias_preparacion": 21,
                "mult_cortes_baja": "2",
                "aumento_presupuesto_alta_pct": "25",
                "fin_anticipado": None,
                "mantener_pausadas": [],
            },
            ads_temporada_amazon_us="live",
            ads_temporada_amazon_mx="live",
        )
        segundo = _corre_f(conn)
        assert segundo.status == "done", segundo.notes
        target = _target_de(conn, segundo.cycle_id)
        assert Decimal(target["target_aplicado"]) == Decimal("28.5")
        assert Decimal(target["target_aplicado"]) != Decimal("25"), (
            "el overlay estacional no es la siguiente base"
        )
        assert inicial["base"]["valor"] == "29.0"
        assert inicial["overlay"]["fase"] == "alta"
        assert inicial["overlay"]["meta_alta_pct"] == "25"
        assert inicial["overlay"]["aplica"] is True
        assert target["base"]["valor"] == "28.5"
        assert target["base"]["origen"] == f"ciclo:{primero.cycle_id}"
        assert target["overlay"]["meta_alta_pct"] == "25"

        # El caso congelado guarda la base y la meta de alta por separado: la
        # meta efectiva la deriva `decide`, no el snapshot.
        bids = [fila for fila in _decisions_de(conn, segundo.cycle_id) if fila[1] == "bid"]
        assert bids, "el mundo de margen deja una decision de bid"
        inputs = bids[0][9]
        assert Decimal(inputs["target_acos_pct_usado"]) == Decimal("28.5")
        assert Decimal(inputs["caso"]["temporada"]["meta_alta_pct"]) == Decimal("25")


@_skip_db
def test_arreglos05_f_salida_de_alta_retoma_la_base():
    """Al salir de la alta, el overlay deja de aplicar y la meta vuelve a la
    base sin rampa ni numero inventado: la base del ciclo siguiente sigue
    siendo la convergente del snapshot."""
    with _db_temporal("orbit_f05_salida") as (conn, _extra):
        _mundo(conn, setting="29.5")
        _temporada(conn, meses=_MESES_ALTA_AGOSTO, modo="live")
        primero = _corre_f(conn)
        assert primero.status == "done", primero.notes
        base = _target_de(conn, primero.cycle_id)["base"]["valor"]
        assert base == "29.0"

        _temporada(conn, meses=_MESES_NORMAL_AGOSTO, modo="live")
        segundo = _corre_f(conn)
        assert segundo.status == "done", segundo.notes
        target = _target_de(conn, segundo.cycle_id)
        assert target["overlay"]["fase"] == "normal"
        assert target["overlay"]["aplica"] is False
        assert target["base"]["valor"] == "28.5"
        assert target["base"]["origen"] == f"ciclo:{primero.cycle_id}"


# ---------------------------------------------------------------------------
# F.1: la practica no modifica el ancla live
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_f_practica_no_cambia_el_ancla_live():
    """Un ciclo en practica (escalera shadow) congela su overlay pero no es
    ancla: el siguiente ciclo live retoma la base del ultimo live
    comprometido, no la del ciclo de practica."""
    with _db_temporal("orbit_f05_practica") as (conn, _extra):
        _mundo(conn, setting="29.5")
        live1 = _corre_f(conn)
        assert live1.status == "done", live1.notes

        _config(
            conn,
            ads_optimizer_mode="shadow",
            ads_target_acos_pct_amazon_us="26",
        )
        practica = _corre_f(conn)
        assert practica.status == "done", practica.notes
        target_practica = _target_de(conn, practica.cycle_id)

        _config(conn, ads_optimizer_mode="live", ads_target_acos_pct_amazon_us="28")
        live2 = _corre_f(conn)
        assert live2.status == "done", live2.notes
        target = _target_de(conn, live2.cycle_id)
        assert Decimal(target["target_aplicado"]) == Decimal("28.5"), (
            "la practica no mueve el ancla del ciclo live"
        )
        assert _target_de(conn, live1.cycle_id)["base"]["valor"] == "29.0"
        assert target_practica["base"]["origen"] == f"ciclo:{live1.cycle_id}"
        assert target["base"]["origen"] == f"ciclo:{live1.cycle_id}", (
            "la practica no es ancla del ciclo live"
        )


# ---------------------------------------------------------------------------
# F.2: snapshot viejo sin base inequivoca
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_f_snapshot_viejo_sin_base_conserva_semantica():
    """Un snapshot v1 con procedencia NULL (el ciclo rancio del binario
    anterior) no acredita base: se conserva la semantica vieja, la ausencia es
    explicita y la inicializacion aprobada es el setting. Jamas se reconstruye
    restando un overlay supuesto."""
    with _db_temporal("orbit_f05_vieja") as (conn, _extra):
        _mundo(conn, setting="28")
        previo = _ciclo_previo(
            conn,
            {
                "target": {
                    "procedencia": None,
                    "motivo_abstencion": "ledger_rancio",
                    "convergiendo_a_setting": True,
                    "target_aplicado": "27",
                }
            },
        )
        _envejece_ledger(conn)
        res = _corre_f(conn)
        assert res.status == "done", res.notes
        target = _target_de(conn, res.cycle_id)
        assert target["procedencia"] is None
        assert target["base"]["origen"] == "setting"
        assert target["base"]["valor"] is None, "ausencia explicita: el peldano no aplica"
        assert target["target_aplicado"] is None, (
            "sin base inequivoca el peldano no camina desde el 27 del snapshot rancio"
        )
        assert target["base"]["origen"] != f"ciclo:{previo}"
        # El setting es la inicializacion ya aprobada: la hoja recibe 28.
        filas = conn.execute(
            "SELECT target_acos_pct, procedencia FROM target_acos_ciclo WHERE cycle_id = %s",
            (res.cycle_id,),
        ).fetchall()
        assert filas == [(Decimal("28"), "setting_plataforma")]


@_skip_db
def test_arreglos05_f_snapshot_viejo_con_base_inequivoca_es_ancla():
    """La compatibilidad conserva la semantica original: un snapshot v1 cuyo
    `procedencia` es 'margen_plataforma' y trae `target_aplicado` si acredita
    base, y el ciclo nuevo lo declara con su origen."""
    with _db_temporal("orbit_f05_v1") as (conn, _extra):
        _mundo(conn, setting="28")
        previo = _ciclo_previo(
            conn,
            {
                "target": {
                    "procedencia": "margen_plataforma",
                    "motivo_abstencion": None,
                    "convergiendo_a_setting": False,
                    "target_aplicado": "29",
                }
            },
        )
        _envejece_ledger(conn)
        res = _corre_f(conn)
        assert res.status == "done", res.notes
        target = _target_de(conn, res.cycle_id)
        assert target["base"]["valor"] == "28.5"
        assert target["base"]["origen"] == f"ciclo:{previo}"


@_skip_db
def test_arreglos05_f_fraccion_apagada_no_borra_el_ancla():
    """Un ciclo sin fraccion no entrega base (`base.valor` nulo) pero no borra
    la ultima base explicita: al reencender la fase la meta camina 0.5 desde
    ahi y no salta al derivado. La ausencia queda visible en su snapshot."""
    with _db_temporal("orbit_f05_fraccion") as (conn, _extra):
        _mundo(conn, setting="29.5")
        primero = _corre_f(conn)
        assert primero.status == "done", primero.notes
        assert Decimal(_target_de(conn, primero.cycle_id)["target_aplicado"]) == Decimal("29")

        _config_version(
            conn,
            {
                "ads_optimizer_mode": "live",
                "ads_target_acos_pct_amazon_us": "29.5",
                "ads_bid_politica_amazon_us": "niveles_v3",
            },
        )
        apagada = _corre_f(conn)
        assert apagada.status == "done", apagada.notes
        target_apagada = _target_de(conn, apagada.cycle_id)
        assert target_apagada["motivo_abstencion"] == "sin_fraccion"
        assert target_apagada["base"]["valor"] is None
        assert target_apagada["target_aplicado"] is None
        filas = conn.execute(
            "SELECT target_acos_pct, procedencia FROM target_acos_ciclo WHERE cycle_id = %s",
            (apagada.cycle_id,),
        ).fetchall()
        assert filas == [(Decimal("29.5"), "setting_plataforma")]

        _config(conn, ads_optimizer_mode="live", ads_target_acos_pct_amazon_us="28")
        vuelta = _corre_f(conn)
        assert vuelta.status == "done", vuelta.notes
        objetivo = _target_de(conn, vuelta.cycle_id)
        assert Decimal(objetivo["target_aplicado"]) == Decimal("28.5"), (
            "reencender la fraccion no inventa margen fresco: camina desde el ancla"
        )
        assert objetivo["base"]["origen"] == f"ciclo:{primero.cycle_id}"


# ---------------------------------------------------------------------------
# F.2: replay de decisiones antiguas y lote nuevo tras recuperacion parcial
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_f_replay_consume_su_version():
    """El replay re-decide con los inputs congelados de su fecha: cambiar la
    configuracion (fraccion apagada, otra meta de alta, escalera off) no altera
    el veredicto de una decision antigua. El snapshot declara su version."""
    from app.optimizer.replay import reproduce

    with _db_temporal("orbit_f05_replay") as (conn, _extra):
        _mundo(conn, setting="29.5")
        _temporada(conn, meses=_MESES_ALTA_AGOSTO, modo="live")
        corrida = _corre_f(conn)
        assert corrida.status == "done", corrida.notes
        filas = [fila for fila in _decisions_de(conn, corrida.cycle_id) if fila[1] == "bid"]
        assert filas
        inputs = filas[0][9]
        esperado = (filas[0][1], filas[0][4], filas[0][5])

        # La configuracion de hoy cambia por completo: la fraccion del margen
        # se apaga (clave ausente), la meta de alta baja a 10 y el motor global
        # queda en off.
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "off",
                "ads_target_acos_pct_amazon_us": "45",
                "ads_bid_politica_amazon_us": "niveles_v3",
                "ads_temporada": {
                    "version": "temporada_v1",
                    "meses": list(_MESES_ALTA_AGOSTO),
                    "reduccion_baja_pct": "30",
                    "dias_preparacion": 21,
                    "mult_cortes_baja": "2",
                    "aumento_presupuesto_alta_pct": "25",
                    "fin_anticipado": None,
                    "mantener_pausadas": [],
                },
                "ads_temporada_amazon_us": "live",
                "ads_temporada_amazon_mx": "live",
                "ads_temporada_meta_alta_amazon_us": "10",
            },
        )
        assert reproduce(inputs) == esperado, (
            "el replay re-decide con su freeze, no con la configuracion de hoy"
        )
        assert inputs["target_snapshot"]["version"] == "target_v2"
        assert inputs["target_snapshot"]["base"]["valor"] == "29.0"


@_skip_db
def test_arreglos05_f_recuperacion_parcial_retoma_la_base():
    """Tras un apply parcial el origen sigue siendo el ancla: el ejecutor
    ligado por `resume_of` no decide ni se sella, y el lote nuevo del dia
    siguiente retoma la base del origen sin duplicar decisiones."""
    from app import cycle as ciclo

    with _db_temporal("orbit_f05_resume") as (conn, _extra):
        _mundo(conn, setting="29.5")
        origen = ciclo.corre_ciclo(
            conn,
            platform="amazon_us",
            owner="f05:origen",
            decided_at=DECIDED_AT,
            heartbeat_cada=1,
            aplicador_factory=_sin_perfil,
        )
        assert origen.status == "degraded", origen.notes

        recuperacion = ciclo.atender_plataforma(
            conn,
            "amazon_us",
            owner="f05:ejecutor",
            ahora=DECIDED_AT,
            heartbeat_cada=1,
            aplicador_factory=_sin_perfil,
        )
        assert recuperacion.ciclo is not None, recuperacion.motivo
        ejecutor = recuperacion.ciclo
        assert ejecutor.resume_of == origen.cycle_id
        assert ejecutor.decisions_count == 0
        fila = conn.execute(
            "SELECT decision_committed_at, notes FROM optimizer_cycle WHERE id = %s",
            (ejecutor.cycle_id,),
        ).fetchone()
        assert fila[0] is None, "el ejecutor no compromete decisiones"
        assert "target" not in json.loads(fila[1]), "el ejecutor no resuelve meta"
        assert (
            conn.execute(
                "SELECT count(*) FROM decision WHERE cycle_id = %s", (ejecutor.cycle_id,)
            ).fetchone()[0]
            == 0
        )

        siguiente = ciclo.corre_ciclo(
            conn,
            platform="amazon_us",
            owner="f05:manana",
            decided_at=DECIDED_AT + dt.timedelta(days=1),
            heartbeat_cada=1,
            aplicador_factory=_aplicador_de_prueba,
        )
        assert siguiente.status == "done", siguiente.notes
        target = _target_de(conn, siguiente.cycle_id)
        assert Decimal(target["target_aplicado"]) == Decimal("28.5"), (
            "el lote nuevo camina desde la base del origen"
        )
        assert _target_de(conn, origen.cycle_id)["base"]["valor"] == "29.0"
        assert target["base"]["origen"] == f"ciclo:{origen.cycle_id}", (
            "el lote nuevo ancla al origen comprometido, nunca al ejecutor"
        )
