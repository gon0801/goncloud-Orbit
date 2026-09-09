"""Tests de app/redaction.py: el piso minimo de secreto (SP-API 01 A.2 F1)."""

from app.redaction import register_secret, scrub


def test_secreto_corto_no_redacta():
    register_secret("T")
    assert scrub("nextToken") == "nextToken"


def test_secreto_largo_si_redacta():
    register_secret("tk-fixture-largo-12345")
    assert scrub("eco tk-fixture-largo-12345 fin") == "eco ***REDACTED*** fin"
