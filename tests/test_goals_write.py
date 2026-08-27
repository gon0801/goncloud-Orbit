"""Tests de la escritura amigable de goals (ORBIT 04, task 3.2; sellado 26).

UN camino (regla 1): `app/goals_write.edita_goal` — el endpoint POST
`/api/ads-optimizer/goals/{goal_id}` y el CLI `goals set` lo DESPACHAN, jamas
duplican su SQL (candado en test_architecture). Contrato:

1. UPDATE con `updated_at` EXPLICITO y OBLIGATORIO (dashboard-01 r2: no hay
   trigger que lo mantenga — omitirlo seria historia falsa). El test pinea un
   instante fijo y afirma que la fila queda con ESE valor exacto (regla 9:
   demostrado en rojo con el mutante que omite la columna del SET).
2. PRE-VALIDACION en espanol ANTES del UPDATE combinando valores NUEVOS con
   los EXISTENTES de la fila: floor <= ceiling (positivos), target > 0,
   harvest_default_bid > 0 o NULL y la terna harvest all-or-nothing del CHECK
   goal_harvest_completo (el flag `harvest_limpia` pone los TRES a NULL).
   GoalInvalido -> 422 (endpoint) / exit 2 (CLI: uso invalido, patron
   argparse); GoalInexistente -> 404 / exit 1.
3. La edicion es VISIBLE al ciclo siguiente: el ciclo que corre tras editar
   target congela inputs.target_acos_pct_usado == valor nuevo en su decision
   (la MISMA fuente _SQL_GOALS de app/cycle, regla 2 — un numero una fuente).
4. AUTH: la del router de escritura (sellado 18) — token solo header ANTES de
   abrir el DSN admin; sin token -> 401 aunque el DSN tambien falte.

Los tests CLI viven en test_cli.py (ahi esta el patron de envoltorio delgado);
aqui van la implementacion (unit + PG16 real) y el endpoint.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_api_write import TOKEN, _ConnFake, _db_con_rol_admin, _secrets_token
from test_cycle import DECIDED_AT, _siembra_maestra
from test_schema import _postgres_obligatorio_ausente

from app import goals_write
from app.main import app

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

# Reloj FIJO de la edicion: distinto del now() de la DB y del valor sembrado,
# para que el test distinga "la fila quedo con el instante PASADO" de cualquier
# otra cosa (mutante que omite updated_at del SET: la fila quedaria en 2020).
T_EDITADO = dt.datetime(2026, 8, 26, 10, 0, tzinfo=dt.UTC)
T_SEMBRADO = dt.datetime(2020, 1, 1, 0, 0, tzinfo=dt.UTC)


def _siembra_goal_plataforma(
    conn, *, target="25", floor="0.40", ceiling="2.50", harvest=None
) -> int:
    """Goal de plataforma amazon_us; `harvest` None = terna NULL, o la tupla
    completa (campaign, ad_group, bid). updated_at sembrado EN 2020: cualquier
    UPDATE honesto lo mueve, el mutante que omite la columna lo deja ahi."""
    cols = [
        "scope",
        "platform",
        "target_acos_pct",
        "bid_floor",
        "bid_ceiling",
        "bid_currency",
        "enabled",
        "mode",
        "updated_at",
    ]
    params: list = [
        "platform",
        "amazon_us",
        Decimal(target),
        Decimal(floor),
        Decimal(ceiling),
        "USD",
        True,
        "shadow",
        T_SEMBRADO,
    ]
    if harvest is not None:
        cols += ["harvest_campaign_id", "harvest_ad_group_id", "harvest_default_bid"]
        params += [harvest[0], harvest[1], Decimal(harvest[2])]
    marcadores = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"INSERT INTO ads_optimizer_goal ({', '.join(cols)}) VALUES ({marcadores}) RETURNING id",
        params,
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# UNIT del endpoint (sin Postgres): auth primero, despacho y mapeo de errores
# ---------------------------------------------------------------------------


def _goal_monkeypatcheada(monkeypatch, tmp_path, resultado=None, error=None, captura=None):
    """Entorno del endpoint de goals SIN Postgres: token al dia, connect falso
    y edita_goal espiada (resultado fijo o excepcion) — mismo patron que
    _reversa_monkeypatcheada de test_api_write."""
    import app.api_write as api_write

    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://fake:fake@127.0.0.1:5432/fake")
    monkeypatch.setattr(api_write, "connect", lambda dsn, **kw: _ConnFake())

    def _edita(conn, goal_id, *, updated_at, **kw):
        if captura is not None:
            captura.clear()
            captura["goal_id"] = goal_id
            captura["updated_at"] = updated_at
            captura.update(kw)
        if error is not None:
            raise error
        return resultado

    monkeypatch.setattr(goals_write, "edita_goal", _edita)


def test_endpoint_goals_sin_token_401(tmp_path, monkeypatch):
    """DoD: sin el header x-orbit-token -> 401 ANTES de abrir el DSN (el test
    ni siquiera define ORBIT_DSN_ADMIN)."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post("/api/ads-optimizer/goals/1", json={"target_acos_pct": "20"})
    assert resp.status_code == 401
    assert "x-orbit-token" in resp.json()["detail"]


def test_endpoint_goals_happy_path_despacha_a_edita_goal(tmp_path, monkeypatch):
    """Token ok -> 200 con la fila que devuelve edita_goal (mismo shape que GET
    /goals); el despacho pasa goal_id del path, los campos del body y un
    updated_at tz-aware (now UTC del servidor)."""
    fila = {
        "id": 7,
        "scope": "platform",
        "platform": "amazon_us",
        "target_acos_pct": "20.00",
        "enabled": True,
        "bid_floor": "0.4000",
        "bid_ceiling": "2.5000",
        "bid_currency": "USD",
        "harvest_campaign_id": None,
        "harvest_ad_group_id": None,
        "harvest_default_bid": None,
        "mode": "shadow",
        "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        "updated_at": T_EDITADO,
    }
    captura: dict = {}
    _goal_monkeypatcheada(monkeypatch, tmp_path, resultado=fila, captura=captura)
    resp = TestClient(app).post(
        "/api/ads-optimizer/goals/7",
        json={
            "target_acos_pct": "20",
            "enabled": True,
            "bid_floor": "0.40",
            "campo_ajeno": "ignorado",
        },
        headers={"x-orbit-token": TOKEN},
    )
    assert resp.status_code == 200, resp.text
    # Las fechas viajan ISO (Python escribe 'Z' por UTC): se comparan
    # PARSEADAS de vuelta (mismo instante), el resto campo por campo.
    cuerpo = resp.json()
    for campo in ("created_at", "updated_at"):
        assert dt.datetime.fromisoformat(cuerpo[campo]) == fila[campo], campo
        del cuerpo[campo]
    sin_fechas = dict(fila)
    del sin_fechas["created_at"]
    del sin_fechas["updated_at"]
    assert cuerpo == sin_fechas
    assert captura["goal_id"] == 7
    assert captura["target_acos_pct"] == Decimal("20")
    assert captura["enabled"] is True
    assert captura["bid_floor"] == Decimal("0.40")
    assert captura["updated_at"].tzinfo is not None  # now UTC del servidor


def test_endpoint_goals_inexistente_404(tmp_path, monkeypatch):
    _goal_monkeypatcheada(
        monkeypatch, tmp_path, error=goals_write.GoalInexistente("goal 99999 no existe")
    )
    resp = TestClient(app).post(
        "/api/ads-optimizer/goals/99999", json={}, headers={"x-orbit-token": TOKEN}
    )
    assert resp.status_code == 404
    assert "99999" in resp.json()["detail"]


def test_endpoint_goals_invalido_422_con_motivo(tmp_path, monkeypatch):
    """GoalInvalido -> 422 y el detail ES el motivo (harvest incompleto,
    floor>ceiling: la pre-validacion combina nuevo+existente)."""
    for motivo in (
        "config de harvest incompleta: los tres campos van juntos o no van",
        "bid_floor 3.00 > bid_ceiling 2.50",
    ):
        _goal_monkeypatcheada(monkeypatch, tmp_path, error=goals_write.GoalInvalido(motivo))
        resp = TestClient(app).post(
            "/api/ads-optimizer/goals/1",
            json={"target_acos_pct": "20"},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == motivo


def test_endpoint_goals_valida_montos_positivos_422(tmp_path, monkeypatch):
    """El body valida gt=0: target/floor/ceiling/harvest-bid en 0 o negativos
    son 422 de pydantic ANTES de tocar la base (edita_goal JAMAS se llama)."""
    _goal_monkeypatcheada(monkeypatch, tmp_path, resultado={"id": 1})
    cliente = TestClient(app)
    for cuerpo in (
        {"target_acos_pct": 0},
        {"target_acos_pct": "-5"},
        {"bid_floor": 0},
        {"bid_ceiling": "-1"},
        {"harvest_default_bid": 0},
    ):
        resp = cliente.post(
            "/api/ads-optimizer/goals/1", json=cuerpo, headers={"x-orbit-token": TOKEN}
        )
        assert resp.status_code == 422, f"{cuerpo} deberia ser 422, no {resp.status_code}"


# ---------------------------------------------------------------------------
# Validacion de ENTRADA pura (sin I/O): hallazgos #1-#3 de la review 3.2
# ---------------------------------------------------------------------------

# Conn centinela: la validacion de entrada corre ANTES de leer/tocar la fila;
# si edita_goal la toca, AttributeError y el test revienta (el guard no disparo).
_CONN_INTOCABLE = object()


def test_edita_goal_rechaza_cadenas_vacias_en_la_terna_harvest():
    """#1: '' (o solo espacios) en harvest_campaign_id/harvest_ad_group_id
    cuenta como "presente" para la terna y almacenaria config harvest
    "completa" con ids vacios (regla 3: faltante no es cadena vacia). Lo
    rechaza el camino UNICO antes de cualquier I/O, con el nombre del campo."""
    with pytest.raises(goals_write.GoalInvalido, match="harvest_campaign_id"):
        goals_write.edita_goal(_CONN_INTOCABLE, 7, harvest_campaign_id="", updated_at=T_EDITADO)
    with pytest.raises(goals_write.GoalInvalido, match="harvest_ad_group_id"):
        goals_write.edita_goal(_CONN_INTOCABLE, 7, harvest_ad_group_id="   ", updated_at=T_EDITADO)


def test_edita_goal_rechaza_edicion_vacia_antes_de_leer_la_fila():
    """#2: un body/argv sin NINGUN campo no es edicion — ejecutaria un UPDATE
    que solo mueve updated_at (rastro que miente en espiritu). GoalInvalido
    ANTES de leer la fila, por el camino unico (el CLI ya lo rechazaba; el
    endpoint re-sellaba la fila con 200)."""
    with pytest.raises(goals_write.GoalInvalido, match="edicion vacia"):
        goals_write.edita_goal(_CONN_INTOCABLE, 7, updated_at=T_EDITADO)


def test_edita_goal_rechaza_decimales_no_finitos():
    """#3: Decimal('Infinity') pasa el gt=0 de pydantic y las comparaciones de
    pre-validacion (NaN las esquiva: toda comparacion es False), y PG16
    ACEPTA ambos valores en NUMERIC. El camino unico exige finitos antes de
    cualquier I/O. (El endpoint ya los rechaza pydantic: test mas abajo.)"""
    for nombre, valor in (
        ("target_acos_pct", Decimal("Infinity")),
        ("target_acos_pct", Decimal("NaN")),
        ("bid_floor", Decimal("Infinity")),
        ("bid_ceiling", Decimal("NaN")),
        ("harvest_default_bid", Decimal("Infinity")),
    ):
        with pytest.raises(goals_write.GoalInvalido, match="finito"):
            goals_write.edita_goal(_CONN_INTOCABLE, 7, **{nombre: valor}, updated_at=T_EDITADO)


def test_endpoint_goals_ids_harvest_vacios_422(tmp_path, monkeypatch):
    """#1 (regresion review 3.2): '' en harvest_campaign_id/harvest_ad_group_id
    -> 422 de pydantic (min_length=1 en CuerpoGoal) ANTES de despachar; el
    whitespace-only lo caza goals_write antes del UPDATE (tambien 422)."""
    _goal_monkeypatcheada(monkeypatch, tmp_path, resultado={"id": 1})
    cliente = TestClient(app)
    for cuerpo in (
        {"harvest_campaign_id": ""},
        {"harvest_ad_group_id": ""},
    ):
        resp = cliente.post(
            "/api/ads-optimizer/goals/1", json=cuerpo, headers={"x-orbit-token": TOKEN}
        )
        assert resp.status_code == 422, f"{cuerpo} deberia ser 422, no {resp.status_code}"


def test_endpoint_goals_numericos_no_finitos_422(tmp_path, monkeypatch):
    """#3 (documentacion de la capa pydantic): Infinity/NaN en los montos del
    body los RECHAZA pydantic por si solo (verificado en vivo contra la
    version instalada) — el endpoint jamas los despacha. Quien si puede llegar
    a goals_write es el CLI (Decimal('NaN') parsea en argparse), y ese caso lo
    cubre el guard del camino unico (test de arriba + test_cli)."""
    _goal_monkeypatcheada(monkeypatch, tmp_path, resultado={"id": 1})
    cliente = TestClient(app)
    for cuerpo in (
        {"target_acos_pct": "Infinity"},
        {"target_acos_pct": "NaN"},
        {"bid_floor": "Infinity"},
        {"bid_ceiling": "NaN"},
        {"harvest_default_bid": "Infinity"},
    ):
        resp = cliente.post(
            "/api/ads-optimizer/goals/1", json=cuerpo, headers={"x-orbit-token": TOKEN}
        )
        assert resp.status_code == 422, f"{cuerpo} deberia ser 422, no {resp.status_code}"


# ---------------------------------------------------------------------------
# PG16 REAL: UPDATE honesto, pre-validacion combinada y visibilidad al ciclo
# ---------------------------------------------------------------------------


@_skip_db
def test_edita_target_actualiza_fila_y_updated_at_es_el_pasado():
    """DoD (regla 9): tras editar target, la fila trae el target NUEVO y
    updated_at == el instante PASADO por quien llama — exacto, no now() de la
    DB (no hay trigger que lo mantenga; dashboard-01 r2). El mutante que omite
    updated_at del SET deja la fila en el 2020 sembrado y este test revienta."""
    with _db_con_rol_admin("orbit_g_upd") as (conn, dsn_admin, _dsn_l):
        goal_id = _siembra_goal_plataforma(conn, target="25")
        conn_admin = psycopg.connect(dsn_admin)
        try:
            fila = goals_write.edita_goal(
                conn_admin, goal_id, target_acos_pct=Decimal("30"), updated_at=T_EDITADO
            )
        finally:
            conn_admin.close()

        assert fila["id"] == goal_id
        assert fila["target_acos_pct"] == "30.00"
        assert fila["updated_at"] == T_EDITADO, (
            "updated_at debe quedar con el instante EXPLICITO pasado por quien llama"
        )
        en_db = conn.execute(
            "SELECT target_acos_pct, updated_at FROM ads_optimizer_goal WHERE id = %s",
            (goal_id,),
        ).fetchone()
        assert en_db[0] == Decimal("30.00")
        assert en_db[1] == T_EDITADO
        assert en_db[1] != T_SEMBRADO, "el UPDATE sin updated_at dejaria la fila en 2020"


@_skip_db
def test_goal_inexistente_reviene_con_excepcion_propia():
    with _db_con_rol_admin("orbit_g_404") as (conn, dsn_admin, _dsn_l):
        conn_admin = psycopg.connect(dsn_admin)
        try:
            with pytest.raises(goals_write.GoalInexistente, match="99999"):
                goals_write.edita_goal(
                    conn_admin, 99999, target_acos_pct=Decimal("20"), updated_at=T_EDITADO
                )
        finally:
            conn_admin.close()


@_skip_db
def test_floor_mayor_que_ceiling_combinado_con_valor_existente():
    """La pre-validacion combina NUEVO+EXISTENTE: solo el floor nuevo (3.00)
    contra el ceiling VIEJO (2.50) ya es invalido — y al reves (ceiling 0.20
    contra floor 0.40). El UPDATE jamas se ejecuta con datos que violarian
    goal_piso_bajo_techo/goal_bids_positivos (mensajes en espanol, 422)."""
    with _db_con_rol_admin("orbit_g_piso") as (conn, dsn_admin, _dsn_l):
        goal_id = _siembra_goal_plataforma(conn)
        conn_admin = psycopg.connect(dsn_admin)
        try:
            with pytest.raises(goals_write.GoalInvalido, match="bid_floor"):
                goals_write.edita_goal(
                    conn_admin, goal_id, bid_floor=Decimal("3.00"), updated_at=T_EDITADO
                )
            with pytest.raises(goals_write.GoalInvalido, match="bid_ceiling"):
                goals_write.edita_goal(
                    conn_admin, goal_id, bid_ceiling=Decimal("0.20"), updated_at=T_EDITADO
                )
            # un floor valido dentro del ceiling existente SI pasa
            fila = goals_write.edita_goal(
                conn_admin, goal_id, bid_floor=Decimal("0.50"), updated_at=T_EDITADO
            )
        finally:
            conn_admin.close()
        assert fila["bid_floor"] == "0.5000"
        assert fila["bid_ceiling"] == "2.5000"


@_skip_db
def test_harvest_all_or_nothing_terna_o_nada():
    """DoD: la terna harvest es all-or-nothing (CHECK goal_harvest_completo,
    pre-validado con mensaje claro ANTES del UPDATE):
    - solo harvest_campaign_id sobre terna NULL -> GoalInvalido;
    - la terna COMPLETA pasa y queda visible en la fila;
    - harvest_limpia deja los TRES en NULL;
    - harvest_limpia JAMAS se combina con campos harvest individuales
      (contradiccion de uso, rechazada con mensaje).
    (El UPDATE directo que viola el CHECK ya lo cubre test_schema: no se
    duplica aqui.)"""
    with _db_con_rol_admin("orbit_g_harv") as (conn, dsn_admin, _dsn_l):
        goal_id = _siembra_goal_plataforma(conn)  # terna NULL
        conn_admin = psycopg.connect(dsn_admin)
        try:
            with pytest.raises(goals_write.GoalInvalido, match="harvest"):
                goals_write.edita_goal(
                    conn_admin, goal_id, harvest_campaign_id="9002", updated_at=T_EDITADO
                )
            fila = goals_write.edita_goal(
                conn_admin,
                goal_id,
                harvest_campaign_id="9002",
                harvest_ad_group_id="9102",
                harvest_default_bid=Decimal("1.00"),
                updated_at=T_EDITADO,
            )
        finally:
            conn_admin.close()

        assert fila["harvest_campaign_id"] == "9002"
        assert fila["harvest_ad_group_id"] == "9102"
        assert fila["harvest_default_bid"] == "1.0000"
        en_db = conn.execute(
            "SELECT harvest_campaign_id, harvest_ad_group_id, harvest_default_bid"
            " FROM ads_optimizer_goal WHERE id = %s",
            (goal_id,),
        ).fetchone()
        assert en_db == ("9002", "9102", Decimal("1.0000"))

        # reemplazo parcial VALIDO: la terna existente completa lo que falta
        conn_admin = psycopg.connect(dsn_admin)
        try:
            fila = goals_write.edita_goal(
                conn_admin, goal_id, harvest_campaign_id="9003", updated_at=T_EDITADO
            )
        finally:
            conn_admin.close()
        assert fila["harvest_campaign_id"] == "9003"

        # limpieza: los TRES a NULL
        conn_admin = psycopg.connect(dsn_admin)
        try:
            fila = goals_write.edita_goal(
                conn_admin, goal_id, harvest_limpia=True, updated_at=T_EDITADO
            )
            with pytest.raises(goals_write.GoalInvalido, match="harvest_limpia"):
                goals_write.edita_goal(
                    conn_admin,
                    goal_id,
                    harvest_limpia=True,
                    harvest_campaign_id="9002",
                    updated_at=T_EDITADO,
                )
        finally:
            conn_admin.close()
        assert fila["harvest_campaign_id"] is None
        assert fila["harvest_ad_group_id"] is None
        assert fila["harvest_default_bid"] is None
        en_db = conn.execute(
            "SELECT harvest_campaign_id, harvest_ad_group_id, harvest_default_bid"
            " FROM ads_optimizer_goal WHERE id = %s",
            (goal_id,),
        ).fetchone()
        assert en_db == (None, None, None)


@_skip_db
def test_edicion_visible_al_ciclo_siguiente_con_rastro():
    """DoD: siembro el goal de plataforma con target A (25), EDITO a B (20)
    por el camino unico y corro UN ciclo real: la decision del motor lleva
    inputs.target_acos_pct_usado == B y congela el goal con B — la fuente del
    ciclo (_SQL_GOALS de app/cycle) lee la fila EDITADA (regla 2). Con B=20 la
    banda -25% sigue disparando (ACoS 36% > 1.35x20), asi que la decision bid
    existe y su unico cambio es el target."""
    with _db_con_rol_admin("orbit_g_cycle") as (conn, dsn_admin, _dsn_l):
        ids = _siembra_maestra(conn)  # goal de plataforma target 25, escalera shadow
        goal_id = conn.execute(
            "SELECT id FROM ads_optimizer_goal WHERE scope = 'platform'"
        ).fetchone()[0]
        conn_admin = psycopg.connect(dsn_admin)
        try:
            fila = goals_write.edita_goal(
                conn_admin, goal_id, target_acos_pct=Decimal("20"), updated_at=T_EDITADO
            )
        finally:
            conn_admin.close()
        assert fila["target_acos_pct"] == "20.00"

        from app import cycle as ciclo

        res = ciclo.corre_ciclo(
            conn,
            platform="amazon_us",
            owner="test-goals:1",
            decided_at=DECIDED_AT,
            heartbeat_cada=1,
        )
        assert res.status == "done", res.notes

        inputs = conn.execute(
            "SELECT inputs FROM decision WHERE cycle_id = %s AND ad_entity_id = %s"
            " AND kind = 'bid'",
            (res.cycle_id, ids["kw_bid"]),
        ).fetchone()[0]
        assert inputs["target_acos_pct_usado"] == "20.00", (
            "la decision del ciclo POST-edicion congela el target EDITADO, no el sembrado"
        )
        assert inputs["goal"]["target_acos_pct"] == "20.00"
        assert inputs["motivo"] == "banda_menos_25"  # la banda sigue disparando con B


@_skip_db
def test_endpoint_pg_happy_path_con_harvest(tmp_path, monkeypatch):
    """Integracion del endpoint contra PG16 real con el ROL admin (como
    produccion): 200, la fila actualizada en el shape de GET /goals y la
    terna harvest visible; updated_at en la respuesta == el instante del
    servidor (isoformat)."""
    with _db_con_rol_admin("orbit_g_ep") as (conn, dsn_admin, _dsn_l):
        goal_id = _siembra_goal_plataforma(conn)
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)

        resp = TestClient(app).post(
            f"/api/ads-optimizer/goals/{goal_id}",
            json={
                "target_acos_pct": "18",
                "enabled": True,
                "harvest_campaign_id": "9002",
                "harvest_ad_group_id": "9102",
                "harvest_default_bid": "0.75",
            },
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert cuerpo["id"] == goal_id
        assert cuerpo["target_acos_pct"] == "18.00"
        assert cuerpo["harvest_default_bid"] == "0.7500"
        assert dt.datetime.fromisoformat(cuerpo["updated_at"]) > T_SEMBRADO

        en_db = conn.execute(
            "SELECT target_acos_pct, harvest_campaign_id FROM ads_optimizer_goal WHERE id = %s",
            (goal_id,),
        ).fetchone()
        assert en_db == (Decimal("18.00"), "9002")


@_skip_db
def test_endpoint_pg_harvest_incompleto_422_sin_tocar_la_fila(tmp_path, monkeypatch):
    """422 con motivo y la fila queda INTACTA (la pre-validacion corre ANTES
    del UPDATE): ni target ni updated_at se movieron."""
    with _db_con_rol_admin("orbit_g_ep422") as (conn, dsn_admin, _dsn_l):
        goal_id = _siembra_goal_plataforma(conn)
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)

        resp = TestClient(app).post(
            f"/api/ads-optimizer/goals/{goal_id}",
            json={"target_acos_pct": "18", "harvest_campaign_id": "9002"},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422
        assert "harvest" in resp.json()["detail"]
        en_db = conn.execute(
            "SELECT target_acos_pct, updated_at FROM ads_optimizer_goal WHERE id = %s",
            (goal_id,),
        ).fetchone()
        assert en_db == (Decimal("25"), T_SEMBRADO)


@_skip_db
def test_endpoint_pg_body_vacio_422_sin_re_sellar_la_fila(tmp_path, monkeypatch):
    """#2 (regresion review 3.2, edita_goal REAL sin mock): body {} NO es
    edicion — 422 'edicion vacia' (antes re-sellaba la fila con 200: un
    updated_at movido sin ningun cambio real). La fila queda intacta."""
    with _db_con_rol_admin("orbit_g_vacio") as (conn, dsn_admin, _dsn_l):
        goal_id = _siembra_goal_plataforma(conn)
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)

        resp = TestClient(app).post(
            f"/api/ads-optimizer/goals/{goal_id}", json={}, headers={"x-orbit-token": TOKEN}
        )
        assert resp.status_code == 422, resp.text
        assert "edicion vacia" in resp.json()["detail"]
        en_db = conn.execute(
            "SELECT target_acos_pct, updated_at FROM ads_optimizer_goal WHERE id = %s",
            (goal_id,),
        ).fetchone()
        assert en_db == (Decimal("25"), T_SEMBRADO), "un body vacio no mueve NADA de la fila"


@_skip_db
def test_endpoint_pg_harvest_whitespace_422_sin_tocar_la_fila(tmp_path, monkeypatch):
    """#1 (cadena de espacios, edita_goal REAL): pydantic la deja pasar
    (min_length=1); la rechaza el camino unico antes del UPDATE — la fila
    queda intacta."""
    with _db_con_rol_admin("orbit_g_ws") as (conn, dsn_admin, _dsn_l):
        goal_id = _siembra_goal_plataforma(conn)
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)

        resp = TestClient(app).post(
            f"/api/ads-optimizer/goals/{goal_id}",
            json={"harvest_ad_group_id": "   "},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422, resp.text
        assert "harvest_ad_group_id" in resp.json()["detail"]
        en_db = conn.execute(
            "SELECT harvest_ad_group_id, updated_at FROM ads_optimizer_goal WHERE id = %s",
            (goal_id,),
        ).fetchone()
        assert en_db == (None, T_SEMBRADO)
