#!/usr/bin/env bash
# Tạo golden image (cấu hình C1): Ubuntu 24.04 arm64 + node_exporter + JRE 21 + app.jar, chạy bằng systemd.
# Chạy trong VM host (Lima) với quyền root:  sudo ./build-golden-image.sh
# Nguồn tham khảo: virt-customize(1) – libguestfs; node_exporter release từ github.com/prometheus/node_exporter
set -euo pipefail
cd "$(dirname "$0")"
REPO=$(cd ../.. && pwd)
NE_VER=${NE_VER:-1.10.2}
WORK=/var/lib/it5404-image; mkdir -p "$WORK"
BASE=$WORK/noble-arm64.img
OUT=$WORK/app-golden.qcow2

[ -f "$BASE" ] || curl -fL -o "$BASE" https://cloud-images.ubuntu.com/releases/noble/release/ubuntu-24.04-server-cloudimg-arm64.img
[ -f "$WORK/ne.tgz" ] || curl -fL -o "$WORK/ne.tgz" \
  "https://github.com/prometheus/node_exporter/releases/download/v${NE_VER}/node_exporter-${NE_VER}.linux-arm64.tar.gz"
# JRE Temurin (Eclipse Adoptium) – copy thẳng vào image, không cần mạng trong appliance libguestfs
JAVA_MAJOR=${JAVA_MAJOR:-21}
if [ ! -d "$WORK/jre-$JAVA_MAJOR" ]; then
  curl -fL -o "$WORK/jre.tgz" "https://api.adoptium.net/v3/binary/latest/$JAVA_MAJOR/ga/linux/aarch64/jre/hotspot/normal/eclipse"
  mkdir -p "$WORK/jre-$JAVA_MAJOR" && tar -xzf "$WORK/jre.tgz" -C "$WORK/jre-$JAVA_MAJOR" --strip-components=1
fi
rm -rf "$WORK/java" && cp -a "$WORK/jre-$JAVA_MAJOR" "$WORK/java"
tar -xzf "$WORK/ne.tgz" -C "$WORK" --strip-components=1 "node_exporter-${NE_VER}.linux-arm64/node_exporter"

cp "$BASE" "$OUT"
qemu-img resize "$OUT" 6G
export LIBGUESTFS_BACKEND=direct
virt-customize -a "$OUT" \
  --run-command 'useradd -r -s /usr/sbin/nologin node_exporter; useradd -r -s /usr/sbin/nologin app; mkdir -p /opt/app' \
  --copy-in "$WORK/node_exporter:/usr/local/bin" \
  --copy-in "$WORK/java:/opt" \
  --copy-in "$REPO/app/target/app.jar:/opt/app" \
  --copy-in "$PWD/node_exporter.service:/etc/systemd/system" \
  --copy-in "$PWD/cpu-app.service:/etc/systemd/system" \
  --run-command 'chmod 755 /usr/local/bin/node_exporter && chown -R app:app /opt/app' \
  --run-command 'systemctl enable node_exporter cpu-app' \
  --copy-in "$PWD/90-it5404.cfg:/etc/cloud/cloud.cfg.d" \
  --run-command 'DEBIAN_FRONTEND=noninteractive apt-get purge -y snapd unattended-upgrades ubuntu-release-upgrader-core update-notifier-common pollinate landscape-common motd-news-config || true' \
  --run-command 'systemctl disable apt-daily.timer apt-daily-upgrade.timer man-db.timer motd-news.timer fwupd-refresh.timer e2scrub_all.timer 2>/dev/null || true' \
  --run-command 'apt-get clean && cloud-init clean --logs || true' \
  --truncate /etc/machine-id
qemu-img convert -O qcow2 -c "$OUT" "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
echo "Golden image: $OUT"
