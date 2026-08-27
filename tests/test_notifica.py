"""Tests del canal de avisos Telegram (`app.notifica`) — ORBIT 04, task 3.3.

DoD (plans/orbit-04.md 3.3; sellados 2 y 19; APPLY.md 10.2), un candado por
test (regla 9 en cada uno). CERO HTTP real: TODO contra `httpx.MockTransport`
(ni Telegram ni Amazon); `tests/conftest.py` aisla el canal por defecto.

1. BUILDERS PUROS: mensajes correctos (aviso con vence_el, digest con lo que
   existe, alerta de harvest) SIN secretos y SIN parse_mode (texto plano).
2. TOLERANTES (regla 3): clave ausente no se menciona, jamas un 0 inventado.
3. `_envia_texto`: 200 ok -> True; 500 / red / JSON raro -> False + WARNING
   (caplog) con el token JAMAS presente (scrub).
4. CANAL DESHABILITADO (sin dir/archivo, JSON invalido): los `notifica_*`
   devuelven True (no es fallo), NO generan NOTA ni warning; el logger.info
   de deshabilitado sale UNA vez por proceso.
5. INTEGRACION ciclo (el corazon del DoD, rojo honesto capturado): canal
   configurado pero envio FALLA -> el ciclo termina 'done' (NO lo tumba ni
   degrada) Y notes['telegram'] queda con la NOTA — el silencio del canal
   jamas es invisible (sellado 2). Con canal OK -> 'done' SIN nota.
6. AVISO AL ENCOLAR: un mensaje POR corte nuevo con el vencimiento (48h).
7. DIGEST: UN mensaje al final con el resumen del ciclo ejecutor.
8. ALERTA harvest failed: sale en el punto de fallo definitivo (junto a la
   reversa automatica); si el envio falla, la bandera viaja con la alerta
   hasta el resumen de liberacion (el ciclo la convierte en NOTA).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from test_apply_harvest import (
    TERMINO,
    _aplicador,
    _decision_harvest,
    _encola_fila,
    _handler_harvest,
    _libera_fila,
    _semilla,
)
from test_cycle import DECIDED_AT, _siembra_maestra
from test_cycle_apply import _db_temporal
from test_schema import _postgres_obligatorio_ausente

from app import notifica
from app.api_common import _parse_notes
from app.apply_cola import fila_cola, libera_vencidos
from app.apply_harvest import MOTIVO_FALLO_KEYWORD, aplica_harvest
from app.cycle import corre_ciclo

FAKE_BOT_TOKEN = "7700000001:AAF-fake-token-XYZ"
FAKE_CHAT_ID = "555001"
OWNER = "test-host:notif"

VENCE = DECIDED_AT + dt.timedelta(hours=48)  # ventana de veto sellada (48h)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# ---------------------------------------------------------------------------
# Canal falso: telegram.json en tmp + MockTransport inyectado (patron repo)
# ---------------------------------------------------------------------------


def _handler_telegram(*, status: int = 200, json_valido: bool = True, tumbar: bool = False):
    """Handler MockTransport de api.telegram.org: captura cada mensaje como
    {"path": ..., "chat_id": ..., "text": ...}. `status` controla el HTTP
    (500 = fallo del canal), `json_valido`=False responde un cuerpo no-JSON
    (JSON raro) y `tumbar` rompe la red ECOANDO la URL (el token vive en el
    path: es el caso real del scrub)."""
    mensajes: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        mensajes.append({"path": request.url.path, **json.loads(request.content)})
        if tumbar:
            raise httpx.ConnectError(f"failed to connect to {request.url}")
        if not json_valido:
            return httpx.Response(200, text="<<respuesta no json>>")
        return httpx.Response(status, json={"ok": status == 200, "result": {"message_id": 1}})

    return handler, mensajes


@contextmanager
def _canal(tmp_path, monkeypatch, *, status: int = 200, json_valido: bool = True, tumbar=False):
    """Canal CONFIGURADO (telegram.json falso) + `_transporte_test` mockeado;
    yield la lista de mensajes capturados. Restaura el cache al salir (el
    autouse del conftest vuelve a deshabilitar el canal)."""
    d = tmp_path / "secrets"
    d.mkdir(exist_ok=True)
    (d / "telegram.json").write_text(
        json.dumps({"bot_token": FAKE_BOT_TOKEN, "chat_id": FAKE_CHAT_ID}), encoding="utf-8"
    )
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))
    handler, mensajes = _handler_telegram(status=status, json_valido=json_valido, tumbar=tumbar)
    monkeypatch.setattr(notifica, "_transporte_test", httpx.MockTransport(handler))
    notifica._reset()
    yield mensajes
    notifica._reset()


@pytest.fixture
def canal_ok(tmp_path, monkeypatch):
    with _canal(tmp_path, monkeypatch) as mensajes:
        yield mensajes


@pytest.fixture
def canal_fail(tmp_path, monkeypatch):
    with _canal(tmp_path, monkeypatch, status=500) as mensajes:
        yield mensajes


def _corre(conn):
    return corre_ciclo(
        conn, platform="amazon_us", owner=OWNER, decided_at=DECIDED_AT, heartbeat_cada=1
    )


def _alerta():
    """AlertaHarvest de prueba (duck typing: el builder no importa la clase)."""
    return SimpleNamespace(
        motivo=MOTIVO_FALLO_KEYWORD,
        decision_id=42,
        search_term=TERMINO,
        plataforma="amazon_us",
        job_id=7,
        detalle="reversa: ok | fallo http 400",
    )


# ---------------------------------------------------------------------------
# 1-2. Builders puros: contenido y tolerancia (regla 3)
# ---------------------------------------------------------------------------


def test_aviso_corte_encolado_contenido_y_vencimiento():
    """El aviso lleva plataforma, familia de efecto, kind, termino, modo y el
    VENCIMIENTO ISO (la ventana 48h es el dato que importa al dueno)."""
    texto = notifica.aviso_corte_encolado(
        notifica.CorteEncolado(
            platform="amazon_mx",
            kind="harvest",
            search_term="nogal cream",
            vence_el=VENCE,
            modo="live",
        )
    )
    assert texto.startswith("[Orbit] corte encolado")
    assert "amazon_mx" in texto
    assert "kind: harvest (familia term_cut)" in texto
    assert "search_term: nogal cream" in texto
    assert "modo: live" in texto
    assert VENCE.isoformat() in texto, "el vencimiento de la ventana de veto viaja en el aviso"


def test_aviso_corte_pause_sin_termino_no_inventa():
    """Regla 3: un pause no tiene search_term — la linea NO aparece (jamas un
    'None' serializado como si fuera dato)."""
    texto = notifica.aviso_corte_encolado(
        notifica.CorteEncolado(
            platform="amazon_us", kind="pause", search_term=None, vence_el=VENCE, modo="shadow"
        )
    )
    assert "search_term" not in texto
    assert "None" not in texto
    assert "kind: pause (familia entity_cut)" in texto


def test_digest_ciclo_contenido():
    resumen = {
        "cycle_id": 42,
        "plataforma": "amazon_us",
        "status": "degraded",
        "decisions_count": 7,
        "apply": {
            "bids_aplicados": 2,
            "bids_descartados": 1,
            "cortes_encolados": {"live": 1, "shadow": 2, "choques": 0},
            "cortes_liberados": {"aplicadas": 3, "fallidas": 1},
        },
    }
    texto = notifica.digest_ciclo(resumen)
    assert texto.startswith("[Orbit] digest ciclo #42 amazon_us — degraded")
    assert "decisiones: 7" in texto
    assert "bids aplicados: 2" in texto
    assert "bids fuera de cap hoy: 1" in texto
    assert "cortes encolados: live=1 shadow=2 choques=0" in texto
    assert "cortes liberados: aplicadas=3 fallidas=1" in texto


def test_digest_ciclo_tolerante_a_claves_ausentes():
    """Regla 3: lo que no existe NO se menciona (jamas un 0 inventado ni un
    KeyError por un notes['apply'] vacio o ausente)."""
    base = {"cycle_id": 1, "plataforma": "x", "status": "done", "decisions_count": 0}
    for resumen in (base | {"apply": {}}, base):
        texto = notifica.digest_ciclo(resumen)
        assert texto.startswith("[Orbit] digest ciclo #1 x — done")
        assert "bids" not in texto and "cortes" not in texto


def test_alerta_harvest_failed_contenido():
    texto = notifica.alerta_harvest_failed(_alerta())
    assert texto.startswith("[Orbit] ALERTA harvest failed")
    assert "motivo: fallo_keyword" in texto
    assert f"search_term: {TERMINO}" in texto
    assert "decision: 42" in texto
    assert "job: 7" in texto
    assert "reversa: ok | fallo http 400" in texto


# ---------------------------------------------------------------------------
# 3. _envia_texto contra MockTransport: ok / fallos con warning
# ---------------------------------------------------------------------------


def test_envia_texto_ok_envia_chat_id_y_texto(tmp_path, monkeypatch):
    with _canal(tmp_path, monkeypatch) as mensajes:
        assert notifica._envia_texto("hola mundo") is True
    (mensaje,) = mensajes
    assert mensaje["path"] == f"/bot{FAKE_BOT_TOKEN}/sendMessage"
    assert mensaje["chat_id"] == FAKE_CHAT_ID
    assert mensaje["text"] == "hola mundo"


def test_envia_texto_500_red_y_json_raro_fallan_con_warning(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="app.notifica")
    with _canal(tmp_path, monkeypatch, status=500):
        assert notifica._envia_texto("x") is False
    with _canal(tmp_path, monkeypatch, tumbar=True):
        assert notifica._envia_texto("x") is False
    with _canal(tmp_path, monkeypatch, json_valido=False):
        assert notifica._envia_texto("x") is False
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3, (
        "cada fallo del canal deja su warning (la NOTA en notes es la otra mitad "
        "de la visibilidad, sellado 2)"
    )


def test_envia_texto_warning_scrubbeado_sin_token(tmp_path, monkeypatch, caplog):
    """El token viaja en la URL: una excepcion que la ecoe (proxies caidos lo
    hacen) pasa por scrub — el token JAMAS aparece en el log."""
    from app.redaction import REDACTED

    caplog.set_level(logging.WARNING, logger="app.notifica")
    with _canal(tmp_path, monkeypatch, tumbar=True):
        notifica._envia_texto("x")
    assert caplog.records, "el fallo dejo warning"
    for record in caplog.records:
        assert FAKE_BOT_TOKEN not in record.getMessage()
    assert any(REDACTED in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. Canal deshabilitado: no es fallo (True, sin NOTA, sin warning)
# ---------------------------------------------------------------------------


def test_canal_deshabilitado_sin_archivo_no_es_fallo(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.notifica")
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))  # dir SIN telegram.json
    notifica._reset()
    assert notifica.canal_activo() is False
    assert notifica.notifica_digest({"cycle_id": 1, "plataforma": "x", "status": "done"}) is True
    assert (
        notifica.notifica_encola(
            notifica.CorteEncolado(
                platform="amazon_us", kind="negative", search_term="t", vence_el=VENCE, modo="live"
            )
        )
        is True
    )
    assert notifica.notifica_harvest_failed(_alerta()) is True
    infos = [r for r in caplog.records if "deshabilitado" in r.getMessage()]
    assert len(infos) == 1, "el aviso de deshabilitado sale UNA vez por proceso"
    notifica.notifica_digest({"cycle_id": 2, "plataforma": "x", "status": "done"})
    assert len([r for r in caplog.records if "deshabilitado" in r.getMessage()]) == 1
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    notifica._reset()


def test_canal_deshabilitado_json_invalido(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.notifica")
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "telegram.json").write_text("{ no es json", encoding="utf-8")
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))
    notifica._reset()
    assert notifica.canal_activo() is False
    assert notifica.notifica_digest({"cycle_id": 1, "plataforma": "x", "status": "done"}) is True
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    notifica._reset()


def test_canal_deshabilitado_claves_vacias_o_faltantes(tmp_path, monkeypatch):
    """bot_token/chat_id vacios o ausentes = canal deshabilitado (patron de
    app.ads.config: claves extra toleradas, exigidas estrictas)."""
    for contenido in ('{"chat_id": "1"}', '{"bot_token": "", "chat_id": "1"}', "{}"):
        d = tmp_path / f"s{len(contenido)}"
        d.mkdir()
        (d / "telegram.json").write_text(contenido, encoding="utf-8")
        monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))
        notifica._reset()
        assert notifica.canal_activo() is False, contenido
    notifica._reset()


# ---------------------------------------------------------------------------
# 5-7. Integracion en el ciclo (DB temporal 0001+0002; Amazon intacta)
# ---------------------------------------------------------------------------


@_skip_db
def test_ciclo_canal_falla_termina_done_con_nota_telegram(canal_fail):
    """EL DoD (regla 9, rojo honesto capturado): canal configurado pero el
    envio FALLA -> el ciclo termina 'done' (un fallo de Telegram JAMAS lo
    tumba ni lo degrada) Y notes['telegram'] queda con la NOTA de lo que
    fallo — sin la nota el silencio del canal seria invisible (sellado 2)."""
    with _db_temporal("orbit_notif_fail") as (conn, _extra):
        _siembra_maestra(conn)  # shadow: 4 decisiones (bid + 3 cortes a la cola)
        res = _corre(conn)
        assert res.status == "done", "el fallo del canal no tumba ni degrada el ciclo"
        notas = json.loads(res.notes)
        assert notas["telegram"]["aviso_encola"].startswith("fallo:")
        assert notas["telegram"]["digest"].startswith("fallo:")
        # La NOTA queda PERSISTIDA en el envelope y sobrevive el parseo de Salud
        persistido = conn.execute(
            "SELECT notes FROM optimizer_cycle WHERE id = %s", (res.cycle_id,)
        ).fetchone()[0]
        assert _parse_notes(persistido)["telegram"] == notas["telegram"]


@_skip_db
def test_ciclo_canal_ok_avisos_por_corte_y_digest_unico_sin_nota(canal_ok):
    """Canal OK: UN aviso POR corte nuevo (con el vencimiento 48h y la familia
    de efecto) + UN digest al final; el ciclo 'done' SIN nota telegram."""
    with _db_temporal("orbit_notif_ok") as (conn, _extra):
        _siembra_maestra(conn)
        res = _corre(conn)
        assert res.status == "done"
        assert "telegram" not in json.loads(res.notes)
        textos = [m["text"] for m in canal_ok]
        avisos = [t for t in textos if t.startswith("[Orbit] corte encolado")]
        digests = [t for t in textos if t.startswith("[Orbit] digest")]
        assert len(avisos) == 3, "pause + negative + harvest encolados"
        assert len(digests) == 1, "UN digest por ciclo ejecutor"
        assert {t.split("kind: ")[1].split(" ")[0] for t in avisos} == {
            "pause",
            "negative",
            "harvest",
        }
        assert all(VENCE.isoformat() in t for t in avisos), "vencimiento en cada aviso"
        assert any("entity_cut" in t for t in avisos), "familia del pause"
        assert any("term_cut" in t for t in avisos), "familia de negative/harvest"
        assert f"#{res.cycle_id}" in digests[0]
        assert "done" in digests[0]
        assert all(m["chat_id"] == FAKE_CHAT_ID for m in canal_ok)


@_skip_db
def test_ciclo_sin_canal_sin_nota_ni_warning(caplog):
    """Canal deshabilitado (default del conftest): no es fallo — el ciclo
    corre, NO queda nota telegram y no hay warning del canal."""
    caplog.set_level(logging.WARNING, logger="app.notifica")
    with _db_temporal("orbit_notif_off") as (conn, _extra):
        _siembra_maestra(conn)
        res = _corre(conn)
    assert res.status == "done"
    assert "telegram" not in json.loads(res.notes)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# ---------------------------------------------------------------------------
# 8. Alerta de harvest failed: en el punto de fallo definitivo
# ---------------------------------------------------------------------------


@_skip_db
def test_alerta_harvest_failed_enviada_en_el_punto_de_fallo(canal_ok):
    """El fallo DEFINITIVO del harvest (>=400, con reversa automatica ya
    corrida) dispara la alerta por el canal AHI MISMO: el mensaje lleva
    termino y motivo, y la bandera envio_fallido queda en False."""
    with _db_temporal("orbit_notif_harv") as (conn, _extra):
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        q = _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        _libera_fila(conn, q)  # released: el hook reclama applying el mismo
        handler, _vistos = _handler_harvest(fallo_keyword_status=400)

        resultado = aplica_harvest(
            conn,
            _aplicador(conn, handler, ids["ciclo_ejec"]),
            fila_cola(conn, q),
            platform="amazon_us",
        )

        assert resultado.estado == "failed"
        assert resultado.alerta is not None
        assert resultado.alerta.envio_fallido is False, "el envio salio bien"
        alertas = [m["text"] for m in canal_ok if m["text"].startswith("[Orbit] ALERTA")]
        assert len(alertas) == 1
        assert TERMINO in alertas[0] and MOTIVO_FALLO_KEYWORD in alertas[0]


@_skip_db
def test_alerta_harvest_failed_envio_falla_bandera_y_propaga(canal_fail):
    """Envio de la alerta FALLA: la bandera envio_fallido viaja con la alerta
    hasta el resumen de liberacion (el ciclo la convierte en la NOTA
    notes['telegram'] — sellado 2)."""
    with _db_temporal("orbit_notif_harvf") as (conn, _extra):
        ids = _semilla(conn)
        dec = _decision_harvest(conn, ids["ciclo_dec"], ids["config"], ids["ag"])
        _encola_fila(conn, dec, ids["ag"], term=TERMINO)
        handler, _vistos = _handler_harvest(fallo_keyword_status=400)

        resumen = libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC),
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert resumen.fallidas == 1
        assert resumen.alertas, "la alerta ya no se cae en el camino a la superficie"
        assert resumen.alertas[0].envio_fallido is True


def test_fase_notifica_mapea_alerta_harvest_fallida_a_nota():
    """b1 de la review: el eslabon FINAL harvest-failed -> NOTA probado
    DIRECTO (cycle._fase_notifica, sin ciclo completo) — la bandera
    envio_fallido=True produce la clave notes['telegram']['harvest_failed']
    (regla 9: un typo en el mapeo pasaba verde); envio OK -> sin NOTA (el
    silencio del canal solo es invisible cuando TODO salio)."""
    from dataclasses import replace

    from app import cycle as ciclo
    from app.apply_harvest import AlertaHarvest

    alerta = AlertaHarvest(
        motivo="fallo_definitivo",
        decision_id=1,
        search_term=TERMINO,
        plataforma="amazon_us",
        job_id=7,
        detalle="500",
        envio_fallido=True,
    )
    notas = ciclo._fase_notifica(
        (),
        (alerta,),
        cycle_id=1,
        platform="amazon_us",
        status="done",
        decisions_count=0,
        notas_apply={},
    )
    assert set(notas) == {"harvest_failed"}
    assert "Telegram" in notas["harvest_failed"]

    ok = replace(alerta, envio_fallido=False)
    assert (
        ciclo._fase_notifica(
            (),
            (ok,),
            cycle_id=1,
            platform="amazon_us",
            status="done",
            decisions_count=0,
            notas_apply={},
        )
        == {}
    )
