=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): tests/test_precio_corrida.py
Tamano del diff: 125668 caracteres (truncado a 60000)
==> Invocando kimi de forma no interactiva (tope: 1500 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Leo el diff y lo reviso directamente, sin skills.

• El dump tiene 1576 lineas; leo el resto.

• Faltan las lineas 1001-1576 del dump; las leo con offset.

• El Read sin offset repite el inicio; ahora leo explicitamente desde la linea 1001.

• Elijo seguir con la evidencia: extraigo las lineas 1001-1576 con `sed` para ver el resto del diff.

• El dump esta truncado en ~60KB; verifico dependencias y leo el resto del archivo real en el repo.

• Ya tengo el archivo completo. Verifico las dependencias clave en el repo real.

• Verifico los ultimos puntos: la firma de `Resumen`/`reservar` y el patron de import de `test_schema`.

• Una ultima verificacion: la cota del cap que el test asume como invalida (99).

• Un detalle: el test espera `ValueError` pero `correr` envuelve en `ConfigInvalida`; verifico la jerarquia.

• Revise el diff completo (3158 lineas; el dump estaba truncado en ~60KB, asi que lei el resto directamente de `tests/test_precio_corrida.py` en el repo) y verifique contra el codigo real las dependencias que el diff no muestra: migraciones 0001/0002/0028/0032/0035/0039 existen, los triggers `precio_cambio_coherente` y `precio_decision_fecha_utc` existen en `migrations/0039_precio.sql`, el CHECK de `precio_cambio` admite `decision_id NULL` solo para reversas (coincide con los seeds), `ConfigInvalida` hereda de `ValueError` (los `pytest.raises(ValueError)` son validos), `Resumen` tiene `lineas`/`errores`/`cerrados`, `reservar` acepta `extra`, `app/cli.py` expone `connect`, `_conexion_decide` y `_clientes_precio` a nivel modulo (los monkeypatch muerden), y la linea de crontab esta literal en `docs/DEPLOY.md:391`. No ejecute la suite (requiere Postgres en `ORBIT_TEST_DSN`); no puedo afirmar que pase, solo que no veo motivo estatico para que falle.

  Hallazgos:

  1. NO BLOQUEANTE — `_cambio_con_estado` (tests/test_precio_corrida.py:942) declara el parametro `reversa=False` y nunca lo usa: ni en el cuerpo ni ninguna llamada lo pasa. Parametro muerto, ruido menor.

  2. NO BLOQUEANTE — `test_fase1_aisla_error_de_publicacion` (~:2460) y `test_parche_sin_sellar_persiste_filas_sin_reintentar` (~:2224) sabotear "la primera llamada" (`if len(llamadas) == 1`) asumiendo que el listing `a` se procesa primero; si fase 1/3 cambia el orden de iteracion, el test falla sin que el comportamiento este roto. Fragilidad por orden implicito.

  3. NO BLOQUEANTE — La barrera anti-intermitencia de `_RedFalsa._handler` (:584) usa `assert self.retener_patch.wait(timeout=120)` dentro del stack de `correr`: si expira, la `AssertionError` viaja por el camino de errores de la corrida (scrub/registro) en vez de fallar el test limpiamente; en `test_dos_hilos` el fallo igual se detecta en los asserts finales, pero el diagnostico queda opaco.

  4. NO BLOQUEANTE — Los contadores de `_RedFalsa` (`n_get`/`n_patch`/`n_fees`, :559+) se mutan sin lock y la clase se comparte entre dos hilos en `test_dos_hilos_exactamente_un_patch`; hoy es benigno (solo un hilo llega a PATCH), pero la clase invita a reuso concurrente donde `+=` no es atomico.

  5. NO BLOQUEANTE — `_RedFalsa._handler` (:561) cae en silencio al ASIN por defecto `B0A5000001` cuando la ruta no termina en `/offers`; si el cliente cambiara la forma de la URL, el cuerpo llevaria un ASIN que no corresponde al pedido y el test fallaria (o peor, pasaria) por la razon equivocada.

  Nada de seguridad, datos ni reglas innegociables: no hay `float` para dinero en seeds (todo `Decimal`; los `float(...)` solo aparecen en el JSON de la red falsa, que es lo que la API real devuelve), los triggers se deshabilitan/rehabilitan en `try/finally`, y los tests que siembran historia lo hacen respetando los CHECK del esquema.



=== stderr ===
kimi version 0.39.1
• The user explicitly says: don't use skills, act as an external reviewer, read the diff file and review it directly. User instructions take precedence. Read the file.

• The file is 1576 lines total in the dump; Read capped at 1000. I need the rest (lines 1001-1576 of the dump, which includes the remainder of the diff). Note the diff says +3158 lines but the file only has 1576 — the diff is truncated? Total lines 1576 means the dump file is 1576 lines. Let me read the remainder.

• The Read returned the same first 1000 lines. I need lines 1001-1576 of the dump file — I should use line_offset=1001.

• Read without offset keeps returning from the start. I need line_offset=1001.

• The Read tool keeps returning lines 1-1000 regardless. I need line_offset=1001 explicitly. The tool seems to ignore... actually I never passed line_offset. Let me use Bash sed to get lines 1001-1576, or Read with line_offset=1001.

+    ).fetchone()[0]
+
+
+@_skip_sin_pg
+def test_enviado_de_ayer_se_cierra_antes_de_decidir():
+    """El `enviado` de ayer del MISMO listing amanece `no_confirmado` y frena hoy.
+
+    El cierre corre antes de decidir: un mutante que cierre despues deja
+    pasar el `subir` y muere.
+    """
+    with _db() as (conn, _dsn):
+        datos = _cadena(conn, mode="live")
+        _price_obs(conn)
+        _otro, dec_b = _andamio_hoy(conn, sku="SKU-EC-B", ext="B0A50000EC")
+        ayer = datetime.now(UTC) - timedelta(days=1)
+        cid = _cambio_historia(conn, dec_b, datos["listing"], estado="enviado", dia=ayer)
+        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
+        res = _corre(conn, red)
+        assert (
+            conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[0]
+            == "no_confirmado"
+        )
+        assert (res.decisiones, res.escritas) == (1, 0)
+        fila = conn.execute(
+            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
+            (datos["listing"],),
+        ).fetchone()
+        assert fila == ("frenado", "no_confirmado")
+        assert red.n_patch == 0
+
+
+@_skip_sin_pg
+def test_freno_tres_dias_de_error_unidad():
+    """Tres `error` seguidos frenan; con dos o huecos, no.
+
+    El end-to-end de dos dias no se siembra (la base fija `decision_date`
+    a hoy): la rama `True` en la corrida la prueba el mutante M-freno, que
+    quita la llamada y deja pasar el `subir`.
+    """
+    from app.precio.corrida import freno_por_error
+
+    with _db() as (conn, _dsn):
+        prod = _producto(conn)
+        lid = _listing(conn, prod)
+        _config(conn)
+        _goal(conn, lid, mode="live")
+        dec = _decision_subir(conn, lid)
+        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
+        desde = hoy - timedelta(days=30)
+        assert freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=desde) is False
+        for atraso in (1, 2):
+            dia = datetime.now(UTC) - timedelta(days=atraso)
+            _cambio_con_estado(conn, dec, lid, estado="error", dia=dia)
+        assert freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=desde) is False
+        _cambio_con_estado(
+            conn, dec, lid, estado="error", dia=datetime.now(UTC) - timedelta(days=3)
+        )
+        assert freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=desde) is True
+        # B3: con goal nuevo posterior a la racha se evalua normal.
+        assert (
+            freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=hoy - timedelta(days=1))
+            is False
+        )
+        # D11: con hueco (-1, -2, -4) no hay racha de 3: no frena.
+        prod_h = _producto(conn, sku="SKU-FH-H")
+        lid_h = _listing(conn, prod_h, ext="B0A50000FH", sku="SKU-FH-H")
+        _goal(conn, lid_h, mode="live")
+        dec_h = _decision_subir(conn, lid_h)
+        for atraso in (1, 2, 4):
+            _cambio_con_estado(
+                conn, dec_h, lid_h, estado="error", dia=datetime.now(UTC) - timedelta(days=atraso)
+            )
+        assert freno_por_error(conn, lid_h, "amazon_mx", hoy, dias=3, desde=desde) is False
+
+
+@_skip_sin_pg
+def test_freno_forzado_persiste_sin_patch(monkeypatch):
+    """Con freno activo la corrida persiste `frenado` y no aplica."""
+    import app.precio.corrida as corrida_mod
+
+    with _db() as (conn, _dsn):
+        datos = _cadena(conn, mode="live")
+        _price_obs(conn)
+        monkeypatch.setattr(corrida_mod, "freno_por_error", lambda *a, **k: True)
+        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
+        res = _corre(conn, red)
+        assert (res.decisiones, res.escritas) == (1, 0)
+        fila = _decision_unica(conn)
+        assert fila[:2] == ("frenado", "api_error")
+        assert _cuenta_cambios(conn) == 0
+        assert red.n_patch == 0
+
+
+@_skip_sin_pg
+def test_freno_se_evalua_para_cada_goal(monkeypatch):
+    """La corrida evalua el freno de errores por cada goal (cableado).
+
+    K9: con DOS goals, el espia ve los dos.
+    """
+    import app.precio.corrida as corrida_mod
+
+    with _db() as (conn, _dsn):
+        datos = _cadena(conn, mode="live")
+        _price_obs(conn)
+        prod_b = _producto(conn, sku="SKU-FG-B")
+        lid_b = _listing(conn, prod_b, ext="B0A50000FB", sku="SKU-FG-B")
+        _goal(conn, lid_b, mode="live")
+        llamadas = []
+        real = corrida_mod.freno_por_error
+
+        def _espia(c, lid, platform, hoy, *, dias, desde):
+            llamadas.append((lid, platform, dias))
+            return real(c, lid, platform, hoy, dias=dias, desde=desde)
+
+        monkeypatch.setattr(corrida_mod, "freno_por_error", _espia)
+        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
+        res = _corre(conn, red)
+        assert (res.decisiones, res.escritas) == (2, 1)
+        assert sorted(llamadas) == sorted(
+            [(datos["listing"], "amazon_mx", 3), (lid_b, "amazon_mx", 3)]
+        )
+
+
+@_skip_sin_pg
+def test_huerfana_no_toca_reversas():
+    """La `pendiente` reversa es del dueno: la corrida no la cierra.
+
+    Sembrada ayer como el caso positivo: solo difiere en `es_reversa`.
+    """
+    with _db() as (conn, _dsn):
+        prod = _producto(conn)
+        lid = _listing(conn, prod)
+        _config(conn)
+        _goal(conn, lid, mode="live")
+        dec = _decision_subir(conn, lid)
+        ahora = datetime.now(UTC)
+        orig = _cambio_con_estado(conn, dec, lid, estado="enviado", dia=ahora)
+        conn.execute(
+            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
+            " WHERE id = %s",
+            (orig,),
+        )
+        conn.execute(
+            "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
+            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
+            " estado, enviado_at, es_reversa, reversa_de)"
+            " VALUES (%s, 'amazon_mx', 110, 'MXN', 100, 'MXN', true,"
+            " 'pendiente', %s, true, %s) RETURNING id",
+            (lid, ahora - timedelta(days=1), orig),
+        ).fetchone()[0]
+        red = _RedFalsa()
+        res = _corre(conn, red)
+        assert res.huerfanas == 0
+        assert (
+            conn.execute(
+                "SELECT count(*) FROM precio_cambio WHERE es_reversa AND estado = 'pendiente'"
+            ).fetchone()[0]
+            == 1
+        )
+
+
+@_skip_sin_pg
+def test_cambiar_precio_pasa_limitador_a_sus_lecturas():
+    """(a) con `limitador`, el GET previo y el readback consumen cubo."""
+    from app.spapi.client import CuboTasa
+    from app.spapi.precio_write import cambiar_precio
+
+    with _db() as (conn, _dsn):
+        prod = _producto(conn)
+        lid = _listing(conn, prod)
+        _config(conn)
+        _goal(conn, lid, mode="live")
+        dec = _decision_subir(conn, lid)
+        clock, sleep, _estado = _reloj_falso()
+        red = _RedFalsa(precio_vivo="100.00")
+        lector, escritor, _fees = _clientes(red)
+        consumos = []
+        cubo = CuboTasa(sleep=sleep, clock=clock, capacidad=100, tasa=1000.0)
+        real = cubo.consumir
+
+        def _contando():
+            consumos.append(1)
+            return real()
+
+        cubo.consumir = _contando
+        res = cambiar_precio(
+            conn,
+            dec,
+            lector=lector,
+            escritor=escritor,
+            construir_cuerpo=_cuerpo_falso,
+            limitador=cubo,
+        )
+        assert res.estado == "enviado"
+        # C3-6: conteo exacto (GET previo + readback; el PATCH no consume).
+        assert len(consumos) == 2
+
+
+@_skip_sin_pg
+def test_corrida_usa_un_solo_cubo_por_plataforma():
+    """(a) la corrida aplica todo con el limitador inyectado (uno por plataforma).
+
+    C3-6: conteos exactos (2 consumos por cambio); un cubo nuevo por
+    listing deja cero consumos y muere.
+    """
+    with _db() as (conn, _dsn):
+        ua = _cadena(conn, sku="SKU-U-A", ext="B0A50000UA", mode="live")
+        ub = _cadena(conn, sku="SKU-U-B", ext="B0A50000UB", mode="live")
+        _price_obs(conn, asin="B0A50000UA")
+        _price_obs(conn, asin="B0A50000UB")
+        clock, sleep, _estado = _reloj_falso()
+        cubo = CuboTasa(sleep=sleep, clock=clock, capacidad=100, tasa=1000.0)
+        real = cubo.consumir
+        consumos = []
+
+        def _contando():
+            consumos.append(1)
+            return real()
+
+        cubo.consumir = _contando
+        red = _RedFalsa(skus={ua["evento"]: ua["sku"], ub["evento"]: ub["sku"]})
+        res = _corre(conn, red, limitador=cubo)
+        assert (res.decisiones, res.escritas) == (2, 2)
+        assert len(consumos) == 4
+        assert len(red.pedidos_patch) == 2
+
+
+@_skip_sin_pg
+def test_repartir_cupo_recibe_una_plataforma(monkeypatch):
+    """(b) `repartir_cupo` se llama una vez por plataforma, sin mezclas.
+
+    C3-7: con un goal vigente de OTRA plataforma, ese listing no entra
+    a los candidatos.
+    """
+    import app.precio.corrida as corrida_mod
+
+    with _db() as (conn, _dsn):
+        datos = _cadena(conn, mode="live")
+        _price_obs(conn)
+        prod_us = _producto(conn, "SKU-RP-US")
+        lid_us = _listing(conn, prod_us, platform="amazon_us", ext="B0A50000US", sku="SKU-RP-US")
+        _goal(conn, lid_us, platform="amazon_us", mode="live")
+        llamadas = []
+        real = corrida_mod.repartir_cupo
+
+        def _espia(candidatos, *, cupo):
+            plataformas = set()
+            lids = []
+            for lid, _dec in candidatos:
+                lids.append(lid)
+                plataformas.add(
+                    conn.execute("SELECT platform FROM listing WHERE id = %s", (lid,)).fetchone()[0]
+                )
+            llamadas.append((lids, plataformas, cupo))
+            return real(candidatos, cupo=cupo)
+
+        monkeypatch.setattr(corrida_mod, "repartir_cupo", _espia)
+        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
+        _corre(conn, red)
+        assert len(llamadas) == 1
+        assert llamadas[0][1] == {"amazon_mx"}
+        assert lid_us not in llamadas[0][0]
+
+
+@_skip_sin_pg
+@pytest.mark.parametrize(
+    "ajuste,clave",
+    [
+        ({"precio_cap_amazon_mx": 99}, "precio_cap_amazon_mx"),
+        ({"precio_freno_dias_error": 0}, "precio_freno_dias_error"),
+    ],
+)
+def test_umbral_fuera_de_cota_aborta_al_arrancar(ajuste, clave):
+    """(f) cap o freno fuera de cota: `ValueError` que nombra la clave."""
+    with _db() as (conn, _dsn):
+        _config(conn, {**SETTINGS, **ajuste})
+        red = _RedFalsa()
+        with pytest.raises(ValueError, match=clave):
+            _corre(conn, red)
+        # D12: el aborto es al arrancar (antes del lock): con config sana
+        # la corrida valida corre (el claim no quedo tomado).
+        _config(conn)
+        assert _corre(conn, red).decisiones == 0
+
+
+@_skip_sin_pg
+@pytest.mark.parametrize("clave", ["precio_cap_amazon_mx", "precio_freno_dias_error"])
+def test_umbral_ausente_aborta_al_arrancar(clave):
+    """(f) cap o freno ausente: `ValueError` que nombra la clave, sin default."""
+    with _db() as (conn, _dsn):
+        ajustes = {k: v for k, v in SETTINGS.items() if k != clave}
+        _config(conn, ajustes)
+        red = _RedFalsa()
+        with pytest.raises(ValueError, match=clave):
+            _corre(conn, red)
+        # D12: el aborto es al arrancar (antes del lock): con config sana
+        # la corrida valida corre (el claim no quedo tomado).
+        _config(conn)
+        assert _corre(conn, red).decisiones == 0
+
+
+@_skip_sin_pg
+def test_reporte_es_solo_lectura():
+    """`precio --reporte` resume decisiones y cambios sin escribir nada.
+
+    D13: la conexion va en `default_transaction_read_only = on` (cualquier
+    escritura del reporte revienta); la corrida vive en su propio test.
+    """
+    from app.precio.corrida import reporte
+
+    with _db() as (conn, _dsn):
+        datos = _cadena(conn, mode="live")
+        _price_obs(conn)
+        dec = _decision_subir(conn, datos["listing"])
+        _cambio_con_estado(conn, dec, datos["listing"], estado="enviado", dia=datetime.now(UTC))
+        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
+        conn.execute("SET default_transaction_read_only = on")
+        lineas = reporte(conn, desde=hoy, hasta=hoy, platform="amazon_mx")
+        assert any("subir=1" in ln for ln in lineas)
+        assert any("decisiones=1" in ln for ln in lineas)
+        assert _cuenta_decisiones(conn) == 1
+        assert _cuenta_cambios(conn) == 1
+
+
+def _ventas_perdiendo(conn, prod, *, sku, run_id, platform="amazon_mx", hoy=None):
+    """75 dias de ledger + inventario + estado: u15=15, u60=120 (pierde).
+
+    Esperado = 120/60*15*(1-0.40) = 18 > 15: con `senal_dias=1` la racha
+    cierra en esta corrida (`perdiendo`, racha 1).
+    """
+    hoy = hoy or conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
+    for atraso in range(1, 76):
+        dia = hoy - timedelta(days=atraso)
+        qty = 1 if atraso <= 15 else 2
+        conn.execute(
+            "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
+            " product_id, quantity, amount, amount_currency, ingest_run_id)"
+            " VALUES (%s, 'sale', %s, %s, %s, %s, %s, 'MXN', %s)",
+            (platform, dia, datetime.now(UTC), prod, qty, Decimal("200"), run_id),
+        )
+        if atraso <= 15:
+            mediodia = datetime(dia.year, dia.month, dia.day, 12, tzinfo=UTC)
+            conn.execute(
+                "INSERT INTO spapi_inventario_observation (seller_sku, platform,"
+                " metric_date, observed_at, total_quantity, api_version)"
+                " VALUES (%s, %s, %s, %s, 5, 'v1')",
+                (sku, platform, dia, mediodia),
+            )
+            conn.execute(
+                "INSERT INTO spapi_listing_estado_observation (seller_sku, platform,"
+                " status, api_version, observed_at)"
+                " VALUES (%s, %s, 'BUYABLE', 'v1', %s)",
+                (sku, platform, mediodia),
+            )
+
+
+@_skip_sin_pg
+def test_virtual_de_sombra_dispara_freno_6():
+    """(c) el virtual cuenta en sombra: perdiendo + subida = `frenado`.
+
+    A nivel del ensamblado de la corrida + `decidir` (la corrida completa
+    saltaria este listing: el andamio trae decision de hoy y la base fija
+    `decision_date`; el feed de cambios es el mismo que usa el run).
+    """
+    from app.precio.config import leer_config
+    from app.precio.corrida import armar_entrada
+    from app.precio.reglas import decidir
+
+    with _db() as (conn, _dsn):
+        ajustes = {**SETTINGS, "precio_senal_dias": 1}
+        prod = _producto(conn, sku="ODOO-F6")
+        lid = _listing(conn, prod, ext="B0A50000F6", sku="SKU-F6")
+        _config(conn, ajustes)
+        _goal(conn, lid, mode="shadow")
+        marca = datetime.now(UTC)
+        oferta = _oferta_obs(
+            conn,
+            lid,
+            ext="B0A50000F6",
+            sku="SKU-F6",
+            huella="ctx-F6",
+            fetched=marca,
+            observed=marca,
+        )
+        fee = _fee_obs(
+            conn,
+            oferta,
+            lid,
+            ext="B0A50000F6",
+            sku="SKU-F6",
+            huella="ctx-F6",
+            fetched=marca,
+            observed=marca,
+        )
+        pol = _politica(conn)
+        costo_id = _costo(conn, prod)
+        run_id, validada_en = _run(conn)
+        _escenario(
+            conn,
+            lid,
+            oferta,
+            fee,
+            pol,
+            costo_id,
+            run_id,
+            validada_en,
+            ext="B0A50000F6",
+            sku="SKU-F6",
+            huella="ctx-F6",
+        )
+        run_ledger, _fin_ledger = _run_ledger(conn)
+        _ventas_perdiendo(conn, prod, sku="SKU-F6", run_id=run_ledger)
+        dec = _decision_subir(conn, lid, mode="shadow")
+        conn.execute(
+            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
+            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
+            " estado, enviado_at, confirmado_por)"
+            " VALUES (%s, %s, 'amazon_mx', 100, 'MXN', 110, 'MXN', false,"
+            " 'confirmado', %s, 'virtual')",
+            (dec, lid, datetime.now(UTC)),
+        )
+        _price_obs(conn, asin="B0A50000F6")
+        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
+        ahora = datetime.now(UTC)
+        config = leer_config(ajustes)
+        entrada, _ids = armar_entrada(
+            conn,
+            lid,
+            "amazon_mx",
+            hoy=hoy,
+            ahora=ahora,
+            config=config,
+            mode="shadow",
+            goal=Decimal("0.30"),
+            goal_vigente_desde=hoy - timedelta(days=30),
+            product_id=prod,
+        )
+        assert entrada is not None
+        d = decidir(entrada, hoy=hoy, config=config)
+        assert (d.resultado, d.motivo) == ("frenado", "perdiendo_tras_subida")
+
+
+# ---------------------------------------------------------------------------
+# CLI `precio` y cron
+# ---------------------------------------------------------------------------
+
+# Fuente unica de la linea (docs/DEPLOY.md la copia de aqui).
+LINEA_CRONTAB_PRECIO = (
+    "10 13 * * * /usr/bin/flock -n /tmp/precio-corrida.lock"
+    " docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx"
+    " >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1"
+)
+
+
+def test_linea_crontab_en_deploy():
+    """`docs/DEPLOY.md` trae la linea EXACTA y los previos a instalarla.
+
+    D15: se afirma contra el doc, no contra la constante (y B6: los
+    renglones previos a instalar la linea).
+    """
+    deploy = (RAIZ / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
+    assert LINEA_CRONTAB_PRECIO in deploy
+    assert "10 13 * * *" in deploy
+    assert "flock" in deploy
+    assert "precio-corrida.log" in deploy
+    assert "claves `precio_*" in deploy
+    assert "no pasa el entorno del host" in deploy
+    assert "13:10 UTC" in deploy
+    assert "no toca Amazon" in deploy
+    assert "forma del parche sellada (A.4)" in deploy
+
+
+def test_cli_precio_sin_dsn_fail_closed(monkeypatch, capsys):
+    """Sin `ORBIT_DSN_DECIDE`, `precio` sale 2 sin tocar nada."""
+    from app import cli as cli_mod
+
+    monkeypatch.delenv("ORBIT_DSN_DECIDE", raising=False)
+    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
+    assert codigo == 2
+    assert "ORBIT_DSN_DECIDE" in capsys.readouterr().err
+
+
+def test_cli_precio_despacha_corrida_e_imprime_resumen(monkeypatch, capsys):
+    """`precio --platform` corre por el mismo camino e imprime el final."""
+    from app import cli as cli_mod
+
+    llamadas = {}
+
+    def _falso_correr(conn, platform, **kw):
+        llamadas["platform"] = platform
+        llamadas["kw"] = kw
+        print("listing=1 amazon_mx shadow mantener en_tolerancia")
+        return Resumen(decisiones=1, escritas=0, cerrados={}, huerfanas=0)
+
+    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
+    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
+    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
+    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
+    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
+    assert codigo == 0
+    assert llamadas["platform"] == "amazon_mx"
+    # D14: `correr` recibe los clientes de `_clientes_precio` y el limitador.
+    assert llamadas["kw"]["lector"] == "l"
+    assert llamadas["kw"]["escritor"] == "e"
+    assert llamadas["kw"]["fees"] == "f"
+    assert llamadas["kw"]["limitador"] == "cubo"
+    assert "decisiones=1 escritas=0" in capsys.readouterr().out
+
+
+def test_cli_precio_lock_ocupado_sale_cero(monkeypatch, capsys):
+    """Claim perdido: mensaje y exit 0 (el trabajo ya esta en curso)."""
+    from app import cli as cli_mod
+
+    def _ocioso(*a, **k):
+        raise LockOcupado("precio:amazon_mx en curso (owner otro)")
+
+    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
+    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
+    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
+    monkeypatch.setattr("app.precio.corrida.correr", _ocioso)
+    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
+    assert codigo == 0
+    assert "en curso" in capsys.readouterr().err
+
+
+@_skip_sin_pg
+def test_huerfana_se_cierra_sin_get_y_sale_en_resumen():
+    """La `pendiente` no-reversa amanece `error/huerfana_sin_patch`, cero red."""
+    with _db() as (conn, _dsn):
+        prod = _producto(conn)
+        lid = _listing(conn, prod)
+        _config(conn)
+        _goal(conn, lid, mode="live")
+        dec = _decision_subir(conn, lid)
+        ayer = datetime.now(UTC) - timedelta(days=1)
+        cid = _cambio_con_estado(conn, dec, lid, estado="pendiente", dia=ayer)
+        red = _RedFalsa()
+        res = _corre(conn, red)
+        assert res.huerfanas == 1
+        fila = conn.execute(
+            "SELECT estado, error_code FROM precio_cambio WHERE id = %s", (cid,)
+        ).fetchone()
+        assert tuple(fila) == ("error", "huerfana_sin_patch")
+        assert red.n_get == 0 and red.n_fees == 0 and red.n_patch == 0
+
+
+# ---------------------------------------------------------------------------
+# r1 (BRIEF-r1): correcciones B, mutantes C y bajas K. Rojo primero en tdd.md.
+# ---------------------------------------------------------------------------
+
+
+def _cierra_goal(conn, lid, hoy):
+    """Cierra la vigencia abierta (lo unico que el trigger permite tocar)."""
+    conn.execute(
+        "UPDATE precio_goal SET valid_to = %s WHERE listing_id = %s AND valid_to IS NULL",
+        (hoy, lid),
+    )
+
+
+def _cambio_historia(conn, dec, lid, **kw):
+    """Cambio con fecha pasada que referencia decision de otro listing."""
+    conn.execute("ALTER TABLE precio_cambio DISABLE TRIGGER precio_cambio_coherente")
+    try:
+        return _cambio_con_estado(conn, dec, lid, **kw)
+    finally:
+        conn.execute("ALTER TABLE precio_cambio ENABLE TRIGGER precio_cambio_coherente")
+
+
+def _andamio_hoy(conn, *, sku="SKU-AND", ext="B0A50000AN"):
+    """Listing con goal live + decision de hoy: andamio para sembrar cambios con fecha.
+
+    La decision de hoy lo excluye de la corrida (`decididas`).
+    """
+    lid = _list

[... diff truncado aca: se supero el limite de 60000 caracteres (~60KB) que usa cross-review.ps1 para no saturar el prompt de revision. Si necesitas el diff completo, revisalo a mano con git. ...]
• The diff is truncated at ~60KB. The dump has 1576 lines total. I've seen lines 1-1576 of the dump, which covers the diff up to `test_precio_corrida.py` around line 2000 of 3158. The rest of the test file isn't in the dump. I should verify key dependencies in the real repo:

  1. `test_schema._postgres_obligatorio_ausente` exists.
  2. `app.precio.corrida` exports LockOcupado, Resumen, correr, cambios_previos, freno_por_error, reporte, armar_entrada.
  3. Migrations 0001, 0002, 0028, 0032, 0035, 0039 exist.
  4. `_cambio_historia` disables trigger `precio_cambio_coherente` — check it exists in migration 0039.
  5. `Resumen(decisiones=..., escritas=..., cerrados={}, huerfanas=0)` fields.
  6. `_respuesta_fees` uses `skus[ident]` — KeyError if missing; only used when POST feesEstimate happens.
  7. The `test_linea_crontab_en_deploy` test is not marked `_skip_sin_pg` and reads DEPLOY.md — fine.

  Potential issues to check:
  - In `test_dos_hilos_exactamente_un_patch`: `red.aviso_patch.wait(timeout=120)` — if neither thread ever issues a PATCH (e.g., both fail to get lock), the assert fails. Fine as a test.
  - `_escenario` builds SQL with string concatenation `moneda_sql` — controlled, fine for tests.
  - `_fee_time`: max(fetched_at) — if no ofertas, returns None → red.fee_time = None → `_respuesta_fees` uses `datetime.now(UTC)`. Fine.
  - Race in `_db`: `DROP DATABASE ... WITH (FORCE)` — if admin connection fails before CREATE, admin.close fine.
  - `_goal` CASE WHEN with %s parameter = mode: `go_literal` NULL for shadow. Check whether precio_goal CHECK requires go_literal NOT NULL for live only. Fine.
  - In `test_freno_se_evalua_para_cada_goal`, the second goal listing has no escenario/price_obs, so it would be `no_evaluado` → decisiones=2, escritas=1. OK.
  - `_corre` passes `conn` (autocommit) — claim advisory lock; ok.

  One suspicious thing: `test_reversa_del_dia_consume_cupo` — `_cambio_con_estado(conn, dec, lid, estado="enviado", dia=ahora - timedelta(days=1))` — the `enviado_at` is set to yesterday, but then reversa insert with `enviado_at = ahora`. Fine.

  Another: in `_RedFalsa._handler` PATCH barrier: `if self.n_patch == 1` and `retener_patch` set — in `test_dos_hilos` the main thread waits for aviso_patch then sets retener. OK.

  `_handler` raises AssertionError for unexpected calls — MockTransport handler exceptions propagate. OK.

  Now, the truncated portion (~1150 more lines of the test file) I haven't seen. The prompt says the diff was limited to this file, but truncated at 60KB. I can read the actual file in the repo: tests/test_precio_corrida.py exists (it's HEAD). I could check the rest of the file plus verify dependencies. Let me do quick verification greps:

  - tests/test_schema.py has `_postgres_obligatorio_ausente`
  - migrations files exist
  - app/precio/corrida.py exports
  - DEPLOY.md contains the crontab line
  - trigger `precio_cambio_coherente` exists

  Also check the rest of the test file (lines ~2000-3158) for anything notable. That's substantial. Given the review scope, I should at least skim the remainder for obvious issues. Let me read the actual file's tail and grep dependencies.

    3158 tests/test_precio_corrida.py
migrations/0001_initial.sql
migrations/0002_apply.sql
migrations/0028_estimacion_venta.sql
migrations/0032_spapi_pricing.sql
migrations/0035_spapi_listings_inventario.sql
migrations/0039_precio.sql
• Now read the rest of the test file (lines ~2000-3158), since the diff was truncated. Also verify trigger `precio_cambio_coherente` exists in migration 0039, and DEPLOY.md contains the crontab line. Let me read the tail of the test file.The Grep matched "CREATE TRIGGER" in many files. I need to verify `precio_cambio_coherente` trigger exists in 0039. Let me grep specifically. Also read the rest of the test file (lines 1000-3158). Let me read pages 1000-2000 and 2000-3158.Good. Read the final part 2000-3158.Now I have the whole file. Let me verify a few suspicious points in the actual code:

  1. `test_latido_fase1_late_un_goal`: `_CorteFase1` raised inside `repartir_cupo` — correr would presumably release lock in finally. Fine.

  2. `test_latido_roto_no_tumba_corrida`: monkeypatch `_SQL_LATIDO` to a table name containing the secret, and asserts "s3cr3t-lat5" not in logs. But `UndefinedTable` error message would contain the table name `ads_optimizer_lock_s3cr3t_lat5` — which contains the registered secret substring, so scrub replaces it. OK by design.

  3. `test_dos_hilos_exactamente_un_patch`: `resultados` list shared across threads; appending is thread-safe enough. The main thread waits for `aviso_patch` — set on first PATCH. But wait: both threads call `_corre`; the thread that doesn't get the lock raises LockOcupado quickly. The barrier ensures the winning thread is retained in PATCH until the loser got LockOcupado. Fine.

  4. Potential issue in `_respuesta_fees`: `skus[ident]` KeyError if a fees request arrives with an identifier not in skus — would raise inside handler → error propagates to client code. That only fails the test, fine.

  5. `_fee_time(conn)` returns max(fetched_at) — could be None when no ofertas; `_corre` sets `red.fee_time = None` then, and `_respuesta_fees` falls back to `datetime.now(UTC)`. OK.

  6. `test_config_invalida_sale_2`: `_conexion_decide` returns `(conn, lambda: None)` — conn is the autocommit connection to temp DB; cli main with cap 99 → exit 2. OK.

  7. In `test_escenario_con_importe_no_numerico`: `_cadena(..., costo="doce")` — inserts into componentes JSON (jsonb, string ok). contribucion m="22.50" still. Fine.

  8. `test_huerfana_otras_plataformas_intactas`: inserts precio_cambio with `decision_id` NOT NULL? They pass dec_u — fine.

  9. `test_reversa_confirmada_no_enfria`: reversa insert with `decision_id NULL` — check 0039 precio_cambio.decision_id nullable. In test at line 2002 they insert `VALUES (NULL, ...)`. Need to check the DDL allows NULL decision_id. Verify in 0039.

  10. `_goal` with mode='live' sets go_literal='go-tdd'; shadow → NULL. Check DDL: go_literal probably has CHECK (mode='live') = (go_literal IS NOT NULL). Fine.

  11. Concurrency concern: `_db()` uses `socket.gethostname().lower()` — hostnames may contain chars needing quoting; uses Identifier, fine.

  12. `_RedFalsa._handler` PATCH barrier: `if self.n_patch == 1` — in `test_dos_hilos`, if the losing thread also made a PATCH (shouldn't), the barrier logic only retains first. Fine.

  13. `test_advisory_ajeno_no_espera`: `_hilo` uses the SAME `conn` (the one from `_db`) in a separate thread while... actually main thread isn't using conn during the join. But `correr` on conn in a thread; conn used in main thread before. psycopg connections aren't thread-safe but here only one thread uses it at a time. OK. Also `otra.execute("SELECT pg_advisory_lock(...)")` on autocommit — session-level lock held until otra closes. OK.

  14. `test_error_de_base_en_fase3_aborta_y_libera_lock`: after OperationalError from cambiar_precio, monkeypatch.undo() then `_corre(conn, red)` again — but wait, first corrida may have persisted cotizacion; second run expects (1,1). Note decisiones=1 even though two goals a and b — because first run decided both? First run: fase1 decides and persists decisions for both a and b? Then the error in fase3 aborts. Second run: listings already have decision of today → skipped → decisiones=0? But the test asserts (1,1). Hmm — this suggests first run raised before persisting decisions, and second run... but there are two goals (a and b). Second run gives (1,1)?? Only 1 decision with two goals? Wait, maybe first run persisted one decision (a) before dying in fase3 on a's apply. Then second run: b is pending... Actually the assert is (1,1) meaning one decision and one written. If the first corrida persisted a's decision but died applying, second corrida decides only b → 1/1. Plausible. But ordering — whether a or b is first. Either way 1 decision remains. OK.

  Actually wait — do decisions get persisted in fase1 or fase3? If persisted in fase1 for both, second run would be (0,0). The test expects (1,1), so persistence must be interleaved per listing. Can't verify without reading corrida.py deeply; it's implementation detail and the test passed presumably.

  15. `test_relazar_despues_de_morir_antes_de_persistir` asserts cotizacion count == 2 (reused? no — "reusa y decide" but count==2? "el relanzamiento reusa y decide" — reuse means it should NOT create a second cotizacion... but asserts == 2. Hmm: maybe reuse creates a new row? The docstring says reusa. count==2 suggests one per attempt. Whatever, test code consistent with itself.

  16. Potential real issue: `test_racha_previa_entra_a_la_senal` disables trigger `precio_decision_fecha_utc` — verify it exists in 0039.

  17. `_cierra_goal`: "lo unico que el trigger permite tocar" — UPDATE precio_goal SET valid_to. OK.

  18. Check that `test_linea_crontab_en_deploy` asserts strings in DEPLOY.md — verify those exist (the test presumably passes in CI; but this review is of the test file only, HEAD includes DEPLOY.md changes). Quick grep.

  19. `_cambio_con_estado(..., estado="pendiente")` — the function doesn't handle "pendiente" in if/elif; returns cid after plain insert. Used in test_huerfana_se_cierra with estado="pendiente". Fine.

  20. `reversa` parameter of `_cambio_con_estado` is unused. Minor dead param.

  21. In `test_freno_se_evalua_para_cada_goal`: escritas=1 — listing b has no escenario → no_evaluado, so only 1 patch. decisiones=2. OK.

  22. `_ventas_perdiendo` inserts 75 days × ... plus inventario for 15 days. OK.

  23. Concern: `test_cli_precio_reporte_conexion_caida_sin_traceback` monkeypatches `cli_mod.connect` — verify app/cli.py has `connect` imported at module level. Check.

  24. `test_reporte_es_solo_lectura` sets `default_transaction_read_only = on` on autocommit conn — subsequent statements read-only. `reporte` only reads. OK. But `_cuenta_decisiones` afterwards are reads. OK.

  25. Hidden landmine: `SETTINGS` lacks "precio_cap_amazon_us" — corrida for amazon_us would abort; tests only run amazon_mx. Fine.

  26. `_goal` CASE WHEN %s = 'live' — parameter used in CASE; psycopg fine.

  Let me verify: trigger precio_decision_fecha_utc exists; decision_id nullable in precio_cambio; cli.py has module-level `connect`, `_conexion_decide`, `_clientes_precio`; DEPLOY.md contains the crontab line. Also check `Resumen` has fields lineas/errores as tuples and `cerrados`.

• All dependencies check out: triggers exist, decision_id NULL allowed for reversas, cli has the hooks, DEPLOY.md has the crontab line.

  Now assess candidate findings:

  1. `test_dos_hilos_exactamente_un_patch`: `assert red.aviso_patch.wait(timeout=120)` — if the winning thread never reaches PATCH (e.g. bug), the test blocks up to 120s then fails. Acceptable.

  2. `_cambio_con_estado` has unused param `reversa=False` — dead parameter, minor. NO BLOQUEANTE.

  3. `_respuesta_fees`: `skus[ident]` raises KeyError for unknown identifier — that's intentional strictness. Fine.

  4. `_RedFalsa._handler` barrier: `assert self.retener_patch.wait(timeout=120)` inside handler — assert inside mock; if it times out, AssertionError propagates into client code path — in the two-thread test the main thread sets retener after seeing "ocupado". OK.

  5. `test_huerfana_otras_plataformas_intactas`: inserts reversa-less precio_cambio with platform amazon_us but currency MXN — fine.

  6. `test_racha_previa_entra_a_la_senal` disables trigger `precio_decision_fecha_utc` — verified exists.

  7. `test_saltado_sin_fila_gasta_cupo` and others use `_decision_unica(conn)` — SELECT without ORDER BY with fetchone; single row expected. OK.

  8. One subtle: `test_error_de_base_en_fase3_aborta_y_libera_lock` — after first `_corre` raised mid-fase3, the second `_corre` expects (1,1). This depends on implementation details of when decisions persist. Can't verify cheaply without running. But it's their design; no basis to claim bug.

  9. `_db()` — `DROP DATABASE ... WITH (FORCE)` requires PG13+; repo uses PG16. OK.

  10. `_CONTADOR` itertools.count module-level shared across pytest-xdist? Each worker separate process. db name includes pid+hostname+counter. OK.

  11. `test_cambios_previos...` uses `_decision_subir(conn, lid, mode="shadow")` — decision mode shadow; fine.

  12. `float(self.precio_vivo)` in ofertas body — ListingPrice.Amount as float; the parser presumably accepts. OK.

  13. In `test_umbral_fuera_de_cota_aborta_al_arrancar` after ValueError they call `_config(conn)` again and run — two config_version rows; leer_config presumably takes latest. OK.

  14. `test_latido_fase1_late_un_goal`: `repartir_cupo` raising `_CorteFase1` — correr must release the lock in finally; not asserted but fine. Note: `latidos == ["amazon_mx", "amazon_mx"]` — exactly 2 heartbeats in fase 1. If correr also latido at claim time, count differs. Whatever — passed presumably.

  15. Potential flakiness: `test_dos_hilos_exactamente_un_patch` — both threads call `_corre(otro, red, ...)`; `_corre` calls `_fee_time(conn)` on each thread's own conn — fine. `_RedFalsa` handler shared between threads: `self.n_patch += 1` not atomic but GIL makes += on int... not atomic actually, but race benign. Also `red.fee_time` set in `_corre` before correr — both threads set same value. OK.

  16. Wait — one thing in `_corre` in the two-thread test: `red.fee_time = _fee_time(conn)` mutates shared `red` from both threads; benign.

  17. `test_cli_precio_conexion_caida_sin_traceback`: `register_secret("s3cr3t")` — register_secret global state; called in multiple tests with different secrets; accumulation fine.

  18. Actual potential finding: `_goal` uses `go_literal = 'go-tdd'` only when mode='live' via CASE — check 0039 requires go_literal for live. Verified pattern exists presumably. Not a finding.

  19. `test_dia_de_estado_es_utc` does `conn.execute("SET TIME ZONE 'America/Mexico_City'")` on the temp conn; connection dropped after. OK.

  20. `_ventas_perdiendo` uses `datetime.now(UTC)` for observed_at with event_date past — append-only metric rule irrelevant in seeds.

  21. Hmm, `test_huerfana_no_toca_reversas` inserts reversa with enviado_at = ahora - 1 day, estado pendiente. `_cambio_con_estado(conn, dec, lid, estado="enviado", dia=ahora)` — enviado today, then updated to confirmado. The corrida should skip. OK.

  22. In `_RedFalsa.__init__` default `patch=(202, None)` mutable default? Tuple immutable. OK.

  23. `_reloj_falso` returns clock starting at 1000.0 — CuboTasa(capacidad=1, tasa=0.5) with fake sleep; fine.

  One more genuine check: `test_reservar_cap_cero_no_inserta` calls `cuota_mod.reservar(conn, platform="amazon_us", cap=2, extra=2)` — extra param exists in cuota.reservar? Verify signature quickly. Also `Resumen` fields: decisiones, escritas, cerrados, huerfanas, errores, lineas. Verify in corrida.py.

  Also check `from test_schema import _postgres_obligatorio_ausente` — tests dir import works because conftest/rootdir on sys.path (pytest rootdir inserts tests dir?). Actually `from test_schema import ...` works if tests/ is on sys.path (rootdir with no __init__.py → pytest inserts test file's dir). tests/ has no __init__.py listed; existing tests presumably import similarly. test_target_margen.py also imports `_postgres_obligatorio_ausente`? Grep showed the def in both test_schema.py and test_target_margen.py. Let me check how other test files import it — if test_target_margen.py defines its own, maybe cross-file import `from test_schema import` is used elsewhere. Check quickly.

• All dependencies verified. Now compile findings. Genuine candidates:

  1. `_cambio_con_estado(..., reversa=False)` — parametro `reversa` declarado y nunca usado (muerto). NO BLOQUEANTE.

  2. `test_dos_hilos_exactamente_un_patch`: posible bloqueo de hasta 120s si la barrera no se arma; ademas el hilo perdedor podria no levantar LockOcupado si... no, esta bien disenado. El wait del main thread a aviso_patch con timeout=120: si el ganador muere antes del PATCH, falla con assert falso tras 120s — aceptable. NO BLOQUEANTE como mucho; no lo reportaria? Es diseno deliberado anti-intermitencia. Skip.

  3. `_respuesta_fees` usa floats en el JSON de respuesta (`Amount: float(precio)`) — float en la respuesta falsa; si el parser de produccion convierte a Decimal via str, ok. No es un problema del test. Skip.

  4. `test_error_de_base_en_fase3_aborta_y_libera_lock` y `test_fase1_error_de_base_aborta_y_libera_lock` — "libera el lock" se prueba indirectamente re-corriendo. OK.

  5. `test_latido_fase1_late_un_goal`: afirma exactamente 2 latidos; `_CorteFase1` sale de repartir_cupo — si correr no libera el lock en ese camino, la DB temporal se tira igual. OK.

  6. Race potencial real: en `_corre`, `red.fee_time = _fee_time(conn)` — en el test de dos hilos, ambos hilos ejecutan `_corre` con la MISMA `red`; `_fee_time` se consulta por conexion separada, mismo valor. Benigno.

  7. `test_advisory_ajeno_no_espera` usa la misma `conn` en un hilo mientras el hilo principal hace join — psycopg connection no es thread-safe pero solo la usa el hilo. OK.

  8. Hallazgo posible: `test_huerfana_otras_plataformas_intactas` — `_RedFalsa` con default `patch=(202, None)`, y la corrida aplica el goal live de datos → PATCH a amazon_mx... wait decisiones=1, escritas=1. El goal amazon_us no se decide (corrida es amazon_mx). OK.

  9. `_db` — si `CREATE DATABASE` falla, `conn` queda None y el finally hace DROP IF EXISTS; ok. Si `psycopg.connect(dsn...)` (admin) falla, no hay try — excepcion propaga sin admin.close... en realidad admin no se creo. OK.

  10. El `assert` dentro de `_handler` (`assert self.retener_patch.wait(timeout=120)`) corre dentro del hilo del cliente; si expira, el error viaja por el stack del cliente y podria enmascararse como error de la corrida en vez de fallo del test. Menor. NO BLOQUEANTE, pero es ruido; los tests capturan excepciones en `errores`... en test_dos_hilos, el hilo ganador: si retener expira (120s), AssertionError dentro de correr — correr podria tragarlo? No: errores de publicacion se registran... Podria convertir un timeout de barrera en un comportamiento confuso. Pero timeout 120s es holgado. Marginal.

  11. `test_linea_crontab_en_deploy` no tiene `_skip_sin_pg` y no necesita DB — bien.

  12. `test_reporte_es_solo_lectura` — `SET default_transaction_read_only = on` en sesion autocommit afecta las transacciones siguientes; luego `_cuenta_decisiones` (SELECT) ok. Bien.

  13. Uno real: en `test_fase1_aisla_error_de_publicacion`, `_revienta` lanza ValueError en la primera llamada a `evaluar_senal` — depende del orden de evaluacion (a antes que b). Si la corrida ordena por prioridad y ambos tienen misma prioridad... el orden podria ser por listing_id; a < b. Asume orden. Si el orden cambiara, el test seguiria pasando (filas se verifican por listing). "fila_a == no_evaluado" — si el primero evaluado fuera b, entonces b recibiria el ValueError y fila_b seria no_evaluado → el test falla. Depende del orden deterministico de fase 1 (probablemente listing_id, y a se crea primero). Riesgo bajo de fragilidad. NO BLOQUEANTE.

  14. `test_latido_fase1_late_un_goal`: afirma `latidos == ["amazon_mx", "amazon_mx"]` — exactamente 2. Si el claim late tambien al tomarse o al cerrar, falla. Es un pin deliberado. OK.

  15. Riesgo de calidad: el archivo es de 3158 lineas, con andamiaje extenso; hay tests que deshabilitan triggers (`DISABLE TRIGGER`) para sembrar historia — legitimo pero frágil ante renombres. No es hallazgo accionable fuerte.

  16. `_goal`: cuando `hasta` se pasa y `desde` no, base = hoy - 30... y valid_to=hoy. OK.

  17. En `test_goal_cerrado_hoy_no_se_decide`, `_goal(conn, lid, mode="live", hasta=hoy)` — valid_to = hoy → la corrida de hoy no lo decide. Aserta (0,0). OK.

  18. `test_reversa_del_dia_consume_cupo` — la reversa se inserta con decision_id omitido en el INSERT (columnas no incluyen decision_id) y es_reversa=true → CHECK ok (es_reversa → decision_id IS NULL). Verificado contra 0039 linea 555. Bien.

  19. En `test_cupo_descuenta_reversas_al_repartir` (linea ~1998): INSERT con `decision_id NULL` explicito y es_reversa=true. OK.

  20. `test_freno_tres_dias_de_error_unidad` — usa `desde=hoy - timedelta(days=1)` para B3 y espera False aunque hay errores en -1..-3... wait: errors at atraso 1,2,3; desde = hoy-1 → la ventana [desde, hoy) incluye solo dia -1? Entonces solo 1 error → no frena. El comentario dice "con goal nuevo posterior a la racha se evalua normal". Coherente.

  En general el archivo es solido. Hallazgos NO BLOQUEANTES:

  1. Parametro `reversa` muerto en `_cambio_con_estado` (linea 942): nunca se usa; ruido.
  2. `test_fase1_aisla_error_de_publicacion` depende implicitamente del orden de evaluacion (el primer goal evaluado recibe el ValueError); si fase 1 cambia de orden, el test falla aunque el comportamiento sea correcto. Fragilidad.
  3. El `assert` dentro de `_RedFalsa._handler` (barrera de PATCH) corre dentro del stack de `correr`: si la barrera expira, la AssertionError puede ser tragada/registrada por la propia corrida como error de aplicacion en vez de fallar el test limpiamente. Verificar: correr captura excepciones de cambiar_precio? Si las captura, un timeout de barrera se convierte en "error" y el test seguiria... En test_dos_hilos, si retener expira, el hilo ganador registraria error, terminaria con escritas=0, y los asserts finales (hechos == [(1,1),"ocupado"]) fallarian. OK, falla igual. Marginal.

  4. `_respuesta_fees` hace `import json as _json` dentro de la funcion — estilo; otros archivos tambien. No.

  5. Riesgo de datos: `test_dos_hilos` usa `sorted(..., key=str)` para comparar resultados — ok.

  6. `test_reporte_es_solo_lectura` muta la sesion (`SET default_transaction_read_only = on`) — si `_db` reusara conexion habria fuga, pero no reusa. OK.

  7. Uno mas: `_RedFalsa._handler` devuelve ASIN por defecto "B0A5000001" cuando la ruta no termina en "/offers" — silencioso; si la URL cambiara de forma, el cuerpo llevaria un ASIN equivocado y el test podria pasar/fallar por la razon incorrecta. Marginal.

  8. `test_cli_precio_reporte_conexion_caida_sin_traceback` monkeypatchea `cli_mod.connect` — verificado que cli.py importa `connect` a nivel modulo (linea 50). OK.

  Tambien debo considerar: no ejecute la suite (requiere Postgres). No puedo afirmar que pasa. Lo digo.

  VERIFICAR candidatos: nada critico que no haya podido verificar.

  Una cosa mas: el test `test_umbral_fuera_de_cota_aborta_al_arrancar` con `precio_cap_amazon_mx: 99` — fuera de cota 99? cap max seria... no se cual es la cota; VERIFICAR? El comportamiento esta en app/precio/config o corrida. Podria verificar rapidamente la cota del cap. Grep "precio_cap" en app/precio.

• Hallazgo real: los tests `test_umbral_fuera_de_cota_aborta_al_arrancar` y `test_umbral_ausente_aborta_al_arrancar` usan `pytest.raises(ValueError, ...)` alrededor de `_corre`, pero `correr` envuelve el `ValueError` de validacion en `ConfigInvalida` (corrida.py:1432-1433). ¿Es `ConfigInvalida` subclase de `ValueError`? Si no, los tests fallarian... pero presumiblemente pasan en CI. Verifico ConfigInvalida.

• `ConfigInvalida(ValueError)` — pytest.raises(ValueError) lo atrapa. Bien.

  Ya verifique todo lo critico. Compilo hallazgos. Honestamente, el archivo es bastante solido. Hallazgos menores:

  1. `_cambio_con_estado` parametro `reversa=False` muerto (nunca usado en el cuerpo ni en llamadas con valor True... espera: `test_reversa_del_dia_consume_cupo` no usa `_cambio_con_estado` para la reversa, inserta directo. Nadie pasa reversa=True). NO BLOQUEANTE.

  2. `test_fase1_aisla_error_de_publicacion` y `test_parche_sin_sellar_persiste_filas_sin_reintentar` dependen del orden de evaluacion/aplicacion: el saboteador golpea "la primera llamada"; si fase 1/3 cambia el orden de iteracion, el test falla sin que el comportamiento este roto. Fragilidad. NO BLOQUEANTE.

  3. En `_RedFalsa._handler`, la barrera `assert self.retener_patch.wait(timeout=120)` corre dentro del stack de la corrida: si expira, la AssertionError puede quedar registrada como error de corrida (scrubbado) en vez de fallar limpio — en `test_dos_hilos` el fallo igual se ve en los asserts finales. NO BLOQUEANTE.

  4. `_RedFalsa` contadores `n_get/n_patch/n_fees` mutados desde dos hilos sin lock: `+=` en int no es atomico; en el test de dos hilos solo un hilo hace PATCH, asi que benigno hoy, pero la clase se presta a reuso concurrente. NO BLOQUEANTE (bien menor).

  5. No puedo afirmar que la suite pase: no la ejecute (requiere Postgres). VERIFICAR? No es un hallazgo del diff. Lo menciono como nota, no hallazgo.

  Tambien: `test_dos_hilos_exactamente_un_patch` — `red.fee_time` se fija dentro de `_corre` desde ambos hilos sobre la misma `red` (mutacion compartida); benigno (mismo valor).

  Nota: los asserts de `sorted(..., key=str)` en test_dos_hilos: "ocupado" vs (1,1) — str((1,1)) = "(1, 1)" vs "ocupado"; sorted por str: "(1, 1)" < "ocupado" ya que "(" (40) < "o". Resultado [(1,1), "ocupado"]. Correcto.

  Respuesta final: lista numerada, NO BLOQUEANTE todo, espanol sin acentos en lo posible.

To resume this session: kimi -r session_e1ec5aff-73ef-4feb-aaeb-e6e3cf5d8344


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=53c6067f55ed09dd01793151d1a3f5e2cbd5c38e archivos=tests/test_precio_corrida.py inicio=2026-09-18T23:25:55Z fin=2026-09-18T23:32:32Z exit=0
