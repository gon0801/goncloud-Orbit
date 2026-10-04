"""Tipos puros, composicion y asesor Jev para Ads (JEV ADS 01).

El diseno manda: docs/superpowers/specs/2026-10-03-jev-ads-design.md. El
NUCLEO (tipos + `componer`) es PURO; la IO vive SOLO en `AsesorAds` con
imports perezosos (candado AST en tests/test_jev_ads.py).

Unidad de juicio: TERMINO LITERAL + VERSION DE FICHA. Reglas de `componer`:
un compatible permite HayCompatible con cobertura visible; NingunoCompatible
exige universo no vacio, exhaustivo, fichas de todos y `no_satisface` en
cada par; el resto es Indeterminado con motivos (vacio no prueba exclusion,
un fallo jamas es incompatibilidad); no se multiplican probabilidades.
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
PlataformaAmazon = Literal["amazon_mx", "amazon_us"]
Finalidad = Literal["exclusion", "ruteo", "keyword"]

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
    """True si parece ASIN; ASIN-like queda FUERA del clasificador de texto."""
    return _ASIN_RE.fullmatch(termino.strip()) is not None


@dataclass(frozen=True)
class HechoConFuente:
    """Hecho de catalogo con su fuente."""

    texto: str
    fuente: str


@dataclass(frozen=True)
class FichaVersion:
    """Ficha aprobada INMUTABLE por version; cada correccion inserta una nueva."""

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
    """Clave de reutilizacion exacta: hash del termino literal + ficha + contrato."""

    termino_literal_sha256: str
    ficha_version_id: UUID
    contrato_sha256: str


DistribucionRelacion = Mapping[Relacion, Decimal]


@dataclass(frozen=True)
class Juicio:
    """Respuesta validada del proveedor para un par (apreciacion, no hecho)."""

    intento_id: UUID
    clave: ClavePar
    relacion: Relacion
    probabilidades: DistribucionRelacion
    confidence: Decimal
    observado_at: datetime  # UTC


@dataclass(frozen=True)
class FichaFaltante:
    """Producto sin ficha aplicable (o de otra variante que no lo acredita)."""

    producto_id: int | None


@dataclass(frozen=True)
class NoAplicaTexto:
    """Termino fuera del clasificador de texto (ASIN-like)."""

    motivo: MotivoNoAplica


@dataclass(frozen=True)
class FalloProveedor:
    """Fallo de transporte/validacion: JAMAS evidencia de incompatibilidad."""

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
    """Miembro del universo congelado (identidad de anuncios conservada).
    producto_id None = anuncio sin listing, jamas acredita ficha. `estados`
    es paralelo a `anuncio_ids` (vacio = sin dato de estado)."""

    anuncio_ids: tuple[int, ...]
    producto_id: int | None
    listing_ids: frozenset[int]
    estados: tuple[EstadoAnuncio, ...] = ()
    ficha_version_id: UUID | None = None


@dataclass(frozen=True)
class CensoCongelado:
    """Universo congelado; exhaustivo=True solo con enumeracion probada
    (grupos Amazon: False en V1; plan de fabrica: True)."""

    miembros: tuple[MiembroCenso, ...]
    exhaustivo: bool


@dataclass(frozen=True)
class HayCompatible:
    """Compatible observado; con_juicio/total expone la cobertura parcial."""

    producto_ids: tuple[int, ...]
    miembros_con_juicio: int
    miembros_totales: int


@dataclass(frozen=True)
class NingunoCompatible:
    """Negativo de exclusion: unico camino posible, con todo verificado."""

    miembros_totales: int


@dataclass(frozen=True)
class Indeterminado:
    """No se puede concluir; los motivos dicen por que."""

    motivos: frozenset[MotivoIndeterminado]


RelevanciaConjunto = HayCompatible | NingunoCompatible | Indeterminado


@dataclass(frozen=True)
class Vigente:
    """Ficha aprobada, cubre el listing, no vencida."""


@dataclass(frozen=True)
class Obsoleta:
    """Uso actual invalido (ficha revocada/vencida/sustituida): la historia queda."""


@dataclass(frozen=True)
class NoComprobable:
    """Sin fichas congeladas que comparar: vigencia desconocida."""


Vigencia = Vigente | Obsoleta | NoComprobable


def _solo_no_activo(estados: tuple[EstadoAnuncio, ...]) -> bool:
    """True si todos los estados son CONOCIDOS y ninguno activo (p. ej. un
    unico anuncio ARCHIVED). Estado ausente no excluye: es missing_state."""
    return bool(estados) and all(
        estado.status is not None and estado.status not in ESTADOS_ACTIVOS for estado in estados
    )


def _con_estado_ausente(miembro: MiembroCenso) -> bool:
    """missing_state solo con anuncios cuyo estado falta (sin anuncios, no)."""
    if not miembro.anuncio_ids:
        return False
    if not miembro.estados:
        return True
    return any(estado.status is None for estado in miembro.estados)


def _universo_anunciado(
    censo: CensoCongelado,
) -> tuple[tuple[MiembroCenso, ...], set[UUID]]:
    """Universo sin los solo-no-activos + fichas excluidas; valida paralelidad."""
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
    """Clasifica los pares: compatibles, juzgados y motivos (ValueError si la
    relacion esta fuera del contrato)."""
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
                motivos.add(
                    "no_anunciado"
                    if par.clave.ficha_version_id in excluidas_fichas
                    else "ficha_ausente"
                )
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
    """Composicion determinista y sin IO (reglas: docstring del modulo).

    Un Juicio acredita a un miembro SOLO si su clave coincide con la ficha
    de ese miembro en el censo; estados ARCHIVED salen del universo
    (`no_anunciado`) y el estado ausente se conserva (`missing_state`).
    Errores estructurales del llamador: ValueError.
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
    if censo.miembros and (total == 0 or total < len(censo.miembros)):
        motivos.add("no_anunciado")
    if censo.miembros and not censo.exhaustivo:
        motivos.add("universo_desconocido")
    if any(_con_estado_ausente(m) for m in universo):
        motivos.add("missing_state")
    if any(m.ficha_version_id is None for m in universo):
        motivos.add("ficha_ausente")

    claves = {
        (par.clave.termino_literal_sha256, par.clave.contrato_sha256)
        for par in pares
        if isinstance(par, Juicio)
    }
    if len(claves) > 1:
        raise ValueError("juicios de distinto termino y contrato en una composicion")
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
    """Decision guardada + termino + censo congelados (leerla es de 2.1).
    Para harvest, `destino_censo` congela el universo del DESTINO y se
    compone por separado del origen (`censo`): la UI muestra ambos ambitos
    sin mezclar universos ni heredar conclusiones entre ellos."""

    decision_id: int
    termino: str
    censo: CensoCongelado
    plataforma: PlataformaAmazon
    decided_at: datetime | None = None
    destino_censo: CensoCongelado | None = None


@dataclass(frozen=True)
class SemillasARevisar:
    """Plan de fabrica congelado: hash, canonico, fuentes y terminos a
    cotejar contra el censo del NUEVO plan (sin heredar conclusiones)."""

    plan_sha256: str
    plan_canonico: Mapping[str, object]
    fuentes_semillas: Mapping[str, object]
    terminos: tuple[str, ...]
    censo: CensoCongelado
    plataforma: PlataformaAmazon


Sujeto = DecisionARevisar | SemillasARevisar


@dataclass(frozen=True)
class PlanSeco:
    """Lo que `evaluar` haria (dry-run del CLI): pares por clave unica."""

    miembros: int
    fichas: int
    pares_nuevos: int
    pares_reutilizables: int
    pagaria: int
    agotaria: bool


@dataclass(frozen=True)
class Revision:
    """Salida de evaluar; presupuesto_agotado = lote retomable (misma
    solicitud). `destinos` trae la composicion del censo destino (harvest),
    un resultado por termino; vacia cuando el sujeto no lleva destino."""

    solicitud: UUID
    resultados: tuple[tuple[str, RelevanciaConjunto], ...]
    presupuesto_agotado: bool
    destinos: tuple[tuple[str, RelevanciaConjunto], ...] = ()


def _censo_a_json(censo: CensoCongelado) -> dict:
    """Congela el censo (con las fichas ya resueltas) para jev_revision."""

    def iso(valor):
        return valor.isoformat() if valor else None

    return {
        "exhaustivo": censo.exhaustivo,
        "miembros": [
            {
                "anuncio_ids": list(m.anuncio_ids),
                "estados": [{"status": e.status, "synced_at": iso(e.synced_at)} for e in m.estados],
                "ficha_version_id": str(m.ficha_version_id) if m.ficha_version_id else None,
                "listing_ids": sorted(m.listing_ids),
                "producto_id": m.producto_id,
            }
            for m in censo.miembros
        ],
    }


def _identidad_del_censo(censo: CensoCongelado) -> list:
    """Lo que hace distinto a un universo: anuncios, productos, listings y
    estados; no la hora de sincronizacion."""
    return [
        (m.anuncio_ids, m.producto_id, sorted(m.listing_ids), [e.status for e in m.estados])
        for m in censo.miembros
    ]


def _censo_crudo(censo: CensoCongelado) -> dict:
    """El censo sin las fichas resueltas: lo que el sujeto trae de origen."""
    return _censo_a_json(
        replace(censo, miembros=tuple(replace(m, ficha_version_id=None) for m in censo.miembros))
    )


def _mismo_origen(congelado: CensoCongelado, crudo: CensoCongelado) -> bool:
    """El sujeto que se retoma trae el MISMO censo: igual sin fichas, y cada
    ficha que el llamador ya traia resuelta es la que la revision congelo."""
    return _censo_crudo(congelado) == _censo_crudo(crudo) and all(
        traida.ficha_version_id in (None, guardado.ficha_version_id)
        for guardado, traida in zip(congelado.miembros, crudo.miembros, strict=True)
    )


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
    """Une catalogo, juicios y revision (1.4).

    Orden sellado: revision e intencion confirmadas ANTES del HTTP,
    resultado despues; retomar con la misma solicitud reutiliza exitos de
    ESTA revision (cero HTTP) y jamas fallos ni exitos ajenos; otro payload
    con la misma solicitud -> ValueError; sin clave, cero llamadas con
    fallos visibles; ASIN-like fuera del clasificador. `pedir` inyectable.
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

            def pedir(termino: str, ficha: FichaVersion):
                return pedir_real(
                    termino,
                    ficha,
                    self._contrato,
                    transporte=transporte or transporte_httpx,
                    api_key=self._api_key,
                )

        self._pedir = pedir

    def evaluar(self, sujeto: Sujeto, *, solicitud_id: UUID) -> Revision:
        """Evalua los terminos del sujeto contra el censo congelado y deja
        revision + eventos auditables (retomable por solicitud_id)."""
        from app.jev_juicios import clave_de, request_sha256

        destino_crudo = sujeto.destino_censo if isinstance(sujeto, DecisionARevisar) else None
        guardada = self._conn.execute(
            "SELECT censos, captured_at FROM jev_revision WHERE solicitud = %s", (solicitud_id,)
        ).fetchone()
        if guardada is None:
            ahora = self._ahora()
            censo, fichas = self._enriquecer(sujeto.censo, sujeto.plataforma, ahora)
            destino = None
            if destino_crudo is not None:
                destino, fichas_destino = self._enriquecer(destino_crudo, sujeto.plataforma, ahora)
                fichas = {**fichas, **fichas_destino}
        else:
            censo, destino, fichas, ahora = self._retomar(
                guardada, sujeto.censo, destino_crudo, sujeto.plataforma
            )
        terminos = (sujeto.termino,) if isinstance(sujeto, DecisionARevisar) else sujeto.terminos
        contexto = {
            "censo": _censo_a_json(censo),
            "plataforma": sujeto.plataforma,
            "terminos": list(terminos),
        }
        if destino is not None:
            contexto["destino"] = _censo_a_json(destino)
        self._abrir_revision(sujeto, solicitud_id, contexto, censo, destino, ahora)
        resultados = []
        destinos: list[tuple[str, RelevanciaConjunto]] = []
        http_hechos = 0
        agotado = False

        def pares_de(un_censo: CensoCongelado, termino: str) -> list[EstadoPar]:
            nonlocal http_hechos, agotado
            pares: list[EstadoPar] = []
            for miembro in un_censo.miembros:
                if _solo_no_activo(miembro.estados):
                    continue  # no anunciado (ARCHIVED): no paga intencion ni HTTP
                ficha = fichas.get(miembro.ficha_version_id) if miembro.ficha_version_id else None
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
                    solicitud_id, clave, request_sha256(termino, ficha, self._contrato)
                )
                self._conn.commit()
                http_hechos += 1
                devuelto = self._pedir(termino, ficha)
                pares.append(self._resultado(solicitud_id, clave, intencion_id, devuelto, miembro))
            return pares

        for termino in terminos:
            if es_asin_like(termino):
                sin_texto = (NoAplicaTexto(motivo="asin_like"),)
                resultados.append((termino, componer(censo, sin_texto)))
                if destino is not None:
                    destinos.append((termino, componer(destino, sin_texto)))
                continue
            resultados.append((termino, componer(censo, tuple(pares_de(censo, termino)))))
            if destino is not None:
                destinos.append((termino, componer(destino, tuple(pares_de(destino, termino)))))
        return Revision(solicitud_id, tuple(resultados), agotado, tuple(destinos))

    # internos (confirmaciones = los tres puntos del orden sellado)

    def _retomar(self, guardada, censo_crudo, destino_crudo, plataforma):
        """Reanudacion: el censo y el captured_at CONGELADOS por la revision
        (R9). Una ficha revocada, vencida o sustituida entre intentos no cambia
        el payload (la vigencia marcara Obsoleta), pero su par ya no se
        reutiliza ni se consulta: el spec reutiliza "solo si la ficha sigue
        aprobada, cubre ese listing y no vencio". Queda como ficha faltante.
        Un censo crudo distinto si es otro payload."""
        from app.jev_catalogo import ficha_vigente, fichas_por_id

        contexto, capturado = guardada
        censo = _censo_de_json(contexto["censo"])
        destino = _censo_de_json(contexto["destino"]) if "destino" in contexto else None
        mismo_destino = (destino is None and destino_crudo is None) or (
            destino is not None
            and destino_crudo is not None
            and _mismo_origen(destino, destino_crudo)
        )
        if not _mismo_origen(censo, censo_crudo) or not mismo_destino:
            raise ValueError("misma solicitud con otro payload")
        miembros = (*censo.miembros, *(destino.miembros if destino is not None else ()))
        fichas = fichas_por_id(
            self._conn, (m.ficha_version_id for m in miembros if m.ficha_version_id)
        )
        hoy = self._ahora()

        def sigue_vigente(miembro) -> bool:
            for listing in miembro.listing_ids:
                actual = ficha_vigente(
                    self._conn,
                    producto_id=miembro.producto_id,
                    plataforma=plataforma,
                    listing_id=listing,
                    ahora=hoy,
                )
                if actual is None or actual.id != miembro.ficha_version_id:
                    return False
            return True

        vencidas = {
            m.ficha_version_id for m in miembros if m.ficha_version_id and not sigue_vigente(m)
        }
        vigentes = {ficha_id: f for ficha_id, f in fichas.items() if ficha_id not in vencidas}
        return censo, destino, vigentes, capturado

    def plan_seco(self, sujeto: Sujeto, *, solicitud_id: UUID) -> PlanSeco:
        """Lo que `evaluar` haria con esta solicitud, SIN escribir ni llamar
        (R2, dry-run fiel): mismo censo y fichas (congelados si la revision ya
        existe; otro payload -> ValueError igual que al aplicar), mismos
        miembros fuera (ARCHIVED, sin ficha, ASIN-like) y exitos de la
        revision reutilizados; los pares nuevos se cuentan por clave unica y
        se cortan con el presupuesto."""
        from app.jev_juicios import clave_de

        destino_crudo = sujeto.destino_censo if isinstance(sujeto, DecisionARevisar) else None
        guardada = self._conn.execute(
            "SELECT censos, captured_at FROM jev_revision WHERE solicitud = %s", (solicitud_id,)
        ).fetchone()
        if guardada is None:
            ahora = self._ahora()
            censo, fichas = self._enriquecer(sujeto.censo, sujeto.plataforma, ahora)
            destino = None
            if destino_crudo is not None:
                destino, fichas_destino = self._enriquecer(destino_crudo, sujeto.plataforma, ahora)
                fichas = {**fichas, **fichas_destino}
        else:
            censo, destino, fichas, _ = self._retomar(
                guardada, sujeto.censo, destino_crudo, sujeto.plataforma
            )
        terminos = (sujeto.termino,) if isinstance(sujeto, DecisionARevisar) else sujeto.terminos
        nuevas: set = set()
        reutilizables: set = set()
        for termino in terminos:
            if es_asin_like(termino):
                continue
            for un_censo in (censo, *((destino,) if destino is not None else ())):
                for miembro in un_censo.miembros:
                    ficha = fichas.get(miembro.ficha_version_id)
                    if ficha is None or _solo_no_activo(miembro.estados):
                        continue
                    clave = clave_de(termino, ficha, self._contrato)
                    if clave in reutilizables or clave in nuevas:
                        continue
                    if guardada is not None and self._exito_previo(solicitud_id, clave):
                        reutilizables.add(clave)
                    else:
                        nuevas.add(clave)
        miembros = (*censo.miembros, *(destino.miembros if destino is not None else ()))
        return PlanSeco(
            miembros=len(miembros),
            fichas=len(fichas),
            pares_nuevos=len(nuevas),
            pares_reutilizables=len(reutilizables),
            pagaria=min(len(nuevas), self._presupuesto),
            agotaria=len(nuevas) > self._presupuesto,
        )

    def _enriquecer(self, censo: CensoCongelado, plataforma, ahora):
        """Ficha vigente por miembro, congelada en el censo. Un producto con
        varios listings se acredita solo si la MISMA ficha vigente cubre todos
        (R8); una ficha que cubre una parte deja al miembro sin ficha."""
        from app.jev_catalogo import ficha_vigente

        fichas: dict[UUID, FichaVersion] = {}
        miembros = []
        for miembro in censo.miembros:
            ficha = None
            if miembro.producto_id is not None and miembro.listing_ids:
                por_listing = [
                    ficha_vigente(
                        self._conn,
                        producto_id=miembro.producto_id,
                        plataforma=plataforma,
                        listing_id=listing_id,
                        ahora=ahora,
                    )
                    for listing_id in sorted(miembro.listing_ids)
                ]
                ids = {f.id if f is not None else None for f in por_listing}
                if len(ids) == 1 and None not in ids:
                    ficha = por_listing[0]
            if ficha is not None:
                fichas[ficha.id] = ficha
                miembro = replace(miembro, ficha_version_id=ficha.id)
            miembros.append(miembro)
        return CensoCongelado(miembros=tuple(miembros), exhaustivo=censo.exhaustivo), fichas

    def _abrir_revision(self, sujeto, solicitud_id, contexto, censo, destino, ahora):
        """Revision ANTES del primer HTTP; otro payload con la misma solicitud -> ValueError."""
        contrato_json = {
            "modelo": self._contrato.modelo,
            "opciones": list(self._contrato.opciones),
            "sha256": self._contrato.sha256(),
            "version": self._contrato.version,
        }
        miembros = (*censo.miembros, *(destino.miembros if destino is not None else ()))
        fichas_ids = sorted({str(m.ficha_version_id) for m in miembros if m.ficha_version_id})
        if isinstance(sujeto, DecisionARevisar):
            columnas = ("decision_id", "decided_at")
            valores: dict = {"decision_id": sujeto.decision_id, "decided_at": sujeto.decided_at}
            esperado = ("decision", sujeto.decision_id, None)  # plan_sha256
        else:
            columnas = ("plan_canonico", "plan_sha256", "fuentes_semillas", "decided_at")
            valores = {
                "plan_canonico": _canonico(sujeto.plan_canonico),
                "plan_sha256": sujeto.plan_sha256,
                "fuentes_semillas": _canonico(sujeto.fuentes_semillas),
                "decided_at": None,
            }
            esperado = ("semillas", None, sujeto.plan_sha256)
        nombres = ", ".join((*columnas, "censos", "ficha_version_ids", "contrato", "captured_at"))
        placeholders = ", ".join(
            ["%s::jsonb" if c in ("plan_canonico", "fuentes_semillas") else "%s" for c in columnas]
            + ["%s::jsonb", "%s", "%s::jsonb", "%s"]
        )
        self._conn.execute(
            f"INSERT INTO jev_revision (solicitud, sujeto_tipo, {nombres})"
            f" VALUES (%s, %s, {placeholders}) ON CONFLICT (solicitud) DO NOTHING",
            (
                solicitud_id,
                esperado[0],
                *valores.values(),
                _canonico(contexto),
                fichas_ids,
                _canonico(contrato_json),
                ahora,
            ),
        )
        guardado = self._conn.execute(
            "SELECT sujeto_tipo, decision_id, plan_sha256, censos, contrato"
            " FROM jev_revision WHERE solicitud = %s",
            (solicitud_id,),
        ).fetchone()
        coherente = (
            guardado[0] == esperado[0]
            and guardado[1] == esperado[1]
            and guardado[2] == esperado[2]
            and guardado[3] == contexto
            and guardado[4] == contrato_json
        )
        if not coherente:
            raise ValueError("misma solicitud con otro payload")

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
        """PRIMER exito validado de ESTA revision para la clave (spec; fallo
        jamas; otra revision jamas)."""
        return self._conn.execute(
            "SELECT id, respuesta FROM jev_par_evento"
            " WHERE revision_id = %s AND termino_sha256 = %s"
            " AND ficha_version_id = %s AND contrato_sha256 = %s"
            " AND tipo = 'resultado' AND respuesta IS NOT NULL"
            " ORDER BY ordinal LIMIT 1",
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
        """Resultado (exito o fallo) tras el HTTP, y su EstadoPar."""
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

    def _vigencia_de_miembros(
        self,
        censo: CensoCongelado,
        destino: CensoCongelado | None,
        contexto: dict,
        ahora: datetime,
    ) -> Vigencia:
        """Vigencia de la revision: para el listing de cada miembro, la ficha
        del miembro sigue siendo la seleccionada por el MISMO predicado que
        `ficha_vigente` (no revocada, no vencida, cubre el listing, ultima por
        observado_at/created_at). Sin comprobaciones posibles: desconocida."""
        from app.jev_catalogo import ficha_vigente

        plataforma = contexto["plataforma"]
        seleccionada: dict[int, UUID | None] = {}
        comprobadas = 0
        desplazada = False
        miembros = (*censo.miembros, *(destino.miembros if destino is not None else ()))
        for miembro in miembros:
            if miembro.ficha_version_id is None or miembro.producto_id is None:
                continue
            for listing in sorted(miembro.listing_ids):
                if listing not in seleccionada:
                    vigente = ficha_vigente(
                        self._posicional(),
                        producto_id=miembro.producto_id,
                        plataforma=plataforma,
                        listing_id=listing,
                        ahora=ahora,
                    )
                    seleccionada[listing] = vigente.id if vigente is not None else None
                comprobadas += 1
                if seleccionada[listing] != miembro.ficha_version_id:
                    desplazada = True
        if not comprobadas:
            return NoComprobable()
        return Obsoleta() if desplazada else Vigente()

    def _posicional(self):
        """La conexion con filas posicionales: el catalogo desempaca filas por
        posicion y la conexion del dashboard llega con dict_row."""
        from psycopg.rows import tuple_row

        conexion = self._conn

        class _Posicional:
            def execute(self, sentencia, parametros=None):
                return conexion.cursor(row_factory=tuple_row).execute(sentencia, parametros)

        return _Posicional()

    def _destino_cambio(self, decision_id: int, congelado: CensoCongelado) -> bool:
        """R3: el destino de hoy ya no es el que la revision congelo (otro
        anuncio, listing o estado). `synced_at` no cuenta: una resincronizacion
        no cambia el destino. Un destino que ya no se puede leer, tampoco es el
        mismo."""
        from app.jev_catalogo import decision_a_revisar

        try:
            actual = decision_a_revisar(self._posicional(), decision_id).destino_censo
        except ValueError:
            return True
        return actual is None or _identidad_del_censo(actual) != _identidad_del_censo(congelado)

    def _vista_de_revision(self, fila: dict, ahora: datetime) -> VistaAsesoria:
        """Vista historica de UNA revision (vigencia por fichas congeladas)."""
        fila = _fila_dict(fila, _COLUMNAS_REVISION)
        contexto = fila["censos"]
        censo = _censo_de_json(contexto["censo"])
        destino = _censo_de_json(contexto["destino"]) if "destino" in contexto else None
        terminos = list(contexto["terminos"])
        ids_fichas = [
            x if isinstance(x, UUID) else UUID(x) for x in fila["ficha_version_ids"] or []
        ]
        columnas_ficha = (
            "id",
            "producto_id",
            "plataforma",
            "aprobador",
            "sha256",
            "observado_at",
            "revisar_antes_de",
        )
        fichas: list[FichaVersion] = []
        vigencia: Vigencia = NoComprobable()
        if ids_fichas:
            filas = [
                _fila_dict(una, columnas_ficha)
                for una in self._conn.execute(
                    "SELECT f.id, f.producto_id, f.plataforma, f.aprobador, f.sha256,"
                    " f.observado_at, f.revisar_antes_de"
                    " FROM jev_ficha_version f WHERE f.id = ANY(%s)",
                    (ids_fichas,),
                ).fetchall()
            ]
            fichas = [
                FichaVersion(
                    id=d["id"],
                    producto_id=d["producto_id"],
                    plataforma=d["plataforma"],
                    listings=frozenset(),
                    hechos=(),
                    desconocidos=frozenset(),
                    aprobador=d["aprobador"],
                    observado_at=d["observado_at"],
                    revisar_antes_de=d["revisar_antes_de"],
                    sha256=d["sha256"],
                )
                for d in filas
            ]
            vigencia = self._vigencia_de_miembros(censo, destino, contexto, ahora)
        decision_id = fila["decision_id"]
        if (
            destino is not None
            and decision_id is not None
            and self._destino_cambio(decision_id, destino)
        ):
            vigencia = Obsoleta()

        columnas_evento = (
            "id",
            "termino_sha256",
            "ficha_version_id",
            "contrato_sha256",
            "respuesta",
            "error",
        )
        eventos = [
            _fila_dict(evento, columnas_evento)
            for evento in self._conn.execute(
                "SELECT id, termino_sha256, ficha_version_id, contrato_sha256,"
                " respuesta, error FROM jev_par_evento"
                " WHERE revision_id = %s AND tipo = 'resultado' ORDER BY ordinal",
                (fila["solicitud"],),
            ).fetchall()
        ]

        def composicion_de(un_censo: CensoCongelado, termino: str, hash_termino: str):
            """Reconstruccion HISTORICA del ambito: mismos eventos, un
            universo propio (origen o destino jamas mezclados)."""
            if es_asin_like(termino):
                return componer(un_censo, (NoAplicaTexto(motivo="asin_like"),))
            pares: list[EstadoPar] = []
            for miembro in un_censo.miembros:
                if _solo_no_activo(miembro.estados):
                    continue
                if miembro.ficha_version_id is None:
                    pares.append(FichaFaltante(producto_id=miembro.producto_id))
                    continue
                propio = _evento_del_par(eventos, hash_termino, miembro.ficha_version_id)
                if propio is None:
                    continue
                clave = ClavePar(
                    termino_literal_sha256=hash_termino,
                    ficha_version_id=miembro.ficha_version_id,
                    contrato_sha256=propio["contrato_sha256"],
                )
                if propio["respuesta"] is not None:
                    pares.append(_juicio_de_respuesta(propio["respuesta"], clave, propio["id"]))
                else:
                    pares.append(
                        FalloProveedor(
                            producto_id=miembro.producto_id,
                            motivo=str(propio["error"] or "fallo del proveedor"),
                        )
                    )
            return componer(un_censo, tuple(pares))

        resultados = []
        destinos = []
        for termino in terminos:
            hash_termino = hashlib.sha256(termino.encode("utf-8")).hexdigest()
            resultados.append((termino, composicion_de(censo, termino, hash_termino)))
            if destino is not None:
                destinos.append((termino, composicion_de(destino, termino, hash_termino)))

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
            destinos=tuple(destinos),
        )

    def leer(self, referencias, *, ahora: datetime) -> dict:
        """Vistas de revisiones guardadas para la UI (2.1/2.2): SOLO lectura,
        eventos de SU revision; None sin revision ligada."""
        salidas: dict = {}
        for ref in referencias:
            campo, valor = (
                ("plan_sha256", ref.plan_sha256)
                if isinstance(ref, ReferenciaPlan)
                else ("decision_id", ref)
            )
            fila = self._conn.execute(
                "SELECT solicitud, sujeto_tipo, decision_id, plan_sha256, censos,"
                " ficha_version_ids, captured_at, fuentes_semillas"
                " FROM jev_revision WHERE sujeto_tipo = %s"
                f" AND {campo} = %s ORDER BY created_at DESC, solicitud LIMIT 1",
                ("semillas" if campo == "plan_sha256" else "decision", valor),
            ).fetchone()
            salidas[ref] = self._vista_de_revision(fila, ahora) if fila is not None else None
        return salidas


# ===========================================================================
# 2.1: lectura de la asesoria guardada (GET; cero HTTP, cero escritura)
# ===========================================================================


@dataclass(frozen=True)
class ReferenciaPlan:
    """Lectura por huella de plan de fabrica (2.2)."""

    plan_sha256: str


@dataclass(frozen=True)
class VistaAsesoria:
    """Revision guardada para la UI: resultados HISTORICOS reconstruidos con
    los eventos de ESA revision y el censo congelado."""

    solicitud: UUID
    sujeto: str
    decision_id: int | None
    plan_sha256: str | None
    captured_at: datetime | None
    resultados: tuple[tuple[str, RelevanciaConjunto], ...]
    fichas: tuple[FichaVersion, ...]
    fuentes_semillas: Mapping[str, object] | None
    vigencia: Vigencia
    destinos: tuple[tuple[str, RelevanciaConjunto], ...] = ()

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
            "destinos": [
                {"termino": termino, "resultado": _relevancia_a_dict(resultado)}
                for termino, resultado in self.destinos
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
    """Normaliza fila psycopg (dict_row o tuple) a dict por columnas."""
    return fila if isinstance(fila, dict) else dict(zip(columnas, fila, strict=True))


def _evento_del_par(eventos: list[dict], hash_termino: str, ficha_id: UUID) -> dict | None:
    """El resultado del par que la vista debe mostrar: el PRIMER exito
    validado si existe; si no, el ULTIMO resultado. Es la misma regla con la
    que evaluar reutiliza y compone (`_exito_previo`): una reanudacion de la MISMA revision
    reintenta un par fallido con ordinal nuevo y la vista no puede quedarse
    con el fallo del primer intento. Los eventos de reutilizacion enlazan a
    un resultado de la misma revision (reutiliza_id), que es el que aqui
    se resuelve."""
    ultimo = None
    for evento in eventos:
        if evento["termino_sha256"] == hash_termino and evento["ficha_version_id"] == ficha_id:
            if evento["respuesta"] is not None:
                return evento
            ultimo = evento
    return ultimo


def _censo_de_json(datos: dict) -> CensoCongelado:
    miembros = [
        MiembroCenso(
            anuncio_ids=tuple(m["anuncio_ids"]),
            producto_id=m["producto_id"],
            listing_ids=frozenset(m["listing_ids"]),
            estados=tuple(
                EstadoAnuncio(
                    status=e["status"],
                    synced_at=datetime.fromisoformat(e["synced_at"]) if e["synced_at"] else None,
                )
                for e in m["estados"]
            ),
            ficha_version_id=UUID(m["ficha_version_id"]) if m["ficha_version_id"] else None,
        )
        for m in datos["miembros"]
    ]
    return CensoCongelado(miembros=tuple(miembros), exhaustivo=datos["exhaustivo"])
