#!/usr/bin/env bash
# Chạy JMeter (non-GUI) từ Mac vào HAProxy, xuất kết quả + HTML report.
#   ./run-load.sh                 # profile chuẩn ~25 phút
#   ./run-load.sh -Jpeak=12 -Jn=30000
set -euo pipefail
cd "$(dirname "$0")"
TS=$(date +%Y%m%d-%H%M%S); OUT=results/$TS; mkdir -p "$OUT"
jmeter -n -t step-load.jmx -l "$OUT/samples.jtl" -e -o "$OUT/report" "$@"
echo "Kết quả: $OUT/report/index.html"
