"""REPRICING 02: bosquejo sintetizado de tipos y firmas. NO es implementacion.

Acompana a docs/superpowers/specs/2026-10-08-repricing-02-design.md. Base: el
candidato A de la arena (frontera de plataforma dentro del motor), con los
injertos que registra juicio.md. Nada de esto vive en app/: es el contrato que
la implementacion debe cumplir. Todos los cuerpos levantan
`NotImplementedError`; la logica delicada va como pseudocodigo `# TODO`.
Compila (`python -m py_compile bosquejo.py`); no pretende importar desde el repo.

LA FORMA EN CINCO LINEAS
  1. La corrida recibe UN `Mercado` (puerto) y deja de conocer Amazon.
  2. El puerto es angosto: lee (`catalogo`, `insumos`, `observado`) y toca la
     plataforma (`cotizar`, `precio_vivo`, `escribir`). Devuelve tipos de dominio.
  3. El protocolo de `precio_cambio` (INSERT+COMMIT -> escritura -> sello,
     reversa, cierre) se escribe UNA vez en `app/precio/cambios.py`.
  4. El escenario de margen NO lo produce el puerto: lo produce la estimacion,
     que gana su propio registro de universos (`platform/canal`). El puerto lo lee.
  5. La corrida son tres pasos idempotentes sobre filas persistidas:
     decidir -> compuerta (fusible, apagador, corte) -> aplicar.

USO (lo que escribe quien llama; los tipos de abajo salen de aqui)

  # 1) cron, una linea por grupo de cuota (Amazon en serie; MeLi en paralelo)
  #    10 13 * * *      flock -n /tmp/precio-spapi.lock python -m app.cli precio \
  #                         --platform amazon_mx --platform amazon_us
  #    10 13 * * *      flock -n /tmp/precio-meli.lock  python -m app.cli precio --platform meli
  #    10 15-23/2 * * * (las mismas dos lineas: repaso; no-op salvo retenidas liberadas)
  def _precio(args):                                   # app/cli.py
      for clave in args.platform:                      # en serie dentro del proceso
          with precio_mercados.abrir(clave) as mercado:
              resumen = corrida.correr(conn, mercado, owner=owner, avisar=avisar_precio)
          print(resumen.linea())

  # 2) el dueno siembra en bloque "goal = margen de hoy" desde /precios
  plan = siembra.planear(                              # puro; el mismo camino sirve al CLI
      goals_write.leer_candidatos(conn, precio_mercados.vitrina("amazon_mx"), filtro=SinGoal()),
      pedido=PedidoMargenDeHoy(mode="shadow"),
      banda=config.banda_goal,
  )                                                    # -> PlanGoals con filas, bloqueos y huella
  goals_write.aplicar_plan(conn, plan, huella=cuerpo.huella, confirmados=cuerpo.confirmados,
                           go_literal=None, actor="dueno")   # TODO o NADA, una transaccion

  # 3) salta el fusible y el dueno lo suelta (se mide por universo platform/canal)
  veredicto = compuerta.evaluar(intenciones_fbm, medidas=n, derivas=derivas_fbm, config=config)
  #   Retenida("movimiento_masivo") -> ninguna decision de ESE universo se aplica
  #   hoy; queda `precio_retencion` por decision y sale UN aviso `compuerta`.
  liberaciones.liberar(conn, corrida_id=812, actor="dueno", nota="subi los goals a proposito")
  #   el repaso de las 15:10 aplica las retenidas (compare-and-set contra el precio vivo)

  # 4) reversa en lote por fecha (el apagador de esa plataforma no puede estar en live)
  #    python tools/precio_reversa.py --platform amazon_mx --fecha 2026-10-12
  #    python tools/precio_reversa.py --platform amazon_mx --fecha 2026-10-12 \
  #           --acepto-mutacion-real --huella H --go "revertir 12-oct"
  plan = cambios.planear_reversa(conn, mercado, SeleccionPorFecha("amazon_mx", date(2026, 10, 12)))
  cambios.revertir_lote(conn, mercado, plan, huella=H, go_literal="revertir 12-oct", owner=owner)

MAPA DE MODULOS (cada seccion dice en que archivo aterriza y quien lo posee)
  El ORDEN de construccion lo fija plans/repricing-02.md, no este mapa: el
  paso 0.b pone la corrida detras del `Mercado` con su orden de hoy y con los
  fees partidos; los tres pasos sobre filas guardadas (seccion J) llegan en S.1.

  PURO (candado `test_precio_puro_sin_io`, sin excepciones nuevas)
    A  app/precio/tipos.py       dinero, cuenta, envio con origen, vocabulario      corte 0
    B  app/precio/puerto.py      NUEVO  Vitrina / Mercado y sus tipos               corte 0
    C  app/precio/envio.py       NUEVO  costo de envio por orden y su origen        carril F
    D  app/precio/ventas.py      senal con evidencia + cohorte                      carril S
    E  app/precio/compuerta.py   NUEVO  apagador, fusible, deriva, corte            carril S
    F  app/precio/reglas.py, objetivo.py   lo que cambia de firma                   carril S
    G  app/precio/siembra.py     NUEVO  plan de goals (uno, bloque, margen de hoy)  carril G
    H  app/precio/config.py      todas las claves `precio_*` en un solo lector      corte 0
  CASCARA GENERICA (psycopg si; `app.spapi`, `app.meli`, `httpx` NO: candado nuevo)
    I  app/precio/cambios.py     NUEVO  protocolo de `precio_cambio` y reversa      carril S
    J  app/precio/corrida.py     decidir -> compuerta -> aplicar                    carril S
       app/precio/liberaciones.py NUEVO unico escritor de la liberacion             carril S
    K  app/precio/goals_write.py unico escritor de `precio_goal`                    carril G
    L  app/precio/fuentes.py, cobertura.py   cobertura sobre `Vitrina`       corte 0b (+S)
  ESTIMACION (frontera de abajo: que deja una venta)
    M  app/estimacion_universo.py NUEVO  registro de universos                      corte 0 + F
    N  app/estimacion_reader.py   `leer_cuenta`: unico que conoce el JSON           corte 0
    O  app/envio_muestras.py      NUEVO  job que escribe `precio_envio_muestra`     carril F
    P  app/ledger_atribucion.py   NUEVO  venta -> publicacion                       corte 0 + F/M
  ADAPTADORES (uno por plataforma; unico lugar que conoce el cable)
    Q  app/spapi/precio_mercado.py NUEVO  MercadoAmazon                             corte 0b + F
    R  app/meli/                   NUEVO  MercadoMeli, escritor, catalogo, ledger   carril M
    S  app/precio_mercados.py      NUEVO  raiz de composicion                       corte 0
  SUPERFICIES
    T  app/api_precios.py, app/api_precios_write.py  NUEVOS                         carril G
    U  app/notifica.py            tipos de aviso nuevos                             carril S
    V  tools/precio_reversa.py, tools/precio_goal.py  despachadores delgados        S / G
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

# --- Nombres que YA existen en el repo y se reutilizan SIN cambio -------------
# from app.estimacion_venta import DetalleFee        # arbol normalizado de fees
# from app.precio.tipos import (ObservacionPricing, CambioPrevio, HistorialMargen,
#                               PideCotizacion, exigir_decimal, motivo_permitido)
# from app.precio.objetivo import (precio_estrella, margen_a_precio,
#                                  techo_centavo, piso_centavo, ErrorObjetivo)
# from app.api_write import exige_token, ConexionEscritura
# from app.reputacion_clientes import ClienteMeli, MeliCredentials   # sigue GET-only
# from app.spapi.write_client import SpapiWriteClient                # sin cambio
Conexion = Any  # psycopg.Connection
DetalleFee = Any  # app.estimacion_venta.DetalleFee (existe)
ObservacionPricing = Any  # app.precio.tipos.ObservacionPricing (existe)
CambioPrevio = Any  # existe; gana `sin_efecto: bool` (ver seccion F)
HistorialMargen = Any  # existe, sin cambio
PideCotizacion = Any  # existe, sin cambio


# =============================================================================
# A. app/precio/tipos.py -- PURO. Dinero, cuenta, envio con origen, vocabulario
# =============================================================================

Plataforma = Literal["amazon_mx", "amazon_us", "meli"]
Canal = Literal["fba", "fbm", "meli"]  # `meli` entra al enum en la migracion 0053
Moneda = Literal["MXN", "USD"]
Modo = Literal["off", "shadow", "live"]  # el goal solo guarda shadow|live
ModoDecision = Literal["shadow", "live"]


class MonedasMezcladas(TypeError):
    """Se intento operar dos importes de moneda distinta. Nunca se convierte solo."""


@dataclass(frozen=True)
class Importe:
    """Dinero con su moneda (regla 4). EXISTE; gana aritmetica que revienta.

    Invariante: `valor` es `Decimal` finito. `a + b`, `a - b` y las
    comparaciones levantan `MonedasMezcladas` si las monedas difieren. No hay
    `__mul__` entre importes ni conversion implicita: la unica via de una
    moneda a otra es `convertir`, que devuelve un `Convertido` con su tasa.
    """

    valor: Decimal
    moneda: Moneda

    def __add__(self, otro: Importe) -> Importe:
        raise NotImplementedError

    def __sub__(self, otro: Importe) -> Importe:
        raise NotImplementedError

    def escalar(self, factor: Decimal) -> Importe:
        raise NotImplementedError


@dataclass(frozen=True)
class TasaFx:
    """Tasa usada, con su fecha y fuente (`fx_resolve`). `quote` por 1 `base`."""

    base: Moneda
    quote: Moneda
    tasa: Decimal
    fecha: date
    fuente: str


@dataclass(frozen=True)
class Convertido:
    """Un importe medido en una moneda y llevado a otra. Guarda las dos puntas.

    Es el unico tipo que cruza monedas. En US: `L` y `C` se miden en MXN y la
    cuenta va en USD. Invariante: `original.moneda != en.moneda`,
    `{tasa.base, tasa.quote} == {original.moneda, en.moneda}`.
    """

    original: Importe
    en: Importe
    tasa: TasaFx


def convertir(importe: Importe, tasa: TasaFx) -> Convertido:
    """La unica puerta entre monedas. Sin tasa no hay llamada: `fx_ausente`."""
    raise NotImplementedError


# --- Envio (L) con origen: un imputado no se puede confundir con un medido ----

OrigenEnvio = Literal["politica", "propio", "familia", "marketplace"]


@dataclass(frozen=True)
class MuestraEnvio:
    """Fila de `precio_envio_muestra` ya leida (evidencia de `L`).

    `valor` va en la moneda en que se midio (MXN en MX y en US). `envios` son
    ordenes de UN solo producto contadas una vez; `donantes` es cuantos
    productos aportaron cuando el origen no es `propio` (None en `propio`).
    """

    id: int
    product_id: int
    plataforma: Plataforma
    canal: Canal
    origen: Literal["propio", "familia", "marketplace"]
    valor: Importe
    envios: int
    donantes: int | None
    ventana: tuple[date, date]
    regla_version: str


@dataclass(frozen=True)
class EnvioIncluido:
    """FBA: la logistica ya va dentro del fee; `L` es la constante de la politica."""

    importe: Importe
    origen: Literal["politica"] = "politica"


@dataclass(frozen=True)
class EnvioPropio:
    """Medido con envios de ESTE producto (mediana por orden; en MX basta uno).

    Invariante (constructor): `muestra.origen == "propio"`.
    """

    importe: Importe  # en moneda de venta
    muestra: MuestraEnvio
    conversion: Convertido | None  # None cuando la moneda de venta es la medida


@dataclass(frozen=True)
class EnvioImputado:
    """NO es de este producto: mediana de hermanos (`familia`) o del marketplace.

    Clase distinta a `EnvioPropio` a proposito: quien quiera tratarlo como
    medido tiene que escribir el `isinstance`. Invariante:
    `muestra.origen in ("familia", "marketplace")` y `muestra.donantes >= 1`.
    """

    importe: Importe
    muestra: MuestraEnvio
    conversion: Convertido | None


Envio = EnvioIncluido | EnvioPropio | EnvioImputado


# --- Fees ya partidos: las reglas puras dejan de saber que es un `ReferralFee` -


@dataclass(frozen=True)
class Fees:
    """Fees a UN precio, ya partidos por el adaptador en parte variable y fija.

    Invariante: `total == variable + fijo` (misma moneda). `variable` es lo que
    escala con el precio (Amazon: el unico `ReferralFee`; MeLi: el cargo
    porcentual); `fijo` es el resto. `impuesto_pendiente` = algun detalle trae
    impuesto sin resolver -> `no_evaluado(impuesto_fee_pendiente)`.
    `detalle` es el arbol normalizado que se guarda para auditoria.
    """

    total: Importe
    variable: Importe
    fijo: Importe
    impuesto_pendiente: bool
    detalle: tuple[DetalleFee, ...]


@dataclass(frozen=True)
class Tasas:
    """Tasas de la politica del universo, tal como las uso el escenario."""

    iva_divisor: Decimal  # 1 cuando el precio no incluye impuesto (US)
    precio_incluye_iva: bool
    retencion_tasa: Decimal  # `isr_tasa` de la politica


@dataclass(frozen=True)
class Cuenta:
    """P, I, C, F, L, R de UN miembro a UN precio. Reemplaza a `Componentes`.

    Invariante de construccion (antes vivia como chequeo tardio en
    `reglas._freno_entrada`): los seis importes van en la MISMA moneda, la de
    venta. Monedas mezcladas no construyen: `MonedasMezcladas`. Adentro del
    motor nadie vuelve a comparar monedas.
    """

    precio: Importe
    ingreso: Importe
    costo: Importe
    fees: Fees
    envio: Envio
    retencion: Importe

    @property
    def margen(self) -> Decimal:
        """`(I - C - F - L - R) / I`. `I > 0` es invariante de construccion."""
        raise NotImplementedError


# --- Vocabulario (una fuente por lista) ---------------------------------------
# MOTIVOS_MANTENER: sale `cuota`. No entra nada: retener NO es un resultado.
# MOTIVOS_NO_EVALUADO = app.estimacion_venta.MOTIVOS_ESTIMACION (NUEVO export,
#   la estimacion es duena de sus motivos) | MOTIVOS_PROPIOS_DEL_MOTOR. Entran
#   por esa via: logistica_fbm_pendiente, us_sin_politica_prospectiva,
#   oferta_ausente, universo_no_soportado, envio_sin_historia,
#   variante_sin_mapear, fx_desactualizado, valoracion_desactualizada.
#   Una prueba exige el superconjunto: un motivo nuevo de la estimacion ya no
#   puede caer en `estimacion_motivo_desconocido` sin que la prueba falle.
# SUBMOTIVOS_SIN_DATO: salen `dia_sin_stock`, `dia_sin_observacion_inventario`,
#   `listing_inactivo`, `dia_sin_estado_listing` (eran de FBA); entran
#   `dia_no_disponible`, `dia_sin_observacion`, `venta_sin_publicacion`,
#   `evidencia_insuficiente`.
CausaRetencion = Literal[
    "movimiento_masivo", "insumo_sistemico", "apagador", "corte_errores", "goal_cambiado"
]
# Buy Box NO es evidencia: perderla avisa y no frena ni baja (decision 5 del
# dueno, vigente). La evidencia nueva es solo el trafico que pidio en D10.
EvidenciaSenal = Literal["unidades", "trafico"]
# Como se comprobo el precio objetivo. `lineal` = el universo no tiene
# cotizador real: se cierra con la formula y se declara en la fila y en la
# pantalla. Nunca se usa donde hay cotizador.
Verificacion = Literal["cotizada", "lineal"]


# =============================================================================
# B. app/precio/puerto.py -- PURO. La frontera de plataforma
# =============================================================================


@dataclass(frozen=True)
class Miembro:
    """Un producto que se vende por una unidad de precio.

    `product_id is None` = variante sin mapear a `product` (se ve, no se inventa).
    `variante` es el id de la variacion en la plataforma; None si no hay.
    """

    product_id: int | None
    seller_sku: str
    variante: str | None


@dataclass(frozen=True)
class UnidadPrecio:
    """LA unidad del motor: lo que recibe UN precio, UN goal y UNA decision al dia.

    Es una fila de `listing` (Amazon: un ASIN; MeLi: una publicacion MLM).
    `miembros` tiene siempre al menos uno; en Amazon exactamente uno.
    `producto_ancla` es `listing.product_id`, que sigue NOT NULL: en MeLi es el
    producto del primer miembro mapeado (SKU ascendente). Es IDENTIDAD, no
    costo: el costo y el envio salen siempre de `miembros`. Asi ningun JOIN
    existente por `listing.product_id` pierde filas (injerto de C).

    D8, variantes despues, sin reescribir:
      * si MeLi separa las variantes en publicaciones propias, cada una llega
        como su propia `UnidadPrecio` de un miembro: no cambia nada;
      * si el dueno quiere goal distinto por variante dentro de UN precio,
        `GoalVigente.por_variante` deja de venir vacio y la regla de
        `miembro_que_manda` ya recorre miembros.
    """

    listing_id: int
    plataforma: Plataforma
    externo: str  # ASIN o id de publicacion
    canal: Canal | None  # None = `canal_sin_dato`
    producto_ancla: int
    miembros: tuple[Miembro, ...]


@dataclass(frozen=True)
class UnidadActiva:
    """Una activa canonica del catalogo, con lo que la cobertura necesita."""

    unidad: UnidadPrecio | None  # None = activa sin fila en `listing` (`sin_listing`)
    seller_sku: str
    precio: Importe | None
    dias_sin_reportar: int


Disponibilidad = Literal["disponible", "no_disponible", "sin_observacion"]


@dataclass(frozen=True)
class DiaUnidad:
    """Un dia de una unidad, ya normalizado por el adaptador.

    `disponible` = ese dia se podia comprar. FBA: stock > 0 y BUYABLE. FBM:
    BUYABLE. MeLi: `active` con stock > 0. Las reglas ya no saben de inventario
    FBA. `exposiciones`/`clics` son la senal temprana (D10): Amazon = anuncios
    del producto (`ads_product_metric_observation`); MeLi = visitas en `clics`.
    None = no hay dato de ese dia (nunca cero inventado). `buy_box_propia` se
    conserva solo para el aviso `buy_box_perdida`; ninguna regla la consulta.
    """

    dia: date
    unidades: int
    disponibilidad: Disponibilidad
    buy_box_propia: bool | None
    exposiciones: int | None
    clics: int | None


@dataclass(frozen=True)
class HistoriaUnidad:
    """Ventas y disponibilidad atribuidas a ESTA unidad, no al producto (G11).

    `sin_atribuir` = ventas del producto que no se pudieron asignar a una
    publicacion (el producto tiene varias y la orden no dice cual). Si pasa de
    cero, la senal por unidades sale `sin_dato(venta_sin_publicacion)`.
    """

    dias: tuple[DiaUnidad, ...]  # los ultimos 76 dias, uno por dia observado
    primera_venta: date | None
    ledger_cubierto_hasta: date | None
    sin_atribuir: int


@dataclass(frozen=True)
class RefsAuditoria:
    """Ids para reproducir la decision sin releer insumos (FKs de la 0039)."""

    escenario_id: int | None
    fee_observation_id: int | None
    oferta_observation_id: int | None
    envio_muestra_id: int | None
    product_id: int | None  # el miembro que manda


@dataclass(frozen=True)
class Insumos:
    """Todo lo que la plataforma aporta a una decision, leido y tipado.

    `cuenta` es la del miembro que manda (ver `miembro_que_manda`); `miembros`
    trae la de todos (un elemento en Amazon). `cotizable` es un token opaco del
    adaptador: el motor lo devuelve tal cual en `Mercado.cotizar`.
    """

    unidad: UnidadPrecio
    cuenta: Cuenta
    miembros: tuple[tuple[Miembro, Cuenta], ...]
    tasas: Tasas
    oferta_observada_en: datetime
    pricing: ObservacionPricing | None  # fila del dia; None -> precio_sin_observar
    historia: HistoriaUnidad
    refs: RefsAuditoria
    cotizable: object


@dataclass(frozen=True)
class SinInsumos:
    """No hay cuenta. `motivo` ya esta en vocabulario; el crudo va en `diagnostico`."""

    unidad: UnidadPrecio
    motivo: str
    diagnostico: str
    refs: RefsAuditoria


@dataclass(frozen=True)
class Cotizacion:
    """Fees reales a un precio. Reemplaza a `CotizacionVerificada`.

    `fees is None` exactamente cuando `error_code is not None`.
    """

    precio: Importe
    fees: Fees | None
    estimada_en: datetime | None
    error_code: str | None


@dataclass(frozen=True)
class SinCotizador:
    """Este universo no tiene cotizacion real de fees (lo dice su registro).

    No es un error: la decision se cierra con la formula (`verificacion =
    "lineal"`) usando `Fees.variable / precio` y `Fees.fijo` del escenario, y
    lo declara en la fila. El escalon y la regla del doble acotan el error.
    Donde SI hay cotizador, una falla sigue siendo `Cotizacion(error_code=...)`
    y bloquea como hoy (injerto de B).
    """

    motivo: str


@dataclass(frozen=True)
class Acuse:
    """Respuesta de la plataforma a una escritura, ya saneada (`scrub`).

    `aceptada` solo dice que la plataforma tomo la orden; el cierre lo decide
    la lectura posterior. `cuerpo` es lo que se guarda en `precio_cambio.ack`.
    """

    aceptada: bool
    codigo: str | None
    cuerpo: Mapping[str, Any]


class PrecioVivoAusente(Exception):
    """No hay precio propio legible: no se escribe nada. (existe en precio_write)"""


class EscrituraNoDisponible(Exception):
    """El camino de escritura de esta plataforma aun no tiene forma sellada por sonda."""


class Vitrina(Protocol):
    """Cara de LECTURA de una plataforma: solo base, sin red ni credenciales.

    Sirve con cualquier rol que pueda leer (`app_read` en la pantalla,
    `app_admin` en la vista previa de goals, `app_decide` en la corrida).
    """

    clave: Plataforma
    moneda: Moneda  # la de venta: llena `p_actual_currency` cuando no hay P

    def catalogo(self, conn: Conexion, *, hoy: date) -> tuple[UnidadActiva, ...]:
        """Activas canonicas. UNA definicion por plataforma, usada por la
        cobertura y por el denominador del fusible."""
        ...

    def insumos(
        self, conn: Conexion, unidades: Sequence[UnidadPrecio], *, hoy: date, ahora: datetime
    ) -> Mapping[int, Insumos | SinInsumos]:
        """La cuenta del dia y la historia de CADA unidad, por `listing_id`.

        Una llamada por plataforma (lecturas por lote, no una ronda de SELECT
        por unidad: son ~260 en MX). Toda unidad pedida viene en la respuesta.
        Nunca levanta por un dato faltante: lo devuelve como `SinInsumos(motivo)`.
        """
        ...

    def observado(
        self, conn: Conexion, unidad: UnidadPrecio, *, despues_de: date, hasta: date
    ) -> ObservacionPricing | None:
        """La observacion propia mas reciente en `(despues_de, hasta]`: es lo
        que cierra un cambio enviado. None = todavia no hay."""
        ...


class Mercado(Vitrina, Protocol):
    """Una plataforma completa: `Vitrina` mas lo que cruza la red.

    Oculta autenticacion, cuotas, reintentos, forma del cuerpo y el parseo del
    cable. Nada de `httpx.Response`, dicts de SP-API ni JSON de MeLi sale de
    aqui. `escribir` es la UNICA mutacion externa del motor.
    """

    grupo_tasa: str  # quien comparte cuota corre en serie ("spapi" | "meli")

    def cotizar(
        self, conn: Conexion, insumos: Insumos, precio: Importe
    ) -> Cotizacion | SinCotizador:
        """Fees reales a `precio`. Un error de la plataforma es un
        `Cotizacion(error_code=...)`, no una excepcion. `SinCotizador` solo si
        el universo de la unidad esta registrado sin cotizador."""
        ...

    def precio_vivo(self, unidad: UnidadPrecio) -> Importe:
        """Precio propio leido ahora. Levanta `PrecioVivoAusente`."""
        ...

    def escribir(self, unidad: UnidadPrecio, precio: Importe, *, sonda: bool = False) -> Acuse:
        """Pide a la plataforma que el precio sea `precio`. No toca la base.
        Levanta `EscrituraNoDisponible` si la forma de escritura no esta
        sellada, salvo con `sonda=True`. Solo `tools/precio_sonda.py` pasa
        `sonda=True`; `cambios.aplicar` y `revertir_lote` nunca."""
        ...

    def cerrar(self) -> None: ...


def miembro_que_manda(
    cuentas: Sequence[tuple[Miembro, Cuenta]], goal_de: Callable[[Miembro], Decimal]
) -> tuple[Miembro, Cuenta]:
    """El miembro cuyo `P*` es el mas alto: fija el precio de toda la unidad.

    Con un goal unico (hoy) es el de margen mas bajo. Con goals por variante
    (despues) es el mismo recorrido con `goal_de` distinto por miembro.
    # TODO: P*_m = precio_estrella(C_m, fijo_m, L_m, ...; goal_de(m)); argmax.
    # TODO: empate -> seller_sku ascendente (determinista).
    """
    raise NotImplementedError


# =============================================================================
# C. app/precio/envio.py -- PURO. Costo de envio por orden y su origen (carril F)
# =============================================================================

FuenteCargo = Literal[
    "labman_label", "shipping_label", "mfn_postage", "shipping_hb", "meli_envio", "otros"
]


@dataclass(frozen=True)
class CargoEnvio:
    """Un evento `fee` de envio del ledger, ya clasificado. Monto en positivo."""

    orden: str
    fuente: FuenteCargo
    importe: Importe  # abs(amount); el ledger lo guarda negativo
    dia: date


def clasificar_cargo(source_event_id: str | None, fee_type: str | None) -> FuenteCargo:
    """De donde viene el cargo. Lo desconocido es `otros` y se cuenta.

    # TODO: la identidad sale de `source_event_id` partido por `|`: el campo 6
    #   si el campo 2 es `finance`, y si no, el campo 2 (es la expresion de
    #   docs/evidencia/repricing-01/E.0/consultas/06-par-labman-shippinghb.sql).
    #   LabmanLabelPurchase -> labman_label ; shipping_label -> shipping_label ;
    #   MFNPostageFee -> mfn_postage ; ShippingHB -> shipping_hb ;
    #   ShippingChargeback, MFNShippingChargeback y lo desconocido -> otros.
    #   La lectura F.0 confirma que no hay identidades fuera de esas seis.
    """
    raise NotImplementedError


def costo_de_orden(cargos: Sequence[CargoEnvio]) -> Importe | None:
    """`max(labman_label, shipping_label, mfn_postage) + shipping_hb`.

    Las tres primeras son la etiqueta por tres caminos: se cuenta UNA vez
    (D5: Labman y `shipping_label` son el mismo cobro). `mfn_postage`
    (`finance:MFNPostageFee`) es casi todo el envio FBM de Mexico: 649
    ordenes que no traen ninguna otra etiqueta. `ShippingChargeback` y
    `MFNShippingChargeback` van a `otros`, que no entra al costo y se cuenta.
    None si la orden no trae etiqueta alguna. Una sola moneda o
    `MonedasMezcladas`. Regla propuesta por el lead a partir de
    `docs/evidencia/repricing-01/E.0/veredicto.md` (tabla de fuentes); la
    lectura F.0 confirma que no hay identidades fuera de esas.
    """
    raise NotImplementedError


@dataclass(frozen=True)
class OrdenMedida:
    """Una orden con UN solo producto y su costo de envio."""

    orden: str
    product_id: int
    costo: Importe
    dia: date


@dataclass(frozen=True)
class Exclusiones:
    """Lo que no entro a ninguna muestra, contado (ningun silencio)."""

    ordenes_multiproducto: int
    ordenes_sin_producto: int
    cargos_otros: int


@dataclass(frozen=True)
class MuestraNueva:
    """Fila lista para `precio_envio_muestra` (sin id)."""

    product_id: int
    plataforma: Plataforma
    canal: Canal
    origen: Literal["propio", "familia", "marketplace"]
    valor: Importe
    envios: int
    donantes: int | None
    familia_id: int | None
    ventana: tuple[date, date]
    mediana: Importe
    p90: Importe | None
    maximo: Importe
    regla_version: str


def resolver_envio(
    product_id: int,
    *,
    propias: Sequence[OrdenMedida],
    hermanas: Sequence[OrdenMedida],
    familia_id: int | None,
    marketplace: Sequence[OrdenMedida],
    plataforma: Plataforma,
    canal: Canal,
    ventana: tuple[date, date],
) -> MuestraNueva | None:
    """UNICA escalera de origen: propio -> familia -> marketplace -> None.

    `propio` con al menos una orden (D3; en US tambien: el dueno pidio no
    esperar). `familia`: mediana de las MEDIANAS de los hermanos, no de sus
    ordenes (un hermano con 40 envios no aplasta a los demas). `marketplace`:
    mediana de medianas de todos los productos de la plataforma y canal.
    None = no hay un solo envio medido en ese marketplace y canal:
    `no_evaluado(envio_sin_historia)`; nunca una constante.
    """
    raise NotImplementedError


# =============================================================================
# D. app/precio/ventas.py -- PURO. Senal con evidencia y cohorte (carril S)
# =============================================================================


@dataclass(frozen=True)
class SenalVentas:
    """EXISTE; gana `evidencia`. `perdiendo` siempre dice con que se probo."""

    estado: Literal["perdiendo", "no_perdiendo", "sin_dato"]
    submotivo: str | None
    evidencia: EvidenciaSenal | None  # None solo en `sin_dato`
    u15: int
    u60: int
    n15: int
    n60: int
    racha: int


def evaluar_senal(
    historia: HistoriaUnidad,
    *,
    hoy: date,
    config: ConfigPrecio,
    racha_previa: int,
    ultima_subida: date | None,
) -> SenalVentas:
    """Dos evidencias en orden; la primera que tiene dato decide (G13, D10).

    1. `unidades`: la regla vigente u15 contra u60, sobre ventas de la UNIDAD,
       con `disponibilidad` en lugar de inventario FBA (G11, G12).
    2. `trafico`: clics/exposiciones de los dias posteriores a `ultima_subida`
       contra los 28 previos; exige `config.senal_trafico_min` exposiciones en
       cada lado y una caida mayor a `config.caida_ventas_pct` que pase una
       prueba de dos proporciones (z > 2). Solo mira DESPUES de una subida
       propia: asi las pujas de Ads no la contaminan mas que al control.
    Sin dato en las dos -> `sin_dato(evidencia_insuficiente)`.
    # TODO: 2 solo aplica si `ultima_subida` no es None: sin subida propia no
    #       hay antes/despues y la respuesta honesta es sin_dato.
    Buy Box no entra: perderla avisa, no frena ni baja (decision 5 vigente).
    """
    raise NotImplementedError


@dataclass(frozen=True)
class LecturaCohorte:
    """Productos que el motor subio contra los que no toco, en conjunto.

    Con ~0.2 ventas por producto cada 15 dias, ningun producto solo prueba
    nada; el conjunto si. Es AVISO, nunca accion (la reversa es del dueno).
    """

    subidos: int
    control: int
    caida_relativa: Decimal | None  # None = conjunto demasiado chico
    alarma: bool


def evaluar_cohorte(
    subidos: Sequence[HistoriaUnidad],
    control: Sequence[HistoriaUnidad],
    *,
    hoy: date,
    config: ConfigPrecio,
) -> LecturaCohorte:
    """# TODO: tasa 14d/60d de cada grupo; alarma si subidos cae
    #       `config.cohorte_caida_pct` mas que control y ambos pasan el minimo."""
    raise NotImplementedError


# =============================================================================
# E. app/precio/compuerta.py -- PURO. Apagador, fusible, deriva, corte (carril S)
# =============================================================================


def modo_efectivo(global_: Modo, universo: Modo, goal: ModoDecision) -> Modo:
    """El menor de los tres en `off < shadow < live`. Copia del reticulo de Ads
    (`app/optimizer/goals.py::modo_efectivo`), no importado."""
    raise NotImplementedError


@dataclass(frozen=True)
class Intencion:
    """Una decision `subir`/`bajar` del dia, vista por el fusible.

    `nueva`: la decision anterior de la unidad NO venia en camino (no era
    subir/bajar ni `mantener(cooldown)` bajo el mismo goal). Un segundo escalon
    hacia el mismo goal no es intencion nueva: por eso las olas de 7 dias no
    vuelven a saltar (G8).
    `liberada`: su retencion pertenece a una corrida que el dueno ya solto.
    """

    decision_id: int
    listing_id: int
    canal: Canal
    nueva: bool
    liberada: bool


InsumoVigilado = Literal["costo", "envio", "retencion", "fx", "fee_fijo", "fee_variable"]


@dataclass(frozen=True)
class Deriva:
    """Cuantas unidades vieron saltar UN insumo desde su decision anterior (G4).

    Se arma comparando `c_valor`, `l_valor`, `r_valor`... de la decision previa
    (ya persistidos) con la cuenta de hoy. No relee ninguna fuente.
    """

    canal: Canal
    insumo: InsumoVigilado
    comparables: int
    con_salto: int


@dataclass(frozen=True)
class Abierta:
    medidas: int
    nuevas: int


@dataclass(frozen=True)
class Retenida:
    """Hoy no se aplica nada. `detalle` va al aviso, sin costo ni margen."""

    causa: Literal["movimiento_masivo", "insumo_sistemico"]
    medidas: int
    nuevas: int
    insumo: InsumoVigilado | None
    detalle: str


Veredicto = Abierta | Retenida


def evaluar(
    intenciones: Sequence[Intencion],
    *,
    medidas: int,
    derivas: Sequence[Deriva],
    config: ConfigPrecio,
) -> Veredicto:
    """El fusible de D1 y el chequeo de insumo sistemico, para UN universo.

    Se llama una vez por `platform/canal` (injerto de C): 102 de las 260
    activas de MX son FBM, asi que un error en la politica FBM nunca pasaria de
    39 % de la plataforma; medido por universo si dispara. Mas estricto que el
    literal del dueno ("la mitad del catalogo"), nunca mas laxo.
    `medidas` = unidades del universo con decision de hoy que SI tiene cuenta
    (`m_actual`), shadow y live juntas (la sombra es fiel). Un `no_evaluado`
    es "no se sabe", no "no quiere moverse": no diluye el denominador.
    # TODO: cuentan = [i for i in intenciones if i.nueva and not i.liberada]
    # TODO: movimiento_masivo si len(cuentan) > config.fusible_frac * medidas
    #       (mayor ESTRICTO) y len(cuentan) >= config.fusible_min_movimientos
    # TODO: insumo_sistemico si alguna deriva tiene
    #       con_salto > config.insumo_salto_frac * comparables
    #       y con_salto >= config.fusible_min_movimientos
    # TODO: insumo_sistemico gana a movimiento_masivo (dice QUE insumo).
    Las liberadas no cuentan en ninguno de los dos.
    """
    raise NotImplementedError


@dataclass(frozen=True)
class Cortacircuito:
    """Corte por errores de escritura seguidos dentro de una corrida (G5).

    Valor inmutable: `tras(resultado)` devuelve el siguiente. `abierto` tras
    `config.corte_errores_consecutivos` errores seguidos; un exito lo reinicia.
    """

    seguidos: int
    limite: int

    @property
    def abierto(self) -> bool:
        raise NotImplementedError

    def tras(self, estado: Literal["enviado", "confirmado", "error", "saltado"]) -> Cortacircuito:
        raise NotImplementedError


# =============================================================================
# F. app/precio/reglas.py y objetivo.py -- PURO. Lo que cambia (carril S)
# =============================================================================


@dataclass(frozen=True)
class GoalVigente:
    """Fila vigente de `precio_goal` (vista `v_precio_goal_vigente`)."""

    goal_id: int
    listing_id: int
    margen: Decimal  # fraccion sobre I
    mode: ModoDecision
    vigente_desde: date
    por_variante: Mapping[str, Decimal]  # vacio hoy (D8)


@dataclass(frozen=True)
class EntradaDecision:
    """EXISTE; se reduce. Salen `ingreso_60d`, `motivo_estimacion`, `canal`,
    `platform`, `product_id` sueltos (viven en `insumos`)."""

    insumos: Insumos
    goal: GoalVigente
    mode: ModoDecision  # el EFECTIVO, nunca mayor que `goal.mode`
    senal: SenalVentas
    cambios: tuple[CambioPrevio, ...]
    historial: tuple[HistorialMargen, ...]


@dataclass(frozen=True)
class Decision:
    """EXISTE. Salen `prioridad` y `aplicado` (nadie los decide ya); `componentes`
    pasa a `cuenta`; entran `envio_origen` y `evidencia`."""

    resultado: str
    motivo: str | None
    m_actual: Decimal | None
    goal: Decimal
    cuenta: Cuenta | None
    p_objetivo: Importe | None
    p_aplicado: Importe | None
    senal: SenalVentas | None
    mode: ModoDecision
    goal_id: int  # el goal que el motor LEYO; la base valida ESE (injerto de C)
    verificacion: Verificacion | None  # None salvo en subir/bajar
    diagnostico: str = ""


def decidir(
    entrada: EntradaDecision,
    *,
    hoy: date,
    config: ConfigPrecio,
    cotizaciones: tuple[Cotizacion, ...] = (),
    lineal: bool = False,
) -> Decision | PideCotizacion:
    """La MISMA maquina de S4. Cuatro diferencias y ninguna mas:

    * `lineal=True` (el mercado respondio `SinCotizador`): no pide cotizacion;
      cierra con el `P*` de la formula y `verificacion = "lineal"`;

    * `ref = fees.variable / precio` y `fijo = fees.fijo` llegan partidos: se
      borra `derivar_ref_fijo` de las reglas (vive en el adaptador de Amazon);
    * el cooldown ignora un `CambioPrevio` con `sin_efecto` (estado `error`
      cuyo readback probo que el precio no se movio): un mal dia de la
      plataforma ya no congela 7 dias y `frenado(api_error)` se vuelve
      alcanzable (G5);
    * la coherencia de monedas ya no se revisa aqui: `Cuenta` no construye
      con monedas mezcladas.
    `repartir_cupo` se BORRA.
    """
    raise NotImplementedError


# =============================================================================
# G. app/precio/siembra.py -- PURO. Plan de goals: uno, bloque, margen de hoy (G)
# =============================================================================


@dataclass(frozen=True)
class Candidato:
    """Una unidad a la que se le puede poner goal, con su cuenta de hoy."""

    unidad: UnidadPrecio
    seller_sku: str
    goal_vigente: GoalVigente | None
    lectura: Insumos | SinInsumos


@dataclass(frozen=True)
class PedidoMargenDeHoy:
    """D6: goal = margen de hoy de cada unidad."""

    mode: ModoDecision


@dataclass(frozen=True)
class PedidoValor:
    """D7: el dueno escribe el goal (una unidad o varias con el mismo valor)."""

    goal: Decimal
    mode: ModoDecision


@dataclass(frozen=True)
class PedidoModo:
    """Pasar a `live` (o volver a `shadow`) conservando el goal vigente."""

    mode: ModoDecision


@dataclass(frozen=True)
class PedidoCierre:
    pass


Pedido = PedidoMargenDeHoy | PedidoValor | PedidoModo | PedidoCierre

Bloqueo = Literal[
    "sin_escenario",  # no hay margen de hoy que copiar; se lista con su motivo
    "fuera_de_banda",  # margen de hoy fuera de 10-60 %: NO se recorta en silencio
    "salto_mayor_25",  # P* se aleja mas de 25 % del precio actual
    "live_sin_escenario",  # no se enciende lo que el motor no puede evaluar
    "sin_cambio",  # ya tiene ese goal y ese modo
]


@dataclass(frozen=True)
class FilaPlan:
    """Lo que la pantalla y el CLI muestran antes de escribir.

    `bloqueo` con `confirmable=True` se destraba marcando la fila (o el grupo);
    con `False` la fila no se puede escribir. `goal_propuesto` en
    `fuera_de_banda` es el borde de la banda: confirmarlo es decidir mover el
    precio, y por eso pide confirmacion aparte.
    """

    listing_id: int
    plataforma: Plataforma
    seller_sku: str
    accion: Literal["sembrar", "reemplazar", "cerrar", "nada"]
    m_hoy: Decimal | None
    goal_actual: Decimal | None
    goal_propuesto: Decimal | None
    p_actual: Importe | None
    p_estrella: Importe | None
    envio_origen: OrigenEnvio | None
    bloqueo: Bloqueo | None
    confirmable: bool
    motivo: str | None


@dataclass(frozen=True)
class PlanGoals:
    """`huella` = hash de (pedido, filas ordenadas). La vista previa la entrega
    y `aplicar_plan` la exige: si algo cambio entre mirar y aceptar, no escribe."""

    pedido: Pedido
    filas: tuple[FilaPlan, ...]
    huella: str


def planear(
    candidatos: Sequence[Candidato], *, pedido: Pedido, banda: tuple[Decimal, Decimal]
) -> PlanGoals:
    """Las protecciones que hoy viven en `tools/precio_goal.py`, en un solo lugar."""
    raise NotImplementedError


# =============================================================================
# H. app/precio/config.py -- PURO. TODAS las claves `precio_*` (corte 0)
# =============================================================================


@dataclass(frozen=True)
class ConfigPrecio:
    """EXISTE; absorbe las claves que hoy leen cuatro copias de `_numero`.

    Sin defaults en codigo: clave ausente o fuera de cota -> `ValueError` que
    la nombra (la corrida sale 2 sin decidir nada = cerrado).
    SALEN `precio_cap_*`: nadie las lee desde S.1 y se quedan inertes en
    `config_version` (borrarlas antes haria fallar a la version vieja).
    ENTRAN (las siembra la migracion 0054, sin paso manual):
    """

    # --- vigentes, sin cambio (se omiten aqui: caida_ventas_pct, senal_dias,
    # u60_min, fechas_excluidas, escalon_max_pct, movimiento_min_*, tolerancia,
    # dias_entre_cambios, freno_cambios, divergencia_max_pct) ---
    caida_ventas_pct: Decimal
    # --- ya existian, leidas en otros modulos ---
    banda_goal: tuple[Decimal, Decimal]  # precio_goal_min_pct / max_pct
    freno_dias_error: int
    catalogo_max_dias_sin_reportar: int
    aviso_dias_sin_evaluar: int
    # --- nuevas ---
    modo_global: Modo  # precio_modo_global
    modo_universo: Mapping[str, Modo]  # precio_modo_universo {"amazon_mx/fbm": "shadow"}
    fusible_frac: Decimal  # precio_fusible_frac (0.50 por D1)
    fusible_min_movimientos: int  # precio_fusible_min_movimientos
    insumo_salto_pct: Decimal  # precio_insumo_salto_pct
    insumo_salto_frac: Decimal  # precio_insumo_salto_frac
    corte_errores_consecutivos: int  # precio_corte_errores_consecutivos
    senal_trafico_min: int  # precio_senal_trafico_min
    cohorte_caida_pct: Decimal  # precio_cohorte_caida_pct
    envio_ventana_dias: int  # precio_envio_ventana_dias (180 medido)

    def modo_de(self, plataforma: Plataforma, canal: Canal | None) -> Modo:
        """`min(modo_global, modo_universo[platform/canal])`. Universo sin
        entrada = `off` (la config es valida; el universo esta apagado)."""
        raise NotImplementedError


def leer_config(settings: Mapping[str, Any]) -> ConfigPrecio:
    raise NotImplementedError


# =============================================================================
# I. app/precio/cambios.py -- CASCARA GENERICA. Protocolo de `precio_cambio` (S)
#    Sale de `app/spapi/precio_write.py`, que se queda solo con lo de Amazon.
#    Candado nuevo: este archivo no importa `app.spapi`, `app.meli` ni `httpx`.
# =============================================================================


@dataclass(frozen=True)
class ResultadoCambio:
    """EXISTE. `confirmado` es nuevo: el readback ya mostro el precio nuevo."""

    id_cambio: int | None
    estado: Literal["enviado", "confirmado", "error", "saltado"]
    motivo: str | None


def aplicar(conn: Conexion, mercado: Mercado, decision_id: int) -> ResultadoCambio:
    """Lleva UNA decision `subir`/`bajar` de modo `live` a la plataforma.

    Idempotente por compare-and-set contra el precio vivo: llamarla dos veces
    escribe una. Orden inmutable de S5.
    # TODO: 1. leer decision + unidad; exigir subir/bajar y mode live.
    # TODO: 2. cambio abierto del par -> saltado(listing_con_cambio_abierto).
    # TODO: 3. vivo = mercado.precio_vivo(unidad)
    #          vivo == p_aplicado -> saltado(ya_aplicado)      <- segunda llamada
    #          vivo != p_actual   -> saltado(precio_vivo_distinto)
    # TODO: 4. INSERT pendiente + COMMIT.
    # TODO: 5. acuse = mercado.escribir(unidad, p_aplicado)
    #          no aceptada o excepcion -> error(codigo) y readback igual.
    # TODO: 6. sello `enviado` con ack; readback = mercado.precio_vivo(unidad)
    #          readback == p_aplicado -> `confirmado` / `lectura_viva`
    #          (el readback solo confirma en positivo; nunca decide no_confirmado).
    """
    raise NotImplementedError


def registrar_virtual(conn: Conexion, decision_id: int, ahora: datetime) -> int:
    """Sombra fiel: cambio virtual cerrado (`confirmado`/`virtual`), sin red.
    Idempotente: si la decision ya tiene su virtual, devuelve ese id."""
    raise NotImplementedError


def cerrar_huerfanas(conn: Conexion, mercado: Mercado) -> Mapping[str, int]:
    """`pendiente` sin escritura (el proceso murio entre COMMIT y la red).

    # TODO: por cada pendiente no-reversa de `mercado.clave`:
    #   vivo == precio_antes   -> error / `huerfana_sin_efecto` (no gasta cooldown)
    #   vivo == precio_despues -> enviado (ack `{"origen": "huerfana_recuperada"}`)
    #                             y luego confirmado / `lectura_viva`
    #   otro o ilegible        -> error / `huerfana_sin_patch` (como hoy)
    Las reversas pendientes tambien se resuelven aqui (hoy quedan a mano).
    """
    raise NotImplementedError


def cerrar_por_observacion(conn: Conexion, vitrina: Vitrina, hoy: date) -> Mapping[str, int]:
    """Como hoy, pero SOLO los cambios de `vitrina.clave` y leyendo
    `vitrina.observado` en vez de `spapi_price_observation`."""
    raise NotImplementedError


@dataclass(frozen=True)
class SeleccionPorCambios:
    ids: tuple[int, ...]


@dataclass(frozen=True)
class SeleccionPorFecha:
    plataforma: Plataforma
    dia: date


@dataclass(frozen=True)
class SeleccionPorCorrida:
    corrida_id: int


Seleccion = SeleccionPorCambios | SeleccionPorFecha | SeleccionPorCorrida


@dataclass(frozen=True)
class FilaReversa:
    cambio_id: int
    listing_id: int
    seller_sku: str
    volver_a: Importe
    desde: Importe
    estado_plan: Literal["revertible", "ya_revertido", "sin_efecto", "no_es_real"]


@dataclass(frozen=True)
class PlanReversa:
    """`huella` = hash de la seleccion y de (cambio_id, volver_a) de cada fila.

    NO incluye el precio vivo: lo que el dueno aprueba es "revertir este
    conjunto". Si un precio se movio entre el plan y el go, esa fila se salta
    sola y las demas siguen (hoy la huella es todo-o-nada).
    """

    seleccion: Seleccion
    filas: tuple[FilaReversa, ...]
    huella: str


def planear_reversa(conn: Conexion, vitrina: Vitrina, seleccion: Seleccion) -> PlanReversa:
    """Solo base: el dry-run no gasta cuota de la plataforma."""
    raise NotImplementedError


def revertir_lote(
    conn: Conexion,
    mercado: Mercado,
    plan: PlanReversa,
    *,
    huella: str,
    go_literal: str,
    owner: str,
    con_motor_encendido: bool = False,
) -> tuple[ResultadoCambio, ...]:
    """Reversa manual en lote. Nunca automatica.

    # TODO: toma el MISMO claim `precio:<platform>` que la corrida: no corren
    #       a la vez (`LockOcupado` -> "hay corrida en curso, reintenta").
    # TODO: si el modo efectivo de la plataforma es `live` y no viene
    #       `con_motor_encendido`, aborta: revertir con el motor encendido lo
    #       deshace al vencer el cooldown. Primero se apaga, luego se revierte.
    # TODO: por fila: vivo = mercado.precio_vivo(unidad)
    #         original `enviado` y vivo == desde -> cerrarlo `confirmado` /
    #           `lectura_viva` (ya no hay que esperar la observacion de manana)
    #         vivo != desde -> saltado(precio_vivo_distinto), sigue el lote
    #         INSERT reversa pendiente (+ `lote_reversa = huella`) + COMMIT ->
    #         mercado.escribir -> sello -> readback
    # TODO: un `Cortacircuito` corta el lote igual que en la corrida.
    # TODO: al final, aviso `reversa` con el conteo.
    """
    raise NotImplementedError


# =============================================================================
# J. app/precio/corrida.py -- CASCARA GENERICA. decidir -> compuerta -> aplicar (S)
#    Ya no importa `app.spapi`, `app.estimacion_fees` ni lee tablas `spapi_*`.
# =============================================================================


@dataclass(frozen=True)
class ResumenCorrida:
    """Lo que queda en `precio_corrida` al cerrar. El resumen diario sale de ahi."""

    corrida_id: int
    plataforma: Plataforma
    dia: date
    decisiones: int
    movidas: int
    virtuales: int
    retenidas: int
    errores: int
    saltadas_por_edicion: int  # el dueno cambio el goal a media corrida
    veredictos: Mapping[Canal, Veredicto]  # uno por universo de la plataforma

    def linea(self) -> str:
        raise NotImplementedError


class LockOcupado(Exception):
    """EXISTE."""


class ConfigInvalida(ValueError):
    """EXISTE."""


def correr(
    conn: Conexion,
    mercado: Mercado,
    *,
    owner: str,
    avisar: Callable[[Conexion, ResumenCorrida], None] | None = None,
) -> ResumenCorrida:
    """La corrida de UNA plataforma. Se puede lanzar N veces al dia: converge.

    # TODO: config = leer_config(...) ; ConfigInvalida si falta una clave
    # TODO: claim `precio:<platform>` + advisory (como hoy) + fila `precio_corrida`
    #       (las `abierta` viejas de la plataforma pasan a `abortada`)
    # TODO: cambios.cerrar_huerfanas ; cambios.cerrar_por_observacion
    #       (la limpieza corre SIEMPRE, tambien con el apagador en off)
    # TODO: si config.modo_de(platform, *) es off para todo -> cerrar y salir
    # TODO: _decidir_pendientes  -> filas `precio_decision` (nada se aplica aqui)
    # TODO: veredictos = _medir_compuerta ; una fila `precio_compuerta_medicion`
    #       por universo (sellada una vez por corrida)
    # TODO: _aplicar_pendientes(veredictos)
    # TODO: cerrar `precio_corrida` ; avisar (fail-silent, como hoy). El aviso
    #       `compuerta` y el resumen diario se mandan UNA vez: se sella
    #       `avisada_at` / `resumen_enviado_at` despues de enviar.
    Sin fase "en memoria": lo que un paso deja, el siguiente lo lee de la base.
    """
    raise NotImplementedError


def _decidir_pendientes(
    conn: Conexion,
    mercado: Mercado,
    corrida_id: int,
    config: ConfigPrecio,
    hoy: date,
    ahora: datetime,
) -> int:
    """Una decision por unidad con goal vigente que aun no tiene la de hoy.

    # TODO: goals = v_precio_goal_vigente(platform)
    # TODO: por goal sin decision hoy:
    #   modo = modo_efectivo(config.modo_global, config.modo_de(...), goal.mode)
    #   modo == off -> no hay fila (la cobertura la cuenta `fuera_de_alcance`)
    #   frenos de corrida (no_confirmado, api_error) como hoy
    # TODO: lecturas = mercado.insumos(conn, unidades_pendientes, hoy=, ahora=)
    #       (UNA llamada por plataforma)
    # TODO: por unidad:
    #   SinInsumos -> Decision no_evaluado(lectura.motivo)
    #   Insumos    -> bucle de hasta dos `mercado.cotizar` con `decidir`
    #                 (reusa la `precio_cotizacion` del dia por source_event_id;
    #                 si la guardada es de otro precio -porque el goal cambio a
    #                 media tarde- sale `no_evaluado(cotizacion_de_otro_goal)`
    #                 y manana se decide limpio: `intento` sigue siendo 1|2)
    #                 `SinCotizador` -> `decidir(..., lineal=True)`
    #   INSERT precio_decision (+corrida_id, goal_id, l_origen, senal_evidencia,
    #                           verificacion)
    #     El trigger valida `goal_id` (vigente hoy, mismo listing). Si el dueno
    #     lo reemplazo a media corrida el INSERT revienta: se cuenta en
    #     `saltadas_por_edicion` y el repaso la decide con el goal nuevo. Asi
    #     ninguna fila queda con un goal distinto del que el motor uso.
    # TODO: una unidad que falla no tumba a las demas (como hoy); psycopg.Error si.
    """
    raise NotImplementedError


def _medir_compuerta(
    conn: Conexion, plataforma: Plataforma, hoy: date, config: ConfigPrecio
) -> Mapping[Canal, Veredicto]:
    """Arma `Intencion` y `Deriva` desde lo PERSISTIDO hoy y llama
    `compuerta.evaluar` una vez por universo de la plataforma.

    Mide el dia completo, no solo lo decidido en esta corrida: una segunda
    corrida con goals sembrados a media tarde ve el mismo numerador.
    """
    raise NotImplementedError


def _aplicar_pendientes(
    conn: Conexion,
    mercado: Mercado,
    corrida_id: int,
    veredictos: Mapping[Canal, Veredicto],
    config: ConfigPrecio,
    hoy: date,
) -> None:
    """Aplica lo pendiente de HOY que la compuerta deja pasar.

    Pendiente = decision de hoy `subir`/`bajar` sin cambio con efecto (ni real
    ni virtual). Nunca una decision de ayer: su escenario ya no vale.
    # TODO: por decision pendiente, en orden de listing_id:
    #   su universo Retenida y no liberada -> INSERT precio_retencion(causa) ; sigue
    #   config releida: modo ya no alcanza -> retencion `apagador` ; sigue
    #   goal releido por `decision.goal_id`: ya no vigente, o ya no `live`
    #     para una decision `live` -> retencion `goal_cambiado` ; sigue
    #   corte.abierto               -> retencion `corte_errores` ; sigue
    #   mode shadow                 -> cambios.registrar_virtual
    #   mode live                   -> r = cambios.aplicar(conn, mercado, id)
    #                                  corte = corte.tras(r.estado)
    # TODO: la config Y el goal de la decision se releen antes de CADA
    #       escritura real (dos consultas de 1 fila contra ~5 s de escritura).
    #       Apagar a media corrida corta. Pasar un goal a `shadow` o cerrarlo
    #       tambien: una decision `live` retenida por la manana no se aplica
    #       en el repaso si su goal cambio entre tanto. El compare-and-set de
    #       `cambios.aplicar` no basta: solo compara el precio vivo.
    """
    raise NotImplementedError


# --- app/precio/liberaciones.py -- UNICO escritor de `precio_compuerta_liberacion`
#     (carril S). Rol `app_admin`. La corrida (`app_decide`) solo la LEE: dos
#     actores, dos tablas, se juntan al leer. Excepcion de pureza por nombre.


@dataclass(frozen=True)
class Liberacion:
    corrida_id: int
    actor: str
    nota: str
    liberada_en: datetime


class CorridaSinRetencion(LookupError):
    """Esa corrida no retuvo nada: no hay que soltar (409 en el router)."""


def liberar(conn: Conexion, *, corrida_id: int, actor: str, nota: str) -> Liberacion:
    """El dueno suelta lo que una corrida retuvo por `movimiento_masivo` o
    `insumo_sistemico`.

    Idempotente: `corrida_id` es la llave primaria; la segunda llamada devuelve
    la fila que ya existe. No aplica nada por si misma: la siguiente corrida
    de la plataforma ve la liberacion y aplica las decisiones de HOY retenidas
    (o, si ya es otro dia, deja de contar esas unidades en el fusible).
    Soltar NO sirve para `apagador` ni `corte_errores`: esas no son estado, se
    reintentan solas en la siguiente corrida si la causa desaparecio.
    """
    raise NotImplementedError


# =============================================================================
# K. app/precio/goals_write.py -- UNICO escritor de `precio_goal` (carril G)
# =============================================================================


def leer_candidatos(conn: Conexion, vitrina: Vitrina, *, filtro: object) -> tuple[Candidato, ...]:
    """Unidades activas con su goal vigente y su lectura de hoy (solo base)."""
    raise NotImplementedError


@dataclass(frozen=True)
class ResultadoPlan:
    sembrados: int
    cerrados: int
    lote: str  # = plan.huella; queda en cada fila `precio_goal.lote`


class HuellaDistinta(Exception):
    """El plan recalculado no coincide con el que el dueno vio. Trae el plan nuevo."""

    plan: PlanGoals


def aplicar_plan(
    conn: Conexion,
    plan: PlanGoals,
    *,
    huella: str,
    confirmados: frozenset[int],
    go_literal: str | None,
    actor: str,
) -> ResultadoPlan:
    """Escribe el plan ENTERO en una transaccion o no escribe nada.

    # TODO: `plan` se recalcula adentro con `leer_candidatos` + `planear`;
    #       si su huella != `huella` -> HuellaDistinta (nada escrito).
    # TODO: fila con bloqueo no confirmable -> se omite y se cuenta.
    #       fila con bloqueo confirmable y listing_id no en `confirmados` -> idem.
    # TODO: `live` exige `go_literal` (CHECK de la 0039, sin cambio).
    # TODO: reemplazar = cerrar (valid_to = hoy) + sembrar (valid_from = hoy).
    #       Reeditar el mismo dia deja la fila anterior ANULADA
    #       (valid_to = valid_from) y la nueva vigente: lo permite el indice
    #       parcial que reemplaza a `precio_goal_unico_por_fecha`.
    `sembrar_goal` y `cerrar_goal` (existen) quedan como pasos internos.
    """
    raise NotImplementedError


# =============================================================================
# L. app/precio/fuentes.py + cobertura.py -- cobertura sobre `Vitrina` (corte 0)
# =============================================================================


def leer_publicaciones(
    conn: Conexion, vitrina: Vitrina, *, hoy: date, config: ConfigPrecio
) -> list[Any]:
    """EXISTE con `platform: str`; ahora recibe la `Vitrina`.

    `_SQL_CANO` (BUYABLE de SP-API) se muda al adaptador de Amazon.
    `FilaPublicacion` gana `modo_universo` y `envio_origen`; `DetalleSinGoal`
    gana `listing_id` (G28). En `cobertura.py` se BORRAN `FASE_FBM`,
    `FASE_MELI` y `_fase_fuera_de_alcance`: `fuera_de_alcance` pasa a ser
    "universo apagado", derivado de la config, no de una lista en codigo.
    La ecuacion `activas = evaluadas + no_evaluadas + sin_goal +
    fuera_de_alcance` sigue cuadrando exacta.
    """
    raise NotImplementedError


# =============================================================================
# M. app/estimacion_universo.py -- registro de universos (corte 0 + carril F)
#    La frontera de ABAJO. Absorbe los seis puntos cableados.
# =============================================================================


class Cotizador(Protocol):
    """Fees reales de una oferta a un precio. Lo usan la ingesta de estimacion
    (para armar el escenario) y el `Mercado.cotizar` del mismo universo."""

    def cotizar(self, oferta: Any, precio: Decimal, *, ahora: Callable[[], datetime]) -> Any: ...


@dataclass(frozen=True)
class Universo:
    """Un `platform/canal`. La clave es la misma que exige el trigger de
    `estimacion_escenario` para la politica.

    Reemplaza: `_motivo_universo` (insumos), `_UNIVERSO_SOPORTADO` +
    `MARKETPLACE_MX` + `IsAmazonFulfilled` fijos (fees), `WHERE platform =
    'amazon_mx'` (ingest) y `logistica` constante (venta).
    """

    plataforma: Plataforma
    canal: Canal
    moneda_venta: Moneda
    envio: Literal["politica", "muestra"]  # fba -> politica; fbm y meli -> muestra
    fx_max_dias: int | None  # US: tope de antiguedad de la tasa; None = no aplica
    # None = el universo no tiene cotizacion real (la sonda no la encontro): el
    # escenario toma variable y fijo de lo medido en el ledger y el motor
    # cierra `lineal`. Se decide por universo, una vez, con su sonda.
    cotizador: Cotizador | None

    @property
    def clave(self) -> str:
        """`"amazon_us/fbm"`."""
        raise NotImplementedError


def universo_de(plataforma: Plataforma, canal: Canal) -> Universo | None:
    """None = `universo_no_soportado` (motivo, no excepcion).

    El registro es un dict literal en ESTE archivo con cuatro entradas; cada
    una apunta a un modulo dueno: `app/estimacion_amazon.py` (carril F) y
    `app/meli/estimacion.py` (carril M). Agregar un universo es una linea aqui
    y una fila en `estimacion_politica_version`.
    """
    raise NotImplementedError


def logistica_del_universo(
    conn: Conexion,
    universo: Universo,
    *,
    product_id: int,
    politica: Any,
    fx: TasaFx | None,
    hoy: date,
) -> Envio | str:
    """`L` del escenario. Devuelve el `Envio` con su origen o un motivo.

    `politica` -> `EnvioIncluido(settings["logistica"])`. `muestra` -> la fila
    mas reciente de `precio_envio_muestra` para (producto, plataforma, canal);
    sin fila -> `"envio_sin_historia"`. Moneda distinta a la de venta y sin
    `fx` -> `"fx_ausente"`. `calcular_contribucion` recibe el importe ya
    resuelto en `EntradaCalculo.logistica` (campo NUEVO) y deja de leerlo de
    la politica; el componente `logistica` del escenario guarda original,
    normalizado, tasa y `fuente = origen`.
    """
    raise NotImplementedError


# =============================================================================
# N. app/estimacion_reader.py -- `leer_cuenta` (corte 0)
# =============================================================================


CuentaLeida = tuple[Cuenta, tuple[tuple[Miembro, Cuenta], ...], Tasas, RefsAuditoria, datetime]


def leer_cuenta(conn: Conexion, listing_id: int, *, as_of: datetime) -> CuentaLeida | str:
    """Escenario vigente ya tipado, o su primer motivo.

    UNICO lugar que conoce la forma del JSON `componentes` del escenario. Hoy
    lo desarman a mano `corrida.armar_entrada` y `tools/precio_goal._referencia`
    (dos copias). Los adaptadores y la siembra llaman aqui. Los fees partidos
    ya no se parsean del arbol: el escenario los trae en columnas
    (`fee_variable`, `fee_fijo`, `fee_cotizable`; injerto de B).
    """
    raise NotImplementedError


# =============================================================================
# O. app/envio_muestras.py -- job diario (carril F). Rol `app_decide`.
#    cron 12:15 UTC, antes de la estimacion de las 12:45.
# =============================================================================


def refrescar_muestras(
    conn: Conexion, plataforma: Plataforma, canal: Canal, *, hoy: date, ventana_dias: int
) -> Exclusiones:
    """Escribe una `precio_envio_muestra` por producto activo del universo.

    # TODO: cargos = ledger_event kind='fee' fee_type='shipping_fee' de la ventana
    #       -> CargoEnvio(clasificar_cargo(...)) ; agrupar por orden
    # TODO: orden -> producto por las ventas de la misma orden; mas de un
    #       producto o ninguno -> se cuenta en `Exclusiones`
    # TODO: por producto activo: resolver_envio(...) ; INSERT solo si cambia
    #       respecto de su ultima muestra (append-only, sin filas repetidas)
    Correrlo dos veces el mismo dia no escribe nada la segunda.
    """
    raise NotImplementedError


# =============================================================================
# P. app/ledger_atribucion.py -- venta -> publicacion (corte 0 + F y M)
#    `ledger_event` es append-only: la atribucion vive en tabla aparte.
# =============================================================================


@dataclass(frozen=True)
class Atribucion:
    """A que publicacion pertenece un evento del ledger.

    Solo existe si se pudo decidir. Lo que hoy no se decide NO se escribe:
    la tabla es append-only y una fila `indeterminado` quedaria fija para
    siempre aunque manana llegue el dato que la resuelve (el canal de la
    orden, o la fila de `listing`). La siguiente ingesta lo reintenta.
    """

    listing_id: int
    variante: str | None
    resuelto_por: Literal["sku", "externo_unico", "producto_unico", "canal_orden"]


class Atribuidor(Protocol):
    """Uno por plataforma de origen. `app/ledger.py` despacha por plataforma
    en vez de la rama `if plataforma == "meli": excluida`."""

    def atribuir(self, fila: Any) -> Atribucion | None:
        """None = todavia no se puede decidir: no se escribe y se reintenta."""
        ...


# =============================================================================
# Q. app/spapi/precio_mercado.py -- MercadoAmazon (corte 0b + carril F)
#    Una clase para amazon_mx y amazon_us; el canal lo trae cada unidad.
# =============================================================================


class MercadoAmazon:
    """Implementa `Mercado`. Se arma con o sin clientes: sin ellos es `Vitrina`.

    Aqui aterriza lo que hoy esta repartido en `corrida.py` (lecturas de
    `spapi_price_observation`, `spapi_inventario_observation`,
    `spapi_listing_estado_observation`, `_oferta_para_cotizar`, `_verificada`,
    `_detalle_fee`) y la mitad de lectura/escritura de `precio_write.py`.
    `precio_write.py` sigue siendo el UNICO importador de `write_client`.
    """

    clave: Plataforma
    moneda: Moneda
    grupo_tasa: str = "spapi"

    def catalogo(self, conn: Conexion, *, hoy: date) -> tuple[UnidadActiva, ...]:
        raise NotImplementedError

    def insumos(
        self, conn: Conexion, unidades: Sequence[UnidadPrecio], *, hoy: date, ahora: datetime
    ) -> Mapping[int, Insumos | SinInsumos]:
        """# TODO: leer_cuenta(...) ; observacion del dia + buy box
        # TODO: historia = ventas de la UNIDAD (`v_precio_venta_unidad`) +
        #       disponibilidad por dia segun `unidad.canal`:
        #         fba: inventario > 0 Y BUYABLE ; fbm: BUYABLE
        # TODO: exposiciones/clics = ads_product_metric_observation del producto
        """
        raise NotImplementedError

    def observado(
        self, conn: Conexion, unidad: UnidadPrecio, *, despues_de: date, hasta: date
    ) -> ObservacionPricing | None:
        raise NotImplementedError

    def cotizar(
        self, conn: Conexion, insumos: Insumos, precio: Importe
    ) -> Cotizacion | SinCotizador:
        """# TODO: universo_de(clave, canal).cotizador ; partir el arbol en
        #       `Fees(variable = unico ReferralFee, fijo = resto)`; sin
        #       referral unico -> Cotizacion(error_code="referral_ausente")."""
        raise NotImplementedError

    def precio_vivo(self, unidad: UnidadPrecio) -> Importe:
        raise NotImplementedError

    def escribir(self, unidad: UnidadPrecio, precio: Importe, *, sonda: bool = False) -> Acuse:
        raise NotImplementedError

    def cerrar(self) -> None:
        raise NotImplementedError


# =============================================================================
# R. app/meli/ -- todo MeLi (carril M)
#    precio_mercado.py  MercadoMeli
#    write_client.py    MeliWriteClient: default-deny, unico `.put(` del repo
#    catalogo.py        ingesta diaria -> listing, listing_miembro,
#                       meli_publicacion_observation (usa `ClienteMeli`, GET)
#    estimacion.py      universo `meli/meli`: cotizador y escenario por miembro
#    ledger.py          `Atribuidor` y mapeo de la rama meli del ledger
# =============================================================================


class MeliWriteClient:
    """La unica puerta de escritura a MeLi. NO hereda de `ClienteMeli` (que
    sigue GET-only): lo compone solo para el token, como `SpapiWriteClient`.

    `modo_confirmado == "live"` exacto o `MutationNotAllowedError` antes de
    construir nada. Un solo metodo publico. La forma del cuerpo (precio en la
    publicacion o repetido por variacion) es la CANDIDATA que concluyo la
    lectura M.0. Mientras `FORMA_ESCRITURA_MELI = "pendiente_sonda"`,
    `poner_precio` levanta `EscrituraNoDisponible` sin tocar la red, salvo
    que el llamador pase `sonda=True`: solo `tools/precio_sonda.py` lo pasa,
    y solo con `--acepto-mutacion-real` y `--go`. La corrida nunca. La sonda
    de +-0.01 prueba la candidata; si pasa, un PR cambia solo la constante.
    """

    def __init__(
        self, *, modo_confirmado: str, proveedor_token: Callable[[], str], transport: Any = None
    ) -> None:
        """`proveedor_token` es el refresco que YA vive en
        `app/reputacion_clientes.py` (una sola pieza maneja el secreto y su
        reescritura atomica); este cliente nunca lee ni escribe credenciales."""
        raise NotImplementedError

    def poner_precio(
        self,
        item_id: str,
        variantes: Sequence[str],
        precio: Decimal,
        moneda: str,
        *,
        sonda: bool = False,
    ) -> Acuse:
        raise NotImplementedError


class MercadoMeli:
    """Implementa `Mercado` para `meli`. Unidad = publicacion.

    `insumos`: `leer_cuenta` devuelve una cuenta por miembro (mismo precio y
    mismos fees; costo y envio propios de cada variante) y la del que manda.
    Una variante sin `product_id` -> `SinInsumos("variante_sin_mapear")` con
    el SKU en el diagnostico. Disponibilidad y visitas salen de
    `meli_publicacion_observation`.
    `cotizar`: consulta de cargos por precio, categoria y tipo de publicacion
    (GET, por `ClienteMeli`); la parte porcentual es `variable`. Si la sonda
    muestra que esa consulta no existe o no separa los cargos, el universo
    `meli/meli` se registra sin cotizador y esto devuelve `SinCotizador`.
    """

    clave: Plataforma = "meli"
    moneda: Moneda = "MXN"
    grupo_tasa: str = "meli"

    def catalogo(self, conn: Conexion, *, hoy: date) -> tuple[UnidadActiva, ...]:
        """Activa canonica de MeLi (G27): ultima observacion de la publicacion
        con estado `active`. Las cerradas del scan no cuentan."""
        raise NotImplementedError

    def insumos(
        self, conn: Conexion, unidades: Sequence[UnidadPrecio], *, hoy: date, ahora: datetime
    ) -> Mapping[int, Insumos | SinInsumos]:
        raise NotImplementedError

    def observado(
        self, conn: Conexion, unidad: UnidadPrecio, *, despues_de: date, hasta: date
    ) -> ObservacionPricing | None:
        raise NotImplementedError

    def cotizar(
        self, conn: Conexion, insumos: Insumos, precio: Importe
    ) -> Cotizacion | SinCotizador:
        raise NotImplementedError

    def precio_vivo(self, unidad: UnidadPrecio) -> Importe:
        raise NotImplementedError

    def escribir(self, unidad: UnidadPrecio, precio: Importe, *, sonda: bool = False) -> Acuse:
        raise NotImplementedError

    def cerrar(self) -> None:
        raise NotImplementedError


# =============================================================================
# S. app/precio_mercados.py -- raiz de composicion (corte 0)
#    UNICO modulo que conoce a los dos adaptadores. Candado nuevo: solo
#    `app/cli.py`, `tools/precio_*.py` y los routers de precios lo importan.
# =============================================================================


def vitrina(plataforma: Plataforma) -> Vitrina:
    """Cara de lectura. No abre credenciales ni red: sirve en la pantalla."""
    raise NotImplementedError


class MercadoAbierto(Protocol):
    def __enter__(self) -> Mercado: ...
    def __exit__(self, *exc: object) -> None: ...


def abrir(plataforma: Plataforma) -> MercadoAbierto:
    """Mercado completo (clientes, cubos de tasa, escritor default-deny).
    Plataforma sin adaptador listo -> `ConfigInvalida` (exit 2), nunca un
    mercado a medias."""
    raise NotImplementedError


# =============================================================================
# T. app/api_precios.py (lectura) y app/api_precios_write.py (escritura) -- G
#    Mismo patron que `app/api_write.py`: `exige_token` ANTES de abrir la
#    conexion admin. Los cuerpos son modelos pydantic del router (no salen de el).
# =============================================================================


def post_goals_plan(cuerpo: Any) -> Mapping[str, Any]:
    """POST /api/precios/goals/plan -- vista previa. No escribe.
    `{platform, pedido, listing_ids | "sin_goal"}` -> filas del plan + huella."""
    raise NotImplementedError


def post_goals_aplicar(cuerpo: Any) -> Mapping[str, Any]:
    """POST /api/precios/goals/aplicar -- `{huella, confirmados, go_literal}`.
    409 con el plan nuevo si la huella ya no coincide. Hasta 500 unidades por
    llamada (tope de `/api/familias/asignar`)."""
    raise NotImplementedError


def post_compuerta_liberar(corrida_id: int, cuerpo: Any) -> Mapping[str, Any]:
    """POST /api/precios/compuerta/{corrida_id}/liberar -- `{nota}`.
    INSERT en `precio_compuerta_liberacion`. Idempotente: liberar dos veces
    devuelve la misma fila. 409 si esa corrida no retuvo nada."""
    raise NotImplementedError


@dataclass(frozen=True)
class EstadoSeguridad:
    """Lo que `/precios` y `/salud` muestran arriba. Lo lee `fuentes.py`."""

    modo_global: Modo
    modo_universo: Mapping[str, Modo]
    ultima_corrida: ResumenCorrida | None
    retenciones_abiertas: Mapping[Canal, Retenida]  # las del dia sin soltar
    liberable_corrida_id: int | None
    cohorte: LecturaCohorte | None


# =============================================================================
# U. app/notifica.py -- tipos de aviso nuevos (carril S)
#    Mismo sender unico `notifica_precio`. Sin costo, margen ni goal.
# =============================================================================
# TIPOS_PRECIO += ("resumen_diario", "compuerta", "cambio_error", "corte_errores",
#                  "reversa", "cohorte")
# resumen_diario: uno por plataforma y dia, en la PRIMERA corrida que cierra con
#   decisiones nuevas: movidos (suben/bajan, hasta 5 SKUs con precio antes ->
#   despues), en sombra, retenidos, errores, sin evaluar, con envio imputado.
#   Un dia sin movimiento tambien avisa ("hoy no se movio ningun precio").


@dataclass(frozen=True)
class ResumenDiarioPrecio:
    plataforma: Plataforma
    dia: date
    subieron: int
    bajaron: int
    en_sombra: int
    retenidos: int
    errores: int
    sin_evaluar: int
    con_envio_imputado: int
    muestra: tuple[tuple[str, str, str], ...]  # (sku, precio antes, precio despues)


def aviso_precio_resumen(resumen: ResumenDiarioPrecio) -> str:
    """Builder puro."""
    raise NotImplementedError


def aviso_precio_compuerta(veredicto: Retenida, plataforma: Plataforma, dia: date) -> str:
    """ "N de M productos quieren moverse hoy; no se aplico nada. Entra a
    /precios para soltarlo." Con `insumo_sistemico` nombra el insumo."""
    raise NotImplementedError


# =============================================================================
# V. tools/ -- despachadores delgados
# =============================================================================
# tools/precio_reversa.py (S): argparse -> `Seleccion` -> cambios.planear_reversa
#   / cambios.revertir_lote. Selectores: --cambio-id (N), --fecha + --platform,
#   --corrida. Deja de importar `app.spapi.*`: recibe el mercado de
#   `precio_mercados.abrir` (la allowlist de imports del candado se reescribe).
# tools/precio_goal.py (G): argparse/CSV -> `Pedido` -> siembra.planear /
#   goals_write.aplicar_plan. Se BORRAN de aqui `_referencia`, `_componente`,
#   `_detalle_fee`, `_guardas_del_plan`, `UMBRAL_SALTO` (viven en `siembra.py`).
# tools/precio_sonda.py (F y M): la sonda de +-0.01 por camino de escritura,
#   generica sobre `Mercado` (un solo script para MX FBM, US y MeLi).


def _usos_de_referencia() -> Iterable[str]:
    """Marcador: este archivo es un bosquejo; nada de aqui se importa."""
    raise NotImplementedError
