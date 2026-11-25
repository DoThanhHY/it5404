#!/usr/bin/env bash
# RQ2 (phần SLO) – chạy profile tải chuẩn khi autoscaler dùng image của từng cấu hình (C0/C1/C2),
# đo % request vượt SLO (latency > 1 s) trong giai đoạn tải tăng. Chạy từ Mac: experiments/rq2_slo.sh [C0 C1 C2]
set -euo pipefail
cd "$(dirname "$0")"
if [ $# -eq 0 ]; then set -- C0 C1 C2; fi
CFG=/opt/stack/autoscaler/config.yaml
for C in "$@"; do
  case $C in
    C0) IMG=ubuntu-24.04-base; UD=/Users/thanhdo/dientoandammay/infra/image/cloud-init-c0.yaml;;
    C1) IMG=app-golden; UD=null;;
    C2) IMG=app-golden-c2; UD=null;;
  esac
  echo "== $C: image=$IMG"
  limactl shell openstack sudo -u stack bash -c "
    cp /Users/thanhdo/dientoandammay/autoscaler/config.yaml $CFG
    sed -i -e 's|^  image:.*|  image: $IMG|' -e 's|^  user_data_file:.*|  user_data_file: $UD|' $CFG
    sudo systemctl restart autoscaler"
  [ "$C" = C0 ] && limactl shell openstack bash -c "pgrep -f 'http.server 8000' >/dev/null || (cd /Users/thanhdo/dientoandammay/app/target && nohup python3 -m http.server 8000 --bind 172.24.4.1 >/dev/null 2>&1 &)"
  until [ "$(curl -s http://192.168.64.2:9180/status | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["instances"]))')" = 1 ]; do sleep 20; done
  sleep 60
  S=$(date +%s); OUT=results/rq2-slo/$C; rm -rf "$OUT"; mkdir -p "$OUT"
  jmeter -n -t ../loadtest/step-load.jmx -l "$OUT/samples.jtl" > "$OUT/jmeter.log" 2>&1
  sleep 300; E=$(date +%s)
  python3 ../loadtest/collect.py "$OUT" "$S" "$E"
done
limactl shell openstack bash -c "pkill -f 'http.server 8000' || true"
limactl shell openstack sudo -u stack bash -c "cp /Users/thanhdo/dientoandammay/autoscaler/config.yaml $CFG && sudo systemctl restart autoscaler"
