"""Rutas de escritura de precios (REPRICING 02, G.2, bosquejo T).

`POST /api/precios/goals/plan` (vista previa, NO escribe),
`POST /api/precios/goals/aplicar` (todo o nada con huella) y
`POST /api/precios/compuerta/{corrida_id}/liberar` (idempotente).

Mismo patron que `app/api_write.py`: `exige_token` ANTES de abrir la
conexion admin (se reutilizan `exige_token` y `ConexionEscritura`: un
solo token y una sola conexion). Los cuerpos son modelos pydantic del
router y no salen de el; el pedido se convierte a `siembra.Pedido` aqui
y el plan se serializa aqui (`siembra` no trae `como_dict`).

Mapeo de errores (el endpoint no inventa semantica):
`HuellaDistinta` -> 409 con `{"message", "plan_nuevo"}` (mismo shape que
`aplicar_ajuste` en `api_write.py`); `CorridaSinRetencion` -> 409 (no se
suelta lo que no se retuvo); `PrecioGoalAusente` -> 404 (listing sin
publicacion); `PrecioGoalInvalido`/`PedidoInvalido`/actor en blanco ->
422. `live` sin go literal lo rechaza `aplicar_plan` (422) antes de
abrir la transaccion.

Tope: 500 unidades por llamada (bosquejo T, tope de
`/api/familias/asignar`): en `listing_ids` lo pinean los modelos
(`max_length=500`); en `sin_goal` lo frena `_seleccion` con 422 (las
dos rutas comparten el ayudante para no planear lo que no se puede
aplicar).

`aplicar_plan` exige conexion en autocommit (el lote confirma al
salir): la ruta de aplicar la enciende antes de llamar. La de liberar
tambien (el re-lee de la carrera de `liberar` no corre en una
transaccion abortada); el plan solo lee y no toca nada.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as RutaPath
from pydantic import BaseModel, Field, model_validator

from app.api_common import _dec_str
from app.api_write import ConexionEscritura, exige_token
from app.precio import liberaciones
from app.precio.goals_write import (
    HuellaDistinta,
    PorIds,
    PrecioGoalAusente,
    PrecioGoalInvalido,
    aplicar_plan,
    banda_desde_settings,
    config_vigente_settings,
    fraccion_desde_porcentaje,
    leer_candidatos,
)
from app.precio.liberaciones import CorridaSinRetencion
from app.precio.siembra import (
    Pedido,
    PedidoCierre,
    PedidoInvalido,
    PedidoMargenDeHoy,
    PedidoModo,
    PedidoValor,
    PedidoValores,
    PlanGoals,
    ValorPorUnidad,
    planear,
)

router = APIRouter(prefix="/api/precios", tags=["precios-write"])

# Tope de unidades por llamada (bosquejo T): el mismo de
# `/api/familias/asignar` (`CuerpoAsignar.product_ids`, `max_length=500`).
TOPE_UNIDADES = 500

Plataforma = Literal["amazon_mx", "amazon_us", "meli"]
TipoPedido = Literal["margen_de_hoy", "valor", "valores", "modo", "cerrar"]
ModoSiembra = Literal["shadow", "live"]


class MetaUnidad(BaseModel):
    """Un goal a mano para una unidad (elemento de `valores`)."""

    listing_id: int = Field(ge=1)
    goal_pct: str = Field(min_length=1, max_length=20)


class PedidoCuerpo(BaseModel):
    """El pedido del plan: `tipo` + sus parametros y nada mas.

    `mode` lo exigen todos menos `cerrar`; `goal_pct` solo `valor`;
    `goals` solo `valores` (un goal por unidad, en por ciento como el
    `--goal-pct` del tool). Lo ajeno sobra (422, como `--cerrar` con
    `--goal-pct` en el tool)."""

    tipo: TipoPedido
    mode: ModoSiembra | None = None
    goal_pct: str | None = Field(default=None, min_length=1, max_length=20)
    goals: list[MetaUnidad] | None = None


class CuerpoPlan(BaseModel):
    """`{platform, pedido, listing_ids | sin_goal}` (bosquejo T).

    Una sola plataforma por llamada; la seleccion es excluyente: ids
    explicitos (1..500) o todas las sin goal vigente de la plataforma."""

    platform: Plataforma
    pedido: PedidoCuerpo
    listing_ids: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None, min_length=1, max_length=TOPE_UNIDADES
    )
    sin_goal: bool = False

    @model_validator(mode="after")
    def _seleccion_excluyente(self):
        if bool(self.listing_ids) == bool(self.sin_goal):
            raise ValueError("pasa listing_ids o sin_goal: son excluyentes")
        return self


class CuerpoAplicar(CuerpoPlan):
    """El plan que se vio (el mismo `platform/pedido/seleccion`) + la
    huella que lo autoriza, los `listing_id` confirmados (bloqueos
    confirmables) y, en `live`, el go literal del dueno."""

    huella: str = Field(min_length=1, max_length=64)
    confirmados: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    go_literal: str | None = Field(default=None, max_length=500)
    actor: str = Field(min_length=1, max_length=200)


class CuerpoLiberar(BaseModel):
    """Quien suelta y por que (la nota queda en la fila)."""

    actor: str = Field(min_length=1, max_length=200)
    nota: str = Field(min_length=1, max_length=2000)


def _pedido_de(cuerpo: PedidoCuerpo, platform: str) -> Pedido:
    """El `Pedido` de `siembra` desde el cuerpo. Lo ajeno al tipo sobra;
    lo que falta aborta: 422 en la ruta (`PrecioGoalInvalido`)."""
    tipo, mode, goal_pct, goals = cuerpo.tipo, cuerpo.mode, cuerpo.goal_pct, cuerpo.goals
    if tipo == "cerrar":
        if mode is not None or goal_pct is not None or goals is not None:
            raise PrecioGoalInvalido("cerrar no siembra: mode/goal_pct/goals sobran")
        return PedidoCierre()
    if mode is None:
        raise PrecioGoalInvalido(f"falta mode (shadow|live) para el pedido {tipo}")
    if tipo == "margen_de_hoy":
        if goal_pct is not None or goals is not None:
            raise PrecioGoalInvalido("margen_de_hoy copia el margen: goal_pct/goals sobran")
        return PedidoMargenDeHoy(mode=mode)
    if tipo == "modo":
        if goal_pct is not None or goals is not None:
            raise PrecioGoalInvalido("modo conserva el goal: goal_pct/goals sobran")
        return PedidoModo(mode=mode)
    if tipo == "valor":
        if goal_pct is None:
            raise PrecioGoalInvalido("falta goal_pct (en por ciento, ej. 30.00)")
        if goals is not None:
            raise PrecioGoalInvalido("valor es un goal para todas: goals sobra")
        return PedidoValor(goal=fraccion_desde_porcentaje(goal_pct), mode=mode)
    if goals is None or not goals:
        raise PrecioGoalInvalido("falta goals: un goal_pct por unidad")
    if goal_pct is not None:
        raise PrecioGoalInvalido("valores trae un goal por unidad: goal_pct sobra")
    return PedidoValores(
        goals=tuple(
            ValorPorUnidad(g.listing_id, platform, fraccion_desde_porcentaje(g.goal_pct))
            for g in goals
        ),
        mode=mode,
    )


# Las unidades sin goal vigente de la plataforma, por id (la seleccion de
# G.3 para "margen de hoy"). Solo lectura: el candado de escritor unico
# de `precio_goal` cubre UPDATE/INSERT, no SELECT.
_SQL_SIN_GOAL = """
SELECT l.id FROM listing l
LEFT JOIN v_precio_goal_vigente v ON v.listing_id = l.id AND v.platform = l.platform
WHERE l.platform = %s AND v.goal_id IS NULL
ORDER BY l.id
"""


def _seleccion(conn, cuerpo: CuerpoPlan) -> tuple[tuple[int, str], ...]:
    """Los pares `(listing_id, platform)` del cuerpo, ya acotados.

    `sin_goal` resuelve contra la base con el MISMO orden en plan y
    aplicar (el ayudante es comun: lo que se planea se puede aplicar).
    Mas de `TOPE_UNIDADES` -> 422, tambien en `sin_goal`."""
    if cuerpo.listing_ids is not None:
        pares = tuple((lid, cuerpo.platform) for lid in cuerpo.listing_ids)
    else:
        pares = tuple(
            (fila[0], cuerpo.platform) for fila in conn.execute(_SQL_SIN_GOAL, (cuerpo.platform,))
        )
    if len(pares) > TOPE_UNIDADES:
        raise PrecioGoalInvalido(
            f"mas de {TOPE_UNIDADES} unidades ({len(pares)}): parte la llamada"
        )
    return pares


def _plan_fresco(conn, cuerpo: CuerpoPlan) -> PlanGoals:
    """El plan recalculado contra la base de ahora (lo usan plan y
    aplicar: aplicar_plan lo vuelve a recalcular adentro y exige la
    huella contra SU recalculo)."""
    pedido = _pedido_de(cuerpo.pedido, cuerpo.platform)
    candidatos = leer_candidatos(conn, filtro=PorIds(pares=_seleccion(conn, cuerpo)))
    return planear(
        candidatos, pedido=pedido, banda=banda_desde_settings(config_vigente_settings(conn))
    )


def _importe_str(importe) -> dict | None:
    if importe is None:
        return None
    return {"valor": _dec_str(importe.valor), "moneda": importe.moneda}


def _fila_plan_dict(fila) -> dict:
    return {
        "listing_id": fila.listing_id,
        "plataforma": fila.plataforma,
        "seller_sku": fila.seller_sku,
        "accion": fila.accion,
        "m_hoy": _dec_str(fila.m_hoy),
        "goal_id": fila.goal_id,
        "goal_actual": _dec_str(fila.goal_actual),
        "goal_propuesto": _dec_str(fila.goal_propuesto),
        "p_actual": _importe_str(fila.p_actual),
        "p_estrella": _importe_str(fila.p_estrella),
        "envio_origen": fila.envio_origen,
        "bloqueo": fila.bloqueo,
        "confirmable": fila.confirmable,
        "motivo": fila.motivo,
    }


def _plan_dict(plan: PlanGoals) -> dict:
    return {"huella": plan.huella, "filas": [_fila_plan_dict(f) for f in plan.filas]}


@router.post("/goals/plan")
def plan_goals(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoPlan,
) -> dict:
    """Vista previa del plan de goals: filas + huella. NO escribe (solo
    `leer_candidatos` + `planear`). 404 listing sin publicacion; 422
    pedido o seleccion que no cierra."""
    try:
        plan = _plan_fresco(conn, cuerpo)
    except PrecioGoalAusente as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except (PrecioGoalInvalido, PedidoInvalido) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _plan_dict(plan)


@router.post("/goals/aplicar")
def aplicar_goals(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoAplicar,
) -> dict:
    """Aplica el plan que el dueno vio: lo recalcula y escribe el lote
    ENTERO o nada (`aplicar_plan`, una transaccion). 409 con el plan
    nuevo si la huella ya no coincide; 422 `live` sin go literal (lo
    exige `aplicar_plan` antes de abrir la transaccion) o pedido que no
    cierra; 404 listing sin publicacion."""
    if not cuerpo.actor.strip():
        raise HTTPException(status_code=422, detail="actor en blanco: quien aplica firma")
    try:
        plan = _plan_fresco(conn, cuerpo)
    except PrecioGoalAusente as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except (PrecioGoalInvalido, PedidoInvalido) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # `aplicar_plan` exige autocommit (el lote confirma al salir de SU
    # transaccion) y psycopg no deja cambiarlo con transaccion abierta:
    # se suelta el snapshot de lectura (`aplicar_plan` relee adentro).
    conn.rollback()
    conn.autocommit = True
    try:
        resultado = aplicar_plan(
            conn,
            plan,
            huella=cuerpo.huella,
            confirmados=frozenset(cuerpo.confirmados),
            go_literal=cuerpo.go_literal,
            actor=cuerpo.actor,
        )
    except HuellaDistinta as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "plan_nuevo": _plan_dict(exc.plan)},
        ) from None
    except (PrecioGoalInvalido, PedidoInvalido) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {
        "sembrados": resultado.sembrados,
        "cerrados": resultado.cerrados,
        "lote": resultado.lote,
        "hechos": [
            {
                "listing_id": h.listing_id,
                "platform": h.platform,
                "accion": h.accion,
                "goal_id": h.goal_id,
                "cerro_id": h.cerro_id,
            }
            for h in resultado.hechos
        ],
    }


@router.post("/compuerta/{corrida_id}/liberar")
def liberar_compuerta(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoLiberar,
    corrida_id: int = RutaPath(ge=1),
) -> dict:
    """El dueno suelta lo que la corrida retuvo. Idempotente: liberar dos
    veces devuelve la misma fila. 409 si esa corrida no retuvo nada
    (`CorridaSinRetencion`); 422 actor o nota en blanco."""
    # Autocommit: el re-lee de la carrera de `liberar` no corre en una
    # transaccion abortada (el INSERT unico confirma al salir).
    conn.autocommit = True
    try:
        hecha = liberaciones.liberar(
            conn, corrida_id=corrida_id, actor=cuerpo.actor, nota=cuerpo.nota
        )
    except CorridaSinRetencion as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {
        "corrida_id": hecha.corrida_id,
        "actor": hecha.actor,
        "nota": hecha.nota,
        "liberada_en": hecha.liberada_en.isoformat(),
    }
