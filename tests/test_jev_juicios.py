"""Tests de `app/jev_juicios.py`: transporte TypeSafe por par (JEV ADS 01, 1.3).

DoD que fijan (todo con HTTP FALSO; cero llamadas reales, cero claves):

- El wire lleva SOLO termino y ficha (sin grupo, campana, metricas ni otros
  productos), con modelo fijo `jev-1.13.0` y la pregunta Choice.
- La respuesta se valida estricta: modelo, IDs, categorias, probabilidades
  finitas que suman 1 y confidence en [0, 1].
- La clave del par cambia si cambia el orden de opciones o la version del
  contrato; y es estable con el mismo contrato.
- Timeout, respuesta invalida, redireccion y contexto excedido producen
  estados visibles SIN secreto en la salida. Falta de usage = costo
  desconocido.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.jev_ads import ClavePar, FichaVersion, HechoConFuente
from app.jev_juicios import (
    CONTRATO_VERSION,
    MODELO,
    PREGUNTA_ID,
    Contrato,
    clave_de,
    contrato_por_defecto,
    leer_api_key,
    pedir_juicio,
    request_sha256,
)

OBS = datetime(2026, 10, 4, tzinfo=UTC)


def _ficha(**extra) -> FichaVersion:
    base = dict(
        id=uuid.uuid4(),
        producto_id=7,
        plataforma="amazon_mx",
        listings=frozenset({3}),
        hechos=(HechoConFuente(texto="hecho", fuente="fuente"),),
        desconocidos=frozenset({"peso"}),
        aprobador="aprobador",
        observado_at=OBS,
        revisar_antes_de=OBS,
        sha256="d" * 64,
    )
    return FichaVersion(**{**base, **extra})


class _Respuesta:
    def __init__(self, status: int, cuerpo: dict):
        self.status_code = status
        self._cuerpo = cuerpo

    def json(self) -> dict:
        return self._cuerpo


class _Transporte:
    """HTTP falso: captura la llamada y devuelve lo configurado."""

    def __init__(
        self,
        respuesta: dict | None = None,
        error: Exception | None = None,
        status: int = 200,
    ):
        self.llamadas = 0
        self.ultima: tuple | None = None
        self._respuesta = respuesta
        self._error = error
        self._status = status

    def __call__(self, url: str, *, payload: dict, headers: dict, timeout_s: float):
        self.llamadas += 1
        self.ultima = (url, payload, headers, timeout_s)
        if self._error is not None:
            raise self._error
        return _Respuesta(self._status, self._respuesta or {})


RESPUESTA_OK = {
    "model": MODELO,
    "answers": {
        PREGUNTA_ID: {
            "type": "choice",
            "choice": "no_satisface",
            "probabilities": {
                "satisface": 0.10,
                "no_satisface": 0.80,
                "informacion_insuficiente": 0.10,
            },
            "confidence": 0.90,
        }
    },
    "usage": {"input_tokens": 296, "output_tokens": 20},
}


def _pedir(transporte, termino="soporte mesa", **kw):
    return pedir_juicio(
        termino,
        _ficha(),
        contrato_por_defecto(),
        transporte=transporte,
        api_key=kw.pop("api_key", "clave-de-prueba"),
        **kw,
    )


# ---------------------------------------------------------------------------
# El wire: solo termino y ficha, modelo fijo, pregunta Choice
# ---------------------------------------------------------------------------


def test_wire_lleva_solo_termino_y_ficha():
    transporte = _Transporte(RESPUESTA_OK)
    # Termino con MAYUSCULAS a proposito (B5-r2, B5b): una normalizacion
    # como .lower() cambia este valor y la asercion literal de abajo lo
    # detecta; con un termino ya minusculo el mutante seria invisible.
    resultado = _pedir(transporte, termino="Soporte MESA")
    assert hasattr(resultado, "juicio")
    url, payload, headers, timeout_s = transporte.ultima
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert payload["model"] == MODELO
    assert set(payload) == {"state", "model", "questions"}
    assert set(payload["state"]) == {"termino", "ficha"}
    # B5-r2 (B5b): el termino viaja LITERAL (DoD 1.3), no normalizado.
    assert payload["state"]["termino"] == "Soporte MESA"
    # Correccion VEREDICTO-B3-r1: SOLO hechos y desconocidos viajan al
    # proveedor; listings/observado_at/revisar_antes_de son internos de
    # Orbit (la identidad viaja por la ficha id en la clave y en el hash).
    ficha_wire = payload["state"]["ficha"]
    assert set(ficha_wire) == {"hechos", "desconocidos"}
    assert ficha_wire["hechos"] == [{"fuente": "fuente", "texto": "hecho"}]
    assert ficha_wire["desconocidos"] == ["peso"]
    pregunta = payload["questions"][PREGUNTA_ID]
    assert pregunta["type"] == "choice"
    assert tuple(pregunta["criteria"]) == (
        "satisface",
        "no_satisface",
        "informacion_insuficiente",
    )
    assert headers["Authorization"] == "Bearer clave-de-prueba"
    assert timeout_s == 30.0
    assert transporte.llamadas == 1


def test_juicio_valido_con_decimales_y_usage():
    resultado = _pedir(_Transporte(RESPUESTA_OK))
    juicio = resultado.juicio
    assert juicio.clave.ficha_version_id is not None
    assert juicio.relacion == "no_satisface"
    assert juicio.probabilidades["no_satisface"] == Decimal("0.80")
    assert juicio.confidence == Decimal("0.90")
    assert resultado.usage == {"input_tokens": 296, "output_tokens": 20}
    assert resultado.duracion_ms is not None


def test_clave_estable_y_request_hash_con_mismo_contrato():
    ficha = _ficha()
    contrato = contrato_por_defecto()
    a = clave_de("soporte mesa", ficha, contrato)
    b = clave_de("soporte mesa", ficha, contrato)
    assert a == b
    assert isinstance(a, ClavePar)
    otra_clave = clave_de("soporte mesas", ficha, contrato).termino_literal_sha256
    assert a.termino_literal_sha256 != otra_clave
    assert a.ficha_version_id == ficha.id
    assert request_sha256("soporte mesa", ficha, contrato) == request_sha256(
        "soporte mesa", ficha, contrato
    )


def test_request_sha256_valor_fijo_y_sensible_al_contenido():
    """B5-r2 (B5c): el hash del request se fija contra un valor LITERAL
    (un hash constante o un payload que pierda el termino no puede pasar)
    y cambia con el contenido que viaja por el wire: termino, hechos de la
    ficha y modelo del contrato. La identidad (id de ficha) y la version
    del contrato NO van en el payload wire (viajan en la clave): no cambian
    este hash por diseno."""
    ficha = _ficha(id=uuid.UUID("00000000-0000-0000-0000-00000000f1a1"))
    contrato = contrato_por_defecto()
    assert (
        request_sha256("soporte mesa", ficha, contrato)
        == "6ddbb70b1b5aaa4901c3a8e46a8f50d03859c68a09510bbeb460ebfe1ca9b1d9"
    )
    assert request_sha256("soporte mesas", ficha, contrato) != request_sha256(
        "soporte mesa", ficha, contrato
    )
    otra_ficha = _ficha(
        id=uuid.UUID("00000000-0000-0000-0000-00000000f1a1"),
        hechos=(HechoConFuente(texto="otro hecho", fuente="fuente"),),
    )
    assert request_sha256("soporte mesa", otra_ficha, contrato) != request_sha256(
        "soporte mesa", ficha, contrato
    )
    base = contrato_por_defecto()
    otro_modelo = Contrato(
        modelo="jev-9.9.9",
        opciones=base.opciones,
        criterios=base.criterios,
        instrucciones=base.instrucciones,
        version=base.version,
        max_bytes_termino=base.max_bytes_termino,
        max_bytes_ficha=base.max_bytes_ficha,
    )
    assert request_sha256("soporte mesa", ficha, otro_modelo) != request_sha256(
        "soporte mesa", ficha, contrato
    )


# ---------------------------------------------------------------------------
# La clave cambia con orden de opciones y version del contrato
# ---------------------------------------------------------------------------


def test_orden_de_opciones_cambia_la_clave():
    ficha = _ficha()
    base = contrato_por_defecto()
    invertido = Contrato(
        modelo=base.modelo,
        opciones=tuple(reversed(base.opciones)),
        criterios=tuple(reversed(base.criterios)),
        instrucciones=base.instrucciones,
        version=base.version,
        max_bytes_termino=base.max_bytes_termino,
        max_bytes_ficha=base.max_bytes_ficha,
    )
    assert clave_de("t", ficha, base) != clave_de("t", ficha, invertido)


def test_reordenar_solo_opciones_cambia_la_clave():
    """R16: el orden de `opciones` entra al hash por si mismo; la prueba de
    arriba invierte tambien `criterios`, que ya codifica el orden."""
    ficha = _ficha()
    base = contrato_por_defecto()
    solo_opciones = replace(base, opciones=tuple(reversed(base.opciones)))
    assert solo_opciones.criterios == base.criterios
    assert clave_de("t", ficha, base) != clave_de("t", ficha, solo_opciones)


def test_version_del_contrato_cambia_la_clave():
    ficha = _ficha()
    base = contrato_por_defecto()
    otra = Contrato(
        modelo=base.modelo,
        opciones=base.opciones,
        criterios=base.criterios,
        instrucciones=base.instrucciones,
        version="2",
        max_bytes_termino=base.max_bytes_termino,
        max_bytes_ficha=base.max_bytes_ficha,
    )
    assert base.version == CONTRATO_VERSION
    assert clave_de("t", ficha, base) != clave_de("t", ficha, otra)


# ---------------------------------------------------------------------------
# Validacion estricta de la respuesta
# ---------------------------------------------------------------------------


def _con_respuesta_muta(**cambios) -> dict:
    cuerpo = json.loads(json.dumps(RESPUESTA_OK))
    respuestas = cuerpo["answers"][PREGUNTA_ID]
    for clave, valor in cambios.items():
        respuestas[clave] = valor
    return cuerpo


@pytest.mark.parametrize(
    "cuerpo",
    [
        {**RESPUESTA_OK, "model": "jev-1.12.9"},
        {**RESPUESTA_OK, "answers": {}},
        _con_respuesta_muta(type="noul"),
        _con_respuesta_muta(choice="producto_similar"),
        _con_respuesta_muta(probabilities={"satisface": 0.5, "no_satisface": 0.5}),
        _con_respuesta_muta(
            probabilities={
                "satisface": 0.1,
                "no_satisface": 0.8,
                "informacion_insuficiente": float("nan"),
            }
        ),
        _con_respuesta_muta(
            probabilities={
                "satisface": 0.1,
                "no_satisface": 0.8,
                "informacion_insuficiente": float("inf"),
            }
        ),
        _con_respuesta_muta(
            probabilities={
                "satisface": 0.1,
                "no_satisface": 0.8,
                "informacion_insuficiente": 0.2,
            }
        ),
        _con_respuesta_muta(confidence=1.5),
    ],
)
def test_respuesta_invalida_da_estado_visible(cuerpo):
    resultado = _pedir(_Transporte(cuerpo))
    assert hasattr(resultado, "codigo")
    assert resultado.codigo == "respuesta_invalida"
    assert not hasattr(resultado, "juicio")


@pytest.mark.parametrize(
    "cuerpo",
    [_con_respuesta_muta(choice="x" * 10_000), {**RESPUESTA_OK, "model": "m" * 10_000}],
    ids=["choice-enorme", "model-enorme"],
)
def test_detalle_de_fallo_queda_acotado(cuerpo):
    """R12 (triage G1-15): el detalle de `respuesta_invalida` repite valores
    del proveedor; se guarda en jev_par_evento.error y no puede crecer sin
    limite."""
    resultado = _pedir(_Transporte(cuerpo))
    assert resultado.codigo == "respuesta_invalida"
    assert len(resultado.detalle) <= 300
    assert resultado.detalle.endswith("…")


# ---------------------------------------------------------------------------
# Estados visibles: timeout, red, redireccion, contexto excedido, sin clave
# ---------------------------------------------------------------------------


def test_timeout_da_estado_visible_y_duraciones():
    resultado = _pedir(_Transporte(error=httpx.TimeoutException("se agoto la espera")))
    assert resultado.codigo == "timeout"
    assert resultado.duracion_ms is not None


def test_error_de_red_da_estado_visible():
    resultado = _pedir(_Transporte(error=httpx.ConnectError("conexion rechazada")))
    assert resultado.codigo == "red"


def test_redireccion_da_estado_visible_y_no_sigue():
    resultado = _pedir(_Transporte(RESPUESTA_OK, status=301))
    assert resultado.codigo == "redireccion"
    assert resultado.duracion_ms is not None


def test_status_5xx_da_estado_visible():
    resultado = _pedir(_Transporte({"error": "overloaded"}, status=529))
    assert resultado.codigo == "red"


def test_contexto_excedido_sin_llamar_al_transporte():
    transporte = _Transporte(RESPUESTA_OK)
    resultado = pedir_juicio(
        "t" * 4096,
        _ficha(),
        contrato_por_defecto(),
        transporte=transporte,
        api_key="clave-de-prueba",
    )
    assert resultado.codigo == "contexto_excedido"
    assert transporte.llamadas == 0


def test_ficha_grande_contexto_excedido_sin_llamar_al_transporte():
    transporte = _Transporte(RESPUESTA_OK)
    ficha = _ficha(hechos=(HechoConFuente(texto="x" * 20000, fuente="f"),))
    resultado = pedir_juicio(
        "termino",
        ficha,
        contrato_por_defecto(),
        transporte=transporte,
        api_key="clave-de-prueba",
    )
    assert resultado.codigo == "contexto_excedido"
    assert transporte.llamadas == 0


def test_sin_api_key_no_llama_al_transporte():
    transporte = _Transporte(RESPUESTA_OK)
    resultado = _pedir(transporte, api_key="")
    assert resultado.codigo == "sin_api_key"
    assert transporte.llamadas == 0


# ---------------------------------------------------------------------------
# Sin secreto en la salida; usage faltante = costo desconocido
# ---------------------------------------------------------------------------


def test_los_estatos_de_fallo_no_contienen_secreto():
    secreto = "clave-secreta-de-prueba"
    transporte = _Transporte(error=httpx.TimeoutException(f"timeout con {secreto}"))
    resultado = _pedir(transporte, api_key=secreto)
    assert secreto not in resultado.detalle
    assert secreto not in resultado.codigo


def test_respuesta_valida_sin_usage_deja_costo_desconocido():
    cuerpo = json.loads(json.dumps(RESPUESTA_OK))
    cuerpo.pop("usage")
    resultado = _pedir(_Transporte(cuerpo))
    assert hasattr(resultado, "juicio")
    assert resultado.usage is None


def test_leer_api_key_desde_secrets_dir(tmp_path, monkeypatch):
    (tmp_path / "typesafe.json").write_text(json.dumps({"api_key": "k-123"}), encoding="utf-8")
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))
    assert leer_api_key() == "k-123"


def test_leer_api_key_sin_config_da_vacia(tmp_path, monkeypatch):
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))
    assert leer_api_key() == ""


def test_leer_api_key_sin_variable_usa_la_ruta_canonica(tmp_path, monkeypatch):
    """Regresion revision automatica B3-r3 (B2): sin ORBIT_SECRETS_DIR la
    ruta es la canonica del repo (DEFAULT_SECRETS_DIR de app.ads.config),
    JAMAS el cwd: un typesafe.json plantado en el directorio actual no se
    lee y la clave canonica si."""
    canonico = tmp_path / "canonico"
    canonico.mkdir()
    (canonico / "typesafe.json").write_text(json.dumps({"api_key": "canonica"}), encoding="utf-8")
    (tmp_path / "typesafe.json").write_text(json.dumps({"api_key": "de-cwd"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # el cwd trampa: contiene su propio typesafe.json
    monkeypatch.delenv("ORBIT_SECRETS_DIR", raising=False)
    monkeypatch.setattr("app.ads.config.DEFAULT_SECRETS_DIR", str(canonico))
    assert leer_api_key() == "canonica"
