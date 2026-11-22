"""Contrato HTTP de la fabrica de campanas: lectura y operaciones confirmadas."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app import fabrica_plan as fp
from app import fabrica_web as fw
from app import familias
from app import impulso_io as io
from app import retiro_anuncios as ra
from app.api import ConexionLectura
from app.api_common import mensaje_validacion
from app.api_write import exige_token
from app.optimizer.goals import MONTO_MAXIMO
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
                errores = exc.errors()
                for error in errores:
                    mensaje = str(error.get("msg", ""))
                    if "no es finito" in mensaje:
                        raise fw.error(422, mensaje) from None
                raise fw.error(422, mensaje_validacion(errores)) from None

        return sin_input_privado


router = APIRouter(prefix="/api/fabrica", tags=["fabrica"], route_class=_RutaFabrica)
Plataforma = Literal["amazon_mx", "amazon_us"]
Accion = Literal["pausar", "reconciliar", "registrar"]
Identificador = Annotated[int, Field(strict=True, ge=1, le=9223372036854775807)]
Lote = Annotated[str, Field(min_length=1, max_length=240, pattern=r"^[a-zA-Z0-9_-]+$")]


def _unicos_publicaciones(valor: list[int]) -> list[int]:
    if len(valor) != len(set(valor)):
        raise ValueError("publicaciones repetidas")
    return valor


def _unicos_retiros(valor: list[int]) -> list[int]:
    if len(valor) != len(set(valor)):
        raise ValueError("retiros repetidos")
    return valor


def _actor_limpio(valor: str) -> str:
    """Actor 1..100 sin NUL (Postgres lo rechaza en text): se valida en
    el borde, antes de mutar nada."""
    if "\x00" in valor:
        raise ValueError("actor con NUL")
    if not valor.strip():
        raise ValueError("actor vacio")
    return valor.strip()


ListingsUnicos = Annotated[
    list[Identificador], Field(min_length=1, max_length=100), AfterValidator(_unicos_publicaciones)
]
RetirosUnicos = Annotated[
    list[Identificador], Field(min_length=1, max_length=100), AfterValidator(_unicos_retiros)
]
ActorLimpio = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_actor_limpio)]


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
        if not monto.is_finite() or not Decimal("0") < monto <= MONTO_MAXIMO:
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
            # El API solo valida forma (decimal finito); el rango vive en
            # fabrica_plan.valida_banda_manual al construir el plan y nunca al
            # releer lotes historicos.
        return self


class SolicitudPlan(_Cuerpo):
    plataforma: Plataforma
    # A2: opcional (con familia unica sale del slug); presente se valida igual.
    tipo_producto: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$"
    )
    nombre_base: str = Field(min_length=1, max_length=100)
    listing_ids: ListingsUnicos
    objetivo: ObjetivoPlan
    modo: Literal["shadow", "live"]
    parametros: dict[str, ParametrosRol]

    @field_validator("nombre_base")
    @classmethod
    def nombre_valido(cls, valor):
        if not valor.strip() or any(ord(c) < 32 for c in valor):
            raise ValueError("nombre vacio o con caracteres de control")
        return valor.strip()

    @model_validator(mode="after")
    def parametros_validos(self):
        if set(self.parametros) != set(fp.ROLES_ORDEN_CREACION):
            raise ValueError("se requieren exactamente los cinco roles")
        moneda = fp.MONEDA_POR_PLATAFORMA[self.plataforma]
        fp.valida_parametros(
            {
                rol: fp.ParametrosRol(rol, Decimal(p.budget), Decimal(p.bid))
                for rol, p in self.parametros.items()
            },
            moneda,
        )
        # W-N: un presupuesto diario alto ya no se rechaza aqui; la previa
        # avisa contra el P90 y el crear exige acepto_presupuesto_alto.
        return self


class SolicitudCrear(_Cuerpo):
    solicitud: SolicitudPlan
    huella: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmacion: Literal["CREAR 5 CAMPAÑAS"]
    acepto_presupuesto_alto: bool = False


class SolicitudBids(_Cuerpo):
    plataforma: Plataforma
    # A2: opcional (con familia unica sale del slug); presente se valida igual.
    tipo_producto: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$"
    )
    listing_ids: ListingsUnicos
    objetivo: ObjetivoPlan


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
    fila = fw.listing_plataforma_externo(conn, listing_id)
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
    return fw.crear(
        cuerpo.solicitud.model_dump(),
        cuerpo.huella,
        cuerpo.confirmacion,
        acepto_presupuesto_alto=cuerpo.acepto_presupuesto_alto,
    )


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


MotivoRetiro = Literal["impulso", "sin_pedidos", "subfamilia"]
# La persona escribe la subfamilia como la nombra ("Arras para boda"); el
# tipo_producto que la fabrica guarda es su slug (arras_para_boda).
Subfamilia = Annotated[
    str,
    BeforeValidator(familias.slug),
    Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$"),
]
Actor = ActorLimpio
Huella = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Listings = ListingsUnicos


class SolicitudImpulsoPlan(_Cuerpo):
    plataforma: Plataforma
    listing_ids: ListingsUnicos
    subfamilia_por_listing: dict[str, Subfamilia]
    tope_monto: Annotated[str, Field(min_length=1, max_length=14)] | None = None

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
    actor: ActorLimpio
    acepto_presupuesto_alto: bool = False


class CuerpoPausarImpulso(_Cuerpo):
    confirmacion: Literal["PAUSAR IMPULSO"]


class CuerpoRetiroPlan(_Cuerpo):
    plataforma: Plataforma
    listing_ids: ListingsUnicos
    motivo: MotivoRetiro
    lote: Lote | None = None


class CuerpoRetiroEjecutar(_Cuerpo):
    plataforma: Plataforma
    listing_ids: ListingsUnicos
    motivo: MotivoRetiro
    lote: Lote | None = None
    huella: Huella
    confirmacion: str = Field(min_length=1, max_length=60)
    actor: ActorLimpio


class CuerpoRetiroReponer(_Cuerpo):
    retiro_ids: RetirosUnicos
    confirmacion: str = Field(min_length=1, max_length=60)
    actor: ActorLimpio
    incluye_adoptados: bool = False


class CuerpoRetiroRecrear(_Cuerpo):
    retiro_ids: RetirosUnicos
    confirmacion: str = Field(min_length=1, max_length=60)
    actor: ActorLimpio


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
    """EjecutorFabrica real: crea el lote con fm.mutar (shadow,
    esperado 2) y lee el grupo creado."""
    from app import fabrica_datos as fd
    from app import fabrica_motor as fm

    def ejecuta(plan_grupo, huella: str, lote: str) -> io.EjecucionFabrica:
        fm.mutar(
            plan_grupo,
            huella,
            esperado=2,
            go=confirmacion,
            lote=lote,
            revierte_al_fallar=True,
            crear_pausado=True,
        )
        grupo = ra.grupo_de_lote(conn, lote)
        estado = fw.estado_lote(conn, lote)
        if grupo is None or estado is None:
            raise fd.Abortar(f"lote {lote} sin grupo o estado tras mutar")
        return io.EjecucionFabrica(lote=lote, grupo_id=grupo, estado_lote=estado)

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
        imp_id = fw.impulso_id_de_grupo(conn, grupo)
        if imp_id is None:
            raise fw.error(404, "El lote no tiene impulso.", lote)
        if not ra.impulso_tuvo_impresiones(conn, imp_id):
            raise fw.error(422, "El impulso aun no tiene impresiones: nada que retirar.")
        return grupo, imp_id
    if not ra.grupo_alcanza_listings(conn, grupo, listing_ids):
        raise fw.error(422, "El lote no es de estos productos.")
    return grupo, None


def _vista_impulso(conn, vista: io.VistaPreviaImpulso, plataforma: str) -> dict:
    """La vista previa con dos marcas por plan. `subfamilia_nueva`: la
    subfamilia no tiene keywords, negativos ni grupo en la plataforma.
    `sin_negativos`: la campana auto arranca sin negativos, tambien con una
    subfamilia conocida que solo tiene keywords o grupo."""
    conocidas = set(fw.tipos_producto(conn, plataforma))
    datos = vista.como_dict()
    for plan, fuente in zip(datos["planes"], vista.planes, strict=True):
        plan["subfamilia_nueva"] = plan["subfamilia"] not in conocidas
        plan["sin_negativos"] = not plan["negatives"]
        # W-N: aviso (no rechazo) del diario que el impulso pone a cada
        # campana contra el P90 de las activas en la moneda.
        plan["presupuesto_alto"] = io.aviso_presupuesto_alto(fuente, vista.p90)
    return datos


@router.post("/impulso/plan")
def impulso_plan(cuerpo: SolicitudImpulsoPlan, conn: ConexionLectura):
    from app import fabrica_datos as fd

    moneda = fp.MONEDA_POR_PLATAFORMA[cuerpo.plataforma]
    try:
        vista = io.previsualiza(
            conn,
            cuerpo.como_solicitud(),
            sugeridor=_sugeridor_plataforma(cuerpo.plataforma, moneda),
        )
    except io.ErrorImpulso as exc:
        raise _error_impulso(exc) from None
    except fd.Abortar as exc:
        raise fw.error(422, str(exc)) from None
    return _vista_impulso(conn, vista, cuerpo.plataforma)


@router.post("/impulso/lanzar")
def impulso_lanzar(_token: Annotated[str, Depends(exige_token)], cuerpo: CuerpoLanzarImpulso):
    from app import fabrica_datos as fd

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
                acepto_presupuesto_alto=cuerpo.acepto_presupuesto_alto,
            )
        except io.ErrorImpulso as exc:
            vista = None
            if exc.status == 409:
                try:
                    vista = _vista_impulso(
                        conn,
                        io.previsualiza(
                            conn, cuerpo.solicitud.como_solicitud(), sugeridor=sugeridor
                        ),
                        cuerpo.solicitud.plataforma,
                    )
                except fd.Abortar:
                    vista = None
            raise _error_impulso(exc, vista=vista) from None
        except fd.Abortar as exc:
            raise fw.error(422, str(exc)) from None
        return {
            "lanzados": [
                {"impulso_id": x.impulso_id, "lote": x.lote, "estado_lote": x.estado_lote}
                for x in lanzados
            ]
        }


@router.post("/impulso/{impulso_id}/pausar")
def impulso_pausar(
    _token: Annotated[str, Depends(exige_token)],
    impulso_id: Annotated[int, Path(ge=1, le=9223372036854775807)],
    cuerpo: CuerpoPausarImpulso,
):
    from app import fabrica_datos as fd

    with fw._conexion("ORBIT_DSN_ADMIN", "impulso") as conn:
        lote = fw.lote_de_impulso(conn, impulso_id)
        if lote is None:
            raise fw.error(404, "El impulso no existe.")
        try:
            with io.PausadorFabrica(conn, actor="dueno") as pausador:
                pausador.pausa(lote)
        except io.ErrorImpulso as exc:
            raise _error_impulso(exc) from None
        except fd.Abortar as exc:
            # G9: Amazon rechazo a media pausa (el actor ya quedo
            # grabado antes del primer PUT): 502 con el detalle,
            # no 500 pelado.
            raise fw.error(502, str(exc)) from None
        conn.commit()
        return {"impulso_id": impulso_id, "lote": lote, "estado": "pausado"}


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
            resultados = ra.ejecuta_retiro(
                conn,
                plan,
                autorizacion=ra.AutorizacionHumana(cuerpo.confirmacion, cuerpo.actor),
                impulso_id=imp,
            )
        except ValueError as exc:
            raise fw.error(422, str(exc)) from None
        except RuntimeError as exc:
            raise fw.error(502, f"Retiro no ejecutado: {exc}") from None
        lote = ra.lote_retiro_de_plan(plan)
        ids = fw.retiros_abiertos_de_lote(conn, lote)
        return {
            "retirados": sum(1 for r in resultados if r.fuera),
            "total": len(resultados),
            "anuncios": [r.como_dict() for r in resultados],
            "lote_retiro": lote,
            "retiro_ids": ids,
        }


def _regresa_retiros(cuerpo, verbo: str, literal: str, regresar):
    """/retiro/reponer y /retiro/recrear: los retiros existen, el literal
    nombra cuantos y cada uno trae su resultado. Un parcial no es error
    del servidor: la respuesta dice cual regreso y cual no."""
    with fw._conexion("ORBIT_DSN_ADMIN", "retiro") as conn:
        hay = fw.retiros_existen(conn, list(cuerpo.retiro_ids))
        if hay != len(cuerpo.retiro_ids):
            raise fw.error(404, "Algun retiro pedido no existe.")
        if cuerpo.confirmacion != f"{literal} {len(cuerpo.retiro_ids)} ANUNCIOS":
            raise fw.error(422, "La confirmación no coincide con la operación.")
        autorizacion = ra.AutorizacionHumana(cuerpo.confirmacion, cuerpo.actor)
        try:
            resultados = regresar(conn, autorizacion)
        except ValueError as exc:
            raise fw.error(422, str(exc)) from None
        except RuntimeError as exc:
            raise fw.error(502, f"No se pudo {verbo}: {exc}") from None
        return resultados


def _ids_con(resultados, resultado: str) -> list[int]:
    return [r.retiro_id for r in resultados if r.resultado == resultado]


@router.post("/retiro/reponer")
def retiro_reponer(_token: Annotated[str, Depends(exige_token)], cuerpo: CuerpoRetiroReponer):
    """Regresa cada anuncio a como estaba (mismo adId). Nunca recrea: los
    ARCHIVED vienen en `requieren_recreacion` para /retiro/recrear. Los
    adoptados vienen aparte en `adoptados` y solo se reactivan con
    `incluye_adoptados`."""
    resultados = _regresa_retiros(
        cuerpo,
        "reponer",
        "REGRESAR",
        lambda conn, autorizacion: ra.repone(
            conn,
            retiro_ids=tuple(cuerpo.retiro_ids),
            autorizacion=autorizacion,
            incluye_adoptados=cuerpo.incluye_adoptados,
        ),
    )
    return {
        "repuestos": sum(1 for r in resultados if r.regresado),
        "total": len(resultados),
        "anuncios": [r.como_dict() for r in resultados],
        "adoptados": _ids_con(resultados, "adoptado_sin_reposicion"),
        "requieren_recreacion": _ids_con(resultados, "requiere_recreacion"),
    }


@router.post("/retiro/recrear")
def retiro_recrear(_token: Annotated[str, Depends(exige_token)], cuerpo: CuerpoRetiroRecrear):
    """Crea otro anuncio para cada retiro cuyo anuncio Amazon archivo,
    con la linea guardada. Exige `RECREAR {n} ANUNCIOS`."""
    resultados = _regresa_retiros(
        cuerpo,
        "recrear",
        "RECREAR",
        lambda conn, autorizacion: ra.recrea(
            conn, retiro_ids=tuple(cuerpo.retiro_ids), autorizacion=autorizacion
        ),
    )
    return {
        "recreados": sum(1 for r in resultados if r.regresado),
        "total": len(resultados),
        "anuncios": [r.como_dict() for r in resultados],
    }
