"""Unico escritor de `envio_observation` y `envio_paquete_observation`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from app.envios.modelo import EnvioObservado, huella

# Se compara contra la ultima observacion y no contra la historia: volver a un
# estado anterior (A -> B -> A) es un hecho nuevo. El ON CONFLICT cubre el mismo
# envio dos veces en un instante, que no debe abortar el lote.
_SQL_ENVIO = """
INSERT INTO envio_observation
    (platform, envio_ref, pedidos, pack_ref, logistica_fuente, estado_fuente,
     subestado_fuente, pedido_creado_el, fuente_actualizado_el, despachar_antes_de,
     entrega_prometida_el, entrega_limite_el, entregado_el, devuelto_el,
     articulos, huella, observed_at, ingest_run_id)
SELECT
    %(platform)s::platform, %(envio_ref)s, %(pedidos)s, %(pack_ref)s,
    %(logistica_fuente)s, %(estado_fuente)s, %(subestado_fuente)s,
    %(pedido_creado_el)s, %(fuente_actualizado_el)s, %(despachar_antes_de)s,
    %(entrega_prometida_el)s, %(entrega_limite_el)s, %(entregado_el)s,
    %(devuelto_el)s, %(articulos)s, %(huella)s, %(observed_at)s, %(run_id)s
WHERE NOT EXISTS (
    SELECT 1 FROM v_envio_actual
     WHERE platform = %(platform)s::platform
       AND envio_ref = %(envio_ref)s
       AND huella = %(huella)s)
ON CONFLICT (platform, envio_ref, observed_at) DO NOTHING
RETURNING id
"""

_SQL_PAQUETE = """
INSERT INTO envio_paquete_observation
    (envio_obs_id, paquete_ref, transportista, servicio, guia, despachado_el,
     estado_fuente, detalle_fuente)
VALUES
    (%(envio_obs_id)s, %(paquete_ref)s, %(transportista)s, %(servicio)s, %(guia)s,
     %(despachado_el)s, %(estado_fuente)s, %(detalle_fuente)s)
"""


def guardar(
    conn: psycopg.Connection,
    envios: Sequence[EnvioObservado],
    *,
    observed_at: datetime,
    run_id: int,
) -> tuple[int, int]:
    """Inserta los envios cuyos hechos cambiaron; devuelve (escritos, sin_cambio).

    El lote es atomico: un envio nunca queda sin sus paquetes. Si el llamador ya
    abrio una transaccion, aqui solo hay un savepoint y el commit es suyo.
    """
    escritos = 0
    with conn.transaction():
        for envio in envios:
            hechos = asdict(envio)
            paquetes = hechos.pop("paquetes")
            fila = conn.execute(
                _SQL_ENVIO,
                hechos
                | {
                    "pedidos": list(envio.pedidos),
                    "articulos": Jsonb(hechos["articulos"]),
                    "huella": huella(envio),
                    "observed_at": observed_at,
                    "run_id": run_id,
                },
            ).fetchone()
            if fila is None:
                continue
            escritos += 1
            for paquete in paquetes:
                conn.execute(_SQL_PAQUETE, paquete | {"envio_obs_id": fila[0]})
    return escritos, len(envios) - escritos
