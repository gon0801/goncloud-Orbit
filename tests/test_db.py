"""Conexion a Postgres: override de host para el contenedor compose (4.1).

En el host, los DSN del .env apuntan a 127.0.0.1 (bind del puerto de Postgres).
Dentro del contenedor `app`, 127.0.0.1 es el propio contenedor: el rewrite a
`ORBIT_PG_HOST` (servicio compose `db`) es lo que hace hablar a la API y al
CLI con `docker compose exec` sin tocar el .env ni los permisos de secrets/.
"""

from __future__ import annotations

from app.db import aplicar_host_override, connect

DSN_LOOPBACK = "postgresql://orbit_read:s3cret@127.0.0.1:5432/orbit"
DSN_LOCALHOST = "postgresql://orbit_read:s3cret@localhost:5432/orbit"
DSN_OTRO = "postgresql://orbit_read:s3cret@postgres:5432/orbit"


def test_sin_override_deja_el_dsn_igual():
    assert aplicar_host_override(DSN_LOOPBACK, None) == DSN_LOOPBACK
    assert aplicar_host_override(DSN_LOOPBACK, "") == DSN_LOOPBACK


def test_override_reescribe_loopback_al_servicio_compose():
    assert (
        aplicar_host_override(DSN_LOOPBACK, "db") == "postgresql://orbit_read:s3cret@db:5432/orbit"
    )


def test_override_reescribe_localhost():
    assert (
        aplicar_host_override(DSN_LOCALHOST, "db") == "postgresql://orbit_read:s3cret@db:5432/orbit"
    )


def test_override_no_toca_un_host_que_ya_no_es_loopback():
    assert aplicar_host_override(DSN_OTRO, "db") == DSN_OTRO


def test_connect_aplica_ORBIT_PG_HOST(monkeypatch):
    visto: dict[str, str] = {}

    def fake_connect(dsn: str, **kw):
        visto["dsn"] = dsn

        class _Conn:
            pass

        return _Conn()

    monkeypatch.setattr("app.db.psycopg.connect", fake_connect)
    monkeypatch.setenv("ORBIT_PG_HOST", "db")
    connect(DSN_LOOPBACK)
    assert visto["dsn"] == "postgresql://orbit_read:s3cret@db:5432/orbit"


def test_connect_sin_ORBIT_PG_HOST_no_reescribe(monkeypatch):
    visto: dict[str, str] = {}

    def fake_connect(dsn: str, **kw):
        visto["dsn"] = dsn

        class _Conn:
            pass

        return _Conn()

    monkeypatch.setattr("app.db.psycopg.connect", fake_connect)
    monkeypatch.delenv("ORBIT_PG_HOST", raising=False)
    connect(DSN_LOOPBACK)
    assert visto["dsn"] == DSN_LOOPBACK
