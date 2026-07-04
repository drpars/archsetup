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

Kurucu akışı: klavye → yansılar → cfdisk → bölüm seçimi → biçimlendir →
bağla → pacstrap → sistem yapılandırması (hostname, locale, kullanıcı,
önyükleyici: systemd-boot/UKI, GRUB veya rEFInd, Secure Boot) → ek paketler
→ yeniden başlat.

Gereksinimler: `python` ve `python-textual` (resmi depoda). Root olarak
çalıştırmayın; sudo gerektiğinde sorulur.

### Fonksiyon modu (TUI olmadan tek görev)

```bash
./archsetup --list          # görevleri listele
./archsetup system-update   # tek görevi çalıştır
./archsetup --lang en       # arayüz dili
```

## Dizin yapısı

```
data/        paket tanımları (TOML) — betiğin "içeriği"
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

56 test: i18n (TR/EN anahtar eşitliği dahil), veri dosyaları, önyükleyici
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
- [x] ASUS ROG/TUF araçları (g14 deposu algılama, koşullu AUR, servisler)
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
- [x] pytest test paketi (56 test) ve QEMU test düzeneği (`tests/qemu/`)
- [ ] Kurucu modun QEMU'da uçtan uca doğrulanması (kontrol listesi hazır)
- [ ] Geliştirme: `installarch` (archfi türevi) + `installarchde` betiklerinin
      birleşimi. Teşekkürler [MatMoul/archfi](https://github.com/MatMoul/archfi).
