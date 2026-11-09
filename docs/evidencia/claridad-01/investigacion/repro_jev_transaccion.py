import psycopg, sys
dsn = sys.argv[1]
for guarda in (False, True):
    conn = psycopg.connect(dsn)  # autocommit=False, como app.db.connect(dsn_read)
    try:
        if guarda:
            conn.execute("SELECT 1").fetchone()  # la guarda metricas_listas.guarda
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.execute("SELECT 1").fetchone()
        print(f"guarda={guarda}: OK")
    except Exception as e:
        print(f"guarda={guarda}: {type(e).__name__}: {e}")
    finally:
        conn.close()
