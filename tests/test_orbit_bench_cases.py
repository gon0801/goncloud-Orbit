from __future__ import annotations

import math
import textwrap
from pathlib import Path

import pytest

from tools.orbit_bench.catalog import CASES, VERSION
from tools.orbit_bench.graders import grade

IDS = {
    "codigo",
    "depuracion",
    "arquitectura",
    "diseno",
    "revision",
    "criterio",
    "mantenibilidad",
}


def _entrega(tmp_path: Path, source: str, *, nombre: str = "solution.py") -> Path:
    candidate = tmp_path / "candidate"
    candidate.mkdir(parents=True)
    (candidate / nombre).write_text(textwrap.dedent(source), encoding="utf-8")
    if nombre == "solution.py":
        (candidate / "NOTAS.md").write_text(
            "Razonamiento y verificacion con casos de frontera.\n", encoding="utf-8"
        )
        (candidate / "MANTENIMIENTO.md").write_text(
            "Cambio acotado y replay de regresion.\n", encoding="utf-8"
        )
    return candidate


def _correcto_codigo() -> str:
    return """
        import json
        import sys
        from datetime import date
        from decimal import Decimal, InvalidOperation

        UMBRALES = {
            "amazon_us": (100, Decimal("40.0000"), "USD"),
            "amazon_mx": (100, Decimal("500.0000"), "MXN"),
        }

        def decidir(data):
            if data.get("cost") is None:
                return {"action": "none", "reason": "dato_faltante"}
            try:
                clicks = int(data["clicks"])
                orders = int(data["orders"])
                cost = Decimal(data["cost"])
                metric_date = date.fromisoformat(data["metric_date"])
                decided_at = date.fromisoformat(data["decided_at"])
                min_clicks, min_cost, currency = UMBRALES[data["platform"]]
            except (KeyError, ValueError, InvalidOperation):
                return {"action": "none", "reason": "dato_faltante"}
            if data.get("currency") != currency:
                return {"action": "none", "reason": "moneda_invalida"}
            madura = (decided_at - metric_date).days >= 10
            if madura and orders == 0 and clicks >= min_clicks and cost >= min_cost:
                return {"action": "pause", "reason": "corte"}
            return {"action": "none", "reason": "evidencia_insuficiente"}

        json.dump(decidir(json.load(sys.stdin)), sys.stdout)
    """


def _correcto_depuracion() -> str:
    return """
        import json
        import sys
        from decimal import Decimal, InvalidOperation

        ACTUAL = {
            "amazon_us": (100, Decimal("40.0000"), "USD"),
            "amazon_mx": (100, Decimal("500.0000"), "MXN"),
        }

        def decidir(data):
            try:
                if data["mode"] == "replay":
                    congelada = data.get("frozen_policy")
                    if congelada is None:
                        return {"action": "none", "reason": "politica_historica_ausente"}
                    min_clicks = int(congelada["min_clicks"])
                    min_cost = Decimal(congelada["min_cost"])
                    currency = congelada["currency"]
                else:
                    min_clicks, min_cost, currency = ACTUAL[data["platform"]]
                clicks = int(data["clicks"])
                orders = int(data["orders"])
                cost = Decimal(data["cost"])
            except (KeyError, ValueError, InvalidOperation):
                return {"action": "none", "reason": "dato_faltante"}
            if data.get("currency") != currency:
                return {"action": "none", "reason": "moneda_invalida"}
            if orders == 0 and clicks >= min_clicks and cost >= min_cost:
                return {"action": "pause", "reason": "corte"}
            return {"action": "none", "reason": "evidencia_insuficiente"}

        json.dump(decidir(json.load(sys.stdin)), sys.stdout)
    """


def _correcto_mantenibilidad() -> str:
    return _correcto_codigo().replace(
        'def decidir(data):\n            if data.get("cost") is None:',
        "def decidir(data):\n"
        '            if data.get("goal_enabled") is False:\n'
        '                return {"action": "none", "reason": "goal_deshabilitado"}\n'
        '            if data.get("cost") is None:',
    )


def test_catalogo_publica_contrato_completo_y_extension_oculta() -> None:
    assert isinstance(VERSION, str) and VERSION
    assert set(CASES) == IDS
    for case_id, case in CASES.items():
        assert set(case) == {
            "title",
            "category",
            "prompt",
            "files",
            "artifacts",
            "dimensions",
            "automatic",
            "extension_of",
        }
        assert case["title"] and case["prompt"]
        assert all(
            isinstance(path, str) and isinstance(body, str) for path, body in case["files"].items()
        )
        assert case["artifacts"]
        assert case["dimensions"]
        assert case["extension_of"] is None or case["extension_of"] in CASES
        assert all("/" not in artifact and "\\" not in artifact for artifact in case["artifacts"])
        assert "REVIEWER.md" not in case["files"]
        assert case_id != "mantenibilidad" or case["files"] == {}
    assert CASES["mantenibilidad"]["extension_of"] == "codigo"


def test_codigo_acepta_frontera_monetaria_y_rechaza_mutante_sin_moneda(tmp_path: Path) -> None:
    correcto = _entrega(tmp_path, _correcto_codigo())
    resultado = grade("codigo", correcto)
    assert resultado["automatic_pass"] is True
    assert all(check["passed"] for check in resultado["checks"])

    mutante = _entrega(
        tmp_path / "mutante",
        """
        import json
        import sys

        from datetime import date

        data = json.load(sys.stdin)
        pisos = {"amazon_us": (100, 40.0, "USD"), "amazon_mx": (100, 500.0, "MXN")}
        clicks, cost, currency = pisos[data["platform"]]
        edad = date.fromisoformat(data["decided_at"]) - date.fromisoformat(data["metric_date"])
        madura = edad.days >= 10
        pasa = (
            madura
            and data["orders"] == 0
            and data["clicks"] >= clicks
            and float(data["cost"]) >= cost
        )
        reason = "corte" if pasa else "evidencia_insuficiente"
        json.dump({"action": "pause" if pasa else "none", "reason": reason}, sys.stdout)
        """,
    )
    mutacion = grade("codigo", mutante)
    assert mutacion["automatic_pass"] is False
    assert any(not check["passed"] for check in mutacion["checks"])


def test_codigo_detecta_moneda_incorrecta_y_dato_faltante(tmp_path: Path) -> None:
    correcto = _entrega(tmp_path, _correcto_codigo())
    resultado = grade("codigo", correcto)
    assert resultado["automatic_pass"] is True

    mutante = _entrega(
        tmp_path / "mutante",
        """
        import json
        import sys

        data = json.load(sys.stdin)
        cost = float(data.get("cost") or 0)
        pasa = data.get("orders") == 0 and data.get("clicks", 0) >= 100 and cost >= 40
        json.dump(
            {
                "action": "pause" if pasa else "none",
                "reason": "corte" if pasa else "evidencia_insuficiente",
            },
            sys.stdout,
        )
        """,
    )
    assert grade("codigo", mutante)["automatic_pass"] is False


def test_codigo_detecta_mutante_que_elimina_umbral_de_clicks(tmp_path: Path) -> None:
    mutante = _entrega(
        tmp_path,
        _correcto_codigo().replace("clicks >= min_clicks and ", ""),
    )

    resultado = grade("codigo", mutante)

    assert resultado["automatic_pass"] is False
    assert any(not check["passed"] for check in resultado["checks"])


def test_grader_admite_candidate_relativo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _entrega(tmp_path, _correcto_codigo())
    monkeypatch.chdir(tmp_path)
    assert grade("codigo", Path("candidate"))["automatic_pass"] is True


def test_depuracion_usa_politica_congelada_y_falla_cerrado_sin_marcador(tmp_path: Path) -> None:
    correcto = _entrega(tmp_path, _correcto_depuracion())
    resultado = grade("depuracion", correcto)
    assert resultado["automatic_pass"] is True

    mutante = _entrega(
        tmp_path / "mutante",
        _correcto_depuracion().replace(
            'if data["mode"] == "replay":',
            'if False and data["mode"] == "replay":',
        ),
    )
    mutacion = grade("depuracion", mutante)
    assert mutacion["automatic_pass"] is False
    assert any(not check["passed"] for check in mutacion["checks"])


def test_depuracion_detecta_mutante_sin_validacion_de_moneda_replay(tmp_path: Path) -> None:
    mutante = _entrega(
        tmp_path,
        _correcto_depuracion().replace(
            'if data.get("currency") != currency:',
            'if False and data.get("currency") != currency:',
        ),
    )

    resultado = grade("depuracion", mutante)

    assert resultado["automatic_pass"] is False
    assert any(not check["passed"] for check in resultado["checks"])


def test_mantenibilidad_rejuega_codigo_y_respeta_opt_out(tmp_path: Path) -> None:
    correcto = _entrega(tmp_path, _correcto_mantenibilidad())
    resultado = grade("mantenibilidad", correcto)
    assert resultado["automatic_pass"] is True
    assert all(check["passed"] for check in resultado["checks"])

    legado = _entrega(tmp_path / "legado", _correcto_codigo())
    assert grade("codigo", legado)["automatic_pass"] is True
    assert grade("mantenibilidad", legado)["automatic_pass"] is False


@pytest.mark.parametrize(
    ("source", "timeout"),
    [
        ("import time; time.sleep(2)", 0.05),
        ("print('no es json')", 1.0),
        ("print('[]')", 1.0),
        ("import sys; sys.stdout.buffer.write(bytes([255]))", 1.0),
    ],
)
def test_grader_falla_ante_timeout_o_json_malformado(
    tmp_path: Path, source: str, timeout: float
) -> None:
    candidate = _entrega(tmp_path, source)
    resultado = grade("codigo", candidate, timeout=timeout)
    assert resultado["automatic_pass"] is False
    assert any(not check["passed"] for check in resultado["checks"])


def test_grader_falla_si_falta_solution(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "NOTAS.md").write_text("analisis\n", encoding="utf-8")
    resultado = grade("codigo", candidate)
    assert resultado["automatic_pass"] is False
    assert resultado["checks"] == [
        {"name": "artefacto solution.py", "passed": False, "detail": "archivo ausente"},
        {"name": "artefacto NOTAS.md", "passed": True, "detail": "archivo presente"},
    ]


def test_grader_ejecuta_codigo_pero_no_aprueba_si_faltan_notas(tmp_path: Path) -> None:
    candidate = _entrega(tmp_path, _correcto_codigo())
    (candidate / "NOTAS.md").unlink()
    resultado = grade("codigo", candidate)
    assert resultado["automatic_pass"] is False
    assert resultado["checks"][1] == {
        "name": "artefacto NOTAS.md",
        "passed": False,
        "detail": "archivo ausente",
    }
    assert any(check["name"].startswith("evaluacion privada") for check in resultado["checks"])


@pytest.mark.parametrize("timeout", [0, -1, math.inf, math.nan, True, "1"])
def test_grader_rechaza_timeout_no_finito_ni_positivo(tmp_path: Path, timeout: object) -> None:
    candidate = _entrega(tmp_path, _correcto_codigo())
    with pytest.raises(ValueError, match="timeout"):
        grade("codigo", candidate, timeout=timeout)


def test_casos_humanos_quedan_pendientes_aunque_haya_artefacto(tmp_path: Path) -> None:
    for case_id in ("arquitectura", "diseno", "revision", "criterio"):
        artifacts = CASES[case_id]["artifacts"]
        candidate = _entrega(tmp_path / case_id, "evidencia concreta\n", nombre=artifacts[0])
        for artifact in artifacts[1:]:
            (candidate / artifact).write_text("evidencia concreta\n", encoding="utf-8")
        resultado = grade(case_id, candidate)
        assert resultado["automatic_pass"] is None
        assert resultado["checks"] == [
            {"name": f"artefacto {artifact}", "passed": True, "detail": "archivo presente"}
            for artifact in artifacts
        ]


def test_caso_desconocido_no_se_convierte_en_aprobacion(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        grade("inexistente", tmp_path)
