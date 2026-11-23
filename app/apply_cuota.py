"""Cuota diaria, vocabulario de motivos, orden bajo cupo y lecturas de soporte de apply.

Modulo hoja extraido de `app/apply.py` (Fase 3.4): no importa `app.ads.write`,
ni `app.apply`, ni capas HTTP/UI. Permite que `api_dashboard`, `config_write`,
`apply_cola`, `apply_harvest` y `cycle` consuman cuotas, motivos y helpers de
lectura sin acoplarse al cliente de escritura ni al nucleo de mutaciones.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg.types.json import Json

from app import temporada_io
from app.db import tx_durable

# CAMPANA ACTIVA 01 · 1.6: ancestros no ENABLED (o sin fila de state, regla 3)
# bloquean reintentos y liberaciones. Definidos AQUI; apply/apply_cola/cycle
# los re-exportan como alias: cero cambio de vocabulario.
MOTIVO_CAMPANA_NO_ENABLED = "campana_no_enabled"
MOTIVO_GRUPO_NO_ENABLED = "grupo_no_enabled"
# Sello del ledger cuando el gate corta una reconciliacion (1.6a).
RESULTADO_ANCESTRO_NO_ENABLED = "fallo:ancestro_no_enabled"

# Vocabulario cerrado de motivos del aplicador (skips y descartes
# estructurados; el digest de 3.3 los consume tal cual).
MOTIVO_MODO_NO_LIVE = "modo_no_live"
MOTIVO_YA_APLICADA = "ya_aplicada"
MOTIVO_TOPE_INTENTOS = "tope_intentos"
# ARREGLOS 01 K (E-H3): una hoja con un bid 'normal' sin sello (resultado
# dudoso) no vuelve a moverse hasta que la reconciliacion lo confirme o lo
# cierre. La guarda del ciclo y reconcilia_bids comparten el MISMO predicado.
MOTIVO_BID_EN_VUELO = "bid_en_vuelo"
MOTIVO_FUERA_DE_CAP = "fuera_de_cap"
MOTIVO_FALLO_HTTP = "fallo_http"
MOTIVO_BID_INCOMPLETO = "bid_incompleto"
MOTIVO_ENTIDAD_NO_DECISORA = "entidad_no_decisora"

# ADS D.1: motivos de no-apply que nacen fuera de aplica_bids (la cola de
# cortes y el hook de harvest los registran desde libera_vencidos). El
# literal de espera_target vive AQUI (una sola fuente): apply_cola lo aliasa.
MOTIVO_SIN_QUOTA = "sin_quota"
MOTIVO_ESPERA_TARGET = "espera_target"
MOTIVO_SIN_RESPUESTA = "sin_respuesta"
MOTIVO_PERDIDA = "perdida"
# D.1b: la decision de corte live cuyo INSERT choco la clave de efecto en
# vuelo (sin fila en la cola; sin registro, v_decision_huerfana la listaria
# como huerfana). `perdida` SE QUEDA en el vocabulario (puede haber filas
# historicas) aunque libera_vencidos ya no la escriba.
MOTIVO_CHOQUE_CLAVE = "choque_clave"
# ORBIT 07 M1: la pausa por cero ventas espera el vencimiento del episodio de
# venta reciente. La fila queda released: no cobra cuota, no entra a applying y
# no reinicia el veto de 48 horas.
MOTIVO_ESPERA_PROTECCION = "espera_proteccion"
# TEMPORADA 01 E (vocabulario y CHECK entran con el bloque B, migracion 0093):
# la lectura fresca antes del PUT encontro en Amazon un bid distinto del que
# Orbit vio al decidir. Cero PUT del destino viejo; la hoja queda excluida del
# episodio (temporada_exclusion) y el desenlace del efecto es mano_del_dueno.
MOTIVO_BID_CAMBIADO_FUERA = "bid_cambiado_fuera"
# ARREGLOS 05 A.3: la decision ordinaria es elegible solo el dia en que se
# decidio. Al cambiar el dia, la que NUNCA se intento deja de ser elegible con
# este desenlace auditable. La que ya tiene un intento no se toca: esa solo la
# resuelve la conciliacion, leyendo.
MOTIVO_VIGENCIA_VENCIDA = "vigencia_vencida"

# Etiqueta de skip en memoria del aplicador (NO se persiste): la decision ya
# tiene un intento normal, cierto o fallido, y el aplicador no la vuelve a
# mandar. Su desenlace lo pone la conciliacion con una lectura fresca.
MOTIVO_YA_INTENTADA = "ya_intentada"

# Sello del ledger cuando el resultado de un PUT es incierto (5xx, red o
# lectura de vuelta ilegible): Amazon pudo aplicar. Mismo literal que leen
# _SQL_INTENTOS_BID_AMBIGUOS_SIN_DESENLACE y _SQL_HOJAS_BID_PENDIENTE.
RESULTADO_READBACK_AMBIGUO = "fallo:readback_ambiguo"

# ADS D.1: vocabulario CERRADO de decision_sin_aplicar (migracion 0044; el
# CHECK nombrado vive desde 0045) — UNA fuente en la app; el CHECK de la
# tabla es su espejo (test estatico y test de la constraint viva en
# test_apply_schema los mantienen sincronizados, mismo patron que
# KINDS_QUOTA <-> trigger). `ya_aplicada` esta en la lista pero JAMAS se
# escribe: su desenlace ya es decision_application (grabarla la marcaria
# como no-aplicada).
MOTIVOS_SIN_APLICAR = (
    MOTIVO_MODO_NO_LIVE,
    MOTIVO_YA_APLICADA,
    MOTIVO_BID_INCOMPLETO,
    MOTIVO_ENTIDAD_NO_DECISORA,
    MOTIVO_TOPE_INTENTOS,
    MOTIVO_FUERA_DE_CAP,
    MOTIVO_FALLO_HTTP,
    MOTIVO_SIN_QUOTA,
    MOTIVO_ESPERA_TARGET,
    MOTIVO_SIN_RESPUESTA,
    MOTIVO_PERDIDA,
    MOTIVO_CHOQUE_CLAVE,
    MOTIVO_ESPERA_PROTECCION,
    MOTIVO_BID_CAMBIADO_FUERA,
    MOTIVO_VIGENCIA_VENCIDA,
)

_SQL_SIN_APLICAR = """
INSERT INTO decision_sin_aplicar (decision_id, cycle_id, motivo, detalle)
VALUES (%s, %s, %s, %s)
ON CONFLICT (decision_id, cycle_id) DO NOTHING
"""


def registra_sin_aplicar(
    conn: psycopg.Connection,
    decision_id: int,
    cycle_id: int,
    motivo: str,
    *,
    detalle: dict | None = None,
) -> None:
    """Registra el desenlace "no aplicado" del ciclo EJECUTOR (0044).

    Idempotente por PK (ON CONFLICT DO NOTHING): un re-run del mismo ciclo
    no duplica; otro ciclo ejecutor agrega su propia fila. El motivo se
    valida contra MOTIVOS_SIN_APLICAR ANTES de tocar la base (la tabla lo
    refuerza con el CHECK espejo).

    TEMPORADA 01 B: en la misma transaccion cierra el efecto de temporada con
    desenlace 'sin_aplicar' (o 'mano_del_dueno' cuando el motivo es
    bid_cambiado_fuera: Amazon ya tenia otra mano)."""
    if motivo not in MOTIVOS_SIN_APLICAR:
        raise ValueError(f"motivo fuera del vocabulario cerrado: {motivo}")
    conn.execute(
        _SQL_SIN_APLICAR,
        (decision_id, cycle_id, motivo, Json(detalle) if detalle is not None else None),
    )
    temporada_io.cierra_efecto_sin_aplicar(conn, decision_id=decision_id, motivo=motivo)


# ARREGLOS 05 A.3: decisiones de dias anteriores que nunca se intentaron. La
# fuente de "sin desenlace" es la vista de auditoria (una sola definicion:
# sin aplicacion, sin no-apply y sin fila en la cola). Solo origenes con
# sello de TX3: las reglas nuevas no reclasifican la historia anterior a la
# 0094, que conserva su `sin_registro`. Sin ningun intento normal: la decision
# que salio a Amazon, cierto o incierto, solo la resuelve la conciliacion.
_SQL_NO_INTENTADAS_VENCIDAS = """
SELECT h.decision_id, h.cycle_id
  FROM v_decision_huerfana h
  JOIN decision d         ON d.id = h.decision_id
  JOIN optimizer_cycle o  ON o.id = h.cycle_id
 WHERE h.origen = 'huerfana'
   AND h.platform = %s::platform
   AND o.decision_committed_at IS NOT NULL
   AND (d.decided_at AT TIME ZONE 'UTC')::date < %s::date
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                    WHERE a.decision_id = h.decision_id AND a.tipo = 'normal')
   AND NOT EXISTS (SELECT 1 FROM apply_queue q WHERE q.decision_id = h.decision_id)
 ORDER BY h.decision_id
"""


def expira_no_intentadas(
    conn: psycopg.Connection, platform: str, *, hoy: dt.date, cycle_id_ejecutor: int
) -> int:
    """Al cambiar el dia de decision, lo ordinario que nunca se intento deja de
    ser elegible con motivo auditable (ARREGLOS 05 A.3). `hoy` es la fecha UTC
    del reloj de la corrida. No borra decisiones ni promueve propuestas
    shadow; el ciclo en curso decide de nuevo con datos frescos. Devuelve
    cuantas dejaron de ser elegibles."""
    filas = conn.execute(_SQL_NO_INTENTADAS_VENCIDAS, (platform, hoy)).fetchall()
    if not filas:
        return 0
    with tx_durable(conn):
        for decision_id, origen in filas:
            registra_sin_aplicar(
                conn,
                decision_id,
                cycle_id_ejecutor,
                MOTIVO_VIGENCIA_VENCIDA,
                detalle={"origen": origen},
            )
    return len(filas)


# Formas de quota de la rampa (preflight 1.4): ESPEJO del CASE del trigger
# apply_cap_de_config (0002) — la UNICA lista en app; los lectores
# (estado_quota y el endpoint de /salud) la recorren. kind nuevo = decision
# nueva del dueno (sellado 8), aca y en el trigger.
KINDS_QUOTA = ("bid", "pause", "negative", "harvest")

# apply_quota_state.cap es INTEGER (0001:823) y el trigger apply_cap_de_config
# castea ::INTEGER (0039:1175): un cap que no cabe ahi revienta el INSERT de la
# fila del dia. Es el techo del dominio que ya vive en el schema.
CAP_MAXIMO = 2_147_483_647


def cap_desde_settings(settings: Mapping, platform: str, kind: str) -> int | None:
    """Lector UNICO del cap `ads_apply_cap_<platform>_<kind>`.

    None = clave ausente o JSON null (fail-closed: no nace fila del dia).
    Presente: int (no bool) o str de digitos ASCII, en [0, CAP_MAXIMO].
    Cualquier otra cosa es config ROTA (regla 3): ValueError ruidoso, jamas
    disfraz de cap infinito. El motor al aplicar y `proxima_config` al
    guardar pasan por aqui, asi que lo que se guarda es lo que el schema
    admite."""
    clave = f"ads_apply_cap_{platform}_{kind}"
    valor = settings.get(clave)
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ValueError(f"{clave}: cap booleano en la config vigente: {valor!r}")
    if isinstance(valor, int):
        n = valor
    elif isinstance(valor, str) and valor.isascii() and valor.isdigit():
        n = int(valor)
    else:
        raise ValueError(f"{clave}: valor no entero en la config vigente: {valor!r}")
    if not 0 <= n <= CAP_MAXIMO:
        raise ValueError(f"cap {kind} fuera de rango 0..{CAP_MAXIMO}, llego {n}")
    return n


def motor_quota(platform: str, kind: str) -> str:
    """Clave del motor de quota: `ads_optimizer:<platform>:<kind>`.

    UNA fuente en la app de este vocabulario; su espejo de schema es el
    mapeo de `apply_cap_de_config` (0002), que resuelve la clave de config
    `ads_apply_cap_<platform>_<kind>` para ESTE motor. kind en KINDS_QUOTA —
    kinds nuevos de quota = decision nueva del dueno (sellado 8)."""
    return f"ads_optimizer:{platform}:{kind}"


_SQL_CONFIG_VIGENTE = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""


def _cap_de_config(conn: psycopg.Connection, platform: str, kind: str) -> int | None:
    """Cap de la config VIGENTE (ultima config_version por id — la misma
    resolucion que el trigger apply_cap_de_config de 0002). None = sin clave
    o sin config: fail-closed, no nace fila del dia. El valor lo interpreta
    `cap_desde_settings`, el mismo lector que valida el guardado."""
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    if fila is None:
        return None
    return cap_desde_settings(fila[0] or {}, platform, kind)


def consume_quota_y_sello(conn: psycopg.Connection, platform: str, kind: str) -> tuple[bool, bool]:
    """Consume UNA unidad de quota del dia y sella la TRANSICION de saturacion
    (preflight 1.4, D3a). Devuelve (usada, saturada).

    INSERT ... ON CONFLICT DO UPDATE en UNA sentencia (no SELECT-luego-UPSERT:
    dos sesiones concurrentes no pueden pasarse del cap). La fila NACE con
    used=1 — el primer consumo tambien cuenta (un INSERT sin used naceria en
    0 y el primer apply del dia seria gratis). El dia va fijado a UTC EN LA
    EXPRESION — el trigger de 0002 valida quota_date contra el dia UTC de la
    base y CURRENT_DATE dependeria de la TimeZone de la sesion (r2 codex).
    Sin fila devuelta (cap agotado o sin clave) -> (False, False). El cap del
    INSERT sale de la config VIGENTE: el trigger lo re-valida igual."""
    cap = _cap_de_config(conn, platform, kind)
    if cap is None:
        return False, False
    if cap <= 0:
        # cap 0 = rampa apagada por config (fail-closed): cero applies, sin
        # nacer fila — la visibilidad del estado es la config misma.
        return False, False
    fila = conn.execute(
        """
        INSERT INTO apply_quota_state (motor, quota_date, cap, used)
        VALUES (%s, (now() AT TIME ZONE 'UTC')::date, %s, 1)
        ON CONFLICT (motor, quota_date) DO UPDATE
           SET used = apply_quota_state.used + 1
         WHERE apply_quota_state.used < apply_quota_state.cap
        RETURNING used, cap
        """,
        (motor_quota(platform, kind), cap),
    ).fetchone()
    if fila is None:
        return False, False
    return True, fila[0] == fila[1]


def consume_quota(conn: psycopg.Connection, platform: str, kind: str) -> bool:
    """Consume UNA unidad de quota del dia, atomicamente (contrato historico:
    True = cobrada). Wrapper que descarta el sello de transicion — la logica
    y el SQL atomico viven en `consume_quota_y_sello` (UNA fuente)."""
    usada, _saturada = consume_quota_y_sello(conn, platform, kind)
    return usada


def estado_quota(conn: psycopg.Connection, platform: str, kind: str) -> dict:
    """Lectura de quota del dia (preflight 1.4, D1): {used, cap, fuente}.

    UNA fuente: motor_quota para la clave y la MISMA expresion de dia UTC de
    consume_quota y del trigger de 0002 ((now() AT TIME ZONE 'UTC')::date —
    jamas CURRENT_DATE ni fecha del cliente). Tres fuentes declaradas:
    - "fila_del_dia": existe fila (motor, hoy UTC) — used y cap SON los que
      rigen hoy; el cap de la fila es INMUTABLE (trigger de 0002).
    - "config_vigente": sin fila aun hoy — cap resuelto igual que
      _cap_de_config (la misma resolucion del trigger).
    - "sin_clave": la config vigente no trae la clave (o no hay config):
      fail-closed explicito, cap None."""
    fila = conn.execute(
        """
        SELECT used, cap FROM apply_quota_state
         WHERE motor = %s AND quota_date = (now() AT TIME ZONE 'UTC')::date
        """,
        (motor_quota(platform, kind),),
    ).fetchone()
    if fila is not None:
        return {"used": fila[0], "cap": fila[1], "fuente": "fila_del_dia"}
    cap = _cap_de_config(conn, platform, kind)
    if cap is not None:
        return {"used": 0, "cap": cap, "fuente": "config_vigente"}
    return {"used": 0, "cap": None, "fuente": "sin_clave"}


@dataclass(frozen=True)
class CapSaturado:
    """Evento de saturacion de quota (preflight 1.4, D3a/D3d): viaja en los
    resultados de apply/apply_cola/apply_harvest hasta el ciclo, que manda UN
    aviso fail-silent por evento (y la NOTA notes['telegram']['cap_agotado']
    si el canal fallo). `used`/`cap` salen de la PROPIA fila del dia
    (evento_cap_saturado), no de memoria."""

    platform: str
    kind: str
    used: int
    cap: int


def evento_cap_saturado(conn: psycopg.Connection, platform: str, kind: str) -> CapSaturado | None:
    """El CapSaturado del consumo que acabo de saturar (D3a): used/cap de la
    propia fila del dia. None si la fila del dia no existe — no puede pasar
    tras un consumo exitoso, pero fail-closed: sin fila no hay evento ni
    numeros inventados (regla 3)."""
    estado = estado_quota(conn, platform, kind)
    if estado["fuente"] != "fila_del_dia":
        return None
    return CapSaturado(platform=platform, kind=kind, used=estado["used"], cap=estado["cap"])


@dataclass(frozen=True)
class DecisionBid:
    """Una decision kind='bid' lista para aplicar (espejo de la fila de
    `decision`; `inputs` trae el congelado del ciclo — el aplicador SOLO lee
    de ahi el motivo (banda) y el costo de la ventana para el orden; JAMAS
    inputs.modo)."""

    id: int
    ad_entity_id: int
    old_value: Decimal | None
    new_value: Decimal | None
    value_currency: str | None
    inputs: dict


@dataclass(frozen=True)
class ResultadoAplicador:
    """Resultado estructurado del lote: el ORDEN sellado (ids), cuantas
    quedaron confirmadas por readback, los motivos de descarte/skip (uno por
    decision; el digest de 3.3 los consume tal cual) y el conteo de
    DIVERGENCIAS de readback."""

    orden: list[int]
    aplicadas: int
    descartadas: list[str]
    skips: list[str]
    divergencias: int = 0
    # ARREGLOS 05 A.3: pujas cuyo PUT quedo incierto (5xx, red o lectura de
    # vuelta ilegible). Selladas como ambiguas y sin desenlace: la
    # conciliacion del ciclo siguiente las resuelve leyendo.
    inciertos: int = 0
    # Preflight 1.4 (D3d): los caps que ESTE lote llevo a su transicion
    # (used == cap) — viajan al ciclo para el aviso fail-silent. El consumo
    # rechazado por tope NO agrega (no es transicion).
    caps_saturados: tuple[CapSaturado, ...] = ()


_SQL_BIDS_CICLO = """
SELECT id, ad_entity_id, old_value, new_value, value_currency, inputs
  FROM decision
 WHERE cycle_id = %s AND kind = 'bid'
 ORDER BY id
"""


def bids_del_ciclo(conn: psycopg.Connection, cycle_id: int) -> list[DecisionBid]:
    """Las decisiones kind='bid' de un ciclo (los bids aplican en su ciclo;
    los cortes van a la cola, sellado 1)."""
    return [
        DecisionBid(
            id=fila[0],
            ad_entity_id=fila[1],
            old_value=fila[2],
            new_value=fila[3],
            value_currency=fila[4],
            inputs=fila[5] or {},
        )
        for fila in conn.execute(_SQL_BIDS_CICLO, (cycle_id,)).fetchall()
    ]


# Clave de orden para gasto desconocido (regla 3): dentro de SU nivel, al
# final — es una clave de ORDEN, no un valor de negocio.
_GASTO_DESCONOCIDO = Decimal(-1)


def prioridad_bajo_cupo(veredicto_kind: str, motivo: str) -> int:
    """Nivel de seleccion bajo cap (BIDS 02 M.3, bosquejo apply.py): 0
    regresos (R1) y los movimientos que devuelven valor a la hoja
    (sale_temporada_baja, ajusta hacia arriba, recupera_nivel); 1 recortes
    con evidencia propia (R3, R9) y la entrada de la baja (TEMPORADA 01 E);
    2 subidas (R6), 3 recortes heredados del ad group (R4, R11); lo demas al
    final. Motivos como literales, como hoy (este modulo no importa
    bid/politica; cada literal se pinea contra politica.py en tests)."""
    if veredicto_kind == "Regresar" or motivo == "regreso_por_desplome":
        return 0
    if motivo in (
        "pierde_dinero",
        "pierde_dinero_fuerte",
        "gasto_sin_venta",
        "gasto_sin_venta_doble",
        "entra_temporada_baja",
    ):
        return 1
    if motivo == "bajo_target":
        return 2
    if motivo in ("grupo_sangra_vendedora", "grupo_sangra"):
        return 3
    return 4


def _gasto_caso(inputs: dict) -> Decimal | None:
    """Gasto crudo de la hoja (el que R9 compara con el gasto para
    concluir): reciente.gasto + antiguo.gasto de inputs.caso.propia.
    None si falta el caso o algun tramo (regla 3)."""
    try:
        propia = (inputs.get("caso") or {}).get("propia") or {}
        reciente = (propia.get("reciente") or {}).get("gasto")
        antiguo = (propia.get("antiguo") or {}).get("gasto")
        if reciente is None or antiguo is None:
            return None
        return Decimal(str(reciente)) + Decimal(str(antiguo))
    except ArithmeticError:  # InvalidOperation: gasto corrupto -> como None
        return None


def _clave_cupo(decision: DecisionBid) -> tuple[int, Decimal]:
    nivel = prioridad_bajo_cupo(
        decision.inputs.get("veredicto_kind"), decision.inputs.get("motivo")
    )
    gasto = _gasto_caso(decision.inputs)
    return (nivel, -gasto if gasto is not None else -_GASTO_DESCONOCIDO)


def orden_bajo_cupo(decisiones: list[DecisionBid]) -> list[DecisionBid]:
    """Orden sellado de seleccion bajo cap: regresos > recortes con
    evidencia propia > subidas > recortes heredados y, dentro de cada
    nivel, gasto crudo de la hoja DESC (el de mas gasto primero; gasto
    desconocido al final de su nivel)."""
    return sorted(decisiones, key=_clave_cupo)


# El crash entre ledger y HTTP deja la fila SIN finished_at: eso ES el rastro
# que la reconciliacion (2.2/2.3) consulta al inicio del ciclo.
_SQL_INTENTOS_SIN_SELLO = """
SELECT id, decision_id, seq, tipo, request_payload, started_at
  FROM apply_attempt
 WHERE finished_at IS NULL
 ORDER BY started_at, id
"""

# CAMPANA ACTIVA 01 · 1.6: status (cache ad_entity_state) del ad group y de la
# campana de una entidad (hoja o ad group). Hoja: el grupo es su padre;
# ad group: el grupo es la propia entidad. LEFT JOIN: sin fila de state = NULL
# = fuera (regla 3).
_SQL_ANCESTROS = """
SELECT sg.status, sc.status
  FROM ad_entity e
  JOIN ad_entity g ON g.id = CASE WHEN e.kind = 'ad_group' THEN e.id ELSE e.parent_id END
  LEFT JOIN ad_entity_state sg ON sg.ad_entity_id = g.id
  LEFT JOIN ad_entity_state sc ON sc.ad_entity_id = g.parent_id
 WHERE e.id = %s
"""

# Los bids no viven en apply_queue: su unico rastro de crash es apply_attempt
# sin finished_at. Se filtra por plataforma porque los locks de ciclo son por
# plataforma (reversa y probe quedan fuera de reconciliacion).
_SQL_INTENTOS_BID_SIN_SELLO = """
SELECT a.id, a.decision_id, a.request_payload, d.ad_entity_id, d.new_value,
       d.value_currency
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id
  JOIN optimizer_cycle oc ON oc.id = d.cycle_id
 WHERE a.finished_at IS NULL AND a.tipo = 'normal' AND d.kind = 'bid'
   AND oc.platform = %s::platform
 ORDER BY a.started_at, a.id
"""

# Criterio COMPARTIDO de intentos pendientes (guarda del ciclo y
# reconciliacion): intentos sin sello MAS TODOS los intentos SELLADOS con
# lectura ambigua sin desenlace — de temporada y ordinarios (el resultado del
# PUT es desconocido; la hoja no genera otra puja hasta que la
# reconciliacion la resuelva con GET fresco: confirmar si la puja == destino,
# 'perdida' si == anterior, divergencia definitiva con un tercer valor).
_SQL_HOJAS_BID_PENDIENTE = """
SELECT d.ad_entity_id
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id
  JOIN ad_entity k ON k.id = d.ad_entity_id
 WHERE a.finished_at IS NULL AND a.tipo = 'normal' AND d.kind = 'bid'
   AND k.platform = %s::platform
UNION
SELECT d.ad_entity_id
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id
  JOIN ad_entity k ON k.id = d.ad_entity_id
 WHERE a.tipo = 'normal' AND a.finished_at IS NOT NULL
   AND a.resultado = 'fallo:readback_ambiguo'
   AND k.platform = %s::platform
   AND NOT EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa WHERE sa.decision_id = d.id)
"""


def intentos_sin_sello(conn: psycopg.Connection) -> list[tuple]:
    """Filas del ledger nacidas PRE-HTTP que nunca se sellaron (crash entre
    ledger y HTTP, o 5xx ambiguo): la entrada de la reconciliacion."""
    return conn.execute(_SQL_INTENTOS_SIN_SELLO).fetchall()


def gate_ancestros(conn: psycopg.Connection, ad_entity_id: int) -> str | None:
    """CAMPANA ACTIVA 01 · 1.6: la UNICA fuente del gate de campana/ad group
    ENABLED (cache ad_entity_state, sync diario; semantica D1/D3: sin fila de
    state = fuera, regla 3). Resuelve hoja vs ad group con el mismo CASE que
    trajo 1.2; la usan la cola (libera) y los TRES reconciliadores (reintentos
    post-crash). None = ancestros ENABLED; motivo = el ancestro de afuera hacia
    adentro manda."""
    fila_anc = conn.execute(_SQL_ANCESTROS, (ad_entity_id,)).fetchone()
    status_grupo, status_campana = fila_anc if fila_anc is not None else (None, None)
    if status_campana != "ENABLED":
        return MOTIVO_CAMPANA_NO_ENABLED
    if status_grupo != "ENABLED":
        return MOTIVO_GRUPO_NO_ENABLED
    return None


def hojas_con_bid_sin_sello(conn: psycopg.Connection, platform: str) -> frozenset[int]:
    """ARREGLOS 01 K (E-H3): ad_entity_id con un intento tipo 'normal' de
    decision kind bid SIN sello en la plataforma. TEMPORADA 01 B (revision):
    incluye TAMBIEN las hojas con un efecto de temporada abierto cuyo intento
    quedo SELLADO con lectura ambigua sin desenlace (Amazon puede haber
    aplicado el cambio: la hoja no genera otra puja hasta que la
    reconciliacion la resuelva con GET fresco). Solo lectura; comparte el
    criterio de intentos pendientes con reconcilia_bids, asi la guarda del
    ciclo bloquea exactamente las hojas cuyo resultado es dudoso. La
    excepcion de Pausar del ciclo se mantiene intacta."""
    filas = conn.execute(_SQL_HOJAS_BID_PENDIENTE, (platform, platform))
    return frozenset(f[0] for f in filas)
