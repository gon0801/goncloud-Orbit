"""Exhaustividad del registro hoja `app/etiquetas.py` contra sus dominios duenos.

`app/etiquetas.py` es una hoja de datos pura (cero imports de `app.*`).
Este modulo conserva todas las garantias que antes corrian en import-time y
comprueba que cada vocabulario dueno este cubierto al 100 % en `ETIQUETAS`.
"""

from __future__ import annotations

from typing import get_args

import pytest

from app import apply_cola, etiquetas, fabrica_plan
from app import cycle as ciclo
from app import disponibilidad as disp
from app import evaluacion_catalogo as evaluacion
from app.bloqueo_dueno import PASOS_VIVOS
from app.estimacion_venta import EXCLUSIONES_FIJAS, MOTIVOS_ESTIMACION
from app.optimizer import bid, eras, hygiene, politica, productos, proteccion
from app.optimizer import evidencia as ev
from app.optimizer import goals as g
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.pantalla_dinero import TIPOS, UBICACIONES, EstrategiaPuja
from app.precio.siembra import Bloqueo as BloqueoSiembra
from app.propuestas_campana import TIPO_RIESGO
from app.retiro_anuncios import MotivoRetiro, ResultadoRegreso, ResultadoRetiro

VOCABULARIOS_DUENOS = (
    ("plataforma", PLATAFORMAS_MONEDA),
    ("procedencia", g.PELDANOS_CASCADA),
    ("pisa", g.PELDANOS_CASCADA),
    ("tipo_campana", TIPOS),
    ("ubicacion", UBICACIONES),
    ("estrategia_puja", get_args(EstrategiaPuja)),
    ("motivo_retiro", get_args(MotivoRetiro)),
    ("motivo_abstencion", g.MOTIVOS_ABSTENCION),
    ("paso_bloqueo", PASOS_VIVOS),
    ("motivo_estimacion", MOTIVOS_ESTIMACION),
    ("exclusion_estimacion", EXCLUSIONES_FIJAS),
    ("resultado_retiro", get_args(ResultadoRetiro)),
    ("resultado_regreso", get_args(ResultadoRegreso)),
    ("bloqueo_siembra", get_args(BloqueoSiembra)),
    ("nivel", get_args(politica.NivelQueJuzga)),
    ("motivo_producto", productos.MOTIVOS_PRODUCTO),
    ("degradacion_ciclo", (g.NOTA_LIVE_DEGRADADO,)),
    ("motivo_propuesta_campana", (TIPO_RIESGO,)),
    (
        "etiqueta_ads",
        (
            evaluacion.ETIQUETA_SIN_DATOS,
            evaluacion.ETIQUETA_GASTO_SIN_VENTAS,
            evaluacion.ETIQUETA_DENTRO_DEL_OBJETIVO,
            evaluacion.ETIQUETA_POR_ENCIMA_DEL_OBJETIVO,
        ),
    ),
    (
        "estado_disponibilidad",
        (
            disp.ESTADO_POSITIVO,
            disp.ESTADO_CERO,
            disp.ESTADO_DESCONOCIDO,
            disp.ESTADO_SIN_VERIFICAR,
        ),
    ),
    ("estado_oferta_destacada", disp.ESTADOS_OFERTA_DESTACADA),
    (
        "objetivo_puja",
        (*fabrica_plan.EXPRESION_BID_POR_ROL.values(), *fabrica_plan.EXPRESIONES_AUTO),
    ),
    (
        "motivo_salud",
        (
            *(
                v
                for k, v in vars(politica).items()
                if k.startswith("MOTIVO_")
                and isinstance(v, str)
                and v not in etiquetas.MOTIVO_DECISION
            ),
            *(v for k, v in vars(ciclo).items() if k.startswith("MOTIVO_") and isinstance(v, str)),
            proteccion.MOTIVO_PROTEGIDA,
            proteccion.MOTIVO_INDETERMINADA,
            apply_cola.MOTIVO_VETO_PENDIENTE,
            *(
                v
                for k, v in vars(bid).items()
                if k.startswith("MOTIVO_")
                and isinstance(v, str)
                and v not in etiquetas.MOTIVO_DECISION
            ),
            ev.MOTIVO_EVIDENCIA_INSUFICIENTE,
            ev.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE,
            *(
                v
                for k, v in vars(hygiene).items()
                if k.startswith("MOTIVO_")
                and isinstance(v, str)
                and v not in etiquetas.MOTIVO_DECISION
            ),
        ),
    ),
    (
        "motivo_decision",
        (
            bid.MOTIVO_PAUSE,
            bid.MOTIVO_PAUSE_ECONOMICA,
            *eras._MOTIVO_BANDA.values(),
            eras.MOTIVO_BANDA_MENOS_25_CERO_VENTAS,
            hygiene.MOTIVO_NEGATIVE,
            hygiene.MOTIVO_HARVEST,
            ev.MOTIVO_EVIDENCIA_INSUFICIENTE,
            ev.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE,
            productos.MOTIVO_PAUSA,
        ),
    ),
)


@pytest.mark.parametrize(("dominio", "vocabulario"), VOCABULARIOS_DUENOS)
def test_vocabulario_dueno_cubierto(dominio, vocabulario):
    faltan = set(vocabulario) - set(etiquetas.ETIQUETAS[dominio])
    assert not faltan, f"etiquetas.{dominio} sin texto para {sorted(faltan)}"


def test_motivo_producto_espeja_textos_de_productos():
    assert etiquetas.ETIQUETAS["motivo_producto"] == productos.MOTIVOS_PRODUCTO
