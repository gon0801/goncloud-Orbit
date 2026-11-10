"""Registro unico de etiquetas de pantalla (CLARIDAD 01, H.1).

Cada dominio traduce los codigos internos de un vocabulario a palabras de la
persona que usa Orbit. Las claves son las constantes del modulo dueno del
vocabulario cuando existen: si el vocabulario cambia, el import truena aqui y
no en la pantalla. Lo usan las plantillas (filtro `etiqueta`), las APIs del
dashboard y los avisos de Telegram (`notifica`).

`etiqueta` nunca devuelve snake_case: un codigo desconocido sale sin guiones
bajos y con mayuscula inicial, y deja un aviso en el log para agregarlo aqui.
"""

from __future__ import annotations

import logging
from typing import Literal, get_args

from app import cycle as ciclo
from app.bloqueo_dueno import PASOS_VIVOS
from app.optimizer import bid, eras, hygiene, politica, productos
from app.optimizer import evidencia as ev
from app.optimizer import goals as g
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.pantalla_dinero import TIPOS, UBICACIONES, EstrategiaPuja
from app.propuestas_campana import TIPO_RIESGO
from app.retiro_anuncios import MotivoRetiro

logger = logging.getLogger(__name__)

Dominio = Literal[
    "plataforma",
    "motivo_decision",
    "motivo_salud",
    "procedencia",
    "pisa",
    "kind",
    "nivel",
    "propuesta",
    "efecto_rechazo",
    "modo",
    "modo_frase",
    "estado_ciclo",
    "estado_cola",
    "estado_campana",
    "estado_propuesta",
    "estado_aviso",
    "motivo_propuesta_campana",
    "paso_bloqueo",
    "motivo_bloqueo",
    "estado_reporte",
    "clasificacion_inerte",
    "tipo_campana",
    "rol_campana",
    "motivo_hermana",
    "motivo_retiro",
    "ubicacion",
    "estrategia_puja",
    "clase_ajuste",
    "tipo_target",
    "motivo_precio",
    "estado_precio",
    "estado_cambio_precio",
    "causa_compuerta",
    "insumo_compuerta",
    "motivo_abstencion",
    "motivo_sin_lectura",
    "motivo_indeterminado",
    "motivo_cierre_jev",
    "degradacion_ciclo",
    "motivo_variacion",
    "fuente_ingesta",
    "reporte_ads",
    "fuente_quota",
    "incidente_ingesta",
    "metodo_fx",
    "motivo_contribucion",
    "tipo_alerta",
    "alcance_goal",
]

_SUBIDA_SIN_ECONOMIA = "Subida de puja frenada: las visitas de más no pagaron el gasto extra"
_PAUSA = "Pausa: tuvo clics y gasto sobre el umbral sin vender"
_SIN_EVIDENCIA = "Todavía no hay datos suficientes para estar seguros: sin cambio de puja"
_SIN_PRECIO_CLIC = (
    "Menos de 20 clics desde el último cambio de puja: aún no se sabe cuánto cuesta cada clic"
)

MOTIVO_SALUD: dict[str, str] = {
    ciclo.MOTIVO_SIN_GOAL: "La campaña no tiene meta configurada: Orbit no mueve su puja",
    ciclo.MOTIVO_GOAL_DISABLED: "La meta de la campaña está apagada: Orbit no mueve su puja",
    ciclo.MOTIVO_GOAL_MODE_OFF: "La meta está apagada: Orbit no mueve su puja",
    ciclo.MOTIVO_ESTADO_NO_ENABLED: "La keyword o el producto no está habilitado o no tiene estado",
    ciclo.MOTIVO_CAMPANA_NO_ENABLED: "Campaña no habilitada (pausada, archivada o sin estado)",
    ciclo.MOTIVO_GRUPO_NO_ENABLED: (
        "Grupo de anuncios no habilitado (pausado, archivado o sin estado)"
    ),
    ciclo.MOTIVO_ENTIDAD_INERTE: "Sin impresiones en 14 días: Orbit no mueve su puja",
    # Lo cuentan la espera tras cualquier cambio aplicado (puja o pausa) y la de
    # una pausa aplicada aunque el dueno la haya revertido (goals.en_cooldown).
    ciclo.MOTIVO_COOLDOWN_7D: (
        "Orbit ya le aplicó un cambio en Amazon hace menos de 7 días (una puja o una pausa,"
        " aunque después se haya revertido): espera antes de volver a actuar"
    ),
    ciclo.MOTIVO_ESCALERA_OFF: "El motor de pujas está apagado en todas las plataformas",
    ciclo.MOTIVO_POLITICA_APAGADA: "El motor de pujas está apagado en esta plataforma",
    ciclo.MOTIVO_SIN_TARGET: "No se pudo calcular una meta de ACoS: sin cambio de puja",
    ciclo.MOTIVO_PRODUCTO_SIN_EVIDENCIA: (
        "La meta por producto no se evaluó en este ciclo: sin cambio de puja"
    ),
    politica.MOTIVO_ESPERANDO_EFECTO: "Puja cambiada hace poco: espera 7 días para ver su efecto",
    politica.MOTIVO_VIGENTE_ENCIMA: (
        "La puja actual ya está arriba de la puja a la que regresaría: sin cambio"
    ),
    politica.MOTIVO_VENDE_DENTRO_DEL_MARGEN: "Vende dentro del margen: sin cambio de puja",
    politica.MOTIVO_AZAR_LO_EXPLICA: "La diferencia puede ser suerte: sin cambio de puja",
    politica.MOTIVO_VENTA_RECIENTE: (
        "Vendió hace poco y esos días todavía pueden cambiar: sin cambio de puja"
    ),
    politica.MOTIVO_SIN_GASTO: "Sin gasto: sin cambio de puja",
    politica.MOTIVO_HOJA_DELGADA: "Gasta tan poco que no vale la pena mover la puja",
    politica.MOTIVO_GRUPO_CUMPLE: "Su grupo de anuncios cumple la meta: sin cambio de puja",
    politica.MOTIVO_SIN_EVIDENCIA: "Sin datos suficientes en la keyword ni en su grupo: sin cambio",
    politica.MOTIVO_ESPERANDO_PRECIO_MEDIDO: (
        "Puja cambiada hace poco: espera a medir cuánto cuesta cada clic"
    ),
    politica.MOTIVO_SIN_CLICS_NUEVOS: "Sin clics nuevos desde el último cambio de puja",
    politica.MOTIVO_RECORTE_COSTO_TRAFICO: "Bajada de puja frenada: perdería demasiadas visitas",
    politica.MOTIVO_SUBIDA_SIN_TRAFICO: "Subida de puja frenada: no hay visitas para medirla",
    politica.MOTIVO_SUBIDA_SIN_ECONOMIA: _SUBIDA_SIN_ECONOMIA,
    politica.MOTIVO_PISO_APRENDIDO: (
        "La bajada llegaría a una puja que ya le hizo daño: sin cambio de puja"
    ),
    politica.MOTIVO_DATO_FALTANTE: "Falta un dato: sin cambio de puja",
    politica.MOTIVO_SIN_PRECIO: "No se sabe cuánto cuesta cada clic: sin cambio de puja",
    # guardas de plataforma (windows.py; el envelope las persiste como
    # motivo_skip = guarda_<guarda>)
    "guarda_watermark": "Los datos de la plataforma tienen más de 7 días de atraso",
    "guarda_synced_at": "Las campañas no se sincronizan con Amazon desde hace más de 48 horas",
    "guarda_sin_datos": "La plataforma no tiene métricas ni estado",
    bid.MOTIVO_PAUSE: _PAUSA,
    bid.MOTIVO_PAUSE_CORTES_INCOMPLETO: "Pausa detenida: faltan días en la ventana de la pausa",
    bid.MOTIVO_PAUSE_MONEDA_INVALIDA: (
        "Pausa detenida: la moneda de las métricas no es la de la plataforma"
    ),
    bid.MOTIVO_PAUSE_ORDERS_DESCONOCIDO: "Pausa detenida: no se sabe cuántos pedidos tuvo",
    bid.MOTIVO_PAUSE_CLICKS_COST_DESCONOCIDOS: "Pausa detenida: no se saben sus clics o su gasto",
    bid.MOTIVO_BIDS_SIN_OBSERVACIONES: "Sin datos de su puja en la ventana",
    bid.MOTIVO_BIDS_INCOMPLETO: (
        "Cambio de puja detenido: la ventana tiene menos de 7 días con datos"
    ),
    bid.MOTIVO_BIDS_MONEDA_INVALIDA: (
        "Cambio de puja detenido: la moneda de las métricas no es la de la plataforma"
    ),
    bid.MOTIVO_ACOS_DESCONOCIDO: "No se sabe el ACoS: falta el gasto o las ventas",
    bid.MOTIVO_RANGO_BLOQUEA_AJUSTE: "La puja mínima o máxima configurada impide el cambio",
    bid.MOTIVO_BID_ACTUAL_INVALIDO: "La puja actual no es válida",
    bid.MOTIVO_BID_ACTUAL_AUSENTE: "No se sabe la puja actual",
    bid.MOTIVO_BID_MONEDA_INVALIDA: "La puja tiene una moneda que no es la de la plataforma",
    bid.MOTIVO_SIN_BANDA: "El ACoS está dentro del rango de la meta: sin cambio de puja",
    bid.MOTIVO_DELTA_BAJO_UMBRAL: "El cambio sería menor a 0.01: no se hace",
    ev.MOTIVO_EVIDENCIA_INSUFICIENTE: _SIN_EVIDENCIA,
    ev.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE: _SIN_PRECIO_CLIC,
    hygiene.MOTIVO_ENTIDAD_INCOMPLETA: "Faltan días de datos para decidir",
    hygiene.MOTIVO_ASIN_LIKE: "La búsqueda es un código de producto: no se bloquea ni se captura",
    hygiene.MOTIVO_ORDERS_DESCONOCIDO: "No se sabe cuántos pedidos tuvo la búsqueda",
    hygiene.MOTIVO_DATO_FALTANTE: "Falta un dato de la búsqueda",
    hygiene.MOTIVO_SIN_UMBRAL_NEGATIVE: (
        "La búsqueda no llega a los clics o al gasto para bloquearla"
    ),
    hygiene.MOTIVO_ACOS_SOBRE_TOPE: "El ACoS de la búsqueda pasa el tope para capturarla",
    hygiene.MOTIVO_HARVEST_SIN_CONFIG: "No hay campaña manual configurada para capturar búsquedas",
    hygiene.MOTIVO_HARVEST_DUPLICADO: "La búsqueda ya existe como keyword: no se captura otra vez",
    hygiene.MOTIVO_HARVEST_MONEDA_INCOHERENTE: "Captura detenida: la moneda no cuadra",
    hygiene.MOTIVO_MONEDA_INCOHERENTE: "La moneda no cuadra",
    hygiene.MOTIVO_ORIGEN_ES_DESTINO: (
        "Captura saltada: el grupo de anuncios de origen ya es el destino de la campaña"
    ),
    hygiene.MOTIVO_SIN_DESTINO_HARVEST: (
        "Captura sin destino: falta el grupo exacto, la excepción o el destino en la meta de la"
        " campaña"
    ),
    hygiene.MOTIVO_DESTINO_INCONSISTENTE: (
        "Captura detenida: el destino de la meta de la campaña no coincide con su grupo exacto"
    ),
    hygiene.MOTIVO_DESTINO_DESINCRONIZADO: (
        "Captura descartada: el grupo exacto cambió después de decidir"
    ),
    hygiene.MOTIVO_MIGRACION_PENDIENTE: (
        "Destino tomado de la meta de la campaña (falta pasarlo a un grupo o a una excepción)"
    ),
}

# `{que}` lo llena `motivo_decision_es` con el sujeto de la fila («La keyword»,
# «El producto objetivo»): la persona tiene que saber que vende o pierde dinero.
MOTIVO_DECISION: dict[str, str] = {
    bid.MOTIVO_PAUSE: _PAUSA,
    eras._MOTIVO_BANDA[bid.FACTOR_BAJA_FUERTE]: "ACoS 35 % arriba de la meta: baja la puja 25 %",
    eras._MOTIVO_BANDA[bid.FACTOR_BAJA_SUAVE]: "ACoS 15 % arriba de la meta: baja la puja 12 %",
    eras.MOTIVO_BANDA_MENOS_25_CERO_VENTAS: (
        "Cero ventas con los clics de una venta y gasto sobre el mínimo: baja la puja 25 %"
    ),
    eras._MOTIVO_BANDA[bid.FACTOR_SUBIDA]: "ACoS 15 % abajo de la meta: sube la puja 15 %",
    hygiene.MOTIVO_NEGATIVE: "Negativo: la búsqueda tuvo clics y gasto sobre el umbral sin vender",
    hygiene.MOTIVO_HARVEST: (
        "Captura: la búsqueda vende con ACoS bajo el tope y pasa a la campaña manual"
    ),
    ev.MOTIVO_EVIDENCIA_INSUFICIENTE: _SIN_EVIDENCIA,
    ev.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE: _SIN_PRECIO_CLIC,
    bid.MOTIVO_PAUSE_ECONOMICA: "Pausa por costo: cada venta cuesta más de 3 veces la meta",
    politica.MOTIVO_REGRESO_DESPLOME: (
        "{que} regresó a su puja anterior: la nueva hizo caer sus visitas"
    ),
    politica.MOTIVO_PIERDE_DINERO: "{que} pierde dinero: baja su puja 12 %",
    politica.MOTIVO_PIERDE_DINERO_FUERTE: "{que} pierde mucho dinero: baja su puja 25 %",
    politica.MOTIVO_GRUPO_PIERDE_VENDEDORA: (
        "{que} vende, pero su grupo de anuncios pierde dinero: baja su puja 12 %"
    ),
    politica.MOTIVO_BAJO_TARGET: "{que} tiene el ACoS por debajo de la meta: sube su puja 15 %",
    politica.MOTIVO_GASTO_SIN_VENTA: (
        "{que} ya gastó lo necesario para decidir y no vendió: baja su puja 12 %"
    ),
    politica.MOTIVO_GASTO_SIN_VENTA_DOBLE: (
        "{que} gastó el doble de lo necesario para decidir y no vendió: baja su puja 25 %"
    ),
    politica.MOTIVO_GRUPO_PIERDE: (
        "{que} no vende y su grupo de anuncios pierde dinero: baja su puja 12 %"
    ),
    politica.MOTIVO_SUBIDA_SIN_ECONOMIA: (
        "{que}: subida de puja frenada, las visitas de más no pagaron el gasto extra"
    ),
    productos.MOTIVO_PAUSA: "Pausa del anuncio: el producto no vendió en la ventana de la pausa",
}

PLATAFORMA: dict[str, str] = {
    "amazon_mx": "Amazon MX",
    "amazon_us": "Amazon US",
    "meli": "Mercado Libre",
}

PROCEDENCIA: dict[str, str] = {
    "goal_campana": "Meta de la campaña",
    "goal_plataforma": "Meta de la plataforma",
    "margen_producto": "Meta por producto",
    "margen_familia": "Margen de la familia",
    "margen_plataforma": "Margen de la plataforma",
    "setting_plataforma": "Meta manual de la plataforma",
}

PISA: dict[str, str] = {
    "goal_campana": "pisa a la meta de otra campaña",
    "goal_plataforma": "pisa a la meta de la plataforma",
    "margen_producto": "pisa a la meta por producto",
    "margen_familia": "pisa a la meta por margen de la familia",
    "margen_plataforma": "pisa a la meta por margen de la plataforma",
    "setting_plataforma": "pisa a la meta manual de la plataforma",
}

KIND: dict[str, str] = {
    "bid": "Puja",
    "budget": "Presupuesto",
    "pause": "Pausa",
    "resume": "Reactivación",
    "negative": "Negativo",
    "harvest": "Captura",
}

# Donde se midio la evidencia de una decision de puja (politica.decide), en
# el orden de politica.NivelQueJuzga.
NIVEL: dict[str, str] = dict(
    zip(
        get_args(politica.NivelQueJuzga),
        ("la keyword o el producto", "el grupo de anuncios"),
        strict=True,
    )
)

# Propuestas (CORTES UI 01): la pausa se nombra por `kind:entidad_kind`;
# negative y harvest no dependen de la entidad.
PROPUESTA: dict[str, str] = {
    "pause:keyword": "Apagar palabra",
    "pause:product_target": "Apagar segmentación de producto",
    "pause:product_ad": "Pausar anuncio del producto",
    "pause": "Apagar",
    "negative": "Bloquear busqueda",
    "harvest": "Capturar termino que vende",
}

EFECTO_RECHAZO: dict[str, str] = {
    "pause:keyword": "Rechazar: la palabra NO se apagara (seguira gastando)",
    "pause:product_target": (
        "Rechazar: la segmentación de producto NO se apagará (seguirá gastando)"
    ),
    "pause:product_ad": "Rechazar: el anuncio del producto NO se pausará (seguirá gastando)",
    "pause": "Rechazar: NO se apagará (seguirá gastando)",
    "negative": "Rechazar: la busqueda NO se bloqueara",
    "harvest": "Rechazar: la palabra NO se creara",
}

MODO: dict[str, str] = {"off": "Apagado", "shadow": "Práctica", "live": "En vivo"}

# Modo dentro de una frase («la pausa está en práctica»).
MODO_FRASE: dict[str, str] = {"off": "apagada", "shadow": "en práctica", "live": "en vivo"}

ESTADO_CICLO: dict[str, str] = {
    "running": "En curso",
    "done": "Completo",
    "degraded": "Con fallas",
    "skipped": "Saltado",
    "failed": "Falló",
}

ESTADO_COLA: dict[str, str] = {
    "pending_veto": "Esperando tu revisión",
    "released": "Lista para aplicarse",
    "vetoed": "Rechazada",
    "applied": "Aplicada",
    "failed": "Falló",
    "discarded": "Descartada",
}

ESTADO_CAMPANA: dict[str, str] = {
    "ENABLED": "Activa",
    "PAUSED": "Pausada",
    "ARCHIVED": "Archivada",
}

ESTADO_PROPUESTA: dict[str, str] = {
    "open": "Abierta",
    "resolved": "Resuelta",
    "dismissed": "Descartada",
    "paused_observed": "Ya pausada",
    "paused_external": "Pausada fuera de Orbit",
}

ESTADO_AVISO: dict[str, str] = {"pending": "Por enviar", "sent": "Enviado"}

MOTIVO_PROPUESTA_CAMPANA: dict[str, str] = {
    TIPO_RIESGO: "Gasta mucho más de lo que permite su meta",
    "bajo_limite": "Dentro de su límite",
    "estado_vencido": "Estado de la campaña sin actualizar",
    "campana_no_enabled": "La campaña no está activa",
    "goal_disabled": "Su meta está apagada",
    "ventana_incompleta": "Faltan días en la ventana",
    "ventana_inmadura": "La ventana aún no madura",
    "moneda_invalida": "Moneda que no es la de la plataforma",
    "cost_ausente": "Sin dato de gasto",
    "revenue_ausente": "Sin dato de ventas",
    "target_ausente": "Sin meta",
    "dato_invalido": "Dato inválido",
}

PASO_BLOQUEO: dict[str, str] = {
    "intencion": "Por aplicar",
    "aplicado": "Aplicado",
    "reversa_intencion": "Quitándose",
    "reversa_fallida": "No se pudo quitar",
}

MOTIVO_BLOQUEO: dict[str, str] = {"por_confirmar": "por confirmar en Amazon"}

ESTADO_REPORTE: dict[str, str] = {
    "written": "Escrito",
    "pending": "Pendiente",
    "requested": "Pedido",
    "failed": "Falló",
    "rejected": "Rechazado",
    "global_failed": "Falló en todos los perfiles",
}

CLASIFICACION_INERTE: dict[str, str] = {
    "con_ventas_previas": "Vendió antes",
    "gasto_sin_ventas": "Gastó sin vender",
    "peso_muerto": "Sin gasto ni ventas",
}

TIPO_CAMPANA: dict[str, str] = {
    "exact": "Exacta",
    "phrase": "Frase",
    "broad": "Amplia",
    "automatica": "Automática",
    "product_targeting": "Por producto",
}

ROL_CAMPANA: dict[str, str] = {
    "category_exact": "Exacta de la categoría",
    "category_phrase": "Frase de la categoría",
    "category_broad": "Amplia de la categoría",
    "product_targeting": "Por producto",
    "auto_discovery": "Automática",
}

# Por que una campaña hermana quedo sin el negativo (apply_harvest).
MOTIVO_HERMANA: dict[str, str] = {
    "pt_no_acepta_negative_keyword": "la campaña por producto no acepta negativos de keyword",
    "http_5xx": "Amazon falló de su lado",
    "http_400": "Amazon rechazó el cambio",
    "ack_sin_id": "Amazon respondió sin confirmar",
    "red_ambigua": "falla de red sin respuesta clara",
}

MOTIVO_RETIRO: dict[str, str] = {
    "impulso": "Terminó su impulso",
    "sin_pedidos": "Sin pedidos",
    "subfamilia": "Su subfamilia se retira",
    "producto_sin_ventas": "El producto no vende",
}

UBICACION: dict[str, str] = {
    "arriba_de_busqueda": "Arriba de búsqueda",
    "resto_de_busqueda": "Resto de búsqueda",
    "paginas_de_producto": "Páginas de producto",
    "fuera_de_amazon": "Fuera de Amazon",
}

ESTRATEGIA_PUJA: dict[str, str] = {
    "solo_hacia_abajo": "solo hacia abajo",
    "arriba_y_abajo": "hacia arriba y hacia abajo",
    "fija": "fija",
    "otra": "otra",
}

CLASE_AJUSTE: dict[str, str] = {
    "presupuesto": "el presupuesto",
    "ajuste_ubicacion": "el ajuste de ubicación",
}

TIPO_TARGET: dict[str, str] = {
    "ASIN_SAME_AS": "mismo ASIN",
    "ASIN_ACCESSORY_RELATED": "accesorio",
    "ASIN_SUBSTITUTE_RELATED": "sustituto",
    "ASIN_ACCESSORY": "accesorio",
    "ASIN_BRAND_SAME_AS": "misma marca",
    "ASIN_CATEGORY_SAME_AS": "misma categoria",
    "ASIN_EXPANDED_FROM": "expandido de",
    "ASIN_AGE_RANGE_SAME_AS": "misma edad",
    "ASIN_GENRE_SAME_AS": "mismo genero",
    "ASIN_IS_PRIME_SHIPPING_ELIGIBLE": "Prime",
    "ASIN_PRICE_LESS_THAN": "precio menor a",
    "ASIN_PRICE_GREATER_THAN": "precio mayor a",
    "ASIN_PRICE_BETWEEN": "precio entre",
    "ASIN_REVIEW_RATING_LESS_THAN": "calificación menor a",
    "ASIN_REVIEW_RATING_GREATER_THAN": "calificación mayor a",
    "ASIN_REVIEW_RATING_BETWEEN": "calificación entre",
    "QUERY_BROAD_REL_MATCHES": "búsqueda amplia",
    "QUERY_HIGH_REL_MATCHES": "búsqueda cercana",
}

MOTIVO_PRECIO: dict[str, str] = {
    "precio_divergente": "precio observado distinto del publicado",
    "moneda_divergente": "moneda distinta entre observacion y escenario",
    "precio_sin_observar": "sin observacion de precio del dia",
    "precio_ausente": "sin precio publicado",
    "fee_ausente": "sin cotizacion de comision",
    "fee_no_lineal": "comision no lineal",
    "fee_error": "falla de comision",
    "impuesto_fee_pendiente": "impuesto pendiente en la comision",
    "escenario_incoherente": "escenario incoherente",
    "estimacion_motivo_desconocido": "motivo de estimacion desconocido",
    "ingreso_no_positivo": "ingreso no positivo",
    "precio_no_cubre_costo": "precio bajo el piso de rentabilidad",
    "precio_mayor_al_doble": "precio mayor al doble del objetivo",
    "sobre_goal_sin_perdida": "por encima del objetivo sin perdida",
    "ledger_hueco": "ventas sin dato (libro rezagado)",
    "historia_corta": "historia de ventas corta",
    "sin_dato": "sin dato",
    "ventas_sin_dato": "ventas sin dato",
    "oferta_desactualizada": "oferta sin observacion reciente",
    "n15_insuficiente": "ventas de 15 dias insuficientes",
    "u60_bajo_minimo": "unidades de 60 dias bajo el minimo",
    "racha_incompleta": "racha de senal incompleta",
    "ventana_60_excluida": "ventana de 60 dias excluida",
    "dia_no_disponible": "dia no disponible",
    "dia_sin_observacion": "dia sin observacion",
    "venta_sin_publicacion": "venta sin publicacion",
    "evidencia_insuficiente": "evidencia insuficiente",
    "no_converge": "sin convergencia tras el cambio de direccion",
    "perdiendo_tras_subida": "perdiendo tras la subida",
    "api_error": "falla de la API",
    "no_confirmado": "no confirmado por la observacion",
    "buy_box_perdida": "buy box perdida",
    "cooldown": "enfriamiento entre cambios",
    "en_tolerancia": "dentro de la tolerancia",
    "movimiento_minimo": "movimiento bajo el minimo",
    "sin_decision": "sin decision",
    "sin_listing": "sin publicación",
    "catalogo_desactualizado": "catalogo desactualizado",
    "no_a_la_venta": "no esta a la venta",
    "canal_sin_dato": "canal sin dato",
    "desconocido": "desconocido",
    "universo_apagado": "motor apagado en este canal",
}

ESTADO_PRECIO: dict[str, str] = {
    "subir": "subida",
    "bajar": "bajada",
    "mantener": "mantenido",
    "no_evaluado": "no evaluado",
    "goal_inalcanzable": "objetivo inalcanzable",
    "frenado": "frenado",
    "no_confirmado": "no confirmado",
    "buy_box_perdida": "buy box perdida",
}

ESTADO_CAMBIO_PRECIO: dict[str, str] = {
    "pendiente": "pendiente de confirmación",
    "enviado": "enviado",
    "error": "en error",
    "confirmado": "confirmado",
    "no_confirmado": "no confirmado por la observación",
}

CAUSA_COMPUERTA: dict[str, str] = {
    "movimiento_masivo": "movimiento masivo",
    "insumo_sistemico": "insumo sistemico",
}

# El insumo sale en el aviso de Telegram y "costo" literal esta vetado en
# esos textos (DoD S.6 de REPRICING), asi que viaja como "coste".
INSUMO_COMPUERTA: dict[str, str] = {
    "costo": "coste",
    "envio": "envio",
    "retencion": "retencion",
    "fx": "fx",
    "fee_fijo": "comision fija",
    "fee_variable": "comision variable",
}

MOTIVO_ABSTENCION: dict[str, str] = {
    "sin_margen": "sin margen medido en la ventana",
    "cobertura_baja": "cobertura de costos bajo el minimo",
    "ventana_corta": "pocos dias con venta en la ventana",
    "sin_fraccion": "sin la fracción del margen configurada para la plataforma",
    "ledger_rancio": "las ventas del libro llevan más de 3 días sin actualizarse",
    "sin_familia": "keyword sin familia etiquetada (no se usa el margen de la familia)",
}

MOTIVO_SIN_LECTURA: dict[str, str] = {
    "dato_de_venta_faltante": "falta un dato de venta",
    "sin_observaciones": "sin observaciones",
    "jev_sin_veredicto": "Jev no concluyó",
    "jev_no_evaluada": "Jev no la evaluó",
}

# Por que Jev no concluyo sobre una busqueda (jev_ads.MotivoIndeterminado; el
# registro no importa modulos jev_*, candado de tests/test_architecture.py).
MOTIVO_INDETERMINADO: dict[str, str] = {
    "ficha_ausente": "falta la ficha del producto",
    "juicio_ausente": "falta el juicio",
    "juicio_insuficiente": "juicio insuficiente",
    "universo_desconocido": "no se sabe qué productos anuncia el grupo",
    "universo_vacio": "el grupo no anuncia productos",
    "fallo_proveedor": "falló el proveedor",
    "texto_no_aplica": "el texto no aplica",
    "no_anunciado": "producto no anunciado",
    "missing_state": "falta el estado",
}

# Como cerro la ultima corrida de Jev (jev_senales.MotivoCierre).
MOTIVO_CIERRE_JEV: dict[str, str] = {
    "apagado": "apagado",
    "ocupado": "otra corrida en curso",
    "completa": "completa",
    "tope": "llegó al tope diario",
    "proveedor_caido": "el proveedor no respondió",
    "sin_api_key": "sin llave del proveedor",
}

# Frase fija con la que el ciclo marca que el modo en vivo corrio en
# practica (notes.degradacion_live; goals.resuelve_modo).
DEGRADACION_CICLO: dict[str, str] = {
    g.NOTA_LIVE_DEGRADADO: "Se pidió el modo en vivo, pero el ciclo corrió en práctica",
}

# Por que una variacion del Resumen no se calcula (dashboard_resumen).
MOTIVO_VARIACION: dict[str, str] = {
    "cobertura_desconocida": "cobertura desconocida en alguno de los periodos",
    "cobertura_distinta": "cobertura distinta entre periodos",
    "cobertura_incompleta": "cobertura incompleta en alguno de los periodos",
    "dinero_no_alineado": "gasto y ventas no cubren los mismos dias",
}

FUENTE_INGESTA: dict[str, str] = {
    "amazon_ads_reports_v3": "Reportes de Amazon Ads",
    "search_terms": "Búsquedas de los compradores",
    "spapi_pricing": "Precios de Amazon",
    "spapi_catalog": "Catálogo de Amazon",
    "spapi_orders": "Pedidos de Amazon",
    "spapi_inventory": "Inventario de Amazon",
    "spapi_finances": "Finanzas de Amazon",
    "spapi_listings": "Publicaciones de Amazon",
    "spapi_fees": "Comisiones de Amazon",
    "spapi_inventario": "Inventario de Amazon",
}

REPORTE_ADS: dict[str, str] = {
    "campanas": "Campañas",
    "keywords": "Keywords",
    "targets": "Productos objetivo",
    "search_terms": "Búsquedas",
    "productos": "Productos anunciados",
}

FUENTE_QUOTA: dict[str, str] = {
    "fila_del_dia": "Contado hoy",
    "config_vigente": "Tope de la configuración",
    "sin_clave": "Sin tope configurado: no se aplica nada",
    "config_rota": "Configuración ilegible: revisar",
}

INCIDENTE_INGESTA: dict[str, str] = {
    "fallo": "falló",
    "atraso": "atrasada",
    "recuperacion": "recuperada",
}

METODO_FX: dict[str, str] = {
    "exact": "del día",
    "nearest_prior": "del último día con dato",
}

MOTIVO_CONTRIBUCION: dict[str, str] = {
    "kind_fuera": "tipo de anuncio que no entra al cálculo",
    "sin_padre": "sin campaña a la que sumar",
    "serie_incompleta": "faltan días en la serie",
    "catalogo_parcial": "catálogo incompleto",
    "sin_precio": "sin precio",
    "sin_mezcla_ledger": "sin ventas en el libro para repartir",
    "sin_fx": "sin tipo de cambio",
}

TIPO_ALERTA: dict[str, str] = {
    "resena_1": "Reseña de 1 estrella",
    "rating_bajo": "Calificación baja",
    "caida_rating": "Caída de calificación",
    "reclamos_suben": "Suben los reclamos",
    "salud_cuenta": "Salud de la cuenta",
}

ALCANCE_GOAL: dict[str, str] = {
    "campaign": "Campaña",
    "platform": "Plataforma",
}

ETIQUETAS: dict[Dominio, dict[str, str]] = {
    "plataforma": PLATAFORMA,
    "motivo_decision": MOTIVO_DECISION,
    "motivo_salud": MOTIVO_SALUD,
    "procedencia": PROCEDENCIA,
    "pisa": PISA,
    "kind": KIND,
    "nivel": NIVEL,
    "propuesta": PROPUESTA,
    "efecto_rechazo": EFECTO_RECHAZO,
    "modo": MODO,
    "modo_frase": MODO_FRASE,
    "estado_ciclo": ESTADO_CICLO,
    "estado_cola": ESTADO_COLA,
    "estado_campana": ESTADO_CAMPANA,
    "estado_propuesta": ESTADO_PROPUESTA,
    "estado_aviso": ESTADO_AVISO,
    "motivo_propuesta_campana": MOTIVO_PROPUESTA_CAMPANA,
    "paso_bloqueo": PASO_BLOQUEO,
    "motivo_bloqueo": MOTIVO_BLOQUEO,
    "estado_reporte": ESTADO_REPORTE,
    "clasificacion_inerte": CLASIFICACION_INERTE,
    "tipo_campana": TIPO_CAMPANA,
    "rol_campana": ROL_CAMPANA,
    "motivo_hermana": MOTIVO_HERMANA,
    "motivo_retiro": MOTIVO_RETIRO,
    "ubicacion": UBICACION,
    "estrategia_puja": ESTRATEGIA_PUJA,
    "clase_ajuste": CLASE_AJUSTE,
    "tipo_target": TIPO_TARGET,
    "motivo_precio": MOTIVO_PRECIO,
    "estado_precio": ESTADO_PRECIO,
    "estado_cambio_precio": ESTADO_CAMBIO_PRECIO,
    "causa_compuerta": CAUSA_COMPUERTA,
    "insumo_compuerta": INSUMO_COMPUERTA,
    "motivo_abstencion": MOTIVO_ABSTENCION,
    "motivo_sin_lectura": MOTIVO_SIN_LECTURA,
    "motivo_indeterminado": MOTIVO_INDETERMINADO,
    "motivo_cierre_jev": MOTIVO_CIERRE_JEV,
    "degradacion_ciclo": DEGRADACION_CICLO,
    "motivo_variacion": MOTIVO_VARIACION,
    "fuente_ingesta": FUENTE_INGESTA,
    "reporte_ads": REPORTE_ADS,
    "fuente_quota": FUENTE_QUOTA,
    "incidente_ingesta": INCIDENTE_INGESTA,
    "metodo_fx": METODO_FX,
    "motivo_contribucion": MOTIVO_CONTRIBUCION,
    "tipo_alerta": TIPO_ALERTA,
    "alcance_goal": ALCANCE_GOAL,
}

# Vocabularios cerrados de su modulo dueno: si uno crece sin etiqueta, truena
# al importar en vez de salir humanizado en la pantalla.
for _dominio, _vocabulario in (
    ("plataforma", PLATAFORMAS_MONEDA),
    ("procedencia", g.PELDANOS_CASCADA),
    ("pisa", g.PELDANOS_CASCADA),
    ("tipo_campana", TIPOS),
    ("ubicacion", UBICACIONES),
    ("estrategia_puja", get_args(EstrategiaPuja)),
    ("motivo_retiro", get_args(MotivoRetiro)),
    ("motivo_abstencion", g.MOTIVOS_ABSTENCION),
    ("paso_bloqueo", PASOS_VIVOS),
):
    _faltan = set(_vocabulario) - set(ETIQUETAS[_dominio])
    if _faltan:
        raise KeyError(f"etiquetas.{_dominio} sin texto para {sorted(_faltan)}")


# `fee_error:<codigo>` trae el codigo de error del proveedor, no un motivo del
# vocabulario: va entre parentesis tal cual (sin guiones bajos).
_SUFIJO_LIBRE = {("motivo_precio", "fee_error")}


def _humaniza(codigo: str) -> str:
    texto = codigo.replace("_", " ").strip()
    return texto[:1].upper() + texto[1:]


def etiqueta(codigo: str | None, dominio: Dominio) -> str:
    """Codigo a texto de pantalla. `a:b` sin entrada propia se traduce por
    partes; desconocido sale humanizado y deja un aviso en el log."""
    if codigo is None or codigo == "":
        return "—"
    tabla = ETIQUETAS[dominio]
    texto = tabla.get(codigo)
    if texto is not None:
        return texto
    base, sep, sub = codigo.partition(":")
    if sep and (dominio, base) in _SUFIJO_LIBRE:
        return f"{tabla[base]} ({_humaniza(sub).lower()})"
    if sep:
        return f"{etiqueta(base.strip(), dominio)}: {etiqueta(sub.strip(), dominio)}"
    logger.warning("etiqueta sin texto: dominio=%s codigo=%s", dominio, codigo)
    return _humaniza(codigo)


# Sujeto de los motivos con {que}: la persona tiene que saber que vende o pierde dinero.
SUJETO_POR_ENTIDAD: dict[str, str] = {
    "keyword": "La keyword",
    "product_target": "El producto objetivo",
    "product_ad": "El anuncio del producto",
    "ad_group": "El grupo de anuncios",
    "campaign": "La campaña",
}


def motivo_decision_es(motivo: str | None, entidad_kind: str | None) -> str | None:
    """Motivo de una decision de puja: los de decision y, por union, los de
    /salud que el feed tambien muestra (abstenciones). None queda None."""
    if motivo is None:
        return None
    if motivo in MOTIVO_DECISION:
        texto = MOTIVO_DECISION[motivo]
    else:
        texto = etiqueta(motivo, "motivo_salud")
    return texto.replace("{que}", SUJETO_POR_ENTIDAD.get(entidad_kind or "", "Este elemento"))
