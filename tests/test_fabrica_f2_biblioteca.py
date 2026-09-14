"""Biblioteca escrita por el motor (FABRICA 02, A.4).

Bloques del brief: keyword en el sello del evento de valor (con segunda
conexion, no-duplicado y tope de ciclos), matriz de cero filas (terna,
excepcion, failed, vetado, shadow, perdida, negativos de harvest),
negative en cola y en reconciliacion (con precedencia keyword), fallo
inyectado (sello y veredicto intactos, rastro y alerta veraz) y rol
`app_decide`. Reutiliza `db_f2` (ya aplica 0038: bibliotecas y GRANTs
existen), los helpers de `test_fabrica_f2_hermanas` y los de
`test_apply_cola`/`test_apply_harvest`; nada duplicado.
"""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest
from psycopg.types.json import Json
from test_apply_cola import (
    _aplicador as _aplicador_cola,
)
from test_apply_cola import (
    _decision_corte as _decision_cola,
)
from test_apply_cola import (
    _encola_fila as _encola_cola,
)
from test_apply_cola import (
    _handler_cortes,
    _payload_negative,
    _termino_obs,
)
from test_apply_cola import (
    _semilla as _semilla_cola,
)
from test_apply_harvest import (
    TERMINO,
    _aplicador,
    _claim_fila,
    _decision_harvest,
    _encola_fila,
    _estado,
    _fila_ledger_abierta,
    _handler_harvest,
    _libera_fila,
    _termino_calificado,
)
from test_apply_harvest import (
    _semilla as _semilla_harvest,
)
from test_fabrica_f2 import _semilla_grupo, db_f2
from test_fabrica_f2_hermanas import (
    ROLES_DISCOVERY,
    _corre_harvest_grupo,
    _decision_harvest_grupo,
    _grupo_listo,
    _job_de,
    _reconcilia,
)
from test_schema import _postgres_obligatorio_ausente

from app.apply_cola import fila_cola, libera_vencidos
from app.apply_harvest import aplica_harvest
from app.biblioteca import (
    MOTIVO_BIBLIOTECA_FALLO,
    MOTIVO_TERMINO_EN_KEYWORD_BIBLIOTECA,
    SQL_BIBLIOTECA_KEYWORD,
    SQL_BIBLIOTECA_NEGATIVE,
    grupo_de_ad_group,
    registra_keyword,
    registra_negative,
    tipo_producto_de_grupo,
)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

# Con mayusculas y espacios sobrantes a proposito: la biblioteca guarda
# el NORMALIZADO (strip + casefold), no el crudo.
TERMINO_A4 = "  Termino Biblioteca A4 "
TERMINO_NORM = "termino biblioteca a4"


def _obs_negative(conn, run_id: int, ag: int, term: str) -> None:
    """Observaciones FRESCAS del termino con orders CERO (la evidencia que
    hace que `decide_hygiene` diga negative al revalidar; mismo shape que
    `test_negative_se_aplica_y_reversa` de `test_apply_cola`: 18 fechas x
    4 clics / 4 costo = 72/72, sobre el umbral 40 y el piso 45)."""
    d = dt.datetime.now(dt.UTC)
    for x in range(23, 41):
        fecha = d.date() - dt.timedelta(days=x)
        _termino_obs(conn, run_id, ag, term, fecha, clicks=4, cost=4, orders=0)


def _kw_bib(conn, term: str = TERMINO_NORM, platform: str = "amazon_us") -> list:
    return conn.execute(
        "SELECT id, tipo_producto, platform, texto, origen, cost, revenue, moneda, orders"
        " FROM keyword_biblioteca WHERE texto = %s AND platform = %s",
        (term, platform),
    ).fetchall()


def _neg_bib(conn, term: str, platform: str = "amazon_us") -> list:
    # `negative_biblioteca` NO tiene columnas de dinero (0018): el "sin
    # dinero" ahi es por esquema; aqui se asierta fila y origen.
    return conn.execute(
        "SELECT id, tipo_producto, platform, texto, origen"
        " FROM negative_biblioteca WHERE texto = %s AND platform = %s",
        (term, platform),
    ).fetchall()


def _decision_negativa_grupo(conn, setup, rol: str, term: str) -> int:
    """Decision `kind = negative` sobre el AD GROUP del rol (la fila de un
    negative ES el ad group: revalidacion, `_SQL_EXTERNALES` y biblioteca
    lo asumen)."""
    return _decision_cola(
        conn,
        setup["ciclo_dec"],
        setup["config"],
        setup["roles"][rol]["ag"],
        "negative",
        term=term,
    )


def _encola_negativa_grupo(conn, dec: int, setup, rol: str, term: str) -> int:
    par = setup["roles"][rol]
    return _encola_cola(
        conn,
        dec,
        par["ag"],
        "negative",
        term=term,
        payload=_payload_negative(par["ag_ext"], par["camp_ext"], term),
    )


def _libera_negativa(conn, setup, dec: int, rol: str, term: str, **handler_kw):
    """Encola el negative de grupo y lo libera por el camino real."""
    _encola_negativa_grupo(conn, dec, setup, rol, term)
    handler, vistos = _handler_cortes(**handler_kw)
    res = libera_vencidos(
        conn,
        "amazon_us",
        ahora=dt.datetime.now(dt.UTC),
        aplicador=_aplicador_cola(conn, handler, setup["ciclo_ejec"]),
    )
    return res, vistos, handler


# ---------------------------------------------------------------------------
# Bloque 2: keyword aprendida en el sello del evento de valor
# ---------------------------------------------------------------------------


@_skip_db
def test_harvest_grupo_aprende_keyword_en_sello():
    """Harvest de grupo hasta `done`: UNA fila con `tipo_producto` del
    grupo, texto normalizado, `origen` exacto grupo/campana/job y dinero
    NULL; rastro `external_ids["biblioteca"]` con `escrita: true` e `id`.
    Regla 9: sin la escritura en el sello, la fila no existe."""
    with db_f2("orbit_bib_kw1") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        corrido = _corre_harvest_grupo(conn, setup, term=TERMINO_A4)
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "done", job
        filas = _kw_bib(conn)
        assert len(filas) == 1, filas
        f_id, tipo, plat, texto, origen, cost, revenue, moneda, orders = filas[0]
        assert tipo == "collar_perro", "tipo_producto del grupo, jamas por nombre"
        assert plat == "amazon_us"
        assert texto == TERMINO_NORM, "texto normalizado (strip + casefold)"
        jid = conn.execute(
            "SELECT id FROM harvest_job WHERE decision_id = %s", (corrido["dec"],)
        ).fetchone()[0]
        assert origen == (
            f"grupo:{setup['grupo_id']}/campana:{setup['origen']['ag']}/harvest:{jid}"
        ), origen
        assert cost is None and revenue is None and moneda is None, "sin dinero"
        assert orders == 0, "default de 0018"
        assert job["ext"].get("biblioteca") == {"escrita": True, "id": f_id}, job["ext"]


@_skip_db
def test_keyword_visible_desde_segunda_conexion_antes_del_primer_post(monkeypatch):
    """Otra conexion ve la fila con la cola `applied` y la fase
    `hermanas_negadas` ANTES del primer POST de hermana. Regla 9: el
    mutante que escribe en `_paso_hermanas` o `_cierra_o_sigue` (en vez
    del sello) deja a la segunda conexion sin fila. Espejo del test de
    sello de A.3."""
    with db_f2("orbit_bib_kw2") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        hermanas_ag = {
            setup["roles"][r]["ag_ext"] for r in ROLES_DISCOVERY if r != setup["origen_rol"]
        }
        visto: dict = {}

        from test_schema import _test_dsn

        from app.ads.write import AdsWriteClient

        original = AdsWriteClient.crear_negative_exacto

        def _espia(self, ad_group_id, campaign_id, keyword_text):
            if str(ad_group_id) in hermanas_ag and "bib" not in visto:
                otra = psycopg.connect(_test_dsn(), dbname=conn.info.dbname, autocommit=True)
                try:
                    visto["bib"] = otra.execute(
                        "SELECT count(*) FROM keyword_biblioteca WHERE texto = %s",
                        (TERMINO_NORM,),
                    ).fetchone()[0]
                    visto["cola"] = otra.execute(
                        "SELECT estado FROM apply_queue WHERE id = %s", (visto["qid"],)
                    ).fetchone()[0]
                    visto["fase"] = otra.execute(
                        "SELECT fase FROM harvest_job WHERE decision_id = %s",
                        (visto["dec"],),
                    ).fetchone()[0]
                finally:
                    otra.close()
            return original(self, ad_group_id, campaign_id, keyword_text)

        monkeypatch.setattr(AdsWriteClient, "crear_negative_exacto", _espia)
        exacta = setup["roles"]["category_exact"]
        dec = _decision_harvest_grupo(
            conn,
            setup["ciclo_dec"],
            setup["config"],
            setup["origen"]["ag"],
            grupo_id=setup["grupo_id"],
            exacta_camp_ext=exacta["camp_ext"],
            exacta_ag_ext=exacta["ag_ext"],
            term=TERMINO_A4,
        )
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_A4)
        visto["dec"], visto["qid"] = dec, qid
        handler, _vistos = _handler_harvest()
        libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, setup["ciclo_ejec"]),
        )
        assert visto.get("bib") == 1, "fila visible antes del primer POST"
        assert visto.get("cola") == "applied", "con la cola applied"
        assert visto.get("fase") == "hermanas_negadas", "y la fase avanzada"


@_skip_db
def test_mismo_termino_otro_job_actualiza_updated_at_sin_duplicar():
    """Repetir el termino en otro job del mismo grupo (otro origen) mueve
    `updated_at` sin duplicar y jamas pisa `origen`. Regla 9: sin ON
    CONFLICT, el segundo sello duplicaria o reventaria por unique."""
    with db_f2("orbit_bib_kw3") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        _corre_harvest_grupo(conn, setup, term=TERMINO_A4)
        assert len(_kw_bib(conn)) == 1
        conn.execute(
            "UPDATE keyword_biblioteca SET updated_at = now() - interval '1 hour' WHERE texto = %s",
            (TERMINO_NORM,),
        )
        primera = _kw_bib(conn)[0]
        marca = conn.execute(
            "SELECT updated_at FROM keyword_biblioteca WHERE id = %s", (primera[0],)
        ).fetchone()[0]
        # Segundo job del MISMO termino desde otro rol de origen (con su
        # evidencia fresca para la revalidacion: sin ella no hay job).
        otro_rol = next(r for r in ROLES_DISCOVERY if r != setup["origen_rol"])
        _termino_calificado(conn, setup["roles"][otro_rol]["ag"], term=TERMINO_A4)
        setup2 = {**setup, "origen": setup["roles"][otro_rol], "origen_rol": otro_rol}
        segundo = _corre_harvest_grupo(conn, setup2, term=TERMINO_A4)
        assert _job_de(conn, segundo["dec"])["fase"] == "done"
        filas = _kw_bib(conn)
        assert len(filas) == 1, "no duplica"
        assert filas[0][0] == primera[0], "id estable"
        assert filas[0][4] == primera[4], "origen del primero intacto"
        movido = conn.execute(
            "SELECT updated_at FROM keyword_biblioteca WHERE id = %s", (primera[0],)
        ).fetchone()[0]
        assert movido > marca, "el segundo sello movio updated_at"


@_skip_db
def test_reconciliacion_de_job_en_hermanas_negadas_no_reescribe():
    """Retomar un job en `hermanas_negadas` (una hermana fallo, se
    reconcilia y cierra) no vuelve a escribir biblioteca: el sello ya lo
    hizo. Regla 9: escribir al retomar moveria `updated_at` y el `origen`
    quedaria ambiguo."""
    with db_f2("orbit_bib_kw4") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn,
            setup,
            term=TERMINO_A4,
            handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}},
        )
        assert _job_de(conn, corrido["dec"])["fase"] == "hermanas_negadas"
        assert len(_kw_bib(conn)) == 1
        conn.execute(
            "UPDATE keyword_biblioteca SET updated_at = now() - interval '1 hour' WHERE texto = %s",
            (TERMINO_NORM,),
        )
        marca = conn.execute(
            "SELECT updated_at FROM keyword_biblioteca WHERE texto = %s", (TERMINO_NORM,)
        ).fetchone()[0]
        handler2, _v2 = _handler_harvest()
        resumen = _reconcilia(conn, setup, handler2)
        assert resumen.jobs_done == 1
        assert _job_de(conn, corrido["dec"])["fase"] == "done"
        filas = _kw_bib(conn)
        assert len(filas) == 1, "sin re-escritura"
        sigue = conn.execute(
            "SELECT updated_at FROM keyword_biblioteca WHERE texto = %s", (TERMINO_NORM,)
        ).fetchone()[0]
        assert sigue == marca, "ni siquiera se toca updated_at al retomar"


@_skip_db
def test_done_por_tope_de_ciclos_no_duplica_ni_reescribe():
    """`done` por tope de ciclos (hermana en 400 persistente): la fila
    existe desde el sello, unica, con el rastro intacto y sin reescritura
    al cerrar con pendientes. Regla 9: escribir en `_cierra_o_sigue`
    moveria `updated_at` al tercer ciclo."""
    with db_f2("orbit_bib_kw5") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        hermanas = [r for r in ROLES_DISCOVERY if r != setup["origen_rol"]]
        ag_falla = setup["roles"][hermanas[0]]["ag_ext"]
        corrido = _corre_harvest_grupo(
            conn,
            setup,
            term=TERMINO_A4,
            handler_kw={"fallo_post_negative_por_adgroup": {ag_falla: 400}},
        )
        conn.execute(
            "UPDATE keyword_biblioteca SET updated_at = now() - interval '1 hour' WHERE texto = %s",
            (TERMINO_NORM,),
        )
        marca = conn.execute(
            "SELECT updated_at FROM keyword_biblioteca WHERE texto = %s", (TERMINO_NORM,)
        ).fetchone()[0]
        for _ in range(2):
            handler, _v = _handler_harvest(fallo_post_negative_por_adgroup={ag_falla: 400})
            _reconcilia(conn, setup, handler)
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "done", job
        assert job["ext"]["hermanas_pendientes"] == {hermanas[0]: "http_400"}, job["ext"]
        filas = _kw_bib(conn)
        assert len(filas) == 1, "una sola fila tras los tres ciclos"
        assert job["ext"].get("biblioteca") == {"escrita": True, "id": filas[0][0]}
        sigue = conn.execute(
            "SELECT updated_at FROM keyword_biblioteca WHERE texto = %s", (TERMINO_NORM,)
        ).fetchone()[0]
        assert sigue == marca, "el cierre por tope no reescribe"


# ---------------------------------------------------------------------------
# Bloque 3: matriz de cero filas
# ---------------------------------------------------------------------------


@_skip_db
def test_terna_sin_grupo_cero_filas_biblioteca():
    """Harvest por terna (sin grupo) cierra `done` como hoy, sin fila de
    keyword y sin que el negativo de origen entre a `negative_biblioteca`.
    Regla 9: el mutante que escribe para `resuelto_por != grupo` deja una
    fila aqui."""
    with db_f2("orbit_bib_cero1") as conn:
        ids = _semilla_harvest(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        qid = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _termino_calificado(conn, ids["ag"], term=TERMINO)
        handler, _vistos = _handler_harvest()
        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )
        assert res.aplicadas == 1
        assert _job_de(conn, dec)["fase"] == "done"
        assert _kw_bib(conn, TERMINO) == [], "sin grupo: cero keywords"
        assert conn.execute("SELECT count(*) FROM negative_biblioteca").fetchone()[0] == 0
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert cola == "applied"


def _decision_harvest_excepcion(conn, ciclo, config_id, ag_origen, *, camp_ext, ag_ext):
    """Decision harvest con destino de excepcion congelado (`resuelto_por =
    excepcion`, como lo deja `_goal_json` en A.1). Espejo de
    `_decision_harvest_grupo` sin grupo."""
    dec = dt.datetime.now(dt.UTC) - dt.timedelta(days=3)
    inputs = {
        "motor": "hygiene",
        "platform": "amazon_us",
        "modo": "live",
        "motivo": "harvest_umbral",
        "goal": {
            "scope": "campaign",
            "bid_floor": "0.10",
            "bid_ceiling": "2.50",
            "harvest": {
                "campaign_id": camp_ext,
                "ad_group_id": ag_ext,
                "default_bid": "1.00",
                "moneda": "USD",
                "resuelto_por": "excepcion",
                "grupo_id": None,
                "motivo": None,
            },
        },
    }
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'harvest', %s, %s, %s - interval '1 day', %s - 60, %s - 30,"
        " %s, 1.00, 'USD', %s) RETURNING id",
        (ciclo, ag_origen, dec, config_id, dec, dec.date(), dec.date(), TERMINO, Json(inputs)),
    ).fetchone()[0]


@_skip_db
def test_excepcion_sin_grupo_cero_filas_biblioteca():
    """Harvest por excepcion (fila real en `harvest_excepcion`, destino en
    `ad_entity`): cierra `done` por la rama no-grupo, sin fila de keyword
    y sin negativos aprendidos. Regla 9: mismo mutante que la terna."""
    with db_f2("orbit_bib_cero1b") as conn:
        ids = _semilla_harvest(conn)
        dest_camp = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_us', 'campaign', 'EXC1') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES ('amazon_us', 'ad_group', 'EXG1', %s)",
            (dest_camp,),
        )
        conn.execute(
            "INSERT INTO harvest_excepcion"
            " (ad_entity_id, destino_campaign_external, destino_ad_group_external, go_literal)"
            " VALUES (%s, 'EXC1', 'EXG1', 'go dueno 2026-09-14')",
            (ids["camp"],),
        )
        dec = _decision_harvest_excepcion(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], camp_ext="EXC1", ag_ext="EXG1"
        )
        qid = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        handler, vistos = _handler_harvest()
        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )
        assert res.aplicadas == 1, res
        assert _job_de(conn, dec)["fase"] == "done"
        assert _kw_bib(conn, TERMINO) == [], "excepcion: cero keywords"
        assert conn.execute("SELECT count(*) FROM negative_biblioteca").fetchone()[0] == 0
        muts = [r for r in vistos if r.method == "POST" and not r.url.path.endswith("/list")]
        assert [r.url.path for r in muts] == ["/sp/negativeKeywords", "/sp/keywords"]
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert cola == "applied"


@_skip_db
def test_grupo_negativos_de_harvest_no_entran_a_negativa():
    """Con grupo: origen y hermanas se niegan en Amazon, pero
    `negative_biblioteca` queda en cero para el termino (invariante (d),
    direccion harvest: son ruteo, no exclusion). Regla 9: el mutante que
    llama `registra_negative` desde `_paso_negative`/`_paso_hermanas`
    deja filas aqui."""
    with db_f2("orbit_bib_cero2") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        corrido = _corre_harvest_grupo(conn, setup, term=TERMINO_A4)
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "done"
        assert job["ext"]["hermanas"], "las hermanas se aplicaron"
        posts_neg = [
            r
            for r in corrido["vistos"]
            if r.method == "POST" and r.url.path == "/sp/negativeKeywords"
        ]
        assert len(posts_neg) == 4, "origen + 3 hermanas se negaron en Amazon"
        assert conn.execute("SELECT count(*) FROM negative_biblioteca").fetchone()[0] == 0
        assert len(_kw_bib(conn)) == 1, "la keyword si se aprendio"


@_skip_db
def test_failed_antes_de_readback_cero_filas():
    """400 en el POST de la keyword: `failed` con reversa y cero filas; la
    biblioteca no aprende lo no confirmado y no deja rastro. Regla 9: el
    mutante que escribe en el camino `failed` deja fila o rastro."""
    with db_f2("orbit_bib_cero3") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        corrido = _corre_harvest_grupo(
            conn, setup, term=TERMINO_A4, handler_kw={"fallo_keyword_status": 400}
        )
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "failed", job
        assert _kw_bib(conn) == []
        assert conn.execute("SELECT count(*) FROM negative_biblioteca").fetchone()[0] == 0
        assert job["ext"].get("biblioteca") is None, "sin rastro sin sello"


@_skip_db
def test_shadow_cero_filas_biblioteca():
    """Grupo en `shadow`: la fila jamas se libera, ningun job nace y las
    bibliotecas quedan vacias. Espejo de `test_grupo_en_shadow_cero_jobs`
    con la matriz de biblioteca."""
    with db_f2("orbit_bib_cero4") as conn:
        ids = _semilla_harvest(conn, caps={"ads_apply_cap_amazon_us_harvest": 2})
        grupo = _semilla_grupo(conn, mode="shadow")
        for par in grupo["roles"].values():
            _estado(conn, par["camp"])
            _estado(conn, par["ag"])
        origen = grupo["roles"]["category_phrase"]
        _termino_calificado(conn, origen["ag"], term=TERMINO_A4)
        exacta = grupo["roles"]["category_exact"]
        dec = _decision_harvest_grupo(
            conn,
            ids["ciclo_dec"],
            ids["config"],
            origen["ag"],
            grupo_id=grupo["grupo_id"],
            exacta_camp_ext=exacta["camp_ext"],
            exacta_ag_ext=exacta["ag_ext"],
            term=TERMINO_A4,
        )
        _encola_fila(conn, dec, origen["ag"], term=TERMINO_A4, modo="shadow")
        handler, vistos = _handler_harvest()
        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )
        assert res.liberadas == 0
        assert conn.execute("SELECT count(*) FROM harvest_job").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM keyword_biblioteca").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM negative_biblioteca").fetchone()[0] == 0
        assert vistos == []


@_skip_db
def test_vetado_cero_filas_biblioteca():
    """Harvest de grupo vetado por el dueno: la fila muere `vetoed`, el job
    jamas nace y las bibliotecas quedan vacias. Espejo de
    `test_harvest_vetado_jamas_crea_harvest_job` en grupo."""
    with db_f2("orbit_bib_cero5") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        exacta = setup["roles"]["category_exact"]
        dec = _decision_harvest_grupo(
            conn,
            setup["ciclo_dec"],
            setup["config"],
            setup["origen"]["ag"],
            grupo_id=setup["grupo_id"],
            exacta_camp_ext=exacta["camp_ext"],
            exacta_ag_ext=exacta["ag_ext"],
            term=TERMINO_A4,
        )
        _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_A4)
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                " vetoed_by = 'dueno', vence_el = now() + interval '30 days'"
                " WHERE decision_id = %s",
                (dec,),
            )
        finally:
            conn.execute("RESET ROLE")
        handler, vistos = _handler_harvest()
        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, setup["ciclo_ejec"]),
        )
        assert res.liberadas == 0
        assert conn.execute("SELECT count(*) FROM harvest_job").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM keyword_biblioteca").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM negative_biblioteca").fetchone()[0] == 0
        assert vistos == []


@_skip_db
def test_perdida_claim_cero_filas_biblioteca():
    """Claim perdido (un veto gano la carrera: la fila no esta `released`
    al reclamar): `aplica_harvest` vuelve `perdida` sin correr fases, el
    job queda `pending` y las bibliotecas quedan vacias. Cero HTTP."""
    with db_f2("orbit_bib_cero6") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        exacta = setup["roles"]["category_exact"]
        dec = _decision_harvest_grupo(
            conn,
            setup["ciclo_dec"],
            setup["config"],
            setup["origen"]["ag"],
            grupo_id=setup["grupo_id"],
            exacta_camp_ext=exacta["camp_ext"],
            exacta_ag_ext=exacta["ag_ext"],
            term=TERMINO_A4,
        )
        qid = _encola_fila(conn, dec, setup["origen"]["ag"], term=TERMINO_A4)
        handler, vistos = _handler_harvest()
        fila = fila_cola(conn, qid)
        assert fila is not None and fila.estado == "pending_veto"
        res = aplica_harvest(
            conn, _aplicador(conn, handler, setup["ciclo_ejec"]), fila, platform="amazon_us"
        )
        assert res.estado == "perdida", res
        assert _job_de(conn, dec)["fase"] == "pending"
        assert conn.execute("SELECT count(*) FROM keyword_biblioteca").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM negative_biblioteca").fetchone()[0] == 0
        assert vistos == [], "perdida antes de cualquier HTTP"


# ---------------------------------------------------------------------------
# Bloque 4: negative en cola y en reconciliacion
# ---------------------------------------------------------------------------


@_skip_db
def test_negative_en_cola_campana_grupo_aprende(monkeypatch):
    """Negative aplicado por `libera_vencidos` en campana de grupo deja UNA
    fila con `origen` de decision; veredicto del ledger `ok` intacto y
    cero alertas (el exito no avisa). Regla 9: sin el cableado en
    `_ejecuta_negative`, la fila no existe."""
    import app.notifica as _notifica

    with db_f2("orbit_bib_neg1") as conn:
        setup = _grupo_listo(conn)
        _obs_negative(conn, setup["run"], setup["roles"]["category_phrase"]["ag"], TERMINO_A4)
        rol = "category_phrase"
        par = setup["roles"][rol]
        dec = _decision_negativa_grupo(conn, setup, rol, TERMINO_A4)
        llamadas: list = []
        monkeypatch.setattr(
            _notifica, "notifica_biblioteca_no_escrita", lambda **kw: llamadas.append(kw) or True
        )
        res, _vistos, _h = _libera_negativa(conn, setup, dec, rol, TERMINO_A4)
        assert res.aplicadas == 1, res
        filas = _neg_bib(conn, TERMINO_NORM)
        assert len(filas) == 1, filas
        assert filas[0][1] == "collar_perro"
        assert filas[0][2] == "amazon_us"
        assert filas[0][3] == TERMINO_NORM, "texto normalizado"
        assert filas[0][4] == (f"grupo:{setup['grupo_id']}/campana:{par['camp']}/decision:{dec}"), (
            filas[0][4]
        )
        ledger = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'",
            (dec,),
        ).fetchone()
        assert ledger[0] == "ok", "el exito no toca el resultado"
        assert llamadas == [], "el exito no alerta"
        assert (
            conn.execute(
                "SELECT count(*) FROM keyword_biblioteca WHERE texto = %s", (TERMINO_NORM,)
            ).fetchone()[0]
            == 0
        )


@_skip_db
def test_negative_campana_sin_grupo_cero_filas_y_cero_alerta(monkeypatch):
    """Mismo negative en campana fuera de grupo: aplicado, cero filas y
    cero alerta (no es fallo: no hay `tipo_producto` que inventar)."""
    import app.notifica as _notifica

    with db_f2("orbit_bib_neg2") as conn:
        ids = _semilla_cola(conn)
        _obs_negative(conn, ids["run"], ids["ag"], TERMINO_A4)
        dec = _decision_cola(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term=TERMINO_A4
        )
        _encola_cola(
            conn,
            dec,
            ids["ag"],
            "negative",
            term=TERMINO_A4,
            payload=_payload_negative("7101", "7001", TERMINO_A4),
        )
        llamadas: list = []
        monkeypatch.setattr(
            _notifica, "notifica_biblioteca_no_escrita", lambda **kw: llamadas.append(kw) or True
        )
        handler, _vistos = _handler_cortes()
        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador_cola(conn, handler, ids["ciclo_ejec"]),
        )
        assert res.aplicadas == 1, res
        assert _neg_bib(conn, TERMINO_NORM) == []
        assert llamadas == [], "sin grupo no hay alerta"


@_skip_db
def test_negative_ack_sin_id_cero_filas():
    """2xx sin id (`fallo:ack_sin_id`): no es applied, cero filas. Regla 9:
    el mutante que escribe con `verify == False` deja una fila aqui."""
    with db_f2("orbit_bib_neg3") as conn:
        setup = _grupo_listo(conn)
        _obs_negative(conn, setup["run"], setup["roles"]["category_phrase"]["ag"], TERMINO_A4)
        rol = "category_phrase"
        dec = _decision_negativa_grupo(conn, setup, rol, TERMINO_A4)
        res, _vistos, _h = _libera_negativa(
            conn, setup, dec, rol, TERMINO_A4, ack_negative_sin_id=True
        )
        assert res.aplicadas == 0 and res.fallidas == 1, res
        cola = conn.execute(
            "SELECT estado FROM apply_queue WHERE decision_id = %s", (dec,)
        ).fetchone()[0]
        assert cola == "failed", "cayo por el ack, no por descarte"
        assert _neg_bib(conn, TERMINO_NORM) == []


def _huerfana_negativa(conn, setup, rol: str, term: str) -> tuple[int, int]:
    """Fila `applying` huerfana kind negative sobre el ad group del rol,
    con su ledger sin sello (la reconciliacion la confirma o la falla)."""
    par = setup["roles"][rol]
    dec = _decision_negativa_grupo(conn, setup, rol, term)
    qid = _encola_cola(
        conn,
        dec,
        par["ag"],
        "negative",
        term=term,
        payload=_payload_negative(par["ag_ext"], par["camp_ext"], term),
    )
    _libera_fila(conn, qid)
    _claim_fila(conn, qid)
    _fila_ledger_abierta(conn, dec)
    return dec, qid


def _neg_vivo(ag_ext: str, camp_ext: str, kid: str, term: str) -> dict:
    return {
        "adGroupId": ag_ext,
        "campaignId": camp_ext,
        "keywordId": kid,
        "keywordText": term,
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }


@_skip_db
def test_reconciliacion_por_identidad_aprende():
    """Negativo huerfano `applying` CONFIRMADO por identidad en campana de
    grupo deja UNA fila con `origen` de decision. Regla 9: sin el
    cableado en la rama `propio is not None`, la fila no existe."""
    from app.apply_harvest import reconcilia_harvest

    with db_f2("orbit_bib_neg4") as conn:
        setup = _grupo_listo(conn)
        rol = "category_phrase"
        par = setup["roles"][rol]
        dec, qid = _huerfana_negativa(conn, setup, rol, TERMINO_A4)
        handler, _vistos = _handler_harvest(
            negatives=[_neg_vivo(par["ag_ext"], par["camp_ext"], "n-9", TERMINO_A4)]
        )
        resumen = reconcilia_harvest(
            conn, _aplicador(conn, handler, setup["ciclo_ejec"]), "amazon_us"
        )
        assert resumen.negativas_confirmadas == 1, resumen
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert cola == "applied"
        filas = _neg_bib(conn, TERMINO_NORM)
        assert len(filas) == 1, filas
        assert filas[0][4] == (f"grupo:{setup['grupo_id']}/campana:{par['camp']}/decision:{dec}"), (
            filas[0][4]
        )


@_skip_db
def test_reconciliacion_senuelo_cero_filas():
    """El termino existe SOLO en OTRO ad group (senuelo): `failed`, cero
    filas. Regla 9: confirmar por texto sin ad group dejaria una fila."""
    from app.apply_harvest import reconcilia_harvest

    with db_f2("orbit_bib_neg5") as conn:
        setup = _grupo_listo(conn)
        rol = "category_phrase"
        otro = "category_broad"
        dec, qid = _huerfana_negativa(conn, setup, rol, TERMINO_A4)
        sen = setup["roles"][otro]
        handler, _vistos = _handler_harvest(
            negatives=[_neg_vivo(sen["ag_ext"], sen["camp_ext"], "n-otro", TERMINO_A4)]
        )
        resumen = reconcilia_harvest(
            conn, _aplicador(conn, handler, setup["ciclo_ejec"]), "amazon_us"
        )
        assert resumen.negativas_fallidas == 1, resumen
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert cola == "failed"
        assert _neg_bib(conn, TERMINO_NORM) == []


@_skip_db
def test_reconciliacion_ausente_reintenta_aplicado_cero_filas():
    """Ausente en Amazon: la reconciliacion REINTENTA el POST y confirma
    `applied`... pero la rama ausente/reintento NO escribe biblioteca
    (brief: solo la confirmacion por identidad escribe). Regla 9: el
    mutante que escribe en la rama del reintento deja una fila aqui."""
    from app.apply_harvest import reconcilia_harvest

    with db_f2("orbit_bib_neg6") as conn:
        setup = _grupo_listo(conn)
        rol = "category_phrase"
        dec, qid = _huerfana_negativa(conn, setup, rol, TERMINO_A4)
        handler, vistos = _handler_harvest()
        resumen = reconcilia_harvest(
            conn, _aplicador(conn, handler, setup["ciclo_ejec"]), "amazon_us"
        )
        assert resumen.negativas_confirmadas == 1, resumen
        posts = [r for r in vistos if r.method == "POST" and r.url.path == "/sp/negativeKeywords"]
        assert len(posts) == 1, "el ausente reintenta el POST"
        cola = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (qid,)).fetchone()[0]
        assert cola == "applied"
        assert _neg_bib(conn, TERMINO_NORM) == [], "ausente/reintento no escribe"


@_skip_db
def test_precedencia_termino_ya_keyword_no_entra_como_exclusion():
    """Invariante (d), direccion negative: negative aplicado sobre un
    termino que ya esta en `keyword_biblioteca` NO entra a
    `negative_biblioteca` (un termino que ya vendio no se ensena como
    exclusion); el veredicto `applied` y el ledger `ok` quedan intactos.
    Regla 9: insertar sin la consulta previa dejaria la fila."""
    with db_f2("orbit_bib_prec") as conn:
        setup = _grupo_listo(conn)
        rastro = registra_keyword(
            conn,
            grupo_id=setup["grupo_id"],
            tipo_producto="collar_perro",
            platform="amazon_us",
            texto=TERMINO_A4,
            origen=f"grupo:{setup['grupo_id']}/semilla-precedencia",
        )
        assert rastro["escrita"] is True, rastro
        _obs_negative(conn, setup["run"], setup["roles"]["category_phrase"]["ag"], TERMINO_A4)
        rol = "category_phrase"
        dec = _decision_negativa_grupo(conn, setup, rol, TERMINO_A4)
        res, _vistos, _h = _libera_negativa(conn, setup, dec, rol, TERMINO_A4)
        assert res.aplicadas == 1, "el negative SI se aplica"
        assert _neg_bib(conn, TERMINO_NORM) == [], "no se ensena como exclusion"
        assert len(_kw_bib(conn)) == 1, "la keyword queda intacta"
        ledger = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'",
            (dec,),
        ).fetchone()
        assert ledger[0] == "ok", ledger


@_skip_db
def test_registra_negative_devuelve_motivo_sin_alertar(monkeypatch):
    """A nivel funcion: termino ya en `keyword_biblioteca` vuelve el dict
    exacto con el motivo y NO alerta (no es fallo)."""
    import app.notifica as _notifica

    with db_f2("orbit_bib_motivo") as conn:
        conn.execute(SQL_BIBLIOTECA_KEYWORD, ("zz_motivo", "amazon_us", "ka", "semilla"))
        llamadas: list = []
        monkeypatch.setattr(
            _notifica, "notifica_biblioteca_no_escrita", lambda **kw: llamadas.append(kw) or True
        )
        rastro = registra_negative(
            conn,
            grupo_id=1,
            tipo_producto="zz_motivo",
            platform="amazon_us",
            texto="KA",
            origen="semilla",
        )
        assert rastro == {"escrita": False, "motivo": MOTIVO_TERMINO_EN_KEYWORD_BIBLIOTECA}
        assert llamadas == [], "la precedencia no alerta"
        assert _neg_bib(conn, "ka") == []
        assert len(_kw_bib(conn, "ka")) == 1


# ---------------------------------------------------------------------------
# Bloque 5: fallo inyectado + rol
# ---------------------------------------------------------------------------


@_skip_db
def test_fallo_inyectado_keyword_sello_intacto(monkeypatch):
    """Fallo de biblioteca en el sello del harvest: job, cola, resumen y
    fase INTACTOS, cero filas, rastro `escrita: false` con motivo y UNA
    alerta veraz por el sender nuevo. Regla 9: sin el SAVEPOINT + catch,
    el sello aborta y este test cae en `done`/`applied`."""
    import app.notifica as _notifica

    with db_f2("orbit_bib_fallo1") as conn:
        setup = _grupo_listo(conn, term=TERMINO_A4)
        envios: list = []
        monkeypatch.setattr(_notifica, "canal_activo", lambda: True)
        monkeypatch.setattr(
            _notifica, "_envia_texto", lambda texto, transport=None: envios.append(texto) or True
        )
        import app.biblioteca as _bib

        monkeypatch.setattr(
            _bib, "SQL_BIBLIOTECA_KEYWORD", "INSERT INTO no_existe VALUES (%s, %s, %s, %s)"
        )
        corrido = _corre_harvest_grupo(conn, setup, term=TERMINO_A4)
        job = _job_de(conn, corrido["dec"])
        assert job["fase"] == "done", "el sello no se afecto"
        rastro = job["ext"].get("biblioteca")
        assert isinstance(rastro, dict) and rastro["escrita"] is False, rastro
        assert rastro["motivo"] == MOTIVO_BIBLIOTECA_FALLO, rastro
        assert rastro["detalle"] == "UndefinedTable", rastro
        assert _kw_bib(conn) == [], "la fila no se escribio"
        estado = conn.execute(
            "SELECT estado FROM apply_queue WHERE id = %s", (corrido["qid"],)
        ).fetchone()[0]
        assert estado == "applied"
        ver = conn.execute(
            "SELECT verify_ok FROM decision_application WHERE decision_id = %s",
            (corrido["dec"],),
        ).fetchone()[0]
        assert ver is True, "resumen confirmado"
        assert len(envios) == 1, "el sender nuevo se llamo una vez"
        assert "failed" not in envios[0].lower(), envios[0]
        assert "aplicado" in envios[0] and TERMINO_A4 in envios[0], envios[0]


@_skip_db
def test_fallo_inyectado_negative_veredicto_intacto(monkeypatch, caplog):
    """Fallo de biblioteca en el negative de cola: veredicto `applied`
    intacto, ledger `ok` (opcion (b): el formato no se toca), fallo en el
    log con scrub y UNA alerta veraz. Regla 9: sin el catch, el apply
    revienta en vez de sellar."""
    import logging

    import app.notifica as _notifica

    with db_f2("orbit_bib_fallo3") as conn:
        setup = _grupo_listo(conn)
        _obs_negative(conn, setup["run"], setup["roles"]["category_phrase"]["ag"], TERMINO_A4)
        envios: list = []
        monkeypatch.setattr(_notifica, "canal_activo", lambda: True)
        monkeypatch.setattr(
            _notifica, "_envia_texto", lambda texto, transport=None: envios.append(texto) or True
        )
        import app.biblioteca as _bib

        monkeypatch.setattr(
            _bib, "SQL_BIBLIOTECA_NEGATIVE", "INSERT INTO no_existe VALUES (%s, %s, %s, %s)"
        )
        rol = "category_phrase"
        dec = _decision_negativa_grupo(conn, setup, rol, TERMINO_A4)
        with caplog.at_level(logging.WARNING, logger="app.biblioteca"):
            res, _vistos, _h = _libera_negativa(conn, setup, dec, rol, TERMINO_A4)
        assert res.aplicadas == 1, "el negative SI quedo aplicado"
        ledger = conn.execute(
            "SELECT resultado FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'",
            (dec,),
        ).fetchone()
        assert ledger[0] == "ok", "opcion (b): el formato del ledger no se toca"
        assert _neg_bib(conn, TERMINO_NORM) == []
        assert len(envios) == 1, "el sender nuevo se llamo una vez"
        assert "failed" not in envios[0].lower(), envios[0]
        assert "aplicado" in envios[0] and TERMINO_A4 in envios[0], envios[0]
        linea = next(r.getMessage() for r in caplog.records if r.name == "app.biblioteca")
        assert MOTIVO_BIBLIOTECA_FALLO in linea and "aplicado" in linea, linea


@_skip_db
def test_rol_app_decide_escribe_y_prohibe():
    """DoD 7: bajo `SET ROLE app_decide`, las funciones de `app/`
    insertan, el segundo upsert del mismo termino actualiza `updated_at`
    sin duplicar ni pisar `origen`; `DELETE` y `UPDATE origen` truenan
    con `InsufficientPrivilege` en ambas tablas. `RESET ROLE` al salir."""
    with db_f2("orbit_bib_rol") as conn:
        conn.execute("SET ROLE app_decide")
        try:
            r1 = registra_keyword(
                conn,
                grupo_id=1,
                tipo_producto="zz_rol_a4",
                platform="amazon_us",
                texto="Termino Rol ",
                origen="grupo:1/test-rol",
            )
            assert r1["escrita"] is True and r1["id"] is not None, r1
            conn.execute(
                "UPDATE keyword_biblioteca SET updated_at = now() - interval '1 hour'"
                " WHERE id = %s",
                (r1["id"],),
            )
            marca = conn.execute(
                "SELECT updated_at FROM keyword_biblioteca WHERE id = %s", (r1["id"],)
            ).fetchone()[0]
            r2 = registra_keyword(
                conn,
                grupo_id=1,
                tipo_producto="zz_rol_a4",
                platform="amazon_us",
                texto="Termino Rol ",
                origen="grupo:1/otro-origen",
            )
            assert r2["escrita"] is True and r2["id"] == r1["id"], "upsert sin duplicar"
            fila = conn.execute(
                "SELECT origen, updated_at FROM keyword_biblioteca WHERE id = %s", (r1["id"],)
            ).fetchone()
            assert fila[0] == "grupo:1/test-rol", "origen no se pisa"
            assert fila[1] > marca, "updated_at se movio"
            rn = registra_negative(
                conn,
                grupo_id=1,
                tipo_producto="zz_rol_a4",
                platform="amazon_us",
                texto="Otro Termino Rol",
                origen="grupo:1/test-rol",
            )
            assert rn["escrita"] is True and rn["id"] is not None, rn
            for sql, params in (
                ("DELETE FROM keyword_biblioteca WHERE tipo_producto = %s", ("zz_rol_a4",)),
                (
                    "UPDATE keyword_biblioteca SET origen = 'x' WHERE id = %s",
                    (r1["id"],),
                ),
                ("DELETE FROM negative_biblioteca WHERE tipo_producto = %s", ("zz_rol_a4",)),
                (
                    "UPDATE negative_biblioteca SET origen = 'x' WHERE id = %s",
                    (rn["id"],),
                ),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sql, params)
        finally:
            conn.execute("RESET ROLE")
        assert conn.execute("SELECT current_user").fetchone()[0] != "app_decide"


@_skip_db
def test_grupo_resolver_por_identidad():
    """`grupo_de_ad_group`: por ad group directo, por `parent_id` de un
    segundo ad group de la campana (ambas ramas cruzan
    `campana_grupo_rol`) y None fuera de grupo. Regla 9: resolver por
    nombre de campana no pasa por aqui."""
    with db_f2("orbit_bib_gr") as conn:
        setup = _grupo_listo(conn)
        par = setup["roles"]["category_phrase"]
        gpo = grupo_de_ad_group(conn, par["ag"])
        assert gpo is not None, "ad group directo"
        assert gpo[0] == setup["grupo_id"] and gpo[1] == "collar_perro"
        assert gpo[2] == par["camp"]
        # Segundo ad group de la misma campana: no esta directo en el rol,
        # se resuelve por su campana via parent_id.
        ag2 = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
            " VALUES ('amazon_us', 'ad_group', '6299', %s) RETURNING id",
            (par["camp"],),
        ).fetchone()[0]
        gpo2 = grupo_de_ad_group(conn, ag2)
        assert gpo2 is not None and gpo2[0] == setup["grupo_id"], "rama parent_id"
        assert tipo_producto_de_grupo(conn, setup["grupo_id"]) == "collar_perro"
        assert tipo_producto_de_grupo(conn, 999999) is None
        assert grupo_de_ad_group(conn, 999999) is None


def test_statements_sin_dinero():
    """DoD 8 (parte estatica): ningun statement del motor menciona `cost`,
    `revenue`, `moneda` ni `orders` (la parte dinamica —filas con NULL—
    la asiertan los tests del sello). Regla 9: agregar `cost` al INSERT
    cae aqui aunque el GRANT lo deje pasar."""
    for statement in (SQL_BIBLIOTECA_KEYWORD, SQL_BIBLIOTECA_NEGATIVE):
        assert "cost" not in statement
        assert "revenue" not in statement
        assert "moneda" not in statement
        assert "orders" not in statement
