#!/usr/bin/env python3
"""Thu số liệu một lần chạy tải từ Prometheus + log autoscaler -> thư mục kết quả (CSV/JSON) để vẽ biểu đồ báo cáo.
   python3 collect.py <out_dir> <start_epoch> <end_epoch>"""
import csv, json, subprocess, sys, urllib.parse, urllib.request
PROM = "http://192.168.64.2:9090"
SERIES = {
    "cpu_avg": "app:cpu_util:avg1m",
    "vms": 'count(up{job="node"} == 1)',
    "rps": "app:http_rps:1m",
    "p95_s": "app:http_p95_seconds:1m",
    "p99_s": 'histogram_quantile(0.99, sum by (le) (rate(http_server_requests_seconds_bucket{job="app",uri="/work"}[1m])))',
    "haproxy_5xx": 'sum(rate(haproxy_frontend_http_responses_total{proxy="web",code="5xx"}[1m])) or vector(0)',
}
out, start, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
rows = {}
for name, q in SERIES.items():
    url = f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode({"query": q, "start": start, "end": end, "step": 15})
    res = json.load(urllib.request.urlopen(url))["data"]["result"]
    for ts, v in (res[0]["values"] if res else []):
        rows.setdefault(int(ts), {})[name] = round(float(v), 4) if v not in ("NaN",) else ""
with open(f"{out}/timeseries.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["t_s", *SERIES])
    for ts in sorted(rows):
        w.writerow([ts - start, *[rows[ts].get(k, "") for k in SERIES]])
log = subprocess.run(["limactl", "shell", "openstack", "cat", "/var/log/autoscaler/decisions.jsonl"],
                     capture_output=True, text=True).stdout.splitlines()
from datetime import datetime
ev = [json.loads(l) for l in log if l.strip()]
ev = [e for e in ev if start <= datetime.fromisoformat(e["ts"]).timestamp() <= end]
json.dump(ev, open(f"{out}/decisions.json", "w"), ensure_ascii=False, indent=1)
print(out, len(rows), "điểm,", len(ev), "sự kiện")
