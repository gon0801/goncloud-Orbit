import re
import sys

html = sys.stdin.read()
for m in re.finditer(r'<div class="tarjeta" data-plataforma="([^"]+)">(.*?)</tbody>', html, re.S):
    print(m.group(1), m.group(2).count("<tr>") - 1)
