"""Embudo de E.4 por mercado a partir de la salida de `auto03_verifica evalua --json`.

Uso, desde la raiz del repo (E=docs/evidencia/auto-03/ejecucion/E):
    PYTHONPATH=. uv run --frozen --python 3.12 python $E/mide.py $E/elegibilidad-mx.json

Lee las evaluaciones congeladas tal cual (no recalcula el resolutor) y cuenta cada etapa.
"""

import json
import sys
from collections import Counter

from app.optimizer import goals as g
from app.optimizer import productos as p
from app.productos_io import evaluacion_de_fila

MOTIVOS_DE_MARGEN = {
    "sin_margen",
    "cobertura_baja",
    "ventana_corta",
    "ledger_rancio",
    "margen_no_positivo",
}


def mide(ruta: str) -> dict:
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    evaluaciones = [
        evaluacion_de_fila(e, r)
        for e, r in zip(datos["entradas"], datos["evaluaciones"], strict=True)
    ]

    def con_margen(ev: p.EvaluacionProducto) -> bool:
        return isinstance(ev.entrada.margen, g.MedicionMargen) and not (
            MOTIVOS_DE_MARGEN & set(ev.motivos)
        )

    def exclusivo(ev: p.EvaluacionProducto) -> bool:
        return bool(ev.entrada.grupos_aptos_para_meta)

    def sin_halo(ev: p.EvaluacionProducto) -> bool:
        return "halo_sin_margen" not in ev.motivos

    def completa_s6(ev: p.EvaluacionProducto) -> bool:
        return isinstance(ev.entrada.actividad_pausa, p.AdsCompletos)

    def completa_literal(ev: p.EvaluacionProducto) -> bool:
        """La regla de la spec antes de S6: una fecha sin fila cuenta como cero
        aunque ninguna ingesta demuestre que la pidio. Sigue exigiendo una fila
        en la ventana y madurez por fecha."""
        actividad = ev.entrada.actividad_pausa
        return completa_s6(ev) or set(actividad.motivos) <= {"fecha_desconocida"}

    resumen = datos["resumen"]
    return {
        "plataforma": resumen["plataforma"],
        "decidido_el": datos["entradas"][0]["decidido_el"] if datos["entradas"] else None,
        "productos_evaluados": len(evaluaciones),
        "con_margen": sum(map(con_margen, evaluaciones)),
        "con_grupo_exclusivo": sum(map(exclusivo, evaluaciones)),
        "con_margen_y_grupo_exclusivo": sum(con_margen(e) and exclusivo(e) for e in evaluaciones),
        "ventana_completa_s6": sum(map(completa_s6, evaluaciones)),
        "ventana_completa_regla_literal": sum(map(completa_literal, evaluaciones)),
        "sin_halo": sum(map(sin_halo, evaluaciones)),
        "con_margen_y_sin_halo": sum(con_margen(e) and sin_halo(e) for e in evaluaciones),
        "con_meta": resumen["con_meta"],
        "aptos_para_meta": sum(
            isinstance(e.meta, p.MetaReferencia) and exclusivo(e) for e in evaluaciones
        ),
        "grupos_aptos": resumen["grupos_aptos"],
        "hojas_bajo_grupo_apto": resumen["hojas_bajo_grupo_apto"],
        "hojas_pisadas_por_goal_con_target": resumen["hojas_pisadas_por_goal_con_target"],
        "hojas_efectivas": resumen["hojas_efectivas"],
        "candidatos_pausa": resumen["candidatos_pausa"],
        "anuncios_afectables": resumen["anuncios_afectables"],
        "anuncios_elegibles": resumen["anuncios_elegibles"],
        "abstenciones": resumen["abstenciones"],
        "motivos_ventana_pausa": resumen["motivos_ventana_pausa"],
        "motivos_de_los_que_tienen_meta": dict(
            Counter(
                m for e in evaluaciones if isinstance(e.meta, p.MetaReferencia) for m in e.motivos
            )
        ),
    }


if __name__ == "__main__":
    for ruta in sys.argv[1:]:
        print(json.dumps(mide(ruta), ensure_ascii=False, sort_keys=True))
