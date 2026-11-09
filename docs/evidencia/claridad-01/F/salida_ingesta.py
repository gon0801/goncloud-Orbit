"""CLARIDAD 01 F: la ingesta de estimacion desplegada sale 1 y nombra la plataforma cuando la copia
del bridge trae una oferta de Amazon de mas de 6 h, y sale 0 cuando todas estan frescas.

Corre `app.estimacion_ingest.main` y `plataformas_sin_oferta_fresca` reales con filas del bridge
armadas aqui (la copia de las 12:45 del 9-nov: MX 12:42, US 06:36). La conexion, la ingesta y el
cliente de MeLi se sustituyen, asi que no lee ni escribe la base, el bridge ni Amazon. Se pasa por
stdin: `docker exec -i orbit-app-1 python - < salida_ingesta.py`. Sale 0 solo si las dos corridas
se distinguen.
"""

import contextlib
import io
import os
import sys
from datetime import UTC, datetime

import app.estimacion_ingest as ei
from app.estimacion_insumos import FilaOfertaBridge

AHORA = datetime(2026, 11, 9, 12, 45, 10, tzinfo=UTC)
MX = FilaOfertaBridge(
    "SS-MX", "B0MX", "A1AM78C64UM0Y8", "amazon_mx", 116.0, "DEFAULT", "2026-11-09T12:42:39+00:00"
)
US_VIEJA = FilaOfertaBridge(
    "SS-US", "B0US", "ATVPDKIKX0DER", "amazon_us", 19.99, "DEFAULT", "2026-11-09T06:36:52+00:00"
)
US_FRESCA = FilaOfertaBridge(
    "SS-US", "B0US", "ATVPDKIKX0DER", "amazon_us", 19.99, "DEFAULT", "2026-11-09T12:48:44+00:00"
)


def _sin_meli():
    raise OSError("sin credenciales en la prueba")


class _Conexion:
    def close(self):
        pass


def corre(filas):
    def ingesta(_conn, **_kw):
        return ei.ResultadoIngestaEstimacion(
            run_id=0,
            ok=True,
            rows_written=0,
            rows_skipped=0,
            skip_reason=None,
            ofertas_nuevas=0,
            ofertas_reutilizadas=0,
            fees_nuevos=0,
            fees_reutilizados=0,
            fees_error=0,
            escenarios_nuevos=0,
            escenarios_reutilizados=0,
            sin_oferta_fresca=ei.plataformas_sin_oferta_fresca(
                filas, {"amazon_mx", "amazon_us"}, AHORA
            ),
        )

    ei.connect = lambda _dsn: _Conexion()
    ei.ejecutar_ingesta = ingesta
    ei.MeliCredentials = type("SinMeli", (), {"from_secrets_dir": staticmethod(_sin_meli)})
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
        rc = ei.main(["--sqlite", sys.executable])
    return rc, salida.getvalue()


os.environ["ORBIT_DSN_INGEST"] = "sin-uso"
rc_vieja, texto_vieja = corre((MX, US_VIEJA))
rc_fresca, texto_fresca = corre((MX, US_FRESCA))
print(f"copia con US de las 06:36: rc={rc_vieja}")
print(f"copia con US de las 12:48: rc={rc_fresca}")
aviso = "sin oferta fresca: amazon_us (la mas nueva del bridge: 2026-11-09T06:36:52+00:00)"
ok = (
    rc_vieja == 1
    and aviso in texto_vieja
    and "amazon_mx" not in texto_vieja
    and rc_fresca == 0
    and "sin oferta fresca" not in texto_fresca
)
if not ok:
    print(texto_vieja + texto_fresca)
sys.exit(0 if ok else 1)
