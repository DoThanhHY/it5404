#!/usr/bin/env bash
# Upload image gốc (C0) và golden image (C1) lên Glance. Chạy bằng user 'stack'.
set -eo pipefail
source /opt/stack/devstack/openrc admin admin >/dev/null
W=/var/lib/it5404-image
PROPS="--property hw_firmware_type=uefi --property os_distro=ubuntu --property hw_qemu_guest_agent=no"
up() { [ "${ONLY:-$1}" = "$1" ] || return 0
       openstack image show "$1" >/dev/null 2>&1 && openstack image delete "$1"
       openstack image create "$1" --file "$2" --disk-format qcow2 --container-format bare --public $PROPS; }
[ -f $W/noble-arm64.img ] && up ubuntu-24.04-base $W/noble-arm64.img
up app-golden $W/app-golden.qcow2
[ -f $W/app-golden-c2.qcow2 ] && up app-golden-c2 $W/app-golden-c2.qcow2
