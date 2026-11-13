"""Reproducciones de la revision del CICLO (solo lectura del codigo de app/).

Cada prueba FALLA por la asercion que describe la falla: la asercion dice
lo que deberia pasar; el codigo de hoy hace otra cosa. Reutiliza los bancos
de tests/test_cycle.py y tests/test_cycle_apply.py (DB temporal por prueba).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cycle_apply import (  # noqa: E402
    JOB_KEY,
    _agrega_kw2,
    _config_live_caps,
    _corre,
    _db_temporal,
    _fabrica_directa,
    _handler,
    _puts,
    _siembra_maestra,
    _skip_db,
)

from app import cycle as ciclo  # noqa: E402


def _handler_put_5xx(remoto: dict[str, str], *, guarda_antes_de_fallar: bool):
    """Mock de Ads: cada PUT de bid responde 500 (fallo ambiguo). Con
    `guarda_antes_de_fallar`, Amazon SI aplico el cambio aunque respondio
    500 (el caso que la reconciliacion existe para resolver)."""
    base, vistos = _handler(remoto)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            if guarda_antes_de_fallar:
                base(request)  # deja el remoto con lo escrito
            else:
                vistos.append(request)
            return httpx.Response(500, json={"code": "INTERNAL_ERROR"})
        return base(request)

    return handler, vistos


@_skip_db
def test_un_5xx_en_un_bid_frena_los_demas_bids_y_la_cola():
    """F1: un 500 de Amazon en UN bid aborta la fase de apply completa: el
    resto de los bids del ciclo nunca se intenta y la liberacion de cortes
    vencidos (libera_vencidos) no corre ese dia."""
    with _db_temporal("rev_cic_5xx") as (conn, _c):
        ids = _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        _agrega_kw2(conn, ids)
        handler, vistos = _handler_put_5xx({"9201": "1.00", "9203": "1.00"}, guarda_antes_de_fallar=False)
        res = _corre(conn, factory=_fabrica_directa(handler))

        bids = conn.execute(
            "SELECT count(*) FROM decision WHERE cycle_id = %s AND kind = 'bid'", (res.cycle_id,)
        ).fetchone()[0]
        intentadas = conn.execute(
            "SELECT count(DISTINCT a.decision_id) FROM apply_attempt a"
            " JOIN decision d ON d.id = a.decision_id WHERE d.cycle_id = %s AND d.kind = 'bid'",
            (res.cycle_id,),
        ).fetchone()[0]
        notas = json.loads(res.notes)
        print("status:", res.status, "| bids decididos:", bids, "| PUTs:", len(_puts(vistos)))
        print("bids con intento:", intentadas, "| notes.apply:", notas.get("apply"))
        assert bids == 2
        assert intentadas == bids, (
            f"un 5xx en un bid dejo {bids - intentadas} bid(s) del ciclo sin intentar"
            " (y la cola de cortes sin liberar)"
        )


@_skip_db
def test_ciclo_degradado_por_apply_bloquea_los_otros_horarios_del_dia(secrets_falsos):
    """F2: si la fase de apply falla (aqui: /v2/profiles sin perfil aceptado,
    igual que un timeout o un 5xx), el ciclo queda 'degraded' y la guarda
    del cron (motivo_de_salto) impide reintentar a las 10:40, 13:40 y 16:40.
    Ningun bid de ese dia se aplica y nadie vuelve a intentarlo."""
    with _db_temporal("rev_cic_degr") as (conn, _c):
        _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        handler, _vistos = _handler(perfiles="MX")  # fabrica falla: apply_error
        from test_cycle_apply import _fabrica_real_mock

        res = _corre(conn, factory=_fabrica_real_mock(handler))
        aplicados = conn.execute(
            "SELECT count(*) FROM decision_application da JOIN decision d ON d.id = da.decision_id"
            " WHERE d.cycle_id = %s",
            (res.cycle_id,),
        ).fetchone()[0]
        salto = ciclo.motivo_de_salto(conn, "amazon_us")
        print("status:", res.status, "| aplicados:", aplicados, "| cron siguiente:", salto)
        assert res.status == "degraded" and aplicados == 0
        assert salto is None, f"el siguiente horario del cron NO reintenta: {salto!r}"


@pytest.fixture
def secrets_falsos(monkeypatch):
    from test_cycle_apply import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN

    from app.ads.config import AdsCredentials

    monkeypatch.setattr(
        AdsCredentials,
        "from_secrets_dir",
        classmethod(
            lambda cls, _dir=None: AdsCredentials(
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
                refresh_token=FAKE_REFRESH_TOKEN,
            )
        ),
    )


@_skip_db
def test_running_huerfano_con_lock_vencido_bloquea_el_siguiente_horario():
    """F3: el proceso del ciclo de las 08:40 muere (deploy, OOM) y deja el
    envelope 'running'. Su lock vence a los 30 min (TTL), pero la guarda del
    cron respeta cualquier 'running' de menos de 3 h: el horario de las
    10:40 se salta aunque corre_ciclo SI ganaria el claim."""
    with _db_temporal("rev_cic_huerf") as (conn, _c):
        _siembra_maestra(conn, escalera="live")
        conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform, started_at)"
            " VALUES ('ads_optimizer', 'live', 'amazon_us', now() - interval '2 hours')"
        )
        conn.execute(
            "INSERT INTO ads_optimizer_lock (job_key, owner, claimed_at, heartbeat_at)"
            " VALUES (%s, 'muerto:1', now() - interval '2 hours', now() - interval '2 hours')",
            (JOB_KEY,),
        )
        lock_vencido = conn.execute(
            "SELECT heartbeat_at + make_interval(secs => ttl_seconds) <= now()"
            " FROM ads_optimizer_lock WHERE job_key = %s",
            (JOB_KEY,),
        ).fetchone()[0]
        salto = ciclo.motivo_de_salto(conn, "amazon_us")
        print("lock vencido:", lock_vencido, "| cron 10:40:", salto)
        assert lock_vencido is True
        assert salto is None, f"el horario siguiente se salta con el lock ya vencido: {salto!r}"


@_skip_db
def test_reconciliacion_corre_despues_de_decidir():
    """F4: CONTEXTO dice 'Reconciliacion al inicio del ciclo contra Amazon
    VIVO'. El ciclo decide primero (TX2) y reconcilia despues (fase de
    apply). Un bid que Amazon SI aplico pero respondio 500 deja la hoja
    'bid_en_vuelo' en el ciclo siguiente, y ESE MISMO ciclo, minutos
    despues, lo confirma: la hoja pierde el dia con dato ya resuelto."""
    with _db_temporal("rev_cic_recon") as (conn, _c):
        _siembra_maestra(conn, escalera="live")
        _config_live_caps(conn)
        handler, _v = _handler_put_5xx({"9201": "1.00"}, guarda_antes_de_fallar=True)
        res1 = _corre(conn, factory=_fabrica_directa(handler))
        # ciclo siguiente (a mano, como --forzar o el dia siguiente)
        handler2, _v2 = _handler({"9201": "0.88"})
        res2 = _corre(conn, factory=_fabrica_directa(handler2))
        n2 = json.loads(res2.notes)
        en_vuelo = n2["skips"]["entidad"].get("bid_en_vuelo")
        confirmados = n2["apply"]["bids_reconciliados"]["confirmados"]
        print("ciclo1:", res1.status, "| ciclo2 skips bid_en_vuelo:", en_vuelo,
              "| ciclo2 reconciliados confirmados:", confirmados)
        assert not (en_vuelo and confirmados), (
            "el mismo ciclo salta la hoja por 'bid en vuelo' y luego confirma ese bid"
        )


def test_piso_aprendido_se_cruza_por_redondeo_de_amazon():
    """F5 (observado en produccion, hojas 4934/5336/4924): tras un regreso
    por desplome desde 0.29, el piso aprendido es 0.29. El recorte siguiente
    calcula 0.33 x 0.88 = 0.2904, que es > 0.29 y pasa el clamp; Amazon lo
    redondea a 0.29: el MISMO bid donde la hoja ya perdio su trafico. Cinco
    dias despues, otro regreso. Vaiven cada ~5 dias."""
    from decimal import Decimal

    from app.ads.write import _bid_payload
    from app.optimizer.caso import BidVigente
    from app.optimizer.politica import Mover, _paso

    bid = BidVigente(valor=Decimal("0.3300"), moneda="USD", piso=Decimal("0.10"), techo=Decimal("2.50"))
    veredicto = _paso(bid, Decimal("-0.12"), "grupo_sangra_vendedora", "ad_group", Decimal("0.2900"))
    enviado = _bid_payload(veredicto.bid_nuevo) if isinstance(veredicto, Mover) else None
    print("veredicto:", veredicto, "| lo que recibe Amazon:", enviado)
    assert not (isinstance(veredicto, Mover) and Decimal(enviado) <= Decimal("0.29")), (
        f"el recorte manda {enviado} a Amazon: igual o debajo del piso aprendido 0.29"
    )
