#!/usr/bin/env python3
"""Sinh các bảng LaTeX cho báo cáo từ docs/figures/summary.json -> docs/report/generated/*.tex.
Thiếu số liệu thì sinh ghi chú "đang chạy thực nghiệm" để báo cáo vẫn biên dịch được."""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/report/generated"
OUT.mkdir(parents=True, exist_ok=True)
sj = ROOT / "docs/figures/summary.json"
S = json.load(open(sj)) if sj.exists() else {}
PENDING = r"\textit{(Số liệu đang được thu thập – bảng sẽ tự cập nhật khi thực nghiệm hoàn tất.)}"


def w(name, body):
    (OUT / name).write_text(body)
    print("  ->", OUT / name)


def f(x, d=1):
    return f"{x:.{d}f}".replace(".", ",") if isinstance(x, (int, float)) else str(x)


def flag(name, ok):
    (OUT / name).write_text(r"\newif\if" + name.replace(".tex", "").replace("-", "") + "\n" +
                            "\\" + name.replace(".tex", "").replace("-", "") + ("true" if ok else "false") + "\n")


# --- 3 lần chạy chuẩn ---
rows = S.get("standard_runs", [])
if rows:
    body = [r"\begin{tabular}{@{}crrrrrrr@{}}", r"\toprule",
            r"Lần & Request & Lỗi (\%) & p50 (ms) & p95 (ms) & p99 (ms) & Scale-out/in & VM tối đa \\", r"\midrule"]
    for r in rows:
        body.append(f"{r['run']} & {r['requests']:,} & {f(r['error_pct'], 2)} & {f(r['p50_ms'], 0)} & ".replace(",", ".", 1)
                    + f"{f(r['p95_ms'], 0)} & {f(r['p99_ms'], 0)} & {r['scale_out']}/{r['scale_in']} & {r['max_vms']} \\\\")
    body += [r"\bottomrule", r"\end{tabular}"]
    tot = [t for r in rows for t in r["scale_out_total_s"]]
    w("standard.tex", "\n".join(body))
    w("standard-scaleout.tex", f"Trung vị thời gian scale-out (từ lúc quyết định đến khi VM mới nhận request) qua "
      f"{len(tot)} lần là {f(statistics.median(tot))}~s (nhỏ nhất {f(min(tot))}~s, lớn nhất {f(max(tot))}~s)." if tot else "")
else:
    w("standard.tex", PENDING); w("standard-scaleout.tex", "")
flag("havestandard.tex", bool(rows))

# --- RQ1/RQ2 ---
b = S.get("rq1_rq2_bench", [])
if b:
    keys = ["API + lập lịch + tạo VM (đến ACTIVE)", "Boot hệ điều hành + cloud-init", "Chờ đến khi JVM khởi chạy",
            "Khởi động JVM + Spring đến khi sẵn sàng"]
    body = [r"\begin{tabular}{@{}lrrrrrr@{}}", r"\toprule",
            r"Cấu hình & Đến ACTIVE & Boot+cloud-init & JVM+Spring & Tổng (trung vị) & p90 & Spring (s) \\",
            r"\midrule"]
    for r in b:
        body.append(f"{r['config']} & {f(r[keys[0]])} & {f(r[keys[1]])} & {f(r[keys[3]])} & "
                    f"\\textbf{{{f(r['ready_median'])}}} & {f(r['ready_p90'])} & {f(r['spring_median'])} \\\\")
    body += [r"\bottomrule", r"\end{tabular}"]
    w("rq12.tex", "\n".join(body))
else:
    w("rq12.tex", PENDING)
flag("haverqone.tex", bool(b))

slo = S.get("rq2_slo", {})
if slo:
    body = [r"\begin{tabular}{@{}lrrrr@{}}", r"\toprule",
            r"Cấu hình & Request (300–900 s) & p95 (ms) & p99 (ms) & Vượt SLO 1 s (\%) \\", r"\midrule"]
    for c, v in slo.items():
        t = v["tang_tai_300_900s"]
        body.append(f"{c} & {t['requests']} & {f(t['p95_ms'], 0)} & {f(t['p99_ms'], 0)} & {f(t['slo_violation_pct'], 2)} \\\\")
    body += [r"\bottomrule", r"\end{tabular}"]
    w("rq2slo.tex", "\n".join(body))
else:
    w("rq2slo.tex", PENDING)
flag("haveslo.tex", bool(slo))

# --- RQ3 ---
r3 = S.get("rq3", [])
if r3:
    body = [r"\begin{tabular}{@{}llrrrrr@{}}", r"\toprule",
            r"Bộ & Tham số (out/in, N, M, cooldown) & Sự kiện scale & Đảo chiều & VM-giờ & p95 (ms) & Vượt SLO (\%) \\",
            r"\midrule"]
    for r in r3:
        body.append(f"{r['set']} & {r['params'].replace('%', r'\%')} & {r['scale_events']} & {r['flaps_5min']} & "
                    f"{f(r['vm_hours'], 2)} & {f(r['p95_ms'], 0)} & {f(r['slo_violation_pct'], 2)} \\\\")
    body += [r"\bottomrule", r"\end{tabular}"]
    w("rq3.tex", "\n".join(body))
else:
    w("rq3.tex", PENDING)
flag("haverqthree.tex", bool(r3))

# --- Trợ lý AI ---
a = S.get("assistant_eval")
w("assistant.tex", (f"Trên bộ {a['n']} câu hỏi, trợ lý ({a['model']}) trả lời đúng "
                    f"\\textbf{{{f(100 * a['accuracy'], 0)}\\%}}; độ trễ trả lời trung vị {f(a['latency_median_s'])}~s, "
                    f"p90 {f(a['latency_p90_s'])}~s (Ollama trên GPU của Mac M4).") if a else PENDING)
