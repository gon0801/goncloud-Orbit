"""Vista previa, lanzamiento y vigia diario del impulso (BIDS 02, I.3).

previsualiza solo lee (mas una llamada a Amazon por producto para los
bids). lanza crea un lote por producto con la fabrica y guarda el
impulso. vigila corre cada dia despues de la ingesta: lee, guarda y
actua (pausar o graduar).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
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
        import httpx

        import tools.fabrica_campanas as fc
        from app.ads.client import AdsClient
        from app.ads.config import AdsCredentials

        if self._armado is None:
            cred = AdsCredentials.from_secrets_dir()
            cliente = AdsClient(cred)
            http = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0))
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

        fc.pausar_lote(self._ctx_para(lote), lote, self._actor)

    def reanuda(self, lote: str) -> None:
        import tools.fabrica_campanas as fc

        fc.reanudar_lote(self._ctx_para(lote), lote, self._actor)


def lote_de_plan(plan: im.PlanImpulso) -> str:
    """Lote durable del impulso: deriva de la huella del plan (mismo plan,
    mismo lote: reintentar no duplica)."""
    return f"impulso-{plan.huella()[:12]}"


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
SELECT estado FROM fabrica_lote WHERE lote = %s
"""


def _grupo_v2_de_plan(plan: im.PlanImpulso) -> fp.PlanGrupoV2:
    """PlanGrupoV2 de dos roles en modo shadow desde el plan de impulso,
    con bids acotados (None sale al diario de su campana)."""
    mitad = plan.tope.presupuesto_diario / 2
    bids, _acotados = im.acota_bids(plan.bid_por_rol, mitad)
    return fp.PlanGrupoV2(
        plan.plataforma,
        plan.subfamilia,
        plan.seller_sku,
        plan.fecha,
        plan.tope.moneda,
        "shadow",
        (
            fp.PublicacionGrupoV2(
                plan.listing_id,
                plan.product_id,
                plan.asin,
                plan.seller_sku,
                plan.plataforma,
                plan.margen_neto_pct,
            ),
        ),
        {rol: fp.ParametrosRol(rol, mitad, bids[rol]) for rol in im.ROLES_IMPULSO},
        plan.objetivo,
        fp.Semillas((), (), plan.negatives, plan.keywords_exact),
        roles=im.ROLES_IMPULSO,
    )


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
    vista = previsualiza(conn, solicitud, sugeridor=sugeridor, hoy=hoy)
    if not vista.planes:
        raise ErrorImpulso(422, "nada lanzable: revisa no_lanzables")
    if vista.huella != huella:
        raise ErrorImpulso(409, "el plan cambio desde la vista previa: previsualiza de nuevo")
    if vista.confirmacion_esperada != confirmacion:
        raise ErrorImpulso(
            422, f"confirmacion distinta: se esperaba {vista.confirmacion_esperada!r}"
        )
    if not actor or not actor.strip():
        raise ErrorImpulso(422, "lanzar exige actor")
    lanzados: list[ImpulsoLanzado] = []
    fallos: dict[int, str] = {}
    for plan in vista.planes:
        lote = lote_de_plan(plan)
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"impulso:{plan.listing_id}",))
        existente = conn.execute(_SQL_IMPULSO_DE_LOTE, (lote,)).fetchone()
        if existente is not None:
            estado = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()[0]
            lanzados.append(ImpulsoLanzado(existente[0], lote, estado))
            conn.commit()
            continue
        huerfano = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
        if huerfano is not None:
            grupo = conn.execute(_SQL_GRUPO_DE_LOTE, (lote,)).fetchone()
            if huerfano[0] == "applied" and grupo is not None:
                imp = _sella_impulso(conn, plan, grupo[0], lote, actor, confirmacion)
                conn.commit()
                lanzados.append(ImpulsoLanzado(imp, lote, "applied"))
                continue
            conn.rollback()
            fallos[plan.listing_id] = f"lote {lote} quedo en {huerfano[0]}: reconcilia a mano"
            continue
        vivo = conn.execute(_SQL_VIVO, (plan.plataforma, plan.listing_id)).fetchone()
        if vivo is not None:
            conn.rollback()
            raise ErrorImpulso(
                409,
                f"listing {plan.listing_id} ya tiene un impulso vivo",
                lanzados=lanzados,
            )
        try:
            hecho = ejecutor(_grupo_v2_de_plan(plan), plan.huella(), lote)
        except Exception as exc:
            conn.rollback()
            fallos[plan.listing_id] = f"la fabrica fallo ({exc})"
            continue
        if hecho.estado_lote != "applied":
            conn.rollback()
            fallos[plan.listing_id] = f"lote {lote} no quedo applied"
            continue
        imp = _sella_impulso(conn, plan, hecho.grupo_id, lote, actor, confirmacion)
        conn.commit()
        lanzados.append(ImpulsoLanzado(imp, lote, hecho.estado_lote))
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
 WHERE e.platform = %s
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
    dias = (hoy - lanzado.date()).days
    if corte is None:
        lectura = im.LecturaImpulso(hoy, dias, None, None, None, None)
        conn.commit()
        return (
            AccionVigia(imp, lectura, im.VeredictoImpulso("en_curso", False, "seguir"), "nada"),
            escritor_retiro,
        )
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
                "UPDATE fabrica_lote SET estado = 'pausado' WHERE lote = %s AND estado = 'applied'",
                (lote,),
            )
        hecho = "pausado"
    elif veredicto.accion == "graduar":
        ahora = dt.datetime.now(dt.UTC)
        for (gid,) in conn.execute(_SQL_GOALS_DE_IMPULSO, (imp,)).fetchall():
            goals_write.edita_goal(conn, gid, mode="live", updated_at=ahora)
        hecho = "graduado"
    if hecho == "nada" and (lectura.impresiones or 0) > 0:
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
                hecho = "retiro_de_bolsas"
    conn.execute(
        "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
        " impresiones, clics, gasto, pedidos, veredicto, es_final, hecho)"
        " VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (impulso_id, corte) DO NOTHING",
        (
            imp,
            corte,
            dias,
            impr,
            clics,
            gasto,
            pedidos,
            veredicto.estado,
            veredicto.final,
            hecho,
        ),
    )
    conn.commit()
    return AccionVigia(imp, lectura, veredicto, hecho), escritor_retiro


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
    Aislado por impulso: si uno falla, se revierte el suyo y el resto
    se atiende (el fallido se reintenta manana). Si muere a medias, la
    siguiente corrida relee y termina."""
    acciones = []
    for fila in conn.execute(_SQL_VIVOS).fetchall():
        try:
            accion, escritor_retiro = _vigila_uno(
                conn, pausador, fila, hoy=hoy, escritor_retiro=escritor_retiro
            )
        except Exception:
            conn.rollback()
            continue
        acciones.append(accion)
    return acciones
