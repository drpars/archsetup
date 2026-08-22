set -euo pipefail
DISK=/dev/vda
echo "### partitioning"
sgdisk --zap-all "$DISK" >/dev/null
sgdisk -n1:0:+512M -t1:ef00 -c1:ESP \
       -n2:0:+2G   -t2:8200 -c2:swappart \
       -n3:0:0     -t3:8300 -c3:root "$DISK" >/dev/null
partprobe "$DISK"; udevadm settle; sleep 1

echo "### filesystems"
mkfs.fat -F32 "${DISK}1" >/dev/null
mkswap "${DISK}2" >/dev/null
mkfs.ext4 -qF "${DISK}3"

mount "${DISK}3" /mnt
mount --mkdir "${DISK}1" /mnt/boot
swapon "${DISK}2"

ROOT_UUID=$(blkid -s UUID -o value "${DISK}3")
SWAP_UUID=$(blkid -s UUID -o value "${DISK}2")
echo "RIG_SWAP_UUID_BLKID=$SWAP_UUID"
echo "RIG_SWAP_UUID_LSBLK=$(lsblk -no UUID ${DISK}2)"
echo "RIG_ROOT_UUID=$ROOT_UUID"
echo "### /proc/swaps in live env"
cat /proc/swaps

timedatectl set-ntp true || true
echo "### pacstrap"
pacstrap -K /mnt base linux mkinitcpio python python-textual git sudo

genfstab -U /mnt >> /mnt/etc/fstab
cat > /mnt/root/setup.sh <<'CHROOT_EOF'
set -euo pipefail
git clone --depth 1 https://github.com/drpars/archsetup /opt/archsetup
git -C /opt/archsetup rev-parse HEAD
ln -sf /usr/share/zoneinfo/UTC /etc/localtime
echo "en_US.UTF-8 UTF-8" > /etc/locale.gen
locale-gen
echo "LANG=en_US.UTF-8" > /etc/locale.conf
echo swaptest > /etc/hostname
printf '127.0.0.1 localhost\n::1 localhost\n127.0.1.1 swaptest\n' > /etc/hosts
passwd -d root
grep -E '^HOOKS=' /etc/mkinitcpio.conf
mkinitcpio -P
bootctl install
printf 'default arch.conf\ntimeout 0\n' > /boot/loader/loader.conf
mkdir -p /etc/systemd/system/serial-getty@ttyS0.service.d
printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin root --keep-baud 115200,57600,38400,9600 - $TERM\n' \
  > /etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf
systemctl enable serial-getty@ttyS0.service
CHROOT_EOF

echo "### chroot setup"
arch-chroot /mnt bash /root/setup.sh

cat > /mnt/boot/loader/entries/arch.conf <<EOF
title   Arch swaptest
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options root=UUID=$ROOT_UUID rw console=ttyS0,115200
EOF
echo "### loader entry"
cat /mnt/boot/loader/entries/arch.conf

umount -R /mnt
swapoff "${DISK}2"
echo "RIG_INSTALL_OK"
