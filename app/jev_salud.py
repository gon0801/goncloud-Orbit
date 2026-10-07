"""Bloque `jev` de /salud (JEV ADS 02, S.4, partido de `jev_senales` en r3).

Solo lecturas baratas para la pantalla. Importa los tipos de corrida del
job (`CierreCorrida`, `_ajustes`); el job nunca importa este modulo, asi
que no hay ciclo.

S.5 suma aqui las lecturas de pantalla (fila Senal en cortes +
gasto sin venta): SOLO SELECT, conexion de lectura, cero HTTP.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.jev_lectura import (
    Ajustes,
    Apagado,
    ClaveBusqueda,
    Lectura,
    PantallaGasto,
    PlataformaAmazon,
    PropuestaEnVeto,
    SenalPropuesta,
    SenalVista,
    vista_de_dict,
)
from app.jev_libro import intenciones_del_dia
from app.jev_senales import CierreCorrida, _ajustes, _propuestas


@dataclass(frozen=True)
class SaludJev:
    interruptor: Ajustes | Apagado
    ultima_corrida: CierreCorrida | None
    ultima_inicio: datetime | None
    ultima_sin_cierre: bool
    llamadas_hoy: int
    pendientes_de_jev: int
    grupos_con_gasto: int
    grupos_probados: int
    motivos_sin_probar: dict
    ajena_impedida_por_abstencion: int
    avisos_sin_entrega: int
    fichas_por_vencer_14d: int

    def como_dict(self) -> dict:
        """El bloque `jev` de /salud, con datos planos."""
        encendido = isinstance(self.interruptor, Ajustes)
        ultima = self.ultima_corrida
        return {
            "interruptor": {
                "encendido": encendido,
                "tope_diario": self.interruptor.tope_diario if encendido else None,
                "min_clics": self.interruptor.min_clics if encendido else None,
                "avisos": self.interruptor.avisos if encendido else None,
                "motivo_apagado": None if encendido else self.interruptor.motivo,
            },
            "ultima_corrida": None
            if ultima is None
            else {
                "corrida_id": str(ultima.corrida_id) if ultima.corrida_id else None,
                "motivo": ultima.motivo,
                "unidades": ultima.unidades,
                "llamadas": ultima.llamadas,
                "fallos": ultima.fallos,
                "senales": ultima.senales_nuevas,
                "avisos": ultima.avisos_enviados,
            },
            "ultima_inicio": self.ultima_inicio.isoformat() if self.ultima_inicio else None,
            "ultima_sin_cierre": self.ultima_sin_cierre,
            "llamadas_hoy": self.llamadas_hoy,
            "pendientes_de_jev": self.pendientes_de_jev,
            "grupos_con_gasto": self.grupos_con_gasto,
            "grupos_probados": self.grupos_probados,
            "motivos_sin_probar": dict(self.motivos_sin_probar),
            "ajena_impedida_por_abstencion": self.ajena_impedida_por_abstencion,
            "avisos_sin_entrega": self.avisos_sin_entrega,
            "fichas_por_vencer_14d": self.fichas_por_vencer_14d,
        }


def salud(conn, *, ahora: datetime) -> SaludJev:
    """Bloque `jev` de /salud, con consultas baratas. Revienta si falta una
    tabla (base sin S.3): `_jev_de` lo convierte en None."""

    def cuenta(sql, *params):
        return conn.execute(sql, params or None).fetchone()[0]

    interruptor = _ajustes(conn)
    fin = conn.execute(
        "SELECT lote_id, cierre, resumen, at FROM jev_corrida"
        " WHERE evento = 'fin' ORDER BY at DESC LIMIT 1"
    ).fetchone()
    ultima = None
    fin_at = None
    if fin is not None:
        fin_at = fin[3]
        resumen = fin[2] or {}
        nums = {
            k: resumen.get(k, 0)
            for k in ("unidades", "llamadas", "fallos", "senales_nuevas", "avisos_enviados")
        }
        ultima = CierreCorrida(fin[0], fin[1], **nums)
    inicio = cuenta("SELECT max(at) FROM jev_corrida WHERE evento = 'inicio'")
    grupos = conn.execute(
        "SELECT ad_group_id, roster_probado, roster_prueba FROM (SELECT DISTINCT ON"
        " (ad_group_id) ad_group_id, roster_probado, roster_prueba FROM jev_senal"
        " ORDER BY ad_group_id, created_at DESC, id DESC) u"
    ).fetchall()
    probados = sum(1 for _, es_probado, _ in grupos if es_probado)
    motivos = Counter(
        motivo
        for _, es_probado, prueba in grupos
        if not es_probado
        for motivo in (prueba or {}).get("motivos", [])
    )
    return SaludJev(
        interruptor,
        ultima,
        inicio,
        inicio is not None and (fin_at is None or inicio > fin_at),
        intenciones_del_dia(conn, ahora),
        cuenta(
            "SELECT count(*) FROM jev_senal_vigente v JOIN jev_senal s ON s.id = v.senal_id"
            " WHERE v.vigente AND s.relevancia = 'no_evaluada'"
        ),
        cuenta("SELECT count(DISTINCT ad_group_id) FROM jev_senal WHERE gasto > 0"),
        probados,
        dict(motivos),
        cuenta(
            "SELECT count(*) FROM (SELECT DISTINCT ON (ad_group_id, termino_sha256) lectura,"
            " motivos_jev, productos_ok, evaluados FROM jev_senal"
            " ORDER BY ad_group_id, termino_sha256, created_at DESC, id DESC) u"
            " WHERE lectura = 'sin_lectura'"
            " AND motivos_jev && ARRAY['juicio_insuficiente', 'fallo_proveedor']"
            " AND cardinality(productos_ok) = 0 AND evaluados > 0"
        ),
        cuenta(
            "SELECT count(*) FROM jev_aviso a"
            " LEFT JOIN jev_aviso_entrega e ON e.aviso_id = a.id WHERE e.aviso_id IS NULL"
        ),
        cuenta(
            "SELECT count(*) FROM jev_ficha_version f"
            " LEFT JOIN jev_ficha_revocacion r ON r.ficha_version_id = f.id"
            " WHERE r.id IS NULL AND f.revisar_antes_de <= %s",
            ahora + timedelta(days=14),
        ),
    )


# --- S.5: lecturas de pantalla (fila Senal en cortes + gasto sin venta) --------

_COLUMNAS_SENAL = (
    "senal_id",
    "plataforma",
    "ad_group_id",
    "termino",
    "lectura",
    "motivos_lectura",
    "relevancia",
    "motivos_jev",
    "productos_ok",
    "evaluados",
    "miembros",
    "regla_version",
    "roster_probado",
    "roster_prueba",
    "ventana_inicio",
    "ventana_fin",
    "datos_hasta",
    "moneda",
    "clics",
    "gasto",
    "ordenes",
    "otros_que_venden",
    "otros_sin_dato",
    "historial",
    "valida_hasta",
    "created_at",
    "vigente",
)

_SQL_SENAL_BASE = (
    "SELECT s.id AS senal_id, s.plataforma::text AS plataforma, s.ad_group_id,"
    " s.termino, s.lectura, s.motivos_lectura, s.relevancia, s.motivos_jev,"
    " s.productos_ok, s.evaluados, s.miembros, s.regla_version,"
    " s.roster_probado, s.roster_prueba, s.ventana_inicio, s.ventana_fin,"
    " s.datos_hasta, s.moneda::text AS moneda, s.clics, s.gasto, s.ordenes,"
    " s.otros_que_venden, s.otros_sin_dato, s.historial,"
    " s.valida_hasta, s.created_at, v.vigente"
    " FROM jev_senal_vigente v JOIN jev_senal s ON s.id = v.senal_id"
)


def _fila_senal(fila) -> dict:
    """Normaliza fila psycopg (dict_row o tuple) al dict que lee
    `vista_de_dict` (misma idea que `_fila_dict` de `jev_vista`)."""
    return dict(fila) if isinstance(fila, dict) else dict(zip(_COLUMNAS_SENAL, fila, strict=True))


def _vistas_de_claves(conn, claves: set[ClaveBusqueda]) -> dict[ClaveBusqueda, SenalVista]:
    """La senal mas reciente de cada clave (vacia si el job no la sello)."""
    if not claves:
        return {}
    # Plazas por clave: psycopg no expande un solo %s en IN; los valores
    # siguen parametrizados (el f-string solo cuenta plazas).
    marcas = ",".join(["(%s,%s,%s)"] * len(claves))
    parametros = [
        valor for clave in claves for valor in (clave.plataforma, clave.ad_group_id, clave.termino)
    ]
    filas = conn.execute(
        _SQL_SENAL_BASE + f" WHERE (s.plataforma::text, s.ad_group_id, s.termino) IN ({marcas})",
        parametros,
    ).fetchall()
    return {vista.clave: vista for vista in (vista_de_dict(_fila_senal(f)) for f in filas)}


def de_propuestas(conn, decision_ids: Iterable[int]) -> dict[int, SenalPropuesta]:
    """GET /cortes, fila Senal (S.5): origen y destino de cada decision con
    sus senales. Resuelve origen y destino con la MISMA consulta que usa el
    job (`_propuestas` de `jev_senales`) y trae la senal mas reciente de cada
    clave. SOLO SELECT, conexion de lectura, cero HTTP, sin plantillas.

    Sin senal sellada, la cara es None (el job todavia no la sello). En
    harvest con destino ilegible, el destino es None y `destino_ilegible`
    es True. Una decision con varias filas en cola muestra la primera
    (orden del job: vence, id)."""
    pedidas = {int(decision) for decision in decision_ids}
    if not pedidas:
        return {}
    propuestas, _ilegibles = _propuestas(conn)
    elegidas: dict[int, PropuestaEnVeto] = {}
    for propuesta in propuestas:
        if propuesta.decision_id in pedidas and propuesta.decision_id not in elegidas:
            elegidas[propuesta.decision_id] = propuesta
    claves: set[ClaveBusqueda] = set()
    for propuesta in elegidas.values():
        claves.add(propuesta.origen)
        if propuesta.destino is not None:
            claves.add(propuesta.destino)
    vistas = _vistas_de_claves(conn, claves)
    return {
        decision_id: SenalPropuesta(
            vistas.get(propuesta.origen),
            vistas.get(propuesta.destino) if propuesta.destino is not None else None,
            propuesta.destino_ilegible,
        )
        for decision_id, propuesta in elegidas.items()
    }


def gasto_sin_venta(conn, *, plataforma: PlataformaAmazon) -> PantallaGasto:
    """GET /gasto-sin-venta (S.5): senales vigentes con gasto y cero ordenes,
    por gasto descendente, con totales por lectura. SOLO SELECT, conexion de
    lectura, cero HTTP, sin plantillas."""
    filas = conn.execute(
        _SQL_SENAL_BASE + " WHERE s.plataforma = %s AND v.vigente AND s.ordenes = 0 AND s.gasto > 0"
        " ORDER BY s.gasto DESC, s.id",
        (plataforma,),
    ).fetchall()
    vistas = tuple(vista_de_dict(_fila_senal(fila)) for fila in filas)
    totales: dict[Lectura, tuple[int, Decimal]] = {}
    for vista in vistas:
        aqui = vista.economia.aqui
        gasto = aqui.gasto if aqui is not None and aqui.gasto is not None else Decimal(0)
        cantidad, acumulado = totales.get(vista.lectura, (0, Decimal(0)))
        totales[vista.lectura] = (cantidad + 1, acumulado + gasto)
    # La conexion puede venir con dict_row (la fija una prueba): se lee por
    # nombre en ese caso y por posicion con tuplas (nota J5-r1).
    ultima = conn.execute(
        "SELECT max(at) AS calculado FROM jev_corrida WHERE evento = 'fin'"
    ).fetchone()
    calculado = ultima["calculado"] if isinstance(ultima, dict) else ultima[0]
    return PantallaGasto(plataforma, calculado, vistas, totales)
