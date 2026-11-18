import datetime as dt

from app.ads.diario import HORARIOS_INGESTA
from app.dia_cerrado import ultimo_dia_cerrado

invierno = dt.datetime(2026, 11, 12, 7, 10, tzinfo=dt.UTC)
assert ultimo_dia_cerrado("amazon_us", invierno) == dt.date(2026, 11, 10), "US a las 23:10 de LA"
assert ultimo_dia_cerrado("amazon_mx", invierno) == dt.date(2026, 11, 11), "MX ya cerro"
assert ultimo_dia_cerrado("amazon_us", invierno.replace(hour=8)) == dt.date(2026, 11, 11), "US cierra 08:00 UTC"
assert HORARIOS_INGESTA[0] == "08:10", f"primer horario {HORARIOS_INGESTA[0]}"
print("D-E desplegado")
