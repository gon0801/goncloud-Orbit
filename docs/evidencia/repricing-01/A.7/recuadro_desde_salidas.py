"""Arma el recuadro S10 desde las salidas de `consultas/*.sql` (A.7, DoD (g)).

Lo corre el lead contra produccion: por cada consulta, `psql -tA -F'|' -f
consultas/NN_*.sql > salida.txt`, y luego este guion con esas salidas y
`--max-dias-sin-reportar N` explicito (el lead lo corre con `3`).
Alimenta `app.precio.cobertura` igual que el tool: el recuadro impreso
tiene el mismo formato que `tools/precio_cobertura.py`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parents[4].as_posix())

from app.precio.cobertura import (  # noqa: E402
    FilaPublicacion,
    Recuadro,
    armar_recuadro,
    aviso_puente,
    cuadra_exact,
)


def _lineas(ruta: str) -> list[list[str]]:
    filas = []
    for cruda in Path(ruta).read_text(encoding="utf-8").splitlines():
        if cruda.strip():
            filas.append(cruda.split("|"))
    return filas


def _entero_o_nulo(texto: str):
    texto = texto.strip()
    return None if not texto else int(texto)


def recuadro_desde_archivos(
    canonicas: str,
    canales: str,
    goals: str,
    decisiones: str,
    puente_f: str,
    hoy_f: str,
    *,
    platform: str,
    max_dias: int,
) -> Recuadro:
    """Recuadro desde salidas `psql -tA` (una fila por linea, `|`)."""
    hoy = date.fromisoformat(_lineas(hoy_f)[0][0].strip())
    canal_por_listing = {}
    for partes in _lineas(canales):
        precio = Decimal(partes[2]) if len(partes) > 2 and partes[2].strip() else None
        moneda = partes[3].strip() if len(partes) > 3 and partes[3].strip() else None
        canal = partes[1].strip() or None
        canal_por_listing[int(partes[0])] = (canal, precio, moneda)
    goals_vigentes = {_entero_o_nulo(partes[0]) for partes in _lineas(goals)}
    decision_por_listing = {}
    for partes in _lineas(decisiones):
        motivo = partes[2].strip() if len(partes) > 2 else ""
        decision_por_listing[int(partes[0])] = (partes[1].strip(), motivo or None)
    filas = []
    for partes in _lineas(canonicas):
        listing_id = int(partes[0])
        observado = datetime.fromisoformat(partes[5].strip())
        canal, precio, moneda = canal_por_listing.get(listing_id, (None, None, None))
        resultado, motivo = decision_por_listing.get(listing_id, (None, None))
        filas.append(
            FilaPublicacion(
                listing_id=listing_id,
                seller_sku=partes[1],
                platform=platform,
                canal=canal,
                precio=precio,
                moneda=moneda,
                dias_sin_reportar=(hoy - observado.date()).days,
                tiene_goal=listing_id in goals_vigentes,
                resultado_hoy=resultado,
                motivo_hoy=motivo,
            )
        )
    return armar_recuadro(filas, platform=platform, max_dias=max_dias)


def _imprimir(rec: Recuadro, *, puente: int, hoy: str) -> None:
    marca = "[CUADRA]" if cuadra_exact(rec) else "[NO-CUADRA]"
    no_ev = " ".join(f"{m}={n}" for m, n in rec.no_evaluadas)
    fuera = " ".join(f"{f}={n}" for f, n in rec.fuera_de_alcance)
    print(f"recuadro {rec.platform} {hoy} (salidas A.7): activas={rec.activas}")
    print(
        f"  evaluadas={rec.evaluadas} no_evaluadas={sum(n for _, n in rec.no_evaluadas)}"
        f" [{no_ev}] sin_goal={len(rec.sin_goal)}"
        f" fuera_de_alcance={sum(n for _, n in rec.fuera_de_alcance)}"
        f" [{fuera}] {marca}"
    )
    for detalle in rec.sin_goal:
        if detalle.precio is None:
            precio = "sin_precio"
        else:
            precio = f"{detalle.precio:.2f} {detalle.moneda}"
        print(f"  sin_goal: {detalle.sku} ({precio}, {detalle.canal})")
    print(f"  puente bridge={puente} vs canonica={rec.activas}")
    for aviso in rec.avisos:
        print(f"  {aviso}")
    puente_aviso = aviso_puente(activas=rec.activas, puente=puente)
    if puente_aviso is not None:
        print(f"  {puente_aviso}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canonicas", required=True)
    ap.add_argument("--canales", required=True)
    ap.add_argument("--goals", required=True)
    ap.add_argument("--decisiones", required=True)
    ap.add_argument("--puente", required=True)
    ap.add_argument("--hoy", required=True)
    ap.add_argument("--platform", required=True)
    ap.add_argument("--max-dias-sin-reportar", required=True, type=int)
    args = ap.parse_args(argv)
    rec = recuadro_desde_archivos(
        args.canonicas,
        args.canales,
        args.goals,
        args.decisiones,
        args.puente,
        args.hoy,
        platform=args.platform,
        max_dias=args.max_dias_sin_reportar,
    )
    hoy = Path(args.hoy).read_text(encoding="utf-8").strip().splitlines()[0]
    puente = int(Path(args.puente).read_text(encoding="utf-8").strip().splitlines()[0])
    _imprimir(rec, puente=puente, hoy=hoy)
    return 0 if cuadra_exact(rec) else 1


if __name__ == "__main__":
    sys.exit(main())
