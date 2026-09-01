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
bir **NVMe denetleyicisi** — `core/diskwipe.py`'nin firmware kolu yalnız orada
koşuyor, çünkü bu makinedeki iki gerçek NVMe de veri tutuyor.

Aynı iki yüzey 2026-09-01'den beri **kurulum sonrası** modda da var
(`Yapılandırma → Disk`); gövde ortak, ISO çağırıcısı `installer/erase.py`.
Buradaki işaretli kayıtların hepsi **kurucu** kolunda alınmıştır — `sudo`'lu
kol ayrıca koşturulmalı, aşağıdaki listede duruyor.

```bash
SCRATCH=1 ./run-vm.sh
```

> **`vda` boş disk DEĞİLDİR.** Kurulum diski her zaman ilk sırada takılıyor,
> yani `SCRATCH=1` altında dizilim `vda` = kurulum, **`vdb` = 512M boş**,
> **`vdc` = 64M boş**, `nvme0n1` = emüle NVMe. Aşağıdaki komutlar bu adlara
> göre yazılı; `lsblk -d -o NAME,SIZE,MODEL` ile bir kez doğrulayın —
> yanlış adla koşan bir `mkfs` kurulumu götürür. (Kurucunun kendi kapıları
> bağlı diski reddeder, ama kabuktan atılan komut o kapıdan geçmez.)

- [x] **`disk-prepare`** — 2026-08-30'da arayüzden koştu ve **bu yüzeyin var
      olma sebebini yakaladı:** ekran *"hazır; üzerinde bölüm görünmüyor"*
      dedi, ama **aynı hizada** bir `sgdisk` sonrası `blkid` eski ext4
      UUID'sini geri verdi. Sebep: `wipefs -a <disk>` yalnız **diskin**
      taşıdığını (GPT, PMBR) siliyor, bölüm içindeki süperblok bölüme göreli
      bir ofsette ve o çağrı oraya hiç bakmıyor. `blkdiscard` kurtarmıyor —
      `discard_max_bytes` sıfırdan farklıydı, komut `rc=0` döndü ve **hiçbir
      şey değişmedi**. Sıra bağlayıcı yapıldı (**önce bölümler, sonra disk**;
      tersi tabloyu düşürüp imza taşıyan bölümlerin adlarını da götürüyor) ve
      düzeltmeden sonra gerçek çekirdeğe karşı tekrarlandı: `wipefs -a
      /dev/vda1` ext4 sihrini siliyor, yeniden bölümlemede geriye yalnız taze
      PARTUUID kalıyor, ofsette `00 00`. Ayrıca **gerçek donanımda uçtan uca**
      koştu (`/dev/sda`, Cruzer Force 58,7G): üç imza da düştü (GPT birincil
      `0x200`, GPT yedek `0xeaefffe00`, PMBR `0x1fe`), `blkdiscard` **atlandı**
      — `discard=0` kapısı gerçekte ateşledi
- [x] **`disk-erase` üzerine yazma kolu** — 2026-08-30'da arayüzden koştu,
      kusursuz: tam bayt sayısıyla (`count=67108864`, 64 MiB'ın tamı), `rc=0`,
      ve `cmp <disk> /dev/zero` diskin **son baytına kadar** sıfır okuyup EOF
      veriyor. `count=`'ın var olma sebebi bu — sınırsız `dd` başarı anında
      `error writing` + rc=1 veriyor, yani "bitti" ile "doldu" ayırt edilmiyor
- [x] **`nvme format --ses 0`** — 2026-08-30'da emüle denetleyicide koştu:
      *"Success formatting namespace:1"*, kanarya gitti ve **tüm ad alanı
      sıfır** okudu. Ama bu **denetleyiciye özel**: `dlfeat=0x9`, yani
      "serbest bırakılmış blok okuması sıfır döner" — `--ses 0`'ın veriyi
      okunamaz kılıp kılmadığı cihazın `dlfeat` alanının fonksiyonu, aracın
      vaadi değil (`nvme id-ns`). `crypto_supported()` de doğru cevapladı:
      QEMU `fna=0x0` bildiriyor ve kripto silme menüde çıkmadı
- [x] **Ortak kapılar** — 2026-08-30'da arayüzden koştu ve **bir kusur
      çıkardı.** Bağlama kapısı doğruydu: bağlı bir bölümü olan disk
      reddedildi ve mesaj bağlama noktasını **adıyla** verdi. `/dev/fd0` ise
      silinecek diskler listesindeydi (`1f54d6b`): 4 KB, `TYPE=disk`, major
      **2** — var olan `-e 7,11` süzgeci yalnız loop ile sr'yi eliyordu.
      Buradaki hiçbir makinede disket yok, o yüzden hiç görünmemişti; ve
      durduğu liste kullanıcının **silinecek diski seçtiği** listeydi, yani
      boyutu önemsiz gösteriyor. Süzgeç `-e 2` ile genişletildi ve fd0
      misafirde **var**ken listede **yok**

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
      düşürürdü. NetworkManager kolu yok
- [x] **Kablolu arayüzde sabit adres** — 2026-08-31'de VM'de koştu
      (`btrfs.qcow2`, `ens2`, systemd 261.2). Joker dosya kurucunun **kendi
      sabitinden** (`chroot.py: WIRED_NETWORK_CONF`) kuruldu, elle yazılmış bir
      taklitten değil. Gidiş: `NetworkFile` `20-wired.network` → **`10-static-
      ens2.network`**, IPv4 `ConfigSource` `DHCPv4` → **`static`**, varsayılan
      yol `proto dhcp metric 100` → **`proto static metric 100`** — yani
      **kablolu metrik 100 gerçekten korunuyor** (host turu yalnız kablosuzun
      600'ünü göstermişti), `Required For Online: yes` de yeniden yazılıyor.
      Dönüş (`net-dhcp`) üçünü de geri aldı ve dosyayı sildi. `Gateways` alanı
      **kablolu linkte de, sabit yapılandırma altında da `null`**. İki reload
      boyunca `Lost carrier` sayısı **0** ve o link üzerinden gelen ssh
      oturumu ikisinde de sağ kaldı — ama adres hiç değişmedi (10.0.2.15 →
      10.0.2.15), yani o hayatta kalış bedava sağlanmıştı; adresi değiştiren
      kol aşağıda ayrıca ölçüldü
- [x] **Adresi gerçekten değiştiren bir reload** — 2026-08-31'de VM'de koştu
      (`btrfs.qcow2`, arayüz `ens3`, systemd 261.2). Rig iki NIC'li: kontrol
      kanalı `ens2`'de (slirp 10.0.2.0/24, hostfwd 2222) hiç dokunulmadan
      kaldı, ölçülen link `ens3` (slirp 10.0.3.0/24, hostfwd 2223→`.15` ve
      2224→`.20`), yani ölçüm kendi kanalını kesmiyor — 4. işi durduran şey
      buydu. Araç `configure()` ile sürüldü ve adres kiradan **farklı**
      yazıldı (10.0.3.15 → **10.0.3.20**), reload'a evet dendi.
      **Taşıyıcı düşmüyor:** iki 10 ms sondası, **23.011** ve **143.836**
      tick, **tek geçiş satırı yok**; boot boyunca `Lost carrier` **0**.
      Adres takası bir sil+ekle çifti ve **2,4 ms / 1,6 ms** geniş.
      **Dosyası değişmeyen link hiç kıpırdamıyor:** `ens2` bütün reload'lar
      boyunca **sıfır** adres/yol netlink olayı üretti.
      **Asıl bulgu — oturum ölmüyor, sessizce donuyor:** silinen adresi
      taşıyan soket `ESTABLISHED` kalıyor, gönderim kuyruğu büyüyor
      (19.860 → 42.480 → 55.200 bayt), retransmit backoff 8→9 tırmanıyor,
      hiç ACK gelmiyor — ve `networkctl status` bu sırada `routable
      (configured)` / `online` demeye devam ediyor. Makinede donan oturumu
      bildiren **hiçbir okuma yok**.
      **Adres geri alınırsa donan oturum kaldığı yerden sürüyor:** 102 sn
      donma, 0,2 sn'lik heartbeat akışında **boşluk yok** — tek bayt
      kaybolmadı. **Alınmazsa** misafirin çekirdeği soketi **952 sn**
      sonra düşürüyor (1 sn sonda; `tcp_retries2=15`, sshd
      `ClientAliveInterval 0` — yani düşüren sshd değil TCP).
      **İstemcinin haberi olup olmaması makinenin değil ssh istemcisinin
      özelliği:** `ServerAliveInterval` kapalıyken (OpenSSH'ın kendi
      varsayılanı) istemci **1136 sn** boyunca tek kelime etmedi (öldürüldü,
      yani alt sınır); `ssh_config`'te 60/3 varken **239,1 sn**'de
      `Timeout, server localhost not responding` ile rc=255 verdi.
      **Kapsam:** QEMU kullanıcı modu ağı (slirp). Birinci koldaki host
      tarafı asılması kısmen slirp artefaktı — istemcinin TCP karşı tarafı
      misafir değil QEMU. Kullanıcıya basılan uyarı bu ölçümle **yeniden
      yazıldı** (ikinci kez: 4. iş de aynı satırı bayat bulmuştu)
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
- [x] **`nvme format --ses 1` gerçek donanımda** — 2026-09-01'de koştu, ve
      **kurulum sonrası kipin kendi yüzeyinden** (`./archsetup disk-erase`,
      `sudo nvme format --ses 1 --force`). Disk: Crucial P3 Plus
      `CT1000P3PSSD8_2306E6A91DBB`, FW `P9CR40A`, kullanıcı harcanabilir
      dedi (üzerinde BitLocker'lı bir Windows kurulumu vardı). `rc=0`,
      *"Success formatting namespace:**ffffffff**"* — `fna 0x1` bit 0 set,
      yani biçimlendirme denetleyicinin **tüm** ad alanlarına uygulanıyor;
      öbür NVMe ayrı denetleyicidedir, dokunulmadı. Kripto reddi bu kez
      **gerçek bir hayır**: `fna` bit 2 boş, ve sonda root'la koştu.
      Kanıt — imzalar: önce `gpt@0x200` + `gpt@0xe8e0db5e00` + `PMBR@0x1fe`,
      sonra `wipefs` **hiçbir şey** bulmuyor; ilk 100 MiB'ın sha256'sı
      100 MiB `/dev/zero`'nunkiyle **birebir aynı**
      (`20492a4d0d84f8be…`); 100000 MiB ofsetinde önce **1.044.494 /
      1.048.576** bayt sıfır-olmayan (BitLocker şifre metni), sonra **0**.
      Tam disk taraması **örnekleme değil**: 1.000.204.886.016 baytın tamamı okundu, sıfır-olmayan bayt **0** (18 dk 12 sn)
- [x] **Kurulum sonrası kipin yıkıcı yarısı** (yüzey 2026-09-01'de eklendi,
      aynı gün gerçek donanımda koştu). Reddetme yarısı: `disk-prepare`,
      `/dev/nvme0n1` seçildi → *"kullanımda (/)"*, `rc=1`, hiçbir komut yok.
      Yıkıcı yarısı: `sudo wipefs -a` (bölümler önce, sonra disk — QEMU'da
      ölçülen sıra **gerçek donanımda doğrulandı**), `sudo blkdiscard`
      (`discard_max_bytes` = 2.199.023.255.040 → kapı ateşledi, atlanmadı),
      `sudo nvme format`, `sudo blockdev --rereadpt`. `disk-prepare` 1,13 s,
      `disk-erase` 0,30 s. **Bir kusur çıkardı ve düzeltildi** → aşağıdaki
      "hayalet bölüm" tuzağı
- [x] **`nvme sanitize` (Block Erase)** — 2026-09-01'de aynı Crucial'da koştu,
      iki kez. archsetup onu **hâlâ bilerek uygulamıyor** ve ölçüm o kararı
      **güçlendirdi**, çürütmedi. `--sanact=2`, `SSTAT` `0x00` → `0x101`
      (başarılı + Global Data Erased), `SCDW10 0x2`. **Komut işi yapmıyor,
      zamanlıyor:** `rc=0` **0,032 / 0,037 s**'de döndü, iş `sanitize-log`'dan
      izlendi ve **≤14,4 s** / **5,2 s**'de bitti (denetleyici tahmini 20 s).
      `SPROG` yalnız `0 / 32767 / 65535` veriyor — iki adım, ilerleme çubuğu
      olmaz. İlk koşu **vakumdu** (disk zaten sıfırdı), o yüzden ikincisi
      düzgün kuruldu: beş noktaya `urandom` yazıldı, varlığı doğrulandı
      (~1.044.4xx/1.048.576 sıfır-olmayan), sanitize sonrası tam disk taraması
      **sıfır-olmayan 0** / 1.000.204.886.016 (17 dk 56 sn, örnekleme değil).
      **Neden eklenmiyor:** `--ses 1`
      aynı işi 0,30 s'de yapıyor, ve sanitize asenkron olduğu için düz bir
      `run()` çağrısı disk hâlâ silinirken *"silindi"* derdi — `sanitize-log`
      yoklayan bir döngü + `sanicap` kapısı gerekir, ve o kapı rig'de
      **test edilemez** (QEMU emülesi ve buradaki öbür NVMe `sanicap 0`)
- [x] **Aletin belgesi ikilisiyle çelişebiliyor, ve yıkıcı komutta bedeli
      ağır.** `man nvme-sanitize` synopsis'i `[--force]` listeliyor; ikilide
      **yok** (`tanınmayan seçenek`, `rc=1`). İyi haber: komut hiç koşmadı
      (`SSTAT` 0'da kaldı), yani sözdizimini yıkıcı komutu *göndererek*
      öğrenmek gerekmedi. `--dry-run` de tanınıyor ve göndermiyor **ama
      hiçbir şey basmıyor** — zararsız, faydasız
- [ ] **`sudo dd` üzerine yazma kolu gerçek donanımda** — `disk-erase`'in dd
      dalı yalnız **NVMe olmayan** aygıtta koşar, bu makinedeki iki disk de
      NVMe. QEMU'da koştu (2026-08-30, 64 MiB virtio) ama **`sudo`'suz**,
      kurucu kolundan
- [ ] **ATA Secure Erase**: doğrudan bağlı harcanabilir SATA aygıtı yok, USB
      köprüleri ATA SECURITY geçirmiyor. `disk-erase` bunu sessizce geçmiyor,
      yazıyor
- [ ] **Kökü device-mapper üzerinde olan bir makinede kapının üçüncü sebebi.**
      `blockdev.in_use_disks()` LUKS/LVM kökünü `lsblk -s` ile bulmak için var
      ve tam o adım **hiçbir yerde ölçülmedi** — erişilebilir iki makinede de
      dm aygıtı yok, `lsblk -s`'in crypt/lvm katmanını basacağı belgeden
      okundu. Ölçülen tek şey düz bölümlü hâli: `/dev/nvme0n1p2` →
      `nvme0n1p2 part`, `nvme0n1 disk` (2026-09-01). Bugün dm'li bir kökü
      koruyan şey ESP'nin çıplak bir bölümden bağlı olması, ve bu
      **tesadüf**: şifreli `/boot`, ya da ESP'yi bağlı tutmayan bir makine o
      satırı taşımaz — yani kapı sessizce açık kalır

**Tuzak — silme başarılı olduğu hâlde `lsblk` eski içeriği gösteriyor, ve bu
"olmadı" diye okunuyor.** 2026-09-01'de gerçek donanımda ölçüldü ve düzeltildi.
`nvme format` **denetleyiciye** giden bir admin komutudur; blok katmanına
hiçbir şey söylenmez. Sonuç: disk kanıtlanabilir biçimde bomboşken
(`wipefs` hiçbir imza bulmuyor, ilk 100 MiB sıfırın sha256'sı) çekirdek
`/sys/block/nvme1n1` altında **dört bölümü de** listelemeye devam etti,
düğümler `/dev`'de durdu, `lsblk -f` silinmiş bölüm için **`BitLocker`** dedi,
ve `blockdev.partitions()` **dört hayalet** döndürdü.

Pahalı yanı iki uçlu: (a) kurucu bu satırı bölümlemeden **önce** sunuyor, yani
bir sonraki ekran diskte var olmayan aygıtları seçtirir, ve peşinden koşan bir
`prepare` arkasında hiçbir şey olmayan düğümlere `wipefs` atar; (b) silmeyi
doğrulayan insan `lsblk`'e bakar — ve **temiz bir olumsuz** okur.

Çare `blockdev --rereadpt` (util-linux, `wipefs` ile aynı paket, yeni bağımlılık
yok) ve `erase_disk()` başarıdan **sonra** çağırıyor; başarısız olursa uyarı
basılıp `rc=0` korunuyor — veri zaten gitti, önbellek yüzünden "silinemedi"
denmez. **A/B ölçüldü:** `prepare_disk()` bu çağrıyı yapmıyor ve yapmamalı —
`wipefs -a` ioctl'i kendi atıyor ve çıktısında **söylüyor** (*"disk bölümleme
tablosunu yeniden okumak için ioctl çağrılıyor: Başarılı"*). Düzeltme aynı
diskte doğrulandı: hayaletler kuruldu, `disk-erase` koştu, sonrasında sysfs
boş, `/dev`'de yalnız `/dev/nvme1n1`, `lsblk -f` temiz.

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
