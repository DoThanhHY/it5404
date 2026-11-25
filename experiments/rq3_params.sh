#!/usr/bin/env bash
# RQ3 – ảnh hưởng của bộ tham số autoscaler (ngưỡng, cửa sổ đánh giá, cooldown) tới flapping, VM-giờ, p95.
# Chạy từ Mac:  experiments/rq3_params.sh [P1 P2 P3 P4]
# Mỗi bộ: ghi config -> restart autoscaler -> chờ về min -> chạy profile JMeter chuẩn -> thu số liệu.
set -euo pipefail
cd "$(dirname "$0")"
params(){ case "$1" in   # "out_thr in_thr out_cycles in_cycles cooldown_out cooldown_in"
  P1) echo "80 20 2 8 60 120";;     # mặc định
  P2) echo "70 30 1 2 15 15";;      # nhạy: phản ứng nhanh, dễ flapping
  P3) echo "85 15 4 16 120 300";;   # bảo thủ
  P4) echo "70 30 1 8 30 120";;     # nhạy khi tăng, chậm khi giảm (bất đối xứng)
esac; }
if [ $# -eq 0 ]; then set -- P1 P2 P3 P4; fi
CFG=/opt/stack/autoscaler/config.yaml
for P in "$@"; do
  read -r OT IT OC IC CO CI <<<"$(params "$P")"
  echo "== $P: out=$OT in=$IT out_cycles=$OC in_cycles=$IC cooldown=$CO/$CI"
  limactl shell openstack sudo -u stack bash -c "
    cp /Users/thanhdo/dientoandammay/autoscaler/config.yaml $CFG
    sed -i -e 's/^scale_out_threshold:.*/scale_out_threshold: $OT/' -e 's/^scale_in_threshold:.*/scale_in_threshold: $IT/' \
           -e 's/^scale_out_cycles:.*/scale_out_cycles: $OC/' -e 's/^scale_in_cycles:.*/scale_in_cycles: $IC/' \
           -e 's/^cooldown_out_s:.*/cooldown_out_s: $CO/' -e 's/^cooldown_in_s:.*/cooldown_in_s: $CI/' $CFG
    sudo systemctl restart autoscaler"
  # chờ hệ thống về 1 VM
  until [ "$(curl -s http://192.168.64.2:9180/status | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["instances"]))')" = 1 ]; do sleep 20; done
  sleep 60
  S=$(date +%s); OUT=results/rq3/$P; rm -rf "$OUT"; mkdir -p "$OUT"
  echo "$P $OT $IT $OC $IC $CO $CI" > "$OUT/params.txt"
  jmeter -n -t ../loadtest/step-load.jmx -l "$OUT/samples.jtl" > "$OUT/jmeter.log" 2>&1
  sleep 300; E=$(date +%s)
  python3 ../loadtest/collect.py "$OUT" "$S" "$E"
done
# khôi phục cấu hình mặc định
limactl shell openstack sudo -u stack bash -c "cp /Users/thanhdo/dientoandammay/autoscaler/config.yaml $CFG && sudo systemctl restart autoscaler"
