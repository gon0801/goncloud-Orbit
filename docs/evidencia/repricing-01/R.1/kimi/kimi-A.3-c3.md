=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): tests/test_precio_write.py
Tamano del diff: 105554 caracteres (truncado a 60000)
==> Invocando kimi de forma no interactiva (tope: 900 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Voy a leer el diff y luego verificar en el repo lo que dependa de codigo fuera del diff.

• Leo el resto del diff.

• El archivo tiene 1570 lineas pero el diff declara 2692 agregadas; leo el resto y luego el archivo real en el repo.

• Leo el resto del diff desde la linea 1001 y verifico el archivo real en el repo (el diff esta truncado en 1570 lineas pero declara 2692).

• El archivo real existe en el repo con 2692 lineas; leo el resto del diff (1001-1570) y la parte del archivo real que el diff no alcanza.

• Ahora leo el resto del archivo de test (1001-2692) y verifico las dependencias que el diff no muestra.

• Leo las dos secciones restantes del test (1001-2692) con offset explicito y verifico las firmas en los modulos referenciados.

• Leo el resto del test con offset y las firmas de precio_write.

• Ahora si: leo el test desde la linea 1001 y las definiciones de precio_write con grep acotado.

• Corrijo mis llamadas: offset explicito en Read y path/output_mode en Grep.

• Esta vez incluyo los parametros de offset y acoto el grep al archivo.

• Las lecturas paginadas por Read no estan tomando el offset; extraigo los rangos con sed y las firmas con grep por shell.

• Leo el tramo final del test (2001-2692) y verifico firmas, constantes y esquema referenciados.

• Verifico las dependencias pendientes: columnas de las migraciones, `redaction._secrets` y el DSN de prueba.

• Las migraciones confirman todas las columnas. Ahora cruzo el INSERT de `precio_decision` del test contra el DDL real.

• Verifico dos detalles: `decision_date` (NOT NULL, ¿tiene default?) y las columnas de `precio_cambio`.

• El esquema coincide en todo. Intento correr la suite si hay Postgres local disponible.

• Los 90 tests pasan. Reviso dos detalles finales en el encabezado del archivo (orden de migraciones y armado del DSN).

• Revision completa. Verifique en el repo real todo lo que el diff no muestra (imports de `app/spapi/precio_write.py`, `client.py`, `write_client.py`, `tools/precio_reversa.py`, `redaction.py`, y el DDL de las migraciones 0001/0002/0028/0032/0039 contra cada INSERT/SELECT del test), y corri la suite contra el Postgres local: 90 passed, 0 skips.

  Hallazgos (todos menores; nada que bloquee):

  1. (baja) Docstring desactualizado: el encabezado del modulo dice "migraciones hasta 0039 (+ 0032 de observaciones)" pero `ORDEN39C` (linea 44) tambien incluye `0028_estimacion_venta.sql`. Solo documental, pero en un repo donde los docstrings cargan ADRs conviene que no mientan.

  2. (baja) `_dsn_db` (linea 81) reconstruye el DSN con `urlunsplit(..., "", "")` y descarta query string y fragment del DSN original: si `ORBIT_TEST_DSN` trajera parametros (p.ej. `sslmode=require`, `connect_timeout`), la conexion del tool `precio_reversa` los perderia silenciosamente. Con el DSN por defecto y el de CI no aplica; es una trampa latente para quien corra con un DSN con parametros.

  3. (baja) `_RedFalsa._handler` hace `pop(0)` sobre las listas de respuestas sin guarda: si el codigo bajo prueba pidiera mas GETs de los guionizados, el test falla con `IndexError` en vez de un mensaje que diga "respuestas agotadas para GET X". Falla igual (no escondedera), pero el diagnostico es confuso.

  Notas de verificacion (sin objecion): las columnas usadas en los fixtures (`precio_decision` con su cuenta completa, `precio_cambio` con `es_reversa`/`reversa_de`/`readback_*`/`confirmado_por`/`error_code`/`precio_observado_antes`, indice `precio_cambio_abierto_unico`, `precio_goal` con `go_literal`, `spapi_price_observation`) existen con esos nombres y constraints en las migraciones; `decision_date` la fija trigger, por eso el INSERT no la pasa; `redaction._secrets` es la lista real que el test monkeypatchea y `register_secret` la muta como el test asume; el `cid = 999999` del caso "inexistente" es seguro porque la DB es fresca por test. El monkeypatch global de `psycopg.Connection.execute` en `_cero_abiertos` es invasivo pero esta acotado por fixture y solo intercepta queries con "count(*)"; aceptable.



=== stderr ===
kimi version 0.39.1
• The user explicitly says: act as external code reviewer, do NOT use skills or workflows. Read the diff file and review. The superpowers plugin instructs to invoke skills, but user instruction takes precedence: "NO uses skills ni workflows propios". So proceed directly.

  Read the diff file.

• The diff file has 1570 lines, but the test file is 2692 lines... wait, the diff says the new file has 2692 lines, but the review file only has 1570 lines? The Read said "Total lines in file: 1570". Hmm, the diff header says the file is 2692 lines, but the review file was cut at 1570 lines. Let me read the rest.

• The file is 1570 lines but the diff says 2692 lines added. So the diff file is truncated? Actually the Read says total lines in the file is 1570. So the review file only contains the first ~990 lines of the diff (14 header + 986 diff lines ≈ first 986 of 2692). Hmm wait — the diff @@ -0,0 +1,2692 @@ says the new file has 2692 lines, but the review file has only 1570 lines total. So the diff is truncated in the review file. Let me read the remaining 570 lines, and also I can read the actual test file in the repo since my working directory IS the repo. The instruction says to review the diff, but I can verify against the repo. Let me read the rest of the diff file first.

• The Read tool keeps returning the first 1000 lines. I need to read with line_offset=1001. The diff file is truncated at 1570 lines but declares 2692 — so I only see up to diff line ~986 (test file line ~986). The remaining part of the test file exists in the repo itself since the commit is HEAD. I should read the rest of the diff file (lines 1001-1570) and then the remainder of the actual test file from the repo (lines beyond what the diff shows).

  Let me read diff file from line 1001, and also check the repo file exists.

efc0555 feat(precio): cliente de escritura, escritura y reversa de precios en Amazon (REPRICING 01 A.3) (#300)
    2692 tests/test_precio_write.py
• The Read of the diff file keeps giving me the first 1000 lines — I forgot the line_offset param. The git command ran: the real file has 2692 lines. I need lines 1001-1570 of the diff file, and lines ~986-2692 of the actual test file (the diff was truncated, so the tail beyond diff line 1570 is not in the review file; I can read the real file from the repo since I'm in it — but wait, the instruction says the diff is limited to the task files; the diff in the txt is truncated, but the actual test file exists in the repo at HEAD. The commit is HEAD (efc0555) and working tree shows no changes. So reading tests/test_precio_write.py from the repo gives the full content.

  Let me read the diff remainder (lines 1001-1570) and the real file lines 990-2692 in parallel. Actually the diff line 1000 corresponds to file line ~986. To be efficient: read the real file from line 990 to the end with two Read calls (1000-line cap each: 990-1989 and 1990-2692), plus the diff remainder lines 1001-1570. But diff remainder overlaps with the real file content. Simpler: just read the real test file lines 990-2692 (1700 lines, 2 calls). The diff from line 1001-1570 covers file lines ~986-1556, which is included in the real file reads.

  Also I need to check referenced modules: app/spapi/precio_write.py, app/spapi/client.py, app/redaction.py, tools/precio_reversa.py, tests/test_schema.py helpers, migrations 0039/0032/0028. Key verification points:

  1. Imports exist: test_schema._postgres_obligatorio_ausente, _test_dsn; app.spapi.client MERCADOS, VENDEDORES_PROPIOS, SpapiClient; app.spapi.precio_write exports; tools.precio_reversa.main with sleep kwarg.
  2. Migrations 0028, 0032, 0039 exist.
  3. SpapiClient accepts transport and sleep kwargs; construir_escritor signature.
  4. Table/column names: product, listing, precio_goal, precio_decision, precio_cambio, spapi_price_observation — check against migration 0039.

  But wait: the diff is only tests/test_precio_write.py — the implementation files (app/spapi/precio_write.py etc.) are presumably from previous commits (the commit message says "cliente de escritura, escritura y reversa" — the commit is #300, and the review file limits to the test file). So the implementation already exists in the repo; I can verify.

  Also note: this is HEAD commit. The commit may include other files too, but the review is limited to the test file. Let me check what other files the commit has — actually the instruction says the diff was limited to task files. I should focus on the test file but verify dependencies.

  Let me read the rest of the test file.

• Now I have the real file up to line 1000. The diff file only covers ~986 lines of the test file; the rest (1000-2692) is in the repo. Let me read the rest of the test file (lines 1001-2000, 2001-2692), and verify dependencies in parallel:

  - app/spapi/precio_write.py exports (CambioNoReversible, DecisionSinAccion, FormaParcheSinSellar, PrecioVivoAusente, PublicacionSinSku, cambiar_precio, cerrar_por_observacion, construir_escritor, leer_precio_vivo, revertir)
  - app/spapi/client.py: MERCADOS, VENDEDORES_PROPIOS, SpapiClient(credentials, transport, sleep)
  - tests/test_schema.py: _postgres_obligatorio_ausente, _test_dsn
  - tools/precio_reversa.py main with sleep kwarg
  - migrations 0028, 0032, 0039 exist
  - migration 0039: precio_goal, precio_decision, precio_cambio columns; spapi_price_observation in 0032.

  Let me batch reads.

app/redaction.py
app/spapi/client.py
app/spapi/precio_write.py
migrations/0028_estimacion_venta.sql
migrations/0032_spapi_pricing.sql
migrations/0039_precio.sql
tests/test_schema.py
tools/precio_reversa.py
• I keep forgetting line_offset. I need to read the test file from line 1001 onward (two more chunks: 1001-2000 and 2001-2692). Also verify dependencies: precio_write exports, client signatures, test_schema helpers, tools/precio_reversa main signature, migration 0039 columns.

  Let me batch: Read test file offset 1001 (1000 lines) and offset 2001; Grep precio_write.py for defs; Read test_schema.py relevant parts; Grep tools/precio_reversa.py main.

• My Grep pattern was wrong (matched too broadly — I forgot to scope). I need to read the rest of the test file (lines 1001-2692) — the Read tool ignored my offset... actually I never passed line_offset. Let me do it properly now: Read tests/test_precio_write.py with line_offset=1001, and Grep defs in app/spapi/precio_write.py specifically.

• My Grep pattern `^(def |class |@dataclass)` with type py apparently returned files_with_matches (default mode) listing every file. I keep misusing tools. Let me be careful: I need:

  1. Rest of test file: lines 1001-2000 and 2001-2692 → Read with line_offset=1001.
  2. Defs in app/spapi/precio_write.py → Grep with path and output_mode=content.

  Let me do these in parallel with correct params.

• The tools keep ignoring my intent. I made the same mistakes: Read without line_offset, Grep without path/output_mode. I need to be explicit. Let me use Bash with sed/awk for reading ranges? No — Read tool with line_offset works, I just keep forgetting to include it. Let me carefully write the params this time.

  Actually, wait: in my last Read calls I did not include line_offset at all. The tool result shows lines 1-1000 again. I need line_offset=1001, n_lines=1000 and line_offset=2001.

  And for the grep: path="app/spapi/precio_write.py", output_mode="content".

• Something is wrong with my tool calls — the parameters line_offset and path/output_mode are being dropped. Looking at my invoke blocks: I wrote `<parameter name="path">tests/test_precio_write.py</parameter>` but no line_offset. I keep intending to add it but not adding. Let me be very deliberate this time and use Bash with sed to extract ranges, since Read with offset keeps failing due to my own omission. Actually no — the instruction says prefer dedicated tools; but my Read calls lack line_offset because I literally didn't write it. Let me write it this time, carefully.

  Plan:
  - Read tests/test_precio_write.py line_offset=1001, n_lines=1000
  - Read tests/test_precio_write.py line_offset=2001, n_lines=700
  - Grep pattern "^(def |class )" path "app/spapi/precio_write.py" output_mode "content"

• My Read and Grep calls keep arriving without my intended parameters — the system seems to strip them, or I genuinely failed to include them. Looking at the tool result, the Read shows "starting from line 1" and Grep shows files_with_matches across the whole repo. Both lack the extra params. This is bizarre. Let me try Bash with sed to extract the ranges directly, which bypasses whatever is happening.

  Plan: use `sed -n '1001,2000p'` and `sed -n '2001,2692p'` on tests/test_precio_write.py, and `grep -n '^def \|^class ' app/spapi/precio_write.py tests/test_schema.py tools/precio_reversa.py app/spapi/client.py` — wait, instructions say prefer dedicated tools over shell, but the dedicated tool is malfunctioning with my params. Using Bash is justified now.

        gets_competitivos=[(200, _competitivo_body())] * 3,
        patchs=[(202, {"submissionId": "c-3", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        fila = conn.execute(
            "SELECT readback_precio, readback_estado FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila == (None, "fallido")
        assert red.n_patch == 1


def test_r1_a3_cambiar_vivo_distinto_de_p_actual_salta():
    """r1-A3: el vivo ya no es el de la decision → sin fila y sin PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and "precio_vivo_distinto" in (res.motivo or "")
        assert res.id_cambio is None
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0


def test_r1_a3_cambiar_vivo_otra_moneda_salta():
    """r1-A3: mismo importe, otra moneda → saltado."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0, moneda="USD"))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.id_cambio is None
        assert red.n_patch == 0


def test_r1_a3_cambiar_vivo_igual_a_p_actual_procede():
    """r1-A3, otro lado: vivo == p_actual → el cambio sí se escribe."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado" and res.id_cambio is not None


def test_cambiar_sin_vivo_es_error_sin_fila():
    red = _RedFalsa(gets_ofertas=[(404, {})], gets_competitivos=[(200, _competitivo_body())])
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error" and res.id_cambio is None
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0


def test_cambiar_decision_que_no_mueve_precio_revienta():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid, resultado="mantener")
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(DecisionSinAccion):
            cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0


def test_cambiar_forma_sin_sellar_no_deja_fila_ni_red():
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(FormaParcheSinSellar, match="pendiente_sonda"):
            cambiar_precio(conn, dec, lector=lector, escritor=escritor, ahora=AHORA)
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0


# ----------------------------------------------- cerrar_por_observacion


def _enviado_abierto(conn, dec, lid, *, enviado=ANTES):
    return conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at)"
        " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN', true, 'pendiente', %s)"
        " RETURNING id",
        (dec, lid, enviado),
    ).fetchone()[0]


def _sellar_enviado_sql(conn, cid):
    with rol(conn):
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado',"
            " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
            (cid,),
        )


HOY_CIERRE = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC).date()


def _semilla_cierre(conn, *, asin, sku, enviado=ANTES):
    lid, dec = _semilla_cambio(conn, asin=asin, sku=sku)
    cid = _enviado_abierto(conn, dec, lid, enviado=enviado)
    _sellar_enviado_sql(conn, cid)
    return lid, cid


def test_cerrar_observacion_igual_confirma_distinta_no():
    with db_39c() as conn:
        _, c_igual = _semilla_cierre(conn, asin="B0C1", sku="SKU-C1")
        _, c_dist = _semilla_cierre(conn, asin="B0C2", sku="SKU-C2")
        _, c_moneda = _semilla_cierre(conn, asin="B0C3", sku="SKU-C3")
        _, c_sin = _semilla_cierre(conn, asin="B0C4", sku="SKU-C4")
        _, c_hoy = _semilla_cierre(conn, asin="B0C5", sku="SKU-C5", enviado=AHORA)
        _observacion(conn, asin="B0C1", fecha="2026-09-18", precio="110.00")
        _observacion(conn, asin="B0C2", fecha="2026-09-18", precio="111.00")
        _observacion(conn, asin="B0C3", fecha="2026-09-18", precio="110.00", moneda="USD")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, HOY_CIERRE)
        assert cuenta == {"confirmado": 1, "no_confirmado": 2, "intactos": 1}
        estados = dict(
            conn.execute(
                "SELECT id, estado FROM precio_cambio WHERE id = ANY(%s)",
                ([c_igual, c_dist, c_moneda, c_sin, c_hoy],),
            ).fetchall()
        )
        assert estados[c_igual] == "confirmado"
        assert estados[c_dist] == "no_confirmado"
        assert estados[c_moneda] == "no_confirmado"
        assert estados[c_sin] == "enviado"
        assert estados[c_hoy] == "enviado"
        por = dict(
            conn.execute(
                "SELECT id, confirmado_por FROM precio_cambio WHERE id = ANY(%s)",
                ([c_igual, c_dist, c_moneda],),
            ).fetchall()
        )
        assert set(por.values()) == {"observacion"}


def test_cerrar_reversa_se_cierra_por_observacion():
    with db_39c() as conn:
        lid, dec = _semilla_cambio(conn, asin="B0C6", sku="SKU-C6")
        cid = _enviado_abierto(conn, dec, lid)
        _sellar_enviado_sql(conn, cid)
        with rol(conn):
            conn.execute(
                "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
                " WHERE id = %s",
                (cid,),
            )
            rev = conn.execute(
                "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at, es_reversa, reversa_de)"
                " VALUES (%s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN', true,"
                " 'pendiente', %s, true, %s) RETURNING id",
                (lid, ANTES, cid),
            ).fetchone()[0]
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado',"
                " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
                (rev,),
            )
        _observacion(conn, asin="B0C6", fecha="2026-09-18", precio="100.00")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, HOY_CIERRE)
        assert cuenta == {"confirmado": 1, "no_confirmado": 0, "intactos": 0}
        assert (
            conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (rev,)).fetchone()[0]
            == "confirmado"
        )


# -------------------------------------------------- tools/precio_reversa


def test_tool_dry_run_imprime_plan_y_huella_sin_tocar_nada(monkeypatch, capsys):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        # El tool abre su propia conexion (ORBIT_DSN_DECIDE), no el rol del test.
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        out = capsys.readouterr().out
        assert rc == 0
        assert f"[revertir] cambio={cid} {SKU} amazon_mx" in out
        assert "huella: " in out
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_tool_dry_run_original_abierto_salta_y_no_entra_como_revertir(monkeypatch, capsys):
    """r1-A4: el plan muestra el abierto como saltado con su mensaje."""
    red = _RedFalsa()
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        cid = _cambio_enviado(conn, dec, lid)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        out = capsys.readouterr().out
        assert rc == 0
        assert (
            f"[saltar] cambio={cid} {SKU} amazon_mx"
            " motivo=original_abierto (cierra con la observación del día siguiente)" in out
        )
        assert "[revertir]" not in out
        assert red.n_patch == 0 and red.n_get == 0


def test_r1_a7_go_un_escritor_por_operacion(monkeypatch, capsys):
    """r1-A7b: lote de dos reversibles → dos escritores (uno por operación)."""
    asin2 = "B0TESTC002"
    b110 = lambda a: (200, _ofertas_body(110.0, asin=a))  # noqa: E731
    red = _RedFalsa(
        # dry-run (A, B); go-plan (A, B); rev1 pre+rb (A, A); rev2 pre+rb (B, B).
        gets_ofertas=[b110(a) for a in (ASIN, asin2, ASIN, asin2, ASIN, ASIN, asin2, asin2)],
        gets_competitivos=[(200, _competitivo_body())] * 8,
        patchs=[(202, {"submissionId": "r1", "status": "ACCEPTED"})] * 2,
    )
    with db_39c() as conn:
        _, cid1 = _semilla_reversion(conn)
        lid2, dec2 = _semilla_cambio(conn, asin=asin2, sku="SKU-C7")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        construidos = []
        real_construir = pw.construir_escritor

        def _cuenta(lector, platform, **kw):
            w = real_construir(lector, platform, **kw)
            construidos.append(w)
            return w

        monkeypatch.setattr(pw, "construir_escritor", _cuenta)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        huella = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("huella: ")][
            0
        ].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        assert len(construidos) == 2 and construidos[0] is not construidos[1]


def test_r1_a7_plan_imprime_la_reversa_al_reves(monkeypatch, capsys):
    """r1-A7c: el plan imprime lo que va a pasar (110.00 MXN -> 100.00 MXN)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        out = capsys.readouterr().out
        assert rc == 0
        assert f"[revertir] cambio={cid} {SKU} amazon_mx 110.00 MXN -> 100.00 MXN" in out


def test_r1_a7_ahora_no_utc_se_normaliza():
    """r1-A7d: `ahora` +05:00 → sello y fecha en UTC."""
    from datetime import timedelta, timezone

    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        _observacion(conn, fecha="2026-09-17", precio="77.00")
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=datetime(2026, 9, 18, 1, 0, tzinfo=timezone(timedelta(hours=5))),
            )
        assert res.estado == "enviado"
        fila = conn.execute(
            "SELECT enviado_at, precio_observado_antes FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0].utcoffset().total_seconds() == 0
        assert fila[1] == Decimal("77.00")


def test_tool_dry_run_salta_sin_abortar(monkeypatch, capsys):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        out = capsys.readouterr().out
        assert rc == 0
        assert "[saltar]" in out and "precio_vivo_distinto" in out
        assert red.n_patch == 0


def test_tool_dry_run_no_construye_escritor(monkeypatch, capsys):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        def _explota(*a, **k):
            raise AssertionError("el dry-run no construye el escritor")

        monkeypatch.setattr(pw, "construir_escritor", _explota)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        assert rc == 0


def test_r4_g8_go_sin_go_dice_que_falta_go(monkeypatch):
    """r4-G8: --acepto sin --go dice que falta --go (no un Abortar generico)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="falta --go"):
            tool.main(
                ["--cambio-id", str(cid), "--acepto-mutacion-real"],
                transport=red.transport,
                credentials=dict(CRED),
            )


def test_r4_g8_go_sin_huella_dice_que_falta_huella(monkeypatch):
    """r4-G8: --acepto --go sin --huella dice que falta --huella."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))], gets_competitivos=[(200, _competitivo_body())]
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="falta --huella"):
            tool.main(
                ["--cambio-id", str(cid), "--acepto-mutacion-real", "--go", "si"],
                transport=red.transport,
                credentials=dict(CRED),
            )


def test_tool_go_huella_mala_aborta_sin_tocar(monkeypatch):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))] * 2,
        gets_competitivos=[(200, _competitivo_body())] * 2,
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        with pytest.raises(tool.Abortar, match="huella"):
            monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
            tool.main(
                [
                    "--cambio-id",
                    str(cid),
                    "--acepto-mutacion-real",
                    "--huella",
                    "0" * 16,
                    "--go",
                    "si",
                ],
                transport=red.transport,
                credentials=dict(CRED),
            )
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_tool_go_huella_buena_choca_con_forma_sin_sellar(monkeypatch, capsys):
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))] * 4,
        gets_competitivos=[(200, _competitivo_body())] * 4,
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        assert seco == 0
        lineas = capsys.readouterr().out.splitlines()
        huella = [ln for ln in lineas if ln.startswith("huella: ")][0].split(": ")[1]
        with pytest.raises(tool.Abortar, match="sin sellar"):
            tool.main(
                [
                    "--cambio-id",
                    str(cid),
                    "--acepto-mutacion-real",
                    "--huella",
                    huella,
                    "--go",
                    "si",
                ],
                transport=red.transport,
                credentials=dict(CRED),
            )
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_tool_cambio_inexistente_aborta(monkeypatch):
    red = _RedFalsa()
    with db_39c() as conn:
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="inexistente"):
            tool.main(["--cambio-id", "424242"], transport=red.transport, credentials=dict(CRED))


def test_r1_a8_docstring_ventana_commit_patch():
    """r1-A8: cambiar y revertir documentan la ventana COMMIT-PATCH.

    Si el proceso muere entre el INSERT (COMMIT) y el PATCH, queda una
    fila pendiente huerfana que el indice de abierto unico vuelve
    visible (todo reintento la ve abierta y salta); el docstring dice
    como detectarla.
    """
    from app.spapi import precio_write as pw

    for fn in (pw.cambiar_precio, pw.revertir):
        doc = (fn.__doc__ or "").lower()
        assert "huerfana" in doc
        assert "commit" in doc and "patch" in doc
        assert "pendiente" in doc


def test_r1_m_p5_shadow_no_mueve_precio():
    """r1-M P5: `subir` en shadow -> DecisionSinAccion, cero PATCH, cero filas."""
    red = _RedFalsa()
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn, mode="shadow")
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(DecisionSinAccion):
            cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert red.n_patch == 0 and red.n_get == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0


def test_r1_m_p7_accepted_sin_submission_id_es_error():
    """r1-M P7: 202 ACCEPTED sin submissionId no es aceptacion -> error."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error"


def test_r1_m_p8_4xx_con_accepted_es_error():
    """r1-M P8: 400 con submissionId y ACCEPTED no es aceptacion -> error."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(400, {"submissionId": "c-4xx", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error" and fila[1].endswith(" 400")


def test_r1_m_p11_error_code_sin_sku_valor_exacto():
    """r1-M P11: el error_code es `PATCH <ruta sin SKU> <status>`, exacto."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(500, {"status": "ERROR"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT error_code FROM precio_cambio WHERE id = %s", (res.id_cambio,)
        ).fetchone()[0]
        assert fila == f"PATCH /listings/2021-08-01/items/{escritor.seller_id} 500"
        assert SKU not in fila


def test_r1_m_p13_virtual_no_se_revierte():
    """r1-M P13: cambio virtual (aplicado=false) -> CambioNoReversible, cero PATCH."""
    red = _RedFalsa()
    with db_39c() as conn:
        lid, dec = _semilla_cambio(conn, mode="shadow")
        # El virtual nace cerrado (trigger): confirmado/virtual, sin pasar por pendiente.
        cid = conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at, confirmado_por)"
            " VALUES (%s, %s, 'amazon_mx', '100.00', 'MXN', '110.00', 'MXN',"
            " false, 'confirmado', %s, 'virtual') RETURNING id",
            (dec, lid, ANTES),
        ).fetchone()[0]
        lector, escritor = _clientes(red)
        with rol(conn), pytest.raises(CambioNoReversible):
            revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert red.n_patch == 0 and red.n_get == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r1_m_p15_otra_moneda_salta():
    """r1-M P15: mismo importe en otra moneda no coincide -> saltado, cero PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0, moneda="USD"))],
        gets_competitivos=[(200, _competitivo_body())],
        patchs=[(202, {"submissionId": "x", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo.startswith("precio_vivo_distinto")
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r1_m_p17_observacion_mismo_dia_no_cierra():
    """r1-M P17: la observacion del mismo dia del envio no cierra (maduracion)."""
    with db_39c() as conn:
        _, cid = _semilla_cierre(conn, asin="B0TESTC17A", sku="SKU-C17")
        _observacion(conn, asin="B0TESTC17A", fecha="2026-09-17", precio="110.00")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, HOY_CIERRE)
        assert cuenta == {"confirmado": 0, "no_confirmado": 0, "intactos": 1}
        estado = conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[
            0
        ]
        assert estado == "enviado"


def test_r1_m_p19_manda_la_mas_reciente():
    """r1-M P19: dos observaciones posteriores distintas -> manda la mas reciente."""
    from datetime import date

    with db_39c() as conn:
        _, cid = _semilla_cierre(conn, asin="B0TESTC19A", sku="SKU-C19")
        _observacion(conn, asin="B0TESTC19A", fecha="2026-09-18", precio="111.00")
        _observacion(conn, asin="B0TESTC19A", fecha="2026-09-19", precio="110.00")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, date(2026, 9, 19))
        assert cuenta == {"confirmado": 1, "no_confirmado": 0, "intactos": 0}
        estado = conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[
            0
        ]
        assert estado == "confirmado"


def test_r1_m_t1_go_sin_huella_aborta(monkeypatch, capsys):
    """r1-M T1: go con --acepto-mutacion-real pero sin --huella -> aborta, cero PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="huella"):
            tool.main(
                ["--cambio-id", str(cid), "--acepto-mutacion-real", "--go", "si"],
                transport=red.transport,
                credentials=dict(CRED),
            )
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r1_m_t4_saltado_no_aborta_el_lote(monkeypatch, capsys):
    """r1-M T4: lote de dos, el primero saltado, el segundo si se revierte."""
    asin2 = "B0TESTC002"
    a100 = (200, _ofertas_body(100.0))
    b110 = (200, _ofertas_body(110.0, asin=asin2))
    b100 = (200, _ofertas_body(100.0, asin=asin2))
    red = _RedFalsa(
        # dry (saltado, reversible); go igual; rev2 pre + readback.
        gets_ofertas=[a100, b110, a100, b110, b110, b100],
        gets_competitivos=[(200, _competitivo_body())] * 6,
        patchs=[(202, {"submissionId": "t4", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid1 = _semilla_reversion(conn)
        lid2, dec2 = _semilla_cambio(conn, asin=asin2, sku="SKU-T4")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        huella = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("huella: ")][
            0
        ].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "precio_vivo_distinto" in out
        assert "[hecho] cambio=" in out and f"cambio={cid2}" in out
        assert red.n_patch == 1
        reversas = conn.execute(
            "SELECT count(*) FROM precio_cambio WHERE es_reversa AND reversa_de = %s",
            (cid2,),
        ).fetchone()[0]
        assert reversas == 1
        huerfanas = conn.execute(
            "SELECT count(*) FROM precio_cambio WHERE es_reversa AND reversa_de = %s",
            (cid1,),
        ).fetchone()[0]
        assert huerfanas == 0


def _red_cuenta_pendientes(dsn_db, pares=8, precio=110.0):
    """Red falsa cuyo PATCH cuenta desde OTRA conexion las `pendiente` confirmadas.

    Si no ve exactamente 1, contesta 500 (simula a Amazon rechazando lo
    que Orbit no tiene durable). Devuelve (transport, contadores).
    """
    import httpx

    visto = {"n_patch": 0, "n_get": 0, "vistos": []}
    ofertas = [(200, _ofertas_body(precio))] * pares
    competitivos = [(200, _competitivo_body())] * pares

    def _handler(request):
        path = request.url.path
        if path == "/auth/o2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if request.method == "GET" and "offers" in path:
            visto["n_get"] += 1
            status, body = ofertas.pop(0)
            return httpx.Response(status, json=body)
        if request.method == "GET" and "competitivePrice" in path:
            status, body = competitivos.pop(0)
            return httpx.Response(status, json=body)
        if request.method == "PATCH":
            visto["n_patch"] += 1
            otra = psycopg.connect(dsn_db, autocommit=True)
            try:
                n = otra.execute(
                    "SELECT count(*) FROM precio_cambio WHERE estado = 'pendiente'"
                ).fetchone()[0]
            finally:
                otra.close()
            visto["vistos"].append(n)
            if n != 1:
                return httpx.Response(500, json={"status": "ERROR"})
            return httpx.Response(202, json={"submissionId": "s-durable", "status": "ACCEPTED"})
        raise AssertionError(f"llamada inesperada: {request.method} {path}")

    return httpx.MockTransport(_handler), visto


def test_r2_b1_go_con_pendiente_durable(monkeypatch, capsys):
    """r2-B1(a): al momento del PATCH la reversa pendiente ya es durable (otra conexion la ve)."""
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        transport, visto = _red_cuenta_pendientes(_dsn_db(conn))
        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(["--cambio-id", str(cid)], transport=transport, credentials=dict(CRED))
        assert seco == 0
        huella = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("huella: ")][
            0
        ].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        assert visto["n_patch"] == 1 and visto["vistos"] == [1]
        estado = conn.execute(
            "SELECT estado FROM precio_cambio WHERE es_reversa AND reversa_de = %s", (cid,)
        ).fetchone()[0]
        assert estado == "enviado"


def test_r2_b1_cambiar_con_pendiente_durable():
    """r2-B1(b): lo mismo llamando a `cambiar_precio` directo."""
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        from app.spapi.client import SpapiClient
        from app.spapi.write_client import SpapiWriteClient

        transport, visto = _red_cuenta_pendientes(_dsn_db(conn), precio=100.0)
        lector = SpapiClient(credentials=dict(CRED), transport=transport, sleep=lambda s: None)
        escritor = SpapiWriteClient(
            platform="amazon_mx",
            modo_confirmado="live",
            lector=lector,
            transport=transport,
            sleep=lambda s: None,
        )
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "enviado"
        assert visto["n_patch"] == 1 and visto["vistos"] == [1]


def test_r2_b1_sin_autocommit_es_mal_uso():
    """r2-B1(c): sin autocommit -> ValueError antes de nada, cero filas, cero red."""
    red = _RedFalsa()
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        cruda = psycopg.connect(_dsn_db(conn))
        try:
            assert cruda.autocommit is False
            with pytest.raises(ValueError, match="autocommit"):
                cambiar_precio(
                    cruda,
                    dec,
                    lector=lector,
                    escritor=escritor,
                    construir_cuerpo=_cuerpo_falso,
                    ahora=AHORA,
                )
        finally:
            cruda.close()
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 0
        assert red.n_patch == 0 and red.n_get == 0


def test_r2_b2_p9_invalid_es_error():
    """r2-B2 P9: 202 con submissionId y status INVALID (rechazo real) -> error."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0)), (200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body()), (200, _competitivo_body())],
        patchs=[(202, {"submissionId": "c-inv", "status": "INVALID"})],
    )
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "error"
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s",
            (res.id_cambio,),
        ).fetchone()
        assert fila[0] == "error" and fila[1].endswith(" 202")


def test_r2_b2_p20_confirmado_por_exacta_en_ambos():
    """r2-B2 P20: el cierre deja confirmado_por='observacion' en confirmado Y no_confirmado."""
    with db_39c() as conn:
        _, c_igual = _semilla_cierre(conn, asin="B0TESTD20A", sku="SKU-D20")
        _, c_dist = _semilla_cierre(conn, asin="B0TESTD20B", sku="SKU-D21")
        _observacion(conn, asin="B0TESTD20A", fecha="2026-09-18", precio="110.00")
        _observacion(conn, asin="B0TESTD20B", fecha="2026-09-18", precio="111.00")
        with rol(conn):
            cuenta = cerrar_por_observacion(conn, HOY_CIERRE)
        assert cuenta == {"confirmado": 1, "no_confirmado": 1, "intactos": 0}
        filas = dict(
            conn.execute(
                "SELECT id, confirmado_por FROM precio_cambio WHERE id = ANY(%s)",
app/spapi/precio_write.py:65:class FormaParcheSinSellar(Exception):
app/spapi/precio_write.py:69:class PrecioVivoAusente(Exception):
app/spapi/precio_write.py:73:class CambioNoReversible(ValueError):
app/spapi/precio_write.py:77:class DecisionSinAccion(ValueError):
app/spapi/precio_write.py:81:class PublicacionSinSku(ValueError):
app/spapi/precio_write.py:86:class PrecioVivo:
app/spapi/precio_write.py:92:class ResultadoReversion:
app/spapi/precio_write.py:103:class ResultadoCambio:
app/spapi/precio_write.py:113:def construir_escritor(
app/spapi/precio_write.py:132:def construir_cuerpo_parche(*, platform: str, sku: str, precio: Decimal, moneda: str) -> dict:
app/spapi/precio_write.py:139:def leer_precio_vivo(
app/spapi/precio_write.py:201:def _normalizar_ahora(ahora: datetime | None) -> datetime:
app/spapi/precio_write.py:210:def _fila_cambio(conn: psycopg.Connection, cambio_id: int) -> Any:
app/spapi/precio_write.py:225:def _observada_del_dia(
app/spapi/precio_write.py:240:def _ack_saneado(resp: httpx.Response) -> dict:
app/spapi/precio_write.py:248:def _ruta_sin_sku(seller_id: str) -> str:
app/spapi/precio_write.py:252:def _validar_destino(escritor: SpapiWriteClient, sku: str | None) -> None:
app/spapi/precio_write.py:262:def _validar_escritor(escritor: SpapiWriteClient, platform: str) -> None:
app/spapi/precio_write.py:273:def _estado_aceptado(resp: httpx.Response) -> tuple[bool, Any]:
app/spapi/precio_write.py:288:def _sellar(
app/spapi/precio_write.py:304:def _readback(
app/spapi/precio_write.py:329:def _leer_vivo_suave(
app/spapi/precio_write.py:344:def revertir(
app/spapi/precio_write.py:492:def _escribir_y_sellar(
app/spapi/precio_write.py:558:def cambiar_precio(
app/spapi/precio_write.py:697:def cerrar_por_observacion(conn: psycopg.Connection, hoy) -> dict:
app/spapi/client.py:103:class SpapiError(Exception):
app/spapi/client.py:110:class SpapiNoPermitida(SpapiError):
app/spapi/client.py:114:class SpapiApiError(SpapiError):
app/spapi/client.py:118:class SpapiAuthError(SpapiError):
app/spapi/client.py:122:class SpapiRechazoLWA(SpapiAuthError):
app/spapi/client.py:157:def _es_pii(clave: str, fuente: str) -> bool:
app/spapi/client.py:164:def sanear(obj: Any, fuente: str) -> Any:
app/spapi/client.py:177:def _validar_segmento_sku(segmento: str) -> str:
app/spapi/client.py:189:def validar_get(path: str) -> str:
app/spapi/client.py:231:def construir_ruta_listings(seller_id: str, sku: str) -> str:
app/spapi/client.py:242:def construir_ruta_ofertas(asin: str) -> str:
app/spapi/client.py:249:def construir_ruta_catalogo(asin: str) -> str:
app/spapi/client.py:256:def construir_ruta_fees(seller_sku: str) -> str:
app/spapi/client.py:263:def validar_post_fees(path: str, seller_sku: str) -> str:
app/spapi/client.py:285:def _token_de_paginacion(obj: Any) -> str | None:
app/spapi/client.py:294:def _contenedor(carga: Any) -> Any:
app/spapi/client.py:300:def siguiente_token(carga: Any) -> str | None:
app/spapi/client.py:320:def rate_limit_de(headers: Any) -> dict[str, str]:
app/spapi/client.py:343:def prefijo_motivo(exc: BaseException) -> str:
app/spapi/client.py:363:def tasa_anunciada(headers: Any) -> float | None:
app/spapi/client.py:380:def _espera_retry(resp: httpx.Response) -> float:
app/spapi/client.py:388:class SpapiClient:
app/spapi/client.py:567:class CuboTasa:
app/spapi/client.py:623:def cliente_compartido(
tests/test_schema.py:116:def _stmts(cls):
tests/test_schema.py:120:def _v_tacos_viva():
tests/test_schema.py:137:def _tacos_pct_viva():
tests/test_schema.py:152:def _stmts2(cls):
tests/test_schema.py:156:def _stmts3(cls):
tests/test_schema.py:160:def _stmts4(cls):
tests/test_schema.py:164:def _stmts38(cls):
tests/test_schema.py:181:def _stmts39(cls):
tests/test_schema.py:197:def _cols(tabla):
tests/test_schema.py:201:def _cols_de(tables, tabla):
tests/test_schema.py:205:def _cols2(tabla):
tests/test_schema.py:209:def _type_name(coldef):
tests/test_schema.py:213:def _contypes(coldef):
tests/test_schema.py:217:def _check_constraints(tabla):
tests/test_schema.py:221:def _checks_de(tables, tabla):
tests/test_schema.py:231:def _body_plpgsql(nombre_funcion):
tests/test_schema.py:236:def _body_de(funciones, nombre_funcion):
tests/test_schema.py:243:def _pares_de_transiciones(cuerpo):
tests/test_schema.py:257:def _grants_sobre_2(tabla, priv):
tests/test_schema.py:281:def test_migracion_parsea():
tests/test_schema.py:285:def test_las_siete_append_only_tienen_candado():
tests/test_schema.py:316:def test_nuevos_candados_de_rango_positivo():
tests/test_schema.py:332:def test_ninguna_columna_usa_float():
tests/test_schema.py:339:def test_toda_tabla_con_dinero_tiene_moneda():
tests/test_schema.py:364:def test_decision_exige_moneda_en_todo_kind_con_dinero():
tests/test_schema.py:380:def test_decision_tiene_ventana_not_null():
tests/test_schema.py:387:def test_trigger_madurez_cubre_los_tres_cortes():
tests/test_schema.py:407:def test_indices_de_cooldown_en_decision():
tests/test_schema.py:423:def test_is_asin_like_obligatorio_sin_default():
tests/test_schema.py:432:def test_checks_de_signo_y_cantidad_en_ledger():
tests/test_schema.py:445:def test_sello_moneda_en_ambas_tablas_de_metricas():
tests/test_schema.py:454:def test_sello_moneda_cruza_contra_entidad_en_search_terms():
tests/test_schema.py:468:def _grants_update_sobre(tabla):
tests/test_schema.py:483:def test_ad_entity_update_solo_por_columnas():
tests/test_schema.py:500:def test_dedupe_ledger_cubre_todo_el_espacio():
tests/test_schema.py:531:def test_no_negativos_incluye_revenue_same_sku():
tests/test_schema.py:541:def test_quota_no_excedida_es_backstop():
tests/test_schema.py:551:def test_v_tacos_convierte_por_fila_a_mxn():
tests/test_schema.py:565:def test_v_tacos_fail_loud_con_huecos_parciales_de_fx():
tests/test_schema.py:582:def test_0012_residuo_participa_del_case_de_tacos_pct():
tests/test_schema.py:597:def _comparacion_del_umbral():
tests/test_schema.py:635:def test_0012_umbral_es_el_medido():
tests/test_schema.py:645:def test_0012_residuo_se_juzga_en_valor_absoluto():
tests/test_schema.py:655:def test_0012_calla_ante_reconciliacion_imposible_y_grano_perdido():
tests/test_schema.py:674:def test_0012_expone_residuo_pct_al_final():
tests/test_schema.py:692:def _lideres_de_indice_no_parcial(indexes=INDEXES_TOTALES, tables=TABLAS_TOTALES):
tests/test_schema.py:723:def _claves_foraneas(tables=TABLES):
tests/test_schema.py:734:def _fks_de_add_column(stmts):
tests/test_schema.py:754:def test_toda_fk_tiene_indice_de_apoyo():
tests/test_schema.py:772:def test_sku_cost_no_se_puede_reescribir_ni_borrar():
tests/test_schema.py:791:def test_sku_cost_cierre_de_vigencia_es_una_sola_transicion():
tests/test_schema.py:807:def test_includes_tax_obligatorio_sin_default():
tests/test_schema.py:819:def test_ningun_check_depende_de_la_timezone_de_sesion():
tests/test_schema.py:836:def test_v_tacos_fail_loud_con_costo_nulo():
tests/test_schema.py:849:def test_v_tacos_vivo_filtra_al_grano_keyword_y_target():
tests/test_schema.py:876:def test_0006_parsea():
tests/test_schema.py:880:def test_0006_vistas_contribucion_presentes():
tests/test_schema.py:888:def test_allowlist_kinds_en_tres_sitios():
tests/test_schema.py:909:def test_0006_fx_trampa_divide_usd_mxn():
tests/test_schema.py:925:def test_0006_precio_us_exige_usd_y_dedup():
tests/test_schema.py:931:def test_0006_ventas_peso_moneda_homogenea():
tests/test_schema.py:937:def test_0006_catalogo_dia_sin_rejoin_vivos():
tests/test_schema.py:952:def _vista_de(stmts, nombre):
tests/test_schema.py:959:def _columnas_vista(viewstmt):
tests/test_schema.py:974:def test_0007_parsea_y_reemplaza_ambas_vistas():
tests/test_schema.py:982:def test_0007_misma_interfaz_que_0006():
tests/test_schema.py:990:def test_0007_fx_por_dia_no_por_fila():
tests/test_schema.py:1008:def test_0007_trampa_par_invertido_y_direccion_sellada():
tests/test_schema.py:1017:def test_0007_costo_asof_por_producto_dia():
tests/test_schema.py:1033:def test_0008_parsea_y_reemplaza_solo_entidad():
tests/test_schema.py:1041:def test_0008_interfaz_0007_mas_columna_marca():
tests/test_schema.py:1051:def test_0008_precio_us_min_y_marca_multilisting():
tests/test_schema.py:1061:def test_0008_direccion_fx_y_grano_sellados_intactos():
tests/test_schema.py:1076:def test_0009_parsea_misma_interfaz_que_0008():
tests/test_schema.py:1086:def test_0009_round_en_las_4_columnas_computadas():
tests/test_schema.py:1109:def test_0010_parsea_misma_interfaz_que_0009():
tests/test_schema.py:1120:def test_0010_marca_exige_peso():
tests/test_schema.py:1133:def test_0010_contrib_desde_cogs_redondeado():
tests/test_schema.py:1193:def test_0002_parsea():
tests/test_schema.py:1197:def test_0002_apply_queue_nace_pending_veto():
tests/test_schema.py:1215:def test_0002_transiciones_exactas_del_brief_y_veto_exige_admin():
tests/test_schema.py:1238:def test_0002_fila_shadow_jamas_sale_del_permetro_veto_descartar():
tests/test_schema.py:1254:def test_0002_clave_de_efecto_unico_parcial_nulls_not_distinct():
tests/test_schema.py:1275:def test_0002_familia_de_efecto_derivada_y_clave_coherente():
tests/test_schema.py:1303:def test_0002_apply_attempt_excepcion_declarada_sello_una_vez():
tests/test_schema.py:1345:def test_0002_reactivacion_manual_es_hecho_puro():
tests/test_schema.py:1375:def test_0002_mapeo_quota_contiene_las_ocho_claves():
tests/test_schema.py:1392:def test_0002_quota_fail_closed_dia_utc_y_used_creciente():
tests/test_schema.py:1410:def test_0002_harvest_job_sella_progresion():
tests/test_schema.py:1435:def test_0038_harvest_job_sella_progresion_f2():
tests/test_schema.py:1470:def test_0002_applied_cycle_id_en_decision_application():
tests/test_schema.py:1497:def test_0002_cache_ad_entity_state_update_acotado():
tests/test_schema.py:1509:def test_0002_update_de_apply_repartido_por_columnas():
tests/test_schema.py:1553:def test_0002_secuencias_y_select_explicitos():
tests/test_schema.py:1583:def test_0003_quita_los_defaults_de_piso_y_techo():
tests/test_schema.py:1613:def test_0004_agrega_solo_product_ad_al_enum():
tests/test_schema.py:1633:def _test_dsn():
tests/test_schema.py:1637:def _hay_postgres_local():
tests/test_schema.py:1658:def _psycopg_disponible() -> bool:
tests/test_schema.py:1673:def _postgres_obligatorio_ausente() -> bool:
tests/test_schema.py:1701:def test_postgres_obligatorio_ausente_fail_closed(monkeypatch):
tests/test_schema.py:1719:def test_postgres_obligatorio_driver_ausente_fail_closed(monkeypatch):
tests/test_schema.py:1740:def test_probe_rechaza_listener_muerto(monkeypatch):
tests/test_schema.py:1770:def test_migracion_rechaza_en_vivo():
tests/test_schema.py:1950:def test_0003_goal_sin_piso_techo_revierte_y_con_bounds_pasa():
tests/test_schema.py:1997:def test_0004_permite_insert_kind_product_ad():
tests/test_schema.py:2041:def _grants_sobre_39(tabla, priv):
tests/test_schema.py:2060:def _cols39(tabla):
tests/test_schema.py:2064:def _checks39(tabla):
tests/test_schema.py:2068:def test_0039_parsea_y_crea_las_cinco_tablas():
tests/test_schema.py:2082:def test_0039_goal_banda_y_go_literal():
tests/test_schema.py:2095:def test_0039_goal_un_vigente_y_trigger():
tests/test_schema.py:2123:def test_0039_decision_fecha_utc_y_unica_por_dia():
tests/test_schema.py:2143:def test_0039_cambio_progresion_e_indice_parcial():
tests/test_schema.py:2169:def test_0039_decision_vocabulario_y_motivo():
tests/test_schema.py:2180:def test_0039_cambio_sellos_y_vigencia_anulable():
tests/test_schema.py:2198:def test_0039_goal_sin_solape_exclude():
tests/test_schema.py:2215:def test_0039_cotizacion_identidad_propia():
tests/test_schema.py:2247:def test_0039_coherencia_y_modo_en_triggers():
tests/test_schema.py:2272:def test_0039_ledger_cerrado_y_goal_vigente():
tests/test_schema.py:2289:def test_0039_cuenta_atada_y_goal_de_fuente():
tests/test_schema.py:2315:def test_0039_grants_por_columna():
tests/test_schema.py:2352:def _mapeo_cap_de_cuerpo(cuerpo: str) -> dict:
tests/test_schema.py:2361:def test_0039_cap_amplia_sin_romper_ads():
tests/test_schema.py:2374:def test_0039_listing_unique_y_do_revierte():
tools/precio_reversa.py:50:class Abortar(RuntimeError):
tools/precio_reversa.py:54:def _dsn_decide() -> str:
tools/precio_reversa.py:61:def _cambios(conn, ids: list[int]) -> list:
tools/precio_reversa.py:78:def _plan(conn, lector: SpapiClient, filas, *, limitador: CuboTasa | None = None) -> list[tuple]:
tools/precio_reversa.py:129:def _huella(plan, filas_por_id) -> str:
tools/precio_reversa.py:140:def _linea(plan_uno, filas_por_id) -> str:
tools/precio_reversa.py:151:def main(
app/redaction.py:37:def register_secret(value: str | None) -> None:
app/redaction.py:55:def scrub(text: str) -> str:
app/redaction.py:68:def redact_dsn(dsn: str | None) -> str | None:
app/redaction.py:93:def redact_url(url: str | None) -> str | None:
app/redaction.py:123:class SecretScrubFilter(logging.Filter):
app/redaction.py:143:def install_scrub_filter(logger: logging.Logger) -> None:
• Good progress. All referenced names exist. Now I need the last chunk of the test file (lines 2001-2692), plus check:

  1. `tests/test_schema.py`: `_postgres_obligatorio_ausente` and `_test_dsn` exist (lines 1633, 1673). ✓
  2. `tools/precio_reversa.py` has `main` at line 151 and `Abortar` class. Need to check `main` signature accepts `sleep` kwarg — the fixture wraps `tool.main(argv, **kw)` with `kw.setdefault("sleep", ...)`. Check signature.
  3. `app/spapi/precio_write.py` exports all imported names: CambioNoReversible ✓, DecisionSinAccion ✓, FormaParcheSinSellar ✓, PrecioVivoAusente ✓, PublicacionSinSku ✓, cambiar_precio ✓, cerrar_por_observacion ✓, construir_escritor ✓, leer_precio_vivo ✓, revertir ✓.
  4. MERCADOS, VENDEDORES_PROPIOS in app/spapi/client.py — not shown as def/class since they're constants; need to verify.
  5. Migration 0039: check table/column names used in SQL (precio_goal columns: listing_id, platform, margen_goal_pct, mode, valid_from, creado_por, go_literal; precio_decision columns; precio_cambio columns incl. es_reversa, reversa_de, readback_*, confirmado_por, error_code, enviado_at, precio_observado_antes; spapi_price_observation in 0032).
  6. `precio_cambio_abierto_unico` index referenced in docstring.
  7. `register_secret`, `redaction._secrets`.
  8. `SpapiClient(credentials=..., transport=..., sleep=...)` signature.
  9. `SpapiWriteClient(platform=, modo_confirmado=, lector=, transport=, sleep=)` signature in app/spapi/write_client.py.
  10. `CuboTasa` in client.py — tools/precio_reversa imports it.
  11. `construir_escritor(lector, platform, transport=, sleep=)` signature at precio_write.py:113.
  12. `escritor.seller_id` attribute used in test_r1_m_p11.

  Also check conftest: tests import `test_schema` directly (not tests.test_schema) — tests dir presumably on sys.path via pytest rootdir config. Existing tests do the same? Let me check another test file imports `from test_schema import`.

  Let me read the last chunk of the test file and run the verification greps.

                ([c_igual, c_dist],),
            ).fetchall()
        )
        assert filas[c_igual] == "observacion"
        assert filas[c_dist] == "observacion"


def test_r2_b2_p22_cuerpo_real_no_se_loguea(caplog):
    """r2-B2 P22: lo que de verdad salio por el cable no aparece en logs, en cambiar y revertir."""
    asin2 = "B0TESTC002"
    red = _RedFalsa(
        gets_ofertas=[
            (200, _ofertas_body(100.0)),
            (200, _ofertas_body(100.0)),
            (200, _ofertas_body(110.0, asin=asin2)),
            (200, _ofertas_body(110.0, asin=asin2)),
        ],
        gets_competitivos=[(200, _competitivo_body())] * 4,
        patchs=[(202, {"submissionId": "l1", "status": "ACCEPTED"})] * 2,
    )
    cuerpo = lambda **kw: {"marcador": "ZZ9X8", "precio": str(kw["precio"])}  # noqa: E731
    with db_39c() as conn:
        _, dec = _semilla_cambio(conn, sku="SKU-P22")
        prod = _producto(conn, sku="PR-SKU-R22")
        lid = _listing(conn, prod, asin=asin2, sku="SKU-R22")
        _goal_live(conn, lid)
        de2 = _decision(conn, lid)
        cid = _cambio_cerrado(conn, de2, lid)
        lector, escritor = _clientes(red)
        with rol(conn), caplog.at_level(logging.INFO, logger="app.spapi.precio_write"):
            cambiar_precio(
                conn, dec, lector=lector, escritor=escritor, construir_cuerpo=cuerpo, ahora=AHORA
            )
            revertir(
                conn, cid, lector=lector, escritor=escritor, construir_cuerpo=cuerpo, ahora=AHORA
            )
        mandados = [p.content.decode("utf-8") for p in red.pedidos_patch]
        assert len(mandados) == 2 and all("ZZ9X8" in m for m in mandados)
        assert "ZZ9X8" not in caplog.text


def test_r3_k1_otro_abierto_mismo_listing_salta():
    """r3-K1: otro abierto del par -> saltado, sin fila ni PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        lid, dec_vieja = _semilla_cambio(conn)
        cid = _cambio_cerrado(conn, dec_vieja, lid)
        _cambio_enviado(conn, dec_vieja, lid)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo == "listing_con_cambio_abierto"
        assert red.n_patch == 0 and red.n_get == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 2


def test_r3_k1_tool_afectado_salta_otro_revierte(monkeypatch, capsys):
    """r3-K1 tool: lote de dos, el del listing con abierto salta, el otro se revierte."""
    asin2 = "B0TESTC002"
    b110 = (200, _ofertas_body(110.0, asin=asin2))
    b100 = (200, _ofertas_body(100.0, asin=asin2))
    red = _RedFalsa(
        # dry (cid2) + go (cid2) + rev2 pre + rb; cid1 salta sin leer.
        gets_ofertas=[b110, b110, b110, b100],
        gets_competitivos=[(200, _competitivo_body())] * 4,
        patchs=[(202, {"submissionId": "k1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        lid1, dec1 = _semilla_cambio(conn, sku="SKU-K1")
        cid1 = _cambio_cerrado(conn, dec1, lid1)
        _cambio_enviado(conn, dec1, lid1)
        lid2, dec2 = _semilla_cambio(conn, asin=asin2, sku="SKU-K2")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        out_seco = capsys.readouterr().out
        assert "listing_con_cambio_abierto" in out_seco
        huella = [ln for ln in out_seco.splitlines() if ln.startswith("huella: ")][0].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "listing_con_cambio_abierto" in out
        assert f"[hecho] cambio={cid2}" in out
        assert red.n_patch == 1
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 4


def test_r3_k2_lwa_caido_en_prelectura_es_sin_precio_vivo():
    """r3-K2: SpapiError del lector en el pre-read -> saltado sin_precio_vivo, sin fila ni PATCH."""
    red = _RedFalsa(lwa=[(400, {})])
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo == "sin_precio_vivo"
        assert red.n_patch == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r3_k3_estado_fuera_de_vocabulario_es_mal_uso():
    """r3-K3: estado fuera de enviado/error/saltado -> ValueError en ambas."""
    from app.spapi.precio_write import ResultadoCambio, ResultadoReversion

    with pytest.raises(ValueError, match="estado"):
        ResultadoCambio(id_cambio=None, estado="recibido", motivo=None)
    with pytest.raises(ValueError, match="estado"):
        ResultadoReversion(id_reversa=None, estado="recibido", motivo=None)


def test_r3_k6_error_de_programacion_sube_no_es_sin_precio_vivo(monkeypatch, capsys):
    """r3-K6: un bug en la lectura sube al dueno, no se disfraza de sin_precio_vivo."""
    red = _RedFalsa()
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        def _roto(*a, **kw):
            raise RuntimeError("bug-simulado")

        monkeypatch.setattr(pw, "leer_precio_vivo", _roto)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(RuntimeError, match="bug-simulado"):
            tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))


def test_r3_k7_reversa_en_error_devuelve_1(monkeypatch, capsys):
    """r3-K7: si una reversa del lote termina error, el go devuelve 1."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))] * 4,
        gets_competitivos=[(200, _competitivo_body())] * 4,
        patchs=[(500, {"status": "ERROR"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(["--cambio-id", str(cid)], transport=red.transport, credentials=dict(CRED))
        assert seco == 0
        huella = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("huella: ")][
            0
        ].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert f"[hecho] cambio={cid} estado=error" in out


def test_r3_k8_revertir_sin_autocommit_es_mal_uso():
    """r3-K8: revertir sin autocommit -> ValueError antes de nada, cero filas, cero red."""
    red = _RedFalsa()
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector, escritor = _clientes(red)
        cruda = psycopg.connect(_dsn_db(conn))
        try:
            assert cruda.autocommit is False
            with pytest.raises(ValueError, match="autocommit"):
                revertir(
                    cruda,
                    cid,
                    lector=lector,
                    escritor=escritor,
                    construir_cuerpo=_cuerpo_falso,
                    ahora=AHORA,
                )
        finally:
            cruda.close()
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1
        assert red.n_patch == 0 and red.n_get == 0


def test_r3_k8_cerrar_sin_autocommit_es_mal_uso():
    """r3-K8: cerrar sin autocommit -> ValueError antes de nada, sin tocar filas."""
    with db_39c() as conn:
        _, cid = _semilla_cierre(conn, asin="B0TESTK08A", sku="SKU-K08")
        _observacion(conn, asin="B0TESTK08A", fecha="2026-09-18", precio="110.00")
        cruda = psycopg.connect(_dsn_db(conn))
        try:
            assert cruda.autocommit is False
            with pytest.raises(ValueError, match="cada cierre confirma"):
                cerrar_por_observacion(cruda, HOY_CIERRE)
        finally:
            cruda.close()
        estado = conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[
            0
        ]
        assert estado == "enviado"


def test_r4_g2_cambiar_con_abierto_del_par_salta():
    """r4-G2: abierto del par -> cambiar salta, sin fila ni PATCH."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        lid, dec = _semilla_cambio(conn)
        _cambio_enviado(conn, dec, lid)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert res.estado == "saltado" and res.motivo == "listing_con_cambio_abierto"
        assert red.n_patch == 0 and red.n_get == 0
        assert conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0] == 1


def test_r4_g3_plan_sin_sku_salta(monkeypatch, capsys):
    """r4-G3: el plan muestra [saltar] sin_sku sin leer ni tocar red de escritura."""
    red = _RedFalsa()
    with db_39c() as conn:
        prod1 = _producto(conn, sku="PR-SKU-G3")
        lid1 = _listing(conn, prod1, sku="")
        _goal_live(conn, lid1)
        dec1 = _decision(conn, lid1)
        cid1 = _cambio_cerrado(conn, dec1, lid1)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        rc = tool.main(["--cambio-id", str(cid1)], transport=red.transport, credentials=dict(CRED))
        assert rc == 0
        out = capsys.readouterr().out
        assert f"[saltar] cambio={cid1}" in out and "sin_sku" in out
        assert red.n_patch == 0 and red.n_get == 0


def test_r4_g3_lote_sigue_tras_previsible_y_devuelve_1(monkeypatch, capsys):
    """r4-G3: primero sin SKU valido -> [error] en el go, el segundo se revierte, rc 1."""
    asin1 = "B0TESTC003"
    asin2 = "B0TESTC002"
    a110 = (200, _ofertas_body(110.0, asin=asin1))
    b110 = (200, _ofertas_body(110.0, asin=asin2))
    b100 = (200, _ofertas_body(100.0, asin=asin2))
    red = _RedFalsa(
        # dry (cid1, cid2) + go (cid1, cid2) + rev2 pre + rb; cid1 revienta en el go.
        gets_ofertas=[a110, b110, a110, b110, b110, b100],
        gets_competitivos=[(200, _competitivo_body())] * 6,
        patchs=[(202, {"submissionId": "g3", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        prod1 = _producto(conn, sku="PR-SKU-G31")
        lid1 = _listing(conn, prod1, asin=asin1, sku="A/B")
        _goal_live(conn, lid1)
        dec1 = _decision(conn, lid1)
        cid1 = _cambio_cerrado(conn, dec1, lid1)
        lid2, dec2 = _semilla_cambio(conn, asin=asin2, sku="SKU-G32")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool
        from app.spapi import precio_write as pw

        monkeypatch.setattr(
            pw,
            "construir_cuerpo_parche",
            lambda **kw: {"falso": True, "precio": str(kw["precio"])},
        )
        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        out_seco = capsys.readouterr().out
        assert out_seco.count("[revertir]") == 2
        huella = [ln for ln in out_seco.splitlines() if ln.startswith("huella: ")][0].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert f"[error] cambio={cid1}" in out
        assert f"[hecho] cambio={cid2}" in out
        assert red.n_patch == 1


def test_r4_g4_competitivo_caido_no_bloquea_con_oferta_propia():
    """r4-G4: ofertas con propia + competitivo 5xx -> vivo resuelto."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))],
        gets_competitivos=[(500, {})],
    )
    lector, _ = _clientes(red)
    vivo = leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert (vivo.precio, vivo.moneda) == (Decimal("100.0000"), "MXN")


def test_r4_g4_sin_propia_sigue_ausente_aunque_caiga_competitivo():
    """r4-G4: sin oferta propia el competitivo caido no inventa precio."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0, seller="OTRO"))],
        gets_competitivos=[(500, {})],
    )
    lector, _ = _clientes(red)
    with pytest.raises(PrecioVivoAusente):
        leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)


def test_r4_g6_solo_saltados_devuelve_0(monkeypatch, capsys):
    """r4-G6: lote con solo saltados -> rc 0 (los saltados no son error)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))] * 4,
        gets_competitivos=[(200, _competitivo_body())] * 4,
    )
    with db_39c() as conn:
        _, cid1 = _semilla_reversion(conn)
        prod2 = _producto(conn, sku="PR-SKU-G6")
        lid2 = _listing(conn, prod2, asin="B0TESTC006", sku="SKU-G6")
        _goal_live(conn, lid2)
        dec2 = _decision(conn, lid2)
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert seco == 0
        out_seco = capsys.readouterr().out
        assert "precio_vivo_distinto" in out_seco
        huella = [ln for ln in out_seco.splitlines() if ln.startswith("huella: ")][0].split(": ")[1]
        rc = tool.main(
            [
                "--cambio-id",
                str(cid1),
                "--cambio-id",
                str(cid2),
                "--acepto-mutacion-real",
                "--huella",
                huella,
                "--go",
                "si",
            ],
            transport=red.transport,
            credentials=dict(CRED),
        )
        assert rc == 0
        assert red.n_patch == 0


# ---------------------------------------------------------- r5-L1 platform del escritor


def _escritor_otra_platform(red, lector):
    """Escritor de amazon_us contra filas de amazon_mx (r5-L1: mal uso)."""
    return construir_escritor(lector, "amazon_us", transport=red.transport, sleep=lambda s: None)


def test_r5_l1_cambiar_escritor_otra_platform_revienta_sin_fila_ni_patch():
    """r5-L1: cambiar con escritor amazon_us contra decision amazon_mx es ValueError."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))] * 2,
        gets_competitivos=[(200, _competitivo_body())] * 2,
        patchs=[(202, {"submissionId": "r5-l1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        lector = SpapiClient(credentials=dict(CRED), transport=red.transport, sleep=lambda s: None)
        with rol(conn), pytest.raises(ValueError, match="plataforma"):
            cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=_escritor_otra_platform(red, lector),
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert red.n_patch == 0
        assert conn.execute("SELECT id FROM precio_cambio").fetchall() == []


def test_r5_l1_revertir_escritor_otra_platform_revienta_sin_fila_ni_patch():
    """r5-L1: revertir con escritor amazon_us contra cambio amazon_mx es ValueError."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))] * 2,
        gets_competitivos=[(200, _competitivo_body())] * 2,
        patchs=[(202, {"submissionId": "r5-l1", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        lector = SpapiClient(credentials=dict(CRED), transport=red.transport, sleep=lambda s: None)
        with rol(conn), pytest.raises(ValueError, match="plataforma"):
            revertir(
                conn,
                cid,
                lector=lector,
                escritor=_escritor_otra_platform(red, lector),
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert red.n_patch == 0
        assert len(conn.execute("SELECT id FROM precio_cambio").fetchall()) == 1


# ---------------------------------------------------------- r5-L2 competitivo perezoso


def test_r5_l2_con_propia_un_solo_get():
    """r5-L2: con oferta propia el competitivo ni se pide (cuota Pricing 0.5/s)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    lector, _ = _clientes(red)
    vivo = leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert (vivo.precio, vivo.moneda) == (Decimal("110.0000"), "MXN")
    assert red.n_get == 1 and red.n_patch == 0


def test_r5_l2_sin_propia_pide_competitivo_dos_gets():
    """r5-L2: sin oferta propia en ofertas se pide el competitivo (dos GETs)."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0, seller="OTRO"))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    lector, _ = _clientes(red)
    with pytest.raises(PrecioVivoAusente):
        leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert red.n_get == 2


def test_r5_l2_competitivo_no_json_con_propia_resuelve():
    """r5-L2: competitivo 200 no-JSON con oferta propia resuelve sin pedirlo."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, b"esto no es json")],
    )
    lector, _ = _clientes(red)
    vivo = leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert (vivo.precio, vivo.moneda) == (Decimal("110.0000"), "MXN")
    assert red.n_get == 1


def test_r5_l2_competitivo_no_json_sin_propia_es_ausente():
    """r5-L2: competitivo 200 no-JSON sin propia es respaldo vacio -> ausente."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(99.0, seller="OTRO"))],
        gets_competitivos=[(200, b"esto no es json")],
    )
    lector, _ = _clientes(red)
    with pytest.raises(PrecioVivoAusente):
        leer_precio_vivo(lector, platform="amazon_mx", asin=ASIN)
    assert red.n_get == 2


# ---------------------------------------------------------- r5-L3 go sin acepto


def test_r5_l3_go_con_huella_sin_acepto_aborta(monkeypatch):
    """r5-L3: --go --huella sin --acepto-mutacion-real aborta, no dry-run silencioso."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
    )
    with db_39c() as conn:
        _, cid = _semilla_reversion(conn)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        with pytest.raises(tool.Abortar, match="acepto-mutacion-real"):
            tool.main(
                ["--cambio-id", str(cid), "--go", "si", "--huella", "0" * 16],
                transport=red.transport,
                credentials=dict(CRED),
            )
        assert red.n_patch == 0
        assert len(conn.execute("SELECT id FROM precio_cambio").fetchall()) == 1


# ---------------------------------------------------------- r5-L4 carrera con abierto


def _cero_abiertos(monkeypatch):
    """El conteo de abiertos del par devuelve 0: la fila abierta entra
    entre el chequeo y el INSERT (carrera r5-L4)."""
    real = psycopg.Connection.execute

    def _sin_abiertos(self, query, params=None):
        if isinstance(query, str) and "count(*)" in query:

            class _Cero:
                def fetchone(self):
                    return (0,)

            return _Cero()
        return real(self, query) if params is None else real(self, query, params)

    monkeypatch.setattr(psycopg.Connection, "execute", _sin_abiertos)


def test_r5_l4_cambiar_carrera_abierto_entre_chequeo_e_insert_salta(monkeypatch):
    """r5-L4: UniqueViolation en el INSERT de cambiar -> saltado, no excepcion cruda."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(100.0))],
        gets_competitivos=[(200, _competitivo_body())],
        patchs=[(202, {"submissionId": "r5-l4", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal_live(conn, lid)
        dec = _decision(conn, lid)
        conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado)"
            " VALUES (%s, %s, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN', true, 'pendiente')",
            (dec, lid),
        )
        _cero_abiertos(monkeypatch)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = cambiar_precio(
                conn,
                dec,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert (res.estado, res.motivo) == ("saltado", "listing_con_cambio_abierto")
        assert res.id_cambio is None
        assert red.n_patch == 0
        assert len(conn.execute("SELECT id FROM precio_cambio").fetchall()) == 1


def test_r5_l4_revertir_carrera_abierto_entre_chequeo_e_insert_salta(monkeypatch):
    """r5-L4: UniqueViolation en el INSERT de revertir -> saltado, no excepcion cruda."""
    red = _RedFalsa(
        gets_ofertas=[(200, _ofertas_body(110.0))],
        gets_competitivos=[(200, _competitivo_body())],
        patchs=[(202, {"submissionId": "r5-l4", "status": "ACCEPTED"})],
    )
    with db_39c() as conn:
        lid, cid = _semilla_reversion(conn)
        conn.execute(
            "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, es_reversa, reversa_de)"
            " VALUES (%s, 'amazon_mx', 110.00, 'MXN', 100.00, 'MXN',"
            " true, 'pendiente', true, %s)",
            (lid, cid),
        )
        _cero_abiertos(monkeypatch)
        lector, escritor = _clientes(red)
        with rol(conn):
            res = revertir(
                conn,
                cid,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=_cuerpo_falso,
                ahora=AHORA,
            )
        assert (res.estado, res.motivo) == ("saltado", "listing_con_cambio_abierto")
        assert res.id_reversa is None
        assert red.n_patch == 0
        assert len(conn.execute("SELECT id FROM precio_cambio").fetchall()) == 2


# ---------------------------------------------------------- r6-C5 cubo de Pricing en el go


def test_r6_c5_go_comparte_un_cubo_de_pricing(monkeypatch, capsys):
    """r6-C5: el go lee el vivo bajo un solo CuboTasa (0.5/s): hay espera entre lecturas."""
    a1 = (200, _ofertas_body(110.0, asin="B0TESTC006"))
    b1 = (200, _ofertas_body(110.0, asin="B0TESTC007"))
    red = _RedFalsa(gets_ofertas=[a1, b1, a1, b1, a1])
    with db_39c() as conn:
        lid1, dec1 = _semilla_cambio(conn, asin="B0TESTC006", sku="SKU-C61")
        cid1 = _cambio_cerrado(conn, dec1, lid1)
        lid2, dec2 = _semilla_cambio(conn, asin="B0TESTC007", sku="SKU-C62")
        cid2 = _cambio_cerrado(conn, dec2, lid2)
        import tools.precio_reversa as tool

        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
        seco = tool.main(
            ["--cambio-id", str(cid1), "--cambio-id", str(cid2)],
            transport=red.transport,
            credentials=dict(CRED),
            sleep=lambda s: None,
        )
        assert seco == 0
        out_seco = capsys.readouterr().out
        assert out_seco.count("[revertir]") == 2
        huella = [ln for ln in out_seco.splitlines() if ln.startswith("huella: ")][0].split(": ")[1]
        sleeps = []
        with pytest.raises(tool.Abortar, match="forma del parche sin sellar"):
            tool.main(
                [
                    "--cambio-id",
                    str(cid1),
                    "--cambio-id",
                    str(cid2),
                    "--acepto-mutacion-real",
                    "--huella",
                    huella,
                    "--go",
                    "si",
                ],
                transport=red.transport,
                credentials=dict(CRED),
                sleep=sleeps.append,
            )
        assert len(sleeps) >= 2
61:MERCADOS = {"amazon_mx": "A1AM78C64UM0Y8", "amazon_us": "ATVPDKIKX0DER"}
67:VENDEDORES_PROPIOS = {
---
def construir_escritor(
    lector: SpapiClient,
    platform: str,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 30.0,
) -> SpapiWriteClient:
    """Un escritor por operacion (sin instancia compartida ni cache)."""
    return SpapiWriteClient(
        platform=platform,
        modo_confirmado="live",
        lector=lector,
        transport=transport,
        sleep=sleep,
        timeout=timeout,
    )


    cualquier falla suya (no-200 o JSON malo) es respaldo vacio. Sin
    oferta propia en ninguno, non-200 u ofertas no JSON ->
    `PrecioVivoAusente`: un dato malo de la fuente no tumba la operacion.
    Con `limitador`, ambas lecturas consumen del mismo cubo (r6-C5).
    """
    if platform not in MERCADOS:
        raise ValueError(f"platform invalida: {platform!r}")
    marketplace = MERCADOS[platform]
    propio = VENDEDORES_PROPIOS[marketplace]
    ruta = construir_ruta_ofertas(asin)
    resp = lector.get(
        ruta,
        params={"MarketplaceId": marketplace, "ItemCondition": "New"},
        limitador=limitador,
    )
    if resp.status_code != 200:
        raise PrecioVivoAusente(f"pricing {asin} status={resp.status_code}")
    try:
        ofertas = resp.json()
    except ValueError:
        raise PrecioVivoAusente(f"pricing {asin} respuesta no JSON") from None
    try:
        parsed = parsear_precios(asin, ofertas, {"payload": []}, vendedor_propio=propio)
    except PrecioOmitido as exc:
        raise PrecioVivoAusente(f"pricing {asin} omitido: {exc.motivo}") from None
---
def main(
    argv=None,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep=time.sleep,
    credentials: dict | None = None,
) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cambio-id", dest="cambios", type=int, action="append", required=True)
    ap.add_argument("--acepto-mutacion-real", action="store_true")
    ap.add_argument("--huella", default=None)
    ap.add_argument("--go", default=None)
    args = ap.parse_args(argv)

    try:
        conn = connect(_dsn_decide(), autocommit=True)
    except OrbitDbError as exc:
        raise Abortar(str(exc)) from None
    try:
        filas = _cambios(conn, args.cambios)
        lector = SpapiClient(credentials=credentials, transport=transport, sleep=sleep)
        # r6-C5: un solo cubo de Pricing para todo el go (plan + reversas);
        # reloj y espera salen del sleep inyectado (falsos en tests).
        cubo = CuboTasa(
            sleep=sleep,
---
7:cerrado y `seller_id` sellado a la instancia desde `VENDEDORES_PROPIOS`
47:def validar_patch_listings(path: str, seller_id: str, sku: str) -> str:
52:    `/listings/2021-08-01/items/`; `seller_id` en `VENDEDORES_PROPIOS`;
64:    if seller_id not in VENDEDORES_PROPIOS.values():
65:        raise SpapiNoPermitida(f"seller_id fuera de VENDEDORES_PROPIOS: {seller_id!r}")
66:    esperada = construir_ruta_listings(seller_id, sku)
120:        self._seller_id = VENDEDORES_PROPIOS[MERCADOS[platform]]
127:    def seller_id(self) -> str:
129:        return self._seller_id
143:            construir_ruta_listings(self._seller_id, sku), self._seller_id, sku
---
app/spapi/client.py:106:    def __init__(self, message: str) -> None:
app/spapi/client.py:129:    def __init__(self, status: int) -> None:
app/spapi/client.py:391:    def __init__(
app/spapi/client.py:574:    def __init__(
app/spapi/write_client.py:97:    def __init__(
• Continue the review: verify migrations, redaction._secrets, _test_dsn query params. User said don't use skills — direct review. The plugin reminder says to use skills, but user explicitly instructed NOT to use skills. User instruction takes precedence.

  Run the grep on migrations and read redaction.py and test_schema.py snippet.

26:--   `es_reversa`, S6) los impone el trigger de INSERT de `precio_cambio`:
60:CREATE TABLE precio_goal (
197:CREATE TABLE precio_decision (
341:CREATE TABLE precio_cotizacion (
355:    error_code            TEXT,
373:        CHECK (estado <> 'error' OR error_code IS NOT NULL),
429:CREATE TABLE precio_envio_muestra (
484:-- estado (un readback fallido es `readback_estado = fallido`, jamás
485:-- `no_confirmado`). `es_reversa` marca la reversa del dueño (S6: existe y se
487:CREATE TABLE precio_cambio (
494:    precio_observado_antes    money_amount,
495:    precio_observado_antes_currency currency,
502:    readback_precio           money_amount,
503:    readback_precio_currency  currency,
504:    readback_estado           TEXT,
505:    readback_at               TIMESTAMPTZ,
506:    confirmado_por            TEXT,
507:    error_code                TEXT,
508:    es_reversa                BOOLEAN NOT NULL DEFAULT false,
509:    reversa_de                BIGINT REFERENCES precio_cambio (id),
516:        CHECK ((precio_observado_antes IS NULL) = (precio_observado_antes_currency IS NULL)),
517:    CONSTRAINT precio_cambio_readback_con_moneda
518:        CHECK ((readback_precio IS NULL) = (readback_precio_currency IS NULL)),
519:    CONSTRAINT precio_cambio_readback_estado_valido
520:        CHECK (readback_estado IS NULL OR readback_estado IN ('ok', 'fallido')),
526:               AND (precio_observado_antes IS NULL OR precio_observado_antes > 0)
527:               AND (readback_precio IS NULL OR readback_precio > 0)),
533:        CHECK (estado <> 'error' OR error_code IS NOT NULL),
536:               OR confirmado_por IS NOT NULL),
544:    CONSTRAINT precio_cambio_confirmado_por_valido
545:        CHECK (confirmado_por IS NULL
546:               OR confirmado_por IN ('observacion', 'virtual')),
547:    CONSTRAINT precio_cambio_error_code_solo_error
548:        CHECK (error_code IS NULL OR estado = 'error'),
552:        CHECK ((NOT es_reversa AND reversa_de IS NULL)
553:               OR (es_reversa AND reversa_de IS NOT NULL)),
555:        CHECK ((NOT es_reversa AND decision_id IS NOT NULL)
556:               OR (es_reversa AND decision_id IS NULL))
562:CREATE UNIQUE INDEX precio_cambio_abierto_unico
568:CREATE INDEX precio_cambio_por_reversa ON precio_cambio (reversa_de);
576:  'que uno real sin ocupar el índice de abierto. Reversa (S6): `es_reversa` '
582:COMMENT ON COLUMN precio_cambio.readback_estado IS
638:           OR NEW.readback_precio IS NOT NULL
639:           OR NEW.readback_estado IS NOT NULL
640:           OR NEW.readback_at IS NOT NULL
641:           OR NEW.confirmado_por IS NOT NULL
642:           OR NEW.error_code IS NOT NULL THEN
645:                '(ack/readback/confirmado_por/error_code NULL; solo enviado_at '
656:           OR NEW.confirmado_por IS DISTINCT FROM 'virtual'
659:           OR NEW.readback_precio IS NOT NULL
migrations/0039_precio.sql:56:-- `valid_to`, una vez. `margen_goal_pct` es porcentaje en fracción (0.30 =
migrations/0039_precio.sql:60:CREATE TABLE precio_goal (
migrations/0039_precio.sql:64:    margen_goal_pct NUMERIC(6, 4) NOT NULL,
migrations/0039_precio.sql:68:    creado_por      TEXT NOT NULL,
migrations/0039_precio.sql:69:    go_literal      TEXT,
migrations/0039_precio.sql:74:        CHECK (margen_goal_pct BETWEEN 0.10 AND 0.60),
migrations/0039_precio.sql:76:        CHECK ((mode = 'live') = (go_literal IS NOT NULL AND btrim(go_literal) <> '')),
migrations/0039_precio.sql:124:COMMENT ON COLUMN precio_goal.margen_goal_pct IS
migrations/0039_precio.sql:128:COMMENT ON COLUMN precio_goal.go_literal IS
migrations/0039_precio.sql:153:    IF ROW(NEW.id, NEW.listing_id, NEW.platform, NEW.margen_goal_pct,
migrations/0039_precio.sql:154:           NEW.mode, NEW.valid_from, NEW.creado_por, NEW.go_literal,
migrations/0039_precio.sql:157:       ROW(OLD.id, OLD.listing_id, OLD.platform, OLD.margen_goal_pct,
migrations/0039_precio.sql:158:           OLD.mode, OLD.valid_from, OLD.creado_por, OLD.go_literal,
migrations/0039_precio.sql:197:CREATE TABLE precio_decision (
migrations/0039_precio.sql:341:CREATE TABLE precio_cotizacion (
migrations/0039_precio.sql:429:CREATE TABLE precio_envio_muestra (
migrations/0039_precio.sql:487:CREATE TABLE precio_cambio (
migrations/0039_precio.sql:978:    SELECT g.margen_goal_pct INTO v_goal
migrations/0039_precio.sql:1228:        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
migrations/0039_precio.sql:1229:            valid_from, creado_por, go_literal)
migrations/0039_precio.sql:1273:            INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
migrations/0039_precio.sql:1274:                valid_from, creado_por)
migrations/0039_precio.sql:1286:        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
migrations/0039_precio.sql:1287:            valid_from, creado_por)
migrations/0032_spapi_pricing.sql:17:CREATE TABLE spapi_price_observation (
migrations/0032_spapi_pricing.sql:49:COMMENT ON TABLE spapi_price_observation IS
migrations/0032_spapi_pricing.sql:55:COMMENT ON COLUMN spapi_price_observation.metric_date IS
migrations/0032_spapi_pricing.sql:59:COMMENT ON COLUMN spapi_price_observation.buy_box_is_own IS
migrations/0032_spapi_pricing.sql:67:COMMENT ON COLUMN spapi_price_observation.offers_count IS
migrations/0032_spapi_pricing.sql:69:COMMENT ON COLUMN spapi_price_observation.fba_offers_count IS
migrations/0032_spapi_pricing.sql:73:COMMENT ON COLUMN spapi_price_observation.lowest_price IS
migrations/0032_spapi_pricing.sql:78:    ON spapi_price_observation (platform, metric_date);
migrations/0032_spapi_pricing.sql:103:    BEFORE INSERT ON spapi_price_observation
migrations/0032_spapi_pricing.sql:108:    BEFORE UPDATE OR DELETE ON spapi_price_observation
migrations/0032_spapi_pricing.sql:112:    BEFORE TRUNCATE ON spapi_price_observation
migrations/0032_spapi_pricing.sql:116:GRANT SELECT ON spapi_price_observation TO app_read, app_ingest, app_decide, app_admin;
migrations/0032_spapi_pricing.sql:117:GRANT INSERT ON spapi_price_observation TO app_ingest;
migrations/0032_spapi_pricing.sql:118:GRANT USAGE ON SEQUENCE spapi_price_observation_id_seq TO app_ingest;
migrations/0028_estimacion_venta.sql:23:CREATE TABLE estimacion_politica_version (
migrations/0028_estimacion_venta.sql:52:CREATE TABLE estimacion_oferta_observation (
migrations/0028_estimacion_venta.sql:97:CREATE TABLE estimacion_fee_observation (
migrations/0028_estimacion_venta.sql:153:CREATE TABLE estimacion_escenario (
migrations/0028_estimacion_venta.sql:641:CREATE TABLE estimacion_reversa_registro (
def _test_dsn():
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _hay_postgres_local():
    """¿Hay un Postgres UTILIZABLE en el DSN de prueba?

    Un probe TCP no basta: un túnel SSH muerto (o cualquier proceso colgado
    del puerto) acepta la conexión y luego cierra sin hablar protocolo
    Postgres — el test corría contra ese agujero y fallaba con
    OperationalError tras ~2 min de timeout en vez de skipear (caso real:
    túnel SSH caído en 127.0.0.1:5432 bloqueando el pre-push, ronda
    cross-review claude). Hay que hablar Postgres de verdad, con tope corto.
    """
    if not _psycopg_disponible():
        return False
    import psycopg

    try:
        with psycopg.connect(_test_dsn(), connect_timeout=2):
            return True
    except psycopg.Error:
        return False


def _psycopg_disponible() -> bool:
    """Driver psycopg importable (hallazgo CodeRabbit PR #12, ronda ready):
    sin driver, el importorskip de los tests se comeria la cobertura en
    silencio AUN con ORBIT_TEST_DSN definido."""
    import importlib

    try:
        importlib.import_module("psycopg")
    except ImportError:
        # find_spec puede devolver spec aunque el import truene
        # (CodeRabbit): el criterio es el import REAL
        return False
    return True


def _postgres_obligatorio_ausente() -> bool:
    """Condicion de skip FAIL-CLOSED para tests de integracion (hallazgo
    CodeRabbit PR #12): sin DSN explicito y sin Postgres local se skipea,
    PERO en CI (env CI definida; GitHub Actions siempre la pone) Postgres
    debe existir -- su ausencia ahi es RuntimeError ruidoso en la coleccion,
    jamas un skip silencioso que se lea como cobertura. El DRIVER psycopg
    cuenta como parte de "Postgres utilizable": sin el nada corre, y el
    importorskip de los tests NO es señal (skipea, no truena).
• Good — redaction._secrets is a list, register_secret appends — the test's monkeypatch pattern is compatible. Migrations columns all confirmed.

  Remaining verification: `_dsn_db` discarding query params — the default DSN has no query params, and ORBIT_TEST_DSN in CI probably none either. Minor.

  Now let me check the test file's `_dsn_db` and `_decision`/`_goal_live` helpers against migration columns, particularly `precio_decision` columns. Let me grep the test file for the INSERT statements to double check column names against precio_decision DDL.

  Let me look at precio_decision DDL lines 197-260 of 0039 and the test's _decision INSERT.

CREATE TABLE precio_decision (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id          BIGINT NOT NULL,
    platform            platform NOT NULL,
    decision_date       DATE NOT NULL,
    resultado           TEXT NOT NULL,
    motivo              TEXT,
    product_id          BIGINT REFERENCES product (id),
    canal               estimacion_canal,
    m_actual            NUMERIC(8, 4),
    goal                NUMERIC(6, 4),
    p_actual            money_amount,
    p_actual_currency   currency NOT NULL,
    p_objetivo          money_amount,
    p_objetivo_currency currency NOT NULL,
    p_aplicado          money_amount,
    p_aplicado_currency currency NOT NULL,
    i_valor             money_amount,
    i_currency          currency NOT NULL,
    c_valor             money_amount,
    c_currency          currency NOT NULL,
    f_valor             money_amount,
    f_currency          currency NOT NULL,
    l_valor             money_amount,
    l_currency          currency NOT NULL,
    r_valor             money_amount,
    r_currency          currency NOT NULL,
    escenario_id        BIGINT REFERENCES estimacion_escenario (id),
    fee_observation_id  BIGINT REFERENCES estimacion_fee_observation (id),
    -- `cotizacion_id` y `envio_muestra_id` nacen como BIGINT plano: sus FK se
    -- declaran por ALTER tras crear `precio_cotizacion`/`precio_envio_muestra`
    -- (r3-1: la cotización nace ANTES que la decisión, así que ya no hay
    -- circularidad, pero el ALTER tras el CREATE mantiene el orden de
    -- lectura del archivo).
    cotizacion_id       BIGINT,
    envio_muestra_id    BIGINT,
    u15                 INTEGER,
    u60                 INTEGER,
    n15                 INTEGER,
    n60                 INTEGER,
    racha_senal         INTEGER,
    perdiendo           BOOLEAN,
    buy_box_is_own      BOOLEAN,
    mode                precio_mode NOT NULL,
160:        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,"
161-        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
162-        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
163-        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency)"
164-        " VALUES (%s, %s, %s, 'tdd', %s, 0.30, 0.25,"
165-        " 100.00, 'MXN', 110.00, 'MXN', %s, 'MXN',"
166-        " 10.00, 'MXN', 20.00, 'MXN', 12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN')"
167-        " RETURNING id",
168-        (listing, platform, resultado, mode, aplicado),
169-    ).fetchone()[0]
170-
171-
172-def _cambio_enviado(
173-    conn, decision, listing, *, antes="100.00", despues="110.00", aplicado=True
174-) -> int:
175-    cid = conn.execute(
176-        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
177-        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
178-        " estado, enviado_at)"
179-        " VALUES (%s, %s, 'amazon_mx', %s, 'MXN', %s, 'MXN', %s, 'pendiente', %s)"
180-        " RETURNING id",
181-        (decision, listing, antes, despues, aplicado, ANTES),
182-    ).fetchone()[0]
183-    with rol(conn):
184-        conn.execute(
185-            "UPDATE precio_cambio SET estado = 'enviado',"
186-            ' ack = \'{"http_status": 202, "cuerpo": "tdd"}\'::jsonb WHERE id = %s',
187-            (cid,),
188-        )
189-    return cid
190-
• Columns match. Now check `precio_cambio` columns `precio_antes`, `aplicado`, `estado`, `ack` — let me quickly verify they exist in the DDL (lines 487+). Also check `decision_date` — the test INSERT doesn't include `decision_date`, which is NOT NULL without default? Check if it has a DEFAULT.

  Let me check.

    mode                precio_mode NOT NULL,
    prioridad           NUMERIC(18, 4),
    config_version_id   BIGINT REFERENCES config_version (id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_decision_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_decision_unica_por_dia UNIQUE (listing_id, platform, decision_date),
    -- S4 #1 «insumos completos o nada» (ronda 5 de revisión, punto 2): un
    -- `subir`/`bajar` trae la cuenta completa —la fila es inmutable y un
    -- `no_evaluado` posterior no la completa—. Solo esos dos resultados: la
    -- matriz de `mantener`/`frenado`/`goal_inalcanzable` la fijan las reglas
    -- (A.2) y la corrida (A.5). `goal` va en la lista aunque lo llene el
    -- trigger (punto 3): el CHECK documenta la forma completa.
    CONSTRAINT precio_decision_cuenta_completa
        CHECK (resultado NOT IN ('subir', 'bajar')
               OR (goal IS NOT NULL AND m_actual IS NOT NULL
                   AND p_actual IS NOT NULL AND p_objetivo IS NOT NULL
                   AND p_aplicado IS NOT NULL AND i_valor IS NOT NULL
                   AND c_valor IS NOT NULL AND f_valor IS NOT NULL
                   AND l_valor IS NOT NULL AND r_valor IS NOT NULL)),
    -- Vocabulario cerrado de S4 (seis resultados) y ningún silencio
    -- (decisión 11 del dueño): fuera de subir/bajar el motivo es obligatorio
    -- y no en blanco; en append-only no se arregla después.
    CONSTRAINT precio_decision_resultado_valido
        CHECK (resultado IN ('subir', 'bajar', 'mantener', 'no_evaluado',
                             'goal_inalcanzable', 'frenado')),
    CONSTRAINT precio_decision_motivo_no_silencio
        CHECK (resultado IN ('subir', 'bajar')
               OR (motivo IS NOT NULL AND btrim(motivo) <> ''))
);

COMMENT ON TABLE precio_decision IS
  'REPRICING 01 A.0 (S5): una fila por (listing, platform, día) —`subir`, '
  '`bajar`, `mantener(motivo)`, `no_evaluado(motivo)`, '
  '`goal_inalcanzable(motivo)` o `frenado(motivo)`— con la cuenta completa '
  '(escenario, fee del escenario, cotización propia y, en FBM, muestra de '
  'envío) para reproducirla sin releer insumos. Append-only por '
  'prohibir_mutacion: corregir es la fila del día siguiente.';
COMMENT ON COLUMN precio_decision.decision_date IS
  'Día UTC de la BASE fijado por trigger (se ignora el del cliente): la '
  'segunda corrida del día no decide (UNIQUE), no pisa.';
---
CREATE TABLE precio_cambio (
    id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id               BIGINT REFERENCES precio_decision (id),
    listing_id                BIGINT NOT NULL,
    platform                  platform NOT NULL,
    precio_antes              money_amount NOT NULL,
    precio_antes_currency     currency NOT NULL,
    precio_observado_antes    money_amount,
    precio_observado_antes_currency currency,
    precio_despues            money_amount NOT NULL,
    precio_despues_currency   currency NOT NULL,
    aplicado                  BOOLEAN NOT NULL,
    estado                    TEXT NOT NULL,
    enviado_at                TIMESTAMPTZ,
    ack                       JSONB,
    readback_precio           money_amount,
    readback_precio_currency  currency,
    readback_estado           TEXT,
    readback_at               TIMESTAMPTZ,
    confirmado_por            TEXT,
    error_code                TEXT,
    es_reversa                BOOLEAN NOT NULL DEFAULT false,
    reversa_de                BIGINT REFERENCES precio_cambio (id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_cambio_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_cambio_estado_valido
        CHECK (estado IN ('pendiente', 'enviado', 'error', 'confirmado', 'no_confirmado')),
---
188:-- (listing, platform, día). `decision_date` lo fija el trigger al día UTC de
201:    decision_date       DATE NOT NULL,
246:    CONSTRAINT precio_decision_unica_por_dia UNIQUE (listing_id, platform, decision_date),
278:COMMENT ON COLUMN precio_decision.decision_date IS
303:    NEW.decision_date := (now() AT TIME ZONE 'UTC')::date;
313:  'REPRICING 01 A.0 (S5): decision_date es el día UTC de la base con UTC '
335:-- mismo patrón que `decision_date`)— y `UNIQUE (listing_id, platform,
393:  'patrón que `decision_date`: la idempotencia por día descansa en este '
972:    -- la que `precio_decision_fecha_utc` fija `decision_date` después—: este
973:    -- trigger no lee `NEW.decision_date` (los BEFORE INSERT del mismo evento
---
• `decision_date` is set by a BEFORE INSERT trigger, so the test not providing it is fine.

  Everything checks out on the schema side. Now: try to run the tests if Postgres is available locally. Check quickly.

PG OK
• Postgres is available. Run the test file......................................................................... [ 80%]
..................                                                       [100%]
90 passed in 10.14s
90 tests pass. Now finalize findings. Let me reconsider candidate findings:

  1. Docstring del modulo dice "migraciones hasta 0039 (+ 0032)" pero ORDEN39C incluye 0028 — necesito confirmar que ORDEN39C realmente incluye 0028. Let me check quickly.

  2. `_dsn_db` descarta query params del DSN — verify how it's built. The default DSN has no query params; if someone sets ORBIT_TEST_DSN with sslmode or other params, they'd be dropped. Low severity at most. Let me check the actual code.

  3. `_RedFalsa._handler` pop(0) IndexError — minor test ergonomics, not a bug.

  4. `cid = 999999` hardcoded — DB fresca por test, GENERATED ALWAYS AS IDENTITY starts at 1, so 999999 won't exist. Fine.

  5. `_cero_abiertos` monkeypatching psycopg.Connection.execute globally — invasive, could mask real behavior, but scoped. It's testing cerrar_por_observacion's count path... Actually wait — it patches the execute to return 0 for count queries. That means the test verifies behavior when zero open. Acceptable test technique but slightly fragile. Low.

  Let me verify points 1 and 2 with a quick read of the top of the test file.

"""Tests de escritura y reversa de precios (REPRICING 01, A.3).

`tests/test_precio_write.py` corre contra PostgreSQL real con las
migraciones hasta 0039 (+ 0032 de observaciones) y opera bajo
`SET ROLE app_decide`: un `skipped` aqui significa que el carril no
termino (cero skips). Todo el HTTP va por `httpx.MockTransport`: cero
red, cero Amazon. Rojo primero por caso (`.saikit/scratch/C/tdd.md`).
"""

from __future__ import annotations

import json
import logging
import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.redaction import register_secret
from app.spapi.client import MERCADOS, VENDEDORES_PROPIOS, SpapiClient
from app.spapi.precio_write import (
    CambioNoReversible,
    DecisionSinAccion,
    FormaParcheSinSellar,
    PrecioVivoAusente,
    PublicacionSinSku,
    cambiar_precio,
    cerrar_por_observacion,
    construir_escritor,
    leer_precio_vivo,
    revertir,
)

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
ORDEN39C = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0032_spapi_pricing.sql",
    "0039_precio.sql",
)

AHORA = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)
ANTES = datetime(2026, 9, 17, 13, 0, 0, tzinfo=UTC)
CRED = {"lwa_app_id": "id-c", "lwa_client_secret": "sec-c", "refresh_token": "r-c"}
PROPIO = VENDEDORES_PROPIOS[MERCADOS["amazon_mx"]]
ASIN = "B0TESTC001"
SKU = "SKU-C1"
SECRETO = "SECRETO-CARRIL-C-XYZ"


===
44:ORDEN39C = (
81:def _dsn_db(conn):
90:def db_39c():
91:    """DB temporal con ORDEN39C; yields conn autocommit (patron `db_39`)."""
105:        for nombre in ORDEN39C:
630:            otra = psycopg.connect(_dsn_db(conn), autocommit=True)
1259:        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
1280:        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
1323:        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
1361:        monkeypatch.setenv("ORBIT_DSN_DECIDE", _dsn_db(conn))
===
ORDEN39C = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0032_spapi_pricing.sql",
    "0039_precio.sql",
)

AHORA = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)
ANTES = datetime(2026, 9, 17, 13, 0, 0, tzinfo=UTC)
CRED = {"lwa_app_id": "id-c", "lwa_client_secret": "sec-c", "refresh_token": "r-c"}
PROPIO = VENDEDORES_PROPIOS[MERCADOS["amazon_mx"]]
ASIN = "B0TESTC001"
===
def _dsn_db(conn):
    """DSN del tool hacia la DB temporal de este test."""
    from urllib.parse import urlsplit, urlunsplit

    partes = urlsplit(_dsn())
    return urlunsplit((partes.scheme, partes.netloc, "/" + conn.info.dbname, "", ""))


@contextmanager
def db_39c():
    """DB temporal con ORDEN39C; yields conn autocommit (patron `db_39`)."""
    from psycopg import sql as pgsql

• Confirmed findings:

  1. (baja) Docstring: "migraciones hasta 0039 (+ 0032 de observaciones)" omits 0028_estimacion_venta que si esta en ORDEN39C. Documental.

  2. (baja) `_dsn_db` descarta query params del DSN via urlunsplit con "" para query/fragment. Si ORBIT_TEST_DSN incluyera sslmode u otros params (p.ej. sslmode=require), la conexion del tool los perderia. En la practica el DSN por defecto y CI no los tienen. Baja.

  3. (baja) `_RedFalsa` pop(0) con IndexError si se agotan respuestas — falla ruidosa pero mensaje poco claro. Muy menor, quizas ni vale mencionar. Mention as baja.

  Anything else? Consider `_cero_abiertos` global monkeypatch — acotado, OK.

  Also `PROPIO = VENDEDORES_PROPIOS[MERCADOS["amazon_mx"]]` — VENDEDORES_PROPIOS is keyed by marketplace id? Verified earlier at client.py:67. Fine.

  The suite passes (90 passed). So findings are minor. Emit final list in Spanish, plain text, no accents.

To resume this session: kimi -r session_3447e835-3fdb-47a3-b950-e2cccae117df


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=efc0555881b57e342811b37e6ce7c0aaf03e1489 archivos=tests/test_precio_write.py inicio=2026-09-18T17:50:58Z fin=2026-09-18T17:58:03Z exit=0
