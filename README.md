# IT5404 – Hệ thống IaaS tự co giãn trên laptop

Đồ án môn Điện toán đám mây (IT5404, học kỳ 2026.1B).

**Đề tài:** IaaS mã nguồn mở (OpenStack) chạy trên một laptop, có giám sát không cần thao tác và tự động scale máy ảo
theo ngưỡng CPU qua OpenStack API. Phần mở rộng gồm phân rã/tối ưu thời gian scale-out và một trợ lý vận hành AI.

| Yêu cầu đề bài | Thành phần trong repo | Trạng thái |
|---|---|---|
| 1a. Cung cấp máy ảo | OpenStack Nova (DevStack all-in-one) | ✅ |
| 1b. Mạng ảo, VM ra Internet | Neutron/OVN, router SNAT, `app-net` | ✅ |
| 1c. GUI và API | Horizon; REST API (`docs/demo-1c-api.sh`), CLI, openstacksdk | ✅ |
| 2a. Agent giám sát tự cài | Golden image có sẵn `node_exporter` (systemd) | ✅ |
| 2b. Tự gửi dữ liệu về máy chủ giám sát | Prometheus `openstack_sd_configs` tự phát hiện VM qua Nova API | ✅ |
| 3a. Theo dõi tải tăng/giảm | JMeter + HAProxy + Grafana | ✅ |
| 3b. Scale out/in tự động qua API IaaS | `autoscaler/` (Python + openstacksdk) | ✅ |
| Mở rộng | `experiments/` (RQ1–RQ3), `assistant/` (trợ lý AI) | ✅ mã / ⏳ số liệu |

---

## 1. Kiến trúc

```
Mac (Apple Silicon)  ── JMeter, trình duyệt, Ollama + trợ lý AI
 └── Lima VM "openstack"   192.168.64.2   Ubuntu 24.04 arm64, 8 vCPU, 16 GB, nested KVM
      ├── DevStack 2026.2: Keystone, Nova, Neutron/OVN, Glance, Placement, Horizon (:80)
      ├── HAProxy  :8888 (ứng dụng)  :8405 (/metrics, /stats)
      ├── Prometheus :9090 · Alertmanager :9093 · Grafana :3000
      ├── Autoscaler :9180 (systemd; log /var/log/autoscaler/decisions.jsonl)
      └── VM app-* (flavor app.small 1 vCPU/1 GB, image app-golden) trên app-net 10.10.0.0/24
            ├── node_exporter :9100
            └── Spring Boot :8080  (/work tốn CPU, /actuator/prometheus, /actuator/health/readiness)
```

Luồng autoscaling: mỗi 15 giây, autoscaler đọc `app:cpu_util:avg1m` (CPU trung bình của nhóm VM) từ Prometheus.
- CPU ≥ 80% trong 2 chu kỳ liên tiếp: gọi Nova tạo 1 VM, chờ VM ACTIVE và app trả `/readiness` 200, rồi thêm VM vào HAProxy qua Runtime API.
- CPU ≤ 20% trong 8 chu kỳ: drain VM mới nhất khỏi HAProxy rồi xóa nó.
- Có cooldown và giới hạn min = 1, max = 4.
- Mỗi quyết định ghi một dòng JSON vào log, kèm thời gian của từng giai đoạn.

## 2. Yêu cầu

| | Tối thiểu | Đã kiểm chứng |
|---|---|---|
| Máy | Mac Apple Silicon **M3 trở lên**, macOS 15+ (nested virtualization) | MacBook M4, 24 GB, macOS 26 |
| RAM | 24 GB (VM host dùng 16 GB) | |
| Ổ đĩa trống | ~150 GB (đĩa ảo cấp phát động) | |
| Phần mềm | Homebrew, Docker Desktop (để build jar), Ollama (cho trợ lý AI) | |

> Trên máy M1/M2 không có nested virtualization. Nova khi đó phải chạy QEMU giả lập (`LIBVIRT_TYPE=qemu`), VM boot rất chậm.
> Trên laptop Linux x86 có VT-x, có thể bỏ Lima và chạy DevStack thẳng trên Ubuntu 24.04. Khi đó đổi image sang bản amd64 và bỏ các thiết lập riêng cho aarch64.

## 3. Cài đặt từng bước (~1,5 giờ, phần lớn là chờ DevStack)

```bash
git clone https://github.com/DoThanhHY/it5404.git ~/it5404 && cd ~/it5404
brew install lima jmeter

# 3.1 VM host (Lima + nested KVM). Script in ra IP của VM; nếu khác 192.168.64.2 thì chạy:
infra/lima/start.sh
# infra/set-host-ip.sh <IP>

# 3.2 Build ứng dụng Spring Boot (cần Docker; hoặc brew install maven openjdk@21 rồi mvn package)
docker run --rm -v "$PWD/app":/src -v "$HOME/.m2":/root/.m2 -w /src maven:3.9-eclipse-temurin-21 mvn -q -B package -DskipTests

# 3.3 Vào VM host. Repo được mount vào VM với cùng đường dẫn tuyệt đối.
limactl shell openstack
REPO=/Users/$USER/it5404   # đường dẫn tuyệt đối trên Mac (trong VM, ~ là thư mục home khác)

# 3.4 DevStack (~40 phút)
sudo -u stack -H bash -lc "cd /opt/stack && git clone -b stable/2026.2 https://opendev.org/openstack/devstack && \
  cp $REPO/infra/devstack/local.conf devstack/ && cd devstack && ./stack.sh"

# 3.5 Tài nguyên cho đồ án: flavor, app-net, security group, keypair, huge pages,
#     service khôi phục mạng sau reboot
sudo -u stack -H bash -lc "$REPO/infra/devstack/post-install.sh"

# 3.6 Golden image (C1) và upload lên Glance
sudo $REPO/infra/image/build-golden-image.sh
sudo -u stack -H bash -lc "$REPO/infra/image/upload-images.sh"

# 3.7 Giám sát: Prometheus, Alertmanager, Grafana
sudo $REPO/monitoring/install.sh

# 3.8 Load balancer và autoscaler
sudo cp $REPO/lb/haproxy.cfg /etc/haproxy/ && sudo systemctl restart haproxy
sudo -u stack -H bash -lc "$REPO/autoscaler/install.sh"
```

Khoảng 1 phút sau bước 3.8, autoscaler tự tạo VM app đầu tiên (`why: below_min`).

### Địa chỉ truy cập từ Mac

| Dịch vụ | URL | Tài khoản |
|---|---|---|
| Horizon | http://192.168.64.2/dashboard | `admin` / `demo`, mật khẩu `secret` |
| Grafana | http://192.168.64.2:3000 | `admin` / `admin`. Có dashboard *IT5404 – Autoscaling overview* và *Node Exporter Full* |
| Prometheus | http://192.168.64.2:9090/targets | |
| HAProxy stats | http://192.168.64.2:8405/stats | |
| Ứng dụng (qua LB) | http://192.168.64.2:8888/work | |
| Autoscaler | http://192.168.64.2:9180/status, `/decisions?limit=20` | |

## 4. Kiểm chứng từng yêu cầu (kịch bản demo ~15 phút)

| Bước | Lệnh / thao tác | Kết quả mong đợi |
|---|---|---|
| 1c | `docs/demo-1c-api.sh` (thêm tham số `create` để tạo và xóa VM qua `POST /servers`) | Lấy token Keystone; liệt kê flavor, image, network, server bằng `curl` |
| 1a, 1b | Horizon → *Launch Instance* (image `app-golden`, flavor `app.small`, mạng `app-net`, SG `app-sg`) → Console: `ping 8.8.8.8`, `curl -I https://google.com` | VM ra được Internet qua router SNAT |
| 2a, 2b | `openstack server create demo2 --image app-golden --flavor app.small --network app-net --security-group app-sg --property group=app --property monitoring=enabled` (**không SSH vào**) | ~1 phút sau VM xuất hiện ở Prometheus *Targets* và trên Grafana |
| 3a | `loadtest/run-load.sh` (profile 25 phút) | CPU và RPS tăng theo bậc trên Grafana |
| 3b scale-out | Theo dõi `tail -f /var/log/autoscaler/decisions.jsonl` trong VM | `scale_out_start` → `scale_out_done` (kèm `phases_s`); VM mới xuất hiện trên Horizon và HAProxy |
| 3b scale-in | Tải giảm ở phút 15 | `scale_in_start` → drain → `scale_in_done` |
| Mở rộng | `assistant/.venv/bin/python assistant/agent.py` → hỏi "Vì sao lần vừa rồi hệ thống thêm máy?" | Trợ lý trả lời kèm số liệu thật |

`loadtest/run-standard.sh 3` chạy profile chuẩn 3 lần và lưu số liệu vào `loadtest/results/standard-*`
(`timeseries.csv`, `decisions.json`, báo cáo HTML của JMeter).

## 5. Trợ lý vận hành AI (mở rộng)

```bash
ollama pull qwen3:8b
cd assistant && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python agent.py                 # hội thoại
.venv/bin/python agent.py -v -q "Hiện có bao nhiêu VM, CPU từng máy là bao nhiêu?"
.venv/bin/python eval.py                  # đánh giá 20 câu hỏi; đáp án tính tự động từ dữ liệu thật
```

- **Tool chỉ đọc:**
  - `query_prometheus`, `query_prometheus_range`
  - `list_servers`: Nova, đăng nhập bằng user Keystone `ops-reader` có role `reader`. Nova trả **403** nếu user này thử tạo VM.
  - `read_scaling_log`, `get_autoscaler_status`
  - `simulate_thresholds`: trả lời câu hỏi "nếu đổi ngưỡng thì sao".
- **`propose_scaling_action`:** chỉ tạo đề xuất. Người vận hành gõ `y` trong CLI thì đề xuất mới được gửi tới autoscaler, và vẫn bị chặn bởi giới hạn min/max. LLM không giữ credential có quyền ghi.

## 6. Thực nghiệm (mở rộng)

| Câu hỏi | Script | Kết quả |
|---|---|---|
| RQ1: phân rã thời gian scale-out | `experiments/rq_scaleout.py --config C0\|C1\|C2 --runs 10` (chạy trong VM) | `experiments/results/rq2/*.csv` |
| RQ2: C0 (cloud-init) / C1 (golden image) / C2 (JDK 25 + AOT cache) | như trên, cộng `experiments/rq2_slo.sh` (% request > 1 s khi tải tăng) | `experiments/results/rq2-slo/` |
| RQ3: ngưỡng, cửa sổ, cooldown → flapping, VM-giờ, p95 | `experiments/rq3_params.sh P1 P2 P3 P4` | `experiments/results/rq3/` |
| Tổng hợp và biểu đồ | `experiments/analyze.py` | `docs/figures/*.png`, `summary.json` |

`experiments/run-all.sh` chạy toàn bộ nối tiếp (~5 giờ). Image C2 build bằng lệnh
`sudo VARIANT=c2 infra/image/build-golden-image.sh`.

## 7. Xử lý sự cố (các lỗi đã gặp)

| Triệu chứng | Nguyên nhân | Cách xử lý (đã có trong repo) |
|---|---|---|
| VM ACTIVE nhưng không có mạng, cloud-init không chạy | Nova trên aarch64 không có DMI "OpenStack Nova", nên `ds-identify` tắt cloud-init | `force_config_drive = True` (`local.conf`); image chỉ dùng datasource ConfigDrive |
| VM tạo khi host đang tải chạy chậm ~30–40 lần, mất 4–5 phút mới sẵn sàng | Bộ nhớ VM không được cấp huge page; dịch địa chỉ 2 tầng trong nested virt rất tốn | 2560 huge page 2MB + flavor `hw:mem_page_size=large` (`post-install.sh`). Thời gian sẵn sàng giảm từ 254–312 s xuống 47 s |
| App khởi động mất vài phút ở lần boot đầu | snapd seeding, pollinate, check-new-release… của Ubuntu cloud image | Gỡ các gói đó khỏi golden image. Tổng scale-out giảm từ 194–304 s xuống ~52–60 s |
| `virt-customize --install` lỗi DNS | Appliance của libguestfs không phân giải được tên miền | Copy JRE Temurin dạng tarball vào image thay vì dùng apt |
| Mất route tới `app-net` hoặc VM mất Internet sau khi reboot VM host | IP br-ex, route và NAT do DevStack đặt không bền vững | Service `it5404-host-network` tự chạy `infra/devstack/host-network.sh` khi boot |
| Autoscaler tạo VM bị ERROR "No valid host" ngay sau reboot | nova-compute chưa báo tài nguyên lên Placement | Autoscaler tự xóa VM ERROR và thử lại sau cooldown |
| Nova trả 400 khi autoscaler tạo VM | Gửi `user_data=None` | Chỉ gửi `user_data` khi có giá trị |
| Không được cài Docker trong VM host | Docker đặt `iptables FORWARD DROP`, làm hỏng Neutron | Prometheus/Grafana cài bằng binary/apt |

Sao lưu VM host (rất nhanh nhờ APFS clone):
`limactl stop openstack && cp -c ~/.lima/openstack/disk ~/lima-backups/openstack-$(date +%F)`.

## 8. Cấu trúc repo

```
infra/lima/        VM host (Lima vz + nested virtualization), start.sh
infra/devstack/    local.conf, post-install.sh, host-network.sh (+ systemd unit)
infra/image/       build-golden-image.sh (C1, C2), cloud-init-c0.yaml (C0), unit systemd
infra/set-host-ip.sh
app/               Spring Boot: /work (SHA-256 lặp n lần), Actuator + Micrometer
monitoring/        prometheus.yml (openstack_sd), rules.yml, alertmanager.yml, Grafana provisioning + dashboard
lb/                haproxy.cfg (server-template + Runtime API)
autoscaler/        autoscaler.py, config.yaml, install.sh
loadtest/          step-load.jmx, run-load.sh, run-standard.sh, collect.py
assistant/         agent.py, tools.py, eval.py (Ollama qwen3:8b)
experiments/       rq_scaleout.py, rq2_slo.sh, rq3_params.sh, run-all.sh, analyze.py
docs/              demo-1c-api.sh, figures/
```

## 9. Mã nguồn và tài nguyên tham khảo (bắt buộc khai báo)

| Thành phần | Nguồn | Dùng để làm gì |
|---|---|---|
| DevStack, OpenStack 2026.2 | opendev.org/openstack | Cài IaaS all-in-one |
| Lima | github.com/lima-vm/lima | Chạy VM host trên macOS (vz, nested virt) |
| Ubuntu 24.04 cloud image (arm64) | cloud-images.ubuntu.com | Image gốc của VM |
| Eclipse Temurin JRE 21/25 | adoptium.net | Môi trường chạy Java trong VM |
| Prometheus, Alertmanager, node_exporter | prometheus.io | Giám sát, cảnh báo |
| Grafana; dashboard *Node Exporter Full* (ID 1860) | grafana.com | Hiển thị số liệu |
| HAProxy | haproxy.org | Load balancer, Runtime API |
| Spring Boot, Micrometer | spring.io | Ứng dụng mẫu, metric |
| openstacksdk, requests, PyYAML, prometheus-client | PyPI | Autoscaler, harness |
| Apache JMeter 5.6.3 | jmeter.apache.org | Sinh tải |
| libguestfs (virt-customize) | libguestfs.org | Build golden image |
| Ollama, Qwen3-8B | ollama.com | LLM cục bộ cho trợ lý |
| matplotlib, pandas | PyPI | Vẽ biểu đồ |
| **Mã nguồn trong repo** | Sinh với sự hỗ trợ của trợ lý AI Claude (Anthropic), nhóm kiểm tra và chạy thực nghiệm | Toàn bộ script, cấu hình, autoscaler, trợ lý, harness |

Mật khẩu trong repo (`secret`, `reader-secret`, Grafana `admin`) là mặc định của môi trường lab. Không dùng chúng cho môi trường thật.
