# QEMU ile Kurucu Modu Testi

## Hazırlık

```bash
sudo pacman -S --needed qemu-desktop edk2-ovmf
cd tests/qemu
./run-vm.sh          # ISO'yu indirir (~1,5 GB), 25G sanal disk oluşturur, UEFI VM açar
```

**ISO bir kez inip orada duruyor, ve eskiyor.** `run-vm.sh` dosya varsa yeniden
indirmez; depolar ise ilerler. Bir aylık bir ISO'da `pacman -Sy python` yeni
glibc'ye karşı derlenmiş bir yorumlayıcı kurar ve o yorumlayıcı **hiç
çalışmaz** — mesaj ISO'yu değil kütüphaneyi işaret ettiği için turu yanlış yere
yorar. `iso.sh` bunu 2026-08-30'dan beri kendisi karşılıyor (glibc'yi aynı
işlemde yükseltiyor, sonra yorumlayıcıya çalışıp çalışmadığını soruyor) ve o
yol taze bir 2026-07-29 imajında uçtan uca ölçüldü. Yine de ISO'yu tazelemek
isterseniz: `rm ~/.cache/archsetup-qemu/archlinux-x86_64.iso`.

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

**Grafik penceresi olmadan sürmek** (`DISPLAY_MODE=none` + `-monitor unix:…`)
mümkün ve turun tamamı öyle koşturuldu. İki şey bilinmeden çalışmıyor: canlı
ISO'da sshd açıktır ama root parolası boştur ve `PermitEmptyPasswords no`, yani
**ilk anahtar konsoldan** konmak zorunda (`sendkey`); ve TUI'yi okumanın yolu
misafirde `tmux` — `tmux capture-pane -p -e` ekranı metin olarak verir, `-e`
olmadan **seçili satır görünmez** (Textual onu yalnız arka plan rengiyle
gösteriyor). Ayrıntı: `archsetup` notlarının 2026-08-30 (7. iş) kaydı.

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
- [ ] Aynı adımın sonunda **firmware sırası** basılıyor ve Arch girdisi öne
      alınıyor (`efibootmgr -o …`). Bu bir kolaylık değil, düzeltmedir:
      `bootctl install` **yeni** bir girdiyi daima sona ekler ve bu rig'de
      makine ağ girdilerinin arkasında kalıp **PXE'ye düştü** — ekranda hiçbir
      hata olmadan
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

**Firmware önyükleme sırası** (`bootloaders.py`, `install_systemd_boot` sonu):

```bash
efibootmgr | grep ^BootOrder     # Arch girdisi BAŞTA olmalı
efibootmgr -v | grep -i arch     # hangi slot, hangi yükleyici
```

- [ ] Arch girdisi sıranın **başında**. Sonda kalmışsa kurulum "bitti" görünür
      ve makine başka bir şey açar — bu rig'de PXE'ye düştü, ekranda hiçbir
      hata yoktu. Sebep kurucunun kusuru değil: `bootctl install` **yeni** bir
      girdiyi daima sona ekler, ve kurucu bunu adımın sonunda düzeltir
- [ ] Öne alınan şey **birincil** girdi, `Fallback …` olan değil. İkisi aynı
      PARTUUID'yi taşıyor, ayıran şey yükleyici yolu

**Modül listesi** (kurucunun bıraktığı artıklar):

```bash
grep ^MODULES /etc/mkinitcpio.conf    # virtio_* ve radeon OLMAMALI
```

- [ ] `MODULES` içinde `virtio_blk`/`virtio_pci`/`virtio_net`/`radeon` yok

> Bu bölüm QEMU içinde çalıştırıldığında bir uyarı: VM'de kök disk gerçekten
> virtio üzerindedir, dolayısıyla `autodetect` `virtio_blk`'i **imaja** koyar.
> Bu doğrudur ve beklenendir — kontrol edilen şey `MODULES=()` satırına elle
> yazılmamış olması, imajda bulunmaması değil.

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

**Adımı ikinci kez koşturmak iki şeyin sıfırlanmasını istiyor.** Anahtarlar
kaydedildikten sonra firmware setup modundan çıkıyor (`SetupMode` 1 → 0) ve adım
— doğru olarak — *"firmware kurulum modunda değil"* deyip duruyor. Kurulu
sistemi **koruyarak** tekrar denemenin yolu:

```bash
rm ~/.cache/archsetup-qemu/OVMF_VARS.secboot.fd   # sonraki sb koşusu anahtarsız açar
rm -rf /mnt/var/lib/sbctl                          # yoksa create-keys eskisini bulur
```

> **Kurulu bir diskte `-boot d` ISO'yu açmaz.** NVRAM'de kalıcı bir
> `BootOrder` varsa OVMF onu komut satırındaki boot sırasına tercih ediyor —
> ölçüldü (2026-08-30, `grub.qcow2`): `-boot d` dururken makine kurulu sisteme
> girdi. Canlı ortama geçmek için kurulu sistemden tek seferlik
> `efibootmgr -n <DVD slotu>` + `reboot`; kalıcı sırayı bozmaz.

`./run-vm.sh reset` ikisini de halleder ama **kurulumu da siler** (disk, iki
NVRAM ve scratch diskler gider; ISO kalır) — yani sıfırdan bir tur demektir.

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

## Disk yüzeyleri: `SCRATCH=1` ile üç boş disk

`disk-prepare`, `disk-erase` ve `nvme format` **geri dönüşü olmayan** işler
yapıyor, yani gerçek makinede denemek için harcanabilir bir aygıt gerekiyor.
`SCRATCH=1 ./run-vm.sh` bunu veriyor: 512M ve 64M iki virtio disk, artı emüle
bir **NVMe denetleyicisi** — `erase.py`'nin firmware kolu yalnız orada koşuyor,
çünkü bu makinedeki iki gerçek NVMe de veri tutuyor.

```bash
SCRATCH=1 ./run-vm.sh
```

> **`vda` boş disk DEĞİLDİR.** Kurulum diski her zaman ilk sırada takılıyor,
> yani `SCRATCH=1` altında dizilim `vda` = kurulum, **`vdb` = 512M boş**,
> **`vdc` = 64M boş**, `nvme0n1` = emüle NVMe. Aşağıdaki komutlar bu adlara
> göre yazılı; `lsblk -d -o NAME,SIZE,MODEL` ile bir kez doğrulayın —
> yanlış adla koşan bir `mkfs` kurulumu götürür. (Kurucunun kendi kapıları
> bağlı diski reddeder, ama kabuktan atılan komut o kapıdan geçmez.)

- [ ] **`disk-prepare`:** önce boş diske bir tablo + dosya sistemi yaz
      (`sgdisk -n 1:0:0 /dev/vdb && partprobe /dev/vdb && mkfs.ext4 /dev/vdb1`),
      `blkid` ile UUID'sini not et, sonra adımı koştur ve `/dev/vdb`'yi seç.
      Ardından **aynı hizada** yeni bir bölüm açıp `blkid` sor: **eski UUID
      dönmemeli.** 2026-08-30'da dönüyordu — bu yüzeyin var olma sebebi tam
      olarak buydu
- [ ] **`disk-erase` üzerine yazma kolu** (NVMe olmayan diskte, `/dev/vdc`):
      tanınır bir desen yaz, sil, `cmp /dev/vdc /dev/zero` ile **disk boyunun
      son baytına kadar** sıfır olduğunu doğrula (`cmp` tam boyda EOF
      demeli). Küçük disk kasten: 64 MiB `dd` saniyeler sürer ve tam boy
      bilinmeden "sonuna kadar gitti" doğrulanamaz
- [ ] **`nvme format`** emüle denetleyicide. `--ses 0` kipinde veri
      **okunamaz hâle gelmek zorunda değil**: bu cihazın `dlfeat`'i öyle
      diyorsa sıfır döner, başka denetleyicide dönmeyebilir (`nvme id-ns`).
      Kripto silme menüde çıkmamalı — QEMU `fna=0x0` bildiriyor
- [ ] **Ortak kapılar:** bağlı bir bölümü olan disk reddedilmeli ve mesaj
      bağlama noktasını adıyla vermeli (`mount /dev/vdb1 /mnt2` sonra
      `/dev/vdb`'yi seçmeyi deneyin); `/dev/fd0` (QEMU'nun 4 KB disketi)
      listede **görünmemeli** — misafirde vardır, listede olmamalıdır

## Henüz hiç koşmamış kollar

Bunlar kasten açık: her biri bir menü satırı, ama her biri kendi kurulumunu
istiyor. Bir sonraki tur için liste burada dursun.

- [x] **GRUB'un UEFI kolu** (`grub-install --target=x86_64-efi`) — 2026-08-30'da
      uçtan uca koştu. Yan soru da kapandı: `efibootmgr -c` girdiyi sıranın
      **başına** koyuyor (taze NVRAM'de dokuz firmware girdisi varken GRUB
      `BootOrder`'ın başına geçti; ikinci koşu kopya üretmedi; ISO takılıyken
      `-boot d` olmadan açılan makine `BootCurrent: 0009` dedi). Yani
      systemd-boot'a yazılan `efibootmgr -o` kolu **buraya gerekmiyor** —
      sorun `bootctl install`'a özel
- [x] **rEFInd** (`refind-install`) — 2026-08-30'da koştu. Girdi
      `BootOrder`'ın başına, üstelik zaten başta duran **GRUB girdisinin**
      önüne geçti; sistem hem varsayılan kolundan (GRUB'a zincirleme) hem
      `refind_linux.conf` kolundan (`Boot boot\vmlinuz-linux-zen from root`)
      ayrı ayrı açıldı. Mekanizma GRUB'ınkiyle aynı değil: `refind-install`
      `efibootmgr -c` kullanıyor **ama** girdi başta değilse onu silip yeniden
      yaratıyor
- [x] **Sabit IP (`net-static` / `net-dhcp`)** — 2026-08-31'de gerçek bir
      networkd'ye verildi (bu makine, `wlan0`, systemd 261.2). Dosya joker
      `20-wireless.network`'ü gerçekten geçersiz kıldı (`NetworkFile:` ve
      günlük geçişi), `ConfigSource` adres/yol/DNS üçünde de **`static`**,
      varsayılan yol `proto static metric 600` — yani `Metric=` yol tablosuna
      yazıldığı metrikle giriyor. `net-dhcp` + reload üçünü de **`DHCPv4`**'e
      döndürdü, yani "ilk eşleşen kazanır" iki yönde de ölçüldü. `Gateways`
      alanı **sabit yapılandırma altında da `null`** — `Routes[]` okuması DHCP
      kolunun tuhaflığı değil. `enp4s0` boyunca `20-wired.network` okumaya
      devam etti: arayüz başına dosya kararı ölçüldü, drop-in adresi her porta
      düşürürdü. **Kablolu arayüzde sabit adres hâlâ koşmadı** (VM'de `en*`
      beklenen metrik 100), NetworkManager kolu da yok
- [ ] **Adresi gerçekten değiştiren bir reload.** Yukarıdaki üç reload'un
      hiçbirinde adres değişmedi (aynı MAC, aynı kira), o yüzden açık TCP
      bağlantılarının hayatta kalması bedava sağlandı ve **hiçbir şey
      kanıtlamıyor**. Ölçülen şey yalnız şu: reload taşıyıcıyı düşürmüyor —
      üç geçişte de `Lost carrier` yok, link `routable` kaldı. Oturumu o link
      üzerinden gelen bir makinede *farklı* bir adres yazmanın ne yaptığı
      açık; kullanıcıya basılan uyarı bu yüzden yerinde duruyor
- [x] **btrfs kökü** — 2026-08-31'de uçtan uca koştu (`btrfs.qcow2`,
      hostname `btrfstest`). `mkfs.btrfs` sonrası subvolume kolu gerçekten
      çalıştı (`subvolume create /mnt/root` + `set-default`), ve **asıl bahis
      tuttu:** `mount_all()` hiçbir `subvol=` seçeneği vermeden `mount`
      çağırıyor, `findmnt` `subvolid=256,subvol=/root` okuyor — yani
      `rootflags=subvol=` yazmayan tasarım `set-default` sayesinde ayakta.
      Kurulan sistem `root=PARTUUID=… rootfstype=btrfs` cmdline'ıyla açıldı.
      `genfstab -U -p` kökü **`subvol=/root`** ile yazdı (subvolid değil), ESP
      maskesi de fstab'a geçti. `fs_packages` `btrfs-progs`'u pacstrap satırına
      koydu. `btrfs` bu çekirdekte **modül değil, gömülü** (`modules.builtin`;
      `virtio_blk` de öyle) — initramfs'te `btrfs.ko` aramak yanlış sonda
- [x] **btrfs'te takas dosyası ve hazırda bekletme** — aynı turda koştu.
      `chattr +C` → `fallocate` → `mkswap` → `swapon` zinciri **gerçek bir
      btrfs kökünde** ilk kez sınandı ve geçti; karşı-olgu da ölçüldü:
      `+C`'siz bir dosyada `mkswap` **rc=0** dönüyor, `swapon` EINVAL ile
      düşüyor — yani kol dekoratif değil, taşıyıcı. `_swap_offset()` btrfs
      dalını seçti: `filefrag` **859392** derken `map-swapfile` **926976**
      dedi, araç ikincisini yazdı. Tam bir S4 gidiş-dönüşü tamamlandı —
      `boot_id` değişmedi, hazırda bekletmeden önce başlatılan işaret süreci
      hayatta kaldı, askıda geçen süre (`BOOTTIME − MONOTONIC`) ≈0'dan
      **76,81 s**'ye çıktı, ve `journalctl --list-boots` gidiş ile dönüşü
      **tek boot** olarak gösteriyor
- [ ] **`nvme format --ses 1`** ve `nvme sanitize` gerçek donanımda —
      ölçülemez değil, harcanabilir NVMe yok
- [ ] **ATA Secure Erase**: doğrudan bağlı harcanabilir SATA aygıtı yok, USB
      köprüleri ATA SECURITY geçirmiyor. `disk-erase` bunu sessizce geçmiyor,
      yazıyor

**Rig tuzağı — S4 dönüşünden sonra `virtio-gpu` asılıyor, ve bu kurucunun
kusuru değil.** 2026-08-31'de bir kez ölçüldü: hazırda bekletmeden dönen misafir
normal çalışıyordu (SSH, `findmnt`, günlük hepsi doğru), ama sonraki
`systemctl poweroff` tamamlanmadı — makine UKI splash'ında kaldı, SSH kapandı,
QEMU çıkmadı. Günlükte sebep açık: `INFO: task kworker/1:1:50 blocked for more
than 122 seconds`, `Workqueue: events drm_fb_helper_damage_work`, çağrı
`virtio_gpu_queue_ctrl_sgs`'te asılı — yani framebuffer hasar işçisi virtio-gpu
kuyruğunu bekliyor. Ekran görüntüsü bir *kurulum* arızasına birebir benziyor
(açılış splash'ında donmuş makine); ayıran şey `journalctl -b -1`. Monitörden
`quit` + yeniden açış temiz geldi, ve **hazırda bekletme girmeyen** bir
`poweroff` aynı turda sorunsuz çalıştı. Kapsam: `-device virtio-vga`,
linux-zen 7.1.11, tek gözlem; kasten tekrarlanmadı.

## Diğer senaryolar

| Komut | Senaryo |
|---|---|
| `./run-vm.sh bios` | BIOS modunda GRUB kurulumu testi |
| `./run-vm.sh reset && ./run-vm.sh` | Temiz diskle yeniden başla (rEFInd turu) |
| `./run-vm.sh boot` / `bios-boot` | Kurulu sistemi diskten başlat |
| `./run-vm.sh sb` / `sb-boot` | Secure Boot destekli firmware (sbctl'in gerçek kolu) |
| `SCRATCH=1 ./run-vm.sh` | Üç boş disk daha: prepare / erase / nvme format |

Disk ve ISO `~/.cache/archsetup-qemu/` altında tutulur.

`run-vm.sh` yalnız `disk.qcow2` / `bios.qcow2`'yi biliyor: ne disk adı ne
`-monitor` dışarıdan verilebiliyor. Mevcut kurulumları korumak isteyen tur
(Secure Boot kayıtlı UEFI diski, GRUB+rEFInd diski, btrfs diski) qemu komut
satırını elle kuruyor — uefi kolunun birebir kopyası, artı kendi
`btrfs.qcow2` / `OVMF_VARS.btrfs.fd` çifti.

## Başka bir düzenek: swap bölümünden hazırda bekletme

`run-vm.sh` kurucu modunu sınar ve elle sürülür. Yanında, tamamen betikle
sürülen ikinci bir düzenek var: `hibernate-swap-partition/`. Swap **bölümü**
olan bir misafir kurar, `swap-hibernate` görevini orada koşturur ve gerçek
bir S4 gidiş-dönüşü ölçer — bu makinede swap bölümü olmadığı için o dal
başka türlü sınanamıyor. Kendi README'si ayrıntıyı taşıyor.
