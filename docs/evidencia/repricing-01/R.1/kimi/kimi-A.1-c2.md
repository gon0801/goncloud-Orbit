=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): tests/test_precio_goals.py
Tamano del diff: 53799 caracteres
==> Invocando kimi de forma no interactiva (tope: 900 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Leo el diff a revisar.

• Necesito el resto del diff (lineas 1001-1664) para ver el final del archivo de tests.

• El Read devolvio el principio porque no pase el offset; ahora leo las lineas 1001-1664 para ver el final del test.

• El problema es que no pase `line_offset`; lo paso ahora explicitamente para leer desde la linea 1001.

• El Read no me respeta el offset sin el parametro; uso Bash `tail` para ver las ultimas ~664 lineas del diff.

• Ya tengo el diff completo. Ahora verifico en el repo las dependencias que el diff no muestra: existencia de los modulos referenciados, el helper `test_schema`, y el esquema de `config_version`.

• Verifico que los constraints que los tests asumen existen en la migracion 0039 y que variables de entorno lee el tool.

• El esquema cuadra. Ahora la prueba de fuego: corro el archivo de tests.

• Los 48 tests pasan contra Postgres real. Una ultima verificacion: que campos del escenario lee el tool, porque el seed mete el mismo valor en `contribucion` y `contribucion_pct`.

• Revision completa. Verifique en el repo todo lo que el diff no muestra: `app/precio/goals_write.py`, `tools/precio_goal.py`, `tests/test_schema.py` (`_postgres_obligatorio_ausente`), las migraciones 0028/0039 (los constraints `precio_goal_banda`, `precio_goal_live_exige_go` y el trigger de cierre existen en `migrations/0039_precio.sql`), y que `config_version` no tiene UNIQUE en `label` (asi que insertar dos configs en un test es valido; la vigente es la mas reciente). Ademas corri el archivo: **48 passed, 0 skipped** contra Postgres real. No hay bugs ni riesgos de seguridad; solo observaciones menores de calidad:

  1. (baja) `_tool` invoca `subprocess.run` sin `timeout` (tests/test_precio_goals.py:408). Si el tool se cuelga en algun entorno, el suite entero se cuelga sin diagnostico. Un `timeout=60` daria un fallo legible.

  2. (baja) `test_huella_estable_entre_dry_runs` (l.673) compara el stdout COMPLETO entre dos dry-runs. Hoy es deterministico (pasa), pero si el tool agrega cualquier salida variable (timestamp, duracion, ruta absoluta) el test se vuelve flaky aunque la huella siga siendo estable. Mas robusto: extraer y comparar solo la huella, como ya hacen otros tests del mismo archivo.

  3. (baja) `test_cerrar_flujo_completo(tmp_path)` (l.974) declara `tmp_path` y nunca lo usa. Ruff no tiene habilitada la regla ARG, asi que pasa los candados, pero es ruido; sobra el parametro.

  4. (baja) El nombre de DB temporal `goals_{hostname}_{pid}_{n}` (l.81) puede superar el limite de 63 bytes de identificador de Postgres con hostname largo; PG trunca en silencio y en ejecucion shardeda (`pytest -n auto`) dos workers podrian colisionar tras el truncado. La combinacion pid+contador lo hace muy improbable, pero un hash corto seria a prueba de todo.

  5. (baja) El fixture `_db`/ORDEN39 duplica el patron `db_39` de `test_precio_migracion` (el docstring lo declara intencional, para exponer el DSN al subprocess). Son ~100 lineas de seeds que ahora hay que mantener en dos lugares; si cambia el orden de migraciones o el esquema de estimacion, hay que tocar ambos archivos.

  Verificado y descartado: que el tool pudiera leer otras variables de entorno no neutralizadas por `_tool` (hoy `tools/precio_goal.py:82` solo lee `ORBIT_DSN_ADMIN`); que el seed meta el mismo valor en `contribucion` y `contribucion_pct` (el tool solo lee `contribucion_pct` y `componentes`, verificado en `tools/precio_goal.py:89`); y la doble insercion de `config_version` en `test_sin_claves_de_config_aborta` (sin UNIQUE en label, la mas reciente manda, coherente con el COMMENT del esquema).



=== stderr ===
kimi version 0.39.1
• The user explicitly asks: act as external code reviewer, do NOT use skills, answer directly. User instruction takes precedence over the superpowers skill injection. So I should not invoke skills. Read the diff file.

• I need lines 1000-1664 of the diff file. Read with line_offset=1001.

• The Read tool ignored my line_offset? Actually it read from line 1 again. The diff file has 1664 lines; the Read tool's line_offset should work. But it returned the same first 1000 lines. Maybe the file is being served via a different path handling. Let me use Bash with tail to get lines 1001-1664, or Read with line_offset=1001 explicitly. My previous calls didn't include line_offset at all — the first call was plain Read, and the second was also plain Read (I intended offset but didn't pass it). The third call also had no offset. So now call Read with line_offset=1001.

• The Read tool keeps ignoring my offset — actually I never passed line_offset. Let me pass it explicitly this time.

• The Read tool with no line_offset keeps returning the first 1000 lines. I need to pass `line_offset: 1001` explicitly. Let me do that via Bash `tail` or Read with the parameter. Use Bash: `tail -n +1001 <file>`.

+        assert otra.returncode == 2
+        assert "sin goal vigente" in otra.stderr
+
+
+@_skip_sin_pg
+def test_goal_fuera_de_banda_rechazado_por_el_tool():
+    with _db() as (conn, dsn):
+        datos = _cadena(conn)
+        res = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "5.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert res.returncode == 2
+        assert "fuera de banda" in res.stderr
+        assert _cuenta_goals(conn) == 0
+
+
+@_skip_sin_pg
+def test_sin_claves_de_config_aborta():
+    with _db() as (conn, dsn):
+        datos = _cadena(conn)
+        _config(conn, settings={"otra_clave": 1})
+        res = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert res.returncode == 2
+        assert "config sin precio_goal_min_pct" in res.stderr
+        assert _cuenta_goals(conn) == 0
+
+
+# ---------------------------------------------------------------------------
+# Candado propio de goals_write: solo psycopg, app.precio.* y stdlib
+# ---------------------------------------------------------------------------
+
+_PERMITIDOS_GOALS_WRITE = ("psycopg", "app.precio.")
+
+
+def _imports_goals_write(path: Path) -> set[str]:
+    importados: set[str] = set()
+    arbol = ast.parse(path.read_text(encoding="utf-8"))
+
+    def _visitar(nodos: list) -> None:
+        for nodo in nodos:
+            if isinstance(nodo, ast.Import):
+                importados.update(a.name for a in nodo.names)
+            elif isinstance(nodo, ast.ImportFrom):
+                if nodo.module and nodo.level == 0:
+                    importados.add(nodo.module)
+                    importados.update(f"{nodo.module}.{a.name}" for a in nodo.names)
+                elif nodo.level and nodo.level >= 1:
+                    importados.add(f"<relativo-nivel-{nodo.level}>")
+            else:
+                _visitar(list(ast.iter_child_nodes(nodo)))
+
+    _visitar(arbol.body)
+    return importados
+
+
+def _fugas_imports_goals_write(path: Path) -> list[str]:
+    import sys as _sys
+
+    return sorted(
+        i
+        for i in _imports_goals_write(path)
+        if i.split(".")[0] not in _sys.stdlib_module_names
+        and not any(i == p or i.startswith(p) for p in _PERMITIDOS_GOALS_WRITE)
+    )
+
+
+def test_goals_write_solo_importa_psycopg_precio_y_stdlib():
+    fugas = _fugas_imports_goals_write(RAIZ / "app" / "precio" / "goals_write.py")
+    assert not fugas, f"goals_write.py importa fuera de lo permitido: {fugas}"
+
+
+def test_candado_imports_caza_fuga_sembrada(tmp_path):
+    (tmp_path / "goals_write.py").write_text(
+        (RAIZ / "app" / "precio" / "goals_write.py").read_text(encoding="utf-8")
+        + "\nimport httpx  # fuga sembrada\n",
+        encoding="utf-8",
+    )
+    assert _fugas_imports_goals_write(tmp_path / "goals_write.py") == ["httpx"]
+
+
+# ---------------------------------------------------------------------------
+# Ronda 1 (auditoria del lead + cruzada): mutantes del lead y comportamiento
+# ---------------------------------------------------------------------------
+
+
+@_skip_sin_pg
+def test_banda_bordes_inclusivos_se_siembran():
+    """L4: 10.00 y 60.00 entran (BETWEEN 0.10 AND 0.60)."""
+    from app.precio.goals_write import sembrar_goal
+
+    with _db() as (conn, _dsn):
+        a = _cadena(conn, sku="SKU-L4-A", ext="ASIN-L4-A")
+        b = _cadena(conn, sku="SKU-L4-B", ext="ASIN-L4-B")
+        gid_a = sembrar_goal(
+            conn,
+            listing_id=a["listing"],
+            platform="amazon_mx",
+            goal_pct="10.00",
+            mode="shadow",
+            go_literal=None,
+        )
+        gid_b = sembrar_goal(
+            conn,
+            listing_id=b["listing"],
+            platform="amazon_mx",
+            goal_pct="60.00",
+            mode="shadow",
+            go_literal=None,
+        )
+        assert gid_a != gid_b
+        assert _cuenta_goals(conn) == 2
+
+
+@_skip_sin_pg
+def test_banda_bordes_inclusivos_en_el_tool():
+    """L4b: el dry-run acepta 10.00 y 60.00 (con salto confirmado)."""
+    with _db() as (conn, dsn):
+        a = _cadena(conn, sku="SKU-L4-A", ext="ASIN-L4-A")
+        b = _cadena(conn, sku="SKU-L4-B", ext="ASIN-L4-B")
+        for datos, goal in ((a, "10.00"), (b, "60.00")):
+            res = _tool(
+                "--listing-id",
+                str(datos["listing"]),
+                "--platform",
+                "amazon_mx",
+                "--goal-pct",
+                goal,
+                "--mode",
+                "shadow",
+                "--confirmar-salto",
+                str(datos["listing"]),
+                dsn=dsn,
+            )
+            assert res.returncode == 0, res.stderr
+            assert "fuera de banda" not in res.stderr
+
+
+@_skip_sin_pg
+def test_huella_ata_el_modo():
+    """L5: la huella de un dry-run shadow no autoriza un go live."""
+    with _db() as (conn, dsn):
+        datos = _cadena(conn)
+        seco = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert seco.returncode == 0, seco.stderr
+        huella = seco.stdout.split("huella: ")[1].split()[0]
+        go = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "live",
+            "--acepto-mutacion-real",
+            "--huella",
+            huella,
+            "--go",
+            "enciende",
+            dsn=dsn,
+        )
+        assert go.returncode == 2
+        assert "huella" in go.stderr
+        assert _cuenta_goals(conn) == 0
+
+
+@_skip_sin_pg
+def test_huella_ata_el_goal():
+    """L5b: la huella de un dry-run al 30.00 no autoriza un go al 50.00."""
+    with _db() as (conn, dsn):
+        datos = _cadena(conn)
+        seco = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert seco.returncode == 0, seco.stderr
+        huella = seco.stdout.split("huella: ")[1].split()[0]
+        go = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "50.00",
+            "--mode",
+            "shadow",
+            "--acepto-mutacion-real",
+            "--huella",
+            huella,
+            dsn=dsn,
+        )
+        assert go.returncode == 2
+        assert "huella" in go.stderr
+        assert _cuenta_goals(conn) == 0
+
+
+@_skip_sin_pg
+def test_salto_hacia_abajo_tambien_aborta():
+    """L10: P* 43 % abajo de P_actual aborta sin --confirmar-salto."""
+    with _db() as (conn, dsn):
+        datos = _cadena(conn)
+        res = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "10.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert res.returncode == 2
+        assert "25" in res.stderr
+        assert "P*=66.07 MXN" in res.stdout
+        assert _cuenta_goals(conn) == 0
+
+
+@_skip_sin_pg
+def test_cerrar_elige_el_vigente_entre_cerrados():
+    """L11: con un goal viejo ya cerrado, --cerrar cierra el vigente."""
+    from app.precio.goals_write import cerrar_goal, sembrar_goal
+
+    with _db() as (conn, _dsn):
+        datos = _cadena(conn)
+        viejo = sembrar_goal(
+            conn,
+            listing_id=datos["listing"],
+            platform="amazon_mx",
+            goal_pct="30.00",
+            mode="shadow",
+            go_literal=None,
+        )
+        cerrar_goal(conn, listing_id=datos["listing"], platform="amazon_mx")
+        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
+        nuevo = conn.execute(
+            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
+            " valid_from, creado_por, go_literal)"
+            " VALUES (%s, 'amazon_mx', 0.35, 'shadow', %s, 't', NULL) RETURNING id",
+            (datos["listing"], hoy - timedelta(days=10)),
+        ).fetchone()[0]
+        assert cerrar_goal(conn, listing_id=datos["listing"], platform="amazon_mx") == nuevo
+        estados = dict(conn.execute("SELECT id, valid_to FROM precio_goal").fetchall())
+        assert estados[viejo] is not None
+        assert estados[nuevo] is not None
+
+
+@_skip_sin_pg
+def test_huella_sin_acepto_tambien_aborta():
+    """L12: --huella sin --acepto-mutacion-real es dry-run enganoso."""
+    with _db() as (conn, dsn):
+        datos = _cadena(conn)
+        res = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            "--huella",
+            "cualquiera",
+            dsn=dsn,
+        )
+        assert res.returncode == 2
+        assert "acepto-mutacion-real" in res.stderr
+        assert _cuenta_goals(conn) == 0
+
+
+@_skip_sin_pg
+def test_r1_siembra_con_vigente_aborta_antes_de_la_huella():
+    """R1: el dry-run ve el goal vigente y aborta sin huella."""
+    from app.precio.goals_write import sembrar_goal
+
+    with _db() as (conn, dsn):
+        datos = _cadena(conn)
+        sembrar_goal(
+            conn,
+            listing_id=datos["listing"],
+            platform="amazon_mx",
+            goal_pct="30.00",
+            mode="shadow",
+            go_literal=None,
+        )
+        res = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "40.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert res.returncode == 2
+        assert "ya tiene goal vigente" in res.stderr
+        assert "--cerrar" in res.stderr
+        assert "huella: " not in res.stdout
+        assert _cuenta_goals(conn) == 1
+
+
+@_skip_sin_pg
+def test_r1_csv_con_vigente_dice_la_linea(tmp_path):
+    """R1 en lote: la fila con vigente aborta con su linea, antes de la huella."""
+    from app.precio.goals_write import sembrar_goal
+
+    with _db() as (conn, dsn):
+        a = _cadena(conn, sku="SKU-R1-A", ext="ASIN-R1-A")
+        b = _cadena(conn, sku="SKU-R1-B", ext="ASIN-R1-B")
+        sembrar_goal(
+            conn,
+            listing_id=b["listing"],
+            platform="amazon_mx",
+            goal_pct="30.00",
+            mode="shadow",
+            go_literal=None,
+        )
+        csv = tmp_path / "lote.csv"
+        csv.write_text(
+            "listing_id,platform,goal_pct\n"
+            f"{a['listing']},amazon_mx,30.00\n"
+            f"{b['listing']},amazon_mx,40.00\n",
+            encoding="utf-8",
+        )
+        res = _tool("--csv", str(csv), "--mode", "shadow", dsn=dsn)
+        assert res.returncode == 2
+        assert "ya tiene goal vigente" in res.stderr
+        assert "nea 3" in res.stderr
+        assert "huella: " not in res.stdout
+        assert _cuenta_goals(conn) == 1
+
+
+@_skip_sin_pg
+def test_r2_mismo_dia_utc_dice_que_espere_manana():
+    """R2: sembrar, cerrar y resembrar el mismo dia UTC no habla de vigente."""
+    from app.precio.goals_write import PrecioGoalInvalido, cerrar_goal, sembrar_goal
+
+    with _db() as (conn, _dsn):
+        datos = _cadena(conn)
+        sembrar_goal(
+            conn,
+            listing_id=datos["listing"],
+            platform="amazon_mx",
+            goal_pct="30.00",
+            mode="shadow",
+            go_literal=None,
+        )
+        cerrar_goal(conn, listing_id=datos["listing"], platform="amazon_mx")
+        with pytest.raises(PrecioGoalInvalido, match="dia siguiente"):
+            sembrar_goal(
+                conn,
+                listing_id=datos["listing"],
+                platform="amazon_mx",
+                goal_pct="40.00",
+                mode="shadow",
+                go_literal=None,
+            )
+        assert _cuenta_goals(conn) == 1
+
+
+def test_k7_no_positivo_separado_de_fraccion():
+    """K7: -5 y 0 dicen «no positivo»; 0.30 dice «parece fraccion»."""
+    from app.precio.goals_write import PrecioGoalInvalido, fraccion_desde_porcentaje
+
+    with pytest.raises(PrecioGoalInvalido, match="no positivo"):
+        fraccion_desde_porcentaje("-5")
+    with pytest.raises(PrecioGoalInvalido, match="no positivo"):
+        fraccion_desde_porcentaje("0")
+    with pytest.raises(PrecioGoalInvalido, match="parece fracci"):
+        fraccion_desde_porcentaje("0.30")
+
+
+@_skip_sin_pg
+def test_g1_goal_y_m_actual_en_por_ciento():
+    """G1: `contribucion_pct` esta en por ciento; el plan imprime goal y
+    m_actual en la misma unidad (por ciento, dos decimales)."""
+    with _db() as (conn, dsn):
+        datos = _cadena(conn, sku="SKU-G1", ext="ASIN-G1", m="36.6379")
+        res = _tool(
+            "--listing-id",
+            str(datos["listing"]),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert res.returncode == 0, res.stderr
+        esperada = (
+            f"[plan] siembra listing={datos['listing']} platform=amazon_mx"
+            f" goal=30.00% m_actual=36.64% P*={P_ESTRELLA_30} MXN P_actual=116.00 MXN"
+        )
+        assert esperada in res.stdout
+
+
+@_skip_sin_pg
+def test_g3_confirmar_salto_es_por_listing(tmp_path):
+    """G3: confirmar un salto no autoriza el salto del otro listing del lote."""
+    with _db() as (conn, dsn):
+        a = _cadena(conn, sku="SKU-G3-A", ext="ASIN-G3-A")
+        b = _cadena(conn, sku="SKU-G3-B", ext="ASIN-G3-B")
+        csv = tmp_path / "lote.csv"
+        csv.write_text(
+            "listing_id,platform,goal_pct\n"
+            f"{a['listing']},amazon_mx,55.00\n"
+            f"{b['listing']},amazon_mx,55.00\n",
+            encoding="utf-8",
+        )
+        solo_uno = _tool(
+            "--csv",
+            str(csv),
+            "--mode",
+            "shadow",
+            "--confirmar-salto",
+            str(a["listing"]),
+            dsn=dsn,
+        )
+        assert solo_uno.returncode == 2
+        assert str(b["listing"]) in solo_uno.stderr
+        assert _cuenta_goals(conn) == 0
+        desconocido = _tool(
+            "--csv",
+            str(csv),
+            "--mode",
+            "shadow",
+            "--confirmar-salto",
+            "999999",
+            dsn=dsn,
+        )
+        assert desconocido.returncode == 2
+        assert "999999" in desconocido.stderr
+        los_dos = _tool(
+            "--csv",
+            str(csv),
+            "--mode",
+            "shadow",
+            "--confirmar-salto",
+            str(a["listing"]),
+            "--confirmar-salto",
+            str(b["listing"]),
+            dsn=dsn,
+        )
+        assert los_dos.returncode == 0, los_dos.stderr
+        assert "huella: " in los_dos.stdout
+
+
+@_skip_sin_pg
+def test_g4_aviso_con_motivo_si_falta_p_estrella():
+    """G4: con escenario pero sin P* (componente faltante), el aviso sale
+    y dice por que no se evalua el salto."""
+    with _db() as (conn, dsn):
+        prod = _producto(conn, sku="SKU-G4")
+        listing = _listing(conn, prod, ext="ASIN-G4", sku="SKU-G4")
+        _config(conn)
+        marca = datetime.now(UTC)
+        oferta = _oferta(
+            conn,
+            listing,
+            ext="ASIN-G4",
+            sku="SKU-G4",
+            evento="oferta-G4",
+            fetched=marca,
+            observed=marca,
+        )
+        fee = _fee(
+            conn,
+            oferta,
+            listing,
+            ext="ASIN-G4",
+            sku="SKU-G4",
+            evento="fee-G4",
+            fetched=marca,
+            observed=marca,
+        )
+        pol = _politica(conn)
+        costo = _costo(conn, prod)
+        run_id, validada_en = _run(conn)
+        _escenario(
+            conn,
+            listing,
+            oferta,
+            fee,
+            pol,
+            costo,
+            run_id,
+            validada_en,
+            ext="ASIN-G4",
+            sku="SKU-G4",
+            m="24.00",
+            evento="esc-G4",
+            sin_componente="isr",
+        )
+        res = _tool(
+            "--listing-id",
+            str(listing),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert res.returncode == 0, res.stderr
+        assert "m_actual=24.00%" in res.stdout
+        assert "P*=sin_escenario" in res.stdout
+        assert "aviso:" in res.stdout
+        assert "referencia_sin_derivar" in res.stdout
+
+
+@_skip_sin_pg
+def test_s1_sku_sin_publicacion_aborta():
+    """S1: --sku que no mapea a nada aborta."""
+    with _db() as (conn, dsn):
+        _config(conn)
+        res = _tool(
+            "--sku",
+            "SKU-NADIE",
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert res.returncode == 2
+        assert "sin publicaci" in res.stderr
+        assert _cuenta_goals(conn) == 0
+
+
+@_skip_sin_pg
+def test_s1_sku_ambiguo_aborta_con_candidatos():
+    """S1: --sku que mapea a dos publicaciones aborta con los listing_id."""
+    with _db() as (conn, dsn):
+        _config(conn)
+        prod_a = _producto(conn, sku="SKU-DUP-A")
+        lid_a = _listing(conn, prod_a, ext="ASIN-DUP-A", sku="SKU-DUP")
+        prod_b = _producto(conn, sku="SKU-DUP-B")
+        lid_b = _listing(conn, prod_b, ext="ASIN-DUP-B", sku="SKU-DUP")
+        res = _tool(
+            "--sku",
+            "SKU-DUP",
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert res.returncode == 2
+        assert str(lid_a) in res.stderr
+        assert str(lid_b) in res.stderr
+        assert _cuenta_goals(conn) == 0
+
+
+@_skip_sin_pg
+def test_s1_sku_unico_sigue_como_listing_id():
+    """S1: --sku con una sola publicacion siembra como --listing-id."""
+    with _db() as (conn, dsn):
+        datos = _cadena(conn, sku="SKU-UNICO", ext="ASIN-UNICO")
+        seco = _tool(
+            "--sku",
+            "SKU-UNICO",
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            dsn=dsn,
+        )
+        assert seco.returncode == 0, seco.stderr
+        assert f"listing={datos['listing']}" in seco.stdout
+        huella = seco.stdout.split("huella: ")[1].split()[0]
+        go = _tool(
+            "--sku",
+            "SKU-UNICO",
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            "--mode",
+            "shadow",
+            "--acepto-mutacion-real",
+            "--huella",
+            huella,
+            dsn=dsn,
+        )
+        assert go.returncode == 0, go.stderr
+        assert _cuenta_goals(conn) == 1
+
+
+@_skip_sin_pg
+def test_s1_sku_excluyente_con_listing_y_csv(tmp_path):
+    """S1: --sku no se combina con --listing-id ni con --csv."""
+    with _db() as (conn, dsn):
+        _config(conn)
+        res = _tool(
+            "--sku",
+            "X",
+            "--listing-id",
+            "1",
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            dsn=dsn,
+        )
+        assert res.returncode == 2
+        csv = tmp_path / "lote.csv"
+        csv.write_text("listing_id,platform,goal_pct\n1,amazon_mx,30.00\n", encoding="utf-8")
+        res2 = _tool(
+            "--sku",
+            "X",
+            "--csv",
+            str(csv),
+            "--platform",
+            "amazon_mx",
+            "--goal-pct",
+            "30.00",
+            dsn=dsn,
+        )
+        assert res2.returncode == 2
• Now I have the full diff. Let me review it for real issues.

  The diff adds only `tests/test_precio_goals.py` (new file, 1650 lines). It references `app/precio/goals_write.py` and `tools/precio_goal.py` — presumably existing in the repo (commit is a merge; the diff was limited to the task file). I should verify some things in the repo:

  1. Does `tests/test_schema.py` export `_postgres_obligatorio_ausente`?
  2. Do `app/precio/goals_write.py` and `tools/precio_goal.py` exist?
  3. Are there bugs in the test code itself?

  Potential findings from reading:

  1. `_cadena` docstring says "(C=40, P=116, F=15)" — consistent with _componentes.

  2. In `test_cerrar_elige_el_vigente_entre_cerrados`: inserts a new goal with `valid_from = hoy - 10 days` directly via SQL with mode shadow and NULL go_literal — fine. Then cerrar returns nuevo. But wait — the check `precio_goal_live_exige_go` for shadow with NULL go is fine. OK.

  3. `_escenario` inserts `contribucion_pct = Decimal(m)` where m="24.00" (percent units), and `contribucion = Decimal(m).quantize(0.01)` — hmm, both are the same value "24.00"; presumably contribucion should be an amount but for tests it doesn't matter.

  4. `test_huella_estable_entre_dry_runs` asserts the entire stdout identical between runs — if output contains timestamps this would be flaky, but presumably deterministic. Not a bug per se.

  5. `_db()`: on `finally`, if `admin.execute(CREATE DATABASE)` fails, conn is None and we still DROP — fine. If `psycopg.connect(dsn, autocommit=True)` fails... admin not defined? Actually if connect fails, the exception propagates before try block? No — `admin = psycopg.connect(...)` is before `try`, so if it fails, no cleanup needed. Fine.

  6. `_CONTADOR` itertools.count — DB name uniqueness across parallel pytest workers: includes hostname + pid + counter. With pytest -n auto, each worker has its own pid, fine. But DB name length: `goals_{hostname}_{pid}_{n}` — hostname could be long; PG identifier limit 63 bytes; truncation could collide. Minor.

  7. `test_r2_mismo_dia_utc_dice_que_espere_manana`: sembrar with today's date, cerrar, resembrar same day expects "dia siguiente". Fine.

  8. `_tool` env: inherits os.environ, sets ORBIT_DSN_ADMIN to test dsn. But it doesn't unset `ORBIT_SECRETS_DIR` or other env that might point the tool at real secrets... The tool is "cero Amazon" so fine. Also env keeps `ORBIT_TEST_DSN` — if the tool read ORBIT_TEST_DSN? Unlikely.

  Potential real issue: `env["ORBIT_DSN_ADMIN"] = dsn` where dsn is the DSN of the temp DB — but the DSN contains credentials from ORBIT_TEST_DSN, e.g. user `orbit`. The temp DB is created by connecting as that user; CREATE DATABASE requires privilege — presumably orbit is superuser in test env. Fine.

  9. The `match="nea 4"` — matches "línea 4" with accent... "nea" matches "línea". OK, they avoid accents deliberately.

  10. `test_banda_bordes_inclusivos_en_el_tool` — goal 10.00 with P* jump: goal 0.10 → P* lower... the comment in L10 says P*=66.07 for goal 10, which is 43% below. They pass --confirmar-salto. OK.

  11. Unused import: `ast` is used in `_imports_goals_write`. OK. `sys` used. `socket` used. OK.

  12. `_config(conn)` is called multiple times in `_cadena` and again in `test_cerrar_sin_vigente_aborta` and `test_sin_claves_de_config_aborta` — config_version may have unique constraint on label 'test-goals'? `_config` inserts label "test-goals" each time. If config_version has UNIQUE(label), calling _cadena then _config again would fail. In `test_cerrar_sin_vigente_aborta`, `_cadena` calls `_config` once, then `_config(conn)` again — would violate unique if exists. Need to check schema. Similarly `test_sin_claves_de_config_aborta` calls _cadena (which calls _config with GOAL_SETTINGS) then _config with {"otra_clave": 1}. If label unique → failure. Also, the test intends to test "sin claves" but _cadena already inserted a config WITH the keys — if the tool picks the latest config_version, the new one (otra_clave) wins; if it picks another one, test may pass for wrong reason or fail. This is a plausible test-design concern but the tests presumably pass (CI green). Can't be sure config_version allows duplicate labels. Let me check migration 0001 for config_version.

  13. In `test_csv_repetido...`: csv line 4 is the repeated one ("nea 4" — línea 4 counting header as line 1: header=1, a=2, b=3, a-repeat=4). Consistent.

  14. `_fugas_imports_goals_write` imports sys as _sys inside function though sys already imported at top — minor style.

  15. The subprocess tests don't set a timeout on subprocess.run — a hung tool would hang the test suite forever. Minor quality issue (no timeout kwarg). Medium/low.

  16. `_tool` passes `**kw` to subprocess.run; tmp_path fixture used in `test_cerrar_flujo_completo` but not used (parameter `tmp_path` unused). Minor lint — ruff might flag unused argument? ARG rules not necessarily enabled. Low.

  17. `test_go_sin_acepto_no_mut_aunque_traiga_go`: passes --huella x --go voy without --acepto; asserts "acepto-mutacion-real" in stderr. Fine.

  18. Duplication: this file is 1650 lines of tests duplicating fixture patterns from test_precio_migracion — the docstring says deliberately autocontained. Quality note, low.

  19. `_dsn_de_db` keeps query/fragment — fine.

  20. DROP DATABASE WITH (FORCE) requires PG13+. Fine.

  21. `test_huella_ata_el_modo` and `_goal` — fine.

  22. One thing to verify: `from test_schema import _postgres_obligatorio_ausente` — does tests/test_schema.py define that? And does `tests/` have conftest that makes `test_schema` importable (rootdir on sys.path)? Check.

  23. Check that `app/precio/goals_write.py` and `tools/precio_goal.py` exist (referenced). Also migration 0039 exists.

  24. `_escenario` uses hardcoded `context_fingerprint='ctx-g1'` and motivos/exclusiones empty; there's a trigger validating escenario (run accounting_sku_costs). Fine.

  25. A subtle one: `_cadena` uses datetime.now(UTC) marca for fetched/observed of oferta/fee, and `_escenario` default observed = now+120s — the "mas reciente manda" logic presumably orders by observed_at; fine.

  26. In `test_g1_goal_y_m_actual_en_por_ciento`: m="36.6379", expects printed "36.64%". OK.

  Let me verify in the repo: existence of files, `_postgres_obligatorio_ausente`, config_version uniqueness, and whether the tests actually run (maybe run a quick subset? Running requires Postgres on localhost:5432 — may not be available; could check quickly).

  Also verify ORDEN39 migrations exist: 0028_estimacion_venta.sql, 0039_precio.sql.

  Also check test_precio_migracion's db_39 pattern — compare, but not essential.

  Also check `.pre-commit-config` would flag unused `tmp_path` in test_cerrar_flujo_completo — ruff config in pyproject; check if ARG001 enabled. Let me check quickly.

  Let me run a few verifications.

app/precio/goals_write.py
migrations/0028_estimacion_venta.sql
migrations/0039_precio.sql
tests/test_schema.py
tools/precio_goal.py
• Interesting — there's a `tests/test_goals_write.py` too (maybe a different older module for ads goals?). And tests/test_precio_goals.py exists in repo. Now check:

  1. config_version uniqueness on label.
  2. Whether test collection passes / run the quick pure tests (no PG) — like the import-guard tests.
  3. Check `_postgres_obligatorio_ausente` in test_schema.py definition.
  4. Check pyproject ruff rules re unused args (ARG).28:[tool.ruff.lint]
36:select = ["E", "F", "I", "UP", "B", "SIM", "C901", "PLR0912", "PLR0915"]
38:[tool.ruff.lint.mccabe]
41:[tool.ruff.lint.pylint]
45:[tool.ruff.lint.per-file-ignores]
Hmm, my earlier grep for "CREATE TABLE config_version|label" matched files list but I need content. Let me grep content mode in migrations.624:CREATE TABLE config_version (
625-    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
626-    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
627-    label      TEXT,
628-    settings   JSONB NOT NULL
629-);
630-
631-COMMENT ON TABLE config_version IS
632-  'Snapshot inmutable (append-only por trigger) del set completo de '
633-  'configuración; la config VIVA vigente es la fila más reciente. Una decisión '
634-  'apunta a la versión que regía cuando se tomó; sin esto no se puede '
635-  'reconstruir por qué el sistema hizo lo que hizo. La inserta app_admin '
636-  '(config humana, escalera off->shadow->live), no los motores. '
migrations/0002_apply.sql:452:-- ÚLTIMA config_version por id (IDENTITY monótono, coherente con created_at),
migrations/0002_apply.sql:453:-- que es lo que el COMMENT de config_version (0001) declara "la config VIVA
migrations/0002_apply.sql:474:          FROM config_version
migrations/0002_apply.sql:488:  'config_version, o si la clave no existe en la config vigente: en los tres '
migrations/0039_precio.sql:242:    config_version_id   BIGINT REFERENCES config_version (id),
migrations/0039_precio.sql:296:CREATE INDEX precio_decision_por_config ON precio_decision (config_version_id);
migrations/0039_precio.sql:1171:          FROM config_version
config_version has no UNIQUE on label — so multiple inserts are fine. Good, that potential issue dissolves.

  Now: does the tool pick "latest config_version"? In test_sin_claves_de_config_aborta, _cadena seeds a config with GOAL_SETTINGS, then a second config with {"otra_clave": 1} is inserted — latest wins, tool aborts with "config sin precio_goal_min_pct". Consistent with "la config VIVA vigente es la fila más reciente". OK.

  Now check ruff select: ["E","F","I","UP","B","SIM","C901","PLR0912","PLR0915"]. No ARG, so unused tmp_path param is fine. F would catch unused imports — `ast` used, `itertools` used, `socket` used, `sys` used, `urlunsplit` used. OK.

  Now — does the test file actually pass? Check if Postgres available and run the pure tests quickly. Also maybe run the full file if DB available. Let me check quickly: `pg_isready` or try pytest collect.

  Also verify tests/test_precio_goals.py in repo matches diff (it's the committed file; the diff is HEAD). Fine.

  Also check tests/test_goals_write.py exists — could there be name collision with module `goals_write` import guard? No.

  One thing to check: `_imports_goals_write` relative import handling adds `<relativo-nivel-N>` which would be flagged as fuga if goals_write used relative imports — but the permitidos check: "<relativo-nivel-1>".split(".")[0] not in stdlib and not permitted → would be flagged. But goals_write presumably uses absolute imports. Fine — actually if goals_write used `from . import x`, test would flag it. It passes presumably.

  Potential real issues to report:

  1. `_tool` subprocess.run has NO timeout — a hang in the tool (e.g. connection attempt to a firewalled host, though DSN points local) would hang pytest indefinitely. Low/medium. ORBIT_DSN_DECIDE/READ point at 127.0.0.1:1 — connection refused immediately, fine. But admin DSN could hang in some environments. Low.

  2. `test_huella_estable_entre_dry_runs` asserts full stdout equality — brittle if tool prints absolute paths, durations, or timestamps. But presumably deterministic; it's the point of the test. Fine.

  3. DB name: `goals_{hostname}_{pid}_{counter}` — hostname may contain chars invalid? Identifier is quoted via pgsql.Identifier, so fine. Length >63 truncated — collision risk minuscule. Low.

  4. `_db()` drops DB with FORCE — requires PG ≥ 13; project uses PG16. Fine.

  5. Race: `test_escenario_mas_reciente_manda` — the new scenario observed = now+600s vs _cadena's escenario default observed = now+120s. OK deterministic.

  6. In `_cadena`, fee's `fees_estimated_at` = fetched (marca) — fine.

  7. `_config` inserts GOAL_SETTINGS with values as strings "0.10"/"0.60" — banda_desde_settings presumably parses strings. Fine.

  8. One notable: `_skip_sin_pg` is evaluated at import time (`_postgres_obligatorio_ausente()` called at module import) — that runs a connection attempt at collection. That's the established pattern across many test files (test_schema exports it), so consistent.

  9. The docstring claims "patron `db_39` de `test_precio_migracion`, autocontenido aqui" — duplicates ~100 lines of fixture code instead of sharing via conftest. Quality note, low. Also duplicates pattern `_config_version` from test_api. The repo seems to accept duplication in tests.

  10. `test_cerrar_flujo_completo(tmp_path)` — tmp_path unused parameter. Cosmetic, low.

  11. Subprocess env: passes `ORBIT_DSN_ADMIN` with credentials in env; also the DSN appears in process args? No, only env. Fine. But note: env also inherits any real ORBIT_* secrets env vars — if the tool honored e.g. ORBIT_DSN_WRITE or other var names not overridden, tests could touch real DB. Depends on tool's accepted env vars — can't verify without reading tool; could check what env vars tools/precio_goal.py reads. Let me grep.

  12. Check the goal table/checks exist in 0039: precio_goal with check constraints precio_goal_live_exige_go, precio_goal_banda, trigger "solo se puede cerrar". Verify quickly.

60:CREATE TABLE precio_goal (
73:    CONSTRAINT precio_goal_banda
75:    CONSTRAINT precio_goal_live_exige_go
130:  'el CHECK precio_goal_live_exige_go lo impone también contra `psql`.';
161:            'precio_goal %: de una fila publicada sólo se puede cerrar la vigencia '
tools/precio_goal.py:82:    dsn = os.environ.get("ORBIT_DSN_ADMIN")
• Tool reads only ORBIT_DSN_ADMIN. goals_write reads nothing from env (no matches) — takes conn. Good.

  Now let me try running the test file quickly to see if it passes (the strongest verification). Check if Postgres available.

................................................                         [100%]
48 passed in 10.31s
• All 48 tests pass (no skips — Postgres available). Now finalize the review.

  Remaining possible findings:

  1. `_tool` subprocess.run sin timeout — baja. A hung child would hang the suite indefinitely. Minor.
  2. `test_cerrar_flujo_completo(tmp_path)` — tmp_path parameter unused. Baja/cosmetica. Ruff select doesn't include ARG so pre-commit won't catch.
  3. Duplicacion del fixture `_db`/ORDEN39 en vez de compartir con test_precio_migracion — declarado intencionalmente en el docstring; baja.
  4. Nombre de DB con hostname — si el hostname supera el limite de 63 bytes de identificador, PG trunca y podria haber colision entre DBs concurrentes (hostname + pid lo hace improbable). Muy baja.
  5. `_fugas_imports_goals_write` re-importa `sys` localmente aunque ya esta importado arriba — cosmetico.
  6. `_escenario` inserta `contribucion` y `contribucion_pct` con el mismo valor `m` (24.00) — el primero deberia ser un monto de dinero y el segundo un porcentaje; en la vida real difieren. Si el tool leyera `contribucion` (monto) en vez del pct, el test no lo detectaria. Verificar que el tool usa contribucion_pct. Let me check quickly what the tool queries. This could be a real test-blindness issue (medium-ish). Let me grep tools/precio_goal.py for contribucion.

tools/precio_goal.py:14:(`contribucion_pct` esta en por ciento); el salto se confirma por
tools/precio_goal.py:89:    "SELECT id, contribucion_pct, componentes, fee_observation_id,"
tools/precio_goal.py:196:    # G1: `contribucion_pct` esta en POR CIENTO (39.0000 = 39 %): goal y
• The tool only reads `contribucion_pct` (and componentes), not `contribucion` amount — so seeding contribucion with the same value is harmless for these tests. Fine.

  Review conclusions. Overall the test file is solid: verified against real repo — modules exist, constraints exist, helper exists, 48/48 pass against real Postgres with zero skips.

  Findings (all low severity):

  1. (baja) `subprocess.run` en `_tool` sin `timeout`: si el tool se cuelga (p.ej. esperando conexion en un entorno raro), el suite se cuelga sin diagnostico.

  2. (baja) `test_cerrar_flujo_completo(tmp_path)` recibe `tmp_path` y nunca lo usa (test_lote... wait, check: test_cerrar_flujo_completo signature has tmp_path but body never uses it). Cosmetic; ruff no tiene ARG habilitado asi que pasa los candados.

  3. (baja) `test_huella_estable_entre_dry_runs` compara el stdout COMPLETO entre dos corridas; si el tool algun dia imprime algo no deterministico (timestamp, duracion, path absoluto), el test se vuelve flaky aunque la huella sea estable. Seria mas robusto extraer y comparar solo la huella. Verificado: hoy el stdout es deterministico (los tests pasan), es un riesgo de fragilidad futura.

  4. (baja) El nombre de DB temporal `goals_{hostname}_{pid}_{n}` puede exceder el limite de 63 bytes de identificador de Postgres (hostname largo); PG trunca en silencio y con shards paralelos podria colisionar. Pid + contador lo hacen muy improbable.

  5. (baja) Duplicacion de infraestructura de fixture (el patron db_39/ORDEN39 vive tambien en test_precio_migracion) — declarado como intencional en el docstring, pero duplica ~100 lineas que habra que mantener en dos lugares.

  Anything higher? Security: the subprocess gets ORBIT_DSN_ADMIN with creds in env — env is more protected than argv; fine. Tests create/drop databases — only against test DSN; the guard `_postgres_obligatorio_ausente` presumably refuses if DSN points to non-test... can't fully verify but pattern used repo-wide.

  One more: `_db()` finally block — if `conn.execute(migration)` fails midway, conn closed and DB dropped; fine. If `psycopg.connect(_dsn_de_db...)` fails, conn stays None, drop runs — fine.

  Also `_tool` hereda `os.environ` completo: si el entorno de CI tuviera otras vars ORBIT_* que el tool leyera en el futuro, el aislamiento se rompe silenciosamente. Hoy el tool solo lee ORBIT_DSN_ADMIN (verificado con grep). Baja.

  Write up the answer in Spanish, plain text, numbered list with severities.

To resume this session: kimi -r session_7b3eca34-f648-499a-be46-7875640bdc32


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=662db383ee517cccfac757cc815531ca70fc786a archivos=tests/test_precio_goals.py inicio=2026-09-18T17:08:11Z fin=2026-09-18T17:13:54Z exit=0
