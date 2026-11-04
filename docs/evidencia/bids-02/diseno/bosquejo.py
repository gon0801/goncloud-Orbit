# ruff: noqa: E501  (evidencia congelada del diseno de BIDS 02: bosquejo de tipos, no codigo vivo)
"""Bosquejo del runner A: politica de bid `niveles_v3`, impulso de productos, estructura, ingesta y pantallas.

SOLO DISENO: tipos, firmas y fronteras de modulo. Los cuerpos levantan NotImplementedError y la logica
delicada va en pseudocodigo. Un solo archivo para leerlo de corrido; cada bloque `# ==== app/... ====` es un
modulo del repo (ninguno pasa de 900 lineas). Las constantes llevan su origen: (A1..A7) son los prototipos de
`prototipos/RESULTADOS.md`; (P1..P6) los de la base; (dueno) decisiones del 2026-10-09.

Flujo de una hoja, leyendo solo tipos y firmas:

    lee_plataforma(conn, ...) -> LecturasPlataforma        (IO, una vez por plataforma)
    LecturasPlataforma.caso(conn, hoja...) -> CasoHoja      (IO minima, por hoja)
    decide(CasoHoja) -> Veredicto                           (pura; toda la politica vive aqui)
    CasoHoja.como_json() -> decision.inputs["caso"]         (freeze)
    decide(CasoHoja.desde_json(inputs["caso"])) -> Veredicto (replay: la misma funcion, cero recalculo)
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

Plataforma = Literal["amazon_mx", "amazon_us"]
Moneda = Literal["MXN", "USD"]

# =====================================================================================================
# ==== app/optimizer/caso.py  (PURO: los datos de una decision de bid. Estructuras primero.) ==========
# =====================================================================================================

POLITICA_BID = "niveles_v3"  # se congela en inputs.politica; el replay despacha por esta clave
ESQUEMA_PESOS = "dos_tramos_v1"  # reciente D-50..D-10 peso 1; antiguo D-90..D-51 peso 1/2 (A5)
PESO_ANTIGUO = Decimal("0.5")
DIAS_MADUREZ = 10  # misma regla 6 que cortes (windows.DIAS_MADUREZ_CORTES): un numero, una fuente
DIAS_TRAMO_RECIENTE = 41
DIAS_LOOKBACK = 90  # windows.LOOKBACK_EVIDENCIA


@dataclass(frozen=True)
class Tramo:
    """Sumas de UN nivel (hoja, ad group o cuenta) en UN tramo de edad de la ventana madura.
    Enteros y Decimal crudos, tal como se midieron. None = desconocido (regla 3): una metrica con
    alguna observacion NULL o negativa en el tramo viaja None y quien la consuma se abstiene.
    Tramo sin filas = ceros MEDIDOS solo si la plataforma tuvo ingesta esos dias (lo decide la
    frontera de lectura, no la politica)."""

    clics: int | None
    pedidos: int | None
    venta: Decimal | None
    gasto: Decimal | None
    impresiones: int | None


@dataclass(frozen=True)
class EvidenciaNivel:
    """Ventana madura D-90..D-10 de un nivel, en dos tramos de edad. Los pesos NO se guardan aplicados:
    se congelan los crudos por tramo y el esquema; `pedidos_pesados` y compania derivan (un numero, una
    fuente). Los pedidos pesados pueden salir en medios: la politica los redondea EN CONTRA del
    movimiento (hacia arriba para recortar, hacia abajo para subir) y asi la gamma sigue entera."""

    reciente: Tramo
    antiguo: Tramo
    esquema: str = ESQUEMA_PESOS

    def pedidos_pesados(self) -> Decimal | None:
        raise NotImplementedError

    def clics_pesados(self) -> Decimal | None:
        raise NotImplementedError

    def venta_pesada(self) -> Decimal | None:
        raise NotImplementedError

    def gasto_pesado(self) -> Decimal | None:
        raise NotImplementedError

    def pedidos_crudos(self) -> int | None:
        """Suma sin pesos de los dos tramos (la usan 'tiene pedidos', el minimo de 3 del grupo y la previa)."""
        raise NotImplementedError

    def gasto_crudo(self) -> Decimal | None:
        """Dinero gastado en la ventana, sin pesos: el dinero gastado es dinero gastado."""
        raise NotImplementedError


OrigenCambio = Literal["motor", "regreso_por_desplome", "regreso_del_dueno", "ajuste_de_campana"]


@dataclass(frozen=True)
class CambioBid:
    """Un cambio de bid YA aplicado y verificado en Amazon. Sale de la vista `v_cambio_bid`
    (bids del motor confirmados en ciclo live + reversas confirmadas del ledger + ajustes de campana de
    ubicacion o de estrategia confirmados, con origen `ajuste_de_campana` y bid_antes == bid_despues: el bid
    no cambio pero el precio del clic si, asi que R2 espera y la escalera de precio no proyecta)."""

    fecha: dt.date  # fecha UTC de la confirmacion
    bid_antes: Decimal
    bid_despues: Decimal
    origen: OrigenCambio

    @property
    def direccion(self) -> Literal[-1, 1]:
        raise NotImplementedError


@dataclass(frozen=True)
class EfectoCambio:
    """Lo que paso alrededor del ULTIMO cambio de la hoja. Se mide contra el calendario de ingesta de la
    plataforma, nunca contra las filas de la propia hoja: una hoja que se queda sin impresiones deja de
    tener filas, y eso aqui cuenta como cero medido (mismo criterio que v_entidad_inerte)."""

    dias_post: int  # dias con ingesta de la plataforma posteriores al cambio (hasta ayer)
    impresiones_pre7: int | None  # 7 dias antes del cambio
    clics_pre7: int | None
    impresiones_post: int | None  # primeros min(7, dias_post) dias despues
    dias_post_trafico: int
    clics_post: (
        int | None
    )  # desde el dia siguiente al cambio hasta D-3 (precio medido al bid nuevo)
    gasto_post: Decimal | None
    clics_pre: int | None  # tramo de precio anterior al cambio (hasta 30 dias o el cambio previo)
    gasto_pre: Decimal | None
    vendia: bool | None  # >= 1 pedido en los 90 dias previos al cambio

    def razon_trafico(self) -> Decimal | None:
        """(impresiones_post / dias_post_trafico) / (impresiones_pre7 / 7). None sin base o sin dias."""
        raise NotImplementedError


@dataclass(frozen=True)
class Trayectoria:
    """Historia de bids de la hoja en los ultimos 90 dias (ascendente) y el efecto del ultimo cambio.
    Sustituye a tres piezas de hoy: cooldown de 7 dias, D.2 y cpc_vigente."""

    cambios: tuple[CambioBid, ...]
    efecto: EfectoCambio | None  # None si y solo si `cambios` esta vacio

    @property
    def ultimo(self) -> CambioBid | None:
        raise NotImplementedError

    @property
    def piso_aprendido(self) -> Decimal | None:
        """El bid mas alto DESDE el que hubo que regresar (por desplome o por el dueno): a ese bid la
        hoja perdio su trafico. Derivado de `cambios`; no se guarda en ninguna tabla."""
        raise NotImplementedError


@dataclass(frozen=True)
class PrecioVentana:
    """Gasto y clics de la hoja en D-33..D-3: el CPC cuando no hubo cambio de bid reciente."""

    gasto: Decimal | None
    clics: int | None


@dataclass(frozen=True)
class EconomiaPlataforma:
    """Lo que el ciclo resuelve UNA vez por plataforma y la politica consume resuelto."""

    moneda: Moneda
    equilibrio_acos_pct: (
        Decimal | None
    )  # margen neto antes de publicidad; None si el target no vino del margen
    gasto_para_concluir: (
        Decimal  # 350 MXN / 36 USD (dueno); la MISMA cifra es el tope de aprendizaje
    )
    confianza_recorte: Decimal  # settings existentes (0.80)
    confianza_subida: Decimal  # settings existentes (0.70)


@dataclass(frozen=True)
class Economia:
    """EconomiaPlataforma mas el target de ESTA hoja (la escalera de goals lo resuelve por hoja)."""

    plataforma: EconomiaPlataforma
    target_acos_pct: Decimal


@dataclass(frozen=True)
class BidVigente:
    valor: Decimal | None  # None: la hoja hereda el bid del ad group (clausulas de auto)
    moneda: str | None
    piso: Decimal
    techo: Decimal


@dataclass(frozen=True)
class InsumosPausa:
    """Lo que PAUSE consume hoy, sin cambios (bid._decide_pause): ventana de cortes y umbrales resueltos."""

    cortes: Any  # windows.AgregadoMetricas | None
    umbral_clics: int
    gasto_minimo: Decimal
    expected_clicks: Decimal | None
    politica_economica: str | None


@dataclass(frozen=True)
class CasoHoja:
    """TODO lo que decide el bid de una hoja en un ciclo. Es el argumento de `decide` y, serializado, es
    `decision.inputs["caso"]`. Invariante: `decide` no lee nada fuera de este valor."""

    plataforma: Plataforma
    hoja_id: int
    ad_group_id: int | None
    bid: BidVigente
    economia: Economia
    propia: EvidenciaNivel | None  # None = hoja sin filas en la ventana madura
    pedidos_inmaduros: int | None  # pedidos ya vistos en D-9..D-1
    grupo: EvidenciaNivel | None  # hojas ACTIVAS del ad group, incluida esta
    cuenta: EvidenciaNivel | None  # hojas ACTIVAS de la plataforma (raiz de la previa)
    precio: PrecioVentana
    trayectoria: Trayectoria
    pausa: InsumosPausa
    ventana_desde: dt.date
    ventana_hasta: dt.date
    observado_al: dt.datetime | None

    def como_json(self) -> dict:
        """Freeze. Decimal como string, fechas ISO, None explicito. Unica serializacion del caso."""
        raise NotImplementedError

    @classmethod
    def desde_json(cls, datos: dict) -> CasoHoja:
        """Inversa exacta de `como_json`; clave ausente o tipo ajeno levanta ValueError (frontera)."""
        raise NotImplementedError


# =====================================================================================================
# ==== app/optimizer/politica.py  (PURO: la unica politica de bid. Sin IO, sin reloj, sin float.) =====
# =====================================================================================================

# Bandas y pasos: los de hoy (docs/CONTEXTO.md), sin tocar.
MULT_BAJA_FUERTE = Decimal("1.35")
MULT_BAJA_SUAVE = Decimal("1.15")
MULT_SUBIDA = Decimal("0.85")
PASO_BAJA_FUERTE = Decimal("-0.25")
PASO_BAJA_SUAVE = Decimal("-0.12")
PASO_SUBIDA = Decimal("0.15")
# Efecto del ultimo cambio.
DIAS_EFECTO = 7  # dias para leer un cambio; es el cooldown de hoy con otro nombre (goals.COOLDOWN)
DIAS_GUARDA = (
    4  # la guarda de desplome ya discrimina con 4 dias: 20 % tras recorte contra 6 % natural (A4)
)
DIAS_SALIDA = 14  # invertir direccion sin 20 clics nuevos: salida por tiempo (elegido, 2 x DIAS_EFECTO; sin medir)
CLICS_NUEVOS = 20  # clics al bid nuevo para repetir direccion (evidencia.MIN_CLICS_CPC de hoy)
RAZON_DESPLOME = Decimal("0.30")  # P2 y A4
RAZON_RETIENE = Decimal(
    "0.70"
)  # un recorte "no costo trafico": natural p25 = 0.69 (P2); 26 % natural por debajo (A4)
RAZON_CRECE = Decimal(
    "1.10"
)  # una subida "trajo trafico": 67 % tras subir contra 30 % natural (A7)
VOLUMEN_IMPRESIONES = 1000  # sin este filtro la guarda no distingue (P2)
VOLUMEN_CLICS = 20
PEDIDOS_GRUPO = 3  # minimo para que el ad group tenga veredicto (P1)
MATERIALIDAD_GRUPO = Decimal(
    "0.25"
)  # una hoja sin pedidos hereda el recorte de su grupo solo si ya gasto
# 1/4 del gasto para concluir: por debajo carga 0.4 % a 2 % del gasto de un grupo que sangra y recortarla
# no ahorra nada (injerto de runner-c, prototipo p_c2)

# Motivos (vocabulario cerrado; los dashboards los traducen en MOTIVOS_ES).
MOTIVO_REGRESO_DESPLOME = "regreso_por_desplome"
MOTIVO_ESPERANDO_EFECTO = "esperando_efecto"
MOTIVO_PIERDE_DINERO = "pierde_dinero"
MOTIVO_PIERDE_DINERO_FUERTE = "pierde_dinero_fuerte"
MOTIVO_GRUPO_SANGRA_VENDEDORA = "grupo_sangra_vendedora"
MOTIVO_VENDE_DENTRO_DEL_MARGEN = "vende_dentro_del_margen"
MOTIVO_BAJO_TARGET = "bajo_target"
MOTIVO_AZAR_LO_EXPLICA = "azar_lo_explica"
MOTIVO_VENTA_RECIENTE = "venta_reciente"
MOTIVO_GASTO_SIN_VENTA = "gasto_sin_venta"
MOTIVO_GASTO_SIN_VENTA_DOBLE = "gasto_sin_venta_doble"
MOTIVO_SIN_GASTO = "sin_gasto"
MOTIVO_GRUPO_SANGRA = "grupo_sangra"
MOTIVO_HOJA_DELGADA = "hoja_delgada_sin_dinero_que_mover"
MOTIVO_GRUPO_CUMPLE = "grupo_cumple"
MOTIVO_SIN_EVIDENCIA = "sin_evidencia"
MOTIVO_ESPERANDO_PRECIO_MEDIDO = "esperando_precio_medido"
MOTIVO_SIN_CLICS_NUEVOS = "sin_clics_nuevos"
MOTIVO_RECORTE_COSTO_TRAFICO = "recorte_costo_trafico"
MOTIVO_SUBIDA_SIN_TRAFICO = "subida_sin_trafico"
MOTIVO_PISO_APRENDIDO = "piso_aprendido"
MOTIVO_DATO_FALTANTE = "dato_faltante"
MOTIVO_SIN_PRECIO = "sin_precio"

NivelQueJuzga = Literal["hoja", "ad_group"]
EstadoGrupo = Literal["sangra", "cumple", "sin_veredicto"]
FuentePrecio = Literal["medido_tras_cambio", "proyectado", "ventana", "ventana_madura"]


@dataclass(frozen=True)
class Mantener:
    """No se mueve el bid. `motivo` dice por que, en el vocabulario cerrado. No se persiste: cuenta en
    notes.skips como hoy."""

    motivo: str


@dataclass(frozen=True)
class Mover:
    """Recorte o subida de un paso. Invariantes de construccion (solo `_paso` lo crea): `bid_nuevo` esta en
    [piso, techo], el cambio respeta el clamp por decision y nunca cruza el piso aprendido."""

    factor: Decimal
    bid_nuevo: Decimal
    motivo: str
    nivel: NivelQueJuzga


@dataclass(frozen=True)
class Regresar:
    """Volver al bid anterior al ultimo recorte. No lleva factor: el destino es un bid ya conocido."""

    bid_nuevo: Decimal
    motivo: str


@dataclass(frozen=True)
class Pausar:
    motivo: str


Veredicto = Mantener | Mover | Regresar | Pausar


@dataclass(frozen=True)
class Estimacion:
    """Derivada auditable (NO se congela: `estima` la re-deriva exacta del caso). La leen el tablero de
    ruido y la ficha de una decision."""

    cpc: Decimal | None
    fuente_precio: FuentePrecio | None
    acos_hoy_pct: Decimal | None  # CPC al bid de hoy / (conversion x ticket de la ventana pesada)
    p_sobre_equilibrio: Decimal | None
    p_sobre_suave: Decimal | None
    p_sobre_fuerte: Decimal | None
    p_bajo_target: Decimal | None
    estado_grupo: EstadoGrupo
    acos_grupo_pct: Decimal | None


def decide(caso: CasoHoja) -> Veredicto:
    """La politica completa. Orden sellado (tabla-decision.md lleva el mismo numero de regla):

    0. PAUSE: identico a hoy (cortes maduros, umbral adaptativo, piso de costo). Gana a todo.

    1. Efecto del ultimo cambio (trayectoria):
       R1  ultimo cambio = recorte del motor, la hoja vendia, tenia volumen (>= 1000 impresiones o >= 20
           clics en 7 dias), dias_post >= DIAS_GUARDA y razon_trafico < RAZON_DESPLOME
           -> Regresar(ultimo.bid_antes, regreso_por_desplome)
       R2  dias_post < DIAS_EFECTO -> Mantener(esperando_efecto)

    2. Juicio. cpc = _precio_de_hoy(caso); grupo = estado_grupo(caso.grupo, caso.economia)
       Con pedidos en la ventana madura (propia.pedidos_crudos >= 1):
         p_sobre(limite) = gamma_p(ceil(pedidos_pesados) + 1, clics_pesados * cpc / (limite * ticket_propio))
            previa PLANA: solo datos propios; pedidos redondeados hacia arriba (beneficio de la duda).
         R3  p_sobre(equilibrio) >= confianza_recorte -> candidato de recorte, nivel hoja:
             fuerte (-25 %) si ademas p_sobre(1.35 x target) >= confianza; si no -12 %.
         R4  si no, y acos_hoy > target y grupo == "sangra" -> candidato -12 %, nivel ad_group
             (una vendedora que no pierde dinero con certeza NUNCA recibe -25 %).
         R5  si no, y p_sobre(1.15 x target) >= confianza -> Mantener(vende_dentro_del_margen)
         R6  si no: p_bajo = gamma_q(floor(pedidos_pesados) + 1,
                                     (clics_pesados + 1/cvr_previa) * cpc / (0.85 * target * ticket_encogido))
             la previa (cuenta -> resto del ad group, K = 1, enteros sin pesos) FRENA la subida;
             p_bajo >= confianza_subida -> candidato +15 %, nivel hoja.
         R7  si no -> Mantener(azar_lo_explica)
       Sin pedidos:
         R8  pedidos_inmaduros >= 1 -> Mantener(venta_reciente)
         R9  gasto_crudo >= 2 x gasto_para_concluir -> candidato -25 %; >= 1 x -> candidato -12 % (nivel hoja)
         R10 gasto_crudo == 0 -> Mantener(sin_gasto)
         R11 grupo == "sangra" y gasto_crudo >= MATERIALIDAD_GRUPO x gasto_para_concluir
             -> candidato -12 %, nivel ad_group
         R11b grupo == "sangra" y gasto_crudo menor -> Mantener(hoja_delgada_sin_dinero_que_mover)
         R12 grupo == "cumple" -> Mantener(grupo_cumple)
         R13 si no -> Mantener(sin_evidencia)
       Cualquier metrica None que la regla consuma -> Mantener(dato_faltante). Sin CPC -> Mantener(sin_precio).

    3. Un movimiento se repite solo si el anterior tuvo el efecto buscado (sobre el candidato):
       R14 direccion contraria al ultimo cambio y clics_post < CLICS_NUEVOS y dias_post < DIAS_SALIDA
           -> Mantener(esperando_precio_medido)
       R15 misma direccion y clics_post < CLICS_NUEVOS -> Mantener(sin_clics_nuevos)
       R16 otro recorte y razon_trafico < RAZON_RETIENE -> Mantener(recorte_costo_trafico)
           otra subida y razon_trafico < RAZON_CRECE -> Mantener(subida_sin_trafico)
       R17 recorte que dejaria el bid en o debajo de trayectoria.piso_aprendido -> Mantener(piso_aprendido)

    4. _paso(caso.bid, candidato): clamps de hoy (factor en [-30 %, +20 %], [piso, techo], delta minimo,
       rango_bloquea_ajuste) -> Mover, o Mantener con el motivo del clamp.
    """
    raise NotImplementedError


def estima(caso: CasoHoja) -> Estimacion:
    """Los numeros detras del veredicto, para pantallas y auditoria. Misma aritmetica que `decide`
    (comparten los privados); un caso sin datos devuelve campos None, jamas levanta."""
    raise NotImplementedError


def estado_grupo(grupo: EvidenciaNivel | None, economia: Economia) -> EstadoGrupo:
    """Regla de grupo del dueno, una sola definicion (P1): con >= PEDIDOS_GRUPO pedidos crudos,
    'sangra' si gasto_pesado > 1.15 x target x venta_pesada y 'cumple' si gasto_pesado <= target x
    venta_pesada; con 0 pedidos, 'sangra' si gasto_crudo >= gasto_para_concluir. Todo lo demas, incluido
    None o veneno, 'sin_veredicto'. Comparacion por multiplicacion, nunca division."""
    raise NotImplementedError


def _precio_de_hoy(caso: CasoHoja) -> tuple[Decimal | None, FuentePrecio | None]:
    """CPC al bid vigente. Escalera (A4 c: error mediano 6 % al escalar; 26 % con el CPC del ad group):
    1. clics_post >= CLICS_NUEVOS            -> gasto_post / clics_post            (medido_tras_cambio)
    2. hay cambio y clics_pre > 0            -> (gasto_pre / clics_pre) * bid_despues / bid_antes (proyectado)
    3. sin cambio y precio.clics > 0         -> precio.gasto / precio.clics       (ventana)
    4. propia con clics                      -> gasto_crudo / clics crudos        (ventana_madura)
    5. nada                                  -> (None, None)"""
    raise NotImplementedError


def _paso(
    bid: BidVigente,
    factor: Decimal,
    motivo: str,
    nivel: NivelQueJuzga,
    piso_aprendido: Decimal | None,
) -> Mover | Mantener:
    raise NotImplementedError


# =====================================================================================================
# ==== app/optimizer/eras.py + app/optimizer/replay.py  (PURO: historia congelada y despacho) =========
# =====================================================================================================


def decide_bid_era_bandas(inputs: dict) -> tuple[str | None, Decimal | None, str | None]:
    """eras.py. La politica `bandas_v1` tal como decidio hasta el corte, MUDADA aqui sin cambios (bandas,
    regla A' de cero ventas, pisos historicos REPLAY_*). Solo la importa replay.py; un candado de
    arquitectura prohibe importarla desde app/cycle.py. Devuelve (kind, new_value, value_currency)."""
    raise NotImplementedError


def reproduce(inputs: dict) -> tuple[str | None, Decimal | None, str | None]:
    """replay.py. Re-decide UNA decision desde lo congelado. Despacho por era, sin valores vigentes:
      inputs.get("politica") == POLITICA_BID -> decide(CasoHoja.desde_json(inputs["caso"]))
      inputs["motor"] == "bid" sin esa clave  -> decide_bid_era_bandas(inputs)
      inputs["motor"] == "hygiene"            -> como hoy
    Las claves `evidencia_v2` y `bandas_v1` de filas viejas quedan como dato: ya no se rejuegan."""
    raise NotImplementedError


# =====================================================================================================
# ==== app/lecturas_caso.py  (IO de SOLO lectura; arma CasoHoja. Fuera de app/optimizer a proposito) ==
# =====================================================================================================


class Conexion(Protocol):
    def execute(self, sql: str, params: tuple = ()) -> Any: ...


@dataclass(frozen=True)
class LecturasPlataforma:
    """Todo lo que se lee UNA vez por plataforma y ciclo, dentro del snapshot REPEATABLE READ:
    - tramos por hoja (una consulta con FILTER por tramo; sustituye a _SQL_CONVERSION_GRANO),
    - roll-up en Python a ad group y cuenta SOLO con hojas activas (v_hoja_activa),
    - cambios de bid de 90 dias de toda la plataforma (v_cambio_bid),
    - calendario de dias con ingesta de la plataforma (para contar dias observados).
    Los dicts son privados: el unico acceso es `caso`."""

    plataforma: Plataforma
    decidido_el: dt.datetime
    economia: EconomiaPlataforma
    _por_hoja: dict[int, EvidenciaNivel]
    _por_grupo: dict[int, EvidenciaNivel]
    _cuenta: EvidenciaNivel | None
    _cambios: dict[int, tuple[CambioBid, ...]]
    _precio: dict[int, PrecioVentana]
    _inmaduros: dict[int, int | None]
    _dias_con_ingesta: frozenset[dt.date]

    def caso(
        self,
        conn: Conexion,
        *,
        hoja_id: int,
        ad_group_id: int | None,
        bid: BidVigente,
        target_acos_pct: Decimal,
        pausa: InsumosPausa,
    ) -> CasoHoja:
        """Arma el caso de una hoja. Cero consultas si la hoja no cambio de bid en 90 dias; UNA consulta
        (`_SQL_EFECTO_CAMBIO`, con FILTER por lado del cambio) si cambio. La ventana de cortes de la pausa
        la sigue leyendo cycle con windows (llega dentro de `pausa`)."""
        raise NotImplementedError


def lee_plataforma(
    conn: Conexion,
    plataforma: Plataforma,
    decidido_el: dt.datetime,
    *,
    economia: EconomiaPlataforma,
    visto_el: dt.datetime | None = None,
) -> LecturasPlataforma:
    """Cuatro consultas por plataforma. `visto_el` None = ultima observacion (v_metric_latest, el ciclo).
    `visto_el` con fecha = lectura bitemporal "como se veia" (metrics_as_of): es lo que usa el rejuego de
    ciclos pasados, y por eso la regla 5 (append-only) alcanza para validar el mismo dia."""
    raise NotImplementedError


# =====================================================================================================
# ==== app/cycle.py  (orquestador: lo unico que cambia en el camino por hoja) ==========================
# =====================================================================================================


def procesa_decisora(
    conn: Conexion, *, fila: tuple, lecturas: LecturasPlataforma, comunes: dict
) -> None:
    """Sustituye el cuerpo de `_procesa_decisora` desde la linea de `windows.ventanas_entidad` hasta el
    freeze (hoy unas 250 lineas con v1, v2, fallback y dos contrafactuales). Los gates de goal, ancestros,
    veto e inerte quedan como estan, antes de esto.

        caso = lecturas.caso(conn, hoja_id=..., ad_group_id=..., bid=..., target_acos_pct=target, pausa=...)
        veredicto = decide(caso)
        if isinstance(veredicto, Mantener):
            contadores.skips_entidad[veredicto.motivo] += 1
            return
        pendientes.append(pendiente_bid(caso, veredicto, modo=..., goal=..., decidido_el=...))

    cycle.py no conoce umbrales, niveles ni guardas: solo arma, llama y guarda."""
    raise NotImplementedError


def pendiente_bid(
    caso: CasoHoja,
    veredicto: Mover | Regresar | Pausar,
    *,
    modo: str,
    goal: Any,
    decidido_el: dt.datetime,
) -> Any:
    """Fila de `decision` lista para insertar. inputs = {"motor": "bid", "politica": POLITICA_BID,
    "platform", "modo", "motivo", "factor", "nivel", "caso": caso.como_json(), mas las claves que leen
    pantallas existentes (target_acos_pct_usado, target_procedencia, goal, bid_actual, corte)}."""
    raise NotImplementedError


# =====================================================================================================
# ==== app/optimizer/goals.py  (target: el paso de 0.5 frena solo las BAJADAS; interruptor de politica) =
# =====================================================================================================

MARGEN_PASO_MAX_BAJADA = Decimal(
    "0.5"
)  # el unico paso que queda: la razon escrita del 0.5 protege contra
# bajadas bruscas y vaiven (spec 2026-09-03, G2 3.6). Una subida solo afloja y aplica de una vez.
PASO_POLITICA = (
    "asimetrico_v1"  # se guarda en optimizer_cycle.notes.target para leer bien las anclas viejas
)
POLITICA_BID_VIGENTE = "niveles_v3"


def resuelve_target_margen(
    medicion: Any,
    fraccion: Decimal | None,
    hoy: dt.date,
    ancla_aplicado_pct: Decimal | None,
    setting: Decimal | None = None,
    dias_min: int = 60,
) -> Any:
    """Igual que hoy (mismas guardas de validez del dato, misma banda [10, 45]) salvo el paso:
      derivado >= ancla -> aplicado = derivado recortado a la banda, de una vez.
          Es el salto de US de 20.79 a 28.34 en el ciclo siguiente, y sigue al margen despues.
      derivado <  ancla -> aplicado = max(derivado recortado, ancla - MARGEN_PASO_MAX_BAJADA).
      sin ancla -> derivado recortado a la banda.
    Sin migracion y sin goal fijo. La convergencia al setting por dato invalido no cambia.
    (Injerto de runner-b y runner-c; sustituye al ancla con fraccion del runner-a.)"""
    raise NotImplementedError


def politica_bid_desde_settings(
    settings: dict, plataforma: Plataforma
) -> Literal["niveles_v3"] | None:
    """Interruptor fail-closed de la clave que ya existe, `ads_bid_politica_<platform>`:
      ausente              -> None: el motor NO mueve bids en esa plataforma (PAUSE, negative y harvest
                              siguen). El ciclo cuenta cada hoja con motivo `politica_apagada`.
      'niveles_v3'         -> la politica nueva decide.
      cualquier otro valor -> ValueError: el ciclo falla cerrado, como hoy.
    El dia del despliegue la clave esta ausente: desplegar ya detiene los recortes. Apagar es quitarla.
    (Injerto de runner-c.)"""
    raise NotImplementedError


def gasto_para_concluir_desde_settings(settings: dict, plataforma: Plataforma) -> Decimal:
    """Lector fail-closed de `ads_gasto_para_concluir_<platform>`: ausente = 350 MXN / 36 USD (dueno,
    2026-10-09); presente y no numerico o <= 0 = ValueError. UNICA fuente del numero: lo consumen la regla
    R9 del motor y el tope por defecto del impulso."""
    raise NotImplementedError


# =====================================================================================================
# ==== app/apply.py  (unico dueno del cliente de escritura: regreso pedido por el dueno) ==============
# =====================================================================================================


@dataclass(frozen=True)
class RegresoHecho:
    hoja_id: int
    bid_antes: Decimal
    bid_ahora: Decimal
    moneda: str
    decision_revertida_id: int

    def como_dict(self) -> dict:
        raise NotImplementedError


class SinRachaDeRecortes(Exception):
    """La hoja no tiene recortes vigentes que regresar (409)."""


def regreso_del_dueno(conn: Conexion, *, hoja_id: int, actor: str) -> RegresoHecho:
    """Regresa el bid de la hoja al que tenia antes de su racha vigente de recortes. Reusa
    `reversa_manual(tipo="bid")` sobre la PRIMERA decision de la racha (ledger pre-HTTP, readback, exenta de
    cupo): no hay escritura nueva a Amazon. El motor lo ve en `v_cambio_bid` como un cambio con origen
    `regreso_del_dueno`: espera su efecto, exige clics nuevos para volver a recortar y aprende el piso.
    Idempotente: una racha ya regresada levanta ReversaYaHecha (409), nunca escribe dos veces."""
    raise NotImplementedError


def regreso_del_dueno_todas(
    conn: Conexion, *, plataforma: Plataforma, confirmacion: str, actor: str
) -> tuple[RegresoHecho | str, ...]:
    """Boton "Regresar todas" (decision D6). Relee la pantalla de danadas, exige la confirmacion literal
    `REGRESAR N KEYWORDS` con el N vigente (409 si la lista cambio) y llama a `regreso_del_dueno` hoja por
    hoja. Una falla no detiene a las demas: en su lugar devuelve el motivo en texto. Idempotente."""
    raise NotImplementedError


def prioridad_bajo_cupo(veredicto_kind: str, motivo: str) -> int:
    """Orden cuando el cupo diario no alcanza: 0 regresos, 1 recortes con evidencia propia, 2 subidas,
    3 recortes heredados del ad group. Dentro de cada nivel, mas gasto primero. (Hoy: recortes antes que
    subidas, y las subidas se descartan primero.)"""
    raise NotImplementedError


# =====================================================================================================
# ==== app/api_write.py y app/api_fabrica.py  (cuerpos de las rutas nuevas; validan en la frontera) ===
# =====================================================================================================


@dataclass(frozen=True)
class CuerpoRegresarBid:
    """POST /api/ads-optimizer/bid/regresar. En el repo es un modelo pydantic. La ruta entra a la lista
    sellada SUPERFICIE_ADS_OPTIMIZER (tests/test_api.py)."""

    hoja_id: int
    actor: str


@dataclass(frozen=True)
class CuerpoLanzarImpulso:
    """POST /api/fabrica/impulso/lanzar. `confirmacion` debe igualar VistaPreviaImpulso.confirmacion_esperada."""

    solicitud: SolicitudImpulso
    huella: str
    confirmacion: str
    actor: str


# =====================================================================================================
# ==== app/impulso.py  (PURO: plan, tope y veredicto del impulso de UN producto) ======================
# =====================================================================================================

ROLES_IMPULSO = (
    "category_exact",
    "auto_discovery",
)  # orden de creacion: el destino del harvest nace primero
PLAZO_VEREDICTO_DIAS = 14
IMPRESIONES_MINIMAS = 2000  # las que piden 20 clics al CTR de la cuenta: 2,266 MX / 1,532 US (A6)
CLICS_MINIMOS = 20
DIAS_DE_PRESUPUESTO = (
    14  # presupuesto diario del impulso = tope / 14. Amazon puede gastar en un dia hasta
)
# 2 veces el presupuesto diario (documentado; medido el 2026-10-09: hasta 2.1 veces en esta cuenta), asi que
# un dia vale como maximo tope / 7. Ver PRUEBAS.md, prueba 1.
DIAS_DE_MARGEN_DEL_VIGIA = 2  # el vigia pausa cuando faltan 2 presupuestos diarios para el tope

EstadoVeredicto = Literal[
    "en_curso", "vende", "clics_sin_ventas", "impresiones_sin_clics", "sin_impresiones"
]
AccionVeredicto = Literal["seguir", "pausar", "graduar"]


@dataclass(frozen=True)
class TopeAprendizaje:
    """Tope por producto. `monto` por defecto = EconomiaPlataforma.gasto_para_concluir (350 MXN, 36 USD):
    es el mismo numero que el motor exige antes de recortar sin venta, y por eso al agotarlo SI se puede
    concluir. `presupuesto_diario` es del impulso ENTERO: cada una de sus dos campanas lleva la mitad como
    presupuesto diario en Amazon, que lo hace cumplir en tiempo real."""

    monto: Decimal
    moneda: Moneda
    presupuesto_diario: Decimal
    plazo_dias: int = PLAZO_VEREDICTO_DIAS


@dataclass(frozen=True)
class AnuncioARetirar:
    """Un product ad del producto en una bolsa vieja. Lista EXPLICITA que el dueno ve en la vista previa."""

    ad_id_externo: str
    ad_group_externo: str
    campana_externa: str
    ad_group_nombre: str
    sku: str
    asin: str


@dataclass(frozen=True)
class PlanImpulso:
    """Un producto, dos campanas (exact y automatica), un ad group en cada una. Un plan = un lote de la
    fabrica = un grupo de dos roles. La subfamilia es la etiqueta que el dueno eligio; solo comparte
    biblioteca de keywords y negatives, no presupuesto."""

    plataforma: Plataforma
    listing_id: int
    product_id: int
    seller_sku: str
    asin: str
    subfamilia: str
    fecha: dt.date
    tope: TopeAprendizaje
    bid_por_rol: dict[str, Decimal]
    keywords_exact: tuple[str, ...]
    negatives: tuple[str, ...]
    retiros: tuple[AnuncioARetirar, ...]

    def como_json(self) -> dict:
        """schema_version 3. Dinero como string. La huella es el sha256 del JSON canonico."""
        raise NotImplementedError

    def huella(self) -> str:
        raise NotImplementedError


def pasos_del_impulso(plan: PlanImpulso) -> list[Any]:
    """Pasos de creacion por rol en orden ROLES_IMPULSO, construidos con `fabrica_plan.pasos_del_rol` (los
    payloads de campana, ad group, product ad, keywords y negatives ya estan sellados por sonda). Lo unico
    nuevo en fabrica_plan es que el plan canonico declara `roles` (ausente = los cinco de hoy)."""
    raise NotImplementedError


@dataclass(frozen=True)
class LecturaImpulso:
    """Acumulado del impulso desde su lanzamiento, medido en las filas `kind='campaign'` de sus dos
    campanas (una campana = un producto: sin reparto ni doble conteo). `dias_observados` cuenta dias con
    ingesta de la plataforma; un dia observado sin fila es cero medido."""

    corte: dt.date
    dias_observados: int
    impresiones: int | None
    clics: int | None
    gasto: Decimal | None
    pedidos: int | None


@dataclass(frozen=True)
class VeredictoImpulso:
    estado: EstadoVeredicto
    final: bool
    accion: AccionVeredicto


def veredicto_impulso(lectura: LecturaImpulso, tope: TopeAprendizaje) -> VeredictoImpulso:
    """Primer caso que aplique:
    algun dato None                                   -> en_curso, no final, seguir (regla 3)
    pedidos >= 1                                       -> vende, final, graduar
    gasto >= tope.monto - DIAS_DE_MARGEN_DEL_VIGIA x tope.presupuesto_diario -> clics_sin_ventas, final, pausar
        (se para dos presupuestos diarios antes: el gasto llega con un dia de retraso y ese dia puede
        costar el doble del presupuesto)
    dias_observados < tope.plazo_dias                  -> en_curso, no final, seguir
    impresiones < IMPRESIONES_MINIMAS                  -> sin_impresiones, final, pausar
    clics < CLICS_MINIMOS                              -> impresiones_sin_clics, final, pausar
    si no (clics sin agotar el tope)                   -> en_curso, no final, seguir"""
    raise NotImplementedError


# =====================================================================================================
# ==== app/impulso_io.py  (IO: vista previa, lanzamiento y vigia diario) ===============================
# =====================================================================================================


@dataclass(frozen=True)
class SolicitudImpulso:
    plataforma: Plataforma
    listing_ids: tuple[int, ...]  # los productos que el dueno marco
    subfamilia_por_listing: dict[int, str]
    tope_monto: Decimal | None = None  # None = el de la plataforma


@dataclass(frozen=True)
class VistaPreviaImpulso:
    planes: tuple[PlanImpulso, ...]
    huella: str  # sha256 de las huellas de los planes, ordenadas
    confirmacion_esperada: str  # p. ej. "LANZAR IMPULSO DE 3 PRODUCTOS"
    presupuesto_diario_total: Decimal
    tope_total: Decimal
    moneda: Moneda

    def como_dict(self) -> dict:
        raise NotImplementedError


@dataclass(frozen=True)
class ImpulsoLanzado:
    impulso_id: int
    lote: str
    estado_lote: str


@dataclass(frozen=True)
class AccionVigia:
    impulso_id: int
    lectura: LecturaImpulso
    veredicto: VeredictoImpulso
    hecho: Literal["nada", "pausado", "graduado", "retiro_de_bolsas"]


class PausadorDeCampanas(Protocol):
    """La fabrica es la unica que pausa y reanuda campanas (PUT /sp/campaigns con `state`, ya sellado en
    `--desarmar` y en reactiva_campanas). El vigia no conoce HTTP."""

    def pausa(self, lote: str) -> None: ...

    def reanuda(self, lote: str) -> None: ...


def previsualiza(conn: Conexion, solicitud: SolicitudImpulso) -> VistaPreviaImpulso:
    """Solo lecturas mas la sugerencia de bids de Amazon (una llamada por producto). Cero mutaciones."""
    raise NotImplementedError


def lanza(
    conn: Conexion, solicitud: SolicitudImpulso, *, huella: str, confirmacion: str, actor: str
) -> list[ImpulsoLanzado]:
    """Re-planifica, compara la huella (409 si cambio) y crea UN lote por producto con el ejecutor de la
    fabrica. Un producto que falla no detiene a los demas. Los goals nacen en modo `shadow`: el motor
    decide y guarda, y no toca nada mientras el producto aprende. Idempotente por huella de plan."""
    raise NotImplementedError


def vigila(conn: Conexion, pausador: PausadorDeCampanas, *, hoy: dt.date) -> list[AccionVigia]:
    """Cron diario, despues de la ingesta y antes del ciclo. Por cada impulso vivo:
      1. lee LecturaImpulso y la guarda (impulso_lectura, append-only; la misma corrida dos veces no duplica:
         clave (impulso_id, corte)).
      2. veredicto_impulso(lectura, tope).
      3. accion: pausar -> pausador.pausa(lote); graduar -> goals del grupo a `live`; seguir -> nada.
      4. si es el primer dia con impresiones > 0 y hay retiros pendientes -> retiro_anuncios.ejecuta
         (primero nace lo nuevo, despues sale de lo viejo: el producto nunca queda sin anuncio).
    Si muere a medias, la siguiente corrida relee y termina: cada paso se decide por lo ya guardado."""
    raise NotImplementedError


# =====================================================================================================
# ==== app/retiro_anuncios.py  (IO: sacar un producto de bolsas viejas, con reposicion) ===============
# =====================================================================================================

MotivoRetiro = Literal["impulso", "sin_pedidos", "subfamilia"]


@dataclass(frozen=True)
class PlanRetiro:
    plataforma: Plataforma
    motivo: MotivoRetiro
    anuncios: tuple[AnuncioARetirar, ...]
    huella: str


def planea_retiro(
    conn: Conexion,
    plataforma: Plataforma,
    *,
    listing_ids: tuple[int, ...],
    motivo: MotivoRetiro,
    conservar_grupo_id: int | None,
) -> PlanRetiro:
    """Los product ads ENABLED de esos listings (por listing_id; los anuncios sin listing se buscan por
    ASIN) en ad groups que NO son del grupo a conservar. Solo SELECT."""
    raise NotImplementedError


def ejecuta_retiro(conn: Conexion, plan: PlanRetiro, *, go: str) -> int:
    """Archiva con `app.ads.archivar.archivar_anuncios` (lista explicita, readback, linea de reposicion)
    y deja una fila por anuncio en `anuncio_retiro`. Devuelve cuantos retiro. Reanudable: salta los que
    ya tienen fila."""
    raise NotImplementedError


def repone(conn: Conexion, *, retiro_ids: tuple[int, ...], go: str) -> int:
    """La reversa (regla 7): `reponer_anuncios` recrea el anuncio en su ad group y guarda
    `anuncio_reposicion`. El anuncio repuesto tiene otro adId; las metricas por producto no dependen de el."""
    raise NotImplementedError


# =====================================================================================================
# ==== app/ads/campana_config.py y app/ads/placements.py  (ingesta: ver lo invisible) =================
# =====================================================================================================


@dataclass(frozen=True)
class ConfigCampana:
    """Lo que `/sp/campaigns/list` ya devuelve y hoy se tira. Dominio, no payload: el parseo del wire
    queda detras de `config_de_payload`. Presupuesto con la moneda del perfil (igual que los bids)."""

    campana_externa: str
    presupuesto_diario: Decimal | None
    moneda: Moneda
    estrategia_puja: str | None  # LEGACY_FOR_SALES | AUTO_FOR_SALES | MANUAL | otro texto tal cual
    ajuste_top_pct: int | None
    ajuste_resto_pct: int | None
    ajuste_producto_pct: int | None
    fuera_de_amazon: str | None = (
        None  # `offAmazonSettings` ya parseado; None = sin configurar (hoy, las 246)
    )


def config_de_payload(item: dict, moneda: Moneda) -> ConfigCampana | None:
    """Frontera: valida forma y tipos; item ilegible = None y se cuenta como skip (jamas ceros)."""
    raise NotImplementedError


def guarda_config(
    conn: Conexion, plataforma: Plataforma, configs: list[ConfigCampana], observado_el: dt.datetime
) -> int:
    """Append-only y solo cuando algo cambio respecto de la ultima fila de esa campana: la tabla es la
    HISTORIA de la configuracion. Corre dentro del sync de estructura (misma llamada, cero crons nuevos)."""
    raise NotImplementedError


def sync_placements(conn: Conexion, cliente: Any, *, desde: dt.date, hasta: dt.date) -> int:
    """Corrida PROPIA (otro `source`, otro cron) para que un fallo aqui no tire los cuatro reportes
    principales. Reporte spCampaigns agrupado por campaignPlacement. Sustituto declarado si Amazon no
    acepta DAILY: un pedido SUMMARY por dia (pregunta abierta 6)."""
    raise NotImplementedError


# =====================================================================================================
# ==== Pantallas "puro contrato": dataclasses + como_dict() + lecturas SOLO SELECT =====================
# =====================================================================================================


@dataclass(frozen=True)
class HojaDanada:
    hoja_id: int
    nombre: str  # linea_entidad existente
    campana: str
    recortes: int
    bid_antes: Decimal
    bid_hoy: Decimal
    moneda: Moneda
    clics_antes_14d: int
    clics_ahora_14d: int
    pedidos_antes_90d: int
    venta_antes_90d: Decimal
    ya_regresada: bool


@dataclass(frozen=True)
class PantallaDanadas:
    """Keywords que vendian y perdieron su trafico tras un recorte. Orden: mas venta previa primero."""

    plataforma: Plataforma
    calculado_el: dt.datetime
    hojas: tuple[HojaDanada, ...]

    def como_dict(self) -> dict:
        raise NotImplementedError


def lee_danadas(conn: Conexion, *, plataforma: Plataforma) -> PantallaDanadas:
    """app/pantalla_danadas.py. Hoja activa, con racha vigente de recortes (v_cambio_bid), >= 1 pedido en
    los 90 dias previos a la racha y clics de los ultimos 14 dias legibles menores a 30 % de los 14 dias
    previos a la racha. Hoy: 6 hojas en MX y 3 en US (A6)."""
    raise NotImplementedError


TipoCampana = Literal["exact", "phrase", "broad", "automatica", "product_targeting"]
Ubicacion = Literal[
    "arriba_de_busqueda", "resto_de_busqueda", "paginas_de_producto", "fuera_de_amazon"
]
EstrategiaPuja = Literal["solo_hacia_abajo", "arriba_y_abajo", "fija", "otra"]


@dataclass(frozen=True)
class FilaUbicacion:
    """Una ubicacion del anuncio en un mercado (reporte spCampaigns por campaignPlacement, DAILY; sonda del
    2026-10-09). `gasta_sin_vender` = gasto >= gasto_para_concluir y pedidos == 0: la fila se marca."""

    ubicacion: Ubicacion
    gasto: Decimal | None
    clics: int | None
    pedidos: int | None
    venta: Decimal | None
    cpc: Decimal | None
    conversion_pct: Decimal | None
    acos_pct: Decimal | None  # None con venta 0: se pinta "sin ventas"
    parte_del_gasto_pct: Decimal | None
    gasta_sin_vender: bool


@dataclass(frozen=True)
class FilaCampana:
    """Configuracion vigente de una campana activa y como usa su presupuesto. Dinero en la moneda del perfil."""

    campana_id: int
    nombre: str
    presupuesto_diario: Decimal | None
    gasto_medio_diario: Decimal | None
    uso_presupuesto_pct: Decimal | None
    estrategia: EstrategiaPuja | None
    ajustes_ubicacion: tuple[tuple[Ubicacion, int], ...]  # (ubicacion, porcentaje)
    gasto_fuera_de_amazon: Decimal | None
    avisos: tuple[
        str, ...
    ]  # frases ya en lenguaje llano: "Este presupuesto no limita", "Se queda sin presupuesto"


@dataclass(frozen=True)
class FilaTipo:
    tipo: TipoCampana
    gasto: Decimal | None
    pedidos: int | None
    venta: Decimal | None
    acos_pct: Decimal | None  # None con venta 0: se pinta "sin ventas", nunca division
    parte_del_gasto_pct: Decimal | None


@dataclass(frozen=True)
class PantallaDinero:
    """Donde poner el dinero: una tabla por mercado, solo lo que hoy esta encendido. Nunca suma MX y US."""

    plataforma: Plataforma
    moneda: Moneda
    desde: dt.date
    hasta: dt.date
    filas: tuple[FilaTipo, ...]
    total: FilaTipo
    por_ubicacion: tuple[FilaUbicacion, ...] = ()  # ultimos 30 dias del reporte por placement
    por_campana: tuple[FilaCampana, ...] = ()  # configuracion vigente + uso del presupuesto
    target_acos_pct: Decimal | None

    def como_dict(self) -> dict:
        raise NotImplementedError


def lee_dinero(conn: Conexion, *, plataforma: Plataforma, dias: int = 90) -> PantallaDinero:
    """app/pantalla_dinero.py. Suma HOJAS activas (v_hoja_activa trae el tipo: primero AUTO/MANUAL de la
    campana, despues match type o product target). Nunca filas de campana: no doble conteo."""
    raise NotImplementedError


@dataclass(frozen=True)
class ProductoCandidato:
    listing_id: int
    product_id: int
    nombre: str
    asin: str
    pedidos_3_meses: int  # por cualquier canal (ledger, unidades: en US el ledger esta en MXN)
    clics_ads_60d: int | None
    ad_groups_donde_esta: int
    impulso_vigente: bool


@dataclass(frozen=True)
class ImpulsoVisto:
    impulso_id: int
    nombre: str
    lectura: LecturaImpulso | None
    veredicto: VeredictoImpulso | None
    tope: TopeAprendizaje


@dataclass(frozen=True)
class PantallaProductos:
    """Tres secciones: candidatos a impulsar, impulsos en curso con su veredicto, y productos sin un
    pedido por ningun canal en 11 meses (el dueno elige: impulsar aparte o retirar de las bolsas)."""

    plataforma: Plataforma
    candidatos: tuple[ProductoCandidato, ...]
    impulsos: tuple[ImpulsoVisto, ...]
    sin_pedidos_11_meses: tuple[ProductoCandidato, ...]
    anuncios_sin_producto: int  # anuncios que no se pueden juzgar: se dice cuantos, no se esconden

    def como_dict(self) -> dict:
        raise NotImplementedError


def lee_productos(conn: Conexion, *, plataforma: Plataforma) -> PantallaProductos:
    """app/pantalla_productos.py. Candidato = anuncio activo, >= 2 pedidos por cualquier canal en los
    ultimos 3 meses y < 20 clics de ads en 60 dias. Hoy: 10 en MX y 5 en US (P6)."""
    raise NotImplementedError


@dataclass(frozen=True)
class PantallaRuido:
    """Tablero de ruido. Todo sale de decision.inputs.caso y de notes.skips. Sin grupo de control: el dueno
    lo descarto el 2026-10-09 (no quiere un sistema de pruebas sobre su cuenta)."""

    plataforma: Plataforma
    ciclos: tuple[dict, ...]  # por ciclo: decisiones por motivo y nivel, abstenciones por motivo
    encogimiento: tuple[dict, ...]  # por hoja: bid de hoy entre el bid mas antiguo de 90 dias
    antes_y_despues: (
        dict  # por mercado: gasto, pedidos, venta y ACoS de los 30 dias previos al encendido
    )
    # contra los dias transcurridos desde entonces, solo campanas activas (comparacion simple, no causal)

    def como_dict(self) -> dict:
        raise NotImplementedError


def lee_ruido(conn: Conexion, *, plataforma: Plataforma, dias: int = 30) -> PantallaRuido:
    """app/pantalla_ruido.py. Trae las filas de `decision` y pasa cada `inputs["caso"]` por
    CasoHoja.desde_json y `estima`: ninguna consulta SQL conoce la forma del JSON congelado (la forma vive
    solo en caso.py)."""
    raise NotImplementedError


# =====================================================================================================
# ==== app/campana_ajustes.py (PURO) + app/apply.py: tres ajustes de campana que aprueba el dueno =====
# ==== app/avisos_campana.py (PURO): avisos diarios. Agregado tras las pruebas del 2026-10-09 ==========
# =====================================================================================================


@dataclass(frozen=True)
class LimitarFueraDeAmazon:
    """Limita el gasto fuera de Amazon de la campana. El valor exacto de `offAmazonSettings` se fija con su
    sonda de escritura: hoy llega vacio en las 246 campanas y sus valores permitidos no estan verificados."""


@dataclass(frozen=True)
class CambiarAjusteUbicacion:
    ubicacion: Ubicacion
    porcentaje: int  # 0 a 900 (tope documentado por Amazon); 0 quita el ajuste


@dataclass(frozen=True)
class CambiarPresupuesto:
    presupuesto_diario: Decimal  # en la moneda del perfil; > 0


AjusteCampana = LimitarFueraDeAmazon | CambiarAjusteUbicacion | CambiarPresupuesto


@dataclass(frozen=True)
class PlanAjuste:
    """Lo que el dueno ve antes de confirmar. `antes` y `despues` son la configuracion completa de la campana
    (no un parche): asi el regreso es aplicar `antes`, y un cambio parcial nunca borra otro ajuste."""

    campana_id: int
    plataforma: Plataforma
    ajuste: AjusteCampana
    antes: ConfigCampana
    despues: ConfigCampana
    frase: str  # "Orbit cambia el ajuste de paginas de producto de +40 % a 0 % en AU2 - Category Phrase - US"

    def huella(self) -> str:
        raise NotImplementedError


def planea_ajuste(vigente: ConfigCampana, ajuste: AjusteCampana) -> PlanAjuste:
    """Pura. Rechaza con ValueError un ajuste que no cambia nada, un porcentaje fuera de 0..900 o un
    presupuesto <= 0. No lee la base ni llama a Amazon."""
    raise NotImplementedError


@dataclass(frozen=True)
class AjusteHecho:
    ajuste_id: int
    campana_id: int
    confirmado_el: dt.datetime

    def como_dict(self) -> dict:
        raise NotImplementedError


def aplica_ajuste_campana(
    conn: Conexion, plan: PlanAjuste, *, huella: str, confirmacion: str, actor: str
) -> AjusteHecho:
    """app/apply.py (unico dueno del cliente de escritura). Compara la huella (409 si la configuracion cambio
    desde la vista previa), inserta la fila `campana_ajuste` ANTES del HTTP, hace UN `PUT /sp/campaigns` para
    UNA campana, relee la campana y confirma la fila solo si el readback coincide con `plan.despues`.
    Idempotente por huella. No pasa por la cola de veto: el dueno ya confirmo."""
    raise NotImplementedError


def regresa_ajuste_campana(conn: Conexion, *, ajuste_id: int, actor: str) -> AjusteHecho:
    """El mismo camino con `antes` como destino. Una vez por ajuste."""
    raise NotImplementedError


@dataclass(frozen=True)
class Aviso:
    plataforma: Plataforma
    clase: Literal["ubicacion_gasta_sin_vender", "campana_sin_presupuesto", "presupuesto_expuesto"]
    campana_id: int | None
    frase: str


def avisos_del_dia(
    por_ubicacion: tuple[FilaUbicacion, ...],
    por_campana: tuple[FilaCampana, ...],
    *,
    gasto_para_concluir: Decimal,
) -> tuple[Aviso, ...]:
    """Pura. Tres avisos: ubicacion con gasto >= gasto_para_concluir y 0 pedidos en 30 dias; campana con uso
    >= 90 % de su presupuesto en 5 de los ultimos 7 dias; presupuesto diario > 10 x gasto medio diario."""
    raise NotImplementedError


# =====================================================================================================
# ==== app/pantalla_busquedas.py  (contrato: "Busquedas que gastan sin vender", decision D11) =========
# =====================================================================================================

PALABRAS_VACIAS = frozenset(
    [
        "de",
        "la",
        "el",
        "los",
        "las",
        "para",
        "y",
        "con",
        "en",
        "un",
        "una",
        "por",
        "del",
        "a",
        "al",
        "que",
        "the",
        "of",
        "for",
        "and",
        "to",
        "in",
        "on",
        "with",
    ]
)


@dataclass(frozen=True)
class FilaBusqueda:
    """Una busqueda dentro de su ad group, ya colapsada a la ultima observacion por fecha y sumada en la
    ventana. Es la entrada de `resume_busquedas`: sin texto de Amazon mas alla del termino."""

    ad_group_id: int
    campana: str
    termino: str
    es_asin: bool
    clics: int | None
    gasto: Decimal | None
    pedidos: int | None


@dataclass(frozen=True)
class FilaAsinAjeno:
    asin: str
    clics: int
    gasto: Decimal
    campanas: tuple[str, ...]
    es_propio: bool  # el ASIN es de un producto del dueno: se marca, no se esconde


@dataclass(frozen=True)
class FilaPalabra:
    palabra: str  # una o dos palabras
    busquedas: int
    clics: int
    gasto: Decimal
    ejemplos: tuple[str, ...]  # hasta tres busquedas, las de mas gasto


@dataclass(frozen=True)
class ReferenciaAzar:
    """Parte del gasto en busquedas sin pedido: lo visto contra lo que daria el azar con la conversion de la
    cuenta. None si la cuenta no tiene pedidos o clics en la ventana (regla 3: no se inventa conversion)."""

    visto_pct: Decimal | None
    azar_pct: Decimal | None


@dataclass(frozen=True)
class PantallaBusquedas:
    plataforma: Plataforma
    moneda: Moneda
    desde: dt.date
    hasta: dt.date
    referencia: ReferenciaAzar
    asins: tuple[FilaAsinAjeno, ...]
    palabras: tuple[FilaPalabra, ...]
    asins_a_medio_camino: int
    palabras_a_medio_camino: int

    def como_dict(self) -> dict:
        raise NotImplementedError


def resume_busquedas(
    filas: tuple[FilaBusqueda, ...], *, gasto_para_concluir: Decimal, asins_propios: frozenset[str]
) -> tuple[ReferenciaAzar, tuple[FilaAsinAjeno, ...], tuple[FilaPalabra, ...], int, int]:
    """Pura. Una fila con clics, gasto o pedidos None no suma y no cuenta como cero (regla 3).
      ASIN: suma por ASIN en toda la cuenta; entra con pedidos == 0 y gasto >= gasto_para_concluir.
      Palabras: de una (sin PALABRAS_VACIAS y con mas de dos letras) y de dos, sobre busquedas que no son
        ASIN; entra si NINGUNA busqueda que la contiene tuvo pedido y la suma de gasto >= gasto_para_concluir.
      A medio camino: mismo criterio con gasto en [gasto_para_concluir / 2, gasto_para_concluir).
      Azar: por (ad_group_id, termino), gasto x (1 - p) ** clics, con p = pedidos / clics de todas las filas.
    Orden: mas gasto primero. Prototipo: prototipos/p7_busquedas.py."""
    raise NotImplementedError


def lee_busquedas(conn: Conexion, *, plataforma: Plataforma, hoy: dt.date) -> PantallaBusquedas:
    """SELECT de solo lectura. Ultima observacion de search_term_observation por (ad_entity_id, search_term,
    metric_date) en los 90 dias que terminan en hoy - 3; solo campanas ENABLED; ASIN propios de `listing`."""
    raise NotImplementedError


# =====================================================================================================
# ==== tools/rejuega_niveles.py  (criterio de encendido: mide lo que pasaria en vivo, el mismo dia) ===
# =====================================================================================================


@dataclass(frozen=True)
class InformeRejuego:
    plataforma: Plataforma
    desde: dt.date
    hasta: dt.date
    por_motivo: dict[str, int]
    recortes_del_motor_viejo: int
    recortes_que_se_repetirian: int
    hojas_recortadas: int
    parte_del_gasto_recortada_pct: Decimal
    regresos: int
    recortes_sobre_danadas: (
        int  # debe ser 0: ninguna hoja de la pantalla de danadas vuelve a recortarse
    )

    deterministas_pct: Decimal = Decimal(
        "0"
    )  # re-decidir cada CasoHoja congelado da el mismo veredicto
    invariantes_rotos: int = (
        0  # recortes que no salen de R3, R4, R9 u R11, o que violan R2, R15, R16, R17
    )
    cobertura_pct: Decimal = Decimal(
        "0"
    )  # hojas elegibles con CasoHoja completo (sin dato_faltante)
    vendedoras_que_recortaria: tuple[
        tuple[int, str], ...
    ] = ()  # (hoja_id, motivo), para revisar a mano

    def cumple(self) -> bool:
        """Criterio de encendido, mecanico y del mismo dia (injerto de runner-c sobre el rejuego de runner-a):
          1. deterministas_pct == 100
          2. invariantes_rotos == 0
          3. cobertura_pct >= 95 (si no, la ingesta no esta sana y no se enciende)
          4. recortes_sobre_danadas == 0
        `parte_del_gasto_recortada_pct` y `vendedoras_que_recortaria` no deciden: se le muestran al dueno
        antes de encender (pregunta abierta 6 fija si quiere un piso para US)."""
        raise NotImplementedError


def rejuega(
    conn: Conexion, *, plataforma: Plataforma, desde: dt.date, hasta: dt.date
) -> InformeRejuego:
    """Por cada ciclo live de [desde, hasta]: lee_plataforma(..., visto_el=ciclo.started_at) y decide cada
    hoja con su historia REAL de bids hasta ese dia (rejuego a un paso). No espera ciclos nuevos."""
    raise NotImplementedError
