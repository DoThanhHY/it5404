#!/usr/bin/env bash
# Khôi phục mạng của VM host sau khi khởi động lại (những thứ DevStack cấu hình nhưng không bền vững):
#   - IP 172.24.4.1/24 trên br-ex (gateway mạng external/floating)
#   - route 10.10.0.0/24 (app-net) qua cổng external của router1 -> Prometheus/HAProxy chạm tới VM
#   - NAT dải floating ra Internet
#   - huge pages cho VM (xem docs: nested virt cần trang 2MB)
set -eo pipefail
ip link set br-ex up
ip addr show br-ex | grep -q "172.24.4.1/24" || ip addr add 172.24.4.1/24 dev br-ex
sysctl -q -w net.ipv4.ip_forward=1
DEV=$(ip route show default | awk 'NR==1{print $5}')
iptables -t nat -C POSTROUTING -s 172.24.4.0/24 -o "$DEV" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 172.24.4.0/24 -o "$DEV" -j MASQUERADE
GW=""
for i in $(seq 1 60); do        # chờ Neutron sẵn sàng
  GW=$(sudo -u stack -H bash -c 'source /opt/stack/devstack/openrc demo demo >/dev/null 2>&1;
       openstack router show router1 -f json -c external_gateway_info 2>/dev/null' \
       | jq -r '.external_gateway_info.external_fixed_ips[]? | select(.ip_address|test("^172\\.")) .ip_address' || true)
  [ -n "$GW" ] && break; sleep 10
done
[ -n "$GW" ] && ip route replace 10.10.0.0/24 via "$GW"
echo "host-network OK (router1 gw=$GW)"
