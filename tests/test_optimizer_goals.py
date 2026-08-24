"""Tests de goals y modo efectivo (`app.optimizer.goals`, task 2.4).

(a) UNITARIOS (siempre corren, sin DB): precedencia campana > plataforma
    INCLUIDO enabled (el opt-out del Spec delta: campana enabled=False con
    plataforma habilitada deja la entidad EXCLUIDA), cascada de target ACoS
    peldano por peldano (goal -> setting -> cache -> default 55), claves
    selladas por plataforma, floor/ceiling con defaults, meet del reticulo
    off < shadow < live, fail-closed PR1 (live degradado a shadow con nota),
    fail-closed de modo_desde_settings (sin clave o invalido -> off) y la
    guarda tz-aware de en_cooldown (sin tocar conn).
(b) INTEGRACION (patron _db_temporal de test_optimizer_windows, COPIADO):
    cooldown sobre una fixture sintetica de ad_entity + config_version +
    optimizer_cycle + decision (kind bid) + decision_application, con reloj
    FIJO tz-aware pasado por parametro (determinismo, sin now() escondido):
    * apply verificado (verify_ok TRUE) confirmado hace <7d -> EN cooldown;
    * divergencia (verify_ok FALSE) confirmada hace <7d -> NO enfria;
    * verificado hace EXACTAMENTE 7d -> NO enfria (comparador ESTRICTO);
    * entidad con decisiones shadow y SIN applies -> NO enfria (los ciclos
      shadow no escriben decision_application: propiedad del modulo).
(c) Guarda de sintaxis: la SQL del modulo parsea como Postgres real (pglast).
"""

from __future__ import annotations

import datetime as dt
import os
import re
import socket
from contextlib import contextmanager
from decimal import Decimal

import pglast
import pytest
from psycopg.types.json import Json
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app.optimizer import goals as g


def _goal(**sobreescribe) -> g.Goal:
    """Goal a mano con los defaults del esquema (NOT NULL DEFAULT en DB);
    scope='campaign' exige platform=None (CHECK goal_scope_coherente)."""
    valores = dict(
        scope="platform",
        ad_entity_id=None,
        platform="amazon_us",
        target_acos_pct=Decimal("25"),
        bid_floor=Decimal("0.10"),
        bid_ceiling=Decimal("2.50"),
        bid_currency="USD",
        harvest_campaign_id=None,
        harvest_ad_group_id=None,
        harvest_default_bid=None,
        enabled=True,
        mode="off",
    )
    valores.update(sobreescribe)
    return g.Goal(**valores)


# ---------------------------------------------------------------------------
# (a) UNITARIOS - precedencia campana > plataforma INCLUIDO enabled
# ---------------------------------------------------------------------------


def test_opt_out_campana_deshabilitada_pisa_plataforma_habilitada():
    """El opt-out del Spec delta: un goal de campana enabled=False PISA a la
    plataforma habilitada -> resuelve_goal devuelve EL DE CAMPANA y la entidad
    queda EXCLUIDA (elegible False). Que cayera al de plataforma seria una
    entidad 'rehabilitada' por la config que su dueno apago."""
    campana = _goal(scope="campaign", ad_entity_id=1, platform=None, enabled=False)
    plataforma = _goal(enabled=True)
    resuelto = g.resuelve_goal(campana, plataforma)
    assert resuelto is campana  # pisa SIEMPRE que existe, incluso deshabilitado
    assert g.elegible(resuelto) is False  # entidad EXCLUIDA


def test_campana_habilitada_manda_sobre_plataforma_deshabilitada():
    """Simetrico: la campana manda tambien para habilitar; su target viaja
    con ella (el goal resuelto es EL de campana, no un mix)."""
    campana = _goal(
        scope="campaign",
        ad_entity_id=1,
        platform=None,
        enabled=True,
        target_acos_pct=Decimal("18"),
    )
    plataforma = _goal(enabled=False)
    resuelto = g.resuelve_goal(campana, plataforma)
    assert resuelto is campana
    assert resuelto.target_acos_pct == Decimal("18")
    assert g.elegible(resuelto) is True


def test_sin_goal_campana_manda_el_de_plataforma():
    plataforma = _goal(enabled=True)
    resuelto = g.resuelve_goal(None, plataforma)
    assert resuelto is plataforma
    assert g.elegible(resuelto) is True


def test_sin_goals_ninguno_resuelve_ninguno_elegible():
    """Ningun goal -> None (3.1 excluye con su motivo); elegible(None) False."""
    assert g.resuelve_goal(None, None) is None
    assert g.elegible(None) is False


# ---------------------------------------------------------------------------
# Cascada de target ACoS: peldano por peldano (cada uno decide SOLO si el
# anterior es None; regla 3: faltante != valor)
# ---------------------------------------------------------------------------


def test_cascada_peldano_por_peldano_discriminando():
    """Con los TRES valores presentes manda el goal (25); goal None -> setting
    (30); goal y setting None -> cache (28); todos None -> default 55. Cada
    peldano es discriminado con los demas presentes."""
    assert g.cascada_target_acos(Decimal("25"), Decimal("30"), Decimal("28")) == Decimal("25")
    assert g.cascada_target_acos(None, Decimal("30"), Decimal("28")) == Decimal("30")
    assert g.cascada_target_acos(None, None, Decimal("28")) == Decimal("28")
    assert g.cascada_target_acos(None, None, None) == Decimal("55")
    assert g.cascada_target_acos(None, None, None) == g.DEFAULT_TARGET_PCT


def test_clave_target_plataforma_sellada():
    """Clave sellada en docs/DATABASE.md: ads_target_acos_pct_<platform>, con
    el nombre de plataforma DEL ENUM (amazon_us / amazon_mx), no 'us/mx'."""
    assert g.clave_target_plataforma("amazon_us") == "ads_target_acos_pct_amazon_us"
    assert g.clave_target_plataforma("amazon_mx") == "ads_target_acos_pct_amazon_mx"


def test_target_desde_settings_por_plataforma_y_sin_clave():
    """La clave es POR plataforma (us y mx son claves distintas); sin clave ->
    None (el default NO se aplica aqui: eso es cosa del ultimo peldano de la
    cascada). Un valor string del JSON humano se normaliza a Decimal exacto."""
    settings = {
        "ads_target_acos_pct_amazon_us": 30,
        "ads_target_acos_pct_amazon_mx": 40,
    }
    assert g.target_desde_settings(settings, "amazon_us") == Decimal("30")
    assert g.target_desde_settings(settings, "amazon_mx") == Decimal("40")
    assert g.target_desde_settings({"otra_cosa": 1}, "amazon_us") is None
    assert g.target_desde_settings({}, "amazon_us") is None
    assert g.target_desde_settings({"ads_target_acos_pct_amazon_us": "30"}, "amazon_us") == (
        Decimal("30")
    )


def test_target_invalido_es_config_corrupta_no_ausente():
    """Hallazgo codex+grok (ronda 1): el setting JSONB y el cache de
    ad_entity_state NO tienen candado de positividad (solo el goal lo tiene
    en DB). Un valor PRESENTE pero 0/negativo/NaN/no numerico es config
    CORRUPTA: ValueError ruidoso, jamas se camufla de ausente para caer al
    default 55 (decidiria con un target que nadie configuro)."""
    with pytest.raises(ValueError, match="debe ser > 0"):
        g.target_desde_settings({"ads_target_acos_pct_amazon_us": 0}, "amazon_us")
    with pytest.raises(ValueError, match="debe ser > 0"):
        g.target_desde_settings({"ads_target_acos_pct_amazon_us": -10}, "amazon_us")
    with pytest.raises(ValueError, match="debe ser > 0"):
        g.target_desde_settings({"ads_target_acos_pct_amazon_us": "nan"}, "amazon_us")
    with pytest.raises(ValueError, match="no numerico"):
        g.target_desde_settings({"ads_target_acos_pct_amazon_us": "abc"}, "amazon_us")
    # la cascada valida IGUAL cada peldano (goal lo cubre el CHECK en DB;
    # setting y cache no). re.escape: los nombres de peldano llevan puntos
    # (hallazgo CodeRabbit RUF043: sin escapar son comodin de regex).
    with pytest.raises(ValueError, match="setting ads_target_acos_pct"):
        g.cascada_target_acos(None, Decimal("0"), None)
    with pytest.raises(ValueError, match=re.escape("ad_entity_state.acos_target")):
        g.cascada_target_acos(None, None, Decimal("-5"))
    with pytest.raises(ValueError, match=re.escape("goal.target_acos_pct")):
        g.cascada_target_acos(Decimal("0"), Decimal("30"), Decimal("28"))
    # NaN de Decimal (is_finite False), mismo trato
    with pytest.raises(ValueError):
        g.cascada_target_acos(None, Decimal("NaN"), None)


# ---------------------------------------------------------------------------
# Floor/ceiling con defaults
# ---------------------------------------------------------------------------


def test_floor_ceiling_defaults_y_valores_dados():
    """En DB son NOT NULL DEFAULT 0.10/2.50; un None solo puede venir de un
    Goal construido a mano (capa pura) y se aplica el MISMO default. Valores
    dados se conservan tal cual (sin quantize: la presentacion es del apply)."""
    assert g.resuelve_floor_ceiling(_goal(bid_floor=None, bid_ceiling=None)) == (
        Decimal("0.10"),
        Decimal("2.50"),
    )
    assert g.resuelve_floor_ceiling(
        _goal(bid_floor=Decimal("0.80"), bid_ceiling=Decimal("1.90"))
    ) == (Decimal("0.80"), Decimal("1.90"))
    assert g.resuelve_floor_ceiling(None) == (Decimal("0.10"), Decimal("2.50"))


# ---------------------------------------------------------------------------
# Modo efectivo: meet del reticulo off < shadow < live
# ---------------------------------------------------------------------------


def test_modo_efectivo_meet_del_reticulo():
    """Infimo del reticulo: off con cualquier cosa -> off; shadow con live ->
    shadow (en ambos ordenes); live SOLO si ambos viven."""
    assert g.modo_efectivo("off", "live") == "off"
    assert g.modo_efectivo("live", "off") == "off"
    assert g.modo_efectivo("shadow", "live") == "shadow"
    assert g.modo_efectivo("live", "shadow") == "shadow"
    assert g.modo_efectivo("shadow", "shadow") == "shadow"
    assert g.modo_efectivo("live", "live") == "live"


def test_resuelve_modo_live_degradado_a_shadow_con_nota_pr1():
    """Fail-closed PR1: HAY_MODULO_APPLY es False, asi que un meet 'live'
    (escalera live + goal live) se degrada a 'shadow' con nota del vocabulario
    estable. Sin nota seria una degradacion invisible en el ciclo."""
    resuelto = g.resuelve_modo("live", "live")
    assert resuelto.modo == "shadow"
    assert resuelto.nota is not None
    assert resuelto.nota == g.NOTA_LIVE_DEGRADADO


def test_resuelve_modo_shadow_por_escalera_no_lleva_nota():
    """Un meet 'shadow' por la escalera (global shadow, goal live) NO es una
    degradacion de PR1: es el meet pedido. Nota None (ruido cero)."""
    assert g.resuelve_modo("shadow", "live") == g.ModoEfectivo(modo="shadow", nota=None)
    assert g.resuelve_modo("off", "live") == g.ModoEfectivo(modo="off", nota=None)


def test_modo_efectivo_valor_invalido_raise():
    """Valor fuera de {off, shadow, live} en CUALQUIERA de los dos -> ValueError
    ruidoso (las funciones puras no adivinan; la config corrupta no pasa)."""
    with pytest.raises(ValueError, match="vocabulario"):
        g.modo_efectivo("pausa", "live")
    with pytest.raises(ValueError, match="vocabulario"):
        g.modo_efectivo("live", "pausa")


def test_modo_desde_settings_fail_closed():
    """La escalera global vive en config_version.settings bajo la clave sellada
    ads_optimizer_mode: sin clave, valor invalido o NULL -> 'off' (fail-closed:
    una config corrupta JAMAS habilita live por accidente)."""
    assert g.modo_desde_settings({}) == "off"
    assert g.modo_desde_settings({"ads_optimizer_mode": "shadow"}) == "shadow"
    assert g.modo_desde_settings({"ads_optimizer_mode": "live"}) == "live"
    assert g.modo_desde_settings({"ads_optimizer_mode": "turbo"}) == "off"
    assert g.modo_desde_settings({"ads_optimizer_mode": None}) == "off"


# ---------------------------------------------------------------------------
# Cooldown: guarda tz-aware (unitario, sin conn)
# ---------------------------------------------------------------------------


def test_cooldown_ahora_naive_raise_sin_tocar_conn():
    """Un naive evaluaria segun la TZ local del proceso: se rechaza
    ruidosamente ANTES de tocar la conexion (conn=None lo demuestra)."""
    with pytest.raises(ValueError, match="tz-aware"):
        g.en_cooldown(None, 1, ahora=dt.datetime(2026, 8, 23, 12, 0))


# ---------------------------------------------------------------------------
# (c) GUARDA DE SINTAXIS - la SQL del modulo es Postgres valido
# ---------------------------------------------------------------------------


def test_sql_del_modulo_parsea_como_postgres():
    """Patron test_optimizer_windows/test_optimizer_hygiene: pglast es dev-dep
    declarada y su desaparicion debe FALLAR ruidosamente, no saltar."""
    for nombre in ("_SQL_EN_COOLDOWN",):
        sql = getattr(g, nombre).replace("%s", "NULL")
        assert pglast.parse_sql(sql), f"{nombre} no parseo"


# ---------------------------------------------------------------------------
# (b) INTEGRACION - patron _db_temporal COPIADO de test_optimizer_windows
# ---------------------------------------------------------------------------

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))

# Reloj FIJO tz-aware (determinismo): todo el seed se cuelga de AHORA.
AHORA = dt.datetime(2026, 8, 23, 12, 0, tzinfo=dt.UTC)
DECIDED_AT = AHORA - dt.timedelta(days=8)


@contextmanager
def _db_temporal(prefijo: str):
    """DB temporal con la migracion entera (el esquema sellado, sin toques)."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # la migracion entera
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _config_version(conn) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("test-goals", Json({"ads_optimizer_mode": "shadow"})),
    ).fetchone()[0]


def _ciclo(conn, mode: str = "live") -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform)"
        " VALUES ('ads_optimizer', %s, %s) RETURNING id",
        (mode, "amazon_us"),
    ).fetchone()[0]


def _campana(conn, external: str) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES (%s, 'campaign', %s) RETURNING id",
        ("amazon_us", external),
    ).fetchone()[0]


def _decision(conn, ciclo: int, config_id: int, entidad: int) -> int:
    """Decision kind 'bid' (lleva moneda por CHECK decision_valor_con_moneda);
    kind bid no dispara decision_madurez_corte (es ajuste, no corte)."""
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, old_value, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, %s, %s, 'USD', %s) RETURNING id",
        (
            ciclo,
            entidad,
            DECIDED_AT,
            config_id,
            DECIDED_AT - dt.timedelta(hours=1),
            dt.date(2026, 7, 25),
            dt.date(2026, 8, 12),
            Decimal("1.00"),
            Decimal("0.75"),
            Json({"target_acos_pct": "25"}),
        ),
    ).fetchone()[0]


def _apply(conn, decision_id: int, *, confirmed_at: dt.datetime, verify_ok: bool) -> None:
    """Terna del esquema sellada JUNTA en el readback: confirmed_at +
    platform_ack + verify_ok (CHECKs application_confirmacion_con_*)."""
    conn.execute(
        "INSERT INTO decision_application (decision_id, attempted_at, confirmed_at,"
        " verify_ok, platform_ack) VALUES (%s, %s, %s, %s, %s)",
        (
            decision_id,
            confirmed_at - dt.timedelta(minutes=5),
            confirmed_at,
            verify_ok,
            Json({"estado": "ok" if verify_ok else "divergente"}),
        ),
    )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_cooldown_en_vivo_verificado_divergencia_y_borde_7d():
    """Cooldown contra la DB real: solo enfria un apply con verify_ok IS TRUE
    de un ciclo LIVE confirmado hace <7d. La divergencia (FALSE, se
    reintenta) NO enfría y el borde EXACTO de 7d tampoco (comparador ESTRICTO:
    confirmed_at > ahora-7d). La unidad es la ENTIDAD: cualquier decision
    suya con apply verificado."""
    with _db_temporal("orbit_goals_cooldown") as conn:
        config_id = _config_version(conn)
        ciclo = _ciclo(conn)  # live (default del helper)

        e_verificada = _campana(conn, "7001")
        d1 = _decision(conn, ciclo, config_id, e_verificada)
        _apply(conn, d1, confirmed_at=AHORA - dt.timedelta(days=6), verify_ok=True)

        e_divergente = _campana(conn, "7002")
        d2 = _decision(conn, ciclo, config_id, e_divergente)
        _apply(conn, d2, confirmed_at=AHORA - dt.timedelta(days=6), verify_ok=False)

        e_borde = _campana(conn, "7003")
        d3 = _decision(conn, ciclo, config_id, e_borde)
        _apply(conn, d3, confirmed_at=AHORA - dt.timedelta(days=7), verify_ok=True)

        assert g.en_cooldown(conn, e_verificada, ahora=AHORA) is True
        assert g.en_cooldown(conn, e_divergente, ahora=AHORA) is False
        assert g.en_cooldown(conn, e_borde, ahora=AHORA) is False


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_cooldown_apply_de_ciclo_shadow_no_enfria():
    """Hallazgo codex+grok (cross-review ronda 1), regla sellada del diseno
    v2 ('solo cuenta mode=live AND verify_ok<>0'): un apply VERIFICADO hace
    <7d sobre una decision cuyo ciclo fue SHADOW NO enfria. Sin el JOIN a
    optimizer_cycle, un dry-run de PR2 que escriba decision_application en
    shadow enfriaria entidades que nunca se tocaron en vivo (contra el test
    anterior, este FALLA contra el SQL sin el filtro)."""
    with _db_temporal("orbit_goals_shadow_apply") as conn:
        config_id = _config_version(conn)
        ciclo = _ciclo(conn, mode="shadow")
        entidad = _campana(conn, "7005")
        d = _decision(conn, ciclo, config_id, entidad)
        _apply(conn, d, confirmed_at=AHORA - dt.timedelta(days=6), verify_ok=True)
        assert g.en_cooldown(conn, entidad, ahora=AHORA) is False


# NOTA (hallazgo CodeRabbit): hubo un test 'cooldown_ciclo_shadow_sin_applies'
# que sembraba decisiones shadow SIN ninguna fila en decision_application --
# EXISTS devolvia false por construccion y el test pasaba igual con el SQL
# roto (sin JOIN a optimizer_cycle, sin verify_ok, sin umbral). Se elimino:
# un test que no discrimina es peor que la ausencia. El caso shadow con
# poder discriminatorio lo cubre test_cooldown_apply_de_ciclo_shadow_no_enfria
# (rojo demostrado contra el SQL sin el filtro), y la propiedad "los ciclos
# shadow no escriben decision_application en PR1" vive en el docstring de
# goals.py (en_cooldown).


# ---------------------------------------------------------------------------
# (d) CASCADA CON PROCEDENCIA (ORBIT 16 - DASHBOARD 01, task 1.2): variante
#     valor+peldaño con los CINCO peldaños, compatible con el camino del motor
# ---------------------------------------------------------------------------


def _settings_target(us=None, mx=None) -> dict:
    """settings de config_version solo con claves de target (la clave sale de
    clave_target_plataforma, NUNCA una clave corta inventada)."""
    settings = {}
    if us is not None:
        settings["ads_target_acos_pct_amazon_us"] = us
    if mx is not None:
        settings["ads_target_acos_pct_amazon_mx"] = mx
    return settings


def test_peldanos_vocabulario_exacto():
    """El vocabulario de los CINCO peldaños es EXACTO y sellado: el dashboard
    lo muestra tal cual y la variante lo reporta; un nombre distinto aqui
    rompe el contrato de 1.4."""
    assert g.PELDANOS_CASCADA == (
        "goal_campana",
        "goal_plataforma",
        "setting_plataforma",
        "cache_estado",
        "default",
    )


def test_peldano_goal_campana_gana_y_reporta_su_nombre():
    """Peldaño 1: el goal de campaña gana con TODO lo demás presente y reporta
    su nombre exacto."""
    campana = _goal(scope="campaign", ad_entity_id=1, platform=None, target_acos_pct=Decimal("18"))
    plataforma = _goal(target_acos_pct=Decimal("25"))
    settings = _settings_target(us=30)
    valor, peldano = g.cascada_target_acos_con_procedencia(
        campana, plataforma, settings, Decimal("28"), "amazon_us"
    )
    assert valor == Decimal("18")
    assert peldano == "goal_campana"


def test_peldano_goal_plataforma_gana_y_reporta_su_nombre():
    """Peldaño 2: sin goal de campaña, el de plataforma gana (y reporta su
    nombre), pisando setting/cache."""
    plataforma = _goal(target_acos_pct=Decimal("25"))
    settings = _settings_target(us=30)
    valor, peldano = g.cascada_target_acos_con_procedencia(
        None, plataforma, settings, Decimal("28"), "amazon_us"
    )
    assert valor == Decimal("25")
    assert peldano == "goal_plataforma"


def test_peldano_setting_plataforma_gana_y_reporta_su_nombre():
    """Peldaño 3: sin goals, el setting de config gana. La clave es POR
    plataforma (clave_target_plataforma): amazon_us y amazon_mx leen la suya."""
    settings = _settings_target(us=30, mx=40)
    valor, peldano = g.cascada_target_acos_con_procedencia(
        None, None, settings, Decimal("28"), "amazon_us"
    )
    assert valor == Decimal("30")
    assert peldano == "setting_plataforma"
    valor_mx, peldano_mx = g.cascada_target_acos_con_procedencia(
        None, None, settings, Decimal("28"), "amazon_mx"
    )
    assert valor_mx == Decimal("40")
    assert peldano_mx == "setting_plataforma"


def test_peldano_cache_estado_gana_y_reporta_su_nombre():
    """Peldaño 4: sin goals ni setting, el cache de ad_entity_state gana."""
    valor, peldano = g.cascada_target_acos_con_procedencia(
        None, None, {}, Decimal("28"), "amazon_us"
    )
    assert valor == Decimal("28")
    assert peldano == "cache_estado"


def test_peldano_default_gana_y_reporta_su_nombre():
    """Peldaño 5: todo None -> default 55 y reporta 'default'."""
    valor, peldano = g.cascada_target_acos_con_procedencia(None, None, {}, None, "amazon_us")
    assert valor == Decimal("55")
    assert peldano == "default"
    assert valor == g.DEFAULT_TARGET_PCT


def test_procedencia_compatible_con_el_motor_campana_con_target_none():
    """Compatibilidad con el camino del motor (cero cambio de comportamiento):
    el motor resuelve con resuelve_goal (la campaña PISA SIEMPRE que existe) y
    la cascada vieja salta el target None del goal resuelto al SETTING. Un goal
    de campaña con target None NO puede caer al target del goal de plataforma:
    el motor jamás lo usaría (resuelto=goal_campana, su target None -> setting).
    Si el dashboard reportara 'goal_plataforma' ahí, mentiría sobre lo que el
    motor decidió (regla 2: un número, una fuente)."""
    campana = _goal(scope="campaign", ad_entity_id=1, platform=None, target_acos_pct=None)
    plataforma = _goal(target_acos_pct=Decimal("25"))
    settings = _settings_target(us=30)
    valor, peldano = g.cascada_target_acos_con_procedencia(
        campana, plataforma, settings, Decimal("28"), "amazon_us"
    )
    assert valor == Decimal("30")  # el 25 de la plataforma NO gana
    assert peldano == "setting_plataforma"
    # sin setting: cae al cache, tampoco al goal de plataforma
    valor2, peldano2 = g.cascada_target_acos_con_procedencia(
        campana, plataforma, {}, Decimal("28"), "amazon_us"
    )
    assert valor2 == Decimal("28")
    assert peldano2 == "cache_estado"


def test_procedencia_equivale_a_la_cascada_del_motor():
    """Mismo valor que la cascada vieja en los casos discriminados (regla 2):
    la variante con procedencia es una REFINACION de la cascada del motor, no
    otra aritmética. El motor ya resolvió el goal (resuelve_goal), así que el
    par (goal_campana, goal_plataforma) se compara contra
    cascada_target_acos(resuelto.target, setting, cache)."""
    casos = [
        (None, None, {}, None, "amazon_us"),
        (None, None, {"ads_target_acos_pct_amazon_us": 30}, None, "amazon_us"),
        (None, None, {}, Decimal("28"), "amazon_us"),
        (None, _goal(target_acos_pct=Decimal("25")), {}, None, "amazon_us"),
        (
            _goal(scope="campaign", ad_entity_id=1, platform=None, target_acos_pct=Decimal("18")),
            _goal(target_acos_pct=Decimal("25")),
            {"ads_target_acos_pct_amazon_us": 30},
            Decimal("28"),
            "amazon_us",
        ),
        (
            _goal(scope="campaign", ad_entity_id=1, platform=None, target_acos_pct=None),
            _goal(target_acos_pct=Decimal("25")),
            {"ads_target_acos_pct_amazon_us": 30},
            Decimal("28"),
            "amazon_us",
        ),
    ]
    for goal_campana, goal_plataforma, settings, cache, platform in casos:
        resuelto = g.resuelve_goal(goal_campana, goal_plataforma)
        # el motor normaliza el setting con target_desde_settings ANTES de la
        # cascada vieja (cycle.py): la comparacion usa la MISMA fuente
        esperado = g.cascada_target_acos(
            resuelto.target_acos_pct if resuelto is not None else None,
            g.target_desde_settings(settings, platform),
            cache,
        )
        valor, _peldano = g.cascada_target_acos_con_procedencia(
            goal_campana, goal_plataforma, settings, cache, platform
        )
        assert valor == esperado


def test_procedencia_peldano_invalido_revienta_no_cae_al_siguiente():
    """Regla 3 (mismo criterio que la cascada vieja): un peldaño PRESENTE pero
    invalido (<= 0, no finito) revienta ruidosamente, jamas cae al siguiente.
    Los nombres de peldaño llevan guion bajo (no puntos): re.escape evita el
    comodin de regex (patron del test de la cascada vieja)."""
    campana_malo = _goal(
        scope="campaign", ad_entity_id=1, platform=None, target_acos_pct=Decimal("0")
    )
    with pytest.raises(ValueError, match=re.escape("goal_campana")):
        g.cascada_target_acos_con_procedencia(campana_malo, None, {}, None, "amazon_us")
    plataforma_mala = _goal(target_acos_pct=Decimal("-5"))
    with pytest.raises(ValueError, match=re.escape("goal_plataforma")):
        g.cascada_target_acos_con_procedencia(None, plataforma_mala, {}, None, "amazon_us")
    with pytest.raises(ValueError, match=re.escape("cache_estado")):
        g.cascada_target_acos_con_procedencia(None, None, {}, Decimal("-5"), "amazon_us")
    # setting corrupto: target_desde_settings (REUTILIZADA, no reimplementada)
    # revienta con la clave sellada en el mensaje
    with pytest.raises(ValueError, match="ads_target_acos_pct_amazon_us"):
        g.cascada_target_acos_con_procedencia(
            None, None, {"ads_target_acos_pct_amazon_us": 0}, None, "amazon_us"
        )
