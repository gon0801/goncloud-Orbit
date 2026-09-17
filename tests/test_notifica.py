"""Tests del canal de avisos Telegram (`app.notifica`) — ORBIT 04, task 3.3.

DoD (plans/orbit-04.md 3.3; sellados 2 y 19; APPLY.md 10.2), un candado por
test (regla 9 en cada uno). APAGON 2026-09-16: CERO red (ni Telegram ni
Amazon); los senders suprimen al log local (`_envia_texto`) y devuelven
True; `tests/conftest.py` aisla el canal por defecto.

1. BUILDERS PUROS: mensajes correctos (aviso con vence_el, digest con lo que
   existe, alerta de harvest) SIN secretos y SIN parse_mode (texto plano).
2. TOLERANTES (regla 3): clave ausente no se menciona, jamas un 0 inventado.
3. `_envia_texto`: APAGON, siempre True con el texto en el log local
   ("aviso Telegram suprimido"); JAMAS sale a la red.
4. CANAL DESHABILITADO (sin dir/archivo, JSON invalido): los `notifica_*`
   devuelven True (no es fallo), NO generan NOTA ni warning; el logger.info
   de deshabilitado sale UNA vez por proceso.
5. INTEGRACION ciclo: APAGON, el ciclo termina 'done' (NO lo tumba ni
   degrada) SIN notes['telegram'] — el aviso queda en el log local, nunca
   en silencio invisible (sellado 2).
6. AVISO AL ENCOLAR: un mensaje POR corte nuevo con el vencimiento (48h).
7. DIGEST: UN mensaje al final con el resumen del ciclo ejecutor.
8. ALERTA harvest failed: se construye en el punto de fallo definitivo
   (junto a la reversa automatica) y queda en el log local; si el armado
   falla, la bandera viaja con la alerta hasta el resumen de liberacion
   (el ciclo la convierte en NOTA).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

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

OWNER = "test-host:notif"

VENCE = DECIDED_AT + dt.timedelta(hours=48)  # ventana de veto sellada (48h)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


# ---------------------------------------------------------------------------
# Canal falso: telegram.json en tmp + MockTransport inyectado (patron repo)
# ---------------------------------------------------------------------------


@contextmanager
def _canal(*_a, **_red_ignorada):
    """APAGON 2026-09-16: el canal no sale a la red ni lee telegram.json
    (andamiaje `_transporte_test`/MockTransport eliminado del modulo).
    Acepta e ignora los viejos parametros de red (status/json_valido/
    tumbar) para no reescribir cada llamada; yield una lista vacia (cero
    envios). Restaura el cache al salir."""
    notifica._reset()
    yield []
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
        "modo": "live",
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
    # El modo viaja en el encabezado: en shadow el dueno practica el veto y el
    # digest tambien sale — sin el modo no distingue un digest de shadow de
    # uno live (hallazgo medio de la review del lead sobre 3.3).
    assert texto.startswith("[Orbit] digest ciclo #42 amazon_us [live] — degraded")
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


def test_digest_sin_inertes_no_menciona_la_linea():
    """BIDS 01 1.3 (regla 3): sin la clave skips.entidad.entidad_inerte la
    linea de inertes NO aparece (ni con skips de otros motivos ni sin
    skips); jamas un 0 inventado ni KeyError."""
    base = {"cycle_id": 1, "plataforma": "amazon_us", "status": "done", "decisions_count": 0}
    casos = [
        base,
        base | {"skips": {}},
        base | {"skips": {"entidad": {"estado_no_enabled": 3}}},
        base | {"skips": {"termino": {"asin_like": 2}}},
    ]
    for resumen in casos:
        texto = notifica.digest_ciclo(resumen)
        assert "entidades sin trafico" not in texto


def test_digest_con_inertes_muestra_saltadas():
    """BIDS 01 1.3: con skips.entidad.entidad_inerte el digest trae la
    linea con el conteo entero."""
    resumen = {
        "cycle_id": 2,
        "plataforma": "amazon_us",
        "modo": "live",
        "status": "done",
        "decisions_count": 5,
        "skips": {"entidad": {"entidad_inerte": 57, "estado_no_enabled": 3}},
    }
    texto = notifica.digest_ciclo(resumen)
    assert "entidades sin trafico (saltadas): 57" in texto


def _resumen_target(aplicado=None, motivo=None, ancla=None, derivado="20") -> dict:
    """Resumen con bloque target (ORBIT 06 2.3 segunda vuelta): aplicado /
    motivo / derivado del ciclo actual + ancla del ultimo aviso emitido
    (ultimo_avisado; ausente = primera vez)."""
    target = {
        "procedencia": "margen_plataforma" if motivo is None else None,
        "motivo_abstencion": motivo,
        "margen_neto_pct": "40",
        "fraccion": "0.5",
        "target_derivado": derivado,
        "target_aplicado": aplicado,
    }
    resumen = {
        "cycle_id": 3,
        "plataforma": "amazon_us",
        "status": "done",
        "decisions_count": 5,
        "target": target,
    }
    if ancla is not None:
        resumen["target_ancla"] = ancla
    return resumen


def test_decide_aviso_target_logica_unica():
    """Rojo (h, D-2.3.14): sin ancla se avisa; |aplicado - ancla| >= 1
    avisa y avanza el ancla; < 1 no avisa y el ancla NO avanza; sin
    aplicado (abstencion) nunca avisa ni avanza."""
    from app.notifica import decide_aviso_target

    assert decide_aviso_target(Decimal("20"), None) == (True, "20")
    assert decide_aviso_target(Decimal("21.5"), "20") == (True, "21.5")
    assert decide_aviso_target(Decimal("20.4"), "20") == (False, "20")
    assert decide_aviso_target(None, "20") == (False, "20")
    assert decide_aviso_target(None, None) == (False, None)


def test_digest_target_acumulado_emite_y_repite_no():
    """Rojo (h, A8): aplicado 20.4 con ancla 20 -> silencio (el paso 0.5
    jamas dispara solo); con ancla 19.2 -> linea (acumulado 1.2)."""
    assert "target margen" not in notifica.digest_ciclo(
        _resumen_target(aplicado="20.4", ancla="20")
    )
    texto = notifica.digest_ciclo(_resumen_target(aplicado="20.4", ancla="19.2"))
    assert "target margen amazon_us: 19.2 -> 20.4" in texto


def test_digest_target_primera_vez_avisa():
    """Rojo (h): sin ancla (primera linea) el aplicado sale aunque no haya
    contra que comparar."""
    texto = notifica.digest_ciclo(_resumen_target(aplicado="29.5"))
    assert "target margen amazon_us: 29.5 (primer aviso)" in texto


def test_digest_target_sin_bloque_no_menciona():
    """Rojo (e): sin bloque target no hay linea (regla 3)."""
    assert "target margen" not in notifica.digest_ciclo(
        {"cycle_id": 3, "plataforma": "amazon_us", "status": "done", "decisions_count": 0}
    )


def test_digest_target_abstencion_con_motivo():
    """Rojo (e): motivo presente -> linea con motivo y etiqueta ES (el
    ancla no importa)."""
    texto = notifica.digest_ciclo(_resumen_target(motivo="cobertura_baja", ancla="20"))
    assert "abstencion cobertura_baja" in texto
    assert "cobertura" in texto


def test_digest_derivado_fuera_de_banda():
    """Rojo (h, D-2.3.10): derivado 45.2 con aplicado 45 -> linea con el
    crudo; derivado en banda -> silencio; sin derivado -> silencio."""
    texto = notifica.digest_ciclo(_resumen_target(aplicado="45", derivado="45.2", ancla="45"))
    assert "derivado 45.2 fuera de banda" in texto
    assert "aplicado 45" in texto
    assert "fuera de banda" not in notifica.digest_ciclo(
        _resumen_target(aplicado="20", derivado="20", ancla="20")
    )
    assert "fuera de banda" not in notifica.digest_ciclo(
        _resumen_target(motivo="sin_margen", ancla="20")
    )


def test_digest_contribucion_rango_invertido_no_usa_notacion_acotada():
    resumen = {
        "cycle_id": 12,
        "plataforma": "amazon_mx",
        "status": "done",
        "decisions_count": 1,
        "contribucion": notifica.ContribucionDigest(
            rango=notifica.RangoContribucion(
                moneda="MXN",
                entidades=12,
                sin_halo=Decimal("-500"),
                con_halo=Decimal("-900"),
                invertido=True,
            ),
            sin_dato=None,
            residual_tacos=None,
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "rango_invertido" in texto
    assert " .. " not in texto.split("contribucion pre-cargos")[1]


def test_digest_contribucion_rango_con_denominador():
    resumen = {
        "cycle_id": 13,
        "plataforma": "amazon_mx",
        "status": "done",
        "decisions_count": 1,
        "contribucion": notifica.ContribucionDigest(
            rango=notifica.RangoContribucion(
                moneda="MXN",
                entidades=108,
                sin_halo=Decimal("1200.50"),
                con_halo=Decimal("3400.75"),
                invertido=False,
                entidades_maduras=4998,
            ),
            sin_dato=None,
            residual_tacos=None,
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "108 entidades de 4998 entidades maduras" in texto


def test_digest_contribucion_multilisting_marcado():
    """Sello 1.5 (enmienda D1.bis): si alguna entidad publicada uso el precio
    MENOR de un producto multilisting, la linea del digest lo declara."""
    resumen = {
        "cycle_id": 15,
        "plataforma": "amazon_us",
        "status": "done",
        "decisions_count": 1,
        "contribucion": notifica.ContribucionDigest(
            rango=notifica.RangoContribucion(
                moneda="USD",
                entidades=42,
                sin_halo=Decimal("100"),
                con_halo=Decimal("200"),
                precio_min_multilisting=True,
            ),
            sin_dato=None,
            residual_tacos=None,
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "precio min multilisting" in texto


def test_digest_contribucion_sin_multilisting_no_lo_menciona():
    resumen = {
        "cycle_id": 16,
        "plataforma": "amazon_mx",
        "status": "done",
        "decisions_count": 1,
        "contribucion": notifica.ContribucionDigest(
            rango=notifica.RangoContribucion(
                moneda="MXN",
                entidades=12,
                sin_halo=Decimal("100"),
                con_halo=Decimal("200"),
            ),
            sin_dato=None,
            residual_tacos=None,
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "multilisting" not in texto


def test_arma_contribucion_digest_propaga_marca_multilisting():
    """La marca viaja desde SQL_CONTRIB_RANGO (6a columna: bool_or del flag)."""
    out = notifica._arma_contribucion_digest(
        [("USD", 42, Decimal("100"), Decimal("200"), False, True)],
        [],
        None,
    )
    assert out is not None and out.rango is not None
    assert out.rango.precio_min_multilisting is True


def test_digest_contribucion_lectura_fallida():
    resumen = {
        "cycle_id": 14,
        "plataforma": "amazon_mx",
        "status": "done",
        "decisions_count": 0,
        "contribucion": notifica.ContribucionDigest(
            rango=None,
            sin_dato=None,
            residual_tacos=None,
            lectura_fallida=True,
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "lectura no disponible" in texto


def test_digest_contribucion_rango_nunca_numero_unico():
    """ORBIT 06 1.3: el rango viaja como rango con la etiqueta sellada; jamas
    un numero unico ni la palabra 'margen' a secas."""
    resumen = {
        "cycle_id": 9,
        "plataforma": "amazon_mx",
        "modo": "shadow",
        "status": "done",
        "decisions_count": 3,
        "contribucion": notifica.ContribucionDigest(
            rango=notifica.RangoContribucion(
                moneda="MXN",
                entidades=108,
                sin_halo=Decimal("1200.50"),
                con_halo=Decimal("3400.75"),
            ),
            sin_dato=None,
            residual_tacos=None,
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "contribucion pre-cargos · no decisoria" in texto
    assert "1200.50 .. 3400.75 MXN (108 entidades)" in texto
    assert "margen" not in texto.lower()


def test_digest_contribucion_sin_dato_con_motivos():
    """Plataforma sin filas publicadas: sin dato con conteo y motivos desde
    cobertura — NUNCA cero ni omision silenciosa."""
    resumen = {
        "cycle_id": 10,
        "plataforma": "amazon_us",
        "modo": "live",
        "status": "done",
        "decisions_count": 0,
        "contribucion": notifica.ContribucionDigest(
            rango=None,
            sin_dato=notifica.SinDatoContribucion(
                total_ausentes=4998,
                por_motivo=(("catalogo_parcial", 4800), ("sin_fx", 198)),
            ),
            residual_tacos=None,
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "contribucion pre-cargos · no decisoria" in texto
    assert "sin dato (4998 entidades ausentes: catalogo_parcial 4800, sin_fx 198)" in texto


def test_digest_contribucion_residual_tacos_negativo():
    resumen = {
        "cycle_id": 15,
        "plataforma": "amazon_mx",
        "status": "done",
        "decisions_count": 1,
        "contribucion": notifica.ContribucionDigest(
            rango=notifica.RangoContribucion(
                moneda="MXN",
                entidades=5,
                sin_halo=Decimal("10"),
                con_halo=Decimal("20"),
            ),
            sin_dato=None,
            residual_tacos=notifica.ResidualTacos(monto=Decimal("-4.75")),
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "residual tacos campaign: -4.75 MXN" in texto


def test_digest_contribucion_residual_tacos():
    """Residual campaign sin contraparte del mes: linea si != 0."""
    resumen = {
        "cycle_id": 11,
        "plataforma": "amazon_mx",
        "status": "done",
        "decisions_count": 1,
        "contribucion": notifica.ContribucionDigest(
            rango=notifica.RangoContribucion(
                moneda="MXN",
                entidades=5,
                sin_halo=Decimal("10"),
                con_halo=Decimal("20"),
            ),
            sin_dato=None,
            residual_tacos=notifica.ResidualTacos(monto=Decimal("4.75")),
        ),
    }
    texto = notifica.digest_ciclo(resumen)
    assert "residual tacos campaign: 4.75 MXN" in texto


def test_digest_sin_contribucion_no_inventa():
    """Regla 3: sin clave contribucion no se menciona contrib ni margen."""
    texto = notifica.digest_ciclo(
        {"cycle_id": 1, "plataforma": "amazon_mx", "status": "done", "decisions_count": 0}
    )
    assert "contribucion" not in texto
    assert "margen" not in texto.lower()


def test_carga_contribucion_digest_rango_y_sin_dato():
    """Lectura de vistas: rango agregado o sin dato con motivos (sin red)."""

    class _Cur:
        def __init__(self, filas):
            self._filas = filas

        def fetchone(self):
            return self._filas[0] if len(self._filas) == 1 else None

        def fetchall(self):
            return self._filas

    class _Conn:
        def __init__(self, respuestas):
            self._respuestas = list(respuestas)

        def execute(self, _sql, _params=None):
            return _Cur(self._respuestas.pop(0))

    mx = notifica.carga_contribucion_digest(
        "amazon_mx",
        conn=_Conn(
            [
                [("MXN", 108, Decimal("100"), Decimal("200"), False, False)],
                [],
                [(Decimal("4.75"),)],
            ]
        ),
    )
    assert mx is not None
    assert mx.rango == notifica.RangoContribucion("MXN", 108, Decimal("100"), Decimal("200"))
    assert mx.sin_dato is None
    assert mx.residual_tacos == notifica.ResidualTacos(Decimal("4.75"))

    us = notifica.carga_contribucion_digest(
        "amazon_us",
        conn=_Conn(
            [
                [],
                [("catalogo_parcial", 4800), ("sin_fx", 198)],
                [(None,)],
            ]
        ),
    )
    assert us is not None
    assert us.rango is None
    assert us.sin_dato == notifica.SinDatoContribucion(
        4998, (("catalogo_parcial", 4800), ("sin_fx", 198))
    )


def test_carga_contribucion_digest_execute_falla(caplog):
    """Fail-silent real: execute que revienta -> lectura_fallida, no None."""

    class _Cur:
        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, _sql, _params=None):
            raise RuntimeError("server closed the connection unexpectedly")

    caplog.set_level(logging.WARNING, logger="app.notifica")
    out = notifica.carga_contribucion_digest("amazon_mx", conn=_Conn())
    assert out is not None
    assert out.lectura_fallida is True
    assert any("fallo leyendo contribucion" in r.message for r in caplog.records)


def test_notifica_digest_falla_lectura_muestra_lectura_no_disponible(monkeypatch):
    """Si carga devuelve lectura_fallida, el digest lo declara (no omite en silencio)."""

    def _fallida(_plataforma, *, conn=None):
        return notifica.ContribucionDigest(
            rango=None, sin_dato=None, residual_tacos=None, lectura_fallida=True
        )

    monkeypatch.setattr(notifica, "carga_contribucion_digest", _fallida)
    notifica._reset()
    ok = notifica.notifica_digest(
        {"cycle_id": 1, "plataforma": "amazon_mx", "status": "done", "decisions_count": 0}
    )
    assert ok is True  # APAGON 2026-09-16: suprimido al log, ver log/salud
    contrib = notifica.ContribucionDigest(
        rango=None, sin_dato=None, residual_tacos=None, lectura_fallida=True
    )
    texto = notifica.digest_ciclo(
        {
            "cycle_id": 1,
            "plataforma": "amazon_mx",
            "status": "done",
            "decisions_count": 0,
            "contribucion": contrib,
        }
    )
    assert "lectura no disponible" in texto
    notifica._reset()


def test_notifica_digest_falla_lectura_no_tumba(monkeypatch):
    """Fail-silent: lectura revienta en execute -> digest declara lectura no
    disponible y notifica_digest devuelve True si el canal manda."""

    class _Conn:
        def execute(self, _sql, _params=None):
            raise RuntimeError("lectura rota")

    real_carga = notifica.carga_contribucion_digest

    def _carga_fallida(plataforma, *, conn=None):
        return real_carga(plataforma, conn=_Conn())

    monkeypatch.setattr(notifica, "carga_contribucion_digest", _carga_fallida)
    notifica._reset()
    ok = notifica.notifica_digest(
        {"cycle_id": 1, "plataforma": "amazon_mx", "status": "done", "decisions_count": 0}
    )
    assert ok is True  # APAGON 2026-09-16: suprimido al log
    notifica._reset()


def test_carga_contribucion_digest_cobertura_parcial_con_rango():
    out = notifica._arma_contribucion_digest(
        [("MXN", 108, Decimal("100"), Decimal("200"), False, False)],
        [("catalogo_parcial", 4890)],
        None,
    )
    assert out is not None
    assert out.rango is not None
    assert out.rango.entidades_maduras == 4998


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
    """APAGON 2026-09-16: no envia red, log local, True, cero mensajes."""
    with _canal(tmp_path, monkeypatch) as mensajes:
        assert notifica._envia_texto("hola mundo") is True
    assert len(mensajes) == 0


def test_envia_texto_apagon_siempre_true_sin_red(tmp_path, monkeypatch):
    """APAGON 2026-09-16: suprimido siempre True, sin red en ningun caso
    (los viejos parametros de red se ignoran)."""
    with _canal(tmp_path, monkeypatch, status=500):
        assert notifica._envia_texto("x") is True
    with _canal(tmp_path, monkeypatch, tumbar=True):
        assert notifica._envia_texto("x") is True
    with _canal(tmp_path, monkeypatch, json_valido=False):
        assert notifica._envia_texto("x") is True


def test_envia_texto_suprimido_queda_en_log(tmp_path, monkeypatch, caplog):
    """Hallazgo revision final (puerta muda): el aviso suprimido NO es
    silencio invisible — el texto queda en el log local."""
    caplog.set_level(logging.INFO, logger="app.notifica")
    with _canal(tmp_path, monkeypatch):
        assert notifica._envia_texto("hola mundo") is True
    assert any(
        "aviso Telegram suprimido" in r.message and "hola mundo" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# 4. Canal deshabilitado: no es fallo (True, sin NOTA, sin warning)
# ---------------------------------------------------------------------------


def test_canal_deshabilitado_sin_archivo_no_es_fallo(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.notifica")
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))  # dir SIN telegram.json
    notifica._reset()
    assert notifica.canal_activo() is False
    assert (
        notifica.notifica_digest(
            {"cycle_id": 1, "plataforma": "x", "status": "done", "decisions_count": 0}
        )
        is True
    )
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
    notifica.notifica_digest(
        {"cycle_id": 2, "plataforma": "x", "status": "done", "decisions_count": 0}
    )
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
    assert (
        notifica.notifica_digest(
            {"cycle_id": 1, "plataforma": "x", "status": "done", "decisions_count": 0}
        )
        is True
    )
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
def test_ciclo_canal_falla_digest_contrib_deja_nota(canal_fail, monkeypatch):
    """Canal caido con seccion contrib: el ciclo sigue 'done' y la NOTA del
    digest persiste (camino nuevo ORBIT 06 1.3)."""
    contrib = notifica.ContribucionDigest(
        rango=notifica.RangoContribucion(
            moneda="MXN", entidades=1, sin_halo=Decimal("1"), con_halo=Decimal("2")
        ),
        sin_dato=None,
        residual_tacos=None,
    )

    def _fija(_plataforma, *, conn=None):
        return contrib

    monkeypatch.setattr(notifica, "carga_contribucion_digest", _fija)
    with _db_temporal("orbit_notif_contrib_fail") as (conn, _extra):
        _siembra_maestra(conn)
        res = _corre(conn)
        assert res.status == "done"
        assert "telegram" not in json.loads(res.notes)  # APAGON: suprimido sin nota
        assert len(canal_fail) == 0


@_skip_db
def test_ciclo_canal_falla_termina_done_con_nota_telegram(canal_fail):
    """EL DoD (regla 9, rojo honesto capturado): canal configurado pero el
    envio FALLA -> el ciclo termina 'done' (un fallo de Telegram JAMAS lo
    tumba ni lo degrada) Y notes['telegram'] queda con la NOTA de lo que
    fallo — sin la nota el silencio del canal seria invisible (sellado 2)."""
    with _db_temporal("orbit_notif_fail") as (conn, _extra):
        _siembra_maestra(conn)  # shadow: 4 decisiones (bid + 3 cortes a la cola)
        res = _corre(conn)
        assert res.status == "done", "el apagon no tumba ni degrada el ciclo"
        assert "telegram" not in json.loads(res.notes)  # APAGON: suprimido sin nota
        persistido = conn.execute(
            "SELECT notes FROM optimizer_cycle WHERE id = %s", (res.cycle_id,)
        ).fetchone()[0]
        assert "telegram" not in _parse_notes(persistido)


def test_fase_notifica_lattea_entre_envios(canal_ok):
    """Greptile P2 (PR #32): los envios son SINCRONOS y corren dentro del lock
    del ciclo — N avisos con el canal lento alargan el lease y abren la
    ventana del zombie (decision 11). _fase_notifica recibe el tick de
    heartbeat del ciclo y late UNA vez por mensaje enviado."""
    from app import cycle as ciclo

    latidos = []
    avisos = tuple(
        notifica.CorteEncolado(
            platform="amazon_us",
            kind=kind,
            search_term=None,
            vence_el=VENCE,
            modo="shadow",
        )
        for kind in ("pause", "negative", "harvest")
    )
    notas = ciclo._fase_notifica(
        avisos,
        (),
        cycle_id=1,
        platform="amazon_us",
        modo="shadow",
        status="done",
        decisions_count=0,
        notas_apply={},
        tick=lambda: latidos.append(1),
    )
    assert notas == {}
    assert len(canal_ok) == 0, "APAGON: cero envios"
    assert len(latidos) == 4, "3 avisos + 1 digest intentados (log local)"


@_skip_db
def test_ciclo_canal_ok_avisos_por_corte_y_digest_unico_sin_nota(canal_ok):
    """Canal OK: UN aviso POR corte nuevo (con el vencimiento 48h y la familia
    de efecto) + UN digest al final; el ciclo 'done' SIN nota telegram."""
    with _db_temporal("orbit_notif_ok") as (conn, _extra):
        _siembra_maestra(conn)
        res = _corre(conn)
        assert res.status == "done"
        assert "telegram" not in json.loads(res.notes)
        assert len(canal_ok) == 0  # APAGON
        # Builders siguen verificados en tests puros; aqui solo supresion.
        return


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
        assert resultado.alerta.envio_fallido is False, "apagon: True sin envio"
        assert len(canal_ok) == 0  # APAGON


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
        assert resumen.alertas[0].envio_fallido is False  # APAGON: True sin envio, sin nota


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
        modo="shadow",
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
            modo="shadow",
            status="done",
            decisions_count=0,
            notas_apply={},
        )
        == {}
    )


def test_fase_notifica_mapea_alerta_hermanas_a_nota_veraz():
    """r4: la alerta de hermanas pendientes con envio fallido produce la
    clave notes['telegram']['harvest_hermanas'] (NO 'harvest_failed': el
    harvest quedo aplicado) y su texto no dice failed. Regla 9: sin la
    rama por motivo, la nota mentiria el estado del harvest."""
    from dataclasses import replace as _replace

    from app import cycle as ciclo
    from app.apply_harvest import MOTIVO_HERMANAS_PENDIENTES, AlertaHarvest

    alerta = AlertaHarvest(
        motivo=MOTIVO_HERMANAS_PENDIENTES,
        decision_id=1,
        search_term=TERMINO,
        plataforma="amazon_us",
        job_id=7,
        detalle="2/3 hermanas aplicadas; pendientes: product_targeting: http_400",
        envio_fallido=True,
    )
    notas = ciclo._fase_notifica(
        (),
        (alerta,),
        cycle_id=1,
        platform="amazon_us",
        modo="shadow",
        status="done",
        decisions_count=0,
        notas_apply={},
    )
    assert set(notas) == {"harvest_hermanas"}, notas
    assert "failed" not in notas["harvest_hermanas"].lower()
    assert "aplicado" in notas["harvest_hermanas"]
    ok = _replace(alerta, envio_fallido=False)
    assert (
        ciclo._fase_notifica(
            (),
            (ok,),
            cycle_id=1,
            platform="amazon_us",
            modo="shadow",
            status="done",
            decisions_count=0,
            notas_apply={},
        )
        == {}
    )


# ---------------------------------------------------------------------------
# 9. Aviso de fallo SP-API (SP-API 01 A.5, ronda review): builder + sender
# ---------------------------------------------------------------------------


def test_aviso_spapi_fallo_contenido_y_matiz_ads():
    """Builder puro: fuente, plataforma y motivo; el matiz de Ads SOLO con
    motivo LWA."""
    texto = notifica.aviso_spapi_fallo(
        "spapi_orders", "amazon_mx", "lwa_fallido: LWA rechazo (401)"
    )
    assert "fuente: spapi_orders" in texto
    assert "plataforma: amazon_mx" in texto
    assert "lwa_fallido" in texto
    assert "El ciclo de Ads no se afecta" in texto
    sin_matiz = notifica.aviso_spapi_fallo(
        "spapi_pricing", "amazon_mx", "http_429: pricing status=429"
    )
    assert "El ciclo de Ads no se afecta" not in sin_matiz


def test_notifica_spapi_fallo_sin_canal_no_es_fallo():
    """Canal deshabilitado (default del conftest): True y cero HTTP. Mata la
    mutacion 'sin rama canal_activo'."""
    assert notifica.notifica_spapi_fallo("spapi_orders", "amazon_mx", "lwa_fallido: x") is True


def test_notifica_spapi_fallo_builder_roto_no_levanta(tmp_path, monkeypatch):
    """El JAMAS levanta cubre tambien el builder. APAGON: sin puerta que
    trague el builder roto -> warning con scrub + False, sin levantar.
    Mata la mutacion 'sin try/except en notifica_spapi_fallo'."""

    def builder_roto(*a, **k):
        raise RuntimeError("builder roto")

    with _canal(tmp_path, monkeypatch):
        monkeypatch.setattr(notifica, "aviso_spapi_fallo", builder_roto)
        assert notifica.notifica_spapi_fallo("spapi_orders", "amazon_mx", "lwa_fallido: x") is False


def test_notifica_spapi_fallo_envia_y_tumba(tmp_path, monkeypatch):
    """Sender APAGON: suprime al log (True) con canal OK o red rota, SIN
    levantar. Mata la mutacion 'sin try/except en notifica_spapi_fallo'."""
    with _canal(tmp_path, monkeypatch) as mensajes:
        assert notifica.notifica_spapi_fallo("spapi_orders", "amazon_mx", "lwa_fallido: x") is True
    assert len(mensajes) == 0  # APAGON
    texto_fallo = notifica.aviso_spapi_fallo("spapi_orders", "amazon_mx", "lwa_fallido: x")
    assert "fuente: spapi_orders" in texto_fallo
    with _canal(tmp_path, monkeypatch, tumbar=True):
        assert (
            notifica.notifica_spapi_fallo("spapi_orders", "amazon_mx", "lwa_fallido: x") is True
        )  # APAGON


def test_alerta_harvest_hermanas_contenido_veraz():
    """Builder puro (F2, A.3): harvest aplicado con pendientes — el texto
    jamas dice "failed" (seria mentira: la keyword vende) y lista rol con
    motivo. Regla 9: un copy del builder de failed mentiria en el evento
    de valor."""
    from types import SimpleNamespace as _NS

    alerta = _NS(
        motivo="hermanas_pendientes",
        decision_id=42,
        search_term=TERMINO,
        plataforma="amazon_us",
        job_id=7,
        detalle="2/3 hermanas aplicadas (auto_discovery, category_broad); pendientes: "
        "product_targeting: http_400",
    )
    texto = notifica.alerta_harvest_hermanas(alerta)
    assert texto.startswith("[Orbit] harvest aplicado con hermanas pendientes")
    assert "failed" not in texto.lower()
    assert "product_targeting: http_400" in texto
    assert f"search_term: {TERMINO}" in texto


def test_notifica_harvest_hermanas_envia_y_tumba(tmp_path, monkeypatch):
    """Sender APAGON (F2, A.3): suprime al log (True) con canal OK o red
    rota, SIN levantar."""
    from types import SimpleNamespace as _NS

    alerta = _NS(
        motivo="hermanas_pendientes",
        decision_id=42,
        search_term=TERMINO,
        plataforma="amazon_us",
        job_id=7,
        detalle="2/3 hermanas aplicadas (auto_discovery, category_broad); pendientes: "
        "product_targeting: http_400",
    )
    with _canal(tmp_path, monkeypatch) as mensajes:
        assert notifica.notifica_harvest_hermanas(alerta) is True
    assert len(mensajes) == 0  # APAGON
    assert "hermanas pendientes" in notifica.alerta_harvest_hermanas(alerta)
    with _canal(tmp_path, monkeypatch, tumbar=True):
        assert notifica.notifica_harvest_hermanas(alerta) is True  # APAGON


def _aviso_biblioteca(**cambios):
    """kwargs base del aviso de biblioteca (camino harvest)."""
    base = {
        "aplicado": "harvest",
        "plataforma": "amazon_us",
        "grupo_id": 3,
        "decision_id": 42,
        "job_id": 7,
        "texto": TERMINO,
        "motivo": "biblioteca_no_escrita",
        "detalle": "UndefinedTable",
    }
    base.update(cambios)
    return base


def test_alerta_biblioteca_no_escrita_contenido_veraz():
    """Builder puro (F2, A.4): el harvest/negative SI quedo aplicado y la
    biblioteca no aprendio el termino; jamas dice "failed". Regla 9: un
    copy del builder de failed mentiria en el evento de valor."""
    texto = notifica.alerta_biblioteca_no_escrita(**_aviso_biblioteca())
    assert texto.startswith("[Orbit] biblioteca no aprendio el termino (harvest aplicado)")
    assert "failed" not in texto.lower()
    for linea in (
        "plataforma: amazon_us",
        "aplicado: harvest",
        "grupo: 3",
        "decision: 42",
        "job: 7",
        f"search_term: {TERMINO}",
        "motivo: biblioteca_no_escrita",
        "detalle: UndefinedTable",
    ):
        assert linea in texto, linea
    sin_job = notifica.alerta_biblioteca_no_escrita(
        **_aviso_biblioteca(aplicado="negative", job_id=None)
    )
    assert "(negative aplicado)" in sin_job
    assert "\njob:" not in sin_job, "el camino negative no tiene job"


def test_notifica_biblioteca_no_escrita_envia_y_tumba(tmp_path, monkeypatch):
    """Sender (F2, A.4): con canal OK envia (True) el texto veraz; con red
    rota devuelve False SIN levantar (el rastro durable ya quedo)."""
    with _canal(tmp_path, monkeypatch) as mensajes:
        assert notifica.notifica_biblioteca_no_escrita(**_aviso_biblioteca()) is True
    assert len(mensajes) == 0  # APAGON
    assert "biblioteca no aprendio" in notifica.alerta_biblioteca_no_escrita(**_aviso_biblioteca())
    with _canal(tmp_path, monkeypatch, tumbar=True):
        assert notifica.notifica_biblioteca_no_escrita(**_aviso_biblioteca()) is True  # APAGON


def test_notifica_biblioteca_no_escrita_sin_canal_no_es_fallo():
    """Canal deshabilitado (default del conftest): True y cero HTTP. Mata
    la mutacion 'sin rama canal_activo'."""
    assert notifica.notifica_biblioteca_no_escrita(**_aviso_biblioteca()) is True


# ---------------------------------------------------------------------------
# FABRICA 02 (A.6): aviso en flanco de harvest de grupo sin destino
# ---------------------------------------------------------------------------


def _salto_grupo(**cambios):
    """SaltoDestinoGrupo base (campana de grupo con nombre)."""
    base = {
        "platform": "amazon_us",
        "grupo_id": 3,
        "campaign_ad_entity_id": 6102,
        "campaign_external": "6102",
        "nombre": "Category Phrase US",
        "rol": "category_phrase",
        "motivo": "destino_inconsistente",
    }
    base.update(cambios)
    return notifica.SaltoDestinoGrupo(**base)


def test_aviso_destino_grupo_inconsistente_texto_exacto():
    """Builder puro: encabezado, plataforma, grupo, campana con rol,
    motivo, linea de accion con el grupo y cierre veraz. Sin 'failed',
    sin fecha, sin acentos."""
    texto = notifica.aviso_destino_grupo(_salto_grupo())
    assert texto == "\n".join(
        [
            "[Orbit] ALERTA harvest de grupo sin destino",
            "plataforma: amazon_us",
            "grupo: 3",
            "campana: Category Phrase US (#6102, rol category_phrase)",
            "motivo: destino_inconsistente",
            "la terna del goal contradice la exacta del grupo: revisar con "
            "tools/harvest_excepcion.py --limpiar-terna --grupo 3 (dry-run primero)",
            "El harvest de esta campana queda saltado hasta corregirlo.",
        ]
    )
    assert "failed" not in texto


def test_aviso_destino_grupo_sin_destino_linea_distinta_y_sin_nombre():
    """Sin nombre, la campana se identifica por su external; la linea de
    accion es la del grupo sin exacta."""
    texto = notifica.aviso_destino_grupo(_salto_grupo(motivo="sin_destino_de_harvest", nombre=None))
    assert "campana: 6102 (#6102, rol category_phrase)" in texto
    assert "motivo: sin_destino_de_harvest" in texto
    assert (
        "el grupo no resuelve su exacta (rol category_exact ausente o de otra "
        "plataforma): revisar campana_grupo_rol" in texto
    )
    assert "El harvest de esta campana queda saltado hasta corregirlo." in texto
    assert "failed" not in texto
    assert "--limpiar-terna" not in texto, "cada motivo tiene SU linea de accion"


def test_aviso_destino_grupo_motivo_desconocido_sin_linea_de_accion():
    """Motivo fuera del vocabulario: el builder no revienta y no inventa
    linea de accion."""
    texto = notifica.aviso_destino_grupo(_salto_grupo(motivo="motivo_futuro"))
    assert "motivo: motivo_futuro" in texto
    assert "El harvest de esta campana queda saltado hasta corregirlo." in texto
    assert "--limpiar-terna" not in texto
    assert "campana_grupo_rol" not in texto


def test_notifica_destino_grupo_sin_canal_no_es_fallo():
    """Canal deshabilitado (default del conftest): True y cero HTTP. Mata
    la mutacion 'sin rama canal_activo'."""
    assert notifica.notifica_destino_grupo(_salto_grupo()) is True


def test_notifica_destino_grupo_builder_roto_no_levanta(tmp_path, monkeypatch):
    """El JAMAS levanta cubre tambien el builder. APAGON: builder roto
    -> False sin levantar."""

    def builder_roto(*a, **k):
        raise RuntimeError("builder roto")

    with _canal(tmp_path, monkeypatch):
        monkeypatch.setattr(notifica, "aviso_destino_grupo", builder_roto)
        assert notifica.notifica_destino_grupo(_salto_grupo()) is False


def test_notifica_destino_grupo_envia_y_tumba(tmp_path, monkeypatch):
    """Sender APAGON: suprime al log (True) con canal OK o HTTP 500, SIN
    levantar."""
    with _canal(tmp_path, monkeypatch) as mensajes:
        assert notifica.notifica_destino_grupo(_salto_grupo()) is True
    assert len(mensajes) == 0  # APAGON
    assert "ALERTA harvest de grupo sin destino" in notifica.aviso_destino_grupo(_salto_grupo())
    with _canal(tmp_path, monkeypatch, status=500):
        assert notifica.notifica_destino_grupo(_salto_grupo()) is True  # APAGON


def test_fase_notifica_mapea_salto_destino_a_nota_y_acumula(monkeypatch):
    """_fase_notifica con saltos de destino y el sender caido: la clave
    notes['telegram']['harvest_destino'] nombra campana y motivo; con dos
    saltos ACUMULA con ' | ', jamas pisa. Canal deshabilitado (default):
    el digest no genera nota y el dict queda exacto."""
    from app import cycle as ciclo

    monkeypatch.setattr(notifica, "notifica_destino_grupo", lambda *a, **k: False)
    notas = ciclo._fase_notifica(
        (),
        (),
        avisos_destino=(_salto_grupo(),),
        cycle_id=1,
        platform="amazon_us",
        modo="shadow",
        status="done",
        decisions_count=0,
        notas_apply={},
    )
    assert set(notas) == {"harvest_destino"}
    assert "Telegram" in notas["harvest_destino"]
    assert "#6102" in notas["harvest_destino"]
    assert "destino_inconsistente" in notas["harvest_destino"]

    notas2 = ciclo._fase_notifica(
        (),
        (),
        avisos_destino=(
            _salto_grupo(),
            _salto_grupo(campaign_ad_entity_id=6103, motivo="sin_destino_de_harvest"),
        ),
        cycle_id=1,
        platform="amazon_us",
        modo="shadow",
        status="done",
        decisions_count=0,
        notas_apply={},
    )
    assert set(notas2) == {"harvest_destino"}
    assert " | " in notas2["harvest_destino"]
    assert "#6102" in notas2["harvest_destino"] and "#6103" in notas2["harvest_destino"]


def test_fase_notifica_salto_destino_canal_caido_deja_nota(tmp_path, monkeypatch):
    """Cableado real APAGON: con el canal caido (HTTP 500, ignorado) el
    aviso de destino se suprime al log SIN nota."""
    from app import cycle as ciclo

    with _canal(tmp_path, monkeypatch, status=500):
        notas = ciclo._fase_notifica(
            (),
            (),
            avisos_destino=(_salto_grupo(),),
            cycle_id=1,
            platform="amazon_us",
            modo="shadow",
            status="done",
            decisions_count=0,
            notas_apply={},
        )
    assert notas == {}  # APAGON: suprimido sin nota


# ---------------------------------------------------------------------------
# 10. Vigilante SP-API: builders exactos + sender fail-silent
# ---------------------------------------------------------------------------


def _ventana_vigilante():
    desde = dt.datetime(2026, 9, 16, 4, 30, tzinfo=dt.UTC)
    hasta = dt.datetime(2026, 9, 16, 7, 30, tzinfo=dt.UTC)
    return desde, hasta


def test_aviso_spapi_silencio_texto_exacto():
    """Builder puro: IGUALDAD EXACTA con el texto del runbook (una linea
    por par, orden fuente/plataforma, `faltan N de 8`)."""
    desde, hasta = _ventana_vigilante()
    texto = notifica.aviso_spapi_silencio(
        [
            ("spapi_pricing", "amazon_mx"),
            ("spapi_pricing", "amazon_us"),
            ("spapi_inventario", "amazon_us"),
        ],
        desde,
        hasta,
    )
    assert texto == (
        "[Orbit] ALERTA SP-API sin corrida\n"
        "ventana: 2026-09-16 04:30 UTC → 2026-09-16 07:30 UTC\n"
        "faltan 3 de 8:\n"
        "- spapi_pricing / amazon_mx\n"
        "- spapi_pricing / amazon_us\n"
        "- spapi_inventario / amazon_us\n"
        "El cron de las 05:00 UTC no dejó estas corridas en ingest_run. "
        "Revisar crontab de gon, flock y el log spapi-diario.log."
    )
    # Un --desde con offset distinto se imprime en UTC: sin normalizar,
    # la etiqueta UTC mentiria (23:30-05:00 no es 23:30 UTC).
    cdmx = dt.timezone(dt.timedelta(hours=-5))
    texto_offset = notifica.aviso_spapi_silencio(
        [
            ("spapi_pricing", "amazon_mx"),
            ("spapi_pricing", "amazon_us"),
            ("spapi_inventario", "amazon_us"),
        ],
        desde.astimezone(cdmx),
        hasta,
    )
    assert texto_offset == texto


def test_aviso_spapi_silencio_respeta_orden_fuente_plataforma():
    """El builder respeta el orden del caller (el vigilante pasa el del
    catalogo: FUENTES_SPAPI x PLATAFORMAS_SPAPI — pricing va ANTES que
    inventario aunque el alfabeto diga lo contrario). El determinismo lo
    pinza `faltantes` + el test exacto; un shuffle en el builder cae en
    el exacto."""
    desde, hasta = _ventana_vigilante()
    texto = notifica.aviso_spapi_silencio(
        [("spapi_pricing", "amazon_mx"), ("spapi_inventario", "amazon_us")],
        desde,
        hasta,
    )
    lineas = texto.splitlines()
    assert lineas[3] == "- spapi_pricing / amazon_mx"
    assert lineas[4] == "- spapi_inventario / amazon_us"


def test_aviso_spapi_vigilante_ciego_texto_exacto_y_scrub():
    """Builder puro del aviso ciego: igualdad exacta + motivo con scrub
    (el DSN roto puede traer password)."""
    from app.redaction import REDACTED, register_secret

    secreto = "tok-secreto-simulado-vig-xyz"
    register_secret(secreto)
    texto = notifica.aviso_spapi_vigilante_ciego(f"connection failed: password={secreto} en el DSN")
    assert texto == (
        "[Orbit] ALERTA vigilante SP-API sin lectura\n"
        f"no pude leer ingest_run: connection failed: password={REDACTED} en el DSN\n"
        "No sé si el cron corrió. Revisar Postgres y el DSN de lectura."
    )


def test_notifica_spapi_silencio_sin_canal_no_es_fallo():
    """Canal deshabilitado (default del conftest): True y cero HTTP. Mata
    la mutacion 'sin rama canal_activo'."""
    desde, hasta = _ventana_vigilante()
    assert notifica.notifica_spapi_silencio([("spapi_orders", "amazon_mx")], desde, hasta) is True


def test_notifica_spapi_silencio_envia_y_tumba(tmp_path, monkeypatch):
    """Sender APAGON: suprime al log (True) con canal OK o red rota,
    SIN levantar. Igual contrato que notifica_spapi_fallo."""
    desde, hasta = _ventana_vigilante()
    with _canal(tmp_path, monkeypatch) as mensajes:
        assert (
            notifica.notifica_spapi_silencio([("spapi_orders", "amazon_mx")], desde, hasta) is True
        )
    assert len(mensajes) == 0  # APAGON
    texto_sil = notifica.aviso_spapi_silencio([("spapi_orders", "amazon_mx")], desde, hasta)
    assert "ALERTA SP-API sin corrida" in texto_sil
    with _canal(tmp_path, monkeypatch, tumbar=True):
        assert (
            notifica.notifica_spapi_silencio([("spapi_orders", "amazon_mx")], desde, hasta) is True
        )  # APAGON


def test_notifica_spapi_silencio_apagon_suprime_y_true(tmp_path, monkeypatch):
    """APAGON: sin red ni parametro transport; el sender suprime al log y
    devuelve True."""

    with _canal(tmp_path, monkeypatch):
        desde, hasta = _ventana_vigilante()
        assert (
            notifica.notifica_spapi_silencio([("spapi_orders", "amazon_mx")], desde, hasta) is True
        )


def test_notifica_spapi_silencio_builder_roto_no_levanta(tmp_path, monkeypatch):
    """El JAMAS levanta cubre tambien el builder. APAGON: builder roto
    -> False sin levantar."""

    def builder_roto(*a, **k):
        raise RuntimeError("builder roto")

    with _canal(tmp_path, monkeypatch):
        monkeypatch.setattr(notifica, "aviso_spapi_silencio", builder_roto)
        desde, hasta = _ventana_vigilante()
        assert (
            notifica.notifica_spapi_silencio([("spapi_orders", "amazon_mx")], desde, hasta) is False
        )
