#!/bin/bash
# ==========================================================
# archsetup kurucu modu için QEMU test düzeneği
#
# Kullanım:
#   ./run-vm.sh            # UEFI + ISO'dan başlat (kurulum testi)
#   ./run-vm.sh bios       # BIOS + ISO'dan başlat (GRUB BIOS testi)
#   ./run-vm.sh boot       # ISO'suz, kurulu diskten başlat (doğrulama)
#   ./run-vm.sh bios-boot  # BIOS modunda diskten başlat
#   ./run-vm.sh reset      # disk ve UEFI değişkenlerini sıfırla
#
# VM açıldıktan sonra canlı ortamda:
#   curl -L https://raw.githubusercontent.com/drpars/archsetup/main/iso.sh | bash
# ==========================================================
set -euo pipefail

MODE="${1:-uefi}"
DIR="${XDG_CACHE_HOME:-$HOME/.cache}/archsetup-qemu"
ISO="$DIR/archlinux-x86_64.iso"
DISK="$DIR/disk.qcow2"
VARS="$DIR/OVMF_VARS.fd"
DISK_SIZE="25G"
RAM="4096"
SSH_PORT="${SSH_PORT:-2222}"   # host portu -> guest 22 (SSH yönlendirmesi)

mkdir -p "$DIR"

die() { echo "HATA: $*" >&2; exit 1; }

command -v qemu-system-x86_64 >/dev/null ||
  die "qemu-system-x86_64 yok. Kurun: sudo pacman -S --needed qemu-desktop"

# OVMF (UEFI firmware) yollarını bul
OVMF_CODE=""
for candidate in /usr/share/edk2/x64/OVMF_CODE.4m.fd \
                 /usr/share/edk2/x64/OVMF_CODE.fd \
                 /usr/share/edk2-ovmf/x64/OVMF_CODE.fd; do
  [[ -f "$candidate" ]] && OVMF_CODE="$candidate" && break
done

if [[ "$MODE" == "reset" ]]; then
  rm -f "$DISK" "$VARS"
  echo "Sıfırlandı: $DISK ve $VARS silindi. (ISO korundu)"
  exit 0
fi

# ISO gerekliyse indir
if [[ "$MODE" == "uefi" || "$MODE" == "bios" ]] && [[ ! -f "$ISO" ]]; then
  echo ">> Arch ISO indiriliyor: $ISO"
  curl -L --fail -o "$ISO.part" \
    "https://geo.mirror.pkgbuild.com/iso/latest/archlinux-x86_64.iso"
  mv "$ISO.part" "$ISO"
fi

# Sanal disk
if [[ ! -f "$DISK" ]]; then
  qemu-img create -f qcow2 "$DISK" "$DISK_SIZE"
  echo ">> $DISK_SIZE sanal disk oluşturuldu: $DISK"
fi

ARGS=(
  -enable-kvm -cpu host -smp 4 -m "$RAM"
  -drive "file=$DISK,if=virtio,format=qcow2"
  -nic "user,model=virtio-net-pci,hostfwd=tcp::$SSH_PORT-:22"
  -device virtio-vga
  -display gtk,zoom-to-fit=on
)

case "$MODE" in
  uefi|boot)
    [[ -n "$OVMF_CODE" ]] ||
      die "OVMF bulunamadı. Kurun: sudo pacman -S --needed edk2-ovmf"
    if [[ ! -f "$VARS" ]]; then
      cp "${OVMF_CODE/CODE/VARS}" "$VARS"
    fi
    ARGS+=(
      -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
      -drive "if=pflash,format=raw,file=$VARS"
    )
    ;;
  bios|bios-boot) ;;
  *) die "Bilinmeyen mod: $MODE (uefi|bios|boot|bios-boot|reset)" ;;
esac

if [[ "$MODE" == "uefi" || "$MODE" == "bios" ]]; then
  ARGS+=(-cdrom "$ISO" -boot d)
fi

echo ">> QEMU başlatılıyor ($MODE)..."
echo ">> SSH: guest'te 'passwd' ile parola belirleyip host'tan:"
echo ">>      ssh -p $SSH_PORT root@localhost"
exec qemu-system-x86_64 "${ARGS[@]}"
