"""Vista PURA de la asesoria guardada (JEV ADS 01, 2.1 y 2.2): lo que leen
/cortes y la fabrica, sin IO (la lectura de la base es de
`AsesorAds.leer` en `app/jev_asesor.py`, R14)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.jev_ads import (
    FichaVersion,
    HayCompatible,
    NingunoCompatible,
    NoComprobable,
    Obsoleta,
    RelevanciaConjunto,
    Vigencia,
    Vigente,
)


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


Banda = Literal["ninguno", "pocos", "una_parte", "todos", "sin_dato"]


def banda_de_proporcion(satisfacen: int, evaluados: int) -> Banda:
    """SOLO PRESENTACION (S.5): agrupa y ordena la pantalla de gasto sin
    venta por cuantos productos dijeron "si". Cortes de pantalla, no
    umbrales de aceptacion: no se guarda y ningun efecto la lee (la unica
    clase que un efecto puede leer es `lectura`).

    ninguno (0 de N), pocos (hasta 15%), todos (95% o mas), una_parte (el
    resto), sin_dato (evaluados == 0)."""
    if evaluados == 0:
        return "sin_dato"
    if satisfacen == 0:
        return "ninguno"
    proporcion = satisfacen / evaluados
    if proporcion >= 0.95:
        return "todos"
    if proporcion <= 0.15:
        return "pocos"
    return "una_parte"


# --- Presentacion de la senal en pantallas (S.5 J5b) -----------------------------

TITULO_POR_LECTURA = {
    "vendio_aqui": "Vendió aquí",
    "vende_en_otro": "Vende en otro ad group",
    "relevante_sin_venta": "Corresponde y no vende en ninguno",
    "ajena": "Ajena",
    "sin_lectura": "Sin lectura",
}

TITULO_POR_BANDA: dict[str, str] = {
    "ninguno": "Ninguno",
    "pocos": "Pocos (hasta 15%)",
    "una_parte": "Una parte",
    "todos": "Todos (95% o más)",
    "sin_dato": "Sin dato",
}

FRASE_POR_LECTURA = {
    "vendio_aqui": "Aquí ya convirtió. Bloquear es el riesgo.",
    "vende_en_otro": "Bloquear aquí junta el tráfico donde ya vende.",
    "relevante_sin_venta": "Es del catálogo y no convierte.",
}

MOTIVO_SIN_LECTURA_ES = {
    "dato_de_venta_faltante": "falta un dato de venta",
    "sin_observaciones": "sin observaciones",
    "jev_sin_veredicto": "Jev no concluyó",
    "jev_no_evaluada": "Jev no la evaluó",
}


def titulo_de_lectura(lectura: str) -> str:
    """Cabecera de seccion por lectura (diseno S.5). Lectura ajena al
    vocabulario: KeyError ruidoso, no un titulo inventado."""
    return TITULO_POR_LECTURA[lectura]


def titulo_de_banda(banda: str) -> str:
    """Cabecera de banda de proporcion (diseno S.5). Igual de estricta."""
    return TITULO_POR_BANDA[banda]


def frase_de_lectura(
    lectura: str,
    motivos: tuple[str, ...] | list[str] = (),
    miembros: int = 0,
    datos_hasta: str | None = None,
) -> str:
    """La frase que la pantalla pinta junto a la lectura (diseno S.5,
    "Que le dice al dueno"): SOLO PRESENTACION, pura, sin IO. `motivos`
    son los `motivos_lectura` guardados; `miembros` y `datos_hasta` (ISO)
    solo se usan para la frase de `ajena`."""
    if lectura == "ajena":
        base = f"No corresponde a ninguno de los {miembros} productos"
        if datos_hasta:
            base += f"; lista comprobada con el listado de Amazon del {datos_hasta[:10]}"
        return base + ". Bloquear, y pronto."
    if lectura == "sin_lectura":
        detalle = ", ".join(MOTIVO_SIN_LECTURA_ES.get(motivo, motivo) for motivo in motivos)
        return f"Sin lectura: {detalle or 'sin motivo'}. Un dato faltante no es cero."
    return FRASE_POR_LECTURA[lectura]
