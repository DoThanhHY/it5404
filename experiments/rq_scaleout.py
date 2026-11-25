#!/usr/bin/env python3
"""RQ1/RQ2 – phân rã thời gian scale-out theo cấu hình C0 / C1 / C2.

Mỗi lần đo: tạo 1 VM (metadata group=bench → autoscaler và Prometheus bỏ qua), ghi các mốc:
  t0 (gọi API) → t_api (API trả về) → t_active (Nova ACTIVE) → t_ready (HTTP /actuator/health/readiness = 200)
rồi SSH vào guest lấy mốc nội bộ (đồng hồ guest đồng bộ với host qua RTC/arch timer, sai số ~1 s):
  guest_boot  = thời điểm kernel bắt đầu (now - /proc/uptime)
  ci_done     = cloud-final.service kết thúc
  jvm_start   = cpu-app.service bắt đầu (ExecMainStartTimestamp)
  spring_s    = "Started CpuApp in X seconds"
Chạy trong VM host bằng user stack:
  /opt/stack/autoscaler/venv/bin/python rq_scaleout.py --config C1 --runs 10 --out results/rq2
"""
from __future__ import annotations

import argparse
import base64
import csv
import os
import re
import subprocess
import time
from pathlib import Path

import openstack
import requests

CONFIGS = {
    "C0": {"image": "ubuntu-24.04-base", "user_data": "../infra/image/cloud-init-c0.yaml"},
    "C1": {"image": "app-golden"},
    "C2": {"image": "app-golden-c2"},
}
SSH = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
       "-o", "ConnectTimeout=5"]
GUEST_PROBE = r"""
now=$(date +%s.%N); up=$(cut -d' ' -f1 /proc/uptime)
mono(){ systemctl show "$1" -p "$2" --value; }
echo boot=$(awk -v n="$now" -v u="$up" 'BEGIN{printf "%.3f", n-u}')
echo now=$now up=$up
echo ci_done_us=$(mono cloud-final.service ExecMainExitTimestampMonotonic)
echo jvm_start_us=$(mono cpu-app.service ExecMainStartTimestampMonotonic)
echo spring=$(journalctl -u cpu-app --no-pager -o cat | grep -oE 'Started CpuApp in [0-9.]+' | tail -1 | grep -oE '[0-9.]+$')
echo kernel_s=$(systemd-analyze 2>/dev/null | grep -oE '[0-9.]+s \(kernel\)' | grep -oE '^[0-9.]+')
"""


def run_once(conn, cfg_name, idx, net, flavor):
    cfg = CONFIGS[cfg_name]
    image = conn.image.find_image(cfg["image"], ignore_missing=False)
    kw = dict(name=f"bench-{cfg_name.lower()}-{idx}", image_id=image.id, flavor_id=flavor.id,
              networks=[{"uuid": net.id}], security_groups=[{"name": "app-sg"}], key_name="it5404",
              metadata={"group": "bench", "config": cfg_name})
    if cfg.get("user_data"):
        kw["user_data"] = base64.b64encode(Path(cfg["user_data"]).read_bytes()).decode()
    t0 = time.time()
    srv = conn.compute.create_server(**kw)
    t_api = time.time()
    while True:
        srv = conn.compute.get_server(srv.id)
        if srv.status in ("ACTIVE", "ERROR"):
            break
        time.sleep(0.5)
    t_active = time.time()
    if srv.status == "ERROR":
        conn.compute.delete_server(srv)
        raise RuntimeError(f"ERROR: {srv.fault}")
    ip = srv.addresses["app-net"][0]["addr"]
    deadline = time.time() + 1200
    while time.time() < deadline:
        try:
            if requests.get(f"http://{ip}:8080/actuator/health/readiness", timeout=1).status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.5)
    t_ready = time.time()
    out = subprocess.run(SSH + [f"ubuntu@{ip}", "bash -s"], input=GUEST_PROBE, capture_output=True, text=True,
                         timeout=60).stdout
    g = dict(re.findall(r"(\w+)=([0-9.]*)", out))
    boot = float(g.get("boot") or "nan")
    def mono(key):
        v = g.get(key)
        return boot + int(v) / 1e6 if v and v != "0" else float("nan")
    row = {
        "config": cfg_name, "run": idx, "ip": ip,
        "api_s": t_api - t0,
        "schedule_spawn_s": t_active - t_api,                 # Nova: scheduler + tải image + tạo libvirt domain
        "guest_boot_offset_s": boot - t0,                     # kernel guest bắt đầu (so với t0)
        "kernel_s": float(g.get("kernel_s") or "nan"),
        "cloudinit_done_s": mono("ci_done_us") - t0,
        "jvm_start_s": mono("jvm_start_us") - t0,
        "spring_start_s": float(g.get("spring") or "nan"),
        "ready_s": t_ready - t0,                               # tổng: từ lúc gọi API tới lúc app phục vụ được
    }
    conn.compute.delete_server(srv)
    conn.compute.wait_for_delete(srv, wait=300)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=CONFIGS, required=True)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--out", default="results/rq2")
    a = ap.parse_args()
    os.chdir(Path(__file__).parent)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    httpd = None
    if a.config == "C0":   # C0 tải app.jar từ host qua HTTP
        httpd = subprocess.Popen(["python3", "-m", "http.server", "8000", "--bind", "172.24.4.1",
                                  "--directory", str(Path("../app/target").resolve())],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    conn = openstack.connect(cloud="devstack")
    net = conn.network.find_network("app-net", ignore_missing=False)
    flavor = conn.compute.find_flavor("app.small", ignore_missing=False)
    path = Path(a.out) / f"{a.config}.csv"
    new = not path.exists()
    try:
        for i in range(1, a.runs + 1):
            row = run_once(conn, a.config, i, net, flavor)
            with path.open("a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(row))
                if new:
                    w.writeheader(); new = False
                w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()})
            print(f"{a.config} #{i}: ready {row['ready_s']:.1f}s (active {row['api_s']+row['schedule_spawn_s']:.1f}s, "
                  f"cloud-init {row['cloudinit_done_s']:.1f}s, jvm {row['jvm_start_s']:.1f}s, spring {row['spring_start_s']}s)",
                  flush=True)
            time.sleep(10)
    finally:
        if httpd:
            httpd.terminate()


if __name__ == "__main__":
    main()
