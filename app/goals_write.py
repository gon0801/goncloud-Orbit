"""Escritura amigable de goals del optimizador (ORBIT 04, task 3.2; sellado 26).

UN solo camino (regla 1): la escritura de `ads_optimizer_goal` vive SOLO en
este modulo: `edita_goal` (UPDATE) y `crea_goal` (INSERT, FABRICA 01); el
endpoint POST `/api/ads-optimizer/goals/{goal_id}` (app/api_write.py, auth
sellada 18), el CLI `goals set` (app/cli.py) y los tools despachan, jamas
duplican SQL (candado en tests/test_architecture.py). Los goals los escribe
`app_admin` (GRANT de 0001); el motor solo lee (`_SQL_GOALS` de app/cycle) y
la edicion es visible al ciclo siguiente porque la decision congela en
`inputs` lo que leyo (regla 2: la
fuente es la fila, no un cache).

CONTRATO DEL UPDATE (dashboard-01 r2): `updated_at` es parametro OBLIGATORIO
y viaja EXPLICITO en el SET — NO hay trigger que lo mantenga, omitirlo seria
escribir historia falsa (fila editada con sello viejo). Quien llama pasa el
now() UTC de SU reloj (endpoint: reloj del servidor; CLI: reloj del operador).

PRE-VALIDACION antes del UPDATE, con mensajes en espanol, combinando los
valores NUEVOS con los EXISTENTES de la fila (un floor nuevo puede chocar con
el ceiling viejo): floor/ceiling positivos y floor <= ceiling, target > 0,
harvest_default_bid > 0 o NULL, y la terna harvest all-or-nothing del CHECK
`goal_harvest_completo` (tras aplicar los cambios, o los TRES son NULL o los
TRES no-NULL; `harvest_limpia` pone los tres a NULL y JAMAS se combina con
campos harvest individuales; `harvest_limpia_destino` pone a NULL SOLO
campaign/ad_group —bid intacto— y exige campana en grupo, validado DESPUES
de leer la fila; el CHECK actual lo rechaza hasta que A.2 lo reemplace por
el trigger que admite bid-solo en grupo, asi que el camino feliz solo corre
con 00NN aplicada). El objetivo es doble: mensaje claro de USO
(422/exit 2) y no quemar la validez del CHECK en la base para un error de
operador. `None` en un parametro significa "no cambiar" — el unico camino a
NULL de la terna harvest es `harvest_limpia` (regla 3: faltante no es cero).

DEFAULTS POR MONEDA (ORBIT 05 preflight 1.2): si el estado efectivo del
goal queda con `bid_floor`/`bid_ceiling` ausentes, se resuelven con
`DEFAULTS_POR_MONEDA` (app.optimizer.goals) de LA MONEDA DE LA FILA antes
de persistir — un goal MXN jamas se guarda con 0.10/2.50 implicitos; moneda
desconocida -> ValueError y el UPDATE no se ejecuta.

VALIDACION DE ENTRADA pura (review 3.2, hallazgos #1-#3) ANTES de leer la
fila, sin I/O: cero campos = edicion invalida (un UPDATE que solo mueva
updated_at es un rastro que miente en espiritu), cadenas vacias o de puros
espacios en los ids harvest NO cuentan como "presentes" para la terna (regla
3), y los Decimales pasados deben ser finitos (Infinity pasa el gt=0 y las
comparaciones; NaN las esquiva; PG16 acepta ambos en NUMERIC).

EXIT CODES del CLI (eleccion sellada aqui): GoalInvalido = exit 2 (uso
invalido, patron argparse); GoalInexistente = exit 1 (fallo contra la base).

Dinero como STRING en la respuesta (regla 4, mismo `_dec_str` y mismos
nombres de campo que GET /goals de app/api.py).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from app.api_common import _dec_str
from app.optimizer.goals import resuelve_floor_ceiling


class GoalInexistente(Exception):
    """El goal_id no existe (404 en el endpoint; exit 1 en el CLI)."""


class GoalInvalido(Exception):
    """La combinacion nuevo+existente viola un CHECK del schema (422 en el
    endpoint; exit 2 en el CLI: uso invalido). El mensaje ES el motivo que
    ve el operador, en espanol."""


# Columnas de la fila completa (lectura y RETURNING): mismo orden/nombres que
# GET /goals de app/api.py — la respuesta de la edicion es EL MISMO shape.
_COLUMNAS = (
    "id, scope, ad_entity_id, platform, target_acos_pct, bid_floor, bid_ceiling,"
    " bid_currency, harvest_campaign_id, harvest_ad_group_id, harvest_default_bid,"
    " enabled, mode, created_at, updated_at"
)

_SQL_LEE = f"SELECT {_COLUMNAS} FROM ads_optimizer_goal WHERE id = %s"


def _fila_respuesta(fila: dict) -> dict:
    """Fila (dict_row) -> respuesta con dinero str() (regla 4; mismo shape que
    GET /goals: mismas claves, misma escala de strings)."""
    return {
        "id": fila["id"],
        "scope": fila["scope"],
        "ad_entity_id": fila["ad_entity_id"],
        "platform": fila["platform"],
        "target_acos_pct": _dec_str(fila["target_acos_pct"]),
        "bid_floor": _dec_str(fila["bid_floor"]),
        "bid_ceiling": _dec_str(fila["bid_ceiling"]),
        "bid_currency": fila["bid_currency"],
        "harvest_campaign_id": fila["harvest_campaign_id"],
        "harvest_ad_group_id": fila["harvest_ad_group_id"],
        "harvest_default_bid": _dec_str(fila["harvest_default_bid"]),
        "enabled": fila["enabled"],
        "mode": fila["mode"],
        "created_at": fila["created_at"],
        "updated_at": fila["updated_at"],
    }


def _valida_limpia_destino_post_lectura(conn: psycopg.Connection, fila: dict) -> None:
    """Validacion DESPUES de leer la fila para `harvest_limpia_destino`: el
    goal debe ser de scope `campaign` y su campana estar en
    `campana_grupo_rol` (su destino lo resuelve el grupo, no la terna; sin
    grupo quedaria en `sin_destino_de_harvest` en silencio)."""
    if fila["scope"] != "campaign" or fila["ad_entity_id"] is None:
        raise GoalInvalido(
            f"harvest_limpia_destino exige un goal de scope campaign: llego scope {fila['scope']}"
        )
    en_grupo = conn.execute(
        "SELECT 1 FROM campana_grupo_rol WHERE ad_entity_id = %s",
        (fila["ad_entity_id"],),
    ).fetchone()
    if en_grupo is None:
        raise GoalInvalido(
            "harvest_limpia_destino exige que la campana este en campana_grupo_rol:"
            " sin grupo el destino quedaria en sin_destino_de_harvest"
        )


def _valida_pre_editar(fila: dict, cambios: dict[str, object], *, permite_bid_solo=False) -> None:
    """Pre-validacion en espanol sobre el estado EFECTIVO (nuevos + existentes).

    Corre ANTES del UPDATE: un choke aqui es un 422/exit-2 con mensaje claro,
    no un CheckViolation crudo de la base. Espeja los CHECKs del schema
    (goal_piso_bajo_techo, goal_bids_positivos, goal_target_acos_positivo,
    goal_harvest_bid_positivo, goal_harvest_completo). `permite_bid_solo`
    (FABRICA 02, `harvest_limpia_destino`): salta el all-or-nothing de la
    terna cuando campaign/ad_group van a NULL con bid intacto (el trigger
    de A.2 lo admite solo en grupo; sin 00NN el UPDATE lo rechaza la base)."""

    def efectivo(col: str):
        return cambios.get(col, fila[col])

    target = efectivo("target_acos_pct")
    if target is not None and target <= 0:
        raise GoalInvalido(f"target_acos_pct debe ser > 0, llego {target}")

    floor = efectivo("bid_floor")
    ceiling = efectivo("bid_ceiling")
    if floor is not None and floor <= 0:
        raise GoalInvalido(f"bid_floor debe ser > 0, llego {floor}")
    if ceiling is not None and ceiling <= 0:
        raise GoalInvalido(f"bid_ceiling debe ser > 0, llego {ceiling}")
    if floor is not None and ceiling is not None and floor > ceiling:
        raise GoalInvalido(
            f"bid_floor {floor} > bid_ceiling {ceiling}: el piso no puede superar el techo"
        )

    bid_harvest = efectivo("harvest_default_bid")
    if bid_harvest is not None and bid_harvest <= 0:
        raise GoalInvalido(f"harvest_default_bid debe ser > 0, llego {bid_harvest}")

    terna = (
        efectivo("harvest_campaign_id"),
        efectivo("harvest_ad_group_id"),
        efectivo("harvest_default_bid"),
    )
    if permite_bid_solo and terna[0] is None and terna[1] is None and terna[2] is not None:
        return
    if any(v is not None for v in terna) and not all(v is not None for v in terna):
        nombres = ("harvest_campaign_id", "harvest_ad_group_id", "harvest_default_bid")
        faltan = [n for n, v in zip(nombres, terna, strict=True) if v is None]
        raise GoalInvalido(
            "config de harvest incompleta: faltan "
            + ", ".join(faltan)
            + " (los tres campos de harvest van juntos o no van)"
        )


def _cambios_edicion(
    *,
    target_acos_pct: Decimal | None,
    enabled: bool | None,
    bid_floor: Decimal | None,
    bid_ceiling: Decimal | None,
    harvest_campaign_id: str | None,
    harvest_ad_group_id: str | None,
    harvest_default_bid: Decimal | None,
    harvest_limpia: bool,
    harvest_limpia_destino: bool,
) -> dict[str, object]:
    """Mapa columna -> valor nuevo desde los parametros (None = no cambiar).
    `harvest_limpia` pone los TRES campos de harvest a NULL;
    `harvest_limpia_destino` (FABRICA 02) pone a NULL solo campaign/ad_group
    (bid intacto); ambos rechazan combinarse con campos harvest
    individuales."""
    cambios: dict[str, object] = {}
    if target_acos_pct is not None:
        cambios["target_acos_pct"] = target_acos_pct
    if enabled is not None:
        cambios["enabled"] = enabled
    if bid_floor is not None:
        cambios["bid_floor"] = bid_floor
    if bid_ceiling is not None:
        cambios["bid_ceiling"] = bid_ceiling
    if harvest_campaign_id is not None:
        cambios["harvest_campaign_id"] = harvest_campaign_id
    if harvest_ad_group_id is not None:
        cambios["harvest_ad_group_id"] = harvest_ad_group_id
    if harvest_default_bid is not None:
        cambios["harvest_default_bid"] = harvest_default_bid
    if harvest_limpia:
        if any(
            v is not None for v in (harvest_campaign_id, harvest_ad_group_id, harvest_default_bid)
        ):
            raise GoalInvalido(
                "harvest_limpia no se combina con campos harvest individuales:"
                " limpia los tres o edita los tres"
            )
        cambios["harvest_campaign_id"] = None
        cambios["harvest_ad_group_id"] = None
        cambios["harvest_default_bid"] = None
    if harvest_limpia_destino:
        if any(
            v is not None for v in (harvest_campaign_id, harvest_ad_group_id, harvest_default_bid)
        ):
            raise GoalInvalido(
                "harvest_limpia_destino no se combina con campos harvest individuales:"
                " el bid queda intacto y el destino se va a NULL"
            )
        cambios["harvest_campaign_id"] = None
        cambios["harvest_ad_group_id"] = None
    return cambios


def edita_goal(
    conn: psycopg.Connection,
    goal_id: int,
    *,
    target_acos_pct: Decimal | None = None,
    enabled: bool | None = None,
    bid_floor: Decimal | None = None,
    bid_ceiling: Decimal | None = None,
    harvest_campaign_id: str | None = None,
    harvest_ad_group_id: str | None = None,
    harvest_default_bid: Decimal | None = None,
    harvest_limpia: bool = False,
    harvest_limpia_destino: bool = False,
    updated_at: dt.datetime,
) -> dict:
    """Edita un goal: UPDATE de SOLO los campos pasados + `updated_at`
    EXPLICITO (parametro OBLIGATORIO, tz-aware; ver docstring del modulo),
    devolviendo la fila completa en el shape de GET /goals.

    `None` = no cambiar ese campo; `harvest_limpia=True` pone los TRES campos
    de harvest a NULL y rechaza combinarse con campos harvest individuales.
    `harvest_limpia_destino=True` (FABRICA 02, limpieza de la terna
    transitoria de F1 grupo por grupo, A.5/D.2): pone a NULL SOLO
    `harvest_campaign_id`/`harvest_ad_group_id` (bid intacto), rechaza
    combinarse con campos harvest individuales y exige —validado DESPUES de
    leer la fila— que el goal sea de scope `campaign` y su campana este en
    `campana_grupo_rol` (sin grupo no hay destino que lo reemplace: seria
    dejar un goal sin cosecha en silencio).
    La conexion puede venir con o sin autocommit: el UPDATE se confirma aqui
    (conn.commit), en la misma transaccion que la lectura previa.

    Excepciones: GoalInexistente (404/exit 1), GoalInvalido (422/exit 2, el
    mensaje es el motivo). ValueError si `updated_at` es None o naive (error
    de programacion del caller, no de uso).
    """
    if updated_at is None:
        raise ValueError("updated_at es obligatorio: sin el no se puede sellar la edicion")
    if updated_at.tzinfo is None:
        raise ValueError(
            "updated_at debe ser tz-aware: un naive se evaluaria segun la TZ de la sesion"
        )

    cambios = _cambios_edicion(
        target_acos_pct=target_acos_pct,
        enabled=enabled,
        bid_floor=bid_floor,
        bid_ceiling=bid_ceiling,
        harvest_campaign_id=harvest_campaign_id,
        harvest_ad_group_id=harvest_ad_group_id,
        harvest_default_bid=harvest_default_bid,
        harvest_limpia=harvest_limpia,
        harvest_limpia_destino=harvest_limpia_destino,
    )

    # Validacion de ENTRADA pura, ANTES de leer la fila (sin I/O; hallazgos
    # #1-#3 de la review 3.2): edicion vacia (un UPDATE que solo mueve
    # updated_at seria un rastro que miente), cadenas vacias/espacios en la
    # terna harvest ("" cuenta como "presente" y dejaria config harvest
    # "completa" con ids vacios — regla 3) y Decimales no finitos
    # (Infinity pasa gt=0 y las comparaciones; NaN las esquiva; PG16 acepta
    # ambos en NUMERIC). Vive SOLO aqui: cubre CLI y endpoint.
    if not cambios:
        raise GoalInvalido("edicion vacia: nada que cambiar")
    for nombre in ("target_acos_pct", "bid_floor", "bid_ceiling", "harvest_default_bid"):
        valor = cambios.get(nombre)
        if valor is not None and not valor.is_finite():
            raise GoalInvalido(f"{nombre} no es un numero finito valido")
    for nombre in ("harvest_campaign_id", "harvest_ad_group_id"):
        valor = cambios.get(nombre)
        if valor is not None and not valor.strip():
            raise GoalInvalido(
                f"{nombre} no puede ser vacio o solo espacios:"
                " la terna harvest se borra con harvest_limpia"
            )

    # La conexion es del caller: se lee y escribe con dict_row pero el
    # factory ORIGINAL se restaura en finally (FABRICA 02 A.1: la
    # herramienta de A.5 sigue usando la conexion con tuplas tras editar;
    # contaminarla rompia apply_cola, que desempaqueta por posicion). Las
    # conns falsas de los tests unitarios (sin row_factory) se dejan
    # intactas: ya devuelven dicts.
    anterior = getattr(conn, "row_factory", None)
    if anterior is not None:
        conn.row_factory = dict_row
    try:
        fila = conn.execute(_SQL_LEE, (goal_id,)).fetchone()
        if fila is None:
            raise GoalInexistente(f"goal {goal_id} no existe")

        if harvest_limpia_destino:
            _valida_limpia_destino_post_lectura(conn, fila)

        # Defaults de piso/techo POR MONEDA (ORBIT 05 preflight 1.2; la DB ya no
        # tiene DEFAULT desde 0003): si el estado efectivo de la fila editable
        # quedara con floor/ceiling ausentes, se resuelven con LA MONEDA DEL
        # PROPIO goal antes de persistir -- JAMAS caen a 0.10/2.50 implicitos
        # (spot-check 4.4: ese techo en USD aplastaba bids vivos de MX). Moneda
        # fuera de DEFAULTS_POR_MONEDA -> ValueError y el UPDATE no se ejecuta.
        # La pre-validacion de abajo corre SOBRE el estado ya resuelto: el
        # operador ve el mensaje claro, no un CheckViolation crudo.
        # DEFENSA EN PROFUNDIDAD (grok, cross-review 1.2 r2): contra filas reales
        # es inalcanzable hoy (bid_floor/bid_ceiling son NOT NULL desde 0001;
        # None en la API significa "no cambiar") -- se queda porque el sello del
        # plan exige que un MXN jamas se persista con defaults USD implicitos.
        if (
            cambios.get("bid_floor", fila["bid_floor"]) is None
            or cambios.get("bid_ceiling", fila["bid_ceiling"]) is None
        ):
            piso_default, techo_default = resuelve_floor_ceiling(None, fila["bid_currency"])
            if cambios.get("bid_floor", fila["bid_floor"]) is None:
                cambios["bid_floor"] = piso_default
            if cambios.get("bid_ceiling", fila["bid_ceiling"]) is None:
                cambios["bid_ceiling"] = techo_default

        _valida_pre_editar(fila, cambios, permite_bid_solo=harvest_limpia_destino)

        # Nombres de columna LITERALES de este codigo (los valores van por
        # parametros): mismo estilo de SQL fijo + %s del resto del repo.
        sets = ", ".join([*(f"{col} = %s" for col in cambios), "updated_at = %s"])
        sql = f"UPDATE ads_optimizer_goal SET {sets} WHERE id = %s RETURNING {_COLUMNAS}"
        fila_nueva = conn.execute(sql, (*cambios.values(), updated_at, goal_id)).fetchone()
    finally:
        if anterior is not None:
            conn.row_factory = anterior
    conn.commit()
    return _fila_respuesta(fila_nueva)


MODOS_CREACION = ("shadow", "live")

_SQL_CREA = (
    "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
    " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
    " harvest_default_bid, enabled, mode, created_at, updated_at)"
    " VALUES ('campaign', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    f" RETURNING {_COLUMNAS}"
)


def crea_goal(
    conn: psycopg.Connection,
    *,
    ad_entity_id: int,
    target_acos_pct: Decimal,
    bid_currency: str,
    mode: str,
    harvest_campaign_id: str,
    harvest_ad_group_id: str,
    harvest_default_bid: Decimal,
    created_at: dt.datetime,
    enabled: bool = True,
) -> dict:
    """Crea el goal de scope campana de una campana NUEVA (FABRICA 01, spec
    §4, decisiones 12 y 13): terna harvest COMPLETA obligatoria, `mode`
    explicito en {shadow, live} (nunca `off`: un goal apagado al nacer es
    una campana que el motor jamas toca), piso/techo de DEFAULTS_POR_MONEDA
    de SU moneda. Camino unico de escritura de goals (sellado 26 de ORBIT 04,
    docs/APPLY.md §10.3): la fabrica despacha aqui, jamas duplica el INSERT.

    Validacion PURA antes de I/O (mismo criterio que edita_goal); los
    rechazos de la base (goal_unico_campana, goal_scope_campana_real) se
    traducen a GoalInvalido con mensaje en espanol.
    """
    if created_at is None or created_at.tzinfo is None:
        raise ValueError("created_at es obligatorio y tz-aware")
    if mode not in MODOS_CREACION:
        raise GoalInvalido(f"mode debe ser uno de {MODOS_CREACION}, llego {mode!r}")
    for nombre, valor in (
        ("target_acos_pct", target_acos_pct),
        ("harvest_default_bid", harvest_default_bid),
    ):
        if not isinstance(valor, Decimal) or not valor.is_finite():
            raise GoalInvalido(f"{nombre} debe ser un Decimal finito, llego {valor!r}")
    for nombre, valor in (
        ("harvest_campaign_id", harvest_campaign_id),
        ("harvest_ad_group_id", harvest_ad_group_id),
    ):
        if not isinstance(valor, str) or not valor.strip():
            raise GoalInvalido(f"{nombre} no puede ser vacio: la terna harvest va completa")
    try:
        piso, techo = resuelve_floor_ceiling(None, bid_currency)
    except ValueError as exc:
        raise GoalInvalido(f"moneda sin defaults: {exc}") from exc
    fila_nueva = {
        "target_acos_pct": target_acos_pct,
        "bid_floor": piso,
        "bid_ceiling": techo,
        "harvest_campaign_id": harvest_campaign_id.strip(),
        "harvest_ad_group_id": harvest_ad_group_id.strip(),
        "harvest_default_bid": harvest_default_bid,
    }
    _valida_pre_editar(fila_nueva, {})  # mismos espejos de CHECK que la edicion

    # El row_factory del caller se restaura en finally (ver edita_goal);
    # las conns falsas sin row_factory se dejan intactas.
    anterior = getattr(conn, "row_factory", None)
    if anterior is not None:
        conn.row_factory = dict_row
    try:
        fila = conn.execute(
            _SQL_CREA,
            (
                ad_entity_id,
                target_acos_pct,
                piso,
                techo,
                bid_currency,
                fila_nueva["harvest_campaign_id"],
                fila_nueva["harvest_ad_group_id"],
                harvest_default_bid,
                enabled,
                mode,
                created_at,
                created_at,
            ),
        ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        raise GoalInvalido(f"la campana {ad_entity_id} ya tiene goal (goal_unico_campana)") from exc
    except psycopg.errors.CheckViolation as exc:
        conn.rollback()
        raise GoalInvalido(f"la base rechazo el goal: {exc.diag.message_primary}") from exc
    finally:
        if anterior is not None:
            conn.row_factory = anterior
    conn.commit()
    return _fila_respuesta(fila)
