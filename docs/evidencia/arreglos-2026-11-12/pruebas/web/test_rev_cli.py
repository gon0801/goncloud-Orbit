"""Revision web/CLI (solo lectura): `cycle --decided-at` acepta un reloj en el
futuro y el ciclo lo usa como reloj de liberacion de la cola de veto, del
cooldown y de la madurez de cortes. Estas pruebas FALLAN a proposito: cada
aserción describe lo que deberia pasar."""

from __future__ import annotations

import datetime as dt

from app import cli
from app import cycle as ciclo
from app.optimizer import windows

from tests.test_cli import FALSO_RESULTADO, _FakeConn


def test_cycle_rechaza_decided_at_en_el_futuro(monkeypatch):
    """Un operador que teclea `--decided-at` con fecha futura (o con el mes
    equivocado) no deberia poder correr un ciclo: el ciclo usa ese reloj para
    liberar la cola de veto (`libera_vencidos(ahora=decided_at)`,
    cycle.py:3533) y para el cooldown de 7 dias."""
    capturado: dict = {}

    def _fake(conn, **kwargs):
        capturado.update(kwargs)
        return FALSO_RESULTADO

    monkeypatch.setattr(ciclo, "corre_ciclo", _fake)
    monkeypatch.setattr(cli, "connect", lambda dsn: _FakeConn())
    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://x:y@127.0.0.1:5432/orbit")
    futuro = (dt.datetime.now(dt.UTC) + dt.timedelta(days=40)).isoformat()

    codigo = cli.main(["cycle", "--platform", "amazon_mx", "--decided-at", futuro])

    # Deberia: exit 2 (uso invalido) y el orquestador jamas llamado.
    assert "decided_at" not in capturado, (
        f"el CLI paso decided_at={capturado.get('decided_at')} al ciclo (exit {codigo}):"
        " con ese reloj se liberan cortes con veto pendiente, expiran vetos de 30 dias"
        " y se salta el cooldown"
    )


def test_madurez_de_cortes_con_reloj_futuro():
    """Regla 6: cortar exige 10 dias de maduracion. Con decided_at una semana
    adelante, la ventana de cortes termina en max(metric_date) - 3d: solo 3
    dias de maduracion real, y el trigger decision_madurez_corte (que compara
    contra decided_at) no lo ve."""
    hoy = dt.datetime.now(dt.UTC).date()
    ayer = hoy - dt.timedelta(days=1)  # ultimo dato real
    decided_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=7)
    fin = windows.fin_ventana_cortes(ayer, decided_at)
    maduracion_real = (hoy - fin).days
    assert maduracion_real >= windows.DIAS_MADUREZ_CORTES, (
        f"ventana de cortes termina {fin}: {maduracion_real} dias de maduracion real"
    )
