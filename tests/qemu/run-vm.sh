#!/bin/bash
# ==========================================================
# archsetup kurucu modu için QEMU test düzeneği
#
# Kullanım:
#   ./run-vm.sh            # UEFI + ISO'dan başlat (kurulum testi)
#   ./run-vm.sh bios       # BIOS + ISO'dan başlat (GRUB BIOS testi)
#   ./run-vm.sh boot       # ISO'suz, kurulu diskten başlat (doğrulama)
#   ./run-vm.sh bios-boot  # BIOS modunda diskten başlat
#   ./run-vm.sh sb         # Secure Boot destekli UEFI + ISO (sbctl'in gerçek kolu)
#   ./run-vm.sh sb-boot    # Secure Boot destekli UEFI, diskten
#   ./run-vm.sh reset      # disk ve UEFI değişkenlerini sıfırla
#
# SCRATCH=1 ile üç boş disk daha takılır (prepare / erase / nvme format
# testleri için). nvme olanı emüle NVMe denetleyicisidir: erase.py'nin
# firmware kolu yalnız orada koşar, ve bu makinedeki iki gerçek NVMe'de
# koşturulamaz -- ikisi de veri tutuyor.
#
# VM açıldıktan sonra canlı ortamda:
#   curl -L https://raw.githubusercontent.com/drpars/archsetup/main/iso.sh | bash
#
# Ekran arka ucu DISPLAY_MODE ile değiştirilir. Varsayılan gtk bir grafik
# oturumu gerektirir; makineye SSH ile bağlıysanız "gtk initialization failed"
# alırsınız. O durumda:
#   DISPLAY_MODE=curses ./run-vm.sh    # VGA konsolu doğrudan terminale çizer
#   DISPLAY_MODE=none   ./run-vm.sh    # ekran yok; yalnızca SSH ile yönetilir
# curses'ten çıkmak için: Esc+2 ile QEMU monitörüne geçip "quit"
# ==========================================================
set -euo pipefail

MODE="${1:-uefi}"
DIR="${XDG_CACHE_HOME:-$HOME/.cache}/archsetup-qemu"
ISO="$DIR/archlinux-x86_64.iso"
DISK="$DIR/disk.qcow2"
VARS="$DIR/OVMF_VARS.fd"
SBVARS="$DIR/OVMF_VARS.secboot.fd"   # SB turu kendi NVRAM'ini kullanır
DISK_SIZE="25G"
RAM="4096"
SSH_PORT="${SSH_PORT:-2222}"   # host portu -> guest 22 (SSH yönlendirmesi)
DISPLAY_MODE="${DISPLAY_MODE:-gtk}"

mkdir -p "$DIR"

die() { echo "HATA: $*" >&2; exit 1; }

command -v qemu-system-x86_64 >/dev/null ||
  die "qemu-system-x86_64 yok. Kurun: sudo pacman -S --needed qemu-desktop"

# OVMF (UEFI firmware) yollarını bul. Secure Boot ayrı bir görüntü ister:
# düz OVMF_CODE'da SetupMode değişkeni hiç yok, o yüzden `sbctl enroll-keys`
# "no such file" ile düşer ve kurucunun Secure Boot adımı imzalamayı atlar --
# yani kapı doğru davranır ama asıl kol hiç sınanmaz. smm=on da zorunlu:
# değişken deposunu SMM dışına yazılamaz kılan şey odur.
OVMF_CODE=""
for candidate in /usr/share/edk2/x64/OVMF_CODE.4m.fd \
                 /usr/share/edk2/x64/OVMF_CODE.fd \
                 /usr/share/edk2-ovmf/x64/OVMF_CODE.fd; do
  [[ -f "$candidate" ]] && OVMF_CODE="$candidate" && break
done

OVMF_SB=""
for candidate in /usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd \
                 /usr/share/edk2/x64/OVMF_CODE.secboot.fd \
                 /usr/share/edk2-ovmf/x64/OVMF_CODE.secboot.fd; do
  [[ -f "$candidate" ]] && OVMF_SB="$candidate" && break
done

if [[ "$MODE" == "reset" ]]; then
  rm -f "$DISK" "$VARS" "$SBVARS" "$DIR"/scratch-*.qcow2
  echo "Sıfırlandı: disk, UEFI değişkenleri ve varsa scratch diskler silindi. (ISO korundu)"
  exit 0
fi

# ISO gerekliyse indir
if [[ "$MODE" == "uefi" || "$MODE" == "bios" || "$MODE" == "sb" ]] && [[ ! -f "$ISO" ]]; then
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
)

case "$DISPLAY_MODE" in
  gtk)    ARGS+=(-device virtio-vga -display gtk,zoom-to-fit=on) ;;
  # curses draws the guest's text console straight into the terminal, which
  # is the only thing that works over SSH. virtio-vga has no text mode the
  # curses backend can read, so the emulated VGA card goes back to std.
  curses) ARGS+=(-device VGA -display curses) ;;
  none)   ARGS+=(-device virtio-vga -display none) ;;
  *)      die "Bilinmeyen DISPLAY_MODE: $DISPLAY_MODE (gtk|curses|none)" ;;
esac

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
  sb|sb-boot)
    [[ -n "$OVMF_SB" ]] ||
      die "Secure Boot destekli OVMF bulunamadı (OVMF_CODE.secboot.*). Kurun: sudo pacman -S --needed edk2-ovmf"
    # Anahtarsız VARS = Setup Mode: sbctl enroll-keys tam da orada çalışır.
    # Kendi kopyası, çünkü SB turu PK/KEK/db yazar ve düz turun NVRAM'ini
    # geri dönülmez biçimde değiştirir.
    if [[ ! -f "$SBVARS" ]]; then
      cp "${OVMF_SB/CODE.secboot/VARS}" "$SBVARS"
    fi
    ARGS+=(
      -machine q35,smm=on
      -global driver=cfi.pflash01,property=secure,value=on
      -drive "if=pflash,format=raw,readonly=on,file=$OVMF_SB"
      -drive "if=pflash,format=raw,file=$SBVARS"
    )
    ;;
  bios|bios-boot) ;;
  *) die "Bilinmeyen mod: $MODE (uefi|bios|boot|bios-boot|sb|sb-boot|reset)" ;;
esac

# İsteğe bağlı boş diskler: disk-prepare / disk-erase / nvme format için.
# Boyutlar kasten küçük -- 64 MiB'lık disk `dd` kolunu saniyeler içinde
# bitirir, ve üzerine yazmanın diskin son baytına kadar gittiği ancak tam
# boyut bilinerek doğrulanabilir.
if [[ "${SCRATCH:-0}" == "1" ]]; then
  for spec in "prep:512M" "erase:64M" "nvme:256M"; do
    name="${spec%%:*}"; size="${spec##*:}"
    img="$DIR/scratch-$name.qcow2"
    [[ -f "$img" ]] || qemu-img create -f qcow2 "$img" "$size" >/dev/null
  done
  ARGS+=(
    -drive "file=$DIR/scratch-prep.qcow2,if=virtio,format=qcow2"
    -drive "file=$DIR/scratch-erase.qcow2,if=virtio,format=qcow2"
    -drive "file=$DIR/scratch-nvme.qcow2,if=none,format=qcow2,id=scratchnvme"
    -device nvme,drive=scratchnvme,serial=ARCHSETUPSCRATCH
  )
fi

if [[ "$MODE" == "uefi" || "$MODE" == "bios" || "$MODE" == "sb" ]]; then
  ARGS+=(-cdrom "$ISO" -boot d)
fi

echo ">> QEMU başlatılıyor ($MODE)..."
echo ">> SSH: guest'te 'passwd' ile parola belirleyip host'tan:"
echo ">>      ssh -p $SSH_PORT root@localhost"
exec qemu-system-x86_64 "${ARGS[@]}"
