"""Biblioteca escrita por el motor (FABRICA 02, tarea A.4).

Cuando una decision se APLICA de verdad en campanas de un grupo, la
biblioteca de ese `tipo_producto` la aprende sola:

- keyword: cada harvest DE GRUPO confirmado por readback deja su termino
  en `keyword_biblioteca`. La escritura va en el sello del evento de
  valor de `apply_harvest._paso_readback` (la transaccion que confirma el
  resumen, deja la cola `applied` y avanza a `hermanas_negadas` con el
  roster), NO en el cierre `done` (`done` es solo el final de la higiene;
  desde el sello el job ya no puede volverse `failed`).
- negative: cada decision `kind = negative` APLICADA en campana de grupo
  deja su termino en `negative_biblioteca`, en los tres sitios que
  confirman `applied` (`apply_cola._ejecuta_negative` con `verify` y las
  ramas `propio is not None` y reintento-con-id de
  `_reconcilia_negativas`, estas dos via `_aprende_negative`; spec,
  decision 5: todo negative aplicado en campana de grupo). Los
  negativos de harvest (origen y hermanas) JAMAS pasan por aqui: son
  ruteo, no exclusion.

Contabilidad derivada: cada `registra_*` recibe la conexion YA dentro de
la transaccion del sello, abre un SAVEPOINT (`conn.transaction()`, patron
CX2 de `_nace_job`) y JAMAS levanta (captura `Exception`, no solo
`psycopg.Error`: una keyword ya creada en Amazon sin sello seria peor
que una fila de biblioteca perdida). Ante fallo deja rastro durable (el
dict de retorno: el caller del harvest lo guarda en
`external_ids["biblioteca"]`; el del negative conserva su veredicto y el
fallo queda en el log con `scrub`) y el caller avisa DESPUES del commit
con `avisa_si_fallo` (el HTTP de Telegram no alarga el sello; la alerta
es fail-silent y su texto dice que el harvest/negative SI quedo aplicado
y nunca dice "failed").

Precedencia (keyword gana; interseccion vacia EN EL MOMENTO DE
ESCRIBIR): `registra_negative` consulta primero `keyword_biblioteca` y
no inserta si el termino ya vendio ahi (motivo
`termino_en_keyword_biblioteca`, sin alerta: no es fallo);
`registra_keyword` inserta siempre y declara `conflicto_negative` si el
termino ya vive en `negative_biblioteca` (`app_decide` no tiene DELETE
por diseno de 0038: no se resuelve aqui; residual para R.1 y la siembra
de F1, donde keyword gana cuando ambas existen).

Sin dinero por construccion: los statements solo tocan (tipo_producto,
platform, texto, origen); `cost`, `revenue` y `moneda` quedan NULL y
`orders` en su DEFAULT de 0018. El "sin dinero" lo garantiza la app, no
el esquema (0038 lo advierte: un INSERT con `cost` pasaria el GRANT).

Sin grupo no hay `tipo_producto` que inventar (regla 3):
`grupo_de_ad_group` devuelve None y el caller no llama a `registra_*`
(cero statements, cero alerta, no es error).

Este modulo NO importa `apply`, `apply_cola`, `apply_harvest` ni
`apply_harvest_reconciliacion` (ellos importan AQUI; evita ciclos y no
engorda modulos ya en allowlist de tamano) y no importa
`app.ads.write` (la biblioteca no habla con Amazon; candado de
`tests/test_architecture.py`). Solo `psycopg`,
`app.optimizer.hygiene` (normalizacion, implementacion unica) y
`app.notifica` (alerta).
"""

from __future__ import annotations

import logging

import psycopg

from app import notifica
from app.optimizer import hygiene
from app.redaction import scrub

logger = logging.getLogger(__name__)

__all__ = [
    "MOTIVO_BIBLIOTECA_FALLO",
    "MOTIVO_TERMINO_EN_KEYWORD_BIBLIOTECA",
    "SQL_BIBLIOTECA_KEYWORD",
    "SQL_BIBLIOTECA_NEGATIVE",
    "avisa_si_fallo",
    "grupo_de_ad_group",
    "registra_keyword",
    "registra_negative",
    "tipo_producto_de_grupo",
]

# Statements canonicos: IDENTICOS al DO sellado de 0038 (mismas columnas,
# ON CONFLICT, SET y RETURNING; aqui con %s de psycopg, alla con variables
# PL). Fuente UNICA: `tests/test_fabrica_0038.py` importa desde aqui y el
# cruce endurecido contra el DO cae si un fragmento cambia.
SQL_BIBLIOTECA_KEYWORD = (
    "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)"
    " VALUES (%s, %s, %s, %s)"
    " ON CONFLICT (tipo_producto, platform, texto) DO UPDATE SET updated_at = now()"
    " RETURNING id"
)
SQL_BIBLIOTECA_NEGATIVE = (
    "INSERT INTO negative_biblioteca (tipo_producto, platform, texto, origen)"
    " VALUES (%s, %s, %s, %s)"
    " ON CONFLICT (tipo_producto, platform, texto) DO NOTHING"
)

MOTIVO_BIBLIOTECA_FALLO = "biblioteca_no_escrita"
MOTIVO_TERMINO_EN_KEYWORD_BIBLIOTECA = "termino_en_keyword_biblioteca"

# Resolucion de grupo por IDENTIDAD, jamas por nombre: la fila de un
# negative ES el ad group (`campana_grupo_rol.ad_group_ad_entity_id`); si
# ese ad group no esta directo (campana con varios ad groups: el rol
# guarda uno), se cruza por su campana via `parent_id`. Ambas ramas
# cruzan `campana_grupo_rol`: un `parent_id` sin rol seria otro grupo.
_SQL_GRUPO_POR_AD_GROUP = """
SELECT cgr.grupo_id, cg.tipo_producto, cgr.ad_entity_id
  FROM campana_grupo_rol cgr
  JOIN campana_grupo cg ON cg.id = cgr.grupo_id
 WHERE cgr.ad_group_ad_entity_id = %s
 LIMIT 1
"""

_SQL_GRUPO_POR_CAMPANA = """
SELECT cgr.grupo_id, cg.tipo_producto, cgr.ad_entity_id
  FROM campana_grupo_rol cgr
  JOIN campana_grupo cg ON cg.id = cgr.grupo_id
 WHERE cgr.ad_entity_id = %s
 LIMIT 1
"""

_SQL_PADRE = "SELECT parent_id FROM ad_entity WHERE id = %s"

_SQL_TIPO_DE_GRUPO = "SELECT tipo_producto FROM campana_grupo WHERE id = %s"

_SQL_KEYWORD_EXISTE = """
SELECT 1 FROM keyword_biblioteca
 WHERE tipo_producto = %s AND platform = %s AND texto = %s
 LIMIT 1
"""

_SQL_NEGATIVE_EXISTE = """
SELECT 1 FROM negative_biblioteca
 WHERE tipo_producto = %s AND platform = %s AND texto = %s
 LIMIT 1
"""

_SQL_NEGATIVE_ID = """
SELECT id FROM negative_biblioteca
 WHERE tipo_producto = %s AND platform = %s AND texto = %s
"""


def grupo_de_ad_group(
    conn: psycopg.Connection, ad_group_ad_entity_id: int
) -> tuple[int, str, int] | None:
    """(grupo_id, tipo_producto, campaign_ad_entity_id) por identidad: el ad
    group directo en `campana_grupo_rol`, o su campana por `parent_id`.
    None si no esta en grupo (harvest por excepcion/terna, negative en
    campana fuera de grupo: no hay `tipo_producto` que inventar y no es
    fallo — el caller no llama a `registra_*`). Sin 0018 (fixtures
    pre-F1) las tablas no existen y ningun grupo puede existir: None por
    el mismo camino, en SAVEPOINT para no envenenar la transaccion del
    sello (patron de `revalida_harvest`)."""
    try:
        with conn.transaction():
            fila = conn.execute(_SQL_GRUPO_POR_AD_GROUP, (ad_group_ad_entity_id,)).fetchone()
            if fila is None:
                padre = conn.execute(_SQL_PADRE, (ad_group_ad_entity_id,)).fetchone()
                if padre is None or padre[0] is None:
                    return None
                fila = conn.execute(_SQL_GRUPO_POR_CAMPANA, (padre[0],)).fetchone()
    except psycopg.errors.UndefinedTable:
        return None
    if fila is None or fila[0] is None:
        return None
    return int(fila[0]), str(fila[1]), int(fila[2])


def tipo_producto_de_grupo(conn: psycopg.Connection, grupo_id: int | None) -> str | None:
    """`tipo_producto` de `campana_grupo` por id (jamas por nombre de
    campana). None si el grupo no existe: el INSERT de `registra_*` falla
    por NOT NULL y deja rastro + alerta (el sello sigue intacto). Sin
    0018, None en SAVEPOINT (mismo patron que `grupo_de_ad_group`)."""
    if grupo_id is None:
        return None
    try:
        with conn.transaction():
            fila = conn.execute(_SQL_TIPO_DE_GRUPO, (grupo_id,)).fetchone()
    except psycopg.errors.UndefinedTable:
        return None
    return str(fila[0]) if fila is not None else None


def _rastro_fallo(
    *,
    aplicado: str,
    grupo_id: int | None,
    decision_id: int | None,
    exc: Exception,
) -> dict:
    """Rastro durable + log con `scrub` del fallo de biblioteca. NO avisa:
    el caller invoca `avisa_si_fallo` DESPUES del commit del sello (un
    canal lento no alarga la transaccion; correccion A.4r1). El sello del
    evento de valor / del corte queda intacto: el caller sigue con el
    veredicto aplicado."""
    detalle = type(exc).__name__
    logger.warning(
        "%s: %s aplicado pero termino no aprendido (grupo=%s decision=%s): %s",
        MOTIVO_BIBLIOTECA_FALLO,
        aplicado,
        grupo_id,
        decision_id,
        scrub(f"{detalle}: {exc}"),
    )
    return {"escrita": False, "motivo": MOTIVO_BIBLIOTECA_FALLO, "detalle": detalle}


def avisa_si_fallo(
    aplicado: str,
    rastro: dict | None,
    *,
    plataforma: str,
    grupo_id: int | None,
    decision_id: int | None,
    job_id: int | None,
    texto: str | None,
) -> bool:
    """Avisa DESPUES del commit del sello si `rastro` es un fallo de
    biblioteca (motivo `biblioteca_no_escrita`); en otro caso (None = sin
    grupo, exito o precedencia keyword) no hace nada y vuelve True. El
    caller la invoca fuera de la transaccion del sello: el HTTP de
    Telegram no alarga el commit (correccion A.4r1; los senders previos
    como `_cierra_o_sigue` ya avisaban fuera). Fail-silent (hereda el
    contrato del sender)."""
    if (
        rastro is None
        or rastro.get("escrita") is not False
        or rastro.get("motivo") != MOTIVO_BIBLIOTECA_FALLO
    ):
        return True
    return notifica.notifica_biblioteca_no_escrita(
        aplicado=aplicado,
        plataforma=plataforma,
        grupo_id=grupo_id,
        decision_id=decision_id,
        job_id=job_id,
        texto=texto,
        motivo=MOTIVO_BIBLIOTECA_FALLO,
        detalle=str(rastro.get("detalle", "?")),
    )


def registra_keyword(
    conn: psycopg.Connection,
    *,
    grupo_id: int | None,
    tipo_producto: str | None,
    platform: str,
    texto: str | None,
    origen: str,
    job_id: int | None = None,
    decision_id: int | None = None,
) -> dict:
    """Guarda el termino (normalizado strip + casefold, el mismo del dedupe
    de harvest) en `keyword_biblioteca`, en SAVEPOINT dentro de la
    transaccion del sello. JAMAS levanta. Devuelve el rastro:
    `{"escrita": True, "id": ...}` (mas `"conflicto_negative": True` si el
    termino ya vive en `negative_biblioteca`: se deja — `app_decide` no
    tiene DELETE por diseno de 0038 — y keyword gana) o `{"escrita":
    False, "motivo": "biblioteca_no_escrita", "detalle": <clase>}`. El
    caller avisa despues del commit con `avisa_si_fallo`."""
    try:
        norm = hygiene.normaliza_texto(texto)
        with conn.transaction():  # savepoint: absorbe el fallo sin abortar el sello
            id_fila = conn.execute(
                SQL_BIBLIOTECA_KEYWORD, (tipo_producto, platform, norm, origen)
            ).fetchone()
            rastro: dict = {"escrita": True, "id": int(id_fila[0]) if id_fila else None}
            choque = conn.execute(_SQL_NEGATIVE_EXISTE, (tipo_producto, platform, norm)).fetchone()
            if choque is not None:
                rastro["conflicto_negative"] = True
            return rastro
    except Exception as exc:  # noqa: BLE001 - el sello jamas aborta por la contabilidad derivada
        return _rastro_fallo(
            aplicado="harvest",
            grupo_id=grupo_id,
            decision_id=decision_id,
            exc=exc,
        )


def registra_negative(
    conn: psycopg.Connection,
    *,
    grupo_id: int | None,
    tipo_producto: str | None,
    platform: str,
    texto: str | None,
    origen: str,
    decision_id: int | None = None,
) -> dict:
    """Guarda el termino (normalizado) en `negative_biblioteca`, en
    SAVEPOINT dentro de la transaccion del sello. Precedencia: si el
    termino ya es keyword de ese tipo de producto (ya vendio en un
    grupo), NO se inserta y vuelve `{"escrita": False, "motivo":
    "termino_en_keyword_biblioteca"}` sin alerta (no es fallo). JAMAS
    levanta. El `id` del rastro se lee por clave (el statement canonico
    es DO NOTHING sin RETURNING por diseno de 0038). El caller avisa
    despues del commit con `avisa_si_fallo`."""
    try:
        norm = hygiene.normaliza_texto(texto)
        with conn.transaction():  # savepoint: absorbe el fallo sin abortar el sello
            ya_keyword = conn.execute(
                _SQL_KEYWORD_EXISTE, (tipo_producto, platform, norm)
            ).fetchone()
            if ya_keyword is not None:
                return {"escrita": False, "motivo": MOTIVO_TERMINO_EN_KEYWORD_BIBLIOTECA}
            conn.execute(SQL_BIBLIOTECA_NEGATIVE, (tipo_producto, platform, norm, origen))
            fila = conn.execute(_SQL_NEGATIVE_ID, (tipo_producto, platform, norm)).fetchone()
            return {"escrita": True, "id": int(fila[0]) if fila else None}
    except Exception as exc:  # noqa: BLE001 - el sello jamas aborta por la contabilidad derivada
        return _rastro_fallo(
            aplicado="negative",
            grupo_id=grupo_id,
            decision_id=decision_id,
            exc=exc,
        )
