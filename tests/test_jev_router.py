"""Pruebas del router Jev (sin red, sin key: todo moqueado o apagado)."""

from __future__ import annotations

import json

import tools.jev_router as jr


def test_apagado_por_defecto(monkeypatch, tmp_path):
    monkeypatch.delenv("JEV_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    r = jr.decidir("?", ["a", "b"], {}, log=tmp_path / "s.jsonl")
    assert r == {"opcion": None, "confianza": 0.0, "via": "off"}


def test_sin_key_dry_run_y_anota(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_ROUTER_ENABLED", "1")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    log = tmp_path / "s.jsonl"
    r = jr.decidir("quien sigue?", ["a", "b"], {"x": 1}, log=log)
    assert r["via"] == "dry-run" and r["opcion"] is None
    fila = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert fila["opciones"] == ["a", "b"] and fila["via"] == "dry-run"


def test_confianza_baja_entrega_none(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_ROUTER_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "falsa")
    monkeypatch.setattr(jr, "_llamar_api", lambda *a: ("a", 0.5))
    r = jr.decidir("?", ["a", "b"], {}, umbral=0.85, log=None)
    assert r == {"opcion": None, "confianza": 0.5, "via": "jev"}


def test_confianza_alta_entrega_opcion(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_ROUTER_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "falsa")
    monkeypatch.setattr(jr, "_llamar_api", lambda *a: ("b", 0.95))
    r = jr.decidir("?", ["a", "b"], {}, umbral=0.85, log=None)
    assert r == {"opcion": "b", "confianza": 0.95, "via": "jev"}


def test_error_de_red_no_levanta(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_ROUTER_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "falsa")

    def roto(*a):
        raise TimeoutError("red caida")

    monkeypatch.setattr(jr, "_llamar_api", roto)
    log = tmp_path / "s.jsonl"
    r = jr.decidir("?", ["a"], {}, log=log)
    assert r["via"] == "error" and r["opcion"] is None
    assert "red caida" in log.read_text(encoding="utf-8")


class _Ans:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_puntuar_devuelve_puntaje(monkeypatch):
    monkeypatch.setenv("JEV_ROUTER_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "falsa")
    monkeypatch.setattr(
        jr,
        "_preguntar",
        lambda key, estado, qs: {"puntaje": _Ans(score=1.7, confidence=0.9)},
    )
    r = jr.puntuar("?", ["malo", "maso", "bueno"], {}, log=None)
    assert r == {"puntaje": 1.7, "confianza": 0.9, "via": "jev"}


def test_lote_mezcla_choice_y_score_en_una_llamada(monkeypatch):
    llamadas = []
    monkeypatch.setenv("JEV_ROUTER_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "falsa")

    def falsa(key, estado, qs):
        llamadas.append((key, estado, sorted(qs)))
        return {
            "w": _Ans(choice="research", confidence=0.9, probabilities={"research": 0.9}),
            "u": _Ans(score=2.0, confidence=0.8),
        }

    monkeypatch.setattr(jr, "_preguntar", falsa)
    r = jr.decidir_lote(
        [
            {
                "id": "w",
                "tipo": "choice",
                "pregunta": "quien?",
                "opciones": ["research", "write"],
                "estado": {"t": 1},
            },
            {
                "id": "u",
                "tipo": "score",
                "pregunta": "urgencia?",
                "niveles": ["espera", "semana", "hoy"],
                "estado": {"t": 1},
            },
        ],
        log=None,
    )
    assert len(llamadas) == 1, "el lote usa UNA llamada"
    assert r["w"] == {"opcion": "research", "confianza": 0.9, "via": "jev"}
    assert r["u"] == {"puntaje": 2.0, "confianza": 0.8, "via": "jev"}


def test_lote_apagado_no_llama(monkeypatch):
    monkeypatch.delenv("JEV_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    r = jr.decidir_lote(
        [{"id": "w", "tipo": "choice", "pregunta": "?", "opciones": ["a"]}], log=None
    )
    assert r == {"w": {"via": "off"}}
