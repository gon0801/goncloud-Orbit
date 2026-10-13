"""Vista previa, lanzamiento y vigia diario del impulso (BIDS 02, I.3).

previsualiza solo lee (mas una llamada a Amazon por producto para los
bids). lanza crea un lote por producto con la fabrica y guarda el
impulso. vigila corre cada dia despues de la ingesta: lee, guarda y
actua (pausar o graduar).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Protocol

from app import fabrica_bids as br
from app import fabrica_plan as fp
from app import goals_write, retiro_anuncios
from app import impulso as im
from app.fabrica_plan import MONEDA_POR_PLATAFORMA
from app.optimizer.goals import gasto_para_concluir_desde_settings


class ErrorImpulso(Exception):
    """Error de dominio del impulso con su status HTTP (lo traduce I.5).
    En un lanzamiento parcial, `lanzados` trae los que salieron y
    `fallos` el mensaje por listing."""

    def __init__(
        self,
        status: int,
        mensaje: str,
        *,
        lanzados: list | None = None,
        fallos: dict | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.status = status
        self.lanzados = lanzados or []
        self.fallos = fallos or {}


@dataclass(frozen=True)
class SolicitudImpulso:
    plataforma: str
    listing_ids: tuple[int, ...]
    subfamilia_por_listing: dict[int, str]
    tope_monto: Decimal | None = None


@dataclass(frozen=True)
class NoLanzable:
    listing_id: int
    motivo: str


@dataclass(frozen=True)
class VistaPreviaImpulso:
    planes: tuple[im.PlanImpulso, ...]
    no_lanzables: tuple[NoLanzable, ...]
    huella: str
    confirmacion_esperada: str
    presupuesto_diario_total: Decimal
    tope_total: Decimal
    moneda: str

    def como_dict(self) -> dict:
        return {
            "huella": self.huella,
            "confirmacion_esperada": self.confirmacion_esperada,
            "presupuesto_diario_total": str(self.presupuesto_diario_total),
            "tope_total": str(self.tope_total),
            "moneda": self.moneda,
            "planes": [
                {**p.como_json(), "bids_acotados": list(p.bids_acotados)} for p in self.planes
            ],
            "no_lanzables": [
                {"listing_id": n.listing_id, "motivo": n.motivo} for n in self.no_lanzables
            ],
        }


@dataclass(frozen=True)
class EjecucionFabrica:
    lote: str
    grupo_id: int
    estado_lote: str


@dataclass(frozen=True)
class ImpulsoLanzado:
    impulso_id: int
    lote: str
    estado_lote: str


@dataclass(frozen=True)
class AccionVigia:
    impulso_id: int
    lectura: im.LecturaImpulso
    veredicto: im.VeredictoImpulso
    hecho: str


class EjecutorFabrica(Protocol):
    """La fabrica es la unica que crea campanas. Recibe el plan v2 de
    dos roles en modo shadow y devuelve el grupo creado."""

    def __call__(self, plan_grupo, huella: str, lote: str) -> EjecucionFabrica: ...


class PausadorDeCampanas(Protocol):
    """La fabrica es la unica que pausa y reanuda campanas. El vigia no
    conoce HTTP."""

    def pausa(self, lote: str) -> None: ...
    def reanuda(self, lote: str) -> None: ...
    def reactiva(self, lote: str) -> None: ...
    def estados(self, lote: str) -> dict[str, str]: ...


class PausadorFabrica:
    """PausadorDeCampanas sobre `pausar_lote`/`reanudar_lote` del tool:
    fila en el ledger antes de cada PUT, readback y sello. Las
    credenciales y el HTTP se arman al primer uso, para que el vigia
    lea sin red cuando no hay nada que pausar."""

    def __init__(self, conn, *, actor: str = "impulso-vigia") -> None:
        self._conn = conn
        self._actor = actor
        self._ctxs: dict[str, object] = {}
        self._armado = None

    def _ctx_para(self, lote: str):
        import tools.fabrica_campanas as fc
        from app.ads.config import AdsCredentials

        if self._armado is None:
            cred = AdsCredentials.from_secrets_dir()
            # fc.AdsClient (no app.ads.client directo): la misma fuente
            # que el resto de la fabrica, para que la frontera de
            # pruebas aplique parejo al PUT y al readback.
            cliente = fc.AdsClient(cred)
            http = fc.httpx.Client(
                timeout=fc.httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
            )
            self._armado = (cred, cliente, http, fc._perfiles(cliente))
        cred, cliente, http, perfiles = self._armado
        fila = self._conn.execute(
            "SELECT platform::text FROM fabrica_lote WHERE lote = %s", (lote,)
        ).fetchone()
        self._conn.commit()
        if fila is None or fila[0] not in perfiles:
            raise ErrorImpulso(422, f"sin perfil aceptado para el lote {lote}")
        platform = fila[0]
        if platform not in self._ctxs:
            self._ctxs[platform] = fc._Ctx(
                http, fc._token_lwa(cred, http), cred, cliente, perfiles[platform], self._conn, lote
            )
        ctx = self._ctxs[platform]
        ctx.lote = lote
        return ctx

    def pausa(self, lote: str) -> None:
        import tools.fabrica_campanas as fc

        try:
            fc.pausar_lote(self._ctx_para(lote), lote, self._actor)
        except fc.Abortar as exc:
            if self._lote_no_pausable(lote):
                raise ErrorImpulso(409, str(exc)) from None
            raise

    def _lote_no_pausable(self, lote: str) -> bool:
        """R09-A: True si el Abortar de la pausa se explica por el
        estado del lote (ya pausado, desarmado o sin campanas que
        pausar). Es un conflicto que el dueno resuelve (409), no un
        rechazo de Amazon a media pausa (502)."""
        fila = self._conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
        self._conn.commit()
        if fila is None or fila[0] != "applied":
            return True
        hay = self._conn.execute(
            "SELECT 1 FROM fabrica_lote_paso WHERE lote = %s AND recurso = 'campaign'"
            " AND estado IN ('applied', 'failed') AND external_id IS NOT NULL LIMIT 1",
            (lote,),
        ).fetchone()
        self._conn.commit()
        return hay is None

    def reanuda(self, lote: str) -> None:
        import tools.fabrica_campanas as fc

        fc.reanudar_lote(self._ctx_para(lote), lote, self._actor)

    def reactiva(self, lote: str) -> None:
        """Activa el par (PUT + readback por campana, levanta si
        alguna no queda ENABLED). Quien lanza pide encender ahora;
        graduar filtra antes (solo lo del vigia)."""
        import tools.fabrica_campanas as fc

        fc.reactiva_lote(self._ctx_para(lote), lote, self._actor)

    def estados(self, lote: str) -> dict[str, str]:
        """State real en Amazon por campana del lote (GET, sin PUTs):
        leer Amazon antes de creerle al lote."""
        import tools.fabrica_campanas as fc

        ctx = self._ctx_para(lote)
        filas = self._conn.execute(fc._SQL_EXTERNALS_CAMPANA_DEL_LOTE, (lote,)).fetchall()
        self._conn.commit()
        estados = {}
        for (external,) in filas:
            leido = fc._readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
            estados[external] = leido.get("state") if isinstance(leido, dict) else None
        return estados

    def pausa_huerfanas(self, lote: str) -> bool:
        """Pausa las campanas ENABLED del lote (PUT + readback por
        campana, con fila en el ledger antes de cada PUT): lo usa la
        reconciliacion (lotes sin impulso) y el vigia cuando el lote
        ya dice pausado (G7: el dueno pudo reencender en consola).
        Devuelve si pauso alguna; si alguna queda ENABLED, levanta."""
        import tools.fabrica_campanas as fc

        ctx = self._ctx_para(lote)
        filas = self._conn.execute(
            "SELECT DISTINCT p.rol::text, p.external_id FROM fabrica_lote_paso p"
            " WHERE p.lote = %s AND p.recurso = 'campaign' AND p.external_id IS NOT NULL",
            (lote,),
        ).fetchall()
        # Como pausar_lote: los pasos nuevos siguen al max(orden);
        # sin esto el INSERT choca con UNIQUE(lote, orden).
        ctx.orden = self._conn.execute(
            "SELECT coalesce(max(orden), 0) FROM fabrica_lote_paso WHERE lote = %s",
            (lote,),
        ).fetchone()[0]
        self._conn.commit()
        pauso = False
        pendientes: list[str] = []
        for rol, external in filas:
            leido = fc._readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
            if not isinstance(leido, dict) or leido.get("state") != "ENABLED":
                continue
            paso = fp.Paso(rol, "campaign", "/sp/campaigns", {}, "reconciliar huerfana")
            paso_id = fc._inserta_paso(ctx, paso, {"campaignId": str(external), "state": "PAUSED"})
            ack = fc._put_estado_campana(ctx, external, "PAUSED")
            visto = None
            if fp.ack_ok(ack):
                releido = fc._readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
                visto = releido.get("state") if isinstance(releido, dict) else None
            fc._sella_paso(ctx, paso_id, visto == "PAUSED", external, ack, visto)
            if visto == "PAUSED":
                pauso = True
            else:
                pendientes.append(str(external))
        if pendientes:
            raise fc.Abortar(f"reconciliacion de {lote} incompleta: siguen ENABLED {pendientes}")
        return pauso


def lote_de_plan(plan: im.PlanImpulso) -> str:
    """Lote durable del impulso: lleva el listing y la huella corta (mismo
    plan, mismo lote: reintentar no duplica; otra huella, otro lote del
    MISMO listing: el reintento encuentra el huerfano por prefijo)."""
    return f"impulso-{plan.listing_id}-{plan.huella()[:8]}"


def _huella_vista(planes: tuple[im.PlanImpulso, ...]) -> str:
    return hashlib.sha256("".join(sorted(p.huella() for p in planes)).encode("utf-8")).hexdigest()


def sugeridor_amazon(cliente, profile_id: str | int, moneda: str):
    """Una llamada a recommend_bids por producto; reparte por rol. Sin
    sugerencias para un rol, su bid queda None (regla 3)."""

    def sugiere(
        asins: tuple[str, ...], expresiones_por_rol: dict[str, tuple]
    ) -> dict[str, Decimal | None]:
        todas = tuple(dict.fromkeys(e for exprs in expresiones_por_rol.values() for e in exprs))
        if not todas:
            return {rol: None for rol in expresiones_por_rol}
        datos = cliente.recommend_bids(br.payload(asins, todas), profile_id=profile_id).json()
        salida = {}
        for rol, expresiones in expresiones_por_rol.items():
            if not expresiones:
                salida[rol] = None
                continue
            reales = br.interpretar(datos, expresiones, exigir_todas=False)
            salida[rol] = br.bid_inicial(reales, moneda) if reales else None
        return salida

    return sugiere


_SQL_SETTINGS = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""


def _plan_producto(
    conn,
    plataforma: str,
    listing_id: int,
    subfamilia: str,
    tope_monto: Decimal | None,
    sugeridor,
    hoy: dt.date,
    settings: dict,
) -> im.PlanImpulso:
    fila = conn.execute(
        "SELECT seller_sku FROM listing WHERE id = %s AND platform = %s",
        (listing_id, plataforma),
    ).fetchone()
    if fila is None:
        raise fp.PlanInvalido(f"listing {listing_id} no existe en {plataforma}")
    if not fila[0]:
        raise fp.PlanInvalido(f"listing {listing_id} sin seller_sku: no se lanza")
    args = SimpleNamespace(
        listing_ids=str(listing_id),
        plataforma=plataforma,
        origen_objetivo="margen_medido",
        target_acos=None,
    )
    import tools.fabrica_campanas as fc

    publicaciones, objetivo, semillas, _existentes = fc._datos_plan_v2(args, conn, subfamilia)
    if len(publicaciones) != 1:
        raise fp.PlanInvalido(
            f"impulso exige un producto: listing {listing_id} dio {len(publicaciones)}"
        )
    pub = publicaciones[0]
    moneda = MONEDA_POR_PLATAFORMA[plataforma]
    if tope_monto is not None:
        monto = tope_monto
    else:
        monto = gasto_para_concluir_desde_settings(settings, plataforma)
    tope = im.TopeAprendizaje(monto, moneda, im.presupuesto_diario(monto, moneda))
    expresiones = {rol: fp.expresiones_bid(semillas, rol) for rol in im.ROLES_IMPULSO}
    bids = sugeridor((pub.asin,), expresiones)
    bid_por_rol = {rol: bids.get(rol) for rol in im.ROLES_IMPULSO}
    _, acotados = im.acota_bids(bid_por_rol, tope.presupuesto_diario / 2)
    retiros = retiro_anuncios.planea_retiro(
        conn, plataforma, listing_ids=(pub.listing_id,), motivo="impulso", conservar_grupo_id=None
    ).anuncios
    return im.PlanImpulso(
        plataforma=plataforma,
        listing_id=pub.listing_id,
        product_id=pub.product_id,
        seller_sku=pub.seller_sku,
        asin=pub.asin,
        subfamilia=subfamilia,
        margen_neto_pct=pub.margen_neto_pct,
        fecha=hoy,
        objetivo=objetivo,
        tope=tope,
        bid_por_rol=bid_por_rol,
        keywords_exact=tuple(semillas.exact),
        negatives=tuple(semillas.negativos),
        bids_acotados=acotados,
        retiros=retiros,
    )


def previsualiza(
    conn, solicitud: SolicitudImpulso, *, sugeridor=None, hoy: dt.date | None = None
) -> VistaPreviaImpulso:
    """Solo lecturas mas la sugerencia de bids (una llamada por
    producto). Cero mutaciones. El producto sin margen queda como no
    lanzable, con su motivo; sin margen positivo no hay objetivo
    medido y el impulso no se lanza."""
    if not solicitud.listing_ids:
        raise ErrorImpulso(422, "impulso sin productos: listing_ids vacio")
    if sugeridor is None:
        raise ErrorImpulso(503, "sin sugeridor de bids")
    dia = hoy or dt.datetime.now(dt.UTC).date()
    fila = conn.execute(_SQL_SETTINGS).fetchone()
    settings = dict(fila[0]) if fila and fila[0] else {}
    planes = []
    no_lanzables = []
    for lid in sorted(set(solicitud.listing_ids)):
        subfamilia = solicitud.subfamilia_por_listing.get(lid)
        if not subfamilia:
            no_lanzables.append(NoLanzable(lid, "sin subfamilia del dueno"))
            continue
        try:
            planes.append(
                _plan_producto(
                    conn,
                    solicitud.plataforma,
                    lid,
                    subfamilia,
                    solicitud.tope_monto,
                    sugeridor,
                    dia,
                    settings,
                )
            )
        except fp.PlanInvalido as exc:
            no_lanzables.append(NoLanzable(lid, str(exc)))
    planes_t = tuple(planes)
    moneda = MONEDA_POR_PLATAFORMA[solicitud.plataforma]
    n = len(planes_t)
    return VistaPreviaImpulso(
        planes=planes_t,
        no_lanzables=tuple(no_lanzables),
        huella=_huella_vista(planes_t),
        confirmacion_esperada=f"LANZAR IMPULSO DE {n} PRODUCTO{'S' if n != 1 else ''}",
        presupuesto_diario_total=sum((p.tope.presupuesto_diario for p in planes_t), Decimal("0")),
        tope_total=sum((p.tope.monto for p in planes_t), Decimal("0")),
        moneda=moneda,
    )


_SQL_VIVO = """
SELECT i.id, i.lote FROM impulso i
 LEFT JOIN impulso_lectura l ON l.impulso_id = i.id AND l.es_final
 WHERE i.platform = %s AND i.listing_id = %s AND l.impulso_id IS NULL
"""

_SQL_IMPULSO_DE_LOTE = """
SELECT id FROM impulso WHERE lote = %s
"""

_SQL_GRUPO_DE_LOTE = """
SELECT id FROM campana_grupo WHERE lote = %s
"""

_SQL_PENDIENTES_LISTING = """
SELECT f.lote, f.estado, g.id
  FROM fabrica_lote f
  LEFT JOIN campana_grupo g ON g.lote = f.lote
 WHERE f.lote LIKE %s
   AND f.estado IN ('applied', 'failed', 'planeado')
   AND NOT EXISTS (SELECT 1 FROM impulso i WHERE i.lote = f.lote)
 ORDER BY f.created_at DESC
"""


def _sella_impulso(conn, plan, grupo_id, lote, actor, confirmacion):
    """Inserta la fila de impulso de un lote ya applied (lanza normal o
    reanudacion tras crash entre _mutar e INSERT). Devuelve el id."""
    return conn.execute(
        "INSERT INTO impulso (platform, listing_id, product_id, grupo_id, lote, subfamilia,"
        " tope_monto, tope_moneda, presupuesto_diario, plazo_dias, lanzado_el, actor,"
        " go_literal) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s)"
        " RETURNING id",
        (
            plan.plataforma,
            plan.listing_id,
            plan.product_id,
            grupo_id,
            lote,
            plan.subfamilia,
            plan.tope.monto,
            plan.tope.moneda,
            plan.tope.presupuesto_diario,
            plan.tope.plazo_dias,
            actor,
            confirmacion,
        ),
    ).fetchone()[0]


_SQL_ESTADO_LOTE = """
SELECT estado, pausado_por FROM fabrica_lote WHERE lote = %s
"""


# R09-r5 B2a: graduar solo reactiva lo que pauso el vigia (lista de
# permitidos exacta: ni NULL/consola ni dueno).
_REACTIVA_SOLO_VIGIA = ("impulso-vigia",)


def _tiene_externals(conn, lote: str) -> bool:
    """Si el lote creo algo en Amazon (algun paso con external_id)."""
    fila = conn.execute(
        "SELECT 1 FROM fabrica_lote_paso WHERE lote = %s AND external_id IS NOT NULL LIMIT 1",
        (lote,),
    ).fetchone()
    return fila is not None


def _archiva_lote_vacio(conn, lote: str) -> None:
    """H6: un lote failed/planeado SIN nada creado no bloquea: se copia
    a un nombre -descartado (historia preservada, estado desarmado),
    se mueven pasos y grupo, y se borra el canonico para que el
    reintento lo reuse. INSERT-copia + UPDATE hijos + DELETE viejo, en
    ese orden: las FKs no son diferibles y el UPDATE-rename viola.
    Conexion propia: el ejecutor crea por la suya y debe ver el nombre
    libre. Con impulso no se archiva (el impulso manda)."""
    propia = _conecta_propia(conn, autocommit=True)
    try:
        if propia.execute("SELECT 1 FROM impulso WHERE lote = %s", (lote,)).fetchone() is not None:
            raise ErrorImpulso(502, f"lote {lote} tiene impulso: no se archiva")
        base, n = f"{lote}-descartado", 0
        while True:
            nuevo = base if n == 0 else f"{base}-{n}"
            n += 1
            if (
                propia.execute("SELECT 1 FROM fabrica_lote WHERE lote = %s", (nuevo,)).fetchone()
                is None
            ):
                break
        propia.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado, detalle, created_at, finished_at, pausado_por)"
            " SELECT %s, platform, tipo_producto, nombre_base, go_literal, huella, plan,"
            " modo_goal, 'desarmado',"
            " 'H6: fallo sin nada creado; el reintento reusa el nombre',"
            " created_at, finished_at, pausado_por FROM fabrica_lote WHERE lote = %s",
            (nuevo, lote),
        )
        propia.execute("UPDATE fabrica_lote_paso SET lote = %s WHERE lote = %s", (nuevo, lote))
        propia.execute("UPDATE campana_grupo SET lote = %s WHERE lote = %s", (nuevo, lote))
        propia.execute("DELETE FROM fabrica_lote WHERE lote = %s", (lote,))
    finally:
        propia.close()


def _plan_coincide_con_lote(conn, plan: im.PlanImpulso, lote: str) -> bool:
    """H2: el huerfano solo se reutiliza si su presupuesto y bids son
    los del plan confirmado (por rol: budget = diario/2, bid acotado).
    Ante la duda (plan ilegible), no coincide."""
    from decimal import InvalidOperation

    fila = conn.execute("SELECT plan FROM fabrica_lote WHERE lote = %s", (lote,)).fetchone()
    if fila is None or not isinstance(fila[0], dict):
        return False
    guardado = fila[0].get("parametros") or {}
    # H11: la misma cuantizacion que grupo_v2_de_plan guarda (con
    # diario impar, 25.01/2 es 12.505 pero el lote trae 12.51).
    mitad = (plan.tope.presupuesto_diario / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    bids, _roles = im.acota_bids(plan.bid_por_rol, mitad)
    for rol in im.ROLES_IMPULSO:
        viejo = guardado.get(rol) or {}
        try:
            if Decimal(str(viejo.get("budget"))) != mitad:
                return False
            if Decimal(str(viejo.get("bid"))) != bids[rol]:
                return False
        except (InvalidOperation, ValueError, TypeError, KeyError):
            return False
    return True


def _archiva_vacios(conn, listing_id: int) -> None:
    """H6: los failed/planeado sin nada creado (de otra huella) no
    bloquean: se archivan para que el reintento cree el par."""
    for otro, estado_otro, _ in conn.execute(
        _SQL_PENDIENTES_LISTING, (f"impulso-{listing_id}-%",)
    ).fetchall():
        if estado_otro in ("failed", "planeado") and not _tiene_externals(conn, otro):
            _archiva_lote_vacio(conn, otro)


def _conecta_propia(conn, *, autocommit: bool):
    """Conexion propia a la misma base (patron cycle._abre_heartbeat):
    conn.info.dsn NO trae la password (psycopg la excluye); se
    reinyecta desde conn.info.password. Sin esto, en produccion
    (scram-sha-256) la conexion propia falla (R09-r5 B1a)."""
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = conn.info.dsn
    if conn.info.password:
        dsn = make_conninfo(dsn, password=conn.info.password)
    return psycopg.connect(dsn, dbname=conn.info.dbname, autocommit=autocommit)


def _reactiva_para_sellar(conn, lote: str) -> None:
    """Reactiva un huerfano que coincide (misma huella u otro plan)
    antes de sellar, en conexion propia (no rompe la tx de lanza).
    Levanta si alguna campana no queda ENABLED."""
    pausador = _pausador_propio(conn)
    try:
        pausador.reactiva(lote)
    finally:
        pausador._propia.close()


def _pausador_propio(conn) -> PausadorFabrica:
    """PausadorFabrica con conexion propia: pausar/activar un huerfano no
    rompe la transaccion de lanza. Quien llama cierra la conexion."""
    propia = _conecta_propia(conn, autocommit=False)
    pausador = PausadorFabrica(propia)
    pausador._propia = propia
    return pausador


def _lanza_par_nuevo(
    conn, plan, lote, actor, confirmacion, ejecutor, aviso_huerfano, lanzados, fallos
) -> bool:
    """Ejecutor + sello del par nuevo. Si traia aviso de huerfano
    (H17: su pausa fallo y puede seguir ENABLED), el aviso va a la
    respuesta aunque el par nazca (502 parcial, no OK silencioso).
    Devuelve False si el plan fallo (sin sello)."""
    try:
        hecho = ejecutor(im.grupo_v2_de_plan(plan), plan.huella(), lote)
    except Exception as exc:
        base = f"la fabrica fallo ({exc})"
        fallos[plan.listing_id] = f"{base}; ademas: {aviso_huerfano}" if aviso_huerfano else base
        return False
    if hecho.estado_lote != "applied":
        fallos[plan.listing_id] = f"lote {lote} no quedo applied"
        if aviso_huerfano:
            fallos[plan.listing_id] += f"; ademas: {aviso_huerfano}"
        return False
    imp = _sella_impulso(conn, plan, hecho.grupo_id, lote, actor, confirmacion)
    lanzados.append(ImpulsoLanzado(imp, lote, hecho.estado_lote))
    if aviso_huerfano is not None:
        fallos[plan.listing_id] = aviso_huerfano
    return True


def _descarta_huerfano(conn, lote_viejo: str, actor: str) -> str | None:
    """H2: el huerfano es de otro plan: se pausa (best effort, con
    conexion propia) y desarma con el motivo; el par nuevo nace aparte.
    Devuelve aviso si la pausa fallo (H17: el huerfano puede seguir
    ENABLED; el vigia lo reconcilia pero SE DICE en la respuesta)."""
    pausador = _pausador_propio(conn)
    try:
        try:
            pausador.pausa(lote_viejo)
            extra = "pausado"
            aviso = None
        except Exception as exc:
            extra = f"pausa best-effort fallo ({exc}); el vigia reconcilia"
            aviso = (
                f"huerfano {lote_viejo} de otro plan sigue ENABLED en Amazon"
                f" (su pausa fallo: {exc}); el vigia lo reconcilia"
            )
    finally:
        pausador._propia.close()
    conn.execute(
        "UPDATE fabrica_lote SET estado = 'desarmado', detalle = %s WHERE lote = %s",
        (
            f"H2: plan distinto al confirmado ({extra}); el par nuevo nace aparte",
            lote_viejo,
        ),
    )
    return aviso


def lanza(
    conn,
    solicitud: SolicitudImpulso,
    *,
    huella: str,
    confirmacion: str,
    actor: str,
    ejecutor: EjecutorFabrica,
    sugeridor=None,
    hoy: dt.date | None = None,
) -> list[ImpulsoLanzado]:
    """Re-planifica, compara la huella (409 si cambio) y crea un lote por
    producto con el ejecutor de la fabrica (modo shadow). Candado por
    listing: un vivo con otro lote da 409. La fila de impulso se
    inserta despues de que su lote queda applied. Un producto que
    falla no detiene a los demas."""
    if not solicitud.listing_ids:
        raise ErrorImpulso(422, "impulso sin productos: listing_ids vacio")
    if not actor or not actor.strip():
        raise ErrorImpulso(422, "lanzar exige actor")
    if "\x00" in actor:
        raise ErrorImpulso(422, "actor con NUL")
    vista = previsualiza(conn, solicitud, sugeridor=sugeridor, hoy=hoy)
    if not vista.planes:
        raise ErrorImpulso(422, "nada lanzable: revisa no_lanzables")
    if vista.huella != huella:
        raise ErrorImpulso(409, "el plan cambio desde la vista previa: previsualiza de nuevo")
    if vista.confirmacion_esperada != confirmacion:
        raise ErrorImpulso(
            422, f"confirmacion distinta: se esperaba {vista.confirmacion_esperada!r}"
        )
    lanzados: list[ImpulsoLanzado] = []
    fallos: dict[int, str] = {}
    for plan in vista.planes:
        lote = lote_de_plan(plan)
        # Una transaccion por producto: el candado advisory dura la
        # transaccion (en autocommit, sin esto se libera al instante y
        # dos lanzar crean dos vivos). Salir commitea; fallar revierte.
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"impulso:{plan.listing_id}",)
            )
            existente = conn.execute(_SQL_IMPULSO_DE_LOTE, (lote,)).fetchone()
            if existente is not None:
                estado = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()[0]
                lanzados.append(ImpulsoLanzado(existente[0], lote, estado))
                continue
            huerfano = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
            if huerfano is not None:
                grupo = conn.execute(_SQL_GRUPO_DE_LOTE, (lote,)).fetchone()
                if huerfano[0] == "applied" and grupo is not None:
                    # H10: misma huella con las campanas pausadas (falla +
                    # reversa + REGISTRAR GRUPO): se reactivan antes de
                    # sellar (un impulso vivo gasta). Si no activan, fallo.
                    # Sin externals no hay nada que activar (N3): se sella
                    # directo, sin tocar la fabrica.
                    try:
                        if _tiene_externals(conn, lote):
                            _reactiva_para_sellar(conn, lote)
                    except Exception as exc:
                        fallos[plan.listing_id] = f"huerfano {lote} coincide pero no activa ({exc})"
                        continue
                    imp = _sella_impulso(conn, plan, grupo[0], lote, actor, confirmacion)
                    lanzados.append(ImpulsoLanzado(imp, lote, "applied"))
                    continue
                if huerfano[0] in ("failed", "planeado") and not _tiene_externals(conn, lote):
                    # H6: fallo sin nada creado en Amazon: no bloquea; se
                    # archiva el nombre y el reintento crea el par.
                    _archiva_lote_vacio(conn, lote)
                else:
                    fallos[plan.listing_id] = (
                        f"lote {lote} quedo en {huerfano[0]}: reconcilia a mano"
                    )
                    continue
            vivo = conn.execute(_SQL_VIVO, (plan.plataforma, plan.listing_id)).fetchone()
            if vivo is not None:
                raise ErrorImpulso(
                    409,
                    f"listing {plan.listing_id} ya tiene un impulso vivo",
                    lanzados=lanzados,
                )
            # Otro lote, mismo listing (otra huella: otro bid u otro dia):
            # el huerfano applied se reutiliza; un failed/planeado o un
            # applied sin grupo bloquea hasta reconciliar a mano.
            pendientes = conn.execute(
                _SQL_PENDIENTES_LISTING, (f"impulso-{plan.listing_id}-%",)
            ).fetchall()
            if pendientes:
                _archiva_vacios(conn, plan.listing_id)
                pendientes = conn.execute(
                    _SQL_PENDIENTES_LISTING, (f"impulso-{plan.listing_id}-%",)
                ).fetchall()
            if pendientes:
                malos = [p for p in pendientes if p[1] != "applied" or p[2] is None]
                if malos:
                    fallos[plan.listing_id] = (
                        "lotes pendientes de este listing: "
                        + ", ".join(f"{lote} ({estado})" for lote, estado, _ in malos)
                        + ": reconcilia a mano"
                    )
                    continue
                viejo, _, grupo_viejo = pendientes[0]
                if _plan_coincide_con_lote(conn, plan, viejo):
                    # L1/L4/L8c: mismo plan; si quedo pausado se activa
                    # antes de sellar (un impulso vivo gasta).
                    pausador = _pausador_propio(conn)
                    try:
                        pausador.reactiva(viejo)
                    except Exception as exc:
                        fallos[plan.listing_id] = (
                            f"huerfano {viejo} coincide pero no activa ({exc})"
                        )
                        continue
                    finally:
                        pausador._propia.close()
                    imp = _sella_impulso(conn, plan, grupo_viejo, viejo, actor, confirmacion)
                    lanzados.append(ImpulsoLanzado(imp, viejo, "applied"))
                    continue
                # H2: otro plan; el huerfano se pausa y desarma, y cae al
                # ejecutor: el par nuevo nace con el plan confirmado.
                # H17: si su pausa fallo, el aviso va a la respuesta (no
                # se responde OK con un huerfano ENABLED sin decirlo).
                aviso_huerfano = _descarta_huerfano(conn, viejo, actor)
            else:
                aviso_huerfano = None
            if not _lanza_par_nuevo(
                conn,
                plan,
                lote,
                actor,
                confirmacion,
                ejecutor,
                aviso_huerfano,
                lanzados,
                fallos,
            ):
                continue
    if fallos:
        raise ErrorImpulso(
            502, "algunos productos no se lanzaron", lanzados=lanzados, fallos=fallos
        )
    return lanzados


_SQL_VIVOS = """
SELECT i.id, i.platform, i.lote, i.tope_monto, i.tope_moneda, i.presupuesto_diario,
       i.plazo_dias, i.lanzado_el, i.grupo_id, i.listing_id, i.go_literal
  FROM impulso i
  LEFT JOIN impulso_lectura l ON l.impulso_id = i.id AND l.es_final
 WHERE l.impulso_id IS NULL
 ORDER BY i.id
"""

_SQL_YA_TUVO_IMPRESIONES = """
SELECT 1 FROM impulso_lectura
 WHERE impulso_id = %s AND impresiones IS NOT NULL AND impresiones > 0
"""

_SQL_CAMPANAS_DE_IMPULSO = """
SELECT r.ad_entity_id
  FROM campana_grupo_rol r
  JOIN impulso i ON i.grupo_id = r.grupo_id
 WHERE i.id = %s
"""

_SQL_CORTE_PLATAFORMA = """
SELECT max(v.metric_date) FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.platform = %s AND e.kind = 'campaign'
"""

_SQL_DIAS_CON_INGESTA = """
SELECT count(DISTINCT v.metric_date) FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.platform = %s AND v.metric_date BETWEEN %s AND %s
   AND e.kind = 'campaign'
"""

_SQL_OBSERVADO = """
SELECT v.metric_date, v.impressions, v.clicks, v.cost, v.orders
  FROM v_metric_latest v
 WHERE v.ad_entity_id = ANY(%s) AND v.metric_date >= %s
"""

_SQL_GOALS_DE_IMPULSO = """
SELECT g.id
  FROM ads_optimizer_goal g
  JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id
  JOIN impulso i ON i.grupo_id = r.grupo_id
 WHERE i.id = %s AND g.scope = 'campaign'
"""


def _acumula(filas):
    """Suma por metrica; si alguna fila trae NULL, el acumulado es None
    (regla 3: nunca un cero inventado)."""
    tot = []
    for col in (1, 2, 3, 4):
        valores = [f[col] for f in filas]
        tot.append(None if any(v is None for v in valores) else sum(valores, 0))
    return tuple(tot)


def _vigila_uno(conn, pausador, fila, *, hoy, escritor_retiro):
    """Un impulso vivo: lee, actua y guarda. Devuelve la accion y el
    escritor de retiro (se crea al primer retiro y se reusa). Si el
    lote ya esta pausado (crash entre pausar y guardar), no llama al
    pausador: registra pausado y sigue."""
    (imp, platform, lote, monto, moneda, diario, plazo, lanzado, grupo, listing, go) = fila
    tope = im.TopeAprendizaje(Decimal(monto), moneda, Decimal(diario), plazo)
    corte = conn.execute(_SQL_CORTE_PLATAFORMA, (platform,)).fetchone()[0]
    if corte is None:
        lectura = im.LecturaImpulso(hoy, 0, None, None, None, None)
        conn.commit()
        return (
            AccionVigia(imp, lectura, im.VeredictoImpulso("en_curso", False, "seguir"), "nada"),
            escritor_retiro,
        )
    ayer = hoy - dt.timedelta(days=1)
    if corte > ayer:
        corte = ayer
    # Dias CON DATO (ingesta de la plataforma entre lanzado y corte),
    # no de calendario: con atraso, el plazo espera al dato.
    dias = conn.execute(_SQL_DIAS_CON_INGESTA, (platform, lanzado.date(), corte)).fetchone()[0]
    camps = [f[0] for f in conn.execute(_SQL_CAMPANAS_DE_IMPULSO, (imp,)).fetchall()]
    filas = [f for f in conn.execute(_SQL_OBSERVADO, (camps, lanzado.date())).fetchall()]
    filas = [f for f in filas if f[0] <= corte]
    if filas:
        impr, clics, gasto, pedidos = _acumula(filas)
    else:
        impr, clics, gasto, pedidos = 0, 0, Decimal("0"), 0
    lectura = im.LecturaImpulso(corte, dias, impr, clics, gasto, pedidos)
    veredicto = im.veredicto_impulso(lectura, tope)
    hecho = "nada"
    ya = conn.execute(
        "SELECT hecho FROM impulso_lectura WHERE impulso_id = %s AND corte = %s",
        (imp, corte),
    ).fetchone()
    if ya is not None:
        conn.commit()
        return AccionVigia(imp, lectura, veredicto, ya[0]), escritor_retiro
    if veredicto.accion == "pausar":
        estado_lote = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
        if estado_lote is None or estado_lote[0] != "pausado":
            pausador.pausa(lote)
            conn.execute(
                "UPDATE fabrica_lote SET estado = 'pausado', pausado_por = 'impulso-vigia'"
                " WHERE lote = %s AND estado = 'applied'",
                (lote,),
            )
        else:
            # G7: el lote ya dice pausado, pero el dueno pudo
            # reencender en consola (no hay boton de reanudar):
            # leer Amazon y pausar las que sigan ENABLED (PUT +
            # readback). Nunca anotar "pausado" sin verificar.
            pausador.pausa_huerfanas(lote)
        hecho = "pausado"
    elif veredicto.accion == "graduar":
        # Graduar casa con Amazon: reactiva las campanas que pauso el
        # vigia antes de pasar goals a live. Solo las del vigia
        # (lista exacta: dueno o consola/NULL no se tocan): gradua
        # sin reencender y lo dice (hecho y detalle del lote).
        # Desarmado no revive: solo goals.
        estado_lote = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
        reencendio = (
            estado_lote is not None
            and estado_lote[0] != "desarmado"
            and estado_lote[1] in _REACTIVA_SOLO_VIGIA
        )
        if reencendio:
            pausador.reactiva(lote)
        ahora = dt.datetime.now(dt.UTC)
        for (gid,) in conn.execute(_SQL_GOALS_DE_IMPULSO, (imp,)).fetchall():
            goals_write.edita_goal(conn, gid, mode="live", updated_at=ahora)
        if reencendio or (estado_lote is not None and estado_lote[0] == "desarmado"):
            hecho = "graduado"
        elif estado_lote is not None and estado_lote[0] == "pausado":
            # El lote dice pausado y no era del vigia: no se reencendio.
            pauso = estado_lote[1]
            quien = pauso if pauso is not None else "fuera de Orbit (consola?)"
            hecho = "graduado_sin_reencender"
            conn.execute(
                "UPDATE fabrica_lote SET detalle = %s WHERE lote = %s",
                (f"graduado sin reencender: pauso {quien}, goals a live", lote),
            )
        else:
            # applied (o lote ido) sin reactivar: solo se dice
            # sin_reencender si Amazon confirma algo apagado.
            try:
                hay_pausada = any(s != "ENABLED" for s in pausador.estados(lote).values())
            except Exception:
                hay_pausada = True  # incierto: avisa en vez de afirmar
            if hay_pausada:
                pauso = estado_lote[1] if estado_lote is not None else None
                quien = pauso if pauso is not None else "fuera de Orbit (consola?)"
                hecho = "graduado_sin_reencender"
                conn.execute(
                    "UPDATE fabrica_lote SET detalle = %s WHERE lote = %s",
                    (f"graduado sin reencender: pauso {quien}, goals a live", lote),
                )
            else:
                hecho = "graduado"
    if hecho in ("nada", "graduado") and (lectura.impresiones or 0) > 0:
        primero = conn.execute(_SQL_YA_TUVO_IMPRESIONES, (imp,)).fetchone() is None
        if primero:
            plan = retiro_anuncios.planea_retiro(
                conn,
                platform,
                listing_ids=(listing,),
                motivo="impulso",
                conservar_grupo_id=grupo,
            )
            pendientes = retiro_anuncios.retiros_pendientes_de_impulso(conn, imp, plan)
            if pendientes.anuncios:
                if escritor_retiro is None:
                    from app.ads import archivar

                    escritor_retiro = archivar.preparar_escritor(platform)
                retiro_anuncios.ejecuta_retiro(
                    conn,
                    pendientes,
                    go=go,
                    impulso_id=imp,
                    escritor=escritor_retiro,
                    actor="impulso-vigia",
                )
                if hecho == "nada":
                    hecho = "retiro_de_bolsas"
    conn.execute(
        "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
        " impresiones, clics, gasto, gasto_moneda, pedidos, veredicto, es_final, hecho)"
        " VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (impulso_id, corte) DO NOTHING",
        (
            imp,
            corte,
            dias,
            impr,
            clics,
            gasto,
            # Regla 4: el monto va sellado con su moneda (la del tope
            # del impulso); gasto None (dato faltante) va con moneda
            # None, nunca una constante.
            moneda if gasto is not None else None,
            pedidos,
            veredicto.estado,
            veredicto.final,
            hecho,
        ),
    )
    conn.commit()
    return AccionVigia(imp, lectura, veredicto, hecho), escritor_retiro


_RECONCILIA_ANTIGUEDAD_MIN = 60
_SQL_LOTES_HUERFANOS = """
SELECT f.lote FROM fabrica_lote f
 WHERE f.lote LIKE 'impulso-%%'
   AND f.estado IN ('planeado', 'failed', 'applied', 'desarmado')
   AND NOT EXISTS (SELECT 1 FROM impulso i WHERE i.lote = f.lote)
   AND f.created_at < now() - make_interval(mins => %s)
   AND EXISTS (SELECT 1 FROM fabrica_lote_paso p
                WHERE p.lote = f.lote AND p.external_id IS NOT NULL)
 ORDER BY f.lote
"""


def reconcilia_huerfanos(conn, pausador) -> list[str]:
    """Cierra lo que un lanzamiento dejo a medias (muerte del proceso):
    pausa en Amazon las campanas ENABLED de lotes de impulso sin
    impulso con mas de _RECONCILIA_ANTIGUEDAD_MIN minutos (un lote
    recien nacido puede ser un lanzamiento en curso; uno de hoy que
    ya envejecio se pausa en la corrida de ese dia, no 32 horas
    despues). Best effort por lote; nunca levanta (lo que falle se
    reintenta en la siguiente corrida)."""
    reconcilia = getattr(pausador, "pausa_huerfanas", None)
    if reconcilia is None:
        return []
    lotes = [
        r[0] for r in conn.execute(_SQL_LOTES_HUERFANOS, (_RECONCILIA_ANTIGUEDAD_MIN,)).fetchall()
    ]
    conn.commit()
    pausados = []
    for lote in lotes:
        try:
            if reconcilia(lote):
                pausados.append(lote)
        except Exception as exc:
            # Aislado por lote: revierte lo suyo (la corrida sigue;
            # el fallido se reintenta manana).
            conn.rollback()
            print(f"reconcilia: lote {lote} fallo ({exc})", file=sys.stderr)
    if pausados:
        print(f"reconcilia: pausadas huerfanas de {pausados}", file=sys.stderr)
    return pausados


def vigila(
    conn, pausador: PausadorDeCampanas, *, hoy: dt.date, escritor_retiro=None
) -> list[AccionVigia]:
    """Cron diario, despues de la ingesta y antes del ciclo. Por cada
    impulso vivo: lee el acumulado en v_metric_latest hasta el ultimo
    dia con ingesta de su plataforma, guarda (idempotente por
    (impulso_id, corte)) y actua: pausar con el pausador y lote
    'pausado'; graduar con goals a live; seguir no toca nada. Un dia
    con ingesta y sin fila de la campana cuenta como cero; una metrica
    NULL llega como None (en_curso). Paso 4: si es el primer dia con
    impresiones y hay retiros pendientes, saca el producto de sus
    bolsas viejas (primero nace lo nuevo, despues sale de lo viejo).
    Aislado por impulso: si uno falla, se revierte lo suyo de base
    (lo de Amazon se tolera por relectura: B1, ya_estaba) y el resto
    se atiende (el fallido se reintenta manana). Si muere a medias, la
    siguiente corrida relee y termina."""
    acciones = []
    reconcilia_huerfanos(conn, pausador)
    # R09-r5 B3 (iii): paso diario de retiros: resuelve las
    # intenciones pendientes de cualquier motivo (nadie mas las
    # retoma si el impulso termino). Best effort: nunca levanta.
    retiro_anuncios.reconcilia_intenciones(conn, escritor=escritor_retiro)
    escritores: dict[str, object] = {}
    for fila in conn.execute(_SQL_VIVOS).fetchall():
        platform = fila[1]
        fijo = escritor_retiro if escritor_retiro is not None else escritores.get(platform)
        try:
            accion, usado = _vigila_uno(conn, pausador, fila, hoy=hoy, escritor_retiro=fijo)
        except Exception as exc:
            conn.rollback()
            print(f"vigila: impulso {fila[0]} fallo ({exc})", file=sys.stderr)
            continue
        if escritor_retiro is None and usado is not None:
            escritores[platform] = usado
        acciones.append(accion)
    return acciones
