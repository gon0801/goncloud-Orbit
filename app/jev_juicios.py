"""Transporte TypeSafe para el juicio por par (JEV ADS 01, 1.3).

Una llamada por par: la unidad de juicio es TERMINO LITERAL + VERSION DE
FICHA. El state lleva SOLO el termino y la ficha (hechos y desconocidos);
jamas grupo, campana, metricas ni otros productos (diseno "Contrato con
Jev y reutilizacion").

- Modelo fijo `jev-1.13.0` (documentacion viva, verificada 2026-10-03/04:
  modelo vigente; aliases no se usan, se pincha la version exacta).
- El contrato (instrucciones, criterios y su ORDEN, serializacion y
  limites de bytes) queda versionado: cambiar orden de opciones o version
  cambia la clave del par y el request hash.
- La respuesta se valida estricta (modelo, IDs, categorias, probabilidades
  finitas que suman 1, confidence en [0, 1]); todo fallo produce un estado
  visible SIN secreto en la salida. V1 no reintenta: registra y permite
  retomar.
- Contexto excedido se detecta ANTES del HTTP por bytes, sin truncar
  hechos. Timeout 30 s y sin redirecciones (no se reenvia autorizacion).
- La falta de usage deja el costo desconocido. Secretos solo via
  ORBIT_SECRETS_DIR; jamas en el repo, request guardado o logs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import httpx

from app.jev_ads import ClavePar, FichaVersion, Juicio, Relacion
from app.redaction import register_secret, scrub

MODELO = "jev-1.13.0"
OPCIONES: tuple[Relacion, ...] = (
    "satisface",
    "no_satisface",
    "informacion_insuficiente",
)
CONTRATO_VERSION = "1"
PREGUNTA_ID = "relacion"
URL = "https://api.typesafe.ai/v1/systemone"
TIMEOUT_S = 30.0
# Limites del piloto, DENTRO del maximo documentado del modelo (64k por
# request, 32k para state + pregunta mas larga): bytes UTF-8 por termino y
# por ficha serializada. Superarlos produce contexto_excedido SIN truncar
# hechos y SIN HTTP.
MAX_BYTES_TERMINO = 1024
MAX_BYTES_FICHA = 16384

INSTRUCCIONES = (
    "Clasifica la relacion entre el termino de busqueda `termino` y la "
    "ficha del producto `ficha`. La ficha trae solo hechos revisados con "
    "fuente y los campos que NO se afirmaron (`desconocidos`). Responde "
    "que tan pertinente es esa busqueda para ESE producto segun sus "
    "hechos; la ausencia de un dato no permite suponerlo ni negarlo."
)
CRITERIOS: tuple[tuple[str, str], ...] = (
    (
        "satisface",
        "El termino busca algo que los hechos de la ficha afirman ofrecer "
        "o ser: busqueda pertinente para el producto.",
    ),
    (
        "no_satisface",
        "El termino busca algo que los hechos de la ficha NO ofrecen o que "
        "los contradicen: busqueda no pertinente para el producto.",
    ),
    (
        "informacion_insuficiente",
        "Los hechos de la ficha no alcanzan para decidir si el termino es pertinente o no.",
    ),
)


@dataclass(frozen=True)
class Contrato:
    """Contrato versionado del juicio. Cualquier cambio de contenido (u
    ORDEN de opciones) cambia su sha256 y con ello la clave del par."""

    modelo: str = MODELO
    opciones: tuple[Relacion, ...] = OPCIONES
    criterios: tuple[tuple[str, str], ...] = CRITERIOS
    instrucciones: str = INSTRUCCIONES
    version: str = CONTRATO_VERSION
    max_bytes_termino: int = MAX_BYTES_TERMINO
    max_bytes_ficha: int = MAX_BYTES_FICHA

    def sha256(self) -> str:
        contenido = {
            "criterios": [list(par) for par in self.criterios],
            "instrucciones": self.instrucciones,
            "max_bytes_ficha": self.max_bytes_ficha,
            "max_bytes_termino": self.max_bytes_termino,
            "modelo": self.modelo,
            "opciones": list(self.opciones),
            "pregunta_id": PREGUNTA_ID,
            "version": self.version,
        }
        return _sha256_json(contenido)


def contrato_por_defecto() -> Contrato:
    return Contrato()


def _sha256_json(contenido: object) -> str:
    canonico = json.dumps(contenido, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _canonico_termino(termino: str) -> str:
    """La clave usa el TEXTO LITERAL UTF-8: no fusiona acentos, numeros ni
    terminos parecidos."""
    return hashlib.sha256(termino.encode("utf-8")).hexdigest()


def clave_de(termino: str, ficha: FichaVersion, contrato: Contrato) -> ClavePar:
    return ClavePar(
        termino_literal_sha256=_canonico_termino(termino),
        ficha_version_id=ficha.id,
        contrato_sha256=contrato.sha256(),
    )


def _ficha_a_state(ficha: FichaVersion) -> dict:
    return {
        "desconocidos": sorted(ficha.desconocidos),
        "hechos": [{"fuente": hecho.fuente, "texto": hecho.texto} for hecho in ficha.hechos],
        "listings": sorted(ficha.listings),
        "observado_at": ficha.observado_at.isoformat(),
        "revisar_antes_de": ficha.revisar_antes_de.isoformat(),
    }


def _payload(termino: str, ficha: FichaVersion, contrato: Contrato) -> dict:
    return {
        "model": contrato.modelo,
        "questions": {
            PREGUNTA_ID: {
                "criteria": dict(contrato.criterios),
                "instructions": contrato.instrucciones,
                "type": "choice",
            }
        },
        "state": {"ficha": _ficha_a_state(ficha), "termino": termino},
    }


def request_sha256(termino: str, ficha: FichaVersion, contrato: Contrato) -> str:
    """Hash del request completo que iria por el wire (para la intencion)."""
    return _sha256_json(_payload(termino, ficha, contrato))


class _TransporteRespuesta(Protocol):
    status_code: int

    def json(self) -> dict: ...


class Transporte(Protocol):
    def __call__(
        self, url: str, *, payload: dict, headers: dict, timeout_s: float
    ) -> _TransporteRespuesta: ...


def transporte_httpx(url: str, *, payload: dict, headers: dict, timeout_s: float) -> httpx.Response:
    """Transporte real. Sin redirecciones: una 3xx no reenvia autorizacion."""
    with httpx.Client(follow_redirects=False, timeout=timeout_s) as cliente:
        return cliente.post(url, json=payload, headers=headers)


@dataclass(frozen=True)
class ResultadoPar:
    """Exito validado, listo para persistir como resultado del par."""

    juicio: Juicio
    usage: Mapping[str, object] | None
    duracion_ms: int


@dataclass(frozen=True)
class FalloPar:
    """Fallo visible del par. `codigo` es un estado del dominio; `detalle`
    va redactado y JAMAS contiene la clave. Un fallo jamas es evidencia de
    incompatibilidad."""

    codigo: str
    detalle: str
    duracion_ms: int | None = None
    usage: Mapping[str, object] | None = None


def leer_api_key() -> str:
    """Lee la clave de `<ORBIT_SECRETS_DIR>/typesafe.json` (patron
    notifica.py). Sin archivo o sin clave devuelve cadena vacia; el llamador
    produce el estado visible sin_api_key sin HTTP."""
    path = Path(os.environ.get("ORBIT_SECRETS_DIR", "")) / "typesafe.json"
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    clave = datos.get("api_key") if isinstance(datos, dict) else None
    if isinstance(clave, str) and clave:
        register_secret(clave)
        return clave
    return ""


def pedir_juicio(
    termino: str,
    ficha: FichaVersion,
    contrato: Contrato,
    *,
    transporte: Transporte = transporte_httpx,
    api_key: str,
) -> ResultadoPar | FalloPar:
    """Pide el juicio de UN par (termino, ficha). Cero reintentos: cada fallo
    sale como estado visible y el lote puede retomarse."""
    inicio = time.monotonic()

    def duracion_ms() -> int:
        return int((time.monotonic() - inicio) * 1000)

    if not api_key:
        return FalloPar("sin_api_key", "falta la clave de TypeSafe en ORBIT_SECRETS_DIR")
    register_secret(api_key)
    if len(termino.encode("utf-8")) > contrato.max_bytes_termino:
        return FalloPar(
            "contexto_excedido",
            f"termino de {len(termino.encode('utf-8'))} bytes supera el maximo"
            f" {contrato.max_bytes_termino}",
        )
    state_ficha = _ficha_a_state(ficha)
    bytes_ficha = len(json.dumps(state_ficha, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    if bytes_ficha > contrato.max_bytes_ficha:
        return FalloPar(
            "contexto_excedido",
            f"ficha de {bytes_ficha} bytes supera el maximo {contrato.max_bytes_ficha}",
        )
    payload = _payload(termino, ficha, contrato)
    try:
        respuesta = transporte(
            URL,
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout_s=TIMEOUT_S,
        )
    except httpx.TimeoutException as error:
        return FalloPar("timeout", scrub(str(error)), duracion_ms())
    except httpx.HTTPError as error:
        return FalloPar("red", scrub(str(error)), duracion_ms())
    if 300 <= respuesta.status_code < 400:
        return FalloPar(
            "redireccion",
            f"status {respuesta.status_code}: no se siguen redirecciones para"
            " no reenviar autorizacion",
            duracion_ms(),
        )
    if respuesta.status_code != 200:
        return FalloPar("red", f"status {respuesta.status_code}", duracion_ms())
    try:
        cuerpo = respuesta.json()
    except (ValueError, TypeError):
        return FalloPar("respuesta_invalida", "cuerpo no JSON", duracion_ms())
    validado = _validar_respuesta(cuerpo, contrato)
    if isinstance(validado, str):
        return FalloPar("respuesta_invalida", validado, duracion_ms())
    relacion, probabilidades, confidence = validado
    juicio = Juicio(
        intento_id=uuid.uuid4(),
        clave=clave_de(termino, ficha, contrato),
        relacion=relacion,
        probabilidades=probabilidades,
        confidence=confidence,
        observado_at=datetime.now(UTC),
    )
    usage = cuerpo.get("usage")
    return ResultadoPar(
        juicio=juicio,
        usage=usage if isinstance(usage, dict) else None,
        duracion_ms=duracion_ms(),
    )


def _validar_respuesta(
    cuerpo: object, contrato: Contrato
) -> tuple[Relacion, Mapping[Relacion, Decimal], Decimal] | str:
    if not isinstance(cuerpo, dict):
        return "cuerpo no es objeto"
    if cuerpo.get("model") != contrato.modelo:
        return f"model distinto al contrato: {cuerpo.get('model')!r}"
    respuestas = cuerpo.get("answers")
    if not isinstance(respuestas, dict) or PREGUNTA_ID not in respuestas:
        return f"falta la respuesta {PREGUNTA_ID}"
    respuesta = respuestas[PREGUNTA_ID]
    if not isinstance(respuesta, dict) or respuesta.get("type") != "choice":
        return "la respuesta no es choice"
    eleccion = respuesta.get("choice")
    if eleccion not in contrato.opciones:
        return f"choice fuera de las opciones: {eleccion!r}"
    probabilidades = respuesta.get("probabilities")
    if not isinstance(probabilidades, dict) or set(probabilidades) != set(contrato.opciones):
        return "probabilities no cubre exactamente las opciones"
    total = 0.0
    distribucion: dict[Relacion, Decimal] = {}
    for opcion, valor in probabilidades.items():
        if not isinstance(valor, (int, float)) or isinstance(valor, bool):
            return f"probabilidad no numerica para {opcion}"
        if not math.isfinite(valor) or valor < 0.0:
            return f"probabilidad no finita o negativa para {opcion}"
        total += valor
        distribucion[opcion] = Decimal(str(valor))
    if abs(total - 1.0) > 1e-6:
        return f"las probabilidades suman {total}"
    confidence = respuesta.get("confidence")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not math.isfinite(confidence)
        or not 0.0 <= confidence <= 1.0
    ):
        return "confidence fuera de [0, 1]"
    return (
        eleccion,
        distribucion,
        Decimal(str(confidence)),
    )
