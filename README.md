# archsetup

[![tests](https://github.com/drpars/archsetup/actions/workflows/tests.yml/badge.svg)](https://github.com/drpars/archsetup/actions/workflows/tests.yml)
[![packages](https://github.com/drpars/archsetup/actions/workflows/packages.yml/badge.svg)](https://github.com/drpars/archsetup/actions/workflows/packages.yml)

🇹🇷 Arch Linux kurulumu ve kurulum sonrası yapılandırma için interaktif TUI aracı.
🇬🇧 Interactive TUI for Arch Linux installation and post-install setup.

[Python](https://www.python.org/) + [Textual](https://textual.textualize.io/) ile
yazılmıştır. `installarch` (canlı ISO'dan kurulum) ve `installarchde`
(kurulum sonrası) betiklerinin modern, tek çatı altında birleşmiş halidir.

## Tasarım

- **Sabit ID mimarisi:** Menü öğeleri ve kategoriler kimliklerle (`console`,
  `system-update`) yönetilir; ekranda görünen metin `locales/*.toml`
  dosyalarından gelir. Dil değiştirmek hiçbir mantığı etkilemez.
- **Veri güdümlü menüler:** Paket listeleri `data/` altındaki TOML
  dosyalarındadır. Yeni uygulama eklemek = birkaç satır TOML.
- **Alt menülerle gruplama:** Görevler `group` alanına göre menülere düşer.
  Yapılandırma yalnızca alt menüleri barındırır; aynı alanın görevleri
  (dotfiles, ağ, sanallaştırma, sistem) tek yerde toplanır.
- **İki mod:** Canlı ISO'da *kurucu* modu (bölümleme, pacstrap, chroot
  yapılandırması, önyükleyici); kurulu sistemde *kurulum sonrası* modu.
  Ortam otomatik algılanır.

## Kullanım

### Kurulu sistemde (kurulum sonrası)

```bash
git clone https://github.com/drpars/archsetup
cd archsetup
./archsetup
```

### Canlı ISO'da (kurucu)

```bash
curl -L https://raw.githubusercontent.com/drpars/archsetup/main/iso.sh | bash
```

Kurucu menüsü **beş aşamadır** ve sıra bağlayıcıdır:

| # | Aşama | Adımlar |
|---|---|---|
| 1 | Canlı Ortam | klavye · yansılar · paralel indirme |
| 2 | Disk | hazırla · sil · bölümle · seç · biçimlendir · bağla |
| 3 | Temel Kurulum | pacstrap · ek paketler |
| 4 | Sistem Yapılandırması | hostname, locale, kullanıcı, önyükleyici (systemd-boot/UKI, GRUB veya rEFInd), Secure Boot |
| 5 | Bitir | bağı ayır · yeniden başlat · kapat |

Aşamalar düz bir listeyken on beş satırdı ve hepsi eşitmiş gibi duruyordu; oysa
`bölümle → seç → biçimlendir → bağla → pacstrap` zorunlu bir sıra. Dördüncüsü
zaten alt menüydü, yani yüzey yarı-aşamalıydı. Numaralandırma sırayı menünün
**ilk cümlesi** yapıyor.

Ek paketler sistem yapılandırmasından **önce** gelir: sbctl, efibootmgr ve
mikrokod oradan kurulur, chroot adımları da bunlara dayanır. `linux-ogc`
seçilirse deposu ([ogc]) pacstrap'ten önce canlı ortama, kurulumdan sonra da
hedefe eklenir — depo olmadan çekirdek ne kurulabilir ne güncellenebilir.
`linux-g14` ve [g14] 2026-08-21'de kaldırıldı: depo 2026-07-19'dan beri yayın
yapmıyor, yani oradan kurulan çekirdek kurulduğu anda güncelleme almayı
bırakıyordu. Eski bir kurulumdan kalmış [g14] **silinmez** (o çekirdeği
çalıştıran makine tek kaynağını kaybederdi) ama [ogc] her zaman onun
**üstüne** yazılır: pacman bir adı dosyada önce gelen depodan çözer, sürüme
bakmaz.

Bölüm seçiminde EFI bölümünün **tipi** de denetlenir: "Linux filesystem"
olarak bırakılmış bir FAT32 bölümü biçimlenir, bağlanır ve dosyaları alır;
iş yalnızca `bootctl install` aşamasında patlar. Yanlış tip bulunursa
`sfdisk --part-type` ile düzeltmesi önerilir (veri silinmez).

Gereksinimler: `python` ve `python-textual` (resmi depoda). Root olarak
çalıştırmayın; sudo gerektiğinde sorulur.

### Fonksiyon modu (TUI olmadan tek görev)

```bash
./archsetup --overview      # araç ne yapar + gruplanmış görev kataloğu
./archsetup --list          # görevleri listele (düz, greplenebilir)
./archsetup system-update   # tek görevi çalıştır
./archsetup --lang en       # arayüz dili
```

İki liste bilerek ayrı: `--list` betiklere ve tamamlamaya bakan düz
`id başlık` satırları üretir; `--overview` ise menülerdeki gruplamayı ve her
görevin tek cümlelik açıklamasını arayüz dilinde gösterir. İkisi de görev
tablosundan ve locale dosyalarından üretilir, yani elle yazılmış bir listenin
aksine gerçeğin gerisinde kalamazlar.

### Ayarlar ve görevler ayrı şeylerdir

Ana menüdeki maddeler **makineyi** değiştirir. Aracın kendi ayarları — dil ve
tema — **Ayarlar** menüsündedir ve `~/.config/archsetup/config.toml`'da tutulur.
Kural basit: `config.toml`'a yazılan şey ayardır, geri kalan görevdir.

### Paket listelerini denetleme

```bash
./archsetup --check-packages
```

`data/` altındaki her paket adını senkron veritabanlarına karşı sınar. Salt
okunur; root gerekmez, ağa çıkmaz.

Bu denetimin sebebi paket listelerinin sessizce çürümesi: bir ad başka pakete
katılır (`libva-mesa-driver` → `mesa`), `Replaces` bırakmadan yeniden
adlandırılır (`plasma-discover` → `discover`) ya da tamamen kalkar
(`bridge-utils`). Kurulum tek `pacman -S --needed <hepsi>` çağrısıyla
yapıldığı için **çözülemeyen tek bir ad işlemin tamamını düşürür** ve o
kategoriden hiçbir paket kurulmaz. Yani ölü bir girdi bütün bir kategoriyi
sessizce devre dışı bırakır.

Adlar dört gruba ayrılır: tam eşleşen, grup (`plasma`), başka paketçe
sağlanan (çalışır ama adı eskimiş) ve çözülemeyen. Yalnızca sonuncusu sıfırdan
farklı çıkış kodu üretir.

Denetim yerel senkron veritabanını okur, yani **son `pacman -Sy` kadar
günceldir**. Bayat bir indeks geçen hafta ölmüş bir ada "temiz" diyebilir —
doğru cevaptan ayırt edilemeyen yanlış bir cevap. Bu yüzden indeksi bir
haftadan eskiyse rapor uyarır.

```bash
./archsetup --check-packages --aur
```

AUR girdilerini de sorgular (ağ gerektirir, tek toplu istek). Üç şeye bakar:
paket AUR'dan kalkmış mı, **resmi depoya terfi etmiş mi** (`aur = true` artık
yanlış: imzalı ikili varken kaynaktan derleniyor), ve bakımsız/eskimiş olarak
işaretli mi. Ağa ulaşılamazsa denetim düşmez, o paketler "denetlenmedi"
sayılır — "soramadık" ile "yok" birbirine karıştırılmaz.

### SSH yönetimi

`Yapılandırma → SSH Yönetimi` altında yedi görev var. Hepsi tek başına da
çalışır: `./archsetup ssh-status` gibi.

| Görev | Ne yapar | Dokunduğu dosya |
|---|---|---|
| `ssh-status` | Salt okunur rapor: anahtarlar, yetkiler, sunucu, agent | — |
| `ssh-identity` | Bu makinenin GitHub anahtarı, `config.local`, ssh-agent | `~/.ssh/config.local` |
| `ssh-harden` | Yalnızca anahtarla giriş, root kapalı; yaz ve doğrula | `/etc/ssh/sshd_config.d/10-local.conf` |
| `ssh-authorize` | Başka bir makinenin açık anahtarını **ekler** | `~/.ssh/authorized_keys` |
| `ssh-forget` | Sıfırlanan makinenin bayat `known_hosts` kaydını siler | `~/.ssh/known_hosts` |
| `ssh-rotate` | Anahtar kaybı/sızıntısı: eskisini arşivle, yenisini üret | `~/.ssh/github_*` |
| `git-identity` | SSH ile commit imzalama | `~/.gitconfig.local`, `allowed_signers` |

#### Önce kavramlar: hangi dosya ne işe yarar

SSH'ta karışması en kolay şey, **iki yönün ayrı dosyalarla yönetilmesi**.
"Dışarı bağlanmak" ve "içeri bağlanılmasına izin vermek" birbirinden bağımsız:

| Dosya | Yön | Ne anlama gelir |
|---|---|---|
| `~/.ssh/id_*` / `github_*` | dışarı | **Özel** anahtarınız. Kimliğinizdir, makineden çıkmaz |
| `~/.ssh/*.pub` | dışarı | **Açık** anahtar. Paylaşılmak içindir, gizli değildir |
| `~/.ssh/config` | dışarı | "Şu host'a şu kullanıcı ve şu anahtarla bağlan" |
| `~/.ssh/known_hosts` | dışarı | Daha önce bağlandığınız **sunucuların** kimliği |
| `~/.ssh/authorized_keys` | **içeri** | Bu makineye girmesine izin verilen açık anahtarlar |
| `/etc/ssh/sshd_config` | **içeri** | Sunucu (sshd) kuralları — kim, nasıl girebilir |

Kritik ayrım: `authorized_keys` **açık** anahtar tutar, özel anahtar değil. Bir
makineye erişim vermek, o makinenin `authorized_keys` dosyasına karşı tarafın
`.pub` dosyasını eklemek demektir. Özel anahtar hiçbir zaman kopyalanmaz.

#### İki dosyaya bilerek farklı davranılır

Bu, aracın en önemli tasarım kararı ve her yerde tekrarlanır:

**`config.local` üretilir.** Yanlış giderse *dışarı* bağlanamazsınız. Makinenin
başındasınız, dosyayı açar düzeltirsiniz. Maliyeti düşük.

**`authorized_keys` asla yeniden yazılmaz — yalnızca eklenir.** Bozulursa
*içeri* bağlanamazsınız. Uzaktaki bir makineye bu olursa geri dönmek için
fiziksel erişim gerekir. En sinsi hâli bozuk bir `from=` değeridir: anahtar
doğrudur, kabul edilmez, hata mesajı da açıklayıcı değildir.

Bu yüzden `ssh-authorize` dosyanın sonuna satır **ekler**; `ssh-harden` ise
eksik `from=` kısıtlarını yalnızca **raporlar**, düzeltmez.

#### Kişisel envanter — `~/.ssh/archsetup.toml`

LAN alt ağınız ve host kısayollarınız bu depoya girmez; ayrı bir dosyadan
okunur. Dosya yoksa iskeleti oluşturulur ve yalnızca genel ayarlar uygulanır.
Varlığı aynı zamanda "bu klasör archsetup tarafından yönetiliyor" işaretidir;
taşıdığı `format` numarası ileride düzen değişirse göç etmeyi mümkün kılar.

```toml
format = 1

[lan]
subnet = "10.0.0.0/24"        # authorized_keys from="..." kisiti icin

[hosts.sunucu]
hostname = "10.0.0.5"
user = "kullanici"
key = "sunucu_ed25519"
```

Buradan `~/.ssh/config.local` üretilir; sizin `~/.ssh/config` dosyanız yalnızca
`Include` satırını ve `Host *` varsayılanlarını tutar. `Include` **en üste**
yazılır, çünkü `ssh` ilk eşleşen değeri kullanır ve `Host *` en sonda kalmalıdır.

#### Yeni makine kurulumu

`~/.ssh` klasörünüzü kopyalayın, sonra sırayla:

```bash
./archsetup ssh-identity    # bu makinenin anahtari + agent
./archsetup ssh-harden      # sunucu sertlestirme
./archsetup git-identity    # commit imzalama (istege bagli)
```

`ssh-identity` bu makine için `~/.ssh/github_<hostname>` anahtarı yoksa üretir
(terminal varsa parolayı sorar) ve GitHub'a eklenecek satırı yazdırır. Klasörde
başka makinelerin `github_*` anahtarı varken yenisini **sessizce üretmez** —
hostname değiştiyse, fark etmeden GitHub'a eklenmemiş bir anahtarla çalışmaya
başlamayasınız diye önce sorar.

#### İki makineyi birbirine bağlamak

Masaüstünden dizüstüne bağlanmak istiyorsunuz diyelim. **Dizüstünde** (hedef):

```bash
./archsetup ssh-harden      # parola girisini kapat, sadece anahtar
./archsetup ssh-authorize   # masaustunun .pub anahtarini yapistirin
```

`ssh-authorize` yapıştırdığınız satırı `ssh-keygen` ile doğrular, envanterdeki
alt ağdan `from="..."` kısıtı üretmeyi teklif eder, dosyayı önce yedekler ve
sonuna **ekler**. Mevcut satırlara dokunmaz.

Doğrulamanın sınırını bilerek yazıyoruz: bu bir **biçim** denetimidir. Satır
aktarılırken kırılmışsa yakalar (e-posta ve sohbet uygulamaları satır kırar,
böyle bir satırı sshd sessizce atlar). Ama "geçerli ama **başka** bir anahtar"
durumunu yakalayamaz — ed25519 anahtarında iç sağlama yoktur, gövdenin son
karakterleri değişirse ortaya yine geçerli bir anahtar çıkar, sadece parmak izi
başka olur. Tek gerçek denetim, görevin yazdırdığı parmak izini **kaynak
makinede** karşılaştırmaktır:

```bash
ssh-keygen -lf ~/.ssh/github_panthera-arch.pub
```

**`from=` ve IPv6 tuzağı.** `from="192.168.1.0/24"` yalnızca IPv4 kapsar. Karşı
makine hedefe **adıyla** bağlanırsa isim IPv6 link-local adrese (`fe80::...`)
çözülebilir ve doğru anahtarla reddedilirsiniz:

```
authorized_keys:3: correct key but not from a permitted host
  (host=fe80::..., required=192.168.1.0/24)
error: Refused by key options
```

İki çözüm var: kısıtı IPv6'yı da kapsayacak şekilde genişletmek, ya da istemciyi
IPv4'e sabitlemek. İkincisi seçildi — sunucu kısıtı dar kalıyor, ki asıl işi o.
Üretilen `config.local` bu yüzden her host'a `AddressFamily inet` yazar.

#### Makine sıfırlandığında: `ssh-forget`

Bir makineyi yeniden kurduğunuzda sunucu anahtarı da değişir ve bağlanmaya
çalıştığınızda şu çıkar:

```
@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@
```

Bu uyarı **gürültü değil**: aynı uyarı, araya giren biri olduğunda da çıkar.
Makineyi siz sıfırladıysanız beklenen bir şeydir; sıfırlamadıysanız durup
düşünmeniz gerekir. `ssh-forget` bu yüzden hiçbir zaman otomatik davranmaz —
kaydı gösterir, uyarır ve açık onay ister; sonra `ssh-keygen -R` ile siler.
Envanterdeki kısayol adını da gerçek adrese çevirir (`known_hosts` adresi tutar).

#### `ssh-rotate` — anahtar kaybı veya sızıntısı

Eski anahtarı `~/.ssh-arsiv/` altına taşır, yenisini üretir ve **GitHub'dan
silinecek parmak izini** yazdırır. Yalnızca GitHub anahtarını kapsar: LAN
anahtarlarını yenilemek karşı makinenin `authorized_keys` dosyasını da
değiştirmeyi gerektirir, oraya erişim yok — yarım iş yapmaktansa dokunmuyor.

#### `git-identity` — commit'i hangi makine yaptı

Aynı hesaba iki makineden push ediyorsanız, commit'i hangisinin yaptığını
ayırt etmenin yolu **imzalayan anahtardır**: `user.name` ve `user.email` her
makinede aynı kalır. Görev iki dosya üretir:

- `~/.gitconfig.local` — imzalama açık, anahtar bu makinenin `github_*.pub`'ı.
  Paylaşılan `~/.gitconfig` yalnızca `[include]` taşır, ona dokunulmaz (dotfiles
  deposuna symlink).
- `~/.config/git/allowed_signers` — imzaları doğrularken güvenilen anahtar
  listesi. **Üretilmez, eklenir:** yeniden üretmek diğer makinenin elle konmuş
  anahtarını düşürürdü ve onun imzaları bir anda "unknown signer" olurdu.

`user.email` uydurulmaz, `git config`'ten okunur — doğrulama e-postaya göre
eşleşiyor. Tanımlı değilse görev hiçbir şey yazmadan durur.

> **Uyarı — `allowed_signers` bir güven listesidir.** Sahibi doğrulanmamış bir
> anahtar eklenirse o anahtarla imzalanan her commit sessizce "geçerli" sayılır.
> Görev bu yüzden anahtarı tahmin etmez; yalnızca bu makinenin kendi `.pub`
> dosyasını yazar.

#### Sık karşılaşılan iki durum

**`git commit` asılı kalıyor.** İmzalama anahtarınız parolalıysa ve agent'a
yüklü değilse, git parolayı terminalden sorar; çıktının görünmediği bir yerde
bu "donma" gibi görünür. Çözüm:

```bash
ssh-add ~/.ssh/github_<makine>          # anahtari agent'a yukle
ssh-add -l                              # yuklu mu, dogrula
```

**Bir komut SSH oturumunda çalışmıyor, makine başında çalışıyor.** SSH
oturumunun *seat*'i yoktur; logind cihaz erişimini (parlaklık, güç yönetimi)
yalnızca seat'i olan etkin oturuma verir. Hangi durumda olduğunuza bakın:

```bash
loginctl show-session "$XDG_SESSION_ID" -p Type -p Seat -p Active
```

`Seat=` boşsa sonucu grafik oturumuna genellemeyin.

## Dizin yapısı

```
data/        paket tanımları (TOML) — betiğin "içeriği"; `audio/` altında
             EasyEffects presetleri ve port izleyici servisi
locales/     tr.toml, en.toml — tüm arayüz metinleri
src/archsetup/
  core/      i18n, pacman, donanım tespiti, önyükleyici, görevler
  ui/        Textual ekranları
  installer/ canlı ISO modu: blockdev (envanter + ortak kapılar), erase,
             disk, pacstrap, chroot, önyükleyiciler
```

## Testler

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Kapsam: i18n (TR/EN anahtar eşitliği dahil), veri dosyaları, önyükleyici
soyutlaması, GPU/hibernation yapılandırması, kurulum sonrası görevler,
kurucu mantığı ve Textual arayüz gezinmesi. Kurucu modun uçtan uca testi
için QEMU düzeneği: [tests/qemu/README.md](tests/qemu/README.md).

## Yol haritası

- [x] Çekirdek: i18n (TR/EN), veri güdümlü menüler, pacman/AUR kurulumu
- [x] Sistem güncelleme görevleri, uygulama ve sürücü kategorileri
- [x] Masaüstü ortamları (Hyprland, Plasma, GNOME) ve giriş yöneticileri
- [x] NVIDIA sürücüleri ve çekirdek modülü yapılandırması (mkinitcpio, modeset)
- [x] Önyükleyici soyutlaması: çekirdek parametreleri düzene göre doğru yere
      yazılır — UKI (`/etc/kernel/cmdline`), systemd-boot girdileri
      (`/boot/loader/entries`), GRUB (`/etc/default/grub` + grub-mkconfig)
      ve rEFInd (`refind_linux.conf`)
- [x] ASUS ROG/TUF araçları: [ogc] deposunu kurma (anahtar + pacman.conf),
      asusctl/rog-control-center, servisler; supergfxctl ayrı bir isteğe
      bağlı görev (upstream arşivledi, paket yalnız AUR'da)
- [x] NVIDIA hibrit dizüstü güç yönetimi ([asus-linux.org rehberi](https://asus-linux.org/guides/arch-guide/)): S0ix + runtime PM için
      `modprobe.d`/`udev` kuralları, Turing/Ampere ayrımı, nvidia-suspend/
      resume/hibernate/powerd servisleri
- [x] NVIDIA uyku birimleri ayrı bir görev: `nvidia-suspend/resume/hibernate`
      dizüstüne özgü değil, askıya alınan her makine ister. Dynamic Boost
      (`nvidia-powerd`) dizüstü görevinde kalıyor; dizüstü görevi ise artık
      şasi denetimi yapıyor (masaüstünde uyarır, "bilinmiyor"u masaüstü saymaz)
- [x] Hoparlör ses DSP'si: EasyEffects + ROG G513RM preseti (EQ, psikoakustik
      bas, DRC, limiter) ve aktif çıkış portunu izleyip kulaklıkta preseti
      devre dışı bırakan kullanıcı servisi
- [x] Yapılandırma görevleri: dotfiles (kopyala/bağla/doğrula, rsync yedekli),
      swap/hibernation (resume parametreleri her önyükleyicide), Neovim
      dotfiles kur/kaldır, bat önbelleği
- [x] SDDM teması (Silent) + giriş ekranı avatarı, duvar kağıtları, kmscon
- [x] Sıfırdan kurulumda `~/.local/state/wallpaper` bağlantısı: hyprpaper ve
      hyprlock oradan okuyor, `wallselect` ise ancak ilk seçimde yazıyor
- [x] Ağ paylaşımı: Samba (usershare, sambashare grubu) + Avahi + firewalld.
      Servisler en sonda başlatılır: smb/nmb `network-online.target`'a bağlı
      olduğu için başlatmak, drop-in yokken 2 dakikaya kadar sürebiliyor —
      o bekleme artık parola isteminin arkasında değil
- [x] Kalan uygulama kategorileri: yazı tipleri, tema motorları, temalar,
      oyun başlatıcılar, sanallaştırma (virt-config görevi), OpenRazer,
      Waydroid binder kurulumu
- [x] Kurucu modu: disk bölümleme, pacstrap, chroot yapılandırması,
      önyükleyici kurulumu (systemd-boot/UKI, GRUB, rEFInd), Secure Boot
      (sbctl), ek paketler — `iso.sh` ile tek komut başlatma
- [x] pytest test paketi (242 test) ve QEMU test düzeneği (`tests/qemu/`)
- [x] Önyükleme süresi ve boot hatası düzeltmeleri (ölçüm: 2dk 20sn → 35sn):
      networkd-wait-online `--any --timeout=3` (servis disable edilmez, smb ve
      keyring-wkd-sync ona bağlı), Samba'nın boot'ta başlaması artık soruluyor,
      libvirtd soket aktivasyonuna geçti, host'a virtio *guest* modülleri
      eklenmiyor, binder DKMS yalnızca çekirdek binder'ı vermiyorsa kuruluyor
      (`/proc/filesystems`), ESP `fmask/dmask=0077` ile bağlanıyor
- [x] Disk hazırlama ve silme, **her taşıyıcı için tek yüzey**: `disk-prepare`
      imzaları siler (`wipefs`, artı aygıt bildiriyorsa `blkdiscard`),
      `disk-erase` içeriği yok eder. Bağlı aygıt ve canlı oturumun açıldığı
      medya reddedilir; silme onayı aygıt yolunu yazdırtır. Dal **sınıfa değil
      yeteneğe** bakar: NVMe'de denetleyiciye `nvme format` (kripto silme
      yalnız `fna` onaylarsa), geri kalanında tam boya sabitlenmiş `dd`.
      Gerekçesi ölçüm: aynı bus'taki iki NVMe farklı `sanicap` bildiriyor ve
      bir USB *flash* bellek `rotational=1` diyor, yani "tip" doğru ekseni
      vermiyor. **ATA Secure Erase bilerek yok** — yarım kalan silme diski
      parola-kilitli bırakır ve o dal ulaşılabilir hiçbir donanımda
      ölçülemedi; `disk-erase` bunu sessizce geçmek yerine yazıyor
- [x] Kurucuda kablosuz ağ: `wl*` için networkd dosyası ve iwd'nin yalnızca
      kimlik doğrulamaya sabitlenmesi; canlı ortamda kayıtlı Wi-Fi
      profillerini (parolalarıyla) hedef sisteme kopyalama
- [x] EFI bölüm tipi denetimi (`sfdisk --part-type` ile düzeltme önerisi),
      seçim ekranlarında yaşayan filtre, kmscon'un doğru paket adı ve
      klavye düzeni, kurulum sonrası reflector görevi
- [x] Terminal kodlama ajanları: Claude Code ve Codewhale, projelerin kendi
      önerdiği kurucu betikleriyle (~/.local/bin, root gerekmez) — betik
      boruya değil dosyaya indirilip öyle çalıştırılır
- [x] SSH yönetimi: sunucu sertleştirme (drop-in + `sshd -t` doğrulaması ve
      geri alma), makine başına GitHub kimliği, `ssh-agent`, anahtar
      yenileme; kişisel envanter depo dışında (`~/.ssh/archsetup.toml`)
- [x] Git makine kimliği: `~/.gitconfig.local` (SSH ile commit imzalama) ve
      `~/.config/git/allowed_signers`. Kimlik her makinede aynı, ayırt edici
      olan imzalayan anahtar. Güven listesine **eklenir**, yeniden üretilmez —
      başka makinelerin anahtarı düşerse imzaları "unknown signer" olur
- [x] SSH: `ssh-authorize` yapıştırılan açık anahtarı doğrular, envanterdeki
      alt ağdan `from="..."` kısıtı üretir ve `authorized_keys`'e **ekler** —
      yeniden yazmaz, önce yedekler. `ssh-forget` sıfırlanan bir makinenin
      bayat `known_hosts` kaydını `ssh-keygen -R` ile siler, her zaman açık
      onay ister
- [ ] Kurucu modun QEMU'da uçtan uca doğrulanması (kontrol listesi hazır)
- [ ] Geliştirme: `installarch` (archfi türevi) + `installarchde` betiklerinin
      birleşimi. Teşekkürler [MatMoul/archfi](https://github.com/MatMoul/archfi).
