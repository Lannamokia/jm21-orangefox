#!/bin/bash
# Run as root on a Linux host with debootstrap installed. The source directory
# must be a clean checkout prepared from manifest.xml, without an out directory.
set -euo pipefail
root=$(realpath -m "${1:?usage: new-chroot.sh NEW_ROOT CLEAN_SOURCE BUNDLE}")
source_dir=$(realpath "${2:?missing clean source}")
bundle=$(realpath "${3:?missing extracted JM21 bundle}")
test ! -e "$root"
test ! -e "$source_dir/out"
debootstrap --variant=minbase --include=ca-certificates,gnupg jammy "$root" \
  "${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu/}"
cp /etc/resolv.conf "$root/etc/resolv.conf"
cat > "$root/etc/apt/sources.list" <<EOF
deb ${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu/} jammy main universe multiverse restricted
deb ${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu/} jammy-updates main universe multiverse restricted
deb ${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu/} jammy-security main universe multiverse restricted
EOF
mkdir -p "$root/root/fox_12.1" "$root/root/jm21"
rsync -a --exclude=out/ "$source_dir/" "$root/root/fox_12.1/"
rsync -a --exclude=.git/ --exclude=__pycache__/ "$bundle/" "$root/root/jm21/"
export JM21_CHROOT="$root"
# Mount changes and build processes live in private mount / PID namespaces.
unshare --mount --pid --fork bash -s <<'EOF'
set -e
mount --make-rprivate /
mount --bind /dev "$JM21_CHROOT/dev"
mount -t proc proc "$JM21_CHROOT/proc"
mount -t sysfs sysfs "$JM21_CHROOT/sys"
chroot "$JM21_CHROOT" bash /root/jm21/scripts/install-deps.sh
chroot "$JM21_CHROOT" python3 -m pip install -r /root/jm21/requirements.txt
chroot "$JM21_CHROOT" python3 /root/jm21/scripts/apply.py /root/fox_12.1
mkdir -p "$JM21_CHROOT/root/fox_12.1/out"
chroot "$JM21_CHROOT" bash /root/jm21/scripts/build.sh /root/fox_12.1
chroot "$JM21_CHROOT" python3 /root/jm21/scripts/assemble.py \
  /root/fox_12.1/out/target/product/jm21/recovery/root /root/OrangeFox-JM21.img
chroot "$JM21_CHROOT" python3 /root/jm21/scripts/compare.py /root/OrangeFox-JM21.img
EOF
