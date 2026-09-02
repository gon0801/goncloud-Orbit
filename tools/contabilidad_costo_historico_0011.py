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

DB = "/mnt/data/appdata/accounting/data/accounting.db"
PLA = "NH-GAM-NEG-PESETA-PLA"
DOR = "NH-NOG-VEN-PESETA-DOR"
DESDE = "2026-02-20 05:00:02"  # misma hora que el backfill de sus hermanos
CORTE = "2026-08-18 05:00:07"  # instante exacto de la vigencia publicada

# timeout: contabilidad escribe desde su propio contenedor; sin esto un lock
# concurrente aborta al instante en vez de esperar su turno.
ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument(
    "--revertir", action="store_true", help="deshace el cambio: espejo de 0011_reversa_*.sql"
)
ap.add_argument("--dry-run", action="store_true", help="valida y muestra el plan; no escribe nada")
args = ap.parse_args()

con = sqlite3.connect(DB, timeout=30)
con.row_factory = sqlite3.Row

# El respaldo NO corre en dry-run: un ensayo no debe dejar archivos.
respaldo = None
if not args.dry_run:
    sello = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    respaldo = f"/mnt/data/appdata/accounting/data/sku_costs-backup-{sello}.sql"
    with open(respaldo, "w", encoding="utf-8") as f:
        for linea in con.iterdump():
            if "sku_costs" in linea:
                f.write(linea + "\n")

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
    # --- guardas de IDA: el estado de partida es EXACTAMENTE el medido ------
    for sku, esperado in ((PLA, 325.0), (DOR, 459.29)):
        assert len(filas[sku]) == 1, f"{sku}: {len(filas[sku])} filas, se esperaba 1"
        r = filas[sku][0]
        assert abs(r["cost"] - esperado) < 0.005, f"{sku}: costo {r['cost']}, esperado {esperado}"
        assert r["valid_to"] is None, f"{sku}: la fila no esta abierta"
        assert str(r["valid_from"]).startswith("2026-08-18"), f"{sku}: valid_from {r['valid_from']}"
        assert (r["currency"] or "MXN") == "MXN", f"{sku}: moneda {r['currency']}"

    plan = [
        ("UPDATE sku_costs SET valid_from=? WHERE id=?", (DESDE, filas[PLA][0]["id"])),
        (
            "INSERT INTO sku_costs (sku, cost, currency, valid_from, valid_to, source,"
            " created_at) VALUES (?, 458.0, 'MXN', ?, ?, 'orbit_0011_costo_historico', ?)",
            (DOR, DESDE, CORTE, DESDE),
        ),
    ]
else:
    # --- guardas de VUELTA: el estado es EXACTAMENTE el que dejo la ida -----
    assert len(filas[PLA]) == 1, f"{PLA}: {len(filas[PLA])} filas, se esperaba 1"
    assert str(filas[PLA][0]["valid_from"]).startswith("2026-02-20"), "plata: la ida no se aplico"
    historicas = [r for r in filas[DOR] if r["source"] == "orbit_0011_costo_historico"]
    assert len(historicas) == 1, f"{DOR}: {len(historicas)} filas de 0011, se esperaba 1"
    assert len(filas[DOR]) == 2, f"{DOR}: {len(filas[DOR])} filas, se esperaban 2"

    plan = [
        ("UPDATE sku_costs SET valid_from=? WHERE id=?", (CORTE, filas[PLA][0]["id"])),
        ("DELETE FROM sku_costs WHERE id=?", (historicas[0]["id"],)),
    ]

if args.dry_run:
    print(f"DRY-RUN ({'reversa' if args.revertir else 'ida'}): no se escribio nada. Plan:")
    for sql, par in plan:
        print("  ", " ".join(sql.split())[:90], "|", par)
    raise SystemExit(0)

# Transaccion unica: o entran las dos escrituras o no entra ninguna.
with con:
    for sql, par in plan:
        con.execute(sql, par)

despues = con.execute("SELECT count(*) FROM sku_costs").fetchone()[0]
print(f"respaldo: {respaldo} | filas {antes} -> {despues}")
for sku in (PLA, DOR):
    for r in con.execute(
        "SELECT sku, cost, currency, valid_from, valid_to, source FROM sku_costs"
        " WHERE sku=? ORDER BY valid_from",
        (sku,),
    ):
        print(" | ".join("" if x is None else str(x) for x in r))
