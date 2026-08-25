"""Tests de integración de la migración `migrations/0002_apply.sql` (ORBIT 04, task 1.2).

Patrón `_db_temporal` de test_cycle (COPIADO) con la diferencia de que aplica
0001 + 0002 juntas. Skip fail-closed `_postgres_obligatorio_ausente` de
test_schema: sin Postgres utilizable se skipea; en CI corre contra el
Postgres 16 del workflow quality.yml. DoD de la tarea, punto por punto:

1. INSERT directo en `released` revienta: la fila nace pending_veto por
   trigger (sellado 4).
2. Transición ilegal revienta en las 3 máquinas (apply_queue, harvest_job,
   apply_attempt); la cadena legal avanza completo.
3. SET ROLE app_decide NO veta (trigger current_user) pero SÍ avanza
   released -> applying; el veto corre como app_admin (sellados 4/18).
4. app_decide no inventa cap (INSERT con cap != config revienta; sin clave
   en la config VIGENTE revienta: fail-closed, sellado 8), no decrementa
   `used`, no toca cap/quota_date/motor; quota_date exige el DÍA UTC de la
   base, no el CURRENT_DATE de la sesión (r2 codex).
5. Dos cortes en vuelo de la misma CLAVE DE EFECTO chocan: pause con
   search_term NULL contra pause, y negative vs harvest del MISMO término
   (misma familia term_cut); el terminal libera la clave.
6. El ledger se sella UNA vez (por partes o junto); cualquier otro cambio de
   columna, el DELETE y el TRUNCATE revientan (excepción declarada).
7. GRANTs positivos probados CON EL ROL REAL (SET ROLE decide / admin /
   ingest / read — patrón test_cycle).
8. reactivacion_manual: INSERT idempotente por PK, solo app_decide escribe,
   y el candado append-only aguanta incluso a superuser.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager

import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import SQL, SQL2, _postgres_obligatorio_ausente, _test_dsn

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))

# Caps del día 1 (brief §5.5): 10 bids / 2 pauses / 5 negatives / 2 harvests
# por día y plataforma. Sirven de config vigente para el sello de quota.
CAPS_DIA_1 = {
    "ads_apply_cap_amazon_us_bid": 10,
    "ads_apply_cap_amazon_us_pause": 2,
    "ads_apply_cap_amazon_us_negative": 5,
    "ads_apply_cap_amazon_us_harvest": 2,
    "ads_apply_cap_amazon_mx_bid": 10,
    "ads_apply_cap_amazon_mx_pause": 2,
    "ads_apply_cap_amazon_mx_negative": 5,
    "ads_apply_cap_amazon_mx_harvest": 2,
}


# ---------------------------------------------------------------------------
# Patron _db_temporal de test_cycle (COPIADO; aplica 0001 + 0002)
# ---------------------------------------------------------------------------


@contextmanager
def _db_temporal(prefijo: str):
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # 0001: roles, esquema sellado, grants
        conn.execute(SQL2)  # 0002: cola de cortes, ledger, sellos de quota
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# Seeds minimos: decision exige ciclo/config/entidad (helpers test_cycle)
# ---------------------------------------------------------------------------


def _entidad(conn, kind: str, external: str, parent=None, **extra) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type, keyword_text)"
        " VALUES ('amazon_us', %s, %s, %s, %s, %s) RETURNING id",
        (kind, external, parent, extra.get("match_type"), extra.get("keyword_text")),
    ).fetchone()[0]


def _decision(
    conn, ciclo: int, config_id: int, entidad: int, kind: str, *, term=None, valor=None, moneda=None
) -> int:
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " data_observed_at, window_start, window_end, search_term, new_value, value_currency,"
        " inputs) VALUES (%s, %s, %s, %s, now() - interval '40 days', CURRENT_DATE - 60,"
        " CURRENT_DATE - 30, %s, %s, %s, '{}'::jsonb) RETURNING id",
        (ciclo, entidad, kind, config_id, term, valor, moneda),
    ).fetchone()[0]


def _semilla(conn) -> dict:
    """Config con caps del día 1 + dos ciclos + entidad keyword y ad_group con
    decisiones maduras (window_end D-30): pause x2 (misma kw, ciclos
    distintos), negative/harvest del MISMO término (ciclos distintos) y una
    negative + harvest de términos libres para harvest_job."""
    config_id = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-0002', %s) RETURNING id",
        (Json(CAPS_DIA_1),),
    ).fetchone()[0]
    ciclo1 = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    ciclo2 = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    camp = _entidad(conn, "campaign", "7001")
    ag = _entidad(conn, "ad_group", "7101", parent=camp)
    kw = _entidad(conn, "keyword", "7201", parent=ag, match_type="EXACT", keyword_text="kw apply")
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status, synced_at)"
        " VALUES (%s, 1.00, 'USD', 'ENABLED', now())",
        (kw,),
    )
    return {
        "config_id": config_id,
        "ciclo1": ciclo1,
        "ciclo2": ciclo2,
        "camp": camp,
        "ag": ag,
        "kw": kw,
        "dec_pause": _decision(conn, ciclo1, config_id, kw, "pause"),
        "dec_pause2": _decision(conn, ciclo2, config_id, kw, "pause"),
        # negative y harvest del MISMO término: en decision son excluyentes
        # por término y ciclo, así que viven en ciclos distintos — en la cola
        # CHOCAN por clave de efecto (misma familia term_cut, sellado 4).
        "dec_neg": _decision(conn, ciclo1, config_id, ag, "negative", term="zapato blanco"),
        "dec_harv": _decision(
            conn, ciclo2, config_id, ag, "harvest", term="zapato blanco", valor=0.75, moneda="USD"
        ),
        # términos libres para no chocar con la clave de efecto de arriba
        "dec_neg2": _decision(conn, ciclo2, config_id, ag, "negative", term="otro termino"),
        "dec_harv2": _decision(
            conn, ciclo1, config_id, ag, "harvest", term="buen termino", valor=0.75, moneda="USD"
        ),
    }


def _encolar(
    conn,
    dec_id: int,
    entidad: int,
    kind: str,
    *,
    term=None,
    modo="live",
    estado="pending_veto",
    vence=None,
) -> int:
    return conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term, decision_id,"
        " modo, estado, vence_el, request_payload) VALUES ('amazon_us', %s, %s, %s, %s, %s, %s,"
        " %s, '{}'::jsonb) RETURNING id",
        (
            entidad,
            kind,
            term,
            dec_id,
            modo,
            estado,
            vence or dt.datetime.now(dt.UTC) + dt.timedelta(hours=48),
        ),
    ).fetchone()[0]


def _avanzar(conn, q: int, estado: str) -> None:
    """UPDATE de transición con el timestamp de la fase destino (como la app)."""
    sello = {
        "released": "released_at",
        "applying": "applying_at",
        "applied": "applied_at",
        "failed": "failed_at",
        "vetoed": "vetoed_at",
        "discarded": "discarded_at",
    }[estado]
    conn.execute(
        f"UPDATE apply_queue SET estado = %s, {sello} = now() WHERE id = %s",
        (estado, q),
    )


def _zona_pg_con_otro_dia() -> str | None:
    """Offset POSIX para `SET TIME ZONE` cuya CURRENT_DATE difiere del día UTC
    (r2 codex: un DATE sin zona validado contra CURRENT_DATE duplicaría el cap
    con sesiones en otra TZ). SIGNO VERIFICADO EN VIVO contra el PG16 del
    server (hallazgo del primer CI real: estaba invertido — en el bare number
    de SET TIME ZONE, '13' ES UTC+13 y '-11' ES UTC-11): pasado el flanco de
    las 11:00 UTC hay que irse a UTC+13 (ya pasó de día); antes, a UTC-11
    (sigue en ayer). El flanco de cambio de ambos es 11:00 UTC: cerca de ese
    borde el test se declara y skipea en vez de arriesgar flake."""
    ahora = dt.datetime.now(dt.UTC)
    minutos = ahora.hour * 60 + ahora.minute
    if abs(minutos - 11 * 60) < 5:
        return None
    if minutos > 11 * 60:
        return "13"  # UTC+13: ya pasó de día
    return "-11"  # UTC-11: sigue en ayer


# ---------------------------------------------------------------------------
# 1. Nace pending_veto: INSERT directo en released revienta
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_nace_pending_veto_y_insert_en_released_revienta():
    with _db_temporal("orbit_apply_nace") as conn:
        ids = _semilla(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            _encolar(conn, ids["dec_pause"], ids["kw"], "pause", estado="released")
        q = _encolar(conn, ids["dec_pause"], ids["kw"], "pause")
        fila = conn.execute(
            "SELECT estado, familia, search_term, modo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        # familia es GENERATED: deriva del kind (pause -> entity_cut) y el
        # search_term queda NULL por el CHECK de coherencia.
        assert fila == ("pending_veto", "entity_cut", None, "live")


# ---------------------------------------------------------------------------
# 2. Maquina de estados: ilegal revienta, la cadena legal avanza
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_maquina_de_estados_transiciones_legales_e_ilegales():
    with _db_temporal("orbit_apply_fsm") as conn:
        ids = _semilla(conn)
        q = _encolar(conn, ids["dec_pause"], ids["kw"], "pause")

        # Desde pending_veto SOLO vetoed/released/discarded (brief §1.2).
        for destino in ("applying", "applied", "failed"):
            with pytest.raises(psycopg.errors.CheckViolation, match=destino):
                conn.execute("UPDATE apply_queue SET estado = %s WHERE id = %s", (destino, q))

        # La cadena legal completa hasta terminal.
        _avanzar(conn, q, "released")
        _avanzar(conn, q, "applying")

        # NO existe applying -> discarded (la quota no se quema en descartes).
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE apply_queue SET estado = 'discarded', discard_motivo = 'x' WHERE id = %s",
                (q,),
            )
        _avanzar(conn, q, "applied")

        # Terminales INMUTABLES.
        for destino in ("vetoed", "released", "applying", "failed", "discarded"):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("UPDATE apply_queue SET estado = %s WHERE id = %s", (destino, q))

        # Todo UPDATE de la cola ES una transición: nada de updates in-place.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE apply_queue SET discard_motivo = 'nota' WHERE id = %s", (q,))


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_fila_shadow_jamas_libera_perimetro_veto_descartar():
    """Sellado 6 (hallazgo reviewer r1 de 1.2): una fila shadow JAMAS
    transiciona a released — ni a applying/applied/failed (de una fila shadow
    no sale HTTP). Candado de SCHEMA (el trigger condiciona por modo): su
    perímetro es vetoed (práctica del dueño; corre como admin) o discarded
    (flip de ORBIT 05; corre como admin). Regla 9: sin el guard, el UPDATE a
    released de una fila shadow pasaría y el test reventaría."""
    with _db_temporal("orbit_apply_shadow") as conn:
        ids = _semilla(conn)
        q = _encolar(conn, ids["dec_pause"], ids["kw"], "pause", modo="shadow")

        for destino in ("released", "applying", "applied", "failed"):
            with pytest.raises(psycopg.errors.CheckViolation, match="shadow"):
                conn.execute("UPDATE apply_queue SET estado = %s WHERE id = %s", (destino, q))

        # El perímetro LEGAL de una fila shadow: discard (corre como admin,
        # como el flip de ORBIT 05) y veto (admin, práctica del dueño).
        conn.execute("SET ROLE app_admin")
        conn.execute(
            "UPDATE apply_queue SET estado = 'discarded', discarded_at = now(),"
            " discard_motivo = 'flip' WHERE id = %s",
            (q,),
        )
        conn.execute("RESET ROLE")
        q2 = _encolar(
            conn, ids["dec_neg"], ids["ag"], "negative", term="zapato blanco", modo="shadow"
        )
        conn.execute("SET ROLE app_admin")
        conn.execute(
            "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(), vetoed_by = 'dueno'"
            " WHERE id = %s",
            (q2,),
        )
        conn.execute("RESET ROLE")
        estados = conn.execute(
            "SELECT estado FROM apply_queue WHERE id IN (%s, %s) ORDER BY id", (q, q2)
        ).fetchall()
        assert [e[0] for e in estados] == ["discarded", "vetoed"]


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_discard_de_fila_shadow_exige_admin_pero_el_motor_descarta_live():
    """Hallazgo post-merge PR #25 (greptile P1): el GRANT del motor incluye
    estado/discarded_at/discard_motivo, así que sin candado en el trigger el
    motor podía ejecutar él solo el flip de ORBIT 05 (discard de filas
    shadow, brief §12 — ceremonia admin): borrar la cola de práctica de veto
    del dueño y liberar sus claves de efecto. Regla 9: sin el candado, el
    primer UPDATE pasa y el test revienta. En el MISMO test se asserta la
    otra dirección (que el candado no sobre-bloquee): el discard del motor
    sobre filas LIVE (re-validación fallida, brief §1.2) sigue pasando."""
    with _db_temporal("orbit_apply_shadow_discard") as conn:
        ids = _semilla(conn)
        q = _encolar(conn, ids["dec_pause"], ids["kw"], "pause", modo="shadow")

        conn.execute("SET ROLE app_decide")
        try:
            # El motor tiene el GRANT de estas TRES columnas: solo el trigger
            # (rol REAL via current_user) puede pararlo.
            with pytest.raises(psycopg.errors.RestrictViolation, match="admin"):
                conn.execute(
                    "UPDATE apply_queue SET estado = 'discarded',"
                    " discarded_at = now(), discard_motivo = 'motor' WHERE id = %s",
                    (q,),
                )
        finally:
            conn.execute("RESET ROLE")
        fila = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()
        assert fila[0] == "pending_veto"  # la práctica de veto sigue intacta

        # Dirección discriminante: la fila LIVE sí la descarta el MOTOR
        # (re-validación fallida) — el candado es por modo, no por estado.
        q2 = _encolar(conn, ids["dec_neg"], ids["ag"], "negative", term="zapato blanco")
        conn.execute("SET ROLE app_decide")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'discarded', discarded_at = now(),"
                " discard_motivo = 're-validacion fallida' WHERE id = %s",
                (q2,),
            )
        finally:
            conn.execute("RESET ROLE")
        fila2 = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q2,)).fetchone()
        assert fila2[0] == "discarded"

        # Y el flip real (admin) sobre la shadow sí procede.
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'discarded', discarded_at = now(),"
                " discard_motivo = 'flip' WHERE id = %s",
                (q,),
            )
        finally:
            conn.execute("RESET ROLE")
        fila3 = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()
        assert fila3[0] == "discarded"


# ---------------------------------------------------------------------------
# 3. Veto exige admin con el ROL REAL; el motor claima
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_veto_exige_admin_y_el_motor_si_avanza():
    with _db_temporal("orbit_apply_veto") as conn:
        ids = _semilla(conn)
        q = _encolar(conn, ids["dec_pause"], ids["kw"], "pause")
        _avanzar(conn, q, "released")

        conn.execute("SET ROLE app_decide")
        try:
            # vence_el es del ADMIN: el motor ni lo toca (r2 grok 7: el mismo
            # hueco que la ronda 1 cerró para quota).
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE apply_queue SET vence_el = now() WHERE id = %s", (q,))
            # Primera capa: el veto COMPLETO (actor + bloqueo) ni siquiera
            # pasa los permisos — esas columnas son del admin.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                    " vetoed_by = 'dueno', vence_el = now() + interval '30 days'"
                    " WHERE id = %s",
                    (q,),
                )
            # Segunda capa, la que no depende de un GRANT: el veto MÍNIMO con
            # las columnas que el motor SÍ tiene (estado) revienta igual en
            # el trigger — el rol del motor NO veta ni siquiera con el UPDATE
            # del claim (sellado 4/18).
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute("UPDATE apply_queue SET estado = 'vetoed' WHERE id = %s", (q,))
            # El claim del motor SÍ pasa: released -> applying.
            _avanzar(conn, q, "applying")
            fila = conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()
            assert fila[0] == "applying"
            # Cerrar q a terminal ANTES de encolar q2 (mismo kw, misma clave
            # de efecto): un en-vuelo bloquearía el INSERT de q2 con
            # UniqueViolation — el candado FUNCIONANDO, pero estropearía el
            # resto del test (hallazgo del primer CI real).
            _avanzar(conn, q, "applied")
        finally:
            conn.execute("SET ROLE NONE")

        # El veto corre como admin (endpoint con DSN admin, sellado 18) y
        # vence_el queda editable AL VETAR (bloqueo durable 30d, sellado 3).
        # Se prueba sobre OTRA fila en released: applying es punto de no
        # retorno y NO es vetable (r2 grok).
        q2 = _encolar(conn, ids["dec_pause2"], ids["kw"], "pause")
        _avanzar(conn, q2, "released")
        with pytest.raises(psycopg.errors.CheckViolation, match="en vuelo|applying"):
            conn.execute("UPDATE apply_queue SET estado = 'vetoed' WHERE id = %s", (q,))
        conn.execute("SET ROLE app_admin")
        try:
            conn.execute(
                "UPDATE apply_queue SET estado = 'vetoed', vetoed_at = now(),"
                " vetoed_by = 'dueno', vence_el = now() + interval '30 days' WHERE id = %s",
                (q2,),
            )
            fila = conn.execute(
                "SELECT estado, vetoed_by FROM apply_queue WHERE id = %s", (q2,)
            ).fetchone()
            assert fila == ("vetoed", "dueno")
            # vetoed es terminal: ni el admin lo mueve.
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("UPDATE apply_queue SET estado = 'released' WHERE id = %s", (q2,))
        finally:
            conn.execute("SET ROLE NONE")


# ---------------------------------------------------------------------------
# 4. Quota sellada: cap solo desde config, dia UTC de la base, used creciente
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_quota_fail_closed_cap_desde_config_dia_utc_y_used_creciente():
    with _db_temporal("orbit_apply_quota") as conn:
        _semilla(conn)  # config vigente con los caps del día 1
        motor = "ads_optimizer:amazon_us:pause"
        hoy_utc = "(now() AT TIME ZONE 'UTC')::date"

        # La fila del día nace SOLO con el cap de la config (2 en día 1).
        conn.execute(
            f"INSERT INTO apply_quota_state (motor, quota_date, cap)"
            f" VALUES ('{motor}', {hoy_utc}, 2)"
        )

        # El motor no inventa cap: otro valor revienta.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"INSERT INTO apply_quota_state (motor, quota_date, cap)"
                f" VALUES ('ads_optimizer:amazon_us:negative', {hoy_utc}, 99)"
            )

        # Vocabulario CERRADO: plataforma o kind fuera del mapa revienta.
        for motor_raro in ("ads_optimizer:meli:pause", "motor_raro", "ads_optimizer:amazon_us"):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    f"INSERT INTO apply_quota_state (motor, quota_date, cap)"
                    f" VALUES ('{motor_raro}', {hoy_utc}, 2)"
                )

        # quota_date = día UTC de la BASE: ayer revienta...
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO apply_quota_state (motor, quota_date, cap)"
                " VALUES ('ads_optimizer:amazon_us:negative', CURRENT_DATE - 1, 5)"
            )
        # ... y el CURRENT_DATE de una sesión en OTRA zona también (r2 codex:
        # DATE sin zona + sesiones con TZ distinta duplicaban el cap).
        zona = _zona_pg_con_otro_dia()
        if zona is None:
            pytest.skip("a menos de 5 min del flanco de cambio de día UTC: declarado, sin flake")
        conn.execute(f"SET TIME ZONE {zona}")
        try:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO apply_quota_state (motor, quota_date, cap)"
                    " VALUES ('ads_optimizer:amazon_us:negative', CURRENT_DATE, 5)"
                )
        finally:
            conn.execute("SET TIME ZONE 'UTC'")

        # used jamás decrece; cap/quota_date/motor inmutables por UPDATE
        # (en 0001 "used nunca decrece" no era enforceable — 0002 lo sella).
        conn.execute("UPDATE apply_quota_state SET used = 1 WHERE motor = %s", (motor,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE apply_quota_state SET used = 0 WHERE motor = %s", (motor,))
        for cambio in (
            "cap = 99",
            "quota_date = CURRENT_DATE - 1",
            "motor = 'ads_optimizer:amazon_us:bid'",
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    f"UPDATE apply_quota_state SET {cambio} WHERE motor = %s",
                    (motor,),
                )

        # FAIL-CLOSED: la config VIGENTE manda, no cualquiera. Una config
        # nueva (más reciente) SIN las claves apaga TODO: sin clave no nace
        # fila, ni siquiera con el cap que la config vieja tenía.
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('sin caps', '{}'::jsonb)"
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"INSERT INTO apply_quota_state (motor, quota_date, cap)"
                f" VALUES ('ads_optimizer:amazon_mx:harvest', {hoy_utc}, 2)"
            )


# ---------------------------------------------------------------------------
# 5. Clave de efecto: dos cortes en vuelo chocan; el terminal libera
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_clave_de_efecto_choques_pause_null_y_negative_vs_harvest():
    with _db_temporal("orbit_apply_clave") as conn:
        ids = _semilla(conn)

        # Dos pauses de la MISMA entidad (search_term NULL): sin NULLS NOT
        # DISTINCT no chocarían — el parcial los atrapa (sellado 4).
        q1 = _encolar(conn, ids["dec_pause"], ids["kw"], "pause")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _encolar(conn, ids["dec_pause2"], ids["kw"], "pause")

        # negative vs harvest del MISMO término chocan: misma familia
        # term_cut aunque el kind sea distinto (con kind en la clave, un veto
        # de negative se eludía proponiendo harvest del mismo término, r2).
        _encolar(conn, ids["dec_neg"], ids["ag"], "negative", term="zapato blanco")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _encolar(conn, ids["dec_harv"], ids["ag"], "harvest", term="zapato blanco")

        # Clave distinta avanza (el bloqueo es por clave de efecto).
        _encolar(conn, ids["dec_neg2"], ids["ag"], "negative", term="otro termino")

        # El terminal libera la clave: descartada la primera (con su nota, EN
        # LA MISMA transición — todo UPDATE de la cola es una transición), un
        # pause nuevo de la misma entidad vuelve a encolarse.
        conn.execute(
            "UPDATE apply_queue SET estado = 'discarded', discarded_at = now(),"
            " discard_motivo = 're-validacion' WHERE id = %s",
            (q1,),
        )
        _encolar(conn, ids["dec_pause2"], ids["kw"], "pause")


# ---------------------------------------------------------------------------
# 6. Ledger: sello UNA vez; todo lo demas y DELETE/TRUNCATE revientan
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ledger_sello_una_vez_y_mutaciones_revientan():
    with _db_temporal("orbit_apply_ledger") as conn:
        ids = _semilla(conn)
        a = conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (%s, 1, 'normal', '{}'::jsonb, true) RETURNING id",
            (ids["dec_pause"],),
        ).fetchone()[0]
        # Los probes nacen SIN decisión (decision_id NULL SOLO si probe).
        conn.execute(
            "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)"
            " VALUES (NULL, 1, 'probe', '{}'::jsonb, false)"
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada) VALUES (NULL, 1, 'normal', '{}'::jsonb, true)"
            )

        # El sello: NULL -> valor, UNA vez, por partes o junto.
        conn.execute("UPDATE apply_attempt SET ack = '{\"ok\": true}'::jsonb WHERE id = %s", (a,))
        conn.execute(
            "UPDATE apply_attempt SET resultado = 'ok', finished_at = now() WHERE id = %s", (a,)
        )
        # Re-sello o cambio: revienta (excepción deliberada, candado acotado).
        for cambio in (
            "ack = '{\"otro\": true}'::jsonb",
            "resultado = 'reintento'",
            "finished_at = now()",
            "request_payload = '{\"x\": 1}'::jsonb",
            "quota_cobrada = false",
            "seq = 2",
        ):
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute(f"UPDATE apply_attempt SET {cambio} WHERE id = %s", (a,))
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM apply_attempt WHERE id = %s", (a,))
        # Los triggers de fila no se disparan con TRUNCATE: capa de sentencia.
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE apply_attempt")


# ---------------------------------------------------------------------------
# 7. harvest_job: progresion sellada (sin saltos ni retrocesos)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_harvest_job_progresion_sin_saltos_ni_retrocesos():
    with _db_temporal("orbit_apply_harvest") as conn:
        ids = _semilla(conn)

        def _job(dec, term):
            return conn.execute(
                "INSERT INTO harvest_job (decision_id, search_term, platform, ad_entity_id,"
                " fase) VALUES (%s, %s, 'amazon_us', %s, 'pending') RETURNING id",
                (dec, term, ids["ag"]),
            ).fetchone()[0]

        j = _job(ids["dec_harv"], "zapato blanco")
        # Regresión 0001: el job nace pending (INSERT en otra fase revienta).
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO harvest_job (decision_id, search_term, platform, ad_entity_id,"
                " fase) VALUES (%s, 'buen termino', 'amazon_us', %s, 'done')",
                (ids["dec_harv2"], ids["ag"]),
            )

        # Salto prohibido: pending -> exact_created se salta negative_created.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE harvest_job SET fase = 'exact_created' WHERE id = %s", (j,))

        # La cadena completa avanza (external_ids acumula por el camino).
        conn.execute(
            "UPDATE harvest_job SET fase = 'negative_created',"
            ' external_ids = \'{"negative": "n1"}\'::jsonb WHERE id = %s',
            (j,),
        )
        conn.execute(
            "UPDATE harvest_job SET fase = 'exact_created',"
            ' external_ids = \'{"negative": "n1", "keyword": "k1"}\'::jsonb WHERE id = %s',
            (j,),
        )
        conn.execute("UPDATE harvest_job SET fase = 'done' WHERE id = %s", (j,))

        # done es terminal: ni retroceso ni re-ejecución.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE harvest_job SET fase = 'pending' WHERE id = %s", (j,))

        # failed alcanzable desde fase en vuelo (matriz §6.1: fallo
        # definitivo -> failed + reversa), y terminal igual.
        j2 = _job(ids["dec_harv2"], "buen termino")
        conn.execute("UPDATE harvest_job SET fase = 'negative_created' WHERE id = %s", (j2,))
        conn.execute("UPDATE harvest_job SET fase = 'failed' WHERE id = %s", (j2,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE harvest_job SET fase = 'exact_created' WHERE id = %s", (j2,))


# ---------------------------------------------------------------------------
# 8. GRANTs con el ROL REAL + reactivacion_manual
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_grants_con_rol_real_y_reactivacion_manual():
    with _db_temporal("orbit_apply_grants") as conn:
        ids = _semilla(conn)
        q = _encolar(conn, ids["dec_pause"], ids["kw"], "pause")

        conn.execute("SET ROLE app_decide")
        try:
            # LO QUE SÍ: el motor encola, escribe ledger y detecciones,
            # actualiza el cache con el readback y sella el resumen.
            _encolar(conn, ids["dec_neg2"], ids["ag"], "negative", term="otro termino")
            conn.execute(
                "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
                " quota_cobrada) VALUES (%s, 1, 'normal', '{}'::jsonb, true)",
                (ids["dec_pause"],),
            )
            # INSERT idempotente por PK (la escribe el APLICADOR, sellado 17).
            for _ in range(2):
                conn.execute(
                    "INSERT INTO reactivacion_manual (ad_entity_id) VALUES (%s)"
                    " ON CONFLICT DO NOTHING",
                    (ids["kw"],),
                )
            conn.execute(
                "UPDATE ad_entity_state SET current_bid = 0.90, status = 'ENABLED',"
                " synced_at = now() WHERE ad_entity_id = %s",
                (ids["kw"],),
            )
            conn.execute(
                "INSERT INTO decision_application (decision_id) VALUES (%s)", (ids["dec_pause"],)
            )
            conn.execute(
                "UPDATE decision_application SET applied_cycle_id = %s WHERE decision_id = %s",
                (ids["ciclo1"], ids["dec_pause"]),
            )
            _avanzar(conn, q, "released")
            # El INSERT de quota ya lo tenía de 0001; el sello del trigger
            # hace el resto (cap del día copiado de la config vigente).
            conn.execute(
                "INSERT INTO apply_quota_state (motor, quota_date, cap)"
                " VALUES ('ads_optimizer:amazon_us:pause', (now() AT TIME ZONE 'UTC')::date, 2)"
            )
        finally:
            conn.execute("SET ROLE NONE")

        conn.execute("SET ROLE app_ingest")
        try:
            # LO QUE NO: la ingesta no escribe la cola ni el ledger ni
            # detecciones de reactivación. La primera SQL lleva DOS params
            # (entidad y decisión); el resto uno.
            for sql, params in (
                (
                    "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo,"
                    " estado, vence_el, request_payload) VALUES ('amazon_us', %s, 'pause', %s,"
                    " 'live', 'pending_veto', now(), '{}'::jsonb)",
                    (ids["kw"], ids["dec_pause2"]),
                ),
                (
                    "INSERT INTO apply_attempt (seq, tipo, request_payload, quota_cobrada)"
                    " VALUES (1, 'probe', '{}'::jsonb, false)",
                    (),
                ),
                ("INSERT INTO reactivacion_manual (ad_entity_id) VALUES (%s)", (ids["kw"],)),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sql, params)
        finally:
            conn.execute("SET ROLE NONE")

        conn.execute("SET ROLE app_read")
        try:
            assert conn.execute("SELECT count(*) FROM apply_queue").fetchone()[0] == 2
            assert conn.execute("SELECT count(*) FROM apply_attempt").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM reactivacion_manual").fetchone()[0] == 1
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo,"
                    " estado, vence_el, request_payload) VALUES ('amazon_us', %s, 'pause', %s,"
                    " 'live', 'pending_veto', now(), '{}'::jsonb)",
                    (ids["kw"], ids["dec_pause2"]),
                )
        finally:
            conn.execute("SET ROLE NONE")

        # reactivacion_manual es un hecho PURO: el candado append-only aguanta
        # incluso a superuser (no depende de la ausencia de un GRANT).
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE reactivacion_manual SET detectada_en = now() - interval '7 days'")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM reactivacion_manual")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE reactivacion_manual")
