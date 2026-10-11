"""Contrato HTTP de la fabrica de campanas: lectura y operaciones confirmadas."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import fabrica_plan as fp
from app import fabrica_web as fw
from app import impulso_io as io
from app import retiro_anuncios as ra
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
            except RequestValidationError as exc:
                # Pydantic incluye input crudo (incluidos extras como token).
                # Excepcion A7 B8: el "no es finito" no lleva el valor (frase
                # fija del validador) y el plan exige el mensaje, no el generico.
                for error in exc.errors():
                    mensaje = str(error.get("msg", ""))
                    if "no es finito" in mensaje:
                        raise fw.error(422, mensaje) from None
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
    fuente_bid: Literal["manual", "amazon_v4"] = "manual"
    recomendaciones: list[RecomendacionBid] = Field(default_factory=list, max_length=500)

    @field_validator("budget", "bid")
    @classmethod
    def monto_valido(cls, valor):
        monto = Decimal(valor)
        if not monto.is_finite() or not Decimal("0") < monto <= Decimal("9999999999.99"):
            raise ValueError("monto fuera del rango permitido")
        return valor

    @model_validator(mode="after")
    def procedencia_valida(self):
        if self.fuente_bid == "manual" and self.recomendaciones:
            raise ValueError("una puja manual no lleva recomendaciones Amazon")
        if self.fuente_bid == "amazon_v4" and not self.recomendaciones:
            raise ValueError("una puja Amazon exige recomendaciones")
        return self


class RecomendacionBid(_Cuerpo):
    tipo: str = Field(min_length=1, max_length=60, pattern=r"^[A-Z_]+$")
    valor: str | None = Field(default=None, max_length=500)
    minimo: str = Field(min_length=1, max_length=14, pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    sugerido: str = Field(min_length=1, max_length=14, pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    maximo: str = Field(min_length=1, max_length=14, pattern=r"^[0-9]+(?:\.[0-9]+)?$")

    @model_validator(mode="after")
    def terna_valida(self):
        valores = tuple(Decimal(v) for v in (self.minimo, self.sugerido, self.maximo))
        if not all(v.is_finite() and v > 0 for v in valores) or not (
            valores[0] <= valores[1] <= valores[2]
        ):
            raise ValueError("terna de bid Amazon invalida")
        return self


class ObjetivoPlan(_Cuerpo):
    origen: Literal["margen_medido", "manual_lanzamiento"]
    acos_pct: Annotated[str, Field(min_length=1, max_length=14)] | float | None = None

    @model_validator(mode="after")
    def objetivo_valido(self):
        if self.origen == "margen_medido" and self.acos_pct is not None:
            raise ValueError("objetivo medido no recibe acos_pct manual")
        if self.origen == "manual_lanzamiento":
            if self.acos_pct is None:
                raise ValueError("objetivo manual exige acos_pct")
            crudo = self.acos_pct
            if isinstance(crudo, float):
                # A7: el form puede mandar JSON numerico (NaN/Infinity de un
                # campo roto); Decimal(float) expande el binario, asi que el
                # finito se valida via str y el no-finito cae con el MISMO
                # mensaje que el str (aguas abajo _decimal tambien hace str).
                if not math.isfinite(crudo):
                    raise ValueError("acos_pct manual no es finito")
            else:
                try:
                    valor = Decimal(crudo)
                except ArithmeticError as exc:
                    raise ValueError("acos_pct manual no es decimal") from exc
                if not valor.is_finite():
                    raise ValueError("acos_pct manual no es finito")
            # A1: el API valida FORMA (decimal finito); el RANGO vive en una
            # sola fuente, fabrica_plan.valida_banda_manual, que corre al
            # construir en _datos_plan_v2 (CLI, preview, crear y
            # bids-sugeridos rechazan con el MISMO mensaje de banda; jamas
            # al releer lotes viejos, F1 AI-review PR #381).
        return self


class SolicitudPlan(_Cuerpo):
    plataforma: Plataforma
    # A2: opcional (con familia unica sale del slug); presente se valida igual.
    tipo_producto: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$"
    )
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


class SolicitudBids(_Cuerpo):
    plataforma: Plataforma
    # A2: opcional (con familia unica sale del slug); presente se valida igual.
    tipo_producto: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$"
    )
    listing_ids: list[Identificador] = Field(min_length=1, max_length=100)
    objetivo: ObjetivoPlan

    @field_validator("listing_ids")
    @classmethod
    def listings_unicos(cls, valor):
        if len(valor) != len(set(valor)):
            raise ValueError("publicaciones repetidas")
        return valor


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


@router.post("/export-semillas")
def export_semillas(cuerpo: SolicitudPlan, conn: ConexionLectura):
    """JEV 2.2: plan canonico + huella + filas fuente de semillas para
    revisar el lote con el asesor. SOLO lectura de fuentes: no escribe y no
    toca la biblioteca."""
    return fw.exportar_semillas(conn, cuerpo.model_dump())


@router.get("/asesoria/{huella}")
def asesoria_huella(huella: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")], conn: ConexionLectura):
    """JEV 2.2: la revision guardada para esa huella (SOLO lectura; 404 si
    nadie la reviso todavia)."""
    vista = fw.asesoria_por_huella(conn, huella)
    if vista is None:
        raise HTTPException(status_code=404, detail="huella sin revision de asesoria")
    return {"asesoria": vista}


@router.post("/bids-sugeridos")
def bids_sugeridos(cuerpo: SolicitudBids, conn: ConexionLectura):
    return fw.sugerir_bids(conn, cuerpo.model_dump())


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


# ---------------------------------------------------------------------------
# Impulso y retiro (BIDS 02 I.5)
# ---------------------------------------------------------------------------

MotivoRetiro = Literal["impulso", "sin_pedidos", "subfamilia"]
Subfamilia = Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")]
Actor = Annotated[str, Field(min_length=1, max_length=100)]


def _actor_limpio(valor: str) -> str:
    """Actor 1..100 sin NUL (Postgres lo rechaza en text): se valida en
    el borde, antes de mutar nada."""
    if "\x00" in valor:
        raise ValueError("actor con NUL")
    if not valor.strip():
        raise ValueError("actor vacio")
    return valor.strip()


Huella = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Listings = Annotated[list[Identificador], Field(min_length=1, max_length=100)]


class SolicitudImpulsoPlan(_Cuerpo):
    plataforma: Plataforma
    listing_ids: Listings
    subfamilia_por_listing: dict[str, Subfamilia]
    tope_monto: Annotated[str, Field(min_length=1, max_length=14)] | None = None

    @field_validator("listing_ids")
    @classmethod
    def listings_unicos(cls, valor):
        if len(valor) != len(set(valor)):
            raise ValueError("publicaciones repetidas")
        return valor

    @field_validator("subfamilia_por_listing")
    @classmethod
    def subfamilias_validas(cls, valor):
        for clave in valor:
            try:
                lid = int(clave)
            except ValueError:
                raise ValueError(f"listing invalido en subfamilias: {clave!r}") from None
            if lid < 1:
                raise ValueError(f"listing invalido en subfamilias: {clave!r}")
        return valor

    @field_validator("tope_monto")
    @classmethod
    def tope_valido(cls, valor):
        if valor is None:
            return valor
        try:
            monto = Decimal(valor)
        except ArithmeticError:
            raise ValueError("tope fuera del rango permitido") from None
        if not monto.is_finite() or monto < Decimal("0.0001") or monto > Decimal("9999999999.9999"):
            raise ValueError("tope fuera del rango permitido")
        return valor

    def como_solicitud(self) -> io.SolicitudImpulso:
        return io.SolicitudImpulso(
            plataforma=self.plataforma,
            listing_ids=tuple(self.listing_ids),
            subfamilia_por_listing={int(k): v for k, v in self.subfamilia_por_listing.items()},
            tope_monto=Decimal(self.tope_monto) if self.tope_monto is not None else None,
        )


class CuerpoLanzarImpulso(_Cuerpo):
    """POST /api/fabrica/impulso/lanzar. `confirmacion` debe igualar
    VistaPreviaImpulso.confirmacion_esperada."""

    solicitud: SolicitudImpulsoPlan
    huella: Huella
    confirmacion: str = Field(min_length=1, max_length=60)
    actor: Actor

    @field_validator("actor")
    @classmethod
    def actor_no_vacio(cls, valor):
        return _actor_limpio(valor)


class CuerpoPausarImpulso(_Cuerpo):
    confirmacion: Literal["PAUSAR IMPULSO"]


class CuerpoRetiroPlan(_Cuerpo):
    plataforma: Plataforma
    listing_ids: Listings
    motivo: MotivoRetiro
    lote: Lote | None = None

    @field_validator("listing_ids")
    @classmethod
    def listings_unicos(cls, valor):
        if len(valor) != len(set(valor)):
            raise ValueError("publicaciones repetidas")
        return valor


class CuerpoRetiroEjecutar(_Cuerpo):
    plataforma: Plataforma
    listing_ids: Listings
    motivo: MotivoRetiro
    lote: Lote | None = None
    huella: Huella
    confirmacion: str = Field(min_length=1, max_length=60)
    actor: Actor

    @field_validator("listing_ids")
    @classmethod
    def listings_unicos(cls, valor):
        if len(valor) != len(set(valor)):
            raise ValueError("publicaciones repetidas")
        return valor

    @field_validator("actor")
    @classmethod
    def actor_no_vacio(cls, valor):
        return _actor_limpio(valor)


class CuerpoRetiroReponer(_Cuerpo):
    retiro_ids: Listings
    confirmacion: str = Field(min_length=1, max_length=60)
    actor: Actor

    @field_validator("retiro_ids")
    @classmethod
    def retiros_unicos(cls, valor):
        if len(valor) != len(set(valor)):
            raise ValueError("retiros repetidos")
        return valor

    @field_validator("actor")
    @classmethod
    def actor_no_vacio(cls, valor):
        return _actor_limpio(valor)


def _sugeridor_plataforma(plataforma: str, moneda: str):
    """Una llamada a recommend_bids por producto, con el perfil sellado
    de la plataforma. Falla 503 sin un perfil unico aceptado."""
    from app.ads.client import AdsClient
    from app.ads.config import AdsCredentials
    from app.ads.structure import evaluar_perfiles

    cred = AdsCredentials.from_secrets_dir()
    cliente = AdsClient(cred)
    perfiles = [p for p in evaluar_perfiles(cliente) if p.aceptado and p.platform == plataforma]
    if len(perfiles) != 1:
        raise fw.error(503, "Sin perfil aceptado unico para sugerir bids.")
    return io.sugeridor_amazon(cliente, perfiles[0].profile_id, moneda)


def _ejecutor_lanzar(conn, confirmacion: str):
    """EjecutorFabrica real: crea el lote con fc._mutar (shadow,
    esperado 2) y lee el grupo creado."""
    from types import SimpleNamespace

    import tools.fabrica_campanas as fc

    def ejecuta(plan_grupo, huella: str, lote: str) -> io.EjecucionFabrica:
        args = SimpleNamespace(
            acepto_mutacion_real=True, esperado=2, huella=huella, go=confirmacion
        )
        fc._mutar(args, plan_grupo, huella, lote=lote, revierte_al_fallar=True, crear_pausado=True)
        grupo = conn.execute(io._SQL_GRUPO_DE_LOTE, (lote,)).fetchone()
        estado = conn.execute("SELECT estado FROM fabrica_lote WHERE lote = %s", (lote,)).fetchone()
        if grupo is None or estado is None:
            raise fc.Abortar(f"lote {lote} sin grupo o estado tras _mutar")
        return io.EjecucionFabrica(lote=lote, grupo_id=grupo[0], estado_lote=estado[0])

    return ejecuta


def _error_impulso(exc: io.ErrorImpulso, *, vista: dict | None = None) -> HTTPException:
    detalle: dict = {"mensaje": str(exc)}
    if vista is not None:
        detalle["vista_previa"] = vista
    if exc.lanzados:
        detalle["lanzados"] = [
            {"impulso_id": x.impulso_id, "lote": x.lote, "estado_lote": x.estado_lote}
            for x in exc.lanzados
        ]
    if exc.fallos:
        detalle["fallos"] = {str(k): v for k, v in exc.fallos.items()}
    return HTTPException(exc.status, detail=detalle)


_SQL_IMPULSO_DE_GRUPO = """
SELECT id FROM impulso WHERE grupo_id = %s
"""


def _conservar_y_impulso(
    conn, lote: str | None, motivo: str, listing_ids
) -> tuple[int | None, int | None]:
    """Resuelve lote -> (grupo, impulso). 'impulso' exige lote con impulso
    que ya midio; 'subfamilia' exige lote propio; otros aceptan lote
    propio o nada."""
    if lote is None:
        if motivo == "impulso":
            raise fw.error(422, "El motivo 'impulso' exige el lote del impulso.")
        if motivo == "subfamilia":
            raise fw.error(422, "El motivo 'subfamilia' exige el lote del grupo nuevo.")
        return None, None
    grupo = ra.grupo_de_lote(conn, lote)
    if grupo is None:
        raise fw.error(404, "El lote no existe.", lote)
    if motivo == "impulso":
        imp = conn.execute(_SQL_IMPULSO_DE_GRUPO, (grupo,)).fetchone()
        if imp is None:
            raise fw.error(404, "El lote no tiene impulso.", lote)
        if not ra.impulso_tuvo_impresiones(conn, imp[0]):
            raise fw.error(422, "El impulso aun no tiene impresiones: nada que retirar.")
        return grupo, imp[0]
    if not ra.grupo_alcanza_listings(conn, grupo, listing_ids):
        raise fw.error(422, "El lote no es de estos productos.")
    return grupo, None


@router.post("/impulso/plan")
def impulso_plan(cuerpo: SolicitudImpulsoPlan, conn: ConexionLectura):
    moneda = fp.MONEDA_POR_PLATAFORMA[cuerpo.plataforma]
    try:
        vista = io.previsualiza(
            conn,
            cuerpo.como_solicitud(),
            sugeridor=_sugeridor_plataforma(cuerpo.plataforma, moneda),
        )
    except io.ErrorImpulso as exc:
        raise _error_impulso(exc) from None
    return vista.como_dict()


@router.post("/impulso/lanzar")
def impulso_lanzar(_token: Annotated[str, Depends(exige_token)], cuerpo: CuerpoLanzarImpulso):
    with fw._conexion("ORBIT_DSN_ADMIN", "impulso") as conn:
        moneda = fp.MONEDA_POR_PLATAFORMA[cuerpo.solicitud.plataforma]
        sugeridor = _sugeridor_plataforma(cuerpo.solicitud.plataforma, moneda)
        try:
            lanzados = io.lanza(
                conn,
                cuerpo.solicitud.como_solicitud(),
                huella=cuerpo.huella,
                confirmacion=cuerpo.confirmacion,
                actor=cuerpo.actor,
                ejecutor=_ejecutor_lanzar(conn, cuerpo.confirmacion),
                sugeridor=sugeridor,
            )
        except io.ErrorImpulso as exc:
            vista = None
            if exc.status == 409:
                vista = io.previsualiza(
                    conn, cuerpo.solicitud.como_solicitud(), sugeridor=sugeridor
                ).como_dict()
            raise _error_impulso(exc, vista=vista) from None
        return {
            "lanzados": [
                {"impulso_id": x.impulso_id, "lote": x.lote, "estado_lote": x.estado_lote}
                for x in lanzados
            ]
        }


_SQL_LOTE_DE_IMPULSO = """
SELECT lote FROM impulso WHERE id = %s
"""


@router.post("/impulso/{impulso_id}/pausar")
def impulso_pausar(
    _token: Annotated[str, Depends(exige_token)],
    impulso_id: Annotated[int, Path(ge=1, le=9223372036854775807)],
    cuerpo: CuerpoPausarImpulso,
):
    with fw._conexion("ORBIT_DSN_ADMIN", "impulso") as conn:
        fila = conn.execute(_SQL_LOTE_DE_IMPULSO, (impulso_id,)).fetchone()
        if fila is None:
            raise fw.error(404, "El impulso no existe.")
        try:
            io.PausadorFabrica(conn).pausa(fila[0])
        except io.ErrorImpulso as exc:
            raise _error_impulso(exc) from None
        conn.commit()
        return {"impulso_id": impulso_id, "lote": fila[0], "estado": "pausado"}


@router.post("/retiro/plan")
def retiro_plan(cuerpo: CuerpoRetiroPlan, conn: ConexionLectura):
    conservar, _imp = _conservar_y_impulso(conn, cuerpo.lote, cuerpo.motivo, cuerpo.listing_ids)
    plan = ra.planea_retiro(
        conn,
        cuerpo.plataforma,
        listing_ids=tuple(cuerpo.listing_ids),
        motivo=cuerpo.motivo,
        conservar_grupo_id=conservar,
    )
    return plan.como_dict()


@router.post("/retiro/ejecutar")
def retiro_ejecutar(_token: Annotated[str, Depends(exige_token)], cuerpo: CuerpoRetiroEjecutar):
    with fw._conexion("ORBIT_DSN_ADMIN", "retiro") as conn:
        conservar, imp = _conservar_y_impulso(conn, cuerpo.lote, cuerpo.motivo, cuerpo.listing_ids)
        plan = ra.planea_retiro(
            conn,
            cuerpo.plataforma,
            listing_ids=tuple(cuerpo.listing_ids),
            motivo=cuerpo.motivo,
            conservar_grupo_id=conservar,
        )
        if plan.huella != cuerpo.huella:
            raise fw.error(409, "El plan cambió. Vuelve a previsualizar antes de ejecutar.")
        n = len(plan.anuncios)
        if cuerpo.confirmacion != f"SACAR {n} ANUNCIOS":
            raise fw.error(422, "La confirmación no coincide con la operación.")
        try:
            retirados = ra.ejecuta_retiro(
                conn, plan, go=cuerpo.confirmacion, impulso_id=imp, actor=cuerpo.actor
            )
        except ValueError as exc:
            raise fw.error(422, str(exc)) from None
        except RuntimeError as exc:
            raise fw.error(502, f"Retiro parcial: {exc}") from None
        lote = ra.lote_retiro_de_plan(plan)
        ids = [f[0] for f in conn.execute(_SQL_ABIERTOS_DE_LOTE, (lote,)).fetchall()]
        return {"retirados": retirados, "lote_retiro": lote, "retiro_ids": ids}


_SQL_RETIROS_EXISTEN = """
SELECT count(*) FROM anuncio_retiro WHERE id = ANY(%s)
"""

_SQL_ABIERTOS_DE_LOTE = """
SELECT r.id FROM anuncio_retiro r
 WHERE r.lote_retiro = %s
   AND NOT EXISTS (SELECT 1 FROM anuncio_reposicion p WHERE p.retiro_id = r.id)
 ORDER BY r.id
"""


@router.post("/retiro/reponer")
def retiro_reponer(_token: Annotated[str, Depends(exige_token)], cuerpo: CuerpoRetiroReponer):
    with fw._conexion("ORBIT_DSN_ADMIN", "retiro") as conn:
        hay = conn.execute(_SQL_RETIROS_EXISTEN, (list(cuerpo.retiro_ids),)).fetchone()[0]
        if hay != len(cuerpo.retiro_ids):
            raise fw.error(404, "Algun retiro pedido no existe.")
        n = len(cuerpo.retiro_ids)
        if cuerpo.confirmacion != f"REGRESAR {n} ANUNCIOS":
            raise fw.error(422, "La confirmación no coincide con la operación.")
        try:
            repuestos = ra.repone(
                conn,
                retiro_ids=tuple(cuerpo.retiro_ids),
                go=cuerpo.confirmacion,
                actor=cuerpo.actor,
            )
        except ValueError as exc:
            raise fw.error(422, str(exc)) from None
        except RuntimeError as exc:
            raise fw.error(502, f"Reposicion parcial: {exc}") from None
        return {"repuestos": repuestos}
