"""Unico escritor de `precio_goal` (REPRICING 01 A.1, fila A.1 del plan).

Un camino (regla 1): sembrar (`sembrar_goal`, INSERT) y cerrar
(`cerrar_goal`, UPDATE de `valid_to` y nada mas) viven SOLO aqui;
`tools/precio_goal.py` despacha, jamas duplica SQL (candado
`_IDENT_PRECIO_GOAL` en `tests/test_architecture.py`). Solo `app_admin`
(GRANTs de la 0039); cero Amazon: este modulo no cotiza ni escribe precios.

El goal se recibe en POR CIENTO con dos decimales (`30.00`) y se guarda
como fraccion (`0.3000`); un valor en fraccion (`0.30`) se rechaza con
mensaje que dice que el argumento va en por ciento (Reject del plan:
goal como fraccion, defaults de goal).

La banda se lee de la `config_version` vigente
(`precio_goal_min_pct`/`precio_goal_max_pct`) con los ayudantes de
`app/precio/config.py` (REPRICING 02 0.a: un solo lector por clave); sin las
claves se aborta con `config sin precio_goal_min_pct`, nunca con un default.
La coherencia `0 < min < max < 1` replica el CHECK de la base.

Todo dinero y fracciones en `Decimal`, cero `float` (regla 4).

REPRICING 02 G.1: el plan vive en `app/precio/siembra.py` (puro) y este
modulo lo lee (`leer_candidatos`) y lo escribe entero o nada
(`aplicar_plan`, una transaccion). Cada fila sembrada deja su rastro
(`origen`, `lote`, `m_referencia`, columnas de la 0054).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
import psycopg.errors

from app.precio.config import _fraccion as config_fraccion
from app.precio.config import _numero as config_numero
from app.precio.puerto import Miembro, UnidadPrecio
from app.precio.siembra import (
    Candidato,
    GoalVigente,
    LecturaHoy,
    PedidoMargenDeHoy,
    PedidoModo,
    PedidoValor,
    PedidoValores,
    PlanGoals,
    planear,
)

__all__ = [
    "PrecioGoalInvalido",
    "PrecioGoalAusente",
    "HuellaDistinta",
    "BloqueoSinConfirmar",
    "MODOS_PRECIO_GOAL",
    "PLATAFORMAS_PRECIO_GOAL",
    "ORIGENES_PRECIO_GOAL",
    "PorIds",
    "HechoPlan",
    "OmitidaPlan",
    "ResultadoPlan",
    "fraccion_desde_porcentaje",
    "banda_desde_settings",
    "config_vigente_settings",
    "sembrar_goal",
    "cerrar_goal",
    "leer_candidatos",
    "aplicar_plan",
]


class PrecioGoalInvalido(ValueError):
    """Uso invalido del escritor de goals (exit 2 en el tool)."""


class PrecioGoalAusente(LookupError):
    """La fila que se pide no existe (exit 2 en el tool)."""


class HuellaDistinta(Exception):
    """El plan recalculado no coincide con el que se vio. Trae el plan nuevo."""

    def __init__(self, plan: PlanGoals, *, esperada: str) -> None:
        super().__init__(
            f"huella distinta (esperaba {esperada}, recalcule {plan.huella}):"
            " el plan cambio; vuelve a pedirlo"
        )
        self.plan = plan


class BloqueoSinConfirmar(Exception):
    """En modo estricto, una fila bloqueada sin confirmar aborta todo antes
    de escribir. Trae el plan fresco."""

    def __init__(self, plan: PlanGoals, *, confirmados: frozenset[int]) -> None:
        pendientes = sorted(
            f.listing_id
            for f in plan.filas
            if f.bloqueo is not None and not (f.confirmable and f.listing_id in confirmados)
        )
        super().__init__(f"filas bloqueadas sin confirmar: {pendientes}")
        self.plan = plan


MODOS_PRECIO_GOAL = ("shadow", "live")
PLATAFORMAS_PRECIO_GOAL = ("amazon_mx", "amazon_us", "meli")


def fraccion_desde_porcentaje(texto: str) -> Decimal:
    """`30.00` -> `Decimal("0.3000")`; `0.30` se rechaza (va en por ciento)."""
    try:
        valor = Decimal(str(texto).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise PrecioGoalInvalido(
            f"--goal-pct no numerico: {texto!r} (el argumento va en por ciento, ej. 30.00)"
        ) from exc
    if not valor.is_finite():
        raise PrecioGoalInvalido(
            f"--goal-pct no finito: {texto!r} (el argumento va en por ciento, ej. 30.00)"
        )
    if valor <= 0:
        raise PrecioGoalInvalido(
            f"--goal-pct no positivo: {texto!r} (el argumento va en por ciento, ej. 30.00)"
        )
    if valor < 1:
        raise PrecioGoalInvalido(
            f"--goal-pct {texto!r} parece fraccion: el argumento va en por ciento (30.00 para 30 %)"
        )
    if valor.as_tuple().exponent < -2:
        raise PrecioGoalInvalido(f"--goal-pct {texto!r}: maximo dos decimales")
    return (valor / 100).quantize(Decimal("0.0001"))


def _numero(settings: Mapping, clave: str) -> Decimal:
    """El ayudante unico de `app/precio/config.py` (REPRICING 02 0.a); el
    error de esta capa sigue siendo `PrecioGoalInvalido`."""
    try:
        return config_numero(settings, clave)
    except ValueError as exc:
        raise PrecioGoalInvalido(str(exc)) from exc


def _fraccion(settings: Mapping, clave: str, *, minima: str, maxima: str) -> Decimal:
    try:
        return config_fraccion(settings, clave, minima=minima, maxima=maxima)
    except ValueError as exc:
        raise PrecioGoalInvalido(str(exc)) from exc


def banda_desde_settings(settings: Mapping) -> tuple[Decimal, Decimal]:
    """`(min, max)` de la config vigente; sin claves o incoherente = aborta.

    Replica el CHECK de la base (`0 < min < max < 1`, cota del plan
    `precio_goal_min/max_pct`): ningun default en codigo (Reject del plan).
    """
    minimo = _fraccion(settings, "precio_goal_min_pct", minima="0", maxima="1")
    maximo = _fraccion(settings, "precio_goal_max_pct", minima="0", maxima="1")
    if not Decimal(0) < minimo < maximo < Decimal(1):
        raise PrecioGoalInvalido(
            f"config precio_goal incoherente: exige 0 < min < max < 1 (min={minimo}, max={maximo})"
        )
    return (minimo, maximo)


def config_vigente_settings(conn: psycopg.Connection) -> dict[str, Any]:
    """Settings de la `config_version` vigente; sin fila o sin claves = aborta."""
    fila = conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()
    if fila is None:
        raise PrecioGoalInvalido("config sin precio_goal_min_pct: no hay config_version vigente")
    return fila[0]


def _validar_fila(
    *,
    goal_pct: str,
    mode: str,
    go_literal: str | None,
    settings: Mapping,
) -> Decimal:
    if mode not in MODOS_PRECIO_GOAL:
        raise PrecioGoalInvalido(f"mode invalido: {mode!r} (vocabulario: shadow|live)")
    fraccion = fraccion_desde_porcentaje(goal_pct)
    minimo, maximo = banda_desde_settings(settings)
    if not minimo <= fraccion <= maximo:
        raise PrecioGoalInvalido(
            f"goal {goal_pct} % (= {fraccion}) fuera de banda [{minimo}, {maximo}]"
        )
    if mode == "live":
        if go_literal is None or not go_literal.strip():
            raise PrecioGoalInvalido(
                "live sin go: subir a live exige go literal no vacio"
                " (CHECK precio_goal_live_exige_go)"
            )
    elif go_literal is not None:
        raise PrecioGoalInvalido(
            "shadow es sin go literal: el go solo aplica a live (CHECK precio_goal_live_exige_go)"
        )
    return fraccion


ORIGENES_PRECIO_GOAL = ("manual", "margen_de_hoy", "cambio_de_modo")


def sembrar_goal(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    platform: str,
    goal_pct: str,
    mode: str,
    go_literal: str | None,
    creado_por: str = "precio_goal",
    origen: str | None = None,
    lote: str | None = None,
    m_referencia: Decimal | None = None,
) -> int:
    """INSERTa el goal vigente; devuelve su id. Falla cerrado y en voz alta.

    G.1: `origen`/`lote`/`m_referencia` son el rastro de la siembra (los
    escribe `aplicar_plan`; directos quedan NULL). Reeditar el mismo dia
    se permite desde la 0054 (la fila anterior queda ANULADA).
    """
    if platform not in PLATAFORMAS_PRECIO_GOAL:
        raise PrecioGoalInvalido(
            f"platform invalida: {platform!r} (vocabulario: amazon_mx|amazon_us|meli)"
        )
    if origen is not None and origen not in ORIGENES_PRECIO_GOAL:
        raise PrecioGoalInvalido(
            f"origen invalido: {origen!r} (vocabulario: manual|margen_de_hoy|cambio_de_modo)"
        )
    fraccion = _validar_fila(
        goal_pct=goal_pct,
        mode=mode,
        go_literal=go_literal,
        settings=config_vigente_settings(conn),
    )
    vigente = conn.execute(
        "SELECT id FROM precio_goal WHERE listing_id = %s AND platform = %s AND valid_to IS NULL",
        (listing_id, platform),
    ).fetchone()
    if vigente is not None:
        raise PrecioGoalInvalido(
            f"ya hay goal vigente para listing {listing_id} en {platform}:"
            " cierralo con --cerrar antes de sembrar otro"
        )
    try:
        fila = conn.execute(
            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
            " valid_from, creado_por, go_literal, origen, lote, m_referencia)"
            " VALUES (%s, %s, %s, %s, (now() AT TIME ZONE 'UTC')::date, %s, %s,"
            " %s, %s, %s)"
            " RETURNING id",
            (
                listing_id,
                platform,
                fraccion,
                mode,
                creado_por,
                go_literal,
                origen,
                lote,
                m_referencia,
            ),
        ).fetchone()
    except (
        psycopg.errors.UniqueViolation,
        psycopg.errors.ExclusionViolation,
    ) as exc:
        raise PrecioGoalInvalido(
            f"ya hay goal vigente para listing {listing_id} en {platform}:"
            " cierralo con --cerrar antes de sembrar otro"
        ) from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        raise PrecioGoalAusente(
            f"listing {listing_id} inexistente en {platform}: sin listing no hay goal"
        ) from exc
    except psycopg.errors.CheckViolation as exc:
        nombre = exc.diag.constraint_name if exc.diag else "?"
        raise PrecioGoalInvalido(
            f"la base rechazo el goal ({nombre}): revisa banda y go literal"
        ) from exc
    assert fila is not None
    return int(fila[0])


def cerrar_goal(conn: psycopg.Connection, *, listing_id: int, platform: str) -> int:
    """Fija `valid_to` del goal vigente (la unica mutacion que el trigger
    admite); devuelve su id. Sin vigente = `PrecioGoalAusente`."""
    if platform not in PLATAFORMAS_PRECIO_GOAL:
        raise PrecioGoalInvalido(
            f"platform invalida: {platform!r} (vocabulario: amazon_mx|amazon_us|meli)"
        )
    fila = conn.execute(
        "SELECT id FROM precio_goal WHERE listing_id = %s AND platform = %s AND valid_to IS NULL",
        (listing_id, platform),
    ).fetchone()
    if fila is None:
        raise PrecioGoalAusente(
            f"sin goal vigente para listing {listing_id} en {platform}: nada que cerrar"
        )
    conn.execute(
        "UPDATE precio_goal SET valid_to = (now() AT TIME ZONE 'UTC')::date WHERE id = %s",
        (fila[0],),
    )
    return int(fila[0])


@dataclass(frozen=True)
class PorIds:
    """Filtro explicito: los pares `(listing_id, platform)` pedidos."""

    pares: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class HechoPlan:
    """Una fila aplicada: lo que el tool imprime por fila."""

    listing_id: int
    platform: str
    accion: str
    goal_id: int
    cerro_id: int | None


@dataclass(frozen=True)
class OmitidaPlan:
    """Una fila NO escrita: la omision deja de ser silenciosa (B-C1).
    `bloqueo`/`motivo` son los del plan fresco (los mismos que se vieron
    en la vista previa)."""

    listing_id: int
    platform: str
    bloqueo: str | None
    motivo: str | None


@dataclass(frozen=True)
class ResultadoPlan:
    """Lo que `aplicar_plan` escribio. `hechos` extiende el bosquejo K: el
    tool imprime un `[hecho]` por fila, como `precio_reversa`. `omitidas`
    lista TODA fila no escrita (`nada` o bloqueada sin confirmar): cada
    fila del plan fresco sale en `hechos` o en `omitidas`, nunca en
    ninguna (B-C1)."""

    sembrados: int
    cerrados: int
    lote: str  # = la huella que autorizo el lote; queda en cada fila sembrada
    hechos: tuple[HechoPlan, ...]
    omitidas: tuple[OmitidaPlan, ...] = ()


_SQL_LISTING = (
    "SELECT id, product_id, external_id, seller_sku FROM listing WHERE id = %s AND platform = %s"
)
_SQL_VIGENTE = (
    "SELECT goal_id, margen_goal_pct, mode::text FROM v_precio_goal_vigente"
    " WHERE listing_id = %s AND platform = %s"
)
_SQL_ESCENARIO = (
    "SELECT id, contribucion_pct, componentes, fee_observation_id,"
    " oferta_observation_id, moneda, canal FROM estimacion_escenario"
    " WHERE listing_id = %s AND platform = %s AND estado = 'disponible'"
    " ORDER BY observed_at DESC LIMIT 1"
)
_SQL_OFERTA = "SELECT price_amount, price_currency FROM estimacion_oferta_observation WHERE id = %s"
_SQL_FEE = (
    "SELECT total_fees, fee_details, quoted_price_amount, estado"
    " FROM estimacion_fee_observation WHERE id = %s"
)


def leer_candidatos(conn: psycopg.Connection, *, filtro: PorIds) -> tuple[Candidato, ...]:
    """Unidades pedidas con su goal vigente y su lectura de hoy (solo base).

    G.1 lee `listing` + `v_precio_goal_vigente` + el escenario disponible
    mas reciente con su oferta y su fee (la misma fuente del tool, sin
    overlay de frescura: el contrato congela esa lectura). La vitrina
    alimenta candidatos en G.2, con los adaptadores de F.4.
    """
    if not isinstance(filtro, PorIds):
        raise TypeError(f"filtro desconocido: {type(filtro).__name__}")
    vistos: set[tuple[int, str]] = set()
    for lid, plat in filtro.pares:
        if isinstance(lid, bool) or not isinstance(lid, int):
            raise PrecioGoalInvalido(f"listing_id invalido: {lid!r}")
        if plat not in PLATAFORMAS_PRECIO_GOAL:
            raise PrecioGoalInvalido(
                f"platform invalida: {plat!r} (vocabulario: amazon_mx|amazon_us|meli)"
            )
        if (lid, plat) in vistos:
            raise PrecioGoalInvalido(
                f"par repetido (listing_id, platform) = {(lid, plat)}:"
                " el lote se aborta antes del plan"
            )
        vistos.add((lid, plat))
    candidatos: list[Candidato] = []
    for lid, plat in filtro.pares:
        fila = conn.execute(_SQL_LISTING, (lid, plat)).fetchone()
        if fila is None:
            raise PrecioGoalAusente(f"listing {lid} inexistente en {plat}: sin listing no hay goal")
        _id, product_id, externo, sku = fila
        vig = conn.execute(_SQL_VIGENTE, (lid, plat)).fetchone()
        vigente = None if vig is None else GoalVigente(id=int(vig[0]), goal=vig[1], mode=vig[2])
        esc = conn.execute(_SQL_ESCENARIO, (lid, plat)).fetchone()
        lectura: LecturaHoy | None = None
        canal: str | None = None
        if esc is not None:
            canal = esc[6]
            oferta = conn.execute(_SQL_OFERTA, (esc[4],)).fetchone()
            fee = conn.execute(_SQL_FEE, (esc[3],)).fetchone()
            lectura = LecturaHoy(
                m_pct=esc[1],
                p_actual=None if oferta is None else oferta[0],
                moneda=None if oferta is None else oferta[1],
                componentes=esc[2],
                fee_total=None if fee is None else fee[0],
                fee_quoted=None if fee is None else fee[2],
                fee_detalles=None if fee is None else fee[1],
                fee_estado=None if fee is None else fee[3],
            )
        candidatos.append(
            Candidato(
                unidad=UnidadPrecio(
                    listing_id=lid,
                    plataforma=plat,
                    externo=externo,
                    canal=canal,
                    producto_ancla=product_id,
                    miembros=(Miembro(product_id, sku, None),),
                ),
                seller_sku=sku,
                goal_vigente=vigente,
                lectura=lectura,
            )
        )
    return tuple(candidatos)


_ORIGEN_POR_PEDIDO = {
    PedidoMargenDeHoy: "margen_de_hoy",
    PedidoValor: "manual",
    PedidoValores: "manual",
    PedidoModo: "cambio_de_modo",
}


def _pct_texto(fraccion: Decimal) -> str:
    # El plan trae fraccion (4 decimales); `sembrar_goal` valida texto en
    # por ciento con 2 decimales: el redondeo es exacto en los dos sentidos.
    return f"{fraccion * 100:.2f}"


def _m_referencia_de(m_hoy: Decimal | None) -> Decimal | None:
    # La columna es NUMERIC(8, 4): se cuantiza aqui, explicito, en vez de
    # dejar que la base redondee en silencio.
    if m_hoy is None:
        return None
    return m_hoy.quantize(Decimal("0.0001"))


def aplicar_plan(
    conn: psycopg.Connection,
    plan: PlanGoals,
    *,
    huella: str,
    confirmados: frozenset[int],
    go_literal: str | None,
    actor: str,
    estricto: bool = False,
) -> ResultadoPlan:
    """Escribe el plan ENTERO en una transaccion o no escribe nada.

    Recalcula adentro (`leer_candidatos` + `planear`); si su huella no
    coincide con `huella` -> `HuellaDistinta` con el plan nuevo (nada
    escrito). Se escribe el recalculo fresco, no el plan viajero.
    Fila con bloqueo no confirmable, o confirmable sin confirmar: se omite
    y se LISTA en `omitidas` (B-C1); en `estricto` (el tool) aborta con
    `BloqueoSinConfirmar` en vez de omitir. `live` con filas accionables
    exige `go_literal` aunque ninguna sea escribible (W5: sin go, un plan
    live todo bloqueado pintaba 0/0 como exito). Reemplazar = cerrar +
    sembrar; reeditar el mismo dia deja la anterior ANULADA
    (`sembrar_goal`/`cerrar_goal` son los pasos internos).
    """
    if not conn.autocommit:
        raise ValueError("aplicar_plan exige conexion en autocommit: el lote confirma al salir")
    confirmados = frozenset(confirmados)
    pares = tuple((f.listing_id, f.plataforma) for f in plan.filas)
    ajenos = sorted(set(confirmados) - {lid for lid, _plat in pares})
    if ajenos:
        raise PrecioGoalInvalido(f"confirmados fuera del plan: {ajenos}")
    candidatos = leer_candidatos(conn, filtro=PorIds(pares=pares))
    minimo, maximo = banda_desde_settings(config_vigente_settings(conn))
    nuevo = planear(candidatos, pedido=plan.pedido, banda=(minimo, maximo))
    if nuevo.huella != huella:
        raise HuellaDistinta(nuevo, esperada=huella)
    escribibles = []
    omitidas: list[OmitidaPlan] = []
    for fila in nuevo.filas:
        if fila.accion == "nada":
            omitidas.append(
                OmitidaPlan(fila.listing_id, fila.plataforma, fila.bloqueo, fila.motivo)
            )
            continue
        if fila.bloqueo is not None and not (fila.confirmable and fila.listing_id in confirmados):
            if estricto:
                raise BloqueoSinConfirmar(nuevo, confirmados=confirmados)
            omitidas.append(
                OmitidaPlan(fila.listing_id, fila.plataforma, fila.bloqueo, fila.motivo)
            )
            continue
        escribibles.append(fila)
    modo = getattr(plan.pedido, "mode", None)
    if modo == "live" and (go_literal is None or not go_literal.strip()):
        if escribibles:
            raise PrecioGoalInvalido(
                "live sin go: subir a live exige go literal no vacio"
                " (CHECK precio_goal_live_exige_go)"
            )
        if any(f.accion != "nada" for f in nuevo.filas):
            raise PrecioGoalInvalido(
                "live sin go: el plan trae filas accionables pero ninguna escribible"
                " (todas omitidas por bloqueo); sin go no hay exito que pintar"
            )
    if go_literal is not None and modo != "live":
        donde = "shadow" if modo == "shadow" else "cerrar"
        raise PrecioGoalInvalido(
            f"{donde} es sin go literal: el go solo aplica a live (CHECK precio_goal_live_exige_go)"
        )
    hechos: list[HechoPlan] = []
    sembrados = 0
    cerrados = 0
    with conn.transaction():
        for fila in escribibles:
            if fila.accion == "cerrar":
                gid = cerrar_goal(conn, listing_id=fila.listing_id, platform=fila.plataforma)
                cerrados += 1
                hechos.append(HechoPlan(fila.listing_id, fila.plataforma, "cerrar", gid, None))
                continue
            cerro = None
            if fila.accion == "reemplazar":
                cerro = cerrar_goal(conn, listing_id=fila.listing_id, platform=fila.plataforma)
                cerrados += 1
            assert fila.goal_propuesto is not None
            gid = sembrar_goal(
                conn,
                listing_id=fila.listing_id,
                platform=fila.plataforma,
                goal_pct=_pct_texto(fila.goal_propuesto),
                mode=modo,
                go_literal=go_literal if modo == "live" else None,
                creado_por=actor,
                origen=_ORIGEN_POR_PEDIDO[type(plan.pedido)],
                lote=huella,
                m_referencia=_m_referencia_de(fila.m_hoy),
            )
            sembrados += 1
            hechos.append(HechoPlan(fila.listing_id, fila.plataforma, fila.accion, gid, cerro))
    return ResultadoPlan(
        sembrados=sembrados,
        cerrados=cerrados,
        lote=huella,
        hechos=tuple(hechos),
        omitidas=tuple(omitidas),
    )
