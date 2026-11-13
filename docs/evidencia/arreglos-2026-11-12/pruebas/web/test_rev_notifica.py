"""Revision de avisos Telegram (solo lectura). Pruebas que FALLAN a
proposito: la aserción dice lo que deberia pasar."""

from __future__ import annotations

import datetime as dt

from app import notifica


def test_aviso_de_corte_en_practica_no_promete_ventana_48h():
    """Los cortes en modo shadow (practica) JAMAS se liberan (CONTEXTO.md,
    modulo apply). El aviso no deberia decirle al dueno que el corte se
    ejecuta al vencer 48 horas. En prod el ciclo 174 (2026-11-08) encolo 31
    de practica = 31 mensajes con ese texto."""
    texto = notifica.aviso_corte_encolado(
        notifica.CorteEncolado(
            platform="amazon_us",
            kind="negative",
            search_term="funda <b>iphone</b>",
            vence_el=dt.datetime(2026, 11, 10, 17, 3, 4, 123456, tzinfo=dt.UTC),
            modo="shadow",
        )
    )
    assert "48h" not in texto and "vence_el" not in texto, texto
