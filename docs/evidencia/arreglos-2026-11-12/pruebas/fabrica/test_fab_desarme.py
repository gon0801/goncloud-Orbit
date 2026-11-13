"""Revision fabrica: pausar un lote interrumpido deja viva una campana conocida.

Escenario: el proceso muere (deploy, reinicio) despues de que Amazon devolvio
el campaignId y _guarda_ack lo hizo durable, pero antes del readback que sella
el paso. El paso queda 'planeado' CON external_id. Pausar (desarmar) solo lee
pasos 'applied'/'failed': esa campana sigue ENABLED y el lote dice desarmado.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_fabrica_campanas as t  # noqa: E402
from test_fabrica_migracion import _dsn_propia, db_fabrica  # noqa: E402

fc = t.fc


@t._skip_db
def test_desarmar_pausa_campana_planeada_con_external(monkeypatch):
    with db_fabrica("orbit_rev_fab_desarme") as conn:
        t._lote_applied_orden_real(conn, lote="LX1")
        # Como lo deja _mutar muerto a medias: lote planeado, la exact
        # verificada y la phrase con id de Amazon ya guardado (ack) pero sin
        # readback (sigue 'planeado').
        conn.execute("UPDATE fabrica_lote SET estado = 'planeado' WHERE lote = 'LX1'")
        conn.execute("DELETE FROM fabrica_lote_paso WHERE lote = 'LX1' AND orden = 2")
        conn.execute(
            "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
            " estado, external_id, ack) VALUES ('LX1', 2, 'category_phrase', 'campaign',"
            " '{}', 'planeado', 'c-phrase', '{}')"
        )
        conn.commit()
        dsn, dbname = _dsn_propia(conn), conn.info.dbname
        amazon = t._AmazonDesarme()
        t._frontera_mutacion(monkeypatch, conn, conn, amazon, stub_registrar=False)
        assert t._corre_desarmar(monkeypatch, amazon, lote="LX1") == 0
        import json

        pausadas = sorted(json.loads(p.content)["campaigns"][0]["campaignId"] for p in amazon.puts)
        conn2 = psycopg.connect(dsn, dbname=dbname)
        try:
            estado = conn2.execute("SELECT estado FROM fabrica_lote WHERE lote = 'LX1'").fetchone()
        finally:
            conn2.close()
        # El lote queda 'desarmado' ("Las campanas conocidas del lote estan
        # pausadas") aunque Amazon nunca recibio la pausa de c-phrase.
        assert estado == ("desarmado",)
        assert pausadas == ["c-1", "c-phrase"], (
            f"solo se pauso {pausadas}; c-phrase (id conocido) sigue ENABLED gastando"
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
