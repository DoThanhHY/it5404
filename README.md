# IT5404 – Hệ thống IaaS tự co giãn trên laptop

OpenStack (DevStack all-in-one) chạy trong VM Ubuntu 24.04 arm64 (Lima + nested KVM) trên MacBook Apple Silicon (M3 trở lên),
giám sát tự động bằng Prometheus (`openstack_sd_configs`) và autoscaling theo ngưỡng qua Nova API.

```
Mac (JMeter, trình duyệt)
 └── Lima VM "openstack"  192.168.64.2   (8 vCPU, 16GB, /dev/kvm)
      ├── DevStack: Keystone, Nova, Neutron/OVN, Glance, Placement, Horizon (:80)
      ├── HAProxy :8888 (app) / :8405 (metrics, /stats)
      ├── Prometheus :9090, Alertmanager :9093, Grafana :3000
      ├── Autoscaler :9180 (systemd, log /var/log/autoscaler/decisions.jsonl)
      └── VM app-* (app.small, golden image) trên app-net 10.10.0.0/24
            ├── node_exporter :9100
            └── Spring Boot :8080 (/work, /actuator/prometheus)
```

## Cấu trúc repo

| Thư mục | Nội dung |
|---|---|
| `infra/lima/` | Cấu hình VM host (Lima, vz, nested virtualization) |
| `infra/devstack/` | `local.conf`, `post-install.sh` (flavor, mạng, security group, keypair, route) |
| `infra/image/` | Tạo golden image (C1) bằng virt-customize; `cloud-init-c0.yaml` (C0) |
| `app/` | Ứng dụng Spring Boot có endpoint tốn CPU `/work` |
| `monitoring/` | Prometheus, rules, Alertmanager, Grafana provisioning + dashboard |
| `lb/` | HAProxy (server-template + Runtime API) |
| `autoscaler/` | Autoscaler Python + openstacksdk |
| `loadtest/` | JMeter step-load |

## Triển khai từng bước

```bash
# 0. Trên Mac
brew install lima jmeter
limactl start --name=openstack infra/lima/openstack-host.yaml
limactl shell openstack                     # vào VM; repo được mount tại ~/dientoandammay (cùng đường dẫn)

# 1. DevStack (~40 phút) – user stack
sudo -u stack -i
git clone -b stable/2026.2 https://opendev.org/openstack/devstack && cd devstack
cp /Users/thanhdo/dientoandammay/infra/devstack/local.conf . && ./stack.sh
/Users/thanhdo/dientoandammay/infra/devstack/post-install.sh

# 2. Ứng dụng + golden image (build jar trên Mac: cd app && docker run ... mvn package)
sudo /Users/thanhdo/dientoandammay/infra/image/build-golden-image.sh
/Users/thanhdo/dientoandammay/infra/image/upload-images.sh          # user stack

# 3. Giám sát
sudo /Users/thanhdo/dientoandammay/monitoring/install.sh

# 4. HAProxy + autoscaler
sudo cp /Users/thanhdo/dientoandammay/lb/haproxy.cfg /etc/haproxy/ && sudo systemctl restart haproxy
/Users/thanhdo/dientoandammay/autoscaler/install.sh                  # user stack

# 5. Sinh tải (trên Mac)
loadtest/run-load.sh
```

Truy cập từ Mac: Horizon http://192.168.64.2/dashboard (admin|demo / secret), Grafana http://192.168.64.2:3000,
Prometheus http://192.168.64.2:9090/targets, HAProxy stats http://192.168.64.2:8405/stats.

## Lưu ý

- IP `192.168.64.2` do macOS vmnet cấp qua DHCP; nếu đổi sau khi khởi động lại, sửa `HOST_IP` trong `local.conf`,
  `prometheus.yml`, `autoscaler/install.sh`.
- Không cài Docker trong VM host: Docker đặt `iptables FORWARD DROP`, làm hỏng mạng Neutron.
- Sau khi khởi động lại VM host cần chạy lại `post-install.sh` (route tới app-net và NAT không bền vững).
- Khai báo mã nguồn tham khảo trong báo cáo: DevStack, Grafana dashboard 1860, node_exporter, Prometheus,
  HAProxy, openstacksdk, Spring Boot, JMeter, Lima, và mọi đoạn mã sinh bởi AI.
