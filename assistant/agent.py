#!/usr/bin/env python3
"""Trợ lý vận hành AI cho hệ thống autoscaling IT5404.
LLM chạy cục bộ bằng Ollama (mặc định qwen3:8b) với tool-calling; mọi tool đều chỉ đọc,
riêng propose_scaling_action chỉ tạo đề xuất – việc phê duyệt do NGƯỜI dùng thực hiện trong CLI này.

  python agent.py                       # hội thoại
  python agent.py -q "Hiện có bao nhiêu VM?"   # hỏi một câu
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

import tools

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("ASSISTANT_MODEL", "qwen3:8b")

SYSTEM = """Bạn là trợ lý vận hành cho một hệ thống IaaS OpenStack có autoscaling.
Hệ thống: nhóm máy ảo ứng dụng (metadata group=app, flavor app.small 1 vCPU) sau HAProxy; Prometheus giám sát;
autoscaler cứ 15 giây đọc CPU trung bình nhóm (app:cpu_util:avg1m): ≥ ngưỡng scale-out trong 2 chu kỳ liên tiếp
thì thêm 1 VM, ≤ ngưỡng scale-in trong 8 chu kỳ thì bớt 1 VM; có cooldown và giới hạn min/max.
Quy tắc:
- Luôn gọi tool để lấy số liệu thật trước khi trả lời; không bịa số. Nêu rõ số liệu và thời điểm (giờ Việt Nam).
- Trả lời ngắn gọn bằng tiếng Việt.
- Bạn KHÔNG có quyền thay đổi hệ thống. Nếu cần thay đổi, dùng propose_scaling_action để tạo đề xuất
  và nói rõ người vận hành cần xác nhận.
Thời điểm hiện tại: {now}."""


def chat(messages, max_steps=6, log=None):
    """Vòng lặp tool-calling. Trả về (câu trả lời, số lần gọi tool)."""
    calls = 0
    for _ in range(max_steps):
        r = requests.post(f"{OLLAMA}/api/chat", json={
            "model": MODEL, "messages": messages, "tools": tools.SCHEMAS, "stream": False,
            "think": False, "options": {"temperature": 0.1, "num_ctx": 16384}}, timeout=300)
        r.raise_for_status()
        msg = r.json()["message"]
        messages.append(msg)
        if not msg.get("tool_calls"):
            return msg.get("content", "").strip(), calls
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            args = tc["function"].get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            calls += 1
            try:
                result = tools.REGISTRY[name](**args)
            except Exception as e:                       # noqa: BLE001
                result = f"Lỗi khi gọi {name}: {e}"
            if log:
                log(f"  ↳ {name}({json.dumps(args, ensure_ascii=False)}) → {result[:160]!r}")
            messages.append({"role": "tool", "tool_name": name, "content": result[:6000]})
    return "(Dừng: quá nhiều bước gọi tool)", calls


def new_conversation():
    now = datetime.now(timezone(timedelta(hours=7))).strftime("%H:%M:%S ngày %d/%m/%Y")
    return [{"role": "system", "content": SYSTEM.format(now=now)}]


def confirm_pending():
    """Phê duyệt đề xuất: do NGƯỜI thực hiện, không qua LLM."""
    while tools.PENDING:
        p = tools.PENDING.pop(0)
        ans = input(f"\n⚠️  Đề xuất #{p['id']}: scale-{p['direction']} – {p.get('reason')}\n   Phê duyệt? [y/N] ")
        if ans.strip().lower() == "y":
            r = requests.post(f"{tools.SCALER}/proposals/{p['id']}/approve", timeout=10)
            print(f"   → autoscaler trả về HTTP {r.status_code}: {r.json().get('status')}")
        else:
            print("   → Đã bỏ qua đề xuất.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--question")
    ap.add_argument("-v", "--verbose", action="store_true", help="in các lần gọi tool")
    a = ap.parse_args()
    log = (lambda s: print(s, file=sys.stderr)) if a.verbose else None
    if a.question:
        t = time.time()
        ans, n = chat(new_conversation() + [{"role": "user", "content": a.question}], log=log)
        print(ans)
        print(f"\n({n} lần gọi tool, {time.time() - t:.1f}s)", file=sys.stderr)
        confirm_pending()
        return
    msgs = new_conversation()
    print(f"Trợ lý vận hành IT5404 ({MODEL}). Gõ 'exit' để thoát.")
    while True:
        try:
            q = input("\n> ").strip()
        except EOFError:
            break
        if q in ("exit", "quit"):
            break
        if not q:
            continue
        msgs.append({"role": "user", "content": q})
        t = time.time()
        ans, n = chat(msgs, log=log or (lambda s: print(s)))
        print(f"\n{ans}\n({n} tool, {time.time() - t:.1f}s)")
        confirm_pending()


if __name__ == "__main__":
    main()
