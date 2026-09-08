"""Contrato HTTP de la fabrica de campanas: lectura y operaciones confirmadas."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import fabrica_plan as fp
from app import fabrica_web as fw
from app.api import ConexionLectura
from app.api_write import exige_token
from app.publicacion_fotos import FotoNoDisponible, fotos_publicacion


def _as_of_consulta(valor: str | None) -> datetime | None:
    """ISO-8601 con zona; None = el adaptador usa ahora UTC."""
    if valor is None or not valor.strip():
        return None
    texto = valor.strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        corte = datetime.fromisoformat(texto)
    except ValueError as exc:
        raise HTTPException(422, "as_of debe ser una fecha-hora ISO-8601 con zona.") from exc
    if corte.tzinfo is None:
        raise HTTPException(422, "as_of debe incluir zona horaria.")
    return corte.astimezone(UTC)


class _RutaFabrica(APIRoute):
    def get_route_handler(self):
        manejar = super().get_route_handler()

        async def sin_input_privado(request):
            try:
                return await manejar(request)
            except RequestValidationError:
                # Pydantic incluye input crudo (incluidos extras como token).
                raise fw.error(
                    422, "Los datos no son válidos. Revisa los campos y la confirmación."
                ) from None

        return sin_input_privado


router = APIRouter(prefix="/api/fabrica", tags=["fabrica"], route_class=_RutaFabrica)
Plataforma = Literal["amazon_mx", "amazon_us"]
Accion = Literal["pausar", "reconciliar", "registrar"]
Identificador = Annotated[int, Field(strict=True, ge=1, le=9223372036854775807)]
Lote = Annotated[str, Field(min_length=1, max_length=240, pattern=r"^[a-zA-Z0-9_-]+$")]


class _Cuerpo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ParametrosRol(_Cuerpo):
    budget: str = Field(min_length=1, max_length=14, pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    bid: str = Field(min_length=1, max_length=14, pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")

    @field_validator("budget", "bid")
    @classmethod
    def monto_valido(cls, valor):
        monto = Decimal(valor)
        if not monto.is_finite() or not Decimal("0") < monto <= Decimal("9999999999.99"):
            raise ValueError("monto fuera del rango permitido")
        return valor


class ObjetivoPlan(_Cuerpo):
    origen: Literal["margen_medido", "manual_lanzamiento"]
    acos_pct: str | None = Field(default=None, min_length=1, max_length=14)

    @model_validator(mode="after")
    def objetivo_valido(self):
        if self.origen == "margen_medido" and self.acos_pct is not None:
            raise ValueError("objetivo medido no recibe acos_pct manual")
        if self.origen == "manual_lanzamiento":
            if self.acos_pct is None:
                raise ValueError("objetivo manual exige acos_pct")
            try:
                valor = Decimal(self.acos_pct)
            except ArithmeticError as exc:
                raise ValueError("acos_pct manual no es decimal") from exc
            if (
                not valor.is_finite()
                or valor <= 0
                or valor.as_tuple().exponent < -2
                or valor > Decimal("9999.99")
            ):
                raise ValueError("acos_pct manual fuera de NUMERIC(6,2)")
        return self


class SolicitudPlan(_Cuerpo):
    plataforma: Plataforma
    tipo_producto: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")
    nombre_base: str = Field(min_length=1, max_length=100)
    productos: list[Identificador] | None = Field(default=None, min_length=1, max_length=100)
    listing_ids: list[Identificador] | None = Field(default=None, min_length=1, max_length=100)
    objetivo: ObjetivoPlan | None = None
    modo: Literal["shadow", "live"]
    parametros: dict[str, ParametrosRol]

    @field_validator("nombre_base")
    @classmethod
    def nombre_valido(cls, valor):
        if not valor.strip() or any(ord(c) < 32 for c in valor):
            raise ValueError("nombre vacio o con caracteres de control")
        return valor.strip()

    @field_validator("productos", "listing_ids")
    @classmethod
    def productos_unicos(cls, valor):
        if valor is None:
            return valor
        if len(valor) != len(set(valor)):
            raise ValueError("productos repetidos")
        return valor

    @model_validator(mode="after")
    def parametros_validos(self):
        es_v1 = self.productos is not None and self.listing_ids is None and self.objetivo is None
        es_v2 = (
            self.productos is None and self.listing_ids is not None and self.objetivo is not None
        )
        if not (es_v1 or es_v2):
            raise ValueError("usa solo productos v1 o listing_ids y objetivo v2")
        if set(self.parametros) != set(fp.ROLES_ORDEN_CREACION):
            raise ValueError("se requieren exactamente los cinco roles")
        fp.valida_parametros(
            {
                rol: fp.ParametrosRol(rol, Decimal(p.budget), Decimal(p.bid))
                for rol, p in self.parametros.items()
            },
            fp.MONEDA_POR_PLATAFORMA[self.plataforma],
        )
        return self


class SolicitudCrear(_Cuerpo):
    solicitud: SolicitudPlan
    huella: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmacion: Literal["CREAR 5 CAMPAÑAS"]


class Confirmacion(_Cuerpo):
    confirmacion: str = Field(min_length=1, max_length=40)


@router.get("/catalogo")
def catalogo(
    conn: ConexionLectura,
    plataforma: Plataforma,
    as_of: Annotated[str | None, Query(max_length=64)] = None,
):
    return fw.catalogo(conn, plataforma, as_of=_as_of_consulta(as_of))


OrdenEvaluacion = Literal[
    "margen_observado",
    "ventas_totales",
    "revenue_ads",
    "gasto",
    "acos",
    "cpc",
    "cvr",
    "compras",
]


@router.get("/evaluacion")
def evaluacion(
    conn: ConexionLectura,
    plataforma: Plataforma,
    orden: OrdenEvaluacion = "margen_observado",
    direccion: Literal["asc", "desc"] = "desc",
    objetivo: Decimal | None = None,
    as_of: Annotated[str | None, Query(max_length=64)] = None,
):
    """Evaluacion completa por publicacion (ORBIT 19 B.4): economia observada
    + Ads + disponibilidad + objetivo del grupo en preparacion. Orden estable
    con NULL al final; ninguna etiqueta bloquea la seleccion.

    `objetivo` es el objetivo manual del grupo que el dueno ESTA preparando
    (D2/0.4 §3): toma precedencia sobre grupos con lote 'planeado' porque en
    el flujo real el grupo solo existe al crear campanas. No acredita
    rentabilidad; fuera de (0, 100] rechaza con 422.

    `as_of` alinea el corte de estimacion con el de catalogo (mismo ISO UTC)."""
    if objetivo is not None and not (Decimal(0) < objetivo <= Decimal(100)):
        raise HTTPException(422, "El objetivo debe estar en (0, 100].")
    return fw.evaluacion(
        conn,
        plataforma,
        orden=orden,
        direccion=direccion,
        objetivo=objetivo,
        as_of=_as_of_consulta(as_of),
    )


@router.get("/publicaciones/{listing_id}/imagen")
def imagen_publicacion(
    listing_id: Annotated[int, Path(ge=1, le=9223372036854775807)], conn: ConexionLectura
):
    fila = conn.execute(
        "SELECT platform::text, external_id FROM listing WHERE id = %s", (listing_id,)
    ).fetchone()
    if fila is None:
        raise HTTPException(404, "Publicacion sin foto disponible.")
    try:
        foto = fotos_publicacion.obtener(fila[0], fila[1])
    except FotoNoDisponible:
        raise HTTPException(
            503, "Foto temporalmente no disponible.", headers={"Retry-After": "60"}
        ) from None
    if foto is None:
        raise HTTPException(404, "Publicacion sin foto disponible.")
    contenido, mime = foto
    return Response(
        contenido,
        media_type=mime,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/plan")
def plan(cuerpo: SolicitudPlan, conn: ConexionLectura):
    return fw.previsualizar(conn, cuerpo.model_dump())


@router.post("/crear")
def crear(_token: Annotated[str, Depends(exige_token)], cuerpo: SolicitudCrear):
    return fw.crear(cuerpo.solicitud.model_dump(), cuerpo.huella, cuerpo.confirmacion)


@router.get("/lotes")
def lotes(conn: ConexionLectura, plataforma: Plataforma):
    return fw.lotes(conn, plataforma)


@router.get("/lotes/{lote}")
def detalle(lote: Lote, conn: ConexionLectura):
    resultado = fw.detalle_lote(conn, lote)
    if resultado is None:
        raise fw.error(404, "El lote no existe.", lote)
    return resultado


@router.post("/lotes/{lote}/{accion}")
def operar(
    _token: Annotated[str, Depends(exige_token)],
    lote: Lote,
    accion: Accion,
    cuerpo: Confirmacion,
):
    if cuerpo.confirmacion != f"{accion.upper()} GRUPO":
        raise fw.error(422, "La confirmación no coincide con la operación.", lote)
    return fw.operar(lote, accion, cuerpo.confirmacion)
