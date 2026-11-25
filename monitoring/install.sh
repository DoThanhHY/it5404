#!/usr/bin/env bash
# Cài Prometheus + Alertmanager (binary, systemd) và Grafana (apt) lên VM host. Chạy: sudo ./install.sh
# Không dùng Docker vì Docker đổi chính sách iptables FORWARD, làm hỏng mạng Neutron/br-ex.
set -euo pipefail
cd "$(dirname "$0")"
ARCH=arm64
latest() { curl -fsSL "https://api.github.com/repos/prometheus/$1/releases/latest" | jq -r .tag_name | sed 's/^v//'; }
PROM_VER=${PROM_VER:-$(latest prometheus)}
AM_VER=${AM_VER:-$(latest alertmanager)}
echo "Prometheus $PROM_VER, Alertmanager $AM_VER"

id prometheus >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin prometheus
mkdir -p /etc/prometheus /var/lib/prometheus /etc/alertmanager /var/lib/alertmanager
cd /tmp
curl -fsSL "https://github.com/prometheus/prometheus/releases/download/v$PROM_VER/prometheus-$PROM_VER.linux-$ARCH.tar.gz" | tar xz
install -m755 prometheus-$PROM_VER.linux-$ARCH/{prometheus,promtool} /usr/local/bin/
curl -fsSL "https://github.com/prometheus/alertmanager/releases/download/v$AM_VER/alertmanager-$AM_VER.linux-$ARCH.tar.gz" | tar xz
install -m755 alertmanager-$AM_VER.linux-$ARCH/{alertmanager,amtool} /usr/local/bin/
cd - >/dev/null

install -m644 prometheus/prometheus.yml prometheus/rules.yml /etc/prometheus/
install -m644 alertmanager/alertmanager.yml /etc/alertmanager/
chown -R prometheus: /var/lib/prometheus /var/lib/alertmanager
promtool check config /etc/prometheus/prometheus.yml

cat > /etc/systemd/system/prometheus.service <<UNIT
[Unit]
Description=Prometheus
After=network-online.target
[Service]
User=prometheus
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus --storage.tsdb.retention.time=30d --web.enable-lifecycle --web.listen-address=0.0.0.0:9090
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/alertmanager.service <<UNIT
[Unit]
Description=Alertmanager
After=network-online.target
[Service]
User=prometheus
ExecStart=/usr/local/bin/alertmanager --config.file=/etc/alertmanager/alertmanager.yml --storage.path=/var/lib/alertmanager --web.listen-address=0.0.0.0:9093 --cluster.listen-address=
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

# Grafana từ repo chính thức
if ! command -v grafana-server >/dev/null; then
  mkdir -p /etc/apt/keyrings
  curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
  apt-get update -q && apt-get install -y -q grafana
fi
install -m644 grafana/provisioning/datasources/prometheus.yml /etc/grafana/provisioning/datasources/
install -m644 grafana/provisioning/dashboards/it5404.yml /etc/grafana/provisioning/dashboards/
mkdir -p /var/lib/grafana/dashboards/it5404
install -m644 grafana/dashboards/*.json /var/lib/grafana/dashboards/it5404/
# Dashboard "Node Exporter Full" (grafana.com ID 1860) – phải khai báo trong báo cáo
curl -fsSL https://grafana.com/api/dashboards/1860/revisions/latest/download \
  | sed 's/${DS_PROMETHEUS}/prom/g' > /var/lib/grafana/dashboards/it5404/node-exporter-full.json
chown -R grafana: /var/lib/grafana/dashboards

systemctl daemon-reload
systemctl enable --now prometheus alertmanager grafana-server
systemctl restart prometheus alertmanager grafana-server
echo "Prometheus :9090  Alertmanager :9093  Grafana :3000 (admin/admin)"
