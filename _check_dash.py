import requests

r = requests.get("http://localhost:9001/static/doctor_dashboard.html")
html = r.text

checks = [
    ("Latency KPI - avg",     "lat-avg" in html),
    ("Latency KPI - max",     "lat-max" in html),
    ("Latency KPI - total",   "lat-total" in html),
    ("Latency KPI - under500","lat-under500" in html),
    ("Latency KPI - pct500",  "lat-pct500" in html),
    ("Latency chart wrap",    "lat-chart-wrap" in html),
    ("Latency table body",    "lat-table-body" in html),
    ("Latency bands - fast",  "lat-band-fast" in html),
    ("Latency bands - ok",    "lat-band-ok" in html),
    ("Latency bands - slow",  "lat-band-slow" in html),
    ("Latency health badge",  "lat-health-badge" in html),
    ("renderLatChart func",   "renderLatChart" in html),
    ("renderLatTable func",   "renderLatTable" in html),
    ("loadMetrics fixed",     "/metrics/asr-confidence" in html),
    ("No /metrics/latency",   "/metrics/latency" not in html),
]

print()
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")

all_ok = all(c[1] for c in checks)
print(f"\n  All checks: {'PASS' if all_ok else 'FAIL'}")
