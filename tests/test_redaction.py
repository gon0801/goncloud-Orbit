"""Tests de app/redaction.py: todo secreto se redacta, sin piso minimo."""

import logging

from app.redaction import (
    SecretScrubFilter,
    install_scrub_filter,
    redact_dsn,
    redact_url,
    register_secret,
    scrub,
)


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


# ---------------------------------------------------------------------------
# redact_dsn, forma URL: password con '@' interno y espacio inicial
# ---------------------------------------------------------------------------


def test_dsn_url_password_con_arroba_interna_se_redacta_completa():
    # Bug real 1: la password se cortaba en el PRIMER '@' y el resto
    # quedaba sin redactar en el log. El grupo codicioso + sufijo anclado
    # al ULTIMO '@' la captura completa.
    dsn = "postgresql://orbit_read:fx@pw@dos@atron@db.interna:5432/orbit"
    resultado = redact_dsn(dsn)
    assert resultado == "postgresql://orbit_read:***@db.interna:5432/orbit"
    assert "fx@pw@dos@atron" not in resultado
    # La password COMPLETA queda registrada como secreto para scrub.
    assert scrub("eco fx@pw@dos@atron fin") == "eco ***REDACTED*** fin"


def test_dsn_url_con_espacio_inicial_tambien_redacta():
    # Bug real 2 (cross-review Codex, ronda 1): un DSN con espacio inicial
    # no matcheaba y la password quedaba sin redactar.
    dsn = " postgresql://orbit_read:pw-esp-fixture@db.interna:5432/orbit"
    resultado = redact_dsn(dsn)
    assert resultado == " postgresql://orbit_read:***@db.interna:5432/orbit"
    assert "pw-esp-fixture" not in resultado
    assert scrub("eco pw-esp-fixture fin") == "eco ***REDACTED*** fin"


# ---------------------------------------------------------------------------
# redact_dsn, forma conninfo: password=... con y sin comillas simples
# ---------------------------------------------------------------------------


def test_dsn_conninfo_password_sin_comillas():
    dsn = "host=db.interna port=5432 user=orbit_read password=kv-sin-comi11as dbname=orbit"
    resultado = redact_dsn(dsn)
    assert "password=***" in resultado
    assert "kv-sin-comi11as" not in resultado
    assert scrub("eco kv-sin-comi11as fin") == "eco ***REDACTED*** fin"


def test_dsn_conninfo_password_con_comillas_simples():
    dsn = "host=db.interna password='kv c0n comi11as' dbname=orbit"
    resultado = redact_dsn(dsn)
    assert "password=***" in resultado
    assert "kv c0n comi11as" not in resultado
    assert "c0n comi11as" not in resultado
    # Se registra la password SIN las comillas: scrub la limpia donde sea
    # que aparezca rodeada de otras cosas.
    assert scrub("eco 'kv c0n comi11as' fin") == "eco '***REDACTED***' fin"


# ---------------------------------------------------------------------------
# redact_url: query firmada, fragment y userinfo
# ---------------------------------------------------------------------------


def test_redact_url_elimina_query_string_firmada():
    # Caso real: las URLs firmadas de descarga llevan el secreto en la
    # query string (X-Amz-Signature); no debe llegar a logs ni errores.
    url = (
        "https://reporting.amz-fixture.example/reporte.csv"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=sig-fixture-8899"
        "&X-Amz-Credential=AKIAFX123"
    )
    resultado = redact_url(url)
    assert resultado == "https://reporting.amz-fixture.example/reporte.csv"
    assert "X-Amz-Signature" not in resultado
    assert "sig-fixture-8899" not in resultado


def test_redact_url_elimina_fragment():
    url = "https://descargas.example/informe.json#frag-tok3n-fx"
    resultado = redact_url(url)
    assert resultado == "https://descargas.example/informe.json"
    assert "frag-tok3n-fx" not in resultado


def test_redact_url_elimina_userinfo_y_registra_password():
    url = "https://usuario-desc:pw-usr-fixture@reportes.example/descarga/r"
    resultado = redact_url(url)
    assert resultado == "https://reportes.example/descarga/r"
    assert "usuario-desc" not in resultado
    assert "pw-usr-fixture" not in resultado
    assert scrub("eco pw-usr-fixture fin") == "eco ***REDACTED*** fin"


# ---------------------------------------------------------------------------
# redact_url, ramas de error: nunca ecoar la entrada
# ---------------------------------------------------------------------------


def test_redact_url_no_parseable_no_ecoa_la_entrada():
    # "http://[..." sin cerrar el corchete hace que urlsplit levante
    # ValueError. Se devuelve un literal fijo, jamas la URL cruda.
    assert redact_url("http://[bracket-fx") == "<url-no-parseable>"


def test_redact_url_malformada_no_ecoa_la_entrada():
    # Un espacio dentro del scheme manda TODO a `path` (incluido un posible
    # userinfo con password): no se rescatan pedazos, no se ecoa la entrada.
    url = "ht tp://usuario:pw-m4l-fx@host.example/x"
    resultado = redact_url(url)
    assert resultado == "<url-malformada>"
    assert url not in resultado
    assert "pw-m4l-fx" not in resultado


# ---------------------------------------------------------------------------
# SecretScrubFilter / install_scrub_filter
# ---------------------------------------------------------------------------


class _Capturador(logging.Handler):
    """Handler de prueba que acumula los records tal como llegan."""

    def __init__(self):
        super().__init__()
        self.registros: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.registros.append(record)


def _logger_limpio(nombre: str) -> logging.Logger:
    log = logging.getLogger(nombre)
    for filtro in list(log.filters):
        log.removeFilter(filtro)
    return log


def test_scrub_filter_limpia_el_mensaje_ya_formateado():
    register_secret("filtr0-fixture-77")
    log = _logger_limpio("orbit_test.filtro")
    capturador = _Capturador()
    log.addHandler(capturador)
    viejo_propagate = log.propagate
    log.propagate = False
    try:
        install_scrub_filter(log)
        log.warning("descarga fallo con %s", "filtr0-fixture-77")
        assert len(capturador.registros) == 1
        mensaje = capturador.registros[0].getMessage()
        assert "filtr0-fixture-77" not in mensaje
        assert "***REDACTED***" in mensaje
    finally:
        log.propagate = viejo_propagate
        log.removeHandler(capturador)
        for filtro in list(log.filters):
            log.removeFilter(filtro)


def test_install_scrub_filter_no_se_instala_dos_veces():
    log = _logger_limpio("orbit_test.instalador")
    try:
        install_scrub_filter(log)
        install_scrub_filter(log)
        instalados = [f for f in log.filters if isinstance(f, SecretScrubFilter)]
        assert len(instalados) == 1
    finally:
        for filtro in list(log.filters):
            log.removeFilter(filtro)
