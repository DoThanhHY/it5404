"""Các tool CHỈ ĐỌC của trợ lý vận hành + tool tạo đề xuất (không thực thi).
Credential dùng ở đây là user Keystone 'ops-reader' (role reader) – không có quyền ghi vào OpenStack."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta

import requests

HOST = os.environ.get("OS_HOST", "192.168.64.2")
PROM = f"http://{HOST}:9090"
SCALER = f"http://{HOST}:9180"
KEYSTONE = f"http://{HOST}/identity/v3"
NOVA = f"http://{HOST}/compute/v2.1"
READER_USER = os.environ.get("OPS_READER_USER", "ops-reader")
READER_PASS = os.environ.get("OPS_READER_PASS", "reader-secret")
TZ = timezone(timedelta(hours=7))          # giờ Việt Nam


def _local(ts: float | str) -> str:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts).timestamp()
    return datetime.fromtimestamp(float(ts), TZ).strftime("%H:%M:%S %d/%m")


_token: dict = {}


def _nova_token() -> str:
    if _token.get("exp", 0) > time.time():
        return _token["id"]
    body = {"auth": {"identity": {"methods": ["password"], "password": {"user": {
        "name": READER_USER, "domain": {"id": "default"}, "password": READER_PASS}}},
        "scope": {"project": {"name": "demo", "domain": {"id": "default"}}}}}
    r = requests.post(f"{KEYSTONE}/auth/tokens", json=body, timeout=10)
    r.raise_for_status()
    _token.update(id=r.headers["X-Subject-Token"], exp=time.time() + 3000)
    return _token["id"]


# ---------------- tools ----------------
def query_prometheus(promql: str) -> str:
    r = requests.get(f"{PROM}/api/v1/query", params={"query": promql}, timeout=10)
    data = r.json()
    if data.get("status") != "success":
        return f"Lỗi PromQL: {data.get('error')}"
    out = []
    for s in data["data"]["result"][:30]:
        labels = {k: v for k, v in s["metric"].items() if k not in ("job",)}
        out.append(f"{labels or '{}'} = {float(s['value'][1]):.3f}")
    return "\n".join(out) or "(không có dữ liệu)"


def query_prometheus_range(promql: str, minutes: int = 30, step_s: int = 60) -> str:
    end = time.time()
    r = requests.get(f"{PROM}/api/v1/query_range", params={
        "query": promql, "start": end - minutes * 60, "end": end, "step": step_s}, timeout=15)
    data = r.json()
    if data.get("status") != "success":
        return f"Lỗi PromQL: {data.get('error')}"
    out = []
    for s in data["data"]["result"][:5]:
        pts = ", ".join(f"{_local(t)[:5]}={float(v):.1f}" for t, v in s["values"])
        out.append(f"{s['metric'] or '{}'}: {pts}")
    return "\n".join(out) or "(không có dữ liệu)"


def list_servers() -> str:
    r = requests.get(f"{NOVA}/servers/detail", headers={"X-Auth-Token": _nova_token()}, timeout=10)
    r.raise_for_status()
    rows = []
    for s in r.json()["servers"]:
        ips = [a["addr"] for net in s["addresses"].values() for a in net]
        rows.append(f"{s['name']}: status={s['status']}, ip={','.join(ips)}, "
                    f"group={s['metadata'].get('group', '-')}, tạo lúc {_local(s['created'].replace('Z', '+00:00'))}")
    return "\n".join(rows) or "(không có máy ảo)"


def read_scaling_log(limit: int = 20, event: str = "") -> str:
    r = requests.get(f"{SCALER}/decisions", params={"limit": 500}, timeout=10)
    evs = r.json()
    if event:
        evs = [e for e in evs if event in e["event"]]
    evs = evs[-int(limit):]
    lines = []
    for e in evs:
        e = dict(e)
        ts = _local(e.pop("ts"))
        ev = e.pop("event")
        e.pop("config", None)
        lines.append(f"[{ts}] {ev} {json.dumps(e, ensure_ascii=False)}")
    return "\n".join(lines) or "(log trống)"


def get_autoscaler_status() -> str:
    return json.dumps(requests.get(f"{SCALER}/status", timeout=10).json(), ensure_ascii=False)


def simulate_thresholds(scale_out_threshold: float = 80, scale_in_threshold: float = 20,
                        minutes: int = 60, out_cycles: int = 2, in_cycles: int = 8,
                        cooldown_out_s: int = 60, cooldown_in_s: int = 120) -> str:
    """What-if: chạy lại logic quyết định của autoscaler trên chuỗi CPU lịch sử (không có phản hồi tải)."""
    end = time.time()
    r = requests.get(f"{PROM}/api/v1/query_range", params={
        "query": "app:cpu_util:avg1m", "start": end - minutes * 60, "end": end, "step": 15}, timeout=15)
    res = r.json()["data"]["result"]
    if not res:
        return "(không có dữ liệu CPU)"
    above = below = 0
    last = -1e9
    events = []
    for t, v in res[0]["values"]:
        t, v = float(t), float(v)
        above = above + 1 if v >= scale_out_threshold else 0
        below = below + 1 if v <= scale_in_threshold else 0
        if above >= out_cycles and t - last >= cooldown_out_s:
            events.append(f"{_local(t)[:8]} scale-out (cpu={v:.0f}%)"); above = 0; last = t
        elif below >= in_cycles and t - last >= cooldown_in_s:
            events.append(f"{_local(t)[:8]} scale-in (cpu={v:.0f}%)"); below = 0; last = t
    return (f"Mô phỏng {minutes} phút gần nhất với ngưỡng out={scale_out_threshold}% / in={scale_in_threshold}%: "
            f"{sum('out' in e for e in events)} lần scale-out, {sum('in (' in e for e in events)} lần scale-in.\n"
            + "\n".join(events) + "\n(Lưu ý: mô phỏng không tính việc thêm máy làm CPU giảm, nên là cận trên.)")


def propose_scaling_action(direction: str, reason: str) -> str:
    """Chỉ TẠO đề xuất ở trạng thái pending. Người vận hành phải xác nhận ở CLI thì mới thực thi."""
    r = requests.post(f"{SCALER}/proposals", json={"direction": direction, "reason": reason}, timeout=10)
    p = r.json()
    if r.status_code >= 300:
        return f"Không tạo được đề xuất: {p}"
    PENDING.append(p)
    return f"Đã tạo đề xuất #{p['id']} (scale-{direction}), đang chờ người vận hành xác nhận."


PENDING: list[dict] = []

REGISTRY = {f.__name__: f for f in (query_prometheus, query_prometheus_range, list_servers, read_scaling_log,
                                    get_autoscaler_status, simulate_thresholds, propose_scaling_action)}

SCHEMAS = [
    {"type": "function", "function": {"name": "query_prometheus", "description":
        "Truy vấn PromQL tức thời (chỉ đọc). Metric hữu ích: app:cpu_util:avg1m (CPU trung bình nhóm app, %), "
        "instance:cpu_util:avg1m (CPU từng VM), app:http_rps:1m, app:http_p95_seconds:1m, "
        "count(up{job=\"node\"}==1) (số VM đang được giám sát), autoscaler_desired_instances, "
        "autoscaler_last_scale_out_seconds{phase=...}.",
        "parameters": {"type": "object", "properties": {"promql": {"type": "string"}}, "required": ["promql"]}}},
    {"type": "function", "function": {"name": "query_prometheus_range", "description":
        "Truy vấn PromQL theo khoảng thời gian gần đây (để xem xu hướng).",
        "parameters": {"type": "object", "properties": {"promql": {"type": "string"},
                       "minutes": {"type": "integer"}, "step_s": {"type": "integer"}}, "required": ["promql"]}}},
    {"type": "function", "function": {"name": "list_servers", "description":
        "Liệt kê máy ảo trong project (Nova API, chỉ đọc): tên, trạng thái, IP, nhóm, thời điểm tạo.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "read_scaling_log", "description":
        "Đọc log quyết định của autoscaler (thời gian giờ VN). event có thể lọc: scale_out, scale_in, alert, error, "
        "cleanup. Sự kiện scale_out_done có phases_s = thời gian từng giai đoạn (giây).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}, "event": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_autoscaler_status", "description":
        "Trạng thái autoscaler: danh sách VM đang quản lý, ngưỡng, min/max, đề xuất đang chờ.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "simulate_thresholds", "description":
        "Trả lời câu hỏi 'nếu đổi ngưỡng/cửa sổ/cooldown thì sao': mô phỏng lại quyết định trên dữ liệu CPU lịch sử.",
        "parameters": {"type": "object", "properties": {
            "scale_out_threshold": {"type": "number"}, "scale_in_threshold": {"type": "number"},
            "minutes": {"type": "integer"}, "out_cycles": {"type": "integer"}, "in_cycles": {"type": "integer"},
            "cooldown_out_s": {"type": "integer"}, "cooldown_in_s": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "propose_scaling_action", "description":
        "Tạo ĐỀ XUẤT thêm (out) hoặc bớt (in) 1 máy ảo. Không thực thi ngay: người vận hành phải xác nhận. "
        "Chỉ dùng khi người dùng yêu cầu hoặc có lý do rõ ràng từ số liệu.",
        "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["out", "in"]},
                       "reason": {"type": "string"}}, "required": ["direction", "reason"]}}},
]
