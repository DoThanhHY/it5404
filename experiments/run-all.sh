#!/usr/bin/env bash
# Chạy toàn bộ phần mở rộng nối tiếp (~5 giờ). Chạy từ Mac: experiments/run-all.sh
set -uo pipefail
cd "$(dirname "$0")"
R=/Users/thanhdo/dientoandammay
log(){ echo "[$(date +%H:%M:%S)] $*"; }
L="limactl shell openstack sudo -u stack -H bash -lc"

log "1. Cập nhật autoscaler (endpoint /decisions, API 0.0.0.0:9180)"
$L "$R/autoscaler/install.sh" >/dev/null 2>&1; sleep 5
curl -s -o /dev/null -w "   /decisions HTTP %{http_code}\n" http://192.168.64.2:9180/decisions?limit=1

log "2. Build image C2 (JDK 25 + AOT cache) và upload"
limactl shell openstack sudo bash -c "VARIANT=c2 $R/infra/image/build-golden-image.sh" 2>&1 | grep -E "app.aot|Golden|rror" | tail -4
$L "ONLY=app-golden-c2 $R/infra/image/upload-images.sh" 2>&1 | grep -E "^\| status" | tail -1

log "3. RQ1/RQ2: đo scale-out 10 lần mỗi cấu hình"
for C in C1 C2 C0; do
  $L "cd $R/experiments && /opt/stack/autoscaler/venv/bin/python rq_scaleout.py --config $C --runs 10" 2>&1 | tail -11
done

log "4. RQ2-SLO: profile tải với C0 và C2 (C1 dùng RQ3-P1)"
./rq2_slo.sh C0 C2 2>&1 | grep -E "==|điểm"

log "5. RQ3: 4 bộ tham số"
./rq3_params.sh P1 P2 P3 P4 2>&1 | grep -E "==|điểm"

log "6. Đánh giá trợ lý AI (20 câu)"
(cd $R/assistant && .venv/bin/python eval.py) 2>&1 | tail -3

log "7. Phân tích + vẽ biểu đồ"
.venv/bin/python analyze.py 2>&1 | tail -3
log "XONG"
