"""Tests de hygiene puro (`app.optimizer.hygiene`, task 2.3).

(a) UNITARIOS (siempre corren, sin DB): decide_hygiene sobre TerminosCortes
    construidos a mano (entidad con >=7 fechas => completa, unidad sellada
    2.1). Bordes EXACTOS por mercado (clicks 20 y cost 8 USD / 130 MXN
    inclusivos), ASIN-like SIEMPRE skip en ambos kinds, harvest sin config /
    duplicado / moneda incoherente, ACoS <= min(35, target) INCLUSIVO por
    multiplicacion exacta, semantica de None (orders None jamas negative),
    completitud de la ENTIDAD (6 fechas paraliza todo; el termino no exige
    fechas propias), ventanas y data_observed_at por camino.
(b) INTEGRACION (patron _db_temporal de test_optimizer_windows, COPIADO):
    * desempate por source_report_id en el colapso de terminos (minor de la
      review 2.1): dos observaciones del mismo (entidad, termino, fecha) con
      el MISMO observed_at y reportes distintos deben desempatar por el
      source_report_id MAYOR. Bajo el esquema sellado el empate es imposible
      (la PK incluye observed_at y el trigger st_metric_moneda_sellada clava
      la plataforma de la fila a la de su entidad), asi que el test RELAJA LA
      PK en su DB temporal para sembrarlo: el query de windows no puede
      depender de ese invariante afuera de el (defensa en profundidad).
    * keywords_campana_destino: keywords EXACT de la campana destino via
      ad_entity (una PHRASE con el mismo texto NO es duplicado; decision
      sellada: el harvest crea keyword EXACT).
(c) Guarda de sintaxis: la SQL del modulo parsea como Postgres real (pglast).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal

import pglast
import pytest
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app.optimizer import hygiene as h
from app.optimizer import windows as w

# ---------------------------------------------------------------------------
# Reloj/ventanas FIJAS (mismas fechas que test_optimizer_windows: la ventana
# madura de cortes termina 08-12; observed_at por termino para discriminar el
# camino que decidio cada resultado).
# ---------------------------------------------------------------------------

INICIO_CORTES = dt.date(2026, 7, 14)
FIN_CORTES = dt.date(2026, 8, 12)
DECIDED_AT = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
OBS_NEG = dt.datetime(2026, 8, 18, 4, 0, tzinfo=dt.UTC)
OBS_HARV = dt.datetime(2026, 8, 19, 5, 30, tzinfo=dt.UTC)

_DIA = dt.timedelta(days=1)


def _term(
    search_term: str = "zapato rojo",
    *,
    cost=None,
    ad_revenue=None,
    clicks=None,
    orders=None,
    moneda: str | None = "USD",
    asin: bool = False,
    observed: dt.datetime = OBS_NEG,
    fechas: int = 3,
) -> w.AgregadoTermino:
    """AgregadoTermino a mano: sin umbral de fechas propio (unidad sellada:
    la completitud la aporta la entidad; el termino lleva SUS fechas como
    evidencia, ej. fechas=3 para el long-tail)."""
    return w.AgregadoTermino(
        ad_entity_id=1,
        search_term=search_term,
        metric_currency=moneda,
        cost=cost,
        ad_revenue=ad_revenue,
        clicks=clicks,
        orders=orders,
        fechas_distintas=fechas,
        is_asin_like=asin,
        observed_at_max=observed,
    )


def _terminos(terminos, n_fechas: int = 7) -> w.TerminosCortes:
    """TerminosCortes a mano: n_fechas>=7 => entidad completa (unidad sellada)."""
    return w.TerminosCortes(
        ad_entity_id=1,
        window_start=INICIO_CORTES,
        window_end=FIN_CORTES,
        fechas_entidad=tuple(INICIO_CORTES + _DIA * i for i in range(n_fechas)),
        terminos=tuple(terminos),
    )


CONFIG = h.ConfigHarvest(
    campaign_id="9002",
    ad_group_id="9102",
    default_bid=Decimal("0.75"),
    moneda="USD",
)


def _decide(
    terminos: w.TerminosCortes,
    *,
    platform: str = "amazon_us",
    target: str = "25",
    config: h.ConfigHarvest | None = CONFIG,
    keywords: frozenset[str] = frozenset(),
    piso: Decimal | None = None,
) -> dict[str, h.ResultadoTermino]:
    """decide_hygiene + indexado por search_term (tests de un vistazo).
    `piso` (CORTES 01 1.4) solo viaja cuando NO es None: el default del motor
    es el legacy de cost por plataforma (8/130), igual que umbral_negative."""
    extra: dict = {} if piso is None else {"piso_negative": piso}
    resultados = h.decide_hygiene(
        platform=platform,
        terminos=terminos,
        target_acos_pct=Decimal(target),
        config_harvest=config,
        keywords_existentes=keywords,
        **extra,
    )
    return {r.search_term: r for r in resultados}


def _negativo(**sobreescribe) -> w.AgregadoTermino:
    """Termino que cumple los umbrales NEGATIVE de amazon_us exacto; los
    bordes se prueban SOBREESCRIBIENDO el valor justo (19, 7.99, 130...)."""
    valores = dict(cost=Decimal("8.00"), clicks=20, orders=0)
    valores.update(sobreescribe)
    return _term(**valores)


def _harvesteable(**sobreescribe) -> w.AgregadoTermino:
    """Termino que cumple HARVEST con target 25 (ACoS 10% << 25%)."""
    valores = dict(cost=Decimal("10"), ad_revenue=Decimal("100"), clicks=5, orders=2)
    valores.update(sobreescribe)
    return _term(**valores)


# ---------------------------------------------------------------------------
# (a) UNITARIOS - NEGATIVE_EXACT: bordes exactos por mercado
# ---------------------------------------------------------------------------


def test_negative_borde_exacto_us_clicks_20_cost_8():
    """Bordes inclusivos exactos en amazon_us: clicks=20 y cost=8.00 USD
    negativizan (>=). kind 'negative' no lleva dinero (esquema sellado:
    value_currency/new_value NULL). La ventana es la de cortes de
    TerminosCortes y data_observed_at es el observed_at_max DEL TERMINO."""
    r = _decide(_terminos([_negativo()]))["zapato rojo"]
    assert r.kind == "negative"
    assert r.motivo == "negative_umbral"
    assert r.new_value is None
    assert r.value_currency is None
    assert (r.window_start, r.window_end) == (INICIO_CORTES, FIN_CORTES)
    assert r.data_observed_at == OBS_NEG


def test_negative_clicks_19_no():
    """clicks=19 (umbral 20 inclusivo) no negativiza aunque el cost sobre."""
    r = _decide(_terminos([_negativo(clicks=19)]))["zapato rojo"]
    assert r.kind is None
    assert r.motivo == "sin_umbral_negative"


def test_negative_cost_7_99_no_us():
    """cost=7.99 USD con clicks=20: un centavo bajo el umbral -> no-op."""
    r = _decide(_terminos([_negativo(cost=Decimal("7.99"))]))["zapato rojo"]
    assert r.kind is None
    assert r.motivo == "sin_umbral_negative"


def test_piso_negative_por_parametro_bloquea():
    """CORTES 01 1.4: el piso de cost del camino negative llega RESUELTO por
    parametro -- piso 19.40 contra un termino de cost 15 con clicks 25 (>=
    umbral 20) -> no-op 'sin_umbral_negative'. SIN piso, el MISMO termino
    dispara negative (15 >= legacy 8). Regla 9: si el motor ignorara el
    parametro, la primera mitad de este test reventaria."""
    r = _decide(_terminos([_negativo(clicks=25, cost=Decimal("15"))]), piso=Decimal("19.40"))
    assert r["zapato rojo"].kind is None
    assert r["zapato rojo"].motivo == "sin_umbral_negative"
    r_sin_piso = _decide(_terminos([_negativo(clicks=25, cost=Decimal("15"))]))
    assert r_sin_piso["zapato rojo"].kind == "negative"


def test_piso_negative_borde_inclusivo():
    """Borde inclusivo del piso (>=, igual que el umbral legacy): cost ==
    piso EXACTO (19.40) dispara negative."""
    r = _decide(_terminos([_negativo(clicks=25, cost=Decimal("19.40"))]), piso=Decimal("19.40"))
    assert r["zapato rojo"].kind == "negative"
    assert r["zapato rojo"].motivo == "negative_umbral"


def test_negative_borde_exacto_mx_cost_130():
    """Cost justo 130 MXN en amazon_mx negativiza (umbral por plataforma)."""
    r = _decide(
        _terminos([_negativo(cost=Decimal("130.00"), moneda="MXN")]),
        platform="amazon_mx",
        config=h.ConfigHarvest("9", "9", Decimal("20"), "MXN"),
    )["zapato rojo"]
    assert r.kind == "negative"
    assert r.value_currency is None


# ---------------------------------------------------------------------------
# ASIN-like: SIEMPRE skip, para AMBOS kinds (decision sellada del lead)
# ---------------------------------------------------------------------------


def test_asin_like_skip_en_negative_y_en_harvest():
    """Regla own-ASIN del diseno v2 + keyword ASIN no harvesteable: el mismo
    termino ASIN-like se salta aunque cumpla umbrales de negative O de
    harvest. El motivo es el MISMO para ambos caminos."""
    r = _decide(
        _terminos(
            [
                _negativo(search_term="b0abc12xyz", asin=True),
                _harvesteable(search_term="b0zzz99yyy", asin=True),
            ]
        )
    )
    assert r["b0abc12xyz"].kind is None
    assert r["b0abc12xyz"].motivo == "asin_like"
    assert r["b0zzz99yyy"].kind is None
    assert r["b0zzz99yyy"].motivo == "asin_like"


# ---------------------------------------------------------------------------
# HARVEST: config, dedupe, ACoS inclusivo, moneda
# ---------------------------------------------------------------------------


def test_harvest_sin_config_none_y_parciales():
    """Config incompleta -> skip CON MOTIVO auditable, jamas placeholder
    (el CHECK goal_harvest_completo lo impide en DB; aqui es la capa pura).
    Parciales: None, solo campaign_id, default_bid=0, moneda vacia."""
    for config in (
        None,
        h.ConfigHarvest("9002", None, Decimal("0.75"), "USD"),
        h.ConfigHarvest("9002", "9102", Decimal("0"), "USD"),
        h.ConfigHarvest("9002", "9102", Decimal("0.75"), ""),
    ):
        r = _decide(_terminos([_harvesteable()]), config=config)["zapato rojo"]
        assert r.kind is None
        assert r.motivo == "harvest_sin_config"


def test_harvest_moneda_de_config_incoherente_con_plataforma():
    """Config COMPLETA pero con moneda cruzada (MXN en amazon_us) -> skip
    'harvest_moneda_incoherente', sin new_value: harvest es la unica salida
    de hygiene que crea dinero en PR2 y el esquema NO sella
    goal.bid_currency contra platform (hallazgo review 2.3, regla 4: un goal
    sembrado a mano con moneda cruzada fluye hasta un bid real)."""
    r = _decide(
        _terminos([_harvesteable()]),
        config=h.ConfigHarvest("9002", "9102", Decimal("0.75"), "MXN"),
    )["zapato rojo"]
    assert r.kind is None
    assert r.motivo == "harvest_moneda_incoherente"
    assert r.new_value is None
    assert r.value_currency is None


def test_harvest_duplicado():
    """El termino ya existe como keyword en la campana destino -> skip (el
    dedupe contra keywords_existentes, que 3.1 llena via
    keywords_campana_destino)."""
    r = _decide(_terminos([_harvesteable()]), keywords=frozenset({"zapato rojo"}))
    assert r["zapato rojo"].kind is None
    assert r["zapato rojo"].motivo == "harvest_duplicado"


def test_harvest_duplicado_normalizado_strip_y_casefold():
    """Hallazgo grok (ronda 1): keyword_text se guarda tal cual llega de la
    API (espacios extremos posibles) y la capitalizacion de searchTerm vs
    keywordText no esta garantizada igual. El dedupe compara NORMALIZADO
    (strip + casefold): ante divergencia de forma, skip -- evitar un
    duplicado en la campana destino manda sobre cosechar."""
    r = _decide(
        _terminos([_harvesteable()]),
        keywords=frozenset({"  ZAPATO ROJO  "}),
    )
    assert r["zapato rojo"].kind is None
    assert r["zapato rojo"].motivo == "harvest_duplicado"


def test_harvest_duplicado_intra_llamada_mismo_texto_normalizado():
    """Hallazgo qwen (ronda 3, media): dos terminos de la MISMA entidad que
    difieren solo en casing/espacios ("Yoga Mat" vs "yoga mat ") normalizan
    al mismo texto -> solo el PRIMERO se harvestea; el segundo es
    'harvest_duplicado' contra lo que ESTA llamada ya emitio (en PR2 serian
    dos POST de la misma keyword EXACT)."""
    terminos = _terminos(
        [
            _harvesteable(search_term="Yoga Mat"),
            _harvesteable(search_term="  yoga mat "),
        ]
    )
    resultados = h.decide_hygiene(
        platform="amazon_us",
        terminos=terminos,
        target_acos_pct=Decimal("25"),
        config_harvest=CONFIG,
        keywords_existentes=frozenset(),
    )
    assert resultados[0].search_term == "Yoga Mat"
    assert resultados[0].kind == "harvest"
    assert resultados[1].search_term == "  yoga mat "
    assert resultados[1].kind is None
    assert resultados[1].motivo == "harvest_duplicado"


def test_keywords_campana_destino_plataforma_invalida_value_error():
    """Hallazgo qwen (ronda 3, baja): la validacion de plataforma es TEMPRANA
    igual que en las funciones hermanas -- ValueError claro ANTES de tocar la
    DB (conn=None lo demuestra: nunca se usa)."""
    with pytest.raises(ValueError, match="vocabulario sellado"):
        h.keywords_campana_destino(None, "shopify", "9002")


def test_negative_moneda_incoherente_skip():
    """Hallazgo grok (ronda 1): el umbral de cost del negative esta en la
    moneda de la plataforma; la capa pura no puede confiar solo en el
    trigger de DB. Termino con metric_currency cruzada -> skip, jamas
    negative (misma defensa que bid/harvest)."""
    r = _decide(_terminos([_negativo(moneda="MXN")]))
    assert r["zapato rojo"].kind is None
    assert r["zapato rojo"].motivo == "moneda_incoherente"


def test_target_acos_invalido_value_error():
    """Misma guarda que bid (hallazgo codex+grok ronda 1): un target <= 0 o
    no finito corrompe el tope min(35, target) del harvest. ValueError
    ruidoso; el gemelo de este test en bid existe desde el fix -- este cierra
    el hueco de cobertura que senalo grok en la ronda 2."""
    for target in ("0", "-25", "nan"):
        with pytest.raises(ValueError, match="target_acos_pct"):
            _decide(_terminos([_harvesteable()]), target=target)


def test_harvest_acos_borde_inclusivo_y_un_pelo_arriba():
    """ACoS justo = tope (25% con target 25: cost 25 sobre revenue 100)
    HARVESTEA (<= inclusivo, por multiplicacion exacta); un pelo arriba
    (25.01) -> no-op 'acos_sobre_tope'."""
    r = _decide(_terminos([_harvesteable(cost=Decimal("25"), ad_revenue=Decimal("100"))]))
    assert r["zapato rojo"].kind == "harvest"
    r2 = _decide(_terminos([_harvesteable(cost=Decimal("25.01"), ad_revenue=Decimal("100"))]))
    assert r2["zapato rojo"].kind is None
    assert r2["zapato rojo"].motivo == "acos_sobre_tope"


def test_harvest_tope_es_min_35_target():
    """El tope es min(35, target): con target 40 manda el 35 sellado (cost 35
    exacto harvestea; 36 no). Usar el target a secas seria una regresion."""
    r = _decide(
        _terminos([_harvesteable(cost=Decimal("35"), ad_revenue=Decimal("100"))]), target="40"
    )
    assert r["zapato rojo"].kind == "harvest"
    r2 = _decide(
        _terminos([_harvesteable(cost=Decimal("36"), ad_revenue=Decimal("100"))]), target="40"
    )
    assert r2["zapato rojo"].kind is None
    assert r2["zapato rojo"].motivo == "acos_sobre_tope"


def test_harvest_ad_revenue_cero_solo_pasa_con_cost_cero():
    """Multiplicacion exacta: ad_revenue=0 deja el tope en cost*100 <= 0, asi
    que SOLO cost=0 pasa (con orders>=2: ventas gratis, se harvestea);
    cost>0 con revenue 0 es ACoS infinito -> sobre tope."""
    r = _decide(_terminos([_harvesteable(cost=Decimal("0"), ad_revenue=Decimal("0"))]))
    assert r["zapato rojo"].kind == "harvest"
    r2 = _decide(_terminos([_harvesteable(cost=Decimal("0.01"), ad_revenue=Decimal("0"))]))
    assert r2["zapato rojo"].kind is None
    assert r2["zapato rojo"].motivo == "acos_sobre_tope"


def test_orders_2_habilita_orders_1_sin_banda():
    """orders=2 exacto habilita harvest; orders=1 no es ni negative ni
    harvest -> no-op 'sin_banda' (ni una venta para harvest, ninguna para
    castigar sin venta)."""
    r = _decide(_terminos([_harvesteable()]))["zapato rojo"]
    assert r.kind == "harvest"
    r2 = _decide(
        _terminos([_term(cost=Decimal("10"), ad_revenue=Decimal("100"), clicks=5, orders=1)])
    )["zapato rojo"]
    assert r2.kind is None
    assert r2.motivo == "sin_banda"


def test_harvest_lleva_default_bid_y_moneda_negative_sin_dinero():
    """kind 'harvest' lleva new_value=default_bid del goal con SU moneda
    (regla 4: el bid lleva moneda); kind 'negative' no lleva dinero."""
    r = _decide(_terminos([_harvesteable(search_term="bueno"), _negativo(search_term="malo")]))
    assert r["bueno"].kind == "harvest"
    assert r["bueno"].new_value == Decimal("0.75")
    assert r["bueno"].value_currency == "USD"
    assert r["bueno"].motivo == "harvest_umbral"
    assert r["malo"].kind == "negative"
    assert r["malo"].new_value is None
    assert r["malo"].value_currency is None


def test_moneda_incoherente_skip_harvest():
    """Defensa en profundidad (igual que 2.2): la moneda del termino debe ser
    la de la plataforma; una MXN (o None) sobre amazon_us no harvestea aunque
    el ACoS pase."""
    r = _decide(_terminos([_harvesteable(moneda="MXN")]))["zapato rojo"]
    assert r.kind is None
    assert r.motivo == "moneda_incoherente"
    r2 = _decide(_terminos([_harvesteable(moneda=None)]))["zapato rojo"]
    assert r2.kind is None
    assert r2.motivo == "moneda_incoherente"


# ---------------------------------------------------------------------------
# Semantica de None (regla 3: faltante != cero) y dato faltante por camino
# ---------------------------------------------------------------------------


def test_orders_none_jamas_negative():
    """orders=None es DESCONOCIDO, no 0 (semantica sellada 1.5): con clicks 50
    y cost 15 (muy sobre los umbrales us) NO hay negative; el motivo declara
    el desconocido. Jamas un corte por dato faltante."""
    r = _decide(_terminos([_term(cost=Decimal("15"), clicks=50, orders=None)]))["zapato rojo"]
    assert r.kind is None
    assert r.motivo == "orders_desconocido"


def test_clicks_o_cost_none_negative_dato_faltante():
    """orders==0 con clicks o cost None -> skip 'dato_faltante' (umbrales no
    evaluables; un None jamas cuenta como 0)."""
    r_clicks = _decide(_terminos([_negativo(clicks=None)]))["zapato rojo"]
    assert r_clicks.kind is None
    assert r_clicks.motivo == "dato_faltante"
    r_cost = _decide(_terminos([_negativo(cost=None)]))["zapato rojo"]
    assert r_cost.kind is None
    assert r_cost.motivo == "dato_faltante"


def test_cost_o_revenue_none_harvest_dato_faltante():
    """orders>=2 con cost o ad_revenue None -> ACoS desconocido -> skip
    'dato_faltante' (mismo vocabulario que el camino negative)."""
    r_cost = _decide(_terminos([_harvesteable(cost=None)]))["zapato rojo"]
    assert r_cost.kind is None
    assert r_cost.motivo == "dato_faltante"
    r_rev = _decide(_terminos([_harvesteable(ad_revenue=None)]))["zapato rojo"]
    assert r_rev.kind is None
    assert r_rev.motivo == "dato_faltante"


# ---------------------------------------------------------------------------
# Completitud: la unidad es la ENTIDAD (sellado 2.1)
# ---------------------------------------------------------------------------


def test_entidad_6_fechas_todos_skip_incluido_el_que_cumpliria():
    """Entidad con 6 fechas (<7) en su ventana de cortes: TODOS sus terminos
    skip 'entidad_incompleta', incluido uno que cumpliria umbrales de
    negative y otro de harvest (regla 6: corte sin madurez no decide)."""
    r = _decide(
        _terminos([_negativo(search_term="malo"), _harvesteable(search_term="bueno")], n_fechas=6)
    )
    assert r["malo"].kind is None
    assert r["malo"].motivo == "entidad_incompleta"
    assert r["malo"].window_end is None
    assert r["bueno"].kind is None
    assert r["bueno"].motivo == "entidad_incompleta"


def test_termino_3_fechas_en_entidad_completa_decide():
    """Unidad sellada: la completitud es de la ENTIDAD; el termino NO exige
    fechas propias (exigirlas mataria los negativos long-tail). Un termino de
    3 fechas dentro de una entidad completa SÍ decide si cumple sus umbrales."""
    r = _decide(_terminos([_negativo(fechas=3)]))["zapato rojo"]
    assert r.kind == "negative"


# ---------------------------------------------------------------------------
# Ventanas y data_observed_at por camino
# ---------------------------------------------------------------------------


def test_ventanas_y_data_observed_at_por_camino():
    """El kind decidido lleva la ventana de cortes de TerminosCortes + SU
    observed_at_max; un no-op/skip no lleva ventana (misma semantica que
    ResultadoBid: nada decidio, nada Congela)."""
    r = _decide(
        _terminos(
            [
                _negativo(search_term="malo", observed=OBS_NEG),
                _harvesteable(search_term="bueno", observed=OBS_HARV),
                _term(search_term="ruido", cost=Decimal("1"), clicks=1, orders=0),
            ]
        )
    )
    assert (r["malo"].window_start, r["malo"].window_end) == (INICIO_CORTES, FIN_CORTES)
    assert r["malo"].data_observed_at == OBS_NEG
    assert (r["bueno"].window_start, r["bueno"].window_end) == (INICIO_CORTES, FIN_CORTES)
    assert r["bueno"].data_observed_at == OBS_HARV
    assert r["ruido"].kind is None
    assert r["ruido"].window_start is None
    assert r["ruido"].window_end is None
    assert r["ruido"].data_observed_at is None


# ---------------------------------------------------------------------------
# Defensa de vocabulario (ValueError ruidoso y temprano)
# ---------------------------------------------------------------------------


def test_plataforma_fuera_de_vocabulario_raise():
    with pytest.raises(ValueError, match="vocabulario"):
        _decide(_terminos([]), platform="meli")


# ---------------------------------------------------------------------------
# (c) GUARDA DE SINTAXIS - la SQL del modulo es Postgres valido
# ---------------------------------------------------------------------------


def test_sql_del_modulo_parsea_como_postgres():
    """Patron test_optimizer_windows: pglast es dev-dep declarada y su
    desaparicion debe FALLAR ruidosamente, no saltar en silencio."""
    for nombre in ("_SQL_KEYWORDS_CAMPANA",):
        sql = getattr(h, nombre).replace("%s", "NULL")
        assert pglast.parse_sql(sql), f"{nombre} no parseo"


# ---------------------------------------------------------------------------
# (b) INTEGRACION - patron _db_temporal COPIADO de test_optimizer_windows
# ---------------------------------------------------------------------------

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


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


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[
        0
    ]


def _entidad(conn, platform: str, kind: str, external: str, parent=None, **extra) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, extra.get("match_type"), extra.get("keyword_text")),
    ).fetchone()[0]


def _termino(
    conn,
    run_id,
    platform,
    ad_entity_id,
    term,
    fecha,
    observed_at,
    *,
    moneda,
    report_id,
    cost=None,
    ad_revenue=None,
    clicks=None,
    orders=None,
    asin=False,
) -> None:
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term,"
        " metric_date, observed_at, metric_currency, cost, clicks, orders,"
        " ad_revenue, is_asin_like, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            platform,
            ad_entity_id,
            term,
            fecha,
            observed_at,
            moneda,
            cost,
            clicks,
            orders,
            ad_revenue,
            asin,
            report_id,
            run_id,
        ),
    )


def _obs(fecha: dt.date, hora: int = 1) -> dt.datetime:
    """observed_at de una observacion: medianoche + hora UTC (>= metric_date)."""
    return dt.datetime(fecha.year, fecha.month, fecha.day, hora, tzinfo=dt.UTC)


def _rango(inicio: dt.date, fin: dt.date) -> list[dt.date]:
    """Fechas diarias inclusive (helper de seed)."""
    dias: list[dt.date] = []
    fecha = inicio
    while fecha <= fin:
        dias.append(fecha)
        fecha += _DIA
    return dias


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_desempate_source_report_id_en_vivo():
    """Rojo real contra windows.py SIN desempate (regla 9): dos observaciones
    del mismo (entidad, termino, fecha) con el MISMO observed_at (mismo run
    de backfill, reportes distintos) hacen ARBITRARIO el DISTINCT ON.

    Bajo el esquema sellado este empate no puede entrar a la tabla (la PK
    incluye observed_at y el trigger st_metric_moneda_sellada clava la
    plataforma de la fila a la de su entidad, cerrando la unica via de
    colision); el test DROPEA la PK en su DB temporal para sembrarlo y probar
    que el query es determinista POR SI MISMO (defensa en profundidad: si la
    PK/grano cambia manana, el colapso sigue siendo correcto). Con el
    desempate source_report_id DESC NULLS LAST, gana el reporte MAYOR y la
    fila CON reporte gana sobre una sin el (NULL): DESC sin clausula pone
    los NULL PRIMEROS, que es lo contrario de trazable (hallazgo codex,
    ronda 1)."""
    with _db_temporal("orbit_hyg_desempate") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9001")
        ag = _entidad(conn, "amazon_us", "ad_group", "9101", parent=camp)

        # 7 fechas maduras base (07-28..08-03) del termino, cost 0.10 c/u
        for fecha in _rango(dt.date(2026, 7, 28), dt.date(2026, 8, 3)):
            _termino(
                conn,
                run_id,
                "amazon_us",
                ag,
                "arras de boda",
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-BASE",
                cost=Decimal("0.10"),
                clicks=1,
                orders=0,
            )
        # EL EMPATE (08-04): mismo observed_at, source_report_id DISTINTO y
        # cost DISTINTO. La PK no deja insertar la segunda: se dropea SOLO en
        # esta DB temporal (el trigger sigue: plataforma y moneda coherentes).
        empate_at = _obs(dt.date(2026, 8, 5), 3)
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "arras de boda",
            dt.date(2026, 8, 4),
            empate_at,
            moneda="USD",
            report_id="R-TIE-1",
            cost=Decimal("1.00"),
            clicks=1,
            orders=0,
        )
        conn.execute(
            "ALTER TABLE search_term_observation DROP CONSTRAINT search_term_observation_pkey"
        )
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "arras de boda",
            dt.date(2026, 8, 4),
            empate_at,
            moneda="USD",
            report_id="R-TIE-2",
            cost=Decimal("2.00"),
            clicks=2,
            orders=0,
        )
        # TERCERA fila del empate SIN source_report_id y cost absurdo: sin
        # NULLS LAST, DESC pone los NULL primero y esta fila GANARIA (99+).
        # El indice de dedupe es parcial (WHERE source_report_id IS NOT
        # NULL): una fila sin reporte no choca con nada.
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "arras de boda",
            dt.date(2026, 8, 4),
            empate_at,
            moneda="USD",
            report_id=None,
            cost=Decimal("99.00"),
            clicks=99,
            orders=0,
        )
        # observacion fresca (08-19) que SOLO sube el ancla de la ventana
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "arras de boda",
            dt.date(2026, 8, 19),
            _obs(dt.date(2026, 8, 19)),
            moneda="USD",
            report_id="R-FRESCO",
            cost=Decimal("5.00"),
            clicks=5,
            orders=0,
        )

        tc = w.terminos_cortes(conn, ag, DECIDED_AT)
        assert tc.window_end == FIN_CORTES  # min(08-19 - 3d, 08-22 - 10d)
        assert tc.completa is True  # 8 fechas de entidad en ventana
        (term,) = tc.terminos
        assert term.search_term == "arras de boda"
        assert term.fechas_distintas == 8
        # SOLO la fila del source_report_id MAYOR entra al colapso del empate:
        # 7 * 0.10 + 2.00 = 2.70 (si ganara R-TIE-1: 1.70; si ambas: 3.70)
        assert term.cost == Decimal("2.70")
        assert term.clicks == 9  # 7 * 1 + clicks del ganador (2)


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_keywords_campana_destino_en_vivo():
    """keywords_campana_destino: SOLO keywords EXACT de la campana destino,
    navegando ad_group -> campaign por parent_id (el comentario de ad_entity
    declara que keyword_text/match_type existen PARA este duplicate-check).
    Una PHRASE con el mismo texto NO es duplicado (el harvest crea EXACT):
    se siembran DOS PHRASE -- una con el MISMO texto del EXACT (caso del
    docstring, invisible en el frozenset) y otra con texto UNICO que hace el
    assert DISCRIMINANTE (si el filtro match_type se rompiera, apareceria en
    el set; hallazgo grok ronda 1). Una EXACT de OTRA plataforma tampoco
    (cammuflaje via campaign_id igual)."""
    with _db_temporal("orbit_hyg_keywords") as conn:
        _run(conn)
        camp_dest = _entidad(conn, "amazon_us", "campaign", "9002")
        ag_dest = _entidad(conn, "amazon_us", "ad_group", "9102", parent=camp_dest)
        _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9202",
            parent=ag_dest,
            match_type="EXACT",
            keyword_text="zapato rojo",
        )
        _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9203",
            parent=ag_dest,
            match_type="PHRASE",
            keyword_text="zapato rojo",
        )
        _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9205",
            parent=ag_dest,
            match_type="PHRASE",
            keyword_text="frase unica phrase",
        )
        # misma campana DESTINO no: otra plataforma con el mismo texto
        camp_mx = _entidad(conn, "amazon_mx", "campaign", "9002")
        ag_mx = _entidad(conn, "amazon_mx", "ad_group", "9102", parent=camp_mx)
        _entidad(
            conn,
            "amazon_mx",
            "keyword",
            "9204",
            parent=ag_mx,
            match_type="EXACT",
            keyword_text="zapato rojo",
        )

        kws = h.keywords_campana_destino(conn, "amazon_us", "9002")
        assert kws == frozenset({"zapato rojo"})
        # end-to-end del claim del docstring: un termino que SOLO existe como
        # keyword PHRASE ('frase unica phrase') NO se deduplica (no esta en
        # el set EXACT) -> el harvest sale. El termino 'zapato rojo' si se
        # deduplicaria (la EXACT existe): eso ya lo cubre el test puro.
        resultados = h.decide_hygiene(
            platform="amazon_us",
            terminos=_terminos([_harvesteable(search_term="frase unica phrase")]),
            target_acos_pct=Decimal("25"),
            config_harvest=CONFIG,
            keywords_existentes=kws,
        )
        assert resultados[0].search_term == "frase unica phrase"
        assert resultados[0].kind == "harvest"
        # campana sin keywords: conjunto VACIO, no error (regla 3: ausencia
        # declarada; el harvest dedupe contra vacio simplemente no bloquea)
        _entidad(conn, "amazon_us", "campaign", "9999")
        assert h.keywords_campana_destino(conn, "amazon_us", "9999") == frozenset()


def test_keywords_campana_destino_incluye_archivadas():
    """BIDS 01 2.2 (a), H2 acotado: una keyword EXACT ARCHIVADA en destino
    SIGUE en el set. Es lo que impide el bucle archivar->recrear: el sync
    solo marca ad_entity_state y la fila de ad_entity se conserva, asi que
    el dedupe la ve. Si alguien excluye archivadas del SQL, el ciclo
    decide un harvest duplicado (rojo en el plan)."""
    with _db_temporal("orbit_hyg_archivada") as conn:
        _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9002")
        ag = _entidad(conn, "amazon_us", "ad_group", "9102", parent=camp)
        kw = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9202",
            parent=ag,
            match_type="EXACT",
            keyword_text="zapato rojo",
        )
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency,"
            " status, synced_at) VALUES (%s, 1.00, 'USD', 'ARCHIVED', now())",
            (kw,),
        )
        assert h.keywords_campana_destino(conn, "amazon_us", "9002") == frozenset({"zapato rojo"})


def test_negative_cost_129_99_no_mx():
    """Un centavo bajo el umbral MX (129.99, umbral 130): NO negativiza
    (simetria con test_negative_borde_exacto_mx_cost_130; review 2.2-2.4)."""
    r = _decide(
        _terminos([_negativo(cost=Decimal("129.99"), moneda="MXN")]),
        platform="amazon_mx",
        config=h.ConfigHarvest("9", "9", Decimal("20"), "MXN"),
    )["zapato rojo"]
    assert r.kind is None
    assert r.motivo == "sin_umbral_negative"
