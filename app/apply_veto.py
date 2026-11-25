"""Veto del dueno sobre el EFECTO de un corte de `apply_queue` (ARREGLOS 05 E).

El veto es de la clave de efecto `(platform, ad_entity_id, search_term)`, no de
una sola fila: tras la migracion 0098 una practica vencida y un corte real
pueden convivir, y vetar la practica debe detener ese corte real. La ruta HTTP
(`app/api_write.veto`) es el adaptador; este modulo posee la transaccion y el
vocabulario de rechazo. No hace commit: el llamador decide cuando sellar.
"""

from __future__ import annotations

from typing import Any

import psycopg

from app.db import cursor_dict


class VetoNoEncontrado(LookupError):
    """La fila pedida no existe."""


class VetoNoVetable(RuntimeError):
    """La fila o su clave no admiten veto: terminal o en vuelo."""


# La clave se identifica por `search_term` porque 0002 garantiza que determina
# la familia (entity_cut = NULL, term_cut = NOT NULL).
_SQL_CLAVE_FILA = """
SELECT platform::text AS platform, ad_entity_id, search_term
  FROM apply_queue WHERE id = %s
"""

_SQL_CANDADO = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"

_SQL_FILA_VETO = """
SELECT platform::text AS platform, ad_entity_id, search_term, estado
  FROM apply_queue WHERE id = %s
"""

_SQL_CLAVE_EN_VUELO = """
SELECT id FROM apply_queue
 WHERE platform = %s::platform AND ad_entity_id = %s
   AND search_term IS NOT DISTINCT FROM %s AND estado = 'applying'
 LIMIT 1
"""

# El WHERE de estados ES la carrera contra el claim del motor
# (released -> applying) y contra un segundo veto; rowcount 0 = alguien mas
# movio la clave y se responde honesto. make_interval fija el vencimiento al
# reloj de la BASE (vence_el TSTZ).
_SQL_VETO = """
UPDATE apply_queue
   SET estado = 'vetoed', vence_el = now() + make_interval(days => %s),
       vetoed_at = now(), vetoed_by = %s
 WHERE platform = %s::platform AND ad_entity_id = %s
   AND search_term IS NOT DISTINCT FROM %s
   AND estado IN ('pending_veto', 'released')
RETURNING id, estado, vence_el, vetoed_at, vetoed_by
"""


def _texto_clave(platform: str, ad_entity_id: int, search_term: str | None) -> str:
    """Mismo texto de clave que el trigger `apply_queue_clave_vigente` de 0098."""
    termino = "<null>" if search_term is None else search_term
    return f"apply_queue:{platform}:{ad_entity_id}:{termino}"


def veta_efecto(
    conn: psycopg.Connection,
    queue_id: int,
    dias: int,
    actor: str,
) -> dict[str, Any]:
    """Veta TODAS las filas vetables de la clave de `queue_id`.

    `pending_veto` y `released` son vetables; `applying` es punto de no retorno
    y cualquier terminal es inmutable. Si el corte equivalente de la misma clave
    ya esta `applying`, no se toca ninguna fila. Devuelve el dict del JSON con
    `filas_vetadas`; no hace commit.
    """
    with cursor_dict(conn) as cur:
        clave_fila = cur.execute(_SQL_CLAVE_FILA, (queue_id,)).fetchone()
        if clave_fila is None:
            raise VetoNoEncontrado(queue_id)
        clave = (
            clave_fila["platform"],
            clave_fila["ad_entity_id"],
            clave_fila["search_term"],
        )
        # Serializa el veto con el ALTA de la misma clave: el trigger de 0098
        # toma el mismo candado, asi una decision calculada antes del veto no
        # puede insertarse despues de confirmado.
        cur.execute(_SQL_CANDADO, (_texto_clave(*clave),))
        fila = cur.execute(_SQL_FILA_VETO, (queue_id,)).fetchone()
        if fila is None:
            raise VetoNoEncontrado(queue_id)
        estado_fila = fila["estado"]
        if estado_fila == "applying":
            raise VetoNoVetable(
                f"fila {queue_id} en vuelo (applying), no vetable: SOLO"
                " applying es punto de no retorno (sellado 4)"
            )
        if estado_fila not in ("pending_veto", "released"):
            raise VetoNoVetable(f"fila {queue_id} en estado terminal {estado_fila}: no vetable")
        clave = (fila["platform"], fila["ad_entity_id"], fila["search_term"])
        en_vuelo = cur.execute(_SQL_CLAVE_EN_VUELO, clave).fetchone()
        if en_vuelo is not None:
            raise VetoNoVetable(
                f"la clave de la fila {queue_id} tiene el corte equivalente"
                f" {en_vuelo['id']} en vuelo (applying): no vetoable"
            )
        filas = cur.execute(_SQL_VETO, (dias, actor, *clave)).fetchall()
        objetivo = next((f for f in filas if f["id"] == queue_id), None)
        if objetivo is None:
            raise VetoNoVetable(f"fila {queue_id} cambio de estado durante el veto: no vetable")
        # Carrera: un claim pudo entrar entre la comprobacion y el UPDATE. Si
        # alguna fila de la clave quedo `applying`, el veto no es honesto y el
        # llamador debe revertir la transaccion.
        if cur.execute(_SQL_CLAVE_EN_VUELO, clave).fetchone() is not None:
            raise VetoNoVetable(
                f"la clave de la fila {queue_id} entro en vuelo (applying)"
                " durante el veto: no vetoable"
            )
    return {
        "id": objetivo["id"],
        "estado": objetivo["estado"],
        "vence_el": objetivo["vence_el"].isoformat(),
        "vetoed_at": objetivo["vetoed_at"].isoformat(),
        "vetoed_by": objetivo["vetoed_by"],
        "filas_vetadas": sorted(f["id"] for f in filas),
    }
