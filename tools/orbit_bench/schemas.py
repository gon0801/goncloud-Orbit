"""Esquemas Pydantic v2 de validacion para Orbit Bench.

Centraliza la validacion estricta de `metrics.json`, `review.json`,
`grade.json`, resultados de evaluadores y snapshots de casos (antes repartida
en ~150 lineas de `isinstance` y conjuntos manuales entre `runs.py` y
`report.py`). Toda violacion se traduce a `BenchError`.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

StrictNumber = StrictInt | StrictFloat
NonNegNumber = Annotated[StrictNumber | None, Field(default=None)]
NonNegInt = Annotated[StrictInt | None, Field(default=None)]


def _check_nonneg_finite(value: int | float | None, name: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} debe ser un numero no negativo o null")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} debe ser un numero no negativo o null")
    return value


class _BenchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MetricsModel(_BenchModel):
    duration_seconds: StrictInt | StrictFloat | None
    cost_usd: StrictInt | StrictFloat | None
    interventions: StrictInt | None
    rework_minutes: StrictInt | StrictFloat | None
    input_tokens: StrictInt | None
    output_tokens: StrictInt | None

    @field_validator("duration_seconds", "cost_usd", "rework_minutes")
    @classmethod
    def _nonneg_float(cls, value: int | float | None, info) -> int | float | None:
        return _check_nonneg_finite(value, info.field_name)

    @field_validator("interventions", "input_tokens", "output_tokens")
    @classmethod
    def _nonneg_int(cls, value: int | None, info) -> int | None:
        if value is not None and value < 0:
            raise ValueError(f"metrica invalida {info.field_name}")
        return value


class ScoreEntryModel(_BenchModel):
    score: StrictInt | None
    evidence: StrictStr

    @field_validator("score")
    @classmethod
    def _score_range(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= 4:
            raise ValueError("score debe estar entre 0 y 4")
        return value


class ReviewModel(_BenchModel):
    reviewer: StrictStr | None
    accepted: StrictBool | None
    scores: dict[StrictStr, ScoreEntryModel]
    critical_failures: list[StrictStr]
    grade_fingerprint: StrictStr | None

    @field_validator("reviewer")
    @classmethod
    def _non_blank_reviewer(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("reviewer invalido")
        return value

    @field_validator("critical_failures")
    @classmethod
    def _non_blank_failures(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("critical_failures invalido")
        return value


class GradeCheckModel(_BenchModel):
    name: StrictStr
    passed: StrictBool
    detail: StrictStr

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, value: str) -> str:
        if not value:
            raise ValueError("name vacio")
        return value


class GradeResultModel(_BenchModel):
    checks: list[GradeCheckModel]
    automatic_pass: StrictBool | None


class GradeFileModel(_BenchModel):
    case_id: StrictStr
    submission_fingerprint: StrictStr
    checks: list[GradeCheckModel]
    automatic_pass: StrictBool | None
    artifacts_present: StrictBool
    graded_at: StrictStr

    @field_validator("submission_fingerprint")
    @classmethod
    def _sha256_hex(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("submission_fingerprint invalido")
        return value

    @field_validator("graded_at")
    @classmethod
    def _non_empty_graded_at(cls, value: str) -> str:
        if not value:
            raise ValueError("graded_at invalido")
        return value


class CaseSpecModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    title: StrictStr
    category: StrictStr
    prompt: StrictStr
    files: dict[StrictStr, StrictStr]
    artifacts: list[StrictStr]
    dimensions: list[StrictStr]
    automatic: StrictBool
    extension_of: StrictStr | None

    @model_validator(mode="after")
    def _check_non_empty(self) -> CaseSpecModel:
        return self


def format_validation_error(exc: ValidationError, context: str) -> str:
    """Resume el primer fallo de Pydantic conservando el nombre del campo."""
    first = exc.errors()[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    msg = first.get("msg", str(exc))
    return f"{loc or context} invalido en {context}: {msg}"


def validate_model(
    model_cls: type[BaseModel],
    data: Any,
    *,
    error_cls: type[Exception],
    context: str | Path,
) -> dict[str, Any]:
    """Valida `data` con `model_cls` y traduce `ValidationError` a `error_cls`."""
    try:
        parsed = model_cls.model_validate(data)
    except ValidationError as exc:
        raise error_cls(format_validation_error(exc, str(context))) from exc
    return parsed.model_dump()
