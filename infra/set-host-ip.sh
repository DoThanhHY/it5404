#!/usr/bin/env bash
# Đổi IP VM host trong mọi file cấu hình của repo (mặc định 192.168.64.2).  infra/set-host-ip.sh <IP mới>
set -euo pipefail
NEW=${1:?Dùng: $0 <IP mới>}
REPO=$(cd "$(dirname "$0")/.." && pwd)
OLD=$(grep -oE '^HOST_IP=[0-9.]+' "$REPO/infra/devstack/local.conf" | cut -d= -f2)
[ "$OLD" = "$NEW" ] && { echo "Đã là $NEW"; exit 0; }
cd "$REPO"
FILES=$(git grep -l -F "$OLD" -- ':!*.md' ':!*.png' || true)
for f in $FILES; do sed -i.bak "s/${OLD//./\\.}/$NEW/g" "$f" && rm -f "$f.bak"; echo "  $f"; done
echo "Đã đổi $OLD -> $NEW. Nếu DevStack đã cài: chạy lại monitoring/install.sh và autoscaler/install.sh trong VM."
