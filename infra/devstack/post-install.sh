#!/usr/bin/env bash
# Chuẩn bị tài nguyên OpenStack cho đồ án (chạy sau stack.sh, bằng user 'stack'; idempotent).
#   - flavor app.small (1 vCPU, 1GB RAM, 6GB disk)
#   - mạng tenant app-net 10.10.0.0/24 gắn vào router1 (SNAT ra external)
#   - security group app-sg: 22, 8080, 9100, ICMP
#   - keypair, quota, route từ host vào app-net (để Prometheus/HAProxy chạm tới IP private)
set -eo pipefail
source /opt/stack/devstack/openrc admin admin >/dev/null
P=demo
os() { openstack "$@"; }

os flavor show app.small >/dev/null 2>&1 || os flavor create --vcpus 1 --ram 1024 --disk 6 app.small
os quota set --instances 8 --cores 12 --ram 12288 $P

source /opt/stack/devstack/openrc demo demo >/dev/null
os network show app-net >/dev/null 2>&1 || os network create app-net
os subnet show app-subnet >/dev/null 2>&1 || os subnet create app-subnet --network app-net \
   --subnet-range 10.10.0.0/24 --dns-nameserver 8.8.8.8 --dns-nameserver 1.1.1.1
os router add subnet router1 app-subnet 2>/dev/null || true

if ! os security group show app-sg >/dev/null 2>&1; then
  os security group create app-sg --description "IT5404 app VMs"
  for port in 22 8080 9100; do
    os security group rule create app-sg --protocol tcp --dst-port $port --remote-ip 0.0.0.0/0
  done
  os security group rule create app-sg --protocol icmp
fi

[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 -q
os keypair show it5404 >/dev/null 2>&1 || os keypair create --public-key ~/.ssh/id_ed25519.pub it5404

# Route từ host tới app-net qua cổng external của router1
GW=$(os router show router1 -f json -c external_gateway_info \
     | jq -r '.external_gateway_info.external_fixed_ips[] | select(.ip_address|test("^172\\.")) .ip_address')
sudo ip route replace 10.10.0.0/24 via "$GW"
# SNAT cho dải floating ra Internet qua card mạng của host (phòng trường hợp devstack chưa thêm)
DEV=$(ip route show default | awk 'NR==1{print $5}')
sudo iptables -t nat -C POSTROUTING -s 172.24.4.0/24 -o "$DEV" -j MASQUERADE 2>/dev/null \
  || sudo iptables -t nat -A POSTROUTING -s 172.24.4.0/24 -o "$DEV" -j MASQUERADE
echo "OK: router1 gw=$GW, route 10.10.0.0/24 via $GW"
