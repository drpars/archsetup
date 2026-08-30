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

## Kurulum bitti ≠ kurulum doğru

Yukarıdaki liste kurucunun **çalıştığını** doğruluyor, ürettiği sistemin
**doğru** olduğunu değil. Aradaki fark teorik değil: 2026-07-28'de bu betikle
yapılan gerçek kurulum hatasız tamamlandı ve her boot'ta 4 servis hatası veren,
2dk 20sn açılan, ESP'si herkese okunabilir bir sistem üretti. Hiçbiri ekranda
görünmedi.

O yüzden yeniden başlatmadan sonra bunlar da bakılmalı:

```bash
journalctl -p 3 -b            # BOŞ olmalı; tek satır bile bir yapılandırma artığıdır
systemd-analyze               # userspace süresi; onlarca saniye ise critical-chain'e bak
systemd-analyze critical-chain
```

- [ ] `journalctl -p 3 -b` çıktısı boş (modül yükleme, servis başlatma hatası yok)
- [ ] Boot süresi makul; `critical-chain`'de tek bir servis dakikalar tutmuyor

**ESP izinleri** (`disk.py` mount seçenekleri → `genfstab` → fstab zinciri):

```bash
findmnt -no OPTIONS /efi      # fmask=0077,dmask=0077 içermeli
grep efi /etc/fstab           # maske fstab'a da yazılmış olmalı
bootctl status | grep -i 'world accessible' || echo "uyari yok - dogru"
```

- [ ] `/efi` `fmask=0077,dmask=0077` ile bağlı
- [ ] Aynı maske **fstab'da da** var (yalnızca mount'ta olması yeterli değil —
      fstab yanlışsa sonraki boot'ta eski davranış döner)
- [ ] `bootctl` "random-seed world accessible" uyarısı vermiyor

**UKI fallback** (`chroot.py` preset düzenlemesi):

```bash
ls /efi/EFI/Linux/             # İKİ dosya olmalı: normal + fallback
bootctl list | grep -i fallback
df -h /efi                     # iki imaj sığdıktan sonra yer kaldı mı
```

- [ ] `/efi/EFI/Linux/` altında **iki** UKI var (biri `-fallback`)
- [ ] `bootctl list` fallback girdisini gösteriyor — yani menüden seçilebilir
- [ ] ESP dolmadı (iki UKI + firmware sığıyor)
- [ ] **Fallback gerçekten açılıyor:** yeniden başlat, önyükleme menüsünden
      fallback girdisini seç, sistem açılsın. Açılmayan bir kurtarma girdisi
      olmamasından beterdir — var sanırsın.

**Modül listesi** (kurucunun bıraktığı artıklar):

```bash
grep ^MODULES /etc/mkinitcpio.conf    # virtio_* ve radeon OLMAMALI
```

- [ ] `MODULES` içinde `virtio_blk`/`virtio_pci`/`virtio_net`/`radeon` yok

**Secure Boot** (`./run-vm.sh sb`, kendi NVRAM'iyle):

```bash
bootctl status | grep -i 'secure boot'   # "enabled (user)" olmalı
sbctl verify                             # HER UKI imzalı olmalı, fallback dahil
bootctl set-oneshot <fallback>.efi       # ve fallback gerçekten açılmalı
```

- [ ] Anahtarlar gerçekten kaydedildi (düz OVMF'de `SetupMode` değişkeni yok,
      `enroll-keys` düşer ve adım *"imzalama atlandı"* der — kapı doğru
      davranır, kol hiç koşmaz)
- [ ] `sbctl verify` çıktısında **tek bir `✗` yok**. Bir ✗ imzasız bir imaj
      demektir ve firmware onu `Access Denied` ile reddeder; 2026-08-30'da
      imzasız kalan şey tam olarak **fallback UKI** idi, yani kurtarma girdisi.

> Bu bölüm QEMU içinde çalıştırıldığında bir uyarı: VM'de kök disk gerçekten
> virtio üzerindedir, dolayısıyla `autodetect` `virtio_blk`'i **imaja** koyar.
> Bu doğrudur ve beklenendir — kontrol edilen şey `MODULES=()` satırına elle
> yazılmamış olması, imajda bulunmaması değil.

## BIOS turu: bölümleme listesi UEFI'ye özeldir

Yukarıdaki kontrol listesi bir ESP kurar; BIOS modunda ESP diye bir şey yok
ve **listeyi olduğu gibi izlemek GRUB'u kurdurtmaz.** Ölçüldü (2026-08-30):
GPT etiketli, BIOS boot bölümü olmayan bir diskte
`grub-install --target=i386-pc` gömecek yer bulamıyor, blocklist'e düşüyor ve
*"will not proceed with blocklists"* ile duruyor. archsetup bunu artık
kurulumdan **önce** söylüyor, ama düzeltmesi bölümlemede:

- [ ] **GPT kullanılacaksa** 1 MiB'lık bir bölüm açılıp tipi **ef02**
      (BIOS boot) yapılır — dosya sistemi yok, bağlanmaz, sadece GRUB'un
      `core.img`'i orada durur. cfdisk'te tip listesinde "BIOS boot" diye
      geçiyor.
- [ ] **MBR (dos) kullanılacaksa** gerekmiyor: önyükleme kaydının ardındaki
      boşluk zaten o iş için.
- [ ] **Bölüm Seçimi**'nde boot = `-` (ESP yok), swap ve root normal.
- [ ] **Önyükleyici → GRUB**; sorulan disk, bölüm değil **diskin kendisi**
      (`/dev/vda`), çünkü yazılan yer MBR.

Açılıştan sonra doğrulama listesi aynıdır, şu ikisi hariç: ESP izinleri ve
UKI bölümleri BIOS'ta karşılıksız. Yerlerine:

```bash
ls -la /boot/grub/grub.cfg          # üretilmiş olmalı
dd if=/dev/vda bs=440 count=1 | strings | head   # MBR'de GRUB kodu
```

## Diğer senaryolar

| Komut | Senaryo |
|---|---|
| `./run-vm.sh bios` | BIOS modunda GRUB kurulumu testi |
| `./run-vm.sh reset && ./run-vm.sh` | Temiz diskle yeniden başla (rEFInd turu) |
| `./run-vm.sh boot` / `bios-boot` | Kurulu sistemi diskten başlat |
| `./run-vm.sh sb` / `sb-boot` | Secure Boot destekli firmware (sbctl'in gerçek kolu) |
| `SCRATCH=1 ./run-vm.sh` | Üç boş disk daha: prepare / erase / nvme format |

Disk ve ISO `~/.cache/archsetup-qemu/` altında tutulur.

## Başka bir düzenek: swap bölümünden hazırda bekletme

`run-vm.sh` kurucu modunu sınar ve elle sürülür. Yanında, tamamen betikle
sürülen ikinci bir düzenek var: `hibernate-swap-partition/`. Swap **bölümü**
olan bir misafir kurar, `swap-hibernate` görevini orada koşturur ve gerçek
bir S4 gidiş-dönüşü ölçer — bu makinede swap bölümü olmadığı için o dal
başka türlü sınanamıyor. Kendi README'si ayrıntıyı taşıyor.
