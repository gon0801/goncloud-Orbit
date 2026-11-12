"""Presentacion de la pagina Salud (DISENOS 01, bloque B).

Convierte el payload de /api/dashboard/salud en el resumen por plataforma y el
detalle por pestanas. No consulta datos ni cambia el endpoint: solo formatea
los mismos valores para el template.
"""

from __future__ import annotations

from app import formato
from app.etiquetas import etiqueta

_TITULOS_SKIP = {"entidad": "Keywords y productos", "termino": "Búsquedas"}
_GRUPO_SIN_PRESUPUESTO = "sin_presupuesto"
_TOPE_NIVEL_CICLOS = 14
_TOPE_NIVEL_SKIP = 20


def _texto(valor) -> str:
    return "—" if valor is None else str(valor)


def _entero(valor) -> int:
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


def _suma(conteos) -> int:
    if isinstance(conteos, dict):
        return sum(_entero(v) for v in conteos.values())
    return sum(_entero(v) for v in conteos or [])


def _nivel(valor, maximo, tope: int) -> int:
    if not maximo or valor <= 0:
        return 0
    nivel = round(valor / maximo * tope)
    return min(tope, max(1, nivel))


def _lista_skips(valor) -> list[dict]:
    if isinstance(valor, dict):
        filas = [
            {"motivo": motivo, "count": count, "motivo_es": etiqueta(motivo, "motivo_salud")}
            for motivo, count in valor.items()
        ]
        return sorted(
            filas,
            key=lambda fila: (-_entero(fila.get("count")), fila.get("motivo") or ""),
        )
    return list(valor or [])


def _clase_estado(status: str | None) -> str:
    if status == "done":
        return "chip ok"
    if status:
        return "chip aviso"
    return "chip"


def _tono(ok: bool | None) -> str:
    if ok is None:
        return "neutro"
    return "ok" if ok else "aviso"


def _resumen_ingesta(ads: dict | None) -> dict:
    reportes = (ads or {}).get("reportes") or []
    if not reportes:
        return {"valor": "—", "tono": "neutro"}
    escritos = sum(1 for reporte in reportes if reporte.get("ultimo_estado") == "written")
    con_fecha = next((r for r in reportes if r.get("metric_date")), None)
    metrica = con_fecha.get("metric_date") if con_fecha else "—"
    edad = con_fecha.get("edad_dias") if con_fecha else None
    edad_texto = f"{edad} dias" if edad is not None else "—"
    valor = f"{escritos} de {len(reportes)} reportes escritos · métrica {metrica} · {edad_texto}"
    return {"valor": valor, "tono": _tono(escritos == len(reportes))}


def _resumen_spapi(spapi: dict | None) -> dict:
    filas = list((spapi or {}).items())
    if not filas:
        return {"valor": "—", "tono": "neutro"}
    ok = sum(1 for _, fuente in filas if (fuente.get("ultima") or {}).get("ok") is True)
    saltadas = sum(
        _entero((fuente.get("ultima") or {}).get("rows_skipped"))
        for _, fuente in filas
        if fuente.get("ultima")
    )
    valor = f"{ok} de {len(filas)} fuentes ok · {formato.numero(saltadas)} filas saltadas"
    return {"valor": valor, "tono": _tono(bool(saltadas == 0 and ok == len(filas)))}


def _telegram_de(ciclo: dict) -> dict | None:
    notas = ciclo.get("notes") if isinstance(ciclo.get("notes"), dict) else {}
    return notas.get("telegram")


def _resumen_ciclo(ciclo: dict) -> dict:
    if not ciclo:
        return {"valor": "—", "tono": "neutro"}
    valor = (
        f"#{ciclo.get('id')} · {formato.fecha_hora(ciclo.get('started_at'))}"
        f" · decisiones {formato.numero(ciclo.get('decisions_count'))}"
        f" · aplicadas {formato.numero(ciclo.get('applied_count'))}"
    )
    telegram = _telegram_de(ciclo)
    if telegram:
        claves = (
            ", ".join(str(clave) for clave in telegram) if isinstance(telegram, dict) else telegram
        )
        valor = f"{valor} · Telegram caído ({claves})"
    return {
        "valor": valor,
        "tono": _tono(ciclo.get("status") == "done" and not telegram),
    }


def _resumen_historico(historico: list[dict]) -> dict:
    if not historico:
        return {"valor": "—", "tono": "neutro"}
    completos = sum(1 for fila in historico if fila.get("status") == "done")
    decisiones = sum(_entero(fila.get("decisions_count")) for fila in historico)
    valor = f"{completos} de 14 completos · {formato.numero(decisiones)} decisiones"
    return {"valor": valor, "tono": _tono(completos == len(historico))}


_MARCADORES_AVISO = (
    (_GRUPO_SIN_PRESUPUESTO, ": se queda sin presupuesto"),
    ("no_limita", ": este presupuesto no limita"),
)


def _partir_aviso(frase: str) -> tuple[str, str, str]:
    """Tipo, campana y texto de un aviso.

    El nombre de la campana puede traer dos puntos; se corta por la frase
    conocida, no por el primer separador.
    """
    for grupo, marcador in _MARCADORES_AVISO:
        indice = frase.rfind(marcador)
        if indice >= 0:
            return grupo, frase[:indice], frase[indice + 2 :]
    campana, separador, texto = frase.partition(": ")
    if not separador:
        return "otros", frase, ""
    return "otros", campana, texto


def _avisos_campana(avisos) -> dict | None:
    if avisos is None:
        return None
    agrupados: dict[str, list[dict]] = {
        _GRUPO_SIN_PRESUPUESTO: [],
        "no_limita": [],
        "otros": [],
    }
    for frase in avisos:
        grupo, campana, texto = _partir_aviso(frase)
        agrupados[grupo].append({"campana": campana, "texto": texto})
    return {"grupos": agrupados}


def _resumen_avisos(grupos: dict | None) -> dict:
    if grupos is None:
        return {"valor": "—", "tono": "neutro"}
    sin_presupuesto = len(grupos.get(_GRUPO_SIN_PRESUPUESTO) or [])
    no_limita = len(grupos.get("no_limita") or [])
    valor = f"{sin_presupuesto} sin presupuesto · {no_limita} con presupuesto que no limita"
    return {"valor": valor, "tono": _tono(sin_presupuesto == 0)}


def _resumen_propuesta(propuesta: dict | None) -> dict:
    if propuesta is None:
        return {"valor": "—", "tono": "neutro"}
    valor = f"{formato.numero(propuesta.get('pendientes'))} pendientes"
    if propuesta.get("fallo"):
        valor = f"{valor} · {propuesta['fallo']}"
    return {"valor": valor, "tono": _tono(not propuesta.get("fallo"))}


def _ciclos(historico: list[dict]) -> dict:
    filas = [
        {
            "id": fila.get("cycle_id"),
            "fecha": formato.fecha_hora(fila.get("fecha")),
            "estado": etiqueta(fila.get("status"), "estado_ciclo") if fila.get("status") else "—",
            "estado_clase": _clase_estado(fila.get("status")),
            "decisiones": _entero(fila.get("decisions_count")),
            "decisiones_texto": formato.numero(fila.get("decisions_count")),
            "motivo": fila.get("motivo") or "—",
        }
        for fila in historico
    ]
    maximo = max([fila["decisiones"] for fila in filas] or [1]) or 1
    barras = [
        {
            "n": fila["decisiones_texto"],
            "nivel": _nivel(fila["decisiones"], maximo, _TOPE_NIVEL_CICLOS),
        }
        for fila in reversed(filas)
    ]
    resumen = _resumen_historico(historico)
    return {
        "filas": filas,
        "barras": barras,
        "primera_fecha": formato.fecha_hora(historico[-1].get("fecha")) if historico else "",
        "ultima_fecha": formato.fecha_hora(historico[0].get("fecha")) if historico else "",
        "resumen": resumen["valor"],
        "tono": resumen["tono"],
        "etiqueta_barras": ", ".join(
            f"{fila['fecha']} {fila['decisiones_texto']}" for fila in reversed(filas)
        ),
    }


def _columna_skip(titulo: str, valor) -> dict:
    filas = _lista_skips(valor)
    maximo = max([_entero(fila.get("count")) for fila in filas] or [1]) or 1
    total = sum(_entero(fila.get("count")) for fila in filas)
    return {
        "titulo": titulo,
        "total": f"{formato.numero(total)} en total",
        "filas": [
            {
                "texto": fila.get("motivo_es") or etiqueta(fila.get("motivo"), "motivo_salud"),
                "n": formato.numero(fila.get("count")),
                "nivel": _nivel(_entero(fila.get("count")), maximo, _TOPE_NIVEL_SKIP),
            }
            for fila in filas
        ],
    }


def _skips(skips: dict | None, ciclo: dict) -> dict:
    skips = skips or {}
    entidad = _lista_skips(skips.get("entidad"))
    termino = _lista_skips(skips.get("termino"))
    return {
        "ciclo_id": f"#{ciclo['id']}" if ciclo.get("id") is not None else "—",
        "ciclo_fecha": formato.fecha_hora(ciclo.get("started_at")),
        "columnas": [
            _columna_skip(_TITULOS_SKIP["entidad"], entidad),
            _columna_skip(_TITULOS_SKIP["termino"], termino),
        ],
        "json": {"entidad": entidad, "termino": termino},
    }


def _ingesta(ads: dict | None) -> dict | None:
    if not ads:
        return None
    filas = []
    for reporte in ads.get("reportes") or []:
        estado = reporte.get("ultimo_estado")
        edad = reporte.get("edad_dias")
        filas.append(
            {
                "perfil": _texto(reporte.get("profile_id")),
                "reporte": etiqueta(reporte.get("reporte"), "reporte_ads"),
                "ultimo_exito": formato.fecha_hora(reporte.get("ultimo_exito")),
                "metrica": reporte.get("metric_date") or "—",
                "edad": f"{edad} dias" if edad is not None else "—",
                "estado": etiqueta(estado, "estado_reporte") if estado else "—",
                "estado_clase": _clase_estado("done" if estado == "written" else estado),
            }
        )
    incidentes = []
    for incidente in ads.get("incidentes") or []:
        aviso = "entregado" if incidente.get("estado_entrega") == "sent" else "pendiente"
        texto = f"aviso {aviso} ({formato.numero(incidente.get('intentos'))} intentos)"
        if incidente.get("recuperado"):
            recuperacion = "entregado" if incidente.get("recuperacion_entregada") else "pendiente"
            texto = f"{texto} · aviso de recuperación {recuperacion}"
        texto = f"{texto} · desde el {formato.fecha_hora(incidente.get('abierto_desde'))}"
        incidentes.append(
            {
                "tipo": etiqueta(incidente.get("tipo"), "incidente_ingesta"),
                "perfil": incidente.get("profile_id"),
                "resto": texto,
            }
        )
    return {
        "fuente": etiqueta(ads.get("fuente"), "fuente_ingesta"),
        "filas": filas,
        "incidentes": incidentes,
    }


def _instante(fila: dict) -> str:
    return formato.fecha_hora(fila.get("finished_at") or fila.get("started_at"))


def _spapi(spapi: dict | None) -> list[dict] | None:
    if not spapi:
        return None
    filas = []
    for fuente, datos in spapi.items():
        ultima = datos.get("ultima")
        ok = ultima.get("ok") if ultima else None
        saltadas = ultima.get("rows_skipped") if ultima else None
        motivo = ultima.get("skip_reason") if ultima else None
        filas.append(
            {
                "fuente": etiqueta(fuente, "fuente_ingesta"),
                "estado": "ok" if ok is True else "fallo" if ok is False else "—",
                "estado_clase": "ok" if ok is True else "aviso" if ok is False else "",
                "ultima": f"#{ultima.get('id')} · {_instante(ultima)}"
                if ultima
                else "sin corridas",
                "filas": formato.numero(ultima.get("rows_written")) if ultima else "—",
                "saltadas": formato.numero(saltadas) if ultima else "—",
                "saltadas_aviso": bool(_entero(saltadas)),
                "motivo": (motivo or "—").replace("_", " ") if motivo else "—",
                "limite": f"#{datos['ultima_429'].get('id')} · {_instante(datos['ultima_429'])}"
                if datos.get("ultima_429")
                else "—",
                "falla": f"#{datos['ultimo_lwa'].get('id')} · {_instante(datos['ultimo_lwa'])}"
                if datos.get("ultimo_lwa")
                else "—",
            }
        )
    return filas


def _quota(quota: dict | None) -> list[dict] | None:
    if quota is None:
        return None
    filas = []
    for forma, datos in quota.items():
        fuente = datos.get("fuente")
        cap = datos.get("cap")
        used = _entero(datos.get("used"))
        if fuente == "fila_del_dia":
            clase = "chip ok"
        elif fuente in ("sin_clave", "config_rota") or fuente is None:
            clase = "chip alerta"
        else:
            clase = "chip"
        if cap is None:
            tope_texto = "— (configuración ilegible)" if fuente == "config_rota" else "— (sin tope)"
        else:
            tope_texto = formato.numero(cap)
        filas.append(
            {
                "tipo": etiqueta(forma, "kind"),
                "usados": formato.numero(datos.get("used")),
                "cap": cap,
                "tope_texto": tope_texto,
                "fuente": etiqueta(fuente, "fuente_quota"),
                "fuente_clase": clase,
                "saturada": cap is not None and used >= _entero(cap),
            }
        )
    return filas


def _precios(precios: dict | None) -> dict:
    if not precios:
        return {"vacio": True, "texto": "Sin datos de precios del día."}
    texto = (
        f"Hoy {precios.get('hoy')}"
        f" · evaluados {formato.numero(precios.get('evaluados'))}"
        f" · movidos {formato.numero(precios.get('movidos'))}"
        f" · en práctica {formato.numero(precios.get('sombra'))}"
        f" · no evaluados {formato.numero(_suma(precios.get('no_evaluados')))}"
        f" · frenados {formato.numero(_suma(precios.get('frenados')))}"
        f" · objetivo inalcanzable {formato.numero(precios.get('goal_inalcanzable'))}"
        f" · huerfanas pendientes: {formato.numero(precios.get('huerfanas'))}"
    )
    return {"vacio": False, "texto": texto}


def _ultimo(ciclo: dict) -> dict | None:
    if not ciclo:
        return None
    return {
        "id": ciclo.get("id"),
        "fecha": formato.fecha_hora(ciclo.get("started_at")),
        "decisiones": formato.numero(ciclo.get("decisions_count")),
        "aplicadas": formato.numero(ciclo.get("applied_count")),
        "telegram": _telegram_de(ciclo),
    }


def _propuesta(propuesta: dict | None) -> dict | None:
    if propuesta is None:
        return None
    return {
        "pendientes": formato.numero(propuesta.get("pendientes")),
        "fallo": propuesta.get("fallo"),
    }


def _plataforma(plataforma: str, datos: dict) -> dict:
    ciclo = datos.get("ultimo_ciclo") or {}
    historico = datos.get("historico_14d") or []
    ingesta = _ingesta(datos.get("ads_ingest"))
    spapi = _spapi(datos.get("spapi"))
    avisos = _avisos_campana(datos.get("avisos_campana"))
    propuesta = _propuesta(datos.get("avisos_propuesta"))
    ciclo_resumen = _resumen_ciclo(ciclo)
    historico_resumen = _resumen_historico(historico)
    return {
        "id": plataforma,
        "nombre": etiqueta(plataforma, "plataforma"),
        "estado": ciclo.get("status"),
        "estado_label": etiqueta(ciclo.get("status"), "estado_ciclo")
        if ciclo.get("status")
        else None,
        "estado_clase": _clase_estado(ciclo.get("status")),
        "modo_label": etiqueta(ciclo.get("mode"), "modo") if ciclo.get("mode") else None,
        "datos": (
            f"Últimos datos de métricas: {datos.get('watermark') or '—'}"
            f" · Estructura sincronizada: {formato.fecha_hora(datos.get('synced_at'))}"
        ),
        "checks": [
            {"label": "Ingesta Ads", **_resumen_ingesta(datos.get("ads_ingest"))},
            {"label": "SP-API", **_resumen_spapi(datos.get("spapi"))},
            {"label": "Último ciclo", **ciclo_resumen},
            {"label": "Últimos 14 ciclos", **historico_resumen},
            {"label": "Avisos de campaña", **_resumen_avisos(avisos["grupos"] if avisos else None)},
            {"label": "Avisos de propuesta", **_resumen_propuesta(datos.get("avisos_propuesta"))},
        ],
        "ingesta": ingesta,
        "spapi": spapi,
        "avisos": avisos,
        "ciclos": _ciclos(historico),
        "skips": _skips(datos.get("skips"), ciclo),
        "ultimo": _ultimo(ciclo),
        "propuesta": propuesta,
        "harvest": datos.get("harvest_destino"),
        "quota": _quota(datos.get("quota")),
        "precios": _precios(datos.get("precios")) if "precios" in datos else None,
    }


def vista_salud(plataformas: dict | None, jev: dict | None, activa: str | None) -> dict:
    """Vista completa de /salud: plataformas en orden de origen y la activa.

    Una plataforma nueva en los datos entra sola a tarjetas y pestanas. Si la
    plataforma pedida no existe, se abre la primera.
    """
    datos = plataformas or {}
    ids = [str(clave) for clave in datos]
    activa = str(activa) if activa is not None else None
    if activa not in ids:
        activa = ids[0] if ids else None
    vistas = [_plataforma(clave, datos.get(clave) or {}) for clave in ids]
    activa_nombre = next((v["nombre"] for v in vistas if v["id"] == activa), None)
    return {
        "activa": activa,
        "activa_nombre": activa_nombre,
        "plataformas": vistas,
        "jev": jev,
    }
