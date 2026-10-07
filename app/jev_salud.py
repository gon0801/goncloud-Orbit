"""Bloque `jev` de /salud (JEV ADS 02, S.4, partido de `jev_senales` en r3).

Solo lecturas baratas para la pantalla. Importa los tipos de corrida del
job (`CierreCorrida`, `_ajustes`); el job nunca importa este modulo, asi
que no hay ciclo. S.5 suma aqui las lecturas de pantalla.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.jev_lectura import Ajustes, Apagado
from app.jev_libro import intenciones_del_dia
from app.jev_senales import CierreCorrida, _ajustes


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
