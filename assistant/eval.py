#!/usr/bin/env python3
"""Đánh giá trợ lý AI: 20 câu hỏi, đáp án đúng được tính TỰ ĐỘNG từ nguồn dữ liệu thật (Prometheus, Nova,
log autoscaler) ngay trước khi hỏi. Đo tỷ lệ trả lời đúng và độ trễ. Kết quả: eval-results.json"""
from __future__ import annotations

import json
import re
import statistics
import time

import requests

import agent
import tools


def prom(q):
    r = requests.get(f"{tools.PROM}/api/v1/query", params={"query": q}, timeout=10).json()["data"]["result"]
    return r


def num(q):
    r = prom(q)
    return float(r[0]["value"][1]) if r else None


def decisions():
    return requests.get(f"{tools.SCALER}/decisions", params={"limit": 500}, timeout=10).json()


def status():
    return requests.get(f"{tools.SCALER}/status", timeout=10).json()


def numbers_in(text):
    return [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", text)]


def expect_num(v, tol_abs=0.0, tol_rel=0.0):
    def check(ans):
        tol = max(tol_abs, abs(v) * tol_rel)
        return any(abs(x - v) <= tol for x in numbers_in(ans))
    return check, f"{v:g} (±{max(tol_abs, abs(v) * tol_rel):.2g})"


def expect_text(*alts):
    return (lambda ans: any(a.lower() in ans.lower() for a in alts)), " | ".join(alts)


def hhmm(ts):
    return tools._local(ts)[:5]


def build_cases():
    d = decisions()
    outs = [e for e in d if e["event"] == "scale_out_done"]
    ins = [e for e in d if e["event"] == "scale_in_done"]
    errs = [e for e in d if e["event"].endswith("_error")]
    st = status()
    cfg = next((e["config"] for e in reversed(d) if e["event"] == "autoscaler_start"), {})
    inst = prom("instance:cpu_util:avg1m")
    top = max(inst, key=lambda s: float(s["value"][1]))["metric"]["instance"] if inst else "-"
    last_out = outs[-1] if outs else None
    start_of_last = next((e for e in reversed(d) if e["event"] == "scale_out_start" and last_out
                          and e.get("name") == last_out["name"]), None)
    phase_names = {"api": ["api"], "boot_to_active": ["boot", "active"],
                   "active_to_app_ready": ["ứng dụng", "app", "sẵn sàng", "ready"], "lb_register": ["haproxy", "lb"]}
    slowest = max((k for k in last_out["phases_s"] if k != "total"), key=lambda k: last_out["phases_s"][k]) if last_out else None
    sim = tools.simulate_thresholds(scale_out_threshold=70, minutes=60)
    sim_n = int(re.search(r"(\d+) lần scale-out", sim).group(1))
    newest = max((s for s in requests.get(f"{tools.NOVA}/servers/detail", headers={"X-Auth-Token": tools._nova_token()},
                  timeout=10).json()["servers"] if s["metadata"].get("group") == "app"), key=lambda s: s["created"])
    newest_ip = [a["addr"] for net in newest["addresses"].values() for a in net][0]
    C = [
        ("Hiện có bao nhiêu máy ảo nhóm app đang chạy?", *expect_num(len(st["instances"]))),
        ("CPU trung bình hiện tại của nhóm máy ảo app là bao nhiêu phần trăm?", *expect_num(num("app:cpu_util:avg1m"), 5)),
        ("Máy ảo nào đang có CPU cao nhất?", *expect_text(top)),
        ("Ngưỡng CPU để scale-out hiện tại là bao nhiêu?", *expect_num(cfg.get("scale_out_threshold", 80))),
        ("Ngưỡng CPU để scale-in là bao nhiêu?", *expect_num(cfg.get("scale_in_threshold", 20))),
        ("Autoscaler cho phép tối đa bao nhiêu máy ảo?", *expect_num(cfg.get("max_instances", 4))),
        ("Số máy ảo tối thiểu mà autoscaler luôn giữ là bao nhiêu?", *expect_num(cfg.get("min_instances", 1))),
        ("Lần scale-out gần nhất hoàn tất lúc mấy giờ?", *expect_text(hhmm(last_out["ts"]))),
        ("Lần scale-out gần nhất mất tổng cộng bao nhiêu giây từ lúc quyết định đến khi máy mới nhận request?",
         *expect_num(last_out["phases_s"]["total"], 1.0)),
        ("Trong lần scale-out gần nhất, giai đoạn nào tốn thời gian nhất?", *expect_text(*phase_names[slowest])),
        ("Vì sao hệ thống thêm máy ảo ở lần scale-out gần nhất? CPU lúc đó là bao nhiêu?",
         *expect_num(start_of_last.get("cpu", 0) if start_of_last else 0, 1.0)),
        ("Lần scale-in gần nhất đã xóa máy ảo nào?", *expect_text(ins[-1]["name"] if ins else "không")),
        ("Trong log có bao nhiêu lần scale-out thành công?", *expect_num(len(outs))),
        ("Trong log có bao nhiêu lần scale-in?", *expect_num(len(ins))),
        ("Autoscaler có gặp lỗi khi scale không? Nếu có thì lỗi gì?",
         *(expect_text("No valid host", "400", "lỗi") if errs else expect_text("không"))),
        ("Địa chỉ IP của máy ảo app được tạo gần đây nhất là gì?", *expect_text(newest_ip)),
        ("Hiện tại có bao nhiêu request mỗi giây vào endpoint /work?", *expect_num(num("app:http_rps:1m") or 0, 2, 0.2)),
        ("Prometheus đang giám sát được bao nhiêu máy ảo (target node đang up)?",
         *expect_num(num('count(up{job="node"}==1)') or 0)),
        ("Nếu hạ ngưỡng scale-out xuống 70% thì trong 60 phút qua sẽ có bao nhiêu lần scale-out?", *expect_num(sim_n)),
        ("Độ trễ p95 cao nhất của /work trong 30 phút qua là bao nhiêu mili giây?",
         *expect_num(1000 * (num("max_over_time(app:http_p95_seconds:1m[30m])") or 0), 5, 0.15)),
    ]
    return C


def main():
    cases = build_cases()
    results = []
    for i, (q, check, truth) in enumerate(cases, 1):
        t = time.time()
        try:
            ans, n = agent.chat(agent.new_conversation() + [{"role": "user", "content": q}])
        except Exception as e:                      # noqa: BLE001
            ans, n = f"ERROR {e}", 0
        dt = time.time() - t
        ok = bool(check(ans))
        results.append({"id": i, "question": q, "truth": truth, "answer": ans, "correct": ok,
                        "tool_calls": n, "latency_s": round(dt, 1)})
        print(f"{i:2d}. {'✔' if ok else '✘'} {dt:5.1f}s  {q}\n      đáp án: {truth}\n      trả lời: {ans[:200]!r}")
        tools.PENDING.clear()
    acc = sum(r["correct"] for r in results) / len(results)
    lat = [r["latency_s"] for r in results]
    summary = {"model": agent.MODEL, "accuracy": acc, "latency_median_s": statistics.median(lat),
               "latency_p90_s": sorted(lat)[int(0.9 * len(lat)) - 1], "n": len(results)}
    print(json.dumps(summary, ensure_ascii=False))
    json.dump({"summary": summary, "results": results}, open("eval-results.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
