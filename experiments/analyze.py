#!/usr/bin/env python3
"""Phân tích kết quả thực nghiệm và vẽ biểu đồ cho báo cáo -> docs/figures/*.png + docs/figures/summary.json
   experiments/.venv/bin/python experiments/analyze.py"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FIG = ROOT / "docs" / "figures"
FIG.mkdir(parents=True, exist_ok=True)
# bảng màu categorical đã kiểm định (dataviz skill, light mode) – dùng theo thứ tự cố định
S = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold", "axes.titlesize": 10,
    "axes.titlecolor": INK, "legend.frameon": False, "figure.dpi": 150, "savefig.bbox": "tight",
    "lines.linewidth": 2,
})
summary: dict = {}


def save(fig, name):
    fig.savefig(FIG / name, facecolor="#fcfcfb")
    plt.close(fig)
    print("  ->", FIG / name)


def jtl_stats(path, t_from=None, t_to=None, slo_ms=1000):
    df = pd.read_csv(path, usecols=["timeStamp", "elapsed", "success", "responseCode"])
    t0 = df.timeStamp.min()
    df["t"] = (df.timeStamp - t0) / 1000
    if t_from is not None:
        df = df[(df.t >= t_from) & (df.t < t_to)]
    return {"requests": int(len(df)), "error_pct": round(100 * (1 - df.success.mean()), 3),
            "p50_ms": float(df.elapsed.quantile(.5)), "p95_ms": float(df.elapsed.quantile(.95)),
            "p99_ms": float(df.elapsed.quantile(.99)), "slo_violation_pct": round(100 * (df.elapsed > slo_ms).mean(), 2)}


def scale_events(dec):
    return [e for e in dec if e["event"] in ("scale_out_done", "scale_in_done")]


def flaps(dec, window_s=300):
    """Số lần đảo chiều: scale-in rồi scale-out (hoặc ngược lại) trong vòng window_s."""
    ev = [(pd.Timestamp(e["ts"]).timestamp(), e["event"][:9]) for e in scale_events(dec)]
    return sum(1 for (t1, a), (t2, b) in zip(ev, ev[1:]) if a != b and t2 - t1 <= window_s)


def vm_hours(ts):
    return float(ts.vms.fillna(0).sum() * 15 / 3600)


# ---------------- 3a/3b: 3 lần chạy chuẩn ----------------
runs = sorted(glob.glob(str(ROOT / "loadtest/results/standard-*")))
if runs:
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.2, 5.2), sharex=True)
    rows = []
    for i, r in enumerate(runs):
        ts = pd.read_csv(f"{r}/timeseries.csv")
        dec = json.load(open(f"{r}/decisions.json"))
        lab = f"Lần {i + 1}"
        a1.plot(ts.t_s / 60, ts.cpu_avg, color=S[i], label=lab)
        a2.step(ts.t_s / 60, ts.vms, where="post", color=S[i], label=lab)
        st = jtl_stats(f"{r}/samples.jtl")
        outs = [e for e in dec if e["event"] == "scale_out_done"]
        rows.append({"run": i + 1, **st, "scale_out": len(outs),
                     "scale_in": sum(e["event"] == "scale_in_done" for e in dec), "max_vms": int(ts.vms.max()),
                     "vm_hours": round(vm_hours(ts), 3),
                     "scale_out_total_s": [e["phases_s"]["total"] for e in outs]})
    for y in (80, 20):
        a1.axhline(y, color=INK2, lw=1, ls="--")
        a1.text(0.2, y + 2, f"ngưỡng {y}%", color=INK2, fontsize=8)
    a1.set(ylabel="CPU trung bình nhóm (%)", ylim=(0, 105), title="CPU trung bình nhóm VM app theo thời gian")
    a2.set(ylabel="Số VM app", xlabel="Thời gian từ lúc bắt đầu tải (phút)", title="Số máy ảo (autoscaler)")
    a2.set_yticks(range(0, 5))
    a1.legend(loc="upper right", ncol=3)
    for a in (a1, a2):
        a.axvspan(5, 13, color=GRID, alpha=.35, lw=0)
    a1.text(9, 98, "giai đoạn tải cao", ha="center", color=INK2, fontsize=8)
    save(fig, "standard-runs.png")
    summary["standard_runs"] = rows

# ---------------- RQ1/RQ2: phân rã thời gian scale-out ----------------
bench = {c: pd.read_csv(p) for c in ("C0", "C1", "C2")
         if os.path.exists(p := ROOT / f"experiments/results/rq2/{c}.csv")}
if bench:
    phases = []
    for c, df in bench.items():
        active = df.api_s + df.schedule_spawn_s
        boot_to_ci = df.cloudinit_done_s - active
        ci_to_jvm = (df.jvm_start_s - df.cloudinit_done_s).clip(lower=0)
        jvm_to_ready = df.ready_s - df[["jvm_start_s", "cloudinit_done_s"]].max(axis=1)
        phases.append({"config": c, "API + lập lịch + tạo VM (đến ACTIVE)": active.median(),
                       "Boot hệ điều hành + cloud-init": boot_to_ci.median(),
                       "Chờ đến khi JVM khởi chạy": ci_to_jvm.median(),
                       "Khởi động JVM + Spring đến khi sẵn sàng": jvm_to_ready.median(),
                       "ready_median": df.ready_s.median(), "ready_p90": df.ready_s.quantile(.9),
                       "ready_min": df.ready_s.min(), "ready_max": df.ready_s.max(),
                       "spring_median": df.spring_start_s.median(), "n": len(df)})
    P = pd.DataFrame(phases).set_index("config")
    cols = [c for c in P.columns if not c.startswith(("ready", "spring", "n"))]
    fig, ax = plt.subplots(figsize=(7.2, 2.2 + 0.45 * len(P)))
    left = pd.Series(0.0, index=P.index)
    for i, c in enumerate(cols):
        ax.barh(P.index, P[c], left=left, color=S[i], label=c, height=0.55, edgecolor="#fcfcfb", linewidth=2)
        left += P[c]
    for cfg, tot in P.ready_median.items():
        ax.text(tot + 2, cfg, f"{tot:.0f} s", va="center", color=INK, fontsize=9)
    ax.invert_yaxis()
    ax.set(xlabel="Thời gian từ lúc gọi API (giây, trung vị)", title="RQ1 – Phân rã thời gian scale-out theo cấu hình")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=2, fontsize=8)
    ax.grid(axis="y", visible=False)
    save(fig, "rq1-breakdown.png")
    summary["rq1_rq2_bench"] = json.loads(P.round(2).reset_index().to_json(orient="records", force_ascii=False))

# ---------------- RQ2: % request vượt SLO khi tải tăng ----------------
slo = {}
for c in ("C0", "C1", "C2"):
    p = ROOT / f"experiments/results/rq2-slo/{c}/samples.jtl"
    if c == "C1" and not p.exists():
        p = ROOT / "experiments/results/rq3/P1/samples.jtl"      # C1 = cấu hình mặc định của RQ3-P1
    if p.exists():
        slo[c] = {"tang_tai_300_900s": jtl_stats(p, 300, 900), "toan_bo": jtl_stats(p)}
if slo:
    fig, ax = plt.subplots(figsize=(5.2, 2.6))
    xs = list(slo)
    vals = [slo[c]["tang_tai_300_900s"]["slo_violation_pct"] for c in xs]
    ax.bar(xs, vals, color=S[0], width=0.5)
    for x, v in zip(xs, vals):
        ax.text(x, v, f"{v:.1f}%", ha="center", va="bottom", color=INK, fontsize=9)
    ax.set(ylabel="% request > 1 s", title="RQ2 – Request vượt SLO trong giai đoạn tải tăng (300–900 s)")
    ax.grid(axis="x", visible=False)
    save(fig, "rq2-slo.png")
    summary["rq2_slo"] = slo

# ---------------- RQ3: bộ tham số ----------------
rq3 = []
for d in sorted(glob.glob(str(ROOT / "experiments/results/rq3/P*"))):
    ts = pd.read_csv(f"{d}/timeseries.csv")
    dec = json.load(open(f"{d}/decisions.json"))
    p = open(f"{d}/params.txt").read().split()
    st = jtl_stats(f"{d}/samples.jtl")
    rq3.append({"set": p[0], "params": f"{p[1]}/{p[2]}%, N={p[3]}, M={p[4]}, cd={p[5]}/{p[6]}s",
                "scale_events": len(scale_events(dec)), "flaps_5min": flaps(dec), "vm_hours": round(vm_hours(ts), 3),
                "p95_ms": st["p95_ms"], "slo_violation_pct": st["slo_violation_pct"], "error_pct": st["error_pct"],
                "ts": ts})
if rq3:
    fig, axes = plt.subplots(len(rq3), 1, figsize=(7.2, 1.25 * len(rq3) + 0.6), sharex=True, sharey=True)
    for i, (r, ax) in enumerate(zip(rq3, axes)):
        ax.step(r["ts"].t_s / 60, r["ts"].vms, where="post", color=S[0])
        ax.set_title(f"{r['set']}: {r['params']}", loc="left", fontsize=8.5)
        ax.set_yticks(range(0, 5))
    axes[-1].set_xlabel("Thời gian từ lúc bắt đầu tải (phút)")
    fig.supylabel("Số VM app", color=INK2, fontsize=9)
    fig.suptitle("RQ3 – Số máy ảo theo thời gian với các bộ tham số", fontweight="bold", fontsize=10)
    save(fig, "rq3-vms.png")
    summary["rq3"] = [{k: v for k, v in r.items() if k != "ts"} for r in rq3]

# ---------------- Trợ lý AI ----------------
ev = ROOT / "assistant/eval-results.json"
if ev.exists():
    summary["assistant_eval"] = json.load(open(ev))["summary"]

json.dump(summary, open(FIG / "summary.json", "w"), ensure_ascii=False, indent=1, default=float)
print(json.dumps({k: (v if k != "standard_runs" else "...") for k, v in summary.items()}, ensure_ascii=False,
                 default=str)[:3000])
