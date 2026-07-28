# archsetup

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

Kurucu akışı: klavye → yansılar → (NVMe sıfırlama) → cfdisk → bölüm seçimi →
biçimlendir → bağla → pacstrap → **ek paketler** → sistem yapılandırması
(hostname, locale, kullanıcı, önyükleyici: systemd-boot/UKI, GRUB veya
rEFInd, Secure Boot) → yeniden başlat.

Ek paketler sistem yapılandırmasından **önce** gelir: sbctl, efibootmgr ve
mikrokod oradan kurulur, chroot adımları da bunlara dayanır. `linux-g14`
seçilirse [g14] deposu pacstrap'ten önce canlı ortama, kurulumdan sonra da
hedefe eklenir — depo olmadan çekirdek ne kurulabilir ne güncellenebilir.

Gereksinimler: `python` ve `python-textual` (resmi depoda). Root olarak
çalıştırmayın; sudo gerektiğinde sorulur.

### Fonksiyon modu (TUI olmadan tek görev)

```bash
./archsetup --list          # görevleri listele
./archsetup system-update   # tek görevi çalıştır
./archsetup --lang en       # arayüz dili
```

### SSH yönetimi

`Yapılandırma → SSH Yönetimi` altında dört görev var:

| Görev | Ne yapar |
|---|---|
| `ssh-status` | Salt okunur rapor: anahtarlar, yetkiler, sunucu, agent |
| `ssh-harden` | Yalnızca anahtarla giriş, root kapalı; drop-in yaz ve doğrula |
| `ssh-identity` | Makineye özel GitHub anahtarı, `config.local`, ssh-agent |
| `ssh-rotate` | Anahtar kaybı/sızıntısı: eskisini arşivle, yenisini üret |

**Yeni makine kurulumu.** `~/.ssh` klasörünüzü kopyalayın, sonra:

```bash
./archsetup ssh-identity    # makineye ozel anahtar + agent
./archsetup ssh-harden      # sunucu sertlestirme
```

`ssh-identity` bu makine için `~/.ssh/github_<hostname>` anahtarı yoksa üretir
(terminal varsa parolayı sorar) ve GitHub'a eklenecek satırı yazdırır. Klasörde
başka makinelerin `github_*` anahtarı varken yenisini sessizce üretmez —
hostname değiştiyse fark etmeden GitHub'a eklenmemiş bir anahtarla çalışmaya
başlamayasınız diye önce sorar.

**Kişisel envanter.** LAN alt ağınız ve host kısayollarınız bu depoya girmez;
`~/.ssh/archsetup.toml` dosyasından okunur. Dosya yoksa iskeleti oluşturulur ve
yalnızca genel ayarlar uygulanır. Varlığı aynı zamanda "bu klasör archsetup
tarafından yönetiliyor" işaretidir; taşıdığı `format` numarası ileride düzen
değişirse göç etmeyi mümkün kılar.

```toml
format = 1

[lan]
subnet = "10.0.0.0/24"        # authorized_keys from="..." denetimi icin

[hosts.sunucu]
hostname = "10.0.0.5"
user = "kullanici"
key = "sunucu_ed25519"
```

Buradan `~/.ssh/config.local` üretilir; sizin `~/.ssh/config` dosyanız yalnızca
`Include` satırını ve `Host *` varsayılanlarını tutar. `Include` en üste yazılır,
çünkü `ssh` ilk eşleşen değeri kullanır ve `Host *` en sonda kalmalıdır.

**`authorized_keys` yeniden yazılmaz.** İki dosyanın hata maliyeti eşit değil:
`config.local` yanlış üretilirse dışarı bağlanamazsınız, makinenin başındasınız
ve düzeltirsiniz. `authorized_keys` bozulursa içeri bağlanılamaz ve fiziksel
erişim gerekebilir — bozuk bir `from=` değeri anahtarın hiçbir zaman
eşleşmemesine yol açar. Bu yüzden `ssh-harden` yalnızca denetler ve eksik
`from=` kısıtlarını raporlar; düzeltmeyi siz bilerek yaparsınız.

## Dizin yapısı

```
data/        paket tanımları (TOML) — betiğin "içeriği"; `audio/` altında
             EasyEffects presetleri ve port izleyici servisi
locales/     tr.toml, en.toml — tüm arayüz metinleri
src/archsetup/
  core/      i18n, pacman, donanım tespiti, önyükleyici, görevler
  ui/        Textual ekranları
  installer/ canlı ISO modu: disk, pacstrap, chroot, önyükleyiciler
```

## Testler

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

129 test: i18n (TR/EN anahtar eşitliği dahil), veri dosyaları, önyükleyici
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
- [x] ASUS ROG/TUF araçları: [g14] deposunu kurma (anahtar + pacman.conf),
      asusctl/rog-control-center, servisler; supergfxctl ayrı bir isteğe
      bağlı görev (upstream aşamalı olarak kaldırıyor)
- [x] NVIDIA hibrit dizüstü güç yönetimi ([asus-linux.org rehberi](https://asus-linux.org/guides/arch-guide/)): S0ix + runtime PM için
      `modprobe.d`/`udev` kuralları, Turing/Ampere ayrımı, nvidia-suspend/
      resume/hibernate/powerd servisleri
- [x] Hoparlör ses DSP'si: EasyEffects + ROG G513RM preseti (EQ, psikoakustik
      bas, DRC, limiter) ve aktif çıkış portunu izleyip kulaklıkta preseti
      devre dışı bırakan kullanıcı servisi
- [x] Yapılandırma görevleri: dotfiles (kopyala/bağla/doğrula, rsync yedekli),
      swap/hibernation (resume parametreleri her önyükleyicide), Neovim
      dotfiles kur/kaldır, bat önbelleği
- [x] SDDM temaları (Silent, Sugar Candy), duvar kağıtları, kmscon
- [x] Ağ paylaşımı: Samba (usershare, sambashare grubu) + Avahi + firewalld
- [x] Kalan uygulama kategorileri: yazı tipleri, tema motorları, temalar,
      oyun başlatıcılar, sanallaştırma (virt-config görevi), OpenRazer,
      Waydroid binder kurulumu
- [x] Kurucu modu: disk bölümleme, pacstrap, chroot yapılandırması,
      önyükleyici kurulumu (systemd-boot/UKI, GRUB, rEFInd), Secure Boot
      (sbctl), ek paketler — `iso.sh` ile tek komut başlatma
- [x] pytest test paketi (129 test) ve QEMU test düzeneği (`tests/qemu/`)
- [x] NVMe ad alanı sıfırlama (`nvme format`, kriptografik/kullanıcı verisi
      silme), bağlı aygıt reddi ve aygıt yolunu yazdırarak onay
- [x] Kurucuda kablosuz ağ: `wl*` için networkd dosyası ve iwd'nin yalnızca
      kimlik doğrulamaya sabitlenmesi; canlı ortamda kayıtlı Wi-Fi
      profillerini (parolalarıyla) hedef sisteme kopyalama
- [x] SSH yönetimi: sunucu sertleştirme (drop-in + `sshd -t` doğrulaması ve
      geri alma), makine başına GitHub kimliği, `ssh-agent`, anahtar
      yenileme; kişisel envanter depo dışında (`~/.ssh/archsetup.toml`)
- [ ] SSH: makine sıfırlama sonrası kalan iki elle adımı göreve dönüştürmek.
      `ssh-authorize` terminale yapıştırılan açık anahtarı `ssh-keygen -l`
      ile doğrular, envanterdeki alt ağdan `from="..."` kısıtını üretir ve
      `authorized_keys`'e **ekler** — yeniden yazmaz, önce yedekler. (Ekleme
      güvenlidir, kilitlenme riski dosyayı yeniden üretmekten gelir; bu görev
      yazılırsa CLAUDE.md'deki "authorized_keys üretilmez" maddesi
      "eklenebilir, üretilemez" diye inceltilmeli.) `ssh-forget` ise
      sıfırlanan bir makinenin bayat `known_hosts` kaydını `ssh-keygen -R`
      ile siler; envanterdeki host'lardan seçilir ve **her zaman açık onay
      ister** — "REMOTE HOST IDENTIFICATION HAS CHANGED" bir ortadaki-adam
      kontrolüdür, otomatik susturulacak bir gürültü değil.
- [ ] Kurucu modun QEMU'da uçtan uca doğrulanması (kontrol listesi hazır)
- [ ] Geliştirme: `installarch` (archfi türevi) + `installarchde` betiklerinin
      birleşimi. Teşekkürler [MatMoul/archfi](https://github.com/MatMoul/archfi).
