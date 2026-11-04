"""Costo historico de los dos SKU Peseta en la SQLite de contabilidad.

Corre en el HOST goncloud (python3 del sistema), NO dentro de un contenedor:
la base vive en /mnt/data/appdata/accounting/data/accounting.db.

Espejo de la migracion 0011 de Orbit (ORBIT 06, palanca de mapeo 2026-09-02).
Sin esto, el siguiente `ingest costs` rechaza ambos SKU por divergencia con el
origen. Importes sellados por el dueno el 2026-09-02, derivados de los
hermanos de familia con historia desde el backfill 2026-02-20:

  NH-GAM-NEG-PESETA-PLA  325.00 MXN  desde 2026-02-20 (corre valid_from de la
                                     fila abierta: mismo costo, UNA vigencia)
  NH-NOG-VEN-PESETA-DOR  458.00 MXN  [2026-02-20, 2026-08-18)  (fila nueva
                                     cerrada; la abierta de 459.29 no se toca)

Fail-closed en AMBAS direcciones: valida el estado de partida y no escribe nada
si no es el esperado. Respalda la tabla antes de tocarla y escribe en UNA sola
transaccion. No es re-runnable.

  --dry-run    valida y muestra el plan; no escribe.
  --revertir   deshace el cambio (espejo de 0011_reversa_costo_historico_peseta.sql):
               devuelve valid_from de plata al 2026-08-18 y borra la fila
               historica de oro por su `source`, jamas por su importe.

El sync horario de Odoo (sync_cogs_odoo.py) solo mira
`WHERE sku=? AND valid_to IS NULL`: no ve la fila cerrada y no toca la abierta
mientras el costo no cambie.
"""

import argparse
import datetime
import sqlite3
import sys

DB = "/mnt/data/appdata/accounting/data/accounting.db"
PLA = "NH-GAM-NEG-PESETA-PLA"
DOR = "NH-NOG-VEN-PESETA-DOR"
DESDE = "2026-02-20 05:00:02"  # misma hora que el backfill de sus hermanos
CORTE = "2026-08-18 05:00:07"  # instante exacto de la vigencia publicada
COSTO_PLA = 325.0
COSTO_DOR_VIGENTE = 459.29
COSTO_DOR_HISTORICO = 458.0
FUENTE_0011 = "orbit_0011_costo_historico"


class EstadoInesperado(RuntimeError):
    """Guarda fail-closed. NO se usa `assert`: con `python3 -O` desaparece y el
    script correria el plan sin validar nada (hallazgo CodeRabbit, PR #118)."""


def exigir(condicion, mensaje):
    if not condicion:
        raise EstadoInesperado(mensaje)


def igual(a, b, tolerancia=0.005):
    return a is not None and abs(float(a) - b) < tolerancia


ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument(
    "--revertir", action="store_true", help="deshace el cambio: espejo de 0011_reversa_*.sql"
)
ap.add_argument("--dry-run", action="store_true", help="valida y muestra el plan; no escribe nada")
args = ap.parse_args()

# mode=rw (no rwc): si la ruta no existe, REVIENTA en vez de crear una
# accounting.db vacia y "validar" contra ella (hallazgo CodeRabbit, PR #118).
# timeout: contabilidad escribe desde su propio contenedor; sin esto un lock
# concurrente aborta al instante en vez de esperar su turno.
con = sqlite3.connect(f"file:{DB}?mode=rw", uri=True, timeout=30, isolation_level=None)
con.row_factory = sqlite3.Row

# BEGIN IMMEDIATE ANTES de leer: toma el lock de escritura, asi que el estado
# que validan las guardas es el MISMO que se muta. Sin esto hay una ventana
# entre validar y escribir en la que otro escritor puede mover las filas y el
# plan aplicaria sobre un estado ya no validado (hallazgo CodeRabbit, PR #118).
con.execute("BEGIN IMMEDIATE")

try:
    filas = {
        sku: con.execute(
            "SELECT id, cost, currency, valid_from, valid_to, source FROM sku_costs"
            " WHERE sku=? ORDER BY id",
            (sku,),
        ).fetchall()
        for sku in (PLA, DOR)
    }
    antes = con.execute("SELECT count(*) FROM sku_costs").fetchone()[0]

    if not args.revertir:
        # --- guardas de IDA: igualdad EXACTA contra el estado medido --------
        for sku, esperado in ((PLA, COSTO_PLA), (DOR, COSTO_DOR_VIGENTE)):
            exigir(len(filas[sku]) == 1, f"{sku}: {len(filas[sku])} filas, se esperaba 1")
            r = filas[sku][0]
            exigir(igual(r["cost"], esperado), f"{sku}: costo {r['cost']}, esperado {esperado}")
            exigir(r["valid_to"] is None, f"{sku}: la fila no esta abierta")
            exigir(r["valid_from"] == CORTE, f"{sku}: valid_from {r['valid_from']!r} != {CORTE!r}")
            exigir((r["currency"] or "MXN") == "MXN", f"{sku}: moneda {r['currency']}")

        plan = [
            ("UPDATE sku_costs SET valid_from=? WHERE id=?", (DESDE, filas[PLA][0]["id"])),
            (
                "INSERT INTO sku_costs (sku, cost, currency, valid_from, valid_to, source,"
                f" created_at) VALUES (?, {COSTO_DOR_HISTORICO}, 'MXN', ?, ?, ?, ?)",
                (DOR, DESDE, CORTE, FUENTE_0011, DESDE),
            ),
        ]
    else:
        # --- guardas de VUELTA: el estado es EXACTAMENTE el que dejo la ida --
        exigir(len(filas[PLA]) == 1, f"{PLA}: {len(filas[PLA])} filas, se esperaba 1")
        r = filas[PLA][0]
        exigir(r["valid_from"] == DESDE, f"{PLA}: valid_from {r['valid_from']!r} — ¿ida aplicada?")
        exigir(r["valid_to"] is None, f"{PLA}: la fila no esta abierta")
        exigir(igual(r["cost"], COSTO_PLA), f"{PLA}: costo {r['cost']} != {COSTO_PLA}")
        exigir((r["currency"] or "MXN") == "MXN", f"{PLA}: moneda {r['currency']}")

        exigir(len(filas[DOR]) == 2, f"{DOR}: {len(filas[DOR])} filas, se esperaban 2")
        historicas = [f for f in filas[DOR] if f["source"] == FUENTE_0011]
        exigir(len(historicas) == 1, f"{DOR}: {len(historicas)} filas de 0011, se esperaba 1")
        h = historicas[0]
        exigir(igual(h["cost"], COSTO_DOR_HISTORICO), f"{DOR} historica: costo {h['cost']}")
        exigir(h["valid_from"] == DESDE, f"{DOR} historica: valid_from {h['valid_from']!r}")
        exigir(h["valid_to"] == CORTE, f"{DOR} historica: valid_to {h['valid_to']!r}")
        exigir((h["currency"] or "MXN") == "MXN", f"{DOR} historica: moneda {h['currency']}")
        vigentes = [f for f in filas[DOR] if f["id"] != h["id"]]
        exigir(igual(vigentes[0]["cost"], COSTO_DOR_VIGENTE), f"{DOR} vigente: la ida la toco")
        exigir(vigentes[0]["valid_to"] is None, f"{DOR} vigente: no esta abierta")

        plan = [
            ("UPDATE sku_costs SET valid_from=? WHERE id=?", (CORTE, filas[PLA][0]["id"])),
            ("DELETE FROM sku_costs WHERE id=? AND source=?", (h["id"], FUENTE_0011)),
        ]

    if args.dry_run:
        print(f"DRY-RUN ({'reversa' if args.revertir else 'ida'}): no se escribio nada. Plan:")
        for sql, par in plan:
            print("  ", " ".join(sql.split())[:90], "|", par)
        # SystemExit es BaseException: el `except` de abajo hace el ROLLBACK y
        # suelta el lock. UN solo camino de rollback, no dos (un segundo
        # ROLLBACK sobre una transaccion ya cerrada es un error).
        sys.exit(0)

    # El respaldo se toma DENTRO de la transaccion: retrata lo que se valido.
    sello = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    respaldo = f"/mnt/data/appdata/accounting/data/sku_costs-backup-{sello}.sql"
    with open(respaldo, "w", encoding="utf-8") as f:
        for linea in con.iterdump():
            if "sku_costs" in linea:
                f.write(linea + "\n")

    for sql, par in plan:
        con.execute(sql, par)
    con.execute("COMMIT")
except BaseException:
    con.execute("ROLLBACK")
    raise

despues = con.execute("SELECT count(*) FROM sku_costs").fetchone()[0]
print(f"respaldo: {respaldo} | filas {antes} -> {despues}")
for sku in (PLA, DOR):
    for r in con.execute(
        "SELECT sku, cost, currency, valid_from, valid_to, source FROM sku_costs"
        " WHERE sku=? ORDER BY valid_from",
        (sku,),
    ):
        print(" | ".join("" if x is None else str(x) for x in r))
