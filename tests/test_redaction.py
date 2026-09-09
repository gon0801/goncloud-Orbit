"""Tests de app/redaction.py: todo secreto se redacta, sin piso minimo."""

from app.redaction import register_secret, scrub


def test_secreto_corto_tambien_redacta():
    # Revision PR #240/#241: passwords DSN y tokens cortos quedaban sin
    # redactar por el piso minimo. El fixture usa un valor corto pero
    # distintivo (el viejo "T" de un caracter rompia "nextToken": ese era
    # bug del fixture, no de la proteccion).
    register_secret("c0rt4!")
    assert scrub("pw c0rt4! fin") == "pw ***REDACTED*** fin"


def test_secreto_largo_si_redacta():
    register_secret("tk-fixture-largo-12345")
    assert scrub("eco tk-fixture-largo-12345 fin") == "eco ***REDACTED*** fin"
