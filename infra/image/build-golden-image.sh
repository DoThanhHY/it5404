#!/usr/bin/env bash
# Tạo golden image cho VM ứng dụng. Chạy trong VM host (Lima) với quyền root.
#   sudo ./build-golden-image.sh            # C1: Ubuntu 24.04 arm64 + node_exporter + JRE 21 + app.jar
#   sudo VARIANT=c2 ./build-golden-image.sh # C2: như C1 nhưng JRE 25 + jar giải nén + AOT cache (JEP 483/514/515)
# Tham khảo: virt-customize(1) – libguestfs; node_exporter (github.com/prometheus/node_exporter);
#            Eclipse Temurin (adoptium.net); Spring Boot "Class Data Sharing / AOT cache" docs.
set -euo pipefail
cd "$(dirname "$0")"
REPO=$(cd ../.. && pwd)
VARIANT=${VARIANT:-c1}
NE_VER=${NE_VER:-1.10.2}
WORK=/var/lib/it5404-image; mkdir -p "$WORK"
BASE=$WORK/noble-arm64.img
if [ "$VARIANT" = c2 ]; then JAVA_MAJOR=${JAVA_MAJOR:-25}; OUT=$WORK/app-golden-c2.qcow2
else JAVA_MAJOR=${JAVA_MAJOR:-21}; OUT=$WORK/app-golden.qcow2; fi

[ -f "$BASE" ] || curl -fL -o "$BASE" https://cloud-images.ubuntu.com/releases/noble/release/ubuntu-24.04-server-cloudimg-arm64.img
[ -f "$WORK/ne.tgz" ] || curl -fL -o "$WORK/ne.tgz" \
  "https://github.com/prometheus/node_exporter/releases/download/v${NE_VER}/node_exporter-${NE_VER}.linux-arm64.tar.gz"
tar -xzf "$WORK/ne.tgz" -C "$WORK" --strip-components=1 "node_exporter-${NE_VER}.linux-arm64/node_exporter"
# JRE Temurin – copy thẳng vào image (appliance libguestfs không phân giải được DNS nên không apt install được)
if [ ! -d "$WORK/jre-$JAVA_MAJOR" ]; then
  curl -fL -o "$WORK/jre-$JAVA_MAJOR.tgz" "https://api.adoptium.net/v3/binary/latest/$JAVA_MAJOR/ga/linux/aarch64/jre/hotspot/normal/eclipse"
  mkdir -p "$WORK/jre-$JAVA_MAJOR" && tar -xzf "$WORK/jre-$JAVA_MAJOR.tgz" -C "$WORK/jre-$JAVA_MAJOR" --strip-components=1
fi
rm -rf "$WORK/java" && cp -a "$WORK/jre-$JAVA_MAJOR" "$WORK/java"

if [ "$VARIANT" = c2 ]; then
  APP_STEPS=(
    --copy-in "$PWD/cpu-app-c2.service:/etc/systemd/system"
    --run-command 'mv /etc/systemd/system/cpu-app-c2.service /etc/systemd/system/cpu-app.service'
    # giải nén fat jar (class trong nested jar không đưa được vào AOT cache)
    --run-command 'cd /opt/app && /opt/java/bin/java -Djarmode=tools -jar app.jar extract --destination /opt/app/ex'
    # training run: khởi động Spring đến khi context sẵn sàng rồi thoát, ghi AOT cache (JDK 25, JEP 514)
    --run-command 'cd /opt/app/ex && /opt/java/bin/java -XX:+UseSerialGC -Xmx512m -XX:AOTCacheOutput=/opt/app/app.aot -Dspring.context.exit=onRefresh -jar /opt/app/ex/app.jar'
    --run-command 'ls -l /opt/app/app.aot'
  )
else
  APP_STEPS=(--copy-in "$PWD/cpu-app.service:/etc/systemd/system")
fi

cp "$BASE" "$OUT"
qemu-img resize "$OUT" 6G
export LIBGUESTFS_BACKEND=direct LIBGUESTFS_MEMSIZE=1536
virt-customize -a "$OUT" \
  --run-command 'useradd -r -s /usr/sbin/nologin node_exporter; useradd -r -s /usr/sbin/nologin app; mkdir -p /opt/app' \
  --copy-in "$WORK/node_exporter:/usr/local/bin" \
  --copy-in "$WORK/java:/opt" \
  --copy-in "$REPO/app/target/app.jar:/opt/app" \
  --copy-in "$PWD/node_exporter.service:/etc/systemd/system" \
  "${APP_STEPS[@]}" \
  --run-command 'chmod 755 /usr/local/bin/node_exporter && chown -R app:app /opt/app' \
  --run-command 'systemctl enable node_exporter cpu-app' \
  --copy-in "$PWD/90-it5404.cfg:/etc/cloud/cloud.cfg.d" \
  --run-command 'DEBIAN_FRONTEND=noninteractive apt-get purge -y snapd unattended-upgrades ubuntu-release-upgrader-core update-notifier-common pollinate landscape-common motd-news-config || true' \
  --run-command 'systemctl disable apt-daily.timer apt-daily-upgrade.timer man-db.timer motd-news.timer fwupd-refresh.timer e2scrub_all.timer 2>/dev/null || true' \
  --run-command 'apt-get clean && cloud-init clean --logs || true' \
  --truncate /etc/machine-id
qemu-img convert -O qcow2 -c "$OUT" "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
echo "Golden image ($VARIANT): $OUT"
