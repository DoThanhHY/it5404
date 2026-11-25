#!/usr/bin/env bash
# Chạy profile chuẩn N lần (mặc định 3), mỗi lần nghỉ 4 phút cho hệ thống scale-in về min, rồi thu số liệu.
set -euo pipefail
cd "$(dirname "$0")"
N=${1:-3}
for i in $(seq 1 "$N"); do
  S=$(date +%s); TS=$(date +%Y%m%d-%H%M%S); OUT=results/standard-$i-$TS; mkdir -p "$OUT"
  jmeter -n -t step-load.jmx -l "$OUT/samples.jtl" -e -o "$OUT/report" > "$OUT/jmeter.log" 2>&1
  sleep 240; E=$(date +%s)
  python3 collect.py "$OUT" "$S" "$E"
  grep "summary =" "$OUT/jmeter.log" | tail -1
done
