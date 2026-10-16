"""Router piloto de decisiones via Jev (TypeSafe) para agentes Hermes.

Patron codila: agente -> Jev decide -> agente ejecuta. Solo para
decisiones repetidas y difusas donde las reglas deterministas no
alcanzan (ej. triage). Lo determinista sigue en codigo ($0).

Seguridad por diseno:
- Apagado por defecto: sin JEV_ROUTER_ENABLED=1 no hay llamada.
- Sin TYPESAFE_API_KEY no hay llamada (modo dry-run: devuelve
  opcion=None para que el llamador siga su camino actual).
- Kill switch: JEV_ROUTER_ENABLED=0 (o ausente) = bypass total.
- El `estado` lo arma el llamador: JAMAS meter datos personales
  (nombres, correos, asuntos, montos) hasta excepcion del dueno.
- La key nunca va en el repo: solo env.

Transporte: usa typesafe-sdk si esta instalado
(`pip install typesafe-sdk`); si no, error claro, sin llamada.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

UMBRAL_DEFAULT = 0.85
LOG_DEFAULT = Path("logs/jev_router_shadow.jsonl")


def _apagado() -> bool:
    return os.environ.get("JEV_ROUTER_ENABLED") != "1"


def _llamar_api(key: str, pregunta: str, opciones: list[str], estado: dict) -> tuple:
    """Una decision Choice contra la API real. Devuelve (opcion, confianza).

    Interfaz verificada contra typesafe-sdk 0.7.5 (rueda local, sin key):
    TypeSafeClient(api_key).system_one(state, {"decision": Choice(...)}).
    """
    from typesafe_sdk import Choice, TypeSafeClient

    criterios = {op: op for op in opciones}
    cliente = TypeSafeClient(api_key=key)
    try:
        resp = cliente.system_one(
            state={"hechos": estado},
            questions={"decision": Choice(criteria=criterios, instructions=pregunta)},
        )
        ans = resp.answers["decision"]
    finally:
        cliente.close()
    confianza = float(getattr(ans, "confidence", 0.0) or 0.0)
    opcion = getattr(ans, "choice", None)
    if opcion is None:
        probs = getattr(ans, "probabilities", {}) or {}
        if probs:
            opcion = max(probs, key=probs.get)
            confianza = max(confianza, float(probs[opcion]))
    return opcion, confianza


def decidir(
    pregunta: str,
    opciones: list[str],
    estado: dict,
    umbral: float = UMBRAL_DEFAULT,
    log: Path | None = LOG_DEFAULT,
) -> dict:
    """Pide a Jev elegir entre `opciones` dado `estado`.

    Devuelve {"opcion", "confianza", "via"} donde via es
    "off" (kill switch), "dry-run" (sin key/SDK) o "jev".
    Con confianza < umbral, opcion es None: el llamador decide.
    Nunca levanta por red: el error se registra y opcion=None.
    """
    if _apagado():
        return {"opcion": None, "confianza": 0.0, "via": "off"}
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        _anotar(log, pregunta, opciones, estado, None, 0.0, "dry-run", "")
        return {"opcion": None, "confianza": 0.0, "via": "dry-run"}
    try:
        opcion, confianza = _llamar_api(key, pregunta, opciones, estado)
    except Exception as exc:  # noqa: BLE001 - red resiliente, no frena
        _anotar(log, pregunta, opciones, estado, None, 0.0, "error", str(exc)[:200])
        return {"opcion": None, "confianza": 0.0, "via": "error"}
    if confianza < umbral:
        opcion = None
    _anotar(log, pregunta, opciones, estado, opcion, confianza, "jev", "")
    return {"opcion": opcion, "confianza": confianza, "via": "jev"}


def _anotar(log, pregunta, opciones, estado, opcion, confianza, via, nota):
    if log is None:
        return
    fila = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "pregunta": pregunta,
        "opciones": opciones,
        "estado": estado,
        "opcion": opcion,
        "confianza": confianza,
        "via": via,
        "nota": nota,
    }
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fila, ensure_ascii=False) + "\n")
    except OSError:
        pass
