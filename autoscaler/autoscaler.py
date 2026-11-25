#!/usr/bin/env python3
"""Autoscaler theo ngưỡng cho nhóm VM app trên OpenStack (đồ án IT5404).

Vòng lặp điều khiển: đọc CPU trung bình từ Prometheus -> quyết định scale-out/in ->
gọi Nova API (openstacksdk) -> cập nhật backend HAProxy qua Runtime API.
Mỗi quyết định ghi 1 dòng JSON vào log có cấu trúc (dùng cho báo cáo, RQ1 và trợ lý AI).
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import openstack
import requests
import yaml
from prometheus_client import Gauge, Counter, generate_latest, CONTENT_TYPE_LATEST

log = logging.getLogger("autoscaler")

G_CPU = Gauge("autoscaler_observed_cpu_percent", "CPU trung bình nhóm app mà autoscaler quan sát")
G_DESIRED = Gauge("autoscaler_desired_instances", "Số VM app autoscaler đang quản lý")
C_SCALE = Counter("autoscaler_scale_events_total", "Số lần scale", ["direction", "result"])
G_REACTION = Gauge("autoscaler_last_scale_out_seconds", "Thời gian phản ứng của lần scale-out gần nhất", ["phase"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DecisionLog:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def write(self, event: str, **fields):
        rec = {"ts": now_iso(), "event": event, **fields}
        with self.lock, self.path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.info("%s %s", event, json.dumps(fields, ensure_ascii=False))


class HAProxy:
    """Điều khiển backend qua Runtime API (server-template app1..appN)."""

    def __init__(self, addr: str, backend: str, slots: int):
        host, port = addr.split(":")
        self.addr = (host, int(port))
        self.backend, self.slots = backend, slots

    def cmd(self, command: str) -> str:
        with socket.create_connection(self.addr, timeout=3) as s:
            s.sendall((command + "\n").encode())
            chunks = []
            while (b := s.recv(65536)):
                chunks.append(b)
        return b"".join(chunks).decode()

    def servers(self) -> dict[str, dict]:
        """slot -> {addr, admin_state}; dùng 'show servers state'."""
        out = self.cmd(f"show servers state {self.backend}").splitlines()
        hdr = None
        res = {}
        for line in out:
            if line.startswith("#"):
                hdr = line.lstrip("# ").split()
                continue
            parts = line.split()
            if hdr and len(parts) >= len(hdr) - 2:
                row = dict(zip(hdr, parts))
                res[row["srv_name"]] = {"addr": row["srv_addr"], "admin": int(row["srv_admin_state"])}
        return res

    EMPTY = ("0.0.0.0", "-")

    def slot_of(self, ip: str) -> str | None:
        for name, s in self.servers().items():
            if s["addr"] == ip:
                return name
        return None

    def add(self, ip: str) -> str:
        existing = self.slot_of(ip)
        if existing:
            return existing
        for name, s in self.servers().items():
            if s["addr"] in self.EMPTY:               # slot trống
                self.cmd(f"set server {self.backend}/{name} addr {ip} port 8080")
                self.cmd(f"set server {self.backend}/{name} state ready")
                return name
        raise RuntimeError("HAProxy hết slot trống")

    def drain(self, ip: str):
        if (name := self.slot_of(ip)):
            self.cmd(f"set server {self.backend}/{name} state drain")

    def remove(self, ip: str):
        if (name := self.slot_of(ip)):
            self.cmd(f"set server {self.backend}/{name} state maint")
            self.cmd(f"set server {self.backend}/{name} addr 0.0.0.0")

    def active_conns(self, ip: str) -> int:
        name = self.slot_of(ip)
        if not name:
            return 0
        out = self.cmd(f"show stat {self.backend} 4 -1").splitlines()   # 4 = servers
        hdr = out[0].lstrip("# ").split(",")
        for line in out[1:]:
            row = dict(zip(hdr, line.split(",")))
            if row.get("svname") == name:
                return int(row.get("scur") or 0)
        return 0


class Autoscaler:
    def __init__(self, cfg: dict, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.conn = openstack.connect(cloud=cfg["cloud"])
        self.lb = HAProxy(cfg["haproxy"]["runtime"], cfg["haproxy"]["backend"], cfg["haproxy"]["slots"])
        self.dlog = DecisionLog(cfg["log_file"])
        self.above = self.below = 0
        self.last_out = self.last_in = 0.0
        self.busy = threading.Lock()          # mỗi lúc chỉ 1 hành động scale
        self.pending: list[dict] = []         # đề xuất từ trợ lý AI chờ xác nhận

    # ---------- OpenStack ----------
    def app_servers(self) -> list:
        meta = self.cfg["server"]["metadata"]
        servers = [s for s in self.conn.compute.servers(details=True)
                   if s.metadata.get("group") == meta["group"] and s.status not in ("DELETED", "SOFT_DELETED")]
        return sorted(servers, key=lambda s: s.created_at)

    @staticmethod
    def ip_of(server, network: str) -> str | None:
        for a in server.addresses.get(network, []):
            if a.get("OS-EXT-IPS:type", "fixed") == "fixed" and a.get("version") == 4:
                return a["addr"]
        return None

    # ---------- Prometheus ----------
    def query(self, q: str) -> float | None:
        r = requests.get(f"{self.cfg['prometheus_url']}/api/v1/query", params={"query": q}, timeout=5)
        r.raise_for_status()
        res = r.json()["data"]["result"]
        return float(res[0]["value"][1]) if res else None

    # ---------- Scale out ----------
    def scale_out(self, reason: dict):
        c, s = self.cfg, self.cfg["server"]
        t = {"t_decide": time.time()}
        name = f"{s['name_prefix']}-{datetime.now().strftime('%H%M%S')}"
        self.dlog.write("scale_out_start", name=name, **reason)
        if self.dry_run:
            return
        user_data = None
        if s.get("user_data_file"):
            import base64
            user_data = base64.b64encode(Path(s["user_data_file"]).read_bytes()).decode()
        image = self.conn.image.find_image(s["image"], ignore_missing=False)
        flavor = self.conn.compute.find_flavor(s["flavor"], ignore_missing=False)
        net = self.conn.network.find_network(s["network"], ignore_missing=False)
        kwargs = dict(name=name, image_id=image.id, flavor_id=flavor.id, networks=[{"uuid": net.id}],
                      security_groups=[{"name": s["security_group"]}], metadata=dict(s["metadata"]))
        if s.get("key_name"):
            kwargs["key_name"] = s["key_name"]
        if user_data:                               # Nova từ chối user_data=None (400)
            kwargs["user_data"] = user_data
        srv = self.conn.compute.create_server(**kwargs)
        t["t_api_return"] = time.time()
        srv = self.conn.compute.wait_for_server(srv, status="ACTIVE", wait=c["ready_timeout_s"], interval=2)
        t["t_active"] = time.time()
        ip = self.ip_of(srv, s["network"])
        # chờ ứng dụng sẵn sàng
        deadline = time.time() + c["ready_timeout_s"]
        while time.time() < deadline:
            try:
                if requests.get(f"http://{ip}:8080/actuator/health/readiness", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        else:
            raise TimeoutError(f"{name} không sẵn sàng sau {c['ready_timeout_s']}s")
        t["t_app_ready"] = time.time()
        slot = self.lb.add(ip)
        t["t_in_lb"] = time.time()
        phases = {
            "api": t["t_api_return"] - t["t_decide"],
            "boot_to_active": t["t_active"] - t["t_api_return"],
            "active_to_app_ready": t["t_app_ready"] - t["t_active"],
            "lb_register": t["t_in_lb"] - t["t_app_ready"],
            "total": t["t_in_lb"] - t["t_decide"],
        }
        for k, v in phases.items():
            G_REACTION.labels(k).set(v)
        self.dlog.write("scale_out_done", name=name, server_id=srv.id, ip=ip, haproxy_slot=slot,
                        phases_s={k: round(v, 2) for k, v in phases.items()})

    # ---------- Scale in ----------
    def scale_in(self, reason: dict):
        servers = self.app_servers()
        victim = servers[-1]                       # máy mới nhất
        ip = self.ip_of(victim, self.cfg["server"]["network"])
        self.dlog.write("scale_in_start", name=victim.name, server_id=victim.id, ip=ip, **reason)
        if self.dry_run:
            return
        self.lb.drain(ip)
        deadline = time.time() + self.cfg["drain_s"]
        while time.time() < deadline and self.lb.active_conns(ip) > 0:
            time.sleep(1)
        self.lb.remove(ip)
        self.conn.compute.delete_server(victim)
        self.conn.compute.wait_for_delete(victim, wait=300)
        self.dlog.write("scale_in_done", name=victim.name, server_id=victim.id)

    def _run_action(self, direction: str, reason: dict):
        with self.busy:
            try:
                (self.scale_out if direction == "out" else self.scale_in)(reason)
                C_SCALE.labels(direction, "ok").inc()
            except Exception as e:                 # noqa: BLE001
                C_SCALE.labels(direction, "error").inc()
                self.dlog.write(f"scale_{direction}_error", error=repr(e), **reason)
            finally:
                if direction == "out":
                    self.last_out = time.time()
                else:
                    self.last_in = time.time()

    # ---------- Reconcile: đảm bảo HAProxy khớp với Nova ----------
    def reconcile(self, servers):
        net = self.cfg["server"]["network"]
        ips = {self.ip_of(s, net) for s in servers if s.status == "ACTIVE"}
        for name, st in self.lb.servers().items():
            if st["addr"] not in HAProxy.EMPTY and st["addr"] not in ips:
                self.lb.remove(st["addr"])
                self.dlog.write("lb_reconcile_remove", slot=name, ip=st["addr"])
        for ip in ips:
            if ip and not self.lb.slot_of(ip):
                try:
                    if requests.get(f"http://{ip}:8080/actuator/health/readiness", timeout=1).status_code == 200:
                        self.dlog.write("lb_reconcile_add", ip=ip, slot=self.lb.add(ip))
                except requests.RequestException:
                    pass

    # ---------- Control loop ----------
    def tick(self):
        c = self.cfg
        if self.busy.locked():
            return
        servers = self.app_servers()
        for bad in [x for x in servers if x.status == "ERROR"]:   # vd "No valid host" lúc Nova vừa khởi động
            self.dlog.write("cleanup_error_server", name=bad.name, server_id=bad.id,
                            fault=(bad.fault or {}).get("message"))
            if not self.dry_run:
                self.conn.compute.delete_server(bad)
        servers = [x for x in servers if x.status != "ERROR"]
        n = len(servers)
        G_DESIRED.set(n)
        self.reconcile(servers)
        if n < c["min_instances"]:
            if time.time() - self.last_out < c["cooldown_out_s"]:   # backoff nếu lần trước lỗi
                return
            threading.Thread(target=self._run_action, args=("out", {"why": "below_min", "n": n}), daemon=True).start()
            return
        cpu = self.query(c["cpu_query"])
        if cpu is None:
            return
        G_CPU.set(cpu)
        self.above = self.above + 1 if cpu >= c["scale_out_threshold"] else 0
        self.below = self.below + 1 if cpu <= c["scale_in_threshold"] else 0
        now = time.time()
        cooled = now - max(self.last_out, self.last_in)
        reason = {"cpu": round(cpu, 1), "n": n}
        if self.above >= c["scale_out_cycles"]:
            if n >= c["max_instances"]:
                self.dlog.write("scale_out_blocked", why="max_instances", **reason)
            elif cooled < c["cooldown_out_s"]:
                pass
            else:
                self.above = 0
                threading.Thread(target=self._run_action, args=("out", reason), daemon=True).start()
        elif self.below >= c["scale_in_cycles"] and n > c["min_instances"] and cooled >= c["cooldown_in_s"]:
            self.below = 0
            threading.Thread(target=self._run_action, args=("in", reason), daemon=True).start()

    def run(self):
        self.dlog.write("autoscaler_start", config={k: v for k, v in self.cfg.items() if k != "server"})
        while True:
            try:
                self.tick()
            except Exception as e:                  # noqa: BLE001
                log.exception("tick lỗi: %s", e)
            time.sleep(self.cfg["interval_s"])


def make_api(scaler: Autoscaler):
    """API HTTP nhỏ: /metrics, /status, /alerts (webhook Alertmanager), /proposals (trợ lý AI)."""

    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if self.path == "/metrics":
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPE_LATEST)
                self.end_headers()
                self.wfile.write(generate_latest())
            elif self.path.startswith("/decisions"):   # log quyết định (chỉ đọc) cho trợ lý AI
                from urllib.parse import urlparse, parse_qs
                limit = int(parse_qs(urlparse(self.path).query).get("limit", ["100"])[0])
                lines = scaler.dlog.path.read_text().splitlines()[-limit:]
                self._json(200, [json.loads(x) for x in lines if x.strip()])
            elif self.path == "/status":
                c = scaler.cfg
                self._json(200, {"instances": [s.name for s in scaler.app_servers()],
                                 "busy": scaler.busy.locked(), "pending_proposals": scaler.pending,
                                 "limits": {"min": c["min_instances"], "max": c["max_instances"]},
                                 "thresholds": {"out": c["scale_out_threshold"], "in": c["scale_in_threshold"]}})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/alerts":
                for a in self._body().get("alerts", []):
                    scaler.dlog.write("alert", name=a["labels"].get("alertname"), status=a.get("status"),
                                      summary=a.get("annotations", {}).get("summary"))
                self._json(200, {"ok": True})
            elif self.path == "/proposals":          # trợ lý AI tạo đề xuất (chưa thực thi)
                p = self._body()
                if p.get("direction") not in ("out", "in"):
                    return self._json(400, {"error": "direction phải là out|in"})
                p.update(id=len(scaler.pending) + 1, ts=now_iso(), status="pending")
                scaler.pending.append(p)
                scaler.dlog.write("proposal_created", **p)
                self._json(201, p)
            elif self.path.startswith("/proposals/") and self.path.endswith("/approve"):
                pid = int(self.path.split("/")[2])
                p = next((x for x in scaler.pending if x["id"] == pid and x["status"] == "pending"), None)
                if not p:
                    return self._json(404, {"error": "không có đề xuất"})
                n = len(scaler.app_servers())
                c = scaler.cfg
                if (p["direction"] == "out" and n >= c["max_instances"]) or \
                   (p["direction"] == "in" and n <= c["min_instances"]):
                    p["status"] = "rejected_limits"
                    return self._json(409, p)
                p["status"] = "approved"
                scaler.dlog.write("proposal_approved", **p)
                threading.Thread(target=scaler._run_action,
                                 args=(p["direction"], {"why": "approved_proposal", "proposal": pid}),
                                 daemon=True).start()
                self._json(202, p)
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, *a):
            pass

    host, port = scaler.cfg["api_listen"].split(":")
    srv = ThreadingHTTPServer((host, int(port)), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", default=str(Path(__file__).with_name("config.yaml")))
    ap.add_argument("--dry-run", action="store_true", help="chỉ ghi quyết định, không gọi API")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(Path(args.config).read_text())
    scaler = Autoscaler(cfg, dry_run=args.dry_run)
    make_api(scaler)
    scaler.run()


if __name__ == "__main__":
    main()
