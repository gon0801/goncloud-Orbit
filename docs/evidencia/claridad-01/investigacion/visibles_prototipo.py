import re, sys, html
from html.parser import HTMLParser
class P(HTMLParser):
    def __init__(s): super().__init__(); s.skip=0; s.out=[]
    def handle_starttag(s,t,a):
        if t in ("script","style","template"): s.skip+=1
        for k,v in a:
            if k in ("title","placeholder","aria-label") and v: s.out.append(v)
    def handle_endtag(s,t):
        if t in ("script","style","template"): s.skip-=1
    def handle_data(s,d):
        if not s.skip: s.out.append(d)
PAT={"snake":r"\b[a-z]+(?:_[a-z0-9]+)+\b","decimales_largos":r"\d+\.\d{5,}","microsegundos":r"\d{2}:\d{2}:\d{2}\.\d+","sin_miles":r"(?<![\d,.])\d{4,}\.\d{1,2}\b","utc":r"\bUTC\b|\+00:00"}
for f in sys.argv[1:]:
    p=P(); p.feed(open(f,encoding="utf-8").read()); txt="\n".join(p.out)
    res={k:sorted(set(re.findall(v,txt))) for k,v in PAT.items()}
    print(f.split("/")[-1], {k:(len(v), v[:12]) for k,v in res.items() if v})
