"""Tipos, PausadorFabrica y vista previa del impulso (BIDS 02, I.3).

Extraido de `app/impulso_io.py` (Ola D2). `_conecta_propia` pasa siempre por
`app.db.connect` (redaccion de secretos y override de host) y `PausadorFabrica`
cierra tanto su `httpx.Client` como la conexion propia al salir de `with`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from psycopg.conninfo import make_conninfo

from app import db, goals_write, retiro_anuncios
from app import fabrica_bids as br
from app import fabrica_plan as fp
from app import impulso as im
from app.ads.campana_config import presupuesto_p90
from app.fabrica_plan import MONEDA_POR_PLATAFORMA
from app.optimizer.goals import (
    TEXTO_FALTA_CONFIRMAR_PRESUPUESTO_ALTO,
    gasto_para_concluir_desde_settings,
    presupuesto_alto,
)

_SQL_ESTADO_LOTE = """
SELECT estado, pausado_por FROM fabrica_lote WHERE lote = %s
"""

_SQL_VIVO_OTRO_LISTING = """
SELECT i.listing_id FROM impulso i
 LEFT JOIN impulso_lectura l ON l.impulso_id = i.id AND l.es_final
 WHERE i.platform = %s AND i.product_id = %s AND i.listing_id <> %s
   AND l.impulso_id IS NULL
 LIMIT 1
"""


class ErrorImpulso(Exception):
    """Error de dominio del impulso con su status HTTP (lo traduce I.5)."""

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
    p90: Decimal | None = None

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
    atendido: bool = True


class EjecutorFabrica(Protocol):
    def __call__(self, plan_grupo, huella: str, lote: str) -> EjecucionFabrica: ...


class PausadorDeCampanas(Protocol):
    def pausa(self, lote: str) -> None: ...
    def reanuda(self, lote: str) -> None: ...
    def reactiva(self, lote: str) -> None: ...
    def estados(self, lote: str) -> dict[str, str]: ...


class PausadorFabrica:
    """PausadorDeCampanas con ledger pre-PUT, readback y armado lazy de HTTP."""

    def __init__(self, conn, *, actor: str = "impulso-vigia") -> None:
        self._conn, self._actor, self._ctxs = conn, actor, {}
        self._armado, self._propia = None, None

    def close(self) -> None:
        armado, propia = self._armado, self._propia
        self._armado, self._propia = None, None
        self._ctxs.clear()
        if armado is not None and hasattr(armado[2], "close"):
            armado[2].close()
        if propia is not None:
            propia.close()

    def __enter__(self) -> PausadorFabrica:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _ctx_para(self, lote: str):
        from app import fabrica_motor as fm
        from app.ads.config import AdsCredentials

        if self._armado is None:
            cred = AdsCredentials.from_secrets_dir()
            cliente = fm.AdsClient(cred)
            http = fm.httpx.Client(
                timeout=fm.httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
            )
            self._armado = (cred, cliente, http, fm._perfiles(cliente))
        cred, cliente, http, perfiles = self._armado
        fila = self._conn.execute(
            "SELECT platform::text FROM fabrica_lote WHERE lote = %s", (lote,)
        ).fetchone()
        self._conn.commit()
        if fila is None or fila[0] not in perfiles:
            raise ErrorImpulso(422, f"sin perfil aceptado para el lote {lote}")
        platform = fila[0]
        if platform not in self._ctxs:
            self._ctxs[platform] = fm._Ctx(
                http, fm._token_lwa(cred, http), cred, cliente, perfiles[platform], self._conn, lote
            )
        ctx = self._ctxs[platform]
        ctx.lote = lote
        return ctx

    def pausa(self, lote: str) -> None:
        from app import fabrica_datos as fd
        from app import fabrica_lotes as fl

        try:
            fl.pausar_lote(self._ctx_para(lote), lote, self._actor)
        except fd.Abortar as exc:
            if self._lote_no_pausable(lote):
                raise ErrorImpulso(409, str(exc)) from None
            raise

    def _lote_no_pausable(self, lote: str) -> bool:
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
        from app import fabrica_lotes as fl

        fl.reanudar_lote(self._ctx_para(lote), lote, self._actor)

    def reactiva(self, lote: str) -> None:
        from app import fabrica_lotes as fl

        fl.reactiva_lote(self._ctx_para(lote), lote, self._actor)

    def estados(self, lote: str) -> dict[str, str]:
        from app import fabrica_motor as fm

        ctx = self._ctx_para(lote)
        filas = self._conn.execute(fm._SQL_EXTERNALS_CAMPANA_DEL_LOTE, (lote,)).fetchall()
        self._conn.commit()
        estados = {}
        for (external,) in filas:
            leido = fm._readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
            estados[external] = leido.get("state") if isinstance(leido, dict) else None
        return estados

    def pausa_huerfanas(self, lote: str) -> bool:
        """Pausa las campanas ENABLED del lote (PUT + readback por campana, con ledger)."""
        from app import fabrica_datos as fd
        from app import fabrica_motor as fm

        ctx = self._ctx_para(lote)
        filas = self._conn.execute(
            "SELECT DISTINCT p.rol::text, p.external_id FROM fabrica_lote_paso p"
            " WHERE p.lote = %s AND p.recurso = 'campaign' AND p.external_id IS NOT NULL",
            (lote,),
        ).fetchall()
        ctx.orden = self._conn.execute(
            "SELECT coalesce(max(orden), 0) FROM fabrica_lote_paso WHERE lote = %s",
            (lote,),
        ).fetchone()[0]
        self._conn.commit()
        pauso = False
        pendientes: list[str] = []
        for rol, external in filas:
            leido = fm._readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
            if not isinstance(leido, dict) or leido.get("state") != "ENABLED":
                continue
            paso = fp.Paso(rol, "campaign", "/sp/campaigns", {}, "reconciliar huerfana")
            paso_id = fm._inserta_paso(ctx, paso, {"campaignId": str(external), "state": "PAUSED"})
            ack = fm._put_estado_campana(ctx, external, "PAUSED")
            visto = None
            if fp.ack_ok(ack):
                releido = fm._readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
                visto = releido.get("state") if isinstance(releido, dict) else None
            fm._sella_paso(ctx, paso_id, visto == "PAUSED", external, ack, visto)
            if visto == "PAUSED":
                pauso = True
            else:
                pendientes.append(str(external))
        if pendientes:
            raise fd.Abortar(f"reconciliacion de {lote} incompleta: siguen ENABLED {pendientes}")
        return pauso


def _conecta_propia(conn, *, autocommit: bool):
    """Conexion propia a la misma base via `app.db.connect` (redaccion y override)."""
    dsn = conn.info.dsn
    if conn.info.password:
        dsn = make_conninfo(dsn, password=conn.info.password)
    return db.connect(dsn, dbname=conn.info.dbname, autocommit=autocommit)


def _pausador_propio(conn) -> PausadorFabrica:
    """PausadorFabrica con conexion propia; debe usarse con `with` para cerrar HTTP y DB."""
    propia = _conecta_propia(conn, autocommit=False)
    pausador = PausadorFabrica(propia)
    pausador._propia = propia
    return pausador


def _reactiva_para_sellar(conn, lote: str) -> None:
    with _pausador_propio(conn) as pausador:
        pausador.reactiva(lote)


def lote_de_plan(plan: im.PlanImpulso) -> str:
    return f"impulso-{plan.listing_id}-{plan.huella()[:8]}"


def _huella_vista(planes: tuple[im.PlanImpulso, ...]) -> str:
    return hashlib.sha256("".join(sorted(p.huella() for p in planes)).encode("utf-8")).hexdigest()


def sugeridor_amazon(cliente, profile_id: str | int, moneda: str):
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
    from app import fabrica_datos as fd

    args = fd.SolicitudDatosPlan(
        listing_ids=str(listing_id),
        plataforma=plataforma,
        origen_objetivo="margen_medido",
        target_acos=None,
    )
    publicaciones, objetivo, semillas, _existentes = fd._datos_plan_v2(args, conn, subfamilia)
    if len(publicaciones) != 1:
        raise fp.PlanInvalido(
            f"impulso exige un producto: listing {listing_id} dio {len(publicaciones)}"
        )
    pub = publicaciones[0]
    moneda = MONEDA_POR_PLATAFORMA[plataforma]
    monto = (
        tope_monto
        if tope_monto is not None
        else gasto_para_concluir_desde_settings(settings, plataforma)
    )
    tope = im.TopeAprendizaje(monto, moneda, im.presupuesto_diario(monto, moneda))
    expresiones = {rol: fp.expresiones_bid(semillas, rol) for rol in im.ROLES_IMPULSO}
    bids = sugeridor((pub.asin,), expresiones)
    bid_por_rol = {rol: bids.get(rol) for rol in im.ROLES_IMPULSO}
    _, acotados = im.acota_bids(bid_por_rol, tope.presupuesto_diario / 2)
    retiros = retiro_anuncios.planea_retiro(
        conn, plataforma, listing_ids=(pub.listing_id,), motivo="impulso", conservar_grupo_id=None
    ).anuncios
    plan = im.PlanImpulso(
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
    fp._valida_plan_v2(im.grupo_v2_de_plan(plan))
    return plan


def aviso_presupuesto_alto(plan: im.PlanImpulso, p90: Decimal | None) -> str | None:
    mitad = (plan.tope.presupuesto_diario / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    aviso = presupuesto_alto(mitad, plan.tope.moneda, p90, "tus_campanas")
    return None if aviso is None else f"Cada campaña de este producto: {aviso.texto()}"


def _exige_acepto_presupuesto(vista: VistaPreviaImpulso, acepto: bool) -> None:
    avisos = [aviso for plan in vista.planes if (aviso := aviso_presupuesto_alto(plan, vista.p90))]
    if avisos and not acepto:
        raise ErrorImpulso(422, TEXTO_FALTA_CONFIRMAR_PRESUPUESTO_ALTO)


def previsualiza(
    conn, solicitud: SolicitudImpulso, *, sugeridor=None, hoy: dt.date | None = None
) -> VistaPreviaImpulso:
    """Solo lecturas mas la sugerencia de bids (una llamada por producto)."""
    if not solicitud.listing_ids:
        raise ErrorImpulso(422, "impulso sin productos: listing_ids vacio")
    if sugeridor is None:
        raise ErrorImpulso(503, "sin sugeridor de bids")
    dia = hoy or dt.datetime.now(dt.UTC).date()
    moneda = MONEDA_POR_PLATAFORMA[solicitud.plataforma]
    p90 = presupuesto_p90(conn, moneda)
    settings = dict(db.settings_vigentes(conn) or {})
    planes = []
    no_lanzables = []
    productos_en_vista: set[int] = set()
    for lid in sorted(set(solicitud.listing_ids)):
        subfamilia = solicitud.subfamilia_por_listing.get(lid)
        if not subfamilia:
            no_lanzables.append(NoLanzable(lid, "sin subfamilia del dueno"))
            continue
        try:
            plan = _plan_producto(
                conn,
                solicitud.plataforma,
                lid,
                subfamilia,
                solicitud.tope_monto,
                sugeridor,
                dia,
                settings,
            )
        except fp.PlanInvalido as exc:
            no_lanzables.append(NoLanzable(lid, str(exc)))
            continue
        if plan.product_id in productos_en_vista:
            no_lanzables.append(
                NoLanzable(lid, "el producto ya va en este lanzamiento (tope por producto)")
            )
            continue
        vivo_otro = conn.execute(
            _SQL_VIVO_OTRO_LISTING, (solicitud.plataforma, plan.product_id, plan.listing_id)
        ).fetchone()
        if vivo_otro is not None:
            no_lanzables.append(
                NoLanzable(
                    lid,
                    f"el producto ya tiene un impulso vivo en el listing {vivo_otro[0]}"
                    " (tope por producto)",
                )
            )
            continue
        productos_en_vista.add(plan.product_id)
        planes.append(plan)
    planes_t = tuple(planes)
    n = len(planes_t)
    return VistaPreviaImpulso(
        planes=planes_t,
        no_lanzables=tuple(no_lanzables),
        huella=_huella_vista(planes_t),
        confirmacion_esperada=f"LANZAR IMPULSO DE {n} PRODUCTO{'S' if n != 1 else ''}",
        presupuesto_diario_total=sum((p.tope.presupuesto_diario for p in planes_t), Decimal("0")),
        tope_total=sum((p.tope.monto for p in planes_t), Decimal("0")),
        moneda=moneda,
        p90=p90,
    )


_SQL_SETTINGS = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
_ = goals_write  # mantenido en dependencia del dominio de impulso
