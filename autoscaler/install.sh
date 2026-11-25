#!/usr/bin/env bash
# Cài autoscaler thành systemd service trên VM host. Chạy bằng user 'stack': ./install.sh
set -euo pipefail
cd "$(dirname "$0")"
DIR=/opt/stack/autoscaler
mkdir -p $DIR ~/.config/openstack
cp autoscaler.py config.yaml requirements.txt $DIR/
python3 -m venv $DIR/venv && $DIR/venv/bin/pip install -q -r $DIR/requirements.txt
cat > ~/.config/openstack/clouds.yaml <<YAML
clouds:
  devstack:
    auth:
      auth_url: http://192.168.64.2/identity/v3
      username: demo
      password: secret
      project_name: demo
      user_domain_name: Default
      project_domain_name: Default
    region_name: RegionOne
    interface: public
    identity_api_version: 3
YAML
sudo mkdir -p /var/log/autoscaler && sudo chown stack: /var/log/autoscaler
sudo tee /etc/systemd/system/autoscaler.service >/dev/null <<UNIT
[Unit]
Description=IT5404 autoscaler
After=network-online.target prometheus.service haproxy.service devstack@n-cpu.service it5404-host-network.service
[Service]
User=stack
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python $DIR/autoscaler.py -c $DIR/config.yaml
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now autoscaler && sudo systemctl restart autoscaler
