# QEMU ile Kurucu Modu Testi

## Hazırlık

```bash
sudo pacman -S --needed qemu-desktop edk2-ovmf
cd tests/qemu
./run-vm.sh          # ISO'yu indirir (~1.2 GB), 25G sanal disk oluşturur, UEFI VM açar
```

## SSH ile bağlanma (isteğe bağlı ama önerilir)

QEMU **kullanıcı modu ağ** kullanır: guest NAT arkasında izole bir ağdadır
(10.0.2.15), host'unuzdan doğrudan erişilemez — bu yüzden 192.168.x.x'ten
SSH tutmaz. `run-vm.sh` bunun için host'un **2222** portunu guest'in 22'sine
yönlendirir. QEMU penceresinde (canlı ortam) sadece root parolası belirleyin:

```bash
passwd            # canlı ISO'da sshd zaten açık; sadece parola gerekir
```

Sonra kendi terminalinizden bağlanın (kopyala-yapıştır ve rahat çalışma için):

```bash
ssh -p 2222 root@localhost
curl -L https://raw.githubusercontent.com/drpars/archsetup/main/iso.sh | bash
```

Farklı port için: `SSH_PORT=2200 ./run-vm.sh`. Guest'i her sıfırladığınızda
host anahtarı değişir; gerekirse `ssh-keygen -R "[localhost]:2222"` ile
eski anahtarı silin.

> Not: SSH şart değil — QEMU'nun GTK penceresinde de her şey çalışır.
> SSH yalnızca kopyala-yapıştır ve daha konforlu bir terminal için.

## Test akışı (kontrol listesi)

VM'de canlı ortam açıldıktan sonra (QEMU penceresinde veya SSH ile):

```bash
curl -L https://raw.githubusercontent.com/drpars/archsetup/main/iso.sh | bash
```

- [ ] archsetup kurucu menüsüyle açıldı (dil seçimi + Tokyo Night tema)
- [ ] **Bölümleri Düzenle (cfdisk):** GPT etiketi; 1G EFI (tip: EFI System),
      4G swap, kalan root
- [ ] **Bölüm Seçimi:** boot=vda1, swap=vda2, root=vda3, home=yok
- [ ] **Biçimlendir:** boot=fat32, root=ext4 (ikinci turda btrfs deneyin —
      subvolume oluşturmalı)
- [ ] **Bağla** → **pacstrap** (linux-zen + headers + firmware)
- [ ] **Sistem Yapılandırması:** hostname, klavye (trq), locale (tr_TR),
      saat dilimi, root parolası, kullanıcı (sudo'lu), fstab (UUID)
- [ ] **Önyükleyici → systemd-boot (UKI)** → ardından **UKI Üret**
      (`/mnt/efi/EFI/Linux/arch-linux-zen.efi` oluşmalı)
- [ ] **Watchdog Kapat**, **Ek Paketler** (iwd, openssh, neovim...)
- [ ] **Ayır** → **Yeniden Başlat**

Yeniden başlatma sonrası (`./run-vm.sh boot` ile ISO'suz):

- [ ] systemd-boot doğrudan UKI'yi açtı, sistem giriş ekranına geldi
- [ ] Ağ çalışıyor (`iwctl`/dhcp), kullanıcı + sudo çalışıyor
- [ ] `git clone https://github.com/drpars/archsetup && cd archsetup && ./archsetup`
      → kurulum sonrası modu açılıyor, `bootloader-info` "systemd-boot (UKI)"
      gösteriyor

## Diğer senaryolar

| Komut | Senaryo |
|---|---|
| `./run-vm.sh bios` | BIOS modunda GRUB kurulumu testi |
| `./run-vm.sh reset && ./run-vm.sh` | Temiz diskle yeniden başla (rEFInd turu) |
| `./run-vm.sh boot` / `bios-boot` | Kurulu sistemi diskten başlat |

Disk ve ISO `~/.cache/archsetup-qemu/` altında tutulur.
