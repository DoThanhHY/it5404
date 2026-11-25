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
# Huge pages 2MB cho VM: trong nested virt, VM không có trang lớn chạy chậm ~25-40 lần (xem docs/troubleshooting.md)
os flavor set app.small --property hw:mem_page_size=large
echo "vm.nr_hugepages = 2560" | sudo tee /etc/sysctl.d/90-it5404-hugepages.conf >/dev/null
sudo sysctl -q -p /etc/sysctl.d/90-it5404-hugepages.conf
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

# Mạng host (br-ex, route app-net, NAT) – cài thành systemd service để tự chạy lại khi boot
sudo mkdir -p /opt/stack/it5404
sudo install -m755 "$(dirname "$0")/host-network.sh" /opt/stack/it5404/host-network.sh
sudo install -m644 "$(dirname "$0")/it5404-host-network.service" /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable it5404-host-network >/dev/null 2>&1
sudo systemctl restart it5404-host-network && systemctl status it5404-host-network --no-pager -n 1 | tail -1
