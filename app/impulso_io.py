"""Vista previa, lanzamiento y vigia diario del impulso (BIDS 02, I.3).

Fachada publica estable que re-exporta los tres submodulos de Ola D2:
- `app.impulso_preview`: tipos, `PausadorFabrica`, `previsualiza`, `sugeridor_amazon`.
- `app.impulso_lanza`: `lanza`, sellado y reconciliacion de lotes huerfanos al lanzar.
- `app.impulso_vigia`: `vigila`, `reconcilia_huerfanos` y lectura diaria.
"""

from __future__ import annotations

from app.impulso_lanza import (  # noqa: F401
    _SQL_GRUPO_DE_LOTE,
    _SQL_IMPULSO_DE_LOTE,
    _SQL_PENDIENTES_LISTING,
    _SQL_PRODUCTO_DE_LISTING,
    _SQL_VIVO_PRODUCTO,
    _archiva_lote_vacio,
    _archiva_vacios,
    _descarta_huerfano,
    _falla_si_hay_vivo,
    _fallos_por_vivo,
    _lanza_par_nuevo,
    _plan_coincide_con_lote,
    _sella_impulso,
    _tiene_externals,
    lanza,
)
from app.impulso_preview import (  # noqa: F401
    _SQL_ESTADO_LOTE,
    _SQL_SETTINGS,
    _SQL_VIVO_OTRO_LISTING,
    AccionVigia,
    EjecucionFabrica,
    EjecutorFabrica,
    ErrorImpulso,
    ImpulsoLanzado,
    NoLanzable,
    PausadorDeCampanas,
    PausadorFabrica,
    SolicitudImpulso,
    VistaPreviaImpulso,
    _conecta_propia,
    _exige_acepto_presupuesto,
    _huella_vista,
    _pausador_propio,
    _plan_producto,
    _reactiva_para_sellar,
    aviso_presupuesto_alto,
    lote_de_plan,
    previsualiza,
    sugeridor_amazon,
)
from app.impulso_vigia import (  # noqa: F401
    _REACTIVA_SOLO_VIGIA,
    _RECONCILIA_ANTIGUEDAD_MIN,
    _SQL_CAMPANAS_DE_IMPULSO,
    _SQL_CORTE_PLATAFORMA,
    _SQL_DIAS_CON_INGESTA,
    _SQL_GOALS_DE_IMPULSO,
    _SQL_LOTES_HUERFANOS,
    _SQL_OBSERVADO,
    _SQL_VIVOS,
    _SQL_YA_TUVO_IMPRESIONES,
    _acumula,
    _vigila_uno,
    reconcilia_huerfanos,
    vigila,
)
