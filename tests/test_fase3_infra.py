"""Pruebas unitarias de la infraestructura compartida de Fase 3.3."""

from __future__ import annotations

from collections import Counter
from contextlib import nullcontext

import pytest
from psycopg import pq

from app import http_base, ingest_run, ledger_atribucion
from app.meli.ledger import AtribuidorMeli
from app.redaction import REDACTED, register_secret


def test_http_base_validar_ruta_relativa_bloquea_ssrf_y_traversal():
    assert http_base.validar_ruta_relativa("/items/MLM123") == "/items/MLM123"
    for mala in (
        "https://evil.test/x",
        "//evil.test/x",
        "/items/../admin",
        "/items\\evil",
        "/items/\r\nX-Injected: 1",
        "sin-barra",
    ):
        with pytest.raises(ValueError):
            http_base.validar_ruta_relativa(mala)


def test_http_base_parse_retry_after_finito_rechaza_nan_inf_y_negativos():
    assert http_base.parse_retry_after_finito("nan") is None
    assert http_base.parse_retry_after_finito("inf") is None
    assert http_base.parse_retry_after_finito("-5") is None
    assert http_base.parse_retry_after_finito("120", techo=60.0) == 60.0
    assert http_base.parse_retry_after_finito("3.5", techo=60.0) == 3.5


def test_ingest_run_sanitiza_nul_y_secretos_y_recupera_inerror():
    secreto = "tok-f33-secreto-abc"
    register_secret(secreto)
    assert ingest_run.sanitizar_motivo(f"fallo\x00 {secreto}") == f"fallo {REDACTED}"
    assert ingest_run.formato_skip_reason(Counter({"b": 2, "a": 0, "c": 1})) == "2x b, 1x c"

    eventos: list[str] = []

    class _Info:
        transaction_status = pq.TransactionStatus.INERROR

    class _Conn:
        info = _Info()

        def rollback(self):
            eventos.append("rollback")
            self.info.transaction_status = pq.TransactionStatus.IDLE

        def transaction(self):
            return nullcontext()

        def execute(self, sql, params=None):
            eventos.append(f"exec:{params[2]}")
            return None

    ingest_run.sellar_fallo_seguro(
        _Conn(),
        42,
        KeyboardInterrupt(f"interrumpido\x00 {secreto}"),
        tras_sellar=lambda: eventos.append("alertas"),
    )
    assert eventos == ["rollback", f"exec:interrumpido {REDACTED}", "alertas"]


def test_atribuidor_no_envenena_fallo_transitorio_y_cierra_context_manager(monkeypatch):
    intentos = {"n": 0}
    cerrada = {"ok": False}

    class _ConnFalsa:
        def execute(self, _sql, _params=None):
            return type("_Cur", (), {"fetchone": lambda self: (7,), "fetchall": lambda self: []})()

        def close(self):
            cerrada["ok"] = True

    def _connect_intermitente(_dsn, **_kw):
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise RuntimeError("base reiniciando")
        return _ConnFalsa()

    monkeypatch.setenv("ORBIT_DSN_INGEST", "postgresql://fake/db")
    monkeypatch.setattr(ledger_atribucion, "connect", _connect_intermitente)

    with AtribuidorMeli() as atr:
        assert atr._db() is None  # fallo transitorio
        assert atr._db() is not None  # recupera en el siguiente intento (A10)
    assert cerrada["ok"] is True
