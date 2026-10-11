"""Contrato de la pantalla de productos sin clics (BIDS 02, P.4).

Tres secciones: candidatos a impulsar (venden y casi no reciben
clics), impulsos (en curso y terminados, con sus clics de fuera de
Amazon) y productos sin un pedido en 11 meses. Puro contrato:
dataclasses + `como_dict()` + lecturas SOLO SELECT.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

DIAS_PEDIDOS_CANDIDATO = 90
DIAS_CLICS_CANDIDATO = 60
PEDIDOS_MINIMOS_CANDIDATO = 2
CLICS_MAXIMOS_CANDIDATO = 20
DIAS_SIN_PEDIDO = 330


@dataclass(frozen=True)
class ProductoCandidato:
    product_id: int
    sku: str
    nombre: str
    unidades_90d: int
    clics_ads_60d: int | None
    listing_ids: tuple[int, ...]

    def como_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "sku": self.sku,
            "nombre": self.nombre,
            "unidades_90d": self.unidades_90d,
            "clics_ads_60d": self.clics_ads_60d,
            "listing_ids": list(self.listing_ids),
        }


@dataclass(frozen=True)
class ProductoSinPedido:
    product_id: int
    sku: str
    nombre: str
    listing_ids: tuple[int, ...]

    def como_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "sku": self.sku,
            "nombre": self.nombre,
            "listing_ids": list(self.listing_ids),
        }


@dataclass(frozen=True)
class ImpulsoVisto:
    impulso_id: int
    product_id: int
    sku: str
    nombre: str
    subfamilia: str
    lote: str
    estado_lote: str
    moneda: str
    tope_monto: str
    dia: int | None
    plazo_dias: int
    impresiones: int | None
    clics: int | None
    gasto: str | None
    pedidos: int | None
    veredicto: str | None
    frase: str
    clics_fuera_de_amazon: int | None
    retiro_ids: tuple[int, ...]

    def como_dict(self) -> dict:
        return {
            "impulso_id": self.impulso_id,
            "product_id": self.product_id,
            "sku": self.sku,
            "nombre": self.nombre,
            "subfamilia": self.subfamilia,
            "lote": self.lote,
            "estado_lote": self.estado_lote,
            "moneda": self.moneda,
            "tope_monto": self.tope_monto,
            "dia": self.dia,
            "plazo_dias": self.plazo_dias,
            "impresiones": self.impresiones,
            "clics": self.clics,
            "gasto": self.gasto,
            "pedidos": self.pedidos,
            "veredicto": self.veredicto,
            "frase": self.frase,
            "clics_fuera_de_amazon": self.clics_fuera_de_amazon,
            "retiro_ids": list(self.retiro_ids),
        }


@dataclass(frozen=True)
class RetiroAbiertoVisto:
    retiro_id: int
    motivo: str
    ad_id_externo: str
    sku: str
    retirado_el: str

    def como_dict(self) -> dict:
        return {
            "retiro_id": self.retiro_id,
            "motivo": self.motivo,
            "ad_id_externo": self.ad_id_externo,
            "sku": self.sku,
            "retirado_el": self.retirado_el,
        }


@dataclass(frozen=True)
class PantallaProductos:
    candidatos: tuple[ProductoCandidato, ...]
    impulsos: tuple[ImpulsoVisto, ...]
    sin_pedido: tuple[ProductoSinPedido, ...]
    anuncios_sin_producto: int
    retiros_abiertos: tuple[RetiroAbiertoVisto, ...] = ()

    def como_dict(self) -> dict:
        return {
            "candidatos": [c.como_dict() for c in self.candidatos],
            "impulsos": [i.como_dict() for i in self.impulsos],
            "sin_pedido": [s.como_dict() for s in self.sin_pedido],
            "anuncios_sin_producto": self.anuncios_sin_producto,
            "retiros_abiertos": [r.como_dict() for r in self.retiros_abiertos],
        }


def _num(valor: Decimal | int) -> str:
    if isinstance(valor, Decimal) and valor == valor.to_integral_value():
        return f"{int(valor):,}"
    if isinstance(valor, Decimal):
        return f"{valor:,.2f}"
    return f"{valor:,}"


def frase_en_curso(
    *,
    dia: int,
    plazo: int,
    impresiones: int | None,
    clics: int | None,
    gasto: Decimal | None,
    tope: Decimal,
    moneda: str,
    pedidos: int | None,
    pausado: bool = False,
) -> str:
    """Día 6 de 14 · 1,840 impresiones · 14 clics · 48 de 350 MXN ·
    sin pedidos · sigue aprendiendo (o 'pausado a mano' si se pausó)."""
    impr = f"{_num(impresiones)} impresiones" if impresiones is not None else "— impresiones"
    cli = f"{_num(clics)} clics" if clics is not None else "— clics"
    gas = _num(gasto) if gasto is not None else "—"
    if pedidos is None:
        ped = "— pedidos"
    elif pedidos == 0:
        ped = "sin pedidos"
    elif pedidos == 1:
        ped = "1 pedido"
    else:
        ped = f"{_num(pedidos)} pedidos"
    cola = "pausado a mano" if pausado else "sigue aprendiendo"
    return (
        f"Día {dia} de {plazo} · {impr} · {cli} · {gas} de {_num(tope)} {moneda} · {ped} · {cola}"
    )


FRASES_FINALES = {
    "sin_impresiones": "No recibió impresiones. Orbit lo pausó.",
    "impresiones_sin_clics": "Recibió impresiones y casi ningún clic. Orbit lo pausó.",
    "clics_sin_ventas": "Recibió clics y no vendió. Orbit lo pausó.",
    "vende": "Vende. Sigue encendido y ya lo cuida el motor.",
}


def frase_final(veredicto: str) -> str:
    return FRASES_FINALES[veredicto]


def frase_fuera_de_amazon(clics_fuera: int | None, clics: int | None) -> str | None:
    """3 de sus 14 clics vinieron de fuera de Amazon. Sin dato, None
    (la plantilla pinta el guion)."""
    if clics_fuera is None or clics is None:
        return None
    return f"{_num(clics_fuera)} de sus {_num(clics)} clics vinieron de fuera de Amazon."


_SQL_CANDIDATOS = """
WITH activos AS (
    SELECT DISTINCT li.product_id FROM ad_entity pa
      JOIN ad_entity ag ON ag.id = pa.parent_id JOIN ad_entity c ON c.id = ag.parent_id
      JOIN ad_entity_state sp ON sp.ad_entity_id = pa.id AND sp.status = 'ENABLED'
      JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id AND sg.status = 'ENABLED'
      JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
      JOIN listing li ON li.id = pa.listing_id
     WHERE pa.platform = %s AND pa.kind = 'product_ad'
), ventas AS (
    SELECT product_id, sum(quantity)::bigint AS unidades FROM ledger_event
     WHERE platform = %s AND kind = 'sale' AND event_date >= %s::date - %s GROUP BY 1
), clics AS (
    SELECT li.product_id, sum(u.clicks)::bigint AS clics
      FROM (SELECT DISTINCT ON (advertised_asin, advertised_sku, metric_date)
                   advertised_asin, clicks
              FROM ads_product_metric_observation
             WHERE platform = %s AND metric_date >= %s::date - %s
             ORDER BY advertised_asin, advertised_sku, metric_date, observed_at DESC) u
      JOIN listing li ON li.platform = %s AND li.external_id = u.advertised_asin GROUP BY 1)
SELECT a.product_id, p.odoo_sku, p.name, v.unidades, c.clics,
       (SELECT array_agg(li.id ORDER BY li.id) FROM listing li
         WHERE li.product_id = a.product_id AND li.platform = %s)
  FROM activos a JOIN ventas v USING (product_id)
  LEFT JOIN clics c USING (product_id)
  JOIN product p ON p.id = a.product_id
 WHERE v.unidades >= %s AND coalesce(c.clics, 0) < %s ORDER BY 1
"""

_SQL_SIN_PEDIDO = """
WITH activos AS (
    SELECT DISTINCT li.product_id FROM ad_entity pa
      JOIN ad_entity ag ON ag.id = pa.parent_id JOIN ad_entity c ON c.id = ag.parent_id
      JOIN ad_entity_state sp ON sp.ad_entity_id = pa.id AND sp.status = 'ENABLED'
      JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id AND sg.status = 'ENABLED'
      JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
      JOIN listing li ON li.id = pa.listing_id
     WHERE pa.platform = %s AND pa.kind = 'product_ad'
), ventas AS (
    SELECT product_id, sum(quantity)::bigint AS unidades FROM ledger_event
     WHERE platform = %s AND kind = 'sale' AND event_date >= %s::date - %s GROUP BY 1
)
SELECT a.product_id, p.odoo_sku, p.name,
       (SELECT array_agg(li.id ORDER BY li.id) FROM listing li
         WHERE li.product_id = a.product_id AND li.platform = %s)
  FROM activos a
  LEFT JOIN ventas v USING (product_id)
  JOIN product p ON p.id = a.product_id
 WHERE coalesce(v.unidades, 0) = 0 ORDER BY 1
"""

_SQL_SIN_PRODUCTO = """
SELECT count(*) FROM ad_entity pa
  JOIN ad_entity ag ON ag.id = pa.parent_id JOIN ad_entity c ON c.id = ag.parent_id
  JOIN ad_entity_state sp ON sp.ad_entity_id = pa.id AND sp.status = 'ENABLED'
  JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id AND sg.status = 'ENABLED'
  JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
 WHERE pa.platform = %s AND pa.kind = 'product_ad' AND pa.listing_id IS NULL
"""

_SQL_IMPULSOS = """
SELECT v.id, v.product_id, p.odoo_sku, p.name, v.subfamilia, v.lote, v.estado_lote,
       v.tope_moneda, v.tope_monto, v.plazo_dias,
       v.corte, v.dias_observados, v.impresiones, v.clics, v.gasto, v.pedidos, v.veredicto
  FROM v_impulso_estado v
  JOIN product p ON p.id = v.product_id
 WHERE v.platform = %s
 ORDER BY (v.es_final IS TRUE), v.id DESC
"""

_SQL_CLICS_FUERA = """
SELECT i.id, sum(u.clicks)::bigint
  FROM impulso i
  JOIN campana_grupo_rol r ON r.grupo_id = i.grupo_id
  JOIN (SELECT DISTINCT ON (o.ad_entity_id, o.metric_date) o.ad_entity_id, o.clicks,
               o.metric_date
          FROM ads_placement_observation o
         WHERE o.platform = %s AND o.placement = 'fuera_de_amazon'
         ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC) u
    ON u.ad_entity_id = r.ad_entity_id AND u.metric_date >= i.lanzado_el::date
 WHERE i.platform = %s
 GROUP BY i.id
"""

_SQL_RETIROS_ABIERTOS = """
SELECT r.impulso_id, array_agg(r.id ORDER BY r.id)
  FROM anuncio_retiro r
 WHERE r.impulso_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM anuncio_reposicion p WHERE p.retiro_id = r.id)
 GROUP BY r.impulso_id
"""

_SQL_RETIROS_ABIERTOS_SUELTOS = """
SELECT r.id, r.motivo, r.ad_id_externo, r.sku,
       to_char(r.retirado_el AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI')
  FROM anuncio_retiro r
 WHERE r.platform = %s AND r.impulso_id IS NULL
   AND NOT EXISTS (SELECT 1 FROM anuncio_reposicion p WHERE p.retiro_id = r.id)
 ORDER BY r.id
"""


def lee_productos(conn, plataforma: str, hoy: dt.date) -> PantallaProductos:
    """Las secciones de /productos-sin-clics, solo SELECT."""
    candidatos = tuple(
        ProductoCandidato(
            f[0],
            f[1] or "",
            f[2] or "",
            int(f[3]),
            None if f[4] is None else int(f[4]),
            tuple(f[5] or ()),
        )
        for f in conn.execute(
            _SQL_CANDIDATOS,
            (
                plataforma,
                plataforma,
                hoy,
                DIAS_PEDIDOS_CANDIDATO,
                plataforma,
                hoy,
                DIAS_CLICS_CANDIDATO,
                plataforma,
                plataforma,
                PEDIDOS_MINIMOS_CANDIDATO,
                CLICS_MAXIMOS_CANDIDATO,
            ),
        ).fetchall()
    )
    sin_pedido = tuple(
        ProductoSinPedido(f[0], f[1] or "", f[2] or "", tuple(f[3] or ()))
        for f in conn.execute(
            _SQL_SIN_PEDIDO, (plataforma, plataforma, hoy, DIAS_SIN_PEDIDO, plataforma)
        ).fetchall()
    )
    sin_producto = conn.execute(_SQL_SIN_PRODUCTO, (plataforma,)).fetchone()[0]
    fuera = {
        f[0]: (None if f[1] is None else int(f[1]))
        for f in conn.execute(_SQL_CLICS_FUERA, (plataforma, plataforma)).fetchall()
    }
    abiertos: dict[int, tuple[int, ...]] = {
        f[0]: tuple(f[1]) for f in conn.execute(_SQL_RETIROS_ABIERTOS).fetchall()
    }
    impulsos = []
    for f in conn.execute(_SQL_IMPULSOS, (plataforma,)).fetchall():
        (
            iid,
            pid,
            sku,
            nombre,
            sub,
            lote,
            estado,
            moneda,
            tope,
            plazo,
            corte,
            dias,
            impr,
            cli,
            gasto,
            ped,
            veredicto,
        ) = f
        tope_d = Decimal(tope)
        dia_n = None if dias is None else min(int(dias) + 1, int(plazo))
        cli_n = None if cli is None else int(cli)
        fue = fuera.get(iid)
        if fue is not None and cli_n is not None:
            fue = min(fue, cli_n)
        if veredicto is None:
            frase = "Aún sin lectura."
        elif veredicto == "en_curso":
            frase = frase_en_curso(
                dia=dia_n,
                plazo=int(plazo),
                impresiones=None if impr is None else int(impr),
                clics=cli_n,
                gasto=None if gasto is None else Decimal(gasto),
                tope=tope_d,
                moneda=moneda,
                pedidos=None if ped is None else int(ped),
                pausado=estado == "pausado",
            )
        else:
            frase = frase_final(veredicto)
        impulsos.append(
            ImpulsoVisto(
                impulso_id=iid,
                product_id=pid,
                sku=sku or "",
                nombre=nombre or "",
                subfamilia=sub,
                lote=lote,
                estado_lote=estado,
                moneda=moneda,
                tope_monto=str(tope_d),
                dia=dia_n,
                plazo_dias=int(plazo),
                impresiones=None if impr is None else int(impr),
                clics=cli_n,
                gasto=None if gasto is None else str(Decimal(gasto)),
                pedidos=None if ped is None else int(ped),
                veredicto=veredicto,
                frase=frase,
                clics_fuera_de_amazon=fue,
                retiro_ids=abiertos.get(iid, ()),
            )
        )
    sueltos = tuple(
        RetiroAbiertoVisto(f[0], f[1], f[2], f[3] or "", f[4])
        for f in conn.execute(_SQL_RETIROS_ABIERTOS_SUELTOS, (plataforma,)).fetchall()
    )
    conn.commit()
    return PantallaProductos(
        candidatos=candidatos,
        impulsos=tuple(impulsos),
        sin_pedido=sin_pedido,
        anuncios_sin_producto=sin_producto,
        retiros_abiertos=sueltos,
    )
