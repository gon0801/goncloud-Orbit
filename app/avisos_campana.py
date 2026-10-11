"""Avisos diarios de campana (BIDS 02, V.4). Puro: sin DB ni red
(candado en `tests/test_arq_bids_v.py`).

Tres clases, un mensaje por plataforma y clase, una linea por caso. Los
textos jamas dicen costo, margen ni target.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.pantalla_dinero import FilaCampana, FilaUbicacion, Plataforma

ClaseAviso = Literal[
    "ubicacion_gasta_sin_vender", "campana_sin_presupuesto", "presupuesto_expuesto"
]

CLASES: tuple[str, ...] = (
    "ubicacion_gasta_sin_vender",
    "campana_sin_presupuesto",
    "presupuesto_expuesto",
)

NOMBRES_UBICACION = {
    "arriba_de_busqueda": "Arriba de búsqueda",
    "resto_de_busqueda": "Resto de búsqueda",
    "paginas_de_producto": "Páginas de producto",
    "fuera_de_amazon": "Fuera de Amazon",
}


@dataclass(frozen=True)
class Aviso:
    plataforma: Plataforma
    clase: ClaseAviso
    campana_id: int | None
    frase: str

    def como_dict(self) -> dict:
        return {
            "plataforma": self.plataforma,
            "clase": self.clase,
            "campana_id": self.campana_id,
            "frase": self.frase,
        }


def _miles(valor: Decimal | int) -> str:
    """Entero con separador de miles, redondeo HALF_EVEN (espejo del
    filtro `miles_ui`, que vive en la capa web y aqui no se importa)."""
    return f"{round(valor):,}"


def avisos_del_dia(
    por_ubicacion: tuple[FilaUbicacion, ...],
    por_campana: tuple[FilaCampana, ...],
    *,
    plataforma: Plataforma,
    gasto_para_concluir: Decimal,
) -> tuple[Aviso, ...]:
    """Avisos del mercado de hoy. La marca de ubicacion recalcula con el
    umbral recibido (no confia en `gasta_sin_vender` de la fila)."""
    moneda = PLATAFORMAS_MONEDA[plataforma]
    avisos: list[Aviso] = []
    for fila in por_ubicacion:
        if fila.gasto is not None and fila.pedidos == 0 and fila.gasto >= gasto_para_concluir:
            clics = _miles(fila.clics) if fila.clics is not None else "—"
            avisos.append(
                Aviso(
                    plataforma=plataforma,
                    clase="ubicacion_gasta_sin_vender",
                    campana_id=None,
                    frase=(
                        f"{NOMBRES_UBICACION[fila.ubicacion]}: {_miles(fila.gasto)}"
                        f" {moneda} en 30 días, {clics} clics, ningún pedido."
                    ),
                )
            )
    for fila in por_campana:
        nombre = fila.nombre or f"Campaña {fila.campana_id}"
        if fila.dias_al_tope_7d is not None and fila.dias_al_tope_7d >= 5:
            avisos.append(
                Aviso(
                    plataforma=plataforma,
                    clase="campana_sin_presupuesto",
                    campana_id=fila.campana_id,
                    frase=(
                        f"{nombre}: se queda sin presupuesto. Usó 90 % o más en"
                        f" {fila.dias_al_tope_7d} de los últimos 7 días."
                    ),
                )
            )
        if (
            fila.presupuesto_diario is not None
            and fila.gasto_medio_diario
            and fila.presupuesto_diario > 10 * fila.gasto_medio_diario
        ):
            avisos.append(
                Aviso(
                    plataforma=plataforma,
                    clase="presupuesto_expuesto",
                    campana_id=fila.campana_id,
                    frase=(
                        f"{nombre}: este presupuesto no limita."
                        f" {_miles(fila.presupuesto_diario)} {moneda} al día y gasta"
                        f" {_miles(fila.gasto_medio_diario)}."
                    ),
                )
            )
    return tuple(avisos)


def mensaje_clase(plataforma: Plataforma, clase: ClaseAviso, avisos: tuple[Aviso, ...]) -> str:
    """Un mensaje por plataforma y clase, una linea por caso. Sin casos,
    cadena vacia (el comando no envia nada)."""
    return "\n".join(
        aviso.frase for aviso in avisos if aviso.plataforma == plataforma and aviso.clase == clase
    )
