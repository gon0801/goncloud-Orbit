"""Tests de la migracion `migrations/0066_bids02_retiro_anuncios.sql` (BIDS 02, I.4)."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_fabrica_migracion import (
    ORDEN,
    _entidad,
    _grupo,
    _lote,
    _producto,
    db_fabrica,
)
from test_migracion_0065 import SQL64, SQL65, _impulso
from test_migracion_0068 import SQL68
from test_migracion_0069 import SQL69
from test_schema import _postgres_obligatorio_ausente

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "migrations"
SQL66 = (MIGRACIONES / "0066_bids02_retiro_anuncios.sql").read_text(encoding="utf-8")
REVERSA66 = (MIGRACIONES / "0066_reversa_bids02_retiro_anuncios.sql").read_text(encoding="utf-8")
REVERSA68 = (MIGRACIONES / "0068_reversa_bids02_sellos.sql").read_text(encoding="utf-8")
REVERSA69 = (MIGRACIONES / "0069_reversa_bids02_retiro_intencion.sql").read_text(encoding="utf-8")
ORDEN66 = ORDEN + (
    "0064_bids02_grupo_tipo.sql",
    "0065_bids02_impulso.sql",
    "0066_bids02_retiro_anuncios.sql",
)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _anuncio(conn, lid, external="ad-1"):
    camp = _entidad(conn, "amazon_mx", "campaign", f"c-{external}")
    ag = _entidad(conn, "amazon_mx", "ad_group", f"ag-{external}", parent=camp)
    return _entidad(conn, "amazon_mx", "product_ad", external, parent=ag, listing_id=lid)


def _retiro(conn, entidad, *, impulso=None, motivo="sin_pedidos", modo="pausa"):
    return conn.execute(
        "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, retirado_el, actor) VALUES ('amazon_mx'::platform, %s, %s, 'retiro-h',"
        " %s, %s, 'ad-1', 'ag-1', 'c-1', 'SS-1', 'B0AAAAAAAA', 'reversa', now(), 'dueno')"
        " RETURNING id",
        (motivo, modo, impulso, entidad),
    ).fetchone()[0]


def _db_i4(prefijo):
    class _Db:
        def __enter__(self):
            self._ctx = db_fabrica(prefijo)
            conn = self._ctx.__enter__()
            conn.execute(SQL64)
            conn.execute(SQL65)
            conn.execute(SQL66)
            conn.execute(SQL68)
            conn.execute(SQL69)
            return conn

        def __exit__(self, *exc):
            return self._ctx.__exit__(*exc)

    return _Db()


@_skip_db
def test_retiro_y_reposicion_derivan_retirado():
    """I.4: el retiro se inserta y la vista dice no repuesto; con la fila
    en reposicion dice repuesto."""
    with _db_i4("orbit_i4_ok") as conn:
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        ret = _retiro(conn, ent)
        assert conn.execute(
            "SELECT repuesto FROM v_anuncio_retirado WHERE id = %s", (ret,)
        ).fetchone() == (False,)
        conn.execute(
            "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo, repuesto_el,"
            " actor) VALUES (%s, 'ad-2', 'PAUSED', now(), 'dueno')",
            (ret,),
        )
        assert conn.execute(
            "SELECT repuesto FROM v_anuncio_retirado WHERE id = %s", (ret,)
        ).fetchone() == (True,)


@_skip_db
def test_segundo_retiro_abierto_se_rechaza_y_repuesto_acepta_nuevo():
    """I.4: un segundo retiro de un anuncio con retiro abierto se
    rechaza; ya repuesto acepta un retiro nuevo."""
    with _db_i4("orbit_i4_abierto") as conn:
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        ret = _retiro(conn, ent)
        with pytest.raises(psycopg.errors.RaiseException, match="retiro abierto"):
            _retiro(conn, ent)
        conn.execute(
            "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo, repuesto_el,"
            " actor) VALUES (%s, 'ad-2', 'PAUSED', now(), 'dueno')",
            (ret,),
        )
        assert _retiro(conn, ent) != ret


@_skip_db
def test_modo_motivo_y_coherencia_se_rechazan():
    """I.4: modo o motivo fuera de vocabulario, y motivo 'impulso' sin
    impulso_id, se rechazan."""
    with _db_i4("orbit_i4_vocab") as conn:
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        with pytest.raises(psycopg.errors.CheckViolation):
            _retiro(conn, ent, modo="borrar")
        with pytest.raises(psycopg.errors.CheckViolation):
            _retiro(conn, ent, motivo="capricho")
        with pytest.raises(psycopg.errors.CheckViolation):
            _retiro(conn, ent, motivo="impulso")


def test_migracion_sin_motivo_in():
    """I.4: la 0066 no contiene `motivo IN (` ni en comentarios: el pin
    de MOTIVOS_SIN_APLICAR mira la ultima migracion con ese texto."""
    assert "motivo IN (" not in SQL66


@_skip_db
def test_nadie_hace_update_ni_delete_en_retiro():
    """I.4: anuncio_retiro es append-only total (fila = retirado; la
    intencion vive en 0069): UPDATE y DELETE se rechazan."""
    with _db_i4("orbit_i4_inmut") as conn:
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        ret = _retiro(conn, ent)
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE anuncio_retiro SET modo = 'archivo' WHERE id = %s", (ret,))
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM anuncio_retiro WHERE id = %s", (ret,))


@_skip_db
def test_reversa_aborta_con_fila_y_sin_fila_restaura():
    """I.4 + R09-r4 (0069, 0068): las reversas corren en orden inverso;
    la 69 aborta con intenciones pendientes; cerradas, quita la tabla
    y la 68/66 abortan con fila / restauran sin filas."""
    with _db_i4("orbit_i4_rev") as conn:
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        _retiro(conn, ent)
        iid = conn.execute(
            "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro,"
            " impulso_id, ad_entity_id, ad_id_externo, ad_group_externo, campana_externa,"
            " sku, asin, reversa, actor, creada_el) VALUES ('amazon_mx', 'sin_pedidos',"
            " 'pausa', 'retiro-h', NULL, %s, 'ad-1', 'ag-1', 'c-1', 'SS-1', 'B0AAAAAAAA',"
            " 'reversa', 'dueno', now()) RETURNING id",
            (ent,),
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException, match="pendientes"):
            conn.execute(REVERSA69)
        conn.rollback()
        conn.execute(
            "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = 'x' WHERE id = %s",
            (iid,),
        )
        conn.execute(REVERSA69)
        conn.execute(REVERSA68)
        with pytest.raises(psycopg.errors.RaiseException, match="tiene filas"):
            conn.execute(REVERSA66)
        conn.rollback()
        conn.execute("TRUNCATE anuncio_reposicion, anuncio_retiro")
        conn.execute(REVERSA66)
        assert conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'anuncio_retiro'"
        ).fetchone() == (0,)


@_skip_db
def test_motivo_impulso_exige_impulso_id():
    """I.4: motivo 'impulso' con su impulso_id se inserta."""
    with _db_i4("orbit_i4_coh") as conn:
        pid, lid = _producto(conn)
        lote = _lote(conn, "fabrica-i4-1")
        grupo = _grupo(conn, lote)
        imp = _impulso(conn, lote, grupo, pid, lid)
        ent = _anuncio(conn, lid)
        assert _retiro(conn, ent, impulso=imp, motivo="impulso") is not None


@_skip_db
def test_r09r3_nadie_hace_update_ni_delete_en_reposicion():
    """R09 B12 (M06/M07): UPDATE y DELETE se rechazan en reposicion."""
    with _db_i4("orbit_i4_r3inm") as conn:
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        ret = _retiro(conn, ent)
        conn.execute(
            "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo, repuesto_el,"
            " actor) VALUES (%s, 'ad-2', 'PAUSED', now(), 'dueno')",
            (ret,),
        )
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute(
                "UPDATE anuncio_reposicion SET estado_nuevo = 'ENABLED' WHERE retiro_id = %s",
                (ret,),
            )
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM anuncio_reposicion WHERE retiro_id = %s", (ret,))
