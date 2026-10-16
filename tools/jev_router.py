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


def _preguntar(key: str, estado: dict, preguntas: dict) -> dict:
    """Una llamada system_one con N preguntas (choice/score) en paralelo.

    Interfaz verificada contra typesafe-sdk 0.7.5 (rueda local, sin key).
    Devuelve el mapping de respuestas tal cual.
    """
    from typesafe_sdk import TypeSafeClient

    cliente = TypeSafeClient(api_key=key)
    try:
        resp = cliente.system_one(state={"hechos": estado}, questions=preguntas)
        return dict(resp.answers)
    finally:
        cliente.close()


def _llamar_api(key: str, pregunta: str, opciones: list[str], estado: dict) -> tuple:
    """Una decision Choice. Devuelve (opcion, confianza)."""
    from typesafe_sdk import Choice

    criterios = {op: op for op in opciones}
    ans = _preguntar(key, estado, {"decision": Choice(criteria=criterios, instructions=pregunta)})[
        "decision"
    ]
    confianza = float(getattr(ans, "confidence", 0.0) or 0.0)
    opcion = getattr(ans, "choice", None)
    if opcion is None:
        probs = getattr(ans, "probabilities", {}) or {}
        if probs:
            opcion = max(probs, key=probs.get)
            confianza = max(confianza, float(probs[opcion]))
    return opcion, confianza


def _puntuar_api(key: str, pregunta: str, niveles: list[str], estado: dict) -> tuple:
    """Un puntaje 0..N. Devuelve (puntaje, confianza)."""
    from typesafe_sdk import Score

    ans = _preguntar(
        key, estado, {"puntaje": Score(criteria=list(niveles), instructions=pregunta)}
    )["puntaje"]
    return float(getattr(ans, "score", 0.0) or 0.0), float(getattr(ans, "confidence", 0.0) or 0.0)


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


def puntuar(
    pregunta: str,
    niveles: list[str],
    estado: dict,
    log: Path | None = LOG_DEFAULT,
) -> dict:
    """Puntaje 0..N segun `niveles` (rubrica ordenada) dado `estado`.

    Devuelve {"puntaje", "confianza", "via"}. Sin umbral: el llamador
    decide (ej. publicar exige aprobacion aunque el puntaje sea alto).
    """
    if _apagado():
        return {"puntaje": None, "confianza": 0.0, "via": "off"}
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        _anotar(log, pregunta, niveles, estado, None, 0.0, "dry-run", "")
        return {"puntaje": None, "confianza": 0.0, "via": "dry-run"}
    try:
        puntaje, confianza = _puntuar_api(key, pregunta, niveles, estado)
    except Exception as exc:  # noqa: BLE001 - red resiliente, no frena
        _anotar(log, pregunta, niveles, estado, None, 0.0, "error", str(exc)[:200])
        return {"puntaje": None, "confianza": 0.0, "via": "error"}
    _anotar(log, pregunta, niveles, estado, puntaje, confianza, "jev", "")
    return {"puntaje": puntaje, "confianza": confianza, "via": "jev"}


def decidir_lote(items: list[dict], log: Path | None = LOG_DEFAULT) -> dict:
    """N decisiones en UNA llamada (urgencia + worker + permiso juntos).

    Cada item: {id, tipo: "choice"|"score", pregunta, opciones?|niveles?,
    estado?, umbral?}. Devuelve {id: resultado}. Con kill switch o sin
    key, todo sale off/dry-run sin llamadas.
    """
    if _apagado():
        return {it["id"]: {"via": "off"} for it in items}
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        for it in items:
            _anotar(
                log,
                it["pregunta"],
                it.get("opciones") or it.get("niveles", []),
                it.get("estado", {}),
                None,
                0.0,
                "dry-run",
                it["id"],
            )
        return {it["id"]: {"via": "dry-run"} for it in items}
    from typesafe_sdk import Choice, Score

    preguntas, metas = {}, {}
    for it in items:
        if it["tipo"] == "score":
            preguntas[it["id"]] = Score(criteria=list(it["niveles"]), instructions=it["pregunta"])
        else:
            preguntas[it["id"]] = Choice(
                criteria={op: op for op in it["opciones"]},
                instructions=it["pregunta"],
            )
        metas[it["id"]] = it
    try:
        respuestas = _preguntar(key, items[0].get("estado", {}), preguntas)
    except Exception as exc:  # noqa: BLE001 - red resiliente, no frena
        return {it["id"]: {"via": "error", "nota": str(exc)[:200]} for it in items}
    salida = {}
    for iid, ans in respuestas.items():
        it = metas[iid]
        umbral = it.get("umbral", UMBRAL_DEFAULT)
        if it["tipo"] == "score":
            puntaje = float(getattr(ans, "score", 0.0) or 0.0)
            conf = float(getattr(ans, "confidence", 0.0) or 0.0)
            salida[iid] = {"puntaje": puntaje, "confianza": conf, "via": "jev"}
        else:
            opcion = getattr(ans, "choice", None)
            conf = float(getattr(ans, "confidence", 0.0) or 0.0)
            if opcion is None:
                probs = getattr(ans, "probabilities", {}) or {}
                if probs:
                    opcion = max(probs, key=probs.get)
            if conf < umbral:
                opcion = None
            salida[iid] = {"opcion": opcion, "confianza": conf, "via": "jev"}
        _anotar(
            log,
            it["pregunta"],
            it.get("opciones") or it.get("niveles", []),
            it.get("estado", {}),
            salida[iid].get("opcion", salida[iid].get("puntaje")),
            salida[iid]["confianza"],
            "jev",
            iid,
        )
    return salida


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
            fh.write(json.dumps(fila, ensure_ascii=False, default=str) + "\n")
    except (OSError, TypeError):
        pass
