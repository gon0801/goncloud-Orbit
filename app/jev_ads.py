"""Tipos puros, composicion de relevancia y el asesor Jev para Ads.

JEV ADS 01 (1.1 y 1.4). El diseno manda:
docs/superpowers/specs/2026-10-03-jev-ads-design.md, secciones "Tipos y
modulos" y "Catalogo y reglas de composicion".

El NUCLEO (tipos + `componer` y sus helpers) es PURO: sin red ni DB. La
IO vive SOLO en `AsesorAds` (catalogo via `app/jev_catalogo.py`,
transporte via `app/jev_juicios.py`, ambos importados de forma perezosa
dentro de sus metodos), y el candado AST de tests/test_jev_ads.py lo
exige por nodo top-level.

La unidad de juicio es TERMINO LITERAL + VERSION DE FICHA. `componer` aplica
las reglas del diseno sobre un censo congelado y los pares evaluados:

- Un producto compatible observado permite HayCompatible, incluso con
  cobertura parcial; la limitacion viaja en el resultado.
- NingunoCompatible exige universo no vacio, exhaustivo, fichas de todos los
  miembros y `no_satisface` en cada par. Es la unica via a un negativo de
  exclusion.
- En el resto de casos, Indeterminado con motivos explicitos: vacio no prueba
  exclusion, la ausencia de evidencia no es incompatibilidad y un fallo del
  proveedor jamas se presenta como incompatibilidad.
- No se multiplican probabilidades: la clasificacion es apreciacion del
  modelo, no un hecho validado por la base.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

Relacion = Literal["satisface", "no_satisface", "informacion_insuficiente"]
Finalidad = Literal["exclusion", "ruteo", "keyword"]
PlataformaAmazon = Literal["amazon_mx", "amazon_us"]

MotivoIndeterminado = Literal[
    "ficha_ausente",
    "juicio_ausente",
    "juicio_insuficiente",
    "universo_desconocido",
    "universo_vacio",
    "fallo_proveedor",
    "texto_no_aplica",
    "no_anunciado",
    "missing_state",
]
MotivoNoAplica = Literal["asin_like"]

# ASIN-like: misma regla que fabrica_plan.PATRON_ASIN y el sellado
# is_asin_like de search_term_observation (10 alfanumericos empezando en b0,
# sin distinguir mayusculas). La equivalencia queda fijada por test.
_ASIN_RE = re.compile(r"b0[a-z0-9]{8}", re.IGNORECASE)


def es_asin_like(termino: str) -> bool:
    """True si el termino parece un ASIN. ASIN-like queda FUERA del
    clasificador de texto (contrato del plan); el llamador construye
    NoAplicaTexto con ese par."""
    return _ASIN_RE.fullmatch(termino.strip()) is not None


@dataclass(frozen=True)
class HechoConFuente:
    """Un hecho de catalogo con su fuente (ficha manual aprobada)."""

    texto: str
    fuente: str


@dataclass(frozen=True)
class FichaVersion:
    """Version inmutable de ficha aprobada. Cada correccion inserta una
    version nueva; la vigencia la decide el catalogo (no revocada, cubre el
    listing y revisar_antes_de no vencio)."""

    id: UUID
    producto_id: int
    plataforma: PlataformaAmazon
    listings: frozenset[int]
    hechos: tuple[HechoConFuente, ...]
    desconocidos: frozenset[str]
    aprobador: str
    observado_at: datetime  # UTC
    revisar_antes_de: datetime  # UTC
    sha256: str


@dataclass(frozen=True)
class ClavePar:
    """Clave de reutilizacion: hash del texto literal UTF-8, ficha exacta y
    hash del contrato completo. No fusiona acentos, numeros ni terminos
    parecidos."""

    termino_literal_sha256: str
    ficha_version_id: UUID
    contrato_sha256: str


DistribucionRelacion = Mapping[Relacion, Decimal]


@dataclass(frozen=True)
class Juicio:
    """Respuesta validada del proveedor para un par. La clasificacion es
    apreciacion del modelo, no un hecho validado."""

    intento_id: UUID
    clave: ClavePar
    relacion: Relacion
    probabilidades: DistribucionRelacion
    confidence: Decimal
    observado_at: datetime  # UTC


@dataclass(frozen=True)
class FichaFaltante:
    """El producto observado no tiene ficha aplicable (no la tiene, o la que
    tiene es de otra variante y no lo acredita)."""

    producto_id: int | None


@dataclass(frozen=True)
class NoAplicaTexto:
    """El termino no entra al clasificador de texto (ASIN-like)."""

    motivo: MotivoNoAplica


@dataclass(frozen=True)
class FalloProveedor:
    """Fallo de transporte o de validacion para ese par. JAMAS es evidencia
    de incompatibilidad."""

    producto_id: int | None
    motivo: str


EstadoPar = Juicio | FichaFaltante | NoAplicaTexto | FalloProveedor

# Estado conocido activo: solo estos cuentan como anunciado (spec: el censo
# observa ENABLED/PAUSED; ARCHIVED conocido no se anuncia). Estado ausente no
# descarta (conserva como incidencia missing_state), no activa.
ESTADOS_ACTIVOS = frozenset({"ENABLED", "PAUSED"})


@dataclass(frozen=True)
class EstadoAnuncio:
    """Estado del anuncio observado (ad_entity_state). status None = estado
    ausente; se conserva y se reporta como incidencia, jamas se descarta."""

    status: str | None = None
    synced_at: datetime | None = None


@dataclass(frozen=True)
class MiembroCenso:
    """Miembro del universo congelado. La identidad de cada anuncio se
    conserva ANTES de deduplicar productos. `producto_id` None = anuncio sin
    listing resuelto; se conserva y no acredita ficha jamas. `estados` es
    paralelo a `anuncio_ids` (misma longitud, mismo orden; vacio = sin dato
    de estado)."""

    anuncio_ids: tuple[int, ...]
    producto_id: int | None
    listing_ids: frozenset[int]
    estados: tuple[EstadoAnuncio, ...] = ()
    ficha_version_id: UUID | None = None


@dataclass(frozen=True)
class CensoCongelado:
    """Universo congelado de la revision. `exhaustivo` solo puede ser True
    con prueba de enumeracion completa del universo: en V1 los grupos Amazon
    quedan False (universo 'desconocido'); el plan de fabrica si conoce su
    conjunto explicito."""

    miembros: tuple[MiembroCenso, ...]
    exhaustivo: bool


@dataclass(frozen=True)
class HayCompatible:
    """Al menos un producto compatible observado. `miembros_con_juicio` contra
    `miembros_totales` expone la cobertura parcial para mostrarla siempre."""

    producto_ids: tuple[int, ...]
    miembros_con_juicio: int
    miembros_totales: int


@dataclass(frozen=True)
class NingunoCompatible:
    """Universo no vacio, exhaustivo, con ficha y juicio no_satisface en cada
    miembro. Unico camino a un negativo de exclusion."""

    miembros_totales: int


@dataclass(frozen=True)
class Indeterminado:
    """No se puede concluir. Los motivos dicen por que: ficha ausente,
    juicio ausente o insuficiente, universo desconocido o vacio, fallo del
    proveedor, texto que no aplica."""

    motivos: frozenset[MotivoIndeterminado]


RelevanciaConjunto = HayCompatible | NingunoCompatible | Indeterminado


@dataclass(frozen=True)
class Vigente:
    """La ficha sigue aprobada, cubre el listing y no vencio su revision."""


@dataclass(frozen=True)
class Obsoleta:
    """La revision describe el uso actual: ficha revocada o destino cambiado
    la marca obsoleta sin reescribir su resultado historico."""


@dataclass(frozen=True)
class NoComprobable:
    """La vigencia no pudo comprobarse con los IDs y versiones disponibles."""


Vigencia = Vigente | Obsoleta | NoComprobable


def _solo_no_activo(estados: tuple[EstadoAnuncio, ...]) -> bool:
    """True si todos los estados son CONOCIDOS y ninguno activo (p. ej. un
    unico anuncio ARCHIVED). Estado ausente no excluye: es missing_state."""
    return bool(estados) and all(
        estado.status is not None and estado.status not in ESTADOS_ACTIVOS for estado in estados
    )


def _con_estado_ausente(miembro: MiembroCenso) -> bool:
    """missing_state SOLO para miembros CON anuncios cuyo estado falta: un
    miembro sin anuncios (plan de fabrica, anuncio_ids vacio) no tiene
    estado que falte y tampoco es no_anunciado."""
    if not miembro.anuncio_ids:
        return False
    if not miembro.estados:
        return True
    return any(estado.status is None for estado in miembro.estados)


def _universo_anunciado(
    censo: CensoCongelado,
) -> tuple[tuple[MiembroCenso, ...], set[UUID]]:
    """Universo anunciado (sin los solo-no-activos) y las fichas excluidas.

    Valida la paralelidad estados/anuncio_ids; los demas errores
    estructurales los valida componer al indexar fichas.
    """
    if any(len(miembro.estados) not in (0, len(miembro.anuncio_ids)) for miembro in censo.miembros):
        raise ValueError("censo con estados no paralelos a anuncio_ids")
    excluidas = {
        miembro.ficha_version_id
        for miembro in censo.miembros
        if miembro.ficha_version_id is not None and _solo_no_activo(miembro.estados)
    }
    universo = tuple(miembro for miembro in censo.miembros if not _solo_no_activo(miembro.estados))
    return universo, excluidas


def _absorber_pares(
    pares: tuple[EstadoPar, ...],
    por_ficha: dict[UUID, MiembroCenso],
    excluidas_fichas: set[UUID],
) -> tuple[list[int], set[UUID], set[MotivoIndeterminado]]:
    """Clasifica los pares: productos compatibles, fichas juzgadas y motivos.

    Un Juicio fuera del censo aporta `no_anunciado` (ficha excluida) o
    `ficha_ausente`; un juicio fuera del contrato (relacion desconocida)
    levanta ValueError: jamas pasa por juicio valido.
    """
    compatibles: list[int] = []
    juzgados: set[UUID] = set()
    motivos: set[MotivoIndeterminado] = set()
    for par in pares:
        if isinstance(par, NoAplicaTexto):
            motivos.add("texto_no_aplica")
        elif isinstance(par, FichaFaltante):
            motivos.add("ficha_ausente")
        elif isinstance(par, FalloProveedor):
            motivos.add("fallo_proveedor")
        else:
            miembro = por_ficha.get(par.clave.ficha_version_id)
            if miembro is None:
                if par.clave.ficha_version_id in excluidas_fichas:
                    motivos.add("no_anunciado")
                else:
                    motivos.add("ficha_ausente")
            elif par.clave.ficha_version_id in juzgados:
                raise ValueError("dos juicios para la misma ficha")
            else:
                juzgados.add(par.clave.ficha_version_id)
                if par.relacion == "satisface":
                    compatibles.append(miembro.producto_id)
                elif par.relacion == "informacion_insuficiente":
                    motivos.add("juicio_insuficiente")
                elif par.relacion != "no_satisface":
                    raise ValueError(f"relacion fuera del contrato: {par.relacion}")
    return compatibles, juzgados, motivos


def componer(censo: CensoCongelado, pares: tuple[EstadoPar, ...]) -> RelevanciaConjunto:
    """Aplica las reglas de composicion del diseno. Determinista y sin IO.

    Un par de Juicio acredita a un miembro SOLO si su clave coincide con la
    ficha de ese miembro en el censo: la ficha de otra variante no acredita.
    Un miembro con ficha pero sin par aporta motivo `juicio_ausente`; con
    par informacion_insuficiente aporta `juicio_insuficiente`.

    El universo se interpreta sobre el ESTADO observado: un miembro cuyos
    anuncios tienen todos estado conocido no activo (ARCHIVED) no cuenta
    como anunciado: se excluye del universo (motivo `no_anunciado`) y su
    juicio no acredita compatibilidad. El estado ausente NO excluye: se
    conserva como incidencia `missing_state` que impide el negativo
    universal; un miembro SIN anuncios (plan de fabrica con conjunto
    explicito) no tiene estado que falte y no aporta esa incidencia. La
    cobertura viaja sobre el universo anunciado.

    Un censo con la misma ficha en dos miembros, un miembro con ficha y sin
    producto, `estados` no paralelo a `anuncio_ids`, o un juicio con
    relacion fuera del contrato, es error estructural del llamador y
    levanta ValueError.
    """
    universo, excluidas_fichas = _universo_anunciado(censo)
    total = len(universo)
    por_ficha: dict[UUID, MiembroCenso] = {}
    for miembro in universo:
        if miembro.ficha_version_id is None:
            continue
        if miembro.ficha_version_id in por_ficha:
            raise ValueError("censo con la misma ficha en dos miembros")
        if miembro.producto_id is None:
            raise ValueError("miembro con ficha pero sin producto_id")
        por_ficha[miembro.ficha_version_id] = miembro

    motivos: set[MotivoIndeterminado] = set()
    if not censo.miembros:
        motivos.add("universo_vacio")
    else:
        if total == 0 or total < len(censo.miembros):
            motivos.add("no_anunciado")
        if not censo.exhaustivo:
            motivos.add("universo_desconocido")
    if any(_con_estado_ausente(miembro) for miembro in universo):
        motivos.add("missing_state")
    if any(miembro.ficha_version_id is None for miembro in universo):
        motivos.add("ficha_ausente")

    compatibles, juzgados, motivos_pares = _absorber_pares(pares, por_ficha, excluidas_fichas)
    motivos |= motivos_pares

    miembros_con_ficha = sum(1 for m in universo if m.ficha_version_id is not None)
    if len(juzgados) < miembros_con_ficha:
        motivos.add("juicio_ausente")

    if compatibles:
        return HayCompatible(
            producto_ids=tuple(compatibles),
            miembros_con_juicio=len(juzgados),
            miembros_totales=total,
        )
    if total > 0 and censo.exhaustivo and not motivos and len(juzgados) == total:
        return NingunoCompatible(miembros_totales=total)
    return Indeterminado(frozenset(motivos))


# ===========================================================================
# 1.4: sujetos de revision y el asesor (une catalogo, juicios y revision)
# ===========================================================================


@dataclass(frozen=True)
class DecisionARevisar:
    """Una decision guardada con su termino y su censo ya congelados. La
    lectura decision_id -> (termino, censo) pertenece al consumidor (2.1);
    aqui la decision solo alimenta la revision (FK de jev_revision)."""

    decision_id: int
    termino: str
    censo: CensoCongelado
    plataforma: PlataformaAmazon
    decided_at: datetime | None = None


@dataclass(frozen=True)
class SemillasARevisar:
    """Un plan de fabrica congelado: hash y contenido canonico, fuentes de
    semillas, terminos a cotejar contra el censo del NUEVO plan (sin heredar
    la conclusion del producto de origen)."""

    plan_sha256: str
    plan_canonico: Mapping[str, object]
    fuentes_semillas: Mapping[str, object]
    terminos: tuple[str, ...]
    censo: CensoCongelado
    plataforma: PlataformaAmazon


Sujeto = DecisionARevisar | SemillasARevisar


@dataclass(frozen=True)
class Revision:
    """Salida de evaluar: la solicitud y sus eventos quedaron en la base.
    `presupuesto_agotado` avisa que el lote quedo pendiente y se retoma con
    la MISMA solicitud."""

    solicitud: UUID
    resultados: tuple[tuple[str, RelevanciaConjunto], ...]
    presupuesto_agotado: bool


def _censo_a_json(censo: CensoCongelado) -> dict:
    """Congela el censo (con las fichas ya resueltas) para jev_revision."""

    def iso(valor):
        return valor.isoformat() if valor else None

    return {
        "exhaustivo": censo.exhaustivo,
        "miembros": [
            {
                "anuncio_ids": list(miembro.anuncio_ids),
                "estados": [
                    {"status": estado.status, "synced_at": iso(estado.synced_at)}
                    for estado in miembro.estados
                ],
                "ficha_version_id": (
                    str(miembro.ficha_version_id) if miembro.ficha_version_id else None
                ),
                "listing_ids": sorted(miembro.listing_ids),
                "producto_id": miembro.producto_id,
            }
            for miembro in censo.miembros
        ],
    }


def _canonico(objeto: object) -> str:
    return json.dumps(objeto, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _juicio_de_respuesta(respuesta: dict, clave: ClavePar, evento_id: UUID) -> Juicio:
    """Reconstruye el Juicio de un exito guardado (mismo intento: el evento)."""
    return Juicio(
        intento_id=evento_id,
        clave=clave,
        relacion=respuesta["relacion"],
        probabilidades={k: Decimal(v) for k, v in respuesta["probabilidades"].items()},
        confidence=Decimal(respuesta["confidence"]),
        observado_at=datetime.fromisoformat(respuesta["observado_at"]),
    )


class AsesorAds:
    """Une catalogo, juicios y revision (JEV ADS 01, 1.4).

    Orden sellado por par: la REVISION se inserta y confirma antes del
    primer HTTP; la INTENCION (request hash) se confirma antes del HTTP; el
    RESULTADO (respuesta validada o error) despues. Un crash deja la
    intencion confirmada sin resultado; re-llamar evaluar con la MISMA
    solicitud retoma: reutiliza exitos previos de ESTA revision (evento de
    reutilizacion, cero HTTP), registra intencion nueva con ordinal nuevo
    para los interrumpidos, y JAMAS reutiliza fallos ni exitos de otras
    revisiones. La misma solicitud con otro payload se rechaza comparando
    el contexto congelado (censo enriquecido + terminos). ASIN-like queda
    fuera del clasificador. Sin clave de TypeSafe no hay una sola llamada y
    cada par queda como fallo visible, jamas como incompatibilidad.

    `pedir` (inyectable; default el transporte real de app.jev_juicios)
    recibe (termino, ficha) y devuelve ResultadoPar | FalloPar. Las
    confirmaciones usan conn.commit(); con autocommit son inocuas.
    """

    def __init__(
        self,
        conn,
        *,
        pedir=None,
        transporte=None,
        api_key: str = "",
        presupuesto: int = 10,
        contrato=None,
        ahora=None,
    ):
        self._conn = conn
        self._presupuesto = presupuesto
        if contrato is None:
            from app.jev_juicios import contrato_por_defecto

            contrato = contrato_por_defecto()
        self._contrato = contrato
        self._api_key = api_key
        self._ahora = ahora or (lambda: datetime.now(UTC))
        if pedir is None:
            from app.jev_juicios import pedir_juicio as pedir_real
            from app.jev_juicios import transporte_httpx

            transporte_elegido = transporte or transporte_httpx

            def pedir(termino: str, ficha: FichaVersion):
                return pedir_real(
                    termino,
                    ficha,
                    self._contrato,
                    transporte=transporte_elegido,
                    api_key=self._api_key,
                )

        self._pedir = pedir

    def evaluar(self, sujeto: Sujeto, *, solicitud_id: UUID) -> Revision:
        """Evalua los terminos del sujeto contra el censo congelado y deja
        revision + eventos auditables. Retomable por solicitud_id."""
        from app.jev_juicios import clave_de, request_sha256

        ahora = self._ahora()
        censo, fichas = self._enriquecer(sujeto.censo, sujeto.plataforma, ahora)
        terminos = (sujeto.termino,) if isinstance(sujeto, DecisionARevisar) else sujeto.terminos
        contexto = {
            "censo": _censo_a_json(censo),
            "plataforma": sujeto.plataforma,
            "terminos": list(terminos),
        }
        self._abrir_revision(sujeto, solicitud_id, contexto, censo, ahora)
        resultados = []
        http_hechos = 0
        agotado = False
        for termino in terminos:
            pares: list[EstadoPar] = []
            if es_asin_like(termino):
                pares.append(NoAplicaTexto(motivo="asin_like"))
            else:
                for miembro in censo.miembros:
                    if _solo_no_activo(miembro.estados):
                        continue  # no anunciado (ARCHIVED): no paga intencion ni HTTP
                    ficha = (
                        fichas.get(miembro.ficha_version_id) if miembro.ficha_version_id else None
                    )
                    if ficha is None:
                        pares.append(FichaFaltante(producto_id=miembro.producto_id))
                        continue
                    if agotado:
                        continue
                    clave = clave_de(termino, ficha, self._contrato)
                    exito = self._exito_previo(solicitud_id, clave)
                    if exito is not None:
                        self._reutilizar(solicitud_id, clave, exito)
                        pares.append(_juicio_de_respuesta(exito[1], clave, exito[0]))
                        continue
                    if http_hechos >= self._presupuesto:
                        agotado = True
                        continue
                    intencion_id = self._intencion(
                        solicitud_id,
                        clave,
                        request_sha256(termino, ficha, self._contrato),
                    )
                    self._conn.commit()
                    http_hechos += 1
                    devuelto = self._pedir(termino, ficha)
                    pares.append(
                        self._resultado(solicitud_id, clave, intencion_id, devuelto, miembro)
                    )
            resultados.append((termino, componer(censo, tuple(pares))))
        return Revision(solicitud_id, tuple(resultados), agotado)

    # ------------------------------------------------------------------
    # Internos (cada uno confirma al salir; sin transaccion global: los
    # commits explicitos marcan los tres puntos del orden sellado)
    # ------------------------------------------------------------------

    def _enriquecer(self, censo: CensoCongelado, plataforma, ahora):
        """Resuelve la ficha vigente por miembro (un solo listing) y congela
        su version en el censo; devuelve tambien las fichas por id."""
        from app.jev_catalogo import ficha_vigente

        fichas: dict[UUID, FichaVersion] = {}
        miembros = []
        for miembro in censo.miembros:
            ficha = None
            if miembro.producto_id is not None and len(miembro.listing_ids) == 1:
                ficha = ficha_vigente(
                    self._conn,
                    producto_id=miembro.producto_id,
                    plataforma=plataforma,
                    listing_id=next(iter(miembro.listing_ids)),
                    ahora=ahora,
                )
            if ficha is not None:
                fichas[ficha.id] = ficha
                miembro = replace(miembro, ficha_version_id=ficha.id)
            miembros.append(miembro)
        return (
            CensoCongelado(miembros=tuple(miembros), exhaustivo=censo.exhaustivo),
            fichas,
        )

    def _abrir_revision(self, sujeto, solicitud_id, contexto, censo, ahora):
        """Inserta la revision ANTES del primer HTTP y confirma; si la
        solicitud ya existia con OTRO payload, rechaza."""
        contrato_json = {
            "modelo": self._contrato.modelo,
            "opciones": list(self._contrato.opciones),
            "sha256": self._contrato.sha256(),
            "version": self._contrato.version,
        }
        fichas_ids = sorted(str(m.ficha_version_id) for m in censo.miembros if m.ficha_version_id)
        if isinstance(sujeto, DecisionARevisar):
            self._conn.execute(
                "INSERT INTO jev_revision (solicitud, sujeto_tipo, decision_id,"
                " censos, ficha_version_ids, contrato, decided_at, captured_at)"
                " VALUES (%s, 'decision', %s, %s::jsonb, %s, %s::jsonb, %s, %s)"
                " ON CONFLICT (solicitud) DO NOTHING",
                (
                    solicitud_id,
                    sujeto.decision_id,
                    _canonico(contexto),
                    fichas_ids,
                    _canonico(contrato_json),
                    sujeto.decided_at,
                    ahora,
                ),
            )
            guardado = self._conn.execute(
                "SELECT sujeto_tipo, decision_id, plan_sha256, censos"
                " FROM jev_revision WHERE solicitud = %s",
                (solicitud_id,),
            ).fetchone()
            coherente = (
                guardado[0] == "decision"
                and guardado[1] == sujeto.decision_id
                and guardado[2] is None
                and guardado[3] == contexto
            )
        else:
            self._conn.execute(
                "INSERT INTO jev_revision (solicitud, sujeto_tipo, plan_canonico,"
                " plan_sha256, fuentes_semillas, censos, ficha_version_ids, contrato,"
                " captured_at)"
                " VALUES (%s, 'semillas', %s::jsonb, %s, %s::jsonb, %s::jsonb, %s,"
                " %s::jsonb, %s) ON CONFLICT (solicitud) DO NOTHING",
                (
                    solicitud_id,
                    _canonico(sujeto.plan_canonico),
                    sujeto.plan_sha256,
                    _canonico(sujeto.fuentes_semillas),
                    _canonico(contexto),
                    fichas_ids,
                    _canonico(contrato_json),
                    ahora,
                ),
            )
            guardado = self._conn.execute(
                "SELECT sujeto_tipo, plan_sha256, censos FROM jev_revision WHERE solicitud = %s",
                (solicitud_id,),
            ).fetchone()
            coherente = (
                guardado[0] == "semillas"
                and guardado[1] == sujeto.plan_sha256
                and guardado[2] == contexto
            )
        if not coherente:
            raise ValueError("misma solicitud con otro payload")
        self._conn.commit()

    def _siguiente_ordinal(self, solicitud_id, clave, tipo) -> int:
        return self._conn.execute(
            "SELECT COALESCE(max(ordinal), 0) + 1 FROM jev_par_evento"
            " WHERE revision_id = %s AND termino_sha256 = %s"
            " AND ficha_version_id = %s AND contrato_sha256 = %s AND tipo = %s",
            (
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                tipo,
            ),
        ).fetchone()[0]

    def _exito_previo(self, solicitud_id, clave):
        """Primer exito de ESTA revision para la clave (fallo jamas; exito de
        otra revision jamas)."""
        return self._conn.execute(
            "SELECT id, respuesta FROM jev_par_evento"
            " WHERE revision_id = %s AND termino_sha256 = %s"
            " AND ficha_version_id = %s AND contrato_sha256 = %s"
            " AND tipo = 'resultado' AND respuesta IS NOT NULL"
            " ORDER BY ordinal DESC LIMIT 1",
            (
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
            ),
        ).fetchone()

    def _reutilizar(self, solicitud_id, clave, exito) -> None:
        self._conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
            " ficha_version_id, contrato_sha256, ordinal, tipo, reutiliza_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'reutilizacion', %s)",
            (
                uuid.uuid4(),
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                self._siguiente_ordinal(solicitud_id, clave, "reutilizacion"),
                exito[0],
            ),
        )
        self._conn.commit()

    def _intencion(self, solicitud_id, clave, request_hash) -> UUID:
        intencion_id = uuid.uuid4()
        self._conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
            " ficha_version_id, contrato_sha256, ordinal, tipo, request_sha256)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'intencion', %s)",
            (
                intencion_id,
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                self._siguiente_ordinal(solicitud_id, clave, "intencion"),
                request_hash,
            ),
        )
        return intencion_id

    def _resultado(self, solicitud_id, clave, intencion_id, devuelto, miembro) -> EstadoPar:
        """Inserta el resultado (exito o fallo) y devuelve el EstadoPar."""
        ordinal = self._siguiente_ordinal(solicitud_id, clave, "resultado")
        if hasattr(devuelto, "juicio"):
            juicio_proveedor = devuelto.juicio
            evento_id = uuid.uuid4()
            respuesta = {
                "confidence": str(juicio_proveedor.confidence),
                "observado_at": juicio_proveedor.observado_at.isoformat(),
                "probabilidades": {k: str(v) for k, v in juicio_proveedor.probabilidades.items()},
                "relacion": juicio_proveedor.relacion,
            }
            self._conn.execute(
                "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
                " ficha_version_id, contrato_sha256, ordinal, tipo, respuesta,"
                " intencion_id, duracion_ms, usage)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'resultado', %s::jsonb, %s, %s,"
                " %s::jsonb)",
                (
                    evento_id,
                    solicitud_id,
                    clave.termino_literal_sha256,
                    clave.ficha_version_id,
                    clave.contrato_sha256,
                    ordinal,
                    _canonico(respuesta),
                    intencion_id,
                    devuelto.duracion_ms,
                    _canonico(devuelto.usage) if devuelto.usage else None,
                ),
            )
            self._conn.commit()
            return Juicio(
                intento_id=evento_id,
                clave=clave,
                relacion=juicio_proveedor.relacion,
                probabilidades=juicio_proveedor.probabilidades,
                confidence=juicio_proveedor.confidence,
                observado_at=juicio_proveedor.observado_at,
            )
        self._conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
            " ficha_version_id, contrato_sha256, ordinal, tipo, error,"
            " intencion_id, duracion_ms, usage)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'resultado', %s, %s, %s, %s::jsonb)",
            (
                uuid.uuid4(),
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                ordinal,
                f"{devuelto.codigo}: {devuelto.detalle}",
                intencion_id,
                devuelto.duracion_ms,
                _canonico(devuelto.usage) if devuelto.usage else None,
            ),
        )
        self._conn.commit()
        return FalloProveedor(
            producto_id=miembro.producto_id,
            motivo=f"{devuelto.codigo}: {devuelto.detalle}",
        )

    def _vista_de_revision(self, fila: dict, ahora: datetime) -> VistaAsesoria:
        fila = _fila_dict(fila, _COLUMNAS_REVISION)
        contexto = fila["censos"]
        censo = _censo_de_json(contexto["censo"])
        terminos = list(contexto["terminos"])
        ids_fichas = [
            x if isinstance(x, UUID) else UUID(x) for x in fila["ficha_version_ids"] or []
        ]

        fichas: list[FichaVersion] = []
        vigencia: Vigencia = NoComprobable()
        if ids_fichas:
            filas = self._conn.execute(
                "SELECT f.id, f.producto_id, f.plataforma, f.aprobador, f.sha256,"
                " f.observado_at, f.revisar_antes_de, f.created_at,"
                " EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)"
                " AS revocada,"
                " EXISTS (SELECT 1 FROM jev_ficha_version n WHERE n.producto_id = f.producto_id"
                "   AND n.plataforma = f.plataforma AND n.id <> f.id"
                "   AND n.created_at > f.created_at"
                "   AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r2"
                "       WHERE r2.ficha_version_id = n.id)) AS hay_mas_nueva"
                " FROM jev_ficha_version f WHERE f.id = ANY(%s)",
                (ids_fichas,),
            ).fetchall()
            for una in filas:
                datos = _fila_dict(
                    una,
                    (
                        "id",
                        "producto_id",
                        "plataforma",
                        "aprobador",
                        "sha256",
                        "observado_at",
                        "revisar_antes_de",
                        "created_at",
                        "revocada",
                        "hay_mas_nueva",
                    ),
                )
                fichas.append(
                    FichaVersion(
                        id=datos["id"],
                        producto_id=datos["producto_id"],
                        plataforma=datos["plataforma"],  # type: ignore[arg-type]
                        listings=frozenset(),
                        hechos=(),
                        desconocidos=frozenset(),
                        aprobador=datos["aprobador"],
                        observado_at=datos["observado_at"],
                        revisar_antes_de=datos["revisar_antes_de"],
                        sha256=datos["sha256"],
                    )
                )
            vigencia = Vigente()
            for una in filas:
                datos = _fila_dict(
                    una,
                    (
                        "id",
                        "producto_id",
                        "plataforma",
                        "aprobador",
                        "sha256",
                        "observado_at",
                        "revisar_antes_de",
                        "created_at",
                        "revocada",
                        "hay_mas_nueva",
                    ),
                )
                if (
                    datos["revocada"]
                    or datos["hay_mas_nueva"]
                    or datos["revisar_antes_de"] <= ahora
                ):
                    vigencia = Obsoleta()

        eventos = self._conn.execute(
            "SELECT id, termino_sha256, ficha_version_id, contrato_sha256, respuesta, error"
            " FROM jev_par_evento WHERE revision_id = %s AND tipo = 'resultado'"
            " ORDER BY ordinal",
            (fila["solicitud"],),
        ).fetchall()

        resultados = []
        for termino in terminos:
            hash_termino = hashlib.sha256(termino.encode("utf-8")).hexdigest()
            pares: list[EstadoPar] = []
            if es_asin_like(termino):
                pares.append(NoAplicaTexto(motivo="asin_like"))
            else:
                for miembro in censo.miembros:
                    if miembro.ficha_version_id is None:
                        pares.append(FichaFaltante(producto_id=miembro.producto_id))
                        continue
                    propio = None
                    evento_id = None
                    for evento in eventos:
                        datos_evento = _fila_dict(
                            evento,
                            (
                                "id",
                                "termino_sha256",
                                "ficha_version_id",
                                "contrato_sha256",
                                "respuesta",
                                "error",
                            ),
                        )
                        if (
                            datos_evento["termino_sha256"] == hash_termino
                            and datos_evento["ficha_version_id"] == miembro.ficha_version_id
                        ):
                            propio = datos_evento
                            evento_id = datos_evento["id"]
                            break
                    if propio is None:
                        continue
                    clave = ClavePar(
                        termino_literal_sha256=hash_termino,
                        ficha_version_id=miembro.ficha_version_id,
                        contrato_sha256=propio["contrato_sha256"],
                    )
                    if propio["respuesta"] is not None:
                        pares.append(
                            _juicio_de_respuesta_guardada(propio["respuesta"], clave, evento_id)
                        )
                    else:
                        pares.append(
                            FalloProveedor(
                                producto_id=miembro.producto_id,
                                motivo=str(propio["error"] or "fallo del proveedor"),
                            )
                        )
            resultados.append((termino, componer(censo, tuple(pares))))

        return VistaAsesoria(
            solicitud=fila["solicitud"],
            sujeto=fila["sujeto_tipo"],
            decision_id=fila["decision_id"],
            plan_sha256=fila["plan_sha256"],
            captured_at=fila["captured_at"],
            resultados=tuple(resultados),
            fichas=tuple(fichas),
            fuentes_semillas=fila["fuentes_semillas"],
            vigencia=vigencia,
        )

    def leer(self, referencias, *, ahora: datetime) -> dict:
        """Vistas de las revisiones guardadas para la UI (2.1/2.2).

        `referencias` mezcla decision_id (int) y ReferenciaPlan (huella); la
        respuesta usa la MISMA referencia como clave y None cuando no hay
        revision ligada. SOLO lectura: sin HTTP y sin escrituras; cada vista
        se reconstruye con los eventos de SU revision y el censo congelado
        (nunca con exitos posteriores de otras revisiones).
        """
        salidas: dict = {}
        for ref in referencias:
            if isinstance(ref, ReferenciaPlan):
                fila = self._conn.execute(
                    "SELECT solicitud, sujeto_tipo, decision_id, plan_sha256, censos,"
                    " ficha_version_ids, captured_at, fuentes_semillas"
                    " FROM jev_revision WHERE sujeto_tipo = 'semillas'"
                    " AND plan_sha256 = %s ORDER BY created_at DESC, solicitud LIMIT 1",
                    (ref.plan_sha256,),
                ).fetchone()
            else:
                fila = self._conn.execute(
                    "SELECT solicitud, sujeto_tipo, decision_id, plan_sha256, censos,"
                    " ficha_version_ids, captured_at, fuentes_semillas"
                    " FROM jev_revision WHERE sujeto_tipo = 'decision'"
                    " AND decision_id = %s ORDER BY created_at DESC, solicitud LIMIT 1",
                    (ref,),
                ).fetchone()
            salidas[ref] = self._vista_de_revision(fila, ahora) if fila is not None else None
        return salidas


# ===========================================================================
# 2.1: lectura de la asesoria guardada (GET; cero HTTP, cero escritura)
# ===========================================================================


@dataclass(frozen=True)
class ReferenciaPlan:
    """Referencia de lectura por huella de plan de fabrica (2.2)."""

    plan_sha256: str


@dataclass(frozen=True)
class VistaAsesoria:
    """Lo que la UI muestra de una revision guardada. Los resultados son
    HISTORICOS: se reconstruyen SOLO con los eventos de ESA revision y el
    censo congelado; un exito posterior de otra revision jamas los tine."""

    solicitud: UUID
    sujeto: str
    decision_id: int | None
    plan_sha256: str | None
    captured_at: datetime | None
    resultados: tuple[tuple[str, RelevanciaConjunto], ...]
    fichas: tuple[FichaVersion, ...]
    fuentes_semillas: Mapping[str, object] | None
    vigencia: Vigencia

    def como_dict(self) -> dict:
        nivel = {Vigente: "vigente", Obsoleta: "obsoleta", NoComprobable: "no_comprobable"}
        return {
            "solicitud": str(self.solicitud),
            "sujeto": self.sujeto,
            "decision_id": self.decision_id,
            "plan_sha256": self.plan_sha256,
            "captured_at": (
                self.captured_at.astimezone(UTC).isoformat()
                if self.captured_at is not None
                else None
            ),
            "vigencia": nivel[type(self.vigencia)],
            "resultados": [
                {"termino": termino, "resultado": _relevancia_a_dict(resultado)}
                for termino, resultado in self.resultados
            ],
            "fichas": [
                {
                    "id": str(ficha.id),
                    "aprobador": ficha.aprobador,
                    "sha256": ficha.sha256[:12],
                    "observado_at": ficha.observado_at.isoformat(),
                }
                for ficha in self.fichas
            ],
            "fuentes_semillas": (dict(self.fuentes_semillas) if self.fuentes_semillas else None),
        }


def _relevancia_a_dict(resultado: RelevanciaConjunto) -> dict:
    if isinstance(resultado, HayCompatible):
        return {
            "tipo": "hay_compatible",
            "producto_ids": list(resultado.producto_ids),
            "miembros_con_juicio": resultado.miembros_con_juicio,
            "miembros_totales": resultado.miembros_totales,
        }
    if isinstance(resultado, NingunoCompatible):
        return {"tipo": "ninguno_compatible", "miembros_totales": resultado.miembros_totales}
    return {"tipo": "indeterminado", "motivos": sorted(resultado.motivos)}


_COLUMNAS_REVISION = (
    "solicitud",
    "sujeto_tipo",
    "decision_id",
    "plan_sha256",
    "censos",
    "ficha_version_ids",
    "captured_at",
    "fuentes_semillas",
)


def _fila_dict(fila, columnas) -> dict:
    if isinstance(fila, dict):
        return fila
    return dict(zip(columnas, fila, strict=True))


def _censo_de_json(datos: dict) -> CensoCongelado:
    miembros = []
    for miembro in datos["miembros"]:
        miembros.append(
            MiembroCenso(
                anuncio_ids=tuple(miembro["anuncio_ids"]),
                producto_id=miembro["producto_id"],
                listing_ids=frozenset(miembro["listing_ids"]),
                estados=tuple(
                    EstadoAnuncio(
                        status=estado["status"],
                        synced_at=(
                            datetime.fromisoformat(estado["synced_at"])
                            if estado["synced_at"]
                            else None
                        ),
                    )
                    for estado in miembro["estados"]
                ),
                ficha_version_id=(
                    UUID(miembro["ficha_version_id"]) if miembro["ficha_version_id"] else None
                ),
            )
        )
    return CensoCongelado(miembros=tuple(miembros), exhaustivo=datos["exhaustivo"])


def _leer_asesor():
    return AsesorAds


def _juicio_de_respuesta_guardada(respuesta: dict, clave: ClavePar, evento_id: UUID) -> Juicio:
    return Juicio(
        intento_id=evento_id,
        clave=clave,
        relacion=respuesta["relacion"],
        probabilidades={k: Decimal(v) for k, v in respuesta["probabilidades"].items()},
        confidence=Decimal(respuesta["confidence"]),
        observado_at=datetime.fromisoformat(respuesta["observado_at"]),
    )


def _juicio_de_respuesta_guardada(respuesta: dict, clave: ClavePar, evento_id: UUID) -> Juicio:
    return Juicio(
        intento_id=evento_id,
        clave=clave,
        relacion=respuesta["relacion"],
        probabilidades={k: Decimal(v) for k, v in respuesta["probabilidades"].items()},
        confidence=Decimal(respuesta["confidence"]),
        observado_at=datetime.fromisoformat(respuesta["observado_at"]),
    )
