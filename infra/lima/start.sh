#!/usr/bin/env bash
# Tạo/khởi động VM host "openstack" và mount repo (ở bất kỳ đâu trong $HOME) vào VM với cùng đường dẫn.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
if limactl list -q 2>/dev/null | grep -qx openstack; then
  limactl start openstack
else
  limactl start --name=openstack --tty=false --set ".mounts[0].location=\"$REPO\"" "$REPO/infra/lima/openstack-host.yaml"
fi
IP=$(limactl shell openstack ip -4 -o addr show lima0 | awk '{print $4}' | cut -d/ -f1)
echo "VM host IP (lima0): $IP"
if [ "$IP" != "$(grep -oE '^HOST_IP=[0-9.]+' "$REPO/infra/devstack/local.conf" | cut -d= -f2)" ]; then
  echo "⚠️  IP khác với cấu hình trong repo -> chạy: infra/set-host-ip.sh $IP"
fi
